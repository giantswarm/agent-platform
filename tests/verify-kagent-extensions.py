#!/usr/bin/env python3
"""Assert the kagent controller run as the kagent-extensions binary
(kagent.controller.image.repository giantswarm/kagent/controller-extensions;
agent-platform.kagent.extensions).

Each case pins one property:

- off by default: the kagent release keeps the controller's own repository and
  carries no KAGENT_EXTENSIONS_* variable;
- on: with the controller's repository giantswarm/kagent/controller-extensions,
  the kagent release's controller.env sets the binary's KAGENT_EXTENSIONS_*
  variables from the controller's own settings: the issuer and key set of
  controller.auth with no audience (a token of any of its clients),
  the user ID claim, the groups claim and the prefixes, the namespace selector
  and the included namespaces, the machine audience, workspace-manager's
  ServiceAccount as the machine caller while workspaces are on (following
  workspace-manager.serviceAccount.name and its release namespace), and
  klaus-gateway's ServiceAccount as the session reader while refusal notices
  are on; an installation's own authorization keys stand;
- an own entry of kagent.controller.env stands, first and once;
- every variable the chart sets names a flag of the binary;
- the render fails naming the key in the noop authorization mode, in the
  insecure authentication mode and with a kagent range below
  agent-platform.kagent.extensionsFloor;
- the kagent chart of the floor release (until it is published, the newest
  release before it, which renders the controller the same way), rendered
  with the release's values, runs controller-extensions at the chart's own
  release with the binary's variables beside the chart's own, and the
  controller ConfigMap's IMAGE_TAG, which the binary checks its release
  against, names the same release.

Usage: verify-kagent-extensions.py <meta chart dir> <connectivity chart dir>
Network: gsoci.azurecr.io. Needs PyYAML. HELM selects the binary.
"""

import importlib.util
import json
import re
import os
import sys

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))


def load(name: str, path: str):
    spec = importlib.util.spec_from_file_location(name, os.path.join(HERE, path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ka = load("kagent_authorization", "verify-kagent-authorization.py")
cc = ka.cc
fail, ok, expect, find, helm = ka.fail, ka.ok, ka.expect, ka.find, ka.helm
CHART, BASE, WORKSPACES = ka.CHART, ka.BASE, ka.WORKSPACES

DEPLOYMENT = "kagent-controller"
REPOSITORY = "giantswarm/kagent/controller-extensions"
FLOOR = "1.26.2"
BELOW_FLOOR = "1.26.1"
RANGE = ["--set", f"components.kagent.versionRange=>={FLOOR} <1.27.0"]
SWITCH = ["--set", f"kagent.controller.image.repository={REPOSITORY}"]
ON = [*SWITCH, *RANGE]
REFUSALS = ["--set", "components.klaus-gateway.enabled=true", "--set", "klausGateway.slack.enabled=true",
            "--set", "klausGateway.slack.secretName=slack"]
# The binary's flags (giantswarm/kagent-extensions README, Configuration), as
# the variables that set them.
FLAGS = {f"KAGENT_EXTENSIONS_{name}" for name in (
    "OIDC_ISSUERS", "OIDC_USERNAME_CLAIM", "OIDC_GROUPS_CLAIM", "OIDC_USERNAME_PREFIX", "OIDC_GROUPS_PREFIX",
    "MACHINE_AUDIENCE", "MACHINE_CALLER", "SESSION_READER", "TOKEN_REVIEW_CACHE_TTL", "AUTHORIZATION_MODE",
    "NAMESPACE_SELECTOR", "INCLUDE_NAMESPACE", "AUTHORIZATION_DECISION_TTL", "ALLOW_VERSION_SKEW")}


def kagent_release(flags: list) -> dict:
    docs = [d for d in yaml.safe_load_all(helm(CHART, [*BASE, *flags])) if d]
    hr = find(docs, "HelmRelease", "kagent")
    if hr is None:
        fail(f"no kagent HelmRelease rendered: {' '.join(flags)}")
    return hr


def controller(flags: list) -> dict:
    return kagent_release(flags)["spec"]["values"].get("controller") or {}


def env(values: dict) -> dict:
    return {e["name"]: e.get("value") for e in values.get("env") or [] if e["name"].startswith("KAGENT_EXTENSIONS_")}


def must_fail(flags: list, needle: str, what: str) -> None:
    err = helm(CHART, [*BASE, *flags], expect_fail=True)
    if needle not in err:
        fail(f"{what} failed for another reason (expected {needle!r}):\n{err}")
    ok(f"{what} fails the render naming {needle}")


def main() -> None:
    c = controller([])
    expect((c.get("image") or {}).get("repository"), "giantswarm/kagent/controller", "off by default: the controller's own repository")
    expect(env(c), {}, "off by default: no variable of the binary")
    ok("off by default: the controller is the kagent line's own")

    c = controller([*ON, *WORKSPACES])
    want = {
        "KAGENT_EXTENSIONS_OIDC_ISSUERS": [{"issuer": "https://dex.ci.example.com", "jwksURL": ka.DEX_KEYS}],
        "KAGENT_EXTENSIONS_OIDC_USERNAME_CLAIM": "email",
        "KAGENT_EXTENSIONS_OIDC_GROUPS_CLAIM": "groups",
        "KAGENT_EXTENSIONS_OIDC_USERNAME_PREFIX": "",
        "KAGENT_EXTENSIONS_OIDC_GROUPS_PREFIX": "",
        "KAGENT_EXTENSIONS_AUTHORIZATION_MODE": "kubernetes",
        "KAGENT_EXTENSIONS_NAMESPACE_SELECTOR": "giantswarm.io/organization",
        "KAGENT_EXTENSIONS_INCLUDE_NAMESPACE": "kagent",
        "KAGENT_EXTENSIONS_MACHINE_AUDIENCE": "kagent",
        "KAGENT_EXTENSIONS_MACHINE_CALLER": f"{ka.NAMESPACE}/workspace-manager",
        "KAGENT_EXTENSIONS_SESSION_READER": "",
    }
    got = env(c)
    got["KAGENT_EXTENSIONS_OIDC_ISSUERS"] = json.loads(got.get("KAGENT_EXTENSIONS_OIDC_ISSUERS", "null"))
    expect(got, want, "the binary's settings are the controller's own")
    expect(set(env(c)) - FLAGS, set(), "every variable names a flag of the binary")
    ok("on: the controller runs the binary with the controller's settings")

    got = env(controller([*ON, *WORKSPACES, "--set", "workspace-manager.serviceAccount.name=wm",
                          "--set", "components.workspace-manager.targetNamespace=workspaces"]))
    expect(got["KAGENT_EXTENSIONS_MACHINE_CALLER"], "workspaces/wm",
           "the machine caller follows workspace-manager's ServiceAccount and release namespace")
    expect(env(controller(ON))["KAGENT_EXTENSIONS_MACHINE_CALLER"], "", "without workspaces no machine caller")
    ok("the machine caller is workspace-manager's ServiceAccount while workspaces are on")

    got = env(controller([*ON, *REFUSALS]))
    expect(got["KAGENT_EXTENSIONS_SESSION_READER"], f"{ka.NAMESPACE}/klaus-gateway",
           "with refusal notices the session reader is klaus-gateway's ServiceAccount")
    ok("the session reader is klaus-gateway's ServiceAccount while refusal notices are on")

    got = env(controller([*ON, "--set", f"{ka.AUTHZ}.groupsPrefix=oidc:", "--set", f"{ka.AUTHZ}.usernamePrefix=oidc:",
                          "--set", f"{ka.AUTHZ}.includeNamespaces={{kagent,shared}}"]))
    expect((got["KAGENT_EXTENSIONS_OIDC_GROUPS_PREFIX"], got["KAGENT_EXTENSIONS_OIDC_USERNAME_PREFIX"],
            got["KAGENT_EXTENSIONS_INCLUDE_NAMESPACE"]), ("oidc:", "oidc:", "kagent,shared"),
           "an installation's own authorization keys stand")
    ok("an installation's own authorization keys reach the binary")

    c = controller([*ON, "--set", "kagent.controller.env[0].name=KAGENT_EXTENSIONS_SESSION_READER",
                    "--set", "kagent.controller.env[0].value=ops/reader", "--set", "kagent.controller.env[1].name=LOG_LEVEL",
                    "--set", "kagent.controller.env[1].value=debug"])
    names = [e["name"] for e in c["env"]]
    expect(names[:2], ["KAGENT_EXTENSIONS_SESSION_READER", "LOG_LEVEL"], "own entries keep their place, first")
    expect(names.count("KAGENT_EXTENSIONS_SESSION_READER"), 1, "an own variable of the binary is not set twice")
    expect(env(c)["KAGENT_EXTENSIONS_SESSION_READER"], "ops/reader", "an own variable of the binary stands")
    ok("an own entry of kagent.controller.env stands")

    must_fail([*ON, "--set", f"{ka.AUTHZ}.mode=noop"], "kagent.controller.authorization.mode", "on in the noop authorization mode")
    must_fail([*ON, "--set", f"{ka.AUTH}.mode=insecure"], "kagent.controller.auth.mode", "on in the insecure authentication mode")
    must_fail([*ON, "--set", f"components.kagent.versionRange=>={BELOW_FLOOR} <{FLOOR}"],
              "components.kagent.versionRange", f"on with a kagent range below {FLOOR}")
    if "KAGENT_EXTENSIONS_OIDC_ISSUERS" not in env(controller(SWITCH)):
        fail("on with the default kagent range, the binary's variables are missing")
    ok("the default kagent range is at the floor: the switch alone runs the binary")

    hr = kagent_release([*ON, *WORKSPACES])
    tags = cc.registry_tags(ka.KAGENT_CHART)
    releases = [t for t in tags if re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", t)]
    key = lambda v: tuple(int(x) for x in v.split("."))
    chart = FLOOR if FLOOR in tags else max((r for r in releases if key(r) < key(FLOOR)), key=key)
    if chart != FLOOR:
        print(f"NOTE: kagent {FLOOR} is not published yet; rendering the kagent chart {chart}")
    docs = ka.kagent_chart_render(hr["spec"]["values"], chart)
    deployment = find(docs, "Deployment", DEPLOYMENT)
    if deployment is None:
        fail(f"the kagent chart {chart} renders no Deployment {DEPLOYMENT}")
    container = next((c for c in deployment["spec"]["template"]["spec"]["containers"] if c["name"] == "controller"), None)
    if container is None:
        fail(f"the kagent chart {chart} renders no container controller in Deployment {DEPLOYMENT}")
    expect(container["image"], f"gsoci.azurecr.io/{REPOSITORY}:{chart}",
           f"the kagent chart {chart}'s controller runs controller-extensions of its own release")
    names = [e["name"] for e in container.get("env") or []]
    expect("KAGENT_NAMESPACE" in names, True, f"the kagent chart {chart}'s own variables stay")
    expect(sorted(n for n in names if n.startswith("KAGENT_EXTENSIONS_")), sorted(env(hr["spec"]["values"]["controller"])),
           "the binary's variables are set")
    config = find(docs, "ConfigMap", DEPLOYMENT)
    if config is None:
        fail(f"the kagent chart {chart} renders no ConfigMap {DEPLOYMENT}")
    expect(config["data"].get("IMAGE_TAG"), chart, "the controller ConfigMap's IMAGE_TAG names the chart's release")
    ok(f"the kagent chart {chart} runs controller-extensions {chart} with the binary's settings")


if __name__ == "__main__":
    main()
