#!/usr/bin/env python3
"""Prove Substrate's controller and credential providers read Secrets only where
granted (giantswarm/agent-platform#1269), on a local API server.

On a management cluster every workload cluster's admin kubeconfig is a Secret.
Substrate's ate-controller reads one Secret, the egress MITM CA pool in its
own namespace; its credential provider and the Google access-token plugin read
the Secret an actor's credential names, in the namespaces the actor's grants
name. The meta chart derives, from Substrate 1.18.0 and the plugin 0.3.0, the
namespaces their own releases bind their Secret readers in
(credentialProvider.rbac.namespaces, rbac.namespaces: every allowedNamespaces
entry of the derived namespacePolicies), and agent-platform-operator's
serviceAccountBindings, which bind both readers in every organization
namespace (agent-platform-operator 0.1.0-rc.13).

Render checks:
- with agentPlatformOrganizations.enabled, Substrate NARROWED and the plugin
  NARROWED_PLUGIN: the substrate and plugin releases carry the platform
  namespace, the operator release both bindings;
- with the switch off: the platform namespace, no operator;
- below Substrate 1.18.0 or the plugin 0.3.0, the key is not forwarded (the
  plugin's schema refuses it, Substrate's older chart ignores it);
- an operator floor before serviceAccountBindings, or a grant on namespaces
  selected by another label, leaves the readers cluster-wide: no organization
  loses its credentials, and no labelled namespace is left unbound;
- an installation's own rbac.namespaces stays.

A control first: Substrate BEFORE, rendered as release `old` in its own
namespace, grants its controller and provider a workload cluster's kubeconfig,
so the reviews below can tell a grant from a refusal.

Then the Substrate chart NARROWED and the plugin chart NARROWED_PLUGIN are
rendered with the values the meta chart forwards, their RBAC applied to an API
server (tests/apiserver.py) beside the operator's bindings in an organization
namespace as serviceAccountBindings names them, and SubjectAccessReviews
(`kubectl auth can-i`, as each ServiceAccount with its groups) prove:
- ate-controller gets, lists and watches egress-mitm-ca-pool in ate-system and
  is refused any other Secret there, a workload cluster's kubeconfig and a
  list of Secrets across the cluster;
- each credential provider gets a Secret in the platform namespace and in the
  organization namespace and is refused one in ate-system, kube-system and a
  workload cluster's namespace, and a list anywhere.

Needs helm, git, openssl, PyYAML; network: gsoci.azurecr.io, github.com (the
envtest binaries on the first run, and the Substrate chart from the release
tag's tree while its chart is not published). HELM selects the binary.

Usage: verify-secret-readers.py META_CHART
"""
import os
import subprocess
import sys
import tempfile

import yaml

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from apiserver import APIServer  # noqa: E402

HELM = os.environ.get("HELM", "helm")
# The first releases whose charts bind the readers by namespace
# (giantswarm/substrate#553 and #554, giantswarm/substrate-credential-providers#33).
NARROWED = "1.18.0"
NARROWED_PLUGIN = "0.3.0"
OPERATOR = "0.2.0"
# The control: a Substrate whose ate-controller and credential provider read
# every Secret, rendered beside the narrowed one in a namespace of its own.
BEFORE = "1.17.5"
BEFORE_NS = "ate-old"
SUBSTRATE_CHART = "oci://gsoci.azurecr.io/giantswarm/substrate/helm/substrate"
PLUGIN = "google-access-token-credential-provider"
PLUGIN_CHART = f"oci://gsoci.azurecr.io/charts/giantswarm/{PLUGIN}"
ATE = "ate-system"
KAGENT = "kagent"
ORG = "agents-payments-x7k2q"
WC = "org-acme"  # a workload cluster's namespace on a management cluster
KUBECONFIG_SECRET = "acme-kubeconfig"
POOL = "egress-mitm-ca-pool"
READERS = [
    {"clusterRole": "k8s-credential-provider-secret-reader", "serviceAccount": {"name": "k8s-credential-provider", "namespace": ATE}},
    {"clusterRole": f"{PLUGIN}-secret-reader", "serviceAccount": {"name": PLUGIN, "namespace": ATE}},
]

BASE = [
    "--namespace", "agent-platform",
    "--set", "global.domain=example.com",
    "--set", "global.identity.issuerUrl=https://dex.example.com",
    "--set", "global.identity.clientId=agent-platform",
    "--set", "global.identity.existingSecret=agent-platform-idp",
    "--set", "kagent.harness.snapshotLocation=s3://ci-agent-snapshots/agents",
    "--set", "kagent.database.postgres.urlFile=/etc/kagent-db/uri",
    "--set", "postgres.enabled=true",
    "--set", "components.kagent.enabled=true",
]
ON = ["--set", "agentPlatformOrganizations.enabled=true", "--set", "kagent.fluxServiceAccountName=kagent-flux",
      "--set", "llmRouting.enabled=true", "--api-versions", "admissionregistration.k8s.io/v1/MutatingAdmissionPolicy"]
PINS = ["--set", f"components.substrate.versionRange={NARROWED}",
        "--set", f"components.{PLUGIN}.versionRange={NARROWED_PLUGIN}",
        "--set", f"components.agent-platform-operator.versionRange={OPERATOR}"]

failures = []


def fail(msg):
    sys.exit(f"FAIL: {msg}")


def check(ok, what):
    print(("ok:  " if ok else "FAIL:"), what)
    if not ok:
        failures.append(what)


def helm(*args):
    return subprocess.run([HELM, *args], capture_output=True, text=True, check=False)


def releases(meta, *flags):
    r = helm("template", "t", meta, *BASE, *flags)
    if r.returncode != 0:
        fail(f"meta render {' '.join(flags)} failed\n{r.stderr}")
    return {d["metadata"]["name"]: d["spec"].get("values") or {} for d in yaml.safe_load_all(r.stdout)
            if d and d.get("kind") == "HelmRelease"}


def bound(rel):
    """What the meta chart forwards: (substrate's reader namespaces, the
    plugin's, the operator's serviceAccountBindings)."""
    return (((rel.get("substrate") or {}).get("credentialProvider") or {}).get("rbac"),
            (rel.get(PLUGIN) or {}).get("rbac"),
            (rel.get("agent-platform-operator") or {}).get("serviceAccountBindings"))


def render_checks(meta, tmp):
    on = releases(meta, *ON, *PINS)
    check(bound(on) == ({"namespaces": [KAGENT]}, {"namespaces": [KAGENT]}, READERS),
          f"on: substrate and plugin readers in {KAGENT}, the operator binds both in every organization namespace: {bound(on)}")
    off = releases(meta, *PINS)
    check(bound(off) == ({"namespaces": [KAGENT]}, {"namespaces": [KAGENT]}, None),
          f"off: substrate and plugin readers in {KAGENT}, no operator: {bound(off)}")
    old = releases(meta, *ON, "--set", "components.substrate.versionRange=>=1.17.5 <1.18.0",
                   "--set", f"components.{PLUGIN}.versionRange=>=0.2.0 <0.3.0", "--set", f"components.agent-platform-operator.versionRange={OPERATOR}")
    check(bound(old)[:2] == (None, None), f"Substrate before 1.18.0 and the plugin before 0.3.0: nothing forwarded: {bound(old)}")
    early = releases(meta, *ON, *PINS[:4], "--set", "components.agent-platform-operator.versionRange=0.1.0-rc.12")
    check(bound(early) == (None, None, None),
          f"an operator before serviceAccountBindings: the readers stay cluster-wide, no binding forwarded: {bound(early)}")
    path = os.path.join(tmp, "team.yaml")
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump({"substrate": {"credentialProvider": {"namespacePolicies": [
            {"atespace": "team-a", "allowedNamespaces": ["team-a"]},
            {"atespace": "team-b", "allowedNamespaceSelector": {"matchLabels": {"team": "b"}}}]}}}, f)
    labelled = releases(meta, *PINS, "-f", path)
    check(bound(labelled)[:2] == (None, None), f"a grant on namespaces of another label: the readers stay cluster-wide: {bound(labelled)}")
    labelled = releases(meta, *ON, *PINS, "-f", path)
    check(bound(labelled)[:2] == (None, None), f"on, a grant on namespaces of another label: the readers stay cluster-wide: {bound(labelled)}")
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump({"substrate": {"credentialProvider": {"namespacePolicies": [
            {"atespace": "team-b", "allowedNamespaceSelector": {"matchExpressions": [
                {"key": "agent-platform.giantswarm.io/organization", "operator": "Exists"},
                {"key": "team", "operator": "In", "values": ["b"]}]}}]}}}, f)
    narrower = releases(meta, *ON, *PINS, "-f", path)
    check(bound(narrower)[:2] == (None, None), f"on, a selector of the organizations' label and one more: the readers stay cluster-wide: {bound(narrower)}")
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump({"substrate": {"credentialProvider": {"namespacePolicies": [
            {"atespace": "team-a", "allowedNamespaces": [ORG, KAGENT]}]}}}, f)
    org_listed = releases(meta, *ON, *PINS, "-f", path)
    check(bound(org_listed)[0] == {"namespaces": [KAGENT]}, f"on, an own grant of an organization namespace is left to the operator: {bound(org_listed)[0]}")
    # Substrate outside ate-system is refused while the organizations are on
    # (agent-platform.validateOrganizations, giantswarm/agent-platform#1388);
    # the plugin outside ate-system still renders.
    r = helm("template", "t", meta, *BASE, *ON, *PINS, "--set", "components.substrate.targetNamespace=substrate")
    check(r.returncode != 0 and "components.substrate.targetNamespace: Agent Substrate installs into substrate, not ate-system" in r.stderr,
          f"on, Substrate outside ate-system: the render fails naming components.substrate.targetNamespace: {r.stderr.strip()[-300:]}")
    moved = releases(meta, *ON, *PINS, "--set", f"components.{PLUGIN}.targetNamespace=substrate")
    check(bound(moved) == (None, None, None),
          f"the plugin outside ate-system: no binding the admission policy would refuse, the readers stay cluster-wide: {bound(moved)}")
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump({"substrate": {"credentialProvider": {"namespacePolicies": [
            {"atespace": "team-a", "allowedNamespaces": ["team-a", KAGENT]}]}}}, f)
    listed = releases(meta, *ON, *PINS, "-f", path)
    check(bound(listed)[0] == {"namespaces": ["team-a", KAGENT]}, f"an own grant's namespaces are bound too: {bound(listed)[0]}")
    own = releases(meta, *ON, *PINS, "--set", "substrate.credentialProvider.rbac.namespaces={kagent,team-a}")
    check(bound(own)[0] == {"namespaces": [KAGENT, "team-a"]}, f"an own credentialProvider.rbac.namespaces stays: {bound(own)[0]}")
    return on


def pull(chart, version, release, tmp):
    """The pulled chart directory."""
    dest = os.path.join(tmp, release)
    r = helm("pull", chart, "--version", version, "--untar", "--untardir", dest)
    if r.returncode != 0:
        fail(f"helm pull {chart} {version}:\n{r.stderr}")
    return os.path.join(dest, os.path.basename(chart))


def rbac(chart, version, release, values, tmp, namespace=ATE):
    path = os.path.join(tmp, f"{release}-values.yaml")
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(values, f)
    name = os.path.basename(chart)
    r = helm("template", release, pull(chart, version, release, tmp), "-n", namespace, "-f", path)
    if r.returncode != 0:
        fail(f"the {name} chart {version} rejects the forwarded values\n{r.stderr}")
    objs = [d for d in yaml.safe_load_all(r.stdout) if d and d.get("kind") in ("ClusterRole", "ClusterRoleBinding", "Role", "RoleBinding")]
    for d in objs:
        if d["kind"] in ("Role", "RoleBinding"):
            d["metadata"].setdefault("namespace", namespace)
    return objs


def main():
    meta = sys.argv[1]
    with tempfile.TemporaryDirectory() as tmp:
        on = render_checks(meta, tmp)
        # The control: the pinned Substrate line's floor, which binds the
        # reader cluster-wide, as release `old` in its own namespace: the same
        # reviews must be answered yes there, or they prove nothing.
        before = [d for d in rbac(SUBSTRATE_CHART, BEFORE, "old", on["substrate"], tmp, namespace=BEFORE_NS)
                  if d["metadata"]["name"].startswith(("old-ate-controller", "old-k8s-credential-provider"))]
        objs = before + rbac(SUBSTRATE_CHART, NARROWED, "substrate", on["substrate"], tmp) + rbac(PLUGIN_CHART, NARROWED_PLUGIN, PLUGIN, on[PLUGIN], tmp)
        api = APIServer()
        try:
            def kubectl(args, stdin=None, as_=None, groups=("system:masters",)):
                rc, out = api.kubectl(args, stdin=stdin, as_=as_, groups=groups)
                if as_ is None and rc != 0:
                    fail(f"kubectl {' '.join(args)}:\n{out}")
                return rc, out

            namespaces = {ATE: {}, BEFORE_NS: {}, KAGENT: {}, WC: {}, ORG: {"agent-platform.giantswarm.io/organization": "payments"}}
            namespaces.update({d["metadata"]["namespace"]: {} for d in objs if d["metadata"].get("namespace") not in namespaces and d["metadata"].get("namespace")})
            for ns, labels in namespaces.items():
                kubectl(["create", "-f", "-"], yaml.safe_dump({"apiVersion": "v1", "kind": "Namespace", "metadata": {"name": ns, "labels": labels}}))
            # The operator's bindings, as serviceAccountBindings names them: a
            # RoleBinding named after each ClusterRole in the organization namespace.
            for b in on["agent-platform-operator"].get("serviceAccountBindings") or []:
                objs.append({"apiVersion": "rbac.authorization.k8s.io/v1", "kind": "RoleBinding",
                             "metadata": {"name": b["clusterRole"], "namespace": ORG},
                             "roleRef": {"apiGroup": "rbac.authorization.k8s.io", "kind": "ClusterRole", "name": b["clusterRole"]},
                             "subjects": [{"kind": "ServiceAccount", **b["serviceAccount"]}]})
            for d in objs:
                kubectl(["create", "-f", "-"], yaml.safe_dump(d))
            # RBAC authorizes from caches the API server fills on its own
            # time (giantswarm/agent-platform#1291).
            api.settle()

            def can(sa, verb, what, ns, home=ATE):
                user = f"system:serviceaccount:{home}:{sa}"
                groups = ("system:serviceaccounts", f"system:serviceaccounts:{home}", "system:authenticated")
                args = ["auth", "can-i", verb, what] + (["-n", ns] if ns else ["--all-namespaces"])
                rc, out = api.kubectl(args, as_=user, groups=groups)
                if rc not in (0, 1):
                    fail(f"kubectl auth can-i {' '.join(args)} as {user}:\n{out}")
                return rc == 0

            for sa, what, ns in (("old-ate-controller", "secrets/other", BEFORE_NS), ("old-ate-controller", f"secrets/{KUBECONFIG_SECRET}", WC),
                                 ("old-k8s-credential-provider", f"secrets/{KUBECONFIG_SECRET}", WC), ("old-k8s-credential-provider", f"secrets/{POOL}", ATE)):
                check(can(sa, "get", what, ns, home=BEFORE_NS), f"control: Substrate {BEFORE}'s {sa} may get {ns}/{what.split('/')[1]}")
            ctl = "ate-controller"
            for verb in ("get", "list", "watch"):
                check(can(ctl, verb, f"secrets/{POOL}", ATE), f"{ctl} may {verb} {ATE}/{POOL}")
            check(not can(ctl, "get", "secrets/other", ATE), f"{ctl} is refused another Secret of {ATE}")
            check(not can(ctl, "get", f"secrets/{KUBECONFIG_SECRET}", WC), f"{ctl} is refused a workload cluster's kubeconfig ({WC}/{KUBECONFIG_SECRET})")
            check(not can(ctl, "list", "secrets", None), f"{ctl} is refused a list of Secrets across the cluster")
            for b in READERS:
                sa = b["serviceAccount"]["name"]
                for ns in (KAGENT, ORG):
                    check(can(sa, "get", "secrets/model-key", ns), f"{sa} may get a Secret in {ns}")
                for ns, what in ((WC, KUBECONFIG_SECRET), (ATE, POOL), ("kube-system", "model-key")):
                    check(not can(sa, "get", f"secrets/{what}", ns), f"{sa} is refused {ns}/{what}")
                check(not can(sa, "list", "secrets", ORG), f"{sa} is refused a list of Secrets in {ORG}")
                check(not can(sa, "list", "secrets", None), f"{sa} is refused a list of Secrets across the cluster")
        finally:
            api.stop()
    if failures:
        fail(f"{len(failures)} check(s) failed: {'; '.join(failures)}")


if __name__ == "__main__":
    main()
