#!/usr/bin/env python3
"""Assert the RBAC of agent tenants (giantswarm/agent-platform#706).

Each case pins one property of the render:

- with kagent on, the connectivity chart renders the three ClusterRoles
  agent-platform:user, agent-platform:editor and agent-platform:admin:
  each role holds every rule of the one before it; the user reads agents,
  templates, RemoteMCPServers, ModelConfigs, Harnesses, EgressPolicies and muster
  MCPServers, gets and lists the namespace's RoleBindings (its members,
  giantswarm/agent-platform#1368) and creates Sessions, and writes nothing;
  the editor writes
  HelmReleases, Agents, AgentTemplates and RemoteMCPServers and reads the
  agent chart's OCIRepository without writing it (giantswarm/agent-platform#1267);
  the admin also writes ModelConfigs, EgressPolicies and RoleBindings, places
  Secrets (create, update, delete) and reads none (a patch answers with the
  stored Secret, so it reads too), may `bind` the
  three roles only, and no role carries `escalate` or reads a Secret;
  agent-platform:platform-user holds the user's rules but the EgressPolicies
  and the RoleBindings;
- the ValidatingAdmissionPolicy agent-platform-tenant-egress-credentials
  (Fail, bound with Deny) admits egresspolicies.api.kagent.dev of every
  version in every namespace labelled agent-platform.giantswarm.io/organization but kagent,
  and refuses a secretKeyRef without the tenant-credential- prefix; with
  kagent off it does not render; agent-platform-tenant-credentials (Fail,
  Deny) in the same namespaces refuses a Secret named tenant-credential-*
  of any type but Opaque (what the CEL decides is proven in the agentlab);
  agent-platform-tenant-model-credentials (Fail, Deny) in the same namespaces
  refuses a ModelConfig whose apiKeySecret lacks the prefix, for every
  requester; agent-platform-tenant-credential-refs
  (Fail, Deny) there refuses, for every requester, an Agent's or
  AgentTemplate's git credentialRef or a RemoteMCPServer's headersFrom Secret
  without the prefix, but a write that leaves the spec of an object being
  deleted as it is;
- agent-platform-platform-tenant-credential-refs (Fail, Deny) selects the
  kagent namespace only, matches the six kinds of every version on create and
  update, but an update that leaves the spec as it is, also on an object being
  deleted, and asks the authorizer for `get` on each referenced Secret for
  every requester; with kagent off it does not render; kagent.namespaceOverride
  moves it;
- the platform tenant: in the kagent namespace, agent-platform:platform-user
  is bound to tenants.platform.users (no agent-platform:user binding there,
  so no signed-in person reads its EgressPolicies) and agent-platform:editor to
  tenants.platform.editors plus tenants.platformTeam; an empty list renders no
  binding; with kagent off, none of the roles and bindings render;
- muster's MCPServers: `list` (get, watch) in the release namespace for
  musterMcpServerViewers, or tenants.platform.users while it is unset, whether
  kagent is on or not; an own list binds the viewers and leaves
  agent-platform:platform-user alone, an empty one binds nobody, and with
  tenants.platform's lists empty in the meta chart (or tenants.platform unset
  in the connectivity chart) agent-platform:platform-user is not bound in
  kagent and agent-platform:editor is bound to tenants.platformTeam only, or
  not at all while it is empty; rendered from the values the meta chart hands
  its connectivity release (giantswarm/agent-platform#1331);
- the meta chart appends tenants.platformTeam to muster's mcpserver-editor and
  workflow-editor subjects (each subject once) and leaves them alone while the
  list is empty.

Needs PyYAML. HELM selects the binary.
"""

import os
import subprocess
import sys
import tempfile

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
SECRET_READS = {"get", "list", "watch", "patch"}


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
        # A patch answers with the stored object, data included: it reads.
        # So does a delete with Orphan or Foreground propagation or of a
        # Secret a finalizer holds; admission refuses that one
        # (agent-platform-organization-secret-deletes), not RBAC.
        reads = {t for t in sets[name] if t[1] == "secrets" and t[2] in SECRET_READS}
        if reads:
            fail(f"ClusterRole {name} reads Secrets: {sorted(reads, key=str)}")
    user, editor, admin = (sets[n] for n in ROLES)
    if not user <= editor <= admin:
        fail("the roles do not nest: user within editor within admin")
    for res in ("agents", "agenttemplates", "remotemcpservers", "modelconfigs", "harnesses", "egresspolicies"):
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
    # Every member reads the members (giantswarm/agent-platform#1368):
    # agent-manager's list_organization_members lists the namespace's
    # RoleBindings as the caller. get and list only: nothing watches them as
    # a member.
    user_rb = {t[2] for t in user if t[0] == "rbac.authorization.k8s.io" and t[1] == "rolebindings"}
    if user_rb != {"get", "list"}:
        fail(f"agent-platform:user holds {sorted(user_rb)} on rolebindings, want get and list: every member reads the members")
    ok("agent-platform:user reads the tenant's agents, servers, Harnesses and members, starts a Session, writes nothing")
    for g, res in (("helm.toolkit.fluxcd.io", "helmreleases"),
                   ("api.kagent.dev", "agents"), ("api.kagent.dev", "agenttemplates"), ("api.kagent.dev", "remotemcpservers")):
        for v in WRITES:
            if (g, res, v, None) not in editor:
                fail(f"agent-platform:editor cannot {v} {res}.{g}")
    for t in editor:
        if t[1] in ("modelconfigs", "egresspolicies", "secrets", "rolebindings", "clusterroles", "ocirepositories") and t[2] in WRITES | {"bind"}:
            fail(f"agent-platform:editor holds {t}")
    editor_rb = {t[2] for t in editor if t[0] == "rbac.authorization.k8s.io" and t[1] == "rolebindings"}
    if editor_rb != {"get", "list"}:
        fail(f"agent-platform:editor holds {sorted(editor_rb)} on rolebindings, want get and list: it reads the members, as a user does")
    for v in ("get", "list", "watch"):
        if ("source.toolkit.fluxcd.io", "ocirepositories", v, None) not in editor:
            fail(f"agent-platform:editor cannot {v} ocirepositories.source.toolkit.fluxcd.io")
    ok("agent-platform:editor writes what agent-manager writes as the caller, reads the agent chart's OCIRepository, and writes no OCIRepository, ModelConfig, EgressPolicy, Secret or RoleBinding")
    for g, res in (("api.kagent.dev", "modelconfigs"), ("api.kagent.dev", "egresspolicies"), ("", "secrets"),
                   ("rbac.authorization.k8s.io", "rolebindings")):
        for v in WRITES - (SECRET_READS if res == "secrets" else set()):
            if (g, res, v, None) not in admin:
                fail(f"agent-platform:admin cannot {v} {res}.{g}")
    binds = {t[3] for t in admin if t[2] == "bind"}
    if binds != set(ROLES):
        fail(f"agent-platform:admin binds {sorted(binds, key=str)}, want the three roles only")
    ok("agent-platform:admin writes ModelConfigs, EgressPolicies and RoleBindings, places Secrets it cannot read, "
       "and binds the three roles only")
    role = find(docs, "ClusterRole", "agent-platform:platform-user")
    if role is None:
        fail("ClusterRole agent-platform:platform-user missing with kagent on")
    platform = grants(role["rules"])
    want = {t for t in user if t[1] not in ("egresspolicies", "rolebindings")}
    if platform != want:
        fail(f"agent-platform:platform-user grants {sorted(platform ^ want, key=str)} unlike agent-platform:user without EgressPolicies and RoleBindings")
    ok("agent-platform:platform-user is agent-platform:user without the EgressPolicies and the RoleBindings")


def check_egress_admission(docs: list, label: str) -> None:
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
    want_ns = [{"key": label, "operator": "Exists"},
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
    name = "agent-platform-tenant-credentials"
    vap = find(docs, "ValidatingAdmissionPolicy", name)
    vapb = find(docs, "ValidatingAdmissionPolicyBinding", name)
    if vap is None or vapb is None:
        fail(f"ValidatingAdmissionPolicy {name} or its binding missing with kagent on")
    spec = vap["spec"]
    if spec.get("failurePolicy") != "Fail" or vapb["spec"].get("validationActions") != ["Deny"]:
        fail(f"{name} does not fail closed and deny")
    if spec["matchConstraints"].get("namespaceSelector", {}).get("matchExpressions") != want_ns:
        fail(f"{name} selects {spec['matchConstraints'].get('namespaceSelector')}, want the Organizations' namespaces but kagent")
    if spec["matchConstraints"].get("resourceRules") != [{"apiGroups": [""], "apiVersions": ["v1"], "operations": ["CREATE", "UPDATE"], "resources": ["secrets"]}]:
        fail(f"{name} matches {spec['matchConstraints'].get('resourceRules')}, want Secret creates and updates")
    if [c["expression"] for c in spec.get("matchConditions", [])] != ["object.metadata.name.startsWith('tenant-credential-')"]:
        fail(f"{name} does not select the tenant-credential- Secrets only: {spec.get('matchConditions')}")
    if "object.type == 'Opaque'" not in " ".join(v["expression"] for v in spec["validations"]):
        fail(f"{name} does not hold the tenant credentials to type Opaque")
    ok(f"{name} keeps a tenant-credential- Secret of type Opaque in the Organizations' namespaces but kagent")
    name = "agent-platform-tenant-model-credentials"
    vap = find(docs, "ValidatingAdmissionPolicy", name)
    vapb = find(docs, "ValidatingAdmissionPolicyBinding", name)
    if vap is None or vapb is None:
        fail(f"ValidatingAdmissionPolicy {name} or its binding missing with kagent on")
    spec = vap["spec"]
    if spec.get("failurePolicy") != "Fail" or vapb["spec"].get("validationActions") != ["Deny"]:
        fail(f"{name} does not fail closed and deny")
    if spec["matchConstraints"].get("namespaceSelector", {}).get("matchExpressions") != want_ns:
        fail(f"{name} selects {spec['matchConstraints'].get('namespaceSelector')}, want the Organizations' namespaces but kagent")
    if spec["matchConstraints"].get("resourceRules") != [{"apiGroups": ["api.kagent.dev"], "apiVersions": ["*"], "operations": ["CREATE", "UPDATE"], "resources": ["modelconfigs"]}]:
        fail(f"{name} matches {spec['matchConstraints'].get('resourceRules')}, want ModelConfig creates and updates")
    cel = " ".join(v["expression"] for v in spec.get("variables", []) + spec["validations"])
    for want in ("apiKeySecret", "startsWith('tenant-credential-')"):
        if want not in cel:
            fail(f"{name} does not hold apiKeySecret by {want}: {cel}")
    ok(f"{name} refuses, in the Organizations' namespaces but kagent, a ModelConfig on a non-tenant-credential- Secret but agent-manager's")
    name = "agent-platform-tenant-credential-refs"
    vap = find(docs, "ValidatingAdmissionPolicy", name)
    vapb = find(docs, "ValidatingAdmissionPolicyBinding", name)
    if vap is None or vapb is None:
        fail(f"ValidatingAdmissionPolicy {name} or its binding missing with kagent on")
    spec = vap["spec"]
    if spec.get("failurePolicy") != "Fail" or vapb["spec"].get("validationActions") != ["Deny"]:
        fail(f"{name} does not fail closed and deny")
    if spec["matchConstraints"].get("namespaceSelector", {}).get("matchExpressions") != want_ns:
        fail(f"{name} selects {spec['matchConstraints'].get('namespaceSelector')}, want the Organizations' namespaces but kagent")
    if spec["matchConstraints"].get("resourceRules") != [{"apiGroups": ["api.kagent.dev"], "apiVersions": ["*"], "operations": ["CREATE", "UPDATE"], "resources": ["agents", "agenttemplates", "remotemcpservers"]}]:
        fail(f"{name} matches {spec['matchConstraints'].get('resourceRules')}, want Agent, AgentTemplate and RemoteMCPServer creates and updates")
    cel = " ".join(v["expression"] for v in spec.get("variables", []) + spec["validations"])
    for want in ("credentialRef", "headersFrom", "h.valueFrom.type == 'Secret'", "!n.startsWith('tenant-credential-')",
                 "variables.releasing ||", "variables.spec == (has(oldObject.spec)"):
        if want not in cel:
            fail(f"{name} does not hold {want}: {cel}")
    if "userInfo.username ==" in cel:
        fail(f"{name} exempts a requester: {cel}")
    ok(f"{name} refuses, in the Organizations' namespaces but kagent and for every requester, a git credentialRef or a headersFrom Secret not named tenant-credential-*")
    if " in [" in cel:
        fail(f"{name} names an installation skills credential without agent-manager: {cel}")


def check_platform_tenant_refs(docs: list, namespace: str) -> None:
    name = "agent-platform-platform-tenant-credential-refs"
    vap = find(docs, "ValidatingAdmissionPolicy", name)
    vapb = find(docs, "ValidatingAdmissionPolicyBinding", name)
    if vap is None or vapb is None:
        fail(f"ValidatingAdmissionPolicy {name} or its binding missing with kagent on")
    spec = vap["spec"]
    if spec.get("failurePolicy") != "Fail" or vapb["spec"].get("policyName") != name or vapb["spec"].get("validationActions") != ["Deny"]:
        fail(f"{name} does not fail closed and deny")
    if spec["matchConstraints"].get("namespaceSelector") != {"matchLabels": {"kubernetes.io/metadata.name": namespace}}:
        fail(f"{name} selects {spec['matchConstraints'].get('namespaceSelector')}, want the {namespace} namespace only")
    want_rules = [{"apiGroups": ["api.kagent.dev"], "apiVersions": ["*"], "operations": ["CREATE", "UPDATE"],
                   "resources": ["agents", "agenttemplates", "remotemcpservers", "egresspolicies", "modelconfigs",
                                 "modelproviderconfigs"]}]
    if spec["matchConstraints"].get("resourceRules") != want_rules:
        fail(f"{name} matches {spec['matchConstraints'].get('resourceRules')}, want {want_rules}")
    cond = " ".join(c["expression"] for c in spec.get("matchConditions", []))
    for want in ("request.operation == 'CREATE'", "!= (has(oldObject.spec)"):
        if want not in cond:
            fail(f"{name} does not condition on {want}: {cond}")
    if "deletionTimestamp" in cond:
        fail(f"{name} admits a spec change to an object being deleted: {cond}")
    cel = " ".join(v["expression"] for v in spec.get("variables", []) + spec["validations"])
    for want in ("credentialRef", "h.valueFrom.type == 'Secret'", "d.authentication.secretKeyRef", "apiKeySecret", "variables.spec.secretRef.name",
                 "authorizer.group('').resource('secrets').namespace(request.namespace).name(n).check('get').allowed()"):
        if want not in cel:
            fail(f"{name} does not hold {want}: {cel}")
    if "userInfo.username ==" in cel or "startsWith(" in cel:
        fail(f"{name} exempts a requester or a name prefix: {cel}")
    ok(f"{name} refuses, in the {namespace} namespace only and for every requester, a Secret reference the requester may not get")


def check_installation_credential(docs: list) -> None:
    name = "agent-platform-tenant-credential-refs"
    vap = find(docs, "ValidatingAdmissionPolicy", name)
    cel = " ".join(v["expression"] for v in vap["spec"].get("variables", []))
    want = "n in ['agent-manager-skills-token', 'tenant-credential-agent-manager-skills-token']"
    if want not in cel:
        fail(f"{name} does not refuse the installation's skills credential under its names ({want}): {cel}")
    ok(f"{name} refuses the installation's skills credential, agent-manager-skills-token and its earlier tenant-credential- name, for every requester")


def check_platform_tenant(docs: list, want_editors: list) -> None:
    user = find(docs, "RoleBinding", "agent-platform:platform-user", "kagent")
    editor = find(docs, "RoleBinding", "agent-platform:editor", "kagent")
    if user is None or editor is None:
        fail("the platform tenant's RoleBindings are missing from the kagent namespace")
    if find(docs, "RoleBinding", "agent-platform:user", "kagent"):
        fail("RoleBinding agent-platform:user renders in the kagent namespace: every signed-in person reads its EgressPolicies and RoleBindings")
    if user["roleRef"] != {"apiGroup": "rbac.authorization.k8s.io", "kind": "ClusterRole", "name": "agent-platform:platform-user"}:
        fail(f"RoleBinding agent-platform:platform-user refers to {user['roleRef']}")
    if subject_names(user) != ["system:authenticated"]:
        fail(f"agent-platform:platform-user is bound to {subject_names(user)}, want every signed-in person")
    if editor["roleRef"]["name"] != "agent-platform:editor":
        fail(f"RoleBinding agent-platform:editor refers to {editor['roleRef']}")
    if subject_names(editor) != want_editors:
        fail(f"agent-platform:editor is bound to {subject_names(editor)}, want {want_editors}")


def check_muster_viewer(docs: list, want: list = None) -> None:
    want = want or ["system:authenticated"]
    role = find(docs, "Role", "agent-platform:mcpserver-viewer", "agent-platform")
    rb = find(docs, "RoleBinding", "agent-platform:mcpserver-viewer", "agent-platform")
    if role is None or rb is None:
        fail("muster's MCPServers are not listed to every signed-in person in the release namespace")
    if grants(role["rules"]) != {("muster.giantswarm.io", "mcpservers", v, None) for v in ("get", "list", "watch")}:
        fail(f"Role agent-platform:mcpserver-viewer grants {role['rules']}")
    if subject_names(rb) != want:
        fail(f"agent-platform:mcpserver-viewer is bound to {subject_names(rb)}, want {want}")


def render_from_meta(meta: str, conn: str, args: list) -> list:
    """The connectivity chart rendered from the values of the connectivity
    HelmRelease the meta chart renders with args."""
    for d in render(meta, ["-f", f"{meta}/ci/ci-values.yaml", *args]):
        if d.get("kind") == "HelmRelease" and d["metadata"]["name"] == "agent-platform-connectivity":
            values = d["spec"]["values"]
            break
    else:
        fail("no agent-platform-connectivity HelmRelease in the meta render")
    with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as f:
        yaml.safe_dump(values, f)
    try:
        return render(conn, ["--namespace", "agent-platform", "-f", f.name])
    finally:
        os.unlink(f.name)


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
    for name in ROLES + ["agent-platform:platform-user"]:
        if find(off, "ClusterRole", name):
            fail(f"ClusterRole {name} renders with kagent off")
    for name in ("agent-platform:platform-user", "agent-platform:editor"):
        if find(off, "RoleBinding", name):
            fail(f"RoleBinding {name} renders with kagent off")
    for name in ("agent-platform-tenant-egress-credentials", "agent-platform-tenant-credentials",
                 "agent-platform-tenant-model-credentials",
                 "agent-platform-tenant-credential-refs", "agent-platform-platform-tenant-credential-refs"):
        if find(off, "ValidatingAdmissionPolicy", name):
            fail(f"{name} renders with kagent off")
    check_muster_viewer(off)
    ok("kagent off: no tenant role or binding; muster's servers listed to every signed-in person")

    on = render(conn, [*CONN, "--set", "components.kagent.enabled=true"])
    check_roles(on)
    # The organizations' namespaces carry agent-platform.giantswarm.io/organization
    # (giantswarm/agent-platform#1268).
    check_egress_admission(on, "agent-platform.giantswarm.io/organization")
    check_platform_tenant_refs(on, "kagent")
    check_platform_tenant_refs(render(conn, [*CONN, "--set", "components.kagent.enabled=true",
                                             "--set", "kagent.namespaceOverride=agents",
                                             "--set", "model-manager.kagent.namespace=agents"]), "agents")
    check_platform_tenant(on, ADMINS)
    check_muster_viewer(on)
    ok("the platform tenant: every signed-in person is a platform user, the admin groups are editors")

    check_installation_credential(render(conn, [*CONN, "--set", "components.kagent.enabled=true", "--set", "components.agent-manager.enabled=true",
                                                "--set", "agent-manager.skills.mintGitAuthSecret=true"]))
    model = find(on, "ValidatingAdmissionPolicy", "agent-platform-tenant-model-credentials")
    if "agent-manager" in yaml.safe_dump(model):
        fail("agent-platform-tenant-model-credentials names agent-manager")
    ok("agent-platform-tenant-model-credentials excepts no writer")

    team = render(conn, [*CONN, "--set", "components.kagent.enabled=true", *TEAM])
    check_platform_tenant(team, ADMINS + ["acme:platform"])
    ok("tenants.platformTeam joins the platform tenant's editors, each subject once")

    empty = render(conn, [*CONN, "--set", "components.kagent.enabled=true", "--set", "tenants.platform.users=null",
                          "--set", "tenants.platform.editors=null"])
    for name in ("agent-platform:platform-user", "agent-platform:editor", "agent-platform:mcpserver-viewer"):
        if find(empty, "RoleBinding", name):
            fail(f"RoleBinding {name} renders with no subjects")
    ok("an empty subject list renders no binding")

    viewers = ["--set-json", 'musterMcpServerViewers=[{"apiGroup":"rbac.authorization.k8s.io","kind":"Group","name":"acme:everyone"}]']
    own = render(conn, [*CONN, "--set", "components.kagent.enabled=true", *viewers])
    check_muster_viewer(own, ["acme:everyone"])
    check_platform_tenant(own, ADMINS)
    ok("musterMcpServerViewers binds muster's viewers; agent-platform:platform-user stays with tenants.platform.users")

    none = render(conn, [*CONN, "--set", "components.kagent.enabled=true", "--set-json", "musterMcpServerViewers=[]"])
    if find(none, "RoleBinding", "agent-platform:mcpserver-viewer"):
        fail("RoleBinding agent-platform:mcpserver-viewer renders with musterMcpServerViewers empty")
    check_platform_tenant(none, ADMINS)
    ok("an empty musterMcpServerViewers binds no viewer and does not fall back to tenants.platform.users")

    # The platform tenant's retirement goes through the meta chart: Helm drops a
    # null override, and the connectivity chart's own defaults would come back
    # on its release, so the retirement empties the lists. Render the
    # connectivity chart from the values the meta chart hands its release.
    retired = render_from_meta(meta, conn, [*CONN[2:], "--set", "components.kagent.enabled=true",
                                            "--set-json", "tenants.platform.users=[]",
                                            "--set-json", "tenants.platform.editors=[]", *viewers])
    for name in ("agent-platform:platform-user", "agent-platform:editor"):
        if find(retired, "RoleBinding", name, "kagent"):
            fail(f"RoleBinding {name} renders in the kagent namespace with tenants.platform's lists empty in the meta chart")
    check_muster_viewer(retired, ["acme:everyone"])
    ok("tenants.platform's lists empty in the meta chart: its connectivity release binds no platform tenant in kagent, muster's viewers still bound")

    # With the platform team set, the retirement keeps its editor binding on
    # purpose: agent-platform:editor in kagent, bound to the platform team only.
    kept = render_from_meta(meta, conn, [*CONN[2:], "--set", "components.kagent.enabled=true",
                                         "--set-json", "tenants.platform.users=[]",
                                         "--set-json", "tenants.platform.editors=[]", *viewers, *TEAM])
    if find(kept, "RoleBinding", "agent-platform:platform-user", "kagent"):
        fail("RoleBinding agent-platform:platform-user renders in kagent with tenants.platform's lists empty and the platform team set")
    editor = find(kept, "RoleBinding", "agent-platform:editor", "kagent")
    if editor is None:
        fail("RoleBinding agent-platform:editor is missing in kagent with tenants.platform's lists empty and tenants.platformTeam set: the platform team loses its editor binding")
    if subject_names(editor) != ["acme:platform", "giantswarm-ad:giantswarm-admins"]:
        fail(f"agent-platform:editor in kagent is bound to {subject_names(editor)}, want tenants.platformTeam only")
    ok("tenants.platform's lists empty with tenants.platformTeam set: agent-platform:editor in kagent stays, bound to the platform team only")

    unset = render(conn, [*CONN, "--set", "components.kagent.enabled=true", "--set", "tenants.platform=null", *viewers])
    if find(unset, "RoleBinding", "agent-platform:platform-user", "kagent"):
        fail("RoleBinding agent-platform:platform-user renders with tenants.platform unset in the connectivity chart")
    check_muster_viewer(unset, ["acme:everyone"])
    ok("tenants.platform unset in the connectivity chart: it renders, without the platform tenant's bindings")

    check_meta(meta)


if __name__ == "__main__":
    main()
