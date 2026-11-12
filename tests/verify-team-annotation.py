#!/usr/bin/env python3
"""Assert that every HelmRelease and OCIRepository the meta chart renders
carries the annotation application.giantswarm.io/team with the chart's team
(Chart.yaml annotation io.giantswarm.application.team).

kube-state-metrics reads the annotation off the Flux objects into the `team`
label of gotk_resource_info, which routes the alerts on them. The team-stamper
controller writes it only for the releases its apps-to-teams mapping lists, and
substrate and substrate-crds are not there: the chart stamps every object.

Two renders: the CI values with Substrate on and the bundled engine (the self
release's OCIRepository and HelmRelease render too), and the engine off (no
self release). Stdlib only, like the other render checks (the CI image has no
PyYAML): the objects are the chart's own render, two-space indented.

Usage: verify-team-annotation.py <meta chart dir>
"""

import os
import re
import subprocess
import sys

HELM = os.environ.get("HELM", "helm")
KEY = "application.giantswarm.io/team"


def chart_team(chart_dir: str) -> str:
    with open(os.path.join(chart_dir, "Chart.yaml")) as f:
        m = re.search(r"^  io\.giantswarm\.application\.team: ['\"]?([^'\"\n]+)['\"]?$", f.read(), re.M)
    if not m:
        sys.exit(f"FAIL: {chart_dir}/Chart.yaml has no io.giantswarm.application.team annotation")
    return m.group(1)


def render(chart_dir: str, flags: list[str]) -> str:
    r = subprocess.run([HELM, "template", "t", chart_dir, "-n", "agent-platform", "-f", f"{chart_dir}/ci/ci-values.yaml", *flags],
                       capture_output=True, text=True, check=False)
    if r.returncode != 0:
        sys.exit(f"FAIL: helm template {chart_dir} {' '.join(flags)}: {r.stderr}")
    return r.stdout


def flux_objects(manifest: str) -> dict:
    """{(kind, name): annotation value or None} of every HelmRelease and OCIRepository."""
    out = {}
    for doc in manifest.split("\n---\n"):
        kind = re.search(r"^kind: (HelmRelease|OCIRepository)$", doc, re.M)
        if not kind:
            continue
        name = re.search(r"^  name: (\S+)$", doc, re.M).group(1)
        annotations = re.search(r"^  annotations:\n((?:    .*\n?)*)", doc, re.M)
        value = None
        if annotations:
            m = re.search(r"^    " + re.escape(KEY) + r": ['\"]?([^'\"\n]*)['\"]?$", annotations.group(1), re.M)
            value = m.group(1) if m else None
        out[(kind.group(1), name)] = value
    return out


def check(objects: dict, team: str, what: str, must: list) -> None:
    missing = [f"{k} {n}" for k, n in must if (k, n) not in objects]
    if missing:
        sys.exit(f"FAIL: {what}: not rendered: {missing}")
    bad = [f"{k} {n}: {v!r}" for (k, n), v in sorted(objects.items()) if v != team]
    if bad:
        sys.exit(f"FAIL: {what}: {KEY} is not {team!r} on: {bad}")
    print(f"ok: {what}: {len(objects)} HelmReleases and OCIRepositories carry {KEY}={team}")


def main(chart_dir: str) -> int:
    team = chart_team(chart_dir)
    substrate = ["--set", "components.substrate.enabled=true", "--set", "components.substrate-crds.enabled=true"]
    pairs = [(k, n) for n in ("substrate", "substrate-crds") for k in ("HelmRelease", "OCIRepository")]

    engine_on = flux_objects(render(chart_dir, substrate + ["--set", "components.flux.enabled=true", "--set", "gitops.self.enabled=true"]))
    # The self release is named after the Helm release: `t` here.
    check(engine_on, team, "engine on, Substrate on", pairs + [("HelmRelease", "t"), ("OCIRepository", "t")])

    engine_off = flux_objects(render(chart_dir, substrate + ["--set", "components.flux.enabled=false"]))
    check(engine_off, team, "engine off, Substrate on", pairs)
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(f"usage: {sys.argv[0]} <meta chart dir>")
    sys.exit(main(sys.argv[1]))
