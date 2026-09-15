"""The scenario inputs of the agent-platform ATS (tests/ats/README.md).

The suite runs against any cluster its kubeconfig points at. What differs
between clusters is not the assertions but the inputs: which values files the
chart is installed with, which identity provider issues the tokens, how the
test reaches muster, and how long each phase may take. One ``Scenario`` carries
them.

Every field defaults to the kind lab value, so a run that sets nothing behaves
exactly as the CI job does. ``ATS_CLUSTER_TYPE`` picks the defaults (``kind``,
the default, or ``eks``) and most fields have their own ``ATS_`` variable on
top. The ports are read out of the URLs that carry them.

A fact that the chart and the suite both need is named once: ``make e2e`` sets
``E2E_ISSUER_URL``, ``E2E_CLIENT_ID``, ``E2E_IDP_CA_SECRET`` and ``E2E_DOMAIN``
for the install, ``load`` reads them as the fallback of the matching ``ATS_``
variable, and an ``ATS_`` variable that is set wins.

No secret and no real domain lives here.
"""

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Sequence
from urllib.parse import urlparse

REPO_ROOT = Path(__file__).resolve().parents[2]
ATS_DIR = Path(__file__).resolve().parent
EXAMPLES_DIR = REPO_ROOT / "helm" / "agent-platform" / "examples"

# The kind lab shape. This example file is what the smoke installs; the smoke
# asserts the path, so the example and the test cannot drift.
KIND_LAB_VALUES = EXAMPLES_DIR / "kind-lab-dex.yaml"
KAGENT_VALUES = ATS_DIR / "values-kagent.yaml"
ROUND_TRIP_VALUES = ATS_DIR / "values-round-trips.yaml"

# The lab Dex manifest (tests/ats/lab-dex.yaml). Public lab credentials by
# design: the issuer is a loopback nip.io name and Dex terminates TLS itself.
LAB_DEX_MANIFEST = ATS_DIR / "lab-dex.yaml"
LAB_DEX_HOST = "dex.127.0.0.1.nip.io"
LAB_DEX_PORT = 5554
LAB_DEX_ISSUER = f"https://{LAB_DEX_HOST}:{LAB_DEX_PORT}"
LAB_DEX_USER = "admin@example.com"
LAB_DEX_PASSWORD = "password"  # nosec: a public lab fixture, not a credential
LAB_CLIENT_ID = "agent-platform"
LAB_CLIENT_SECRET = "lab-only-agent-platform-client-secret"  # nosec: public lab fixture
LAB_REGISTRATION_TOKEN = "lab-only-registration-token"  # nosec: public lab fixture
LAB_CA_SECRET = "agent-platform-idp-ca"

# muster's OAuth base URL in the lab values: a loopback address reached through
# the port-forward to svc/muster. The port is part of the URL, so it is the
# same inside the values and outside.
LAB_MUSTER_PORT = 8090
# The loopback redirect target of the lab OAuth client. Never served: the flow
# stops at the redirect and parses the code out of Location.
LAB_CALLBACK_PORT = 18763

# muster's in-cluster Service port, the remote end of the port-forward.
MUSTER_SERVICE_PORT = 8090

# The local end of every kubectl port-forward: the address the test connects to
# once a forward is up. A property of port-forwarding, not of any one cluster.
LOOPBACK = "127.0.0.1"

# How the test reaches muster.
VIA_PORT_FORWARD = "port-forward"
VIA_HOSTNAME = "hostname"


def _env(names: Sequence[str], default: str) -> str:
    """The first of these variables that is set, or the scenario default. An
    empty variable counts as unset, so `VAR=` in a wrapper script does not blank
    a default. The chain is how one fact reaches both the chart and the suite:
    `make e2e` sets E2E_ISSUER_URL, and ATS_ISSUER_URL overrides it."""
    for name in names:
        value = os.environ.get(name)
        if value:
            return value
    return default


def _env_int(name: str, default: int) -> int:
    """An integer override, or the scenario default. A value that is not an
    integer is an error naming the variable, as every other input's is."""
    raw = _env((name,), str(default))
    try:
        return int(raw)
    except ValueError:
        raise AssertionError(f"{name}={raw!r} is not an integer") from None


_DEFAULT_PORTS = {"https": 443, "http": 80}


def _port_of(url: str) -> int:
    """The port a URL is served on: the one it carries, else its scheme's."""
    parsed = urlparse(url)
    if parsed.port:
        return parsed.port
    return _DEFAULT_PORTS.get(parsed.scheme, 443)


def _env_paths(name: str, default: List[Path]) -> List[Path]:
    """A values-file list from the environment, colon- or comma-separated.
    A relative path resolves against the repository root, so the variable reads
    the same from any working directory."""
    raw = os.environ.get(name)
    if not raw:
        return default
    out: List[Path] = []
    for part in raw.replace(",", ":").split(":"):
        part = part.strip()
        if not part:
            continue
        p = Path(part)
        out.append(p if p.is_absolute() else REPO_ROOT / p)
    return out


@dataclass(frozen=True)
class Scenario:
    """Everything the suite needs to know about one cluster and one shape."""

    name: str

    # --- what is installed ---------------------------------------------------
    # The values files, in Helm's order. The first is an example file the
    # repository ships; the overlays add the kagent runtime and the round trips.
    values: List[Path]
    # Extra values files layered last, from the environment: the overlay
    # `make e2e` writes for a real domain and a real identity provider.
    overlays: List[Path] = field(default_factory=list)

    # --- the identity provider ----------------------------------------------
    # The issuer exactly as it appears in a token's iss claim.
    issuer_url: str = LAB_DEX_ISSUER
    client_id: str = LAB_CLIENT_ID
    client_secret: str = LAB_CLIENT_SECRET
    registration_token: str = LAB_REGISTRATION_TOKEN
    # A static user the headless logins present: the OAuth password grant and
    # the login form muster redirects to. Empty means the scenario has none,
    # and conftest's REQUIRES_STATIC_USER skips the tests that log in.
    user: str = LAB_DEX_USER
    password: str = LAB_DEX_PASSWORD
    # The Secret holding the issuer's CA (key ca.crt), in the release namespace.
    # Empty means the issuer is served by a publicly trusted certificate and the
    # test uses its system trust store.
    ca_secret: str = LAB_CA_SECRET
    # Whether the run installs the lab Dex itself (lab-dex.yaml, with its
    # certificate Job and the CoreDNS rewrite).
    install_lab_dex: bool = True

    # --- reaching muster -----------------------------------------------------
    # The base URL the tests call muster on, and the base URL muster is
    # configured with: the two must agree, because muster's OAuth metadata
    # echoes its own base URL, so the install pins it with `--set`.
    muster_base_url: str = f"http://localhost:{LAB_MUSTER_PORT}"
    # port-forward: a kubectl port-forward to svc/muster on the URL's port.
    # hostname: the URL is a real hostname served through the Gateway.
    muster_reach: str = VIA_PORT_FORWARD

    # --- the OAuth redirect --------------------------------------------------
    # Never served; the flow stops at the redirect and reads the code from
    # Location. The identity provider must still list it for the client.
    callback: str = f"http://{LOOPBACK}:{LAB_CALLBACK_PORT}/callback"

    # --- budgets -------------------------------------------------------------
    install_timeout: str = "12m"
    uninstall_timeout: str = "5m"
    # The ordered teardown's budget. Measured on kind: 12-16 s with muster,
    # dicebear and connectivity; about 65 s with kagent and agent-manager on,
    # where the long pole is the kagent namespace's termination.
    uninstall_budget_s: int = 120
    # How long a Deployment or a HelmRelease may take to become Ready.
    ready_timeout_s: int = 600

    @property
    def issuer_port(self) -> int:
        """The port the issuer serves on, from its URL. With install_lab_dex it
        is also the local port of the port-forward to svc/lab-dex."""
        return _port_of(self.issuer_url)

    @property
    def muster_port(self) -> int:
        """The local port of the port-forward to svc/muster, from the base URL:
        the port is part of the URL muster is configured with."""
        return _port_of(self.muster_base_url)

    @property
    def values_files(self) -> List[Path]:
        return [*self.values, *self.overlays]

    @property
    def via_port_forward(self) -> bool:
        return self.muster_reach == VIA_PORT_FORWARD

    @property
    def base_values(self) -> Path:
        """The example file the scenario installs first."""
        return self.values[0]

    @property
    def own_flux_values(self) -> List[Path]:
        """The functional scenario's list: the example file and the kagent
        runtime, not the round trips, with the overlays layered last. A run
        that names ATS_VALUES replaces the example file only; the entries after
        it belong to the smoke."""
        return [self.base_values, KAGENT_VALUES, *self.overlays]


def _kind() -> Scenario:
    return Scenario(name="kind", values=[KIND_LAB_VALUES, KAGENT_VALUES, ROUND_TRIP_VALUES])


def _eks() -> Scenario:
    """A managed cloud cluster. It brings its own identity provider behind a
    real certificate, and muster answers on a real hostname through the
    Gateway, so nothing is port-forwarded and no lab Dex is installed.

    These are defaults only: `load` reads every field's own environment
    variable over them. The fields that name the installation default to empty
    here, and `_validate` then fails the run naming the variable."""
    return Scenario(
        name="eks",
        values=[],
        issuer_url="",
        client_id="",
        client_secret="",
        registration_token="",
        user="",
        password="",
        ca_secret="",
        install_lab_dex=False,
        muster_base_url="",
        muster_reach=VIA_HOSTNAME,
        install_timeout="20m",
        uninstall_timeout="10m",
        uninstall_budget_s=300,
        ready_timeout_s=900,
    )


DEFAULTS = {"kind": _kind, "eks": _eks}


def load() -> Scenario:
    """The scenario of this run: the ATS_CLUSTER_TYPE defaults, with every
    field overridable on its own. An unknown cluster type is an error naming the
    known ones, rather than a silent fall back to the kind lab."""
    cluster_type = _env(("ATS_CLUSTER_TYPE",), "kind").strip().lower()
    if cluster_type not in DEFAULTS:
        raise AssertionError(
            f"ATS_CLUSTER_TYPE={cluster_type!r} is not a scenario; known: {', '.join(sorted(DEFAULTS))}")
    base = DEFAULTS[cluster_type]()

    muster_base_url = _env(("ATS_MUSTER_BASE_URL",), base.muster_base_url)
    # `make e2e` passes the installation's domain once. muster answers on the
    # hostname the chart itself derives from it, so a scenario that reaches
    # muster by hostname needs no second variable for the same fact.
    domain = os.environ.get("E2E_DOMAIN")
    if not muster_base_url and domain and base.muster_reach == VIA_HOSTNAME:
        muster_base_url = f"https://muster.{domain}"

    scenario = Scenario(
        name=base.name,
        values=_env_paths("ATS_VALUES", base.values),
        overlays=_env_paths("ATS_OVERLAY_VALUES", base.overlays),
        issuer_url=_env(("ATS_ISSUER_URL", "E2E_ISSUER_URL"), base.issuer_url),
        client_id=_env(("ATS_CLIENT_ID", "E2E_CLIENT_ID"), base.client_id),
        client_secret=_env(("ATS_CLIENT_SECRET",), base.client_secret),
        registration_token=_env(("ATS_REGISTRATION_TOKEN",), base.registration_token),
        user=_env(("ATS_IDP_USER",), base.user),
        password=_env(("ATS_IDP_PASSWORD",), base.password),
        ca_secret=_env(("ATS_IDP_CA_SECRET", "E2E_IDP_CA_SECRET"), base.ca_secret),
        install_lab_dex=base.install_lab_dex,
        muster_base_url=muster_base_url,
        muster_reach=_env(("ATS_MUSTER_REACH",), base.muster_reach),
        callback=_env(("ATS_OAUTH_CALLBACK",), base.callback),
        install_timeout=_env(("ATS_INSTALL_TIMEOUT",), base.install_timeout),
        uninstall_timeout=_env(("ATS_UNINSTALL_TIMEOUT",), base.uninstall_timeout),
        uninstall_budget_s=_env_int("ATS_UNINSTALL_BUDGET_S", base.uninstall_budget_s),
        ready_timeout_s=_env_int("ATS_READY_TIMEOUT_S", base.ready_timeout_s),
    )
    _validate(scenario)
    return scenario


def _validate(scenario: Scenario) -> None:
    if scenario.muster_reach not in (VIA_PORT_FORWARD, VIA_HOSTNAME):
        raise AssertionError(
            f"ATS_MUSTER_REACH={scenario.muster_reach!r} is neither {VIA_PORT_FORWARD!r} nor {VIA_HOSTNAME!r}")
    missing = [
        name for name, value in (
            ("ATS_ISSUER_URL", scenario.issuer_url),
            ("ATS_CLIENT_ID", scenario.client_id),
            ("ATS_MUSTER_BASE_URL", scenario.muster_base_url),
        ) if not value
    ]
    if missing:
        raise AssertionError(
            f"scenario {scenario.name!r} needs {', '.join(missing)}: this cluster brings its own "
            "identity provider and hostname, so the run must name them (see tests/ats/README.md)")
    if not scenario.values:
        raise AssertionError(
            f"scenario {scenario.name!r} names no values file: set ATS_VALUES (colon- or "
            "comma-separated, in Helm's order), whose first entry is an example file under "
            f"{EXAMPLES_DIR.relative_to(REPO_ROOT)} (see tests/ats/README.md)")
    for path in scenario.values_files:
        if not path.is_file():
            raise AssertionError(f"values file not found: {path}")


def base_url_sets(scenario: Scenario) -> List[str]:
    """The `--set` that pins muster's own base URL to the one the tests call.
    muster's OAuth metadata echoes its base URL, so the two must agree; the pin
    is idempotent when the scenario's values files already carry that URL."""
    return [f"muster.muster.oauth.server.baseUrl={scenario.muster_base_url}"]


def _display(path: Path) -> str:
    """A values path as the reader knows it: relative to the repository root
    when it lives there, absolute otherwise. `make e2e` writes its overlay to a
    temporary directory, which is outside the root."""
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def summary(scenario: Scenario) -> str:
    values = ", ".join(_display(p) for p in scenario.values_files)
    return (f"scenario {scenario.name}: values [{values}], issuer {scenario.issuer_url}, "
            f"muster {scenario.muster_base_url} ({scenario.muster_reach}), "
            f"lab Dex {'installed' if scenario.install_lab_dex else 'not installed'}")
