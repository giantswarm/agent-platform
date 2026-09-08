#!/usr/bin/env python3
"""Render every chart the BOM pins against the values the meta chart forwards.

`make verify-meta` renders the meta chart only: it proves the HelmReleases and
their values are shaped as intended, never that the CHART on the other end
accepts them. A component chart with `additionalProperties: false` rejects a
key the meta chart forwards unconditionally, and the release then fails to
install or upgrade on every installation — which is invisible to a meta-only
render. That happened once already: `model-manager.lmstudio.*` was forwarded
to every release while the BOM still pinned a model-manager that had no such
key (agent-platform#272).

So for each HelmRelease the meta render produces, this resolves the chart's
pinned version from the BOM, pulls it, and renders it with exactly the values
the release carries. `tests/verify-toolset-presets.py` does the same for
muster's toolset presets; this is the BOM-wide form of that check.
"""

from __future__ import annotations

import json
import pathlib
import subprocess
import sys
import tempfile

import yaml

REGISTRY = "oci://gsoci.azurecr.io/charts/giantswarm"


def fail(msg: str) -> None:
    print(f"FAIL: {msg}")
    sys.exit(1)


def run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True)


def is_schema_error(err: str) -> bool:
    """Helm validates values against the chart's schema before it renders.

    That is the failure this check exists for: a key the meta chart forwards
    unconditionally which the chart's schema does not allow. A failure later,
    while the templates run, means the values were accepted and the chart
    wanted an input the BOM alone does not carry — a different thing.
    """
    return ("specifications of the schema" in err
            or "additional propert" in err)


def first_line(err: str) -> str:
    return err.splitlines()[0] if err.splitlines() else err


def releases(render: str) -> dict[str, dict]:
    """The HelmReleases that carry values, by release name."""
    out: dict[str, dict] = {}
    for doc in yaml.safe_load_all(render):
        if isinstance(doc, dict) and doc.get("kind") == "HelmRelease" and doc["spec"].get("values"):
            out[doc["metadata"]["name"]] = doc["spec"]["values"]
    return out


def pins(bom: pathlib.Path) -> dict[str, str]:
    """The BOM's component pins, only the exact ones (a range is not a version)."""
    doc = yaml.safe_load(bom.read_text())
    out = {}
    for name, spec in (doc.get("components") or {}).items():
        v = (spec or {}).get("versionRange", "")
        # Exact pins only: "0.19.0" yes, "0.x" or ">=1 <2" no.
        if v and all(c.isdigit() or c == "." for c in v):
            out[name] = v
    return out


def main() -> None:
    if len(sys.argv) != 3:
        fail("usage: verify-bom-charts.py <meta-render> <bom.yaml>")
    render = pathlib.Path(sys.argv[1]).read_text()
    bom = pathlib.Path(sys.argv[2])

    values_by_release = releases(render)
    pinned = pins(bom)
    if not values_by_release:
        fail("the render carries no HelmRelease values — wrong input file?")
    if not pinned:
        fail(f"{bom} pins no exact component versions")

    checked, skipped, needs = [], [], []
    with tempfile.TemporaryDirectory() as tmp:
        for name, version in sorted(pinned.items()):
            values = values_by_release.get(name)
            if values is None:
                skipped.append(f"{name} (no forwarded values)")
                continue
            pulled = run("helm", "pull", f"{REGISTRY}/{name}", "--version", version,
                         "--untar", "--untardir", f"{tmp}/{name}")
            if pulled.returncode != 0:
                fail(f"{name} {version}: cannot pull the pinned chart\n{pulled.stderr.strip()}")
            vf = pathlib.Path(tmp) / f"{name}-values.yaml"
            vf.write_text(yaml.safe_dump(values))
            rendered = run("helm", "template", name, f"{tmp}/{name}/{name}",
                           "--namespace", "agent-platform", "-f", str(vf))
            if rendered.returncode != 0:
                err = rendered.stderr.strip()
                if is_schema_error(err):
                    fail(f"{name} {version} REJECTS the values the meta chart forwards to it "
                         f"— every installation on this BOM would get a release that cannot "
                         f"install or upgrade, whatever the operator sets:\n{err}")
                # Anything else is the chart asking for an input this synthetic
                # render does not supply (a credential the operator provides,
                # say). Not our class of break: the values were accepted, the
                # template merely wanted more. Report it, do not fail on it.
                needs.append(f"{name} {version}: {first_line(err)}")
                continue
            checked.append(f"{name} {version}")

    for s in skipped:
        print(f"  skip: {s}")
    for n in needs:
        print(f"  needs-input: {n}")
    for c in checked:
        print(f"  ok:   {c} accepts its forwarded values")
    print(f"ok: {len(checked) + len(needs)} pinned charts accept what the meta chart forwards "
          f"({len(needs)} could not be rendered further without operator input)")


if __name__ == "__main__":
    main()
