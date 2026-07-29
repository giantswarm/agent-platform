# Handoff: wire agent-sandbox into the agentic-platform umbrella

You are picking up the **final phase** of integrating `agent-sandbox` into Giant
Swarm's `agentic-platform` umbrella chart. Everything upstream of this (the
charts, their CI, the image mirror, team ownership) is **already done and
merged**. Your job is the in-repo wiring in **this repo** (`giantswarm/agentic-platform`).

---

## 1. Background (why this exists)

- **agent-sandbox** = the `kubernetes-sigs/agent-sandbox` controller (the `Sandbox`
  CRD + controller). It is the **substrate that kagent's `SandboxAgent` CRD needs**
  to actually run — the two are complementary, not alternatives. `agentic-platform-crds`
  already ships kagent's `SandboxAgent` CRD, but nothing installs the agent-sandbox
  controller yet. This wiring fixes that.
- Giant Swarm publishes **one repo, two charts** (the muster + muster-crds pattern):
  `giantswarm/agent-sandbox` ships **both** `agent-sandbox` (controller) and
  `agent-sandbox-crds`, published to `oci://gsoci.azurecr.io/charts/giantswarm`.
- `agent-sandbox` is a thin wrapper: it vendors the upstream chart as a `file://`
  subchart (managed by vendir) and the GS-mirrored image
  (`gsoci.azurecr.io/giantswarm/agent-sandbox-controller`) is already the default.
- **Repo convention (hard rule):** the workload chart installs **NO** CRDs — they
  ship only in `agentic-platform-crds`. The `agent-sandbox` chart already has its
  CRDs stripped; `agent-sandbox-crds` carries them (keep-annotated).

## 2. Prerequisites — verify BEFORE you start

This wiring can only **merge** once the charts are published. Check:

```bash
# Both charts must resolve at the same version (they release in lockstep off a v* tag):
helm show chart oci://gsoci.azurecr.io/charts/giantswarm/agent-sandbox | grep -E '^version|^appVersion'
helm show chart oci://gsoci.azurecr.io/charts/giantswarm/agent-sandbox-crds | grep -E '^version'
# Controller image mirrored (retagger PR #1195):
docker manifest inspect gsoci.azurecr.io/giantswarm/agent-sandbox-controller:v0.4.6
```

If the charts are **not yet published**, you can still do all the edits, but pin a
placeholder version and expect `helm dependency update` / CI to fail until the
release lands. Use the **published chart version** (NOT the upstream appVersion
`0.4.6`) for the `version:` fields below — call it `<ASB_VERSION>`.

## 3. Tasks

### 3.1 — Add the CRDs dependency to `helm/agentic-platform-crds/Chart.yaml`
Add **unconditionally** (CRD charts install all CRDs regardless of whether the
workload is enabled — same as the existing `kagent-crds` entry). Place after
`kagent-crds`:

```yaml
  # agent-sandbox CRDs (Sandbox + SandboxTemplate / SandboxClaim / SandboxWarmPool).
  # No condition — a CRDs chart installs all of its CRDs. Unlike the agentgateway
  # and kagent CRDs, these ARE marked `helm.sh/resource-policy: keep` (injected at
  # render time by the agent-sandbox-crds chart), so they survive uninstall.
  - name: agent-sandbox-crds
    version: <ASB_VERSION>
    repository: oci://gsoci.azurecr.io/charts/giantswarm
```
Then `helm dependency update helm/agentic-platform-crds` to refresh `Chart.lock`.

### 3.2 — Add the workload dependency to `helm/agentic-platform/Chart.yaml`
Add after the existing deps, **conditioned**:

```yaml
  # agent-sandbox — kubernetes-sigs/agent-sandbox controller (Sandbox runtime),
  # the substrate kagent's SandboxAgent delegates isolation to. CRDs ship in the
  # companion agentic-platform-crds chart (install first). Opt-in via the top-level
  # `agentSandbox.enabled` toggle (NOT a key inside the chart's value namespace —
  # see values.yaml note).
  - name: agent-sandbox
    version: <ASB_VERSION>
    repository: oci://gsoci.azurecr.io/charts/giantswarm
    condition: agentSandbox.enabled
```
Then `helm dependency update helm/agentic-platform` to refresh `Chart.lock`.

### 3.3 — values.yaml — ⚠️ THE KEY GOTCHA
The `condition` toggle **must be a separate top-level key**, NOT a key inside the
`agent-sandbox` chart's own value namespace. Reason: the published `agent-sandbox`
chart's `values.schema.json` has `additionalProperties: false` at the root, so an
`enabled` key under its namespace would be **rejected at render time**. This is the
exact same reason `mcps.enabled` lives outside the `agentic-platform-mcps:` block
(see the README "Bundled MCP servers" section).

So in `helm/agentic-platform/values.yaml` add a **separate** block:

```yaml
# agent-sandbox controller (kubernetes-sigs/agent-sandbox). Opt-in. The `enabled`
# toggle is the `condition: agentSandbox.enabled` dependency gate and lives OUTSIDE
# the agent-sandbox chart's value namespace because that chart's strict
# values.schema.json (additionalProperties:false) rejects an `enabled` key.
agentSandbox:
  enabled: false
  # podSecurity below feeds the Kyverno mutate policy in templates/agent-sandbox/.
  podSecurity:
    enabled: true
    namespace: agent-sandbox-system   # must match the agent-sandbox chart's namespace.name
    podSecurityContext:
      runAsNonRoot: true
      seccompProfile:
        type: RuntimeDefault
    containerSecurityContext:
      allowPrivilegeEscalation: false
      capabilities:
        drop: [ALL]
      runAsNonRoot: true
      seccompProfile:
        type: RuntimeDefault

# Values forwarded to the agent-sandbox dependency chart itself (its namespace is
# its chart name). Usually empty — the chart already defaults the gsoci image. Only
# add overrides here if needed; note the agent-sandbox chart nests the upstream
# subchart under its own `agent-sandbox:` key, so a controller override is
# `agent-sandbox.agent-sandbox.<key>`.
# agent-sandbox: {}
```

Model the block placement/comment style on the existing `kagent:` and `mcps:`
blocks in the same file.

### 3.4 — Kyverno securityContext policy (the important one)
During the agent-sandbox chart review it was decided to keep that chart
**vendor-agnostic** — the restricted-PSS securityContext was **removed from the
chart** and must be applied **here, in the umbrella**, via a Kyverno mutate policy.
(The upstream v0.4.6 controller exposes no securityContext values knob, and
kube-linter/PSS check the static manifest, so a render-time mutation is needed.)

Create `helm/agentic-platform/templates/agent-sandbox/pod-security.yaml`, modeled
**exactly** on `helm/agentic-platform/templates/kagent/declarative-agent-pod-security.yaml`
(same ClusterPolicy + `patchStrategicMerge` style, the repo's helpers for name/labels):

```yaml
{{- if and .Values.agentSandbox.enabled .Values.agentSandbox.podSecurity.enabled }}
# The agent-sandbox controller comes from the vendored upstream chart, which at
# v0.4.6 exposes no securityContext via values. Inject restricted-PSS at admission
# so it passes Kyverno / PSA on Giant Swarm clusters. (When upstream v0.5.0 stable
# exposes securityContext via values, prefer plain subchart values and drop this.)
apiVersion: kyverno.io/v1
kind: ClusterPolicy
metadata:
  name: {{ include "name" . }}-agent-sandbox-pod-security
  labels:
    {{- include "labels.common" . | nindent 4 }}
  annotations:
    policies.kyverno.io/title: agent-sandbox controller pod security
spec:
  rules:
  - name: add-controller-security-context
    match:
      any:
      - resources:
          kinds: ["apps/v1/Deployment"]
          names: ["agent-sandbox-controller"]
          namespaces: ["{{ .Values.agentSandbox.podSecurity.namespace }}"]
    mutate:
      patchStrategicMerge:
        spec:
          template:
            spec:
              +(securityContext):
                {{- toYaml .Values.agentSandbox.podSecurity.podSecurityContext | nindent 16 }}
              containers:
              - (name): agent-sandbox-controller
                +(securityContext):
                  {{- toYaml .Values.agentSandbox.podSecurity.containerSecurityContext | nindent 18 }}
{{- end }}
```
Use whatever helper names the repo's `_helpers.tpl` actually defines (check
`templates/_helpers.tpl`; the kagent policy uses `include "name"` and
`include "labels.common"`).

### 3.5 — values.schema.json
Add an `agentSandbox` property under the top-level `properties` in
`helm/agentic-platform/values.schema.json`. The top level is
`"additionalProperties": true`, so keep it permissive — follow the existing
`kagent` entry's `"additionalProperties": true` style rather than a strict block.

### 3.6 — CI test values
Add `helm/agentic-platform/ci/test-agent-sandbox-values.yaml` exercising
`agentSandbox.enabled: true` (in `muster-direct` mode, OAuth/valkey off — model on
the simplest existing `ci/test-*.yaml`).

### 3.7 — CHANGELOG.md
Add an entry under `## [Unreleased]` (Keep a Changelog format, matching existing
entries) describing the new opt-in agent-sandbox controller + CRDs and the kagent
`SandboxAgent` relationship.

### 3.8 — README.md
- Add `agentSandbox.enabled` to the Configuration table.
- Add an "Agent sandbox" subsection explaining it's the controller kagent's
  `SandboxAgent` requires, and that the securityContext is applied via the umbrella
  Kyverno policy.
- Add a row to the CRD-lifecycle table (the agent-sandbox CRDs ARE keep-protected).
- Add the controller image to the "Private registry overrides" table.

## 4. Verification

```bash
helm dependency update helm/agentic-platform-crds
helm dependency update helm/agentic-platform
helm lint helm/agentic-platform helm/agentic-platform-crds

# CRDs chart renders the agent-sandbox CRDs (with keep annotation):
helm template helm/agentic-platform-crds | grep -E 'sandboxes?\.|agents\.x-k8s\.io|resource-policy'

# Disabled by default -> no agent-sandbox controller, no ClusterPolicy:
helm template helm/agentic-platform | grep -iE 'agent-sandbox-controller|ClusterPolicy' # expect none from agent-sandbox

# Enabled -> controller Deployment + Kyverno ClusterPolicy render:
helm template helm/agentic-platform -f helm/agentic-platform/ci/test-agent-sandbox-values.yaml \
  | grep -E 'kind: (Deployment|ClusterPolicy)|agent-sandbox-controller'

# Confirm the WORKLOAD chart still installs NO CRDs (the agent-sandbox subchart has crds/ stripped):
helm template helm/agentic-platform -f helm/agentic-platform/ci/test-agent-sandbox-values.yaml \
  | grep -c 'kind: CustomResourceDefinition'   # expect 0
```

## 5. House rules (from the user's global CLAUDE.md — follow exactly)
- **Never push to `main`/`master`.** Branch with hyphens (e.g. `wire-agent-sandbox`).
- **Never force-push.**
- Open a **draft PR assigned to `fiunchinho`** and **share the link** in your reply.
- **Conventional Commits** for commit messages AND the PR title (a `semantic_pull_request`
  check validates the PR title).
- Add a **CHANGELOG.md** entry (this repo requires it).
- If you run any `kubectl`, always pass `--context`.

## 6. Reference files to copy patterns from (this repo)
- Dependency entries + conditions: `helm/agentic-platform/Chart.yaml` (`kagent`, `mcps`/`agentic-platform-mcps`, `klaus-gateway`).
- CRD sub-chart dep (unconditional + keep-gap note): `helm/agentic-platform-crds/Chart.yaml` (`kagent-crds`).
- Separate-toggle-outside-strict-namespace pattern: `mcps.enabled` in `values.yaml` + README "Bundled MCP servers".
- Kyverno mutate ClusterPolicy: `helm/agentic-platform/templates/kagent/declarative-agent-pod-security.yaml`.
- values block style: the `kagent:` block in `helm/agentic-platform/values.yaml`.

## 7. Current state snapshot (as of handoff)
- `agentic-platform` chart `version: 1.1.26`; deps: muster 0.4.1, agentgateway v1.2.1, valkey 0.1.2, agentic-platform-mcps 0.3.0, kagent 0.9.6, klaus-gateway.
- `agentic-platform-crds` chart `version: 0.2.0`; deps: agentgateway-crds v1.2.1, muster-crds 0.4.0, kagent-crds 0.9.6.
- `agent-sandbox` repo: PR #7 merged (charts + vendir + multi-chart CI + ATS smoke test). Not yet released — **needs a `v*` tag** to publish to gsoci OCI.
- retagger PR #1195 (image mirror) open — merge it so the controller image exists at gsoci.
- Nothing agent-sandbox-related is wired into this repo yet (greenfield).
