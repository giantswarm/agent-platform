#!/usr/bin/env python3
"""Assert the workspace-manager component behind the one switch workspaces.enabled
(giantswarm/agent-platform#884).

Workspaces are an Organization's repositories mirrored on one shared volume in the
kagent namespace, a directory per Session, and the provider sign-ins; the
workspace-manager (giantswarm/workspace-manager) serves them as MCP tools through
muster. Each case below pins one property of its wiring:

- off by default: no release, and the render is byte-identical to the render
  without the component, its roster entry and blocks removed (default, CI and kagent
  shapes of the meta chart, the connectivity chart's default and CI renders): the
  connectivity release receives neither the
  roster entry (gatedRoster) nor the three blocks (gatedValues);
- the switch: workspaces.enabled turns the component on, beside the storage
  slices' workspaces.storage and workspaces.substrate (the lab's block,
  tests/fixtures/workspaces-lab-values.yaml), which both schemas take; an explicit
  components.workspace-manager.enabled that disagrees fails the render either way
  round, one that agrees passes;
- on with two provider instances (a GitHub one and a second kind,
  tests/fixtures/workspaces-values.yaml): ONE OCIRepository and ONE HelmRelease that
  dependsOn muster, the block forwarded — the pinned Service name, the OAuth resource
  server, the chart's own unpinned muster registration (forwardToken, no audience,
  no authorization server), the provider instances, the sign-in store's and the
  grant's keys — every credential a Secret reference {key, name} and nothing else;
  workspaces.namespace derived from kagent's; the keys left empty are not forwarded,
  and nothing of workspaces.storage is (verify-workspace-storage covers the class; the
  component chart declares no key for it yet);
  nothing under muster.* moves (the muster release and the muster block forwarded
  to connectivity are unchanged) and no MCPServer renders anywhere for the
  component or a provider;
- inline credentials, a duplicate or non-DNS provider name, a provider without a
  kind fail the render naming the path; a workspaces.namespace that differs from
  kagent's fails naming the key, kagent.namespaceOverride moves it;
- the connectivity chart, cilium: the ingress (muster, kagent's controller, the
  kubelet's probes, the metrics port), the egress (DNS with the proxy clause, the
  kube-apiserver, the Dex issuer by name, each provider instance's hosts on their
  ports — github.com and api.github.com for a GitHub instance without url), the
  muster-to and the kagent-controller-to policies; kubernetes: the same as
  NetworkPolicy; without kagent no kagent rule or policy;
- the connectivity guards: muster off with the MCPServer on, a provider of another
  kind without url, a bad CIDR;
- the schema refuses a non-boolean switch; the BOM pins the exact version and the
  pin reaches the OCIRepository.

Deliberately stdlib-only: the CI image has no PyYAML. HELM selects the binary.
"""

import os
import re
import subprocess
import sys
import tempfile

HELM = os.environ.get("HELM", "helm")
NAME = "workspace-manager"
REPOSITORY = "oci://gsoci.azurecr.io/charts/giantswarm"
RANGE_RE = r'semver: "(>=[^"]+ <1\.0\.0)"'
FIXTURE = "tests/fixtures/workspaces-values.yaml"
CI = ["--set", "components.flux.enabled=false"]
ON = ["-f", FIXTURE]
KAGENT = ["--set", "components.kagent.enabled=true", "--set", "kagent.harness.snapshotLocation=s3://ci/agents"]
IDENTITY = [
    "--set", "global.domain=ci.example.com",
    "--set", "global.identity.issuerUrl=https://dex.ci.example.com",
    "--set", "global.identity.clientId=platform",
    "--set", "global.identity.existingSecret=platform-oauth",
]
CREDENTIALS = ("privateKey", "clientSecret", "token", "encryptionKey", "signingKey")


def fail(msg: str) -> None:
    sys.exit(f"FAIL: {msg}")


def ok(msg: str) -> None:
    print(f"ok: {msg}")


def helm(chart: str, flags: list, expect_fail: bool = False, ci_values: bool = True, cwd: str | None = None) -> str:
    cmd = [HELM, "template", "t", chart]
    if ci_values:
        cmd += ["-f", f"{chart}/ci/ci-values.yaml", *CI]
    cmd += flags
    r = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd)
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


def block(doc: str, key: str, indent: int) -> str:
    """The lines of `key:` at `indent` spaces in a rendered document, the key line included."""
    m = re.search(rf"^{' ' * indent}{re.escape(key)}:.*\n(?:(?:{' ' * (indent + 1)}.*|{' ' * indent}- .*)?\n)*", doc, re.M)
    return m.group(0) if m else ""


def must_have(doc: str, lines: tuple, what: str) -> None:
    for line in lines:
        if line not in doc:
            fail(f"{what} lacks {line!r}:\n{doc}")


def must_fail(chart: str, flags: list, needle: str, what: str, ci_values: bool = True) -> None:
    err = helm(chart, flags, expect_fail=True, ci_values=ci_values)
    if needle not in err:
        fail(f"{what} failed for another reason (expected {needle!r}):\n{err}")


def golden(meta: str, connectivity: str) -> None:
    """With the switch off, the renders are byte-identical to the same chart's renders
    without the component: its roster entry and its blocks removed (a Helm null)."""
    absent = [x for key in (f"components.{NAME}", NAME, "workspaceManager", "workspaces") for x in ("--set", f"{key}=null")]
    shapes = [
        (meta, ["-f", f"{meta}/ci/ci-values.yaml"]),
        (meta, []),
        (meta, ["-f", f"{meta}/ci/ci-values.yaml", *KAGENT]),
        (connectivity, ["--set", "ingress.parentRefs[0].name=x"]),
        (connectivity, ["-f", f"{connectivity}/ci/ci-values.yaml", "--set", "ingress.parentRefs[0].name=x"]),
    ]
    for chart, flags in shapes:
        head = helm(chart, flags, ci_values=False)
        ref = helm(chart, [*flags, *absent], ci_values=False)
        if head != ref:
            with tempfile.NamedTemporaryFile("w", suffix=".ref") as a, tempfile.NamedTemporaryFile("w", suffix=".head") as b:
                a.write(ref), b.write(head), a.flush(), b.flush()
                diff = subprocess.run(["diff", "-u", a.name, b.name], capture_output=True, text=True).stdout
            fail(f"the switch-off render of {chart} {' '.join(flags)} differs from the render without the component\n{diff[:4000]}")
    ok("switch off: the meta (default, CI, kagent) and connectivity (default, CI) renders are byte-identical to those without the component")


def main(meta: str, connectivity: str) -> int:
    # --- off by default -------------------------------------------------------
    off_docs = documents(helm(meta, []))
    for kind in ("OCIRepository", "HelmRelease"):
        if (kind, NAME) in off_docs:
            fail(f"workspaces are not off by default: the {kind} {NAME} rendered")
    conn = off_docs[("HelmRelease", "agent-platform-connectivity")]
    if f"      {NAME}:\n" in conn:
        fail(f"the roster forwarded to connectivity names {NAME} while workspaces are off (components.{NAME}.gatedRoster)")
    for name in (NAME, "workspaceManager", "workspaces"):
        if re.search(rf"^    {re.escape(name)}:", conn, re.M):
            fail(f"the {name} block reached the connectivity release while workspaces are off (components.{NAME}.gatedValues)")
    if NAME in helm(connectivity, ["--set", "ingress.parentRefs[0].name=x"], ci_values=False):
        fail(f"the connectivity chart renders something named {NAME} with its defaults")
    ok("off by default: no release, no roster entry and no block reach connectivity, connectivity renders nothing of it")
    golden(meta, connectivity)

    # --- the switch ------------------------------------------------------------
    must_fail(meta, [*ON, "--set", f"components.{NAME}.enabled=false"], "workspaces are one switch", "the switch on with the component off")
    must_fail(meta, ["--set", f"components.{NAME}.enabled=true"], "workspaces are one switch", "the component on with the switch off")
    helm(meta, [*ON, "--set", f"components.{NAME}.enabled=true"])
    must_fail(meta, ["--set", "workspaces.enabled=maybe"], "workspaces", "a non-boolean switch")
    ok("one switch: workspaces.enabled turns the component on, a disagreeing toggle fails either way, an agreeing one passes; the schema refuses a non-boolean")

    # --- on with two provider instances --------------------------------------------
    on_docs = documents(helm(meta, ON))
    for kind in ("OCIRepository", "HelmRelease"):
        if (kind, NAME) not in on_docs:
            fail(f"workspaces.enabled=true rendered no {kind} named {NAME}")
    oci = on_docs[("OCIRepository", NAME)]
    must_have(oci, (f"url: {REPOSITORY}/{NAME}",), "the OCIRepository")
    rng = re.search(RANGE_RE, oci)
    if not rng:
        fail(f"the OCIRepository's range is not a floor below 1.0.0:\n{oci}")
    hr = on_docs[("HelmRelease", NAME)]
    must_have(hr, (
        f"releaseName: {NAME}",
        "  dependsOn:\n    - name: muster\n",
        f"    fullnameOverride: {NAME}\n",
        "    oauth:\n      baseURL: https://workspaces.ci.example.com\n      dex:\n        allowPrivateURLs: true\n      enabled: true\n      sso:\n        allowPrivateIPs: true\n",
        "    muster:\n      mcpServer:\n        auth:\n          forwardToken: true\n        enabled: true\n",
        "    workspaces:\n      namespace: kagent\n",
        "    - kind: github\n      name: github\n",
        "    - kind: gitlab\n      name: gitlab\n",
        "        url: https://gitlab.example.com:8443\n",
        "    signInStore:\n      encryptionKey:\n        key: sign-in-store\n        name: workspace-manager-keys\n",
        "    grant:\n      signingKey:\n        key: grant\n        name: workspace-manager-keys\n",
        "\n    global:\n",
    ), "the HelmRelease")
    for absent in ("requiredAudiences", "authorizationServer", "\n    sync:", "\n    sessions:", "\n    storage:", "\n    networkPolicy:"):
        if absent in hr:
            fail(f"the HelmRelease carries {absent.strip()!r}")
    refs = 0
    for m in re.finditer(rf"^( +)({'|'.join(CREDENTIALS)}):(.*)\n", hr, re.M):
        indent, key, rest = len(m.group(1)), m.group(2), m.group(3).strip()
        after = hr[m.end():].split("\n")[:2]
        expect = [f"{' ' * (indent + 2)}key: ", f"{' ' * (indent + 2)}name: "]
        if rest or not all(line.startswith(e) for line, e in zip(after, expect)) or len(after) < 2:
            fail(f"{key} in the HelmRelease is not a Secret reference {{key, name}}: {m.group(0)!r} {after}")
        refs += 1
    if refs != 6:
        fail(f"expected the 6 credentials of the fixture as Secret references in the HelmRelease, found {refs}")
    conn_on = on_docs[("HelmRelease", "agent-platform-connectivity")]
    if f"      {NAME}:\n        enabled: true\n" not in conn_on:
        fail(f"the roster forwarded to connectivity does not say {NAME}: enabled: true")
    for name in (NAME, "workspaceManager", "workspaces"):
        if not re.search(rf"^    {re.escape(name)}:", conn_on, re.M):
            fail(f"the {name} block did not reach the connectivity release with workspaces on")
    if block(conn_on, "muster", 4) != block(conn, "muster", 4) or not block(conn, "muster", 4):
        fail("the muster block forwarded to connectivity moved with workspaces on")
    added = set(on_docs) - set(off_docs)
    if added != {("OCIRepository", NAME), ("HelmRelease", NAME)}:
        fail(f"switching workspaces on added documents other than the component's two: {sorted(added)}")
    for key, doc in off_docs.items():
        if key != ("HelmRelease", "agent-platform-connectivity") and on_docs.get(key) != doc:
            fail(f"switching workspaces on changed {key} (muster.* included)")
    if any(kind == "MCPServer" for kind, _ in on_docs):
        fail("the meta chart renders an MCPServer: the registration is the component chart's own")
    ok(f"on: one OCIRepository ({rng.group(1)}) + one HelmRelease dependsOn muster with the block forwarded "
       "(Service name, OAuth, the unpinned muster registration, two provider instances, the keys), "
       f"{refs} credentials all Secret references, the namespace kagent's, empty keys not forwarded; nothing else moved, muster.* included; no MCPServer")

    # --- the storage slices' keys beside the switch ----------------------------------------
    lab = "tests/fixtures/workspaces-lab-values.yaml"
    lab_docs = documents(helm(meta, ["-f", lab]))
    lab_conn = lab_docs[("HelmRelease", "agent-platform-connectivity")]
    must_have(lab_conn, ("      storageClassName: agentlab-workspaces\n", "        name: nfs.csi.k8s.io\n"),
              "the workspaces block forwarded to connectivity with the storage slices' keys")
    if ("HelmRelease", NAME) not in lab_docs:
        fail("the lab's workspaces block (the switch with storage and substrate) rendered no workspace-manager release")
    helm(connectivity, ["--set", "ingress.parentRefs[0].name=x", "--set", f"components.{NAME}.enabled=true", "-f", lab, *IDENTITY], ci_values=False)
    ok("the switch beside workspaces.storage and workspaces.substrate (the lab's block): both schemas take it, the component renders, connectivity takes the forwarded block")

    # --- inline credentials and bad instances -----------------------------------------
    must_fail(meta, [*ON, "--set", f"{NAME}.providers[0].values.app.privateKey=abc"],
              f"{NAME}.providers[0].values.app.privateKey is a credential", "an inline private key")
    must_fail(meta, [*ON, "--set", f"{NAME}.providers[1].values.token=abc"],
              f"{NAME}.providers[1].values.token is a credential", "an inline token")
    must_fail(meta, [*ON, "--set", f"{NAME}.signInStore.encryptionKey=abc"],
              f"{NAME}.signInStore.encryptionKey is a credential", "an inline sign-in store key")
    must_fail(meta, [*ON, "--set", f"{NAME}.providers[1].name=github"], "is a duplicate", "a duplicate provider name")
    must_fail(meta, [*ON, "--set", f"{NAME}.providers[1].name=Git_Lab"], "is not a DNS label", "a non-DNS provider name")
    must_fail(meta, [*ON, "--set", f"{NAME}.providers[1].kind="], "names no kind", "a provider without kind")
    ok("an inline credential, a duplicate or non-DNS name and a missing kind fail naming the path")

    # --- the derived namespace ----------------------------------------------------------
    moved = documents(helm(meta, [*ON, "--set", "kagent.namespaceOverride=agents"]))[("HelmRelease", NAME)]
    must_have(moved, ("    workspaces:\n      namespace: agents\n",), "the HelmRelease with kagent.namespaceOverride")
    helm(meta, [*ON, "--set", f"{NAME}.workspaces.namespace=kagent"])
    must_fail(meta, [*ON, "--set", f"{NAME}.workspaces.namespace=elsewhere"], f"{NAME}.workspaces.namespace (elsewhere) differs",
              "a disagreeing workspaces.namespace")
    ok("workspaces.namespace follows the kagent namespace; an own value must agree")

    # --- the connectivity chart -------------------------------------------------------------
    conn_on_flags = ["--set", "ingress.parentRefs[0].name=x", "--set", f"components.{NAME}.enabled=true", "-f", FIXTURE, *IDENTITY]
    prefix = "agent-platform-connectivity"
    for flavor, kind in (("cilium", "CiliumNetworkPolicy"), ("kubernetes", "NetworkPolicy")):
        flags = [*conn_on_flags, "--set", f"networkPolicy.flavor={flavor}", "--set", "components.kagent.enabled=true"]
        docs = documents(helm(connectivity, flags, ci_values=False))
        names = {n for k, n in docs if k == kind and NAME in n}
        want = {f"{prefix}-{NAME}-ingress", f"{prefix}-{NAME}-egress", f"{prefix}-muster-to-{NAME}", f"{prefix}-kagent-controller-to-{NAME}"}
        if names != want:
            fail(f"{flavor}: the policies for {NAME} are {sorted(names)}, expected {sorted(want)}")
        ingress = docs[(kind, f"{prefix}-{NAME}-ingress")]
        egress = docs[(kind, f"{prefix}-{NAME}-egress")]
        kc = docs[(kind, f"{prefix}-kagent-controller-to-{NAME}")]
        must_have(kc, ("  namespace: kagent\n",), f"{flavor}: the kagent controller's egress")
        if flavor == "cilium":
            must_have(ingress, ("app.kubernetes.io/name: muster\n", "app.kubernetes.io/name: kagent\n",
                                "io.kubernetes.pod.namespace: kagent\n", "- host\n", 'port: "9464"\n'), "cilium ingress")
            must_have(egress, ("- kube-apiserver\n", "matchName: dex.ci.example.com\n",
                               '- matchName: "github.com"\n        - matchName: "api.github.com"\n',
                               '- matchName: "gitlab.example.com"\n', 'port: "8443"\n', "dns:\n"), "cilium egress")
        else:
            must_have(ingress, ("app.kubernetes.io/name: muster\n", "kubernetes.io/metadata.name: kagent\n"), "kubernetes ingress")
            must_have(egress, ("cidr: 0.0.0.0/0\n", "- port: 443\n", "- port: 8443\n", "port: 53\n"), "kubernetes egress")
        if any(k == "MCPServer" for k, _ in docs):
            fail(f"{flavor}: the connectivity chart renders an MCPServer; the registration is the component chart's own")
        no_kagent = documents(helm(connectivity, [*conn_on_flags, "--set", f"networkPolicy.flavor={flavor}"], ci_values=False))
        if (kind, f"{prefix}-kagent-controller-to-{NAME}") in no_kagent or "app.kubernetes.io/name: kagent\n" in no_kagent[(kind, f"{prefix}-{NAME}-ingress")]:
            fail(f"{flavor}: a kagent rule renders with kagent off")
    narrowed = documents(helm(connectivity, [*conn_on_flags, "--set", "networkPolicy.flavor=kubernetes",
                                             "--set", "workspaceManager.networkPolicy.egress.cidrs[0]=198.51.100.0/24"], ci_values=False))
    narrowed_egress = narrowed[("NetworkPolicy", f"{prefix}-{NAME}-egress")]
    if "except:" in narrowed_egress or 'cidr: "198.51.100.0/24"\n      ports:\n        - port: 443\n          protocol: TCP\n        - port: 8443\n' not in narrowed_egress:
        fail("kubernetes: workspaceManager.networkPolicy.egress.cidrs does not narrow the providers' egress")
    ok("connectivity: ingress (muster, kagent's controller, probes, metrics), egress (DNS, kube-apiserver, the issuer, "
       "every provider instance's hosts on their ports), muster-to and kagent-controller-to policies in both flavors; none of kagent's without it; cidrs narrow")

    must_fail(connectivity, [*conn_on_flags, "--set", "components.muster.enabled=false"], "components.muster.enabled is false",
              "muster off with the MCPServer on", ci_values=False)
    must_fail(connectivity, [*conn_on_flags, "--set", f"{NAME}.providers[1].values.url="], "names neither values.url nor values.apiURL",
              "a provider of another kind without url", ci_values=False)
    must_fail(connectivity, [*conn_on_flags, "--set", "workspaceManager.networkPolicy.egress.cidrs[0]=nope"], "is not an IPv4 CIDR",
              "a bad CIDR", ci_values=False)
    ok("connectivity guards: muster off with the MCPServer on, a provider without url, a bad CIDR")

    # --- the BOM ----------------------------------------------------------------------------
    with open(f"{meta}/examples/customer-bom.yaml", encoding="utf-8") as f:
        bom = f.read()
    m = re.search(rf'^\s*{re.escape(NAME)}:\s*\{{\s*versionRange:\s*"([0-9][0-9A-Za-z.-]*)"\s*\}}', bom, re.M)
    if not m:
        fail(f"examples/customer-bom.yaml pins no exact {NAME} version")
    pinned = documents(helm(meta, [*ON, "-f", f"{meta}/examples/customer-bom.yaml"]))[("OCIRepository", NAME)]
    if f'semver: "{m.group(1)}"' not in pinned:
        fail(f"the BOM pin {m.group(1)} did not reach the OCIRepository")
    ok(f"the BOM pins {m.group(1)} and the pin reaches the OCIRepository")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2]))
