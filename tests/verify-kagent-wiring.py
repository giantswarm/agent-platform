#!/usr/bin/env python3
"""Assert the kagent component's forwarded values match the 0.2.x chart.

kagent 0.2.0 flattened the upstream chart onto its chart root, so the
meta-package forwards the block un-nested and drops its own keys from it. The
mistakes below fail only at reconcile time, in the child HelmRelease, because
the chart validates values with additionalProperties: false:

  * putting `valuesKey: kagent` back, which hands the chart a block it rejects
    as an unknown property;
  * forwarding an umbrella-only key (`controllerRoute`, `uiRoute`,
    `modelConfigs`, `remoteMcpServers`, `serviceMonitor`), which only the
    connectivity chart reads, or the component toggle `enabled`;
  * dropping `fullnameOverride` or `namespaceOverride`, which the chart AND the
    connectivity chart read, so the two would name different objects.

Reads a rendered meta-package manifest. Deliberately stdlib-only: the CI image
has no PyYAML.
"""

import sys

VALUES_INDENT = "    "
OMITTED = ("controllerRoute", "enabled", "modelConfigs", "remoteMcpServers", "serviceMonitor", "uiRoute")


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
    values = forwarded_values(helm_release(open(path, encoding="utf-8").read(), "kagent"))

    if "kagent" in values:
        sys.exit("FAIL: kagent values still nested under a kagent key; the 0.2.x chart is flat")
    for key in OMITTED:
        if key in values:
            sys.exit(f"FAIL: `{key}` forwarded to the kagent chart, whose schema is additionalProperties:false")
    if "fullnameOverride" not in values or "namespaceOverride" not in values:
        sys.exit("FAIL: kagent values lost fullnameOverride or namespaceOverride, which the connectivity chart also reads")
    if "repository: kagent-controller" not in values.get("controller", []):
        sys.exit("FAIL: kagent values lost controller.image.repository")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
