#!/usr/bin/env python3
"""Assert that disabling a component removes BOTH its release and its wiring.

`components.<name>.enabled` decides whether the meta chart renders a component's
HelmRelease. The connectivity chart renders that component's cluster-side wiring
(ClusterRoles, NetworkPolicies, routes, CRs) and must reach the same answer, so
the meta chart forwards its answers under that same key.

Before that forward existed, the connectivity chart read the component's own
`<component>.enabled` value instead. Setting `components.agentgateway.enabled:
false` with `agentgateway.enabled: true` then produced a cluster with no
agentgateway release and a ListenerSet ClusterRole for a controller that was
never installed. Each case below turns one such inconsistency into a failure.

Deliberately stdlib-only: the CI image has no PyYAML.
"""

import re
import subprocess
import sys

# component key -> the marker that only this component's connectivity wiring
# renders, and the extra flags each side of the toggle needs. agentgateway's
# ingress.mode must agree with its own toggle, so the two sides differ.
WIRING = {
    "agentgateway": (
        "agentgateway-listenerset-t",
        ["--set", "ingress.mode=agentgateway-muster"],
        ["--set", "ingress.mode=muster-direct"],
    ),
    "kagent": ("agent-platform-connectivity-kagent-controller", [], []),
    # The route renders agentgateway.dev objects on the agentgateway Gateway, so
    # it needs an agentgateway-* mode with the agentgateway component on (the
    # ingress guard refuses it in muster-direct).
    "klaus-gateway": (
        "agent-platform-connectivity-dataplane-to-klausgateway",
        ["--set", "klausGateway.agentgatewayRoute.enabled=true", "--set", "ingress.mode=agentgateway-muster", "--set", "components.agentgateway.enabled=true"],
        ["--set", "klausGateway.agentgatewayRoute.enabled=true", "--set", "ingress.mode=agentgateway-muster", "--set", "components.agentgateway.enabled=true"],
    ),
    "agent-sandbox": ("agent-platform-connectivity-agent-sandbox-pod-security", [], []),
    # The egress policy renders whenever the component is on (networkPolicy is
    # on by default); the guards need a backend endpoint / the kagent runtime.
    "model-manager": (
        "agent-platform-connectivity-model-manager-egress",
        ["--set", "model-manager.ollama.endpoint=http://10.0.0.1:11434", "--set", "components.kagent.enabled=true", "--set", "kagent.harness.snapshotLocation=s3://ci-agent-snapshots/agents", "--set", "model-manager.oauth.enabled=false"],
        [],
    ),
    "agent-manager": (
        "agent-platform-connectivity-agent-manager-egress",
        ["--set", "components.kagent.enabled=true", "--set", "kagent.harness.snapshotLocation=s3://ci-agent-snapshots/agents", "--set", "agent-manager.oauth.enabled=false"],
        [],
    ),
}

# The fleet's API groups: the cluster-shape knobs default to `auto` and resolve
# from .Capabilities.APIVersions, so without these a `helm template` renders the
# vanilla shape and the Kyverno / Cilium markers below never appear.
FLEET_APIS = [
    "--api-versions", "kyverno.io/v1",
    "--api-versions", "cilium.io/v2",
    "--api-versions", "monitoring.coreos.com/v1",
    "--api-versions", "gateway.networking.k8s.io/v1",
    "--api-versions", "gateway.envoyproxy.io/v1alpha1",
]
PARENT_REF = ["--set", "ingress.parentRefs[0].name=x", *FLEET_APIS]


def render(chart: str, flags: list[str]) -> str:
    result = subprocess.run(
        ["helm", "template", "t", chart, *flags],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        sys.exit(f"FAIL: render of {chart} failed\n{result.stderr}")
    return result.stdout


def released(manifest: str, name: str) -> bool:
    return f"\n  name: {name}\n" in manifest


def main(meta: str, connectivity: str) -> int:
    ci = ["-f", f"{meta}/ci/ci-values.yaml"]

    for name, (marker, on_extra, off_extra) in WIRING.items():
        # The meta chart drops the release.
        off = render(meta, [*ci, "--set", f"components.{name}.enabled=false"])
        if released(off, name):
            sys.exit(f"FAIL: components.{name}.enabled=false still rendered the {name} release")

        # And the connectivity chart, reading the forwarded answer, drops the wiring.
        wiring_off = render(
            connectivity,
            [*PARENT_REF, *off_extra, "--set", f"components.{name}.enabled=false"],
        )
        if marker in wiring_off:
            sys.exit(
                f"FAIL: connectivity still renders {name} wiring ({marker!r}) "
                f"with components.{name}.enabled=false"
            )

        # The marker only proves something when the enabled render carries it.
        wiring_on = render(
            connectivity,
            [*PARENT_REF, *on_extra, "--set", f"components.{name}.enabled=true"],
        )
        if marker not in wiring_on:
            sys.exit(
                f"FAIL: the {name} wiring marker {marker!r} is absent even when enabled; "
                "this case proves nothing — fix the marker"
            )

    # The regression this whole forward exists for: the meta chart must hand the
    # connectivity chart the same answer it used itself, never the raw per-chart
    # toggle. agentgateway.enabled no longer exists, so nothing can re-introduce
    # the disagreement without this failing.
    both = render(meta, [*ci, "--set", "components.agentgateway.enabled=false"])
    if released(both, "agentgateway"):
        sys.exit("FAIL: the agentgateway release survived components.agentgateway.enabled=false")
    if not re.search(r"^ +agentgateway:\n +enabled: false$", both, re.M):
        sys.exit(
            "FAIL: the meta chart did not forward components.agentgateway.enabled=false to the "
            "connectivity chart; the wiring would render for a controller that was never installed"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2]))
