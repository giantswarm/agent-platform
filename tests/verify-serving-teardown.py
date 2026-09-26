#!/usr/bin/env python3
"""Assert the serving slice's ordered teardown (giantswarm/agent-platform#527).

The llm-d controller's webhook denies every delete of a well-known
LLMInferenceServiceConfig while it runs, and nothing clears the configs'
finalizer once it is gone, so the meta chart removes the two serving children
in order: a hook Job <release>-serving-teardown.

Renders (offline, `helm template`):
- the serving slice (examples/serving-slice.yaml, the engine off): the Job at
  pre-delete, weight -2, in the helm image, as <release>-hooks, and that
  identity (ServiceAccount, ClusterRoleBinding) and its apiserver egress policy
  rendered at pre-delete for it;
- the engine on with the three kserve components: the Job at pre-delete before
  the engine's teardown waves (weight -2 < 0);
- no Job — and, the engine off, no hook identity — with kserve-runtime-configs
  off (the pre-upgrade case needs a live HelmRelease: a lookup, empty offline),
  with kserve-llmisvc-resources off, or with gitops.target.kubeConfig.

The script, run against a stub kubectl: the controller's release deleted and
waited for first, then its Deployment and the webhook configuration waited
for, then the configs of kserve-runtime-configs (never another release's)
listed, deleted and freed of serving.kserve.io/llmisvcconfig-finalizer through
the CRD's storage version (other finalizers kept), then the configs' release;
a re-run with everything gone completes without touching a config.

The live half (the slice switched off in place and uninstalled, no release
UninstallFailed, no config terminating) runs in agentlab. HELM selects the
binary.
"""
import json
import os
import re
import subprocess
import sys
import tempfile

import yaml

HELM = os.environ.get("HELM", "helm")
FLEET_APIS = ["--api-versions", "kyverno.io/v1", "--api-versions", "cilium.io/v2", "--api-versions", "monitoring.coreos.com/v1",
              "--api-versions", "gateway.networking.k8s.io/v1", "--api-versions", "gateway.envoyproxy.io/v1alpha1"]
BASE = ["--namespace", "agent-platform", "--set", "kagent.harness.snapshotLocation=s3://ci-agent-snapshots/agents",
        "--set", "global.domain=wc01.example.com", "--set", "global.identity.issuerUrl=https://dex.mc.example.com",
        "--set", "gatewayApi.gateway.tls.secretName=wildcard-tls", *FLEET_APIS]
KSERVE_ON = [a for c in ("kserve-llmisvc-crd", "kserve-llmisvc-resources", "kserve-runtime-configs")
             for a in ("--set", f"components.{c}.enabled=true")]
JOB = "t-serving-teardown"
FINALIZER = "serving.kserve.io/llmisvcconfig-finalizer"


def render(chart: str, flags: list[str]) -> list[dict]:
    result = subprocess.run([HELM, "template", "t", chart, *BASE, *flags], capture_output=True, text=True, check=False)
    if result.returncode != 0:
        sys.exit(f"FAIL: render {' '.join(flags)} failed\n{result.stderr}")
    return [d for d in yaml.safe_load_all(result.stdout) if d]


def find(docs: list[dict], kind: str, name: str) -> dict | None:
    return next((d for d in docs if d.get("kind") == kind and d["metadata"]["name"] == name), None)


def hook(doc: dict) -> tuple[set[str], str]:
    ann = doc["metadata"].get("annotations", {})
    return set(ann.get("helm.sh/hook", "").split(",")), ann.get("helm.sh/hook-weight", "")


def ok(msg: str) -> None:
    print(f"ok: {msg}")


def check_slice(chart: str) -> dict:
    docs = render(chart, ["-f", f"{chart}/examples/serving-slice.yaml"])
    job = find(docs, "Job", JOB)
    if not job:
        sys.exit(f"FAIL: the serving slice renders no {JOB} hook Job")
    if hook(job) != ({"pre-delete"}, "-2"):
        sys.exit(f"FAIL: {JOB} runs at {hook(job)}, expected pre-delete at weight -2")
    pod = job["spec"]["template"]["spec"]
    if pod["serviceAccountName"] != "t-hooks" or "/alpine-k8s:" not in pod["containers"][0]["image"]:
        sys.exit(f"FAIL: {JOB} must run the helm image (a script: sh, kubectl, jq) as t-hooks: {pod['serviceAccountName']} {pod['containers'][0]['image']}")
    for kind in ("ServiceAccount", "ClusterRoleBinding"):
        doc = find(docs, kind, "t-hooks")
        if not doc or "pre-delete" not in hook(doc)[0]:
            sys.exit(f"FAIL: the engine off, the hook identity's {kind} must render at pre-delete for {JOB}: {doc and hook(doc)}")
    policy = find(docs, "CiliumNetworkPolicy", "t-hooks") or find(docs, "NetworkPolicy", "t-hooks")
    if not policy or "pre-delete" not in hook(policy)[0]:
        sys.exit(f"FAIL: the hook identity's apiserver egress policy must render at pre-delete for {JOB} (a default-deny cluster admits nothing else)")
    ok(f"the serving slice renders {JOB} at pre-delete, weight -2, in the helm image as t-hooks, with the identity and its egress policy")
    return job


def check_engine(chart: str) -> None:
    docs = render(chart, ["--set", "ingress.parentRefs[0].name=x", *KSERVE_ON])
    job, waves = find(docs, "Job", JOB), find(docs, "Job", "t-teardown-releases")
    if not job or not waves:
        sys.exit("FAIL: the engine on with the kserve components renders the serving teardown and the engine's teardown waves")
    if hook(job)[0] != {"pre-delete"} or int(hook(job)[1]) >= int(hook(waves)[1]):
        sys.exit(f"FAIL: {JOB} {hook(job)} must run at pre-delete before t-teardown-releases {hook(waves)}")
    ok(f"the engine on: {JOB} runs before the engine's teardown waves")


def check_absent(chart: str) -> None:
    profile = ["-f", f"{chart}/examples/serving-slice.yaml"]
    cases = {
        "kserve-runtime-configs off (no live HelmRelease offline)": [*profile, "--set", "components.kserve-runtime-configs.enabled=false",
                                                                      "--set", "components.kserve-llmisvc-resources.enabled=false"],
        "kserve-llmisvc-resources off": [*profile, "--set", "components.kserve-llmisvc-resources.enabled=false"],
        "gitops.target.kubeConfig": [*profile, "-f", f"{chart}/ci/test-target-values.yaml"],
    }
    for what, flags in cases.items():
        docs = render(chart, flags)
        if find(docs, "Job", JOB):
            sys.exit(f"FAIL: {what} renders {JOB}")
        if what != "gitops.target.kubeConfig" and find(docs, "ServiceAccount", "t-hooks"):
            sys.exit(f"FAIL: {what}, the engine off: the hook identity renders with no hook to run")
        ok(f"{what}: no {JOB}")


# A stub kubectl for the script: every call is logged; the controller's Deployment is listed once (the wait loops),
# the CRD stores v1alpha2, the namespace holds two configs of kserve-runtime-configs and one of another release, each
# with the llm-d finalizer and one of someone else. STUB_GONE: everything is gone already (a re-run). STUB_NO_FLUX:
# Flux's HelmRelease API is gone too (a retried uninstall after the bundled engine's teardown).
STUB_KUBECTL = r"""#!/bin/sh
echo "$*" >> "$STUB_LOG"
cfg() { printf '{"metadata":{"name":"%s","namespace":"agent-platform","annotations":{"meta.helm.sh/release-name":"%s"},"finalizers":["%s","example.com/keep"]}}' "$1" "$2" "$FINALIZER"; }
case "$*" in
  "delete helmreleases.helm.toolkit.fluxcd.io "*) ;;
  "get deployments "*)
    if [ -z "$STUB_GONE" ] && [ ! -e "$STUB_LOG.deployment" ]; then touch "$STUB_LOG.deployment"; echo deployment.apps/llmisvc-controller-manager; fi ;;
  "get validatingwebhookconfigurations "*) ;;
  "get customresourcedefinitions helmreleases.helm.toolkit.fluxcd.io "*) [ -n "$STUB_NO_FLUX" ] || echo customresourcedefinition.apiextensions.k8s.io/helmreleases.helm.toolkit.fluxcd.io ;;
  "get customresourcedefinitions "*) [ -n "$STUB_GONE" ] || printf v1alpha2 ;;
  "get llminferenceserviceconfigs.v1alpha2.serving.kserve.io --namespace agent-platform -o json")
    printf '{"items":[%s,%s,%s]}' "$(cfg kserve-config-llm-template kserve-runtime-configs)" "$(cfg kserve-config-llm-tracing kserve-runtime-configs)" "$(cfg someone-elses other)" ;;
  "get llminferenceserviceconfigs.v1alpha2.serving.kserve.io "*"-o json") cfg "$2" kserve-runtime-configs ;;
  "get llminferenceserviceconfigs.v1alpha2.serving.kserve.io "*"-o name") ;;
  "delete llminferenceserviceconfigs.v1alpha2.serving.kserve.io "*|"patch llminferenceserviceconfigs.v1alpha2.serving.kserve.io "*) ;;
  *) echo "stub kubectl: unexpected $*" >&2; exit 9 ;;
esac
"""


def run_script(job: dict, gone: bool, no_flux: bool = False) -> list[str]:
    script = job["spec"]["template"]["spec"]["containers"][0]["args"][0]
    with tempfile.TemporaryDirectory() as d:
        for name, body in (("kubectl", STUB_KUBECTL), ("sleep", "#!/bin/sh\n")):
            path = os.path.join(d, name)
            with open(path, "w", encoding="utf-8") as f:
                f.write(body)
            os.chmod(path, 0o755)
        log = os.path.join(d, "calls")
        env = dict(os.environ, PATH=f"{d}:{os.environ['PATH']}", STUB_LOG=log, FINALIZER=FINALIZER, STUB_GONE="1" if gone else "", STUB_NO_FLUX="1" if no_flux else "")
        result = subprocess.run(["sh", "-eu", "-c", script], env=env, capture_output=True, text=True, check=False)
        if result.returncode != 0:
            sys.exit(f"FAIL: the script exited {result.returncode}\n{result.stdout}{result.stderr}")
        with open(log, encoding="utf-8") as f:
            return f.read().splitlines()


def index(calls: list[str], pattern: str, after: int = -1) -> int:
    for i, call in enumerate(calls):
        if i > after and re.search(pattern, call):
            return i
    sys.exit(f"FAIL: the script makes no call matching {pattern!r} after call {after}:\n" + "\n".join(calls))


def check_script(job: dict) -> None:
    calls = run_script(job, gone=False)
    controller = index(calls, r"^delete helmreleases\S* --namespace agent-platform kserve-llmisvc-resources .*--wait ")
    deployment = index(calls, r"^get deployments --namespace agent-platform --selector control-plane=llmisvc-controller-manager", controller)
    index(calls, r"^get deployments ", deployment)  # listed again: the wait loops until it is gone
    webhook = index(calls, r"^get validatingwebhookconfigurations llminferenceserviceconfig.serving.kserve.io", deployment)
    storage = index(calls, r"^get customresourcedefinitions llminferenceserviceconfigs.serving.kserve.io ", webhook)
    releases = index(calls, r"^delete helmreleases\S* --namespace agent-platform kserve-runtime-configs .*--wait ", storage)
    for name in ("kserve-config-llm-template", "kserve-config-llm-tracing"):
        delete = index(calls, rf"^delete llminferenceserviceconfigs\.v1alpha2\.serving\.kserve\.io {name} --namespace agent-platform ", storage)
        patch = index(calls, rf"^patch llminferenceserviceconfigs\.v1alpha2\.serving\.kserve\.io {name} .*--type=merge", delete)
        if not patch < releases:
            sys.exit(f"FAIL: {name} must be gone before the configs' release is deleted:\n" + "\n".join(calls))
        body = json.loads(calls[patch].split("--patch ", 1)[1])
        if body != {"metadata": {"finalizers": ["example.com/keep"]}}:
            sys.exit(f"FAIL: the patch of {name} must drop {FINALIZER} and keep every other finalizer: {body}")
    if any("someone-elses" in c for c in calls):
        sys.exit("FAIL: the script touched a config of another release:\n" + "\n".join(calls))
    touched = [c for c in calls if re.match(r"^(delete|patch|get) llminferenceserviceconfigs", c)]
    if any(not c.split()[1].startswith("llminferenceserviceconfigs.v1alpha2.") for c in touched):
        sys.exit("FAIL: every config request goes through the CRD's storage version (no conversion webhook once the controller is gone)")
    ok("the script: the controller's release, its Deployment and webhook gone, the configs of kserve-runtime-configs deleted "
       f"and freed of {FINALIZER} through the storage version (other finalizers and other releases' configs kept), then the configs' release")
    calls = run_script(job, gone=True)
    if any(re.match(r"^(delete|patch) llminferenceserviceconfigs", c) for c in calls) or not re.match(r"^delete helmreleases\S* --namespace agent-platform kserve-runtime-configs ", calls[-1]):
        sys.exit("FAIL: a re-run with everything gone must touch no config and end with the configs' release:\n" + "\n".join(calls))
    ok("the script re-run with everything gone completes without touching a config")
    calls = run_script(job, gone=True, no_flux=True)
    if any(re.match(r"^(delete|patch) ", c) for c in calls):
        sys.exit("FAIL: a retry after the bundled engine's teardown (no HelmRelease API) must delete nothing:\n" + "\n".join(calls))
    ok("the script retried with Flux's HelmRelease API gone completes without a delete")


def main(chart: str) -> int:
    job = check_slice(chart)
    check_engine(chart)
    check_absent(chart)
    check_script(job)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "helm/agent-platform"))
