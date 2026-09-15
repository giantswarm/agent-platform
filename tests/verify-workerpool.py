#!/usr/bin/env python3
"""Assert the Substrate WorkerPool's placement pin reaches the cluster as written
(giantswarm/agent-platform#457).

One WorkerPool runs one CPU FEATURE SET: an actor's golden snapshot is a gVisor
checkpoint that restores only on a host whose CPU offers every feature the
checkpoint recorded. The pin is `kagent.substrateWorkerPool.template.nodeSelector`
— the architecture by default, and on CAPA (Karpenter) the vendor and the CPU
generation an installation sets through its values (the fleet template renders
`karpenter.k8s.aws/instance-cpu-manufacturer` and `instance-generation`). The
meta chart forwards the map to the kagent release, the kagent chart renders it
verbatim into WorkerPool.spec.template (toYaml). Nothing in the lab exercises the
labels (kind has no Karpenter), so this is where the pin is proven:
  - the default forwards the architecture alone;
  - an installation's three-label pin reaches the kagent release verbatim, every
    value a string, nothing else in the map;
  - a value that is not a string (`instance-generation: 6` unquoted, which the
    apiserver refuses on apply — map[string]string — long after a silent render)
    fails the render naming the key;
  - the kagent chart the range resolves to renders the forwarded values into ONE
    WorkerPool named substrateWorkerPool.name whose spec.template.nodeSelector is
    the pin, unchanged, next to the forwarded resources.

Network: ghcr.io (the kagent chart). Usage: verify-workerpool.py <meta chart dir>
"""
import importlib.util
import os
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


def values_file(tmp: str, name: str, node_selector: dict) -> str:
    path = os.path.join(tmp, f"{name}.yaml")
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump({"kagent": {"substrateWorkerPool": {"template": {"nodeSelector": node_selector}}}}, f)
    return path


def kagent_release(meta: str, flags: list[str]) -> tuple[dict, str, str]:
    """The kagent HelmRelease's values (parsed and as forwarded YAML) and its source."""
    rendered = cc.docs(cc.render_meta(meta, [*cc.QUICKSTART, *KAGENT_ON, *flags]))
    if ("HelmRelease", "kagent") not in rendered:
        cc.fail("the meta chart renders no kagent HelmRelease with components.kagent on")
    forwarded = cc.hr_values(rendered[("HelmRelease", "kagent")])
    url, rng = cc.source(rendered[("OCIRepository", "kagent")])
    return yaml.safe_load(forwarded), forwarded, f"{url} {rng}"


def node_selector(values: dict) -> dict:
    return ((values.get("substrateWorkerPool") or {}).get("template") or {}).get("nodeSelector")


def main(meta: str) -> int:
    with tempfile.TemporaryDirectory() as tmp:
        values, _, _ = kagent_release(meta, [])
        if node_selector(values) != ARCH:
            cc.fail(f"the default forwards nodeSelector {node_selector(values)!r} to the kagent release; expected the architecture alone {ARCH!r}")
        print(f"ok: the default forwards the architecture alone ({ARCH})")

        values, forwarded, source = kagent_release(meta, ["-f", values_file(tmp, "pin", PIN)])
        got = node_selector(values)
        if got != PIN:
            cc.fail(f"an installation's pin does not reach the kagent release verbatim:\n  got      {got!r}\n  expected {PIN!r}")
        if wrong := {k: v for k, v in got.items() if not isinstance(v, str)}:
            cc.fail(f"the forwarded nodeSelector carries non-string values {wrong!r}; the apiserver refuses them on apply")
        print(f"ok: the vendor and generation pin reaches the kagent release verbatim, every value a string ({PIN})")

        unquoted = values_file(tmp, "unquoted", {**PIN, GENERATION_KEY: 6})
        r = cc.run(["helm", "template", cc.RELEASE, meta, *cc.QUICKSTART, *KAGENT_ON, "-f", unquoted])
        if r.returncode == 0:
            cc.fail(f"an unquoted generation ({GENERATION_KEY}: 6) renders; it must fail the render — the apiserver refuses a non-string nodeSelector value only on apply")
        if f"kagent.substrateWorkerPool.template.nodeSelector.{GENERATION_KEY} is 6" not in r.stderr:
            cc.fail(f"the unquoted generation failed the render for the wrong reason:\n{r.stderr}")
        print(f"ok: an unquoted generation fails the render naming kagent.substrateWorkerPool.template.nodeSelector.{GENERATION_KEY}")

        # The rendered object: the kagent chart the range resolves to, with the
        # values the meta chart forwards for the pin.
        url, rng = source.split(" ", 1)
        version = fluxsemver.resolve(cc.registry_tags(url), rng)
        if not version:
            cc.fail(f"no published kagent chart satisfies {rng!r} at {url}")
        chart_dir = os.path.join(tmp, "kagent")
        resolved = cc.pull(url, version, chart_dir)
        forwarded_file = os.path.join(tmp, "forwarded.yaml")
        with open(forwarded_file, "w", encoding="utf-8") as f:
            f.write(forwarded)
        r = cc.run(["helm", "template", "kagent", f"{chart_dir}/kagent", "-n", "agent-platform", "-f", forwarded_file, *cc.API_VERSIONS])
        if r.returncode != 0:
            cc.fail(f"the kagent chart {resolved} rejects the values the meta chart forwards for the pin\n{r.stderr}")
        pools = [d for d in yaml.safe_load_all(r.stdout) if isinstance(d, dict) and d.get("kind") == "WorkerPool"]
        name = values["substrateWorkerPool"]["name"]
        if [p["metadata"]["name"] for p in pools] != [name]:
            cc.fail(f"the kagent chart {resolved} renders WorkerPools {[p['metadata']['name'] for p in pools]}; expected the one pool {name!r}")
        template = pools[0]["spec"].get("template") or {}
        if template.get("nodeSelector") != PIN:
            cc.fail(f"WorkerPool {name} of the kagent chart {resolved} carries nodeSelector {template.get('nodeSelector')!r}; the forwarded pin is {PIN!r}")
        if template.get("resources") != values["substrateWorkerPool"]["template"]["resources"]:
            cc.fail(f"WorkerPool {name} of the kagent chart {resolved} does not carry the forwarded resources: {template.get('resources')!r}")
        print(f"ok: the kagent chart {resolved} (the range {rng!r}) renders WorkerPool {name} with the pin verbatim in spec.template.nodeSelector, next to the forwarded resources")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    sys.exit(main(sys.argv[1]))
