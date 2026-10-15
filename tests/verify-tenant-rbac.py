#!/usr/bin/env python3
"""Assert the RBAC of agent tenants (giantswarm/agent-platform#706).

Each case pins one property of the render:

- with kagent on, the connectivity chart renders the three ClusterRoles
  agent-platform:user, agent-platform:editor and agent-platform:admin:
  each role holds every rule of the one before it; the user reads agents,
  templates, RemoteMCPServers, ModelConfigs, EgressPolicies and muster
  MCPServers and creates Sessions, and writes nothing; the editor writes
  HelmReleases, OCIRepositories, Agents, AgentTemplates and RemoteMCPServers;
  the admin also writes ModelConfigs, EgressPolicies and RoleBindings, places
  Secrets (create, update, patch, delete) and reads none, may `bind` the
  three roles only, and no role carries `escalate` or reads a Secret;
- the ValidatingAdmissionPolicy agent-platform-tenant-egress-credentials
  (Fail, bound with Deny) admits egresspolicies.api.kagent.dev of every
  version in every namespace labelled giantswarm.io/organization but kagent,
  and refuses a secretKeyRef without the tenant-credential- prefix; with
  kagent off it does not render (what its CEL decides is proven in the
  agentlab);
- the platform tenant: in the kagent namespace, agent-platform:user is bound
  to tenants.platform.users and agent-platform:editor to
  tenants.platform.editors plus tenants.platformTeam; an empty list renders no
  binding; with kagent off, none of the roles and bindings render;
- muster's MCPServers: `list` (get, watch) in the release namespace for
  tenants.platform.users, whether kagent is on or not;
- the meta chart appends tenants.platformTeam to muster's mcpserver-editor and
  workflow-editor subjects (each subject once) and leaves them alone while the
  list is empty.

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
ROLES = ["agent-platform:user", "agent-platform:editor", "agent-platform:admin"]
WRITES = {"create", "update", "patch", "delete"}


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
        reads = {t for t in sets[name] if t[1] == "secrets" and t[2] in ("get", "list", "watch")}
        if reads:
            fail(f"ClusterRole {name} reads Secrets: {sorted(reads, key=str)}")
    user, editor, admin = (sets[n] for n in ROLES)
    if not user <= editor <= admin:
        fail("the roles do not nest: user within editor within admin")
    for res in ("agents", "agenttemplates", "remotemcpservers", "modelconfigs", "egresspolicies"):
        for v in ("get", "list", "watch"):
            if ("api.kagent.dev", res, v, None) not in user:
                fail(f"agent-platform:user cannot {v} {res}.api.kagent.dev")
    for v in ("get", "list", "watch"):
        if ("muster.giantswarm.io", "mcpservers", v, None) not in user:
            fail(f"agent-platform:user cannot {v} mcpservers.muster.giantswarm.io")
    if ("api.kagent.dev", "sessions", "create", None) not in user:
        fail("agent-platform:user cannot create sessions.api.kagent.dev")
    user_writes = {t for t in user if t[2] in WRITES or t[2] == "bind"} - {("api.kagent.dev", "sessions", "create", None)}
    if user_writes:
        fail(f"agent-platform:user writes {sorted(user_writes)}")
    ok("agent-platform:user reads the tenant's agents and servers, starts a Session, writes nothing")
    for g, res in (("helm.toolkit.fluxcd.io", "helmreleases"), ("source.toolkit.fluxcd.io", "ocirepositories"),
                   ("api.kagent.dev", "agents"), ("api.kagent.dev", "agenttemplates"), ("api.kagent.dev", "remotemcpservers")):
        for v in WRITES:
            if (g, res, v, None) not in editor:
                fail(f"agent-platform:editor cannot {v} {res}.{g}")
    for t in editor:
        if t[1] in ("modelconfigs", "egresspolicies", "secrets", "rolebindings", "clusterroles") and t[2] in WRITES | {"bind"}:
            fail(f"agent-platform:editor holds {t}")
    ok("agent-platform:editor writes what agent-manager writes as the caller, and no ModelConfig, EgressPolicy, Secret or RoleBinding")
    for g, res in (("api.kagent.dev", "modelconfigs"), ("api.kagent.dev", "egresspolicies"), ("", "secrets"),
                   ("rbac.authorization.k8s.io", "rolebindings")):
        for v in WRITES:
            if (g, res, v, None) not in admin:
                fail(f"agent-platform:admin cannot {v} {res}.{g}")
    binds = {t[3] for t in admin if t[2] == "bind"}
    if binds != set(ROLES):
        fail(f"agent-platform:admin binds {sorted(binds, key=str)}, want the three roles only")
    ok("agent-platform:admin writes ModelConfigs, EgressPolicies and RoleBindings, places Secrets it cannot read, "
       "and binds the three roles only")


def check_egress_admission(docs: list) -> None:
    name = "agent-platform-tenant-egress-credentials"
    vap = find(docs, "ValidatingAdmissionPolicy", name)
    vapb = find(docs, "ValidatingAdmissionPolicyBinding", name)
    if vap is None or vapb is None:
        fail(f"ValidatingAdmissionPolicy {name} or its binding missing with kagent on")
    spec = vap["spec"]
    if spec.get("failurePolicy") != "Fail":
        fail(f"{name} failurePolicy is {spec.get('failurePolicy')}, want Fail")
    if vapb["spec"].get("policyName") != name or vapb["spec"].get("validationActions") != ["Deny"]:
        fail(f"{name}'s binding is {vapb['spec']}, want the policy with Deny")
    want_ns = [{"key": "giantswarm.io/organization", "operator": "Exists"},
               {"key": "kubernetes.io/metadata.name", "operator": "NotIn", "values": ["kagent"]}]
    if spec["matchConstraints"].get("namespaceSelector", {}).get("matchExpressions") != want_ns:
        fail(f"{name} selects {spec['matchConstraints'].get('namespaceSelector')}, want the Organizations' namespaces but kagent")
    want_rules = [{"apiGroups": ["api.kagent.dev"], "apiVersions": ["*"], "operations": ["CREATE", "UPDATE"],
                   "resources": ["egresspolicies"]}]
    if spec["matchConstraints"].get("resourceRules") != want_rules:
        fail(f"{name} matches {spec['matchConstraints'].get('resourceRules')}, want {want_rules}")
    cel = " ".join(v["expression"] for v in spec.get("variables", []))
    if "secretKeyRef" not in cel or "startsWith('tenant-credential-')" not in cel:
        fail(f"{name} does not hold secretKeyRef to the tenant-credential- prefix: {cel}")
    ok(f"{name} refuses, in the Organizations' namespaces but kagent, an EgressPolicy naming a non-tenant-credential- Secret")


def check_platform_tenant(docs: list, want_editors: list) -> None:
    user = find(docs, "RoleBinding", "agent-platform:user", "kagent")
    editor = find(docs, "RoleBinding", "agent-platform:editor", "kagent")
    if user is None or editor is None:
        fail("the platform tenant's RoleBindings are missing from the kagent namespace")
    if user["roleRef"] != {"apiGroup": "rbac.authorization.k8s.io", "kind": "ClusterRole", "name": "agent-platform:user"}:
        fail(f"RoleBinding agent-platform:user refers to {user['roleRef']}")
    if subject_names(user) != ["system:authenticated"]:
        fail(f"agent-platform:user is bound to {subject_names(user)}, want every signed-in person")
    if editor["roleRef"]["name"] != "agent-platform:editor":
        fail(f"RoleBinding agent-platform:editor refers to {editor['roleRef']}")
    if subject_names(editor) != want_editors:
        fail(f"agent-platform:editor is bound to {subject_names(editor)}, want {want_editors}")


def check_muster_viewer(docs: list) -> None:
    role = find(docs, "Role", "agent-platform:mcpserver-viewer", "agent-platform")
    rb = find(docs, "RoleBinding", "agent-platform:mcpserver-viewer", "agent-platform")
    if role is None or rb is None:
        fail("muster's MCPServers are not listed to every signed-in person in the release namespace")
    if grants(role["rules"]) != {("muster.giantswarm.io", "mcpservers", v, None) for v in ("get", "list", "watch")}:
        fail(f"Role agent-platform:mcpserver-viewer grants {role['rules']}")
    if subject_names(rb) != ["system:authenticated"]:
        fail(f"agent-platform:mcpserver-viewer is bound to {subject_names(rb)}")


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


def main() -> None:
    meta, conn = sys.argv[1], sys.argv[2]
    off = render(conn, CONN)
    for name in ROLES:
        if find(off, "ClusterRole", name):
            fail(f"ClusterRole {name} renders with kagent off")
    for name in ROLES[:2]:
        if find(off, "RoleBinding", name):
            fail(f"RoleBinding {name} renders with kagent off")
    if find(off, "ValidatingAdmissionPolicy", "agent-platform-tenant-egress-credentials"):
        fail("agent-platform-tenant-egress-credentials renders with kagent off")
    check_muster_viewer(off)
    ok("kagent off: no tenant role or binding; muster's servers listed to every signed-in person")

    on = render(conn, [*CONN, "--set", "components.kagent.enabled=true"])
    check_roles(on)
    check_egress_admission(on)
    check_platform_tenant(on, ADMINS)
    check_muster_viewer(on)
    ok("the platform tenant: every signed-in person is a user, the admin groups are editors")

    team = render(conn, [*CONN, "--set", "components.kagent.enabled=true", *TEAM])
    check_platform_tenant(team, ADMINS + ["acme:platform"])
    ok("tenants.platformTeam joins the platform tenant's editors, each subject once")

    empty = render(conn, [*CONN, "--set", "components.kagent.enabled=true", "--set", "tenants.platform.users=null",
                          "--set", "tenants.platform.editors=null"])
    for name in ("agent-platform:user", "agent-platform:editor", "agent-platform:mcpserver-viewer"):
        if find(empty, "RoleBinding", name):
            fail(f"RoleBinding {name} renders with no subjects")
    ok("an empty subject list renders no binding")

    check_meta(meta)


if __name__ == "__main__":
    main()
