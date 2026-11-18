#!/usr/bin/env python3
"""Assert the identity provider egress of the services that validate tokens themselves (giantswarm/agent-platform#1169).

Every caller of agent-platform.idpEgress.cilium (the workspace-, cluster-, agent-,
vm- and model-manager egress policies and the portal's) opens the hosts its
identity provider serves discovery, JWKS, userinfo and the token endpoint from.
The issuer decides the list, never a provider value that defaults to dex:

- with global.identity.issuerUrl on accounts.google.com (any case, a trailing
  slash) each policy opens exactly accounts.google.com, www.googleapis.com
  (JWKS), openidconnect.googleapis.com (userinfo) and oauth2.googleapis.com
  (token) on 443;
- with a Dex issuer each opens that issuer's host alone, and no Google host.

The in-cluster leg (giantswarm/agent-platform#1383): no policy opens the cluster
entity on 443 or 10443. Each opens networkPolicy.identityProvider.inClusterPeers:
unset, the proxy pods of the Gateway muster's route attaches to
(ingress.parentRefs) on 443 and 10443, where a private installation's issuer is
at policy time, and an in-cluster issuer Service's namespace on its URL's port;
a list replaces them, [] renders none. A token-validating workload (the policies
above, the skill-manager's and the toolchain-manager's MCP backend pods) that an
egress policy selects has the identity provider rule in one of them, or its
egress default-deny cuts the login. The toolchain-manager's own chart renders no
policy today (giantswarm/agent-platform#1383, second comment): when it does, set
identityProvider on its MCP backend entry.

Usage: verify-idp-egress.py <connectivity chart dir>
Deliberately stdlib-only: the CI image has no PyYAML. HELM selects the binary.
"""

import os
import re
import subprocess
import sys

HELM = os.environ.get("HELM", "helm")
PREFIX = "agent-platform-connectivity"
COMPONENTS = ("workspace-manager", "cluster-manager", "agent-manager", "vm-manager", "model-manager", "backstage")
POLICIES = {f"{PREFIX}-{c}-egress" for c in COMPONENTS if c != "backstage"} | {f"{PREFIX}-backstage"}
GOOGLE = ["accounts.google.com", "www.googleapis.com", "openidconnect.googleapis.com", "oauth2.googleapis.com"]
DEX = "dex.ci.example.com"
IDP_HOSTS = re.compile(r'matchName: "?((?:[a-z0-9-]+\.)*(?:google\.com|googleapis\.com)|' + re.escape(DEX) + ')"?$', re.M)
FLAGS = [
    "--set", "networkPolicy.flavor=cilium",
    "--set", "global.gatewayApi.parentRefs[0].name=x",
    "--set", "global.domain=ci.example.com",
    "--set", "global.identity.clientId=platform",
    "--set", "global.identity.existingSecret=platform-oauth",
    "-f", "tests/fixtures/workspaces-values.yaml",
    "--set", "components.kagent.enabled=true",
    "--set", "kagent.database.postgres.urlFile=/etc/kagent-db/uri",
    *[x for c in COMPONENTS for x in ("--set", f"components.{c}.enabled=true")],
]


def fail(msg: str) -> None:
    sys.exit(f"FAIL: {msg}")


def ok(msg: str) -> None:
    print(f"ok: {msg}")


def idp_hosts(chart: str, issuer: str) -> dict:
    """The identity provider hosts each policy of POLICIES opens, by policy name."""
    r = subprocess.run([HELM, "template", "t", chart, *FLAGS, "--set", f"global.identity.issuerUrl={issuer}"], capture_output=True, text=True)
    if r.returncode != 0:
        fail(f"the render with issuer {issuer} failed\n{r.stderr}")
    out = {}
    for doc in r.stdout.split("\n---\n"):
        name = re.search(r"^  name: (\S+)", doc, re.M)
        if "\nkind: CiliumNetworkPolicy\n" in doc and name and name.group(1) in POLICIES:
            out[name.group(1)] = IDP_HOSTS.findall(doc)
    if set(out) != POLICIES:
        fail(f"issuer {issuer}: the egress policies rendered are {sorted(out)}, expected {sorted(POLICIES)}")
    return out


def render(chart: str, *extra: str) -> list:
    """The CiliumNetworkPolicy documents of a render with FLAGS and extra."""
    r = subprocess.run([HELM, "template", "t", chart, *FLAGS, *extra], capture_output=True, text=True)
    if r.returncode != 0:
        fail(f"the render with {extra} failed\n{r.stderr}")
    return [d for d in r.stdout.split("\n---\n") if "\nkind: CiliumNetworkPolicy\n" in d]


def doc_name(doc: str) -> str:
    return re.search(r"^  name: (\S+)", doc, re.M).group(1)


def selector(doc: str) -> dict:
    """The endpointSelector's matchLabels of a policy."""
    m = re.search(r"^  endpointSelector:\n    matchLabels:\n((?:      \S.*\n)+)", doc, re.M)
    return dict(re.match(r"\s+([^:]+): \"?([^\"]*)\"?$", l).groups() for l in m.group(1).splitlines()) if m else {}


def egress_rules(doc: str) -> list:
    """The egress rules of a policy, each as its text."""
    m = re.search(r"^  egress:\n((?:(?:    .*|\s*)\n?)+)", doc, re.M)
    if not m:
        return []
    return ["    - " + r for r in re.split(r"^    - ", m.group(1), flags=re.M)[1:]]


def ports(rule: str) -> set:
    return set(re.findall(r'- port: "?(\d+)"?', rule))


def endpoint_rules(rule_list: list, labels: dict) -> list:
    """The toEndpoints rules whose one selector is exactly these labels."""
    out = []
    for r in rule_list:
        if not r.startswith("    - toEndpoints:") or r.count("- matchLabels:") != 1:
            continue
        lines = [l for l in r.split("- matchLabels:\n", 1)[1].split("toPorts:")[0].splitlines() if l.strip()]
        pairs = [re.match(r"\s+([^:#\s]+): \"?([^\"]*)\"?$", l) for l in lines]
        if all(pairs) and dict(p.groups() for p in pairs) == labels:
            out.append(r)
    return out


def check_peers(chart: str) -> None:
    dex = ["--set", f"global.identity.issuerUrl=https://{DEX}"]
    backend = ["--set", "networkPolicy.mcpBackends.x.namespace=mcp-x", "--set", "networkPolicy.mcpBackends.x.ports[0]=8080",
               "--set", "networkPolicy.mcpBackends.x.podSelector.app\\.kubernetes\\.io/name=x",
               "--set", f"networkPolicy.mcpBackends.x.identityProvider.issuerUrl=https://{DEX}"]
    callers = POLICIES | {f"{PREFIX}-mcp-backend-x-idp-egress"}
    edge = ["--set", "ingress.parentRefs[0].name=front", "--set", "ingress.parentRefs[0].namespace=edge"]
    front = {"app.kubernetes.io/name": "envoy", "gateway.envoyproxy.io/owning-gateway-name": "front",
             "gateway.envoyproxy.io/owning-gateway-namespace": "edge", "io.kubernetes.pod.namespace": "envoy-gateway-system"}

    def by_name(docs: list) -> dict:
        out = {doc_name(d): egress_rules(d) for d in docs if doc_name(d) in callers}
        if set(out) != callers:
            fail(f"the egress policies rendered are {sorted(out)}, expected {sorted(callers)}")
        return out

    # A private installation: the issuer's name resolves to the Gateway muster's
    # route attaches to, which Cilium translates to its proxy pods on 10443.
    for name, rules in sorted(by_name(render(chart, *dex, *backend, *edge)).items()):
        for r in rules:
            if re.search(r"^        - cluster$", r, re.M) and ports(r) & {"443", "10443"}:
                fail(f"{name} opens the cluster entity on {sorted(ports(r))}:\n{r}")
        if not any(ports(r) == {"443", "10443"} for r in endpoint_rules(rules, front)):
            fail(f"{name} does not open the proxy pods of ingress.parentRefs' Gateway (front in edge) on 443 and 10443")
    ok("a private installation: every caller opens the proxy pods of the Gateway muster's route attaches to on 443 and 10443, and no caller the cluster entity on 443 or 10443")

    # An in-cluster issuer Service: its namespace, on the port of the URL.
    svc = ["--set", "global.identity.issuerUrl=http://dex.giantswarm.svc.cluster.local:5556",
           "--set", "networkPolicy.mcpBackends.x.identityProvider.issuerUrl=http://dex.giantswarm.svc.cluster.local:5556"]
    for name, rules in sorted(by_name(render(chart, *dex, *backend, *edge, *svc)).items()):
        if not any(ports(r) == {"5556"} for r in endpoint_rules(rules, {"io.kubernetes.pod.namespace": "giantswarm"})):
            fail(f"{name} does not open the issuer Service's namespace giantswarm on 5556 alone")
    ok("an in-cluster issuer Service: every caller opens its namespace on the URL's port, 5556")

    # An installation's own list replaces the derived peers; [] renders none.
    own = ["--set", "networkPolicy.identityProvider.inClusterPeers[0].namespace=ingress-nginx",
           "--set", "networkPolicy.identityProvider.inClusterPeers[0].matchLabels.app=nginx",
           "--set", "networkPolicy.identityProvider.inClusterPeers[0].ports[0]=8443"]
    nginx = {"app": "nginx", "io.kubernetes.pod.namespace": "ingress-nginx"}
    for name, rules in sorted(by_name(render(chart, *dex, *backend, *edge, *own)).items()):
        if not any(ports(r) == {"8443"} for r in endpoint_rules(rules, nginx)):
            fail(f"{name} does not open the listed peer (app=nginx in ingress-nginx) on 8443")
        if name != f"{PREFIX}-backstage" and endpoint_rules(rules, front):
            fail(f"{name} still opens the derived Gateway peer beside an installation's own list")
    ok("inClusterPeers set: every caller opens the listed peer on its port, and no derived Gateway peer")
    for name, rules in sorted(by_name(render(chart, *dex, *backend, *edge, "--set-json", "networkPolicy.identityProvider.inClusterPeers=[]")).items()):
        if name != f"{PREFIX}-backstage" and any(r.startswith("    - toEndpoints:") and "envoy" in r for r in rules):
            fail(f"{name} opens an Envoy peer with inClusterPeers: []")
    ok("inClusterPeers: []: no caller opens an in-cluster identity provider peer")


def check_guard(chart: str) -> None:
    """Every token-validating workload that an egress policy selects has the
    identity provider rule (the issuer by name) in one of the policies that
    select it: Cilium egress policies are additive, and one that selects the
    pods puts them in egress default-deny."""
    # The MCP backend entries the meta chart forwards (agent-platform.skillManager.mcpBackend,
    # agent-platform.toolchainManager.mcpBackend).
    entries = ["--set", "networkPolicy.mcpBackends.skill-manager.namespace=agent-platform",
               "--set", "networkPolicy.mcpBackends.skill-manager.ports[0]=8080",
               "--set", "networkPolicy.mcpBackends.skill-manager.podSelector.app\\.kubernetes\\.io/name=skill-manager",
               "--set", f"networkPolicy.mcpBackends.skill-manager.identityProvider.issuerUrl=https://{DEX}",
               "--set", "networkPolicy.mcpBackends.toolchain-manager.namespace=agent-platform",
               "--set", "networkPolicy.mcpBackends.toolchain-manager.ports[0]=8080",
               "--set", "networkPolicy.mcpBackends.toolchain-manager.podSelector.app\\.kubernetes\\.io/name=toolchain-manager"]
    docs = render(chart, "--set", f"global.identity.issuerUrl=https://{DEX}", *entries)
    validators = {doc_name(d): selector(d) for d in docs if doc_name(d) in POLICIES}
    validators["skill-manager"] = {"app.kubernetes.io/name": "skill-manager"}
    validators["toolchain-manager"] = {"app.kubernetes.io/name": "toolchain-manager"}
    for who, pods in sorted(validators.items()):
        egress = [d for d in docs if egress_rules(d) and selector(d) and set(selector(d).items()) <= set(pods.items())]
        if egress and not any(IDP_HOSTS.findall(d) == [DEX] for d in egress):
            fail(f"{who}: the egress policies that select its pods ({', '.join(doc_name(d) for d in egress)}) have no identity provider rule")
    ok(f"every token-validating workload an egress policy selects has the identity provider rule: {', '.join(sorted(validators))}")


def main() -> int:
    chart = sys.argv[1]
    for issuer in ("https://accounts.google.com", "https://Accounts.Google.com/"):
        for name, hosts in sorted(idp_hosts(chart, issuer).items()):
            if hosts != GOOGLE:
                fail(f"issuer {issuer}: {name} opens {hosts}, expected {GOOGLE}")
    ok(f"a Google issuer: {', '.join(sorted(POLICIES))} each open {', '.join(GOOGLE)} on 443")
    for name, hosts in sorted(idp_hosts(chart, f"https://{DEX}").items()):
        if hosts != [DEX]:
            fail(f"a Dex issuer: {name} opens {hosts}, expected [{DEX!r}] alone")
    ok(f"a Dex issuer: each policy opens {DEX} alone and no Google host")

    # An MCP backend entry with identityProvider (the skill-manager's, from the meta chart):
    # its pods get the same rules, cilium only, and never a whole namespace.
    entry = ["--set", "networkPolicy.mcpBackends.x.namespace=mcp-x", "--set", "networkPolicy.mcpBackends.x.ports[0]=8080",
             "--set", f"networkPolicy.mcpBackends.x.identityProvider.issuerUrl=https://{DEX}"]
    selector = ["--set", "networkPolicy.mcpBackends.x.podSelector.app\\.kubernetes\\.io/name=x"]
    base = [HELM, "template", "t", chart, *FLAGS, "--set", f"global.identity.issuerUrl=https://{DEX}", *entry]
    name = f"{PREFIX}-mcp-backend-x-idp-egress"
    r = subprocess.run([*base, *selector], capture_output=True, text=True)
    if r.returncode != 0:
        fail(f"an MCP backend with identityProvider: the render failed\n{r.stderr}")
    doc = next((d for d in r.stdout.split("\n---\n") if re.search(rf"^  name: {re.escape(name)}$", d, re.M)), None)
    if doc is None or IDP_HOSTS.findall(doc) != [DEX]:
        fail(f"an MCP backend with identityProvider: {name} does not open {DEX}\n{doc}")
    ok(f"an MCP backend with identityProvider: {name} opens {DEX}")
    r = subprocess.run(base, capture_output=True, text=True)
    if r.returncode == 0 or "needs the entry's podSelector" not in r.stderr:
        fail(f"an MCP backend with identityProvider and no podSelector did not fail the render\n{r.stderr}")
    ok("an MCP backend with identityProvider and no podSelector fails the render")
    r = subprocess.run([*base, *selector, "--set", "networkPolicy.flavor=kubernetes"], capture_output=True, text=True)
    if r.returncode != 0 or name in r.stdout:
        fail(f"kubernetes flavour: the render failed or still has {name}\n{r.stderr}")
    ok("kubernetes flavour: an MCP backend's identityProvider renders nothing")
    check_peers(chart)
    check_guard(chart)
    return 0


if __name__ == "__main__":
    sys.exit(main())
