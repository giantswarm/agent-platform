#!/usr/bin/env python3
"""Assert muster-valkey's memory bound (giantswarm/agent-platform#446) and its
readers (giantswarm/agent-platform#851).

The meta chart forwards valkey.valkey.* to the valkey release, where the
upstream subchart appends valkey.valkey.valkeyConfig to the generated
valkey.conf. Without a fragment Valkey runs with `maxmemory 0`, the container
limit is its only bound and the kernel kills it once the dataset — a cache
that grows with sessions — reaches the limit (gazelle 2026-09-14). The default
render must therefore carry a `maxmemory` that leaves the fork's copy-on-write
pages and the client buffers their headroom under `resources.limits.memory`
(at or under two thirds of it), and an eviction policy that evicts TTL-carrying
keys rather than refusing writes.

The default fragment also enables the append-only file (appendonly yes,
appendfsync everysec): losing writes since the last snapshot would bring a
deleted client or a revoked grant back after a crash. Valkey started with
appendonly on and no append-only file loads nothing and ignores dump.rdb, so
the render must carry the append-only-conversion init container, running the
server's own image with its resources and security context, whenever the
fragment enables appendonly, and none when it does not.

Reads a rendered meta-package manifest (stdout of `helm template`, PyYAML);
with --override, the render with an installation's own valkeyConfig, which
must reach the release verbatim.

The store's Cilium policy (valkey-app's ciliumNetworkPolicy.ingress.clients)
admits every pod of the release namespace unless the meta chart names the
readers. The default render must name exactly muster and klaus-gateway, by the
pod labels the connectivity chart's policies select them with: with
--selectors, a connectivity render whose muster-to-* and
klausgateway-store-egress policies must select those same labels, so a
renamed label cannot drift apart from the store's client list.
"""

import re
import sys

import yaml

HEADROOM = 2 / 3
OVERRIDE = "maxmemory 100mb\nmaxmemory-policy allkeys-lru\n"
# The store's readers in the release namespace ("" = the release's).
CLIENTS = [
    {"namespace": "", "matchLabels": {"app.kubernetes.io/name": "muster"}},
    {"namespace": "", "matchLabels": {"app.kubernetes.io/name": "klaus-gateway"}},
]

UNITS = {
    "": 1,
    "b": 1,
    "k": 1000, "kb": 1000, "m": 1000**2, "mb": 1000**2, "g": 1000**3, "gb": 1000**3,
    "ki": 1024, "mi": 1024**2, "gi": 1024**3,
}


def quantity(text: str) -> int:
    """Bytes of a Kubernetes quantity (1Gi, 256Mi) or a Valkey size (640mb, 1gb)."""
    m = re.fullmatch(r"(\d+)\s*([A-Za-z]*)", text.strip())
    if not m:
        sys.exit(f"FAIL: cannot parse a size out of {text!r}")
    return int(m.group(1)) * UNITS[m.group(2).lower()]


def fragment_directives(fragment: str) -> dict[str, str]:
    out = {}
    for line in fragment.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, _, value = line.partition(" ")
        out[key] = value.strip()
    return out


def helm_release(manifest: str, name: str) -> dict:
    for doc in yaml.safe_load_all(manifest):
        if doc and doc.get("kind") == "HelmRelease" and doc["metadata"]["name"] == name:
            return doc
    sys.exit(f"FAIL: no HelmRelease {name} in the render")


CONVERSION = "append-only-conversion"


def conversion_containers(vals: dict) -> list[dict]:
    return [c for c in vals.get("extraInitContainers") or [] if c.get("name") == CONVERSION]


def check_conversion(vals: dict) -> None:
    found = conversion_containers(vals)
    if len(found) != 1:
        sys.exit(f"FAIL: valkeyConfig enables appendonly but the release carries {len(found)} {CONVERSION} init containers, expected one — the enabling restart would load an empty store")
    container = found[0]
    image = vals.get("image") or {}
    want = f"{image.get('registry')}/{image.get('repository')}:{image.get('tag')}"
    if not image.get("tag") or container.get("image") != want:
        sys.exit(f"FAIL: {CONVERSION} runs {container.get('image')!r}, expected the server's own pinned image {want!r}")
    if container.get("resources") != vals.get("resources"):
        sys.exit(f"FAIL: {CONVERSION} resources {container.get('resources')!r} differ from the server's {vals.get('resources')!r} — it holds the whole dataset in memory")
    if container.get("securityContext") != vals.get("securityContext"):
        sys.exit(f"FAIL: {CONVERSION} securityContext differs from the server's")
    if {"name": "valkey-data", "mountPath": "/data"} not in container.get("volumeMounts", []):
        sys.exit(f"FAIL: {CONVERSION} does not mount the data volume at /data")
    print(f"ok: {CONVERSION} runs {want} with the server's resources and security context")


def check_clients(release: dict) -> None:
    clients = release.get("ciliumNetworkPolicy", {}).get("ingress", {}).get("clients")
    if clients != CLIENTS:
        sys.exit(f"FAIL: valkey.ciliumNetworkPolicy.ingress.clients is {clients!r}, expected muster and klaus-gateway only: {CLIENTS!r}")
    print("ok: muster-valkey admits muster and klaus-gateway on the Valkey port (host entity and metricsScrapers stay the wrapper's)")


def check_selectors(manifest: str) -> None:
    """The connectivity chart's policies select the store's readers by the client labels."""
    want = {"muster": CLIENTS[0]["matchLabels"], "klausgateway-store-egress": CLIENTS[1]["matchLabels"]}
    seen = {}
    for doc in yaml.safe_load_all(manifest):
        if not doc or doc.get("kind") != "CiliumNetworkPolicy":
            continue
        name = doc["metadata"]["name"]
        key = "muster" if "-muster-to-" in name else "klausgateway-store-egress" if name.endswith("-klausgateway-store-egress") else None
        if key is None:
            continue
        labels = doc["spec"]["endpointSelector"]["matchLabels"]
        if labels != want[key]:
            sys.exit(f"FAIL: {name} selects {labels!r}, but muster-valkey's client list names {want[key]!r}")
        seen.setdefault(key, []).append(name)
    missing = set(want) - set(seen)
    if missing:
        sys.exit(f"FAIL: the connectivity render has no {sorted(missing)} policy to compare the client labels with")
    print(f"ok: {sum(map(len, seen.values()))} connectivity policies select the readers by the client labels")


def main() -> None:
    override = "--override" in sys.argv
    path = [a for a in sys.argv[1:] if not a.startswith("--")][0]
    with open(path, encoding="utf-8") as fh:
        manifest = fh.read()
    if "--selectors" in sys.argv:
        check_selectors(manifest)
        return
    release = helm_release(manifest, "valkey")["spec"]["values"]
    vals = release["valkey"]

    fragment = vals.get("valkeyConfig")
    if not isinstance(fragment, str) or not fragment.strip():
        sys.exit("FAIL: the valkey release carries no valkey.valkeyConfig fragment — Valkey would run with maxmemory 0")

    if override:
        if fragment != OVERRIDE:
            sys.exit(f"FAIL: an installation's valkeyConfig did not reach the release verbatim: {fragment!r}")
        if conversion_containers(vals):
            sys.exit(f"FAIL: an own valkeyConfig without appendonly still carries the {CONVERSION} init container")
        print("ok: an installation's own valkeyConfig travels to the valkey release verbatim, without the append-only conversion")
        return

    check_clients(release)
    directives = fragment_directives(fragment)
    if "maxmemory" not in directives:
        sys.exit(f"FAIL: valkeyConfig sets no maxmemory: {fragment!r}")
    if directives.get("maxmemory-policy") != "volatile-lru":
        sys.exit(f"FAIL: maxmemory-policy is {directives.get('maxmemory-policy')!r}, expected volatile-lru (evict TTL-carrying keys, never refuse writes)")
    if directives.get("appendonly") != "yes" or directives.get("appendfsync") != "everysec":
        sys.exit(f"FAIL: valkeyConfig must enable the append-only file fsynced every second (appendonly yes, appendfsync everysec): {fragment!r}")
    check_conversion(vals)

    limit = quantity(vals["resources"]["limits"]["memory"])
    request = quantity(vals["resources"]["requests"]["memory"])
    maxmemory = quantity(directives["maxmemory"])
    if maxmemory > limit * HEADROOM:
        sys.exit(f"FAIL: maxmemory {directives['maxmemory']} is above two thirds of the {vals['resources']['limits']['memory']} limit — the BGSAVE fork's copy-on-write has no headroom under the cgroup")
    if maxmemory < request:
        sys.exit(f"FAIL: maxmemory {directives['maxmemory']} is below the {vals['resources']['requests']['memory']} request — the request would never be used")
    print(f"ok: maxmemory {directives['maxmemory']} = {maxmemory / limit:.0%} of the {vals['resources']['limits']['memory']} limit, policy volatile-lru, append-only everysec")


if __name__ == "__main__":
    main()
