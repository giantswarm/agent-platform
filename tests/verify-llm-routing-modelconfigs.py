#!/usr/bin/env python3
"""Assert the ModelConfig reconcile of llmRouting (giantswarm/agent-platform#1057).

The modelconfig-llm-routing policies write the LLM listener URL into a
ModelConfig without a baseUrl; Helm never owns that field, so turning routing
off would leave it on the removed listener. The connectivity chart's hook
<release>-llm-routing-modelconfigs puts every ModelConfig on the side of the
toggle the release says. This test asserts:
  - the policies mark what they write: the annotation
    agent-platform.giantswarm.io/llm-routing-base-url with the URL they set;
  - the hook renders, post-install and post-upgrade, with routing on and off,
    wherever the policies would (kagent, llmRouting.modelConfigPolicy, the GA
    MutatingAdmissionPolicy API), and nowhere else; the hook identity then
    carries get, list and patch on modelconfigs, nothing more;
  - the script, run against a stub kubectl over a fixture namespace:
      off removes a marked baseUrl and an unmarked listener URL on an object
      Helm does not manage, each with its mark, and touches nothing else (an
      operator's provider URL, a Helm release's own listener URL);
      on marks an unmarked listener URL, takes the chart's baseUrl off a
      provider the listener no longer serves and off a ChatGPT login
      (openAI.credentialType chatgptOAuth, which the OpenAI policy skips) and
      routes no ModelConfig
      without a baseUrl (the policies do at its next write; the default
      ModelConfig's cutover stays the README's second step);
      a second run on the result changes nothing;
      without the ModelConfig API (a first install before kagent's) it exits 0.

Usage: verify-llm-routing-modelconfigs.py <connectivity chart dir>
"""
import json
import os
import pathlib
import subprocess
import sys
import tempfile

import yaml

MARK = "agent-platform.giantswarm.io/llm-routing-base-url"
ORIGIN = "http://agentgateway.default.svc:8081"
MAP = ["-a", "admissionregistration.k8s.io/v1/MutatingAdmissionPolicy"]
HOOK = "t-llm-routing-modelconfigs"

# A kubectl that answers the hook's three calls from files in $STUB.
STUB_KUBECTL = """#!/bin/sh
case " $* " in
  *" api-resources "*) cat "$STUB/api-resources" ;;
  *" get "*) cat "$STUB/modelconfigs.json" ;;
  *" patch "*)
    for a in "$@"; do case "$a" in --patch-file=*) f=${a#--patch-file=} ;; esac; done
    name=$5
    printf '%s %s\\n' "$name" "$(cat "$f")" >>"$STUB/patched" ;;
  *) echo "stub kubectl: unexpected $*" >&2; exit 2 ;;
esac
"""


def fail(msg):
    print(f"FAIL: {msg}")
    sys.exit(1)


def render(chart, *args):
    out = subprocess.run(
        ["helm", "template", "t", chart, "-f", f"{chart}/ci/test-llm-routing-values.yaml", *args],
        capture_output=True, text=True)
    if out.returncode:
        fail(f"render {' '.join(args)}: {out.stderr.strip()}")
    return [d for d in yaml.safe_load_all(out.stdout) if d]


def find(docs, kind, name):
    return next((d for d in docs if d["kind"] == kind and d["metadata"]["name"] == name), None)


def mc(name, provider, base_url=None, mark=None, helm=False, chatgpt=False):
    meta = {"name": name, "resourceVersion": str(len(name))}
    if mark:
        meta["annotations"] = {MARK: mark}
    if helm:
        meta["labels"] = {"app.kubernetes.io/managed-by": "Helm"}
    spec = {"provider": provider}
    if base_url:
        key = {"Anthropic": "anthropic", "OpenAI": "openAI"}[provider]
        spec[key] = {"baseUrl": base_url}
    if chatgpt:
        spec.setdefault("openAI", {})["credentialType"] = "chatgptOAuth"
    return {"metadata": meta, "spec": spec}


FIXTURE = [
    mc("marked", "Anthropic", ORIGIN, mark=ORIGIN),
    mc("legacy-openai", "OpenAI", ORIGIN + "/v1"),
    mc("helm-explicit", "Anthropic", ORIGIN, helm=True),
    mc("operator", "Anthropic", "https://api.anthropic.com"),
    mc("operator-marked-stale", "Anthropic", "https://proxy.example.com", mark=ORIGIN),
    mc("bare", "Anthropic"),
    mc("bare-openai", "OpenAI"),
    mc("gemini", "Gemini"),
    mc("chatgpt-marked", "OpenAI", ORIGIN + "/v1", mark=ORIGIN + "/v1", chatgpt=True),
    mc("chatgpt-bare", "OpenAI", chatgpt=True),
]


def run_hook(script, items, api=True):
    with tempfile.TemporaryDirectory() as stub:
        s = pathlib.Path(stub)
        (s / "bin").mkdir()
        k = s / "bin" / "kubectl"
        k.write_text(STUB_KUBECTL)
        k.chmod(0o755)
        (s / "api-resources").write_text("modelconfigs.api.kagent.dev\nagents.api.kagent.dev\n" if api else "")
        (s / "modelconfigs.json").write_text(json.dumps({"items": items}))
        env = dict(os.environ, STUB=stub, PATH=f"{s / 'bin'}:{os.environ['PATH']}", TMPDIR=stub)
        out = subprocess.run(["sh", "-eu", "-c", script], capture_output=True, text=True, env=env)
        if out.returncode:
            fail(f"hook script exited {out.returncode}: {out.stderr.strip()}")
        patched = {}
        if (s / "patched").exists():
            for line in (s / "patched").read_text().splitlines():
                name, patch = line.split(" ", 1)
                patched[name] = json.loads(patch)
        return patched, out.stdout


def apply(items, patched):
    """The fixture after the merge patches, for an idempotence run."""
    out = []
    for item in json.loads(json.dumps(items)):
        p = patched.get(item["metadata"]["name"])
        if p:
            ann = item["metadata"].setdefault("annotations", {})
            for k, v in p["metadata"]["annotations"].items():
                if v is None:
                    ann.pop(k, None)
                else:
                    ann[k] = v
            for key, block in p["spec"].items():
                tgt = item["spec"].setdefault(key, {})
                if block["baseUrl"] is None:
                    tgt.pop("baseUrl", None)
                else:
                    tgt["baseUrl"] = block["baseUrl"]
        out.append(item)
    return out


def url_of(patch):
    (block,) = patch["spec"].values()
    if patch["metadata"]["annotations"][MARK] != block["baseUrl"]:
        fail(f"a patch whose mark and baseUrl differ: {patch}")
    if "resourceVersion" not in patch["metadata"]:
        fail(f"a patch without the resourceVersion precondition: {patch}")
    return block["baseUrl"]


def hook_script(docs):
    job = find(docs, "Job", HOOK)
    if not job:
        fail("no hook Job")
    hooks = job["metadata"]["annotations"]["helm.sh/hook"]
    if hooks != "post-install,post-upgrade":
        fail(f"hook Job runs at {hooks}, want post-install,post-upgrade")
    return job["spec"]["template"]["spec"]["containers"][0]["args"][0]


def main():
    chart = sys.argv[1]

    print("--> the policies mark the baseUrl they write")
    # The CI values serve Anthropic and OpenAI.
    on = render(chart, *MAP)
    for name, url in (("agent-platform-connectivity-modelconfig-llm-routing", ORIGIN),
                      ("agent-platform-connectivity-modelconfig-llm-routing-openai", ORIGIN + "/v1")):
        pol = find(on, "MutatingAdmissionPolicy", name)
        if not pol:
            fail(f"no policy {name}")
        expr = pol["spec"]["mutations"][0]["applyConfiguration"]["expression"]
        if f'"{MARK}": "{url}"' not in expr or f'baseUrl: "{url}"' not in expr:
            fail(f"{name} does not write and mark {url}: {expr}")
        chatgpt = [c for c in pol["spec"]["matchConditions"] if "chatgptOAuth" in c["expression"]]
        if (url == ORIGIN + "/v1") != bool(chatgpt):
            fail(f"{name}: a ChatGPT login condition {chatgpt}, want one on the OpenAI policy only")

    print("--> the hook renders on and off wherever the policies would, with its RBAC")
    off = render(chart, *MAP, "--set", "llmRouting.enabled=false")
    for docs, label in ((on, "on"), (off, "off")):
        hook_script(docs)
        role = find(docs, "ClusterRole", "t-hooks")
        rules = [r for r in (role or {}).get("rules", []) if "api.kagent.dev" in r["apiGroups"]]
        if rules != [{"apiGroups": ["api.kagent.dev"], "resources": ["modelconfigs"], "verbs": ["get", "list", "patch"]}]:
            fail(f"routing {label}: the hook identity's ModelConfig rule is {rules}")
    for args, why in ((["--set", "llmRouting.enabled=false"], "without the GA MutatingAdmissionPolicy API"),
                      ([*MAP, "--set", "llmRouting.modelConfigPolicy.enabled=false"], "with llmRouting.modelConfigPolicy off")):
        docs = render(chart, *args)
        if find(docs, "Job", HOOK):
            fail(f"the hook renders {why}")
        role = find(docs, "ClusterRole", "t-hooks")
        if role and any("api.kagent.dev" in r["apiGroups"] for r in role["rules"]):
            fail(f"the hook identity keeps the ModelConfig rule {why}")

    print("--> off: the chart's baseUrls come off, the operator's stay")
    patched, log = run_hook(hook_script(off), FIXTURE)
    if set(patched) != {"marked", "legacy-openai", "chatgpt-marked"}:
        fail(f"off patched {sorted(patched)}, want marked, legacy-openai and chatgpt-marked")
    for name, p in patched.items():
        (block,) = p["spec"].values()
        if block["baseUrl"] is not None or p["metadata"]["annotations"][MARK] is not None:
            fail(f"off: {name} keeps its baseUrl or mark: {p}")
    if "3 changed" not in log:
        fail(f"off: the log does not count the changes: {log}")
    again, _ = run_hook(hook_script(off), apply(FIXTURE, patched))
    if again:
        fail(f"off: a second run patches {sorted(again)}")

    print("--> on: the chart's listener URLs are marked, a ChatGPT login's comes off, nothing else is routed")
    patched, _ = run_hook(hook_script(on), FIXTURE)
    chatgpt = patched.pop("chatgpt-marked", None)
    if not chatgpt or chatgpt["spec"]["openAI"]["baseUrl"] is not None or chatgpt["metadata"]["annotations"][MARK] is not None:
        fail(f"on: a ChatGPT login keeps the listener URL the chart wrote: {chatgpt}")
    want = {"legacy-openai": ORIGIN + "/v1"}
    got = {n: url_of(p) for n, p in patched.items()}
    if got != want:
        fail(f"on patched {got}, want {want}")
    again, _ = run_hook(hook_script(on), apply(FIXTURE, {**patched, "chatgpt-marked": chatgpt}))
    if again:
        fail(f"on: a second run patches {sorted(again)}")

    print("--> on: a provider the listener no longer serves leaves it")
    anthropic_only = render(chart, *MAP, "--set-json", 'llmRouting.models=[{"name":"anthropic","provider":"Anthropic","baseURL":"https://api.anthropic.com/v1","match":"claude-*"}]')
    patched, _ = run_hook(hook_script(anthropic_only), FIXTURE)
    if patched.get("legacy-openai", {}).get("spec", {}).get("openAI", {}).get("baseUrl", "x") is not None:
        fail(f"on, OpenAI not served: legacy-openai keeps the listener URL: {patched.get('legacy-openai')}")
    if "bare-openai" in patched:
        fail("on, OpenAI not served: bare-openai was routed")

    print("--> without the ModelConfig API the hook has nothing to do")
    patched, log = run_hook(hook_script(off), FIXTURE, api=False)
    if patched or "nothing to reconcile" not in log:
        fail(f"without the API: patched {sorted(patched)}, log {log}")


if __name__ == "__main__":
    main()
