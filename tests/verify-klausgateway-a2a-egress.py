#!/usr/bin/env python3
"""Assert the A2A server's JWKS and muster egress without Slack OBO."""

import os
from pathlib import Path
import subprocess
import sys

import yaml


def fail(message):
    sys.exit(f"FAIL: {message}")


def render(chart, flavor, settings, policy_name="agent-platform-connectivity-klausgateway-a2a-server-egress", error=None):
    command = [
        os.environ.get("HELM", "helm"), "template", "t", str(chart),
        "--namespace", "agent-platform",
        "-f", str(chart / "ci/test-klausgateway-a2a-values.yaml"),
        "--set", "ingress.parentRefs[0].name=x",
        "--set", "kagent.harness.snapshotLocation=s3://ci-agent-snapshots/agents",
        "--set", "kagent.database.postgres.urlFile=/etc/kagent-db/uri",
        "--set", f"networkPolicy.flavor={flavor}",
        "--set", "klausGateway.obo.enabled=false",
        "--set", "klausGateway.slack.enabled=false",
        "--set", "networkPolicy.kubernetes.apiServerCIDR=10.96.0.1/32",
    ]
    for setting in settings:
        command.extend(["--set", setting])
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if error:
        if result.returncode == 0 or error not in result.stderr:
            fail(f"{flavor}: expected guard {error!r}, got {result.stderr!r}")
        return None
    if result.returncode:
        fail(f"{flavor} egress render failed: {result.stderr}")
    documents = [document for document in yaml.safe_load_all(result.stdout) if document]
    policy = next((document for document in documents if document.get("kind") in
                   ("NetworkPolicy", "CiliumNetworkPolicy") and document["metadata"]["name"] ==
                   policy_name), None)
    if policy is not None:
        upstream = next((document for document in documents if document.get("kind") in
                         ("NetworkPolicy", "CiliumNetworkPolicy") and document["metadata"]["name"] ==
                         "agent-platform-connectivity-klausgateway-a2a-egress"), None)
        upstream_port = next((int(setting.split("=", 1)[1]) for setting in settings if setting.startswith(
            "gateway.listeners[0].port=")), 8080)
        if upstream is None or not any(upstream_port in ports(rule, flavor) for rule in upstream["spec"]["egress"]):
            fail(f"{flavor}: A2A server cannot reach the agentgateway upstream on {upstream_port}")
        namespace = next((setting.split("=", 1)[1] for setting in settings if setting.startswith(
            "components.klaus-gateway.targetNamespace=")), "agent-platform")
        if upstream["metadata"]["namespace"] != namespace:
            fail(f"{flavor}: the upstream allowance does not select the gateway's namespace")
        if namespace != "agent-platform" and not reaches(upstream, flavor, namespace="agent-platform", port=upstream_port):
            fail(f"{flavor}: the gateway cannot reach agentgateway across namespaces")
        if namespace != "agent-platform":
            dataplane = next((document for document in documents if document.get("kind") in
                             ("NetworkPolicy", "CiliumNetworkPolicy") and document["metadata"]["name"] ==
                             "agent-platform-connectivity-dataplane"), None)
            if dataplane is None or not reaches(dataplane, flavor, namespace=namespace, port=8080):
                fail(f"{flavor}: agentgateway cannot reach the relocated gateway")
        store = next((document for document in documents if document.get("kind") in
                      ("NetworkPolicy", "CiliumNetworkPolicy") and document["metadata"]["name"] ==
                      "agent-platform-connectivity-klausgateway-store-egress"), None)
        if store is None or store["metadata"]["namespace"] != namespace:
            fail(f"{flavor}: no API server allowance in the gateway's namespace")
        if flavor == "cilium":
            allowed = any("kube-apiserver" in rule.get("toEntities", []) for rule in store["spec"]["egress"])
        else:
            allowed = any(peer.get("ipBlock", {}).get("cidr") == "10.96.0.1/32"
                          for rule in store["spec"]["egress"] for peer in rule.get("to", []))
        if not allowed:
            fail(f"{flavor}: the organization lookup cannot reach the API server")
    return policy


def ports(rule, flavor):
    entries = rule.get("ports", []) if flavor == "kubernetes" else [
        port for group in rule.get("toPorts", []) for port in group.get("ports", [])
    ]
    return {int(entry["port"]) for entry in entries if entry.get("protocol", "TCP") == "TCP"}


def reaches(policy, flavor, namespace=None, host=None, cidr=None, port=None):
    for rule in policy["spec"]["egress"]:
        if port not in ports(rule, flavor):
            continue
        if namespace:
            if flavor == "cilium" and any(
                peer.get("matchLabels", {}).get("io.kubernetes.pod.namespace") == namespace
                for peer in rule.get("toEndpoints", [])
            ):
                return True
            if flavor == "kubernetes" and any(
                peer.get("namespaceSelector", {}).get("matchLabels", {}).get(
                    "kubernetes.io/metadata.name") == namespace for peer in rule.get("to", [])
            ):
                return True
        elif cidr:
            if flavor == "cilium" and cidr in rule.get("toCIDR", []):
                return True
            if flavor == "kubernetes" and any(
                peer.get("ipBlock", {}).get("cidr") == cidr for peer in rule.get("to", [])
            ):
                return True
        elif host:
            if flavor == "cilium" and {"matchName": host} in rule.get("toFQDNs", []):
                return True
            if flavor == "kubernetes" and any(
                peer.get("ipBlock", {}).get("cidr") == "0.0.0.0/0" and
                peer["ipBlock"].get("except") for peer in rule.get("to", [])
            ):
                return True
    return False


def check(chart, flavor, name, settings, targets):
    policy = render(chart, flavor, settings)
    if policy is None:
        fail(f"{name}: no {flavor} A2A server egress without Slack OBO")
    for target in targets:
        if not reaches(policy, flavor, **target):
            fail(f"{name}: {flavor} does not reach {target}")
    if any("cluster" in rule.get("toEntities", []) for rule in policy["spec"]["egress"]):
        fail(f"{name}: all cluster endpoints are allowed")
    print(f"ok: {name} ({flavor})")


def main(chart):
    for flavor in ("cilium", "kubernetes"):
        check(chart, flavor, "relocated gateway with custom agentgateway listener", [
            "components.klaus-gateway.targetNamespace=gateway-system",
            "gateway.listeners[0].port=9090",
        ], [])
        check(chart, flavor, "explicit endpoints on custom ports", [
            "klausGateway.a2aServer.token.jwksUrl=https://keys.private.example:8443/keys",
            "klausGateway.obo.musterUrl=https://muster.private.example:9443",
            "gateway.jwksEgress.external.cidrs[0]=10.80.0.0/16",
        ], [
            {"host": "keys.private.example", "port": 8443},
            {"host": "muster.private.example", "port": 9443},
            {"cidr": "10.80.0.0/16", "port": 8443},
            {"cidr": "10.80.0.0/16", "port": 9443},
            {"namespace": "envoy-gateway-system", "port": 10443},
        ])
        check(chart, flavor, "Service endpoints", [
            "klausGateway.a2aServer.token.jwksUrl=https://dex.identity.svc.cluster.local:5556/keys",
            "klausGateway.obo.musterUrl=http://muster.agent-platform.svc.cluster.local:8090",
        ], [
            {"namespace": "identity", "port": 5556},
            {"namespace": "agent-platform", "port": 8090},
        ])
        check(chart, flavor, "gateway component target namespace", [
            "components.klaus-gateway.targetNamespace=agent-ingress",
            "klausGateway.a2aServer.token.jwksUrl=https://dex.identity.svc.cluster.local:5556/keys",
            "klausGateway.obo.musterUrl=http://muster.agent-platform.svc.cluster.local:8090",
        ], [
            {"namespace": "identity", "port": 5556},
            {"namespace": "agent-platform", "port": 8090},
        ])
        check(chart, flavor, "identity and muster defaults", [
            "global.identity.issuerUrl=https://dex.default.example:8543/oidc",
            "muster.muster.oauth.server.baseUrl=https://muster.default.example:8443",
        ], [
            {"host": "dex.default.example", "port": 8543},
            {"host": "muster.default.example", "port": 8443},
        ])
        check(chart, flavor, "address literals", [
            "klausGateway.a2aServer.token.jwksUrl=https://[fd00::10]:8443/keys",
            "klausGateway.obo.musterUrl=https://10.1.2.3:9443",
        ], [
            {"cidr": "fd00::10/128", "port": 8443},
            {"cidr": "10.1.2.3/32", "port": 9443},
        ])
        check(chart, flavor, "standalone A2A server", [
            "klausGateway.agentgatewayRoute.a2aServer.enabled=false",
            "klausGateway.a2aServer.enabled=true",
            "klausGateway.a2aServer.token.jwksUrl=https://dex.default.example/keys",
            "klausGateway.obo.musterUrl=https://muster.default.example",
        ], [
            {"host": "dex.default.example", "port": 443},
            {"host": "muster.default.example", "port": 443},
        ])
        service_proxies = [
            "klausGateway.agentgatewayRoute.a2aServer.issuers[0].jwks.tunnel.url=http://jwks-proxy.proxy-system.svc.cluster.local:3128",
            "klausGateway.agentgatewayRoute.a2aServer.issuers[0].jwks.tunnel.mode=Connect",
            "klausGateway.agentgatewayRoute.a2aServer.exchange.endpoint.tunnel.url=http://token-proxy.proxy-system.svc.cluster.local:3129",
            "klausGateway.agentgatewayRoute.a2aServer.exchange.endpoint.tunnel.mode=Connect",
        ]
        check(chart, flavor, "configured Service proxies for gateway HTTP clients", service_proxies, [
            {"namespace": "proxy-system", "port": 3128},
            {"namespace": "proxy-system", "port": 3129},
        ])
        for role, port, other_port in (("controller", 3128, 3129), ("dataplane", 3129, 3128)):
            policy = render(chart, flavor, service_proxies, f"agent-platform-connectivity-{role}")
            if not policy or not reaches(policy, flavor, namespace="proxy-system", port=port):
                fail(f"{flavor}: {role} cannot reach its CONNECT proxy")
            if reaches(policy, flavor, namespace="proxy-system", port=other_port):
                fail(f"{flavor}: {role} reaches another request hop's proxy")
        external = [
            "klausGateway.agentgatewayRoute.a2aServer.exchange.endpoint.host=token.private.example",
            "klausGateway.agentgatewayRoute.a2aServer.exchange.endpoint.port=8443",
            "klausGateway.agentgatewayRoute.a2aServer.exchange.endpoint.path=/token",
            "klausGateway.agentgatewayRoute.a2aServer.exchange.endpoint.tunnel.url=http://proxy.private.example:3130",
            "klausGateway.agentgatewayRoute.a2aServer.exchange.endpoint.tunnel.mode=Connect",
            "gateway.jwksEgress.external.cidrs[0]=10.90.0.0/16",
        ]
        dataplane = render(chart, flavor, external, "agent-platform-connectivity-dataplane")
        for target in (
            {"host": "token.private.example", "port": 8443},
            {"host": "proxy.private.example", "port": 3130},
            {"cidr": "10.90.0.0/16", "port": 8443},
            {"cidr": "10.90.0.0/16", "port": 3130},
        ):
            if not dataplane or not reaches(dataplane, flavor, **target):
                fail(f"{flavor}: the token exchange cannot reach {target}")
        print(f"ok: CONNECT proxy and token endpoint egress ({flavor})")
        for setting in (
            "networkPolicy.enabled=false",
            "components.klaus-gateway.enabled=false",
            "klausGateway.agentgatewayRoute.a2aServer.enabled=false",
        ):
            if render(chart, flavor, [setting]) is not None:
                fail(f"{flavor}: A2A server egress survives {setting}")
    print("ok: A2A server egress gates")
    for cidr in ("0.0.0.0/0", "::/0"):
        render(chart, "kubernetes", [f"networkPolicy.kubernetes.apiServerCIDR={cidr}"],
               error="networkPolicy.kubernetes.apiServerCIDR")
        render(chart, "kubernetes", [
            f"networkPolicy.kubernetes.apiServerCIDR={cidr}",
            "klausGateway.agentgatewayRoute.a2aServer.enabled=false",
        ])
    print("ok: the A2A server requires an explicit API server CIDR only while enabled")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        fail("usage: verify-klausgateway-a2a-egress.py <connectivity-chart>")
    main(Path(sys.argv[1]))
