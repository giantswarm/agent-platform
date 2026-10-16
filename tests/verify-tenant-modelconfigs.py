#!/usr/bin/env python3
"""Assert the render of a tenant's own ModelConfig (giantswarm/agent-platform#759).

What the policies' CEL decides is proven by prove-tenant-modelconfigs.py on a
cluster; this script pins what the connectivity chart renders:

- off (the default), neither agent-platform-tenant-model-routing nor
  agent-platform-tenant-secret-creation renders, and
  agent-platform-tenant-model-credentials lets agent-manager's ServiceAccount
  name the platform's key under any ModelConfig name, as before;
- on, both policies render (Fail, bound with Deny) for every namespace
  labelled giantswarm.io/organization but the kagent namespace: the model
  routing one over modelconfigs.api.kagent.dev of every version, created or
  updated, with each listener provider's block and listener URL (an OpenAI
  model's ends in /v1); the Secret one over Secret creation only, for names
  outside the tenant-credential- prefix, with the authorizer's check; and
  agent-manager's exception in agent-platform-tenant-model-credentials holds
  to the name default-model-config;
- the render fails, naming the key, below Kubernetes 1.30, with llmRouting
  off, and with tenants.organizations.enabled when the default ModelConfig's
  provider is not one the listener serves.

Needs PyYAML. HELM selects the binary.

Usage: verify-tenant-modelconfigs.py CONNECTIVITY_CHART
"""

import os
import subprocess
import sys

import yaml

HELM = os.environ.get("HELM", "helm")
BASE = [
    "--namespace", "agent-platform",
    "--set", "ingress.parentRefs[0].name=x",
    "--set", "kagent.harness.snapshotLocation=s3://ci-agent-snapshots/agents",
    "--set", "kagent.namespaceOverride=kagent",
    "--set", "components.kagent.enabled=true",
    "--set", "components.agent-manager.enabled=true",
    "--set", "global.domain=ci.example.com",
    "--set", "global.identity.issuerUrl=https://dex.ci.example.com",
    "--set", "global.identity.clientId=platform",
    "--set", "global.identity.existingSecret=platform-oauth",
    "--set", "tenants.organizations.enabled=true",
]
ON = ["--set", "llmRouting.enabled=true", "--set", "tenants.organizations.modelConfigs.enabled=true"]
OPENAI = ["--set", "llmRouting.models[0].name=anthropic", "--set", "llmRouting.models[0].provider=Anthropic",
          "--set", "llmRouting.models[0].baseURL=https://api.anthropic.com/v1",
          "--set", "llmRouting.models[1].name=openai", "--set", "llmRouting.models[1].provider=OpenAI",
          "--set", "llmRouting.models[1].baseURL=https://api.openai.com/v1"]
NEW = ["agent-platform-tenant-model-routing", "agent-platform-tenant-secret-creation"]
ORG_NS = [{"key": "giantswarm.io/organization", "operator": "Exists"},
          {"key": "kubernetes.io/metadata.name", "operator": "NotIn", "values": ["kagent"]}]
RECONCILER = "system:serviceaccount:agent-platform:agent-manager"
LISTENER = "http://agentgateway.agent-platform.svc:8081"


def fail(msg: str) -> None:
    sys.exit(f"FAIL: {msg}")


def ok(msg: str) -> None:
    print(f"ok: {msg}")


def helm(chart: str, args: list):
    return subprocess.run([HELM, "template", "t", chart, *BASE, *args], capture_output=True, text=True)


def render(chart: str, args: list) -> list:
    r = helm(chart, args)
    if r.returncode != 0:
        fail(f"helm template {' '.join(args)}:\n{r.stderr}")
    return [d for d in yaml.safe_load_all(r.stdout) if d]


def refused(chart: str, args: list, needles: list, what: str) -> None:
    r = helm(chart, args)
    if r.returncode == 0 or not all(n in r.stderr for n in needles):
        fail(f"{what}: want a failed render naming {needles}, got rc {r.returncode}:\n{r.stderr}")
    ok(what)


def find(docs: list, kind: str, name: str):
    return next((d for d in docs if d.get("kind") == kind and d["metadata"]["name"] == name), None)


def credentials_exception(docs: list) -> str:
    vap = find(docs, "ValidatingAdmissionPolicy", "agent-platform-tenant-model-credentials")
    return vap["spec"]["validations"][0]["expression"]


def check_off(chart: str) -> None:
    docs = render(chart, ["--set", "llmRouting.enabled=true"])
    present = [n for n in NEW if find(docs, "ValidatingAdmissionPolicy", n) or find(docs, "ValidatingAdmissionPolicyBinding", n)]
    if present:
        fail(f"off, the chart renders {present}")
    if RECONCILER not in credentials_exception(docs) or "default-model-config" in credentials_exception(docs):
        fail(f"off, agent-manager's exception changed: {credentials_exception(docs)}")
    ok("off, no tenant ModelConfig policy renders and agent-manager's key exception is unchanged")


def check_on(chart: str) -> None:
    docs = render(chart, ON + OPENAI)
    for name in NEW:
        vap = find(docs, "ValidatingAdmissionPolicy", name)
        vapb = find(docs, "ValidatingAdmissionPolicyBinding", name)
        if vap is None or vapb is None:
            fail(f"on, {name} or its binding is missing")
        if vap["spec"].get("failurePolicy") != "Fail":
            fail(f"{name} failurePolicy is {vap['spec'].get('failurePolicy')}, want Fail")
        if vapb["spec"].get("policyName") != name or vapb["spec"].get("validationActions") != ["Deny"]:
            fail(f"{name}'s binding is {vapb['spec']}, want the policy with Deny")
        if vap["spec"]["matchConstraints"].get("namespaceSelector", {}).get("matchExpressions") != ORG_NS:
            fail(f"{name} selects {vap['spec']['matchConstraints'].get('namespaceSelector')}, want the Organizations' namespaces but kagent")
    routing = find(docs, "ValidatingAdmissionPolicy", NEW[0])["spec"]
    want = [{"apiGroups": ["api.kagent.dev"], "apiVersions": ["*"], "operations": ["CREATE", "UPDATE"], "resources": ["modelconfigs"]}]
    if routing["matchConstraints"]["resourceRules"] != want:
        fail(f"{NEW[0]} matches {routing['matchConstraints']['resourceRules']}, want {want}")
    served = next(v["expression"] for v in routing["variables"] if v["name"] == "served")
    want_served = f"{{'anthropic': ['anthropic', '{LISTENER}'], 'openai': ['openAI', '{LISTENER}/v1']}}"
    if served != want_served:
        fail(f"{NEW[0]} serves {served}, want {want_served}")
    ok(f"{NEW[0]} holds every ModelConfig of the Organizations' namespaces to the listener's providers and URLs")
    secrets = find(docs, "ValidatingAdmissionPolicy", NEW[1])["spec"]
    want = [{"apiGroups": [""], "apiVersions": ["v1"], "operations": ["CREATE"], "resources": ["secrets"]}]
    if secrets["matchConstraints"]["resourceRules"] != want:
        fail(f"{NEW[1]} matches {secrets['matchConstraints']['resourceRules']}, want {want}")
    if "'tenant-credential-'" not in secrets["matchConditions"][0]["expression"] or "authorizer" not in secrets["validations"][0]["expression"]:
        fail(f"{NEW[1]} does not ask the authorizer for Secrets outside the prefix: {secrets}")
    ok(f"{NEW[1]} asks the authorizer on the creation of a Secret outside the tenant-credential- prefix")
    exception = credentials_exception(docs)
    if RECONCILER not in exception or "request.name == 'default-model-config'" not in exception:
        fail(f"on, agent-manager's key exception is not held to default-model-config: {exception}")
    ok("on, only agent-manager's default-model-config names the platform's key")


def main() -> None:
    chart = sys.argv[1]
    check_off(chart)
    check_on(chart)
    refused(chart, ON + ["--kube-version", "1.29.0"], ["tenants.organizations.modelConfigs.enabled", "1.30", "v1.29.0"],
            "below Kubernetes 1.30 the render fails")
    ok_render = helm(chart, ON + ["--kube-version", "1.30.0"])
    if ok_render.returncode != 0:
        fail(f"Kubernetes 1.30 refused:\n{ok_render.stderr}")
    ok("Kubernetes 1.30 renders")
    refused(chart, ["--set", "tenants.organizations.modelConfigs.enabled=true"], ["tenants.organizations.modelConfigs.enabled needs llmRouting.enabled"],
            "with llmRouting off the render fails")
    refused(chart, ON + ["--set", "kagent.providers.default=bedrock", "--set", "kagent.providers.bedrock.provider=Bedrock",
                         "--set", "kagent.providers.bedrock.model=m", "--set", "kagent.providers.bedrock.apiKeySecretRef=k"],
            ["kagent.providers.bedrock.provider", "Bedrock"], "a default ModelConfig the listener does not serve fails the render")


if __name__ == "__main__":
    main()
