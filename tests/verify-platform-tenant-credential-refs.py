#!/usr/bin/env python3
"""Assert agent-platform-platform-tenant-credential-refs on a local API server.

The authorizer's CEL cost allows two checks per expression
(k8s.io/apiserver pkg/cel/library/cost.go). The policy asks it once per
distinct Secret name and refuses more than two distinct names with its own
message. On a local API server (tests/apiserver.py, RBAC on, kagent's Agent,
AgentTemplate and EgressPolicy CRDs at the floor of
components.kagent-crds.versionRange), for an agent-platform:editor of the
kagent namespace:

- an Agent or AgentTemplate whose skills name one Secret the editor may get,
  any number of times, is admitted, as is one naming two distinct Secrets he
  may get;
- one naming a Secret he may not get is refused;
- three distinct Secrets are refused by the limit, for the editor and for a
  cluster-admin, and a cluster-admin's EgressPolicy with three distinct
  secretKeyRefs too (an editor writes no EgressPolicy);
- an Agent written before the policy with six references takes an annotation.

Every refusal names the policy with its own message, never an evaluation
error (which refuses too under failurePolicy Fail and would read as the one
under test).

Needs helm (gsoci.azurecr.io), openssl, PyYAML and github.com on the first
run (the envtest binaries). HELM selects the binary.

Usage: verify-platform-tenant-credential-refs.py META_CHART CONNECTIVITY_CHART
"""
import json
import os
import re
import subprocess
import sys

import yaml

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from apiserver import APIServer  # noqa: E402

HELM = os.environ.get("HELM", "helm")
POLICY = "agent-platform-platform-tenant-credential-refs"
KAGENT = "kagent"
EDITOR = "bob"  # agent-platform:editor in the kagent namespace
READABLE = ["bob-readable-1", "bob-readable-2", "bob-readable-3"]  # Secrets bob may get
PLATFORM_SECRET = "kagent-postgres"  # one he may not
CRDS = ("agents.api.kagent.dev", "agenttemplates.api.kagent.dev", "egresspolicies.api.kagent.dev")

API = None
results = []


def fail(msg):
    sys.exit(f"FAIL: {msg}")


def render(chart):
    r = subprocess.run([HELM, "template", "t", chart, "--namespace", "agent-platform",
                        "--set", "ingress.parentRefs[0].name=x", "--set", "kagent.harness.snapshotLocation=s3://a/b",
                        "--set", "kagent.database.postgres.urlFile=/etc/kagent-db/uri",
                        "--set", "components.kagent.enabled=true", "--set", f"kagent.namespaceOverride={KAGENT}"],
                       capture_output=True, text=True)
    if r.returncode != 0:
        fail(f"the render failed:\n{r.stderr}")
    docs = [d for d in yaml.safe_load_all(r.stdout) if d]
    objs = [d for d in docs if d["metadata"]["name"] == POLICY
            or (d["kind"] == "ClusterRole" and d["metadata"]["name"] == "agent-platform:editor")]
    if sorted(d["kind"] for d in objs) != ["ClusterRole", "ValidatingAdmissionPolicy", "ValidatingAdmissionPolicyBinding"]:
        fail(f"{POLICY}, its binding and agent-platform:editor do not render: {[d['kind'] for d in objs]}")
    return objs


def kagent_crds(meta):
    component = yaml.safe_load(open(os.path.join(meta, "values.yaml")))["components"]["kagent-crds"]
    floor = re.match(r"^>=\s*(\S+)", component["versionRange"]).group(1)
    r = subprocess.run([HELM, "template", "t", f"{component['repository']}/kagent-crds", "--version", floor],
                       capture_output=True, text=True)
    if r.returncode != 0:
        fail(f"helm template kagent-crds {floor}:\n{r.stderr}")
    crds = [d for d in yaml.safe_load_all(r.stdout) if d and d.get("metadata", {}).get("name") in CRDS]
    if len(crds) != len(CRDS):
        fail(f"kagent-crds {floor} carries {[d['metadata']['name'] for d in crds]}, want {CRDS}")
    return crds


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


def create(obj, as_=EDITOR, groups=(), dry=True):
    return kubectl(["create", "-f", "-"] + (["--dry-run=server"] if dry else []), yaml.safe_dump(obj), as_, groups)


def case(name, rc_out, refused=False):
    rc, out = rc_out
    last = out.strip().splitlines()[-1] if out.strip() else ""
    if refused:
        ok = (rc != 0 and f"ValidatingAdmissionPolicy '{POLICY}'" in out and "denied request" in out
              and "resulted in error" not in out and "failed expression" not in out)
    else:
        ok = rc == 0
    results.append((ok, name))
    print(("ok:  " if ok else "FAIL:"), name, "|", last)


def skills(*credentials):
    return [{"name": f"s{i}",
             "source": {"path": f"s{i}", "git": {"url": "https://example.com/x.git", "commit": "0" * 40,
                                                 "credentialRef": {"name": c, "key": "token"}}}}
            for i, c in enumerate(credentials)]


def agent(name, *credentials):
    return {"apiVersion": "api.kagent.dev/v1alpha3", "kind": "Agent", "metadata": {"name": name, "namespace": KAGENT},
            "spec": {"harnessRef": {"name": "kagent"}, "template": {"description": "d", "skills": skills(*credentials)}}}


def agent_template(name, *credentials):
    return {"apiVersion": "api.kagent.dev/v1alpha3", "kind": "AgentTemplate", "metadata": {"name": name, "namespace": KAGENT},
            "spec": {"description": "d", "skills": skills(*credentials)}}


def egress_policy(name, *credentials):
    return {"apiVersion": "api.kagent.dev/v1alpha3", "kind": "EgressPolicy", "metadata": {"name": name, "namespace": KAGENT},
            "spec": {"destinations": [{"origin": f"https://h{i}.example.com",
                                       "authentication": {"header": "Authorization", "secretKeyRef": {"name": c, "key": "token"}}}
                                      for i, c in enumerate(credentials)]}}


def fixtures():
    must(create({"apiVersion": "v1", "kind": "Namespace", "metadata": {"name": KAGENT}}, as_=None, dry=False), "create namespace")
    subject = [{"apiGroup": "rbac.authorization.k8s.io", "kind": "User", "name": EDITOR}]
    apply({"apiVersion": "rbac.authorization.k8s.io/v1", "kind": "RoleBinding", "metadata": {"name": "bob-editor", "namespace": KAGENT},
           "roleRef": {"apiGroup": "rbac.authorization.k8s.io", "kind": "ClusterRole", "name": "agent-platform:editor"}, "subjects": subject})
    apply({"apiVersion": "rbac.authorization.k8s.io/v1", "kind": "Role", "metadata": {"name": "bob-readable", "namespace": KAGENT},
           "rules": [{"apiGroups": [""], "resources": ["secrets"], "verbs": ["get"], "resourceNames": READABLE}]})
    apply({"apiVersion": "rbac.authorization.k8s.io/v1", "kind": "RoleBinding", "metadata": {"name": "bob-readable", "namespace": KAGENT},
           "roleRef": {"apiGroup": "rbac.authorization.k8s.io", "kind": "Role", "name": "bob-readable"}, "subjects": subject})
    # Written before the policy.
    must(create(agent("legacy", *[READABLE[0]] * 6), as_=None, dry=False), "create the legacy Agent")


def install(objs):
    for d in objs:
        apply(d)
    # The API server loads the policy, its binding and the fixtures' RBAC on
    # its own time: wait for all of them (giantswarm/agent-platform#1372).
    API.settle([d for d in objs if d["kind"].endswith(("AdmissionPolicy", "AdmissionPolicyBinding"))])


def prove():
    one = READABLE[0]
    case("an Agent naming one Secret its writer may get once is admitted", create(agent("a", one)))
    case("an Agent naming one Secret its writer may get six times is admitted", create(agent("a", *[one] * 6)))
    case("an AgentTemplate naming one Secret its writer may get six times is admitted", create(agent_template("a", *[one] * 6)))
    case("an Agent naming two distinct Secrets its writer may get, repeated, is admitted",
         create(agent("a", one, READABLE[1], one, READABLE[1])))
    case("an Agent naming a Secret its writer may not get is refused", create(agent("a", one, PLATFORM_SECRET)), refused=True)
    case("an Agent naming three distinct Secrets is refused by the limit", create(agent("a", *READABLE)), refused=True)
    case("a cluster-admin's Agent naming three distinct Secrets is refused by the limit",
         create(agent("a", *READABLE), as_="jose", groups=("system:masters",)), refused=True)
    case("a cluster-admin's EgressPolicy naming three distinct Secrets is refused by the limit",
         create(egress_policy("a", *READABLE), as_="jose", groups=("system:masters",)), refused=True)
    case("a cluster-admin's EgressPolicy naming one Secret three times is admitted",
         create(egress_policy("a", one, one, one), as_="jose", groups=("system:masters",)))
    case("an Agent written before the policy with six references takes an annotation",
         kubectl(["annotate", "agents.api.kagent.dev", "legacy", "-n", KAGENT, "x=y", "--dry-run=server"], as_=EDITOR, groups=()))
    case("an Agent written before the policy with six references to one Secret takes a spec change",
         kubectl(["patch", "agents.api.kagent.dev", "legacy", "-n", KAGENT, "--type=merge", "--dry-run=server",
                  "-p", json.dumps({"spec": {"template": {"description": "changed"}}})], as_=EDITOR, groups=()))


def main():
    global API
    meta, chart = sys.argv[1:3]
    objs = render(chart)
    crds = kagent_crds(meta)
    API = APIServer()
    try:
        for d in crds:
            apply(d)
        must(kubectl(["wait", "--for=condition=Established", "crd", "--all", "--timeout=60s"]), "wait for the CRDs")
        fixtures()
        install(objs)
        prove()
    finally:
        API.stop()
    failed = [n for ok, n in results if not ok]
    print(f"{len(results) - len(failed)}/{len(results)} cases as expected")
    if failed:
        fail("; ".join(failed))


if __name__ == "__main__":
    main()
