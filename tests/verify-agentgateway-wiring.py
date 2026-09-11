#!/usr/bin/env python3
"""Assert the agentgateway component's forwarded values match the 2.x chart.

agentgateway 2.0.0 flattened the upstream chart onto its chart root, so the
meta-package forwards the block un-nested. Two mistakes are easy to make and
both fail only at reconcile time, in the child HelmRelease:

  * putting `valuesKey: agentgateway` back, which hands the chart a block it
    rejects as an unknown property;
  * forwarding `enabled`, which is this umbrella's toggle and not one of the
    chart's keys. The chart validates values with additionalProperties: false.

It also holds the controller at two or more replicas (controller.replicaCount):
the data plane fetches its config over xDS, and a data-plane pod rescheduled
onto a rebooted node waits for a controller to answer — and holds the budget and
the spread that keep a drain or the scheduler from taking both:
controller.podDisruptionBudget with maxUnavailable 1, and a root
topologySpreadConstraints entry on kubernetes.io/hostname selecting the
controller pods by the chart's own label (agentgateway: agentgateway). The
packaging chart's schema accepts those keys from 2.1.2, the floor of
components.agentgateway.versionRange.

Reads a rendered meta-package manifest. Deliberately stdlib-only: the CI image
has no PyYAML.
"""

import sys

VALUES_INDENT = "    "
# One YAML nesting step inside the forwarded block (`toYaml | nindent 4`).
NEST = "  "


def helm_release(manifest: str, name: str) -> list[str]:
    for doc in manifest.split("\n---\n"):
        lines = doc.split("\n")
        if "kind: HelmRelease" in lines and f"  name: {name}" in lines:
            return lines
    sys.exit(f"FAIL: no {name} HelmRelease in the render")


def forwarded_values(lines: list[str]) -> dict[str, list[str]]:
    """The spec.values block, as top-level key -> its nested lines, indentation kept."""
    values: dict[str, list[str]] = {}
    key = None
    for line in lines[lines.index("  values:") + 1 :]:
        if line and not line.startswith(VALUES_INDENT):
            break
        # toYaml puts a list's items flush with the key that holds them, so a
        # `- ` at the values indent is the current key's item, not a new key.
        if line.startswith(VALUES_INDENT) and not line[len(VALUES_INDENT)].isspace() and not line[len(VALUES_INDENT):].startswith("- "):
            key = line.strip().rstrip(":").split(":")[0]
            values[key] = []
        elif key:
            values[key].append(line)
    return values


def direct_child(block: list[str], name: str) -> str | None:
    """The scalar value of a DIRECT child of one forwarded top-level key.

    Anchored on that child's own indentation, so a same-named key nested
    deeper -- a `replicaCount` under `controller.horizontalPodAutoscaler`,
    say -- cannot answer for it. Quotes are stripped: a YAML `"2"` is the
    same replica count as a bare 2.
    """
    prefix = VALUES_INDENT + NEST + name + ":"
    for line in block:
        if line.startswith(prefix):
            return line[len(prefix) :].strip().strip("\"'")
    return None


def main(path: str) -> int:
    values = forwarded_values(helm_release(open(path, encoding="utf-8").read(), "agentgateway"))

    if "agentgateway" in values:
        sys.exit("FAIL: agentgateway values still nested under an agentgateway key; the 2.x chart is flat")
    if "enabled" in values:
        sys.exit("FAIL: `enabled` forwarded to the agentgateway chart, whose schema is additionalProperties:false")
    controller = values.get("controller", [])
    if "repository: giantswarm/agentgateway-controller" not in [l.strip() for l in controller]:
        sys.exit("FAIL: agentgateway values lost controller.image.repository")
    replicas = direct_child(controller, "replicaCount")
    if replicas is None or not replicas.isdigit() or int(replicas) < 2:
        sys.exit(
            "FAIL: controller.replicaCount is not forwarded at 2 or more (got "
            f"{replicas if replicas is not None else 'nothing'}); a lone controller pod leaves a data-plane pod that "
            "starts on a rebooted node without an xDS server until the controller is rescheduled"
        )
    stripped = [l.strip() for l in controller]
    if "podDisruptionBudget:" not in stripped or "maxUnavailable: 1" not in stripped:
        sys.exit(
            "FAIL: controller.podDisruptionBudget with maxUnavailable: 1 is not forwarded; a drain could evict both "
            "controller pods at once and leave the data plane without xDS"
        )
    spread = [l.strip() for l in values.get("topologySpreadConstraints", [])]
    if "topologyKey: kubernetes.io/hostname" not in spread:
        sys.exit("FAIL: no root topologySpreadConstraints entry on kubernetes.io/hostname; both controller pods may land on the node that reboots")
    if "agentgateway: agentgateway" not in spread:
        sys.exit("FAIL: the controller spread does not select the controller pods by the chart's label (agentgateway: agentgateway); the constraint would select nothing")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
