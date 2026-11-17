#!/usr/bin/env python3
"""Assert the workers of the organizations' namespaces are admitted (giantswarm/agent-platform#1036, #1268).

agent-platform-operator puts a WorkerPool in each organization namespace,
labelled agent-platform.giantswarm.io/organization. Its worker pods need what
the kagent namespace's workers get from this chart: the atenet router's egress
to them (cilium), the egress gateway's and the actors' dns's ingress from them
(both flavors), and the workers' Kyverno PolicyException.

With agentPlatformOrganizations.enabled, in both flavors:
- every rule that admits the kagent namespace's workers also admits the worker
  pods (ate.dev/worker-pool exists) of every namespace that carries the label
  (exists), on the same ports: the namespaces are selected by the label, never
  listed, and only worker pods are selected;
- substrate-workers stays as it is off; the PolicyException
  substrate-workers-tenants (background false: Kyverno refuses subjects
  otherwise; verify-policyexception-userinfo holds it) matches the same pods in
  those namespaces by a namespaceSelector, excepts the same rules, and only
  when Substrate's controller chain writes them: ate-controller the
  Deployment, the deployment controller the ReplicaSet, the ReplicaSet
  controller the Pods;
- two ValidatingAdmissionPolicies hold the writes there, in every labelled
  namespace: only agent-platform-operator's ServiceAccount writes a
  WorkerPool, and only as a copy of the platform WorkerPool (the policy's
  parameter, refused without it); only the writers the PolicyException
  excepts write a labelled Deployment, ReplicaSet or Pod, and no one adds an
  ephemeral container, execs or attaches to a worker;
- with the value on, no other object of this chart selects the label but the
  egress credential and ModelConfig admission (verify-tenant-rbac and
  verify-tenant-modelconfigs) and the organizations' own policies
  (verify-organization-admission).
Off (the default), no Substrate object selects the label. What the policies'
CEL decides is proven on a local API server by
tests/verify-organization-admission.py. Needs PyYAML. HELM selects the binary.

Usage: verify-tenant-workers.py CONNECTIVITY_CHART
"""

import os
import subprocess
import sys

import yaml

HELM = os.environ.get("HELM", "helm")
ORG = "agent-platform.giantswarm.io/organization"
WORKERS = {"key": "ate.dev/worker-pool", "operator": "Exists"}
TENANT_EXCEPTION = "substrate-workers-tenants"
CILIUM_ORG_PEER = {"matchExpressions": [WORKERS, {"key": f"io.cilium.k8s.namespace.labels.{ORG}", "operator": "Exists"}]}
K8S_ORG_PEER = {
    "namespaceSelector": {"matchExpressions": [{"key": ORG, "operator": "Exists"}]},
    "podSelector": {"matchExpressions": [WORKERS]},
}
BASE = [
    "--namespace", "agent-platform",
    "--set", "ingress.parentRefs[0].name=x",
    "--set", "kagent.harness.snapshotLocation=s3://ci-agent-snapshots/agents",
    "--set", "kagent.namespaceOverride=kagent",
    "--set", "components.kagent.enabled=true",
    "--set", "components.substrate.enabled=true",
    "--set", "components.substrate-crds.enabled=true",
    "--api-versions", "kyverno.io/v1",
    "--api-versions", "cilium.io/v2",
    # The operator's names, which the meta chart derives onto this release.
    "-f", os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "organizations-operator-values.yaml"),
]
# agentPlatformOrganizations.enabled needs the llm listener and its routing policy
# (giantswarm/agent-platform#759).
ON = ["--set", "agentPlatformOrganizations.enabled=true", "--set", "kagent.fluxServiceAccountName=kagent-flux", "--set", "global.domain=ci.example.com",
      "--set", "llmRouting.enabled=true", "--api-versions", "admissionregistration.k8s.io/v1/MutatingAdmissionPolicy"]
# (kind, name, direction, port): the rules of each flavor that admit the
# kagent namespace's workers.
HOPS = {
    "cilium": [
        ("CiliumNetworkPolicy", "substrate-atenet-router", "egress", {"443", "8443"}),
        ("CiliumNetworkPolicy", "substrate-atenet-egress", "ingress", {"8443"}),
        ("CiliumNetworkPolicy", "substrate-dns", "ingress", {"53"}),
    ],
    "kubernetes": [
        ("NetworkPolicy", "substrate-atenet-egress-ingress", "ingress", {"8443"}),
        ("NetworkPolicy", "substrate-dns-ingress", "ingress", {"53"}),
    ],
}


def fail(msg):
    sys.exit(f"FAIL: {msg}")


def render(chart, args):
    r = subprocess.run([HELM, "template", "t", chart, *BASE, *args], capture_output=True, text=True)
    if r.returncode != 0:
        fail(f"helm template {' '.join(args)}:\n{r.stderr}")
    return [d for d in yaml.safe_load_all(r.stdout) if d]


def find(docs, kind, name):
    for d in docs:
        if d.get("kind") == kind and d["metadata"]["name"] == name:
            return d
    fail(f"no {kind} {name}")


def is_kagent_worker_peer(flavor, peer):
    if flavor == "cilium":
        exprs = peer.get("matchExpressions") or []
        return WORKERS in exprs and {"key": "io.kubernetes.pod.namespace", "operator": "In", "values": ["kagent"]} in exprs
    ns = (peer.get("namespaceSelector") or {}).get("matchLabels") or {}
    return ns == {"kubernetes.io/metadata.name": "kagent"} and peer.get("podSelector") == {"matchExpressions": [WORKERS]}


def rule_parts(flavor, direction, rule):
    """(peers, ports) of one rule."""
    if flavor == "cilium":
        peers = rule.get("toEndpoints" if direction == "egress" else "fromEndpoints") or []
        ports = {p["port"] for tp in rule.get("toPorts") or [] for p in tp["ports"]}
    else:
        peers = rule.get("from") or []
        ports = {str(p["port"]) for p in rule.get("ports") or []}
    return peers, ports


def check_hops(flavor, docs):
    expected = CILIUM_ORG_PEER if flavor == "cilium" else K8S_ORG_PEER
    for kind, name, direction, want_ports in HOPS[flavor]:
        rules = find(docs, kind, name)["spec"].get(direction) or []
        found = False
        for rule in rules:
            peers, ports = rule_parts(flavor, direction, rule)
            org_peers = [p for p in peers if ORG in yaml.safe_dump(p)]
            if not org_peers:
                continue
            if not any(is_kagent_worker_peer(flavor, p) for p in peers):
                fail(f"{name}: the organizations' workers are admitted by a rule that does not admit the kagent namespace's workers")
            if org_peers != [expected]:
                fail(f"{name}: the organizations' workers peer is {org_peers}, not the worker pods of every labelled namespace {expected}")
            if ports != want_ports:
                fail(f"{name}: the organizations' workers get ports {sorted(ports)}, not the kagent workers' {sorted(want_ports)}")
            found = True
        if not found:
            fail(f"{name} ({flavor}): the organizations' workers are not admitted; a tenant's first agent never turns Ready")


# The writers of the workers in an Organization's namespace, per kind: an
# Organization's admins may write pods there, so only Substrate's controller
# chain is excepted.
ATE_CONTROLLER = {"kind": "ServiceAccount", "name": "ate-controller", "namespace": "ate-system"}
DEPLOYMENT_CONTROLLER = {"kind": "ServiceAccount", "name": "deployment-controller", "namespace": "kube-system"}
REPLICASET_CONTROLLER = {"kind": "ServiceAccount", "name": "replicaset-controller", "namespace": "kube-system"}
WRITERS = {
    "Deployment": [ATE_CONTROLLER, DEPLOYMENT_CONTROLLER],
    "ReplicaSet": [DEPLOYMENT_CONTROLLER],
    "Pod": [REPLICASET_CONTROLLER, ATE_CONTROLLER],
}


def check_exception(on, off):
    workers_on = find(on, "PolicyException", "substrate-workers")["spec"]
    workers_off = find(off, "PolicyException", "substrate-workers")["spec"]
    if workers_on != workers_off:
        fail("substrate-workers changed with agentPlatformOrganizations on: the kagent namespace's workers keep their exception as it is")
    if any(d["kind"] == "PolicyException" and d["metadata"]["name"] == TENANT_EXCEPTION for d in off):
        fail(f"agentPlatformOrganizations off: {TENANT_EXCEPTION} renders")
    # Its background: false, which Kyverno requires beside subjects, is held by
    # verify-policyexception-userinfo with every other exception of the charts.
    polex = find(on, "PolicyException", TENANT_EXCEPTION)["spec"]
    if polex["exceptions"] != workers_off["exceptions"]:
        fail(f"{TENANT_EXCEPTION} excepts other rules than substrate-workers")
    kagent = workers_off["match"]["any"][0]
    got = {}
    for entry in polex["match"]["any"]:
        res = entry.get("resources") or {}
        if res.get("namespaces") or res.get("namespaceSelector") != {"matchExpressions": [{"key": ORG, "operator": "Exists"}]}:
            fail(f"{TENANT_EXCEPTION}: an entry does not select every labelled namespace by the label: {entry}")
        if res.get("selector") != kagent["resources"]["selector"]:
            fail(f"{TENANT_EXCEPTION}: an entry selects other pods than the kagent workers': {entry}")
        if set(entry) != {"resources", "subjects"}:
            fail(f"{TENANT_EXCEPTION}: an entry is not limited to its writers: {entry}")
        for kind in res.get("kinds") or []:
            if kind in got:
                fail(f"{TENANT_EXCEPTION}: {kind} matched twice in the Organizations' namespaces")
            got[kind] = entry["subjects"]
    if got != WRITERS:
        fail(f"{TENANT_EXCEPTION}: the organizations' workers are excepted for writers {got}, not Substrate's controller chain {WRITERS}; anyone who writes a labelled pod there would get the workers' exception")


# The policies' namespaces: every labelled namespace (the kagent namespace
# carries no organization label; only the operator sets it).
POLICY_NAMESPACES = {"matchExpressions": [{"key": ORG, "operator": "Exists"}]}
RECONCILER = "system:serviceaccount:agent-platform:agent-platform-operator"
WORKERS_POLICY = "agent-platform-organization-workers"
COPY_POLICY = "agent-platform-organization-workerpool-copy"
WORKERS_RULES = [
    (["ate.dev"], ["CREATE", "UPDATE", "DELETE"], ["workerpools"]),
    (["apps"], ["CREATE", "UPDATE"], ["deployments", "replicasets"]),
    ([""], ["CREATE", "UPDATE"], ["pods", "pods/ephemeralcontainers"]),
    ([""], ["CONNECT"], ["pods/exec", "pods/attach"]),
]


def cel_writers(policy):
    """The writers variable of the workers policy spec, a CEL map literal, as a dict."""
    expr = next(v["expression"] for v in policy["variables"] if v["name"] == "writers")
    return yaml.safe_load(expr.replace("'", '"'))


def check_admission(docs, pool="kagent-default"):
    """The admission policies; pool is kagent.substrateWorkerPool.name, the platform WorkerPool."""
    policies = {}
    for name in (WORKERS_POLICY, COPY_POLICY):
        policy = find(docs, "ValidatingAdmissionPolicy", name)["spec"]
        binding = find(docs, "ValidatingAdmissionPolicyBinding", name)["spec"]
        if policy.get("failurePolicy") != "Fail" or binding.get("policyName") != name or binding.get("validationActions") != ["Deny"]:
            fail(f"{name}: the policy does not refuse (failurePolicy Fail, binding Deny)")
        if policy["matchConstraints"].get("namespaceSelector") != POLICY_NAMESPACES or binding.get("matchResources"):
            fail(f"{name}: the policy does not hold every labelled namespace but kagent: {policy['matchConstraints'].get('namespaceSelector')}")
        policies[name] = (policy, binding)
    policy, _ = policies[WORKERS_POLICY]
    rules = [(r["apiGroups"], r["operations"], r["resources"]) for r in policy["matchConstraints"]["resourceRules"]]
    if rules != WORKERS_RULES:
        fail(f"{WORKERS_POLICY}: matches {rules}, not {WORKERS_RULES}")
    want = {f"{kind.lower()}s": [f"system:serviceaccount:{s['namespace']}:{s['name']}" for s in subjects] for kind, subjects in WRITERS.items()}
    if cel_writers(policy) != want:
        fail(f"{WORKERS_POLICY}: the workers' writers are {cel_writers(policy)}, not the PolicyException's {want}")
    if not any(f"variables.writer == '{RECONCILER}'" in v["expression"] for v in policy["validations"]):
        fail(f"{WORKERS_POLICY}: no validation lets {RECONCILER} alone write WorkerPools")
    # A CONNECT request names the pod only: the workers are the pods named after the pool.
    if not any(f"!request.name.startsWith('{pool}-')" in v["expression"] for v in policy["validations"]):
        fail(f"{WORKERS_POLICY}: no validation refuses exec into the workers of the WorkerPool {pool} ({pool}-*)")
    policy, binding = policies[COPY_POLICY]
    if policy.get("paramKind") != {"apiVersion": "ate.dev/v1alpha1", "kind": "WorkerPool"}:
        fail(f"{COPY_POLICY}: the parameter is not a WorkerPool: {policy.get('paramKind')}")
    if binding.get("paramRef") != {"name": pool, "namespace": "kagent", "parameterNotFoundAction": "Deny"}:
        fail(f"{COPY_POLICY}: the parameter is not the platform WorkerPool kagent/{pool}, refused when missing: {binding.get('paramRef')}")
    rules = [(r["apiGroups"], r["operations"], r["resources"]) for r in policy["matchConstraints"]["resourceRules"]]
    if rules != [(["ate.dev"], ["CREATE", "UPDATE"], ["workerpools"])]:
        fail(f"{COPY_POLICY}: matches {rules}, not the WorkerPools' creates and updates")


# The organizations' credential admission (giantswarm/agent-platform#1040)
# selects the label with kagent on, whatever agentPlatformOrganizations says
# (verify-tenant-rbac asserts it).
EGRESS_POLICIES = ("agent-platform-tenant-egress-credentials", "agent-platform-tenant-model-credentials",
                   "agent-platform-tenant-credentials", "agent-platform-tenant-credential-refs")
# A tenant's own ModelConfig (giantswarm/agent-platform#759) selects the label
# with kagent and agentPlatformOrganizations on; verify-tenant-modelconfigs asserts it.
MODEL_POLICIES = {("ValidatingAdmissionPolicy", "agent-platform-tenant-model-routing"),
                  ("ValidatingAdmissionPolicy", "agent-platform-tenant-secret-creation"),
                  ("MutatingAdmissionPolicyBinding", "agent-platform-connectivity-modelconfig-llm-routing-organizations")}


def main():
    chart = sys.argv[1]
    for flavor in ("cilium", "kubernetes"):
        args = ["--set", f"networkPolicy.flavor={flavor}"]
        off = render(chart, args)
        if any(ORG in yaml.safe_dump(d) for d in off if d["metadata"]["name"].startswith("substrate-")):
            fail(f"agentPlatformOrganizations off ({flavor}): a Substrate policy or exception selects {ORG}")
        if any(d["metadata"]["name"] in (WORKERS_POLICY, COPY_POLICY) for d in off):
            fail(f"agentPlatformOrganizations off ({flavor}): the admission policies render")
        on = render(chart, args + ON)
        # The organizations' own role and policies are verify-organization-admission's.
        selecting = {(d["kind"], d["metadata"]["name"]) for d in on if ORG in yaml.safe_dump(d)
                     and (d["metadata"]["name"] in (WORKERS_POLICY, COPY_POLICY) or not d["metadata"]["name"].startswith(
                         ("agent-platform-organization-", "agent-platform-organizations-", "agent-platform:organization")))}
        want = {(kind, name) for kind, name, _, _ in HOPS[flavor]} | {("PolicyException", TENANT_EXCEPTION)} | {
            ("ValidatingAdmissionPolicy", WORKERS_POLICY), ("ValidatingAdmissionPolicy", COPY_POLICY)} | {
            ("ValidatingAdmissionPolicy", n) for n in EGRESS_POLICIES} | MODEL_POLICIES
        if selecting != want:
            fail(f"agentPlatformOrganizations on ({flavor}): {sorted(selecting)} select {ORG}, not exactly {sorted(want)}")
        check_hops(flavor, on)
        check_exception(on, off)
        check_admission(on)
        # A renamed platform WorkerPool (kagent.substrateWorkerPool.name): the
        # copy's parameter and the workers' exec prefix follow it.
        check_admission(render(chart, args + ON + ["--set", "kagent.substrateWorkerPool.name=org-workers"]), pool="org-workers")
        # A pool name the API server would cut from the pods' names fails the render.
        long = "w" * 58
        r = subprocess.run([HELM, "template", "t", chart, *BASE, *args, *ON, "--set", f"kagent.substrateWorkerPool.name={long}"], capture_output=True, text=True)
        if r.returncode == 0 or "longer than 57 characters" not in r.stderr:
            fail(f"a 58-character kagent.substrateWorkerPool.name renders ({flavor}): the exec rule would match no worker")
        check_admission(render(chart, args + ON + ["--set", f"kagent.substrateWorkerPool.name={long[:57]}"]), pool=long[:57])
        print(f"ok: the organizations' workers admitted on the router, the egress gateway, the dns and the PolicyException, written only by Substrate's controllers from agent-platform-operator's copy of the platform WorkerPool, nothing off ({flavor})")


if __name__ == "__main__":
    main()
