#!/usr/bin/env python3
"""Check ImageVolume admission selectors for source and recovery workloads."""

import os
import re
import subprocess
import sys
from pathlib import Path

chart = sys.argv[1]
helm = os.environ.get("HELM", "helm")
values = Path(chart) / "ci/test-postgres-backup-aws-values.yaml"


def render(*overrides):
    return subprocess.check_output(
        [helm, "template", "t", chart, "-f", str(values),
         "--set", "kyvernoPolicies.enabled=true", *overrides],
        text=True,
    )


for name, namespace in (("kagent-pg", "kagent"), ("custom-pg", "kagent")):
    output = render("--set", f"postgres.clusterName={name}",
                    "--set", f"postgres.namespace={namespace}",
                    "--set", f"kagent.namespaceOverride={namespace}")
    exception = next(part for part in output.split("\n---")
                     if "kind: PolicyException" in part
                     and f"name: {name}-image-volume" in part)
    assert re.search(r"kinds:\s*\n\s*- Pod\s*\n\s*- Job", exception)
    assert re.search(rf"namespaces:\s*\n\s*- {namespace}\s*\n", exception)
    assert "key: cnpg.io/cluster" in exception
    assert "operator: In" in exception
    selector = exception.split("key: cnpg.io/cluster", 1)[1]
    allowed = re.findall(r"^\s*- ([a-z0-9-]+)\s*$", selector, re.M)
    for candidate, expected in ((name, True), (name + "-restore", True),
                                (name + "-restore-other", False),
                                ("unrelated", False)):
        assert (candidate in allowed) == expected, (candidate, allowed)
    assert set(allowed) == {name, name + "-restore"}

output = render("--set", "postgres.vector.enabled=false")
assert "-image-volume" not in output
output = render("--set", "postgres.vector.extensionImage.reference=")
assert "-image-volume" not in output
print("ok: source/recovery ImageVolume selectors, unrelated-name rejection, and extension gating")
