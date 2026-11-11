#!/usr/bin/env python3
"""Assert the default agent the meta chart ships (giantswarm/giantswarm#38970).

- components.default-agent renders one HelmRelease of the agent chart,
  named default-agent, into the kagent component's namespace,
  labelled managed-by agent-platform and default "true" (the HelmRelease and
  the Agent), only
  while it, kagent, muster and agent-manager are on, after kagent alone (a
  failed agent-manager or skill-manager never holds it); its range is
  agent-manager's agentChart.semver; its values are
  agents/default-agent/values.yaml with the derived muster.url, iconUrl,
  displayName and description (defaultAgent; the display name also in the
  system message), toolset, labels and modelConfig.name. The range's floor is
  at least 2.6.0, the agent chart whose empty modelConfig.name renders no model.
- The model: defaultAgent.modelConfig as modelConfig.name; unset,
  modelConfig.name "" and the label follows-default-model "true", and
  agent-manager gets defaultAgentModel.agent default-agent from
  agent-platform.agentManager.skillManagerFloor on, so it sets the namespace's
  default model at runtime. No Helm lookup. components.default-agent.labels add
  to managed-by and default on the Agent, never drop them.
- The working tree and a pre-release carry no skills; stamped by
  hack/stamp-release-commit.sh, the packaged chart pins every skill of skills/
  to that commit (the tag pipeline's step, simulated on a copy); a release
  packaged unstamped fails its render. skills.platformSource.ref (a full
  commit id, nothing else) pins the default agent's skills and the platform
  source to it, on a release and a pre-release alike.
- components.skill-manager follows agent-manager (its own switch wins), one
  release from the catalog's skill-manager chart in the platform's namespace,
  after muster, with nothing after it; the connectivity release gets its
  roster entry and MCP backend entry only while it is on, and agent-manager's
  egress policy reaches it.
- skillManager.sources feeds skill-manager.sources and
  backstage.skillsRepositories: the platform's source first (the portal: only
  from its object-form floor), at v<version> on a published release, at the
  h<sha> commit on a dev build, without a ref otherwise, off with skills.platformSource.enabled=false;
  backstage.skillsRepositories replaces skillManager.sources for the portal;
  one entry per url + ref + path; an entry for the platform's repository fails
  the render; an own skill-manager.sources fails. The schema takes URLs and
  objects.
- agent-manager gets skillManager.url (the skill-manager's Service) and no
  skill sources from agent-platform.agentManager.skillManagerFloor on, with
  the skill-manager on; else skills.repositories as URLs, no platform source,
  replaced by its own deprecated list. From the same floor agent-manager gets
  installation (backstage.installationName), which get_info reports for the
  default agent's start links. The deprecated list fails the render while
  agent-manager pins through the skill-manager.
- The connectivity chart wires the portal's meta agents to it while it is on.

usage: verify-default-agent.py <meta chart dir> <connectivity chart dir>
"""
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile

import yaml

META, CONN = sys.argv[1], sys.argv[2]
HELM = os.environ.get("HELM") or "helm"
ROOT = pathlib.Path(__file__).resolve().parent.parent
NAME = "default-agent"
LABEL = {"agent-platform.giantswarm.io/managed-by": "agent-platform", "agent-platform.giantswarm.io/default": "true"}
FOLLOWS = {**LABEL, "agent-platform.giantswarm.io/follows-default-model": "true"}
# The agent chart release whose empty modelConfig.name renders no model (giantswarm/agent#130).
AGENT_CHART_FLOOR = (2, 6, 0)
PLATFORM = "https://github.com/giantswarm/agent-platform"
SHA = "0123456789abcdef0123456789abcdef01234567"
BASE = [
    "--namespace", "agent-platform",
    "--set", "global.domain=example.com",
    "--set", "global.identity.issuerUrl=https://dex.example.com",
    "--set", "global.identity.clientId=agent-platform",
    "--set", "global.identity.existingSecret=agent-platform-idp",
    "--set", "global.gatewayApi.parentRefs[0].name=giantswarm-default",
    "--set", "model-manager.ollama.endpoint=http://ollama.example.com:11434",
    "--set", "kagent.harness.snapshotLocation=s3://ci-agent-snapshots/agents",
    "--set", "kagent.database.postgres.urlFile=/etc/kagent-db/uri",
    "--set", "postgres.enabled=true",
]
ON = ["--set", "components.kagent.enabled=true", "--set", "components.agent-manager.enabled=true"]
FLOORS = ["--set", "components.agent-manager.versionRange=>=2.99.0 <3.0.0", "--set", "components.backstage.versionRange=>=3.99.0 <4.0.0"]
# Below agent-platform.backstage.skillManagerFloor: the portal still gets its own list.
LIST_FLOORS = ["--set", "components.agent-manager.versionRange=>=2.99.0 <3.0.0", "--set", "components.backstage.versionRange=>=3.31.0 <3.36.0"]
SM_OFF = ["--set", "components.skill-manager.enabled=false"]
SM_URL = "http://skill-manager.agent-platform.svc.cluster.local:8080"
PORTAL = ["--set", "components.backstage.enabled=true"]


def fail(msg: str) -> None:
    print(f"FAIL: {msg}", file=sys.stderr)
    sys.exit(1)


def run(chart: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([HELM, "template", "ap", chart, *BASE, *args], capture_output=True, text=True)


def render(chart: str, *args: str) -> list[dict]:
    r = run(chart, *args)
    if r.returncode != 0:
        fail(f"render of {chart} {' '.join(args)} failed:\n{r.stderr}")
    return [d for d in yaml.safe_load_all(r.stdout) if d]


def refused(chart: str, needle: str, *args: str) -> None:
    r = run(chart, *args)
    if r.returncode == 0 or needle not in r.stderr:
        fail(f"render of {chart} {' '.join(args)} is not refused with {needle!r}:\n{r.stderr}")


def find(docs: list[dict], kind: str, name: str) -> dict | None:
    return next((d for d in docs if d["kind"] == kind and d["metadata"]["name"] == name), None)


def repos(docs: list[dict]) -> tuple[list, list]:
    """The skill-manager's sources and the portal's skillsRepositories."""
    sm = find(docs, "HelmRelease", "skill-manager")["spec"]["values"]["sources"]
    conn = find(docs, "HelmRelease", "agent-platform-connectivity")["spec"]["values"]["backstage"]["skillsRepositories"]
    return sm, conn


def am_skills(docs: list[dict]) -> tuple[list, dict | None]:
    """agent-manager's skills.repositories and skillManager."""
    v = find(docs, "HelmRelease", "agent-manager")["spec"]["values"]
    return v["skills"]["repositories"], v.get("skillManager")


def package(tmp: str, version: str, stamp: bool) -> str:
    """A copy of the chart, stamped or not, packaged at a version (appVersion
    the same, as the pipeline packages it)."""
    chart = f"{tmp}/{version}/agent-platform"
    shutil.copytree(META, chart)
    if stamp:
        r = subprocess.run([str(ROOT / "hack/stamp-release-commit.sh"), SHA, chart], capture_output=True, text=True)
        if r.returncode != 0:
            fail(f"hack/stamp-release-commit.sh failed:\n{r.stderr}")
    p = subprocess.run([HELM, "package", chart, "--version", version, "--app-version", version, "-d", f"{tmp}/{version}"], capture_output=True, text=True)
    if p.returncode != 0:
        fail(f"helm package {version} failed:\n{p.stderr}")
    return f"{tmp}/{version}/agent-platform-{version}.tgz"


values = yaml.safe_load(open(f"{META}/values.yaml"))
semver = values["agent-manager"]["agentChart"]["semver"]
if values["components"][NAME]["versionRange"] != semver:
    fail(f"components.{NAME}.versionRange {values['components'][NAME]['versionRange']!r} is not agent-manager.agentChart.semver {semver!r}: the default agent runs the agent chart line agent-manager composes")
floor = tuple(int(x) for x in semver.split()[0].lstrip(">=").split("-")[0].split("."))
if not semver.startswith(">=") or floor < AGENT_CHART_FLOOR or not semver.endswith("<3.0.0"):
    fail(f"agent-manager.agentChart.semver {semver!r}: want a 2.x range from {'.'.join(map(str, AGENT_CHART_FLOOR))}, whose empty modelConfig.name renders no model")
helpers = (pathlib.Path(META) / "templates/_helpers.tpl").read_text()
derived = helpers[helpers.index('{{- define "agent-platform.defaultAgent.derived" -}}'):]
derived = derived[:derived.index("\n{{/*")]
if "lookup" in derived or 'define "agent-platform.defaultAgent.modelConfig"' in helpers:
    fail("the default agent's model is looked up at render: agent-manager follows the namespace's default model at runtime")
skills = sorted(p.parent.name for p in (ROOT / "skills").glob("*/SKILL.md"))
installation = values["skillManager"]["sources"]

# Off with kagent, muster, agent-manager or its own switch off.
for what, args in {"kagent off (the default)": ["--set", "components.agent-manager.enabled=true"],
                   "agent-manager off (the default)": ["--set", "components.kagent.enabled=true"],
                   "its switch off": [*ON, "--set", f"components.{NAME}.enabled=false"],
                   "muster off": [*ON, "--set", "components.muster.enabled=false"]}.items():
    docs = render(META, *args)
    if find(docs, "HelmRelease", NAME):
        fail(f"{what}: the {NAME} HelmRelease renders")
    conn = find(docs, "HelmRelease", "agent-platform-connectivity")
    if conn and NAME in conn["spec"]["values"]["components"]:
        fail(f"{what}: the roster forwards {NAME} to the connectivity release (gatedRoster)")
print("ok: no default agent without kagent, muster, agent-manager or its switch")

docs = render(META, *ON, "--set", "dicebear.route.hostnames[0]=avatars.example.org")
hr, oci = find(docs, "HelmRelease", NAME), find(docs, "OCIRepository", NAME)
if not hr or not oci:
    fail(f"{NAME}: no HelmRelease/OCIRepository with kagent, muster and agent-manager on")
spec, v = hr["spec"], hr["spec"]["values"]
checks = {
    "releaseName": (spec["releaseName"], NAME),
    "install/upgrade disableWait": ((spec["install"].get("disableWait"), spec["upgrade"].get("disableWait")), (True, True)),
    "targetNamespace": (spec["targetNamespace"], "kagent"),
    "HelmRelease labels": ({k: hr["metadata"]["labels"].get(k) for k in LABEL}, LABEL),
    "Agent labels (unpinned: follows the default model)": (v.get("labels"), FOLLOWS),
    "chart": (oci["spec"]["url"], "oci://gsoci.azurecr.io/charts/giantswarm/agent"),
    "range": (oci["spec"]["ref"]["semver"], semver),
    "dependsOn (kagent alone)": (sorted(d["name"] for d in spec["dependsOn"]), ["kagent"]),
    "displayName (defaultAgent.displayName's default)": (v["agent"]["displayName"], "General Assistant"),
    "systemMessage names the display name": (v["agent"]["systemMessage"].startswith("Your name is General Assistant. You are the default agent"), True),
    "iconUrl (the route's hostname)": (v["agent"]["iconUrl"], f"https://avatars.example.org/v1/{NAME}.png"),
    "toolset": (v["toolset"], ["preset:full"]),
    "systemMessage carries no installation name": ("this installation is" in v["agent"]["systemMessage"], False),
    "muster.url": (v["muster"]["url"], "http://muster.agent-platform.svc.cluster.local:8090/mcp"),
    "modelConfig (unset: none, agent-manager sets it)": (v.get("modelConfig"), {"name": ""}),
    "skills (the working tree is unstamped)": (v["skills"], []),
    "no global": ("global" in v, False),
}
for what, (got, want) in checks.items():
    if got != want:
        fail(f"{NAME}: {what} is {got!r}, want {want!r}")
v = find(render(META, *ON), "HelmRelease", NAME)["spec"]["values"]
if v["agent"].get("iconUrl") != f"https://avatars.example.com/v1/{NAME}.png":
    fail(f"{NAME}: without a route hostname the avatar is not on avatars.<global.domain>: {v['agent']!r}")
v = find(render(META, *ON, "--set", "components.dicebear.enabled=false", "--set", "defaultAgent.modelConfig=claude-opus"), "HelmRelease", NAME)["spec"]["values"]
if v.get("modelConfig") != {"name": "claude-opus"} or v.get("labels") != LABEL or "iconUrl" in v["agent"]:
    fail(f"{NAME}: defaultAgent.modelConfig is not its modelConfig.name without the follow label, or an iconUrl renders without DiceBear: {v.get('modelConfig')!r}, {v.get('labels')!r}, {v['agent']!r}")
v = find(render(META, *ON, "--set", f"components.{NAME}.labels.team=x", "--set-string", f"components.{NAME}.labels.agent-platform\\.giantswarm\\.io/default=false",
                "--set", f"components.{NAME}.labels.agent-platform\\.giantswarm\\.io/managed-by=me"), "HelmRelease", NAME)["spec"]["values"]
if v.get("labels") != {**FOLLOWS, "team": "x"}:
    fail(f"{NAME}: components.{NAME}.labels drop or change managed-by/default on the Agent, or add nothing: {v.get('labels')!r}")
# agent-manager follows the default model for the default agent from the release that takes the key.
for what, args, want in (("below the floor", [*ON], None), ("from the floor", [*ON, *FLOORS], {"agent": NAME}),
                         ("the default agent off", [*ON, *FLOORS, "--set", f"components.{NAME}.enabled=false"], None)):
    got = find(render(META, *args), "HelmRelease", "agent-manager")["spec"]["values"].get("defaultAgentModel")
    if got != want:
        fail(f"agent-manager, {what}: defaultAgentModel {got!r}, want {want!r}")
v = find(render(META, *ON, "--set", "defaultAgent.displayName=Swarmy", "--set", "defaultAgent.description=Ask me."), "HelmRelease", NAME)["spec"]["values"]
if (v["agent"]["displayName"], v["agent"]["description"]) != ("Swarmy", "Ask me.") or "[[" in v["agent"]["systemMessage"] or not v["agent"]["systemMessage"].startswith("Your name is Swarmy. "):
    fail(f"{NAME}: defaultAgent.displayName/description are not its agent.displayName/description and the system message's name: {v['agent']!r}")
refused(META, "displayName", *ON, "--set", "defaultAgent.displayName=")
# agent-manager reports the portal's installation name (get_info installation) from its floor on.
for what, args, want in (("below the floor", [*ON], None), ("from the floor", [*ON, *FLOORS], "agent-platform"),
                         ("backstage.installationName=lab", [*ON, *FLOORS, "--set", "backstage.installationName=lab"], "lab")):
    got = find(render(META, *args), "HelmRelease", "agent-manager")["spec"]["values"].get("installation")
    if got != want:
        fail(f"agent-manager, {what}: installation {got!r}, want {want!r}")
# Slack's agent picker preselects the default agent (klausGateway.a2a.defaultAgent derived), an
# own value wins, and nothing is derived while the default agent is off.
KG = ["--set", "components.klaus-gateway.enabled=true"]
for what, args, want in (("derived", [*ON, *KG], "kagent/default-agent"),
                         ("kagent.namespaceOverride=agents", [*ON, *KG, "--set", "kagent.namespaceOverride=agents"], "agents/default-agent"),
                         ("an own value", [*ON, *KG, "--set", "klausGateway.a2a.defaultAgent=sre-agent"], "sre-agent"),
                         ("the default agent off", [*ON, *KG, "--set", "components.default-agent.enabled=false"], "")):
    got = find(render(META, *args), "HelmRelease", "klaus-gateway")["spec"]["values"]["a2a"].get("defaultAgent")
    if got != want:
        fail(f"klaus-gateway, {what}: a2a.defaultAgent {got!r}, want {want!r}")
hr = find(render(META, *ON, "--set", "kagent.namespaceOverride=agents"), "HelmRelease", NAME)
if hr["spec"]["targetNamespace"] != "agents":
    fail(f"{NAME}: targetNamespace {hr['spec']['targetNamespace']!r} does not follow kagent.namespaceOverride=agents")
print(f"ok: {NAME} renders the agent chart into the kagent namespace with the derived values, no skills unstamped; unpinned it follows the default model through agent-manager, no lookup")

# The release pipeline's stamp, on copies of the chart, packaged as the pipeline packages them.
with tempfile.TemporaryDirectory() as tmp:
    bad = subprocess.run([str(ROOT / "hack/stamp-release-commit.sh"), "v1.2.3", META], capture_output=True, text=True)
    if bad.returncode == 0:
        fail("hack/stamp-release-commit.sh accepts a tag in place of a commit id")
    release = package(tmp, "9.9.9", stamp=True)
    again = subprocess.run([str(ROOT / "hack/stamp-release-commit.sh"), SHA, f"{tmp}/9.9.9/agent-platform"], capture_output=True, text=True)
    if again.returncode == 0:
        fail("hack/stamp-release-commit.sh passes on a chart with nothing to stamp")
    docs = render(release, *ON, *LIST_FLOORS, *PORTAL)
    v = find(docs, "HelmRelease", NAME)["spec"]["values"]
    want = [{"name": s, "path": f"skills/{s}", "git": {"url": PLATFORM, "commit": SHA}} for s in skills]
    if sorted(v["skills"], key=lambda e: e["name"]) != want:
        fail(f"the stamped release's {NAME} skills are {v['skills']!r}, want every skill of skills/ at {SHA}: {want!r}")
    sm, conn = repos(docs)
    if sm[0] != {"url": PLATFORM, "ref": "v9.9.9", "path": "skills", "readOnly": True} or conn[0] != sm[0]:
        fail(f"release 9.9.9: the platform source is {sm[0]!r} (portal {conn[0]!r}), want it at ref v9.9.9")
    refused(package(tmp, "9.9.8", stamp=False), "[[ .SHA ]] placeholder", *ON)
    dev = package(tmp, "9.9.9-dev.1", stamp=False)
    docs = render(dev, *ON, *LIST_FLOORS, *PORTAL)
    if find(docs, "HelmRelease", NAME)["spec"]["values"]["skills"] != []:
        fail("a pre-release carries the unstamped skills")
    sm, _ = repos(docs)
    if sm[0] != {"url": PLATFORM, "path": "skills", "readOnly": True}:
        fail(f"a pre-release of no dev build shape: the platform source is {sm[0]!r}, want it without a ref (the default branch)")
    # A dev build (gitsemver 3) reads the platform source at the commit it was built from.
    sm, _ = repos(render(package(tmp, "9.9.9-r588f3d76t20261111120000ha6c12a3", stamp=False), *ON))
    if sm[0] != {"url": PLATFORM, "ref": "a6c12a3", "path": "skills", "readOnly": True}:
        fail(f"a dev build's platform source is {sm[0]!r}, want it at its h<sha> commit a6c12a3")
    # skills.platformSource.ref wins over both, on the source and the default agent's pins.
    REF = "fedcba9876543210fedcba9876543210fedcba98"
    for what, chart in (("release 9.9.9", release), ("dev build", dev)):
        docs = render(chart, *ON, "--set", f"skills.platformSource.ref={REF}")
        got = sorted(find(docs, "HelmRelease", NAME)["spec"]["values"]["skills"], key=lambda e: e["name"])
        want = [{"name": s, "path": f"skills/{s}", "git": {"url": PLATFORM, "commit": REF}} for s in skills]
        sm, _ = repos(docs)
        if got != want or sm[0] != {"url": PLATFORM, "ref": REF, "path": "skills", "readOnly": True}:
            fail(f"{what}, skills.platformSource.ref={REF}: {NAME} skills {got!r}, platform source {sm[0]!r}; want both at {REF}")
    for bad in ("main", "v1.2.3", "a6c12a3"):
        refused(META, "skills.platformSource.ref", *ON, "--set", f"skills.platformSource.ref={bad}")
print(f"ok: a stamped release pins the {len(skills)} skills of skills/ to its commit, an unstamped release fails, a pre-release carries none; a dev build reads its commit; skills.platformSource.ref pins both, a commit only")

# The skill-manager component: on with agent-manager, its own switch wins.
for what, args, want in (("agent-manager off (the default)", [], False), ("agent-manager on", ON, True),
                         ("its switch off", [*ON, *SM_OFF], False),
                         ("its switch on, agent-manager off", ["--set", "components.skill-manager.enabled=true"], True)):
    docs = render(META, *args)
    hr, oci = find(docs, "HelmRelease", "skill-manager"), find(docs, "OCIRepository", "skill-manager")
    conn = find(docs, "HelmRelease", "agent-platform-connectivity")["spec"]["values"]
    backend = conn["networkPolicy"].get("mcpBackends", {}).get("skill-manager")
    if bool(hr) != want or bool(oci) != want or ("skill-manager" in conn["components"]) != want or bool(backend) != want:
        fail(f"skill-manager, {what}: HelmRelease {bool(hr)}, OCIRepository {bool(oci)}, roster entry {'skill-manager' in conn['components']}, MCP backend {backend!r}; want {want} for each")
    if "skillManager" in conn or "skill-manager" in conn:
        fail(f"skill-manager, {what}: the skillManager / skill-manager blocks reach the connectivity release, whose root schema refuses them")
docs = render(META, *ON)
hr, oci = find(docs, "HelmRelease", "skill-manager"), find(docs, "OCIRepository", "skill-manager")
v = hr["spec"]["values"]
checks = {
    "chart": (oci["spec"]["url"], "oci://gsoci.azurecr.io/charts/giantswarm/skill-manager"),
    "range": (oci["spec"]["ref"]["semver"], values["components"]["skill-manager"]["versionRange"]),
    "targetNamespace": (hr["spec"]["targetNamespace"], "agent-platform"),
    "dependsOn": ([d["name"] for d in hr["spec"]["dependsOn"]], ["muster"]),
    "agent-manager dependsOn (no skill-manager)": ([d["name"] for d in find(docs, "HelmRelease", "agent-manager")["spec"]["dependsOn"]], ["muster", "kagent"]),
    "muster.mcpServer": (v["muster"]["mcpServer"], {"enabled": True, "auth": {"forwardToken": True, "requiredAudiences": ["dex-k8s-authenticator"]}}),
    "oauth.enabled": (v["oauth"]["enabled"], True),
    "MCP backend": (find(docs, "HelmRelease", "agent-platform-connectivity")["spec"]["values"]["networkPolicy"]["mcpBackends"]["skill-manager"],
                    {"namespace": "agent-platform", "ports": [8080], "podSelector": {"app.kubernetes.io/name": "skill-manager"}}),
}
for what, (got, want) in checks.items():
    if got != want:
        fail(f"skill-manager: {what} is {got!r}, want {want!r}")
refused(META, "skill-manager.sources", *ON, "--set", "skill-manager.sources[0]=https://github.com/acme/skills")
for flavor, extra in (("kubernetes", []), ("cilium", ["--api-versions", "cilium.io/v2"])):
    conn = find(render(META, *ON, *extra), "HelmRelease", "agent-platform-connectivity")["spec"]["values"]
    with tempfile.NamedTemporaryFile("w", suffix=".yaml") as f:
        yaml.safe_dump(conn, f)
        f.flush()
        kind = "CiliumNetworkPolicy" if flavor == "cilium" else "NetworkPolicy"
        cdocs = render(CONN, "-f", f.name, *extra)
        egress = find(cdocs, kind, "ap-agent-manager-egress") or find(cdocs, kind, "agent-platform-connectivity-agent-manager-egress")
        if not egress or "skill-manager" not in json.dumps(egress):
            fail(f"agent-manager's {flavor} egress policy does not reach the skill-manager: {egress and json.dumps(egress)[:300]!r}")
        ingress = next((d for d in cdocs if d["kind"] == kind and d["metadata"]["name"].endswith("mcp-backend-skill-manager")), None)
        if not ingress:
            fail(f"no {flavor} ingress policy for the skill-manager's MCP backend")
print("ok: skill-manager follows agent-manager, after muster, nothing waits for it, muster and agent-manager admitted to it")

# One skill source value: the skill-manager and the portal.
# The working tree is no published release (appVersion is not its version): no ref, as on a pre-release.
own = {"url": PLATFORM, "path": "skills", "readOnly": True}
sm, conn = repos(render(META, *ON, *PORTAL))
if sm != [own, *installation] or conn != installation:
    fail(f"below the portal's object-form floor: skill-manager {sm!r}, portal {conn!r}; want the platform source, then skillManager.sources, and the portal URLs only")
sm, conn = repos(render(META, *ON, *PORTAL, *LIST_FLOORS))
if sm != [own, *installation] or conn != [own, *installation]:
    fail(f"at the portal's object-form floor: skill-manager {sm!r}, portal {conn!r}; want the platform source, then skillManager.sources")
sm, conn = repos(render(META, *ON, *PORTAL, *LIST_FLOORS, "--set", "skills.platformSource.enabled=false"))
if sm != installation or conn != installation:
    fail(f"skills.platformSource.enabled=false: skill-manager {sm!r}, portal {conn!r}; want no platform source")
sm, conn = repos(render(META, *ON, *PORTAL, *LIST_FLOORS,
                       "--set", "skillManager.sources[0]=https://github.com/acme/skills",
                       "--set", "skillManager.sources[1].url=https://github.com/acme/skills.git",
                       "--set", "skillManager.sources[1].ref=v1",
                       "--set", "skillManager.sources[1].path=skills/",
                       "--set", "skillManager.sources[1].readOnly=true",
                       "--set", "skillManager.sources[2].url=https://github.com/acme/skills/",
                       "--set", "backstage.skillsRepositories[0]=https://github.com/acme/more.git",
                       "--set", "backstage.skillsRepositories[1]=https://github.com/acme/more/",
                       "--set", "backstage.skillsRepositories[2].url=https://github.com/acme/more",
                       "--set", "backstage.skillsRepositories[2].ref=main"))
if sm != [own, "https://github.com/acme/skills", {"url": "https://github.com/acme/skills.git", "ref": "v1", "path": "skills/", "readOnly": True}] \
        or conn != [own, "https://github.com/acme/more.git", {"url": "https://github.com/acme/more", "ref": "main"}]:
    fail(f"skillManager.sources and the portal's deprecated list: skill-manager {sm!r}, portal {conn!r}; want the entries as written, the portal's list replacing them, one entry per url + ref + path")
refused(META, "names the platform's own repository", *ON, "--set", f"skillManager.sources[0]={PLATFORM}.git")

# The portal through the skill-manager from agent-platform.backstage.skillManagerFloor on:
# no list of its own, GitHub egress kept for its skill pull requests.
def portal(docs: list[dict]) -> dict:
    return find(docs, "HelmRelease", "agent-platform-connectivity")["spec"]["values"]["backstage"]
p = portal(render(META, *ON, *PORTAL, *FLOORS))
if p.get("skillsRepositories") != [] or p.get("skillPullRequests") is not True:
    fail(f"at the portal's skill-manager floor: {p!r}; want skillsRepositories [] (over the connectivity default) and skillPullRequests: true")
p = portal(render(META, *ON, *PORTAL, *FLOORS, *SM_OFF))
if p.get("skillsRepositories") != [own, *installation] or "skillPullRequests" in p:
    fail(f"the skill-manager off: {p!r}; want the portal's own list")
refused(META, "the portal reads its skills from the skill-manager", *ON, *PORTAL, *FLOORS,
        "--set", "backstage.skillsRepositories[0]=https://github.com/acme/more")
docs = render(META, *ON, *PORTAL, *FLOORS, "--set", "backstage.skillPullRequests=true")
if "skillPullRequests" in find(docs, "HelmRelease", "backstage")["spec"].get("values", {}):
    fail("backstage.skillPullRequests reaches the backstage release, whose schema rejects it; add it to components.backstage.omitKeys")
print("ok: the portal reads skills from the skill-manager from its floor on, with no list of its own")
sm, _ = repos(render(META, *ON, "--set", "skills.platformSource.enabled=false", "--set", f"skillManager.sources[0].url={PLATFORM}", "--set", "skillManager.sources[0].path=skills"))
if sm != [{"url": PLATFORM, "path": "skills"}]:
    fail(f"with the platform source off an installation lists the repository itself: {sm!r}")

# agent-manager: through the skill-manager from its floor on, else the deprecated URL list.
objs = ["--set", "skillManager.sources[0].url=https://github.com/acme/skills", "--set", "skillManager.sources[0].ref=v1",
        "--set", "skillManager.sources[1].url=https://github.com/acme/skills", "--set", "skillManager.sources[1].path=x"]
for what, args in (("below agent-platform.agentManager.skillManagerFloor", [*ON, *objs]), ("the skill-manager off", [*ON, *FLOORS, *SM_OFF, *objs])):
    repositories, smv = am_skills(render(META, *args))
    if repositories != ["https://github.com/acme/skills"] or smv is not None:
        fail(f"agent-manager, {what}: skills.repositories {repositories!r}, skillManager {smv!r}; want skillManager.sources as URLs, once, no platform source, no skillManager")
repositories, _ = am_skills(render(META, *ON, "--set", "agent-manager.skills.repositories[0]=https://github.com/acme/own"))
if repositories != ["https://github.com/acme/own"]:
    fail(f"agent-manager below the floor: its deprecated list does not replace skillManager.sources: {repositories!r}")
repositories, smv = am_skills(render(META, *ON, *FLOORS))
if repositories != [] or smv != {"url": SM_URL}:
    fail(f"agent-manager at the floor: skills.repositories {repositories!r}, skillManager {smv!r}; want none and {{'url': {SM_URL!r}}}")
_, smv = am_skills(render(META, *ON, *FLOORS, "--set", "skill-manager.fullnameOverride=skills", "--set", "skill-manager.service.port=9090",
                          "--set", "components.skill-manager.targetNamespace=skills"))
if smv != {"url": "http://skills.skills.svc.cluster.local:9090"}:
    fail(f"agent-manager's skillManager.url does not follow the skill-manager's name, port and namespace: {smv!r}")
refused(META, "move the list to skillManager.sources", *ON, *FLOORS, "--set", "agent-manager.skills.repositories[0]=https://github.com/acme/own")

# The skill-manager's callers and the platform's bearer for agent-manager's migrate run.
def sm_values(docs: list[dict]) -> dict:
    return find(docs, "HelmRelease", "skill-manager")["spec"]["values"]


v = sm_values(render(META, *ON))
if v["networkPolicy"].get("ingressNamespaces") != [] or "serviceToken" in v:
    fail(f"skill-manager below agent-manager's floor, all in one namespace: ingressNamespaces {v['networkPolicy'].get('ingressNamespaces')!r}, serviceToken {v.get('serviceToken')!r}; want [] and none")
docs = render(META, *ON, *FLOORS, *PORTAL, "--set", "components.backstage.targetNamespace=portal", "--set", "skill-manager.networkPolicy.ingressNamespaces[0]=monitoring")
v = sm_values(docs)
if v["networkPolicy"]["ingressNamespaces"] != ["monitoring", "portal", "kagent"] or v.get("serviceToken") != {"secretName": "skill-manager-service-token"}:
    fail(f"skill-manager at agent-manager's floor: ingressNamespaces {v['networkPolicy']['ingressNamespaces']!r}, serviceToken {v.get('serviceToken')!r}; want the own entry, the portal's and kagent, and the generated Secret")
refused(META, "skill-manager.serviceToken.secretName", *ON, *FLOORS, "--set", "skill-manager.serviceToken.secretName=own")
v = sm_values(render(META, *ON, *FLOORS, "--set", "skill-manager.oauth.enabled=false"))
if "serviceToken" in v:
    fail(f"skill-manager without OAuth gets a service token: {v['serviceToken']!r}")
conn = find(docs, "HelmRelease", "agent-platform-connectivity")["spec"]["values"]
if conn["agentManager"].get("skillManager") != {"url": SM_URL, "namespace": "agent-platform", "serviceTokenSecret": "skill-manager-service-token"}:
    fail(f"the connectivity release's agentManager.skillManager is {conn['agentManager'].get('skillManager')!r}")
with tempfile.NamedTemporaryFile("w", suffix=".yaml") as f:
    yaml.safe_dump(conn, f)
    f.flush()
    cdocs = render(CONN, "-f", f.name)
secrets = sorted((d["metadata"]["namespace"], d["metadata"]["name"]) for d in cdocs if d["kind"] == "Secret" and d["metadata"]["name"] == "skill-manager-service-token")
if secrets != [("agent-platform", "skill-manager-service-token"), ("kagent", "skill-manager-service-token")]:
    fail(f"the service token Secrets are {secrets!r}, want one in the skill-manager's and one in the kagent namespace")
tokens = {d["data"]["token"] for d in cdocs if d["kind"] == "Secret" and d["metadata"]["name"] == "skill-manager-service-token"}
if len(tokens) != 1:
    fail("the two service token Secrets carry different tokens")
cron = find(cdocs, "CronJob", "agent-platform-connectivity-agent-manager-migrate") or next((d for d in cdocs if d["kind"] == "CronJob" and "migrate" in d["metadata"]["name"]), None)
env = cron["spec"]["jobTemplate"]["spec"]["template"]["spec"]["containers"][0]["env"] if cron else []
if {"name": "AGENT_MANAGER_SKILL_MANAGER_URL", "value": SM_URL} not in env or \
        {"name": "SKILL_MANAGER_TOKEN", "valueFrom": {"secretKeyRef": {"name": "skill-manager-service-token", "key": "token"}}} not in env:
    fail(f"agent-manager's migrate run does not call the skill-manager with the service token: {env!r}")
print("ok: the skill-manager admits its callers' namespaces; agent-manager's migrate run calls it with the generated service token")

schema = json.load(open(f"{META}/values.schema.json"))
try:
    import jsonschema
except ImportError:
    jsonschema = None
for entry in ("https://github.com/acme/skills", {"url": "https://github.com/acme/skills", "ref": "main", "path": "skills", "readOnly": True}):
    if jsonschema and (errors := [e.message for e in jsonschema.Draft202012Validator(schema).iter_errors({"skillManager": {"sources": [entry]}})]):
        fail(f"values.schema.json (draft 2020-12) refuses the skill source {entry!r}: {errors}")
if jsonschema and not list(jsonschema.Draft202012Validator(schema).iter_errors({"skillManager": {"sources": [{"url": "https://x", "branch": "main"}]}})):
    fail("values.schema.json takes a skill source with an unknown key")
if jsonschema and not list(jsonschema.Draft202012Validator(schema).iter_errors({"skills": {"repositories": ["https://github.com/acme/skills"]}})):
    fail("values.schema.json still takes skills.repositories")
print(f"ok: skillManager.sources feeds the skill-manager and the portal, agent-manager through skillManager.url from its floor; the platform source, the deprecated lists, dedup and the schema ({'draft 2020-12 checked' if jsonschema else 'helm only, jsonschema missing'})")


# The portal's meta agents: every role the portal reads
# (giantswarm/backstage plugins/agent-platform/config.d.ts, agentPlatform.metaAgents).
ROLES = ("generalInformation", "agentManagement", "workflowManagement", "platformOperations", "skillEvaluation", "skillCreation")
def app_config(docs: list[dict]) -> dict:
    vals = find(docs, "HelmRelease", "agent-platform-connectivity")["spec"]["values"]
    with tempfile.NamedTemporaryFile("w", suffix=".yaml") as f:
        yaml.safe_dump(vals, f)
        f.flush()
        cm = find(render(CONN, "-f", f.name), "ConfigMap", "agent-platform-backstage-app-config")
    return yaml.safe_load(cm["data"]["app-config.agent-platform.yaml"])["agentPlatform"]


for ns, args in {"kagent": [], "agents": ["--set", "kagent.namespaceOverride=agents", "--set", "model-manager.kagent.namespace=agents", "--set", "agent-manager.kagent.namespace=agents", "--set", "postgres.namespace=agents"]}.items():
    ref = f"agent-platform/{ns}/{NAME}"
    meta = app_config(render(META, *ON, *PORTAL, *args)).get("metaAgents")
    if meta != {k: ref for k in ROLES}:
        fail(f"agentPlatform.metaAgents is {meta!r}, want every role on {ref}")
meta = app_config(render(META, *ON, *PORTAL, "--set", "backstage.metaAgents.generalInformation=agent-platform/kagent/swarmgeist")).get("metaAgents")
if meta != {**{k: f"agent-platform/kagent/{NAME}" for k in ROLES}, "generalInformation": "agent-platform/kagent/swarmgeist"}:
    fail(f"backstage.metaAgents.generalInformation does not point that role elsewhere: {meta!r}")
meta = app_config(render(META, *ON, *PORTAL, "--set", f"components.{NAME}.enabled=false", "--set", "backstage.metaAgents.skillCreation=agent-platform/kagent/writer")).get("metaAgents")
if meta != {"skillCreation": "agent-platform/kagent/writer"}:
    fail(f"with the default agent off, an installation's role does not render alone: {meta!r}")
if "metaAgents" in app_config(render(META, *ON, *PORTAL, "--set", f"components.{NAME}.enabled=false")):
    fail(f"agentPlatform.metaAgents renders with components.{NAME} off")
if "metaAgents" in app_config(render(META, "--set", "components.kagent.enabled=true", *PORTAL)):
    fail("agentPlatform.metaAgents renders with agent-manager off")
print("ok: the portal's meta agents are the default agent while it is on, in the kagent namespace")
