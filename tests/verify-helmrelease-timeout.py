#!/usr/bin/env python3
"""Assert the child HelmReleases' timeout and upgrade strategy in a meta render.

The substrate release carries spec.timeout 15m (a cold roll of 20+ atelet nodes
plus the WorkerPool exceeds helm-controller's 5m default) and the upgrade
strategy RetryOnFailure every 5m with no remediation block: Flux's rollback
remediation stalls a release whose rollback target is gone, and every
dependant with it (giantswarm/agent-platform#1367). Every other child keeps
helm-controller's default timeout, no strategy and gitops.retries. With
"remediate", the render set components.substrate.upgradeStrategy=null and
substrate is expected back on rollback remediation with gitops.retries.

Stdlib only, like the other render checks (the CI image has no PyYAML): the
HelmReleases are the chart's own render, two-space indented.

Usage: verify-helmrelease-timeout.py <render> [<retries> [remediate]]
"""

import re
import sys


def block(text, key, indent):
    """{k: v} of the scalar keys under `key:` at `indent` spaces, or None."""
    pad = " " * indent
    found = re.search(rf"^{pad}{key}:\n((?:{pad}  .*\n?)*)", text, re.M)
    if not found:
        return None
    return dict(re.findall(rf"^{pad}  (\w+): (\S+)$", found.group(1), re.M))


def helmreleases(path):
    """{name: (timeout, upgrade strategy, upgrade remediation)} of every HelmRelease in the render."""
    with open(path) as f:
        docs = f.read().split("\n---\n")
    releases = {}
    for doc in docs:
        if not re.search(r"^kind: HelmRelease$", doc, re.M):
            continue
        name = re.search(r"^  name: (\S+)$", doc, re.M).group(1)
        timeout = re.search(r"^  timeout: (\S+)$", doc, re.M)
        upgrade = re.search(r"^  upgrade:\n((?:    .*\n?)*)", doc, re.M)
        body = upgrade.group(1) if upgrade else ""
        releases[name] = (timeout.group(1) if timeout else None, block(body, "strategy", 4), block(body, "remediation", 4))
    return releases


def main() -> int:
    path = sys.argv[1]
    retries = sys.argv[2] if len(sys.argv) > 2 else "5"
    mode = sys.argv[3] if len(sys.argv) > 3 else ""
    if len(sys.argv) > 4 or mode not in ("", "remediate"):
        # A misspelt mode would otherwise silently check the RetryOnFailure
        # default against a render that set upgradeStrategy=null.
        print(f"usage: {sys.argv[0]} <render> [<retries> [remediate]]", file=sys.stderr)
        return 2
    remediate = mode == "remediate"
    releases = helmreleases(path)
    if "substrate" not in releases:
        print(f"FAIL: no substrate HelmRelease in {path}")
        return 1
    failures = []
    for name, got in sorted(releases.items()):
        if name == "substrate" and remediate:
            want = ("15m", None, {"retries": retries})
        elif name == "substrate":
            want = ("15m", {"name": "RetryOnFailure", "retryInterval": "5m"}, None)
        else:
            want = (None, None, {"retries": retries})
        if got != want:
            failures.append(
                f"{name}: timeout={got[0]!r} upgrade.strategy={got[1]!r} upgrade.remediation={got[2]!r}, "
                f"want timeout={want[0]!r} upgrade.strategy={want[1]!r} upgrade.remediation={want[2]!r}"
            )
    if failures:
        print("FAIL: child HelmRelease timeout/upgrade strategy:\n  " + "\n  ".join(failures))
        return 1
    substrate = f"no strategy and retries {retries}" if remediate else "upgrade RetryOnFailure every 5m and no remediation"
    print(
        f"ok: substrate timeout 15m, {substrate}; "
        f"{len(releases) - 1} other children on the default timeout and retries {retries}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
