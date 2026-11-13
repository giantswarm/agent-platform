#!/usr/bin/env python3
"""Assert agent-platform-operator's access for the erasure of deleted organizations
(agentPlatformOrganizations.enabled, giantswarm/giantswarm#38988), in the
connectivity chart.

Each case pins one property:
- off by default: no ClusterRole agent-platform:organization-erasure, no
  binding, no IAM role <store role>-operator;
- on, with kagent on: the ClusterRole holds exactly list and delete on
  sessions.api.kagent.dev (kagent reviews ListSessions with all_creators as a
  cluster-wide list, and a delete of another creator's session as a delete),
  bound to the operator's ServiceAccount in the release namespace (the meta
  chart installs the operator there);
- on, with the Crossplane snapshot store on AWS: the IAM role
  <store role>-operator, trusted for that ServiceAccount only, allows
  s3:ListBucket on the bucket only with an s3:prefix that starts with a UID
  segment, and s3:DeleteObject only on keys under a UID segment, nothing else;
  evaluated with IAM's wildcards (`?` one character, `*` any), an
  organization's keys and the ListObjectsV2 prefix the operator sends
  (<uid>/) pass, and the platform Harness's prefix (kagent/), a shorter or
  longer first segment, and the bucket's other keys do not;
- on, the IAM role is the one agentPlatformOrganizations.operator.awsRoleName
  names, and the render fails naming that key without it;
- on, with network policies on: the kagent controller's API port (8083)
  admits the operator's pods (its chart's app.kubernetes.io/name) in the
  operator's namespace,
  in both flavors; none with network policies off;
- on, with the store off: no IAM role; with kagent off the render fails.

The operator's names (its ServiceAccount, pod labels, the RustFS Secret and
the IAM role) are the meta chart's derivation
(agentPlatformOrganizations.operator); these renders take them from
tests/fixtures/organizations-operator-values.yaml, which verify-operator-names
holds equal to it, and where the role name that leaves no room for -operator
in IAM's 64 characters fails the meta chart's render. The meta chart's half,
the operator's ServiceAccount among the kagent controller's machine callers,
is asserted by verify-kagent-extensions.

Usage: verify-organization-erasure.py CONNECTIVITY_CHART
"""

import json
import os
import re
import subprocess
import sys

import yaml

HELM = os.environ.get("HELM", "helm")
CHART = sys.argv[1]
NAMESPACE = "agent-platform"
STORE = f"{CHART}/ci/test-substrate-store-aws-values.yaml"
ROLE = "agent-platform:organization-erasure"
IAM_ROLE = "giantswarm-ci-substrate-operator"
# agentPlatformOrganizations.enabled, with what its other policies need: the
# agents' Flux identity, the API hostname (api.<global.domain>) and the llm
# listener's routing of an organization's ModelConfigs.
# The operator's names: what the meta chart derives onto this release.
OPERATOR_VALUES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "organizations-operator-values.yaml")
ON = ["--set", "agentPlatformOrganizations.enabled=true", "--set", "kagent.fluxServiceAccountName=kagent-flux",
      "--set", "global.domain=ci.example.com", "-f", f"{CHART}/ci/test-llm-routing-values.yaml",
      "--api-versions", "admissionregistration.k8s.io/v1/MutatingAdmissionPolicy", "-f", OPERATOR_VALUES]
BUCKET = "giantswarm-ci-substrate"
UID = "0b3c5a47-7f6e-4c1d-9a8b-2f1e3d4c5b6a"


def fail(msg):
    print(f"FAIL: {msg}")
    sys.exit(1)


def ok(msg):
    print(f"ok: {msg}")


def render(*args):
    out = subprocess.run([HELM, "template", "t", CHART, "-n", NAMESPACE, *args],
                         capture_output=True, text=True)
    if out.returncode != 0:
        fail(f"helm template {' '.join(args)}: {out.stderr}")
    return [d for d in yaml.safe_load_all(out.stdout) if isinstance(d, dict)]


def find(docs, kind, name):
    for d in docs:
        if d.get("kind") == kind and (d.get("metadata") or {}).get("name") == name:
            return d
    return None


def iam_match(pattern, value):
    """IAM's StringLike and resource wildcards: `*` any run, `?` one character."""
    regex = "".join(".*" if c == "*" else "." if c == "?" else re.escape(c) for c in pattern)
    return re.fullmatch(regex, value) is not None


def allowed(statements, action, resource, prefix=None):
    """Whether one Allow statement grants action on resource (with s3:prefix)."""
    for st in statements:
        if st.get("Effect") != "Allow" or action not in st.get("Action", []):
            continue
        if not any(iam_match(r, resource) for r in st.get("Resource", [])):
            continue
        cond = st.get("Condition", {}).get("StringLike", {}).get("s3:prefix")
        if cond is not None and (prefix is None or not any(iam_match(p, prefix) for p in cond)):
            continue
        return True
    return False


def main():
    off = render("-f", STORE)
    for kind, name in (("ClusterRole", ROLE), ("ClusterRoleBinding", ROLE), ("Role", IAM_ROLE)):
        if find(off, kind, name):
            fail(f"off by default: {kind} {name} renders")
    ok("off by default: no grant, no binding, no IAM role")

    on = render("-f", STORE, *ON)
    role = find(on, "ClusterRole", ROLE)
    if not role:
        fail(f"on: no ClusterRole {ROLE}")
    want = [{"apiGroups": ["api.kagent.dev"], "resources": ["sessions"], "verbs": ["list", "delete"]}]
    if role["rules"] != want:
        fail(f"on: {ROLE} rules are {role['rules']}, want {want}")
    binding = find(on, "ClusterRoleBinding", ROLE)
    if not binding or binding["roleRef"]["name"] != ROLE:
        fail(f"on: no ClusterRoleBinding {ROLE} to the role")
    sa = [{"kind": "ServiceAccount", "name": "agent-platform-operator", "namespace": NAMESPACE}]
    if binding["subjects"] != sa:
        fail(f"on: {ROLE} binds {binding['subjects']}, want {sa}")
    ok("on: list and delete on sessions.api.kagent.dev, bound to the operator's ServiceAccount in the release namespace")


    iam = find(on, "Role", IAM_ROLE)
    if not iam or not iam["apiVersion"].startswith("iam.aws.upbound.io/"):
        fail(f"on: no IAM Role {IAM_ROLE}")
    fp = iam["spec"]["forProvider"]
    trust = json.loads(fp["assumeRolePolicy"])["Statement"]
    subs = [s["Condition"]["StringEquals"]["irsa.ci.example.com:sub"] for s in trust]
    if subs != [f"system:serviceaccount:{NAMESPACE}:agent-platform-operator"]:
        fail(f"on: {IAM_ROLE} is trusted for {subs}")
    policies = fp["inlinePolicy"]
    if len(policies) != 1:
        fail(f"on: {IAM_ROLE} has {len(policies)} inline policies")
    statements = json.loads(policies[0]["policy"])["Statement"]
    actions = sorted({a for st in statements for a in st["Action"]})
    if actions != ["s3:DeleteObject", "s3:ListBucket"]:
        fail(f"on: {IAM_ROLE} allows {actions}, want only s3:ListBucket and s3:DeleteObject")
    bucket = f"arn:aws:s3:::{BUCKET}"
    passes = [
        ("s3:ListBucket", bucket, f"{UID}/"),
        ("s3:DeleteObject", f"{bucket}/{UID}/kagent/atespaces/agents-acme-x7k2q/actor/snap", None),
        ("s3:DeleteObject", f"{bucket}/{UID}/agents/triage/snap", None),
    ]
    refusals = [
        ("s3:ListBucket", bucket, None),
        ("s3:ListBucket", bucket, ""),
        ("s3:ListBucket", bucket, "kagent/"),
        ("s3:ListBucket", bucket, f"{UID}x/"),
        ("s3:DeleteObject", f"{bucket}/kagent/atespaces/kagent/actor/snap", None),
        ("s3:DeleteObject", f"{bucket}/{UID[:-1]}/snap", None),
        ("s3:DeleteObject", f"{bucket}/{UID}x/snap", None),
        ("s3:DeleteObject", f"{bucket}/{UID}", None),
        ("s3:DeleteObject", f"arn:aws:s3:::other-bucket/{UID}/snap", None),
        ("s3:GetObject", f"{bucket}/{UID}/snap", None),
        ("s3:PutObject", f"{bucket}/{UID}/snap", None),
    ]
    for action, resource, prefix in passes:
        if not allowed(statements, action, resource, prefix):
            fail(f"on: {IAM_ROLE} refuses {action} {resource} prefix={prefix!r}")
    for action, resource, prefix in refusals:
        if allowed(statements, action, resource, prefix):
            fail(f"on: {IAM_ROLE} allows {action} {resource} prefix={prefix!r}")
    ok(f"on: {IAM_ROLE} lists and deletes under a UID prefix only, trusted for the operator only")

    if not find(render("-f", STORE, *ON, "--set", "agentPlatformOrganizations.operator.awsRoleName=derived-operator"), "Role", "derived-operator"):
        fail("the IAM role does not take the name agentPlatformOrganizations.operator.awsRoleName gives")
    out = subprocess.run([HELM, "template", "t", CHART, "-n", NAMESPACE, "-f", STORE, *ON,
                          "--set", "agentPlatformOrganizations.operator.awsRoleName="], capture_output=True, text=True)
    if out.returncode == 0 or "agentPlatformOrganizations.operator.awsRoleName is required" not in out.stderr:
        fail(f"the AWS store without agentPlatformOrganizations.operator.awsRoleName did not fail the render naming the key: {out.stderr}")
    ok("the IAM role is the one agentPlatformOrganizations.operator.awsRoleName names; the render fails without it")

    policy = "agent-platform-connectivity-operator-to-kagent-controller"
    pods = {"app.kubernetes.io/name": "agent-platform-operator"}
    cilium = find(render("-f", STORE, *ON, "--set", "networkPolicy.enabled=true",
                         "--set", "networkPolicy.flavor=cilium"), "CiliumNetworkPolicy", policy)
    if not cilium:
        fail(f"cilium: no {policy}")
    want_from = [{"matchLabels": {**pods, "io.kubernetes.pod.namespace": NAMESPACE}}]
    if cilium["metadata"]["namespace"] != "kagent" or cilium["spec"]["ingress"][0]["fromEndpoints"] != want_from \
            or cilium["spec"]["ingress"][0]["toPorts"] != [{"ports": [{"port": "8083", "protocol": "TCP"}]}] \
            or cilium["spec"]["endpointSelector"]["matchLabels"] != {"app.kubernetes.io/component": "controller", "app.kubernetes.io/name": "kagent"}:
        fail(f"cilium: {policy} is {cilium['spec']} in {cilium['metadata']['namespace']}")
    k8s = find(render("-f", STORE, *ON, "--set", "networkPolicy.enabled=true",
                      "--set", "networkPolicy.flavor=kubernetes"), "NetworkPolicy", policy)
    if not k8s:
        fail(f"kubernetes: no {policy}")
    want_peer = [{"namespaceSelector": {"matchLabels": {"kubernetes.io/metadata.name": NAMESPACE}}, "podSelector": {"matchLabels": pods}}]
    if k8s["spec"]["ingress"] != [{"from": want_peer, "ports": [{"port": 8083, "protocol": "TCP"}]}]:
        fail(f"kubernetes: {policy} admits {k8s['spec']['ingress']}")
    if find(render("-f", STORE, *ON, "--set", "networkPolicy.enabled=false"), "NetworkPolicy", policy):
        fail("network policies off: the operator's policy renders")
    if find(off, "CiliumNetworkPolicy", policy) or find(off, "NetworkPolicy", policy):
        fail("off by default: the operator's policy renders")
    ok("the kagent controller's API port admits the operator's pods, both flavors; none with network policies off")

    for flavor, kind in (("cilium", "CiliumNetworkPolicy"), ("kubernetes", "NetworkPolicy")):
        for store, selectors, namespace, port, options in (
            ("rustfs", {"app": "rustfs"}, "ate-system", 9000, ["-f", f"{CHART}/ci/test-substrate-values.yaml", "--set", "substrate.rustfs.enabled=true", "--set", "substrate.rustfs.accessKey=proof-access", "--set", "substrate.rustfs.secretKey=proof-secret"]),
            ("s3proxy", {"app.kubernetes.io/name": "s3proxy", "app.kubernetes.io/component": "snapshot-store"}, NAMESPACE, 8080, ["-f", f"{CHART}/ci/test-substrate-store-capz-values.yaml"]),
        ):
            documents = render(*options, *ON, "--set", f"networkPolicy.flavor={flavor}")
            ingress = find(documents, kind, "agent-platform-connectivity-operator-to-snapshot-store")
            egress = find(documents, kind, "agent-platform-connectivity-operator-egress")["spec"]["egress"]
            if not ingress or ingress["metadata"]["namespace"] != namespace:
                fail(f"{store} {flavor}: missing store-side ingress")
            if flavor == "cilium":
                expected_rule = {"toEndpoints": [{"matchLabels": {**selectors, "io.kubernetes.pod.namespace": namespace}}], "toPorts": [{"ports": [{"port": str(port), "protocol": "TCP"}]}]}
                expected_ingress = [{"fromEndpoints": [{"matchLabels": {**pods, "io.kubernetes.pod.namespace": NAMESPACE}}], "toPorts": [{"ports": [{"port": str(port), "protocol": "TCP"}]}]}]
                if ingress["spec"]["endpointSelector"]["matchLabels"] != selectors or any("world" in rule.get("toEntities", []) for rule in egress):
                    fail(f"{store} {flavor}: wrong store selector or world egress")
            else:
                expected_rule = {"to": [{"namespaceSelector": {"matchLabels": {"kubernetes.io/metadata.name": namespace}}, "podSelector": {"matchLabels": selectors}}], "ports": [{"port": port, "protocol": "TCP"}]}
                expected_ingress = [{"from": want_peer, "ports": [{"port": port, "protocol": "TCP"}]}]
                if ingress["spec"]["podSelector"]["matchLabels"] != selectors or any(peer.get("ipBlock", {}).get("cidr") == "0.0.0.0/0" and peer["ipBlock"].get("except") for rule in egress if {"port": 443, "protocol": "TCP"} in rule.get("ports", []) for peer in rule.get("to", [])):
                    fail(f"{store} {flavor}: wrong store selector or world egress")
            if expected_rule not in egress or ingress["spec"]["ingress"] != expected_ingress:
                fail(f"{store} {flavor}: wrong operator egress or store ingress")
            secret_name = "substrate-rustfs-operator" if store == "rustfs" else "substrate-s3proxy"
            secret = find(documents, "Secret", secret_name)
            if not secret or secret["metadata"]["namespace"] != NAMESPACE:
                fail(f"{store}: no credential Secret in the operator namespace")
            if store == "rustfs" and secret.get("stringData") != {"accessKeyId": "proof-access", "secretAccessKey": "proof-secret"}:
                fail("RustFS credential Secret differs from configured store")
            disabled = render(*options, *ON, "--set", "networkPolicy.enabled=false")
            if find(disabled, kind, "agent-platform-connectivity-operator-to-snapshot-store"):
                fail(f"{store}: store ingress renders with policies disabled")
            ok(f"{store} {flavor}: operator and store admit only the selected API port; credentials in operator namespace")

    nostore = render("-f", f"{CHART}/ci/test-substrate-values.yaml", *ON)
    if find(nostore, "Role", IAM_ROLE) or any(d.get("kind") == "Role" and d["apiVersion"].startswith("iam.aws") and d["metadata"]["name"].endswith("-operator") for d in nostore):
        fail("on with the snapshot store off: an IAM role renders")
    if not find(nostore, "ClusterRole", ROLE):
        fail("on with the snapshot store off: the sessions grant is gone too")
    ok("on with the snapshot store off: the sessions grant, no IAM role")

    out = subprocess.run([HELM, "template", "t", CHART, "-n", NAMESPACE, "-f", STORE, *ON, "--set", "components.kagent.enabled=false"],
                         capture_output=True, text=True)
    if out.returncode == 0 or "agentPlatformOrganizations.enabled needs kagent" not in out.stderr:
        fail(f"on with kagent off: the render does not fail naming kagent: {out.stderr}")
    ok("on with kagent off: the render fails naming kagent")


if __name__ == "__main__":
    main()
