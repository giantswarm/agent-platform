#!/usr/bin/env python3
"""Assert the workspace-manager's public route (giantswarm/agent-platform#885).

The grants the manager issues name its base URL as their issuer, so the key set
that verifies them, <baseURL>/.well-known/jwks.json, is the manager's only public
surface; a person's provider sign-in is muster's grant of the provider
instance's App, so no sign-in, connect or callback path and no sign-in keys
Secret is the manager's. Each case below pins one property:

- the connectivity chart routes exactly /.well-known/jwks.json (Exact) on the base
  URL's host — derived https://workspace-manager.<global.domain>, or the host of
  workspace-manager.oauth.baseURL, its port dropped — straight to the manager's
  Service, on muster's public parents (ingress.parentRefs) unless the route names
  its own; with the route off, or no host, no route;
- on that host, a Gateway API match over every HTTPRoute of the render routes
  /.well-known/jwks.json to the manager and nothing else of the manager's: MCP, the
  retired sign-in, connect, callback and token paths, its own OAuth server and its
  metadata, and the probes are not routed;
- the network policies admit the route's Gateway on the Service port, both flavors:
  the Envoy edge of the parents' namespaces, or the agentgateway data plane when it
  is the edge; none with the route off;
- no sign-in keys Secret renders, with providers and oauth.enabled included;
- the meta chart forwards the resolved base URL to the component release
  (oauth.baseURL, derived or the explicit one) and no signin block, and NOTES
  prints the routed key set and muster's pin instead of a Dex redirect URI or
  provider callback URLs;
- a base URL the installation routes itself (the route off) renders.

Deliberately stdlib-only: the CI image has no PyYAML. HELM selects the binary.
"""

import json
import os
import re
import shutil
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
ROUTED = ("/.well-known/jwks.json",)
INTERNAL = ("/signin", "/connect/github", "/connect/gitlab", "/callback/github", "/callback/gitlab", "/mcp", "/mcp/", "/token", "/.well-known/jwks.json/x", "/.well-known/oauth-authorization-server",
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
    """The rendered NOTES.txt: `helm template` skips NOTES, and a dry-run install needs
    a cluster on older Helm, so a copy of the chart renders it inside a ConfigMap."""
    with tempfile.TemporaryDirectory() as tmp:
        copy = os.path.join(tmp, os.path.basename(os.path.normpath(chart)))
        shutil.copytree(chart, copy)
        templates = os.path.join(copy, "templates")
        with open(os.path.join(templates, "NOTES.txt"), encoding="utf-8") as f:
            text = f.read()
        os.remove(os.path.join(templates, "NOTES.txt"))
        with open(os.path.join(templates, "_zz_notes.tpl"), "w", encoding="utf-8") as f:
            f.write('{{- define "zz.notes" -}}\n' + text + "{{- end -}}\n")
        with open(os.path.join(templates, "zz-notes.yaml"), "w", encoding="utf-8") as f:
            f.write('apiVersion: v1\nkind: ConfigMap\nmetadata:\n  name: zz-notes\ndata:\n  notes: {{ include "zz.notes" . | toJson }}\n')
        out = helm(copy, [*flags, "--show-only", "templates/zz-notes.yaml"])
    return json.loads(re.search(r"^  notes: (.*)$", out, re.M).group(1)) + "\n"


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
    if [sorted(r["matches"]) for r in parsed["rules"]] != [[("Exact", "/.well-known/jwks.json")]]:
        fail(f"the route's matches are not exactly /.well-known/jwks.json (Exact):\n{route}")
    if parsed["rules"][0]["backends"] != [NAME] or "          port: 8080\n" not in route:
        fail(f"the route does not proxy straight to the {NAME} Service on 8080:\n{route}")
    if "  parentRefs:\n    - name: edge\n      namespace: envoy-gateway-system\n" not in route:
        fail(f"the route does not attach to muster's public parents (ingress.parentRefs):\n{route}")
    others = [r["name"] for r in routes(docs) if HOST in r["hostnames"] and r["name"] != f"{NAME}-public"]
    if others:
        fail(f"other HTTPRoutes name the manager's host: {others}")
    ok(f"the route serves {HOST} with exactly /.well-known/jwks.json (Exact) to {NAME}:8080 on muster's parents")

    # --- what the host routes ----------------------------------------------------------------
    for flags in ([], EDGE):
        everything = routes(documents(helm(connectivity, [*CONN, *flags])))
        for path in ROUTED:
            if routed_to(everything, HOST, path) != {NAME}:
                fail(f"{path} on {HOST} is not routed to {NAME} alone ({sorted(routed_to(everything, HOST, path))}) {' '.join(flags)}")
        for path in INTERNAL:
            if NAME in routed_to(everything, HOST, path):
                fail(f"{path} on {HOST} reaches {NAME}: only the key set is public {' '.join(flags)}")
        for r in everything:
            if r["name"] != f"{NAME}-public" and any(NAME in rule["backends"] for rule in r["rules"]):
                fail(f"HTTPRoute {r['name']} reaches {NAME}: the key-set route is its only one")
    ok(f"on {HOST}: {', '.join(ROUTED)} reach {NAME}; the retired sign-in, connect, callback and token paths, MCP, the OAuth server and its metadata, the probes and look-alike paths do not "
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

    # --- no sign-in keys Secret ---------------------------------------------------------------
    for flags, what in (([], "with providers and oauth on"), (["--set", "workspaceManager.signinKeys.create=true"], "with the retired signinKeys.create")):
        if any(k == "Secret" and "signin" in n for k, n in documents(helm(connectivity, [*CONN, *flags]))):
            fail(f"a sign-in keys Secret renders {what}: the person's provider token is muster's grant")
    ok("no sign-in keys Secret, with providers and oauth on")

    # --- the meta chart: the forward and NOTES ----------------------------------------------------
    hr = documents(helm(meta, META))[("HelmRelease", NAME)]
    if f"      baseURL: {BASE}\n" not in hr:
        fail(f"the {NAME} HelmRelease lacks the base URL {BASE}")
    if "\n    signin:" in hr:
        fail(f"the {NAME} HelmRelease forwards a signin block: the manager keeps no sign-in")
    derived_hr = documents(helm(meta, [*META, "--set", f"{NAME}.oauth.baseURL=", "--set", "global.domain=example.org"]))[("HelmRelease", NAME)]
    if "      baseURL: https://workspace-manager.example.org\n" not in derived_hr:
        fail("the meta chart does not forward the derived base URL https://workspace-manager.<global.domain>")
    text = notes(meta, META)
    for line in (f"Workspaces, base URL {BASE} (routed: /.well-known/jwks.json):\n",
                 "  Provider sign-in: muster, pinned to provider instance github's App (issuer https://github.com/apps/ci-agents, OAuth client in Secret workspace-github)"):
        if line not in text:
            fail(f"NOTES lacks {line.strip()!r}:\n{text}")
    for gone in (f"{BASE}/signin", f"{BASE}/callback/"):
        if gone in text:
            fail(f"NOTES still prints {gone}")
    ok("meta: the base URL forwarded (explicit, or derived from global.domain), no signin block; NOTES prints the key set and muster's pin, "
       "no Dex redirect URI and no provider callback URL")

    # --- a route of the installation's own -------------------------------------------------------
    for chart, flags in ((meta, [*META, "--set", "global.domain=example.org"]), (connectivity, CONN)):
        helm(chart, [*flags, "--set", "workspaceManager.route.enabled=false"])
        helm(chart, [*flags, "--set", f"{NAME}.oauth.baseURL=http://workspace-manager.lab.localhost:8080"])
    ok("both charts: the route off with an explicit base URL and a loopback base URL render")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(f"usage: {sys.argv[0]} <meta chart dir> <connectivity chart dir>")
    sys.exit(main(sys.argv[1], sys.argv[2]))
