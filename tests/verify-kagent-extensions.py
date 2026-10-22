#!/usr/bin/env python3
"""Assert the kagent controller run as the kagent-extensions binary
(kagentExtensions; agent-platform.kagent.extensionsPostRenderer).

Each case pins one property:

- off by default: the kagent HelmRelease carries no patch on the controller
  Deployment;
- on: one kustomize strategic-merge patch on Deployment kagent-controller sets
  the container controller's image to the binary's and its
  KAGENT_EXTENSIONS_* environment from the controller's own settings: the
  issuer and key set of controller.auth with kagentExtensions.audiences, the
  user ID claim, the groups claim and the prefixes, the namespace selector and
  the included namespaces, the machine audience, workspace-manager's
  ServiceAccount as the machine caller while workspaces are on (following
  workspace-manager.serviceAccount.name and its release namespace), and
  klaus-gateway's ServiceAccount as the session reader while refusal notices
  are on; an installation's own authorization keys stand;
- every variable the patch sets names a flag of the binary;
- the connectivity release never receives kagentExtensions, which its root
  schema refuses;
- the render fails naming the key without an image tag, without an audience,
  in the noop authorization mode and in the insecure authentication mode;
- the kagent chart at the range's floor, rendered with the forwarded values,
  renders that Deployment with a container controller, and the patch merged
  by name replaces its image and adds the binary's variables beside the
  chart's own.

Usage: verify-kagent-extensions.py <meta chart dir> <connectivity chart dir>
Network: gsoci.azurecr.io. Needs PyYAML. HELM selects the binary.
"""

import importlib.util
import json
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
IMAGE = "gsoci.azurecr.io/giantswarm/kagent-extensions:0.1.0"
ON = ["--set", "kagentExtensions.enabled=true", "--set", "kagentExtensions.image.tag=0.1.0",
      "--set", "kagentExtensions.audiences={oauth2-proxy,backstage,muster}"]
REFUSALS = ["--set", "components.klaus-gateway.enabled=true", "--set", "klausGateway.slack.enabled=true",
            "--set", "klausGateway.slack.secretName=slack"]
# The binary's flags (giantswarm/kagent-extensions README, Configuration), as
# the variables that set them.
FLAGS = {f"KAGENT_EXTENSIONS_{name}" for name in (
    "OIDC_ISSUERS", "OIDC_USERNAME_CLAIM", "OIDC_GROUPS_CLAIM", "OIDC_USERNAME_PREFIX", "OIDC_GROUPS_PREFIX",
    "MACHINE_AUDIENCE", "MACHINE_CALLER", "SESSION_READER", "TOKEN_REVIEW_CACHE_TTL", "AUTHORIZATION_MODE",
    "NAMESPACE_SELECTOR", "INCLUDE_NAMESPACE", "AUTHORIZATION_DECISION_TTL")}


def kagent_release(flags: list) -> dict:
    docs = [d for d in yaml.safe_load_all(helm(CHART, [*BASE, *flags])) if d]
    hr = find(docs, "HelmRelease", "kagent")
    if hr is None:
        fail(f"no kagent HelmRelease rendered: {' '.join(flags)}")
    return hr


def controller_patches(hr: dict) -> list:
    return [yaml.safe_load(p["patch"]) for pr in hr["spec"].get("postRenderers") or []
            for p in (pr.get("kustomize") or {}).get("patches") or []
            if p.get("target") == {"kind": "Deployment", "name": DEPLOYMENT}]


def controller_patch(flags: list) -> dict:
    patches = controller_patches(kagent_release(flags))
    if len(patches) != 1:
        fail(f"{len(patches)} patches on Deployment {DEPLOYMENT}, want one: {' '.join(flags)}")
    patch = patches[0]
    expect((patch.get("kind"), patch.get("metadata")), ("Deployment", {"name": DEPLOYMENT}),
           "the patch is a strategic-merge patch on the controller Deployment")
    containers = patch["spec"]["template"]["spec"]["containers"]
    expect([c["name"] for c in containers], ["controller"], "the patch names the container controller only")
    return containers[0]


def env(container: dict) -> dict:
    return {e["name"]: e["value"] for e in container.get("env") or []}


def must_fail(flags: list, needle: str, what: str) -> None:
    err = helm(CHART, [*BASE, *flags], expect_fail=True)
    if needle not in err:
        fail(f"{what} failed for another reason (expected {needle!r}):\n{err}")
    ok(f"{what} fails the render naming {needle}")


def merge_by_name(deployment: dict, container_patch: dict) -> dict:
    """The controller container with the patch merged as a strategic merge
    does: image replaced, env merged by name."""
    containers = deployment["spec"]["template"]["spec"]["containers"]
    base = next((c for c in containers if c["name"] == container_patch["name"]), None)
    if base is None:
        fail(f"the kagent chart renders no container {container_patch['name']} in Deployment {DEPLOYMENT}")
    merged = dict(base, image=container_patch["image"])
    variables = {e["name"]: e for e in base.get("env") or []}
    for e in container_patch["env"]:
        variables[e["name"]] = e
    merged["env"] = list(variables.values())
    return merged


def main() -> None:
    expect(controller_patches(kagent_release([])), [], "off by default: no patch on the controller")

    c = controller_patch([*ON, *WORKSPACES])
    expect(c["image"], IMAGE, "the controller runs the binary's image")
    want = {
        "KAGENT_EXTENSIONS_OIDC_ISSUERS": [{"issuer": "https://dex.ci.example.com",
                                            "audiences": ["oauth2-proxy", "backstage", "muster"],
                                            "jwksURL": ka.DEX_KEYS}],
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
    unknown = set(env(c)) - FLAGS
    expect(unknown, set(), "every variable names a flag of the binary")
    ok("on: the controller runs the binary with the controller's settings")

    got = env(controller_patch([*ON, *WORKSPACES, "--set", "workspace-manager.serviceAccount.name=wm",
                                "--set", "components.workspace-manager.targetNamespace=workspaces"]))
    expect(got["KAGENT_EXTENSIONS_MACHINE_CALLER"], "workspaces/wm",
           "the machine caller follows workspace-manager's ServiceAccount and release namespace")
    expect(env(controller_patch(ON))["KAGENT_EXTENSIONS_MACHINE_CALLER"], "", "without workspaces no machine caller")
    ok("the machine caller is workspace-manager's ServiceAccount while workspaces are on")

    got = env(controller_patch([*ON, *REFUSALS]))
    expect(got["KAGENT_EXTENSIONS_SESSION_READER"], f"{ka.NAMESPACE}/klaus-gateway",
           "with refusal notices the session reader is klaus-gateway's ServiceAccount")
    ok("the session reader is klaus-gateway's ServiceAccount while refusal notices are on")

    got = env(controller_patch([*ON, "--set", f"{ka.AUTHZ}.groupsPrefix=oidc:", "--set", f"{ka.AUTHZ}.usernamePrefix=oidc:",
                                "--set", f"{ka.AUTHZ}.includeNamespaces={{kagent,shared}}"]))
    expect((got["KAGENT_EXTENSIONS_OIDC_GROUPS_PREFIX"], got["KAGENT_EXTENSIONS_OIDC_USERNAME_PREFIX"],
            got["KAGENT_EXTENSIONS_INCLUDE_NAMESPACE"]), ("oidc:", "oidc:", "kagent,shared"),
           "an installation's own authorization keys stand")
    ok("an installation's own authorization keys reach the binary")

    docs = [d for d in yaml.safe_load_all(helm(CHART, [*BASE, *ON])) if d]
    connectivity = [d for d in docs if d.get("kind") == "HelmRelease" and "connectivity" in d["metadata"]["name"]]
    if len(connectivity) != 1:
        fail(f"{len(connectivity)} connectivity HelmReleases rendered, want one")
    expect("kagentExtensions" in connectivity[0]["spec"]["values"], False, "the connectivity release does not receive kagentExtensions")
    ok("the connectivity release never receives kagentExtensions")

    must_fail(["--set", "kagentExtensions.enabled=true", "--set", "kagentExtensions.audiences={x}"],
              "kagentExtensions.image.tag", "on without an image tag")
    must_fail(["--set", "kagentExtensions.enabled=true", "--set", "kagentExtensions.image.tag=0.1.0"],
              "kagentExtensions.audiences", "on without an audience")
    must_fail([*ON, "--set", f"{ka.AUTHZ}.mode=noop"], "kagent.controller.authorization.mode", "on in the noop authorization mode")
    must_fail([*ON, "--set", f"{ka.AUTH}.mode=insecure"], "kagent.controller.auth.mode", "on in the insecure authentication mode")

    hr = kagent_release([*ON, *WORKSPACES])
    floor = cc.floor_chart("kagent", ka.KAGENT_CHART, f">={ka.kagent_floor()}")
    docs = ka.kagent_chart_render(hr["spec"]["values"], floor)
    deployment = find(docs, "Deployment", DEPLOYMENT)
    if deployment is None:
        fail(f"the kagent chart {floor} renders no Deployment {DEPLOYMENT}")
    patch = controller_patches(hr)[0]["spec"]["template"]["spec"]["containers"][0]
    base = next(c for c in deployment["spec"]["template"]["spec"]["containers"] if c["name"] == "controller")
    merged = merge_by_name(deployment, patch)
    expect(merged["image"], IMAGE, f"the kagent chart {floor}'s controller takes the binary's image")
    names = [e["name"] for e in merged["env"]]
    missing = [e["name"] for e in base.get("env") or [] if e["name"] not in names]
    expect(missing, [], f"the kagent chart {floor}'s own variables stay")
    expect(sorted(n for n in names if n.startswith("KAGENT_EXTENSIONS_")), sorted(env(patch)),
           "the binary's variables are added")
    expect(merged.get("envFrom"), base.get("envFrom"), "the controller ConfigMap stays mounted")
    ok(f"the kagent chart {floor} renders Deployment {DEPLOYMENT}, whose controller the patch turns into the binary")


if __name__ == "__main__":
    main()
