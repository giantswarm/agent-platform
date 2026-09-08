"""Kind install smoke of the agent-platform meta chart.

On every PR, on the ATS kind cluster, the way the README quick start installs
the chart:

  1. the Gateway API CRDs go on first (the one prerequisite the chart does not
     bring; the connectivity component renders an HTTPRoute);
  2. `helm install --wait` of the candidate archive with tests/test-values.yaml
     (ATS's own pre-test deploy is skipped via app-tests-skip-app-deploy) — the
     bundled Flux engine on, so a cluster without Flux ends up with a running
     platform;
  3. the release is `deployed`, the FluxInstance is Ready at a Flux 2.x, every
     component HelmRelease is Ready under the tenant identity
     agent-platform-flux, `helm history` shows one revision;
  4. `helm uninstall --wait` returns clean in under a minute (the pre-delete
     hooks delete the platform HelmReleases, then the FluxInstance; the
     operator removes Flux including its CRDs), the four operator CRDs remain
     (Helm never deletes crds/), and a reinstall reaches `deployed` again.

Helm and kubectl come with the ATS image; ``kube_cluster`` (pytest-helm-charts)
carries the kubeconfig. No agent round trips here: they need kagent and
agentgateway, which do not fit the CI executor's budget next to this — the
self-management slice adds them as a second scenario.
"""

import json
import logging
import subprocess  # nosec: fixed argv, the archive path comes from ATS
import threading
import time
from pathlib import Path
from typing import Any, Dict, List

import pykube
import pytest
from pytest_helm_charts.clusters import Cluster

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[2]
RELEASE = "agent-platform"
NAMESPACE = "agent-platform"
VALUES = REPO_ROOT / "tests" / "test-values.yaml"
GATEWAY_API_CRDS = (
    "https://github.com/kubernetes-sigs/gateway-api/releases/download/"
    "v1.5.0/standard-install.yaml"
)
TENANT_SA = "agent-platform-flux"
# The component HelmReleases tests/test-values.yaml leaves on.
COMPONENTS = ("muster", "dicebear", "agent-platform-connectivity")
FLUX_CRD_SUFFIX = ".toolkit.fluxcd.io"
OPERATOR_CRDS = {
    "fluxinstances.fluxcd.controlplane.io",
    "fluxreports.fluxcd.controlplane.io",
    "resourcesets.fluxcd.controlplane.io",
    "resourcesetinputproviders.fluxcd.controlplane.io",
}
INSTALL_TIMEOUT = "10m"
UNINSTALL_TIMEOUT = "5m"
# The acceptance criterion: the ordered teardown returns in under a minute.
UNINSTALL_BUDGET_S = 60


@pytest.fixture(scope="module", autouse=True)
def log_heartbeat() -> Any:
    """One log line a minute so CircleCI's no-output timeout never fires during
    the silent `helm install --wait`."""
    stop = threading.Event()

    def beat() -> None:
        minutes = 0
        while not stop.wait(60):
            minutes += 1
            logger.info("heartbeat: %d min elapsed, still waiting/working", minutes)

    thread = threading.Thread(target=beat, name="log-heartbeat", daemon=True)
    thread.start()
    yield
    stop.set()


def _run(args: List[str], timeout: int = 900) -> subprocess.CompletedProcess:
    logger.info("$ %s", " ".join(args))
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False)  # nosec


class Helm:
    def __init__(self, kubeconfig: str, archive: Path) -> None:
        self.kubeconfig = kubeconfig
        self.archive = archive

    def _cmd(self, *args: str) -> List[str]:
        return ["helm", "--kubeconfig", self.kubeconfig, *args]

    def install(self) -> float:
        """`helm install --wait` with the smoke values; returns the wall-clock seconds."""
        started = time.monotonic()
        r = _run(
            self._cmd(
                "install", RELEASE, str(self.archive),
                "--namespace", NAMESPACE, "--create-namespace",
                "--values", str(VALUES),
                "--wait", "--timeout", INSTALL_TIMEOUT,
            ),
            timeout=15 * 60,
        )
        elapsed = time.monotonic() - started
        assert r.returncode == 0, f"helm install failed after {elapsed:.0f}s:\n{r.stdout}\n{r.stderr}"
        logger.info("helm install --wait returned deployed after %.0f s", elapsed)
        return elapsed

    def uninstall(self) -> float:
        started = time.monotonic()
        r = _run(
            self._cmd("uninstall", RELEASE, "--namespace", NAMESPACE, "--wait", "--timeout", UNINSTALL_TIMEOUT),
            timeout=10 * 60,
        )
        elapsed = time.monotonic() - started
        assert r.returncode == 0, f"helm uninstall failed after {elapsed:.0f}s:\n{r.stdout}\n{r.stderr}"
        logger.info("helm uninstall --wait returned after %.0f s", elapsed)
        return elapsed

    def status(self) -> Dict[str, Any]:
        r = _run(self._cmd("status", RELEASE, "--namespace", NAMESPACE, "-o", "json"))
        assert r.returncode == 0, r.stderr
        return json.loads(r.stdout)

    def history(self) -> List[Dict[str, Any]]:
        r = _run(self._cmd("history", RELEASE, "--namespace", NAMESPACE, "-o", "json"))
        assert r.returncode == 0, r.stderr
        return json.loads(r.stdout)


def _kubectl_obj(kube_cluster: Cluster, args: str) -> Any:
    """One object from `kubectl get ... -o json` (pytest-helm-charts parses the
    JSON itself; a text result is parsed here for older versions)."""
    out = kube_cluster.kubectl(args, output_format="json")
    return json.loads(out) if isinstance(out, (str, bytes)) else out


def _kubectl_items(kube_cluster: Cluster, args: str) -> List[Dict[str, Any]]:
    """The items of a `kubectl get ... -o json` list (pytest-helm-charts hands
    back the unwrapped items list already; a wrapped List is unwrapped here)."""
    out = _kubectl_obj(kube_cluster, args)
    if isinstance(out, dict):
        return out.get("items", [out])
    return out


def _condition(obj: Dict[str, Any], kind: str = "Ready") -> Dict[str, Any]:
    for c in obj.get("status", {}).get("conditions", []) or []:
        if c.get("type") == kind:
            return c
    return {}


def _crd_names(kube_cluster: Cluster) -> List[str]:
    return [i["metadata"]["name"] for i in _kubectl_items(kube_cluster, "get crd")]


def _dump_debug(kube_cluster: Cluster) -> None:
    """Best-effort state dump when an assertion fails, so the CI log explains itself."""
    for cmd in (
        f"-n {NAMESPACE} get fluxinstance flux -o yaml",
        f"-n {NAMESPACE} get helmreleases.helm.toolkit.fluxcd.io -o wide",
        f"-n {NAMESPACE} get ocirepositories.source.toolkit.fluxcd.io -o wide",
        f"-n {NAMESPACE} get pods -o wide",
        f"-n {NAMESPACE} get events --sort-by=.lastTimestamp",
        f"-n {NAMESPACE} logs deployment/flux-operator --tail=60",
        f"-n {NAMESPACE} logs deployment/helm-controller --tail=60",
    ):
        try:
            logger.error("$ kubectl %s\n%s", cmd, kube_cluster.kubectl(cmd, output_format=""))
        except Exception as exc:  # diagnostics must never mask the assertion
            logger.error("kubectl %s failed: %s", cmd, exc)


def assert_platform_running(kube_cluster: Cluster, helm: Helm) -> None:
    """What `helm install --wait` promises: deployed, engine Ready, components Ready."""
    try:
        status = helm.status()
        assert status["info"]["status"] == "deployed", status["info"]

        fi = _kubectl_obj(kube_cluster, f"-n {NAMESPACE} get fluxinstance flux")
        ready = _condition(fi)
        assert ready.get("status") == "True", f"FluxInstance not Ready: {ready}"
        revision = fi["status"].get("lastAppliedRevision", "")
        assert revision.startswith("v2."), f"FluxInstance revision {revision!r} is not a Flux 2.x"
        logger.info("FluxInstance Ready at %s", revision)

        hrs = _kubectl_items(kube_cluster, f"-n {NAMESPACE} get helmreleases.helm.toolkit.fluxcd.io")
        by_name = {hr["metadata"]["name"]: hr for hr in hrs}
        assert set(by_name) == set(COMPONENTS), f"HelmReleases differ from the smoke values: {sorted(by_name)}"
        for name, hr in by_name.items():
            ready = _condition(hr)
            assert ready.get("status") == "True", f"HelmRelease {name} not Ready: {ready}"
            assert hr["spec"].get("serviceAccountName") == TENANT_SA, (
                f"HelmRelease {name} does not run as {TENANT_SA}: {hr['spec'].get('serviceAccountName')!r}"
            )
        logger.info("component HelmReleases Ready as %s: %s", TENANT_SA, sorted(by_name))

        # The engine's controllers and the components' workloads are up.
        deployments = {
            d.name: d for d in pykube.Deployment.objects(kube_cluster.kube_client).filter(namespace=NAMESPACE)
        }
        for name in ("flux-operator", "source-controller", "helm-controller", "muster"):
            assert name in deployments, f"Deployment {name} missing; have {sorted(deployments)}"
            st = deployments[name].obj.get("status", {})
            assert st.get("readyReplicas", 0) >= 1, f"Deployment {name} has no ready replica: {st}"
    except AssertionError:
        _dump_debug(kube_cluster)
        raise


@pytest.fixture(scope="module")
def prerequisites(kube_cluster: Cluster) -> None:
    """The one cluster prerequisite: the Gateway API CRDs (idempotent)."""
    kube_cluster.kubectl("apply", filename=GATEWAY_API_CRDS, output_format="")


@pytest.fixture(scope="module")
def helm(kube_cluster: Cluster, chart_path: str) -> Helm:
    """``chart_path`` is the archive the CI job copied to the working directory
    ATS runs in; pytest runs in tests/ats, hence the resolution against the
    repo root."""
    archive = Path(chart_path)
    if not archive.is_absolute():
        archive = REPO_ROOT / archive
    assert archive.is_file(), f"chart archive not found: {archive}"
    return Helm(kube_cluster.kube_config_path, archive)


@pytest.fixture(scope="module")
def app_deployment(kube_cluster: Cluster, helm: Helm, prerequisites: None) -> float:
    """`helm install --wait` of the candidate, the quick start's way. Returns
    the wall-clock seconds it took."""
    assert not any(n.endswith(FLUX_CRD_SUFFIX) for n in _crd_names(kube_cluster)), (
        "the smoke needs a cluster without Flux CRDs; another Flux is present"
    )
    return helm.install()


@pytest.mark.smoke
def test_install_reaches_deployed_with_the_platform_ready(
    kube_cluster: Cluster, helm: Helm, app_deployment: float
) -> None:
    assert_platform_running(kube_cluster, helm)
    history = helm.history()
    assert len(history) == 1 and history[0]["status"] == "deployed", history
    logger.info("helm history: one revision, deployed; install took %.0f s", app_deployment)


@pytest.mark.smoke
def test_engine_objects(kube_cluster: Cluster, app_deployment: float) -> None:
    crds = _crd_names(kube_cluster)
    flux_crds = sorted(n for n in crds if n.endswith(FLUX_CRD_SUFFIX))
    assert len(flux_crds) == 7, flux_crds
    assert OPERATOR_CRDS <= set(crds), sorted(set(crds) - OPERATOR_CRDS)
    # The operator adopted the CRDs the chart brought (Helm installed them, the
    # operator manages them from here on).
    hr_crd = _kubectl_obj(kube_cluster, "get crd helmreleases.helm.toolkit.fluxcd.io --show-managed-fields")
    managers = {mf["manager"] for mf in hr_crd["metadata"].get("managedFields", [])}
    assert "flux-operator" in managers, f"the operator does not manage the Flux CRDs: {sorted(managers)}"
    # The tenant identity exists in the release namespace.
    kube_cluster.kubectl(f"-n {NAMESPACE} get serviceaccount {TENANT_SA}", output_format="")
    kube_cluster.kubectl(f"get clusterrolebinding {TENANT_SA}", output_format="")
    # No hook object lingers after a successful install (hooks are pre-delete only).
    jobs = pykube.Job.objects(kube_cluster.kube_client).filter(namespace=NAMESPACE)
    assert not [j.name for j in jobs], [j.name for j in jobs]


@pytest.mark.smoke
def test_uninstall_is_ordered_and_reinstall_is_clean(
    kube_cluster: Cluster, helm: Helm, app_deployment: float
) -> None:
    elapsed = helm.uninstall()
    assert elapsed < UNINSTALL_BUDGET_S, f"helm uninstall --wait took {elapsed:.0f}s (budget {UNINSTALL_BUDGET_S}s)"

    crds = _crd_names(kube_cluster)
    left = sorted(n for n in crds if n.endswith(FLUX_CRD_SUFFIX))
    assert not left, f"Flux CRDs left behind after the uninstall: {left}"
    assert OPERATOR_CRDS <= set(crds), "the operator CRDs must remain (Helm never deletes crds/)"
    for name in ("flux-operator", "source-controller", "helm-controller"):
        assert pykube.Deployment.objects(kube_cluster.kube_client).filter(namespace=NAMESPACE).get_or_none(name=name) is None, (
            f"Deployment {name} survived the uninstall"
        )
    jobs = pykube.Job.objects(kube_cluster.kube_client).filter(namespace=NAMESPACE)
    assert not [j.name for j in jobs], f"hook Jobs left behind: {[j.name for j in jobs]}"
    # Every state Helm 3 and 4 can list (Helm 4 dropped the --all shorthand).
    r = _run(["helm", "--kubeconfig", kube_cluster.kube_config_path, "list", "-n", NAMESPACE, "-o", "json",
              "--failed", "--pending", "--uninstalling", "--superseded"])
    assert r.returncode == 0 and json.loads(r.stdout) == [], r.stdout or r.stderr
    logger.info("uninstall clean in %.0f s: no Flux CRD, operator CRDs kept, no controller, no hook Job", elapsed)

    reinstall = helm.install()
    assert_platform_running(kube_cluster, helm)
    logger.info("reinstall reached deployed after %.0f s", reinstall)
