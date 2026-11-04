#!/usr/bin/env python3
"""Assert the Agent Manager the meta chart ships (giantswarm/giantswarm#38970).

- components.agent-manager-assistant renders one HelmRelease of the agent chart,
  named agent-manager-assistant, into kagent, labelled managed-by agent-platform,
  only while it, kagent and muster are on; its range is agent-manager's
  agentChart.semver; its values are agents/agent-manager/values.yaml with the
  derived muster.url, iconUrl, toolset and modelConfig.name.
- Unstamped (any build but a release tag) it carries no skills; stamped by
  hack/stamp-release-commit.sh, the packaged chart pins every skill of skills/
  to that commit — the tag pipeline's step, simulated on a copy.
- skills.repositories feeds agent-manager.skills.repositories and
  backstage.skillsRepositories: the platform's own source first, the deprecated
  lists appended, one entry per URL; objects only from the consumer's floor on.
- The connectivity chart wires the portal's meta agents to it while it is on.

usage: verify-agent-manager-assistant.py <meta chart dir> <connectivity chart dir>
"""
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
KAGENT = ["--set", "components.kagent.enabled=true"]


def fail(msg: str) -> None:
    print(f"FAIL: {msg}", file=sys.stderr)
    sys.exit(1)


def render(chart: str, *args: str) -> list[dict]:
    r = subprocess.run([HELM, "template", "ap", chart, *BASE, *args], capture_output=True, text=True)
    if r.returncode != 0:
        fail(f"render of {chart} {' '.join(args)} failed:\n{r.stderr}")
    return [d for d in yaml.safe_load_all(r.stdout) if d]


def find(docs: list[dict], kind: str, name: str) -> dict | None:
    return next((d for d in docs if d["kind"] == kind and d["metadata"]["name"] == name), None)


def repos(docs: list[dict]) -> tuple[list, list]:
    am = find(docs, "HelmRelease", "agent-manager")["spec"]["values"]["skills"]["repositories"]
    conn = find(docs, "HelmRelease", "agent-platform-connectivity")["spec"]["values"]["backstage"]["skillsRepositories"]
    return am, conn


values = yaml.safe_load(open(f"{META}/values.yaml"))
semver = values["agent-manager"]["agentChart"]["semver"]
if values["components"][NAME]["versionRange"] != semver:
    fail(f"components.{NAME}.versionRange {values['components'][NAME]['versionRange']!r} is not agent-manager.agentChart.semver {semver!r}: the Agent Manager runs the agent chart line agent-manager composes")
skills = sorted(p.parent.name for p in (ROOT / "skills").glob("*/SKILL.md"))

# Off with kagent or muster off, or its own switch.
for what, args in {"kagent off (the default)": [], "its switch off": [*KAGENT, "--set", f"components.{NAME}.enabled=false"],
                   "muster off": [*KAGENT, "--set", "components.muster.enabled=false"]}.items():
    docs = render(META, *args)
    if find(docs, "HelmRelease", NAME):
        fail(f"{what}: the {NAME} HelmRelease renders")
    conn = find(docs, "HelmRelease", "agent-platform-connectivity")
    if conn and NAME in conn["spec"]["values"]["components"]:
        fail(f"{what}: the roster forwards {NAME} to the connectivity release (gatedRoster)")
print("ok: no Agent Manager without kagent, muster or its switch")

docs = render(META, *KAGENT, "--set", "dicebear.route.hostnames[0]=avatars.example.com")
hr, oci = find(docs, "HelmRelease", NAME), find(docs, "OCIRepository", NAME)
if not hr or not oci:
    fail(f"kagent on: no {NAME} HelmRelease/OCIRepository")
spec, v = hr["spec"], hr["spec"]["values"]
checks = {
    "releaseName": (spec["releaseName"], NAME),
    "targetNamespace": (spec["targetNamespace"], "kagent"),
    "managed-by label": (hr["metadata"]["labels"].get("agent-platform.giantswarm.io/managed-by"), "agent-platform"),
    "chart": (oci["spec"]["url"], "oci://gsoci.azurecr.io/charts/giantswarm/agent"),
    "range": (oci["spec"]["ref"]["semver"], semver),
    "dependsOn": (sorted(d["name"] for d in spec["dependsOn"]), ["kagent", "muster"]),
    "displayName": (v["agent"]["displayName"], "Agent Manager"),
    "iconUrl": (v["agent"]["iconUrl"], f"https://avatars.example.com/v1/{NAME}.png"),
    "toolset": (v["toolset"], ["preset:agent-platform"]),
    "muster.url": (v["muster"]["url"], "http://muster.agent-platform.svc.cluster.local:8090/mcp"),
    "modelConfig (unset: the chart's default)": (v.get("modelConfig"), None),
    "skills (unstamped)": (v["skills"], []),
    "no global": ("global" in v, False),
}
for what, (got, want) in checks.items():
    if got != want:
        fail(f"{NAME}: {what} is {got!r}, want {want!r}")
v = find(render(META, *KAGENT, "--set", "agentManagerAssistant.modelConfig=claude-opus"), "HelmRelease", NAME)["spec"]["values"]
if v.get("modelConfig") != {"name": "claude-opus"} or "iconUrl" in v["agent"]:
    fail(f"{NAME}: agentManagerAssistant.modelConfig is not its modelConfig.name, or an iconUrl renders without an avatar host: {v.get('modelConfig')!r}, {v['agent']!r}")
print(f"ok: {NAME} renders the agent chart into kagent with the derived values, no skills unstamped")

# The release pipeline's stamp, on a copy of the chart, packaged as the pipeline packages it.
with tempfile.TemporaryDirectory() as tmp:
    chart = f"{tmp}/agent-platform"
    shutil.copytree(META, chart, symlinks=True)
    bad = subprocess.run([str(ROOT / "hack/stamp-release-commit.sh"), "v1.2.3", chart], capture_output=True, text=True)
    if bad.returncode == 0:
        fail("hack/stamp-release-commit.sh accepts a tag in place of a commit id")
    r = subprocess.run([str(ROOT / "hack/stamp-release-commit.sh"), SHA, chart], capture_output=True, text=True)
    if r.returncode != 0:
        fail(f"hack/stamp-release-commit.sh failed:\n{r.stderr}")
    again = subprocess.run([str(ROOT / "hack/stamp-release-commit.sh"), SHA, chart], capture_output=True, text=True)
    if again.returncode == 0:
        fail("hack/stamp-release-commit.sh passes on a chart with nothing to stamp")
    p = subprocess.run([HELM, "package", chart, "-d", tmp], capture_output=True, text=True)
    if p.returncode != 0:
        fail(f"helm package of the stamped chart failed:\n{p.stderr}")
    tgz = next(pathlib.Path(tmp).glob("agent-platform-*.tgz"))
    v = find(render(str(tgz), *KAGENT), "HelmRelease", NAME)["spec"]["values"]
    want = [{"name": s, "path": f"skills/{s}", "git": {"url": PLATFORM, "commit": SHA}} for s in skills]
    if sorted(v["skills"], key=lambda e: e["name"]) != want:
        fail(f"the stamped, packaged chart's {NAME} skills are {v['skills']!r}, want every skill of skills/ at {SHA}: {want!r}")
print(f"ok: the stamped, packaged chart pins the {len(skills)} skills of skills/ to the release commit")

# One skill-repository value, both consumers.
own = {"url": PLATFORM, "ref": f"v{yaml.safe_load(open(f'{META}/Chart.yaml'))['version']}", "path": "skills", "readOnly": True}
on = [*KAGENT, "--set", "components.agent-manager.enabled=true", "--set", "components.backstage.enabled=true"]
am, conn = repos(render(META, *on))
if am != conn or am != [PLATFORM, *values["skills"]["repositories"]]:
    fail(f"default skill repositories: agent-manager {am!r}, portal {conn!r}; want the platform's URL, then skills.repositories")
legacy = ["--set", "agent-manager.skills.repositories[0]=https://github.com/acme/skills",
          "--set", f"backstage.skillsRepositories[0]={values['skills']['repositories'][0]}",
          "--set", "backstage.skillsRepositories[1]=https://github.com/acme/more"]
floors = ["--set", "components.agent-manager.versionRange=>=2.99.0 <3.0.0", "--set", "components.backstage.versionRange=>=3.99.0 <4.0.0"]
am, conn = repos(render(META, *on, *legacy, *floors))
want = [own, *values["skills"]["repositories"], "https://github.com/acme/skills", "https://github.com/acme/more"]
if am != want or conn != want:
    fail(f"skill repositories with the deprecated lists set, at object-form floors: agent-manager {am!r}, portal {conn!r}; want {want!r}")
print("ok: skills.repositories feeds both consumers, platform first, deprecated lists merged, objects from the floors on")

# The portal's meta agents.
def app_config(docs: list[dict]) -> dict:
    vals = find(docs, "HelmRelease", "agent-platform-connectivity")["spec"]["values"]
    with tempfile.NamedTemporaryFile("w", suffix=".yaml") as f:
        yaml.safe_dump(vals, f)
        f.flush()
        cm = find(render(CONN, "-f", f.name), "ConfigMap", "agent-platform-backstage-app-config")
    return yaml.safe_load(cm["data"]["app-config.agent-platform.yaml"])["agentPlatform"]


ref = f"agent-platform/kagent/{NAME}"
meta = app_config(render(META, *on)).get("metaAgents")
if meta != {k: ref for k in ("agentManagement", "skillEvaluation", "skillCreation", "workflowManagement")}:
    fail(f"agentPlatform.metaAgents is {meta!r}, want every role on {ref}")
if "metaAgents" in app_config(render(META, *on, "--set", f"components.{NAME}.enabled=false")):
    fail(f"agentPlatform.metaAgents renders with components.{NAME} off")
print("ok: the portal's meta agents are the Agent Manager while it is on")
