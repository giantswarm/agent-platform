#!/usr/bin/env python3
"""Assert agent-platform-secret-deletes in every namespace, organizations off
(giantswarm/agent-platform#1365), on a local API server.

agent-platform:admin holds create, update and delete on Secrets and no get.
The API server answers a Secret delete with the stored Secret, data included,
also on a dry run, when it adds a garbage collector finalizer (Orphan or
Foreground propagation, orphanDependents), when a finalizer already holds the
Secret, and for a DeleteCollection (the list of its items):
tests/verify-organization-admission.py prove_secret_deletes has the
k8s.io/apiserver lines. The role renders whenever kagent is on and can be
bound in any namespace, so the policy renders with it and matches every
namespace, with agentPlatformOrganizations.enabled off as on.

The render: the policy and its binding render with kagent on, organizations
off (Fail, Deny, no namespace or object selector), the same with
organizations on, and not with kagent off.

On the API server (tests/apiserver.py: envtest's etcd and kube-apiserver,
RBAC on, no controllers), with only this render's roles and policy, for the
admin bound in the kagent namespace and in a plain namespace:

- an Orphan, Foreground or orphanDependents delete (body or query), a delete
  of a Secret a finalizer holds or that is being deleted, dry run or not, is
  refused by the policy; a DeleteCollection by a requester who may
  deletecollection Secrets but not get them is refused, with or without a
  body, with or without a held Secret; the admin's own DeleteCollection is
  refused by RBAC;
- every other delete of the admin is answered with a Status, never the
  Secret;
- a requester who may read the Secret (get, list, watch or patch, also by
  resourceNames) is not held: a reader's Orphan delete and DeleteCollection,
  a list-, watch- or patch-only deleter, a Flux controller bound to
  cluster-admin pruning a held Secret, the namespace controller's
  DeleteCollection while a finalizer holds a Secret, the garbage collector
  with its preconditions, the legacy token cleaner with its precondition; a
  deleter who may list only another Secret by name is held;
- no default role of the API server (its bootstrap ClusterRoles, and its
  Roles in kube-system and kube-public) deletes Secrets without one of those
  four verbs.

Every refusal names the policy with its own message, never an evaluation
error (which refuses too under failurePolicy Fail and would read as the one
under test).

Needs helm (gsoci.azurecr.io), openssl, PyYAML and github.com on the first
run (the envtest binaries). HELM selects the binary.

Usage: verify-secret-deletes.py META_CHART CONNECTIVITY_CHART
"""
import importlib.util
import json
import os
import subprocess
import sys

import yaml

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from apiserver import APIServer  # noqa: E402

HELM = os.environ.get("HELM", "helm")
POLICY = "agent-platform-secret-deletes"
KAGENT = "kagent"
PLAIN = "team-a"
ADMIN = "dana"  # agent-platform:admin in KAGENT and PLAIN: create, update, delete on Secrets, no get
READER = "rita"  # gets and deletes Secrets in both
COLLECTOR = "erin"  # deletecollection on Secrets, no get
SECRET_DATA = "c3ludGhldGlj"
NS_CONTROLLER = "system:serviceaccount:kube-system:namespace-controller"
GC = "system:serviceaccount:kube-system:generic-garbage-collector"
FLUX = "system:serviceaccount:flux-giantswarm:kustomize-controller"  # bound to cluster-admin, as Flux installs it
# The bootstrap controller that deletes Secrets with delete and patch, no get
# (k8s.io/kubernetes plugin/pkg/auth/authorizer/rbac/bootstrappolicy
# controller_policy.go).
TOKEN_CLEANER = "legacy-service-account-token-cleaner"
# Deleters who read the Secrets by one verb only; NARROW lists one other Secret.
READ_VERBS = ("list", "watch", "patch")
NARROW = "nora"

API = None
results = []


def fail(msg):
    sys.exit(f"FAIL: {msg}")


def run(cmd):
    return subprocess.run(cmd, capture_output=True, text=True)


# ---- the render --------------------------------------------------------------

def render(chart, *extra):
    r = run([HELM, "template", "t", chart, "--namespace", "agent-platform",
             "--set", "ingress.parentRefs[0].name=x", "--set", "kagent.harness.snapshotLocation=s3://a/b",
             "--set", "kagent.database.postgres.urlFile=/etc/kagent-db/uri", "--set", f"kagent.namespaceOverride={KAGENT}",
             *extra])
    if r.returncode != 0:
        fail(f"the render failed:\n{r.stderr}")
    return [d for d in yaml.safe_load_all(r.stdout) if d]


def organizations_on(chart):
    """The organization admission test's render with organizations on."""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "verify-organization-admission.py")
    spec = importlib.util.spec_from_file_location("organization_admission", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    r = mod.render(chart, *mod.ON)
    if r.returncode != 0:
        fail(f"the render with organizations on failed:\n{r.stderr}")
    return [d for d in yaml.safe_load_all(r.stdout) if d]


def named(objs):
    return sorted((d for d in objs if d["metadata"]["name"] == POLICY), key=lambda d: d["kind"])


def check_render(chart):
    off = render(chart, "--set", "components.kagent.enabled=true")
    policy = named(off)
    if [d["kind"] for d in policy] != ["ValidatingAdmissionPolicy", "ValidatingAdmissionPolicyBinding"]:
        fail(f"with kagent on and organizations off, {POLICY} and its binding do not render: {[d['kind'] for d in policy]}")
    vap, binding = policy
    if any(d["metadata"]["name"].startswith("agent-platform-organization") for d in off):
        fail("organizations off renders an organization policy")
    if vap["spec"].get("failurePolicy") != "Fail" or binding["spec"].get("validationActions") != ["Deny"]:
        fail(f"{POLICY}: failurePolicy {vap['spec'].get('failurePolicy')}, validationActions {binding['spec'].get('validationActions')}")
    selectors = {k for k in ("namespaceSelector", "objectSelector") if vap["spec"]["matchConstraints"].get(k)}
    selectors |= {k for k in ("namespaceSelector", "objectSelector") if (binding["spec"].get("matchResources") or {}).get(k)}
    if selectors:
        fail(f"{POLICY} narrows its namespaces or objects by {sorted(selectors)}: the role is bound in any namespace")
    print(f"ok:  kagent on, organizations off: {POLICY} renders (Fail, Deny, every namespace)")
    if named(organizations_on(chart)) != policy:
        fail(f"{POLICY} renders differently with organizations on")
    print(f"ok:  organizations on: {POLICY} renders the same")
    if named(render(chart, "--set", "components.kagent.enabled=false")):
        fail(f"{POLICY} renders with kagent off")
    print(f"ok:  kagent off: no {POLICY}")
    roles = [d for d in off if d["kind"] == "ClusterRole" and d["metadata"]["name"] == "agent-platform:admin"]
    if not roles:
        fail("agent-platform:admin does not render with kagent on")
    return roles + policy


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


def case(name, rc_out, by=None, msg=None):
    """by=None: admitted. by=POLICY: refused by the policy with its own
    message (msg in it when given). by='RBAC:<text>': refused by the
    authorizer with that text."""
    rc, out = rc_out
    last = out.strip().splitlines()[-1] if out.strip() else ""
    if by is None:
        ok = rc == 0
    elif by.startswith("RBAC:"):
        ok = rc != 0 and by.split(":", 1)[1] in out
    else:
        ok = (rc != 0 and f"ValidatingAdmissionPolicy '{by}'" in out and "denied request" in out
              and "resulted in error" not in out and "failed expression" not in out and (msg is None or msg in out))
    results.append((ok, name))
    print(("ok:  " if ok else "FAIL:"), name, "|", last)


def binding(kind, name, namespace, role, subject):
    meta = {"name": name, **({"namespace": namespace} if namespace else {})}
    return {"apiVersion": "rbac.authorization.k8s.io/v1", "kind": kind, "metadata": meta,
            "roleRef": {"apiGroup": "rbac.authorization.k8s.io", "kind": "ClusterRole", "name": role}, "subjects": [subject]}


def user(name):
    return {"apiGroup": "rbac.authorization.k8s.io", "kind": "User", "name": name}


def secret(name, namespace, finalizers=None, owner=None):
    meta = {"name": name, "namespace": namespace}
    if finalizers:
        meta["finalizers"] = finalizers
    if owner:
        meta["ownerReferences"] = [owner]
    return {"apiVersion": "v1", "kind": "Secret", "type": "Opaque", "metadata": meta, "data": {"apiKey": SECRET_DATA}}


def make(obj):
    return json.loads(must(kubectl(["create", "-o", "json", "-f", "-"], yaml.safe_dump(obj)),
                           f"create {obj['kind']} {obj['metadata'].get('namespace', '')}/{obj['metadata']['name']}"))


def delete_secret(namespace, name, as_, groups=(), dry=True, query="", **options):
    """A DELETE as a client sends it: DeleteOptions in the body, dryRun=All
    unless dry is False; no_body=True sends none (dryRun=All in the query)."""
    url = f"/api/v1/namespaces/{namespace}/secrets/{name}{query}"
    if options.pop("no_body", False):
        return kubectl(["delete", "--raw", url + ("&" if "?" in url else "?") + "dryRun=All" if dry else url], None, as_, groups)
    body = {"kind": "DeleteOptions", "apiVersion": "v1", **options, **({"dryRun": ["All"]} if dry else {})}
    return kubectl(["delete", "--raw", url, "-f", "-"], json.dumps(body), as_, groups)


def delete_collection(namespace, as_, groups=(), body=True, **options):
    url = f"/api/v1/namespaces/{namespace}/secrets"
    if not body:
        return kubectl(["delete", "--raw", url + "?dryRun=All"], None, as_, groups)
    opts = {"kind": "DeleteOptions", "apiVersion": "v1", "dryRun": ["All"], **options}
    return kubectl(["delete", "--raw", url, "-f", "-"], json.dumps(opts), as_, groups)


def status_only(rc_out):
    """An admitted delete of the admin must be answered with a Status."""
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


# ---- the proof ---------------------------------------------------------------

def fixtures():
    for n in (KAGENT, PLAIN, "flux-giantswarm"):
        must(kubectl(["create", "namespace", n]), f"create namespace {n}")
    for n in (KAGENT, PLAIN):
        apply(binding("RoleBinding", "admins", n, "agent-platform:admin", user(ADMIN)))
        apply({"apiVersion": "rbac.authorization.k8s.io/v1", "kind": "Role", "metadata": {"name": "secret-reader", "namespace": n},
               "rules": [{"apiGroups": [""], "resources": ["secrets"], "verbs": ["get", "delete", "deletecollection"]}]})
        apply({"apiVersion": "rbac.authorization.k8s.io/v1", "kind": "RoleBinding", "metadata": {"name": "secret-reader", "namespace": n},
               "roleRef": {"apiGroup": "rbac.authorization.k8s.io", "kind": "Role", "name": "secret-reader"}, "subjects": [user(READER)]})
    apply({"apiVersion": "rbac.authorization.k8s.io/v1", "kind": "ClusterRole", "metadata": {"name": "secret-collection-deleter"},
           "rules": [{"apiGroups": [""], "resources": ["secrets"], "verbs": ["deletecollection"]}]})
    apply(binding("ClusterRoleBinding", "erin-secret-collection-deleter", None, "secret-collection-deleter", user(COLLECTOR)))
    apply(binding("ClusterRoleBinding", "flux-cluster-admin", None, "cluster-admin",
                  {"kind": "ServiceAccount", "name": "kustomize-controller", "namespace": "flux-giantswarm"}))
    for verb in READ_VERBS:
        apply({"apiVersion": "rbac.authorization.k8s.io/v1", "kind": "ClusterRole", "metadata": {"name": f"secret-{verb}-deleter"},
               "rules": [{"apiGroups": [""], "resources": ["secrets"], "verbs": [verb, "delete", "deletecollection"]}]})
        apply(binding("ClusterRoleBinding", f"{verb}-deleter", None, f"secret-{verb}-deleter", user(f"{verb}-deleter")))
    apply({"apiVersion": "rbac.authorization.k8s.io/v1", "kind": "ClusterRole", "metadata": {"name": "secret-narrow-deleter"},
           "rules": [{"apiGroups": [""], "resources": ["secrets"], "verbs": ["delete"]},
                     {"apiGroups": [""], "resources": ["secrets"], "verbs": ["list"], "resourceNames": ["tenant-credential-plain"]}]})
    apply(binding("ClusterRoleBinding", "narrow-deleter", None, "secret-narrow-deleter", user(NARROW)))
    for n in (KAGENT, PLAIN):
        for name in ("plain", "real-orphan", "gone"):
            make(secret(f"tenant-credential-{name}", n))
        make(secret("tenant-credential-held", n, finalizers=["example.com/hold"]))
        make(secret("tenant-credential-leaving", n, finalizers=["example.com/hold"]))
        must(kubectl(["delete", "secret", "tenant-credential-leaving", "-n", n, "--wait=false"]), f"start deleting {n}/tenant-credential-leaving")


def survey():
    """The default roles that delete Secrets without reading them, from the API
    server's own bootstrap RBAC (ClusterRoles, and Roles in kube-system and
    kube-public): the policy would hold their controllers. None today; a new
    one in a later Kubernetes fails here, naming it."""
    roles = json.loads(must(kubectl(["get", "clusterroles", "-o", "json"]), "list the ClusterRoles"))["items"]
    for n in ("kube-system", "kube-public"):
        roles += json.loads(must(kubectl(["get", "roles", "-n", n, "-o", "json"]), f"list the Roles of {n}"))["items"]

    def verbs(role):
        out = set()
        for r in role.get("rules") or []:
            if set(r.get("apiGroups", [])) & {"", "*"} and set(r.get("resources", [])) & {"secrets", "*"}:
                out |= set(r.get("verbs", []))
        return out

    blind = sorted(f"{r['metadata'].get('namespace', '')}/{r['metadata']['name']}" for r in roles
                   if r["metadata"]["name"].startswith("system:")
                   and verbs(r) & {"delete", "deletecollection"} and not verbs(r) & {"get", "list", "watch", "patch", "*"})
    readers = sorted(r["metadata"]["name"] for r in roles if r["metadata"]["name"].startswith("system:")
                     and verbs(r) & {"delete", "deletecollection"})
    ok = not blind
    results.append((ok, "no default role deletes Secrets without get, list, watch or patch"))
    print(("ok:  " if ok else "FAIL:"), "default roles deleting Secrets without reading them:", blind, "| deleting and reading:", readers)


def prove(n):
    refused = f"refused: {n}/tenant-credential-plain"
    for propagation in ("Orphan", "Foreground"):
        case(f"[{n}] the admin's dry-run {propagation} delete is refused",
             delete_secret(n, "tenant-credential-plain", ADMIN, propagationPolicy=propagation), POLICY, refused)
    case(f"[{n}] the admin's dry-run delete with orphanDependents: true is refused",
         delete_secret(n, "tenant-credential-plain", ADMIN, orphanDependents=True), POLICY, refused)
    case(f"[{n}] the admin's dry-run delete with ?orphanDependents=true and no body is refused",
         delete_secret(n, "tenant-credential-plain", ADMIN, query="?orphanDependents=true", no_body=True), POLICY, refused)
    case(f"[{n}] the admin's Orphan delete (not a dry run) is refused",
         delete_secret(n, "tenant-credential-real-orphan", ADMIN, dry=False, propagationPolicy="Orphan"), POLICY,
         f"refused: {n}/tenant-credential-real-orphan")
    case(f"[{n}] the admin's dry-run Background delete of a Secret a finalizer holds is refused",
         delete_secret(n, "tenant-credential-held", ADMIN, propagationPolicy="Background"), POLICY, f"refused: {n}/tenant-credential-held")
    case(f"[{n}] the admin's delete with no body of a Secret a finalizer holds is refused",
         delete_secret(n, "tenant-credential-held", ADMIN, no_body=True), POLICY, f"refused: {n}/tenant-credential-held")
    case(f"[{n}] the admin's delete of a Secret being deleted, a finalizer holding it, is refused",
         delete_secret(n, "tenant-credential-leaving", ADMIN), POLICY, f"refused: {n}/tenant-credential-leaving")
    case(f"[{n}] the admin deletes no collection of Secrets (RBAC)",
         delete_collection(n, ADMIN, body=False), "RBAC:cannot deletecollection resource \"secrets\"")
    for what, kw in (("Background, with a body", {"propagationPolicy": "Background"}), ("Orphan", {"propagationPolicy": "Orphan"}),
                     ("no body", {"body": False})):
        case(f"[{n}] a requester who may deletecollection but not get Secrets deletes no collection ({what})",
             delete_collection(n, COLLECTOR, **kw), POLICY, f"refused: {n}/tenant-credential-")
    for what, options in (("no DeleteOptions body", {"no_body": True}), ("DeleteOptions without a propagation policy", {}),
                          ("Background propagation", {"propagationPolicy": "Background"}),
                          ("orphanDependents: false", {"orphanDependents": False})):
        case(f"[{n}] the admin's dry-run delete with {what} is answered with a Status",
             status_only(delete_secret(n, "tenant-credential-plain", ADMIN, **options)))
    case(f"[{n}] the admin's Background delete deletes and is answered with a Status",
         status_only(delete_secret(n, "tenant-credential-gone", ADMIN, dry=False, propagationPolicy="Background")))

    # Who may get the Secret is not held.
    case(f"[{n}] a reader's Orphan delete is admitted", delete_secret(n, "tenant-credential-plain", READER, propagationPolicy="Orphan"))
    case(f"[{n}] a reader's Background delete of a held Secret is admitted",
         delete_secret(n, "tenant-credential-held", READER, propagationPolicy="Background"))
    case(f"[{n}] a reader's DeleteCollection is admitted", delete_collection(n, READER, propagationPolicy="Background"))
    for verb in READ_VERBS:
        case(f"[{n}] a deleter who may {verb} Secrets (no get) deletes a held Secret",
             delete_secret(n, "tenant-credential-held", f"{verb}-deleter", propagationPolicy="Background"))
        case(f"[{n}] a deleter who may {verb} Secrets (no get) makes an Orphan delete",
             delete_secret(n, "tenant-credential-plain", f"{verb}-deleter", propagationPolicy="Orphan"))
        case(f"[{n}] a deleter who may {verb} Secrets (no get) deletes a collection",
             delete_collection(n, f"{verb}-deleter", propagationPolicy="Background"))
    case(f"[{n}] a deleter who may list that Secret by name makes an Orphan delete",
         delete_secret(n, "tenant-credential-plain", NARROW, propagationPolicy="Orphan"))
    case(f"[{n}] a deleter who may list only another Secret by name deletes no held Secret",
         delete_secret(n, "tenant-credential-held", NARROW, propagationPolicy="Background"), POLICY, f"refused: {n}/tenant-credential-held")
    case(f"[{n}] a Flux controller bound to cluster-admin prunes a held Secret (Background)",
         delete_secret(n, "tenant-credential-held", FLUX, propagationPolicy="Background"))
    case(f"[{n}] the namespace controller's DeleteCollection while a finalizer holds a Secret is admitted",
         delete_collection(n, NS_CONTROLLER, propagationPolicy="Background"))
    case(f"[{n}] the namespace controller deletes a held Secret",
         delete_secret(n, "tenant-credential-held", NS_CONTROLLER, propagationPolicy="Background"))
    owner = make({"apiVersion": "v1", "kind": "ConfigMap", "metadata": {"name": "credential-owner", "namespace": n}})
    owned = make(secret("tenant-credential-owned", n, owner={"apiVersion": "v1", "kind": "ConfigMap", "name": "credential-owner",
                                                             "uid": owner["metadata"]["uid"]}))
    pre = {"uid": owned["metadata"]["uid"], "resourceVersion": owned["metadata"]["resourceVersion"]}
    for propagation in ("Foreground", "Orphan", "Background"):
        case(f"[{n}] the garbage collector deletes an owned Secret with {propagation} propagation and its preconditions",
             delete_secret(n, "tenant-credential-owned", GC, propagationPolicy=propagation, preconditions=pre))
    # The token cleaner deletes with a resourceVersion precondition
    # (pkg/controller/serviceaccount legacy_serviceaccount_token_cleaner.go),
    # holding delete and patch.
    held = json.loads(must(kubectl(["get", "secret", "tenant-credential-held", "-n", n, "-o", "json"]), "read the held Secret"))
    case(f"[{n}] the legacy token cleaner deletes a held Secret with its precondition",
         delete_secret(n, "tenant-credential-held", f"system:serviceaccount:kube-system:{TOKEN_CLEANER}",
                       preconditions={"resourceVersion": held["metadata"]["resourceVersion"]}))


def main():
    global API
    chart = sys.argv[2]
    objs = check_render(chart)
    API = APIServer()
    try:
        fixtures()
        for d in objs:
            apply(d)
        API.settle([d for d in objs if d["kind"].endswith(("AdmissionPolicy", "AdmissionPolicyBinding"))])
        survey()
        for n in (KAGENT, PLAIN):
            prove(n)
    finally:
        API.stop()
    failed = [n for ok, n in results if not ok]
    print(f"{len(results) - len(failed)}/{len(results)} cases as expected")
    if failed:
        fail("; ".join(failed))


if __name__ == "__main__":
    main()
