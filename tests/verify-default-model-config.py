#!/usr/bin/env python3
"""Assert the default ModelConfig marker and kagent's own default ModelConfig switch.

Render, meta chart:
- kagent.defaultModelConfig.enabled true (the default): the kagent release
  carries the providers block; false: providers is exactly false (not null,
  which Helm's upgrade patch drops);
- neither defaultModelConfig nor modelConfigDefaults reaches the kagent
  release; the connectivity release carries both.

Render, connectivity chart:
- a kagent.modelConfigs[] entry with default: true carries the label
  agent-platform.giantswarm.io/is-default-model-config: "true", the others none;
- two entries with default: true fail the render, naming both;
- an Anthropic entry inherits promptCaching, cacheTTL and maxTokens per key
  from kagent.modelConfigDefaults.anthropic first, then
  kagent.providers.anthropic.config;
- with kagent on, the ValidatingAdmissionPolicy
  agent-platform-default-model-config (Fail, paramKind ModelConfig) and its
  binding (Deny, paramRef selecting the label in the request namespace,
  parameterNotFoundAction Allow); none with kagent off.

API server (tests/apiserver.py), with the ModelConfig CRD of the
components.kagent-crds floor and the rendered policy: with one labelled
ModelConfig in a namespace, a second labelled one is refused on create and on
update naming the first; an unlabelled one, one labelled "false", the first one
itself, a labelled one in another namespace and the first namespace after the
label moved are admitted.

Needs helm (gsoci.azurecr.io), openssl, PyYAML and github.com on the first run
(the envtest binaries). HELM selects the binary.

Usage: verify-default-model-config.py META_CHART CONNECTIVITY_CHART
"""
import json
import os
import re
import subprocess
import sys
import time

import yaml

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from apiserver import APIServer  # noqa: E402

HELM = os.environ.get("HELM", "helm")
LABEL = "agent-platform.giantswarm.io/is-default-model-config"
POLICY = "agent-platform-default-model-config"
KAGENT_NS = "kagent"
results = []


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
    labels = {n: (m["metadata"].get("labels") or {}).get(LABEL) for n, m in mcs.items()}
    if labels != {"a": "true", "b": None, "c": None}:
        fail(f"the default label renders as {labels}, want it on a only")
    ok("default: true labels the entry, the others carry no label")

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

    policy = [d for d in objs if d["metadata"]["name"] == POLICY]
    kinds = {d["kind"]: d for d in policy}
    if set(kinds) != {"ValidatingAdmissionPolicy", "ValidatingAdmissionPolicyBinding"}:
        fail(f"{POLICY} renders as {sorted(kinds)}, want a ValidatingAdmissionPolicy and its binding")
    vap, binding = kinds["ValidatingAdmissionPolicy"]["spec"], kinds["ValidatingAdmissionPolicyBinding"]["spec"]
    if vap["failurePolicy"] != "Fail" or vap["paramKind"] != {"apiVersion": "api.kagent.dev/v1alpha3", "kind": "ModelConfig"}:
        fail(f"{POLICY}: failurePolicy {vap['failurePolicy']}, paramKind {vap['paramKind']}")
    if "namespaceSelector" in vap["matchConstraints"]:
        fail(f"{POLICY} selects namespaces: {vap['matchConstraints']['namespaceSelector']}, want every namespace")
    want_ref = {"selector": {"matchLabels": {LABEL: "true"}}, "parameterNotFoundAction": "Allow"}
    if binding.get("paramRef") != want_ref or binding.get("validationActions") != ["Deny"]:
        fail(f"{POLICY} binding: paramRef {binding.get('paramRef')}, actions {binding.get('validationActions')}; want {want_ref}, [Deny]")
    ok(f"{POLICY}: Fail, paramKind ModelConfig, every namespace; binding Deny, the label's params of the request namespace, Allow without one")
    none = docs(connectivity(chart, "--set", "components.kagent.enabled=false"), "connectivity, kagent off")
    if any(d["metadata"]["name"] == POLICY for d in none):
        fail(f"{POLICY} renders with kagent off")
    ok(f"{POLICY} does not render with kagent off")
    return policy


def crd(meta_chart):
    values = yaml.safe_load(open(os.path.join(meta_chart, "values.yaml")))
    component = values["components"]["kagent-crds"]
    floor = re.match(r"^>=\s*(\S+)", component["versionRange"]).group(1)
    r = run([HELM, "template", "t", f"{component['repository']}/kagent-crds", "--version", floor])
    if r.returncode != 0:
        fail(f"helm template kagent-crds {floor}:\n{r.stderr}")
    found = [d for d in yaml.safe_load_all(r.stdout) if d and d.get("metadata", {}).get("name") == "modelconfigs.api.kagent.dev"]
    if not found:
        fail(f"kagent-crds {floor} carries no modelconfigs.api.kagent.dev")
    return yaml.safe_dump(found[0])


def model_config(name, ns, label=None):
    labels = {LABEL: label} if label is not None else {}
    return {"apiVersion": "api.kagent.dev/v1alpha3", "kind": "ModelConfig",
            "metadata": {"name": name, "namespace": ns, "labels": labels},
            "spec": {"provider": "Anthropic", "model": "m", "apiKeySecret": "s", "apiKeySecretKey": "k"}}


def prove(api, meta_chart, policy):
    def kubectl(args, stdin=None):
        return api.kubectl(args, stdin=stdin)

    def must(rc_out, what):
        if rc_out[0] != 0:
            fail(f"{what}:\n{rc_out[1]}")

    must(kubectl(["apply", "-f", "-"], crd(meta_chart)), "install the ModelConfig CRD")
    must(kubectl(["wait", "--for=condition=Established", "crd/modelconfigs.api.kagent.dev", "--timeout=60s"]), "the CRD is established")
    for ns in (KAGENT_NS, "org-acme"):
        must(kubectl(["create", "namespace", ns]), f"create namespace {ns}")
    must(kubectl(["create", "-f", "-"], yaml.safe_dump(model_config("first", KAGENT_NS, "true"))), "create the first labelled ModelConfig")
    must(kubectl(["apply", "-f", "-"], yaml.safe_dump_all(policy)), "install the policy")

    def create(obj):
        return kubectl(["create", "--dry-run=server", "-f", "-"], yaml.safe_dump(obj))

    def relabel(name, ns, value):
        return kubectl(["label", "--dry-run=server", "--overwrite", "-n", ns, "modelconfig.api.kagent.dev", name, f"{LABEL}={value}"])

    refused_create = model_config("second", KAGENT_NS, "true")
    deadline = time.time() + 60
    while True:
        rc, out = create(refused_create)
        if rc != 0 and f"ValidatingAdmissionPolicy '{POLICY}'" in out:
            break
        if time.time() > deadline:
            fail(f"a second labelled ModelConfig is still admitted 60 s after the policy was installed:\n{out}")
        time.sleep(1)

    def case(name, rc_out, refused):
        rc, out = rc_out
        good = (rc != 0 and f"ValidatingAdmissionPolicy '{POLICY}'" in out and "denied request" in out
                and "already has a default ModelConfig, first" in out and "failed expression" not in out) if refused else rc == 0
        results.append((good, name))
        print(("ok:  " if good else "FAIL:"), name, "|", out.strip().splitlines()[-1] if out.strip() else "")

    must(kubectl(["create", "-f", "-"], yaml.safe_dump(model_config("plain", KAGENT_NS))), "create an unlabelled ModelConfig")
    case("a second labelled ModelConfig in the namespace is refused, naming the first", create(refused_create), True)
    case("labelling an existing ModelConfig the second default is refused", relabel("plain", KAGENT_NS, "true"), True)
    case("an unlabelled ModelConfig is admitted", create(model_config("other", KAGENT_NS)), False)
    case("a ModelConfig labelled \"false\" is admitted", create(model_config("other", KAGENT_NS, "false")), False)
    case("the labelled ModelConfig itself is updated", kubectl(["annotate", "--dry-run=server", "-n", KAGENT_NS, "modelconfig.api.kagent.dev", "first", "x=y"]), False)
    case("a labelled ModelConfig in another namespace is admitted", create(model_config("second", "org-acme", "true")), False)
    must(kubectl(["label", "-n", KAGENT_NS, "modelconfig.api.kagent.dev", "first", f"{LABEL}-"]), "remove the label from the first")
    deadline = time.time() + 60
    while relabel("plain", KAGENT_NS, "true")[0] != 0:
        if time.time() > deadline:
            fail("the default does not move once the label left the first ModelConfig")
        time.sleep(1)
    case("the default moves once the label left the first ModelConfig", relabel("plain", KAGENT_NS, "true"), False)


def main():
    meta_chart, conn_chart = sys.argv[1], sys.argv[2]
    check_meta(meta_chart)
    policy = check_connectivity(conn_chart)
    api = APIServer()
    try:
        prove(api, meta_chart, policy)
    finally:
        api.stop()
    failed = [n for good, n in results if not good]
    if failed:
        fail(f"{len(failed)} case(s): {failed}")
    print(f"ok:  {len(results)} admission cases")


if __name__ == "__main__":
    main()
