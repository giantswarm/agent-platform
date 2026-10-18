#!/usr/bin/env python3
"""Prove what the admission policies of a tenant's own ModelConfig decide, on a real API server (giantswarm/agent-platform#759).

verify-tenant-modelconfigs.py checks what the connectivity chart renders; this
script checks what the policies' CEL decides. It runs against a cluster that
already serves kagent's CRDs (api.kagent.dev ModelConfigs and
RemoteMCPServers) and admissionregistration.k8s.io/v1 MutatingAdmissionPolicy
(agentlab's kind cluster does) and changes nothing there but what it creates
and deletes again:

- it renders the connectivity chart with tenants.organizations.enabled (an
  Anthropic and an OpenAI listener model), takes the policies a tenant's
  model and credentials meet (agent-platform-tenant-model-routing,
  -secret-creation, -model-credentials, -credentials, -credential-refs, and
  the modelconfig-llm-routing MutatingAdmissionPolicies with their
  Organizations' binding), and applies them under a name of this run,
  selecting only a namespace of this run by a label of its own: the
  cluster's own Organization namespaces and policies are not touched, and
  the namespace carries no giantswarm.io/organization label, so the cluster's
  own copies of the policies and agent-manager's reconciler leave it alone.
  The namespace selector itself is the render test's;
- it binds, in that namespace, the rules of the chart's agent-platform:admin
  (a tenant's admin: writes ModelConfigs, creates Secrets, reads none) to an
  impersonated person, the built-in `admin` ClusterRole (reads Secrets) to
  another, and to the reconciler's ServiceAccount user what agent-manager's
  chart grants it there (get on the model key Secret only, create Secrets,
  write ModelConfigs);
- then writes as each of them (server-side dry runs) and checks each answer.

Opt-in, not in CI: needs kubectl, helm and PyYAML. KUBECONFIG and
KUBE_CONTEXT select the cluster (KUBE_CONTEXT is required). HELM selects the
binary.

Usage: prove-tenant-modelconfigs.py CONNECTIVITY_CHART
"""
import json
import os
import subprocess
import sys
import time
import uuid

import yaml

HELM = os.environ.get("HELM", "helm")
CTX = os.environ.get("KUBE_CONTEXT", "")
KCFG = os.environ.get("KUBECONFIG", "")
RUN = uuid.uuid4().hex[:8]
NS = f"prove-tenant-mc-{RUN}"
LABEL = "agent-platform.giantswarm.io/prove-tenant-modelconfigs"
PLATFORM_NS = "prove-platform"
REC = f"system:serviceaccount:{PLATFORM_NS}:agent-manager"
ADMIN = "prove-tenant-admin"
READER = "prove-secret-reader"
KEY = "kagent-anthropic"
ANTHROPIC = f"http://agentgateway.{PLATFORM_NS}.svc:8081"
OPENAI = f"{ANTHROPIC}/v1"
ROUTING = "agent-platform-connectivity-modelconfig-llm-routing"
POLICIES = ["agent-platform-tenant-model-routing", "agent-platform-tenant-secret-creation",
            "agent-platform-tenant-model-credentials", "agent-platform-tenant-credentials",
            "agent-platform-tenant-credential-refs"]
results = []


def kubectl(args, stdin=None, as_=None):
    cmd = ["kubectl", "--context", CTX] + (["--kubeconfig", KCFG] if KCFG else [])
    if as_:
        cmd += [f"--as={as_}"]
    r = subprocess.run(cmd + args, input=stdin, capture_output=True, text=True)
    return r.returncode, r.stdout + r.stderr


def apply(obj, as_=None):
    return kubectl(["create", "--dry-run=server", "-f", "-"], yaml.safe_dump(obj), as_)


def filled(obj, block, url, as_=None):
    """Admitted, with the listener written in as spec.<block>.baseUrl."""
    rc, out = kubectl(["create", "--dry-run=server", "-o", "json", "-f", "-"], yaml.safe_dump(obj), as_)
    if rc != 0:
        return rc, out
    got = json.loads(out).get("spec", {}).get(block, {}).get("baseUrl")
    return (0, f"baseUrl {got}") if got == url else (1, f"admitted with baseUrl {got}, want {url}")


def case(name, rc_out, allowed, needle=None):
    rc, out = rc_out
    last = out.strip().splitlines()[-1] if out.strip() else ""
    ok = (rc == 0) if allowed else (rc != 0 and (needle is None or needle in out))
    results.append((ok, name))
    print(("ok:  " if ok else "FAIL:"), name, "|", last)


def mc(name, provider="Anthropic", block="anthropic", url=ANTHROPIC, secret="tenant-credential-acme-anthropic"):
    spec = {"provider": provider, "model": "m"}
    if secret:
        spec["apiKeySecret"] = secret
        spec["apiKeySecretKey"] = "API_KEY"
    if url is not None:
        spec[block] = {"baseUrl": url}
    return {"apiVersion": "api.kagent.dev/v1alpha3", "kind": "ModelConfig", "metadata": {"name": name, "namespace": NS}, "spec": spec}


def secret(name=None, generate=None, type_="Opaque"):
    md = {"namespace": NS}
    md.update({"name": name} if name else {"generateName": generate})
    return {"apiVersion": "v1", "kind": "Secret", "metadata": md, "type": type_, "stringData": {"API_KEY": "x"}}


def rmcp(secret_name):
    return {"apiVersion": "api.kagent.dev/v1alpha3", "kind": "RemoteMCPServer", "metadata": {"name": "srv", "namespace": NS},
            "spec": {"description": "d", "url": "https://mcp.example.com/mcp",
                     "headersFrom": [{"name": "Authorization", "valueFrom": {"type": "Secret", "name": secret_name, "key": "k"}}]}}


def render(chart):
    r = subprocess.run([HELM, "template", "t", chart, "--namespace", PLATFORM_NS,
                        "--set", "ingress.parentRefs[0].name=x", "--set", "kagent.harness.snapshotLocation=s3://a/b",
                        "--set", "kagent.namespaceOverride=kagent", "--set", "components.kagent.enabled=true",
                        "--set", "components.agent-manager.enabled=true", "--set", "global.domain=ci.example.com",
                        "--set", "global.identity.issuerUrl=https://dex.ci.example.com",
                        "--set", "global.identity.clientId=platform", "--set", "global.identity.existingSecret=platform-oauth",
                        "--set", "llmRouting.enabled=true",
                        "--set", "llmRouting.models[0].name=anthropic", "--set", "llmRouting.models[0].provider=Anthropic",
                        "--set", "llmRouting.models[0].baseURL=https://api.anthropic.com/v1",
                        "--set", "llmRouting.models[1].name=openai", "--set", "llmRouting.models[1].provider=OpenAI",
                        "--set", "llmRouting.models[1].baseURL=https://api.openai.com/v1",
                        "--set", "tenants.organizations.enabled=true",
                        "--api-versions", "admissionregistration.k8s.io/v1/MutatingAdmissionPolicy"], capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit(f"FAIL: helm template:\n{r.stderr}")
    return [d for d in yaml.safe_load_all(r.stdout) if d]


def find(docs, kind, name):
    return next((d for d in docs if d.get("kind") == kind and d["metadata"]["name"] == name), None)


def main():
    if not CTX:
        sys.exit("KUBE_CONTEXT is required: the cluster that serves kagent's CRDs")
    docs = render(sys.argv[1])
    objs = []
    for name in POLICIES:
        vap, vapb = find(docs, "ValidatingAdmissionPolicy", name), find(docs, "ValidatingAdmissionPolicyBinding", name)
        if vap is None or vapb is None:
            sys.exit(f"FAIL: the chart renders no {name}")
        vap["metadata"] = {"name": f"{name}-{RUN}"}
        vap["spec"]["matchConstraints"]["namespaceSelector"] = {"matchLabels": {LABEL: RUN}}
        vapb["metadata"] = {"name": f"{name}-{RUN}"}
        vapb["spec"]["policyName"] = f"{name}-{RUN}"
        objs += [vap, vapb]
    # The routing policies, with only their Organizations' binding.
    for map_ in [d for d in docs if d.get("kind") == "MutatingAdmissionPolicy" and d["metadata"]["name"].startswith(ROUTING)]:
        name = map_["metadata"]["name"]
        binding = find(docs, "MutatingAdmissionPolicyBinding", f"{name}-organizations")
        if binding is None:
            sys.exit(f"FAIL: the chart renders no Organizations' binding for {name}")
        map_["metadata"] = {"name": f"{name}-{RUN}"}
        map_["spec"]["matchConstraints"]["namespaceSelector"] = {"matchLabels": {LABEL: RUN}}
        binding["metadata"] = {"name": f"{name}-organizations-{RUN}"}
        binding["spec"]["policyName"] = f"{name}-{RUN}"
        binding["spec"]["matchResources"]["namespaceSelector"] = {"matchLabels": {LABEL: RUN}}
        objs += [map_, binding]
    if not any(o["kind"] == "MutatingAdmissionPolicy" for o in objs):
        sys.exit("FAIL: the chart renders no modelconfig-llm-routing policy")
    admin = find(docs, "ClusterRole", "agent-platform:admin")
    try:
        prove(objs, admin["rules"])
    finally:
        kubectl(["delete", "namespace", NS, "--wait=false", "--ignore-not-found"])
        for o in objs:
            kubectl(["delete", o["kind"].lower(), o["metadata"]["name"], "--ignore-not-found"])
    failed = [n for ok, n in results if not ok]
    print(f"{len(results) - len(failed)}/{len(results)} cases as expected")
    if failed:
        sys.exit("FAIL: " + "; ".join(failed))


def prove(objs, admin_rules):
    def create(obj):
        rc, out = kubectl(["create", "-f", "-"], yaml.safe_dump(obj))
        if rc != 0:
            sys.exit(f"FAIL: create {obj['kind']} {obj['metadata'].get('name')}:\n{out}")

    create({"apiVersion": "v1", "kind": "Namespace", "metadata": {"name": NS, "labels": {LABEL: RUN}}})
    create({"apiVersion": "rbac.authorization.k8s.io/v1", "kind": "Role", "metadata": {"name": "tenant-admin", "namespace": NS},
            "rules": [r for r in admin_rules if "resourceNames" not in r]})
    create({"apiVersion": "rbac.authorization.k8s.io/v1", "kind": "Role", "metadata": {"name": "reconciler", "namespace": NS},
            "rules": [{"apiGroups": [""], "resources": ["secrets"], "resourceNames": [KEY], "verbs": ["get"]},
                      {"apiGroups": [""], "resources": ["secrets"], "verbs": ["create"]},
                      {"apiGroups": ["api.kagent.dev"], "resources": ["modelconfigs"], "verbs": ["create", "update"]}]})
    for name, kind, role, user in (("tenant-admin", "Role", "tenant-admin", ADMIN), ("reconciler", "Role", "reconciler", REC),
                                   ("reader", "ClusterRole", "admin", READER)):
        create({"apiVersion": "rbac.authorization.k8s.io/v1", "kind": "RoleBinding", "metadata": {"name": name, "namespace": NS},
                "roleRef": {"apiGroup": "rbac.authorization.k8s.io", "kind": kind, "name": role},
                "subjects": [{"apiGroup": "rbac.authorization.k8s.io", "kind": "User", "name": user}]})
    for o in objs:
        create(o)
    # The API server's policy informer: wait for each policy's first refusal,
    # and for the routing policies' first write.
    for probe in (mc("probe", url="https://api.anthropic.com"), secret("probe-plain"), mc("probe", secret="plain"), secret("tenant-credential-p", type_="example.com/token"),
                  rmcp("plain")):
        for _ in range(100):
            if apply(probe, as_=ADMIN)[0] != 0:
                break
            time.sleep(0.3)
    for probe, block, url in ((mc("probe", url=None), "anthropic", ANTHROPIC), (mc("probe", "OpenAI", "openAI", None), "openAI", OPENAI)):
        for _ in range(100):
            if filled(probe, block, url, as_=ADMIN)[0] == 0:
                break
            time.sleep(0.3)
    for o in objs:
        if o["kind"] in ("ValidatingAdmissionPolicy", "MutatingAdmissionPolicy"):
            _, out = kubectl(["get", o["kind"].lower(), o["metadata"]["name"], "-o", "json"])
            tc = json.loads(out).get("status", {}).get("typeChecking", {})
            case(f"{o['metadata']['name'][:-9]}: the API server's type check finds nothing", (1 if tc else 0, json.dumps(tc)), True)

    # A tenant's own ModelConfig (agent-platform-tenant-model-routing).
    case("MC: Anthropic on the listener with a tenant credential", apply(mc("own"), as_=ADMIN), True)
    case("MC: OpenAI on the listener's /v1 with a tenant credential", apply(mc("own-openai", "OpenAI", "openAI", OPENAI), as_=ADMIN), True)
    case("MC: Anthropic without a baseUrl gets the listener", filled(mc("nourl", url=None), "anthropic", ANTHROPIC, as_=ADMIN), True)
    case("MC: OpenAI without a baseUrl gets the listener's /v1",
         filled(mc("nourl-openai", "OpenAI", "openAI", None), "openAI", OPENAI, as_=ADMIN), True)
    case("MC: Anthropic straight to the provider", apply(mc("direct", url="https://api.anthropic.com"), as_=ADMIN), False,
         "(it is https://api.anthropic.com)")
    case("MC: OpenAI on the listener without /v1", apply(mc("openai-root", "OpenAI", "openAI", ANTHROPIC), as_=ADMIN), False,
         f"set spec.openAI.baseUrl to {OPENAI}")
    case("MC: Bedrock", apply(mc("bedrock", "Bedrock", "bedrock", None), as_=ADMIN), False,
         "provider Bedrock is not one of them")
    case("MC: Gemini", apply(mc("gemini", "Gemini", "gemini", None), as_=ADMIN), False, "provider Gemini is not one of them")
    # Its key (agent-platform-tenant-model-credentials).
    case("MC: the listener with the platform's key", apply(mc("platform-key", secret=KEY), as_=ADMIN), False,
         "names only a tenant credential")
    case("MC: the reconciler's default-model-config with the platform's key", apply(mc("default-model-config", secret=KEY), as_=REC), True)
    case("MC: the reconciler's default-model-config without a baseUrl gets the listener",
         filled(mc("default-model-config", url=None, secret=KEY), "anthropic", ANTHROPIC, as_=REC), True)
    case("MC: the reconciler's copy under another name", apply(mc("other", secret=KEY), as_=REC), False, "names only a tenant credential")
    case("MC: the reconciler's default-model-config straight to the provider",
         apply(mc("default-model-config", url="https://api.anthropic.com", secret=KEY), as_=REC), False, ".baseUrl to")
    case("MC: a tenant's default-model-config with the platform's key", apply(mc("default-model-config", secret=KEY), as_=ADMIN), False,
         "names only a tenant credential")

    # Secrets (agent-platform-tenant-secret-creation, agent-platform-tenant-credentials).
    case("Secret: the admin creates an Opaque tenant credential", apply(secret("tenant-credential-acme-anthropic"), as_=ADMIN), True)
    case("Secret: the admin creates a generated tenant credential", apply(secret(generate="tenant-credential-acme-"), as_=ADMIN), True)
    case("Secret: the admin creates a typed tenant credential", apply(secret("tenant-credential-token", type_="example.com/token"), as_=ADMIN),
         False, "is of type Opaque")
    case("Secret: the admin creates a Secret outside the prefix", apply(secret("kagent-anthropic"), as_=ADMIN), False,
         "only a requester who may read its Secrets")
    case("Secret: the admin creates a generated Secret outside the prefix", apply(secret(generate="plain-"), as_=ADMIN), False,
         "Refused for prove-tenant-admin: plain-")
    case("Secret: a reader of the namespace's Secrets creates one outside the prefix", apply(secret("app-config"), as_=READER), True)
    case("Secret: the reconciler creates the model key it may read", apply(secret(KEY), as_=REC), True)
    case("Secret: the reconciler creates another Secret outside the prefix", apply(secret("other"), as_=REC), False,
         "only a requester who may read its Secrets")

    # The other credential references (agent-platform-tenant-credential-refs).
    case("RemoteMCPServer: headersFrom a tenant credential", apply(rmcp("tenant-credential-mcp"), as_=ADMIN), True)
    case("RemoteMCPServer: headersFrom another Secret", apply(rmcp(KEY), as_=ADMIN), False, "may name only tenant credentials")


if __name__ == "__main__":
    main()
