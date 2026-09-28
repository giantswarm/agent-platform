#!/usr/bin/env python3
"""Assert the three shapes of the bundled mcp-kubernetes registration (giantswarm/agent-platform#403).

The connectivity chart registers the bundled mcp-kubernetes with muster
(templates/mcp-kubernetes/mcpserver.yaml); mcp-kubernetes.mcpServer picks the shape:
  - default: the family-less singleton mcp-kubernetes, no management-cluster label,
    no spec.family (unchanged);
  - managementCluster: <name>: a member of muster's kubernetes family, exactly as
    agent-platform-mcps renders one — <name>-mcp-kubernetes, the
    muster.giantswarm.io/management-cluster label, spec.family {name: kubernetes,
    instanceArg: management_cluster}, url and auth unchanged;
  - enabled: false: no MCPServer;
  - a managementCluster that is not a DNS label fails the render naming the key;
  - with the component or muster off nothing renders, whatever the block says;
  - the meta chart forwards the block to the connectivity release and drops it from
    the mcp-kubernetes release's values (the chart validates strictly).

Usage: verify-mcp-kubernetes-registration.py <meta chart dir> <connectivity chart dir>
"""
import subprocess
import sys

import yaml

CONN_BASE = ["--set", "ingress.parentRefs[0].name=x", "--set", "components.mcp-kubernetes.enabled=true", "--set", "components.muster.enabled=true"]
CLUSTER_LABEL = "muster.giantswarm.io/management-cluster"
TIER_LABEL = "agent-platform.giantswarm.io/tool-group"


def fail(msg: str) -> None:
    sys.exit(f"FAIL: {msg}")


def render(chart: str, args: list[str], expect_fail: bool = False) -> list[dict] | str:
    r = subprocess.run(["helm", "template", "t", chart, *args], capture_output=True, text=True)
    if expect_fail:
        if r.returncode == 0:
            fail(f"render with {args} succeeded, want a failure")
        return r.stderr
    if r.returncode != 0:
        fail(f"render with {args} failed:\n{r.stderr}")
    return [d for d in yaml.safe_load_all(r.stdout) if d]


def mcpservers(docs: list[dict]) -> list[dict]:
    return [d for d in docs if d.get("kind") == "MCPServer" and d["metadata"].get("labels", {}).get("muster.giantswarm.io/type") == "mcp-kubernetes"]


def main() -> None:
    meta, conn = sys.argv[1], sys.argv[2]

    default = mcpservers(render(conn, CONN_BASE))
    if len(default) != 1:
        fail(f"default: want one mcp-kubernetes MCPServer, got {len(default)}")
    single = default[0]
    if single["metadata"]["name"] != "mcp-kubernetes" or CLUSTER_LABEL in single["metadata"]["labels"] or "family" in single["spec"]:
        fail(f"default: want the family-less singleton mcp-kubernetes, got {single['metadata']} {single['spec']}")
    if single["metadata"]["labels"].get(TIER_LABEL) != "infrastructure":
        fail("default: the singleton lost its infrastructure tool-group label")

    member = mcpservers(render(conn, CONN_BASE + ["--set", "mcp-kubernetes.mcpServer.managementCluster=agentlab"]))
    if len(member) != 1:
        fail(f"member: want one MCPServer, got {len(member)}")
    m = member[0]
    if m["metadata"]["name"] != "agentlab-mcp-kubernetes":
        fail(f"member: name {m['metadata']['name']}, want agentlab-mcp-kubernetes")
    if m["metadata"]["labels"].get(CLUSTER_LABEL) != "agentlab" or m["metadata"]["labels"].get(TIER_LABEL) != "infrastructure":
        fail(f"member: labels {m['metadata']['labels']}")
    if m["spec"].get("family") != {"name": "kubernetes", "instanceArg": "management_cluster"}:
        fail(f"member: spec.family {m['spec'].get('family')}")
    rest = {k: v for k, v in m["spec"].items() if k != "family"}
    if rest != single["spec"]:
        fail(f"member: spec differs from the singleton's beyond family:\n{rest}\n{single['spec']}")

    if mcpservers(render(conn, CONN_BASE + ["--set", "mcp-kubernetes.mcpServer.enabled=false"])):
        fail("enabled=false: an mcp-kubernetes MCPServer rendered")
    for off in ("components.mcp-kubernetes.enabled=false", "components.muster.enabled=false"):
        if mcpservers(render(conn, CONN_BASE + ["--set", off, "--set", "mcp-kubernetes.mcpServer.managementCluster=agentlab"])):
            fail(f"{off}: an mcp-kubernetes MCPServer rendered")

    err = render(conn, CONN_BASE + ["--set", "mcp-kubernetes.mcpServer.managementCluster=Not_A_Label"], expect_fail=True)
    if "mcp-kubernetes.mcpServer.managementCluster" not in err:
        fail(f"a bad managementCluster: the failure does not name the key:\n{err}")

    docs = render(meta, ["-f", f"{meta}/ci/ci-values.yaml", "--set", "components.flux.enabled=false", "--set", "components.mcp-kubernetes.enabled=true", "--set", "mcp-kubernetes.mcpServer.managementCluster=agentlab"])
    releases = {d["metadata"]["name"]: d for d in docs if d.get("kind") == "HelmRelease"}
    if "mcpServer" in releases["mcp-kubernetes"]["spec"].get("values", {}):
        fail("meta: mcp-kubernetes.mcpServer reaches the mcp-kubernetes release")
    forwarded = releases["agent-platform-connectivity"]["spec"]["values"].get("mcp-kubernetes", {}).get("mcpServer")
    if forwarded != {"managementCluster": "agentlab"}:
        fail(f"meta: the connectivity release carries mcp-kubernetes.mcpServer {forwarded}")

    print("ok: singleton by default, family member with managementCluster, none with enabled=false; the guard; the meta chart forwards and omits the block")


if __name__ == "__main__":
    main()
