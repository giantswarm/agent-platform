#!/usr/bin/env python3
"""Assert the default agent the meta chart ships (giantswarm/giantswarm#39057).

- components.default-agent renders one HelmRelease of the agent chart, named
  default-agent, into the kagent component's namespace, labelled managed-by
  agent-platform (the HelmRelease and the Agent), only while it, kagent and
  muster are on; its range is agent-manager's agentChart.semver; its values are
  agents/default-agent/values.yaml with the derived muster.url, iconUrl,
  toolset (preset:full), labels, displayName and modelConfig.name.
- The working tree and a pre-release carry no skills; stamped by
  hack/stamp-release-commit.sh, the packaged chart pins every skill of skills/
  to that commit (the tag pipeline's step, simulated on a copy); a release
  packaged unstamped fails its render.
- The avatar renders only while the avatar route does; extra labels on the
  entry are taken; a values file that does not parse, or carries the release
  placeholder outside skills[].git.commit, fails the render.
- klaus-gateway's a2a.defaultAgent is <kagent namespace>/default-agent while it
  is on; an own value wins.
- The connectivity chart points every portal agent role at it while it is on;
  backstage.metaAgents wins per role.

usage: verify-default-agent.py <meta chart dir> <connectivity chart dir>
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
NAME = "default-agent"
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
ON = ["--set", "components.kagent.enabled=true"]
# The avatar HTTPRoute renders (dicebear.route.enabled: auto) where Envoy Gateway is served.
ROUTE = ["--api-versions", "gateway.envoyproxy.io/v1alpha1"]
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
skills = sorted(p.parent.name for p in (ROOT / "skills").glob("*/SKILL.md"))

# Off with kagent, muster or its own switch off.
for what, args in {"kagent off (the default)": [],
                   "its switch off": [*ON, "--set", f"components.{NAME}.enabled=false"],
                   "muster off": [*ON, "--set", "components.muster.enabled=false"]}.items():
    docs = render(META, *args)
    if find(docs, "HelmRelease", NAME):
        fail(f"{what}: the {NAME} HelmRelease renders")
    conn = find(docs, "HelmRelease", "agent-platform-connectivity")
    if conn and NAME in conn["spec"]["values"]["components"]:
        fail(f"{what}: the roster forwards {NAME} to the connectivity release (gatedRoster)")
print("ok: no default agent without kagent, muster or its switch")

docs = render(META, *ON, *ROUTE, "--set", "dicebear.route.hostnames[0]=avatars.example.org", "--set", f"components.{NAME}.labels.team=bumblebee")
hr, oci = find(docs, "HelmRelease", NAME), find(docs, "OCIRepository", NAME)
if not hr or not oci:
    fail(f"{NAME}: no HelmRelease/OCIRepository with kagent and muster on")
spec, v = hr["spec"], hr["spec"]["values"]
checks = {
    "releaseName": (spec["releaseName"], NAME),
    "install/upgrade disableWait": ((spec["install"].get("disableWait"), spec["upgrade"].get("disableWait")), (True, True)),
    "targetNamespace": (spec["targetNamespace"], "kagent"),
    "HelmRelease managed-by label": (hr["metadata"]["labels"].get("agent-platform.giantswarm.io/managed-by"), "agent-platform"),
    "HelmRelease extra label": (hr["metadata"]["labels"].get("team"), "bumblebee"),
    "Agent labels": (v.get("labels"), LABEL),
    "chart": (oci["spec"]["url"], "oci://gsoci.azurecr.io/charts/giantswarm/agent"),
    "range": (oci["spec"]["ref"]["semver"], semver),
    "dependsOn": (sorted(d["name"] for d in spec["dependsOn"]), ["kagent", "muster"]),
    "displayName": (v["agent"]["displayName"], "General Purpose Agent"),
    "iconUrl (the route's hostname)": (v["agent"]["iconUrl"], f"https://avatars.example.org/v1/{NAME}.png"),
    "toolset": (v["toolset"], ["preset:full"]),
    "muster.url": (v["muster"]["url"], "http://muster.agent-platform.svc.cluster.local:8090/mcp"),
    "modelConfig (unset: the chart's default)": (v.get("modelConfig"), None),
    "skills (the working tree is unstamped)": (v["skills"], []),
    "no global": ("global" in v, False),
}
for what, (got, want) in checks.items():
    if got != want:
        fail(f"{NAME}: {what} is {got!r}, want {want!r}")
v = find(render(META, *ON, *ROUTE), "HelmRelease", NAME)["spec"]["values"]
if v["agent"].get("iconUrl") != f"https://avatars.example.com/v1/{NAME}.png":
    fail(f"{NAME}: without a route hostname the avatar is not on avatars.<global.domain>: {v['agent']!r}")
for what, args in {"auto without Envoy Gateway": [], "the route off": [*ROUTE, "--set", "dicebear.route.enabled=false"]}.items():
    v = find(render(META, *ON, *args), "HelmRelease", NAME)["spec"]["values"]
    if "iconUrl" in v["agent"]:
        fail(f"{NAME}: an iconUrl renders with the avatar route not rendered ({what}): {v['agent']!r}")
v = find(render(META, *ON, "--set", "components.dicebear.enabled=false", "--set", "defaultAgent.modelConfig=claude-opus", "--set", "defaultAgent.displayName=Helper"), "HelmRelease", NAME)["spec"]["values"]
if v.get("modelConfig") != {"name": "claude-opus"} or "iconUrl" in v["agent"] or v["agent"]["displayName"] != "Helper":
    fail(f"{NAME}: defaultAgent.modelConfig is not its modelConfig.name, defaultAgent.displayName is not its displayName, or an iconUrl renders without DiceBear: {v.get('modelConfig')!r}, {v['agent']!r}")
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
    v = find(render(release, *ON), "HelmRelease", NAME)["spec"]["values"]
    want = [{"name": s, "path": f"skills/{s}", "git": {"url": PLATFORM, "commit": SHA}} for s in skills]
    if sorted(v["skills"], key=lambda e: e["name"]) != want:
        fail(f"the stamped release's {NAME} skills are {v['skills']!r}, want every skill of skills/ at {SHA}: {want!r}")
    refused(package(tmp, "9.9.8", stamp=False), "[[ .SHA ]] placeholder", *ON)
    dev = package(tmp, "9.9.9-dev.1", stamp=False)
    if find(render(dev, *ON), "HelmRelease", NAME)["spec"]["values"]["skills"] != []:
        fail("a pre-release carries the unstamped skills")
    values_file = pathlib.Path(f"{tmp}/9.9.9-dev.1/agent-platform/agents/{NAME}/values.yaml")
    original = values_file.read_text()
    values_file.write_text(original + "plugins:\n  - git:\n      commit: \"[[ .SHA ]]\"\n")
    refused(str(values_file.parents[2]), "outside skills[].git.commit", *ON)
    values_file.write_text(original + "agent: [unclosed\n")
    refused(str(values_file.parents[2]), "does not parse", *ON)
print(f"ok: a stamped release pins the {len(skills)} skills of skills/ to its commit, an unstamped release fails, a pre-release carries none")

# klaus-gateway's fallback agent for a channel turn that names none.
KG = ["--set", "components.klaus-gateway.enabled=true"]
a2a = find(render(META, *ON, *KG), "HelmRelease", "klaus-gateway")["spec"]["values"].get("a2a", {})
if a2a.get("defaultAgent") != f"kagent/{NAME}":
    fail(f"klaus-gateway a2a.defaultAgent is {a2a.get('defaultAgent')!r}, want kagent/{NAME}")
a2a = find(render(META, *ON, *KG, "--set", "kagent.namespaceOverride=agents"), "HelmRelease", "klaus-gateway")["spec"]["values"].get("a2a", {})
if a2a.get("defaultAgent") != f"agents/{NAME}":
    fail(f"klaus-gateway a2a.defaultAgent is {a2a.get('defaultAgent')!r} with kagent.namespaceOverride=agents, want agents/{NAME}")
a2a = find(render(META, *ON, *KG, "--set", "klausGateway.a2a.defaultAgent=helper"), "HelmRelease", "klaus-gateway")["spec"]["values"].get("a2a", {})
if a2a.get("defaultAgent") != "helper":
    fail(f"an own klausGateway.a2a.defaultAgent does not win: {a2a.get('defaultAgent')!r}")
a2a = find(render(META, *ON, *KG, "--set", f"components.{NAME}.enabled=false"), "HelmRelease", "klaus-gateway")["spec"]["values"].get("a2a", {})
if a2a.get("defaultAgent"):
    fail(f"klaus-gateway a2a.defaultAgent is {a2a.get('defaultAgent')!r} with components.{NAME} off")
print(f"ok: klaus-gateway falls back to <kagent namespace>/{NAME}; an own a2a.defaultAgent wins")


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
    fail(f"with {NAME} off, an installation's role does not render alone: {meta!r}")
if "metaAgents" in app_config(render(META, *ON, *PORTAL, "--set", f"components.{NAME}.enabled=false")):
    fail(f"agentPlatform.metaAgents renders with components.{NAME} off")
if "metaAgents" in app_config(render(META, *PORTAL)):
    fail("agentPlatform.metaAgents renders with kagent off")
print(f"ok: the portal's agent roles are {NAME} while it is on, in the kagent namespace; backstage.metaAgents wins per role")
