#!/usr/bin/env python3
"""Assert the deploy of agent-platform-operator and the organization label
(giantswarm/agent-platform#1268, slice 3 of giantswarm/giantswarm#38891).

The meta chart's render:
- agentPlatformOrganizations.enabled off: no operator release, and kagent's
  authorizer lists a person's agents over agent-platform.giantswarm.io/organization
  (nothing selects giantswarm.io/organization);
- on: the agent-platform-operator HelmRelease, in the connectivity release's
  namespace, after it, with its CRD under CreateReplace on install and
  upgrade, with the wiring derived onto it (the
  kagent namespace, the workers' policies of the flavor, the agent chart line,
  the curated copies, the snapshot bucket, the erasure's targets, audiences and
  CA ConfigMap, the Flux namespace, and with the AWS store the IRSA role the
  connectivity chart renders); agent-manager's organization tools on
  (tenants.enabled, with an agent-manager floor of 2.20.0, whose chart leaves
  agent-platform-tenant-flux to the connectivity chart) with devPortal.url from
  the installed portal;
  Substrate's credential provider reading
  the organization namespaces by the new label;
- tenants.organizations is refused by the schema (the tenants mode is gone);
- the render fails with an
  explicit components.agent-platform-operator.enabled or targetNamespace that
  disagrees, with an own derived operator value or kagent namespaceSelector
  that differs.

On a local API server (tests/apiserver.py), with the connectivity chart's
admission policies of the organizations mode applied: a namespace labelled
agent-platform.giantswarm.io/organization is selected by the credential
provider's grants, the workers' PolicyException, the model routing bindings
and the admission policies, and gets the worker admission, the model routing
(the listener admitted, another base URL and an unserved provider refused),
the Secret-creation check and the tenant credential's type, and the egress
credential admission; no one execs into, attaches to or adds an ephemeral
container to a worker there (a Pending pod is enough: admission runs before
the node lookup); the operator writes its namespaced kinds only in
organization namespaces (agent-platform-organization-operator-scope); one labelled only
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

import json
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


def refuses_key(stderr, key):
    """A values-schema refusal of an extra key, in Helm 3's and Helm 4's words."""
    low = stderr.lower()
    return "additional propert" in low and key in low and "not allowed" in low


def expect(cond, what):
    if not cond:
        fail(what)
    print("ok: ", what)


# ---- the meta chart ----------------------------------------------------------

def meta(chart, *extra):
    return run([HELM, "template", "t", chart, "-n", RELEASE_NS, "-f", f"{chart}/ci/ci-values.yaml",
                "--set", "kagent.fluxServiceAccountName=kagent-flux", "--set", "llmRouting.enabled=true", "--set", "global.domain=ci.example.com",
                *MAP_API, *extra])


SWITCH = ["--set", "agentPlatformOrganizations.enabled=true"]
ON = [*SWITCH, "--set", "agentPlatformOrganizations.fluxNamespace=flux-giantswarm", "--set", "agentPlatformOrganizations.fluxSource.name=giantswarm-config"]
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
    operator_peer = {"namespaceSelector": {"matchLabels": {"kubernetes.io/metadata.name": RELEASE_NS}}, "podSelector": {"matchLabels": {"app.kubernetes.io/name": "agent-platform-operator"}}}
    expect(operator_peer in release(on, "kagent")["values"]["controller"]["networkPolicy"]["from"], "kagent admits the operator through controller.networkPolicy.from even with platform policies disabled")
    expect(operator_peer not in release(off, "kagent")["values"]["controller"]["networkPolicy"]["from"], "organizations off: kagent admits no operator")
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
                              "caClusterTrustBundle": {"signerName": "servicedns.podcert.ate.dev/identity",
                                                       "matchLabels": {"podcert.ate.dev/canarying": "live"}}},
           "the erasure reaches ate-api with the audience its authentication config accepts and trusts the ClusterTrustBundles its serving certificate's signer publishes")
    hook = [d for d in docs(connectivity(f"{chart}/../agent-platform-connectivity")) if d["kind"] == "Job" and "substrate-bootstrap" in d["metadata"]["name"]]
    script = "\n".join(arg for d in hook for c in d["spec"]["template"]["spec"]["containers"] for arg in c.get("command", []) + c.get("args", []))
    expect(hook and 'trust_bundle "$pc_ns" service-dns-ca-pool servicedns.podcert.ate.dev/identity' in script
           and "agent-platform-operator-ate-api-ca" not in script,
           "the bootstrap hook publishes the signer's ClusterTrustBundle the operator projects, and copies no CA ConfigMap")
    conn_chart = f"{chart}/../agent-platform-connectivity"
    expect(find(docs(connectivity(conn_chart)), "ClusterRole", "agent-platform-tenant-flux") is not None,
           "on, the connectivity chart renders agent-platform-tenant-flux, which the operator binds")
    expect(find(docs(connectivity(conn_chart, "kubernetes", "--set", "components.agent-manager.enabled=true", "--set", "agent-manager.oauth.enabled=false")),
                "ClusterRole", "agent-platform-tenant-flux") is not None,
           "on with agent-manager on, the connectivity chart still renders agent-platform-tenant-flux: no other release takes it over")
    am = release(on, "agent-manager")["values"]
    expect(am.get("tenants") == {"enabled": True}, "on, agent-manager serves the organization tools (tenants.enabled true)")
    expect("devPortal" not in am, "on with components.backstage off, no devPortal.url is derived")
    expect(release(off, "agent-manager")["values"].get("tenants") == {"enabled": False} and "devPortal" not in release(off, "agent-manager")["values"],
           "off, agent-manager's tenants.enabled is false and no devPortal.url is derived")
    portal = ["--set", "components.backstage.enabled=true"]
    for extra, url in (([], "https://backstage.ci.example.com"),
                       (["--set", "backstage.hostname=portal.example.com"], "https://portal.example.com"),
                       (["--set", "agent-manager.devPortal.url=https://backstage.ci.example.com/"], "https://backstage.ci.example.com")):
        am = release(docs(meta(chart, *ON, *portal, *extra)), "agent-manager")["values"]
        expect(am.get("devPortal") == {"url": url}, f"on with the portal installed ({extra}), agent-manager's devPortal.url is {url}")
    am = release(docs(meta(chart, *ON, "--set", "agent-manager.devPortal.url=https://portal.elsewhere.example.com")), "agent-manager")["values"]
    expect(am.get("devPortal") == {"url": "https://portal.elsewhere.example.com"},
           "on with components.backstage off, an own devPortal.url (a portal installed apart) travels unchanged")
    expect(v["commitMode"] == {"sourceRef": {"kind": "GitRepository", "name": "giantswarm-config"},
                               "path": "./agent-platform/organizations/{parent}/{name}", "decryptionSecret": "sops-keys"},
           f"with fluxNamespace, the operator's commit mode follows fluxSource, fluxPathPrefix and fluxDecryptionSecret ({v.get('commitMode')})")
    own_cm = release(docs(meta(chart, *ON, "--set", "agent-platform-operator.commitMode.interval=5m",
                               "--set", "agent-platform-operator.commitMode.installationKustomization.name=agent-platform",
                               "--set", "agentPlatformOrganizations.fluxSource.kind=OCIRepository",
                               "--set", "agentPlatformOrganizations.fluxPathPrefix=./orgs/",
                               "--set", "agentPlatformOrganizations.fluxDecryptionSecret=")), "agent-platform-operator")["values"]["commitMode"]
    expect(own_cm.get("interval") == "5m" and own_cm.get("installationKustomization", {}).get("name") == "agent-platform"
           and own_cm["sourceRef"] == {"kind": "OCIRepository", "name": "giantswarm-config"}
           and own_cm["path"] == "./orgs/{parent}/{name}" and own_cm["decryptionSecret"] == "",
           f"an own commitMode.interval and installationKustomization travel beside the derived source, path and key ({own_cm})")
    no_flux = release(docs(meta(chart, *SWITCH)), "agent-platform-operator")["values"]
    expect("commitMode" not in no_flux or not no_flux["commitMode"].get("sourceRef", {}).get("name"),
           "without fluxNamespace the operator gets no commit mode")
    org_selector = {"matchExpressions": [{"key": NEW, "operator": "Exists"}]}
    muster = release(on, "muster")["values"]["muster"]
    expect(muster.get("mcpServerNamespaceSelector") == org_selector,
           f"on, muster serves the MCPServers of the namespaces labelled {NEW} (mcpServerNamespaceSelector)")
    expect("mcpServerNamespaceSelector" not in release(off, "muster")["values"].get("muster", {}),
           "off, muster gets no namespace selector and serves its own namespace only")
    own = {"matchLabels": {"team": "a"}}
    muster = release(docs(meta(chart, "--set-json", f"muster.muster.mcpServerNamespaceSelector={json.dumps(own)}")), "muster")["values"]["muster"]
    expect(muster.get("mcpServerNamespaceSelector") == own, "off, an own muster namespace selector travels unchanged")
    muster = release(docs(meta(chart, *ON, "--set-json", f"muster.muster.mcpServerNamespaceSelector={json.dumps(org_selector)}")), "muster")["values"]["muster"]
    expect(muster.get("mcpServerNamespaceSelector") == org_selector, "on, an own muster namespace selector equal to the derived one is a no-op")
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

    moved = docs(meta(chart, *ON, "--set", "components.agent-platform-connectivity.targetNamespace=apo"))
    expect(release(moved, "agent-platform-operator")["targetNamespace"] == "apo",
           "with the connectivity release in another namespace, the operator installs there too")

    aws = docs(meta(chart, *ON, *AWS))
    rustfs = docs(meta(chart, *ON, "--set", "substrate.rustfs.enabled=true", "--set", "substrate.rustfs.accessKey=proof-access", "--set", "substrate.rustfs.secretKey=proof-secret"))
    snapshots = release(rustfs, "agent-platform-operator")["values"]["snapshots"]
    expect(snapshots == {"bucket": "ci-agent-snapshots", "endpoint": "http://rustfs.ate-system.svc:9000", "usePathStyle": True, "region": "us-east-1", "credentials": {"existingSecret": "substrate-rustfs-operator"}}, "RustFS endpoint, path addressing, region and credential reference follow the configured store")
    capz = docs(meta(chart, "-f", f"{chart}/../agent-platform-connectivity/ci/test-substrate-store-capz-values.yaml", *ON, "--set", "kagent.harness.snapshotLocation=", "--set", "muster.muster.oauth.server.enabled=true", "--set", "muster.muster.oauth.server.provider=dex"))
    snapshots = release(capz, "agent-platform-operator")["values"]["snapshots"]
    expect(snapshots["endpoint"] == f"http://substrate-s3proxy.{RELEASE_NS}.svc:80" and snapshots["usePathStyle"] is True and snapshots["region"] == "us-east-1" and snapshots["credentials"] == {"existingSecret": "substrate-s3proxy"}, "Azure s3proxy forwards endpoint, path addressing and Secret reference")
    relocated_capz = docs(meta(chart, "-f", f"{chart}/../agent-platform-connectivity/ci/test-substrate-store-capz-values.yaml", *ON, "--set", "kagent.harness.snapshotLocation=", "--set", "muster.muster.oauth.server.enabled=true", "--set", "muster.muster.oauth.server.provider=dex", "--set", "components.agent-platform-connectivity.targetNamespace=apo"))
    expect(release(relocated_capz, "agent-platform-operator")["values"]["snapshots"]["endpoint"] == "http://substrate-s3proxy.apo.svc:80", "s3proxy endpoint follows the connectivity namespace")
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

    r = meta(chart, "--set", "tenants.organizations.enabled=true")
    expect(r.returncode != 0 and refuses_key(r.stderr, "organizations"),
           "tenants.organizations is refused by the meta chart, naming the key: the tenants mode is gone")
    r = run([HELM, "template", "t", f"{chart}/../agent-platform-connectivity", "--set", "tenants.organizations.enabled=true"])
    expect(r.returncode != 0 and refuses_key(r.stderr, "organizations"),
           "tenants.organizations is refused by the connectivity chart, naming the key")
    r = meta(chart, "--set", "agent-manager.tenants.enabled=true")
    expect(r.returncode != 0 and "agent-manager.tenants.enabled (true) differs from agentPlatformOrganizations.enabled (false)" in r.stderr,
           "off with an own agent-manager.tenants.enabled true, the render fails naming the switch")
    r = meta(chart, "--set", "agent-manager.tenants.organizations.enabled=true")
    expect(r.returncode != 0 and "agent-manager.tenants.organizations is set" in r.stderr,
           "an own agent-manager.tenants key other than enabled fails the render naming it (agent-manager 2.19 refuses it)")
    for what, extra, needle in (
            ("components.agent-platform-operator.enabled false", ["--set", "components.agent-platform-operator.enabled=false"],
             "components.agent-platform-operator.enabled is false but agentPlatformOrganizations.enabled is true"),
            ("another targetNamespace", ["--set", "components.agent-platform-operator.targetNamespace=elsewhere"],
             "components.agent-platform-operator.targetNamespace (elsewhere) differs"),
            ("an agent-manager floor below 2.20.0", ["--set", "components.agent-manager.versionRange=>=2.19.0 <3.0.0"],
             "agentPlatformOrganizations.enabled needs agent-manager 2.20.0 or later: components.agent-manager.versionRange (>=2.19.0 <3.0.0)"),
            ("an own agent-manager.tenants.enabled false", ["--set", "agent-manager.tenants.enabled=false"],
             "agent-manager.tenants.enabled (false) differs from agentPlatformOrganizations.enabled (true)"),
            ("agent-manager's downstream OAuth off", ["--set", "agent-manager.oauth.downstream.enabled=false"],
             "needs agent-manager.oauth.enabled and agent-manager.oauth.downstream.enabled"),
            ("an own devPortal.url other than the installed portal's",
             ["--set", "components.backstage.enabled=true", "--set", "agent-manager.devPortal.url=https://other.example.com"],
             "agent-manager.devPortal.url (https://other.example.com) differs from the Dev Portal this chart installs (https://backstage.ci.example.com"),
            ("an own commit mode path that differs", ["--set", "agent-platform-operator.commitMode.path=./elsewhere/{name}"],
             "agent-platform-operator.commitMode.path (\"./elsewhere/{name}\") differs from what the chart derives"),
            ("an own commit mode source that differs", ["--set", "agent-platform-operator.commitMode.sourceRef.name=other"],
             "agent-platform-operator.commitMode.sourceRef.name (\"other\") differs from what the chart derives"),
            ("fluxNamespace without fluxSource.name", ["--set", "agentPlatformOrganizations.fluxSource.name="],
             "agentPlatformOrganizations.fluxSource.name is required with fluxNamespace"),
            ("an own derived operator value", ["--set", "agent-platform-operator.platformNamespace=other"],
             "agent-platform-operator.platformNamespace (\"other\") differs from what the chart derives"),
            ("a muster floor below 5.58.0", ["--set", "components.muster.versionRange=>=5.57.0 <6.0.0"],
             "agentPlatformOrganizations.enabled needs muster 5.58.0 or later, whose chart takes muster.mcpServerNamespaceSelector"),
            ("an own muster namespace selector that differs", ["--set-json", 'muster.muster.mcpServerNamespaceSelector={"matchLabels":{"team":"a"}}'],
             "muster.muster.mcpServerNamespaceSelector ({\"matchLabels\":{\"team\":\"a\"}}) differs from the one agentPlatformOrganizations.enabled derives"),
            ("muster's OAuth server off", ["--set", "muster.muster.oauth.server.enabled=false"],
             "needs muster.muster.oauth.server.enabled with the provider dex"),
            ("no platform users", ["--set", "tenants.platform.users=null"], "needs tenants.platform.users"),
            ("llmRouting off", ["--set", "llmRouting.enabled=false"], "needs llmRouting.enabled"),
            ("no API hostname", ["--set", "global.domain="], "needs the installation's Kubernetes API hostnames"),
            ("an own kagent namespaceSelector", ["--set", f"kagent.controller.authorization.namespaceSelector={OLD}"],
             "kagent.controller.authorization.namespaceSelector (giantswarm.io/organization) differs")):
        r = meta(chart, *ON, *extra)
        expect(r.returncode != 0 and needle in r.stderr, f"on with {what}, the render fails naming it")
    r = meta(chart, *SWITCH, "--set", "agent-platform-operator.commitMode.sourceRef.name=flux")
    expect(r.returncode != 0 and "agent-platform-operator.commitMode.sourceRef.name is set but agentPlatformOrganizations.fluxNamespace is not" in r.stderr,
           "on without fluxNamespace, an own commit mode source fails the render naming fluxNamespace")
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
           f"with the switch off, the organizations' egress and credential policies select {NEW}, never {OLD}")
    expect(find(objs, "ClusterRole", "agent-platform-tenant-flux") is not None,
           "with the switch off and kagent on, the connectivity chart renders agent-platform-tenant-flux")
    objs = docs(run([HELM, "template", "t", chart, "-n", RELEASE_NS, "-f", f"{chart}/ci/test-llm-routing-values.yaml",
                     "--set", "components.agent-manager.enabled=true", "--set", "agent-manager.oauth.enabled=false"]))
    expect(find(objs, "ClusterRole", "agent-platform-tenant-flux") is not None,
           "with the switch off and agent-manager on, the connectivity chart renders agent-platform-tenant-flux")


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
    def gateway(*extra):
        return find(docs(connectivity(chart, "kubernetes", *extra)), "NetworkPolicy", "substrate-atenet-egress-egress")["spec"]["egress"]

    def blocks(rules):
        return [(t["ipBlock"], r.get("ports")) for r in rules for t in r.get("to", []) if "ipBlock" in t]
    default = blocks(gateway("--set", "networkPolicy.additionalEgressCIDRs[0]=10.10.0.5/32"))
    expect(not any(b["cidr"] == "::/0" for b, _ in default), "by default the egress gateway reaches no IPv6 destination")
    expect(not any(b["cidr"] == "10.10.0.5/32" for b, _ in default),
           "networkPolicy.additionalEgressCIDRs (the components' in-cluster VIPs) never reaches the actors' egress gateway")
    private = blocks(gateway("--set", "networkPolicy.substrate.egressGateway.privateCIDRs[0]=10.20.0.0/16"))
    expect(({"cidr": "10.20.0.0/16"}, None) in private,
           "networkPolicy.substrate.egressGateway.privateCIDRs opens a private destination outside the cluster to the gateway, on every port")
    v6 = blocks(gateway("--set", "networkPolicy.kubernetes.ipv6.enabled=true", "--set", "networkPolicy.kubernetes.ipv6.excludedCIDRs[0]=2600:1f14::/56"))
    expect(({"cidr": "::/0", "except": ["fc00::/7", "fe80::/10", "2600:1f14::/56"]}, None) in v6,
           "with networkPolicy.kubernetes.ipv6 the gateway reaches the IPv6 world but the cluster's own IPv6 CIDRs, ULA and link-local")
    for cidr in ("10.20.0.0/16", "172.16.0.0/12", "192.168.0.0/16"):
        expect(connectivity(chart, "kubernetes", "--set", f"networkPolicy.substrate.egressGateway.privateCIDRs[0]={cidr}").returncode == 0,
               f"privateCIDRs takes the RFC 1918 range {cidr}")
    for cidr, why in (("169.254.169.254/32", "overlaps the instance metadata range"), ("169.0.0.0/8", "overlaps the instance metadata range"),
                      ("168.63.129.16/32", "overlaps Azure's WireServer"), ("128.0.0.0/1", "overlaps")):
        r = connectivity(chart, "kubernetes", "--set", f"networkPolicy.substrate.egressGateway.privateCIDRs[0]={cidr}")
        expect(r.returncode != 0 and why in r.stderr, f"privateCIDRs {cidr} fails the render: it {why}")
    for cidr in ("0.0.0.0/0", "2600:1f14::/56", "10.20.0.0"):
        r = connectivity(chart, "kubernetes", "--set", f"networkPolicy.substrate.egressGateway.privateCIDRs[0]={cidr}")
        expect(r.returncode != 0 and "privateCIDRs" in r.stderr, f"privateCIDRs {cidr} is refused: not an IPv4 CIDR with a prefix of 1 to 32")
    r = connectivity(chart, "kubernetes", "--set", "networkPolicy.kubernetes.ipv6.enabled=true", "--set", "networkPolicy.kubernetes.ipv6.excludedCIDRs[0]=10.0.0.0/8")
    expect(r.returncode != 0 and "excludedCIDRs" in r.stderr, "an IPv4 CIDR in networkPolicy.kubernetes.ipv6.excludedCIDRs is refused at render, not at apply")
    r = connectivity(chart, "kubernetes", "--set", "networkPolicy.kubernetes.ipv6.enabled=true")
    expect(r.returncode != 0 and "networkPolicy.kubernetes.ipv6.enabled needs networkPolicy.kubernetes.ipv6.excludedCIDRs" in r.stderr,
           "networkPolicy.kubernetes.ipv6 without the cluster's IPv6 CIDRs fails the render")
    # No rule of the egress gateway names the cluster entity: a host model
    # server named by hostname reaches the nodes on its port (never on 443 or
    # 10443), and an external OTLP collector is the world rule's.
    mm = ["--set", "components.model-manager.enabled=true", "--set", "model-manager.backend=ollama",
          "--set", "agent-manager.oauth.enabled=false", "--set", "global.identity.clientId=platform",
          "--set", "global.identity.existingSecret=platform-oauth"]
    for endpoint, port in (("https://models.example.com", "443"), ("https://models.example.com:10443", "10443"), ("http://ollama.lan:11434", "11434")):
        egress = find(docs(connectivity(chart, "cilium", *mm, "--set", f"model-manager.ollama.endpoint={endpoint}")),
                      "CiliumNetworkPolicy", "substrate-atenet-egress")["spec"]["egress"]
        entity_rules = [r for r in egress if "toEntities" in r and r["toEntities"] != ["world"]]
        expect(not any("cluster" in r["toEntities"] for r in entity_rules),
               f"a host model server at {endpoint} opens no cluster entity on the egress gateway")
        nodes = [r for r in entity_rules if r["toEntities"] == ["host", "remote-node"]]
        if port in ("443", "10443"):
            expect(not nodes, f"a host model server at {endpoint} opens no node on {port}: in the cluster 443/10443 stay muster's Gateway pods")
        else:
            expect(len(nodes) == 1 and nodes[0]["toPorts"] == [{"ports": [{"port": port, "protocol": "TCP"}]}],
                   f"a host model server at {endpoint} reaches the nodes on {port}, never the pods")
        fqdn = [r for r in egress if "toFQDNs" in r]
        expect(any(r["toFQDNs"] == [{"matchName": endpoint.split("//")[1].split(":")[0]}] and
                   r["toPorts"] == [{"ports": [{"port": port, "protocol": "TCP"}]}] for r in fqdn),
               f"a host model server at {endpoint} is reached by name on {port}")
    otlp = ["--set", "kagent.otel.traces.enabled=true", "--set", "kagent.otel.traces.endpoint=https://otlp.example.com"]
    egress = find(docs(connectivity(chart, "cilium", *otlp)), "CiliumNetworkPolicy", "substrate-atenet-egress")["spec"]["egress"]
    expect(not any("cluster" in r.get("toEntities", []) for r in egress),
           "an external OTLP collector opens no cluster entity on the egress gateway (the world rule reaches it)")
    # The operator's S3 and STS egress follows the storage backend, whoever provisioned it.
    def operator_world(flavor, *extra):
        objs = docs(connectivity(chart, flavor, *extra))
        if flavor == "cilium":
            pol = find(objs, "CiliumNetworkPolicy", "agent-platform-connectivity-operator-egress")
            return any(r.get("toEntities") == ["world"] and r["toPorts"] == [{"ports": [{"port": "443", "protocol": "TCP"}]}] for r in pol["spec"]["egress"])
        pol = find(objs, "NetworkPolicy", "agent-platform-connectivity-operator-egress")
        # The world rule leaves worldExcludedCIDRs out; the API server rule
        # (networkPolicy.kubernetes.apiServerCIDR, 0.0.0.0/0 here) has no except.
        return any(t.get("ipBlock", {}).get("cidr") == "0.0.0.0/0" and t["ipBlock"].get("except")
                   for r in pol["spec"]["egress"] for t in r.get("to", []))
    for flavor in ("cilium", "kubernetes"):
        expect(operator_world(flavor), f"with an existing S3 bucket (kagent.harness.snapshotLocation, no Crossplane), the operator reaches S3 and STS ({flavor})")
        expect(operator_world(flavor, "-f", f"{chart}/ci/test-substrate-store-aws-values.yaml", "--set", "kagent.harness.snapshotLocation="),
               f"with the Crossplane AWS store, the operator reaches S3 and STS ({flavor})")
        expect(not operator_world(flavor, "-f", f"{chart}/ci/test-substrate-store-capz-values.yaml", "--set", "kagent.harness.snapshotLocation="),
               f"behind the in-cluster s3proxy façade, the operator gets no world egress ({flavor})")
    serving = docs(connectivity(chart, "kubernetes", "-f", f"{chart}/ci/test-model-serving-oci-values.yaml"))
    to_model = [d for d in serving if d["kind"] == "NetworkPolicy" and d["metadata"]["name"].endswith("-kagent-agents-to-model-serving")]
    ingress = [d for d in serving if d["kind"] == "NetworkPolicy" and "-model-serving-" in d["metadata"]["name"] and d["metadata"]["name"].endswith("-ingress")]
    ok_rule = bool(to_model and ingress) and to_model[0]["metadata"]["namespace"] == "ate-system" and to_model[0]["spec"]["egress"] == [{
        "to": [{"namespaceSelector": {"matchLabels": {"kubernetes.io/metadata.name": ingress[0]["metadata"]["namespace"]}},
                "podSelector": ingress[0]["spec"]["podSelector"]}],
        "ports": ingress[0]["spec"]["ingress"][0]["ports"]}]
    expect(ok_rule, "with model serving, the egress gateway reaches the model pods on the port their ingress policy admits it on")
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
    for name, keep in (("kagent-crds", ("egresspolicies.api.kagent.dev", "modelconfigs.api.kagent.dev", "agents.api.kagent.dev")),
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
    case(f"a ModelConfig whose baseUrl is the llm listener in {ORG_NS}", create(mc("routed", ORG_NS, f"{LISTENER}/v1")))
    bedrock = {"apiVersion": "api.kagent.dev/v1alpha3", "kind": "ModelConfig", "metadata": {"name": "bedrock", "namespace": ORG_NS},
               "spec": {"provider": "Bedrock", "model": "m", "bedrock": {"region": "eu-west-1"}}}
    case(f"a Bedrock ModelConfig in {ORG_NS} (no listener serves it)", create(bedrock), "agent-platform-tenant-model-routing")
    secret = lambda name, type_="Opaque": {"apiVersion": "v1", "kind": "Secret", "metadata": {"name": name, "namespace": ORG_NS},
                                           "type": type_, "stringData": {"k": "v"}}
    must(create({"apiVersion": "rbac.authorization.k8s.io/v1", "kind": "Role", "metadata": {"name": "secret-writer", "namespace": ORG_NS},
                 "rules": [{"apiGroups": [""], "resources": ["secrets"], "verbs": ["create"]}]}, dry=False), "create the Role secret-writer")
    must(create({"apiVersion": "rbac.authorization.k8s.io/v1", "kind": "RoleBinding", "metadata": {"name": "secret-writer", "namespace": ORG_NS},
                 "roleRef": {"apiGroup": "rbac.authorization.k8s.io", "kind": "Role", "name": "secret-writer"},
                 "subjects": [{"apiGroup": "rbac.authorization.k8s.io", "kind": "User", "name": "writer"}]}, dry=False), "bind writer")
    case("a requester who may only create Secrets writes a Secret outside tenant-credential-*",
         kubectl(["create", "-f", "-", "--dry-run=server"], yaml.safe_dump(secret("platform-key")), "writer", ()), "agent-platform-tenant-secret-creation")
    case("a requester who may only create Secrets writes a tenant credential",
         kubectl(["create", "-f", "-", "--dry-run=server"], yaml.safe_dump(secret("tenant-credential-key")), "writer", ()))
    case("a requester who may read Secrets writes a Secret outside tenant-credential-*", create(secret("helm-release")))
    case("a tenant credential of type kubernetes.io/basic-auth", create(secret("tenant-credential-basic", "kubernetes.io/basic-auth") | {"stringData": {"username": "u", "password": "p"}}),
         "agent-platform-tenant-credentials")
    for ns, by in ((ORG_NS, "agent-platform-tenant-egress-credentials"), (TENANT_NS, None)):
        case(f"an EgressPolicy naming a Secret outside tenant-credential-* in {ns}", create(egress_policy(ns, "platform-key")), by)
    case(f"an EgressPolicy naming a tenant credential in {ORG_NS}", create(egress_policy(ORG_NS, "tenant-credential-key")))
    for ns, by in ((ORG_NS, "agent-platform-organization-workers"), (TENANT_NS, None)):
        case(f"a person writes a WorkerPool in {ns}", create(workerpool(ns), as_=PERSON), by)


def prove_operator_scope():
    op = f"system:serviceaccount:{RELEASE_NS}:agent-platform-operator"
    quota = lambda ns: {"apiVersion": "v1", "kind": "ResourceQuota", "metadata": {"name": "q", "namespace": ns}, "spec": {"hard": {"secrets": "10"}}}
    netpol = lambda ns: {"apiVersion": "networking.k8s.io/v1", "kind": "NetworkPolicy", "metadata": {"name": "probe", "namespace": ns},
                         "spec": {"podSelector": {}, "policyTypes": ["Ingress"]}}
    for what, obj in (("a NetworkPolicy", netpol), ("a ResourceQuota", quota)):
        case(f"the operator writes {what} in an organization namespace", create(obj(ORG_NS), as_=op))
        for ns in ("kube-system", "ate-system", TENANT_NS):
            case(f"the operator writes {what} in {ns}", create(obj(ns), as_=op), "agent-platform-organization-operator-scope")
    case("the operator writes a WorkerPool in the kagent namespace", create(workerpool("kagent"), as_=op), "agent-platform-organization-operator-scope")
    case("a person's NetworkPolicy in kube-system is not the operator's policy's business", create(netpol("kube-system"), as_=PERSON))
    agent = lambda ns: {"apiVersion": "api.kagent.dev/v1alpha3", "kind": "Agent", "metadata": {"name": "probe", "namespace": ns},
                        "spec": {"templateRef": {"name": "t"}, "harnessRef": {"name": "kagent"}}}
    must(create(agent("kagent"), dry=False), "create a platform Agent")
    case("the operator deletes a platform Agent in the kagent namespace",
         kubectl(["delete", "agent", "probe", "-n", "kagent", "--dry-run=server", "--wait=false"], None, op), "agent-platform-organization-operator-scope")
    must(create(agent(ORG_NS), dry=False), "create an organization's Agent")
    case("the operator deletes an organization's Agent", kubectl(["delete", "agent", "probe", "-n", ORG_NS, "--dry-run=server", "--wait=false"], None, op))


def prove_worker_access():
    """CONNECT and the ephemeralcontainers subresource are admitted before the
    API server looks for the pod's node, so a Pending pod is enough."""
    def pod(ns, name, labels=None):
        return {"apiVersion": "v1", "kind": "Pod", "metadata": {"name": name, "namespace": ns, "labels": labels or {}},
                "spec": {"containers": [{"name": "c", "image": "registry.k8s.io/pause:3.10"}]}}
    must(create(pod(ORG_NS, f"{POOL}-abc-x"), dry=False), "create a pod named after the platform WorkerPool")
    must(create(pod(ORG_NS, "other-abc-x"), dry=False), "create another pod")
    for sub in ("exec", "attach"):
        q = "command=true&stdout=true" if sub == "exec" else "stdout=true"
        case(f"{sub} into a worker of an organization namespace",
             kubectl(["create", "--raw", f"/api/v1/namespaces/{ORG_NS}/pods/{POOL}-abc-x/{sub}?{q}", "-f", "-"], ""),
             "agent-platform-organization-workers")
        rc, out = kubectl(["create", "--raw", f"/api/v1/namespaces/{ORG_NS}/pods/other-abc-x/{sub}?{q}", "-f", "-"], "")
        case(f"{sub} into another pod of an organization namespace passes admission (and fails later, at the node)",
             (0 if "agent-platform-organization-workers" not in out else 1, out.strip().splitlines()[-1] if out.strip() else ""))
    # A worker carries ate.dev/worker-pool; the ReplicaSet controller, one of
    # the workers' writers, creates it.
    must(create(pod(ORG_NS, f"{POOL}-abc-w", {"ate.dev/worker-pool": POOL}), as_="system:serviceaccount:kube-system:replicaset-controller", dry=False),
         "create a worker pod as the ReplicaSet controller")
    patch = '{"spec": {"ephemeralContainers": [{"name": "debug", "image": "busybox", "targetContainerName": "c"}]}}'
    case("an ephemeral container in a worker of an organization namespace",
         kubectl(["patch", "pod", f"{POOL}-abc-w", "-n", ORG_NS, "--subresource=ephemeralcontainers", "--type=strategic", "-p", patch, "--dry-run=server"]),
         "agent-platform-organization-workers")


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
    passed = ("execution error" not in r.stderr and "hostNetwork" not in r.stderr
              and (r.returncode == 0 or "ensure CRDs are installed first" in r.stderr))
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
        prove_operator_scope()
        prove_worker_access()
        prove_render_check(chart)
    finally:
        API.stop()
    failed = [n for ok, n in results if not ok]
    print(f"{len(results) - len(failed)}/{len(results)} cases as expected")
    if failed:
        fail("; ".join(failed))


if __name__ == "__main__":
    main()
