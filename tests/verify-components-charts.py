#!/usr/bin/env python3
"""Pull the component charts whose values the meta chart composes — the seven
extras of the standalone chart, the two managers and the kagent line's two charts
— and render each with the values the meta chart forwards to it.

The meta chart cannot know whether a component chart accepts the block it
forwards: the block is inlined into a HelmRelease and validated by helm-controller
on the cluster, against the chart the OCIRepository resolved. This check does that
validation here, twice per component: at the version the `versionRange` resolves
to today (what a dogfooding installation gets) and at the exact pin in
examples/customer-bom.yaml (what a BOM installation gets). The render uses the
quick-start inputs Backstage and mcp-kubernetes require (global.domain and
global.identity) and the API groups the charts' optional objects need.

The two managers matter most: their charts validate values with a CLOSED schema
(additionalProperties: false at the root), so one key the meta chart forwards —
from the `agent-manager:` / `model-manager:` block or derived by
agent-platform.componentDerivedValues — that the chart the range resolves to
does not declare fails the HelmRelease on every installation that turns the
component on. agent-manager needs the kagent component on, so the render turns
kagent on too. The kagent charts have no schema; rendering them proves the
forwarded block templates (the WorkerPool's required image, the Substrate
wiring, the bundled Postgres image) against the chart the values name.

A range is resolved the way Flux does — the registry's tag list, the highest
semver the constraint admits (tests/fluxsemver.py: Masterminds semantics, a
`-gs.N` prerelease included) — because `helm pull --version <range>` reads the
constraint with its own semver and, for the kagent line's prerelease releases,
differently. While a range or a BOM pin names a release that is not published
yet (UNRELEASED below: a sibling's 1.0 or the kagent line's tag), the block is
rendered against the newest chart the line has instead — the kagent build the
values name (`kagent.tag`) or the newest 0.x of a manager — and the fallback is
printed; an entry leaves UNRELEASED with the release it waits for.

Network: pulls from gsoci.azurecr.io and ghcr.io (three attempts each); the tag
list comes from the registry's anonymous `/v2/<repo>/tags/list`.
Deliberately stdlib-only: the CI image has no PyYAML.
"""

import json
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

import fluxsemver

EXTRAS = [
    "backstage", "mcp-kubernetes", "cloudnative-pg",
    "kserve-crd", "kserve-resources", "kserve-llmisvc-crd", "kserve-llmisvc-resources",
]
MANAGERS = ["model-manager", "agent-manager"]
# The kagent.dev API version the meta chart pins into both managers
# (`kagent.apiVersion`, giantswarm/agent-platform#401); their charts must render
# it as the container's `--kagent-api-version`, the pod-template change that
# rolls the Deployments on the cut-over.
KAGENT_API_VERSION = "v1alpha3"
KAGENT = ["kagent-crds", "kagent"]
# The Substrate line's two charts: rendered with the substrate: block the meta
# chart forwards (the derived postgres.enabled boolean, the CNPG connection
# Secret reference) — the substrate chart has no schema, so this is what proves
# the forwarded keys (postgres.connectionStringSecretRef, atelet.nodeSelector /
# tolerations / affinity) exist in the pinned build.
SUBSTRATE = ["substrate-crds", "substrate"]
COMPONENTS = [*EXTRAS, *MANAGERS, *KAGENT, *SUBSTRATE]
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
API_VERSIONS = [
    "--api-versions", "cilium.io/v2",
    "--api-versions", "monitoring.coreos.com/v1",
    "--api-versions", "cert-manager.io/v1",
    "--api-versions", "gateway.networking.k8s.io/v1",
]


def run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, check=False)


def render_meta(meta: str, flags: list[str]) -> str:
    r = run(["helm", "template", "t", meta, *flags])
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
    published: the kagent build the values name (kagent.tag — the chart version
    of the same build), the branch build RENDER_AGAINST names, else the newest
    release tag."""
    if name not in UNRELEASED:
        sys.exit(f"FAIL: no published version of {name} satisfies {constraint!r}, and nothing says it is expected (UNRELEASED)")
    chosen = kagent_tag if name in KAGENT else RENDER_AGAINST.get(name) or fluxsemver.resolve(tags, ">=0.0.0")
    if not chosen or chosen not in tags:
        sys.exit(f"FAIL: no published chart of {name} to render against while {constraint!r} waits for {UNRELEASED[name]}")
    print(f"NOTE: {name}: {constraint!r} matches no published chart yet (waits for {UNRELEASED[name]}); rendering against {chosen}")
    return chosen


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


def main(meta: str) -> int:
    follow_kagent = {"kagent-crds", *SUBSTRATE}  # no switch of their own: on with kagent
    on = [f"--set=components.{n}.enabled=true" for n in COMPONENTS if n not in follow_kagent]
    wide = docs(render_meta(meta, [*QUICKSTART, *on]))
    pinned = docs(render_meta(meta, ["-f", f"{meta}/examples/customer-bom.yaml", *QUICKSTART, *on]))
    m = re.search(r"^tag: \"?([^\"\n]+)\"?$", hr_values(wide[("HelmRelease", "kagent")]), re.M)
    kagent_tag = m.group(1) if m else ""
    for name in COMPONENTS:
        url, rng = source(wide[("OCIRepository", name)])
        _, pin = source(pinned[("OCIRepository", name)])
        values = hr_values(wide[("HelmRelease", name)])
        with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as f:
            f.write(values)
        tags = registry_tags(url)
        for label, constraint in (("range", rng), ("BOM pin", pin)):
            version = fluxsemver.resolve(tags, constraint) or fallback(name, constraint, tags, kagent_tag)
            with tempfile.TemporaryDirectory() as d:
                resolved = pull(url, version, d)
                r = run(["helm", "template", name, f"{d}/{name}", "-n", "agent-platform", "-f", f.name, *API_VERSIONS])
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
                kinds = len(re.findall(r"^kind: ", r.stdout, re.M))
                print(f"ok: {name} {resolved} ({label} {constraint!r}) renders the forwarded values ({kinds} objects)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
