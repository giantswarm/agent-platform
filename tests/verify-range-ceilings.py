#!/usr/bin/env python3
"""Every component range of the meta chart is capped at the release line its
rendered values were proven against (giantswarm/agent-platform#1094).

Flux re-resolves each OCIRepository's range on every poll, so whatever a range
admits reaches an installed chart without a release of this one. A range that
stays open across a breaking line lets a component release this chart's values
were never rendered against roll into an installation by itself — the 0.x
workspace-manager range below 1.0.0 took 0.10.0, whose muster registration the
installed chart did not yet render. The breaking line is semver's: in 0.x every
minor (0.N), from 1.0.0 every major. So each range of values.yaml:

- has a ceiling (`<`, `<=`, an exact version or an x-range): no floor alone,
  no `x` / `*`;
- confines its newest admitted line: a 0.x range ends at a minor (`<0.N.0`,
  `0.N.x`, `~0.N`, `^0.N`), never at `<1.0.0` or `0.x`; a range from 1.0.0
  ends at a major or below;
- admits neither the first release of the line above its ceiling nor that
  release's candidates, as written and as gitops.prereleases widens it (every
  comparator without a pre-release gets `-0`), so an installed chart never picks
  up a component release newer than its cap, stable or candidate.

The ceiling moves in the release that renders values for the next line: that
release is what proves them (verify-components-charts renders every component
chart at the version its range resolves to).

Components released with this chart (releasedWithChart, no range) carry the
chart's own exact version and are skipped. Stdlib only: the CI image has no
PyYAML, so the roster is read from values.yaml's own layout — two-space entries
under `components:`, four-space keys.
"""

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fluxsemver  # noqa: E402
from fluxsemver import Version  # noqa: E402


def fail(msg: str) -> None:
    sys.exit(f"FAIL: {msg}")


def roster(values: str) -> dict[str, str]:
    """components.<name>.versionRange of every entry that names a chart."""
    block = re.search(r"^components:.*?\n(.*?)^\S", values, re.S | re.M)
    if not block:
        fail("values.yaml has no components: block")
    out: dict[str, str] = {}
    for entry in re.split(r"^(?=  [a-z0-9-]+:)", block.group(1), flags=re.M):
        name = re.match(r"  ([a-z0-9-]+):", entry)
        if not name or not re.search(r"^    chart: \S", entry, re.M):
            continue
        rng = re.search(r'^    versionRange: "([^"]*)"', entry, re.M)
        out[name.group(1)] = rng.group(1) if rng else ""
    if len(out) < 20:
        fail(f"read only {len(out)} components from values.yaml; the layout this check reads changed")
    return out


def widen(rng: str) -> str:
    """The range gitops.prereleases renders: `-0` on every comparator without
    a pre-release (agent-platform.prereleases.apply)."""
    terms = re.split(r"(\s+|\|\||,)", rng)
    return "".join(t if not re.search(r"\d", t) or "-" in t else f"{t}-0" for t in terms)


def first_above(rng: str) -> Version:
    """The first release above the range's ceiling: the ceiling itself for a
    `<` bound, the next line's first release for `<=`, an exact version or an
    x-range's top. ValueError when the range has no ceiling or leaves 0.x's
    minors open."""
    ceilings = []
    for alternative in rng.split("||"):
        terms = [t for t in re.split(r"[\s,]+", alternative.strip()) if t]
        comparators = [pair for t in terms for pair in fluxsemver._comparators(t)]
        caps = [(op, c) for op, c in comparators if op in ("<", "<=", "=", "==")]
        if not caps:
            raise ValueError(f"{rng!r} has no ceiling: every future release of the component would roll in by itself")
        ceilings.append(min(caps, key=lambda p: p[1].key()))
    op, cap = max(ceilings, key=lambda p: p[1].key())
    major, minor, _ = cap.core
    if op == "<":
        if cap.core == (1, 0, 0):
            raise ValueError(f"{rng!r} admits every 0.x minor: in 0.x a minor is a breaking release, so the ceiling is the next minor of the line the values were proven against (<0.N.0 or 0.N.x)")
        return Version(fluxsemver.base(cap.text))
    return Version(f"0.{minor + 1}.0" if major == 0 else f"{major + 1}.0.0")


def main() -> None:
    chart = sys.argv[1] if len(sys.argv) > 1 else "helm/agent-platform"
    with open(f"{chart}/values.yaml", encoding="utf-8") as f:
        components = roster(f.read())
    failures = []
    for name, rng in sorted(components.items()):
        if not rng:
            print(f"ok: {name}: released with the chart (no range)")
            continue
        try:
            above = first_above(rng)
        except ValueError as e:
            failures.append(f"components.{name}.versionRange {e}")
            continue
        probes = [above.text, f"{above.text}-rc.1", f"{above.text}-0"]
        admitted = [f"{p} ({shape})" for shape, constraint in (("as written", rng), ("with gitops.prereleases", widen(rng)))
                    for p in probes if fluxsemver.satisfies(p, constraint)]
        if admitted:
            failures.append(f"components.{name}.versionRange {rng!r} admits {', '.join(admitted)}, at or above its ceiling: a ceiling below a range whose floor is a release candidate carries -0 (<{above.text}-0), else the next line's candidates match")
            continue
        print(f"ok: {name}: {rng!r} stops below {above.text}, its candidates included")
    if failures:
        fail("a range admits a release its values were not proven against; cap it at the proven line and raise the cap in the release that renders values for the next one:\n  " + "\n  ".join(failures))
    print(f"all {len(components)} component ranges are capped at the line their values were proven against.")


if __name__ == "__main__":
    main()
