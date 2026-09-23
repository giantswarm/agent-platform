#!/usr/bin/env python3
"""Assert one release of this chart per target cluster (giantswarm/agent-platform#328).

The serving slice (#326) and the runtime slice (#317) are values profiles of this
chart that cluster-manager installs as ONE `<cluster>-agent-platform` release per
target cluster — beside the platform's own release on the installation's cluster,
or onto a workload cluster that runs no Flux, through the installation's Flux the
way the fleet installs every workload-cluster app. Each case below pins one
property that shape relies on:

- the target knob: gitops.target.kubeConfig.secretRef stamps spec.kubeConfig.secretRef
  (name, and key when set) onto EVERY HelmRelease the component loop renders and
  changes nothing else — the knob render minus those lines is the default render;
  unset, no HelmRelease carries kubeConfig;
- the goldens: the default and CI renders of the meta chart and the default,
  full and Backstage renders of the connectivity chart are what tests/golden/
  says they are. The baseline is committed, so an intended change is
  `make golden-update` and its diff is reviewed in the pull request; there is
  nothing to hold equal and nothing to clean up once it merges;
- the toggles: components.muster.enabled / components.dicebear.enabled off render
  no release of theirs, the roster forwarded to the connectivity release says so,
  and the connectivity chart drops their references — muster's /mcp route, the
  muster egress policy and every policy rule that selects its pods; the avatars
  host in the portal's CSP;
- the guards that can be asserted offline: the knob with the bundled engine on
  fails naming the engine; the schema refuses an unknown key under gitops.target.
  The lookup guards (a foreign helm-controller with the engine on; a second owner
  of a component's CRDs, components.<name>.ownedCrds) need a live cluster and are
  asserted there (README, "One release per target cluster");
- no hook Job renders with the knob (a hook runs where the chart is installed, not
  on the target), while the same toggles without the knob render the kagent
  storage-version pair as before;
- the slices: the serving- and runtime-shaped toggle sets (ci/test-slice-*-values.yaml)
  render alone and combined, the combined release is the union, and every
  OCIRepository / HelmRelease of the first slice is byte-identical in the combined
  render (switching the second slice on is an in-place upgrade: nothing renames
  or moves; the connectivity release's values follow the roster by design);
  agentgateway follows the target: no release beside the platform's own, a
  release with the target knob (ci/test-target-values.yaml), every HelmRelease
  then carrying the kubeConfig.

Deliberately stdlib-only: the CI image has no PyYAML. HELM selects the binary.
"""

import os
import re
import subprocess
import sys

HELM = os.environ.get("HELM", "helm")
FLEET_APIS = [
    "--api-versions", "kyverno.io/v1",
    "--api-versions", "cilium.io/v2",
    "--api-versions", "monitoring.coreos.com/v1",
    "--api-versions", "gateway.networking.k8s.io/v1",
    "--api-versions", "gateway.envoyproxy.io/v1alpha1",
]
# Makefile.custom.mk's VM: the all-modes ingress guard satisfied, the Harness's
# snapshot store set, the fleet's API groups served.
VM = ["--set", "ingress.parentRefs[0].name=x", "--set", "kagent.harness.snapshotLocation=s3://ci-agent-snapshots/agents", *FLEET_APIS]
ENGINE_OFF = ["--set", "components.flux.enabled=false"]
FLUX_KINDS = {"OCIRepository", "HelmRelease"}
# The kagent storage-version hooks and their identity (hooks/*.yaml), the only
# objects of the engine-off render that are not Flux documents.
HOOK_OBJECTS = {("Job", "t-kagent-storage-version-backup"), ("Job", "t-kagent-storage-version-restore"),
                ("ServiceAccount", "t-hooks"), ("ClusterRole", "t-hooks"), ("ClusterRoleBinding", "t-hooks"),
                ("Role", "t-hooks"), ("RoleBinding", "t-hooks"), ("NetworkPolicy", "t-hooks"), ("CiliumNetworkPolicy", "t-hooks")}
KNOB = ["--set", "gitops.target.kubeConfig.secretRef.name=wc01-kubeconfig"]
KNOB_KEY = ["--set", "gitops.target.kubeConfig.secretRef.key=value"]
# The connectivity chart with everything that references muster on: the
# agentgateway wiring (its /mcp route, the data-plane and controller policies),
# the Substrate egress and muster's own in-cluster MCP egress.
CONN_FULL = [
    *VM,
    "--set", "components.agentgateway.enabled=true", "--set", "ingress.mode=agentgateway-muster",
    "--set", "components.kagent.enabled=true", "--set", "components.substrate.enabled=true",
    "--set", "components.substrate-crds.enabled=true", "--set", "networkPolicy.musterInClusterMcpPorts[0]=8080",
]
# Makefile.custom.mk's WIRING_BACKSTAGE: the portal on, whose CSP carries the avatars host.
CONN_BACKSTAGE = [
    *VM, "--namespace", "agent-platform",
    "--set", "global.domain=ci.example.com", "--set", "global.identity.issuerUrl=https://dex.ci.example.com",
    "--set", "global.identity.clientId=agent-platform", "--set", "global.identity.existingSecret=agent-platform-idp",
    "--set", "global.gatewayApi.parentRefs[0].name=giantswarm-default",
    "--set", "global.gatewayApi.parentRefs[0].namespace=envoy-gateway-system",
    "--set", "components.backstage.enabled=true",
]


def helm(chart: str, flags: list[str], expect_failure: bool = False) -> str:
    result = subprocess.run([HELM, "template", "t", chart, *flags], capture_output=True, text=True, check=False)
    if expect_failure:
        if result.returncode == 0:
            sys.exit(f"FAIL: render of {chart} {' '.join(flags)} succeeded, a failure was expected")
        return result.stderr
    if result.returncode != 0:
        sys.exit(f"FAIL: render of {chart} {' '.join(flags)} failed\n{result.stderr}")
    return result.stdout


def documents(manifest: str) -> dict[tuple[str, str], str]:
    """(kind, name) -> document, for every rendered object."""
    docs = {}
    for doc in manifest.split("\n---\n"):
        kind = re.search(r"^kind: (\S+)$", doc, re.M)
        name = re.search(r"^  name: (\S+)$", doc, re.M)
        if kind and name:
            docs[(kind.group(1), name.group(1))] = doc.strip("\n")
    return docs


def releases(manifest: str) -> set[str]:
    return {name for kind, name in documents(manifest) if kind == "HelmRelease"}


def ok(msg: str) -> None:
    print(f"ok: {msg}")


def check_knob(meta: str) -> None:
    ci = ["-f", f"{meta}/ci/ci-values.yaml", *ENGINE_OFF]
    plain = helm(meta, ci)
    if "kubeConfig:" in plain:
        sys.exit("FAIL: a HelmRelease carries kubeConfig without the target knob")
    for flags, lines in ((KNOB, ["  kubeConfig:", "    secretRef:", "      name: wc01-kubeconfig"]),
                         (KNOB + KNOB_KEY, ["  kubeConfig:", "    secretRef:", "      name: wc01-kubeconfig", "      key: value"])):
        knob = helm(meta, [*ci, *flags])
        docs = documents(knob)
        hrs = [d for (kind, _), d in docs.items() if kind == "HelmRelease"]
        block = "\n".join(lines)
        missing = [d for d in hrs if block not in d]
        if not hrs or missing:
            sys.exit(f"FAIL: {len(missing)} of {len(hrs)} HelmReleases lack the kubeConfig block with {flags}")
        if knob.count("  kubeConfig:") != len(hrs):
            sys.exit("FAIL: kubeConfig rendered outside a HelmRelease")
        # The Flux documents minus the kubeConfig lines are the default's; what else
        # leaves the render is the hook family (ci-values turn kagent on, and the
        # storage-version hooks run where the chart is installed — check_hooks).
        stripped = {k: "\n".join(line for line in d.split("\n") if line not in lines) for k, d in docs.items() if k[0] in FLUX_KINDS}
        if stripped != {k: d for k, d in documents(plain).items() if k[0] in FLUX_KINDS}:
            sys.exit(f"FAIL: the knob changed an OCIRepository / HelmRelease beyond the kubeConfig lines ({flags})")
        gone = {k for k in documents(plain) if k not in docs}
        if not gone <= HOOK_OBJECTS or {k for k in docs if k not in documents(plain)}:
            sys.exit(f"FAIL: the knob changed the render beyond the kubeConfig lines and the hooks: gone={sorted(gone)}")
        ok(f"every one of the {len(hrs)} HelmReleases carries the kubeConfig secretRef ({', '.join(f.split('.')[-1] for f in flags[1::2])}), nothing else changed")
    for f in ("test-target-values.yaml", "test-slice-serving-values.yaml", "test-slice-runtime-values.yaml"):
        helm(meta, ["-f", f"{meta}/ci/{f}"])
    ok("the target and slice fixtures each render alone")


GOLDEN_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "golden")
# The shapes whose rendered output is committed under tests/golden/. `meta` and
# `connectivity` are substituted for the chart directories at call time.
GOLDEN_SHAPES = [
    ("meta-default", "meta", []),
    ("meta-ci-engine-off", "meta", ["-f", "{meta}/ci/ci-values.yaml", *ENGINE_OFF]),
    ("connectivity-default", "connectivity", VM),
    ("connectivity-full", "connectivity", CONN_FULL),
    ("connectivity-backstage", "connectivity", CONN_BACKSTAGE),
]


def normalize(render: str) -> str:
    """The render as it is committed: trailing whitespace off every line and runs
    of blank lines collapsed.

    This is the whole difference between `helm template` on the 3.17.x CI pins
    and on a 4.x a developer is likely to have — measured, not assumed: the two
    binaries render these charts identically once it is applied. Normalizing
    here is what lets the committed file be the baseline for both.
    """
    return re.sub(r"\n{2,}", "\n", "\n".join(l.rstrip() for l in render.split("\n"))).strip() + "\n"


# The helm minor the committed renders were produced with, and the one CI pins
# (.circleci/custom.yml). It matters for this check and no other: helm 3 writes
# a null-valued key into a forwarded values tree (`maxUnavailable: null`, which
# this chart uses as the "explicitly cleared" marker) and helm 4 omits it, so
# the two disagree on the committed bytes. Every other target renders both sides
# with the same binary and never sees it.
HELM_PIN = "v3.17"


def require_pinned_helm(what: str) -> None:
    result = subprocess.run([HELM, "version", "--short"], capture_output=True, text=True)
    version = result.stdout.strip()
    if not version.startswith(HELM_PIN):
        sys.exit(
            f"FAIL: {what} needs helm {HELM_PIN}.x, the version CI pins and the committed renders "
            f"were produced with; this is {version or 'not a helm binary'}. helm 3 and helm 4 disagree "
            "on whether a null-valued key survives into a forwarded values tree, so the bytes would "
            f"differ for that reason alone. Point HELM at a {HELM_PIN}.x binary:\n"
            f"    curl -fsSL https://get.helm.sh/helm-{HELM_PIN}.3-$(uname -s | tr A-Z a-z)-"
            "$(uname -m | sed 's/x86_64/amd64/;s/aarch64/arm64/').tar.gz | tar xz -C /tmp\n"
            "    make verify-target HELM=/tmp/*/helm"
        )


def golden_shapes(meta: str, connectivity: str) -> list:
    charts = {"meta": meta, "connectivity": connectivity}
    return [(name, charts[which], [f.format(meta=meta) for f in flags]) for name, which, flags in GOLDEN_SHAPES]


def check_golden(meta: str, connectivity: str) -> None:
    """Every shape renders what tests/golden/ says it renders.

    The baseline is a file in the repository, not another commit. An intended
    change is `make golden-update`, whose diff lands in the pull request for a
    reviewer to read — so the rendered blast radius of a values or helper edit
    is reviewed rather than suppressed. Nothing has to be held equal, and
    nothing has to be cleaned up afterwards: the regenerated file IS the new
    baseline (giantswarm/agent-platform#455, #501, #529, #530, #531 were all
    cleanups the moving baseline made necessary).
    """
    require_pinned_helm("the golden comparison")
    stale = []
    for name, chart, flags in golden_shapes(meta, connectivity):
        path = os.path.join(GOLDEN_DIR, f"{name}.yaml")
        rendered = normalize(helm(chart, flags))
        if not os.path.isfile(path):
            stale.append(f"{name}: tests/golden/{name}.yaml does not exist")
            continue
        with open(path) as f:
            committed = f.read()
        if rendered == committed:
            continue
        import difflib
        excerpt = list(difflib.unified_diff(
            committed.splitlines(), rendered.splitlines(),
            f"tests/golden/{name}.yaml", "this tree", lineterm="", n=2))[:40]
        stale.append(f"{name} renders something else than tests/golden/{name}.yaml says\n" + "\n".join(excerpt))
    if stale:
        sys.exit("FAIL: " + "\n\n".join(stale) + "\n\nIf the change is intended, run `make golden-update` and "
                 "commit the result: the diff is what a reviewer reads to see which objects moved.")
    ok(f"{len(GOLDEN_SHAPES)} renders match tests/golden/")


def update_golden(meta: str, connectivity: str) -> int:
    require_pinned_helm("`make golden-update`")
    os.makedirs(GOLDEN_DIR, exist_ok=True)
    for name, chart, flags in golden_shapes(meta, connectivity):
        path = os.path.join(GOLDEN_DIR, f"{name}.yaml")
        with open(path, "w") as f:
            f.write(normalize(helm(chart, flags)))
        print(f"wrote tests/golden/{name}.yaml")
    return 0


def check_toggles(meta: str, connectivity: str) -> None:
    ci = ["-f", f"{meta}/ci/ci-values.yaml", *ENGINE_OFF]
    for name in ("muster", "dicebear"):
        off = helm(meta, [*ci, "--set", f"components.{name}.enabled=false"])
        docs = documents(off)
        if ("HelmRelease", name) in docs or ("OCIRepository", name) in docs:
            sys.exit(f"FAIL: components.{name}.enabled=false still rendered the {name} release")
        roster = f"\n      {name}:\n        enabled: false\n"
        if roster not in docs[("HelmRelease", "agent-platform-connectivity")]:
            sys.exit(f"FAIL: the roster forwarded to connectivity does not say {name}: enabled: false")
        ok(f"components.{name}.enabled=false: no release, the roster says so")

    on = helm(connectivity, CONN_FULL)
    off = helm(connectivity, [*CONN_FULL, "--set", "components.muster.enabled=false"])
    markers = ("app.kubernetes.io/name: muster", "muster-mcp-egress", "value: /mcp")
    for marker in markers:
        if marker not in on:
            sys.exit(f"FAIL: the full connectivity render lacks {marker!r} with muster on (the fixture is stale)")
        if marker in off:
            sys.exit(f"FAIL: connectivity still renders {marker!r} with components.muster.enabled=false")
    kinds = {kind for kind, _ in documents(on)} - {kind for kind, _ in documents(off)}
    if kinds != {"HTTPRoute"}:
        sys.exit(f"FAIL: muster off dropped {sorted(kinds)} from the connectivity render; expected the /mcp HTTPRoute only")
    ok("connectivity with muster off: no /mcp route, no muster egress policy, no rule selecting muster pods")

    on = helm(connectivity, CONN_BACKSTAGE)
    off = helm(connectivity, [*CONN_BACKSTAGE, "--set", "components.dicebear.enabled=false"])
    if "https://avatars.ci.example.com" not in on:
        sys.exit("FAIL: the portal's CSP lacks the avatars host with dicebear on")
    if "avatars." in off:
        sys.exit("FAIL: the portal's CSP still names the avatars host with components.dicebear.enabled=false")
    ok("connectivity with dicebear off: the avatars host leaves the portal's CSP")


def check_guards(meta: str) -> None:
    ci = ["-f", f"{meta}/ci/ci-values.yaml"]
    err = helm(meta, [*ci, *KNOB], expect_failure=True)
    if "cannot be combined with the bundled Flux engine" not in err:
        sys.exit(f"FAIL: the knob with the engine on failed for the wrong reason:\n{err}")
    ok("the target knob with the bundled engine on fails naming the engine")
    err = helm(meta, [*ci, *ENGINE_OFF, "--set", "gitops.target.bogus=1"], expect_failure=True)
    if "gitops" not in err or "bogus" not in err.lower() and "additional propert" not in err.lower():
        sys.exit(f"FAIL: gitops.target.bogus was not refused by the schema:\n{err}")
    ok("an unknown key under gitops.target is refused by the schema")


def check_hooks(meta: str) -> None:
    runtime = ["-f", f"{meta}/ci/test-slice-runtime-values.yaml"]
    jobs = lambda m: sum(1 for kind, _ in documents(m) if kind == "Job")
    if jobs(helm(meta, runtime)) == 0:
        sys.exit("FAIL: the runtime shape without the knob renders no hook Job (the kagent storage-version pair is expected)")
    with_knob = helm(meta, [*runtime, "-f", f"{meta}/ci/test-target-values.yaml"])
    if jobs(with_knob) or "kind: ServiceAccount" in with_knob:
        sys.exit("FAIL: a hook Job or its identity renders with the target knob; hooks run on the installation, not on the target")
    ok("no hook Job renders with the target knob; the storage-version pair still renders without it")


def check_slices(meta: str) -> None:
    ci = f"{meta}/ci"
    serving = helm(meta, ["-f", f"{ci}/test-slice-serving-values.yaml"])
    runtime = helm(meta, ["-f", f"{ci}/test-slice-runtime-values.yaml"])
    both = helm(meta, ["-f", f"{ci}/test-slice-serving-values.yaml", "-f", f"{ci}/test-slice-runtime-values.yaml"])
    s, r, b = releases(serving), releases(runtime), releases(both)
    if not (s and r) or s & r != {"agent-platform-connectivity"} or b != s | r:
        sys.exit(f"FAIL: the combined release is not the union of the slices: serving={sorted(s)} runtime={sorted(r)} both={sorted(b)}")
    if "muster" in b or "dicebear" in b or "valkey" in b or "agentgateway" in b or "model-manager" in b:
        sys.exit(f"FAIL: a slice beside the platform's release renders a component the platform's release owns: {sorted(b)}")
    for kind_name, doc in documents(serving).items():
        if kind_name[1] == "agent-platform-connectivity":
            continue
        if documents(both).get(kind_name) != doc:
            sys.exit(f"FAIL: {kind_name} of the serving slice changed when the runtime slice was switched on (not an in-place upgrade)")
    ok(f"serving ({len(s)}) + runtime ({len(r)}) = one release of {len(b)} HelmReleases; the first slice's documents unchanged when the second is switched on")

    target = helm(meta, ["-f", f"{ci}/test-slice-serving-values.yaml", "-f", f"{ci}/test-target-values.yaml"])
    t = releases(target)
    if t != s | {"agentgateway"}:
        sys.exit(f"FAIL: the serving slice with the target knob should add exactly agentgateway: {sorted(t)}")
    if target.count("  kubeConfig:") != len(t) or "      name: wc01-kubeconfig" not in target:
        sys.exit("FAIL: not every HelmRelease of the targeted slice carries the kubeConfig")
    ok("agentgateway follows the target: off beside the platform's release, on with the knob; every targeted HelmRelease carries the kubeConfig")


def main(meta: str, connectivity: str) -> int:
    check_knob(meta)
    check_golden(meta, connectivity)
    check_toggles(meta, connectivity)
    check_guards(meta)
    check_hooks(meta)
    check_slices(meta)
    return 0


if __name__ == "__main__":
    argv = [a for a in sys.argv[1:] if a != "--update"]
    if len(argv) != 2:
        sys.exit(f"usage: {sys.argv[0]} [--update] <meta chart dir> <connectivity chart dir>")
    if "--update" in sys.argv[1:]:
        sys.exit(update_golden(argv[0], argv[1]))
    sys.exit(main(argv[0], argv[1]))
