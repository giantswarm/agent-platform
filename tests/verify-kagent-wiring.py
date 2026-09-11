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
    that Flux (Masterminds semver) would not match a `-gs.N` release against;
  * a kagent-crds source that differs from kagent's, a kagent release that does
    not come after its CRDs, a `crds:` policy on a chart whose CRDs are
    templates, `global` injected into a chart that reads none;
  * an image tag the chart would fall back to (`.Chart.Version`, which under
    helm-controller is `<version>+<digest>`), a Harness image given as a tag
    (the CRD accepts a digest only), or a tag and a range that name different
    upstream versions of the line;
  * the retired 0.10 keys: the ten bundled example agents (dead keys on the
    line — the chart ships none), the `METRICS_*` controller env and a metrics
    Service to a port the line's controller never serves.

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
# substrateWorkerPool is shared: the kagent chart renders the WorkerPool from it and
# the connectivity chart reads .name for the platform Harness workerPoolRef, so it stays forwarded.
UPSTREAM_KEYS = {"fullnameOverride", "namespaceOverride", "providers", "controller", "substrateWorkerPool"}
KAGENT_READ = re.compile(r'\.Values\.kagent\.([A-Za-z0-9_-]+)|dig "([A-Za-z0-9_-]+)"[^\n]*\.Values\.kagent\b')

LINE_REPOSITORY = "oci://ghcr.io/giantswarm/kagent/helm"
LINE_IMAGES = "giantswarm/kagent"
HARNESS_IMAGE = re.compile(r"^ghcr\.io/giantswarm/kagent/golang-adk@sha256:[a-f0-9]{64}$")
# What the release range must admit and refuse, by shape: the line's releases
# of the pinned upstream version and nothing else — not its dev builds (`dev` <
# `gs` identifier-wise), not a later upstream base (the ceiling holds the patch;
# Masterminds confines a prerelease to no patch tuple, so `<0.12.0-0` would let
# 0.11.1-dev.… through), not the next minor.
ADMITTED = ["{base}-gs.1", "{base}-gs.2", "{base}-gs.10", "{base}"]
REFUSED = ["{base}-dev.giantswarm.2026-09-10.22-06-46.h0ac5240", "{next_patch}-dev.giantswarm.2026-09-11.00-00-00.h0000000",
           "{next_patch}-gs.1", "{next_patch}", "{next_minor}-gs.1", "{next_minor}"]
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


def check_one_build(values: dict[str, list[str]], kagent_range: str, conn_kagent: str) -> str:
    if scalar(values.get("registry", []), "registry") != "ghcr.io":
        fail("kagent.registry is not ghcr.io (the line's images live under ghcr.io/giantswarm/kagent)")
    controller = values.get("controller", [])
    for key, repo in (("image", "controller"), ("agentImage", "golang-adk")):
        if f"repository: {LINE_IMAGES}/{repo}" not in controller:
            fail(f"kagent.controller.{key}.repository is not {LINE_IMAGES}/{repo} (the line's image)")
    if f"repository: {LINE_IMAGES}/ui" not in values.get("ui", []):
        fail(f"kagent.ui.image.repository is not {LINE_IMAGES}/ui (the line's image)")
    tag = scalar(values.get("tag", []), "tag")
    if not tag or fluxsemver.parse(tag) is None:
        fail("kagent.tag is not an explicit image tag; upstream's chart falls back to .Chart.Version, "
             "which under helm-controller is <version>+<digest> — an invalid image tag")
    if fluxsemver.base(tag) != fluxsemver.base(kagent_range.split()[0].lstrip(">=")):
        fail(f"kagent.tag {tag} and components.kagent.versionRange {kagent_range!r} name different upstream versions of the line; "
             "one build of the line: tag, Harness digest and the two ranges move together")
    m = re.search(r"^  harness:\n(?:    .*\n)*?    image: (\S+)$", conn_kagent, re.M)
    harness = m.group(1) if m else ""
    if not HARNESS_IMAGE.match(harness):
        fail(f"kagent.harness.image is not the line's Go ADK image by digest (got {harness!r}); the Harness CRD accepts nothing else "
             "and the connectivity chart renders the platform Harness from it")
    if "harness" in values:
        fail("kagent.harness forwarded to the kagent chart; it is the connectivity chart's key")
    pool = scalar(values.get("substrateWorkerPool", []), "name")
    if not pool or f"name: {pool}" not in controller:
        fail("kagent.substrateWorkerPool.name and kagent.controller.substrate.defaultWorkerPool.name differ; "
             "the Harness's workerPoolRef and the controller's default name one pool")
    if "enabled: true" not in controller or "ateApiEndpoint:" not in "\n".join(controller):
        fail("kagent.controller.substrate is not on with its ate-api endpoint; the line has no runtime without Substrate")
    print(f"ok: one build of the line — tag {tag}, Harness digest {harness.rsplit(':', 1)[1][:12]}…, WorkerPool {pool}")
    return tag


def check_sources(docs, kagent_range: str) -> None:
    for name in ("kagent", "kagent-crds"):
        url, semver, filtered = source(docs[("OCIRepository", name)])
        if url != f"{LINE_REPOSITORY}/{name}":
            fail(f"{name} OCIRepository url is {url}, not {LINE_REPOSITORY}/{name} (the line's charts)")
        if semver != kagent_range:
            fail(f"{name} OCIRepository range is {semver!r}; kagent and kagent-crds are one build of the line and share {kagent_range!r}")
        if filtered:
            fail(f"{name} OCIRepository carries a semverFilter by default; the release range selects releases, a filter is a consumer's dev-channel knob")
    base = fluxsemver.base(kagent_range.split()[0].lstrip(">="))
    major, minor, patch = (int(x) for x in base.split("."))
    shapes = {"base": base, "next_patch": f"{major}.{minor}.{patch + 1}", "next_minor": f"{major}.{minor + 1}.0"}
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
        fail("kagent-crds carries a crds: policy, but its CRDs are templates (the kserve-crd shape)")
    crds_values = forwarded_values(crds)
    if "global" in crds_values:
        fail("global injected into kagent-crds, which reads none (components.kagent-crds.injectGlobal must be false)")
    for sub in ("kmcp", "substrate"):
        if "enabled: false" not in crds_values.get(sub, []):
            fail(f"kagent-crds.{sub}.enabled is not false (kmcp is not part of the platform; the Substrate CRDs come with Substrate)")
    print(f"ok: kagent + kagent-crds from {LINE_REPOSITORY} on {kagent_range!r} (admits {', '.join(t.format(**shapes) for t in ADMITTED)}; "
          f"refuses the dev builds and {shapes['next_patch']}, {shapes['next_minor']}); kagent after kagent-crds; CRDs as templates, subcharts off")


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


def main(path: str) -> int:
    docs = documents(open(path, encoding="utf-8").read())
    for kind, name in (("HelmRelease", "kagent"), ("HelmRelease", "kagent-crds"), ("OCIRepository", "kagent"),
                       ("OCIRepository", "kagent-crds"), ("HelmRelease", "agent-platform-connectivity")):
        if (kind, name) not in docs:
            fail(f"no {name} {kind} in the render")
    values = forwarded_values(docs[("HelmRelease", "kagent")])
    _, kagent_range, _ = source(docs[("OCIRepository", "kagent")])
    conn = docs[("HelmRelease", "agent-platform-connectivity")]
    conn_kagent = connectivity_block(conn, "kagent")
    check_forwarded_block(values, omit_keys())
    check_sources(docs, kagent_range)
    check_one_build(values, kagent_range, conn_kagent)
    check_retired_keys(values, conn_kagent)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
