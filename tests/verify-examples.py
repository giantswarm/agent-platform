#!/usr/bin/env python3
"""Render every install example the way an installation gets it.

docs/install.md sends a newcomer to one of the cluster-shape examples under
helm/agent-platform/examples/ and to nothing else: the file, unchanged, is the
values of the first `helm install`. A meta-chart render alone cannot prove that
file installs: the meta chart forwards each component's block into a
HelmRelease, and the component chart (muster's OAuth guards, the connectivity
chart's route guards, the Substrate wiring) validates it on the cluster. So each
example is rendered with the meta chart, then every component the render turns
on is rendered with the values its HelmRelease carries, against the chart its
range resolves to today (the way Flux resolves it, tests/verify-components-charts.py)
and the connectivity chart from the working tree.

An example that needs a cluster-shape answer (a served API) says so in EXAMPLES
and is rendered with it, as the live cluster would answer. Network:
gsoci.azurecr.io (ghcr.io for the CloudNativePG chart).
"""

import importlib.util
import os
import pathlib
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location("components_charts", os.path.join(HERE, "verify-components-charts.py"))
cc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cc)

# example -> the --api-versions the cluster of that shape serves. Every example
# of the guide is listed here; a file under examples/ that is neither listed nor
# in OTHER fails, so a new example cannot skip the check.
EXAMPLES = {
    "kind-lab-dex.yaml": [],
    "own-gateway.yaml": ["--api-versions", "gateway.networking.k8s.io/v1"],
    "chart-owned-edge.yaml": ["--api-versions", "gateway.networking.k8s.io/v1"],
    "managed-cloud.yaml": ["--api-versions", "gateway.networking.k8s.io/v1"],
    # A workload cluster of the fleet: Kyverno (the Substrate PolicyExceptions)
    # and Cilium (the network policies of the Substrate hops).
    "runtime-slice.yaml": ["--api-versions", "kyverno.io/v1", "--api-versions", "cilium.io/v2"],
}
# Examples with a check of their own: the BOM (verify-components-charts) and the
# serving slice (verify-serving-slice).
OTHER = {"customer-bom.yaml", "serving-slice.yaml"}


def render(name: str, chart_dir: str, values: str, api_versions: list[str], what: str, tmp: str) -> None:
    """One `helm template` of a component chart with the values its HelmRelease
    carries, on the cluster shape the example names."""
    values_file = f"{tmp}/values-{name}.yaml"
    with open(values_file, "w", encoding="utf-8") as f:
        f.write(values)
    r = cc.run(["helm", "template", name, chart_dir, "-n", "agent-platform", "-f", values_file, *api_versions])
    if r.returncode != 0:
        cc.fail(f"{what} rejects the values the example gives it\n{r.stderr}")
    print(f"ok: {what} ({r.stdout.count(chr(10) + 'kind: ')} objects)")


def main(meta: str) -> int:
    examples = pathlib.Path(meta) / "examples"
    present = {p.name for p in examples.glob("*.yaml")}
    if unlisted := sorted(present - set(EXAMPLES) - OTHER):
        cc.fail(f"examples/{', examples/'.join(unlisted)} is checked by nothing: add it to EXAMPLES in {__file__}")
    if missing := sorted(set(EXAMPLES) - present):
        cc.fail(f"examples/{', examples/'.join(missing)} is listed but absent")
    components = cc.roster(meta)
    by_chart = {c["chart"]: n for n, c in components.items()}
    with tempfile.TemporaryDirectory() as tmp:
        for example, api_versions in EXAMPLES.items():
            print(f"--> examples/{example}")
            rendered = cc.docs(cc.render_meta(meta, ["-n", "agent-platform", "-f", str(examples / example), *api_versions]))
            on = sorted(n for kind, n in rendered if kind == "HelmRelease" and n in by_chart)
            if not on:
                cc.fail(f"examples/{example} renders no component HelmRelease")
            for chart in on:
                name = by_chart[chart]
                values = cc.hr_values(rendered[("HelmRelease", chart)])
                what = f"{name} for examples/{example}"
                if components[name].get("releasedWithChart"):
                    render(name, str(cc.REPO_ROOT / "helm" / chart), values, api_versions, f"{what} (working tree)", tmp)
                    continue
                url, rng = cc.source(rendered[("OCIRepository", chart)])
                version = cc.fluxsemver.resolve(cc.registry_tags(url), rng)
                if not version:
                    cc.fail(f"{what}: nothing published at {url} satisfies {rng!r}")
                chart_dir = f"{tmp}/{name}/{version}"
                if not os.path.isdir(f"{chart_dir}/{chart}"):
                    cc.pull(url, version, chart_dir)
                render(name, f"{chart_dir}/{chart}", values, api_versions, f"{what} ({version})", tmp)
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(f"usage: {sys.argv[0]} <meta chart dir>")
    sys.exit(main(sys.argv[1]))
