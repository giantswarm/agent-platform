#!/usr/bin/env python3
"""gitops.prereleases: off (default), every rendered range stays stable-only;
on, every component range and the self-management range admit pre-releases,
while the exact version of a component released with this chart and a range
filtered by a semverFilter stay as written, and a range with no version to
mark fails the render.

"Admits pre-releases" is checked the way Flux evaluates the range
(tests/fluxsemver.py): an rc of the range's floor admits, a stable version
still does. Deliberately stdlib-only: the CI image has no PyYAML. HELM selects
the binary.
"""

import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fluxsemver  # noqa: E402

HELM = os.environ.get("HELM", "helm")
RELEASE = "agent-platform"
NAMESPACE = "agent-platform"
ON = ["--set", "gitops.prereleases=true"]


def fail(msg: str) -> None:
    sys.exit(f"FAIL: {msg}")


def helm(chart: str, flags: list[str], expect_fail: str | None = None) -> str:
    cmd = [HELM, "template", RELEASE, chart, "-n", NAMESPACE, "-f", f"{chart}/ci/ci-values.yaml", *flags]
    r = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if expect_fail is not None:
        if r.returncode == 0:
            fail(f"render succeeded but should have failed: {' '.join(flags)}")
        if expect_fail not in r.stderr:
            fail(f"render failed for the wrong reason ({' '.join(flags)}):\n{r.stderr}")
        return r.stderr
    if r.returncode != 0:
        fail(f"render failed: {' '.join(cmd)}\n{r.stderr}")
    return r.stdout


def ranges(manifest: str) -> dict[str, str]:
    """OCIRepository name -> spec.ref.semver."""
    out = {}
    for d in manifest.split("\n---\n"):
        if not re.search(r"^kind: OCIRepository$", d, re.M):
            continue
        name = re.search(r"^  name: (\S+)$", d, re.M)
        semver = re.search(r'^    semver: "([^"]*)"$', d, re.M)
        if name and semver:
            out[name.group(1)] = semver.group(1)
    return out


def floor_rc(constraint: str) -> str | None:
    """An rc of the range's first version: `>=5.31.4-0 <6.0.0-0` -> 5.31.4-rc.1,
    `0.x-0` -> 0.0.0-rc.1."""
    m = re.search(r"(\d+)(?:\.(\d+|[xX*]))?(?:\.(\d+|[xX*]))?", constraint)
    if not m:
        return None
    parts = [p if p and p.isdigit() else "0" for p in m.groups()]
    return f"{parts[0]}.{parts[1]}.{parts[2]}-rc.1"


def main() -> None:
    chart = sys.argv[1]

    off = ranges(helm(chart, []))
    on = ranges(helm(chart, ON))
    if not off or off.keys() != on.keys():
        fail(f"the switch changes which OCIRepositories render: off {sorted(off)} on {sorted(on)}")

    exact = [n for n, c in off.items() if fluxsemver.parse(c) is not None]
    if not exact:
        fail("no component released with this chart renders an exact version; the test no longer covers it")
    for name, constraint in off.items():
        if "-" in constraint:
            fail(f"{name}: {constraint!r} carries a pre-release with gitops.prereleases off")
        if name in exact:
            if on[name] != constraint:
                fail(f"{name}: the exact version {constraint!r} became {on[name]!r}")
            continue
        rc = floor_rc(constraint)
        if rc is None:
            fail(f"{name}: no version in {constraint!r}")
        if fluxsemver.satisfies(rc, constraint):
            fail(f"{name}: off, {constraint!r} admits {rc}")
        if not fluxsemver.satisfies(rc, on[name]):
            fail(f"{name}: on, {on[name]!r} does not admit {rc}")
        stable = rc.split("-")[0]
        if fluxsemver.satisfies(stable, constraint) and not fluxsemver.satisfies(stable, on[name]):
            fail(f"{name}: on, {on[name]!r} no longer admits the stable {stable}")

    # The self-management range is one of them (ci-values renders the engine).
    if RELEASE not in off:
        fail(f"no self-management OCIRepository {RELEASE} rendered; the test no longer covers its range")

    # A semverFilter selects a dev channel with its own range: left as written.
    filtered = ranges(helm(chart, [*ON, "--set", "components.muster.semverFilter=^.*-rabc$", "--set", "components.muster.versionRange=>=5.0.0-0"]))
    if filtered.get("muster") != ">=5.0.0-0":
        fail(f"muster with a semverFilter: {filtered.get('muster')!r}, want the range as written")

    # A range with no version to mark would stay stable-only: refused.
    helm(chart, [*ON, "--set", "components.muster.versionRange=*"], expect_fail="has no version to admit pre-releases for")

    print(f"gitops.prereleases verified over {len(off)} ranges.")


if __name__ == "__main__":
    main()
