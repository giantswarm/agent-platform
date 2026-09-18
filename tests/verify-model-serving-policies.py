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
    container (its readOnly as KServe declared it: the runtime writes nothing
    into the model's directory); the runtime container mounts the claim a
    second time at /mnt/vllm-cache from the claim-wide subPath .vllm-cache and
    carries VLLM_CACHE_ROOT naming that path and TRITON_CACHE_DIR naming its
    triton/ directory (a preset that serves eager never compiles, so vLLM's
    own in-process redirect of Triton's cache under VLLM_CACHE_ROOT never
    runs for it, #572), the storage-initializer neither
    — vLLM's cache a directory of the claim's own, never under /mnt/models,
    where the initializer's Hugging Face client owns <model>/.cache (uid 1000,
    mode 755) and a cache root there crash-looped every cold start (#537,
    #541); no mount or env value of the pod names a path under /mnt/models
    — the pod carries the claim's
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
    cache, neither cache env and no fsGroup (the mount would otherwise land
    on the claim's root).
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
  * The pre-pull DaemonSet (modelServing.prepull, #545) renders in the serving
    namespace by default: one init container per image of
    modelServing.prepull.images — the first the llm-d runtime image the
    well-known LLMInferenceServiceConfig names — running /bin/true, a pause
    main container, the pool's taint tolerated first and every taint after it,
    the manufacturer label selected with the pool's own label merged under it,
    no GPU resource, no runtimeClassName, no ServiceAccount token. It is a
    post-install,post-upgrade,post-rollback hook object at weight 0, replaced
    before creation (#563: a release resource's pods gate the release's wait,
    and a pod whose image cannot be pulled is never Ready), its deny-all policy
    a release resource; a pre-delete hook Job deletes it by name as the hook
    identity, whose ClusterRole carries delete on exactly that DaemonSet and is
    created for the pre-delete event; prepull.enabled: false renders neither
    the Job nor the rule. modelServing.prepull.nodeSelector set renders alone
    (#562), empty renders Karpenter's label, the pool's label is merged under
    either, a non-string label value fails the render naming the key; a
    selector set on the meta chart reaches the DaemonSet alone. Its pod,
    built from the template, passes the fleet's restricted PSS with NO
    exception (every rule passes), is touched by none of the chart's
    mutations, and is selected by no shape's policy, not by the
    PolicyException and not by the agents' egress — only by its own deny-all
    policy (kubernetes: both policy types, no rule; cilium: one empty rule per
    direction), which selects no shape's fixture and not the download Job's
    pod. `enabled: false` renders neither object; an empty image list fails
    the render naming the key; the values the meta chart forwards render the
    same DaemonSet with the same images. A model init container named through
    modelServing.prepull.modelPresets (#551) keeps the pod PSS-clean with no
    exception; the default carries none (tests/verify-model-images.py holds
    the container's shape).
  * The network policies (both flavours), the kagent agents' egress and the
    PolicyException select each fixture by exactly its own shape's policy and
    never the download Job's pod.
  * Image verification (modelServing.imageVerification, #552, #575) is off by
    default and renders nothing without kyverno.io/v1. Enabled alone, the
    chart's defaults reach the rule: every image under the platform's
    registry namespace, the Giant Swarm CircleCI identity as the one keyless
    attestor, the Sigstore bundle format. On, one verifyImages
    ClusterPolicy with one rule per pod shape selects exactly its shape's
    Pods in the serving namespace at CREATE and UPDATE, carrying the image
    references and the attestor entries verbatim — a keyless CircleCI
    identity and a public key, as one attestor set of count 1 — with
    mutateDigest, required and failureAction as set; `kyverno apply` accepts
    the policy and skips both fixture pods, none of whose images match the
    references (a signature cannot be checked offline), and drops a policy
    with a misspelt verifyImages field, so that acceptance has teeth; enabled
    with an empty images list, no attestor, an entry that is no Kyverno
    attestor entry, a failureAction outside Enforce | Audit or an unknown key
    fails the render naming the key; the values the meta chart forwards
    render the same policy.
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
MEMORY = "8Gi"
# modelServing.policies.env, as the chart ships it (#520).
ENV = {"HF_HUB_DISABLE_XET": "1"}
MODEL_DIR = "/mnt/models"
# vLLM's cache: the claim's own directory, mounted on the runtime container by
# the redirect rule, which sets the envs naming it in the same patch (#537,
# #541) — Triton's kernel cache a directory of it (#572).
VLLM_CACHE = {"name": CLAIM, "mountPath": "/mnt/vllm-cache", "subPath": ".vllm-cache"}
VLLM_ENV = {"VLLM_CACHE_ROOT": VLLM_CACHE["mountPath"], "TRITON_CACHE_DIR": f"{VLLM_CACHE['mountPath']}/triton"}
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
# The pre-pull DaemonSet's pods (#545): its selector label; the pool's taint they tolerate first; the label Karpenter
# gives every GPU node, their default selector.
PREPULL_LABEL = {"agent-platform.giantswarm.io/model-serving-prepull": "true"}
POOL_TOLERATION = {"key": "nvidia.com/gpu", "operator": "Exists", "effect": "NoSchedule"}
GPU_NODE = {"karpenter.k8s.aws/instance-gpu-manufacturer": "nvidia"}
# An installation's own selector (#562: a GPU node not launched by Karpenter, labelled by the GPU operator's feature
# discovery) and a pool's label; the Helm annotations that make the DaemonSet a hook object (#563).
OWN_NODE = {"nvidia.com/gpu.present": "true"}
POOL_LABEL = {"giantswarm.io/machine-pool": "ci-gpu00"}
OWN_SELECTOR = ["--set-string", "modelServing.prepull.nodeSelector.nvidia\\.com/gpu\\.present=true"]
POOL_SELECTOR = ["--set-string", "modelServing.gpuPool.nodeSelector.giantswarm\\.io/machine-pool=ci-gpu00"]
PREPULL_HOOK = {"helm.sh/hook": "post-install,post-upgrade,post-rollback", "helm.sh/hook-weight": "0", "helm.sh/hook-delete-policy": "before-hook-creation"}
# The names the Hugging Face download path uses (#522): the Hub and its API redirects, the LFS fronts one label
# under hf.co, the Xet fronts two, and the download CDN three — the Hub redirects every shard request of a
# Xet-backed repository there, Xet client or not (us.aws.cdn.hf.co; the regional siblings share the shape). A new
# host shape is added here, and modelServing.networkPolicy.huggingFace.fqdns gains its depth.
CDN = "us.aws.cdn.hf.co"
HUB_HOSTS = ["huggingface.co", "cdn-lfs.huggingface.co", "cdn-lfs-us-1.hf.co", "cas-server.xethub.hf.co",
             "transfer.xethub.hf.co", "cas-bridge.xethub.hf.co", CDN, "eu.aws.cdn.hf.co"]
# What the allow-list keeps out: a depth no name of the download path has, the bare apex, look-alike domains.
NOT_HUB_HOSTS = ["a.b.c.d.hf.co", "hf.co", "huggingface.co.example.com", "hf.co.example.com", "example.com"]
# modelServing.imageVerification (#552, #575). The DEFAULTS are the chart's: every image under the platform's registry
# namespace, one keyless attestor — the identity every image a Giant Swarm CircleCI project signs carries (the architect
# orb's cosign keyless signing: issuer https://oidc.circleci.com, subject the pipeline definition that ran) — and the
# Sigstore bundle format cosign 3 writes (the only format Kyverno finds an orb signature in). The OVERRIDE is an
# installation's own block, passed through as written: a registry of its own, the fleet identity by exact subject next to a
# public key (Kyverno's documentation key), the legacy Cosign format.
IV_SUFFIX = "-model-serving-image-verification"
IV_DEFAULT_IMAGES = ["gsoci.azurecr.io/giantswarm/*"]
IV_DEFAULT_ATTESTORS = [
    {"keyless": {"issuer": "https://oidc.circleci.com",
                 "subjectRegExp": r"^https://circleci\.com/api/v2/projects/[a-f0-9-]+/pipeline-definitions/[a-f0-9-]+$",
                 "rekor": {"url": "https://rekor.sigstore.dev"}}},
]
IV_DEFAULT_TYPE = "SigstoreBundle"
IV_IMAGES = ["registry.example.com/models/*"]
IV_ATTESTORS = [
    {"keyless": {"issuer": "https://oidc.circleci.com",
                 "subject": "https://circleci.com/api/v2/projects/00000000-0000-0000-0000-000000000000/pipeline-definitions/00000000-0000-0000-0000-000000000000",
                 "rekor": {"url": "https://rekor.sigstore.dev"}}},
    {"keys": {"publicKeys": "-----BEGIN PUBLIC KEY-----\nMFkwEwYHKoZIzj0CAQYIKoZIzj0DAQcDQgAE8nXRh950IZbRj8Ra/N9sbqOPZrfM\n"
                            "5/KAQN0/KjHcorm/J5yctVd7iEcnessRQjU917hmKO6JWVGHpDguIyakZA==\n-----END PUBLIC KEY-----"}},
]
IV_ON = {"enabled": True, "images": IV_IMAGES, "attestors": IV_ATTESTORS, "type": "Cosign"}
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


def render(chart: str, flags: list[str], base: list[str] = BASE) -> list[dict]:
    result = subprocess.run([HELM, "template", "t", chart, *base, *flags], capture_output=True, text=True, check=False)
    if result.returncode != 0:
        fail(f"render of {chart} {' '.join(flags)} failed:\n{result.stderr}")
    return [doc for doc in yaml.safe_load_all(result.stdout) if doc]


def forwarded_values(meta: str, apis: list[str], extra: list[str] = ()) -> dict:
    """The values an installation's connectivity release receives: the meta chart's defaults with the model serving switch
    on (BASE) and the one input every render needs (global.domain — the CI values would trip the wiring's ingress-mode
    guards, as tests/verify-components.py notes), rendered with the engine off; the connectivity HelmRelease's spec.values.
    The meta chart resolves the cluster-shape knobs from the API groups before it forwards (the child never sees `auto`),
    so the tree carries the flavour of the `apis` it was rendered with: with CILIUM the cilium one, without it kubernetes."""
    docs = render(meta, ["--set", "components.flux.enabled=false", "--set", "global.domain=example.com", *apis, *extra])
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
    rm = mounts(runtime_of(spec, runtime)).get(MODEL_DIR, {})
    if rm.get("name") != CLAIM or rm.get("subPath") != model:
        fail(f"{shape}: {runtime}'s {MODEL_DIR} is not {CLAIM}/{model}: {rm}")
    original = mounts(runtime_of(pod["spec"], runtime))[MODEL_DIR]
    if rm.get("readOnly") != original.get("readOnly"):
        fail(f"{shape}: {runtime}'s {MODEL_DIR} readOnly changed from {original.get('readOnly')} to {rm.get('readOnly')}; "
             "the runtime writes nothing into the model's directory (#541)")
    cm = mounts(runtime_of(spec, runtime)).get(VLLM_CACHE["mountPath"], {})
    if cm != VLLM_CACHE:
        fail(f"{shape}: {runtime}'s {VLLM_CACHE['mountPath']} is not {CLAIM}/{VLLM_CACHE['subPath']}: {cm}")
    if VLLM_CACHE["mountPath"] in mounts(storage):
        fail(f"{shape}: the storage-initializer mounts vLLM's cache; the directory is the runtime's alone")
    for name in VLLM_ENV:
        value = env_of(runtime_of(spec, runtime)).get(name) or ""
        if value != VLLM_CACHE["mountPath"] and not value.startswith(f"{VLLM_CACHE['mountPath']}/"):
            fail(f"{shape}: {runtime}'s {name}={value!r} names a path outside the cache mount {VLLM_CACHE['mountPath']}; "
                 "the rule sets no cache env without the directory behind it")
    for label, before, after, extra in (("storage-initializer", init_of(pod["spec"], "storage-initializer"), storage, {}),
                                        (runtime, runtime_of(pod["spec"], runtime), runtime_of(spec, runtime), VLLM_ENV)):
        if env_of(after) != {**env_of(before), **ENV, **extra}:
            fail(f"{shape}: {label}'s env is {env_of(after)}; expected its own {env_of(before)} plus {ENV}{' plus ' + str(extra) if extra else ''}")
    for c in spec["initContainers"] + spec["containers"]:
        for path in [m["mountPath"] for m in c.get("volumeMounts") or []] + [v for v in env_of(c).values() if v]:
            if path.startswith(f"{MODEL_DIR}/"):
                fail(f"{shape}: {c['name']} names {path}, a path under the model's directory — the initializer's Hugging Face client "
                     f"owns {MODEL_DIR}/.cache (uid 1000, mode 755) and nothing of the pod writes there (#541)")
    volumes = {v["name"]: v for v in spec["volumes"]}
    if volumes.get(CLAIM) != {"name": CLAIM, "persistentVolumeClaim": {"claimName": CLAIM}}:
        fail(f"{shape}: no {CLAIM} claim volume: {volumes.get(CLAIM)}")
    if missing := [v["name"] for v in pod["spec"]["volumes"] if v["name"] not in volumes]:
        fail(f"{shape}: the mutation dropped volumes {missing}")
    ok(f"{shape}: {CLAIM}/{model} mounted at {MODEL_DIR} on storage-initializer and {runtime} (readOnly as declared), "
       f"vLLM's cache {CLAIM}/{VLLM_CACHE['subPath']} at {VLLM_CACHE['mountPath']} with {VLLM_ENV} on {runtime} alone, "
       f"fsGroup {FSGROUP}, the limit {MEMORY}, {ENV} next to both containers' own env, nothing under {MODEL_DIR}/, "
       f"no container added, containers and volumes kept")

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
    if out is None or env_of(init_of(out["spec"], "storage-initializer")) != ENV or env_of(runtime_of(out["spec"], runtime)) != {**ENV, **VLLM_ENV}:
        fail(f"{shape}: containers without env did not get exactly {ENV} (the {runtime} plus {VLLM_ENV}): "
             f"{out and (env_of(init_of(out['spec'], 'storage-initializer')), env_of(runtime_of(out['spec'], runtime)))}")
    ok(f"{shape}: a storage-initializer without env gets exactly {ENV}, a {runtime} without env exactly {ENV} plus {VLLM_ENV}")

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
        nameless_runtime = runtime_of(out["spec"], runtime)
        if set(VLLM_ENV) & set(env_of(nameless_runtime)) or VLLM_CACHE["mountPath"] in mounts(nameless_runtime):
            fail(f"{shape}: a pod without {name_label} got a cache env or vLLM's cache mount without the claim")
        ok(f"{shape}: a pod without {name_label} gets the limit and the env, no cache mount, neither cache env, no fsGroup")


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
        if not d["metadata"]["name"].endswith("-model-serving-prepull") and selects(selector, prepull_labels(k8s)):
            fail(f"{d['metadata']['name']} selects the pre-pull DaemonSet's pod")
    for who, labels in (("model-manager's download-Job pod", DOWNLOAD_LABELS), ("the pre-pull DaemonSet's pod", prepull_labels(k8s))):
        if any(selects(s, labels) for s in exception_selectors) or any(selects(p, {**labels, "io.kubernetes.pod.namespace": NS}) for p in peers):
            fail(f"the PolicyException or the agents' egress selects {who}")
    ok("each shape's pod is selected by exactly its own network policies (both flavours), the agents' egress and the PolicyException; "
       "the download Job's pod and the pre-pull pod by none of them")


def prepull_pod(docs: list[dict]) -> dict:
    """The pod the pre-pull DaemonSet's template describes, as the kubelet would create it in the serving namespace."""
    ds = one(docs, "DaemonSet", "-model-serving-prepull")
    if ds["metadata"]["namespace"] != NS:
        fail(f"the pre-pull DaemonSet renders in {ds['metadata']['namespace']}, expected the serving namespace {NS}")
    template = ds["spec"]["template"]
    return {"apiVersion": "v1", "kind": "Pod", "metadata": {"name": f"{ds['metadata']['name']}-x7k2q", "namespace": NS, "labels": template["metadata"]["labels"]},
            "spec": copy.deepcopy(template["spec"])}


def prepull_labels(docs: list[dict]) -> dict:
    return prepull_pod(docs)["metadata"]["labels"]


def check_prepull(connectivity: str, k8s: list[dict], cilium: list[dict], pods_policy: dict, exception: dict) -> None:
    """The pre-pull DaemonSet (#545): its shape, its pod against the fleet's restricted PSS with no exception, its deny-all policy."""
    with open(f"{connectivity}/values.yaml", encoding="utf-8") as f:
        prepull = yaml.safe_load(f)["modelServing"]["prepull"]
    ds = one(k8s, "DaemonSet", "-model-serving-prepull")
    pod = prepull_pod(k8s)
    spec = pod["spec"]
    if not selects(ds["spec"]["selector"], pod["metadata"]["labels"]) or not selects({"matchLabels": PREPULL_LABEL}, pod["metadata"]["labels"]):
        fail(f"the pre-pull DaemonSet's selector {ds['spec']['selector']} does not select its own pod, or the pod lacks {PREPULL_LABEL}")
    images = [c["image"] for c in spec.get("initContainers") or []]
    if images != prepull["images"] or not images or "/llm-d-cuda:" not in images[0]:
        fail(f"the pre-pull init containers pull {images}; expected modelServing.prepull.images {prepull['images']}, the llm-d runtime image first")
    if any(c.get("command") != ["/bin/true"] for c in spec["initContainers"]):
        fail(f"every pre-pull init container runs /bin/true; got {[c.get('command') for c in spec['initContainers']]}")
    if [c["name"] for c in spec["containers"]] != ["pause"] or not spec["containers"][0]["image"].endswith("/giantswarm/pause:" + prepull["pauseImage"]["tag"]):
        fail(f"the pre-pull pod's main container is the pause image alone; got {[(c['name'], c['image']) for c in spec['containers']]}")
    for c in spec["initContainers"] + spec["containers"]:
        for kind in ("requests", "limits"):
            if "nvidia.com/gpu" in (c.get("resources") or {}).get(kind, {}):
                fail(f"pre-pull container {c['name']} {kind} a GPU")
        if not (c.get("resources") or {}).get("requests") or not c["resources"].get("limits"):
            fail(f"pre-pull container {c['name']} declares no requests or no limits")
    if "runtimeClassName" in spec or spec.get("automountServiceAccountToken") is not False:
        fail("the pre-pull pod names a runtimeClass or mounts a ServiceAccount token")
    tolerations = spec.get("tolerations") or []
    if not tolerations or tolerations[0] != POOL_TOLERATION or {"operator": "Exists"} not in tolerations:
        fail(f"the pre-pull pod tolerates {tolerations}; expected the pool's taint first and every taint after it")
    if spec.get("nodeSelector") != GPU_NODE:
        fail(f"the pre-pull pod's default node selector is {spec.get('nodeSelector')}; expected Karpenter's GPU label {GPU_NODE}")
    ok("the pre-pull DaemonSet: one /bin/true init container per image (the llm-d runtime image first), the pause main container, "
       "no GPU, no runtimeClass, no token; the pool's taint tolerated first and every taint after it; Karpenter's GPU label selected")

    # A hook object, not a release resource (#563): the release's wait counts a DaemonSet ready by its pods, and a pod
    # whose image cannot be pulled is never Ready. The deny-all policy stays a release resource; the pre-delete hook
    # Job removes the DaemonSet as the hook identity, which carries delete on exactly that DaemonSet for that event.
    annotations = {k: v for k, v in (ds["metadata"].get("annotations") or {}).items() if k.startswith("helm.sh/")}
    if annotations != PREPULL_HOOK:
        fail(f"the pre-pull DaemonSet's Helm annotations are {annotations}; expected the hook object {PREPULL_HOOK}")
    for policy in (one(k8s, "NetworkPolicy", "-model-serving-prepull"), one(cilium, "CiliumNetworkPolicy", "-model-serving-prepull")):
        if any(k.startswith("helm.sh/hook") for k in (policy["metadata"].get("annotations") or {})):
            fail(f"{policy['kind']}/{policy['metadata']['name']} carries hook annotations; the deny-all is a release resource")
    cleanup = one(k8s, "Job", "-model-serving-prepull-cleanup")
    script = cleanup["spec"]["template"]["spec"]["containers"][0]["args"][0]
    if cleanup["metadata"]["annotations"].get("helm.sh/hook") != "pre-delete" or cleanup["metadata"]["namespace"] != "agent-platform":
        fail(f"the pre-pull cleanup Job is not a pre-delete hook in the release namespace: {cleanup['metadata']}")
    if f'kubectl -n "{NS}" delete daemonset "{ds["metadata"]["name"]}" --ignore-not-found --wait=false' not in script:
        fail(f"the pre-pull cleanup Job does not delete the DaemonSet by name in the serving namespace:\n{script}")
    role = one(k8s, "ClusterRole", "-hooks")
    rule = {"apiGroups": ["apps"], "resources": ["daemonsets"], "resourceNames": [ds["metadata"]["name"]], "verbs": ["delete"]}
    if rule not in role["rules"] or "pre-delete" not in role["metadata"]["annotations"]["helm.sh/hook"].split(","):
        fail(f"the hook identity lacks delete on exactly the pre-pull DaemonSet, or is not created for the pre-delete event: {role['rules']}, {role['metadata']['annotations']}")
    if cleanup["spec"]["template"]["spec"].get("serviceAccountName") != role["metadata"]["name"]:
        fail(f"the pre-pull cleanup Job runs as {cleanup['spec']['template']['spec'].get('serviceAccountName')}, not as the hook identity {role['metadata']['name']}")
    ok("the pre-pull DaemonSet is a post-install,post-upgrade,post-rollback hook object at weight 0, replaced before creation, its deny-all a "
       "release resource; the pre-delete cleanup Job deletes it by name as the hook identity, whose ClusterRole carries delete on exactly that "
       "DaemonSet and is created for the pre-delete event")

    # The selector (#562): a set map renders alone, the pool's label merged under it; empty renders the default; a
    # non-string label value fails the render naming the key.
    if (own := prepull_pod(render(connectivity, OWN_SELECTOR))["spec"].get("nodeSelector")) != OWN_NODE:
        fail(f"modelServing.prepull.nodeSelector set renders {own}; expected the installation's map alone, {OWN_NODE}")
    if (pooled := prepull_pod(render(connectivity, [*OWN_SELECTOR, *POOL_SELECTOR]))["spec"].get("nodeSelector")) != {**OWN_NODE, **POOL_LABEL}:
        fail(f"modelServing.prepull.nodeSelector set with a pool label renders {pooled}; expected the pool's label merged under the installation's map")
    if (default_pooled := prepull_pod(render(connectivity, POOL_SELECTOR))["spec"].get("nodeSelector")) != {**GPU_NODE, **POOL_LABEL}:
        fail(f"the default pre-pull selector with a pool label renders {default_pooled}; expected the pool's label merged under Karpenter's")
    result = subprocess.run([HELM, "template", "t", connectivity, *BASE, "--set", "modelServing.prepull.nodeSelector.generation=6"], capture_output=True, text=True, check=False)
    if result.returncode == 0 or "modelServing.prepull.nodeSelector[generation] (6) must be a string" not in result.stderr:
        fail(f"a non-string pre-pull label value must fail the render naming the key; got rc={result.returncode}:\n{result.stderr}")
    ok("modelServing.prepull.nodeSelector set renders alone, empty renders Karpenter's label, the pool's label is merged under either; a non-string label value fails the render naming the key")

    results = validate(pod, None)
    if failed := outcome(results, "fail"):
        fail(f"the pre-pull pod fails the restricted PSS with no exception: {sorted(failed)}")
    if outcome(results, "pass") != set(results):
        fail(f"every restricted-PSS rule must pass on the pre-pull pod; skipped {sorted(outcome(results, 'skip'))}")
    out = apply([pods_policy], pod)
    if out is not None and out["spec"] != pod["spec"]:
        fail("the chart's model-pod mutations touch the pre-pull pod")
    for label_selector in [m["resources"]["selector"] for m in exception["spec"]["match"]["any"] if "selector" in m["resources"]]:
        if selects(label_selector, pod["metadata"]["labels"]):
            fail("the PolicyException selects the pre-pull pod; its pod needs none")
    ok("the pre-pull pod passes every rule of the fleet's restricted PSS with no exception, and no mutation of the chart touches it")

    # A model init container (modelServing.prepull.modelPresets, #551) carries the runtime init containers' security context, so
    # the pod stays PSS-clean with one; tests/verify-model-images.py holds the container's shape, this holds the pod's admission.
    if any(c["name"].startswith("pull-model-") for c in spec["initContainers"]):
        fail(f"the default pre-pull pod carries a model init container: {[c['name'] for c in spec['initContainers']]}")
    with_model = prepull_pod(render(connectivity, ["-f", f"{connectivity}/ci/test-model-serving-oci-values.yaml"]))
    model_inits = [c["name"] for c in with_model["spec"]["initContainers"] if c["name"].startswith("pull-model-")]
    if model_inits != ["pull-model-oci-model"]:
        fail(f"the fixture's pre-pull pod carries the model init containers {model_inits}; expected pull-model-oci-model alone")
    results = validate(with_model, None)
    if outcome(results, "pass") != set(results):
        fail(f"the pre-pull pod with a model init container must pass every restricted-PSS rule with no exception; "
             f"failed {sorted(outcome(results, 'fail'))}, skipped {sorted(outcome(results, 'skip'))}")
    ok("the default pre-pull pod carries no model init container; with one (modelServing.prepull.modelPresets) the pod still passes every "
       "rule of the fleet's restricted PSS with no exception")

    k8s_policy = one(k8s, "NetworkPolicy", "-model-serving-prepull")
    if sorted(k8s_policy["spec"]["policyTypes"]) != ["Egress", "Ingress"] or "ingress" in k8s_policy["spec"] or "egress" in k8s_policy["spec"]:
        fail(f"the kubernetes-flavour pre-pull policy is not a deny-all: {k8s_policy['spec']}")
    cilium_policy = one(cilium, "CiliumNetworkPolicy", "-model-serving-prepull")
    if cilium_policy["spec"].get("ingress") != [{}] or cilium_policy["spec"].get("egress") != [{}]:
        fail(f"the cilium-flavour pre-pull policy is not a deny-all (one empty rule per direction): {cilium_policy['spec']}")
    for policy, selector in ((k8s_policy, k8s_policy["spec"]["podSelector"]), (cilium_policy, cilium_policy["spec"]["endpointSelector"])):
        if policy["metadata"]["namespace"] != NS or not selects(selector, pod["metadata"]["labels"]):
            fail(f"{policy['kind']}/{policy['metadata']['name']} does not select the pre-pull pod in {NS}")
        if selects(selector, DOWNLOAD_LABELS) or any(selects(selector, fixture(shape)["metadata"]["labels"]) for shape in SHAPES):
            fail(f"{policy['kind']}/{policy['metadata']['name']} selects a model pod or the download Job's pod")
    ok("the pre-pull pods are denied all traffic by a policy of their own in both flavours, which selects them alone")

    off = render(connectivity, ["--set", "modelServing.prepull.enabled=false", *CILIUM])
    if any(d["metadata"]["name"].endswith(("-model-serving-prepull", "-model-serving-prepull-cleanup")) for d in off):
        fail("modelServing.prepull.enabled=false still renders a pre-pull object or its cleanup Job")
    if any("daemonsets" in r.get("resources", []) for r in one(off, "ClusterRole", "-hooks")["rules"]):
        fail("modelServing.prepull.enabled=false still grants the hook identity delete on daemonsets")
    result = subprocess.run([HELM, "template", "t", connectivity, *BASE, "--set", "modelServing.prepull.images=null"], capture_output=True, text=True, check=False)
    if result.returncode == 0 or "modelServing.prepull.images is empty" not in result.stderr:
        fail(f"an empty modelServing.prepull.images must fail the render naming the key; got rc={result.returncode}:\n{result.stderr}")
    ok("modelServing.prepull.enabled=false renders no pre-pull object, no cleanup Job and no daemonsets rule; an empty image list fails the render naming the key")


def check_prepull_forwarded(connectivity: str, meta: str, through_meta: list[dict], tmp: str) -> None:
    """The values the meta chart forwards render the same DaemonSet: the mirrored image list, the same selector (#545);
    a selector set on the meta chart reaches the DaemonSet alone (#562)."""
    own = one(render(connectivity, []), "DaemonSet", "-model-serving-prepull")["spec"]["template"]["spec"]
    forwarded = one(through_meta, "DaemonSet", "-model-serving-prepull")["spec"]["template"]["spec"]
    if forwarded != own:
        fail(f"the pre-pull pod the meta chart's forwarded values render differs from the connectivity default:\n{yaml.safe_dump(forwarded)}\n--- connectivity:\n{yaml.safe_dump(own)}")
    ok("the meta chart's forwarded values render the pre-pull DaemonSet's pod as the connectivity default does (the mirrored images and selector)")
    # The meta chart's copy of the block is `{}`, so nothing is merged into an installation's map on the way, and the
    # connectivity default is the template's, which a set map replaces (the 4.37 shape kept Karpenter's key beside it).
    path = os.path.join(tmp, "forwarded-own-selector.yaml")
    with open(path, "w") as f:
        yaml.safe_dump(forwarded_values(meta, [], OWN_SELECTOR), f)
    if (selector := prepull_pod(render(connectivity, ["-f", path]))["spec"].get("nodeSelector")) != OWN_NODE:
        fail(f"an installation's prepull.nodeSelector set on the meta chart renders {selector} on the DaemonSet; expected its map alone, {OWN_NODE}")
    ok("an installation's modelServing.prepull.nodeSelector set on the meta chart reaches the DaemonSet alone")


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


def loaded_rules(policies: list[dict], resource: dict) -> int:
    """How many rules `kyverno apply` loaded from the policies: the CLI drops a policy its schema refuses and applies the rest."""
    with tempfile.TemporaryDirectory(prefix="ap-model-serving-iv-") as tmp:
        pathlib.Path(tmp, "policies.yaml").write_text(yaml.safe_dump_all(policies), encoding="utf-8")
        pathlib.Path(tmp, "resource.yaml").write_text(yaml.safe_dump(resource), encoding="utf-8")
        result = subprocess.run([KYVERNO, "apply", f"{tmp}/policies.yaml", "--resource", f"{tmp}/resource.yaml"], capture_output=True, text=True, check=False)
    if not (m := re.search(r"Applying (\d+) policy rule", result.stdout)):
        fail(f"kyverno apply did not report how many rules it loaded:\n{result.stdout}\n{result.stderr}")
    return int(m.group(1))


def image_verification_values(tmp: str, block: dict, *extra: str) -> str:
    """A values file turning modelServing.imageVerification on with `block` merged over IV_ON."""
    path = os.path.join(tmp, f"iv-{len(os.listdir(tmp))}.yaml")
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump({"modelServing": {"imageVerification": {**IV_ON, **block}}}, f)
    return path


def check_image_verification(connectivity: str, docs: list[dict]) -> None:
    """modelServing.imageVerification (#552): off by default and without Kyverno; on, one verifyImages rule per shape with the
    references and the attestor entries verbatim and the knobs as set, accepted by Kyverno's schema and skipping a pod none of
    whose images match; the guards name their key."""
    if any(d["metadata"]["name"].endswith(IV_SUFFIX) for d in docs):
        fail("modelServing.imageVerification is off by default, yet the default render carries the image-verification policy")
    with tempfile.TemporaryDirectory(prefix="ap-model-serving-iv-") as tmp:
        # The switch alone: the chart's defaults reach the rule — the platform's registry namespace, the Giant Swarm
        # CircleCI identity as one keyless entry, the Sigstore bundle format (#575).
        defaults = one(render(connectivity, ["--set", "modelServing.imageVerification.enabled=true"]), "ClusterPolicy", IV_SUFFIX)
        for rule in defaults["spec"]["rules"]:
            v = rule["verifyImages"][0]
            if v["imageReferences"] != IV_DEFAULT_IMAGES or v["type"] != IV_DEFAULT_TYPE \
                    or v["attestors"] != [{"count": 1, "entries": IV_DEFAULT_ATTESTORS}]:
                fail(f"{rule['name']}: enabled alone must render the defaults — imageReferences {IV_DEFAULT_IMAGES}, type "
                     f"{IV_DEFAULT_TYPE}, the Giant Swarm CircleCI identity as the one keyless attestor; got\n{yaml.safe_dump(v)}")
        ok("enabled alone verifies every image under the platform's registry namespace against the Giant Swarm CircleCI identity "
           "(issuer https://oidc.circleci.com, subject a pipeline definition) in the Sigstore bundle format")
        on = image_verification_values(tmp, {})
        policy = one(render(connectivity, ["-f", on]), "ClusterPolicy", IV_SUFFIX)
        spec = policy["spec"]
        if spec.get("background") is not False or spec.get("webhookTimeoutSeconds") != 30:
            fail(f"the image-verification policy is admission-only with a 30 s webhook timeout; got background={spec.get('background')} "
                 f"webhookTimeoutSeconds={spec.get('webhookTimeoutSeconds')}")
        if [r["name"] for r in spec["rules"]] != [f"verify-model-images-{s}" for s in SHAPES]:
            fail(f"the image-verification policy's rules are {[r['name'] for r in spec['rules']]}; expected one per pod shape")
        expected = {"imageReferences": IV_IMAGES, "type": "Cosign", "mutateDigest": True, "required": True, "failureAction": "Enforce",
                    "attestors": [{"count": 1, "entries": IV_ATTESTORS}]}
        for rule, shape in zip(spec["rules"], SHAPES):
            matches = rule["match"]["any"]
            resources = matches[0]["resources"] if len(matches) == 1 else {}
            if resources.get("kinds") != ["Pod"] or resources.get("namespaces") != [NS] or sorted(resources.get("operations") or []) != ["CREATE", "UPDATE"]:
                fail(f"{rule['name']}: matches {matches}; expected the shape's Pods in {NS} at CREATE and UPDATE (a pod's images are mutable)")
            selector = resources["selector"]
            if not selects(selector, fixture(shape)["metadata"]["labels"]) or selects(selector, DOWNLOAD_LABELS) \
                    or any(selects(selector, fixture(other)["metadata"]["labels"]) for other in SHAPES if other != shape):
                fail(f"{rule['name']}: the selector {selector} does not select exactly its own shape's fixture pod")
            if rule.get("verifyImages") != [expected]:
                fail(f"{rule['name']}: verifyImages is\n{yaml.safe_dump(rule.get('verifyImages'))}--- expected one entry:\n{yaml.safe_dump(expected)}")
        ok("an installation's own block — a registry of its own, the fleet identity by exact subject next to a public key, the legacy "
           "Cosign format — reaches every rule verbatim: one verifyImages rule per pod shape selects exactly its shape's Pods in the "
           "serving namespace at CREATE and UPDATE, with the references, the type and the attestor entries in one attestor set of "
           "count 1, mutateDigest, required and Enforce")
        knobs = one(render(connectivity, ["-f", image_verification_values(tmp, {"mutateDigest": False, "required": False, "failureAction": "Audit"})]),
                    "ClusterPolicy", IV_SUFFIX)
        for rule in knobs["spec"]["rules"]:
            v = rule["verifyImages"][0]
            if v["mutateDigest"] is not False or v["required"] is not False or v["failureAction"] != "Audit":
                fail(f"{rule['name']}: mutateDigest false, required false and Audit did not reach the rule: {v}")
        ok("mutateDigest, required and failureAction reach every rule as set")
        for shape in SHAPES:
            if apply([policy], fixture(shape)) is not None:
                fail(f"{shape}: the image-verification policy changed a fixture pod none of whose images match the references")
        bogus = copy.deepcopy(policy)
        for rule in bogus["spec"]["rules"]:
            rule["verifyImages"][0]["mutateDigst"] = rule["verifyImages"][0].pop("mutateDigest")
        if loaded_rules([policy], fixture("predictor")) != len(SHAPES) or loaded_rules([bogus], fixture("predictor")) != 0:
            fail("kyverno apply must load every rule of the rendered policy and none of a policy with a misspelt verifyImages field")
        ok("kyverno apply accepts the policy (every rule loaded; a misspelt verifyImages field drops it) and skips both fixture pods, "
           "none of whose images match the references")
        no_kyverno = [flag for i, flag in enumerate(BASE) if flag != "kyverno.io/v1" and not (flag == "--api-versions" and BASE[i + 1] == "kyverno.io/v1")]
        if any(d["metadata"]["name"].endswith(IV_SUFFIX) for d in render(connectivity, ["-f", on], no_kyverno)):
            fail("without kyverno.io/v1 served, the image-verification policy still renders")
        if any(d["metadata"]["name"].endswith(IV_SUFFIX) for d in render(connectivity, ["-f", on, "--set", "modelServing.imageVerification.enabled=false"])):
            fail("modelServing.imageVerification.enabled=false still renders the image-verification policy")
        ok("nothing renders while disabled or without kyverno.io/v1")
        for description, block, needle in (
            ("an empty images list", {"images": []}, "modelServing.imageVerification.images is empty"),
            ("no attestor", {"attestors": []}, "modelServing.imageVerification.attestors is empty"),
            ("an entry that is no Kyverno attestor entry", {"attestors": [{"issuer": "https://oidc.circleci.com"}]}, "modelServing.imageVerification.attestors[0]"),
            ("a type outside SigstoreBundle | Cosign", {"type": "Notary"}, "modelServing.imageVerification.type"),
            ("a failureAction outside Enforce | Audit", {"failureAction": "Deny"}, "modelServing.imageVerification.failureAction"),
            ("an unknown key", {"imageRefrences": IV_IMAGES}, "modelServing.imageVerification.imageRefrences"),
        ):
            result = subprocess.run([HELM, "template", "t", connectivity, *BASE, "-f", image_verification_values(tmp, block)],
                                    capture_output=True, text=True, check=False)
            if result.returncode == 0 or needle not in result.stderr:
                fail(f"{description} must fail the render naming the key ({needle}); got rc={result.returncode}:\n{result.stderr}")
        ok("enabled with an empty images list, no attestor, an entry that is no Kyverno attestor entry, a type outside "
           "SigstoreBundle | Cosign, a failureAction outside Enforce | Audit or an unknown key fails the render naming the key")


def check_image_verification_forwarded(connectivity: str, forwarded: str) -> None:
    """The values the meta chart forwards render the same image-verification policy as the connectivity defaults with the block on (#552)."""
    with tempfile.TemporaryDirectory(prefix="ap-model-serving-iv-") as tmp:
        on = image_verification_values(tmp, {})
        own = one(render(connectivity, ["-f", on]), "ClusterPolicy", IV_SUFFIX)["spec"]
        through = one(render(connectivity, ["-f", forwarded, "-f", on]), "ClusterPolicy", IV_SUFFIX)["spec"]
        if through != own:
            fail(f"the image-verification policy the meta chart's forwarded values render differs from the connectivity default:\n"
                 f"{yaml.safe_dump(through)}\n--- connectivity:\n{yaml.safe_dump(own)}")
    ok("the meta chart's forwarded values render the image-verification policy as the connectivity default does")


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
    check_image_verification(connectivity, docs)
    cilium = render(connectivity, CILIUM)
    check_prepull(connectivity, docs, cilium, pods_policy, exception)
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
        check_prepull_forwarded(connectivity, meta, through_meta[1], tmp)
        check_image_verification_forwarded(connectivity, forwarded)
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit("usage: verify-model-serving-policies.py <connectivity chart dir> <meta chart dir>")
    sys.exit(main(sys.argv[1], sys.argv[2]))
