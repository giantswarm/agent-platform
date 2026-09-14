#!/usr/bin/env python3
"""Tie the connectivity chart's egress rules to kagent's built-in tool server to
the namespace and port the kagent chart renders the server into.

The kagent chart (`components.kagent`, the line's chart) renders the kagent-tools
subchart's Deployment and Services into `kagent.kagent-tools.namespaceOverride`,
else its release namespace, and composes the `kagent-tool-server` RemoteMCPServer
URL from the same inputs. The connectivity chart opens the kagent controller's
egress (tool discovery) and the actors' egress through Substrate's egress gateway
to that server under Cilium's default-deny (templates/kagent/netpol.yaml,
templates/substrate/netpol.yaml). Three places, one namespace: a rule that names
another one leaves the controller's discovery in a timeout and the RemoteMCPServer
never Accepted (giantswarm/agent-platform#421 — the rule said `kagent` while the
subchart rendered into the release namespace).

The check follows the meta chart's wiring instead of re-deriving it: it renders
the meta chart with the tool server on, takes the kagent HelmRelease's values and
target namespace and renders the kagent chart the range resolves to with them
(pulled from the line's registry the way tests/verify-components-charts.py does)
— the tools Deployment's namespace, the RemoteMCPServer's URL — then renders this
checkout's connectivity chart with the connectivity HelmRelease's values and
target namespace and compares the `io.kubernetes.pod.namespace` and port next to
`app.kubernetes.io/name: kagent-tools` in both egress policies with them. Twice:
with the meta chart's defaults (the server in the kagent namespace with the rest
of kagent) and with `kagent.kagent-tools.namespaceOverride` unset, where both
charts fall back to the release namespace — the shape that broke.

Network: ghcr.io (the kagent line's chart). Deliberately stdlib-only: the CI
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

TOOLS_LABEL = "app.kubernetes.io/name: kagent-tools"
# The two egress policies that open the server: the controller's, the actors'.
POLICIES = ("agent-platform-connectivity-kagent-controller-egress", "substrate-atenet-egress")
TOOLS_ON = [
    "--set", "components.kagent.enabled=true", "--set", "kagent.kagent-tools.enabled=true",
    # The connectivity chart's routes need the cluster's public Gateway; the
    # meta chart forwards it through `global` (an installation input).
    "--set-json", 'global.gatewayApi.parentRefs=[{"name": "public", "namespace": "envoy-gateway-system"}]',
]
RELEASE_NAMESPACE = "agent-platform"
# (label, extra meta flags, the namespace the server must end up in)
CASES = (
    ("the meta chart's defaults", [], "kagent"),
    ("kagent.kagent-tools.namespaceOverride unset — both charts fall back to the release namespace",
     ["--set", "kagent.kagent-tools.namespaceOverride=null"], RELEASE_NAMESPACE),
)


def fail(msg: str) -> None:
    sys.exit(f"FAIL: {msg}")


def target_namespace(hr: str) -> str:
    return re.search(r"^  targetNamespace: (\S+)", hr, re.M).group(1)


def write(directory: str, name: str, content: str) -> str:
    path = os.path.join(directory, name)
    with open(path, "w") as f:
        f.write(content)
    return path


def rendered_server(manifest: str) -> tuple[str, str, str]:
    """The kagent chart's side: the tools Deployment's namespace and the
    RemoteMCPServer URL's namespace and port."""
    objects = cc.docs(manifest)
    deployments = [d for (kind, _), d in objects.items() if kind == "Deployment" and re.search(rf"^    {re.escape(TOOLS_LABEL)}$", d, re.M)]
    if len(deployments) != 1:
        fail(f"the kagent chart renders {len(deployments)} Deployments labelled {TOOLS_LABEL}, not one")
    deployment_ns = re.search(r"^  namespace: (\S+)", deployments[0], re.M).group(1)
    servers = [d for (kind, name), d in objects.items() if kind == "RemoteMCPServer" and name.endswith("-tool-server")]
    if len(servers) != 1:
        fail(f"the kagent chart renders {len(servers)} *-tool-server RemoteMCPServers, not one")
    url = re.search(r'^  url: "?([^"\s]+)"?', servers[0], re.M).group(1)
    m = re.fullmatch(r"http://[^.]+\.([^:/]+):(\d+)/mcp", url)
    if not m:
        fail(f"the RemoteMCPServer URL {url!r} is not http://<service>.<namespace>:<port>/mcp")
    return deployment_ns, m.group(1), m.group(2)


def rule_target(manifest: str, policy: str) -> tuple[str, str]:
    """The connectivity chart's side: the namespace and port the policy's
    kagent-tools rule names."""
    doc = cc.docs(manifest).get(("CiliumNetworkPolicy", policy))
    if doc is None:
        fail(f"no CiliumNetworkPolicy {policy} in the connectivity render")
    m = re.search(
        rf"^ +{re.escape(TOOLS_LABEL)}\n +io\.kubernetes\.pod\.namespace: (\S+)\n +toPorts:\n +- ports:\n +- port: \"(\d+)\"",
        doc, re.M,
    )
    if not m:
        fail(f"{policy} carries no egress rule to the kagent-tools pods, or the rule's shape moved:\n{doc}")
    return m.group(1), m.group(2)


def check(meta: str, connectivity: str, kagent_chart: str, label: str, flags: list[str], expected_ns: str) -> None:
    # `-n`: the meta chart's release namespace, which every platform HelmRelease
    # targets (gitops.targetNamespace unset); the cilium API served so the
    # forwarded networkPolicy.flavor resolves to cilium.
    rendered = cc.docs(cc.render_meta(meta, ["-n", RELEASE_NAMESPACE, *cc.QUICKSTART, *cc.API_VERSIONS, *TOOLS_ON, *flags]))
    kagent_hr, connectivity_hr = rendered[("HelmRelease", "kagent")], rendered[("HelmRelease", "agent-platform-connectivity")]
    with tempfile.TemporaryDirectory() as d:
        r = cc.run(["helm", "template", "kagent", kagent_chart, "-n", target_namespace(kagent_hr),
                    "-f", write(d, "kagent.yaml", cc.hr_values(kagent_hr)), *cc.API_VERSIONS])
        if r.returncode != 0:
            fail(f"the kagent chart rejects the values the meta chart forwards ({label})\n{r.stderr}")
        deployment_ns, url_ns, url_port = rendered_server(r.stdout)
        r = cc.run(["helm", "template", "agent-platform-connectivity", connectivity, "-n", target_namespace(connectivity_hr),
                    "-f", write(d, "connectivity.yaml", cc.hr_values(connectivity_hr)), *cc.API_VERSIONS])
        if r.returncode != 0:
            fail(f"the connectivity chart rejects the values the meta chart forwards ({label})\n{r.stderr}")
        connectivity_render = r.stdout
    if deployment_ns != url_ns:
        fail(f"the kagent chart disagrees with itself ({label}): the kagent-tools Deployment is in {deployment_ns}, the RemoteMCPServer URL names {url_ns}")
    if deployment_ns != expected_ns:
        fail(f"{label}: the kagent chart renders the tool server into {deployment_ns}, expected {expected_ns}")
    for policy in POLICIES:
        rule_ns, rule_port = rule_target(connectivity_render, policy)
        if (rule_ns, rule_port) != (deployment_ns, url_port):
            fail(f"{label}: {policy} opens the tool server in {rule_ns}:{rule_port}, but the kagent chart renders it into "
                 f"{deployment_ns} and the RemoteMCPServer dials port {url_port} — the controller's discovery would time out (#421)")
    print(f"ok ({label}): kagent-tools Deployment and RemoteMCPServer URL in {deployment_ns}:{url_port}; "
          f"{' and '.join(POLICIES)} open exactly that")


def main(meta: str, connectivity: str) -> int:
    source = cc.docs(cc.render_meta(meta, [*cc.QUICKSTART, *TOOLS_ON]))[("OCIRepository", "kagent")]
    url, constraint = cc.source(source)
    tags = cc.registry_tags(url)
    floor = constraint.split()[0].lstrip(">=")
    version = cc.fluxsemver.resolve(tags, constraint) or cc.fallback("kagent", constraint, tags, floor)
    with tempfile.TemporaryDirectory() as d:
        resolved = cc.pull(url, version, d)
        print(f"kagent chart {resolved} ({url}, the range {constraint!r})")
        for label, flags, expected_ns in CASES:
            check(meta, connectivity, f"{d}/kagent", label, flags, expected_ns)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2]))
