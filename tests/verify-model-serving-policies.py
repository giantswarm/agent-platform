#!/usr/bin/env python3
"""Assert the connectivity chart's model-serving policies over both pod shapes
KServe creates (giantswarm/agent-platform#506, #518, #520, #522, #525).

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
    container is added or lost, the original volumes stay, the initializer's
    memory limit is raised, and the storage-initializer and the runtime carry
    modelServing.policies.env (HF_HUB_DISABLE_XET=1: the Hugging Face client
    off the Xet path, whose CDN a toFQDNs allow-list cannot follow, #520) next
    to their own env — KServe's HF_HUB_ENABLE_HF_TRANSFER and HF_XET_* on the
    initializer, the runtime's on the classic predictor — and added where a
    container has none; an empty list renders no env rule. The policy over its own output is a
    no-op: the API server reinvokes Kyverno's webhook whenever another
    mutating webhook changed the pod after Kyverno ran, and a rule that is not
    idempotent then adds its patch twice (#514). A pod of the shape without a
    storage-initializer, and a model-manager download-Job pod, are untouched;
    a workload pod without a model name gets the limit and the env but no
    cache and no fsGroup (the mount would otherwise land on the claim's root).
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
  * Each shape's policies admit exactly the port its fixture pod is reached
    on — the container port KServe's Service targets: the classic predictor's
    kserve-container on 8080, the llm-d workload's routing sidecar on 8000
    (`<name>-kserve-workload-svc` and the HTTPRoute's backendRef name it;
    vLLM's main listens on 8001 behind the sidecar; #525) — the ingress from the callers and
    from the kubelet (cilium), the kubernetes-flavour ingress and the kagent
    agents' egress rule that selects the shape; the two fixtures listen on
    different ports, so one shared value could not pass. Both renders below
    (the connectivity defaults and the meta chart's forwarded values) are
    held to it, and a render whose llm-d value is the classic port (the
    4.28.18 shape, every request through the models Gateway a 503) fails
    naming the shape.
  * The model pods' and the download Job's cilium egress admits every name of
    the Hugging Face download path (HUB_HOSTS: the Hub and its API redirects,
    the LFS fronts one label under hf.co, the Xet fronts two, the download
    CDN three — us.aws.cdn.hf.co, where the Hub redirects every shard request
    of a Xet-backed repository, Xet client or not; #522) under Cilium's rule
    for a matchPattern — `*` is [-a-zA-Z0-9_]*, DNS label characters and
    never a dot, so a pattern admits exactly one label per `*` and there is no
    multi-label wildcard — keeps out a deeper name and a look-alike domain,
    and stays a toFQDNs allow-list (no toCIDR, no toEntities world). The
    kubernetes flavour, which selects no names, admits 443 to every public
    block. A new host shape of the download path fails here, not a download
    on an installation. The same names are matched a second time against the
    policies rendered from the values the META chart forwards to its
    connectivity release (templates/components.yaml, forwardAllValues, the
    render an installation gets): a forwarded copy of a default shadows the
    child's own, and 4.28.17 shipped the CDN pattern in the connectivity
    defaults while the meta chart's mirrored lists kept the four old entries,
    so every installation still rendered the drop (#522, round 2).

Needs PyYAML and the kyverno CLI (the CI job installs both).

usage: verify-model-serving-policies.py <connectivity chart dir> <meta chart dir>
"""

import copy
import glob
import os
import pathlib
import re
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
# modelServing.policies.env, as the chart ships it (#520).
ENV = {"HF_HUB_DISABLE_XET": "1"}
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
# shape -> (fixture, runtime container, the label carrying the model's name, the container the pod is reached on — the
# one KServe's Service targets: the runtime itself, or the llm-d routing sidecar in front of it)
SHAPES = {
    "predictor": ("model-serving-classic-predictor-pod.yaml", "kserve-container", "serving.kserve.io/inferenceservice", "kserve-container"),
    "llmisvc-workload": ("model-serving-llmisvc-workload-pod.yaml", "main", "app.kubernetes.io/name", "llm-d-routing-sidecar"),
}
DOWNLOAD_LABELS = {"app.kubernetes.io/managed-by": "model-manager", "model-manager.giantswarm.io/component": "download", "job-name": "pull-qwen3-4b"}
# The names the Hugging Face download path uses (#522): the Hub and its API redirects, the LFS fronts one label
# under hf.co, the Xet fronts two, and the download CDN three — the Hub redirects every shard request of a
# Xet-backed repository there, Xet client or not (us.aws.cdn.hf.co; the regional siblings share the shape). A new
# host shape is added here, and modelServing.networkPolicy.huggingFace.fqdns gains its depth.
CDN = "us.aws.cdn.hf.co"
HUB_HOSTS = ["huggingface.co", "cdn-lfs.huggingface.co", "cdn-lfs-us-1.hf.co", "cas-server.xethub.hf.co",
             "transfer.xethub.hf.co", "cas-bridge.xethub.hf.co", CDN, "eu.aws.cdn.hf.co"]
# What the allow-list keeps out: a depth no name of the download path has, the bare apex, look-alike domains.
NOT_HUB_HOSTS = ["a.b.c.d.hf.co", "hf.co", "huggingface.co.example.com", "hf.co.example.com", "example.com"]
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


def forwarded_values(meta: str, apis: list[str]) -> dict:
    """The values an installation's connectivity release receives: the meta chart's defaults with the model serving switch
    on (BASE) and the one input every render needs (global.domain — the CI values would trip the wiring's ingress-mode
    guards, as tests/verify-components.py notes), rendered with the engine off; the connectivity HelmRelease's spec.values.
    The meta chart resolves the cluster-shape knobs from the API groups before it forwards (the child never sees `auto`),
    so the tree carries the flavour of the `apis` it was rendered with: with CILIUM the cilium one, without it kubernetes."""
    docs = render(meta, ["--set", "components.flux.enabled=false", "--set", "global.domain=example.com", *apis])
    releases = [d for d in docs if d.get("kind") == "HelmRelease" and d["metadata"]["name"] == "agent-platform-connectivity"]
    if len(releases) != 1:
        fail(f"the meta chart rendered {len(releases)} connectivity HelmReleases; expected exactly one")
    return releases[0]["spec"]["values"]


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


def env_of(container: dict) -> dict[str, str | None]:
    return {e["name"]: e.get("value") for e in container.get("env") or []}


def runtime_of(spec: dict, runtime: str) -> dict:
    return next(c for c in spec["containers"] if c["name"] == runtime)


def init_of(spec: dict, name: str) -> dict:
    return next(c for c in spec["initContainers"] if c["name"] == name)


def check_mutations(pods_policy: dict, shape: str) -> None:
    _, runtime, name_label, _ = SHAPES[shape]
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
    storage = init_of(spec, "storage-initializer")
    if storage["resources"]["limits"]["memory"] != MEMORY:
        fail(f"{shape}: the storage-initializer's memory limit is {storage['resources']['limits'].get('memory')}, expected {MEMORY}")
    sm = mounts(storage).get("/mnt/models", {})
    if sm.get("name") != CLAIM or sm.get("subPath") != model:
        fail(f"{shape}: the storage-initializer's /mnt/models is not {CLAIM}/{model}: {sm}")
    if [c["name"] for c in spec["containers"]] != [c["name"] for c in pod["spec"]["containers"]]:
        fail(f"{shape}: the mutation changed the container list: {[c['name'] for c in spec['containers']]}")
    rm = mounts(runtime_of(spec, runtime)).get("/mnt/models", {})
    if rm.get("name") != CLAIM or rm.get("subPath") != model:
        fail(f"{shape}: {runtime}'s /mnt/models is not {CLAIM}/{model}: {rm}")
    original = mounts(runtime_of(pod["spec"], runtime))["/mnt/models"]
    if rm.get("readOnly") != original.get("readOnly"):
        fail(f"{shape}: {runtime}'s /mnt/models readOnly changed from {original.get('readOnly')} to {rm.get('readOnly')}")
    for label, before, after in (("storage-initializer", init_of(pod["spec"], "storage-initializer"), storage),
                                 (runtime, runtime_of(pod["spec"], runtime), runtime_of(spec, runtime))):
        if env_of(after) != {**env_of(before), **ENV}:
            fail(f"{shape}: {label}'s env is {env_of(after)}; expected its own {env_of(before)} plus {ENV}")
    volumes = {v["name"]: v for v in spec["volumes"]}
    if volumes.get(CLAIM) != {"name": CLAIM, "persistentVolumeClaim": {"claimName": CLAIM}}:
        fail(f"{shape}: no {CLAIM} claim volume: {volumes.get(CLAIM)}")
    if missing := [v["name"] for v in pod["spec"]["volumes"] if v["name"] not in volumes]:
        fail(f"{shape}: the mutation dropped volumes {missing}")
    ok(f"{shape}: {CLAIM}/{model} mounted at /mnt/models on storage-initializer and {runtime}, fsGroup {FSGROUP}, the limit {MEMORY}, "
       f"{ENV} next to both containers' own env, no container added, containers and volumes kept")

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

    bare = copy.deepcopy(pod)
    for c in bare["spec"]["initContainers"] + bare["spec"]["containers"]:
        c.pop("env", None)
    out = apply([pods_policy], bare)
    if out is None or env_of(init_of(out["spec"], "storage-initializer")) != ENV or env_of(runtime_of(out["spec"], runtime)) != ENV:
        fail(f"{shape}: containers without env did not get exactly {ENV}: "
             f"{out and (env_of(init_of(out['spec'], 'storage-initializer')), env_of(runtime_of(out['spec'], runtime)))}")
    ok(f"{shape}: a storage-initializer and a {runtime} without env get exactly {ENV}")

    plain = copy.deepcopy(pod)
    plain["spec"]["initContainers"] = [c for c in plain["spec"]["initContainers"] if c["name"] != "storage-initializer"]
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
        if out is None or [c["name"] for c in out["spec"]["initContainers"]] != [c["name"] for c in pod["spec"]["initContainers"]] \
                or CLAIM in {v["name"] for v in out["spec"]["volumes"]}:
            fail(f"{shape}: a pod without {name_label} got the cache (it would mount the claim's root)")
        if fs_group(out["spec"]) is not None:
            fail(f"{shape}: a pod without {name_label} got the claim's fsGroup without the claim")
        if init_of(out["spec"], "storage-initializer")["resources"]["limits"]["memory"] != MEMORY:
            fail(f"{shape}: a pod without {name_label} kept the initializer's default limit")
        if env_of(init_of(out["spec"], "storage-initializer")) != {**env_of(init_of(pod["spec"], "storage-initializer")), **ENV}:
            fail(f"{shape}: a pod without {name_label} did not get {ENV}: {env_of(init_of(out['spec'], 'storage-initializer'))}")
        ok(f"{shape}: a pod without {name_label} gets the limit and the env, no cache mount, no fsGroup")


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


def check_selectors(cilium: list[dict], k8s: list[dict]) -> None:
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


def container_port(shape: str) -> int:
    """The one port of the fixture container the shape's pod is reached on (SHAPES; an init container for a native sidecar)."""
    spec, entry = fixture(shape)["spec"], SHAPES[shape][3]
    container = next(c for c in spec["containers"] + spec.get("initContainers", []) if c["name"] == entry)
    ports = container.get("ports") or []
    if len(ports) != 1:
        fail(f"{shape}: the fixture's {entry} declares {len(ports)} container ports; this check reads exactly one")
    return int(ports[0]["containerPort"])


def cilium_ports(rules: list[dict]) -> set[int]:
    return {int(p["port"]) for r in rules for tp in r.get("toPorts") or [] for p in tp["ports"]}


def check_ports(cilium: list[dict], k8s: list[dict], via: str) -> None:
    """Each shape's policies admit exactly the port its fixture pod's runtime container listens on (#525): every ingress
    rule of its CiliumNetworkPolicy (the callers and the kubelet), its kubernetes-flavour ingress, and the kagent agents'
    egress rule that selects the shape — over the policies as rendered `via` the connectivity defaults or the meta chart's
    forwarded values."""
    agents = one(cilium, "CiliumNetworkPolicy", "-kagent-agents-to-model-serving")
    ports: dict[str, int] = {}
    for shape in SHAPES:
        want, runtime = container_port(shape), SHAPES[shape][3]
        for rule in one(cilium, "CiliumNetworkPolicy", f"-model-serving-{shape}")["spec"]["ingress"]:
            if (got := cilium_ports([rule])) != {want}:
                fail(f"-model-serving-{shape} ({via}): the ingress rule from {next(k for k in rule if k.startswith('from'))} admits {sorted(got)}; "
                     f"the fixture's {runtime} is reached on {want}")
        ingress = one(k8s, "NetworkPolicy", f"-model-serving-{shape}-ingress")["spec"]["ingress"]
        if (got := {int(p["port"]) for r in ingress for p in r["ports"]}) != {want}:
            fail(f"-model-serving-{shape}-ingress ({via}): admits {sorted(got)}; the fixture's {runtime} is reached on {want}")
        labels = {**fixture(shape)["metadata"]["labels"], "io.kubernetes.pod.namespace": NS}
        to_shape = [r for r in agents["spec"]["egress"] if any(selects(p, labels) for p in r.get("toEndpoints") or [])]
        if not to_shape or (got := cilium_ports(to_shape)) != {want}:
            fail(f"-kagent-agents-to-model-serving ({via}): the egress to the {shape} pods admits {sorted(cilium_ports(to_shape))}; "
                 f"the fixture's {runtime} is reached on {want}")
        ports[shape] = want
    if len(set(ports.values())) != len(ports):
        fail(f"the fixtures are reached on the same port ({ports}); the check could not tell one shape's value from the other's")
    ok(f"{via}: each shape's ingress (both flavours, the kubelet's rule too) and the agents' egress admit exactly the port its fixture is reached on: {ports}")


def cilium_regex(entry: dict) -> re.Pattern:
    """A toFQDNs entry as Cilium's DNS proxy compiles it (pkg/fqdn/matchpattern): lower-cased and anchored; in a
    matchPattern `*` is [-a-zA-Z0-9_]* — DNS label characters, never a dot — so a `*` admits exactly one label."""
    if "matchName" in entry:
        return re.compile("^" + re.escape(entry["matchName"].lower().rstrip(".")) + "$")
    return re.compile("^" + re.escape(entry["matchPattern"].lower().rstrip(".")).replace(r"\*", "[-a-zA-Z0-9_]*") + "$")


def check_fqdns(cilium: list[dict], k8s: list[dict], via: str) -> None:
    """The model pods' and the download Job's egress admits every name of the Hugging Face download path (#522) and stays an
    allow-list — over the policies as rendered `via` the connectivity defaults or the meta chart's forwarded values."""
    if cilium_regex({"matchPattern": "*.*.hf.co"}).match(CDN) or not cilium_regex({"matchPattern": "*.*.*.hf.co"}).match(CDN):
        fail(f"this check's Cilium pattern rule is wrong: *.*.hf.co must not, *.*.*.hf.co must match {CDN}")
    for suffix in [f"-model-serving-{s}" for s in SHAPES] + ["-model-serving-download"]:
        egress = one(cilium, "CiliumNetworkPolicy", suffix)["spec"]["egress"]
        if widened := [k for r in egress for k in ("toCIDR", "toCIDRSet") if k in r] + [e for r in egress for e in r.get("toEntities") or [] if e == "world"]:
            fail(f"{suffix}: the egress is no longer a toFQDNs allow-list: {widened}")
        entries = [e for r in egress for e in r.get("toFQDNs") or []]
        rendered = [e.get("matchName") or e.get("matchPattern") for e in entries]
        patterns = [cilium_regex(e) for e in entries]
        if denied := [h for h in HUB_HOSTS if not any(p.match(h) for p in patterns)]:
            fail(f"{suffix} ({via}): toFQDNs {rendered} admit none of {denied} under Cilium's rule (a * never crosses a dot)")
        if admitted := [h for h in NOT_HUB_HOSTS if any(p.match(h) for p in patterns)]:
            fail(f"{suffix} ({via}): toFQDNs {rendered} admit {admitted}")
        ok(f"{suffix} ({via}): toFQDNs {rendered} admit every name of the download path ({', '.join(HUB_HOSTS)}), none of {NOT_HUB_HOSTS}; no toCIDR, no world")
    for suffix in [f"-model-serving-{s}-egress" for s in SHAPES] + ["-model-serving-download-egress"]:
        egress = one(k8s, "NetworkPolicy", suffix)["spec"]["egress"]
        blocks = [(t["ipBlock"]["cidr"], [p["port"] for p in r.get("ports") or []]) for r in egress for t in r.get("to") or [] if "ipBlock" in t]
        if ("0.0.0.0/0", [443]) not in blocks:
            fail(f"{suffix}: the kubernetes flavour admits {blocks}; it selects no names, so 443 to every public block is what reaches the CDN")
    ok(f"kubernetes flavour ({via}): each model pod's and the download Job's egress admits 443 to every public block (no name to get wrong)")


def main(connectivity: str, meta: str) -> int:
    if shutil.which(KYVERNO) is None:
        fail(f"the kyverno CLI ({KYVERNO}) is not installed; the mutations are asserted with `kyverno apply`")
    docs = render(connectivity, [])
    pods_policy = one(docs, "ClusterPolicy", "-model-serving-pods")
    deployments_policy = one(docs, "ClusterPolicy", "-model-serving-deployments")
    exception = one(docs, "PolicyException", "model-serving-predictors")
    rules = [r["name"] for r in pods_policy["spec"]["rules"]]
    expected = [f"redirect-model-storage-{s}" for s in SHAPES] + [f"model-pod-env-{s}" for s in SHAPES] + ["storage-initializer-memory"]
    if rules != expected:
        fail(f"the pods policy's rules are {rules}; expected the redirect rules, the env rules and the limit, no rule adding a container (#518)")
    without = one(render(connectivity, ["--set", "modelServing.policies.env=null"]), "ClusterPolicy", "-model-serving-pods")
    if [r["name"] for r in without["spec"]["rules"]] != [r for r in expected if not r.startswith("model-pod-env-")]:
        fail(f"an empty modelServing.policies.env renders {[r['name'] for r in without['spec']['rules']]}; expected no env rule")
    ok("the pods policy's rules: the redirect rules, the env rules, the limit; an empty modelServing.policies.env renders no env rule")
    for shape in SHAPES:
        check_mutations(pods_policy, shape)
        check_pod_security(pods_policy, exception, shape)
    check_deployments(deployments_policy)
    cilium = render(connectivity, CILIUM)
    check_selectors(cilium, docs)
    check_fqdns(cilium, docs, "the connectivity chart's defaults")
    check_ports(cilium, docs, "the connectivity chart's defaults")
    regressed = render(connectivity, [*CILIUM, "--set", f"modelServing.networkPolicy.llmisvcWorkload.port={container_port('predictor')}"])
    try:
        check_ports(regressed, docs, "the negative control")
    except SystemExit as e:
        if "-model-serving-llmisvc-workload (" not in str(e):
            fail(f"the port check failed the 4.28.18 shape for the wrong reason: {e}")
    else:
        fail("the port check passed a render whose llmisvc-workload policy admits the classic predictor's port (the 4.28.18 shape)")
    ok("a render whose llm-d value is the classic port fails the port check naming -model-serving-llmisvc-workload")
    with tempfile.TemporaryDirectory() as tmp:
        through_meta = []
        for flavour, apis in (("cilium", CILIUM), ("kubernetes", [])):
            forwarded = os.path.join(tmp, f"forwarded-{flavour}.yaml")
            with open(forwarded, "w") as f:
                yaml.safe_dump(forwarded_values(meta, apis), f)
            through_meta.append(render(connectivity, ["-f", forwarded, *apis]))
        check_fqdns(*through_meta, "the meta chart's forwarded values")
        check_ports(*through_meta, "the meta chart's forwarded values")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit("usage: verify-model-serving-policies.py <connectivity chart dir> <meta chart dir>")
    sys.exit(main(sys.argv[1], sys.argv[2]))
