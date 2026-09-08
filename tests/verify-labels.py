#!/usr/bin/env python3
"""Assert that every label value the two charts render stays a valid Kubernetes
label at the chart versions they are installed under.

The helm.sh/chart label is `<name>-<version>` cut to 63 characters. Two things
make that cut dangerous: helm-controller installs every chart with the OCI
digest appended as build metadata (`3.20.0+8c89e1be4cbf`; `+` is replaced by
`_`), and a branch build carries a long prerelease version (abs stamps
`3.19.1-dev.<branch>.<date>.<time>.h<sha>`). A cut that lands on `_`, `-` or
`.` is rejected by the apiserver for EVERY object of the release ("must start
and end with an alphanumeric character") — measured once on the self-management
adoption of a branch build, which then also poisoned `helm uninstall` (the
hooks of the failed revision carried the label). The helpers trim those
characters after the cut; this script packages each chart at versions whose cut
lands on each of them and validates every label of the render.

Stdlib only (the CI image has no PyYAML); HELM selects the binary.
"""

import os
import re
import subprocess
import sys
import tempfile

HELM = os.environ.get("HELM", "helm")
LABEL = re.compile(r"^(([A-Za-z0-9][-A-Za-z0-9_.]*)?[A-Za-z0-9])?$")
DIGEST = "+8c89e1be4cbf"


def fail(msg: str) -> None:
    sys.exit(f"FAIL: {msg}")


def versions_cutting_on(name: str, separators: str) -> list[str]:
    """Prerelease versions (the abs shape) whose label `<name>-<version>_<digest>`
    has each separator exactly at the 63rd character, plus a short release."""
    base = "3.19.1-dev.a.2026-09-08."
    pad = 62 - (len(name) + 1) - len(base)
    assert pad >= 0, (name, base)
    out = []
    for sep in separators:
        if sep == "_":  # the `_` of the digest is the 63rd character
            out.append(base + "x" * pad)
        else:  # the separator inside the prerelease is the 63rd character
            out.append(base + "x" * pad + sep + "h1234567")
    out.append("3.20.0")
    return out


def render(chart_dir: str, version: str, flags: list[str], tmp: str) -> str:
    r = subprocess.run([HELM, "package", chart_dir, "--version", version + DIGEST, "--app-version", version, "-d", tmp],
                       capture_output=True, text=True, check=False)
    if r.returncode != 0:
        fail(f"helm package {chart_dir} --version {version}{DIGEST}: {r.stderr}")
    archive = re.search(r"saved it to: (\S+)", r.stdout).group(1)
    r = subprocess.run([HELM, "template", "t", archive, "-n", "agent-platform", "--include-crds", *flags], capture_output=True, text=True, check=False)
    if r.returncode != 0:
        fail(f"helm template {archive}: {r.stderr}")
    return r.stdout


def check_labels(manifest: str, what: str) -> int:
    bad = []
    count = 0
    for m in re.finditer(r"""^\s+(helm\.sh/chart|app\.kubernetes\.io/version|app\.kubernetes\.io/name|app\.kubernetes\.io/instance): ["']?([^"'\n]*)["']?$""", manifest, re.M):
        count += 1
        value = m.group(2)
        if len(value) > 63 or not LABEL.match(value):
            bad.append(f"{m.group(1)}={value!r}")
    if not count:
        fail(f"{what}: no label rendered at all")
    if bad:
        fail(f"{what}: invalid label values: {sorted(set(bad))}")
    return count


def main(meta: str, connectivity: str) -> int:
    with tempfile.TemporaryDirectory() as tmp:
        for chart_dir, name, flags in (
            (meta, "agent-platform", ["-f", f"{meta}/ci/ci-values.yaml"]),
            (connectivity, "agent-platform-connectivity", ["--set", "ingress.parentRefs[0].name=x", "--set", "components.kagent.enabled=true"]),
        ):
            for version in versions_cutting_on(name, "_-."):
                manifest = render(chart_dir, version, flags, tmp)
                n = check_labels(manifest, f"{name} {version}{DIGEST}")
                chart_labels = set(re.findall(r"""^\s+helm\.sh/chart: ["']?([^"'\n]+?)["']?$""", manifest, re.M))
                print(f"ok: {name} at {version}{DIGEST}: {n} label values valid; helm.sh/chart = {sorted(chart_labels)}")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(f"usage: {sys.argv[0]} <meta chart dir> <connectivity chart dir>")
    sys.exit(main(sys.argv[1], sys.argv[2]))
