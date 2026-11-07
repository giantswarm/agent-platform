#!/usr/bin/env python3
"""The component version floors, read from the one place they are declared:
components.<name>.versionRange in the meta chart's values.yaml. Stdlib-only.

A floor is the `>=` bound of a range. A test that needs a component's range or
floor reads it here instead of naming the version, so a floor move is one edit
of values.yaml plus regenerated files (`make golden-update`: the goldens and
the customer BOM's pins). A range without a `>=` bound (an x-range such as
0.x) has no floor: the BOM pins such a component by hand.

The customer BOM (examples/customer-bom.yaml) pins every floored component at
its floor: `bom` FAILS on a pin that differs, `bom --write` rewrites the pins.

Usage (CHART defaults to helm/agent-platform):
  floors.py [--chart CHART] range <name>                print the range
  floors.py [--chart CHART] floor <name> [--min VERSION] print the floor; FAIL below VERSION
  floors.py [--chart CHART] bom [--write]                the BOM pins every floor
"""

import argparse
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fluxsemver  # noqa: E402

CHART = "helm/agent-platform"
BOM = "examples/customer-bom.yaml"
_FLOOR = re.compile(r"^\s*>=\s*v?(\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?)(?:\s|,|$)")


def fail(msg: str) -> None:
    print(f"FAIL: {msg}", file=sys.stderr)
    sys.exit(1)


_TOP = re.compile(r"^(\S[^:]*):")
_ENTRY = re.compile(r"^  ([A-Za-z0-9_.-]+):\s*(?:#.*)?$")
_RANGE = re.compile(r"""^    versionRange:\s*(?:"([^"]*)"|'([^']*)'|([^\s#]+))""")


def ranges(chart: str = CHART) -> dict[str, str]:
    """name -> components.<name>.versionRange, for every component that has one.
    Read line by line (values.yaml's block layout: entries at two spaces, their
    keys at four), so the scripts that stay stdlib-only can use it."""
    out: dict[str, str] = {}
    inside, name = False, None
    with open(f"{chart}/values.yaml", encoding="utf-8") as f:
        for line in f:
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            if m := _TOP.match(line):
                inside, name = m.group(1) == "components", None
            elif inside and (m := _ENTRY.match(line)):
                name = m.group(1)
            elif inside and name and (m := _RANGE.match(line)):
                rng = next(g for g in m.groups() if g is not None)
                if rng:
                    out[name] = rng
    if not out:
        fail(f"{chart}/values.yaml declares no components.<name>.versionRange")
    return out


def range_of(name: str, chart: str = CHART) -> str:
    rng = ranges(chart).get(name)
    if not rng:
        fail(f"components.{name}.versionRange is not set in {chart}/values.yaml")
    return rng


def floor_of_range(rng: str) -> str | None:
    """The `>=` bound of a single AND group, or None (an x-range, an exact pin, an OR)."""
    if "||" in rng:
        return None
    m = _FLOOR.match(rng)
    return m.group(1) if m else None


def floor(name: str, chart: str = CHART) -> str:
    rng = range_of(name, chart)
    f = floor_of_range(rng)
    if f is None:
        fail(f"components.{name}.versionRange {rng!r} has no >= floor")
    return f


def floors(chart: str = CHART) -> dict[str, str]:
    """name -> floor, for every component whose range has one."""
    return {n: f for n, r in ranges(chart).items() if (f := floor_of_range(r))}


def _pin_line(name: str) -> re.Pattern:
    return re.compile(rf'^(\s*{re.escape(name)}:\s*\{{\s*versionRange:\s*")([^"]+)("\s*\}})', re.M)


def bom(chart: str = CHART, write: bool = False) -> None:
    path = f"{chart}/{BOM}"
    with open(path, encoding="utf-8") as f:
        text = f.read()
    stale = []
    for name, want in floors(chart).items():
        pin = _pin_line(name)
        m = pin.search(text)
        if not m:
            fail(f"{BOM} does not pin components.{name} in the form `{name}: {{ versionRange: \"<version>\" }}`")
        if m.group(2) != want:
            stale.append(f"{name} {m.group(2)} (floor {want})")
            text = pin.sub(lambda m, want=want: m.group(1) + want + m.group(3), text, count=1)
    if write:
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        print(f"{BOM}: {'rewrote ' + ', '.join(stale) if stale else 'every pin is its floor'}")
        return
    if stale:
        fail(f"{BOM} pins a component off the floor of its components.<name>.versionRange: {', '.join(stale)}. Run `make golden-update`")
    print(f"ok: {BOM} pins every floored component at the floor of its range ({len(floors(chart))} components)")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--chart", default=CHART)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("range").add_argument("name")
    fp = sub.add_parser("floor")
    fp.add_argument("name")
    fp.add_argument("--min")
    sub.add_parser("bom").add_argument("--write", action="store_true")
    a = p.parse_args()
    if a.cmd == "range":
        print(range_of(a.name, a.chart))
    elif a.cmd == "floor":
        f = floor(a.name, a.chart)
        if a.min and fluxsemver.Version(f) < fluxsemver.Version(a.min):
            fail(f"the floor of components.{a.name}.versionRange is {f}, below {a.min}")
        print(f)
    else:
        bom(a.chart, a.write)


if __name__ == "__main__":
    main()
