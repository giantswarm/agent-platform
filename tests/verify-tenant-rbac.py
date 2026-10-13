#!/usr/bin/env python3
"""Assert the RBAC of agent tenants (giantswarm/agent-platform#706).

Each case pins one property of the render:

- with kagent on, the connectivity chart renders the three ClusterRoles
  agent-platform-user, agent-platform-author and agent-platform-tenant-admin:
  each role holds every rule of the one before it; the user reads agents,
  templates, RemoteMCPServers, ModelConfigs and muster MCPServers and creates
  Sessions, and writes nothing; the author writes HelmReleases,
  OCIRepositories, Agents, AgentTemplates and RemoteMCPServers; the tenant
  admin also writes ModelConfigs and RoleBindings, may `bind` the three roles
  only, and no role carries `escalate`;
- the platform tenant: in the kagent namespace, agent-platform-user is bound
  to tenants.platform.users and agent-platform-author to
  tenants.platform.authors plus tenants.platformTeam; an empty list renders no
  binding; with kagent off, none of the roles and bindings render;
- muster's MCPServers: `list` (get, watch) in the release namespace for
  tenants.platform.users, whether kagent is on or not;
- with agent-manager on, its ServiceAccount is bound cluster-wide to
  agent-manager-tenants (namespaces read; Harnesses, ModelConfigs,
  WorkerPools, OCIRepositories, the network policies of the resolved flavor,
  ServiceAccounts and RoleBindings written; `bind` on agent-platform-tenant-flux only); with
  agent-manager off, nothing of it renders;
- the meta chart appends tenants.platformTeam to muster's mcpserver-editor and
  workflow-editor subjects (each subject once) and leaves them alone while the
  list is empty;
- kagent's UI proxy asks for the Kubernetes API server's audience
  (audience:server:client_id:dex-k8s-authenticator) in both charts' values.

Needs PyYAML. HELM selects the binary.
"""

import os
import subprocess
import sys

import yaml

HELM = os.environ.get("HELM", "helm")
CONN = [
    "--namespace", "agent-platform",
    "--set", "ingress.parentRefs[0].name=x",
    "--set", "kagent.harness.snapshotLocation=s3://ci-agent-snapshots/agents",
    "--set", "global.domain=ci.example.com",
    "--set", "global.identity.issuerUrl=https://dex.ci.example.com",
    "--set", "global.identity.clientId=platform",
    "--set", "global.identity.existingSecret=platform-oauth",
]
TEAM = [
    "--set", "tenants.platformTeam[0].apiGroup=rbac.authorization.k8s.io",
    "--set", "tenants.platformTeam[0].kind=Group",
    "--set", "tenants.platformTeam[0].name=acme:platform",
    # An admin group already in the defaults: appended once only.
    "--set", "tenants.platformTeam[1].apiGroup=rbac.authorization.k8s.io",
    "--set", "tenants.platformTeam[1].kind=Group",
    "--set", "tenants.platformTeam[1].name=giantswarm-ad:giantswarm-admins",
]
ADMINS = ["giantswarm-ad:giantswarm-admins", "giantswarm-github:giantswarm:giantswarm-admins"]
ROLES = ["agent-platform-user", "agent-platform-author", "agent-platform-tenant-admin"]
WRITES = {"create", "update", "patch", "delete"}
AUDIENCE = "audience:server:client_id:dex-k8s-authenticator"


def fail(msg: str) -> None:
    sys.exit(f"FAIL: {msg}")


def ok(msg: str) -> None:
    print(f"ok: {msg}")


def render(chart: str, args: list) -> list:
    r = subprocess.run([HELM, "template", "t", chart, *args], capture_output=True, text=True)
    if r.returncode != 0:
        fail(f"helm template {chart} {' '.join(args)}:\n{r.stderr}")
    return [d for d in yaml.safe_load_all(r.stdout) if d]


def find(docs: list, kind: str, name: str, namespace: str = None):
    for d in docs:
        md = d.get("metadata", {})
        if d.get("kind") == kind and md.get("name") == name and (namespace is None or md.get("namespace") == namespace):
            return d
    return None


def grants(rules: list) -> set:
    """The (group, resource, verb, resourceName) tuples a rule list grants."""
    out = set()
    for r in rules:
        for g in r.get("apiGroups", []):
            for res in r.get("resources", []):
                for v in r.get("verbs", []):
                    for n in r.get("resourceNames", [None]):
                        out.add((g, res, v, n))
    return out


def subject_names(binding) -> list:
    return [s["name"] for s in binding.get("subjects", [])]


def check_roles(docs: list) -> None:
    sets = {}
    for name in ROLES:
        role = find(docs, "ClusterRole", name)
        if role is None:
            fail(f"ClusterRole {name} missing with kagent on")
        sets[name] = grants(role["rules"])
        if any(v == "escalate" for _, _, v, _ in sets[name]):
            fail(f"ClusterRole {name} carries escalate")
    user, author, admin = (sets[n] for n in ROLES)
    if not user <= author <= admin:
        fail("the roles do not nest: user within author within tenant-admin")
    for res in ("agents", "agenttemplates", "remotemcpservers", "modelconfigs"):
        for v in ("get", "list", "watch"):
            if ("api.kagent.dev", res, v, None) not in user:
                fail(f"agent-platform-user cannot {v} {res}.api.kagent.dev")
    for v in ("get", "list", "watch"):
        if ("muster.giantswarm.io", "mcpservers", v, None) not in user:
            fail(f"agent-platform-user cannot {v} mcpservers.muster.giantswarm.io")
    if ("api.kagent.dev", "sessions", "create", None) not in user:
        fail("agent-platform-user cannot create sessions.api.kagent.dev")
    user_writes = {t for t in user if t[2] in WRITES or t[2] == "bind"} - {("api.kagent.dev", "sessions", "create", None)}
    if user_writes:
        fail(f"agent-platform-user writes {sorted(user_writes)}")
    ok("agent-platform-user reads the tenant's agents and servers, starts a Session, writes nothing")
    for g, res in (("helm.toolkit.fluxcd.io", "helmreleases"), ("source.toolkit.fluxcd.io", "ocirepositories"),
                   ("api.kagent.dev", "agents"), ("api.kagent.dev", "agenttemplates"), ("api.kagent.dev", "remotemcpservers")):
        for v in WRITES:
            if (g, res, v, None) not in author:
                fail(f"agent-platform-author cannot {v} {res}.{g}")
    for t in author:
        if t[1] in ("modelconfigs", "rolebindings", "clusterroles") and t[2] in WRITES | {"bind"}:
            fail(f"agent-platform-author holds {t}")
    ok("agent-platform-author writes what agent-manager writes as the caller, and no ModelConfig or RoleBinding")
    for g, res in (("api.kagent.dev", "modelconfigs"), ("rbac.authorization.k8s.io", "rolebindings")):
        for v in WRITES:
            if (g, res, v, None) not in admin:
                fail(f"agent-platform-tenant-admin cannot {v} {res}.{g}")
    binds = {t[3] for t in admin if t[2] == "bind"}
    if binds != set(ROLES):
        fail(f"agent-platform-tenant-admin binds {sorted(binds, key=str)}, want the three roles only")
    ok("agent-platform-tenant-admin writes ModelConfigs and RoleBindings and binds the three roles only")


def check_platform_tenant(docs: list, want_authors: list) -> None:
    user = find(docs, "RoleBinding", "agent-platform-user", "kagent")
    author = find(docs, "RoleBinding", "agent-platform-author", "kagent")
    if user is None or author is None:
        fail("the platform tenant's RoleBindings are missing from the kagent namespace")
    if user["roleRef"] != {"apiGroup": "rbac.authorization.k8s.io", "kind": "ClusterRole", "name": "agent-platform-user"}:
        fail(f"RoleBinding agent-platform-user refers to {user['roleRef']}")
    if subject_names(user) != ["system:authenticated"]:
        fail(f"agent-platform-user is bound to {subject_names(user)}, want every signed-in person")
    if author["roleRef"]["name"] != "agent-platform-author":
        fail(f"RoleBinding agent-platform-author refers to {author['roleRef']}")
    if subject_names(author) != want_authors:
        fail(f"agent-platform-author is bound to {subject_names(author)}, want {want_authors}")


def check_muster_viewer(docs: list) -> None:
    role = find(docs, "Role", "agent-platform-mcpserver-viewer", "agent-platform")
    rb = find(docs, "RoleBinding", "agent-platform-mcpserver-viewer", "agent-platform")
    if role is None or rb is None:
        fail("muster's MCPServers are not listed to every signed-in person in the release namespace")
    if grants(role["rules"]) != {("muster.giantswarm.io", "mcpservers", v, None) for v in ("get", "list", "watch")}:
        fail(f"Role agent-platform-mcpserver-viewer grants {role['rules']}")
    if subject_names(rb) != ["system:authenticated"]:
        fail(f"agent-platform-mcpserver-viewer is bound to {subject_names(rb)}")


def check_reconciler(docs: list, netpol: tuple) -> None:
    role = find(docs, "ClusterRole", "agent-manager-tenants")
    crb = find(docs, "ClusterRoleBinding", "agent-manager-tenants")
    flux = find(docs, "ClusterRole", "agent-platform-tenant-flux")
    if role is None or crb is None or flux is None:
        fail("agent-manager's tenant reconciler RBAC is missing with agent-manager on")
    if crb["subjects"] != [{"kind": "ServiceAccount", "name": "agent-manager", "namespace": "agent-platform"}]:
        fail(f"ClusterRoleBinding agent-manager-tenants binds {crb['subjects']}")
    g = grants(role["rules"])
    for v in ("get", "list", "watch"):
        if ("", "namespaces", v, None) not in g:
            fail(f"agent-manager cannot {v} namespaces")
    for group, res in (("api.kagent.dev", "harnesses"), ("api.kagent.dev", "modelconfigs"), ("ate.dev", "workerpools"),
                       ("source.toolkit.fluxcd.io", "ocirepositories"), netpol, ("", "serviceaccounts"),
                       ("rbac.authorization.k8s.io", "rolebindings")):
        for v in WRITES:
            if (group, res, v, None) not in g:
                fail(f"agent-manager cannot {v} {res}.{group or 'core'}")
    if {t for t in g if t[2] in ("bind", "escalate")} != {("rbac.authorization.k8s.io", "clusterroles", "bind", "agent-platform-tenant-flux")}:
        fail("agent-manager may bind another role than agent-platform-tenant-flux, or escalate")
    if any(t[1] in ("namespaces",) and t[2] in WRITES for t in g):
        fail("agent-manager writes namespaces")
    other = {("networking.k8s.io", "networkpolicies"), ("cilium.io", "ciliumnetworkpolicies")} - {netpol}
    if any((t[0], t[1]) in other for t in g):
        fail(f"agent-manager writes the network policies of the other flavor than {netpol}")


def check_meta(chart: str) -> None:
    def muster_rbac(args):
        for d in render(chart, ["-f", f"{chart}/ci/ci-values.yaml", *args]):
            if d.get("kind") == "HelmRelease" and d["metadata"]["name"] == "muster":
                return d["spec"]["values"].get("rbac", {})
        fail("no muster HelmRelease in the meta render")
    for role in ("mcpServerEditor", "workflowEditor"):
        if subject_names(muster_rbac([])[role]) != ADMINS:
            fail(f"muster.rbac.{role} changed without tenants.platformTeam")
    rbac = muster_rbac(TEAM)
    for role in ("mcpServerEditor", "workflowEditor"):
        if subject_names(rbac[role]) != ADMINS + ["acme:platform"]:
            fail(f"muster.rbac.{role}.subjects are {subject_names(rbac[role])}, want the admin groups and acme:platform once")
    ok("the meta chart appends tenants.platformTeam to muster's two editor bindings, each subject once")


def check_scope(meta: str, conn: str) -> None:
    for chart in (meta, conn):
        with open(f"{chart}/values.yaml", encoding="utf-8") as f:
            v = yaml.safe_load(f)
        scope = v["kagent"]["oauth2-proxy"]["extraArgs"]["scope"].split()
        if AUDIENCE not in scope:
            fail(f"{chart}: kagent.oauth2-proxy.extraArgs.scope lacks {AUDIENCE}")
    ok("kagent's UI proxy asks for the API server's audience")


def main() -> None:
    meta, conn = sys.argv[1], sys.argv[2]
    off = render(conn, CONN)
    for kind, name in (("ClusterRole", r) for r in ROLES + ["agent-manager-tenants", "agent-platform-tenant-flux"]):
        if find(off, kind, name):
            fail(f"{kind} {name} renders with kagent and agent-manager off")
    for name in ROLES[:2]:
        if find(off, "RoleBinding", name):
            fail(f"RoleBinding {name} renders with kagent off")
    check_muster_viewer(off)
    ok("kagent and agent-manager off: no tenant role or binding; muster's servers listed to every signed-in person")

    on = render(conn, [*CONN, "--set", "components.kagent.enabled=true"])
    check_roles(on)
    check_platform_tenant(on, ADMINS)
    check_muster_viewer(on)
    if find(on, "ClusterRole", "agent-manager-tenants"):
        fail("agent-manager's tenant RBAC renders with agent-manager off")
    ok("the platform tenant: every signed-in person is a user, the admin groups are authors")

    team = render(conn, [*CONN, "--set", "components.kagent.enabled=true", *TEAM])
    check_platform_tenant(team, ADMINS + ["acme:platform"])
    ok("tenants.platformTeam joins the platform tenant's authors, each subject once")

    empty = render(conn, [*CONN, "--set", "components.kagent.enabled=true", "--set", "tenants.platform.users=null",
                          "--set", "tenants.platform.authors=null"])
    for name in ("agent-platform-user", "agent-platform-author", "agent-platform-mcpserver-viewer"):
        if find(empty, "RoleBinding", name):
            fail(f"RoleBinding {name} renders with no subjects")
    ok("an empty subject list renders no binding")

    am = render(conn, [*CONN, "--set", "components.kagent.enabled=true", "--set", "components.agent-manager.enabled=true"])
    check_reconciler(am, ("networking.k8s.io", "networkpolicies"))
    am = render(conn, [*CONN, "--set", "components.kagent.enabled=true", "--set", "components.agent-manager.enabled=true",
                       "--api-versions", "cilium.io/v2"])
    check_reconciler(am, ("cilium.io", "ciliumnetworkpolicies"))
    ok("agent-manager's tenant reconciler: namespaces read, a tenant's objects (network policies of the flavor) written, agent-platform-tenant-flux bound only")

    check_meta(meta)
    check_scope(meta, conn)


if __name__ == "__main__":
    main()
