#!/usr/bin/env python3
"""Assert the GPU operator component (components.gpu-operator, giantswarm/agent-platform#327).

NVIDIA's GPU operator runs once per cluster and reaches it as a HelmRelease of
Giant Swarm's gpu-operator-app (the catalog's `gpu-operator` wrapper chart, which
vendors NVIDIA's chart as a subchart of the same name) into kube-system — as this
component in an installation's GitOps values, or as cluster-manager's
`<cluster>-gpu-operator` with the first GPU pool (bumblebee-plans#46, the plan's
D3). Each case below pins one property of the component's render:

- off by default: no release, the roster forwarded to connectivity says
  `gpu-operator: enabled: false`, and the `gpu-operator:` values block is held
  back from the connectivity release (no wiring reads it);
- on: ONE OCIRepository (the catalog's gpu-operator chart on 1.x) and ONE
  HelmRelease into kube-system — release history there too, crds CreateReplace on
  install and upgrade, no dependsOn, no global — with the values nested under the
  wrapper's subchart key: the Flatcar row, driver and toolkit off, plus the
  DCGM exporter ServiceMonitor's tenant label; switching the component on
  changes nothing else of the render but the roster entry;
- the second row: gpu-operator.toolkit.enabled=true reaches the release, the
  driver stays off;
- the target knob stamps spec.kubeConfig.secretRef onto the release like every
  other (#328);
- the one-owner guard is silent offline with the nvidia.com, Flux and App APIs
  served (its lookups run and find nothing; on a cluster,
  tests/fixtures/gpu-operator-foreign-owner.yaml shows the refusal);
- the schema refuses a non-boolean toggle;
- examples/customer-bom.yaml pins the exact version the range resolves.

Deliberately stdlib-only: the CI image has no PyYAML. HELM selects the binary.
"""

import os
import re
import subprocess
import sys

HELM = os.environ.get("HELM", "helm")
NAME = "gpu-operator"
REPOSITORY = "oci://gsoci.azurecr.io/charts/giantswarm"
CI = ["--set", "components.flux.enabled=false"]
ON = ["--set", f"components.{NAME}.enabled=true"]
# The APIs the guard's lookups are gated on. Served, every lookup runs; under
# `helm template` each returns nothing, so the render must pass.
GUARD_APIS = [
    "--api-versions", "nvidia.com/v1",
    "--api-versions", "helm.toolkit.fluxcd.io/v2",
    "--api-versions", "application.giantswarm.io/v1alpha1",
]
FLATCAR_ROW = (
    f"    {NAME}:\n"
    "      dcgmExporter:\n"
    "        serviceMonitor:\n"
    "          additionalLabels:\n"
    "            observability.giantswarm.io/tenant: giantswarm\n"
    "      driver:\n"
    "        enabled: false\n"
    "      toolkit:\n"
    "        enabled: false\n"
)


def fail(msg: str) -> None:
    sys.exit(f"FAIL: {msg}")


def ok(msg: str) -> None:
    print(f"ok: {msg}")


def helm(meta: str, flags: list, expect_fail: bool = False) -> str:
    cmd = [HELM, "template", "t", meta, "-f", f"{meta}/ci/ci-values.yaml", *CI, *flags]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if expect_fail:
        if r.returncode == 0:
            fail(f"the render passed but had to fail: {' '.join(flags)}")
        return r.stderr
    if r.returncode != 0:
        fail(f"the render failed: {' '.join(flags)}\n{r.stderr}")
    return r.stdout


def documents(render: str) -> dict:
    """(kind, name) -> the document, for every document that has both.

    Every document ends in exactly one newline: the last document of a render
    ends in as many as the Helm build prints, and the assertions below match
    whole lines.
    """
    out = {}
    for doc in render.split("\n---\n"):
        kind = re.search(r"^kind: (\S+)", doc, re.M)
        name = re.search(r"^  name: (\S+)", doc, re.M)
        if kind and name:
            out[(kind.group(1), name.group(1))] = doc.rstrip("\n") + "\n"
    return out


def main(meta: str) -> int:
    # --- off by default -------------------------------------------------------
    off = helm(meta, [])
    off_docs = documents(off)
    for kind in ("OCIRepository", "HelmRelease"):
        if (kind, NAME) in off_docs:
            fail(f"components.{NAME} is not off by default: its {kind} rendered")
    conn = off_docs[("HelmRelease", "agent-platform-connectivity")]
    if f"\n      {NAME}:\n        enabled: false\n" not in conn:
        fail(f"the roster forwarded to connectivity does not say {NAME}: enabled: false")
    if re.search(rf"^    {re.escape(NAME)}:", conn, re.M):
        fail(f"the {NAME} block reached the connectivity release; hold it back with components.agent-platform-connectivity.omitKeys (no wiring reads it)")
    ok("off by default: no release, the roster says so, the values block is held back from connectivity")

    # --- on: the Flatcar row into kube-system -----------------------------------
    on = helm(meta, ON)
    on_docs = documents(on)
    for kind in ("OCIRepository", "HelmRelease"):
        if (kind, NAME) not in on_docs:
            fail(f"components.{NAME}.enabled=true rendered no {kind} named {NAME}")
    oci = on_docs[("OCIRepository", NAME)]
    for line in (f"url: {REPOSITORY}/{NAME}", 'semver: "1.x"'):
        if line not in oci:
            fail(f"the OCIRepository lacks {line!r}")
    if "semverFilter" in oci or "insecure" in oci:
        fail("the OCIRepository carries a dev-channel knob by default")
    hr = on_docs[("HelmRelease", NAME)]
    for line in (f"releaseName: {NAME}", "targetNamespace: kube-system", "storageNamespace: kube-system", "createNamespace: true"):
        if line not in hr:
            fail(f"the HelmRelease lacks {line!r}")
    if hr.count("crds: CreateReplace") != 2:
        fail("the HelmRelease does not carry crds: CreateReplace on both install and upgrade")
    for absent in ("dependsOn", "kubeConfig", "\n    global:", "valuesFrom", "postRenderers"):
        if absent in hr:
            fail(f"the HelmRelease carries {absent.strip()!r}; the operator has no dependency, no target, no global")
    values = hr.split("\n  values:\n", 1)[1]
    if values != FLATCAR_ROW:
        fail(f"the forwarded values are not the Flatcar row nested under the wrapper's key:\n{values}")
    # Nothing else of the render moves: the same documents, the roster flipped.
    added = set(on_docs) - set(off_docs)
    if added != {("OCIRepository", NAME), ("HelmRelease", NAME)}:
        fail(f"switching the component on added documents other than its two: {sorted(added)}")
    if set(off_docs) - set(on_docs):
        fail(f"switching the component on removed documents: {sorted(set(off_docs) - set(on_docs))}")
    for key, doc in off_docs.items():
        expected = doc.replace(f"\n      {NAME}:\n        enabled: false\n", f"\n      {NAME}:\n        enabled: true\n")
        if on_docs[key] != expected:
            fail(f"switching the component on changed {key} beyond the roster entry")
    ok("on: one OCIRepository (1.x) + one HelmRelease into kube-system, CreateReplace, no dependsOn, the Flatcar row nested under the subchart key; nothing else moved")

    # --- the second row -----------------------------------------------------------
    row2 = documents(helm(meta, [*ON, "--set", f"{NAME}.toolkit.enabled=true"]))[("HelmRelease", NAME)]
    if "      toolkit:\n        enabled: true\n" not in row2 or "      driver:\n        enabled: false\n" not in row2:
        fail("gpu-operator.toolkit.enabled=true did not reach the release with the driver still off")
    ok("the pre-installed-driver row: toolkit on, driver off")

    # --- the target knob ------------------------------------------------------------
    target = documents(helm(meta, [*ON, "--set", "gitops.target.kubeConfig.secretRef.name=wc1-kubeconfig"]))[("HelmRelease", NAME)]
    if "  kubeConfig:\n    secretRef:\n      name: wc1-kubeconfig\n" not in target:
        fail("the target knob did not stamp spec.kubeConfig.secretRef onto the gpu-operator release")
    ok("the target knob stamps kubeConfig.secretRef on the release")

    # --- the guard, offline -----------------------------------------------------------
    helm(meta, [*ON, *GUARD_APIS])
    ok("the one-owner guard is silent offline with the nvidia.com, Flux and App APIs served")

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
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "helm/agent-platform"))
