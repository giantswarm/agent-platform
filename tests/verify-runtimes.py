#!/usr/bin/env python3
"""Assert modelServing.additionalRuntimes (giantswarm/agent-platform#550): more vLLM
ClusterServingRuntimes beside the default one, rendered from the same template.

modelServing.runtime rendered exactly one runtime and every preset ran on it; an
installation whose accelerator needs a different serving image for some models had
to move every preset to it or hand-write a runtime outside the chart. The list
renders one ClusterServingRuntime per entry, every field the entry leaves unset the
default runtime's. Each case below pins one property:

- the default render: one runtime, the discovery ConfigMap's spec.runtimes naming
  it alone next to spec.runtime; an empty list renders the same;
- the fixture (ci/test-model-serving-runtimes-values.yaml): three runtimes in
  order -- the default, then the entries; an entry that names an image only differs
  from the default in its name and image alone (every other field inherited); an
  entry's own image, base arguments, environment (a list replaces the default's
  whole) and scheduling render, its unset fields inherited; the pool's taint
  tolerated first and its label under every runtime's selector; the same shell
  entrypoint on each, ahead of the runtime's own base arguments; the model-serving
  labels on each; spec.runtimes naming all three, the default first, spec.runtime
  unchanged;
- the guards: the default's name reused, another entry's name reused, a missing
  name, a name that is no DNS-1123 subdomain, an entry that is not a mapping --
  each fails the render naming the entry;
- the meta chart forwards the list, and the values it forwards render the same
  three runtimes as the connectivity chart renders from the fixture.

Needs PyYAML. HELM selects the binary.
"""

import os
import subprocess
import sys
import tempfile

import yaml

HELM = os.environ.get("HELM", "helm")
META, CONN = sys.argv[1], sys.argv[2]
FIXTURE = f"{CONN}/ci/test-model-serving-runtimes-values.yaml"
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
# The meta chart's serving shape with the engine off: the connectivity HelmRelease
# carries the forwarded values (the CI values would trip the wiring's ingress-mode
# guards, as tests/verify-components.py notes).
META_SERVING = [
    "--namespace", "agent-platform",
    "--set", "ingress.parentRefs[0].name=x",
    "--set", "kagent.harness.snapshotLocation=s3://ci-agent-snapshots/agents",
    "--set", "components.flux.enabled=false",
]
DEFAULT = "kserve-vllm"
# The llm-d template's argument grammar every runtime's container runs through
# (giantswarm/agent-platform#549), ahead of the runtime's own base arguments.
COMMAND = ["/bin/sh", "-c", 'eval "exec vllm serve $@"', "--"]
POOL_TOL = {"effect": "NoSchedule", "key": "nvidia.com/gpu", "operator": "Exists"}
LABEL = {"giantswarm.io/machine-pool": "ci-gpu00"}
# The fields an entry inherits from the default runtime, as rendered.
INHERITED_CONTAINER = ("args", "env", "resources")
INHERITED_SPEC = ("annotations", "supportedModelFormats")


def fail(msg: str) -> None:
    sys.exit(f"FAIL: {msg}")


def ok(msg: str) -> None:
    print(f"ok: {msg}")


def expect(what: str, got, want) -> None:
    if got != want:
        fail(f"{what}: got {got!r}, expected {want!r}")


def helm(chart: str, flags: list, expect_fail: bool = False) -> str:
    r = subprocess.run([HELM, "template", "t", chart, *flags], capture_output=True, text=True)
    if expect_fail:
        if r.returncode == 0:
            fail(f"the render passed but had to fail: {' '.join(flags)}")
        return r.stderr
    if r.returncode != 0:
        fail(f"the render failed: {' '.join(flags)}\n{r.stderr}")
    return r.stdout


def documents(render: str) -> list:
    return [d for d in yaml.safe_load_all(render) if d]


def runtimes(docs: list) -> list:
    """The ClusterServingRuntimes in render order."""
    return [d for d in docs if d.get("kind") == "ClusterServingRuntime"]


def names(docs: list) -> list:
    return [rt["metadata"]["name"] for rt in runtimes(docs)]


def discovery(docs: list) -> dict:
    """spec of the discovery ConfigMap's ModelServingConfig."""
    hits = [d for d in docs if d.get("kind") == "ConfigMap" and d["metadata"]["name"] == "agent-platform-model-serving"]
    if len(hits) != 1:
        fail(f"{len(hits)} discovery ConfigMaps in the render, expected one")
    return yaml.safe_load(hits[0]["data"]["config.yaml"])["spec"]


def container(rt: dict) -> dict:
    return rt["spec"]["containers"][0]


def image(entry: dict) -> str:
    return f"{entry['image']['registry']}/{entry['image']['name']}:{entry['image']['version']}"


def without_identity(rt: dict) -> dict:
    """A runtime document less its name and image -- what an image-only entry must share with the default."""
    return {**rt, "metadata": {**rt["metadata"], "name": None},
            "spec": {**rt["spec"], "containers": [{**container(rt), "image": None}, *rt["spec"]["containers"][1:]]}}


with open(FIXTURE) as f:
    ENTRIES = yaml.safe_load(f)["modelServing"]["additionalRuntimes"]
OWN, IMAGE_ONLY = ENTRIES

# --- the default: one runtime, published alone ------------------------------
docs = documents(helm(CONN, SERVING))
expect("runtimes in the default render", names(docs), [DEFAULT])
spec = discovery(docs)
expect("spec.runtime", spec["runtime"], DEFAULT)
expect("spec.runtimes", spec["runtimes"], [DEFAULT])
ok("the default render: one ClusterServingRuntime, spec.runtimes names it alone next to spec.runtime")
docs = documents(helm(CONN, [*SERVING, "--set-json", "modelServing.additionalRuntimes=[]"]))
expect("runtimes with an empty list", names(docs), [DEFAULT])
expect("spec.runtimes with an empty list", discovery(docs)["runtimes"], [DEFAULT])
ok("an empty list renders the default alone")

# --- the fixture: three runtimes, inheritance, the pool, the entrypoint -----
docs = documents(helm(CONN, [*SERVING, "-f", FIXTURE]))
rendered = names(docs)
expect("runtimes in the fixture render", rendered, [DEFAULT, OWN["name"], IMAGE_ONLY["name"]])
spec = discovery(docs)
expect("spec.runtime (fixture)", spec["runtime"], DEFAULT)
expect("spec.runtimes (fixture)", spec["runtimes"], rendered)
by_name = dict(zip(rendered, runtimes(docs)))
default = by_name[DEFAULT]
for name, rt in by_name.items():
    expect(f"{name}: the model-serving component label", rt["metadata"]["labels"].get("app.kubernetes.io/component"), "model-serving")
    expect(f"{name}: the shell entrypoint", container(rt)["command"], COMMAND)
    if not container(rt).get("args"):
        fail(f"{name}: no base arguments after the entrypoint")
    expect(f"{name}: the pool's toleration first", rt["spec"]["tolerations"][0], POOL_TOL)
    expect(f"{name}: the pool's label under the selector", {k: rt["spec"]["nodeSelector"].get(k) for k in LABEL}, LABEL)
ok("every runtime carries the model-serving labels, the shell entrypoint ahead of its base arguments, the pool's toleration first and its label")
image_only = by_name[IMAGE_ONLY["name"]]
expect("the image-only entry's image", container(image_only)["image"], image(IMAGE_ONLY))
if without_identity(image_only) != without_identity(default):
    fail(f"the image-only entry differs from the default beyond its name and image:\n{yaml.safe_dump(image_only)}\n--- the default:\n{yaml.safe_dump(default)}")
ok(f"{IMAGE_ONLY['name']}: an image-only entry differs from the default in its name and image alone -- args, env, resources, /dev/shm, startup probe, annotations, model formats and scheduling inherited")
own = by_name[OWN["name"]]
expect("the own entry's image", container(own)["image"], image(OWN))
expect("the own entry's base arguments", container(own)["args"], OWN["args"])
expect("the own entry's env (its list replaces the default's whole)", container(own)["env"], OWN["env"])
expect("the own entry's tolerations: the pool's first, its own after it, the equal entry once", own["spec"]["tolerations"], [POOL_TOL, OWN["tolerations"][1]])
expect("the own entry's nodeSelector: its own keys with the pool's label", own["spec"]["nodeSelector"], {**OWN["nodeSelector"], **LABEL})
for field in ("resources",):
    expect(f"the own entry inherits container.{field}", container(own)[field], container(default)[field])
for field in INHERITED_SPEC:
    expect(f"the own entry inherits spec.{field}", own["spec"][field], default["spec"][field])
expect("the own entry inherits the startup probe", container(own)["startupProbe"], container(default)["startupProbe"])
expect("the own entry inherits /dev/shm", own["spec"]["volumes"], default["spec"]["volumes"])
ok(f"{OWN['name']}: its own image, base arguments, env and scheduling render; resources, startup probe, /dev/shm, annotations and model formats inherited")

# --- the guards --------------------------------------------------------------
for flags, needle in [
    (["--set", f"modelServing.additionalRuntimes[0].name={DEFAULT}"], f'additionalRuntimes[0]: runtime "{DEFAULT}" is rendered already'),
    (["-f", FIXTURE, "--set", f"modelServing.additionalRuntimes[1].name={OWN['name']}"], f'additionalRuntimes[1]: runtime "{OWN["name"]}" is rendered already'),
    (["--set", "modelServing.additionalRuntimes[0].image.name=serving/vllm"], "additionalRuntimes[0]: name is required"),
    (["--set", "modelServing.additionalRuntimes[0].name=Not_A_Subdomain"], "must be a lowercase DNS-1123 subdomain"),
    (["--set-json", 'modelServing.additionalRuntimes=["kserve-vllm-b"]'], "additionalRuntimes[0]: a runtime is a mapping"),
]:
    err = helm(CONN, [*SERVING, *flags], expect_fail=True)
    if needle not in err:
        fail(f"{' '.join(flags)} failed for the wrong reason:\n{err}")
ok("guards: the default's name reused, another entry's name reused, a missing name, a malformed name and a non-mapping entry fail the render naming the entry")

# --- the meta chart forwards the list, and its forwarded values render alike -
meta = documents(helm(META, [*META_SERVING, "-f", FIXTURE]))
releases = [d for d in meta if d.get("kind") == "HelmRelease" and d["metadata"]["name"] == "agent-platform-connectivity"]
expect("connectivity HelmReleases in the meta render", len(releases), 1)
forwarded = releases[0]["spec"]["values"]
expect("the forwarded modelServing.additionalRuntimes", forwarded.get("modelServing", {}).get("additionalRuntimes"), ENTRIES)
with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as f:
    yaml.safe_dump(forwarded, f)
try:
    through_meta = runtimes(documents(helm(CONN, ["--namespace", "agent-platform", "-f", f.name])))
finally:
    os.unlink(f.name)
if through_meta != runtimes(docs):
    fail(f"the runtimes the meta chart's forwarded values render differ from the connectivity chart's own fixture render:\n{yaml.safe_dump(through_meta)}\n--- connectivity:\n{yaml.safe_dump(runtimes(docs))}")
ok("the meta chart forwards modelServing.additionalRuntimes to the connectivity release, and the forwarded values render the same three runtimes")
