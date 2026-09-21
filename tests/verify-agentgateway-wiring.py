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

import re
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
    images = {}
    for name in ("controller", "proxy"):
        block = values.get(name, [])
        repo = next((l.split(": ", 1)[1] for l in block if l.startswith("repository: ")), None)
        tag = next((l.split(": ", 1)[1].strip("\"'") for l in block if l.startswith("tag: ")), None)
        images[name] = (repo, tag)
    for name, want in (("controller", "giantswarm/agentgateway-upstream/controller"), ("proxy", "giantswarm/agentgateway-upstream/agentgateway")):
        repo, tag = images[name]
        if repo != want:
            sys.exit(f"FAIL: agentgateway {name}.image.repository is {repo!r}, not {want!r}: the agentgateway line's image under its nested name")
        if not tag or not re.fullmatch(r"\d+\.\d+\.\d+", tag):
            sys.exit(f"FAIL: agentgateway {name}.image.tag is {tag!r}, not a release of the line's stable semver (a bare X.Y.Z — no v, no -gs.N): the meta chart names the release in full so the packaging chart's defaults cannot move it")
    if images["controller"][1] != images["proxy"][1]:
        sys.exit(f"FAIL: the controller ({images['controller'][1]}) and its default data plane ({images['proxy'][1]}) name different releases of the line; they move together")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
