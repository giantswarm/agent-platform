#!/usr/bin/env python3
"""Assert the deploy of agent-platform-operator and the organization label
(giantswarm/agent-platform#1268, slice 3 of giantswarm/giantswarm#38891).

The meta chart's render:
- agentPlatformOrganizations.enabled off: no operator release, and kagent's
  authorizer lists a person's agents over agent-platform.giantswarm.io/organization
  (nothing selects giantswarm.io/organization outside the tenants mode);
- on: the agent-platform-operator HelmRelease, in the connectivity release's
  namespace, after it, with its CRD under CreateReplace on install and
  upgrade, with the wiring derived onto it (the
  kagent namespace, the workers' policies of the flavor, the agent chart line,
  the curated copies, the snapshot bucket, the erasure's targets, audiences and
  CA ConfigMap, the Flux namespace, and with the AWS store the IRSA role the
  connectivity chart renders); agent-manager's tenant reconciler off;
  Substrate's credential provider reading
  the organization namespaces by the new label;
- the render fails with tenants.organizations.enabled on as well, with an
  explicit components.agent-platform-operator.enabled or targetNamespace that
  disagrees, with an own derived operator value or kagent namespaceSelector
  that differs.

On a local API server (tests/apiserver.py), with the connectivity chart's
admission policies of the organizations mode applied: a namespace labelled
agent-platform.giantswarm.io/organization is selected by the credential
provider's grants, the workers' PolicyException, the model routing bindings
and the admission policies, and gets the worker admission, the model routing
and the egress credential admission; one labelled only
giantswarm.io/organization gets none of them. Each refusal names its policy
and has an admitted twin.

The egress gateway: its Cilium policy has no kube-apiserver rule and reaches
in-cluster endpoints on 443/10443 only at the pods of muster's public Gateway
(Envoy Gateway's proxy in the edge's namespace, or the chart-owned data
plane); the Kubernetes flavour renders the same egress for the gateway and
the workers, which the API server admits. The render check: with an Envoy pod
on host networking in the edge's namespace, a server-side render fails naming
it; on pod networking it renders.

Needs PyYAML and openssl. HELM selects the binary. Network: gsoci.azurecr.io,
github.com (the envtest binaries, cached in .bin/).

Usage: verify-organization-namespaces.py META_CHART CONNECTIVITY_CHART
"""

import os
import re
import subprocess
import sys
import tempfile
import time

import yaml

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from apiserver import APIServer  # noqa: E402

HELM = os.environ.get("HELM", "helm")
NEW = "agent-platform.giantswarm.io/organization"
OLD = "giantswarm.io/organization"
RELEASE_NS = "agent-platform"
ORG_NS = "agents-payments-x7k2q"
TENANT_NS = "org-acme"
PLAIN_NS = "team-a"
POOL = "kagent-default"
PERSON = "jose"
LISTENER = "http://agentgateway.agent-platform.svc:8081"
MAP_API = ["--api-versions", "admissionregistration.k8s.io/v1/MutatingAdmissionPolicy"]

API = None
results = []


def fail(msg):
    sys.exit(f"FAIL: {msg}")


def run(cmd):
    return subprocess.run(cmd, capture_output=True, text=True)


def docs(r):
    if r.returncode != 0:
        fail(f"the render failed:\n{r.stderr}")
    return [d for d in yaml.safe_load_all(r.stdout) if d]


def find(objs, kind, name):
    for d in objs:
        if d["kind"] == kind and d["metadata"]["name"] == name:
            return d
    return None


def expect(cond, what):
    if not cond:
        fail(what)
    print("ok: ", what)


# ---- the meta chart ----------------------------------------------------------

def meta(chart, *extra):
    return run([HELM, "template", "t", chart, "-n", RELEASE_NS, "-f", f"{chart}/ci/ci-values.yaml",
                "--set", "kagent.fluxServiceAccountName=kagent-flux", "--set", "llmRouting.enabled=true", *MAP_API, *extra])


ON = ["--set", "agentPlatformOrganizations.enabled=true", "--set", "agentPlatformOrganizations.fluxNamespace=flux-giantswarm"]
AWS = ["--set", "kagent.harness.snapshotLocation=", "--set", "kagent.harness.snapshotStore.crossplane.enabled=true",
       "--set", "kagent.harness.snapshotStore.crossplane.provider=aws", "--set", "kagent.harness.snapshotStore.crossplane.providerConfigRef=ci",
       "--set", "kagent.harness.snapshotStore.crossplane.region=eu-west-1",
       "--set", "kagent.harness.snapshotStore.crossplane.aws.bucketName=giantswarm-ci-substrate",
       "--set-string", "kagent.harness.snapshotStore.crossplane.aws.accountId=123456789012",
       "--set", "kagent.harness.snapshotStore.crossplane.aws.oidcProvider=irsa.ci.example.com"]


def release(objs, name):
    hr = find(objs, "HelmRelease", name)
    return hr["spec"] if hr else None


def check_meta(chart):
    off = docs(meta(chart))
    expect(release(off, "agent-platform-operator") is None, "off, no agent-platform-operator release renders")
    authz = release(off, "kagent")["values"]["controller"]["authorization"]
    expect(authz.get("namespaceSelector") == NEW, f"off, kagent lists a person's agents over {NEW}")
    conn = release(off, "agent-platform-connectivity")["values"]
    expect("agent-platform-operator" not in conn["components"], "off, the connectivity roster carries no agent-platform-operator entry")

    on = docs(meta(chart, *ON))
    op = release(on, "agent-platform-operator")
    expect(op is not None, "on, the agent-platform-operator release renders")
    expect(op["install"].get("crds") == "CreateReplace" and op["upgrade"].get("crds") == "CreateReplace",
           "the operator's CRD is created and replaced with the release (crds: CreateReplace on install and upgrade)")
    expect([d["name"] for d in op.get("dependsOn", [])] == ["agent-platform-connectivity"], "the operator release waits for the connectivity release")
    expect(op["targetNamespace"] == release(on, "agent-platform-connectivity")["targetNamespace"],
           "the operator installs into the connectivity release's namespace, where the policies name its ServiceAccount")
    v = op["values"]
    expect(v["platformNamespace"] == "kagent", "the operator copies from the kagent namespace")
    expect(v["networkPolicy"] == {"flavor": "kubernetes", "names": ["substrate-workers-ingress", "substrate-workers-egress"]},
           f"the operator copies the workers' ingress and egress policies of the kubernetes flavor ({v['networkPolicy']})")
    expect(v["curated"] == {"enabled": True, "workerPool": POOL}, "the curated copies are on, from the platform WorkerPool")
    expect(v["snapshots"]["bucket"] == "ci-agent-snapshots", "the organizations' snapshots sit in the Harness snapshot bucket")
    expect(v["helmReleaseServiceAccount"] == "kagent-flux", "an organization's HelmReleases run as kagent.fluxServiceAccountName")
    e = v["erasure"]
    expect(e["fluxNamespace"] == "flux-giantswarm", "the erasure's Flux namespace is agentPlatformOrganizations.fluxNamespace")
    expect(e["kagent"] == {"target": "grpc://kagent-controller.kagent.svc:8083", "audience": "kagent"},
           "the erasure reaches the kagent controller's API Service with kagent's machine audience")
    expect(e["substrate"] == {"target": "grpcs://api.ate-system.svc:443", "audience": "api.ate-system.svc",
                              "caConfigMap": {"name": "agent-platform-operator-ate-api-ca", "key": "ca.crt"}},
           "the erasure reaches ate-api with the audience its authentication config accepts and the CA the bootstrap hook publishes")
    am = release(on, "agent-manager")["values"]
    expect(am.get("tenants") == {"enabled": False}, "agent-manager's tenant reconciler is off while the operator runs")
    kagent = release(on, "kagent")["values"]["controller"]
    env = {x["name"]: x.get("value") for x in kagent.get("env", [])}
    expect(kagent["authorization"]["namespaceSelector"] == NEW and env.get("KAGENT_EXTENSIONS_NAMESPACE_SELECTOR") == NEW,
           f"kagent's authorizer and the extensions binary list a person's agents over {NEW}")
    grants = release(on, "substrate")["values"]["credentialProvider"]["namespacePolicies"]
    sel = {"matchExpressions": [{"key": NEW, "operator": "Exists"}]}
    expect({"ownNamespaceSelector": sel} in grants and any(g.get("atespace") == "ate-golden" and g.get("allowedNamespaceSelector") == sel for g in grants),
           "Substrate's credential provider reads each organization namespace for its own atespace and ate-golden's, by the new label")
    conn = release(on, "agent-platform-connectivity")["values"]
    expect(conn["components"].get("agent-platform-operator") == {"enabled": True} and "agent-platform-operator" not in conn,
           "the connectivity release sees the operator in the roster, never its values block")

    aws = docs(meta(chart, *ON, *AWS))
    arn = release(aws, "agent-platform-operator")["values"]["serviceAccount"]["annotations"]["eks.amazonaws.com/role-arn"]
    roles = [d for d in docs(run([HELM, "template", "t", f"{chart}/../agent-platform-connectivity", "-n", RELEASE_NS, "-f",
                                  f"{chart}/../agent-platform-connectivity/ci/test-substrate-store-aws-values.yaml",
                                  "--set", "agentPlatformOrganizations.enabled=true", "--set", "kagent.fluxServiceAccountName=kagent-flux",
                                  "--set", "global.domain=ci.example.com", "--set", "llmRouting.enabled=true", *MAP_API,
                                  "-f", f"{chart}/../agent-platform-connectivity/ci/test-llm-routing-values.yaml"]))
             if d["kind"] == "Role" and d["apiVersion"].startswith("iam.aws")]
    role_names = {d["metadata"]["name"] for d in roles}
    expect(arn.split("/")[-1] in role_names and arn.startswith("arn:aws:iam::123456789012:role/"),
           f"with the AWS store the operator's ServiceAccount names the IAM role the connectivity chart renders ({arn}; {sorted(role_names)})")
    expect(release(aws, "agent-platform-operator")["values"]["snapshots"] == {"bucket": "giantswarm-ci-substrate", "region": "eu-west-1"},
           "with the AWS store the operator gets the bucket and its region")

    for what, extra, needle in (
            ("tenants.organizations.enabled on as well", ["--set", "tenants.organizations.enabled=true"], "are both on"),
            ("components.agent-platform-operator.enabled false", ["--set", "components.agent-platform-operator.enabled=false"],
             "components.agent-platform-operator.enabled is false but agentPlatformOrganizations.enabled is true"),
            ("another targetNamespace", ["--set", "components.agent-platform-operator.targetNamespace=elsewhere"],
             "components.agent-platform-operator.targetNamespace (elsewhere) differs"),
            ("an own derived operator value", ["--set", "agent-platform-operator.platformNamespace=other"],
             "agent-platform-operator.platformNamespace (\"other\") differs from what the chart derives"),
            ("an own kagent namespaceSelector", ["--set", f"kagent.controller.authorization.namespaceSelector={OLD}"],
             "kagent.controller.authorization.namespaceSelector (giantswarm.io/organization) differs")):
        r = meta(chart, *ON, *extra)
        expect(r.returncode != 0 and needle in r.stderr, f"on with {what}, the render fails naming it")
    r = meta(chart, "--set", "components.agent-platform-operator.enabled=true")
    expect(r.returncode != 0 and "components.agent-platform-operator.enabled is true but agentPlatformOrganizations.enabled is false" in r.stderr,
           "off with components.agent-platform-operator.enabled true, the render fails naming it")


# ---- the connectivity chart ---------------------------------------------------

def connectivity(chart, flavor="kubernetes", *extra):
    return run([HELM, "template", "t", chart, "-n", RELEASE_NS, "-f", f"{chart}/ci/test-substrate-values.yaml",
                "-f", f"{chart}/ci/test-llm-routing-values.yaml", "--set", "agentPlatformOrganizations.enabled=true",
                "--set", "kagent.fluxServiceAccountName=kagent-flux", "--set", "global.domain=ci.example.com",
                "--set", "kagent.namespaceOverride=kagent", "--set", f"networkPolicy.flavor={flavor}",
                "--api-versions", "kyverno.io/v2/PolicyException", *MAP_API, *extra])


def check_default(chart):
    objs = docs(run([HELM, "template", "t", chart, "-n", RELEASE_NS, "-f", f"{chart}/ci/test-llm-routing-values.yaml"]))
    keys = {e["key"] for d in objs if d["kind"] == "ValidatingAdmissionPolicy" and d["metadata"]["name"].startswith("agent-platform-tenant-")
            for e in d["spec"]["matchConstraints"]["namespaceSelector"].get("matchExpressions", [])}
    expect(NEW in keys and OLD not in keys,
           f"with neither mode on, the organizations' egress and credential policies select {NEW}, never {OLD}")


def check_egress(chart):
    for flavor in ("cilium", "kubernetes"):
        objs = docs(connectivity(chart, flavor))
        for d in objs:
            if d["kind"] == "CiliumNetworkPolicy" and d["spec"].get("endpointSelector", {}).get("matchLabels", {}).get("app") == "atenet-egress":
                for rule in d["spec"].get("egress", []):
                    if "kube-apiserver" in rule.get("toEntities", []):
                        fail(f"{d['metadata']['name']} keeps a kube-apiserver egress rule")
                    if "cluster" in rule.get("toEntities", []) and any(p.get("port") in ("443", "10443") for tp in rule.get("toPorts", []) for p in tp["ports"]):
                        fail(f"{d['metadata']['name']} reaches the cluster entity on 443/10443: {rule}")
    objs = docs(connectivity(chart, "cilium"))
    gw = find(objs, "CiliumNetworkPolicy", "substrate-atenet-egress")
    edge = [r for r in gw["spec"]["egress"] if any(p["port"] == "10443" for tp in r.get("toPorts", []) for p in tp["ports"])]
    expect(len(edge) == 1 and edge[0]["toEndpoints"] == [{"matchLabels": {"app.kubernetes.io/name": "envoy", "io.kubernetes.pod.namespace": "envoy-gateway-system"}}],
           "the egress gateway reaches in-cluster 443/10443 only at the Envoy pods of muster's Gateway, and no kube-apiserver")
    own = docs(connectivity(chart, "cilium", "--set", "gatewayApi.gateway.create=true", "--set", "gatewayApi.gateway.tls.secretName=wildcard",
                            "--set", "ingress.parentRefs=null", "--set", "global.gatewayApi.parentRefs=null"))
    edge = [r for r in find(own, "CiliumNetworkPolicy", "substrate-atenet-egress")["spec"]["egress"]
            if any(p["port"] == "10443" for tp in r.get("toPorts", []) for p in tp["ports"])]
    expect(len(edge) == 1 and edge[0]["toEndpoints"] == [{"matchLabels": {"gateway.networking.k8s.io/gateway-name": "agentgateway",
                                                                            "io.kubernetes.pod.namespace": RELEASE_NS}}],
           "with the chart-owned edge, the egress gateway reaches the agentgateway data plane pods instead")
    k8s = docs(connectivity(chart, "kubernetes"))
    for name in ("substrate-atenet-egress-egress", "substrate-workers-egress"):
        np = find(k8s, "NetworkPolicy", name)
        expect(np is not None and np["spec"]["policyTypes"] == ["Egress"], f"the kubernetes flavour renders {name}")
    gw = find(k8s, "NetworkPolicy", "substrate-atenet-egress-egress")
    edge = [r for r in gw["spec"]["egress"] if any(p["port"] == 10443 for p in r.get("ports", []))]
    expect(len(edge) == 1 and edge[0]["to"] == [{"namespaceSelector": {"matchLabels": {"kubernetes.io/metadata.name": "envoy-gateway-system"}},
                                                  "podSelector": {"matchLabels": {"app.kubernetes.io/name": "envoy"}}}],
           "the kubernetes flavour reaches in-cluster 443/10443 only at the Envoy pods of muster's Gateway")
    world = [r for r in gw["spec"]["egress"] if any(t.get("ipBlock", {}).get("cidr") == "0.0.0.0/0" for t in r["to"])]
    excepted = world[0]["to"][0]["ipBlock"]["except"] if world else []
    expect("169.254.0.0/16" in excepted and "168.63.129.16/32" in excepted, "the world rule leaves the metadata endpoints and WireServer out")
    return k8s


# ---- the API server ----------------------------------------------------------

def kubectl(args, stdin=None, as_=None, groups=("system:masters",)):
    return API.kubectl(args, stdin=stdin, as_=as_, groups=groups)


def must(rc_out, what):
    rc, out = rc_out
    if rc != 0:
        fail(f"{what}:\n{out}")
    return out


def create(obj, as_=None, dry=True, extra=()):
    return kubectl(["create", "-f", "-"] + (["--dry-run=server"] if dry else []) + list(extra), yaml.safe_dump(obj), as_)


def case(name, rc_out, by=None):
    """by=None: admitted. by=<policy>: refused by that ValidatingAdmissionPolicy."""
    rc, out = rc_out
    last = out.strip().splitlines()[-1] if out.strip() else ""
    if by is None:
        ok = rc == 0
    else:
        ok = (rc != 0 and f"ValidatingAdmissionPolicy '{by}'" in out and "denied request" in out
              and "resulted in error" not in out and "failed expression" not in out)
    results.append((ok, name))
    print(("ok:  " if ok else "FAIL:"), name, "|", last)


def crds(meta_chart):
    values = yaml.safe_load(open(os.path.join(meta_chart, "values.yaml")))
    out = []
    for name, keep in (("kagent-crds", ("egresspolicies.api.kagent.dev", "modelconfigs.api.kagent.dev")),
                       ("substrate-crds", ("workerpools.ate.dev",))):
        component = values["components"][name]
        floor = re.match(r"^>=\s*(\S+)", component["versionRange"]).group(1)
        r = run([HELM, "template", "t", f"{component['repository']}/{name}", "--version", floor])
        if r.returncode != 0:
            fail(f"helm template {name} {floor}:\n{r.stderr}")
        out += [yaml.safe_dump(d) for d in yaml.safe_load_all(r.stdout) if d and d.get("metadata", {}).get("name") in keep]
    return "\n---\n".join(out)


def selector_string(sel):
    """A label selector as kubectl's -l takes it (matchLabels, In, NotIn, Exists, DoesNotExist)."""
    parts = [f"{k}={v}" for k, v in (sel.get("matchLabels") or {}).items()]
    for e in sel.get("matchExpressions") or []:
        op = e["operator"]
        if op == "Exists":
            parts.append(e["key"])
        elif op == "DoesNotExist":
            parts.append(f"!{e['key']}")
        else:
            parts.append(f"{e['key']} {'in' if op == 'In' else 'notin'} ({','.join(e['values'])})")
    return ",".join(parts)


def selected(sel):
    out = must(kubectl(["get", "namespaces", "-l", selector_string(sel), "-o", "name"]), f"list the namespaces of {sel}")
    return {line.split("/", 1)[1] for line in out.split()}


def mc(name, ns, url):
    spec = {"provider": "OpenAI", "model": "m", "apiKeySecret": "tenant-credential-key", "apiKeySecretKey": "API_KEY"}
    if url:
        spec["openAI"] = {"baseUrl": url}
    return {"apiVersion": "api.kagent.dev/v1alpha3", "kind": "ModelConfig", "metadata": {"name": name, "namespace": ns}, "spec": spec}


def egress_policy(ns, secret):
    return {"apiVersion": "api.kagent.dev/v1alpha3", "kind": "EgressPolicy", "metadata": {"name": "e", "namespace": ns},
            "spec": {"destinations": [{"origin": "https://api.example.com",
                                       "authentication": {"header": "Authorization", "secretKeyRef": {"name": secret, "key": "token"}}}]}}


def workerpool(ns):
    return {"apiVersion": "ate.dev/v1alpha1", "kind": "WorkerPool", "metadata": {"name": POOL, "namespace": ns},
            "spec": {"replicas": 1, "workerImage": "ghcr.io/x/ateom:1", "sandboxClass": "gvisor",
                     "template": {"labels": {"team": "x"}, "serviceAccountName": "workers"}}}


def prove_label(meta_chart, k8s):
    sel = {"matchExpressions": [{"key": NEW, "operator": "Exists"}]}
    grants = release(docs(meta(meta_chart, *ON)), "substrate")["values"]["credentialProvider"]["namespacePolicies"]
    for what, s in (("the credential provider's own-namespace grant", [g for g in grants if "ownNamespaceSelector" in g][0]["ownNamespaceSelector"]),
                    ("ate-golden's credential grant", [g for g in grants if g.get("atespace") == "ate-golden"][0]["allowedNamespaceSelector"])):
        got = selected(s)
        case(f"{what} selects the organization namespace, not the tenant one ({sorted(got)})", (0 if ORG_NS in got and TENANT_NS not in got else 1, ""))
    pe = find(k8s, "PolicyException", "substrate-workers-tenants")
    got = selected(pe["spec"]["match"]["any"][0]["resources"]["namespaceSelector"]) if pe else set()
    case(f"the workers' Kyverno PolicyException selects the organization namespace only ({sorted(got)})",
         (0 if ORG_NS in got and TENANT_NS not in got else 1, ""))
    for d in k8s:
        if d["kind"] == "MutatingAdmissionPolicyBinding" and d["metadata"]["name"].endswith("-organizations"):
            got = selected(d["spec"]["matchResources"]["namespaceSelector"])
            case(f"{d['metadata']['name']} binds the organization namespace only ({sorted(got)})",
                 (0 if got == {ORG_NS} else 1, ""))
    # Admission, organization namespace against Giant Swarm Organization namespace.
    for ns, by in ((ORG_NS, "agent-platform-tenant-model-routing"), (TENANT_NS, None)):
        case(f"a ModelConfig whose baseUrl is not the llm listener in {ns}", create(mc("direct", ns, "https://api.openai.com/v1")), by)
    for ns, by in ((ORG_NS, "agent-platform-tenant-egress-credentials"), (TENANT_NS, None)):
        case(f"an EgressPolicy naming a Secret outside tenant-credential-* in {ns}", create(egress_policy(ns, "platform-key")), by)
    case(f"an EgressPolicy naming a tenant credential in {ORG_NS}", create(egress_policy(ORG_NS, "tenant-credential-key")))
    for ns, by in ((ORG_NS, "agent-platform-organization-workers"), (TENANT_NS, None)):
        case(f"a person writes a WorkerPool in {ns}", create(workerpool(ns), as_=PERSON), by)


def prove_netpol(k8s):
    for name in ("substrate-atenet-egress-egress", "substrate-workers-egress"):
        case(f"the API server admits {name}", create(find(k8s, "NetworkPolicy", name)))


def prove_render_check(chart):
    def envoy(host_network):
        spec = {"containers": [{"name": "envoy", "image": "registry.k8s.io/pause:3.10"}]}
        if host_network:
            spec["hostNetwork"] = True
        return {"apiVersion": "v1", "kind": "Pod", "metadata": {"name": "envoy-edge", "namespace": "envoy-gateway-system",
                                                                "labels": {"app.kubernetes.io/name": "envoy"}}, "spec": spec}
    server = ["--dry-run=server", "--kubeconfig", API.kubeconfig, "--kube-context", "verify"]
    must(create(envoy(True), dry=False), "create an Envoy pod on host networking")
    r = connectivity(chart, "cilium", *server)
    case("a server-side render fails while muster's Gateway runs an Envoy pod on host networking",
         (0 if r.returncode != 0 and "runs with hostNetwork" in r.stderr else 1, r.stderr.strip()[-200:]))
    must(kubectl(["delete", "pod", "envoy-edge", "-n", "envoy-gateway-system", "--grace-period=0", "--force"]), "delete the Envoy pod")
    must(create(envoy(False), dry=False), "create an Envoy pod on pod networking")
    r = connectivity(chart, "cilium", *server)
    # This API server serves none of the chart's custom kinds (Cilium, Gateway
    # API, kagent, Kyverno), so Helm stops after the templates rendered, at
    # building the objects; what matters is that templating passed.
    passed = "execution error" not in r.stderr and "hostNetwork" not in r.stderr
    case("a server-side render gets past the render check while muster's Gateway runs on pod networking",
         (0 if passed else 1, r.stderr.strip()[-200:]))


def main():
    global API
    meta_chart, chart = sys.argv[1:3]
    check_meta(meta_chart)
    check_default(chart)
    k8s = check_egress(chart)
    policies = [d for d in k8s if d["kind"] in ("ValidatingAdmissionPolicy", "ValidatingAdmissionPolicyBinding",
                                                "MutatingAdmissionPolicy", "MutatingAdmissionPolicyBinding")]
    API = APIServer()
    try:
        must(kubectl(["apply", "--server-side", "-f", "-"], crds(meta_chart)), "install the CRDs")
        must(kubectl(["wait", "--for=condition=Established", "crd", "--all", "--timeout=60s"]), "wait for the CRDs")
        for n, labels in ((ORG_NS, {NEW: "payments"}), (TENANT_NS, {OLD: "acme"}), (PLAIN_NS, {}), ("kagent", {}),
                          (RELEASE_NS, {}), ("ate-system", {}), ("envoy-gateway-system", {})):
            must(create({"apiVersion": "v1", "kind": "Namespace", "metadata": {"name": n, "labels": labels}}, dry=False), f"create namespace {n}")
        must(create(workerpool("kagent"), dry=False), "create the platform WorkerPool")
        for d in policies:
            must(kubectl(["apply", "--server-side", "-f", "-"], yaml.safe_dump(d)), f"apply {d['kind']} {d['metadata']['name']}")
        deadline = time.time() + 60
        while time.time() < deadline:
            rc, out = create(mc("probe", ORG_NS, "https://api.openai.com/v1"))
            if rc != 0 and "agent-platform-tenant-model-routing" in out:
                break
            time.sleep(0.5)
        else:
            fail("the API server did not apply the policies within 60s")
        prove_label(meta_chart, k8s)
        prove_netpol(k8s)
        prove_render_check(chart)
    finally:
        API.stop()
    failed = [n for ok, n in results if not ok]
    print(f"{len(results) - len(failed)}/{len(results)} cases as expected")
    if failed:
        fail("; ".join(failed))


if __name__ == "__main__":
    main()
