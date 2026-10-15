#!/usr/bin/env python3
"""Assert agent-manager's tenant reconciler follows tenants.organizations.enabled (giantswarm/agent-platform#1044).

The meta chart derives agent-manager's tenants block (agent-platform.agentManager.tenants)
from the switch that also admits the Organizations' workers in the connectivity
release (giantswarm/agent-platform#1036):
- off (the default): the agent-manager release carries no tenants key, and its
  values are those of the render with the switch on minus that key — the switch
  changes nothing else, so an agent-manager 1.x chart takes them as before;
- on: tenants.enabled, the kagent namespace's default ModelConfig and its key
  Secret (kagent.providers), the workers' own network policy in the resolved
  flavor (substrate-workers on cilium, substrate-workers-ingress on kubernetes,
  none with networkPolicy.enabled off) and 1 warm worker per tenant (not
  kagent.substrateWorkerPool.replicas);
  the operator's own workerPoolReplicas and interval stand;
- the agent-manager chart of the reconciler's release (TENANTS_FLOOR, pulled
  from the registry) accepts the forwarded values and starts with
  --multi-tenant and the tenant flags;
- the render fails, naming the key, for: agent-manager.tenants.enabled against
  the switch (either way), an agent-manager range below TENANTS_FLOOR, kagent
  off, agent-manager's OAuth or downstream OAuth off, no
  kagent.fluxServiceAccountName, a default provider without apiKeySecretRef,
  and an own tenants value that differs from the derived one.

Needs PyYAML. HELM selects the binary. Network: gsoci.azurecr.io.

Usage: verify-tenant-reconciler.py META_CHART
"""

import os
import subprocess
import sys
import tempfile

import yaml

HELM = os.environ.get("HELM", "helm")
TENANTS_FLOOR = "2.1.0"
AGENT_MANAGER_CHART = "oci://gsoci.azurecr.io/charts/giantswarm/agent-manager"

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
    "--set", "components.agent-manager.enabled=true",
    "--set", f"components.agent-manager.versionRange=>={TENANTS_FLOOR} <3.0.0",
]
CILIUM = ["--api-versions", "cilium.io/v2"]
ON = ["--set", "tenants.organizations.enabled=true"]


def fail(msg: str) -> None:
    sys.exit(f"FAIL: {msg}")


def helm(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([HELM, *args], capture_output=True, text=True, check=False)


def agent_manager(meta: str, flags: list[str]) -> dict:
    r = helm("template", "t", meta, *BASE, *flags)
    if r.returncode != 0:
        fail(f"meta render {' '.join(flags)} failed\n{r.stderr}")
    for d in yaml.safe_load_all(r.stdout):
        if d and d.get("kind") == "HelmRelease" and d["metadata"]["name"] == "agent-manager":
            return d["spec"]["values"]
    fail(f"no agent-manager HelmRelease in the render {' '.join(flags)}")


def expect(what: str, got, want) -> None:
    if got != want:
        fail(f"{what}: got {got!r}, want {want!r}")


def tenants(want_policy):
    block = {
        "enabled": True,
        "modelConfig": {"name": "default-model-config", "apiKeySecretName": "kagent-anthropic"},
        "workerPoolReplicas": 1,
    }
    if want_policy:
        block["networkPolicy"] = want_policy
    return block


def main() -> None:
    meta = sys.argv[1]

    print("--> off: no tenants key; the switch adds that key and changes nothing else")
    off = agent_manager(meta, CILIUM)
    on = agent_manager(meta, [*CILIUM, *ON])
    if "tenants" in off:
        fail(f"off: the agent-manager release carries tenants: {off['tenants']!r}")
    expect("on: agent-manager's values beside tenants", {k: v for k, v in on.items() if k != "tenants"}, off)
    print("ok: off")

    print("--> on: the derived block in both flavors and without network policies")
    expect("on, cilium: tenants", on["tenants"], tenants({"flavor": "cilium", "names": ["substrate-workers"]}))
    expect("on, kubernetes: tenants", agent_manager(meta, ON)["tenants"],
           tenants({"flavor": "kubernetes", "names": ["substrate-workers-ingress"]}))
    expect("on, networkPolicy.enabled=false: tenants",
           agent_manager(meta, [*CILIUM, *ON, "--set", "networkPolicy.enabled=false"])["tenants"], tenants(None))
    own = agent_manager(meta, [*CILIUM, *ON, "--set", "agent-manager.tenants.workerPoolReplicas=3",
                               "--set", "agent-manager.tenants.interval=5m"])["tenants"]
    expect("on: the operator's workerPoolReplicas", own.get("workerPoolReplicas"), 3)
    expect("on: the operator's interval", own.get("interval"), "5m")
    expect("on: an own value equal to the derived one", agent_manager(
        meta, [*CILIUM, *ON, "--set", "agent-manager.tenants.modelConfig.name=default-model-config"])["tenants"],
        on["tenants"])
    print("ok: on")

    print(f"--> the agent-manager chart {TENANTS_FLOOR} takes the forwarded values and starts the reconciler")
    with tempfile.TemporaryDirectory() as tmp:
        r = helm("pull", AGENT_MANAGER_CHART, "--version", TENANTS_FLOOR, "--untar", "--untardir", tmp)
        if r.returncode != 0:
            fail(f"could not pull {AGENT_MANAGER_CHART} {TENANTS_FLOOR}\n{r.stderr}")
        values = os.path.join(tmp, "values.yaml")
        with open(values, "w", encoding="utf-8") as f:
            yaml.safe_dump(on, f)
        r = helm("template", "agent-manager", os.path.join(tmp, "agent-manager"), "-n", "agent-platform",
                 "-f", values, *CILIUM)
        if r.returncode != 0:
            fail(f"agent-manager {TENANTS_FLOOR} rejects the forwarded values\n{r.stderr}")
        for flag in ("--multi-tenant=true", "--tenant-model-config=default-model-config",
                     "--tenant-model-secret=kagent-anthropic", "--tenant-worker-pool-replicas=1",
                     "--tenant-network-policy-flavor=cilium", "--tenant-network-policies=substrate-workers",
                     "--helmrelease-service-account=kagent-flux"):
            if flag not in r.stdout:
                fail(f"agent-manager {TENANTS_FLOOR} with the forwarded values does not start with {flag}")
    print("ok: chart")

    print("--> refusals name what is missing")
    refusals = [
        ("agent-manager.tenants.enabled without the switch", [*CILIUM, "--set", "agent-manager.tenants.enabled=true"],
         "agent-manager.tenants.enabled is true but tenants.organizations.enabled is not"),
        ("agent-manager.tenants.enabled=false with the switch", [*CILIUM, *ON, "--set", "agent-manager.tenants.enabled=false"],
         "agent-manager.tenants.enabled is false but tenants.organizations.enabled is true"),
        ("an agent-manager range below the floor", [*CILIUM, *ON, "--set", "components.agent-manager.versionRange=>=1.13.0 <2.0.0"],
         f"needs agent-manager {TENANTS_FLOOR} or later"),
        ("kagent off", [*CILIUM, *ON, "--set", "components.kagent.enabled=false"],
         "tenants.organizations.enabled with agent-manager on needs components.kagent"),
        ("downstream OAuth off", [*CILIUM, *ON, "--set", "agent-manager.oauth.downstream.enabled=false"],
         "needs agent-manager.oauth.enabled and agent-manager.oauth.downstream.enabled"),
        ("OAuth off", [*CILIUM, *ON, "--set", "agent-manager.oauth.enabled=false"],
         "needs agent-manager.oauth.enabled and agent-manager.oauth.downstream.enabled"),
        ("no Flux ServiceAccount", [*CILIUM, *ON, "--set", "kagent.fluxServiceAccountName="],
         "needs kagent.fluxServiceAccountName"),
        ("no key Secret", [*CILIUM, *ON, "--set", "kagent.providers.anthropic.apiKeySecretRef="],
         "needs kagent.providers.anthropic.apiKeySecretRef"),
        ("an own value that differs", [*CILIUM, *ON, "--set", "agent-manager.tenants.networkPolicy.flavor=kubernetes"],
         "agent-manager.tenants.networkPolicy.flavor (\"kubernetes\") differs"),
    ]
    for what, flags, message in refusals:
        r = helm("template", "t", meta, *BASE, *flags)
        if r.returncode == 0:
            fail(f"refusal, {what}: the render passed")
        if message not in r.stderr:
            fail(f"refusal, {what}: the render failed without naming it ({message!r})\n{r.stderr}")
        print(f"ok: refused: {what}")


if __name__ == "__main__":
    main()
