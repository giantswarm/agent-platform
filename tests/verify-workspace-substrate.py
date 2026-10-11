#!/usr/bin/env python3
"""Assert Substrate's workspace driver registration (giantswarm/agent-platform#888).

A Session's workspace is a Substrate volume mount of the workspace's
read-write-many volume: ate-api-server calls the CSI driver's controller service
over gRPC and atelet the node plugin's socket, both found through a
cluster-scoped CSIDriverConfig named after the driver. The connectivity release
renders it from workspaces.substrate.csiDriver. Each case below pins one
property:

- with the fixture (tests/fixtures/workspaces-substrate-values.yaml) ONE
  CSIDriverConfig of the driver's name with its controller endpoint, node
  socket and mTLS through Substrate's pod identity; tls off renders enabled:
  false without usePodIdentity; an empty node socket and server name are left
  out; the meta chart forwards the block to the connectivity release;
- the gate: nothing with the switch off, without a name, or without Substrate's
  CRDs (substrate off and ate.dev/v1alpha1 CSIDriverConfig not served); a
  cluster that serves the API renders it with the component off;
- Cilium: ate-api-server's egress admits the cluster on the controller
  endpoint's port exactly while the object renders;
- no --preview argument reaches any Substrate component: the line's range
  carries no preview gate (agent-substrate/substrate#1994 is open upstream);
- the guards in both charts, each naming the key: a controller endpoint
  missing or without a port (the schema's pattern, then the guard behind it),
  tls without the pod identity, driver keys without a name, a preset whose
  provisioner is not the driver;
- on a cluster (LIVE_CONTEXT=<kube context>, else skipped): a provided class
  of another provisioner is refused, one of the driver's passes. The check
  creates and deletes StorageClasses named ap-verify-workspace-substrate-*
  there: a lab cluster, never an installation.

Deliberately stdlib-only: the CI image has no PyYAML. HELM selects the binary.
"""

import os
import re
import subprocess
import sys

HELM = os.environ.get("HELM", "helm")
LIVE_CONTEXT = os.environ.get("LIVE_CONTEXT", "")
FIXTURE = "tests/fixtures/workspaces-substrate-values.yaml"
DRIVER = "nfs.csi.k8s.io"
ENDPOINT = "tcp://csi-nfs-controller.agentlab-workspaces.svc.cluster.local:50051"
API = "ate.dev/v1alpha1/CSIDriverConfig"
CI = ["--set", "components.flux.enabled=false"]
CONN = ["--set", "ingress.parentRefs[0].name=x", "--set", "networkPolicy.enabled=true", "--set", "networkPolicy.flavor=cilium"]
SUBSTRATE = ["--set", "components.substrate.enabled=true"]
CSI = "workspaces.substrate.csiDriver"


def fail(msg: str) -> None:
    sys.exit(f"FAIL: {msg}")


def ok(msg: str) -> None:
    print(f"ok: {msg}")


def helm(chart: str, flags: list, expect_fail: bool = False, ci_values: bool = True, live: bool = False) -> str:
    cmd = [HELM, "template", "t", chart]
    if ci_values:
        cmd += ["-f", f"{chart}/ci/ci-values.yaml", *CI]
    if live:
        cmd += ["--dry-run=server", "--kube-context", LIVE_CONTEXT]
    cmd += flags
    r = subprocess.run(cmd, capture_output=True, text=True)
    if expect_fail:
        if r.returncode == 0:
            fail(f"the render passed but had to fail: {' '.join(flags)}")
        return r.stderr
    if r.returncode != 0:
        fail(f"the render failed: {' '.join(flags)}\n{r.stderr}")
    return r.stdout


def documents(render: str) -> dict:
    """(kind, name) -> the document, for every document that has both."""
    out = {}
    for doc in render.split("\n---\n"):
        kind = re.search(r"^kind: (\S+)", doc, re.M)
        name = re.search(r"^  name: (\S+)", doc, re.M)
        if kind and name:
            out[(kind.group(1), name.group(1))] = doc.rstrip("\n") + "\n"
    return out


def must_fail(chart: str, flags: list, needle: str, what: str, ci_values: bool = True, live: bool = False) -> str:
    err = helm(chart, flags, expect_fail=True, ci_values=ci_values, live=live)
    if needle not in err:
        fail(f"{what} failed for another reason (expected {needle!r}):\n{err}")
    return err


def configs(render: str) -> dict:
    return {n: d for (k, n), d in documents(render).items() if k == "CSIDriverConfig"}


def spec(doc: str) -> str:
    return doc[doc.index("\nspec:\n") + 1:]


def controller_egress(render: str) -> bool:
    doc = documents(render)[("CiliumNetworkPolicy", "substrate-ate-api-server")]
    return bool(re.search(r'- toEntities:\n\s+- cluster\n\s+toPorts:\n\s+- ports:\n\s+- port: "50051"\n', doc))


def conn(flags: list, live: bool = False) -> str:
    return helm(CONNECTIVITY, [*CONN, *flags], ci_values=False, live=live)


def live() -> None:
    if not LIVE_CONTEXT:
        print("skip: LIVE_CONTEXT is empty; the provisioner check of a provided class needs a cluster")
        return
    kubectl = ["kubectl", "--context", LIVE_CONTEXT]
    other, same = "ap-verify-workspace-substrate-other", "ap-verify-workspace-substrate-nfs"
    manifests = "\n---\n".join(
        f"apiVersion: storage.k8s.io/v1\nkind: StorageClass\nmetadata:\n  name: {name}\nprovisioner: {provisioner}\n"
        for name, provisioner in ((other, "example.com/other"), (same, DRIVER)))
    subprocess.run([*kubectl, "apply", "-f", "-"], input=manifests, text=True, check=True, capture_output=True)
    try:
        provided = ["-f", FIXTURE, *SUBSTRATE, "--set", "workspaces.storage.storageClass.create=false"]
        must_fail(CONNECTIVITY, [*CONN, *provided, "--set", f"workspaces.storage.storageClassName={other}"],
                  f'{CSI}.name ({DRIVER}) differs from the provisioner of the StorageClass "{other}" (workspaces.storage.storageClassName), example.com/other',
                  "a provided class of another provisioner", ci_values=False, live=True)
        if DRIVER not in configs(conn([*provided, "--set", f"workspaces.storage.storageClassName={same}"], live=True)):
            fail("a provided class of the driver rendered no CSIDriverConfig")
        ok(f"live ({LIVE_CONTEXT}): a provided class of another provisioner is refused, one of the driver's passes")
    finally:
        subprocess.run([*kubectl, "delete", "storageclass", other, same, "--ignore-not-found"], check=True, capture_output=True)


def main() -> int:
    # --- the object -------------------------------------------------------------------------
    render = conn(["-f", FIXTURE, *SUBSTRATE])
    found = configs(render)
    if set(found) != {DRIVER}:
        fail(f"expected exactly one CSIDriverConfig named {DRIVER}, got {sorted(found)}")
    want = (f"spec:\n  driverName: {DRIVER}\n  controllerEndpoint: \"{ENDPOINT}\"\n"
            "  nodeSocketOverride: \"unix:///var/lib/kubelet/plugins/csi-nfsplugin/csi.sock\"\n"
            "  tls:\n    enabled: true\n    usePodIdentity: true\n    serverName: \"csi-nfs-controller.agentlab-workspaces.svc\"\n")
    if spec(found[DRIVER]) != want:
        fail(f"the CSIDriverConfig's spec is off:\n{found[DRIVER]}")
    if "apiVersion: ate.dev/v1alpha1\n" not in found[DRIVER] or "namespace:" in found[DRIVER]:
        fail(f"the CSIDriverConfig is not a cluster-scoped ate.dev/v1alpha1 object:\n{found[DRIVER]}")
    plain = configs(conn(["-f", FIXTURE, *SUBSTRATE, "--set", f"{CSI}.tls.enabled=false", "--set", f"{CSI}.nodeSocketOverride=", "--set", f"{CSI}.tls.serverName="]))
    if spec(plain[DRIVER]) != f"spec:\n  driverName: {DRIVER}\n  controllerEndpoint: \"{ENDPOINT}\"\n  tls:\n    enabled: false\n":
        fail(f"tls off, no node socket and no server name render another spec:\n{plain[DRIVER]}")
    wiring = documents(helm(META, ["-f", FIXTURE]))[("HelmRelease", "agent-platform-connectivity")]
    if not re.search(rf"      substrate:\n        csiDriver:\n          controllerEndpoint: {re.escape(ENDPOINT)}\n          name: {DRIVER}\n", wiring):
        fail(f"the meta chart did not forward {CSI} to the connectivity release:\n{wiring}")
    ok(f"one CSIDriverConfig {DRIVER} with the controller endpoint, node socket and mTLS through the pod identity; tls off, an empty socket and server name "
       "render the bare spec; the meta chart forwards the block")

    # --- the gate ---------------------------------------------------------------------------
    for what, flags in (("the switch off", ["-f", FIXTURE, *SUBSTRATE, "--set", "workspaces.enabled=false"]),
                        ("no driver name", ["-f", FIXTURE, *SUBSTRATE, "--set", f"{CSI}.name=", "--set", f"{CSI}.controllerEndpoint=",
                                            "--set", f"{CSI}.nodeSocketOverride=", "--set", f"{CSI}.tls.serverName="]),
                        ("no Substrate CRDs", ["-f", FIXTURE, "--set", "components.substrate.enabled=false"])):
        if configs(conn(flags)):
            fail(f"a CSIDriverConfig rendered with {what}")
    if DRIVER not in configs(conn(["-f", FIXTURE, "--set", "components.substrate.enabled=false", "--api-versions", API])):
        fail(f"a cluster serving {API} rendered no CSIDriverConfig with the substrate component off")
    ok(f"nothing with the switch off, without a name or without Substrate's CRDs; a cluster serving {API} renders it with the component off")

    # --- the network policy and the preview gate --------------------------------------------
    if not controller_egress(render):
        fail("ate-api-server's egress does not admit the cluster on the controller endpoint's port")
    if controller_egress(conn(["-f", FIXTURE, *SUBSTRATE, "--set", "workspaces.enabled=false"])):
        fail("ate-api-server's egress admits the controller endpoint's port with the switch off")
    if "--preview" in render or "--preview" in helm(META, ["-f", FIXTURE]):
        fail("a --preview argument reached a Substrate component: the line carries no preview gate")
    ok("ate-api-server's egress admits the cluster on the controller endpoint's port exactly while the object renders; no --preview argument anywhere")

    # --- the guards -------------------------------------------------------------------------
    for chart, flags, ci in ((META, ["-f", FIXTURE], True), (CONNECTIVITY, [*CONN, "-f", FIXTURE, *SUBSTRATE], False)):
        must_fail(chart, [*flags, "--set", f"{CSI}.controllerEndpoint="],
                  f'{CSI}.controllerEndpoint is "": Substrate\'s ate-api-server calls', "a driver without a controller endpoint", ci_values=ci)
        err = must_fail(chart, [*flags, "--set", f"{CSI}.controllerEndpoint=tcp://controller"], "controllerEndpoint", "an endpoint without a port (the schema)", ci_values=ci)
        # Helm 3 and 4 word the schema's pattern refusal differently; both say "match pattern".
        if "match pattern" not in err.lower() and "should match" not in err.lower():
            fail(f"an endpoint without a port failed for another reason than the schema's pattern:\n{err}")
        must_fail(chart, [*flags, "--set", f"{CSI}.controllerEndpoint=tcp://controller", "--skip-schema-validation"],
                  f'{CSI}.controllerEndpoint is "tcp://controller"', "an endpoint without a port (the guard behind the schema)", ci_values=ci)
        must_fail(chart, [*flags, "--set", f"{CSI}.tls.usePodIdentity=false"],
                  f"{CSI}.tls.enabled is true but tls.usePodIdentity is not", "tls without the pod identity", ci_values=ci)
        must_fail(chart, [*flags, "--set", f"{CSI}.name="], f"{CSI}.name is empty but other csiDriver keys are set", "driver keys without a name", ci_values=ci)
        must_fail(chart, [*flags, "--set", "workspaces.storage.storageClass.preset=efs", "--set", "workspaces.storage.storageClass.efs.fileSystemId=fs-0123456789abcdef0"],
                  f"{CSI}.name ({DRIVER}) differs from the provisioner of the workspace StorageClass rendered from preset efs (efs.csi.aws.com)",
                  "a preset whose provisioner is not the driver", ci_values=ci)
    ok("both charts: an endpoint missing or without a port (schema, then the guard), tls without the pod identity, driver keys without a name and a preset of another "
       "provisioner fail naming the key")

    live()
    return 0


if __name__ == "__main__":
    META, CONNECTIVITY = sys.argv[1], sys.argv[2]
    sys.exit(main())
