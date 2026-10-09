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
chart's guard.

The meta chart is rendered in every SHAPE an installation can give it: the
vanilla cluster (no --api-versions, every `auto` knob off) and the Giant Swarm
fleet (API_VERSIONS: cilium network policies, monitoring objects, the kagent
OTLP headers), each with the platform's CNPG Cluster (postgres.enabled) and
with the bundled Postgres a lab or a quick start runs (substrate's
single-instance StatefulSet). A component's forwarded values differ between
shapes, and a fleet-only key against a closed schema fails every installation
while the vanilla render passes (giantswarm/agent-platform#467); every distinct
block is rendered, the byte-identical ones once.

A range is resolved the way Flux does — the registry's tag list, the highest
semver the constraint admits (tests/fluxsemver.py: Masterminds semantics, a
a prerelease included) — because `helm pull --version <range>` reads the
constraint with its own semver and, for the kagent line's prerelease releases,
differently. A BOM pin that resolves to no published tag FAILS: a BOM no
installation can install is a broken BOM, not something to render a substitute
for. The layer is resolved the way Flux does too: every rendered OCIRepository
selects the Helm chart layer, and the resolved artifact must carry it
(flux_layer) — `helm pull` finds the chart by media type wherever it sits, so a
pull that succeeds says nothing about the OCIRepository (cloudnative-pg 0.29.1,
whose provenance layer sorts first, giantswarm/agent-platform#649). A RANGE
that admits nothing published yet (a line re-pinned ahead of its release) is
rendered against the newest chart the line has while UNRELEASED names the
release it waits for (fallback()); the entry goes with the release.

`--strict` is the tag pipeline's run (giantswarm/agent-platform#624): a release
naming a chart nobody can pull is a release nobody can install, so UNRELEASED and
RENDER_AGAINST do not apply, and a range that admits nothing published, or a BOM
pin that is not published, FAILS naming the component and the version the
release waits for — the range's floor. A GitHub release or a tag of the
component is not evidence its chart exists; a tag pipeline that failed after
tagging (klaus-gateway 1.20.0) leaves neither chart nor image. A release refused
this way is recovered by rerunning the tag's workflow from failed once the chart
is out; the branch pipeline keeps rendering against the fallbacks.

A branch whose range waits that way still pushes a dev build of the meta chart,
and nothing installs it (giantswarm/agent-platform#823). The branch run records
every range rendered against a fallback in Chart.yaml's annotation
agent-platform.giantswarm.io/unreleased (UNRELEASED_ANNOTATION: JSON, component
-> {versionRange, waitsFor}, waitsFor the range's floor) and FAILS when the
annotation says anything else; `--write` (`make sync-unreleased`) writes it. The
dev build carries it: the meta chart's pre-install hook refuses the install at
once naming the component and the version
(templates/hooks/unreleased-components.yaml), and an installer reads it before it
applies anything. A release ignores it: --strict passed, so it waits for nothing.

Network: pulls from gsoci.azurecr.io, and from ghcr.io for the CloudNativePG
chart (three attempts each); the tag
list comes from the registry's anonymous `/v2/<repo>/tags/list`. Every Helm call
is bounded (TIMEOUT). PyYAML is in the CI image (the job installs python3-yaml).

The components are independent, and the work is registry round trips and Helm
subprocesses: the meta renders and the components run on WORKERS threads. Each
component's lines are held back and printed in roster order once it is done
(Lines, held()), so the output reads as a sequential run's; the first component
in roster order that fails ends the run with its FAIL, as before.
"""

import concurrent.futures
import hashlib
import importlib.util
import io
import json
import pathlib
import re
import subprocess
import sys
import tempfile
import threading
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
# Threads for the meta renders and the components: the work waits on the
# registry and on Helm, not on Python.
WORKERS = 8
MANAGERS = ["model-manager", "agent-manager"]
# The kagent.dev API version the meta chart pins into both managers
# (`kagent.apiVersion`, giantswarm/agent-platform#401); their charts must render
# it as the container's `--kagent-api-version`, the pod-template change that
# rolls the Deployments on the cut-over.
KAGENT_API_VERSION = "v1alpha3"
KAGENT = ["kagent-crds", "kagent"]
# component -> the release its RANGE waits for. While nothing the range admits
# is published, the forwarded block is rendered against the newest chart the
# line has (see fallback()); the entry goes when the release exists.
UNRELEASED: dict[str, str] = {}
# component -> a published branch build that already carries the schema of the
# release UNRELEASED waits for, when the newest release's schema would refuse a
# value the meta chart forwards. The entry goes with the release.
RENDER_AGAINST: dict[str, str] = {
    "agent-manager": "1.9.3-rf107e304t20261006134136h2486711",  # giantswarm/agent-manager#101
}
# The layer every OCIRepository of the meta chart selects, and the manifest
# types a Helm chart artifact is fetched as.
HELM_CHART_LAYER = "application/vnd.cncf.helm.chart.content.v1.tar+gzip"
OCI_MANIFEST = "application/vnd.oci.image.manifest.v1+json, application/vnd.docker.distribution.manifest.v2+json"
# An exact version, prerelease included (a dev build is one) — what a BOM
# line may carry; a range is not a version this check can render "the pin" at.
EXACT_RE = re.compile(r"^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?$")
# --strict: the tag pipeline's run — no fallback, a release that names an
# unpublished chart fails (main() sets it from the command line).
STRICT = False
# The Chart.yaml annotation a dev build names the component releases it waits for
# in (see above), what the branch run found to put there, and --write.
UNRELEASED_ANNOTATION = "agent-platform.giantswarm.io/unreleased"
WAITS: dict[str, dict[str, str]] = {}
WRITE = False
# A coding agent's Harness on top of the reference shape: the kagent block it
# forwards is rendered at the range with the others.
CODING_AGENTS = "coding agents (ci/test-coding-agents-values.yaml)"


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


def recorded_waits(meta: str) -> dict[str, dict[str, str]]:
    """What Chart.yaml's UNRELEASED_ANNOTATION says the chart waits for."""
    with open(f"{meta}/Chart.yaml", encoding="utf-8") as f:
        raw = (yaml.safe_load(f).get("annotations") or {}).get(UNRELEASED_ANNOTATION)
    return json.loads(raw) if raw else {}


def write_waits(meta: str, waits: dict[str, dict[str, str]]) -> None:
    """Write `waits` as Chart.yaml's UNRELEASED_ANNOTATION, the last line of its
    annotations, or drop the line when nothing waits; the rest of the file as it is."""
    path = f"{meta}/Chart.yaml"
    with open(path, encoding="utf-8") as f:
        text = re.sub(rf"^  {re.escape(UNRELEASED_ANNOTATION)}: .*\n", "", f.read(), flags=re.M)
    if waits:
        end = re.search(r"^annotations:\n(?:  .*\n)*", text, re.M).end()
        text = f"{text[:end]}  {UNRELEASED_ANNOTATION}: '{json.dumps(waits, sort_keys=True, separators=(',', ':'))}'\n{text[end:]}"
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def check_waits(meta: str, waits: dict[str, dict[str, str]]) -> None:
    """The branch run's verdict on the annotation: it names exactly what the
    ranges wait for (written first under --write), and the output says what a
    dev build of this branch waits for."""
    if WRITE:
        write_waits(meta, waits)
    if (recorded := recorded_waits(meta)) != waits:
        fail(f"{meta}/Chart.yaml annotation {UNRELEASED_ANNOTATION} is {recorded or 'absent'}, the ranges wait for {waits or 'nothing'}: "
             "a dev build must name the component releases it waits for, and a published one must not refuse its install — run `make sync-unreleased`")
    if waits:
        named = ", ".join(f"{n} {w['waitsFor']} ({w['versionRange']!r})" for n, w in sorted(waits.items()))
        print(f"NOTE: a dev build of this branch waits for a component release: {named}. Its install fails at the meta chart's pre-install hook, "
              f"and the tag pipeline refuses the release, until that chart is published ({UNRELEASED_ANNOTATION} in Chart.yaml)")
    else:
        print(f"ok: every range is published; Chart.yaml carries no {UNRELEASED_ANNOTATION}")
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
    # The platform Cluster on: the Substrate render takes the CNPG path (the
    # derived connection Secret reference); SHAPES adds the bundled one.
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

# Components without a toggle of their own: each follows its switch, and a
# toggle set beside it is refused by the meta chart's guard.
SWITCHED = {"workspace-manager": "workspaces.enabled"}

API_VERSIONS = [
    "--api-versions", "cilium.io/v2",
    "--api-versions", "monitoring.coreos.com/v1",
    "--api-versions", "cert-manager.io/v1",
    "--api-versions", "gateway.networking.k8s.io/v1",
    "--api-versions", "autoscaling.k8s.io/v1",
]
# The shapes the meta chart is rendered in, name -> the flags on top of
# QUICKSTART. The first is the reference render the roster check reads.
SHAPES: dict[str, list[str]] = {
    "vanilla/CNPG": [],
    "fleet/CNPG": API_VERSIONS,
    "vanilla/bundled Postgres": ["--set", "postgres.enabled=false"],
    "fleet/bundled Postgres": [*API_VERSIONS, "--set", "postgres.enabled=false"],
}


def where(forwarded: dict[str, list[str]]) -> str:
    """Where one values block is forwarded: axis label -> shapes, as one
    readable clause; axes with the same shapes are named together."""
    by_shapes: dict[tuple[str, ...], list[str]] = {}
    for label, shapes in forwarded.items():
        by_shapes.setdefault(tuple(shapes), []).append(label)
    return "; ".join(
        f"{' = '.join(labels)}, {'every shape' if len(shapes) == len(SHAPES) else ' and '.join(shapes)}"
        for shapes, labels in by_shapes.items()
    )


def fail(msg: str) -> None:
    sys.exit(f"FAIL: {msg}")


class Lines:
    """sys.stdout while the components run in parallel: what a worker thread
    prints goes to its own buffer (held()), the main thread's straight through."""

    def __init__(self, out):
        self.out, self.local = out, threading.local()

    def write(self, text: str) -> int:
        buf = getattr(self.local, "buf", None)
        return (self.out if buf is None else buf).write(text)

    def flush(self) -> None:
        self.out.flush()


def held(check, *args) -> tuple[str, BaseException | None]:
    """Run check(*args) in a worker with its output held back: the lines it
    printed, and the exception it ended in (fail() raises SystemExit) or None."""
    lines = sys.stdout
    lines.local.buf = io.StringIO()
    try:
        check(*args)
        return lines.local.buf.getvalue(), None
    except BaseException as e:  # handed to the main thread, which raises it in roster order
        return lines.local.buf.getvalue(), e
    finally:
        lines.local.buf = None


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


# Anonymous bearer tokens, one per OCI repository (`oci://host/path`).
TOKENS: dict[str, str] = {}


def registry_get(url: str, target: str, accept: str = "application/json") -> tuple[dict, dict]:
    """GET one distribution API URL of an OCI repository (`oci://host/path`)
    anonymously, answering the token challenge (again, once, when a held token
    is refused); the JSON body and the response headers."""
    for attempt in range(2):
        headers = {"Accept": accept}
        if url in TOKENS:
            headers["Authorization"] = f"Bearer {TOKENS[url]}"
        try:
            with urllib.request.urlopen(urllib.request.Request(target, headers=headers), timeout=60) as r:
                return json.load(r), r.headers
        except urllib.error.HTTPError as e:
            if e.code != 401 or attempt:
                raise
            challenge = dict(re.findall(r'(\w+)="([^"]*)"', e.headers.get("Www-Authenticate", "")))
            with urllib.request.urlopen(f"{challenge['realm']}?service={challenge['service']}&scope={challenge['scope']}", timeout=60) as t:
                body = json.load(t)
            TOKENS[url] = body.get("access_token") or body.get("token")
    raise AssertionError("unreachable")


def registry_tags(url: str) -> list[str]:
    """Every tag of an OCI repository (`oci://host/path`), anonymously,
    following the distribution API's Link pagination."""
    host, _, path = url.removeprefix("oci://").partition("/")
    next_url, tags = f"https://{host}/v2/{path}/tags/list?n=1000", []
    for _ in range(100):
        body, headers = registry_get(url, next_url)
        tags += body.get("tags") or []
        m = re.search(r"<([^>]+)>", headers.get("Link", ""))
        if not m:
            return tags
        next_url = m.group(1) if m.group(1).startswith("http") else f"https://{host}{m.group(1)}"
    fail(f"the tag list of {url} did not end after 100 pages")


def layer_selector(doc: str) -> str:
    """The media type a rendered OCIRepository's layerSelector names. Every
    OCIRepository of the meta chart must name the Helm chart's: without a
    selector source-controller takes layers[0], and on a signed chart that is
    the provenance on about every other release (`helm push` orders the two
    layers by digest; cloudnative-pg 0.29.1, giantswarm/agent-platform#649)."""
    m = re.search(r"^  layerSelector:\n    mediaType: (\S+)\n    operation: copy$", doc, re.M)
    name = re.search(r"^  name: (\S+)", doc, re.M).group(1)
    if not m or m.group(1) != HELM_CHART_LAYER:
        fail(f"the OCIRepository {name} does not select the Helm chart layer (layerSelector {{mediaType: {HELM_CHART_LAYER}, operation: copy}}): "
             "source-controller then takes layers[0], which on a signed chart is its provenance about every other release")
    return m.group(1)


def flux_layer(name: str, url: str, version: str, doc: str) -> None:
    """The layer source-controller takes from the artifact at `version` must be
    the Helm chart: the first layer of the selector's media type
    (OCIRepositoryReconciler.selectLayer; none is a failed OCIRepository).
    `helm pull` picks by media type and succeeds either way, so the pull below
    does not see this."""
    selector = layer_selector(doc)
    host, _, path = url.removeprefix("oci://").partition("/")
    try:
        manifest, _ = registry_get(url, f"https://{host}/v2/{path}/manifests/{version}", OCI_MANIFEST)
    except urllib.error.URLError as e:
        fail(f"{name} {version}: the manifest at {url} could not be fetched: {e}")
    layers = [layer["mediaType"] for layer in manifest.get("layers") or []]
    if selector not in layers:
        fail(f"{name} {version}: the artifact at {url} has no {selector} layer ({layers}); the OCIRepository fails to find one")
    print(f"ok: {name} {version}: source-controller takes the Helm chart, layer {layers.index(selector)} of {len(layers)} ({', '.join(layers)})")


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
    chosen = RENDER_AGAINST.get(name) or (kagent_tag if name in KAGENT else fluxsemver.resolve(tags, ">=0.0.0"))
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


_KAGENT_CRDS: dict[str, dict] = {}
_KAGENT_CRDS_LOCK = threading.Lock()


def kagent_crds() -> tuple[object, dict[str, dict]]:
    """verify-kagent-crds.py (its structural validator) and the Harness CRD at
    the floor of components.kagent-crds.versionRange, loaded once."""
    with _KAGENT_CRDS_LOCK:
        if not _KAGENT_CRDS:
            spec = importlib.util.spec_from_file_location("kagent_crds", REPO_ROOT / "tests" / "verify-kagent-crds.py")
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            _KAGENT_CRDS["module"] = module
            _KAGENT_CRDS["crds"] = module.load_crds()
        return _KAGENT_CRDS["module"], _KAGENT_CRDS["crds"]


def check_harnesses(manifest: str, values: str, what: str) -> None:
    """The kagent chart renders the platform Harness plus one per forwarded
    kagent.harnesses entry, each valid against the pinned Harness CRD. The
    platform Harness carries no admission selector: an Agent names its Harness
    by spec.harnessRef, and the line's CRD has no allowedAgentTemplates. A
    claude entry reaches its Harness as forwarded (snapshot location, env,
    limits) and takes the chart's claude-harness image at its digest."""
    entries = (yaml.safe_load(values) or {}).get("harnesses") or []
    rendered = [d for d in yaml.safe_load_all(manifest) if isinstance(d, dict) and d.get("kind") == "Harness"]
    if len(rendered) != 1 + len(entries):
        fail(f"{what} renders {len(rendered)} Harness objects, expected the platform Harness and {len(entries)} from kagent.harnesses")
    if any(h.get("apiVersion") != "api.kagent.dev/v1alpha3" for h in rendered):
        fail(f"{what} renders a Harness outside api.kagent.dev/v1alpha3, the group the line serves")
    by_name = {h["metadata"]["name"]: h for h in rendered}
    platform = [h for name, h in by_name.items() if name not in {e["name"] for e in entries}]
    if len(platform) != 1 or "allowedAgentTemplates" in platform[0]["spec"]:
        fail(f"{what} renders the platform Harness with an admission selector, or not at all; the line's Harness has none (an Agent names it by spec.harnessRef)")
    for entry in entries:
        spec = by_name.get(entry["name"], {}).get("spec")
        if spec is None:
            fail(f"{what} renders no Harness for the kagent.harnesses entry {entry['name']!r}")
        if entry.get("runtime", "claude") != "claude":
            continue
        got = {
            "claude": "claude" in spec,
            "snapshotLocation": spec.get("substrate", {}).get("snapshotPolicy", {}).get("location"),
            "env": spec.get("env"),
            "limits": spec.get("claude", {}).get("limits"),
        }
        want = {"claude": True, "snapshotLocation": entry.get("snapshotLocation"), "env": entry.get("env"), "limits": entry.get("limits")}
        if got != want:
            fail(f"{what} renders the claude Harness {entry['name']!r} off its entry:\n  got      {got}\n  expected {want}")
        image = spec.get("workload", {}).get("image", "")
        if "image" not in entry and not re.search(r"/claude-harness@sha256:[0-9a-f]{64}$", image):
            fail(f"{what} renders the claude Harness {entry['name']!r} with image {image!r}, not the chart's claude-harness at its digest")
    module, crds = kagent_crds()
    if errors := [e for h in rendered for e in module.validate(h, crds, what)]:
        fail("a rendered Harness does not validate against the pinned Harness CRD:\n  " + "\n  ".join(errors))
    print(f"ok: {what} renders the platform Harness (no admission selector) and {len(entries)} from kagent.harnesses, "
          f"valid against the Harness CRD at {module.KAGENT_LINE_REF}")


def render_component(name: str, chart_dir: str, values: str, what: str, tmp: str) -> None:
    """One `helm template` of a component chart with one forwarded values block,
    and the per-component assertions on what it rendered."""
    values_file = f"{tmp}/{name}-{hashlib.sha256(values.encode()).hexdigest()[:12]}.yaml"
    with open(values_file, "w", encoding="utf-8") as f:
        f.write(values)
    r = run(["helm", "template", name, chart_dir, "-n", "agent-platform", "-f", values_file, *API_VERSIONS])
    if r.returncode != 0:
        fail(f"{what} rejects the values the meta chart forwards to it\n{r.stderr}")
    if name in MANAGERS and f"--kagent-api-version={KAGENT_API_VERSION}" not in r.stdout:
        fail(f"{what} does not render the forwarded kagent.apiVersion as its --kagent-api-version={KAGENT_API_VERSION} argument")
    if name == "kagent":
        check_harnesses(r.stdout, values, what)
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
    on = [f"--set={SWITCHED.get(n, f'components.{n}.enabled')}=true" for n in components]
    base = [*QUICKSTART, *ALL_ON_INPUTS, *on]
    bom = ["-f", f"{meta}/examples/customer-bom.yaml"]
    # shape -> (the defaults' render, the BOM's render)
    with concurrent.futures.ThreadPoolExecutor(WORKERS) as pool:
        meta_renders = [pool.submit(render_meta, meta, [*values, *base, *flags]) for flags in SHAPES.values() for values in ([], bom)]
        coding_render = pool.submit(render_meta, meta, [*base, "-f", f"{meta}/ci/test-coding-agents-values.yaml"])
        manifests = iter([docs(f.result()) for f in meta_renders])
        coding = docs(coding_render.result())
    renders = {shape: (next(manifests), next(manifests)) for shape in SHAPES}
    wide, pinned = next(iter(renders.values()))
    # The OCIRepository and the HelmRelease are named after the entry's chart.
    charts = {n: c["chart"] for n, c in components.items()}
    for shape, (w, _) in renders.items():
        rendered = {n for kind, n in w if kind == "OCIRepository" and n != RELEASE}
        if rendered != set(charts.values()):
            fail(f"the {shape} render's OCIRepositories {sorted(rendered)} are not the roster's charts {sorted(charts.values())}")
    for w, p in renders.values():
        for (kind, _), doc in [*w.items(), *p.items()]:
            if kind == "OCIRepository":
                layer_selector(doc)
    kagent_oci = wide.get(("OCIRepository", charts.get("kagent", "kagent")))
    kagent_tag = source(kagent_oci)[1].split()[0].lstrip(">=") if kagent_oci else ""  # the range's floor = the build the values name
    print(f"--> {len(components)} components (the meta chart's roster): {len(pins)} pinned by the BOM, {len(released)} released with the chart")
    print(f"--> {len(SHAPES)} shapes ({', '.join(SHAPES)}): each distinct forwarded block rendered once")
    if STRICT:
        print("--> strict (the tag pipeline): every range and every BOM pin must resolve to a published chart; UNRELEASED and RENDER_AGAINST do not apply")
    def check(name: str, tmp: str) -> None:
        """One component: its range and its BOM pin resolved, pulled and
        rendered with every distinct block the meta chart forwards to it."""
        chart = charts[name]
        url, rng = source(wide[("OCIRepository", chart)])
        _, pin = source(pinned[("OCIRepository", chart)])
        for shape, (w, p) in renders.items():
            if (source(w[("OCIRepository", chart)])[1], source(p[("OCIRepository", chart)])[1]) != (rng, pin):
                fail(f"{name}: the {shape} render gives another range or pin than the {next(iter(SHAPES))} render — a shape must not move a version")
        # (axis, values) -> the shapes that forward them, in SHAPES order.
        blocks: dict[tuple[str, str], list[str]] = {}
        for shape, (w, p) in renders.items():
            blocks.setdefault(("range", hr_values(w[("HelmRelease", chart)])), []).append(shape)
            blocks.setdefault(("BOM pin", hr_values(p[("HelmRelease", chart)])), []).append(shape)
        if name == "kagent":
            blocks.setdefault(("range", hr_values(coding[("HelmRelease", chart)])), []).append(CODING_AGENTS)
        if name in released:
            # One version matters, the working tree's: every distinct block of
            # both renders against it, once.
            chart_dir = REPO_ROOT / "helm" / chart
            if not (chart_dir / "Chart.yaml").is_file():
                fail(f"{name}: no chart at {chart_dir} — releasedWithChart names a chart this repository does not have")
            if rng != pin:
                fail(f"{name}: the BOM render gives the chart released with the meta chart another version ({pin!r}) than the defaults ({rng!r})")
            seen: dict[str, dict[str, list[str]]] = {}
            for (axis, values), shapes in blocks.items():
                seen.setdefault(values, {})[f"the {axis}"] = shapes
            for values, forwarded in seen.items():
                render_component(name, str(chart_dir), values, f"{name} working tree (released with the chart, {rng}; {where(forwarded)})", tmp)
            return
        if pin != pins[name]:
            fail(f"{name}: the BOM render carries {pin!r} while examples/customer-bom.yaml pins {pins[name]!r} — the pin did not reach the OCIRepository")
        tags = registry_tags(url)
        version_range = fluxsemver.resolve(tags, rng)
        if not version_range:
            version_range = fallback(name, url, rng, tags, kagent_tag)
            own = components[name]["versionRange"]
            WAITS[name] = {"versionRange": own, "waitsFor": floor(own)}
        version_pin = fluxsemver.resolve(tags, pin)
        if not version_pin and pin == UNRELEASED.get(name) and not STRICT:
            version_pin = fallback(name, url, pin, tags, kagent_tag)
        if not version_pin:
            fail(f"{name}: the BOM pins {pin!r}, which {url} does not publish — no installation on this BOM can install it"
                 + (f"; {waits_for(name, url, pin, tags)}" if STRICT else ""))
        axes = {"range": (rng, version_range), "BOM pin": (pin, version_pin)}
        # (version, values) -> where it is forwarded; a version is checked and pulled once.
        jobs: dict[tuple[str, str], dict[str, list[str]]] = {}
        for (axis, values), shapes in blocks.items():
            constraint, version = axes[axis]
            jobs.setdefault((version, values), {})[f"the {axis} {constraint!r}"] = shapes
        pulled: dict[str, str] = {}
        for (version, values), forwarded in jobs.items():
            chart_dir = f"{tmp}/{name}/{version}"
            if version not in pulled:
                flux_layer(name, url, version, wide[("OCIRepository", chart)])
                pulled[version] = pull(url, version, chart_dir)
            render_component(name, f"{chart_dir}/{chart}", values, f"{name} {pulled[version]} ({where(forwarded)})", tmp)

    sys.stdout = Lines(sys.stdout)
    try:
        with tempfile.TemporaryDirectory() as tmp, concurrent.futures.ThreadPoolExecutor(WORKERS) as pool:
            checks = [pool.submit(held, check, name, tmp) for name in sorted(components)]
            for c in checks:
                out, err = c.result()
                sys.stdout.write(out)
                if err:
                    pool.shutdown(cancel_futures=True)
                    raise err
    finally:
        sys.stdout = sys.stdout.out
    if not STRICT:
        check_waits(meta, WAITS)
    return 0


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if a not in ("--strict", "--write")]
    STRICT, WRITE = "--strict" in sys.argv, "--write" in sys.argv
    if len(args) != 1 or (STRICT and WRITE):
        sys.exit(f"usage: {sys.argv[0]} <meta chart dir> [--strict | --write]")
    sys.exit(main(args[0]))
