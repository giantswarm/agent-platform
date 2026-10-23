#!/usr/bin/env python3
"""Assert the toolchain-manager component (components.toolchain-manager, giantswarm/giantswarm#38787).

toolchain-manager is the Agent Platform's toolchain surface (giantswarm/toolchain-manager):
the toolchain sources an installation offers, package search, binaries and the Dockerfile a
toolchain is built from, MCP only through muster. The meta chart carries it as an opt-in
component release with the chart's own MCPServer CR, and derives what the platform already
knows (agent-platform.toolchainManager.derived). Each case below pins one property of that:

- off by default: no release, the roster forwarded to connectivity has no toolchain-manager
  entry (gatedRoster), the toolchain-manager block never reaches connectivity (its omitKeys),
  and the meta and connectivity renders carry nothing named toolchain-manager;
- on: ONE OCIRepository (the catalog's toolchain-manager chart on its range) and ONE
  HelmRelease that dependsOn muster, the block forwarded with the derived login (issuer and
  trusted audience from global.identity, else muster's login; the issuer's CA from
  global.identity.ca), muster.mcpServer.enabled and the giantswarm/agent-toolchains source;
  connectivity receives the roster entry and the MCP backend entry, and renders the ingress
  policy that admits muster to the manager's pods, in both flavors;
- the pin: with workspaces on, the registration is pinned to the App of the
  workspace-manager's github provider instance (its issuer, GitHub's endpoints and iss, the
  App's client Secret in the workspace-manager's namespace); without workspaces, none;
- own values stand: issuer, trusted audiences, the pin, muster.mcpServer.enabled;
- additionalSources follow sources and are not forwarded as such;
- muster off: the registration off and no dependsOn on muster;
- agent-manager's muster.toolchainsToolsPrefix is x_<the MCPServer name>_ while the component is
  on, from agent-manager 2.14.0 (none off or below the floor), an own value that differs fails;
- the guards: no issuer, no OAuth client, a repository named twice;
- examples/customer-bom.yaml pins the exact version and the pin reaches the OCIRepository.

Deliberately stdlib-only: the CI image has no PyYAML. HELM selects the binary.
"""

import os
import re
import subprocess
import sys

import floors

HELM = os.environ.get("HELM", "helm")
NAME = "toolchain-manager"
REPOSITORY = "oci://gsoci.azurecr.io/charts/giantswarm"
CI = ["--set", "components.flux.enabled=false"]
ON = ["--set", f"components.{NAME}.enabled=true"]
IDENTITY = [
    "--set", "global.domain=ci.example.com",
    "--set", "global.identity.issuerUrl=https://dex.ci.example.com",
    "--set", "global.identity.clientId=platform",
    "--set", "global.identity.existingSecret=platform-oauth",
]
WORKSPACES = ["-f", "tests/fixtures/workspaces-values.yaml"]


def fail(msg: str) -> None:
    sys.exit(f"FAIL: {msg}")


def ok(msg: str) -> None:
    print(f"ok: {msg}")


def helm(chart: str, flags: list, expect_fail: bool = False) -> str:
    cmd = [HELM, "template", "t", chart, "-f", f"{chart}/ci/ci-values.yaml", *CI, *flags]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if expect_fail:
        if r.returncode == 0:
            fail(f"the render passed but had to fail: {' '.join(flags)}")
        return r.stderr
    if r.returncode != 0:
        fail(f"the render failed: {' '.join(flags)}\n{r.stderr}")
    return r.stdout


def documents(render: str) -> dict:
    """(kind, name) -> the document, for every document that has both."""
    out = {}
    for doc in render.split("\n---\n"):
        kind = re.search(r"^kind: (\S+)", doc, re.M)
        name = re.search(r"^  name: (\S+)", doc, re.M)
        if kind and name:
            out[(kind.group(1), name.group(1))] = doc.rstrip("\n") + "\n"
    return out


def values(doc: str) -> str:
    """A HelmRelease's spec.values, dedented to the top level."""
    m = re.search(r"^  values:\n((?:    .*\n|\n)*)", doc, re.M)
    if not m:
        fail(f"no spec.values in:\n{doc}")
    return "\n".join(line[4:] for line in m.group(1).splitlines()) + "\n"


def must_have(text: str, lines: tuple, what: str) -> None:
    for line in lines:
        if line not in text:
            fail(f"{what} lacks {line!r}:\n{text}")


def must_lack(text: str, lines: tuple, what: str) -> None:
    for line in lines:
        if line in text:
            fail(f"{what} carries {line!r}:\n{text}")


def release(meta: str, flags: list) -> tuple:
    """The component's HelmRelease values and the connectivity release's values."""
    d = documents(helm(meta, flags))
    hr = d.get(("HelmRelease", NAME))
    if not hr:
        fail(f"no HelmRelease {NAME} with {' '.join(flags)}")
    return hr, values(hr), values(d[("HelmRelease", "agent-platform-connectivity")])


def main(meta: str, connectivity: str) -> int:
    rng = floors.range_of(NAME, meta)
    # --- off by default -------------------------------------------------------
    off = helm(meta, [])
    off_docs = documents(off)
    for kind in ("OCIRepository", "HelmRelease"):
        if (kind, NAME) in off_docs:
            fail(f"components.{NAME} is off by default, but the render has a {kind} {NAME}")
    if NAME in off:
        fail(f"the default render names {NAME}: nothing of it may reach an installation that leaves it off")
    ok(f"off by default: no release, and neither the roster entry nor the block reach connectivity")

    # --- on -------------------------------------------------------------------
    on_docs = documents(helm(meta, [*ON, *IDENTITY]))
    oci = on_docs.get(("OCIRepository", NAME))
    hr = on_docs.get(("HelmRelease", NAME))
    if not oci or not hr:
        fail(f"components.{NAME} on did not render one OCIRepository + one HelmRelease")
    must_have(oci, (f"  url: {REPOSITORY}/{NAME}\n", f'semver: "{rng}"'), "the OCIRepository")
    must_have(hr, ("  releaseName: toolchain-manager\n", "  dependsOn:\n    - name: muster\n"), "the HelmRelease")
    vals = values(hr)
    must_have(vals, (
        "auth:\n  dex:\n    issuerURL: https://dex.ci.example.com\n  trustedAudiences:\n  - platform\n",
        "muster:\n  mcpServer:\n    enabled: true\n",
        "sources:\n- registry: gsoci.azurecr.io/giantswarm/agent-toolchains\n  repository: giantswarm/agent-toolchains\n",
        "global:\n",
    ), "the release values")
    must_lack(vals, ("authorizationServer", "additionalSources", "caSecret"), "the release values without workspaces or a CA")
    conn = values(on_docs[("HelmRelease", "agent-platform-connectivity")])
    must_have(conn, (
        "  toolchain-manager:\n    enabled: true\n",
        "  mcpBackends:\n    toolchain-manager:\n      namespace: default\n      podSelector:\n        app.kubernetes.io/name: toolchain-manager\n      ports:\n      - 8080\n",
    ), "the connectivity release values")
    if re.search(r"^toolchain-manager:", conn, re.M):
        fail("the toolchain-manager block reached the connectivity release, whose root schema refuses it")
    ok(f"on: one OCIRepository ({REPOSITORY}/{NAME}, {rng}) and one HelmRelease after muster, the login derived from global.identity, the registration on, the agent-toolchains source; connectivity gets the roster and MCP backend entries")

    # The MCP backend entry renders muster's ingress to the manager in both flavors.
    for flavor, kind in (("cilium", "CiliumNetworkPolicy"), ("kubernetes", "NetworkPolicy")):
        cmd = [HELM, "template", "t", connectivity, "-f", f"{connectivity}/ci/ci-values.yaml",
               "--set", "ingress.parentRefs[0].name=x", "--set", f"networkPolicy.flavor={flavor}",
               "--set", "networkPolicy.mcpBackends.toolchain-manager.namespace=agent-platform",
               "--set", "networkPolicy.mcpBackends.toolchain-manager.ports={8080}",
               "--set", "networkPolicy.mcpBackends.toolchain-manager.podSelector.app\\.kubernetes\\.io/name=toolchain-manager"]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            fail(f"the connectivity render with the MCP backend entry failed ({flavor}):\n{r.stderr}")
        policy = documents(r.stdout).get((kind, "agent-platform-connectivity-mcp-backend-toolchain-manager"))
        if not policy:
            fail(f"connectivity ({flavor}) renders no {kind} agent-platform-connectivity-mcp-backend-toolchain-manager")
        must_have(policy, ("app.kubernetes.io/name: muster", "app.kubernetes.io/name: toolchain-manager"), f"the {flavor} ingress policy")
    ok("connectivity admits muster to the manager's pods (CiliumNetworkPolicy and NetworkPolicy)")

    # muster's login stands in for global.identity.
    _, vals, _ = release(meta, [*ON, "--set", "muster.muster.oauth.server.dex.issuerUrl=https://login.ci.example.com",
                                "--set", "muster.muster.oauth.server.dex.clientId=muster-client"])
    must_have(vals, ("    issuerURL: https://login.ci.example.com\n", "  trustedAudiences:\n  - muster-client\n"), "the release values from muster's login")
    _, vals, _ = release(meta, [*ON, *IDENTITY, "--set", "global.identity.ca.secretName=dex-ca"])
    must_have(vals, ("    caSecret:\n      key: ca.crt\n      name: dex-ca\n",), "the release values with global.identity.ca")
    ok("the login from muster's OAuth server without global.identity; the issuer's CA from global.identity.ca")

    # --- the pin: the workspace-manager's github App ---------------------------
    _, vals, _ = release(meta, [*ON, *IDENTITY, *WORKSPACES])
    must_have(vals, (
        "    authorizationServer:\n"
        "      authorizationEndpoint: https://github.com/login/oauth/authorize\n"
        "      clientCredentialsSecretRef:\n"
        "        clientIdKey: client-id\n"
        "        clientSecretKey: client-secret\n"
        "        name: workspace-github\n"
        "        namespace: default\n"
        "      expectedIssuer: https://github.com/login/oauth\n"
        "      issuer: https://github.com/apps/ci-agents\n"
        "      tokenEndpoint: https://github.com/login/oauth/access_token\n",
    ), "the release values with workspaces")
    ok("with workspaces on the registration is pinned to the workspace-manager's github App: its issuer, GitHub's endpoints and iss, its client Secret")

    # --- own values stand ------------------------------------------------------
    _, vals, _ = release(meta, [*ON, *IDENTITY, *WORKSPACES,
                                "--set", "toolchain-manager.auth.dex.issuerURL=https://own.example.com",
                                "--set", "toolchain-manager.auth.trustedAudiences={own}",
                                "--set", "toolchain-manager.muster.mcpServer.authorizationServer.issuer=https://github.com/apps/own",
                                "--set", "toolchain-manager.muster.mcpServer.enabled=false"])
    must_have(vals, ("    issuerURL: https://own.example.com\n", "  trustedAudiences:\n  - own\n",
                     "      issuer: https://github.com/apps/own\n", "    enabled: false\n"), "the release values with own keys")
    must_lack(vals, ("ci-agents", "  - platform\n"), "the release values with own keys")
    ok("own issuer, trusted audiences, pin and registration switch stand")

    # --- additionalSources ------------------------------------------------------
    _, vals, _ = release(meta, [*ON, *IDENTITY,
                                "--set", "toolchain-manager.additionalSources[0].repository=acme/toolchains",
                                "--set", "toolchain-manager.additionalSources[0].registry=registry.acme.example/toolchains"])
    must_have(vals, ("sources:\n- registry: gsoci.azurecr.io/giantswarm/agent-toolchains\n  repository: giantswarm/agent-toolchains\n"
                     "- registry: registry.acme.example/toolchains\n  repository: acme/toolchains\n",), "the release values with additionalSources")
    must_lack(vals, ("additionalSources",), "the release values with additionalSources")
    ok("additionalSources follow sources, and the key itself is not forwarded")

    # --- muster off --------------------------------------------------------------
    d = documents(helm(meta, [*ON, *IDENTITY, "--set", "components.muster.enabled=false"]))
    hr = d.get(("HelmRelease", NAME))
    if not hr or "dependsOn" in hr:
        fail(f"with muster off the {NAME} release must render without dependsOn:\n{hr}")
    must_have(values(hr), ("muster:\n  mcpServer:\n    enabled: false\n",), "the release values with muster off")
    ok("muster off: the registration off, no dependsOn on a release that does not exist")

    # --- agent-manager asks list_toolchains by muster's name ------------------------
    AGENTS = ["--set", "components.kagent.enabled=true", "--set", "components.agent-manager.enabled=true"]
    PREFIX = "  toolchainsToolsPrefix: x_toolchain-manager_\n"
    d = documents(helm(meta, [*ON, *IDENTITY, *AGENTS]))
    must_have(values(d[("HelmRelease", "agent-manager")]), (PREFIX,), "the agent-manager release values with the component on")
    d = documents(helm(meta, [*IDENTITY, *AGENTS]))
    must_lack(values(d[("HelmRelease", "agent-manager")]), ("toolchainsToolsPrefix",), "the agent-manager release values with the component off")
    d = documents(helm(meta, [*ON, *IDENTITY, *AGENTS, "--set", "toolchain-manager.muster.mcpServer.name=toolchains"]))
    must_have(values(d[("HelmRelease", "agent-manager")]), ("  toolchainsToolsPrefix: x_toolchains_\n",), "the agent-manager release values with a renamed MCPServer")
    d = documents(helm(meta, [*ON, *IDENTITY, *AGENTS, "--set", "components.agent-manager.versionRange=>=2.13.0 <3.0.0"]))
    must_lack(values(d[("HelmRelease", "agent-manager")]), ("toolchainsToolsPrefix",), "the agent-manager release values below the 2.14.0 floor")
    if "differs from the prefix muster gives" not in helm(meta, [*ON, *IDENTITY, *AGENTS, "--set", "agent-manager.muster.toolchainsToolsPrefix=x_other_"], expect_fail=True):
        fail("an own agent-manager.muster.toolchainsToolsPrefix that differs failed for the wrong reason")
    ok("agent-manager's muster.toolchainsToolsPrefix is x_<the MCPServer name>_ while the component is on, from agent-manager 2.14.0; none off or below the floor; an own value that differs fails")

    # --- guards --------------------------------------------------------------------
    for flags, needle, what in (
        ([*ON], "needs the platform's login", "no issuer"),
        ([*ON, "--set", "global.identity.issuerUrl=https://dex.ci.example.com"], "needs the platform's OAuth client", "no OAuth client"),
        ([*ON, *IDENTITY, "--set", "toolchain-manager.additionalSources[0].repository=giantswarm/agent-toolchains",
          "--set", "toolchain-manager.additionalSources[0].registry=x"], "named twice", "a repository named twice"),
    ):
        if needle not in helm(meta, flags, expect_fail=True):
            fail(f"the guard for {what} failed for the wrong reason")
        ok(f"guard: {what}")

    # --- the BOM pin ----------------------------------------------------------------
    pin = re.search(rf'^\s*{NAME}:\s*\{{\s*versionRange:\s*"([^"]+)"', open(f"{meta}/examples/customer-bom.yaml").read(), re.M)
    if not pin:
        fail(f"examples/customer-bom.yaml does not pin components.{NAME}")
    d = documents(helm(meta, ["-f", f"{meta}/examples/customer-bom.yaml", *ON, *IDENTITY]))
    if f'semver: "{pin.group(1)}"' not in d[("OCIRepository", NAME)]:
        fail(f"the BOM pin {pin.group(1)} did not reach the OCIRepository")
    ok(f"the BOM pins {pin.group(1)} and the pin reaches the OCIRepository")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2]))
