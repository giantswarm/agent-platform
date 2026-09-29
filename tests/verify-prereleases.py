#!/usr/bin/env python3
"""gitops.prereleases: off (default), every rendered range stays stable-only;
on, every component range and the self-management range admit pre-releases
and filter their tags to releases and release candidates. The exact version of
a component released with this chart, an exact pin (1.2.3, =1.2.3), an
exclusion (!=1.2.3) and a range filtered by a semverFilter stay as written; a
range that already carries a pre-release is filtered; a range with no version
fails the render.

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


def refs(manifest: str) -> dict[str, tuple[str, str]]:
    """OCIRepository name -> (spec.ref.semver, spec.ref.semverFilter or "")."""
    out = {}
    for d in manifest.split("\n---\n"):
        if not re.search(r"^kind: OCIRepository$", d, re.M):
            continue
        name = re.search(r"^  name: (\S+)$", d, re.M)
        semver = re.search(r'^    semver: "([^"]*)"$', d, re.M)
        flt = re.search(r'^    semverFilter: "([^"]*)"$', d, re.M)
        if name and semver:
            out[name.group(1)] = (semver.group(1), flt.group(1).replace("\\\\", "\\") if flt else "")
    return out


def ranges(manifest: str) -> dict[str, str]:
    """OCIRepository name -> spec.ref.semver."""
    return {n: r for n, (r, _) in refs(manifest).items()}


# Tags the registry holds next to the releases: a release candidate the filter
# lets through, and the branch builds of both dev shapes it must keep out.
TAGS_ADMITTED = ("5.31.4", "5.32.0-rc.1", "v5.32.0-rc.12")
TAGS_REFUSED = ("5.31.5-r961c88f6t20260929072057hee3d339", "5.9.9-dev.renovate-gi--mcp-go-1-x.2026-09-05.12-05-28.h6e32395")


def agent_charts(manifest: str) -> dict[str, tuple[str, str]]:
    """HelmRelease name -> (agentChart.semver, agentChart.semverFilter or "")
    for every release whose values carry agent-manager's agentChart block."""
    out = {}
    for d in manifest.split("\n---\n"):
        if not re.search(r"^kind: HelmRelease$", d, re.M):
            continue
        name = re.search(r"^  name: (\S+)$", d, re.M)
        block = re.search(r"^( +)agentChart:\n((?:\1 .*\n)+)", d + "\n", re.M)
        if not (name and block):
            continue
        fields = dict(re.findall(r"^ +(semver|semverFilter): (.*)$", block.group(2), re.M))
        unquote = lambda v: v[1:-1] if len(v) > 1 and v[0] == v[-1] and v[0] in "'\"" else v
        out[name.group(1)] = (unquote(fields.get("semver", "")), unquote(fields.get("semverFilter", "")))
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

    # Every widened range filters its tags to releases and release candidates:
    # a -0 range alone also selects the branch builds pushed to the same
    # repository. Off, no filter renders.
    for name, (constraint, flt) in refs(helm(chart, ON)).items():
        if name in exact:
            continue
        if not flt:
            fail(f"{name}: {constraint!r} renders no semverFilter; the branch builds of the repository would match it")
        for tag in TAGS_ADMITTED:
            if not re.search(flt, tag):
                fail(f"{name}: semverFilter {flt!r} refuses the release tag {tag}")
        for tag in TAGS_REFUSED:
            if re.search(flt, tag):
                fail(f"{name}: semverFilter {flt!r} admits the branch build {tag}")
    for name, (_, flt) in refs(helm(chart, [])).items():
        if flt:
            fail(f"{name}: semverFilter {flt!r} renders with gitops.prereleases off")

    # The self-management range is one of them (ci-values renders the engine).
    if RELEASE not in off:
        fail(f"no self-management OCIRepository {RELEASE} rendered; the test no longer covers its range")

    # A semverFilter selects a dev channel with its own range: left as written.
    filtered = refs(helm(chart, [*ON, "--set", "components.muster.semverFilter=^.*-rabc$", "--set", "components.muster.versionRange=>=5.0.0-0"]))
    if filtered.get("muster") != (">=5.0.0-0", "^.*-rabc$"):
        fail(f"muster with a semverFilter: {filtered.get('muster')!r}, want the range and the filter as written")

    # The agent chart line agent-manager composes for every agent, in the
    # agent-manager release and in the connectivity release (its migrate Job
    # reads the same block): widened and filtered on, as written off.
    on_chart = agent_charts(helm(chart, ON))
    off_chart = agent_charts(helm(chart, []))
    if not on_chart or on_chart.keys() != off_chart.keys():
        fail(f"agent-manager.agentChart renders in {sorted(off_chart)} off and {sorted(on_chart)} on")
    for release, (semver, flt) in off_chart.items():
        if flt or "-" in semver:
            fail(f"{release}: agentChart {semver!r} / {flt!r} with gitops.prereleases off")
        on_semver, on_flt = on_chart[release]
        rc = floor_rc(semver)
        if not fluxsemver.satisfies(rc, on_semver):
            fail(f"{release}: agentChart.semver {on_semver!r} does not admit {rc}")
        if not on_flt or any(re.search(on_flt, t) for t in TAGS_REFUSED) or not all(re.search(on_flt, t) for t in TAGS_ADMITTED):
            fail(f"{release}: agentChart.semverFilter {on_flt!r} is not the release tag filter")
    own = agent_charts(helm(chart, [*ON, "--set", "agent-manager.agentChart.semver=1.x", "--set", "agent-manager.agentChart.semverFilter=^1[.]x$"]))
    if set(own.values()) != {("1.x", "^1[.]x$")}:
        fail(f"an agentChart with its own semverFilter: {own}, want it as written")
    pinned = agent_charts(helm(chart, [*ON, "--set-string", "agent-manager.agentChart.semver=1.5.0"]))
    if set(pinned.values()) != {("1.5.0", "")}:
        fail(f"an agentChart pinned to 1.5.0: {pinned}, want the pin as written and no filter")
    # An exact pin and an exclusion stay as written: `-0` would turn 1.2.3
    # into a version that does not exist and stop != from excluding. A range
    # of those only admits no pre-release, so it gets no filter. A range that
    # carries a pre-release already admits them and gets the filter.
    for written, want, filtered in [
        ("1.2.3", "1.2.3", False),
        ("=1.2.3", "=1.2.3", False),
        ("==1.2.3", "==1.2.3", False),
        ("!=1.2.3", "!=1.2.3", False),
        (">=5.0.0-0", ">=5.0.0-0", True),
        (">=5.0.0-rc.1", ">=5.0.0-rc.1", True),
        ("1.2.3 || >=2.0.0 <3.0.0", "1.2.3 || >=2.0.0-0 <3.0.0-0", True),
    ]:
        got = refs(helm(chart, [*ON, "--set-string", f"components.muster.versionRange={written}"])).get("muster")
        if got is None or got[0] != want or bool(got[1]) != filtered:
            fail(f"components.muster.versionRange={written!r}: {got!r}, want {want!r} {'with' if filtered else 'without'} a filter")
        if want == "1.2.3" and not fluxsemver.satisfies("1.2.3", got[0]):
            fail(f"the exact pin {got[0]!r} does not admit 1.2.3")
    self_pin = refs(helm(chart, [*ON, "--set-string", "gitops.self.versionRange=1.1.35"])).get(RELEASE)
    if self_pin != ("1.1.35", ""):
        fail(f"gitops.self.versionRange=1.1.35: {self_pin!r}, want the pin as written and no filter")

    # A range with no version to mark would stay stable-only: refused.
    helm(chart, [*ON, "--set", "components.muster.versionRange=*"], expect_fail="has no version to admit pre-releases for")

    print(f"gitops.prereleases verified over {len(off)} ranges.")


if __name__ == "__main__":
    main()
