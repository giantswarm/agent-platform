#!/usr/bin/env python3
"""Assert the default ModelConfig marker and kagent's own default ModelConfig switch.

Render, meta chart:
- kagent.defaultModelConfig.enabled true (the default): the kagent release
  carries the providers block; false: providers is exactly false (not null,
  which Helm's upgrade patch drops) and nothing else of the release changes;
- defaultModelConfig never reaches the kagent release;
- with the switch off, the connectivity release still carries
  kagent.providers.anthropic.config, which its Anthropic modelConfigs entries
  inherit.

Render, connectivity chart:
- a kagent.modelConfigs[] entry with default: true (or "true") carries the
  annotation agent-platform.giantswarm.io/is-default-model-config: "true";
  false, "false" and an unset default carry none;
- two entries with default: true fail the render, naming both.

Needs helm and PyYAML. HELM selects the binary.

Usage: verify-default-model-config.py META_CHART CONNECTIVITY_CHART
"""
import json
import os
import subprocess
import sys

import yaml

HELM = os.environ.get("HELM", "helm")
ANNOTATION = "agent-platform.giantswarm.io/is-default-model-config"
KAGENT_NS = "kagent"


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
                "--set", "kagent.database.postgres.urlFile=/etc/kagent-db/uri", "--set", f"kagent.namespaceOverride={KAGENT_NS}",
                "--set", "components.kagent.enabled=true", *extra])


def release_values(objs, name):
    for d in objs:
        if d.get("kind") == "HelmRelease" and d["metadata"]["name"] == name:
            return d["spec"]["values"]
    fail(f"no {name} HelmRelease in the render")


def entry(name, **kw):
    return {"name": name, "provider": "Anthropic", "model": "m", "apiKeySecret": "s", **kw}


def model_configs(objs):
    return {d["metadata"]["name"]: d for d in objs if d.get("kind") == "ModelConfig"}


def check_meta(chart):
    on = docs(meta(chart), "meta, default")
    kagent = release_values(on, "kagent")
    if not isinstance(kagent.get("providers"), dict) or "anthropic" not in kagent["providers"]:
        fail(f"default: the kagent release's providers is {kagent.get('providers')!r}, want the anthropic block")
    off = docs(meta(chart, "--set", "kagent.defaultModelConfig.enabled=false"), "meta, defaultModelConfig off")
    kagent_off = release_values(off, "kagent")
    if kagent_off.get("providers") is not False:
        fail(f"defaultModelConfig.enabled=false: the kagent release's providers is {kagent_off.get('providers')!r}, want false")
    if {k: v for k, v in kagent_off.items() if k != "providers"} != {k: v for k, v in kagent.items() if k != "providers"}:
        fail("defaultModelConfig.enabled=false changes the kagent release's values beside providers")
    ok("the kagent release carries providers by default and providers: false with defaultModelConfig.enabled=false")
    for objs in (on, off):
        if "defaultModelConfig" in release_values(objs, "kagent"):
            fail("the kagent release carries the meta-only key defaultModelConfig")
    ok("defaultModelConfig does not reach the kagent release")
    conn = release_values(off, "agent-platform-connectivity").get("kagent", {})
    inherited = ((conn.get("providers") or {}).get("anthropic") or {}).get("config")
    if not isinstance(inherited, dict) or inherited.get("promptCaching") is not True:
        fail(f"defaultModelConfig.enabled=false: the connectivity release's kagent.providers.anthropic.config is {inherited!r}, want the meta default the modelConfigs entries inherit")
    ok("with defaultModelConfig.enabled=false the connectivity release keeps kagent.providers.anthropic.config")


def check_connectivity(chart):
    entries = [entry("a", default=True), entry("b"), entry("c", default=False), entry("d", default="false"), entry("e", default="true")]
    objs = docs(connectivity(chart, "--set-json", f"kagent.modelConfigs={json.dumps([entries[0], *entries[1:4]])}"), "connectivity, one default")
    mcs = model_configs(objs)
    marks = {n: (m["metadata"].get("annotations") or {}).get(ANNOTATION) for n, m in mcs.items()}
    if marks != {"a": "true", "b": None, "c": None, "d": None}:
        fail(f"the default annotation renders as {marks}, want it on a only")
    ok('default: true annotates the entry; false, "false" and unset carry no marker')

    quoted = model_configs(docs(connectivity(chart, "--set-json", f"kagent.modelConfigs={json.dumps([entries[4]])}"), "connectivity, quoted true"))
    if (quoted["e"]["metadata"].get("annotations") or {}).get(ANNOTATION) != "true":
        fail(f'default: "true" does not annotate the entry: {quoted["e"]["metadata"]}')
    ok('default: "true" annotates the entry')

    two = [entry("a", default=True), entry("b", default=True)]
    r = connectivity(chart, "--set-json", f"kagent.modelConfigs={json.dumps(two)}")
    if r.returncode == 0 or "more than one entry default (a, b)" not in r.stderr:
        fail(f"two default entries render, or fail for another reason:\n{r.stderr}")
    ok("two default entries fail the render, naming both")


def main():
    check_meta(sys.argv[1])
    check_connectivity(sys.argv[2])


if __name__ == "__main__":
    main()
