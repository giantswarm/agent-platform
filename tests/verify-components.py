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
- the seven blocks REACH the connectivity release (nothing is held back any
  more: the connectivity chart reads them for the wiring it renders — the
  Backstage app-config and route, the mcp-kubernetes MCPServer, the model
  serving objects, the KServe controllers' network policies), off and on;
- the wiring's own keys under backstage: / mcp-kubernetes: (the keys the
  standalone kept under components.<name>) never reach the component chart's
  release (components.<name>.omitKeys — both charts validate strictly);
- components.modelServing is a feature switch: no chart, so no release when on,
  but its answer is in the roster forwarded to connectivity, and its values
  block (modelServing:) travels only while the switch is on;
- every top-level key of the meta chart's schema except gitops is a key of the
  connectivity chart's schema, for the same reason;
- components.<name>.semverFilter reaches OCIRepository.spec.ref.semverFilter
  (a dev channel: the tags a branch's dev builds carry, matched before the range
  is evaluated), for exactly the components the defaults put on a dev channel
  (DEV_CHANNEL) and for a component given one, and for no other; the dead
  ImagePolicy-shaped `filterTags` block never renders.

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
    "backstage": (GSOCI, "0.x", ["agent-platform-connectivity", "cloudnative-pg"], "configMapRef: agent-platform-backstage-app-config"),
    "mcp-kubernetes": (GSOCI, ">=1.1.1 <2.0.0", [], "fullnameOverride: mcp-kubernetes"),
    "cloudnative-pg": ("oci://ghcr.io/cloudnative-pg/charts", "0.29.x", [], None),
    "kserve-crd": (GSOCI, "0.2.x", [], None),
    "kserve-resources": (GSOCI, "0.2.x", ["kserve-crd"], "deploymentMode: Standard"),
    "kserve-llmisvc-crd": (GSOCI, "0.2.x", [], None),
    "kserve-llmisvc-resources": (
        GSOCI, "0.2.x", ["kserve-crd", "kserve-llmisvc-crd", "kserve-resources"], "createSharedResources: false",
    ),
}

# The wiring chart's range: released off the same tag as the meta chart and
# re-resolved by every installation's Flux, so it stays below the next major --
# the kagent API v2 line must not reach a 3.x installation ahead of its cut-over.
CONNECTIVITY_RANGE = ">=1.0.0 <4.0.0"

# CR consumers that come after the operator / control plane when those are on.
CONSUMERS = {
    "agent-platform-connectivity": ["muster", "cloudnative-pg", "kserve-resources"],
    "model-manager": ["kserve-resources"],
}

# The wiring's own keys that live in a component chart's block and must be
# dropped from the values forwarded to that chart (components.<name>.omitKeys).
WIRING_KEYS = {
    "backstage": [
        "hostname", "parentRefs", "installationName", "extraScopes", "startUrlSearchParams",
        "enabledExtensions", "disabledExtensions", "skillsRepositories", "catalogs", "configReload",
    ],
    "mcp-kubernetes": ["kubernetesAudience"],
}

# Feature switches of the roster: an entry without chart:, no release, forwarded
# like every other flag.
SWITCHES = ["modelServing"]

# component -> the semverFilter its default source carries (a dev channel). Every
# other component's OCIRepository renders none. Empty on the stable line.
DEV_CHANNEL: dict[str, str] = {}
# A filter handed to a component that has none by default; the value carries
# the backslashes a real filter has (`\.`), so the quoting is exercised.
PROBE_FILTER = ".*-dev\\.x\\..*"

ON = [f"--set=components.{n}.enabled=true" for n in NEW]
PARENT_REF = ["--set", "ingress.parentRefs[0].name=x"]
# The bundled Flux engine (components.flux.enabled, default true) adds its own
# objects to the render; its two shapes are tests/verify-engine.py's. The roster
# assertions here look at the platform objects, so they render with it off.
ENGINE_OFF = ["--set", "components.flux.enabled=false"]


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


def semver_filters(manifest: str) -> dict[str, str]:
    """OCIRepository name -> its spec.ref.semverFilter, for the ones that carry one."""
    out = {}
    for (kind, name), d in docs(manifest).items():
        if kind != "OCIRepository":
            continue
        if "filterTags" in d:
            fail(f"{name} OCIRepository renders filterTags — an ImagePolicy field, not an OCIRepository one; the key is semverFilter")
        m = re.search(r'^    semverFilter: (".*")$', d, re.M)
        if m:
            out[name] = json.loads(m.group(1))  # Go %q quoting == JSON for these strings
    return out


def check_semver_filters(meta: str, ci: list[str]) -> None:
    every = [*ci, *ON, *[f"--set=components.{n}.enabled=true" for n in SWITCHES]]
    got = semver_filters(render(meta, every))
    if got != DEV_CHANNEL:
        fail(f"OCIRepository semverFilters differ from the dev-channel defaults: rendered {got}, expected {DEV_CHANNEL}")
    probe = render(meta, [*every, "--set-json", f"components.muster.semverFilter={json.dumps(PROBE_FILTER)}"])
    if semver_filters(probe) != {**DEV_CHANNEL, "muster": PROBE_FILTER}:
        fail(f"components.muster.semverFilter did not reach the muster OCIRepository alone: {semver_filters(probe)}")
    if f"semverFilter: {json.dumps(PROBE_FILTER)}" not in probe:
        fail("the semverFilter is not rendered as a double-quoted string with its backslashes escaped (Flux reads it as a Go regexp)")
    print(f"ok: semverFilter — the dev-channel defaults {sorted(DEV_CHANNEL) or 'none'} and no other component; a component given one renders it verbatim; no filterTags")


def main(meta: str, connectivity: str) -> int:
    ci = ["-f", f"{meta}/ci/ci-values.yaml", *ENGINE_OFF]

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
        if not re.search(rf"^{re.escape(name)}:", conn_off, re.M):
            fail(f"the {name} block did not reach the connectivity release (held back by omitKeys?); its wiring reads it")
    for name in SWITCHES:
        if ro.get(name) is not False:
            fail(f"the roster forwarded to connectivity lacks the switch {name}: enabled: false (got {ro.get(name)!r})")
        if re.search(rf"^{re.escape(name)}:", conn_off, re.M):
            fail(f"the {name} block reached the connectivity release while the switch is off (a live chart that predates the block would reject it)")
    for (kind, name), d in off.items():
        if kind == "HelmRelease":
            dangling = [x for x in depends_on(d) if x in NEW]
            if dangling:
                fail(f"{name} dependsOn {dangling} while those components are off (would block forever)")
    print("ok: the seven are off by default — no release, no dangling dependsOn, roster says false, blocks forwarded")

    # --- the wiring chart's range is bounded below the next major ------------------
    conn_oci = off.get(("OCIRepository", "agent-platform-connectivity"))
    if not conn_oci or f'semver: "{CONNECTIVITY_RANGE}"' not in conn_oci:
        fail(f"the connectivity OCIRepository does not carry versionRange {CONNECTIVITY_RANGE!r}: the 3.x line must not follow the wiring chart into the next major (kagent API v2), which every installation re-resolves on each reconcile")
    print(f"ok: the connectivity range is {CONNECTIVITY_RANGE} -- bounded below the next major")

    # --- all on ---------------------------------------------------------------
    on_manifest = render(meta, [*ci, *ON, *[f"--set=components.{n}.enabled=true" for n in SWITCHES]])
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
        if not re.search(rf"^{re.escape(name)}:", conn_on_values, re.M):
            fail(f"the {name} block did not reach the connectivity release (held back by omitKeys?); its wiring reads it")
        for key in WIRING_KEYS.get(name, []):
            if re.search(rf"^{re.escape(key)}:", vals, re.M):
                fail(f"{name} release values carry the wiring key {key}, which the {name} chart rejects; add it to components.{name}.omitKeys")
    for name in SWITCHES:
        for kind in ("OCIRepository", "HelmRelease"):
            if (kind, name) in on:
                fail(f"components.{name} is a feature switch (no chart:) but rendered a {kind}")
        if ron.get(name) is not True:
            fail(f"the roster forwarded to connectivity does not say {name}: enabled: true (a switch is forwarded like a component)")
        if not re.search(rf"^{re.escape(name)}:", conn_on_values, re.M):
            fail(f"the {name} block did not reach the connectivity release with the switch on; its wiring reads it")
    for consumer, deps in CONSUMERS.items():
        have = depends_on(on[("HelmRelease", consumer)])
        missing = [d for d in deps if d not in have]
        if missing:
            fail(f"{consumer} does not dependsOn {missing} with those components on (got {have})")
    print("ok: seven on — one OCIRepository + HelmRelease each, sources, ranges, defaults, global, CRD-before-CR dependsOn, blocks forwarded, wiring keys omitted, the switch renders no release")

    # --- the dev channel: semverFilter ----------------------------------------------
    check_semver_filters(meta, ci)

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
    # With the seven (and the modelServing switch) on, the wiring needs the
    # quick-start inputs Backstage takes by design: global.domain, global.identity
    # and a public Gateway for its route.
    quickstart = [
        "--set", "global.domain=example.com",
        "--set", "global.identity.issuerUrl=https://dex.example.com",
        "--set", "global.identity.clientId=agent-platform",
        "--set", "global.identity.existingSecret=agent-platform-idp",
        "--set", "global.gatewayApi.parentRefs[0].name=gw",
        "--set", "global.gatewayApi.parentRefs[0].namespace=gw-system",
    ]
    switches_on = [f"--set=components.{n}.enabled=true" for n in SWITCHES]
    for label, extra in (("off", []), ("on", [*ON, *switches_on, *quickstart])):
        tree = hr_values(docs(render(meta, [*PARENT_REF, *extra]))[("HelmRelease", "agent-platform-connectivity")])
        with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as f:
            f.write(tree)
        render(connectivity, ["-f", f.name])
    print("ok: the forwarded values tree (roster included) validates against the connectivity chart, seven off and on (with the wiring's quick-start inputs)")

    # --- schema symmetry ------------------------------------------------------------
    # gitops is never forwarded; a block named in the connectivity entry's omitKeys
    # is held back (the flux-engine subchart's values; the seven blocks of the
    # standalone's extras are forwarded — the connectivity chart reads them).
    omit = re.search(r"^    omitKeys:\n((?:      .*\n)+)", open(f"{meta}/values.yaml").read()[open(f"{meta}/values.yaml").read().index("  agent-platform-connectivity:"):], re.M)
    held = set(re.findall(r"^      - (\S+)$", omit.group(1), re.M)) if omit else set()
    meta_keys = set(json.load(open(f"{meta}/values.schema.json"))["properties"]) - {"gitops"} - held
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
