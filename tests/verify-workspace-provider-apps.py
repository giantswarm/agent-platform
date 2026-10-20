#!/usr/bin/env python3
"""Assert muster's provider Apps of the workspace provider instances, GitHub and GitLab
sign-in side by side (giantswarm/giantswarm#38672, agent-platform.workspaceManager.providerApps).

- two instances with person sign-in (tests/fixtures/workspaces-provider-apps-values.yaml):
  the muster release keeps ONE App per instance (muster.providerApps) — github with the
  pin's own issuer, GitHub's endpoints and iss, the instance's client Secret in the
  workspace-manager's namespace; gitlab with its url as issuer, GitLab's endpoints and
  <url>/oauth/revoke, the
  default scopes and gitUser oauth2 — the workspace-manager release's registration names
  both (muster.mcpServer.auth.providerApps) and pins none (no auth.provider); with
  Substrate on muster's credentialProvider releases both under their issuers, gitlab with
  git user oauth2; the component charts at the BOM's pins take the forwarded values (the
  workspace-manager's MCPServer receives both Apps' grants, muster's configuration keeps
  both Apps and releases them);
- a single instance with sign-in (tests/fixtures/workspaces-values.yaml) renders as
  before: no providerApps anywhere, the registration pinned to the github instance;
- derivation: without an own issuer the github App's is <url>/apps/<app.id> on the
  instance's url (a GitHub Enterprise Server's endpoints too, and no iss, which only
  github.com is known to name); an own scopes value stands;
- own values: an own auth.provider keeps the pin and derives no App; an own
  auth.providerApps names the instances (the pin derived beside it); an own
  muster.muster.providerApps entry stands in place of the derived one;
- the guards name the key: an own providerApps entry naming no instance, an instance
  whose App cannot be derived, a workspace-manager floor below
  agent-platform.workspaceManager.providerAppsFloor (0.12.0) and a muster floor below
  agent-platform.muster.providerAppsFloor (5.48.0).

Deliberately stdlib-only: the CI image has no PyYAML. HELM selects the binary.
"""

import os
import re
import subprocess
import sys
import tempfile

HELM = os.environ.get("HELM", "helm")
NAME = "workspace-manager"
REPOSITORY = "oci://gsoci.azurecr.io/charts/giantswarm"
BOM = "helm/agent-platform/examples/customer-bom.yaml"
CI = ["--set", "components.flux.enabled=false"]
SINGLE = ["-f", "tests/fixtures/workspaces-values.yaml", "-f", "tests/fixtures/workspaces-agents-values.yaml"]
BOTH = [*SINGLE, "-f", "tests/fixtures/workspaces-provider-apps-values.yaml"]
SUBSTRATE = ["--set", "components.substrate.enabled=true"]
# The identity the component charts need with OAuth on.
IDENTITY = [
    "--set", "global.domain=ci.example.com",
    "--set", "global.identity.issuerUrl=https://dex.ci.example.com",
    "--set", "global.identity.clientId=platform",
    "--set", "global.identity.existingSecret=platform-oauth",
    "--set", "muster.muster.oauth.server.existingSecret=platform-oauth",
]
AUTH = f"{NAME}.muster.mcpServer.auth"

GITHUB_APP = """\
      - authorizationEndpoint: https://github.com/login/oauth/authorize
        clientCredentialsSecretRef:
          name: workspace-github
          namespace: default
        expectedIssuer: https://github.com/login/oauth
        issuer: https://github.com/apps/ci-agents
        name: github
        tokenEndpoint: https://github.com/login/oauth/access_token
"""
GITLAB_APP = """\
      - authorizationEndpoint: https://gitlab.example.com:8443/oauth/authorize
        clientCredentialsSecretRef:
          name: workspace-gitlab-oauth
          namespace: default
        gitUser: oauth2
        issuer: https://gitlab.example.com:8443
        name: gitlab
        revocationEndpoint: https://gitlab.example.com:8443/oauth/revoke
        scopes: api read_user read_repository write_repository
        tokenEndpoint: https://gitlab.example.com:8443/oauth/token
"""


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


def must_fail(chart: str, flags: list, needle: str, what: str) -> None:
    err = helm(chart, flags, expect_fail=True)
    if needle not in err:
        fail(f"{what} failed for another reason (expected {needle!r}):\n{err}")


def release(render: str, name: str) -> str:
    for doc in render.split("\n---\n"):
        if re.search(r"^kind: HelmRelease$", doc, re.M) and re.search(rf"^  name: {re.escape(name)}$", doc, re.M):
            return doc.rstrip("\n") + "\n"
    fail(f"no HelmRelease {name} in the render")
    return ""


def block(doc: str, key: str, indent: int) -> str:
    """The lines of `key:` at `indent` spaces in a rendered document, the key line included."""
    m = re.search(rf"^{' ' * indent}{re.escape(key)}:.*\n(?:(?:{' ' * (indent + 1)}.*|{' ' * indent}- .*)?\n)*", doc, re.M)
    return m.group(0) if m else ""


def muster_block(render: str) -> str:
    """The muster chart's own `muster:` block of the muster release's values."""
    return block(block(release(render, "muster"), "values", 2), "muster", 4)


def auth_block(render: str) -> str:
    return block(block(block(block(release(render, NAME), "values", 2), "muster", 4), "mcpServer", 6), "auth", 8)


def must_have(doc: str, lines: tuple, what: str) -> None:
    for line in lines:
        if line not in doc:
            fail(f"{what} lacks {line!r}:\n{doc}")


def bom_pin(name: str) -> str:
    with open(BOM, encoding="utf-8") as f:
        return re.search(rf'^\s*{re.escape(name)}:\s*\{{\s*versionRange:\s*"([^"]+)"', f.read(), re.M).group(1)


def component_render(hr: str, chart: str, version: str) -> str:
    """`helm template` of a component chart at version with the values block the HelmRelease forwards."""
    values = block(hr, "values", 2)
    with tempfile.TemporaryDirectory() as tmp:
        with open(f"{tmp}/values.yaml", "w", encoding="utf-8") as f:
            f.write("".join(line[4:] + "\n" for line in values.splitlines()[1:]))
        r = subprocess.run([HELM, "pull", f"{REPOSITORY}/{chart}", "--version", version, "--untar", "-d", tmp], capture_output=True, text=True)
        if r.returncode != 0:
            fail(f"helm pull {chart} {version}: {r.stderr}")
        r = subprocess.run([HELM, "template", chart, f"{tmp}/{chart}", "-n", "default", "-f", f"{tmp}/values.yaml"], capture_output=True, text=True)
        if r.returncode != 0:
            fail(f"the {chart} chart {version} refuses the forwarded values:\n{r.stderr}")
        return r.stdout


def main(meta: str) -> int:
    # --- two instances with sign-in -------------------------------------------------------
    both = helm(meta, [*BOTH, *SUBSTRATE, *IDENTITY])
    muster = muster_block(both)
    apps = block(muster, "providerApps", 6)
    if apps != "      providerApps:\n" + GITHUB_APP + GITLAB_APP:
        fail(f"the muster release's providerApps are not the github and gitlab Apps:\n{apps}")
    provider = block(muster, "credentialProvider", 6)
    must_have(provider, ("        releasable:\n          github: https://github.com/apps/ci-agents\n          gitlab: https://gitlab.example.com:8443\n",
                         "        gitUsers:\n          gitlab: oauth2\n"), "muster's credentialProvider")
    auth = auth_block(both)
    must_have(auth, ("          providerApps:\n          - github\n          - gitlab\n",), "the workspace-manager's registration")
    if re.search(r"^          provider:", auth, re.M):
        fail(f"the registration is still pinned beside the provider Apps:\n{auth}")
    ok("github and gitlab with sign-in: two muster Apps, one registration naming both and no pin, both releasable, gitlab with git user oauth2")

    wm_pin, muster_pin = bom_pin(NAME), bom_pin("muster")
    server = component_render(release(both, NAME), NAME, wm_pin)
    must_have(server, ("kind: MCPServer", "    forwardToken: true\n    providerApps:\n      - github\n      - gitlab\n"),
              f"the {NAME} chart {wm_pin}'s MCPServer")
    if "authorizationServer:" in server:
        fail(f"the {NAME} chart {wm_pin} renders a pinned MCPServer beside the provider Apps:\n{server}")
    config = component_render(release(both, "muster"), "muster", muster_pin)
    must_have(config, ("providerApps:", "name: gitlab", "revocationEndpoint: https://gitlab.example.com:8443/oauth/revoke",
                       "gitUser: oauth2", "issuer: https://github.com/apps/ci-agents"), f"the muster chart {muster_pin}'s configuration")
    ok(f"the {NAME} chart {wm_pin} registers both Apps' grants unpinned; the muster chart {muster_pin} keeps both Apps")

    # --- one instance with sign-in renders as before --------------------------------------
    single = helm(meta, [*SINGLE, *SUBSTRATE])
    if "providerApps" in single:
        fail("a single instance with sign-in renders providerApps")
    must_have(auth_block(single), ("          provider: github\n",), "the single-sign-in registration")
    must_have(block(muster_block(single), "credentialProvider", 6), ("        releasable:\n          github: https://github.com/apps/ci-agents\n",),
              "the single-sign-in credentialProvider")
    ok("one instance with sign-in: the registration pinned to it as before, no provider Apps")

    # --- derivation -------------------------------------------------------------------------
    derived = muster_block(helm(meta, [*BOTH, "--set", f"{AUTH}.authorizationServer.issuer=",
                                       "--set", f"{NAME}.providers[0].values.url=https://ghe.example.com/",
                                       "--set", f"{NAME}.providers[1].values.oauth.scopes=read_api read_repository"]))
    must_have(derived, ("        issuer: https://ghe.example.com/apps/123\n", "      - authorizationEndpoint: https://ghe.example.com/login/oauth/authorize\n",
                        "        tokenEndpoint: https://ghe.example.com/login/oauth/access_token\n",
                        "        scopes: read_api read_repository\n"), "the derived Apps")
    if "expectedIssuer" in derived:
        fail(f"a GitHub Enterprise Server's App names an iss only github.com is known to send:\n{derived}")
    ok("without an own issuer the github App's is <url>/apps/<app.id> with the instance's endpoints; an own scopes value stands")

    # --- own values -------------------------------------------------------------------------
    pinned = helm(meta, [*BOTH, "--set", f"{AUTH}.provider=github"])
    if "providerApps" in pinned:
        fail("an own auth.provider derives provider Apps")
    must_have(auth_block(pinned), ("          provider: github\n",), "the registration with an own pin")
    named = helm(meta, [*BOTH, "--set", f"{AUTH}.providerApps={{gitlab}}"])
    if block(muster_block(named), "providerApps", 6) != "      providerApps:\n" + GITLAB_APP:
        fail(f"an own auth.providerApps [gitlab] keeps other Apps:\n{muster_block(named)}")
    must_have(auth_block(named), ("          provider: github\n", "          providerApps:\n          - gitlab\n"), "the registration naming its own Apps")
    own = muster_block(helm(meta, [*BOTH, "--set", "muster.muster.providerApps[0].name=gitlab",
                                   "--set", "muster.muster.providerApps[0].issuer=https://gitlab.example.com:8443",
                                   "--set", "muster.muster.providerApps[0].clientCredentialsSecretRef.name=own-gitlab"]))
    apps = block(own, "providerApps", 6)
    if apps.count("name: gitlab") != 1 or "name: own-gitlab" not in apps or "name: github" not in apps:
        fail(f"an own muster App does not stand in place of the derived one:\n{apps}")
    ok("an own pin derives no App; own auth.providerApps name the Apps beside the pin; an own muster App stands")

    # --- guards -----------------------------------------------------------------------------
    must_fail(meta, [*BOTH, "--set", f"{AUTH}.providerApps={{bitbucket}}"], f"{AUTH}.providerApps names \"bitbucket\"", "an App of no instance")
    must_fail(meta, [*BOTH, "--set", f"{AUTH}.authorizationServer.issuer=", "--set", f"{NAME}.providers[0].values.app.id="],
              "muster keeps no App of provider instance github", "a github instance without an App id")
    must_fail(meta, [*BOTH, "--set", f"components.{NAME}.versionRange=>=0.11.0 <0.13.0"],
              f"components.{NAME}.versionRange (>=0.11.0 <0.13.0) admits releases before 0.12.0", "a workspace-manager floor below 0.12.0")
    must_fail(meta, [*BOTH, "--set", "components.muster.versionRange=>=5.47.0 <6.0.0"],
              "components.muster.versionRange (>=5.47.0 <6.0.0) admits releases before 5.48.0", "a muster floor below 5.48.0")
    ok("the guards: an App of no instance, an underivable App, the workspace-manager and muster floors")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
