#!/usr/bin/env python3
"""Assert muster-valkey's memory bound (giantswarm/agent-platform#446).

The meta chart forwards valkey.valkey.* to the valkey release, where the
upstream subchart appends valkey.valkey.valkeyConfig to the generated
valkey.conf. Without a fragment Valkey runs with `maxmemory 0`, the container
limit is its only bound and the kernel kills it once the dataset — a cache
that grows with sessions — reaches the limit (gazelle 2026-09-14). The default
render must therefore carry a `maxmemory` that leaves the fork's copy-on-write
pages and the client buffers their headroom under `resources.limits.memory`
(at or under two thirds of it), and an eviction policy that evicts TTL-carrying
keys rather than refusing writes.

Reads a rendered meta-package manifest (stdout of `helm template`, PyYAML);
with --override, the render with an installation's own valkeyConfig, which
must reach the release verbatim.
"""

import re
import sys

import yaml

HEADROOM = 2 / 3
OVERRIDE = "maxmemory 100mb\nmaxmemory-policy allkeys-lru\n"

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


def main() -> None:
    override = "--override" in sys.argv
    path = [a for a in sys.argv[1:] if not a.startswith("--")][0]
    with open(path, encoding="utf-8") as fh:
        vals = helm_release(fh.read(), "valkey")["spec"]["values"]["valkey"]

    fragment = vals.get("valkeyConfig")
    if not isinstance(fragment, str) or not fragment.strip():
        sys.exit("FAIL: the valkey release carries no valkey.valkeyConfig fragment — Valkey would run with maxmemory 0")

    if override:
        if fragment != OVERRIDE:
            sys.exit(f"FAIL: an installation's valkeyConfig did not reach the release verbatim: {fragment!r}")
        print("ok: an installation's own valkeyConfig travels to the valkey release verbatim")
        return

    directives = fragment_directives(fragment)
    if "maxmemory" not in directives:
        sys.exit(f"FAIL: valkeyConfig sets no maxmemory: {fragment!r}")
    if directives.get("maxmemory-policy") != "volatile-lru":
        sys.exit(f"FAIL: maxmemory-policy is {directives.get('maxmemory-policy')!r}, expected volatile-lru (evict TTL-carrying keys, never refuse writes)")
    if directives.get("appendonly", "no") != "no":
        sys.exit("FAIL: valkeyConfig enables AOF — the persistence note in values.yaml forbids it (data-loss footgun on the enabling deploy)")

    limit = quantity(vals["resources"]["limits"]["memory"])
    request = quantity(vals["resources"]["requests"]["memory"])
    maxmemory = quantity(directives["maxmemory"])
    if maxmemory > limit * HEADROOM:
        sys.exit(f"FAIL: maxmemory {directives['maxmemory']} is above two thirds of the {vals['resources']['limits']['memory']} limit — the BGSAVE fork's copy-on-write has no headroom under the cgroup")
    if maxmemory < request:
        sys.exit(f"FAIL: maxmemory {directives['maxmemory']} is below the {vals['resources']['requests']['memory']} request — the request would never be used")
    print(f"ok: maxmemory {directives['maxmemory']} = {maxmemory / limit:.0%} of the {vals['resources']['limits']['memory']} limit, policy volatile-lru, no AOF")


if __name__ == "__main__":
    main()
