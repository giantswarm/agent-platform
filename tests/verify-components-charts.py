#!/usr/bin/env python3
"""Render every component chart with the values the meta chart forwards to it.

The meta chart cannot know whether a component chart accepts the block it
forwards: the block is inlined into a HelmRelease and validated by helm-controller
on the cluster, against the chart the OCIRepository resolved. This check does that
validation here, twice per component: at the version the `versionRange` resolves
to today (what a dogfooding installation gets) and at the exact pin in
examples/customer-bom.yaml (what a BOM installation gets — rendered with the
values the BOM render forwards, which may differ from the defaults').

The set of components is the meta chart's own roster — every `components.*`
entry of values.yaml with a `chart` (an entry without one is a feature switch,
templates/components.yaml) — and the BOM has to match it both ways: a roster
entry the BOM does not pin, a pin that names no component, and a pin the render
gives another version than the BOM's line each FAIL. A hand-kept list is what
let agent-platform#278's three breaks through: the meta chart forwarded
gateway.parameters.podAnnotations (#431) and vm-manager/vmManager (#441) to
agent-platform-connectivity, and podDisruptionBudget/podAnnotations to
klaus-gateway, against pins whose schemas declare none of them — and neither
chart was on the list, so neither was ever rendered.

A component released with the meta chart (components.<name>.releasedWithChart:
agent-platform-connectivity, published off the same tag) has no pin: its version
is the meta chart's own, and the working tree is the chart to render — the pair
an installation gets is always the pair of one commit, and a key added to both
charts in one commit exists nowhere else until the tag is cut (#339). The BOM
must not pin it (the render refuses such a pin).

A chart that validates values with a CLOSED schema (additionalProperties:
false at the root) is the sharp case: one key the meta chart forwards that the
chart does not declare fails the HelmRelease on every installation that turns
the component on, and nothing short of rendering the pair sees it. A chart
with no schema is still worth rendering: that proves the forwarded block
templates (the WorkerPool's required image, the Substrate wiring, the bundled
Postgres image) against the chart the values name.

QUICKSTART carries the inputs every meta render here needs (the identity block,
the managers' and the kagent line's required inputs); ALL_ON_INPUTS the inputs
an installation supplies and the meta chart's values leave empty once EVERY
component is on — the resource servers' OAuth clients, a public Gateway for the
routes, the ingress mode that admits agentgateway. Without them the charts
refuse to render at all, and the point here is a full render, not merely an
accepted schema. The two lists are apart because
tests/verify-kagent-tools-namespace.py imports QUICKSTART for renders with a much
smaller component set, where the all-on ingress mode would trip the connectivity
chart's guard. The meta renders are the vanilla cluster shape (no
--api-versions): the fleet shape's forwarded values are not rendered here.

A range is resolved the way Flux does — the registry's tag list, the highest
semver the constraint admits (tests/fluxsemver.py: Masterminds semantics, a
a prerelease included) — because `helm pull --version <range>` reads the
constraint with its own semver and, for the kagent line's prerelease releases,
differently. A BOM pin that resolves to no published tag FAILS: a BOM no
installation can install is a broken BOM, not something to render a substitute
for. A RANGE that admits nothing published yet (a line re-pinned ahead of its
release) is rendered against the newest chart the line has while UNRELEASED
names the release it waits for (fallback()); the entry goes with the release.

`--strict` is the tag pipeline's run (giantswarm/agent-platform#624): a release
naming a chart nobody can pull is a release nobody can install, so UNRELEASED and
RENDER_AGAINST do not apply, and a range that admits nothing published, or a BOM
pin that is not published, FAILS naming the component and the version the
release waits for — the range's floor. A GitHub release or a tag of the
component is not evidence its chart exists; a tag pipeline that failed after
tagging (klaus-gateway 1.20.0) leaves neither chart nor image. A release refused
this way is recovered by rerunning the tag's workflow from failed once the chart
is out; the branch pipeline keeps rendering against the fallbacks.

Network: pulls from gsoci.azurecr.io, and from ghcr.io for the CloudNativePG
chart (three attempts each); the tag
list comes from the registry's anonymous `/v2/<repo>/tags/list`. Every Helm call
is bounded (TIMEOUT). PyYAML is in the CI image (the job installs python3-yaml).
"""

import json
import pathlib
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

import yaml

import fluxsemver

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
# The release name every render here uses. It also names the chart's own
# self-management OCIRepository, which is not a component.
RELEASE = "t"
# Bound on every helm call: a stalled registry connection ends in a FAIL line
# naming the call, not in CircleCI's no-output kill of the whole job.
TIMEOUT = 300
MANAGERS = ["model-manager", "agent-manager"]
# The kagent.dev API version the meta chart pins into both managers
# (`kagent.apiVersion`, giantswarm/agent-platform#401); their charts must render
# it as the container's `--kagent-api-version`, the pod-template change that
# rolls the Deployments on the cut-over.
KAGENT_API_VERSION = "v1alpha3"
# The platform Harness's admission label (the Generic agent chart stamps it);
# the rendered kagent chart must select by it alone.
HARNESS_LABEL = "agent-platform.giantswarm.io/harness"
KAGENT = ["kagent-crds", "kagent"]
# component -> the release its RANGE waits for. While nothing the range admits
# is published, the forwarded block is rendered against the newest chart the
# line has (see fallback()); the entry goes when the release exists.
UNRELEASED: dict[str, str] = {}
# component -> a published branch build that already carries the schema of the
# release UNRELEASED waits for, when the newest release's schema would refuse a
# value the meta chart forwards. The entry goes with the release.
RENDER_AGAINST: dict[str, str] = {}
# An exact version, prerelease included (a dev build is one) — what a BOM
# line may carry; a range is not a version this check can render "the pin" at.
EXACT_RE = re.compile(r"^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?$")
# --strict: the tag pipeline's run — no fallback, a release that names an
# unpublished chart fails (main() sets it from the command line).
STRICT = False


def floor(constraint: str) -> str:
    """The lowest version a constraint admits, as the release it waits for: the
    `>=` bound of a range, an exact version itself."""
    m = re.search(r">=\s*v?([0-9A-Za-z.+-]+)", constraint)
    return m.group(1) if m else constraint.strip()


def waits_for(name: str, url: str, constraint: str, tags: list[str]) -> str:
    """The FAIL line of the strict run: which component, which version, what is
    published instead, and how the release is recovered."""
    newest = fluxsemver.resolve(tags, ">=0.0.0")
    return (
        f"{name}: nothing published at {url} satisfies {constraint!r}; the release waits for {name} {floor(constraint)} "
        f"(the newest published chart is {newest or 'none'}). A GitHub release or a tag of the component is not its chart: "
        f"once `devctl release wait` on it exits 0, rerun this tag's workflow from failed — no new tag"
    )
QUICKSTART = [
    "--set", "global.domain=example.com",
    "--set", "global.identity.issuerUrl=https://dex.example.com",
    "--set", "global.identity.clientId=agent-platform",
    "--set", "global.identity.existingSecret=agent-platform-idp",
    # model-manager's chart requires the endpoint of its default backend (an
    # installation input, empty in the meta chart's values).
    "--set", "model-manager.ollama.endpoint=http://ollama.example.com:11434",
    # kagent on requires the Harness's snapshot store (the meta chart's guard).
    "--set", "kagent.harness.snapshotLocation=s3://ci-agent-snapshots/agents",
    # The platform Cluster on, so the Substrate render exercises the CNPG path
    # (the derived connection Secret reference) rather than the bundled one.
    "--set", "postgres.enabled=true",
]
# The inputs that only matter once EVERY component is turned on, kept out of
# QUICKSTART because tests/verify-kagent-tools-namespace.py imports that list
# for renders with a much smaller component set: ingress.mode and its
# companion are tied to which components are enabled, and a mode that disagrees
# with them fails the connectivity chart's guard.
ALL_ON_INPUTS = [
    # Every route the connectivity chart renders needs a public Gateway to
    # hang off (the chart's own guard); set once for all of them, the name is
    # immaterial to a render.
    "--set", "global.gatewayApi.parentRefs[0].name=x",
    # agentgateway is among the components here, so the ingress mode must be
    # the one that admits it (the connectivity chart's guard ties them).
    "--set", "ingress.mode=agentgateway-muster",
    "--set", "agent-platform-mcps.agentgateway.viaMuster=true",
    # The OAuth clients each resource server needs. An installation supplies
    # these; they are not in the meta chart's values, and without them the
    # charts refuse to render at all.
    "--set", "muster.muster.oauth.server.dex.clientSecret=x",
    "--set", "muster.muster.oauth.server.registrationToken=x",
    # valkey's ACL users need their passwords from somewhere (its own guard).
    "--set", "valkey.valkey.auth.usersExistingSecret=x",
    "--set", "mcp-kubernetes.mcpKubernetes.oauth.dex.clientSecret=x",
]

API_VERSIONS = [
    "--api-versions", "cilium.io/v2",
    "--api-versions", "monitoring.coreos.com/v1",
    "--api-versions", "cert-manager.io/v1",
    "--api-versions", "gateway.networking.k8s.io/v1",
    "--api-versions", "autoscaling.k8s.io/v1",
]


def fail(msg: str) -> None:
    sys.exit(f"FAIL: {msg}")


def run(cmd: list[str]) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, check=False, timeout=TIMEOUT)
    except subprocess.TimeoutExpired:
        fail(f"timed out after {TIMEOUT}s: {' '.join(cmd)}")


def render_meta(meta: str, flags: list[str]) -> str:
    r = run(["helm", "template", RELEASE, meta, *flags])
    if r.returncode != 0:
        fail(f"meta render failed\n{r.stderr}")
    return r.stdout


def docs(manifest: str) -> dict[tuple[str, str], str]:
    out = {}
    for d in manifest.split("\n---\n"):
        kind = re.search(r"^kind: (\S+)", d, re.M)
        name = re.search(r"^  name: (\S+)", d, re.M)
        if kind and name:
            out[(kind.group(1), name.group(1))] = d
    return out


def hr_values(doc: str) -> str:
    body = doc[doc.index("\n  values:\n") + len("\n  values:\n"):]
    return "\n".join(line[4:] if line.startswith("    ") else line for line in body.splitlines())


def source(doc: str) -> tuple[str, str]:
    """An OCIRepository's url and semver range. A semverFilter would change the
    resolution; the defaults carry none and this check renders the defaults."""
    if "semverFilter" in doc:
        fail("a default OCIRepository carries a semverFilter; the release ranges select releases, a filter is a consumer's knob")
    url = re.search(r"^  url: (\S+)", doc, re.M).group(1)
    semver = re.search(r'semver: "([^"]+)"', doc).group(1)
    return url, semver


def registry_tags(url: str) -> list[str]:
    """Every tag of an OCI repository (`oci://host/path`), anonymously, following
    the distribution API's token challenge and Link pagination."""
    host, _, path = url.removeprefix("oci://").partition("/")
    next_url, token, tags = f"https://{host}/v2/{path}/tags/list?n=1000", None, []
    for _ in range(100):
        req = urllib.request.Request(next_url, headers={"Authorization": f"Bearer {token}"} if token else {})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                tags += json.load(r).get("tags") or []
                link = r.headers.get("Link", "")
        except urllib.error.HTTPError as e:
            if e.code != 401 or token:
                raise
            challenge = dict(re.findall(r'(\w+)="([^"]*)"', e.headers.get("Www-Authenticate", "")))
            with urllib.request.urlopen(f"{challenge['realm']}?service={challenge['service']}&scope={challenge['scope']}", timeout=60) as t:
                body = json.load(t)
            token = body.get("access_token") or body.get("token")
            continue
        m = re.search(r"<([^>]+)>", link)
        if not m:
            return tags
        next_url = m.group(1) if m.group(1).startswith("http") else f"https://{host}{m.group(1)}"
    fail(f"the tag list of {url} did not end after 100 pages")


def fallback(name: str, url: str, constraint: str, tags: list[str], kagent_tag: str) -> str:
    """The chart to render a RANGE against while nothing the constraint admits is
    published: the kagent build the values name (the kagent range's floor — the
    chart version of the same build), the branch build RENDER_AGAINST names, else
    the newest release tag. Only for a range UNRELEASED names, or for a BOM pin
    that IS the version UNRELEASED names — the one release the range waits for;
    any other unpublished pin fails, because no installation on it can install.
    Under --strict there is no fallback: the release waits for the floor."""
    if STRICT:
        fail(waits_for(name, url, constraint, tags))
    if name not in UNRELEASED:
        fail(f"no published version of {name} satisfies {constraint!r}, and nothing says it is expected (UNRELEASED); the release would wait for {name} {floor(constraint)}")
    chosen = kagent_tag if name in KAGENT else RENDER_AGAINST.get(name) or fluxsemver.resolve(tags, ">=0.0.0")
    if not chosen or chosen not in tags:
        fail(f"no published chart of {name} to render against while {constraint!r} waits for {UNRELEASED[name]}")
    print(f"NOTE: {name}: {constraint!r} matches no published chart yet (waits for {UNRELEASED[name]}); rendering against {chosen}")
    return chosen


def roster(meta: str) -> dict[str, dict]:
    """The meta chart's components: every `components.*` entry of values.yaml
    that names a chart (an entry without one is a feature switch and renders
    no release — templates/components.yaml), name -> entry."""
    with open(f"{meta}/values.yaml", encoding="utf-8") as f:
        components = yaml.safe_load(f)["components"]
    return {n: c for n, c in components.items() if isinstance(c, dict) and c.get("chart")}


def bom_pins(meta: str) -> dict[str, str]:
    """The customer BOM's component pins, name -> exact version. Every entry
    under components: must be exactly `{ versionRange: "<exact>" }` — a range
    is not a version an installation is pinned to, and any other key is not a
    pin."""
    with open(f"{meta}/examples/customer-bom.yaml", encoding="utf-8") as f:
        components = yaml.safe_load(f).get("components") or {}
    out: dict[str, str] = {}
    for name, entry in components.items():
        if not isinstance(entry, dict) or set(entry) != {"versionRange"} or not isinstance(entry["versionRange"], str):
            fail(f"examples/customer-bom.yaml: components.{name} is not a pin of the form {{ versionRange: \"<version>\" }}: {entry!r}")
        if not EXACT_RE.match(entry["versionRange"]):
            fail(f"examples/customer-bom.yaml: components.{name}.versionRange {entry['versionRange']!r} is a range, not an exact version — the BOM pins")
        out[name] = entry["versionRange"]
    if not out:
        fail(f"{meta}/examples/customer-bom.yaml pins no components")
    return out


def pull(url: str, version: str, dest: str) -> str:
    """helm pull of one exact version; returns the chart version it unpacked."""
    err = ""
    for attempt in range(3):
        r = run(["helm", "pull", url, "--version", version, "--untar", "--untardir", dest])
        if r.returncode == 0:
            chart = open(f"{dest}/{url.rsplit('/', 1)[1]}/Chart.yaml", encoding="utf-8").read()
            return re.search(r"^version: (\S+)", chart, re.M).group(1).strip("'\"")
        err = r.stderr
        if attempt < 2:
            time.sleep(5 * (attempt + 1))
    fail(f"could not pull {url} --version {version!r}\n{err}")


def check_harness_selector(manifest: str, what: str) -> None:
    """The platform Harness the kagent chart renders admits templates by the ONE
    label the Generic agent chart stamps. The meta chart blanks the chart's own
    default key (kagent.dev/harness: "") and the line's template drops the empty
    value — this is where that contract is proven against the chart the range
    resolves to, on the rendered object (giantswarm/agent-platform#418)."""
    harness = [d for d in manifest.split("\n---") if re.search(r"^kind: Harness$", d, re.M)]
    if len(harness) != 1:
        fail(f"{what} renders {len(harness)} Harness objects, expected the one platform Harness")
    m = re.search(r"^ {6}matchLabels:\n((?: {8}\S.*\n)+)", harness[0] + "\n", re.M)
    labels = dict(line.strip().split(": ", 1) for line in m.group(1).splitlines()) if m else {}
    if labels != {HARNESS_LABEL: "kagent"}:
        fail(
            f"{what} renders the platform Harness selecting by {labels or 'nothing'}; the admission contract is "
            f"{HARNESS_LABEL}=kagent alone — the chart's own kagent.dev/harness must be dropped (the meta chart forwards it "
            "empty; the line's Harness template drops an empty-valued selector label, giantswarm/agent-platform#418)"
        )
    print(f"ok: {what} renders the platform Harness selecting by {HARNESS_LABEL}=kagent alone")


def render_component(name: str, chart_dir: str, values: str, what: str, tmp: str) -> None:
    """One `helm template` of a component chart with one forwarded values block,
    and the per-component assertions on what it rendered."""
    values_file = f"{tmp}/{name}-{re.sub(r'[^a-z0-9]+', '-', what.lower())}.yaml"
    with open(values_file, "w", encoding="utf-8") as f:
        f.write(values)
    r = run(["helm", "template", name, chart_dir, "-n", "agent-platform", "-f", values_file, *API_VERSIONS])
    if r.returncode != 0:
        fail(f"{what} rejects the values the meta chart forwards to it\n{r.stderr}")
    if name in MANAGERS and f"--kagent-api-version={KAGENT_API_VERSION}" not in r.stdout:
        fail(f"{what} does not render the forwarded kagent.apiVersion as its --kagent-api-version={KAGENT_API_VERSION} argument")
    if name == "kagent":
        check_harness_selector(r.stdout, what)
    print(f"ok: {what} renders the forwarded values ({r.stdout.count(chr(10) + 'kind: ')} objects)")


def main(meta: str) -> int:
    components = roster(meta)
    released = {n for n, c in components.items() if c.get("releasedWithChart")}
    pins = bom_pins(meta)
    # The BOM and the roster agree both ways; a component released with the chart
    # has no pin (its version is the chart's, the render refuses a pin).
    if unpinned := sorted(set(components) - released - set(pins)):
        fail(f"examples/customer-bom.yaml does not pin components.{', components.'.join(unpinned)} — every component of values.yaml is pinned, or it drops out of this check and of every BOM installation unseen")
    if stray := sorted(set(pins) - set(components)):
        fail(f"examples/customer-bom.yaml pins components.{', components.'.join(stray)}, which values.yaml does not know — the BOM and the roster have drifted apart")
    if pinned_released := sorted(set(pins) & released):
        fail(f"examples/customer-bom.yaml pins components.{', components.'.join(pinned_released)}, a chart released with the meta chart: its version is the meta chart's own, a pin here lags the moment the meta chart moves")
    on = [f"--set=components.{n}.enabled=true" for n in components]
    wide = docs(render_meta(meta, [*QUICKSTART, *ALL_ON_INPUTS, *on]))
    pinned = docs(render_meta(meta, ["-f", f"{meta}/examples/customer-bom.yaml", *QUICKSTART, *ALL_ON_INPUTS, *on]))
    # The OCIRepository and the HelmRelease are named after the entry's chart.
    charts = {n: c["chart"] for n, c in components.items()}
    rendered = {n for kind, n in wide if kind == "OCIRepository" and n != RELEASE}
    if rendered != set(charts.values()):
        fail(f"the render's OCIRepositories {sorted(rendered)} are not the roster's charts {sorted(charts.values())}")
    kagent_oci = wide.get(("OCIRepository", charts.get("kagent", "kagent")))
    kagent_tag = source(kagent_oci)[1].split()[0].lstrip(">=") if kagent_oci else ""  # the range's floor = the build the values name
    print(f"--> {len(components)} components (the meta chart's roster): {len(pins)} pinned by the BOM, {len(released)} released with the chart")
    if STRICT:
        print("--> strict (the tag pipeline): every range and every BOM pin must resolve to a published chart; UNRELEASED and RENDER_AGAINST do not apply")
    with tempfile.TemporaryDirectory() as tmp:
        for name in sorted(components):
            chart = charts[name]
            url, rng = source(wide[("OCIRepository", chart)])
            _, pin = source(pinned[("OCIRepository", chart)])
            values_range = hr_values(wide[("HelmRelease", chart)])
            values_pin = hr_values(pinned[("HelmRelease", chart)])
            if name in released:
                # One version matters, the working tree's: rendered once, with the
                # forwarded values of both renders when they differ.
                chart_dir = REPO_ROOT / "helm" / chart
                if not (chart_dir / "Chart.yaml").is_file():
                    fail(f"{name}: no chart at {chart_dir} — releasedWithChart names a chart this repository does not have")
                if rng != pin:
                    fail(f"{name}: the BOM render gives the chart released with the meta chart another version ({pin!r}) than the defaults ({rng!r})")
                render_component(name, str(chart_dir), values_range, f"{name} working tree (released with the chart, {rng})", tmp)
                if values_pin != values_range:
                    render_component(name, str(chart_dir), values_pin, f"{name} working tree (released with the chart, the BOM's values)", tmp)
                continue
            if pin != pins[name]:
                fail(f"{name}: the BOM render carries {pin!r} while examples/customer-bom.yaml pins {pins[name]!r} — the pin did not reach the OCIRepository")
            tags = registry_tags(url)
            version_range = fluxsemver.resolve(tags, rng) or fallback(name, url, rng, tags, kagent_tag)
            version_pin = fluxsemver.resolve(tags, pin)
            if not version_pin and pin == UNRELEASED.get(name) and not STRICT:
                version_pin = fallback(name, url, pin, tags, kagent_tag)
            if not version_pin:
                fail(f"{name}: the BOM pins {pin!r}, which {url} does not publish — no installation on this BOM can install it"
                     + (f"; {waits_for(name, url, pin, tags)}" if STRICT else ""))
            axes = [("range", rng, version_range, values_range), ("BOM pin", pin, version_pin, values_pin)]
            if version_range == version_pin and values_range == values_pin:
                axes = [("range = BOM pin", f"{rng} = {pin}", version_range, values_range)]
            for label, constraint, version, values in axes:
                chart_dir = f"{tmp}/{name}/{version}"
                resolved = pull(url, version, chart_dir)
                render_component(name, f"{chart_dir}/{chart}", values, f"{name} {resolved} (the {label} {constraint!r})", tmp)
    return 0


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if a != "--strict"]
    STRICT = len(args) != len(sys.argv) - 1
    if len(args) != 1:
        sys.exit(f"usage: {sys.argv[0]} <meta chart dir> [--strict]")
    sys.exit(main(args[0]))
