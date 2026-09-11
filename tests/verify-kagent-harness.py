#!/usr/bin/env python3
"""Assert the connectivity chart renders exactly ONE platform Harness per managed
namespace with the shape the platform owns, and that its guards fire.

On kagent API v2 how an agent runs is a Harness (giantswarm/agent-platform#344,
bumblebee-plans#51 D5): a digest-pinned Go ADK runtime image, the environment
that makes the caller's token reach muster (KAGENT_PROPAGATE_TOKEN), and the
Substrate policy (a WorkerPool, a snapshot location). An AgentTemplate becomes
Ready only when a Harness admits it (allowedAgentTemplates.selector); the Generic
agent chart 1.x labels every template agent-platform.giantswarm.io/harness:
kagent, and that one label is the whole admission contract.

4.0 renders one `kagent` Harness, kagent type only — no free list of arbitrary
Harness types. This test renders the chart in a kagent-on shape and asserts:
  - exactly one Harness, named `kagent`, in the kagent namespace;
  - spec.kagent is the only runtime block (not codex/claude/byo);
  - workload.image is the configured digest; env carries only
    KAGENT_PROPAGATE_TOKEN=true;
  - substrate.workerPoolRef.name resolves to kagent.substrateWorkerPool.name (and
    follows an override); snapshotPolicy.location is kagent.harness.snapshotLocation;
  - allowedAgentTemplates.selector.matchLabels is exactly
    {agent-platform.giantswarm.io/harness: kagent};
  - a Go ADK image given as a TAG fails the render naming the digest requirement;
  - kagent off renders no Harness.

Structural validation of the object against the Harness CRD is verify-kagent-crds.

Usage: verify-kagent-harness.py <connectivity chart dir>
"""
import subprocess
import sys

import yaml

HARNESS_LABEL = "agent-platform.giantswarm.io/harness"
HARNESS_NAME = "kagent"
DIGEST = "ghcr.io/giantswarm/kagent/golang-adk@sha256:" + "a" * 64
BASE = ["--set", "ingress.parentRefs[0].name=x", "--set", "components.kagent.enabled=true"]


def fail(msg: str) -> None:
    sys.exit(f"FAIL: {msg}")


def render(chart: str, args: list[str]) -> list[dict]:
    r = subprocess.run(["helm", "template", "t", chart, *args], capture_output=True, text=True)
    if r.returncode != 0:
        fail(f"helm template failed unexpectedly:\n{r.stderr}")
    return [d for d in yaml.safe_load_all(r.stdout) if isinstance(d, dict)]


def render_expect_fail(chart: str, args: list[str]) -> str:
    r = subprocess.run(["helm", "template", "t", chart, *args], capture_output=True, text=True)
    if r.returncode == 0:
        fail(f"render was expected to fail but succeeded: helm template {' '.join(args)}")
    return r.stderr


def harnesses(docs: list[dict]) -> list[dict]:
    return [d for d in docs if d.get("kind") == "Harness"]


def main(chart: str) -> int:
    # A default kagent-on render: exactly one Harness, the platform shape.
    docs = render(chart, [*BASE, "--set", f"kagent.harness.image={DIGEST}",
                          "--set", "kagent.harness.snapshotLocation=s3://bucket/agents"])
    hs = harnesses(docs)
    if len(hs) != 1:
        fail(f"expected exactly one Harness per managed namespace, got {len(hs)}")
    h = hs[0]
    spec = h.get("spec", {})

    if h["metadata"].get("name") != HARNESS_NAME:
        fail(f"Harness name is {h['metadata'].get('name')!r}, expected {HARNESS_NAME!r} (the label value = the Harness name)")
    if h["metadata"].get("namespace") != "kagent":
        fail(f"Harness namespace is {h['metadata'].get('namespace')!r}, expected the kagent namespace")

    runtimes = [r for r in ("kagent", "codex", "claude", "byo") if r in spec]
    if runtimes != ["kagent"]:
        fail(f"the platform Harness must carry the kagent runtime only, got {runtimes}")

    if spec.get("workload", {}).get("image") != DIGEST:
        fail(f"workload.image is {spec.get('workload', {}).get('image')!r}, expected the configured digest {DIGEST!r}")

    if spec.get("env") != [{"name": "KAGENT_PROPAGATE_TOKEN", "value": "true"}]:
        fail(f"env must be exactly [KAGENT_PROPAGATE_TOKEN=true] (the D8 skills credential is not wired), got {spec.get('env')}")

    if spec.get("substrate", {}).get("workerPoolRef", {}).get("name") != "kagent-default":
        fail(f"substrate.workerPoolRef.name is {spec.get('substrate', {}).get('workerPoolRef', {}).get('name')!r}, expected the default kagent-default")
    if spec.get("substrate", {}).get("snapshotPolicy", {}).get("location") != "s3://bucket/agents":
        fail(f"substrate.snapshotPolicy.location is {spec.get('substrate', {}).get('snapshotPolicy', {}).get('location')!r}, expected kagent.harness.snapshotLocation")

    selector = spec.get("allowedAgentTemplates", {}).get("selector", {}).get("matchLabels")
    if selector != {HARNESS_LABEL: HARNESS_NAME}:
        fail(f"allowedAgentTemplates.selector.matchLabels is {selector!r}, expected {{{HARNESS_LABEL!r}: {HARNESS_NAME!r}}}")
    print(f"ok: one platform Harness {HARNESS_NAME!r} in the kagent namespace — kagent runtime, digest image, KAGENT_PROPAGATE_TOKEN, workerPoolRef kagent-default, the selector label")

    # The workerPoolRef follows the WorkerPool the meta chart names.
    docs = render(chart, [*BASE, "--set", f"kagent.harness.image={DIGEST}",
                          "--set", "kagent.substrateWorkerPool.name=pool-b"])
    ref = harnesses(docs)[0]["spec"]["substrate"]["workerPoolRef"]["name"]
    if ref != "pool-b":
        fail(f"workerPoolRef.name did not follow kagent.substrateWorkerPool.name: got {ref!r}, expected pool-b")
    print("ok: workerPoolRef resolves to kagent.substrateWorkerPool.name (follows an override)")

    # A tag image fails the render, naming the digest requirement.
    err = render_expect_fail(chart, [*BASE, "--set", "kagent.harness.image=ghcr.io/giantswarm/kagent/golang-adk:v0.11.0-gs.2"])
    if "digest" not in err.lower():
        fail(f"a tag image failed the render but the message did not name the digest requirement:\n{err}")
    print("ok: a Go ADK image given as a tag fails the render naming the digest requirement")

    # kagent off: no Harness.
    docs = render(chart, ["--set", "ingress.parentRefs[0].name=x"])
    if harnesses(docs):
        fail("a Harness renders while kagent is off")
    print("ok: no Harness renders while components.kagent is off")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    sys.exit(main(sys.argv[1]))
