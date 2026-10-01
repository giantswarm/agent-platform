#!/usr/bin/env python3
"""Assert that the flux-engine subchart's crds/ are the pinned releases' CRDs,
byte for byte, or write them (`--write`, the `make sync-flux-crds` target).

- crds/flux-operator.yaml: the four CRDs of the upstream chart
  controlplaneio-fluxcd/flux-operator at the subchart's appVersion (the operator
  image tag; Renovate moves both), `templates/crds.yaml` rendered with
  installCRDs=true, the release-specific labels app.kubernetes.io/instance,
  app.kubernetes.io/managed-by and helm.sh/chart removed;
- crds/flux.yaml: the seven CRDs of `flux install --export
  --components=source-controller,helm-controller --version=<v>`, the
  app.kubernetes.io/instance label removed. <v> is the version the file's
  header names (`--flux-version` moves it); the flux CLI of that release is
  downloaded into .bin/ and checked against the release's checksums.

Each file is its header (generated here, naming the release) and the upstream
documents, so a stale CRD, a stale label and a stale header comment all fail,
naming the file and the release, with the diff and the command that refreshes
them. Both renders need the network (ghcr.io, github.com).

Deliberately stdlib-only, like the other verify scripts. HELM selects the
binary.
"""

import argparse
import difflib
import hashlib
import io
import os
import platform
import re
import subprocess
import sys
import tarfile
import time
import urllib.request
from pathlib import Path

HELM = os.environ.get("HELM", "helm")
ROOT = Path(__file__).resolve().parent.parent
OPERATOR_CHART = "oci://ghcr.io/controlplaneio-fluxcd/charts/flux-operator"
FLUX_RELEASES = "https://github.com/fluxcd/flux2/releases/download"
FLUX_COMPONENTS = "source-controller,helm-controller"
OPERATOR_DROPPED = re.compile(r"(?m)^    (?:app\.kubernetes\.io/instance|app\.kubernetes\.io/managed-by|helm\.sh/chart): .*\n")
FLUX_DROPPED = re.compile(r"(?m)^    app\.kubernetes\.io/instance: .*\n")
APP_VERSION = re.compile(r'(?m)^appVersion: "(v\d+\.\d+\.\d+)"$')
FLUX_PIN = re.compile(r"--version=(v\d+\.\d+\.\d+)`")
DIFF_LINES = 40

OPERATOR_HEADER = """\
# The four CustomResourceDefinitions of the Flux Operator, rendered from the
# upstream chart controlplaneio-fluxcd/flux-operator {version} (templates/crds.yaml
# with installCRDs=true, the labels app.kubernetes.io/instance,
# app.kubernetes.io/managed-by and helm.sh/chart removed). They live in
# crds/ so Helm installs them before the FluxInstance template is validated.
# Helm never upgrades or deletes crds/, and the operator does not manage its own
# CRDs, so the meta chart's pre-install/pre-upgrade hook server-side applies this
# file on every upgrade (templates/hooks/flux-operator-crds.yaml). They stay
# behind on uninstall (the Flux CRDs do not - the operator removes those with the
# FluxInstance).
#
# Generated, do not edit: `make sync-flux-crds` writes this file for Chart.yaml's
# appVersion (Renovate moves it with operator.image.tag in values.yaml), and
# `make verify-flux-crds` fails on any other content.
"""

FLUX_HEADER = """\
# The seven CustomResourceDefinitions of Flux's source-controller and
# helm-controller, from `flux install --export
# --components=source-controller,helm-controller --version={version}` (the
# label app.kubernetes.io/instance removed). They live in
# crds/ so Helm installs them before the meta chart's HelmRelease templates are
# validated against the API server (Helm validates template kinds before any
# hook runs, so a hook cannot bring them). The Flux Operator adopts them as soon
# as the FluxInstance reconciles and upgrades them with Flux inside
# flux-engine.instance.distribution.version; it also removes them when the
# FluxInstance is deleted (the ordered teardown of the meta chart).
#
# Generated, do not edit: `make sync-flux-crds FLUX_VERSION=<v>` writes this
# file (without FLUX_VERSION for the version named above), and
# `make verify-flux-crds` fails on any other content.
"""


def fail(msg: str) -> None:
    sys.exit(f"FAIL: {msg}")


def run(cmd: list[str]) -> str:
    """One upstream render, bounded and retried: a registry hiccup is not drift."""
    for attempt in range(1, 4):
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=180, check=False)
        except subprocess.TimeoutExpired:
            err = "timed out after 180s"
        else:
            if r.returncode == 0:
                return r.stdout
            err = r.stderr.strip()
        print(f"{cmd[0]} attempt {attempt} failed: {err}", file=sys.stderr)
        time.sleep(5 * attempt)
    fail(f"could not render {' '.join(cmd)}")


def fetch(url: str) -> bytes:
    for attempt in range(1, 4):
        try:
            with urllib.request.urlopen(url, timeout=60) as r:
                return r.read()
        except OSError as e:
            print(f"GET {url} attempt {attempt} failed: {e}", file=sys.stderr)
            time.sleep(5 * attempt)
    fail(f"could not download {url}")


def flux_cli(version: str) -> Path:
    """The flux CLI of the pinned release in .bin/, verified against its checksums file."""
    path = ROOT / ".bin" / f"flux-{version}" / "flux"
    if path.is_file():
        return path
    arch = {"x86_64": "amd64", "amd64": "amd64", "aarch64": "arm64", "arm64": "arm64"}[platform.machine().lower()]
    tarball = f"flux_{version[1:]}_{platform.system().lower()}_{arch}.tar.gz"
    data = fetch(f"{FLUX_RELEASES}/{version}/{tarball}")
    sums = fetch(f"{FLUX_RELEASES}/{version}/flux_{version[1:]}_checksums.txt").decode()
    if f"{hashlib.sha256(data).hexdigest()}  {tarball}" not in sums.splitlines():
        fail(f"{tarball} does not match the checksums of Flux {version}")
    path.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(data)) as tar:
        path.write_bytes(tar.extractfile("flux").read())
    path.chmod(0o755)
    return path


def crd_documents(stream: str, dropped: re.Pattern) -> str:
    """The CRD documents of a multi-document stream, `---`-separated, without
    Helm's `# Source:` comments, surrounding blank lines and the dropped labels."""
    docs = []
    for doc in re.split(r"(?m)^---\n", stream):
        doc = re.sub(r"(?m)^# Source: .*\n", "", doc).strip("\n")
        if re.search(r"(?m)^kind: CustomResourceDefinition$", doc):
            docs.append("---\n" + dropped.sub("", doc + "\n"))
    return "".join(docs)


def expected(chart_dir: Path, flux_version: str | None) -> dict[Path, tuple[str, str]]:
    """Each CRD file of the subchart mapped to (its release, its expected content)."""
    m = APP_VERSION.search((chart_dir / "Chart.yaml").read_text())
    if not m:
        fail(f"{chart_dir / 'Chart.yaml'} names no appVersion vX.Y.Z")
    operator = m.group(1)
    flux_file = chart_dir / "crds" / "flux.yaml"
    if flux_version is None:
        m = FLUX_PIN.search(flux_file.read_text())
        if not m:
            fail(f"{flux_file}'s header names no `--version=vX.Y.Z`; pass FLUX_VERSION")
        flux_version = m.group(1)
    rendered = run([HELM, "template", "flux-operator", OPERATOR_CHART, "--version", operator[1:],
                    "--set", "installCRDs=true", "--show-only", "templates/crds.yaml"])
    exported = run([str(flux_cli(flux_version)), "install", "--export", f"--components={FLUX_COMPONENTS}",
                    f"--version={flux_version}"])
    return {
        chart_dir / "crds" / "flux-operator.yaml": (f"the Flux Operator {operator} release",
                                                    OPERATOR_HEADER.format(version=operator[1:]) + crd_documents(rendered, OPERATOR_DROPPED)),
        flux_file: (f"Flux {flux_version}", FLUX_HEADER.format(version=flux_version) + crd_documents(exported, FLUX_DROPPED)),
    }


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("chart_dir", type=Path, help="the flux-engine subchart")
    p.add_argument("--write", action="store_true", help="write the files instead of checking them")
    p.add_argument("--flux-version", help="the Flux release of crds/flux.yaml (default: the one its header names)")
    args = p.parse_args()

    drifted = []
    for path, (release, want) in expected(args.chart_dir, args.flux_version).items():
        have = path.read_text()
        if args.write:
            if have != want:
                path.write_text(want)
                print(f"wrote {path} from {release}")
            else:
                print(f"ok: {path} already is {release}")
            continue
        if have == want:
            print(f"ok: {path} is {release}")
            continue
        diff = list(difflib.unified_diff(have.splitlines(), want.splitlines(), f"{path} (committed)", f"{path} ({release})", lineterm=""))
        print(f"FAIL: {path} is not the CRDs of {release}")
        print("\n".join(diff[:DIFF_LINES]))
        if len(diff) > DIFF_LINES:
            print(f"... {len(diff) - DIFF_LINES} more diff lines")
        drifted.append(path)
    if drifted:
        fail(f"{len(drifted)} CRD file(s) drifted from the pinned releases; refresh them with `make sync-flux-crds` "
             "(and `make golden-update`: the meta chart's CRD hook carries crds/flux-operator.yaml)")


if __name__ == "__main__":
    main()
