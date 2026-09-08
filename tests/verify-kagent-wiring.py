#!/usr/bin/env python3
"""Assert the kagent component's forwarded values match the 0.2.x chart.

kagent 0.2.0 flattened the upstream chart onto its chart root, so the
meta-package forwards the block un-nested and drops its own keys from it. The
mistakes below fail only at reconcile time, in the child HelmRelease, because
the chart validates values with additionalProperties: false:

  * putting `valuesKey: kagent` back, which hands the chart a block it rejects
    as an unknown property;
  * forwarding an umbrella-only key, one the connectivity chart reads under
    `.Values.kagent` and the kagent chart does not define (controllerRoute,
    uiRoute, modelConfigs, remoteMcpServers, serviceMonitor,
    oauth2ProxyIngress, ...), or the component toggle `enabled`. The list is
    derived from the connectivity templates, so a new wiring key must be added
    to `components.kagent.omitKeys` before this passes;
  * dropping `fullnameOverride` or `namespaceOverride`, which the chart AND the
    connectivity chart read, so the two would name different objects.

Reads a rendered meta-package manifest. Deliberately stdlib-only: the CI image
has no PyYAML.
"""

import pathlib
import re
import sys

VALUES_INDENT = "    "
UMBRELLA_VALUES = pathlib.Path("helm/agent-platform/values.yaml")
CONNECTIVITY_TEMPLATES = pathlib.Path("helm/agent-platform-connectivity/templates")
# Keys the connectivity chart reads under .Values.kagent that ARE upstream kagent
# keys, so they must keep being forwarded. Everything else it reads there is
# umbrella-only and must be in omitKeys.
UPSTREAM_KEYS = {"fullnameOverride", "namespaceOverride"}
KAGENT_READ = re.compile(r'\.Values\.kagent\.([A-Za-z0-9_-]+)|dig "([A-Za-z0-9_-]+)"[^\n]*\.Values\.kagent\b')


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


def connectivity_reads() -> set[str]:
    """Every key the connectivity chart reads under .Values.kagent."""
    keys: set[str] = set()
    for path in CONNECTIVITY_TEMPLATES.rglob("*"):
        if not path.is_file():
            continue
        for direct, dug in KAGENT_READ.findall(path.read_text(encoding="utf-8")):
            keys.add(direct or dug)
    return keys


def omit_keys() -> set[str]:
    """components.kagent.omitKeys, read from the umbrella values without PyYAML."""
    lines = UMBRELLA_VALUES.read_text(encoding="utf-8").split("\n")
    start = lines.index("  kagent:  # @schema additionalProperties: true")
    keys: set[str] = set()
    collecting = False
    for line in lines[start + 1 :]:
        if line.startswith("  ") and not line.startswith("   ") and line.strip():
            break
        if line.strip() == "omitKeys:":
            collecting = True
            continue
        if collecting:
            if line.startswith("      - "):
                keys.add(line.strip()[2:])
            else:
                collecting = False
    return keys


def main(path: str) -> int:
    values = forwarded_values(helm_release(open(path, encoding="utf-8").read(), "kagent"))
    omitted = omit_keys()
    umbrella_only = connectivity_reads() - UPSTREAM_KEYS

    missing = sorted(umbrella_only - omitted)
    if missing:
        sys.exit(
            f"FAIL: the connectivity chart reads kagent.{', kagent.'.join(missing)} but components.kagent.omitKeys "
            "does not drop them; the flattened kagent chart rejects them"
        )
    if "kagent" in values:
        sys.exit("FAIL: kagent values still nested under a kagent key; the 0.2.x chart is flat")
    for key in sorted(omitted | {"enabled"}):
        if key in values:
            sys.exit(f"FAIL: `{key}` forwarded to the kagent chart, whose schema is additionalProperties:false")
    if "fullnameOverride" not in values or "namespaceOverride" not in values:
        sys.exit("FAIL: kagent values lost fullnameOverride or namespaceOverride, which the connectivity chart also reads")
    if "repository: kagent-controller" not in values.get("controller", []):
        sys.exit("FAIL: kagent values lost controller.image.repository")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
