#!/usr/bin/env python3
"""Assert the kagent controller's A2A HTTP routes in a connectivity render.

The controller serves A2A JSON-RPC at POST /agents/<namespace>/<name> and the
agent card at GET /agents/<namespace>/<name>/.well-known/agent-card.json on its
API port. The data plane carries them on the HTTPRoute kagent-a2a (hostname-less,
the kagent AgentgatewayBackend, behind kagent-a2a-jwt, the twin of
kagent-controller-jwt) and the public Gateway on kagent-a2a-public (the public
GRPCRoute's hostname and parents, forwarded to the agentgateway Service, under
the public BackendTrafficPolicy).

Usage:
  verify-kagent-a2a-route.py <render>          the default shape (BackendTrafficPolicy on)
  verify-kagent-a2a-route.py --edge <render>   the edge as data plane: no public route
  verify-kagent-a2a-route.py --no-jwt <render> the JWT policy off: the route stays, no policy
"""

import sys

import yaml


def fail(msg: str) -> None:
    sys.exit(f"FAIL: {msg}")


def ok(msg: str) -> None:
    print(f"ok: {msg}")


def load(path: str) -> list:
    with open(path, encoding="utf-8") as f:
        return [d for d in yaml.safe_load_all(f) if d]


def find(docs: list, kind: str, name: str):
    hits = [d for d in docs if d.get("kind") == kind and d["metadata"]["name"] == name]
    return hits[0] if len(hits) == 1 else None


def need(docs: list, kind: str, name: str) -> dict:
    d = find(docs, kind, name)
    if d is None:
        fail(f"{kind} {name} missing from the render (or rendered more than once)")
    return d


RULE_MATCHES = [{"path": {"type": "PathPrefix", "value": "/agents/"}}]
INNER_BACKEND = [{"name": "kagent", "kind": "AgentgatewayBackend", "group": "agentgateway.dev"}]


def check_inner(docs: list) -> None:
    route = need(docs, "HTTPRoute", "kagent-a2a")
    spec = route["spec"]
    if "hostnames" in spec:
        fail("the kagent-a2a HTTPRoute is hostname-scoped (the edge listener and the forwarded public hop need it hostname-less, like the GRPCRoute)")
    grpc = need(docs, "GRPCRoute", "kagent-controller")
    if spec["parentRefs"] != grpc["spec"]["parentRefs"]:
        fail(f"kagent-a2a attaches to {spec['parentRefs']}, not the inner GRPCRoute's parents {grpc['spec']['parentRefs']}")
    if [r["matches"] for r in spec["rules"]] != [RULE_MATCHES]:
        fail(f"kagent-a2a does not carry exactly one rule matching PathPrefix /agents/: {spec['rules']}")
    if spec["rules"][0]["backendRefs"] != INNER_BACKEND:
        fail(f"kagent-a2a does not forward to the kagent AgentgatewayBackend: {spec['rules'][0]['backendRefs']}")
    ok("kagent-a2a: hostname-less, PathPrefix /agents/ only, the inner GRPCRoute's parents, the kagent AgentgatewayBackend")

    others = [d["metadata"]["name"] for d in docs if d.get("kind") == "HTTPRoute" and d["metadata"]["name"] not in ("kagent-a2a", "kagent-a2a-public")
              and any(m.get("path", {}).get("value", "").startswith("/agents") for r in d["spec"].get("rules", []) for m in r.get("matches", []))]
    if others:
        fail(f"another HTTPRoute also matches /agents: {others}")
    ok("no other HTTPRoute matches /agents")


def check_policy(docs: list) -> None:
    policy = need(docs, "AgentgatewayPolicy", "kagent-a2a-jwt")
    twin = need(docs, "AgentgatewayPolicy", "kagent-controller-jwt")
    want = [{"group": "gateway.networking.k8s.io", "kind": "HTTPRoute", "name": "kagent-a2a"}]
    if policy["spec"]["targetRefs"] != want:
        fail(f"kagent-a2a-jwt targets {policy['spec']['targetRefs']}, want {want}")
    if policy["spec"]["traffic"] != twin["spec"]["traffic"]:
        fail("kagent-a2a-jwt's traffic block differs from kagent-controller-jwt's")
    if policy["spec"]["traffic"]["jwtAuthentication"]["mode"] != "Strict":
        fail("kagent-a2a-jwt is not Strict")
    if twin["spec"]["targetRefs"] != [{"group": "gateway.networking.k8s.io", "kind": "GRPCRoute", "name": "kagent-controller"}]:
        fail(f"kagent-controller-jwt's targets changed: {twin['spec']['targetRefs']}")
    ok("kagent-a2a-jwt: on kagent-a2a, Strict, the same JWT, claim requirement and x-user-id transformation as kagent-controller-jwt")


def check_public(docs: list) -> None:
    route = need(docs, "HTTPRoute", "kagent-a2a-public")
    grpc = need(docs, "GRPCRoute", "kagent-controller-public")
    spec = route["spec"]
    if spec.get("hostnames") != grpc["spec"]["hostnames"]:
        fail(f"kagent-a2a-public serves {spec.get('hostnames')}, not the public GRPCRoute's {grpc['spec']['hostnames']}")
    if spec["parentRefs"] != grpc["spec"]["parentRefs"]:
        fail(f"kagent-a2a-public attaches to {spec['parentRefs']}, not the public GRPCRoute's parents {grpc['spec']['parentRefs']}")
    if [r["matches"] for r in spec["rules"]] != [RULE_MATCHES]:
        fail(f"kagent-a2a-public does not carry exactly one rule matching PathPrefix /agents/: {spec['rules']}")
    refs = spec["rules"][0]["backendRefs"]
    if refs != grpc["spec"]["rules"][0]["backendRefs"]:
        fail(f"kagent-a2a-public forwards to {refs}, not where the public GRPCRoute forwards")
    ok(f"kagent-a2a-public: {spec['hostnames']}, the public GRPCRoute's parents and backend {refs}")

    btp = need(docs, "BackendTrafficPolicy", "kagent-controller-public")
    want = {"group": "gateway.networking.k8s.io", "kind": "HTTPRoute", "name": "kagent-a2a-public"}
    if want not in btp["spec"]["targetRefs"]:
        fail(f"the public BackendTrafficPolicy does not target kagent-a2a-public: {btp['spec']['targetRefs']}")
    ok("the public BackendTrafficPolicy targets kagent-a2a-public beside the public GRPCRoute")


def main() -> None:
    mode, path = (sys.argv[1], sys.argv[2]) if len(sys.argv) == 3 else ("", sys.argv[1])
    docs = load(path)
    check_inner(docs)
    if mode == "--no-jwt":
        if find(docs, "AgentgatewayPolicy", "kagent-a2a-jwt") is not None:
            fail("kagent-a2a-jwt renders with jwtAuthentication.enabled=false")
        ok("the JWT policy off: kagent-a2a stays, no kagent-a2a-jwt")
        return
    check_policy(docs)
    if mode == "--edge":
        if find(docs, "HTTPRoute", "kagent-a2a-public") is not None:
            fail("kagent-a2a-public renders with the edge as data plane")
        ok("the edge as data plane: no kagent-a2a-public")
        return
    check_public(docs)


if __name__ == "__main__":
    main()
