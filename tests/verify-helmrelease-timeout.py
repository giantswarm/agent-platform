#!/usr/bin/env python3
"""Assert the child HelmReleases' timeout and upgrade remediation in a meta render.

The substrate release carries spec.timeout 15m (a cold roll of 20+ atelet nodes
plus the WorkerPool exceeds helm-controller's 5m default) and three upgrade
attempts; every other child keeps helm-controller's default timeout and
gitops.retries.

Usage: verify-helmrelease-timeout.py <render> [<retries>]
"""

import sys

import yaml

SUBSTRATE = {"timeout": "15m", "remediation": {"retries": 3}}


def main() -> int:
    path = sys.argv[1]
    retries = int(sys.argv[2]) if len(sys.argv) > 2 else 5
    with open(path) as f:
        releases = {
            d["metadata"]["name"]: d["spec"]
            for d in yaml.safe_load_all(f)
            if d and d.get("kind") == "HelmRelease"
        }
    if "substrate" not in releases:
        print(f"FAIL: no substrate HelmRelease in {path}")
        return 1
    failures = []
    for name, spec in sorted(releases.items()):
        want = SUBSTRATE if name == "substrate" else {"timeout": None, "remediation": {"retries": retries}}
        got = {"timeout": spec.get("timeout"), "remediation": spec.get("upgrade", {}).get("remediation")}
        if got != want:
            failures.append(f"{name}: timeout={got['timeout']!r} upgrade.remediation={got['remediation']!r}, want timeout={want['timeout']!r} upgrade.remediation={want['remediation']!r}")
    if failures:
        print("FAIL: child HelmRelease timeout/remediation:\n  " + "\n  ".join(failures))
        return 1
    print(f"ok: substrate timeout 15m, upgrade retries 3; {len(releases) - 1} other children on the default timeout and retries {retries}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
