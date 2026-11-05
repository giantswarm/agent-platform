#!/usr/bin/env python3
"""Assert the default ModelConfig marker and kagent's own default ModelConfig switch.

Render, meta chart:
- kagent.defaultModelConfig.enabled true (the default): the kagent release
  carries the providers block; false: providers is exactly false (not null,
  which Helm's upgrade patch drops);
- neither defaultModelConfig nor modelConfigDefaults reaches the kagent
  release; the connectivity release carries both.

Render, connectivity chart:
- a kagent.modelConfigs[] entry with default: true carries the annotation
  agent-platform.giantswarm.io/is-default-model-config: "true", the others none;
- two entries with default: true fail the render, naming both;
- an Anthropic entry inherits promptCaching, cacheTTL and maxTokens per key
  from kagent.modelConfigDefaults.anthropic first, then
  kagent.providers.anthropic.config;
- no admission policy holds the marker.

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
        leaked = {"defaultModelConfig", "modelConfigDefaults"} & set(release_values(objs, "kagent"))
        if leaked:
            fail(f"the kagent release carries the meta-only keys {sorted(leaked)}")
        conn = release_values(objs, "agent-platform-connectivity").get("kagent", {})
        if "defaultModelConfig" not in conn or "modelConfigDefaults" not in conn:
            fail("the connectivity release lacks kagent.defaultModelConfig or kagent.modelConfigDefaults")
    ok("defaultModelConfig and modelConfigDefaults reach the connectivity release only")


def check_connectivity(chart):
    entries = [entry("a", default=True), entry("b"), entry("c", default=False)]
    objs = docs(connectivity(chart, "--set-json", f"kagent.modelConfigs={json.dumps(entries)}"), "connectivity, one default")
    mcs = model_configs(objs)
    marks = {n: (m["metadata"].get("annotations") or {}).get(ANNOTATION) for n, m in mcs.items()}
    if marks != {"a": "true", "b": None, "c": None}:
        fail(f"the default annotation renders as {marks}, want it on a only")
    if any(ANNOTATION in (m["metadata"].get("labels") or {}) for m in mcs.values()):
        fail("the default marker renders as a label")
    ok("default: true annotates the entry, the others carry no marker")

    two = [entry("a", default=True), entry("b", default=True)]
    r = connectivity(chart, "--set-json", f"kagent.modelConfigs={json.dumps(two)}")
    if r.returncode == 0 or "more than one entry default (a, b)" not in r.stderr:
        fail(f"two default entries render, or fail for another reason:\n{r.stderr}")
    ok("two default entries fail the render, naming both")

    inherit = connectivity(chart, "--set-json", f"kagent.modelConfigs={json.dumps([entry('a'), entry('own', maxTokens=10)])}",
                           "--set", "kagent.providers.anthropic.config.maxTokens=1000",
                           "--set", "kagent.providers.anthropic.config.promptCaching=true",
                           "--set", "kagent.providers.anthropic.config.cacheTTL=1h",
                           "--set", "kagent.modelConfigDefaults.anthropic.maxTokens=2000",
                           "--set", "kagent.modelConfigDefaults.anthropic.promptCaching=false")
    mcs = model_configs(docs(inherit, "connectivity, modelConfigDefaults"))
    want = {"maxTokens": 2000, "promptCaching": False}
    if mcs["a"]["spec"].get("anthropic") != want:
        fail(f"modelConfigDefaults.anthropic does not win per key: spec.anthropic {mcs['a']['spec'].get('anthropic')}, want {want}")
    if mcs["own"]["spec"]["anthropic"].get("maxTokens") != 10:
        fail(f"an entry's own maxTokens does not win over modelConfigDefaults: {mcs['own']['spec']['anthropic']}")
    only_defaults = connectivity(chart, "--set-json", f"kagent.modelConfigs={json.dumps([entry('a')])}", "--set", "kagent.providers=null",
                                 "--set", "kagent.modelConfigDefaults.anthropic.maxTokens=32000")
    got = model_configs(docs(only_defaults, "connectivity, modelConfigDefaults without providers"))["a"]["spec"].get("anthropic")
    if got != {"maxTokens": 32000}:
        fail(f"without kagent.providers, modelConfigDefaults.anthropic.maxTokens is not inherited: {got}")
    ok("Anthropic entries inherit per key from modelConfigDefaults.anthropic, then providers.anthropic.config")

    if any(d.get("kind") in ("ValidatingAdmissionPolicy", "ValidatingAdmissionPolicyBinding") and "default-model" in d["metadata"]["name"] for d in objs):
        fail("an admission policy holds the default marker")
    ok("no admission policy holds the default marker")

def main():
    check_meta(sys.argv[1])
    check_connectivity(sys.argv[2])


if __name__ == "__main__":
    main()
