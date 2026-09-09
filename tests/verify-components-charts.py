#!/usr/bin/env python3
"""Pull the seven component charts of the standalone chart's extras and render each
with the values the meta chart forwards to it.

The meta chart cannot know whether a component chart accepts the block it
forwards: the block is inlined into a HelmRelease and validated by helm-controller
on the cluster, against the chart the OCIRepository resolved. This check does that
validation here, twice per component: at the version the wide `versionRange`
resolves to today (what a dogfooding installation gets) and at the exact pin in
examples/customer-bom.yaml (what a BOM installation gets). The render uses the
quick-start inputs Backstage and mcp-kubernetes require (global.domain and
global.identity) and the API groups the charts' optional objects need.

A component on a dev channel (components.<name>.semverFilter) is resolved the
way Flux resolves it — the registry's tag list filtered by the regexp, then the
highest semver of what is left — because `helm pull --version <range>` knows no
filter and would pick any branch's dev build (or a stable tag) instead.

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

NEW = [
    "backstage", "mcp-kubernetes", "cloudnative-pg",
    "kserve-crd", "kserve-resources", "kserve-llmisvc-crd", "kserve-llmisvc-resources",
]
QUICKSTART = [
    "--set", "global.domain=example.com",
    "--set", "global.identity.issuerUrl=https://dex.example.com",
    "--set", "global.identity.clientId=agent-platform",
    "--set", "global.identity.existingSecret=agent-platform-idp",
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


def source(doc: str) -> tuple[str, str, str]:
    """An OCIRepository's url, semver range and semverFilter ("" when none)."""
    url = re.search(r"^  url: (\S+)", doc, re.M).group(1)
    semver = re.search(r'semver: "([^"]+)"', doc).group(1)
    m = re.search(r'^    semverFilter: (".*")$', doc, re.M)
    return url, semver, json.loads(m.group(1)) if m else ""


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


def semver_key(tag: str):
    """SemVer 2.0 precedence: numeric core, then a release above every
    prerelease, then the prerelease identifiers left to right (numeric < alnum,
    numeric by value, alnum lexically, a shorter prefix first)."""
    core, _, rest = tag.partition("-")
    pre = rest.split("+")[0]
    ids = [(0, int(i), "") if i.isdigit() else (1, 0, i) for i in pre.split(".")] if pre else []
    return (tuple(int(x) for x in core.split(".")), 0 if pre else 1, ids)


def resolve_filtered(url: str, constraint: str, semver_filter: str) -> str:
    """The tag Flux picks for a dev channel: the highest semver among the tags
    the filter admits. The constraint of a dev channel is a whole-line range
    (`>=X.0.0-0 <Y.0.0-0`), so the filter alone decides; the bounds are checked."""
    lo, hi = re.fullmatch(r">=(\d+)\.0\.0-0 <(\d+)\.0\.0-0", constraint).groups()
    matching = [t for t in registry_tags(url) if re.search(semver_filter, t) and re.match(r"\d+\.\d+\.\d+(-|$)", t)
                and int(lo) <= int(t.split(".")[0]) < int(hi)]
    if not matching:
        sys.exit(f"FAIL: no tag of {url} matches the dev-channel filter {semver_filter!r} within {constraint!r}")
    return max(matching, key=semver_key)


def pull(url: str, constraint: str, dest: str) -> str:
    """helm pull with a semver constraint; returns the resolved chart version."""
    err = ""
    for attempt in range(3):
        r = run(["helm", "pull", url, "--version", constraint, "--untar", "--untardir", dest])
        if r.returncode == 0:
            chart = open(f"{dest}/{url.rsplit('/', 1)[1]}/Chart.yaml").read()
            return re.search(r"^version: (\S+)", chart, re.M).group(1).strip("'\"")
        err = r.stderr
        time.sleep(5 * (attempt + 1))
    sys.exit(f"FAIL: could not pull {url} --version {constraint!r}\n{err}")


def main(meta: str) -> int:
    on = [f"--set=components.{n}.enabled=true" for n in NEW]
    wide = docs(render_meta(meta, [*QUICKSTART, *on]))
    pinned = docs(render_meta(meta, ["-f", f"{meta}/examples/customer-bom.yaml", *QUICKSTART, *on]))
    for name in NEW:
        url, rng, semver_filter = source(wide[("OCIRepository", name)])
        _, pin, pin_filter = source(pinned[("OCIRepository", name)])
        if pin_filter:
            sys.exit(f"FAIL: the BOM leaves the semverFilter {pin_filter!r} on {name}; an exact pin with a filter matches no tag")
        with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as f:
            f.write(hr_values(wide[("HelmRelease", name)]))
        if semver_filter:
            rng = resolve_filtered(url, rng, semver_filter)
            print(f"ok: {name} dev channel {semver_filter!r} resolves to {rng} today")
        for label, constraint in (("range", rng), ("BOM pin", pin)):
            with tempfile.TemporaryDirectory() as d:
                resolved = pull(url, constraint, d)
                r = run(["helm", "template", name, f"{d}/{name}", "-n", "agent-platform", "-f", f.name, *API_VERSIONS])
                if r.returncode != 0:
                    sys.exit(
                        f"FAIL: {name} {resolved} (the {label} {constraint!r}) rejects the values the meta chart "
                        f"forwards to it\n{r.stderr}"
                    )
                kinds = len(re.findall(r"^kind: ", r.stdout, re.M))
                print(f"ok: {name} {resolved} ({label} {constraint!r}) renders the forwarded values ({kinds} objects)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
