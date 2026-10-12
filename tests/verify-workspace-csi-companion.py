#!/usr/bin/env python3
"""Assert the workspaces' CSI companion (giantswarm/agent-platform#889).

Substrate mounts a Session's workspace through the cluster's file driver, which
it drives itself: ate-api-server calls the driver's controller service over the
network and atelet a node plugin that propagates Substrate's volume directory.
The cloud drivers a cluster runs (Amazon EFS, Azure Files) listen on an in-pod
socket and propagate the kubelet directory only, and those driver apps are the
cluster operators' to configure. So the connectivity chart runs the driver's
own image twice more for Substrate while workspaces are on and
workspaces.substrate.csiCompanion.enabled: the controller behind an Envoy
sidecar terminating mTLS for ate-api-server alone, and the node plugin on the
Substrate worker nodes. Each case below pins one property:

- off by default: nothing named csi-companion in the connectivity render
  without the block, with the installation's own driver registration
  (tests/fixtures/workspaces-substrate-values.yaml, whose CSIDriverConfig
  renders unchanged), with the switch off, or with the companion off beside a
  storage preset (then no CSIDriverConfig either: nothing to derive it from);
- each preset (tests/fixtures/workspaces-csi-companion-{efs,azure-files}-values.yaml):
  exactly the companion's objects — two ServiceAccounts, the proxy ConfigMap,
  ONE Service, ONE Deployment, ONE DaemonSet, the two Cilium policies, the two
  PolicyExceptions, ONE CSIDriverConfig named after the preset's driver and,
  for efs, a ClusterRole and binding on nodes get — the Service ahead of the
  Deployment (Substrate's service-DNS signer certifies a pod for the Services
  that select it); the CSIDriverConfig derived from the companion: the
  Service as the controller endpoint, the node socket under the plugins
  directory, mTLS through the pod identity with the Service's name verified;
  the controller: the preset's image and arguments, the driver's socket in an
  emptyDir, the Envoy sidecar as root (the driver's socket is root-owned) on
  the proxy port, its serving certificate a projected PodCertificate of the
  service-DNS signer and its client trust the pod-identity CA's live
  ClusterTrustBundle; the proxy requires a client certificate and admits
  exactly one SPIFFE URI SAN, ate-api-server's; the node plugin: hostNetwork,
  the driver privileged with Substrate's directory mounted Bidirectional and
  its socket on a hostPath under the kubelet's plugins directory, no
  node-driver-registrar (the kubelet never learns of it), the preset's host
  paths (efs-utils' state and configuration for efs, the node's cloud
  configuration read-only for azureFiles), the node selector derived from the
  WorkerPool template plus the os label; everything namespaced in the release
  namespace but the PolicyExceptions (kyvernoPolicies.policyExceptionNamespace),
  none with Kyverno off; the ingress policy admits ate-api-server from
  Substrate's namespace on the proxy port, the egress policy DNS, the API
  server, the cloud's APIs and (efs only) the instance metadata service, and
  ate-api-server's own egress the controller endpoint's port; the kubernetes
  flavour renders the ingress policy; the meta chart forwards the block;
- cloud identity by values: the IRSA role annotates both ServiceAccounts, the
  workload-identity client id annotates them and labels both pod templates;
  nothing by default;
- the overrides: the image, the controller's arguments appended and the node's
  replaced, the node selector and tolerations set on the node block win over
  the pool's, another socket directory moves the hostPath and the registration,
  other ports move the Service, the listener, the policies and the endpoint,
  another client identity the SAN;
- the installation's own workspaces.substrate.csiDriver wins over the derived
  registration while the companion still renders (ate-api-server's egress
  follows the own endpoint's port); a csiDriver naming another driver is
  refused;
- the guards in both charts, each naming the key: the companion without a
  preset, with an unknown one (the schema's enum, then the guard behind it),
  beside a storage preset of another driver, a client identity that is no
  SPIFFE ID, a socket directory outside the kubelet's plugins directory, an
  unknown key (the schema).

The PolicyExceptions' rule lists are held to the pod specs by
tests/verify-kyverno.py. Deliberately stdlib-only: the CI image has no PyYAML.
HELM selects the binary.
"""

import os
import re
import subprocess
import sys

HELM = os.environ.get("HELM", "helm")
NS = "agent-platform"
POLEX_NS = "policy-exceptions"
SUBSTRATE_NS = "ate-system"
FIXTURES = {
    "efs": "tests/fixtures/workspaces-csi-companion-efs-values.yaml",
    "azureFiles": "tests/fixtures/workspaces-csi-companion-azure-files-values.yaml",
}
SUBSTRATE_FIXTURE = "tests/fixtures/workspaces-substrate-values.yaml"
PRESETS = {
    "efs": {
        "driver": "efs.csi.aws.com",
        "image": "gsoci.azurecr.io/giantswarm/aws-efs-csi-driver:v3.5.0",
        "controllerArgs": ["--endpoint=$(CSI_ENDPOINT)", "--logtostderr", "--v=2", "--delete-access-point-root-dir=false"],
        "nodeArgs": ["--endpoint=$(CSI_ENDPOINT)", "--logtostderr", "--v=2", "--vol-metrics-opt-in=false"],
    },
    "azureFiles": {
        "driver": "file.csi.azure.com",
        "image": "gsoci.azurecr.io/giantswarm/azurefile-csi:v1.35.7",
        "controllerArgs": ["--v=2", "--endpoint=$(CSI_ENDPOINT)", "--drivername=file.csi.azure.com", "--cloud-config-secret-name=",
                           "--cloud-config-secret-namespace=", "--allow-empty-cloud-config=true", "--user-agent-suffix=agent-platform"],
        "nodeArgs": ["--v=2", "--endpoint=$(CSI_ENDPOINT)", "--nodeid=$(KUBE_NODE_NAME)", "--drivername=file.csi.azure.com", "--cloud-config-secret-name=",
                     "--cloud-config-secret-namespace=", "--allow-empty-cloud-config=true", "--mount-permissions=0777", "--user-agent-suffix=agent-platform"],
    },
}
IDENTITY = "spiffe://cluster.local/ns/ate-system/sa/ate-api-server"
CONTROLLER = "csi-companion-controller"
NODE = "csi-companion-node"
PROXY_CONFIG = "csi-companion-proxy"
SOCKET_DIR = "/var/lib/kubelet/plugins/agent-platform-csi-companion"
SUBSTRATE_DIR = "/var/lib/ate"
PROXY_IMAGE = "gsoci.azurecr.io/giantswarm/envoy:v1.39.3"
PROBE_IMAGE = "gsoci.azurecr.io/giantswarm/livenessprobe:v2.19.0"
CI = ["--set", "components.flux.enabled=false"]
CONN = ["--namespace", NS, "--set", "ingress.parentRefs[0].name=x", "--set", "networkPolicy.enabled=true", "--set", "networkPolicy.flavor=cilium",
        "--set", "components.substrate.enabled=true", "--set", "kyvernoPolicies.enabled=true",
        "--api-versions", "kyverno.io/v1", "--api-versions", "cilium.io/v2"]
COMPANION = "workspaces.substrate.csiCompanion"
CSI = "workspaces.substrate.csiDriver"
POOL = "kagent.substrateWorkerPool.template"
POLICY = "agent-platform-connectivity-csi-companion-controller"


def fail(msg: str) -> None:
    sys.exit(f"FAIL: {msg}")


def ok(msg: str) -> None:
    print(f"ok: {msg}")


def helm(chart: str, flags: list, expect_fail: bool = False, ci_values: bool = True) -> str:
    cmd = [HELM, "template", "t", chart]
    if ci_values:
        cmd += ["-f", f"{chart}/ci/ci-values.yaml", *CI]
    cmd += flags
    r = subprocess.run(cmd, capture_output=True, text=True)
    if expect_fail:
        if r.returncode == 0:
            fail(f"the render passed but had to fail: {' '.join(flags)}")
        return r.stderr
    if r.returncode != 0:
        fail(f"the render failed: {' '.join(flags)}\n{r.stderr}")
    return r.stdout


def must_fail(chart: str, flags: list, needle: str, what: str, ci_values: bool = True) -> str:
    err = helm(chart, flags, expect_fail=True, ci_values=ci_values)
    if needle not in err:
        fail(f"{what} failed for another reason (expected {needle!r}):\n{err}")
    return err


def documents(render: str) -> dict:
    """(kind, name) -> the document, in render order, for every document that has both."""
    out = {}
    for doc in render.split("\n---\n"):
        kind = re.search(r"^kind: (\S+)", doc, re.M)
        name = re.search(r"^  name: (\S+)", doc, re.M)
        if kind and name:
            out[(kind.group(1), name.group(1))] = doc.rstrip("\n") + "\n"
    return out


def companion(docs: dict) -> dict:
    return {k: d for k, d in docs.items() if "csi-companion" in k[1] or k[0] == "CSIDriverConfig"}


def spec(doc: str) -> str:
    return doc[doc.index("\nspec:\n") + 1:]


def container(doc: str, name: str) -> str:
    """One container's block of a pod template (the top-level containers list)."""
    m = re.search(rf"^        - name: {re.escape(name)}\n(.*?)(?=^        - name: |^      volumes:\n|\Z)", doc, re.M | re.S)
    if not m:
        fail(f"no container {name} in:\n{doc}")
    return m.group(0)


def containers(doc: str) -> list:
    block = doc[doc.index("\n      containers:\n"):doc.index("\n      volumes:\n")]
    return re.findall(r"^        - name: (\S+)$", block, re.M)


def args(block: str) -> list:
    m = re.search(r"^          args:\n((?:            - .*\n)+)", block, re.M)
    return [re.sub(r'^"(.*)"$', r"\1", line.strip()[2:]) for line in m.group(1).splitlines()] if m else []


def node_selector(doc: str) -> dict:
    m = re.search(r"^      nodeSelector:\n((?:        \S.*\n)+)", doc, re.M)
    return dict(line.strip().split(": ", 1) for line in m.group(1).splitlines()) if m else {}


def conn(flags: list) -> str:
    return helm(CONNECTIVITY, [*CONN, *flags], ci_values=False)


def ate_api_server_egress_ports(render: str) -> set:
    doc = documents(render)[("CiliumNetworkPolicy", "substrate-ate-api-server")]
    return set(re.findall(r'- toEntities:\n\s+- cluster\n\s+toPorts:\n\s+- ports:\n\s+- port: "(\d+)"\n', doc))


def off() -> None:
    for what, flags in (("without the block", []),
                        ("with the installation's own driver registration", ["-f", SUBSTRATE_FIXTURE]),
                        ("with the switch off", ["-f", FIXTURES["efs"], "--set", "workspaces.enabled=false"]),
                        ("with the companion off beside a storage preset", ["-f", FIXTURES["efs"], "--set", f"{COMPANION}.enabled=false"])):
        render = conn(flags)
        if "csi-companion" in render:
            fail(f"something named csi-companion rendered {what}")
        configs = [k for k in documents(render) if k[0] == "CSIDriverConfig"]
        want = [("CSIDriverConfig", "nfs.csi.k8s.io")] if "own" in what else []
        if configs != want:
            fail(f"{what}: expected the CSIDriverConfigs {want}, got {configs}")
    ok("nothing named csi-companion without the block, with the installation's own driver registration (its CSIDriverConfig unchanged), with the switch off, "
       "or with the companion off beside a storage preset (then no CSIDriverConfig either)")


def preset(name: str) -> None:
    p = PRESETS[name]
    efs = name == "efs"
    render = conn(["-f", FIXTURES[name]])
    docs = documents(render)
    found = companion(docs)
    want = {("ServiceAccount", CONTROLLER), ("ServiceAccount", NODE), ("ConfigMap", PROXY_CONFIG), ("Service", CONTROLLER), ("Deployment", CONTROLLER),
            ("DaemonSet", NODE), ("CiliumNetworkPolicy", f"{POLICY}-ingress"), ("CiliumNetworkPolicy", f"{POLICY}-egress"),
            ("PolicyException", CONTROLLER), ("PolicyException", NODE), ("CSIDriverConfig", p["driver"])}
    if efs:
        want |= {("ClusterRole", "t-csi-companion"), ("ClusterRoleBinding", "t-csi-companion")}
    if set(found) != want:
        fail(f"{name}: expected exactly the companion's objects {sorted(want)}, got {sorted(found)}")
    if render.index(found[("Service", CONTROLLER)]) > render.index(found[("Deployment", CONTROLLER)]):
        fail(f"{name}: the Service renders after the Deployment it selects; the service-DNS signer certifies the pod for the Services that exist")

    # The derived registration.
    want_spec = (f"spec:\n  driverName: {p['driver']}\n  controllerEndpoint: \"tcp://{CONTROLLER}.{NS}.svc.cluster.local:50051\"\n"
                 f"  nodeSocketOverride: \"unix://{SOCKET_DIR}/csi.sock\"\n"
                 f"  tls:\n    enabled: true\n    usePodIdentity: true\n    serverName: \"{CONTROLLER}.{NS}.svc\"\n")
    if spec(found[("CSIDriverConfig", p["driver"])]) != want_spec:
        fail(f"{name}: the derived CSIDriverConfig is off:\n{found[('CSIDriverConfig', p['driver'])]}")
    if ate_api_server_egress_ports(render) != {"50051"}:
        fail(f"{name}: ate-api-server's egress admits {ate_api_server_egress_ports(render)}, not the controller endpoint's port alone")

    # The controller.
    deploy = found[("Deployment", CONTROLLER)]
    if containers(deploy) != ["driver", "liveness-probe", "envoy"]:
        fail(f"{name}: the controller runs {containers(deploy)}, not driver, liveness-probe, envoy")
    driver = container(deploy, "driver")
    if f"image: {p['image']}\n" not in driver or args(driver) != p["controllerArgs"]:
        fail(f"{name}: the controller's driver is not the preset's image with its arguments:\n{driver}")
    if "value: unix:///csi/csi.sock\n" not in driver or ("CSI_NODE_NAME" in driver) != efs or ("AWS_STS_REGIONAL_ENDPOINTS" in driver) != efs:
        fail(f"{name}: the controller's driver environment is off:\n{driver}")
    envoy = container(deploy, "envoy")
    if f"image: {PROXY_IMAGE}\n" not in envoy or "runAsUser: 0\n" not in envoy or "containerPort: 10000\n" not in envoy:
        fail(f"{name}: the Envoy sidecar is not the pinned image as root on the proxy port:\n{envoy}")
    if f"image: {PROBE_IMAGE}\n" not in container(deploy, "liveness-probe"):
        fail(f"{name}: the controller's liveness probe is not the pinned image")
    for needle in ("signerName: servicedns.podcert.ate.dev/identity\n", "credentialBundlePath: credential-bundle.pem\n",
                   "signerName: podidentity.podcert.ate.dev/identity\n", "podcert.ate.dev/canarying: live\n", "emptyDir:\n"):
        if needle not in deploy:
            fail(f"{name}: the controller's volumes miss {needle.strip()}:\n{deploy}")
    for needle in ("hostNetwork", "privileged", "hostPath", "mountPropagation"):
        if needle in deploy:
            fail(f"{name}: the controller carries {needle}; nothing of the node is needed there")
    proxy = found[("ConfigMap", PROXY_CONFIG)]
    sans = re.findall(r"san_type: URI\n\s+matcher:\n\s+exact: \"([^\"]+)\"", proxy)
    if "require_client_certificate: true\n" not in proxy or sans != [IDENTITY]:
        fail(f"{name}: the proxy does not require a client certificate bearing exactly {IDENTITY} (SANs {sans}):\n{proxy}")
    if "port_value: 10000 }" not in proxy or "path: /csi/csi.sock\n" not in proxy:
        fail(f"{name}: the proxy does not listen on the proxy port in front of the driver's socket:\n{proxy}")
    if "trusted_ca: { filename: /run/podidentity-ca/trust-bundle.pem }" not in proxy or "certificate_chain: { filename: /run/servicedns/credential-bundle.pem }" not in proxy:
        fail(f"{name}: the proxy's certificates are not the projected service-DNS bundle and pod-identity trust:\n{proxy}")
    service = found[("Service", CONTROLLER)]
    if not re.search(r"  selector:\n    app.kubernetes.io/component: controller\n    app.kubernetes.io/name: csi-companion\n  ports:\n    - name: grpc\n      port: 50051\n      targetPort: grpc\n", service):
        fail(f"{name}: the Service is not port 50051 to the proxy's grpc port on the controller's selector:\n{service}")

    # The node plugin.
    ds = found[("DaemonSet", NODE)]
    if containers(ds) != ["liveness-probe", "driver"]:
        fail(f"{name}: the node plugin runs {containers(ds)}, not liveness-probe, driver: no node-driver-registrar")
    if "registrar" in ds or "registration" in ds:
        fail(f"{name}: the node plugin registers with the kubelet; atelet reaches its socket through the CSIDriverConfig alone")
    if "hostNetwork: true\n" not in ds or "priorityClassName: system-node-critical\n" not in ds:
        fail(f"{name}: the node plugin is not on the node's network at node-critical priority")
    if node_selector(ds) != {"kubernetes.io/os": "linux"}:
        fail(f"{name}: the node selector without a pool template is {node_selector(ds)}, not the os label alone")
    nd = container(ds, "driver")
    if f"image: {p['image']}\n" not in nd or args(nd) != p["nodeArgs"] or "privileged: true\n" not in nd:
        fail(f"{name}: the node plugin's driver is not the preset's image with its arguments, privileged:\n{nd}")
    if f"mountPath: {SUBSTRATE_DIR}\n              mountPropagation: Bidirectional\n" not in nd:
        fail(f"{name}: {SUBSTRATE_DIR} is not mounted Bidirectional into the node plugin:\n{nd}")
    if f"- name: socket-dir\n          hostPath:\n            path: {SOCKET_DIR}\n" not in ds or f"- name: substrate-dir\n          hostPath:\n            path: {SUBSTRATE_DIR}\n" not in ds:
        fail(f"{name}: the node plugin's socket and Substrate directory are not the host paths:\n{ds}")
    efs_paths = ("path: /var/run/agent-platform-csi-companion/efs\n" in ds, "path: /var/lib/agent-platform-csi-companion/efs-utils\n" in ds,
                 "mountPath: /var/run/efs\n" in nd, "mountPath: /var/amazon/efs\n" in nd)
    azure_paths = ("path: /etc/kubernetes\n" in ds, "mountPath: /etc/kubernetes\n              readOnly: true\n" in nd, "AZURE_CREDENTIAL_FILE" in nd)
    if any(efs_paths) != efs or all(efs_paths) != efs or any(azure_paths) == efs or all(azure_paths) == efs:
        fail(f"{name}: the preset's host paths are off (efs {efs_paths}, azure {azure_paths}):\n{ds}")

    # Namespaces, the exceptions, the policies, the RBAC.
    for (kind, obj), doc in found.items():
        ns = re.search(r"^  namespace: (\S+)$", doc, re.M)
        if kind in ("ClusterRole", "ClusterRoleBinding", "CSIDriverConfig"):
            if ns:
                fail(f"{name}: {kind} {obj} is namespaced")
            continue
        want_ns = POLEX_NS if kind == "PolicyException" else NS
        if not ns or ns.group(1) != want_ns:
            fail(f"{name}: {kind} {obj} is in {ns.group(1) if ns else 'no namespace'}, not {want_ns}")
    for obj, component in ((CONTROLLER, "controller"), (NODE, "node")):
        pe = found[("PolicyException", obj)]
        if f"        namespaces:\n        - {NS}\n        selector:\n          matchLabels:\n            app.kubernetes.io/name: csi-companion\n            app.kubernetes.io/component: {component}\n" not in pe:
            fail(f"{name}: PolicyException {obj} does not select the {component} by its labels in {NS}:\n{pe}")
    if any(k[0] == "PolicyException" for k in companion(documents(conn(["-f", FIXTURES[name], "--set", "kyvernoPolicies.enabled=false"])))):
        fail(f"{name}: a PolicyException rendered with Kyverno off")
    ingress = found[("CiliumNetworkPolicy", f"{POLICY}-ingress")]
    if f"app: ate-api-server\n            io.kubernetes.pod.namespace: {SUBSTRATE_NS}\n      toPorts:\n        - ports:\n            - port: \"10000\"\n" not in ingress:
        fail(f"{name}: the ingress policy does not admit ate-api-server from {SUBSTRATE_NS} on the proxy port:\n{ingress}")
    egress = found[("CiliumNetworkPolicy", f"{POLICY}-egress")]
    if "- kube-apiserver\n" not in egress or '- world\n      toPorts:\n        - ports:\n            - port: "443"\n' not in egress or ("169.254.169.254/32" in egress) != efs:
        fail(f"{name}: the egress policy is off (the API server, the cloud's APIs, the metadata service for efs only):\n{egress}")
    k8s = documents(conn(["-f", FIXTURES[name], "--set", "networkPolicy.flavor=kubernetes"]))
    if ("NetworkPolicy", f"{POLICY}-ingress") not in k8s or ("NetworkPolicy", f"{POLICY}-egress") in k8s or "port: 10000\n" not in k8s[("NetworkPolicy", f"{POLICY}-ingress")]:
        fail(f"{name}: the kubernetes flavour does not render the ingress policy alone on the proxy port")
    if efs:
        role = found[("ClusterRole", "t-csi-companion")]
        if not role.endswith('rules:\n  - apiGroups: [""]\n    resources: ["nodes"]\n    verbs: ["get"]\n'):
            fail(f"{name}: the ClusterRole grants more than nodes get:\n{role}")
        binding = found[("ClusterRoleBinding", "t-csi-companion")]
        if binding.count("kind: ServiceAccount") != 2 or f"name: {CONTROLLER}\n" not in binding or f"name: {NODE}\n" not in binding:
            fail(f"{name}: the ClusterRoleBinding does not bind both ServiceAccounts:\n{binding}")

    # The meta chart.
    wiring = documents(helm(META, ["-f", FIXTURES[name]]))[("HelmRelease", "agent-platform-connectivity")]
    block = re.search(r"^        csiCompanion:\n((?:          .*\n)+)", wiring, re.M)
    if not block or "          enabled: true\n" not in block.group(1) or f"          preset: {name}\n" not in block.group(1):
        fail(f"{name}: the meta chart did not forward {COMPANION} to the connectivity release:\n{wiring}")
    ok(f"{name}: exactly the companion's objects, the Service ahead of the Deployment; the registration derived from the companion; the controller runs the preset's "
       "driver behind Envoy as root with the projected service-DNS certificate and pod-identity trust, requiring a client certificate with exactly ate-api-server's "
       "SPIFFE ID; the node plugin privileged on the node's network with Substrate's directory Bidirectional, its socket under the plugins directory, no registrar, "
       "the preset's host paths; the release namespace throughout, the exceptions in the policy namespace and none with Kyverno off; the policies in both "
       "flavours; the meta chart forwards the block")


def identity() -> None:
    arn = "arn:aws:iam::123456789012:role/agent-platform-csi-companion"
    client = "00000000-0000-0000-0000-000000000000"
    for name in PRESETS:
        docs = companion(documents(conn(["-f", FIXTURES[name]])))
        for kind in ("ServiceAccount", "Deployment", "DaemonSet"):
            for (k, obj), doc in docs.items():
                if k == kind and ("annotations:" in doc or "azure.workload.identity" in doc or "eks.amazonaws.com" in doc):
                    fail(f"{name}: {kind} {obj} carries a cloud identity by default:\n{doc}")
    docs = companion(documents(conn(["-f", FIXTURES["efs"], "--set", f"{COMPANION}.cloudIdentity.aws.roleARN={arn}"])))
    for obj in (CONTROLLER, NODE):
        if f"  annotations:\n    eks.amazonaws.com/role-arn: {arn}\n" not in docs[("ServiceAccount", obj)]:
            fail(f"the IRSA role does not annotate ServiceAccount {obj}:\n{docs[('ServiceAccount', obj)]}")
    if any("azure.workload.identity" in d for d in docs.values()):
        fail("the IRSA role rendered a workload-identity label")
    docs = companion(documents(conn(["-f", FIXTURES["azureFiles"], "--set", f"{COMPANION}.cloudIdentity.azure.workloadIdentityClientID={client}"])))
    for obj in (CONTROLLER, NODE):
        if f"  annotations:\n    azure.workload.identity/client-id: {client}\n" not in docs[("ServiceAccount", obj)]:
            fail(f"the workload-identity client id does not annotate ServiceAccount {obj}:\n{docs[('ServiceAccount', obj)]}")
    for key in (("Deployment", CONTROLLER), ("DaemonSet", NODE)):
        if docs[key].count('azure.workload.identity/use: "true"\n') != 1 or docs[key].index("template:") > docs[key].index('azure.workload.identity/use: "true"'):
            fail(f"the workload-identity label is not on the pod template of {key[0]} {key[1]} alone:\n{docs[key]}")
    if any("eks.amazonaws.com" in d for d in docs.values()):
        fail("the workload-identity client id rendered an IRSA annotation")
    ok("no cloud identity by default; the IRSA role annotates both ServiceAccounts, the workload-identity client id annotates them and labels both pod templates")


def overrides() -> None:
    pool = ["--set", f"{POOL}.nodeSelector.giantswarm\\.io/machine-pool=substrate", "--set", f"{POOL}.tolerations[0].key=substrate",
            "--set", f"{POOL}.tolerations[0].operator=Exists", "--set", f"{POOL}.tolerations[0].effect=NoSchedule"]
    docs = companion(documents(conn([
        "-f", FIXTURES["efs"], *pool,
        "--set", f"{COMPANION}.image.repository=giantswarm/aws-efs-csi-driver-fips", "--set", f"{COMPANION}.image.tag=v9.9.9",
        "--set", f"{COMPANION}.controller.extraArgs[0]=--foo=bar", "--set", f"{COMPANION}.node.args[0]=--only",
        "--set", f"{COMPANION}.controller.nodeSelector.role=control"])))
    deploy, ds = docs[("Deployment", CONTROLLER)], docs[("DaemonSet", NODE)]
    image = "gsoci.azurecr.io/giantswarm/aws-efs-csi-driver-fips:v9.9.9"
    if f"image: {image}\n" not in container(deploy, "driver") or f"image: {image}\n" not in container(ds, "driver"):
        fail("the image override does not reach both drivers")
    if args(container(deploy, "driver")) != [*PRESETS["efs"]["controllerArgs"], "--foo=bar"] or args(container(ds, "driver")) != ["--only"]:
        fail(f"extraArgs do not append to the preset's arguments or args do not replace them: {args(container(deploy, 'driver'))}, {args(container(ds, 'driver'))}")
    if node_selector(ds) != {"giantswarm.io/machine-pool": "substrate", "kubernetes.io/os": "linux"}:
        fail(f"the node selector is not the pool template's plus the os label: {node_selector(ds)}")
    if "      tolerations:\n        - effect: NoSchedule\n          key: substrate\n          operator: Exists\n" not in ds:
        fail(f"the node plugin does not carry the pool template's tolerations:\n{ds}")
    if node_selector(deploy) != {"role": "control"}:
        fail(f"the controller's node selector is not the one set: {node_selector(deploy)}")
    ds = companion(documents(conn(["-f", FIXTURES["efs"], *pool, "--set", f"{COMPANION}.node.nodeSelector.role=csi",
                                   "--set", f"{COMPANION}.node.tolerations[0].key=csi", "--set", f"{COMPANION}.node.tolerations[0].operator=Exists"])))[("DaemonSet", NODE)]
    if node_selector(ds) != {"role": "csi", "kubernetes.io/os": "linux"} or "key: substrate" in ds or "      tolerations:\n        - key: csi\n          operator: Exists\n" not in ds:
        fail(f"the node block's selector and tolerations do not win over the pool template's: {node_selector(ds)}\n{ds}")
    render = conn(["-f", FIXTURES["efs"], "--set", f"{COMPANION}.node.socketDir=/var/lib/kubelet/plugins/other/", "--set", f"{COMPANION}.service.port=50052",
                   "--set", f"{COMPANION}.proxy.port=10001", "--set", f"{COMPANION}.proxy.clientIdentity=spiffe://cluster.local/ns/ate-system/sa/other"])
    docs = companion(documents(render))
    cfg = spec(docs[("CSIDriverConfig", "efs.csi.aws.com")])
    if 'controllerEndpoint: "tcp://csi-companion-controller.agent-platform.svc.cluster.local:50052"\n' not in cfg or 'nodeSocketOverride: "unix:///var/lib/kubelet/plugins/other/csi.sock"\n' not in cfg:
        fail(f"another socket directory and Service port do not move the registration:\n{cfg}")
    if "path: /var/lib/kubelet/plugins/other\n" not in docs[("DaemonSet", NODE)]:
        fail("another socket directory does not move the node plugin's hostPath")
    if "      port: 50052\n" not in docs[("Service", CONTROLLER)] or "containerPort: 10001\n" not in docs[("Deployment", CONTROLLER)]:
        fail("other ports do not move the Service and the sidecar")
    if "port_value: 10001 }" not in docs[("ConfigMap", PROXY_CONFIG)] or 'exact: "spiffe://cluster.local/ns/ate-system/sa/other"' not in docs[("ConfigMap", PROXY_CONFIG)]:
        fail("another proxy port and client identity do not move the listener and the SAN")
    if '- port: "10001"' not in docs[("CiliumNetworkPolicy", f"{POLICY}-ingress")] or ate_api_server_egress_ports(render) != {"50052"}:
        fail("other ports do not move the ingress policy and ate-api-server's egress")
    ok("the image, appended controller arguments and replaced node arguments, the pool template's selector and tolerations (the node block's win), the controller's "
       "selector, another socket directory, other ports and another client identity move the render where they belong")


def own_driver() -> None:
    own = ["--set", f"{CSI}.name=efs.csi.aws.com", "--set", f"{CSI}.controllerEndpoint=tcp://own.storage.svc:4444",
           "--set", f"{CSI}.nodeSocketOverride=unix:///var/lib/kubelet/plugins/own/csi.sock", "--set", f"{CSI}.tls.serverName=own.storage.svc"]
    render = conn(["-f", FIXTURES["efs"], *own])
    docs = companion(documents(render))
    want = ('spec:\n  driverName: efs.csi.aws.com\n  controllerEndpoint: "tcp://own.storage.svc:4444"\n  nodeSocketOverride: "unix:///var/lib/kubelet/plugins/own/csi.sock"\n'
            '  tls:\n    enabled: true\n    usePodIdentity: true\n    serverName: "own.storage.svc"\n')
    if spec(docs[("CSIDriverConfig", "efs.csi.aws.com")]) != want:
        fail(f"the installation's own csiDriver does not win over the derived registration:\n{docs[('CSIDriverConfig', 'efs.csi.aws.com')]}")
    if ("Deployment", CONTROLLER) not in docs or ("DaemonSet", NODE) not in docs:
        fail("the companion does not render beside the installation's own csiDriver")
    if ate_api_server_egress_ports(render) != {"4444"}:
        fail(f"ate-api-server's egress admits {ate_api_server_egress_ports(render)}, not the own endpoint's port alone")
    for chart, flags, ci in ((META, ["-f", FIXTURES["efs"]], True), (CONNECTIVITY, [*CONN, "-f", FIXTURES["efs"]], False)):
        must_fail(chart, [*flags, "--set", f"{CSI}.name=nfs.csi.k8s.io", "--set", f"{CSI}.controllerEndpoint=tcp://own.storage.svc:4444"],
                  f"{CSI}.name (nfs.csi.k8s.io) is not the driver of the CSI companion's preset efs (efs.csi.aws.com)", "a csiDriver naming another driver", ci_values=ci)
    ok("the installation's own csiDriver wins over the derived registration while the companion still renders, ate-api-server's egress following its port; "
       "a csiDriver naming another driver is refused in both charts")


def guards() -> None:
    for chart, flags, ci in ((META, ["-f", FIXTURES["efs"]], True), (CONNECTIVITY, [*CONN, "-f", FIXTURES["efs"]], False)):
        must_fail(chart, [*flags, "--set", f"{COMPANION}.preset="], f'{COMPANION}.enabled is true but {COMPANION}.preset is ""', "the companion without a preset", ci_values=ci)
        err = must_fail(chart, [*flags, "--set", f"{COMPANION}.preset=nfs"], "preset", "an unknown preset (the schema)", ci_values=ci)
        if "one of" not in err:
            fail(f"an unknown preset failed for another reason than the schema's enum:\n{err}")
        must_fail(chart, [*flags, "--set", f"{COMPANION}.preset=nfs", "--skip-schema-validation"], f'{COMPANION}.preset is "nfs"', "an unknown preset (the guard)", ci_values=ci)
        must_fail(chart, [*flags, "--set", "workspaces.storage.storageClass.preset=azureFiles"],
                  f"{CSI}.name (efs.csi.aws.com) differs from the provisioner of the workspace StorageClass rendered from preset azureFiles (file.csi.azure.com)",
                  "a storage preset of another driver", ci_values=ci)
        must_fail(chart, [*flags, "--set", f"{COMPANION}.proxy.clientIdentity=ate-api-server"], f'{COMPANION}.proxy.clientIdentity is "ate-api-server"',
                  "a client identity that is no SPIFFE ID", ci_values=ci)
        must_fail(chart, [*flags, "--set", f"{COMPANION}.node.socketDir=/var/lib/csi"], f'{COMPANION}.node.socketDir is "/var/lib/csi"',
                  "a socket directory outside the plugins directory", ci_values=ci)
        must_fail(chart, [*flags, "--set", f"{COMPANION}.unknownKey=1"], "unknownKey", "an unknown key (the schema)", ci_values=ci)
    ok("both charts: the companion without a preset, with an unknown one (schema, then the guard), beside a storage preset of another driver, a client identity "
       "that is no SPIFFE ID, a socket directory outside the plugins directory and an unknown key fail naming the key")


def main() -> int:
    off()
    for name in PRESETS:
        preset(name)
    identity()
    overrides()
    own_driver()
    guards()
    return 0


if __name__ == "__main__":
    META, CONNECTIVITY = sys.argv[1], sys.argv[2]
    sys.exit(main())
