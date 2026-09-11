#!/usr/bin/env python3
"""Assert the two shapes of the meta chart around its bundled Flux engine.

`components.flux.enabled` is the condition of the flux-engine subchart
(charts/flux-engine): the Flux Operator, one FluxInstance, the tenant identity
`agent-platform-flux`, eleven CRDs in crds/, and — in the meta chart itself —
the pre-delete hooks of the ordered teardown and the default tenant
ServiceAccount on every platform HelmRelease. Each case below pins one property
the fleet, the kind quick start or the sibling slices rely on:

- engine OFF (the fleet's value): `helm template --include-crds` renders no CRD,
  no hook, no operator, no FluxInstance, no identity — the pure app-of-apps
  render, every platform HelmRelease without serviceAccountName unless
  gitops.serviceAccountName is set, the roster says `flux: enabled: false`;
- engine ON (the default): exactly the eleven CRDs, the operator objects, the
  FluxInstance (two controllers, multitenant), the identity, the hook
  ServiceAccount at weight -10, the two teardown Jobs at weights 0 and 5 — the
  first a script in the helm image that deletes the platform HelmReleases in
  waves of reverse dependency order (every rendered dependsOn edge points from
  an earlier wave to a later one, so a CRD chart's release goes after its CR
  consumers), the second one kubectl command deleting the FluxInstance — the
  self-management hooks at -6/-5 with their identity <release>-self (they
  render whenever the engine is on), the kagent namespace hook at -8 (ci-values
  turn kagent on) — and every platform HelmRelease naming agent-platform-flux.
  The engine-on renders here set gitops.self.enabled=false so the assertions
  stay about the engine; the self OCIRepository/HelmRelease, the values hook
  and the admission policy are tests/verify-self.py's;
- the kagent namespace hook (giantswarm/agent-platform#306): with the engine on
  and kagent on, one pre-install,pre-upgrade Job <release>-kagent-namespace at
  weight -8 in the helm image, as the hook ServiceAccount — whose own hook
  events then include pre-install,pre-upgrade — that creates the namespace the
  kagent component installs into (kagent.namespaceOverride) when it is missing,
  waits out one that is terminating, and leaves an existing one alone; the
  Namespace itself is never a rendered object of this chart (the connectivity
  release tracks it). Not rendered with kagent off, with the namespace equal
  to the HelmReleases' target namespace, with kagent.namespaceOverride empty —
  the ServiceAccount is then back to pre-delete only — nor, with kagent on,
  with the engine off (the pure render);
- the engine changes nothing else: the OCIRepository/HelmRelease documents of
  both shapes are identical but for the serviceAccountName line and the roster;
- Chart.yaml's only dependency is flux-engine, conditional on
  components.flux.enabled, a directory in charts/ (no repository), not a
  component pin; the subchart's version matches the entry and its appVersion
  the operator image tag;
- the guards that can be asserted offline: gitops.namespace set with the engine
  on fails; the operator API served offline (lookup empty) renders. The foreign
  Flux guard needs a live `lookup` and is asserted on a cluster (README).

Deliberately stdlib-only: the CI image has no PyYAML. HELM selects the binary
(the CI job runs Helm 3.17.3 as `helm`).
"""

import os
import re
import subprocess
import sys

HELM = os.environ.get("HELM", "helm")
RELEASE = "agent-platform"
NAMESPACE = "agent-platform"
TENANT_SA = "agent-platform-flux"
OFF = ["--set", "components.flux.enabled=false"]
SELF_OFF = ["--set", "gitops.self.enabled=false"]
CRDS = {
    "fluxinstances.fluxcd.controlplane.io",
    "fluxreports.fluxcd.controlplane.io",
    "resourcesetinputproviders.fluxcd.controlplane.io",
    "resourcesets.fluxcd.controlplane.io",
    "buckets.source.toolkit.fluxcd.io",
    "externalartifacts.source.toolkit.fluxcd.io",
    "gitrepositories.source.toolkit.fluxcd.io",
    "helmcharts.source.toolkit.fluxcd.io",
    "helmrepositories.source.toolkit.fluxcd.io",
    "ocirepositories.source.toolkit.fluxcd.io",
    "helmreleases.helm.toolkit.fluxcd.io",
}
FLEET_APIS = [
    "--api-versions", "kyverno.io/v1", "--api-versions", "cilium.io/v2",
    "--api-versions", "monitoring.coreos.com/v1", "--api-versions", "gateway.networking.k8s.io/v1",
    "--api-versions", "gateway.envoyproxy.io/v1alpha1",
]


def fail(msg: str) -> None:
    sys.exit(f"FAIL: {msg}")


def helm(chart: str, flags: list[str], expect_fail: str | None = None) -> str:
    cmd = [HELM, "template", RELEASE, chart, "-n", NAMESPACE, *flags]
    r = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if expect_fail is not None:
        if r.returncode == 0:
            fail(f"render succeeded but should have failed: {' '.join(flags)}")
        if expect_fail not in r.stderr:
            fail(f"render failed for the wrong reason ({' '.join(flags)}):\n{r.stderr}")
        return r.stderr
    if r.returncode != 0:
        fail(f"render failed: {' '.join(cmd)}\n{r.stderr}")
    return r.stdout


def docs(manifest: str) -> list[tuple[str, str, str, str]]:
    """(kind, namespace, name, document) for every rendered object."""
    out = []
    for d in manifest.split("\n---\n"):
        kind = re.search(r"^kind: (\S+)$", d, re.M)
        if not kind:
            continue
        meta = d[d.index("\nmetadata:\n"):] if "\nmetadata:\n" in d else d
        name = re.search(r"^  name: (\S+)$", meta, re.M)
        ns = re.search(r"^  namespace: (\S+)$", meta, re.M)
        out.append((kind.group(1), ns.group(1) if ns else "", name.group(1) if name else "", d))
    return out


def kinds(ds) -> dict[str, int]:
    out: dict[str, int] = {}
    for k, _, _, _ in ds:
        out[k] = out.get(k, 0) + 1
    return out


def one(ds, kind: str, name: str) -> str:
    hits = [d for k, _, n, d in ds if k == kind and n == name]
    if len(hits) != 1:
        fail(f"expected exactly one {kind} {name}, found {len(hits)}")
    return hits[0]


def hr_values(doc: str) -> str:
    body = doc[doc.index("\n  values:\n") + len("\n  values:\n"):]
    return "\n".join(line[4:] if line.startswith("    ") else line for line in body.splitlines())


def roster_flux(conn_values: str) -> str | None:
    m = re.search(r"^components:\n((?:  .*\n)+)", conn_values + "\n", re.M)
    if not m:
        fail("the connectivity release carries no components roster")
    f = re.search(r"^  flux:\n    enabled: (true|false)$", m.group(1), re.M)
    return f.group(1) if f else None


def hook_meta(doc: str) -> tuple[str, str, str]:
    hook = re.search(r"^    helm.sh/hook: (\S+)$", doc, re.M)
    weight = re.search(r'^    helm.sh/hook-weight: "(-?\d+)"$', doc, re.M)
    policy = re.search(r"^    helm.sh/hook-delete-policy: (\S+)$", doc, re.M)
    return (hook.group(1) if hook else "", weight.group(1) if weight else "", policy.group(1) if policy else "")


def job_args(doc: str) -> list[str]:
    m = re.search(r"^          args:\n((?:            - .*\n)+)", doc + "\n", re.M)
    if not m:
        fail("hook Job without args")
    return [a.strip('"') for a in re.findall(r"^            - (.*)$", m.group(1), re.M)]


def platform_docs(ds):
    """The component OCIRepository/HelmRelease documents, normalised across the two shapes."""
    out = {}
    for k, ns, n, d in ds:
        if k in ("OCIRepository", "HelmRelease"):
            d = re.sub(r"\A(---\n)+", "", d).rstrip("\n")  # separators and the final newline are splitting artifacts
            d = re.sub(r"^  serviceAccountName: \S+\n", "", d, flags=re.M)
            d = re.sub(r"^      flux:\n        enabled: (true|false)\n", "", d, flags=re.M)
            out[(k, ns, n)] = d
    return out


def main(chart: str) -> int:
    ci = ["-f", f"{chart}/ci/ci-values.yaml"]
    sub = f"{chart}/charts/flux-engine"

    # --- Chart.yaml: the one dependency, conditional, a local directory, not a component
    chart_yaml = open(f"{chart}/Chart.yaml").read()
    deps = re.search(r"^dependencies:\n((?:  .*\n)+)", chart_yaml, re.M)
    if not deps:
        fail("Chart.yaml has no dependencies block; the flux-engine subchart must be declared")
    entries = re.findall(r"^  - name: (\S+)$", deps.group(1), re.M)
    if entries != ["flux-engine"]:
        fail(f"Chart.yaml dependencies must be exactly [flux-engine], got {entries} (components are never pinned here)")
    if "condition: components.flux.enabled" not in deps.group(1):
        fail("the flux-engine dependency is not conditional on components.flux.enabled")
    if re.search(r"repository: \S", deps.group(1)):
        fail("the flux-engine dependency names a repository; it is a directory in charts/")
    dep_version = re.search(r"^    version: (\S+)$", deps.group(1), re.M).group(1)
    sub_chart = open(f"{sub}/Chart.yaml").read()
    sub_version = re.search(r"^version: (\S+)$", sub_chart, re.M).group(1)
    if sub_version != dep_version:
        fail(f"charts/flux-engine/Chart.yaml version {sub_version} != dependency version {dep_version}")
    sub_app = re.search(r'^appVersion: "?([^"\n]+)"?$', sub_chart, re.M).group(1)
    sub_values = open(f"{sub}/values.yaml").read()
    op_tag = re.search(r"^    tag: (\S+)$", sub_values, re.M).group(1)
    if sub_app != op_tag:
        fail(f"charts/flux-engine appVersion {sub_app} != operator.image.tag {op_tag}")
    values = open(f"{chart}/values.yaml").read()
    flux_entry = re.search(r"^  flux:  # @schema additionalProperties: true\n((?:    .*\n)+)", values, re.M)
    if not flux_entry or "    enabled: true\n" not in flux_entry.group(1) or re.search(r"^    chart:", flux_entry.group(1), re.M):
        fail("components.flux must be a feature switch: enabled: true by default and no chart key")
    omit = re.search(r"^    omitKeys:\n((?:      .*\n)+)", values[values.index("  agent-platform-connectivity:"):], re.M)
    if not omit or "      - flux-engine\n" not in omit.group(1):
        fail("components.agent-platform-connectivity.omitKeys must hold back the flux-engine block (Helm coalesces the subchart values into it)")
    print(f"ok: Chart.yaml's only dependency is flux-engine {dep_version} (condition components.flux.enabled, local directory); appVersion = operator tag {op_tag}")

    # --- engine OFF: the pure app-of-apps render
    off = docs(helm(chart, [*ci, *OFF, "--include-crds"]))
    extra = set(kinds(off)) - {"OCIRepository", "HelmRelease"}
    if extra:
        fail(f"engine off still renders {sorted(extra)}")
    off_text = "\n---\n".join(d for *_, d in off)
    for needle in ("helm.sh/hook", TENANT_SA, "FluxInstance", "flux-operator", "CustomResourceDefinition"):
        if needle in off_text:
            fail(f"engine off render mentions {needle!r}")
    hrs_off = [d for k, _, _, d in off if k == "HelmRelease"]
    if any("serviceAccountName:" in d for d in hrs_off):
        fail("engine off: a platform HelmRelease names a serviceAccountName without gitops.serviceAccountName")
    if roster_flux(hr_values(one(off, "HelmRelease", "agent-platform-connectivity"))) != "false":
        fail("engine off: the roster forwarded to connectivity does not say flux: enabled: false")
    off_sa = docs(helm(chart, [*ci, *OFF, "--set", "gitops.serviceAccountName=custom-sa"]))
    if not all("\n  serviceAccountName: custom-sa\n" in d for k, _, _, d in off_sa if k == "HelmRelease"):
        fail("engine off: gitops.serviceAccountName is not stamped on every HelmRelease")
    fleet = docs(helm(chart, [*ci, *OFF, "--set", "gitops.namespace=flux-giantswarm", "--set", "gitops.targetNamespace=agent-platform", *FLEET_APIS]))
    if set(kinds(fleet)) - {"OCIRepository", "HelmRelease"} or any(ns != "flux-giantswarm" for _, ns, _, _ in fleet):
        fail("fleet shape (engine off, exempt namespace): a non-Flux object or a wrong namespace rendered")
    print(f"ok: engine off — {len(off)} Flux objects, no CRD/hook/operator/FluxInstance/identity, no serviceAccountName, roster flux: false, fleet shape clean")

    # --- engine ON: exactly the engine besides the platform objects
    on = docs(helm(chart, [*ci, *SELF_OFF, "--include-crds"]))
    crds = {n for k, _, n, _ in on if k == "CustomResourceDefinition"}
    if crds != CRDS:
        fail(f"engine on: CRDs differ from the eleven expected: missing {sorted(CRDS - crds)}, extra {sorted(crds - CRDS)}")
    expected = {
        ("Deployment", NAMESPACE, "flux-operator"), ("ServiceAccount", NAMESPACE, "flux-operator"),
        ("Service", NAMESPACE, "flux-operator"), ("ClusterRoleBinding", "", "agent-platform-flux-operator"),
        ("FluxInstance", NAMESPACE, "flux"),
        ("ServiceAccount", NAMESPACE, TENANT_SA), ("ClusterRoleBinding", "", TENANT_SA),
        ("ServiceAccount", NAMESPACE, f"{RELEASE}-hooks"), ("ClusterRoleBinding", "", f"{RELEASE}-hooks"),
        ("Job", NAMESPACE, f"{RELEASE}-teardown-releases"), ("Job", NAMESPACE, f"{RELEASE}-teardown-engine"),
        ("ServiceAccount", NAMESPACE, f"{RELEASE}-self"), ("Role", NAMESPACE, f"{RELEASE}-self"), ("RoleBinding", NAMESPACE, f"{RELEASE}-self"),
        ("Job", NAMESPACE, f"{RELEASE}-self-stop-resumer"), ("Job", NAMESPACE, f"{RELEASE}-self-suspend"),
        ("Job", NAMESPACE, f"{RELEASE}-kagent-namespace"),  # ci-values turn kagent on
    }
    engine = {(k, ns, n) for k, ns, n, _ in on if k not in ("OCIRepository", "HelmRelease", "CustomResourceDefinition")}
    if engine != expected:
        fail(f"engine on: engine objects differ: missing {sorted(expected - engine)}, extra {sorted(engine - expected)}")
    dep = one(on, "Deployment", "flux-operator")
    if "app.kubernetes.io/component: helm-controller" in dep:
        fail("the operator Deployment carries the helm-controller component label the render guard looks for")
    if f'image: "ghcr.io/controlplaneio-fluxcd/flux-operator:{op_tag}"' not in dep:
        fail(f"the operator Deployment does not run the pinned image tag {op_tag}")
    if 'value: "0"' not in dep or "WEB_SERVER_PORT" not in dep:
        fail("the operator's web server is not disabled (WEB_SERVER_PORT=0)")
    fi = one(on, "FluxInstance", "flux")
    comps = re.search(r"^  components:\n((?:    - .*\n)+)", fi + "\n", re.M)
    if not comps or re.findall(r"- (\S+)", comps.group(1)) != ["source-controller", "helm-controller"]:
        fail("FluxInstance components are not exactly [source-controller, helm-controller]")
    for needle in ('version: "2.x"', 'registry: "ghcr.io/fluxcd"', "multitenant: true", "tenantDefaultServiceAccount: default", "networkPolicy: false"):
        if needle not in fi:
            fail(f"FluxInstance lacks {needle!r}")
    if "artifact:" in fi:
        fail("FluxInstance sets distribution.artifact; the manifests come embedded in the operator image")
    for crb, sa in ((TENANT_SA, TENANT_SA), ("agent-platform-flux-operator", "flux-operator"), (f"{RELEASE}-hooks", f"{RELEASE}-hooks")):
        d = one(on, "ClusterRoleBinding", crb)
        if "name: cluster-admin" not in d or f"    name: {sa}\n    namespace: {NAMESPACE}" not in d:
            fail(f"ClusterRoleBinding {crb} does not bind ServiceAccount {sa} in {NAMESPACE} to cluster-admin")
    hrs_on = [d for k, _, _, d in on if k == "HelmRelease"]
    if not hrs_on or not all(f"\n  serviceAccountName: {TENANT_SA}\n" in d for d in hrs_on):
        fail(f"engine on: a platform HelmRelease does not name {TENANT_SA}")
    conn_values = hr_values(one(on, "HelmRelease", "agent-platform-connectivity"))
    if roster_flux(conn_values) != "true":
        fail("engine on: the roster forwarded to connectivity does not say flux: enabled: true")
    if re.search(r"^flux-engine:", conn_values, re.M):
        fail("the flux-engine values block reached the connectivity release (omitKeys)")
    on_sa = docs(helm(chart, [*ci, *SELF_OFF, "--set", "gitops.serviceAccountName=custom-sa"]))
    if not all("\n  serviceAccountName: custom-sa\n" in d for k, _, _, d in on_sa if k == "HelmRelease"):
        fail("engine on: gitops.serviceAccountName does not override the tenant default")
    print(f"ok: engine on — the eleven CRDs, operator, FluxInstance (2 controllers, multitenant), identities, hooks; {len(hrs_on)} HelmReleases name {TENANT_SA}")

    # --- the hooks: weights, policy, one plain command each, restricted pods
    hooks = {(k, n): d for k, _, n, d in on if "helm.sh/hook:" in d}
    events = {}
    for (k, n), d in hooks.items():
        hook, weight, policy = hook_meta(d)
        if policy != "before-hook-creation,hook-succeeded":
            fail(f"hook {k} {n}: policy {policy!r}")
        events[(k, n)] = (hook, int(weight))
    # the hook identity is created for pre-install,pre-upgrade too while the kagent namespace hook renders (ci-values turn kagent on)
    expected_events = {
        ("ServiceAccount", f"{RELEASE}-hooks"): ("pre-install,pre-upgrade,pre-delete", -10), ("ClusterRoleBinding", f"{RELEASE}-hooks"): ("pre-install,pre-upgrade,pre-delete", -10),
        ("Job", f"{RELEASE}-kagent-namespace"): ("pre-install,pre-upgrade", -8),
        # the self-management hooks (verify-self.py): pre-upgrade too while self-management is off (the hand-back)
        ("Job", f"{RELEASE}-self-stop-resumer"): ("pre-upgrade,pre-delete", -6), ("Job", f"{RELEASE}-self-suspend"): ("pre-upgrade,pre-delete", -5),
        ("Job", f"{RELEASE}-teardown-releases"): ("pre-delete", 0), ("Job", f"{RELEASE}-teardown-engine"): ("pre-delete", 5),
    }
    if events != expected_events:
        fail(f"hook events/weights differ:\n  got      {events}\n  expected {expected_events}")
    hr_names = sorted(n for k, _, n, _ in on if k == "HelmRelease")
    # teardown-releases: a script, one `kubectl delete --wait` per wave; the waves
    # are the reverse of the rendered dependsOn graph — a release is deleted only
    # after every release that dependsOn it (a CRD chart after its CR consumers:
    # Helm cannot delete an object whose kind is gone, and helm-controller would
    # retry that uninstall until the hook times out).
    rel_job = one(on, "Job", f"{RELEASE}-teardown-releases")
    waves = [line.split()[8:] for line in rel_job.splitlines()
             if line.strip().startswith(f"kubectl delete helmreleases.helm.toolkit.fluxcd.io --namespace {NAMESPACE} --ignore-not-found --wait --timeout=5m ")]
    if not waves or sorted(n for w in waves for n in w) != hr_names or any(not w for w in waves):
        fail(f"teardown-releases Job does not delete exactly the rendered HelmReleases by name, in waves: {waves} vs {hr_names}")
    wave_of = {n: i for i, w in enumerate(waves) for n in w}
    depends_on = {n: re.findall(r"^  dependsOn:\n((?:    - name: .*\n)+)", d + "\n", re.M) for k, _, n, d in on if k == "HelmRelease"}
    edges = [(n, dep) for n, blocks in depends_on.items() for block in blocks for dep in re.findall(r"^    - name: (\S+)$", block, re.M)]
    if not edges:
        fail("no dependsOn edge among the rendered HelmReleases; the wave assertion has nothing to check")
    for dependent, dependency in edges:
        if dependency not in wave_of:
            fail(f"HelmRelease {dependent} dependsOn {dependency}, which the teardown does not delete")
        if wave_of[dependent] >= wave_of[dependency]:
            fail(f"teardown order: {dependent} (wave {wave_of[dependent] + 1}) dependsOn {dependency} (wave {wave_of[dependency] + 1}); the dependent must go first")
    eng = job_args(one(on, "Job", f"{RELEASE}-teardown-engine"))
    if eng != ["delete", "fluxinstances.fluxcd.controlplane.io", "--namespace", NAMESPACE, "flux", "--ignore-not-found", "--wait", "--timeout=5m"]:
        fail(f"teardown-engine Job does not delete the FluxInstance flux and wait: {eng}")
    hooks_image = re.search(r"^  hooks:\n    image:\n      registry: (\S+)\n      repository: (\S+)\n      tag: (\S+)$", values, re.M)
    if not hooks_image:
        fail("gitops.hooks.image is not a registry/repository/tag block (the Renovate regex needs the three lines)")
    image = "/".join(hooks_image.group(1, 2)) + ":" + hooks_image.group(3)
    helm_image = re.search(r"^    helmImage:\n      registry: (\S+)\n      repository: (\S+)\n      tag: (\S+)$", values, re.M)
    if not helm_image:
        fail("gitops.hooks.helmImage is not a registry/repository/tag block")
    helm_ref = "/".join(helm_image.group(1, 2)) + ":" + helm_image.group(3)
    for job, ref, scripted in ((f"{RELEASE}-teardown-releases", helm_ref, True), (f"{RELEASE}-teardown-engine", image, False)):
        d = one(on, "Job", job)
        for needle in (f'image: "{ref}"', "restartPolicy: Never", "runAsNonRoot: true", "readOnlyRootFilesystem: true", "allowPrivilegeEscalation: false", "- ALL", "type: RuntimeDefault", f"serviceAccountName: {RELEASE}-hooks"):
            if needle not in d:
                fail(f"hook Job {job} lacks {needle!r}")
        if scripted != ('command: ["/bin/sh", "-eu", "-c"]' in d):
            fail(f"hook Job {job}: {'a script in the helm image' if scripted else 'one kubectl command, no entrypoint override'} expected")
    print(f"ok: hooks — SA/CRB at -10, kagent namespace hook at -8, self hooks at -6/-5, teardown-releases at 0 (deletes {len(hr_names)} HelmReleases in {len(waves)} waves, reverse of {len(edges)} dependsOn edges: {' > '.join(','.join(w) for w in waves)}), teardown-engine at 5, restricted pods running {image} / {helm_ref}")

    # --- the kagent namespace hook (giantswarm/agent-platform#306)
    ns_job = one(on, "Job", f"{RELEASE}-kagent-namespace")
    for needle in (f'image: "{helm_ref}"', 'command: ["/bin/sh", "-eu", "-c"]', f"serviceAccountName: {RELEASE}-hooks", 'ns="kagent"',
                   'kubectl create namespace "$ns"', 'kubectl wait --for=delete "namespace/$ns"', "Terminating)", "left as it is",
                   "restartPolicy: Never", "runAsNonRoot: true", "readOnlyRootFilesystem: true", "allowPrivilegeEscalation: false", "- ALL", "type: RuntimeDefault"):
        if needle not in ns_job:
            fail(f"the kagent namespace hook lacks {needle!r}")
    if ("Namespace", "", "kagent") in {(k, ns, n) for k, ns, n, _ in on} or re.search(r"^kind: Namespace$", "\n---\n".join(d for *_, d in on), re.M):
        fail("the meta chart renders a Namespace object; the kagent namespace is the connectivity release's and must only be created by the hook")
    cases = {
        "kagent off": ["--set", "components.kagent.enabled=false"],
        "the namespace is the HelmReleases' target": ["--set", "gitops.targetNamespace=kagent"],
        "kagent.namespaceOverride empty": ["--set", "kagent.namespaceOverride="],
    }
    for label, flags in cases.items():
        ds = docs(helm(chart, [*ci, *SELF_OFF, *flags]))
        if any(n == f"{RELEASE}-kagent-namespace" for _, _, n, _ in ds):
            fail(f"{label}: the kagent namespace hook still renders")
        for kind in ("ServiceAccount", "ClusterRoleBinding"):
            if hook_meta(one(ds, kind, f"{RELEASE}-hooks"))[0] != "pre-delete":
                fail(f"{label}: the hook {kind} is not back to pre-delete only")
    kag_off = helm(chart, [*ci, *OFF, "--set", "components.kagent.enabled=true"])
    if "kagent-namespace" in kag_off or "helm.sh/hook" in kag_off:
        fail("engine off with kagent on: the kagent namespace hook rendered (the pure render must not carry it; a cluster's own Flux gets the namespace out of band)")
    print(f"ok: kagent namespace hook — pre-install,pre-upgrade at -8 as {RELEASE}-hooks in {helm_ref}, create-if-missing / wait out Terminating / leave Active; no Namespace object; absent with kagent off, target namespace = kagent, namespaceOverride empty, engine off")

    # --- the engine changes nothing else about the platform objects
    p_off, p_on = platform_docs(off), platform_docs(on)
    if p_off != p_on:
        diff = sorted(set(p_off) ^ set(p_on)) or [k for k in p_off if p_off[k] != p_on.get(k)]
        fail(f"the engine changes platform objects beyond serviceAccountName and the roster: {diff}")
    print("ok: OCIRepository/HelmRelease documents identical across the shapes but for serviceAccountName and the roster")

    # --- offline guards and the default
    helm(chart, [*ci, "--set", "gitops.namespace=flux-giantswarm"], expect_fail="cannot be combined with the bundled Flux engine")
    helm(chart, [*ci, "--api-versions", "fluxcd.controlplane.io/v1"])
    default = docs(helm(chart, ["--set", "ingress.parentRefs[0].name=x", "--include-crds"]))
    if ("FluxInstance", NAMESPACE, "flux") not in {(k, ns, n) for k, ns, n, _ in default}:
        fail("the default render (no values file) does not carry the engine; components.flux.enabled must default to true")
    print("ok: gitops.namespace with the engine on is refused; the operator API served offline renders; the default render carries the engine")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
