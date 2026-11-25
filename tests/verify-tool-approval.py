#!/usr/bin/env python3
"""Assert the platform side of Governing tool calls (giantswarm/giantswarm#38902, slice 7).

- governance: the meta chart renders muster's governance configuration into the
  muster release (muster.governance) from the top-level `governance` value, from
  the muster floor that reads it (agent-platform.muster.governanceFloor): mode
  trusted by default, governed when set; servers agentManager (following
  agent-manager.muster.mcpServer.name) and platformManager
  (giantswarm-platform-manager); an own servers entry stands; an own mode that
  differs fails naming both keys; an unknown mode fails in the schema and, past
  it, in the template; below the floor nothing is forwarded.
- connector-admin: muster's mcpserver-editor Role (the platform's
  connector-admin role) is bound to the configured groups: the Giant Swarm admin
  groups by default, tenants.platformTeam appended, an installation's own list
  standing.
- muster-approval-binder: the connectivity release binds muster's Role
  muster-approval-binder to agent-manager's ServiceAccount and nothing else (one
  subject, its name following agent-manager.serviceAccount.name), in muster's
  namespace; none with agent-manager off or muster off.
- approval: every MCPServer the connectivity chart renders carries explicit
  settings: mcp-kubernetes the Recommended preset (rule Ask, reads Allow, writes
  Ask), kagent's session tools the Allow all preset (rule Allow, every tool
  Allow); a per-tool override reaches spec.approval, null renders none, a value
  outside the CRD's enums, an unknown key fails naming the key.

Stdlib only (the CI image has no PyYAML): documents are matched by text.

Usage: verify-tool-approval.py <meta chart dir> <connectivity chart dir>
"""
from __future__ import annotations

import os
import subprocess
import sys

HELM = os.environ.get("HELM", "helm")
FLOOR_RANGE = "components.muster.versionRange=>=5.65.0 <6.0.0-0"
META = ["--set", "components.flux.enabled=false"]
CONN = [
    "--namespace", "agent-platform",
    "--set", "ingress.parentRefs[0].name=x",
    "--set", "kagent.harness.snapshotLocation=s3://ci-agent-snapshots/agents",
    "--set", "kagent.database.postgres.urlFile=/etc/kagent-db/uri",
    "--set", "global.domain=ci.example.com",
    "--set", "global.identity.issuerUrl=https://dex.ci.example.com",
    "--set", "global.identity.clientId=platform",
    "--set", "global.identity.existingSecret=platform-oauth",
    "--set", "gateway.jwksEgress.enabled=true",
    "--set", "components.kagent.enabled=true",
    "--set", "components.agentgateway.enabled=true",
]
AGENT_MANAGER = ["--set", "components.agent-manager.enabled=true"]
KAGENT_MCP = ["--set", "kagent.controllerRoute.enabled=true"]
MCPK = ["--set", "components.mcp-kubernetes.enabled=true"]


def fail(msg: str) -> None:
    sys.exit(f"FAIL: {msg}")


def render(chart: str, args: list[str], expect_fail: bool = False) -> str:
    r = subprocess.run([HELM, "template", "t", chart, *args], capture_output=True, text=True)
    if expect_fail:
        if r.returncode == 0:
            fail(f"render with {args} succeeded, want a failure")
        return r.stderr
    if r.returncode != 0:
        fail(f"render with {args} failed:\n{r.stderr}")
    return r.stdout


def docs(out: str) -> list[str]:
    return [d for d in ("\n" + out).split("\n---\n") if d.strip()]


def doc(out: str, kind: str, name: str) -> str | None:
    found = [d for d in docs(out) if f"\nkind: {kind}\n" in "\n" + d and f"\n  name: {name}\n" in d]
    if len(found) > 1:
        fail(f"{len(found)} {kind} {name}, want at most one")
    return found[0] if found else None


def block(text: str, header: str) -> str:
    """The lines of `header` and everything indented deeper below it."""
    lines = text.split("\n")
    for i, line in enumerate(lines):
        if line.rstrip() == header.rstrip():
            indent = len(line) - len(line.lstrip())
            out = [line]
            for nxt in lines[i + 1:]:
                if nxt.strip() and len(nxt) - len(nxt.lstrip()) <= indent and not nxt.lstrip().startswith("- "):
                    break
                if nxt.strip() and len(nxt) - len(nxt.lstrip()) < indent:
                    break
                out.append(nxt)
            return "\n".join(out)
    return ""


def muster_release(args: list[str]) -> str:
    hr = doc(render_meta(args), "HelmRelease", "muster")
    if hr is None:
        fail(f"no muster HelmRelease with {args}")
    return hr


def render_meta(args: list[str]) -> str:
    return render(META_DIR, META + args)


def governance(meta_args: list[str]) -> str:
    return block(muster_release(meta_args), "      governance:")


def check_governance() -> None:
    trusted = governance(["--set", FLOOR_RANGE])
    want = "\n".join([
        "      governance:",
        "        mode: trusted",
        "        servers:",
        "          agentManager:",
        "          - agent-manager",
        "          platformManager:",
        "          - giantswarm-platform-manager",
    ])
    if trusted.rstrip() != want:
        fail(f"default governance, want:\n{want}\ngot:\n{trusted}")
    governed = governance(["--set", FLOOR_RANGE, "--set", "governance=governed", "--set", "agent-manager.muster.mcpServer.name=agents"])
    if "mode: governed" not in governed or "          - agents" not in governed:
        fail(f"governed with a renamed agent-manager server:\n{governed}")
    own = governance(["--set", FLOOR_RANGE, "--set", "muster.muster.governance.servers.platformManager[0]=pm"])
    if "          - pm" not in own or "giantswarm-platform-manager" in own:
        fail(f"an own platformManager entry must stand:\n{own}")
    if governance([]):
        fail("below the muster floor the muster release must carry no governance block")
    err = render(META_DIR, META + ["--set", "governance=open"], expect_fail=True)
    if "governance" not in err:
        fail(f"an unknown mode must fail in the schema naming governance:\n{err}")
    err = render(META_DIR, META + ["--set", "governance=open", "--skip-schema-validation"], expect_fail=True)
    if 'governance "open": want trusted or governed' not in err:
        fail(f"an unknown mode must fail in the template naming governance:\n{err}")
    err = render(META_DIR, META + ["--set", FLOOR_RANGE, "--set", "muster.muster.governance.mode=governed"], expect_fail=True)
    if "muster.muster.governance.mode (governed) differs from governance (trusted)" not in err:
        fail(f"an own mode that differs must fail naming both keys:\n{err}")
    connectivity = doc(render_meta([]), "HelmRelease", "agent-platform-connectivity")
    if connectivity and "\n      governance:" in connectivity:
        fail("governance must not reach the connectivity release (omitKeys)")
    print("ok: governance renders muster's governance configuration (trusted by default, governed, servers, own entries), none below the floor, unknown and conflicting modes fail")


def check_connector_admin() -> None:
    editor = block(muster_release([]), "      mcpServerEditor:")
    for group in ("giantswarm-ad:giantswarm-admins", "giantswarm-github:giantswarm:giantswarm-admins"):
        if f"name: {group}" not in editor:
            fail(f"connector admins by default must include {group}:\n{editor}")
    if "system:authenticated" in editor:
        fail(f"connector admins must not default to everyone:\n{editor}")
    team = block(muster_release(["--set", "tenants.platformTeam[0].kind=Group", "--set", "tenants.platformTeam[0].name=customer:platform"]), "      mcpServerEditor:")
    if "name: customer:platform" not in team or "giantswarm-ad:giantswarm-admins" not in team:
        fail(f"tenants.platformTeam must be appended to the connector admins:\n{team}")
    own = block(muster_release(["--set", "muster.rbac.mcpServerEditor.subjects[0].kind=Group", "--set", "muster.rbac.mcpServerEditor.subjects[0].name=ops"]), "      mcpServerEditor:")
    if "name: ops" not in own or "giantswarm-ad:giantswarm-admins" in own:
        fail(f"an installation's own connector admins must stand:\n{own}")
    print("ok: the connector-admin role (muster's mcpserver-editor) binds the Giant Swarm admin groups plus tenants.platformTeam, an own list standing")


def check_binder() -> None:
    out = render(CONN_DIR, CONN + AGENT_MANAGER)
    rb = doc(out, "RoleBinding", "agent-platform:muster-approval-binder")
    if rb is None:
        fail("no RoleBinding agent-platform:muster-approval-binder with agent-manager on")
    for want in ("\n  namespace: agent-platform\n", "\nroleRef:\n  apiGroup: rbac.authorization.k8s.io\n  kind: Role\n  name: muster-approval-binder\n"):
        if want not in rb:
            fail(f"binder RoleBinding lacks {want!r}:\n{rb}")
    subjects = block(rb, "subjects:")
    if subjects.count("- kind:") != 1 or "kind: ServiceAccount" not in subjects or "name: agent-manager\n" not in subjects + "\n" or "namespace: agent-platform" not in subjects:
        fail(f"binder must bind agent-manager's ServiceAccount only:\n{subjects}")
    if "muster-approval-binder" in "".join(d for d in docs(out) if "\nkind: Role\n" in "\n" + d or "\nkind: ClusterRoleBinding\n" in "\n" + d):
        fail("the connectivity chart must not ship the binder Role itself (the muster chart does) or bind it cluster-wide")
    renamed = doc(render(CONN_DIR, CONN + AGENT_MANAGER + ["--set", "agent-manager.serviceAccount.name=am-sa"]), "RoleBinding", "agent-platform:muster-approval-binder")
    if renamed is None or "name: am-sa" not in renamed:
        fail(f"binder must follow agent-manager.serviceAccount.name:\n{renamed}")
    if doc(render(CONN_DIR, CONN), "RoleBinding", "agent-platform:muster-approval-binder"):
        fail("binder RoleBinding with agent-manager off")
    off = render(CONN_DIR, CONN + AGENT_MANAGER + ["--set", "components.muster.enabled=false", "--set", "agent-manager.muster.mcpServer.enabled=false"])
    if doc(off, "RoleBinding", "agent-platform:muster-approval-binder"):
        fail("binder RoleBinding with muster off")
    print("ok: muster-approval-binder is bound to agent-manager's ServiceAccount only, in muster's namespace; none with agent-manager or muster off")


def approval_of(out: str, name: str) -> str:
    cr = doc(out, "MCPServer", name)
    if cr is None:
        fail(f"no MCPServer {name}")
    return block(cr, "  approval:")


def check_approval() -> None:
    mcpk = approval_of(render(CONN_DIR, CONN + MCPK), "mcp-kubernetes")
    if "    rule: Ask" not in mcpk:
        fail(f"mcp-kubernetes: want the Recommended rule Ask:\n{mcpk}")
    for tool in ("get", "list", "describe", "logs", "can_i", "capi_list_clusters"):
        if f"      {tool}: Allow" not in mcpk:
            fail(f"mcp-kubernetes: read-only {tool} must be Allow:\n{mcpk}")
    for tool in ("create", "apply", "delete", "patch", "scale", "exec", "port_forward"):
        if f"      {tool}: Ask" not in mcpk:
            fail(f"mcp-kubernetes: mutating {tool} must be Ask:\n{mcpk}")
    if "Block" in mcpk:
        fail("mcp-kubernetes: the default blocks nothing")
    kagent = approval_of(render(CONN_DIR, CONN + KAGENT_MCP), "kagent")
    if "    rule: Allow" not in kagent or "Ask" in kagent or "Block" in kagent:
        fail(f"kagent: want the Allow all preset:\n{kagent}")
    for tool in ("list_sessions", "invoke_session", "fork_session", "write_sandbox_file"):
        if f"      {tool}: Allow" not in kagent:
            fail(f"kagent: {tool} must be listed Allow:\n{kagent}")
    tightened = approval_of(render(CONN_DIR, CONN + MCPK + ["--set", "mcp-kubernetes.mcpServer.approval.tools.exec=Block", "--set", "mcp-kubernetes.mcpServer.managementCluster=lab"]), "lab-mcp-kubernetes")
    if "      exec: Block" not in tightened:
        fail(f"a per-tool override must reach spec.approval of the family member:\n{tightened}")
    cleared = render(CONN_DIR, CONN + MCPK + ["--set-json", "mcp-kubernetes.mcpServer.approval=null"])
    if approval_of(cleared, "mcp-kubernetes"):
        fail("approval null must render no spec.approval")
    for args, want in (
        (["--set", "mcp-kubernetes.mcpServer.approval.rule=Block"], 'mcp-kubernetes.mcpServer.approval.rule "Block": want Allow or Ask'),
        (["--set", "mcp-kubernetes.mcpServer.approval.tools.get=Deny"], 'mcp-kubernetes.mcpServer.approval.tools.get "Deny": want Allow, Ask or Block'),
        (["--set", "mcp-kubernetes.mcpServer.approval.preset=recommended"], "mcp-kubernetes.mcpServer.approval.preset: unknown key"),
    ):
        err = render(CONN_DIR, CONN + MCPK + args, expect_fail=True)
        if want not in err:
            fail(f"{args}: want {want!r}, got:\n{err}")
    err = render(CONN_DIR, CONN + KAGENT_MCP + ["--set", "kagent.controllerRoute.mcp.approval.tools.invoke_session=never"], expect_fail=True)
    if "kagent.controllerRoute.mcp.approval.tools.invoke_session" not in err:
        fail(f"kagent: an invalid value must fail naming the key:\n{err}")
    print("ok: mcp-kubernetes carries the Recommended settings, kagent the Allow all settings, overrides reach spec.approval, null renders none, invalid values fail naming the key")


if __name__ == "__main__":
    META_DIR, CONN_DIR = sys.argv[1], sys.argv[2]
    check_governance()
    check_connector_admin()
    check_binder()
    check_approval()
