#!/usr/bin/env python3
"""Assert the GPU node pool input of the model serving layer (modelServing.gpuPool, giantswarm/agent-platform#315).

A GPU node pool created through the platform (bumblebee-plans#46, the plan's D3)
arrives tainted nvidia.com/gpu NoSchedule and labelled
giantswarm.io/machine-pool=<cluster>-<pool>. modelServing.gpuPool is the serving
layer's one input for both, applied to everything the connectivity chart renders
onto the pool and published for model-manager. Each case below pins one property:

- default (taint nvidia.com/gpu NoSchedule, no value; no selector): the
  every published preset carries the one toleration
  (operator Exists) and no node selector; the discovery ConfigMap publishes
  spec.gpuPool.taint.{key,value,effect} and spec.gpuPool.nodeSelector: {};
- the pool selected (ci/test-model-serving-gpu-pool-values.yaml): the label on
  the runtime, on every preset and in the discovery ConfigMap; a preset's own
  scheduling block keeps its keys, its equal toleration appears once, the pool's
  first;
- a taint value: operator Equal with the value, in the runtime and the
  discovery ConfigMap;
- an empty taint key (an untainted pool): no toleration anywhere, no taint in
  the discovery ConfigMap, and the parsed serving render equal, object for
  object, to the default render with the pool's toleration taken out, but for
  the discovery block (both renders are the head's, so a preset or default
  change moves both sides; a mismatch prints a unified diff of the object);
- the guards: the effect, the key, string label values (a number must be
  quoted; --set-string passes);
- the meta chart forwards the block to the connectivity release.

PyYAML parses the renders (the CI job installs it). HELM selects the binary.
"""

import glob
import os
import difflib
import re
import subprocess
import sys

import yaml

HELM = os.environ.get("HELM", "helm")
META, CONN = sys.argv[1], sys.argv[2]
FIXTURE = f"{CONN}/ci/test-model-serving-gpu-pool-values.yaml"
# The serving shape of the connectivity chart: the switch and the llm-d
# components on (the prerequisite guard), one Gateway parent.
SERVING = [
    "--namespace", "agent-platform",
    "--set", "global.gatewayApi.parentRefs[0].name=giantswarm-default",
    "--set", "global.gatewayApi.parentRefs[0].namespace=envoy-gateway-system",
    "--set", "components.kserve-llmisvc-crd.enabled=true",
    "--set", "components.kserve-llmisvc-resources.enabled=true",
    "--set", "components.modelServing.enabled=true",
    # The serving layer without the agentgateway data plane, which became the
    # default (#252) and is not what this compares; origin/main renders the same.
    "--set", "ingress.mode=muster-direct",
    "--set", "components.agentgateway.enabled=false",
]
UNTAINTED = ["--set", "modelServing.gpuPool.taint.key="]
POOL_TOL = {"effect": "NoSchedule", "key": "nvidia.com/gpu", "operator": "Exists"}
LABEL = {"giantswarm.io/machine-pool": "ci-gpu00"}
# The classic path's runtime, removed by giantswarm/agent-platform#574.
CLASSIC_RUNTIME = ("ClusterServingRuntime", "kserve-vllm")
DISCOVERY = ("ConfigMap", "agent-platform-model-serving")
PRESET = re.compile(r"^agent-platform-serving-preset-(.+)$")
# The presets the connectivity chart ships (one file each; #481 added two).
SHIPPED = len(glob.glob(os.path.join(CONN, "files", "model-serving", "presets", "*.yaml")))


def fail(msg: str) -> None:
    sys.exit(f"FAIL: {msg}")


def ok(msg: str) -> None:
    print(f"ok: {msg}")


def helm(chart: str, flags: list, expect_fail: bool = False) -> str:
    r = subprocess.run([HELM, "template", "t", chart, *flags], capture_output=True, text=True)
    if expect_fail:
        if r.returncode == 0:
            fail(f"the render passed but had to fail: {' '.join(flags)}")
        return r.stderr
    if r.returncode != 0:
        fail(f"the render failed: {' '.join(flags)}\n{r.stderr}")
    return r.stdout


def documents(render: str) -> dict:
    """(kind, metadata.name) -> the document, each ending in exactly one newline."""
    out = {}
    for doc in render.split("\n---\n"):
        kind = re.search(r"^kind: (\S+)", doc, re.M)
        name = re.search(r"^  name: (\S+)", doc, re.M)
        if kind and name:
            out[(kind.group(1), name.group(1))] = doc.rstrip("\n") + "\n"
    return out


def block(text: str, key: str) -> list | None:
    """The lines nested under the first `key:` line, dedented to it; [] for an
    inline `{}` / `[]`; None when the key is absent (comments never match)."""
    lines = text.split("\n")
    for i, line in enumerate(lines):
        if not re.match(rf"^\s*{re.escape(key)}:\s*(\{{\}}|\[\])?\s*$", line):
            continue
        if line.rstrip().endswith(("{}", "[]")):
            return []
        indent = len(line) - len(line.lstrip())
        out = []
        for nxt in lines[i + 1:]:
            deeper = len(nxt) - len(nxt.lstrip()) > indent
            # toYaml puts a list's dashes at the parent key's indent.
            sibling_item = len(nxt) - len(nxt.lstrip()) == indent and nxt.lstrip().startswith("- ")
            if nxt.strip() == "" or not (deeper or sibling_item):
                break
            out.append(nxt[indent:])
        return out
    return None


def pairs(line: str) -> tuple:
    k, _, v = line.strip().lstrip("- ").partition(":")
    return k.strip(), v.strip().strip('"')


def items(lines: list | None) -> list:
    """A YAML list of flat mappings -> list of dicts (quotes stripped)."""
    res: list = []
    for line in lines or []:
        if line.strip().startswith("- "):
            res.append({})
        k, v = pairs(line)
        res[-1][k] = v
    return res


def mapping(lines: list | None) -> dict:
    return dict(pairs(line) for line in lines or [])


def scheduling(doc: str) -> tuple:
    """(tolerations, nodeSelector) of a document's first tolerations:/nodeSelector: keys."""
    return items(block(doc, "tolerations")), mapping(block(doc, "nodeSelector"))


def presets(docs: dict) -> dict:
    return {PRESET.match(name).group(1): doc for (kind, name), doc in docs.items() if kind == "ConfigMap" and PRESET.match(name)}


def gpu_pool(docs: dict) -> str:
    lines = block(docs[DISCOVERY], "gpuPool")
    if lines is None:
        fail("the discovery ConfigMap publishes no spec.gpuPool")
    return "\n".join(lines) + "\n"


def expect(what: str, got, want) -> None:
    if got != want:
        fail(f"{what}: got {got!r}, expected {want!r}")


def nested(node):
    """A parsed object with every multi-line string that holds a YAML mapping or
    list (a ConfigMap's preset, the discovery config) parsed in place."""
    if isinstance(node, dict):
        return {k: nested(v) for k, v in node.items()}
    if isinstance(node, list):
        return [nested(v) for v in node]
    if isinstance(node, str) and "\n" in node:
        try:
            parsed = yaml.safe_load(node)
        except yaml.YAMLError:
            return node
        if isinstance(parsed, (dict, list)):
            return nested(parsed)
    return node


def objects(render: str) -> dict:
    """(kind, namespace, name) -> the parsed object, nested YAML parsed too."""
    out = {}
    for obj in yaml.safe_load_all(render):
        if isinstance(obj, dict) and obj.get("kind"):
            meta = obj.get("metadata") or {}
            key = (obj["kind"], meta.get("namespace", ""), meta.get("name", ""))
            if key in out:
                fail(f"the render carries {key} twice")
            out[key] = nested(obj)
    return out


def unpooled(node):
    """node with the pool's toleration taken out of every tolerations list; a
    list or mapping that leaves empty is dropped, as the untainted render
    renders none."""
    if isinstance(node, list):
        return [unpooled(v) for v in node]
    if not isinstance(node, dict):
        return node
    out = {}
    for k, v in node.items():
        w = unpooled(v)
        if k == "tolerations" and isinstance(w, list):
            w = [t for t in w if t != POOL_TOL]
        if w in ([], {}) and v not in ([], {}):
            continue
        out[k] = w
    return out


def compare(what: str, got: dict, against: str, want: dict) -> None:
    """Fail with a unified diff of every object that differs (key order and
    document order never count)."""
    diffs = []
    for key in sorted(set(got) | set(want)):
        if got.get(key) == want.get(key):
            continue
        dump = lambda side: [] if key not in side else yaml.safe_dump(side[key], sort_keys=True, width=200).splitlines(keepends=True)
        diffs.append("".join(difflib.unified_diff(dump(want), dump(got), f"{'/'.join(key)} ({against})", f"{'/'.join(key)} ({what})")))
    if diffs:
        fail(f"{what} vs {against}: {len(diffs)} object(s) differ:\n" + "\n".join(diffs))


# --- default: the taint tolerated everywhere, no selector, published ---------
docs = documents(helm(CONN, SERVING))
if CLASSIC_RUNTIME in docs:
    fail("the serving render carries a ClusterServingRuntime; the classic path was removed (giantswarm/agent-platform#574)")
shipped = presets(docs)
expect("shipped presets", len(shipped), SHIPPED)
for name, doc in shipped.items():
    sched = block(doc, "scheduling")
    if sched is None:
        fail(f"preset {name} publishes no scheduling block")
    expect(f"preset {name} tolerations", items(block("\n".join(sched), "tolerations")), [POOL_TOL])
    expect(f"preset {name} nodeSelector", block("\n".join(sched), "nodeSelector"), None)
gp = gpu_pool(docs)
expect("discovery spec.gpuPool.taint", mapping(block(gp, "taint")), {"key": "nvidia.com/gpu", "value": "", "effect": "NoSchedule"})
expect("discovery spec.gpuPool.nodeSelector", block(gp, "nodeSelector"), [])
ok(f"default: the pool taint tolerated (Exists) by all {SHIPPED} presets, no selector, published as spec.gpuPool")

# --- the pool selected: the label on both sites, a preset's own kept ---------
docs = documents(helm(CONN, [*SERVING, "-f", FIXTURE]))
all_presets = presets(docs)
expect("presets with the fixture's own", len(all_presets), SHIPPED + 1)
for name, doc in all_presets.items():
    sched = "\n".join(block(doc, "scheduling") or [])
    tol, sel = items(block(sched, "tolerations")), mapping(block(sched, "nodeSelector"))
    if name == "pinned-model":
        expect("pinned-model tolerations (the pool's first, its equal entry once, its own after)", tol,
               [POOL_TOL, {"effect": "NoSchedule", "key": "example.com/dedicated", "operator": "Equal", "value": "serving"}])
        expect("pinned-model nodeSelector (its keys and the pool's)", sel, {**LABEL, "nvidia.com/gpu.product": "NVIDIA-L4"})
    else:
        expect(f"preset {name} tolerations (pool selected)", tol, [POOL_TOL])
        expect(f"preset {name} nodeSelector (pool selected)", sel, LABEL)
gp = gpu_pool(docs)
expect("discovery spec.gpuPool.nodeSelector (pool selected)", mapping(block(gp, "nodeSelector")), LABEL)
ok("pool selected: the label on every preset and the discovery ConfigMap; a preset's own scheduling kept, the pool's toleration once")

# --- a taint value: operator Equal -------------------------------------------
docs = documents(helm(CONN, [*SERVING, "--set", "modelServing.gpuPool.taint.value=present"]))
for name, doc in presets(docs).items():
    expect(f"preset {name} tolerations (value)", items(block("\n".join(block(doc, "scheduling") or []), "tolerations")), [{**POOL_TOL, "operator": "Equal", "value": "present"}])
expect("discovery taint (value)", mapping(block(gpu_pool(docs), "taint")), {"key": "nvidia.com/gpu", "value": "present", "effect": "NoSchedule"})
ok("a taint value narrows the toleration to Equal and is published")

# --- untainted: the pool's toleration is all an empty taint key takes away --
untainted = helm(CONN, [*SERVING, *UNTAINTED])
docs = documents(untainted)
for name, doc in presets(docs).items():
    if block(doc, "scheduling") is not None:
        fail(f"an empty taint key still renders a scheduling block on preset {name}")
gp = gpu_pool(docs)
expect("discovery taint (untainted)", block(gp, "taint"), None)
expect("discovery nodeSelector (untainted)", block(gp, "nodeSelector"), [])
ok("an empty taint key renders no toleration and no taint")

# The same head rendered twice, with the default taint and with an empty key,
# parsed: the tainted render with the pool's toleration taken out of every
# tolerations list (and a list or block that leaves empty dropped) must equal
# the untainted render object for object, but for the discovery ConfigMap's
# spec.gpuPool (asserted above). No other ref is read, so a preset, a serving
# default or the discovery block changes on both sides alike.
tainted = objects(helm(CONN, SERVING))
want = {key: unpooled(obj) for key, obj in tainted.items()}
got = objects(untainted)
for side in (want, got):
    config = side.get(("ConfigMap", "agent-platform", DISCOVERY[1]), {}).get("data", {}).get("config.yaml")
    if not isinstance(config, dict) or config.get("spec", {}).pop("gpuPool", None) is None:
        fail("the discovery ConfigMap carries no spec.gpuPool to leave out of the comparison")
compare("an empty taint key", got, "the default taint, its toleration taken out", want)
ok(f"an empty taint key changes the serving render ({len(got)} objects) by the pool's toleration alone, but for the discovery block")

# --- the guards --------------------------------------------------------------
for flags, needle in [
    (["--set", "modelServing.gpuPool.taint.effect=Sometimes"], "must be NoSchedule, PreferNoSchedule or NoExecute"),
    (["--set", "modelServing.gpuPool.taint.key=bad key"], "must be a taint key"),
    (["--set", "modelServing.gpuPool.nodeSelector.generation=6"], "must be a string"),
]:
    err = helm(CONN, [*SERVING, *flags], expect_fail=True)
    if needle not in err:
        fail(f"{' '.join(flags)} failed for the wrong reason:\n{err}")
docs = documents(helm(CONN, [*SERVING, "--set-string", "modelServing.gpuPool.nodeSelector.generation=6"]))
first = next(iter(presets(docs).values()))
expect("a quoted number as a label value", mapping(block("\n".join(block(first, "scheduling") or []), "nodeSelector")), {"generation": "6"})
ok("guards: the effect, the key and string label values")

# --- the meta chart forwards the block --------------------------------------
meta = helm(META, [
    "-f", f"{META}/ci/ci-values.yaml", "--set", "components.flux.enabled=false",
    "--set", "components.kserve-llmisvc-crd.enabled=true", "--set", "components.kserve-llmisvc-resources.enabled=true",
    "--set", "components.modelServing.enabled=true",
    "--set-json", 'modelServing.gpuPool.nodeSelector={"giantswarm.io/machine-pool":"ci-gpu00"}',
])
conn = documents(meta).get(("HelmRelease", "agent-platform-connectivity"))
if conn is None:
    fail("the meta chart renders no connectivity HelmRelease")
gp = block(conn, "gpuPool")
if gp is None:
    fail("the meta chart does not forward modelServing.gpuPool to the connectivity release")
expect("forwarded taint", mapping(block("\n".join(gp), "taint")), {"key": "nvidia.com/gpu", "value": "", "effect": "NoSchedule"})
expect("forwarded nodeSelector", mapping(block("\n".join(gp), "nodeSelector")), LABEL)
ok("the meta chart forwards modelServing.gpuPool to the connectivity release")
