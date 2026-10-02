#!/usr/bin/env python3
"""Assert the network policies of Substrate's bundled stores (giantswarm/agent-platform#381).

The substrate chart bundles two stores an installation runs when it has nothing
better: the single-instance Postgres StatefulSet (`app: postgres`, port 5432;
substrate.postgres resolving to `bundled`) and the rustfs snapshot store
(`app: rustfs`, port 9000; substrate.rustfs.enabled) with its bucket-init Job
(the Job controller's `job-name: rustfs-bucket-init` label). Under a default-deny
CNI an endpoint no policy selects is unreachable, so the connectivity chart
renders, in the Substrate namespace:

  cilium:     substrate-postgres            ingress ate-api-server :5432; egress DNS
              substrate-rustfs              ingress ate-api-server, atelet,
                                            bucket-init :9000; egress DNS
              substrate-rustfs-bucket-init  egress rustfs :9000, DNS
  kubernetes: substrate-postgres-ingress, substrate-rustfs-ingress (the flavour
              restricts what reaches a workload and leaves egress open)

and none of them while the store is not bundled (the CNPG Cluster, an external
database, an S3 snapshot store).

Usage: verify-substrate-store-netpol.py {--cilium|--kubernetes|--off} RENDER
"""
import sys

import yaml

NS = "ate-system"
NAMES = (
    "substrate-postgres",
    "substrate-rustfs",
    "substrate-rustfs-bucket-init",
    "substrate-postgres-ingress",
    "substrate-rustfs-ingress",
)
BUCKET_INIT = {"job-name": "rustfs-bucket-init"}


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
        and d["metadata"]["name"] in NAMES
    }


def get(pols, name, kind):
    pol = pols.get(name)
    if pol is None:
        fail(f"{name} does not render")
    if pol["kind"] != kind:
        fail(f"{name} is a {pol['kind']}, not a {kind}")
    if pol["metadata"].get("namespace") != NS:
        fail(f"{name} is in {pol['metadata'].get('namespace')}, not {NS}")
    return pol


def ports(rule):
    return sorted(
        (str(p["port"]), p.get("protocol", "TCP"))
        for tp in rule.get("toPorts", [])
        for p in tp.get("ports", [])
    )


def is_dns(rule):
    # agent-platform.dnsEgress: the cluster DNS pods in kube-system, on 53 and 1053.
    peers = rule.get("toEndpoints") or []
    return bool(peers) and all(
        e.get("matchLabels", {}).get("io.kubernetes.pod.namespace") == "kube-system"
        and e["matchLabels"].get("k8s-app") in ("kube-dns", "coredns", "k8s-dns-node-cache")
        for e in peers
    ) and {p for p, _ in ports(rule)} <= {"53", "1053"}


def cilium_ingress(pol, peers, port):
    rules = pol["spec"].get("ingress") or []
    if len(rules) != 1:
        fail(f"{pol['metadata']['name']}: {len(rules)} ingress rules, expected exactly one")
    rule = rules[0]
    if set(rule) - {"fromEndpoints", "toPorts"}:
        fail(f"{pol['metadata']['name']}: ingress admits more than endpoints ({sorted(rule)})")
    got = [e["matchLabels"] for e in rule["fromEndpoints"]]
    if sorted(map(repr, got)) != sorted(map(repr, peers)):
        fail(f"{pol['metadata']['name']}: ingress peers {got}, expected {peers}")
    if ports(rule) != [(port, "TCP")]:
        fail(f"{pol['metadata']['name']}: ingress ports {ports(rule)}, expected {port}/TCP")


def cilium_egress(pol, extra):
    """DNS and, if given, the one extra rule; nothing else."""
    rules = pol["spec"].get("egress") or []
    rest = [r for r in rules if not is_dns(r)]
    if len(rest) == len(rules):
        fail(f"{pol['metadata']['name']}: no DNS egress")
    if extra is None:
        if rest:
            fail(f"{pol['metadata']['name']}: egress beyond DNS: {rest}")
        return
    if len(rest) != 1:
        fail(f"{pol['metadata']['name']}: {len(rest)} egress rules beyond DNS, expected one")
    labels, port = extra
    got = [e["matchLabels"] for e in rest[0].get("toEndpoints", [])]
    if got != [labels] or ports(rest[0]) != [(port, "TCP")] or set(rest[0]) != {"toEndpoints", "toPorts"}:
        fail(f"{pol['metadata']['name']}: egress {rest[0]}, expected {labels} on {port}/TCP")


def k8s_ingress(pol, selector, apps, extra_peers, port):
    spec = pol["spec"]
    name = pol["metadata"]["name"]
    if spec["podSelector"] != {"matchLabels": selector}:
        fail(f"{name}: selects {spec['podSelector']}, expected {selector}")
    if spec.get("policyTypes") != ["Ingress"]:
        fail(f"{name}: policyTypes {spec.get('policyTypes')}, expected [Ingress]")
    rules = spec.get("ingress") or []
    if len(rules) != 1:
        fail(f"{name}: {len(rules)} ingress rules, expected exactly one")
    peers = rules[0]["from"]
    for p in peers:
        if set(p) != {"podSelector"}:
            fail(f"{name}: a peer beyond the namespace's pods: {p}")
    got_apps = [
        v
        for p in peers
        for e in p["podSelector"].get("matchExpressions", [])
        if e["key"] == "app" and e["operator"] == "In"
        for v in e["values"]
    ]
    got_labels = [p["podSelector"]["matchLabels"] for p in peers if "matchLabels" in p["podSelector"]]
    if sorted(got_apps) != sorted(apps) or got_labels != extra_peers:
        fail(f"{name}: peers {peers}, expected app in {apps} and {extra_peers}")
    got_ports = sorted((str(p["port"]), p["protocol"]) for p in rules[0]["ports"])
    if got_ports != [(port, "TCP")]:
        fail(f"{name}: ports {got_ports}, expected {port}/TCP")


def main():
    mode, path = sys.argv[1], sys.argv[2]
    pols = policies(path)
    if mode == "--off":
        if pols:
            fail(f"bundled-store policies render without the bundled stores: {sorted(pols)}")
        print("ok: no bundled-store policy without the bundled stores")
        return
    if mode == "--cilium":
        pg = get(pols, "substrate-postgres", "CiliumNetworkPolicy")
        if pg["spec"]["endpointSelector"] != {"matchLabels": {"app": "postgres"}}:
            fail(f"substrate-postgres selects {pg['spec']['endpointSelector']}")
        cilium_ingress(pg, [{"app": "ate-api-server"}], "5432")
        cilium_egress(pg, None)
        rf = get(pols, "substrate-rustfs", "CiliumNetworkPolicy")
        if rf["spec"]["endpointSelector"] != {"matchLabels": {"app": "rustfs"}}:
            fail(f"substrate-rustfs selects {rf['spec']['endpointSelector']}")
        cilium_ingress(rf, [{"app": "ate-api-server"}, {"app": "atelet"}, BUCKET_INIT], "9000")
        cilium_egress(rf, None)
        bi = get(pols, "substrate-rustfs-bucket-init", "CiliumNetworkPolicy")
        if bi["spec"]["endpointSelector"] != {"matchLabels": BUCKET_INIT}:
            fail(f"substrate-rustfs-bucket-init selects {bi['spec']['endpointSelector']}")
        if bi["spec"].get("ingress"):
            fail("substrate-rustfs-bucket-init admits ingress; the Job serves nothing")
        cilium_egress(bi, ({"app": "rustfs"}, "9000"))
        print("ok: cilium — Postgres from ate-api-server, rustfs from ate-api-server, atelet and the bucket-init Job, DNS out")
        return
    if mode == "--kubernetes":
        k8s_ingress(get(pols, "substrate-postgres-ingress", "NetworkPolicy"), {"app": "postgres"}, ["ate-api-server"], [], "5432")
        k8s_ingress(
            get(pols, "substrate-rustfs-ingress", "NetworkPolicy"),
            {"app": "rustfs"},
            ["ate-api-server", "atelet"],
            [BUCKET_INIT],
            "9000",
        )
        for name in ("substrate-postgres", "substrate-rustfs", "substrate-rustfs-bucket-init"):
            if name in pols:
                fail(f"the kubernetes flavour renders {name}, an egress policy it leaves to the CNI")
        print("ok: kubernetes — the two ingress policies, nothing else")
        return
    fail(f"unknown mode {mode}")


if __name__ == "__main__":
    main()
