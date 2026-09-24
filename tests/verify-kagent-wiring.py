#!/usr/bin/env python3
"""Assert the kagent component's wiring: the kagent line's charts on their release
range, one build of the line, flat forwarded values with no umbrella-only key.

The kagent chart (upstream's, from the line giantswarm/kagent-upstream) takes
its keys at the chart root and validates nothing, so the meta-package forwards
the block un-nested, drops its own keys from it, and names one build of the line
in three places that have to agree. The mistakes below fail only at reconcile
time, in the child HelmRelease or in the running controller:

  * putting `valuesKey: kagent` back, which nests the block under a key the
    chart never reads;
  * forwarding an umbrella-only key, one the connectivity chart reads under
    `.Values.kagent` and the kagent chart does not define (controllerRoute,
    uiRoute, harness, modelConfigs, remoteMcpServers, serviceMonitor,
    oauth2ProxyIngress, ...), or the component toggle `enabled` — a chart
    without a schema carries them on silently. The list is derived from the
    connectivity templates, so a new wiring key must be added to
    `components.kagent.omitKeys` before this passes;
  * dropping `fullnameOverride` or `namespaceOverride`, which the chart AND the
    connectivity chart read, so the two would name different objects; or
    `providers`, which the chart reads for the default ModelConfig (the LLM
    cutover, `providers.<default>.config.baseUrl`) and the connectivity chart
    reads for the provider key of its own ModelConfigs;
  * a range that admits the line's dev builds, or upstream's next minor, or
    that Flux (Masterminds semver) would not match the line's stable releases against;
  * a kagent-crds source that differs from kagent's, a kagent release that does
    not come after its CRDs, a `crds:` policy on a chart whose CRDs are
    templates, `global` injected into a chart that reads none;
  * an image tag the chart would fall back to (`.Chart.Version`, which under
    helm-controller is `<version>+<digest>`) — since 4.8.0 nothing here names a
    build: the chart stamps its tag and digests, a forwarded tag, workerImage or
    Harness digest is the fault; a range whose floor predates the stamps, or
    upstream versions of the line;
  * the retired 0.10 keys: the ten bundled example agents (dead keys on the
    line — the chart ships none), the `METRICS_*` controller env and a metrics
    Service to a port the line's controller never serves;
  * a kagent release that opts out of helm-controller's take-ownership default
    (install/upgrade disableTakeOwnership) or carries a `takeOwnership` key the
    HelmRelease CRD does not know: the 4.8.0 upgrade adopts the platform Harness
    the connectivity chart (through 4.7.19) left in place through helm.sh/resource-policy: keep
    (giantswarm/agent-platform#406 step 2);
  * a kagent or muster release without drift detection (spec.driftDetection.mode:
    enabled): a plain reconcile of an unchanged release reports "in-sync", so the
    platform Harness a consumer skipping 4.7.19 loses on the 4.8.0 upgrade would
    stay deleted until a values change or a forced reconcile
    (giantswarm/agent-platform#409), and a muster CiliumNetworkPolicy left
    drifted by the agentic-platform -> agent-platform rename would stay drifted
    across every upgrade (giantswarm/agent-platform#287); or drift detection on
    another release by default — a decision per release, its objects must
    tolerate the re-apply.

Reads a rendered meta-package manifest (the CI values: kagent on). Deliberately
stdlib-only: the CI image has no PyYAML.
"""

import pathlib
import re
import sys

import fluxsemver

VALUES_INDENT = "    "
UMBRELLA_VALUES = pathlib.Path("helm/agent-platform/values.yaml")
CONNECTIVITY_TEMPLATES = pathlib.Path("helm/agent-platform-connectivity/templates")
# Keys the connectivity chart reads under .Values.kagent that ARE upstream kagent
# keys, so they must keep being forwarded. Everything else it reads there is
# umbrella-only and must be in omitKeys.
# `controller` is read for controller.auth.userIdClaim: the claim the kagent
# controller derives the caller from (AUTH_USER_ID_CLAIM) is also the claim the
# gateway's identity transformation copies into x-user-id (ONE value).
# substrateWorkerPool and harness are the kagent chart's: it renders the WorkerPool and,
# since 4.8.0, the platform Harness (harness.create) from them. otel is the kagent
# chart's exporter configuration (the controller ConfigMap); the connectivity chart
# reads the same endpoints for the OTLP egress of the controller and of the actors'
# egress gateway (agent-platform.kagent.otlpTargets, giantswarm/agent-platform#456).
UPSTREAM_KEYS = {"fullnameOverride", "namespaceOverride", "providers", "controller", "substrateWorkerPool", "harness", "otel"}
KAGENT_READ = re.compile(r'\.Values\.kagent\.([A-Za-z0-9_-]+)|dig "([A-Za-z0-9_-]+)"[^\n]*\.Values\.kagent\b')

LINE_REPOSITORY = "oci://gsoci.azurecr.io/giantswarm/kagent/helm"
LINE_IMAGES = "giantswarm/kagent"
HARNESS_LABEL = "agent-platform.giantswarm.io/harness"
# What the release range must admit and refuse, by shape: the line's releases
# of the pinned minor — the floor and its patches, which never change a runtime
# contract — and nothing else: not the line's dev builds in gitsemver 3's
# shape (X.Y.Z-r588f3d76t<time>h<sha>, which sorts above the `-gs.N` and
# `-dev.` prereleases of its X.Y.Z) nor in the superseded one (Masterminds skips
# every prerelease while no bound of the range carries one — and evaluates them
# all once one does, so a `-0` anywhere in the range is refused), not the last release
# of the former coupled `-gs.N` scheme, not the next minor (a re-pin onto
# another upstream release) or its release candidates, not the next major.
ADMITTED = ["{floor}", "{floor_patch}", "{floor_patch_tenfold}"]
REFUSED = ["{last_coupled}",
           "{floor}-r588f3d76t20260924050320h8e763ab", "{floor_patch}-r588f3d76t20260924050320h8e763ab",
           "{floor}-dev.giantswarm.2026-09-19.00-00-00.h0000000", "{floor_patch}-dev.giantswarm.2026-09-19.00-00-00.h0000000",
           "{next_minor}-rc.1", "{next_minor}", "{next_major}"]
STABLE_FLOOR = "1.0.0"  # the line's first release of its own stable semver
LAST_COUPLED = "0.11.0-gs.22"  # the line's last release under the coupled scheme, below every stable range
EXAMPLE_AGENTS = ["k8s-agent", "kgateway-agent", "istio-agent", "promql-agent", "observability-agent",
                  "argo-rollouts-agent", "helm-agent", "cilium-policy-agent", "cilium-manager-agent", "cilium-debug-agent"]


def fail(msg: str) -> None:
    sys.exit(f"FAIL: {msg}")


def documents(manifest: str) -> dict[tuple[str, str], list[str]]:
    out = {}
    for doc in manifest.split("\n---\n"):
        lines = doc.split("\n")
        kind = next((l[len("kind: "):] for l in lines if l.startswith("kind: ")), None)
        name = next((l[len("  name: "):] for l in lines if l.startswith("  name: ")), None)
        if kind and name:
            out[(kind, name)] = lines
    return out


def forwarded_values(lines: list[str]) -> dict[str, list[str]]:
    """The spec.values block, as top-level key -> its nested lines (stripped)."""
    values: dict[str, list[str]] = {}
    key = None
    for line in lines[lines.index("  values:") + 1 :]:
        if line and not line.startswith(VALUES_INDENT):
            break
        if line.startswith(VALUES_INDENT) and not line[len(VALUES_INDENT)].isspace():
            key = line.strip().rstrip(":").split(":")[0]
            values[key] = [line.strip()]
        elif key:
            values[key].append(line.strip())
    return values


def scalar(lines: list[str], key: str) -> str:
    """The value of `key: value` among stripped lines, unquoted; "" when absent."""
    for line in lines:
        m = re.match(rf"^{re.escape(key)}: (.+)$", line)
        if m:
            return m.group(1).strip().strip('"')
    return ""


def source(lines: list[str]) -> tuple[str, str, bool]:
    text = "\n".join(lines)
    url = re.search(r"^  url: (\S+)$", text, re.M).group(1)
    semver = re.search(r'^    semver: "([^"]+)"$', text, re.M).group(1)
    return url, semver, "semverFilter" in text


def depends_on(lines: list[str]) -> list[str]:
    text = "\n".join(lines)
    m = re.search(r"^  dependsOn:\n((?:    - name: \S+\n)+)", text + "\n", re.M)
    return re.findall(r"- name: (\S+)", m.group(1)) if m else []


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
            elif line.strip().startswith("#"):
                continue  # a comment between entries
            else:
                collecting = False
    return keys


def connectivity_block(conn: list[str], block: str) -> str:
    """One top-level block of the values forwarded to the connectivity release,
    de-indented to the shape the connectivity chart sees (nested keys keep two
    spaces per level)."""
    lines = conn[conn.index("  values:") + 1 :]
    start = lines.index(f"    {block}:") + 1
    body = []
    for line in lines[start:]:
        if line.startswith("    ") and not line[4].isspace():
            break
        body.append(line[4:])
    return "\n".join(body) + "\n"


def check_forwarded_block(values: dict[str, list[str]], omitted: set[str]) -> None:
    umbrella_only = connectivity_reads() - UPSTREAM_KEYS
    missing = sorted(umbrella_only - omitted)
    if missing:
        fail(f"the connectivity chart reads kagent.{', kagent.'.join(missing)} but components.kagent.omitKeys "
             "does not drop them; the kagent chart has no schema and would carry them on silently")
    if "kagent" in values:
        fail("kagent values still nested under a kagent key; the chart takes its keys at the root")
    for key in sorted(omitted | {"enabled"}):
        if key in values:
            fail(f"`{key}` forwarded to the kagent chart, which does not read it (an umbrella-only key)")
    if "fullnameOverride" not in values or "namespaceOverride" not in values:
        fail("kagent values lost fullnameOverride or namespaceOverride, which the connectivity chart also reads")
    if "providers" not in values:
        fail("kagent values lost providers (the chart's default ModelConfig, the connectivity chart's provider key)")
    print("ok: forwarded kagent values are flat, carry no umbrella-only key, keep the shared keys")


def check_one_build(values: dict[str, list[str]], kagent_range: str, conn_kagent: str, substrate_range: str) -> None:
    if scalar(values.get("registry", []), "registry") != "gsoci.azurecr.io":
        fail("kagent.registry is not gsoci.azurecr.io (the line's release images are copied under their upstream path, "
             "gsoci.azurecr.io/giantswarm/kagent/<image>, giantswarm/retagger#1229; giantswarm/agent-platform#580)")
    controller = values.get("controller", [])
    for key, repo in (("image", "controller"), ("agentImage", "golang-adk")):
        if f"repository: {LINE_IMAGES}/{repo}" not in controller:
            fail(f"kagent.controller.{key}.repository is not {LINE_IMAGES}/{repo} (the line's image)")
    if f"repository: {LINE_IMAGES}/ui" not in values.get("ui", []):
        fail(f"kagent.ui.image.repository is not {LINE_IMAGES}/ui (the line's image)")
    if "tag" in values:
        fail("kagent.tag forwarded to the kagent chart; the line stamps the build's image tag into the chart at publish, "
             "an override here would pin every release the range admits to one build")
    if fluxsemver.parse(kagent_range.split()[0].lstrip(">=")) < fluxsemver.parse(STABLE_FLOOR):
        fail(f"components.kagent.versionRange {kagent_range!r} admits a release of the former coupled scheme (vX.Y.Z-gs.N), which the "
             f"line publishes no more; its stable releases begin at {STABLE_FLOOR}, and every one of them carries the stamps (tag, "
             "controller.agentImage.digest, harness.create, the kagent-images ConfigMap) and a Harness template that drops an "
             "empty-valued selector label (giantswarm/agent-platform#418)")
    # The worker image is the meta chart's derivation from its own Substrate pin
    # (giantswarm/agent-platform#466; tests/verify-worker-image.py holds the rule).
    worker = re.search(r"^workerImage: gsoci\.azurecr\.io/giantswarm/substrate/ateom-gvisor:(\S+)$", "\n".join(values.get("substrateWorkerPool", [])), re.M)
    if not worker:
        fail("kagent.substrateWorkerPool.workerImage is not forwarded as gsoci.azurecr.io/giantswarm/substrate/ateom-gvisor:<the floor of "
             "components.substrate.versionRange>; the chart derives the worker from its own Substrate pin so the worker and the atelet "
             "are one Substrate release whatever kagent build the range admits (giantswarm/agent-platform#466)")
    if worker.group(1) != substrate_range.split()[0].lstrip(">="):
        fail(f"kagent.substrateWorkerPool.workerImage tag {worker.group(1)!r} is not the floor of the substrate OCIRepository's range "
             f"{substrate_range!r}; the worker follows the chart's Substrate pin (giantswarm/agent-platform#466)")
    harness = "\n".join(values.get("harness", []))
    if not re.search(r"^create: true$", harness, re.M):
        fail("kagent.harness.create is not true; since 4.8.0 the kagent chart renders the platform Harness")
    if re.search(r"^image:", harness, re.M):
        fail("kagent.harness.image forwarded by default; the chart's own Go ADK digest is the Harness image — an override travels only when set")
    for needle, what in (("snapshotLocation: ", "the snapshot location"), ("- name: KAGENT_PROPAGATE_TOKEN", "KAGENT_PROPAGATE_TOKEN in env"),
                         (f"{HARNESS_LABEL}: kagent", "the platform admission label"),
                         ('kagent.dev/harness: ""', "the chart's own admission label blanked (an empty value, which the line's Harness "
                                                    "template drops — never a null, which the patch of a pre-existing HelmRelease loses, #418)")):
        if needle not in harness:
            fail(f"kagent.harness forwarded without {what}: the meta chart owns the GS policy of the platform Harness\n{harness}")
    pool = scalar(values.get("substrateWorkerPool", []), "name")
    if not pool or f"name: {pool}" not in controller:
        fail("kagent.substrateWorkerPool.name and kagent.controller.substrate.defaultWorkerPool.name differ; "
             "the Harness's workerPoolRef and the controller's default name one pool")
    if "enabled: true" not in controller or "ateApiEndpoint:" not in "\n".join(controller):
        fail("kagent.controller.substrate is not on with its ate-api endpoint; the line has no runtime without Substrate")
    print(f"ok: one build of the line, named by the chart — no tag, no workerImage, no Harness digest forwarded; the Harness policy forwarded; WorkerPool {pool}")


def check_sources(docs, kagent_range: str) -> None:
    for name in ("kagent", "kagent-crds"):
        url, semver, filtered = source(docs[("OCIRepository", name)])
        if url != f"{LINE_REPOSITORY}/{name}":
            fail(f"{name} OCIRepository url is {url}, not {LINE_REPOSITORY}/{name} (the line's charts)")
        if semver != kagent_range:
            fail(f"{name} OCIRepository range is {semver!r}; kagent and kagent-crds are one build of the line and share {kagent_range!r}")
        if filtered:
            fail(f"{name} OCIRepository carries a semverFilter by default; the release range selects releases, a filter is a consumer's dev-channel knob")
    floor = kagent_range.split()[0].lstrip(">=")
    if floor != fluxsemver.base(floor):
        fail(f"components.kagent.versionRange {kagent_range!r} has a prerelease floor; the line's releases are stable semver")
    if "-" in kagent_range:
        fail(f"components.kagent.versionRange {kagent_range!r} carries a prerelease bound: Flux's Masterminds semver evaluates every prerelease against a range as soon as one bound carries one, so a -0 here would put every installation on the line's newest dev build")
    major, minor, patch = (int(x) for x in floor.split("."))
    shapes = {"floor": floor, "floor_patch": f"{major}.{minor}.{patch + 7}", "floor_patch_tenfold": f"{major}.{minor}.{patch + 10}",
              "last_coupled": LAST_COUPLED, "next_minor": f"{major}.{minor + 1}.0", "next_major": f"{major + 1}.0.0"}
    for template in ADMITTED:
        v = template.format(**shapes)
        if not fluxsemver.satisfies(v, kagent_range):
            fail(f"components.kagent.versionRange {kagent_range!r} does not admit {v} (a release of the line's pinned upstream version)")
    for template in REFUSED:
        v = template.format(**shapes)
        if fluxsemver.satisfies(v, kagent_range):
            fail(f"components.kagent.versionRange {kagent_range!r} admits {v}; the range must select the line's releases of one upstream version only")
    deps = depends_on(docs[("HelmRelease", "kagent")])
    for dep, why in (("kagent-crds", "its CRDs are their own chart"), ("substrate-crds", "its WorkerPool is an ate.dev CR"),
                     ("substrate", "the controller dials ate-api and routes through atenet"),
                     ("agent-platform-connectivity", "its hooks mint the CNPG connection Secret the controller starts against")):
        if dep not in deps:
            fail(f"the kagent release does not dependsOn {dep} ({why})")
    crds = docs[("HelmRelease", "kagent-crds")]
    if any(line.strip().startswith("crds: ") for line in crds):
        fail("kagent-crds carries a crds: policy, but its CRDs are templates (the kserve-llmisvc-crd shape)")
    crds_values = forwarded_values(crds)
    if "global" in crds_values:
        fail("global injected into kagent-crds, which reads none (components.kagent-crds.injectGlobal must be false)")
    for sub in ("kmcp", "substrate"):
        if "enabled: false" not in crds_values.get(sub, []):
            fail(f"kagent-crds.{sub}.enabled is not false (kmcp is not part of the platform; the Substrate CRDs come with Substrate)")
    print(f"ok: kagent + kagent-crds from {LINE_REPOSITORY} on {kagent_range!r} (admits {', '.join(t.format(**shapes) for t in ADMITTED)}; "
          f"refuses the dev builds, {shapes['last_coupled']}, {shapes['next_minor']} and {shapes['next_major']}); kagent after kagent-crds; CRDs as templates, subcharts off")


def check_retired_keys(values: dict[str, list[str]], conn_kagent: str) -> None:
    left = [k for k in EXAMPLE_AGENTS if k in values]
    if left:
        fail(f"the 0.10 wrapper's bundled example agents are still forwarded: {', '.join(left)}; the line ships none")
    controller = "\n".join(values.get("controller", []))
    if "METRICS_" in controller:
        fail("METRICS_* env forwarded to the controller; the line serves no Prometheus /metrics")
    if not re.search(r"^metrics:\nenabled: false$", controller, re.M):
        fail("kagent.controller.metrics.enabled is not false; the upstream chart's knob renders a Service to a port the line's controller never serves")
    if "skillsInitImage" in controller:
        fail("kagent.controller.skillsInitImage forwarded; the line has no skills-init image")
    if not re.search(r"^  serviceMonitor:\n    enabled: false$", conn_kagent, re.M):
        fail("kagent.serviceMonitor.enabled is not false in the values the connectivity chart receives; the monitor would scrape a refused port")
    print("ok: no example agent, no METRICS_* env, controller metrics and the ServiceMonitor off")


def check_substrate_pins(docs) -> None:
    """The substrate release takes atelet's pinned runtime images from the kagent release's
    ConfigMap (valuesFrom, optional — it installs before kagent renders it) and carries no
    pinnedImages of its own by default: Flux lets spec.values win over valuesFrom."""
    hr = "\n".join(docs[("HelmRelease", "substrate")])
    if not re.search(r"^  valuesFrom:\n\s+- kind: ConfigMap\n\s+name: kagent-images\n\s+optional: true\n\s+valuesKey: substrate-values\.yaml$", hr, re.M):
        fail("the substrate release does not read the kagent release's ConfigMap kagent-images (key substrate-values.yaml, optional) through valuesFrom")
    if "pinnedImages:" in hr:
        fail("the substrate release carries atelet.imageCache.pinnedImages in spec.values by default; it would shadow the ConfigMap's set")
    print("ok: the substrate release pins atelet's runtime images from the kagent release's ConfigMap kagent-images, nothing of its own by default")


def check_take_ownership(docs) -> None:
    """The kagent release keeps helm-controller's take-ownership default: neither install nor
    upgrade sets disableTakeOwnership (giantswarm/agent-platform#406 step 2): from 4.8.0 the
    kagent chart renders the platform Harness the connectivity chart renders today; the object
    stayed through connectivity's helm.sh/resource-policy: keep (4.7.19) and the
    kagent release adopts it. HelmRelease v2 has no opt-in field — a `takeOwnership` key is not
    in the CRD schema and fails the server-side apply — so the assertion is on the opt-out."""
    text = "\n".join(docs[("HelmRelease", "kagent")]) + "\n"
    for phase in ("install", "upgrade"):
        m = re.search(rf"^  {phase}:\n((?:    .*\n)+)", text, re.M)
        block = m.group(1) if m else ""
        if re.search(r"^    disableTakeOwnership: true$", block, re.M):
            fail(f"the kagent release's {phase} sets disableTakeOwnership: true — the 4.8.0 upgrade must adopt the Harness the connectivity release leaves behind")
        if re.search(r"^    takeOwnership:", block, re.M):
            fail(f"the kagent release's {phase} carries takeOwnership — not a HelmRelease v2 field (the CRD knows disableTakeOwnership only); the apply fails on it")
    print("ok: the kagent release keeps helm-controller's take-ownership default (no disableTakeOwnership, no invented takeOwnership) on install and upgrade")


# release -> why it carries spec.driftDetection.mode: enabled. Every other
# release keeps helm-controller's default (disabled).
DRIFT_DETECTION = {
    "kagent": "a platform Harness deleted on the 4.8.0 skip path would stay deleted until a values change or a forced "
              "reconcile (giantswarm/agent-platform#409)",
    "muster": "a CiliumNetworkPolicy the agentic-platform -> agent-platform rename left pointing at the old namespace "
              "would stay drifted across every upgrade, with muster's egress to Valkey denied "
              "(giantswarm/agent-platform#287)",
}


def check_drift_detection(docs) -> None:
    """The kagent and muster releases detect and correct drift
    (giantswarm/agent-platform#409, #287): with spec.driftDetection.mode: enabled
    helm-controller re-applies, on every reconcile, what differs from the release manifest or
    is missing — the platform Harness a skipped 4.7.19 loses on the 4.8.0 upgrade, and a
    muster object an operator or a rename edited away from its manifest — as a server-side
    apply, with no forced reconcile and no Helm revision. Off (helm-controller's default) a
    plain reconcile of the unchanged release reports in-sync and corrects nothing; Helm's
    three-way merge does not correct it either, because it only patches what changed between
    two release manifests. Per release, not fleet-wide: every other component keeps the
    default until its objects are known to tolerate the re-apply, so a driftDetection block
    on another release is a deliberate values change, never a side effect of these two."""
    for (kind, name), lines in docs.items():
        if kind != "HelmRelease":
            continue
        text = "\n".join(lines) + "\n"
        m = re.search(r"^  driftDetection:\n((?:    .*\n)+)", text, re.M)
        if name in DRIFT_DETECTION:
            if not m or not re.search(r"^    mode: enabled$", m.group(1), re.M):
                fail(f"the {name} release does not carry spec.driftDetection.mode: enabled — {DRIFT_DETECTION[name]}")
        elif m:
            fail(f"the {name} release carries spec.driftDetection by default; drift detection is decided per release "
                 f"(components.<name>.driftDetection), today {', '.join(sorted(DRIFT_DETECTION))} only")
    print(f"ok: the {', '.join(sorted(DRIFT_DETECTION))} releases detect and correct drift "
          "(spec.driftDetection.mode: enabled); no other release does by default")


def main(path: str) -> int:
    docs = documents(open(path, encoding="utf-8").read())
    for kind, name in (("HelmRelease", "kagent"), ("HelmRelease", "kagent-crds"), ("OCIRepository", "kagent"),
                       ("OCIRepository", "kagent-crds"), ("HelmRelease", "agent-platform-connectivity"), ("HelmRelease", "substrate"),
                       ("OCIRepository", "substrate")):
        if (kind, name) not in docs:
            fail(f"no {name} {kind} in the render")
    values = forwarded_values(docs[("HelmRelease", "kagent")])
    _, kagent_range, _ = source(docs[("OCIRepository", "kagent")])
    _, substrate_range, _ = source(docs[("OCIRepository", "substrate")])
    conn = docs[("HelmRelease", "agent-platform-connectivity")]
    conn_kagent = connectivity_block(conn, "kagent")
    check_forwarded_block(values, omit_keys())
    check_sources(docs, kagent_range)
    check_one_build(values, kagent_range, conn_kagent, substrate_range)
    check_retired_keys(values, conn_kagent)
    check_substrate_pins(docs)
    check_take_ownership(docs)
    check_drift_detection(docs)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
