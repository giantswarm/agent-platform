#!/usr/bin/env python3
"""Assert the voluntary-disruption knobs the meta chart forwards to the component
charts (giantswarm/agent-platform#431): karpenter.sh/do-not-disrupt on the pods
of kagent-controller and klaus-gateway through each chart's podAnnotations, and
each chart's PodDisruptionBudget knob on and drainable, maxUnavailable: 1 — the
klaus-gateway chart's (1.1.0+) podDisruptionBudget with minAvailable emptied (the
chart defaults to minAvailable: 1, and a null set at this layer is consumed by
Helm before it reaches the chart), kagent's controller.pdb on the chart's own
default. muster is HA instead (giantswarm/agent-platform#1080): replicaCount 2,
a preferred pod anti-affinity on kubernetes.io/hostname selecting the muster
pods, a budget of minAvailable: 1 alone, and no do-not-disrupt. muster-valkey
(giantswarm/agent-platform#439): karpenter.sh/do-not-disrupt through the valkey
subchart's podAnnotations (valkey.valkey.podAnnotations); its budget is the
connectivity chart's (valkey.podDisruptionBudget travels to the connectivity
release and never to the valkey release — components.valkey.omitKeys). The
Substrate worker pool (giantswarm/agent-platform#1014): ate-controller's
busy-worker marking, karpenter.sh/do-not-disrupt on a worker pod only while it
hosts an actor, through the substrate chart's ateController.busyWorker.annotations.

Placement of the stateful singletons (#439): scheduling.singletons.nodeSelector
/ tolerations are merged into the four components' own scheduling knobs on
their releases (muster.nodeSelector, valkey.valkey.nodeSelector,
kagent.controller.nodeSelector, klausGateway.nodeSelector, and the tolerations
next to them) and the scheduling block itself is held back from the
connectivity release. By default the knob is empty and nothing is forwarded.

Reads a rendered meta-package manifest; with --off, the render with every knob
switched off, and asserts the switches travelled; with --placement, the render
with scheduling.singletons set to the PLACEMENT values below (and muster's own
nodeSelector keys and the kagent controller's own toleration set, which must
survive the merge). Deliberately stdlib-only: the CI image has no PyYAML.
"""

import sys

VALUES_INDENT = "    "
ANNOTATION = 'karpenter.sh/do-not-disrupt: "true"'
# The placement render's inputs (Makefile.custom.mk verify-disruption).
PLACEMENT_SELECTOR = "karpenter.sh/capacity-type: on-demand"
PLACEMENT_TOLERATION = ["effect: NoSchedule", "key: dedicated", "operator: Equal", "value: singletons"]
MUSTER_OWN_SELECTOR = ["karpenter.sh/capacity-type: spot", "topology.kubernetes.io/zone: eu-central-1a"]
KAGENT_OWN_TOLERATION = ["key: own", "operator: Exists"]
# Where the four components' charts read their scheduling knobs.
SCHEDULING_PATHS = {
    "muster": ("muster", ()),
    "valkey": ("valkey", ("valkey",)),
    "kagent": ("kagent", ("controller",)),
    "klaus-gateway": ("klaus-gateway", ()),
}


def helm_release(manifest: str, name: str) -> list[str]:
    for doc in manifest.split("\n---\n"):
        lines = doc.split("\n")
        if "kind: HelmRelease" in lines and f"  name: {name}" in lines:
            return lines
    sys.exit(f"FAIL: no {name} HelmRelease in the render")


def values(lines: list[str]) -> list[str]:
    """The spec.values block, stripped of the HelmRelease indentation."""
    out = []
    for line in lines[lines.index("  values:") + 1 :]:
        if line and not line.startswith(VALUES_INDENT):
            break
        out.append(line[len(VALUES_INDENT):])
    return out


def block(vals: list[str], *path: str) -> list[str]:
    """The lines nested under a key path (top-level key first): a map's keys two
    spaces in, or a list's items — toYaml renders `- ` at the key's own indent."""
    lines, indent = vals, ""
    for key in path:
        found = None
        for i, line in enumerate(lines):
            if line == f"{indent}{key}:":
                found = i
                break
        if found is None:
            return []
        item, indent = f"{indent}- ", indent + "  "
        nested = []
        for line in lines[found + 1 :]:
            if line.strip() and not (line.startswith(indent) or line.startswith(item)):
                break
            nested.append(line)
        lines = nested
    return lines


def expect(cond: bool, msg: str) -> None:
    if not cond:
        sys.exit(f"FAIL: {msg}")


def stripped(vals: list[str], *path: str) -> list[str]:
    """The block's lines without indentation; a list item's leading `- ` goes too."""
    return [l.strip().removeprefix("- ") for l in block(vals, *path) if l.strip()]


def check_placement(manifest: str, on: bool) -> None:
    """The four releases carry scheduling.singletons as their charts' knobs — or nothing."""
    for name, (release, prefix) in SCHEDULING_PATHS.items():
        vals = values(helm_release(manifest, release))
        selector = stripped(vals, *prefix, "nodeSelector")
        tolerations = stripped(vals, *prefix, "tolerations")
        if not on:
            expect(not selector, f"{name}: a nodeSelector travels with scheduling.singletons empty ({selector})")
            expect(not tolerations, f"{name}: tolerations travel with scheduling.singletons empty ({tolerations})")
            continue
        if name == "muster":
            # muster's own keys win: the knob's capacity-type must not overwrite spot, the zone stays.
            expect(all(l in selector for l in MUSTER_OWN_SELECTOR), f"muster: its own nodeSelector keys were lost in the merge ({selector})")
            expect(PLACEMENT_SELECTOR not in selector, "muster: scheduling.singletons overwrote the component's own capacity-type key")
        else:
            expect(PLACEMENT_SELECTOR in selector, f"{name}: scheduling.singletons.nodeSelector did not reach {'.'.join((release,) + prefix + ('nodeSelector',))} ({selector})")
        expect(all(l in tolerations for l in PLACEMENT_TOLERATION), f"{name}: scheduling.singletons.tolerations did not reach the release ({tolerations})")
        if name == "kagent":
            expect(all(l in tolerations for l in KAGENT_OWN_TOLERATION), "kagent: the controller's own toleration was lost in the merge")
            expect(tolerations.index("key: own") < tolerations.index("key: dedicated"), "kagent: the component's own tolerations must come first")
    connectivity = values(helm_release(manifest, "agent-platform-connectivity"))
    expect(not block(connectivity, "scheduling"), "connectivity: the scheduling block leaked into the connectivity release (components.agent-platform-connectivity.omitKeys)")
    for release in ("muster", "valkey", "kagent", "klaus-gateway"):
        expect(not block(values(helm_release(manifest, release)), "scheduling"), f"{release}: the scheduling block leaked into a component release")


def check_valkey(manifest: str, off: bool) -> None:
    """muster-valkey: the annotation through the subchart's knob, the budget through the connectivity release only."""
    valkey = values(helm_release(manifest, "valkey"))
    ann = stripped(valkey, "valkey", "podAnnotations")
    if off:
        expect(ANNOTATION not in ann, "valkey: do-not-disrupt still travels with the key set to null")
    else:
        expect(ANNOTATION in ann, "valkey: no karpenter.sh/do-not-disrupt on valkey.valkey.podAnnotations")
    expect(not block(valkey, "podDisruptionBudget"), "valkey: podDisruptionBudget reached the valkey release (components.valkey.omitKeys must hold it back — the wrapper reads nothing there)")
    connectivity = values(helm_release(manifest, "agent-platform-connectivity"))
    pdb = stripped(connectivity, "valkey", "podDisruptionBudget")
    expect(pdb, "connectivity: valkey.podDisruptionBudget not forwarded")
    expect(f"enabled: {str(not off).lower()}" in pdb, f"connectivity: valkey.podDisruptionBudget.enabled is not {not off}")
    expect("maxUnavailable: 1" in pdb, "connectivity: valkey.podDisruptionBudget.maxUnavailable is not 1 (a single replica must stay drainable)")
    expect("minAvailable: 1" not in pdb, "connectivity: valkey.podDisruptionBudget still forwards minAvailable: 1")
    expect("unhealthyPodEvictionPolicy: AlwaysAllow" in pdb, "connectivity: the valkey budget does not keep unhealthy pods evictable")


def check_busy_worker(manifest: str, off: bool) -> None:
    """The substrate release carries the busy-worker marking — or, switched off, nothing."""
    ann = stripped(values(helm_release(manifest, "substrate")), "ateController", "busyWorker", "annotations")
    if off:
        expect(ANNOTATION not in ann, "substrate: the busy-worker marking still travels with the key set to null")
    else:
        expect(ANNOTATION in ann, "substrate: no karpenter.sh/do-not-disrupt on ateController.busyWorker.annotations (a busy worker's node would be consolidated under its actors)")


def check_muster_ha(muster: list[str], off: bool) -> None:
    """muster runs two replicas, preferably on two nodes, behind a budget that keeps one serving."""
    expect("replicaCount: 2" in muster, "muster: replicaCount is not 2 (one replica leaves no endpoint while its pod is evicted)")
    term = stripped(muster, "affinity", "podAntiAffinity", "preferredDuringSchedulingIgnoredDuringExecution")
    expect(term, "muster: no preferred pod anti-affinity (the two replicas could share one node)")
    expect("topologyKey: kubernetes.io/hostname" in term, f"muster: the anti-affinity does not spread over kubernetes.io/hostname ({term})")
    expect("app.kubernetes.io/name: muster" in term, f"muster: the anti-affinity does not select the muster pods by name ({term})")
    expect(not block(muster, "affinity", "podAntiAffinity", "requiredDuringSchedulingIgnoredDuringExecution"), "muster: a required anti-affinity leaves the second replica Pending on a cluster with one schedulable node")
    pdb = stripped(muster, "podDisruptionBudget")
    expect(pdb, "muster: no podDisruptionBudget forwarded")
    expect(f"enabled: {str(not off).lower()}" in pdb, f"muster: podDisruptionBudget.enabled is not {not off}")
    expect("minAvailable: 1" in pdb, f"muster: podDisruptionBudget.minAvailable is not 1 (an eviction must leave one replica serving) ({pdb})")
    expect(not any(l.startswith("maxUnavailable") for l in pdb), f"muster: podDisruptionBudget forwards maxUnavailable next to minAvailable ({pdb})")


def main(argv: list[str]) -> int:
    off = "--off" in argv
    placement = "--placement" in argv
    path = [a for a in argv if not a.startswith("--")][0]
    manifest = open(path, encoding="utf-8").read()
    if placement:
        check_placement(manifest, on=True)
        print("ok: scheduling.singletons reaches muster, muster-valkey, kagent-controller and klaus-gateway as their charts' nodeSelector / tolerations (own keys win, own tolerations first) and never the connectivity release")
        return 0
    check_placement(manifest, on=False)
    check_valkey(manifest, off)
    check_busy_worker(manifest, off)

    muster = values(helm_release(manifest, "muster"))
    kagent = values(helm_release(manifest, "kagent"))
    klaus = values(helm_release(manifest, "klaus-gateway"))

    klaus_pdb = [l.strip() for l in block(klaus, "podDisruptionBudget")]
    expect(klaus_pdb, "klaus-gateway: no podDisruptionBudget forwarded")
    expect(f"enabled: {str(not off).lower()}" in klaus_pdb, f"klaus-gateway: podDisruptionBudget.enabled is not {not off}")
    expect("maxUnavailable: 1" in klaus_pdb, "klaus-gateway: podDisruptionBudget.maxUnavailable is not 1 (a single replica must stay drainable)")
    expect('minAvailable: ""' in klaus_pdb, "klaus-gateway: podDisruptionBudget.minAvailable must travel as the empty string (a null never reaches the chart and its minAvailable: 1 default would come back next to maxUnavailable)")
    expect("unhealthyPodEvictionPolicy: AlwaysAllow" in klaus_pdb, "klaus-gateway: the budget does not keep unhealthy pods evictable")
    check_muster_ha(muster, off)

    controller = [l.strip() for l in block(kagent, "controller", "pdb")]
    expect(controller, "kagent: no controller.pdb forwarded")
    expect(f"enabled: {str(not off).lower()}" in controller, f"kagent: controller.pdb.enabled is not {not off}")
    expect(not any(l.startswith(("minAvailable", "maxUnavailable")) for l in controller), "kagent: controller.pdb forwards minAvailable or maxUnavailable; the chart's own default, maxUnavailable: 1, keeps the single replica drainable")
    expect("unhealthyPodEvictionPolicy: AlwaysAllow" in controller, "kagent: the controller budget does not keep unhealthy pods evictable")

    muster_ann = [l.strip() for l in block(muster, "podAnnotations")]
    kagent_ann = [l.strip() for l in block(kagent, "controller", "podAnnotations")]
    klaus_ann = [l.strip() for l in block(klaus, "podAnnotations")]
    expect("application.giantswarm.io/team: bumblebee" in muster_ann, "muster: the team annotation was lost")
    expect(ANNOTATION not in muster_ann, "muster: karpenter.sh/do-not-disrupt travels next to two replicas and their budget; it would pin both pods' nodes against consolidation, drift and expiry and the budget would never be reached")
    expect(ANNOTATION in kagent_ann, "kagent: no karpenter.sh/do-not-disrupt on controller.podAnnotations")
    expect(ANNOTATION in klaus_ann, "klaus-gateway: no karpenter.sh/do-not-disrupt on podAnnotations")

    # The umbrella-owned knobs are the connectivity release's business, not the component charts'.
    for name, vals in (("muster", muster), ("kagent", kagent), ("klaus-gateway", klaus)):
        expect(not block(vals, "agentManager"), f"{name}: the agentManager wiring block leaked into the component release")
    connectivity = values(helm_release(manifest, "agent-platform-connectivity"))
    expect([l.strip() for l in block(connectivity, "agentManager", "podDisruptionBudget")], "connectivity: agentManager.podDisruptionBudget not forwarded")
    # The data plane is HA rather than undisruptable (two replicas, a budget and a
    # hostname spread; verify-dataplane-ha owns that shape), so the meta chart must
    # NOT forward #431's annotation onto it: it would pin both pods' nodes against
    # Karpenter's consolidation, drift and expiry and the budget would never be reached.
    expect(ANNOTATION not in [l.strip() for l in block(connectivity, "gateway", "parameters", "podAnnotations")], "connectivity: the meta chart still forwards karpenter.sh/do-not-disrupt onto the HA data plane")

    print("ok: muster runs two replicas spread over nodes behind minAvailable: 1 without do-not-disrupt; kagent-controller, klaus-gateway and muster-valkey carry do-not-disrupt, busy Substrate workers are marked (the HA data plane is not) and their budgets travel where their charts read them" + (" (switched off)" if off else "") + "; scheduling.singletons empty forwards no placement")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
