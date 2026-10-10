#!/usr/bin/env python3
"""Assert the workspace-manager's public sign-in route (giantswarm/agent-platform#885).

A person connects a workspace provider in the browser: the manager signs the
browser in to Dex at <baseURL>/signin, starts the provider's consent at
<baseURL>/connect/<instance>, and the provider redirects back to
<baseURL>/callback/<instance>. Those three paths are the manager's only public
surface. Each case below pins one property:

- the connectivity chart routes exactly /signin (Exact), /connect and /callback
  (PathPrefix) on the base URL's host — derived https://workspace-manager.<global.domain>,
  or the host of workspace-manager.oauth.baseURL, its port dropped — straight to the
  manager's Service, on muster's public parents (ingress.parentRefs) unless the route
  names its own; with the route off, or no host, no route;
- on that host, a Gateway API match over every HTTPRoute of the render routes
  /signin, /connect/<i> and /callback/<i> to the manager and nothing else of the
  manager's: MCP, the token exchange, JWKS, its own OAuth server and the probes are
  not routed;
- the network policies admit the route's Gateway on the Service port, both flavors:
  the Envoy edge of the parents' namespaces, or the agentgateway data plane when it
  is the edge; none with the route off;
- the sign-in keys Secret workspace-manager.signin.keys names: with providers and
  oauth.enabled, ONE Secret holding the current key id with 32 random bytes, kept on
  uninstall; none without providers, with oauth off or with signinKeys.create false;
- the meta chart forwards the resolved base URL to the component release
  (oauth.baseURL, derived or the explicit one) with the sign-in keys' names, and
  NOTES prints the Dex redirect URI <baseURL>/signin and each provider instance's
  callback URL;
- the guards, in both charts: a provider instance with an OAuth client and no
  callback host, with the route off and no explicit base URL, or with a base URL a
  browser cannot reach (a cluster name, http off loopback); a bad key id or no
  Secret name.

Deliberately stdlib-only: the CI image has no PyYAML. HELM selects the binary.
"""

import base64
import os
import re
import subprocess
import sys
import tempfile

HELM = os.environ.get("HELM", "helm")
NAME = "workspace-manager"
FIXTURE = "tests/fixtures/workspaces-values.yaml"
BASE = "https://workspaces.ci.example.com"
HOST = "workspaces.ci.example.com"
IDENTITY = [
    "--set", "global.domain=ci.example.com",
    "--set", "global.identity.issuerUrl=https://dex.ci.example.com",
    "--set", "global.identity.clientId=platform",
    "--set", "global.identity.existingSecret=platform-oauth",
]
CONN = ["--set", "ingress.parentRefs[0].name=edge", "--set", "ingress.parentRefs[0].namespace=envoy-gateway-system",
        "--set", f"components.{NAME}.enabled=true", "-f", FIXTURE, *IDENTITY]
# The chart-owned edge: the agentgateway data plane is the public Gateway.
EDGE = ["--set", "gatewayApi.gateway.create=true", "--set", "gatewayApi.gateway.tls.secretName=wildcard", "--set", "ingress.parentRefs=null"]
META = ["--set", "components.flux.enabled=false", "-f", FIXTURE]
ROUTED = ("/signin", "/connect/github", "/connect/gitlab", "/callback/github", "/callback/gitlab")
INTERNAL = ("/mcp", "/mcp/", "/token", "/.well-known/jwks.json", "/.well-known/oauth-authorization-server",
            "/.well-known/oauth-protected-resource", "/oauth/token", "/oauth/callback", "/oauth/authorize",
            "/oauth/register", "/healthz", "/readyz", "/metrics", "/", "/signin/x", "/signins", "/callbacks/github",
            "/connector/github")


def fail(msg: str) -> None:
    sys.exit(f"FAIL: {msg}")


def ok(msg: str) -> None:
    print(f"ok: {msg}")


def run(cmd: list, expect_fail: bool = False) -> str:
    r = subprocess.run(cmd, capture_output=True, text=True)
    if expect_fail:
        if r.returncode == 0:
            fail(f"the render passed but had to fail: {' '.join(cmd[3:])}")
        return r.stderr
    if r.returncode != 0:
        fail(f"the render failed: {' '.join(cmd[3:])}\n{r.stderr}")
    return r.stdout


def helm(chart: str, flags: list, expect_fail: bool = False) -> str:
    return run([HELM, "template", "t", chart, *flags], expect_fail)


def notes(chart: str, flags: list) -> str:
    """The rendered NOTES.txt: a client-only dry run against an empty kubeconfig."""
    with tempfile.NamedTemporaryFile("w", suffix=".kubeconfig") as kc:
        kc.write("apiVersion: v1\nkind: Config\nclusters: []\ncontexts: []\nusers: []\n")
        kc.flush()
        out = run([HELM, "install", "t", chart, "--dry-run=client", "--kubeconfig", kc.name, *flags])
    return out.split("\nNOTES:\n", 1)[1] if "\nNOTES:\n" in out else ""


def must_fail(chart: str, flags: list, needle: str, what: str) -> None:
    err = helm(chart, flags, expect_fail=True)
    if needle not in err:
        fail(f"{what} failed for another reason (expected {needle!r}):\n{err}")


def documents(render: str) -> dict:
    """(kind, name) -> the document, for every document that has both."""
    out = {}
    for doc in render.split("\n---\n"):
        kind = re.search(r"^kind: (\S+)", doc, re.M)
        name = re.search(r"^  name: (\S+)", doc, re.M)
        if kind and name:
            out[(kind.group(1), name.group(1))] = doc.rstrip("\n") + "\n"
    return out


def routes(docs: dict) -> list:
    """Every HTTPRoute as {name, hostnames, rules: [{matches: [(type, value)], backends: [name]}]}."""
    out = []
    for (kind, name), doc in docs.items():
        if kind != "HTTPRoute":
            continue
        spec = doc.split("\nspec:\n", 1)[1]
        hosts = re.search(r"^  hostnames:\n((?:    - .*\n)+)", spec, re.M)
        hostnames = [h.strip().strip('"') for h in re.findall(r"- (.*)", hosts.group(1))] if hosts else []
        rules = []
        for chunk in re.split(r"^    - (?=matches:|backendRefs:|filters:)", spec.split("\n  rules:\n", 1)[1], flags=re.M)[1:]:
            matches = re.findall(r"type: (Exact|PathPrefix|RegularExpression)\n\s+value: (\S+)", chunk)
            backends = re.findall(r"backendRefs:\n(?:.*\n)*?\s+- name: (\S+)", chunk)
            rules.append({"matches": matches or [("PathPrefix", "/")], "backends": backends})
        out.append({"name": name, "hostnames": hostnames, "rules": rules})
    return out


def path_matches(kind: str, value: str, path: str) -> bool:
    """Gateway API path matching: Exact, or PathPrefix element by element (a trailing slash ignored)."""
    if kind == "Exact":
        return path == value
    if kind == "PathPrefix":
        prefix = value.rstrip("/")
        return prefix == "" or path == prefix or path.startswith(prefix + "/")
    return re.fullmatch(value, path) is not None


def routed_to(all_routes: list, host: str, path: str) -> set:
    """The backends of every rule of every route serving `host` that matches `path`."""
    out = set()
    for r in all_routes:
        if r["hostnames"] and host not in r["hostnames"]:
            continue
        for rule in r["rules"]:
            if any(path_matches(k, v, path) for k, v in rule["matches"]):
                out.update(rule["backends"])
    return out


def main(meta: str, connectivity: str) -> int:
    # --- the route ------------------------------------------------------------------------
    docs = documents(helm(connectivity, CONN))
    route = docs.get(("HTTPRoute", f"{NAME}-public"))
    if not route:
        fail(f"workspaces on rendered no HTTPRoute {NAME}-public")
    parsed = [r for r in routes(docs) if r["name"] == f"{NAME}-public"][0]
    if parsed["hostnames"] != [HOST]:
        fail(f"the route serves {parsed['hostnames']}, expected [{HOST}]")
    if [sorted(r["matches"]) for r in parsed["rules"]] != [sorted([("Exact", "/signin"), ("PathPrefix", "/connect"), ("PathPrefix", "/callback")])]:
        fail(f"the route's matches are not exactly /signin (Exact), /connect and /callback (PathPrefix):\n{route}")
    if parsed["rules"][0]["backends"] != [NAME] or "          port: 8080\n" not in route:
        fail(f"the route does not proxy straight to the {NAME} Service on 8080:\n{route}")
    if "  parentRefs:\n    - name: edge\n      namespace: envoy-gateway-system\n" not in route:
        fail(f"the route does not attach to muster's public parents (ingress.parentRefs):\n{route}")
    others = [r["name"] for r in routes(docs) if HOST in r["hostnames"] and r["name"] != f"{NAME}-public"]
    if others:
        fail(f"other HTTPRoutes name the sign-in host: {others}")
    ok(f"the route serves {HOST} with exactly /signin (Exact), /connect and /callback (PathPrefix) to {NAME}:8080 on muster's parents")

    # --- what the host routes ----------------------------------------------------------------
    for flags in ([], EDGE):
        everything = routes(documents(helm(connectivity, [*CONN, *flags])))
        for path in ROUTED:
            if routed_to(everything, HOST, path) != {NAME}:
                fail(f"{path} on {HOST} is not routed to {NAME} alone ({sorted(routed_to(everything, HOST, path))}) {' '.join(flags)}")
        for path in INTERNAL:
            if NAME in routed_to(everything, HOST, path):
                fail(f"{path} on {HOST} reaches {NAME}: only the sign-in paths are public {' '.join(flags)}")
        for r in everything:
            if r["name"] != f"{NAME}-public" and any(NAME in rule["backends"] for rule in r["rules"]):
                fail(f"HTTPRoute {r['name']} reaches {NAME}: the sign-in route is its only one")
    ok(f"on {HOST}: {', '.join(ROUTED)} reach {NAME}; MCP, the token exchange, JWKS, the OAuth server, the probes and look-alike paths do not "
       "(front Gateway and chart-owned edge)")

    # --- the host: derived, explicit, with a port; off ---------------------------------------
    derived = documents(helm(connectivity, [*CONN, "--set", f"{NAME}.oauth.baseURL="]))[("HTTPRoute", f"{NAME}-public")]
    if '    - "workspace-manager.ci.example.com"\n' not in derived:
        fail(f"the derived host is not workspace-manager.<global.domain>:\n{derived}")
    ported = documents(helm(connectivity, [*CONN, "--set", f"{NAME}.oauth.baseURL=https://wm.lab.localhost:8443/"]))[("HTTPRoute", f"{NAME}-public")]
    if '    - "wm.lab.localhost"\n' not in ported:
        fail(f"a base URL with a port does not route its host:\n{ported}")
    pinned = documents(helm(connectivity, [*CONN, "--set", "workspaceManager.route.parentRefs[0].name=public",
                                           "--set", "workspaceManager.route.parentRefs[0].namespace=gw"]))[("HTTPRoute", f"{NAME}-public")]
    if "    - name: public\n      namespace: gw\n" not in pinned:
        fail(f"workspaceManager.route.parentRefs does not move the route:\n{pinned}")
    off = documents(helm(connectivity, [*CONN, "--set", "workspaceManager.route.enabled=false"]))
    if ("HTTPRoute", f"{NAME}-public") in off:
        fail("workspaceManager.route.enabled=false still renders the route")
    hostless = documents(helm(connectivity, ["--set", "ingress.parentRefs[0].name=edge", "--set", f"components.{NAME}.enabled=true",
                                             "--set", f"{NAME}.oauth.enabled=false"]))
    if ("HTTPRoute", f"{NAME}-public") in hostless:
        fail("a route renders without a host (no base URL, no global.domain): it would take the paths on every host")
    ok("the host: derived workspace-manager.<global.domain>, the explicit base URL's (port dropped); route.parentRefs moves it; "
       "none with the route off or without a host")

    # --- the network policies ----------------------------------------------------------------
    for flavor, kind in (("cilium", "CiliumNetworkPolicy"), ("kubernetes", "NetworkPolicy")):
        base = [*CONN, "--set", f"networkPolicy.flavor={flavor}"]
        ingress = documents(helm(connectivity, base))[(kind, f"agent-platform-connectivity-{NAME}-ingress")]
        edge = ("app.kubernetes.io/name: envoy\n", "io.kubernetes.pod.namespace: envoy-gateway-system\n") if flavor == "cilium" \
            else ("kubernetes.io/metadata.name: envoy-gateway-system\n", "app.kubernetes.io/name: envoy\n")
        for line in edge:
            if line not in ingress:
                fail(f"{flavor}: the ingress policy does not admit the route's Envoy edge ({line.strip()}):\n{ingress}")
        plane = documents(helm(connectivity, [*base, *EDGE]))
        if "gateway.networking.k8s.io/gateway-name: " not in plane[(kind, f"agent-platform-connectivity-{NAME}-ingress")]:
            fail(f"{flavor}: with the chart-owned edge the ingress policy does not admit the data plane")
        closed = documents(helm(connectivity, [*base, "--set", "workspaceManager.route.enabled=false"]))[(kind, f"agent-platform-connectivity-{NAME}-ingress")]
        if "envoy" in closed or "gateway-name" in closed:
            fail(f"{flavor}: the route off still admits a Gateway:\n{closed}")
    ok("network policies, both flavors: the route's Envoy edge (its parents' namespaces) or the data plane admitted on the Service port; none with the route off")

    # --- the sign-in keys Secret -----------------------------------------------------------------
    secret = docs.get(("Secret", "workspace-manager-signin-keys"))
    if not secret:
        fail("providers with oauth on rendered no Secret workspace-manager-signin-keys")
    data = re.findall(r"^  (\S+): (\S+)$", secret.split("\ndata:\n", 1)[1], re.M)
    if [k for k, _ in data] != ["key-1"] or len(base64.b64decode(data[0][1])) != 32:
        fail(f"the Secret does not hold exactly key-1 with 32 bytes:\n{secret}")
    if "    helm.sh/resource-policy: keep\n" not in secret:
        fail("the sign-in keys Secret is not kept on uninstall")
    rotated = documents(helm(connectivity, [*CONN, "--set", f"{NAME}.signin.keys.current=key-2"]))[("Secret", "workspace-manager-signin-keys")]
    if "  key-2: " not in rotated:
        fail("signin.keys.current does not name the generated key")
    for flags, what in ((["--set", f"{NAME}.providers=null"], "without providers"),
                        (["--set", f"{NAME}.oauth.enabled=false"], "with oauth off"),
                        (["--set", "workspaceManager.signinKeys.create=false"], "with signinKeys.create false")):
        if any(k == "Secret" and "signin" in n for k, n in documents(helm(connectivity, [*CONN, *flags]))):
            fail(f"the sign-in keys Secret renders {what}")
    ok("the sign-in keys Secret: the current key id with 32 random bytes, kept on uninstall, follows signin.keys.current; "
       "none without providers, with oauth off or with signinKeys.create false")

    # --- the meta chart: the forward and NOTES ----------------------------------------------------
    hr = documents(helm(meta, META))[("HelmRelease", NAME)]
    for line in (f"      baseURL: {BASE}\n", "    signin:\n      keys:\n        current: key-1\n        secretName: workspace-manager-signin-keys\n"):
        if line not in hr:
            fail(f"the {NAME} HelmRelease lacks {line!r}")
    derived_hr = documents(helm(meta, [*META, "--set", f"{NAME}.oauth.baseURL=", "--set", "global.domain=example.org"]))[("HelmRelease", NAME)]
    if "      baseURL: https://workspace-manager.example.org\n" not in derived_hr:
        fail("the meta chart does not forward the derived base URL https://workspace-manager.<global.domain>")
    text = notes(meta, META)
    for line in (f"    {BASE}/signin\n", f"    github: {BASE}/callback/github\n", f"    gitlab: {BASE}/callback/gitlab\n"):
        if line not in text:
            fail(f"NOTES lacks {line.strip()!r}:\n{text}")
    ok("meta: the base URL forwarded (explicit, or derived from global.domain) with the sign-in keys' names; NOTES prints the Dex redirect URI "
       "<baseURL>/signin and each instance's callback URL")

    # --- the guards ------------------------------------------------------------------------------
    for chart, flags in ((meta, META), (connectivity, [*CONN, "--set", "global.domain="])):
        must_fail(chart, [*flags, "--set", f"{NAME}.oauth.baseURL="], "callback has no host", f"{chart}: an OAuth client without a host")
    for chart, flags in ((meta, [*META, "--set", "global.domain=example.org"]), (connectivity, CONN)):
        must_fail(chart, [*flags, "--set", f"{NAME}.oauth.baseURL=", "--set", "workspaceManager.route.enabled=false"],
                  "nothing routes", f"{chart}: the route off without an explicit base URL")
        helm(chart, [*flags, "--set", "workspaceManager.route.enabled=false"])
        for url in ("http://workspace-manager.agent-platform.svc:8080", "https://workspace-manager.agent-platform.svc.cluster.local",
                    "http://workspaces.example.org"):
            must_fail(chart, [*flags, "--set", f"{NAME}.oauth.baseURL={url}"], "is not reachable from the person's browser",
                      f"{chart}: base URL {url}")
        helm(chart, [*flags, "--set", f"{NAME}.oauth.baseURL=http://workspace-manager.lab.localhost:8080"])
        must_fail(chart, [*flags, "--set", f"{NAME}.signin.keys.current=Key_1"], "is not a key id", f"{chart}: a bad key id")
        must_fail(chart, [*flags, "--set", f"{NAME}.signin.keys.secretName="], "signin.keys.secretName is empty", f"{chart}: no Secret name")
    helm(meta, [*META, "--set", f"{NAME}.oauth.baseURL=", "--set", f"{NAME}.providers[0].values.oauth=null",
                "--set", f"{NAME}.providers[1].values.oauth=null"])
    ok("guards, both charts: an OAuth client without a host, the route off without an explicit base URL, a cluster name or http off loopback, "
       "a bad key id, no Secret name; providers without an OAuth client and an explicit base URL with the route off pass")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(f"usage: {sys.argv[0]} <meta chart dir> <connectivity chart dir>")
    sys.exit(main(sys.argv[1], sys.argv[2]))
