#!/usr/bin/env python3
"""Assert the atenet router admits the kagent controller (giantswarm/agent-platform#827).

A turn's ingress is the kagent controller dialling atenet-router :8080
(atenetRouterURL). Under an enforcing CNI the router's policy decides whether it
lands, so the 8080 rule of substrate-atenet-router (cilium) and
substrate-atenet-router-ingress (kubernetes) has to admit exactly the
controller's pods, by the kagent chart's selector labels
(app.kubernetes.io/name: kagent, app.kubernetes.io/component: controller), in
the namespace the kagent chart renders the controller into: kagent.namespaceOverride,
the namespace the controller's own policies follow, never the connectivity
release's or the Substrate namespace.

Usage: verify-atenet-router-ingress.py {--cilium|--kubernetes} KAGENT_NS RENDER
"""
import sys

import yaml

CONTROLLER = {"app.kubernetes.io/name": "kagent", "app.kubernetes.io/component": "controller"}
NS_LABEL = {"--cilium": "io.kubernetes.pod.namespace", "--kubernetes": "kubernetes.io/metadata.name"}
POLICY = {
    "--cilium": ("CiliumNetworkPolicy", "substrate-atenet-router"),
    "--kubernetes": ("NetworkPolicy", "substrate-atenet-router-ingress"),
}


def fail(msg):
    print(f"FAIL: {msg}")
    sys.exit(1)


def policy(path, kind, name):
    with open(path) as f:
        for d in yaml.safe_load_all(f):
            if d and d.get("kind") == kind and d["metadata"]["name"] == name:
                return d
    fail(f"no {kind} {name}")


def peers_on_8080(flavor, pol):
    """The (namespace, pod labels) peers of the rules that open 8080."""
    peers = []
    for rule in pol["spec"].get("ingress") or []:
        if flavor == "--cilium":
            ports = [p["port"] for tp in rule.get("toPorts") or [] for p in tp["ports"]]
            if "8080" not in ports:
                continue
            if rule.get("fromEntities"):
                fail(f"8080 admits the entities {rule['fromEntities']}, not only the kagent controller")
            for ep in rule.get("fromEndpoints") or []:
                labels = dict(ep.get("matchLabels") or {})
                peers.append((labels.pop(NS_LABEL[flavor], None), labels))
        else:
            if 8080 not in [p.get("port") for p in rule.get("ports") or []]:
                continue
            if not rule.get("from"):
                fail("8080 is open to every peer, not only the kagent controller")
            for peer in rule["from"]:
                ns = (peer.get("namespaceSelector") or {}).get("matchLabels", {}).get(NS_LABEL[flavor])
                peers.append((ns, (peer.get("podSelector") or {}).get("matchLabels") or {}))
    return peers


def main():
    flavor, kagent_ns, path = sys.argv[1:4]
    kind, name = POLICY[flavor]
    peers = peers_on_8080(flavor, policy(path, kind, name))
    if not peers:
        fail(f"{name} opens 8080 to no one: the kagent controller's turns are dropped")
    for ns, labels in peers:
        if ns != kagent_ns:
            fail(f"{name} admits 8080 from namespace {ns!r}, not the kagent controller's ({kagent_ns!r}, kagent.namespaceOverride)")
        if labels != CONTROLLER:
            fail(f"{name} admits 8080 from pods {labels}, not the kagent controller's selector labels {CONTROLLER}")
    print(f"ok: {name} admits the kagent controller in {kagent_ns} on 8080 ({flavor[2:]})")


if __name__ == "__main__":
    main()
