#!/usr/bin/env python3
"""Assert the objects and values muster.grants.<server>.git renders (Makefile: verify-grants-git)."""
import json
import sys

import yaml


def fail(msg):
    print(f"FAIL: {msg}")
    sys.exit(1)


def docs(path):
    with open(path, encoding="utf-8") as f:
        return [d for d in yaml.safe_load_all(f) if d]


def meta(path):
    releases = {d["metadata"]["name"]: d for d in docs(path) if d.get("kind") == "HelmRelease"}
    muster = releases["muster"]["spec"]["values"]
    if "grants" in muster:
        fail("muster.grants reached the muster release; it is meta-chart-owned (components.muster.omitKeys)")
    server = muster["muster"]["oauth"]["server"]
    broker = server["tokenExchangeBroker"]
    if broker["brokerClients"]["agentgateway-grants"]["clientCredentialsSecretRef"]["name"] != "muster-broker-agentgateway":
        fail(f"broker client: {broker['brokerClients']}")
    if broker["clientAudiences"]["agentgateway-grants"] != ["github"]:
        fail(f"client audiences: {broker['clientAudiences']}")
    if broker["targets"]["github"] != {"grantIssuer": "https://github.com/login/oauth"}:
        fail(f"grant target: {broker['targets']}")
    issuers = server["trustedIssuers"]
    dex = [i for i in issuers if i.get("issuer") == "https://dex.example.com"]
    if len(dex) != 1 or dex[0].get("acceptedTypHeaders") != [""] or dex[0].get("jwksUrl") != "https://dex.example.com/keys" or dex[0].get("allowedAudiences") != ["agent-platform"]:
        fail(f"trusted issuer entry: {issuers}")
    harnesses = releases["kagent"]["spec"]["values"]["harnesses"]
    env = {e["name"]: e["value"] for e in harnesses[0]["env"]}
    routes = json.loads(env["KAGENT_CLAUDE_CALLER_ROUTES"])
    if routes != {"github.com": "http://agentgateway.default.svc.cluster.local:8080/route/github.com/"}:
        fail(f"claude Harness routes: {routes}")
    if "agentgateway.default.svc.cluster.local" not in harnesses[0]["egress"]:
        fail(f"claude Harness egress lacks the Gateway: {harnesses[0]['egress']}")
    print("ok: muster's broker, the trusted issuer and the Harness route derived")


def connectivity(path):
    objs = {(d["kind"], d["metadata"]["name"]): d for d in docs(path)}
    for kind in ("AgentgatewayBackend", "HTTPRoute", "AgentgatewayPolicy"):
        for name in ("grant-github-git", "grant-gitlab-git"):
            if (kind, name) not in objs:
                fail(f"{kind} {name} not rendered")
    secrets = [d for (k, n), d in objs.items() if k == "Secret" and n == "muster-broker-agentgateway"]
    if len(secrets) != 1 or set(secrets[0]["stringData"]) != {"client-id", "client-secret"} or secrets[0]["stringData"]["client-id"] != "agentgateway-grants":
        fail(f"broker Secret: {secrets}")
    backend = objs[("AgentgatewayBackend", "grant-github-git")]["spec"]
    exchange = backend["policies"]["auth"]["oauthTokenExchange"]
    if exchange["cache"] != {"inMemory": {"maxEntries": 0}}:
        fail(f"exchange cache is not off: {exchange['cache']}")
    if exchange["audiences"] != ["github"] or exchange["backendRef"]["port"] != 8090 or exchange["path"] != "/oauth/token":
        fail(f"exchange: {exchange}")
    if "x-access-token:" not in backend["policies"]["transformation"]["request"]["set"][0]["value"]:
        fail("the Basic x-access-token credential is not set")
    if backend["static"] != {"host": "github.com", "port": 443}:
        fail(f"backend target: {backend['static']}")
    route = objs[("HTTPRoute", "grant-github-git")]["spec"]["rules"][0]
    if route["matches"][0]["path"]["value"] != "/route/github.com/" or route["timeouts"]["request"] != "0s":
        fail(f"route: {route}")
    github = objs[("AgentgatewayPolicy", "grant-github-git")]["spec"]["traffic"]
    if "git-receive-pack" not in github["authorization"]["policy"]["matchExpressions"][0]:
        fail("the github grant admits no push")
    gitlab = objs[("AgentgatewayPolicy", "grant-gitlab-git")]["spec"]["traffic"]
    if "git-receive-pack" in gitlab["authorization"]["policy"]["matchExpressions"][0]:
        fail("push: false left git-receive-pack in")
    sets = github["transformation"]["response"]["set"]
    headers = {h["name"]: h["value"] for h in sets}
    if [h["name"] for h in sets] != ["www-authenticate", ":status"]:
        fail(f"the realm must be set before the status rewrites the code: {[h['name'] for h in sets]}")
    if headers[":status"] != "response.code == 400 ? 401 : response.code" or "core_auth_login with server github" not in headers["www-authenticate"] or not headers["www-authenticate"].endswith(": response.headers['www-authenticate']"):
        fail(f"401 answer: {headers}")
    print("ok: grant objects rendered as declared")


if __name__ == "__main__":
    if sys.argv[1] == "--connectivity":
        connectivity(sys.argv[2])
    else:
        meta(sys.argv[1])
