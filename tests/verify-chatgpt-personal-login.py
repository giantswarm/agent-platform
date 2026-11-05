#!/usr/bin/env python3
"""Assert the wiring of a person's own ChatGPT login (giantswarm/giantswarm#38731,
agent-platform.chatgpt.personalLogin): with chatgptPersonalLogin.enabled, the
codex turns of a ModelConfig with credentialType chatgptOAuth and no
apiKeySecret run on the subscription the Session's person connected in muster.

- the meta chart (tests/fixtures/chatgpt-personal-login-values.yaml over the
  workspaces fixtures, Substrate on): the muster release keeps the provider App
  chatgpt (flow chatgptDeviceCode) after the workspace provider Apps; the kagent
  release carries the installation provider chatgpt (host chatgpt.com, path
  prefix /backend-api/codex/, scheme Bearer); an own key of that entry and an
  own muster App named chatgpt stand; with the switch off nothing of it
  renders; the component charts at the floors take the forwarded values
  (muster 5.52.5 keeps the App, kagent 1.24.0's controller lists the provider);
- the meta chart's guards name the switch: muster's token encryption off, its
  OAuth client off, the credential provider off (workspaces off), the kagent,
  kagent-crds and muster floors below 1.24.0, 1.24.0 and 5.52.5;
- the muster chart at its floor refuses the App without the encryption key.

Deliberately stdlib-only: the CI image has no PyYAML. HELM selects the binary.
"""

import os
import re
import subprocess
import sys
import tempfile

HELM = os.environ.get("HELM", "helm")
CI = ["--set", "components.flux.enabled=false"]
ON = ["-f", "tests/fixtures/workspaces-values.yaml", "-f", "tests/fixtures/workspaces-agents-values.yaml",
      "--set", "components.substrate.enabled=true", "-f", "tests/fixtures/chatgpt-personal-login-values.yaml",
      # The identity the component charts need with OAuth on.
      "--set", "global.domain=ci.example.com",
      "--set", "global.identity.issuerUrl=https://dex.ci.example.com",
      "--set", "global.identity.clientId=platform",
      "--set", "global.identity.existingSecret=platform-oauth",
      "--set", "muster.muster.oauth.server.existingSecret=platform-oauth"]
MUSTER_CHART = ("oci://gsoci.azurecr.io/charts/giantswarm/muster", "5.52.5")
KAGENT_CHART = ("oci://gsoci.azurecr.io/giantswarm/kagent/helm/kagent", "1.24.0-rc.1")
PROVIDER = "chatgpt:\n{i}  hostname: chatgpt.com\n{i}  pathPrefix: /backend-api/codex/\n{i}  scheme: Bearer\n"
APP = "- flow: chatgptDeviceCode\n{i}  name: chatgpt\n"



def fail(msg: str) -> None:
    sys.exit(f"FAIL: {msg}")


def ok(msg: str) -> None:
    print(f"ok: {msg}")


def run(cmd: list, expect_fail: bool = False) -> str:
    r = subprocess.run(cmd, capture_output=True, text=True)
    if expect_fail:
        if r.returncode == 0:
            fail(f"the render passed but had to fail: {' '.join(cmd)}")
        return r.stderr
    if r.returncode != 0:
        fail(f"the render failed: {' '.join(cmd)}\n{r.stderr}")
    return r.stdout


def meta(chart: str, flags: list, expect_fail: bool = False) -> str:
    return run([HELM, "template", "t", chart, "-f", f"{chart}/ci/ci-values.yaml", *CI, *flags], expect_fail)


def must_fail(err: str, needle: str, what: str) -> None:
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


def values(render: str, name: str) -> str:
    return block(release(render, name), "values", 2)


def providers(render: str) -> str:
    """controller.session.actorJWTProviders of the kagent release."""
    return block(block(block(values(render, "kagent"), "controller", 4), "session", 6), "actorJWTProviders", 8)


def component_render(hr: str, chart: tuple) -> str:
    """`helm template` of a component chart with the values block the HelmRelease forwards."""
    ref, version = chart
    vals = block(hr, "values", 2)
    with tempfile.TemporaryDirectory() as tmp:
        with open(f"{tmp}/values.yaml", "w", encoding="utf-8") as f:
            f.write("".join(line[4:] + "\n" for line in vals.splitlines()[1:]))
        name = ref.rsplit("/", 1)[1]
        run([HELM, "pull", ref, "--version", version, "--untar", "-d", tmp])
        return run([HELM, "template", name, f"{tmp}/{name}", "-n", "default", "-f", f"{tmp}/values.yaml"])


def main(chart: str) -> int:
    # --- the meta chart derives the App and the provider ----------------------------------
    on = meta(chart, ON)
    musterv = block(values(on, "muster"), "muster", 4)
    apps = block(musterv, "providerApps", 6)
    if not apps.endswith(APP.format(i="      ")):
        fail(f"the muster release's providerApps do not end with the chatgpt App:\n{apps}")
    want = PROVIDER.format(i="          ")
    if want not in providers(on):
        fail(f"the kagent release lacks the chatgpt provider:\n{values(on, 'kagent')}")
    if "chatgpt" in block(values(on, "agent-platform-connectivity"), "kagent", 4):
        fail("the connectivity release carries the chatgpt provider; nothing there reads it")
    ok("chatgptPersonalLogin.enabled derives muster's App chatgpt and kagent's provider chatgpt for the kagent release")

    own = meta(chart, [*ON, "--set", "kagent.controller.session.actorJWTProviders.chatgpt.pathPrefix=/backend-api/",
                       "--set", "muster.muster.providerApps[0].name=chatgpt",
                       "--set", "muster.muster.providerApps[0].flow=chatgptDeviceCode",
                       "--set", "muster.muster.providerApps[0].issuer=https://auth.example.com"])
    if "pathPrefix: /backend-api/\n" not in providers(own):
        fail(f"an own pathPrefix of the chatgpt provider does not stand:\n{providers(own)}")
    apps = block(block(values(own, "muster"), "muster", 4), "providerApps", 6)
    if apps.count("name: chatgpt") != 1 or "issuer: https://auth.example.com" not in apps:
        fail(f"an own muster App chatgpt does not stand in place of the derived one:\n{apps}")
    ok("an own key of the provider entry and an own muster App chatgpt stand")

    off = meta(chart, [*ON, "--set", "chatgptPersonalLogin.enabled=false"])
    if "chatgptDeviceCode" in off or "/backend-api/codex/" in off:
        fail("chatgptPersonalLogin.enabled=false derives the person's App or provider")
    ok("with the switch off nothing is derived")

    config = component_render(release(on, "muster"), MUSTER_CHART)
    if not re.search(r"providerApps:\n\s+- flow: chatgptDeviceCode\n\s+name: chatgpt", config):
        fail(f"the muster chart {MUSTER_CHART[1]} does not keep the chatgpt App:\n{config}")
    controller = component_render(release(on, "kagent"), KAGENT_CHART).replace('\\"', '"')
    if '"chatgpt":{"hostname":"chatgpt.com","pathPrefix":"/backend-api/codex/","scheme":"Bearer"}' not in controller:
        fail(f"the kagent chart {KAGENT_CHART[1]} does not list the chatgpt provider in KAGENT_SESSION_ACTOR_JWT_PROVIDERS")
    ok(f"the muster chart {MUSTER_CHART[1]} keeps the App; the kagent chart {KAGENT_CHART[1]} lists the provider")

    # --- the meta chart's guards ----------------------------------------------------------
    what = "chatgptPersonalLogin.enabled (a person's own ChatGPT login)"
    for flags, needle, case in (
        (["--set", "muster.muster.oauth.server.encryptionKey=false"], "muster.muster.oauth.server.encryptionKey is off", "token encryption off"),
        (["--set", "muster.muster.oauth.mcpClient.enabled=false"], "muster.muster.oauth.mcpClient.enabled is off", "muster's OAuth client off"),
        (["--set", "workspaces.enabled=false"], "the credential provider is off", "the credential provider off"),
        (["--set", "components.kagent.versionRange=>=1.23.0 <1.25.0", "--set", "kagent.controller.image.repository=giantswarm/kagent/controller"], "components.kagent.versionRange (>=1.23.0 <1.25.0) admits releases before 1.24.0", "the kagent floor"),
        (["--set", "components.kagent-crds.versionRange=>=1.23.0 <1.25.0", "--set", "kagent.controller.image.repository=giantswarm/kagent/controller"], "components.kagent-crds.versionRange (>=1.23.0 <1.25.0) admits releases before 1.24.0", "the kagent-crds floor"),
        (["--set", "components.muster.versionRange=>=5.50.0 <6.0.0"], "components.muster.versionRange (>=5.50.0 <6.0.0) admits releases before 5.52.5", "the muster floor"),
    ):
        err = meta(chart, [*ON, *flags], expect_fail=True)
        must_fail(err, what, case)
        must_fail(err, needle, case)
    ok("the guards: token encryption, muster's OAuth client, the credential provider, the kagent, kagent-crds and muster floors")

    # --- the muster chart refuses the App without the key ---------------------------------
    with tempfile.TemporaryDirectory() as tmp:
        run([HELM, "pull", MUSTER_CHART[0], "--version", MUSTER_CHART[1], "--untar", "-d", tmp])
        err = run([HELM, "template", "muster", f"{tmp}/muster", "--set", "muster.oauth.mcpClient.enabled=true",
                   "--set", "muster.providerApps[0].name=chatgpt", "--set", "muster.providerApps[0].flow=chatgptDeviceCode"], expect_fail=True)
        must_fail(err, "needs muster.oauth.server.encryptionKey", "the muster chart without the key")
    ok(f"the muster chart {MUSTER_CHART[1]} refuses the App without muster.oauth.server.encryptionKey")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
