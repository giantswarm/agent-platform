#!/usr/bin/env python3
"""Assert that the agent chart line moves to 3.x only with what makes it safe
(giantswarm/giantswarm#38902, slice 8).

Agent chart 3.0 removes muster.requireApproval; agent-manager's migrate
rewrites it to `default: Ask` from agent-platform.agentManager.agentChartV3Floor,
and muster enforces `default: Ask` from agent-platform.muster.agentApprovalFloor.
agent-manager.agentChart.semver feeds agent-manager, its migrate run and the
agent-platform-operator (every organization's chart source), so:

- the default renders on the 2.x line;
- a 3.x line with agent-manager and muster at their floors and the default
  agent's range on 3.x renders, and the range reaches agent-manager's release,
  the connectivity release (the migrate run's input) and the default agent's
  source;
- a 3.x line is refused with agent-manager below its floor, with muster below
  its floor, with agent-manager.muster.toolApproval.enabled false, with the
  default agent's range on the other major, and a range spanning both majors
  is refused; the default agent on 3.x with the line on 2.x is refused too;
- agentChartV3Floor is the tool approval floor's release (one X.Y.Z each).

Stdlib only: documents are matched by text.
Usage: verify-agent-chart-line.py <meta chart dir>
Environment: HELM selects the binary.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys

META = sys.argv[1]
HELM = os.environ.get("HELM") or "helm"
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
    "--set", "components.kagent.enabled=true",
    "--set", "components.agent-manager.enabled=true",
]
V3 = ">=3.0.0 <4.0.0"
LINE = ["--set", f"agent-manager.agentChart.semver={V3}"]
AM = ["--set", "components.agent-manager.versionRange=>={floor}-rc.1 <4.0.0-0"]
MUSTER = ["--set", "components.muster.versionRange=>={floor} <6.0.0"]
DEFAULT_AGENT = ["--set", f"components.default-agent.versionRange={V3}"]

failures: list[str] = []


def fail(msg: str) -> None:
    failures.append(msg)


def run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([HELM, "template", "ap", META, *BASE, *args], capture_output=True, text=True)


def renders(what: str, *args: str) -> str:
    r = run(*args)
    if r.returncode != 0:
        fail(f"{what}: render failed:\n{r.stderr}")
        return ""
    return r.stdout


def refused(what: str, needle: str, *args: str) -> None:
    r = run(*args)
    if r.returncode == 0 or needle not in r.stderr:
        fail(f"{what}: not refused with {needle!r}:\n{r.stderr[-600:]}")


def floor(name: str) -> str:
    text = open(os.path.join(META, "templates", "_helpers.tpl"), encoding="utf-8").read()
    m = re.search(r'\{\{- define "agent-platform\.' + re.escape(name) + r'" -\}\}([^{]*)\{\{- end -\}\}', text)
    if not m or not re.fullmatch(r"\d+\.\d+\.\d+", m.group(1)):
        fail(f"agent-platform.{name} is not one X.Y.Z")
        return "0.0.0"
    return m.group(1)


am_floor = floor("agentManager.agentChartV3Floor")
muster_floor = floor("muster.agentApprovalFloor")
if am_floor != floor("agentManager.toolApprovalFloor"):
    fail(f"agentManager.agentChartV3Floor {am_floor} is not the tool approval floor's release")
am = [a.replace("{floor}", am_floor) for a in AM]
muster = [a.replace("{floor}", muster_floor) for a in MUSTER]

out = renders("default")
if V3 in out:
    fail("the default render carries the 3.x line")
if "'>=2.6.0 <3.0.0'" not in out and '">=2.6.0 <3.0.0"' not in out:
    fail("the default render does not carry the 2.x line")

out = renders("3.x line with its floors", *LINE, *am, *muster, *DEFAULT_AGENT)
if out.count(V3) < 3:
    fail(f"the 3.x line reaches {out.count(V3)} places; want agent-manager's release, the connectivity release and the default agent's source")

refused("3.x line, agent-manager below its floor", f"admits a release below {am_floor}", *LINE, *muster, *DEFAULT_AGENT)
refused("3.x line, muster below its floor", f"admits a release below {muster_floor}", *LINE, *am, *DEFAULT_AGENT)
refused("3.x line, tool approval off", "agent-manager.muster.toolApproval.enabled is false",
        *LINE, *am, *muster, *DEFAULT_AGENT, "--set", "agent-manager.muster.toolApproval.enabled=false")
refused("3.x line, default agent on 2.x", "select different agent chart majors", *LINE, *am, *muster)
refused("2.x line, default agent on 3.x", "select different agent chart majors", *DEFAULT_AGENT)
refused("range spanning both majors", "admits agent chart 2.x and 3.x", "--set", "agent-manager.agentChart.semver=>=2.6.0")

if failures:
    for f in failures:
        print(f"FAIL: {f}", file=sys.stderr)
    sys.exit(1)
print("agent chart line verified.")
