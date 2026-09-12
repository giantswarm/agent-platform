#!/usr/bin/env python3
"""Assert the platform Harness is the kagent chart's since 4.8.0 (giantswarm/agent-platform#406).

On kagent API v2 how an agent runs is a Harness (giantswarm/agent-platform#344,
bumblebee-plans#51 D5): a digest-pinned Go ADK runtime image, the environment
that makes the caller's token reach muster (KAGENT_PROPAGATE_TOKEN), and the
Substrate policy (a WorkerPool, a snapshot location). An AgentTemplate becomes
Ready only when a Harness admits it (allowedAgentTemplates.selector); the Generic
agent chart 1.x labels every template agent-platform.giantswarm.io/harness:
kagent, and that one label is the whole admission contract.

Through 4.7.19 the connectivity chart rendered that Harness from a digest the
meta chart pinned. Since 4.8.0 the kagent chart (0.11.0-gs.6+) renders it from
its own Go ADK image at the digest stamped into the chart at publish, and the
meta chart forwards only the GS policy. This test asserts:
  - the connectivity chart renders NO Harness and reads no kagent.harness key;
  - the meta chart forwards kagent.harness with create: true, the snapshot
    location, env exactly [KAGENT_PROPAGATE_TOKEN=true], the admission label
    and a null for the chart's own label key (kagent.dev/harness) — and no
    image, no workerPoolRef (the chart's defaults: its stamped digest, the
    WorkerPool substrateWorkerPool.name);
  - an override kagent.harness.image (a dev loop's locally built image, by
    digest) reaches the kagent release's harness.image verbatim;
  - kagent.substrateWorkerPool.name follows an override (the Harness's
    workerPoolRef defaults to it in the chart);
  - kagent off forwards nothing.

The rendered object itself is the kagent chart's (helm unittest there);
structural validation against the Harness CRD is verify-kagent-crds.

Usage: verify-kagent-harness.py <connectivity chart dir> <meta chart dir>
"""
import pathlib
import re
import subprocess
import sys

import yaml

HARNESS_LABEL = "agent-platform.giantswarm.io/harness"
CHART_LABEL = "kagent.dev/harness"  # the kagent chart's own default selector key, deleted by a null
DIGEST = "localhost:5001/golang-adk@sha256:" + "a" * 64
SNAPSHOT = "s3://bucket/agents"
CONN_BASE = ["--set", "ingress.parentRefs[0].name=x", "--set", "components.kagent.enabled=true"]


def fail(msg: str) -> None:
    sys.exit(f"FAIL: {msg}")


def render(chart: str, args: list[str]) -> list[dict]:
    r = subprocess.run(["helm", "template", "t", chart, *args], capture_output=True, text=True)
    if r.returncode != 0:
        fail(f"helm template failed unexpectedly:\n{r.stderr}")
    return [d for d in yaml.safe_load_all(r.stdout) if isinstance(d, dict)]


def kagent_values(docs: list[dict]) -> dict | None:
    for d in docs:
        if d.get("kind") == "HelmRelease" and d["metadata"]["name"] == "kagent":
            return d["spec"]["values"]
    return None


def main(connectivity: str, meta: str) -> int:
    if [d for d in render(connectivity, CONN_BASE) if d.get("kind") == "Harness"]:
        fail("the connectivity chart still renders a Harness; since 4.8.0 the kagent chart renders the platform Harness (kagent.harness.create)")
    reads = [str(p) for p in pathlib.Path(connectivity, "templates").rglob("*") if p.is_file() and re.search(r"\.Values\.kagent\.harness\b", p.read_text())]
    if reads:
        fail(f"the connectivity chart reads kagent.harness ({', '.join(reads)}); the key is the kagent chart's now")
    print("ok: the connectivity chart renders no Harness and reads no kagent.harness key")

    base = ["-f", f"{meta}/ci/ci-values.yaml", *CONN_BASE, "--set", f"kagent.harness.snapshotLocation={SNAPSHOT}"]
    harness = kagent_values(render(meta, base)).get("harness")
    if not harness:
        fail("the meta chart forwards no kagent.harness block to the kagent release")
    expected = {
        "create": True,
        "snapshotLocation": SNAPSHOT,
        "env": [{"name": "KAGENT_PROPAGATE_TOKEN", "value": "true"}],
        "allowedAgentTemplates": {"selector": {"matchLabels": {HARNESS_LABEL: "kagent", CHART_LABEL: None}}},
    }
    if harness != expected:
        fail(f"the forwarded kagent.harness is not the GS policy alone:\n  got      {harness}\n  expected {expected}\n"
             "(no image by default — the chart's stamped digest is the Harness image; no workerPoolRef — the chart defaults it to "
             f"substrateWorkerPool.name; the null deletes the chart's own selector key {CHART_LABEL} on coalesce)")
    print(f"ok: the meta chart forwards the platform Harness policy — create, the snapshot location, KAGENT_PROPAGATE_TOKEN, "
          f"{HARNESS_LABEL}: kagent with {CHART_LABEL} nulled; no image, no workerPoolRef")

    values = kagent_values(render(meta, [*base, "--set", f"kagent.harness.image={DIGEST}", "--set", "kagent.substrateWorkerPool.name=pool-b"]))
    if values["harness"].get("image") != DIGEST:
        fail(f"a set kagent.harness.image does not reach the kagent release's harness.image (got {values['harness'].get('image')!r})")
    if values["substrateWorkerPool"].get("name") != "pool-b":
        fail("kagent.substrateWorkerPool.name does not follow an override (the Harness's workerPoolRef defaults to it in the chart)")
    print("ok: an override kagent.harness.image reaches the kagent release verbatim; the WorkerPool name follows an override")

    if kagent_values(render(meta, ["-f", f"{meta}/ci/ci-values.yaml", "--set", "ingress.parentRefs[0].name=x", "--set", "components.kagent.enabled=false"])) is not None:
        fail("a kagent release renders while components.kagent is off")
    print("ok: no kagent release (and so no Harness) while components.kagent is off")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    sys.exit(main(sys.argv[1], sys.argv[2]))
