#!/usr/bin/env python3
"""Assert models as OCI images (modelServing.modelImages, modelServing.prepull.modelPresets; giantswarm/agent-platform#551).

A preset whose spec.model.storageUri is oci://<registry>/<path>[:tag|@digest] is
served from a model image — KServe's modelcar path — and the chart gives an
installation two knobs for it. Each case below pins one property:

- default (no registry): every published preset's storageUri is its source's,
  shipped and values presets alike; the discovery ConfigMap publishes
  spec.modelImages.registry: ""; the pre-pull DaemonSet carries no model init
  container;
- the registry set (ci/test-model-serving-oci-values.yaml): the oci:// preset's
  storageUri carries the registry host and nothing else of the document
  changes; the hf:// preset beside it is untouched; a shipped oci:// preset is
  rewritten the same way and a shipped hf:// one not at all; the discovery
  ConfigMap publishes the host; the named preset's image — its published
  storageUri minus oci:// — is one init container, pull-model-<preset>, after
  the runtime images, on /bin/true with the runtime init containers' security
  context and resources; a digest reference keeps its digest; an empty
  registry pre-pulls the reference as written;
- the guards: a name that is not a published preset, a preset that is not
  oci://, a name listed twice, a registry with a scheme or a path, an oci://
  reference without a registry host — each fails the render naming it;
- the meta chart forwards both blocks, and the values it forwards render the
  same preset ConfigMap and the same pre-pull pod as the connectivity chart
  does from the fixture.

Needs PyYAML. HELM selects the binary.
"""

import copy
import glob
import os
import subprocess
import sys
import tempfile

import yaml

HELM = os.environ.get("HELM", "helm")
META, CONN = sys.argv[1], sys.argv[2]
FIXTURE = f"{CONN}/ci/test-model-serving-oci-values.yaml"
# The serving shape of the connectivity chart: the switch and the KServe
# components on (the prerequisite guard), one Gateway parent, the harness key
# every render needs.
SERVING = [
    "--namespace", "agent-platform",
    "--set", "kagent.harness.snapshotLocation=s3://ci-agent-snapshots/agents",
    "--set", "global.gatewayApi.parentRefs[0].name=giantswarm-default",
    "--set", "global.gatewayApi.parentRefs[0].namespace=envoy-gateway-system",
    "--set", "components.kserve-crd.enabled=true",
    "--set", "components.kserve-resources.enabled=true",
    "--set", "components.modelServing.enabled=true",
]
REGISTRY = "registry.example.com:5000"
OCI_PRESET, HUB_PRESET = "oci-model", "hub-model"
OCI_AS_WRITTEN = "oci://registry.example.org/models/oci-model:abc1234"
HUB_AS_WRITTEN = "hf://org/Hub-Model"
DIGEST = "sha256:" + "0123456789abcdef" * 4
DISCOVERY = ("ConfigMap", "agent-platform-model-serving")
PRESET_PREFIX = "agent-platform-serving-preset-"
DAEMONSET_SUFFIX = "-model-serving-prepull"


def fail(msg: str) -> None:
    sys.exit(f"FAIL: {msg}")


def ok(msg: str) -> None:
    print(f"ok: {msg}")


def helm(chart: str, flags: list[str], expect_fail: bool = False) -> str:
    r = subprocess.run([HELM, "template", "t", chart, *flags], capture_output=True, text=True, check=False)
    if expect_fail:
        if r.returncode == 0:
            fail(f"render of {chart} {' '.join(flags)} passed; expected it to fail")
        return r.stderr
    if r.returncode != 0:
        fail(f"render of {chart} {' '.join(flags)} failed:\n{r.stderr}")
    return r.stdout


def documents(render: str) -> dict[tuple[str, str], dict]:
    return {(d["kind"], d["metadata"]["name"]): d for d in yaml.safe_load_all(render) if d}


def presets(docs: dict) -> dict[str, dict]:
    """The published ServingPreset documents by name."""
    return {name[len(PRESET_PREFIX):]: yaml.safe_load(doc["data"]["preset.yaml"])
            for (kind, name), doc in docs.items() if kind == "ConfigMap" and name.startswith(PRESET_PREFIX)}


def discovery(docs: dict) -> dict:
    return yaml.safe_load(docs[DISCOVERY]["data"]["config.yaml"])["spec"]


def prepull_pod(docs: dict) -> dict:
    hits = [d for (kind, name), d in docs.items() if kind == "DaemonSet" and name.endswith(DAEMONSET_SUFFIX)]
    if len(hits) != 1:
        fail(f"{len(hits)} pre-pull DaemonSets in the render, expected one")
    return hits[0]["spec"]["template"]["spec"]


def storage_uri(preset: dict) -> str:
    return preset["spec"]["model"]["storageUri"]


def shipped_sources() -> dict[str, str]:
    """Every shipped preset's storageUri as written in its file."""
    out = {}
    for path in glob.glob(os.path.join(CONN, "files", "model-serving", "presets", "*.yaml")):
        with open(path, encoding="utf-8") as f:
            out[os.path.basename(path)[:-len(".yaml")]] = storage_uri(yaml.safe_load(f))
    return out


def swapped(uri: str, registry: str) -> str:
    """The host swap as the chart defines it: the segment before the first / replaced, the rest kept."""
    _, path = uri[len("oci://"):].split("/", 1)
    return f"oci://{registry}/{path}"


def expect(what: str, got, want) -> None:
    if got != want:
        fail(f"{what}: got {got!r}, expected {want!r}")


def check_shipped(published: dict[str, dict], registry: str) -> None:
    """Every shipped preset published as written, an oci:// one with the registry's host when set."""
    sources = shipped_sources()
    if not sources:
        fail("no shipped presets found")
    for name, written in sources.items():
        if name not in published:
            continue  # excluded or replaced by a values preset of the same name
        want = swapped(written, registry) if registry and written.startswith("oci://") else written
        expect(f"shipped preset {name} storageUri (registry {registry!r})", storage_uri(published[name]), want)


# --- default: nothing rewritten, nothing pre-pulled -------------------------
docs = documents(helm(CONN, SERVING))
expect("discovery spec.modelImages.registry (default)", discovery(docs)["modelImages"], {"registry": ""})
check_shipped(presets(docs), "")
default_inits = prepull_pod(docs)["initContainers"]
if any(c["name"].startswith("pull-model-") for c in default_inits):
    fail(f"the default pre-pull pod carries a model init container: {[c['name'] for c in default_inits]}")
ok("default: every shipped preset's storageUri as written, the discovery ConfigMap publishes an empty registry, no model init container")

# --- the registry set: the oci:// preset rewritten, nothing else ------------
with_registry = documents(helm(CONN, [*SERVING, "-f", FIXTURE]))
without = documents(helm(CONN, [*SERVING, "-f", FIXTURE, "--set", "modelServing.modelImages.registry="]))
expect("discovery spec.modelImages.registry (fixture)", discovery(with_registry)["modelImages"], {"registry": REGISTRY})
expect("discovery spec.modelImages.registry (fixture, registry emptied)", discovery(without)["modelImages"], {"registry": ""})
oci_on, oci_off = presets(with_registry)[OCI_PRESET], presets(without)[OCI_PRESET]
expect(f"{OCI_PRESET} storageUri with the registry", storage_uri(oci_on), f"oci://{REGISTRY}/models/oci-model:abc1234")
expect(f"{OCI_PRESET} storageUri with the registry emptied", storage_uri(oci_off), OCI_AS_WRITTEN)
same = copy.deepcopy(oci_on)
same["spec"]["model"]["storageUri"] = OCI_AS_WRITTEN
expect(f"{OCI_PRESET}: the registry changes the storageUri and nothing else of the document", same, oci_off)
expect(f"{HUB_PRESET} storageUri with the registry", storage_uri(presets(with_registry)[HUB_PRESET]), HUB_AS_WRITTEN)
expect(f"{HUB_PRESET}: the registry leaves the document untouched", presets(with_registry)[HUB_PRESET], presets(without)[HUB_PRESET])
check_shipped(presets(with_registry), REGISTRY)
ok(f"the registry set: the oci:// preset's storageUri carries {REGISTRY} and nothing else changes, the hf:// preset is untouched, "
   "every shipped preset follows the same rule (an oci:// one rewritten, an hf:// one as written), the discovery ConfigMap publishes the host")

# --- the pre-pull: one init container per named preset, after the runtime images
with open(f"{CONN}/values.yaml", encoding="utf-8") as f:
    runtime_images = yaml.safe_load(f)["modelServing"]["prepull"]["images"]
for registry, docs_, written in ((REGISTRY, with_registry, f"oci://{REGISTRY}/models/oci-model:abc1234"), ("", without, OCI_AS_WRITTEN)):
    inits = prepull_pod(docs_)["initContainers"]
    expect(f"init container images (registry {registry!r})", [c["image"] for c in inits], [*runtime_images, written[len("oci://"):]])
    model = inits[-1]
    expect("the model init container's name", model["name"], f"pull-model-{OCI_PRESET}")
    expect("the model init container's command", model["command"], ["/bin/true"])
    expect("the model init container's imagePullPolicy", model["imagePullPolicy"], "IfNotPresent")
    expect("the model init container's securityContext (the runtime init containers')", model["securityContext"], inits[0]["securityContext"])
    expect("the model init container's resources (the runtime init containers')", model["resources"], inits[0]["resources"])
digest_uri = f"oci://registry.example.org/models/oci-model@{DIGEST}"
with_digest = documents(helm(CONN, [*SERVING, "-f", FIXTURE, "--set", f"modelServing.presets[0].spec.model.storageUri={digest_uri}"]))
expect("a digest reference's published storageUri", storage_uri(presets(with_digest)[OCI_PRESET]), f"oci://{REGISTRY}/models/oci-model@{DIGEST}")
expect("a digest reference's init container image", prepull_pod(with_digest)["initContainers"][-1]["image"], f"{REGISTRY}/models/oci-model@{DIGEST}")
ok("the pre-pull: the named preset's image is one init container after the runtime images — pull-model-<preset>, the published storageUri minus oci://, "
   "/bin/true, the runtime init containers' security context and resources; a digest reference keeps its digest; an empty registry pre-pulls the reference as written")

# --- the guards --------------------------------------------------------------
for flags, needles in [
    (["--set", "modelServing.prepull.modelPresets[0]=unknown-model"], ['names "unknown-model"', "not a published serving preset", OCI_PRESET]),
    (["--set", f"modelServing.prepull.modelPresets[0]={HUB_PRESET}"], [f'names "{HUB_PRESET}"', HUB_AS_WRITTEN, "only a preset served from an OCI model image"]),
    (["--set", f"modelServing.prepull.modelPresets[1]={OCI_PRESET}"], [f'names "{OCI_PRESET}" twice']),
    (["--set", "modelServing.modelImages.registry=https://registry.example.com"], ['"https://registry.example.com" must be a registry host']),
    (["--set", "modelServing.modelImages.registry=registry.example.com/models"], ['"registry.example.com/models" must be a registry host']),
    (["--set", "modelServing.presets[0].spec.model.storageUri=oci://oci-model:abc1234"], [f'serving preset "{OCI_PRESET}"', '"oci://oci-model:abc1234" names no registry host']),
    (["--set", "modelServing.presets[0].spec.model.storageUri=oci://oci-model:abc1234", "--set", "modelServing.modelImages.registry="],
     [f'serving preset "{OCI_PRESET}"', "names no registry host"]),
]:
    err = helm(CONN, [*SERVING, "-f", FIXTURE, *flags], expect_fail=True)
    for needle in needles:
        if needle not in err:
            fail(f"{' '.join(flags)} failed without naming {needle!r}:\n{err}")
ok("guards: an unknown name, a preset that is not oci://, a name listed twice, a registry with a scheme or a path, "
   "an oci:// reference without a registry host (with and without a registry set) — each fails the render naming it")

# --- the meta chart forwards the blocks, and its forwarded values render alike
# The meta chart's defaults with the fixture on top and the engine off (the CI values would trip the
# wiring's ingress-mode guards, as tests/verify-components.py notes); the connectivity HelmRelease's spec.values.
meta = documents(helm(META, [*SERVING, "-f", FIXTURE, "--set", "components.flux.enabled=false"]))
release = meta.get(("HelmRelease", "agent-platform-connectivity"))
if release is None:
    fail("the meta chart renders no connectivity HelmRelease")
forwarded = release["spec"]["values"]
expect("forwarded modelServing.modelImages", forwarded["modelServing"]["modelImages"], {"registry": REGISTRY})
expect("forwarded modelServing.prepull.modelPresets", forwarded["modelServing"]["prepull"]["modelPresets"], [OCI_PRESET])
with tempfile.TemporaryDirectory() as tmp:
    path = os.path.join(tmp, "forwarded.yaml")
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(forwarded, f)
    through_meta = documents(helm(CONN, ["--namespace", "agent-platform", "-f", path]))
expect("the preset ConfigMap the forwarded values render", presets(through_meta)[OCI_PRESET], oci_on)
expect("the pre-pull pod the forwarded values render", prepull_pod(through_meta), prepull_pod(with_registry))
expect("the registry the forwarded values publish", discovery(through_meta)["modelImages"], {"registry": REGISTRY})
ok("the meta chart forwards modelServing.modelImages and modelServing.prepull.modelPresets, and the values it forwards render the same preset ConfigMap, "
   "pre-pull pod and discovery entry as the connectivity chart does from the fixture")
