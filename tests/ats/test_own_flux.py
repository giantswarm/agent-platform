"""The own-Flux scenario: a cluster that runs its own Flux installs the chart
through it, with the bundled engine off — the fleet's path (every Giant Swarm
management cluster) on the ATS kind cluster, after the smoke's uninstall.

  1. Flux's source-controller and helm-controller from the pinned upstream
     release manifest, applied with the field manager `flux` (what `flux install
     --components=source-controller,helm-controller` does); the cluster owns its
     namespaces, so `kagent` is created up front the way the fleet bases do;
  2. the chart THROUGH a HelmRelease in flux-system — an OCIRepository on the
     in-cluster registry the smoke pushed the candidate to, `components.flux.enabled:
     false` — reaches Ready; the platform HelmReleases it renders are reconciled by
     the cluster's Flux and reach Ready without a serviceAccountName;
  3. nothing of the engine reached the cluster: no operator Deployment, no
     FluxInstance, exactly one helm-controller, the Flux CRDs' field managers
     exactly what `flux install` left (`flux` and the apiserver);
  4. an agent deploys through the cluster's Flux: an OCIRepository + HelmRelease
     of the agent chart in the kagent namespace, run as the kagent-flux identity
     the connectivity release rendered, reach Ready; the Agent reaches Ready;
  5. the render guard: flipping the value to true makes the HelmRelease FAIL
     with `this cluster runs Flux; set components.flux.enabled=false or install
     the chart through it` — no operator, no FluxInstance, the platform and the
     agent still Ready, the Flux CRDs' content unchanged. One thing the guard
     cannot stop under helm-controller: it applies a chart's crds/ before it
     renders, so the flipped value server-side-applies the vendored Flux CRDs
     (the same Flux version as the cluster's, so identical content) and
     helm-controller joins their field managers (asserted and logged as the
     finding it is; `upgrade.crds: Skip` on the installing HelmRelease avoids
     it). Flipping the value back recovers;
  6. the way back: deleting the HelmRelease lets that Flux uninstall the
     platform (the chart renders no hook here and never touches the cluster's
     Flux); the Flux install is removed last.

Runs as the `functional` scenario (one pytest process after the smoke's); the
smoke leaves the lab Dex, the registry with the chart and the four operator
CRDs behind, and nothing else the guard could mistake for an engine.
"""

import logging
import time
from typing import Any, Dict, Iterator, List, Set

import pytest
import requests
import yaml

from conftest import (
    BASE_VALUES,
    KAGENT_FLUX_SA,
    KAGENT_NAMESPACE,
    KAGENT_VALUES,
    NAMESPACE,
    OPERATOR_CRDS,
    REGISTRY_URL,
    RELEASE,
    TIMINGS,
    Kube,
    condition,
    connectivity_values,
    is_ready,
    load_values,
    wait_for,
)

logger = logging.getLogger(__name__)

FLUX_VERSION = "v2.9.5"  # renovate: datasource=github-releases depName=fluxcd/flux2
FLUX_INSTALL_URL = f"https://github.com/fluxcd/flux2/releases/download/{FLUX_VERSION}/install.yaml"
FLUX_NAMESPACE = "flux-system"
FLUX_COMPONENTS = {"source-controller", "helm-controller"}
FLUX_FIELD_MANAGER = "flux"
GUARD_MESSAGE = "this cluster runs Flux; set components.flux.enabled=false or install the chart through it"
COMPONENTS = ("muster", "dicebear", "agent-platform-connectivity", "kagent")
AGENT = "ats-flux-agent"
AGENT_CHART_URL = "oci://gsoci.azurecr.io/charts/giantswarm/agent"
MODEL_CONFIG = "default-model-config"


class State:
    crd_managers: Dict[str, Set[str]] = {}
    crd_specs: Dict[str, Any] = {}
    flux_manifest: List[Dict[str, Any]] = []


STATE = State()


def dump(kube: Kube) -> None:
    kube.dump([
        f"-n {FLUX_NAMESPACE} get helmreleases.helm.toolkit.fluxcd.io,ocirepositories.source.toolkit.fluxcd.io -o wide",
        f"-n {FLUX_NAMESPACE} get helmrelease {RELEASE} -o yaml",
        f"-n {NAMESPACE} get helmreleases.helm.toolkit.fluxcd.io,ocirepositories.source.toolkit.fluxcd.io -o wide",
        f"-n {NAMESPACE} get pods -o wide",
        f"-n {KAGENT_NAMESPACE} get helmreleases.helm.toolkit.fluxcd.io,agents.kagent.dev,pods -o wide",
        f"-n {FLUX_NAMESPACE} logs deployment/helm-controller --tail=60",
        f"-n {FLUX_NAMESPACE} logs deployment/source-controller --tail=40",
    ])


def flux_crd_managers(kube: Kube) -> Dict[str, Set[str]]:
    return {name: kube.managers("crd", name) for name in kube.flux_crds()}


def flux_crd_specs(kube: Kube) -> Dict[str, Any]:
    return {name: (kube.get("crd", name) or {}).get("spec") for name in kube.flux_crds()}


def helm_controllers(kube: Kube) -> List[str]:
    return [f"{d['metadata']['namespace']}/{d['metadata']['name']}"
            for d in kube.items("deployments", "-l", "app.kubernetes.io/component=helm-controller", all_namespaces=True)]


def operator_deployments(kube: Kube) -> List[str]:
    return [f"{d['metadata']['namespace']}/{d['metadata']['name']}" for d in kube.items("deployments", all_namespaces=True)
            if d["metadata"]["name"] == "flux-operator"]


def assert_no_engine(kube: Kube, crds_untouched: bool = True) -> None:
    """Nothing of the engine on the cluster. With crds_untouched the Flux CRDs'
    field managers are exactly what `flux install` left; without it (after the
    guard fired under helm-controller) their content must be unchanged and no
    engine manager (flux-operator) may have appeared — helm-controller applies
    a chart's crds/ BEFORE it renders the templates, so the flipped value makes
    it server-side-apply the vendored Flux CRDs (identical to the cluster's,
    the same Flux version) and join their managers even though the render then
    fails. Under the Helm CLI `helm upgrade` never touches crds/."""
    assert not operator_deployments(kube), f"a flux-operator Deployment exists: {operator_deployments(kube)}"
    assert not kube.items("fluxinstances.fluxcd.controlplane.io", all_namespaces=True), "a FluxInstance exists"
    assert helm_controllers(kube) == [f"{FLUX_NAMESPACE}/helm-controller"], f"helm-controllers: {helm_controllers(kube)}"
    managers = flux_crd_managers(kube)
    assert flux_crd_specs(kube) == STATE.crd_specs, "the Flux CRDs' content changed"
    if crds_untouched:
        assert managers == STATE.crd_managers, f"the Flux CRDs' field managers changed: {managers} != {STATE.crd_managers}"
    else:
        extra = {m for ms in managers.values() for m in ms} - {m for ms in STATE.crd_managers.values() for m in ms}
        assert extra <= {"helm-controller"}, f"unexpected field managers on the Flux CRDs: {sorted(extra)}"
        if extra:
            logger.warning("finding: helm-controller applied the chart's crds/ before the render failed — the Flux CRDs gained the manager %s (content unchanged); "
                           "a HelmRelease that installs this chart on a cluster with its own Flux can set upgrade.crds: Skip to avoid even that", sorted(extra))


def platform_values() -> Dict[str, Any]:
    """The smoke's base values (engine on there) with the engine off: what the
    README's own-Flux HelmRelease inlines."""
    return load_values([BASE_VALUES, KAGENT_VALUES], ["components.flux.enabled=false"])


def meta_helmrelease(version: str, engine: bool) -> List[Dict[str, Any]]:
    values = platform_values()
    values["components"]["flux"]["enabled"] = engine
    values["components"].update(connectivity_values(version)["components"])
    return [
        {"apiVersion": "source.toolkit.fluxcd.io/v1", "kind": "OCIRepository",
         "metadata": {"name": RELEASE, "namespace": FLUX_NAMESPACE},
         "spec": {"interval": "1m", "url": f"{REGISTRY_URL}/{RELEASE}", "insecure": True, "ref": {"semver": version}}},
        {"apiVersion": "helm.toolkit.fluxcd.io/v2", "kind": "HelmRelease",
         "metadata": {"name": RELEASE, "namespace": FLUX_NAMESPACE},
         "spec": {"interval": "1m", "releaseName": RELEASE, "targetNamespace": NAMESPACE,
                  "chartRef": {"kind": "OCIRepository", "name": RELEASE},
                  "install": {"createNamespace": True}, "values": values}},
    ]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def own_flux(kube: Kube, prerequisites: None) -> Iterator[None]:
    """Flux's source-controller + helm-controller from the upstream release
    manifest, the way `flux install --components=…` applies them (field manager
    `flux`), on a cluster without Flux. Yields with the CRD managers recorded.
    ``prerequisites`` (idempotent) brings what the smoke left when this scenario
    runs alone: the Gateway API CRDs, the lab Dex, the policy-exceptions namespace."""
    assert not kube.flux_crds(), f"the own-Flux scenario needs a cluster without Flux CRDs: {kube.flux_crds()}"
    assert not operator_deployments(kube) and not helm_controllers(kube), "an engine is still running"
    assert OPERATOR_CRDS <= set(kube.crd_names()), "the smoke's operator CRDs are expected to remain; the guard's lookup must find no FluxInstance among them"
    started = time.monotonic()
    r = requests.get(FLUX_INSTALL_URL, timeout=60)
    assert r.status_code == 200, f"{FLUX_INSTALL_URL}: {r.status_code}"
    docs = [d for d in yaml.safe_load_all(r.text) if d]
    keep = [d for d in docs if ((d.get("metadata") or {}).get("labels") or {}).get("app.kubernetes.io/component", "") in FLUX_COMPONENTS | {""}]
    dropped = sorted({((d.get("metadata") or {}).get("labels") or {}).get("app.kubernetes.io/component") for d in docs} - FLUX_COMPONENTS - {None})
    STATE.flux_manifest = keep
    kube.cmd(["apply", "--server-side", f"--field-manager={FLUX_FIELD_MANAGER}", "-f", "-"], stdin=yaml.safe_dump_all(keep))
    for name in sorted(FLUX_COMPONENTS):
        kube.wait_deployment(FLUX_NAMESPACE, name, timeout=300)
    kube.cmd(["wait", "--for=condition=Established", "--timeout=60s", "crd", *kube.flux_crds()])
    STATE.crd_managers = flux_crd_managers(kube)
    STATE.crd_specs = flux_crd_specs(kube)
    # The cluster owns its namespaces: the fleet bases create kagent's on every
    # management cluster; here the test does (README "Clusters that run Flux").
    # The smoke's teardown deleted the namespace with the connectivity release;
    # its termination may still be running when this scenario starts.
    wait_for(f"namespace {KAGENT_NAMESPACE} gone or Active (not Terminating)",
             lambda: (kube.get("namespace", KAGENT_NAMESPACE) or {}).get("status", {}).get("phase", "gone") != "Terminating", 300)
    kube.apply({"apiVersion": "v1", "kind": "Namespace", "metadata": {"name": KAGENT_NAMESPACE}})
    TIMINGS.record(f"flux install ({', '.join(sorted(FLUX_COMPONENTS))} {FLUX_VERSION}; {len(keep)} objects, components dropped: {dropped})", time.monotonic() - started)
    logger.info("Flux CRD managers after flux install: %s", sorted({m for ms in STATE.crd_managers.values() for m in ms}))
    yield
    # 6. the way back (best effort, bounded): the HelmRelease first — that Flux
    # uninstalls the platform — then the Flux install.
    started = time.monotonic()
    try:
        kube.delete("helmreleases.helm.toolkit.fluxcd.io", AGENT, namespace=KAGENT_NAMESPACE, timeout="3m")
        kube.delete("ocirepositories.source.toolkit.fluxcd.io", "agent", namespace=KAGENT_NAMESPACE, timeout="1m")
        kube.delete("helmreleases.helm.toolkit.fluxcd.io", RELEASE, namespace=FLUX_NAMESPACE, timeout="3m")
        wait_for("the platform HelmReleases uninstalled by the cluster's Flux",
                 lambda: not kube.items("helmreleases.helm.toolkit.fluxcd.io", namespace=NAMESPACE), 120)
        kube.delete("ocirepositories.source.toolkit.fluxcd.io", RELEASE, namespace=FLUX_NAMESPACE, timeout="1m")
        kube.cmd(["delete", "--ignore-not-found", "--wait=false", "-f", "-"], stdin=yaml.safe_dump_all(STATE.flux_manifest), check=False)
        TIMINGS.record("the way back: HelmRelease deleted, platform uninstalled by the cluster's Flux, Flux removed", time.monotonic() - started)
    except AssertionError as exc:  # cleanup never masks a verdict; the job deletes the cluster
        logger.error("cleanup incomplete: %s", exc)
    for phase, seconds in TIMINGS.entries.items():
        logger.info("TIMING %-90s %6.0f s", phase, seconds)


@pytest.fixture(scope="module")
def platform_through_flux(kube: Kube, own_flux: None, pushed_chart: str, candidate_version: str) -> None:
    """The chart installed through the cluster's Flux with the engine off."""
    started = time.monotonic()
    kube.apply(meta_helmrelease(candidate_version, engine=False))
    try:
        wait_for(f"HelmRelease {FLUX_NAMESPACE}/{RELEASE} Ready",
                 lambda: is_ready(kube.get("helmreleases.helm.toolkit.fluxcd.io", RELEASE, namespace=FLUX_NAMESPACE)), 600)
        TIMINGS.record("HelmRelease agent-platform Ready through the cluster's Flux (engine off)", time.monotonic() - started)

        def components_ready() -> bool:
            hrs = {hr["metadata"]["name"]: hr for hr in kube.items("helmreleases.helm.toolkit.fluxcd.io", namespace=NAMESPACE)}
            return set(hrs) == set(COMPONENTS) and all(is_ready(hr) for hr in hrs.values())

        wait_for("the platform HelmReleases Ready under the cluster's Flux", components_ready, 600)
    except AssertionError:
        dump(kube)
        raise
    TIMINGS.record("platform HelmReleases Ready through the cluster's Flux", time.monotonic() - started)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.functional
def test_platform_installs_through_the_clusters_flux(kube: Kube, platform_through_flux: None) -> None:
    hrs = {hr["metadata"]["name"]: hr for hr in kube.items("helmreleases.helm.toolkit.fluxcd.io", namespace=NAMESPACE)}
    assert set(hrs) == set(COMPONENTS), sorted(hrs)
    for name, hr in hrs.items():
        assert is_ready(hr), f"{name}: {condition(hr)}"
        assert "serviceAccountName" not in hr["spec"], f"{name} names a serviceAccountName with the engine off: {hr['spec'].get('serviceAccountName')}"
    assert hrs["kagent"]["spec"]["targetNamespace"] == NAMESPACE, "engine off: the kagent HelmRelease must keep gitops.targetNamespace (the fleet render)"
    assert_no_engine(kube)
    hook_jobs = [j["metadata"]["name"] for j in kube.items("jobs", "-l", f"app.kubernetes.io/instance={RELEASE}", namespace=NAMESPACE)]
    assert not hook_jobs, f"the chart rendered hooks with the engine off: {hook_jobs}"
    assert not kube.items("validatingadmissionpolicies.admissionregistration.k8s.io", "-l", f"app.kubernetes.io/instance={RELEASE}"), "self-management rendered with the engine off"
    assert kube.deployment_ready(NAMESPACE, "muster")
    logger.info("platform Ready through the cluster's Flux: %s; one helm-controller, no operator, CRD managers %s",
                sorted(hrs), sorted({m for ms in STATE.crd_managers.values() for m in ms}))


@pytest.mark.functional
def test_agent_deploys_through_the_clusters_flux(kube: Kube, platform_through_flux: None) -> None:
    started = time.monotonic()
    try:
        kube.wait_deployment(KAGENT_NAMESPACE, "kagent-controller", timeout=600)
        wait_for(f"ModelConfig {MODEL_CONFIG}", lambda: kube.get("modelconfigs.kagent.dev", MODEL_CONFIG, namespace=KAGENT_NAMESPACE), 120)
        assert kube.get("serviceaccount", KAGENT_FLUX_SA, namespace=KAGENT_NAMESPACE), f"the connectivity release did not render ServiceAccount {KAGENT_FLUX_SA}"
        kube.apply({"apiVersion": "v1", "kind": "Secret", "metadata": {"name": "kagent-anthropic", "namespace": KAGENT_NAMESPACE},
                    "stringData": {"ANTHROPIC_API_KEY": "lab-only-placeholder-key"}})
        kube.apply([
            {"apiVersion": "source.toolkit.fluxcd.io/v1", "kind": "OCIRepository",
             "metadata": {"name": "agent", "namespace": KAGENT_NAMESPACE},
             "spec": {"interval": "10m", "url": AGENT_CHART_URL, "ref": {"semver": ">=0.2.1 <1.0.0"}}},  # the 0.x chart: 1.0.0 renders kagent API v2
            {"apiVersion": "helm.toolkit.fluxcd.io/v2", "kind": "HelmRelease",
             "metadata": {"name": AGENT, "namespace": KAGENT_NAMESPACE},
             "spec": {"interval": "10m", "releaseName": AGENT, "serviceAccountName": KAGENT_FLUX_SA,
                      "chartRef": {"kind": "OCIRepository", "name": "agent"},
                      "values": {"agent": {"description": "ATS own-Flux agent (lab only)", "systemMessage": "You are the ATS own-Flux agent."},
                                 "modelConfig": {"name": MODEL_CONFIG}, "toolset": ["preset:none"]}}},
        ])
        wait_for(f"HelmRelease {AGENT} Ready", lambda: is_ready(kube.get("helmreleases.helm.toolkit.fluxcd.io", AGENT, namespace=KAGENT_NAMESPACE)), 600)
        wait_for(f"Agent {AGENT} Ready", lambda: is_ready(kube.get("agents.kagent.dev", AGENT, namespace=KAGENT_NAMESPACE)), 600)
    except AssertionError:
        dump(kube)
        raise
    TIMINGS.record("agent through the cluster's Flux: HelmRelease + Agent Ready", time.monotonic() - started)


@pytest.mark.functional
def test_flipping_the_engine_on_fails_the_render_and_touches_nothing(kube: Kube, platform_through_flux: None, candidate_version: str) -> None:
    started = time.monotonic()
    kube.apply(meta_helmrelease(candidate_version, engine=True))
    try:
        def guard_fired() -> Any:
            hr = kube.get("helmreleases.helm.toolkit.fluxcd.io", RELEASE, namespace=FLUX_NAMESPACE)
            messages = " | ".join(c.get("message", "") for c in (hr or {}).get("status", {}).get("conditions", []))
            return messages if hr and GUARD_MESSAGE in messages and condition(hr).get("status") == "False" else False

        messages = wait_for("the HelmRelease failing with the render guard's message", guard_fired, 300, interval=3)
        TIMINGS.record("render guard: HelmRelease reports the refusal after the flip", time.monotonic() - started)
        assert_no_engine(kube, crds_untouched=False)
        hrs = {hr["metadata"]["name"]: hr for hr in kube.items("helmreleases.helm.toolkit.fluxcd.io", namespace=NAMESPACE)}
        assert set(hrs) == set(COMPONENTS) and all(is_ready(hr) for hr in hrs.values()), {n: condition(h) for n, h in hrs.items()}
        assert all("serviceAccountName" not in hr["spec"] for hr in hrs.values()), "the failed upgrade changed the platform HelmReleases"
        assert is_ready(kube.get("agents.kagent.dev", AGENT, namespace=KAGENT_NAMESPACE)), "the agent is no longer Ready"
        logger.info("guard fired: %s", messages[:300])
        # the way out the message names: the value back to false recovers
        kube.apply(meta_helmrelease(candidate_version, engine=False))
        wait_for(f"HelmRelease {RELEASE} Ready again with the engine off",
                 lambda: is_ready(kube.get("helmreleases.helm.toolkit.fluxcd.io", RELEASE, namespace=FLUX_NAMESPACE)), 300, interval=3)
        assert_no_engine(kube, crds_untouched=False)
    except AssertionError:
        dump(kube)
        raise
    TIMINGS.record("render guard flipped on and back (refusal, nothing touched, recovery)", time.monotonic() - started)
