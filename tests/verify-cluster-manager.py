#!/usr/bin/env python3
"""Assert the cluster-manager component (components.cluster-manager, giantswarm/agent-platform#316).

cluster-manager is the Agent Platform's cluster write surface (giantswarm/giantswarm#37637;
bumblebee-plans#42 and #46): the clusters and GPU node pools of an installation, MCP only,
every write as the person. The meta chart carries it in model-manager's mold — a
component release with the chart's own MCPServer CR, the identity contract forwarded from
global.*, and the network policies the connectivity chart renders. Each case below pins
one property of that:

- off by default: no release, the roster forwarded to connectivity says
  `cluster-manager: enabled: false`, the two blocks (`cluster-manager:`, `clusterManager:`)
  are held back from the connectivity release (gatedValues), and the connectivity chart
  renders nothing named cluster-manager;
- on: ONE OCIRepository (the catalog's cluster-manager chart on >=0.4.0 <1.0.0) and ONE
  HelmRelease that dependsOn muster, with the block forwarded — the pinned Service name,
  oauth on with downstream, the muster registration with forwardToken and the audience
  the kube-apiserver trusts, global injected, modelManager.namespace derived from the
  platform's namespace; the two blocks reach connectivity; nothing else of the render
  moves but the roster entry and the blocks;
- the derived namespace: an own value that agrees passes, one that differs fails naming
  the key; gitops.targetNamespace moves it;
- the connectivity chart on, cilium: the ingress (muster + the kubelet's probes), the
  egress (DNS with the proxy clause, the kube-apiserver entity, the Dex issuer by name,
  the workload clusters' API servers by name and address on their ports, the extra names
  and blocks on 443) and the muster-to policy; kubernetes: the same three as
  NetworkPolicy — the apiserver CIDR, every public destination on the workload ports,
  a narrowed list when cidrs are set;
- the guards: muster off with the MCPServer on, a missing identity input, a bad CIDR,
  an empty port list, Flux required without the API;
- the target knob stamps spec.kubeConfig.secretRef onto the release like every other;
- the schema refuses a non-boolean toggle;
- examples/customer-bom.yaml pins the exact version and the pin reaches the OCIRepository.

Deliberately stdlib-only: the CI image has no PyYAML. HELM selects the binary.
"""

import os
import re
import subprocess
import sys

HELM = os.environ.get("HELM", "helm")
NAME = "cluster-manager"
WIRING = "clusterManager"
REPOSITORY = "oci://gsoci.azurecr.io/charts/giantswarm"
RANGE = ">=0.4.0 <1.0.0"
CI = ["--set", "components.flux.enabled=false"]
ON = ["--set", f"components.{NAME}.enabled=true"]
IDENTITY = [
    "--set", "global.domain=ci.example.com",
    "--set", "global.identity.issuerUrl=https://dex.ci.example.com",
    "--set", "global.identity.clientId=platform",
    "--set", "global.identity.existingSecret=platform-oauth",
]
# The connectivity chart's minimal on-state: muster on (the default), the
# component on, the identity contract set so the OAuth guard is satisfied.
CONN = ["--set", "ingress.parentRefs[0].name=x", *ON, *IDENTITY]
FLUX_APIS = ["--api-versions", "helm.toolkit.fluxcd.io/v2", "--api-versions", "source.toolkit.fluxcd.io/v1"]
POLICIES = ("ingress", "egress")


def fail(msg: str) -> None:
    sys.exit(f"FAIL: {msg}")


def ok(msg: str) -> None:
    print(f"ok: {msg}")


def helm(chart: str, flags: list, expect_fail: bool = False, ci_values: bool = True) -> str:
    cmd = [HELM, "template", "t", chart]
    if ci_values:
        cmd += ["-f", f"{chart}/ci/ci-values.yaml", *CI]
    cmd += flags
    r = subprocess.run(cmd, capture_output=True, text=True)
    if expect_fail:
        if r.returncode == 0:
            fail(f"the render passed but had to fail: {' '.join(flags)}")
        return r.stderr
    if r.returncode != 0:
        fail(f"the render failed: {' '.join(flags)}\n{r.stderr}")
    return r.stdout


def documents(render: str) -> dict:
    """(kind, name) -> the document, for every document that has both."""
    out = {}
    for doc in render.split("\n---\n"):
        kind = re.search(r"^kind: (\S+)", doc, re.M)
        name = re.search(r"^  name: (\S+)", doc, re.M)
        if kind and name:
            out[(kind.group(1), name.group(1))] = doc.rstrip("\n") + "\n"
    return out


def must_have(doc: str, lines: tuple, what: str) -> None:
    for line in lines:
        if line not in doc:
            fail(f"{what} lacks {line!r}:\n{doc}")


def main(meta: str, connectivity: str) -> int:
    # --- off by default -------------------------------------------------------
    off = helm(meta, [])
    off_docs = documents(off)
    for kind in ("OCIRepository", "HelmRelease"):
        if (kind, NAME) in off_docs:
            fail(f"components.{NAME} is not off by default: its {kind} rendered")
    conn = off_docs[("HelmRelease", "agent-platform-connectivity")]
    if f"\n      {NAME}:\n        enabled: false\n" not in conn:
        fail(f"the roster forwarded to connectivity does not say {NAME}: enabled: false")
    for block in (NAME, WIRING):
        if re.search(rf"^    {re.escape(block)}:", conn, re.M):
            fail(f"the {block} block reached the connectivity release while the component is off (components.{NAME}.gatedValues)")
    conn_off = helm(connectivity, ["--set", "ingress.parentRefs[0].name=x"], ci_values=False)
    if NAME in conn_off:
        fail(f"the connectivity chart renders something named {NAME} while the component is off")
    ok("off by default: no release, the roster says so, the two blocks are held back, connectivity renders nothing of it")

    # --- on --------------------------------------------------------------------
    on_docs = documents(helm(meta, ON))
    for kind in ("OCIRepository", "HelmRelease"):
        if (kind, NAME) not in on_docs:
            fail(f"components.{NAME}.enabled=true rendered no {kind} named {NAME}")
    oci = on_docs[("OCIRepository", NAME)]
    must_have(oci, (f"url: {REPOSITORY}/{NAME}", f'semver: "{RANGE}"'), "the OCIRepository")
    if "semverFilter" in oci or "insecure" in oci:
        fail("the OCIRepository carries a dev-channel knob by default")
    hr = on_docs[("HelmRelease", NAME)]
    must_have(hr, (
        f"releaseName: {NAME}",
        "  dependsOn:\n    - name: muster\n",
        f"    fullnameOverride: {NAME}\n",
        "    installation:\n      name: \"\"\n",
        "    modelManager:\n      namespace: default\n",
        "    oauth:\n      dex:\n        allowPrivateURLs: true\n      downstream:\n        enabled: true\n      enabled: true\n      provider: dex\n",
        "    muster:\n      mcpServer:\n        auth:\n          forwardToken: true\n          requiredAudiences:\n          - dex-k8s-authenticator\n        enabled: true\n",
        "    networkPolicy:\n      enabled: false\n",
        "\n    global:\n",
    ), "the HelmRelease")
    for absent in ("kubeConfig", "crds:", "postRenderers", "targetNamespace: kube-system"):
        if absent in hr:
            fail(f"the HelmRelease carries {absent.strip()!r}")
    conn_on = on_docs[("HelmRelease", "agent-platform-connectivity")]
    for block in (NAME, WIRING):
        if not re.search(rf"^    {re.escape(block)}:", conn_on, re.M):
            fail(f"the {block} block did not reach the connectivity release with the component on; its wiring reads it")
    added = set(on_docs) - set(off_docs)
    if added != {("OCIRepository", NAME), ("HelmRelease", NAME)}:
        fail(f"switching the component on added documents other than its two: {sorted(added)}")
    if set(off_docs) - set(on_docs):
        fail(f"switching the component on removed documents: {sorted(set(off_docs) - set(on_docs))}")
    for key, doc in off_docs.items():
        if key == ("HelmRelease", "agent-platform-connectivity"):
            continue
        if on_docs[key] != doc:
            fail(f"switching the component on changed {key}")
    ok(f"on: one OCIRepository ({RANGE}) + one HelmRelease dependsOn muster, the block forwarded (Service name, OAuth as the caller, the muster registration, global), the namespace derived, the two blocks reach connectivity; nothing else moved")

    # --- the derived namespace ------------------------------------------------------
    moved = documents(helm(meta, [*ON, "--set", "gitops.targetNamespace=agent-platform"]))[("HelmRelease", NAME)]
    must_have(moved, ("    modelManager:\n      namespace: agent-platform\n",), "the HelmRelease with gitops.targetNamespace")
    helm(meta, [*ON, "--set", f"{NAME}.modelManager.namespace=default"])
    err = helm(meta, [*ON, "--set", f"{NAME}.modelManager.namespace=elsewhere"], expect_fail=True)
    if f"{NAME}.modelManager.namespace (elsewhere) differs" not in err:
        fail(f"a disagreeing {NAME}.modelManager.namespace failed for another reason:\n{err}")
    ok("modelManager.namespace follows the platform's namespace; an own value must agree")

    # --- the target knob ------------------------------------------------------------
    target = documents(helm(meta, [*ON, "--set", "gitops.target.kubeConfig.secretRef.name=wc1-kubeconfig"]))[("HelmRelease", NAME)]
    if "  kubeConfig:\n    secretRef:\n      name: wc1-kubeconfig\n" not in target:
        fail("the target knob did not stamp spec.kubeConfig.secretRef onto the release")
    ok("the target knob stamps kubeConfig.secretRef on the release")

    # --- the schema -----------------------------------------------------------------------
    err = helm(meta, ["--set", f"components.{NAME}.enabled=maybe"], expect_fail=True)
    if NAME not in err:
        fail(f"a non-boolean toggle failed for another reason:\n{err}")
    ok("the schema refuses a non-boolean toggle")

    # --- the BOM ----------------------------------------------------------------------------
    bom_file = f"{meta}/examples/customer-bom.yaml"
    with open(bom_file, encoding="utf-8") as f:
        bom = f.read()
    m = re.search(rf'^\s*{re.escape(NAME)}:\s*\{{\s*versionRange:\s*"(\d+\.\d+\.\d+)"\s*\}}', bom, re.M)
    if not m:
        fail(f"{bom_file} does not pin components.{NAME}.versionRange to an exact version")
    pinned = documents(helm(meta, [*ON, "-f", bom_file]))[("OCIRepository", NAME)]
    if f'semver: "{m.group(1)}"' not in pinned:
        fail(f"the BOM pin {m.group(1)} did not reach the OCIRepository")
    ok(f"the BOM pins {m.group(1)} and the pin reaches the OCIRepository")

    # --- connectivity, cilium -------------------------------------------------------------
    prefix = f"agent-platform-connectivity-{NAME}"
    cilium = documents(helm(connectivity, [*CONN, "--set", "networkPolicy.flavor=cilium",
                                           "--set", f"{WIRING}.networkPolicy.workloadClusters.fqdns[0].matchPattern=api.*.example.com",
                                           "--set", f"{WIRING}.networkPolicy.workloadClusters.cidrs[0]=198.51.100.0/24",
                                           "--set", f"{WIRING}.networkPolicy.egress.fqdns[0].matchName=dex.private.example",
                                           "--set", f"{WIRING}.networkPolicy.egress.cidrs[0]=203.0.113.0/24"], ci_values=False))
    for pol in POLICIES:
        if ("CiliumNetworkPolicy", f"{prefix}-{pol}") not in cilium:
            fail(f"cilium: CiliumNetworkPolicy {prefix}-{pol} missing")
    if ("CiliumNetworkPolicy", f"agent-platform-connectivity-muster-to-{NAME}") not in cilium:
        fail(f"cilium: CiliumNetworkPolicy agent-platform-connectivity-muster-to-{NAME} missing")
    ingress = cilium[("CiliumNetworkPolicy", f"{prefix}-ingress")]
    must_have(ingress, (f"app.kubernetes.io/name: {NAME}\n", "app.kubernetes.io/name: muster\n", "        - host\n        - remote-node\n", '- port: "8080"\n'), "the cilium ingress policy")
    egress = cilium[("CiliumNetworkPolicy", f"{prefix}-egress")]
    must_have(egress, (
        "        - kube-apiserver\n",
        "matchName: dex.ci.example.com\n",
        "matchPattern: api.*.example.com\n",
        "- 198.51.100.0/24\n",
        "matchName: dex.private.example\n",
        "- 203.0.113.0/24\n",
        '- port: "6443"\n',
        "rules:\n            dns:\n",
    ), "the cilium egress policy")
    if egress.count('- port: "443"') < 3:
        fail("cilium egress: the IdP, the workload ports and the extra egress do not all open 443")
    ok("cilium: ingress (muster + probes), egress (DNS proxy, kube-apiserver, the Dex issuer, the workload clusters by name and address on 443/6443, the extra names and blocks), muster-to")

    # --- connectivity, kubernetes -----------------------------------------------------
    k8s = documents(helm(connectivity, [*CONN, "--set", "networkPolicy.flavor=kubernetes"], ci_values=False))
    for pol in POLICIES:
        if ("NetworkPolicy", f"{prefix}-{pol}") not in k8s:
            fail(f"kubernetes: NetworkPolicy {prefix}-{pol} missing")
    if ("NetworkPolicy", f"agent-platform-connectivity-muster-to-{NAME}") not in k8s:
        fail(f"kubernetes: NetworkPolicy agent-platform-connectivity-muster-to-{NAME} missing")
    egress = k8s[("NetworkPolicy", f"{prefix}-egress")]
    must_have(egress, ("cidr: 0.0.0.0/0\n", "- port: 6443\n", "k8s-app\n"), "the kubernetes egress policy")
    if egress.count("cidr: 0.0.0.0/0") != 2:
        fail("kubernetes egress: expected every public destination twice (the workload ports, the IdP on 443)")
    narrowed = documents(helm(connectivity, [*CONN, "--set", "networkPolicy.flavor=kubernetes",
                                             "--set", f"{WIRING}.networkPolicy.workloadClusters.cidrs[0]=198.51.100.0/24"], ci_values=False))[("NetworkPolicy", f"{prefix}-egress")]
    must_have(narrowed, ('cidr: "198.51.100.0/24"\n',), "the narrowed kubernetes egress policy")
    if narrowed.count("cidr: 0.0.0.0/0") != 1:
        fail("kubernetes egress with workload cidrs: the workload rule still opens every public destination")
    ok("kubernetes: the three policies; the apiserver CIDR; every public destination on the workload ports, narrowed by cidrs; the IdP on 443")

    # --- the guards -------------------------------------------------------------------------
    for what, flags, fragment in (
        ("muster off with the MCPServer on", ["--set", "components.muster.enabled=false"], f"{NAME}.muster.mcpServer.enabled is true but components.muster.enabled is false"),
        ("a missing issuer", ["--set", "global.identity.issuerUrl="], f"{NAME}.oauth.dex.issuerURL is empty and global.identity.issuerUrl is not set"),
        ("a missing domain", ["--set", "global.domain="], f"{NAME}.oauth.baseURL is empty and global.domain is not set"),
        ("a bad CIDR", ["--set", f"{WIRING}.networkPolicy.workloadClusters.cidrs[0]=not-a-cidr"], "is not an IPv4 CIDR"),
        ("no workload ports", ["--set", f"{WIRING}.networkPolicy.workloadClusters.ports=null"], f"{WIRING}.networkPolicy.workloadClusters.ports is empty"),
        ("Flux required without the API", ["--set", f"{WIRING}.flux.requireApi=true"], "helm.toolkit.fluxcd.io/v2 API (HelmRelease) is not on the cluster"),
    ):
        err = helm(connectivity, [*CONN, *flags], expect_fail=True, ci_values=False)
        if fragment not in err:
            fail(f"{what}: failed for another reason:\n{err}")
    helm(connectivity, [*CONN, "--set", f"{WIRING}.flux.requireApi=true", *FLUX_APIS], ci_values=False)
    ok("the guards: muster off, a missing identity input, a bad CIDR, no ports, Flux required; Flux served passes")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "helm/agent-platform",
                  sys.argv[2] if len(sys.argv) > 2 else "helm/agent-platform-connectivity"))
