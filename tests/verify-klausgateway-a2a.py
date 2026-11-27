#!/usr/bin/env python3
"""Assert klaus-gateway's A2A server entry (klausGateway.agentgatewayRoute.a2aServer).

Modes, each reading a rendered manifest (stdout of `helm template`, PyYAML):
  --connectivity  the connectivity chart with ci/test-klausgateway-a2a-values.yaml:
                  the A2A backend, the card route without a policy, the /a2a
                  route with the token check and the exchange, the JWKS
                  backends, the data plane's egress to klaus-gateway.
  --connectivity-in-cluster  the same with an in-cluster Dex token endpoint and
                  its CA: the TLS backend with the CA, the data plane's egress.
  --off           the connectivity chart without the entry: none of it.
  --meta          the meta chart with ci/test-klausgateway-a2a-values.yaml: the
                  klaus-gateway release's derived a2aServer block.
  --meta-off      the meta chart without the entry: no a2aServer forwarded.
"""

import sys

import yaml

CLIENT = (
    '(has(jwt.azp) ? jwt.azp : (has(jwt.client_id) ? jwt.client_id : '
    '(type(jwt.aud) == string ? jwt.aud : (size(jwt.aud) == 1 ? jwt.aud[0] : ""))))'
)


def load(path):
    with open(path, encoding="utf-8") as f:
        return [d for d in yaml.safe_load_all(f) if d]


def find(docs, kind, name):
    for d in docs:
        if d.get("kind") == kind and d["metadata"]["name"] == name:
            return d
    return None


def fail(msg):
    sys.exit(f"FAIL: {msg}")


def connectivity(docs):
    backend = find(docs, "AgentgatewayBackend", "klausgateway-a2a")
    if not backend or backend["spec"].get("a2a") != {"host": "klaus-gateway.agent-platform.svc.cluster.local", "port": 8080}:
        fail(f"klausgateway-a2a must be an A2A backend at klaus-gateway:8080, got {backend and backend['spec']!r}")
    cards = find(docs, "AgentgatewayBackend", "klausgateway-a2a-cards")
    if not cards or cards["spec"].get("static") != backend["spec"]["a2a"]:
        fail("card GETs must use the same destination without A2A response rewriting")
    for name, kind, path in (("klausgateway-a2a-card", "Exact", "/.well-known/agent-card.json"), ("klausgateway-a2a", "PathPrefix", "/a2a")):
        route = find(docs, "HTTPRoute", name)
        if not route:
            fail(f"no HTTPRoute {name}")
        match = route["spec"]["rules"][0]["matches"][0]["path"]
        if match != {"type": kind, "value": path} or route["spec"]["hostnames"] != ["a2a.ci.example.com"]:
            fail(f"HTTPRoute {name} matches {match!r} on {route['spec']['hostnames']!r}")
        if not find(docs, "HTTPRoute", f"agentgateway-{name}"):
            fail(f"no layer-1 HTTPRoute agentgateway-{name}")
        rules = route["spec"]["rules"]
        if name == "klausgateway-a2a":
            if rules[0]["matches"][0].get("method") != "GET" or rules[0]["backendRefs"][0]["name"] != "klausgateway-a2a-cards":
                fail("authenticated card GETs must use the static backend")
            if len(rules) != 2 or "method" in rules[1]["matches"][0] or rules[1]["backendRefs"][0]["name"] != "klausgateway-a2a":
                fail("JSON-RPC calls must keep the A2A backend")
        elif rules[0]["backendRefs"][0]["name"] != "klausgateway-a2a-cards":
            fail("the public card must use the static backend")
    targeted = [
        ref["name"]
        for d in docs
        if d.get("kind") == "AgentgatewayPolicy"
        for ref in d["spec"].get("targetRefs", [])
    ]
    if "klausgateway-a2a-card" in targeted:
        fail("the Agent Card route must carry no policy: a client reads it before it has a token")
    policy = find(docs, "AgentgatewayPolicy", "klausgateway-a2a")
    if not policy:
        fail("no AgentgatewayPolicy klausgateway-a2a")
    spec = policy["spec"]
    if spec["targetRefs"] != [{"group": "gateway.networking.k8s.io", "kind": "HTTPRoute", "name": "klausgateway-a2a"}]:
        fail(f"the policy targets {spec['targetRefs']!r}")
    jwt = spec["traffic"]["jwtAuthentication"]
    if jwt["mode"] != "Strict" or jwt.get("preserveToken") is not True:
        fail(f"jwtAuthentication must be Strict and keep the token for the exchange, got mode {jwt['mode']!r} preserveToken {jwt.get('preserveToken')!r}")
    providers = {(p["issuer"], tuple(p["audiences"]), p["jwks"]["remote"]["backendRef"]["name"], p["jwks"]["remote"]["jwksPath"]) for p in jwt["providers"]}
    want_providers = {
        ("https://dex.ci.example.com", ("klaus-gateway-app",), "klausgateway-a2a-jwks-0", "/keys"),
        ("https://login.example.org", ("platform-backend",), "klausgateway-a2a-jwks-1", "/.well-known/jwks.json"),
    }
    if providers != want_providers:
        fail(f"providers {providers!r}, want {want_providers!r}")
    authz = spec["traffic"]["authorization"]
    want_rule = (
        f'(jwt.iss == "https://dex.ci.example.com" && {CLIENT} in ["example-app"]) || '
        f'(jwt.iss == "https://login.example.org" && {CLIENT} in ["example-backend","example-batch"])'
    )
    if authz["action"] != "Require" or authz["policy"]["matchExpressions"] != [want_rule]:
        fail(f"authorization {authz!r}, want Require {want_rule!r}")
    exchange = spec["backend"]["auth"]["oauthTokenExchange"]
    want_exchange = {
        "backendRef": {"group": "agentgateway.dev", "kind": "AgentgatewayBackend", "name": "klausgateway-a2a-dex-token"},
        "path": "/token",
        "subjectToken": {"tokenType": "IdToken"},
        "requestedTokenType": "IdToken",
        "scopes": [
            "openid", "email", "profile", "groups",
            "audience:server:client_id:klaus-gateway",
            "audience:server:client_id:agent-platform",
            "audience:server:client_id:dex-k8s-authenticator",
        ],
        "additionalParams": {
            "connector_id": 'jwt.iss == "https://dex.ci.example.com" ? "self" : (jwt.iss == "https://login.example.org" ? "example-idp" : (""))',
        },
        "clientAuth": {
            "clientId": "klaus-gateway-exchange",
            "method": "ClientSecretBasic",
            "secretRef": {"name": "klaus-gateway-exchange", "key": "clientSecret"},
        },
        "cache": {"inMemory": {"maxEntries": 0}},
    }
    if exchange != want_exchange:
        fail(f"oauthTokenExchange {exchange!r}, want {want_exchange!r}")
    jwks0 = find(docs, "AgentgatewayBackend", "klausgateway-a2a-jwks-0")
    jwks1 = find(docs, "AgentgatewayBackend", "klausgateway-a2a-jwks-1")
    if not jwks0 or "policies" in jwks0["spec"] or jwks0["spec"]["static"] != {"host": "dex.giantswarm.svc.cluster.local", "port": 5556}:
        fail(f"klausgateway-a2a-jwks-0 {jwks0 and jwks0['spec']!r}")
    if not jwks1 or jwks1["spec"].get("policies") != {"tls": {}} or jwks1["spec"]["static"] != {"host": "login.example.org", "port": 443}:
        fail(f"klausgateway-a2a-jwks-1 must originate TLS on 443 with the system trust, got {jwks1 and jwks1['spec']!r}")
    token = find(docs, "AgentgatewayBackend", "klausgateway-a2a-dex-token")
    if not token or token["spec"] != {"policies": {"tls": {}}, "static": {"host": "dex.ci.example.com", "port": 443}}:
        fail(f"klausgateway-a2a-dex-token must originate TLS to the issuer's host on 443, got {token and token['spec']!r}")
    egress = None
    for d in docs:
        if d["metadata"]["name"].endswith("-dataplane") and d.get("kind") in ("NetworkPolicy", "CiliumNetworkPolicy"):
            egress = d["spec"].get("egress", [])
    if egress is None or "klaus-gateway" not in yaml.safe_dump(egress):
        fail("the data plane's network policy opens no egress to klaus-gateway")
    print("ok: A2A backend, public card route, /a2a token check and exchange, JWKS backends, data plane egress")


def connectivity_in_cluster(docs):
    policy = find(docs, "AgentgatewayPolicy", "klausgateway-a2a")
    exchange = policy["spec"]["backend"]["auth"]["oauthTokenExchange"]
    if exchange.get("path") != "/token" or "url" in exchange:
        fail(f"the exchange must call the backend at /token, got {exchange!r}")
    token = find(docs, "AgentgatewayBackend", "klausgateway-a2a-dex-token")
    want = {
        "policies": {"tls": {"caCertificateRefs": [{"kind": "Secret", "name": "dex-ca"}]}},
        "static": {"host": "dex.giantswarm.svc.cluster.local", "port": 5556},
    }
    if not token or token["spec"] != want:
        fail(f"klausgateway-a2a-dex-token {token and token['spec']!r}, want {want!r}")
    egress = None
    for d in docs:
        if d["metadata"]["name"].endswith("-dataplane") and d.get("kind") in ("NetworkPolicy", "CiliumNetworkPolicy"):
            egress = yaml.safe_dump(d["spec"].get("egress", []))
    if egress is None or "giantswarm" not in egress or "5556" not in egress:
        fail("the data plane's network policy opens no egress to the in-cluster Dex token endpoint")
    print("ok: an in-cluster Dex token endpoint with a private CA: TLS backend with the CA, data plane egress")


def off(docs):
    names = [d["metadata"]["name"] for d in docs if "klausgateway-a2a" in d["metadata"]["name"]]
    if names:
        fail(f"the A2A entry is off but renders {names!r}")
    print("ok: nothing of the A2A entry renders while it is off")


def connectivity_ca(docs):
    name, key = sys.argv[3:5]
    for backend_name in ("klausgateway-a2a-jwks-0", "klausgateway-a2a-dex-token"):
        backend = find(docs, "AgentgatewayBackend", backend_name)
        references = backend["spec"]["policies"]["tls"]["caCertificateRefs"]
        if len(references) != 1 or references[0].get("name") != name or references[0].get("key", "ca.crt") != key:
            fail(f"{backend_name} CA references {references!r}, want Secret {name!r} key {key!r}")
    public = find(docs, "AgentgatewayBackend", "klausgateway-a2a-jwks-1")
    if public["spec"]["policies"]["tls"] != {}:
        fail("the outside issuer must retain system trust when only port 443 implies TLS")
    print("ok: A2A JWKS and token endpoint preserve the selected CA Secret key")


def gateway_values(docs):
    release = find(docs, "HelmRelease", "klaus-gateway")
    if not release:
        fail("no klaus-gateway HelmRelease in the render")
    return release["spec"].get("values", {})


def meta(docs, private_ca=False, trailing_slash=False, ca_key="issuer.pem"):
    meta_namespace(docs)
    meta_starter(docs, "x_agent-manager_")
    values = gateway_values(docs)
    got = values.get("a2aServer")
    if values.get("a2a", {}).get("enabled") is not True:
        fail("the A2A server entry must enable the gateway's A2A client")
    muster = find(docs, "HelmRelease", "muster")
    namespace = muster["spec"].get("targetNamespace") or muster["metadata"]["namespace"]
    if values.get("obo", {}).get("musterUrl") != f"http://muster.{namespace}.svc.cluster.local:8090":
        fail("A2A-only session starts need the platform's muster URL")
    want = {
        "enabled": True,
        "publicUrl": "https://a2a.ci.example.com",
        "issuers": [
            {"name": "dex", "issuer": "https://dex.ci.example.com", "audience": "klaus-gateway-app", "tokenUrl": "https://dex.ci.example.com/token"},
            {"name": "example-idp", "issuer": "https://login.example.org", "audience": "platform-backend"},
        ],
        "token": {
            "issuer": "https://dex.ci.example.com",
            "jwksUrl": "https://dex.ci.example.com/keys",
            "audience": "klaus-gateway",
            "allowedClients": ["klaus-gateway-exchange"],
        },
    }
    if private_ca:
        want["caSecret"] = "dex-ca"
        want["caSecretKey"] = ca_key
    if trailing_slash:
        want["token"]["issuer"] += "/"
    if got != want:
        fail(f"a2aServer {got!r}, want {want!r}")
    print("ok: the klaus-gateway release's a2aServer is derived from the entry")


def meta_off(docs):
    got = gateway_values(docs).get("a2aServer")
    if got:
        fail(f"the A2A entry is off but a2aServer {got!r} is forwarded")
    if gateway_values(docs).get("a2a", {}).get("agentManagerToolsPrefix"):
        fail("an entry that is off must not derive the session starter's tool prefix")
    print("ok: no a2aServer forwarded while the entry is off")


def meta_starter(docs, prefix, muster_url=None):
    values = gateway_values(docs)
    if values.get("a2a", {}).get("agentManagerToolsPrefix", "") != prefix:
        fail(f"the A2A session starter does not use tool prefix {prefix!r}")
    if muster_url is None:
        muster = find(docs, "HelmRelease", "muster")
        namespace = muster["spec"].get("targetNamespace") or muster["metadata"]["namespace"]
        muster_url = f"http://muster.{namespace}.svc.cluster.local:8090"
    if values.get("obo", {}).get("musterUrl") != muster_url:
        fail(f"the A2A session starter does not use muster {muster_url!r}")
    print("ok: A2A session-start tools use the configured MCP registration and muster")


def meta_namespace(docs, own_target=None):
    gateway = find(docs, "HelmRelease", "klaus-gateway")
    connectivity_release = find(docs, "HelmRelease", "agent-platform-connectivity")
    gateway_namespace = gateway["spec"].get("targetNamespace") or gateway["metadata"]["namespace"]
    connectivity_namespace = connectivity_release["spec"].get("targetNamespace") or connectivity_release["metadata"]["namespace"]
    roster = connectivity_release["spec"]["values"]["components"]["klaus-gateway"]
    if (roster.get("targetNamespace") or connectivity_namespace) != gateway_namespace:
        fail("the connectivity roster does not name klaus-gateway's effective release namespace")
    values = connectivity_release["spec"]["values"]
    gateway_name = values["gateway"]["name"]
    gateway_port = values["gateway"]["listeners"][0]["port"]
    target = own_target or f"grpc://{gateway_name}.{connectivity_namespace}.svc.cluster.local:{gateway_port}"
    if gateway["spec"]["values"]["a2a"]["url"] != target:
        fail("the A2A client does not target the effective agentgateway Service")
    print("ok: connectivity targets klaus-gateway's effective release namespace")


def main():
    mode, path = sys.argv[1], sys.argv[2]
    if mode == "--meta-starter":
        meta_starter(load(path), sys.argv[3], sys.argv[4] if len(sys.argv) > 4 else None)
        return
    if mode == "--meta-own-ca-key":
        meta(load(path), private_ca=True, ca_key="gateway.pem")
        return
    if mode == "--connectivity-ca":
        connectivity_ca(load(path))
        return
    {"--connectivity": connectivity, "--connectivity-in-cluster": connectivity_in_cluster, "--off": off, "--meta": meta, "--meta-private-ca": lambda docs: meta(docs, private_ca=True), "--meta-trailing-slash": lambda docs: meta(docs, trailing_slash=True), "--meta-namespace": meta_namespace, "--meta-own-target": lambda docs: meta_namespace(docs, own_target="grpcs://gateway.private.example:8443"), "--meta-off": meta_off}[mode](load(path))


if __name__ == "__main__":
    main()
