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
- the goldens: with the new keys at their defaults, the default and CI renders of
  the meta chart and the default and full renders of the connectivity chart are
  byte-identical to GOLDEN_REF (origin/main; GOLDEN_REF= opts out);
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
import shutil
import subprocess
import sys
import tempfile

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


ROSTER = re.compile(r"(?<=\n    components:\n)((?:      [a-z0-9-]+:\n        enabled: (?:true|false)\n)+)")


def drop_new_roster_entries(here: str, there: str) -> tuple:
    """The two meta renders with the roster entries only one side has removed.

    The component loop forwards EVERY roster entry's `enabled` to the connectivity
    release (templates/components.yaml, the roster), so a component new to the
    working tree is a roster line the golden ref cannot have — the one difference
    a new component is allowed. Only the roster block of the connectivity
    HelmRelease is touched, and only by the entries in the symmetric difference;
    everything else stays byte for byte.
    """
    def entries(render: str) -> set:
        m = ROSTER.search(render)
        return set(re.findall(r"^      ([a-z0-9-]+):$", m.group(1), re.M)) if m else set()

    new = entries(here) ^ entries(there)
    if not new:
        return here, there
    def strip(render: str) -> str:
        m = ROSTER.search(render)
        if not m:
            return render
        block = m.group(1)
        for name in new:
            block = re.sub(rf"^      {re.escape(name)}:\n        enabled: (?:true|false)\n", "", block, flags=re.M)
        return render[:m.start(1)] + block + render[m.end(1):]
    print(f"note: roster entries only one side has, dropped from the golden comparison: {', '.join(sorted(new))}")
    return strip(here), strip(there)


def check_golden(meta: str, connectivity: str) -> None:
    ref = os.environ.get("GOLDEN_REF", "origin/main")
    if not ref:
        print("skip: GOLDEN_REF is empty (explicit opt-out)")
        return
    if subprocess.run(["git", "rev-parse", "--verify", "-q", ref], capture_output=True).returncode != 0:
        sys.exit(f"FAIL: GOLDEN_REF={ref} does not resolve; fetch it, point GOLDEN_REF at another ref, or run with GOLDEN_REF= to opt out")
    tree = tempfile.mkdtemp(prefix="ap-target-golden-")
    shutil.rmtree(tree)
    subprocess.run(["git", "worktree", "add", "-q", "--detach", tree, ref], check=True)
    try:
        # No held-equal flags: GOLDEN_REF is origin/main, which now carries every
        # feature the holds here used to mask, so each one only narrowed the
        # comparison (giantswarm/agent-platform#455 and #530 for the one that
        # narrowed it asymmetrically and broke the target). A NEW intended
        # difference gets its hold back, applied to BOTH sides and with the key
        # named, and is dropped again once GOLDEN_REF carries it — the image
        # defaults' move to gsoci, the kserve 0.4.x ranges and the forwarded
        # imageVerification defaults (#575) are the newest to have reached that
        # point.
        shapes = [
            ("meta default", meta, []),
            ("meta ci + engine off", meta, ["-f", f"{meta}/ci/ci-values.yaml", *ENGINE_OFF]),
            ("connectivity default", connectivity, [*VM]),
            ("connectivity full", connectivity, [*CONN_FULL]),
            ("connectivity backstage", connectivity, [*CONN_BACKSTAGE]),
        ]
        for label, chart, flags in shapes:
            here = helm(chart, flags)
            there = helm(os.path.join(tree, chart), [f.replace(f"{meta}/", f"{tree}/{meta}/") for f in flags])
            if chart == meta:
                here, there = drop_new_roster_entries(here, there)
            if here != there:
                import difflib
                excerpt = list(difflib.unified_diff(there.splitlines(), here.splitlines(), f"{ref}", "head", lineterm="", n=2))[:40]
                sys.exit(f"FAIL: the {label} render drifted from {ref}\n" + "\n".join(excerpt))
        ok(f"{len(shapes)} renders byte-identical to {ref}")
    finally:
        subprocess.run(["git", "worktree", "remove", "--force", tree], check=False)


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
    if len(sys.argv) != 3:
        sys.exit(f"usage: {sys.argv[0]} <meta chart dir> <connectivity chart dir>")
    sys.exit(main(sys.argv[1], sys.argv[2]))
