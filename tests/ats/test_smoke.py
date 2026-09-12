"""Kind smoke of the agent-platform meta chart: the quick start on a bare cluster.

On every PR, on the ATS kind cluster, in this order (pytest runs the tests of a
module top to bottom; each one builds on the state the previous left):

  1. prerequisites — the Gateway API CRDs (the one prerequisite the chart does
     not bring), the lab Dex (tests/ats/lab-dex.yaml: static users, the
     platform's identity Secret, a self-signed CA, the CoreDNS rewrite) and an
     in-cluster registry the candidate archive is pushed to;
  2. `helm install --wait` of the candidate with the quick start's shape —
     tests/test-values.yaml (muster, dicebear, connectivity; the bundled Flux
     engine on) + values-kagent.yaml (the kagent runtime) + values-round-trips.yaml
     (the lab Dex as global.identity, muster's OAuth server on, agent-manager) —
     and self-management ON against the in-cluster registry: the chart's own
     OCIRepository follows the registry at the candidate's version (the trap
     the issue names, a self HelmRelease replacing an unreleased chart with the
     published one, does not arise when the registry holds the candidate);
  3. what the install promises: the release deployed, the FluxInstance Ready at
     a Flux 2.x, every component HelmRelease Ready as the tenant identity
     agent-platform-flux, the engine objects, the operator managing the Flux CRDs;
  4. adoption: the bundled helm-controller takes the CLI's release over —
     `helm history` shows the install and one adoption revision;
  5. the auth round trip: an unauthenticated /mcp is a 401 with the RFC 9728
     discovery chain; a lab Dex user reaches /mcp with a token from the OAuth
     password grant (a trusted audience) and with muster's own login flow (RFC 7591
     registration, authorization code + PKCE, the Dex login form);
  6. the agent round trips on kagent API v2: Agent Substrate, installed by the
     chart under test, has its WorkerPool's gVisor worker Ready; a declarative
     AgentTemplate labelled for the platform Harness, against the chart's
     default ModelConfig (a placeholder provider key), reaches Ready on that
     Harness (status.harnesses[]); and — the write path the standalone's smoke
     never had — agent-manager's create_agent, called through muster as the Dex
     user with the forwarded token, writes an OCIRepository + HelmRelease of the
     agent chart 1.x into the kagent namespace, the HelmRelease runs as
     kagent-flux and reaches Ready, the AgentTemplate reaches Ready on the
     Harness and the agent's RemoteMCPServer (the toolset carrier) is Accepted;
     and the drift correction: the platform Harness deleted by hand (what the
     4.8.0 upgrade does to a consumer whose pinned connectivity chart skipped
     4.7.19's keep) is back on the kagent release's next reconcile — a
     requested reconcile stands in for the 10-minute interval; no forceAt, no
     Helm revision — and both templates return to Ready on it;
  7. the fixpoint: two self-management intervals after the adoption `helm
     history` is unchanged and the values Secret equals the values used;
  8. the Helm CLI is day-0 only: `helm upgrade` is refused by the admission
     policy with its message in Helm's own output, no revision written;
  9. `helm uninstall --wait`: the ordered teardown returns clean within budget,
     no Flux CRD left, the four operator CRDs remaining, no controller, no hook
     Job, no release in any state; the kagent-crds release is uninstalled and
     the kagent CRDs survive it, with the agents' AgentTemplates and
     RemoteMCPServer (the line's keep policy), the substrate-crds release is
     uninstalled and the three ate.dev CRDs survive it (the Substrate line's
     keep policy), the platform Harness (the kagent release's keep policy) —
     and nothing else does: no ModelConfig, WorkerPool, worker or controller
     in the kept kagent namespace, no SandboxConfig, Substrate's control plane
     gone.

The lab shape (`gitops.self.enabled: false`, what agentlab installs) renders
none of the self-management objects; that shape is asserted offline by
`make verify-self`. A second `helm install` on the same cluster is the
functional scenario's job (test_own_flux.py installs the chart again, through
the cluster's own Flux).

The CI job's kind cluster carries the feature gates Substrate needs
(.ats/kind-config.yaml); values-kagent.yaml sizes Substrate and the kagent
runtime for the executor (tests/ats/README.md).
"""

import base64
import logging
import time
from pathlib import Path
from typing import Any, Dict, List

import pytest
import yaml

from conftest import (
    AGENT_CHART_SEMVER,
    AGENT_CHART_URL,
    ATE_NAMESPACE,
    CONNECTIVITY,
    CROSS_CLIENT_AUDIENCE,
    DEX_USER,
    FLUX_CRD_SUFFIX,
    HARNESS,
    HARNESS_LABEL,
    KAGENT_FLUX_SA,
    KAGENT_NAMESPACE,
    KEPT_CRDS,
    MODEL_CONFIG,
    MUSTER_BASE_URL,
    MUSTER_BASE_URL_SETS,
    NAMESPACE,
    OPERATOR_CRDS,
    RELEASE,
    SANDBOX_CONFIG,
    SELF_INTERVAL_S,
    SELF_POLICY,
    SMOKE_VALUES,
    TENANT_SA,
    TIMINGS,
    TOOLSET,
    UNINSTALL_BUDGET_S,
    VALUES_SECRET,
    WORKER_POOL,
    assert_substrate_trust_chain,
    PODCERT_SIGNERS,
    PODCERT_NAMESPACE,
    Helm,
    Kube,
    MusterSession,
    PortForward,
    apply_placeholder_provider_secret,
    assert_kept_crds,
    assert_remote_mcp_server,
    condition,
    connectivity_sets,
    dex_password_grant,
    dump_agents,
    is_ready,
    jwt_claims,
    load_values,
    login_through_muster,
    self_management_sets,
    template_state,
    unauthenticated_mcp_challenge,
    wait_for,
    wait_for_muster_healthy,
    substrate_trust_bundles,
    wait_for_substrate,
    wait_for_template_ready,
)

logger = logging.getLogger(__name__)

# The component HelmReleases the smoke values turn on: the quick start's three,
# kagent with its CRD chart, Agent Substrate with its CRD chart (both follow
# components.kagent) and agent-manager.
COMPONENTS = ("muster", "dicebear", "agent-platform-connectivity", "kagent", "kagent-crds", "substrate", "substrate-crds", "agent-manager")
# The Deployments `helm install --wait` leaves running in the release namespace
# (the kagent controller runs in the kagent namespace and is waited for separately).
CORE_DEPLOYMENTS = ("flux-operator", "source-controller", "helm-controller", "muster", "agent-manager")
DECLARATIVE_AGENT = "ats-smoke-agent"
MANAGED_AGENT = "ats-managed-agent"
MANAGED_AGENT_DISPLAY_NAME = "ATS managed agent"
AGENT_CHART_REPOSITORY = "agent"  # the shared per-namespace OCIRepository agent-manager writes
POLICY_MESSAGE = f"release {RELEASE} manages itself through its bundled Flux"


class State:
    """What one test hands to the next (module-scoped, in test order)."""

    adopted_at: float = 0.0
    history_after_adoption: List[Dict[str, Any]] = []
    dex_token: str = ""


STATE = State()


def dump_platform(kube: Kube) -> None:
    kube.dump([
        f"-n {NAMESPACE} get fluxinstance flux -o yaml",
        f"-n {NAMESPACE} get helmreleases.helm.toolkit.fluxcd.io -o wide",
        f"-n {NAMESPACE} get ocirepositories.source.toolkit.fluxcd.io -o wide",
        f"-n {NAMESPACE} get pods -o wide",
        f"-n {KAGENT_NAMESPACE} get pods -o wide",
        f"-n {ATE_NAMESPACE} get pods -o wide",
        f"-n {NAMESPACE} get events --sort-by=.lastTimestamp",
        f"-n {NAMESPACE} logs deployment/helm-controller --tail=60",
        # The connectivity release's hook Jobs (the Substrate bootstrap, the
        # databases hook): a failed pre-install is theirs to explain.
        f"-n {NAMESPACE} logs -l app.kubernetes.io/component=hooks --all-containers --prefix --tail=40",
        f"-n {NAMESPACE} logs -l job-name=agent-platform-connectivity-substrate-bootstrap --all-containers --prefix --tail=40",
    ])


def dump_auth(kube: Kube) -> None:
    kube.dump([f"-n {NAMESPACE} logs deployment/muster --tail=120", f"-n {NAMESPACE} logs deployment/lab-dex --tail=60",
               f"-n {NAMESPACE} logs deployment/agent-manager --tail=60"])


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def smoke_sets(candidate_version: str) -> List[str]:
    """The --set values of the smoke install: self-management against the
    in-cluster registry, the connectivity chart of this checkout from the same
    registry (and the muster base URL when the local port moved)."""
    return self_management_sets(candidate_version) + connectivity_sets(candidate_version) + MUSTER_BASE_URL_SETS


@pytest.fixture(scope="module")
def app_deployment(kube: Kube, helm: Helm, prerequisites: None, chart_archive: Path, pushed_chart: str, smoke_sets: List[str]) -> float:
    """`helm install --wait` of the candidate, the quick start's way. Returns the seconds it took."""
    assert not kube.flux_crds(), "the smoke needs a cluster without Flux CRDs; another Flux is present"
    try:
        elapsed = helm.install(str(chart_archive), SMOKE_VALUES, smoke_sets)
    except AssertionError:
        dump_platform(kube)
        raise
    TIMINGS.record(f"helm install --wait (engine, muster+OAuth, {', '.join(c for c in COMPONENTS if c != 'muster')}, self on)", elapsed)
    return elapsed


@pytest.fixture(scope="module")
def muster(muster_forward: PortForward, app_deployment: float) -> PortForward:
    """muster reachable and healthy (its OAuth server discovers the lab Dex)."""
    started = time.monotonic()
    wait_for_muster_healthy(MUSTER_BASE_URL)
    TIMINGS.record("muster /health ok after the install", time.monotonic() - started)
    return muster_forward


@pytest.fixture(scope="module")
def dex(dex_forward: PortForward, dex_ca: str, app_deployment: float) -> str:
    """The lab Dex reachable from the test; returns the CA path."""
    return dex_ca


@pytest.fixture(scope="module")
def kagent_controller(kube: Kube, app_deployment: float) -> None:
    """The kagent controller running (its HelmRelease is Ready before the pod is)."""
    started = time.monotonic()
    kube.wait_deployment(KAGENT_NAMESPACE, "kagent-controller", timeout=600)
    wait_for(f"ModelConfig {MODEL_CONFIG}", lambda: kube.get("modelconfigs.kagent.dev", MODEL_CONFIG, namespace=KAGENT_NAMESPACE), 120)
    apply_placeholder_provider_secret(kube)
    TIMINGS.record("kagent controller Ready after the install returned", time.monotonic() - started)


@pytest.fixture(scope="module")
def substrate(kube: Kube, app_deployment: float) -> Dict[str, Any]:
    """Agent Substrate from the chart under test, ready to run actors: the
    WorkerPool's worker Ready, the platform Harness rendered. Returns the WorkerPool."""
    try:
        return wait_for_substrate(kube)
    except AssertionError:
        dump_agents(kube)
        raise


# ---------------------------------------------------------------------------
# 3. what the install promises
# ---------------------------------------------------------------------------


def assert_platform_running(kube: Kube, helm: Helm) -> None:
    try:
        status = helm.status()
        assert status["info"]["status"] == "deployed", status["info"]
        fi = kube.get("fluxinstance", "flux", namespace=NAMESPACE)
        assert is_ready(fi), f"FluxInstance not Ready: {condition(fi)}"
        revision = fi["status"].get("lastAppliedRevision", "")
        assert revision.startswith("v2."), f"FluxInstance revision {revision!r} is not a Flux 2.x"
        hrs = {hr["metadata"]["name"]: hr for hr in kube.items("helmreleases.helm.toolkit.fluxcd.io", namespace=NAMESPACE)}
        platform = {n: hr for n, hr in hrs.items() if n != RELEASE}
        assert set(platform) == set(COMPONENTS), f"HelmReleases differ from the smoke values: {sorted(platform)}"
        for name, hr in platform.items():
            assert is_ready(hr), f"HelmRelease {name} not Ready: {condition(hr)}"
            assert hr["spec"].get("serviceAccountName") == TENANT_SA, f"HelmRelease {name} does not run as {TENANT_SA}"
        assert platform["kagent"]["spec"]["targetNamespace"] == NAMESPACE, "the kagent HelmRelease must target the platform namespace (the pre-install hook creates the kagent namespace)"
        for name in CORE_DEPLOYMENTS:
            assert kube.deployment_ready(NAMESPACE, name), f"Deployment {name} is not ready"
        logger.info("deployed; FluxInstance Ready at %s; %d component HelmReleases Ready as %s", revision, len(platform), TENANT_SA)
    except AssertionError:
        dump_platform(kube)
        raise


@pytest.mark.smoke
def test_install_reaches_deployed_with_the_platform_ready(kube: Kube, helm: Helm, app_deployment: float) -> None:
    assert_platform_running(kube, helm)
    history = helm.history()
    assert history and history[0]["status"] in ("deployed", "superseded"), history
    logger.info("install took %.0f s; helm history has %d revision(s)", app_deployment, len(history))


@pytest.mark.smoke
def test_engine_objects(kube: Kube, app_deployment: float) -> None:
    crds = kube.crd_names()
    assert len(kube.flux_crds()) == 7, kube.flux_crds()
    assert OPERATOR_CRDS <= set(crds), sorted(OPERATOR_CRDS - set(crds))
    # The operator adopted the CRDs the chart brought (Helm installed them, the
    # operator manages them from here on — within its first reconcile).
    wait_for("flux-operator among the Flux CRDs' field managers",
             lambda: "flux-operator" in kube.managers("crd", "helmreleases.helm.toolkit.fluxcd.io"), 120, interval=3)
    assert kube.get("serviceaccount", TENANT_SA, namespace=NAMESPACE), f"ServiceAccount {TENANT_SA} missing"
    assert kube.get("clusterrolebinding", TENANT_SA), f"ClusterRoleBinding {TENANT_SA} missing"
    assert kube.get("namespace", KAGENT_NAMESPACE), "the pre-install hook did not create the kagent namespace"
    # No hook object lingers after a successful install (Helm removes them once
    # every hook of the event succeeded, moments after the install returns; the
    # detached resumer Job is not a hook and stays for an hour to be read).

    def hook_jobs() -> List[str]:
        return [j["metadata"]["name"] for j in kube.items("jobs", namespace=NAMESPACE) if "helm.sh/hook" in (j["metadata"].get("annotations") or {})]

    wait_for("the install's hook Jobs removed (hook-succeeded)", lambda: not hook_jobs(), 120, interval=3)


# ---------------------------------------------------------------------------
# 4. adoption
# ---------------------------------------------------------------------------


@pytest.mark.smoke
def test_self_management_adopts_the_release(kube: Kube, helm: Helm, candidate_version: str, app_deployment: float) -> None:
    started = time.monotonic()
    try:
        oci = wait_for(f"self OCIRepository {RELEASE} Ready", lambda: is_ready(kube.get("ocirepositories.source.toolkit.fluxcd.io", RELEASE, namespace=NAMESPACE))
                       and kube.get("ocirepositories.source.toolkit.fluxcd.io", RELEASE, namespace=NAMESPACE), 300)
        artifact_rev = (oci.get("status", {}).get("artifact") or {}).get("revision", "")
        assert artifact_rev.startswith(candidate_version), f"the self OCIRepository resolved {artifact_rev!r}, not the candidate {candidate_version}"

        def adopted() -> Any:
            hr = kube.get("helmreleases.helm.toolkit.fluxcd.io", RELEASE, namespace=NAMESPACE)
            if not hr or hr["spec"].get("suspend"):
                return False
            history = helm.history()
            # the install (superseded by the adoption) and the adoption itself
            return hr if is_ready(hr) and len(history) == 2 and history[-1]["status"] == "deployed" else False

        hr = wait_for("the self HelmRelease resumed, Ready, and the adoption revision deployed", adopted, 600)
    except AssertionError:
        dump_platform(kube)
        kube.dump([f"-n {NAMESPACE} get jobs -o wide", f"-n {NAMESPACE} logs job/{RELEASE}-self-resume --tail=40"])
        raise
    STATE.adopted_at = time.monotonic()
    STATE.history_after_adoption = helm.history()
    TIMINGS.record("adoption (self HelmRelease Ready, revision 2 deployed) after the install returned", STATE.adopted_at - started)
    assert hr["spec"]["serviceAccountName"] == TENANT_SA
    assert hr["status"].get("lastAttemptedRevision", "").startswith(candidate_version), hr["status"]
    assert [h["revision"] for h in STATE.history_after_adoption] == [1, 2], STATE.history_after_adoption
    assert kube.get("validatingadmissionpolicies.admissionregistration.k8s.io", SELF_POLICY), "the admission policy is missing"
    assert kube.get("secret", VALUES_SECRET, namespace=NAMESPACE), "the values Secret is missing"
    # The platform is still whole after the adoption upgrade.
    assert_platform_running(kube, helm)
    logger.info("adopted: %s", [(h["revision"], h["status"], h["description"]) for h in STATE.history_after_adoption])


# ---------------------------------------------------------------------------
# 5. the auth round trip
# ---------------------------------------------------------------------------


@pytest.mark.smoke
@pytest.mark.flaky(reruns=2, reruns_delay=20)
def test_unauthenticated_mcp_gets_401_with_discovery_chain(kube: Kube, muster: PortForward) -> None:
    try:
        meta = unauthenticated_mcp_challenge(MUSTER_BASE_URL)
    except AssertionError:
        dump_auth(kube)
        raise
    logger.info("401 chain ok: AS metadata carries %s", sorted(k for k in meta if k.endswith("_endpoint")))


@pytest.mark.smoke
@pytest.mark.flaky(reruns=2, reruns_delay=20)
def test_dex_user_reaches_mcp_with_a_password_grant(kube: Kube, muster: PortForward, dex: str) -> None:
    """The lab Dex's OAuth password grant issues an ID token for the platform
    client (a trusted audience of muster) carrying the cross-client audience
    agent-manager requires; muster accepts it as a bearer and lists its tools."""
    started = time.monotonic()
    try:
        token = dex_password_grant(dex)
        claims = jwt_claims(token)
        assert claims.get("email") == DEX_USER, claims
        aud = claims.get("aud") if isinstance(claims.get("aud"), list) else [claims.get("aud")]
        assert CROSS_CLIENT_AUDIENCE in aud, f"the token lacks the {CROSS_CLIENT_AUDIENCE} audience: {aud}"
        session = MusterSession(MUSTER_BASE_URL, token, "ats-password-grant").initialize()
        tools = session.list_tools()
        assert {"list_tools", "call_tool"} <= set(tools), f"muster's meta-tools missing from tools/list: {tools}"
        aggregated = session.aggregated_tools()
        assert any(t.startswith("core_") for t in aggregated), f"no core_ tool aggregated for the user: {aggregated[:20]}"
    except AssertionError:
        dump_auth(kube)
        raise
    STATE.dex_token = token
    TIMINGS.record("auth round trip: password grant -> /mcp initialize -> tools/list -> list_tools", time.monotonic() - started)
    logger.info("Dex user %s reached /mcp: %d meta-tools, %d aggregated tools", DEX_USER, len(tools), len(aggregated))


@pytest.mark.smoke
@pytest.mark.flaky(reruns=2, reruns_delay=20)
def test_static_user_login_through_muster_reaches_mcp(kube: Kube, muster: PortForward, dex: str) -> None:
    """The full muster login — dynamic client registration, authorization code
    with PKCE, the Dex login form — headless; the access token reaches /mcp."""
    started = time.monotonic()
    try:
        token = login_through_muster(MUSTER_BASE_URL, dex)
        session = MusterSession(MUSTER_BASE_URL, token, "ats-login").initialize()
        assert "call_tool" in session.list_tools()
        assert any(t.startswith("core_") for t in session.aggregated_tools())
    except AssertionError:
        dump_auth(kube)
        raise
    TIMINGS.record("auth round trip: muster login flow (DCR, PKCE, Dex form) -> /mcp", time.monotonic() - started)


# ---------------------------------------------------------------------------
# 6. the agent round trips
# ---------------------------------------------------------------------------


@pytest.mark.smoke
def test_substrate_runs_a_worker_for_the_platform_harness(kube: Kube, substrate: Dict[str, Any]) -> None:
    """Agent Substrate from the chart under test on the CI job's kind cluster:
    the cluster serves certificates.k8s.io/v1beta1 with the feature gates on
    (.ats/kind-config.yaml — Substrate projects pod identities and trust bundles
    through PodCertificateRequest and ClusterTrustBundle), the control plane runs
    in ate-system, the WorkerPool of the kagent namespace has its gVisor
    worker(s) Running and Ready, and the platform Harness points at it."""
    assert "certificates.k8s.io/v1beta1" in kube.text(["api-versions"]).split(), "the cluster does not serve certificates.k8s.io/v1beta1 (the kind config's runtimeConfig)"
    resources = {line.split()[0] for line in kube.text(["api-resources", "--api-group=certificates.k8s.io", "--no-headers"]).splitlines() if line.strip()}
    assert {"podcertificaterequests", "clustertrustbundles"} <= resources, sorted(resources)
    workers = kube.items("pods", "-l", f"ate.dev/worker-pool={WORKER_POOL}", namespace=KAGENT_NAMESPACE)
    phases = {p["metadata"]["name"]: p["status"].get("phase") for p in workers}
    assert len(workers) == substrate["spec"]["replicas"] and all(phase == "Running" for phase in phases.values()), phases
    logger.info("Substrate worker(s) Running on %s: %s", substrate["spec"].get("workerImage"), sorted(phases))


@pytest.mark.smoke
def test_declarative_agent_reaches_ready(kube: Kube, kagent_controller: None, substrate: Dict[str, Any]) -> None:
    """A minimal declarative AgentTemplate labelled for the platform Harness,
    against the chart's default ModelConfig (a placeholder provider key): Ready
    on that Harness means the Harness admitted the template and Substrate booted
    the actor's golden snapshot on the WorkerPool — not that a model call
    succeeded."""
    started = time.monotonic()
    kube.apply({"apiVersion": "kagent.dev/v1alpha3", "kind": "AgentTemplate",
                "metadata": {"name": DECLARATIVE_AGENT, "namespace": KAGENT_NAMESPACE, "labels": {HARNESS_LABEL: HARNESS}},
                "spec": {"description": "ATS smoke agent (lab only)", "modelConfig": {"name": MODEL_CONFIG},
                         "systemPrompt": "You are the ATS smoke agent."}})
    try:
        template = wait_for_template_ready(kube, DECLARATIVE_AGENT)
    except AssertionError:
        dump_agents(kube)
        raise
    TIMINGS.record(f"declarative AgentTemplate Ready on Harness {HARNESS}", time.monotonic() - started)
    logger.info("AgentTemplate %s on Harness %s: %s", DECLARATIVE_AGENT, HARNESS, template_state(template))


@pytest.mark.smoke
def test_agent_manager_create_agent_reaches_a_ready_helmrelease(kube: Kube, muster: PortForward, dex: str, kagent_controller: None, substrate: Dict[str, Any]) -> None:
    """agent-manager's create_agent through muster, as the Dex user: muster
    forwards the bearer to agent-manager (MCPServer auth.forwardToken; the
    token carries the required cross-client audience), agent-manager validates
    it against the lab Dex and writes the agent's HelmRelease of the Generic
    chart 1.x (and the shared OCIRepository of the chart) into the kagent
    namespace with its own ServiceAccount; the bundled helm-controller executes
    the HelmRelease as kagent-flux, the chart renders the AgentTemplate labelled
    for the platform Harness and the agent's RemoteMCPServer, the Harness admits
    the template and Substrate boots it."""
    started = time.monotonic()
    token = STATE.dex_token or dex_password_grant(dex)
    session = MusterSession(MUSTER_BASE_URL, token, "ats-agent-manager").initialize()
    try:
        # muster connects to a forwardToken server per session, after the first
        # authenticated request; the tools appear once the SSO connection is up.
        wait_for("agent-manager's tools in muster's aggregated tool list", lambda: "x_agent-manager_create_agent" in session.aggregated_tools(), 120, interval=3)
        info = session.call_server_json("x_agent-manager_get_info")
        logger.info("agent-manager get_info: version %s, chart %s %s, harness %s, muster %s, apiVersions %s",
                    info.get("version"), info.get("chart", {}).get("ociUrl"), info.get("chart", {}).get("semver"), info.get("harness"), info.get("muster"), info.get("apiVersions"))
        assert info.get("flux", {}).get("serviceAccountName") == KAGENT_FLUX_SA, info.get("flux")
        assert info.get("chart", {}).get("ociUrl") == AGENT_CHART_URL, info.get("chart")
        assert info.get("chart", {}).get("semver") == AGENT_CHART_SEMVER, info.get("chart")
        assert info.get("harness", {}).get("name") == HARNESS, info.get("harness")
        # Clean up an earlier attempt (a flaky rerun) so create_agent does not refuse a duplicate.
        kube.delete("helmreleases.helm.toolkit.fluxcd.io", MANAGED_AGENT, namespace=KAGENT_NAMESPACE, timeout="2m")
        result = session.call_server_json("x_agent-manager_create_agent", {
            "name": MANAGED_AGENT, "modelConfig": MODEL_CONFIG, "displayName": MANAGED_AGENT_DISPLAY_NAME,
            "description": "created through agent-manager by the ATS smoke (lab only)",
            "systemMessage": "You are the ATS managed agent.", "toolset": TOOLSET})
        logger.info("create_agent returned: %s", str(result)[:400])
        assert result.get("requestedBy") == DEX_USER, f"the write is not attributed to the Dex user: {result.get('requestedBy')!r}"
    except AssertionError:
        dump_auth(kube)
        dump_agents(kube)
        raise
    try:
        oci = wait_for(f"OCIRepository {AGENT_CHART_REPOSITORY} in {KAGENT_NAMESPACE} Ready",
                       lambda: is_ready(kube.get("ocirepositories.source.toolkit.fluxcd.io", AGENT_CHART_REPOSITORY, namespace=KAGENT_NAMESPACE))
                       and kube.get("ocirepositories.source.toolkit.fluxcd.io", AGENT_CHART_REPOSITORY, namespace=KAGENT_NAMESPACE), 300)
        assert oci["spec"]["url"] == AGENT_CHART_URL, oci["spec"]
        assert oci["spec"].get("ref", {}).get("semver") == AGENT_CHART_SEMVER, f"the shared OCIRepository does not track the 1.x chart: {oci['spec'].get('ref')}"
        hr = kube.get("helmreleases.helm.toolkit.fluxcd.io", MANAGED_AGENT, namespace=KAGENT_NAMESPACE)
        assert hr, f"agent-manager wrote no HelmRelease {MANAGED_AGENT}"
        assert hr["spec"].get("serviceAccountName") == KAGENT_FLUX_SA, f"the agent HelmRelease does not run as {KAGENT_FLUX_SA}: {hr['spec'].get('serviceAccountName')!r}"
        assert hr["spec"]["chartRef"]["name"] == AGENT_CHART_REPOSITORY, hr["spec"]["chartRef"]
        values = hr["spec"].get("values", {})
        assert values.get("toolset") == TOOLSET, values.get("toolset")
        assert values.get("agent", {}).get("harness") == HARNESS, f"agent-manager composed no agent.harness for the platform Harness: {values.get('agent')}"
        wait_for(f"HelmRelease {MANAGED_AGENT} Ready", lambda: is_ready(kube.get("helmreleases.helm.toolkit.fluxcd.io", MANAGED_AGENT, namespace=KAGENT_NAMESPACE)), 600)
        template = wait_for_template_ready(kube, MANAGED_AGENT)
        assert (template["metadata"].get("labels") or {}).get(HARNESS_LABEL) == HARNESS, template["metadata"].get("labels")
        assert (template["metadata"].get("annotations") or {}).get("ui.giantswarm.io/display-name") == MANAGED_AGENT_DISPLAY_NAME, template["metadata"].get("annotations")
        assert any(((t.get("mcp") or {}).get("server") or {}).get("name") == MANAGED_AGENT for t in template["spec"].get("tools", []) or []), \
            f"the template does not bind the agent's RemoteMCPServer: {template['spec'].get('tools')}"
        assert_remote_mcp_server(kube, MANAGED_AGENT)

        def status_ready() -> Any:
            status = session.call_server_json("x_agent-manager_get_agent_status", {"name": MANAGED_AGENT})
            return status if status.get("verdict") == "ready" else False

        status = wait_for("agent-manager get_agent_status verdict ready", status_ready, 120, interval=5)
        listed = session.call_server_json("x_agent-manager_list_agents")
        names = {a.get("name") for a in (listed.get("agents") if isinstance(listed, dict) else listed) or []}
        assert MANAGED_AGENT in names, f"list_agents does not list {MANAGED_AGENT}: {names}"
    except AssertionError:
        dump_agents(kube)
        raise
    TIMINGS.record(f"agent-manager create_agent -> HelmRelease Ready -> AgentTemplate Ready on Harness {HARNESS} + RemoteMCPServer Accepted", time.monotonic() - started)
    logger.info("agent-manager wrote %s (as %s, requested by %s); status verdict %s: %s", MANAGED_AGENT, KAGENT_FLUX_SA, result.get("requestedBy"), status.get("verdict"), status.get("summary"))


# ---------------------------------------------------------------------------
# 6b. the drift correction
# ---------------------------------------------------------------------------


@pytest.mark.smoke
def test_deleted_platform_harness_comes_back_on_the_next_reconcile(kube: Kube, kagent_controller: None, substrate: Dict[str, Any]) -> None:
    """The kagent release detects and corrects drift (spec.driftDetection.mode:
    enabled, giantswarm/agent-platform#409): the platform Harness deleted by
    hand — what the 4.8.0 upgrade does to a consumer whose exactly pinned
    connectivity chart skipped 4.7.19's keep — is back on the release's next
    reconcile, as a server-side apply of the release manifest: no Helm revision,
    no `reconcile.fluxcd.io/forceAt`; both templates it admits return to Ready.
    `reconcile.fluxcd.io/requestedAt` stands in for the interval (10 minutes,
    the chart's default) — a plain reconcile, the code path the interval takes,
    which without drift detection logs "release in-sync with desired state" and
    recreates nothing (600 s observed in the lab)."""
    before = kube.get("harnesses.kagent.dev", HARNESS, namespace=KAGENT_NAMESPACE)
    assert before, f"no Harness {HARNESS} in {KAGENT_NAMESPACE} to delete"
    hr = kube.get("helmreleases.helm.toolkit.fluxcd.io", "kagent", namespace=NAMESPACE)
    assert (hr["spec"].get("driftDetection") or {}).get("mode") == "enabled", f"the kagent HelmRelease carries no spec.driftDetection.mode: enabled: {hr['spec'].get('driftDetection')}"
    revision = hr["status"]["history"][0]["version"]
    started = time.monotonic()
    kube.delete("harnesses.kagent.dev", HARNESS, namespace=KAGENT_NAMESPACE)
    assert kube.get("harnesses.kagent.dev", HARNESS, namespace=KAGENT_NAMESPACE) is None, "the Harness survived its delete"
    stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    kube.cmd(["-n", NAMESPACE, "annotate", "helmreleases.helm.toolkit.fluxcd.io", "kagent", f"reconcile.fluxcd.io/requestedAt={stamp}", "--overwrite"])

    def recreated() -> Any:
        harness = kube.get("harnesses.kagent.dev", HARNESS, namespace=KAGENT_NAMESPACE)
        return harness if harness and harness["metadata"]["uid"] != before["metadata"]["uid"] else False

    try:
        harness = wait_for(f"Harness {HARNESS} recreated by the kagent release's reconcile", recreated, 180)
        TIMINGS.record(f"deleted Harness {HARNESS} back (drift correction on a requested reconcile, no forceAt)", time.monotonic() - started)
        annotations = harness["metadata"].get("annotations") or {}
        assert annotations.get("meta.helm.sh/release-name") == "kagent", f"the recreated Harness is not the kagent release's: {annotations}"
        assert annotations.get("helm.sh/resource-policy") == "keep", annotations
        assert harness["spec"] == before["spec"], f"the recreated Harness differs from the deleted one:\n{harness['spec']}\n{before['spec']}"
        for name in (DECLARATIVE_AGENT, MANAGED_AGENT):
            wait_for_template_ready(kube, name, timeout=300)
        TIMINGS.record(f"deleted Harness {HARNESS} back and both AgentTemplates Ready on it again", time.monotonic() - started)
    except AssertionError:
        dump_agents(kube)
        kube.dump([f"-n {NAMESPACE} get helmreleases.helm.toolkit.fluxcd.io kagent -o yaml",
                   f"-n {NAMESPACE} get events --field-selector involvedObject.name=kagent --sort-by=.lastTimestamp",
                   f"-n {NAMESPACE} logs deployment/helm-controller --tail=80"])
        raise
    hr = kube.get("helmreleases.helm.toolkit.fluxcd.io", "kagent", namespace=NAMESPACE)
    assert is_ready(hr), f"the kagent HelmRelease is not Ready after the correction: {condition(hr)}"
    assert hr["status"]["history"][0]["version"] == revision, f"the correction wrote a Helm revision ({revision} -> {hr['status']['history'][0]['version']}); a drift correction is a server-side apply, not an upgrade"
    logger.info("Harness %s deleted and back (uid %s -> %s) on a plain reconcile of the kagent release, Helm revision %s unchanged; templates %s Ready again",
                HARNESS, before["metadata"]["uid"], harness["metadata"]["uid"], revision, (DECLARATIVE_AGENT, MANAGED_AGENT))


# ---------------------------------------------------------------------------
# 7. the fixpoint and the values Secret; 8. the refused CLI
# ---------------------------------------------------------------------------


@pytest.mark.smoke
def test_self_management_fixpoint_and_values_secret(kube: Kube, helm: Helm, smoke_sets: List[str], app_deployment: float) -> None:
    """Two intervals after the adoption nothing moved: the same two revisions,
    the self HelmRelease Ready, and the values Secret holds exactly the
    user-supplied values of the install."""
    assert STATE.adopted_at, "the adoption test did not run"
    remaining = STATE.adopted_at + 2 * SELF_INTERVAL_S - time.monotonic()
    if remaining > 0:
        logger.info("waiting %.0f s more for two self-management intervals to pass", remaining)
        time.sleep(remaining)
    history = helm.history()
    assert [(h["revision"], h["status"]) for h in history] == [(h["revision"], h["status"]) for h in STATE.history_after_adoption], history
    hr = kube.get("helmreleases.helm.toolkit.fluxcd.io", RELEASE, namespace=NAMESPACE)
    assert is_ready(hr) and not hr["spec"].get("suspend"), condition(hr)
    secret = kube.get("secret", VALUES_SECRET, namespace=NAMESPACE)
    assert secret and secret["metadata"]["labels"].get("reconcile.fluxcd.io/watch") == "Enabled", secret and secret["metadata"]
    stored = yaml.safe_load(base64.b64decode(secret["data"]["values.yaml"])) or {}
    expected = load_values(SMOKE_VALUES, smoke_sets)
    assert stored == expected, f"values Secret differs from the install's values:\n{yaml.safe_dump(stored)}\n---\n{yaml.safe_dump(expected)}"
    assert helm.get_values() == expected, "helm get values differs from the install's values"
    logger.info("fixpoint held over two intervals: %s; values Secret == install values (%d top-level keys)", [(h["revision"], h["status"]) for h in history], len(expected))


@pytest.mark.smoke
def test_cli_upgrade_is_refused(kube: Kube, helm: Helm, chart_archive: Path, smoke_sets: List[str], app_deployment: float) -> None:
    started = time.monotonic()
    r = helm.upgrade(str(chart_archive), SMOKE_VALUES, smoke_sets)
    elapsed = time.monotonic() - started
    assert r.returncode != 0, f"helm upgrade succeeded; the Helm CLI must be day-0 only:\n{r.stdout}"
    assert POLICY_MESSAGE in r.stderr and SELF_POLICY in r.stderr, f"helm upgrade failed for another reason:\n{r.stderr}"
    assert VALUES_SECRET in r.stderr, r.stderr
    history = helm.history()
    assert [(h["revision"], h["status"]) for h in history] == [(h["revision"], h["status"]) for h in STATE.history_after_adoption], f"the refused upgrade wrote a revision: {history}"
    assert helm.status()["info"]["status"] == "deployed"
    TIMINGS.record("helm upgrade refused by the admission policy", elapsed)
    logger.info("helm upgrade refused in %.1f s with the policy's message, no revision written", elapsed)


# ---------------------------------------------------------------------------
# 9. the ordered teardown
# ---------------------------------------------------------------------------


@pytest.mark.smoke
def test_uninstall_is_the_ordered_teardown(kube: Kube, helm: Helm, app_deployment: float) -> None:
    agent_hrs_before = [hr["metadata"]["name"] for hr in kube.items("helmreleases.helm.toolkit.fluxcd.io", namespace=KAGENT_NAMESPACE)]
    templates_before = sorted(t["metadata"]["name"] for t in kube.items("agenttemplates.kagent.dev", namespace=KAGENT_NAMESPACE))
    servers_before = sorted(s["metadata"]["name"] for s in kube.items("remotemcpservers.kagent.dev", namespace=KAGENT_NAMESPACE))
    assert MANAGED_AGENT in agent_hrs_before, f"the managed agent's HelmRelease was not there before the uninstall: {agent_hrs_before}"
    assert templates_before == sorted((DECLARATIVE_AGENT, MANAGED_AGENT)), templates_before
    assert servers_before == [MANAGED_AGENT], servers_before
    try:
        elapsed = helm.uninstall()
    except AssertionError:
        dump_platform(kube)
        kube.dump([f"-n {NAMESPACE} get jobs -o wide", f"-n {NAMESPACE} get events --sort-by=.lastTimestamp"])
        raise
    TIMINGS.record("helm uninstall --wait (ordered teardown)", elapsed)
    crds = kube.crd_names()
    left = sorted(n for n in crds if n.endswith(FLUX_CRD_SUFFIX))
    assert not left, f"Flux CRDs left behind after the uninstall: {left}"
    assert OPERATOR_CRDS <= set(crds), "the operator CRDs must remain (Helm never deletes crds/)"
    for name in ("flux-operator", "source-controller", "helm-controller", "muster", "agent-manager"):
        assert kube.get("deployment", name, namespace=NAMESPACE) is None, f"Deployment {name} survived the uninstall"
    # No Job of the release is left (the lab Dex's cert-gen Job is the prerequisite's, not the chart's).
    jobs = [j["metadata"]["name"] for j in kube.items("jobs", "-l", f"app.kubernetes.io/instance={RELEASE}", namespace=NAMESPACE)]
    assert not jobs, f"Jobs of the release left behind: {jobs}"
    assert helm.releases_in_any_state() == [], helm.releases_in_any_state()
    assert kube.get("secret", VALUES_SECRET, namespace=NAMESPACE) is None, "the values Secret survived"
    wait_for("the admission policy gone (it lingers a second in the apiserver cache)",
             lambda: kube.get("validatingadmissionpolicies.admissionregistration.k8s.io", SELF_POLICY) is None, 60, interval=2)
    # The keep policy of the kagent line: the ordered teardown uninstalled the
    # kagent-crds release with the others, and its CRDs — templates carrying
    # helm.sh/resource-policy: keep — survived it, so every AgentTemplate and
    # RemoteMCPServer is still there in the kept kagent namespace (the agents'
    # HelmRelease objects went with the Flux CRDs; the declarative template was
    # never Helm's). The platform Harness stays too — since 4.8.0 the kagent
    # release renders it (harness.create) with helm.sh/resource-policy: keep,
    # the runtime of every template admitted under it (through 4.7.19 the
    # connectivity release rendered it with the same keep, and the 4.8.0
    # upgrade adopted it in place; giantswarm/agent-platform#406). Nothing else of the agent
    # runtime survives: the ModelConfig, the WorkerPool and its workers, the
    # controller and its Postgres (the kagent release's), the
    # substrate release's SandboxConfig and Substrate's control plane go. The
    # three ate.dev CRDs stay too — the Substrate line's keep policy (from
    # v0.0.27-gs.3 on), the same convention as kagent-crds', so a consumer's
    # uninstall can always delete its CRs whatever order the releases go in
    # (giantswarm/agent-platform#385). What stays by design, and what the next
    # install on this cluster relies on: in ate-system the bootstrap hook's
    # CA/JWT pools, ate-api-server's authentication config and the bundled
    # Postgres's claim (a StatefulSet's PVC), none Helm-owned; the
    # podcertificate-controller's namespace with its two CA pools, kept by the
    # Substrate line's chart (helm.sh/resource-policy: keep, from v0.0.27-gs.5
    # on — the pools it signs from must outlive the release); and the signers'
    # cluster-scoped ClusterTrustBundles, which carry those pools' roots
    # (giantswarm/agent-platform#384). The own-Flux scenario reinstalls onto
    # exactly this.
    assert_kept_crds(kube)
    assert kube.get("namespace", KAGENT_NAMESPACE), "the kagent namespace went with the uninstall; it must be kept (the agents live there)"
    templates = sorted(t["metadata"]["name"] for t in kube.items("agenttemplates.kagent.dev", namespace=KAGENT_NAMESPACE))
    assert templates == templates_before, f"AgentTemplates after the uninstall {templates} != before {templates_before}"
    servers = sorted(s["metadata"]["name"] for s in kube.items("remotemcpservers.kagent.dev", namespace=KAGENT_NAMESPACE))
    assert servers == servers_before, f"RemoteMCPServers after the uninstall {servers} != before {servers_before}"
    harness = kube.get("harnesses.kagent.dev", HARNESS, namespace=KAGENT_NAMESPACE)
    assert harness, f"the platform Harness {HARNESS} went with the uninstall of the kagent release; it must be kept (helm.sh/resource-policy: keep, giantswarm/agent-platform#406)"
    harness_annotations = harness["metadata"].get("annotations") or {}
    assert harness_annotations.get("helm.sh/resource-policy") == "keep", harness_annotations
    assert harness_annotations.get("meta.helm.sh/release-name") == "kagent", f"the kept Harness is not the kagent release's (components.kagent.chart = the releaseName): {harness_annotations}"
    assert not kube.items("modelconfigs.kagent.dev", namespace=KAGENT_NAMESPACE), "ModelConfigs survived the uninstall of the kagent release"
    assert kube.get("sandboxconfigs.ate.dev", SANDBOX_CONFIG) is None, "the substrate release's SandboxConfig survived its uninstall"
    assert not kube.items("workerpools.ate.dev", all_namespaces=True), "a WorkerPool survived the kagent release's uninstall"
    wait_for(f"no workload left in {KAGENT_NAMESPACE} (the controller, the UI, its Postgres, the WorkerPool's workers)",
             lambda: not (kube.items("deployments", namespace=KAGENT_NAMESPACE) or kube.items("statefulsets", namespace=KAGENT_NAMESPACE) or kube.items("pods", namespace=KAGENT_NAMESPACE)), 180, interval=3)
    wait_for(f"Substrate's control plane gone from {ATE_NAMESPACE}", lambda: not kube.items("pods", namespace=ATE_NAMESPACE), 180, interval=3)
    wait_for(f"the podcertificate-controller gone from {PODCERT_NAMESPACE}", lambda: not kube.items("pods", namespace=PODCERT_NAMESPACE), 180, interval=3)
    podcert_ns = kube.get("namespace", PODCERT_NAMESPACE)
    assert podcert_ns and podcert_ns["status"].get("phase") == "Active", f"{PODCERT_NAMESPACE} went with the substrate release (the Substrate line's keep policy missing): {podcert_ns and podcert_ns['status']}"
    assert (podcert_ns["metadata"].get("annotations") or {}).get("helm.sh/resource-policy") == "keep", podcert_ns["metadata"].get("annotations")
    for pool in PODCERT_SIGNERS.values():
        assert kube.get("secret", pool, namespace=PODCERT_NAMESPACE), f"CA pool {PODCERT_NAMESPACE}/{pool} went with the uninstall"
    for pool in ("actor-id-ca-pool", "actor-id-jwt-pool", "actor-id-ca-certs"):
        assert kube.get("secret", pool, namespace=ATE_NAMESPACE), f"{ATE_NAMESPACE}/{pool} went with the uninstall"
    bundles = substrate_trust_bundles(kube)
    assert bundles == sorted(s.replace("/", ":") + ":primary-bundle" for s in PODCERT_SIGNERS), f"the podcert signers' ClusterTrustBundles after the uninstall: {bundles}"
    assert_substrate_trust_chain(kube)
    logger.info("kept after the uninstall: CRDs %s; in %s AgentTemplates %s, RemoteMCPServers %s (their HelmReleases %s are gone with the Flux CRDs), the Harness %s (keep policy, the %s release's); of Substrate: %s with its pools, %s with its two CA pools (keep policy), the ClusterTrustBundles %s carrying the pools' roots",
                sorted(KEPT_CRDS), KAGENT_NAMESPACE, templates, servers, agent_hrs_before, HARNESS, CONNECTIVITY, ATE_NAMESPACE, PODCERT_NAMESPACE, bundles)
    assert elapsed < UNINSTALL_BUDGET_S, f"helm uninstall --wait took {elapsed:.0f}s (budget {UNINSTALL_BUDGET_S}s)"
    logger.info("uninstall clean in %.0f s: no Flux CRD, operator CRDs kept, no controller, no Job, no release", elapsed)
    # Leave the next scenario a cluster without the kept templates and Harness
    # (its own kagent runs there; the kept CRDs it adopts); the namespace's termination
    # completes in the background. Substrate's leftovers stay: the own-Flux
    # scenario is the reinstall onto them (giantswarm/agent-platform#384).
    kube.delete("namespace", KAGENT_NAMESPACE, wait=False)
    for phase, seconds in TIMINGS.entries.items():
        logger.info("TIMING %-90s %6.0f s", phase, seconds)
