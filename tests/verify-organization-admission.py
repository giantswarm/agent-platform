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
  the Flux namespace only when labelled with the organization's UID; a
  Kustomization there runs as the ServiceAccount of its own name and applies
  into the namespace of its own name, and the operator binds a Flux-namespace
  ServiceAccount only in the namespace of its name;
- agent-platform:organization-flux, bound there, lets the organization's Flux
  ServiceAccount write the organization's kinds in its namespace only, and
  bind no role but user, editor and admin;
- only the operator sets, changes or removes the organization labels;
- organization-admins is not deleted or emptied while its namespace lives;
- a RoleBinding there binds no system: identity and no ServiceAccount of
  another namespace;
- the operator binds each credential provider's ServiceAccount alone to its
  own Secret reader, by a RoleBinding named after it, in organization
  namespaces only, and a Secret reader to its provider only; a cluster-admin
  binds no provider there (giantswarm/agent-platform#1269);
- the operator's mirror of a customer admin Group is admitted; a person's
  mirror labelled as the operator's is refused, an organization admin binds
  no organizations-admin (giantswarm/agent-platform#1357);
- an organization admin's Secret delete is answered with a Status, never the
  Secret: a delete the API server would answer with it (Orphan, Foreground,
  orphanDependents, a finalizer, also one the admin's own update added) is
  refused; the Flux prune, the namespace controller and the garbage
  collector stay admitted (giantswarm/agent-platform#1355);
- the HelmRelease field lock, also for the organization's Flux ServiceAccount;
- an organization is deleted only with prune: enabled or while its parent
  terminates, and one applied from git only once its delete pull request
  merged (Orphaned, reason DeleteMerged);
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
FLUX_SOURCE = "management-clusters-fleet"  # the installation's GitRepository
SOPS_KEY = "sops-keys"  # the installation's SOPS key Secret, the chart's default
ORG_PATHS = "./agent-platform/organizations/"  # agentPlatformOrganizations.fluxPathPrefix, the chart's default
API_HOST = "api.acme.example.io"
POOL = "kagent-default"
OP = f"system:serviceaccount:{RELEASE_NS}:agent-platform-operator"
FLUX_SA = f"system:serviceaccount:{FLUX_NS}:agents-payments-x7k2q"  # named after ORG, as the operator names it
SOURCE_CONTROLLER = f"system:serviceaccount:{FLUX_NS}:source-controller"
NS_CONTROLLER = "system:serviceaccount:kube-system:namespace-controller"
PERSON = "jose"  # with system:masters: a cluster-admin
ALICE = "alice"  # organizations-admin by ClusterRoleBinding (platform staff)
CAROL = "carol"  # organizations-admin by RoleBinding in org-acme (a customer platform team)
BOB = "bob"  # agent-platform:editor in the organization namespace

PARENT = "org-acme"
ORG = "agents-payments-x7k2q"
ORG2 = "agents-billing-b4n9d"
ORG3 = "agents-treasury-c5p7e"  # commit mode: its first admins come from git
FLUX_SA3 = f"system:serviceaccount:{FLUX_NS}:{ORG3}"
OLD = "agents-old-aaaaa"  # terminating
GONE = "org-gone"  # a terminating parent
PLAIN = "team-a"
# The credential providers in Substrate's namespace and their Secret readers.
SUBSTRATE_NS = "ate-system"
PROVIDER, READER = "k8s-credential-provider", "k8s-credential-provider-secret-reader"
PLUGIN, PLUGIN_READER = "google-access-token-credential-provider", "google-access-token-credential-provider-secret-reader"
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
      "--set", "llmRouting.enabled=true", "--api-versions", "admissionregistration.k8s.io/v1/MutatingAdmissionPolicy",
      "--set", f"agentPlatformOrganizations.fluxSource.name={FLUX_SOURCE}"]  # last: ON[:-2] drops it
SUBSTRATE = ["--set", "components.substrate.enabled=true", "--set", "components.substrate-crds.enabled=true"]


def docs(r):
    if r.returncode != 0:
        fail(f"the render failed:\n{r.stderr}")
    return [d for d in yaml.safe_load_all(r.stdout) if d]


def organization_objects(objs):
    return [d for d in objs if d["metadata"]["name"].startswith(("agent-platform-organization", "agent-platform-organizations-admin"))
            or d["metadata"]["name"] in ("agent-platform:organizations-admin", "agent-platform:organization-flux")]


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
             ("agent-platform:user", "agent-platform:editor", "agent-platform:admin", "agent-platform:organizations-admin",
              "agent-platform:organization-flux")]
    if "agent-platform:organization-flux" not in {d["metadata"]["name"] for d in roles}:
        fail("on with fluxNamespace, agent-platform:organization-flux does not render")
    no_flux = docs(render(chart, *ON[:2], *ON[4:]))
    if any(d["metadata"]["name"] == "agent-platform:organization-flux" for d in no_flux):
        fail("agent-platform:organization-flux renders without fluxNamespace")
    print("ok:  agent-platform:organization-flux renders with fluxNamespace only")
    r = render(chart, *ON[:-2])
    if r.returncode == 0 or "agentPlatformOrganizations.fluxSource.name is required with fluxNamespace" not in r.stderr:
        fail(f"fluxNamespace without fluxSource.name renders, or fails for another reason:\n{r.stderr}")
    print("ok:  fluxNamespace without fluxSource.name: the render fails naming the key")
    for prefix in ("", "./agent-platform/organizations", "agent-platform/organizations/", "./agent-platform/../organizations/", "./agent-platform//organizations/"):
        r = render(chart, *ON, "--set-string", f"agentPlatformOrganizations.fluxPathPrefix={prefix}")
        if r.returncode == 0 or "agentPlatformOrganizations.fluxPathPrefix" not in r.stderr:
            fail(f"fluxPathPrefix {prefix!r} renders, or fails for another reason:\n{r.stderr}")
    print("ok:  a fluxPathPrefix that is not ./<segments>/ fails the render naming the key")
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


def replace(obj, as_=None, groups=("system:masters",)):
    """An update without a resourceVersion: what a writer who may not read the object sends."""
    return kubectl(["replace", "-f", "-", "--dry-run=server"], yaml.safe_dump(obj), as_, groups)


def delete(kind, name, ns, as_=None, groups=("system:masters",), cascade=None):
    args = ["delete", kind, name, "--dry-run=server", "--wait=false"] + (["-n", ns] if ns else [])
    args += [f"--cascade={cascade}"] if cascade else []
    return kubectl(args, None, as_, groups)


def can_i(verb, resource, ns, as_):
    """The authorizer's answer (a SubjectAccessReview): "yes" or "no"."""
    rc, out = kubectl(["auth", "can-i", verb, resource, "-n", ns], None, as_, ())
    if rc not in (0, 1):
        fail(f"kubectl auth can-i {verb} {resource} as {as_}:\n{out}")
    return "yes" if rc == 0 else "no"


def sar(user, verb, group, resource, ns):
    """The authorizer's answer for a kind the API server need not serve."""
    review = {"apiVersion": "authorization.k8s.io/v1", "kind": "SubjectAccessReview",
              "spec": {"user": user, "resourceAttributes": {"verb": verb, "group": group, "resource": resource, "namespace": ns}}}
    out = must(kubectl(["create", "-f", "-", "-o", "json"], yaml.safe_dump(review)), f"SubjectAccessReview {verb} {resource} as {user}")
    return "yes" if json.loads(out)["status"].get("allowed") else "no"


def case(name, rc_out, by=None, msg=None):
    """by=None: admitted. by=<policy>: refused by that ValidatingAdmissionPolicy,
    with msg in its message when given. by='RBAC:<text>': refused by the
    authorizer with that text; by='API:<text>': refused by the API server
    itself (a registry check) with that text."""
    rc, out = rc_out
    last = out.strip().splitlines()[-1] if out.strip() else ""
    if by is None:
        ok = rc == 0
    elif by.startswith(("RBAC:", "API:")):
        ok = rc != 0 and by.split(":", 1)[1] in out
    else:
        # The policy's own message, not an evaluation error: an expression
        # that fails to evaluate refuses too (failurePolicy Fail), naming
        # the policy, and would read as the refusal under test.
        ok = (rc != 0 and f"ValidatingAdmissionPolicy '{by}'" in out and "denied request" in out
              and "resulted in error" not in out and "failed expression" not in out and (msg is None or msg in out))
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


# The organizations the operator judges applied from git, by the status it
# writes: name -> (Orphaned status, reason).
ORPHANED = {"in-git": ("False", "InInventory"), "lost": ("True", "MissingFromGit"), "merged": ("True", "DeleteMerged")}


def set_orphaned(name, status, reason):
    condition = {"type": "Orphaned", "status": status, "reason": reason, "message": "test",
                 "lastTransitionTime": "2026-01-01T00:00:00Z"}
    args = ["patch", "agentplatformorganization", name, "-n", PARENT, "--subresource=status", "--type=merge",
            "-p", json.dumps({"status": {"conditions": [condition]}})]
    must(kubectl(args), f"set Orphaned {status}/{reason} on {PARENT}/{name}")


def helmrelease(name, namespace, **spec_extra):
    spec = {"interval": "10m", "chartRef": {"kind": "OCIRepository", "name": "agent"}, "serviceAccountName": "kagent-flux",
            "values": {"agent": {"name": name}}}
    spec.update(spec_extra)
    return {"apiVersion": "helm.toolkit.fluxcd.io/v2", "kind": "HelmRelease", "metadata": {"name": name, "namespace": namespace},
            "spec": {k: v for k, v in spec.items() if v is not None}}


def kustomization(name, namespace, uid=True):
    """An organization's Kustomization as the operator writes it: named after
    the organization namespace (agents-<organization>-*), labelled with the
    organization and its parent, running as the Flux ServiceAccount of that
    name, applying the organization's path into that namespace."""
    organization = name.split("-")[1] if name.startswith("agents-") else name
    meta = {"name": name, "namespace": namespace,
            "labels": {"agent-platform.giantswarm.io/parent": PARENT, "agent-platform.giantswarm.io/organization": organization}}
    if uid:
        meta["labels"]["agent-platform.giantswarm.io/organization-uid"] = UID
    return {"apiVersion": "kustomize.toolkit.fluxcd.io/v1", "kind": "Kustomization", "metadata": meta,
            "spec": {"interval": "10m", "prune": True, "path": f"{ORG_PATHS}{PARENT}/{organization}", "serviceAccountName": name, "targetNamespace": name,
                     "sourceRef": {"kind": "GitRepository", "name": FLUX_SOURCE, "namespace": namespace},
                     "decryption": {"provider": "sops", "secretRef": {"name": SOPS_KEY}}}}


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
                      (ORG, LABELS), (ORG2, {**LABELS, "agent-platform.giantswarm.io/organization": "billing"}), (OLD, LABELS),
                      (ORG3, {**LABELS, "agent-platform.giantswarm.io/organization": "treasury"})):
        must(create(ns(n, labels), dry=False), f"create namespace {n}")
    objects = [
        workerpool("kagent", replicas=1),
        apo("payments", PARENT), apo("retired", PARENT, prune="enabled"), apo("kept", PARENT, prune="disabled"), apo("gone", GONE),
        *(apo(n, PARENT, prune="enabled") for n in ORPHANED),
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
        kustomization(ORG, FLUX_NS), kustomization("installation", FLUX_NS, uid=False),
    ]
    for o in objects:
        must(create(o, dry=False), f"create {o['kind']} {o['metadata'].get('namespace', '')}/{o['metadata']['name']}")
    for n, (status, reason) in ORPHANED.items():
        set_orphaned(n, status, reason)
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
    case("the operator creates its labelled Kustomization in the Flux namespace", create(kustomization(ORG2, FLUX_NS), as_=OP))
    case("the operator creates no unlabelled Kustomization", create(kustomization(ORG2, FLUX_NS, uid=False), as_=OP),
         "agent-platform-organization-operator-objects", "only Kustomizations labelled")
    pinned = "agent-platform-organization-operator-objects"
    as_sa = "runs as the organization Flux ServiceAccount, spec.serviceAccountName " + ORG2
    into = "applies into the organization namespace, spec.targetNamespace " + ORG2

    def changed(**spec):
        k = kustomization(ORG2, FLUX_NS)
        for key, value in spec.items():
            if value is None:
                del k["spec"][key]
            else:
                k["spec"][key] = value
        return k
    case("the operator creates no Kustomization that Flux applies with its own identity (no serviceAccountName)",
         create(changed(serviceAccountName=None), as_=OP), pinned, as_sa + "; refused: " + FLUX_NS + "/" + ORG2 + " with none")
    case("the operator creates no Kustomization that runs as a Flux controller",
         create(changed(serviceAccountName="kustomize-controller"), as_=OP), pinned, as_sa)
    case("the operator creates no Kustomization that runs as another organization's Flux ServiceAccount",
         create(changed(serviceAccountName=ORG), as_=OP), pinned, as_sa + "; refused: " + FLUX_NS + "/" + ORG2 + " with " + ORG)
    case("the operator creates no Kustomization that applies into another organization's namespace",
         create(changed(targetNamespace=ORG), as_=OP), pinned, into + "; refused: " + FLUX_NS + "/" + ORG2 + " with " + ORG)
    case("the operator creates no Kustomization without a targetNamespace (its files' own namespaces)",
         create(changed(targetNamespace=None), as_=OP), pinned, into + "; refused: " + FLUX_NS + "/" + ORG2 + " with none")
    other = kustomization("kube-system", FLUX_NS)
    case("the operator creates no Kustomization outside the agents-* names, its ServiceAccount and target of the same name",
         create(other, as_=OP), pinned, "is an organization Kustomization, named agents-<organization>-*")
    case("the operator moves no Kustomization into another organization's namespace",
         patch("kustomization", ORG, FLUX_NS, {"spec": {"targetNamespace": ORG2}}, as_=OP), pinned, "spec.targetNamespace " + ORG + "; refused")
    for field, value in (("kubeConfig", {"secretRef": {"name": "other-cluster"}}),
                         ("postBuild", {"substituteFrom": [{"kind": "Secret", "name": SOPS_KEY}]}),
                         ("patches", [{"patch": "[]", "target": {"kind": "Secret"}}]), ("components", ["../../other"])):
        case(f"the operator creates no Kustomization with {field}", create(changed(**{field: value}), as_=OP), pinned,
             "sets none of spec.kubeConfig, spec.postBuild.substituteFrom, spec.patches and spec.components")
    source = f"applies from the installation source GitRepository {FLUX_NS}/{FLUX_SOURCE}"
    for what, ref in (("another GitRepository", {"kind": "GitRepository", "name": "customer-fleet"}),
                      ("a source of another namespace", {"kind": "GitRepository", "name": FLUX_SOURCE, "namespace": "org-acme"}),
                      ("a Bucket", {"kind": "Bucket", "name": FLUX_SOURCE})):
        case(f"the operator creates no Kustomization that applies from {what}", create(changed(sourceRef=ref), as_=OP), pinned, source)
    key = f"decrypts with the installation key {SOPS_KEY} only"
    case("the operator creates no Kustomization that decrypts with another Secret",
         create(changed(decryption={"provider": "sops", "secretRef": {"name": "other-team-age"}}), as_=OP), pinned, key)
    case("the operator creates no Kustomization that decrypts as a ServiceAccount",
         create(changed(decryption={"provider": "sops", "secretRef": {"name": SOPS_KEY}, "serviceAccountName": "kustomize-controller"}), as_=OP), pinned, key)
    case("the operator creates a Kustomization that decrypts nothing", create(changed(decryption=None), as_=OP))
    # ORG2 is billing in org-acme: its path is ORG_PATHS + org-acme/billing.
    own = (f"applies its organization path, {ORG_PATHS}<agent-platform.giantswarm.io/parent>/<agent-platform.giantswarm.io/organization>; "
           f"refused: {FLUX_NS}/{ORG2} with ")
    for what, path in (("outside the organizations' paths", "./management-clusters/acme"),
                       ("that steps out with ..", f"{ORG_PATHS}org-acme/billing/../../../management-clusters/acme"),
                       ("of the prefix alone", ORG_PATHS),
                       ("of the organizations' root (.)", ORG_PATHS + "."),
                       ("with an empty segment (//)", ORG_PATHS + "/"),
                       ("of a whole parent", ORG_PATHS + PARENT),
                       ("of another organization", f"{ORG_PATHS}{PARENT}/payments")):
        case(f"the operator creates no Kustomization with a path {what}", create(changed(path=path), as_=OP), pinned, own + path)
    case("the operator creates its Kustomization with its organization's own path", create(changed(path=f"{ORG_PATHS}{PARENT}/billing"), as_=OP))
    case("the operator updates its Kustomization within the pins",
         patch("kustomization", ORG, FLUX_NS, {"spec": {"path": "./agent-platform/organizations/org-acme/payments"}}, as_=OP))
    case("the operator creates no Kustomization outside the Flux namespace", create(kustomization(ORG2, PLAIN), as_=OP),
         "agent-platform-organization-operator-objects")
    case("the operator deletes its labelled Kustomization", delete("kustomization", ORG, FLUX_NS, as_=OP))
    case("the operator deletes no Kustomization it does not label", delete("kustomization", "installation", FLUX_NS, as_=OP),
         "agent-platform-organization-operator-objects")
    case("the operator removes no UID label from a Kustomization",
         patch("kustomization", ORG, FLUX_NS, {"metadata": {"labels": {"agent-platform.giantswarm.io/organization-uid": None}}}, as_=OP),
         "agent-platform-organization-operator-objects")
    case("the operator creates its labelled ServiceAccount in the Flux namespace", create(sa(ORG, FLUX_NS), as_=OP))
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
         create(rb("x", ORG, subjects=[{"kind": "ServiceAccount", "name": ORG, "namespace": FLUX_NS}]), as_=PERSON),
         "agent-platform-organization-rolebinding-subjects")
    case("the operator binds the organization's Flux ServiceAccount in its namespace",
         create(rb("organization-flux", ORG, role="agent-platform:organization-flux", subjects=[{"kind": "ServiceAccount", "name": ORG, "namespace": FLUX_NS}]), as_=OP))
    case("the operator binds the organization's Flux ServiceAccount to nothing but agent-platform:organization-flux",
         create(rb("organization-flux", ORG, role="agent-platform:admin", subjects=[{"kind": "ServiceAccount", "name": ORG, "namespace": FLUX_NS}]), as_=OP),
         "agent-platform-organization-operator-objects", "to the ClusterRole agent-platform:organization-flux only; refused: " + ORG + "/organization-flux to ClusterRole agent-platform:admin")
    case("the operator binds no other organization's Flux ServiceAccount",
         create(rb("organization-flux", ORG2, role="agent-platform:organization-flux", subjects=[{"kind": "ServiceAccount", "name": ORG, "namespace": FLUX_NS}]), as_=OP),
         "agent-platform-organization-rolebinding-subjects", f"ServiceAccount {FLUX_NS}/{ORG}")
    # The credential providers' Secret readers (giantswarm/agent-platform#1269):
    # the operator binds each provider's ServiceAccount of Substrate's
    # namespace alone to its own reader, named after it; nobody else binds it.
    for role, provider in ((READER, PROVIDER), (PLUGIN_READER, PLUGIN)):
        subject = [{"kind": "ServiceAccount", "name": provider, "namespace": SUBSTRATE_NS}]
        case(f"the operator binds {provider} to {role} in an organization namespace",
             create(rb(role, ORG, role=role, subjects=subject), as_=OP))
        case(f"the operator binds {provider} in no namespace but an organization's",
             create(rb(role, PLAIN, role=role, subjects=subject), as_=OP),
             "agent-platform-organization-operator-objects", "RoleBindings only in organization namespaces")
        case(f"a cluster-admin binds no {provider} in an organization namespace",
             create(rb(role, ORG, role=role, subjects=subject), as_=PERSON),
             "agent-platform-organization-rolebinding-subjects", f"ServiceAccount {SUBSTRATE_NS}/{provider}")
    subject = [{"kind": "ServiceAccount", "name": PROVIDER, "namespace": SUBSTRATE_NS}]
    case("the operator binds the credential provider to nothing but its Secret reader",
         create(rb(READER, ORG, role="agent-platform:admin", subjects=subject), as_=OP),
         "agent-platform-organization-operator-objects", f"alone to its own Secret reader, by a RoleBinding named after it; refused: {ORG}/{READER} to ClusterRole agent-platform:admin")
    case("the operator binds the credential provider to the plugin's Secret reader not",
         create(rb(PLUGIN_READER, ORG, role=PLUGIN_READER, subjects=subject), as_=OP),
         "agent-platform-organization-operator-objects", "alone to its own Secret reader")
    case("the operator binds the credential provider's Secret reader by no other name",
         create(rb("reader", ORG, role=READER, subjects=subject), as_=OP),
         "agent-platform-organization-operator-objects", f"refused: {ORG}/reader to ClusterRole {READER}")
    case("the operator binds the credential provider with no other subject",
         create(rb(READER, ORG, role=READER, subjects=[*subject, {"apiGroup": "rbac.authorization.k8s.io", "kind": "Group", "name": "acme-devs"}]), as_=OP),
         "agent-platform-organization-operator-objects", "alone to its own Secret reader")
    case("the operator binds a Secret reader to no other subject",
         create(rb(READER, ORG, role=READER), as_=OP),
         "agent-platform-organization-operator-objects", f"to the ServiceAccount of its credential provider only; refused: {ORG}/{READER}")


def prove_flux_role():
    """agent-platform:organization-flux as the operator binds it: the
    organization's Flux ServiceAccount, with no group (what kustomize-controller
    impersonates), applies the organization's kinds into its namespace only."""
    must(create(rb("organization-flux", ORG, role="agent-platform:organization-flux",
                   subjects=[{"kind": "ServiceAccount", "name": ORG, "namespace": FLUX_NS}]), as_=OP, dry=False),
         "bind the organization's Flux ServiceAccount")
    # kubectl auth can-i resolves only served kinds; of kagent's this API
    # server serves EgressPolicy, the others are asked by a
    # SubjectAccessReview.
    want = {(v, r, n): "yes" if n == ORG else "no" for v, r in (("create", "secrets"), ("create", "helmreleases.helm.toolkit.fluxcd.io"),
            ("create", "egresspolicies.api.kagent.dev"), ("create", "rolebindings")) for n in (ORG, ORG2, FLUX_NS, "kagent")}
    for v, r in (("create", "kustomizations.kustomize.toolkit.fluxcd.io"), ("create", "ocirepositories.source.toolkit.fluxcd.io"),
                 ("create", "workerpools.ate.dev"), ("create", "namespaces")):
        want[(v, r, ORG)] = "no"
    wrong = {f"{v} {r} in {n}": a for (v, r, n), w in want.items() if (a := can_i(v, r, n, FLUX_SA)) != w}
    for r in ("agents", "agenttemplates", "modelconfigs", "remotemcpservers"):
        for n in (ORG, ORG2):
            if (a := sar(FLUX_SA, "create", "api.kagent.dev", r, n)) != ("yes" if n == ORG else "no"):
                wrong[f"create {r}.api.kagent.dev in {n}"] = a
    case("the organization's Flux ServiceAccount writes the organization's kinds in its namespace and nowhere else", (1 if wrong else 0, json.dumps(wrong)))
    case("a merged Secret lands in the organization namespace", create({"apiVersion": "v1", "kind": "Secret", "metadata": {"name": "tenant-credential-a", "namespace": ORG}},
         as_=FLUX_SA, groups=()))
    case("a merged Secret is refused in another organization's namespace",
         create({"apiVersion": "v1", "kind": "Secret", "metadata": {"name": "tenant-credential-a", "namespace": ORG2}}, as_=FLUX_SA, groups=()),
         "RBAC:cannot create resource \"secrets\"")
    for role in ("agent-platform:user", "agent-platform:editor", "agent-platform:admin"):
        case(f"a merged RoleBinding binds {role}", create(rb("members-git", ORG, role=role), as_=FLUX_SA, groups=()))
    # What git applied carries kustomize-controller's labels of the
    # organization's Kustomization; organization-admins (a fixture written by
    # people) carries none.
    took = "agent-platform-organization-flux-writes"
    def applied_by(name, ks):
        b = rb(name, ORG)
        b["metadata"]["labels"] = {"kustomize.toolkit.fluxcd.io/name": ks, "kustomize.toolkit.fluxcd.io/namespace": FLUX_NS}
        must(create(b, as_=PERSON, dry=False), f"create RoleBinding {ORG}/{name}")
    applied_by("members-from-git", ORG)
    applied_by("members-other-git", ORG2)
    more = {"subjects": [{"apiGroup": "rbac.authorization.k8s.io", "kind": "Group", "name": "acme-git"}]}
    case("a file in git takes over no RoleBinding people wrote (organization-admins)",
         patch("rolebinding", "organization-admins", ORG, more, as_=FLUX_SA, groups=()), took,
         f"changes or deletes only a RoleBinding that git applied; refused: UPDATE {ORG}/organization-admins")
    # organization-admins itself is not deleted while its namespace lives
    # (agent-platform-organization-admins-binding); members is another
    # binding people wrote.
    case("prune deletes no RoleBinding people wrote (members)",
         delete("rolebinding", "members", ORG, as_=FLUX_SA, groups=()), took,
         f"changes or deletes only a RoleBinding that git applied; refused: DELETE {ORG}/members")
    case("a file in git changes no RoleBinding another organization's Kustomization applied",
         patch("rolebinding", "members-other-git", ORG, more, as_=FLUX_SA, groups=()), took, f"refused: UPDATE {ORG}/members-other-git")
    case("a file in git changes a RoleBinding its own Kustomization applied",
         patch("rolebinding", "members-from-git", ORG, more, as_=FLUX_SA, groups=()))
    case("prune deletes a RoleBinding its own Kustomization applied",
         delete("rolebinding", "members-from-git", ORG, as_=FLUX_SA, groups=()))
    case("a merged RoleBinding binds no other role", create(rb("members-git", ORG, role="cluster-admin"), as_=FLUX_SA, groups=()),
         "RBAC:attempting to grant RBAC permissions not currently held")
    # RBAC admits a binding of a role whose rules the binder holds, without
    # bind: the policy refuses it.
    for role, kind in (("agent-platform:organization-flux", "ClusterRole"), ("flux-held-subset", "Role")):
        if kind == "Role":
            must(create({"apiVersion": "rbac.authorization.k8s.io/v1", "kind": "Role", "metadata": {"name": role, "namespace": ORG},
                         "rules": [{"apiGroups": [""], "resources": ["secrets"], "verbs": ["get", "list"]}]}, dry=False), "create a Role")
        case(f"a merged RoleBinding binds no {kind} {role}, whose rules it holds", create(rb("members-git", ORG, role=role, kind=kind), as_=FLUX_SA, groups=()),
             "agent-platform-organization-flux-writes", f"refused: {ORG}/members-git to {kind} {role}")
    token = {"apiVersion": "v1", "kind": "Secret", "type": "kubernetes.io/service-account-token",
             "metadata": {"name": "minted", "namespace": ORG, "annotations": {"kubernetes.io/service-account.name": "kagent-flux"}}}
    case("a merged Secret is no ServiceAccount token", create(token, as_=FLUX_SA, groups=()), "agent-platform-organization-flux-writes",
         f"is no ServiceAccount token; refused: {ORG}/minted")
    # An organization admin (acme-devs, bound by organization-admins) replaces
    # a credential Secret by an update it cannot read back; one git applied
    # stays git's, which admission tells from the stored labels.
    def secret(name, labels=None):
        return {"apiVersion": "v1", "kind": "Secret", "type": "Opaque",
                "metadata": {"name": name, "namespace": ORG, **({"labels": labels} if labels else {})},
                "data": {"apiKey": "c3ludGhldGlj"}}
    from_git = {"kustomize.toolkit.fluxcd.io/name": ORG, "kustomize.toolkit.fluxcd.io/namespace": FLUX_NS}
    must(create(secret("tenant-credential-from-git", from_git), dry=False), "create a Secret git applied")
    must(create(secret("tenant-credential-by-people"), dry=False), "create a Secret people wrote")
    admin = ("dana", ("acme-devs",))
    case("an organization admin replaces no Secret git applied",
         replace(secret("tenant-credential-from-git"), as_=admin[0], groups=admin[1]), "agent-platform-organization-git-secrets",
         f"is changed in git; refused: {ORG}/tenant-credential-from-git")
    case("an organization admin replaces a Secret people wrote",
         replace(secret("tenant-credential-by-people"), as_=admin[0], groups=admin[1]))
    case("the organization's Flux ServiceAccount changes a Secret its Kustomization applied",
         replace(secret("tenant-credential-from-git", from_git), as_=FLUX_SA, groups=()))
    # A delete answered with the stored Secret reads it: the admin, who may
    # not get Secrets, deletes only with background propagation and only a
    # Secret no finalizer holds.
    held = secret("tenant-credential-held")
    held["metadata"]["finalizers"] = ["example.com/hold"]
    must(create(held, dry=False), "create a Secret a finalizer holds")
    policy = "agent-platform-organization-secret-deletes"
    for cascade in ("orphan", "foreground"):
        case(f"an organization admin deletes no Secret with {cascade} propagation",
             delete("secret", "tenant-credential-by-people", ORG, as_=admin[0], groups=admin[1], cascade=cascade), policy,
             f"refused: {ORG}/tenant-credential-by-people")
    case("an organization admin deletes no Secret a finalizer holds",
         delete("secret", "tenant-credential-held", ORG, as_=admin[0], groups=admin[1], cascade="background"), policy,
         f"refused: {ORG}/tenant-credential-held")
    case("an organization admin deletes a Secret with background propagation",
         delete("secret", "tenant-credential-by-people", ORG, as_=admin[0], groups=admin[1], cascade="background"))
    case("an organization admin deletes a Secret with no propagation policy (agent-manager's delete)",
         kubectl(["delete", "--raw", f"/api/v1/namespaces/{ORG}/secrets/tenant-credential-by-people?dryRun=All"], None, admin[0], admin[1]))
    case("a requester that may get the Secret deletes it with orphan propagation",
         delete("secret", "tenant-credential-held", ORG, cascade="orphan"))
    # A Flux controller acting as itself (a platform Kustomization) is not an
    # organization's Flux ServiceAccount.
    case("kustomize-controller as itself is not held as an organization's Flux ServiceAccount",
         create(rb("platform-view", ORG, role="view"), as_=f"system:serviceaccount:{FLUX_NS}:kustomize-controller"))


SECRET_DATA = "c3ludGhldGlj"  # the apiKey of every credential Secret below
GC = "system:serviceaccount:kube-system:generic-garbage-collector"
ADMIN = ("dana", ("acme-devs",))  # agent-platform:admin of ORG by organization-admins: create, update, delete on Secrets, no get


def credential(name, finalizers=None, owner=None):
    meta = {"name": name, "namespace": ORG}
    if finalizers:
        meta["finalizers"] = finalizers
    if owner:
        meta["ownerReferences"] = [owner]
    return {"apiVersion": "v1", "kind": "Secret", "type": "Opaque", "metadata": meta, "data": {"apiKey": SECRET_DATA}}


def delete_secret(name, as_, groups=(), dry=True, query="", **options):
    """A DELETE of a Secret of ORG as a client sends it: DeleteOptions in the
    body (the API server reads the body, not the query, when there is one),
    dryRun=All unless dry is False. options=None sends no body."""
    url = f"/api/v1/namespaces/{ORG}/secrets/{name}{query}"
    if options.pop("no_body", False):
        return kubectl(["delete", "--raw", url + ("&" if "?" in url else "?") + "dryRun=All" if dry else url], None, as_, groups)
    body = {"kind": "DeleteOptions", "apiVersion": "v1", **options, **({"dryRun": ["All"]} if dry else {})}
    return kubectl(["delete", "--raw", url, "-f", "-"], json.dumps(body), as_, groups)


def status_only(rc_out):
    """An admitted delete must be answered with a Status, never with the
    Secret: a requester without get would read it. The case fails on an
    answer that carries the Secret or its data."""
    rc, out = rc_out
    if rc != 0:
        return rc, out
    try:
        answer = json.loads(out)
    except ValueError:
        return 1, f"answered with no JSON: {out}"
    if answer.get("kind") != "Status" or SECRET_DATA in out:
        return 1, f"answered with the {answer.get('kind')}: {out}"
    return 0, f"answered with a Status: {json.dumps(answer.get('details'))}"


def prove_secret_deletes():
    """giantswarm/agent-platform#1355: an organization admin holds create,
    update and delete on Secrets and no get. A DELETE the API server answers
    with the stored object reads the Secret, so
    agent-platform-organization-secret-deletes admits it only for a requester
    that may get it; every other delete must be answered with a Status.

    Every way a Secret DELETE is answered with the object, in k8s.io/apiserver
    v0.36.3 (Kubernetes 1.36) pkg/registry/generic/registry/store.go:
    Store.Delete (1139) takes the update path (1182-1183) when the Secret has
    a finalizer (1173) or the options change its finalizers
    (deletionFinalizersForGarbageCollection, 984-1013: orphanDependents true
    or propagationPolicy Orphan adds orphan, 906-916; Foreground adds
    foregroundDeletion, 952-958), and returns the stored object, data
    included, when finalizers stay (1111-1112, 1196-1197), also on a dry run
    (1105, 1208-1209). Admission runs on that stored object (1063). Graceful
    deletion never applies to a Secret: rest.BeforeDelete returns not
    graceful for a strategy without CheckGracefulDelete
    (pkg/registry/rest/delete.go:101-106), and the Secret strategy has none
    (k8s.io/kubernetes v1.36.3 pkg/registry/core/secret/strategy.go), so
    gracePeriodSeconds changes nothing. Any other delete goes to storage (1215)
    and finalizeDelete (1394-1418) answers with a Status, as the Secret store
    sets no ReturnDeletedObject (pkg/registry/core/secret/storage/storage.go:37-49).
    Preconditions are checked before any write and refuse with a Conflict
    (pkg/registry/rest/delete.go:84-91)."""
    policy = "agent-platform-organization-secret-deletes"
    for name in ("tenant-credential-plain", "tenant-credential-finalized"):
        must(create(credential(name), dry=False), f"create the Secret {name}")
    must(create(credential("tenant-credential-pinned", finalizers=["example.com/hold"]), dry=False), "create a Secret a finalizer holds")
    refused = f"refused: {ORG}/tenant-credential-plain"
    # The legacy option: orphanDependents true is Orphan (store.go:906-909).
    case("an organization admin deletes no Secret with orphanDependents: true",
         delete_secret("tenant-credential-plain", *ADMIN, orphanDependents=True), policy, refused)
    case("an organization admin deletes no Secret with ?orphanDependents=true and no body",
         delete_secret("tenant-credential-plain", *ADMIN, query="?orphanDependents=true", no_body=True), policy, refused)
    case("an organization admin deletes no Secret with Orphan propagation in the DeleteOptions body",
         delete_secret("tenant-credential-plain", *ADMIN, propagationPolicy="Orphan"), policy, refused)
    # What is admitted is answered with a Status, dry run or not.
    for what, options in (("no DeleteOptions body (dry run in the query)", {"no_body": True}),
                          ("DeleteOptions without a propagation policy (controller-runtime's)", {}),
                          ("Background propagation", {"propagationPolicy": "Background"}),
                          ("orphanDependents: false", {"orphanDependents": False}),
                          ("gracePeriodSeconds: 30 (a Secret has no graceful deletion)", {"gracePeriodSeconds": 30})):
        case(f"an organization admin's dry-run delete with {what} is answered with a Status, not the Secret",
             status_only(delete_secret("tenant-credential-plain", *ADMIN, **options)))
    for what, options in (("no DeleteOptions body", {"no_body": True}), ("Background propagation", {"propagationPolicy": "Background"})):
        name = "tenant-credential-gone-" + ("raw" if options.get("no_body") else "background")
        must(create(credential(name), dry=False), f"create the Secret {name}")
        case(f"an organization admin's delete with {what} deletes and is answered with a Status, not the Secret",
             status_only(delete_secret(name, *ADMIN, dry=False, **options)))
    case("a delete with a precondition that does not hold is refused with a Conflict, not the Secret",
         delete_secret("tenant-credential-plain", *ADMIN, preconditions={"resourceVersion": "1"}),
         "API:the ResourceVersion in the precondition (1) does not match")
    case("an organization admin deletes no Secret ignoring store read errors",
         delete_secret("tenant-credential-plain", *ADMIN, ignoreStoreReadErrorWithClusterBreakingPotential=True),
         "RBAC:not permitted to do \"unsafe-delete-ignore-read-errors\"")
    case("an organization admin deletes no collection of Secrets (its answer lists them)",
         kubectl(["delete", "--raw", f"/api/v1/namespaces/{ORG}/secrets?dryRun=All"], None, *ADMIN),
         "RBAC:cannot deletecollection resource \"secrets\"")
    # The admin replaces a Secret it wrote with a finalizer (update is its
    # right), then deletes it: the finalizer keeps it, so the delete would be
    # answered with it. The replacement holds only what the admin sent.
    must(kubectl(["replace", "-f", "-"], yaml.safe_dump(credential("tenant-credential-finalized", finalizers=["example.com/mine"])), *ADMIN),
         "an organization admin adds a finalizer to a Secret by an update")
    case("an organization admin deletes no Secret it added a finalizer to by an update",
         delete_secret("tenant-credential-finalized", *ADMIN), policy, f"refused: {ORG}/tenant-credential-finalized")
    # A Secret already being deleted keeps its finalizer: a second delete
    # takes the update path again and would be answered with it.
    must(kubectl(["delete", "secret", "tenant-credential-pinned", "-n", ORG, "--wait=false"]), "start the deletion of the Secret a finalizer holds")
    case("an organization admin deletes no Secret that is being deleted, a finalizer holding it",
         delete_secret("tenant-credential-pinned", *ADMIN, propagationPolicy="Background"), policy, f"refused: {ORG}/tenant-credential-pinned")

    # Who deletes Secrets of an organization namespace and stays admitted.
    # kustomize-controller v1.9.5 (Flux v2.9.5) prunes with Background
    # propagation (internal/controller/kustomization_controller.go:1407-1416)
    # through fluxcd/pkg/ssa v0.76.2, which gets the object first and deletes
    # it with that policy (manager_delete.go:68, 91): as the organization's
    # Flux ServiceAccount, which may get Secrets.
    for name, finalizers in (("tenant-credential-pruned", None), ("tenant-credential-pruned-held", ["example.com/hold"])):
        from_git = credential(name, finalizers=finalizers)
        from_git["metadata"]["labels"] = {"kustomize.toolkit.fluxcd.io/name": ORG, "kustomize.toolkit.fluxcd.io/namespace": FLUX_NS}
        must(create(from_git, dry=False), f"create the Secret {name} git applied")
    case("the organization's Flux ServiceAccount prunes a Secret with Background propagation",
         delete_secret("tenant-credential-pruned", FLUX_SA, propagationPolicy="Background"))
    case("the organization's Flux ServiceAccount, which may get Secrets, prunes a Secret a finalizer holds",
         delete_secret("tenant-credential-pruned-held", FLUX_SA, propagationPolicy="Background"))
    # The namespace controller empties a terminating namespace with a
    # Background DeleteCollection, then item by item
    # (k8s.io/kubernetes v1.36.3 pkg/controller/namespace/deletion/namespaced_resources_deleter.go:325-326, 392-393).
    collection = json.dumps({"kind": "DeleteOptions", "apiVersion": "v1", "propagationPolicy": "Background", "dryRun": ["All"]})
    must(create({**credential("tenant-credential-a"), "metadata": {"name": "tenant-credential-a", "namespace": ORG2}}, dry=False),
         f"create a Secret in {ORG2}, which no finalizer holds")
    case("the namespace controller deletes the Secrets of an organization namespace none of which a finalizer holds (DeleteCollection, Background)",
         kubectl(["delete", "--raw", f"/api/v1/namespaces/{ORG2}/secrets", "-f", "-"], collection, NS_CONTROLLER, ()))
    # A defect, pinned until giantswarm/agent-platform#1362 fixes it: a
    # collection request has no name, so the policy's request.name fails to
    # evaluate on an item it matches, and failurePolicy Fail refuses the whole
    # DeleteCollection, for every requester. The namespace controller does
    # not fall back to item deletes on that error.
    case("DEFECT giantswarm/agent-platform#1362: the namespace controller's DeleteCollection fails while a finalizer holds one Secret",
         kubectl(["delete", "--raw", f"/api/v1/namespaces/{ORG}/secrets", "-f", "-"], collection, NS_CONTROLLER, ()),
         "API:agent-platform-organization-secret-deletes' with binding 'agent-platform-organization-secret-deletes' denied request: expression "
         "'authorizer.group('').resource('secrets').namespace(request.namespace).name(request.name).check('get').allowed()' resulted in error: no such key: name")
    case("the namespace controller deletes a Secret a finalizer holds (one item, Background)",
         delete_secret("tenant-credential-finalized", NS_CONTROLLER, propagationPolicy="Background"))
    # The garbage collector deletes a dependent with Foreground, Orphan or
    # Background propagation and UID and resourceVersion preconditions
    # (pkg/controller/garbagecollector/garbagecollector.go:627, 638-648;
    # operations.go:53-65); it may get every object.
    owner = json.loads(must(kubectl(["create", "-o", "json", "-f", "-"],
                                    yaml.safe_dump({"apiVersion": "v1", "kind": "ConfigMap", "metadata": {"name": "credential-owner", "namespace": ORG}})),
                            "create the owner of a Secret"))
    owned = json.loads(must(kubectl(["create", "-o", "json", "-f", "-"], yaml.safe_dump(credential("tenant-credential-owned", owner={
        "apiVersion": "v1", "kind": "ConfigMap", "name": "credential-owner", "uid": owner["metadata"]["uid"]}))), "create an owned Secret"))
    pre = {"uid": owned["metadata"]["uid"], "resourceVersion": owned["metadata"]["resourceVersion"]}
    for propagation in ("Foreground", "Orphan", "Background"):
        case(f"the garbage collector deletes an owned Secret with {propagation} propagation and its preconditions",
             delete_secret("tenant-credential-owned", GC, propagationPolicy=propagation, preconditions=pre))


def prove_admin_mirrors():
    """giantswarm/agent-platform#1357: agent-platform-operator (0.3.x,
    internal/controller/objects.go mirrorAdmins) mirrors each rbac-operator
    RoleBinding of a parent to cluster-admin (write-all-customer-group,
    rbac-operator pkg/key WriteAllCustomerGroupRoleBindingName) into each of
    its organization namespaces: the same name, the roleRef
    agent-platform:organizations-admin, its User and Group subjects, labelled
    as the operator's object and annotated with its source."""
    mirror = rb("write-all-customer-group", ORG, role="agent-platform:organizations-admin",
                subjects=[{"apiGroup": "rbac.authorization.k8s.io", "kind": "Group", "name": "acme:platform-admins"}])
    mirror["metadata"]["labels"] = {OBJECT_LABEL: "organizations-admin-mirror", "app.kubernetes.io/managed-by": "agent-platform-operator"}
    mirror["metadata"]["annotations"] = {"agent-platform.giantswarm.io/copied-from": f"{PARENT}/write-all-customer-group"}
    case("the operator mirrors a customer admin Group of rbac-operator as organizations-admin", create(mirror, as_=OP))
    # By hand, as the operator writes it: the copies' label keeps it the
    # operator's, so a person who may bind the role (carol, organizations-admin
    # in ORG by her mirror) is refused by admission.
    case("an organizations-admin writes no mirror labelled as the operator's",
         create(mirror, as_=CAROL, groups=()), "agent-platform-organization-copies", "refused for carol: CREATE rolebindings write-all-customer-group")
    # An organization admin may not bind organizations-admin: RBAC refuses it,
    # as the admin holds neither bind on it nor its rules.
    plain = rb("write-all-customer-group", ORG, role="agent-platform:organizations-admin",
               subjects=[{"apiGroup": "rbac.authorization.k8s.io", "kind": "Group", "name": "acme:platform-admins"}])
    case("an organization admin writes no RoleBinding to organizations-admin",
         create(plain, *ADMIN), "RBAC:attempting to grant RBAC permissions not currently held")
    # Without the label, an organizations-admin bound in ORG holds every rule
    # of the role there, so RBAC lets it bind the role itself (no `bind`
    # needed), and no policy holds the roleRef of a person's RoleBinding in an
    # organization namespace: admitted today. The subject gains no right in
    # ORG beyond agent-platform:admin's, which the writer may grant; the
    # operator does not remove a binding it did not label. Pinned as it is
    # until giantswarm/agent-platform#1363 decides it.
    case("NOT HELD giantswarm/agent-platform#1363: an organizations-admin binds organizations-admin in an organization namespace without the operator's label",
         create(plain, as_=CAROL, groups=()))


def prove_git_admins():
    """Commit mode: ORG3's first admins are a file of its path, applied by its
    Flux ServiceAccount (with no group, as kustomize-controller impersonates
    it), which kustomize-controller labels with the Kustomization. The erasure
    deletes that Kustomization first and waits for it: Flux must never try to
    prune organization-admins, which is not deleted while the namespace
    lives, so the binding carries prune: disabled and goes with the
    namespace."""
    must(create(rb("organization-flux", ORG3, role="agent-platform:organization-flux",
                   subjects=[{"kind": "ServiceAccount", "name": ORG3, "namespace": FLUX_NS}]), as_=OP, dry=False),
         "bind ORG3's Flux ServiceAccount")
    admins = rb("organization-admins", ORG3, role="agent-platform:admin")
    admins["metadata"]["labels"] = {"kustomize.toolkit.fluxcd.io/name": ORG3, "kustomize.toolkit.fluxcd.io/namespace": FLUX_NS}
    policy = "agent-platform-organization-admins-binding"
    keep = "applied from git carries kustomize.toolkit.fluxcd.io/prune: disabled, so the erasure of its organization never waits on its prune"
    case("a first-admins file without prune: disabled is refused", create(admins, as_=FLUX_SA3, groups=()), policy,
         keep + f"; refused: CREATE in {ORG3}")
    admins["metadata"]["annotations"] = {"kustomize.toolkit.fluxcd.io/prune": "disabled"}
    case("a first-admins file with prune: disabled is applied", create(admins, as_=FLUX_SA3, groups=(), dry=False))
    case("a file in git that drops prune: disabled from organization-admins is refused",
         patch("rolebinding", "organization-admins", ORG3, {"metadata": {"annotations": {"kustomize.toolkit.fluxcd.io/prune": None}}},
               as_=FLUX_SA3, groups=()), policy, keep + f"; refused: UPDATE in {ORG3}")
    case("a file in git changes the first admins and keeps prune: disabled",
         patch("rolebinding", "organization-admins", ORG3,
               {"subjects": [{"apiGroup": "rbac.authorization.k8s.io", "kind": "Group", "name": "treasury-leads"}]}, as_=FLUX_SA3, groups=()))
    # Flux skips an object annotated prune: disabled; were it to prune it
    # while the namespace lives, the delete would be refused and the
    # Kustomization would keep its finalizer: hence the annotation.
    case("a prune of organization-admins while its namespace lives is refused", delete("rolebinding", "organization-admins", ORG3, as_=FLUX_SA3, groups=()),
         policy, f"refused: DELETE in {ORG3}")
    # The erasure's namespace step: the namespace controller removes it.
    must(kubectl(["delete", "namespace", ORG3, "--wait=false"]), f"delete namespace {ORG3}")
    deadline = time.time() + 30
    while time.time() < deadline:
        rc, out = kubectl(["get", "namespace", ORG3, "-o", "jsonpath={.metadata.deletionTimestamp}"])
        if rc == 0 and out.strip():
            break
        time.sleep(0.5)
    case("the namespace controller deletes the git-applied organization-admins once its namespace terminates",
         delete("rolebinding", "organization-admins", ORG3, as_=NS_CONTROLLER, groups=()))


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
    for namespace, denied in ((ORG, "agent-platform-organization-admins-binding"), (OLD, None)):
        case(f"RoleBinding DELETE collection in {namespace}",
             kubectl(["delete", "--raw", f"/apis/rbac.authorization.k8s.io/v1/namespaces/{namespace}/rolebindings?dryRun=All"], as_=NS_CONTROLLER), denied)
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
    in_git = "erased only once its delete pull request merged"
    case("an organization in git with prune: enabled is not deleted", delete("agentplatformorganization", "in-git", PARENT, as_=PERSON),
         "agent-platform-organization-delete-intent", in_git)
    case("an organization missing from git with prune: enabled is not deleted", delete("agentplatformorganization", "lost", PARENT, as_=PERSON),
         "agent-platform-organization-delete-intent", in_git)
    case("an organization whose delete pull request merged is deleted", delete("agentplatformorganization", "merged", PARENT, as_=PERSON))


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
            for prove in (prove_roles, prove_admin_mirrors, prove_operator, prove_flux_role, prove_secret_deletes, prove_labels, prove_admins_binding, prove_subjects, prove_helmreleases,
                          prove_delete_intent, prove_egress_floor, prove_copies, prove_sandbox,
                          prove_git_admins):  # last: it deletes ORG3
                prove()
        finally:
            API.stop()
    failed = [n for ok, n in results if not ok]
    print(f"{len(results) - len(failed)}/{len(results)} cases as expected")
    if failed:
        fail("; ".join(failed))


if __name__ == "__main__":
    main()
