#!/usr/bin/env python3
"""Prove what the Agent Platform organizations' role and admission policies decide
(giantswarm/agent-platform#1267), on a local API server.

The connectivity chart renders, with agentPlatformOrganizations.enabled, the
ClusterRole agent-platform:organizations-admin and the
ValidatingAdmissionPolicies of templates/organizations/admission.yaml and
substrate/tenant-workers-admission.yaml. This script renders them, starts an
API server (tests/apiserver.py: envtest's etcd and kube-apiserver, RBAC on,
no controllers), installs the CRDs of what the policies hold
(AgentPlatformOrganization from the agent-platform-operator chart, Flux's
HelmRelease, OCIRepository and Kustomization, kagent's EgressPolicy,
Substrate's WorkerPool, Kyverno's PolicyException), the three tenant roles and
every rendered policy, and writes as the people and controllers involved with
server-side dry runs. Every refusal must name the policy (or RBAC) that
refuses it, and each refusal has a passing twin that differs only in what the
policy holds:

- organizations-admin (by ClusterRoleBinding, and by a RoleBinding in a parent
  the operator mirrors) creates organizations and binds user, editor and admin
  in organization namespaces, nothing else there, and no RoleBinding
  elsewhere; a cluster-admin is not held;
- an editor reads and never writes the agent chart's OCIRepository;
- the operator's ServiceAccount creates only agents-<organization>-*
  namespaces with the labels, deletes only labelled ones, writes RoleBindings
  only in organization namespaces, and Kustomizations and ServiceAccounts in
  the Flux namespace only when labelled with the organization's UID;
- only the operator sets, changes or removes the organization labels;
- organization-admins is not deleted or emptied while its namespace lives;
- a RoleBinding there binds no system: identity and no ServiceAccount of
  another namespace;
- the HelmRelease field lock, also for the organization's Flux ServiceAccount;
- an organization is deleted only with prune: enabled or while its parent
  terminates;
- the EgressPolicy floor;
- the operator's copies are the operator's;
- the sandbox shape holds with a Kyverno PolicyException for agents-* in place.

Needs helm (gsoci.azurecr.io), openssl, PyYAML and github.com on the first run
(the envtest binaries, the Flux install manifest, the Kyverno CRD). HELM
selects the binary.

Usage: verify-organization-admission.py META_CHART CONNECTIVITY_CHART
"""
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.request

import yaml

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from apiserver import APIServer  # noqa: E402

HELM = os.environ.get("HELM", "helm")
# The operator's chart carries the AgentPlatformOrganization CRD: the floor of
# components.agent-platform-operator.versionRange (read in crds()).
OPERATOR_CHART = "oci://gsoci.azurecr.io/charts/giantswarm/agent-platform-operator"
FLUX_INSTALL = "https://github.com/fluxcd/flux2/releases/download/{v}/install.yaml"
KYVERNO_VERSION = "v1.19.1"  # the kyverno CLI CI installs (.circleci/custom.yml)
KYVERNO_CRD = "https://raw.githubusercontent.com/kyverno/kyverno/{v}/config/crds/kyverno/kyverno.io_policyexceptions.yaml"

RELEASE_NS = "agent-platform"
FLUX_NS = "flux-giantswarm"
API_HOST = "api.acme.example.io"
POOL = "kagent-default"
OP = f"system:serviceaccount:{RELEASE_NS}:agent-platform-operator"
FLUX_SA = f"system:serviceaccount:{FLUX_NS}:agents-payments"
SOURCE_CONTROLLER = f"system:serviceaccount:{FLUX_NS}:source-controller"
NS_CONTROLLER = "system:serviceaccount:kube-system:namespace-controller"
PERSON = "jose"  # with system:masters: a cluster-admin
ALICE = "alice"  # organizations-admin by ClusterRoleBinding (platform staff)
CAROL = "carol"  # organizations-admin by RoleBinding in org-acme (a customer platform team)
BOB = "bob"  # agent-platform:editor in the organization namespace

PARENT = "org-acme"
ORG = "agents-payments-x7k2q"
ORG2 = "agents-billing-b4n9d"
OLD = "agents-old-aaaaa"  # terminating
GONE = "org-gone"  # a terminating parent
PLAIN = "team-a"
UID = "6c1f9a52-0d7e-4b3a-9a51-2f4f0e1a2b3c"
LABELS = {"agent-platform.giantswarm.io/organization": "payments", "agent-platform.giantswarm.io/parent": PARENT,
          "agent-platform.giantswarm.io/organization-uid": UID}
OBJECT_LABEL = "agent-platform.giantswarm.io/organization-object"

API = None
results = []


def fail(msg):
    sys.exit(f"FAIL: {msg}")


def run(cmd):
    return subprocess.run(cmd, capture_output=True, text=True)


# ---- the render --------------------------------------------------------------

def render(chart, *extra):
    return run([HELM, "template", "t", chart, "--namespace", RELEASE_NS,
                "--set", "ingress.parentRefs[0].name=x", "--set", "kagent.harness.snapshotLocation=s3://a/b",
                "--set", "kagent.database.postgres.urlFile=/etc/kagent-db/uri", "--set", "kagent.namespaceOverride=kagent",
                "--set", "components.kagent.enabled=true", *extra])


ON = ["--set", "agentPlatformOrganizations.enabled=true", "--set", f"agentPlatformOrganizations.fluxNamespace={FLUX_NS}",
      "--set", f"agentPlatformOrganizations.apiHostnames[0]={API_HOST}",
      # An organization's ModelConfigs go through the llm listener (giantswarm/agent-platform#1268).
      "--set", "llmRouting.enabled=true", "--api-versions", "admissionregistration.k8s.io/v1/MutatingAdmissionPolicy"]
SUBSTRATE = ["--set", "components.substrate.enabled=true", "--set", "components.substrate-crds.enabled=true"]


def docs(r):
    if r.returncode != 0:
        fail(f"the render failed:\n{r.stderr}")
    return [d for d in yaml.safe_load_all(r.stdout) if d]


def organization_objects(objs):
    return [d for d in objs if d["metadata"]["name"].startswith(("agent-platform-organization", "agent-platform-organizations-admin"))
            or d["metadata"]["name"] == "agent-platform:organizations-admin"]


def check_render(chart):
    off = organization_objects(docs(render(chart, *SUBSTRATE)))
    if off:
        fail(f"agentPlatformOrganizations.enabled off renders {[d['metadata']['name'] for d in off]}")
    print("ok:  off, no organization role or policy renders")
    r = render(chart, *ON, "--set", "components.kagent.enabled=false")
    if r.returncode == 0 or "agentPlatformOrganizations.enabled needs kagent" not in r.stderr:
        fail(f"agentPlatformOrganizations.enabled with kagent off renders, or fails for another reason:\n{r.stderr}")
    print("ok:  on with kagent off, the render fails naming kagent")
    r = render(chart, *ON[:4], *ON[6:])
    if r.returncode == 0 or "agentPlatformOrganizations.enabled needs the installation's Kubernetes API hostnames" not in r.stderr:
        fail(f"agentPlatformOrganizations.enabled without apiHostnames and global.domain renders, or fails for another reason:\n{r.stderr}")
    print("ok:  on without apiHostnames and global.domain, the render fails naming the keys")
    floor = [d for d in docs(render(chart, *ON[:4], *ON[6:], "--set", "global.domain=acme.example.io"))
             if d["kind"] == "ValidatingAdmissionPolicy" and d["metadata"]["name"] == "agent-platform-organization-egress-floor"]
    if not floor or "'api.acme.example.io'" not in yaml.safe_dump(floor[0]):
        fail("with global.domain and no apiHostnames, the EgressPolicy floor does not name api.<global.domain>")
    print("ok:  with global.domain, the EgressPolicy floor names api.<global.domain>")
    no_substrate = {d["metadata"]["name"] for d in organization_objects(docs(render(chart, *ON)))}
    if {"agent-platform-organization-workers", "agent-platform-organization-workerpool-copy"} & no_substrate:
        fail("the organization workers' policies render with Substrate off")
    print("ok:  on with Substrate off, no workers policy renders")
    objs = docs(render(chart, *ON, *SUBSTRATE))
    roles = [d for d in objs if d["kind"] == "ClusterRole" and d["metadata"]["name"] in
             ("agent-platform:user", "agent-platform:editor", "agent-platform:admin", "agent-platform:organizations-admin")]
    policies = [d for d in organization_objects(objs) if d["kind"] != "ClusterRole"]
    # The sandbox shape is admission's own object, not Kyverno's: a
    # PolicyException excepts from Kyverno policies only.
    for name in ("agent-platform-organization-workers", "agent-platform-organization-workerpool-copy"):
        kinds = {(d["apiVersion"], d["kind"]) for d in policies if d["metadata"]["name"] == name}
        if kinds != {("admissionregistration.k8s.io/v1", "ValidatingAdmissionPolicy"), ("admissionregistration.k8s.io/v1", "ValidatingAdmissionPolicyBinding")}:
            fail(f"{name} renders as {sorted(kinds)}, not a ValidatingAdmissionPolicy and its binding")
    for d in policies:
        if d["kind"] == "ValidatingAdmissionPolicyBinding" and d["spec"].get("validationActions") != ["Deny"]:
            fail(f"{d['metadata']['name']}: validationActions {d['spec'].get('validationActions')}, not [Deny]")
        if d["kind"] == "ValidatingAdmissionPolicy" and d["spec"].get("failurePolicy") != "Fail":
            fail(f"{d['metadata']['name']}: failurePolicy {d['spec'].get('failurePolicy')}, not Fail")
    return roles, policies


# ---- the CRDs ----------------------------------------------------------------

def fetch(url):
    for attempt in range(3):
        try:
            with urllib.request.urlopen(url, timeout=120) as r:
                return r.read().decode()
        except OSError as e:
            if attempt == 2:
                fail(f"download {url}: {e}")
            time.sleep(5)


def crds(meta, tmp):
    """The CRDs, as one multi-document YAML."""
    out = []
    values = yaml.safe_load(open(os.path.join(meta, "values.yaml")))
    operator = re.match(r"^(?:>=\s*)?(\S+)", values["components"]["agent-platform-operator"]["versionRange"]).group(1)
    r = run([HELM, "pull", OPERATOR_CHART, "--version", operator, "--untar", "--untardir", tmp])
    if r.returncode != 0:
        fail(f"helm pull {OPERATOR_CHART} {operator}:\n{r.stderr}")
    crd_dir = os.path.join(tmp, "agent-platform-operator", "crds")
    out += [open(os.path.join(crd_dir, f)).read() for f in sorted(os.listdir(crd_dir))]
    flux = open(os.path.join(meta, "charts", "flux-engine", "crds", "flux.yaml")).read()
    out.append(flux)
    version = re.search(r"--version=(v\d+\.\d+\.\d+)", flux).group(1)
    install = [d for d in yaml.safe_load_all(fetch(FLUX_INSTALL.format(v=version))) if d]
    out += [yaml.safe_dump(d) for d in install if d["kind"] == "CustomResourceDefinition" and d["metadata"]["name"] == "kustomizations.kustomize.toolkit.fluxcd.io"]
    values = yaml.safe_load(open(os.path.join(meta, "values.yaml")))
    for name, keep in (("kagent-crds", "egresspolicies.api.kagent.dev"), ("substrate-crds", "workerpools.ate.dev")):
        component = values["components"][name]
        floor = re.match(r"^>=\s*(\S+)", component["versionRange"]).group(1)
        r = run([HELM, "template", "t", f"{component['repository']}/{name}", "--version", floor])
        if r.returncode != 0:
            fail(f"helm template {name} {floor}:\n{r.stderr}")
        out += [yaml.safe_dump(d) for d in yaml.safe_load_all(r.stdout) if d and d.get("metadata", {}).get("name") == keep]
    out.append(fetch(KYVERNO_CRD.format(v=KYVERNO_VERSION)))
    return "\n---\n".join(out)


# ---- writes ------------------------------------------------------------------

def kubectl(args, stdin=None, as_=None, groups=("system:masters",)):
    return API.kubectl(args, stdin=stdin, as_=as_, groups=groups)


def must(rc_out, what):
    rc, out = rc_out
    if rc != 0:
        fail(f"{what}:\n{out}")
    return out


def create(obj, as_=None, groups=("system:masters",), dry=True):
    return kubectl(["create", "-f", "-"] + (["--dry-run=server"] if dry else []), yaml.safe_dump(obj), as_, groups)


def patch(kind, name, ns, body, as_=None, groups=("system:masters",)):
    args = ["patch", kind, name, "--type=merge", "-p", json.dumps(body), "--dry-run=server"] + (["-n", ns] if ns else [])
    return kubectl(args, None, as_, groups)


def delete(kind, name, ns, as_=None, groups=("system:masters",)):
    args = ["delete", kind, name, "--dry-run=server", "--wait=false"] + (["-n", ns] if ns else [])
    return kubectl(args, None, as_, groups)


def can_i(verb, resource, ns, as_):
    """The authorizer's answer (a SubjectAccessReview): "yes" or "no"."""
    rc, out = kubectl(["auth", "can-i", verb, resource, "-n", ns], None, as_, ())
    if rc not in (0, 1):
        fail(f"kubectl auth can-i {verb} {resource} as {as_}:\n{out}")
    return "yes" if rc == 0 else "no"


def case(name, rc_out, by=None):
    """by=None: admitted. by=<policy>: refused by that ValidatingAdmissionPolicy.
    by='RBAC:<text>': refused by the authorizer with that text."""
    rc, out = rc_out
    last = out.strip().splitlines()[-1] if out.strip() else ""
    if by is None:
        ok = rc == 0
    elif by.startswith("RBAC:"):
        ok = rc != 0 and by[5:] in out
    else:
        # The policy's own message, not an evaluation error: an expression
        # that fails to evaluate refuses too (failurePolicy Fail), naming
        # the policy, and would read as the refusal under test.
        ok = (rc != 0 and f"ValidatingAdmissionPolicy '{by}'" in out and "denied request" in out
              and "resulted in error" not in out and "failed expression" not in out)
    results.append((ok, name))
    print(("ok:  " if ok else "FAIL:"), name, "|", last)


def ns(name, labels=None):
    return {"apiVersion": "v1", "kind": "Namespace", "metadata": {"name": name, "labels": labels or {}}}


def rb(name, namespace, role="agent-platform:editor", subjects=None, kind="ClusterRole"):
    return {"apiVersion": "rbac.authorization.k8s.io/v1", "kind": "RoleBinding", "metadata": {"name": name, "namespace": namespace},
            "roleRef": {"apiGroup": "rbac.authorization.k8s.io", "kind": kind, "name": role},
            "subjects": subjects if subjects is not None else [{"apiGroup": "rbac.authorization.k8s.io", "kind": "Group", "name": "acme-devs"}]}


def crb(name, role, user):
    return {"apiVersion": "rbac.authorization.k8s.io/v1", "kind": "ClusterRoleBinding", "metadata": {"name": name},
            "roleRef": {"apiGroup": "rbac.authorization.k8s.io", "kind": "ClusterRole", "name": role},
            "subjects": [{"apiGroup": "rbac.authorization.k8s.io", "kind": "User", "name": user}]}


def apo(name, namespace, prune=None):
    meta = {"name": name, "namespace": namespace}
    if prune:
        meta["annotations"] = {"kustomize.toolkit.fluxcd.io/prune": prune}
    return {"apiVersion": "agent-platform.giantswarm.io/v1alpha1", "kind": "AgentPlatformOrganization", "metadata": meta, "spec": {}}


def helmrelease(name, namespace, **spec_extra):
    spec = {"interval": "10m", "chartRef": {"kind": "OCIRepository", "name": "agent"}, "serviceAccountName": "kagent-flux",
            "values": {"agent": {"name": name}}}
    spec.update(spec_extra)
    return {"apiVersion": "helm.toolkit.fluxcd.io/v2", "kind": "HelmRelease", "metadata": {"name": name, "namespace": namespace},
            "spec": {k: v for k, v in spec.items() if v is not None}}


def kustomization(name, namespace, uid=True):
    meta = {"name": name, "namespace": namespace}
    if uid:
        meta["labels"] = {"agent-platform.giantswarm.io/organization-uid": UID}
    return {"apiVersion": "kustomize.toolkit.fluxcd.io/v1", "kind": "Kustomization", "metadata": meta,
            "spec": {"interval": "10m", "prune": True, "path": "./agents", "serviceAccountName": "agents-payments",
                     "sourceRef": {"kind": "GitRepository", "name": "installation"}}}


def sa(name, namespace, uid=True):
    meta = {"name": name, "namespace": namespace}
    if uid:
        meta["labels"] = {"agent-platform.giantswarm.io/organization-uid": UID}
    return {"apiVersion": "v1", "kind": "ServiceAccount", "metadata": meta}


def egress(name, namespace, *origins):
    return {"apiVersion": "api.kagent.dev/v1alpha3", "kind": "EgressPolicy", "metadata": {"name": name, "namespace": namespace},
            "spec": {"destinations": [{"origin": o} for o in origins]}}


def ocirepository(namespace, labelled=True):
    meta = {"name": "agent", "namespace": namespace}
    if labelled:
        meta["labels"] = {OBJECT_LABEL: "namespace", "app.kubernetes.io/managed-by": "agent-platform-operator"}
    return {"apiVersion": "source.toolkit.fluxcd.io/v1", "kind": "OCIRepository", "metadata": meta,
            "spec": {"interval": "30m", "url": "oci://gsoci.azurecr.io/charts/giantswarm/agent", "ref": {"semver": "2.x"}}}


def workerpool(namespace, sandbox_class="gvisor", name=POOL, replicas=2):
    return {"apiVersion": "ate.dev/v1alpha1", "kind": "WorkerPool", "metadata": {"name": name, "namespace": namespace},
            "spec": {"replicas": replicas, "workerImage": "ghcr.io/x/ateom:1", "sandboxClass": sandbox_class,
                     "template": {"labels": {"team": "x"}, "serviceAccountName": "workers"}}}


# ---- the proof ---------------------------------------------------------------

def fixtures():
    """What exists before the policies: the namespaces, the platform's pool,
    a HelmRelease written before the lock, the objects each case acts on."""
    for n, labels in ((PARENT, None), (GONE, None), (PLAIN, None), (FLUX_NS, None), ("kagent", None), (RELEASE_NS, None),
                      (ORG, LABELS), (ORG2, {**LABELS, "agent-platform.giantswarm.io/organization": "billing"}), (OLD, LABELS)):
        must(create(ns(n, labels), dry=False), f"create namespace {n}")
    objects = [
        workerpool("kagent", replicas=1),
        apo("payments", PARENT), apo("retired", PARENT, prune="enabled"), apo("kept", PARENT, prune="disabled"), apo("gone", GONE),
        rb("organization-admins", ORG, role="agent-platform:admin"), rb("organization-admins", OLD, role="agent-platform:admin"),
        rb("members", ORG),
        # organizations-admin: alice by ClusterRoleBinding; carol in her
        # parent; bob an editor of the organization.
        crb("alice-organizations-admin", "agent-platform:organizations-admin", ALICE),
        rb("carol-organizations-admin", PARENT, role="agent-platform:organizations-admin",
           subjects=[{"apiGroup": "rbac.authorization.k8s.io", "kind": "User", "name": CAROL}]),
        rb("bob-editor", ORG, subjects=[{"apiGroup": "rbac.authorization.k8s.io", "kind": "User", "name": BOB}]),
        helmrelease("legacy", ORG, valuesFrom=[{"kind": "Secret", "name": "tenant-credential-x"}]),
        ocirepository(ORG),
        # A curated copy as agent-platform-operator keeps it: the WorkerPool's
        # ServiceAccount, labelled curated, annotated with its source.
        {"apiVersion": "v1", "kind": "ServiceAccount", "metadata": {"name": "workers", "namespace": ORG,
         "labels": {OBJECT_LABEL: "curated"}, "annotations": {"agent-platform.giantswarm.io/copied-from": "kagent/workers"}},
         "automountServiceAccountToken": False},
        kustomization("agents-payments", FLUX_NS), kustomization("installation", FLUX_NS, uid=False),
    ]
    for o in objects:
        must(create(o, dry=False), f"create {o['kind']} {o['metadata'].get('namespace', '')}/{o['metadata']['name']}")
    for n in (OLD, GONE):
        must(kubectl(["delete", "namespace", n, "--wait=false"]), f"delete namespace {n}")


def install(roles, policies):
    for d in roles + policies:
        must(kubectl(["apply", "--server-side", "-f", "-"], yaml.safe_dump(d)), f"apply {d['kind']} {d['metadata']['name']}")
    # The API server compiles a policy and its binding on its own time: wait
    # for the refusals of two of them; the others load with them. No
    # controller manager runs, so no policy gets a type-check status: an
    # expression that does not fit its kind fails to evaluate, which case()
    # tells from a refusal.
    probe = egress("probe", ORG, "https://kubernetes.default")
    deadline = time.time() + 60
    while time.time() < deadline:
        rc, out = create(probe)
        ready = rc != 0 and "agent-platform-organization-egress-floor" in out
        rc, out = create(workerpool(ORG, sandbox_class="microvm"), as_=OP)
        if ready and rc != 0 and "agent-platform-organization-workerpool-copy" in out:
            break
        time.sleep(0.5)
    else:
        fail("the API server did not apply the policies within 60s")


def prove_roles():
    case("organizations-admin (ClusterRoleBinding) creates an AgentPlatformOrganization in a parent", create(apo("new", PARENT), as_=ALICE, groups=()))
    case("organizations-admin binds agent-platform:editor in an organization namespace", create(rb("devs", ORG), as_=ALICE, groups=()))
    for role in ("agent-platform:user", "agent-platform:admin"):
        case(f"organizations-admin binds {role} in an organization namespace", create(rb("devs", ORG, role=role), as_=ALICE, groups=()))
    case("organizations-admin cannot bind cluster-admin in an organization namespace",
         create(rb("devs", ORG, role="cluster-admin"), as_=ALICE, groups=()), "RBAC:attempting to grant RBAC permissions not currently held")
    held = [f"{v} {r}" for v, r in (("get", "secrets"), ("create", "helmreleases.helm.toolkit.fluxcd.io"), ("get", "agents.api.kagent.dev"),
                                    ("create", "egresspolicies.api.kagent.dev"), ("get", "sessions.api.kagent.dev")) if can_i(v, r, ORG, ALICE) != "no"]
    case("organizations-admin holds nothing else in an organization namespace (secrets, HelmReleases, agents, egress, sessions)",
         (1 if held else 0, "holds: " + ", ".join(held)))
    case("organizations-admin writes no RoleBinding in its parent", create(rb("devs", PARENT), as_=ALICE, groups=()),
         "agent-platform-organizations-admin-rolebindings")
    case("organizations-admin writes no RoleBinding in a namespace that is no organization's", create(rb("devs", PLAIN), as_=ALICE, groups=()),
         "agent-platform-organizations-admin-rolebindings")
    case("organizations-admin deletes no RoleBinding outside organization namespaces", delete("rolebinding", "carol-organizations-admin", PARENT, as_=ALICE, groups=()),
         "agent-platform-organizations-admin-rolebindings")
    case("a cluster-admin writes a RoleBinding in a parent (not held)", create(rb("devs", PARENT), as_=PERSON))
    # The built-in admin role is aggregated by the controller manager, which
    # does not run here: a Role with its RBAC rules stands in for it.
    must(create({"apiVersion": "rbac.authorization.k8s.io/v1", "kind": "Role", "metadata": {"name": "namespace-admin", "namespace": PLAIN},
                 "rules": [{"apiGroups": ["rbac.authorization.k8s.io"], "resources": ["roles", "rolebindings"],
                           "verbs": ["create", "delete", "deletecollection", "get", "list", "patch", "update", "watch"]}]}, dry=False),
         "create the namespace admin Role of team-a")
    must(create(rb("alice-admin", PLAIN, role="namespace-admin", kind="Role",
                   subjects=[{"apiGroup": "rbac.authorization.k8s.io", "kind": "User", "name": ALICE}]), dry=False), "bind alice as admin of team-a")
    case("organizations-admin who may create Roles in a namespace (its admin) writes RoleBindings there (not held)",
         create(rb("devs", PLAIN, role="namespace-admin", kind="Role"), as_=ALICE, groups=()))
    case("organizations-admin by RoleBinding in a parent writes no RoleBinding there", create(rb("devs", PARENT), as_=CAROL, groups=()),
         "agent-platform-organizations-admin-rolebindings")
    must(create(rb("carol-organizations-admin", ORG, role="agent-platform:organizations-admin",
                   subjects=[{"apiGroup": "rbac.authorization.k8s.io", "kind": "User", "name": CAROL}]), as_=OP, dry=False), "mirror carol's binding")
    case("organizations-admin by RoleBinding, mirrored by the operator, binds members in the organization namespace",
         create(rb("devs", ORG), as_=CAROL, groups=()))
    answers = {f"{v} ocirepositories": can_i(v, "ocirepositories.source.toolkit.fluxcd.io", ORG, BOB) for v in ("create", "update", "delete", "get")}
    wrong = {k: a for k, a in answers.items() if a != ("yes" if k.startswith("get") else "no")}
    case("an editor reads the agent chart's OCIRepository and never writes it", (1 if wrong else 0, json.dumps(answers)))
    case("an editor creates an agent's HelmRelease (agent-manager's shape)", create(helmrelease("bobs", ORG), as_=BOB, groups=()))


def prove_operator():
    gen = {"apiVersion": "v1", "kind": "Namespace", "metadata": {"generateName": "agents-payments-", "labels": LABELS}}
    case("the operator creates agents-<organization>-* with the labels", create(gen, as_=OP))
    case("the operator creates no namespace outside agents-*", create(ns("payments-x7k2q", LABELS), as_=OP), "agent-platform-organization-operator-namespaces")
    case("the operator creates no agents-* namespace of another organization's name",
         create({**gen, "metadata": {**gen["metadata"], "generateName": "agents-billing-"}}, as_=OP), "agent-platform-organization-operator-namespaces")
    unlabelled = {**LABELS}
    del unlabelled["agent-platform.giantswarm.io/organization-uid"]
    case("the operator creates no namespace without the organization-uid label",
         create({**gen, "metadata": {**gen["metadata"], "labels": unlabelled}}, as_=OP), "agent-platform-organization-operator-namespaces")
    case("the operator deletes an organization namespace", delete("namespace", ORG, None, as_=OP))
    case("the operator deletes no other namespace", delete("namespace", PLAIN, None, as_=OP), "agent-platform-organization-operator-namespaces")
    case("the operator updates no other namespace", patch("namespace", PLAIN, None, {"metadata": {"labels": {"x": "y"}}}, as_=OP),
         "agent-platform-organization-operator-namespaces")
    case("the operator writes a RoleBinding in an organization namespace", create(rb("op", ORG), as_=OP))
    case("the operator writes no RoleBinding in a parent", create(rb("op", PARENT), as_=OP), "agent-platform-organization-operator-objects")
    case("the operator creates its labelled Kustomization in the Flux namespace", create(kustomization("agents-billing", FLUX_NS), as_=OP))
    case("the operator creates no unlabelled Kustomization", create(kustomization("other", FLUX_NS, uid=False), as_=OP),
         "agent-platform-organization-operator-objects")
    nosa = kustomization("agents-billing", FLUX_NS)
    del nosa["spec"]["serviceAccountName"]
    case("the operator creates no Kustomization that Flux applies with its own identity (no serviceAccountName)", create(nosa, as_=OP),
         "agent-platform-organization-operator-objects")
    controller = kustomization("agents-billing", FLUX_NS)
    controller["spec"]["serviceAccountName"] = "kustomize-controller"
    case("the operator creates no Kustomization that runs as a Flux controller", create(controller, as_=OP),
         "agent-platform-organization-operator-objects")
    case("the operator creates no Kustomization outside the Flux namespace", create(kustomization("agents-billing", PLAIN), as_=OP),
         "agent-platform-organization-operator-objects")
    case("the operator deletes its labelled Kustomization", delete("kustomization", "agents-payments", FLUX_NS, as_=OP))
    case("the operator deletes no Kustomization it does not label", delete("kustomization", "installation", FLUX_NS, as_=OP),
         "agent-platform-organization-operator-objects")
    case("the operator removes no UID label from a Kustomization",
         patch("kustomization", "agents-payments", FLUX_NS, {"metadata": {"labels": {"agent-platform.giantswarm.io/organization-uid": None}}}, as_=OP),
         "agent-platform-organization-operator-objects")
    case("the operator creates its labelled ServiceAccount in the Flux namespace", create(sa("agents-payments", FLUX_NS), as_=OP))
    case("the operator creates no unlabelled ServiceAccount in the Flux namespace", create(sa("x", FLUX_NS, uid=False), as_=OP),
         "agent-platform-organization-operator-objects")
    case("the operator creates a ServiceAccount in an organization namespace", create(sa("kagent-flux", ORG, uid=False), as_=OP))
    case("the operator creates no ServiceAccount elsewhere", create(sa("x", PLAIN), as_=OP), "agent-platform-organization-operator-objects")
    case("the operator mirrors no system: group", create(rb("mirror", ORG, role="agent-platform:organizations-admin",
         subjects=[{"apiGroup": "rbac.authorization.k8s.io", "kind": "Group", "name": "system:authenticated"}]), as_=OP),
         "agent-platform-organization-rolebinding-subjects")
    case("the operator binds no ServiceAccount of a namespace other than the Flux namespace",
         create(rb("x", ORG, subjects=[{"kind": "ServiceAccount", "name": "kagent-controller", "namespace": "kagent"}]), as_=OP),
         "agent-platform-organization-rolebinding-subjects")
    case("a cluster-admin binds no ServiceAccount of the Flux namespace",
         create(rb("x", ORG, subjects=[{"kind": "ServiceAccount", "name": "agents-payments", "namespace": FLUX_NS}]), as_=PERSON),
         "agent-platform-organization-rolebinding-subjects")
    case("the operator binds the organization's Flux ServiceAccount in its namespace",
         create(rb("agents-payments-flux", ORG, role="admin", subjects=[{"kind": "ServiceAccount", "name": "agents-payments", "namespace": FLUX_NS}]), as_=OP))


def prove_labels():
    case("a cluster-admin creates a namespace without the organization labels", create(ns("agents-mine-aaaaa"), as_=PERSON))
    case("a cluster-admin creates no namespace with them", create(ns("agents-mine-aaaaa", LABELS), as_=PERSON), "agent-platform-organization-labels")
    case("a cluster-admin adds no organization label to a namespace",
         patch("namespace", PLAIN, None, {"metadata": {"labels": {"agent-platform.giantswarm.io/organization": "x"}}}, as_=PERSON), "agent-platform-organization-labels")
    case("a cluster-admin changes no organization label",
         patch("namespace", ORG, None, {"metadata": {"labels": {"agent-platform.giantswarm.io/parent": "org-other"}}}, as_=PERSON), "agent-platform-organization-labels")
    case("a cluster-admin removes no organization label",
         patch("namespace", ORG, None, {"metadata": {"labels": {"agent-platform.giantswarm.io/organization-uid": None}}}, as_=PERSON), "agent-platform-organization-labels")
    case("a cluster-admin changes another label of an organization namespace",
         patch("namespace", ORG, None, {"metadata": {"labels": {"team": "payments"}}}, as_=PERSON))
    case("the operator changes an organization label", patch("namespace", ORG, None, {"metadata": {"labels": {"agent-platform.giantswarm.io/parent": "org-other"}}}, as_=OP))


def prove_admins_binding():
    case("organization-admins is not deleted while its namespace lives", delete("rolebinding", "organization-admins", ORG, as_=PERSON),
         "agent-platform-organization-admins-binding")
    case("organization-admins is not emptied while its namespace lives", patch("rolebinding", "organization-admins", ORG, {"subjects": []}, as_=PERSON),
         "agent-platform-organization-admins-binding")
    case("organization-admins gets another subject",
         patch("rolebinding", "organization-admins", ORG, {"subjects": [{"apiGroup": "rbac.authorization.k8s.io", "kind": "Group", "name": "acme-devs"},
                                                                         {"apiGroup": "rbac.authorization.k8s.io", "kind": "Group", "name": "acme-leads"}]}, as_=PERSON))
    case("another binding of an organization namespace is deleted", delete("rolebinding", "members", ORG, as_=PERSON))
    case("organization-admins is deleted while its namespace terminates", delete("rolebinding", "organization-admins", OLD, as_=PERSON))
    case("organization-admins is emptied while its namespace terminates", patch("rolebinding", "organization-admins", OLD, {"subjects": []}, as_=PERSON))
    case("organization-admins binds agent-platform:admin", create(rb("organization-admins", ORG2, role="agent-platform:admin"), as_=PERSON))
    case("organization-admins binds nothing else", create(rb("organization-admins", ORG2, role="agent-platform:user"), as_=PERSON),
         "agent-platform-organization-admins-binding")


def prove_subjects():
    for kind, name, extra in (("Group", "system:authenticated", {}), ("Group", "system:serviceaccounts", {}),
                              ("User", "system:anonymous", {}), ("User", "system:serviceaccount:kagent:kagent-controller", {}),
                              ("ServiceAccount", "kagent-controller", {"namespace": "kagent"})):
        subject = {"kind": kind, "name": name, **extra, **({} if kind == "ServiceAccount" else {"apiGroup": "rbac.authorization.k8s.io"})}
        case(f"a RoleBinding in an organization namespace binds no {kind} {extra.get('namespace', '') and extra['namespace'] + '/'}{name}",
             create(rb("s", ORG, subjects=[subject]), as_=PERSON), "agent-platform-organization-rolebinding-subjects")
    case("a RoleBinding binds a ServiceAccount of its own namespace",
         create(rb("s", ORG, subjects=[{"kind": "ServiceAccount", "name": "ci", "namespace": ORG}]), as_=PERSON))
    case("a RoleBinding binds the user of a ServiceAccount of its own namespace",
         create(rb("s", ORG, subjects=[{"apiGroup": "rbac.authorization.k8s.io", "kind": "User", "name": f"system:serviceaccount:{ORG}:ci"}]), as_=PERSON))
    case("a RoleBinding outside organization namespaces is not held",
         create(rb("s", PLAIN, subjects=[{"apiGroup": "rbac.authorization.k8s.io", "kind": "Group", "name": "system:authenticated"}]), as_=PERSON))


def prove_helmreleases():
    case("the organization's Flux ServiceAccount writes agent-manager's shape", create(helmrelease("a", ORG), as_=FLUX_SA))
    case("the organization's Flux ServiceAccount writes no valuesFrom",
         create(helmrelease("a", ORG, valuesFrom=[{"kind": "Secret", "name": "tenant-credential-x"}]), as_=FLUX_SA), "agent-platform-organization-helmreleases")
    case("an editor writes no valuesFrom", create(helmrelease("a", ORG, valuesFrom=[{"kind": "Secret", "name": "tenant-credential-x"}]), as_=BOB, groups=()),
         "agent-platform-organization-helmreleases")
    for field, value in (("postRenderers", [{"kustomize": {"patches": [{"patch": "[]", "target": {"kind": "Deployment"}}]}}]),
                         ("kubeConfig", {"secretRef": {"name": "kubeconfig"}}),
                         ("targetNamespace", "kagent"), ("storageNamespace", "kagent"),
                         ("chart", {"spec": {"chart": "agent", "sourceRef": {"kind": "HelmRepository", "name": "x"}}})):
        extra = {field: value, **({"chartRef": None} if field == "chart" else {})}
        case(f"a HelmRelease sets no {field}", create(helmrelease("a", ORG, **extra), as_=PERSON), "agent-platform-organization-helmreleases")
    case("a HelmRelease installs from no other OCIRepository", create(helmrelease("a", ORG, chartRef={"kind": "OCIRepository", "name": "evil"}), as_=PERSON),
         "agent-platform-organization-helmreleases")
    case("a HelmRelease installs from no OCIRepository of another namespace",
         create(helmrelease("a", ORG, chartRef={"kind": "OCIRepository", "name": "agent", "namespace": "kagent"}), as_=PERSON),
         "agent-platform-organization-helmreleases")
    case("a HelmRelease installs from its namespace's OCIRepository named with the namespace",
         create(helmrelease("a", ORG, chartRef={"kind": "OCIRepository", "name": "agent", "namespace": ORG}), as_=PERSON))
    case("a HelmRelease runs as no other ServiceAccount", create(helmrelease("a", ORG, serviceAccountName="kagent-controller"), as_=PERSON),
         "agent-platform-organization-helmreleases")
    case("a HelmRelease runs as the organization's Helm ServiceAccount, never the controller's default",
         create(helmrelease("a", ORG, serviceAccountName=None), as_=PERSON), "agent-platform-organization-helmreleases")
    case("a HelmRelease written before the lock takes an annotation", patch("helmrelease", "legacy", ORG,
         {"metadata": {"annotations": {"reconcile.fluxcd.io/requestedAt": "now"}}}, as_=PERSON))
    case("a HelmRelease written before the lock changes no spec", patch("helmrelease", "legacy", ORG, {"spec": {"values": {"x": 1}}}, as_=PERSON),
         "agent-platform-organization-helmreleases")
    case("the operator is not held", create(helmrelease("a", ORG, valuesFrom=[{"kind": "Secret", "name": "x"}]), as_=OP))
    case("a HelmRelease outside organization namespaces is not held", create(helmrelease("a", PLAIN, valuesFrom=[{"kind": "Secret", "name": "x"}]), as_=PERSON))


def prove_delete_intent():
    case("an organization without prune: enabled is not deleted", delete("agentplatformorganization", "payments", PARENT, as_=PERSON),
         "agent-platform-organization-delete-intent")
    case("an organization with prune: disabled is not deleted", delete("agentplatformorganization", "kept", PARENT, as_=PERSON),
         "agent-platform-organization-delete-intent")
    case("an organization with prune: enabled is deleted", delete("agentplatformorganization", "retired", PARENT, as_=PERSON))
    case("an organization is deleted while its parent terminates", delete("agentplatformorganization", "gone", GONE, as_=PERSON))


def prove_egress_floor():
    for origin in ("https://muster.muster.svc", "https://kagent-controller.kagent.svc.cluster.local:8083", "https://kubernetes.default",
                   "http://localhost:8080", "https://app.localhost", "https://metadata.google.internal", "https://muster",
                   f"https://{API_HOST}:6443", "https://*.acme.example.io", "https://*.svc.cluster.local",
                   "tls://postgres.kagent.svc:5432"):
        case(f"an EgressPolicy names no {origin}", create(egress("e", ORG, "https://api.github.com", origin), as_=PERSON),
             "agent-platform-organization-egress-floor")
    case("an EgressPolicy names public origins", create(egress("e", ORG, "https://api.github.com", "https://*.github.com",
                                                                f"https://console.{API_HOST}", "tls://db.example.com:5432"), as_=PERSON))
    case("an EgressPolicy outside organization namespaces is not held", create(egress("e", PLAIN, "https://kubernetes.default"), as_=PERSON))


def prove_copies():
    case("a cluster-admin changes no copy's spec", patch("ocirepository", "agent", ORG, {"spec": {"url": "oci://evil.example.io/agent"}}, as_=PERSON),
         "agent-platform-organization-copies")
    case("a cluster-admin removes no copy's label", patch("ocirepository", "agent", ORG, {"metadata": {"labels": {OBJECT_LABEL: None}}}, as_=PERSON),
         "agent-platform-organization-copies")
    case("a cluster-admin deletes no copy", delete("ocirepository", "agent", ORG, as_=PERSON), "agent-platform-organization-copies")
    case("a cluster-admin creates no object with the copies' label", create(ocirepository(ORG2), as_=PERSON), "agent-platform-organization-copies")
    case("a cluster-admin creates the same object without the label", create(ocirepository(ORG2, labelled=False), as_=PERSON))
    case("source-controller adds its finalizer to a copy", patch("ocirepository", "agent", ORG, {"metadata": {"finalizers": ["finalizers.fluxcd.io"]}},
                                                                  as_=SOURCE_CONTROLLER))
    case("the operator changes its copy", patch("ocirepository", "agent", ORG, {"spec": {"interval": "10m"}}, as_=OP))
    case("a cluster-admin changes no curated copy (a field beside metadata)",
         patch("serviceaccount", "workers", ORG, {"automountServiceAccountToken": True}, as_=PERSON), "agent-platform-organization-copies")
    case("a cluster-admin changes no curated copy's source annotation",
         patch("serviceaccount", "workers", ORG, {"metadata": {"annotations": {"agent-platform.giantswarm.io/copied-from": "kagent/other"}}}, as_=PERSON),
         "agent-platform-organization-copies")
    case("a cluster-admin adds another annotation to a curated copy",
         patch("serviceaccount", "workers", ORG, {"metadata": {"annotations": {"example.com/note": "x"}}}, as_=PERSON))
    case("the namespace controller deletes a copy", delete("ocirepository", "agent", ORG, as_=NS_CONTROLLER))


def prove_sandbox():
    """No Kyverno runs on this API server, so the PolicyException below
    changes nothing here: what it proves is that the refusals come from the
    ValidatingAdmissionPolicies (named in each refusal), which check_render
    shows are admissionregistration.k8s.io objects. A Kyverno
    PolicyException excepts from Kyverno's own policies only; it has no
    field that names a ValidatingAdmissionPolicy."""
    must(create({"apiVersion": "kyverno.io/v2", "kind": "PolicyException", "metadata": {"name": "agents-everything", "namespace": "kagent"},
                 "spec": {"exceptions": [{"policyName": "*", "ruleNames": ["*"]}],
                          "match": {"any": [{"resources": {"namespaces": ["agents-*"], "kinds": ["*"]}}]}}}, dry=False),
         "create a Kyverno PolicyException for agents-*")
    case("the operator writes the organization's copy of the platform WorkerPool", create(workerpool(ORG), as_=OP))
    for what, pool in (("another runtime class", workerpool(ORG, sandbox_class="microvm")),
                       ("another worker template", {**workerpool(ORG), "spec": {**workerpool(ORG)["spec"], "template": {"serviceAccountName": "kagent-controller"}}})):
        case(f"with a PolicyException for agents-*, a WorkerPool with {what} is refused", create(pool, as_=OP),
             "agent-platform-organization-workerpool-copy")
    case("with a PolicyException for agents-*, only the operator writes a WorkerPool", create(workerpool(ORG), as_=PERSON),
         "agent-platform-organization-workers")
    deployment = {"apiVersion": "apps/v1", "kind": "Deployment", "metadata": {"name": f"{POOL}-x", "namespace": ORG, "labels": {"ate.dev/worker-pool": POOL}},
                  "spec": {"selector": {"matchLabels": {"app": "w"}}, "template": {"metadata": {"labels": {"app": "w"}},
                           "spec": {"containers": [{"name": "c", "image": "registry.k8s.io/pause:3.10"}]}}}}
    case("with a PolicyException for agents-*, only Substrate's controllers write a worker Deployment", create(deployment, as_=PERSON),
         "agent-platform-organization-workers")


def main():
    global API
    meta, chart = sys.argv[1:3]
    roles, policies = check_render(chart)
    with tempfile.TemporaryDirectory() as tmp:
        all_crds = crds(meta, tmp)
        API = APIServer()
        try:
            must(kubectl(["apply", "--server-side", "-f", "-"], all_crds), "install the CRDs")
            must(kubectl(["wait", "--for=condition=Established", "crd", "--all", "--timeout=60s"]), "wait for the CRDs")
            fixtures()
            install(roles, policies)
            for prove in (prove_roles, prove_operator, prove_labels, prove_admins_binding, prove_subjects, prove_helmreleases,
                          prove_delete_intent, prove_egress_floor, prove_copies, prove_sandbox):
                prove()
        finally:
            API.stop()
    failed = [n for ok, n in results if not ok]
    print(f"{len(results) - len(failed)}/{len(results)} cases as expected")
    if failed:
        fail("; ".join(failed))


if __name__ == "__main__":
    main()
