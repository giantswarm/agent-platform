#!/usr/bin/env python3
"""Assert the child HelmReleases' timeout and upgrade remediation in a meta render.

The substrate release carries spec.timeout 15m (a cold roll of 20+ atelet nodes
plus the WorkerPool exceeds helm-controller's 5m default) and three upgrade
attempts; every other child keeps helm-controller's default timeout and
gitops.retries.

Stdlib only, like the other render checks (the CI image has no PyYAML): the
HelmReleases are the chart's own render, two-space indented.

Usage: verify-helmrelease-timeout.py <render> [<retries>]
"""

import re
import sys


def helmreleases(path):
    """{name: (timeout, upgrade retries)} of every HelmRelease in the render."""
    with open(path) as f:
        docs = f.read().split("\n---\n")
    releases = {}
    for doc in docs:
        if not re.search(r"^kind: HelmRelease$", doc, re.M):
            continue
        name = re.search(r"^  name: (\S+)$", doc, re.M).group(1)
        timeout = re.search(r"^  timeout: (\S+)$", doc, re.M)
        upgrade = re.search(r"^  upgrade:\n((?:    .*\n?)*)", doc, re.M)
        remediation = re.search(r"^    remediation:\n((?:      .*\n?)*)", upgrade.group(1) if upgrade else "", re.M)
        keys = dict(re.findall(r"^      (\w+): (\S+)$", remediation.group(1), re.M)) if remediation else None
        releases[name] = (timeout.group(1) if timeout else None, keys)
    return releases


def main() -> int:
    path = sys.argv[1]
    retries = sys.argv[2] if len(sys.argv) > 2 else "5"
    releases = helmreleases(path)
    if "substrate" not in releases:
        print(f"FAIL: no substrate HelmRelease in {path}")
        return 1
    failures = []
    for name, got in sorted(releases.items()):
        want = ("15m", {"retries": "3"}) if name == "substrate" else (None, {"retries": retries})
        if got != want:
            failures.append(f"{name}: timeout={got[0]!r} upgrade.remediation={got[1]!r}, want timeout={want[0]!r} upgrade.remediation={want[1]!r}")
    if failures:
        print("FAIL: child HelmRelease timeout/remediation:\n  " + "\n  ".join(failures))
        return 1
    print(f"ok: substrate timeout 15m, upgrade retries 3; {len(releases) - 1} other children on the default timeout and retries {retries}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
