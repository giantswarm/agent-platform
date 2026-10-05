#!/usr/bin/env python3
"""Assert the platform Harness is the kagent chart's since 4.8.0 (giantswarm/agent-platform#406)
and that the meta chart forwards its policy alone.

On kagent API v2 how an agent runs is a Harness (giantswarm/agent-platform#344,
bumblebee-plans#51 D5): a digest-pinned Go ADK runtime image, the environment
that makes the caller's token reach muster (KAGENT_PROPAGATE_TOKEN), and the
Substrate policy (a WorkerPool, a snapshot location). An Agent names its Harness
by spec.harnessRef; the Harness has no admission selector. This test asserts:
  - the connectivity chart renders NO Harness and reads no kagent.harness key;
  - the meta chart forwards kagent.harness with create: true, the snapshot
    location, the env and the compaction policy — no image, no workerPoolRef
    (the chart's defaults: its stamped digest, the WorkerPool
    substrateWorkerPool.name) and no allowedAgentTemplates;
  - the env follows the kagent OTel exporters the way the controller's tenant
    header does: KAGENT_PROPAGATE_TOKEN always; with an exporter on,
    OTEL_EXPORTER_OTLP_HEADERS (the tenant). Both exporters resolve off without
    the monitoring API (auto) or explicitly. The controller compiles every other
    telemetry setting into the actors from kagent.otel, whose export timeout
    (500 ms) is the cap of the Go ADK's flush before a turn's response
    (giantswarm/agent-platform#456);
  - the kagent release's values carry no null anywhere (a null is removed, not
    stored, by the merge patch of a HelmRelease that already exists, #418);
  - an override kagent.harness.image (a dev loop's locally built image, by
    digest) reaches the kagent release's harness.image verbatim;
  - kagent.substrateWorkerPool.name follows an override (the Harness's
    workerPoolRef defaults to it in the chart);
  - kagent off forwards nothing.

The rendered object itself is the kagent chart's (verify-components-charts
renders the chart the range resolves to with these values and asserts the
Harness it emits carries no selector); structural validation against the
Harness CRD is verify-kagent-crds.

Usage: verify-kagent-harness.py <connectivity chart dir> <meta chart dir>
"""
import pathlib
import re
import subprocess
import sys

import yaml

DIGEST = "localhost:5001/golang-adk@sha256:" + "a" * 64
SNAPSHOT = "s3://bucket/agents"
CONN_BASE = ["--set", "ingress.parentRefs[0].name=x", "--set", "components.kagent.enabled=true"]
MONITORING_API = ["--api-versions", "monitoring.coreos.com/v1"]  # the observability platform: the auto knobs resolve on
ENV_PROPAGATE = {"name": "KAGENT_PROPAGATE_TOKEN", "value": "true"}
ENV_HEADERS = {"name": "OTEL_EXPORTER_OTLP_HEADERS", "value": "X-Scope-OrgID=giantswarm"}
EXPORT_TIMEOUT = "500"  # kagent.otel.exporter.otlp.timeout, milliseconds (#456)


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
    # No --api-versions: the observability knobs resolve off (auto), so the env
    # carries no exporter entry — the shape of a lab or a cluster without the
    # observability platform.
    expected = {
        "create": True,
        "snapshotLocation": SNAPSHOT,
        "env": [ENV_PROPAGATE],
        "compaction": {"tokenThreshold": 600000, "eventRetentionSize": 4},
    }
    if harness != expected:
        fail(f"the forwarded kagent.harness is not the GS policy alone:\n  got      {harness}\n  expected {expected}\n"
             "(no image by default — the chart's stamped digest is the Harness image; no workerPoolRef — the chart defaults it to "
             "substrateWorkerPool.name; no allowedAgentTemplates — an Agent names its Harness by spec.harnessRef; with both "
             "OTel exporters resolved off the env is KAGENT_PROPAGATE_TOKEN alone)")
    print("ok: the meta chart forwards the platform Harness policy — create, the snapshot location, KAGENT_PROPAGATE_TOKEN "
          "(exporters off), the compaction; no image, no workerPoolRef, no selector")

    # The actors' tenant header follows the exporters; the signals reach the
    # kagent chart resolved, in its SDK-spec shape.
    for label, args, want, signals in (
        ("both exporters on (auto, monitoring API served)", [*MONITORING_API], [ENV_PROPAGATE, ENV_HEADERS], (True, True)),
        ("traces on, logs off", [*MONITORING_API, "--set", "kagent.otel.logs.enabled=false"], [ENV_PROPAGATE, ENV_HEADERS], (True, False)),
        ("logs on alone (explicit, no monitoring API)", ["--set", "kagent.otel.logs.enabled=true"], [ENV_PROPAGATE, ENV_HEADERS], (False, True)),
        ("both off explicitly with the monitoring API served", [*MONITORING_API, "--set", "kagent.otel.traces.enabled=false", "--set", "kagent.otel.logs.enabled=false"], [ENV_PROPAGATE], (False, False)),
    ):
        values = kagent_values(render(meta, [*base, *args]))
        got = values["harness"]["env"]
        if got != want:
            fail(f"Harness env with {label}:\n  got      {got}\n  expected {want}\n(OTEL_EXPORTER_OTLP_HEADERS travels with "
                 "either exporter)")
        otel = values.get("otel", {})
        resolved = (otel.get("traces", {}).get("enabled"), otel.get("logs", {}).get("enabled"))
        if resolved != signals:
            fail(f"kagent.otel traces/logs enabled with {label}: got {resolved}, expected {signals} (booleans: the kagent "
                 "chart renders OTEL_<SIGNAL>_EXPORTER from them with ternary, which takes any non-empty string as true)")
        timeout = otel.get("exporter", {}).get("otlp", {}).get("timeout")
        if timeout != EXPORT_TIMEOUT:
            fail(f"kagent.otel.exporter.otlp.timeout with {label}: got {timeout!r}, expected {EXPORT_TIMEOUT!r} (the flush cap — #456)")
    ctrl_env = kagent_values(render(meta, [*base, *MONITORING_API]))["controller"]["env"]
    if ENV_HEADERS not in ctrl_env:
        fail(f"the controller's tenant header is gone with the exporters on: {ctrl_env}")
    ctrl_env = kagent_values(render(meta, base))["controller"]["env"]
    if ENV_HEADERS in ctrl_env:
        fail(f"the controller's tenant header stays with both exporters off: {ctrl_env}")
    print("ok: the Harness env follows the exporters — the tenant header with either; kagent.otel reaches the chart with "
          f"resolved booleans and the {EXPORT_TIMEOUT} ms export timeout; the controller's header as before")

    # A null is removed, not stored, by the merge patch of a kagent HelmRelease
    # that already exists (#418), so whatever it was meant to delete comes back.
    if paths := nulls(values):
        fail(f"the kagent release's values carry a null at {', '.join(paths)}; a null is removed — not stored — by the merge patch "
             "of a HelmRelease that already exists, so whatever it was meant to delete comes back on that installation (#418)")
    print("ok: the kagent release's values carry no null")

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
