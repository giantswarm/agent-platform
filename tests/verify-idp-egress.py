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
    if doc is None or IDP_HOSTS.findall(doc) != [DEX] or '- port: "10443"' not in doc or "- cluster" not in doc:
        fail(f"an MCP backend with identityProvider: {name} does not open {DEX} and the cluster entity on 443 and 10443\n{doc}")
    ok(f"an MCP backend with identityProvider: {name} opens {DEX} and the cluster entity on 443 and 10443")
    r = subprocess.run(base, capture_output=True, text=True)
    if r.returncode == 0 or "needs the entry's podSelector" not in r.stderr:
        fail(f"an MCP backend with identityProvider and no podSelector did not fail the render\n{r.stderr}")
    ok("an MCP backend with identityProvider and no podSelector fails the render")
    r = subprocess.run([*base, *selector, "--set", "networkPolicy.flavor=kubernetes"], capture_output=True, text=True)
    if r.returncode != 0 or name in r.stdout:
        fail(f"kubernetes flavour: the render failed or still has {name}\n{r.stderr}")
    ok("kubernetes flavour: an MCP backend's identityProvider renders nothing")
    return 0


if __name__ == "__main__":
    sys.exit(main())
