#!/usr/bin/env python3
"""Assert the platform side of Governing tool calls.

- governance: the meta chart renders muster's governance configuration into the
  muster release (muster.governance: mode and governedServers {server, role,
  safe}) from the top-level `governance` and `governedServers` values, from the
  muster floor that reads it (agent-platform.muster.governanceFloor): mode
  trusted by default, governed when set; one entry per MCPServer a source ships
  while it renders, keyed as muster keys servers (name in muster's namespace,
  namespace/name outside it); an own governedServers entry stands; the earlier
  servers.<role> shape, an own mode that differs, an unknown key or an unknown
  mode fail; below the floor nothing is forwarded and `governed` fails.
- coverage: every MCPServer source the platform ships (the manager components
  whose release values carry muster.mcpServer, the connectivity chart's
  MCPServers, agent-platform-mcps' families and, where the component checkouts
  sit beside this repository, every chart that renders an MCPServer) is in
  governedServers or in governanceExempt; no safe entry is a floor; every safe
  entry names a tool of its server (the connectivity chart's approval maps
  here, the component charts' where their checkouts are beside this one); the
  connectivity and agent-platform-mcps kubernetes maps are equal.
- approval configuration: from the same floor the muster release carries
  muster.approval.installation (backstage.installationName, default
  agent-platform; an own value and an own ttl stand) and the redirect allowlist
  muster.oauth.mcpClient.postLoginRedirectAllowlist: the portal's
  https://<portal host>[:<backstage.publicPort>]/sessions while the portal is on
  and has a host (no port for 443), and klaus-gateway's
  <obo.callbackBaseUrl>/auth/slack/return with its port while klaus-gateway is
  on and names it; invalid ports and URLs fail; own entries stay, first, once
  each; none below the floor.
- governance floor: agent-platform.muster.governanceFloor is one X.Y.Z; its
  PLACEHOLDER-GOVERNANCE-FLOOR marker, while it is still there, appears once.
- connector-admin: muster's mcpserver-editor Role (the platform's
  connector-admin role) is bound to the configured groups: by default a
  ServiceAccount subject no identity provider can issue a token for
  (kube-system/agent-platform-unset-connector-admins; never empty, so muster's
  upgrade guard passes, never everyone, never a group name), tenants.platformTeam
  appended, an installation's own list standing.
- role floors: governance.roleFloors renders muster.governance.roleFloors (a
  role an installation declares for its own server, usable in
  governedServers); malformed values and a second definition in
  muster.muster.governance.roleFloors fail; the default ships no server or
  role outside the platform's own components.
- muster-approval-binder: the muster release binds muster's Role
  muster-approval-binder (in muster's namespace, the muster chart's) to
  agent-manager's ServiceAccount and nothing else
  (muster.rbac.approvalBinder.subjects, its name and namespace following
  agent-manager's); an own list stands; a ServiceAccount the chart does not
  create needs a name; none with agent-manager off or below the floor; the
  connectivity chart ships no binding of its own.
- approval: every MCPServer the connectivity chart renders carries explicit
  settings: mcp-kubernetes the Recommended preset (rule Ask, reads Allow, writes
  Ask), kagent's session tools the Allow all preset (rule Allow, every tool
  Allow); a rule Block renders, a per-tool override reaches spec.approval (also
  from the meta chart's values), null renders none, a value outside the CRD's
  enums or an unknown key fails naming the key.

Stdlib only (the CI image has no PyYAML): documents are matched by text.

Usage: verify-tool-approval.py <meta chart dir> <connectivity chart dir>
Environment: HELM selects the binary; COMPONENT_CHECKOUTS (default: the
directory holding this repository) is where the component repositories are
looked for; the checks that need them say when they are skipped.
"""
from __future__ import annotations

import atexit
import os
import re
import shutil
import subprocess
import sys
import tempfile

HELM = os.environ.get("HELM", "helm")
FLOOR_RANGE = ""  # set in main from agent-platform.muster.governanceFloor
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


def governance_floor(meta_dir: str) -> str:
    """The value of agent-platform.muster.governanceFloor, and its one marker."""
    text = open(os.path.join(meta_dir, "templates", "_helpers.tpl")).read()
    m = re.search(r'define "agent-platform\.muster\.governanceFloor" -\}\}((?:[0-9]+\.[0-9]+\.[0-9]+)?)\{\{-', text)
    if not m:
        fail("agent-platform.muster.governanceFloor must be one line holding one X.Y.Z, or nothing while its release does not exist")
    if not m.group(1):
        print("note: the governance floor is unset (PLACEHOLDER-GOVERNANCE-FLOOR): nothing it gates renders; set it in the platform release that pins the muster release carrying tool approval")
        return ""
    markers = text.count("PLACEHOLDER-GOVERNANCE-FLOOR")
    if markers > 1:
        fail("the governance floor placeholder must be marked at most once (PLACEHOLDER-GOVERNANCE-FLOOR) next to its define")
    if markers and m.group(1):
        print(f"note: the governance floor {m.group(1)} is still a placeholder (PLACEHOLDER-GOVERNANCE-FLOOR): move it to the muster release that carries tool approval before releasing")
    values = open(os.path.join(meta_dir, "values.yaml")).read()
    if f">={m.group(1)}" in values:
        fail(f"values.yaml repeats the governance floor {m.group(1)}: it lives in agent-platform.muster.governanceFloor alone")
    return m.group(1)


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


def governed_entries(gov: str) -> dict[str, dict]:
    """server key -> {"role": str, "safe": [str]} of a rendered governance block."""
    out: dict[str, dict] = {}
    cur: dict = {}
    field = ""
    for line in gov.split("\n"):
        st = line.strip()
        if st.startswith("- role: ") or st.startswith("- safe:") or st.startswith("- server: "):
            cur = {"role": "", "safe": [], "server": ""}
            st = st[2:]
        if st.startswith("role: "):
            cur["role"] = st[len("role: "):]
        elif st.startswith("safe:"):
            field = "safe"
        elif st.startswith("server: "):
            cur["server"] = st[len("server: "):]
            out[cur["server"]] = cur
        elif st.startswith("- ") and field == "safe" and cur is not None and line.startswith("          - "):
            cur["safe"].append(st[2:])
    return out


FULL = [
    "--namespace", "agent-platform",
    "--set", "global.domain=example.com",
    "--set", "global.identity.issuerUrl=https://dex.example.com",
    "--set", "global.identity.clientId=agent-platform",
    "--set", "global.identity.existingSecret=agent-platform-idp",
    "--set", "global.gatewayApi.parentRefs[0].name=example-gateway",
    "--set", "model-manager.ollama.endpoint=http://ollama.example.com:11434",
    "--set", "kagent.harness.snapshotLocation=s3://ci-agent-snapshots/agents",
    "--set", "kagent.database.postgres.urlFile=/etc/kagent-db/uri",
    "--set", "postgres.enabled=true",
    "--set", "components.kagent.enabled=true",
    "--set", "components.agent-manager.enabled=true",
]


def check_governance() -> None:
    trusted = governance(["--set", FLOOR_RANGE])
    entries = governed_entries(trusted)
    if "        mode: trusted" not in trusted:
        fail(f"default governance, want mode trusted:\n{trusted}")
    if entries.get("model-manager", {}).get("role") != "modelManager":
        fail(f"default governance must govern model-manager as modelManager:\n{trusted}")
    roles = {e.get("role") for e in entries.values()}
    if not roles <= {"agentManager", "sessionManager", "modelManager", "clusterManager", "vmManager", "toolchainManager", "workspaceManager", "skillManager", "infrastructure"}:
        fail(f"the default governs only the platform's own components: {sorted(roles)}")
    if "roleFloors" in trusted:
        fail(f"the default declares no role floors:\n{trusted}")
    # An installation's own server and role.
    own_role = governance(["--set", FLOOR_RANGE, "--set-json", 'governance.roleFloors={"exampleManager":["approve_change","merge_change"]}',
                           "--set", "governedServers.example-manager.role=exampleManager", "--set", "governedServers.example-manager.safe[0]=get_info"])
    if "roleFloors:\n          exampleManager:\n          - approve_change\n          - merge_change" not in own_role:
        fail(f"governance.roleFloors must render as muster.governance.roleFloors:\n{own_role}")
    if governed_entries(own_role).get("example-manager", {}).get("role") != "exampleManager":
        fail(f"an installation's own server key governs with its declared role:\n{own_role}")
    for args, want in ((["--set-json", 'governance.roleFloors={"Bad Role":["x"]}', "--skip-schema-validation"], 'role "Bad Role": want a camelCase name'),
                       (["--set-json", 'governance.roleFloors={"exampleManager":"x"}', "--skip-schema-validation"], "governance.roleFloors.exampleManager: want a list"),
                       (["--set-json", 'governance.roleFloors={"exampleManager":["a b"]}', "--skip-schema-validation"], "is not a tool name"),
                       (["--set-json", 'governance.roleFloors={"exampleManager":["x"]}', "--set-json", 'muster.muster.governance.roleFloors={"exampleManager":["y"]}'], "are both set")):
        err = render(META_DIR, META + ["--set", FLOOR_RANGE, *args], expect_fail=True)
        if want not in err:
            fail(f"{args}: want {want!r}, got:\n{err}")
    if "agent-manager" in entries or "vm-manager" in entries:
        fail(f"components that are off ship no server to govern:\n{trusted}")
    governed = governed_entries(governance(["--set", FLOOR_RANGE, "--set", "governance.mode=governed", "--set", "components.agent-manager.enabled=true", "--set", "agent-manager.muster.mcpServer.name=agents"]))
    am = governed.get("agents")
    if not am or am["role"] != "agentManager" or "list_agents" not in am["safe"] or "update_agent" in am["safe"]:
        fail(f"governed with a renamed agent-manager server: want agents as agentManager with its reads safe, got {governed}")
    if "mode: governed" not in governance(["--set", FLOOR_RANGE, "--set", "governance.mode=governed"]):
        fail("governance.mode=governed must render mode governed")
    apart = governed_entries(governance(["--set", FLOOR_RANGE, "--set", "components.muster.targetNamespace=muster", "--set", "components.vm-manager.enabled=true"]))
    if not any(k.endswith("/vm-manager") for k in apart):
        fail(f"a server outside muster's namespace is keyed namespace/name: {sorted(apart)}")
    own = governance(["--set", FLOOR_RANGE,
                      "--set", "muster.muster.governance.governedServers[0].server=model-manager",
                      "--set", "muster.muster.governance.governedServers[0].role=modelManager",
                      "--set", "muster.muster.governance.governedServers[0].safe[0]=list_models"])
    mm = governed_entries(own)["model-manager"]
    if mm["safe"] != ["list_models"] or own.count("server: model-manager") != 1:
        fail(f"an own governedServers entry must stand for its server, once:\n{own}")
    dropped = governed_entries(governance(["--set", FLOOR_RANGE, "--set-json", "governedServers.model-manager=null"]))
    if "model-manager" in dropped:
        fail("a source set to null governs nothing")
    if governance([]):
        fail("below the muster floor the muster release must carry no governance block")
    err = render(META_DIR, META + ["--set", "governance.mode=governed"], expect_fail=True)
    if "governance.mode governed needs muster" not in err or "components.muster.versionRange" not in err:
        fail(f"governed below the floor must fail naming governance and components.muster.versionRange:\n{err}")
    err = render(META_DIR, META + ["--set", "governance.mode=open"], expect_fail=True)
    if "governance" not in err:
        fail(f"an unknown mode must fail in the schema naming governance:\n{err}")
    err = render(META_DIR, META + ["--set", "governance.mode=open", "--skip-schema-validation"], expect_fail=True)
    if 'governance.mode "open": want trusted or governed' not in err:
        fail(f"an unknown mode must fail in the template naming governance:\n{err}")
    for args, want in ((["--set", "governance=governed"], "governance"),
                       (["--set", "governance=governed", "--skip-schema-validation"], "governance is a map mirroring muster's governance configuration"),
                       (["--set", "governance.policy=x", "--skip-schema-validation"], "governance.policy: unknown key")):
        err = render(META_DIR, META + args, expect_fail=True)
        if want not in err:
            fail(f"{args}: want {want!r}, got:\n{err}")
    err = render(META_DIR, META + ["--set", FLOOR_RANGE, "--set", "muster.muster.governance.mode=governed"], expect_fail=True)
    if "muster.muster.governance.mode (governed) differs from governance.mode (trusted)" not in err:
        fail(f"an own mode that differs must fail naming both keys:\n{err}")
    err = render(META_DIR, META + ["--set", FLOOR_RANGE, "--set", "muster.muster.governance.servers.agentManager[0]=am"], expect_fail=True)
    if "muster.muster.governance.servers is the earlier governance shape" not in err:
        fail(f"the earlier servers shape must fail:\n{err}")
    err = render(META_DIR, META + ["--set", FLOOR_RANGE, "--set", "governedServers.model-manager.safe_list[0]=x"], expect_fail=True)
    if "governedServers" not in err or "safe_list" not in err:
        fail(f"an unknown key of a source must fail in the schema naming it:\n{err}")
    err = render(META_DIR, META + ["--set", FLOOR_RANGE, "--set", "governedServers.model-manager.safe_list[0]=x", "--skip-schema-validation"], expect_fail=True)
    if "governedServers.model-manager.safe_list: unknown key" not in err:
        fail(f"an unknown key of a source must fail naming it:\n{err}")
    connectivity = doc(render_meta([]), "HelmRelease", "agent-platform-connectivity")
    if connectivity and ("\n      governance:" in connectivity or "governedServers" in connectivity or "governanceExempt" in connectivity):
        fail("governance, governedServers and governanceExempt must not reach the connectivity release (omitKeys)")
    print("ok: governance renders muster's governance configuration (mode, governedServers per shipped server, keys, own entries, null sources), none below the floor, governed below the floor, the earlier shape, unknown keys and conflicting modes fail")


# muster's floors (muster internal/api/governance.go roleFloors): never safe.
FLOORS = {
    "agentManager": {"set_agent_approval", "set_organization_connector", "add_organization_member", "set_organization_model", "set_organization_egress_policy", "replace_organization_credential"},
    "connectorManager": {"mcpserver_create", "mcpserver_connect", "mcpserver_set_approval"},
    "toolchainManager": {"approve_source"},
    "modelManager": {"add_backend"},
}
MANAGERS = ["agent-manager", "model-manager", "vm-manager", "cluster-manager", "workspace-manager", "toolchain-manager", "skill-manager"]


def values_block(text: str, key: str) -> list[str]:
    """The lines of a top-level values.yaml block."""
    lines = text.split("\n")
    start = next(i for i, l in enumerate(lines) if l.startswith(f"{key}:"))
    out = []
    for l in lines[start + 1:]:
        if l and not l.startswith(" ") and not l.startswith("#"):
            break
        out.append(l)
    return out


def sources(meta_dir: str) -> tuple[dict[str, str], dict[str, str]]:
    """governedServers source -> role ('' for null) and governanceExempt source -> reason, from values.yaml."""
    text = open(os.path.join(meta_dir, "values.yaml")).read()
    governed: dict[str, str] = {}
    cur = None
    for l in values_block(text, "governedServers"):
        m = re.match(r"^  ([a-z0-9-]+):\s*(null)?\s*$", l)
        if m:
            cur = m.group(1)
            governed[cur] = ""
            continue
        m = re.match(r"^    role: (\S+)", l)
        if m and cur:
            governed[cur] = m.group(1)
    exempt = {}
    for l in values_block(text, "governanceExempt"):
        m = re.match(r'^  ([a-z0-9-]+): "(.+)"$', l)
        if m:
            exempt[m.group(1)] = m.group(2)
    return governed, exempt


def tools_map(text: str, header: re.Pattern[str]) -> dict[str, str]:
    """tool -> setting of the approval block whose `approval:` line follows the first match of header."""
    lines = text.split("\n")
    i = next(n for n, l in enumerate(lines) if header.search(l))
    i = next(n for n in range(i, len(lines)) if lines[n].strip().startswith("approval:"))
    t = next(n for n in range(i, len(lines)) if lines[n].strip().startswith("tools:"))
    indent = len(lines[t]) - len(lines[t].lstrip())
    out = {}
    for l in lines[t + 1:]:
        if l.strip().startswith("#"):
            continue
        if not l.strip() or len(l) - len(l.lstrip()) <= indent:
            break
        m = re.match(r"^\s+([A-Za-z0-9_.-]+): (Allow|Ask|Block)\b", l)
        if m:
            out[m.group(1)] = m.group(2)
    return out


# A safe entry whose name starts with one of these verbs looks like a write:
# refused unless the source's block says why it is a read with a comment
# `# safe-write: <tool>: <reason>`.
WRITE_VERBS = {
    "create", "update", "delete", "patch", "apply", "scale", "exec", "set", "add", "remove", "approve",
    "grant", "revoke", "write", "start", "stop", "kill", "invoke", "fork", "merge", "deprecate",
    "authorize", "sync", "wire", "unwire", "load", "unload", "pull", "push", "cancel", "replace",
    "suspend", "resume", "reboot", "run", "send", "mint", "init", "enable", "disable", "port",
    "forward", "rename", "move", "restart", "upgrade", "install", "uninstall", "edit", "import",
}


def write_like_safe(vtext: str) -> dict[str, str]:
    """source -> why, for safe entries that look like writes and carry no safe-write annotation."""
    out = {}
    for m in re.finditer(r"\n  ([a-z0-9-]+):\n    role: \S+\n((?:    #.*\n)*)    safe: \[(.*)\]\n", "\n" + "\n".join(values_block(vtext, "governedServers")) + "\n"):
        src, comments, listed = m.group(1), m.group(2), m.group(3)
        annotated = set(re.findall(r"# safe-write: ([A-Za-z0-9_]+): \S", comments))
        for tool in (t.strip() for t in listed.split(",") if t.strip()):
            tokens = tool.lower().split("_")
            if (tokens[0] in WRITE_VERBS or tool.lower() in WRITE_VERBS) and tool not in annotated:
                out[src] = f"{tool} looks like a write ({tokens[0]}): take it off the safe list, or say why it is a read in a `# safe-write: {tool}: <reason>` comment above `safe:`"
    return out


def check_write_like() -> None:
    base = "governedServers:\n  mcp-kubernetes:\n    role: infrastructure\n{c}    safe: [get, {t}, list]\nnext: x\n"
    for tool in ("delete", "authorize_workspace", "create_vm", "exec"):
        if not write_like_safe(base.format(c="", t=tool)):
            fail(f"{tool} on a safe list must be refused as write-like")
    if write_like_safe(base.format(c=f"    # safe-write: delete: dry run only here\n", t="delete")):
        fail("an annotated write-like entry must pass")
    for tool in ("list_port_forward_sessions", "get_vm_console", "read_sandbox_file", "toolchain_users", "can_i"):
        if write_like_safe(base.format(c="", t=tool)):
            fail(f"{tool} is a read and must pass")
    print("ok: safe entries that look like writes (delete, authorize_workspace, create_vm, exec) are refused unless annotated with safe-write; reads pass")


def check_coverage() -> None:
    governed, exempt = sources(META_DIR)
    covered = {k for k, role in governed.items() if role} | set(exempt)
    for k in set(governed) & set(exempt):
        fail(f"{k} is both governed and exempt")
    # 1. The meta chart's own picture, everything on.
    meta = render_meta(FULL + ["--set", FLOOR_RANGE, "--set", "components.mcp-kubernetes.enabled=true",
                               *[a for m in MANAGERS if m != "workspace-manager" for a in ("--set", f"components.{m}.enabled=true")],
                               "--set", "workspaces.enabled=true",
                               "--set", "muster.muster.credentialProvider.releasable.github=https://github.com/apps/ci-agents",
                               *[a for i, g in enumerate(("kubernetes", "prometheus", "capi")) for a in (
                                   "--set", f"agent-platform-mcps.mcpServers[{i}].cluster=mc{i}",
                                   "--set", f"agent-platform-mcps.mcpServers[{i}].group={g}",
                                   "--set", f"agent-platform-mcps.mcpServers[{i}].url=https://mc{i}.example.com/mcp")]])
    shipped = {}
    for d in docs(meta):
        m = re.search(r"\nkind: HelmRelease\nmetadata:\n  name: (\S+)\n", "\n" + d)
        if m and re.search(r"\n    muster:\n(      .*\n)*?      mcpServer:\n", d + "\n"):
            shipped[m.group(1)] = "meta release values carry muster.mcpServer"
    conn = render(CONN_DIR, CONN + AGENT_MANAGER + KAGENT_MCP + MCPK)
    for d in docs(conn):
        m = re.search(r"\nkind: MCPServer\nmetadata:\n  name: (\S+)\n", "\n" + d)
        if m:
            shipped[{"kagent": "kagent", "mcp-kubernetes": "mcp-kubernetes"}.get(m.group(1), m.group(1))] = "connectivity chart MCPServer"
    for g in ("kubernetes", "prometheus", "capi"):
        shipped[{"kubernetes": "mcp-kubernetes"}.get(g, g)] = "agent-platform-mcps family"
    # 2. The component charts beside this repository.
    siblings = os.environ.get("COMPONENT_CHECKOUTS") or os.path.dirname(os.path.abspath(os.path.join(META_DIR, "..", "..")))
    seen_siblings = []
    for repo in sorted(os.listdir(siblings)) if os.path.isdir(siblings) else []:
        tdir = os.path.join(siblings, repo, "helm", repo, "templates")
        if repo == "agent-platform" or not os.path.isdir(tdir):
            continue
        if any("kind: MCPServer" in open(os.path.join(tdir, f)).read() for f in os.listdir(tdir) if f.endswith((".yaml", ".tpl"))):
            seen_siblings.append(repo)
            if repo != "agent-platform-mcps":
                shipped[repo] = "component chart renders an MCPServer"
    for src, why in sorted(shipped.items()):
        if src not in covered:
            fail(f"{src} ({why}) ships an MCPServer that is neither in governedServers nor in governanceExempt: govern it with its reads safe, or exempt it with the reason")
    # 3. The rendered entries: every governed shipped source rendered, no floor is safe.
    rendered = governed_entries(block(doc(meta, "HelmRelease", "muster") or "", "      governance:"))
    for key, e in rendered.items():
        bad = set(e["safe"]) & FLOORS.get(e["role"], set())
        if bad:
            fail(f"{key}: floors {sorted(bad)} of role {e['role']} on its safe list (muster refuses to start)")
    for src in [s for s in shipped if s in governed and governed[s]]:
        if not any(k == src or k.endswith("/" + src) or k.endswith("-" + src) for k in rendered):
            fail(f"{src} is governed but no entry rendered for it with every component on: {sorted(rendered)}")
    # 4. Safe entries name tools of their server.
    conn_values = open(os.path.join(CONN_DIR, "values.yaml")).read()
    maps = {
        "kagent": tools_map(conn_values, re.compile(r"^      description: \"Your agent sessions")),
        "mcp-kubernetes": tools_map(conn_values, re.compile(r"^    description: \"Resources, events and pod logs of this installation")),
    }
    for repo in seen_siblings:
        text = open(os.path.join(siblings, repo, "helm", repo, "values.yaml")).read()
        if repo == "agent-platform-mcps":
            fam = tools_map(text, re.compile(r"^    kubernetes:\s*$"))
            if fam != maps["mcp-kubernetes"]:
                fail(f"agent-platform-mcps' kubernetes family map differs from the connectivity chart's mcp-kubernetes map: {sorted(set(fam.items()) ^ set(maps['mcp-kubernetes'].items()))}")
        else:
            maps[repo] = tools_map(text, re.compile(r"^  mcpServer:\s*$"))
    vtext = open(os.path.join(META_DIR, "values.yaml")).read()
    for src, why in write_like_safe(vtext).items():
        fail(f"governedServers.{src}.safe: {why}")
    for src, role in governed.items():
        if not role or src not in maps:
            continue
        m = re.search(rf"\n  {re.escape(src)}:\n    role: \S+\n(?:    #.*\n)*    safe: \[(.*)\]\n", vtext)
        if not m:
            fail(f"governedServers.{src}: want role then safe as a one-line list")
        safe = [t.strip() for t in m.group(1).split(",") if t.strip()]
        unknown = [t for t in safe if t not in maps[src]]
        if unknown:
            fail(f"governedServers.{src}.safe names no tool of its server: {unknown} (muster counts them in muster_governance_safe_list_unmatched)")
    missing = sorted(set(MANAGERS) - set(seen_siblings))
    note = f"; component checkouts not found for {missing}, their safe lists not checked against their tools" if missing else ""
    print(f"ok: every shipped MCPServer source ({len(shipped)}) is governed or exempt, no floor is safe, safe entries name tools of their server ({len(maps)} maps), the two kubernetes maps agree" + note)


def check_approval_config() -> None:
    def release(args: list[str]) -> str:
        return muster_release(["--set", FLOOR_RANGE, "--set", "global.domain=ci.example.com", *args])

    default = release([])
    approval = block(default, "      approval:")
    if approval.rstrip() != "      approval:\n        installation: agent-platform":
        fail(f"default approval, want installation agent-platform:\n{approval}")
    named = block(release(["--set", "backstage.installationName=lab-1"]), "      approval:")
    if "installation: lab-1" not in named:
        fail(f"approval.installation must follow backstage.installationName:\n{named}")
    own = block(release(["--set", "muster.muster.approval.installation=mine", "--set", "muster.muster.approval.ttl=10m"]), "      approval:")
    if "installation: mine" not in own or "ttl: 10m" not in own or "agent-platform" in own:
        fail(f"an own installation and ttl must stand:\n{own}")
    # the allowlist renders only for muster's OAuth client role, as muster's chart does
    mc = ["--set", "muster.muster.oauth.mcpClient.enabled=true"]
    portal = ["--set", "components.backstage.enabled=true"]
    kg = ["--set", "components.klaus-gateway.enabled=true", "--set", "klausGateway.obo.callbackBaseUrl=https://gw.ci.example.com/"]
    list_header = "          postLoginRedirectAllowlist:"
    if block(release(mc), list_header):
        fail("with the portal and klaus-gateway off there is nothing to allow: no allowlist expected")
    both = block(release(mc + portal + kg), list_header)
    want = list_header + "\n          - https://backstage.ci.example.com/sessions\n          - https://gw.ci.example.com/auth/slack/return"
    if both.rstrip() != want:
        fail(f"allowlist with the portal and klaus-gateway, want:\n{want}\ngot:\n{both}")
    only_portal = block(release(mc + portal + ["--set", "klausGateway.obo.callbackBaseUrl=https://gw.ci.example.com"]), list_header)
    if "auth/slack/return" in only_portal:
        fail(f"no klaus-gateway entry while klaus-gateway is off:\n{only_portal}")
    off = block(release(mc + kg), list_header)
    if "sessions" in off or "https://gw.ci.example.com/auth/slack/return" not in off:
        fail(f"klaus-gateway alone must give its return and no portal entry:\n{off}")
    no_url = block(release(mc + ["--set", "components.klaus-gateway.enabled=true"]), list_header)
    if no_url:
        fail(f"klaus-gateway without a callbackBaseUrl names nothing to allow:\n{no_url}")
    host = block(release(mc + portal + ["--set", "backstage.hostname=portal.example.org"]), list_header)
    if "https://portal.example.org/sessions" not in host or "backstage.ci.example.com" in host:
        fail(f"backstage.hostname must win over the global.domain derivation:\n{host}")
    merged = block(release(mc + portal + kg + [
        "--set", "muster.muster.oauth.mcpClient.postLoginRedirectAllowlist[0]=https://own.example.com/x",
        "--set", "muster.muster.oauth.mcpClient.postLoginRedirectAllowlist[1]=https://backstage.ci.example.com/sessions",
    ]), list_header)
    lines = [l.strip() for l in merged.split("\n")[1:]]
    if lines != ["- https://own.example.com/x", "- https://backstage.ci.example.com/sessions", "- https://gw.ci.example.com/auth/slack/return"]:
        fail(f"own entries stay first, each entry once:\n{merged}")
    ported = block(release(mc + portal + ["--set", "backstage.publicPort=8443", "--set", "components.klaus-gateway.enabled=true", "--set", "klausGateway.obo.callbackBaseUrl=https://gw.ci.example.com:8443/"]), list_header)
    if [l.strip() for l in ported.split("\n")[1:]] != ["- https://backstage.ci.example.com:8443/sessions", "- https://gw.ci.example.com:8443/auth/slack/return"]:
        fail(f"a non-default port must stay in both entries (M1-1):\n{ported}")
    default_port = block(release(mc + portal + ["--set", "backstage.publicPort=443"]), list_header)
    top = block(release(mc + portal + ["--set", "backstage.publicPort=65535"]), list_header)
    if "- https://backstage.ci.example.com:65535/sessions" not in top:
        fail(f"port 65535 is a port:\n{top}")
    if "- https://backstage.ci.example.com/sessions" not in default_port:
        fail(f"port 443 writes no port:\n{default_port}")
    for args, want in (
        (["--set", "backstage.publicPort=84a3"], 'backstage.publicPort "84a3"'),
        (["--set", "backstage.publicPort=65536"], 'backstage.publicPort "65536"'),
        (["--set", "backstage.publicPort=99999"], 'backstage.publicPort "99999"'),
        (["--set", "components.klaus-gateway.enabled=true", "--set", "klausGateway.obo.callbackBaseUrl=gw.ci.example.com"], 'klausGateway.obo.callbackBaseUrl "gw.ci.example.com": want an http(s) URL'),
    ):
        err = render(META_DIR, META + ["--set", FLOOR_RANGE, "--set", "global.domain=ci.example.com", *mc, *portal, *args], expect_fail=True)
        if want not in err:
            fail(f"{args}: want {want!r}, got:\n{err}")
    below = muster_release(["--set", "global.domain=ci.example.com", *mc, *portal, *kg])
    if block(below, "      approval:") or "auth/slack/return" in below or "/sessions" in below:
        fail("below the muster floor the muster release must carry neither approval nor derived allowlist entries")
    connectivity = doc(render_meta(["--set", FLOOR_RANGE, *portal]), "HelmRelease", "agent-platform-connectivity")
    if connectivity and "\n      approval:\n        installation:" in connectivity:
        fail("the approval block must not reach the connectivity release")
    print("ok: muster.approval.installation follows backstage.installationName, the redirect allowlist names the portal's /sessions and klaus-gateway's /auth/slack/return where they are on, with a non-default port, invalid ports and URLs fail, own values stand, none below the floor")


def check_connector_admin() -> None:
    editor = block(muster_release([]), "      mcpServerEditor:")
    if "kind: ServiceAccount" not in editor or "name: agent-platform-unset-connector-admins" not in editor or "namespace: kube-system" not in editor or editor.count("name: ") != 1 or "kind: Group" in editor:
        fail(f"connector admins by default are the unissuable ServiceAccount subject agent-platform-unset-connector-admins alone (no group an IdP could emit):\n{editor}")
    if "system:authenticated" in editor:
        fail(f"connector admins must not default to everyone:\n{editor}")
    team = block(muster_release(["--set", "tenants.platformTeam[0].kind=Group", "--set", "tenants.platformTeam[0].name=customer:platform"]), "      mcpServerEditor:")
    if "name: customer:platform" not in team or "agent-platform-unset-connector-admins" not in team:
        fail(f"tenants.platformTeam must be appended to the connector admins:\n{team}")
    own = block(muster_release(["--set", "muster.rbac.mcpServerEditor.subjects[0].kind=Group", "--set", "muster.rbac.mcpServerEditor.subjects[0].name=ops"]), "      mcpServerEditor:")
    if "name: ops" not in own or "agent-platform-unset-connector-admins" in own:
        fail(f"an installation's own connector admins must stand:\n{own}")
    print("ok: the connector-admin role (muster's mcpserver-editor) binds a ServiceAccount nobody holds by default plus tenants.platformTeam, an own list standing")


def check_binder() -> None:
    def binder(args: list[str]) -> str:
        return block(muster_release(["--set", FLOOR_RANGE, *args]), "      approvalBinder:")

    on = ["--set", "components.agent-manager.enabled=true"]
    got = binder(on)
    want = "\n".join([
        "      approvalBinder:",
        "        subjects:",
        "        - kind: ServiceAccount",
        "          name: agent-manager",
        "          namespace: default",
    ])
    if got.rstrip() != want:
        fail(f"muster-approval-binder must bind agent-manager's ServiceAccount only, want:\n{want}\ngot:\n{got}")
    moved = binder(on + ["--set", "components.agent-manager.targetNamespace=am", "--set", "agent-manager.serviceAccount.name=am-sa", "--set", "components.muster.targetNamespace=muster"])
    if "name: am-sa" not in moved or "namespace: am" not in moved:
        fail(f"the subject must follow agent-manager's ServiceAccount name and namespace:\n{moved}")
    own = binder(on + ["--set", "muster.rbac.approvalBinder.subjects[0].kind=ServiceAccount", "--set", "muster.rbac.approvalBinder.subjects[0].name=other", "--set", "muster.rbac.approvalBinder.subjects[0].namespace=x"])
    if "name: other" not in own or "name: agent-manager" in own:
        fail(f"an own approvalBinder list must stand:\n{own}")
    err = render(META_DIR, META + ["--set", FLOOR_RANGE, *on, "--set", "agent-manager.serviceAccount.create=false"], expect_fail=True)
    if "agent-manager.serviceAccount.create is false without agent-manager.serviceAccount.name" not in err:
        fail(f"a ServiceAccount the chart does not create must be named:\n{err}")
    if "name: am-sa" not in binder(on + ["--set", "agent-manager.serviceAccount.create=false", "--set", "agent-manager.serviceAccount.name=am-sa"]):
        fail("an own ServiceAccount with a name must be bound")
    if binder([]):
        fail("no approvalBinder subjects with agent-manager off")
    if block(muster_release(on), "      approvalBinder:"):
        fail("no approvalBinder subjects below the muster floor")
    conn = render(CONN_DIR, CONN + AGENT_MANAGER)
    if "muster-approval-binder" in conn:
        fail("the connectivity chart must ship no muster-approval-binder binding (the muster chart binds it in muster's namespace)")
    print("ok: muster-approval-binder is bound by the muster chart to agent-manager's ServiceAccount only (name and namespace following agent-manager), an own list stands, an unnamed external ServiceAccount fails, none with agent-manager off or below the floor, none in the connectivity chart")


def check_agent_manager_tool_approval() -> None:
    def release(args: list[str]) -> str:
        hr = doc(render_meta(["--set", "components.agent-manager.enabled=true", *args]), "HelmRelease", "agent-manager")
        if hr is None:
            fail(f"no agent-manager HelmRelease with {args}")
        return block(hr, "      toolApproval:")

    am = "components.agent-manager.versionRange=>=3.6.0 <4.0.0-0"
    if release(["--set", am]).split() != ["toolApproval:", "enabled:", "false"]:
        fail(f"below muster's agent approval floor agent-manager must bind no settings (toolApproval.enabled false):\n{release(['--set', am])}")
    on = release(["--set", am, "--set", "components.muster.versionRange=>=5.66.0 <6.0.0-0"])
    if on.split() != ["toolApproval:", "enabled:", "true"]:
        fail(f"from both floors agent-manager must bind approval settings:\n{on}")
    if release([]):
        fail("below agent-manager's tool approval floor its chart takes no muster.toolApproval")
    own = release(["--set", am, "--set", "components.muster.versionRange=>=5.66.0 <6.0.0-0", "--set", "agent-manager.muster.toolApproval.enabled=false"])
    if "true" in own:
        fail(f"an own agent-manager.muster.toolApproval.enabled must stand:\n{own}")
    print("ok: agent-manager's muster.toolApproval.enabled is on from its floor and muster's, off below muster's, absent below its own, an own value stands")


def check_session_bindings() -> None:
    base = [
        "--set", "global.identity.issuerUrl=https://dex.ci.example.com",
        "--set", "components.kagent.enabled=true",
        "--set", "components.agent-manager.enabled=true",
        "--set", "kagent.harness.snapshotLocation=s3://ci-agent-snapshots/agents",
        "--set", "kagent.database.postgres.urlFile=/etc/kagent-db/uri",
        "--set", "muster.muster.oauth.server.baseUrl=https://muster.ci.example.com",
    ]
    floor = ["--set", "components.muster.versionRange=>=5.66.0 <6.0.0-0"]

    def releases(args: list[str]) -> tuple[str, str]:
        out = render_meta([*base, *args])
        muster, am = doc(out, "HelmRelease", "muster"), doc(out, "HelmRelease", "agent-manager")
        if muster is None or am is None:
            fail(f"no muster or agent-manager HelmRelease with {args}")
        return block(muster, "        sessionBindings:"), am

    conn = doc(render_meta([*base, *floor]), "HelmRelease", "agent-platform-connectivity") or ""
    if "musterSessionBindingsPort: 8091" not in conn:
        fail("from the floor the connectivity release must open agent-manager's egress to the session bindings port (networkPolicy.musterSessionBindingsPort)")
    sb, am = releases(floor)
    if "onMainPort: false" not in sb or "namespace:" not in sb or "app.kubernetes.io/name: agent-manager" not in sb:
        fail(f"from muster's session bindings floor muster must serve the API on its own port, off the aggregator port, admitting agent-manager's pods alone:\n{sb}")
    if not re.search(r"bindingsUrl: http://muster\.[^.]+\.svc\.cluster\.local:8091/session-bindings", am):
        fail(f"from the floor agent-manager must call the session bindings port:\n{am}")
    sb, am = releases([])
    if sb or "bindingsUrl:" in am:
        fail(f"below the floor neither the port nor the URL are derived:\n{sb}")
    sb, _ = releases([*floor, "--set", "muster.muster.credentialProvider.sessionBindings.onMainPort=true"])
    if "onMainPort: true" not in sb:
        fail(f"an own sessionBindings.onMainPort must stand:\n{sb}")
    _, am = releases([*floor, "--set", "agent-manager.muster.bindingsUrl=https://bindings.example.com/session-bindings"])
    if "bindingsUrl: https://bindings.example.com/session-bindings" not in am or ":8091/session-bindings" in am:
        fail(f"an own agent-manager.muster.bindingsUrl must stand:\n{am}")
    # muster in a namespace of its own: the egress rule selects that namespace,
    # in both policy flavours, not the connectivity release's.
    ns_args = [*floor, "--set", "components.muster.targetNamespace=muster-only"]
    out = render_meta([*base, *ns_args])
    conn = doc(out, "HelmRelease", "agent-platform-connectivity") or ""
    if "musterSessionBindingsNamespace: muster-only" not in conn:
        fail(f"the connectivity release must carry muster's namespace for the session bindings egress rule:\n{conn}")
    _, am = releases(ns_args)
    if "muster.muster-only.svc.cluster.local:8091/session-bindings" not in am:
        fail(f"agent-manager's bindingsUrl must name muster's namespace:\n{am}")
    for flavor, want in (("cilium", "io.kubernetes.pod.namespace: muster-only"), ("kubernetes", "kubernetes.io/metadata.name: muster-only")):
        for ns_value, expect in (("muster-only", want), ("", "")):
            rendered = render(CONN_DIR, CONN + AGENT_MANAGER + ["--set", f"networkPolicy.flavor={flavor}", "--set", "networkPolicy.musterSessionBindingsPort=8091", "--set", f"networkPolicy.musterSessionBindingsNamespace={ns_value}"])
            rule = rendered.split("session bindings API on its own port", 1)
            if len(rule) != 2:
                fail(f"{flavor}: no session bindings egress rule rendered")
            snippet = rule[1][:400]
            if expect and expect not in snippet:
                fail(f"{flavor}: the session bindings egress rule must select muster's namespace ({expect}):\n{snippet}")
            if not expect and ("muster-only" in snippet or ("metadata.name" not in snippet and flavor == "kubernetes") or ("io.kubernetes.pod.namespace: agent-platform" not in snippet and flavor == "cilium")):
                fail(f"{flavor}: without a namespace the rule must select the release's own (agent-platform):\n{snippet}")
    print("ok: from muster's session bindings floor muster serves the session bindings API on its own port, off the aggregator port, its network policy admitting agent-manager's pods alone, and agent-manager's bindingsUrl names that port; none below the floor; own values stand")


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
    blocked = approval_of(render(CONN_DIR, CONN + MCPK + ["--set", "mcp-kubernetes.mcpServer.approval.rule=Block"]), "mcp-kubernetes")
    if "    rule: Block" not in blocked:
        fail(f"a rule Block must render:\n{blocked}")
    forwarded = doc(render_meta(["--set", "mcp-kubernetes.mcpServer.approval.tools.exec=Block", "--set", "kagent.controllerRoute.mcp.approval.tools.invoke_session=Ask"]), "HelmRelease", "agent-platform-connectivity")
    if forwarded is None or "exec: Block" not in forwarded or "invoke_session: Ask" not in forwarded:
        fail("per-connector overrides in the meta chart's values must reach the connectivity release")
    cleared = render(CONN_DIR, CONN + MCPK + ["--set-json", "mcp-kubernetes.mcpServer.approval=null"])
    if approval_of(cleared, "mcp-kubernetes"):
        fail("approval null must render no spec.approval")
    for args, want in (
        (["--set", "mcp-kubernetes.mcpServer.approval.rule=Deny"], 'mcp-kubernetes.mcpServer.approval.rule "Deny": want Allow, Ask or Block'),
        (["--set", "mcp-kubernetes.mcpServer.approval.tools.get=Deny"], 'mcp-kubernetes.mcpServer.approval.tools.get "Deny": want Allow, Ask or Block'),
        (["--set", "mcp-kubernetes.mcpServer.approval.preset=recommended"], "mcp-kubernetes.mcpServer.approval.preset: unknown key"),
    ):
        err = render(CONN_DIR, CONN + MCPK + args, expect_fail=True)
        if want not in err:
            fail(f"{args}: want {want!r}, got:\n{err}")
    err = render(CONN_DIR, CONN + KAGENT_MCP + ["--set", "kagent.controllerRoute.mcp.approval.tools.invoke_session=never"], expect_fail=True)
    if "kagent.controllerRoute.mcp.approval.tools.invoke_session" not in err:
        fail(f"kagent: an invalid value must fail naming the key:\n{err}")
    print("ok: mcp-kubernetes carries the Recommended settings, kagent the Allow all settings, a Block rule renders, overrides reach spec.approval (also from the meta chart), null renders none, invalid values fail naming the key")


# Test values for the floors this file's checks need, set in a copy of the
# chart while the chart keeps them unset until their releases exist
#.
TEST_FLOORS = {"muster.governanceFloor": "5.65.0", "muster.agentApprovalFloor": "5.66.0",
               "agentManager.toolApprovalFloor": "3.6.0", "muster.sessionBindingsPortFloor": "5.66.0"}


def with_test_floors(meta_dir: str, dst: str) -> str:
    """A copy of the meta chart with every unset floor of TEST_FLOORS set."""
    chart = os.path.join(dst, "agent-platform")
    shutil.copytree(meta_dir, chart)
    path = os.path.join(chart, "templates", "_helpers.tpl")
    text = open(path).read()
    for name, v in TEST_FLOORS.items():
        text, n = re.subn(r'(\{\{- define "agent-platform\.' + re.escape(name) + r'" -\}\})(\{\{- end -\}\})', r"\g<1>" + v + r"\g<2>", text)
        if n == 0 and f'define "agent-platform.{name}" -}}}}{{{{- end' not in text and not re.search(r'define "agent-platform\.' + re.escape(name) + r'" -\}\}[0-9]', text):
            fail(f"agent-platform.{name}: no one-line define")
    open(path, "w").write(text)
    return chart


def chart_with_floors(meta_dir: str, dst: str, floors: dict[str, str]) -> str:
    """A copy of the meta chart with exactly these floors set; every other
    floor of TEST_FLOORS unset."""
    chart = os.path.join(dst, "agent-platform")
    shutil.copytree(meta_dir, chart)
    path = os.path.join(chart, "templates", "_helpers.tpl")
    text = open(path).read()
    for name in list(TEST_FLOORS) + ["agentManager.agentChartV3Floor"]:
        text, n = re.subn(r'(\{\{- define "agent-platform\.' + re.escape(name) + r'" -\}\})[^{]*(\{\{- end -\}\})', r"\g<1>" + floors.get(name, "") + r"\g<2>", text)
        if n != 1:
            fail(f"agent-platform.{name}: no one-line define")
    open(path, "w").write(text)
    return chart


def check_floor_dependencies() -> None:
    """The floors that need the governance wiring fail the render while the
    governance floor is unset, whichever of them is set; governed mode and role
    floors fail with muster off."""
    dependent = ["agentManager.toolApprovalFloor", "muster.agentApprovalFloor", "muster.sessionBindingsPortFloor", "agentManager.agentChartV3Floor"]
    values = {**TEST_FLOORS, "agentManager.agentChartV3Floor": "3.6.0"}
    with tempfile.TemporaryDirectory(prefix="verify-floor-deps-") as tmp:
        for i, name in enumerate(dependent):
            os.makedirs(os.path.join(tmp, str(i)))
            chart = chart_with_floors(META_DIR, os.path.join(tmp, str(i)), {name: values[name]})
            err = render(chart, META, expect_fail=True)
            want = f"agent-platform.{name} is set while agent-platform.muster.governanceFloor is unset"
            if want not in err:
                fail(f"floor {name} set with the governance floor unset must fail the render naming both:\n{err}")
        os.makedirs(os.path.join(tmp, "all"))
        chart = chart_with_floors(META_DIR, os.path.join(tmp, "all"), {n: v for n, v in values.items() if n != "muster.governanceFloor"})
        err = render(chart, META, expect_fail=True)
        if "agent-platform.muster.governanceFloor is unset" not in err:
            fail(f"every floor but the governance floor set must fail naming it:\n{err}")
        os.makedirs(os.path.join(tmp, "ok"))
        chart = chart_with_floors(META_DIR, os.path.join(tmp, "ok"), values)
        render(chart, META)
        for args, want in (
            (["--set", "components.muster.enabled=false", "--set", "governance.mode=governed"], "governance.mode governed needs muster as a component of this release"),
            (["--set", "components.muster.enabled=false", "--set-json", 'governance.roleFloors={"exampleManager":["approve_change"]}'], "governance.roleFloors is set while muster is off"),
        ):
            err = render(chart, META + args, expect_fail=True)
            if want not in err:
                fail(f"{args}: want {want!r}, got:\n{err}")
        render(chart, META + ["--set", "components.muster.enabled=false"])
    print("ok: a floor that needs the governance wiring fails the render while the governance floor is unset, naming both; governed mode and role floors fail with muster off, trusted renders")


def check_unset_floors_keep_everything_off() -> None:
    """On the chart as it is: a floor whose release does not exist gates its
    wiring off whatever the component ranges say."""
    high = ["--set", "components.muster.versionRange=>=5.99.0 <6.0.0-0", "--set", "components.agent-manager.versionRange=>=3.99.0 <4.0.0-0",
            "--set", "components.agent-manager.enabled=true"]
    text = open(os.path.join(META_DIR, "templates", "_helpers.tpl")).read()
    unset = [n for n in TEST_FLOORS if re.search(r'define "agent-platform\.' + re.escape(n) + r'" -\}\}\{\{- end', text)]
    if not unset:
        return
    out = render_meta(high)
    muster, am = doc(out, "HelmRelease", "muster"), doc(out, "HelmRelease", "agent-manager")
    if "muster.governanceFloor" in unset:
        if block(muster, "      governance:"):
            fail("with the governance floor unset the muster release must carry no governance configuration")
        err = render_meta_fail(high + ["--set", "governance.mode=governed"])
        if "no muster release this chart knows reads the governance configuration yet" not in err or ">= " in err or "muster  or" in err:
            fail(f"with the governance floor unset `governed` must fail naming why, with no empty floor:\n{err}")
        err = render_meta_fail(high + ["--set-json", 'governance.roleFloors={"exampleManager":["approve_change"]}'])
        if "governance.roleFloors is set, but this chart renders muster's governance configuration only from" not in err:
            fail(f"with the governance floor unset governance.roleFloors must fail, not be dropped:\n{err}")
        for own in (["--set", "muster.muster.governance.mode=governed"], ["--set", "muster.muster.governance.servers.agentManager[0]=am"]):
            err = render_meta_fail(high + own)
            if "muster.muster.governance is set" not in err:
                fail(f"with the governance floor unset an own {own[1]} must fail (governed, governing nothing):\n{err}")
    if "muster.agentApprovalFloor" in unset and "enabled: true" in block(am, "      toolApproval:"):
        fail("with muster's agent approval floor unset agent-manager must bind no settings")
    if "muster.sessionBindingsPortFloor" in unset and block(muster, "        sessionBindings:"):
        fail("with the session bindings floor unset muster must keep its earlier bindings wiring")
    print(f"ok: unset floors ({', '.join(unset)}) keep what they gate off for any muster and agent-manager range; `governed` fails")


# The default agent: kagent, muster and agent-manager on (componentEnabled).
DEFAULT_AGENT_ON = [
    "--namespace", "agent-platform",
    "--set", "global.domain=example.com",
    "--set", "global.identity.issuerUrl=https://dex.example.com",
    "--set", "global.identity.clientId=agent-platform",
    "--set", "global.identity.existingSecret=agent-platform-idp",
    "--set", "global.gatewayApi.parentRefs[0].name=example-gateway",
    "--set", "model-manager.ollama.endpoint=http://ollama.example.com:11434",
    "--set", "kagent.harness.snapshotLocation=s3://ci-agent-snapshots/agents",
    "--set", "kagent.database.postgres.urlFile=/etc/kagent-db/uri",
    "--set", "postgres.enabled=true",
    "--set", "components.kagent.enabled=true",
    "--set", "components.agent-manager.enabled=true",
]


def check_default_agent_release_readable() -> None:
    """The default agent's release lives in the chart's Flux namespace, not in
    the kagent namespace agent-manager manages: agent-manager gets get on it
    by name (muster.toolApproval.platformReleases), so a session of the
    default agent can read its tool approval settings for every person."""
    am = "components.agent-manager.versionRange=>=3.6.0 <4.0.0-0"
    musters = "components.muster.versionRange=>=5.66.0 <6.0.0-0"

    def releases(args: list[str]) -> str:
        out = render_meta([*DEFAULT_AGENT_ON, *args])
        hr = doc(out, "HelmRelease", "agent-manager")
        if hr is None:
            fail(f"no agent-manager HelmRelease with {args}")
        return block(hr, "        platformReleases:")

    def items(text: str) -> list[str]:
        return [line.strip().lstrip("- ").strip() for line in text.split("\n")[1:] if line.strip()]

    got = releases(["--set", am, "--set", musters])
    if sorted(items(got)) != ["name: default-agent", "namespace: agent-platform"]:
        fail(f"agent-manager must be granted the default agent's release agent-platform/default-agent:\n{got}")
    got = releases(["--set", am, "--set", musters, "--set", "gitops.namespace=flux-giantswarm"])
    if "namespace: flux-giantswarm" not in got:
        fail(f"the default agent's release lives in gitops.namespace:\n{got}")
    if releases(["--set", am, "--set", musters, "--set", "components.default-agent.enabled=false"]):
        fail("no default agent, no platform release to read")
    if releases([]):
        fail("below agent-manager's tool approval floor its chart takes no muster.toolApproval")
    print("ok: agent-manager may get the default agent's release (muster.toolApproval.platformReleases: <Flux namespace>/default-agent) from its tool approval floor, none without the default agent or below the floor")


def check_default_agent_on_remote_target() -> None:
    """With a remote target (gitops.target.kubeConfig) agent-manager runs on
    the target and cannot read the default agent's release on this cluster:
    it gets the release's approval values instead
    (muster.toolApproval.platformAgents), the Agent, the release its Flux
    labels name (<Flux namespace>/<target.name>-default-agent) and the
    release's own tools/toolset/default, and no platformReleases."""
    am = "components.agent-manager.versionRange=>=3.6.0 <4.0.0-0"
    musters = "components.muster.versionRange=>=5.66.0 <6.0.0-0"
    target = ["--set", "gitops.target.kubeConfig.secretRef.name=wc1-kubeconfig", "--set", "gitops.target.name=wc1",
              "--set", "gitops.namespace=flux-giantswarm"]

    def releases(args: list[str]) -> tuple[str, str, str]:
        out = render_meta([*DEFAULT_AGENT_ON, "--set", am, "--set", musters, *target, *args])
        hr = doc(out, "HelmRelease", "wc1-agent-manager")
        if hr is None:
            fail(f"no wc1-agent-manager HelmRelease with {args}")
        da = doc(out, "HelmRelease", "wc1-default-agent") or ""
        return block(hr, "        platformAgents:"), block(hr, "        platformReleases:"), da

    agents, plain, da = releases([])
    if plain:
        fail(f"with a remote target agent-manager cannot read the release: no platformReleases, got\n{plain}")
    lines = [line.strip().lstrip("- ").strip() for line in agents.split("\n")[1:] if line.strip()]
    for want in ["name: default-agent", "namespace: kagent", "release:", "name: wc1-default-agent", "namespace: flux-giantswarm",
                 "values:", "toolset:", "preset:full"]:
        if want not in lines:
            fail(f"platformAgents must carry the default agent kagent/default-agent, its release flux-giantswarm/wc1-default-agent and its toolset; missing {want!r}:\n{agents}")
    for key in ["tools:", "default:", "requireApproval:"]:
        if (key in lines) != (("\n    " + key) in da or ("\n      " + key) in da):
            fail(f"platformAgents' {key} must match the default agent's release values:\n{agents}\n{da}")
    if "toolset:\n    - preset:full" not in da:
        fail(f"the default agent's release carries toolset [preset:full]:\n{da}")
    agents, plain, _ = releases(["--set", "components.default-agent.enabled=false"])
    if agents or plain:
        fail("no default agent, no platform agent")
    print("ok: with a remote target agent-manager gets the default agent's approval values (muster.toolApproval.platformAgents: kagent/default-agent, release <Flux namespace>/<target>-default-agent, the release's toolset) instead of platformReleases; none without the default agent")


def render_meta_fail(args: list[str]) -> str:
    return render(META_DIR, META + args, expect_fail=True)


if __name__ == "__main__":
    META_DIR, CONN_DIR = sys.argv[1], sys.argv[2]
    governance_floor(META_DIR)
    check_unset_floors_keep_everything_off()
    check_floor_dependencies()
    _tmp = tempfile.mkdtemp(prefix="verify-tool-approval-")
    atexit.register(shutil.rmtree, _tmp, True)
    META_DIR = with_test_floors(META_DIR, _tmp)
    FLOOR_RANGE = f"components.muster.versionRange=>={governance_floor(META_DIR)} <6.0.0-0"
    check_governance()
    check_write_like()
    check_coverage()
    check_approval_config()
    check_connector_admin()
    check_binder()
    check_agent_manager_tool_approval()
    check_default_agent_release_readable()
    check_default_agent_on_remote_target()
    check_session_bindings()
    check_approval()
