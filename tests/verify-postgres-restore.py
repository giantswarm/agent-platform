#!/usr/bin/env python3
"""Check ImageVolume admission selectors for source and recovery workloads."""

import os
import re
import subprocess
import sys
import tempfile
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


for name, namespace in (("kagent-pg", "kagent"), ("custom-pg", "custom-db")):
    output = render("--set", f"postgres.clusterName={name}",
                    "--set", f"postgres.namespace={namespace}",
                    "--set", f"kagent.namespaceOverride={namespace}",
                    "--set", f"model-manager.kagent.namespace={namespace}")
    with tempfile.NamedTemporaryFile(mode="w+", suffix=".yaml") as manifest:
        manifest.write(output)
        manifest.flush()
        picked = subprocess.run(
            [sys.executable, str(Path(__file__).with_name("pick-doc.py")),
             manifest.name, "PolicyException", f"{name}-image-volume"],
            text=True, capture_output=True,
        )
    assert picked.returncode == 0, f"Missing {name}-image-volume PolicyException"
    exception = picked.stdout
    assert re.search(r"kinds:\s*\n\s*- Pod\s*\n\s*- Job", exception)
    assert re.search(rf"namespaces:\s*\n\s*- {namespace}\s*\n", exception)
    assert "key: cnpg.io/cluster" in exception
    assert "operator: In" in exception
    selector = exception.split("key: cnpg.io/cluster", 1)[1]
    allowed = re.findall(r"^\s*- ([a-z0-9-]+)\s*$", selector, re.M)
    assert set(allowed) == {name, name + "-restore"}

for override in ("postgres.enabled=false", "kyvernoPolicies.enabled=false",
                 "postgres.vector.enabled=false", "postgres.vector.extensionImage.reference="):
    output = render("--set", override)
    assert "-image-volume" not in output, override
print("ok: source/recovery ImageVolume selectors, unrelated-name rejection, and extension gating")
