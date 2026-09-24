#!/usr/bin/env python3
"""Assert every Substrate release the meta chart admits runs its third-party
images from gsoci, and that the forwarded `substrate.images` block can neither
hold a release's data plane back nor turn this chart red when the line tags a
patch (giantswarm/agent-platform#575, #580, #654).

The substrate chart runs images that are not the line's own — the bundled
control-plane database, the bundled snapshot store and its bucket-init Job, the
agentgateway build of the atenet router and egress — from an `images:` map the
line stamps at publish. The meta chart forwards its `substrate:` block
verbatim, so a key of values.yaml's `substrate.images` wins over the release's
default. `components.substrate.versionRange` admits the line's patches without
a release of this chart, so nothing here may require a value of this chart to
equal what a newer admitted release names: that turned `main` red each time the
line tagged a patch whose data plane moved (#654). What is checked instead:

  * every release the range admits: each third-party default is a gsoci
    reference the registry publishes (the digest where it carries one, else the
    tag), `images.agentgateway` under the repository of the platform's own data
    planes (values.yaml agentgateway.proxy.image); and the release, rendered
    with the values the meta chart forwards (the bundled database and store
    on), runs no third-party image off gsoci;
  * `images.agentgateway` is never forwarded (FOLLOWS_RELEASE): atenet-router
    and atenet-egress run agentgateway with the static config the release
    renders (`-f /etc/agentgateway/config.yaml`), written for the build the
    release stamps, so an installation that floats onto a patch runs that
    patch's data plane; a forwarded value would hold it at an older build than
    its config was written for;
  * a key the block does pin is held to the floor's default only: the digest
    (or, without one, the tag) of the floor's default under gsoci, a key the
    floor knows. A newer admitted release naming another default is not a
    failure — the pin holds the floor's bits of the same service until a re-pin
    of the floor moves it.

`images.awsCli` is the exception (TOLERATED): the rustfs-bucket-init Job is a
release resource and a Job's pod template is immutable, so its default stays
the chart's own short name, and a pin must be exactly the floor's value until
the line ships a Job that can be recreated (values.yaml says why;
tests/verify-images.py tolerates the rendered value by name).

Network: gsoci.azurecr.io (the substrate charts the range admits and the
registry manifests of their defaults). Needs PyYAML.
Usage: verify-substrate-images.py <meta chart dir>
"""
import importlib.util
import os
import re
import sys
import tempfile
import urllib.error

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
# Keys whose default stays off gsoci on purpose, with the reason.
TOLERATED = {
    "awsCli": "the rustfs-bucket-init Job's pod template is immutable; its image moves with a release whose Job can be recreated",
}
# Keys the release's own default rules, never a forwarded value, with the reason.
FOLLOWS_RELEASE = {
    "agentgateway": "atenet-router and atenet-egress run the static config the release renders, written for the agentgateway build it stamps; "
                    "a forwarded value holds an installation that floats onto a newer patch at an older data plane",
}
# An image index (multi-arch) or a single manifest, OCI or Docker.
MANIFESTS = ", ".join([
    "application/vnd.oci.image.index.v1+json",
    "application/vnd.docker.distribution.manifest.list.v2+json",
    cc.OCI_MANIFEST,
])
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


def repository_of(reference: str) -> str:
    """The reference without its tag and digest: host/path."""
    bare = re.sub(r"@sha256:[a-f0-9]{64}$", "", reference)
    head, _, last = bare.rpartition("/")
    return f"{head}/{last.split(':', 1)[0]}"


PUBLISHED: set[str] = set()


def published(reference: str) -> bool:
    """Whether the registry serves the reference's manifest: its digest where
    it carries one (what the runtime pulls), else its tag."""
    if reference not in PUBLISHED:
        repo = repository_of(reference)
        host, _, path = repo.partition("/")
        target = digest_of(reference) or tag_of(reference)
        if not target:
            return False
        try:
            cc.registry_get(f"oci://{repo}", f"https://{host}/v2/{path}/manifests/{target}", MANIFESTS)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return False
            cc.fail(f"the manifest of {reference} could not be fetched: {e}")
        except urllib.error.URLError as e:
            cc.fail(f"the manifest of {reference} could not be fetched: {e}")
        PUBLISHED.add(reference)
    return True


def admitted_releases(url: str, rng: str) -> list[str]:
    """Every release tag of the substrate chart the range admits, newest last."""
    tags = [t for t in cc.registry_tags(url) if fluxsemver.satisfies(t, rng)]
    if not tags:
        cc.fail(f"{url} publishes no version in {rng!r}")
    return sorted(tags, key=lambda t: fluxsemver.parse(t))


def check_defaults(version: str, defaults: dict, dataplane: str) -> None:
    """The release's own third-party defaults: on gsoci and published."""
    if not isinstance(defaults.get("agentgateway"), str):
        cc.fail(f"the substrate chart {version} has no images.agentgateway: the atenet data plane moved to a key this check does not know")
    for key, default in defaults.items():
        if key in TOLERATED:
            continue
        if not isinstance(default, str) or host_of(default) != REGISTRY:
            cc.fail(f"the substrate chart {version}'s images.{key} default {default!r} is not a {REGISTRY} reference: a defect of the Substrate release — its line stamps the gsoci copy")
        if key == "agentgateway" and repository_of(default) != dataplane:
            cc.fail(f"the substrate chart {version}'s images.agentgateway default {default!r} is not a release of the agentgateway line the platform's data planes run ({dataplane}, values.yaml agentgateway.proxy.image)")
        if not published(default):
            cc.fail(f"the substrate chart {version}'s images.{key} default {default!r} is not published: an installation that floats onto {version} cannot pull it")
    print(f"ok: {version} names its third-party images on {REGISTRY}, each published — the atenet data plane {defaults['agentgateway']}")


def check_pins(floor: str, defaults: dict, pins: dict) -> None:
    """The forwarded pins against the floor's defaults."""
    for key, pin in pins.items():
        if key in FOLLOWS_RELEASE:
            cc.fail(f"values.yaml substrate.images.{key} ({pin!r}) is forwarded: the Substrate release's own default rules this key — {FOLLOWS_RELEASE[key]}")
        default = defaults.get(key)
        if not isinstance(default, str):
            cc.fail(f"substrate.images.{key} pins {pin!r}, but the substrate chart {floor} (the floor) has no images.{key}: the value would forward to nothing — the key moved or went with the release")
        if key in TOLERATED:
            if pin != default:
                cc.fail(f"substrate.images.{key} ({pin!r}) differs from the substrate chart {floor}'s own default ({default!r}); the meta chart forwards this one unchanged on purpose — {TOLERATED[key]}")
            continue
        if host_of(pin) != REGISTRY:
            cc.fail(f"substrate.images.{key} ({pin!r}) is not a {REGISTRY} reference")
        if digest_of(default):
            if digest_of(pin) != digest_of(default):
                cc.fail(f"substrate.images.{key} ({pin!r}) does not carry the digest of the substrate chart {floor}'s default {default!r}: the pin holds the floor's bits — re-pin it from the floor's `helm show values`")
        elif tag_of(pin) != tag_of(default):
            cc.fail(f"substrate.images.{key} ({pin!r}) is not tagged like the substrate chart {floor}'s default {default!r}: the pin holds the floor's image — re-pin it from the floor's `helm show values`")
        if not published(pin):
            cc.fail(f"substrate.images.{key} ({pin!r}) is not published")
        print(f"ok: images.{key}: {pin} holds the floor {floor}'s {default}")


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
    seen = dataplane = 0
    for where, image in containers(r.stdout):
        seen += 1
        dataplane += image == defaults["agentgateway"]
        if image.startswith(own_registry.rstrip("/") + "/"):
            continue  # the line's own components (giantswarm/agent-platform#580)
        if image in tolerated:
            continue
        if host_of(image) != REGISTRY:
            cc.fail(f"the substrate chart {version}, rendered with the meta chart's forwarded values, runs {image!r} in {where}: a third-party image off {REGISTRY}")
    if not seen:
        cc.fail(f"the substrate chart {version} rendered no container")
    if not dataplane:
        cc.fail(f"the substrate chart {version}, rendered with the meta chart's forwarded values, never runs its own data plane {defaults['agentgateway']!r}")
    print(f"ok: {version} rendered with the forwarded block and the bundled store on — {seen} containers, every third-party image on {REGISTRY}, "
          f"the release's own data plane in {dataplane}")


def main(meta: str) -> int:
    with open(os.path.join(meta, "values.yaml"), encoding="utf-8") as f:
        values = yaml.safe_load(f)
    pins = values.get("substrate", {}).get("images") or {}
    proxy = values.get("agentgateway", {}).get("proxy", {}).get("image", {})
    dataplane = f"{proxy.get('registry')}/{proxy.get('repository')}"
    rendered = cc.docs(cc.render_meta(meta, [*cc.QUICKSTART, *KAGENT_ON]))
    if ("HelmRelease", "substrate") not in rendered:
        cc.fail("the meta chart renders no substrate HelmRelease with components.kagent on")
    forwarded = cc.hr_values(rendered[("HelmRelease", "substrate")])
    if (yaml.safe_load(forwarded).get("images") or {}) != pins:
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
            check_defaults(version, defaults, dataplane)
            if version == floor:
                check_pins(floor, defaults, pins)
            # The line's own components come from image.registry — the meta chart's
            # forwarded value where it sets one (a mirror), else the chart's own.
            own_registry = (yaml.safe_load(forwarded).get("image") or {}).get("registry") or chart_values.get("image", {}).get("registry", "")
            check_render(version, chart_dir, forwarded, defaults, own_registry, tmp)
    print(f"ok: {len(releases)} release(s) of {rng!r} run their own gsoci defaults ({', '.join(releases)}); substrate.images pins {', '.join(pins) or 'nothing'} at the floor {floor}")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        cc.fail(__doc__.strip().splitlines()[-1])
    sys.exit(main(sys.argv[1]))
