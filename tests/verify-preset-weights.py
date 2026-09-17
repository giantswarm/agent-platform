#!/usr/bin/env python3
"""Assert every shipped serving preset's requirements.weightsGiB matches the Hub
(giantswarm/agent-platform#535).

Two components judge whether a GPU pool hosts a preset, from two inputs:
cluster-manager's create_node_pool sizes the pool from the preset's declared
weightsGiB + overheadGiB; model-manager's check_fit / load_model size the
weights from the Hub and add the preset's overheadGiB. A preset whose
weightsGiB understates the Hub guides a person into a pool the serve step then
refuses; one that overstates it hides pools that would serve the model.

Each preset's spec.model.id is sized the way model-manager sizes a fit
(internal/backend/kserve/fit.go): the repository's model.safetensors.index.json
metadata.total_size when the repository has an index, else the sum of its
*.safetensors files (else the other checkpoint formats). The preset passes when
weightsGiB is at least the Hub's size and at most 15 % above it. A preset the
Hub cannot size fails; it is never skipped.

Usage:
  verify-preset-weights.py <preset.yaml | directory>...
      every preset must pass; exit 1 otherwise
  verify-preset-weights.py --expect <understated|overstated> <preset.yaml>...
      negative control: every preset's verdict must be exactly that one

Needs network (the Hub API). HF_TOKEN, when set, is sent as the bearer so a
gated repository can be sized.
"""

import glob
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

import yaml

HUB = os.environ.get("HF_ENDPOINT", "https://huggingface.co")
TOLERANCE_ABOVE = 0.15
GIB = 1024 ** 3
INDEX = "model.safetensors.index.json"
# The checkpoint formats model-manager sums when a repository ships no safetensors.
OTHER_WEIGHTS = (".bin", ".pt", ".pth", ".gguf", ".ckpt", ".msgpack", ".h5", ".onnx")
VERDICTS = ("ok", "understated", "overstated", "unsized")


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


def hub_weights(model_id: str):
    """(bytes, source) of the model's weights as model-manager resolves them."""
    quoted = "/".join(urllib.parse.quote(p, safe="") for p in model_id.split("/"))
    info = get_json(f"{HUB}/api/models/{quoted}?blobs=true")
    files = {s["rfilename"]: s.get("size") or 0 for s in info.get("siblings", [])}
    if INDEX in files:
        doc = get_json(f"{HUB}/{quoted}/resolve/main/{INDEX}")
        total = int((doc.get("metadata") or {}).get("total_size") or 0)
        if total > 0:
            return total, "safetensors-index"
    safetensors = sum(size for name, size in files.items() if name.lower().endswith(".safetensors"))
    if safetensors > 0:
        return safetensors, "safetensors files"
    other = sum(size for name, size in files.items() if name.lower().endswith(OTHER_WEIGHTS))
    if other > 0:
        return other, "checkpoint files"
    raise ValueError("no safetensors index, no weight files")


def load_preset(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        doc = yaml.safe_load(f)
    if not isinstance(doc, dict) or doc.get("kind") != "ServingPreset":
        fail(f"{path}: not a ServingPreset")
    return doc


def check(path: str):
    """(verdict, line) for one preset file."""
    preset = load_preset(path)
    name = preset["metadata"]["name"]
    model_id = preset["spec"]["model"]["id"]
    declared = float(preset["spec"]["requirements"]["weightsGiB"])
    try:
        hub_bytes, source = hub_weights(model_id)
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
        verdict, line = check(path)
        good = verdict == expect
        print(f"{'ok' if good else 'FAIL'}: {verdict:11} {line}", file=sys.stdout if good else sys.stderr)
        failures += not good
    if failures:
        fail(f"{failures} preset(s) did not verify as {expect} "
             f"(a preset passes at >= the Hub's size and <= {TOLERANCE_ABOVE:.0%} above it)")
    what = "match the Hub" if expect == "ok" else f"are {expect}, as the negative control expects"
    print(f"ok: {len(preset_files(args))} preset(s) {what}", file=sys.stderr)


if __name__ == "__main__":
    main()
