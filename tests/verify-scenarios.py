#!/usr/bin/env python3
"""Assert the ATS scenario inputs and the `make e2e` overlay, offline.

`tests/ats/scenarios.py` holds what differs between clusters — the values
files, the identity provider, how muster is reached, the budgets — and
`tests/e2e_overlay.py` turns the environment into the values overlay `make e2e`
layers last. Both are pure Python: they need no cluster, so they are asserted
here rather than only on a live run.

Each case below pins one property a run on a real cluster relies on:

- the defaults: `kind` with nothing set installs the three lab values files at
  the lab ports, budgets and credentials, and `eks` brings no default issuer,
  client, base URL or values file;
- every refusal names its variable: an unknown cluster type, an unknown reach,
  a non-boolean ATS_LAB_DEX, a non-integer port, a missing issuer, client or
  base URL, a scenario with no values file, and a values file that is absent;
- the derived base URL: a moved ATS_MUSTER_PORT moves the URL with it, because
  the port is part of the URL muster is configured with, and an empty
  ATS_MUSTER_BASE_URL counts as unset there as everywhere else;
- the base-URL `--set`: none while the scenario's own values files carry the
  URL, one as soon as the run's URL differs from them, ATS_VALUES included;
- the overlay's shapes: `global.identity` appears only when one of its keys is
  set, because an empty `identity:` is a null that drops the block from every
  component release, and every value is a quoted scalar.

Deliberately stdlib-only, as the other verifiers are: the CI image has no
PyYAML and runs no pytest outside the ATS directory.
"""

import dataclasses
import importlib
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "tests"))
sys.path.insert(0, str(REPO_ROOT / "tests" / "ats"))

import e2e_overlay  # noqa: E402
import scenarios  # noqa: E402

FAILURES = []


def load(**env):
    """The scenario of a run whose environment is exactly `env`."""
    for name in [n for n in os.environ if n.startswith("ATS_")]:
        del os.environ[name]
    os.environ.update({k: v for k, v in env.items() if v is not None})
    importlib.reload(scenarios)
    return scenarios.load()


def check(label: str, ok: bool, detail: str = "") -> None:
    if not ok:
        FAILURES.append(f"{label}{': ' + detail if detail else ''}")


def refuses(label: str, needles, **env) -> None:
    """The run must fail, and the message must name every needle."""
    try:
        load(**env)
    except AssertionError as err:
        for needle in needles:
            check(label, needle in str(err), f"the message lacks {needle!r}: {err}")
        return
    FAILURES.append(f"{label}: the run was accepted")


def main() -> int:
    # --- the defaults
    kind = load()
    check("kind values", [p.name for p in kind.values] == [
        "kind-lab-dex.yaml", "values-kagent.yaml", "values-round-trips.yaml"],
        str([p.name for p in kind.values]))
    check("kind base values is a shipped example", kind.base_values.parent == scenarios.EXAMPLES_DIR)
    check("kind installs the lab Dex", kind.install_lab_dex)
    check("kind reaches muster through a port-forward", kind.via_port_forward)
    check("kind base URL", kind.muster_base_url == f"http://localhost:{scenarios.LAB_MUSTER_PORT}", kind.muster_base_url)
    check("kind issuer", kind.issuer_url == scenarios.LAB_DEX_ISSUER, kind.issuer_url)
    check("kind has a static user", bool(kind.user and kind.password))
    check("kind names a CA Secret", kind.ca_secret == scenarios.LAB_CA_SECRET, kind.ca_secret)
    check("kind own-Flux values", [p.name for p in kind.own_flux_values] == [
        "kind-lab-dex.yaml", "values-kagent.yaml"], str([p.name for p in kind.own_flux_values]))
    print("ok: the kind scenario's defaults — the three lab values files, the lab issuer, a static user, the port-forward")

    # `make e2e` passes every derived variable, empty ones included, so an
    # empty variable must leave the scenario's default in place.
    blanked = load(ATS_ISSUER_URL="", ATS_CLIENT_ID="", ATS_IDP_CA_SECRET="",
                   ATS_MUSTER_BASE_URL="", ATS_VALUES="", ATS_OVERLAY_VALUES="")
    # A reload rebinds the dataclass, so the two instances compare by field.
    check("an empty variable keeps the kind default",
          dataclasses.astuple(blanked) == dataclasses.astuple(kind), scenarios.summary(blanked))
    print("ok: an empty ATS_ variable, as `make e2e` passes it, keeps the scenario's default")

    eks = load(ATS_CLUSTER_TYPE="eks", ATS_ISSUER_URL="https://dex.example.com",
               ATS_CLIENT_ID="platform", ATS_MUSTER_BASE_URL="https://muster.example.com",
               ATS_VALUES="helm/agent-platform/examples/kind-lab-dex.yaml")
    check("eks installs no lab Dex", not eks.install_lab_dex)
    check("eks port-forwards nothing", not eks.via_port_forward)
    check("eks has no static user", not eks.user, eks.user)
    check("eks names no CA Secret by default", not eks.ca_secret, eks.ca_secret)
    check("eks issuer port", eks.issuer_port == 443, str(eks.issuer_port))
    check("eks budgets", (eks.install_timeout, eks.uninstall_budget_s, eks.ready_timeout_s) == ("20m", 300, 900),
          str((eks.install_timeout, eks.uninstall_budget_s, eks.ready_timeout_s)))
    check("eks values files come from the run", [p.name for p in eks.values] == ["kind-lab-dex.yaml"],
          str([p.name for p in eks.values]))
    print("ok: the eks scenario — no lab Dex, no port-forward, no static user, its own values files and budgets")

    # --- every refusal names its variable
    refuses("an unknown cluster type", ["ATS_CLUSTER_TYPE", "gke", "kind"], ATS_CLUSTER_TYPE="gke")
    refuses("an unknown reach", ["ATS_MUSTER_REACH", "tunnel"], ATS_MUSTER_REACH="tunnel")
    refuses("a non-boolean ATS_LAB_DEX", ["ATS_LAB_DEX", "maybe"], ATS_LAB_DEX="maybe")
    refuses("a non-integer ATS_MUSTER_PORT", ["ATS_MUSTER_PORT", "abc"], ATS_MUSTER_PORT="abc")
    refuses("a non-integer ATS_ISSUER_PORT", ["ATS_ISSUER_PORT", "https"], ATS_ISSUER_PORT="https")
    refuses("a non-integer ATS_UNINSTALL_BUDGET_S", ["ATS_UNINSTALL_BUDGET_S"], ATS_UNINSTALL_BUDGET_S="5m")
    refuses("an eks run that names nothing",
            ["ATS_ISSUER_URL", "ATS_CLIENT_ID", "ATS_MUSTER_BASE_URL"], ATS_CLUSTER_TYPE="eks")
    refuses("an eks run with no values file", ["ATS_VALUES", "examples"],
            ATS_CLUSTER_TYPE="eks", ATS_ISSUER_URL="https://dex.example.com",
            ATS_CLIENT_ID="platform", ATS_MUSTER_BASE_URL="https://muster.example.com")
    refuses("a values file that does not exist", ["tests/ats/nope.yaml"],
            ATS_VALUES="tests/ats/nope.yaml")
    print("ok: the refusals — an unknown cluster type, an unknown reach, a non-boolean flag, a non-integer port or budget, a missing issuer / client / base URL, no values file, an absent values file")

    # --- the derived base URL
    moved = load(ATS_MUSTER_PORT="9000")
    check("a moved port moves the base URL", moved.muster_base_url == "http://localhost:9000", moved.muster_base_url)
    empty = load(ATS_MUSTER_PORT="9000", ATS_MUSTER_BASE_URL="")
    check("an empty ATS_MUSTER_BASE_URL counts as unset",
          empty.muster_base_url == "http://localhost:9000", empty.muster_base_url)
    named = load(ATS_MUSTER_PORT="9000", ATS_MUSTER_BASE_URL="http://localhost:7000")
    check("a named base URL wins", named.muster_base_url == "http://localhost:7000", named.muster_base_url)
    hostname = load(ATS_MUSTER_PORT="9000", ATS_MUSTER_REACH="hostname",
                    ATS_MUSTER_BASE_URL="https://muster.example.com")
    check("a hostname reach keeps its URL", hostname.muster_base_url == "https://muster.example.com",
          hostname.muster_base_url)
    print("ok: the derived base URL — a moved port moves it, an empty variable counts as unset, a named URL wins")

    # --- the base-URL --set
    check("no --set while the values files carry the URL", scenarios.base_url_sets(load()) == [],
          str(scenarios.base_url_sets(load())))
    sets = scenarios.base_url_sets(load(ATS_MUSTER_PORT="9000"))
    check("one --set once the URL differs",
          sets == ["muster.muster.oauth.server.baseUrl=http://localhost:9000"], str(sets))
    own = scenarios.base_url_sets(load(ATS_VALUES="helm/agent-platform/examples/kind-lab-dex.yaml"))
    check("one --set for a run that names its own values files",
          own == [f"muster.muster.oauth.server.baseUrl=http://localhost:{scenarios.LAB_MUSTER_PORT}"], str(own))
    print("ok: the base-URL --set — none while the scenario's values files carry it, one as soon as the run's differs")

    # --- the make e2e overlay
    check("no overlay from an empty environment", e2e_overlay.overlay_lines({}) == [],
          str(e2e_overlay.overlay_lines({})))
    domain_only = e2e_overlay.overlay_lines({"E2E_DOMAIN": "example.com"})
    check("a domain-only overlay writes no identity key",
          domain_only == [e2e_overlay.HEADER, "global:", '  domain: "example.com"'], str(domain_only))
    ca_only = e2e_overlay.overlay_lines({"E2E_IDP_CA_SECRET": "idp-ca"})
    check("a CA-only overlay carries identity.ca",
          ca_only == [e2e_overlay.HEADER, "global:", "  identity:", "    ca:",
                      '      secretName: "idp-ca"'], str(ca_only))
    full = e2e_overlay.overlay_lines({
        "E2E_DOMAIN": "example.com", "E2E_ISSUER_URL": "https://dex.example.com",
        "E2E_CLIENT_ID": "platform", "E2E_IDP_SECRET_NAME": "idp", "E2E_IDP_CA_SECRET": "idp-ca"})
    check("the full overlay's shape and order", full == [
        e2e_overlay.HEADER, "global:", '  domain: "example.com"', "  identity:",
        '    issuerUrl: "https://dex.example.com"', '    clientId: "platform"',
        '    existingSecret: "idp"', "    ca:", '      secretName: "idp-ca"'], str(full))
    check("an empty variable adds no key",
          e2e_overlay.overlay_lines({"E2E_DOMAIN": "example.com", "E2E_ISSUER_URL": ""}) == domain_only,
          str(e2e_overlay.overlay_lines({"E2E_DOMAIN": "example.com", "E2E_ISSUER_URL": ""})))
    awkward = e2e_overlay.overlay_lines({"E2E_DOMAIN": 'a b#c"d'})
    check("a value with a space, a # and a quote stays one scalar",
          awkward[-1] == '  domain: "a b#c\\"d"', str(awkward))
    print("ok: the make e2e overlay — global.identity only when a key is set, the order, quoted scalars")

    if FAILURES:
        for failure in FAILURES:
            print(f"FAIL: {failure}")
        return 1
    print("the ATS scenario inputs and the e2e overlay verified.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
