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
onto a rebooted node waits for a controller to answer.

And it holds the images: the block names the agentgateway line's release IN
FULL — controller.image and proxy.image at the line's nested repositories
(giantswarm/agentgateway-upstream/{controller,agentgateway}) with one bare
X.Y.Z tag for both — so the packaging chart's defaults cannot move them (a
value wins over a chart default; giantswarm/agent-platform#608).

Reads a rendered meta-package manifest. Deliberately stdlib-only: the CI image
has no PyYAML.
"""

import re
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
        if line.startswith(VALUES_INDENT) and not line[len(VALUES_INDENT)].isspace():
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
    images = {}
    for name, block in (("controller", controller), ("proxy", values.get("proxy", []))):
        stripped = [l.strip() for l in block]
        repo = next((l.split(": ", 1)[1] for l in stripped if l.startswith("repository: ")), None)
        tag = next((l.split(": ", 1)[1].strip("\"'") for l in stripped if l.startswith("tag: ")), None)
        images[name] = (repo, tag)
    for name, want in (("controller", "giantswarm/agentgateway-upstream/controller"), ("proxy", "giantswarm/agentgateway-upstream/agentgateway")):
        repo, tag = images[name]
        if repo != want:
            sys.exit(f"FAIL: agentgateway {name}.image.repository is {repo!r}, not {want!r}: the agentgateway line's image under its nested name (giantswarm/agent-platform#608)")
        if not tag or not re.fullmatch(r"\d+\.\d+\.\d+", tag):
            sys.exit(f"FAIL: agentgateway {name}.image.tag is {tag!r}, not a release of the line's stable semver (a bare X.Y.Z — no v, no -gs.N): the meta chart names the release in full so the packaging chart's defaults cannot move it (giantswarm/agent-platform#608)")
    if images["controller"][1] != images["proxy"][1]:
        sys.exit(f"FAIL: the controller ({images['controller'][1]}) and the data plane ({images['proxy'][1]}) name different releases of the line; they move together")
    replicas = direct_child(controller, "replicaCount")
    if replicas is None or not replicas.isdigit() or int(replicas) < 2:
        sys.exit(
            "FAIL: controller.replicaCount is not forwarded at 2 or more (got "
            f"{replicas if replicas is not None else 'nothing'}); a lone controller pod leaves a data-plane pod that "
            "starts on a rebooted node without an xDS server until the controller is rescheduled"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
