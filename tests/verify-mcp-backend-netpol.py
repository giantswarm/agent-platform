#!/usr/bin/env python3
"""Assert the ingress policies of the in-cluster MCP backends (networkPolicy.mcpBackends).

A backend muster registers is reachable only through muster: each entry of
tests/fixtures/mcp-backends-values.yaml renders one policy in the backend's
namespace, named agent-platform-connectivity-mcp-backend-<key>, that admits on
the entry's ports exactly muster from the release namespace, plus the scrapers
(metrics: true) and the entry's additionalPeers; the namespace's own pods on
every port; in the cilium flavour the kubelet's probes (the host entity) on
every port, since a backend may probe on another port than it serves. Nothing renders without entries or with
networkPolicy.enabled off.

Usage: verify-mcp-backend-netpol.py {--cilium|--kubernetes|--off} RENDER
"""
import sys

import yaml

RELEASE_NS = "agent-platform"
PREFIX = "agent-platform-connectivity-mcp-backend-"
MUSTER = (RELEASE_NS, {"app.kubernetes.io/name": "muster"})
SCRAPER = ("kube-system", {"app.kubernetes.io/instance": "alloy-metrics"})
ENVOY = ("envoy-gateway-system", {"app.kubernetes.io/name": "envoy"})
# key: (namespace, pod selector, ports, callers besides the namespace's own pods)
WANT = {
    "pagerduty": ("mcp-pagerduty", {}, ["8080"], [MUSTER]),
    "runbooks": ("mcp-runbooks", {"app.kubernetes.io/name": "mcp-runbooks"}, ["8080"], [MUSTER, SCRAPER]),
    "pro": ("mcp-pro", {}, ["8080", "9090"], [MUSTER, ENVOY]),
}


def fail(msg):
    print(f"FAIL: {msg}")
    sys.exit(1)


def policies(path):
    with open(path) as f:
        docs = [d for d in yaml.safe_load_all(f) if d]
    return {
        d["metadata"]["name"]: d
        for d in docs
        if d.get("kind") in ("CiliumNetworkPolicy", "NetworkPolicy")
        and d["metadata"]["name"].startswith(PREFIX)
    }


def cilium_peer(sel):
    labels = dict(sel["matchLabels"])
    return labels.pop("io.kubernetes.pod.namespace"), labels


def check_cilium(name, pol, ns, selector, ports, callers):
    spec = pol["spec"]
    if (spec["endpointSelector"] or {}).get("matchLabels", {}) != selector:
        fail(f"{name} selects {spec['endpointSelector']}, want {selector or 'every pod'}")
    rules = spec["ingress"]
    if len(rules) != 3:
        fail(f"{name} carries {len(rules)} ingress rules, want 3 (callers, namespace, probes)")
    callers_rule, own_rule, probe_rule = rules
    got = [cilium_peer(e) for e in callers_rule["fromEndpoints"]]
    if got != callers:
        fail(f"{name} admits {got} on its ports, want {callers}")
    got_ports = [p["port"] for tp in callers_rule["toPorts"] for p in tp["ports"]]
    if got_ports != ports:
        fail(f"{name}: the callers rule opens {got_ports}, want {ports}")
    if own_rule != {"fromEndpoints": [{"matchLabels": {"io.kubernetes.pod.namespace": ns}}]}:
        fail(f"{name}: the namespace rule is {own_rule}, want the namespace's own pods on every port")
    if probe_rule != {"fromEntities": ["host"]}:
        fail(f"{name}: the probe rule is {probe_rule}, want the host entity on every port")


def check_kubernetes(name, pol, ns, selector, ports, callers):
    spec = pol["spec"]
    if (spec["podSelector"] or {}).get("matchLabels", {}) != selector:
        fail(f"{name} selects {spec['podSelector']}, want {selector or 'every pod'}")
    if spec.get("policyTypes") != ["Ingress"]:
        fail(f"{name} has policyTypes {spec.get('policyTypes')}, want [Ingress]")
    rules = spec["ingress"]
    if len(rules) != 2:
        fail(f"{name} carries {len(rules)} ingress rules, want 2 (callers, namespace)")
    callers_rule, own_rule = rules
    got = [
        (p["namespaceSelector"]["matchLabels"]["kubernetes.io/metadata.name"], p["podSelector"]["matchLabels"])
        for p in callers_rule["from"]
    ]
    if got != callers:
        fail(f"{name} admits {got} on its ports, want {callers}")
    got_ports = [str(p["port"]) for p in callers_rule["ports"]]
    if got_ports != ports:
        fail(f"{name}: the callers rule opens {got_ports}, want {ports}")
    if own_rule != {"from": [{"podSelector": {}}]}:
        fail(f"{name}: the namespace rule is {own_rule}, want the namespace's own pods on every port")


def main():
    mode, path = sys.argv[1], sys.argv[2]
    pols = policies(path)
    if mode == "--off":
        if pols:
            fail(f"{sorted(pols)} render, want none")
        print("OK: no MCP backend policy renders")
        return
    kind, check = {
        "--cilium": ("CiliumNetworkPolicy", check_cilium),
        "--kubernetes": ("NetworkPolicy", check_kubernetes),
    }[mode]
    if sorted(pols) != sorted(PREFIX + k for k in WANT):
        fail(f"rendered {sorted(pols)}, want {sorted(PREFIX + k for k in WANT)}")
    for key, (ns, selector, ports, callers) in WANT.items():
        name = PREFIX + key
        pol = pols[name]
        if pol["kind"] != kind:
            fail(f"{name} is a {pol['kind']}, not a {kind}")
        if pol["metadata"]["namespace"] != ns:
            fail(f"{name} is in {pol['metadata']['namespace']}, not {ns}")
        check(name, pol, ns, selector, ports, callers)
    print(f"OK: {len(WANT)} MCP backends admit only muster, their namespace and their named callers ({kind})")


if __name__ == "__main__":
    main()
