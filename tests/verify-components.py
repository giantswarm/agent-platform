#!/usr/bin/env python3
"""Assert the roster entries of the standalone chart's extras.

Backstage, mcp-kubernetes, the CloudNativePG operator and the four KServe charts
joined `components.*` off by default. Each case below pins one property of that
roster that the fleet, the quick-start values or the connectivity wiring rely on:

- off by default: no release, no dangling `dependsOn`, the roster forwarded to
  the connectivity release says `enabled: false`, and the connectivity render is
  the same as before they existed;
- on: one OCIRepository + one HelmRelease each, at the documented OCI source and
  version range, with `global` injected, the standalone's defaults forwarded, no
  `crds:` policy (none of the seven ships a crds/ dir), and the CRD-before-CR
  order in `dependsOn` — kserve-crd before the KServe controllers, the operator
  and control plane before their CR consumers (connectivity, model-manager);
- the customer BOM pins every one of them exactly;
- the values tree the meta chart forwards to the connectivity release validates
  against the connectivity chart's schema with the seven off and on. The
  connectivity root schema is additionalProperties: false, so a top-level block
  the meta chart forwards but the connectivity chart does not declare fails the
  connectivity release on every installation — for as long as the fleet's
  connectivity OCIRepository has not re-resolved to a chart that declares it;
- every top-level key of the meta chart's schema except gitops is a key of the
  connectivity chart's schema, for the same reason.

Deliberately stdlib-only: the CI image has no PyYAML.
"""

import json
import re
import subprocess
import sys
import tempfile

GSOCI = "oci://gsoci.azurecr.io/charts/giantswarm"

# component -> (repository, versionRange, dependsOn, a line only the standalone's
# defaults put into the forwarded values, or None when the block is empty)
NEW = {
    "backstage": (GSOCI, "0.x", ["cloudnative-pg"], "configMapRef: agent-platform-backstage-app-config"),
    "mcp-kubernetes": (GSOCI, ">=1.1.1 <2.0.0", [], "fullnameOverride: mcp-kubernetes"),
    "cloudnative-pg": ("oci://ghcr.io/cloudnative-pg/charts", "0.29.x", [], None),
    "kserve-crd": (GSOCI, "0.2.x", [], None),
    "kserve-resources": (GSOCI, "0.2.x", ["kserve-crd"], "deploymentMode: Standard"),
    "kserve-llmisvc-crd": (GSOCI, "0.2.x", [], None),
    "kserve-llmisvc-resources": (
        GSOCI, "0.2.x", ["kserve-crd", "kserve-llmisvc-crd", "kserve-resources"], "createSharedResources: false",
    ),
}

# CR consumers that come after the operator / control plane when those are on.
CONSUMERS = {
    "agent-platform-connectivity": ["cloudnative-pg", "kserve-resources"],
    "model-manager": ["kserve-resources"],
}

# Blocks held back from the connectivity release (components.agent-platform-
# connectivity.omitKeys) until the connectivity chart reads them. When a slice
# adds wiring that reads one, drop it from omitKeys in values.yaml AND from this
# list; the connectivity values.yaml already declares all seven.
HELD_BACK = sorted(NEW)

ON = [f"--set=components.{n}.enabled=true" for n in NEW]
PARENT_REF = ["--set", "ingress.parentRefs[0].name=x"]


def render(chart: str, flags: list[str]) -> str:
    result = subprocess.run(["helm", "template", "t", chart, *flags], capture_output=True, text=True, check=False)
    if result.returncode != 0:
        sys.exit(f"FAIL: render of {chart} {' '.join(flags)} failed\n{result.stderr}")
    return result.stdout


def docs(manifest: str) -> dict[tuple[str, str], str]:
    out = {}
    for d in manifest.split("\n---\n"):
        kind = re.search(r"^kind: (\S+)", d, re.M)
        name = re.search(r"^  name: (\S+)", d, re.M)
        if kind and name:
            out[(kind.group(1), name.group(1))] = d
    return out


def depends_on(doc: str) -> list[str]:
    m = re.search(r"^  dependsOn:\n((?:    - name: \S+\n)+)", doc, re.M)
    return re.findall(r"- name: (\S+)", m.group(1)) if m else []


def hr_values(doc: str) -> str:
    body = doc[doc.index("\n  values:\n") + len("\n  values:\n"):]
    return "\n".join(line[4:] if line.startswith("    ") else line for line in body.splitlines())


def roster(conn_values: str) -> dict[str, bool]:
    m = re.search(r"^components:\n((?:  .*\n)+)", conn_values + "\n", re.M)
    if not m:
        sys.exit("FAIL: the connectivity release carries no components roster")
    return {k: v == "true" for k, v in re.findall(r"^  (\S+):\n    enabled: (true|false)$", m.group(1), re.M)}


def fail(msg: str) -> None:
    sys.exit(f"FAIL: {msg}")


def main(meta: str, connectivity: str) -> int:
    ci = ["-f", f"{meta}/ci/ci-values.yaml"]

    # --- off by default -------------------------------------------------------
    off = docs(render(meta, ci))
    conn_off = hr_values(off[("HelmRelease", "agent-platform-connectivity")])
    ro = roster(conn_off)
    for name in NEW:
        for kind in ("OCIRepository", "HelmRelease"):
            if (kind, name) in off:
                fail(f"components.{name} is not off by default: its {kind} rendered with the CI values")
        if ro.get(name) is not False:
            fail(f"the roster forwarded to connectivity lacks {name}: enabled: false (got {ro.get(name)!r})")
        if re.search(rf"^{re.escape(name)}:", conn_off, re.M):
            fail(f"the {name} block reached the connectivity release while held back (omitKeys)")
    for (kind, name), d in off.items():
        if kind == "HelmRelease":
            dangling = [x for x in depends_on(d) if x in NEW]
            if dangling:
                fail(f"{name} dependsOn {dangling} while those components are off (would block forever)")
    print("ok: the seven are off by default — no release, no dangling dependsOn, roster says false, blocks held back")

    # --- all on ---------------------------------------------------------------
    on_manifest = render(meta, [*ci, *ON])
    kinds = set(re.findall(r"^kind: (\S+)$", on_manifest, re.M))
    if kinds - {"OCIRepository", "HelmRelease"}:
        fail(f"the seven-on render is not a pure app-of-apps render: {sorted(kinds)}")
    on = docs(on_manifest)
    conn_on_values = hr_values(on[("HelmRelease", "agent-platform-connectivity")])
    ron = roster(conn_on_values)
    for name, (repo, rng, deps, marker) in NEW.items():
        oci = on.get(("OCIRepository", name))
        hr = on.get(("HelmRelease", name))
        if not oci or not hr:
            fail(f"components.{name}.enabled=true did not render one OCIRepository + one HelmRelease")
        if f"\n  url: {repo}/{name}\n" not in oci:
            fail(f"{name} OCIRepository url is not {repo}/{name}")
        if f'semver: "{rng}"' not in oci:
            fail(f"{name} OCIRepository does not carry versionRange {rng!r} as a value")
        if sorted(depends_on(hr)) != sorted(deps):
            fail(f"{name} dependsOn {depends_on(hr)}, expected {deps}")
        if "crds: " in hr:
            fail(f"{name} carries a crds: policy, but none of the seven ships a crds/ dir (CRDs are templates)")
        vals = hr_values(hr)
        if not re.search(r"^global:$", vals, re.M):
            fail(f"{name} release values carry no injected global (every one of the seven charts accepts it)")
        if marker and marker not in vals:
            fail(f"{name} release values lack the standalone default {marker!r}")
        if ron.get(name) is not True:
            fail(f"the roster forwarded to connectivity does not say {name}: enabled: true")
        if re.search(rf"^{re.escape(name)}:", conn_on_values, re.M) and name in HELD_BACK:
            fail(f"the {name} block reached the connectivity release although it is held back (omitKeys)")
    for consumer, deps in CONSUMERS.items():
        have = depends_on(on[("HelmRelease", consumer)])
        missing = [d for d in deps if d not in have]
        if missing:
            fail(f"{consumer} does not dependsOn {missing} with those components on (got {have})")
    print("ok: seven on — one OCIRepository + HelmRelease each, sources, ranges, defaults, global, CRD-before-CR dependsOn")

    # --- the BOM pins every one exactly ------------------------------------------
    bom_file = open(f"{meta}/examples/customer-bom.yaml").read()
    bom = docs(render(meta, [*ci, "-f", f"{meta}/examples/customer-bom.yaml", *ON]))
    for name in NEW:
        m = re.search(rf"^\s*{re.escape(name)}:\s*\{{\s*versionRange:\s*\"([^\"]+)\"\s*\}}", bom_file, re.M)
        if not m:
            fail(f"examples/customer-bom.yaml does not pin components.{name}.versionRange")
        pin = m.group(1)
        if not re.fullmatch(r"\d+\.\d+\.\d+", pin):
            fail(f"the BOM pin for {name} is not an exact version: {pin!r}")
        if f'semver: "{pin}"' not in bom[("OCIRepository", name)]:
            fail(f"the BOM pin {pin} for {name} did not reach its OCIRepository")
    print("ok: the customer BOM pins all seven exactly")

    # --- the forwarded tree validates against the connectivity chart --------------
    # The meta chart's defaults plus the one input every render needs; the CI
    # values would trip connectivity's ingress-mode guards, which is not the point.
    for label, extra in (("off", []), ("on", ON)):
        tree = hr_values(docs(render(meta, [*PARENT_REF, *extra]))[("HelmRelease", "agent-platform-connectivity")])
        with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as f:
            f.write(tree)
        render(connectivity, ["-f", f.name])
    print("ok: the forwarded values tree (roster included) validates against the connectivity chart, seven off and on")

    # --- schema symmetry ------------------------------------------------------------
    meta_keys = set(json.load(open(f"{meta}/values.schema.json"))["properties"]) - {"gitops"}
    conn_keys = set(json.load(open(f"{connectivity}/values.schema.json"))["properties"])
    extra = sorted(meta_keys - conn_keys)
    if extra:
        fail(
            "meta-chart top-level keys the connectivity schema does not declare: "
            + ", ".join(extra)
            + ". forwardAllValues hands them to the connectivity release, whose root schema is "
            "additionalProperties: false — declare them in the connectivity values.yaml (skipProperties) "
            "or hold them back with components.agent-platform-connectivity.omitKeys"
        )
    for name in NEW:
        if name not in conn_keys:
            fail(f"connectivity values.yaml does not declare the {name} block")
    print("ok: every meta top-level key is declared by the connectivity schema; the seven blocks included")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2]))
