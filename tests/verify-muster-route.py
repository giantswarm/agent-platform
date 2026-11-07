#!/usr/bin/env python3
"""Assert that the agentgateway data plane has a route to muster wherever muster is on.

In the one ingress topology the public /mcp reaches the agentgateway data plane,
whose only route to muster is the hostless HTTPRoute the agent-platform-mcps
release renders (agentgateway.viaMuster). The meta chart turns that release on
with muster, so for the default values and the examples named below this
renders the meta chart, takes the agent-platform-mcps HelmRelease's values,
renders the published agent-platform-mcps chart (components.agent-platform-mcps
.versionRange) with them and asserts: an HTTPRoute on the data-plane Gateway
whose `/` prefix (a /mcp request included) goes to an AgentgatewayBackend whose
one MCP target is muster's /mcp. Both charts refuse muster on with that release
off, and a slice without muster renders no such release.

Usage: verify-muster-route.py META_CHART CONNECTIVITY_CHART SCRATCH_DIR
HELM selects the binary. Needs PyYAML; pulls the chart over the network.
"""

import os
import re
import subprocess
import sys

import yaml

HELM = os.environ.get("HELM", "helm")
META, CONNECTIVITY, SCRATCH = sys.argv[1:4]
BASE = ["--set", "components.flux.enabled=false", "--set", "ingress.parentRefs[0].name=x"]
# The default install and the two published installation shapes (issue #1185).
CASES = {
    "default": [],
    "examples/own-gateway.yaml": ["-f", f"{META}/examples/own-gateway.yaml"],
    "examples/managed-cloud.yaml": ["-f", f"{META}/examples/managed-cloud.yaml"],
    # A null switch is no switch: the component follows muster.
    "enabled: null": ["--set", "components.agent-platform-mcps.enabled=null"],
}
SLICES = ["examples/runtime-slice.yaml", "examples/serving-slice.yaml"]
GUARD = "components.agent-platform-mcps.enabled must be true while muster is on"


def fail(msg: str) -> None:
    print(f"FAIL: {msg}")
    sys.exit(1)


def helm(*args: str, ok: bool = True) -> subprocess.CompletedProcess:
    res = subprocess.run([HELM, *args], capture_output=True, text=True, check=False)
    if ok and res.returncode != 0:
        fail(f"helm {' '.join(args)}:\n{res.stderr}")
    return res


def docs(text: str) -> list[dict]:
    return [d for d in yaml.safe_load_all(text) if d]


def find(objs: list[dict], kind: str, name: str | None = None) -> list[dict]:
    return [o for o in objs if o.get("kind") == kind and (name is None or o["metadata"]["name"] == name)]


def version_range() -> str:
    values = yaml.safe_load(open(f"{META}/values.yaml"))
    return values["components"]["agent-platform-mcps"]["versionRange"]


def pull() -> str:
    dest = os.path.join(SCRATCH, "vmr-chart")
    subprocess.run(["rm", "-rf", dest], check=True)
    helm("pull", "oci://gsoci.azurecr.io/charts/giantswarm/agent-platform-mcps",
         "--version", version_range(), "--untar", "--untardir", dest)
    return os.path.join(dest, "agent-platform-mcps")


def gateway_name(objs: list[dict]) -> str:
    conn = find(objs, "HelmRelease", "agent-platform-connectivity")
    if not conn:
        fail("no agent-platform-connectivity HelmRelease")
    default = yaml.safe_load(open(f"{CONNECTIVITY}/values.yaml"))["gateway"]["name"]
    return (conn[0]["spec"].get("values", {}).get("gateway") or {}).get("name") or default


def assert_route(case: str, flags: list[str], chart: str) -> None:
    meta = docs(helm("template", "t", META, *BASE, *flags).stdout)
    hr = find(meta, "HelmRelease", "agent-platform-mcps")
    if not hr:
        fail(f"{case}: muster is on and no agent-platform-mcps HelmRelease renders, so /mcp reaches nothing")
    spec = hr[0]["spec"]
    values_file = os.path.join(SCRATCH, "vmr-values.yaml")
    with open(values_file, "w") as f:
        yaml.safe_dump(spec.get("values", {}), f)
    objs = docs(helm("template", spec.get("releaseName", "agent-platform-mcps"), chart,
                     "--namespace", spec.get("targetNamespace", "agent-platform"), "-f", values_file).stdout)
    gw = gateway_name(meta)
    backends = {b["metadata"]["name"]: b for b in find(objs, "AgentgatewayBackend")}
    for route in find(objs, "HTTPRoute"):
        if not any(p.get("kind", "Gateway") == "Gateway" and p["name"] == gw for p in route["spec"]["parentRefs"]):
            continue
        if route["spec"].get("hostnames"):
            continue
        for rule in route["spec"]["rules"]:
            prefixes = [m["path"]["value"] for m in rule.get("matches", []) if m.get("path", {}).get("type") == "PathPrefix"]
            if not any("/mcp".startswith(p.rstrip("/") + "/") or p == "/mcp" for p in prefixes):
                continue
            for ref in rule.get("backendRefs", []):
                backend = backends.get(ref["name"]) if ref.get("kind") == "AgentgatewayBackend" else None
                targets = (backend or {}).get("spec", {}).get("mcp", {}).get("targets", [])
                if len(targets) == 1 and re.match(r"^muster\.", targets[0]["static"]["host"]) and targets[0]["static"].get("path") == "/mcp":
                    t = targets[0]["static"]
                    print(f"ok: {case}: HTTPRoute {route['metadata']['name']} on Gateway {gw} sends /mcp to {t['host']}:{t['port']}{t['path']}")
                    return
    fail(f"{case}: no hostless HTTPRoute on the data-plane Gateway {gw} sends /mcp to muster's /mcp")


def assert_guard(chart: str, flags: list[str]) -> None:
    res = helm("template", "t", chart, *flags, "--set", "components.agent-platform-mcps.enabled=false", ok=False)
    if res.returncode == 0:
        fail(f"{chart}: muster on with agent-platform-mcps off rendered")
    if GUARD not in res.stderr:
        fail(f"{chart}: muster on with agent-platform-mcps off refused for the wrong reason:\n{res.stderr}")
    print(f"ok: {chart} refuses muster on with agent-platform-mcps off")


def main() -> None:
    chart = pull()
    for case, flags in CASES.items():
        assert_route(case, flags, chart)
    assert_guard(META, BASE)
    assert_guard(CONNECTIVITY, ["--set", "ingress.parentRefs[0].name=x"])
    for s in SLICES:
        # A slice is layered over its installation's identity provider, as the
        # examples say (the kagent controller needs the people's issuer).
        meta = docs(helm("template", "t", META, *BASE, "-f", f"{META}/{s}",
                         "--set", "global.identity.issuerUrl=https://dex.ci.example.com").stdout)
        if find(meta, "HelmRelease", "agent-platform-mcps"):
            fail(f"{s}: a slice without muster renders an agent-platform-mcps release (it dependsOn muster)")
        print(f"ok: {s}: no muster, no agent-platform-mcps release")


main()
