"""Shared fixtures and helpers of the agent-platform ATS (tests/ats/README.md).

Two scenarios run on the one kind cluster the CI job creates, in this order:

  smoke       test_smoke.py     the quick start on a bare cluster: the chart under
                                test pushed to an in-cluster registry and installed
                                with the bundled engine and self-management ON
                                against that registry — adoption, the auth round
                                trip, the agent round trips (a declarative
                                AgentTemplate Ready on the platform Harness,
                                agent-manager's create_agent through muster), the
                                fixpoint, the refused CLI, the ordered teardown
  functional  test_own_flux.py  a cluster that runs its own Flux: the chart through
                                a HelmRelease with the engine off (no operator, no
                                second helm-controller, the Flux CRDs untouched, an
                                agent through that Flux) and the render guard when
                                the value is flipped on

ATS runs `uv run pytest -m smoke` then `pytest -m functional` in this directory
with KUBECONFIG, ATS_CHART_PATH and ATS_CHART_VERSION set (docs/TEST_CONTRACT.md
in app-test-suite); ``kube_cluster`` (pytest-helm-charts) carries the
kubeconfig. Helm 4 and kubectl come with the ATS image. What the smoke leaves on
the cluster is what the functional scenario expects to find (the lab Dex, the
registry with the chart, the four operator CRDs, the kept kagent CRDs).

The platform's kagent is the kagent line (kagent API v2, kagent.dev/v1alpha3):
agents are AgentTemplates a Harness admits and runs as Agent Substrate actors in
gVisor worker pods. Both scenarios get Substrate from the chart under test and
assert readiness on the platform Harness (AgentTemplate.status.harnesses[]); the
CI job's kind cluster carries the feature gates Substrate needs
(.ats/kind-config.yaml), on the `large` class (tests/ats/README.md).

Local run (a throwaway kind cluster; the lab URLs carry fixed ports, so 5554 and
8090 must be free — ATS_MUSTER_PORT picks another muster port, see lab-dex.yaml):

  helm package helm/agent-platform --version 3.99.0-dev.local -d dist
  cd tests/ats && KUBECONFIG=… ATS_CHART_PATH=$PWD/../../dist/agent-platform-3.99.0-dev.local.tgz \
    ATS_CHART_VERSION=3.99.0-dev.local ATS_CLUSTER_TYPE=kind uv run pytest -m smoke --log-cli-level info
"""

import base64
import hashlib
import html
import json
import logging
import os
import re
import secrets
import socket
import subprocess  # nosec: fixed argv throughout; the archive path comes from ATS
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit

import pytest
import requests
import yaml
from pytest_helm_charts.clusters import Cluster

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[2]
ATS_DIR = Path(__file__).resolve().parent

RELEASE = "agent-platform"
NAMESPACE = "agent-platform"
KAGENT_NAMESPACE = "kagent"
TENANT_SA = "agent-platform-flux"
KAGENT_FLUX_SA = "kagent-flux"
# kagent API v2: the platform Harness the connectivity chart renders in the
# kagent namespace, the label that places an AgentTemplate on it (the whole
# admission contract; the agent chart 1.x sets it), the Substrate WorkerPool the
# Harness runs on (the kagent chart renders it, sized in values-kagent.yaml) and
# the namespace of Substrate's control plane.
HARNESS = "kagent"
HARNESS_LABEL = "agent-platform.giantswarm.io/harness"
WORKER_POOL = "kagent-default"
ATE_NAMESPACE = "ate-system"
# The kagent line's CRDs: their templates carry helm.sh/resource-policy: keep, so
# uninstalling the kagent-crds release leaves them — and every AgentTemplate and
# RemoteMCPServer — in place (Substrate's ate.dev CRDs carry no such policy).
KAGENT_CRDS = {f"{plural}.kagent.dev" for plural in ("agenttemplates", "harnesses", "modelconfigs", "modelproviderconfigs", "remotemcpservers")}
# The chart's default ModelConfig every agent of the smoke starts from, and the
# provider Secret it references: a placeholder key is enough for a template to
# reach Ready (Ready means the Harness booted the actor's golden snapshot, not
# that a model answered).
MODEL_CONFIG = "default-model-config"
PLACEHOLDER_PROVIDER_SECRET = {"name": "kagent-anthropic", "key": "ANTHROPIC_API_KEY"}
# The Generic agent chart, 1.x = kagent API v2 (0.x rendered the retired Agent).
AGENT_CHART_URL = "oci://gsoci.azurecr.io/charts/giantswarm/agent"
AGENT_CHART_SEMVER = "1.x"
# The toolset of the smoke's managed agents: a shipped muster preset, so the
# agent chart renders the agent's own RemoteMCPServer — the toolset carrier,
# the X-Muster-Toolset header on it; ["preset:none"] alone renders none.
TOOLSET = ["preset:read-only"]
SELF_SA = f"{RELEASE}-self"
SELF_POLICY = f"{RELEASE}-self-managed-{NAMESPACE}"
VALUES_SECRET = "agent-platform-values"
FLUX_CRD_SUFFIX = ".toolkit.fluxcd.io"
OPERATOR_CRDS = {
    "fluxinstances.fluxcd.controlplane.io",
    "fluxreports.fluxcd.controlplane.io",
    "resourcesets.fluxcd.controlplane.io",
    "resourcesetinputproviders.fluxcd.controlplane.io",
}
GATEWAY_API_CRDS = (
    "https://github.com/kubernetes-sigs/gateway-api/releases/download/"
    "v1.5.0/standard-install.yaml"
)

# Values: the lab shape (tests/test-values.yaml, also the third fleet-render
# shape), the kagent runtime and the round trips layered over it.
BASE_VALUES = REPO_ROOT / "tests" / "test-values.yaml"
KAGENT_VALUES = ATS_DIR / "values-kagent.yaml"
ROUND_TRIP_VALUES = ATS_DIR / "values-round-trips.yaml"
SMOKE_VALUES = [BASE_VALUES, KAGENT_VALUES, ROUND_TRIP_VALUES]

# The lab Dex (tests/ats/lab-dex.yaml). Public lab credentials by design.
LAB_DEX_MANIFEST = ATS_DIR / "lab-dex.yaml"
DEX_ISSUER = "https://dex.127.0.0.1.nip.io:5554"
DEX_PORT = 5554
DEX_USER = "admin@example.com"
DEX_PASSWORD = "password"
CLIENT_ID = "agent-platform"
CLIENT_SECRET = "lab-only-agent-platform-client-secret"
REGISTRATION_TOKEN = "lab-only-registration-token"
# The cross-client audience the platform's agent-manager MCPServer requires on
# the token muster forwards (agent-manager.muster.mcpServer.auth.requiredAudiences).
CROSS_CLIENT_AUDIENCE = "dex-k8s-authenticator"
LOGIN_SCOPES = f"openid profile email groups audience:server:client_id:{CROSS_CLIENT_AUDIENCE}"
# muster's OAuth base URL in the smoke values: a loopback address, reached
# through the port-forward to svc/muster. The port is part of the URL, so it is
# the same inside the values and outside: 8090 in CI (tests/ats/values-round-trips.yaml);
# ATS_MUSTER_PORT=18090 on a developer machine where 8090 is taken (the lab Dex
# client lists both callbacks, and the install then overrides the base URL).
MUSTER_PORT = int(os.environ.get("ATS_MUSTER_PORT", "8090"))
MUSTER_BASE_URL = f"http://localhost:{MUSTER_PORT}"
MUSTER_BASE_URL_SETS = [] if MUSTER_PORT == 8090 else [f"muster.muster.oauth.server.baseUrl={MUSTER_BASE_URL}"]
# Loopback redirect target of the smoke's OAuth client. Never served: the flow
# stops at the redirect and parses the code from Location.
CALLBACK = "http://127.0.0.1:18763/callback"

# The in-cluster registry (tests/ats/registry.yaml).
REGISTRY_MANIFEST = ATS_DIR / "registry.yaml"
REGISTRY_NAMESPACE = "registry"
REGISTRY_PORT = 5000
REGISTRY_URL = f"oci://registry.{REGISTRY_NAMESPACE}.svc.cluster.local:{REGISTRY_PORT}/charts"
# muster's MCP endpoint as in-cluster callers reach it: the URL every agent's
# RemoteMCPServer carries (the agent chart's default, what agent-manager composes).
MUSTER_MCP_URL = f"http://muster.{NAMESPACE}.svc.cluster.local:8090/mcp"

INSTALL_TIMEOUT = "12m"
UNINSTALL_TIMEOUT = "5m"
# The ordered teardown's budget. Measured: 12–16 s with muster + dicebear +
# connectivity (the shape PRD Q9's "under a minute" was measured on); with
# kagent, Substrate and agent-manager on the teardown uninstalls seven more
# releases in dependency order (managers, kagent, substrate, connectivity, the
# two CRD charts) and their pods, PVCs and the WorkerPool's workers go with them
# — measured 33 s on CI (tests/ats/README.md); the budget is twice that, rounded up.
UNINSTALL_BUDGET_S = 120
# Self-management in the smoke: the chart's own OCIRepository follows the
# in-cluster registry the candidate was pushed to, at the candidate's exact
# version. Exact, not the chart's derived range: a branch build carries a
# prerelease version (3.19.1-dev.<branch>.<date>.h<sha>, abs), and Masterminds
# semver — Flux's — never matches a prerelease against a release-only bound
# (`>=X <4.0.0`), so the derived range would find no tag. A released chart
# has no prerelease; verify-self asserts the derived range offline.
SELF_INTERVAL = "1m"
SELF_INTERVAL_S = 60


def self_management_sets(version: str) -> List[str]:
    return [
        "gitops.self.enabled=true",
        f"gitops.self.repository={REGISTRY_URL}",
        "gitops.self.insecure=true",
        f"gitops.self.interval={SELF_INTERVAL}",
        f"gitops.self.versionRange={version}",
    ]


# The connectivity chart of this checkout, pushed to the in-cluster registry
# next to the meta chart (both charts release off one tag and change together;
# the published one would not carry this PR's connectivity changes). The
# component's roster entry is pointed at the registry at the same version.
CONNECTIVITY_CHART_DIR = REPO_ROOT / "helm" / "agent-platform-connectivity"
CONNECTIVITY = "agent-platform-connectivity"


def connectivity_sets(version: str) -> List[str]:
    return [
        f"components.{CONNECTIVITY}.repository={REGISTRY_URL}",
        f"components.{CONNECTIVITY}.versionRange={version}",
        f"components.{CONNECTIVITY}.insecure=true",
    ]


def connectivity_values(version: str) -> Dict[str, Any]:
    return {"components": {CONNECTIVITY: {"repository": REGISTRY_URL, "versionRange": version, "insecure": True}}}

# ---------------------------------------------------------------------------
# Processes, waiting, timing
# ---------------------------------------------------------------------------


def run(args: List[str], timeout: int = 900, stdin: Optional[str] = None) -> subprocess.CompletedProcess:
    logger.info("$ %s", " ".join(args))
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False, input=stdin)  # nosec


class Abort(Exception):
    """Raised by a wait_for predicate that has seen a terminal state (a failed
    HelmRelease, a refused install): the wait ends at once with this message
    instead of running out its timeout."""


def wait_for(what: str, predicate: Callable[[], Any], timeout: float, interval: float = 5) -> Any:
    """Poll until ``predicate`` returns a truthy value (returned) or the
    timeout passes (AssertionError naming the last outcome). Exceptions inside
    the predicate count as "not yet" — except Abort, which ends the wait."""
    deadline = time.monotonic() + timeout
    last: Any = "not evaluated"
    while True:
        try:
            value = predicate()
            if value:
                return value
            last = value
        except Abort as exc:
            raise AssertionError(f"gave up waiting for {what}: {exc}") from exc
        except Exception as exc:  # the predicate reads a cluster that is still converging
            last = f"{type(exc).__name__}: {exc}"
        if time.monotonic() >= deadline:
            raise AssertionError(f"timed out after {timeout:.0f}s waiting for {what}; last: {str(last)[:400]}")
        time.sleep(interval)


class Timings:
    """Wall-clock seconds per phase, logged for the README's timing table."""

    def __init__(self) -> None:
        self.entries: Dict[str, float] = {}

    def record(self, phase: str, seconds: float) -> None:
        self.entries[phase] = seconds
        logger.info("TIMING %s: %.0f s", phase, seconds)


TIMINGS = Timings()


def timed(phase: str) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    def wrap(fn: Callable[..., Any]) -> Callable[..., Any]:
        def inner(*args: Any, **kwargs: Any) -> Any:
            started = time.monotonic()
            try:
                return fn(*args, **kwargs)
            finally:
                TIMINGS.record(phase, time.monotonic() - started)
        return inner
    return wrap


# ---------------------------------------------------------------------------
# kubectl
# ---------------------------------------------------------------------------


class Kube:
    """kubectl against the ATS cluster, JSON in and out."""

    def __init__(self, kubeconfig: str) -> None:
        self.kubeconfig = kubeconfig

    def cmd(self, args: List[str], stdin: Optional[str] = None, check: bool = True, timeout: int = 600) -> subprocess.CompletedProcess:
        r = run(["kubectl", "--kubeconfig", self.kubeconfig, *args], timeout=timeout, stdin=stdin)
        if check and r.returncode != 0:
            raise AssertionError(f"kubectl {' '.join(args)} failed ({r.returncode}):\n{r.stdout}\n{r.stderr}")
        return r

    def text(self, args: List[str], check: bool = True) -> str:
        return self.cmd(args, check=check).stdout

    def get(self, *args: str, namespace: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """One object, or None when it does not exist."""
        ns = ["-n", namespace] if namespace else []
        r = self.cmd(["get", *ns, *args, "-o", "json", "--ignore-not-found"])
        return json.loads(r.stdout) if r.stdout.strip() else None

    def items(self, *args: str, namespace: Optional[str] = None, all_namespaces: bool = False) -> List[Dict[str, Any]]:
        ns = ["-A"] if all_namespaces else (["-n", namespace] if namespace else [])
        r = self.cmd(["get", *ns, *args, "-o", "json", "--ignore-not-found"])
        if not r.stdout.strip():
            return []
        out = json.loads(r.stdout)
        return out.get("items", [out]) if isinstance(out, dict) else out

    def apply(self, manifest: Any, server_side: bool = False) -> None:
        text = manifest if isinstance(manifest, str) else yaml.safe_dump_all(manifest) if isinstance(manifest, list) else yaml.safe_dump(manifest)
        extra = ["--server-side", "--force-conflicts"] if server_side else []
        self.cmd(["apply", *extra, "-f", "-"], stdin=text)

    def apply_file(self, path: str) -> None:
        self.cmd(["apply", "-f", path])

    def delete(self, *args: str, namespace: Optional[str] = None, wait: bool = True, timeout: str = "5m") -> None:
        ns = ["-n", namespace] if namespace else []
        self.cmd(["delete", *ns, *args, "--ignore-not-found", f"--wait={'true' if wait else 'false'}", f"--timeout={timeout}"])

    def crd_names(self) -> List[str]:
        return [i["metadata"]["name"] for i in self.items("crd")]

    def flux_crds(self) -> List[str]:
        return sorted(n for n in self.crd_names() if n.endswith(FLUX_CRD_SUFFIX))

    def managers(self, *args: str, namespace: Optional[str] = None) -> set:
        """The field managers of one object."""
        ns = ["-n", namespace] if namespace else []
        r = self.cmd(["get", *ns, *args, "-o", "json", "--show-managed-fields"])
        return {mf["manager"] for mf in json.loads(r.stdout)["metadata"].get("managedFields", [])}

    def deployment_ready(self, namespace: str, name: str) -> bool:
        d = self.get("deployment", name, namespace=namespace)
        if not d:
            return False
        st = d.get("status", {})
        return st.get("observedGeneration", 0) >= d["metadata"]["generation"] and st.get("readyReplicas", 0) >= max(d["spec"].get("replicas", 1), 1)

    def wait_deployment(self, namespace: str, name: str, timeout: float = 600) -> None:
        wait_for(f"Deployment {namespace}/{name} Ready", lambda: self.deployment_ready(namespace, name), timeout)

    def logs(self, namespace: str, target: str, tail: int = 80) -> str:
        return self.cmd(["-n", namespace, "logs", target, f"--tail={tail}", "--all-containers"], check=False).stdout

    def dump(self, commands: List[str]) -> None:
        """Best-effort state dump when an assertion fails, so the CI log explains itself."""
        for c in commands:
            r = self.cmd(c.split(" "), check=False, timeout=120)
            logger.error("$ kubectl %s\n%s%s", c, r.stdout[-6000:], r.stderr[-2000:])


def condition(obj: Optional[Dict[str, Any]], kind: str = "Ready") -> Dict[str, Any]:
    for c in (obj or {}).get("status", {}).get("conditions", []) or []:
        if c.get("type") == kind:
            return c
    return {}


def is_ready(obj: Optional[Dict[str, Any]]) -> bool:
    return condition(obj).get("status") == "True"


def is_accepted(obj: Optional[Dict[str, Any]]) -> bool:
    """A RemoteMCPServer's (or ModelConfig's) Accepted condition — with tool
    discovery off (kagent.dev/discovery: disabled, the agent chart's default)
    the controller accepts the server without dialing it."""
    return condition(obj, "Accepted").get("status") == "True"


def harness_entry(template: Optional[Dict[str, Any]], harness: str = HARNESS) -> Optional[Dict[str, Any]]:
    """The AgentTemplate's status entry for one Harness (status.harnesses[] is
    keyed by harness), or None while no Harness admits it — a template the
    platform Harness does not select (no label) has an empty list with
    observedGeneration caught up."""
    for entry in (template or {}).get("status", {}).get("harnesses", []) or []:
        if entry.get("harness") == harness:
            return entry
    return None


def harness_condition(template: Optional[Dict[str, Any]], kind: str = "Ready", harness: str = HARNESS) -> Dict[str, Any]:
    for c in (harness_entry(template, harness) or {}).get("conditions", []) or []:
        if c.get("type") == kind:
            return c
    return {}


def template_ready(template: Optional[Dict[str, Any]], harness: str = HARNESS) -> bool:
    """Ready on the platform Harness: admitted (an entry for the Harness) and
    the entry's Ready condition True — the Harness booted the actor's golden
    snapshot on the Substrate WorkerPool."""
    return harness_condition(template, "Ready", harness).get("status") == "True"


def template_state(template: Optional[Dict[str, Any]], harness: str = HARNESS) -> str:
    """One line for a failure message: admitted or not, and the conditions."""
    if not template:
        return "absent"
    entry = harness_entry(template, harness)
    if entry is None:
        status = template.get("status", {})
        return (f"not admitted by Harness {harness}: harnesses={status.get('harnesses')} "
                f"observedGeneration={status.get('observedGeneration')} generation={template['metadata'].get('generation')}")
    return ", ".join(f"{c.get('type')}={c.get('status')} ({c.get('reason')}: {str(c.get('message', ''))[:120]})" for c in entry.get("conditions", []) or []) or "admitted, no conditions yet"


# ---------------------------------------------------------------------------
# Helm
# ---------------------------------------------------------------------------


class Helm:
    def __init__(self, kubeconfig: str) -> None:
        self.kubeconfig = kubeconfig

    def _cmd(self, *args: str) -> List[str]:
        return ["helm", "--kubeconfig", self.kubeconfig, *args]

    def install(self, chart: str, values: List[Path], sets: Optional[List[str]] = None, wait: bool = True, timeout: str = INSTALL_TIMEOUT) -> float:
        """`helm install` of the candidate; returns the wall-clock seconds. Fails
        the test with Helm's output when the install fails."""
        args = ["install", RELEASE, chart, "--namespace", NAMESPACE, "--create-namespace", "--timeout", timeout]
        for v in values:
            args += ["--values", str(v)]
        for s in sets or []:
            args += ["--set", s]
        if wait:
            args.append("--wait")
        started = time.monotonic()
        r = run(self._cmd(*args), timeout=20 * 60)
        elapsed = time.monotonic() - started
        assert r.returncode == 0, f"helm install failed after {elapsed:.0f}s:\n{r.stdout}\n{r.stderr}"
        logger.info("helm install%s returned after %.0f s", " --wait" if wait else "", elapsed)
        return elapsed

    def upgrade(self, chart: str, values: List[Path], sets: Optional[List[str]] = None, timeout: str = "3m") -> subprocess.CompletedProcess:
        """`helm upgrade`, returned for the caller to judge (the refusal test expects it to fail)."""
        args = ["upgrade", RELEASE, chart, "--namespace", NAMESPACE, "--timeout", timeout]
        for v in values:
            args += ["--values", str(v)]
        for s in sets or []:
            args += ["--set", s]
        return run(self._cmd(*args), timeout=10 * 60)

    def uninstall(self) -> float:
        started = time.monotonic()
        r = run(self._cmd("uninstall", RELEASE, "--namespace", NAMESPACE, "--wait", "--timeout", UNINSTALL_TIMEOUT), timeout=10 * 60)
        elapsed = time.monotonic() - started
        assert r.returncode == 0, f"helm uninstall failed after {elapsed:.0f}s:\n{r.stdout}\n{r.stderr}"
        logger.info("helm uninstall --wait returned after %.0f s", elapsed)
        return elapsed

    def status(self) -> Dict[str, Any]:
        r = run(self._cmd("status", RELEASE, "--namespace", NAMESPACE, "-o", "json"))
        assert r.returncode == 0, r.stderr
        return json.loads(r.stdout)

    def history(self) -> List[Dict[str, Any]]:
        r = run(self._cmd("history", RELEASE, "--namespace", NAMESPACE, "-o", "json"))
        assert r.returncode == 0, r.stderr
        return json.loads(r.stdout)

    def get_values(self) -> Dict[str, Any]:
        """The user-supplied values of the release (what the values Secret must equal)."""
        r = run(self._cmd("get", "values", RELEASE, "--namespace", NAMESPACE, "-o", "yaml"))
        assert r.returncode == 0, r.stderr
        return yaml.safe_load(r.stdout) or {}

    def releases_in_any_state(self) -> List[Dict[str, Any]]:
        """Every state Helm 3 and 4 can list (Helm 4 dropped the --all shorthand)."""
        r = run(self._cmd("list", "-n", NAMESPACE, "-o", "json", "--deployed", "--failed", "--pending", "--uninstalling", "--superseded"))
        assert r.returncode == 0, r.stderr
        return json.loads(r.stdout)

    def show_version(self, archive: Path) -> str:
        r = run(["helm", "show", "chart", str(archive)])
        assert r.returncode == 0, r.stderr
        return str(yaml.safe_load(r.stdout)["version"])

    def push(self, archive: Path, registry: str) -> None:
        r = run(["helm", "push", str(archive), registry, "--plain-http"], timeout=300)
        assert r.returncode == 0, f"helm push failed:\n{r.stdout}\n{r.stderr}"
        logger.info("pushed %s to %s", archive.name, registry)

    def package(self, chart_dir: Path, version: str, dest: Path) -> Path:
        """`helm package` of a chart directory at the given version (the way abs
        stamps it); returns the archive path."""
        r = run(["helm", "package", str(chart_dir), "--version", version, "--app-version", version, "--destination", str(dest)], timeout=300)
        assert r.returncode == 0, f"helm package {chart_dir} failed:\n{r.stdout}\n{r.stderr}"
        m = re.search(r"saved it to: (\S+)", r.stdout)
        assert m, r.stdout
        return Path(m.group(1))


def deep_merge(base: Dict[str, Any], over: Dict[str, Any]) -> Dict[str, Any]:
    """Helm's values merge (maps merge recursively, everything else replaces)."""
    out = dict(base)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def set_path(values: Dict[str, Any], dotted: str, value: Any) -> None:
    keys = dotted.split(".")
    cur = values
    for k in keys[:-1]:
        cur = cur.setdefault(k, {})
    cur[keys[-1]] = value


def helm_set_value(raw: str) -> Any:
    """The type Helm gives a --set value: true/false booleans, integers, else a string."""
    if raw in ("true", "false"):
        return raw == "true"
    if re.fullmatch(r"-?\d+", raw):
        return int(raw)
    return raw


def load_values(files: List[Path], sets: Optional[List[str]] = None) -> Dict[str, Any]:
    """The user-supplied values a `helm install -f … --set …` produces (what
    `helm get values` prints and the values Secret must equal)."""
    merged: Dict[str, Any] = {}
    for f in files:
        merged = deep_merge(merged, yaml.safe_load(f.read_text()) or {})
    for item in sets or []:
        k, _, v = item.partition("=")
        set_path(merged, k, helm_set_value(v))
    return merged


# ---------------------------------------------------------------------------
# Reaching Services from the test: kubectl port-forward
# ---------------------------------------------------------------------------


class PortForward:
    """`kubectl port-forward` to a Service, on a fixed local port (the lab URLs
    carry the port, so it must be the same one inside and outside)."""

    def __init__(self, kubeconfig: str, namespace: str, service: str, local_port: int, remote_port: int) -> None:
        self.kubeconfig, self.namespace, self.service = kubeconfig, namespace, service
        self.local_port, self.remote_port = local_port, remote_port
        self._proc: Optional[subprocess.Popen] = None

    def start(self) -> "PortForward":
        self.stop()
        self._proc = subprocess.Popen(  # nosec
            ["kubectl", f"--kubeconfig={self.kubeconfig}", "-n", self.namespace, "port-forward",
             f"service/{self.service}", f"{self.local_port}:{self.remote_port}"],
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
        )
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            self._raise_if_exited()
            try:
                with socket.create_connection(("127.0.0.1", self.local_port), timeout=2):
                    pass
            except OSError:
                time.sleep(1)
                continue
            # Something answers on the port. Make sure it is OUR kubectl and not
            # another process that held the port first (kubectl then exits with
            # "address already in use" a moment later): the lab URLs are fixed,
            # so a foreign listener would silently take every request.
            time.sleep(1)
            self._raise_if_exited()
            return self
        raise AssertionError(f"port-forward to {self.namespace}/{self.service} did not start listening on {self.local_port}")

    def _raise_if_exited(self) -> None:
        if self._proc is not None and self._proc.poll() is not None:
            err = (self._proc.stderr.read() if self._proc.stderr else "")[:500]
            raise AssertionError(
                f"port-forward to {self.namespace}/{self.service} on local port {self.local_port} exited with {self._proc.returncode}: {err}\n"
                f"(a process already listening on {self.local_port}? the lab URLs carry the port — free it, or set ATS_MUSTER_PORT / ATS_DEX_PORT to a free one)")

    def alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def ensure(self) -> None:
        if not self.alive():
            logger.warning("port-forward to %s/%s is gone; respawning it", self.namespace, self.service)
            self.start()

    def stop(self) -> None:
        if self._proc is not None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self._proc.kill()
            self._proc = None


def wait_for_substrate(kube: Kube, timeout: float = 600) -> Dict[str, Any]:
    """Agent Substrate, installed by the chart under test, ready to run actors:
    the control plane's Deployments in ate-system, the WorkerPool of the kagent
    namespace with every worker Ready (the gVisor worker pods atelet admitted),
    and the platform Harness the connectivity release rendered. Returns the
    WorkerPool. Not part of `helm install --wait`: the kagent HelmRelease is
    Ready when the kagent chart's workloads are, the WorkerPool is a CR whose
    workers come up behind it."""
    started = time.monotonic()
    for name in ("ate-api-server", "ate-controller"):
        kube.wait_deployment(ATE_NAMESPACE, name, timeout=timeout)

    def workers_ready() -> Any:
        wp = kube.get("workerpools.ate.dev", WORKER_POOL, namespace=KAGENT_NAMESPACE)
        want = (wp or {}).get("spec", {}).get("replicas", 0)
        return wp if wp and want and wp.get("status", {}).get("readyReplicas", 0) >= want else False

    wp = wait_for(f"WorkerPool {KAGENT_NAMESPACE}/{WORKER_POOL} with every worker Ready", workers_ready, timeout)
    harness = kube.get("harnesses.kagent.dev", HARNESS, namespace=KAGENT_NAMESPACE)
    assert harness, f"the connectivity release rendered no Harness {HARNESS} in {KAGENT_NAMESPACE}"
    assert harness["spec"]["substrate"]["workerPoolRef"]["name"] == WORKER_POOL, harness["spec"]["substrate"]
    assert harness["spec"]["allowedAgentTemplates"]["selector"]["matchLabels"] == {HARNESS_LABEL: HARNESS}, harness["spec"]["allowedAgentTemplates"]
    TIMINGS.record(f"Substrate ready (ate-system control plane, WorkerPool {WORKER_POOL} {wp['status'].get('readyReplicas')}/{wp['spec']['replicas']} workers, Harness {HARNESS})", time.monotonic() - started)
    logger.info("Substrate ready: WorkerPool %s %s/%s workers on %s, Harness %s on %s",
                WORKER_POOL, wp["status"].get("readyReplicas"), wp["spec"]["replicas"], wp["spec"].get("workerImage"), HARNESS, harness["spec"]["workload"]["image"])
    return wp


def wait_for_template_ready(kube: Kube, name: str, timeout: float = 600) -> Dict[str, Any]:
    """The AgentTemplate Ready on the platform Harness; the failure message
    carries the template's state (not admitted, or which condition is off)."""
    def ready() -> Any:
        template = kube.get("agenttemplates.kagent.dev", name, namespace=KAGENT_NAMESPACE)
        return template if template_ready(template) else False

    try:
        return wait_for(f"AgentTemplate {name} Ready on Harness {HARNESS}", ready, timeout)
    except AssertionError as exc:
        template = kube.get("agenttemplates.kagent.dev", name, namespace=KAGENT_NAMESPACE)
        raise AssertionError(f"{exc}; AgentTemplate {name}: {template_state(template)}") from exc


def assert_remote_mcp_server(kube: Kube, name: str, toolset: Optional[List[str]] = None) -> Dict[str, Any]:
    """The agent's own RemoteMCPServer — the toolset carrier the agent chart
    renders next to the AgentTemplate: muster's in-cluster URL, the toolset as
    the X-Muster-Toolset header, controller-side tool discovery off (agents
    resolve the tools at run time as the caller), never a static Authorization
    header (the Harness propagates the caller's token; a static one would make
    every user act as one identity) — and Accepted by the controller."""
    toolset = TOOLSET if toolset is None else toolset

    def accepted() -> Any:
        server = kube.get("remotemcpservers.kagent.dev", name, namespace=KAGENT_NAMESPACE)
        return server if is_accepted(server) else False

    server = wait_for(f"RemoteMCPServer {name} Accepted", accepted, 300)
    spec = server["spec"]
    assert spec.get("url") == MUSTER_MCP_URL, spec.get("url")
    assert spec.get("protocol") == "STREAMABLE_HTTP", spec.get("protocol")
    headers = {h.get("name"): h.get("value") for h in spec.get("headersFrom", []) or []}
    assert headers.get("X-Muster-Toolset") == ",".join(toolset), headers
    assert "Authorization" not in headers, f"a static Authorization header on RemoteMCPServer {name}: it would override the propagated caller token"
    assert (server["metadata"].get("labels") or {}).get("kagent.dev/discovery") == "disabled", server["metadata"].get("labels")
    return server


def apply_placeholder_provider_secret(kube: Kube) -> None:
    """The Secret the chart's default ModelConfig references, with a placeholder
    key: enough for the controller to resolve the ModelConfig and for a template
    to reach Ready (no model is called)."""
    kube.apply({"apiVersion": "v1", "kind": "Secret", "metadata": {"name": PLACEHOLDER_PROVIDER_SECRET["name"], "namespace": KAGENT_NAMESPACE},
                "stringData": {PLACEHOLDER_PROVIDER_SECRET["key"]: "lab-only-placeholder-key"}})


def dump_agents(kube: Kube) -> None:
    """The agent path's state when an assertion fails: the kagent API v2 objects,
    Substrate's control plane and the WorkerPool's workers, their logs."""
    kube.dump([
        f"-n {KAGENT_NAMESPACE} get agenttemplates.kagent.dev,remotemcpservers.kagent.dev,modelconfigs.kagent.dev -o yaml",
        f"-n {KAGENT_NAMESPACE} get harnesses.kagent.dev,workerpools.ate.dev -o yaml",
        f"-n {KAGENT_NAMESPACE} get helmreleases.helm.toolkit.fluxcd.io,ocirepositories.source.toolkit.fluxcd.io -o wide",
        f"-n {KAGENT_NAMESPACE} get pods -o wide",
        f"-n {ATE_NAMESPACE} get pods -o wide",
        f"-n {KAGENT_NAMESPACE} get events --sort-by=.lastTimestamp",
        f"-n {ATE_NAMESPACE} get events --sort-by=.lastTimestamp",
        f"-n {KAGENT_NAMESPACE} logs deployment/kagent-controller --tail=80",
        f"-n {ATE_NAMESPACE} logs deployment/ate-controller --tail=60",
        f"-n {ATE_NAMESPACE} logs daemonset/atelet --tail=60",
    ])


def wait_for_endpoints(kube: Kube, namespace: str, service: str, timeout: float = 600) -> None:
    """A port-forward against a Service without ready pods exits at once."""
    def ready() -> bool:
        ep = kube.get("endpoints", service, namespace=namespace)
        return bool(ep and any(s.get("addresses") for s in ep.get("subsets") or []))
    wait_for(f"ready endpoints of {namespace}/{service}", ready, timeout)


# ---------------------------------------------------------------------------
# The lab Dex and muster's MCP endpoint
# ---------------------------------------------------------------------------


def dex_password_grant(ca_path: str, scope: str = LOGIN_SCOPES, user: str = DEX_USER, password: str = DEX_PASSWORD) -> str:
    """The OAuth password grant against the lab Dex (oauth2.passwordConnector:
    local); returns the raw id_token — headless CI login in one request."""
    r = requests.post(
        f"{DEX_ISSUER}/token",
        auth=(CLIENT_ID, CLIENT_SECRET),
        data={"grant_type": "password", "username": user, "password": password, "scope": scope},
        verify=ca_path, timeout=30,
    )
    assert r.status_code == 200, f"Dex password grant failed: {r.status_code} {r.text[:300]}"
    token = r.json().get("id_token")
    assert token, f"no id_token in Dex's answer: {r.text[:300]}"
    return token


def jwt_claims(token: str) -> Dict[str, Any]:
    payload = token.split(".")[1]
    payload += "=" * (-len(payload) % 4)
    return json.loads(base64.urlsafe_b64decode(payload))


def parse_mcp_response(resp: requests.Response) -> Dict[str, Any]:
    """One JSON-RPC response, whether muster answered JSON or an SSE stream."""
    ctype = resp.headers.get("Content-Type", "")
    if "text/event-stream" in ctype:
        last: Optional[Dict[str, Any]] = None
        for line in resp.text.splitlines():
            if line.startswith("data:"):
                try:
                    last = json.loads(line[5:].strip())
                except json.JSONDecodeError:
                    continue
        assert last is not None, f"no JSON-RPC message in the SSE stream: {resp.text[:300]}"
        return last
    return resp.json()


class MusterSession:
    """One MCP Streamable-HTTP session against muster with a bearer token —
    the path every MCP client takes (Claude Code after its login, the portal,
    an agent's kagent runtime with the propagated user token)."""

    def __init__(self, base_url: str, token: str, client_name: str = "ats") -> None:
        self.url = f"{base_url}/mcp"
        self.token = token
        self.client_name = client_name
        self.session_id = ""
        self.seq = 0
        self.tools: List[str] = []

    def post(self, payload: Dict[str, Any], with_session: bool = True) -> requests.Response:
        headers = {"Authorization": f"Bearer {self.token}", "Content-Type": "application/json",
                   "Accept": "application/json, text/event-stream"}
        if with_session and self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        return requests.post(self.url, headers=headers, data=json.dumps(payload), timeout=120)

    def initialize(self) -> "MusterSession":
        r = self.post({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": self.client_name, "version": "1"}}}, with_session=False)
        assert r.status_code == 200, f"/mcp initialize with a token: {r.status_code} {r.text[:300]}"
        self.session_id = r.headers.get("Mcp-Session-Id", "")
        assert self.session_id, f"no MCP session id — muster rejected the token: {r.text[:300]}"
        self.post({"jsonrpc": "2.0", "method": "notifications/initialized"})
        self.seq = 1
        return self

    def request(self, method: str, params: Dict[str, Any]) -> Dict[str, Any]:
        self.seq += 1
        r = self.post({"jsonrpc": "2.0", "id": self.seq, "method": method, "params": params})
        assert r.status_code == 200, f"{method}: {r.status_code} {r.text[:300]}"
        msg = parse_mcp_response(r)
        assert "error" not in msg, f"muster {method} error: {msg['error']}"
        return msg["result"]

    def list_tools(self) -> List[str]:
        names: List[str] = []
        cursor: Optional[str] = None
        while True:
            result = self.request("tools/list", {"cursor": cursor} if cursor else {})
            names += [t["name"] for t in result.get("tools", [])]
            cursor = result.get("nextCursor")
            if not cursor:
                break
        self.tools = names
        return names

    def call(self, name: str, arguments: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        return self.request("tools/call", {"name": name, "arguments": arguments or {}})

    def aggregated_tools(self) -> List[str]:
        """The tools muster aggregates for this session — its own core_* tools
        and every connected server's x_<server>_<tool> — from the list_tools
        meta-tool. tools/list carries only the meta-tools (list_tools,
        describe_tool, filter_tools, call_tool, …); the aggregated tools are
        called through call_tool."""
        listing = json.loads(self.text_of(self.call("list_tools")))
        return [t["name"] for t in (listing.get("tools", []) if isinstance(listing, dict) else listing)]

    @staticmethod
    def text_of(result: Dict[str, Any]) -> str:
        return "".join(c.get("text", "") for c in result.get("content", []) if c.get("type") == "text")

    def call_server_tool(self, name: str, arguments: Optional[Dict[str, Any]] = None) -> str:
        """One aggregated server tool (x_<server>_<tool>): directly when muster
        lists it, else through muster's call_tool meta-tool (the toolset path,
        whose envelope in result.content[0].text carries the tool's own content
        and isError). Returns the tool's text payload; raises on isError."""
        if not self.tools:
            self.list_tools()
        if name in self.tools:
            result = self.call(name, arguments)
            text = self.text_of(result)
            assert not result.get("isError"), f"{name} failed: {text[:600]}"
            return text
        outer = self.call("call_tool", {"name": name, "arguments": arguments or {}})
        inner_text = self.text_of(outer)
        try:
            env = json.loads(inner_text)
            text = "".join(c.get("text", "") for c in env.get("content", []))
            is_error = bool(env.get("isError"))
        except (json.JSONDecodeError, AttributeError):
            text, is_error = inner_text, bool(outer.get("isError"))
        assert not is_error, f"{name} failed: {text[:600]}"
        return text

    def call_server_json(self, name: str, arguments: Optional[Dict[str, Any]] = None) -> Any:
        text = self.call_server_tool(name, arguments)
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise AssertionError(f"{name} returned no JSON: {text[:300]}") from exc


def unauthenticated_mcp_challenge(base_url: str) -> Dict[str, Any]:
    """No token -> 401 with the RFC 9728 WWW-Authenticate discovery chain;
    returns the authorization-server metadata the chain leads to."""
    # A well-formed initialize without a token (muster validates the content
    # type before the bearer: a bare POST is a 400, not the challenge).
    r = requests.post(f"{base_url}/mcp", headers={"Content-Type": "application/json", "Accept": "application/json, text/event-stream"},
                      data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
                          "protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "ats", "version": "1"}}}), timeout=30)
    assert r.status_code == 401, f"expected 401, got {r.status_code}: {r.text[:300]}"
    challenge = r.headers.get("WWW-Authenticate", "")
    prm_url = f"{base_url}/.well-known/oauth-protected-resource"
    assert f'resource_metadata="{prm_url}"' in challenge, challenge
    assert 'error="invalid_token"' in challenge, challenge
    prm = requests.get(prm_url, timeout=30)
    assert prm.status_code == 200, prm.text[:300]
    servers = prm.json()["authorization_servers"]
    assert servers == [base_url], servers
    asm = requests.get(f"{servers[0]}/.well-known/oauth-authorization-server", timeout=30)
    assert asm.status_code == 200, asm.text[:300]
    meta = asm.json()
    for key in ("authorization_endpoint", "token_endpoint", "registration_endpoint"):
        assert key in meta, f"{key} missing from AS metadata: {meta}"
    return meta


def login_through_muster(base_url: str, ca_path: str) -> str:
    """The full muster OAuth flow with a lab Dex static user, headless: RFC 7591
    dynamic client registration (with the lab registration token), the
    authorization code + PKCE dance, the Dex login form — the same path a
    browser login takes, minus the browser. Returns muster's access token."""
    r = requests.post(
        f"{base_url}/oauth/register",
        headers={"Authorization": f"Bearer {REGISTRATION_TOKEN}"},
        json={"client_name": "ats-smoke", "redirect_uris": [CALLBACK], "token_endpoint_auth_method": "client_secret_basic",
              "grant_types": ["authorization_code"], "response_types": ["code"]},
        timeout=30,
    )
    assert r.status_code in (200, 201), f"DCR failed: {r.status_code} {r.text[:300]}"
    client_id, client_secret = r.json()["client_id"], r.json()["client_secret"]

    verifier = base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode()
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state = secrets.token_urlsafe(24)  # muster rejects a state shorter than 24 characters
    session = requests.Session()
    session.verify = ca_path  # the lab CA for Dex; ignored on muster's plain-HTTP base URL
    url = f"{base_url}/oauth/authorize?" + urlencode({
        "response_type": "code", "client_id": client_id, "redirect_uri": CALLBACK, "state": state,
        "code_challenge": challenge, "code_challenge_method": "S256", "scope": "openid profile email groups"})
    # Walk the redirects by hand: muster -> Dex -> login form -> (approval
    # skipped) -> muster callback -> our loopback redirect, never fetched.
    for _ in range(15):
        if url.startswith(CALLBACK):
            break
        r = session.get(url, allow_redirects=False, timeout=30)
        if r.status_code in (301, 302, 303, 307, 308):
            url = urljoin(url, r.headers["Location"])
            continue
        assert r.status_code == 200, f"{url} -> {r.status_code}: {r.text[:300]}"
        action = re.search(r'action="([^"]+)"', r.text)
        assert action, f"no form to submit on {url}: {r.text[:300]}"
        post_url = urljoin(url, html.unescape(action.group(1)))
        r = session.post(post_url, data={"login": DEX_USER, "password": DEX_PASSWORD}, allow_redirects=False, timeout=30)
        assert r.status_code in (302, 303), f"Dex login failed: {r.status_code} {r.text[:300]}"
        url = urljoin(post_url, r.headers["Location"])
    else:
        raise AssertionError(f"OAuth flow never reached the redirect URI: {url}")
    query = parse_qs(urlsplit(url).query)
    assert query.get("state") == [state], f"state mismatch in {url}"
    assert "code" in query, f"authorization did not yield a code: {url}"
    r = requests.post(
        f"{base_url}/oauth/token", auth=(client_id, client_secret),
        data={"grant_type": "authorization_code", "code": query["code"][0], "redirect_uri": CALLBACK,
              "client_id": client_id, "code_verifier": verifier},
        timeout=30,
    )
    assert r.status_code == 200, f"token exchange failed: {r.status_code} {r.text[:300]}"
    return r.json()["access_token"]


def wait_for_muster_healthy(base_url: str, timeout: float = 600) -> None:
    """muster's OAuth server answers 503 until OIDC discovery against the lab
    Dex succeeds (CoreDNS rewrite, TLS, the CA) — poll /health until ok."""
    def ok() -> bool:
        r = requests.get(f"{base_url}/health", timeout=10)
        return r.ok and r.json().get("status") == "ok"
    wait_for("muster /health ok", ok, timeout, interval=5)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module", autouse=True)
def log_heartbeat() -> Iterator[None]:
    """One log line a minute so CircleCI's no-output timeout never fires during
    a silent `helm install --wait` or a fixpoint wait."""
    stop = threading.Event()

    def beat() -> None:
        minutes = 0
        while not stop.wait(60):
            minutes += 1
            logger.info("heartbeat: %d min elapsed, still waiting/working", minutes)

    threading.Thread(target=beat, name="log-heartbeat", daemon=True).start()
    yield
    stop.set()


@pytest.fixture(scope="module")
def kube(kube_cluster: Cluster) -> Kube:
    return Kube(kube_cluster.kube_config_path)


@pytest.fixture(scope="module")
def helm(kube_cluster: Cluster) -> Helm:
    return Helm(kube_cluster.kube_config_path)


@pytest.fixture(scope="module")
def chart_archive(chart_path: str) -> Path:
    """The archive the CI job copied to the working directory ATS runs in;
    pytest runs in tests/ats, hence the resolution against the repo root."""
    archive = Path(chart_path)
    if not archive.is_absolute():
        archive = REPO_ROOT / archive
    assert archive.is_file(), f"chart archive not found: {archive}"
    return archive


@pytest.fixture(scope="module")
def candidate_version(helm: Helm, chart_archive: Path, chart_version: str) -> str:
    """The version of the chart under test (abs stamps a dev version on branches)."""
    return chart_version or helm.show_version(chart_archive)


@pytest.fixture(scope="module")
def prerequisites(kube: Kube) -> None:
    """The cluster prerequisites, the quick start's order, idempotent: the
    Gateway API CRDs (the one prerequisite the chart does not bring), the lab
    Dex with its certificate Job, then CoreDNS restarted so the rewrite is
    live before muster resolves the issuer."""
    started = time.monotonic()
    kube.apply_file(GATEWAY_API_CRDS)
    # The ATS image applies the CRD families a Giant Swarm cluster serves
    # (Kyverno, Cilium, prometheus-operator, Gateway API, …) to the kind cluster
    # before the tests, so the cluster-shape knobs resolve the FLEET shape here:
    # the connectivity release renders Kyverno objects, among them a
    # PolicyException in the policy-exceptions namespace — which only Giant Swarm
    # clusters have. ATS creates it on its own deploy path; the smoke installs
    # the chart itself, so it creates it too (the standalone smoke did the same).
    kube.apply({"apiVersion": "v1", "kind": "Namespace", "metadata": {"name": "policy-exceptions"}})
    kube.apply_file(str(LAB_DEX_MANIFEST))
    kube.cmd(["-n", NAMESPACE, "wait", "--for=condition=complete", "--timeout=300s", "job/lab-dex-cert-gen"])
    kube.cmd(["-n", "kube-system", "rollout", "restart", "deployment", "coredns"])
    kube.cmd(["-n", "kube-system", "rollout", "status", "deployment", "coredns", "--timeout=120s"])
    kube.wait_deployment(NAMESPACE, "lab-dex", timeout=300)
    TIMINGS.record("prerequisites (Gateway API CRDs, lab Dex, CoreDNS)", time.monotonic() - started)


@pytest.fixture(scope="module")
def dex_ca(kube: Kube, prerequisites: None, tmp_path_factory: pytest.TempPathFactory) -> str:
    """The lab CA, for the test's own TLS connections to Dex."""
    secret = kube.get("secret", "agent-platform-idp-ca", namespace=NAMESPACE)
    assert secret, "the lab CA Secret agent-platform-idp-ca is missing"
    path = tmp_path_factory.mktemp("lab-ca") / "ca.crt"
    path.write_bytes(base64.b64decode(secret["data"]["ca.crt"]))
    return str(path)


@pytest.fixture(scope="module")
def dex_forward(kube: Kube, prerequisites: None) -> Iterator[PortForward]:
    """https://dex.127.0.0.1.nip.io:5554 from the test: the port-forward on the issuer's port."""
    wait_for_endpoints(kube, NAMESPACE, "lab-dex")
    pf = PortForward(kube.kubeconfig, NAMESPACE, "lab-dex", DEX_PORT, DEX_PORT).start()
    yield pf
    pf.stop()


@pytest.fixture(scope="module")
def registry(kube: Kube) -> str:
    """The in-cluster registry (idempotent); returns its in-cluster OCI URL."""
    started = time.monotonic()
    kube.apply_file(str(REGISTRY_MANIFEST))
    kube.wait_deployment(REGISTRY_NAMESPACE, "registry", timeout=300)
    TIMINGS.record("registry (Deployment ready)", time.monotonic() - started)
    return REGISTRY_URL


@pytest.fixture(scope="module")
def muster_forward(kube: Kube) -> Iterator[PortForward]:
    """http://localhost:<MUSTER_PORT> from the test: muster's OAuth base URL."""
    wait_for_endpoints(kube, NAMESPACE, "muster")
    pf = PortForward(kube.kubeconfig, NAMESPACE, "muster", MUSTER_PORT, 8090).start()
    yield pf
    pf.stop()


@pytest.fixture(scope="module")
def pushed_chart(kube: Kube, helm: Helm, registry: str, chart_archive: Path, candidate_version: str, tmp_path_factory: pytest.TempPathFactory) -> str:
    """The charts under test in the in-cluster registry: the meta chart archive
    ATS hands over, and the connectivity chart packaged from this checkout at
    the same version (pushed once; a second module finds the tags and skips the
    push). Returns the in-cluster OCI URL of the repository the charts are in."""
    started = time.monotonic()
    wait_for_endpoints(kube, REGISTRY_NAMESPACE, "registry")
    pf = PortForward(kube.kubeconfig, REGISTRY_NAMESPACE, "registry", REGISTRY_PORT, REGISTRY_PORT).start()

    def tags(name: str) -> List[str]:
        r = requests.get(f"http://127.0.0.1:{REGISTRY_PORT}/v2/charts/{name}/tags/list", timeout=30)
        return (r.json().get("tags") or []) if r.status_code == 200 else []

    try:
        for name, archive in ((RELEASE, chart_archive), (CONNECTIVITY, None)):
            if candidate_version in tags(name):
                logger.info("%s %s is already in the registry", name, candidate_version)
                continue
            if archive is None:
                archive = helm.package(CONNECTIVITY_CHART_DIR, candidate_version, tmp_path_factory.mktemp("connectivity"))
            helm.push(archive, f"oci://127.0.0.1:{REGISTRY_PORT}/charts")
            assert candidate_version in tags(name), f"pushed tag of {name} not listed"
    finally:
        pf.stop()
    TIMINGS.record("chart push (meta archive + the connectivity chart of the checkout, helm push --plain-http)", time.monotonic() - started)
    return registry
