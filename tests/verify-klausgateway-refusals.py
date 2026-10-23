#!/usr/bin/env python3
"""Assert klaus-gateway's egress refusal notices (giantswarm/giantswarm#38667).

Each case pins one property:

- klausGateway.refusals.enabled is auto by default and resolves on with the
  chart's default ranges (klaus-gateway 4.11 and kagent 1.20 or later), and off while the
  floor of components.klaus-gateway.versionRange is below 4.11.0 or that of
  components.kagent.versionRange below 1.20.0 (a kagent range pinned to 1.19,
  rendered in the insecure authentication mode, the only one that admits a
  kagent floor below 1.24.1): the klaus-gateway release gets
  no refusals block (an older chart's closed schema refuses it), the kagent
  release no session reader, the connectivity release refusals.enabled false
  and no -klausgateway-refusals-egress policy;
- with both floors raised, Slack, kagent and Substrate on and the controller
  in the kubernetes mode, the klaus-gateway release gets refusals enabled with
  the derived egressService, kagentTarget and kagentAudience, and its own keys
  (agentURLTemplate) beside them; the kagent release admits
  agent-platform/klaus-gateway as its session reader with the token audience
  and klaus-gateway's pods to its API port; the connectivity release opens the
  gateway's egress to atenet-egress's stats port and the controller's API
  port, and the store-egress policy (its kube-apiserver rule), in both
  flavours;
- enabled false, or Slack off, keeps it off with the floors raised;
- enabled true below the floors, an own egressService that differs and an own
  session reader that differs fail the render naming the key.

Needs PyYAML. HELM selects the binary.
"""

import os
import subprocess
import sys
import tempfile

import yaml

HELM = os.environ.get("HELM", "helm")
CHART, CONNECTIVITY = sys.argv[1], sys.argv[2]
NAMESPACE = "agent-platform"
BASE = [
    "-n", NAMESPACE, "-f", f"{CHART}/ci/ci-values.yaml",
    "--set", "components.flux.enabled=false",
    "--set", "components.kagent.enabled=true",
    "--set", "components.klaus-gateway.enabled=true",
    "--set", "klausGateway.slack.enabled=true",
    "--set", "klausGateway.slack.secretName=slack",
    "--set", "kagent.database.postgres.urlFile=/etc/kagent-db/uri",
    "--set", "kagent.harness.snapshotLocation=s3://ci/agents",
    "--set", "ingress.parentRefs[0].name=x",
    "--set", "global.domain=ci.example.com",
    "--set", "global.identity.issuerUrl=https://dex.ci.example.com",
    "--set", "global.identity.clientId=platform",
    "--set", "global.identity.existingSecret=platform-oauth",
]
FLOORS = {"components": {
    "klaus-gateway": {"versionRange": ">=4.11.0 <5.0.0"},
    "kagent": {"versionRange": ">=1.26.2 <1.27.0"},
    "kagent-crds": {"versionRange": ">=1.26.2 <1.27.0"},
}}
BELOW = {"components": {
    "kagent": {"versionRange": ">=1.19.0 <1.20.0"},
    "kagent-crds": {"versionRange": ">=1.19.0 <1.20.0"},
}}
# A kagent range below agent-platform.kagent.authenticationFloor renders only
# outside the trusted-proxy mode, in which the meta chart derives the
# controller's verification of people's tokens.
BELOW_AUTH_FLOOR = ["--set", "kagent.controller.auth.mode=insecure"]
READER = f"{NAMESPACE}/klaus-gateway"
PEER = {"namespaceSelector": {"matchLabels": {"kubernetes.io/metadata.name": NAMESPACE}},
        "podSelector": {"matchLabels": {"app.kubernetes.io/name": "klaus-gateway"}}}
POLICY = "agent-platform-connectivity-klausgateway-refusals-egress"


def fail(msg: str) -> None:
    sys.exit(f"FAIL: {msg}")


def ok(msg: str) -> None:
    print(f"ok: {msg}")


def helm(chart: str, flags: list, expect_fail: bool = False) -> str:
    r = subprocess.run([HELM, "template", "t", chart, *flags], capture_output=True, text=True)
    if expect_fail:
        if r.returncode == 0:
            fail(f"the render passed but had to fail: {' '.join(flags)}")
        return r.stderr
    if r.returncode != 0:
        fail(f"the render failed: {' '.join(flags)}\n{r.stderr}")
    return r.stdout


def find(docs: list, kind: str, name: str):
    return next((d for d in docs if d.get("kind") == kind and d.get("metadata", {}).get("name") == name), None)


def expect(got, want, what: str) -> None:
    if got != want:
        fail(f"{what}:\n  got  {got!r}\n  want {want!r}")
    ok(what)


def render(flags: list, floors: bool) -> list:
    with tempfile.NamedTemporaryFile("w", suffix=".yaml") as f:
        yaml.safe_dump(FLOORS if floors else BELOW, f)
        f.flush()
        below = [] if floors else BELOW_AUTH_FLOOR
        return [d for d in yaml.safe_load_all(helm(CHART, [*BASE, "-f", f.name, *below, *flags])) if d]


def must_fail(flags: list, floors: bool, needle: str, what: str) -> None:
    with tempfile.NamedTemporaryFile("w", suffix=".yaml") as f:
        yaml.safe_dump(FLOORS if floors else BELOW, f)
        f.flush()
        below = [] if floors else BELOW_AUTH_FLOOR
        err = helm(CHART, [*BASE, "-f", f.name, *below, *flags], expect_fail=True)
    if needle not in err:
        fail(f"{what} failed for another reason (expected {needle!r}):\n{err}")
    ok(f"{what} fails the render naming {needle}")


def values(docs: list, release: str) -> dict:
    hr = find(docs, "HelmRelease", release)
    if hr is None:
        fail(f"no {release} HelmRelease rendered")
    return hr["spec"]["values"]


def connectivity(docs: list) -> list:
    with tempfile.NamedTemporaryFile("w", suffix=".yaml") as f:
        yaml.safe_dump(values(docs, "agent-platform-connectivity"), f)
        f.flush()
        return [d for d in yaml.safe_load_all(helm(CONNECTIVITY, ["-n", NAMESPACE, "-f", f.name])) if d]


def egress_ports(policy: dict, flavor: str) -> set:
    """(app, port) of every pod rule of the policy: atenet-egress's app label, the kagent controller's name."""
    out = set()
    for rule in policy["spec"]["egress"]:
        if flavor == "cilium":
            peers, ports = rule.get("toEndpoints", []), rule.get("toPorts", [{}])[0].get("ports", [])
        else:
            peers, ports = [p.get("podSelector", {}) for p in rule.get("to", [])], rule.get("ports", [])
        for peer in peers:
            labels = peer.get("matchLabels", {})
            app = labels.get("app") or labels.get("app.kubernetes.io/name")
            if app in ("atenet-egress", "kagent"):
                out |= {(app, int(p["port"])) for p in ports}
    return out


def state(docs: list) -> tuple:
    kg = values(docs, "klaus-gateway").get("refusals")
    authz = values(docs, "kagent")["controller"].get("authorization", {})
    peers = values(docs, "kagent")["controller"].get("networkPolicy", {}).get("from", [])
    conn = values(docs, "agent-platform-connectivity")["klausGateway"].get("refusals", {}).get("enabled")
    return kg, authz.get("sessionReaderServiceAccount"), PEER in peers, conn


def main() -> None:
    off = (None, None, False, False)
    default = [d for d in yaml.safe_load_all(helm(CHART, BASE)) if d]
    expect(state(default)[1:], (READER, True, True), "default ranges: the session reader, the peer and connectivity on")
    expect(state(default)[0].get("enabled"), True, "default ranges: the klaus-gateway release runs the notices")
    docs = render([], floors=False)
    expect(state(docs), off, "kagent 1.19: no refusals block, no session reader, no peer, connectivity off")
    if find(connectivity(docs), "CiliumNetworkPolicy", POLICY):
        fail(f"kagent 1.19: {POLICY} renders")
    ok(f"kagent 1.19: no {POLICY}")
    expect(state(render(["--set", "klausGateway.refusals.agentURLTemplate=https://p/{name}"], floors=False))[0], None,
           "below the floors an own agentURLTemplate is not forwarded either")

    docs = render(["--set", "klausGateway.refusals.agentURLTemplate=https://p/{name}"], floors=True)
    kg, reader, peer, conn = state(docs)
    expect(kg, {"enabled": True, "egressService": "ate-system/atenet-egress",
                "kagentTarget": f"kagent-controller.kagent.svc:8083", "kagentAudience": "kagent",
                "agentURLTemplate": "https://p/{name}"},
           "floors raised: the klaus-gateway release runs the notices with the derived targets and its own keys")
    expect((reader, peer, conn), (READER, True, True),
           "floors raised: the controller admits klaus-gateway as session reader and to its API port, connectivity on")
    expect(values(docs, "kagent")["controller"]["authorization"].get("serviceAccountTokenAudience"), "kagent",
           "floors raised: the controller's TokenReview audience is the gateway's token audience")
    for flavor in ("kubernetes", "cilium"):
        cdocs = connectivity(render(["--set", f"networkPolicy.flavor={flavor}"], floors=True))
        kind = "CiliumNetworkPolicy" if flavor == "cilium" else "NetworkPolicy"
        policy = find(cdocs, kind, POLICY)
        if policy is None:
            fail(f"floors raised, {flavor}: no {POLICY}")
        expect(egress_ports(policy, flavor), {("atenet-egress", 15020), ("kagent", 8083)},
               f"floors raised, {flavor}: {POLICY} opens atenet-egress's stats port and the controller's API port")
        store = find(cdocs, kind, "agent-platform-connectivity-klausgateway-store-egress")
        if store is None:
            fail(f"floors raised, {flavor}: no store-egress policy (the gateway's kube-apiserver egress)")
        ok(f"floors raised, {flavor}: the store-egress policy renders (the gateway's kube-apiserver egress)")

    expect(state(render(["--set", "klausGateway.refusals.enabled=false"], floors=True)), off, "enabled false: off")
    expect(state(render(["--set", "klausGateway.slack.enabled=false"], floors=True))[1:], (None, False, False),
           "Slack off: off")

    must_fail(["--set", "klausGateway.refusals.enabled=true"], False,
              "klausGateway.refusals.enabled is true, but", "enabled true below the floors")
    must_fail(["--set", "klausGateway.refusals.egressService=x/y"], True,
              "klausGateway.refusals.egressService (x/y) differs", "an own egressService")
    must_fail(["--set", "kagent.controller.authorization.sessionReaderServiceAccount=a/b"], True,
              "kagent.controller.authorization.sessionReaderServiceAccount (a/b) differs", "an own session reader")


if __name__ == "__main__":
    main()
