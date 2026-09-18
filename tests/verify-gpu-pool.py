#!/usr/bin/env python3
"""Assert the GPU node pool input of the model serving layer (modelServing.gpuPool, giantswarm/agent-platform#315).

A GPU node pool created through the platform (bumblebee-plans#46, the plan's D3)
arrives tainted nvidia.com/gpu NoSchedule and labelled
giantswarm.io/machine-pool=<cluster>-<pool>. modelServing.gpuPool is the serving
layer's one input for both, applied to everything the connectivity chart renders
onto the pool and published for model-manager. Each case below pins one property:

- default (taint nvidia.com/gpu NoSchedule, no value; no selector): the
  ClusterServingRuntime and every published preset carry the one toleration
  (operator Exists) and no node selector; the discovery ConfigMap publishes
  spec.gpuPool.taint.{key,value,effect} and spec.gpuPool.nodeSelector: {};
- the pool selected (ci/test-model-serving-gpu-pool-values.yaml): the label on
  the runtime, on every preset and in the discovery ConfigMap; a preset's own
  scheduling block keeps its keys, its equal toleration appears once, the pool's
  first;
- a taint value: operator Equal with the value, in the runtime and the
  discovery ConfigMap;
- an empty taint key (an untainted pool): no toleration anywhere, no taint in
  the discovery ConfigMap, and the serving render byte-identical to GOLDEN_REF
  (origin/main; GOLDEN_REF= opts out) but for the discovery block itself --
  against a golden that already carries this change the override goes to both
  sides, since a tainted golden could never equal an untainted head;
- the guards: the effect, the key, string label values (a number must be
  quoted; --set-string passes);
- the meta chart forwards the block to the connectivity release.

Deliberately stdlib-only: the CI image has no PyYAML. HELM selects the binary.
"""

import glob
import os
import re
import subprocess
import sys
import tempfile

HELM = os.environ.get("HELM", "helm")
META, CONN = sys.argv[1], sys.argv[2]
FIXTURE = f"{CONN}/ci/test-model-serving-gpu-pool-values.yaml"
# The serving shape of the connectivity chart: the switch and the KServe
# components on (the prerequisite guard), one Gateway parent.
SERVING = [
    "--namespace", "agent-platform",
    "--set", "global.gatewayApi.parentRefs[0].name=giantswarm-default",
    "--set", "global.gatewayApi.parentRefs[0].namespace=envoy-gateway-system",
    "--set", "components.kserve-crd.enabled=true",
    "--set", "components.kserve-resources.enabled=true",
    "--set", "components.modelServing.enabled=true",
]
UNTAINTED = ["--set", "modelServing.gpuPool.taint.key="]
# The cache claim is applied by a hook Job since #483 (a chart from before
# rendered a PersistentVolumeClaim, and no hook identity); the byte-identity
# check is about the pool input, so both sides render without the claim.
NO_CACHE = ["--set", "modelServing.cache.enabled=false"]
POOL_TOL = {"effect": "NoSchedule", "key": "nvidia.com/gpu", "operator": "Exists"}
LABEL = {"giantswarm.io/machine-pool": "ci-gpu00"}
RUNTIME = ("ClusterServingRuntime", "kserve-vllm")
DISCOVERY = ("ConfigMap", "agent-platform-model-serving")
PRESET = re.compile(r"^agent-platform-serving-preset-(.+)$")
# The presets the connectivity chart ships (one file each; #481 added two).
SHIPPED = len(glob.glob(os.path.join(CONN, "files", "model-serving", "presets", "*.yaml")))
# The discovery block this change adds, cut out for the byte-identity check.
GPU_POOL_BLOCK = re.compile(
    r"      # The GPU node pool \(modelServing\.gpuPool\).*?(?=      # Whether this chart renders network policies)", re.S
)
# The runtimes list giantswarm/agent-platform#550 adds to the discovery ConfigMap,
# cut out of the head for a golden from before it.
RUNTIMES_BLOCK = re.compile(
    r"      # Every ClusterServingRuntime the chart renders.*?(?=      # Defaults of every InferenceService)", re.S
)

# The model-images block of the discovery ConfigMap (#551), cut out while GOLDEN_REF predates it.
MODEL_IMAGES_BLOCK = re.compile(r"      # Models as OCI images \(modelServing\.modelImages\).*?(?=      presets:\n)", re.S)


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


# --- default: the taint tolerated everywhere, no selector, published ---------
docs = documents(helm(CONN, SERVING))
tol, sel = scheduling(docs[RUNTIME])
expect("runtime tolerations", tol, [POOL_TOL])
expect("runtime nodeSelector", sel, {})
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
ok(f"default: the pool taint tolerated (Exists) by the runtime and all {SHIPPED} presets, no selector, published as spec.gpuPool")

# --- the pool selected: the label on the three sites, a preset's own kept ----
docs = documents(helm(CONN, [*SERVING, "-f", FIXTURE]))
tol, sel = scheduling(docs[RUNTIME])
expect("runtime tolerations (pool selected)", tol, [POOL_TOL])
expect("runtime nodeSelector (pool selected)", sel, LABEL)
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
ok("pool selected: the label on the runtime, every preset and the discovery ConfigMap; a preset's own scheduling kept, the pool's toleration once")

# --- a taint value: operator Equal -------------------------------------------
docs = documents(helm(CONN, [*SERVING, "--set", "modelServing.gpuPool.taint.value=present"]))
tol, _ = scheduling(docs[RUNTIME])
expect("runtime tolerations (value)", tol, [{**POOL_TOL, "operator": "Equal", "value": "present"}])
expect("discovery taint (value)", mapping(block(gpu_pool(docs), "taint")), {"key": "nvidia.com/gpu", "value": "present", "effect": "NoSchedule"})
ok("a taint value narrows the toleration to Equal and is published")

# --- untainted: nothing rendered, byte-identical to GOLDEN_REF but for the block
untainted = helm(CONN, [*SERVING, *UNTAINTED, *NO_CACHE])
docs = documents(untainted)
if block(docs[RUNTIME], "tolerations") is not None:
    fail("an empty taint key still renders runtime tolerations")
for name, doc in presets(docs).items():
    if block(doc, "scheduling") is not None:
        fail(f"an empty taint key still renders a scheduling block on preset {name}")
gp = gpu_pool(docs)
expect("discovery taint (untainted)", block(gp, "taint"), None)
expect("discovery nodeSelector (untainted)", block(gp, "nodeSelector"), [])
ok("an empty taint key renders no toleration and no taint")

ref = os.environ.get("GOLDEN_REF", "origin/main")
if ref == "":
    print("skip: GOLDEN_REF is empty (explicit opt-out)")
elif subprocess.run(["git", "rev-parse", "--verify", "-q", ref], capture_output=True).returncode != 0:
    fail(f"GOLDEN_REF={ref} does not resolve; fetch it, point GOLDEN_REF at another ref, or run with GOLDEN_REF= to opt out")
else:
    tree = tempfile.mkdtemp(prefix="ap-gpu-pool-golden-")
    subprocess.run(["git", "worktree", "add", "-q", "--detach", tree, ref], check=True)
    try:
        # Once GOLDEN_REF carries this change -- which it does from the commit
        # that merges it -- the golden's own default render is TAINTED, and a
        # tainted golden can never equal an untainted head: the assertion is
        # that equal inputs render alike, so the golden takes the same
        # UNTAINTED override and the discovery block comes out of both sides.
        # A golden from before the change has neither the flag nor the block.
        carries = "gpuPool:" in open(f"{tree}/{CONN}/values.yaml", encoding="utf-8").read()
        golden = documents(helm(f"{tree}/{CONN}", [*SERVING, *UNTAINTED, *NO_CACHE] if carries else [*SERVING, *NO_CACHE]))
        resized = "g6.xlarge" in open(f"{tree}/{CONN}/files/model-serving/presets/qwen3-4b-instruct.yaml", encoding="utf-8").read()
        shaped = "podShapes" in open(f"{tree}/{CONN}/templates/model-serving/_helpers.tpl", encoding="utf-8").read()
        ported = "llmisvcWorkload:" in open(f"{tree}/{CONN}/values.yaml", encoding="utf-8").read()
        quoted = "--default-chat-template-kwargs='" in open(f"{tree}/{CONN}/files/model-serving/presets/qwen3-8b-fp8.yaml", encoding="utf-8").read()
        sized = "weightsGiB: 25" in open(f"{tree}/{CONN}/files/model-serving/presets/qwen3-8-27b.yaml", encoding="utf-8").read()
        kept = "$ms.namespace.keep" in open(f"{tree}/{CONN}/templates/model-serving/namespace.yaml", encoding="utf-8").read()
        added = os.path.exists(f"{tree}/{CONN}/files/model-serving/presets/qwen3-8-27b-l40s.yaml")
        prepulled = "prepull:" in open(f"{tree}/{CONN}/values.yaml", encoding="utf-8").read()
        fastimaged = "llm-d-fast/" in open(f"{tree}/{CONN}/values.yaml", encoding="utf-8").read()
        hooked = "helm.sh/hook" in open(f"{tree}/{CONN}/templates/model-serving/prepull.yaml", encoding="utf-8").read()
        evaled = "exec vllm serve" in open(f"{tree}/{CONN}/templates/model-serving/clusterservingruntime.yaml", encoding="utf-8").read()
        listed = "additionalRuntimes:" in open(f"{tree}/{CONN}/values.yaml", encoding="utf-8").read()
        imaged = "modelImages:" in open(f"{tree}/{CONN}/templates/model-serving/config.yaml", encoding="utf-8").read()
        flashnext = os.path.exists(f"{tree}/{CONN}/files/model-serving/presets/qwen3-8-flash-next-nvfp4.yaml")
        flashsized = flashnext and "memory: 118Gi" in open(f"{tree}/{CONN}/files/model-serving/presets/qwen3-8-flash-next-nvfp4.yaml", encoding="utf-8").read()
    finally:
        subprocess.run(["git", "worktree", "remove", "--force", tree], check=False)
    head = dict(docs)
    head[DISCOVERY], cuts = GPU_POOL_BLOCK.subn("", head[DISCOVERY])
    expect("the discovery block cut out once", cuts, 1)
    if carries:
        golden[DISCOVERY], gcuts = GPU_POOL_BLOCK.subn("", golden[DISCOVERY])
        expect(f"the discovery block cut out of {ref} once", gcuts, 1)
    # The two L4 presets are sized for a g6.xlarge (giantswarm/agent-platform#502:
    # requests 2 vCPU / 10 GiB, the description says so); a golden from before
    # carries the old 4 vCPU / 16 GiB, so its two preset documents are left out
    # of the comparison on both sides. Drop this once GOLDEN_REF carries #502.
    if not resized:
        for name in ("qwen3-4b-instruct", "qwen3-8b-fp8"):
            head.pop(("ConfigMap", f"agent-platform-serving-preset-{name}"), None)
            golden.pop(("ConfigMap", f"agent-platform-serving-preset-{name}"), None)
        print(f"note: the two L4 presets are resized on this side (#502) and not on {ref}: their ConfigMaps are left out of the comparison")
    # Two presets declare the Hub's weight size (giantswarm/agent-platform#535:
    # qwen3-8-27b 25 GiB, devstral-small-2 25 GiB, their descriptions say so); a
    # golden from before carries 15 and 48, so those two preset documents are
    # left out of the comparison on both sides. Drop this once GOLDEN_REF
    # carries #535.
    if not sized:
        for name in ("qwen3-8-27b", "devstral-small-2"):
            head.pop(("ConfigMap", f"agent-platform-serving-preset-{name}"), None)
            golden.pop(("ConfigMap", f"agent-platform-serving-preset-{name}"), None)
        print(f"note: two presets declare the Hub's weight size on this side (#535) and not on {ref}: their ConfigMaps are left out of the comparison")
    # Six presets write their JSON-valued vLLM arguments as one single-quoted
    # argument, the form the llm-d runtime template's eval keeps intact
    # (giantswarm/agent-platform#532); a golden from before carries the bare
    # two-argument form, so those preset documents are left out of the
    # comparison on both sides. Drop this once GOLDEN_REF carries #532.
    if not quoted:
        for name in ("nemotron-3-super-nvfp4", "qwen3-14b", "qwen3-5-27b", "qwen3-5-35b-a3b", "qwen3-8-27b", "qwen3-8b-fp8"):
            head.pop(("ConfigMap", f"agent-platform-serving-preset-{name}"), None)
            golden.pop(("ConfigMap", f"agent-platform-serving-preset-{name}"), None)
        print(f"note: six presets quote their JSON arguments on this side (#532) and not on {ref}: their ConfigMaps are left out of the comparison")
    # The Flash-Next preset's memory limit covers the B12X stack's mlock()ed
    # resident weights (giantswarm/agent-platform#567: limits.memory 118Gi, the
    # comment in the preset says why); a golden from before carries 64Gi, so
    # that preset document is left out of the comparison on both sides. Drop
    # this once GOLDEN_REF carries #567.
    if flashnext and not flashsized:
        head.pop(("ConfigMap", "agent-platform-serving-preset-qwen3-8-flash-next-nvfp4"), None)
        golden.pop(("ConfigMap", "agent-platform-serving-preset-qwen3-8-flash-next-nvfp4"), None)
        print(f"note: the Flash-Next preset's memory limit is 118Gi on this side (#567) and not on {ref}: its ConfigMap is left out of the comparison")
    # The serving namespace's policies select both pod shapes — the classic
    # predictor and the LLMInferenceService workload pod — and render per shape
    # (giantswarm/agent-platform#506); a golden from before knows the classic
    # predictor only, so those policies are left out of the comparison on both
    # sides. Drop this once GOLDEN_REF carries #506.
    if not shaped:
        policies = ("NetworkPolicy", "CiliumNetworkPolicy", "ClusterPolicy", "PolicyException")
        for side in (head, golden):
            for key in [k for k in side if k[0] in policies and "model-serving" in k[1]]:
                side.pop(key)
        print(f"note: the serving policies select both pod shapes on this side (#506) and not on {ref}: they are left out of the comparison")
    # The preset qwen3-8-27b-l40s ships on this side (giantswarm/agent-platform#544);
    # a golden from before has no such file, so its ConfigMap is left out of the
    # comparison, and so is its name in the discovery ConfigMap's presets list.
    # Drop this once GOLDEN_REF carries #544.
    if not added:
        head.pop(("ConfigMap", "agent-platform-serving-preset-qwen3-8-27b-l40s"), None)
        head[DISCOVERY], ncuts = re.subn(r"^ +- qwen3-8-27b-l40s\n", "", head[DISCOVERY], flags=re.M)
        expect("the new preset's name cut out of the discovery list once", ncuts, 1)
        print(f"note: the preset qwen3-8-27b-l40s ships on this side (#544) and not on {ref}: its ConfigMap and its name in the discovery list are left out of the comparison")
    # The preset qwen3-8-flash-next-nvfp4 ships on this side (giantswarm/agent-platform#553);
    # a golden from before has no such file, so its ConfigMap is left out of the
    # comparison, and so is its name in the discovery ConfigMap's presets list.
    # Drop this once GOLDEN_REF carries #553.
    if not flashnext:
        head.pop(("ConfigMap", "agent-platform-serving-preset-qwen3-8-flash-next-nvfp4"), None)
        head[DISCOVERY], ncuts = re.subn(r"^ +- qwen3-8-flash-next-nvfp4\n", "", head[DISCOVERY], flags=re.M)
        expect("the OCI preset's name cut out of the discovery list once", ncuts, 1)
        print(f"note: the preset qwen3-8-flash-next-nvfp4 ships on this side (#553) and not on {ref}: its ConfigMap and its name in the discovery list are left out of the comparison")
    # The serving namespace is kept whatever the cache switch says
    # (giantswarm/agent-platform#565; modelServing.namespace.keep) and its
    # template's comment says so; the untainted render has the cache off, so a
    # golden from before renders the namespace without the policy and with the
    # old comment — the namespace document is left out of the comparison on
    # both sides. Drop this once GOLDEN_REF carries #565.
    if not kept:
        for side in (head, golden):
            side.pop(("Namespace", "model-serving"), None)
        print(f"note: the serving namespace is kept with the cache off on this side (#565) and not on {ref}: its document is left out of the comparison")
    # The llm-d workload's ingress admits the workload's own port, 8000, instead
    # of the classic predictor's 8080 (giantswarm/agent-platform#525); a golden
    # from before renders the old port, so that one policy is left out of the
    # comparison on both sides. Drop this once GOLDEN_REF carries #525.
    if not ported:
        for side in (head, golden):
            for key in [k for k in side if k[0] in ("NetworkPolicy", "CiliumNetworkPolicy")
                        and k[1].endswith(("-model-serving-llmisvc-workload", "-model-serving-llmisvc-workload-ingress"))]:
                side.pop(key)
        print(f"note: the llm-d workload's ingress admits the workload's port on this side (#525) and not on {ref}: that policy is left out of the comparison")
    # The pre-pull DaemonSet and its deny-all policy (giantswarm/agent-platform#545)
    # are new documents of the serving render; a golden from before has neither,
    # so both are left out of the comparison on both sides. Drop this once
    # GOLDEN_REF carries #545.
    if not prepulled:
        for side in (head, golden):
            for key in [k for k in side if k[1].endswith("-model-serving-prepull")]:
                side.pop(key)
        print(f"note: the pre-pull DaemonSet and its policy render on this side (#545) and not on {ref}: they are left out of the comparison")
    # The classic runtime's container runs through the shell entrypoint with the
    # llm-d template's argument grammar (giantswarm/agent-platform#549); a golden
    # from before renders the container without a command, so the runtime
    # document is left out of the comparison on both sides. Drop this once
    # GOLDEN_REF carries #549.
    if not evaled:
        for side in (head, golden):
            side.pop(RUNTIME, None)
        print(f"note: the classic runtime carries the shell entrypoint on this side (#549) and not on {ref}: its document is left out of the comparison")
    # The discovery ConfigMap publishes every runtime's name as spec.runtimes
    # (giantswarm/agent-platform#550); a golden from before has no such block,
    # so it is cut out of the head's document. Drop this once GOLDEN_REF
    # carries #550.
    if not listed:
        head[DISCOVERY], rcuts = RUNTIMES_BLOCK.subn("", head[DISCOVERY])
        expect("the runtimes block cut out of the discovery ConfigMap once", rcuts, 1)
        print(f"note: the discovery ConfigMap publishes spec.runtimes on this side (#550) and not on {ref}: the block is left out of the comparison")
    # The discovery ConfigMap publishes the model-images registry
    # (giantswarm/agent-platform#551, spec.modelImages.registry); a golden from
    # before has no such block, so it is cut out of the head's discovery
    # document. Drop this once GOLDEN_REF carries #551.
    if not imaged:
        head[DISCOVERY], icuts = MODEL_IMAGES_BLOCK.subn("", head[DISCOVERY])
        expect("the model-images block cut out of the discovery ConfigMap once", icuts, 1)
        print(f"note: the discovery ConfigMap publishes the model-images registry on this side (#551) and not on {ref}: that block is left out of the comparison")
    # The pre-pull DaemonSet names the re-layered llm-d-fast/ runtime image
    # (giantswarm/agent-platform#568); a golden from before names the mirror's,
    # so the DaemonSet document is left out of the comparison on both sides.
    # Drop this once GOLDEN_REF carries #568.
    if prepulled and not fastimaged:
        for side in (head, golden):
            for key in [k for k in side if k[0] == "DaemonSet" and k[1].endswith("-model-serving-prepull")]:
                side.pop(key)
        print(f"note: the pre-pull DaemonSet names the llm-d-fast/ runtime image on this side (#568) and not on {ref}: its document is left out of the comparison")
    # The pre-pull DaemonSet is a post-install/post-upgrade hook object with a
    # pre-delete cleanup Job, for which the hook identity renders, and its
    # selector default is the template's (giantswarm/agent-platform#562, #563);
    # a golden from before renders it as a release resource and — the cache
    # off — no hook object at all, so the DaemonSet is left out of the
    # comparison on both sides and the head's Job and identity are left out.
    # Drop this once GOLDEN_REF carries #563.
    if prepulled and not hooked:
        for side in (head, golden):
            for key in [k for k in side if k[0] == "DaemonSet" and k[1].endswith("-model-serving-prepull")]:
                side.pop(key)
        for key in [k for k in head if k[1] in ("t-model-serving-prepull-cleanup", "t-hooks")]:
            head.pop(key)
        print(f"note: the pre-pull DaemonSet is a hook object with a pre-delete cleanup Job on this side (#563) and not on {ref}: its document, the Job and the hook identity are left out of the comparison")
    if set(head) != set(golden):
        fail(f"untainted render vs {ref}: documents differ: {sorted(set(head) ^ set(golden))}")
    for key in sorted(head):
        if head[key] != golden[key]:
            fail(f"untainted render vs {ref}: {key[0]}/{key[1]} differs:\n{head[key]}\n--- {ref}:\n{golden[key]}")
    ok(f"an empty taint key leaves the serving render (cache claim off on both sides) byte-identical to {ref} but for the discovery block")

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
expect("a quoted number as a label value", scheduling(docs[RUNTIME])[1], {"generation": "6"})
ok("guards: the effect, the key and string label values")

# --- the meta chart forwards the block --------------------------------------
meta = helm(META, [
    "-f", f"{META}/ci/ci-values.yaml", "--set", "components.flux.enabled=false",
    "--set", "components.kserve-crd.enabled=true", "--set", "components.kserve-resources.enabled=true",
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
