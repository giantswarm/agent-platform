#!/usr/bin/env python3
"""Assert kagent's oauth2-proxy runs the gsoci copy of its subchart's default
image (giantswarm/agent-platform#880).

The fleet's restrict-image-registries admits gsoci.azurecr.io only, and the
oauth2-proxy subchart the kagent chart bundles defaults to
quay.io/oauth2-proxy/oauth2-proxy at its appVersion. The meta chart pins
kagent.oauth2-proxy.image to the gsoci copy of that tag (the same digest, the
registry's mirror). The pin holds the tag of the subchart the kagent release at
the range's floor bundles, so a kagent re-pin that bumps the subchart moves it.

The check renders the meta chart, reads the kagent HelmRelease's forwarded
oauth2-proxy.image, pulls the kagent chart at the range's floor, reads the
bundled subchart's appVersion and default repository, and requires the pin's
registry to be gsoci.azurecr.io, its repository giantswarm/<the default's
image name>, its tag v<appVersion>, and the copy published.

Usage: verify-kagent-oauth2-proxy-image.py <meta chart dir>
Network: gsoci.azurecr.io. Needs PyYAML.
"""
import glob
import importlib.util
import io
import os
import sys
import tarfile
import tempfile

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))


def load(name: str):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), os.path.join(HERE, f"{name}.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


cc = load("verify-components-charts")
worker = load("verify-worker-image")
images = load("verify-substrate-images")

REGISTRY = "gsoci.azurecr.io"
KAGENT_ON = ["--set", "components.kagent.enabled=true", "--set", "ingress.parentRefs[0].name=x"]
SUBCHART = "oauth2-proxy"


def subchart_files(chart_dir: str) -> tuple[dict, dict]:
    """The bundled oauth2-proxy subchart's Chart.yaml and values.yaml, from the
    archive helm package vendored or the directory a checkout carries."""
    for archive in glob.glob(os.path.join(chart_dir, "charts", f"{SUBCHART}-*.tgz")):
        with tarfile.open(archive) as tar:
            chart = yaml.safe_load(io.TextIOWrapper(tar.extractfile(f"{SUBCHART}/Chart.yaml"), encoding="utf-8"))
            values = yaml.safe_load(io.TextIOWrapper(tar.extractfile(f"{SUBCHART}/values.yaml"), encoding="utf-8"))
        return chart, values
    directory = os.path.join(chart_dir, "charts", SUBCHART)
    if os.path.isdir(directory):
        with open(os.path.join(directory, "Chart.yaml"), encoding="utf-8") as f:
            chart = yaml.safe_load(f)
        with open(os.path.join(directory, "values.yaml"), encoding="utf-8") as f:
            values = yaml.safe_load(f)
        return chart, values
    cc.fail(f"the kagent chart bundles no {SUBCHART} subchart under charts/")


def main(meta: str) -> int:
    with open(os.path.join(meta, "values.yaml"), encoding="utf-8") as f:
        own = ((yaml.safe_load(f).get("kagent") or {}).get(SUBCHART) or {}).get("image") or {}
    if not own:
        cc.fail(f"values.yaml carries no kagent.{SUBCHART}.image pin")

    rendered = cc.docs(cc.render_meta(meta, [*cc.QUICKSTART, *KAGENT_ON]))
    if ("HelmRelease", "kagent") not in rendered:
        cc.fail("the meta chart renders no kagent HelmRelease with components.kagent on")
    forwarded = (yaml.safe_load(cc.hr_values(rendered[("HelmRelease", "kagent")])).get(SUBCHART) or {}).get("image") or {}
    if forwarded != own:
        cc.fail(f"the kagent HelmRelease does not forward values.yaml's kagent.{SUBCHART}.image verbatim")

    url, rng = cc.source(rendered[("OCIRepository", "kagent")])
    floor = worker.floor_of(rng)
    with tempfile.TemporaryDirectory() as tmp:
        got = cc.pull(url, floor, tmp)
        if got != floor:
            cc.fail(f"helm pull {url} --version {floor} unpacked {got}")
        chart, values = subchart_files(os.path.join(tmp, url.rsplit("/", 1)[1]))
    app_version = str(chart.get("appVersion") or "")
    default = (values.get("image") or {}).get("repository") or ""
    if not app_version or not default:
        cc.fail(f"the {SUBCHART} subchart {chart.get('version')} of kagent {floor} names no appVersion or image.repository")
    want = {"registry": REGISTRY, "repository": f"giantswarm/{default.rsplit('/', 1)[-1]}", "tag": f"v{app_version}"}
    if own != want:
        cc.fail(f"kagent.{SUBCHART}.image is {own}, want {want}: the gsoci copy of the subchart's default "
                f"({default}:v{app_version}, {SUBCHART} chart {chart.get('version')} bundled by kagent {floor}) — move the pin with the floor")
    reference = f"{own['registry']}/{own['repository']}:{own['tag']}"
    if not images.published(reference):
        cc.fail(f"{reference} is not published: retagger mirrors quay.io/{default} by semver (images/skopeo-quay-io.yaml in giantswarm/retagger), check its range and the mirror's run")
    print(f"ok: kagent {floor} bundles {SUBCHART} chart {chart.get('version')} (appVersion {app_version}); the pin {reference} is its gsoci copy and is published")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    sys.exit(main(sys.argv[1]))
