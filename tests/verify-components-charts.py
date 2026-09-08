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

Network: pulls from gsoci.azurecr.io and ghcr.io (three attempts each).
Deliberately stdlib-only: the CI image has no PyYAML.
"""

import re
import subprocess
import sys
import tempfile
import time

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


def source(doc: str) -> tuple[str, str]:
    url = re.search(r"^  url: (\S+)", doc, re.M).group(1)
    semver = re.search(r'semver: "([^"]+)"', doc).group(1)
    return url, semver


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
        url, rng = source(wide[("OCIRepository", name)])
        _, pin = source(pinned[("OCIRepository", name)])
        with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as f:
            f.write(hr_values(wide[("HelmRelease", name)]))
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
