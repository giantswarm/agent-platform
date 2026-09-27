#!/usr/bin/env python3
"""Assert the fast-link input of the model serving layer (modelServing.fastLinks, giantswarm/model-manager#190).

A fast link joins nodes by RDMA; model-manager splits one model across the
nodes of one (placement split) and never across nodes without one. The
connectivity chart publishes the list in the discovery ConfigMap as
spec.fastLinks exactly as written; empty (the default) renders no key, so the
discovery document of an installation without fast links is unchanged. The
meta chart forwards the block with modelServing.

A split runs as a LeaderWorkerSet: its workers carry no
kserve.io/component=workload, so the model pod selector every serving policy
renders from matches them by app.kubernetes.io/component; their StatefulSets
fall under the model pods' PolicyException; and, with a fast link declared,
the leader and workers may add the multi-node template's capabilities and
reach each other on every port — a single-node model pod gains neither.

usage: verify-fast-links.py <meta chart dir> <connectivity chart dir>
"""
import json
import os
import subprocess
import sys

import yaml

META, CONN = sys.argv[1], sys.argv[2]
HELM = os.environ.get("HELM") or "helm"
SERVING = [
    "--namespace", "agent-platform",
    "--set", "global.gatewayApi.parentRefs[0].name=giantswarm-default",
    "--set", "global.gatewayApi.parentRefs[0].namespace=envoy-gateway-system",
    "--set", "components.kserve-llmisvc-crd.enabled=true",
    "--set", "components.kserve-llmisvc-resources.enabled=true",
    "--set", "components.modelServing.enabled=true",
]
LINKS = [{
    "name": "gpu-pair",
    "nodes": ["gpu-a", "gpu-b"],
    "networks": ["roce-a", "roce-b"],
    "resources": {"rdma/rdma_shared_device_a": "1", "rdma/rdma_shared_device_b": "1"},
    "env": [{"name": "NCCL_IB_HCA", "value": "mlx5_0,mlx5_1"}],
}]


def fail(msg: str) -> None:
    print(f"FAIL: {msg}")
    sys.exit(1)


def ok(msg: str) -> None:
    print(f"ok: {msg}")


def helm(chart: str, flags: list) -> str:
    res = subprocess.run([HELM, "template", "t", chart, *flags], capture_output=True, text=True)
    if res.returncode != 0:
        fail(f"helm template {chart} failed: {res.stderr.strip()}")
    return res.stdout


def discovery(render: str) -> dict:
    for doc in yaml.safe_load_all(render):
        if doc and doc.get("kind") == "ConfigMap" and doc["metadata"]["name"] == "agent-platform-model-serving":
            return yaml.safe_load(doc["data"]["config.yaml"])["spec"]
    fail("the discovery ConfigMap agent-platform-model-serving is not rendered")


spec = discovery(helm(CONN, SERVING))
if "fastLinks" in spec:
    fail(f"default: the discovery document carries fastLinks {spec['fastLinks']!r}; no fast link renders no key")
ok("default: no fast link, no spec.fastLinks key")

spec = discovery(helm(CONN, [*SERVING, "--set-json", "modelServing.fastLinks=" + json.dumps(LINKS)]))
if spec.get("fastLinks") != LINKS:
    fail(f"spec.fastLinks {spec.get('fastLinks')!r} != modelServing.fastLinks {LINKS!r}")
ok("set: spec.fastLinks published as written (name, nodes, networks, resources, env)")

MULTI = {"llminferenceservice-workload-leader", "llminferenceservice-workload-worker",
         "llminferenceservice-workload-leader-prefill", "llminferenceservice-workload-worker-prefill"}


def docs(render: str) -> list:
    return [d for d in yaml.safe_load_all(render) if d]


def named(objects: list, kind: str, name: str):
    return next((d for d in objects if d["kind"] == kind and d["metadata"]["name"] == name), None)


def components(expressions: list) -> set:
    for e in expressions:
        if e["key"] == "app.kubernetes.io/component" and e["operator"] == "In":
            return set(e["values"])
    fail(f"no app.kubernetes.io/component In selector in {expressions!r}")


for flavor in ("cilium", "kubernetes"):
    flags = [*SERVING, "--set", f"networkPolicy.flavor={flavor}"]
    plain, linked = docs(helm(CONN, flags)), docs(helm(CONN, [*flags, "--set-json", "modelServing.fastLinks=" + json.dumps(LINKS)]))
    kind = "CiliumNetworkPolicy" if flavor == "cilium" else "NetworkPolicy"
    if named(plain, kind, "agent-platform-connectivity-model-serving-multi-node"):
        fail(f"{flavor}: the multi-node policy renders without a fast link")
    pol = named(linked, kind, "agent-platform-connectivity-model-serving-multi-node")
    if not pol:
        fail(f"{flavor}: no multi-node policy with a fast link declared")
    sel = pol["spec"]["endpointSelector" if flavor == "cilium" else "podSelector"]["matchExpressions"]
    if components(sel) != MULTI:
        fail(f"{flavor}: the multi-node policy selects {components(sel)!r}, want the LeaderWorkerSet pods {MULTI!r}")
    ok(f"{flavor}: with a fast link the leader and workers reach each other on every port; none without")

KYVERNO = [*SERVING, "--api-versions", "kyverno.io/v1"]
linked = docs(helm(CONN, [*KYVERNO, "--set-json", "modelServing.fastLinks=" + json.dumps(LINKS)]))
pods = named(linked, "PolicyException", "model-serving-predictors")
by_labels = pods["spec"]["match"]["any"][0]["resources"]
if "StatefulSet" not in by_labels["kinds"] or not MULTI <= components(by_labels["selector"]["matchExpressions"]):
    fail(f"model-serving-predictors does not cover a LeaderWorkerSet's pods and StatefulSets: {by_labels!r}")
if "kserve.io/component" in json.dumps(by_labels):
    fail("the model pod selector still requires kserve.io/component, which a LeaderWorkerSet's workers do not carry")
ok("the model pods' PolicyException covers a LeaderWorkerSet's pods (by app.kubernetes.io/component) and StatefulSets")

if named(linked, "PolicyException", "model-serving-fast-links"):
    fail("model-serving-fast-links renders; a split's pods add no capability (giantswarm/model-manager#193)")
ok("no PolicyException lets a split's pods add capabilities")

meta = yaml.safe_load(open(os.path.join(META, "values.yaml"), encoding="utf-8"))
conn = yaml.safe_load(open(os.path.join(CONN, "values.yaml"), encoding="utf-8"))
for name, values in (("meta", meta), ("connectivity", conn)):
    if values["modelServing"].get("fastLinks") != []:
        fail(f"the {name} chart's modelServing.fastLinks default is {values['modelServing'].get('fastLinks')!r}, want []")
ok("both charts default modelServing.fastLinks to [] (the meta copy forwarded with modelServing)")
print("fast links verified.")
