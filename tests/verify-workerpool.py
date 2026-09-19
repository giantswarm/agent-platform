#!/usr/bin/env python3
"""Assert the Substrate WorkerPool reaches the cluster as written and is guarded
(giantswarm/agent-platform#457, #472).

One WorkerPool runs one CPU FEATURE SET: an actor's golden snapshot is a gVisor
checkpoint that restores only on a host whose CPU offers every feature the
checkpoint recorded. The pin is `kagent.substrateWorkerPool.template.nodeSelector`
— the architecture by default, and on CAPA (Karpenter) the vendor and the CPU
generation an installation sets through its values (the fleet template renders
`karpenter.k8s.aws/instance-cpu-manufacturer` and `instance-generation`). The
meta chart forwards the map to the kagent release, the kagent chart renders it
verbatim into WorkerPool.spec.template (toYaml). Nothing in the lab exercises the
labels (kind has no Karpenter), so this is where the pin is proven:
  - the default forwards the architecture alone, no annotation, no spread key;
  - an installation's three-label pin reaches the kagent release verbatim, every
    value a string, nothing else in the map;
  - a value that is not a string (`instance-generation: 6` unquoted, which the
    apiserver refuses on apply — map[string]string — long after a silent render)
    fails the render naming the key;
  - the kagent chart the range resolves to renders the forwarded values into ONE
    WorkerPool named substrateWorkerPool.name whose spec.template.nodeSelector is
    the pin, unchanged, next to the forwarded resources.

The one pool is also the platform's failure domain (#472):
  - the two Karpenter knobs of the template — `annotations` karpenter.sh/do-not-
    disrupt and `nodeSelector` karpenter.sh/capacity-type — reach the kagent
    release and the resolved kagent chart's WorkerPool verbatim (they are unset
    by default; an installation sets them);
  - `topologySpreadConstraints` and `podAntiAffinity` are PRUNED SILENTLY by the
    WorkerPool CRD of every Substrate release before the line's 1.0.0 (a structural
    schema): the render refuses them naming the key, the floor and the range
    while components.substrate.versionRange's floor is below the release that
    carries the fields (agent-platform.substrate.workerPoolSpreadFloor in
    _helpers.tpl — read from that file here; an EMPTY floor would refuse the two
    keys unconditionally), and forwards them verbatim from it on;
  - any other key WorkerPool.spec.template does not have is refused too (a typo
    would be pruned in silence);
  - the worker PodDisruptionBudget (kagent.substrateWorkerPool.podDisruptionBudget,
    on by default) renders from the connectivity chart OF THE WORKING TREE with
    the values the meta chart forwards to that release — named after the pool, in
    the kagent namespace, selecting ate.dev/worker-pool: <pool>, maxUnavailable 1,
    AlwaysAllow — is gone with enabled: false, and never reaches the kagent
    release (components.kagent.omitKeys).

Network: gsoci.azurecr.io (the kagent chart). Usage: verify-workerpool.py <meta chart dir>
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
ARCH = {"kubernetes.io/arch": "amd64"}
# What the fleet template renders for a CAPA installation with both pins set
# (shared-configs default/apps/agent-platform: agentPlatform.workerPoolCpuManufacturer,
# workerPoolCpuGeneration) — the generation as the string the label carries.
PIN = {**ARCH, "karpenter.k8s.aws/instance-cpu-manufacturer": "amd", "karpenter.k8s.aws/instance-generation": "6"}
GENERATION_KEY = "karpenter.k8s.aws/instance-generation"
# The two Karpenter knobs of #472, as an installation sets them (the fleet
# template renders the capacity type from agentPlatform.workerPoolCapacityType).
DO_NOT_DISRUPT = {"karpenter.sh/do-not-disrupt": "true"}
CAPACITY_KEY = "karpenter.sh/capacity-type"
KNOBS = {"nodeSelector": {**PIN, CAPACITY_KEY: "on-demand"}, "annotations": DO_NOT_DISRUPT}
# The spread the README recommends once the Substrate line carries the fields.
SPREAD = {
    "topologySpreadConstraints": [
        {"maxSkew": 1, "minDomains": 2, "topologyKey": "kubernetes.io/hostname", "whenUnsatisfiable": "DoNotSchedule",
         "labelSelector": {"matchLabels": {"ate.dev/worker-pool": "kagent-default"}}},
        {"maxSkew": 1, "topologyKey": "topology.kubernetes.io/zone", "whenUnsatisfiable": "ScheduleAnyway",
         "labelSelector": {"matchLabels": {"ate.dev/worker-pool": "kagent-default"}}},
    ],
    "podAntiAffinity": {"preferredDuringSchedulingIgnoredDuringExecution": [
        {"weight": 100, "podAffinityTerm": {"topologyKey": "kubernetes.io/hostname",
                                            "labelSelector": {"matchLabels": {"ate.dev/worker-pool": "kagent-default"}}}}]},
}
# The Substrate line's ungated WorkerPool.spec.template fields
# (pkg/api/v1alpha1/workerpool_types.go; the two gated ones are the line's carried patch);
# the guard names them.
TEMPLATE_FIELDS = "labels, annotations, nodeSelector, tolerations, priorityClassName, nodeAffinity, resources"
WORKER_LABEL = "ate.dev/worker-pool"
SPREAD_FLOOR_RE = re.compile(r'^\{\{- define "agent-platform\.substrate\.workerPoolSpreadFloor" -\}\}(\S*?)\{\{- end -\}\}$', re.M)
# A range below any release that could carry the spread fields (a synthetic older
# minor in the stable shape), and one at the floor, for the guard's two branches
# once the floor is set.
BELOW_FLOOR_RANGE = ">=0.9.0 <0.10.0-0"


def values_file(tmp: str, name: str, template: dict) -> str:
    """A values file setting kagent.substrateWorkerPool.template to `template`."""
    path = os.path.join(tmp, f"{name}.yaml")
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump({"kagent": {"substrateWorkerPool": {"template": template}}}, f)
    return path


def render(meta: str, flags: list[str]) -> dict[tuple[str, str], str]:
    return cc.docs(cc.render_meta(meta, [*cc.QUICKSTART, *KAGENT_ON, *flags]))


def kagent_release(meta: str, flags: list[str]) -> tuple[dict, str, str]:
    """The kagent HelmRelease's values (parsed and as forwarded YAML) and its source."""
    rendered = render(meta, flags)
    if ("HelmRelease", "kagent") not in rendered:
        cc.fail("the meta chart renders no kagent HelmRelease with components.kagent on")
    forwarded = cc.hr_values(rendered[("HelmRelease", "kagent")])
    url, rng = cc.source(rendered[("OCIRepository", "kagent")])
    return yaml.safe_load(forwarded), forwarded, f"{url} {rng}"


def connectivity_values(meta: str, flags: list[str]) -> str:
    rendered = render(meta, flags)
    if ("HelmRelease", "agent-platform-connectivity") not in rendered:
        cc.fail("the meta chart renders no agent-platform-connectivity HelmRelease")
    return cc.hr_values(rendered[("HelmRelease", "agent-platform-connectivity")])


def template_of(values: dict) -> dict:
    return ((values.get("substrateWorkerPool") or {}).get("template") or {})


def node_selector(values: dict) -> dict:
    return template_of(values).get("nodeSelector")


def render_fails(meta: str, flags: list[str], needle: str, what: str) -> None:
    r = cc.run(["helm", "template", cc.RELEASE, meta, *cc.QUICKSTART, *KAGENT_ON, *flags])
    if r.returncode == 0:
        cc.fail(f"{what} renders; it must fail the render")
    if needle not in r.stderr:
        cc.fail(f"{what} failed the render for the wrong reason (expected {needle!r}):\n{r.stderr}")


def next_minor(version: str) -> str:
    """X.(Y+1).0 of a version — the ceiling a Substrate range confines itself to (#466)."""
    x, y, _ = version.split("-", 1)[0].split(".")
    return f"{x}.{int(y) + 1}.0"


def spread_floor(meta: str) -> str:
    """The Substrate release agent-platform.substrate.workerPoolSpreadFloor names — the
    one line of _helpers.tpl this check and the guard share."""
    helpers = open(os.path.join(meta, "templates", "_helpers.tpl"), encoding="utf-8").read()
    m = SPREAD_FLOOR_RE.search(helpers)
    if not m:
        cc.fail("templates/_helpers.tpl has no one-line define agent-platform.substrate.workerPoolSpreadFloor; the guard and this check read the Substrate floor from it")
    return m.group(1)


def worker_pdbs(manifest: str, pool: str) -> list[dict]:
    return [d for d in yaml.safe_load_all(manifest)
            if isinstance(d, dict) and d.get("kind") == "PodDisruptionBudget"
            and (((d.get("spec") or {}).get("selector") or {}).get("matchLabels") or {}).get(WORKER_LABEL) == pool]


def check_pdb(meta: str, tmp: str) -> None:
    connectivity = os.path.join(os.path.dirname(os.path.abspath(meta)), "agent-platform-connectivity")
    if not os.path.isdir(connectivity):
        cc.fail(f"no connectivity chart next to {meta} ({connectivity}); the worker budget is its object")
    kagent_values, _, _ = kagent_release(meta, [])
    pool = kagent_values["substrateWorkerPool"]["name"]
    if "podDisruptionBudget" in kagent_values["substrateWorkerPool"]:
        cc.fail("kagent.substrateWorkerPool.podDisruptionBudget reaches the kagent release; the budget is the connectivity chart's (components.kagent.omitKeys)")
    print("ok: kagent.substrateWorkerPool.podDisruptionBudget never reaches the kagent release")
    for enabled in (True, False):
        flags = [] if enabled else ["--set", "kagent.substrateWorkerPool.podDisruptionBudget.enabled=false"]
        forwarded = connectivity_values(meta, flags)
        pdb = ((yaml.safe_load(forwarded).get("kagent") or {}).get("substrateWorkerPool") or {}).get("podDisruptionBudget") or {}
        if pdb.get("enabled") is not enabled:
            cc.fail(f"the connectivity release does not carry kagent.substrateWorkerPool.podDisruptionBudget.enabled={enabled}: {pdb!r}")
        values_path = os.path.join(tmp, f"connectivity-{enabled}.yaml")
        with open(values_path, "w", encoding="utf-8") as f:
            f.write(forwarded)
        r = cc.run(["helm", "template", "agent-platform-connectivity", connectivity, "-n", "agent-platform", "-f", values_path, *cc.API_VERSIONS])
        if r.returncode != 0:
            cc.fail(f"the connectivity chart of the working tree rejects the values the meta chart forwards\n{r.stderr}")
        pdbs = worker_pdbs(r.stdout, pool)
        if not enabled:
            if pdbs:
                cc.fail(f"a PodDisruptionBudget selecting {WORKER_LABEL}={pool} renders with podDisruptionBudget.enabled=false")
            print("ok: kagent.substrateWorkerPool.podDisruptionBudget.enabled=false renders no worker budget")
            continue
        if len(pdbs) != 1:
            cc.fail(f"expected one PodDisruptionBudget selecting {WORKER_LABEL}={pool} from the connectivity chart, got {len(pdbs)}")
        pdb_doc = pdbs[0]
        ns = (yaml.safe_load(forwarded).get("kagent") or {}).get("namespaceOverride") or "agent-platform"
        spec = pdb_doc["spec"]
        problems = []
        if pdb_doc["metadata"]["name"] != pool:
            problems.append(f"named {pdb_doc['metadata']['name']!r}, not after the pool {pool!r}")
        if pdb_doc["metadata"].get("namespace") != ns:
            problems.append(f"in namespace {pdb_doc['metadata'].get('namespace')!r}, not the kagent namespace {ns!r}")
        if spec.get("maxUnavailable") != 1:
            problems.append(f"maxUnavailable is {spec.get('maxUnavailable')!r}, not 1")
        if "minAvailable" in spec:
            problems.append(f"carries minAvailable {spec['minAvailable']!r} next to maxUnavailable")
        if spec.get("unhealthyPodEvictionPolicy") != "AlwaysAllow":
            problems.append(f"unhealthyPodEvictionPolicy is {spec.get('unhealthyPodEvictionPolicy')!r}, not AlwaysAllow")
        if spec["selector"]["matchLabels"] != {WORKER_LABEL: pool}:
            problems.append(f"selects {spec['selector']['matchLabels']!r}, not {WORKER_LABEL}: {pool} alone")
        if problems:
            cc.fail("the worker PodDisruptionBudget is wrong: " + "; ".join(problems))
        print(f"ok: the connectivity chart renders PodDisruptionBudget {pool} in {ns} selecting {WORKER_LABEL}={pool}, maxUnavailable 1, AlwaysAllow")


def main(meta: str) -> int:
    with tempfile.TemporaryDirectory() as tmp:
        values, _, _ = kagent_release(meta, [])
        template = template_of(values)
        if node_selector(values) != ARCH:
            cc.fail(f"the default forwards nodeSelector {node_selector(values)!r} to the kagent release; expected the architecture alone {ARCH!r}")
        if stray := sorted(set(template) - {"nodeSelector", "resources"}):
            cc.fail(f"the default forwards {stray!r} in kagent.substrateWorkerPool.template; the default template is the architecture pin and the resources, nothing else (the disruption knobs are an installation's)")
        print(f"ok: the default forwards the architecture alone ({ARCH}) — no annotation, no spread, no capacity type")

        values, forwarded, source = kagent_release(meta, ["-f", values_file(tmp, "pin", {"nodeSelector": PIN})])
        got = node_selector(values)
        if got != PIN:
            cc.fail(f"an installation's pin does not reach the kagent release verbatim:\n  got      {got!r}\n  expected {PIN!r}")
        if wrong := {k: v for k, v in got.items() if not isinstance(v, str)}:
            cc.fail(f"the forwarded nodeSelector carries non-string values {wrong!r}; the apiserver refuses them on apply")
        print(f"ok: the vendor and generation pin reaches the kagent release verbatim, every value a string ({PIN})")

        render_fails(meta, ["-f", values_file(tmp, "unquoted", {"nodeSelector": {**PIN, GENERATION_KEY: 6}})],
                     f"kagent.substrateWorkerPool.template.nodeSelector.{GENERATION_KEY} is 6",
                     f"an unquoted generation ({GENERATION_KEY}: 6)")
        print(f"ok: an unquoted generation fails the render naming kagent.substrateWorkerPool.template.nodeSelector.{GENERATION_KEY}")

        # --- #472: the two Karpenter knobs reach the kagent release verbatim ---
        knobs_values, knobs_forwarded, _ = kagent_release(meta, ["-f", values_file(tmp, "knobs", KNOBS)])
        knobs_template = template_of(knobs_values)
        if knobs_template.get("annotations") != DO_NOT_DISRUPT or knobs_template.get("nodeSelector") != KNOBS["nodeSelector"]:
            cc.fail(f"the disruption knobs do not reach the kagent release verbatim:\n  got      annotations={knobs_template.get('annotations')!r} nodeSelector={knobs_template.get('nodeSelector')!r}\n  expected annotations={DO_NOT_DISRUPT!r} nodeSelector={KNOBS['nodeSelector']!r}")
        print(f"ok: karpenter.sh/do-not-disrupt and {CAPACITY_KEY}: on-demand reach the kagent release verbatim in the template")

        # --- #472: what WorkerPool.spec.template would prune is refused ---
        render_fails(meta, ["-f", values_file(tmp, "typo", {"nodeSelectors": ARCH})],
                     f"kagent.substrateWorkerPool.template.nodeSelectors is not a WorkerPool.spec.template field of the Substrate line ({TEMPLATE_FIELDS})",
                     "a key WorkerPool.spec.template does not have (nodeSelectors)")
        print("ok: a template key the WorkerPool CRD does not have fails the render naming the key and the fields")

        floor = spread_floor(meta)
        for key in ("topologySpreadConstraints", "podAntiAffinity"):
            spread = {"nodeSelector": ARCH, key: SPREAD[key]}
            if not floor:
                needle = f"kagent.substrateWorkerPool.template.{key} is set, but no release of the Substrate line (components.substrate.versionRange"
                render_fails(meta, ["-f", values_file(tmp, f"spread-{key}", spread)], needle, f"template.{key} with no Substrate release carrying it")
                # Unconditional: a range moved ahead by hand changes nothing while no release carries the field.
                render_fails(meta, ["-f", values_file(tmp, f"spread-{key}", spread), "--set", "components.substrate.versionRange=>=99.0.0 <100.0.0-0"],
                             needle, f"template.{key} with the Substrate range moved ahead by hand")
                print(f"ok: template.{key} fails the render naming the key and the range — no Substrate release carries the field (workerPoolSpreadFloor is empty), whatever the range")
                continue
            needle = f"kagent.substrateWorkerPool.template.{key} needs the Substrate line at {floor} or later"
            render_fails(meta, ["-f", values_file(tmp, f"spread-{key}", spread), "--set", f"components.substrate.versionRange={BELOW_FLOOR_RANGE}"],
                         needle, f"template.{key} with components.substrate.versionRange below {floor}")
            # A range without a floor is refused before the spread guard runs: the
            # worker image is derived from the floor (#466, verify-worker-image.py).
            render_fails(meta, ["-f", values_file(tmp, f"spread-{key}", spread), "--set", "components.substrate.versionRange=0.x"],
                         'components.substrate.versionRange "0.x" does not confine one Substrate release', f"template.{key} with a components.substrate.versionRange without a floor")
            at_floor, _, _ = kagent_release(meta, ["-f", values_file(tmp, f"spread-{key}", spread), "--set", f"components.substrate.versionRange=>={floor} <{next_minor(floor)}-0"])
            if template_of(at_floor).get(key) != SPREAD[key]:
                cc.fail(f"template.{key} does not reach the kagent release verbatim with the Substrate range at {floor}: {template_of(at_floor).get(key)!r}")
            print(f"ok: template.{key} fails the render below the Substrate floor {floor} (naming the key, the floor and the range) and reaches the kagent release verbatim from it on")

        # --- #472: the worker budget, from the connectivity chart of the working tree ---
        check_pdb(meta, tmp)

        # The rendered object: the kagent chart the range resolves to, with the
        # values the meta chart forwards for the pin and the knobs.
        url, rng = source.split(" ", 1)
        version = fluxsemver.resolve(cc.registry_tags(url), rng)
        if not version:
            cc.fail(f"no published kagent chart satisfies {rng!r} at {url}")
        chart_dir = os.path.join(tmp, "kagent")
        resolved = cc.pull(url, version, chart_dir)
        for what, text, expected_template in (("the pin", forwarded, {"nodeSelector": PIN}), ("the disruption knobs", knobs_forwarded, KNOBS)):
            forwarded_file = os.path.join(tmp, f"forwarded-{what.split()[-1]}.yaml")
            with open(forwarded_file, "w", encoding="utf-8") as f:
                f.write(text)
            r = cc.run(["helm", "template", "kagent", f"{chart_dir}/kagent", "-n", "agent-platform", "-f", forwarded_file, *cc.API_VERSIONS])
            if r.returncode != 0:
                cc.fail(f"the kagent chart {resolved} rejects the values the meta chart forwards for {what}\n{r.stderr}")
            pools = [d for d in yaml.safe_load_all(r.stdout) if isinstance(d, dict) and d.get("kind") == "WorkerPool"]
            name = values["substrateWorkerPool"]["name"]
            if [p["metadata"]["name"] for p in pools] != [name]:
                cc.fail(f"the kagent chart {resolved} renders WorkerPools {[p['metadata']['name'] for p in pools]}; expected the one pool {name!r}")
            rendered_template = pools[0]["spec"].get("template") or {}
            for field, want in expected_template.items():
                if rendered_template.get(field) != want:
                    cc.fail(f"WorkerPool {name} of the kagent chart {resolved} carries {field} {rendered_template.get(field)!r} for {what}; forwarded {want!r}")
            if rendered_template.get("resources") != values["substrateWorkerPool"]["template"]["resources"]:
                cc.fail(f"WorkerPool {name} of the kagent chart {resolved} does not carry the forwarded resources: {rendered_template.get('resources')!r}")
            print(f"ok: the kagent chart {resolved} (the range {rng!r}) renders the one WorkerPool {name} with {what} verbatim in spec.template, next to the forwarded resources")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    sys.exit(main(sys.argv[1]))
