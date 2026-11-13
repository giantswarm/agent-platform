#!/usr/bin/env python3
"""Assert every network policy names the owning Gateway of the Envoy pods it selects.

Envoy Gateway runs one proxy Deployment per Gateway and labels its pods with
the Gateway it serves (gateway.envoyproxy.io/owning-gateway-name and
-namespace). A policy that selects `app.kubernetes.io/name: envoy` alone
admits, or reaches, the proxy of every Gateway of that namespace — the
internal Gateway's next to the public one's (giantswarm/agent-platform#1292).

Over the shapes below, in both flavours, every NetworkPolicy and
CiliumNetworkPolicy of the connectivity chart is read:

- every selector (matchLabels) that selects Envoy pods carries both owning
  labels, and a selector with one owning label carries the other;
- each shape's policies select exactly the Gateways the routes they follow
  attach to: the values the chart attaches the routes with
  (global.gatewayApi.parentRefs, ingress.parentRefs, backstage.parentRefs,
  kagent.uiRoute.parentRef, workspaceManager.route.parentRefs), so a policy
  cannot pass by selecting nothing.

Usage: verify-envoy-peers.py CONNECTIVITY_CHART. Needs PyYAML. HELM selects the binary.
"""
import os
import subprocess
import sys

import yaml

HELM = os.environ.get("HELM", "helm")
ENVOY = "app.kubernetes.io/name"
OWNER = "gateway.envoyproxy.io/owning-gateway-name"
OWNER_NS = "gateway.envoyproxy.io/owning-gateway-namespace"
POLICY_KINDS = ("NetworkPolicy", "CiliumNetworkPolicy")
PREFIX = "agent-platform-connectivity-"

FLEET_APIS = [
    "--api-versions", "kyverno.io/v1",
    "--api-versions", "cilium.io/v2",
    "--api-versions", "monitoring.coreos.com/v1",
    "--api-versions", "gateway.networking.k8s.io/v1",
    "--api-versions", "gateway.envoyproxy.io/v1alpha1",
]
# Makefile.custom.mk's VM, in the release namespace agent-platform.
VM = ["--namespace", "agent-platform", "--set", "kagent.harness.snapshotLocation=s3://ci-agent-snapshots/agents",
      "--set", "kagent.database.postgres.urlFile=/etc/kagent-db/uri", *FLEET_APIS]
IDENTITY = [
    "--set", "global.domain=ci.example.com", "--set", "global.identity.issuerUrl=https://dex.ci.example.com",
    "--set", "global.identity.clientId=agent-platform", "--set", "global.identity.existingSecret=agent-platform-idp",
]
EG = "envoy-gateway-system"
PUBLIC = ("giantswarm-default", EG)
INTERNAL = ("giantswarm-internal", EG)
# The fleet's two Gateways: the public one is the platform's default parent,
# muster's route sits on the internal one (a private installation).
GATEWAYS = [
    "--set", f"global.gatewayApi.parentRefs[0].name={PUBLIC[0]}", "--set", f"global.gatewayApi.parentRefs[0].namespace={EG}",
    "--set", f"ingress.parentRefs[0].name={INTERNAL[0]}", "--set", f"ingress.parentRefs[0].namespace={EG}",
]
SUBSTRATE = ["--set", "components.substrate.enabled=true", "--set", "components.substrate-crds.enabled=true",
             "--set", "components.kagent.enabled=true", "--set", "muster.enabled=true"]
KAGENT_UI = ["--set", "components.kagent.enabled=true", "--set", "kagent.namespaceOverride=kagent",
             "--set", "kagent.oauth2-proxy.enabled=true", "--set", "kagent.uiRoute.enabled=true"]
WORKSPACES = ["--set", "components.workspace-manager.enabled=true", "-f", "tests/fixtures/workspaces-values.yaml"]

# name, flags, flavours, {policy name (less the prefix): {(gateway, namespace), ...}}
SHAPES = [
    ("the portal: its own route on the public Gateway, the app-config's hostnames on muster's internal one",
     [*VM, *IDENTITY, *GATEWAYS, "--set", "components.backstage.enabled=true"],
     {"cilium": {"backstage": {PUBLIC}},
      "kubernetes": {"backstage": {PUBLIC}, "backstage-egress": {INTERNAL}}}),
    ("the portal pinned to the internal Gateway (backstage.parentRefs)",
     [*VM, *IDENTITY, *GATEWAYS, "--set", "components.backstage.enabled=true",
      "--set", f"backstage.parentRefs[0].name={INTERNAL[0]}", "--set", f"backstage.parentRefs[0].namespace={EG}"],
     {"cilium": {"backstage": {INTERNAL}},
      "kubernetes": {"backstage": {INTERNAL}, "backstage-egress": {INTERNAL}}}),
    ("the actors' egress gateway: muster's route on the internal Gateway",
     [*VM, *IDENTITY, *GATEWAYS, *SUBSTRATE],
     {"cilium": {"substrate-atenet-egress": {INTERNAL}},
      "kubernetes": {"substrate-atenet-egress-egress": {INTERNAL}}}),
    ("the actors' egress gateway: muster's route on a Gateway of another name",
     [*VM, *IDENTITY, *SUBSTRATE, "--set", "ingress.parentRefs[0].name=edge", "--set", "ingress.parentRefs[0].namespace=edge-system"],
     {"cilium": {"substrate-atenet-egress": {("edge", "edge-system")}},
      "kubernetes": {"substrate-atenet-egress-egress": {("edge", "edge-system")}}}),
    ("kagent's UI: oauth2-proxy behind the UI route's Gateway (kagent.uiRoute.parentRef)",
     [*VM, *IDENTITY, *GATEWAYS, *KAGENT_UI, "--set", "kagent.uiRoute.hostname=kagent.ci.example.com"],
     {"cilium": {"oauth2-proxy-ingress": {PUBLIC}}}),
    ("kagent's UI on the internal Gateway",
     [*VM, *IDENTITY, *GATEWAYS, *KAGENT_UI, "--set", "kagent.uiRoute.hostname=kagent.ci.example.com",
      "--set", f"kagent.uiRoute.parentRef.name={INTERNAL[0]}"],
     {"cilium": {"oauth2-proxy-ingress": {INTERNAL}}}),
    ("the workspace manager's key-set route on muster's Gateway",
     [*VM, *IDENTITY, *GATEWAYS, *WORKSPACES],
     {"cilium": {"workspace-manager-ingress": {INTERNAL}},
      "kubernetes": {"workspace-manager-ingress": {INTERNAL}}}),
    ("the workspace manager's key-set route pinned to the public Gateway",
     [*VM, *IDENTITY, *GATEWAYS, *WORKSPACES,
      "--set", f"workspaceManager.route.parentRefs[0].name={PUBLIC[0]}", "--set", f"workspaceManager.route.parentRefs[0].namespace={EG}"],
     {"cilium": {"workspace-manager-ingress": {PUBLIC}},
      "kubernetes": {"workspace-manager-ingress": {PUBLIC}}}),
]


def fail(msg: str) -> None:
    sys.exit(f"FAIL: {msg}")


def ok(msg: str) -> None:
    print(f"ok: {msg}")


def render(chart: str, flags: list) -> list:
    result = subprocess.run([HELM, "template", "t", chart, *flags], capture_output=True, text=True, check=False)
    if result.returncode != 0:
        fail(f"render of {' '.join(flags)} failed\n{result.stderr}")
    return [d for d in yaml.safe_load_all(result.stdout) if d]


def selectors(node):
    """Every matchLabels map under node."""
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "matchLabels" and isinstance(value, dict):
                yield value
            else:
                yield from selectors(value)
    elif isinstance(node, list):
        for item in node:
            yield from selectors(item)


def envoy_gateways(policy: dict, where: str) -> set:
    """The owning Gateways a policy selects Envoy pods of; fails on a selector without its owner."""
    found = set()
    for labels in selectors(policy.get("spec", {})):
        owned = OWNER in labels or OWNER_NS in labels
        if labels.get(ENVOY) == "envoy" or owned:
            if not (labels.get(OWNER) and labels.get(OWNER_NS)):
                fail(f"{where}: a selector picks Envoy pods without naming their Gateway "
                     f"({OWNER} and {OWNER_NS}): {labels}")
            found.add((str(labels[OWNER]), str(labels[OWNER_NS])))
    return found


def main(argv: list) -> int:
    if len(argv) != 1:
        sys.exit(__doc__)
    chart = argv[0]
    for name, flags, flavours in SHAPES:
        for flavour, want in flavours.items():
            docs = render(chart, [*flags, "--set", f"networkPolicy.flavor={flavour}"])
            got = {}
            for doc in docs:
                if doc.get("kind") not in POLICY_KINDS:
                    continue
                where = f"{name}, {flavour}: {doc['kind']} {doc['metadata']['name']}"
                gateways = envoy_gateways(doc, where)
                if gateways:
                    got[doc["metadata"]["name"].removeprefix(PREFIX)] = gateways
            if got != want:
                fail(f"{name}, {flavour}: the policies select the Envoy pods of {got}, want {want}")
        ok(f"{name}: " + "; ".join(f"{fl} {sorted(w)}" for fl, w in flavours.items()))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
