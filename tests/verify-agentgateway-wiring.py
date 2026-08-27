#!/usr/bin/env python3
"""Assert the agentgateway component's forwarded values match the 2.x chart.

agentgateway 2.0.0 flattened the upstream chart onto its chart root, so the
meta-package forwards the block un-nested. Two mistakes are easy to make and
both fail only at reconcile time, in the child HelmRelease:

  * putting `valuesKey: agentgateway` back, which hands the chart a block it
    rejects as an unknown property;
  * forwarding `enabled`, which is this umbrella's toggle and not one of the
    chart's keys. The chart validates values with additionalProperties: false.

Reads a rendered meta-package manifest. Deliberately stdlib-only: the CI image
has no PyYAML.
"""

import sys

VALUES_INDENT = "    "


def helm_release(manifest: str, name: str) -> list[str]:
    for doc in manifest.split("\n---\n"):
        lines = doc.split("\n")
        if "kind: HelmRelease" in lines and f"  name: {name}" in lines:
            return lines
    sys.exit(f"FAIL: no {name} HelmRelease in the render")


def forwarded_values(lines: list[str]) -> dict[str, list[str]]:
    """The spec.values block, as top-level key -> its nested lines."""
    values: dict[str, list[str]] = {}
    key = None
    for line in lines[lines.index("  values:") + 1 :]:
        if line and not line.startswith(VALUES_INDENT):
            break
        if line.startswith(VALUES_INDENT) and not line[len(VALUES_INDENT)].isspace():
            key = line.strip().rstrip(":").split(":")[0]
            values[key] = []
        elif key:
            values[key].append(line.strip())
    return values


def main(path: str) -> int:
    values = forwarded_values(helm_release(open(path, encoding="utf-8").read(), "agentgateway"))

    if "agentgateway" in values:
        sys.exit("FAIL: agentgateway values still nested under an agentgateway key; the 2.x chart is flat")
    if "enabled" in values:
        sys.exit("FAIL: `enabled` forwarded to the agentgateway chart, whose schema is additionalProperties:false")
    if "repository: giantswarm/agentgateway-controller" not in values.get("controller", []):
        sys.exit("FAIL: agentgateway values lost controller.image.repository")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
