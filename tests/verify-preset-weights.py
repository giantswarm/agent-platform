#!/usr/bin/env python3
"""Assert every shipped serving preset's requirements match the Hub: weightsGiB
the checkpoint's size (giantswarm/agent-platform#535), minComputeCapability
the GPU generation its weights need (giantswarm/agent-platform#832).

Two components judge whether a GPU pool hosts a preset, from two inputs:
cluster-manager's create_node_pool sizes the pool from the preset's declared
weightsGiB + overheadGiB and refuses an accelerator below its declared
minComputeCapability; model-manager's check_fit / load_model size the weights
from the Hub, add the preset's overheadGiB and judge the GPU generation from
the checkpoint's config.json. A preset whose weightsGiB understates the Hub
guides a person into a pool the serve step then refuses; one that overstates
it hides pools that would serve the model. A preset whose minComputeCapability
is below what its weights need offers a pool where the model goes Ready and
answers wrong: an FP8 W8A8 checkpoint on an A10G (Ampere, 8.6) runs through
vLLM's weight-only FP8 Marlin fallback and answers gibberish
(giantswarm/model-manager#264).

Weights. Each preset's spec.model.id is sized the way model-manager sizes a fit
(internal/backend/kserve/fit.go): the repository's model.safetensors.index.json
metadata.total_size when the repository has an index, else the sum of its
*.safetensors files (else the other checkpoint formats). The index's total_size
counts only when it agrees with the shards its weight_map names (within 1 %):
a stale index -- a quantized repository that kept the unquantized total -- is
overruled by those shards' sum. The preset passes when weightsGiB is at least
the Hub's size and at most 15 % above it. A preset the Hub cannot size fails;
it is never skipped.

GPU generation. The checkpoint's quantization and dtype (config.json, or
params.json for the mistral format; hf_quant_config.json for a ModelOpt
mixed-precision checkpoint whose config.json does not say to what) set the
floor the way model-manager's fit reads it (internal/backend/kserve/compute.go):
FP8 weights and activations (vLLM's fp8, compressed-tensors 8-bit float,
ModelOpt FP8, mistral fp8_e4m3) need 8.9 -- native FP8 (Ada, Hopper,
Blackwell); FP4 weights (NVFP4: compressed-tensors 4-bit float, ModelOpt NVFP4)
need 8.9 -- native on Blackwell, the Marlin weight-only FP4 path proven on the
L40S, nothing proven below; weight-only schemes the runtime dequantizes (int4,
MXFP4) and unquantized weights need what their activations' dtype needs: bf16
8.0 (Ampere; a T4 predates it), float16 7.0 (vLLM's own floor). A recipe
pinned to Blackwell kernels (CUTE_DSL_ARCH=sm_100a, sm_103a, sm_120a, sm_121a
in spec.env) needs 10.0 whatever its weights. The preset passes when its
declared minComputeCapability is at or above the highest of those; declaring
more than the floor is the recipe's call (the hardware it was proven on). A
preset that declares none, or whose checkpoint names neither quantization nor
dtype, fails.

Usage:
  verify-preset-weights.py <preset.yaml | directory>...
      every preset must pass both; exit 1 otherwise
  verify-preset-weights.py --expect <verdict> <preset.yaml>...
      negative control: every preset's one failing verdict must be exactly
      that one (understated, overstated, unsized; floor-understated,
      floor-undeclared, floor-unjudged)

Needs network (the Hub API). HF_TOKEN, when set, is sent as the bearer so a
gated repository can be read.
"""

import glob
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

import yaml

HUB = os.environ.get("HF_ENDPOINT", "https://huggingface.co")
TOLERANCE_ABOVE = 0.15
# An index total and the shards it maps differ by the safetensors headers only; more is a stale index.
INDEX_TOLERANCE = 0.01
GIB = 1024 ** 3
INDEX = "model.safetensors.index.json"
# The checkpoint formats model-manager sums when a repository ships no safetensors.
OTHER_WEIGHTS = (".bin", ".pt", ".pth", ".gguf", ".ckpt", ".msgpack", ".h5", ".onnx")
WEIGHT_VERDICTS = ("understated", "overstated", "unsized")
FLOOR_VERDICTS = ("floor-understated", "floor-undeclared", "floor-unjudged")
VERDICTS = ("ok",) + WEIGHT_VERDICTS + FLOOR_VERDICTS

# The compute capability each precision needs; the same table as model-manager's
# serve-time fit (internal/backend/kserve/compute.go), so the two judges agree.
CAPABILITY_FP16 = "7.0"
CAPABILITY_BF16 = "8.0"
CAPABILITY_FP8 = "8.9"
CAPABILITY_FP4 = "8.9"
CAPABILITY_BLACKWELL = "10.0"
# The CuTe DSL architecture pins of Blackwell GPUs (B200/GB200, B300, RTX PRO, GB10).
BLACKWELL_ARCHS = ("sm_100", "sm_103", "sm_120", "sm_121")
CAPABILITY_PATTERN = re.compile(r"^[0-9]+\.[0-9]+$")


def fail(msg: str) -> None:
    print(f"FAIL: {msg}", file=sys.stderr)
    sys.exit(1)


def get_json(url: str, attempts: int = 3):
    """One Hub GET as JSON; a transient failure is retried, a 404 raised at once."""
    headers = {"Accept": "application/json", "User-Agent": "agent-platform/verify-preset-weights"}
    token = os.environ.get("HF_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    for attempt in range(1, attempts + 1):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=60) as resp:
                return json.load(resp)
        except urllib.error.HTTPError as e:
            if e.code in (401, 403, 404) or attempt == attempts:
                raise
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
            if attempt == attempts:
                raise
        time.sleep(2 * attempt)


class Repository:
    """One Hub repository: its file list, and its files read on demand."""

    def __init__(self, model_id: str):
        self.quoted = "/".join(urllib.parse.quote(p, safe="") for p in model_id.split("/"))
        info = get_json(f"{HUB}/api/models/{self.quoted}?blobs=true")
        self.files = {s["rfilename"]: s.get("size") or 0 for s in info.get("siblings", [])}

    def read(self, name: str):
        return get_json(f"{HUB}/{self.quoted}/resolve/main/{name}")


def hub_weights(repo: Repository):
    """(bytes, source) of the model's weights as model-manager resolves them."""
    files = repo.files
    if INDEX in files:
        doc = repo.read(INDEX)
        total = int((doc.get("metadata") or {}).get("total_size") or 0)
        # The shards the index maps, not every .safetensors file: a repository that also ships a
        # consolidated checkpoint would count its weights twice.
        mapped = sum(files.get(name, 0) for name in set((doc.get("weight_map") or {}).values()))
        if total > 0 and (mapped == 0 or abs(total - mapped) <= INDEX_TOLERANCE * mapped):
            return total, "safetensors-index"
        if total > 0:
            return mapped, f"the index's shards; its total_size of {total} B disagrees with them"
    shards = sum(size for name, size in files.items() if name.lower().endswith(".safetensors"))
    if shards > 0:
        return shards, "safetensors files"
    other = sum(size for name, size in files.items() if name.lower().endswith(OTHER_WEIGHTS))
    if other > 0:
        return other, "checkpoint files"
    raise ValueError("no safetensors index, no weight files")


def parse_capability(s: str) -> float:
    """"8.6" as a number that orders generations (8.6 < 8.9 < 10.0); ValueError otherwise."""
    if not isinstance(s, str) or not CAPABILITY_PATTERN.match(s):
        raise ValueError(f"{s!r} is not a compute capability of the form major.minor")
    major, minor = s.split(".")
    return int(major) + int(minor) / 100


def group_needs(groups: dict, producer: str = "compressed-tensors"):
    """The needs of compressed-tensors-shaped config_groups (ModelOpt's and the mistral format's
    too): the weights' float precision; an int scheme is weight-only and leaves the dtype to decide."""
    for group in (groups or {}).values():
        weights = (group or {}).get("weights") or {}
        if str(weights.get("type", "")).lower() != "float":
            continue
        bits = weights.get("num_bits")
        if bits == 8:
            yield CAPABILITY_FP8, f"FP8 weights ({producer})"
        elif bits == 4:
            yield CAPABILITY_FP4, f"FP4 weights ({producer})"


def algo_needs(algos, producer: str):
    """The needs of ModelOpt quantization algorithms (NVFP4, FP8, MXFP8, W4A16_NVFP4, ...)."""
    seen = set()
    for algo in algos:
        algo = str(algo or "").upper()
        if "FP4" in algo:
            need = (CAPABILITY_FP4, f"NVFP4 weights ({producer})")
        elif "FP8" in algo:
            need = (CAPABILITY_FP8, f"FP8 weights ({producer})")
        else:
            continue
        if need not in seen:
            seen.add(need)
            yield need


def quantization_needs(repo: Repository, quant: dict):
    """(needs, weight_only): what a quantization_config's weights need, and whether the scheme
    is weight-only (int4, MXFP4: the runtime dequantizes it, the activations' dtype decides)."""
    method = str(quant.get("quant_method", "")).lower()
    if method == "fp8":
        return [(CAPABILITY_FP8, "FP8 weights (quant_method fp8)")], False
    if method == "compressed-tensors":
        needs = list(group_needs(quant.get("config_groups")))
        return needs, not needs
    if method.startswith("modelopt"):
        needs = list(algo_needs([quant.get("quant_algo")], "ModelOpt"))
        if not needs:
            needs = list(group_needs(quant.get("config_groups"), "ModelOpt"))
        if not needs and "hf_quant_config.json" in repo.files:
            # A mixed-precision checkpoint's config.json names no precision; the ModelOpt
            # document beside it names every quantized layer's algorithm.
            doc = repo.read("hf_quant_config.json")
            layers = (doc.get("quantization") or doc).get("quantized_layers") or {}
            needs = list(algo_needs(((layer or {}).get("quant_algo") for layer in layers.values()), "ModelOpt"))
        return needs, False
    if method == "mxfp4":
        # gpt-oss: the Marlin kernels dequantize the MXFP4 experts to bf16 activations on a GPU
        # without native MXFP4, and the checkpoint's config.json names no dtype.
        return [(CAPABILITY_BF16, "MXFP4 weights (bf16 activations)")], False
    # gptq, awq, int8, ...: weight-only schemes vLLM dequantizes on any GPU it supports.
    return [], True


def mistral_needs(params: dict):
    """The needs of a mistral-format checkpoint (params.json): compressed-tensors-shaped
    quantization_config, or the older quantization.qformat_weight."""
    if params.get("quantization_config"):
        return list(group_needs(params["quantization_config"].get("config_groups"), "mistral format"))
    fmt = str((params.get("quantization") or {}).get("qformat_weight", "")).lower()
    if fmt.startswith("fp8"):
        return [(CAPABILITY_FP8, "FP8 weights (mistral format)")]
    if "fp4" in fmt:
        return [(CAPABILITY_FP4, "FP4 weights (mistral format)")]
    return []


def dtype_flag(preset: dict) -> str:
    args = preset["spec"].get("args") or []
    for i, arg in enumerate(args):
        if arg.startswith("--dtype="):
            return arg.split("=", 1)[1]
        if arg == "--dtype" and i + 1 < len(args):
            return args[i + 1]
    return ""


def hub_floor(repo: Repository, preset: dict):
    """(capability, why) the preset's checkpoint and recipe need; ValueError when the Hub
    names neither a quantization nor a dtype."""
    needs = []
    dtype = ""
    if "config.json" in repo.files:
        cfg = repo.read("config.json")
        text = cfg.get("text_config") or {}
        quant = cfg.get("quantization_config") or text.get("quantization_config")
        weight_only = True
        if quant:
            needs, weight_only = quantization_needs(repo, quant)
        if weight_only:
            dtype = str(cfg.get("dtype") or cfg.get("torch_dtype") or text.get("dtype") or text.get("torch_dtype") or "")
    elif "params.json" in repo.files:
        needs = mistral_needs(repo.read("params.json"))
    else:
        raise ValueError("no config.json, no params.json")
    if not needs:
        flag = dtype_flag(preset).lower()
        if flag and flag != "auto":
            dtype = flag
        dtype = dtype.lower()
        if dtype in ("bfloat16", "bf16"):
            needs = [(CAPABILITY_BF16, "bf16 weights")]
        elif dtype in ("float16", "half", "fp16", "float32", "float"):
            needs = [(CAPABILITY_FP16, f"{dtype} weights")]
        else:
            raise ValueError("the checkpoint names neither a float quantization nor a dtype")
    for env in preset["spec"].get("env") or []:
        if env.get("name") == "CUTE_DSL_ARCH" and str(env.get("value", "")).lower().startswith(BLACKWELL_ARCHS):
            needs.append((CAPABILITY_BLACKWELL, f"Blackwell kernels (CUTE_DSL_ARCH={env['value']})"))
    return max(needs, key=lambda need: parse_capability(need[0]))


def load_preset(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        doc = yaml.safe_load(f)
    if not isinstance(doc, dict) or doc.get("kind") != "ServingPreset":
        fail(f"{path}: not a ServingPreset")
    return doc


def check_weights(repo, name: str, model_id: str, preset: dict):
    declared = float(preset["spec"]["requirements"]["weightsGiB"])
    if isinstance(repo, Exception):
        return "unsized", f"{name} ({model_id}): declared {declared:g} GiB, the Hub cannot size it: {repo}"
    try:
        hub_bytes, source = hub_weights(repo)
    except Exception as e:  # noqa: BLE001 - every cause is one verdict: the preset cannot be sized
        return "unsized", f"{name} ({model_id}): declared {declared:g} GiB, the Hub cannot size it: {e}"
    hub_gib = hub_bytes / GIB
    ratio = declared / hub_gib
    if declared < hub_gib:
        verdict = "understated"
    elif declared > hub_gib * (1 + TOLERANCE_ABOVE):
        verdict = "overstated"
    else:
        verdict = "ok"
    return verdict, (f"{name} ({model_id}): declared {declared:g} GiB, Hub {hub_gib:.2f} GiB "
                     f"({source}, {hub_bytes} B), {(ratio - 1) * 100:+.1f} %")


def check_floor(repo, name: str, model_id: str, preset: dict):
    declared = preset["spec"]["requirements"].get("minComputeCapability")
    if declared is None:
        return "floor-undeclared", f"{name} ({model_id}): declares no minComputeCapability"
    try:
        have = parse_capability(declared)
    except ValueError as e:
        return "floor-undeclared", f"{name} ({model_id}): minComputeCapability {e}"
    if isinstance(repo, Exception):
        return "floor-unjudged", f"{name} ({model_id}): declares {declared}, the Hub cannot be read: {repo}"
    try:
        floor, why = hub_floor(repo, preset)
    except Exception as e:  # noqa: BLE001 - every cause is one verdict: the floor cannot be judged
        return "floor-unjudged", f"{name} ({model_id}): declares {declared}, the checkpoint's floor cannot be judged: {e}"
    verdict = "ok" if have >= parse_capability(floor) else "floor-understated"
    return verdict, f"{name} ({model_id}): declares compute capability {declared}, its {why} need {floor}"


def check(path: str):
    """[(verdict, line)] for one preset file: its weights and its GPU generation."""
    preset = load_preset(path)
    name = preset["metadata"]["name"]
    model_id = preset["spec"]["model"]["id"]
    try:
        repo = Repository(model_id)
    except Exception as e:  # noqa: BLE001 - the repository cannot be read: both verdicts say so
        repo = e
    return [check_weights(repo, name, model_id, preset), check_floor(repo, name, model_id, preset)]


def preset_files(args) -> list:
    files = []
    for arg in args:
        if os.path.isdir(arg):
            files.extend(sorted(glob.glob(os.path.join(arg, "*.yaml"))))
        else:
            files.append(arg)
    if not files:
        fail("no preset files given")
    return files


def main() -> None:
    args = sys.argv[1:]
    expect = "ok"
    if args[:1] == ["--expect"]:
        if len(args) < 3 or args[1] not in VERDICTS[1:]:
            print(__doc__, file=sys.stderr)
            sys.exit(2)
        expect, args = args[1], args[2:]
    if not args:
        print(__doc__, file=sys.stderr)
        sys.exit(2)
    failures = 0
    for path in preset_files(args):
        results = check(path)
        # The negative control fails on exactly the expected verdict and passes the other aspect.
        good = {verdict for verdict, _ in results} - {"ok"} == ({expect} - {"ok"})
        for verdict, line in results:
            print(f"{'ok' if good else 'FAIL'}: {verdict:17} {line}", file=sys.stdout if good else sys.stderr)
        failures += not good
    if failures:
        fail(f"{failures} preset(s) did not verify as {expect} "
             f"(a preset passes at >= the Hub's size and <= {TOLERANCE_ABOVE:.0%} above it, declaring a "
             f"minComputeCapability at or above what its checkpoint's precision needs)")
    what = "match the Hub" if expect == "ok" else f"are {expect}, as the negative control expects"
    print(f"ok: {len(preset_files(args))} preset(s) {what}", file=sys.stderr)


if __name__ == "__main__":
    main()
