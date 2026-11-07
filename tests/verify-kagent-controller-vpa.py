#!/usr/bin/env python3
"""Tie the connectivity chart's VerticalPodAutoscaler on the kagent controller
(templates/kagent/controller-vpa.yaml) to the kagent chart: the network policies
select pods by the labels the kagent chart sets, but the VPA names the Deployment
and the container. A rename in the kagent line would leave the render green and
the VPA targeting nothing, so the VPA's targetRef name, namespace and
containerName must be the rendered controller Deployment's.

The check follows the meta chart's wiring instead of re-deriving it: it renders
the meta chart, takes the kagent HelmRelease's values and target namespace and
renders the kagent chart the range resolves to with them (pulled from the line's
registry the way tests/verify-components-charts.py does), then renders this
checkout's connectivity chart with the connectivity HelmRelease's values and
target namespace and compares the two.

Network: gsoci.azurecr.io (the kagent line's chart). Deliberately stdlib-only: the CI
image has no PyYAML.
"""

import importlib.util
import os
import re
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location("components_charts", os.path.join(HERE, "verify-components-charts.py"))
cc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cc)

KAGENT_ON = [
    "--set", "components.kagent.enabled=true", "--set", "kagent.database.postgres.urlFile=/etc/kagent-db/uri",
    # The connectivity chart's routes need the cluster's public Gateway; the
    # meta chart forwards it through `global` (an installation input).
    "--set-json", 'global.gatewayApi.parentRefs=[{"name": "public", "namespace": "envoy-gateway-system"}]',
]
RELEASE_NAMESPACE = "agent-platform"


def fail(msg: str) -> None:
    sys.exit(f"FAIL: {msg}")


def target_namespace(hr: str) -> str:
    return re.search(r"^  targetNamespace: (\S+)", hr, re.M).group(1)


def write(directory: str, name: str, content: str) -> str:
    path = os.path.join(directory, name)
    with open(path, "w") as f:
        f.write(content)
    return path


def rendered_controller(manifest: str) -> tuple[str, str, str]:
    """The kagent chart's side: the controller Deployment's name, namespace and
    its one container's name."""
    objects = cc.docs(manifest)
    deployments = [(name, d) for (kind, name), d in objects.items()
                   if kind == "Deployment" and re.search(r"^    app\.kubernetes\.io/component: controller$", d, re.M)]
    if len(deployments) != 1:
        fail(f"the kagent chart renders {len(deployments)} Deployments labelled app.kubernetes.io/component: controller, not one")
    name, doc = deployments[0]
    namespace = re.search(r"^  namespace: (\S+)", doc, re.M).group(1)
    spec = doc[doc.index("\n      containers:\n"):]
    containers = re.findall(r"^        - name: (\S+)$", spec, re.M)
    if len(containers) != 1:
        fail(f"the kagent controller Deployment {name} has {len(containers)} containers ({containers}), the VPA's container policy names one")
    return name, namespace, containers[0]


def vpa_target(manifest: str) -> tuple[str, str, str]:
    """The connectivity chart's side: the one VerticalPodAutoscaler's targetRef
    name, namespace and containerName."""
    vpas = [d for (kind, _), d in cc.docs(manifest).items() if kind == "VerticalPodAutoscaler"]
    if len(vpas) != 1:
        fail(f"the connectivity chart renders {len(vpas)} VerticalPodAutoscalers, not one")
    doc = vpas[0]
    namespace = re.search(r"^  namespace: (\S+)", doc, re.M).group(1)
    m = re.search(r"^  targetRef:\n    apiVersion: apps/v1\n    kind: Deployment\n    name: (\S+)$", doc, re.M)
    if not m:
        fail(f"the VerticalPodAutoscaler does not target an apps/v1 Deployment by name:\n{doc}")
    containers = re.findall(r"^      - containerName: (\S+)$", doc, re.M)
    if len(containers) != 1:
        fail(f"the VerticalPodAutoscaler carries {len(containers)} container policies, not one:\n{doc}")
    return m.group(1), namespace, containers[0]


def check(meta: str, connectivity: str, kagent_chart: str) -> None:
    # `-n`: the meta chart's release namespace, which every platform HelmRelease
    # targets (gitops.targetNamespace unset); the cilium API served so the
    # forwarded networkPolicy.flavor resolves to cilium.
    rendered = cc.docs(cc.render_meta(meta, ["-n", RELEASE_NAMESPACE, *cc.QUICKSTART, *cc.API_VERSIONS, *KAGENT_ON]))
    kagent_hr, connectivity_hr = rendered[("HelmRelease", "kagent")], rendered[("HelmRelease", "agent-platform-connectivity")]
    with tempfile.TemporaryDirectory() as d:
        r = cc.run(["helm", "template", "kagent", kagent_chart, "-n", target_namespace(kagent_hr),
                    "-f", write(d, "kagent.yaml", cc.hr_values(kagent_hr)), *cc.API_VERSIONS])
        if r.returncode != 0:
            fail(f"the kagent chart rejects the values the meta chart forwards\n{r.stderr}")
        controller = rendered_controller(r.stdout)
        r = cc.run(["helm", "template", "agent-platform-connectivity", connectivity, "-n", target_namespace(connectivity_hr),
                    "-f", write(d, "connectivity.yaml", cc.hr_values(connectivity_hr)), *cc.API_VERSIONS])
        if r.returncode != 0:
            fail(f"the connectivity chart rejects the values the meta chart forwards\n{r.stderr}")
        connectivity_render = r.stdout
    target = vpa_target(connectivity_render)
    if target != controller:
        fail(f"the VerticalPodAutoscaler targets Deployment {target[0]} in {target[1]}, container {target[2]}, "
             f"but the kagent chart renders the controller as Deployment {controller[0]} in {controller[1]}, container {controller[2]} — the VPA would target nothing")
    print(f"ok: the controller VPA targets Deployment {controller[0]} in {controller[1]}, container {controller[2]} — the kagent chart's")


def main(meta: str, connectivity: str) -> int:
    source = cc.docs(cc.render_meta(meta, [*cc.QUICKSTART, *KAGENT_ON]))[("OCIRepository", "kagent")]
    url, constraint = cc.source(source)
    tags = cc.registry_tags(url)
    floor = constraint.split()[0].lstrip(">=")
    version = cc.fluxsemver.resolve(tags, constraint) or cc.fallback("kagent", url, constraint, tags, floor)
    with tempfile.TemporaryDirectory() as d:
        resolved = cc.pull(url, version, d)
        print(f"kagent chart {resolved} ({url}, the range {constraint!r})")
        check(meta, connectivity, f"{d}/kagent")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2]))
