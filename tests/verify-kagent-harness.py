#!/usr/bin/env python3
"""Assert the platform Harness is the kagent chart's since 4.8.0 (giantswarm/agent-platform#406)
and that its admission selector survives the way a HelmRelease reaches the cluster (#418).

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
meta chart forwards only the GS policy. The chart's own default selector key
(kagent.dev/harness: kagent) has to go, and Helm merges maps on coalesce: through
4.9.3 the meta chart forwarded a null for it, which deletes the key on coalesce —
but a kagent HelmRelease that pre-existed the cut-over is PATCHED, not created,
and a JSON merge patch removes a null key instead of storing it, so the default
came back and the live Harness admitted no template (graveler, 2026-09-13,
giantswarm/agent-platform#418). Since 0.11.0-gs.9 the line's Harness template
drops every selector label whose value is the empty string, and the meta chart
forwards "" — a value every path stores. This test asserts:
  - the connectivity chart renders NO Harness and reads no kagent.harness key;
  - the meta chart forwards kagent.harness with create: true, the snapshot
    location, env exactly [KAGENT_PROPAGATE_TOKEN=true], the admission label
    and the chart's own label key (kagent.dev/harness) blanked — and no image,
    no workerPoolRef (the chart's defaults: its stamped digest, the WorkerPool
    substrateWorkerPool.name);
  - the live-shaped path: the kagent release's values carry no null anywhere,
    and replayed as a merge patch onto a release that never held the key, then
    coalesced with the chart's default and stripped of empty values the way the
    line's template does, the selector is the platform label alone;
  - an override kagent.harness.image (a dev loop's locally built image, by
    digest) reaches the kagent release's harness.image verbatim;
  - kagent.substrateWorkerPool.name follows an override (the Harness's
    workerPoolRef defaults to it in the chart);
  - kagent off forwards nothing.

The rendered object itself is the kagent chart's (helm unittest there; and
verify-components-charts renders the chart the range resolves to with these
values and asserts the Harness it emits selects by the platform label alone);
structural validation against the Harness CRD is verify-kagent-crds.

Usage: verify-kagent-harness.py <connectivity chart dir> <meta chart dir>
"""
import pathlib
import re
import subprocess
import sys

import yaml

HARNESS_LABEL = "agent-platform.giantswarm.io/harness"
CHART_LABEL = "kagent.dev/harness"  # the kagent chart's own default selector key, forwarded empty so the line's template drops it
CHART_DEFAULT_SELECTOR = {CHART_LABEL: "kagent"}  # helm/kagent/values.yaml harness.allowedAgentTemplates.selector.matchLabels
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


def nulls(value, path: str = "") -> list[str]:
    """Every null in a values tree, by dotted path."""
    if value is None:
        return [path or "<root>"]
    if isinstance(value, dict):
        return [p for k, v in value.items() for p in nulls(v, f"{path}.{k}" if path else str(k))]
    if isinstance(value, list):
        return [p for i, v in enumerate(value) for p in nulls(v, f"{path}[{i}]")]
    return []


def merge_patch(target, patch):
    """RFC 7386 — what the apiserver does to an existing HelmRelease's spec.values
    when the meta chart's upgrade patches it: a null REMOVES the key."""
    if not isinstance(patch, dict):
        return patch
    out = dict(target) if isinstance(target, dict) else {}
    for key, value in patch.items():
        if value is None:
            out.pop(key, None)
        else:
            out[key] = merge_patch(out.get(key), value)
    return out


def main(connectivity: str, meta: str) -> int:
    if [d for d in render(connectivity, CONN_BASE) if d.get("kind") == "Harness"]:
        fail("the connectivity chart still renders a Harness; since 4.8.0 the kagent chart renders the platform Harness (kagent.harness.create)")
    reads = [str(p) for p in pathlib.Path(connectivity, "templates").rglob("*") if p.is_file() and re.search(r"\.Values\.kagent\.harness\b", p.read_text())]
    if reads:
        fail(f"the connectivity chart reads kagent.harness ({', '.join(reads)}); the key is the kagent chart's now")
    print("ok: the connectivity chart renders no Harness and reads no kagent.harness key")

    base = ["-f", f"{meta}/ci/ci-values.yaml", *CONN_BASE, "--set", f"kagent.harness.snapshotLocation={SNAPSHOT}"]
    values = kagent_values(render(meta, base))
    harness = values.get("harness")
    if not harness:
        fail("the meta chart forwards no kagent.harness block to the kagent release")
    expected = {
        "create": True,
        "snapshotLocation": SNAPSHOT,
        "env": [{"name": "KAGENT_PROPAGATE_TOKEN", "value": "true"}],
        "allowedAgentTemplates": {"selector": {"matchLabels": {HARNESS_LABEL: "kagent", CHART_LABEL: ""}}},
    }
    if harness != expected:
        fail(f"the forwarded kagent.harness is not the GS policy alone:\n  got      {harness}\n  expected {expected}\n"
             "(no image by default — the chart's stamped digest is the Harness image; no workerPoolRef — the chart defaults it to "
             f"substrateWorkerPool.name; {CHART_LABEL} is forwarded EMPTY, never null: the line's Harness template drops an "
             "empty-valued selector label, while a null is lost on the patch of a pre-existing HelmRelease — #418)")
    print(f"ok: the meta chart forwards the platform Harness policy — create, the snapshot location, KAGENT_PROPAGATE_TOKEN, "
          f"{HARNESS_LABEL}: kagent with {CHART_LABEL} blanked; no image, no workerPoolRef")

    # The live-shaped path (#418): an installation upgraded from 3.x has a kagent
    # HelmRelease already, and the meta chart's upgrade patches its spec.values.
    if paths := nulls(values):
        fail(f"the kagent release's values carry a null at {', '.join(paths)}; a null is removed — not stored — by the merge patch "
             "of a HelmRelease that already exists, so whatever it was meant to delete comes back on that installation (#418)")
    live = merge_patch({"registry": "ghcr.io"}, values)  # a 3.x release: no harness block at all
    forwarded = live["harness"]["allowedAgentTemplates"]["selector"]["matchLabels"]
    coalesced = {**CHART_DEFAULT_SELECTOR, **forwarded}  # Helm coalesce: the release's values win, key by key
    effective = {k: v for k, v in coalesced.items() if v != ""}  # the line's template drops an empty value (0.11.0-gs.9+)
    if effective != {HARNESS_LABEL: "kagent"}:
        fail(f"replayed as a patch onto a pre-existing kagent release and coalesced with the chart's default, the Harness selects by "
             f"{effective}; the admission contract is {HARNESS_LABEL}=kagent alone (#418)")
    print(f"ok: the kagent release's values carry no null; patched onto a pre-existing release and coalesced with the chart's "
          f"default, the Harness selector is {HARNESS_LABEL}=kagent alone")

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
