#!/usr/bin/env python3
"""Assert the gateway's workspace tools prefix (giantswarm/agent-platform#966).

klaus-gateway calls the workspace-manager's tools through muster as the person,
by the name muster gives them: workspaces.toolsPrefix (the chart's
--workspace-tools-prefix, from klaus-gateway 4.3.0; empty turns workspaces off
in the gateway). The meta chart derives it onto the klaus-gateway release while
workspaces are on. Each case below pins one property:

- workspaces on, the klaus-gateway range's floor at
  agent-platform.klausGateway.workspacesFloor (4.3.0, a release candidate of it
  too): the release carries workspaces.toolsPrefix x_workspace-manager_, x_ and
  workspace-manager.muster.mcpServer.name and _ (a renamed MCPServer moves it);
- below the floor (the default range, an earlier release's closed schema refuses
  the block) or with workspaces off: no workspaces block on the release;
- an own klausGateway.workspaces.toolsPrefix that agrees passes, one that differs
  fails the render naming the key.

Deliberately stdlib-only: the CI image has no PyYAML. HELM selects the binary.
"""

import os
import re
import subprocess
import sys

HELM = os.environ.get("HELM", "helm")
FIXTURE = "tests/fixtures/workspaces-values.yaml"
BASE = [
    "--set", "components.flux.enabled=false",
    "--set", "components.klaus-gateway.enabled=true",
    "--set", "kagent.harness.snapshotLocation=s3://ci/agents",
]
ON = ["-f", FIXTURE]
FLOOR = ["--set", "components.klaus-gateway.versionRange=>=4.3.0 <5.0.0"]
RC = ["--set", "components.klaus-gateway.versionRange=>=4.3.0-rc.1 <5.0.0"]
PREFIX = "    workspaces:\n      toolsPrefix: x_workspace-manager_\n"


def fail(msg: str) -> None:
    sys.exit(f"FAIL: {msg}")


def ok(msg: str) -> None:
    print(f"ok: {msg}")


def helm(chart: str, flags: list, expect_fail: bool = False) -> str:
    cmd = [HELM, "template", "t", chart, "-f", f"{chart}/ci/ci-values.yaml", *BASE, *flags]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if expect_fail:
        if r.returncode == 0:
            fail(f"the render passed but had to fail: {' '.join(flags)}")
        return r.stderr
    if r.returncode != 0:
        fail(f"the render failed: {' '.join(flags)}\n{r.stderr}")
    return r.stdout


def gateway_values(render: str) -> str:
    """The values block of the klaus-gateway HelmRelease."""
    for doc in render.split("\n---\n"):
        if re.search(r"^kind: HelmRelease$", doc, re.M) and re.search(r"^  name: klaus-gateway$", doc, re.M):
            m = re.search(r"^  values:\n((?:    .*\n|\n)*)", doc + "\n", re.M)
            return m.group(1) if m else ""
    fail("no klaus-gateway HelmRelease in the render")
    return ""


def main(chart: str) -> int:
    for what, flags in (("the 4.3.0 floor", FLOOR), ("a 4.3.0 release candidate floor", RC)):
        values = gateway_values(helm(chart, ON + flags))
        if PREFIX not in values:
            fail(f"workspaces on at {what}: the klaus-gateway release lacks workspaces.toolsPrefix x_workspace-manager_:\n{values}")
    ok("workspaces on from the 4.3.0 floor (a release candidate too): workspaces.toolsPrefix x_workspace-manager_")

    values = gateway_values(helm(chart, ON + FLOOR + ["--set", "workspace-manager.muster.mcpServer.name=workspaces"]))
    if "      toolsPrefix: x_workspaces_\n" not in values:
        fail(f"a renamed MCPServer did not move the prefix:\n{values}")
    ok("the prefix follows workspace-manager.muster.mcpServer.name")

    for what, flags in (("below the floor", ON), ("workspaces off", FLOOR)):
        values = gateway_values(helm(chart, flags))
        if re.search(r"^    workspaces:", values, re.M):
            fail(f"{what}: the klaus-gateway release carries a workspaces block:\n{values}")
    ok("below the floor or with workspaces off: no workspaces block")

    own = ["--set", "klausGateway.workspaces.toolsPrefix=x_workspace-manager_"]
    if PREFIX not in gateway_values(helm(chart, ON + FLOOR + own)):
        fail("an own toolsPrefix that agrees did not reach the release")
    err = helm(chart, ON + FLOOR + ["--set", "klausGateway.workspaces.toolsPrefix=x_other_"], expect_fail=True)
    if "klausGateway.workspaces.toolsPrefix (x_other_) differs" not in err:
        fail(f"an own toolsPrefix that differs failed for another reason:\n{err}")
    ok("an own toolsPrefix that agrees passes, one that differs fails naming the key")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
