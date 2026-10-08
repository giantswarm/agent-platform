#!/usr/bin/env python3
"""Assert the serving preset catalog is published wherever the connectivity chart runs (giantswarm/agent-platform#847).

model-manager's check_fit answers a preset's declaration before a GPU pool and
its backend exist, from the ServingPreset ConfigMaps
(agent-platform.giantswarm.io/serving-preset=true) in the release namespace. So
the catalog renders with components.modelServing.enabled off too, while what
serving runs and mounts stays behind the switch. Each case below pins one
property:

- serving off (the chart's default): one preset ConfigMap per shipped preset in
  the release namespace, each with its preset, preset-source and serving-preset
  labels and the chart-version annotation; no discovery ConfigMap, no
  chat-template ConfigMap and no other model-serving object (namespace,
  policies, pre-pull, cache hook, Gateway);
- serving off honours the catalog's knobs: shippedPresets.exclude drops a
  shipped preset, a modelServing.presets entry is published as source values,
  shippedPresets.enabled false publishes the values presets alone;
- serving on: the same preset ConfigMaps byte for byte, beside the discovery
  ConfigMap and the chat-template ConfigMaps in the serving namespace.

Needs PyYAML. HELM selects the binary.
"""

import glob
import os
import subprocess
import sys

import yaml

HELM = os.environ.get("HELM", "helm")
CONN = sys.argv[1]
NAMESPACE = "agent-platform"
BASE = [
    "--namespace", NAMESPACE,
    "--set", "kagent.harness.snapshotLocation=s3://ci-agent-snapshots/agents",
    "--set", "global.gatewayApi.parentRefs[0].name=giantswarm-default",
    "--set", "global.gatewayApi.parentRefs[0].namespace=envoy-gateway-system",
]
SERVING = [
    "--set", "components.kserve-llmisvc-crd.enabled=true",
    "--set", "components.kserve-llmisvc-resources.enabled=true",
    "--set", "components.modelServing.enabled=true",
    "--set", "modelServing.namespace.name=model-serving",
]
PRESET_LABEL = "agent-platform.giantswarm.io/serving-preset"
VALUES_PRESET = {
    "apiVersion": "agent-platform.giantswarm.io/v1alpha1",
    "kind": "ServingPreset",
    "metadata": {"name": "catalog-probe"},
    "spec": {
        "displayName": "Catalog probe",
        "model": {"id": "org/Catalog-Probe", "storageUri": "hf://org/Catalog-Probe"},
        "requirements": {"weightsGiB": 1, "minComputeCapability": "8.0"},
    },
}


def fail(msg: str) -> None:
    print(f"FAIL: {msg}")
    sys.exit(1)


def ok(msg: str) -> None:
    print(f"ok: {msg}")


def render(flags: list) -> list:
    proc = subprocess.run([HELM, "template", "t", CONN, *BASE, *flags], capture_output=True, text=True)
    if proc.returncode != 0:
        fail(f"render failed ({' '.join(flags) or 'defaults'}):\n{proc.stderr}")
    return [d for d in yaml.safe_load_all(proc.stdout) if d]


def labels(doc: dict) -> dict:
    return doc.get("metadata", {}).get("labels") or {}


def presets(docs: list) -> dict:
    return {d["metadata"]["name"]: d for d in docs if d.get("kind") == "ConfigMap" and labels(d).get(PRESET_LABEL) == "true"}


def serving_objects(docs: list) -> list:
    """Every model-serving object that is not a published preset."""
    return [
        f"{d.get('kind')}/{d['metadata']['name']}"
        for d in docs
        if labels(d).get("app.kubernetes.io/component") == "model-serving" and labels(d).get(PRESET_LABEL) != "true"
    ]


def with_preset_values(flags: list, path: str) -> list:
    with open(path, "w") as f:
        yaml.safe_dump({"modelServing": {"presets": [VALUES_PRESET]}}, f)
    return [*flags, "-f", path]


shipped = sorted(os.path.basename(p)[: -len(".yaml")] for p in glob.glob(f"{CONN}/files/model-serving/presets/*.yaml"))
if not shipped:
    fail(f"no shipped preset under {CONN}/files/model-serving/presets")
with open(f"{CONN}/Chart.yaml") as f:
    chart_version = yaml.safe_load(f)["version"]
cm = "agent-platform-serving-preset-"

# --- serving off: the catalog, nothing else of the serving layer
off = render([])
published = presets(off)
if sorted(published) != [cm + n for n in shipped]:
    fail(f"serving off publishes {sorted(published)}, want one ConfigMap per shipped preset {shipped}")
for name, doc in published.items():
    meta = doc["metadata"]
    preset = name[len(cm):]
    if meta.get("namespace") != NAMESPACE:
        fail(f"{name} is in namespace {meta.get('namespace')!r}, want the release namespace {NAMESPACE!r}")
    want = {"agent-platform.giantswarm.io/preset": preset, "agent-platform.giantswarm.io/preset-source": "shipped"}
    for key, value in want.items():
        if labels(doc).get(key) != value:
            fail(f"{name}: label {key} is {labels(doc).get(key)!r}, want {value!r}")
    if (meta.get("annotations") or {}).get("agent-platform.giantswarm.io/chart-version") != chart_version:
        fail(f"{name}: the chart-version annotation is not the chart's version {chart_version!r}")
    if yaml.safe_load(doc["data"]["preset.yaml"]).get("metadata", {}).get("name") != preset:
        fail(f"{name}: preset.yaml does not carry the preset {preset!r}")
ok(f"serving off publishes the {len(shipped)} shipped presets in {NAMESPACE} with labels and the chart-version annotation")
if serving_objects(off):
    fail(f"serving off renders model-serving objects beside the catalog: {serving_objects(off)}")
ok("serving off renders no discovery ConfigMap, no chat-template ConfigMap and no serving workload, policy or namespace")

# --- serving off honours the catalog's knobs
tmp = os.environ.get("VERIFY_TMP", "/tmp")
os.makedirs(tmp, exist_ok=True)
values = f"{tmp}/preset-catalog-values.yaml"
knobs = presets(render(with_preset_values(["--set", f"modelServing.shippedPresets.exclude[0]={shipped[0]}"], values)))
want = sorted([cm + n for n in shipped[1:]] + [cm + "catalog-probe"])
if sorted(knobs) != want:
    fail(f"serving off with exclude and a values preset publishes {sorted(knobs)}, want {want}")
if labels(knobs[cm + "catalog-probe"]).get("agent-platform.giantswarm.io/preset-source") != "values":
    fail("the values preset is not published as preset-source values")
ok("serving off: shippedPresets.exclude drops the shipped preset, a modelServing.presets entry is published as values")
values_only = presets(render(with_preset_values(["--set", "modelServing.shippedPresets.enabled=false"], values)))
if sorted(values_only) != [cm + "catalog-probe"]:
    fail(f"serving off with shippedPresets.enabled false publishes {sorted(values_only)}, want the values preset alone")
ok("serving off: shippedPresets.enabled false publishes the values presets alone")

# --- serving on: the same catalog, beside the discovery and chat-template ConfigMaps
on = render(SERVING)
if presets(on) != published:
    fail("serving on publishes a different catalog than serving off")
kinds = {(d.get("kind"), d["metadata"]["name"], d["metadata"].get("namespace")) for d in on}
if ("ConfigMap", "agent-platform-model-serving", NAMESPACE) not in kinds:
    fail("serving on renders no discovery ConfigMap")
templates = [d for d in on if labels(d).get("agent-platform.giantswarm.io/serving-asset") == "chat-template"]
if not templates or any(d["metadata"].get("namespace") != "model-serving" for d in templates):
    fail("serving on renders no chat-template ConfigMap in the serving namespace")
ok("serving on publishes the same catalog beside the discovery ConfigMap and the chat-template ConfigMaps")

print("preset catalog verified.")
