#!/usr/bin/env python3
"""Assert that every image the two charts' rendered defaults name comes from
gsoci.azurecr.io (giantswarm/agent-platform#575).

An installation that pulls from one trusted registry — every Giant Swarm
installation — must not have to override image defaults one by one, and a
signature policy that trusts one identity cannot admit an image nobody
re-signed. So the shipped defaults name gsoci copies only: the images the
charts' own objects run (hook Jobs, the pre-pull DaemonSet, the s3proxy façade,
the migrate Job, the ClusterServingRuntime), the chart sources the meta chart's
OCIRepositories pull, and the image knobs the meta chart forwards into every
component's HelmRelease values (a registry, a repository with a host, a full
reference, a list of references, an oci:// model URI).

The render is the charts' defaults — plus the switches that turn components on
and the non-image inputs their guards require (a domain, an identity, a parent
Gateway, the Harness's snapshot location), never an image override — in the
shapes an installation gets: the meta chart with its defaults and with the
engine and every component on under the fleet's API groups; the connectivity
chart with its defaults and with every component and model serving on. Every
rendered object is walked:

  * a container image (`image` of a containers / initContainers /
    ephemeralContainers entry, wherever the pod template sits — a Deployment, a
    Job, a DaemonSet, a ClusterServingRuntime) must be `gsoci.azurecr.io/…`; a
    bare name would resolve to Docker Hub;
  * an OCIRepository's `spec.url` must be `oci://gsoci.azurecr.io/…`;
  * a `registry` / `imageRegistry` / `*Registry` value must be gsoci (empty is
    the child chart's own default and passes; the child charts pin gsoci);
  * every other whitespace-free string that names a public registry followed
    by a path (docker.io/…, ghcr.io/…, quay.io/…, registry.k8s.io/… and the
    others in FOREIGN) or an `oci://` reference off gsoci fails — this catches
    a forwarded `repository`, `workerImage`, `reference`, `storageUri` or list
    entry whatever its key. A host without a path (a network policy's FQDN
    allow-list naming ghcr.io as an egress destination) is not a reference and
    passes;
  * a string under a key named `image`, or an entry of a map or list named
    `images`, that is shaped like an image reference (`name[:tag][@sha256:…]`,
    with or without a registry host) must be a gsoci reference: a bare name
    such as `postgres:18-alpine@sha256:…` names no host at all — Docker Hub on
    containerd, a refusal on CRI-O's short-name mode — and names no registry
    the FOREIGN rule could see; a forwarded `images:` map (the substrate
    chart's third-party defaults) is where these travel.

References whose gsoci copy does not exist yet are listed in PENDING with the
issue that lands it, each as a pattern over the object, the field and the
value: such a finding is reported and tolerated, and an entry that matches
nothing fails the check — the exception leaves with the reference, or the check
refuses to pass. Nothing else is exempt.

Needs PyYAML (the CI job installs it); HELM selects the binary. Usage:
verify-images.py <meta chart dir> <connectivity chart dir>
"""

import os
import re
import subprocess
import sys

import yaml

HELM = os.environ.get("HELM", "helm")
REGISTRY = "gsoci.azurecr.io"
# The public registries a shipped default must not name (giantswarm/agent-platform#575:
# docker.io, ghcr.io, quay.io, registry.k8s.io) and the hosts our lines and the
# mirrors' sources publish to.
FOREIGN = ("docker.io", "index.docker.io", "ghcr.io", "quay.io", "registry.k8s.io", "k8s.gcr.io", "gcr.io",
           "nvcr.io", "public.ecr.aws", "mcr.microsoft.com", "cr.kagent.dev", "cr.agentgateway.dev")
CONTAINER_LISTS = ("containers", "initContainers", "ephemeralContainers")
# An image reference under a key named `image` or in a map or list named
# `images`: an optional registry host (with a port), a path of lowercase
# components, an optional tag, an optional digest. Prose and bare tags or
# version strings do not match (a tag alone has no name before the colon).
IMAGE_REFERENCE = re.compile(
    r"(?:[A-Za-z0-9][A-Za-z0-9.-]*(?::[0-9]+)?/)?"
    r"[a-z0-9]+(?:[._-]+[a-z0-9]+)*(?:/[a-z0-9]+(?:[._-]+[a-z0-9]+)*)*"
    r"(?::[A-Za-z0-9_][A-Za-z0-9._-]{0,127})?(?:@sha256:[a-f0-9]{64})?")
# The API groups a Giant Swarm management cluster serves (Makefile FLEET_APIS):
# with them the Kyverno policies, the Cilium policies and the monitors render too.
FLEET_APIS = ["--api-versions", "kyverno.io/v1", "--api-versions", "cilium.io/v2", "--api-versions", "monitoring.coreos.com/v1",
              "--api-versions", "gateway.networking.k8s.io/v1", "--api-versions", "gateway.envoyproxy.io/v1alpha1"]
# The non-image inputs the guards require once components are on: a domain and
# an identity for Backstage and mcp-kubernetes, a parent Gateway for the routes,
# the Harness's snapshot store for kagent.
INPUTS = ["--set", "global.domain=ci.example.com", "--set", "global.identity.issuerUrl=https://dex.ci.example.com",
          "--set", "global.identity.clientId=agent-platform", "--set", "global.identity.existingSecret=agent-platform-idp",
          "--set", "global.gatewayApi.parentRefs[0].name=giantswarm-default", "--set", "global.gatewayApi.parentRefs[0].namespace=envoy-gateway-system",
          "--set", "kagent.harness.snapshotLocation=s3://ci-agent-snapshots/agents"]
# References whose gsoci copy is not published yet, each with the change that
# publishes it and removes the entry: a regular expression over `<path>=<value>`
# (the rendered object's kind/name, the field path and the value).
PENDING = {
    # The kagent line (giantswarm/kagent-upstream) and the Substrate line
    # (giantswarm/substrate) publish their charts to ghcr.io and retagger copies
    # images only; their release charts on gsoci and the switch of the two
    # chart sources: giantswarm/agent-platform#580. The upstream CloudNativePG
    # chart likewise, behind a Giant Swarm wrapper release on the same operator
    # line (giantswarm/agent-platform#580).
    r"OCIRepository/kagent(-crds)?\.spec\.url=oci://ghcr\.io/giantswarm/kagent/helm/kagent(-crds)?":
        "the kagent line's charts from gsoci (giantswarm/agent-platform#580)",
    r"OCIRepository/substrate(-crds)?\.spec\.url=oci://ghcr\.io/giantswarm/substrate/helm/substrate(-crds)?":
        "the Substrate line's charts from gsoci (giantswarm/agent-platform#580)",
    r"OCIRepository/cloudnative-pg\.spec\.url=oci://ghcr\.io/cloudnative-pg/charts/cloudnative-pg":
        "the CloudNativePG operator chart from gsoci (giantswarm/agent-platform#580)",
    # The substrate chart's rustfs-bucket-init Job runs Docker Hub's aws-cli by
    # its short name, and the meta chart forwards that value unchanged on
    # purpose (values.yaml substrate.images.awsCli): the Job is a release
    # resource whose pod template is immutable, so a changed image fails the
    # upgrade of every installation with the bundled store on; the connectivity
    # release carries the meta chart's whole tree, so the same value sits under
    # its values.substrate as well. Its gsoci copy
    # (giantswarm/aws-cli:2.17.0, the same digest) is named once the Substrate
    # line ships a release whose Job can be recreated (giantswarm/agent-platform#580).
    r"HelmRelease/(substrate|agent-platform-connectivity)\.spec\.values(\.substrate)?\.images\.awsCli=amazon/aws-cli:2\.17\.0@sha256:[a-f0-9]{64}":
        "the rustfs-bucket-init Job recreatable, its image the gsoci copy (giantswarm/agent-platform#580)",
}

findings: list[tuple[str, str, str]] = []
pending: list[tuple[str, str, str, str]] = []
matched_pending: set[str] = set()


def fail(message: str) -> None:
    print(f"FAIL: {message}", file=sys.stderr)
    sys.exit(1)


def render(chart: str, args: list[str]) -> list[dict]:
    result = subprocess.run([HELM, "template", "t", chart, *args], capture_output=True, text=True, check=False)
    if result.returncode != 0:
        fail(f"helm template {chart} {' '.join(args)} failed:\n{result.stderr}")
    return [d for d in yaml.safe_load_all(result.stdout) if isinstance(d, dict)]


def host_of(reference: str) -> str | None:
    """The registry host of an image reference or oci:// URL, None for a bare name."""
    ref = reference[len("oci://"):] if reference.startswith("oci://") else reference
    first = ref.split("/", 1)[0]
    if "/" in ref and ("." in first or ":" in first or first == "localhost"):
        return first
    return None


def record(shape: str, path: str, value: str, why: str) -> None:
    for pattern, reason in PENDING.items():
        if re.fullmatch(pattern, f"{path}={value}"):
            matched_pending.add(pattern)
            pending.append((shape, path, value, reason))
            return
    findings.append((shape, path, f"{value} ({why})"))


def is_gsoci(reference: str) -> bool:
    return host_of(reference) == REGISTRY


def walk(node, shape: str, kind: str, path: str, parent: str = "") -> None:
    """Every string of the object: `parent` is the key the node sits under (a
    list's entries share the list's key), so a map or list named `images` is
    known to hold references."""
    if isinstance(node, dict):
        for key, value in node.items():
            here = f"{path}.{key}" if path else key
            if isinstance(value, str):
                check_string(shape, kind, here, key, value, parent)
            elif key in CONTAINER_LISTS and isinstance(value, list):
                for i, container in enumerate(value):
                    if not isinstance(container, dict):
                        continue
                    if isinstance(container.get("image"), str) and not is_gsoci(container["image"]):
                        record(shape, f"{here}[{i}].image", container["image"], "a container image off gsoci")
                    walk({k: v for k, v in container.items() if k != "image"}, shape, kind, f"{here}[{i}]", key)
            else:
                walk(value, shape, kind, here, key)
    elif isinstance(node, list):
        for i, item in enumerate(node):
            if isinstance(item, str):
                check_string(shape, kind, f"{path}[{i}]", "", item, parent)
            else:
                walk(item, shape, kind, f"{path}[{i}]", parent)


def check_string(shape: str, kind: str, path: str, key: str, value: str, parent: str = "") -> None:
    if not value or re.search(r"\s", value):
        return  # prose (an annotation, a description) is not a reference
    if kind == "OCIRepository" and path == "spec.url":
        if not value.startswith(f"oci://{REGISTRY}/"):
            record(shape, path, value, "a chart source off gsoci")
        return
    if key in ("registry", "imageRegistry") or key.endswith("Registry"):
        if value != REGISTRY and not value.startswith(f"{REGISTRY}/"):
            record(shape, path, value, "a registry off gsoci")
        return
    if value.startswith("oci://"):
        if not is_gsoci(value):
            record(shape, path, value, "an oci:// reference off gsoci")
        return
    for host in FOREIGN:
        if re.search(rf"(^|[^A-Za-z0-9.-]){re.escape(host)}/", value):
            record(shape, path, value, f"a reference on {host}")
            return
    if (key == "image" or parent == "images") and IMAGE_REFERENCE.fullmatch(value) and not is_gsoci(value):
        host = host_of(value)
        record(shape, path, value, f"an image reference on {host}" if host
               else "a bare name — Docker Hub on containerd, refused by CRI-O's short-name mode")


def scan(shape: str, docs: list[dict]) -> int:
    for doc in docs:
        kind = doc.get("kind", "")
        name = doc.get("metadata", {}).get("name", "")
        walk(doc, shape, kind, f"{kind}/{name}")
    return len(docs)


def every_component_on(chart: str) -> list[str]:
    """--set components.<name>.enabled=true for every component the chart's values switch off."""
    with open(os.path.join(chart, "values.yaml"), encoding="utf-8") as f:
        values = yaml.safe_load(f)
    flags: list[str] = []
    for name, component in values.get("components", {}).items():
        if isinstance(component, dict) and component.get("enabled") is False:
            flags += ["--set", f"components.{name}.enabled=true"]
    return flags


def main() -> None:
    if len(sys.argv) != 3:
        fail(__doc__.strip().splitlines()[-1])
    meta, connectivity = sys.argv[1], sys.argv[2]
    shapes = [
        ("meta: defaults", meta, []),
        ("meta: fleet APIs, the engine and every component on", meta, [*FLEET_APIS, *INPUTS, *every_component_on(meta)]),
        ("connectivity: defaults", connectivity, ["--set", "ingress.parentRefs[0].name=x"]),
        # agentgateway on is the agentgateway-muster ingress mode with the MCPs behind muster.
        ("connectivity: fleet APIs and every component on", connectivity,
         [*FLEET_APIS, *INPUTS, "--set", "ingress.parentRefs[0].name=x", "--set", "ingress.mode=agentgateway-muster",
          "--set", "agent-platform-mcps.agentgateway.viaMuster=true", *every_component_on(connectivity)]),
    ]
    objects = 0
    for shape, chart, args in shapes:
        objects += scan(shape, render(chart, args))
    if not objects:
        fail("no object rendered")
    for shape, path, value, reason in pending:
        print(f"pending: [{shape}] {path}: {value} — until {reason}")
    stale = [reason for pattern, reason in PENDING.items() if pattern not in matched_pending]
    if findings:
        for shape, path, value in findings:
            print(f"FAIL: [{shape}] {path}: {value}", file=sys.stderr)
        fail(f"{len(findings)} image reference(s) outside {REGISTRY} in the rendered defaults (giantswarm/agent-platform#575)")
    if stale:
        fail("stale PENDING entries — the reference is gone, remove them: " + "; ".join(stale))
    print(f"ok: every image reference in {objects} rendered objects of {len(shapes)} shapes is on {REGISTRY}"
          + (f" — {len(pending)} pending reference(s) tolerated until their copies are published" if pending else ""))


if __name__ == "__main__":
    main()
