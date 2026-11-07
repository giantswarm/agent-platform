#!/usr/bin/env python3
"""Assert the charts declare no model: an installation's ModelConfigs are
objects declared in GitOps next to its agents.

Render, meta chart:
- the kagent release carries providers: false on every render (not null, which
  Helm's upgrade patch drops, and not {}, which merges the kagent chart's
  defaults back in), so the kagent chart renders no default-model-config;
- no meta-only key (defaultModelConfig, modelConfigs)
  reaches the kagent or the connectivity release;
- kagent.providers, kagent.defaultModelConfig and kagent.modelConfigs each fail
  the render, naming the key and the replacement.

Render, connectivity chart:
- kagent on renders no ModelConfig;
- kagent.providers and kagent.modelConfigs each fail the render, naming the key
  and the replacement.

Needs helm and PyYAML. HELM selects the binary.

Usage: verify-default-model-config.py META_CHART CONNECTIVITY_CHART
"""
import os
import subprocess
import sys

import yaml

HELM = os.environ.get("HELM", "helm")
REPLACEMENT = "Declare each model as a ModelConfig object"


def fail(msg):
    sys.exit(f"FAIL: {msg}")


def ok(msg):
    print(f"ok:  {msg}")


def run(cmd):
    return subprocess.run(cmd, capture_output=True, text=True)


def docs(r, what):
    if r.returncode != 0:
        fail(f"{what}: the render failed:\n{r.stderr}")
    return [d for d in yaml.safe_load_all(r.stdout) if d]


def meta(chart, *extra):
    return run([HELM, "template", "t", chart, "--namespace", "agent-platform",
                "-f", os.path.join(chart, "ci", "ci-values.yaml"),
                "--set", "components.kagent.enabled=true", "--set", "kagent.harness.snapshotLocation=s3://a/b",
                "--set", "kagent.database.postgres.urlFile=/etc/kagent-db/uri",
                "--set", "global.identity.issuerUrl=https://dex.example.com", *extra])


def connectivity(chart, *extra):
    return run([HELM, "template", "t", chart, "--namespace", "agent-platform",
                "--set", "ingress.parentRefs[0].name=x", "--set", "kagent.harness.snapshotLocation=s3://a/b",
                "--set", "kagent.database.postgres.urlFile=/etc/kagent-db/uri", "--set", "kagent.namespaceOverride=kagent",
                "--set", "components.kagent.enabled=true", *extra])


def release_values(objs, name):
    for d in objs:
        if d.get("kind") == "HelmRelease" and d["metadata"]["name"] == name:
            return d["spec"]["values"]
    fail(f"no {name} HelmRelease in the render")


def refused(r, key, what):
    if r.returncode == 0:
        fail(f"{what}: {key} rendered; it must fail the render")
    if key not in r.stderr or REPLACEMENT not in r.stderr:
        fail(f"{what}: {key} failed without naming the key and the replacement:\n{r.stderr}")


def check_meta(chart):
    objs = docs(meta(chart), "meta, default")
    kagent = release_values(objs, "kagent")
    if "providers" not in kagent or kagent["providers"] is not False:
        fail(f"the kagent release's providers is {kagent.get('providers')!r}, want false")
    ok("the kagent release carries providers: false")
    for key in ("defaultModelConfig", "modelConfigs"):
        if key in kagent:
            fail(f"the kagent release carries the meta-only key {key}")
    conn = release_values(objs, "agent-platform-connectivity")
    for key in ("providers", "defaultModelConfig", "modelConfigs"):
        if key in (conn.get("kagent") or {}):
            fail(f"the connectivity release carries kagent.{key}")
    ok("no meta-only key reaches the kagent or the connectivity release")
    for args, key in ((["--set", "kagent.providers.anthropic.model=m"], "kagent.providers"),
                      (["--set", "kagent.defaultModelConfig.enabled=false"], "kagent.defaultModelConfig"),
                      (["--set", "kagent.modelConfigs[0].name=m"], "kagent.modelConfigs")):
        refused(meta(chart, *args), key, "meta")
    ok("kagent.providers, kagent.defaultModelConfig and kagent.modelConfigs fail the meta render, naming the replacement")


def check_connectivity(chart):
    objs = docs(connectivity(chart), "connectivity, kagent on")
    if any(d.get("kind") == "ModelConfig" for d in objs):
        fail("the connectivity chart rendered a ModelConfig")
    ok("the connectivity chart renders no ModelConfig")
    for args, key in ((["--set", "kagent.providers.anthropic.model=m"], "kagent.providers"),
                      (["--set", "kagent.modelConfigs[0].name=m"], "kagent.modelConfigs")):
        refused(connectivity(chart, *args), key, "connectivity")
    ok("kagent.providers and kagent.modelConfigs fail the connectivity render, naming the replacement")


def main():
    check_meta(sys.argv[1])
    check_connectivity(sys.argv[2])


if __name__ == "__main__":
    main()
