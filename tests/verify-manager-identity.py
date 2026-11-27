#!/usr/bin/env python3
"""Assert the managers' OAuth inputs follow muster's login (giantswarm/agent-platform#484).

The meta chart fills what a manager's oauth block and global.identity leave
unset from muster.muster.oauth.server (agent-platform.identity.apply):
oauth.dex.issuerURL, oauth.dex.clientID with oauth.trustedAudiences, and
oauth.existingSecret for agent-manager, vm-manager, cluster-manager and
skill-manager, oauth.baseURL for agent-manager's route; model-manager, on
mcp-oauth's values layout, gets oauth.authorizationServer.upstream.issuer,
upstream.client.id, upstream.client.secretKeyRef and authorizationServer.issuer
instead, and no trustedAudiences (its chart derives them from the client).
The connectivity release receives the same filled blocks (not skill-manager's,
which only its own release reads), so each case renders the meta chart, then
the connectivity chart with the values its HelmRelease carries — where the
absence guard and the one-login guard fire. skill-manager's guards fire in the
meta render (agent-platform.identity.validate, giantswarm/agent-platform#1382).

Usage: verify-manager-identity.py <meta chart> <connectivity chart> [helm flags...]
The helm flags (the Makefile's VM: API versions, required inputs) go to both renders.
"""

import copy
import re
import subprocess
import sys
import tempfile
import urllib.parse

import yaml

MANAGERS = ("model-manager", "agent-manager", "vm-manager", "cluster-manager", "skill-manager")
# Blocks the connectivity release does not receive (components.agent-platform-connectivity.omitKeys).
NOT_IN_CONNECTIVITY = ("skill-manager",)
DEX_OPT_IN_UNSET = ("model-manager",)
# Components with an oauth block that this script does not render, and where they are covered.
EXEMPT = {"workspace-manager": "needs the workspaces fixture; verify-workspace-manager and verify-idp-egress cover it"}
OWN_CLIENTS = {"credential-api": "its own PKCE browser client; verify-credential-api checks issuer and audience derivation"}
# The managers that call the Kubernetes API as the person (oauth.downstream).
DOWNSTREAM = ("model-manager", "agent-manager", "cluster-manager")
ROUTED = {"model-manager": "/model-manager", "agent-manager": "/agent-manager"}
ISSUER = "https://dex.ci.example.com"
CLIENT = "muster-acme"
SECRET = "muster-oauth"
# agent-platform.idpEgress.cilium for a Dex issuer: its host by name, and the proxy pods of the
# Gateway muster's route attaches to (ingress.parentRefs, x in the release namespace) on the ports
# that Gateway serves it on (a private installation's Dex resolves there), never the cluster entity.
IDP_EGRESS = [
    {"toFQDNs": [{"matchName": "dex.ci.example.com"}], "toPorts": [{"ports": [{"port": "443", "protocol": "TCP"}]}]},
    {"toEndpoints": [{"matchLabels": {"app.kubernetes.io/name": "envoy", "gateway.envoyproxy.io/owning-gateway-name": "x",
                                      "gateway.envoyproxy.io/owning-gateway-namespace": "agent-platform",
                                      "io.kubernetes.pod.namespace": "envoy-gateway-system"}}],
     "toPorts": [{"ports": [{"port": "443", "protocol": "TCP"}, {"port": "10443", "protocol": "TCP"}]}]},
]


def sets(*pairs: str) -> list[str]:
    return [flag for pair in pairs for flag in ("--set", pair)]


# muster's login configured and nothing else: no global.identity, no manager oauth value.
# Without an issuer the line's own controller runs, not the kagent-extensions binary.
LOGIN = sets(
    "kagent.controller.image.repository=giantswarm/kagent/controller",
    "kagent.controller.auth.audiences[0]=agent-platform",
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


# Managers on mcp-oauth's values layout: the meta chart fills
# oauth.authorizationServer, not the deprecated keys.
LAYOUT = ("model-manager",)


def filled(o: dict, name: str) -> dict:
    """The login inputs a manager's oauth block carries, read from the layout the meta chart fills for it."""
    if name in LAYOUT:
        authz = o.get("authorizationServer", {})
        upstream = authz.get("upstream", {})
        client = upstream.get("client", {})
        ref = client.get("secretKeyRef", {})
        return {"issuer": upstream.get("issuer"), "client": client.get("id"), "secret": ref.get("name"),
                "secretKey": ref.get("key"), "audiences": o.get("trustedAudiences"), "base": authz.get("issuer")}
    dex = o.get("dex", {})
    return {"issuer": dex.get("issuerURL"), "client": dex.get("clientID"), "secret": o.get("existingSecret"),
            "secretKey": None, "audiences": o.get("trustedAudiences"), "base": o.get("baseURL")}


def login_audiences(name: str, client: str):
    """The trustedAudiences the meta chart fills with a client: none on the values layout."""
    return None if name in LAYOUT else [client]


def required_audiences(rels: dict[str, dict], name: str):
    return rels[name].get("muster", {}).get("mcpServer", {}).get("auth", {}).get("requiredAudiences")


def merge(dst: dict, src: dict) -> None:
    for key, value in src.items():
        if isinstance(value, dict) and isinstance(dst.get(key), dict):
            merge(dst[key], value)
        else:
            dst[key] = copy.deepcopy(value)


def layout_value(layout: dict) -> str:
    while isinstance(layout, dict):
        layout = next(iter(layout.values()))
    return layout


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
    missing = sorted(with_oauth - listed - set(OWN_CLIENTS))
    if missing:
        sys.exit(f"FAIL: roster: {missing} forward an oauth block but are not in agent-platform.identity.apply's list")
    if not set(MANAGERS) <= with_oauth:
        sys.exit(f"FAIL: roster: {sorted(set(MANAGERS) - with_oauth)} have no oauth block in values.yaml")
    # Every one of them is rendered below (its client and its identity-provider egress), or exempt with a reason.
    unchecked = sorted(with_oauth - set(MANAGERS) - set(EXEMPT) - set(OWN_CLIENTS))
    if unchecked:
        sys.exit(f"FAIL: roster: {unchecked} forward an oauth block but are not in MANAGERS: add them, so their login and identity-provider egress are checked")


def idp_rules(docs: list[dict], name: str) -> list[dict]:
    """The identity-provider egress rules (toFQDNs, the cluster entity, the Envoy edge) of the Cilium policies selecting <name>'s pods."""
    rules = []
    for d in docs:
        if d.get("kind") != "CiliumNetworkPolicy":
            continue
        if d["spec"].get("endpointSelector", {}).get("matchLabels", {}).get("app.kubernetes.io/name") != name:
            continue
        rules += [r for r in d["spec"].get("egress", []) if "toFQDNs" in r or "cluster" in r.get("toEntities", [])
                  or any(e.get("matchLabels", {}).get("app.kubernetes.io/name") == "envoy" for e in r.get("toEndpoints", []))]
    return rules


def expect_no_dex_opt_in(label: str, name: str, manager_oauth: dict) -> None:
    """model-manager's chart defaults the no-op opt-in to false, so its block leaves the key out; the others set it to false."""
    if name in DEX_OPT_IN_UNSET:
        expect(f"{label} obsolete Dex opt-in unset", "allowPrivateURLs" in manager_oauth.get("dex", {}), False)
    else:
        expect(f"{label} obsolete Dex opt-in", manager_oauth["dex"].get("allowPrivateURLs"), False)


def verify_private_issuer_defaults(meta: str, conn: str, base: list[str]) -> None:
    for chart in (meta, conn):
        with open(f"{chart}/values.yaml") as source:
            values = yaml.safe_load(source)
        for name in MANAGERS:
            if chart == conn and name in NOT_IN_CONNECTIVITY:
                continue
            manager_oauth = values[name]["oauth"]
            expect_no_dex_opt_in(f"{chart}: {name}", name, manager_oauth)
            expect(f"{chart}: {name} JWKS blanket unset", "sso" in manager_oauth, False)
        expect(f"{chart}: workspace-manager obsolete Dex opt-in", values["workspace-manager"]["oauth"]["dex"].get("allowPrivateURLs"), False)
        expect(f"{chart}: workspace-manager JWKS blanket unset", "sso" in values["workspace-manager"]["oauth"], False)
    with open(f"{meta}/values.yaml") as source:
        values = yaml.safe_load(source)
    expect(f"{meta}: mcp-kubernetes obsolete Dex opt-in unset", "allowPrivateURLs" in values["mcp-kubernetes"]["mcpKubernetes"]["oauth"], False)
    expect(f"{meta}: mcp-kubernetes JWKS blanket unset", "sso" in values["mcp-kubernetes"]["mcpKubernetes"]["oauth"], False)

    rels = releases(meta, [*base, *ALL_ON, *LOGIN])
    for name in MANAGERS:
        expect_no_dex_opt_in(f"{name} HelmRelease", name, oauth(rels, name))
        if name not in NOT_IN_CONNECTIVITY:
            forwarded = rels["agent-platform-connectivity"][name]["oauth"]
            expect_no_dex_opt_in(f"connectivity {name}", name, forwarded)
            expect(f"connectivity {name} JWKS blanket unset", "sso" in forwarded, False)
        expect(f"{name} HelmRelease JWKS blanket unset", "sso" in oauth(rels, name), False)
    print("ok: the five manager releases and workspace-manager disable or leave out the obsolete Dex opt-in and set no JWKS blanket; mcp-kubernetes sets neither")


def main() -> None:
    if sys.argv[1] == "--oauth-settings-only":
        verify_private_issuer_defaults(sys.argv[2], sys.argv[3], sys.argv[4:])
        return
    meta, conn, base = sys.argv[1], sys.argv[2], sys.argv[3:]

    print("--> roster: every component that forwards an oauth block takes muster's login")
    roster_covered(meta)
    print("ok: roster")

    print("--> derived: muster's login fills every manager; the routed ones get the route's base URL")
    rels = releases(meta, [*base, *ALL_ON, *LOGIN])
    for name in MANAGERS:
        f = filled(oauth(rels, name), name)
        expect(f"{name} issuer", f["issuer"], ISSUER)
        expect(f"{name} client", f["client"], CLIENT)
        expect(f"{name} client secret", f["secret"], SECRET)
        expect(f"{name} oauth.trustedAudiences", f["audiences"], login_audiences(name, CLIENT))
        want = f"https://agentgateway.ci.example.com{ROUTED[name]}" if name in ROUTED else None
        expect(f"{name} base URL", f["base"], want)
    expect("model-manager client secret key", filled(oauth(rels, "model-manager"), "model-manager")["secretKey"], "dex-client-secret")
    for key in ("dex", "existingSecret", "baseURL", "trustedAudiences"):
        expect(f"model-manager deprecated oauth.{key} left unset", key in oauth(rels, "model-manager"), False)
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

    print("--> global.identity: the managers that call the Kubernetes API as the person take its provider and audiences where their blocks leave them unset")
    for name in DOWNSTREAM:
        expect(f"{name} oauth.provider from global.identity.type", oauth(rels, name).get("provider"), "dex")
        expect(f"{name} requiredAudiences from global.identity.kubernetes.audiences", required_audiences(rels, name), ["dex-k8s-authenticator"])
    expect("skill-manager requiredAudiences (its own)", required_audiences(rels, "skill-manager"), ["dex-k8s-authenticator"])
    rels = releases(meta, [*base, *ALL_ON, *LOGIN, *sets(
        "global.identity.kubernetes.audiences={api-a,api-b}",
        "agent-manager.muster.mcpServer.auth.requiredAudiences={own}",
    ), "--set-json", "cluster-manager.muster.mcpServer.auth.requiredAudiences=[]"])
    expect("model-manager requiredAudiences from a set global", required_audiences(rels, "model-manager"), ["api-a", "api-b"])
    expect("agent-manager requiredAudiences (its own wins)", required_audiences(rels, "agent-manager"), ["own"])
    expect("cluster-manager requiredAudiences (its own empty list wins)", required_audiences(rels, "cluster-manager"), [])
    expect("skill-manager requiredAudiences (not filled)", required_audiences(rels, "skill-manager"), ["dex-k8s-authenticator"])
    rels = releases(meta, [*base, *ALL_ON, *LOGIN, *sets("global.identity.type=google", "model-manager.oauth.provider=dex")])
    expect("agent-manager oauth.provider from global.identity.type google", oauth(rels, "agent-manager").get("provider"), "google")
    expect("model-manager oauth.provider (its own wins)", oauth(rels, "model-manager").get("provider"), "dex")
    print("ok: global.identity")

    print("--> identity-provider egress: every manager's pods reach the login by name and through the in-cluster Gateway's proxy pods on 443 and 10443, not the cluster entity")
    docs = [d for d in yaml.safe_load_all(result.stdout) if d]
    for name in MANAGERS:
        rules = idp_rules(docs, name)
        for want in IDP_EGRESS:
            if want not in rules:
                sys.exit(f"FAIL: idp egress: no Cilium policy gives {name}'s pods {want}; it has {rules}")
        if any("cluster" in r.get("toEntities", []) for r in rules):
            sys.exit(f"FAIL: idp egress: a Cilium policy gives {name}'s pods the cluster entity; it has {rules}")
    print("ok: idp egress")

    print("--> trusted issuers: model-manager's egress reaches each issuer and key set host of oauth.resourceServer.trustedIssuers by name, an in-cluster one through its namespace, the login host once; an address fails the render")
    values = copy.deepcopy(rels["agent-platform-connectivity"])
    merge(values["model-manager"]["oauth"], {"resourceServer": {"trustedIssuers": [
        {"issuer": "https://Other.Example.com/", "jwksURI": "https://keys.example.net/jwks", "audiences": ["a"]},
        {"issuer": "http://dex.idp.svc.cluster.local:5556/dex", "jwksURI": "http://dex.idp.svc.cluster.local:5556/dex/keys", "audiences": ["a"]},
        {"issuer": ISSUER, "jwksURI": f"{ISSUER}/keys", "audiences": ["a"]},
    ]}})
    result = connectivity(conn, values, base)
    if result.returncode != 0:
        sys.exit(f"FAIL: trusted issuers: the connectivity render failed\n{result.stderr}")
    egress = next(d for d in yaml.safe_load_all(result.stdout) if d and d.get("kind") == "CiliumNetworkPolicy"
                  and d["metadata"]["name"] == "agent-platform-connectivity-model-manager-egress")["spec"]["egress"]
    names = [m["matchName"] for r in egress for m in r.get("toFQDNs", []) if "matchName" in m]
    expect("trusted issuers: external hosts by name", sorted(n for n in names if n in ("other.example.com", "keys.example.net")), ["keys.example.net", "other.example.com"])
    expect("trusted issuers: the login host once", names.count(urllib.parse.urlparse(ISSUER).hostname), 1)
    expect("trusted issuers: no FQDN rule for an in-cluster Service", [n for n in names if n.endswith(".svc.cluster.local")], [])
    if not any(m.get("matchLabels") == {"io.kubernetes.pod.namespace": "idp"} for r in egress for m in r.get("toEndpoints", [])):
        sys.exit(f"FAIL: trusted issuers: no rule reaches the in-cluster issuer's namespace idp; egress is {egress}")
    values["model-manager"]["oauth"]["resourceServer"]["trustedIssuers"].append({"issuer": "https://203.0.113.7/", "jwksURI": "https://203.0.113.7/keys", "audiences": ["a"]})
    result = connectivity(conn, values, base)
    if result.returncode == 0 or "model-manager.oauth.resourceServer.trustedIssuers[3].issuer names an address (203.0.113.7)" not in result.stderr:
        sys.exit(f"FAIL: trusted issuers: an address literal did not fail the render naming the entry\n{result.stderr}")
    print("ok: trusted issuers")

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
        # One that differs from global.identity fails the render (the one-login case below).
        ("its own issuer wins over muster's", ("skill-manager.oauth.dex.issuerURL=https://own.example.com",), "https://own.example.com"),
    ):
        expect(f"skill-manager identityProvider, {what}", entry(*extra), {"provider": "dex", "issuerUrl": want})
    expect("skill-manager identityProvider with its OAuth off", entry("skill-manager.oauth.enabled=false"), None)
    expect("skill-manager identityProvider with its NetworkPolicy off", entry("skill-manager.networkPolicy.enabled=false"), None)
    print("ok: skill-manager's identity provider entry")

    print("--> skill-manager's pod selector is its chart's name label: nameOverride, an empty one meaning the chart name, cut to 63 without a trailing hyphen")
    long = "s" * 62 + "-x"
    for override, want in (("", "skill-manager"), ("sm", "sm"), (long, "s" * 62)):
        rels = releases(meta, [*base, *ALL_ON, *LOGIN, "--set-string", f"skill-manager.nameOverride={override}"])
        got = rels["agent-platform-connectivity"]["networkPolicy"]["mcpBackends"]["skill-manager"].get("podSelector")
        expect(f"skill-manager podSelector with nameOverride {override!r}", got, {"app.kubernetes.io/name": want})
    print("ok: skill-manager's pod selector")

    print("--> agent-manager's route hostname override is its base URL's host; model-manager, which has no route, keeps agentgateway.<domain>")
    rels = releases(meta, [*base, *ALL_ON, *LOGIN, *sets("agentManager.route.hostname=agents.ci.example.com")])
    expect("agent-manager oauth.baseURL", oauth(rels, "agent-manager").get("baseURL"), "https://agents.ci.example.com/agent-manager")
    expect("model-manager base URL", filled(oauth(rels, "model-manager"), "model-manager")["base"], "https://agentgateway.ci.example.com/model-manager")
    print("ok: route hostname")

    print("--> explicit wins: a manager's own client, Secret, audiences and base URL stand")
    rels = releases(meta, [*base, *ALL_ON, *LOGIN, *sets(
        "agent-manager.oauth.dex.clientID=own-client",
        "agent-manager.oauth.existingSecret=own-secret",
        "agent-manager.oauth.trustedAudiences[0]=own-audience",
        "agent-manager.oauth.baseURL=https://am.example.com",
    )])
    o = oauth(rels, "agent-manager")
    expect("agent-manager oauth.dex.clientID", o["dex"].get("clientID"), "own-client")
    expect("agent-manager oauth.existingSecret", o.get("existingSecret"), "own-secret")
    expect("agent-manager oauth.trustedAudiences", o.get("trustedAudiences"), ["own-audience"])
    expect("agent-manager oauth.baseURL", o.get("baseURL"), "https://am.example.com")
    expect("agent-manager oauth.dex.issuerURL (still derived)", o["dex"].get("issuerURL"), ISSUER)
    print("ok: explicit wins")

    print("--> explicit wins on the values layout: model-manager's own keys stand, in either layout, and the rest is still filled")
    rels = releases(meta, [*base, *ALL_ON, *LOGIN, *sets(
        "model-manager.oauth.authorizationServer.upstream.client.id=own-client",
        "model-manager.oauth.authorizationServer.upstream.client.secretKeyRef.name=own-secret",
        "model-manager.oauth.authorizationServer.upstream.client.secretKeyRef.key=client-secret",
        "model-manager.oauth.authorizationServer.issuer=https://mm.example.com",
    )])
    f = filled(oauth(rels, "model-manager"), "model-manager")
    expect("model-manager upstream.client.id", f["client"], "own-client")
    expect("model-manager upstream.client.secretKeyRef", (f["secret"], f["secretKey"]), ("own-secret", "client-secret"))
    expect("model-manager authorizationServer.issuer", f["base"], "https://mm.example.com")
    expect("model-manager upstream.issuer (still derived)", f["issuer"], ISSUER)
    rels = releases(meta, [*base, *ALL_ON, *LOGIN, *sets(
        "model-manager.oauth.dex.clientID=own-client",
        "model-manager.oauth.existingSecret=own-secret",
        "model-manager.oauth.baseURL=https://mm.example.com",
    )])
    o = oauth(rels, "model-manager")
    f = filled(o, "model-manager")
    expect("model-manager deprecated oauth.dex.clientID stands", o["dex"].get("clientID"), "own-client")
    expect("model-manager upstream.client with deprecated keys set", (f["client"], f["secret"]), (None, None))
    expect("model-manager authorizationServer.issuer with deprecated baseURL set", f["base"], None)
    expect("model-manager upstream.issuer (still derived)", f["issuer"], ISSUER)
    print("ok: explicit wins on the values layout")

    print("--> global.identity wins: set, nothing is filled (the charts read it themselves)")
    rels = releases(meta, [*base, *ALL_ON, *LOGIN, *sets(
        f"global.identity.issuerUrl={ISSUER}",
        f"global.identity.clientId={CLIENT}",
        f"global.identity.existingSecret={SECRET}",
    )])
    for name in MANAGERS:
        f = filled(oauth(rels, name), name)
        for key in ("issuer", "client", "secret", "audiences"):
            expect(f"{name} {key} with global.identity set", f[key], None)
    print("ok: global.identity wins")

    print("--> global.identity without a client (enigma's shape): each key is filled on its own, the client from muster's login")
    rels = releases(meta, [*base, *ALL_ON, *LOGIN, *sets(
        f"global.identity.issuerUrl={ISSUER}",
        f"global.identity.existingSecret={SECRET}",
    )])
    for name in MANAGERS:
        f = filled(oauth(rels, name), name)
        expect(f"{name} client without global.identity.clientId", f["client"], CLIENT)
        expect(f"{name} oauth.trustedAudiences without global.identity.clientId", f["audiences"], login_audiences(name, CLIENT))
        expect(f"{name} issuer with global.identity.issuerUrl set", f["issuer"], None)
        expect(f"{name} client secret with global.identity.existingSecret set", f["secret"], None)
    print("ok: global.identity without a client")

    print("--> one login: a global.identity.clientId that differs from muster's fails the connectivity render")
    rels = releases(meta, [*base, *ALL_ON, *LOGIN, *sets(f"global.identity.issuerUrl={ISSUER}", "global.identity.clientId=platform")])
    result = connectivity(conn, rels["agent-platform-connectivity"], base)
    if result.returncode == 0 or f"muster.muster.oauth.server.dex.clientId ({CLIENT}) differs from global.identity.clientId (platform)" not in result.stderr:
        sys.exit(f"FAIL: one login: the differing client did not fail the render by that guard\n{result.stderr}")
    print("ok: one login")

    print("--> model-manager's values layout: the connectivity guard reads oauth.authorizationServer before the deprecated keys and names the key that differs")
    agreed = sets(f"global.identity.issuerUrl={ISSUER}", f"global.identity.clientId={CLIENT}", f"global.identity.existingSecret={SECRET}")
    rels = releases(meta, [*base, *ALL_ON, *LOGIN, *agreed])
    for key, layout, global_key, value in (
        ("authorizationServer.upstream.issuer", {"authorizationServer": {"upstream": {"issuer": "https://other.example.com"}}}, "issuerUrl", ISSUER),
        ("authorizationServer.upstream.client.id", {"authorizationServer": {"upstream": {"client": {"id": "other"}}}}, "clientId", CLIENT),
        ("authorizationServer.upstream.client.secretKeyRef.name", {"authorizationServer": {"upstream": {"client": {"secretKeyRef": {"name": "other"}}}}}, "existingSecret", SECRET),
        ("dex.clientID", {"dex": {"clientID": "other"}}, "clientId", CLIENT),
    ):
        values = copy.deepcopy(rels["agent-platform-connectivity"])
        merge(values["model-manager"]["oauth"], layout)
        result = connectivity(conn, values, base)
        got = layout_value(layout)
        want = f"model-manager.oauth.{key} ({got}) differs from global.identity.{global_key} ({value})"
        if result.returncode == 0 or want not in result.stderr:
            sys.exit(f"FAIL: values layout, {key}: the connectivity render did not fail with {want!r}\n{result.stderr}")
        print(f"ok: values layout, {key}")

    print("--> model-manager's values layout satisfies the absence guard, and the provider falls back to global.identity.type")
    absent = sets("muster.muster.oauth.server.dex.clientId=", "muster.muster.oauth.server.existingSecret=", "global.domain=")
    flags = [*base, *LOGIN, *absent, *sets("components.kagent.enabled=true", "kagent.database.postgres.urlFile=/etc/kagent-db/uri", "components.agentgateway.enabled=true")]
    rels = releases(meta, flags)
    values = copy.deepcopy(rels["agent-platform-connectivity"])
    merge(values["model-manager"]["oauth"], {"authorizationServer": {"issuer": "https://mm.example.com", "upstream": {"client": {"id": CLIENT, "secretKeyRef": {"name": SECRET}}}}})
    result = connectivity(conn, values, base)
    if result.returncode != 0:
        sys.exit(f"FAIL: values layout, absence: the new keys did not satisfy the guard\n{result.stderr}")
    print("ok: values layout, absence")
    values = copy.deepcopy(rels["agent-platform-connectivity"])
    # Both charts default model-manager.oauth.provider to dex; null removes it.
    values["model-manager"]["oauth"]["provider"] = None
    values["global"]["identity"]["type"] = "google"
    result = connectivity(conn, values, base)
    if result.returncode != 0:
        sys.exit(f"FAIL: values layout, global.identity.type google: the Dex absence guard fired\n{result.stderr}")
    merge(values["model-manager"]["oauth"], {"authorizationServer": {"upstream": {"type": "dex"}}})
    result = connectivity(conn, values, base)
    if result.returncode == 0 or "model-manager.oauth.authorizationServer.upstream.client.id is empty" not in result.stderr:
        sys.exit(f"FAIL: values layout, upstream.type dex over global.identity.type google: the Dex absence guard did not fire\n{result.stderr}")
    print("ok: values layout, provider: oauth.authorizationServer.upstream.type, else global.identity.type")

    print("--> absent: what cannot be derived fails the connectivity render naming the key")
    for what, extra, fragments in (
        ("no Secret", sets("muster.muster.oauth.server.existingSecret="), ("none of model-manager.oauth.authorizationServer.upstream.client.secretKeyRef.name", "set muster.muster.oauth.server.existingSecret")),
        ("no client", sets("muster.muster.oauth.server.dex.clientId="), ("model-manager.oauth.authorizationServer.upstream.client.id is empty", "set muster.muster.oauth.server.dex.clientId")),
        ("no base URL (no domain)", sets("global.domain="), ("model-manager.oauth.authorizationServer.issuer is empty", "set global.domain")),
    ):
        flags = [*base, *LOGIN, *sets("components.kagent.enabled=true", "kagent.database.postgres.urlFile=/etc/kagent-db/uri", "global.domain=ci.example.com", "components.agentgateway.enabled=true"), *extra]
        rels = releases(meta, flags)
        result = connectivity(conn, rels["agent-platform-connectivity"], base)
        if result.returncode == 0 or not all(f in result.stderr for f in fragments):
            sys.exit(f"FAIL: absent, {what}: the connectivity render did not fail naming {fragments}\n{result.stderr}")
        print(f"ok: absent, {what}")

    # skill-manager's block reaches its own release only, so the meta render guards it.
    print("--> one login, skill-manager: an own value that differs from global.identity fails the meta render naming both")
    agreed = sets(f"global.identity.issuerUrl={ISSUER}", f"global.identity.clientId={CLIENT}", f"global.identity.existingSecret={SECRET}")
    for key, global_key, value in (
        ("dex.issuerURL", "issuerUrl", ISSUER),
        ("dex.clientID", "clientId", CLIENT),
        ("existingSecret", "existingSecret", SECRET),
    ):
        flags = [*base, *ALL_ON, *LOGIN, *agreed, *sets(f"skill-manager.oauth.{key}=other")]
        result = helm(meta, flags)
        want = f"skill-manager.oauth.{key} (other) differs from global.identity.{global_key} ({value})"
        if result.returncode == 0 or want not in result.stderr:
            sys.exit(f"FAIL: one login, skill-manager.oauth.{key}: the meta render did not fail with {want!r}\n{result.stderr}")
        if helm(meta, [*flags, *sets("components.skill-manager.enabled=false")]).returncode != 0:
            sys.exit(f"FAIL: one login, skill-manager.oauth.{key}: the guard fires with the skill-manager off")
        releases(meta, [*base, *ALL_ON, *LOGIN, *agreed, *sets(f"skill-manager.oauth.{key}={value}")])
        print(f"ok: one login, skill-manager.oauth.{key}")

    print("--> absent, skill-manager: what cannot be derived fails the meta render naming the key")
    for what, extra, fragments in (
        ("no Secret", sets("muster.muster.oauth.server.existingSecret="), ("none of skill-manager.oauth.existingSecret", "set muster.muster.oauth.server.existingSecret")),
        ("no client", sets("muster.muster.oauth.server.dex.clientId="), ("skill-manager.oauth.dex.clientID is empty", "set muster.muster.oauth.server.dex.clientId")),
        ("no base URL (no domain)", sets("global.domain="), ("skill-manager.oauth.baseURL is empty", "set global.domain")),
    ):
        result = helm(meta, [*base, *ALL_ON, *LOGIN, *extra])
        if result.returncode == 0 or not all(f in result.stderr for f in fragments):
            sys.exit(f"FAIL: absent, skill-manager, {what}: the meta render did not fail naming {fragments}\n{result.stderr}")
        print(f"ok: absent, skill-manager, {what}")

    print("--> no login named (muster's issuer unset): nothing filled, the guard stays silent")
    rels = releases(meta, [*base, *sets("components.kagent.enabled=true", "kagent.database.postgres.urlFile=/etc/kagent-db/uri",
                                        "kagent.controller.image.repository=giantswarm/kagent/controller")])
    expect("model-manager oauth.dex", oauth(rels, "model-manager").get("dex") or {}, {})
    result = connectivity(conn, rels["agent-platform-connectivity"], base)
    if result.returncode != 0:
        sys.exit(f"FAIL: no login: the connectivity render failed\n{result.stderr}")
    print("ok: no login")


if __name__ == "__main__":
    main()
