#!/usr/bin/env python3
"""Assert the Substrate egress gateway's ephemeral storage is bounded on the
release the meta chart pins (giantswarm/agent-platform#880).

Kyverno's require-emptydir-requests-and-limits refuses a container that mounts
an emptyDir without a sizeLimit unless it requests and limits ephemeral-storage.
The Substrate chart bounds its atenet-egress Deployment itself from the release
agent-platform.substrate.egressStorageFloor names (atenetEgress.resources,
atenetEgress.extProc.resources, atenetEgress.drainSignal.sizeLimit); an older
release prunes those keys in silence, so below the floor the meta chart applies
the same bound as a kustomize strategic-merge patch on the substrate
HelmRelease (a Flux postRenderer, agent-platform.substrate.egressStoragePatch).

The check renders the meta chart, reads the substrate HelmRelease's forwarded
values and postRenderers, pulls the substrate chart at the range's floor,
renders it with the forwarded values and applies the rule to the atenet-egress
pod template:

  * floor below egressStorageFloor: the chart's own render fails the rule (the
    floor is not stale), the HelmRelease carries exactly one patch, on the
    Deployment atenet-egress, naming only containers and volumes the release
    renders, and the render with the patch merged by name passes;
  * floor at or above it: no patch, and the chart's own render passes from the
    forwarded keys;
  * the three knobs null: no patch (the meta chart then bounds nothing).

The forwarded block carries values.yaml's substrate.atenetEgress verbatim.

Usage: verify-substrate-egress-ephemeral-storage.py <meta chart dir>
Network: gsoci.azurecr.io. Needs PyYAML.
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

KAGENT_ON = ["--set", "components.kagent.enabled=true", "--set", "ingress.parentRefs[0].name=x"]
KNOBS_NULL = ["--set", "substrate.atenetEgress.resources=null",
              "--set", "substrate.atenetEgress.extProc.resources=null",
              "--set", "substrate.atenetEgress.drainSignal.sizeLimit=null"]
FLOOR_RE = r'^\{\{- define "agent-platform\.substrate\.egressStorageFloor" -\}\}(\S*?)\{\{- end -\}\}$'
DEPLOYMENT = "atenet-egress"
CONTAINER_LISTS = ("initContainers", "containers")


def egress_floor(meta: str) -> str:
    with open(os.path.join(meta, "templates", "_helpers.tpl"), encoding="utf-8") as f:
        m = re.search(FLOOR_RE, f.read(), re.M)
    if not m or not m.group(1):
        cc.fail("agent-platform.substrate.egressStorageFloor is not a one-line define in templates/_helpers.tpl")
    return m.group(1)


def stable(version: str) -> tuple:
    """The version without its prerelease, as the helper compares it."""
    return fluxsemver.parse(version.split("-", 1)[0])


def egress_patches(hr: dict) -> list[dict]:
    """The kustomize patches of the HelmRelease's postRenderers that target the egress Deployment."""
    out = []
    for pr in hr.get("spec", {}).get("postRenderers") or []:
        for p in (pr.get("kustomize") or {}).get("patches") or []:
            if (p.get("target") or {}).get("kind") == "Deployment" and (p.get("target") or {}).get("name") == DEPLOYMENT:
                out.append(p)
    return out


def pod_template(render: str) -> dict:
    for d in yaml.safe_load_all(render):
        if d and d.get("kind") == "Deployment" and d["metadata"]["name"] == DEPLOYMENT:
            return d["spec"]["template"]["spec"]
    cc.fail(f"the substrate chart renders no Deployment {DEPLOYMENT}")


def problems(spec: dict) -> list[str]:
    """What require-emptydir-requests-and-limits would report on the pod spec."""
    unbounded = {v["name"] for v in spec.get("volumes") or [] if "emptyDir" in v and not (v["emptyDir"] or {}).get("sizeLimit")}
    out = []
    for key in CONTAINER_LISTS:
        for c in spec.get(key) or []:
            if not unbounded & {m["name"] for m in c.get("volumeMounts") or []}:
                continue
            res = c.get("resources") or {}
            for field in ("requests", "limits"):
                if not (res.get(field) or {}).get("ephemeral-storage"):
                    out.append(f"{c['name']} mounts an unbounded emptyDir and has no resources.{field}.ephemeral-storage")
    return out


def merge(base, patch):
    """Strategic-merge semantics for what the patch carries: maps merge, the
    containers and volumes lists merge by name."""
    if isinstance(base, dict) and isinstance(patch, dict):
        out = dict(base)
        for k, v in patch.items():
            out[k] = merge(base[k], v) if k in base else v
        return out
    return patch


def apply(spec: dict, patch: dict) -> dict:
    """The pod template spec with the patch's containers and volumes merged by
    name; a name the release does not render fails (the patch would add a
    container without an image, or a stray volume)."""
    pspec = patch.get("spec", {}).get("template", {}).get("spec", {})
    out = {k: v for k, v in spec.items()}
    for key in ("containers", "volumes"):
        have = {x["name"]: x for x in spec.get(key) or []}
        for item in pspec.get(key) or []:
            if item["name"] not in have:
                cc.fail(f"the egress patch names {key[:-1]} {item['name']!r}, which the substrate chart does not render on Deployment {DEPLOYMENT}: the patch would add it")
            have[item["name"]] = merge(have[item["name"]], item)
        out[key] = list(have.values())
    return out


def render_chart(chart_dir: str, forwarded: str, tmp: str) -> dict:
    values = os.path.join(tmp, "forwarded.yaml")
    with open(values, "w", encoding="utf-8") as f:
        f.write(forwarded)
    r = cc.run(["helm", "template", "substrate", chart_dir, "-n", "ate-system", "-f", values])
    if r.returncode != 0:
        cc.fail(f"the substrate chart does not render with the values the meta chart forwards\n{r.stderr}")
    return pod_template(r.stdout)


def main(meta: str) -> int:
    with open(os.path.join(meta, "values.yaml"), encoding="utf-8") as f:
        own = (yaml.safe_load(f).get("substrate") or {}).get("atenetEgress") or {}
    if not own:
        cc.fail("values.yaml carries no substrate.atenetEgress block")
    floor_release = egress_floor(meta)

    rendered = cc.docs(cc.render_meta(meta, [*cc.QUICKSTART, *KAGENT_ON]))
    if ("HelmRelease", "substrate") not in rendered:
        cc.fail("the meta chart renders no substrate HelmRelease with components.kagent on")
    hr = yaml.safe_load(rendered[("HelmRelease", "substrate")])
    forwarded = cc.hr_values(rendered[("HelmRelease", "substrate")])
    if (yaml.safe_load(forwarded).get("atenetEgress") or {}) != own:
        cc.fail("the substrate HelmRelease does not forward values.yaml's substrate.atenetEgress verbatim")
    patches = egress_patches(hr)

    url, rng = cc.source(rendered[("OCIRepository", "substrate")])
    floor = worker.floor_of(rng)
    below = stable(floor) < stable(floor_release)
    with tempfile.TemporaryDirectory() as tmp:
        got = cc.pull(url, floor, tmp)
        if got != floor:
            cc.fail(f"helm pull {url} --version {floor} unpacked {got}")
        spec = render_chart(os.path.join(tmp, url.rsplit("/", 1)[1]), forwarded, tmp)
    own_render = problems(spec)

    if below:
        if not own_render:
            cc.fail(f"the substrate chart {floor} bounds the egress gateway's ephemeral storage itself: agent-platform.substrate.egressStorageFloor ({floor_release}) is stale — move it down to the release that first does")
        if len(patches) != 1:
            cc.fail(f"the substrate HelmRelease carries {len(patches)} egress patches with the range's floor {floor} below {floor_release}, want exactly one")
        patch = yaml.safe_load(patches[0]["patch"])
        if (patch.get("kind"), patch.get("metadata", {}).get("name")) != ("Deployment", DEPLOYMENT):
            cc.fail(f"the egress patch is not a strategic-merge patch on Deployment {DEPLOYMENT}: {patches[0]['patch']!r}")
        patched = problems(apply(spec, patch))
        if patched:
            cc.fail(f"the substrate chart {floor} with the egress patch still fails require-emptydir-requests-and-limits: {'; '.join(patched)}")
        print(f"ok: the substrate chart {floor} (below {floor_release}) fails the rule by itself ({'; '.join(own_render)}) and passes with the HelmRelease's patch merged by name")
    else:
        if patches:
            cc.fail(f"the substrate HelmRelease carries an egress patch with the range's floor {floor} at or above {floor_release}: the chart renders the bound from the forwarded keys, the patch is retired")
        if own_render:
            cc.fail(f"the substrate chart {floor}, rendered with the forwarded substrate.atenetEgress, fails require-emptydir-requests-and-limits: {'; '.join(own_render)}")
        print(f"ok: the substrate chart {floor} (at or above {floor_release}) passes the rule from the forwarded keys, no patch rendered")

    rendered_null = cc.docs(cc.render_meta(meta, [*cc.QUICKSTART, *KAGENT_ON, *KNOBS_NULL]))
    if egress_patches(yaml.safe_load(rendered_null[("HelmRelease", "substrate")])):
        cc.fail("the substrate HelmRelease carries an egress patch with the three knobs null: a null bounds nothing")
    print("ok: the three knobs null render no patch")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    sys.exit(main(sys.argv[1]))
