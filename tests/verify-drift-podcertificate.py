#!/usr/bin/env python3
"""Assert the drift-detection ignore rules for pod-certificate workloads
(giantswarm/agent-platform#879).

helm-controller before 1.4 (Flux 2.6) decodes a Deployment through API types
that predate the projected volume source podCertificate (Kubernetes 1.35): with
drift detection on, its correction re-applied kagent-controller with the
source emptied and the next ReplicaSet's pod crash-looped without its Substrate
identity. While a component release's drift detection is on (mode enabled or
warn), the meta chart appends an ignore rule for /spec/template/spec/volumes of
the release's pod-certificate workloads: Deployment kagent-controller in the
kagent release, every Deployment, StatefulSet and DaemonSet in the substrate
release. Asserted here:

- the default render: kagent carries mode enabled and exactly its one rule;
  muster's block is unchanged (no pod-certificate workload); substrate has none;
- an installation's own ignore list keeps its rules, the chart's rule is added
  once even when the list already carries it (the issue's bridge);
- renamed kagent controllers keep the rule, including the chart naming fallbacks;
- substrate with drift detection turned on (mode warn) gets its three rules;
- mode disabled renders the entry's block unchanged, no rule.

Stdlib only, like the other render checks (the CI image has no PyYAML): the
HelmReleases are the chart's own render, two-space indented, toYaml's sorted keys.

Usage: verify-drift-podcertificate.py <chart dir>   (HELM selects the binary)
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile

KAGENT_RULE = """\
    - paths:
      - /spec/template/spec/volumes
      target:
        kind: Deployment
        name: kagent-controller
"""

SUBSTRATE_RULES = "".join(f"""\
    - paths:
      - /spec/template/spec/volumes
      target:
        kind: {kind}
""" for kind in ("Deployment", "StatefulSet", "DaemonSet"))

BRIDGE_AND_OWN = """\
components:
  kagent:
    driftDetection:
      mode: enabled
      ignore:
        - paths: ["/spec/template/spec/volumes"]
          target: {kind: Deployment, name: kagent-controller}
        - paths: ["/spec/replicas"]
          target: {kind: Deployment, name: kagent-ui}
"""

SUBSTRATE_WARN = """\
components:
  substrate:
    driftDetection:
      mode: warn
"""

KAGENT_DISABLED = """\
components:
  kagent:
    driftDetection:
      mode: disabled
"""

failed = False


def fail(msg: str) -> None:
    global failed
    failed = True
    print(f"FAIL: {msg}")


def render(chart: str, overlay: str | None) -> dict[str, str | None]:
    """{HelmRelease name: its spec.driftDetection block (None without one)}."""
    cmd = [os.environ.get("HELM", "helm"), "template", "t", chart,
           "-f", os.path.join(chart, "ci", "ci-values.yaml"),
           "--set", "components.flux.enabled=false"]
    with tempfile.TemporaryDirectory() as tmp:
        if overlay:
            path = os.path.join(tmp, "overlay.yaml")
            with open(path, "w", encoding="utf-8") as f:
                f.write(overlay)
            cmd += ["-f", path]
        out = subprocess.run(cmd, capture_output=True, text=True)
    if out.returncode != 0:
        sys.exit(f"FAIL: helm template failed:\n{out.stderr}")
    releases = {}
    for doc in out.stdout.split("\n---\n"):
        if not re.search(r"^kind: HelmRelease$", doc, re.M):
            continue
        name = re.search(r"^  name: (\S+)$", doc, re.M).group(1)
        m = re.search(r"^  driftDetection:\n((?:    .*\n)+)", doc + "\n", re.M)
        releases[name] = m.group(1) if m else None
    return releases


def expect(case: str, releases: dict, name: str, block: str | None) -> None:
    got = releases.get(name)
    if got != block:
        fail(f"{case}: the {name} release's spec.driftDetection is\n{got}\nexpected\n{block}")


def main(chart: str) -> int:
    default = render(chart, None)
    expect("default", default, "kagent", "    ignore:\n" + KAGENT_RULE + "    mode: enabled\n")
    expect("default", default, "muster", "    mode: enabled\n")
    expect("default", default, "substrate", None)

    own = render(chart, BRIDGE_AND_OWN)
    expect("an installation's ignore list", own, "kagent", "    ignore:\n" + KAGENT_RULE + """\
    - paths:
      - /spec/replicas
      target:
        kind: Deployment
        name: kagent-ui
    mode: enabled
""")

    for naming, controller in (
        ("fullnameOverride: custom-kagent", "custom-kagent-controller"),
        ('fullnameOverride: ""\n  nameOverride: custom', "kagent-custom-controller"),
        ('fullnameOverride: ""', "kagent-controller"),
        ("fullnameOverride: " + "x" * 62 + "-extra", "x" * 62 + "-controller"),
    ):
        renamed = render(chart, "kagent:\n  " + naming + "\n")
        expect("kagent naming: " + naming, renamed, "kagent",
               "    ignore:\n" + KAGENT_RULE.replace("kagent-controller", controller) + "    mode: enabled\n")

    added = render(chart, BRIDGE_AND_OWN.replace(
        '        - paths: ["/spec/template/spec/volumes"]\n'
        '          target: {kind: Deployment, name: kagent-controller}\n', ""))
    expect("append beside an installation's rule", added, "kagent", """\
    ignore:
    - paths:
      - /spec/replicas
      target:
        kind: Deployment
        name: kagent-ui
""" + KAGENT_RULE + "    mode: enabled\n")

    warn = render(chart, SUBSTRATE_WARN)
    expect("substrate mode warn", warn, "substrate", "    ignore:\n" + SUBSTRATE_RULES + "    mode: warn\n")

    disabled = render(chart, KAGENT_DISABLED)
    expect("kagent mode disabled", disabled, "kagent", "    mode: disabled\n")

    if failed:
        return 1
    print("ok: drift detection leaves the pod-certificate workloads' volumes to Helm (kagent-controller by default; "
          "every substrate workload once on), kept beside an installation's own rules, none while disabled")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    sys.exit(main(sys.argv[1]))
