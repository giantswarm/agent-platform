#!/usr/bin/env python3
"""Assert the serving slice (giantswarm/agent-platform#326; bumblebee-plans#46, D4/D6/D8/D9).

examples/serving-slice.yaml is the values profile of ONE `<cluster>-agent-platform`
release that serves models on a GPU cluster and nothing else. Each case pins one
property the slice relies on:

- the profile renders exactly the serving component set — the five kserve
  releases and the connectivity release; no muster, dicebear, valkey, kagent,
  Backstage, agent-manager, model-manager, no agentgateway (the platform's
  release owns the controller beside it), the engine off;
- components.kserve-runtime-configs: dependsOn kserve-llmisvc-crd, targetNamespace
  kserve (release history there too), llmisvcConfigs on and servingruntime off,
  no registry value (the chart's gsoci default stands), the block held back from
  the connectivity release;
- the derived KServe ingress-gateway value: kserve-resources carries
  kserve.controller.gateway.ingressGateway.kserveGateway = <namespace>/<gateway>,
  a differing copy of the operator's fails the render naming both;
- modelServing.serving.runtimeClassName: nvidia reaches the connectivity release;
- with the target knob (ci/test-target-values.yaml) agentgateway is on and every
  HelmRelease carries the kubeConfig;
- the connectivity chart, rendered with the values the profile forwards: the
  models Gateway (HTTPS listener on models.<global.domain>, the wildcard Secret,
  routes from every namespace, the external-dns hostname), one AgentgatewayPolicy
  on the Gateway (Strict, audiences [dex-k8s-authenticator], inheritance Override,
  the issuer), the JWKS backend at the issuer's host on 443 with TLS, the discovery
  ConfigMap's gateway entry; a cert-manager Certificate only with tls.issuerRef.name;
  nothing of it with modelsGateway.enabled: false; the guards (no issuer, no
  certificate, no audience, a host carrying a port) fail naming the key;
- the two 24 GB presets pass the preset schema's required keys, name no image,
  enable tools with a parser, request one GPU, fit 24 GB, and request no more
  CPU and memory than the smallest L4 instance (a g6.xlarge: 4 vCPU, 16 GiB)
  leaves a predictor after the node's kubelet reservations and daemonsets
  (giantswarm/agent-platform#502) -- the same node model cluster-manager's
  create_node_pool answers with;
- the cache claim (giantswarm/agent-platform#483): no PersistentVolumeClaim object
  in the connectivity render -- a post-install,post-upgrade hook Job server-side
  applies hf-cache into the serving namespace (keep, the access modes, the size,
  the class knob incl. "-", volumeName) as <release>-hooks, whose ClusterRole
  carries get/create/patch on claims; cache.enabled: false and an existing claim
  render neither the hook nor the identity, and the existing claim is published.

Deliberately stdlib-only: the CI image has no PyYAML. HELM selects the binary.
"""

import json
import os
import re
import subprocess
import sys
import tempfile

HELM = os.environ.get("HELM", "helm")
FLEET_APIS = ["--api-versions", "kyverno.io/v1", "--api-versions", "cilium.io/v2", "--api-versions", "monitoring.coreos.com/v1",
              "--api-versions", "gateway.networking.k8s.io/v1", "--api-versions", "gateway.envoyproxy.io/v1alpha1"]
# No ingress.parentRefs: the slice runs no muster, so it needs no edge Gateway
# (#490) — its Gateway is the models Gateway.
VM = ["--set", "kagent.harness.snapshotLocation=s3://ci-agent-snapshots/agents", *FLEET_APIS]
# The installation's platform inputs the profile is layered over.
INSTALLATION = ["--namespace", "agent-platform", "--set", "global.domain=wc01.example.com", "--set", "global.identity.issuerUrl=https://dex.mc.example.com",
                "--set", "gatewayApi.gateway.tls.secretName=wildcard-tls"]
SERVING = {"kserve-crd", "kserve-resources", "kserve-llmisvc-crd", "kserve-llmisvc-resources", "kserve-runtime-configs", "agent-platform-connectivity"}
PRESETS = ("qwen3-4b-instruct", "qwen3-8b-fp8")
# The smallest instance of the presets' accelerator (nvidia-l4 -> g6.xlarge:
# 4 vCPU, 16 GiB) and what a Giant Swarm node of that shape leaves a predictor
# (giantswarm/agent-platform#502): the hypervisor takes ~5 % of the memory, the
# kubelet keeps 0.6 vCPU and ~1.8 GiB (a 4 vCPU / 16 GiB node reports 3.4 vCPU
# / 13.4 GiB allocatable), and the daemonsets that follow the pool's taint
# request ~0.4 vCPU / ~1.5 GiB (Cilium, the exporters, Alloy, the DNS cache,
# the GPU operator's operands). cluster-manager's instance table
# (internal/compose/instances.go) applies the same model to every size.
G6_XLARGE_VCPU, G6_XLARGE_GIB = 4, 16
USABLE_VCPU = G6_XLARGE_VCPU - 1.0
USABLE_GIB = G6_XLARGE_GIB * 0.95 - 3.3
AUDIENCE = "dex-k8s-authenticator"


def helm(chart: str, flags: list[str], expect_failure: str = "") -> str:
    result = subprocess.run([HELM, "template", "t", chart, *flags], capture_output=True, text=True, check=False)
    if expect_failure:
        if result.returncode == 0:
            sys.exit(f"FAIL: render of {chart} {' '.join(flags)} succeeded, a failure naming {expect_failure!r} was expected")
        if expect_failure not in result.stderr:
            sys.exit(f"FAIL: render of {chart} failed without naming {expect_failure!r}:\n{result.stderr}")
        return result.stderr
    if result.returncode != 0:
        sys.exit(f"FAIL: render of {chart} {' '.join(flags)} failed\n{result.stderr}")
    return result.stdout


def documents(manifest: str) -> dict[tuple[str, str], str]:
    docs = {}
    for doc in manifest.split("\n---\n"):
        kind = re.search(r"^kind: (\S+)$", doc, re.M)
        name = re.search(r"^  name: (\S+)$", doc, re.M)
        if kind and name:
            docs[(kind.group(1), name.group(1))] = doc.strip("\n")
    return docs


def releases(manifest: str) -> set[str]:
    return {name for kind, name in documents(manifest) if kind == "HelmRelease"}


def values_block(doc: str) -> str:
    """The `values:` block of a HelmRelease, de-indented to a values file."""
    lines = doc.splitlines()
    for i, line in enumerate(lines):
        if line == "  values:":
            block = []
            for inner in lines[i + 1:]:
                if inner.strip() == "" or len(inner) - len(inner.lstrip()) > 2:
                    block.append(inner[4:] if inner.strip() else "")
                else:
                    break
            return "\n".join(block) + "\n"
    sys.exit("FAIL: the release carries no values block")


def ok(msg: str) -> None:
    print(f"ok: {msg}")


def need(doc: str, needle: str, where: str) -> None:
    if needle not in doc:
        sys.exit(f"FAIL: {where} lacks {needle!r}:\n{doc}")


def check_profile(meta: str) -> str:
    profile = f"{meta}/examples/serving-slice.yaml"
    render = helm(meta, ["-f", profile, *VM, *INSTALLATION])
    if releases(render) != SERVING:
        sys.exit(f"FAIL: the profile renders {sorted(releases(render))}, expected exactly {sorted(SERVING)}")
    docs = documents(render)
    rc = docs[("HelmRelease", "kserve-runtime-configs")]
    for needle in ("    - name: kserve-llmisvc-crd", "      llmisvcConfigs:\n        enabled: true", "      servingruntime:\n        enabled: false"):
        need(rc, needle, "the kserve-runtime-configs release")
    need(rc, "  targetNamespace: agent-platform", "the kserve-runtime-configs release")
    if "storageNamespace" in rc:
        sys.exit("FAIL: the kserve-runtime-configs release targets a namespace of its own; the llm-d controller resolves the well-known configs from the LLMInferenceService's namespace and its own (the release namespace) only")
    if "imageRegistry" in rc:
        sys.exit("FAIL: the kserve-runtime-configs release passes a registry value; the chart's gsoci default stands (giantswarm/kserve#78)")
    need(docs[("HelmRelease", "kserve-resources")], "            kserveGateway: agent-platform/models", "the kserve-resources release")
    conn = docs[("HelmRelease", "agent-platform-connectivity")]
    need(conn, "    runtimeClassName: nvidia", "the connectivity release")
    if "\n    kserve-runtime-configs:\n      kserve:" in conn:
        sys.exit("FAIL: the kserve-runtime-configs block reached the connectivity release (omitKeys)")
    need(conn, "      kserve-runtime-configs:\n        enabled: true", "the connectivity release's roster")
    if "kubeConfig" in render:
        sys.exit("FAIL: the profile without the target knob renders a kubeConfig")
    ok(f"examples/serving-slice.yaml: exactly {len(SERVING)} releases; kserve-runtime-configs after kserve-llmisvc-crd into the release namespace, the llm-d controller's (configs on, runtimes off, no registry, held back from connectivity); kserveGateway derived; runtimeClassName nvidia")

    helm(meta, ["-f", profile, *VM, *INSTALLATION, "--set", "kserve-resources.kserve.controller.gateway.ingressGateway.kserveGateway=other/gw"],
         expect_failure="kserve-resources.kserve.controller.gateway.ingressGateway.kserveGateway (other/gw) differs")
    same = helm(meta, ["-f", profile, *VM, *INSTALLATION, "--set", "kserve-resources.kserve.controller.gateway.ingressGateway.kserveGateway=agent-platform/models"])
    if documents(same)[("HelmRelease", "kserve-resources")] != docs[("HelmRelease", "kserve-resources")]:
        sys.exit("FAIL: an equal kserveGateway copy changed the kserve-resources release")
    off = helm(meta, ["-f", profile, *VM, *INSTALLATION, "--set", "modelServing.modelsGateway.enabled=false"])
    if "kserveGateway" in documents(off)[("HelmRelease", "kserve-resources")]:
        sys.exit("FAIL: kserveGateway derived with modelsGateway.enabled=false")
    ok("kserveGateway: a differing operator copy fails naming both, an equal one is a no-op, no derivation with the Gateway off")

    target = helm(meta, ["-f", profile, "-f", f"{meta}/ci/test-target-values.yaml", *VM, *INSTALLATION])
    t = releases(target)
    if t != SERVING | {"agentgateway"}:
        sys.exit(f"FAIL: the profile with the target knob should add exactly agentgateway: {sorted(t)}")
    if target.count("  kubeConfig:") != len(t) or "      name: wc01-kubeconfig" not in target:
        sys.exit("FAIL: not every HelmRelease of the targeted profile carries the kubeConfig")
    ok("with the target knob: agentgateway on, every HelmRelease carries the kubeConfig")
    return values_block(conn)


def check_ingress_guard(connectivity: str, base: list[str]) -> None:
    """The slice renders without an edge Gateway; muster on keeps the guard (#490)."""
    if ("HTTPRoute", "muster") in documents(helm(connectivity, base)):
        sys.exit("FAIL: the slice renders a muster route")
    helm(connectivity, [*base, "--set", "components.muster.enabled=true"], expect_failure="no public Gateway for ingress.parentRefs")
    helm(connectivity, [*base, "--set", "components.muster.enabled=true", "--set", "components.agentgateway.enabled=true", "--set", "ingress.parentRefs[0].name=x"],
         expect_failure="components.agentgateway.enabled must be false in muster-direct mode")
    helm(connectivity, [*base, "--set", "components.agentgateway.enabled=true", "--set", "ingress.mode=agentgateway-muster"])
    helm(connectivity, [*base, "--set", "ingress.mode=agentgateway-muster"], expect_failure="components.agentgateway.enabled must be true in agentgateway-* modes")
    ok("ingress guard: the slice needs no edge Gateway; with muster on the Gateway and the mode/agentgateway agreement are still required; agentgateway-* modes still need the component")


def check_policy_exception(connectivity: str, base: list[str]) -> None:
    """The predictors' PolicyException (#498): the four restricted-PSS rules the root vLLM image violates, in the serving namespace."""
    docs = documents(helm(connectivity, base))
    pe = docs.get(("PolicyException", "model-serving-predictors"))
    if not pe:
        sys.exit("FAIL: no PolicyException model-serving-predictors: the fleet's restricted PSS policies deny the predictor Deployment")
    for needle in ("  namespace: policy-exceptions", "  - policyName: disallow-capabilities-strict", "      - require-drop-all", "      - autogen-require-drop-all",
                   "  - policyName: disallow-privilege-escalation", "      - autogen-privilege-escalation", "  - policyName: require-run-as-nonroot", "      - autogen-run-as-non-root",
                   "  - policyName: restrict-seccomp-strict", "      - autogen-check-seccomp-strict", "        - model-serving\n", "          - key: serving.kserve.io/inferenceservice", "          - key: kserve.io/component", "          - key: app.kubernetes.io/part-of",
                   "            - llminferenceservice",
                   '        - "*-kserve*"'):
        need(pe, needle, "the predictors' PolicyException")
    if pe.count("- policyName:") != 4:
        sys.exit("FAIL: the predictors' PolicyException names more or fewer than the four policies the predictor violates")
    off = documents(helm(connectivity, [*base, "--set", "kyvernoPolicies.enabled=false", "--set", "modelServing.policies.enabled=false"]))
    if ("PolicyException", "model-serving-predictors") in off:
        sys.exit("FAIL: the predictors' PolicyException rendered with the Kyverno objects off")
    knob = documents(helm(connectivity, [*base, "--set", "modelServing.policyException.enabled=false"]))
    if ("PolicyException", "model-serving-predictors") in knob:
        sys.exit("FAIL: the predictors' PolicyException rendered with modelServing.policyException.enabled=false")
    ok("the predictors' PolicyException: the four restricted-PSS rules with their autogen copies, the serving namespace, both pod shapes' labels (#506) and the name match; none with Kyverno off")


def check_controller_xds(connectivity: str, base: list[str]) -> None:
    """The controller admits xDS from every data plane of its GatewayClass in any namespace (#495)."""
    on = [*base, "--set", "components.agentgateway.enabled=true", "--set", "ingress.mode=agentgateway-muster", "--set", "ingress.parentRefs[0].name=x"]
    cil = documents(helm(connectivity, [*on, "--set", "networkPolicy.flavor=cilium"])).get(("CiliumNetworkPolicy", "agent-platform-connectivity-controller"))
    if not cil:
        sys.exit("FAIL: no CiliumNetworkPolicy agent-platform-connectivity-controller with agentgateway on")
    need(cil, "            gateway.networking.k8s.io/gateway-class-name: agentgateway\n          matchExpressions:\n            - key: k8s:io.kubernetes.pod.namespace\n              operator: Exists", "the controller's xDS peers (cilium)")
    k8s = documents(helm(connectivity, [*on, "--set", "networkPolicy.flavor=kubernetes"])).get(("NetworkPolicy", "agent-platform-connectivity-controller"))
    if not k8s:
        sys.exit("FAIL: no NetworkPolicy agent-platform-connectivity-controller with agentgateway on")
    need(k8s, "        - namespaceSelector: {}\n          podSelector:\n            matchLabels:\n              gateway.networking.k8s.io/gateway-class-name: agentgateway", "the controller's xDS peers (kubernetes)")
    ok("the controller admits xDS from every data plane of its GatewayClass in any namespace, both flavours")


def check_gateway(connectivity: str, base: list[str]) -> None:
    render = helm(connectivity, base)
    docs = documents(render)
    gw = docs.get(("Gateway", "models"))
    if not gw:
        sys.exit("FAIL: no Gateway models in the connectivity render")
    for needle in ("  gatewayClassName: agentgateway", "      protocol: HTTPS", '      hostname: "models.wc01.example.com"', "            name: wildcard-tls", "          from: All",
                   "      external-dns.alpha.kubernetes.io/hostname: models.wc01.example.com", "      giantswarm.io/external-dns: managed",
                   "      kind: AgentgatewayParameters\n      name: models"):
        need(gw, needle, "the models Gateway")
    params = docs.get(("AgentgatewayParameters", "models"))
    if not params:
        sys.exit("FAIL: no AgentgatewayParameters models: the controller's default Deployment carries no seccomp profile and is denied by restrict-seccomp-strict")
    for needle in ("      replicas: 1", "      type: LoadBalancer", "    repository: giantswarm/agentgateway"):
        need(params, needle, "the models data plane parameters")
    if params.count("            seccompProfile:\n              type: RuntimeDefault") + params.count("                seccompProfile:\n                  type: RuntimeDefault") != 2:
        sys.exit(f"FAIL: the models data plane lacks RuntimeDefault seccomp on pod and container:\n{params}")
    for needle in ("            runAsNonRoot: true", "                allowPrivilegeEscalation: false", "                  drop:\n                  - ALL", "                readOnlyRootFilesystem: true"):
        need(params, needle, "the models data plane security context")
    pol = docs.get(("AgentgatewayPolicy", "models-jwt"))
    if not pol:
        sys.exit("FAIL: no AgentgatewayPolicy models-jwt")
    for needle in ("      kind: Gateway\n      name: models", "    inheritance: Override", "      mode: Strict", f"            - {AUDIENCE}", '        - issuer: "https://dex.mc.example.com"',
                   "                name: models-jwks", '              jwksPath: "/keys"'):
        need(pol, needle, "the models JWT policy")
    if sum(1 for (kind, name) in docs if kind == "AgentgatewayPolicy" and name.startswith("models")) != 1:
        sys.exit("FAIL: more than one models policy")
    be = docs.get(("AgentgatewayBackend", "models-jwks"))
    if not be:
        sys.exit("FAIL: no AgentgatewayBackend models-jwks")
    for needle in ("    host: dex.mc.example.com", "    port: 443", "    tls:"):
        need(be, needle, "the JWKS backend")
    if ("Certificate", "models-tls") in docs:
        sys.exit("FAIL: a Certificate rendered without tls.issuerRef.name")
    cm = docs[("ConfigMap", "agent-platform-model-serving")]
    for needle in ("      gateway:\n        enabled: true", "        endpoint: https://models.wc01.example.com", "        pathConvention: /<namespace>/<model>/v1"):
        need(cm, needle, "the discovery ConfigMap")
    ok("connectivity: the models Gateway on models.<domain> with the wildcard, its data plane's parameters (the platform's security contexts, one replica, LoadBalancer), the external-dns hostname and filter, one Strict policy (audience, Override, issuer), the JWKS backend at the issuer on 443/TLS, the discovery entry")

    cert = documents(helm(connectivity, [*base, "--set", "modelServing.modelsGateway.tls.issuerRef.name=platform-ca"]))
    c = cert.get(("Certificate", "models-tls"))
    if not c or "  secretName: models-tls" not in c or "    - models.wc01.example.com" not in c or "    name: platform-ca\n    kind: ClusterIssuer" not in c:
        sys.exit(f"FAIL: the Certificate from tls.issuerRef is wrong:\n{c}")
    need(cert[("Gateway", "models")], "            name: models-tls", "the Gateway with a Certificate")
    ok("tls.issuerRef.name renders a Certificate for the host into models-tls and the listener names it")

    off = documents(helm(connectivity, [*base, "--set", "modelServing.modelsGateway.enabled=false"]))
    if any(name.startswith("models") for kind, name in off if kind in ("Gateway", "AgentgatewayParameters", "AgentgatewayPolicy", "AgentgatewayBackend", "Certificate")):
        sys.exit("FAIL: models Gateway objects rendered with modelsGateway.enabled=false")
    need(off[("ConfigMap", "agent-platform-model-serving")], "      gateway:\n        enabled: false", "the discovery ConfigMap with the Gateway off")
    ok("modelsGateway.enabled: false renders none of it")

    for flags, message in (
        (["--set", "global.identity.issuerUrl="], "global.identity.issuerUrl is empty but modelServing.modelsGateway.jwtAuthentication"),
        (["--set", "gatewayApi.gateway.tls.secretName="], "modelServing.modelsGateway.tls names no certificate"),
        (["--set", "modelServing.modelsGateway.jwtAuthentication.audiences=null"], "modelServing.modelsGateway.jwtAuthentication.audiences is empty"),
        (["--set", "modelServing.modelsGateway.jwtAuthentication.jwks.host=dex.example.com:443"], "modelServing.modelsGateway.jwtAuthentication.jwks.host is"),
    ):
        helm(connectivity, [*base, *flags], expect_failure=message)
    ok("the guards fail naming the key: issuer, certificate, audiences, a host carrying a port")


CACHE_JOB = ("Job", "t-model-serving-cache")
HOOK_IDENTITY = "t-hooks"


def applied_claim(job: str) -> dict:
    """The claim the hook applies: the one JSON line the script pipes into kubectl."""
    m = re.search(r"printf '%s' '(\{.*\})' \\$", job, re.M)
    if not m:
        sys.exit(f"FAIL: the cache claim hook pipes no JSON claim into kubectl:\n{job}")
    return json.loads(m.group(1))


def check_cache(connectivity: str, base: list[str]) -> None:
    render = helm(connectivity, base)
    docs = documents(render)
    if any(kind == "PersistentVolumeClaim" for kind, _ in docs):
        sys.exit("FAIL: the cache claim rendered as a release resource; Helm's wait would wait for a Bind only the first predictor brings (#483)")
    job = docs.get(CACHE_JOB)
    if not job:
        sys.exit(f"FAIL: no hook Job {CACHE_JOB[1]} in the connectivity render")
    for needle in ("    helm.sh/hook: post-install,post-upgrade", "    helm.sh/hook-delete-policy: before-hook-creation,hook-succeeded", f"      serviceAccountName: {HOOK_IDENTITY}",
                   "kubectl apply --server-side --force-conflicts --field-manager=agent-platform-connectivity -f -", 'then state=present; else state=created; fi'):
        need(job, needle, "the cache claim hook")
    claim = applied_claim(job)
    meta, spec = claim["metadata"], claim["spec"]
    if (claim["kind"], meta["name"], meta["namespace"]) != ("PersistentVolumeClaim", "hf-cache", "model-serving"):
        sys.exit(f"FAIL: the hook applies {claim['kind']} {meta.get('namespace')}/{meta.get('name')}, not the claim hf-cache in model-serving")
    if meta["annotations"].get("helm.sh/resource-policy") != "keep" or meta["labels"].get("app.kubernetes.io/component") != "model-serving":
        sys.exit(f"FAIL: the applied claim lacks the keep policy or the model-serving component label:\n{json.dumps(meta, indent=1)}")
    if spec != {"accessModes": ["ReadWriteOnce"], "resources": {"requests": {"storage": "500Gi"}}}:
        sys.exit(f"FAIL: the applied claim's default spec is off (RWO, 500Gi, the cluster's default class, no volumeName expected):\n{json.dumps(spec, indent=1)}")
    role = docs.get(("ClusterRole", HOOK_IDENTITY))
    if not role or ("ServiceAccount", HOOK_IDENTITY) not in docs or ("ClusterRoleBinding", HOOK_IDENTITY) not in docs:
        sys.exit(f"FAIL: the hook identity {HOOK_IDENTITY} (ServiceAccount, ClusterRole, ClusterRoleBinding) is incomplete")
    need(role, '    resources: ["persistentvolumeclaims"]\n    verbs: ["get", "create", "patch"]', "the hook identity's ClusterRole")
    ok("the cache claim: no PersistentVolumeClaim object; a post-install,post-upgrade hook Job server-side applies hf-cache into model-serving (keep, RWO, 500Gi, the default class) as t-hooks, whose ClusterRole carries get/create/patch on claims and never delete")

    knobs = documents(helm(connectivity, [*base, "--set", "modelServing.cache.pvc.storageClassName=gp3", "--set", "modelServing.cache.pvc.size=1Ti", "--set", "modelServing.cache.pvc.volumeName=nvme-0",
                                          "--set", "modelServing.cache.pvc.accessModes[0]=ReadWriteMany"]))
    spec = applied_claim(knobs[CACHE_JOB])["spec"]
    if spec != {"accessModes": ["ReadWriteMany"], "resources": {"requests": {"storage": "1Ti"}}, "storageClassName": "gp3", "volumeName": "nvme-0"}:
        sys.exit(f"FAIL: the class, size, volumeName and access-mode knobs did not reach the applied claim:\n{json.dumps(spec, indent=1)}")
    dash = applied_claim(documents(helm(connectivity, [*base, "--set", "modelServing.cache.pvc.storageClassName=-"]))[CACHE_JOB])["spec"]
    if dash.get("storageClassName") != "":
        sys.exit(f"FAIL: storageClassName \"-\" should apply the empty class (static binding):\n{json.dumps(dash, indent=1)}")
    ok('storageClassName, size, volumeName and accessModes reach the applied claim; "-" is the empty class')

    for flags, label in ((["--set", "modelServing.cache.enabled=false"], "cache.enabled: false"), (["--set", "modelServing.cache.pvc.existingClaim=models"], "an existing claim")):
        text = helm(connectivity, [*base, *flags])
        off = documents(text)
        if "PersistentVolumeClaim" in text or CACHE_JOB in off or any(name == HOOK_IDENTITY for _, name in off):
            sys.exit(f"FAIL: with {label} the render still carries the claim, its hook or the hook identity (the slice has no other hook): {sorted(k for k in off if k == CACHE_JOB or k[1] == HOOK_IDENTITY)}")
        if label == "an existing claim" and "claimName: models" not in text:
            sys.exit("FAIL: the existing claim is not published")
    ok("cache.enabled: false and an existing claim render no claim, no hook and no hook identity; the existing claim is published")


def check_presets(connectivity: str) -> None:
    schema = json.load(open(f"{connectivity}/files/model-serving/serving-preset.schema.json"))
    spec_keys = set(schema["properties"]["spec"]["properties"])
    for name in PRESETS:
        text = open(f"{connectivity}/files/model-serving/presets/{name}.yaml").read()
        need(text, f"  name: {name}", name)
        for key in schema["properties"]["spec"]["required"]:
            need(text, f"  {key}:", f"preset {name}")
        for key in re.findall(r"^  ([a-zA-Z]+):", text.split("\nspec:\n", 1)[1], re.M):
            if key not in spec_keys:
                sys.exit(f"FAIL: preset {name} carries spec.{key}, not in the schema")
        if re.search(r"image", text, re.I) and re.search(r"^\s+image\w*:", text, re.M):
            sys.exit(f"FAIL: preset {name} names an image; the well-known config's llm-d-cuda serves")
        for needle in ("    - --enable-auto-tool-choice", "    - --tool-call-parser=hermes", "    gpus: 1", "    capabilities: [chat, tools]"):
            need(text, needle, f"preset {name}")
        w = float(re.search(r"weightsGiB: ([\d.]+)", text).group(1))
        o = float(re.search(r"overheadGiB: ([\d.]+)", text).group(1))
        if w + o > 24:
            sys.exit(f"FAIL: preset {name} needs {w + o} GiB, more than a 24 GB GPU")
        cpu, mem = resource_requests(text, name)
        if cpu > USABLE_VCPU or mem > USABLE_GIB:
            sys.exit(f"FAIL: preset {name} requests {cpu:g} vCPU / {mem:g} GiB; a g6.xlarge leaves a predictor {USABLE_VCPU:g} vCPU / {USABLE_GIB:.1f} GiB "
                     "after the kubelet's reservations and the daemonsets (giantswarm/agent-platform#502)")
        if "g6.xlarge" not in text:
            sys.exit(f"FAIL: preset {name}'s description does not name the instance it is sized for (g6.xlarge)")
    if "template" not in spec_keys:
        sys.exit("FAIL: the preset schema has no spec.template")
    ok(f"presets {', '.join(PRESETS)}: schema keys, no image, tools on with a parser, one GPU, <= 24 GiB, "
       f"requests within a g6.xlarge's {USABLE_VCPU:g} vCPU / {USABLE_GIB:.1f} GiB; the schema knows spec.template")


def resource_requests(text: str, name: str) -> tuple:
    """The preset's requests.cpu (vCPU) and requests.memory (GiB), from the
    authoring form's `resources:` block (quantities as Kubernetes writes them)."""
    block = re.search(r"^  resources:\n((?:    .*\n)+)", text, re.M)
    if not block:
        sys.exit(f"FAIL: preset {name} carries no resources block")
    requests = re.search(r"^    requests:\n((?:      .*\n)+)", block.group(1), re.M)
    if not requests:
        sys.exit(f"FAIL: preset {name} carries no resources.requests")
    cpu = re.search(r'^      cpu: "?([\d.]+)(m?)"?$', requests.group(1), re.M)
    mem = re.search(r"^      memory: ([\d.]+)(Gi|Mi)$", requests.group(1), re.M)
    if not cpu or not mem:
        sys.exit(f"FAIL: preset {name}'s requests name no cpu (a count or millicores) or no memory (Gi or Mi)")
    vcpu = float(cpu.group(1)) / (1000 if cpu.group(2) == "m" else 1)
    gib = float(mem.group(1)) / (1024 if mem.group(2) == "Mi" else 1)
    return vcpu, gib


def main(meta: str, connectivity: str) -> int:
    forwarded = check_profile(meta)
    with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as f:
        f.write(forwarded)
        values = f.name
    try:
        base = ["-f", values, "--namespace", "agent-platform", *FLEET_APIS]
        check_ingress_guard(connectivity, base)
        check_policy_exception(connectivity, base)
        check_controller_xds(connectivity, base)
        check_gateway(connectivity, base)
        check_cache(connectivity, base)
    finally:
        os.unlink(values)
    check_presets(connectivity)
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit("usage: verify-serving-slice.py <meta chart dir> <connectivity chart dir>")
    sys.exit(main(sys.argv[1], sys.argv[2]))
