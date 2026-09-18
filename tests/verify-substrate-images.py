#!/usr/bin/env python3
"""Assert the meta chart's `substrate.images` pins are the pinned Substrate
release's own third-party image defaults, on gsoci (giantswarm/agent-platform#575,
#580).

The substrate chart runs images that are not the line's own — the bundled
control-plane database, the bundled snapshot store and its bucket-init Job, the
agentgateway build of the atenet router and egress — from an `images:` map of
full references, Docker Hub short names and the agentgateway line's ghcr build
by default. The meta chart forwards its `substrate:` block verbatim, so the
fleet gets those defaults unless the block names the gsoci copies: values.yaml's
`substrate.images`. A pin is a copy of a value that lives in another chart, and
the two drift apart in two ways this check refuses:

  * a Substrate re-pin (`components.substrate.versionRange`) whose release
    moved a default — a newer postgres, another agentgateway build the egress
    config is written for — while the meta chart still forwards the old one:
    every release the range admits is pulled and, key by key, the pin must
    carry the release default's digest (or, without a digest, its tag) under
    `gsoci.azurecr.io`; a pin whose key the release's `images:` does not know
    fails too (it would forward nothing);
  * a release that runs a third-party image the block does not pin: the release
    is rendered with the values the meta chart forwards to it — the bundled
    database and store on — and every container image that is not one of the
    line's own (`image.registry`) must be a gsoci reference.

`images.awsCli` is the exception both ways (TOLERATED): the rustfs-bucket-init
Job is a release resource and a Job's pod template is immutable, so the meta
chart forwards the chart's own value unchanged and the check requires exactly
that until the line ships a Job that can be recreated (values.yaml says why;
tests/verify-images.py tolerates the rendered value by name).

Network: ghcr.io (the substrate charts the range admits). Needs PyYAML.
Usage: verify-substrate-images.py <meta chart dir>
"""
import importlib.util
import os
import re
import sys
import tempfile

import yaml

import fluxsemver

HERE = os.path.dirname(os.path.abspath(__file__))


def load(name: str):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), os.path.join(HERE, f"{name}.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


cc = load("verify-components-charts")
worker = load("verify-worker-image")

REGISTRY = "gsoci.azurecr.io"
KAGENT_ON = ["--set", "components.kagent.enabled=true", "--set", "ingress.parentRefs[0].name=x"]
# The bundled shapes: the database StatefulSet, the store Deployment and its Job.
BUNDLED = ["--set", "rustfs.enabled=true", "--set", "postgres.enabled=true"]
# Keys forwarded at the chart's own value on purpose, with the reason.
TOLERATED = {
    "awsCli": "the rustfs-bucket-init Job's pod template is immutable; its image moves with a release whose Job can be recreated",
}
CONTAINER_LISTS = ("containers", "initContainers", "ephemeralContainers")


def host_of(reference: str) -> str | None:
    first = reference.split("/", 1)[0]
    if "/" in reference and ("." in first or ":" in first or first == "localhost"):
        return first
    return None


def digest_of(reference: str) -> str | None:
    m = re.search(r"@(sha256:[a-f0-9]{64})$", reference)
    return m.group(1) if m else None


def tag_of(reference: str) -> str | None:
    bare = re.sub(r"@sha256:[a-f0-9]{64}$", "", reference)
    last = bare.rsplit("/", 1)[-1]
    return last.split(":", 1)[1] if ":" in last else None


def admitted_releases(url: str, rng: str) -> list[str]:
    """Every release tag of the substrate chart the range admits, newest last."""
    tags = [t for t in cc.registry_tags(url) if fluxsemver.satisfies(t, rng)]
    if not tags:
        cc.fail(f"{url} publishes no version in {rng!r}")
    return sorted(tags, key=lambda t: fluxsemver.parse(t))


def check_pins(version: str, defaults: dict, pins: dict) -> None:
    for key, pin in pins.items():
        default = defaults.get(key)
        if not isinstance(default, str):
            cc.fail(f"substrate.images.{key} pins {pin!r}, but the substrate chart {version} has no images.{key}: the value would forward to nothing — the key moved or went with the release")
        if key in TOLERATED:
            if pin != default:
                cc.fail(f"substrate.images.{key} ({pin!r}) differs from the substrate chart {version}'s own default ({default!r}); the meta chart forwards this one unchanged on purpose — {TOLERATED[key]}")
            continue
        if host_of(pin) != REGISTRY:
            cc.fail(f"substrate.images.{key} ({pin!r}) is not a {REGISTRY} reference")
        if digest_of(default):
            if digest_of(pin) != digest_of(default):
                cc.fail(f"substrate.images.{key} ({pin!r}) does not carry the digest of the substrate chart {version}'s default {default!r}: the gsoci copy must be the same bits — re-pin it from the release's `helm show values`")
        elif tag_of(pin) != tag_of(default):
            cc.fail(f"substrate.images.{key} ({pin!r}) is not tagged like the substrate chart {version}'s default {default!r}: the pin moves with the Substrate release (the egress config is written for the agentgateway build the release names)")
        print(f"ok: {version} images.{key}: {pin} = the release's {default}")


def containers(manifest: str):
    for doc in yaml.safe_load_all(manifest):
        if not isinstance(doc, dict):
            continue
        kind, name = doc.get("kind", ""), doc.get("metadata", {}).get("name", "")
        stack = [doc]
        while stack:
            node = stack.pop()
            if isinstance(node, dict):
                for k, v in node.items():
                    if k in CONTAINER_LISTS and isinstance(v, list):
                        for c in v:
                            if isinstance(c, dict) and isinstance(c.get("image"), str):
                                yield f"{kind}/{name}", c["image"]
                    else:
                        stack.append(v)
            elif isinstance(node, list):
                stack.extend(node)


def check_render(version: str, chart_dir: str, forwarded: str, defaults: dict, own_registry: str, tmp: str) -> None:
    values = os.path.join(tmp, f"forwarded-{version}.yaml")
    with open(values, "w", encoding="utf-8") as f:
        f.write(forwarded)
    r = cc.run(["helm", "template", "t", chart_dir, "-f", values, *BUNDLED])
    if r.returncode != 0:
        cc.fail(f"the substrate chart {version} does not render with the values the meta chart forwards\n{r.stderr}")
    tolerated = {defaults[k] for k in TOLERATED if isinstance(defaults.get(k), str)}
    seen = 0
    for where, image in containers(r.stdout):
        seen += 1
        if image.startswith(own_registry.rstrip("/") + "/"):
            continue  # the line's own components (giantswarm/agent-platform#580)
        if image in tolerated:
            continue
        if host_of(image) != REGISTRY:
            cc.fail(f"the substrate chart {version}, rendered with the meta chart's forwarded values, runs {image!r} in {where}: a third-party image off {REGISTRY} — pin its key in values.yaml substrate.images")
    if not seen:
        cc.fail(f"the substrate chart {version} rendered no container")
    print(f"ok: {version} rendered with the forwarded block and the bundled store on — {seen} containers, every third-party image on {REGISTRY}")


def main(meta: str) -> int:
    with open(os.path.join(meta, "values.yaml"), encoding="utf-8") as f:
        values = yaml.safe_load(f)
    pins = values.get("substrate", {}).get("images")
    if not isinstance(pins, dict) or not pins:
        cc.fail("values.yaml substrate.images is empty: the substrate chart's third-party defaults are Docker Hub and ghcr")
    rendered = cc.docs(cc.render_meta(meta, [*cc.QUICKSTART, *KAGENT_ON]))
    if ("HelmRelease", "substrate") not in rendered:
        cc.fail("the meta chart renders no substrate HelmRelease with components.kagent on")
    forwarded = cc.hr_values(rendered[("HelmRelease", "substrate")])
    if yaml.safe_load(forwarded).get("images") != pins:
        cc.fail("the substrate HelmRelease does not forward values.yaml's substrate.images verbatim")
    url, rng = cc.source(rendered[("OCIRepository", "substrate")])
    floor = worker.floor_of(rng)
    releases = admitted_releases(url, rng)
    if floor not in releases:
        cc.fail(f"{url} does not publish {floor}, the floor of {rng!r}")
    with tempfile.TemporaryDirectory() as tmp:
        for version in releases:
            dest = os.path.join(tmp, version)
            os.makedirs(dest)
            got = cc.pull(url, version, dest)
            if got != version:
                cc.fail(f"helm pull {url} --version {version} unpacked {got}")
            chart_dir = os.path.join(dest, url.rsplit("/", 1)[1])
            with open(os.path.join(chart_dir, "values.yaml"), encoding="utf-8") as f:
                chart_values = yaml.safe_load(f)
            defaults = chart_values.get("images") or {}
            check_pins(version, defaults, pins)
            # The line's own components come from image.registry — the meta chart's
            # forwarded value where it sets one (a mirror), else the chart's own.
            own_registry = (yaml.safe_load(forwarded).get("image") or {}).get("registry") or chart_values.get("image", {}).get("registry", "")
            check_render(version, chart_dir, forwarded, defaults, own_registry, tmp)
    print(f"ok: substrate.images holds for {len(releases)} release(s) of {rng!r}: {', '.join(releases)}")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        cc.fail(__doc__.strip().splitlines()[-1])
    sys.exit(main(sys.argv[1]))
