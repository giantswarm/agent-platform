#!/usr/bin/env python3
"""Prove the admission policies of the Organizations' workers on a real API server (giantswarm/agent-platform#1036).

verify-tenant-workers.py checks what the connectivity chart renders; this
script checks what the policies' CEL decides. It creates a kind cluster,
installs the WorkerPool CRD of the Substrate floor (the substrate-crds chart
at the floor of components.substrate-crds.versionRange) and the two
ValidatingAdmissionPolicies the chart renders with tenants.organizations.enabled,
then writes as the people and the controllers involved (impersonated; RBAC is
not under test, every identity is cluster-admin):

- WorkerPools in a labelled namespace: only agent-manager's ServiceAccount,
  only as a copy of the platform WorkerPool kagent/<POOL> (name and
  spec, replicas aside); the namespace controller may delete one; the status
  and scale subresources stay open; without the platform WorkerPool every
  write is refused; the kagent and unlabelled namespaces are not held;
- worker Deployments, ReplicaSets and Pods (the label on the object or its
  pod template, before or after the write): only ate-controller and the
  Deployment and ReplicaSet controllers; a real worker Deployment rolls out
  through kind's kube-controller-manager; ate-controller marks a pod busy;
  no one adds an ephemeral container, execs or attaches to a worker; an
  orphaning and a foreground delete complete (the garbage collector's
  updates), and so does the namespace's deletion.

Opt-in, not in CI: needs docker, kind, kubectl, helm (gsoci.azurecr.io) and
PyYAML. KIND_IMAGE selects the node image (kindest/node:v1.35.0; the
Substrate CRD needs the CEL format library, which Kubernetes 1.31 lacks).
HELM selects the binary. The cluster is deleted at the end.

Usage: prove-tenant-workers.py META_CHART CONNECTIVITY_CHART
"""
import json
import os
import re
import subprocess
import sys
import tempfile
import time

import yaml

HELM = os.environ.get("HELM", "helm")
KIND_IMAGE = os.environ.get("KIND_IMAGE", "kindest/node:v1.35.0")
CLUSTER = "agent-platform-tenant-workers"
CTX = f"kind-{CLUSTER}"
KCFG = ""
REC = "system:serviceaccount:agent-platform:agent-manager"
ATE = "system:serviceaccount:ate-system:ate-controller"
RSC = "system:serviceaccount:kube-system:replicaset-controller"
NSC = "system:serviceaccount:kube-system:namespace-controller"
PERSON = ["--as=jose", "--as-group=system:masters"]
# The platform WorkerPool, renamed from the chart's default: the policies take
# its name from kagent.substrateWorkerPool.name.
POOL = "org-workers"
results = []


def kubectl(args, stdin=None, as_=None):
    cmd = ["kubectl", "--kubeconfig", KCFG, "--context", CTX]
    if as_ == "person":
        cmd += PERSON
    elif as_:
        cmd += [f"--as={as_}"]
    cmd += args
    r = subprocess.run(cmd, input=stdin, capture_output=True, text=True)
    return r.returncode, r.stdout + r.stderr


def apply(obj, as_=None, dry=False, verb="create"):
    args = [verb, "-f", "-"] + (["--dry-run=server"] if dry else [])
    return kubectl(args, yaml.safe_dump(obj), as_)


def case(name, rc_out, allowed, needle=None):
    rc, out = rc_out
    ok = (rc == 0) if allowed else (rc != 0 and (needle is None or needle in out))
    results.append((ok, name, out.strip().splitlines()[-1] if out.strip() else ""))
    print(("ok:  " if ok else "FAIL:"), name, "|", out.strip().splitlines()[-1] if out.strip() else "")


def wp(ns, image="ghcr.io/x/ateom:1", replicas=1, template=None):
    return {"apiVersion": "ate.dev/v1alpha1", "kind": "WorkerPool", "metadata": {"name": POOL, "namespace": ns},
            "spec": {"replicas": replicas, "workerImage": image,
                     "template": template if template is not None else {"labels": {"team": "x"}, "serviceAccountName": "workers"}}}


def deploy(ns, name, meta_label=True, tmpl_label=True):
    labels = {"ate.dev/worker-pool": POOL} if meta_label else {}
    tl = {"app": name, **({"ate.dev/worker-pool": POOL} if tmpl_label else {})}
    return {"apiVersion": "apps/v1", "kind": "Deployment", "metadata": {"name": name, "namespace": ns, "labels": labels},
            "spec": {"replicas": 1, "selector": {"matchLabels": {"app": name}},
                     "template": {"metadata": {"labels": tl}, "spec": {"containers": [{"name": "c", "image": "registry.k8s.io/pause:3.10"}]}}}}


def pod(ns, name):
    return {"apiVersion": "v1", "kind": "Pod", "metadata": {"name": name, "namespace": ns, "labels": {"ate.dev/worker-pool": POOL}},
            "spec": {"containers": [{"name": "c", "image": "registry.k8s.io/pause:3.10"}]}}


def substrate_floor(meta_chart):
    values = yaml.safe_load(open(os.path.join(meta_chart, "values.yaml")))
    rng = values["components"]["substrate-crds"]["versionRange"]
    m = re.match(r"^>=\s*(\S+)", rng)
    if not m:
        sys.exit(f"FAIL: no floor in components.substrate-crds.versionRange {rng!r}")
    return m.group(1)


def workerpool_crd(meta_chart, tmp):
    values = yaml.safe_load(open(os.path.join(meta_chart, "values.yaml")))
    repo = values["components"]["substrate-crds"]["repository"]
    subprocess.run([HELM, "pull", f"{repo}/substrate-crds", "--version", substrate_floor(meta_chart), "--untar", "--untardir", tmp],
                   check=True, capture_output=True, text=True)
    return os.path.join(tmp, "substrate-crds", "templates", "ate.dev_workerpools.yaml")


def main():
    global KCFG
    meta, chart = sys.argv[1:3]
    with tempfile.TemporaryDirectory() as tmp:
        KCFG = os.path.join(tmp, "kubeconfig")
        crd = workerpool_crd(meta, tmp)
        subprocess.run(["kind", "create", "cluster", "--name", CLUSTER, "--image", KIND_IMAGE, "--kubeconfig", KCFG, "--wait", "120s"],
                       check=True, capture_output=True, text=True)
        try:
            prove(chart, crd)
        finally:
            subprocess.run(["kind", "delete", "cluster", "--name", CLUSTER, "--kubeconfig", KCFG], capture_output=True, text=True)
    failed = [n for ok, n, _ in results if not ok]
    print(f"{len(results) - len(failed)}/{len(results)} cases as expected")
    if failed:
        sys.exit("FAIL: " + "; ".join(failed))


def prove(chart, crd):
    r = subprocess.run([HELM, "template", "t", chart, "--namespace", "agent-platform",
                        "--set", "ingress.parentRefs[0].name=x", "--set", "kagent.harness.snapshotLocation=s3://a/b",
                        "--set", "kagent.namespaceOverride=kagent", "--set", "components.kagent.enabled=true",
                        "--set", "components.substrate.enabled=true", "--set", "components.substrate-crds.enabled=true",
                        "--set", "tenants.organizations.enabled=true",
                        "--set", f"kagent.substrateWorkerPool.name={POOL}",
                        "--show-only", "templates/substrate/tenant-workers-admission.yaml"], capture_output=True, text=True, check=True)
    policies = [d for d in yaml.safe_load_all(r.stdout) if d]
    if len(policies) != 4:
        sys.exit(f"FAIL: the chart renders {len(policies)} admission objects, not the two policies and their bindings")
    rc, out = kubectl(["apply", "--server-side", "-f", crd])
    if rc != 0:
        sys.exit(f"FAIL: the WorkerPool CRD does not install on {KIND_IMAGE}:\n{out}")
    kubectl(["wait", "--for=condition=Established", "crd/workerpools.ate.dev", "--timeout=60s"])
    for ns, labels in (("kagent", {"giantswarm.io/organization": "giantswarm"}), ("org-acme", {"giantswarm.io/organization": "acme"}), ("plain", {})):
        apply({"apiVersion": "v1", "kind": "Namespace", "metadata": {"name": ns, "labels": labels}})
    # RBAC is not under test: the impersonated ServiceAccounts may do anything.
    apply({"apiVersion": "rbac.authorization.k8s.io/v1", "kind": "ClusterRoleBinding", "metadata": {"name": "impersonated-controllers"},
           "roleRef": {"apiGroup": "rbac.authorization.k8s.io", "kind": "ClusterRole", "name": "cluster-admin"},
           "subjects": [{"kind": "ServiceAccount", "name": u.split(":")[3], "namespace": u.split(":")[2]} for u in (REC, ATE, RSC, NSC)]})
    case("platform pool in kagent by a person (kagent excluded)", apply(wp("kagent"), as_="person"), True)
    for p in policies:
        rc, out = apply(p, verb="apply")
        if rc != 0:
            sys.exit(f"FAIL: the API server refuses {p['kind']} {p['metadata']['name']}:\n{out}")
    # The API server's policy and parameter informers: wait for the first
    # refusal of each policy (each binding syncs on its own).
    for writer, image in (("person", "ghcr.io/x/ateom:1"), (REC, "evil:1")):
        for _ in range(100):
            rc, out = apply(wp("org-acme", image=image), as_=writer, dry=True)
            if rc != 0:
                break
            time.sleep(0.3)
    for name in ("agent-platform-tenant-workers", "agent-platform-tenant-workerpool-copy"):
        _, out = kubectl(["get", "validatingadmissionpolicy", name, "-o", "json"])
        tc = json.loads(out).get("status", {}).get("typeChecking", {})
        case(f"{name}: the API server's type check finds nothing", (1 if tc else 0, json.dumps(tc)), True)

    # WorkerPools
    case("WP: a person (cluster-admin) creates a copy in org-acme", apply(wp("org-acme"), as_="person", dry=True), False, "only agent-manager")
    case("WP: the reconciler creates another image", apply(wp("org-acme", image="evil:1"), as_=REC, dry=True), False, f"is a copy of the WorkerPool kagent/{POOL}")
    case("WP: the reconciler creates another template", apply(wp("org-acme", template={"nodeSelector": {"a": "b"}, "serviceAccountName": "workers", "labels": {"team": "x"}}), as_=REC, dry=True), False, "is a copy")
    case("WP: the reconciler creates no template", apply(wp("org-acme", template={}), as_=REC, dry=True), False, "is a copy")
    other = wp("org-acme"); other["metadata"]["name"] = "other"
    case("WP: the reconciler creates a copy under another name", apply(other, as_=REC, dry=True), False, "is a copy")
    case("WP: the reconciler creates the copy (other replicas)", apply(wp("org-acme", replicas=3), as_=REC), True)
    case("WP: the reconciler updates the copy's replicas", kubectl(["patch", "workerpool", POOL, "-n", "org-acme", "--type=merge", "-p", '{"spec":{"replicas":1}}'], as_=REC), True)
    case("WP: a person patches the copy's image", kubectl(["patch", "workerpool", POOL, "-n", "org-acme", "--type=merge", "-p", '{"spec":{"workerImage":"evil:1"}}'], as_="person"), False, "ValidatingAdmissionPolicy")
    case("WP: a person scales the copy (scale subresource)", kubectl(["scale", "workerpool", POOL, "-n", "org-acme", "--replicas=2"], as_="person"), True)
    case("WP: a person writes the status subresource", kubectl(["patch", "workerpool", POOL, "-n", "org-acme", "--subresource=status", "--type=merge", "-p", '{"status":{"readyReplicas":0}}'], as_="person"), True)
    case("WP: a person deletes the copy", kubectl(["delete", "workerpool", POOL, "-n", "org-acme", "--dry-run=server"], as_="person"), False, "only agent-manager")
    case("WP: the namespace controller deletes the copy", kubectl(["delete", "workerpool", POOL, "-n", "org-acme", "--dry-run=server"], as_=NSC), True)
    case("WP: a person in an unlabelled namespace", apply(wp("plain", image="any:1"), as_="person", dry=True), True)

    # Workers
    case("Deploy: a person creates a labelled Deployment", apply(deploy("org-acme", "p1"), as_="person", dry=True), False, "only Substrate controllers write deployments")
    case("Deploy: a person creates a Deployment labelled on its template only", apply(deploy("org-acme", "p2", meta_label=False), as_="person", dry=True), False, "only Substrate controllers")
    case("Deploy: a person creates an unlabelled Deployment", apply(deploy("org-acme", "p3", False, False), as_="person", dry=True), True)
    case("Deploy: a person creates a labelled Deployment in kagent", apply(deploy("kagent", "p4"), as_="person", dry=True), True)
    case("Deploy: ate-controller creates the worker Deployment", apply(deploy("org-acme", POOL), as_=ATE), True)
    rs = pods = None
    for _ in range(60):
        _, rs = kubectl(["get", "rs", "-n", "org-acme", "-l", f"app={POOL}", "-o", "name"])
        _, pods = kubectl(["get", "pods", "-n", "org-acme", "-l", f"app={POOL}", "-o", "name"])
        if rs.strip() and pods.strip():
            break
        time.sleep(1)
    case("Chain: the deployment controller made the ReplicaSet and the ReplicaSet controller the Pod", (0 if rs.strip() and pods.strip() else 1, f"{rs.strip()} {pods.strip()}"), True)
    podname = pods.strip().split("/")[-1]
    case("Deploy: a person removes the label from the worker Deployment", kubectl(["label", "deployment", POOL, "-n", "org-acme", "ate.dev/worker-pool-"], as_="person"), False, "only Substrate controllers")
    case("Deploy: a person changes the worker image", kubectl(["set", "image", f"deployment/{POOL}", "-n", "org-acme", "c=evil:1"], as_="person"), False, "only Substrate controllers")
    case("Deploy: a person scales the worker Deployment", kubectl(["scale", "deployment", POOL, "-n", "org-acme", "--replicas=1"], as_="person"), True)
    case("Pod: a person creates a labelled Pod", apply(pod("org-acme", "pp"), as_="person", dry=True), False, "only Substrate controllers write pods")
    case("Pod: the ReplicaSet controller creates a labelled Pod", apply(pod("org-acme", "pp"), as_=RSC, dry=True), True)
    case("Pod: ate-controller marks a worker busy", kubectl(["label", "pod", podname, "-n", "org-acme", "ate.dev/busy=true"], as_=ATE), True)
    case("Pod: a person takes the worker label off a worker pod", kubectl(["label", "pod", podname, "-n", "org-acme", "ate.dev/worker-pool-"], as_="person"), False, "only Substrate controllers")
    case("Pod: a person labels a worker pod", kubectl(["label", "pod", podname, "-n", "org-acme", "x=y"], as_="person"), False, "only Substrate controllers")
    case("Pod: a person adds an ephemeral container", kubectl(["debug", podname, "-n", "org-acme", "--image=busybox", "--", "true"], as_="person"), False, "pods/ephemeralcontainers")
    case("Pod: a person execs into a worker pod", kubectl(["exec", podname, "-n", "org-acme", "--", "true"], as_="person"), False, "no one execs into or attaches to a Substrate worker")
    case("Pod: a person attaches to a worker pod", kubectl(["attach", podname, "-n", "org-acme"], as_="person"), False, "no one execs into or attaches to a Substrate worker")
    apply(pod("org-acme", "mine") | {"metadata": {"name": "mine", "namespace": "org-acme"}}, as_="person")
    rc, out = kubectl(["exec", "mine", "-n", "org-acme", "--", "true"], as_="person")
    case("Pod: a person execs into their own pod (admission lets it through)", (1 if "ValidatingAdmissionPolicy" in out else 0, out), True)
    case("Pod: a person deletes a worker pod", kubectl(["delete", "pod", podname, "-n", "org-acme", "--wait=false"], as_="person"), True)

    # Garbage collector: an orphaning delete edits the ReplicaSet's owner references.
    case("GC: orphan the worker Deployment", kubectl(["delete", "deployment", POOL, "-n", "org-acme", "--cascade=orphan", "--timeout=60s"]), True)
    owned = "x"
    for _ in range(30):
        _, owned = kubectl(["get", "rs", "-n", "org-acme", "-l", f"app={POOL}", "-o", "jsonpath={range .items[*]}{.metadata.name}={.metadata.ownerReferences}{end}"])
        if owned.strip().endswith("="):
            break
        time.sleep(1)
    case("GC: the ReplicaSet lost its owner reference", (0 if owned.strip().endswith("=") else 1, owned), True)
    case("GC: foreground delete of the ReplicaSet completes", kubectl(["delete", "rs", "-n", "org-acme", "-l", f"app={POOL}", "--cascade=foreground", "--timeout=60s"]), True)

    # Without the platform pool every WorkerPool write in a tenant is refused.
    kubectl(["delete", "workerpool", POOL, "-n", "kagent"])
    time.sleep(2)
    case("Param: no platform pool, the reconciler updates the copy", kubectl(["patch", "workerpool", POOL, "-n", "org-acme", "--type=merge", "-p", '{"spec":{"replicas":2}}'], as_=REC), False, POOL)
    case("Namespace: org-acme with a WorkerPool and workers is deleted", kubectl(["delete", "namespace", "org-acme", "--timeout=120s"]), True)


if __name__ == "__main__":
    main()
