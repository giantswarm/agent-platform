#!/usr/bin/env python3
"""Assert muster's actor bearer follows Substrate, not the workspaces switch (giantswarm/giantswarm#38993).

With kagent on (Substrate follows it), muster's OAuth server on and workspaces
off, every Session agent-manager starts is bound, so its MCP calls reach muster
with its Actor's JWT instead of the person's forwarded token:

- the muster release's credentialProvider is on with actorBearer on and
  Substrate's issuer, and releases nothing (no releasable, no gitUsers);
- the agent-manager release gets muster.actorBearer: true;
- the kagent release lists the actor-JWT provider muster (muster's in-cluster
  host, muster's audience).

An own muster.muster.credentialProvider.actorBearer.enabled: false (passed to
muster as written), muster's OAuth server off or kagent off derive none of the
three.

Usage: verify-actor-bearer.py <meta chart dir>
Deliberately stdlib-only: the CI image has no PyYAML. HELM selects the binary.
"""

import os
import re
import subprocess
import sys

HELM = os.environ.get("HELM", "helm")
AUDIENCE = "https://muster.ci.example.com"
ISSUER = "https://idp.ate-system.svc"
BASE = [
    "--set", "global.identity.issuerUrl=https://dex.ci.example.com",
    "--set", "components.kagent.enabled=true",
    "--set", "components.agent-manager.enabled=true",
    "--set", "kagent.harness.snapshotLocation=s3://ci-agent-snapshots/agents",
    "--set", "kagent.database.postgres.urlFile=/etc/kagent-db/uri",
    "--set", f"muster.muster.oauth.server.baseUrl={AUDIENCE}",
]


def fail(msg: str) -> None:
    sys.exit(f"FAIL: {msg}")


def ok(msg: str) -> None:
    print(f"ok: {msg}")


def releases(chart: str, *flags: str) -> dict:
    """The HelmRelease documents of the render, by metadata.name."""
    r = subprocess.run([HELM, "template", "t", chart, *BASE, *flags], capture_output=True, text=True)
    if r.returncode != 0:
        fail(f"the render with {' '.join(flags) or 'the base flags'} failed\n{r.stderr}")
    out = {}
    for doc in r.stdout.split("\n---\n"):
        name = re.search(r"^  name: (\S+)", doc, re.M)
        if "\nkind: HelmRelease\n" in doc and name:
            out[name.group(1)] = doc
    for want in ("muster", "agent-manager", "kagent"):
        if want not in out:
            fail(f"no HelmRelease {want} in the render with {' '.join(flags) or 'the base flags'}")
    return out


def block(doc: str, key: str) -> str:
    """The lines of the first mapping `key:` in doc and everything indented below it."""
    lines = doc.splitlines()
    for i, line in enumerate(lines):
        if line.strip() == f"{key}:":
            indent = len(line) - len(line.lstrip())
            body = [line]
            for nxt in lines[i + 1:]:
                if nxt.strip() and len(nxt) - len(nxt.lstrip()) <= indent:
                    break
                body.append(nxt)
            return "\n".join(body)
    return ""


def assert_bound(rels: dict) -> None:
    provider = block(rels["muster"], "credentialProvider")
    if not provider:
        fail(f"workspaces off: the muster release has no credentialProvider\n{rels['muster']}")
    bearer = block(provider, "actorBearer")
    if not re.search(r"^\s+enabled: true$", provider, re.M) or "enabled: true" not in bearer or f"issuer: {ISSUER}" not in bearer:
        fail(f"workspaces off: muster's credentialProvider is not on with actorBearer on and issuer {ISSUER}\n{provider}")
    for key in ("releasable", "gitUsers"):
        if block(provider, key):
            fail(f"workspaces off: muster's credentialProvider carries {key}, it must release nothing\n{provider}")
    if not re.search(r"^\s+actorBearer: true$", block(rels["agent-manager"], "muster"), re.M):
        fail(f"workspaces off: the agent-manager release lacks muster.actorBearer: true\n{rels['agent-manager']}")
    jwt = block(block(rels["kagent"], "actorJWTProviders"), "muster")
    if f"audience: {AUDIENCE}" not in jwt or not re.search(r"hostname: muster\.", jwt):
        fail(f"workspaces off: the kagent release does not list the actor-JWT provider muster for {AUDIENCE}\n{rels['kagent']}")


def assert_unbound(rels: dict, why: str) -> None:
    provider = block(rels["muster"], "credentialProvider")
    if "enabled: true" in provider or "gatewayIdentity:" in provider:
        fail(f"{why}: the muster release still derives a credentialProvider\n{provider}")
    if re.search(r"^\s+actorBearer: true$", rels["agent-manager"], re.M):
        fail(f"{why}: the agent-manager release still has muster.actorBearer: true")
    if block(rels["kagent"], "actorJWTProviders"):
        fail(f"{why}: the kagent release still lists actor-JWT providers")


def main() -> int:
    chart = sys.argv[1]
    assert_bound(releases(chart))
    ok("workspaces off, Substrate on: muster serves the actor bearer and releases nothing, agent-manager binds, kagent lists muster's actor-JWT provider")
    assert_unbound(releases(chart, "--set", "muster.muster.credentialProvider.actorBearer.enabled=false"), "an own actorBearer.enabled false")
    ok("an own muster.muster.credentialProvider.actorBearer.enabled false turns the three off")
    assert_unbound(releases(chart, "--set", "muster.muster.oauth.server.enabled=false"), "muster's OAuth server off")
    ok("muster's OAuth server off renders none of the three")
    r = subprocess.run([HELM, "template", "t", chart], capture_output=True, text=True)
    if r.returncode != 0:
        fail(f"the default render failed\n{r.stderr}")
    for doc in r.stdout.split("\n---\n"):
        name = re.search(r"^  name: (\S+)", doc, re.M)
        if "\nkind: HelmRelease\n" in doc and name and name.group(1) in ("muster", "agent-manager"):
            if "gatewayIdentity:" in doc or re.search(r"^\s+actorBearer: true$", doc, re.M):
                fail(f"kagent off (the default): the {name.group(1)} release derives a credentialProvider or actorBearer")
    ok("kagent off (the default): muster and agent-manager derive no credentialProvider, no actorBearer")
    return 0


if __name__ == "__main__":
    sys.exit(main())
