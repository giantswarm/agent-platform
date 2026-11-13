#!/usr/bin/env python3
"""Assert agent-platform-platform-helmreleases (giantswarm/giantswarm#38840).

Each case pins one property:

- the render: with kagent on, the policy and its binding render (Fail, Deny),
  select the kagent namespace (kagent.namespaceOverride moves it) and name
  kagent.fluxServiceAccountName, the agent chart's OCIRepository and the
  installation's skills credential (current and earlier names); with kagent
  off or an empty kagent.fluxServiceAccountName nothing renders;
- on a local API server (tests/apiserver.py, RBAC on, Flux's CRDs), for an
  agent-platform:editor of the kagent namespace: agent-manager's shape is
  admitted; spec.chart, valuesFrom, postRenderers, kubeConfig,
  targetNamespace and storageNamespace, another OCIRepository, one of another
  namespace, another ServiceAccount or none are refused;
  values.skillsGitAuthSecretRef.name and a git credentialRef of an
  extraAgentSpec skill or plugin are admitted empty, as the installation's
  skills credential (either name) or as a Secret the editor may get, and
  refused for one the editor may not get; each name counts once, and more
  than two distinct Secrets besides the installation's skills credential are
  refused, for a cluster-admin too; a HelmRelease written before the
  policy takes an annotation but no spec change; a HelmRelease outside the
  kagent namespace is not held. Every refusal names the policy with its own
  message, never an evaluation error;
- kagent's own HelmRelease (tenants.platform.kagentRelease,
  giantswarm/agent-platform#1387): unset, the policy has no exemption; a name
  without writers, writers without a name, a writer of another kind, without a
  name or a ServiceAccount without its namespace fail the render; set, kagent's
  release as a deployer places it (its own OCIRepository, no ServiceAccount,
  valuesFrom) is created and upgraded by each writer (a user, a group, a
  ServiceAccount), and refused for an editor and for a cluster-admin who is no
  writer; any other HelmRelease from a writer is held as before.

Needs helm, openssl, PyYAML and github.com on the first run (the envtest
binaries). HELM selects the binary.

Usage: verify-platform-helmreleases.py META_CHART CONNECTIVITY_CHART
"""
import json
import os
import subprocess
import sys
import time

import yaml

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from apiserver import APIServer  # noqa: E402

HELM = os.environ.get("HELM", "helm")
POLICY = "agent-platform-platform-helmreleases"
KAGENT = "kagent"
OTHER = "team-a"
EDITOR = "bob"  # agent-platform:editor in the kagent namespace, no Secret rights
SKILLS = "agent-manager-skills-token"
SKILLS_OLD = "old-skills-token"
READABLE = "bob-readable"  # a Secret bob may get
READABLE_MORE = ["bob-readable-2", "bob-readable-3"]  # two more he may get
PLATFORM_SECRET = "kagent-postgres"  # one he may not
KAGENT_RELEASE = "kagent-kagent"  # kagent's own HelmRelease, as a deployer places it
DEPLOYER = "admin"  # a writer of it by user name (a cluster's admin kubeconfig)
DEPLOYERS = "fog-deployers"  # a writer of it by group
FLUX_SA = ("flux-system", "kustomize-controller")  # a writer of it by ServiceAccount
RELEASE_FLAGS = ["--set", f"tenants.platform.kagentRelease.name={KAGENT_RELEASE}",
                 "--set", "tenants.platform.kagentRelease.writers[0].kind=User",
                 "--set", f"tenants.platform.kagentRelease.writers[0].name={DEPLOYER}",
                 "--set", "tenants.platform.kagentRelease.writers[1].kind=Group",
                 "--set", f"tenants.platform.kagentRelease.writers[1].name={DEPLOYERS}",
                 "--set", "tenants.platform.kagentRelease.writers[2].kind=ServiceAccount",
                 "--set", f"tenants.platform.kagentRelease.writers[2].name={FLUX_SA[1]}",
                 "--set", f"tenants.platform.kagentRelease.writers[2].namespace={FLUX_SA[0]}"]

API = None
results = []


def fail(msg):
    sys.exit(f"FAIL: {msg}")


def render(chart, *extra, fails=None):
    """fails: the render must fail with this text in its error."""
    r = subprocess.run([HELM, "template", "t", chart, "--namespace", "agent-platform",
                        "--set", "ingress.parentRefs[0].name=x", "--set", "kagent.harness.snapshotLocation=s3://a/b",
                        "--set", "kagent.database.postgres.urlFile=/etc/kagent-db/uri",
                        "--set", "components.agent-manager.enabled=true", "--set", "agent-manager.oauth.enabled=false",
                        "--set", f"agent-manager.skills.gitAuthSecretName={SKILLS}",
                        "--set", f"agent-manager.skills.previousGitAuthSecretNames[0]={SKILLS_OLD}", *extra],
                       capture_output=True, text=True)
    if fails is not None:
        if r.returncode == 0 or fails not in r.stderr:
            fail(f"the render does not fail with {fails!r}:\n{r.stderr}")
        return []
    if r.returncode != 0:
        fail(f"the render failed:\n{r.stderr}")
    return [d for d in yaml.safe_load_all(r.stdout) if d]


def named(objs, name):
    return [d for d in objs if d["metadata"]["name"] == name]


def check_render(chart):
    on = ["--set", "components.kagent.enabled=true", "--set", f"kagent.namespaceOverride={KAGENT}"]
    if named(render(chart, "--set", "components.kagent.enabled=false", "--set", "components.agent-manager.enabled=false"), POLICY):
        fail("kagent off renders the policy")
    print("ok:  kagent off, no policy renders")
    if named(render(chart, *on, "--set", "kagent.fluxServiceAccountName="), POLICY):
        fail("an empty kagent.fluxServiceAccountName renders the policy")
    print("ok:  an empty kagent.fluxServiceAccountName renders no policy")
    moved = named(render(chart, "--set", "components.kagent.enabled=true", "--set", "kagent.namespaceOverride=agents",
                         "--set", "model-manager.kagent.namespace=agents",
                         "--set", "agent-manager.kagent.namespace=agents"), POLICY)
    sel = [d["spec"]["matchConstraints"]["namespaceSelector"] for d in moved if d["kind"] == "ValidatingAdmissionPolicy"]
    if sel != [{"matchLabels": {"kubernetes.io/metadata.name": "agents"}}]:
        fail(f"kagent.namespaceOverride=agents selects {sel}")
    print("ok:  kagent.namespaceOverride moves the policy")
    objs = named(render(chart, *on), POLICY)
    kinds = sorted(d["kind"] for d in objs)
    if kinds != ["ValidatingAdmissionPolicy", "ValidatingAdmissionPolicyBinding"]:
        fail(f"{POLICY} renders as {kinds}")
    for d in objs:
        if d["kind"] == "ValidatingAdmissionPolicy" and d["spec"].get("failurePolicy") != "Fail":
            fail(f"failurePolicy {d['spec'].get('failurePolicy')}, not Fail")
        if d["kind"] == "ValidatingAdmissionPolicyBinding" and d["spec"].get("validationActions") != ["Deny"]:
            fail(f"validationActions {d['spec'].get('validationActions')}, not [Deny]")
    print("ok:  the policy and its binding render (Fail, Deny)")
    conds = [c["name"] for d in objs if d["kind"] == "ValidatingAdmissionPolicy" for c in d["spec"]["matchConditions"]]
    if "not-kagent-release" in conds:
        fail(f"tenants.platform.kagentRelease unset renders an exemption: {conds}")
    print("ok:  tenants.platform.kagentRelease unset, no exemption renders")
    k = "tenants.platform.kagentRelease"
    for what, flags, err in (
            ("a name without writers", ["--set", f"{k}.name={KAGENT_RELEASE}"], f"{k}.name needs {k}.writers"),
            ("writers without a name", ["--set", f"{k}.writers[0].kind=User", "--set", f"{k}.writers[0].name=x"],
             f"{k}.writers needs {k}.name"),
            ("a writer of another kind", ["--set", f"{k}.name={KAGENT_RELEASE}", "--set", f"{k}.writers[0].kind=Role",
                                          "--set", f"{k}.writers[0].name=x"], "value must be one of 'User', 'Group', 'ServiceAccount'"),
            ("a writer without a name", ["--set", f"{k}.name={KAGENT_RELEASE}", "--set", f"{k}.writers[0].kind=User",
                                         "--set", f"{k}.writers[0].name="], "every writer names"),
            ("a ServiceAccount without its namespace", ["--set", f"{k}.name={KAGENT_RELEASE}",
                                                        "--set", f"{k}.writers[0].kind=ServiceAccount",
                                                        "--set", f"{k}.writers[0].name=x"], "needs its namespace")):
        render(chart, *on, *flags, fails=err)
        print(f"ok:  {what} fails the render")
    objs = named(render(chart, *on, *RELEASE_FLAGS), POLICY)
    conds = {c["name"]: c["expression"] for d in objs if d["kind"] == "ValidatingAdmissionPolicy" for c in d["spec"]["matchConditions"]}
    if "not-kagent-release" not in conds:
        fail(f"tenants.platform.kagentRelease set renders no exemption: {sorted(conds)}")
    print("ok:  tenants.platform.kagentRelease set renders the exemption |", conds["not-kagent-release"])
    editor = [d for d in render(chart, *on) if d["kind"] == "ClusterRole" and d["metadata"]["name"] == "agent-platform:editor"]
    if not editor:
        fail("agent-platform:editor does not render")
    return objs + editor


def flux_crd(meta):
    for d in yaml.safe_load_all(open(os.path.join(meta, "charts", "flux-engine", "crds", "flux.yaml"))):
        if d and d.get("kind") == "CustomResourceDefinition" and d["metadata"]["name"] == "helmreleases.helm.toolkit.fluxcd.io":
            return d
    fail("flux-engine's crds/flux.yaml carries no HelmRelease CRD")


# ---- writes ------------------------------------------------------------------

def kubectl(args, stdin=None, as_=None, groups=("system:masters",)):
    return API.kubectl(args, stdin=stdin, as_=as_, groups=groups)


def must(rc_out, what):
    rc, out = rc_out
    if rc != 0:
        fail(f"{what}:\n{out}")
    return out


def apply(obj):
    must(kubectl(["apply", "--server-side", "-f", "-"], yaml.safe_dump(obj)), f"apply {obj['kind']} {obj['metadata']['name']}")


def create(obj, as_=EDITOR, groups=(), dry=True):
    return kubectl(["create", "-f", "-"] + (["--dry-run=server"] if dry else []), yaml.safe_dump(obj), as_, groups)


def patch(name, ns, body, as_=EDITOR, groups=(), dry=True):
    return kubectl(["patch", "helmrelease", name, "-n", ns, "--type=merge", "-p", json.dumps(body)] + (["--dry-run=server"] if dry else []),
                   None, as_, groups)


def case(name, rc_out, refused=False):
    """refused: the policy's own refusal, not an evaluation error (which
    refuses too under failurePolicy Fail and would read as the one under
    test)."""
    rc, out = rc_out
    last = out.strip().splitlines()[-1] if out.strip() else ""
    if refused:
        ok = (rc != 0 and f"ValidatingAdmissionPolicy '{POLICY}'" in out and "denied request" in out
              and "resulted in error" not in out and "failed expression" not in out)
    else:
        ok = rc == 0
    results.append((ok, name))
    print(("ok:  " if ok else "FAIL:"), name, "|", last)


def git_source(credential):
    return {"source": {"git": {"url": "https://example.com/x.git", "commit": "0" * 40,
                               "credentialRef": {"name": credential, "key": "token"}}, "path": "."}}


def helmrelease(name, namespace=KAGENT, skills=None, extra_agent_spec=None, **spec_extra):
    """An agent's HelmRelease as agent-manager composes it
    (internal/agents/compose.go)."""
    values = {"agent": {"name": name}}
    if skills is not None:
        values["skillsGitAuthSecretRef"] = {"name": skills}
    if extra_agent_spec is not None:
        values["extraAgentSpec"] = extra_agent_spec
    spec = {"interval": "10m", "chartRef": {"kind": "OCIRepository", "name": "agent"}, "serviceAccountName": "kagent-flux",
            "values": values}
    spec.update(spec_extra)
    return {"apiVersion": "helm.toolkit.fluxcd.io/v2", "kind": "HelmRelease", "metadata": {"name": name, "namespace": namespace},
            "spec": {k: v for k, v in spec.items() if v is not None}}


def kagent_release():
    """kagent's own HelmRelease as a deployer that places each workload's
    HelmRelease in its target namespace writes it: its own OCIRepository,
    helm-controller's identity, values from a Secret."""
    return {"apiVersion": "helm.toolkit.fluxcd.io/v2", "kind": "HelmRelease",
            "metadata": {"name": KAGENT_RELEASE, "namespace": KAGENT},
            "spec": {"interval": "10m", "chartRef": {"kind": "OCIRepository", "name": KAGENT_RELEASE},
                     "valuesFrom": [{"kind": "Secret", "name": "kagent-values"}], "values": {"controller": {"replicas": 1}}}}


def fixtures():
    for n in (KAGENT, OTHER):
        must(create({"apiVersion": "v1", "kind": "Namespace", "metadata": {"name": n}}, as_=None, dry=False), f"create namespace {n}")
    subject = [{"apiGroup": "rbac.authorization.k8s.io", "kind": "User", "name": EDITOR}]
    for n in (KAGENT, OTHER):
        apply({"apiVersion": "rbac.authorization.k8s.io/v1", "kind": "RoleBinding", "metadata": {"name": "bob-editor", "namespace": n},
               "roleRef": {"apiGroup": "rbac.authorization.k8s.io", "kind": "ClusterRole", "name": "agent-platform:editor"}, "subjects": subject})
    apply({"apiVersion": "rbac.authorization.k8s.io/v1", "kind": "Role", "metadata": {"name": "bob-readable", "namespace": KAGENT},
           "rules": [{"apiGroups": [""], "resources": ["secrets"], "verbs": ["get"], "resourceNames": [READABLE, *READABLE_MORE]}]})
    apply({"apiVersion": "rbac.authorization.k8s.io/v1", "kind": "RoleBinding", "metadata": {"name": "bob-readable", "namespace": KAGENT},
           "roleRef": {"apiGroup": "rbac.authorization.k8s.io", "kind": "Role", "name": "bob-readable"}, "subjects": subject})
    # Written before the policy, as another identity.
    must(create(helmrelease("legacy", serviceAccountName="kagent-controller"), as_=None, dry=False), "create the legacy HelmRelease")


def install(objs):
    for d in objs:
        apply(d)
    deadline = time.time() + 60
    while time.time() < deadline:
        rc, out = create(helmrelease("probe", valuesFrom=[{"kind": "Secret", "name": "x"}]))
        if rc != 0 and POLICY in out:
            return
        time.sleep(0.5)
    fail("the API server did not apply the policy within 60s")


def prove():
    case("an editor writes agent-manager's shape", create(helmrelease("a")))
    case("an editor writes agent-manager's shape with the chartRef namespace set",
         create(helmrelease("a", chartRef={"kind": "OCIRepository", "name": "agent", "namespace": KAGENT})))
    for field, value in (("valuesFrom", [{"kind": "Secret", "name": PLATFORM_SECRET}]),
                         ("postRenderers", [{"kustomize": {"patches": [{"patch": "[]", "target": {"kind": "Agent"}}]}}]),
                         ("kubeConfig", {"secretRef": {"name": "kubeconfig"}}),
                         ("targetNamespace", OTHER), ("storageNamespace", OTHER),
                         ("chart", {"spec": {"chart": "agent", "sourceRef": {"kind": "HelmRepository", "name": "x"}}})):
        extra = {field: value, **({"chartRef": None} if field == "chart" else {})}
        case(f"a HelmRelease sets no {field}", create(helmrelease("a", **extra)), refused=True)
    case("a HelmRelease installs from no other OCIRepository",
         create(helmrelease("a", chartRef={"kind": "OCIRepository", "name": "evil"})), refused=True)
    case("a HelmRelease installs from no OCIRepository of another namespace",
         create(helmrelease("a", chartRef={"kind": "OCIRepository", "name": "agent", "namespace": OTHER})), refused=True)
    case("a HelmRelease runs as no other ServiceAccount", create(helmrelease("a", serviceAccountName="kagent-controller")), refused=True)
    case("a HelmRelease runs as kagent-flux, never the controller's own identity", create(helmrelease("a", serviceAccountName=None)), refused=True)
    for name in (SKILLS, SKILLS_OLD):
        case(f"a HelmRelease names the installation skills credential {name}", create(helmrelease("a", skills=name)))
    case("a HelmRelease names a Secret its writer may get", create(helmrelease("a", skills=READABLE)))
    case("a HelmRelease names no Secret its writer may not get", create(helmrelease("a", skills=PLATFORM_SECRET)), refused=True)
    case("a cluster-admin names any Secret", create(helmrelease("a", skills=PLATFORM_SECRET), as_="jose", groups=("system:masters",)))
    case("an empty skills credential name passes", create(helmrelease("a", skills="")))
    case("an extraAgentSpec without a credential passes",
         create(helmrelease("a", extra_agent_spec={"systemMessage": "hi", "skills": [{"name": "s", "source": {"oci": "r@sha256:" + "0" * 64}}]})))
    for kind in ("skills", "plugins"):
        entry = lambda c: {"name": "s", **git_source(c)} if kind == "skills" else git_source(c)  # noqa: E731
        case(f"an extraAgentSpec {kind} credentialRef names a Secret its writer may get",
             create(helmrelease("a", extra_agent_spec={kind: [entry(READABLE)]})))
        case(f"an extraAgentSpec {kind} credentialRef names the installation skills credential",
             create(helmrelease("a", extra_agent_spec={kind: [entry(SKILLS)]})))
        case(f"an extraAgentSpec {kind} credentialRef names no Secret its writer may not get",
             create(helmrelease("a", skills=SKILLS, extra_agent_spec={kind: [entry(READABLE), entry(PLATFORM_SECRET)]})), refused=True)
    case("an extraAgentSpec naming one Secret its writer may get six times passes",
         create(helmrelease("a", skills=READABLE, extra_agent_spec={"skills": [{"name": f"s{i}", **git_source(READABLE)} for i in range(6)]})))
    case("the installation skills credential named six times beside two distinct Secrets its writer may get passes",
         create(helmrelease("a", skills=SKILLS, extra_agent_spec={"skills": [{"name": f"s{i}", **git_source(c)} for i, c in
                                                                            enumerate([SKILLS] * 5 + [READABLE, READABLE_MORE[0], READABLE])]})))
    case("three distinct Secrets besides the installation skills credential are refused by the limit",
         create(helmrelease("a", skills=SKILLS, extra_agent_spec={"skills": [{"name": f"s{i}", **git_source(c)} for i, c in
                                                                            enumerate([READABLE, *READABLE_MORE])]})), refused=True)
    case("a cluster-admin's three distinct Secrets are refused by the limit",
         create(helmrelease("a", extra_agent_spec={"skills": [{"name": f"s{i}", **git_source(c)} for i, c in enumerate([READABLE, *READABLE_MORE])]}),
                as_="jose", groups=("system:masters",)), refused=True)
    case("a HelmRelease written before the policy takes an annotation",
         patch("legacy", KAGENT, {"metadata": {"annotations": {"reconcile.fluxcd.io/requestedAt": "now"}}}, as_=None, groups=("system:masters",)))
    case("a HelmRelease written before the policy changes no spec", patch("legacy", KAGENT, {"spec": {"values": {"x": 1}}},
         as_="jose", groups=("system:masters",)), refused=True)
    case("a HelmRelease written before the policy moves to kagent-flux",
         patch("legacy", KAGENT, {"spec": {"serviceAccountName": "kagent-flux"}}, as_="jose", groups=("system:masters",)))
    case("a HelmRelease outside the kagent namespace is not held",
         create(helmrelease("a", OTHER, valuesFrom=[{"kind": "Secret", "name": "x"}], serviceAccountName="x")))
    # kagent's own release (tenants.platform.kagentRelease). The writers are
    # cluster-admins as well, so RBAC lets each write; the policy decides.
    sa = f"system:serviceaccount:{FLUX_SA[0]}:{FLUX_SA[1]}"
    writers = ((f"the user {DEPLOYER}", DEPLOYER, ("system:masters",)),
               (f"the group {DEPLOYERS}", "carol", ("system:masters", DEPLOYERS)),
               (f"the ServiceAccount {FLUX_SA[0]}/{FLUX_SA[1]}", sa, ("system:masters", "system:serviceaccounts")))
    for what, user, groups in writers:
        case(f"{what} creates kagent's own HelmRelease", create(kagent_release(), as_=user, groups=groups))
    case("kagent's own HelmRelease is placed by its deployer", create(kagent_release(), as_=DEPLOYER, groups=("system:masters",), dry=False))
    for what, user, groups in writers:
        case(f"{what} upgrades kagent's own HelmRelease",
             patch(KAGENT_RELEASE, KAGENT, {"spec": {"values": {"controller": {"replicas": 2}}}}, as_=user, groups=groups))
    case("kagent's own HelmRelease is upgraded with the binding in place",
         patch(KAGENT_RELEASE, KAGENT, {"spec": {"values": {"controller": {"replicas": 2}}}}, as_=DEPLOYER, groups=("system:masters",), dry=False))
    case("an editor writes no kagent release", create(dict(kagent_release(), metadata={"name": "evil", "namespace": KAGENT})), refused=True)
    case("an editor does not upgrade kagent's own HelmRelease",
         patch(KAGENT_RELEASE, KAGENT, {"spec": {"values": {"controller": {"replicas": 3}}}}), refused=True)
    case("a cluster-admin who is no writer does not upgrade kagent's own HelmRelease",
         patch(KAGENT_RELEASE, KAGENT, {"spec": {"values": {"controller": {"replicas": 3}}}}, as_="jose", groups=("system:masters",)),
         refused=True)
    case("a cluster-admin who is no writer writes no HelmRelease of kagent's shape",
         create(dict(kagent_release(), metadata={"name": "kagent-other", "namespace": KAGENT}), as_="jose", groups=("system:masters",)),
         refused=True)
    for what, user, groups in writers:
        case(f"{what} writes no other HelmRelease of kagent's shape",
             create(dict(kagent_release(), metadata={"name": "kagent-other", "namespace": KAGENT}), as_=user, groups=groups), refused=True)


def main():
    global API
    meta, chart = sys.argv[1:3]
    objs = check_render(chart)
    crd = flux_crd(meta)
    API = APIServer()
    try:
        apply(crd)
        must(kubectl(["wait", "--for=condition=Established", "crd", "--all", "--timeout=60s"]), "wait for the CRD")
        fixtures()
        install(objs)
        prove()
    finally:
        API.stop()
    failed = [n for ok, n in results if not ok]
    print(f"{len(results) - len(failed)}/{len(results)} cases as expected")
    if failed:
        fail("; ".join(failed))


if __name__ == "__main__":
    main()
