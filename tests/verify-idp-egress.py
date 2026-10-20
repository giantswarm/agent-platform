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
    return 0


if __name__ == "__main__":
    sys.exit(main())
