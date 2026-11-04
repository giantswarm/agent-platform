#!/usr/bin/env python3
"""Assert the Agent Manager the meta chart ships (giantswarm/giantswarm#38970).

- components.agent-manager-assistant renders one HelmRelease of the agent chart,
  named agent-manager-assistant, into the kagent component's namespace,
  labelled managed-by agent-platform (the HelmRelease and the Agent), only
  while it, kagent, muster and agent-manager are on; its range is
  agent-manager's agentChart.semver; its values are
  agents/agent-manager/values.yaml with the derived muster.url, iconUrl,
  toolset, labels and modelConfig.name.
- The working tree and a pre-release carry no skills; stamped by
  hack/stamp-release-commit.sh, the packaged chart pins every skill of skills/
  to that commit (the tag pipeline's step, simulated on a copy); a release
  packaged unstamped fails its render.
- skills.repositories feeds agent-manager.skills.repositories and
  backstage.skillsRepositories: the platform's source first, only at a
  consumer floor that takes objects, without a ref unless the chart is a published release, off with
  skills.platformSource.enabled=false; a consumer's own deprecated list
  replaces skills.repositories; one entry per url + ref + path; an entry for
  the platform's repository fails the render. The schema takes URLs and objects.
- The connectivity chart wires the portal's meta agents to it while it is on.

usage: verify-agent-manager-assistant.py <meta chart dir> <connectivity chart dir>
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
NAME = "agent-manager-assistant"
LABEL = {"agent-platform.giantswarm.io/managed-by": "agent-platform"}
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
    am = find(docs, "HelmRelease", "agent-manager")["spec"]["values"]["skills"]["repositories"]
    conn = find(docs, "HelmRelease", "agent-platform-connectivity")["spec"]["values"]["backstage"]["skillsRepositories"]
    return am, conn


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
    fail(f"components.{NAME}.versionRange {values['components'][NAME]['versionRange']!r} is not agent-manager.agentChart.semver {semver!r}: the Agent Manager runs the agent chart line agent-manager composes")
skills = sorted(p.parent.name for p in (ROOT / "skills").glob("*/SKILL.md"))
installation = values["skills"]["repositories"]

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
print("ok: no Agent Manager without kagent, muster, agent-manager or its switch")

docs = render(META, *ON, "--set", "dicebear.route.hostnames[0]=avatars.example.org")
hr, oci = find(docs, "HelmRelease", NAME), find(docs, "OCIRepository", NAME)
if not hr or not oci:
    fail(f"{NAME}: no HelmRelease/OCIRepository with kagent, muster and agent-manager on")
spec, v = hr["spec"], hr["spec"]["values"]
checks = {
    "releaseName": (spec["releaseName"], NAME),
    "targetNamespace": (spec["targetNamespace"], "kagent"),
    "HelmRelease managed-by label": (hr["metadata"]["labels"].get("agent-platform.giantswarm.io/managed-by"), "agent-platform"),
    "Agent labels": (v.get("labels"), LABEL),
    "chart": (oci["spec"]["url"], "oci://gsoci.azurecr.io/charts/giantswarm/agent"),
    "range": (oci["spec"]["ref"]["semver"], semver),
    "dependsOn": (sorted(d["name"] for d in spec["dependsOn"]), ["agent-manager", "kagent", "muster"]),
    "displayName": (v["agent"]["displayName"], "Agent Manager"),
    "iconUrl (the route's hostname)": (v["agent"]["iconUrl"], f"https://avatars.example.org/v1/{NAME}.png"),
    "toolset": (v["toolset"], ["preset:agent-platform"]),
    "muster.url": (v["muster"]["url"], "http://muster.agent-platform.svc.cluster.local:8090/mcp"),
    "modelConfig (unset: the chart's default)": (v.get("modelConfig"), None),
    "skills (the working tree is unstamped)": (v["skills"], []),
    "no global": ("global" in v, False),
}
for what, (got, want) in checks.items():
    if got != want:
        fail(f"{NAME}: {what} is {got!r}, want {want!r}")
v = find(render(META, *ON), "HelmRelease", NAME)["spec"]["values"]
if v["agent"].get("iconUrl") != f"https://avatars.example.com/v1/{NAME}.png":
    fail(f"{NAME}: without a route hostname the avatar is not on avatars.<global.domain>: {v['agent']!r}")
v = find(render(META, *ON, "--set", "components.dicebear.enabled=false", "--set", "agentManagerAssistant.modelConfig=claude-opus"), "HelmRelease", NAME)["spec"]["values"]
if v.get("modelConfig") != {"name": "claude-opus"} or "iconUrl" in v["agent"]:
    fail(f"{NAME}: agentManagerAssistant.modelConfig is not its modelConfig.name, or an iconUrl renders without DiceBear: {v.get('modelConfig')!r}, {v['agent']!r}")
hr = find(render(META, *ON, "--set", "kagent.namespaceOverride=agents"), "HelmRelease", NAME)
if hr["spec"]["targetNamespace"] != "agents":
    fail(f"{NAME}: targetNamespace {hr['spec']['targetNamespace']!r} does not follow kagent.namespaceOverride=agents")
print(f"ok: {NAME} renders the agent chart into the kagent namespace with the derived values, no skills unstamped")

# The release pipeline's stamp, on copies of the chart, packaged as the pipeline packages them.
with tempfile.TemporaryDirectory() as tmp:
    bad = subprocess.run([str(ROOT / "hack/stamp-release-commit.sh"), "v1.2.3", META], capture_output=True, text=True)
    if bad.returncode == 0:
        fail("hack/stamp-release-commit.sh accepts a tag in place of a commit id")
    release = package(tmp, "9.9.9", stamp=True)
    again = subprocess.run([str(ROOT / "hack/stamp-release-commit.sh"), SHA, f"{tmp}/9.9.9/agent-platform"], capture_output=True, text=True)
    if again.returncode == 0:
        fail("hack/stamp-release-commit.sh passes on a chart with nothing to stamp")
    docs = render(release, *ON, *FLOORS, *PORTAL)
    v = find(docs, "HelmRelease", NAME)["spec"]["values"]
    want = [{"name": s, "path": f"skills/{s}", "git": {"url": PLATFORM, "commit": SHA}} for s in skills]
    if sorted(v["skills"], key=lambda e: e["name"]) != want:
        fail(f"the stamped release's {NAME} skills are {v['skills']!r}, want every skill of skills/ at {SHA}: {want!r}")
    am, _ = repos(docs)
    if am[0] != {"url": PLATFORM, "ref": "v9.9.9", "path": "skills", "readOnly": True}:
        fail(f"release 9.9.9: the platform source is {am[0]!r}, want it at ref v9.9.9")
    refused(package(tmp, "9.9.8", stamp=False), "[[ .SHA ]] placeholder", *ON)
    dev = package(tmp, "9.9.9-dev.1", stamp=False)
    docs = render(dev, *ON, *FLOORS, *PORTAL)
    if find(docs, "HelmRelease", NAME)["spec"]["values"]["skills"] != []:
        fail("a pre-release carries the unstamped skills")
    am, _ = repos(docs)
    if am[0] != {"url": PLATFORM, "path": "skills", "readOnly": True}:
        fail(f"a pre-release's platform source is {am[0]!r}, want it without a ref (the default branch)")
print(f"ok: a stamped release pins the {len(skills)} skills of skills/ to its commit, an unstamped release fails, a pre-release carries none")

# One skill-repository value, both consumers.
# The working tree is no published release (appVersion is not its version): no ref, as on a pre-release.
own = {"url": PLATFORM, "path": "skills", "readOnly": True}
am, conn = repos(render(META, *ON, *PORTAL))
if am != installation or conn != installation:
    fail(f"below the object-form floors: agent-manager {am!r}, portal {conn!r}; want skills.repositories as URLs and no platform source")
am, conn = repos(render(META, *ON, *PORTAL, *FLOORS))
if am != [own, *installation] or conn != [own, *installation]:
    fail(f"at the object-form floors: agent-manager {am!r}, portal {conn!r}; want the platform source, then skills.repositories")
am, conn = repos(render(META, *ON, *PORTAL, *FLOORS, "--set", "skills.platformSource.enabled=false"))
if am != installation or conn != installation:
    fail(f"skills.platformSource.enabled=false: agent-manager {am!r}, portal {conn!r}; want no platform source")
am, conn = repos(render(META, *ON, *PORTAL, *FLOORS,
                       "--set", "agent-manager.skills.repositories[0]=https://github.com/acme/skills",
                       "--set", "backstage.skillsRepositories[0]=https://github.com/acme/more.git",
                       "--set", "backstage.skillsRepositories[1]=https://github.com/acme/more/",
                       "--set", "backstage.skillsRepositories[2].url=https://github.com/acme/more",
                       "--set", "backstage.skillsRepositories[2].ref=main"))
if am != [own, "https://github.com/acme/skills"] or conn != [own, "https://github.com/acme/more.git", {"url": "https://github.com/acme/more", "ref": "main"}]:
    fail(f"the deprecated lists: agent-manager {am!r}, portal {conn!r}; want each replacing skills.repositories for its consumer, one entry per url + ref + path")
refused(META, "names the platform's own repository", *ON, *FLOORS, "--set", f"skills.repositories[0]={PLATFORM}.git")
am, _ = repos(render(META, *ON, *FLOORS, "--set", "skills.platformSource.enabled=false", "--set", f"skills.repositories[0].url={PLATFORM}", "--set", "skills.repositories[0].path=skills"))
if am != [{"url": PLATFORM, "path": "skills"}]:
    fail(f"with the platform source off an installation lists the repository itself: {am!r}")
schema = json.load(open(f"{META}/values.schema.json"))
try:
    import jsonschema
except ImportError:
    jsonschema = None
for entry in ("https://github.com/acme/skills", {"url": "https://github.com/acme/skills", "ref": "main", "path": "skills", "readOnly": True}):
    if jsonschema and (errors := [e.message for e in jsonschema.Draft202012Validator(schema).iter_errors({"skills": {"repositories": [entry]}})]):
        fail(f"values.schema.json (draft 2020-12) refuses the skill source {entry!r}: {errors}")
if jsonschema and not list(jsonschema.Draft202012Validator(schema).iter_errors({"skills": {"repositories": [{"url": "https://x", "branch": "main"}]}})):
    fail("values.schema.json takes a skill source with an unknown key")
print(f"ok: skills.repositories feeds both consumers; the platform source, the deprecated lists, dedup and the schema ({'draft 2020-12 checked' if jsonschema else 'helm only, jsonschema missing'})")


# The portal's meta agents.
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
    if meta != {k: ref for k in ("agentManagement", "skillEvaluation", "skillCreation", "workflowManagement")}:
        fail(f"agentPlatform.metaAgents is {meta!r}, want every role on {ref}")
if "metaAgents" in app_config(render(META, *ON, *PORTAL, "--set", f"components.{NAME}.enabled=false")):
    fail(f"agentPlatform.metaAgents renders with components.{NAME} off")
if "metaAgents" in app_config(render(META, "--set", "components.kagent.enabled=true", *PORTAL)):
    fail("agentPlatform.metaAgents renders with agent-manager off")
print("ok: the portal's meta agents are the Agent Manager while it is on, in the kagent namespace")
