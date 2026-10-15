#!/usr/bin/env python3
"""Assert the workers of the Organizations' namespaces are admitted (giantswarm/agent-platform#1036).

agent-manager's tenant reconciler puts a WorkerPool in each namespace labelled
giantswarm.io/organization that holds an agent. Its worker pods need what the
kagent namespace's workers get from this chart: the atenet router's egress to
them (cilium), the egress gateway's and the actors' dns's ingress from them
(both flavors), and the workers' Kyverno PolicyException.

With tenants.organizations.enabled, in both flavors:
- every rule that admits the kagent namespace's workers also admits the worker
  pods (ate.dev/worker-pool exists) of every namespace that carries the label
  (exists), on the same ports: the namespaces are selected by the label, never
  listed, and only worker pods are selected;
- the substrate-workers PolicyException matches the same pods in those
  namespaces by a namespaceSelector, excepts the same rules, and only when
  Substrate's controller chain writes them: ate-controller the Deployment, the
  deployment controller the ReplicaSet, the ReplicaSet controller the Pods (an
  Organization's admins may write pods there; a labelled pod they write is
  judged like any other);
- with the value on, no other object selects the label.
Off (the default), nothing selects the label. Needs PyYAML. HELM selects the
binary.

Usage: verify-tenant-workers.py CONNECTIVITY_CHART
"""

import os
import subprocess
import sys

import yaml

HELM = os.environ.get("HELM", "helm")
ORG = "giantswarm.io/organization"
WORKERS = {"key": "ate.dev/worker-pool", "operator": "Exists"}
CILIUM_ORG_PEER = {"matchExpressions": [WORKERS, {"key": f"io.cilium.k8s.namespace.labels.{ORG}", "operator": "Exists"}]}
K8S_ORG_PEER = {
    "namespaceSelector": {"matchExpressions": [{"key": ORG, "operator": "Exists"}]},
    "podSelector": {"matchExpressions": [WORKERS]},
}
BASE = [
    "--namespace", "agent-platform",
    "--set", "ingress.parentRefs[0].name=x",
    "--set", "kagent.harness.snapshotLocation=s3://ci-agent-snapshots/agents",
    "--set", "kagent.namespaceOverride=kagent",
    "--set", "components.kagent.enabled=true",
    "--set", "components.substrate.enabled=true",
    "--set", "components.substrate-crds.enabled=true",
    "--api-versions", "kyverno.io/v1",
    "--api-versions", "cilium.io/v2",
]
ON = ["--set", "tenants.organizations.enabled=true"]
# (kind, name, direction, port): the rules of each flavor that admit the
# kagent namespace's workers.
HOPS = {
    "cilium": [
        ("CiliumNetworkPolicy", "substrate-atenet-router", "egress", {"443", "8443"}),
        ("CiliumNetworkPolicy", "substrate-atenet-egress", "ingress", {"8443"}),
        ("CiliumNetworkPolicy", "substrate-dns", "ingress", {"53"}),
    ],
    "kubernetes": [
        ("NetworkPolicy", "substrate-atenet-egress-ingress", "ingress", {"8443"}),
        ("NetworkPolicy", "substrate-dns-ingress", "ingress", {"53"}),
    ],
}


def fail(msg):
    sys.exit(f"FAIL: {msg}")


def render(chart, args):
    r = subprocess.run([HELM, "template", "t", chart, *BASE, *args], capture_output=True, text=True)
    if r.returncode != 0:
        fail(f"helm template {' '.join(args)}:\n{r.stderr}")
    return [d for d in yaml.safe_load_all(r.stdout) if d]


def find(docs, kind, name):
    for d in docs:
        if d.get("kind") == kind and d["metadata"]["name"] == name:
            return d
    fail(f"no {kind} {name}")


def is_kagent_worker_peer(flavor, peer):
    if flavor == "cilium":
        exprs = peer.get("matchExpressions") or []
        return WORKERS in exprs and {"key": "io.kubernetes.pod.namespace", "operator": "In", "values": ["kagent"]} in exprs
    ns = (peer.get("namespaceSelector") or {}).get("matchLabels") or {}
    return ns == {"kubernetes.io/metadata.name": "kagent"} and peer.get("podSelector") == {"matchExpressions": [WORKERS]}


def rule_parts(flavor, direction, rule):
    """(peers, ports) of one rule."""
    if flavor == "cilium":
        peers = rule.get("toEndpoints" if direction == "egress" else "fromEndpoints") or []
        ports = {p["port"] for tp in rule.get("toPorts") or [] for p in tp["ports"]}
    else:
        peers = rule.get("from") or []
        ports = {str(p["port"]) for p in rule.get("ports") or []}
    return peers, ports


def check_hops(flavor, docs):
    expected = CILIUM_ORG_PEER if flavor == "cilium" else K8S_ORG_PEER
    for kind, name, direction, want_ports in HOPS[flavor]:
        rules = find(docs, kind, name)["spec"].get(direction) or []
        found = False
        for rule in rules:
            peers, ports = rule_parts(flavor, direction, rule)
            org_peers = [p for p in peers if ORG in yaml.safe_dump(p)]
            if not org_peers:
                continue
            if not any(is_kagent_worker_peer(flavor, p) for p in peers):
                fail(f"{name}: the Organizations' workers are admitted by a rule that does not admit the kagent namespace's workers")
            if org_peers != [expected]:
                fail(f"{name}: the Organizations' workers peer is {org_peers}, not the worker pods of every labelled namespace {expected}")
            if ports != want_ports:
                fail(f"{name}: the Organizations' workers get ports {sorted(ports)}, not the kagent workers' {sorted(want_ports)}")
            found = True
        if not found:
            fail(f"{name} ({flavor}): the Organizations' workers are not admitted; a tenant's first agent never turns Ready")


# The writers of the workers in an Organization's namespace, per kind: an
# Organization's admins may write pods there, so only Substrate's controller
# chain is excepted.
ATE_CONTROLLER = {"kind": "ServiceAccount", "name": "ate-controller", "namespace": "ate-system"}
DEPLOYMENT_CONTROLLER = {"kind": "ServiceAccount", "name": "deployment-controller", "namespace": "kube-system"}
REPLICASET_CONTROLLER = {"kind": "ServiceAccount", "name": "replicaset-controller", "namespace": "kube-system"}
WRITERS = {
    "Deployment": [ATE_CONTROLLER, DEPLOYMENT_CONTROLLER],
    "ReplicaSet": [DEPLOYMENT_CONTROLLER],
    "Pod": [REPLICASET_CONTROLLER, ATE_CONTROLLER],
}


def check_exception(on, off):
    polex_on = find(on, "PolicyException", "substrate-workers")["spec"]
    polex_off = find(off, "PolicyException", "substrate-workers")["spec"]
    if polex_on["exceptions"] != polex_off["exceptions"]:
        fail("the PolicyException excepts other rules with tenants.organizations on")
    kagent, *rest = polex_on["match"]["any"]
    if kagent != polex_off["match"]["any"][0]:
        fail("the kagent namespace's entry of substrate-workers changed with tenants.organizations on")
    got = {}
    for entry in rest:
        res = entry.get("resources") or {}
        if res.get("namespaces") or res.get("namespaceSelector") != {"matchExpressions": [{"key": ORG, "operator": "Exists"}]}:
            fail(f"substrate-workers: an Organizations' entry does not select every labelled namespace by the label: {entry}")
        if res.get("selector") != kagent["resources"]["selector"]:
            fail(f"substrate-workers: an Organizations' entry selects other pods than the kagent workers': {entry}")
        if set(entry) != {"resources", "subjects"}:
            fail(f"substrate-workers: an Organizations' entry is not limited to its writers: {entry}")
        for kind in res.get("kinds") or []:
            if kind in got:
                fail(f"substrate-workers: {kind} matched twice in the Organizations' namespaces")
            got[kind] = entry["subjects"]
    if got != WRITERS:
        fail(f"substrate-workers: the Organizations' workers are excepted for writers {got}, not Substrate's controller chain {WRITERS}; anyone who writes a labelled pod there would get the workers' exception")


def main():
    chart = sys.argv[1]
    for flavor in ("cilium", "kubernetes"):
        args = ["--set", f"networkPolicy.flavor={flavor}"]
        off = render(chart, args)
        if any(ORG in yaml.safe_dump(d) for d in off if d["metadata"]["name"].startswith("substrate-")):
            fail(f"tenants.organizations off ({flavor}): a Substrate policy or exception selects {ORG}")
        on = render(chart, args + ON)
        selecting = {(d["kind"], d["metadata"]["name"]) for d in on if ORG in yaml.safe_dump(d)}
        want = {(kind, name) for kind, name, _, _ in HOPS[flavor]} | {("PolicyException", "substrate-workers")}
        if selecting != want:
            fail(f"tenants.organizations on ({flavor}): {sorted(selecting)} select {ORG}, not exactly {sorted(want)}")
        check_hops(flavor, on)
        check_exception(on, off)
        print(f"ok: the Organizations' workers admitted on the router, the egress gateway, the dns and the PolicyException, nothing off ({flavor})")


if __name__ == "__main__":
    main()
