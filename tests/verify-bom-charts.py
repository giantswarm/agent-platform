#!/usr/bin/env python3
"""Render every chart the BOM pins against the values the meta chart forwards.

`make verify-meta` renders the meta chart only: it proves the HelmReleases and
their values are shaped as intended, never that the CHART on the other end
accepts them. A component chart with `additionalProperties: false` rejects a
key the meta chart forwards unconditionally, and the release then fails to
install or upgrade on every installation — which is invisible to a meta-only
render. That happened once already: `model-manager.lmstudio.*` was forwarded
to every release while the BOM still pinned a model-manager that had no such
key (agent-platform#272).

So for each component the meta render produces, this pulls the chart at the
version and from the registry the rendered OCIRepository names, and renders it
with exactly the values the HelmRelease carries.
`tests/verify-toolset-presets.py` does the same for muster's toolset presets;
this is the BOM-wide form of that check.

Deliberately stdlib-only, as that script is: the CI image has no PyYAML. The
render is split on document separators and each values block is cut by
indentation, which is all the shape of a HelmRelease needs; the BOM's pins are
one line each.
"""

from __future__ import annotations

import json
import pathlib
import re
import subprocess
import sys
import tempfile

# `  <name>: { versionRange: "0.19.0" }` — the BOM's one-line component pins.
PIN_RE = re.compile(r'^\s{2}([a-z0-9][a-z0-9-]*):\s*\{\s*versionRange:\s*"([^"]+)"')

# Every pinned chart that forwards no values is named here, with the reason. A
# chart that drops out of the check for any other reason is a failure, not a
# silent skip: the whole point is that no pinned chart goes unrendered without
# someone saying so.
TIMEOUT = 300


def fail(msg: str) -> None:
    print(f"FAIL: {msg}", file=sys.stderr)
    sys.exit(1)


def run(*args: str) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=TIMEOUT)
    except subprocess.TimeoutExpired:
        fail(f"timed out after {TIMEOUT}s: {' '.join(args)}")


def is_schema_error(err: str) -> bool:
    """Helm validates values against the chart's schema before it renders.

    That is the failure this check exists for: a key the meta chart forwards
    unconditionally which the chart's schema does not allow. A failure later,
    while the templates run, means the values were accepted and the chart
    wanted an input the BOM alone does not carry — a different thing.

    The two are told apart by Helm's message, so `selftest` below proves
    against the local Helm that this still reads it right.
    """
    return ("specifications of the schema" in err
            or "additional propert" in err)


def selftest() -> None:
    """Prove `is_schema_error` still splits the two failures the way this
    check depends on, against the Helm actually installed.

    Without this the check degrades silently: a reworded Helm message turns
    the break it exists for into a passing `needs-input` line.
    """
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp) / "probe"
        (root / "templates").mkdir(parents=True)
        (root / "Chart.yaml").write_text(
            "apiVersion: v2\nname: probe\nversion: 0.0.0\n", encoding="utf-8")
        (root / "values.yaml").write_text('known: "a"\nboom: false\n', encoding="utf-8")
        (root / "values.schema.json").write_text(json.dumps({
            "$schema": "http://json-schema.org/draft-07/schema#",
            "type": "object",
            "properties": {"known": {"type": "string"}, "boom": {"type": "boolean"}},
            "additionalProperties": False,
        }), encoding="utf-8")
        # Fails only when asked to, so the schema rejection is reached first.
        (root / "templates" / "t.yaml").write_text(
            '{{- if .Values.boom }}{{ fail "boom" }}{{ end }}\n'
            "apiVersion: v1\nkind: ConfigMap\nmetadata:\n  name: probe\n",
            encoding="utf-8")

        rejected = run("helm", "template", "probe", str(root), "--set", "unknown=1")
        if rejected.returncode == 0:
            fail("selftest: Helm accepted a value the chart's schema forbids — "
                 "this check cannot detect the break it exists for")
        if not is_schema_error(rejected.stderr):
            fail("selftest: is_schema_error() no longer recognises a Helm schema "
                 f"rejection, so a real break would report as needs-input:\n{rejected.stderr.strip()}")

        template_error = run("helm", "template", "probe", str(root), "--set", "boom=true")
        if template_error.returncode == 0:
            fail("selftest: the probe chart's template failure did not fail the render")
        if is_schema_error(template_error.stderr):
            fail("selftest: is_schema_error() reads a plain template failure as a schema "
                 f"rejection, so this check would fail on charts that are fine:\n{template_error.stderr.strip()}")


def first_line(err: str) -> str:
    return err.splitlines()[0] if err.splitlines() else err


def documents(text: str):
    doc: list[str] = []
    for line in text.splitlines():
        if line.strip() == "---":
            if doc:
                yield "\n".join(doc)
            doc = []
            continue
        doc.append(line)
    if doc:
        yield "\n".join(doc)


def metadata_name(lines: list[str]) -> str:
    """The `metadata.name` of a rendered CR (indent 2, before `spec:`)."""
    for line in lines:
        if line.startswith("spec:"):
            break
        if line.startswith("  name: "):
            return line[len("  name: "):].strip()
    return ""


def values_block(lines: list[str]) -> str:
    """The release's `values:` block (HelmRelease, indent 2) or an Argo
    Application's `valuesObject:`, de-indented to a values file. Empty when
    the release forwards nothing."""
    for key in ("  values:", "      valuesObject:"):
        indent = len(key) - len(key.lstrip())
        for i, line in enumerate(lines):
            if line != key:
                continue
            block = []
            for inner in lines[i + 1:]:
                if inner.strip() == "":
                    block.append("")
                elif len(inner) - len(inner.lstrip()) > indent:
                    block.append(inner[indent + 2:])
                else:
                    break
            return "\n".join(block).strip("\n")
    return ""


def releases(render: str) -> dict[str, str]:
    """The values each release carries, by release name, for the ones that
    carry any. A release the meta chart forwards nothing to renders `{}`,
    which is no more input than an absent block."""
    out: dict[str, str] = {}
    for doc in documents(render):
        lines = doc.splitlines()
        if not any(l.strip() in ("kind: HelmRelease", "kind: Application") for l in lines):
            continue
        name = metadata_name(lines)
        values = values_block(lines)
        if name and values and values.strip() != "{}":
            out[name] = values + "\n"
    return out


def sources(render: str) -> dict[str, tuple[str, str]]:
    """Each component's chart source from the rendered OCIRepositories:
    name -> (url, version). The registry is read here rather than assumed,
    because the components do not all come from one (kagent and Substrate are
    on ghcr.io, cloudnative-pg too)."""
    out: dict[str, tuple[str, str]] = {}
    for doc in documents(render):
        lines = doc.splitlines()
        if not any(l.strip() == "kind: OCIRepository" for l in lines):
            continue
        name = metadata_name(lines)
        url = next((l.split("url:", 1)[1].strip() for l in lines
                    if l.startswith("  url:")), "")
        version = next((l.split("semver:", 1)[1].strip().strip('"') for l in lines
                        if l.strip().startswith("semver:")), "")
        if name and url and version:
            out[name] = (url, version)
    return out


# An exact version, prerelease included (`0.11.0-gs.12` is one) — as opposed to
# a range, which is not a thing this check can pull.
EXACT_RE = re.compile(r"^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?$")


def pins(bom: pathlib.Path) -> dict[str, str]:
    """The BOM's component pins, only the exact ones (a range is not a
    version): `0.19.0` and `0.11.0-gs.12` yes, `0.x` or `>=1 <2` no."""
    out: dict[str, str] = {}
    in_components = False
    for line in bom.read_text(encoding="utf-8").splitlines():
        if line.startswith("components:"):
            in_components = True
            continue
        if in_components and line and not line.startswith((" ", "#")):
            break
        m = PIN_RE.match(line) if in_components else None
        if m and EXACT_RE.match(m.group(2)):
            out[m.group(1)] = m.group(2)
    return out


def main() -> None:
    if len(sys.argv) != 3:
        fail("usage: verify-bom-charts.py <meta-render> <bom.yaml>")
    render = pathlib.Path(sys.argv[1]).read_text(encoding="utf-8")
    bom = pathlib.Path(sys.argv[2])

    selftest()

    values_by_release = releases(render)
    source_by_name = sources(render)
    pinned = pins(bom)
    if not values_by_release:
        fail("the render carries no HelmRelease values — wrong input file?")
    if not pinned:
        fail(f"{bom} pins no exact component versions")

    # Every exact pin in the BOM must reach a chart this check can pull. A pin
    # with no rendered OCIRepository, or one resolved to another version, means
    # the BOM and the render have drifted apart — the check would then quietly
    # cover less than it reads as covering.
    missing = sorted(n for n in pinned if n not in source_by_name)
    if missing:
        fail("the render has no OCIRepository for BOM-pinned " + ", ".join(missing)
             + " — the BOM and the meta chart's components have drifted apart")
    drifted = sorted(f"{n}: BOM {v}, render {source_by_name[n][1]}"
                     for n, v in pinned.items() if source_by_name[n][1] != v)
    if drifted:
        fail("the render resolves a different version than the BOM pins for "
             + "; ".join(drifted))

    checked, no_values, needs = [], [], []
    with tempfile.TemporaryDirectory() as tmp:
        for name, version in sorted(pinned.items()):
            url, _ = source_by_name[name]
            values = values_by_release.get(name)
            if values is None:
                no_values.append(name)
                continue
            pulled = run("helm", "pull", url, "--version", version,
                         "--untar", "--untardir", f"{tmp}/{name}")
            if pulled.returncode != 0:
                fail(f"{name} {version}: cannot pull {url}\n{pulled.stderr.strip()}")
            vf = pathlib.Path(tmp) / f"{name}-values.yaml"
            vf.write_text(values, encoding="utf-8")
            rendered = run("helm", "template", name, f"{tmp}/{name}/{name}",
                           "--namespace", "agent-platform", "-f", str(vf))
            if rendered.returncode != 0:
                err = rendered.stderr.strip()
                if is_schema_error(err):
                    fail(f"{name} {version} REJECTS the values the meta chart forwards to it "
                         f"— every installation on this BOM would get a release that cannot "
                         f"install or upgrade, whatever the operator sets:\n{err}")
                # Anything else is the chart asking for an input this synthetic
                # render does not supply (a credential the operator provides,
                # say). Not our class of break: the values were accepted, the
                # template merely wanted more. Report it, do not fail on it.
                needs.append(f"{name} {version}: {first_line(err)}")
                continue
            checked.append(f"{name} {version}")

    if no_values:
        print(f"  no forwarded values ({len(no_values)}): " + ", ".join(sorted(no_values)))
    for n in needs:
        print(f"  needs-input: {n}")
    for c in checked:
        print(f"  ok:   {c} accepts its forwarded values")
    print(f"ok: {len(pinned)} BOM pins — {len(checked)} rendered clean, "
          f"{len(needs)} accepted the values but need operator input to render further, "
          f"{len(no_values)} carry no forwarded values to test")


if __name__ == "__main__":
    main()
