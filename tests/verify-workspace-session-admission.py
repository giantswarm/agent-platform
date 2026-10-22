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
  (kagent), and allowHTTP true: the kagent chart from 1.11.5 refuses the plain http key set
  without it; NOTES prints them; no null anywhere in the release's values;
- the same with the connectivity release's key (no own reference, providers and
  oauth.enabled); nothing without a grant key: no providers, oauth off,
  grantKey.create false; workspaces with kagent off fail the render;
- the derivation follows fullnameOverride, service.port, gitops.targetNamespace and
  components.workspace-manager.targetNamespace (the key set), global.domain (the
  issuer) and grant.audience; a value the kagent block sets for one of the three
  stands and the rest is derived; an own allowHTTP stands, and an own jwksURL carries no
  derived allowHTTP unless it names the derived key set (an external key set stays https
  unless the block opts in);
- without the derivation an own block reaches the release as written, and one set in
  part fails the render naming the keys (the kagent chart needs all three);
- muster's credentialProvider with workspaces and Substrate on: without a provider pin
  (no providers, oauth off) it releases nothing and the render fails naming
  muster.muster.credentialProvider.releasable (giantswarm/agent-platform#1103); an own
  releasable alone turns it on;
- with Substrate on muster serves FetchSecret and mounts Substrate's pod certificates, which
  only releases after muster publish and issue: until the substrate release is Ready
  (offline: never) its install and upgrade do not wait for its pods (disableWait);
  nothing with workspaces off (giantswarm/agent-platform#1107);
- the guards: a kagent range whose floor is below 1.6.0
  (agent-platform.kagent.sessionAdmissionFloor; an exact pin and a release candidate
  of 1.6.0 judged alike), no issuer (neither global.domain nor oauth.baseURL), a
  userIdClaim other than email — each only with the admission derived;
- the kagent chart at the range's floor renders the three KAGENT_SESSION_ADMISSION_JWT_*
  entries and KAGENT_SESSION_CREDENTIAL_AUTHORITY (the workspace write path, from
  kagent 1.15.0) of the controller ConfigMap from the forwarded block, and the first chart that
  takes the opt-in renders KAGENT_SESSION_ADMISSION_JWT_ALLOW_HTTP "true" from it
  (network: gsoci);
- kagent's actor-JWT provider muster (giantswarm/giantswarm#38607): while muster trusts
  the actor JWT the kagent release carries controller.session.actorJWTProviders.muster,
  the host of the agents' in-cluster MCP URL and muster's audience (its own
  actorBearer.audience, else oauth.server.baseUrl); an own key stands; nothing with the
  actor bearer off or without an audience; the kagent chart at the range's floor renders
  KAGENT_SESSION_ACTOR_JWT_PROVIDERS from it.

Deliberately stdlib-only: the CI image has no PyYAML. HELM selects the binary.
"""

import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

HELM = os.environ.get("HELM", "helm")
HERE = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location("components_charts", os.path.join(HERE, "verify-components-charts.py"))
cc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cc)
NAME = "workspace-manager"
NAMESPACE = "agent-platform"
FIXTURE = "tests/fixtures/workspaces-values.yaml"
# The agents beside them: the meta chart refuses workspaces without kagent.
AGENTS = "tests/fixtures/workspaces-agents-values.yaml"
KAGENT_CHART = "oci://gsoci.azurecr.io/giantswarm/kagent/helm/kagent"
CI = ["--set", "components.flux.enabled=false"]
ON = ["-f", FIXTURE, "-f", AGENTS]
KAGENT = ["--set", "components.kagent.enabled=true", "--set", "kagent.database.postgres.urlFile=/etc/kagent-db/uri", "--set", "kagent.harness.snapshotLocation=s3://ci/agents", "--set", "kagent.database.postgres.urlFile=/etc/kagent-db/uri"]
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
WANT = {"jwksURL": JWKS, "issuer": ISSUER, "audience": "kagent", "allowHTTP": "true"}
# The first kagent chart whose Session admission refuses a plain http jwksURL without
# controller.session.admission.jwt.allowHTTP.
ALLOW_HTTP_CHART = "1.11.5-rc.1"
# A kagent range below agent-platform.kagent.authenticationFloor renders only
# outside the trusted-proxy mode, in which the meta chart derives the
# controller's verification of people's tokens.
BELOW_AUTH_FLOOR = ["--set", "kagent.controller.auth.mode=insecure"]
MUSTER_BASE_URL = ["--set", "muster.muster.oauth.server.baseUrl=https://muster.ci.example.com"]
ACTOR_JWT_PROVIDERS = ("        actorJWTProviders:\n          muster:\n            audience: https://muster.ci.example.com\n"
                       f"            hostname: muster.{NAMESPACE}.svc.cluster.local\n")
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
    """The floor of components.kagent.versionRange as values.yaml pins it; while
    the floor is unpublished (UNRELEASED), the chart verify-components-charts
    renders against."""
    with open(f"{chart}/values.yaml", encoding="utf-8") as f:
        entry = block(f.read(), "kagent", 2)
    m = re.search(r'^    versionRange: ">=([0-9]+\.[0-9]+\.[0-9]+(?:-rc\.[0-9]+)?)[ "]', entry, re.M)
    if not m:
        fail("components.kagent.versionRange in values.yaml has no `>=X.Y.Z` or `>=X.Y.Z-rc.N` floor")
    return cc.floor_chart("kagent", KAGENT_CHART, f">={m.group(1)}")


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
       f"jwksURL {JWKS}, issuer {ISSUER}, audience kagent, allowHTTP true; NOTES prints it; no null in the release's values")

    # --- the person's credential on the providers' hosts ------------------------------------------
    session = block(block(hr, "controller", 4), "session", 6)
    if "        credentialAuthority: muster.giantswarm.io\n" not in session:
        fail(f"the kagent release's controller.session names no credentialAuthority muster.giantswarm.io:\n{session}")
    own = block(block(kagent_release([*base, "--set", "kagent.controller.session.credentialAuthority=creds.example.com"], meta), "controller", 4), "session", 6)
    if "        credentialAuthority: creds.example.com\n" not in own:
        fail(f"an own kagent.controller.session.credentialAuthority does not stand:\n{own}")
    docs = documents(helm(meta, [*base, "--set", "components.substrate.enabled=true"]))
    muster = block(block(docs.get(("HelmRelease", "muster"), ""), "muster", 4), "credentialProvider", 6)
    for line in ("        enabled: true\n", "        gatewayIdentity: spiffe://cluster.local/ns/ate-system/sa/atenet-egress\n"):
        if line not in muster:
            fail(f"with workspaces and Substrate on the muster release's credentialProvider lacks {line.strip()!r}:\n{muster}")
    bearer = block(muster, "actorBearer", 8)
    if bearer != "        actorBearer:\n          enabled: true\n          issuer: https://idp.ate-system.svc\n":
        fail(f"with workspaces and Substrate on the muster release's credentialProvider.actorBearer is not on with Substrate's issuer https://idp.ate-system.svc:\n{muster}")
    def bearer_of(flags: list) -> str:
        return block(block(block(documents(helm(meta, [*base, "--set", "components.substrate.enabled=true", *flags])).get(("HelmRelease", "muster"), ""), "muster", 4), "credentialProvider", 6), "actorBearer", 8)
    if "          issuer: https://idp.substrate.example\n" not in bearer_of(["--set", "substrate.ateApi.actorJWTIssuer=https://idp.substrate.example"]):
        fail("an own substrate.ateApi.actorJWTIssuer does not become muster's actorBearer.issuer")
    if "          issuer: https://idp.ate.svc\n" not in bearer_of(["--set", "components.substrate.targetNamespace=ate"]):
        fail("muster's actorBearer.issuer does not follow components.substrate.targetNamespace")
    if "          enabled: false\n" not in bearer_of(["--set", "muster.muster.credentialProvider.actorBearer.enabled=false"]):
        fail("an own muster.muster.credentialProvider.actorBearer.enabled=false does not stand")
    if "          audience: https://muster.example\n" not in bearer_of(["--set", "muster.muster.credentialProvider.actorBearer.audience=https://muster.example"]):
        fail("an own muster.muster.credentialProvider.actorBearer.audience does not stand")
    # agent-manager is told muster trusts the actor JWT: it reports an agent's toolset as
    # bound to each session at its start (giantswarm/agent-manager#228).
    AM_ON = ["--set", "components.agent-manager.enabled=true"]
    def am_muster(flags: list) -> str:
        return block(block(documents(helm(meta, [*base, *AM_ON, *flags])).get(("HelmRelease", "agent-manager"), ""), "values", 2), "muster", 4)
    if "      actorBearer: true\n" not in am_muster(["--set", "components.substrate.enabled=true"]):
        fail(f"with muster trusting the actor JWT the agent-manager release's muster.actorBearer is not true:\n{am_muster(['--set', 'components.substrate.enabled=true'])}")
    off = am_muster(["--set", "components.substrate.enabled=true", "--set", "muster.muster.credentialProvider.actorBearer.enabled=false"])
    if "actorBearer" in off:
        fail(f"with muster's actorBearer off the agent-manager release carries muster.actorBearer:\n{off}")
    must_fail(meta, [*base, *AM_ON, "--set", "components.substrate.enabled=true", "--set", "agent-manager.muster.actorBearer=false"],
              "agent-manager.muster.actorBearer", "an own agent-manager.muster.actorBearer that differs")
    must_fail(meta, [*base, *AM_ON, "--set", "components.substrate.enabled=true", "--set", "components.agent-manager.versionRange=>=2.8.0 <3.0.0"],
              "components.agent-manager.versionRange", "an agent-manager range below the toolset-binding floor")
    must_fail(meta, [*base, *AM_ON, "--set", "components.substrate.enabled=true", "--set", "components.muster.versionRange=>=5.48.0 <6.0.0"],
              "components.muster.versionRange", "a muster range below the toolset-binding floor")
    ok("agent-manager's muster.actorBearer follows muster's actor bearer: true with it on, absent with it off, an own value that differs fails the render")
    ok("with workspaces and Substrate on muster accepts Substrate's actor JWT (actorBearer, issuer https://idp.<substrate namespace>.svc "
       "or substrate.ateApi.actorJWTIssuer); an own enabled: false or audience stands")
    def actor_jwt_of(flags: list) -> str:
        return block(block(block(kagent_release([*base, *flags], meta), "controller", 4), "session", 6), "actorJWTProviders", 8)
    if actor_jwt_of(MUSTER_BASE_URL) != ACTOR_JWT_PROVIDERS:
        fail(f"with muster trusting the actor JWT the kagent release's controller.session.actorJWTProviders is not muster's "
             f"in-cluster host and its oauth.server.baseUrl:\n{actor_jwt_of(MUSTER_BASE_URL)}")
    if "            audience: https://muster.example\n" not in actor_jwt_of([*MUSTER_BASE_URL, "--set", "muster.muster.credentialProvider.actorBearer.audience=https://muster.example"]):
        fail("kagent's muster actor-JWT provider does not take muster's own actorBearer.audience")
    if "            hostname: muster.example.internal\n" not in actor_jwt_of([*MUSTER_BASE_URL, "--set", "kagent.controller.session.actorJWTProviders.muster.hostname=muster.example.internal"]):
        fail("an own kagent.controller.session.actorJWTProviders.muster.hostname does not stand")
    if "            hostname: muster-gw.agent-platform.svc.cluster.local\n" not in actor_jwt_of([*MUSTER_BASE_URL, "--set", "muster.fullnameOverride=muster-gw"]):
        fail("kagent's muster actor-JWT provider does not follow muster.fullnameOverride, the host every agent's RemoteMCPServer targets")
    for flags, what in (([*MUSTER_BASE_URL, "--set", "muster.muster.credentialProvider.actorBearer.enabled=false"], "with muster's actorBearer off"),
                        ([], "without an audience (no oauth.server.baseUrl): kagent's controller refuses an entry without one")):
        if actor_jwt_of(flags):
            fail(f"the kagent release names an actor-JWT provider {what}:\n{actor_jwt_of(flags)}")
    ok("with muster trusting the actor JWT the kagent release names muster as its actor-JWT provider: the host of the agents' "
       "in-cluster MCP URL (following muster.fullnameOverride) and muster's audience (actorBearer.audience, else oauth.server.baseUrl); "
       "an own key stands; nothing with the actor bearer off or without an audience")
    releasable = block(muster, "releasable", 8)
    if "          github: https://github.com/apps/ci-agents\n" not in releasable:
        fail(f"the muster release's credentialProvider.releasable does not name the pinned provider instance github under its App's issuer:\n{muster}")
    own = block(block(block(documents(helm(meta, [*base, "--set", "components.substrate.enabled=true", "--set", "muster.muster.credentialProvider.releasable.gitlab=https://gitlab.example.com"])).get(("HelmRelease", "muster"), ""), "muster", 4), "credentialProvider", 6), "releasable", 8)
    for line in ("          github: https://github.com/apps/ci-agents\n", "          gitlab: https://gitlab.example.com\n"):
        if line not in own:
            fail(f"an own muster.muster.credentialProvider.releasable entry does not stand beside the pinned instance's ({line.strip()!r}):\n{own}")
    derived = block(block(block(documents(helm(meta, [*base, "--set", "components.substrate.enabled=true", "--set", f"{NAME}.muster.mcpServer.auth.authorizationServer.issuer="])).get(("HelmRelease", "muster"), ""), "muster", 4), "credentialProvider", 6), "releasable", 8)
    if "          github: https://github.com/apps/123\n" not in derived:
        fail(f"without an own issuer the muster release's credentialProvider.releasable does not name github under the derived https://github.com/apps/<app.id>:\n{derived}")
    substrate = block(block(docs.get(("HelmRelease", "substrate"), ""), "credentialProvider", 4), "additionalProviders", 6)
    if "uriAuthority: muster.giantswarm.io" not in substrate or "host: muster.agent-platform.svc:50051" not in substrate:
        fail(f"the substrate release's credentialProvider.additionalProviders lacks muster's FetchSecret:\n{substrate}")
    off = documents(helm(meta, [*KAGENT, *IDENTITY, "--set", "components.substrate.enabled=true"]))
    if "credentialProvider:" in block(off.get(("HelmRelease", "muster"), ""), "muster", 4):
        fail("the muster release carries a credentialProvider with workspaces off")
    # muster's pods mount Substrate's pod certificates and trust bundles, which the
    # connectivity release's hook publishes and the substrate release issues, both after
    # muster: until the substrate release is Ready its install and upgrade do not wait
    # for its pods (giantswarm/agent-platform#1107). Offline no release is Ready.
    release = docs.get(("HelmRelease", "muster"), "")
    for action in ("install", "upgrade"):
        if "    disableWait: true\n" not in block(release, action, 2):
            fail(f"with a credentialProvider and Substrate not Ready the muster release's {action} waits for pods that mount Substrate's certificates, issued only after muster (giantswarm/agent-platform#1107):\n{block(release, action, 2)}")
        if "disableWait" in block(off.get(("HelmRelease", "muster"), ""), action, 2):
            fail(f"the muster release's {action} does not wait with workspaces off")
    ok("with the switch on the kagent release names muster.giantswarm.io as its credentialAuthority (an own value stands), "
       "the muster release serves FetchSecret to the egress gateway's identity with Substrate on and releases the pinned provider instance github's grants under its own or derived issuer (an own entry stands beside it), "
       "and Substrate routes the authority to muster; until Substrate is Ready its install and upgrade do not wait for the pods that mount Substrate's certificates; "
       "with workspaces off muster gets no credentialProvider")
    sub = ["--set", "components.substrate.enabled=true"]
    unpinned = (([*NO_OWN, "--set", f"{NAME}.providers=null", "--set", "workspaceManager.route.enabled=false"], "without providers"),
                ([*NO_OWN, "--set", f"{NAME}.oauth.enabled=false"], "with oauth off"))
    for flags, what in unpinned:
        must_fail(meta, [*base, *sub, *flags], "muster releases no provider instance's grant",
                  f"workspaces and Substrate on {what} (no provider pin, no own releasable)")
    own = block(block(block(documents(helm(meta, [*base, *sub, *unpinned[0][0], "--set", "muster.muster.credentialProvider.releasable.gitlab=https://gitlab.example.com"])).get(("HelmRelease", "muster"), ""), "muster", 4), "credentialProvider", 6), "releasable", 8)
    if own != "        releasable:\n          gitlab: https://gitlab.example.com\n":
        fail(f"without a provider pin an own muster.muster.credentialProvider.releasable does not reach the muster release alone:\n{own}")
    ok("without a provider pin (no providers, oauth off) workspaces and Substrate fail the render naming muster's releasable, "
       "and an own releasable alone turns muster's credentialProvider on")

    # --- nothing without a grant key, or without kagent ------------------------------------------
    # Substrate follows kagent: without a provider pin muster's releasable is the installation's own.
    releasable = ["--set", "muster.muster.credentialProvider.releasable.gitlab=https://gitlab.example.com"]
    for flags, what in (([*NO_OWN, "--set", f"{NAME}.providers=null", "--set", "workspaceManager.route.enabled=false", *releasable], "without providers"),
                        ([*NO_OWN, "--set", f"{NAME}.oauth.enabled=false", *releasable], "with oauth off"),
                        ([*NO_OWN, "--set", "workspaceManager.grantKey.create=false"], "with grantKey.create false")):
        if admission(kagent_release([*base, *flags], meta)) is not None:
            fail(f"the kagent release carries a Session admission {what}: the manager signs no grant then")
    must_fail(meta, [*ON, *IDENTITY, "--set", "components.kagent.enabled=false"], "workspaces need the agents", "workspaces on with kagent off")
    ok("nothing without a grant key (no providers, oauth off, grantKey.create false); workspaces with kagent off fail the render")

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
    if own != {"jwksURL": JWKS, "issuer": "https://wm.example.com", "audience": "other", "allowHTTP": "true"}:
        fail(f"an own issuer and audience do not stand beside the derived key set: {own}")
    own = admission(kagent_release([*base, "--set", f"{JWT}.jwksURL=https://keys.example.com/jwks.json"], meta))
    if own != {"jwksURL": "https://keys.example.com/jwks.json", "issuer": ISSUER, "audience": "kagent"}:
        fail(f"an own key set does not stand beside the derived issuer and audience, or carries a derived allowHTTP: {own}")
    own = admission(kagent_release([*base, "--set", f"{JWT}.jwksURL=http://keys.example.com/jwks.json"], meta))
    if "allowHTTP" in own:
        fail(f"an own plain http key set carries a derived allowHTTP: {own}")
    own = admission(kagent_release([*base, "--set", f"{JWT}.jwksURL={JWKS}"], meta))
    if own != WANT:
        fail(f"an own jwksURL that names the derived key set carries no derived allowHTTP: {own}")
    own = admission(kagent_release([*base, "--set", f"{JWT}.allowHTTP=false"], meta))
    if own != {**WANT, "allowHTTP": "false"}:
        fail(f"an own allowHTTP does not stand: {own}")
    ok(f"the key set follows {NAME}.fullnameOverride, service.port, gitops.targetNamespace and components.{NAME}.targetNamespace, "
       f"the issuer global.domain without oauth.baseURL, the audience {NAME}.grant.audience; a value the kagent block sets stands per key; "
       f"another own jwksURL carries no derived allowHTTP, one naming the derived key set does")

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
        helm(meta, [*KAGENT, *IDENTITY, *ranges, *BELOW_AUTH_FLOOR])
    for rng in (">=1.6.0 <1.7.0", "1.6.0", ">=1.6.0-rc.1 <1.7.0"):
        kagent_release([*base, "--set", f"components.kagent.versionRange={rng}", "--set", f"components.kagent-crds.versionRange={rng}", *BELOW_AUTH_FLOOR], meta)
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
    if 'KAGENT_SESSION_CREDENTIAL_AUTHORITY: "muster.giantswarm.io"' not in cm[0]:
        fail(f"the kagent chart {floor} does not render KAGENT_SESSION_CREDENTIAL_AUTHORITY from the forwarded "
             f"kagent.controller.session.credentialAuthority: the range admits no release of the workspace write path:\n{cm[0]}")
    ok(f"the kagent chart {floor} renders the three KAGENT_SESSION_ADMISSION_JWT_* entries and KAGENT_SESSION_CREDENTIAL_AUTHORITY "
       f"of the controller ConfigMap from the forwarded block")
    render = kagent_chart_render(kagent_release([*base, *MUSTER_BASE_URL], meta), floor)
    cm = [d for d in render.split("\n---\n") if re.search(r"^kind: ConfigMap$", d, re.M) and "KAGENT_CONTROLLER_NAME:" in d]
    want = json.dumps({"muster": {"audience": "https://muster.ci.example.com", "hostname": f"muster.{NAMESPACE}.svc.cluster.local"}}, separators=(",", ":"))
    if not cm or f"KAGENT_SESSION_ACTOR_JWT_PROVIDERS: {json.dumps(want)}" not in cm[0]:
        fail(f"the kagent chart {floor} does not render KAGENT_SESSION_ACTOR_JWT_PROVIDERS {want} from the forwarded "
             f"kagent.controller.session.actorJWTProviders: the range admits no release that compiles muster's actor JWT:\n{cm[0] if cm else render}")
    ok(f"the kagent chart {floor} renders KAGENT_SESSION_ACTOR_JWT_PROVIDERS with muster's host and audience from the forwarded block")
    render = kagent_chart_render(hr, ALLOW_HTTP_CHART)
    cm = [d for d in render.split("\n---\n") if re.search(r"^kind: ConfigMap$", d, re.M) and "KAGENT_CONTROLLER_NAME:" in d]
    if not cm or 'KAGENT_SESSION_ADMISSION_JWT_ALLOW_HTTP: "true"' not in cm[0]:
        fail(f"the kagent chart {ALLOW_HTTP_CHART} does not render KAGENT_SESSION_ADMISSION_JWT_ALLOW_HTTP from the forwarded {JWT}.allowHTTP:\n{cm[0] if cm else render}")
    ok(f"the kagent chart {ALLOW_HTTP_CHART}, the first that refuses a plain http key set, renders KAGENT_SESSION_ADMISSION_JWT_ALLOW_HTTP \"true\" from the forwarded block")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(f"usage: {sys.argv[0]} <meta chart dir>")
    sys.exit(main(sys.argv[1]))
