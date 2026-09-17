#!/usr/bin/env python3
"""Assert the connectivity chart's model-serving policies over both pod shapes
KServe creates (giantswarm/agent-platform#506, #518).

A served model runs as one of two pods, and their labels share nothing: the
classic InferenceService predictor (serving.kserve.io/inferenceservice=<name>,
runtime container kserve-container) and the LLMInferenceService workload pod
the llm-d controller creates (kserve.io/component=workload,
app.kubernetes.io/part-of=llminferenceservice, app.kubernetes.io/name=<name>,
runtime container main). The chart renders every selector of the serving
namespace from one list of those shapes (_helpers.tpl,
agent-platform.modelServing.podShapes); this check holds what that buys:

  * `kyverno apply` of the rendered ClusterPolicies over a fixture pod of each
    shape (tests/fixtures/model-serving-*-pod.yaml — the llm-d one as observed
    on a GPU cluster, with the storage-initializer KServe injects for an hf://
    model URI): the hf-cache claim is mounted at /mnt/models with the model's
    name as subPath on the storage-initializer and on the shape's runtime
    container (the classic one stays read-only), the pod carries the claim's
    fsGroup (also when it declared another: one claim, one group), no
    container is added or lost, the original volumes stay, and the
    initializer's memory limit is raised. The policy over its own output is a
    no-op: the API server reinvokes Kyverno's webhook whenever another
    mutating webhook changed the pod after Kyverno ran, and a rule that is not
    idempotent then adds its patch twice (#514). A pod of the shape without a
    storage-initializer, and a model-manager download-Job pod, are untouched;
    a workload pod without a model name gets the limit but no cache and no
    fsGroup (the mount would otherwise land on the claim's root).
  * The mutated pod of each shape passes the fleet's restricted Pod Security
    Standard with the chart's PolicyException and nothing else
    (tests/fixtures/restricted-pss-clusterpolicies.yaml, the five
    ClusterPolicies as installed on a Giant Swarm cluster): every rule passes
    or is skipped by the exception; the bare pod fails exactly the four rules
    the exception names (the exception carries the vLLM image and the
    storage-initializer, not the chart's additions); a pod with the root
    hf-cache-init of chart 4.28.14 fails exactly the two rules that denied
    every LLMInferenceService workload pod on a Giant Swarm cluster (#518).
  * The Deployments' progress-deadline rule applies to both shapes' Deployments.
  * The network policies (both flavours), the kagent agents' egress and the
    PolicyException select each fixture by exactly its own shape's policy and
    never the download Job's pod.

Needs PyYAML and the kyverno CLI (the CI job installs both).
"""

import copy
import glob
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile

import yaml

HELM = os.environ.get("HELM", "helm")
KYVERNO = os.environ.get("KYVERNO", "kyverno")
HERE = pathlib.Path(__file__).resolve().parent
NS = "model-serving"
CLAIM = "hf-cache"
FSGROUP = 1000
MEMORY = "4Gi"
DEADLINE = 3600
PSS = HERE / "fixtures" / "restricted-pss-clusterpolicies.yaml"
# The rules the chart's PolicyException names, as (policy, rule).
EXCEPTED = {("disallow-capabilities-strict", "require-drop-all"), ("disallow-privilege-escalation", "privilege-escalation"),
            ("require-run-as-nonroot", "run-as-non-root"), ("restrict-seccomp-strict", "check-seccomp-strict")}
# The two rules that denied the root init container of chart 4.28.14 (#518).
ROOT_DENIED = {("disallow-capabilities-strict", "adding-capabilities-strict"), ("require-run-as-non-root-user", "run-as-non-root-user")}
# That init container, as the chart injected it — the negative control.
ROOT_INIT = {"name": "hf-cache-init", "image": "gsoci.azurecr.io/giantswarm/alpine:3.24.1",
             "command": ["sh", "-ec", 'mkdir -p "/cache/$MODEL_DIR" && chmod 0777 "/cache/$MODEL_DIR"'],
             "securityContext": {"runAsUser": 0, "runAsNonRoot": False, "allowPrivilegeEscalation": False, "readOnlyRootFilesystem": True,
                                 "capabilities": {"drop": ["ALL"], "add": ["CHOWN", "DAC_OVERRIDE", "FOWNER"]}},
             "volumeMounts": [{"name": CLAIM, "mountPath": "/cache"}]}
# shape -> (fixture, runtime container, the label carrying the model's name)
SHAPES = {
    "predictor": ("model-serving-classic-predictor-pod.yaml", "kserve-container", "serving.kserve.io/inferenceservice"),
    "llmisvc-workload": ("model-serving-llmisvc-workload-pod.yaml", "main", "app.kubernetes.io/name"),
}
DOWNLOAD_LABELS = {"app.kubernetes.io/managed-by": "model-manager", "model-manager.giantswarm.io/component": "download", "job-name": "pull-qwen3-4b"}
BASE = [
    "--namespace", "agent-platform",
    "--set", "ingress.parentRefs[0].name=x",
    "--set", "kagent.harness.snapshotLocation=s3://ci-agent-snapshots/agents",
    "--set", "components.modelServing.enabled=true",
    "--set", "components.kserve-crd.enabled=true",
    "--set", "components.kserve-resources.enabled=true",
    "--set", "components.kagent.enabled=true",
    "--set", f"modelServing.namespace.name={NS}",
    "--api-versions", "kyverno.io/v1",
    "--api-versions", "gateway.networking.k8s.io/v1",
]
CILIUM = ["--api-versions", "cilium.io/v2"]


def fail(msg: str) -> None:
    sys.exit(f"FAIL: {msg}")


def ok(msg: str) -> None:
    print(f"ok: {msg}")


def render(chart: str, flags: list[str]) -> list[dict]:
    result = subprocess.run([HELM, "template", "t", chart, *BASE, *flags], capture_output=True, text=True, check=False)
    if result.returncode != 0:
        fail(f"render of {chart} {' '.join(flags)} failed:\n{result.stderr}")
    return [doc for doc in yaml.safe_load_all(result.stdout) if doc]


def one(docs: list[dict], kind: str, suffix: str) -> dict:
    """The one document of the kind whose name ends in the suffix (the release name varies with the render)."""
    hits = [d for d in docs if d.get("kind") == kind and d["metadata"]["name"].endswith(suffix)]
    if len(hits) != 1:
        fail(f"{kind}/*{suffix}: {len(hits)} documents in the render, expected one")
    return hits[0]


def suffixes(docs: list[dict], prefix: str) -> list[str]:
    """The documents' names past the release prefix, sorted."""
    return sorted(d["metadata"]["name"].split(prefix, 1)[1] for d in docs)


def selects(selector: dict, labels: dict) -> bool:
    """A LabelSelector (matchLabels, matchExpressions) against a pod's labels."""
    if any(labels.get(k) != v for k, v in (selector.get("matchLabels") or {}).items()):
        return False
    for expr in selector.get("matchExpressions") or []:
        key, op, values = expr["key"], expr["operator"], expr.get("values") or []
        if (op == "Exists" and key not in labels) or (op == "DoesNotExist" and key in labels):
            return False
        if (op == "In" and labels.get(key) not in values) or (op == "NotIn" and labels.get(key) in values):
            return False
    return True


def apply(policies: list[dict], resource: dict) -> dict | None:
    """`kyverno apply` of the policies to the resource: the mutated resource, None when nothing applied."""
    with tempfile.TemporaryDirectory(prefix="ap-model-serving-policies-") as tmp:
        pathlib.Path(tmp, "policies.yaml").write_text(yaml.safe_dump_all(policies), encoding="utf-8")
        pathlib.Path(tmp, "resource.yaml").write_text(yaml.safe_dump(resource), encoding="utf-8")
        result = subprocess.run([KYVERNO, "apply", f"{tmp}/policies.yaml", "--resource", f"{tmp}/resource.yaml", "-o", f"{tmp}/out"],
                                capture_output=True, text=True, check=False)
        if result.returncode != 0 or "fail: 0" not in result.stdout or "error: 0" not in result.stdout:
            fail(f"kyverno apply on {resource['kind']}/{resource['metadata']['name']}:\n{result.stdout}\n{result.stderr}")
        docs = [d for f in glob.glob(f"{tmp}/out/**/*.yaml", recursive=True) for d in yaml.safe_load_all(open(f, encoding="utf-8")) if d]
    if len(docs) > 1:
        fail(f"kyverno apply wrote {len(docs)} documents for one resource")
    return docs[0] if docs else None


def validate(resource: dict, exception: dict | None) -> dict[tuple[str, str], str]:
    """`kyverno apply` of the fleet's restricted-PSS policies to the resource, with the exception when given: (policy, rule) -> pass/fail/skip."""
    with tempfile.TemporaryDirectory(prefix="ap-model-serving-pss-") as tmp:
        pathlib.Path(tmp, "resource.yaml").write_text(yaml.safe_dump(resource), encoding="utf-8")
        cmd = [KYVERNO, "apply", str(PSS), "--resource", f"{tmp}/resource.yaml", "--policy-report"]
        if exception is not None:
            pathlib.Path(tmp, "exception.yaml").write_text(yaml.safe_dump(exception), encoding="utf-8")
            cmd += ["--exception", f"{tmp}/exception.yaml"]
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    # The CLI exits 1 whenever a rule fails; the report says which. No report, or an evaluation error, is the failure here.
    report = yaml.safe_load(result.stdout[result.stdout.index("apiVersion:"):]) if "apiVersion:" in result.stdout else None
    if not report or not report.get("results") or (report.get("summary") or {}).get("error"):
        fail(f"kyverno apply of the PSS policies on {resource['kind']}/{resource['metadata']['name']} produced no report:\n{result.stdout}\n{result.stderr}")
    results = {(r["policy"], r["rule"]): r["result"] for r in report["results"]}
    expected = {(p["metadata"]["name"], r["name"]) for p in yaml.safe_load_all(PSS.read_text(encoding="utf-8")) if p for r in p["spec"]["rules"]}
    if set(results) != expected:
        fail(f"the PSS report covers {sorted(results)}, expected every rule of {PSS.name}: {sorted(expected)}")
    return results


def outcome(results: dict[tuple[str, str], str], value: str) -> set[tuple[str, str]]:
    """The (policy, rule) pairs the report gave the outcome."""
    return {k for k, v in results.items() if v == value}


def fixture(shape: str) -> dict:
    return yaml.safe_load((HERE / "fixtures" / SHAPES[shape][0]).read_text(encoding="utf-8"))


def mounts(container: dict) -> dict[str, dict]:
    return {m["mountPath"]: m for m in container.get("volumeMounts") or []}


def fs_group(spec: dict) -> int | None:
    return (spec.get("securityContext") or {}).get("fsGroup")


def check_mutations(pods_policy: dict, shape: str) -> None:
    _, runtime, name_label = SHAPES[shape]
    pod = fixture(shape)
    model = pod["metadata"]["labels"][name_label]
    out = apply([pods_policy], pod)
    if out is None:
        fail(f"{shape}: no rule of the pods policy applied")
    spec = out["spec"]
    if [c["name"] for c in spec["initContainers"]] != [c["name"] for c in pod["spec"]["initContainers"]]:
        fail(f"{shape}: initContainers are {[c['name'] for c in spec['initContainers']]}; the chart adds no container to a model pod (#518)")
    if fs_group(spec) != FSGROUP:
        fail(f"{shape}: the pod does not carry the claim's fsGroup {FSGROUP}: {spec.get('securityContext')}")
    storage = spec["initContainers"][0]
    if storage["resources"]["limits"]["memory"] != MEMORY:
        fail(f"{shape}: the storage-initializer's memory limit is {storage['resources']['limits'].get('memory')}, expected {MEMORY}")
    sm = mounts(storage).get("/mnt/models", {})
    if sm.get("name") != CLAIM or sm.get("subPath") != model:
        fail(f"{shape}: the storage-initializer's /mnt/models is not {CLAIM}/{model}: {sm}")
    if [c["name"] for c in spec["containers"]] != [c["name"] for c in pod["spec"]["containers"]]:
        fail(f"{shape}: the mutation changed the container list: {[c['name'] for c in spec['containers']]}")
    rm = mounts(next(c for c in spec["containers"] if c["name"] == runtime)).get("/mnt/models", {})
    if rm.get("name") != CLAIM or rm.get("subPath") != model:
        fail(f"{shape}: {runtime}'s /mnt/models is not {CLAIM}/{model}: {rm}")
    original = mounts(next(c for c in pod["spec"]["containers"] if c["name"] == runtime))["/mnt/models"]
    if rm.get("readOnly") != original.get("readOnly"):
        fail(f"{shape}: {runtime}'s /mnt/models readOnly changed from {original.get('readOnly')} to {rm.get('readOnly')}")
    volumes = {v["name"]: v for v in spec["volumes"]}
    if volumes.get(CLAIM) != {"name": CLAIM, "persistentVolumeClaim": {"claimName": CLAIM}}:
        fail(f"{shape}: no {CLAIM} claim volume: {volumes.get(CLAIM)}")
    if missing := [v["name"] for v in pod["spec"]["volumes"] if v["name"] not in volumes]:
        fail(f"{shape}: the mutation dropped volumes {missing}")
    ok(f"{shape}: {CLAIM}/{model} mounted at /mnt/models on storage-initializer and {runtime}, fsGroup {FSGROUP}, the limit {MEMORY}, no container added, containers and volumes kept")

    again = apply([pods_policy], out)
    if again is not None and again["spec"] != spec:
        fail(f"{shape}: the policy over its own output changed the pod again, as a reinvoked webhook would: "
             f"initContainers {[c['name'] for c in again['spec']['initContainers']]}, "
             f"{sum(v['name'] == CLAIM for v in again['spec']['volumes'])} {CLAIM} volume(s)")
    ok(f"{shape}: the policy over its own output is a no-op (one {CLAIM} volume, one mount per container under webhook reinvocation)")

    declared = copy.deepcopy(pod)
    declared["spec"]["securityContext"] = {"fsGroup": FSGROUP + 1, "runAsNonRoot": True}
    out = apply([pods_policy], declared)
    if out is None or fs_group(out["spec"]) != FSGROUP or out["spec"]["securityContext"].get("runAsNonRoot") is not True:
        fail(f"{shape}: a pod declaring fsGroup {FSGROUP + 1} did not get the claim's {FSGROUP} with its other fields kept: {out and out['spec'].get('securityContext')}")
    ok(f"{shape}: a pod declaring another fsGroup gets the claim's, its other securityContext fields kept")

    plain = copy.deepcopy(pod)
    del plain["spec"]["initContainers"]
    out = apply([pods_policy], plain)
    if out is not None and out["spec"] != plain["spec"]:
        fail(f"{shape}: a pod without a storage-initializer was mutated")
    job = copy.deepcopy(pod)
    job["metadata"]["labels"] = dict(DOWNLOAD_LABELS)
    out = apply([pods_policy], job)
    if out is not None and out["spec"] != job["spec"]:
        fail(f"{shape}: a download-Job pod with a storage-initializer was mutated")
    ok(f"{shape}: a pod without a storage-initializer and a download-Job pod are untouched")

    if name_label not in {e["key"] for e in selector_of(shape, pods_policy)}:
        nameless = copy.deepcopy(pod)
        del nameless["metadata"]["labels"][name_label]
        out = apply([pods_policy], nameless)
        if out is None or [c["name"] for c in out["spec"]["initContainers"]] != ["storage-initializer"] or CLAIM in {v["name"] for v in out["spec"]["volumes"]}:
            fail(f"{shape}: a pod without {name_label} got the cache (it would mount the claim's root)")
        if fs_group(out["spec"]) is not None:
            fail(f"{shape}: a pod without {name_label} got the claim's fsGroup without the claim")
        if out["spec"]["initContainers"][0]["resources"]["limits"]["memory"] != MEMORY:
            fail(f"{shape}: a pod without {name_label} kept the initializer's default limit")
        ok(f"{shape}: a pod without {name_label} gets the limit and no cache mount, no fsGroup")


def selector_of(shape: str, policy: dict) -> list[dict]:
    """The matchExpressions of the pods policy's redirect rule for the shape."""
    rule = next(r for r in policy["spec"]["rules"] if r["name"] == f"redirect-model-storage-{shape}")
    return rule["match"]["any"][0]["resources"]["selector"]["matchExpressions"]


def check_pod_security(pods_policy: dict, exception: dict, shape: str) -> None:
    """The mutated pod against the fleet's restricted PSS: admitted with the chart's exception, and for the right reasons."""
    pod = apply([pods_policy], fixture(shape))
    results = validate(pod, exception)
    if failed := outcome(results, "fail"):
        fail(f"{shape}: the mutated pod fails the restricted PSS with the chart's exception: {sorted(failed)}")
    if (skipped := outcome(results, "skip")) != EXCEPTED:
        fail(f"{shape}: the exception skips {sorted(skipped)}, expected exactly the four rules it names: {sorted(EXCEPTED)}")
    if (passed := outcome(results, "pass")) != set(results) - EXCEPTED:
        fail(f"{shape}: every rule the exception does not name must pass; passed {sorted(passed)}")
    if (bare := outcome(validate(pod, None), "fail")) != EXCEPTED:
        fail(f"{shape}: without the exception the mutated pod fails {sorted(bare)}, expected exactly the four excepted rules")
    ok(f"{shape}: the mutated pod passes the fleet's restricted PSS with the chart's exception — the four excepted rules skip, "
       f"{', '.join(sorted(r for _, r in ROOT_DENIED))} pass; without the exception exactly the four fail")

    rooted = copy.deepcopy(pod)
    rooted["spec"]["initContainers"].insert(0, copy.deepcopy(ROOT_INIT))
    if (denied := outcome(validate(rooted, exception), "fail")) != ROOT_DENIED:
        fail(f"{shape}: the pod with the 4.28.14 root hf-cache-init fails {sorted(denied)}, expected exactly {sorted(ROOT_DENIED)}")
    ok(f"{shape}: the pod with the former root hf-cache-init fails exactly the two rules that denied it (#518)")


def check_deployments(deployments_policy: dict) -> None:
    for shape in SHAPES:
        pod = fixture(shape)
        labels = pod["metadata"]["labels"]
        deployment = {"apiVersion": "apps/v1", "kind": "Deployment",
                      "metadata": {"name": f"{labels[SHAPES[shape][2]]}-kserve", "namespace": NS, "labels": dict(labels)},
                      "spec": {"selector": {"matchLabels": dict(labels)}, "template": {"metadata": {"labels": dict(labels)}, "spec": pod["spec"]}}}
        out = apply([deployments_policy], deployment)
        if out is None or out["spec"].get("progressDeadlineSeconds") != DEADLINE:
            fail(f"{shape}: the Deployment did not get progressDeadlineSeconds {DEADLINE}")
    ok(f"both shapes' Deployments get progressDeadlineSeconds {DEADLINE}")


def check_selectors(connectivity: str, k8s: list[dict]) -> None:
    cilium = render(connectivity, CILIUM)
    exception = one(k8s, "PolicyException", "model-serving-predictors")
    exception_selectors = [m["resources"]["selector"] for m in exception["spec"]["match"]["any"]
                           if "selector" in m["resources"] and "Pod" in m["resources"]["kinds"] and NS in m["resources"]["namespaces"]]
    agents = one(cilium, "CiliumNetworkPolicy", "-kagent-agents-to-model-serving")
    peers = [p for rule in agents["spec"]["egress"] for p in rule.get("toEndpoints") or []]
    serving_k8s = [d for d in k8s if d["kind"] == "NetworkPolicy" and "-model-serving-" in d["metadata"]["name"]]
    serving_cilium = [d for d in cilium if d["kind"] == "CiliumNetworkPolicy" and "-model-serving-" in d["metadata"]["name"]]
    for shape in SHAPES:
        labels = fixture(shape)["metadata"]["labels"]
        hit_k8s = suffixes([d for d in serving_k8s if selects(d["spec"]["podSelector"], labels)], "-model-serving-")
        if hit_k8s != [f"{shape}-egress", f"{shape}-ingress"]:
            fail(f"{shape}: the kubernetes-flavour policies selecting it are {hit_k8s}")
        hit_cilium = suffixes([d for d in serving_cilium if selects(d["spec"]["endpointSelector"], labels)], "-model-serving-")
        if hit_cilium != [shape]:
            fail(f"{shape}: the cilium-flavour policies selecting it are {hit_cilium}")
        if not any(selects(p, {**labels, "io.kubernetes.pod.namespace": NS}) for p in peers):
            fail(f"{shape}: the kagent agents' egress selects no {shape} pod in {NS}")
        if not any(selects(s, labels) for s in exception_selectors):
            fail(f"{shape}: the PolicyException's label matches select no {shape} pod")
    for d in serving_k8s + serving_cilium:
        selector = d["spec"].get("podSelector") or d["spec"]["endpointSelector"]
        if "download" not in d["metadata"]["name"] and selects(selector, DOWNLOAD_LABELS):
            fail(f"{d['metadata']['name']} selects model-manager's download-Job pod")
    if any(selects(s, DOWNLOAD_LABELS) for s in exception_selectors) or any(selects(p, {**DOWNLOAD_LABELS, "io.kubernetes.pod.namespace": NS}) for p in peers):
        fail("the PolicyException or the agents' egress selects model-manager's download-Job pod")
    ok("each shape's pod is selected by exactly its own network policies (both flavours), the agents' egress and the PolicyException; the download Job's pod by none")


def main(connectivity: str) -> int:
    if shutil.which(KYVERNO) is None:
        fail(f"the kyverno CLI ({KYVERNO}) is not installed; the mutations are asserted with `kyverno apply`")
    docs = render(connectivity, [])
    pods_policy = one(docs, "ClusterPolicy", "-model-serving-pods")
    deployments_policy = one(docs, "ClusterPolicy", "-model-serving-deployments")
    exception = one(docs, "PolicyException", "model-serving-predictors")
    rules = [r["name"] for r in pods_policy["spec"]["rules"]]
    if rules != [f"redirect-model-storage-{s}" for s in SHAPES] + ["storage-initializer-memory"]:
        fail(f"the pods policy's rules are {rules}; expected the redirect rules and the limit, no rule adding a container (#518)")
    for shape in SHAPES:
        check_mutations(pods_policy, shape)
        check_pod_security(pods_policy, exception, shape)
    check_deployments(deployments_policy)
    check_selectors(connectivity, docs)
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: verify-model-serving-policies.py <connectivity chart dir>")
    sys.exit(main(sys.argv[1]))
