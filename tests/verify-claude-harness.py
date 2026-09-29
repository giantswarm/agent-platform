#!/usr/bin/env python3
"""Assert the Claude Harness (kagent.claudeHarness) is the kagent chart's and the meta
chart forwards its policy the way it forwards the platform Harness (kagent.harness).

The kagent line renders a second Harness, `claude`, from its claudeHarness.* keys:
the `claude` runtime adapter (Harness.spec.claude) on the chart's claude-harness
image at the digest stamped as runtimeImages.claudeHarness. This test asserts:
  - the connectivity chart renders no Harness at all and reads nothing of
    kagent.claudeHarness but create and modelConfig;
  - off by default: kagent.claudeHarness reaches the kagent release with
    create: false, no image (the chart's stamp applies), no derived location and
    no modelConfig (the connectivity chart's key); the connectivity chart
    renders no claude-code ModelConfig;
  - on: snapshotLocation derives as <the platform Harness's location>/claude,
    from kagent.harness.snapshotLocation or the store block's rendered bucket;
    a set value stands (the platform location itself is required while kagent
    is on, the meta chart's own guard); image travels only when set; egress,
    callerRoutes, env, propagateToken and projectInstructions reach the
    release verbatim;
  - on: the connectivity chart renders the claude-code ModelConfig at
    api.kagent.dev/v1alpha3 (Anthropic, promptCaching: false, no cacheTTL),
    and none with modelConfig.create off.

The rendered Harness itself is the kagent chart's; the ModelConfig's CRD
validation is verify-kagent-crds.

Usage: verify-claude-harness.py <connectivity chart dir> <meta chart dir>
"""

import json
import pathlib
import re
import subprocess
import sys

import yaml

DIGEST = "localhost:5001/claude-harness@sha256:" + "b" * 64
SNAPSHOT = "s3://bucket/agents"
ROUTES = {"github.com": "http://agentgateway.agent-platform.svc:8080/git/github.com/"}
EGRESS = ["github.com", "*.githubusercontent.com"]
EXTRA_ENV = [{"name": "EXTRA", "value": "1"}]
CONN_BASE = ["--set", "ingress.parentRefs[0].name=x", "--set", "components.kagent.enabled=true"]
ON = ["--set", "kagent.claudeHarness.create=true"]


def fail(msg: str) -> None:
    sys.exit(f"FAIL: {msg}")


def render(chart: str, args: list[str]) -> list[dict]:
    r = subprocess.run(["helm", "template", "t", chart, *args], capture_output=True, text=True)
    if r.returncode != 0:
        fail(f"helm template failed unexpectedly:\n{r.stderr}")
    return [d for d in yaml.safe_load_all(r.stdout) if isinstance(d, dict)]


def kagent_values(docs: list[dict]) -> dict:
    for d in docs:
        if d.get("kind") == "HelmRelease" and d["metadata"]["name"] == "kagent":
            return d["spec"]["values"]
    fail("no kagent HelmRelease in the meta render")


def model_configs(docs: list[dict], name: str) -> list[dict]:
    return [d for d in docs if d.get("kind") == "ModelConfig" and d["metadata"]["name"] == name]


def main(connectivity: str, meta: str) -> int:
    reads = set()
    for p in pathlib.Path(connectivity, "templates").rglob("*"):
        if p.is_file():
            reads |= set(re.findall(r"\$claude\.([A-Za-z]+)|\"claudeHarness\" \"([A-Za-z]+)\"", p.read_text()))
    reads = {a or b for a, b in reads}
    if reads - {"create", "modelConfig"}:
        fail(f"the connectivity chart reads kagent.claudeHarness.{', '.join(sorted(reads - {'create', 'modelConfig'}))}; "
             "the Harness is the kagent chart's, this chart renders its ModelConfig alone")
    docs = render(connectivity, [*CONN_BASE, *ON])
    if [d for d in docs if d.get("kind") == "Harness"]:
        fail("the connectivity chart renders a Harness; the Claude Harness is the kagent chart's (kagent.claudeHarness.create)")
    print("ok: the connectivity chart renders no Harness and reads kagent.claudeHarness.create and .modelConfig alone")

    if model_configs(render(connectivity, CONN_BASE), "claude-code"):
        fail("the claude-code ModelConfig renders with kagent.claudeHarness.create off")
    mc = model_configs(docs, "claude-code")
    if len(mc) != 1:
        fail(f"expected one claude-code ModelConfig with the Claude Harness on, got {len(mc)}")
    spec = mc[0]["spec"]
    if mc[0]["apiVersion"] != "api.kagent.dev/v1alpha3" or spec.get("provider") != "Anthropic":
        fail(f"the claude-code ModelConfig is not an Anthropic ModelConfig at api.kagent.dev/v1alpha3: {mc[0]}")
    if spec.get("anthropic", {}).get("promptCaching") is not False or "cacheTTL" in spec.get("anthropic", {}):
        fail(f"the claude-code ModelConfig does not turn prompt caching off without a TTL: {spec.get('anthropic')}")
    if model_configs(render(connectivity, [*CONN_BASE, *ON, "--set", "kagent.claudeHarness.modelConfig.create=false"]), "claude-code"):
        fail("the claude-code ModelConfig renders with kagent.claudeHarness.modelConfig.create off")
    print("ok: the claude-code ModelConfig renders with the Harness on (Anthropic, prompt caching off, no TTL), not off")

    base = ["-f", f"{meta}/ci/ci-values.yaml", *CONN_BASE, "--set", f"kagent.harness.snapshotLocation={SNAPSHOT}"]
    claude = kagent_values(render(meta, base)).get("claudeHarness")
    expected_off = {"create": False, "name": "claude", "snapshotLocation": "", "egress": [], "propagateToken": True,
                    "projectInstructions": True, "callerRoutes": {}, "env": []}
    if claude != expected_off:
        fail(f"the forwarded kagent.claudeHarness is not the policy alone, off by default:\n  got      {claude}\n  expected {expected_off}\n"
             "(no image — the chart's stamped digest is the Harness image; no modelConfig — the connectivity chart's key; "
             "no derived location while off)")
    print("ok: kagent.claudeHarness reaches the kagent release off by default, without image, modelConfig or a derived location")

    on = [*base, *ON, "--set-json", f"kagent.claudeHarness.callerRoutes={json.dumps(ROUTES)}",
          "--set-json", f"kagent.claudeHarness.egress={json.dumps(EGRESS)}", "--set-json", f"kagent.claudeHarness.env={json.dumps(EXTRA_ENV)}"]
    claude = kagent_values(render(meta, on))["claudeHarness"]
    expected_on = {**expected_off, "create": True, "snapshotLocation": f"{SNAPSHOT}/claude", "egress": EGRESS, "callerRoutes": ROUTES, "env": EXTRA_ENV}
    if claude != expected_on:
        fail(f"the forwarded kagent.claudeHarness on is not the policy with the derived location:\n  got      {claude}\n  expected {expected_on}")
    print(f"ok: on, the location derives as {SNAPSHOT}/claude; egress, callerRoutes and env reach the release verbatim; no image")

    claude = kagent_values(render(meta, [*base, *ON, "--set", "kagent.claudeHarness.snapshotLocation=s3://other/claude-agents",
                                          "--set", f"kagent.claudeHarness.image={DIGEST}"]))["claudeHarness"]
    if claude.get("snapshotLocation") != "s3://other/claude-agents":
        fail(f"a set kagent.claudeHarness.snapshotLocation does not stand over the derived one (got {claude.get('snapshotLocation')!r})")
    if claude.get("image") != DIGEST:
        fail(f"a set kagent.claudeHarness.image does not reach the kagent release verbatim (got {claude.get('image')!r})")
    print("ok: a set snapshotLocation stands; a set image travels")

    store = ["-f", f"{meta}/ci/ci-values.yaml", *CONN_BASE, *ON, "--set", "kagent.harness.snapshotLocation=",
             "--set", "kagent.harness.snapshotStore.crossplane.enabled=true", "--set", "kagent.harness.snapshotStore.crossplane.providerConfigRef=ci",
             "--set", "kagent.harness.snapshotStore.crossplane.region=eu-central-1", "--set", "kagent.harness.snapshotStore.crossplane.aws.bucketName=giantswarm-ci-substrate",
             "--set", "kagent.harness.snapshotStore.crossplane.aws.oidcProvider=irsa.ci.example.com", "--set-string", "kagent.harness.snapshotStore.crossplane.aws.accountId=123456789012"]
    values = kagent_values(render(meta, store))
    platform = values["harness"]["snapshotLocation"]
    if values["claudeHarness"].get("snapshotLocation") != f"{platform}/claude":
        fail(f"with the store block rendering the bucket ({platform}) the Claude Harness's location is {values['claudeHarness'].get('snapshotLocation')!r}, not its `claude` prefix")
    print(f"ok: with kagent.harness.snapshotStore the location derives from the rendered bucket ({platform}/claude)")

    return 0


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    sys.exit(main(sys.argv[1], sys.argv[2]))
