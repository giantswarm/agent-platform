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

--muster-egress asserts the other direction on a kubernetes-flavour render with
agent-manager's MCP endpoint on: every NetworkPolicy that selects muster makes
its egress default-deny beyond their union, so that union must reach
agent-manager on its port, each backend on its ports and DNS — without the
cilium-only supplement and whatever muster's own chart policy opens.

Usage: verify-mcp-backend-netpol.py {--cilium|--kubernetes|--off|--muster-egress} RENDER
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


def selects(selector, labels):
    """A label selector (matchLabels, matchExpressions with In) against a pod's labels."""
    if any(labels.get(k) != v for k, v in (selector.get("matchLabels") or {}).items()):
        return False
    for e in selector.get("matchExpressions") or []:
        if e["operator"] != "In" or labels.get(e["key"]) not in e["values"]:
            fail(f"the evaluator only reads In expressions, got {e}")
    return True


def peer_admits(peer, ns, labels):
    """One egress `to` peer of a policy in RELEASE_NS against a pod (ns, labels)."""
    if "ipBlock" in peer:
        return False  # an address block is not a pod selection: in-cluster reach is asserted by selector
    nsel = peer.get("namespaceSelector")
    if nsel is None:
        if ns != RELEASE_NS:
            return False
    elif not selects(nsel, {"kubernetes.io/metadata.name": ns}):
        return False
    return selects(peer.get("podSelector") or {}, labels)


def egress_allows(pols, ns, labels, port, protocol="TCP"):
    for pol in pols:
        for rule in pol["spec"].get("egress") or []:
            ports = rule.get("ports")
            if ports and not any(str(p.get("port")) == str(port) and p.get("protocol", "TCP") == protocol for p in ports):
                continue
            peers = rule.get("to")
            if not peers or any(peer_admits(p, ns, labels) for p in peers):
                return True
    return False


def check_muster_egress(path):
    with open(path) as f:
        docs = [d for d in yaml.safe_load_all(f) if d]
    if any(d.get("kind") == "CiliumNetworkPolicy" for d in docs):
        fail("the kubernetes-flavour render carries a CiliumNetworkPolicy")
    muster = {"app.kubernetes.io/name": "muster"}
    pols = [
        d for d in docs
        if d.get("kind") == "NetworkPolicy"
        and d["metadata"]["namespace"] == RELEASE_NS
        and "Egress" in d["spec"].get("policyTypes", [])
        and selects(d["spec"]["podSelector"], muster)
    ]
    names = sorted(p["metadata"]["name"] for p in pols)
    if "agent-platform-connectivity-muster-to-agent-manager" not in names:
        fail(f"the policies selecting muster for egress are {names}: the fixture lost agent-manager's MCP endpoint")
    am = next(d for d in docs if d["metadata"]["name"] == "agent-platform-connectivity-agent-manager-ingress")
    am_labels = am["spec"]["podSelector"]["matchLabels"]
    am_port = am["spec"]["ingress"][0]["ports"][0]["port"]
    if not egress_allows(pols, RELEASE_NS, am_labels, am_port):
        fail(f"muster's egress ({names}) does not reach agent-manager {am_labels} on {am_port}")
    for key, (ns, selector, ports, _) in WANT.items():
        for port in ports:
            if not egress_allows(pols, ns, selector, port):
                fail(f"muster's egress ({names}) does not reach the {key} backend in {ns} on {port}")
    dns = {"k8s-app": "kube-dns"}
    for protocol in ("UDP", "TCP"):
        if not egress_allows(pols, "kube-system", dns, 53, protocol):
            fail(f"muster's egress ({names}) does not reach kube-dns on 53/{protocol}")
    if egress_allows(pols, "mcp-pagerduty", {}, 9999):
        fail(f"muster's egress ({names}) reaches a backend on a port no entry names")
    print(f"OK: muster's kubernetes-flavour egress ({len(pols)} policies) reaches agent-manager, {len(WANT)} MCP backends and DNS")


def main():
    mode, path = sys.argv[1], sys.argv[2]
    if mode == "--muster-egress":
        check_muster_egress(path)
        return
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
