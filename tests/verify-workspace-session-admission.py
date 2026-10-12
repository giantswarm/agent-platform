#!/usr/bin/env python3
"""Assert kagent's jwt Session admission follows the workspaces switch
(giantswarm/agent-platform#890).

A Session that names a workspace volume is admitted by kagent only with the
workspace-manager's grant: the controller's jwt Session admission
(controller.session.admission.jwt — jwksURL, issuer, audience; the kagent chart from
1.6.0) verifies it against the manager's published key set, with the manager's base
URL as iss, its grant.audience as aud and the caller as sub, and refuses every Session
naming a volume source while the block is unset. The meta chart derives the block onto
the kagent release while workspaces are on and the manager signs grants. Each case
below pins one property:

- nothing by default (the switch off): the kagent release carries no
  controller.session key, and the render is the one without this derivation;
- with the switch on and the installation's own grant signing key
  (tests/fixtures/workspaces-values.yaml) the kagent release carries the three:
  jwksURL the manager's key set through its Service in-cluster
  (http://<workspace-manager.fullnameOverride>.<platform namespace>.svc:<workspace-manager.service.port>/.well-known/jwks.json),
  issuer the manager's resolved base URL (workspace-manager.oauth.baseURL, else
  https://workspace-manager.<global.domain>), audience workspace-manager.grant.audience
  (kagent); NOTES prints them; no null anywhere in the release's values;
- the same with the connectivity release's key (no own reference, providers and
  oauth.enabled); nothing without a grant key: no providers, oauth off,
  grantKey.create false; nothing with kagent off;
- the derivation follows fullnameOverride, service.port, gitops.targetNamespace and
  components.workspace-manager.targetNamespace (the key set), global.domain (the
  issuer) and grant.audience; a value the kagent block sets for one of the three
  stands and the rest is derived;
- without the derivation an own block reaches the release as written, and one set in
  part fails the render naming the keys (the kagent chart needs all three);
- the guards: a kagent range whose floor is below 1.6.0
  (agent-platform.kagent.sessionAdmissionFloor; an exact pin and a release candidate
  of 1.6.0 judged alike), no issuer (neither global.domain nor oauth.baseURL), a
  userIdClaim other than email — each only with the admission derived;
- the kagent chart at the range's floor renders the three KAGENT_SESSION_ADMISSION_JWT_*
  entries of the controller ConfigMap from the forwarded block (network: gsoci).

Deliberately stdlib-only: the CI image has no PyYAML. HELM selects the binary.
"""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

HELM = os.environ.get("HELM", "helm")
NAME = "workspace-manager"
NAMESPACE = "agent-platform"
FIXTURE = "tests/fixtures/workspaces-values.yaml"
KAGENT_CHART = "oci://gsoci.azurecr.io/giantswarm/kagent/helm/kagent"
CI = ["--set", "components.flux.enabled=false"]
ON = ["-f", FIXTURE]
KAGENT = ["--set", "components.kagent.enabled=true", "--set", "kagent.harness.snapshotLocation=s3://ci/agents"]
IDENTITY = [
    "--set", "global.domain=ci.example.com",
    "--set", "global.identity.issuerUrl=https://dex.ci.example.com",
    "--set", "global.identity.clientId=platform",
    "--set", "global.identity.existingSecret=platform-oauth",
]
NO_OWN = ["--set", f"{NAME}.grant.signingKey=null"]
JWT = "kagent.controller.session.admission.jwt"
JWKS = f"http://{NAME}.{NAMESPACE}.svc:8080/.well-known/jwks.json"
ISSUER = "https://workspaces.ci.example.com"
WANT = {"jwksURL": JWKS, "issuer": ISSUER, "audience": "kagent"}
ENV = {"jwksURL": "KAGENT_SESSION_ADMISSION_JWT_JWKS_URL", "issuer": "KAGENT_SESSION_ADMISSION_JWT_ISSUER",
       "audience": "KAGENT_SESSION_ADMISSION_JWT_AUDIENCE"}


def fail(msg: str) -> None:
    sys.exit(f"FAIL: {msg}")


def ok(msg: str) -> None:
    print(f"ok: {msg}")


def helm(chart: str, flags: list, expect_fail: bool = False) -> str:
    cmd = [HELM, "template", "t", chart, "-n", NAMESPACE, "-f", f"{chart}/ci/ci-values.yaml", *CI, *flags]
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


def documents(render: str) -> dict:
    """(kind, name) -> the document, for every document that has both."""
    out = {}
    for doc in render.split("\n---\n"):
        kind = re.search(r"^kind: (\S+)", doc, re.M)
        name = re.search(r"^  name: (\S+)", doc, re.M)
        if kind and name:
            out[(kind.group(1), name.group(1))] = doc.rstrip("\n") + "\n"
    return out


def block(doc: str, key: str, indent: int) -> str:
    """The lines of `key:` at `indent` spaces in a rendered document, the key line included."""
    m = re.search(rf"^{' ' * indent}{re.escape(key)}:.*\n(?:(?:{' ' * (indent + 1)}.*|{' ' * indent}- .*)?\n)*", doc, re.M)
    return m.group(0) if m else ""


def kagent_release(flags: list, chart: str) -> str:
    docs = documents(helm(chart, flags))
    hr = docs.get(("HelmRelease", "kagent"))
    if not hr:
        fail(f"no kagent HelmRelease rendered: {' '.join(flags)}")
    return hr


def admission(hr: str) -> dict | None:
    """The kagent release's controller.session.admission.jwt as {key: value}, None without one."""
    jwt = block(block(block(block(hr, "controller", 4), "session", 6), "admission", 8), "jwt", 10)
    if not jwt:
        return None
    return {k: v.strip('"') for k, v in re.findall(r"^ {12}(\w+): (.*)$", jwt, re.M)}


def notes(chart: str, flags: list) -> str:
    """The rendered NOTES.txt: `helm template` skips NOTES, so a copy of the chart renders it
    inside a ConfigMap."""
    with tempfile.TemporaryDirectory() as tmp:
        copy = os.path.join(tmp, os.path.basename(os.path.normpath(chart)))
        shutil.copytree(chart, copy)
        templates = os.path.join(copy, "templates")
        with open(os.path.join(templates, "NOTES.txt"), encoding="utf-8") as f:
            text = f.read()
        os.remove(os.path.join(templates, "NOTES.txt"))
        with open(os.path.join(templates, "_zz_notes.tpl"), "w", encoding="utf-8") as f:
            f.write('{{- define "zz.notes" -}}\n' + text + "{{- end -}}\n")
        with open(os.path.join(templates, "zz-notes.yaml"), "w", encoding="utf-8") as f:
            f.write('apiVersion: v1\nkind: ConfigMap\nmetadata:\n  name: zz-notes\ndata:\n  notes: {{ include "zz.notes" . | toJson }}\n')
        out = helm(copy, [*flags, "--show-only", "templates/zz-notes.yaml"])
    return json.loads(re.search(r"^  notes: (.*)$", out, re.M).group(1)) + "\n"


def kagent_floor(chart: str) -> str:
    """The floor of components.kagent.versionRange as values.yaml pins it."""
    with open(f"{chart}/values.yaml", encoding="utf-8") as f:
        entry = block(f.read(), "kagent", 2)
    m = re.search(r'^    versionRange: ">=([0-9]+\.[0-9]+\.[0-9]+)', entry, re.M)
    if not m:
        fail("components.kagent.versionRange in values.yaml has no `>=X.Y.Z` floor")
    return m.group(1)


def kagent_chart_render(hr: str, version: str) -> str:
    """`helm template` of the kagent chart at version with the values block the HelmRelease forwards."""
    values = block(hr, "values", 2)
    if not values:
        fail(f"the kagent HelmRelease carries no values block:\n{hr}")
    with tempfile.TemporaryDirectory() as tmp:
        with open(f"{tmp}/values.yaml", "w", encoding="utf-8") as f:
            f.write("".join(line[4:] + "\n" for line in values.splitlines()[1:]))
        r = subprocess.run([HELM, "pull", KAGENT_CHART, "--version", version, "--untar", "-d", tmp], capture_output=True, text=True)
        if r.returncode != 0:
            fail(f"helm pull kagent {version}: {r.stderr}")
        r = subprocess.run([HELM, "template", "kagent", f"{tmp}/kagent", "-n", "kagent", "-f", f"{tmp}/values.yaml"], capture_output=True, text=True)
        if r.returncode != 0:
            fail(f"the kagent chart {version} refuses the forwarded values:\n{r.stderr}")
        return r.stdout


def main(meta: str) -> int:
    base = [*ON, *KAGENT, *IDENTITY]

    # --- nothing by default ----------------------------------------------------------------------
    if admission(kagent_release([*KAGENT, *IDENTITY], meta)) is not None:
        fail("the kagent release carries a Session admission with workspaces off")
    if "\n      session:\n" in block(kagent_release([*KAGENT, *IDENTITY], meta), "controller", 4):
        fail("the kagent release carries a controller.session key with workspaces off")
    ok("nothing by default: with workspaces off the kagent release carries no controller.session")

    # --- the derivation --------------------------------------------------------------------------
    hr = kagent_release(base, meta)
    got = admission(hr)
    if got != WANT:
        fail(f"the kagent release's admission is {got}, expected {WANT}:\n{block(hr, 'controller', 4)}")
    if re.search(r"^\s+\S+: null$", block(hr, "values", 2), re.M):
        fail(f"the kagent release's values carry a null (removed, not stored, by the merge patch of an existing HelmRelease):\n{block(hr, 'values', 2)}")
    text = notes(meta, base)
    line = f"kagent's Session admission: a Session naming a workspace volume is admitted with a grant the key set {JWKS} verifies (issuer {ISSUER}, audience kagent), naming the caller by email"
    if line not in text:
        fail(f"NOTES does not print the admission:\n{text}")
    if admission(kagent_release([*base, *NO_OWN], meta)) != WANT:
        fail("with no own reference the connectivity release's key (providers, oauth.enabled, grantKey.create) derives no admission")
    ok(f"with the switch on and a grant signing key (own or created) the kagent release carries {JWT}: "
       f"jwksURL {JWKS}, issuer {ISSUER}, audience kagent; NOTES prints it; no null in the release's values")

    # --- nothing without a grant key, or without kagent ------------------------------------------
    for flags, what in (([*NO_OWN, "--set", f"{NAME}.providers=null", "--set", "workspaceManager.route.enabled=false"], "without providers"),
                        ([*NO_OWN, "--set", f"{NAME}.oauth.enabled=false"], "with oauth off"),
                        ([*NO_OWN, "--set", "workspaceManager.grantKey.create=false"], "with grantKey.create false")):
        if admission(kagent_release([*base, *flags], meta)) is not None:
            fail(f"the kagent release carries a Session admission {what}: the manager signs no grant then")
    if ("HelmRelease", "kagent") in documents(helm(meta, [*ON, *IDENTITY, "--set", "components.kagent.enabled=false"])):
        fail("a kagent release renders with kagent off")
    ok("nothing without a grant key (no providers, oauth off, grantKey.create false) and nothing with kagent off")

    # --- what the derivation follows -------------------------------------------------------------
    moved = admission(kagent_release([*base, "--set", f"{NAME}.fullnameOverride=wm", "--set", f"{NAME}.service.port=9090",
                                      "--set", "gitops.targetNamespace=plat"], meta))
    if moved["jwksURL"] != "http://wm.plat.svc:9090/.well-known/jwks.json":
        fail(f"the key set does not follow fullnameOverride, service.port and gitops.targetNamespace: {moved}")
    moved = admission(kagent_release([*base, "--set", f"components.{NAME}.targetNamespace=wmns"], meta))
    if moved["jwksURL"] != f"http://{NAME}.wmns.svc:8080/.well-known/jwks.json":
        fail(f"the key set does not follow components.{NAME}.targetNamespace: {moved}")
    derived = admission(kagent_release([*base, "--set", f"{NAME}.oauth.baseURL="], meta))
    if derived["issuer"] != f"https://{NAME}.ci.example.com":
        fail(f"without oauth.baseURL the issuer is not derived from global.domain: {derived}")
    aud = admission(kagent_release([*base, "--set", f"{NAME}.grant.audience=platform-kagent"], meta))
    if aud["audience"] != "platform-kagent":
        fail(f"the audience does not follow {NAME}.grant.audience: {aud}")
    own = admission(kagent_release([*base, "--set", f"{JWT}.issuer=https://wm.example.com", "--set", f"{JWT}.audience=other"], meta))
    if own != {"jwksURL": JWKS, "issuer": "https://wm.example.com", "audience": "other"}:
        fail(f"an own issuer and audience do not stand beside the derived key set: {own}")
    own = admission(kagent_release([*base, "--set", f"{JWT}.jwksURL=https://keys.example.com/jwks.json"], meta))
    if own != {"jwksURL": "https://keys.example.com/jwks.json", "issuer": ISSUER, "audience": "kagent"}:
        fail(f"an own key set does not stand beside the derived issuer and audience: {own}")
    ok(f"the key set follows {NAME}.fullnameOverride, service.port, gitops.targetNamespace and components.{NAME}.targetNamespace, "
       f"the issuer global.domain without oauth.baseURL, the audience {NAME}.grant.audience; a value the kagent block sets stands per key")

    # --- an own block without the derivation -----------------------------------------------------
    full = ["--set", f"{JWT}.jwksURL=https://keys.example.com/jwks.json", "--set", f"{JWT}.issuer=https://wm.example.com", "--set", f"{JWT}.audience=kagent"]
    if admission(kagent_release([*KAGENT, *IDENTITY, *full], meta)) != {"jwksURL": "https://keys.example.com/jwks.json", "issuer": "https://wm.example.com", "audience": "kagent"}:
        fail("with workspaces off an own admission block does not reach the kagent release as written")
    must_fail(meta, [*KAGENT, *IDENTITY, "--set", f"{JWT}.issuer=https://wm.example.com"],
              f"{JWT} sets issuer but the kagent chart needs jwksURL, issuer and audience together", "an own block set in part")
    ok("without the derivation an own block reaches the release as written, and one set in part fails naming the keys")

    # --- the guards ------------------------------------------------------------------------------
    for rng in (">=1.5.0 <1.6.0", "1.5.9"):
        ranges = ["--set", f"components.kagent.versionRange={rng}", "--set", f"components.kagent-crds.versionRange={rng}"]
        must_fail(meta, [*base, *ranges], "admits a kagent release below 1.6.0", f"a kagent range {rng} with the admission derived")
        helm(meta, [*KAGENT, *IDENTITY, *ranges])
    for rng in (">=1.6.0 <1.7.0", "1.6.0", ">=1.6.0-rc.1 <1.7.0"):
        kagent_release([*base, "--set", f"components.kagent.versionRange={rng}", "--set", f"components.kagent-crds.versionRange={rng}"], meta)
    no_client = ["--set", f"{NAME}.providers[0].values.oauth.clientID=", "--set", f"{NAME}.providers[1].values.oauth.clientID="]
    must_fail(meta, [*ON, *KAGENT, *no_client, "--set", f"{NAME}.oauth.baseURL="], "kagent's Session admission has no issuer", "no issuer")
    must_fail(meta, [*base, "--set", "kagent.controller.auth.userIdClaim=sub"], 'kagent.controller.auth.userIdClaim is "sub"', "another user-id claim")
    helm(meta, [*KAGENT, *IDENTITY, "--set", "kagent.controller.auth.userIdClaim=sub"])
    ok("the guards: a kagent range whose floor is below 1.6.0 (a range and an exact pin; 1.6.0, its release candidate and later pass), "
       "no issuer, a userIdClaim other than email — each only with the admission derived")

    # --- the kagent chart at the range's floor ---------------------------------------------------
    floor = kagent_floor(meta)
    render = kagent_chart_render(hr, floor)
    cm = [d for d in render.split("\n---\n") if re.search(r"^kind: ConfigMap$", d, re.M) and "KAGENT_CONTROLLER_NAME:" in d]
    if not cm:
        fail(f"the kagent chart {floor} renders no controller ConfigMap")
    for key, env in ENV.items():
        if f'{env}: "{WANT[key]}"' not in cm[0]:
            fail(f"the kagent chart {floor} does not render {env} from the forwarded {JWT}.{key}:\n{cm[0]}")
    ok(f"the kagent chart {floor} renders the three KAGENT_SESSION_ADMISSION_JWT_* entries of the controller ConfigMap from the forwarded block")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(f"usage: {sys.argv[0]} <meta chart dir>")
    sys.exit(main(sys.argv[1]))
