"""Kind smoke of the agent-platform meta chart: the quick start on a bare cluster.

On every PR, on the ATS kind cluster, in this order (pytest runs the tests of a
module top to bottom; each one builds on the state the previous left):

  1. prerequisites — the Gateway API CRDs (the one prerequisite the chart does
     not bring), the lab Dex (tests/ats/lab-dex.yaml: static users, the
     platform's identity Secret, a self-signed CA, the CoreDNS rewrite) and an
     in-cluster registry the candidate archive is pushed to;
  2. `helm install --wait` of the candidate with the quick start's shape —
     helm/agent-platform/examples/kind-lab-dex.yaml (muster, dicebear, connectivity; the bundled Flux
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
  6. the agent round trips: a declarative kagent Agent against the chart's
     default ModelConfig (a placeholder provider key) reaches Ready; and — the
     write path the standalone's smoke never had — agent-manager's create_agent,
     called through muster as the Dex user with the forwarded token, writes an
     OCIRepository + HelmRelease of the agent chart into the kagent namespace,
     the HelmRelease runs as kagent-flux and reaches Ready, the Agent reaches Ready;
  7. the fixpoint: two self-management intervals after the adoption `helm
     history` is unchanged and the values Secret equals the values used;
  8. the Helm CLI is day-0 only: `helm upgrade` is refused by the admission
     policy with its message in Helm's own output, no revision written;
  9. `helm uninstall --wait`: the ordered teardown returns clean in under a
     minute, no Flux CRD left, the four operator CRDs remaining, no controller,
     no hook Job, no release in any state, the agents' HelmRelease objects gone
     with the CRDs.

The lab shape (`gitops.self.enabled: false`, what agentlab installs) renders
none of the self-management objects; that shape is asserted offline by
`make verify-self`. A second `helm install` on the same cluster is the
functional scenario's job (test_own_flux.py installs the chart again, through
the cluster's own Flux).
"""

import base64
import logging
import time
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

import pytest
import yaml

from scenarios import EXAMPLES_DIR, KIND_LAB_VALUES

from conftest import (
    CROSS_CLIENT_AUDIENCE,
    DEX_USER,
    SCENARIO,
    FLUX_CRD_SUFFIX,
    KAGENT_FLUX_SA,
    KAGENT_NAMESPACE,
    MUSTER_BASE_URL,
    MUSTER_BASE_URL_SETS,
    NAMESPACE,
    OPERATOR_CRDS,
    RELEASE,
    SELF_INTERVAL_S,
    SELF_POLICY,
    SMOKE_VALUES,
    TENANT_SA,
    TIMINGS,
    UNINSTALL_BUDGET_S,
    VALUES_SECRET,
    Helm,
    Kube,
    MusterSession,
    PortForward,
    condition,
    connectivity_sets,
    dex_password_grant,
    is_ready,
    jwt_claims,
    load_values,
    login_through_muster,
    self_management_sets,
    unauthenticated_mcp_challenge,
    wait_for,
    wait_for_muster_healthy,
)

logger = logging.getLogger(__name__)

# The component HelmReleases the smoke values leave on.
COMPONENTS = ("muster", "dicebear", "agent-platform-connectivity", "kagent", "agent-manager")
# The Deployments `helm install --wait` leaves running (the kagent controller is
# waited for separately: its HelmRelease reports Ready once the manifests are
# applied, installDisableWait).
CORE_DEPLOYMENTS = ("flux-operator", "source-controller", "helm-controller", "muster", "agent-manager")
DECLARATIVE_AGENT = "ats-smoke-agent"
MANAGED_AGENT = "ats-managed-agent"
AGENT_CHART_REPOSITORY = "agent"  # the shared per-namespace OCIRepository agent-manager writes
MODEL_CONFIG = "default-model-config"
PLACEHOLDER_PROVIDER_SECRET = {"name": "kagent-anthropic", "key": "ANTHROPIC_API_KEY"}
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
        f"-n {NAMESPACE} get events --sort-by=.lastTimestamp",
        f"-n {NAMESPACE} logs deployment/helm-controller --tail=60",
    ])


def dump_auth(kube: Kube) -> None:
    kube.dump([f"-n {NAMESPACE} logs deployment/muster --tail=120", f"-n {NAMESPACE} logs deployment/lab-dex --tail=60",
               f"-n {NAMESPACE} logs deployment/agent-manager --tail=60"])


def dump_agents(kube: Kube) -> None:
    kube.dump([
        f"-n {KAGENT_NAMESPACE} get agents.kagent.dev -o yaml",
        f"-n {KAGENT_NAMESPACE} get helmreleases.helm.toolkit.fluxcd.io,ocirepositories.source.toolkit.fluxcd.io -o wide",
        f"-n {KAGENT_NAMESPACE} get pods -o wide",
        f"-n {KAGENT_NAMESPACE} get events --sort-by=.lastTimestamp",
        f"-n {KAGENT_NAMESPACE} logs deployment/kagent-controller --tail=60",
    ])


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def smoke_sets(candidate_version: str) -> List[str]:
    """The --set values of the smoke install: self-management against the
    in-cluster registry, the connectivity chart of this checkout from the same
    registry (and the muster base URL when the local port moved)."""
    return self_management_sets(candidate_version) + connectivity_sets(candidate_version) + MUSTER_BASE_URL_SETS


@pytest.mark.smoke
def test_the_scenario_installs_the_example_file() -> None:
    """The values file the smoke installs IS an example file the repository
    ships, so the documented install and the tested install cannot drift. The
    kind scenario's is helm/agent-platform/examples/kind-lab-dex.yaml."""
    base = SCENARIO.base_values
    assert base.is_file(), f"the scenario's base values file does not exist: {base}"
    assert base.parent == EXAMPLES_DIR, (
        f"the scenario installs {base}, which is not in {EXAMPLES_DIR}: the file the test installs "
        "must be an example the repository ships, or the two drift")
    if SCENARIO.name == "kind":
        assert base == KIND_LAB_VALUES, f"the kind scenario installs {base}, not {KIND_LAB_VALUES}"


@pytest.fixture(scope="module")
def app_deployment(kube: Kube, helm: Helm, prerequisites: None, chart_archive: Path, pushed_chart: str, smoke_sets: List[str]) -> float:
    """`helm install --wait` of the candidate, the quick start's way. Returns the seconds it took."""
    assert not kube.flux_crds(), "the smoke needs a cluster without Flux CRDs; another Flux is present"
    try:
        elapsed = helm.install(str(chart_archive), SMOKE_VALUES, smoke_sets)
    except AssertionError:
        dump_platform(kube)
        raise
    TIMINGS.record("helm install --wait (engine, muster+OAuth, dicebear, connectivity, kagent, agent-manager, self on)", elapsed)
    return elapsed


@pytest.fixture(scope="module")
def muster(muster_forward: Optional[PortForward], app_deployment: float) -> Optional[PortForward]:
    """muster reachable and healthy (its OAuth server discovers the issuer)."""
    started = time.monotonic()
    wait_for_muster_healthy(MUSTER_BASE_URL)
    TIMINGS.record("muster /health ok after the install", time.monotonic() - started)
    return muster_forward


@pytest.fixture(scope="module")
def dex(dex_forward: Optional[PortForward], dex_ca: Optional[str], app_deployment: float) -> Optional[str]:
    """The issuer reachable from the test; returns its CA path, or None when the
    issuer is served by a publicly trusted certificate."""
    return dex_ca


@pytest.fixture(scope="module")
def kagent_controller(kube: Kube, app_deployment: float) -> None:
    """The kagent controller running (its HelmRelease is Ready before the pod is)."""
    started = time.monotonic()
    kube.wait_deployment(KAGENT_NAMESPACE, "kagent-controller", timeout=600)
    wait_for(f"ModelConfig {MODEL_CONFIG}", lambda: kube.get("modelconfigs.kagent.dev", MODEL_CONFIG, namespace=KAGENT_NAMESPACE), 120)
    # The chart's default ModelConfig references this Secret; a placeholder key
    # is enough for the controller to accept an Agent (Ready means reconciled,
    # not that a model answered).
    kube.apply({"apiVersion": "v1", "kind": "Secret", "metadata": {"name": PLACEHOLDER_PROVIDER_SECRET["name"], "namespace": KAGENT_NAMESPACE},
                "stringData": {PLACEHOLDER_PROVIDER_SECRET["key"]: "lab-only-placeholder-key"}})
    TIMINGS.record("kagent controller Ready after the install returned", time.monotonic() - started)


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
def test_declarative_agent_reaches_ready(kube: Kube, kagent_controller: None) -> None:
    """A minimal declarative Agent against the chart's default ModelConfig (the
    provider key is a placeholder: Ready means the controller accepted and
    reconciled the Agent, not that a model call succeeded)."""
    started = time.monotonic()
    kube.apply({"apiVersion": "kagent.dev/v1alpha2", "kind": "Agent",
                "metadata": {"name": DECLARATIVE_AGENT, "namespace": KAGENT_NAMESPACE},
                "spec": {"description": "ATS smoke agent (lab only)", "type": "Declarative",
                         "declarative": {"modelConfig": MODEL_CONFIG, "systemMessage": "You are the ATS smoke agent."}}})
    try:
        wait_for(f"Agent {DECLARATIVE_AGENT} Ready", lambda: is_ready(kube.get("agents.kagent.dev", DECLARATIVE_AGENT, namespace=KAGENT_NAMESPACE)), 600)
    except AssertionError:
        dump_agents(kube)
        raise
    TIMINGS.record("declarative Agent Ready", time.monotonic() - started)


@pytest.mark.smoke
def test_agent_manager_create_agent_reaches_a_ready_helmrelease(kube: Kube, muster: PortForward, dex: str, kagent_controller: None) -> None:
    """agent-manager's create_agent through muster, as the Dex user: muster
    forwards the bearer to agent-manager (MCPServer auth.forwardToken; the
    token carries the required cross-client audience), agent-manager validates
    it against the lab Dex and writes the agent's HelmRelease (and the shared
    OCIRepository of the agent chart) into the kagent namespace with its own
    ServiceAccount; the bundled helm-controller executes the HelmRelease as
    kagent-flux, renders the Agent, and kagent runs it."""
    started = time.monotonic()
    token = STATE.dex_token or dex_password_grant(dex)
    session = MusterSession(MUSTER_BASE_URL, token, "ats-agent-manager").initialize()
    try:
        # muster connects to a forwardToken server per session, after the first
        # authenticated request; the tools appear once the SSO connection is up.
        wait_for("agent-manager's tools in muster's aggregated tool list", lambda: "x_agent-manager_create_agent" in session.aggregated_tools(), 120, interval=3)
        info = session.call_server_json("x_agent-manager_get_info")
        logger.info("agent-manager get_info: version %s, chart %s, flux %s", info.get("version"), info.get("chart", {}).get("ociUrl"), info.get("flux"))
        assert info.get("flux", {}).get("serviceAccountName") == KAGENT_FLUX_SA, info.get("flux")
        assert info.get("chart", {}).get("ociUrl") == "oci://gsoci.azurecr.io/charts/giantswarm/agent", info.get("chart")
        # Clean up an earlier attempt (a flaky rerun) so create_agent does not refuse a duplicate.
        kube.delete("helmreleases.helm.toolkit.fluxcd.io", MANAGED_AGENT, namespace=KAGENT_NAMESPACE, timeout="2m")
        result = session.call_server_json("x_agent-manager_create_agent", {
            "name": MANAGED_AGENT, "modelConfig": MODEL_CONFIG, "displayName": "ATS managed agent",
            "description": "created through agent-manager by the ATS smoke (lab only)",
            "systemMessage": "You are the ATS managed agent.", "toolset": ["preset:none"]})
        logger.info("create_agent returned: %s", str(result)[:400])
    except AssertionError:
        dump_auth(kube)
        dump_agents(kube)
        raise
    try:
        oci = wait_for(f"OCIRepository {AGENT_CHART_REPOSITORY} in {KAGENT_NAMESPACE} Ready",
                       lambda: is_ready(kube.get("ocirepositories.source.toolkit.fluxcd.io", AGENT_CHART_REPOSITORY, namespace=KAGENT_NAMESPACE))
                       and kube.get("ocirepositories.source.toolkit.fluxcd.io", AGENT_CHART_REPOSITORY, namespace=KAGENT_NAMESPACE), 300)
        assert oci["spec"]["url"] == "oci://gsoci.azurecr.io/charts/giantswarm/agent", oci["spec"]
        hr = kube.get("helmreleases.helm.toolkit.fluxcd.io", MANAGED_AGENT, namespace=KAGENT_NAMESPACE)
        assert hr, f"agent-manager wrote no HelmRelease {MANAGED_AGENT}"
        assert hr["spec"].get("serviceAccountName") == KAGENT_FLUX_SA, f"the agent HelmRelease does not run as {KAGENT_FLUX_SA}: {hr['spec'].get('serviceAccountName')!r}"
        assert hr["spec"]["chartRef"]["name"] == AGENT_CHART_REPOSITORY, hr["spec"]["chartRef"]
        wait_for(f"HelmRelease {MANAGED_AGENT} Ready", lambda: is_ready(kube.get("helmreleases.helm.toolkit.fluxcd.io", MANAGED_AGENT, namespace=KAGENT_NAMESPACE)), 600)
        wait_for(f"Agent {MANAGED_AGENT} Ready", lambda: is_ready(kube.get("agents.kagent.dev", MANAGED_AGENT, namespace=KAGENT_NAMESPACE)), 600)
        status = session.call_server_json("x_agent-manager_get_agent_status", {"name": MANAGED_AGENT})
        assert status.get("verdict") in ("ready", "progressing"), status
        listed = session.call_server_json("x_agent-manager_list_agents")
        names = {a.get("name") for a in (listed.get("agents") if isinstance(listed, dict) else listed) or []}
        assert MANAGED_AGENT in names, f"list_agents does not list {MANAGED_AGENT}: {names}"
    except AssertionError:
        dump_agents(kube)
        raise
    TIMINGS.record("agent-manager create_agent -> HelmRelease Ready -> Agent Ready", time.monotonic() - started)
    logger.info("agent-manager wrote %s (as %s); status verdict %s", MANAGED_AGENT, KAGENT_FLUX_SA, status.get("verdict"))


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
    # The agents' HelmRelease objects went with the Flux CRDs (the CRD is gone,
    # so is every object of its kind); their workloads stay behind, orphaned:
    # the kagent namespace is kept (helm.sh/resource-policy: keep on the
    # connectivity release's Namespace), the Agent CRs with it (the kagent CRDs
    # are app-owned, Helm never deletes crds/), and the agents' Deployments run on.
    assert MANAGED_AGENT in agent_hrs_before, f"the managed agent's HelmRelease was not there before the uninstall: {agent_hrs_before}"
    assert kube.get("namespace", KAGENT_NAMESPACE), "the kagent namespace went with the uninstall; it must be kept (the agents live there)"
    orphans = {d["metadata"]["name"] for d in kube.items("deployments", namespace=KAGENT_NAMESPACE)}
    assert {DECLARATIVE_AGENT, MANAGED_AGENT} <= orphans, f"the agents' Deployments did not survive the uninstall: {sorted(orphans)}"
    assert kube.get("agents.kagent.dev", MANAGED_AGENT, namespace=KAGENT_NAMESPACE), "the managed agent's Agent CR did not survive"
    logger.info("orphaned in %s after the uninstall: Deployments %s (their HelmReleases %s are gone with the CRDs)", KAGENT_NAMESPACE, sorted(orphans), agent_hrs_before)
    assert elapsed < UNINSTALL_BUDGET_S, f"helm uninstall --wait took {elapsed:.0f}s (budget {UNINSTALL_BUDGET_S}s)"
    logger.info("uninstall clean in %.0f s: no Flux CRD, operator CRDs kept, no controller, no Job, no release", elapsed)
    # Leave the next scenario a cluster without the orphans (its own kagent
    # runs there); the namespace's termination completes in the background.
    kube.delete("namespace", KAGENT_NAMESPACE, wait=False)
    for phase, seconds in TIMINGS.entries.items():
        logger.info("TIMING %-90s %6.0f s", phase, seconds)
