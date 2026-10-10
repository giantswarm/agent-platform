#!/usr/bin/env python3
"""Assert the workspace StorageClass value and its presets (giantswarm/agent-platform#900).

A workspace is one read-write-many volume shared by its sync and every Session,
used by git, so it needs a class that keeps POSIX semantics; a cluster rarely has
one. With workspaces on, workspaces.storage.storageClassName names it (required),
and workspaces.storage.storageClass.create renders it in the connectivity release
from a preset. Each case below pins one property:

- the name is required: the switch on without it fails both charts naming the key;
  the meta chart forwards the block to the connectivity release and nothing of it
  to the workspace-manager release, whose chart declares no key for the class yet;
  with the switch off a preset renders nothing anywhere (the off render's
  byte-identity is verify-workspace-manager's);
- the three presets (tests/fixtures/workspaces-storage-*-values.yaml) render ONE
  StorageClass of that name, Delete / Immediate / expandable, with the preset's
  provisioner, parameters and mount options: efs (efs.csi.aws.com, an access
  point per volume on the file system, TLS), azureFiles (file.csi.azure.com,
  protocol nfs, PremiumV2_LRS, a private endpoint, nconnect and actimeo) and nfs
  (nfs.csi.k8s.io, the server's export, NFS 4.1 hard); the meta chart renders
  each fixture too; parameters merge over the preset's, mountOptions replace
  them, an empty networkEndpointType drops the parameter; create false renders
  no class;
- the guards: create without a preset, an unknown preset (the schema's enum, and
  the guard behind it), efs without fileSystemId, nfs without server, the
  azureFiles preset with another protocol, each naming the key; the SMB refusal
  names the missing file modes and links;
- on a cluster (LIVE_CONTEXT=<kube context>, else skipped): a class of the name
  another owner created is not replaced (create: true fails naming the owner), a
  provided Azure Files class over SMB is refused, one over NFS passes. The check
  creates and deletes StorageClasses named ap-verify-workspace-storage-* there:
  a lab cluster, never an installation.

Deliberately stdlib-only: the CI image has no PyYAML. HELM selects the binary.
"""

import os
import re
import subprocess
import sys

HELM = os.environ.get("HELM", "helm")
LIVE_CONTEXT = os.environ.get("LIVE_CONTEXT", "")
NAME = "workspaces-rwx"
PRESETS = {
    "efs": ("tests/fixtures/workspaces-storage-efs-values.yaml", "efs.csi.aws.com",
            {"basePath": "/workspaces", "directoryPerms": "700", "fileSystemId": "fs-0123456789abcdef0", "provisioningMode": "efs-ap"},
            ["tls"]),
    "azureFiles": ("tests/fixtures/workspaces-storage-azure-files-values.yaml", "file.csi.azure.com",
                   {"networkEndpointType": "privateEndpoint", "protocol": "nfs", "skuName": "PremiumV2_LRS"},
                   ["nconnect=4", "actimeo=30"]),
    "nfs": ("tests/fixtures/workspaces-storage-nfs-values.yaml", "nfs.csi.k8s.io",
            {"mountPermissions": "0777", "server": "nfs-server.agentlab-workspaces.svc.cluster.local", "share": "/"},
            ["nfsvers=4.1", "hard"]),
}
CI = ["--set", "components.flux.enabled=false"]
CONN = ["--set", "ingress.parentRefs[0].name=x"]
ON = ["--set", "workspaces.enabled=true", "--set", f"workspaces.storage.storageClassName={NAME}"]


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


def must_have(doc: str, lines: tuple, what: str) -> None:
    for line in lines:
        if line not in doc:
            fail(f"{what} lacks {line!r}:\n{doc}")


def must_fail(chart: str, flags: list, needle: str, what: str, ci_values: bool = True, live: bool = False) -> str:
    err = helm(chart, flags, expect_fail=True, ci_values=ci_values, live=live)
    if needle not in err:
        fail(f"{what} failed for another reason (expected {needle!r}):\n{err}")
    return err


def storage_classes(render: str) -> dict:
    return {n: d for (k, n), d in documents(render).items() if k == "StorageClass"}


def the_class(connectivity: str, flags: list) -> str:
    classes = storage_classes(helm(connectivity, [*CONN, *flags], ci_values=False))
    if set(classes) != {NAME}:
        fail(f"expected exactly one StorageClass named {NAME}, got {sorted(classes)}: {' '.join(flags)}")
    return classes[NAME]


def parameters(doc: str) -> dict:
    m = re.search(r"^parameters:\n((?:  \S.*\n)*)", doc, re.M)
    return dict(re.findall(r'^  (\S+): "(.*)"$', m.group(1), re.M)) if m else {}


def mount_options(doc: str) -> list:
    m = re.search(r"^mountOptions:\n((?:  - .*\n)*)", doc, re.M)
    return re.findall(r'^  - "(.*)"$', m.group(1), re.M) if m else []


def live(connectivity: str) -> None:
    if not LIVE_CONTEXT:
        print("skip: LIVE_CONTEXT is empty; the lookup guards (an existing class, a provided SMB class) need a cluster")
        return
    kubectl = ["kubectl", "--context", LIVE_CONTEXT]
    smb, nfs = "ap-verify-workspace-storage-smb", "ap-verify-workspace-storage-nfs"
    manifests = "\n---\n".join(
        f"apiVersion: storage.k8s.io/v1\nkind: StorageClass\nmetadata:\n  name: {name}\nprovisioner: file.csi.azure.com\nparameters:\n  protocol: {protocol}\n  skuName: Premium_LRS\n"
        for name, protocol in ((smb, "smb"), (nfs, "nfs")))
    subprocess.run([*kubectl, "apply", "-f", "-"], input=manifests, text=True, check=True, capture_output=True)
    try:
        existing = ["--set", f"workspaces.storage.storageClassName={nfs}"]
        err = must_fail(connectivity, [*CONN, *ON, *existing, "--set", "workspaces.storage.storageClass.create=true",
                                       "--set", "workspaces.storage.storageClass.preset=nfs", "--set", "workspaces.storage.storageClass.nfs.server=x"],
                        f'StorageClass "{nfs}" (workspaces.storage.storageClassName) exists and is not this release\'s (no Helm release)',
                        "create: true over a class another owner created", ci_values=False, live=True)
        must_fail(connectivity, [*CONN, *ON, "--set", f"workspaces.storage.storageClassName={smb}"],
                  f'StorageClass "{smb}" (workspaces.storage.storageClassName) is Azure Files over SMB (provisioner file.csi.azure.com, parameters.protocol "smb")',
                  "a provided Azure Files class over SMB", ci_values=False, live=True)
        if NAME in helm(connectivity, [*CONN, *ON, *existing], ci_values=False, live=True):
            fail("a provided class over NFS rendered something named after it")
        ok(f"live ({LIVE_CONTEXT}): create: true over another owner's class is refused naming the owner, a provided SMB class is refused, a provided NFS class passes")
    finally:
        subprocess.run([*kubectl, "delete", "storageclass", smb, nfs, "--ignore-not-found"], check=True, capture_output=True)


def main(meta: str, connectivity: str) -> int:
    # --- the name is required, and forwarded -------------------------------------------------
    must_fail(meta, ["--set", "workspaces.enabled=true"], "workspaces.storage.storageClassName is empty", "the meta chart: the switch on without a class")
    must_fail(connectivity, [*CONN, "--set", "workspaces.enabled=true"], "workspaces.storage.storageClassName is empty",
              "the connectivity chart: the switch on without a class", ci_values=False)
    on_docs = documents(helm(meta, ON))
    must_have(on_docs[("HelmRelease", "agent-platform-connectivity")], (f"    workspaces:\n      enabled: true\n      storage:\n", f"        storageClassName: {NAME}\n"),
              "the connectivity HelmRelease")
    if "\n    storage:" in on_docs[("HelmRelease", "workspace-manager")]:
        fail("the workspace-manager HelmRelease carries a storage block: its chart declares no key for the class yet")
    for chart, flags in ((meta, []), (connectivity, [*CONN])):
        off = helm(chart, [*flags, "-f", PRESETS["efs"][0], "--set", "workspaces.enabled=false"], ci_values=chart == meta)
        if storage_classes(off) or (chart == meta and re.search(r"^    workspaces:", off, re.M)):
            fail(f"{chart}: a preset with the switch off rendered a class or forwarded the block")
    ok("the class is required with the switch on in both charts; the meta chart forwards the block to connectivity and nothing of it "
       "to workspace-manager; off, a preset renders nothing")

    # --- the presets --------------------------------------------------------------------------
    for preset, (fixture, provisioner, params, mount) in PRESETS.items():
        doc = the_class(connectivity, ["-f", fixture])
        must_have(doc, (f"  name: {NAME}\n", f"provisioner: {provisioner}\n", "reclaimPolicy: Delete\n", "volumeBindingMode: Immediate\n",
                        "allowVolumeExpansion: true\n", 'app.kubernetes.io/managed-by: "Helm"\n'), f"the {preset} class")
        if parameters(doc) != params or mount_options(doc) != mount:
            fail(f"the {preset} class's parameters or mount options are off:\n{doc}")
        meta_docs = documents(helm(meta, ["-f", fixture]))
        must_have(meta_docs[("HelmRelease", "agent-platform-connectivity")], ("        create: true\n", f"        preset: {preset}\n"),
                  f"the connectivity HelmRelease with the {preset} fixture")
    ok("efs, azureFiles and nfs each render one class of the name (Delete, Immediate, expandable) with the preset's provisioner, parameters and mount options; the meta chart forwards each")

    efs = the_class(connectivity, ["-f", PRESETS["efs"][0], "--set", "workspaces.storage.storageClass.parameters.gidRangeStart=1000"])
    if parameters(efs) != {**PRESETS["efs"][2], "gidRangeStart": "1000"}:
        fail(f"parameters did not merge over the efs preset's:\n{efs}")
    nfs = the_class(connectivity, ["-f", PRESETS["nfs"][0], "--set", "workspaces.storage.storageClass.mountOptions[0]=nfsvers=4.2"])
    if mount_options(nfs) != ["nfsvers=4.2"]:
        fail(f"mountOptions did not replace the nfs preset's:\n{nfs}")
    az = the_class(connectivity, ["-f", PRESETS["azureFiles"][0], "--set", "workspaces.storage.storageClass.azureFiles.networkEndpointType=",
                                  "--set", "workspaces.storage.storageClass.azureFiles.skuName=Premium_LRS"])
    if parameters(az) != {"protocol": "nfs", "skuName": "Premium_LRS"}:
        fail(f"an empty networkEndpointType did not drop the parameter, or skuName did not move:\n{az}")
    if storage_classes(helm(connectivity, [*CONN, "-f", PRESETS["efs"][0], "--set", "workspaces.storage.storageClass.create=false"], ci_values=False)):
        fail("create: false rendered a class")
    ok("parameters merge over the preset's, mountOptions replace them, an empty networkEndpointType drops the parameter, skuName moves; create false renders no class")

    # --- the guards ---------------------------------------------------------------------------
    for chart, flags, ci in ((meta, [], True), (connectivity, [*CONN], False)):
        must_fail(chart, [*flags, *ON, "--set", "workspaces.storage.storageClass.create=true"],
                  'workspaces.storage.storageClass.preset is "": the presets are efs', "create without a preset", ci_values=ci)
        must_fail(chart, [*flags, *ON, "--set", "workspaces.storage.storageClass.create=true", "--set", "workspaces.storage.storageClass.preset=bogus"],
                  "storageClass/preset': value must be one of '', 'efs', 'azureFiles', 'nfs'", "an unknown preset (the schema)", ci_values=ci)
        must_fail(chart, [*flags, *ON, "--set", "workspaces.storage.storageClass.create=true", "--set", "workspaces.storage.storageClass.preset=bogus", "--skip-schema-validation"],
                  'workspaces.storage.storageClass.preset is "bogus": the presets are efs', "an unknown preset (the guard behind the schema)", ci_values=ci)
        must_fail(chart, [*flags, "-f", PRESETS["efs"][0], "--set", "workspaces.storage.storageClass.efs.fileSystemId="],
                  "workspaces.storage.storageClass.efs.fileSystemId is empty", "efs without a file system", ci_values=ci)
        must_fail(chart, [*flags, "-f", PRESETS["nfs"][0], "--set", "workspaces.storage.storageClass.nfs.server="],
                  "workspaces.storage.storageClass.nfs.server is empty", "nfs without a server", ci_values=ci)
        err = must_fail(chart, [*flags, "-f", PRESETS["azureFiles"][0], "--set", "workspaces.storage.storageClass.parameters.protocol=smb"],
                        'workspaces.storage.storageClass.parameters.protocol is "smb" on the azureFiles preset', "azureFiles over SMB", ci_values=ci)
        must_have(err, ("fixes every file's mode at mount and has no symbolic links",), "the SMB refusal")
    ok("both charts: create without a preset, an unknown preset (schema, then the guard), efs without fileSystemId, nfs without server and azureFiles over SMB fail naming the key; the SMB refusal names the missing file modes and links")

    live(connectivity)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2]))
