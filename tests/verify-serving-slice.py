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
  the llm-d-fast/ prefix as imageRegistry — the same prefix the pre-pull's
  llm-d-cuda reference carries (#568) — the block held back from the
  connectivity release; spec.driftDetection.mode: enabled by default, so a
  well-known config that went missing is re-created on the release's next
  reconcile (#508), with the configs' /spec ignored (KServe defaults fields
  into it after the apply; correcting them kept a fresh install from coming
  back Ready), and no other release of the slice carries drift detection;
- the derived KServe ingress-gateway value: kserve-llmisvc-resources carries
  kserve.controller.gateway.ingressGateway.kserveGateway = <namespace>/<gateway>,
  a differing copy of the operator's fails the render naming both;
- modelServing.serving.runtimeClassName: nvidia reaches the connectivity release;
- with the target knob (ci/test-target-values.yaml) agentgateway is on and every
  HelmRelease carries the kubeConfig;
- the controller policy of the slice on a workload cluster (components.agentgateway
  on, no ingress mode): rendered in both flavours and admitting the issuer's JWKS
  host on 443 — the agentgateway controller fetches the key set (#505); beside the
  platform's release none;
- the connectivity chart, rendered with the values the profile forwards: the
  models Gateway (HTTPS listener on models.<global.domain>, the wildcard Secret,
  routes from every namespace, the external-dns hostname), one AgentgatewayPolicy
  on the Gateway (Strict, audiences [dex-k8s-authenticator], inheritance Override,
  the issuer), the JWKS backend at the issuer's host on 443 with TLS, the discovery
  ConfigMap's gateway entry; a cert-manager Certificate only with tls.issuerRef.name;
  nothing of it with modelsGateway.enabled: false; the guards (no issuer, no
  certificate, no audience, a host carrying a port) fail naming the key;
- every shipped preset's resources.gpus equals the tensor-parallel size its arguments
  set (1 without the flag), and its arguments survive the runtime template's entrypoint
  (eval "… $@" re-parses them through a shell): the exact eval, run over each
  preset's args, yields one word per argument, a JSON value parses; a values
  preset with a bare JSON, a space, a stray quote or a metacharacter fails the
  render naming the guard (giantswarm/agent-platform#532)
- the four 24 GB presets (the September 2026 line-up, giantswarm/agent-platform#591)
  pass the preset schema's required keys, are served from a signed model image
  and name no serving image, enable tools and reasoning with their parsers,
  request one GPU, fit 24 GB, and request no more
  CPU and memory than the smallest L4 instance (a g6.xlarge: 4 vCPU, 16 GiB)
  leaves a predictor after the node's kubelet reservations and daemonsets
  (giantswarm/agent-platform#502) -- the same node model cluster-manager's
  create_node_pool answers with;
- the cache claim (giantswarm/agent-platform#483): no PersistentVolumeClaim object
  in the connectivity render -- a post-install,post-upgrade hook Job server-side
  applies hf-cache into the serving namespace (keep, the access modes, the size,
  the class, volumeName) as <release>-hooks, whose ClusterRole carries
  get/create/patch on claims; cache.enabled: false and an existing claim render
  neither the hook nor the claim rule, and the existing claim is published; the
  identity then stays for the pre-pull DaemonSet's pre-delete cleanup Job alone
  (giantswarm/agent-platform#563) and goes with prepull.enabled: false.
- what outlives the release (giantswarm/agent-platform#537, #565): the serving
  namespace carries helm.sh/resource-policy: keep with the cache on and off (a
  namespace kept only with the cache on took a claim an earlier slice left there
  down with it) and not with namespace.keep: false; namespace.create: false
  renders none; the claim references the chart's own StorageClass
  (<chart>-<claim>-<digest>: the EBS CSI provisioner, gp3 at 500 MiB/s / 3000 IOPS as
  strings, WaitForFirstConsumer, expansion allowed, Delete, no keep policy);
  storageClass.create: false with a name references that name and renders no
  class, without a name leaves the claim on the cluster's default; a name and
  parameters reach the rendered class; pvc.storageClassName next to
  storageClass.create: true is refused naming both; cache.enabled: false and an
  existing claim render no class.

Deliberately stdlib-only: the CI image has no PyYAML. HELM selects the binary.
"""

import glob
import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
import tempfile

import yaml

HELM = os.environ.get("HELM", "helm")
FLEET_APIS = ["--api-versions", "kyverno.io/v1", "--api-versions", "cilium.io/v2", "--api-versions", "monitoring.coreos.com/v1",
              "--api-versions", "gateway.networking.k8s.io/v1", "--api-versions", "gateway.envoyproxy.io/v1alpha1"]
# No ingress.parentRefs: the slice runs no muster, so it needs no edge Gateway
# (#490) — its Gateway is the models Gateway.
VM = ["--set", "kagent.harness.snapshotLocation=s3://ci-agent-snapshots/agents", *FLEET_APIS]
# The installation's platform inputs the profile is layered over.
INSTALLATION = ["--namespace", "agent-platform", "--set", "global.domain=wc01.example.com", "--set", "global.identity.issuerUrl=https://dex.mc.example.com",
                "--set", "gatewayApi.gateway.tls.secretName=wildcard-tls"]
SERVING = {"kserve-llmisvc-crd", "kserve-llmisvc-resources", "kserve-runtime-configs", "agent-platform-connectivity"}
# The prefix the slice passes to the well-known configs as imageRegistry (#568): the
# re-layered llm-d-fast/ set. The pre-pull's runtime image carries the same prefix.
FAST_PREFIX = "gsoci.azurecr.io/giantswarm/llm-d-fast/"
# The 24 GB presets of the September 2026 line-up (giantswarm/agent-platform#591),
# each served from a signed model image.
PRESETS = ("gpt-oss-20b", "gemma-4-12b", "qwen3-5-9b-fp8", "qwen3-5-4b")
MODEL_IMAGES = "oci://gsoci.azurecr.io/giantswarm/models/"
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
    need(rc, f"      llmisvcConfigs:\n        enabled: true\n        imageRegistry: {FAST_PREFIX}", "the kserve-runtime-configs release")
    need(rc, "  driftDetection:\n    ignore:\n    - paths:\n      - /spec\n      target:\n        kind: LLMInferenceServiceConfig\n    mode: enabled\n", "the kserve-runtime-configs release (a missing well-known config is re-created on the next reconcile; the spec KServe defaults after the apply is not drift, or a fresh install never comes back Ready, #508)")
    drifting = sorted(name for (kind, name), doc in docs.items() if kind == "HelmRelease" and name != "kserve-runtime-configs" and "\n  driftDetection:" in doc)
    if drifting:
        sys.exit(f"FAIL: {drifting} carry spec.driftDetection in the slice; drift detection is decided per release, kserve-runtime-configs only here")
    need(docs[("HelmRelease", "kserve-llmisvc-resources")], "            kserveGateway: agent-platform/models", "the kserve-llmisvc-resources release")
    need(docs[("HelmRelease", "kserve-llmisvc-resources")], "      createSharedResources: true", "the kserve-llmisvc-resources release (the control plane's shared objects are its own)")
    need(docs[("HelmRelease", "kserve-llmisvc-resources")], "        deploymentMode: Standard", "the kserve-llmisvc-resources release")
    for classic in ("kserve-crd", "kserve-resources"):
        if ("HelmRelease", classic) in docs or ("OCIRepository", classic) in docs:
            sys.exit(f"FAIL: the profile renders the classic {classic} release; the classic InferenceService path was removed (giantswarm/agent-platform#574)")
    conn = docs[("HelmRelease", "agent-platform-connectivity")]
    need(conn, "    runtimeClassName: nvidia", "the connectivity release")
    if "\n    kserve-runtime-configs:\n      kserve:" in conn:
        sys.exit("FAIL: the kserve-runtime-configs block reached the connectivity release (omitKeys)")
    need(conn, "      kserve-runtime-configs:\n        enabled: true", "the connectivity release's roster")
    if "kubeConfig" in render:
        sys.exit("FAIL: the profile without the target knob renders a kubeConfig")
    ok(f"examples/serving-slice.yaml: exactly {len(SERVING)} releases; kserve-runtime-configs after kserve-llmisvc-crd into the release namespace, the llm-d controller's (configs on, runtimes off, the llm-d-fast/ prefix as imageRegistry, held back from connectivity, drift detection on and on no other release); kserveGateway derived; runtimeClassName nvidia")

    helm(meta, ["-f", profile, *VM, *INSTALLATION, "--set", "kserve-llmisvc-resources.kserve.controller.gateway.ingressGateway.kserveGateway=other/gw"],
         expect_failure="kserve-llmisvc-resources.kserve.controller.gateway.ingressGateway.kserveGateway (other/gw) differs")
    same = helm(meta, ["-f", profile, *VM, *INSTALLATION, "--set", "kserve-llmisvc-resources.kserve.controller.gateway.ingressGateway.kserveGateway=agent-platform/models"])
    if documents(same)[("HelmRelease", "kserve-llmisvc-resources")] != docs[("HelmRelease", "kserve-llmisvc-resources")]:
        sys.exit("FAIL: an equal kserveGateway copy changed the kserve-llmisvc-resources release")
    off = helm(meta, ["-f", profile, *VM, *INSTALLATION, "--set", "modelServing.modelsGateway.enabled=false"])
    if "kserveGateway" in documents(off)[("HelmRelease", "kserve-llmisvc-resources")]:
        sys.exit("FAIL: kserveGateway derived with modelsGateway.enabled=false")
    helm(meta, ["-f", profile, *VM, *INSTALLATION, "--set", "components.kserve-resources.enabled=true"],
         expect_failure="its keys are refused: components.kserve-resources")
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
    """The model pods' PolicyException (#498): the four restricted-PSS rules the root vLLM image violates, in the serving namespace."""
    docs = documents(helm(connectivity, base))
    pe = docs.get(("PolicyException", "model-serving-predictors"))
    if not pe:
        sys.exit("FAIL: no PolicyException model-serving-predictors: the fleet's restricted PSS policies deny the predictor Deployment")
    for needle in ("  namespace: policy-exceptions", "  - policyName: disallow-capabilities-strict", "      - require-drop-all", "      - autogen-require-drop-all",
                   "  - policyName: disallow-privilege-escalation", "      - autogen-privilege-escalation", "  - policyName: require-run-as-nonroot", "      - autogen-run-as-non-root",
                   "  - policyName: restrict-seccomp-strict", "      - autogen-check-seccomp-strict", "        - model-serving\n", "          - key: kserve.io/component", "          - key: app.kubernetes.io/part-of",
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
    if "serving.kserve.io/inferenceservice" in pe:
        sys.exit("FAIL: the PolicyException still selects the classic predictor's label (giantswarm/agent-platform#574)")
    ok("the model pods' PolicyException: the four restricted-PSS rules with their autogen copies, the serving namespace, the workload pod's labels (#506) and the name match; none with Kyverno off")


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


def check_controller_jwks_egress(connectivity: str, base: list[str]) -> None:
    """The controller reaches the models policy's JWKS on a workload cluster (#505).

    The agentgateway controller fetches the JWKS of every JWT policy and pushes the
    keys to the data plane over xDS; a fetch its network policy denies is an empty
    key set and `401 token uses the unknown key` for every caller. On a workload
    cluster the slice's own release runs the controller (components.agentgateway on,
    ingress.mode at its muster-direct default, no edge Gateway), so its controller
    policy must render there and admit the issuer's host on 443: a toFQDNs matchName
    behind the DNS proxy clause in the cilium flavour, port 443 of the wide rule in
    the kubernetes flavour. Beside the platform's release (the component off) the
    slice renders no controller policy — the platform's release owns it and admits
    the issuer itself (Makefile.custom.mk verify-wiring).
    """
    on = [*base, "--set", "components.agentgateway.enabled=true"]
    cil = documents(helm(connectivity, [*on, "--set", "networkPolicy.flavor=cilium"])).get(("CiliumNetworkPolicy", "agent-platform-connectivity-controller"))
    if not cil:
        sys.exit("FAIL: no CiliumNetworkPolicy agent-platform-connectivity-controller for the slice on a workload cluster (components.agentgateway on, ingress.mode muster-direct): the controller runs there without a policy that admits the issuer")
    need(cil, '        - matchName: "dex.mc.example.com"\n      toPorts:\n        - ports:\n            - port: "443"', "the controller's egress to the issuer's JWKS (cilium)")
    need(cil, '          rules:\n            dns:\n              - matchPattern: "*"', "the DNS proxy clause the toFQDNs selector needs (cilium)")
    own = documents(helm(connectivity, [*on, "--set", "networkPolicy.flavor=cilium", "--set", "modelServing.modelsGateway.jwtAuthentication.jwks.host=keys.other.example.com"]))[("CiliumNetworkPolicy", "agent-platform-connectivity-controller")]
    for host in ("dex.mc.example.com", "keys.other.example.com"):
        need(own, f'        - matchName: "{host}"\n      toPorts:\n        - ports:\n            - port: "443"', f"the controller's egress to {host} with an own jwks.host")
    k8s = documents(helm(connectivity, [*on, "--set", "networkPolicy.flavor=kubernetes"])).get(("NetworkPolicy", "agent-platform-connectivity-controller"))
    if not k8s:
        sys.exit("FAIL: no NetworkPolicy agent-platform-connectivity-controller for the slice on a workload cluster (kubernetes flavour)")
    need(k8s, "            cidr: 0.0.0.0/0", "the controller's wide egress rule (kubernetes)")
    need(k8s[k8s.index("cidr: 0.0.0.0/0"):], "        - port: 443\n          protocol: TCP", "port 443 on the controller's wide egress rule (kubernetes)")
    for flavor, kind in (("cilium", "CiliumNetworkPolicy"), ("kubernetes", "NetworkPolicy")):
        if (kind, "agent-platform-connectivity-controller") in documents(helm(connectivity, [*base, "--set", f"networkPolicy.flavor={flavor}"])):
            sys.exit(f"FAIL: the slice beside the platform's release (components.agentgateway off) rendered a controller policy ({flavor}); the platform's release owns it")
    ok("the slice on a workload cluster renders the controller policy without an ingress mode and admits the issuer's JWKS host on 443 (toFQDNs + DNS proxy; the wide rule's 443), an own jwks.host beside it; beside the platform none")


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
    # The models data plane runs the release of the agentgateway line the
    # platform's data planes run: agentgateway.proxy.image of the connectivity
    # values, read here so the pin moves in one place.
    proxy = re.search(r"^  proxy:\n    image:\n(?:      \w+: .*\n)*?      repository: (\S+)\n      tag: \"?([^\"\n]+)\"?", open(f"{connectivity}/values.yaml", encoding="utf-8").read(), re.M)
    if not proxy:
        sys.exit("FAIL: agentgateway.proxy.image (repository, tag) not found in the connectivity values")
    for needle in ("      replicas: 1", "      type: LoadBalancer", f"    repository: {proxy.group(1)}", f"    tag: {proxy.group(2)}"):
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
# The pre-pull DaemonSet's pre-delete cleanup Job (#563): the one hook left with the cache off.
PREPULL_CLEANUP = ("Job", "t-model-serving-prepull-cleanup")
HOOK_IDENTITY = "t-hooks"
SERVING_NS = ("Namespace", "model-serving")
# The claim's StorageClass, named after the chart (cluster-scoped), the claim and a digest of
# provisioner and parameters (#537, #570) -- both immutable on the API, so a parameter change is a new class.
CACHE_PROVISIONER, CACHE_PARAMETERS = "ebs.csi.aws.com", {"type": "gp3", "iops": "3000", "throughput": "500"}


def class_digest(provisioner: str, parameters: dict) -> str:
    """The eight hex characters a default class name carries: sha256 of "<provisioner>;<key>=<value>;..." over the parameters sorted by key."""
    spec = provisioner + "".join(f";{k}={v}" for k, v in sorted(parameters.items()))
    return hashlib.sha256(spec.encode()).hexdigest()[:8]


CACHE_CLASS = ("StorageClass", f"agent-platform-connectivity-hf-cache-{class_digest(CACHE_PROVISIONER, CACHE_PARAMETERS)}")
NO_CLASS = ["--set", "modelServing.cache.storageClass.create=false"]
# The keep annotation as rendered (the templates' comments name the policy too).
KEEP = "    helm.sh/resource-policy: keep"


# A stub kubectl for the hook's script (#570): STUB_SIZE describes an existing claim (empty: no claim), STUB_PHASE its phase,
# STUB_CLASS its storageClassName (the variable unset: the claim carries no such key; empty: the empty class), STUB_SC_EXISTS
# whether `get storageclass` finds the class, STUB_APPLIED where the applied manifest lands.
STUB_KUBECTL = """#!/bin/sh
case "$*" in
  *"get pvc"*"{.spec.resources.requests.storage}"*) printf '%s' "$STUB_SIZE" ;;
  *"get pvc"*"{.status.phase}"*) printf '%s' "$STUB_PHASE" ;;
  *"get pvc"*"{.spec.storageClassName}"*"--allow-missing-template-keys=false"*)
    [ -n "${STUB_CLASS+x}" ] || { echo 'error: error executing jsonpath "{.spec.storageClassName}": storageClassName is not found' >&2; exit 1; }
    printf '%s' "$STUB_CLASS" ;;
  *"get pvc"*) [ -n "$STUB_SIZE" ] || exit 1 ;;
  *"get storageclass"*) [ "$STUB_SC_EXISTS" = 1 ] || exit 1 ;;
  *"apply"*) cat > "$STUB_APPLIED" ;;
  *) echo "stub kubectl: unexpected $*" >&2; exit 9 ;;
esac
"""


def run_hook(job: str, phase: str = "", size: str = "", storage_class: str | None = None, class_exists: bool = True) -> tuple[int, dict | None, str, str]:
    """Run the hook Job's script against the stub kubectl: (rc, the applied manifest or None, stdout, stderr). size "" is no existing claim."""
    script = yaml.safe_load(job)["spec"]["template"]["spec"]["containers"][0]["args"][0]
    with tempfile.TemporaryDirectory() as d:
        stub = os.path.join(d, "kubectl")
        with open(stub, "w", encoding="utf-8") as f:
            f.write(STUB_KUBECTL)
        os.chmod(stub, 0o755)
        applied = os.path.join(d, "applied.json")
        env = {k: v for k, v in os.environ.items() if not k.startswith("STUB_")}
        env.update(PATH=f"{d}:{env['PATH']}", STUB_APPLIED=applied, STUB_PHASE=phase, STUB_SIZE=size, STUB_SC_EXISTS="1" if class_exists else "0")
        if storage_class is not None:
            env["STUB_CLASS"] = storage_class
        result = subprocess.run(["sh", "-eu", "-c", script], env=env, capture_output=True, text=True, check=False)
        manifest = None
        if os.path.exists(applied):
            with open(applied, encoding="utf-8") as f:
                manifest = json.load(f)
    return result.returncode, manifest, result.stdout, result.stderr


def applied_claim(job: str) -> dict:
    """The claim the hook applies where none exists yet: the script run against the stub kubectl."""
    rc, manifest, out, err = run_hook(job)
    if rc != 0 or not manifest or "created" not in out:
        sys.exit(f"FAIL: the cache claim hook script does not apply the claim where none exists (rc={rc}):\n{out}\n{err}")
    return manifest


def check_cache(connectivity: str, base: list[str]) -> None:
    render = helm(connectivity, base)
    docs = documents(render)
    if any(kind == "PersistentVolumeClaim" for kind, _ in docs):
        sys.exit("FAIL: the cache claim rendered as a release resource; Helm's wait would wait for a Bind only the first predictor brings (#483)")
    job = docs.get(CACHE_JOB)
    if not job:
        sys.exit(f"FAIL: no hook Job {CACHE_JOB[1]} in the connectivity render")
    for needle in ("    helm.sh/hook: post-install,post-upgrade", "    helm.sh/hook-delete-policy: before-hook-creation,hook-succeeded", f"      serviceAccountName: {HOOK_IDENTITY}",
                   "kubectl apply --server-side --force-conflicts --field-manager=agent-platform-connectivity -f -", "state=present", "state=created",
                   "--allow-missing-template-keys=false", "a claim never shrinks"):
        need(job, needle, "the cache claim hook")
    claim = applied_claim(job)
    meta, spec = claim["metadata"], claim["spec"]
    if (claim["kind"], meta["name"], meta["namespace"]) != ("PersistentVolumeClaim", "hf-cache", "model-serving"):
        sys.exit(f"FAIL: the hook applies {claim['kind']} {meta.get('namespace')}/{meta.get('name')}, not the claim hf-cache in model-serving")
    if meta["annotations"].get("helm.sh/resource-policy") != "keep" or meta["labels"].get("app.kubernetes.io/component") != "model-serving":
        sys.exit(f"FAIL: the applied claim lacks the keep policy or the model-serving component label:\n{json.dumps(meta, indent=1)}")
    if spec != {"accessModes": ["ReadWriteOnce"], "resources": {"requests": {"storage": "100Gi"}}, "storageClassName": CACHE_CLASS[1]}:
        sys.exit(f"FAIL: the applied claim's default spec is off (RWO, 100Gi, the chart's class {CACHE_CLASS[1]}, no volumeName expected):\n{json.dumps(spec, indent=1)}")
    role = docs.get(("ClusterRole", HOOK_IDENTITY))
    if not role or ("ServiceAccount", HOOK_IDENTITY) not in docs or ("ClusterRoleBinding", HOOK_IDENTITY) not in docs:
        sys.exit(f"FAIL: the hook identity {HOOK_IDENTITY} (ServiceAccount, ClusterRole, ClusterRoleBinding) is incomplete")
    need(role, '    resources: ["persistentvolumeclaims"]\n    verbs: ["get", "create", "patch"]', "the hook identity's ClusterRole")
    ok(f"the cache claim: no PersistentVolumeClaim object; a post-install,post-upgrade hook Job server-side applies hf-cache into model-serving (keep, RWO, 100Gi, the chart's class {CACHE_CLASS[1]}) as t-hooks, whose ClusterRole carries get/create/patch on claims and never delete on them")

    ns = docs.get(SERVING_NS)
    if not ns:
        sys.exit("FAIL: no serving namespace in the connectivity render")
    need(ns, KEEP, "the serving namespace with the cache on")
    cls = docs.get(CACHE_CLASS)
    if not cls:
        sys.exit(f"FAIL: no StorageClass {CACHE_CLASS[1]} in the connectivity render; the claim references it")
    for needle in ('provisioner: "ebs.csi.aws.com"', '  type: "gp3"', '  iops: "3000"', '  throughput: "500"', "volumeBindingMode: WaitForFirstConsumer",
                   "allowVolumeExpansion: true", "reclaimPolicy: Delete", "app.kubernetes.io/component: model-serving"):
        need(cls, needle, "the claim's StorageClass")
    if KEEP in cls:
        sys.exit(f"FAIL: the claim's StorageClass carries a resource policy; the claim is the durable object, the class goes with the release:\n{cls}")
    ok(f"with the cache on the serving namespace is kept (helm.sh/resource-policy: keep) and the claim's StorageClass {CACHE_CLASS[1]} renders: ebs.csi.aws.com, gp3 at 500 MiB/s / 3000 IOPS as strings, WaitForFirstConsumer, expansion allowed, Delete, Helm-owned; its name carries the digest of provisioner and parameters")

    # An existing claim keeps its class and its size (#570): the hook's script against the stub kubectl.
    old = "agent-platform-connectivity-hf-cache"
    rc, kept, out, err = run_hook(job, "Bound", "500Gi", old)
    if rc != 0 or kept["spec"] != {"accessModes": ["ReadWriteOnce"], "resources": {"requests": {"storage": "500Gi"}}, "storageClassName": old} \
            or "present" not in out or "size 500Gi kept" not in out or f"class {old} kept" not in out:
        sys.exit(f"FAIL: a Bound 500Gi claim on the former class should be applied as it is, the log naming what was kept (rc={rc}):\n{json.dumps(kept, indent=1)}\n{out}\n{err}")
    rc, grown, out, err = run_hook(job, "Bound", "50Gi", CACHE_CLASS[1])
    if rc != 0 or grown["spec"]["resources"]["requests"]["storage"] != "100Gi" or grown["spec"]["storageClassName"] != CACHE_CLASS[1] or "grown from 50Gi to 100Gi" not in out:
        sys.exit(f"FAIL: a smaller claim on the rendered class should be grown to the rendered size (rc={rc}):\n{json.dumps(grown, indent=1)}\n{out}\n{err}")
    rc, same, out, err = run_hook(job, "Bound", "107374182400", CACHE_CLASS[1])
    if rc != 0 or same["spec"]["resources"]["requests"]["storage"] != "107374182400" or "kept" in out or "grown" in out:
        sys.exit(f"FAIL: an equal size in another spelling should be applied as it is and reported as nothing (rc={rc}):\n{json.dumps(same, indent=1)}\n{out}\n{err}")
    rc, pending, out, err = run_hook(job, "Pending", "500Gi", old, class_exists=False)
    if rc == 0 or pending is not None or "cannot bind" not in err or f"delete pvc hf-cache" not in err or CACHE_CLASS[1] not in err:
        sys.exit(f"FAIL: a Pending claim on a class the cluster lacks should fail the hook naming the claim, the way out and the rendered class (rc={rc}):\n{out}\n{err}")
    rc, other, out, err = run_hook(job, "Pending", "100Gi", "gp3")
    if rc != 0 or other["spec"]["storageClassName"] != "gp3" or "class gp3 kept" not in out:
        sys.exit(f"FAIL: a Pending claim on a class the cluster has should keep it (rc={rc}):\n{json.dumps(other, indent=1)}\n{out}\n{err}")
    rc, none, out, err = run_hook(job, "Bound", "1Ti", None)
    if rc != 0 or "storageClassName" in none["spec"] or none["spec"]["resources"]["requests"]["storage"] != "1Ti" or "no class kept" not in out:
        sys.exit(f"FAIL: a claim without a storageClassName should be applied without one, its 1Ti kept (rc={rc}):\n{json.dumps(none, indent=1)}\n{out}\n{err}")
    rc, empty, out, err = run_hook(job, "Bound", "100Gi", "")
    if rc != 0 or empty["spec"].get("storageClassName") != "" or "the empty class kept" not in out:
        sys.exit(f"FAIL: a claim on the empty class should keep it (rc={rc}):\n{json.dumps(empty, indent=1)}\n{out}\n{err}")
    ok("an existing claim keeps its class and its size: a Bound 500Gi claim on the former class is applied as it is (both kept, the log says so); a smaller one on the rendered class "
       "is grown to 100Gi; an equal size in another spelling passes silently; a Pending claim on a class the cluster lacks fails naming the claim, the way out and the rendered class; "
       "a Pending claim on a class the cluster has, a claim without a class and one on the empty class keep theirs")

    # A parameter change renders a new class -- the API forbids changing a class's parameters -- and the claim references it (#570).
    retiered = documents(helm(connectivity, [*base, "--set", "modelServing.cache.storageClass.parameters.throughput=1000"]))
    retiered_name = f"agent-platform-connectivity-hf-cache-{class_digest(CACHE_PROVISIONER, {**CACHE_PARAMETERS, 'throughput': '1000'})}"
    if ("StorageClass", retiered_name) not in retiered or CACHE_CLASS in retiered or applied_claim(retiered[CACHE_JOB])["spec"]["storageClassName"] != retiered_name:
        sys.exit(f"FAIL: a parameter change should render the class under a new name ({retiered_name}) and the claim reference it: {sorted(k for k in retiered if k[0] == 'StorageClass')}, {applied_claim(retiered[CACHE_JOB])['spec']}")
    need(retiered[("StorageClass", retiered_name)], '  throughput: "1000"', "the re-tiered StorageClass")
    ok(f"a parameter change renders the class under a new digest name ({retiered_name}), the former ({CACHE_CLASS[1]}) gone, and the applied claim references the new one")

    knobs = documents(helm(connectivity, [*base, *NO_CLASS, "--set", "modelServing.cache.pvc.storageClassName=gp3", "--set", "modelServing.cache.pvc.size=1Ti",
                                          "--set", "modelServing.cache.pvc.volumeName=nvme-0", "--set", "modelServing.cache.pvc.accessModes[0]=ReadWriteMany"]))
    spec = applied_claim(knobs[CACHE_JOB])["spec"]
    if spec != {"accessModes": ["ReadWriteMany"], "resources": {"requests": {"storage": "1Ti"}}, "storageClassName": "gp3", "volumeName": "nvme-0"}:
        sys.exit(f"FAIL: the class, size, volumeName and access-mode knobs did not reach the applied claim:\n{json.dumps(spec, indent=1)}")
    dash = applied_claim(documents(helm(connectivity, [*base, *NO_CLASS, "--set", "modelServing.cache.pvc.storageClassName=-"]))[CACHE_JOB])["spec"]
    if dash.get("storageClassName") != "":
        sys.exit(f"FAIL: storageClassName \"-\" should apply the empty class (static binding):\n{json.dumps(dash, indent=1)}")
    ok('with storageClass.create: false, pvc.storageClassName, size, volumeName and accessModes reach the applied claim; "-" is the empty class')

    helm(connectivity, [*base, "--set", "modelServing.cache.pvc.storageClassName=gp3"], expect_failure="both name the cache claim's class")
    named = documents(helm(connectivity, [*base, *NO_CLASS, "--set", "modelServing.cache.storageClass.name=io2-fast"]))
    if any(kind == "StorageClass" for kind, _ in named) or applied_claim(named[CACHE_JOB])["spec"].get("storageClassName") != "io2-fast":
        sys.exit(f"FAIL: storageClass.create: false with a name should render no class and reference io2-fast: {sorted(k for k in named if k[0] == 'StorageClass')}, {applied_claim(named[CACHE_JOB])['spec']}")
    default_class = documents(helm(connectivity, [*base, *NO_CLASS]))
    if any(kind == "StorageClass" for kind, _ in default_class) or "storageClassName" in applied_claim(default_class[CACHE_JOB])["spec"]:
        sys.exit(f"FAIL: storageClass.create: false without a name should render no class and leave the claim on the cluster's default: {applied_claim(default_class[CACHE_JOB])['spec']}")
    own = documents(helm(connectivity, [*base, "--set", "modelServing.cache.storageClass.name=fast", "--set", "modelServing.cache.storageClass.parameters.iops=16000",
                                        "--set", "modelServing.cache.storageClass.provisioner=disk.csi.azure.com"]))
    fast = own.get(("StorageClass", "fast"))
    if not fast or CACHE_CLASS in own or applied_claim(own[CACHE_JOB])["spec"].get("storageClassName") != "fast":
        sys.exit(f"FAIL: storageClass.name should name the rendered class and the claim's reference: {sorted(k for k in own if k[0] == 'StorageClass')}, {applied_claim(own[CACHE_JOB])['spec']}")
    for needle in ('provisioner: "disk.csi.azure.com"', '  iops: "16000"', '  type: "gp3"'):
        need(fast, needle, "the renamed StorageClass")
    ok("storageClass.create: false with a name references it and renders no class, without a name leaves the claim on the cluster's default; a name, the provisioner and a parameter (as a string) reach the rendered class; pvc.storageClassName next to create: true is refused naming both")

    for flags, label in ((["--set", "modelServing.cache.enabled=false"], "cache.enabled: false"), (["--set", "modelServing.cache.pvc.existingClaim=models"], "an existing claim")):
        text = helm(connectivity, [*base, *flags])
        off = documents(text)
        if "PersistentVolumeClaim" in text or CACHE_JOB in off:
            sys.exit(f"FAIL: with {label} the render still carries the claim or its hook: {sorted(k for k in off if k == CACHE_JOB)}")
        # The hook identity stays for the pre-pull DaemonSet's pre-delete cleanup Job (#563) — created for that event
        # alone, without the claim rule — and goes with the pre-pull switch: the slice then has no hook at all.
        role = off.get(("ClusterRole", HOOK_IDENTITY))
        if not role or PREPULL_CLEANUP not in off or ("ServiceAccount", HOOK_IDENTITY) not in off or ("ClusterRoleBinding", HOOK_IDENTITY) not in off:
            sys.exit(f"FAIL: with {label} the pre-pull cleanup Job or the hook identity it runs as is missing: {sorted(k for k in off if k == PREPULL_CLEANUP or k[1] == HOOK_IDENTITY)}")
        need(role, "    helm.sh/hook: pre-delete\n", f"the hook identity with {label} (pre-delete its only event)")
        if 'resources: ["persistentvolumeclaims"]' in role:
            sys.exit(f"FAIL: with {label} the hook identity still carries the claim rule")
        none = documents(helm(connectivity, [*base, *flags, "--set", "modelServing.prepull.enabled=false"]))
        if PREPULL_CLEANUP in none or any(name == HOOK_IDENTITY for _, name in none):
            sys.exit(f"FAIL: with {label} and prepull.enabled: false the render still carries a hook or the hook identity (the slice has no other hook): {sorted(k for k in none if k == PREPULL_CLEANUP or k[1] == HOOK_IDENTITY)}")
        if any(kind == "StorageClass" for kind, _ in off):
            sys.exit(f"FAIL: with {label} the render still carries a StorageClass; the class is the chart's claim's")
        if label == "an existing claim" and "claimName: models" not in text:
            sys.exit("FAIL: the existing claim is not published")
        if KEEP not in off.get(SERVING_NS, ""):
            sys.exit(f"FAIL: with {label} the serving namespace is not kept; the policy is independent of the cache switch — a namespace Helm deletes takes a claim an earlier release left there along (#565):\n{off.get(SERVING_NS)}")
    ok("cache.enabled: false and an existing claim render no claim, no cache hook and no StorageClass; the hook identity stays for the pre-pull's pre-delete cleanup Job alone (pre-delete its only event, no claim rule) and goes with prepull.enabled: false; the namespace is kept either way; the existing claim is published")

    unkept = documents(helm(connectivity, [*base, "--set", "modelServing.namespace.keep=false", "--set", "modelServing.cache.enabled=false"]))
    if SERVING_NS not in unkept or KEEP in unkept[SERVING_NS] or "annotations:" in unkept[SERVING_NS]:
        sys.exit(f"FAIL: namespace.keep: false should render the serving namespace without the keep policy (the namespace and everything in it go with the release):\n{unkept.get(SERVING_NS)}")
    if SERVING_NS in documents(helm(connectivity, [*base, "--set", "modelServing.namespace.create=false"])):
        sys.exit("FAIL: namespace.create: false still renders the serving namespace")
    ok("namespace.keep: false renders the namespace without the keep policy (for an installation that wants nothing of the serving layer to outlive the release); namespace.create: false renders no namespace")


# The tail of the well-known runtime template's entrypoint (kserve-runtime-configs,
# kserve-config-llm-template): `bash -c <script> -- <args>` ends in an eval with an
# unquoted $@, so every argument is re-parsed by the shell — a bare JSON splits at
# its whitespace and loses its double quotes (giantswarm/agent-platform#532). The
# check runs that eval, with argv dumped instead of vLLM, over each preset's args.
ENTRYPOINT_EVAL = 'eval "exec {dump} serve /mnt/models --served-model-name "m" "publishers/ns/models/m" --port 8000 ${{VLLM_ADDITIONAL_ARGS}} $@"'
ARGV_DUMP = "import json, sys; print(json.dumps(sys.argv[1:]))"
# The classic InferenceService path's preset fields, refused by the render
# since giantswarm/agent-platform#574.
CLASSIC_PRESET_FIELDS = ("runtime", "predictor")


def eval_argv(args: list[str]) -> list[str]:
    """What vLLM's argv would be after the entrypoint's eval of args: the dumped
    argv from `serve` on, without the fixed prefix the template puts first."""
    script = ENTRYPOINT_EVAL.format(dump=f"python3 -c {shlex.quote(ARGV_DUMP)}")
    result = subprocess.run(["bash", "-c", script, "--", *args], capture_output=True, text=True, check=False, env={"PATH": os.environ["PATH"]})
    if result.returncode != 0:
        sys.exit(f"FAIL: the entrypoint's eval fails over {args!r}:\n{result.stderr}")
    return json.loads(result.stdout)[7:]


def json_values(name: str, path: str, got: list[str], expected: list[str]) -> int:
    """Assert vLLM sees one word per argument and every JSON value parses; the
    count of JSON values checked."""
    if got != expected:
        sys.exit(f"FAIL: preset {name} on {path}: vLLM would see {got!r}, not one word per argument {expected!r}")
    checked = 0
    for word in got:
        flag, sep, value = word.partition("=")
        if sep and value[:1] in "{[":
            try:
                json.loads(value)
            except ValueError as err:
                sys.exit(f"FAIL: preset {name} on {path}: {flag}'s value {value!r} is not JSON: {err}")
            checked += 1
    return checked


def check_preset_args(connectivity: str, base: list[str]) -> None:
    files = sorted(glob.glob(f"{connectivity}/files/model-serving/presets/*.yaml"))
    if len(files) < 2:
        sys.exit(f"FAIL: expected the shipped presets under {connectivity}/files/model-serving/presets/, found {files}")
    docs = documents(helm(connectivity, base))
    if classic := [k for k in docs if k[0] in ("ClusterServingRuntime", "ServingRuntime", "InferenceService")]:
        sys.exit(f"FAIL: the serving render carries classic serving objects {classic}; the classic path was removed (giantswarm/agent-platform#574)")
    checked = 0
    for path in files:
        preset = yaml.safe_load(open(path))
        preset_name = preset["metadata"]["name"]
        if carried := [f for f in CLASSIC_PRESET_FIELDS if f in preset["spec"]]:
            sys.exit(f"FAIL: shipped preset {preset_name} carries the classic field(s) {carried}; a preset composes onto the well-known LLMInferenceServiceConfigs (#574)")
        preset_args = [str(a) for a in preset["spec"].get("args") or []]
        # A preset on N GPUs splits the model across them itself: the well-known template adds a
        # --tensor-parallel-size only for spec.parallelism.tensor, which model-manager never sets (#591).
        gpus = int((preset["spec"].get("resources") or {}).get("gpus", 1))
        tp = next((int(a.split("=", 1)[1]) for a in preset_args if a.startswith("--tensor-parallel-size=")), 1)
        if gpus != tp:
            sys.exit(f"FAIL: shipped preset {preset_name} requests {gpus} GPU(s) (resources.gpus) but its arguments set tensor parallel {tp}; "
                     "a preset on N GPUs carries --tensor-parallel-size=N, a one-GPU preset no such flag")
        if docs_off := [a for a in preset_args if re.match(r"--disable-fastapi-docs(=|$)", a)]:
            sys.exit(f"FAIL: shipped preset {preset_name} carries {docs_off}: it removes the runtime's /openapi.json, the route list "
                     "model-manager reads the model's API interfaces from (giantswarm/agent-platform#602)")
        expected = [shlex.split(a)[0] for a in preset_args]
        checked += json_values(preset_name, "the llm-d template", eval_argv(preset_args), expected)
    if checked == 0:
        sys.exit("FAIL: no JSON value among the shipped presets' arguments; the eval check has nothing to prove")
    # A values preset that still carries a classic field fails the render naming it.
    for field in CLASSIC_PRESET_FIELDS:
        doc = {"apiVersion": "agent-platform.giantswarm.io/v1alpha1", "kind": "ServingPreset", "metadata": {"name": "old"},
               "spec": {"displayName": "Old", "model": {"id": "o/M", "storageUri": "hf://o/M"}, "requirements": {"weightsGiB": 1}, field: {} if field == "predictor" else "kserve-vllm"}}
        err = helm(connectivity, [*base, "--set-json", "modelServing.presets=" + json.dumps([doc])], expect_failure=f"spec.{field} is no longer a preset field")
        need(err, 'serving preset "old" (values)', f"the guard's message for spec.{field}")
    # A values preset that turns the runtime's route list off fails the render naming the flag.
    for flag in ("--disable-fastapi-docs", "--disable-fastapi-docs=true"):
        doc = {"apiVersion": "agent-platform.giantswarm.io/v1alpha1", "kind": "ServingPreset", "metadata": {"name": "nodocs"},
               "spec": {"displayName": "No docs", "model": {"id": "o/M", "storageUri": "hf://o/M"}, "requirements": {"weightsGiB": 1}, "args": ["--enforce-eager", flag]}}
        err = helm(connectivity, [*base, "--set-json", "modelServing.presets=" + json.dumps([doc])], expect_failure="removes the runtime's /openapi.json")
        need(err, f'serving preset "nodocs" (values): spec.args carries "{flag}"', f"the guard's message for {flag}")
    bad = {"bare JSON in two arguments": ["--default-chat-template-kwargs", '{"enable_thinking": false}'],
           "a space": ["--x=a b"], "a stray single quote": ["--x=it's"], "a double quote": ['--x="a"'],
           "a brace expansion": ["--x={a,b}"], "a variable": ["--x=$HOME"], "a glob": ["--x=*"]}
    for what, bad_args in bad.items():
        doc = {"apiVersion": "agent-platform.giantswarm.io/v1alpha1", "kind": "ServingPreset", "metadata": {"name": "bad"},
               "spec": {"displayName": "Bad", "model": {"id": "o/M", "storageUri": "hf://o/M"}, "requirements": {"weightsGiB": 1}, "args": bad_args}}
        err = helm(connectivity, [*base, "--set-json", "modelServing.presets=" + json.dumps([doc])], expect_failure="outside single quotes")
        need(err, 'serving preset "bad" (values): spec.args', f"the guard's message for {what}")
    ok(f"{len(files)} shipped presets' arguments survive the llm-d template's eval ({checked} JSON values parse), none carries a classic field, "
       f"each one's resources.gpus equals its tensor-parallel size, "
       f"the render carries no classic serving object; a values preset with spec.runtime or spec.predictor fails the render naming the field; "
       f"none carries --disable-fastapi-docs and a values preset with it fails the render naming the flag (the route list model-manager reads the interfaces from); "
       f"{len(bad)} argument shapes the shell would re-split, expand or choke on fail the render naming the guard")


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
        for needle in ("    - --enable-auto-tool-choice", "    gpus: 1", f"    storageUri: {MODEL_IMAGES}"):
            need(text, needle, f"preset {name}")
        for flag in ("--tool-call-parser", "--reasoning-parser"):
            if not re.search(rf"^    - {flag}=\w+$", text, re.M):
                sys.exit(f"FAIL: preset {name} sets no {flag}; agents send tools and read reasoning on every request")
        caps = re.search(r"^    capabilities: \[(.*)\]$", text, re.M)
        if not caps or not {"tools", "reasoning"} <= {c.strip() for c in caps.group(1).split(",")}:
            sys.exit(f"FAIL: preset {name} does not declare the tools and reasoning capabilities its parsers serve")
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
    ok(f"presets {', '.join(PRESETS)}: schema keys, a signed model image and no serving image, tools and reasoning on with their parsers, one GPU, <= 24 GiB, "
       f"requests within a g6.xlarge's {USABLE_VCPU:g} vCPU / {USABLE_GIB:.1f} GiB; the schema knows spec.template")


def family_problems(preset: dict, families: list[dict], runtime: dict, templates: str) -> list[str]:
    """What is wrong with a preset against the model-family table (files/model-serving/model-families.yaml,
    giantswarm/agent-platform#313): its family, found by spec.model.id, and the parsers and chat template it must carry."""
    name, spec = preset["metadata"]["name"], preset["spec"]
    model = spec["model"]["id"]
    rows = [f for f in families if re.search(f["match"], model)]
    if len(rows) != 1:
        which = "no family row" if not rows else f"{len(rows)} family rows ({', '.join(r['name'] for r in rows)})"
        return [f"preset {name} serves {model}, which {which} of model-families.yaml matches; add the family's row (its tool-call parsers, "
                "reasoning parser and chat template from its vLLM recipe) or narrow the matches"]
    row = rows[0]
    args = [str(a) for a in spec.get("args") or []]
    caps = set(spec["model"].get("capabilities") or [])
    tool = [a.split("=", 1)[1] for a in args if a.startswith("--tool-call-parser=")]
    reasoning = [a.split("=", 1)[1] for a in args if a.startswith("--reasoning-parser=")]
    problems = []
    for parser in tool:
        if parser not in row["toolCallParsers"]:
            problems.append(f"preset {name} (family {row['name']}) carries --tool-call-parser={parser}; the family's are {row['toolCallParsers']}")
    for parser in reasoning:
        if parser != row["reasoningParser"]:
            problems.append(f"preset {name} (family {row['name']}) carries --reasoning-parser={parser}; the family's is {row['reasoningParser']}")
    if "tools" in caps and ("--enable-auto-tool-choice" not in args or not tool):
        problems.append(f"preset {name} (family {row['name']}) is tagged tools without --enable-auto-tool-choice and a tool-call parser of "
                        f"{row['toolCallParsers']}: kagent sends tools with tool_choice auto on every turn, which vLLM then refuses with 400")
    if "reasoning" in caps and not reasoning:
        problems.append(f"preset {name} (family {row['name']}) is tagged reasoning without --reasoning-parser={row['reasoningParser']}: "
                        "the thinking comes back inside the answer")
    chat = spec.get("chatTemplate") or {}
    if row["chatTemplate"] == "required" and not chat:
        problems.append(f"preset {name} (family {row['name']}) sets no spec.chatTemplate; the family's checkpoints ship none vLLM serves tools with")
    if chat.get("file") and not os.path.isfile(os.path.join(templates, chat["file"])):
        problems.append(f"preset {name} mounts the chat template {chat['file']}, which is not under files/model-serving/chat-templates/")
    return problems


def check_model_families(connectivity: str) -> None:
    """Every shipped preset against the model-family table: its family's parsers and chat template; every row's parsers
    registered by the runtime's vLLM; a wrong parser, a missing one and an unknown family fail naming what (#313)."""
    table = yaml.safe_load(open(f"{connectivity}/files/model-serving/model-families.yaml"))
    families, runtime = table["families"], table["runtime"]
    templates = f"{connectivity}/files/model-serving/chat-templates"
    for row in families:
        if row.get("chatTemplate") not in ("required", "optional"):
            sys.exit(f"FAIL: model-families.yaml row {row['name']}: chatTemplate is required or optional, not {row.get('chatTemplate')!r}")
        if row.get("runtimeImage"):
            continue  # a family on a runtime image of its own names that vLLM's parsers
        unknown = [p for p in row["toolCallParsers"] if p not in runtime["toolCallParsers"]]
        if row["reasoningParser"] not in runtime["reasoningParsers"]:
            unknown.append(row["reasoningParser"])
        if unknown:
            sys.exit(f"FAIL: model-families.yaml row {row['name']} names {unknown}, which vLLM {runtime['vllm']} does not register")
    files = sorted(glob.glob(f"{connectivity}/files/model-serving/presets/*.yaml"))
    problems = [p for path in files for p in family_problems(yaml.safe_load(open(path)), families, runtime, templates)]
    if problems:
        sys.exit("FAIL: " + "\nFAIL: ".join(problems))
    # Negative controls: the check names what is wrong.
    base = yaml.safe_load(open(f"{connectivity}/files/model-serving/presets/qwen3-5-4b.yaml"))
    controls = {
        "a tool-call parser the family does not list": (["--enable-auto-tool-choice", "--tool-call-parser=hermes", "--reasoning-parser=qwen3"], "Qwen/Qwen3.5-4B", "--tool-call-parser=hermes; the family's are"),
        "a tools preset without its parser": (["--reasoning-parser=qwen3"], "Qwen/Qwen3.5-4B", "is tagged tools without --enable-auto-tool-choice"),
        "a reasoning preset without its parser": (["--enable-auto-tool-choice", "--tool-call-parser=qwen3_coder"], "Qwen/Qwen3.5-4B", "is tagged reasoning without --reasoning-parser=qwen3"),
        "a family the table lacks": (["--enable-auto-tool-choice", "--tool-call-parser=hermes"], "org/Unknown-7B", "which no family row of model-families.yaml matches"),
    }
    for what, (args, model, needle) in controls.items():
        control = yaml.safe_load(yaml.safe_dump(base))
        control["spec"]["args"], control["spec"]["model"]["id"] = args, model
        got = family_problems(control, families, runtime, templates)
        if not any(needle in p for p in got):
            sys.exit(f"FAIL: the model-family check passes {what} (got {got})")
    ok(f"{len(files)} shipped presets carry their family's parsers ({len(families)} rows of model-families.yaml, every parser registered by "
       f"vLLM {runtime['vllm']}): tools with --enable-auto-tool-choice and a family tool-call parser, reasoning with the family's reasoning parser, "
       f"every mounted chat template present; a foreign parser, a missing one and an unknown family fail naming preset, family and parser")


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


def check_prepull_prefix(meta: str, connectivity: str) -> None:
    """The pre-pull's runtime image is the well-known config's llm-d-cuda at the prefix the slice passes (#568)."""
    with open(f"{meta}/values.yaml", encoding="utf-8") as f:
        registry = yaml.safe_load(f)["kserve-runtime-configs"]["kserve"]["llmisvcConfigs"]["imageRegistry"]
    with open(f"{connectivity}/values.yaml", encoding="utf-8") as f:
        images = yaml.safe_load(f)["modelServing"]["prepull"]["images"]
    if registry != FAST_PREFIX:
        sys.exit(f"FAIL: the kserve-runtime-configs block passes imageRegistry {registry!r}, expected the llm-d-fast/ prefix {FAST_PREFIX!r}")
    if not images or not images[0].startswith(f"{registry}llm-d-cuda:"):
        sys.exit(f"FAIL: modelServing.prepull.images {images} does not start with the well-known config's llm-d-cuda at the slice's imageRegistry {registry!r}: the pre-pull would warm an image no predictor runs")
    ok(f"the pre-pull's runtime image {images[0]} is the well-known config's llm-d-cuda at the prefix the slice passes as imageRegistry ({registry})")


def main(meta: str, connectivity: str) -> int:
    check_prepull_prefix(meta, connectivity)
    forwarded = check_profile(meta)
    with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as f:
        f.write(forwarded)
        values = f.name
    try:
        base = ["-f", values, "--namespace", "agent-platform", *FLEET_APIS]
        check_ingress_guard(connectivity, base)
        check_policy_exception(connectivity, base)
        check_controller_xds(connectivity, base)
        check_controller_jwks_egress(connectivity, base)
        check_gateway(connectivity, base)
        check_cache(connectivity, base)
        check_preset_args(connectivity, base)
    finally:
        os.unlink(values)
    check_presets(connectivity)
    check_model_families(connectivity)
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit("usage: verify-serving-slice.py <meta chart dir> <connectivity chart dir>")
    sys.exit(main(sys.argv[1], sys.argv[2]))
