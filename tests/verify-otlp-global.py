#!/usr/bin/env python3
"""Assert one OTLP collector for the whole platform (giantswarm/giantswarm#36711).

global.observability.traces.otlp (endpoint, protocol, tenant, headers) is the one
source: the meta chart writes it into every exporter's own key that is `auto`
before it forwards the component's values (agent-platform.shape.otlp), and the
connectivity chart's egress rules follow each exporter's resolved endpoint. The
cases, each through the meta chart and then through the connectivity chart
rendered with the values the meta chart hands its release (the Flux path):

- defaults: every exporter names the kube-system otlp-gateway on 4317, the tenant
  giantswarm travels as X-Scope-OrgID (kagent controller + Harness env, muster,
  klaus-gateway, the four managers, the portal, mcp-kubernetes) or as the observability.giantswarm.io/tenant pod label
  (Substrate, the data plane); every OTLP egress rule selects kube-system on 4317;
- a customer collector in another namespace, on another port, with another tenant
  and one more header: every key follows, every egress rule selects that
  namespace on that port in both flavours, no kube-system OTLP rule is left;
  mcp-kubernetes takes the endpoint as host:port, with the scheme's or the
  protocol's default port when it names none, and otlpInsecure from the scheme;
- an https collector outside the cluster: kagent's `insecure` resolves false (the
  SDKs read it after the endpoint), every egress rule is the cluster entity on 443;
- an explicit per-component key wins over the global one, and its egress rule
  follows it, while the other exporters keep the global endpoint;
- an empty endpoint exports nothing: kagent's exporters resolve off, Substrate's
  signals are off (its chart would fall back to localhost:4317), the other
  endpoints are empty, the data plane loses its endpoint entry and its tracing
  policy, no OTLP egress rule renders;
- an empty tenant sends no X-Scope-OrgID and sets no tenant label;
- an X-Scope-OrgID header that differs from the tenant fails the render;
- http/protobuf fails while a gRPC-only exporter (klaus-gateway, Substrate,
  kagent's log exporter) would take the endpoint, and passes once each names
  its own; the other exporters and the data plane then speak http/protobuf and
  their egress rules open 4318 for an endpoint without a port.

Deliberately stdlib-only: the CI image has no PyYAML. HELM selects the binary.
"""

import os
import re
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
VM = ["--set", "ingress.parentRefs[0].name=x", "--set", "kagent.harness.snapshotLocation=s3://ci-agent-snapshots/agents", *FLEET_APIS]
# Every exporter on: kagent (controller + actors), muster (on by default),
# klaus-gateway, Substrate, the agentgateway data plane.
ON = [
    "--set", "components.kagent.enabled=true",
    "--set", "components.substrate.enabled=true", "--set", "components.substrate-crds.enabled=true",
    "--set", "components.klaus-gateway.enabled=true",
    "--set", "components.agentgateway.enabled=true", "--set", "ingress.mode=agentgateway-muster",
    # OAuth off for the managers and mcp-kubernetes: their OTLP keys and
    # egress do not depend on it, and on it needs each one's issuer and client.
    *[f for c in ("agent-manager", "vm-manager", "cluster-manager", "backstage", "mcp-kubernetes")
      for f in ("--set", f"components.{c}.enabled=true")],
    *[f for c in ("model-manager", "agent-manager", "vm-manager", "cluster-manager")
      for f in ("--set", f"{c}.oauth.enabled=false")],
    "--set", "mcp-kubernetes.mcpKubernetes.oauth.enabled=false",
    # The portal's egress policy names the login issuer, its route a Gateway,
    # its public URL the domain.
    "--set", "global.identity.issuerUrl=https://dex.ci.example.com",
    "--set", "global.gatewayApi.parentRefs[0].name=gw", "--set", "global.gatewayApi.parentRefs[0].namespace=gw-system",
    "--set", "global.domain=example.com",
]
OTLP = "global.observability.traces.otlp"
DEFAULT_EP = "http://otlp-gateway.kube-system.svc:4317"
CUSTOMER_EP = "http://otel-collector.customer-otel.svc:14317"

# The connectivity chart's OTLP egress policies, by exporter.
KAGENT_POLICIES = ["agent-platform-connectivity-kagent-controller-egress", "substrate-atenet-egress"]
SUBSTRATE_POLICIES = ["substrate-ate-api-server", "substrate-ate-controller", "substrate-atelet", "substrate-atenet-router"]
MUSTER_POLICY = "muster-otlp-egress"
KLAUS_POLICY = "agent-platform-connectivity-klausgateway-otlp-egress"
DATAPLANE_POLICY = "agent-platform-connectivity-dataplane-otlp-egress"
TRACING_POLICY = "agent-platform-connectivity-tracing"
# The components whose chart block carries observability.otel (model-manager on
# by default, the rest turned on above), and their egress policy in each flavour.
COMPONENTS = ["model-manager", "agent-manager", "vm-manager", "cluster-manager", "backstage"]
COMPONENT_POLICIES = {c: f"agent-platform-connectivity-{c}-egress" for c in COMPONENTS if c != "backstage"}
COMPONENT_POLICIES_CILIUM = {**COMPONENT_POLICIES, "backstage": "agent-platform-connectivity-backstage"}
COMPONENT_POLICIES_K8S = {**COMPONENT_POLICIES, "backstage": "agent-platform-connectivity-backstage-egress"}


def fail(msg: str) -> None:
    sys.exit(f"FAIL: {msg}")


def helm(chart: str, flags: list) -> subprocess.CompletedProcess:
    return subprocess.run([HELM, "template", "t", chart, *flags], capture_output=True, text=True, check=False)


def render(chart: str, flags: list) -> str:
    r = helm(chart, flags)
    if r.returncode != 0:
        fail(f"render of {chart} failed\n{r.stderr}")
    return r.stdout


def docs(manifest: str) -> dict:
    out = {}
    for d in manifest.split("\n---\n"):
        kind = re.search(r"^kind: (\S+)", d, re.M)
        name = re.search(r"^  name: (\S+)", d, re.M)
        if kind and name:
            out[(kind.group(1), name.group(1))] = d
    return out


def doc(manifest: dict, kind: str, name: str) -> str:
    if (kind, name) not in manifest:
        fail(f"no {kind} {name}")
    return manifest[(kind, name)]


def hr_values(hr: str) -> str:
    """A HelmRelease's spec.values as a values file (spec.values is spec's last key)."""
    if "\n  values:\n" not in hr:
        return ""
    body = hr[hr.index("\n  values:\n") + len("\n  values:\n"):]
    return "\n".join(line[4:] if line.startswith("    ") else line for line in body.splitlines()) + "\n"


def get(text: str, path: list):
    """The value at path in a block-style YAML text: a scalar as written, a map
    or a list as its indented text; None when a key is missing."""
    lines = text.splitlines()
    lo, hi, indent = 0, len(lines), 0
    value = None
    for key in path:
        want = re.compile(rf"^ {{{indent}}}{re.escape(key)}:(?: (.*))?$")
        for i in range(lo, hi):
            m = want.match(lines[i])
            if m:
                value = m.group(1)
                end = i + 1
                while end < hi and (not lines[end].strip() or len(lines[end]) - len(lines[end].lstrip()) > indent
                                    or (lines[end].startswith(" " * indent + "- "))):
                    end += 1
                lo, hi = i + 1, end
                child = next((l for l in lines[lo:hi] if l.strip()), None)
                indent = len(child) - len(child.lstrip()) if child else indent + 2
                break
        else:
            return None
    return value if value is not None else "\n".join(lines[lo:hi])


def env(text: str, path: list, name: str):
    """The value of the env entry `name` in the list at path; None when absent."""
    block = get(text, path) or ""
    m = re.search(rf"^\s*- name: {re.escape(name)}\n\s+value: (.*)$", block, re.M)
    return m.group(1).strip('"') if m else None


def expect(what: str, got, want) -> None:
    if got != want:
        fail(f"{what}: got {got!r}, want {want!r}")


def meta_and_conn(meta_chart: str, conn_chart: str, flags: list, tmp: str, name: str) -> tuple:
    """The meta render, its HelmReleases' values by release, and the connectivity
    render from the values its release carries, cilium and kubernetes flavour."""
    meta = docs(render(meta_chart, VM + ON + flags))
    values = {n: hr_values(d) for (k, n), d in meta.items() if k == "HelmRelease"}
    path = os.path.join(tmp, f"{name}.yaml")
    with open(path, "w", encoding="utf-8") as f:
        f.write(values["agent-platform-connectivity"])
    conn = docs(render(conn_chart, ["-f", path, *FLEET_APIS]))
    k8s = docs(render(conn_chart, ["-f", path, *FLEET_APIS, "--set", "networkPolicy.flavor=kubernetes",
                                   "--set", "networkPolicy.kubernetes.apiServerCIDR=10.9.0.1/32"]))
    return values, conn, k8s


def exporters(values: dict) -> dict:
    """Every exporter's endpoint as its release (or the connectivity release, for the data plane) carries it."""
    kagent, muster, kg, sub, conn = (values[n] for n in ("kagent", "muster", "klaus-gateway", "substrate", "agent-platform-connectivity"))
    return {
        "kagent.otel.tracing": get(kagent, ["otel", "tracing", "exporter", "otlp", "endpoint"]),
        "kagent.otel.logging": get(kagent, ["otel", "logging", "exporter", "otlp", "endpoint"]),
        "muster": get(muster, ["muster", "observability", "otel", "endpoint"]),
        "klaus-gateway": get(kg, ["observability", "otlpEndpoint"]),
        "substrate": get(sub, ["otel", "endpoint"]),
        "data plane": env(conn, ["gateway", "parameters", "dataPlaneEnv"], "OTEL_EXPORTER_OTLP_ENDPOINT"),
        **{c: get(values[c], ["observability", "otel", "endpoint"]) for c in COMPONENTS},
    }


def mcp_kubernetes(values: dict) -> dict:
    """mcp-kubernetes' instrumentation keys as its release carries them."""
    return {k: get(values["mcp-kubernetes"], ["mcpKubernetes", "instrumentation", k])
            for k in ("tracingExporter", "otlpEndpoint", "otlpInsecure", "otlpProtocol", "otlpHeaders")}


def otlp_rules(policy: str) -> list:
    """The (namespace or 'cluster', port) of every OTLP rule in a CiliumNetworkPolicy:
    the rule item that follows each `# The OTLP gateway ...` comment."""
    rules = []
    lines = policy.splitlines()
    for i, line in enumerate(lines):
        if not line.lstrip().startswith("# The OTLP gateway "):
            continue
        head = lines[i + 1]
        indent = len(head) - len(head.lstrip())
        item = [head]
        for nxt in lines[i + 2:]:
            if len(nxt) - len(nxt.lstrip()) <= indent and nxt.strip():
                break
            item.append(nxt)
        rule = "\n".join(item)
        ns = re.search(r"io\.kubernetes\.pod\.namespace: (\S+)", rule)
        port = re.search(r'port: "(\d+)"', rule)
        rules.append((ns.group(1) if ns else ("cluster" if "- cluster" in rule else "?"), port.group(1) if port else "?"))
    return rules


def k8s_rule(policy: str) -> tuple:
    """The (namespace, port) a kubernetes-flavour OTLP NetworkPolicy opens."""
    ns = re.search(r"kubernetes\.io/metadata\.name: (\S+)", policy)
    port = re.search(r"port: (\d+)\n\s+protocol: TCP\n\s+- ports:", policy)
    return (ns.group(1) if ns else None, port.group(1) if port else None)


def k8s_otlp_rules(policy: str) -> list:
    """The (namespace or 'any', port) of every OTLP rule in a kubernetes-flavour
    NetworkPolicy: the rule item that follows each `# The OTLP gateway ...` comment."""
    rules = []
    lines = policy.splitlines()
    for i, line in enumerate(lines):
        if not line.lstrip().startswith("# The OTLP gateway "):
            continue
        rule = "\n".join(lines[i + 1:i + 12])
        ns = re.search(r"kubernetes\.io/metadata\.name: (\S+)", rule)
        port = re.search(r"port: (\d+)", rule)
        rules.append((ns.group(1) if ns and "ipBlock" not in rule.split("ports:")[0] else "any", port.group(1) if port else "?"))
    return rules


def check_components(conn: dict, k8s: dict, dest, what: str, k8s_dest="same") -> None:
    """Every component policy opens exactly dest (None: no OTLP rule), cilium and,
    when k8s is given, kubernetes flavour (k8s_dest, default the same)."""
    for c, name in COMPONENT_POLICIES_CILIUM.items():
        expect(f"{what}: {name} OTLP rules", sorted(set(otlp_rules(doc(conn, "CiliumNetworkPolicy", name)))), [dest] if dest else [])
    if k8s:
        want = dest if k8s_dest == "same" else k8s_dest
        for c, name in COMPONENT_POLICIES_K8S.items():
            expect(f"{what}: {name} OTLP rules (kubernetes flavour)", sorted(set(k8s_otlp_rules(doc(k8s, "NetworkPolicy", name)))), [want] if want else [])


def check_egress(conn: dict, k8s: dict, want: dict, what: str) -> None:
    """want: policy name -> the one (namespace, port) its OTLP rules open."""
    for name, dest in want.items():
        rules = otlp_rules(doc(conn, "CiliumNetworkPolicy", name))
        if dest is None:
            expect(f"{what}: {name} OTLP rules", rules, [])
        else:
            expect(f"{what}: {name} OTLP rules", sorted(set(rules)), [dest])
    for name in (MUSTER_POLICY, KLAUS_POLICY, DATAPLANE_POLICY):
        if k8s and want.get(name):
            expect(f"{what}: {name} (kubernetes flavour)", k8s_rule(doc(k8s, "NetworkPolicy", name)), want[name])


def everywhere(dest) -> dict:
    return {p: dest for p in [*KAGENT_POLICIES, *SUBSTRATE_POLICIES, MUSTER_POLICY, KLAUS_POLICY, DATAPLANE_POLICY]}


def case_defaults(meta: str, conn_chart: str, tmp: str) -> None:
    values, conn, k8s = meta_and_conn(meta, conn_chart, [], tmp, "defaults")
    expect("defaults: exporters", set(exporters(values).values()), {DEFAULT_EP})
    kagent, muster, kg, sub, cv = (values[n] for n in ("kagent", "muster", "klaus-gateway", "substrate", "agent-platform-connectivity"))
    expect("defaults: kagent tracing protocol", get(kagent, ["otel", "tracing", "exporter", "otlp", "protocol"]), "grpc")
    expect("defaults: kagent tracing insecure (http:// endpoint)", get(kagent, ["otel", "tracing", "exporter", "otlp", "insecure"]), "true")
    expect("defaults: kagent logging insecure (http:// endpoint)", get(kagent, ["otel", "logging", "exporter", "otlp", "insecure"]), "true")
    expect("defaults: kagent tracing enabled", get(kagent, ["otel", "tracing", "enabled"]), "true")
    expect("defaults: kagent logging enabled", get(kagent, ["otel", "logging", "enabled"]), "true")
    expect("defaults: kagent controller header", env(kagent, ["controller", "env"], "OTEL_EXPORTER_OTLP_HEADERS"), "X-Scope-OrgID=giantswarm")
    expect("defaults: kagent Harness header", env(kagent, ["harness", "env"], "OTEL_EXPORTER_OTLP_HEADERS"), "X-Scope-OrgID=giantswarm")
    expect("defaults: muster protocol", get(muster, ["muster", "observability", "otel", "protocol"]), "grpc")
    expect("defaults: muster headers", get(muster, ["muster", "observability", "otel", "headers"]), "X-Scope-OrgID=giantswarm")
    expect("defaults: klaus-gateway headers", get(kg, ["observability", "otlpHeaders", "X-Scope-OrgID"]), "giantswarm")
    expect("defaults: substrate tenant label", get(sub, ["podLabels", "observability.giantswarm.io/tenant"]), "giantswarm")
    expect("defaults: data-plane protocol", env(cv, ["gateway", "parameters", "dataPlaneEnv"], "OTEL_EXPORTER_OTLP_PROTOCOL"), "grpc")
    expect("defaults: data-plane tenant label", get(cv, ["gateway", "parameters", "podLabels", "observability.giantswarm.io/tenant"]), "giantswarm")
    for c in COMPONENTS:
        expect(f"defaults: {c} protocol", get(values[c], ["observability", "otel", "protocol"]), "grpc")
        expect(f"defaults: {c} headers", get(values[c], ["observability", "otel", "headers"]), "X-Scope-OrgID=giantswarm")
    expect("defaults: mcp-kubernetes", mcp_kubernetes(values), {
        "tracingExporter": "otlp", "otlpEndpoint": "otlp-gateway.kube-system.svc:4317", "otlpInsecure": "true",
        "otlpProtocol": "grpc", "otlpHeaders": "X-Scope-OrgID=giantswarm"})
    left = re.compile(r"^\s*(?:endpoint|protocol|headers|otlpEndpoint|otlpHeaders|otlpInsecure|otlpProtocol|tracingExporter|value|observability\.giantswarm\.io/tenant): auto$", re.M)
    if any(left.search(v) for v in values.values()):
        fail("defaults: an OTLP value reaches a release as `auto`: " + ", ".join(n for n, v in values.items() if left.search(v)))
    check_egress(conn, k8s, everywhere(("kube-system", "4317")), "defaults")
    check_components(conn, k8s, ("kube-system", "4317"), "defaults")
    print("ok: defaults: every exporter on the kube-system otlp-gateway, tenant as header or label, egress on kube-system:4317")


def case_customer(meta: str, conn_chart: str, tmp: str) -> None:
    flags = ["--set", f"{OTLP}.endpoint={CUSTOMER_EP}", "--set", f"{OTLP}.tenant=acme", "--set", f"{OTLP}.headers.X-Team=platform"]
    values, conn, k8s = meta_and_conn(meta, conn_chart, flags, tmp, "customer")
    expect("customer: exporters", set(exporters(values).values()), {CUSTOMER_EP})
    kagent, muster, kg, sub, cv = (values[n] for n in ("kagent", "muster", "klaus-gateway", "substrate", "agent-platform-connectivity"))
    for owner in ("controller", "harness"):
        expect(f"customer: kagent {owner} headers", env(kagent, [owner, "env"], "OTEL_EXPORTER_OTLP_HEADERS"), "X-Scope-OrgID=acme,X-Team=platform")
    expect("customer: muster headers", get(muster, ["muster", "observability", "otel", "headers"]), "X-Scope-OrgID=acme,X-Team=platform")
    expect("customer: klaus-gateway tenant header", get(kg, ["observability", "otlpHeaders", "X-Scope-OrgID"]), "acme")
    expect("customer: klaus-gateway extra header", get(kg, ["observability", "otlpHeaders", "X-Team"]), "platform")
    expect("customer: substrate tenant label", get(sub, ["podLabels", "observability.giantswarm.io/tenant"]), "acme")
    expect("customer: data-plane tenant label", get(cv, ["gateway", "parameters", "podLabels", "observability.giantswarm.io/tenant"]), "acme")
    params = doc(conn, "AgentgatewayParameters", "t")
    if not re.search(r"- name: OTEL_EXPORTER_OTLP_HEADERS\n\s+value: X-Team=platform$", params, re.M):
        fail("customer: the data plane's env does not carry the extra header (and only it: the tenant is the pod label)")
    expect("customer: tracing policy url", re.search(r'url: "([^"]+)"', doc(conn, "AgentgatewayPolicy", TRACING_POLICY)).group(1), CUSTOMER_EP)
    for c in COMPONENTS:
        expect(f"customer: {c} headers", get(values[c], ["observability", "otel", "headers"]), "X-Scope-OrgID=acme,X-Team=platform")
    expect("customer: mcp-kubernetes endpoint", mcp_kubernetes(values)["otlpEndpoint"], "otel-collector.customer-otel.svc:14317")
    expect("customer: mcp-kubernetes headers", mcp_kubernetes(values)["otlpHeaders"], "X-Scope-OrgID=acme,X-Team=platform")
    check_egress(conn, k8s, everywhere(("customer-otel", "14317")), "customer")
    check_components(conn, k8s, ("customer-otel", "14317"), "customer")
    print("ok: customer collector: every exporter, header and label follows; every egress rule selects customer-otel:14317 in both flavours")


def case_tls(meta: str, conn_chart: str, tmp: str) -> None:
    values, conn, _ = meta_and_conn(meta, conn_chart, ["--set", f"{OTLP}.endpoint=https://otlp.example.com"], tmp, "tls")
    kagent = values["kagent"]
    for signal in ("tracing", "logging"):
        expect(f"tls: kagent {signal} insecure", get(kagent, ["otel", signal, "exporter", "otlp", "insecure"]), "false")
    expect("tls: substrate signals left on", get(values["substrate"], ["otel", "traces", "enabled"]), None)
    check_egress(conn, {}, {p: ("cluster", "443") for p in [*KAGENT_POLICIES, *SUBSTRATE_POLICIES, MUSTER_POLICY, KLAUS_POLICY, DATAPLANE_POLICY]}, "tls")
    mk = mcp_kubernetes(values)
    expect("tls: mcp-kubernetes endpoint (the scheme's port)", mk["otlpEndpoint"], "otlp.example.com:443")
    expect("tls: mcp-kubernetes insecure", mk["otlpInsecure"], "false")
    check_components(conn, {}, ("cluster", "443"), "tls")
    print("ok: an https collector outside the cluster: kagent exports over TLS, every egress rule is the cluster entity on 443")


def case_explicit(meta: str, conn_chart: str, tmp: str) -> None:
    own = {
        "kagent.otel.tracing.exporter.otlp.endpoint": "http://traces.own-kagent.svc:4317",
        "muster.muster.observability.otel.endpoint": "http://collector.own-muster.svc:4317",
        "klausGateway.observability.otlpEndpoint": "http://collector.own-klaus.svc:4317",
        "substrate.otel.endpoint": "http://collector.own-substrate.svc:4317",
        "agent-manager.observability.otel.endpoint": "http://collector.own-agent-manager.svc:4317",
    }
    flags = ["--set", f"{OTLP}.endpoint={CUSTOMER_EP}",
             "--set", "gateway.parameters.dataPlaneEnv[0].name=OTEL_EXPORTER_OTLP_ENDPOINT",
             "--set", "gateway.parameters.dataPlaneEnv[0].value=http://collector.own-dataplane.svc:4317",
             "--set", "substrate.podLabels.observability\\.giantswarm\\.io/tenant=own",
             "--set", "muster.muster.observability.otel.headers=X-Scope-OrgID=own"]
    for k, v in own.items():
        flags += ["--set", f"{k}={v}"]
    values, conn, k8s = meta_and_conn(meta, conn_chart, flags, tmp, "explicit")
    expect("explicit: exporters", exporters(values), {
        "kagent.otel.tracing": own["kagent.otel.tracing.exporter.otlp.endpoint"],
        "kagent.otel.logging": CUSTOMER_EP,
        "muster": own["muster.muster.observability.otel.endpoint"],
        "klaus-gateway": own["klausGateway.observability.otlpEndpoint"],
        "substrate": own["substrate.otel.endpoint"],
        "data plane": "http://collector.own-dataplane.svc:4317",
        **{c: CUSTOMER_EP for c in COMPONENTS},
        "agent-manager": own["agent-manager.observability.otel.endpoint"],
    })
    expect("explicit: agent-manager OTLP rules", otlp_rules(doc(conn, "CiliumNetworkPolicy", COMPONENT_POLICIES["agent-manager"])),
           [("own-agent-manager", "4317")])
    expect("explicit: model-manager OTLP rules", otlp_rules(doc(conn, "CiliumNetworkPolicy", COMPONENT_POLICIES["model-manager"])),
           [("customer-otel", "14317")])
    expect("explicit: substrate tenant label", get(values["substrate"], ["podLabels", "observability.giantswarm.io/tenant"]), "own")
    expect("explicit: muster headers", get(values["muster"], ["muster", "observability", "otel", "headers"]), "X-Scope-OrgID=own")
    expect("explicit: kagent controller OTLP rules", sorted(otlp_rules(doc(conn, "CiliumNetworkPolicy", KAGENT_POLICIES[0]))),
           [("customer-otel", "14317"), ("own-kagent", "4317")])
    check_egress(conn, k8s, {
        MUSTER_POLICY: ("own-muster", "4317"), KLAUS_POLICY: ("own-klaus", "4317"),
        DATAPLANE_POLICY: ("own-dataplane", "4317"), **{p: ("own-substrate", "4317") for p in SUBSTRATE_POLICIES},
    }, "explicit")
    print("ok: explicit: a component's own key wins over the global one, its egress rule follows it, the others keep the global endpoint")


def case_empty_endpoint(meta: str, conn_chart: str, tmp: str) -> None:
    values, conn, k8s = meta_and_conn(meta, conn_chart, ["--set", f"{OTLP}.endpoint="], tmp, "empty")
    got = exporters(values)
    expect("empty: exporters", {k: v for k, v in got.items() if k != "data plane"},
           {"kagent.otel.tracing": '""', "kagent.otel.logging": '""', "muster": '""', "klaus-gateway": '""', "substrate": '""',
            **{c: '""' for c in COMPONENTS}})
    expect("empty: mcp-kubernetes exporter", mcp_kubernetes(values)["tracingExporter"], "none")
    check_components(conn, k8s, None, "empty")
    expect("empty: data-plane endpoint entry", got["data plane"], None)
    kagent = values["kagent"]
    expect("empty: kagent tracing enabled", get(kagent, ["otel", "tracing", "enabled"]), "false")
    expect("empty: kagent logging enabled", get(kagent, ["otel", "logging", "enabled"]), "false")
    expect("empty: kagent controller header", env(kagent, ["controller", "env"], "OTEL_EXPORTER_OTLP_HEADERS"), None)
    for signal in ("traces", "metrics", "logs"):
        expect(f"empty: substrate {signal} enabled (else localhost:4317)", get(values["substrate"], ["otel", signal, "enabled"]), "false")
    for kind, name in (("AgentgatewayPolicy", TRACING_POLICY), ("CiliumNetworkPolicy", DATAPLANE_POLICY),
                       ("CiliumNetworkPolicy", MUSTER_POLICY), ("CiliumNetworkPolicy", KLAUS_POLICY)):
        if (kind, name) in conn:
            fail(f"empty: {kind} {name} renders with no endpoint")
    check_egress(conn, k8s, {p: None for p in [*KAGENT_POLICIES, *SUBSTRATE_POLICIES]}, "empty")
    print("ok: empty endpoint: nothing exports, no tracing policy, no OTLP egress rule")


def case_empty_tenant(meta: str, conn_chart: str, tmp: str) -> None:
    values, _, _ = meta_and_conn(meta, conn_chart, ["--set", f"{OTLP}.tenant="], tmp, "notenant")
    for name, text in values.items():
        if "X-Scope-OrgID" in text.replace(f"X-Scope-OrgID: giantswarm\n", "") and name != "agent-platform-connectivity":
            fail(f"no tenant: the {name} release still sends an X-Scope-OrgID header")
    kagent = values["kagent"]
    expect("no tenant: kagent controller header", env(kagent, ["controller", "env"], "OTEL_EXPORTER_OTLP_HEADERS"), None)
    expect("no tenant: kagent Harness header", env(kagent, ["harness", "env"], "OTEL_EXPORTER_OTLP_HEADERS"), None)
    expect("no tenant: muster headers", get(values["muster"], ["muster", "observability", "otel", "headers"]), '""')
    expect("no tenant: klaus-gateway headers", get(values["klaus-gateway"], ["observability", "otlpHeaders"]), "{}")
    for c in COMPONENTS:
        expect(f"no tenant: {c} headers", get(values[c], ["observability", "otel", "headers"]), '""')
    expect("no tenant: mcp-kubernetes headers", mcp_kubernetes(values)["otlpHeaders"], '""')
    expect("no tenant: substrate tenant label", get(values["substrate"], ["podLabels", "observability.giantswarm.io/tenant"]), None)
    expect("no tenant: data-plane tenant label",
           get(values["agent-platform-connectivity"], ["gateway", "parameters", "podLabels", "observability.giantswarm.io/tenant"]), None)
    print("ok: empty tenant: no X-Scope-OrgID header, no tenant label")


def case_header_conflict(meta: str) -> None:
    r = helm(meta, VM + ON + ["--set", f"{OTLP}.headers.X-Scope-OrgID=other"])
    if r.returncode == 0 or "differs from global.observability.traces.otlp.tenant" not in r.stderr:
        fail(f"an X-Scope-OrgID header that differs from the tenant did not fail the render naming the tenant\n{r.stderr}")
    render(meta, VM + ON + ["--set", f"{OTLP}.headers.X-Scope-OrgID=giantswarm"])
    print("ok: a header that disagrees with the tenant fails; one that agrees renders")


def case_http(meta: str, conn_chart: str, tmp: str) -> None:
    http = ["--set", f"{OTLP}.protocol=http/protobuf", "--set", f"{OTLP}.endpoint=http://otel-collector.customer-otel.svc"]
    r = helm(meta, VM + ON + http)
    for key in ("klausGateway.observability.otlpEndpoint", "substrate.otel.endpoint", "kagent.otel.logging.exporter.otlp.endpoint"):
        if r.returncode == 0 or key not in r.stderr:
            fail(f"http/protobuf with {key} at auto did not fail the render naming it\n{r.stderr}")
    grpc = "http://otel-collector.customer-otel.svc:4317"
    flags = http + ["--set", f"klausGateway.observability.otlpEndpoint={grpc}", "--set", f"substrate.otel.endpoint={grpc}",
                    "--set", f"kagent.otel.logging.exporter.otlp.endpoint={grpc}"]
    values, conn, k8s = meta_and_conn(meta, conn_chart, flags, tmp, "http")
    expect("http: kagent tracing protocol", get(values["kagent"], ["otel", "tracing", "exporter", "otlp", "protocol"]), "http/protobuf")
    expect("http: muster protocol", get(values["muster"], ["muster", "observability", "otel", "protocol"]), "http/protobuf")
    expect("http: data-plane protocol",
           env(values["agent-platform-connectivity"], ["gateway", "parameters", "dataPlaneEnv"], "OTEL_EXPORTER_OTLP_PROTOCOL"), "http/protobuf")
    expect("http: tracing policy protocol", re.search(r"protocol: (\S+)", doc(conn, "AgentgatewayPolicy", TRACING_POLICY)).group(1), "HTTP")
    check_egress(conn, k8s, {MUSTER_POLICY: ("customer-otel", "4318"), DATAPLANE_POLICY: ("customer-otel", "4318"),
                             KLAUS_POLICY: ("customer-otel", "4317")}, "http")
    for c in COMPONENTS:
        expect(f"http: {c} protocol", get(values[c], ["observability", "otel", "protocol"]), "http/protobuf")
    expect("http: mcp-kubernetes endpoint (the protocol's port)", mcp_kubernetes(values)["otlpEndpoint"], "otel-collector.customer-otel.svc:4318")
    expect("http: mcp-kubernetes protocol", mcp_kubernetes(values)["otlpProtocol"], "http/protobuf")
    check_components(conn, k8s, ("customer-otel", "4318"), "http")
    expect("http: kagent controller OTLP rules", sorted(otlp_rules(doc(conn, "CiliumNetworkPolicy", KAGENT_POLICIES[0]))),
           [("customer-otel", "4317"), ("customer-otel", "4318")])
    print("ok: http/protobuf: refused for the gRPC-only exporters at auto; with their own endpoints the rest speak http/protobuf on 4318")


def main(meta: str, conn_chart: str) -> int:
    with tempfile.TemporaryDirectory() as tmp:
        case_defaults(meta, conn_chart, tmp)
        case_customer(meta, conn_chart, tmp)
        case_tls(meta, conn_chart, tmp)
        case_explicit(meta, conn_chart, tmp)
        case_empty_endpoint(meta, conn_chart, tmp)
        case_empty_tenant(meta, conn_chart, tmp)
        case_header_conflict(meta)
        case_http(meta, conn_chart, tmp)
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit("usage: verify-otlp-global.py <meta chart> <connectivity chart>")
    sys.exit(main(sys.argv[1], sys.argv[2]))
