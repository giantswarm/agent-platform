#!/usr/bin/env python3
"""Assert the managers' OAuth inputs follow muster's login (giantswarm/agent-platform#484).

The meta chart fills what a manager's oauth block and global.identity leave
unset from muster.muster.oauth.server (agent-platform.identity.apply):
oauth.dex.issuerURL, oauth.dex.clientID with oauth.trustedAudiences, and
oauth.existingSecret for model-manager, agent-manager, vm-manager and
cluster-manager; oauth.baseURL for the two routed managers under an
agentgateway-* ingress.mode. The connectivity release receives the same filled
blocks, so each case renders the meta chart, then the connectivity chart with
the values its HelmRelease carries — where the absence guard and the
one-login guard fire.

Usage: verify-manager-identity.py <meta chart> <connectivity chart> [helm flags...]
The helm flags (the Makefile's VM: API versions, required inputs) go to both renders.
"""

import subprocess
import sys
import tempfile

import yaml

MANAGERS = ("model-manager", "agent-manager", "vm-manager", "cluster-manager")
ROUTED = {"model-manager": "/model-manager", "agent-manager": "/agent-manager"}
ISSUER = "https://dex.ci.example.com"
CLIENT = "muster-acme"
SECRET = "muster-oauth"


def sets(*pairs: str) -> list[str]:
    return [flag for pair in pairs for flag in ("--set", pair)]


# muster's login configured and nothing else: no global.identity, no manager oauth value.
LOGIN = sets(
    f"muster.muster.oauth.server.dex.issuerUrl={ISSUER}",
    f"muster.muster.oauth.server.dex.clientId={CLIENT}",
    f"muster.muster.oauth.server.existingSecret={SECRET}",
)
ALL_ON = sets(
    "ingress.mode=agentgateway-muster",
    "components.agentgateway.enabled=true",
    "components.kagent.enabled=true",
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


def main() -> None:
    meta, conn, base = sys.argv[1], sys.argv[2], sys.argv[3:]

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

    print("--> the route's hostname override is the base URL's host")
    rels = releases(meta, [*base, *ALL_ON, *LOGIN, *sets("modelManager.route.hostname=models.ci.example.com")])
    expect("model-manager oauth.baseURL", oauth(rels, "model-manager").get("baseURL"), "https://models.ci.example.com/model-manager")
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
        ("no base URL (muster-direct, no domain)", sets("ingress.mode=muster-direct", "global.domain="), ("model-manager.oauth.baseURL is empty", "modelManager.route.hostname")),
    ):
        flags = [*base, *LOGIN, *sets("components.kagent.enabled=true", "global.domain=ci.example.com", "ingress.mode=agentgateway-muster", "components.agentgateway.enabled=true"), *extra]
        rels = releases(meta, flags)
        result = connectivity(conn, rels["agent-platform-connectivity"], base)
        if result.returncode == 0 or not all(f in result.stderr for f in fragments):
            sys.exit(f"FAIL: absent, {what}: the connectivity render did not fail naming {fragments}\n{result.stderr}")
        print(f"ok: absent, {what}")

    print("--> no login named (muster's issuer unset): nothing filled, the guard stays silent")
    rels = releases(meta, [*base, *sets("components.kagent.enabled=true")])
    expect("model-manager oauth.dex", oauth(rels, "model-manager").get("dex"), {"allowPrivateURLs": True})
    result = connectivity(conn, rels["agent-platform-connectivity"], base)
    if result.returncode != 0:
        sys.exit(f"FAIL: no login: the connectivity render failed\n{result.stderr}")
    print("ok: no login")


if __name__ == "__main__":
    main()
