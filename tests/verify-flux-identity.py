#!/usr/bin/env python3
"""Assert the rights of the agents' Flux identity (kagent.fluxServiceAccountName).

helm-controller impersonates this ServiceAccount for every agent HelmRelease in
the kagent namespace, and whoever may write such a HelmRelease picks the chart
it installs. So the identity holds exactly what the Generic agent chart 2.x
needs there and nothing else: Helm's release Secrets and the two kinds the
chart renders (an api.kagent.dev Agent and its RemoteMCPServer). Every binding
of it is a RoleBinding in the kagent namespace to a Role of this chart; no
ClusterRole (cluster-admin included) and no other namespace. The agent-manager
migrate run and its start hook, which need other rights, run as an identity of
their own.

Usage: verify-flux-identity.py <render> <ServiceAccount> <kagent namespace> [--migration]
<render> is a `helm template` output of the connectivity chart; --migration
also asserts the migrate run's identity, which the render then carries: its
one cluster-scoped binding is the ClusterRole <name>-crds, get and delete on
exactly the nine 0.x kagent.dev CRDs and list on their resources, nothing else.
"""

import sys

import yaml

HELM_STORAGE = {("", "secrets", v) for v in ("get", "list", "create", "update", "delete")}
AGENT_CHART = {
    ("api.kagent.dev", r, v)
    for r in ("agents", "remotemcpservers")
    for v in ("get", "list", "watch", "create", "update", "patch", "delete")
}
EXPECTED = HELM_STORAGE | AGENT_CHART
BINDING_KINDS = ("RoleBinding", "ClusterRoleBinding")
LEGACY = ("agents", "sandboxagents", "agentharnesses", "memories", "toolservers", "modelconfigs", "remotemcpservers", "modelproviderconfigs", "mcpservers")
MIGRATE_CLUSTER = {("apiextensions.k8s.io", "customresourcedefinitions", f"{r}.kagent.dev", v) for r in LEGACY for v in ("get", "delete")} | {
    ("kagent.dev", r, "", "list") for r in LEGACY
}


def fail(msg: str) -> None:
    print(f"FAIL: {msg}")
    sys.exit(1)


def names_sa(binding: dict, sa: str, ns: str) -> bool:
    return any(
        s.get("kind") == "ServiceAccount" and s.get("name") == sa and s.get("namespace") == ns
        for s in binding.get("subjects") or []
    )


def flatten(role: dict) -> set:
    out = set()
    for rule in role.get("rules") or []:
        if rule.get("resourceNames") or rule.get("nonResourceURLs"):
            fail(f"Role {role['metadata']['name']}: a rule with resourceNames or nonResourceURLs: {rule}")
        for g in rule.get("apiGroups") or []:
            for r in rule.get("resources") or []:
                for v in rule.get("verbs") or []:
                    out.add((g, r, v))
    return out


def pod_service_account(doc: dict) -> str:
    spec = doc["spec"]
    if doc["kind"] == "CronJob":
        spec = spec["jobTemplate"]["spec"]
    return spec["template"]["spec"].get("serviceAccountName", "default")


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    migration = "--migration" in sys.argv[1:]
    if len(args) != 3:
        fail(__doc__)
    render, sa, ns = args
    docs = [d for d in yaml.safe_load_all(open(render)) if d]

    if not any(d["kind"] == "ServiceAccount" and d["metadata"]["name"] == sa and d["metadata"].get("namespace") == ns for d in docs):
        fail(f"no ServiceAccount {ns}/{sa}")

    bindings = [d for d in docs if d["kind"] in BINDING_KINDS and names_sa(d, sa, ns)]
    if not bindings:
        fail(f"no binding names {ns}/{sa}")
    roles = {(d["metadata"].get("namespace"), d["metadata"]["name"]): d for d in docs if d["kind"] == "Role"}
    granted = set()
    for b in bindings:
        where = f"{b['kind']} {b['metadata'].get('namespace', '')}/{b['metadata']['name']}"
        if b["kind"] != "RoleBinding" or b["metadata"].get("namespace") != ns:
            fail(f"{where} binds {sa} outside a RoleBinding of the {ns} namespace")
        ref = b["roleRef"]
        if ref.get("kind") != "Role":
            fail(f"{where} binds {sa} to the ClusterRole {ref.get('name')}; only a Role of this chart")
        role = roles.get((ns, ref["name"]))
        if role is None:
            fail(f"{where} names the Role {ref['name']}, which this chart does not render in {ns}")
        granted |= flatten(role)

    if granted != EXPECTED:
        missing = sorted(EXPECTED - granted)
        extra = sorted(granted - EXPECTED)
        fail(f"{sa}'s rights in {ns} are not the agent chart's: missing {missing}, extra {extra}")
    print(f"ok: {sa} holds Helm's release Secrets and the agent chart's Agent and RemoteMCPServer in {ns}, nothing else")

    if migration:
        runs = [d for d in docs if d["kind"] == "CronJob" and d["metadata"]["name"].endswith("agent-manager-migrate")]
        starts = [d for d in docs if d["kind"] == "Job" and d["metadata"]["name"].endswith("agent-manager-migrate-start")]
        if not runs or not starts:
            fail("the render carries no migrate CronJob or start hook")
        for d in runs + starts:
            if pod_service_account(d) == sa:
                fail(f"{d['kind']} {d['metadata']['name']} runs as {sa}; the migration has an identity of its own")
        print(f"ok: the migrate run and its start hook do not run as {sa}")
        migrate_sa = pod_service_account(runs[0])
        cluster = [d for d in docs if d["kind"] == "ClusterRoleBinding" and names_sa(d, migrate_sa, ns)]
        if len(cluster) != 1:
            fail(f"{migrate_sa} has {len(cluster)} ClusterRoleBindings; exactly one, to its -crds ClusterRole")
        ref = cluster[0]["roleRef"]
        role = next((d for d in docs if d["kind"] == "ClusterRole" and d["metadata"]["name"] == ref.get("name")), None)
        if ref.get("kind") != "ClusterRole" or role is None or not ref["name"].endswith("-crds"):
            fail(f"{migrate_sa}'s ClusterRoleBinding names {ref}, not a -crds ClusterRole of this chart")
        granted = {
            (g, r, n, v)
            for rule in role.get("rules") or []
            for g in rule.get("apiGroups") or []
            for r in rule.get("resources") or []
            for n in (rule.get("resourceNames") or [""])
            for v in rule.get("verbs") or []
        }
        if granted != MIGRATE_CLUSTER:
            fail(f"{migrate_sa}'s cluster rights are not the 0.x CRD contract's: missing {sorted(MIGRATE_CLUSTER - granted)}, extra {sorted(granted - MIGRATE_CLUSTER)}")
        print(f"ok: {migrate_sa} holds cluster-wide only get/delete on the nine 0.x kagent.dev CRDs and list on their objects")


if __name__ == "__main__":
    main()
