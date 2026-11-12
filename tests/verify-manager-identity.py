#!/usr/bin/env python3
"""Assert the managers' OAuth inputs follow muster's login (giantswarm/agent-platform#484).

The meta chart fills what a manager's oauth block and global.identity leave
unset from muster.muster.oauth.server (agent-platform.identity.apply):
oauth.dex.issuerURL, oauth.dex.clientID with oauth.trustedAudiences, and
oauth.existingSecret for model-manager, agent-manager, vm-manager,
cluster-manager and skill-manager; oauth.baseURL for the two routed managers.
The connectivity release receives the same filled blocks (not skill-manager's,
which only its own release reads), so each case renders the meta chart, then
the connectivity chart with the values its HelmRelease carries — where the
absence guard and the one-login guard fire.

Usage: verify-manager-identity.py <meta chart> <connectivity chart> [helm flags...]
The helm flags (the Makefile's VM: API versions, required inputs) go to both renders.
"""

import re
import subprocess
import sys
import tempfile

import yaml

MANAGERS = ("model-manager", "agent-manager", "vm-manager", "cluster-manager", "skill-manager")
# Blocks the connectivity release does not receive (components.agent-platform-connectivity.omitKeys).
NOT_IN_CONNECTIVITY = ("skill-manager",)
# Components with an oauth block that this script does not render, and where they are covered.
EXEMPT = {"workspace-manager": "needs the workspaces fixture; verify-workspace-manager and verify-idp-egress cover it"}
ROUTED = {"model-manager": "/model-manager", "agent-manager": "/agent-manager"}
ISSUER = "https://dex.ci.example.com"
CLIENT = "muster-acme"
SECRET = "muster-oauth"
# agent-platform.idpEgress.cilium for a Dex issuer: its host by name, and the cluster entity on the
# ports an in-cluster Gateway serves it on (a private installation's Dex resolves there).
IDP_EGRESS = [
    {"toFQDNs": [{"matchName": "dex.ci.example.com"}], "toPorts": [{"ports": [{"port": "443", "protocol": "TCP"}]}]},
    {"toEntities": ["cluster"], "toPorts": [{"ports": [{"port": "443", "protocol": "TCP"}, {"port": "10443", "protocol": "TCP"}]}]},
]


def sets(*pairs: str) -> list[str]:
    return [flag for pair in pairs for flag in ("--set", pair)]


# muster's login configured and nothing else: no global.identity, no manager oauth value.
# Without an issuer the line's own controller runs, not the kagent-extensions binary.
LOGIN = sets(
    "kagent.controller.image.repository=giantswarm/kagent/controller",
    f"muster.muster.oauth.server.dex.issuerUrl={ISSUER}",
    f"muster.muster.oauth.server.dex.clientId={CLIENT}",
    f"muster.muster.oauth.server.existingSecret={SECRET}",
)
ALL_ON = sets(
    "components.agentgateway.enabled=true",
    "components.kagent.enabled=true",
    "kagent.database.postgres.urlFile=/etc/kagent-db/uri",
    "components.agent-manager.enabled=true",
    "components.vm-manager.enabled=true",
    "components.cluster-manager.enabled=true",
    "global.domain=ci.example.com",
)


def helm(chart: str, flags: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["helm", "template", "t", chart, "--namespace", "agent-platform", *flags],
        capture_output=True, text=True, check=False,
    )


def releases(meta: str, flags: list[str]) -> dict[str, dict]:
    result = helm(meta, flags)
    if result.returncode != 0:
        sys.exit(f"FAIL: meta render {' '.join(flags)} failed\n{result.stderr}")
    return {
        d["metadata"]["name"]: d["spec"].get("values", {})
        for d in yaml.safe_load_all(result.stdout)
        if d and d.get("kind") == "HelmRelease"
    }


def connectivity(conn: str, values: dict, base: list[str]) -> subprocess.CompletedProcess:
    with tempfile.NamedTemporaryFile("w", suffix=".yaml") as f:
        yaml.safe_dump(values, f)
        f.flush()
        return helm(conn, [*base, "-f", f.name])


def oauth(rels: dict[str, dict], name: str) -> dict:
    if name not in rels:
        sys.exit(f"FAIL: no {name} HelmRelease in the render")
    return rels[name].get("oauth", {})


def expect(what: str, got, want) -> None:
    if got != want:
        sys.exit(f"FAIL: {what}: got {got!r}, want {want!r}")


def roster_covered(meta: str) -> None:
    """Every roster component whose forwarded block has an oauth block is in the helper's list.

    A manager that is an OAuth resource server and is not in the list gets no client
    from muster's login, and its chart refuses to render where global.identity.clientId
    is unset (skill-manager on enigma). A component that derives its login itself
    (toolchain-manager.auth, agent-platform.toolchainManager.derived) has no oauth block.
    """
    with open(f"{meta}/values.yaml") as f:
        values = yaml.safe_load(f)
    with open(f"{meta}/templates/_helpers.tpl") as f:
        helpers = f.read()
    line = re.search(r"\{\{- range \$name, \$wiring := dict (.*?) -\}\}", helpers)
    if not line:
        sys.exit("FAIL: roster: the agent-platform.identity.apply list is not found in _helpers.tpl")
    pairs = line.group(1)
    if re.sub(r'"[^"]+"\s+"[^"]*"', "", pairs).strip():
        sys.exit(f"FAIL: roster: the agent-platform.identity.apply list is not pairs of quoted strings: {pairs}")
    listed = set(re.findall(r'"([^"]+)"\s+"[^"]*"', pairs))
    # The helper reads the top-level block (index $v $name): the component's valuesFrom key.
    with_oauth = {
        c["valuesFrom"] for c in values["components"].values()
        if isinstance(values.get(c.get("valuesFrom") or ""), dict)
        and isinstance(values[c["valuesFrom"]].get("oauth"), dict)
    }
    missing = sorted(with_oauth - listed)
    if missing:
        sys.exit(f"FAIL: roster: {missing} forward an oauth block but are not in agent-platform.identity.apply's list")
    if not set(MANAGERS) <= with_oauth:
        sys.exit(f"FAIL: roster: {sorted(set(MANAGERS) - with_oauth)} have no oauth block in values.yaml")
    # Every one of them is rendered below (its client and its identity-provider egress), or exempt with a reason.
    unchecked = sorted(with_oauth - set(MANAGERS) - set(EXEMPT))
    if unchecked:
        sys.exit(f"FAIL: roster: {unchecked} forward an oauth block but are not in MANAGERS: add them, so their login and identity-provider egress are checked")


def idp_rules(docs: list[dict], name: str) -> list[dict]:
    """The identity-provider egress rules (toFQDNs, the cluster entity) of the Cilium policies selecting <name>'s pods."""
    rules = []
    for d in docs:
        if d.get("kind") != "CiliumNetworkPolicy":
            continue
        if d["spec"].get("endpointSelector", {}).get("matchLabels", {}).get("app.kubernetes.io/name") != name:
            continue
        rules += [r for r in d["spec"].get("egress", []) if "toFQDNs" in r or "cluster" in r.get("toEntities", [])]
    return rules


def main() -> None:
    meta, conn, base = sys.argv[1], sys.argv[2], sys.argv[3:]

    print("--> roster: every component that forwards an oauth block takes muster's login")
    roster_covered(meta)
    print("ok: roster")

    print("--> derived: muster's login fills every manager; the routed ones get the route's base URL")
    rels = releases(meta, [*base, *ALL_ON, *LOGIN])
    for name in MANAGERS:
        o = oauth(rels, name)
        dex = o.get("dex", {})
        expect(f"{name} oauth.dex.issuerURL", dex.get("issuerURL"), ISSUER)
        expect(f"{name} oauth.dex.clientID", dex.get("clientID"), CLIENT)
        expect(f"{name} oauth.existingSecret", o.get("existingSecret"), SECRET)
        expect(f"{name} oauth.trustedAudiences", o.get("trustedAudiences"), [CLIENT])
        want = f"https://agentgateway.ci.example.com{ROUTED[name]}" if name in ROUTED else None
        expect(f"{name} oauth.baseURL", o.get("baseURL"), want)
    cvals = rels["agent-platform-connectivity"]
    for name in MANAGERS:
        if name not in NOT_IN_CONNECTIVITY:
            expect(f"the connectivity release's {name}.oauth", cvals.get(name, {}).get("oauth"), oauth(rels, name))
    result = connectivity(conn, cvals, base)
    if result.returncode != 0:
        sys.exit(f"FAIL: derived: the connectivity render failed\n{result.stderr}")
    egress = next(
        (d for d in yaml.safe_load_all(result.stdout)
         if d and d.get("kind") == "CiliumNetworkPolicy"
         and d["metadata"]["name"] == "agent-platform-connectivity-model-manager-egress"),
        None,
    )
    if egress is None or "dex.ci.example.com" not in yaml.safe_dump(egress):
        sys.exit("FAIL: derived: model-manager's egress does not reach the derived issuer")
    print("ok: derived")

    print("--> identity-provider egress: every manager's pods reach the login by name and through an in-cluster gateway (cluster on 443 and 10443)")
    docs = [d for d in yaml.safe_load_all(result.stdout) if d]
    for name in MANAGERS:
        rules = idp_rules(docs, name)
        for want in IDP_EGRESS:
            if want not in rules:
                sys.exit(f"FAIL: idp egress: no Cilium policy gives {name}'s pods {want}; it has {rules}")
    print("ok: idp egress")

    print("--> skill-manager off: no policy selects its pods")
    rels = releases(meta, [*base, *ALL_ON, *LOGIN, *sets("components.skill-manager.enabled=false")])
    result = connectivity(conn, rels["agent-platform-connectivity"], base)
    if result.returncode != 0:
        sys.exit(f"FAIL: skill-manager off: the connectivity render failed\n{result.stderr}")
    if idp_rules([d for d in yaml.safe_load_all(result.stdout) if d], "skill-manager"):
        sys.exit("FAIL: skill-manager off: a policy still gives its pods identity-provider egress")
    print("ok: skill-manager off")

    print("--> skill-manager's identity provider entry: the issuer it runs with, only while its OAuth and its own NetworkPolicy are on")
    def entry(*extra: str) -> dict | None:
        rels = releases(meta, [*base, *ALL_ON, *LOGIN, *sets(*extra)])
        return rels["agent-platform-connectivity"]["networkPolicy"]["mcpBackends"]["skill-manager"].get("identityProvider")
    for what, extra, want in (
        ("muster's login", (), ISSUER),
        ("global.identity wins over muster's", ("global.identity.issuerUrl=https://global.example.com",), "https://global.example.com"),
        ("its own issuer wins", ("global.identity.issuerUrl=https://global.example.com", "skill-manager.oauth.dex.issuerURL=https://own.example.com"), "https://own.example.com"),
    ):
        expect(f"skill-manager identityProvider, {what}", entry(*extra), {"provider": "dex", "issuerUrl": want})
    expect("skill-manager identityProvider with its OAuth off", entry("skill-manager.oauth.enabled=false"), None)
    expect("skill-manager identityProvider with its NetworkPolicy off", entry("skill-manager.networkPolicy.enabled=false"), None)
    print("ok: skill-manager's identity provider entry")

    print("--> agent-manager's route hostname override is its base URL's host; model-manager, which has no route, keeps agentgateway.<domain>")
    rels = releases(meta, [*base, *ALL_ON, *LOGIN, *sets("agentManager.route.hostname=agents.ci.example.com")])
    expect("agent-manager oauth.baseURL", oauth(rels, "agent-manager").get("baseURL"), "https://agents.ci.example.com/agent-manager")
    expect("model-manager oauth.baseURL", oauth(rels, "model-manager").get("baseURL"), "https://agentgateway.ci.example.com/model-manager")
    print("ok: route hostname")

    print("--> explicit wins: a manager's own client, Secret, audiences and base URL stand")
    rels = releases(meta, [*base, *ALL_ON, *LOGIN, *sets(
        "model-manager.oauth.dex.clientID=own-client",
        "model-manager.oauth.existingSecret=own-secret",
        "model-manager.oauth.trustedAudiences[0]=own-audience",
        "model-manager.oauth.baseURL=https://mm.example.com",
    )])
    o = oauth(rels, "model-manager")
    expect("model-manager oauth.dex.clientID", o["dex"].get("clientID"), "own-client")
    expect("model-manager oauth.existingSecret", o.get("existingSecret"), "own-secret")
    expect("model-manager oauth.trustedAudiences", o.get("trustedAudiences"), ["own-audience"])
    expect("model-manager oauth.baseURL", o.get("baseURL"), "https://mm.example.com")
    expect("model-manager oauth.dex.issuerURL (still derived)", o["dex"].get("issuerURL"), ISSUER)
    print("ok: explicit wins")

    print("--> global.identity wins: set, nothing is filled (the charts read it themselves)")
    rels = releases(meta, [*base, *ALL_ON, *LOGIN, *sets(
        f"global.identity.issuerUrl={ISSUER}",
        f"global.identity.clientId={CLIENT}",
        f"global.identity.existingSecret={SECRET}",
    )])
    for name in MANAGERS:
        o = oauth(rels, name)
        for key in ("issuerURL", "clientID"):
            expect(f"{name} oauth.dex.{key} with global.identity set", o.get("dex", {}).get(key), None)
        expect(f"{name} oauth.existingSecret with global.identity set", o.get("existingSecret"), None)
        expect(f"{name} oauth.trustedAudiences with global.identity set", o.get("trustedAudiences"), None)
    print("ok: global.identity wins")

    print("--> global.identity without a client (enigma's shape): each key is filled on its own, the client from muster's login")
    rels = releases(meta, [*base, *ALL_ON, *LOGIN, *sets(
        f"global.identity.issuerUrl={ISSUER}",
        f"global.identity.existingSecret={SECRET}",
    )])
    for name in MANAGERS:
        o = oauth(rels, name)
        expect(f"{name} oauth.dex.clientID without global.identity.clientId", o.get("dex", {}).get("clientID"), CLIENT)
        expect(f"{name} oauth.trustedAudiences without global.identity.clientId", o.get("trustedAudiences"), [CLIENT])
        expect(f"{name} oauth.dex.issuerURL with global.identity.issuerUrl set", o.get("dex", {}).get("issuerURL"), None)
        expect(f"{name} oauth.existingSecret with global.identity.existingSecret set", o.get("existingSecret"), None)
    print("ok: global.identity without a client")

    print("--> one login: a global.identity.clientId that differs from muster's fails the connectivity render")
    rels = releases(meta, [*base, *ALL_ON, *LOGIN, *sets(f"global.identity.issuerUrl={ISSUER}", "global.identity.clientId=platform")])
    result = connectivity(conn, rels["agent-platform-connectivity"], base)
    if result.returncode == 0 or f"muster.muster.oauth.server.dex.clientId ({CLIENT}) differs from global.identity.clientId (platform)" not in result.stderr:
        sys.exit(f"FAIL: one login: the differing client did not fail the render by that guard\n{result.stderr}")
    print("ok: one login")

    print("--> absent: what cannot be derived fails the connectivity render naming the key")
    for what, extra, fragments in (
        ("no Secret", sets("muster.muster.oauth.server.existingSecret="), ("none of model-manager.oauth.existingSecret", "set muster.muster.oauth.server.existingSecret")),
        ("no client", sets("muster.muster.oauth.server.dex.clientId="), ("model-manager.oauth.dex.clientID is empty", "set muster.muster.oauth.server.dex.clientId")),
        ("no base URL (no domain)", sets("global.domain="), ("model-manager.oauth.baseURL is empty", "set global.domain")),
    ):
        flags = [*base, *LOGIN, *sets("components.kagent.enabled=true", "kagent.database.postgres.urlFile=/etc/kagent-db/uri", "global.domain=ci.example.com", "components.agentgateway.enabled=true"), *extra]
        rels = releases(meta, flags)
        result = connectivity(conn, rels["agent-platform-connectivity"], base)
        if result.returncode == 0 or not all(f in result.stderr for f in fragments):
            sys.exit(f"FAIL: absent, {what}: the connectivity render did not fail naming {fragments}\n{result.stderr}")
        print(f"ok: absent, {what}")

    print("--> no login named (muster's issuer unset): nothing filled, the guard stays silent")
    rels = releases(meta, [*base, *sets("components.kagent.enabled=true", "kagent.database.postgres.urlFile=/etc/kagent-db/uri",
                                        "kagent.controller.image.repository=giantswarm/kagent/controller")])
    expect("model-manager oauth.dex", oauth(rels, "model-manager").get("dex"), {"allowPrivateURLs": True})
    result = connectivity(conn, rels["agent-platform-connectivity"], base)
    if result.returncode != 0:
        sys.exit(f"FAIL: no login: the connectivity render failed\n{result.stderr}")
    print("ok: no login")


if __name__ == "__main__":
    main()
