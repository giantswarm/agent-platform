#!/usr/bin/env python3
"""Assert the platform toolset presets reach muster, at two points of the path.

`release-values <render>` reads the meta chart's render, finds the muster
HelmRelease, checks its values carry the `infrastructure`
and `agent-platform` presets selecting by the tool-group label, and prints the
values block as a plain values file so the muster chart itself can be rendered
with exactly what the meta chart forwards.

`configmap <render>` reads a render of the muster chart's ConfigMap and checks
the presets landed in muster's config.yaml, where muster reads them at startup.

`icons <render> on|off` reads the meta chart's render and checks both presets
in the muster HelmRelease values carry an svg `data:` icon (on) or no icon
(off): the chart sets them only from the muster floor that takes the key.

Deliberately stdlib-only: the CI image has no PyYAML. The render is split on
document separators and the values block is cut by indentation, which is all
the shape of a HelmRelease needs.
"""

import sys

LABEL_KEY = "agent-platform.giantswarm.io/tool-group"
PRESETS = {
    "infrastructure": [f"label: {LABEL_KEY}=infrastructure"],
    "agent-platform": [f"label: {LABEL_KEY}=agent-platform", "pattern: core_*"],
}
BUILT_IN = ("read-only", "none", "full")


def fail(msg: str) -> None:
    print(f"FAIL: {msg}", file=sys.stderr)
    sys.exit(1)


def documents(text: str):
    doc = []
    for line in text.splitlines():
        if line.strip() == "---":
            if doc:
                yield "\n".join(doc)
            doc = []
            continue
        doc.append(line)
    if doc:
        yield "\n".join(doc)


def muster_release(text: str) -> str:
    for doc in documents(text):
        lines = doc.splitlines()
        is_release = any(l.strip() == "kind: HelmRelease" for l in lines)
        if is_release and "  name: muster" in lines:
            return doc
    fail("no HelmRelease named muster in the render")
    return ""  # unreachable


def values_block(doc: str) -> str:
    """The `values:` block of a HelmRelease (indent 2), de-indented to a values
    file."""
    lines = doc.splitlines()
    key = "  values:"
    indent = len(key) - len(key.lstrip())
    for i, line in enumerate(lines):
        if line == key:
            block = []
            for inner in lines[i + 1:]:
                if inner.strip() == "" or len(inner) - len(inner.lstrip()) > indent:
                    block.append(inner[indent + 2:] if inner.strip() else "")
                else:
                    break
            return "\n".join(block) + "\n"
    fail("the muster release carries no values block")
    return ""  # unreachable


def assert_presets(text: str, where: str) -> None:
    if "toolsetPresets:" not in text:
        fail(f"{where}: no toolsetPresets key")
    for name, rules in PRESETS.items():
        if f"{name}:" not in text:
            fail(f"{where}: preset {name} missing")
        for rule in rules:
            if rule not in text:
                fail(f"{where}: preset {name} lacks the rule `{rule}`")
        if f"{name}:\n" in text and "description:" not in text:
            fail(f"{where}: preset {name} has no description")
    for name in BUILT_IN:
        # A top-level preset named like a built-in makes muster refuse to start.
        if f"\n  {name}:\n" in text or f"\n      {name}:\n" in text:
            fail(f"{where}: a preset redefines the built-in {name}")


def preset_icon(values: str, name: str) -> str:
    """The icon line of preset `name` under toolsetPresets, "" without one."""
    lines = values.splitlines()
    seen = False
    for i, line in enumerate(lines):
        seen = seen or line.strip() == "toolsetPresets:"
        if seen and line.strip() == f"{name}:":
            indent = len(line) - len(line.lstrip())
            for inner in lines[i + 1:]:
                if inner.strip() and len(inner) - len(inner.lstrip()) <= indent:
                    break
                if inner.strip().startswith("icon:"):
                    return inner.strip()
            return ""
    fail(f"preset {name} missing")
    return ""  # unreachable


def assert_icons(text: str, want: str) -> None:
    values = values_block(muster_release(text))
    for name in PRESETS:
        icon = preset_icon(values, name)
        if want == "on" and not icon.startswith("icon: data:image/svg+xml;base64,"):
            fail(f"preset {name} carries no svg data: icon")
        if want == "off" and icon:
            fail(f"preset {name} carries an icon below the muster floor that takes it")
    print(f"ok: preset icons {want}", file=sys.stderr)


def main() -> None:
    if len(sys.argv) == 4 and sys.argv[1] == "icons" and sys.argv[3] in ("on", "off"):
        assert_icons(open(sys.argv[2], encoding="utf-8").read(), sys.argv[3])
        return
    if len(sys.argv) != 3 or sys.argv[1] not in ("release-values", "configmap"):
        print(__doc__, file=sys.stderr)
        sys.exit(2)
    mode, path = sys.argv[1], sys.argv[2]
    text = open(path, encoding="utf-8").read()
    if mode == "release-values":
        values = values_block(muster_release(text))
        assert_presets(values, "muster release values")
        sys.stdout.write(values)
        print("ok: muster release values carry both presets", file=sys.stderr)
    else:
        if "kind: ConfigMap" not in text:
            fail("the muster render carries no ConfigMap")
        assert_presets(text, "muster ConfigMap")
        print("ok: muster ConfigMap carries both presets", file=sys.stderr)


if __name__ == "__main__":
    main()
