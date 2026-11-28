#!/usr/bin/env python3
"""Assert that the agent chart line moves to 3.x only with what makes it safe
.

Agent chart 3.0 removes muster.requireApproval; agent-manager's migrate
rewrites it to `default: Ask` from agent-platform.agentManager.agentChartV3Floor,
and muster enforces `default: Ask` from agent-platform.muster.agentApprovalFloor.
agent-manager.agentChart.semver feeds agent-manager, its migrate run and the
agent-platform-operator (every organization's chart source), so:

- the default renders on the 2.x line;
- a 3.x line with agent-manager and muster at their floors and the default
  agent's range on 3.x renders, and the range reaches agent-manager's release,
  the connectivity release (the migrate run's input) and the default agent's
  source;
- a 3.x line is refused with agent-manager below its floor, with muster below
  its floor, with agent-manager.muster.toolApproval.enabled false, with the
  default agent's range on the other major, and a range spanning both majors
  is refused; the default agent on 3.x with the line on 2.x is refused too;
- the placeholder floors (agent approval, the hop, governance, session
  bindings) are unset or above the last release without the feature; while
  the hop's or muster's floor is unset every 3.x range is refused, whatever
  its form (~3.2, 3.1.x, bounds), a pre-release alone and >=3.0.0 (spans
  majors, as migrate says) too; the gated paths run on a copy of the chart
  with test floors;
- a floor that needs the governance wiring (agent approval, the hop, tool
  approval, session bindings) set while the governance floor is unset fails
  the render naming both, 3.x line or not;
- the bounds that admit a pre-release of the next major: the probes serve
  Helm 4 and the textual read of the upper bounds Helm 3.17 (whose semver
  never matches a pre-release against a range with no pre-release lower
  bound); HELM_LEGACY, the Helm 3.17 binary, runs the bound cases once more
  under it (`make` passes the one in .bin/ when it is there).

Stdlib only: documents are matched by text.
Usage: verify-agent-chart-line.py <meta chart dir>
Environment: HELM selects the binary.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile

META = META_ORIG = sys.argv[1]
CONN = sys.argv[2] if len(sys.argv) > 2 else ""
HELM = os.environ.get("HELM") or "helm"
BASE = [
    "--namespace", "agent-platform",
    "--set", "global.domain=example.com",
    "--set", "global.identity.issuerUrl=https://dex.example.com",
    "--set", "global.identity.clientId=agent-platform",
    "--set", "global.identity.existingSecret=agent-platform-idp",
    "--set", "global.gatewayApi.parentRefs[0].name=example-gateway",
    "--set", "model-manager.ollama.endpoint=http://ollama.example.com:11434",
    "--set", "kagent.harness.snapshotLocation=s3://ci-agent-snapshots/agents",
    "--set", "kagent.database.postgres.urlFile=/etc/kagent-db/uri",
    "--set", "postgres.enabled=true",
    "--set", "components.kagent.enabled=true",
    "--set", "components.agent-manager.enabled=true",
]
V3 = ">=3.0.0 <4.0.0"
LINE = ["--set", f"agent-manager.agentChart.semver={V3}"]
AM = ["--set", "components.agent-manager.versionRange=>={floor}-rc.1 <4.0.0-0"]
MUSTER = ["--set", "components.muster.versionRange=>={floor} <6.0.0"]
DEFAULT_AGENT = ["--set", f"components.default-agent.versionRange={V3}"]

failures: list[str] = []


def fail(msg: str) -> None:
    failures.append(msg)


def run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([HELM, "template", "ap", META, *BASE, *args], capture_output=True, text=True)


def renders(what: str, *args: str) -> str:
    r = run(*args)
    if r.returncode != 0:
        fail(f"{what}: render failed:\n{r.stderr}")
        return ""
    return r.stdout


def refused(what: str, needle: str, *args: str) -> None:
    r = run(*args)
    if r.returncode == 0 or needle not in r.stderr:
        fail(f"{what}: not refused with {needle!r}:\n{r.stderr[-600:]}")


# The releases that do not carry what the 3.x line needs: a floor at or below
# them would let a 3.x line through against a muster that ignores default: Ask
# or an agent-manager whose migrate has no 2.x -> 3.x hop.
LAST_WITHOUT = {"muster.agentApprovalFloor": (5, 67, 0), "agentManager.agentChartV3Floor": (3, 5, 8),
                "agentManager.toolApprovalFloor": (3, 5, 8), "muster.governanceFloor": (5, 67, 0),
                "muster.sessionBindingsPortFloor": (5, 67, 0)}
PLACEHOLDERS = list(LAST_WITHOUT)
# Test values for the floors in a copy of the chart, so the paths they gate
# are exercised while the chart itself keeps them unset.
TEST_FLOORS = {"muster.agentApprovalFloor": "5.68.0", "agentManager.agentChartV3Floor": "3.6.0",
               "agentManager.toolApprovalFloor": "3.6.0", "muster.governanceFloor": "5.68.0",
               "muster.sessionBindingsPortFloor": "5.68.0"}
BYPASS = ["~3.2", "3.1.x", ">=3.1.0 <3.6.0", "3.0.0-rc.6", ">=3.0.0", "3.x", V3]


def floor_value(chart: str, name: str) -> str | None:
    text = open(os.path.join(chart, "templates", "_helpers.tpl"), encoding="utf-8").read()
    m = re.search(r'\{\{- define "agent-platform\.' + re.escape(name) + r'" -\}\}([^{]*)\{\{- end -\}\}', text)
    if not m:
        fail(f"agent-platform.{name}: no one-line define")
        return None
    return m.group(1)


def with_floors(dst: str, floors: dict[str, str] | None = None) -> str:
    chart = os.path.join(dst, "agent-platform")
    shutil.copytree(META_ORIG, chart)
    path = os.path.join(chart, "templates", "_helpers.tpl")
    text = open(path, encoding="utf-8").read()
    for name, v in (TEST_FLOORS if floors is None else floors).items():
        text, n = re.subn(r'(\{\{- define "agent-platform\.' + re.escape(name) + r'" -\}\})[^{]*(\{\{- end -\}\})', r"\g<1>" + v + r"\g<2>", text)
        if n != 1:
            fail(f"agent-platform.{name}: cannot set the test floor")
    open(path, "w", encoding="utf-8").write(text)
    return chart


# The chart's own floors: unset until the releases exist, else above the last
# release without the feature.
for name in PLACEHOLDERS:
    v = floor_value(META, name)
    if v is None or v == "":
        continue
    if not re.fullmatch(r"\d+\.\d+\.\d+", v):
        fail(f"agent-platform.{name} {v!r} is neither unset nor one X.Y.Z")
    elif tuple(map(int, v.split("."))) <= LAST_WITHOUT[name]:
        fail(f"agent-platform.{name} {v} admits a release without the feature (last without it: {'.'.join(map(str, LAST_WITHOUT[name]))})")
unset = not floor_value(META, "agentManager.agentChartV3Floor") or not floor_value(META, "muster.agentApprovalFloor")

out = renders("default")
if V3 in out:
    fail("the default render carries the 3.x line")
if "'>=2.6.0 <3.0.0'" not in out and '">=2.6.0 <3.0.0"' not in out:
    fail("the default render does not carry the 2.x line")

# The preflight range reaches the connectivity release and is not the line:
# no guard refuses it.
out = renders("preflight on the 2.x line", "--set", f"agentManager.migration.preflightSemver={V3}")
if f"preflightSemver: '{V3}'" not in out:
    fail("agentManager.migration.preflightSemver does not reach the connectivity release")

# Ranges the guard must read as migrate does, on the chart as it is.
for r in ["~3.2", "3.1.x", ">=3.1.0 <3.6.0", "3.x", V3]:
    if unset:
        refused(f"{r} while the floors are unset", "are unset; keep the 2.x line", "--set", f"agent-manager.agentChart.semver={r}")
    else:
        refused(f"{r} below the floors", "admits a release below", "--set", f"agent-manager.agentChart.semver={r}")
refused("a pre-release alone", "admits no release version", "--set", "agent-manager.agentChart.semver=3.0.0-rc.6")
refused(">=3.0.0 spans majors as for migrate", "admits agent chart majors 3, 4, 5, 6, 7, …", "--set", "agent-manager.agentChart.semver=>=3.0.0")
# QB-07: majors past the probes (99) are refused as unsupported, as
# agent-manager's migrate and the operator refuse them, never read short.
for r in [">=100.0.0 <102.0.0", "100.x", ">=99.5.0", "^2.0.0 || 150.0.0"]:
    refused(f"{r} past major 99", "admits an agent chart major of 100 or more", "--set", f"agent-manager.agentChart.semver={r}")
refused("range spanning 2.x and 3.x", "admits agent chart majors 2, 3", "--set", "agent-manager.agentChart.semver=>=2.6.0")
refused("3.x with agent-manager off: the operator still gets the range", "keep the 2.x line" if unset else "admits a release below",
        "--set", "components.agent-manager.enabled=false", "--set", f"agent-manager.agentChart.semver={V3}")

# With the floors set (a copy of the chart), the gated paths.
with tempfile.TemporaryDirectory() as tmp:
    META = with_floors(tmp)
    am = [a.replace("{floor}", TEST_FLOORS["agentManager.agentChartV3Floor"]) for a in AM]
    muster = [a.replace("{floor}", TEST_FLOORS["muster.agentApprovalFloor"]) for a in MUSTER]
    out = renders("3.x line with its floors", *LINE, *am, *muster, *DEFAULT_AGENT)
    if out.count(V3) < 3:
        fail(f"the 3.x line reaches {out.count(V3)} places; want agent-manager's release, the connectivity release and the default agent's source")
    for r in ["~3.2", "3.1.x", ">=3.1.0 <3.6.0"]:
        renders(f"{r} with its floors", "--set", f"agent-manager.agentChart.semver={r}", *am, *muster, "--set", f"components.default-agent.versionRange={r}")
        refused(f"{r}, muster below its floor", "admits a release below 5.68.0", "--set", f"agent-manager.agentChart.semver={r}", *am, "--set", f"components.default-agent.versionRange={r}")
    refused("3.x line, agent-manager below its floor", "admits a release below 3.6.0", *LINE, *muster, *DEFAULT_AGENT)
    refused("3.x line, muster below its floor", "admits a release below 5.68.0", *LINE, *am, *DEFAULT_AGENT)
    refused("3.x line, tool approval off", "agent-manager.muster.toolApproval.enabled is false",
            *LINE, *am, *muster, *DEFAULT_AGENT, "--set", "agent-manager.muster.toolApproval.enabled=false")
    refused("3.x line, default agent on 2.x", "select different agent chart majors", *LINE, *am, *muster)
    refused("2.x line, default agent on 3.x", "select different agent chart majors", *DEFAULT_AGENT)

META = META_ORIG

# The migrate run reads organization namespaces on the 3.x path:
# list on namespaces always, cluster-wide reads of releases, sources and Agents
# with organizations on, no write anywhere outside its own namespaces.
if CONN:
    conn_base = [HELM, "template", "t", CONN, "-n", "agent-platform", "-f", f"{CONN}/ci/test-substrate-values.yaml",
                 "-f", f"{CONN}/ci/test-llm-routing-values.yaml", "-f", os.path.join(os.path.dirname(__file__), "fixtures", "organizations-operator-values.yaml"),
                 "--set", "kagent.fluxServiceAccountName=kagent-flux", "--set", "global.domain=ci.example.com",
                 "--set", "components.agent-manager.enabled=true", "--set", "global.identity.issuerUrl=https://dex.ci.example.com",
                 "--set", "global.identity.clientId=platform", "--set", "global.identity.existingSecret=platform-oauth",
                 "--api-versions", "admissionregistration.k8s.io/v1/MutatingAdmissionPolicy"]

    def migrate_cluster_rules(*args: str) -> str:
        r = subprocess.run([*conn_base, *args], capture_output=True, text=True)
        if r.returncode != 0:
            fail(f"connectivity render {args} failed:\n{r.stderr[-600:]}")
            return ""
        found = [d for d in r.stdout.split("\n---\n") if "\nkind: ClusterRole\n" in "\n" + d and "-agent-manager-migrate-crds\n" in d]
        return found[0] if found else ""

    v3 = ("--set", f"agent-manager.agentChart.semver={V3}")
    ns_rule = 'resources: ["namespaces"]\n    verbs: ["list"]'
    two = migrate_cluster_rules()
    two_orgs = migrate_cluster_rules("--set", "agentPlatformOrganizations.enabled=true")
    three = migrate_cluster_rules(*v3)
    three_orgs = migrate_cluster_rules(*v3, "--set", "agentPlatformOrganizations.enabled=true")
    cutover = ("--set", "agentManager.migration.cutover=true")
    three_cut = migrate_cluster_rules(*v3, *cutover, "--set", "agentPlatformOrganizations.enabled=true")
    for what, text in (("2.x line", two), ("2.x line with organizations", two_orgs)):
        if ns_rule in text or "helm.toolkit.fluxcd.io" in text:
            fail(f"{what}: the migrate run reads no organization namespace, so its identity gets no namespace list and no cluster-wide release read")
    pre = ("--set", f"agentManager.migration.preflightSemver={V3}")
    pre_orgs = migrate_cluster_rules(*pre, "--set", "agentPlatformOrganizations.enabled=true")
    if ns_rule not in pre_orgs or 'resources: ["helmreleases"]\n    verbs: ["get", "list"]' not in pre_orgs:
        fail("a 3.x preflight range on the 2.x line grants the reads its dry run needs")
    r = subprocess.run([*conn_base, *pre], capture_output=True, text=True)
    cron = [d for d in r.stdout.split("\n---\n") if "\nkind: CronJob\n" in "\n" + d and "-agent-manager-migrate-preflight\n" in d]
    if len(cron) != 1:
        fail("a preflight range renders one CronJob <migration>-preflight")
    else:
        body = cron[0]
        if "suspend: true" not in body or "- --dry-run" not in body or f"value: '{V3}'" not in body or "AGENT_CHART_SEMVER_FILTER" in body:
            fail(f"the preflight CronJob is suspended, runs migrate --dry-run against the preflight range, without the line's filter:\n{body}")
    r = subprocess.run([*conn_base], capture_output=True, text=True)
    if "-agent-manager-migrate-preflight" in r.stdout:
        fail("without a preflight range no preflight CronJob renders")
    if ns_rule not in three or "helm.toolkit.fluxcd.io" in three:
        fail("3.x line without organizations: the run lists namespaces (and finds none) and reads no release cluster-wide")
    for want in ('resources: ["helmreleases"]\n    verbs: ["get", "list"]', 'resources: ["ocirepositories"]\n    verbs: ["get", "list"]', 'resources: ["agents"]\n    verbs: ["list"]', ns_rule):
        if want not in three_orgs:
            fail(f"3.x line with organizations: the migrate run's ClusterRole lacks {want!r}")
    # D-GO-03, D-GO-27: on the 3.x line itself (not a preflight) and only
    # while agentManager.migration.cutover is set, the run rewrites the
    # organization releases agent-manager owns and writes its report there:
    # update on HelmReleases, the report ConfigMap; nothing else, and the
    # scope policy holds those writes to organization namespaces and the
    # kagent namespace, spec.values only, the report's name only.
    def org_rules(text: str) -> str:
        return text.split("cluster-wide list on their kagent.dev", 1)[-1].split('resources: ["namespaces"]', 1)[-1]
    writes = ('resources: ["helmreleases"]\n    verbs: ["update"]', 'resources: ["configmaps"]\n    verbs: ["create"]',
              'resources: ["configmaps"]\n    resourceNames: ["agent-manager-migrate-report"]\n    verbs: ["get", "update"]')
    for want in writes:
        if want not in three_cut:
            fail(f"3.x line with organizations during the cut-over: the migrate run's ClusterRole lacks {want!r}")
    rules = org_rules(three_cut)
    for w in writes:
        rules = rules.replace(w, "")
    for verb in ("create", "update", "patch", "delete", "watch"):
        if f'"{verb}"' in rules:
            fail(f"the migrate run's organization rights carry {verb} beyond the release update and its report")
    pre_cut = migrate_cluster_rules(*pre, *cutover, "--set", "agentPlatformOrganizations.enabled=true")
    for what, text in (("a 3.x preflight on the 2.x line", pre_orgs), ("a 3.x preflight with cutover set", pre_cut),
                       ("3.x line without organizations", three), ("3.x line with organizations, cutover unset", three_orgs)):
        for verb in ("create", "update", "patch", "delete", "watch"):
            if f'"{verb}"' in org_rules(text):
                fail(f"{what}: the migrate run's cluster-wide rights carry {verb}; only the 3.x line with organizations writes")
    if '"get", "list"]\n' in rules.split('resources: ["agents"]', 1)[-1][:40]:
        fail("the run lists Agents in organization namespaces, it gets none")

    def scope_policy(*args: str) -> str:
        r = subprocess.run([*conn_base, *args], capture_output=True, text=True)
        found = [d for d in r.stdout.split("\n---\n") if "\nkind: ValidatingAdmissionPolicy\n" in "\n" + d and "name: agent-platform-organization-migrate-scope\n" in d]
        return found[0] if found else ""
    policy = scope_policy(*v3, *cutover, "--set", "agentPlatformOrganizations.enabled=true")
    if not policy:
        fail("3.x line with organizations: no agent-platform-organization-migrate-scope policy holds the run's writes")
    else:
        for want in ("system:serviceaccount:", "-agent-manager-migrate'", "request.name == 'agent-manager-migrate-report'",
                     "k == 'values'", "variables.organizationNamespace", 'resources: ["helmreleases"]', 'resources: ["configmaps"]',
                     "variables.sameMetadata", "ownerReferences", "finalizers"):
            if want not in policy:
                fail(f"the migrate scope policy lacks {want!r}:\n{policy}")
    for what, args in (("2.x line", (*cutover, "--set", "agentPlatformOrganizations.enabled=true")), ("preflight", (*pre, *cutover, "--set", "agentPlatformOrganizations.enabled=true")),
                       ("3.x without organizations", (*v3, *cutover)), ("3.x, cutover unset", (*v3, "--set", "agentPlatformOrganizations.enabled=true"))):
        if scope_policy(*args):
            fail(f"{what}: the migrate scope policy renders without the writes it holds")

    # D-GO-38: only a boolean true grants the writes. A string, also "false",
    # fails the render naming the key; a boolean false and an unset value
    # grant none.
    for text in ("false", "true", "no", ""):
        r = subprocess.run([*conn_base, *v3, "--set-string", f"agentManager.migration.cutover={text}", "--set", "agentPlatformOrganizations.enabled=true"],
                           capture_output=True, text=True)
        if r.returncode == 0 or "agentManager.migration.cutover must be the boolean" not in r.stderr:
            fail(f"agentManager.migration.cutover as the string {text!r} is not refused:\n{r.stderr[-400:]}")
    if scope_policy(*v3, "--set", "agentManager.migration.cutover=false", "--set", "agentPlatformOrganizations.enabled=true"):
        fail("a boolean false cutover renders the migrate scope policy")
    if not scope_policy(*v3, "--set-json", "agentManager.migration.cutover=true", "--set", "agentPlatformOrganizations.enabled=true"):
        fail("a boolean true cutover (set as JSON) renders no migrate scope policy")

    # D-GO-08: every alternative of a || range and every version a range
    # names counts, here as in migrate and the agent-platform-operator.
    refused("an alternative on another major", "admits agent chart majors 2, 3", "--set", "agent-manager.agentChart.semver=3.0.1 || 2.6.0")
    for r in (">=2.6.1 <2.6.9", "= 2.6.1", "~2.250", "2.6.1 - 2.6.5"):
        renders(f"{r} reads as 2.x", "--set", f"agent-manager.agentChart.semver={r}")
    # The connectivity chart's own probe (isV3): an exact pin and an
    # alternative only the named-version probes see (D-GO-08, D-GO-31).
    for r in ("3.0.1", "=3.0.1", "2.6.0 || 3.0.1", "3.0.1 || 3.2.0"):
        if ns_rule not in migrate_cluster_rules("--set", f"agent-manager.agentChart.semver={r}"):
            fail(f"the connectivity chart does not read {r} as a 3.x line")
    for r in ("2.6.1", ">=2.6.1 <2.6.9", "~2.250"):
        if ns_rule in migrate_cluster_rules("--set", f"agent-manager.agentChart.semver={r}"):
            fail(f"the connectivity chart reads {r} as a 3.x line")

# A floor that needs the governance wiring, set while the governance floor is
# unset (a release that sets its floors in two steps): the render fails naming
# both, not an agent-manager that binds what muster refuses.
for dependent in ("muster.agentApprovalFloor", "agentManager.agentChartV3Floor", "agentManager.toolApprovalFloor", "muster.sessionBindingsPortFloor"):
    with tempfile.TemporaryDirectory() as tmp:
        META = with_floors(tmp, {dependent: TEST_FLOORS[dependent]} | {"muster.governanceFloor": ""})
        refused(f"{dependent} set, governance floor unset", f"agent-platform.{dependent} is set while agent-platform.muster.governanceFloor is unset")
        refused(f"{dependent} set, governance floor unset, 2.x line", "agent-platform.muster.governanceFloor is unset", "--set", "agent-manager.agentChart.semver=2.6.1")
META = META_ORIG
with tempfile.TemporaryDirectory() as tmp:
    META = with_floors(tmp, {n: v for n, v in TEST_FLOORS.items() if n != "muster.governanceFloor"} | {"muster.governanceFloor": ""})
    refused("every floor but the governance floor set", "agent-platform.muster.governanceFloor is unset", *LINE, "--set", "components.default-agent.versionRange=" + V3)
META = META_ORIG


# A bound that admits a pre-release of the next major, and range
# shapes that are no semver range. Run under the Helm of the run, and once more
# under Helm 3.17 (HELM_LEGACY), whose semver library never matches a
# pre-release against a range with no pre-release lower bound, so only the
# textual read of the upper bounds (nextMajorPreRelease) can see them there.
def bound_cases() -> None:
    refused("<=3.0.0-rc.1 bound", "admits 3.0.0-0, a pre-release of the next agent chart major", "--set", "agent-manager.agentChart.semver=>=2.6.0 <=3.0.0-rc.1")
    refused("<3.0.0-rc.1 bound", "a pre-release of the next agent chart major", "--set", "agent-manager.agentChart.semver=>=2.6.0 <3.0.0-rc.1")
    refused("<3.0.0-0.1 bound (not the -0 exception)", "a pre-release of the next agent chart major", "--set", "agent-manager.agentChart.semver=>=2.6.0 <3.0.0-0.1")
    renders("<3.0.0-0 bound (the exception)", "--set", "agent-manager.agentChart.semver=>=2.6.0 <3.0.0-0")


bound_cases()
if os.environ.get("HELM_LEGACY"):
    HELM = os.environ["HELM_LEGACY"]
    bound_cases()
    HELM = os.environ.get("HELM") or "helm"
elif not failures:
    print("note: HELM_LEGACY (Helm 3.17) is not set: the textual read of pre-release upper bounds ran only where the probes find them", file=sys.stderr)
refused("majors beyond 5", "admits agent chart majors 3, 6", "--set", "agent-manager.agentChart.semver=>=3.0.0 <4.0.0 || 6.x")
refused("latest", "is not a semver range", "--set", "agent-manager.agentChart.semver=latest")
refused("empty", "is not a semver range", "--set-string", "agent-manager.agentChart.semver=")
renders("an exact 2.x pin", "--set-string", "agent-manager.agentChart.semver=2.6.1")
refused("an exact 3.x pin", "keep the 2.x line" if unset else "admits a release below", "--set-string", "agent-manager.agentChart.semver=3.0.0")
renders("prerelease-widened 2.x line", "--set", "agent-manager.agentChart.semver=>=2.6.0-0 <3.0.0-0")

# An || range is held to its lowest alternative's floor.
with tempfile.TemporaryDirectory() as tmp:
    META = with_floors(tmp)
    am = [a.replace("{floor}", TEST_FLOORS["agentManager.agentChartV3Floor"]) for a in AM]
    muster = [a.replace("{floor}", TEST_FLOORS["muster.agentApprovalFloor"]) for a in MUSTER]
    for what, args in (("muster", ["--set", "components.muster.versionRange=>=5.68.0 <6.0.0 || >=5.0.0", *am]),
                       ("muster, exact alternatives", ["--set", "components.muster.versionRange=5.68.0 || 5.66.2", *am]),
                       ("agent-manager", ["--set", "components.agent-manager.versionRange=>=3.6.0 || >=3.5.0", *muster])):
        refused(f"|| range of {what} below its floor", "admits a release below", *LINE, *DEFAULT_AGENT, *args)
META = META_ORIG

if failures:
    for f in failures:
        print(f"FAIL: {f}", file=sys.stderr)
    sys.exit(1)
print("agent chart line verified.")
