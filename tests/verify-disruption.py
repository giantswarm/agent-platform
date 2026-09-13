#!/usr/bin/env python3
"""Assert the voluntary-disruption knobs the meta chart forwards to the component
charts (giantswarm/agent-platform#431): karpenter.sh/do-not-disrupt on the pods
of muster, kagent-controller and klaus-gateway through each chart's
podAnnotations, and each chart's PodDisruptionBudget knob on — muster's
podDisruptionBudget, kagent's controller.pdb (minAvailable with maxUnavailable
emptied, because the chart defaults to maxUnavailable: 1 and refuses both, and a
null set at this layer is consumed by Helm before it reaches the chart), the
klaus-gateway chart's podDisruptionBudget (1.0.4+).

Reads a rendered meta-package manifest; with --off, the render with every knob
switched off, and asserts the switches travelled. Deliberately stdlib-only: the
CI image has no PyYAML.
"""

import sys

VALUES_INDENT = "    "
ANNOTATION = 'karpenter.sh/do-not-disrupt: "true"'


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
    """The lines nested under a key path (top-level key first)."""
    lines, indent = vals, ""
    for key in path:
        found = None
        for i, line in enumerate(lines):
            if line == f"{indent}{key}:":
                found = i
                break
        if found is None:
            return []
        indent += "  "
        nested = []
        for line in lines[found + 1 :]:
            if line.strip() and not line.startswith(indent):
                break
            nested.append(line)
        lines = nested
    return lines


def expect(cond: bool, msg: str) -> None:
    if not cond:
        sys.exit(f"FAIL: {msg}")


def main(argv: list[str]) -> int:
    off = "--off" in argv
    path = [a for a in argv if not a.startswith("--")][0]
    manifest = open(path, encoding="utf-8").read()

    muster = values(helm_release(manifest, "muster"))
    kagent = values(helm_release(manifest, "kagent"))
    klaus = values(helm_release(manifest, "klaus-gateway"))

    for name, vals in (("muster", muster), ("klaus-gateway", klaus)):
        pdb = [l.strip() for l in block(vals, "podDisruptionBudget")]
        expect(pdb, f"{name}: no podDisruptionBudget forwarded")
        expect(f"enabled: {str(not off).lower()}" in pdb, f"{name}: podDisruptionBudget.enabled is not {not off}")
        expect("minAvailable: 1" in pdb, f"{name}: podDisruptionBudget.minAvailable is not 1")
        expect(not any(l.startswith("maxUnavailable") for l in pdb), f"{name}: maxUnavailable travels next to minAvailable")
    klaus_pdb = [l.strip() for l in block(klaus, "podDisruptionBudget")]
    expect("unhealthyPodEvictionPolicy: AlwaysAllow" in klaus_pdb, "klaus-gateway: the budget does not keep unhealthy pods evictable")

    controller = [l.strip() for l in block(kagent, "controller", "pdb")]
    expect(controller, "kagent: no controller.pdb forwarded")
    expect(f"enabled: {str(not off).lower()}" in controller, f"kagent: controller.pdb.enabled is not {not off}")
    expect("minAvailable: 1" in controller, "kagent: controller.pdb.minAvailable is not 1")
    expect('maxUnavailable: ""' in controller, "kagent: controller.pdb.maxUnavailable must travel as the empty string (a null never reaches the chart and its maxUnavailable: 1 default would come back next to minAvailable)")
    expect("unhealthyPodEvictionPolicy: AlwaysAllow" in controller, "kagent: the controller budget does not keep unhealthy pods evictable")

    muster_ann = [l.strip() for l in block(muster, "podAnnotations")]
    kagent_ann = [l.strip() for l in block(kagent, "controller", "podAnnotations")]
    klaus_ann = [l.strip() for l in block(klaus, "podAnnotations")]
    expect("application.giantswarm.io/team: bumblebee" in muster_ann, "muster: the team annotation was lost")
    if off:
        expect(ANNOTATION not in muster_ann, "muster: do-not-disrupt still travels with the key set to null")
    else:
        expect(ANNOTATION in muster_ann, "muster: no karpenter.sh/do-not-disrupt annotation")
    expect(ANNOTATION in kagent_ann, "kagent: no karpenter.sh/do-not-disrupt on controller.podAnnotations")
    expect(ANNOTATION in klaus_ann, "klaus-gateway: no karpenter.sh/do-not-disrupt on podAnnotations")

    # The umbrella-owned knobs are the connectivity release's business, not the component charts'.
    for name, vals in (("muster", muster), ("kagent", kagent), ("klaus-gateway", klaus)):
        expect(not block(vals, "agentManager"), f"{name}: the agentManager wiring block leaked into the component release")
    connectivity = values(helm_release(manifest, "agent-platform-connectivity"))
    expect([l.strip() for l in block(connectivity, "agentManager", "podDisruptionBudget")], "connectivity: agentManager.podDisruptionBudget not forwarded")
    expect(ANNOTATION in [l.strip() for l in block(connectivity, "gateway", "parameters", "podAnnotations")], "connectivity: gateway.parameters.podAnnotations not forwarded")

    print("ok: muster, kagent-controller and klaus-gateway carry do-not-disrupt and their charts' budgets" + (" (switched off)" if off else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
