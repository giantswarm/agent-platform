[![CircleCI](https://dl.circleci.com/status-badge/img/gh/giantswarm/agent-platform/tree/main.svg?style=svg)](https://dl.circleci.com/status-badge/redirect/gh/giantswarm/agent-platform/tree/main)
[![OpenSSF Scorecard](https://api.securityscorecards.dev/projects/github.com/giantswarm/agent-platform/badge)](https://securityscorecards.dev/viewer/?uri=github.com/giantswarm/agent-platform)

# agent-platform

The Agent Platform is Giant Swarm's MCP gateway deploy unit. It ships [muster](https://github.com/giantswarm/muster) (MCP gateway / aggregator) and [agentgateway](https://github.com/agentgateway/agentgateway) (MCP data plane) together, plus the consumer-side `Gateway`, `AgentgatewayParameters`, and network policies that wire them up.

Owner: team-bumblebee.

This repo publishes **two** charts:

| Chart | What it is |
|---|---|
| `agent-platform` | the **meta-package** — an app-of-apps that renders each component and the connectivity layer as Flux `OCIRepository` + `HelmRelease`. The single thing you install. |
| `agent-platform-connectivity` | the consumer-side **wiring** the meta-package renders as a child release: the public muster route, the agentgateway data-plane `Gateway` + `AgentgatewayParameters` + `HTTPRoute`s + `BackendTrafficPolicy`s, the `NetworkPolicy`s, the kagent controller `GRPCRoute` with its JWT policy, the klaus-gateway/model-manager/agent-manager routes, the admin-owned kagent catalog (`ModelConfig`s and operator `RemoteMCPServer`s at `kagent.dev/v1alpha3`; the per-agent muster `RemoteMCPServer` is the agent chart's), the `kagent-flux` tenant identity, and the CNPG `Cluster`. Agents themselves are created with the generic [`agent` chart](https://github.com/giantswarm/agent), one release per agent. |

> **CRDs are app-owned.** There is no longer a standalone `agent-platform-crds` bundle chart — each component ships its own CRDs: in its chart's `crds/` dir, upgraded atomically with the app via Flux `CreateReplace` (muster, agentgateway, agent-sandbox), or as templates of its own CRD chart (`kagent-crds` for the kagent line, `kserve-crd`, `kserve-llmisvc-crd`, `cloudnative-pg`). A CR consumer `dependsOn` the component that owns the CRD it needs. See [CRD lifecycle](#crd-lifecycle).

## Meta-package release flow

> Implements [giantswarm/giantswarm#36875](https://github.com/giantswarm/giantswarm/issues/36875). Concept write-up: klaus-lab `architecture/agent-platform-meta-package.md`.

The `agent-platform` chart no longer bundles its components as pinned Helm subcharts. It is an **app-of-apps meta-package**: `templates/components.yaml` renders, per entry in `.Values.components`, a Flux `OCIRepository` + `HelmRelease`. Flux is the only render engine (`gitops.engine` accepts `flux` only). It emits **only** those objects (a *pure* renderer — no raw CRs of its own) — plus, where a cluster has no Flux, the engine that reconciles them: the `flux-engine` subchart, on by default, off on a cluster that runs its own Flux (see [Installing](#installing)).

The decisive change: each component's version is a **constraint expressed as a value** (`components.<name>.versionRange`), not a `Chart.yaml` pin. Flux re-resolves the range on every reconcile, so a new component release rolls forward **with no PR to this chart and no umbrella re-package**.

- **One gitops entry.** You install the meta-package (one `OCIRepository` + `HelmRelease`). It renders each component release and the `agent-platform-connectivity` release for you.
- **CRD-before-CR ordering is preserved** — each component ships its own CRDs (app-owned CRDs), and a CR consumer `dependsOn` the component that owns the CRD it needs. A `dependsOn` reference to a component that is toggled off is dropped at render time, so an always-on consumer never blocks on a release that was never rendered. Because the connectivity CRs live in their own release (not in the meta-package's own manifest), they only apply after the CRDs are Established — the meta-package itself ships no CR that could race a CRD.
- **Per-component values keep their blocks; the on/off switch does not** — the existing `muster:`, `agentgateway:`, `kagent:`, `valkey:`, `klausGateway:`, `agentSandbox:`, `agent-platform-mcps:` blocks still drive each component's configuration, and the connectivity wiring blocks (`ingress:`, `gateway:`, `networkPolicy:`, `postgres:`, `extraObjects:`) still drive the wiring. Whether a component is installed at all is `components.<name>.enabled` — see [Enabling and disabling components](#enabling-and-disabling-components). Each `components.<name>` entry names its source block via `valuesFrom` (connectivity uses `forwardAllValues`).
- **Dev vs customer track** — keep the `components.*.versionRange` values **wide** for the internal/dogfooding track (continuous auto-update, the default). **Pin** them to exact versions for a customer **bill-of-materials**; see [`helm/agent-platform/examples/customer-bom.yaml`](helm/agent-platform/examples/customer-bom.yaml). A "product release" is that pinned values snapshot.
- **Dev channel** — `components.<name>.semverFilter` (rendered as `OCIRepository.spec.ref.semverFilter`) is a Go regexp the registry's tags must match *before* `versionRange` is evaluated. With a prerelease-admitting range (`>=3.0.0-0 <4.0.0-0`) and the filter of one branch's dev builds (`.*-dev\.<sanitized-branch>\..*`) a component follows that branch's newest build and never falls back to a stable tag; `gitops.self.semverFilter` does the same for the chart's own source under self-management. Unset by default. See [Dev channel](#dev-channel).

### Dev channel

Every push to a branch of this repository publishes both charts as **dev builds** — `X.Y.Z-dev.<branch, lowercased to [a-z0-9-]>.<YYYY-MM-DD>.<HH-MM-SS>.h<sha7>`, one version for the two charts of a commit — to `oci://gsoci.azurecr.io/charts/giantswarm/{agent-platform,agent-platform-connectivity}`; the component repositories publish theirs the same way (the kagent line's as `0.11.0-dev.giantswarm.…` under `ghcr.io/giantswarm/kagent`). Two knobs make a consumer follow such a build instead of the releases the defaults select:

- `components.<name>.semverFilter` — a Go regexp rendered as `OCIRepository.spec.ref.semverFilter`, which Flux applies to the registry's tags *before* it evaluates the range. With a prerelease-admitting range (`>=3.0.0-0 <4.0.0-0`: Flux's Masterminds semver never matches a prerelease against a bound without a `-0`, and rejects the RFC's `*-*`) and one branch's filter (`.*-dev\.<sanitized-branch>\..*`) the component follows that branch's newest build and never falls back to a release. An exact pin (`versionRange: "<full dev tag>"`) selects one build without a filter.
- `gitops.self.semverFilter` — the same for the chart's own source under self-management.

The defaults carry no filter: a release selects releases. agentlab follows a branch of this repository with `agentlab configure --chart-branch <branch>` and installs a checkout's chart directly through `platform.chartPath`; component overrides go into a `platform.valuesFiles` overlay. `make verify-components` asserts the defaults render no filter and that a filter set on one component reaches that component's `OCIRepository` alone.

### Enabling and disabling components

`components.<name>.enabled` is the only switch. It decides whether the component gets a release, and the render loop forwards the roster, stripped to enablement, into the `agent-platform-connectivity` release, which reads the same key path. The wiring that chart renders for a component (ClusterRoles, NetworkPolicies, routes, CRs) therefore cannot disagree with whether the component is installed. `<name>` is the `components:` key, which is also the chart name:

| Component | Key | Default |
|---|---|---|
| agentgateway controller | `components.agentgateway.enabled` | `false` |
| Valkey | `components.valkey.enabled` | `true` |
| MCP server CRs | `components.agent-platform-mcps.enabled` | `false` |
| kagent | `components.kagent.enabled` | `false` |
| kagent's CRDs | `components.kagent-crds.enabled` | follows `components.kagent` |
| Agent Substrate (the actors' runtime) and its CRDs | `components.substrate.enabled`, `components.substrate-crds.enabled` | follow `components.kagent` |
| klaus-gateway | `components.klaus-gateway.enabled` | `false` |
| agent-sandbox | `components.agent-sandbox.enabled` | `false` |
| model-manager | `components.model-manager.enabled` | `false` |
| agent-manager | `components.agent-manager.enabled` | `false` |
| Backstage (the portal) | `components.backstage.enabled` | `false` |
| mcp-kubernetes | `components.mcp-kubernetes.enabled` | `false` |
| CloudNativePG operator | `components.cloudnative-pg.enabled` | `false` |
| KServe CRDs | `components.kserve-crd.enabled` | `false` |
| KServe controller | `components.kserve-resources.enabled` | `false` |
| LLMInferenceService CRDs | `components.kserve-llmisvc-crd.enabled` | `false` |
| LLMInferenceService controller | `components.kserve-llmisvc-resources.enabled` | `false` |
| the bundled Flux engine (`flux-engine` subchart) | `components.flux.enabled` | `true` |
| Model serving (KServe/vLLM runtime, presets, cache) — a feature switch, no chart | `components.modelServing.enabled` | `false` |

A component with no `enabled` key is always installed (`muster`, `dicebear`, `agent-platform-connectivity`). `components.flux` and `components.modelServing` are feature switches, not components: an entry without a `chart` renders no release, and its flag travels in the roster forwarded to connectivity like every other entry — `components.flux` is the condition of the `flux-engine` subchart (Chart.yaml `dependencies`), `components.modelServing` gates the model serving objects the connectivity chart renders, and its values block `modelServing:` travels to the connectivity release only while the switch is on (see [Turning on the standalone's extras](#turning-on-the-standalones-extras)). The `agentgateway:`, `kagent:`, `valkey:`, `klausGateway:`, `agentSandbox:`, `agent-platform-mcps:`, `model-manager:`, `agent-manager:`, `backstage:`, `mcp-kubernetes:`, `cloudnative-pg:` and `kserve-*:` blocks hold that component's values and no longer hold an `enabled` key; `make verify-meta` fails if the two ever diverge again. The last seven are the components the standalone umbrella carried on top of this roster — see [Backstage, mcp-kubernetes, CloudNativePG and KServe](#backstage-mcp-kubernetes-cloudnativepg-and-kserve).

```bash
helm template r helm/agent-platform -f helm/agent-platform/ci/ci-values.yaml                          # flux objects, wide ranges
helm template r helm/agent-platform -f helm/agent-platform/ci/ci-values.yaml -f helm/agent-platform/examples/customer-bom.yaml
make verify-meta verify-modes verify-postgres
```

## Prerequisites

- Kubernetes ≥ 1.33 on the install target. With `components.kagent.enabled`: **Kubernetes ≥ 1.35 with the `ClusterTrustBundle`, `ClusterTrustBundleProjection` and `PodCertificateRequest` feature gates on kube-apiserver, kube-controller-manager and every kubelet** — Agent Substrate itself comes with the chart (the `substrate` and `substrate-crds` components), a live render refuses a cluster that does not serve the API; see [Agent Substrate](#agent-substrate).
- Flux — **optional**. The chart brings its own engine (the Flux Operator and one `FluxInstance` running source-controller + helm-controller) where a cluster has none; a cluster that already runs Flux installs the chart through it with `components.flux.enabled: false`. See [Installing](#installing).
- Gateway API v1 CRDs (`gateways.gateway.networking.k8s.io`, `httproutes.gateway.networking.k8s.io`, `gatewayclasses.gateway.networking.k8s.io`) installed cluster-wide. The Agent Platform does **not** install them.
- No separate CRD chart to install first — every component ships its own CRDs (`AgentgatewayParameters` / `AgentgatewayPolicy` / `AgentgatewayBackend` with the agentgateway component, `MCPServer` / `Workflow` with muster, the `kagent.dev` CRDs with the `kagent-crds` component, the agent-sandbox CRDs with theirs). The meta-package orders each CR consumer after the CRD-owning component for you; see [CRD lifecycle](#crd-lifecycle).
- A `GatewayClass` CR named `agentgateway` (`status.conditions[type=Accepted]=True`). The bundled `agentgateway` sub-chart creates it on install; operators managing the controller out-of-band must ensure the `GatewayClass` exists.
- Kyverno for the four `kyverno.io` objects the connectivity chart renders — optional: `kyvernoPolicies.enabled: auto` (the default) renders them only where `kyverno.io/v1` is served. Clusters without Kyverno that enforce restricted PSS through PSA labels also set `components.agent-sandbox.enabled: false`; see [Kyverno](#kyverno) and [Cluster shape](#cluster-shape-auto).
- Cilium CNI for the `cilium` network-policy flavor — optional: `networkPolicy.flavor: auto` (the default) selects `cilium` where `cilium.io/v2` is served and `kubernetes` otherwise, and muster's flavor and the bundled valkey's Cilium policy follow. Opt out of network policies entirely with `networkPolicy.enabled: false` + `muster.networkPolicy.enabled: false` + `valkey.ciliumNetworkPolicy.enabled: false`.
- cert-manager for `components.kserve-resources` (the KServe controller's webhook certificates) — only when that component is on.
- For the standalone's extras (Backstage, mcp-kubernetes, model serving): `global.domain`, `global.identity` and a public Gateway; see [Turning on the standalone's extras](#turning-on-the-standalones-extras).

## Installing

**One install.** The `agent-platform` meta-package renders the per-component and `agent-platform-connectivity` releases as Flux `OCIRepository` + `HelmRelease` objects (each component ships its own CRDs, and a CR consumer `dependsOn` the CRD-owning component so CRDs Establish before any CR applies) — and brings the Flux engine that reconciles them where the cluster has none. `helm install` on a cluster without Flux yields a running platform.

### Quick start

Three inputs: the domain, the identity provider, the components. Nothing about Flux.

```yaml
# values.yaml
global:
  domain: platform.example.com               # muster., avatars., kagent.<domain> derive from it
  identity:                                  # the platform's one OIDC provider
    issuerUrl: https://dex.platform.example.com
    clientId: agent-platform
    existingSecret: agent-platform-idp       # dex-client-secret, registration-token, oauth-encryption-key, valkey-password
  gatewayApi:
    parentRefs:                              # the public Gateway every route attaches to
      - name: public
        namespace: gateway-system
components:
  kagent: { enabled: true }                  # pick the components you want; muster, dicebear and connectivity are always on
  agent-manager: { enabled: true }
```

```bash
# the one cluster prerequisite the chart does not bring: the Gateway API CRDs
kubectl apply -f https://github.com/kubernetes-sigs/gateway-api/releases/download/v1.5.0/standard-install.yaml
helm install agent-platform oci://gsoci.azurecr.io/charts/giantswarm/agent-platform \
  --namespace agent-platform --create-namespace -f values.yaml --wait --timeout 10m
```

`--wait` returns when the platform runs: Helm waits for the component `HelmRelease`s to be Ready (Helm 4 waits on custom resources' `Ready` condition), which on a fresh kind cluster takes about two minutes with the default components and about four minutes with kagent on (220 s measured, Substrate included). The cluster-shape knobs (Kyverno, network-policy flavor, monitors, the Envoy-only avatar route) default to `auto` and follow what the cluster serves — see [Cluster shape](#cluster-shape-auto). That `helm install` is the last Helm command besides `helm uninstall`: the release now manages itself through the engine it brought — the chart rolls forward inside its major on its own, and a values change is a rewrite of Secret `agent-platform-values`, not a `helm upgrade` (which is refused) — see [Self-management](#self-management). Component versions roll forward on their own inside their `versionRange`s either way.

**The kagent namespace.** `components.kagent.enabled: true` installs the kagent chart into `kagent.namespaceOverride` (`kagent`), a namespace its own `HelmRelease` does not create (it targets the platform namespace like every component) and that the connectivity chart renders — in a release that `dependsOn` kagent. With the bundled engine the install runs one `pre-install,pre-upgrade` hook Job, `<release>-kagent-namespace` (weight -8, as the hook ServiceAccount; see [Uninstalling](#uninstalling) for the hook family), that creates the namespace when it is missing, waits for one that is still terminating to be gone, and leaves an existing one alone — so a first install on a bare cluster converges (giantswarm/agent-platform#306). The namespace is the connectivity release's from then on: it adopts it and labels it, and keeps it on uninstall (`helm.sh/resource-policy: keep` — the agents in it survive the platform's teardown, see [Uninstalling](#uninstalling)). With the engine off nothing renders here; the cluster's own Flux is given the namespace out of band (on Giant Swarm management clusters by the bases).

### The engine

`components.flux.enabled: true` (the default) makes the `flux-engine` subchart (`helm/agent-platform/charts/flux-engine`, released with this chart) part of the release. It carries the seven Flux CRDs of source-controller and helm-controller and the four Flux Operator CRDs in its `crds/`, and renders the [Flux Operator](https://fluxoperator.dev) (web UI off, no network policy; `ghcr.io/controlplaneio-fluxcd/flux-operator`, a Renovate-managed pin) plus one `FluxInstance` named `flux` in the release namespace: `distribution.version: "2.x"`, `components: [source-controller, helm-controller]`, `cluster.multitenant: true` — the multi-tenancy lockdown, under which helm-controller impersonates the ServiceAccount a `HelmRelease` names and refuses cross-namespace references. The platform's own releases therefore run as the tenant identity `agent-platform-flux` (a ServiceAccount in the release namespace bound to `cluster-admin`, rendered by the subchart; `gitops.serviceAccountName` defaults to it whenever the engine is on). The operator keeps Flux current inside the `2.x` range — controllers, CRDs and stored objects — on its own; Helm never upgrades `crds/`, and with the operator it does not have to. The Flux manifests come embedded in the operator image, so a new Flux minor arrives with a new operator tag. The subchart's values sit under `flux-engine:` (the operator image, the distribution registry for a mirror of `ghcr.io/fluxcd`, kustomize patches for the controllers); nothing needs setting for a default installation.

The engine is a value, not detection: Helm and helm-controller evaluate a dependency's `condition` before they collect `crds/`, so `false` drops CRDs and engine together — a chart that always carried Flux CRDs would be force-applied over a cluster's own by helm-controller's default CRD policy, and `crds/` are processed before any template could look at the cluster.

### Self-management

Where the chart brings the engine, the engine also holds the chart. `gitops.self.enabled` defaults to `auto` — on with `components.flux.enabled`, off without it (`true` with the engine off fails the render) — and, on, the release renders its own `OCIRepository` and `HelmRelease`, both named after the release, in the release namespace (`templates/self/`): the `OCIRepository` follows `<gitops.self.repository>/agent-platform` (default `oci://gsoci.azurecr.io/charts/giantswarm`; `gitops.self.insecure` for a plain-HTTP lab registry) at the range `>=<installed version> <next major>.0.0`, derived from the running chart at render time so a release follows patch and minor releases of its own major and never a downgrade (`gitops.self.versionRange` overrides it; `gitops.self.semverFilter` narrows the tags to a dev channel first, see [Dev channel](#dev-channel); an offline `helm template` of a checkout derives it from `Chart.yaml`'s placeholder version, the released chart carries its tag); the `HelmRelease` names the release (`releaseName`, `targetNamespace`), runs as the tenant identity `agent-platform-flux` (`gitops.serviceAccountName` overrides it here too), reads its values from Secret `agent-platform-values` (`optional: false`) and sets `disableWait` (helm-controller must not wait on the chart's own `HelmRelease` objects — itself among them). The bundled helm-controller adopts the release the CLI installed with one extra revision — `helm history` shows the install and the adoption (`3.18.90+<oci digest>`, the digest is chart build metadata) and then stays put — and from then on rolls the chart forward inside its major without a `helm upgrade`: a new chart version in the registry is applied within `gitops.self.interval` (default `10m`), the new chart defaults take effect, your values survive. Measured on kind: install `--wait` 87 s, adoption in the same second the release went `deployed`, a pushed version applied 70 s after the push.

**The install-time bracket.** The `HelmRelease` is created *suspended* and resumed by a detached Job (`<release>-self-resume`, started by the post-install hook `<release>-self-values`) once `helm status` reports `deployed`. Without it helm-controller would reconcile the `HelmRelease` while the CLI's revision is still `pending-install`, unlock that revision (mark it `failed`) and upgrade the release with an empty values Secret — every user value reverts (measured). The same bracket covers an engine-on installation of an earlier version that `helm upgrade`s into this one: the `HelmRelease` does not exist yet, so it is rendered suspended; a controller-driven upgrade finds its own `HelmRelease` and renders no `suspend` at all. The resumer polls for up to 300 s and, if the release is still pending by then, exits 1 *without* resuming (the `HelmRelease` stays suspended; the Job stays for an hour to be read). Consequence: `helm template` renders `suspend: true` (`.Release.IsInstall` is true there and it sees no cluster), so a `helm template | kubectl apply` user gets a suspended `HelmRelease`.

**The Helm CLI is day-0 only.** `helm install` is the one Helm command a self-managed installation runs, besides `helm uninstall`. Helm writes a release revision — a Secret of type `helm.sh/release.v1`, label `name=<release>` — *before* it renders, runs hooks or applies anything; a `ValidatingAdmissionPolicy` the chart renders (`<release>-self-managed-<namespace>`, with a binding of the same name scoped to the release namespace) refuses that CREATE to everyone but the tenant ServiceAccount the bundled helm-controller impersonates (`system:serviceaccount:<namespace>:agent-platform-flux`). So `helm upgrade` and `helm rollback` fail in about a second with the reason in Helm's own error output —

```
Error: UPGRADE FAILED: create: failed to create: secrets "sh.helm.release.v1.agent-platform.v3" is forbidden:
ValidatingAdmissionPolicy 'agent-platform-self-managed-agent-platform' with binding 'agent-platform-self-managed-agent-platform'
denied request: release agent-platform manages itself through its bundled Flux: change values by rewriting Secret
agent-platform-values in namespace agent-platform (key values.yaml), or hand the release back to the Helm CLI — annotate
the namespace agent-platform.giantswarm.io/helm-cli=allow, then helm upgrade --set gitops.self.enabled=false --force-conflicts
```

— and write no revision: `helm status` stays `deployed`, `helm history` is unchanged, the self `HelmRelease` is untouched. UPDATE and DELETE are not matched, so `helm uninstall` and the install's own status updates pass. It needs **Kubernetes ≥ 1.30** (`admissionregistration.k8s.io/v1`); the render refuses an older cluster and names `gitops.self.enabled=false` as the way out. The policy takes effect about a second after the install creates it and lingers about a second after `helm uninstall` deletes it: a reinstall in the very same second is refused once, a retry passes.

**Day 2 is the Secret, not Helm.** Secret `agent-platform-values` (key `values.yaml`, in the release namespace) holds the *user-supplied* values of the release — what `helm get values` prints, never the merged tree — written by the post-install/post-upgrade hook, labelled `reconcile.fluxcd.io/watch=Enabled` so helm-controller reconciles a change at once (measured: a new revision `deployed` about 20 s after the rewrite). It holds your secrets exactly as Helm's release storage does. Rewrite it with your complete values file (server-side apply keeps the labels the hook set):

```bash
# change values
kubectl -n agent-platform create secret generic agent-platform-values \
  --from-file=values.yaml=values.yaml --dry-run=client -o yaml \
  | kubectl apply --server-side --force-conflicts -f -
# move to the next major: pin the range in the same values file, then rewrite the Secret as above
#   gitops:
#     self:
#       versionRange: ">=4.0.0 <5.0.0"
```

`--set`-style partial changes have no place here: the Secret *is* the values of the release, so a partial file reverts everything it omits to chart defaults.

**The escape hatch** hands the release back to the Helm CLI (the lab shape) without an uninstall. Two steps, because the policy refuses the CLI until the namespace says otherwise, and `--force-conflicts` once, because helm-controller co-owns the objects' fields:

```bash
kubectl annotate namespace agent-platform agent-platform.giantswarm.io/helm-cli=allow
helm upgrade agent-platform oci://gsoci.azurecr.io/charts/giantswarm/agent-platform \
  --namespace agent-platform -f values.yaml --set gitops.self.enabled=false --force-conflicts --wait
kubectl annotate namespace agent-platform agent-platform.giantswarm.io/helm-cli-   # optional tidy-up
```

The upgrade's pre-upgrade hooks (weight -6 stops a resumer, -5 suspends the self `HelmRelease` and removes the values Secret) run before Helm deletes the self `HelmRelease`, so helm-controller drops its finalizer *without* uninstalling the release; the policy goes with the render. **Keep `gitops.self.enabled: false` in the values file**: a later `helm upgrade -f values.yaml` without it re-renders the self objects and re-adopts the release (with the bracket — the `HelmRelease` is absent, so it comes back suspended). The annotation alone does not switch anything: the render refuses `gitops.self.enabled` on while the namespace carries it (two writers on one release), under the CLI and — for the duration of a hand-back — on the self `HelmRelease`.

**Traps.** A `helm install --wait` (or an upgrade into this version) that fails leaves the self `HelmRelease` suspended and the policy in force, so the CLI cannot retry with `helm upgrade`: either `helm uninstall --wait` and install again, or finish the bracket by hand — write the Secret as above and `kubectl -n agent-platform patch helmrelease agent-platform --type merge -p '{"spec":{"suspend":false}}'` — and helm-controller takes it from there. Never `helm rollback` a self-managed release: with the annotation in place it would apply an older manifest without the self `HelmRelease`, and Helm's deletion of the *unsuspended* `HelmRelease` uninstalls the release. The lab shape — agentlab and this repository's own kind smoke — installs *unreleased* charts with `gitops.self.enabled: false`: a self `HelmRelease` following the published range would replace the chart under test with the published one; the -6/-5 hooks still render there as no-ops (`make verify-self`, `tests/verify-self.py`).

### Clusters that run Flux

A cluster that runs its own Flux — every Giant Swarm management cluster — sets `components.flux.enabled: false` and installs the chart through that Flux. Nothing of the engine reaches the cluster: no CRD, no operator, no `FluxInstance`, no hook, no tenant identity, and the platform `HelmRelease`s carry no `serviceAccountName` unless `gitops.serviceAccountName` says so. Self-management is off with the engine (`gitops.self.enabled: auto`) — that Flux holds the chart's `HelmRelease`, as below. The render is the pure app-of-apps render, byte for byte what it was before the engine existed.

```yaml
apiVersion: source.toolkit.fluxcd.io/v1
kind: OCIRepository
metadata:
  name: agent-platform
  namespace: flux-giantswarm
spec:
  interval: 1h
  url: oci://gsoci.azurecr.io/charts/giantswarm/agent-platform
  ref:
    semver: ">=1.0.0"   # pin a tag for a customer release
---
apiVersion: helm.toolkit.fluxcd.io/v2
kind: HelmRelease
metadata:
  name: agent-platform
  namespace: flux-giantswarm
spec:
  interval: 10m
  timeout: 12m          # helm-controller's 5m default is too short for the first 4.x reconcile — see below
  chartRef: { kind: OCIRepository, name: agent-platform }
  install:
    createNamespace: true
  values:
    components:
      flux:
        enabled: false        # this cluster runs Flux
    gitops:
      namespace: flux-giantswarm       # a namespace exempt from the tenancy policy holds the Flux CRs
      targetNamespace: agent-platform  # the workloads land here
  valuesFrom:
    - kind: Secret
      name: agent-platform-values
```

**Set `spec.timeout` to `12m` or more on that `HelmRelease`.** helm-controller's install is the Helm SDK's `--wait`: it returns when every component `HelmRelease` the chart renders is Ready, and the reconcile fails on `spec.timeout` first — whose default is 5 minutes. With kagent on, the first 4.x reconcile installs nine component `HelmRelease`s plus Substrate and kagent in dependency order (the CRD charts and muster, then connectivity, then Substrate's control plane and its worker, then the kagent controller and the managers) — about 5–6 minutes through a cluster's own Flux on the CI kind cluster: `helm install --wait` takes 220 s for the components with cold image pulls, and the platform reached Ready through Flux in 289 s in the green run. A `HelmRelease` that keeps the default fails its first 4.x reconcile on the timeout and is retried by helm-controller. Measured by the ATS own-Flux scenario, [`tests/ats/test_own_flux.py`](tests/ats/test_own_flux.py), which sets `spec.timeout: 12m` (giantswarm/agent-platform#380: CircleCI job 5941 hit the 5-minute default, job 6033 passed with `12m`; the timings are in [tests/ats/README.md](tests/ats/README.md)). Giant Swarm's management-cluster base sets `20m` on its `HelmRelease`. The uninstall through that Flux — the `HelmRelease` deleted, the component releases removed in reverse-dependency waves — takes about 30–105 s (see [Uninstalling](#uninstalling)).

With the value left at `true` on such a cluster the render fails — under the Helm CLI, `helm upgrade --dry-run=server` and helm-controller alike — with `this cluster runs Flux; set components.flux.enabled=false or install the chart through it`: the guard looks the cluster up for a helm-controller `Deployment` (`app.kubernetes.io/component=helm-controller`) or a `FluxInstance` outside the release namespace. It stops a second, locked-down helm-controller from reconciling every `HelmRelease` in the cluster as the default account, which is what an engine next to a cluster's Flux does (measured). `helm template` sees no cluster and renders the engine; the guard needs a live API. One thing the guard cannot stop under helm-controller: it applies a chart's `crds/` *before* it renders the templates, so a `HelmRelease` whose value is flipped to `true` on such a cluster server-side-applies the vendored Flux CRDs — the same Flux version as the cluster's own, so their content is unchanged and only the field managers gain `helm-controller` (the ATS asserts both) — before the render fails; `upgrade.crds: Skip` on the `HelmRelease` that installs this chart avoids even that. The Helm CLI never touches `crds/` on upgrade. Two more guards: `gitops.namespace` cannot be combined with the bundled engine (the tenant identity lives in the release namespace), and `components.flux.enabled=false` is refused on an installation that runs the engine (an upgrade would delete the operator together with the `FluxInstance` it finalizes and hang — uninstall instead).

**Uninstalling through that Flux.** Delete the agents' `HelmRelease`s first, while the kagent controller still runs (an `AgentTemplate`'s cleanup on Substrate needs it), then the chart's `HelmRelease`: helm-controller uninstalls the meta release, which deletes every platform `HelmRelease` it rendered in one pass, and finalizes them **all at once, concurrently** — helm-controller has no reverse-`dependsOn` uninstall (fluxcd/flux2#1744), and the chart never touches the cluster's Flux, so nothing orders them. That is safe because the CRD charts keep their CRDs: `kagent-crds` and `substrate-crds` render them as templates carrying `helm.sh/resource-policy: keep` (the kagent line from giantswarm/kagent-upstream#10, the Substrate line from `v0.0.27-gs.4`, giantswarm/agent-platform#385), so a release whose objects are their CRs can always delete them, whatever order the releases go in — before the Substrate line carried the policy, `substrate-crds` could go before `substrate`, whose uninstall then failed for good on its `SandboxConfig` (`failed to delete release: substrate`: Helm cannot delete an object whose kind is gone) and was retried until the meta `HelmRelease`'s finalizer timed out (measured on the ATS own-Flux scenario). What stays: the eight kept CRDs (`kagent.dev`, `ate.dev`), the kagent namespace with the agents' `AgentTemplate`s and `RemoteMCPServer`s, and Substrate's leftovers in `ate-system` (above); no `SandboxConfig` or `WorkerPool` outlives the release that owned it, and one created by hand survives until its owner deletes it. To remove the CRDs as well, `kubectl delete crd` them explicitly — with the objects behind them. On an installation whose Substrate line predates `v0.0.27-gs.4` (the range's floor names it), delete the component `HelmRelease`s in reverse dependency order by hand before the chart's — `kagent`, then `substrate`, then the CRD charts — or the `substrate` release's uninstall fails as described. The ATS own-Flux scenario's way back asserts the concurrent uninstall on every release (`tests/ats/test_own_flux.py`).

### Uninstalling

```bash
helm uninstall agent-platform --namespace agent-platform --wait --timeout 5m
```

Uninstalling the platform uninstalls its engine and everything the engine held, in order. Helm deletes a release's objects in one pass, so on its own `helm uninstall --wait` would remove the operator together with the `FluxInstance` whose finalizer it processes and hang for its timeout (measured). The chart therefore ships pre-delete hook Jobs — restricted pods running one plain `kubectl` command in `registry.k8s.io/kubectl` (`gitops.hooks.image`) or a small shell script in `alpine/k8s` (`gitops.hooks.helmImage`), as a hook ServiceAccount bound to `cluster-admin` at weight -10 — that stop a self-management resumer still running (weight -6) and suspend the chart's own `HelmRelease` so it is deleted without uninstalling the release and cannot re-create the platform `HelmRelease`s mid-teardown, removing the hook-written values Secret with it (weight -5; these two run in `alpine/k8s`, `gitops.hooks.helmImage`, as the namespaced identity `<release>-self`, and tolerate a missing `HelmRelease`), delete the platform `HelmRelease`s by name in **reverse dependency order** and wait (weight 0; waves computed from the roster's `dependsOn`, each wave's releases uninstalling concurrently, the next wave starting once helm-controller has removed the last of them — so a CRD chart's release, `kagent-crds` or `substrate-crds`, goes only after every release whose objects are its CRs: a Helm uninstall cannot delete an object whose kind is already gone, and helm-controller would retry it until the hook times out), then delete the `FluxInstance` and wait (weight 5). The operator then uninstalls Flux **including the Flux CRDs**, and every `HelmRelease` object in the cluster goes with them: the agents' too, while their `AgentTemplate` and `RemoteMCPServer` objects and Helm storage stay behind, orphaned — the kagent namespace is kept (`helm.sh/resource-policy: keep` on the connectivity release's `Namespace`) and so are the kagent CRDs (the `kagent-crds` release is uninstalled with the others; the line's CRD templates carry `keep`), so a reinstall finds the agents where it left them; the three `ate.dev` CRDs are kept the same way (the Substrate line's templates carry `keep` from `v0.0.27-gs.4` on), while their objects go with the releases that own them — the `substrate` release's `SandboxConfig`, the kagent release's `WorkerPool`. What Substrate leaves behind is different: the `ate-system` pools and the bundled Postgres's claim survive, the `podcertificate-controller-system` namespace and its two CA pools go with the `substrate` release while the signers' `ClusterTrustBundle`s stay — a reinstall then fails its TLS handshakes until those bundles are removed (giantswarm/agent-platform#384; the ATS smoke removes them between its scenarios). The uninstall returns in about 31 s on the CI kind cluster with kagent, Substrate, agent-manager and two agents on (the five waves; 12–16 s without kagent); the four `fluxcd.controlplane.io` CRDs remain (Helm never deletes `crds/`), and a reinstall is clean. "Keep my agents running" is not something an uninstall can offer — the way to keep agents is not to uninstall. A failed hook aborts the uninstall before anything is deleted (the release is left `uninstalling`, the platform untouched); `helm uninstall --no-hooks` skips the ordering and is only safe once the `FluxInstance` is already gone. With the engine off (a cluster's own Flux) none of this applies: that Flux finalizes the platform `HelmRelease`s, the chart renders no hook and never touches it — see "Clusters that run Flux" for the uninstall there.

### Helm versions

Measured for this release on kind (kindest/node v1.36): **Helm 4.2** installs the chart with `--wait` honoured on the Flux custom resources (the command returns when the component `HelmRelease`s are Ready), upgrades it, and uninstalls it through the ordered teardown. **Helm 3.17** renders the templates identically — CI renders every assertion with 3.17.3 — but a Helm 3 install of this release is not measured: Helm 3's `--wait` does not wait on custom resources, so `helm install` would return before the components are Ready. Installing through a cluster's own Flux (helm-controller ≥ 1.5 runs the Helm 4 SDK) is the fleet's path and is verified on every release.

### Raw Helm without the engine

For a controller-free install with no Flux at all, drive the components directly from a pinned bill-of-materials — install each component chart (which ships its own CRDs) then `agent-platform-connectivity`, at the exact versions in [`examples/customer-bom.yaml`](helm/agent-platform/examples/customer-bom.yaml):

```bash
# Each component chart ships its own CRDs in crds/ (app-owned CRDs). Helm applies
# a chart's crds/ before its templates, so installing the component installs its
# CRDs. Install the components whose CRDs the wiring references first:
helm install muster      oci://gsoci.azurecr.io/charts/giantswarm/muster      --version <muster-version>      --namespace muster --create-namespace
helm install agentgateway oci://gsoci.azurecr.io/charts/giantswarm/agentgateway --version <agentgateway-version> --namespace muster
# kagent, agent-sandbox, … as needed, then the consumer-side wiring:
helm install agent-platform-connectivity \
  oci://gsoci.azurecr.io/charts/giantswarm/agent-platform-connectivity \
  --version <connectivity-version> --namespace muster -f values.yaml
```

> Helm's `crds/` directory is install-only: `helm upgrade` never re-applies or upgrades CRDs from `crds/`. The meta-package solves this for the component CRDs by setting `crds: CreateReplace` on each app-owned component's `HelmRelease`, and for the Flux CRDs by letting the operator own them. For a raw-Helm install of the components you must apply CRD schema changes out of band (`kubectl apply`/`replace`) on a component upgrade.

## Configuration

| Key | Default | Purpose |
|---|---|---|
| `global.registry` | `gsoci.azurecr.io` | Default container image registry. |
| `ingress.mode` | `muster-direct` | Request topology selector. `muster-direct` (client → muster, no agentgateway data plane), `agentgateway-muster` (client → agentgateway `/mcp` → muster), or `agentgateway-direct` (client → agentgateway `/mcp` → servers; **not yet supported**). See [Ingress topology](#ingress-topology). |
| `ingress.parentRefs` | `[]` | **All modes (required).** The public `Gateway`(s) both rendered routes attach to (typically `envoy-gateway-system/giantswarm-default`). Render fails if empty — the muster `/` route always attaches to it. |
| `ingress.hostnames` | `[]` | **All modes.** muster's public hostname(s); must match the OAuth callback URL. |
| `ingress.backendTrafficPolicy.enabled` | `false` | Render route-scoped `BackendTrafficPolicy` objects (preserve `WWW-Authenticate`, set `requestTimeout: 0s`): one over muster's `/` route in **all** modes, plus one over the agentgateway `/mcp` route in `agentgateway-*` modes. |
| `components.agentgateway.enabled` | `false` | Install the agentgateway controller component. Must be `true` in `agentgateway-*` modes. |
| `gateway.name` | `agentgateway` | `agentgateway-*` modes only. Data-plane `Gateway` resource name. |
| `gateway.gatewayClassName` | `agentgateway` | `agentgateway-*` modes only. The `GatewayClass` the data plane attaches to. |
| `gateway.listeners` | `[{name: http, port: 8080, protocol: HTTP}]` | `agentgateway-*` modes only. Listener spec passed verbatim. |
| `gateway.parameters.serviceType` | `ClusterIP` | `agentgateway-*` modes only. Data-plane Service type (overrides controller-hardcoded LoadBalancer via `AgentgatewayParameters.spec.service`). |
| `gateway.parameters.{pod,container}SecurityContext` | restricted-PSS compatible | `agentgateway-*` modes only. Strategic-merge overlay on the data-plane Deployment. |
| `networkPolicy.enabled` | `true` | Master switch for the umbrella's network policies. |
| `networkPolicy.flavor` | `auto` | `auto` → `cilium` where `cilium.io/v2` is served, else `kubernetes`; `cilium` → CiliumNetworkPolicy; `kubernetes` → vanilla NetworkPolicy (best-effort, no entity selectors / FQDN egress). muster and valkey follow the resolved flavor. See [Cluster shape](#cluster-shape-auto). |
| `kyvernoPolicies.enabled` | `auto` | `auto` → the four `kyverno.io` objects render where `kyverno.io/v1` is served; `true` / `false` force them. See [Kyverno](#kyverno). |
| `global.observability.metrics.serviceMonitor.enabled` | `auto` | `auto` → ServiceMonitors / PodMonitors (and, derived, muster's PrometheusRule and dashboard, kagent's OTel exporters) render where `monitoring.coreos.com/v1` is served; `true` / `false` force them. See [Observability](#observability). |
| `extraObjects` | `[]` | Arbitrary manifests rendered through `tpl` alongside the chart. |
| `muster.*` | passes through to muster | See [muster chart README](https://github.com/giantswarm/muster/blob/main/helm/muster/README.md). |
| `agentgateway.*` | passes through to upstream agentgateway | See [agentgateway docs](https://agentgateway.dev). |
| `components.valkey.enabled` | `true` | Bundle [giantswarm/valkey-app](https://github.com/giantswarm/valkey-app) for muster OAuth session storage. |
| `components.agent-platform-mcps.enabled` | `false` | Bundle [giantswarm/agent-platform-mcps](https://github.com/giantswarm/agent-platform-mcps) to render the platform's MCP server CRs. See [Bundled MCP servers](#bundled-mcp-servers). |
| `components.kagent.enabled` | `false` | Install the kagent line (kagent API v2): the `kagent` and `kagent-crds` components. See [The kagent line](#the-kagent-line). |
| `components.klaus-gateway.enabled` | `false` | Install [klaus-gateway](https://github.com/giantswarm/klaus-gateway), the channel front door. |
| `agent-platform-mcps.mcpServers` | `[]` | Abstract list of MCP servers rendered into `MCPServer` / `AgentgatewayBackend` CRs. Renders nothing until populated. |
| `components.agent-sandbox.enabled` | `false` | Bundle the [agent-sandbox](https://github.com/kubernetes-sigs/agent-sandbox) controller — the Sandbox runtime kagent's `SandboxAgent` requires. See [Agent sandbox](#agent-sandbox). |
| `components.model-manager.enabled` | `false` | Install [model-manager](https://github.com/giantswarm/model-manager), model inventory / pull / load / unload / delete and kagent `ModelConfig` wiring on an Ollama, Lemonade Server (FastFlowLM on AMD NPUs) or KServe backend, as REST and MCP (`x_model-manager_*` through muster). See [Model manager and agent manager](#model-manager-and-agent-manager). |
| `components.agent-manager.enabled` | `false` | Install [agent-manager](https://github.com/giantswarm/agent-manager), the agent write surface: create / update / delete / inspect kagent agents as Flux `HelmRelease`s of the agent chart, as REST and MCP (`x_agent-manager_*` through muster). Needs kagent and Flux. See [Model manager and agent manager](#model-manager-and-agent-manager). |
| `modelManager.route.enabled`, `agentManager.route.enabled` | `false` | Expose the service's REST API on the agentgateway data plane at `https://agentgateway.<domain>/model-manager` / `/agent-manager` (the portal's path), with an optional JWT policy in front (`…route.jwtAuthentication`). `agentgateway-*` modes only. |
| `kagent.controllerRoute.enabled` | `false` | Expose the kagent controller's gRPC API (kagent API v2 `kagent.api.v1alpha1.*` and A2A v1 `lf.a2a.v1.A2AService`) through the agentgateway data plane as a `GRPCRoute` on `https://agentgateway.<domain>` and, in-cluster, `grpc://agentgateway.<namespace>.svc.cluster.local:8080` — one service-only match per service (`kagent.controllerRoute.grpc.services`; RPC lists as the fallback for an agentgateway chart older than 2.1.1), no path prefix, native gRPC over HTTP/2 end to end, gRPC-Web on the same route. The JWT policy (`kagent.controllerRoute.jwtAuthentication`) is **on by default**: `Strict` against `global.identity.issuerUrl`, the identity header `x-user-id` set from the verified `email` claim and any inbound copy replaced; the UI route strips the header; the controller admits agentgateway and the UI only. See [Authentication flow](docs/authentication.md#5-the-kagent-controller-route). `agentgateway-*` modes only; needs `gateway.jwksEgress`. |
| `dicebear.route.parentRefs` | `[]` | **Required.** The public `Gateway`(s) the avatar `HTTPRoute` attaches to — typically the same as `ingress.parentRefs`. The [dicebear](https://github.com/giantswarm/dicebear) avatar renderer is a force-enabled component (deployed on every install) and its public route is on by default, so the child release fails to render if this is empty. See [giantswarm/giantswarm#37211](https://github.com/giantswarm/giantswarm/issues/37211). |
| `dicebear.route.hostnames` | `[]` | **Required.** Hostname(s) the avatar endpoint is served on, e.g. `avatars.<domain>`. Consumers fetch `https://<hostname>/v1/<agent-name>.png`. |
| `muster.muster.oauth.server.enabled` | `true` | OAuth resource-server protection on the muster API. Requires `baseUrl`, `dex.{issuerUrl,clientId}`, and a Secret carrying `dex-client-secret` / `registration-token` / `oauth-encryption-key` / `valkey-password`. |
| `muster.muster.oauth.server.storage.type` | `valkey` | Muster storage backend default. Pairs with `components.valkey.enabled: true`; flip to `memory` for dev. |
| `muster.muster.oauth.server.storage.valkey.url` | `muster-valkey:6379` | Bundled-valkey Service. Override to point at an out-of-band Valkey. |

Full schema: [`helm/agent-platform/values.schema.json`](./helm/agent-platform/values.schema.json).

### Model manager and agent manager

Two platform services, off by default, each a component with the same shape: a component release (`components.model-manager` / `components.agent-manager`, chart values in the `model-manager:` / `agent-manager:` blocks, forwarded verbatim) plus the wiring the connectivity chart renders for it from the `modelManager:` / `agentManager:` blocks — the agentgateway route and JWT policy (`route.*`, a path-prefixed `HTTPRoute` with an optional JWT policy — the kagent controller's `kagent.controllerRoute` is the `GRPCRoute` form with the policy on by default), the network policies in both flavors (ingress from the data plane, muster and the kubelet's probes; egress to DNS, the Kubernetes API, the identity provider and the service's own backends), and render-time guards that fail the install with an actionable message instead of shipping a service that cannot work.

- **[model-manager](https://github.com/giantswarm/model-manager)** manages models for the agents: inventory, pull, load/unload, delete, and the kagent `ModelConfig` wiring. `model-manager.backend` selects the serving driver — `ollama` proxies an Ollama reached at `model-manager.ollama.endpoint` (required, as reached from pods), `lemonade` proxies a [Lemonade Server](https://lemonade-server.ai) — FastFlowLM on AMD Ryzen AI NPUs, llama.cpp on GPU/CPU — reached at `model-manager.lemonade.endpoint` (required likewise; Lemonade listens on 13305 by default; its models are wired with kagent's `OpenAI` provider against `<endpoint>/api/v1`), `lmstudio` proxies an [LM Studio](https://lmstudio.ai) — llama.cpp on GPU/CPU, MLX on Apple silicon — reached at `model-manager.lmstudio.endpoint` (required likewise; LM Studio listens on 1234 by default and needs 0.4.0 or newer; its models are wired with kagent's `OpenAI` provider against `<endpoint>/v1`, and it serves no delete, so that call answers `501 unsupported`), `kserve` manages `InferenceService`s on a KServe the cluster already has (this meta-package does not bundle it; the render fails while the `serving.kserve.io` API is missing unless `modelManager.kserve.requireApi: false`). ModelConfigs land in `model-manager.kagent.namespace`, which must be the kagent component's namespace; without kagent set `model-manager.kagent.disableWiring: true`.
- **[agent-manager](https://github.com/giantswarm/agent-manager)** is the agent write surface: it composes a kagent agent the way the portal's create flow does — a Flux `HelmRelease` of the [agent chart](https://github.com/giantswarm/agent) plus the shared per-namespace `OCIRepository` — validates the values against the chart's `values.schema.json` before applying, and reads agents back from the `Agent`, its `HelmRelease` and the workload. It needs the kagent component and Flux's helm and source controllers; the HelmReleases it writes execute as the `kagent-flux` tenant identity the connectivity chart renders (`kagent.fluxServiceAccountName`, see [Tenant identity](#tenant-identity)). An agent whose `HelmRelease` a GitOps Kustomization owns is reported as `managed: gitops` and refused for update/delete unless forced.

Both register their MCP endpoint with muster through the chart's own `MCPServer` CR (tools appear as `x_model-manager_*` / `x_agent-manager_*`), and both **act as the user, not as a ServiceAccount**: `oauth.enabled` makes the service an OAuth 2.1 resource server in front of its MCP endpoint and REST API, muster forwards the session's IdP id_token (the CR's `auth.forwardToken`) and requests `requiredAudiences` at login — the cross-client audience the kube-apiserver trusts (`dex-k8s-authenticator` on Giant Swarm clusters) — so `oauth.downstream` presents the same token to the Kubernetes API and the user's RBAC governs; the ServiceAccount holds no permissions (the charts render no RBAC). The issuer, client, secret and base URL fall back to `global.identity` / `global.domain` inside the charts; an installation that sets no `global.*` names them in the block (`oauth.baseURL`, `oauth.dex.issuerURL`, `oauth.dex.clientID`, `oauth.existingSecret` with the key `dex-client-secret` — the same Dex client muster logs users in with — and that client in `oauth.trustedAudiences`). The guards fail the render when any of them is missing. The cilium network policies open the service's egress to that provider by name: the Dex issuer host, or for `oauth.provider: google` the three Google hosts its discovery, JWKS/userinfo and token endpoints live on (`accounts.google.com`, `www.googleapis.com`, `oauth2.googleapis.com`); further destinations go in `modelManager.networkPolicy.egress` / `agentManager.networkPolicy.egress`.

`make verify-managers` covers the wiring, both flavors and every guard.

### The kagent line

`components.kagent.enabled: true` installs **kagent API v2** — `kagent.dev/v1alpha3`: `AgentTemplate`, `Harness`, `ModelConfig`, `ModelProviderConfig`, `RemoteMCPServer`; agents run as [Agent Substrate](https://github.com/kagent-dev/substrate) actors in gVisor worker pods — from the kagent line Giant Swarm maintains: [giantswarm/kagent-upstream](https://github.com/giantswarm/kagent-upstream), branch `giantswarm`, upstream kagent `main` at a pinned commit plus the carried patches; its `FORK.md` is the ledger of pin, patches and published artifacts. The line publishes two charts, `kagent` and `kagent-crds`, at `oci://ghcr.io/giantswarm/kagent/helm` and the multi-arch images `ghcr.io/giantswarm/kagent/{controller,ui,golang-adk}` — as releases `vX.Y.Z-gs.N` (`X.Y.Z` the upstream version the pin anticipates, `N` the line's counter) and, per push to the consumed branch, as dev builds. Two roster entries: `components.kagent` and `components.kagent-crds`, the CRDs as their own chart in the kserve-crd shape (templates carrying `helm.sh/resource-policy: keep`, no `crds:` policy). `kagent-crds` follows `components.kagent` unless switched explicitly, the kagent release and the connectivity release `dependsOn` it, and the render refuses kagent on without it.

**One build of the line.** Three values name it and move together: `components.kagent.versionRange` = `components.kagent-crds.versionRange` (`>=0.11.0-gs.1 <0.11.1-0`, the line's releases of upstream 0.11.0: Flux's Masterminds semver compares prerelease identifiers left to right, so the line's `0.11.0-dev.giantswarm.…` builds fall below the `gs.1` floor and a later `-gs.N` is admitted; the `-0` on the ceiling lets prereleases through at all, and the ceiling holds the patch because a prerelease is confined to no patch tuple; a bare `0.11.0` would be admitted too, should upstream's release ever appear at this OCI path — by design the switch to an upstream release is a range change), `kagent.tag` (the controller and UI image tag, equal to the chart version of the same build — upstream's chart would fall back to `.Chart.Version`, which under helm-controller is `<version>+<oci digest>`, an invalid tag) and `kagent.harness.image` (the Go ADK image **by digest**, from which the connectivity chart renders the platform `Harness`; the CRD accepts nothing else).

**Re-pinning the kagent line.** A new release of the line is a values change here, not a Flux roll: take the release's row from [`builds.md`](https://github.com/giantswarm/kagent-upstream/blob/ledger/builds.md) on the fork's `ledger` branch (one row per published build, every image's index digest), set `kagent.tag` to its version, `kagent.harness.image` to its `golang-adk` digest and — when the upstream base version moved — the two `versionRange`s to the new base. `make verify-meta` asserts the three agree (tag and range on one upstream version, the Harness image a digest) and that the range admits only the line's releases of that version; `make verify-components-charts` renders the forwarded `kagent` and `kagent-crds` blocks against the charts the ranges resolve to. The customer BOM pins `kagent` and `kagent-crds` to one release.

**What the chart forwards.** The `kagent:` block is upstream's chart shape (keys at the root): `registry: ghcr.io`, the fork's image names, `controller.substrate.*` (the controller talks to Substrate's `ate-api` and routes through `atenet` in `ate-system`), `substrateWorkerPool` (the `WorkerPool` `kagent-default` the platform Harness runs on: `replicas` bound the concurrently active agents, `template` carries resources and node placement, `workerImage` is the gVisor worker of the Substrate version the cluster runs), pgvector on the bundled Postgres (the line's memory API needs it; with the platform CNPG cluster set `postgres.vector.enabled`), `kmcp` / `kagent-tools` / `grafana-mcp` off. The connectivity chart's keys — `controllerRoute`, `uiRoute`, `harness`, `modelConfigs`, `remoteMcpServers`, `serviceMonitor`, `oauth2ProxyIngress`, `fluxServiceAccountName` — stay in the block and are dropped on the way to the kagent release (`components.kagent.omitKeys`): the kagent chart has no schema and would carry them on silently. `kagent-crds:` keeps the chart's `kmcp` and `substrate` CRD subcharts off — muster fills kmcp's role, Substrate's CRDs come with the `substrate-crds` component.

**Prerequisites.** Kubernetes 1.35 with the three feature gates and Agent Substrate — the runtime the chart ships next to kagent; [Agent Substrate](#agent-substrate) has the details. Upgrading a 3.x installation: [UPGRADE.md](./UPGRADE.md).

### Agent Substrate

kagent API v2 has no runtime without [Agent Substrate](https://github.com/kagent-dev/substrate): every agent runs as a Substrate *actor* in a gVisor *worker* pod, a `Harness` names a `WorkerPool` and a snapshot location, and the kagent controller drives it all through Substrate's `ate-api`. The chart ships Substrate as two components in the `kagent-crds` shape — `components.substrate-crds` (the three `ate.dev` CRDs as templates) and `components.substrate` (the control plane in `ate-system`: `ate-api-server`, `ate-controller`, the `atenet` router, egress gateway and dns, the per-node `atelet` DaemonSet; the `podcertificate-controller` in `podcertificate-controller-system`) — from the [Giant Swarm Substrate line](https://github.com/giantswarm/substrate), branch `giantswarm`: the upstream release kagent's `go.mod` pins plus the carried patches; its `FORK.md` is the ledger. Both follow `components.kagent` unless switched explicitly, and the render refuses kagent on without them. Both land in `ate-system` (`components.<name>.targetNamespace` — the chart's Roles and Service names are fixed to it), and both carry **one version pin**: the same range in the two `versionRange`s (`>=0.0.27-gs.4 <0.0.28-0`, the kagent entry's shape — the line's releases of upstream 0.0.27 from `v0.0.27-gs.4` on: the egress gateway's frontend policy for its agentgateway dataplane, the connection string from a Secret, atelet scheduling, `helm.sh/resource-policy: keep` on the three CRD templates, so no concurrent uninstall fails on a vanished kind — giantswarm/agent-platform#385 — and, from `v0.0.27-gs.4`, a golden boot whose image the registry refuses failing with the reason instead of pending forever — giantswarm/substrate#14), whose floor is the version `kagent.substrateWorkerPool.workerImage` names — the control plane and the workers are one Substrate version. A re-pin of Substrate moves the three together, with the kagent pin (`make verify-components` holds them to one).

**Why roster components and not the kagent charts' `substrate` subchart switches:** each Substrate release has its own `HelmRelease` status on the installation, the order is explicit (`substrate-crds` → `agent-platform-connectivity` → `substrate` → `kagent`), the repository is a value (`components.substrate.repository`, a mirror for a proxied installation, with `substrate.image.registry` for the images), and Substrate's namespace is not kagent's.

**Bootstrap.** The substrate chart mounts material it does not render: the CA pools `service-dns-ca-pool` and `pod-identity-ca-pool` the podcertificate-controller signs from, the JWT authority pool `actor-id-jwt-pool` and the CA pool `actor-id-ca-pool` ate-api-server mints actor identities from, the trust anchor `actor-id-ca-certs` derived from the latter, and the ConfigMap `ate-api-authentication` naming the apiserver's ServiceAccount issuer — verbatim as the apiserver's OpenID discovery document spells it (a kubeadm cluster issues `https://kubernetes.default.svc.cluster.local`; a CAPA cluster with IRSA its public issuer; ate-api-server compares the token's `iss` literally). Upstream does this with four `kubectl-ate admin` invocations and a shell step; here the connectivity release's `pre-install,pre-upgrade` hook Job `<release>-substrate-bootstrap` mints the same key material (`openssl` in an init container: an ED25519 root, 365 days, `IsCA` + `keyCertSign` per CA pool; an ECDSA P-256 ES256 authority — written in the pools' wire format) and creates what is missing. A pool that exists is **never touched** — a regenerated root would orphan every certificate and token issued from it while the pods holding them keep running — so every upgrade re-runs the hook as a no-op that logs `present`; the trust anchor follows the pool; the authentication ConfigMap is kept and its issuer compared. The hook runs as `<release>-hooks` (a ClusterRole on secrets, configmaps and namespaces — get, list, watch, create, patch; it creates the two namespaces bare when missing, the substrate release adopts them) for its lifetime only. Because Substrate's pods start against what the hook mints, and kagent's against the CNPG connection Secret the same release derives, both `dependsOn` the connectivity release and install with a real readiness wait: the install and the 3.x → 4.0 upgrade order is `substrate-crds`, `kagent-crds` → `agent-platform-connectivity` → `substrate` → `kagent` → the managers.

**The control-plane database.** `substrate.postgres.enabled` is `auto`: with the platform's CNPG Cluster on (`postgres.enabled`) Substrate's database is the CNPG `Database` `postgres.databases.substrate` on that Cluster (spec.name `substrate`, owned by the application role, `retain`), ate-api-server reads its DSN from the derived Secret `<postgres.clusterName>-substrate-app` in `ate-system` (the meta chart derives `substrate.postgres.connectionStringSecretRef` from those values; the connectivity release's `post-install,post-upgrade` hook `<release>-postgres-databases` waits for CNPG's `<clusterName>-app` and copies it with `dbname`, `uri`, `jdbc-uri` and `pgpass` rewritten — CNPG mints that Secret for the initdb database only, a `Database` mints none), and the chart's bundled single-instance StatefulSet stays off; without the Cluster the bundled StatefulSet runs (a lab, a quick start), as kagent's own bundled Postgres does. `true` forces the bundled one, `false` refuses it, `substrate.postgres.connectionString` names an external database; a Substrate with none of the three fails the render. `postgres.databases` is a map (one `Database` + one derived Secret per entry, copied into every `secretNamespaces` entry), so an installation adds its own databases next to the shipped ones and the kagent database of the cut-over joins the same map.

**The snapshot store.** `kagent.harness.snapshotLocation` — the platform Harness's `snapshotPolicy.location`, an object-store URL such as `s3://<bucket>/<prefix>` — is **required whenever kagent is on** and has no default: the installation names its store. On CAPA that is an S3 bucket the installation provisions (its infrastructure, not rendered here) reached through the standard AWS credential chain of `atelet` and `ate-api-server` (IRSA — annotate their ServiceAccounts through the substrate chart's values once the bucket's role exists); an S3-compatible store elsewhere takes its endpoint and credentials in `substrate.atelet.extraEnv` (`AWS_ENDPOINT_URL`, `AWS_S3_USE_PATH_STYLE`, `AWS_REGION`, the keys from a Secret) — the substrate chart has no such knob for ate-api-server yet, so a store outside AWS S3 waits for it; `substrate.atelet.storageBackend` selects `s3` or `gcs`. A lab runs the chart's bundled in-cluster store (`substrate.rustfs.enabled: true`, `s3://ate-snapshots/<prefix>`).

**Scheduling.** `atelet` runs on every node the DaemonSet controller schedules to — on a cluster whose control-plane nodes carry the `node-role.kubernetes.io/control-plane` taint, the worker nodes; `substrate.atelet.{nodeSelector,tolerations,affinity}` pin it to a node pool or admit a tainted one. The WorkerPool is pinned to **one CPU architecture** (`kagent.substrateWorkerPool.template.nodeSelector` `kubernetes.io/arch: amd64`): an actor's golden snapshot is architecture-specific and a mixed pool wedges the actors that land on the other one. Its replicas and the rest of its pod template are the capacity knobs.

**Kyverno.** On a cluster that enforces the restricted Pod Security Standard through Kyverno, the connectivity chart ships one `PolicyException` per Substrate workload — `substrate-atelet` (privileged, hostPorts 8085/9090, hostPath mounts), `substrate-workers` (the worker pods of every WorkerPool: root, the gVisor capability set, AppArmor and seccomp `Unconfined`, a hostPath with `HostToContainer` propagation), `substrate-control-plane` (the Deployments in `ate-system` declare no securityContext), `substrate-podcertificate-controller` — each naming exactly the rules its pod spec violates, with their `autogen-` copies; `kyvernoPolicies.rules` maps each rule to the cluster's ClusterPolicy (the upstream kyverno-policies names by default). `make verify-kyverno` computes the violations from the rendered pod specs and holds the exceptions to them. [docs/substrate-security.md](./docs/substrate-security.md) is the write-up for installation owners and security reviewers.

**Network policies** (`networkPolicy.flavor`, both flavours): Substrate's hops — `atelet` ↔ `ate-api`, the kagent controller → `ate-api` and the `atenet` router, the router → a worker's tunnel on 443, `ate-api` → `atelet`'s hostPort 8085 (the egress rule upstream lacks), the workers → the egress gateway and the dns — and the actors' destinations on the **egress gateway** (`atenet-egress`), where an actor's outbound connections leave: muster, the kagent controller, the LLM provider (or the agentgateway LLM listener), DNS. The worker pods reach only the egress gateway, the dns and the cluster DNS. The kubernetes flavour renders the ingress policies. `make verify-kagent-netpol` asserts the render; the runtime proof is an installation with Cilium enforcing — agentlab renders no network policies.

**Prerequisites.** Kubernetes **1.35** (Substrate's `PodCertificateRequest` is `certificates.k8s.io/v1beta1`, which 1.34 serves only as `v1alpha1`) with the feature gates `ClusterTrustBundle`, `ClusterTrustBundleProjection` and `PodCertificateRequest` on kube-apiserver, kube-controller-manager **and every kubelet**; enabling them rolls the control plane and every node. Where the render is live (the Helm CLI, `--dry-run=server`, helm-controller) it refuses a cluster that does not serve `certificates.k8s.io/v1beta1/PodCertificateRequest`, naming the gates — the kubelet gates cannot be seen from the apiserver and the message says so; `helm template` and CI, which see no cluster, are never refused. On a Giant Swarm cluster the cluster chart's `internal.advancedConfiguration.{controlPlane.apiServer,controlPlane.controllerManager,kubelet}.featureGates` bridges until giantswarm/cluster#1005 makes the gates the default; each list replaces the chart's default list, so repeat the defaults next to the three gates. A proxied installation mirrors `oci://ghcr.io/giantswarm/substrate/helm` (`components.substrate.repository`), `ghcr.io/giantswarm/substrate` (`substrate.image.registry`), the two hook images (`hooks.kubectlImage`, `hooks.opensslImage`) and the gVisor release asset the chart's `SandboxConfig gvisor-default` names (`gs://gvisor/releases/…`, fetched by `atelet` from `storage.googleapis.com` — override `spec.assets` on the SandboxConfig for a mirror). Upgrading a 3.x installation: [UPGRADE.md](./UPGRADE.md).

### Backstage, mcp-kubernetes, CloudNativePG and KServe

The components the [agent-platform-standalone](https://github.com/giantswarm/agent-platform-standalone) umbrella carried as Helm dependencies on top of this roster, so that a cluster with none of them can turn them on from the same `components:` map. All seven are **off by default**: a management cluster runs each of them as its own app and keeps them off, and the fleet render is unchanged. Each is rendered by the same loop as one `OCIRepository` + `HelmRelease`; the values blocks (`backstage:`, `mcp-kubernetes:`, `cloudnative-pg:`, `kserve-crd:`, `kserve-resources:`, `kserve-llmisvc-crd:`, `kserve-llmisvc-resources:`) carry the standalone's defaults and are forwarded verbatim, with `global` injected (all seven charts accept it).

| Component | Chart | Source | `versionRange` | `dependsOn` |
|---|---|---|---|---|
| `backstage` | [giantswarm/backstage](https://github.com/giantswarm/backstage) | `oci://gsoci.azurecr.io/charts/giantswarm` | `0.x` | `cloudnative-pg` |
| `mcp-kubernetes` | [giantswarm/mcp-kubernetes](https://github.com/giantswarm/mcp-kubernetes) | `oci://gsoci.azurecr.io/charts/giantswarm` | `>=1.1.1 <2.0.0` (the `global.identity` fallbacks the block relies on) | — |
| `cloudnative-pg` | [cloudnative-pg/charts](https://github.com/cloudnative-pg/charts) (upstream) | `oci://ghcr.io/cloudnative-pg/charts` | `0.29.x` (one chart minor is one operator line; moving it is a deliberate edit, an operator upgrade rolls every instance pod) | — |
| `kserve-crd` | [giantswarm/kserve](https://github.com/giantswarm/kserve) | `oci://gsoci.azurecr.io/charts/giantswarm` | `0.2.x` (the four kserve charts move together) | — |
| `kserve-resources` | giantswarm/kserve | `oci://gsoci.azurecr.io/charts/giantswarm` | `0.2.x` | `kserve-crd` |
| `kserve-llmisvc-crd` | giantswarm/kserve | `oci://gsoci.azurecr.io/charts/giantswarm` | `0.2.x` | — |
| `kserve-llmisvc-resources` | giantswarm/kserve | `oci://gsoci.azurecr.io/charts/giantswarm` | `0.2.x` | `kserve-crd`, `kserve-llmisvc-crd`, `kserve-resources` |

**Turning them on.** `components.<name>.enabled: true`. Backstage and mcp-kubernetes also need `global.domain` and `global.identity` (`issuerUrl`, `clientId`, `existingSecret` — the platform credentials Secret, with the keys `dex-client-secret` and, for Backstage, `backstage-session-secret`): the same quick-start inputs muster takes. The mcp-kubernetes chart fails its render without them, by design; the Backstage values mount that Secret by name. `kserve-resources` needs cert-manager on the cluster; `kserve-llmisvc-resources` reuses the shared objects `kserve-resources` renders (`kserve.createSharedResources: false`). On a cluster without Cilium set `mcp-kubernetes.ciliumNetworkPolicy.enabled: false` (see [Prerequisites](#prerequisites)).

**Order.** `dependsOn` replaces the two-phase first-install guard the standalone needed: `kserve-crd` is Established before the two controllers; the operator and the control plane come before their CR consumers — `agent-platform-connectivity` (the CNPG `Cluster` under `postgres.enabled`, the model serving objects) `dependsOn` `cloudnative-pg` and `kserve-resources`, `model-manager` `dependsOn` `kserve-resources`, `backstage` `dependsOn` `cloudnative-pg` (its database is a CNPG `Cluster` when `backstage.database.engine: postgresql`; the chart's default is sqlite). A reference to a toggled-off component is dropped at render time, as everywhere, so a management cluster that provides the operator and KServe as its own apps sees no change.

**CRDs.** None of the seven ships a `crds/` dir. `kserve-crd`, `kserve-llmisvc-crd` and `cloudnative-pg` render their CRDs as ordinary templates — Helm applies and upgrades them with every release, the KServe ones annotated `helm.sh/resource-policy: keep` — so their `HelmRelease`s carry no `crds:` policy; see [CRD lifecycle](#crd-lifecycle). The LLMInferenceService CRDs are their own component (`kserve-llmisvc-crd`): the standalone had to leave that 4.5 MB chart a hand-installed prerequisite because it did not fit next to everything else in one Helm release Secret; as its own release it does.

**Wiring.** The standalone rendered by hand what makes these a platform: the Backstage app-config ConfigMap (`agent-platform-backstage-app-config`, which the `backstage:` block mounts) and `HTTPRoute`, the mcp-kubernetes `MCPServer` registration with muster, the model serving runtime and presets. That wiring lives in the connectivity chart, gated on these same toggles — see [Turning on the standalone's extras](#turning-on-the-standalones-extras). The seven values blocks travel to the connectivity release with the rest of the tree (nothing of theirs is held back; `components.agent-platform-connectivity.omitKeys` stays the tool for a NEW top-level key the live connectivity chart does not declare yet — the new `modelServing:` block is a switch's block and travels only while `components.modelServing` is on, which closes that window without a follow-up release), and the connectivity release `dependsOn` `muster` as well, for the `MCPServer` CRD.

```bash
helm template r helm/agent-platform -f helm/agent-platform/ci/ci-values.yaml \
  --set components.backstage.enabled=true --set components.mcp-kubernetes.enabled=true \
  --set components.cloudnative-pg.enabled=true --set components.kserve-crd.enabled=true \
  --set components.kserve-resources.enabled=true --set components.kserve-llmisvc-crd.enabled=true \
  --set components.kserve-llmisvc-resources.enabled=true
make verify-components          # roster, order, BOM pins, the forwarded tree against the connectivity schema
make verify-components-charts   # pulls the seven charts and renders each with the forwarded values
```

### Turning on the standalone's extras

What the [agent-platform-standalone](https://github.com/giantswarm/agent-platform-standalone) umbrella wired by hand is connectivity-chart wiring here, gated on the component toggles the meta chart forwards. With every toggle off (the fleet) none of it renders and the connectivity objects are byte-identical to before; a cluster that turns the toggles on gets the standalone's behaviour from the same chart. Prerequisites on every path: the Gateway API CRDs and a public Gateway (`global.gatewayApi.parentRefs`, or `gatewayApi.gateway.create: true` for the chart-owned edge), the identity provider (`global.identity`: `issuerUrl`, `clientId`, `existingSecret` — the platform credentials Secret with `dex-client-secret`, and `backstage-session-secret` for the portal; `global.identity.ca.secretName` for a provider with a private CA) and `global.domain`, from which every hostname derives.

| Toggle | What the connectivity chart renders | Inputs |
|---|---|---|
| `components.backstage.enabled` | The portal's Agent Platform app-config ConfigMap `agent-platform-backstage-app-config` (the login provider from `global.identity`, one installation `backstage.installationName`, the in-cluster Kubernetes and muster entries, the Agent Platform plugin block with `agentPlatform.fluxServiceAccountName` from the one `kagent.fluxServiceAccountName` value, the skill repositories and — when their routes are on — the kagent and model-manager API URLs, the pg block when `backstage.database.engine` is `postgresql`), the public `HTTPRoute` `backstage.<domain>`, and the config-reload hook (a post-install/post-upgrade Job that stamps the rendered app-config's checksum into the Backstage Deployment's pod template so a config change rolls the pod; a no-op while unchanged, nothing on a first install — the backstage release `dependsOn` connectivity) with its network policy. | `backstage.hostname`, `.parentRefs`, `.installationName`, `.extraScopes`, `.startUrlSearchParams`, `.enabledExtensions`, `.disabledExtensions`, `.skillsRepositories`, `.catalogs.version`, `.configReload.*` — the wiring's keys in the `backstage:` block, dropped from the values forwarded to the backstage chart (`components.backstage.omitKeys`) |
| `components.mcp-kubernetes.enabled` | The `MCPServer` CR that registers the server with muster: `http://mcp-kubernetes.<namespace>.svc.cluster.local:8080/mcp`, with the server's OAuth on (`mcp-kubernetes.mcpKubernetes.oauth.enabled`) `auth: {type: oauth, forwardToken: true}` and `requiredAudiences: [mcp-kubernetes.kubernetesAudience]` — muster forwards the session's id_token, mcp-kubernetes presents it to the kube-apiserver, the user's RBAC governs. Labelled `agent-platform.giantswarm.io/tool-group: infrastructure` (the `infrastructure` toolset preset). Rendered only with muster on; the connectivity release `dependsOn` muster for the CRD. | `mcp-kubernetes.kubernetesAudience` (default `dex-k8s-authenticator`; `""` for Google), dropped from the values forwarded to the mcp-kubernetes chart |
| `components.modelServing.enabled` (+ `components.kserve-crd`, `components.kserve-resources`) | Model serving on KServe with vLLM (the `modelServing:` block reaches the connectivity release only while the switch is on): the `ClusterServingRuntime` `kserve-vllm`, the serving presets (one ConfigMap per preset, label `agent-platform.giantswarm.io/serving-preset=true`; seven shipped under `files/model-serving/presets/`, more or replacements under `modelServing.presets`), the discovery ConfigMap `agent-platform-model-serving`, the serving `Namespace`, the Hugging Face cache `PersistentVolumeClaim` (kept on uninstall), the two Kyverno `ClusterPolicy` objects that wire every predictor pod to the cache (`modelServing.policies.enabled: auto` — where `kyverno.io/v1` is served), and the network policies of the serving namespace in the resolved flavor. The render fails with a clear message when neither the two KServe components are on nor the `serving.kserve.io` APIs are served (`modelServing.kserve.requireApi`). A model-manager on the `kserve` backend must agree with the layer (`model-manager.kserve.*`; the chart's defaults do). | `modelServing.*` — the standalone's `components.modelServing.*` block, one-to-one |
| `components.kserve-resources.enabled` (`components.kserve-llmisvc-resources.enabled`) | The controllers' network policies (webhook 9443, metrics 8443, probes 8081; DNS and API egress) in both flavors, and the guards: `kserve-resources.kserve.controller.deploymentMode` must be `Standard`, `kserve-llmisvc-resources.kserve.createSharedResources` must stay `false`, the llmisvc controller needs `components.kserve-resources` on. | the component charts' own blocks |

**Migrating a standalone installation's values.** Drop `components.` for the wiring keys, and split `components.kserve`:

| agent-platform-standalone | agent-platform (this chart) |
|---|---|
| `components.backstage.enabled` | `components.backstage.enabled` |
| `components.backstage.<hostname, parentRefs, extraScopes, startUrlSearchParams, enabledExtensions, disabledExtensions, skillsRepositories, catalogs, configReload>` | `backstage.<same key>` |
| the standalone's release name (`gs.installations.<release>`) | `backstage.installationName` (default `agent-platform`) |
| `components.mcp-kubernetes.enabled` | `components.mcp-kubernetes.enabled` |
| `components.mcp-kubernetes.kubernetesAudience` | `mcp-kubernetes.kubernetesAudience` |
| `components.modelServing.enabled` | `components.modelServing.enabled` (unchanged) |
| `components.modelServing.<kserve, namespace, runtime, serving, presets, shippedPresets, cache, policies, networkPolicy>` | `modelServing.<same key>` |
| `components.modelServing.policies.enabled: false` (Kyverno was not a prerequisite) | `modelServing.policies.enabled: auto` (follows `kyvernoPolicies.enabled`; `true` / `false` force) |
| `components.kserve.enabled` | `components.kserve-crd.enabled` + `components.kserve-resources.enabled` |
| `components.kserve.llmisvc.enabled` | `components.kserve-llmisvc-crd.enabled` + `components.kserve-llmisvc-resources.enabled` (the CRDs are a component now, not a hand-installed prerequisite) |
| `components.kserve.certManager.requireApi`, `components.kserve.llmisvc.requireApi`, the two-phase first install | gone: `dependsOn` orders the CRD charts before the controllers and both before the connectivity and model-manager releases; cert-manager stays a documented prerequisite of `kserve-resources` |
| `components.cloudnative-pg.enabled`, `backstage.database.engine: postgresql` | unchanged |
| `components.kagent.controllerRoute.*`, `components.kagent.uiRoute.*` | `kagent.controllerRoute.*`, `kagent.uiRoute.*` |
| `components.model-manager.route.*`, `components.agent-manager.route.*` | `modelManager.route.*`, `agentManager.route.*` |
| `components.model-manager.networkPolicy.*`, `components.agent-manager.networkPolicy.*` | `modelManager.networkPolicy.*`, `agentManager.networkPolicy.*` |
| `global.networkPolicy.<enabled, flavor, additionalEgressCIDRs, additionalEgressFQDNs, kubernetes.*>` | `networkPolicy.<same key>` (`flavor: auto` detects Cilium; see [Cluster shape](#cluster-shape-auto)) |
| `kyvernoPolicies.enabled: false`, `global.observability.metrics.serviceMonitor.enabled: false` (the vanilla overlay) | drop them — `auto` renders by served API group |
| `kagent.kagent.*`, `agentgateway.agentgateway.*` (nested subcharts) | `kagent.*`, `agentgateway.*` (keys at the chart root: the kagent line's chart, the flattened 2.x agentgateway chart) |
| the contract's defaults: `global.identity.clientId: agent-platform`, `global.identity.existingSecret: agent-platform-idp`, `ingress.mode: agentgateway-muster`, `agent-platform-mcps.agentgateway.viaMuster: true`, `ingress.httpRoute.timeouts.request: 0s`, `components.<kagent, agentgateway, agent-platform-mcps, agent-manager, backstage, mcp-kubernetes>.enabled: true` | set them explicitly — this chart's defaults are the fleet's (empty identity, `muster-direct`, no route timeout, the six off) |
| `mcp-kubernetes.*`, `backstage.*` (the chart blocks), `kserve-resources.*`, `kserve-llmisvc-resources.*` | unchanged |

```bash
helm template t helm/agent-platform-connectivity -f helm/agent-platform-connectivity/ci/test-standalone-extras-values.yaml   # every toggle on, the vanilla shape
make verify-wiring   # off = no object; on = the objects; the guards; the meta chart forwards the blocks and omits the wiring keys
```

### Tenant identity

Agents are Flux `HelmRelease`s of the [agent chart](https://github.com/giantswarm/agent) in the kagent namespace, written by the portal's create flow and by agent-manager. Under a Flux multi-tenancy lockdown — helm-controller with `--no-cross-namespace-refs` and a rights-less default ServiceAccount, or the Flux multi-tenancy admission policy on Giant Swarm management clusters — a `HelmRelease` executes as the ServiceAccount it names and fails without one (`secrets is forbidden … :default`). Creating the `HelmRelease` stays the caller's identity; executing it is the tenant's.

The connectivity chart renders that tenant identity whenever kagent is on (`templates/kagent/flux-service-account.yaml`): ServiceAccount `kagent-flux` in the kagent namespace and a RoleBinding to `cluster-admin` — namespace-scoped admin, full control of the kagent namespace and nothing outside it (the built-in `admin` role does not cover the kagent.dev kinds). **One value names it everywhere.** `kagent.fluxServiceAccountName` (default `kagent-flux`) is the ServiceAccount's name and the RoleBinding's subject; the meta chart derives agent-manager's `flux.helmReleaseServiceAccount` from it (`agent-platform.componentDerivedValues` — a value set in the `agent-manager:` block must agree, or the render fails naming the key); and the portal's `agentPlatform.fluxServiceAccountName` is rendered from the same helper, `agent-platform.kagent.fluxServiceAccountName`. Changing it in one place changes all three; an empty value renders no identity and hands both callers an empty name. `make verify-identity` asserts it.

The platform's own component `HelmRelease`s are the second tenant. On a Giant Swarm management cluster they live in the exempt `flux-giantswarm` namespace (`gitops.namespace`) and need no identity of their own; a cluster whose Flux the chart brings itself gives them one, `agent-platform-flux`, with the bundled engine.

### Gateway API CR ownership

Of the resources the umbrella manages, **only `AgentgatewayParameters` is vendor-specific** to agentgateway. Everything else is standard Gateway API / standard Cilium.

| Resource | API | Owned by | Notes |
|---|---|---|---|
| `Gateway` (data-plane spawn trigger) | `gateway.networking.k8s.io/v1` (standard) | this umbrella (`templates/agentgateway/gateway.yaml`) | Coupled to agentgateway only via `gatewayClassName: agentgateway`. One per data plane. |
| `AgentgatewayParameters` | `agentgateway.dev/v1alpha1` (**vendor-specific**) | this umbrella (`templates/agentgateway/agentgatewayparameters.yaml`) | The only agentgateway-vendor CR the umbrella ships. Strategic-merge overlay over the controller-rendered Deployment + Service; forces Service type ClusterIP by default. |
| `CiliumNetworkPolicy` / `NetworkPolicy` (×4 — controller + data-plane, per flavor) | `cilium.io/v2` or `networking.k8s.io/v1` | this umbrella (`templates/agentgateway/networkpolicy-*.yaml`) | Two pods covered: controller and data-plane. Upstream agentgateway ships no policies. |
| `HTTPRoute` (muster public `/` route) | `gateway.networking.k8s.io/v1` (standard) | this umbrella (`templates/ingress/muster-httproute.yaml`) | Always rendered. Attaches to the public Gateway from `ingress.parentRefs` / `ingress.hostnames` (typically `envoy-gateway-system/giantswarm-default`), NOT to the umbrella's data-plane Gateway. |
| `HTTPRoute` (agentgateway `/mcp` route) | `gateway.networking.k8s.io/v1` (standard) | this umbrella (`templates/agentgateway/httproute.yaml`) | `agentgateway-*` modes only. Reads the same `ingress.parentRefs` / `ingress.hostnames`; the more-specific `/mcp` path steals MCP traffic while OAuth / `.well-known` / DCR stay on muster's `/` route. |
| `BackendTrafficPolicy` (muster `/` route) | `gateway.envoyproxy.io/v1alpha1` (standard Envoy Gateway) | this umbrella (`templates/ingress/muster-backendtrafficpolicy.yaml`) | All modes, gated on `ingress.backendTrafficPolicy.enabled`. Route-scoped over muster's `/` route to preserve muster's `401 … WWW-Authenticate` challenge against the cluster-wide error-pages policy — critical in `muster-direct`, where muster serves `/mcp` directly. |
| `BackendTrafficPolicy` (agentgateway `/mcp` route) | `gateway.envoyproxy.io/v1alpha1` (standard Envoy Gateway) | this umbrella (`templates/agentgateway/backendtrafficpolicy.yaml`) | `agentgateway-*` modes only, gated on `ingress.backendTrafficPolicy.enabled`. Route-scoped over the `/mcp` route to preserve `WWW-Authenticate` and set `requestTimeout: 0s`. |
| `Gateway` (public routing endpoint) | `gateway.networking.k8s.io/v1` (standard) | platform team | `envoy-gateway-system/giantswarm-default` — not owned by this chart. |

### OAuth secrets

Three orthogonal paths. None replace the others — pick what matches your topology.

| Path | Configured via | Use case |
|---|---|---|
| **Inline values** | `muster.muster.oauth.server.dex.clientSecret`, `registrationToken`, `encryptionKeyValue`, `storage.valkey.password` | Quick dev/test or GitOps with values-level encryption (sops + helm-secrets, Flux `decryption:`). No external Secret to manage. |
| **`existingSecret`** | `muster.muster.oauth.server.existingSecret: <name>` (Secret pre-created out-of-band) | Production Giant Swarm pattern — SOPS-encrypted Secret in giantswarm-configs reconciled by Flux ahead of the platform; or `kubectl create secret` for manual ops. |
| **`extraObjects`** | Umbrella-level `extraObjects: []` list (this chart) + muster `existingSecret` pointed at the rendered Secret | Single Helm release ships Secret + values together. Raw-Helm installs (no GitOps controller) that want one `helm upgrade` to manage everything. |

Example using `extraObjects` + `existingSecret`:

```yaml
extraObjects:
  - apiVersion: v1
    kind: Secret
    metadata:
      name: muster-oauth
    type: Opaque
    stringData:
      dex-client-secret: REPLACE_ME
      registration-token: REPLACE_ME
      oauth-encryption-key: REPLACE_ME
      valkey-password: REPLACE_ME

muster:
  muster:
    oauth:
      server:
        existingSecret: muster-oauth
```

Random auto-generation via Helm `lookup` is intentionally not supported — every `helm upgrade` would either regenerate values (invalidating issued tokens) or render different output under `helm template` / `--dry-run` than at install time.

`muster.muster.oauth.server.dex.{issuerUrl,clientId}` and the matching `dex-client-secret` must correspond to a client registered against the cluster's OIDC issuer. Registration is out of scope for this chart; `redirectURI` must equal `<muster.oauth.server.baseUrl>/oauth/callback`.

### Bundled Valkey

`components.valkey.enabled: true` bundles [giantswarm/valkey-app](https://github.com/giantswarm/valkey-app) (a Giant Swarm wrapper around upstream `valkey-io/valkey-helm`) with persistent storage. Single-pod Deployment behind a Service named `muster-valkey` (via `fullnameOverride`). Muster's storage defaults to `type: valkey` + `url: muster-valkey:6379`, so enabling the bundled chart alongside `oauth.server.enabled: true` is the only flip needed — no `url` override required.

```yaml
valkey:
  enabled: true
  valkey:
    auth:
      usersExistingSecret: muster-oauth  # Secret must carry key `valkey-password`

muster:
  muster:
    oauth:
      server:
        enabled: true
        existingSecret: muster-oauth
        # storage.type: valkey
        # storage.valkey.url: muster-valkey:6379
        # — both inherited from the umbrella defaults.
```

ACL authentication is enabled by default for the `default` user (`~* &* +@all`), with the cleartext password read from `valkey-password` in the operator-supplied Secret. Muster sends `AUTH <password>` against the default user, which is the standard backwards-compatible form.

Operators with an out-of-band Valkey leave `components.valkey.enabled: false` and override `muster.muster.oauth.server.storage.valkey.url` to point at the external endpoint. See [UPGRADE.md](./UPGRADE.md) for migration notes from a previously-existing standalone Valkey.

### Bundled MCP servers

`components.agent-platform-mcps.enabled: true` bundles [giantswarm/agent-platform-mcps](https://github.com/giantswarm/agent-platform-mcps), which renders the platform's MCP server CRs from one abstract, vendor-neutral `mcpServers` list — muster `MCPServer` CRs by default, and/or agentgateway `AgentgatewayBackend` + `AgentgatewayPolicy` CRs. Like the umbrella's other CRs, these consume app-owned CRDs (`MCPServer` rides the muster component, the agentgateway CRs ride the agentgateway component); this sub-chart ships **no** CRDs of its own, and its release `dependsOn` muster and agentgateway so the CRDs Establish first.

```yaml
mcps:
  enabled: true

agent-platform-mcps:
  mcpServers:
    - cluster: <cluster>
      group: kubernetes
      url: https://mcp.<cluster>.<base-domain>/mcp
```

The toggle is `components.agent-platform-mcps.enabled`, not a key inside the chart's own value namespace: the sub-chart's `values.schema.json` is strict (`additionalProperties: false`) and rejects an `enabled` key. Everything under `agent-platform-mcps.*` is passed through to the sub-chart verbatim — see its [values reference](https://github.com/giantswarm/agent-platform-mcps) for `defaults`, `identityProviders`, per-entry `auth`, and the `muster` / `agentgateway` rendering toggles. Even when enabled, the chart renders nothing until `mcpServers` is populated.

### Toolset presets

An agent declares a **toolset** — the selectors that say which of the gateway's tools it is composed with (`preset:<name>`, `server:<name>`, `workflow:<name>`, `tool:<name>`; the agent chart's `toolset` value, agent-manager's `toolset` argument). muster resolves it per request, on top of the caller's own access. Presets are muster configuration, so this chart ships the platform's two under `muster.muster.toolsetPresets`, both selecting by the tool-group label every platform-shipped `MCPServer` carries (`agent-platform.giantswarm.io/tool-group`):

| Preset | Selects |
|---|---|
| `infrastructure` | The mcp-kubernetes, mcp-capi and mcp-prometheus families (label `infrastructure`, stamped by agent-platform-mcps ≥ 0.9.0). |
| `agent-platform` | agent-manager, model-manager and muster's `core_*` tools — the meta agent's preset (label `agent-platform`, stamped by agent-manager ≥ 0.3.0 and model-manager ≥ 0.18.0). |

muster builds `read-only`, `none` and `full` in. An installation adds its own (`test-clusters`, …) next to the shipped ones in its gitops values. The `label:` rule needs muster ≥ 5.12.0, which is the floor of `components.muster.versionRange`. Details, the installation examples and the verification recipe: [docs/toolset-presets.md](./docs/toolset-presets.md); `make verify-presets` renders the real muster chart's ConfigMap from the forwarded values.

### Postgres backups

`postgres.enabled` renders the CloudNativePG `Cluster` the kagent controller uses (the CNPG operator is a cluster-level prerequisite). `postgres.backup` protects it; without the block the Cluster carries `agent-platform.giantswarm.io/backup: none` and NOTES warns that its PVCs are the only copy of the platform database (agents, sessions, tasks, tools). Do not read `ContinuousArchiving=True` on such a Cluster as a backup: CNPG's `wal-archive` command exits 0 when it has nowhere to archive to.

- `postgres.backup.method: plugin` (default) uses the [Barman Cloud plugin](https://cloudnative-pg.io/plugin-barman-cloud/) — installed next to the CNPG operator, a prerequisite this chart does not ship — for continuous WAL archiving and scheduled base backups to an object store, giving point-in-time recovery. The chart renders the `ObjectStore` (`postgres.backup.objectStore.*`: `destinationPath`, credentials, `retentionPolicy`) and a `ScheduledBackup` (`postgres.backup.schedule`, daily by default).
- `postgres.backup.crossplane.*` provisions the store as Crossplane managed resources (S3 bucket + IRSA role on AWS, Storage Account + Container on Azure, with a PrivateEndpoint on private installations) and derives the path and credentials for the `ObjectStore`. Installations without Crossplane set `destinationPath` and one credential source themselves.
- `postgres.backup.method: volumeSnapshot` takes CSI snapshots instead (needs a `VolumeSnapshotClass`; no WAL archive).
- Restore is a second Cluster bootstrapped with `recovery` from the same `ObjectStore` (`externalClusters[].plugin` with `serverName` = the source Cluster's name), never the live one. Name it exactly `<clusterName>-restore`: the Cluster's network policy selects that name too (the recovery Job needs the Kubernetes API and the store), and the AWS role trusts `<clusterName>-restore*` ServiceAccounts.
- Turning the block on for a Cluster that already runs rolls its instance pods once (the plugin sidecar). The `immediate` first `Backup` fires before the sidecar exists and fails with `requested plugin is not available`; the next scheduled run, or a manual `Backup` with `method: plugin` and `pluginConfiguration.name: barman-cloud.cloudnative-pg.io`, succeeds. A Cluster created with the block does not hit this.

```bash
helm template r helm/agent-platform-connectivity -f helm/agent-platform-connectivity/ci/test-postgres-backup-aws-values.yaml
make verify-postgres
```

### Agent sandbox

`components.agent-sandbox.enabled: true` bundles the [agent-sandbox](https://github.com/kubernetes-sigs/agent-sandbox) controller, which reconciles `Sandbox` resources into isolated pods. This is the runtime **kagent's `SandboxAgent` delegates pod isolation to** — the `SandboxAgent` CRD ships with the kagent component and the `Sandbox*` CRDs ship with the agent-sandbox component (app-owned CRDs), but the feature is inert until this controller runs, so enabling it is the prerequisite for sandboxed agents.

```yaml
agentSandbox:
  enabled: true
```

The toggle is `components.agent-sandbox.enabled`; the `agentSandbox:` block holds only values the connectivity chart reads, because the bundled `agent-sandbox` chart's `values.schema.json` is strict (`additionalProperties: false`) and rejects everything it does not declare. The agent-sandbox CRDs (`Sandbox` / `SandboxTemplate` / `SandboxClaim` / `SandboxWarmPool`) ship with the agent-sandbox component chart itself (app-owned CRDs), so enabling the controller installs them too.

The agent-sandbox chart is kept vendor-agnostic and the upstream controller exposes no `securityContext` knob, so the umbrella injects restricted-PSS fields into the controller Deployment at admission via a Kyverno mutate policy (`agentSandbox.podSecurity.*`). `agentSandbox.podSecurity.enabled` defaults to `auto` and follows the resolved `kyvernoPolicies.enabled`; set it `false` to drop the policy, or tune the `podSecurityContext` / `containerSecurityContext` blocks. The policy matches the `agent-sandbox-controller` Deployment in `agentSandbox.podSecurity.namespace` (default `agent-sandbox-system`), which must match the chart's namespace.

That policy is the controller's only source of a `securityContext`. So `kyvernoPolicies.enabled: false` (see [Kyverno](#kyverno)) fails the render while `agentSandbox.podSecurity.enabled` is explicitly `true` (at `auto` it goes off with Kyverno), and the failure names the two ways out: `components.agent-sandbox.enabled: false` on a cluster that enforces restricted PSS, or `agentSandbox.podSecurity.enabled: false` where nothing enforces it and a controller pod without a `securityContext` is acceptable. On the flux engine the `agent-sandbox` release `dependsOn` the connectivity release, so Kyverno holds the policy before the Deployment applies.

### Ingress topology

> For the end-to-end authentication story — the request path, OAuth discovery,
> `forward` vs `exchange` token handling, and edge JWT validation / JWKS — see
> [docs/authentication.md](./docs/authentication.md).

The request topology is selected by a single declared selector, `ingress.mode`, with three values:

| `ingress.mode` | Path | Renders |
|---|---|---|
| `muster-direct` (default) | client → muster `/` | muster public `/` route only — **no** agentgateway controller, **no** data-plane `Gateway`, **no** data-plane NetworkPolicies. |
| `agentgateway-muster` | client → agentgateway `/mcp` → muster; everything else → muster | the above **+** agentgateway controller dependency, data-plane `Gateway`, `AgentgatewayParameters`, data-plane NetworkPolicies, `/mcp` `HTTPRoute`, and the optional route-scoped `BackendTrafficPolicy`. |
| `agentgateway-direct` | client → agentgateway `/mcp` → servers | same as `agentgateway-muster`, plus the optional `gateway.jwksEgress` rule. **Not yet supported — install is blocked** (needs a DCR-capable IdP, RFC 7591/8707). |

The mechanism is Gateway-API path-specificity. The umbrella **always** renders muster's public `/` catch-all route (`templates/ingress/muster-httproute.yaml`). In the `agentgateway-*` modes it additionally renders a more-specific `/mcp` route that steals MCP traffic into agentgateway, while OAuth / `.well-known` / DCR stay on muster's `/` route.

Both rendered routes attach to the public Gateway and use the muster hostname(s). `parentRefs` and `hostnames` are now set **once** under `ingress.*` and shared by both routes — they must match the OAuth callback URL from `muster.oauth.mcpClient.publicUrl`. The umbrella's `ingress.parentRefs` guard rejects install in **every** mode (including `muster-direct`) until it is set — the muster `/` route always needs a Gateway to attach to. In `muster-direct` mode neither the agentgateway controller nor any data-plane object is installed.

```yaml
ingress:
  mode: muster-direct          # muster-direct | agentgateway-muster | agentgateway-direct
  parentRefs: []               # ALL modes (required): the public Gateway both rendered routes attach to
  hostnames: []                # ALL modes: muster public hostname(s)
  httpRoute:                   # shared base, applied to both routes
    annotations: {}
    labels: {}
    # Optional per-route overrides, merged over the shared maps above
    # (per-route keys win). `muster` = the `/` route; `mcp` = the `/mcp` route.
    muster: {}                 # { annotations: {}, labels: {} }
    mcp: {}                    # { annotations: {}, labels: {} }
  backendTrafficPolicy:        # agentgateway-* modes only
    enabled: false
    timeout: "0s"
    annotations: {}
    labels: {}
```

In an `agentgateway-*` mode, also set `components.agentgateway.enabled: true` and the shared `ingress.parentRefs` / `ingress.hostnames`:

```yaml
agentgateway:
  enabled: true

ingress:
  mode: agentgateway-muster
  parentRefs:
    - name: giantswarm-default
      namespace: envoy-gateway-system
      group: gateway.networking.k8s.io
      kind: Gateway
  hostnames:
    - muster.<cluster>.<base-domain>
```

## Observability

All bundled components push OTel traces to the cluster-wide `otlp-gateway.kube-system.svc:4317` (gRPC) by default:

| Component | Mechanism | Default endpoint |
|---|---|---|
| agentgateway data plane | `OTEL_EXPORTER_OTLP_ENDPOINT` + `OTEL_EXPORTER_OTLP_PROTOCOL` env vars via `gateway.parameters.dataPlaneEnv` | `http://otlp-gateway.kube-system.svc:4317` |

Muster does not yet support OTLP push. Its `/metrics` endpoint is scraped via `ServiceMonitor` (`muster.muster.observability.metrics.prometheus.serviceMonitor.enabled`, `auto`: follows the resolved `global.observability.metrics.serviceMonitor.enabled`).

The kagent line serves **no Prometheus `/metrics`**: its controller binds the metrics server to `0`, so `kagent.serviceMonitor.enabled` (the connectivity chart's controller metrics `Service` + `ServiceMonitor`) and the forwarded `kagent.controller.metrics.enabled` (upstream's knob, which renders a Service and `METRICS_*` env to that dead port) default to `false` in both charts — a monitor would scrape a refused port and sit at `up=0`. Nothing reads those metrics: the fleet's `KagentControllerDown` rule reads the Deployment's kube-state-metrics series, the HelmRelease and CNPG alerts read Flux and CNPG state. Both knobs go back on together when upstream serves metrics. kagent's OTel exporters (traces, logs) are unaffected and keep following the resolved `global.observability.metrics.serviceMonitor.enabled`. `make verify-global` asserts the default renders no kagent monitor and that `kagent.serviceMonitor.enabled: true` still renders one under the global gate.

Override any endpoint per component:

```yaml
gateway:
  parameters:
    dataPlaneEnv:
      - name: OTEL_EXPORTER_OTLP_ENDPOINT
        value: http://tempo-distributor.tempo.svc:4317
      - name: OTEL_EXPORTER_OTLP_PROTOCOL
        value: grpc

```

## Reference workloads (not bundled)

These MCP servers and agent runtimes integrate with the Agent Platform but are maintained by other teams and are not bundled in this umbrella.

| Component | Team | Purpose |
|---|---|---|
| [giantswarm/mcp-observability-platform](https://github.com/giantswarm/mcp-observability-platform) | atlas | MCP server exposing Grafana, Mimir, Loki, Tempo, and Alertmanager with OIDC RBAC. Deploy as an `MCPServer` CR behind muster. |
| [giantswarm/mcp-prometheus](https://github.com/giantswarm/mcp-prometheus) | planeteers | MCP server for Prometheus query. |
| [giantswarm/mcp-capi](https://github.com/giantswarm/mcp-capi) | planeteers | MCP server for Cluster API. |
| [giantswarm/mcp-runbooks](https://github.com/giantswarm/mcp-runbooks) | planeteers | MCP server for Giant Swarm runbooks. |
| [giantswarm/mcp-kubernetes](https://github.com/giantswarm/mcp-kubernetes) | bumblebee | MCP server for the Kubernetes API. |

## Private registry overrides

All images default to `gsoci.azurecr.io/giantswarm/*`:

| Image | Source (mirrored by GS retagger) |
|---|---|
| `gsoci.azurecr.io/giantswarm/muster:0.1.197` | `gsoci.azurecr.io/giantswarm/muster` (native GS image) |
| `gsoci.azurecr.io/giantswarm/agentgateway-controller:v1.2.1` | `cr.agentgateway.dev/controller` |
| `gsoci.azurecr.io/giantswarm/agentgateway:v1.2.1` | `cr.agentgateway.dev/agentgateway` |
| `gsoci.azurecr.io/giantswarm/agent-sandbox-controller:v0.4.6` | `registry.k8s.io/agent-sandbox/agent-sandbox-controller` (only when `components.agent-sandbox.enabled`) |

To pull from a private mirror, override the registry on every image (neither subchart exposes a `global.registry` that propagates to all images):

```yaml
global:
  registry: registry.example.com

muster:
  image:
    registry: registry.example.com

agentgateway:
  image:
    registry: registry.example.com
  proxy:
    image:
      registry: registry.example.com

# agent-sandbox nests the upstream controller under its own `agent-sandbox:` key,
# and the chart ignores global.registry — override the full image reference:
agent-sandbox:
  agent-sandbox:
    image:
      repository: registry.example.com/giantswarm/agent-sandbox-controller
```

## Security

The agentgateway data-plane pod template is rendered at runtime by the controller, not by Helm. To inject restricted-PSS-compatible `securityContext` fields, the umbrella ships an `AgentgatewayParameters` resource referenced from `Gateway.spec.infrastructure.parametersRef`. The controller applies it as a strategic merge patch over the generated Deployment and Service — that's how `gateway.parameters.serviceType: ClusterIP` forces the otherwise-hardcoded `type: LoadBalancer`.

The data-plane pod template hardcodes `sysctls: [net.ipv4.ip_unprivileged_port_start=0]`. This is a namespaced-safe sysctl (no kubelet allowlist required). On clusters with built-in Pod Security Admission `restricted` enforced, the sysctl will be rejected — label the install namespace with `pod-security.kubernetes.io/enforce: baseline` (or allowlist the sysctl in Kyverno's `restrict-sysctls` policy as Giant Swarm workload clusters already do).

## CRD lifecycle

**CRDs are app-owned.** There is no standalone CRD chart. Each component ships its own CRDs in its chart's `crds/` directory and owns their version; the `agent-platform` meta-package itself installs **no** CRDs — it only renders the per-component releases (which carry the CRDs) and the CRs that consume them.

| CRDs | Owned by (ships them in its chart `crds/` dir) |
|---|---|
| `gateways.gateway.networking.k8s.io`, `httproutes…`, `gatewayclasses…` | Gateway API upstream — cluster prerequisite (not shipped by any of these charts) |
| `agentgatewayparameters.agentgateway.dev`, `agentgatewaybackends…`, `agentgatewaypolicies…` | the **agentgateway** component chart (`giantswarm/agentgateway` GS wrapper) |
| `mcpservers.muster.giantswarm.io`, `workflows.muster.giantswarm.io` | the **muster** component chart |
| `agenttemplates.kagent.dev`, `harnesses…`, `modelconfigs…`, `modelproviderconfigs…`, `remotemcpservers…` | the **kagent-crds** component chart (the kagent line, `giantswarm/kagent-upstream`) — as templates, not `crds/`; `keep`-annotated. Follows `components.kagent`. |
| `workerpools.ate.dev`, `sandboxconfigs.ate.dev`, `csidriverconfigs.ate.dev` | the **substrate-crds** component chart (the Substrate line, `giantswarm/substrate`) — as templates, not `crds/`; `keep`-annotated (from `v0.0.27-gs.3`). Follows `components.kagent`; the kagent release's `WorkerPool` and the substrate release's `SandboxConfig` are its CRs. |
| `sandboxes.agents.x-k8s.io`, `sandboxtemplates…`, `sandboxclaims…`, `sandboxwarmpools.extensions.agents.x-k8s.io` | the **agent-sandbox** component chart (`giantswarm/agent-sandbox`) |
| `inferenceservices.serving.kserve.io`, `servingruntimes…`, `clusterservingruntimes…`, `clusterstoragecontainers…`, `inferencegraphs…`, `trainedmodels…` | the **kserve-crd** component chart (`giantswarm/kserve`) — as templates, not `crds/`; `keep`-annotated. Off by default. |
| `llminferenceservices.serving.kserve.io`, `llminferenceserviceconfigs…` | the **kserve-llmisvc-crd** component chart (`giantswarm/kserve`) — as templates; `keep`-annotated. Off by default. |
| `clusters.postgresql.cnpg.io`, `backups…`, `scheduledbackups…`, `poolers…`, `databases…`, … | the **cloudnative-pg** component chart (upstream, `crds.create`) — as templates. Off by default; a management cluster runs the CNPG operator as its own app. |

Each component that ships a `crds/` dir sets `crds: CreateReplace` on its `HelmRelease` (rendered by the meta-package), so Flux applies and upgrades those CRDs atomically with the app at the same resolved version — Helm on its own never upgrades `crds/`-dir CRDs. The kagent-crds, substrate-crds, kserve and cloudnative-pg charts render their CRDs as ordinary templates instead, so their `HelmRelease`s carry no `crds:` policy and Helm applies and upgrades them with every release. All these CRDs carry `helm.sh/resource-policy: keep` (CNPG's through the operator chart's own handling), so they survive a component uninstall (the CRs are never cascade-deleted).

`helm uninstall agent-platform` (the meta-package) leaves everything intact — it owns no CRDs or CRs. Uninstalling a **component** release leaves its `keep`-annotated CRDs (and their CRs) in place; to remove a CRD you must delete it explicitly.

### Upgrading CRDs

CRDs migrate with their owning component release — a new component version (resolved by `components.<name>.versionRange`) ships the matching CRD schema and `CreateReplace` applies it. No separate CRD-chart upgrade step.

> **History.** Through `1.9.x` these CRDs shipped in a standalone `agent-platform-crds` bundle chart that every release `dependsOn`. That bundle has been retired in favour of app-owned CRDs; the staged, non-destructive migration (the live CRDs were first re-annotated with `helm.sh/resource-policy: keep` so dropping the bundle never cascade-deletes a CR) is documented in [UPGRADE.md](./UPGRADE.md).

## Compatibility

Targets Kubernetes ≥ 1.33. Giant Swarm management clusters run Cilium, Kyverno, prometheus-operator and Envoy Gateway; the chart renders what the cluster serves (see below), so a kind or plain cloud cluster installs the same chart. The `kubernetes` NetworkPolicy flavor works on any CNI but is best-effort (no entity selectors, no FQDN egress).

### Cluster shape (`auto`)

The knobs that describe what a cluster can admit default to `auto` and resolve by served API group, so one chart renders the fleet shape on a management cluster and the vanilla shape on a kind or plain cloud cluster without a values file that describes the cluster: `kyvernoPolicies.enabled` (Kyverno objects when `kyverno.io/v1` is served), `networkPolicy.flavor` (`cilium` when `cilium.io/v2` is served, else `kubernetes`), `global.observability.metrics.serviceMonitor.enabled` (monitor objects when `monitoring.coreos.com/v1` is served), `dicebear.route.enabled` (the avatar route needs Envoy Gateway's `HTTPRouteFilter`, `gateway.envoyproxy.io/v1alpha1`) and `agentSandbox.podSecurity.enabled` (a Kyverno mutate policy; follows the resolved Kyverno answer). The meta chart detects once, from `.Capabilities.APIVersions`, and resolves the knobs before it inlines a component's values — including the component-level copies left at `auto` (muster's `networkPolicy.flavor`, ServiceMonitor, PrometheusRule and Grafana dashboard; valkey's `ciliumNetworkPolicy.enabled` and PodMonitor; kagent's OTel exporters, oauth2-proxy ServiceMonitor and the `OTEL_EXPORTER_OTLP_HEADERS` env entry) — so every component sees the same answer and the connectivity chart never sees `auto`. Detection is live under helm-controller, the Helm CLI and `helm install --dry-run=server`; `helm template` sees only Helm's built-in API set and renders the vanilla shape unless the groups are passed (`--api-versions kyverno.io/v1 --api-versions cilium.io/v2 --api-versions monitoring.coreos.com/v1 --api-versions gateway.networking.k8s.io/v1 --api-versions gateway.envoyproxy.io/v1alpha1` renders the fleet shape offline).

To override, set the value instead of `auto`: `true` / `false` (`cilium` / `kubernetes`) on a knob wins over detection everywhere it is derived (`networkPolicy.flavor: kubernetes` with Cilium present renders the kubernetes flavor for the connectivity chart, muster and valkey); a component copy set explicitly (`muster.networkPolicy.flavor: cilium`) wins for that component alone. The schema accepts `auto|true|false` (`auto|cilium|kubernetes`) and rejects anything else. The connectivity chart's other `.Capabilities` checks — the Flux APIs for agent-manager, the KServe APIs for the kserve backend — stay hard failures: those are prerequisites, not cluster shape. `make verify-auto` asserts the resolution per shape, the byte-identical fleet render and the overrides.

### Kyverno

The connectivity chart renders `kyverno.io` objects: the agent-sandbox pod-security ClusterPolicy; with Agent Substrate on, the four PolicyExceptions of Substrate's workloads ([Agent Substrate](#agent-substrate)); and, only with `postgres.vector.extensionImage.reference` set, the PolicyException that admits the CNPG instance pods' `image` volume. (The two ClusterPolicies that gave the v1alpha2 declarative agents their securityContext and their srt settings went with kagent API v2 — agents run as Substrate actors, so there is no `Agent` CR, per-agent Deployment or config Secret left to mutate — and so did those agents' seccomp exception; `substrate-workers` is its successor.) On a cluster with no Kyverno they would fail the install on an unknown API group, so `kyvernoPolicies.enabled: auto` (the default) renders none of them where `kyverno.io/v1` is not served; `false` forces that, `true` forces them on.

A PolicyException targets policies this chart does not own, so the names must match the target cluster. `kyvernoPolicies.rules` maps every rule an exception may name to its ClusterPolicy — the upstream kyverno-policies names of the baseline and restricted Pod Security Standard policies Giant Swarm enforces by default (`host-path: disallow-host-path`, `check-seccomp-strict: restrict-seccomp-strict`, …); each exception cites the rule and its `autogen-<rule>` copy for the controller kind. A cluster that names its policies differently overrides the map; a rule with no entry fails the render. `policyExceptionNamespace` (default `policy-exceptions`) is where the exceptions live.

A cluster that enforces restricted PSS through PSA labels instead of Kyverno must also set `components.agent-sandbox.enabled: false` (see [agent-sandbox](#agent-sandbox)).

## Development

Two layers of checks, both in CI on every PR and both runnable locally.

**Render assertions** (no cluster; `test-ingress-modes`, Helm 3.17.3): `make verify-modes verify-global verify-meta verify-engine verify-self verify-managers verify-kagent-netpol verify-kagent-discovery verify-kagent-crds verify-postgres verify-secrets verify-presets verify-components verify-components-charts verify-auto verify-identity verify-wiring verify-migration` — `Makefile.custom.mk` describes each target. `verify-meta` also walks the two `values.schema.json` files against each other, nested keys included (`tests/verify-schema-symmetry.py`): every key the connectivity chart declares is settable through the meta chart, and every key the meta chart forwards is declared by the connectivity chart — a key missing on either side fails a render, at render time here or on the connectivity release on every installation. The three shapes of the chart map onto them: engine on + self on (the quick start) is `verify-self`'s; engine on + self off (the lab shape, the hand-back) is `verify-engine`'s and `verify-self`'s; engine off (the fleet: `--include-crds` renders no CRD, hook, operator or self object — the pure renderer) is `verify-engine`'s and `verify-meta`'s; the `auto` knobs — the vanilla shape without `--api-versions`, the fleet shape with the API groups passed — are `verify-auto`'s. `pre-commit run -a` regenerates the schemas and the helm-docs READMEs.

**The ATS** (`execute-chart-tests`, a required check; [tests/ats/README.md](tests/ats/README.md)) runs two scenarios on one kind cluster on a 4-vCPU `large` CircleCI machine — a cluster created from [`.ats/kind-config.yaml`](.ats/kind-config.yaml) with the `ClusterTrustBundle`, `ClusterTrustBundleProjection` and `PodCertificateRequest` feature gates Agent Substrate needs: the **smoke** — the quick start on a bare cluster (the Gateway API CRDs, a lab Dex and an in-cluster registry the candidate is pushed to, together with the connectivity chart of the checkout, which the roster is pointed at through `components.agent-platform-connectivity.{repository,versionRange,insecure}`; `helm install --wait` with the bundled engine, muster with its OAuth server, dicebear, connectivity, kagent with its CRD chart, Substrate with its CRD chart, agent-manager, self-management on against that registry), then the adoption, the auth round trip (the 401 discovery chain, a Dex user through the password grant and through muster's login flow), Substrate from the chart under test (its WorkerPool's gVisor worker Ready, the platform Harness), the agent round trips on kagent API v2 (a declarative `AgentTemplate` Ready on the platform Harness; agent-manager's `create_agent` through muster as the Dex user → the chart `1.x` HelmRelease as `kagent-flux` → the `AgentTemplate` Ready on the Harness and the agent's `RemoteMCPServer` Accepted), the self-management fixpoint and values Secret, the refused `helm upgrade`, and the ordered teardown with the line's keep policy (the `kagent-crds` release uninstalled, its CRDs and the agents' templates surviving, nothing else) — and the **own-Flux scenario**: Flux's source-controller and helm-controller from the upstream manifest, the chart through a `HelmRelease` with `components.flux.enabled: false` (no operator, no second helm-controller, the Flux CRDs' field managers untouched, an agent through that Flux on the platform Harness) and the render guard when the value is flipped on. The smoke found the fresh-install deadlock on the kagent namespace fixed in this release.

Measured (each test logs `TIMING <phase>`; CI = the 4-vCPU `large` machine with cold pulls, CircleCI job 6033 of PR #380; the per-phase table with the Substrate sizing is in [tests/ats/README.md](tests/ats/README.md#the-cluster-kind-with-agent-substrate)): **smoke** — `helm install --wait` 220 s, Substrate ready 0 s after the install returned (the install already waited for kagent behind it), the declarative `AgentTemplate` Ready on the Harness 20 s, agent-manager `create_agent` → HelmRelease Ready → `AgentTemplate` Ready + `RemoteMCPServer` Accepted 16 s, the fixpoint wait fills the rest of two 1-minute intervals after the adoption, `helm upgrade` refused in 1 s, `helm uninstall --wait` 31 s (five waves; the kagent namespace is kept); the smoke's pytest run 6:58. **functional** — the chart through the cluster's Flux 289 s, the agent through that Flux 36 s, the guard firing 51 s after the flip, the way back 105 s; the pytest run 8:54. The whole `execute-chart-tests` job: **16:55** (kind create 36 s, the ATS image pull and `uv sync` ~1 min, the two pytest runs the rest). The long pole is the two platform installs — every component image pulled once; Substrate's control plane, the worker image and the Go ADK image the Harness boots are the additions of kagent API v2.

## Credit

- [muster](https://github.com/giantswarm/muster) — Giant Swarm, Apache 2.0.
- [agentgateway](https://github.com/agentgateway/agentgateway) — agentgateway authors, Apache 2.0.
