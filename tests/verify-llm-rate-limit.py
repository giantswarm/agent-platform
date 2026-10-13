#!/usr/bin/env python3
"""Assert the LLM listener's rate limits (llmRouting.rateLimits, giantswarm/giantswarm#38004).

The connectivity chart renders one AgentgatewayPolicy, <name>-llm-rate-limit,
on the data-plane Gateway's LLM listener (sectionName), whose
traffic.rateLimit.local is the list as an installation writes it. This test
asserts:
  - empty (the default) renders no policy;
  - a list renders one policy, targeting the Gateway's LLM listener alone, with
    the entries as written (a token entry, a request entry with its burst);
  - a key renders through tpl: the per-agent key of values.yaml's example
    reads the kagent identity headers behind the Substrate egress predicate;
  - llmRouting.external.rateLimits renders <name>-llm-external-rate-limit on
    the external HTTPRoute alone, none while the endpoint is off;
  - llmRouting off renders neither, even with entries set;
  - the schema refuses an entry with both or neither of tokens and requests, a
    zero, an unknown unit, an empty key and an unknown field;
  - the entry schema's fields are the CRD's, so it cannot admit what the CRD
    prunes;
  - both rendered policies validate against agentgateway.dev_agentgatewaypolicies
    of the agentgateway packaging chart at the floor of
    components.agentgateway.versionRange (verify-kagent-crds.py's structural
    validator);
  - the meta chart forwards both lists to the connectivity release as written.

Usage: verify-llm-rate-limit.py <connectivity chart dir> <meta chart dir>
Network: gsoci.azurecr.io (the agentgateway packaging chart).
"""
import importlib.util
import json
import pathlib
import re
import subprocess
import sys
import tempfile

import yaml

HERE = pathlib.Path(__file__).resolve().parent
CONN_BASE = ["--set", "ingress.parentRefs[0].name=x", "--set", "ingress.mode=agentgateway-muster", "--set", "global.domain=example.com"]
LLM_ON = ["--set", "llmRouting.enabled=true"]
EXTERNAL_ON = ["--set", "llmRouting.external.enabled=true", "--set", "llmRouting.external.apiKeys.secretRef.name=keys",
               "--set", "global.gatewayApi.parentRefs[0].name=public"]
POLICY_SUFFIX = "-llm-rate-limit"
EXTERNAL_SUFFIX = "-llm-external-rate-limit"
EGRESS = '(source.unverifiedWorkload.namespace == "ate-system" && source.unverifiedWorkload.serviceAccount == "atenet-egress")'
PER_AGENT_TEMPLATE = ('{{ include "agent-platform.substrate.egressCall" . }} ? request.headers["x-kagent-agent-namespace"] + "/" + '
                      'request.headers["x-kagent-agent"] : source.unverifiedWorkload.namespace + "/" + source.unverifiedWorkload.serviceAccount')
PER_AGENT = PER_AGENT_TEMPLATE.replace('{{ include "agent-platform.substrate.egressCall" . }}', EGRESS)
PER_KEY = [{"tokens": 200000, "unit": "Minutes", "key": "apiKey.name"}]
TOKENS = [{"tokens": 200000, "unit": "Minutes"}]
MIXED = [{"tokens": 200000, "unit": "Minutes"}, {"requests": 600, "unit": "Minutes", "burst": 60}]
REFUSED = {
    "both tokens and requests": [{"tokens": 1, "requests": 1, "unit": "Minutes"}],
    "neither tokens nor requests": [{"unit": "Minutes"}],
    "tokens 0": [{"tokens": 0, "unit": "Minutes"}],
    "an unknown unit": [{"tokens": 1, "unit": "Days"}],
    "an empty key": [{"tokens": 1, "unit": "Minutes", "key": ""}],
    "an unknown field": [{"tokens": 1, "unit": "Minutes", "cost": 1}],
}
CRD_FILE = "agentgateway.dev_agentgatewaypolicies.yaml"


def fail(msg: str) -> None:
    sys.exit(f"FAIL: {msg}")


def template(chart: str, args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(["helm", "template", "t", chart, *args], capture_output=True, text=True)


def render(chart: str, args: list[str]) -> list[dict]:
    r = template(chart, args)
    if r.returncode != 0:
        fail(f"helm template failed unexpectedly:\n{r.stderr}")
    return [d for d in yaml.safe_load_all(r.stdout) if isinstance(d, dict)]


def limits(entries: list[dict]) -> list[str]:
    return ["--set-json", f"llmRouting.rateLimits={json.dumps(entries)}"]


def limits_external(entries: list[dict]) -> list[str]:
    return ["--set-json", f"llmRouting.external.rateLimits={json.dumps(entries)}"]


def policies(docs: list[dict], suffix: str = POLICY_SUFFIX) -> list[dict]:
    return [d for d in docs if d.get("kind") == "AgentgatewayPolicy" and d["metadata"]["name"].endswith(suffix)
            and (suffix == EXTERNAL_SUFFIX or not d["metadata"]["name"].endswith(EXTERNAL_SUFFIX))]


def floor_crd(meta: str) -> dict:
    """The AgentgatewayPolicy openAPIV3Schema of the packaging chart at the
    floor of components.agentgateway.versionRange."""
    agw = yaml.safe_load(open(f"{meta}/values.yaml"))["components"]["agentgateway"]
    m = re.match(r">=\s*(\d+\.\d+\.\d+)\s", agw["versionRange"])
    if not m:
        fail(f"components.agentgateway.versionRange {agw['versionRange']!r} has no >=X.Y.Z floor")
    url = f"{agw['repository']}/{agw.get('chart', 'agentgateway')}"
    with tempfile.TemporaryDirectory() as d:
        r = subprocess.run(["helm", "pull", url, "--version", m.group(1), "--untar", "--untardir", d], capture_output=True, text=True)
        if r.returncode != 0:
            fail(f"could not pull {url} {m.group(1)}\n{r.stderr}")
        found = list(pathlib.Path(d).rglob(CRD_FILE))
        if len(found) != 1:
            fail(f"{url} {m.group(1)} ships {len(found)} {CRD_FILE}, expected one")
        crd = yaml.safe_load(found[0].read_text())
    versions = {v["name"]: v for v in crd["spec"]["versions"]}
    if "v1alpha1" not in versions:
        fail(f"{CRD_FILE} at {m.group(1)} serves {sorted(versions)}, not v1alpha1")
    return {"version": m.group(1), "schema": versions["v1alpha1"]["schema"]["openAPIV3Schema"]}


def main(connectivity: str, meta: str) -> int:
    if found := policies(render(connectivity, [*CONN_BASE, *LLM_ON])):
        fail(f"llmRouting.rateLimits empty renders {[p['metadata']['name'] for p in found]}; empty renders no policy")
    print("ok: no rate-limit policy by default")

    for label, entries in (("a token entry", TOKENS), ("a token and a request entry with burst", MIXED)):
        found = policies(render(connectivity, [*CONN_BASE, *LLM_ON, *limits(entries)]))
        if len(found) != 1:
            fail(f"{label} renders {len(found)} rate-limit policies, expected one")
        spec = found[0]["spec"]
        want_target = [{"group": "gateway.networking.k8s.io", "kind": "Gateway", "name": "agentgateway", "sectionName": "llm"}]
        if spec.get("targetRefs") != want_target:
            fail(f"the rate-limit policy targets {spec.get('targetRefs')}, expected the data-plane Gateway's LLM listener alone {want_target}")
        if spec.get("traffic") != {"rateLimit": {"local": entries}}:
            fail(f"the rate-limit policy with {label} carries {spec.get('traffic')}, expected traffic.rateLimit.local {entries} as written")
    print("ok: entries render one policy on the Gateway's LLM listener, traffic.rateLimit.local as written")

    keyed = [*TOKENS, {"tokens": 400000, "unit": "Minutes", "key": PER_AGENT_TEMPLATE}]
    local = policies(render(connectivity, [*CONN_BASE, *LLM_ON, *limits(keyed)]))[0]["spec"]["traffic"]["rateLimit"]["local"]
    if local[1].get("key") != PER_AGENT:
        fail(f"the per-agent key does not render through tpl:\n  got      {local[1].get('key')!r}\n  expected {PER_AGENT!r}")
    print("ok: a key renders through tpl (the per-agent key reads the identity headers behind the Substrate egress predicate)")

    docs = render(connectivity, [*CONN_BASE, *LLM_ON, *EXTERNAL_ON])
    if found := policies(docs, EXTERNAL_SUFFIX):
        fail(f"llmRouting.external.rateLimits empty renders {[p['metadata']['name'] for p in found]}")
    found = policies(render(connectivity, [*CONN_BASE, *LLM_ON, *EXTERNAL_ON, *limits_external(PER_KEY)]), EXTERNAL_SUFFIX)
    if len(found) != 1:
        fail(f"llmRouting.external.rateLimits renders {len(found)} external rate-limit policies, expected one")
    spec = found[0]["spec"]
    want_target = [{"group": "gateway.networking.k8s.io", "kind": "HTTPRoute", "name": "agent-platform-connectivity-llm-external"}]
    if spec.get("targetRefs") != want_target or spec.get("traffic") != {"rateLimit": {"local": PER_KEY}}:
        fail(f"the external rate-limit policy is {spec}, expected {want_target} with {PER_KEY}")
    if found := policies(render(connectivity, [*CONN_BASE, *LLM_ON, *limits_external(PER_KEY)]), EXTERNAL_SUFFIX):
        fail("llmRouting.external.rateLimits renders a policy while the external endpoint is off")
    print("ok: llmRouting.external.rateLimits renders one policy on the external HTTPRoute alone, none while the endpoint is off")

    docs = render(connectivity, [*CONN_BASE, *EXTERNAL_ON, *limits(TOKENS), *limits_external(PER_KEY)])
    if found := policies(docs) + policies(docs, EXTERNAL_SUFFIX):
        fail(f"llmRouting off renders {[p['metadata']['name'] for p in found]}; no listener, no policy")
    print("ok: llmRouting off renders no rate-limit policy, entries set or not")

    for label, entries in REFUSED.items():
        r = template(connectivity, [*CONN_BASE, *LLM_ON, *limits(entries)])
        if r.returncode == 0 or "rateLimits" not in r.stderr:
            fail(f"the schema admits an entry with {label}: {entries}")
    print(f"ok: the schema refuses {', '.join(REFUSED)}")

    crd = floor_crd(meta)
    item = crd["schema"]["properties"]["spec"]["properties"]["traffic"]["properties"]["rateLimit"]["properties"]["local"]["items"]
    ours = json.loads((pathlib.Path(connectivity) / "files" / "llm" / "rate-limit.schema.json").read_text())
    if extra := sorted(set(ours["properties"]) - set(item["properties"])):
        fail(f"llmRouting.rateLimits admits {extra}, which {CRD_FILE} at {crd['version']} does not carry: the API server would prune it")
    if missing := sorted(set(item["properties"]) - set(ours["properties"])):
        fail(f"{CRD_FILE} at {crd['version']} carries {missing}, which llmRouting.rateLimits refuses: the floor moved, extend files/llm/rate-limit.schema.json")
    spec = importlib.util.spec_from_file_location("kagent_crds", HERE / "verify-kagent-crds.py")
    validator = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(validator)
    errors: list[str] = []
    docs = render(connectivity, [*CONN_BASE, *LLM_ON, *EXTERNAL_ON, *limits([*MIXED, *keyed[1:]]), *limits_external(PER_KEY)])
    for policy in policies(docs) + policies(docs, EXTERNAL_SUFFIX):
        validator.check(crd["schema"]["properties"]["spec"], policy["spec"], f"AgentgatewayPolicy/{policy['metadata']['name']}.spec", errors)
    if errors:
        fail(f"the rate-limit policy does not validate against {CRD_FILE} at {crd['version']}:\n  " + "\n  ".join(errors))
    print(f"ok: the entry schema's fields are the CRD's, and both policies validate against {CRD_FILE} of agentgateway {crd['version']}")

    docs = render(meta, ["-f", f"{meta}/ci/ci-values.yaml", *CONN_BASE, *LLM_ON, *limits(MIXED), *limits_external(PER_KEY)])
    conn = [d for d in docs if d.get("kind") == "HelmRelease" and d["metadata"]["name"] == "agent-platform-connectivity"]
    llm = conn[0]["spec"]["values"].get("llmRouting", {}) if conn else {}
    if llm.get("rateLimits") != MIXED or llm.get("external", {}).get("rateLimits") != PER_KEY:
        fail(f"the meta chart forwards llmRouting.rateLimits {llm.get('rateLimits')} and external.rateLimits "
             f"{llm.get('external', {}).get('rateLimits')}, expected {MIXED} and {PER_KEY}")
    print("ok: the meta chart forwards both lists to the connectivity release as written")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    sys.exit(main(sys.argv[1], sys.argv[2]))
