#!/usr/bin/env python3
"""Assert the shapes of the meta chart's self-management (README "Self-management").

`gitops.self.enabled` (default `auto`, follows `components.flux.enabled`) makes
the release render its own OCIRepository + HelmRelease so the bundled
helm-controller adopts it, the hooks that bracket the adoption, and the
ValidatingAdmissionPolicy that makes the Helm CLI day-0 only. Each case below
pins one property the fleet, the quick start, the lab shape or the hand-back
rely on:

- engine OFF (the fleet's value): nothing of it — no self objects, no hook, no
  policy, no values-Secret reference, no identity; the fleet shape is unchanged;
- engine ON, self default: exactly the self OCIRepository (the chart's own OCI
  source, the derived range `>=<version> <next major>.0.0`) and HelmRelease
  (rendered SUSPENDED, the release's name, the tenant identity, values from
  Secret agent-platform-values with optional: false, disableWait), the identity
  <release>-self with a namespaced Role, the values hook at post-install /
  post-upgrade, the -6 stop-resumer and -5 suspend hooks at pre-delete only,
  the policy + binding (CREATE of this release's Helm storage Secrets, the
  tenant identity or the hand-back annotation, the message naming the Secret
  and the escape hatch) — and nothing else changes against the self-off render;
- engine ON, self OFF (the lab shape and the hand-back render): none of the
  self objects, no values hook, no policy — but the -6/-5 hooks at pre-upgrade
  AND pre-delete (the hand-back must suspend the self HelmRelease before Helm
  deletes it) and their identity;
- the guards that can be asserted offline: `true` with the engine off is
  refused; a Kubernetes below 1.30 is refused while self-management is on and
  accepted with it off; a value outside auto/true/false fails the schema;
- the knobs: repository (with or without a trailing slash), insecure,
  versionRange, semverFilter (the dev-channel tag filter, rendered iff set),
  interval, gitops.serviceAccountName (the policy admits the
  overriding identity); `lookup` is empty offline, so the HelmRelease stays
  suspended even when the HelmRelease API is passed in;
- the Renovate-tracked image block of the helm hooks and the schema comment of
  the tri-state knob in values.yaml.

The install-time bracket itself (suspend → resumer → adoption), the refusal in
Helm's own output and the hand-back run on a cluster (the PR's proof and the
README). Deliberately stdlib-only: the CI image has no PyYAML. HELM selects the
binary (the CI job runs Helm 3.17.3 as `helm`).
"""

import json
import os
import re
import subprocess
import sys

HELM = os.environ.get("HELM", "helm")
RELEASE = "agent-platform"
NAMESPACE = "agent-platform"
TENANT_SA = "agent-platform-flux"
SELF_SA = f"{RELEASE}-self"
POLICY = f"{RELEASE}-self-managed-{NAMESPACE}"
VALUES_SECRET = "agent-platform-values"
ANNOTATION = "agent-platform.giantswarm.io/helm-cli"
ENGINE_OFF = ["--set", "components.flux.enabled=false"]
SELF_OFF = ["--set", "gitops.self.enabled=false"]
SELF_ON = ["--set", "gitops.self.enabled=true"]
FLEET_APIS = [
    "--api-versions", "kyverno.io/v1", "--api-versions", "cilium.io/v2",
    "--api-versions", "monitoring.coreos.com/v1", "--api-versions", "gateway.networking.k8s.io/v1",
    "--api-versions", "gateway.envoyproxy.io/v1alpha1",
]
SELF_MARKERS = ("ValidatingAdmissionPolicy", VALUES_SECRET, SELF_SA, ANNOTATION, "self-management")
# The kagent CRDs' storage-version hooks (giantswarm/agent-platform#396, verify-engine.py)
# render with the engine OFF too, as the hook identity: the one hook family the fleet shape carries.
STORAGE_HOOKS = {f"{RELEASE}-kagent-storage-version-backup": ("pre-install,pre-upgrade", -7), f"{RELEASE}-kagent-storage-version-restore": ("post-install,post-upgrade", 0)}
STORAGE_FAMILY = {("Job", NAMESPACE, n) for n in STORAGE_HOOKS} | {("ServiceAccount", NAMESPACE, f"{RELEASE}-hooks"), ("ClusterRoleBinding", "", f"{RELEASE}-hooks")}


def fail(msg: str) -> None:
    sys.exit(f"FAIL: {msg}")


def helm(chart: str, flags: list[str], expect_fail: str | tuple[str, ...] | None = None) -> str:
    """Render; with expect_fail, the render must fail and stderr must carry one
    of the fragments (Helm 3 and 4 word a schema violation differently)."""
    cmd = [HELM, "template", RELEASE, chart, "-n", NAMESPACE, *flags]
    r = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if expect_fail is not None:
        if r.returncode == 0:
            fail(f"render succeeded but should have failed: {' '.join(flags)}")
        fragments = (expect_fail,) if isinstance(expect_fail, str) else expect_fail
        if not any(f in r.stderr for f in fragments):
            fail(f"render failed for the wrong reason ({' '.join(flags)}):\n{r.stderr}")
        return r.stderr
    if r.returncode != 0:
        fail(f"render failed: {' '.join(cmd)}\n{r.stderr}")
    return r.stdout


def docs(manifest: str) -> dict[tuple[str, str, str], str]:
    """(kind, namespace, name) -> document for every rendered object."""
    out = {}
    for d in manifest.split("\n---\n"):
        kind = re.search(r"^kind: (\S+)$", d, re.M)
        if not kind:
            continue
        meta = d[d.index("\nmetadata:\n"):] if "\nmetadata:\n" in d else d
        name = re.search(r"^  name: (\S+)$", meta, re.M)
        ns = re.search(r"^  namespace: (\S+)$", meta, re.M)
        d = re.sub(r"\A(---\n)+", "", d).rstrip("\n")
        out[(kind.group(1), ns.group(1) if ns else "", name.group(1) if name else "")] = d
    return out


def hook_meta(doc: str) -> tuple[str, int]:
    hook = re.search(r"^    helm.sh/hook: (\S+)$", doc, re.M)
    weight = re.search(r'^    helm.sh/hook-weight: "(-?\d+)"$', doc, re.M)
    return (hook.group(1) if hook else "", int(weight.group(1)) if weight else 0)


def job_args(doc: str) -> list[str]:
    m = re.search(r"^          args:\n((?:            - .*\n)+)", doc + "\n", re.M)
    if not m:
        fail("hook Job without args")
    return [a.strip('"') for a in re.findall(r"^            - (.*)$", m.group(1), re.M)]


def needles(doc: str, what: str, *needles: str) -> None:
    for n in needles:
        if n not in doc:
            fail(f"{what} lacks {n!r}")


def self_objects(ds) -> dict:
    return {k: v for k, v in ds.items() if k[2] in (RELEASE, POLICY, f"{RELEASE}-self-values") and k[0] in (
        "OCIRepository", "HelmRelease", "ValidatingAdmissionPolicy", "ValidatingAdmissionPolicyBinding", "Job")}


def main(chart: str) -> int:
    ci = ["-f", f"{chart}/ci/ci-values.yaml"]
    values = open(f"{chart}/values.yaml").read()
    chart_version = re.search(r"^version: (\S+)$", open(f"{chart}/Chart.yaml").read(), re.M).group(1)
    major, minor, patch = (int(x) for x in re.match(r"(\d+)\.(\d+)\.(\d+)", chart_version).groups())
    derived = f">={major}.{minor}.{patch} <{major + 1}.0.0"
    kubectl_image = re.search(r"^  hooks:\n    image:\n      registry: (\S+)\n      repository: (\S+)\n      tag: (\S+)$", values, re.M)
    helm_image = re.search(r"^    helmImage:\n      registry: (\S+)\n      repository: (\S+)\n      tag: (\S+)$", values, re.M)
    if not kubectl_image or not helm_image:
        fail("gitops.hooks.image / gitops.hooks.helmImage are not registry/repository/tag blocks (the Renovate regex needs the three lines)")
    kubectl_ref = "/".join(kubectl_image.group(1, 2)) + ":" + kubectl_image.group(3)
    helm_ref = "/".join(helm_image.group(1, 2)) + ":" + helm_image.group(3)
    if "  self:\n" not in values or "    enabled: auto  # @schema type: [boolean, string]; enum: [auto, true, false]\n" not in values[values.index("  self:\n"):]:
        fail("gitops.self.enabled is not the tri-state knob `auto  # @schema type: [boolean, string]; enum: [auto, true, false]`")
    self_block = values[values.index("  self:\n"):]
    m = re.search(r'^    semverFilter: (".*")$', self_block, re.M)
    if not m:
        fail("gitops.self.semverFilter is not a double-quoted string in values.yaml")
    self_filter = json.loads(m.group(1))  # YAML double-quoted == JSON for these strings
    print(f"ok: values — gitops.self tri-state knob, hook images {kubectl_ref} and {helm_ref} as Renovate blocks; derived range {derived}; self semverFilter {self_filter!r}")

    # --- engine OFF: nothing of self-management, the fleet shape unchanged
    for flags in ([*ci, *ENGINE_OFF, "--include-crds"],
                  [*ci, *ENGINE_OFF, "--set", "gitops.namespace=flux-giantswarm", "--set", "gitops.targetNamespace=agent-platform", *FLEET_APIS]):
        manifest = helm(chart, flags)
        off = docs(manifest)
        if {k for k in off if k[0] not in ("OCIRepository", "HelmRelease")} != STORAGE_FAMILY:
            fail(f"engine off renders a non-Flux object beyond the storage-version hooks (ci-values turn kagent on): {sorted(k for k in off if k[0] not in ('OCIRepository', 'HelmRelease'))}")
        if any("pre-delete" in hook_meta(d)[0] for d in off.values()):
            fail("engine off renders a pre-delete hook (the ordered teardown is the engine's)")
        if any(n == RELEASE for k, _, n in off):
            fail("engine off renders the self OCIRepository/HelmRelease")
        for needle in SELF_MARKERS:
            if needle in manifest:
                fail(f"engine off render mentions {needle!r}")
    print("ok: engine off — no self object, self hook, policy, identity or values-Secret reference (the storage-version hooks alone); fleet shape clean")

    # --- engine ON, self default (auto → on)
    on_manifest = helm(chart, [*ci, "--include-crds"])
    on = docs(on_manifest)
    oci = on.get(("OCIRepository", NAMESPACE, RELEASE))
    hr = on.get(("HelmRelease", NAMESPACE, RELEASE))
    if not oci or not hr:
        fail("engine on: the self OCIRepository / HelmRelease did not render by default (gitops.self.enabled: auto must follow the engine)")
    needles(oci, "self OCIRepository", "\n  interval: 10m\n", f"\n  url: oci://gsoci.azurecr.io/charts/giantswarm/{RELEASE}\n", f'semver: "{derived}"', "app.kubernetes.io/component: self-management")
    if "insecure" in oci:
        fail("self OCIRepository sets insecure by default")
    if self_filter:
        needles(oci, "self OCIRepository", f"\n    semverFilter: {json.dumps(self_filter)}")
    elif "semverFilter" in oci:
        fail("self OCIRepository renders a semverFilter although gitops.self.semverFilter is empty")
    needles(hr, "self HelmRelease", "\n  suspend: true\n", f"\n  releaseName: {RELEASE}\n", f"\n  targetNamespace: {NAMESPACE}\n",
            f"\n  serviceAccountName: {TENANT_SA}\n", f"\n  chartRef:\n    kind: OCIRepository\n    name: {RELEASE}\n",
            "\n  install:\n    disableWait: true\n", "\n  upgrade:\n    disableWait: true\n",
            f"\n  valuesFrom:\n    - kind: Secret\n      name: {VALUES_SECRET}\n      valuesKey: values.yaml\n      optional: false", "\n  interval: 10m\n")
    if re.search(r"^    namespace:", hr[hr.index("chartRef:"):], re.M):
        fail("self HelmRelease chartRef names a namespace (cross-namespace references are refused under the lockdown)")
    for kind in ("ServiceAccount", "Role", "RoleBinding"):
        if (kind, NAMESPACE, SELF_SA) not in on:
            fail(f"engine on: {kind} {SELF_SA} missing")
    role = on[("Role", NAMESPACE, SELF_SA)]
    needles(role, "Role agent-platform-self", 'resources: ["secrets"]', 'resources: ["jobs"]', 'resources: ["helmreleases"]', 'apiGroups: ["helm.toolkit.fluxcd.io"]')
    if any(k == "ClusterRole" for k, *_ in on):
        fail("self-management renders a ClusterRole; a namespaced Role is enough")
    hooks = {(k, n): hook_meta(d) for (k, _, n), d in on.items() if "helm.sh/hook:" in d}
    expected_hooks = {
        # ci-values turn kagent on: the kagent namespace hook and the storage-version hooks (verify-engine.py) and the hook identity at their events
        ("ServiceAccount", f"{RELEASE}-hooks"): ("pre-install,pre-upgrade,post-install,post-upgrade,pre-delete", -10), ("ClusterRoleBinding", f"{RELEASE}-hooks"): ("pre-install,pre-upgrade,post-install,post-upgrade,pre-delete", -10),
        ("Job", f"{RELEASE}-kagent-namespace"): ("pre-install,pre-upgrade", -8),
        **{("Job", n): ev for n, ev in STORAGE_HOOKS.items()},
        ("Job", f"{RELEASE}-self-stop-resumer"): ("pre-delete", -6), ("Job", f"{RELEASE}-self-suspend"): ("pre-delete", -5),
        ("Job", f"{RELEASE}-self-values"): ("post-install,post-upgrade", 0),
        ("Job", f"{RELEASE}-teardown-releases"): ("pre-delete", 0), ("Job", f"{RELEASE}-teardown-engine"): ("pre-delete", 5),
    }
    if hooks != expected_hooks:
        fail(f"engine on, self on: hooks differ from the expected events/weights:\n  got      {hooks}\n  expected {expected_hooks}")
    stop = on[("Job", NAMESPACE, f"{RELEASE}-self-stop-resumer")]
    if job_args(stop) != ["delete", "jobs.batch", "--namespace", NAMESPACE, f"{RELEASE}-self-resume", "--ignore-not-found"] or f'image: "{kubectl_ref}"' not in stop or "command:" in stop:
        fail(f"the -6 hook is not one plain kubectl delete of the resumer Job in the kubectl image: {job_args(stop)}")
    suspend = on[("Job", NAMESPACE, f"{RELEASE}-self-suspend")]
    needles(suspend, "the -5 hook", f'image: "{helm_ref}"', 'command: ["/bin/sh", "-eu", "-c"]', '{"spec":{"suspend":true}}', "nothing to suspend",
            "kubectl delete secrets", "app.kubernetes.io/component=self-management", f"serviceAccountName: {SELF_SA}")
    if VALUES_SECRET in suspend:
        fail("the -5 hook deletes the values Secret by name; it must go by the hook's labels")
    vals = on[("Job", NAMESPACE, f"{RELEASE}-self-values")]
    needles(vals, "the values hook", f'image: "{helm_ref}"', "helm get values", f'secret="{VALUES_SECRET}"', "reconcile.fluxcd.io/watch", "--server-side",
            f"{RELEASE}-self-resume", "kubectl create -f - <<'EOF'", "\n              EOF\n", "activeDeadlineSeconds: 420", "deployed|failed",
            "NOT resuming", "exit 1", '{"spec":{"suspend":false}}', f"serviceAccountName: {SELF_SA}", "--all" if False else "helm history")
    if "--all" in vals.split("helm get values")[1].split("\n")[0]:
        fail("the values hook writes the MERGED values (helm get values --all); it must write the user-supplied values only")
    for job in (stop, suspend, vals):
        needles(job, "self hook Job", "restartPolicy: Never", "runAsNonRoot: true", "readOnlyRootFilesystem: true", "allowPrivilegeEscalation: false", "- ALL", "type: RuntimeDefault")
    pol = on.get(("ValidatingAdmissionPolicy", "", POLICY))
    binding = on.get(("ValidatingAdmissionPolicyBinding", "", POLICY))
    if not pol or not binding:
        fail(f"engine on: ValidatingAdmissionPolicy / Binding {POLICY} missing")
    needles(pol, "the admission policy", "apiVersion: admissionregistration.k8s.io/v1", "failurePolicy: Fail", 'operations: ["CREATE"]', 'resources: ["secrets"]',
            "object.type == 'helm.sh/release.v1'", f"object.metadata.labels['name'] == \"{RELEASE}\"", f'"{ANNOTATION}" in namespaceObject.metadata.annotations',
            f'namespaceObject.metadata.annotations["{ANNOTATION}"] == \'allow\'', f'request.userInfo.username == "system:serviceaccount:{NAMESPACE}:{TENANT_SA}" || variables.handBack',
            f"Secret {VALUES_SECRET} in namespace {NAMESPACE}", f"{ANNOTATION}=allow", "gitops.self.enabled=false --force-conflicts")
    if re.search(r'operations: \[.*"(UPDATE|DELETE)"', pol):
        fail("the admission policy matches UPDATE or DELETE; helm uninstall and the status updates must pass")
    needles(binding, "the policy binding", f"policyName: {POLICY}", "validationActions: [Deny]", f"kubernetes.io/metadata.name: {NAMESPACE}")
    print(f"ok: engine on, self on — OCIRepository ({derived}), suspended HelmRelease as {TENANT_SA}, {SELF_SA} with a Role, hooks at -6/-5 (pre-delete) and 0 (post-install/-upgrade), policy + binding {POLICY}")

    # --- engine ON, self OFF: the hand-back / lab render
    for label, flags in (("self off", [*ci, *SELF_OFF, "--include-crds"]), ("tests/test-values.yaml", ["-f", f"{chart}/../../tests/test-values.yaml", "--include-crds"])):
        off_manifest = helm(chart, flags)
        soff = docs(off_manifest)
        if self_objects(soff):
            fail(f"{label}: self objects rendered: {sorted(self_objects(soff))}")
        for needle in ("ValidatingAdmissionPolicy", VALUES_SECRET):
            if needle in off_manifest:
                fail(f"{label}: the render mentions {needle!r}")
        for kind in ("ServiceAccount", "Role", "RoleBinding"):
            if (kind, NAMESPACE, SELF_SA) not in soff:
                fail(f"{label}: {kind} {SELF_SA} missing (the -6/-5 hooks need it whenever the engine is on)")
        hooks_off = {(k, n): hook_meta(d) for (k, _, n), d in soff.items() if "helm.sh/hook:" in d}
        expected_off = dict(expected_hooks)
        del expected_off[("Job", f"{RELEASE}-self-values")]
        expected_off[("Job", f"{RELEASE}-self-stop-resumer")] = ("pre-upgrade,pre-delete", -6)
        expected_off[("Job", f"{RELEASE}-self-suspend")] = ("pre-upgrade,pre-delete", -5)
        if label != "self off":
            # the lab values leave kagent off: no kagent namespace hook, no storage-version hooks, the hook identity at pre-delete only
            del expected_off[("Job", f"{RELEASE}-kagent-namespace")]
            for n in STORAGE_HOOKS:
                del expected_off[("Job", n)]
            expected_off[("ServiceAccount", f"{RELEASE}-hooks")] = ("pre-delete", -10)
            expected_off[("ClusterRoleBinding", f"{RELEASE}-hooks")] = ("pre-delete", -10)
        if hooks_off != expected_off:
            fail(f"{label}: hooks differ:\n  got      {hooks_off}\n  expected {expected_off}")
        if label == "self off":
            # nothing else changes: every object the two renders share is identical, but for the two hooks' events
            shared = set(on) & set(soff)
            for key in sorted(shared):
                a, b = on[key], soff[key]
                if key[2] in (f"{RELEASE}-self-stop-resumer", f"{RELEASE}-self-suspend"):
                    a = a.replace("helm.sh/hook: pre-delete", "helm.sh/hook: pre-upgrade,pre-delete")
                if a != b:
                    fail(f"self-management changes {key} beyond its own objects")
            only_on = set(on) - set(soff)
            if only_on != {("OCIRepository", NAMESPACE, RELEASE), ("HelmRelease", NAMESPACE, RELEASE), ("Job", NAMESPACE, f"{RELEASE}-self-values"),
                           ("ValidatingAdmissionPolicy", "", POLICY), ("ValidatingAdmissionPolicyBinding", "", POLICY)}:
                fail(f"self on adds objects beyond the five: {sorted(only_on)}")
            if set(soff) - set(on):
                fail(f"self off renders objects self on does not: {sorted(set(soff) - set(on))}")
    print("ok: engine on, self off (and the lab values) — no self object/policy/values hook; -6/-5 at pre-upgrade,pre-delete; nothing else differs")

    # --- guards
    helm(chart, [*ci, *ENGINE_OFF, *SELF_ON], expect_fail="gitops.self.enabled=true needs the bundled Flux engine")
    helm(chart, [*ci, "--kube-version", "1.29.0"], expect_fail="needs Kubernetes >= 1.30")
    helm(chart, [*ci, "--kube-version", "1.30.0"])
    helm(chart, [*ci, *SELF_OFF, "--kube-version", "1.29.0"])
    helm(chart, [*ci, "--set", "gitops.self.enabled=bogus"], expect_fail=("/gitops/self/enabled", "gitops.self.enabled must be one of"))
    print("ok: guards — true without the engine refused; Kubernetes < 1.30 refused with self on, accepted with it off; the schema pins auto/true/false")

    # --- knobs
    knobs = docs(helm(chart, [*ci, "--set", "gitops.self.repository=oci://localhost:5000/charts/", "--set", "gitops.self.insecure=true",
                              "--set", "gitops.self.versionRange=>=3.0.0 <4.0.0", "--set-json", 'gitops.self.semverFilter=".*-dev\\\\.x\\\\..*"',
                              "--set", "gitops.self.interval=1m", "--api-versions", "helm.toolkit.fluxcd.io/v2"]))
    needles(knobs[("OCIRepository", NAMESPACE, RELEASE)], "self OCIRepository with knobs", f"\n  url: oci://localhost:5000/charts/{RELEASE}\n", "\n  insecure: true\n", 'semver: ">=3.0.0 <4.0.0"',
            '\n    semverFilter: ".*-dev\\\\.x\\\\..*"', "\n  interval: 1m\n")
    needles(knobs[("HelmRelease", NAMESPACE, RELEASE)], "self HelmRelease with the HelmRelease API served offline", "\n  suspend: true\n", "\n  interval: 1m\n")
    custom = docs(helm(chart, [*ci, "--set", "gitops.serviceAccountName=custom-sa"]))
    needles(custom[("HelmRelease", NAMESPACE, RELEASE)], "self HelmRelease with gitops.serviceAccountName", "\n  serviceAccountName: custom-sa\n")
    needles(custom[("ValidatingAdmissionPolicy", "", POLICY)], "the policy with gitops.serviceAccountName", f'"system:serviceaccount:{NAMESPACE}:custom-sa"')
    print("ok: knobs — repository, insecure, versionRange, semverFilter, interval; suspended with the HelmRelease API offline; gitops.serviceAccountName carried into the HelmRelease and the policy")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
