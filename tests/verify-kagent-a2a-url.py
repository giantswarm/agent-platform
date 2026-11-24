#!/usr/bin/env python3
"""Assert the public A2A address the kagent controller's agent cards advertise.

The controller builds every card's interfaces from controller.a2aGatewayUrl:
JSON-RPC at <url>/agents/<namespace>/<name>, then gRPC at <url>. The kagent
chart defaults it to the controller's in-cluster plaintext Service, which no
caller outside the cluster reaches. While the controller route is on, the meta
chart derives https://<controller hostname> onto the kagent release, the
hostname the connectivity release's public kagent-a2a-public HTTPRoute and
kagent-controller-public GRPCRoute serve; an own value stands.

Usage: verify-kagent-a2a-url.py <meta chart> <connectivity chart>
Needs PyYAML. Network: gsoci.azurecr.io (the kagent chart at the range's floor).
"""

import os
import re
import subprocess
import sys
import tempfile

import yaml

HELM = os.environ.get("HELM", "helm")
CHART, CONNECTIVITY = sys.argv[1], sys.argv[2]
NAMESPACE = "agent-platform"
KAGENT_CHART = "oci://gsoci.azurecr.io/giantswarm/kagent/helm/kagent"
BASE = [
    "-n", NAMESPACE, "-f", f"{CHART}/ci/ci-values.yaml",
    "--set", "components.flux.enabled=false",
    "--set", "ingress.parentRefs[0].name=x",
    "--set", "global.domain=ci.example.com",
    "--set", "global.identity.clientId=platform",
    "--set", "global.identity.existingSecret=platform-oauth",
    "--set", "gateway.jwksEgress.enabled=true",
]
ROUTE = ["--set", "kagent.controllerRoute.enabled=true"]
URL = "kagent.controller.a2aGatewayUrl"


def fail(msg: str) -> None:
    sys.exit(f"FAIL: {msg}")


def ok(msg: str) -> None:
    print(f"ok: {msg}")


def helm(chart: str, flags: list, release: str = "t") -> str:
    r = subprocess.run([HELM, "template", release, chart, *flags], capture_output=True, text=True)
    if r.returncode != 0:
        fail(f"the render failed: {' '.join(flags)}\n{r.stderr}")
    return r.stdout


def find(docs: list, kind: str, name: str):
    return next((d for d in docs if d.get("kind") == kind and d.get("metadata", {}).get("name") == name), None)


def release_values(flags: list, release: str) -> dict:
    docs = [d for d in yaml.safe_load_all(helm(CHART, [*BASE, *flags])) if d]
    hr = find(docs, "HelmRelease", release)
    if hr is None:
        fail(f"no {release} HelmRelease rendered: {' '.join(flags)}")
    return hr["spec"]["values"]


def a2a_url(flags: list):
    return release_values(flags, "kagent").get("controller", {}).get("a2aGatewayUrl")


def expect(got, want, what: str) -> None:
    if got != want:
        fail(f"{what}:\n  got  {got!r}\n  want {want!r}")
    ok(what)


def public_hostnames(flags: list) -> list:
    """The hostnames of the connectivity release's public A2A HTTPRoute, rendered from the values the meta chart forwards."""
    with tempfile.NamedTemporaryFile("w", suffix=".yaml") as f:
        yaml.safe_dump(release_values(flags, "agent-platform-connectivity"), f)
        f.flush()
        docs = [d for d in yaml.safe_load_all(helm(CONNECTIVITY, ["-n", NAMESPACE, "-f", f.name])) if d]
    route = find(docs, "HTTPRoute", "kagent-a2a-public")
    if route is None:
        fail(f"no HTTPRoute kagent-a2a-public in the connectivity release: {' '.join(flags)}")
    return route["spec"]["hostnames"]


def kagent_floor() -> str:
    with open(f"{CHART}/values.yaml", encoding="utf-8") as f:
        text = f.read()
    m = re.search(r'^  kagent:.*?^    versionRange: ">=([0-9]+\.[0-9]+\.[0-9]+(?:-rc\.[0-9]+)?)[ "]', text, re.M | re.S)
    if not m:
        fail("components.kagent.versionRange in values.yaml has no `>=X.Y.Z` floor")
    return m.group(1)


def kagent_configmap(values: dict, version: str) -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        with open(f"{tmp}/values.yaml", "w", encoding="utf-8") as f:
            yaml.safe_dump(values, f)
        r = subprocess.run([HELM, "pull", KAGENT_CHART, "--version", version, "--untar", "-d", tmp], capture_output=True, text=True)
        if r.returncode != 0:
            fail(f"helm pull kagent {version}: {r.stderr}")
        docs = [d for d in yaml.safe_load_all(helm(f"{tmp}/kagent", ["-n", "kagent", "-f", f"{tmp}/values.yaml"], release="kagent")) if d]
    cm = find(docs, "ConfigMap", "kagent-controller")
    if cm is None:
        fail(f"the kagent chart {version} renders no ConfigMap kagent-controller")
    return cm["data"]


def main() -> None:
    expect(a2a_url(ROUTE), "https://agentgateway.ci.example.com",
           "the cards advertise https://agentgateway.<global.domain> while the controller route is on")
    expect(public_hostnames(ROUTE), ["agentgateway.ci.example.com"],
           "the public A2A HTTPRoute serves the hostname the cards advertise")
    custom = [*ROUTE, "--set", "kagent.controllerRoute.hostname=agents.example.org"]
    expect(a2a_url(custom), "https://agents.example.org", "the address follows kagent.controllerRoute.hostname")
    expect(public_hostnames(custom), ["agents.example.org"], "the public A2A HTTPRoute follows kagent.controllerRoute.hostname")
    edge = [*ROUTE, "--set", "gatewayApi.gateway.create=true", "--set", "gatewayApi.gateway.tls.secretName=wildcard-tls"]
    expect(a2a_url(edge), "https://agentgateway.ci.example.com", "the edge as data plane advertises the same address")
    expect(a2a_url([*ROUTE, "--set", f"{URL}=https://own.example.com:8443"]), "https://own.example.com:8443", "an own address stands")
    expect(a2a_url([]), None, "the controller route off: nothing derived, the kagent chart's in-cluster default stands")
    expect(a2a_url([*ROUTE, "--set", "components.agentgateway.enabled=false"]), None,
           "agentgateway off: nothing derived (no route serves the address)")

    floor = kagent_floor()
    data = kagent_configmap(release_values(ROUTE, "kagent"), floor)
    expect(data.get("KAGENT_GATEWAY_URL"), "https://agentgateway.ci.example.com",
           f"the kagent chart {floor} hands the derived address to the controller (KAGENT_GATEWAY_URL)")


if __name__ == "__main__":
    main()
