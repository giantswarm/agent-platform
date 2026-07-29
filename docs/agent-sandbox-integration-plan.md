# Integrate agent-sandbox into the agentic-platform umbrella chart

## Context

`agentic-platform` is Giant Swarm's MCP-gateway umbrella Helm chart. It bundles
components (muster, agentgateway, kagent, valkey, …) as `Chart.yaml`
dependencies resolved from published OCI registries, and keeps a strict split:
**all CRDs live in the companion `agentic-platform-crds` chart; the workload
chart installs none.**

We want [kubernetes-sigs/agent-sandbox](https://github.com/kubernetes-sigs/agent-sandbox)
(the `Sandbox` CRD + controller) installable with the platform. This is not a
nice-to-have: `agentic-platform-crds` already ships kagent's `SandboxAgent` CRD,
but kagent's sandboxed-agent feature is **inert without the agent-sandbox
controller running** — agent-sandbox is the substrate `SandboxAgent` delegates
isolation to. Integrating it lights up a capability the platform already
half-ships.

### The blocker (verified against source, 2026-06-15)

agent-sandbox *does* ship a clean Helm chart in-repo (`helm/`, `name:
agent-sandbox`, `version: 0.1.0`, with `crds/` + `templates/`), but **publishes
it nowhere** — releases carry only `manifest.yaml` / `extensions.yaml` + a pip
package, `Chart.yaml` is pinned at a static `0.1.0`, and there is no
chart-releaser / OCI push. So it **cannot** be added as a remote
`dependencies:` entry the way the others are. Only the controller *image* is
published: `registry.k8s.io/agent-sandbox/agent-sandbox-controller` (latest
stable **v0.4.6**; v0.5.0rc1 is pre-release). The chart does **not** read
`global.registry`; `image.tag` is required (defaults empty).

### Decided approach (user-confirmed)

Mirror the existing `muster` + `muster-crds` split: Giant Swarm publishes **two**
OCI charts to `oci://gsoci.azurecr.io/charts/giantswarm`, then this repo adds
normal dependencies. CRDs go into `agentic-platform-crds`. Controller image
mirrored via the GS retagger.

## Work outside this repo (prerequisites — new GS repos + retagger)

These gate the in-repo wiring; they must land and publish first.

1. **Mirror the controller image** `registry.k8s.io/agent-sandbox/agent-sandbox-controller`
   → `gsoci.azurecr.io/giantswarm/agent-sandbox-controller` via the GS retagger
   (use `override_repo_name` for a flat path, as kagent does). Pin **v0.4.6**.

2. **New repo `giantswarm/agent-sandbox-crds`** — re-template the 4 upstream
   CRDs from `helm/crds/` (`sandboxes.agents.x-k8s.io`,
   `sandboxtemplates`/`sandboxclaims`/`sandboxwarmpools.extensions.agents.x-k8s.io`)
   so GS can inject `helm.sh/resource-policy: keep` (the knob upstream lacks —
   same keep-gap documented for agentgateway/kagent CRDs). Ship **all four**
   CRDs unconditionally (CRD-chart convention; the core `Sandbox` is what
   `SandboxAgent` needs, the rest support the extensions controller). Publish to
   `oci://gsoci.azurecr.io/charts/giantswarm`.

3. **New repo `giantswarm/agent-sandbox-app`** — repackage the upstream `helm/`
   chart with `crds/` **removed** (CRDs now come from `agent-sandbox-crds`,
   honoring "workload chart installs no CRDs"). Keep the upstream `templates/`
   (deployment, RBAC, service, SA, namespace). Default `image.repository` to the
   GS-mirrored path and `image.tag` to the pinned version. Publish to the same
   registry.

   - **Why a GS crds chart rather than vendoring raw CRD YAML into
     `agentic-platform-crds`:** that chart has *zero* precedent for carrying its
     own templates (it is a pure dependency umbrella), and the keep-policy
     injection needs templating anyway. A GS-owned crds chart satisfies both.

## In-repo changes (this repo — wire up once the charts publish)

### 1. `helm/agentic-platform-crds/Chart.yaml`
Add an **unconditional** dependency (CRD charts install all CRDs regardless of
whether the workload is enabled, matching the existing `kagent-crds` comment):
```yaml
  - name: agent-sandbox-crds
    version: <gs-x.y.z>
    repository: oci://gsoci.azurecr.io/charts/giantswarm
```
Then regenerate `helm/agentic-platform-crds/Chart.lock` (`helm dependency update`).

### 2. `helm/agentic-platform-crds/README.md`
Add a "What it ships" table row for the `agents.x-k8s.io` /
`extensions.agents.x-k8s.io` CRDs and note their keep-policy status in the
uninstall table.

### 3. `helm/agentic-platform/Chart.yaml`
Add a **conditioned** dependency, after the existing entries:
```yaml
  - name: agent-sandbox
    version: <gs-x.y.z>
    repository: oci://gsoci.azurecr.io/charts/giantswarm
    condition: agentSandbox.enabled
```
Then regenerate `helm/agentic-platform/Chart.lock`.

### 4. `helm/agentic-platform/values.yaml`
Add a top-level `agentSandbox:` block (model it on the existing `kagent:` block,
`helm/agentic-platform/values.yaml:356`). Default **off**; pin the GS-mirrored
image explicitly since the sub-chart ignores `global.registry`:
```yaml
agentSandbox:
  enabled: false
  image:
    repository: gsoci.azurecr.io/giantswarm/agent-sandbox-controller
    tag: v0.4.6
  controller:
    extensions: false   # core Sandbox only by default
  # Restricted-PSS contexts (Kyverno on GS clusters) — mirror kagent's blocks:
  podSecurityContext: { runAsNonRoot: true, seccompProfile: { type: RuntimeDefault } }
  containerSecurityContext:
    allowPrivilegeEscalation: false
    capabilities: { drop: [ALL] }
    runAsNonRoot: true
    seccompProfile: { type: RuntimeDefault }
```

### 5. `helm/agentic-platform/values.schema.json`
Add an `agentSandbox` property under the top-level `properties` (top level is
`additionalProperties: true`, so this is permissive — follow the `kagent`
entry's `additionalProperties: true` style at line 253 rather than the strict
blocks).

### 6. `helm/agentic-platform/ci/test-agent-sandbox-values.yaml` (new)
Minimal CI install exercising `agentSandbox.enabled: true` in `muster-direct`
mode (no agentgateway), with OAuth/valkey disabled — model on
`ci/test-kagent-routing-values.yaml` but simpler.

### 7. `CHANGELOG.md`
Add an entry under `## [Unreleased]` (currently empty,
`CHANGELOG.md:8`), e.g. under `### Added`: bundle agent-sandbox controller
(opt-in `agentSandbox.enabled`), CRDs via `agentic-platform-crds`; note it is
the substrate kagent's `SandboxAgent` requires.

### 8. `README.md`
- Add `agentSandbox.enabled` to the Configuration table.
- Add an "Agent sandbox" subsection explaining the kagent `SandboxAgent`
  relationship and that enabling it is the prerequisite controller.
- Add the image to the "Private registry overrides" table.

### 9. (Optional) `helm/agentic-platform/templates/kagent/validate.yaml`
Add a soft guard/NOTES warning when `kagent.enabled` and a `SandboxAgent` is in
use but `agentSandbox.enabled: false`, since that combination silently does
nothing.

## Open items to confirm before/at implementation
- **GS chart versions** for the two new charts (start `0.1.0`?) — fill the
  `<gs-x.y.z>` placeholders once published.
- **Extensions** (`SandboxTemplate`/`Claim`/`WarmPool` controllers): ship CRDs
  now, controller off by default (`controller.extensions: false`). Confirm
  that's the desired initial scope.
- **Namespace**: upstream chart defaults to `agent-sandbox-system` and can
  create it. Decide whether to keep that or co-locate (the controller is
  cluster-scoped, so a dedicated namespace is fine — recommend keeping the
  upstream default).

## Verification

Prereqs unpublished → verify in stages.

**In-repo, against the published charts:**
```bash
# CRDs chart resolves + renders the 4 agent-sandbox CRDs
helm dependency update helm/agentic-platform-crds
helm template helm/agentic-platform-crds | grep -E 'kind: CustomResourceDefinition' -A2 | grep agents.x-k8s.io

# Workload chart: disabled by default renders nothing for agent-sandbox
helm dependency update helm/agentic-platform
helm template helm/agentic-platform | grep -i agent-sandbox    # expect: no controller Deployment

# Enabled renders the controller, no CRDs from the workload chart
helm template helm/agentic-platform -f helm/agentic-platform/ci/test-agent-sandbox-values.yaml \
  | grep -E 'kind: (Deployment|CustomResourceDefinition)'      # Deployment yes, CRD no

# Schema accepts the new values
helm lint helm/agentic-platform -f helm/agentic-platform/ci/test-agent-sandbox-values.yaml
```

**End-to-end on a test cluster (`--context` required per house rules):**
```bash
helm install agentic-platform-crds oci://.../agentic-platform-crds --version <ver> -n muster --create-namespace --kube-context <ctx>
kubectl --context <ctx> wait --for=condition=Established crd/sandboxes.agents.x-k8s.io
helm install agentic-platform oci://.../agentic-platform --version <ver> -n muster \
  -f values.yaml --set agentSandbox.enabled=true --kube-context <ctx>
kubectl --context <ctx> -n agent-sandbox-system get deploy   # controller Running
# Then: create a SandboxAgent (kagent) and confirm a Sandbox CR is reconciled into a pod.
```
