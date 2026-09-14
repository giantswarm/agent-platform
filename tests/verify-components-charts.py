#!/usr/bin/env python3
"""Pull EVERY component chart the customer BOM pins and render each with the
values the meta chart forwards to it.

The set of components comes from the BOM, never from a list kept in this file.
A hand-kept list is what let agent-platform#278's three breaks through: the
meta chart forwarded gateway.parameters.podAnnotations (#431) and
vm-manager/vmManager (#441) to agent-platform-connectivity, and
podDisruptionBudget/podAnnotations to klaus-gateway, against pins whose
schemas declare none of them — and neither chart was on the list, so neither
was ever rendered. The BOM is the roster of what an installation actually
gets, so a component is covered the day it is pinned rather than the day
someone remembers to add it, and a pin the meta chart renders no
OCIRepository for fails rather than passing unnoticed.

The meta chart cannot know whether a component chart accepts the block it
forwards: the block is inlined into a HelmRelease and validated by helm-controller
on the cluster, against the chart the OCIRepository resolved. This check does that
validation here, twice per component: at the version the `versionRange` resolves
to today (what a dogfooding installation gets) and at the exact pin in
examples/customer-bom.yaml (what a BOM installation gets). The render uses the
quick-start inputs Backstage and mcp-kubernetes require (global.domain and
global.identity) and the API groups the charts' optional objects need.

A chart that validates values with a CLOSED schema (additionalProperties:
false at the root) is the sharp case: one key the meta chart forwards that the
chart does not declare fails the HelmRelease on every installation that turns
the component on, and nothing short of rendering the pair sees it. A chart
with no schema is still worth rendering: that proves the forwarded block
templates (the WorkerPool's required image, the Substrate wiring, the bundled
Postgres image) against the chart the values name.

QUICKSTART carries the inputs an installation supplies and the meta chart's
values leave empty — the identity block, the resource servers' OAuth clients,
a public Gateway for the routes. Without them the charts refuse to render at
all, and the point here is a full render, not merely an accepted schema.

A range is resolved the way Flux does — the registry's tag list, the highest
semver the constraint admits (tests/fluxsemver.py: Masterminds semantics, a
`-gs.N` prerelease included) — because `helm pull --version <range>` reads the
constraint with its own semver and, for the kagent line's prerelease releases,
differently. While a range or a BOM pin names a release that is not published
yet (UNRELEASED below: a sibling's 1.0 or the kagent line's tag), the block is
rendered against the newest chart the line has instead — the kagent build the
values name (the floor of the kagent range) or the newest 0.x of a manager — and the fallback is
printed; an entry leaves UNRELEASED with the release it waits for.

Network: pulls from gsoci.azurecr.io and ghcr.io (three attempts each); the tag
list comes from the registry's anonymous `/v2/<repo>/tags/list`.
Deliberately stdlib-only: the CI image has no PyYAML.
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

import fluxsemver

# The components this check covers come from the customer BOM, never from a
# list kept here: a hand-kept list is what let agent-platform#278's three
# breaks through — agent-platform-connectivity and klaus-gateway forwarded keys
# their pinned charts refuse, and neither chart was on it, so nothing rendered
# them. The BOM is the roster of what an installation actually gets, so a
# component is covered the day it is pinned rather than the day someone
# remembers to add it. bom_components() reads it; COMPONENT_PIN_RE is its
# one-line pin form.
COMPONENT_PIN_RE = re.compile(r'^\s{2}([a-z0-9][a-z0-9-]*):\s*\{\s*versionRange:\s*"([^"]+)"')
# Components whose chart lives in this repo and is released off the same tag as
# the meta chart: the pair an installation gets is always the pair in this
# commit, so the working tree is the chart to render, not a published version
# the range or the example BOM happens to name. Rendering a published one
# instead would fail every change that adds a key to both charts at once — the
# key exists nowhere but here until the tag is cut (giantswarm/agent-platform#339).
LOCAL_CHARTS = {"agent-platform-connectivity": "helm/agent-platform-connectivity"}
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
# The Substrate line's two charts: rendered with the substrate: block the meta
# chart forwards (the derived postgres.enabled boolean, the CNPG connection
# Secret reference) — the substrate chart has no schema, so this is what proves
# the forwarded keys (postgres.connectionStringSecretRef, atelet.nodeSelector /
# tolerations / affinity) exist in the pinned build.
SUBSTRATE = ["substrate-crds", "substrate"]
# component -> the release its range / BOM pin waits for. While nothing the
# range admits is published, the forwarded block is rendered against the newest
# chart the line has (see fallback()); the entry goes when the release exists.
UNRELEASED = {
    "agent-manager": "agent-manager 1.0.0 (kagent API v2, giantswarm/agent-manager#37)",
    "backstage": "the Dev Portal 1.0.0 (kagent API v2, giantswarm/backstage#2343)",
    "kagent": "the kagent line's first release tag (giantswarm/giantswarm#37010)",
    "kagent-crds": "the kagent line's first release tag (giantswarm/giantswarm#37010)",
}
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
# The inputs that only matter once EVERY component the BOM pins is turned on,
# kept out of QUICKSTART because tests/verify-kagent-tools-namespace.py imports
# that list for renders with a much smaller component set: ingress.mode and its
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
]


def run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, check=False)


# The release name every render here uses. It also names the chart's own
# self-management OCIRepository, which is not a component.
RELEASE = "t"


def render_meta(meta: str, flags: list[str]) -> str:
    r = run(["helm", "template", RELEASE, meta, *flags])
    if r.returncode != 0:
        sys.exit(f"FAIL: meta render failed\n{r.stderr}")
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
        sys.exit("FAIL: a default OCIRepository carries a semverFilter; the release ranges select releases, a filter is a consumer's knob")
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
    sys.exit(f"FAIL: the tag list of {url} did not end after 100 pages")


# component -> a published branch build that already carries the schema of the
# release UNRELEASED waits for, when the newest release's schema would refuse a
# value 4.0 forwards (agent-manager 0.x: muster is additionalProperties false,
# so the derived muster.url fails its render). The entry goes with the release.
RENDER_AGAINST = {
    "agent-manager": "0.4.5-dev.kagent-v2-agenttemplates.2026-09-10.23-57-22.hebd7516",
}


def fallback(name: str, constraint: str, tags: list[str], kagent_tag: str) -> str:
    """The chart to render against while nothing the constraint admits is
    published: the kagent build the values name (the kagent range's floor — the chart version
    of the same build), the branch build RENDER_AGAINST names, else the newest
    release tag."""
    if name not in UNRELEASED:
        sys.exit(f"FAIL: no published version of {name} satisfies {constraint!r}, and nothing says it is expected (UNRELEASED)")
    chosen = kagent_tag if name in KAGENT else RENDER_AGAINST.get(name) or fluxsemver.resolve(tags, ">=0.0.0")
    if not chosen or chosen not in tags:
        sys.exit(f"FAIL: no published chart of {name} to render against while {constraint!r} waits for {UNRELEASED[name]}")
    print(f"NOTE: {name}: {constraint!r} matches no published chart yet (waits for {UNRELEASED[name]}); rendering against {chosen}")
    return chosen


def bom_components(meta: str) -> dict[str, str]:
    """The components the customer BOM pins, name -> pinned version, in the
    order the BOM lists them. Only exact pins: a range is not a version this
    check can render against, and the BOM carries none by design
    (verify-meta asserts that)."""
    out: dict[str, str] = {}
    in_components = False
    for line in open(f"{meta}/examples/customer-bom.yaml", encoding="utf-8"):
        line = line.rstrip("\n")
        if line.startswith("components:"):
            in_components = True
            continue
        if in_components and line and not line.startswith((" ", "#")):
            break
        m = COMPONENT_PIN_RE.match(line) if in_components else None
        if m:
            out[m.group(1)] = m.group(2)
    if not out:
        sys.exit(f"FAIL: {meta}/examples/customer-bom.yaml pins no components")
    return out


def pull(url: str, version: str, dest: str) -> str:
    """helm pull of one exact version; returns the chart version it unpacked."""
    err = ""
    for attempt in range(3):
        r = run(["helm", "pull", url, "--version", version, "--untar", "--untardir", dest])
        if r.returncode == 0:
            chart = open(f"{dest}/{url.rsplit('/', 1)[1]}/Chart.yaml").read()
            return re.search(r"^version: (\S+)", chart, re.M).group(1).strip("'\"")
        err = r.stderr
        time.sleep(5 * (attempt + 1))
    sys.exit(f"FAIL: could not pull {url} --version {version!r}\n{err}")


def check_harness_selector(manifest: str, what: str) -> None:
    """The platform Harness the kagent chart renders admits templates by the ONE
    label the Generic agent chart stamps. The meta chart blanks the chart's own
    default key (kagent.dev/harness: "") and the line's template drops the empty
    value — this is where that contract is proven against the chart the range
    resolves to, on the rendered object (giantswarm/agent-platform#418)."""
    harness = [d for d in manifest.split("\n---") if re.search(r"^kind: Harness$", d, re.M)]
    if len(harness) != 1:
        sys.exit(f"FAIL: {what} renders {len(harness)} Harness objects, expected the one platform Harness")
    m = re.search(r"^ {6}matchLabels:\n((?: {8}\S.*\n)+)", harness[0] + "\n", re.M)
    labels = dict(line.strip().split(": ", 1) for line in m.group(1).splitlines()) if m else {}
    if labels != {HARNESS_LABEL: "kagent"}:
        sys.exit(
            f"FAIL: {what} renders the platform Harness selecting by {labels or 'nothing'}; the admission contract is "
            f"{HARNESS_LABEL}=kagent alone — the chart's own kagent.dev/harness must be dropped (the meta chart forwards it "
            "empty; the line's Harness template drops an empty-valued selector label from 0.11.0-gs.9, giantswarm/agent-platform#418)"
        )
    print(f"ok: {what} renders the platform Harness selecting by {HARNESS_LABEL}=kagent alone")


def main(meta: str) -> int:
    pins = bom_components(meta)
    on = [f"--set=components.{n}.enabled=true" for n in pins]
    wide = docs(render_meta(meta, [*QUICKSTART, *ALL_ON_INPUTS, *on]))
    pinned = docs(render_meta(meta, ["-f", f"{meta}/examples/customer-bom.yaml", *QUICKSTART, *ALL_ON_INPUTS, *on]))
    # What the render actually produced, which is the authority on what a
    # component is: every OCIRepository except the chart's own self-management
    # source, which is named after the release and is not a component.
    components = sorted(n for kind, n in wide if kind == "OCIRepository" and n != RELEASE)
    missing = sorted(set(pins) - set(components))
    if missing:
        sys.exit(
            f"FAIL: the BOM pins {', '.join(missing)} but the meta chart renders no OCIRepository for it — "
            "the BOM and the components have drifted apart, and these pins go unchecked"
        )
    kagent_tag = source(wide[("OCIRepository", "kagent")])[1].split()[0].lstrip(">=")  # the range's floor = the build the values name
    print(f"--> {len(components)} components, from the BOM's pins")
    for name in components:
        url, rng = source(wide[("OCIRepository", name)])
        _, pin = source(pinned[("OCIRepository", name)])
        values = hr_values(wide[("HelmRelease", name)])
        with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as f:
            f.write(values)
        # A chart of this repo has one version that matters — the working tree —
        # so both axes collapse to that single render.
        local = LOCAL_CHARTS.get(name)
        if local:
            chart = str(pathlib.Path(__file__).resolve().parent.parent / local)
            if not pathlib.Path(chart, "Chart.yaml").is_file():
                sys.exit(f"FAIL: {name}: no chart at {chart} — LOCAL_CHARTS names a path this repo does not have")
            axes = [("working tree", f"{rng} (range), {pin} (BOM pin)")]
        else:
            axes = [("range", rng), ("BOM pin", pin)]
        tags = [] if local else registry_tags(url)
        for label, constraint in axes:
            if local:
                resolved_chart, version = chart, "working tree"
            else:
                version = fluxsemver.resolve(tags, constraint) or fallback(name, constraint, tags, kagent_tag)
            with tempfile.TemporaryDirectory() as d:
                resolved = version if local else pull(url, version, d)
                if not local:
                    resolved_chart = f"{d}/{name}"
                r = run(["helm", "template", name, resolved_chart, "-n", "agent-platform", "-f", f.name, *API_VERSIONS])
                if r.returncode != 0:
                    sys.exit(
                        f"FAIL: {name} {resolved} (the {label} {constraint!r}) rejects the values the meta chart "
                        f"forwards to it\n{r.stderr}"
                    )
                if name in MANAGERS and f"--kagent-api-version={KAGENT_API_VERSION}" not in r.stdout:
                    sys.exit(
                        f"FAIL: {name} {resolved} (the {label} {constraint!r}) does not render the forwarded "
                        f"kagent.apiVersion as its --kagent-api-version={KAGENT_API_VERSION} argument"
                    )
                if name == "kagent":
                    check_harness_selector(r.stdout, f"{name} {resolved} (the {label} {constraint!r})")
                kinds = len(re.findall(r"^kind: ", r.stdout, re.M))
                print(f"ok: {name} {resolved} ({label} {constraint!r}) renders the forwarded values ({kinds} objects)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
