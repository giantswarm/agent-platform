#!/usr/bin/env python3
"""Assert the kagent controller's authorizer and its NetworkPolicy
(giantswarm/agent-platform#706; the agent-permissions plan, D3 and R1).

Each case pins one property:

- by default the kagent release carries controller.authorization with mode
  kubernetes, groupsClaim groups, no username or groups prefix, the
  Organizations' label as namespaceSelector and the kagent namespace (the
  platform tenant) as includeNamespaces; the kagent namespace follows
  kagent.namespaceOverride;
- each of those keys an installation sets stands as written (agentlab's
  groups prefix oidc:, an own includeNamespaces);
- with the authorizer on the release carries controller.networkPolicy, on,
  admitting the agentgateway data plane (gateway.name, the connectivity
  release's namespace) and Substrate's egress gateway; an own peer is
  admitted beside them once; enabled: false fails the render naming the key;
- `mode: noop` keeps today's controller: no NetworkPolicy and nothing derived;
- with workspaces on: workspace-manager's pods are admitted too,
  serviceAccountTokenAudience is the audience of the manager's token
  (workspace-manager.kagent.tokenAudience, kagent unless set), an own
  audience that differs fails the render and an equal one passes, a
  workspace-manager range whose floor sends no token (below 0.10.0) fails the
  render, and the peer follows the manager's release namespace;
- the connectivity release (with the values the meta chart forwards) binds
  workspace-manager's ServiceAccount, and only it, to a ClusterRole with
  `list` on sessions.api.kagent.dev and nothing else, following
  workspace-manager.serviceAccount.name and the manager's release namespace
  (components.workspace-manager.targetNamespace); nothing without workspaces;
- the kagent chart at the range's floor, rendered with the values the release
  forwards, renders the KAGENT_AUTHORIZATION_* entries of the controller
  ConfigMap, the NetworkPolicy kagent-controller that admits on the API port
  exactly kagent's UI and the derived peers, and the ClusterRole that lets the
  controller create subjectaccessreviews and tokenreviews (network: gsoci).

Needs PyYAML. HELM selects the binary.
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
    "--set", "components.kagent.enabled=true",
    "--set", "kagent.database.postgres.urlFile=/etc/kagent-db/uri",
    "--set", "kagent.harness.snapshotLocation=s3://ci/agents",
    "--set", "ingress.parentRefs[0].name=x",
    "--set", "global.domain=ci.example.com",
    "--set", "global.identity.issuerUrl=https://dex.ci.example.com",
    "--set", "global.identity.clientId=platform",
    "--set", "global.identity.existingSecret=platform-oauth",
]
WORKSPACES = ["-f", "tests/fixtures/workspaces-values.yaml", "-f", "tests/fixtures/workspaces-agents-values.yaml"]
AUTHZ = "kagent.controller.authorization"
GATEWAY = {"namespaceSelector": {"matchLabels": {"kubernetes.io/metadata.name": NAMESPACE}},
           "podSelector": {"matchLabels": {"gateway.networking.k8s.io/gateway-name": "agentgateway"}}}
EGRESS = {"namespaceSelector": {"matchLabels": {"kubernetes.io/metadata.name": "ate-system"}},
          "podSelector": {"matchLabels": {"app": "atenet-egress"}}}
MANAGER = {"namespaceSelector": {"matchLabels": {"kubernetes.io/metadata.name": NAMESPACE}},
           "podSelector": {"matchLabels": {"app.kubernetes.io/name": "workspace-manager"}}}
UI = {"podSelector": {"matchLabels": {"app.kubernetes.io/component": "ui", "app.kubernetes.io/instance": "kagent",
                                      "app.kubernetes.io/name": "kagent"}}}
DEFAULT = {"mode": "kubernetes", "usernamePrefix": "", "groupsClaim": "groups", "groupsPrefix": "",
           "namespaceSelector": "giantswarm.io/organization", "includeNamespaces": ["kagent"]}
ROLE = "agent-platform:workspace-manager-sessions"


def fail(msg: str) -> None:
    sys.exit(f"FAIL: {msg}")


def ok(msg: str) -> None:
    print(f"ok: {msg}")


def helm(chart: str, flags: list, expect_fail: bool = False, release: str = "t") -> str:
    r = subprocess.run([HELM, "template", release, chart, *flags], capture_output=True, text=True)
    if expect_fail:
        if r.returncode == 0:
            fail(f"the render passed but had to fail: {' '.join(flags)}")
        return r.stderr
    if r.returncode != 0:
        fail(f"the render failed: {' '.join(flags)}\n{r.stderr}")
    return r.stdout


def must_fail(flags: list, needle: str, what: str) -> None:
    err = helm(CHART, [*BASE, *flags], expect_fail=True)
    if needle not in err:
        fail(f"{what} failed for another reason (expected {needle!r}):\n{err}")
    ok(f"{what} fails the render naming {needle}")


def find(docs: list, kind: str, name: str):
    return next((d for d in docs if d.get("kind") == kind and d.get("metadata", {}).get("name") == name), None)


def release_values(flags: list, release: str) -> dict:
    docs = [d for d in yaml.safe_load_all(helm(CHART, [*BASE, *flags])) if d]
    hr = find(docs, "HelmRelease", release)
    if hr is None:
        fail(f"no {release} HelmRelease rendered: {' '.join(flags)}")
    return hr["spec"]["values"]


def controller(flags: list) -> dict:
    return release_values(flags, "kagent")["controller"]


def expect(got, want, what: str) -> None:
    if got != want:
        fail(f"{what}:\n  got  {got!r}\n  want {want!r}")
    ok(what)


def connectivity(flags: list) -> list:
    """The connectivity chart rendered with the values the meta chart forwards to its release."""
    with tempfile.NamedTemporaryFile("w", suffix=".yaml") as f:
        yaml.safe_dump(release_values(flags, "agent-platform-connectivity"), f)
        f.flush()
        return [d for d in yaml.safe_load_all(helm(CONNECTIVITY, ["-n", NAMESPACE, "-f", f.name])) if d]


def kagent_floor() -> str:
    with open(f"{CHART}/values.yaml", encoding="utf-8") as f:
        text = f.read()
    m = re.search(r'^  kagent:.*?^    versionRange: ">=([0-9]+\.[0-9]+\.[0-9]+(?:-rc\.[0-9]+)?)[ "]', text, re.M | re.S)
    if not m:
        fail("components.kagent.versionRange in values.yaml has no `>=X.Y.Z` floor")
    return m.group(1)


def kagent_chart_render(values: dict, version: str) -> list:
    with tempfile.TemporaryDirectory() as tmp:
        with open(f"{tmp}/values.yaml", "w", encoding="utf-8") as f:
            yaml.safe_dump(values, f)
        r = subprocess.run([HELM, "pull", KAGENT_CHART, "--version", version, "--untar", "-d", tmp], capture_output=True, text=True)
        if r.returncode != 0:
            fail(f"helm pull kagent {version}: {r.stderr}")
        out = helm(f"{tmp}/kagent", ["-n", "kagent", "-f", f"{tmp}/values.yaml"], release="kagent")
    return [d for d in yaml.safe_load_all(out) if d]


def main() -> None:
    c = controller([])
    expect(c.get("authorization"), DEFAULT, "default: the kubernetes mode with the installation's OIDC mapping and the tenants' namespaces")
    expect(c.get("networkPolicy"), {"enabled": True, "from": [GATEWAY, EGRESS]},
           "default: the controller's NetworkPolicy admits the agentgateway data plane and Substrate's egress gateway")
    c = controller(["--set", "kagent.namespaceOverride=agents", "--set", "gateway.name=edge"])
    expect(c["authorization"]["includeNamespaces"], ["agents"], "includeNamespaces follows kagent.namespaceOverride")
    expect(c["networkPolicy"]["from"][0]["podSelector"], {"matchLabels": {"gateway.networking.k8s.io/gateway-name": "edge"}},
           "the data-plane peer follows gateway.name")

    own = ["--set", f"{AUTHZ}.groupsPrefix=oidc:", "--set", f"{AUTHZ}.usernamePrefix=oidc:",
           "--set", f"{AUTHZ}.groupsClaim=grp", "--set", f"{AUTHZ}.namespaceSelector=tenant=yes",
           "--set", f"{AUTHZ}.includeNamespaces[0]=shared"]
    expect(controller(own)["authorization"],
           {"mode": "kubernetes", "usernamePrefix": "oidc:", "groupsClaim": "grp", "groupsPrefix": "oidc:",
            "namespaceSelector": "tenant=yes", "includeNamespaces": ["shared"]},
           "an installation's own keys stand as written")

    c = controller(["--set", f"{AUTHZ}.mode=noop"])
    expect((c["authorization"]["mode"], c["authorization"]["includeNamespaces"], "networkPolicy" in c), ("noop", [], False),
           "mode noop: no NetworkPolicy and nothing derived")

    extra = {"podSelector": {"matchLabels": {"app": "probe"}}}
    c = controller(["--set", "kagent.controller.networkPolicy.from[0].podSelector.matchLabels.app=probe",
                    "--set", "kagent.controller.networkPolicy.from[1].podSelector.matchLabels.app=probe"])
    expect(c["networkPolicy"]["from"], [GATEWAY, EGRESS, extra], "an own peer is admitted beside the derived ones, once")
    must_fail(["--set", "kagent.controller.networkPolicy.enabled=false"], "kagent.controller.networkPolicy.enabled is false",
              "the authorizer without the NetworkPolicy")

    values = release_values(WORKSPACES, "kagent")
    c = values["controller"]
    expect(c["authorization"].get("serviceAccountTokenAudience"), "kagent", "workspaces: the token audience is workspace-manager's default, kagent")
    expect(c["networkPolicy"]["from"], [GATEWAY, EGRESS, MANAGER], "workspaces: workspace-manager is admitted to the API port")
    expect(controller([*WORKSPACES, "--set", "workspace-manager.kagent.tokenAudience=wm"])["authorization"]["serviceAccountTokenAudience"],
           "wm", "the token audience follows workspace-manager.kagent.tokenAudience")
    expect(controller([*WORKSPACES, "--set", f"{AUTHZ}.serviceAccountTokenAudience=kagent"])["authorization"]["serviceAccountTokenAudience"],
           "kagent", "an own audience equal to the manager's passes")
    must_fail([*WORKSPACES, "--set", f"{AUTHZ}.serviceAccountTokenAudience=other"],
              "kagent.controller.authorization.serviceAccountTokenAudience (other) differs", "an own audience other than the manager's")
    # Below the muster grant floor the manager renders only without OAuth (verify-workspace-manager's PRE_GRANT).
    pre_grant = ["--set", "workspace-manager.oauth.enabled=false", "--set", "workspace-manager.grant.signingKey=null",
                 "--set", "workspace-manager.muster.mcpServer.auth.authorizationServer=null",
                 "--set", "muster.muster.credentialProvider.releasable.github=https://github.com/apps/ci-agents"]
    must_fail([*WORKSPACES, *pre_grant, "--set", "components.workspace-manager.versionRange=>=0.9.0 <0.11.0"],
              "admits a workspace-manager before 0.10.0", "a workspace-manager floor that sends no token")
    split = ["--set", "components.workspace-manager.targetNamespace=elsewhere"]
    expect(controller([*WORKSPACES, *split])["networkPolicy"]["from"][2]["namespaceSelector"],
           {"matchLabels": {"kubernetes.io/metadata.name": "elsewhere"}}, "the workspace-manager peer follows its release's namespace")

    for flags, sa, ns in (([], "workspace-manager", NAMESPACE), (["--set", "workspace-manager.serviceAccount.name=wm-sa"], "wm-sa", NAMESPACE),
                          (split, "workspace-manager", "elsewhere")):
        docs = connectivity([*WORKSPACES, *flags])
        role, binding = find(docs, "ClusterRole", ROLE), find(docs, "ClusterRoleBinding", ROLE)
        if role is None or binding is None:
            fail(f"workspaces: no ClusterRole/ClusterRoleBinding {ROLE}")
        expect(role["rules"], [{"apiGroups": ["api.kagent.dev"], "resources": ["sessions"], "verbs": ["list"]}],
               f"{ROLE} grants list on sessions.api.kagent.dev and nothing else")
        expect((binding["roleRef"]["name"], binding["subjects"]),
               (ROLE, [{"kind": "ServiceAccount", "name": sa, "namespace": ns}]),
               f"{ROLE} is bound to workspace-manager's ServiceAccount {ns}/{sa} only")
    expect(find(connectivity([]), "ClusterRole", ROLE), None, f"without workspaces no {ROLE}")

    floor = kagent_floor()
    docs = kagent_chart_render(values, floor)
    cm = find(docs, "ConfigMap", "kagent-controller")
    if cm is None:
        fail(f"the kagent chart {floor} renders no ConfigMap kagent-controller")
    expect({k: v for k, v in cm["data"].items() if k.startswith("KAGENT_AUTHORIZATION_")},
           {"KAGENT_AUTHORIZATION_MODE": "kubernetes", "KAGENT_AUTHORIZATION_USERNAME_PREFIX": "",
            "KAGENT_AUTHORIZATION_GROUPS_CLAIM": "groups", "KAGENT_AUTHORIZATION_GROUPS_PREFIX": "",
            "KAGENT_AUTHORIZATION_NAMESPACE_SELECTOR": "giantswarm.io/organization",
            "KAGENT_AUTHORIZATION_INCLUDE_NAMESPACES": "kagent",
            "KAGENT_AUTHORIZATION_SERVICE_ACCOUNT_TOKEN_AUDIENCE": "kagent"},
           f"the kagent chart {floor} configures the authorizer from the forwarded block")
    np = find(docs, "NetworkPolicy", "kagent-controller")
    if np is None:
        fail(f"the kagent chart {floor} renders no NetworkPolicy kagent-controller")
    api = [r for r in np["spec"]["ingress"] if r.get("ports") == [{"port": "http", "protocol": "TCP"}]]
    expect([r.get("from") for r in api], [[UI, GATEWAY, EGRESS, MANAGER]],
           f"the kagent chart {floor} admits to the API port exactly the UI and the platform's callers")
    role = find(docs, "ClusterRole", "kagent-authorization-role")
    expect(role and sorted((g, res, v) for r in role["rules"] for g in r["apiGroups"] for res in r["resources"] for v in r["verbs"]),
           [("authentication.k8s.io", "tokenreviews", "create"), ("authorization.k8s.io", "subjectaccessreviews", "create")],
           f"the kagent chart {floor} lets the controller create subjectaccessreviews and tokenreviews")


if __name__ == "__main__":
    main()
