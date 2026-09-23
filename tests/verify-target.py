#!/usr/bin/env python3
"""Assert one release of this chart per target cluster (giantswarm/agent-platform#328).

The serving slice (#326) and the runtime slice (#317) are values profiles of this
chart that cluster-manager installs as ONE `<cluster>-agent-platform` release per
target cluster — beside the platform's own release on the installation's cluster,
or onto a workload cluster that runs no Flux, through the installation's Flux the
way the fleet installs every workload-cluster app. Each case below pins one
property that shape relies on:

- the target knob: gitops.target.kubeConfig.secretRef stamps spec.kubeConfig.secretRef
  (name, and key when set) onto EVERY HelmRelease the component loop renders and
  changes nothing else — the knob render minus those lines is the default render;
  unset, no HelmRelease carries kubeConfig;
- the goldens: with the new keys at their defaults, the default and CI renders of
  the meta chart and the default and full renders of the connectivity chart are
  byte-identical to GOLDEN_REF (origin/main; GOLDEN_REF= opts out);
- the toggles: components.muster.enabled / components.dicebear.enabled off render
  no release of theirs, the roster forwarded to the connectivity release says so,
  and the connectivity chart drops their references — muster's /mcp route, the
  muster egress policy and every policy rule that selects its pods; the avatars
  host in the portal's CSP;
- the guards that can be asserted offline: the knob with the bundled engine on
  fails naming the engine; the schema refuses an unknown key under gitops.target.
  The lookup guards (a foreign helm-controller with the engine on; a second owner
  of a component's CRDs, components.<name>.ownedCrds) need a live cluster and are
  asserted there (README, "One release per target cluster");
- no hook Job renders with the knob (a hook runs where the chart is installed, not
  on the target), while the same toggles without the knob render the kagent
  storage-version pair as before;
- the slices: the serving- and runtime-shaped toggle sets (ci/test-slice-*-values.yaml)
  render alone and combined, the combined release is the union, and every
  OCIRepository / HelmRelease of the first slice is byte-identical in the combined
  render (switching the second slice on is an in-place upgrade: nothing renames
  or moves; the connectivity release's values follow the roster by design);
  agentgateway follows the target: no release beside the platform's own, a
  release with the target knob (ci/test-target-values.yaml), every HelmRelease
  then carrying the kubeConfig.

Deliberately stdlib-only: the CI image has no PyYAML. HELM selects the binary.
"""

import os
import re
import shutil
import subprocess
import sys
import tempfile

HELM = os.environ.get("HELM", "helm")
FLEET_APIS = [
    "--api-versions", "kyverno.io/v1",
    "--api-versions", "cilium.io/v2",
    "--api-versions", "monitoring.coreos.com/v1",
    "--api-versions", "gateway.networking.k8s.io/v1",
    "--api-versions", "gateway.envoyproxy.io/v1alpha1",
]
# Makefile.custom.mk's VM: the all-modes ingress guard satisfied, the Harness's
# snapshot store set, the fleet's API groups served.
VM = ["--set", "ingress.parentRefs[0].name=x", "--set", "kagent.harness.snapshotLocation=s3://ci-agent-snapshots/agents", *FLEET_APIS]
ENGINE_OFF = ["--set", "components.flux.enabled=false"]
FLUX_KINDS = {"OCIRepository", "HelmRelease"}
# The kagent storage-version hooks and their identity (hooks/*.yaml), the only
# objects of the engine-off render that are not Flux documents.
HOOK_OBJECTS = {("Job", "t-kagent-storage-version-backup"), ("Job", "t-kagent-storage-version-restore"),
                ("ServiceAccount", "t-hooks"), ("ClusterRole", "t-hooks"), ("ClusterRoleBinding", "t-hooks"),
                ("Role", "t-hooks"), ("RoleBinding", "t-hooks"), ("NetworkPolicy", "t-hooks"), ("CiliumNetworkPolicy", "t-hooks")}
KNOB = ["--set", "gitops.target.kubeConfig.secretRef.name=wc01-kubeconfig"]
KNOB_KEY = ["--set", "gitops.target.kubeConfig.secretRef.key=value"]
# The connectivity chart with everything that references muster on: the
# agentgateway wiring (its /mcp route, the data-plane and controller policies),
# the Substrate egress and muster's own in-cluster MCP egress.
CONN_FULL = [
    *VM,
    "--set", "components.agentgateway.enabled=true", "--set", "ingress.mode=agentgateway-muster",
    "--set", "components.kagent.enabled=true", "--set", "components.substrate.enabled=true",
    "--set", "components.substrate-crds.enabled=true", "--set", "networkPolicy.musterInClusterMcpPorts[0]=8080",
]
# giantswarm/agent-platform#586: the default metric-label expressions read the
# kagent runtime's identity headers behind the Substrate egress predicate, and
# GOLDEN_REF renders the plain source-IP attributes. Held equal on BOTH sides --
# the three old expressions, which the meta chart forwards as written -- and
# dropped once GOLDEN_REF carries #586.
METRIC_LABELS_HOLD = [
    "--set", "gateway.metricLabels.agent.expression=source.unverifiedWorkload.serviceAccount",
    "--set", "gateway.metricLabels.agent_namespace.expression=source.unverifiedWorkload.namespace",
    "--set", "gateway.metricLabels.user.expression=jwt.email",
]
# giantswarm/agent-platform#608: both charts name the agentgateway line's 2.0.0
# under its nested names in full (the controller and the data plane), and
# GOLDEN_REF names the flattened controller repository and the v1.5.1-gs.4 data
# plane. Held equal on BOTH sides of the meta and the connectivity renders;
# dropped once GOLDEN_REF carries #608.
# giantswarm/giantswarm#36711: the muster board moves to the platform's own
# Grafana folder and to the organization customers reach, and GOLDEN_REF carries
# the muster chart's own defaults for both. Held equal on BOTH sides of the meta
# renders; dropped once GOLDEN_REF carries them.
MUSTER_DASHBOARD_HOLD = [
    "--set", "muster.muster.observability.grafanaDashboard.folder=muster",
    "--set", "muster.muster.observability.grafanaDashboard.giantswarm.organization=Giant Swarm",
]
# giantswarm/giantswarm#36711: muster's and Substrate's OTLP endpoints, unset
# on GOLDEN_REF (the meta chart forwards no otel block for either). Neither
# key exists on GOLDEN_REF's side at all, so it cannot be held by --set (there
# is no default to fall back to); the two blocks are cut from the meta
# renders instead. Dropped once GOLDEN_REF carries them.
MUSTER_OTEL = re.compile(
    r"^(\s+)otel:\n\1  endpoint: http://otlp-gateway\.kube-system\.svc:4317\n"
    r"\1  headers: X-Scope-OrgID=giantswarm\n\1  protocol: grpc\n", re.M)
SUBSTRATE_OTEL = re.compile(r"^(\s+)otel:\n\1  endpoint: http://otlp-gateway\.kube-system\.svc:4317\n", re.M)
# The data plane's tenant pod label (gateway.parameters.podLabels), a key
# GOLDEN_REF's closed gateway.parameters schema refuses, so it is cut too.
DATAPLANE_POD_LABELS = re.compile(r"^(\s+)podLabels:\n\1  observability\.giantswarm\.io/tenant: giantswarm\n", re.M)


def hold_otlp_endpoints(here: str, there: str) -> tuple:
    """The two meta renders with muster's and Substrate's new OTLP blocks and the data plane's tenant pod label cut out."""
    def strip(render: str) -> str:
        render = MUSTER_OTEL.sub("", render)
        render = DATAPLANE_POD_LABELS.sub("", render)
        return SUBSTRATE_OTEL.sub("", render)
    if (h := strip(here)) != here or strip(there) != there:
        print("note: #36711 hold — muster's and Substrate's OTLP endpoint blocks and the data plane's tenant pod label are left out of the golden comparison")
    return h, strip(there)
# giantswarm/agentgateway#60: this tree turns the packaging chart's own
# monitoring on (its controller ServiceMonitor, proxy PodMonitor and dashboard
# ConfigMap), which GOLDEN_REF's defaults leave off and whose block it does not
# carry at all. The whole block is written on BOTH sides so the forwarded values
# compare equal; dropped once GOLDEN_REF carries it.
AGENTGATEWAY_MONITORING_HOLD = [
    "--set", "agentgateway.monitoring.enabled=false",
    "--set", "agentgateway.monitoring.serviceMonitor.enabled=true",
    "--set", "agentgateway.monitoring.serviceMonitor.interval=60s",
    "--set", "agentgateway.monitoring.serviceMonitor.extraLabels.observability\\.giantswarm\\.io/tenant=giantswarm",
    "--set", "agentgateway.monitoring.grafanaDashboard.enabled=true",
    "--set", "agentgateway.monitoring.grafanaDashboard.labels.app\\.giantswarm\\.io/kind=dashboard",
    "--set", "agentgateway.monitoring.grafanaDashboard.annotations.observability\\.giantswarm\\.io/organization=Shared Org",
    "--set", "agentgateway.monitoring.grafanaDashboard.annotations.observability\\.giantswarm\\.io/folder=Agent Platform",
]
# giantswarm/giantswarm#36711: this tree turns the mcp-kubernetes chart's own
# ServiceMonitor and its three Grafana boards on, which GOLDEN_REF's defaults
# leave off and whose keys it does not carry at all. The whole block is written
# on BOTH sides so the forwarded values compare equal; dropped once GOLDEN_REF
# carries it.
MCP_KUBERNETES_MONITORING_HOLD = [
    "--set", "mcp-kubernetes.mcpKubernetes.instrumentation.serviceMonitor.enabled=false",
    "--set", "mcp-kubernetes.mcpKubernetes.instrumentation.serviceMonitor.labels.observability\\.giantswarm\\.io/tenant=giantswarm",
    "--set", "mcp-kubernetes.grafanaDashboards.enabled=false",
    "--set", "mcp-kubernetes.grafanaDashboards.folder=Agent Platform",
    "--set", "mcp-kubernetes.grafanaDashboards.giantswarm.enabled=true",
    "--set", "mcp-kubernetes.grafanaDashboards.giantswarm.organization=Shared Org",
]
# giantswarm/klaus-gateway#319: the meta chart no longer forwards the six
# klausGateway keys the Slack-only gateway accepts only as no-ops, so the head
# renders LACK the lines GOLDEN_REF still carries. A hold written on both sides
# cannot express that — on head a `--set <key>=null` inserts `key: null` where
# nothing stands — so this one is applied to the GOLDEN side alone, where
# `null` deletes the key from the forwarded tree. Drop it once GOLDEN_REF
# carries the removal.
KLAUS_GATEWAY_DROPPED_KEYS_GOLDEN_ONLY = [
    "--set", "klausGateway.cli=null",
    "--set", "klausGateway.lifecycle=null",
    "--set", "klausGateway.upstream=null",
    "--set", "klausGateway.agentgateway=null",
    "--set", "klausGateway.routing.defaultTTL=null",
    "--set", "klausGateway.a2a.saToken=null",
]
# giantswarm/klaus-gateway#319: the component's floor is the Slack-only release,
# because the values above no longer start a 1.x gateway; GOLDEN_REF stops at
# 1.20.0. Written on BOTH sides so the range compares equal; dropped once
# GOLDEN_REF carries the floor.
KLAUS_GATEWAY_FLOOR_HOLD = [
    # The ceiling admits the 3.x line (giantswarm/klaus-gateway#319, PR B).
    "--set", "components.klaus-gateway.versionRange=>=2.0.0 <4.0.0",
]
# giantswarm/vm-manager#73, giantswarm/giantswarm#36711: the vm-manager chart
# gains its own ServiceMonitor and this tree resolves its `auto` switch and
# sets the tenant label, keys GOLDEN_REF's vm-manager block does not carry
# (an open block, so it forwards them as written), and moves the component's
# floor to the release that opens the keys. Written on BOTH sides so the
# forwarded values and the range compare equal; dropped once GOLDEN_REF
# carries them.
VM_MANAGER_MONITOR_HOLD = [
    "--set", "components.vm-manager.versionRange=>=0.22.0 <1.0.0",
    "--set", "vm-manager.serviceMonitor.enabled=false",
    "--set", "vm-manager.serviceMonitor.interval=60s",
    "--set", "vm-manager.serviceMonitor.labels.observability\\.giantswarm\\.io/tenant=giantswarm",
]
# giantswarm/kserve#88, giantswarm/giantswarm#36711: the llmisvc controller's
# ServiceMonitor and plain-HTTP metrics, keys GOLDEN_REF's
# kserve-llmisvc-resources block does not carry, and the kserve charts' 0.5.x
# line that opens them. Written on BOTH sides so the forwarded values and the
# ranges compare equal; dropped once GOLDEN_REF carries them.
KSERVE_MONITOR_HOLD = [
    "--set", "components.kserve-llmisvc-crd.versionRange=0.5.x",
    "--set", "components.kserve-llmisvc-resources.versionRange=0.5.x",
    "--set", "components.kserve-runtime-configs.versionRange=0.5.x",
    "--set", "kserve-llmisvc-resources.kserve.llmisvc.controller.metricsSecure=false",
    "--set", "kserve-llmisvc-resources.kserve.llmisvc.controller.serviceMonitor.enabled=false",
    "--set", "kserve-llmisvc-resources.kserve.llmisvc.controller.serviceMonitor.interval=60s",
    "--set", "kserve-llmisvc-resources.kserve.llmisvc.controller.serviceMonitor.labels.observability\\.giantswarm\\.io/tenant=giantswarm",
]
# giantswarm/agent-platform#455 follow-up: this tree sets the kagent
# controller's resources (the memory limit raised to 1536Mi), a key GOLDEN_REF's
# kagent.controller block does not carry (an open block, so it forwards it as
# written), and moves the VPA's memory cap to 1280Mi. Written on BOTH sides so
# the forwarded values and the rendered VPA compare equal; dropped once
# GOLDEN_REF carries them.
KAGENT_CONTROLLER_RESOURCES_HOLD = [
    "--set", "kagent.controller.resources.requests.cpu=100m",
    "--set", "kagent.controller.resources.requests.memory=128Mi",
    "--set", "kagent.controller.resources.limits.cpu=2",
    "--set", "kagent.controller.resources.limits.memory=1536Mi",
    "--set", "kagent.controller.vpa.maxAllowed.memory=1280Mi",
]
KAGENT_VPA_CAP_HOLD = ["--set", "kagent.controller.vpa.maxAllowed.memory=1280Mi"]
AGENTGATEWAY_IMAGES_HOLD = [
    "--set", "agentgateway.controller.image.repository=giantswarm/agentgateway-upstream/controller",
    "--set", "agentgateway.controller.image.tag=2.0.0",
    "--set", "agentgateway.proxy.image.repository=giantswarm/agentgateway-upstream/agentgateway",
    "--set", "agentgateway.proxy.image.tag=2.0.0",
]
# Makefile.custom.mk's WIRING_BACKSTAGE: the portal on, whose CSP carries the avatars host.
CONN_BACKSTAGE = [
    *VM, "--namespace", "agent-platform",
    "--set", "global.domain=ci.example.com", "--set", "global.identity.issuerUrl=https://dex.ci.example.com",
    "--set", "global.identity.clientId=agent-platform", "--set", "global.identity.existingSecret=agent-platform-idp",
    "--set", "global.gatewayApi.parentRefs[0].name=giantswarm-default",
    "--set", "global.gatewayApi.parentRefs[0].namespace=envoy-gateway-system",
    "--set", "components.backstage.enabled=true",
]


def helm(chart: str, flags: list[str], expect_failure: bool = False) -> str:
    result = subprocess.run([HELM, "template", "t", chart, *flags], capture_output=True, text=True, check=False)
    if expect_failure:
        if result.returncode == 0:
            sys.exit(f"FAIL: render of {chart} {' '.join(flags)} succeeded, a failure was expected")
        return result.stderr
    if result.returncode != 0:
        sys.exit(f"FAIL: render of {chart} {' '.join(flags)} failed\n{result.stderr}")
    return result.stdout


def documents(manifest: str) -> dict[tuple[str, str], str]:
    """(kind, name) -> document, for every rendered object."""
    docs = {}
    for doc in manifest.split("\n---\n"):
        kind = re.search(r"^kind: (\S+)$", doc, re.M)
        name = re.search(r"^  name: (\S+)$", doc, re.M)
        if kind and name:
            docs[(kind.group(1), name.group(1))] = doc.strip("\n")
    return docs


def releases(manifest: str) -> set[str]:
    return {name for kind, name in documents(manifest) if kind == "HelmRelease"}


def ok(msg: str) -> None:
    print(f"ok: {msg}")


def check_knob(meta: str) -> None:
    ci = ["-f", f"{meta}/ci/ci-values.yaml", *ENGINE_OFF]
    plain = helm(meta, ci)
    if "kubeConfig:" in plain:
        sys.exit("FAIL: a HelmRelease carries kubeConfig without the target knob")
    for flags, lines in ((KNOB, ["  kubeConfig:", "    secretRef:", "      name: wc01-kubeconfig"]),
                         (KNOB + KNOB_KEY, ["  kubeConfig:", "    secretRef:", "      name: wc01-kubeconfig", "      key: value"])):
        knob = helm(meta, [*ci, *flags])
        docs = documents(knob)
        hrs = [d for (kind, _), d in docs.items() if kind == "HelmRelease"]
        block = "\n".join(lines)
        missing = [d for d in hrs if block not in d]
        if not hrs or missing:
            sys.exit(f"FAIL: {len(missing)} of {len(hrs)} HelmReleases lack the kubeConfig block with {flags}")
        if knob.count("  kubeConfig:") != len(hrs):
            sys.exit("FAIL: kubeConfig rendered outside a HelmRelease")
        # The Flux documents minus the kubeConfig lines are the default's; what else
        # leaves the render is the hook family (ci-values turn kagent on, and the
        # storage-version hooks run where the chart is installed — check_hooks).
        stripped = {k: "\n".join(line for line in d.split("\n") if line not in lines) for k, d in docs.items() if k[0] in FLUX_KINDS}
        if stripped != {k: d for k, d in documents(plain).items() if k[0] in FLUX_KINDS}:
            sys.exit(f"FAIL: the knob changed an OCIRepository / HelmRelease beyond the kubeConfig lines ({flags})")
        gone = {k for k in documents(plain) if k not in docs}
        if not gone <= HOOK_OBJECTS or {k for k in docs if k not in documents(plain)}:
            sys.exit(f"FAIL: the knob changed the render beyond the kubeConfig lines and the hooks: gone={sorted(gone)}")
        ok(f"every one of the {len(hrs)} HelmReleases carries the kubeConfig secretRef ({', '.join(f.split('.')[-1] for f in flags[1::2])}), nothing else changed")
    for f in ("test-target-values.yaml", "test-slice-serving-values.yaml", "test-slice-runtime-values.yaml"):
        helm(meta, ["-f", f"{meta}/ci/{f}"])
    ok("the target and slice fixtures each render alone")


ROSTER = re.compile(r"(?<=\n    components:\n)((?:      [a-z0-9-]+:\n        enabled: (?:true|false)\n)+)")

# The classic KServe controller went with the classic InferenceService serving
# path (giantswarm/agent-platform#574): the meta chart forwards no kserve-crd /
# kserve-resources block any more, the kserve-llmisvc-resources block carries
# the control plane's shared objects and the controller knobs that lived on
# kserve-resources, and model-manager's range admits 1.0.0. Held on BOTH sides,
# each key named; drop once GOLDEN_REF carries #574.
LLMD_ONLY_BLOCKS = re.compile(r"^    (?:kserve-crd|kserve-resources|kserve-llmisvc-resources):(?: \{\})?\n(?:      .*\n)*", re.M)
MODEL_MANAGER_RANGE = re.compile(r'(url: oci://gsoci\.azurecr\.io/charts/giantswarm/model-manager\n  ref:\n    semver: )"[^"]*"')


def hold_llmd_only(here: str, there: str) -> tuple:
    """The two meta renders with #574's intended differences held equal on both sides."""
    def strip(render: str) -> str:
        render = LLMD_ONLY_BLOCKS.sub("", render)
        return MODEL_MANAGER_RANGE.sub(r'\1"<held: #574>"', render)
    if (h := strip(here)) != here or strip(there) != there:
        print("note: #574 hold — the connectivity release's kserve-crd, kserve-resources and kserve-llmisvc-resources values blocks and model-manager's range are left out of the golden comparison")
    return h, strip(there)


# The hook Jobs' pod template (giantswarm/agent-platform#593): every hook Job of
# the meta chart runs with a 512Mi memory limit and carries the comment on why;
# GOLDEN_REF renders 128Mi and no comment. Held on BOTH sides by the shape of the
# hook pods' resources block (requests 10m / 32Mi, one memory limit), at any
# indentation — the self-management hooks are also rendered inside the values of
# the chart's own release; drop once GOLDEN_REF carries #593.
HOOK_POD_RESOURCES = re.compile(
    r"(?:^ +# .*\n){0,9}(^ +resources:\n +requests:\n +cpu: 10m\n +memory: 32Mi\n +limits:\n +memory: )(?:128Mi|512Mi)$", re.M)
# The same PR moves the storage-version backup's release pre-filter from a plain
# grep for the version to the JSON-encoded manifest marker (two comment lines and
# the grep line, at the script's indentation); both shapes are held to one line.
HOOK_PREFILTER = re.compile(
    r"(?:^ +# a manifest that names an object at v1alpha2.*\n^ +# mentions the version.*\n)?^( +)grep -qF? '(?:apiVersion: )?kagent\.dev/v1alpha2(?:\\n)?' /tmp/release\.json \|\| continue$", re.M)


def hold_hook_pods(here: str, there: str) -> tuple:
    """The two meta renders with #593's hook Job pod template held equal on both sides."""
    def strip(render: str) -> str:
        render = HOOK_POD_RESOURCES.sub(r"\g<1><held: #593>", render)
        return HOOK_PREFILTER.sub(r"\g<1><held: #593 pre-filter>", render)
    if (h := strip(here)) != here or strip(there) != there:
        print("note: #593 hold — the hook pods' resources block (the memory limit and its comment) and the backup's release pre-filter lines are left out of the golden comparison")
    return h, strip(there)

# giantswarm/agent-platform#608: the Substrate range moved from the former
# `>=X.Y.Z-gs.N <X.Y.(Z+1)-0` shape to `>=1.0.0 <1.1.0`, and each side's
# validateRange refuses the other's shape, so the range cannot be held by --set.
# The two substrate OCIRepositories' semver and the kagent release's derived
# worker image (ateom-gvisor:<floor>) are blanked on BOTH sides instead; drop
# once GOLDEN_REF carries #608.
SUBSTRATE_RANGE = re.compile(r'(url: oci://gsoci\.azurecr\.io/giantswarm/substrate/helm/substrate(?:-crds)?\n  ref:\n    semver: )"[^"]*"')
WORKER_IMAGE = re.compile(r"(workerImage: gsoci\.azurecr\.io/giantswarm/substrate/ateom-gvisor:)\S+")


def hold_substrate_range(here: str, there: str) -> tuple:
    """The two meta renders with #608's Substrate range and derived worker image held equal on both sides."""
    def strip(render: str) -> str:
        render = SUBSTRATE_RANGE.sub(r'\1"<held: #608>"', render)
        return WORKER_IMAGE.sub(r"\1<held: #608>", render)
    if (h := strip(here)) != here or strip(there) != there:
        print("note: #608 hold — the substrate and substrate-crds ranges and the derived worker image tag are left out of the golden comparison")
    return h, strip(there)


def drop_new_roster_entries(here: str, there: str) -> tuple:
    """The two meta renders with the roster entries only one side has removed.

    The component loop forwards EVERY roster entry's `enabled` to the connectivity
    release (templates/components.yaml, the roster), so a component new to the
    working tree is a roster line the golden ref cannot have — the one difference
    a new component is allowed. Only the roster block of the connectivity
    HelmRelease is touched, and only by the entries in the symmetric difference;
    everything else stays byte for byte.
    """
    def entries(render: str) -> set:
        m = ROSTER.search(render)
        return set(re.findall(r"^      ([a-z0-9-]+):$", m.group(1), re.M)) if m else set()

    new = entries(here) ^ entries(there)
    if not new:
        return here, there
    def strip(render: str) -> str:
        m = ROSTER.search(render)
        if not m:
            return render
        block = m.group(1)
        for name in new:
            block = re.sub(rf"^      {re.escape(name)}:\n        enabled: (?:true|false)\n", "", block, flags=re.M)
        return render[:m.start(1)] + block + render[m.end(1):]
    print(f"note: roster entries only one side has, dropped from the golden comparison: {', '.join(sorted(new))}")
    return strip(here), strip(there)


# giantswarm/agentgateway#60: this chart's own data-plane PodMonitor is gone —
# the packaging chart's own monitor is the one scrape path, and two monitors of
# the same pods double every data-plane series. GOLDEN_REF still renders it, so
# the document is dropped from BOTH sides; dropped once GOLDEN_REF carries it.
PODMONITOR_SOURCE = "# Source: agent-platform-connectivity/templates/agentgateway/podmonitor.yaml"


# giantswarm/agent-platform#634: the model catalog prices claude-opus-5-5, a key
# GOLDEN_REF's closed anthropic.models map refuses, so it cannot be held by --set
# and is cut from every render instead. Dropped once GOLDEN_REF carries it.
OPUS_5_5_PRICE = re.compile(r"^(\s+)claude-opus-5-5:\n\1  rates:\n(?:\1    \w+: \"[\d.]+\"\n){4}", re.M)


def hold_opus_5_5_price(here: str, there: str) -> tuple:
    """Both renders with the claude-opus-5-5 catalog entry cut out."""
    h, t = OPUS_5_5_PRICE.sub("", here), OPUS_5_5_PRICE.sub("", there)
    if h != here or t != there:
        print("note: #634 hold — the claude-opus-5-5 catalog entry is left out of the golden comparison")
    return h, t


# giantswarm/giantswarm#36711: the data plane's trace export — the Gateway-scoped
# tracing policy and its egress policy, new objects GOLDEN_REF does not render.
# Dropped from BOTH sides; dropped once GOLDEN_REF carries them.
TRACING_SOURCE = "# Source: agent-platform-connectivity/templates/agentgateway/tracing.yaml"


# giantswarm/agent-platform#630: the data plane's buffer — gateway.http.maxBufferSize,
# a key GOLDEN_REF's closed gateway block refuses (so it cannot be held by --set),
# forwarded by the meta chart to the connectivity release and rendered by the
# connectivity chart as the Gateway-scoped -http policy, a new object. Both are
# cut from BOTH sides; dropped once GOLDEN_REF carries them.
HTTP_BUFFER_VALUE = re.compile(r"^(\s+)http:\n\1  maxBufferSize: 8Mi\n", re.M)
HTTP_POLICY_SOURCE = "# Source: agent-platform-connectivity/templates/agentgateway/http-policy.yaml"


def hold_dataplane_buffer(here: str, there: str) -> tuple:
    """Both renders with the forwarded buffer size and the -http policy left out."""
    def strip(render: str) -> str:
        docs = [d for d in render.split("\n---\n") if HTTP_POLICY_SOURCE not in d]
        return HTTP_BUFFER_VALUE.sub("", "\n---\n".join(docs))
    if (h := strip(here)) != here or strip(there) != there:
        print("note: #630 hold — the data plane's buffer size (gateway.http.maxBufferSize) and the -http policy are left out of the golden comparison")
    return h, strip(there)


def hold_dataplane_tracing(here: str, there: str) -> tuple:
    """The two connectivity renders with the data plane's tracing objects left out."""
    def strip(render: str) -> str:
        return "\n---\n".join(d for d in render.split("\n---\n") if TRACING_SOURCE not in d)
    if (h := strip(here)) != here or strip(there) != there:
        print("note: #36711 hold — the data plane's tracing policy and its egress policy are left out of the golden comparison")
    return h, strip(there)


def hold_dataplane_podmonitor(here: str, there: str) -> tuple:
    """The two connectivity renders with this chart's retired PodMonitor left out."""
    def strip(render: str) -> str:
        return "\n---\n".join(d for d in render.split("\n---\n") if PODMONITOR_SOURCE not in d)
    if (h := strip(here)) != here or strip(there) != there:
        print("note: agentgateway#60 hold — this chart's retired data-plane PodMonitor is left out of the golden comparison")
    return h, strip(there)


# giantswarm/giantswarm#36711: the platform's own board ConfigMaps and the
# dashboards block that configures them. GOLDEN_REF has neither — its schema has
# no `dashboards` key either, so this cannot be held with --set — so the block is
# dropped from the meta renders' forwarded values and the ConfigMap documents
# from the connectivity renders. Both go with the line.
DASHBOARDS_KEY = re.compile(r"^(\s+)dashboards:\s*$")
DASHBOARDS_CONFIGMAP = "# Source: agent-platform-connectivity/templates/dashboards/configmap.yaml"


def drop_mapping(render: str, key: re.Pattern) -> str:
    """The render without the mapping `key` names: its line and every line
    indented deeper than it."""
    out, lines, i = [], render.split("\n"), 0
    while i < len(lines):
        if m := key.match(lines[i]):
            indent = len(m.group(1))
            i += 1
            while i < len(lines) and (not lines[i].strip()
                                      or len(lines[i]) - len(lines[i].lstrip(" ")) > indent):
                i += 1
            continue
        out.append(lines[i])
        i += 1
    return "\n".join(out)


def hold_dashboards(here: str, there: str, is_meta: bool) -> tuple:
    """The two renders with the platform's own boards left out of the comparison."""
    def strip(render: str) -> str:
        if is_meta:
            return drop_mapping(render, DASHBOARDS_KEY)
        return "\n---\n".join(d for d in render.split("\n---\n") if DASHBOARDS_CONFIGMAP not in d)
    if (h := strip(here)) != here or strip(there) != there:
        print("note: #36711 hold — the platform's own dashboards block and board ConfigMaps are left out of the golden comparison")
    return h, strip(there)


def check_golden(meta: str, connectivity: str) -> None:
    ref = os.environ.get("GOLDEN_REF", "origin/main")
    if not ref:
        print("skip: GOLDEN_REF is empty (explicit opt-out)")
        return
    if subprocess.run(["git", "rev-parse", "--verify", "-q", ref], capture_output=True).returncode != 0:
        sys.exit(f"FAIL: GOLDEN_REF={ref} does not resolve; fetch it, point GOLDEN_REF at another ref, or run with GOLDEN_REF= to opt out")
    tree = tempfile.mkdtemp(prefix="ap-target-golden-")
    shutil.rmtree(tree)
    subprocess.run(["git", "worktree", "add", "-q", "--detach", tree, ref], check=True)
    try:
        # No held-equal flags: GOLDEN_REF is origin/main, which now carries every
        # feature the holds here used to mask, so each one only narrowed the
        # comparison (giantswarm/agent-platform#455 and #530 for the one that
        # narrowed it asymmetrically and broke the target). A NEW intended
        # difference gets its hold back, applied to BOTH sides and with the key
        # named, and is dropped again once GOLDEN_REF carries it — the image
        # defaults' move to gsoci, the kserve 0.4.x ranges and the forwarded
        # imageVerification defaults (#575) are the newest to have reached that
        # point.
        # The hold for giantswarm/agent-platform#608, applied to BOTH sides: the
        # kagent line at its decoupled 1.0 range, the agentgateway component
        # floored at the packaging release that renders a bare tag as written,
        # the Substrate egress dataplane at the agentgateway line's 2.0.0 — meta
        # chart keys — and AGENTGATEWAY_IMAGES_HOLD on every render. The
        # Substrate range cannot be set on both sides (GOLDEN_REF's validateRange
        # admits the former `-gs.N` shape only, this tree's the stable one), so
        # hold_substrate_range() blanks it and the worker image derived from it
        # in both renders. Dropped once GOLDEN_REF carries them. METRIC_LABELS_HOLD
        # (#586) is the other hold in force.
        hold_608 = ["--set", "components.kagent.versionRange=>=1.0.0 <1.1.0",
                    "--set", "components.kagent-crds.versionRange=>=1.0.0 <1.1.0",
                    "--set", "components.agentgateway.versionRange=>=2.4.0 <3.0.0",
                    "--set", "substrate.images.agentgateway=gsoci.azurecr.io/giantswarm/agentgateway-upstream/agentgateway:2.0.0",
                    *AGENTGATEWAY_IMAGES_HOLD]
        # The hold for the switch of giantswarm/agent-platform#575, applied to BOTH
        # sides: modelServing.imageVerification is on by default now, so a serving
        # shape under Kyverno renders the image-verification policy that GOLDEN_REF's
        # defaults do not (both charts mirror the block). Dropped once GOLDEN_REF
        # carries the switch.
        hold_iv = ["--set", "modelServing.imageVerification.enabled=false"]
        # Applied to the GOLDEN render only (see the list's comment).
        golden_only = {meta: KLAUS_GATEWAY_DROPPED_KEYS_GOLDEN_ONLY, connectivity: []}
        shapes = [
            ("meta default", meta, [*hold_608, *METRIC_LABELS_HOLD, *hold_iv, *MUSTER_DASHBOARD_HOLD, *AGENTGATEWAY_MONITORING_HOLD, *MCP_KUBERNETES_MONITORING_HOLD, *KLAUS_GATEWAY_FLOOR_HOLD, *VM_MANAGER_MONITOR_HOLD, *KSERVE_MONITOR_HOLD, *KAGENT_CONTROLLER_RESOURCES_HOLD]),
            ("meta ci + engine off", meta, ["-f", f"{meta}/ci/ci-values.yaml", *ENGINE_OFF, *hold_608, *METRIC_LABELS_HOLD, *hold_iv, *MUSTER_DASHBOARD_HOLD, *AGENTGATEWAY_MONITORING_HOLD, *MCP_KUBERNETES_MONITORING_HOLD, *KLAUS_GATEWAY_FLOOR_HOLD, *VM_MANAGER_MONITOR_HOLD, *KSERVE_MONITOR_HOLD, *KAGENT_CONTROLLER_RESOURCES_HOLD]),
            ("connectivity default", connectivity, [*VM, *METRIC_LABELS_HOLD, *hold_iv, *AGENTGATEWAY_IMAGES_HOLD, *KAGENT_VPA_CAP_HOLD]),
            ("connectivity full", connectivity, [*CONN_FULL, *METRIC_LABELS_HOLD, *hold_iv, *AGENTGATEWAY_IMAGES_HOLD, *KAGENT_VPA_CAP_HOLD]),
            ("connectivity backstage", connectivity, [*CONN_BACKSTAGE, *METRIC_LABELS_HOLD, *hold_iv, *AGENTGATEWAY_IMAGES_HOLD, *KAGENT_VPA_CAP_HOLD]),
        ]
        for label, chart, flags in shapes:
            here = helm(chart, flags)
            there = helm(os.path.join(tree, chart),
                         [f.replace(f"{meta}/", f"{tree}/{meta}/") for f in flags] + golden_only[chart])
            here, there = hold_dashboards(here, there, chart == meta)
            here, there = hold_opus_5_5_price(here, there)
            here, there = hold_dataplane_buffer(here, there)
            if chart == meta:
                here, there = drop_new_roster_entries(here, there)
                here, there = hold_llmd_only(here, there)
                here, there = hold_hook_pods(here, there)
                here, there = hold_substrate_range(here, there)
                here, there = hold_otlp_endpoints(here, there)
            else:
                here, there = hold_dataplane_podmonitor(here, there)
                here, there = hold_dataplane_tracing(here, there)
            if here != there:
                import difflib
                excerpt = list(difflib.unified_diff(there.splitlines(), here.splitlines(), f"{ref}", "head", lineterm="", n=2))[:40]
                sys.exit(f"FAIL: the {label} render drifted from {ref}\n" + "\n".join(excerpt))
        ok(f"{len(shapes)} renders byte-identical to {ref}")
    finally:
        subprocess.run(["git", "worktree", "remove", "--force", tree], check=False)


def check_toggles(meta: str, connectivity: str) -> None:
    ci = ["-f", f"{meta}/ci/ci-values.yaml", *ENGINE_OFF]
    for name in ("muster", "dicebear"):
        off = helm(meta, [*ci, "--set", f"components.{name}.enabled=false"])
        docs = documents(off)
        if ("HelmRelease", name) in docs or ("OCIRepository", name) in docs:
            sys.exit(f"FAIL: components.{name}.enabled=false still rendered the {name} release")
        roster = f"\n      {name}:\n        enabled: false\n"
        if roster not in docs[("HelmRelease", "agent-platform-connectivity")]:
            sys.exit(f"FAIL: the roster forwarded to connectivity does not say {name}: enabled: false")
        ok(f"components.{name}.enabled=false: no release, the roster says so")

    on = helm(connectivity, CONN_FULL)
    off = helm(connectivity, [*CONN_FULL, "--set", "components.muster.enabled=false"])
    markers = ("app.kubernetes.io/name: muster", "muster-mcp-egress", "value: /mcp")
    for marker in markers:
        if marker not in on:
            sys.exit(f"FAIL: the full connectivity render lacks {marker!r} with muster on (the fixture is stale)")
        if marker in off:
            sys.exit(f"FAIL: connectivity still renders {marker!r} with components.muster.enabled=false")
    kinds = {kind for kind, _ in documents(on)} - {kind for kind, _ in documents(off)}
    if kinds != {"HTTPRoute"}:
        sys.exit(f"FAIL: muster off dropped {sorted(kinds)} from the connectivity render; expected the /mcp HTTPRoute only")
    ok("connectivity with muster off: no /mcp route, no muster egress policy, no rule selecting muster pods")

    on = helm(connectivity, CONN_BACKSTAGE)
    off = helm(connectivity, [*CONN_BACKSTAGE, "--set", "components.dicebear.enabled=false"])
    if "https://avatars.ci.example.com" not in on:
        sys.exit("FAIL: the portal's CSP lacks the avatars host with dicebear on")
    if "avatars." in off:
        sys.exit("FAIL: the portal's CSP still names the avatars host with components.dicebear.enabled=false")
    ok("connectivity with dicebear off: the avatars host leaves the portal's CSP")


def check_guards(meta: str) -> None:
    ci = ["-f", f"{meta}/ci/ci-values.yaml"]
    err = helm(meta, [*ci, *KNOB], expect_failure=True)
    if "cannot be combined with the bundled Flux engine" not in err:
        sys.exit(f"FAIL: the knob with the engine on failed for the wrong reason:\n{err}")
    ok("the target knob with the bundled engine on fails naming the engine")
    err = helm(meta, [*ci, *ENGINE_OFF, "--set", "gitops.target.bogus=1"], expect_failure=True)
    if "gitops" not in err or "bogus" not in err.lower() and "additional propert" not in err.lower():
        sys.exit(f"FAIL: gitops.target.bogus was not refused by the schema:\n{err}")
    ok("an unknown key under gitops.target is refused by the schema")


def check_hooks(meta: str) -> None:
    runtime = ["-f", f"{meta}/ci/test-slice-runtime-values.yaml"]
    jobs = lambda m: sum(1 for kind, _ in documents(m) if kind == "Job")
    if jobs(helm(meta, runtime)) == 0:
        sys.exit("FAIL: the runtime shape without the knob renders no hook Job (the kagent storage-version pair is expected)")
    with_knob = helm(meta, [*runtime, "-f", f"{meta}/ci/test-target-values.yaml"])
    if jobs(with_knob) or "kind: ServiceAccount" in with_knob:
        sys.exit("FAIL: a hook Job or its identity renders with the target knob; hooks run on the installation, not on the target")
    ok("no hook Job renders with the target knob; the storage-version pair still renders without it")


def check_slices(meta: str) -> None:
    ci = f"{meta}/ci"
    serving = helm(meta, ["-f", f"{ci}/test-slice-serving-values.yaml"])
    runtime = helm(meta, ["-f", f"{ci}/test-slice-runtime-values.yaml"])
    both = helm(meta, ["-f", f"{ci}/test-slice-serving-values.yaml", "-f", f"{ci}/test-slice-runtime-values.yaml"])
    s, r, b = releases(serving), releases(runtime), releases(both)
    if not (s and r) or s & r != {"agent-platform-connectivity"} or b != s | r:
        sys.exit(f"FAIL: the combined release is not the union of the slices: serving={sorted(s)} runtime={sorted(r)} both={sorted(b)}")
    if "muster" in b or "dicebear" in b or "valkey" in b or "agentgateway" in b or "model-manager" in b:
        sys.exit(f"FAIL: a slice beside the platform's release renders a component the platform's release owns: {sorted(b)}")
    for kind_name, doc in documents(serving).items():
        if kind_name[1] == "agent-platform-connectivity":
            continue
        if documents(both).get(kind_name) != doc:
            sys.exit(f"FAIL: {kind_name} of the serving slice changed when the runtime slice was switched on (not an in-place upgrade)")
    ok(f"serving ({len(s)}) + runtime ({len(r)}) = one release of {len(b)} HelmReleases; the first slice's documents unchanged when the second is switched on")

    target = helm(meta, ["-f", f"{ci}/test-slice-serving-values.yaml", "-f", f"{ci}/test-target-values.yaml"])
    t = releases(target)
    if t != s | {"agentgateway"}:
        sys.exit(f"FAIL: the serving slice with the target knob should add exactly agentgateway: {sorted(t)}")
    if target.count("  kubeConfig:") != len(t) or "      name: wc01-kubeconfig" not in target:
        sys.exit("FAIL: not every HelmRelease of the targeted slice carries the kubeConfig")
    ok("agentgateway follows the target: off beside the platform's release, on with the knob; every targeted HelmRelease carries the kubeConfig")


def main(meta: str, connectivity: str) -> int:
    check_knob(meta)
    check_golden(meta, connectivity)
    check_toggles(meta, connectivity)
    check_guards(meta)
    check_hooks(meta)
    check_slices(meta)
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(f"usage: {sys.argv[0]} <meta chart dir> <connectivity chart dir>")
    sys.exit(main(sys.argv[1], sys.argv[2]))
