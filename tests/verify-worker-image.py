#!/usr/bin/env python3
"""Assert the Substrate worker image the kagent WorkerPool runs follows the meta
chart's own Substrate pin, never the kagent build's stamp
(giantswarm/agent-platform#466).

The chart pins Substrate and kagent as two independent ranges, and every kagent
build stamps into its chart the worker image (`substrateWorkerPool.workerImage`,
ateom-gvisor) of the Substrate it was published against. Chart 4.15.2 pinned
one Substrate release with an open kagent range; a kagent build that moved the
line to the next Substrate was admitted, so the newer worker ran under the
older atelet — the pause bundle had been renamed, every golden boot failed on
`bundles/_pause/config.json`, and nothing on any hop named the skew. The chart
now DERIVES the worker from `components.substrate.versionRange`'s floor
(`agent-platform.substrate.workerImage`: `<substrate.image.registry>/
<substrate.image.repository>/ateom-gvisor:<floor>`), merged over the forwarded
kagent block, and confines the Substrate
range to one release (`agent-platform.substrate.validateRange`). Here:
  - the default render forwards workerImage = gsoci.azurecr.io/giantswarm/
    substrate/ateom-gvisor:<floor of values.yaml's components.substrate.versionRange>
    (the line's release copied under its upstream path, giantswarm/retagger#1229)
    to the kagent release, and the substrate OCIRepository carries that range;
  - substrate.image.registry (a mirror) moves the worker's registry with it, the
    repository path unchanged;
  - an installation's own workerImage stands verbatim while its tag is the pinned
    release; another tag, or a digest without a tag, fails the render naming the
    key, the release and the derived image;
  - the 4.15.2 shape — the Substrate range a minor behind, the kagent range as it
    is — forwards that minor's worker: the worker follows the chart's
    Substrate, whatever kagent build the range admits;
  - an exact Substrate pin (the BOM shape) derives that version;
  - a range that does not confine one runtime contract (no floor, a ceiling past
    the next minor or at the next patch, a -0 bound, a tilde or caret range, a <=
    ceiling, the former -gs.N shape) fails the render naming the range;
  - the kagent chart the range resolves to renders the forwarded values into the
    one WorkerPool whose spec.workerImage is the derived image, its own stamp
    overridden — and the Substrate that build was published against
    (Chart.yaml dependencies[substrate].version) is the pinned release or an
    older patch of its minor: a patch of the Substrate line changes no runtime
    contract (the range shape says so), so the chart's pin may lead the kagent
    stamp by a patch — a data-plane fix ships without a kagent rebuild — while
    a kagent build stamped against a newer Substrate than the chart pins, or
    against another minor, fails: the meta chart's CI names the day the kagent
    line moves to another Substrate release before the chart's own pin does.

Network: gsoci.azurecr.io (the kagent chart). Usage: verify-worker-image.py <meta chart dir>
"""
import importlib.util
import os
import re
import sys
import tempfile

import yaml

import fluxsemver

HERE = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location("components_charts", os.path.join(HERE, "verify-components-charts.py"))
cc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cc)

KAGENT_ON = ["--set", "components.kagent.enabled=true", "--set", "ingress.parentRefs[0].name=x"]
REGISTRY = "gsoci.azurecr.io"
REPOSITORY = "giantswarm/substrate"
WORKER = "ateom-gvisor"
# The 4.15.2 shape of the skew: the Substrate range a minor behind the kagent
# line's stamp (a synthetic older minor in the stable shape; the render alone is
# asserted). The atelet of that range and this worker are one release.
OLDER_RANGE = ">=0.9.0 <0.10.0"
FLOOR_RE = re.compile(r"^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?$")


def tuple_of(version: str) -> str:
    return version.split("-", 1)[0]


def ints_of(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in tuple_of(version).split("."))


def next_patch(version: str) -> str:
    x, y, z = tuple_of(version).split(".")
    return f"{x}.{y}.{int(z) + 1}"


def next_minor(version: str) -> str:
    """X.(Y+1).0 of a version — the ceiling a Substrate range confines itself to (#466)."""
    x, y, _ = tuple_of(version).split(".")
    return f"{x}.{int(y) + 1}.0"


def floor_of(rng: str) -> str:
    first = rng.replace(",", " ").split()[0]
    v = first[2:] if first.startswith(">=") else first
    if not FLOOR_RE.match(v):
        cc.fail(f"components.substrate.versionRange {rng!r} has no floor to derive the worker from")
    return v


def render(meta: str, flags: list[str]) -> dict[tuple[str, str], str]:
    return cc.docs(cc.render_meta(meta, [*cc.QUICKSTART, *KAGENT_ON, *flags]))


def kagent_release(meta: str, flags: list[str]) -> tuple[dict, str, str]:
    rendered = render(meta, flags)
    if ("HelmRelease", "kagent") not in rendered:
        cc.fail("the meta chart renders no kagent HelmRelease with components.kagent on")
    forwarded = cc.hr_values(rendered[("HelmRelease", "kagent")])
    url, rng = cc.source(rendered[("OCIRepository", "kagent")])
    return yaml.safe_load(forwarded), forwarded, f"{url} {rng}"


def worker_image(values: dict) -> str:
    return ((values.get("substrateWorkerPool") or {}).get("workerImage")) or ""


def render_fails(meta: str, flags: list[str], needle: str, what: str) -> None:
    r = cc.run(["helm", "template", cc.RELEASE, meta, *cc.QUICKSTART, *KAGENT_ON, *flags])
    if r.returncode == 0:
        cc.fail(f"{what} renders; it must fail the render")
    if needle not in r.stderr:
        cc.fail(f"{what} failed the render for the wrong reason (expected {needle!r}):\n{r.stderr}")


def main(meta: str) -> int:
    values_yaml = yaml.safe_load(open(os.path.join(meta, "values.yaml"), encoding="utf-8"))
    rng = values_yaml["components"]["substrate"]["versionRange"]
    floor = floor_of(rng)
    derived = f"{REGISTRY}/{REPOSITORY}/{WORKER}:{floor}"
    if rng != f">={floor} <{next_minor(floor)}":
        cc.fail(f"values.yaml's components.substrate.versionRange {rng!r} is not `>={floor} <{next_minor(floor)}`: the range confines the pinned minor of the Substrate line — one runtime contract, the worker image's — and carries no -0 (a prerelease bound anywhere makes Flux evaluate the line's dev builds against the range)")
    if values_yaml["components"]["substrate-crds"]["versionRange"] != rng:
        cc.fail(f"components.substrate-crds.versionRange differs from components.substrate.versionRange {rng!r}; the two charts are one release of the line")

    values, _, source = kagent_release(meta, [])
    if worker_image(values) != derived:
        cc.fail(f"the default render forwards substrateWorkerPool.workerImage {worker_image(values)!r} to the kagent release; expected {derived!r}, the worker of the Substrate release the chart pins (the floor of components.substrate.versionRange {rng!r})")
    rendered = render(meta, [])
    _, substrate_rng = cc.source(rendered[("OCIRepository", "substrate")])
    if substrate_rng != rng:
        cc.fail(f"the substrate OCIRepository carries {substrate_rng!r}, not components.substrate.versionRange {rng!r}")
    print(f"ok: the default render forwards workerImage {derived} to the kagent release — the floor of the Substrate range {rng!r} the substrate OCIRepository carries")

    mirror = "mirror.example.com"
    values, _, _ = kagent_release(meta, ["--set", f"substrate.image.registry={mirror}"])
    if worker_image(values) != f"{mirror}/{REPOSITORY}/{WORKER}:{floor}":
        cc.fail(f"substrate.image.registry={mirror} does not move the worker's registry: {worker_image(values)!r}")
    print(f"ok: substrate.image.registry moves the worker image with the control plane's ({mirror}/{REPOSITORY}/{WORKER}:{floor})")

    own = f"quay.io/example/{WORKER}:{floor}"
    values, _, _ = kagent_release(meta, ["--set", f"kagent.substrateWorkerPool.workerImage={own}"])
    if worker_image(values) != own:
        cc.fail(f"an installation's own workerImage tagged with the pinned release does not reach the kagent release verbatim: {worker_image(values)!r}")
    print(f"ok: an own workerImage tagged {floor} reaches the kagent release verbatim ({own})")
    for wrong, what in ((f"{REGISTRY}/{REPOSITORY}/{WORKER}:{next_patch(floor)}", "another tag"), (f"{REGISTRY}/{REPOSITORY}/{WORKER}@sha256:{'0' * 64}", "a digest without a tag")):
        render_fails(meta, ["--set", f"kagent.substrateWorkerPool.workerImage={wrong}"],
                     f"kagent.substrateWorkerPool.workerImage ({wrong}) does not carry the Substrate release this chart pins ({floor}, the floor of components.substrate.versionRange)",
                     f"an own workerImage with {what}")
    print(f"ok: an own workerImage with another tag or a digest alone fails the render naming the key, the pinned release {floor} and the derived image")

    values, _, _ = kagent_release(meta, ["--set", f"components.substrate.versionRange={OLDER_RANGE}"])
    old_floor = floor_of(OLDER_RANGE)
    if worker_image(values) != f"{REGISTRY}/{REPOSITORY}/{WORKER}:{old_floor}":
        cc.fail(f"with the Substrate range at {OLDER_RANGE!r} the kagent release carries workerImage {worker_image(values)!r}; expected the {old_floor} worker — the worker follows the chart's Substrate, not the kagent build the range admits (the 4.15.2 skew, #466)")
    print(f"ok: the 4.15.2 shape (Substrate {OLDER_RANGE!r}, the kagent range untouched) forwards the {old_floor} worker — the worker follows the chart's Substrate pin")

    values, _, _ = kagent_release(meta, ["--set", f"components.substrate.versionRange={floor}"])
    if worker_image(values) != derived:
        cc.fail(f"an exact Substrate pin {floor!r} (the BOM shape) derives workerImage {worker_image(values)!r}; expected {derived!r}")
    print(f"ok: an exact Substrate pin (the BOM shape) derives the {floor} worker")

    for bad in ("0.x", f">={floor} <{next_minor(next_minor(floor))}", f">={floor} <{next_patch(floor)}", f">={floor} <{next_minor(floor)}-0", ">=0.0.30-gs.5 <0.0.31-0",
                f"~{tuple_of(floor)}", f"^{tuple_of(floor)}", f">={floor} <={floor}", f">={floor}", f"<{next_minor(floor)}"):
        render_fails(meta, ["--set", f"components.substrate.versionRange={bad}"],
                     f"components.substrate.versionRange {bad!r} does not confine one Substrate release".replace("'", '"'),
                     f"a Substrate range that does not confine one release ({bad})")
    print("ok: a Substrate range with no floor, a ceiling past the next minor or at the next patch, a -0 ceiling (which admits the line's dev builds), the former -gs.N shape, a ~ or ^ range, a <= ceiling or a floor alone fails the render naming the range")

    # --- the kagent chart the range resolves to, with the forwarded values ---
    with tempfile.TemporaryDirectory() as tmp:
        values, forwarded, source = kagent_release(meta, [])
        url, kagent_rng = source.split(" ", 1)
        version = fluxsemver.resolve(cc.registry_tags(url), kagent_rng)
        if not version:
            cc.fail(f"no published kagent chart satisfies {kagent_rng!r} at {url}")
        chart_dir = os.path.join(tmp, "kagent")
        resolved = cc.pull(url, version, chart_dir)
        forwarded_file = os.path.join(tmp, "forwarded.yaml")
        with open(forwarded_file, "w", encoding="utf-8") as f:
            f.write(forwarded)
        r = cc.run(["helm", "template", "kagent", f"{chart_dir}/kagent", "-n", "agent-platform", "-f", forwarded_file, *cc.API_VERSIONS])
        if r.returncode != 0:
            cc.fail(f"the kagent chart {resolved} rejects the values the meta chart forwards\n{r.stderr}")
        pools = [d for d in yaml.safe_load_all(r.stdout) if isinstance(d, dict) and d.get("kind") == "WorkerPool"]
        if len(pools) != 1:
            cc.fail(f"the kagent chart {resolved} renders {len(pools)} WorkerPools with the forwarded values; expected one")
        if pools[0]["spec"].get("workerImage") != derived:
            cc.fail(f"WorkerPool {pools[0]['metadata']['name']} of the kagent chart {resolved} runs workerImage {pools[0]['spec'].get('workerImage')!r}; the meta chart forwarded {derived!r}")
        chart_meta = yaml.safe_load(open(os.path.join(chart_dir, "kagent", "Chart.yaml"), encoding="utf-8"))
        stamped = yaml.safe_load(open(os.path.join(chart_dir, "kagent", "values.yaml"), encoding="utf-8"))
        stamp = ((stamped.get("substrateWorkerPool") or {}).get("workerImage")) or ""
        built_against = next((d.get("version") for d in chart_meta.get("dependencies") or [] if d.get("name") == "substrate"), "")
        if not built_against:
            cc.fail(f"the kagent chart {resolved} names no substrate dependency in Chart.yaml; the line stamps the Substrate it was published against there")
        print(f"ok: the kagent chart {resolved} (the range {kagent_rng!r}) renders the one WorkerPool with workerImage {derived} — its own stamp {stamp or '(none)'} overridden; published against Substrate {built_against}")
        built, pinned = ints_of(built_against), ints_of(floor)
        if built[:2] != pinned[:2] or built > pinned:
            cc.fail(f"the kagent build the range {kagent_rng!r} admits ({resolved}) was published against Substrate {built_against}, not the release the chart pins ({floor}) or an older patch of its minor: the derived worker keeps the runtime coherent, but the kagent controller and the chart's Substrate are meant to be one line — move components.substrate.versionRange (and its floor) with the kagent line, or hold the kagent range below that build")
        if built == pinned:
            print(f"ok: the kagent build the range admits was published against Substrate {tuple_of(built_against)}, the release the chart pins ({floor})")
        else:
            print(f"ok: the kagent build the range admits was published against Substrate {tuple_of(built_against)}, an older patch of the minor the chart pins ({floor}): a patch of the line changes no runtime contract, the kagent line follows at its next release")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    sys.exit(main(sys.argv[1]))
