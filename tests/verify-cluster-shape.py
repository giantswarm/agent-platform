#!/usr/bin/env python3
"""Assert the cluster-shape knobs: `auto` resolves by served API group, once.

The knobs that describe what a cluster can admit default to `auto`:
kyvernoPolicies.enabled (kyverno.io/v1), networkPolicy.flavor (cilium.io/v2 ->
cilium, else kubernetes), global.observability.metrics.serviceMonitor.enabled
(monitoring.coreos.com/v1), dicebear.route.enabled (gateway.envoyproxy.io/v1alpha1),
agentSandbox.podSecurity.enabled and modelServing.policies.enabled (both follow
the resolved kyvernoPolicies). The
meta chart resolves them once from .Capabilities.APIVersions and derives the
component copies (muster's flavor and monitors, valkey's Cilium policy and
PodMonitor, kagent's OTel exporters, oauth2-proxy monitor and OTLP header) before
it inlines each component's values. `helm template --api-versions ...` stands in
for a cluster that serves those groups; no flag is the vanilla cluster.

Each case below pins one promise:
  * fleet shape == explicit fleet values, byte for byte (no default flipped);
  * every shape resolves every leaf as expected, in the connectivity values and
    in each component's own release, and no `auto` reaches a child;
  * the connectivity chart rendered on its own with the same served groups
    emits the matching objects, never both network-policy flavors;
  * explicit values win over detection in both directions, on the knob and on a
    component copy; the podSecurity/Kyverno guard still fires on an explicit
    disagreement; a value outside auto|true|false (the booleans, not their
    string forms) fails the render.

Deliberately stdlib-only: the CI image has no PyYAML.
"""

import subprocess
import sys

FLEET_APIS = [
    "kyverno.io/v1",
    "cilium.io/v2",
    "monitoring.coreos.com/v1",
    "gateway.networking.k8s.io/v1",
    "gateway.envoyproxy.io/v1alpha1",
]

# The shapes: the served groups, from the fleet down to a bare kind cluster.
SHAPES = {
    "fleet": FLEET_APIS,
    "cilium-only": ["cilium.io/v2"],
    "kyverno-only": ["kyverno.io/v1"],
    "monitoring-only": ["monitoring.coreos.com/v1"],
    "envoy-only": ["gateway.envoyproxy.io/v1alpha1"],
    "vanilla": [],
}

PARENT_REF = ["--set", "ingress.parentRefs[0].name=x"]
# kagent, agent-sandbox and postgres on, so every gated object is reachable.
ON = [
    "--set", "components.kagent.enabled=true",
    "--set", "components.agent-sandbox.enabled=true",
    "--set", "postgres.enabled=true",
    # A pgvector extension image: the one case the CNPG ImageVolume
    # PolicyException renders — the chart's only PolicyException since the
    # kagent seccomp exception went with kagent main.
    "--set", "postgres.vector.enabled=true",
    "--set", "postgres.vector.extensionImage.reference=gsoci.azurecr.io/giantswarm/pgvector:0.8.2-18-bookworm",
    # The modelServing switch with its KServe prerequisites: its values block
    # (and the policies knob) travels to connectivity only while it is on.
    "--set", "components.modelServing.enabled=true",
    "--set", "components.kserve-crd.enabled=true",
    "--set", "components.kserve-resources.enabled=true",
]
# The fleet's values, written out: what `auto` has to resolve to under FLEET_APIS.
EXPLICIT_FLEET_KNOBS = [
    "--set", "kyvernoPolicies.enabled=true",
    "--set", "networkPolicy.flavor=cilium",
    "--set", "global.observability.metrics.serviceMonitor.enabled=true",
    "--set", "agentSandbox.podSecurity.enabled=true",
    "--set", "modelServing.policies.enabled=true",
]
EXPLICIT_FLEET_COPIES = [
    "--set", "dicebear.route.enabled=true",
    "--set", "muster.networkPolicy.flavor=cilium",
    "--set", "muster.muster.observability.metrics.prometheus.serviceMonitor.enabled=true",
    "--set", "muster.muster.observability.metrics.prometheus.prometheusRule.enabled=true",
    "--set", "muster.muster.observability.grafanaDashboard.enabled=true",
    "--set", "valkey.ciliumNetworkPolicy.enabled=true",
    "--set", "kagent.otel.tracing.enabled=true",
    "--set", "kagent.otel.logging.enabled=true",
    "--set", "kagent.oauth2-proxy.metrics.serviceMonitor.enabled=true",
]
EXPLICIT_VANILLA_KNOBS = [
    "--set", "kyvernoPolicies.enabled=false",
    "--set", "networkPolicy.flavor=kubernetes",
    "--set", "global.observability.metrics.serviceMonitor.enabled=false",
    "--set", "agentSandbox.podSecurity.enabled=false",
    "--set", "modelServing.policies.enabled=false",
]
OTLP_HEADER = "name: OTEL_EXPORTER_OTLP_HEADERS"


def apis(groups: list[str]) -> list[str]:
    return [flag for group in groups for flag in ("--api-versions", group)]


def render(chart: str, flags: list[str]) -> str:
    result = subprocess.run(
        ["helm", "template", "t", chart, *flags], capture_output=True, text=True, check=False
    )
    if result.returncode != 0:
        sys.exit(f"FAIL: render of {chart} {' '.join(flags)} failed\n{result.stderr}")
    return result.stdout


def render_fails(chart: str, flags: list[str], fragment: str, what: str) -> None:
    result = subprocess.run(
        ["helm", "template", "t", chart, *flags], capture_output=True, text=True, check=False
    )
    if result.returncode == 0:
        sys.exit(f"FAIL: {what}: the render succeeded")
    if fragment not in result.stderr:
        sys.exit(f"FAIL: {what}: failed for the wrong reason\n{result.stderr}")


def helm_releases(manifest: str) -> dict[str, list[str]]:
    """HelmRelease name -> its spec.values lines, dedented by the values indent."""
    releases: dict[str, list[str]] = {}
    for doc in manifest.split("\n---\n"):
        lines = doc.split("\n")
        if "kind: HelmRelease" not in lines or "  values:" not in lines:
            continue
        name = next(l[len("  name: "):] for l in lines if l.startswith("  name: "))
        values = []
        for line in lines[lines.index("  values:") + 1:]:
            if line and not line.startswith("    "):
                break
            values.append(line[4:])
        releases[name] = values
    return releases


def leaf(lines: list[str], path: list[str]) -> str | None:
    """The scalar at a nested map path in toYaml output, None when absent."""
    stack: list[tuple[int, str]] = []
    for line in lines:
        body = line.strip()
        if not body or body.startswith("#") or body.startswith("- "):
            continue
        indent = len(line) - len(line.lstrip(" "))
        while stack and stack[-1][0] >= indent:
            stack.pop()
        key, sep, value = body.partition(":")
        if not sep:
            continue
        stack.append((indent, key.strip()))
        if [k for _, k in stack] == path:
            return value.strip()
    return None


def kinds(manifest: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for line in manifest.split("\n"):
        if line.startswith("kind: "):
            counts[line[6:]] = counts.get(line[6:], 0) + 1
    return counts


def expect(condition: bool, message: str) -> None:
    if not condition:
        sys.exit(f"FAIL: {message}")


def yes(flag: bool) -> str:
    return "true" if flag else "false"


def check_shape(meta: str, connectivity: str, ci: list[str], name: str, served: list[str]) -> None:
    kyverno = "kyverno.io/v1" in served
    cilium = "cilium.io/v2" in served
    monitors = "monitoring.coreos.com/v1" in served
    envoy = "gateway.envoyproxy.io/v1alpha1" in served
    flavor = "cilium" if cilium else "kubernetes"
    where = f"[{name}]"

    manifest = render(meta, [*ci, *ON, *apis(served)])
    expect("enabled: auto" not in manifest and "flavor: auto" not in manifest,
           f"{where} an unresolved `auto` reached a child HelmRelease")
    hr = helm_releases(manifest)
    conn = hr["agent-platform-connectivity"]
    for path, want in (
        (["kyvernoPolicies", "enabled"], yes(kyverno)),
        (["networkPolicy", "flavor"], flavor),
        (["global", "observability", "metrics", "serviceMonitor", "enabled"], yes(monitors)),
        (["agentSandbox", "podSecurity", "enabled"], yes(kyverno)),
        (["modelServing", "policies", "enabled"], yes(kyverno)),
        (["dicebear", "route", "enabled"], yes(envoy)),
        (["muster", "networkPolicy", "flavor"], flavor),
        (["valkey", "ciliumNetworkPolicy", "enabled"], yes(cilium)),
    ):
        got = leaf(conn, path)
        expect(got == want, f"{where} connectivity values {'.'.join(path)} = {got!r}, want {want!r}")

    muster = hr["muster"]
    for path, want in (
        (["networkPolicy", "flavor"], flavor),
        (["muster", "observability", "metrics", "prometheus", "serviceMonitor", "enabled"], yes(monitors)),
        (["muster", "observability", "metrics", "prometheus", "prometheusRule", "enabled"], yes(monitors)),
        (["muster", "observability", "grafanaDashboard", "enabled"], yes(monitors)),
    ):
        got = leaf(muster, path)
        expect(got == want, f"{where} muster values {'.'.join(path)} = {got!r}, want {want!r}")

    valkey = hr["valkey"]
    got = leaf(valkey, ["ciliumNetworkPolicy", "enabled"])
    expect(got == yes(cilium), f"{where} valkey ciliumNetworkPolicy.enabled = {got!r}, want {yes(cilium)!r}")
    pod_monitor = leaf(valkey, ["valkey", "metrics", "podMonitor", "enabled"])
    if monitors:
        expect(pod_monitor is None, f"{where} valkey podMonitor.enabled written as {pod_monitor!r}; "
               "the wrapper's own default must be left alone so the fleet's values stay unchanged")
    else:
        expect(pod_monitor == "false", f"{where} valkey podMonitor.enabled = {pod_monitor!r}, want 'false'")

    kagent = hr["kagent"]
    for path in (["otel", "tracing", "enabled"], ["otel", "logging", "enabled"],
                 ["oauth2-proxy", "metrics", "serviceMonitor", "enabled"]):
        got = leaf(kagent, path)
        expect(got == yes(monitors), f"{where} kagent values {'.'.join(path)} = {got!r}, want {yes(monitors)!r}")
    expect((OTLP_HEADER in "\n".join(kagent)) == monitors,
           f"{where} kagent OTEL_EXPORTER_OTLP_HEADERS env entry present={OTLP_HEADER in chr(10).join(kagent)}, want {monitors}")
    expect((OTLP_HEADER in "\n".join(conn)) == monitors,
           f"{where} the kagent copy forwarded to connectivity disagrees on the OTLP header")

    got = leaf(hr["dicebear"], ["route", "enabled"])
    expect(got == yes(envoy), f"{where} dicebear route.enabled = {got!r}, want {yes(envoy)!r}")

    # The connectivity chart on its own, same served groups: the matching objects.
    objects = kinds(render(connectivity, [*PARENT_REF, *ON, *apis(served)]))
    cnp, netpol = objects.get("CiliumNetworkPolicy", 0), objects.get("NetworkPolicy", 0)
    expect(not (cnp and netpol), f"{where} connectivity renders both network-policy flavors")
    expect((cnp > 0) == cilium and (netpol > 0) == (not cilium),
           f"{where} connectivity flavor objects: CiliumNetworkPolicy={cnp} NetworkPolicy={netpol}")
    for kind in ("ClusterPolicy", "PolicyException"):
        expect((objects.get(kind, 0) > 0) == kyverno, f"{where} connectivity {kind} count={objects.get(kind, 0)}, kyverno served={kyverno}")
    expect((objects.get("ServiceMonitor", 0) > 0) == monitors,
           f"{where} connectivity ServiceMonitor count={objects.get('ServiceMonitor', 0)}, monitoring served={monitors}")


def main(meta: str, connectivity: str) -> int:
    ci = ["-f", f"{meta}/ci/ci-values.yaml"]
    fleet = apis(FLEET_APIS)

    # 1. No default flipped: the fleet shape IS today's explicit fleet values.
    expect(render(meta, [*ci, *ON, *fleet]) == render(meta, [*ci, *ON, *fleet, *EXPLICIT_FLEET_KNOBS, *EXPLICIT_FLEET_COPIES]),
           "the meta render with the fleet APIs served differs from the render with the fleet values set explicitly")
    expect(render(connectivity, [*PARENT_REF, *ON, *fleet]) == render(connectivity, [*PARENT_REF, *ON, *fleet, *EXPLICIT_FLEET_KNOBS]),
           "the connectivity render with the fleet APIs served differs from the render with the fleet values set explicitly")
    expect(render(connectivity, [*PARENT_REF, *ON]) == render(connectivity, [*PARENT_REF, *ON, *EXPLICIT_VANILLA_KNOBS]),
           "the connectivity render without served APIs differs from the render with the vanilla values set explicitly")

    # 2. Every shape, every leaf, both charts.
    for name, served in SHAPES.items():
        check_shape(meta, connectivity, ci, name, served)

    # 3. Explicit values win over detection, on the knob and on a component copy.
    m = helm_releases(render(meta, [*ci, *ON, *fleet, "--set", "networkPolicy.flavor=kubernetes"]))
    expect(leaf(m["agent-platform-connectivity"], ["networkPolicy", "flavor"]) == "kubernetes"
           and leaf(m["muster"], ["networkPolicy", "flavor"]) == "kubernetes"
           and leaf(m["valkey"], ["ciliumNetworkPolicy", "enabled"]) == "false"
           and leaf(m["agent-platform-connectivity"], ["kyvernoPolicies", "enabled"]) == "true",
           "networkPolicy.flavor=kubernetes with Cilium served did not reach every copy (or touched another knob)")
    c = kinds(render(connectivity, [*PARENT_REF, *ON, *fleet, "--set", "networkPolicy.flavor=kubernetes"]))
    expect(c.get("CiliumNetworkPolicy", 0) == 0 and c.get("NetworkPolicy", 0) > 0,
           "connectivity: networkPolicy.flavor=kubernetes with Cilium served still renders CiliumNetworkPolicy")

    m = helm_releases(render(meta, [*ci, *ON, *fleet, "--set", "kyvernoPolicies.enabled=false"]))
    expect(leaf(m["agent-platform-connectivity"], ["kyvernoPolicies", "enabled"]) == "false"
           and leaf(m["agent-platform-connectivity"], ["agentSandbox", "podSecurity", "enabled"]) == "false"
           and leaf(m["agent-platform-connectivity"], ["networkPolicy", "flavor"]) == "cilium",
           "kyvernoPolicies.enabled=false with Kyverno served did not switch podSecurity off with it (or touched the flavor)")
    c = kinds(render(connectivity, [*PARENT_REF, *ON, *fleet, "--set", "kyvernoPolicies.enabled=false"]))
    expect(c.get("ClusterPolicy", 0) == 0 and c.get("PolicyException", 0) == 0 and c.get("CiliumNetworkPolicy", 0) > 0,
           "connectivity: kyvernoPolicies.enabled=false with Kyverno served still renders kyverno.io objects")

    m = helm_releases(render(meta, [*ci, *ON, *fleet, "--set", "global.observability.metrics.serviceMonitor.enabled=false"]))
    expect(leaf(m["muster"], ["muster", "observability", "metrics", "prometheus", "serviceMonitor", "enabled"]) == "false"
           and leaf(m["kagent"], ["otel", "tracing", "enabled"]) == "false"
           and OTLP_HEADER not in "\n".join(m["kagent"])
           and leaf(m["valkey"], ["valkey", "metrics", "podMonitor", "enabled"]) == "false",
           "global.observability.metrics.serviceMonitor.enabled=false with monitoring served did not reach every copy")
    rendered = render(connectivity, [*PARENT_REF, *ON, *fleet, "--set", "global.observability.metrics.serviceMonitor.enabled=false"])
    expect("kind: ServiceMonitor" not in rendered and "enablePodMonitor" not in rendered,
           "connectivity: serviceMonitor.enabled=false with monitoring served still renders a monitor")

    m = helm_releases(render(meta, [*ci, *ON, *fleet, "--set", "muster.networkPolicy.flavor=kubernetes"]))
    expect(leaf(m["muster"], ["networkPolicy", "flavor"]) == "kubernetes"
           and leaf(m["agent-platform-connectivity"], ["networkPolicy", "flavor"]) == "cilium",
           "an explicit muster.networkPolicy.flavor did not win over the derived copy")

    m = helm_releases(render(meta, [*ci, *ON, *fleet, "--set", "agentSandbox.podSecurity.enabled=false", "--set", "dicebear.route.enabled=false"]))
    expect(leaf(m["agent-platform-connectivity"], ["agentSandbox", "podSecurity", "enabled"]) == "false"
           and leaf(m["agent-platform-connectivity"], ["kyvernoPolicies", "enabled"]) == "true"
           and leaf(m["dicebear"], ["route", "enabled"]) == "false",
           "explicit podSecurity / dicebear route values did not win with their APIs served")

    # ... and the other way: explicit true on a cluster that does not serve the API.
    m = helm_releases(render(meta, [*ci, *ON, "--set", "kyvernoPolicies.enabled=true", "--set", "networkPolicy.flavor=cilium",
                                     "--set", "global.observability.metrics.serviceMonitor.enabled=true"]))
    expect(leaf(m["agent-platform-connectivity"], ["kyvernoPolicies", "enabled"]) == "true"
           and leaf(m["agent-platform-connectivity"], ["agentSandbox", "podSecurity", "enabled"]) == "true"
           and leaf(m["muster"], ["networkPolicy", "flavor"]) == "cilium"
           and leaf(m["valkey"], ["ciliumNetworkPolicy", "enabled"]) == "true"
           and leaf(m["valkey"], ["valkey", "metrics", "podMonitor", "enabled"]) is None
           and OTLP_HEADER in "\n".join(m["kagent"]),
           "explicit true values on a vanilla render did not win over detection")
    c = kinds(render(connectivity, [*PARENT_REF, *ON, "--set", "kyvernoPolicies.enabled=true", "--set", "networkPolicy.flavor=cilium"]))
    expect(c.get("ClusterPolicy", 0) > 0 and c.get("CiliumNetworkPolicy", 0) > 0 and c.get("NetworkPolicy", 0) == 0,
           "connectivity: explicit true values on a vanilla render did not render the objects")

    # 4. The guard on an explicit disagreement still fires.
    render_fails(connectivity, [*PARENT_REF, *ON, *fleet, "--set", "kyvernoPolicies.enabled=false", "--set", "agentSandbox.podSecurity.enabled=true"],
                 "requires kyvernoPolicies.enabled", "podSecurity on with kyvernoPolicies off")

    # 5. Anything but auto|true|false (auto|cilium|kubernetes) fails, in both charts —
    #    a string "true" from --set-string included: the schema's enum holds the booleans.
    for chart, base in ((meta, ci), (connectivity, PARENT_REF)):
        render_fails(chart, [*base, "--set", "networkPolicy.flavor=bogus"], "flavor", f"{chart}: bogus flavor")
        render_fails(chart, [*base, "--set", "kyvernoPolicies.enabled=maybe"], "kyvernoPolicies", f"{chart}: bogus kyvernoPolicies.enabled")
        render_fails(chart, [*base, "--set-string", "kyvernoPolicies.enabled=true"], "kyvernoPolicies", f"{chart}: string 'true' for kyvernoPolicies.enabled")
        render_fails(chart, [*base, "--set", "global.observability.metrics.serviceMonitor.enabled=maybe"], "serviceMonitor", f"{chart}: bogus serviceMonitor.enabled")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2]))
