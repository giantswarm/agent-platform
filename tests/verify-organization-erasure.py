#!/usr/bin/env python3
"""Assert agent-platform-operator's access for the erasure of deleted organizations
(organizationErasure, giantswarm/giantswarm#38988), in the connectivity chart.

Each case pins one property:
- off by default: no ClusterRole agent-platform:organization-erasure, no
  binding, no IAM role <store role>-operator;
- on, with kagent on: the ClusterRole holds exactly list and delete on
  sessions.api.kagent.dev (kagent reviews ListSessions with all_creators as a
  cluster-wide list, and a delete of another creator's session as a delete),
  bound to the operator's ServiceAccount in the release namespace, or in
  operatorServiceAccount.namespace when set;
- on, with the Crossplane snapshot store on AWS: the IAM role
  <store role>-operator, trusted for that ServiceAccount only, allows
  s3:ListBucket on the bucket only with an s3:prefix that starts with a UID
  segment, and s3:DeleteObject only on keys under a UID segment, nothing else;
  evaluated with IAM's wildcards (`?` one character, `*` any), an
  organization's keys and the ListObjectsV2 prefix the operator sends
  (<uid>/) pass, and the platform Harness's prefix (kagent/), a shorter or
  longer first segment, and the bucket's other keys do not;
- on, with the store off: no IAM role; with kagent off: no ClusterRole.

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

    on = render("-f", STORE, "--set", "organizationErasure.enabled=true")
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

    own = render("-f", STORE, "--set", "organizationErasure.enabled=true",
                 "--set", "organizationErasure.operatorServiceAccount.namespace=apo")
    if find(own, "ClusterRoleBinding", ROLE)["subjects"][0]["namespace"] != "apo":
        fail("operatorServiceAccount.namespace is not the binding's subject namespace")
    ok("operatorServiceAccount.namespace is followed")

    iam = find(on, "Role", IAM_ROLE)
    if not iam or not iam["apiVersion"].startswith("iam.aws.upbound.io/"):
        fail(f"on: no IAM Role {IAM_ROLE}")
    fp = iam["spec"]["forProvider"]
    trust = json.loads(fp["assumeRolePolicy"])["Statement"]
    subs = [s["Condition"]["StringEquals"]["irsa.ci.example.com:sub"] for s in trust]
    if subs != [f"system:serviceaccount:{NAMESPACE}:agent-platform-operator"]:
        fail(f"on: {IAM_ROLE} is trusted for {subs}")
    if find(own, "Role", IAM_ROLE)["spec"]["forProvider"]["assumeRolePolicy"].find("system:serviceaccount:apo:agent-platform-operator") < 0:
        fail("operatorServiceAccount.namespace is not the IAM role's trusted subject")
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

    nostore = render("-f", f"{CHART}/ci/test-substrate-values.yaml", "--set", "organizationErasure.enabled=true")
    if find(nostore, "Role", IAM_ROLE) or any(d.get("kind") == "Role" and d["apiVersion"].startswith("iam.aws") and d["metadata"]["name"].endswith("-operator") for d in nostore):
        fail("on with the snapshot store off: an IAM role renders")
    if not find(nostore, "ClusterRole", ROLE):
        fail("on with the snapshot store off: the sessions grant is gone too")
    ok("on with the snapshot store off: the sessions grant, no IAM role")

    nokagent = render("-f", STORE, "--set", "organizationErasure.enabled=true", "--set", "components.kagent.enabled=false")
    if find(nokagent, "ClusterRole", ROLE) or find(nokagent, "Role", IAM_ROLE):
        fail("on with kagent off: a grant renders")
    ok("on with kagent off: nothing renders")


if __name__ == "__main__":
    main()
