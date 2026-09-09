[![CircleCI](https://dl.circleci.com/status-badge/img/gh/giantswarm/agent-platform/tree/main.svg?style=svg)](https://dl.circleci.com/status-badge/redirect/gh/giantswarm/agent-platform/tree/main)
[![OpenSSF Scorecard](https://api.securityscorecards.dev/projects/github.com/giantswarm/agent-platform/badge)](https://securityscorecards.dev/viewer/?uri=github.com/giantswarm/agent-platform)

# agent-platform

The Agent Platform is Giant Swarm's MCP gateway deploy unit. It ships [muster](https://github.com/giantswarm/muster) (MCP gateway / aggregator) and [agentgateway](https://github.com/agentgateway/agentgateway) (MCP data plane) together, plus the consumer-side `Gateway`, `AgentgatewayParameters`, and network policies that wire them up.

Owner: team-bumblebee.

This repo publishes **two** charts:

| Chart | What it is |
|---|---|
| `agent-platform` | the **meta-package** — an app-of-apps that renders each component and the connectivity layer as Flux `OCIRepository` + `HelmRelease`. The single thing you install. |
| `agent-platform-connectivity` | the consumer-side **wiring** the meta-package renders as a child release: the public muster route, the agentgateway data-plane `Gateway` + `AgentgatewayParameters` + `HTTPRoute`s + `BackendTrafficPolicy`s, the `NetworkPolicy`s, the kagent/klaus-gateway/model-manager/agent-manager routes, the admin-owned kagent shared resources (the shared muster `RemoteMCPServer`, `ModelConfig`s), and the CNPG `Cluster`. Agents themselves are created with the generic [`agent` chart](https://github.com/giantswarm/agent), one release per agent. |

> **CRDs are app-owned.** There is no longer a standalone `agent-platform-crds` bundle chart — each component (muster, agentgateway, kagent, agent-sandbox) ships its own CRDs in its chart's `crds/` dir and upgrades them atomically with the app via Flux `CreateReplace`. A CR consumer `dependsOn` the component that owns the CRD it needs. See [CRD lifecycle](#crd-lifecycle).

## Meta-package release flow

> Implements [giantswarm/giantswarm#36875](https://github.com/giantswarm/giantswarm/issues/36875). Concept write-up: klaus-lab `architecture/agent-platform-meta-package.md`.

The `agent-platform` chart no longer bundles its components as pinned Helm subcharts. It is an **app-of-apps meta-package**: `templates/components.yaml` renders, per entry in `.Values.components`, a Flux `OCIRepository` + `HelmRelease`. Flux is the only render engine (`gitops.engine` accepts `flux` only). It emits **only** those objects (a *pure* renderer — no raw CRs of its own) — plus, where a cluster has no Flux, the engine that reconciles them: the `flux-engine` subchart, on by default, off on a cluster that runs its own Flux (see [Installing](#installing)).

The decisive change: each component's version is a **constraint expressed as a value** (`components.<name>.versionRange`), not a `Chart.yaml` pin. Flux re-resolves the range on every reconcile, so a new component release rolls forward **with no PR to this chart and no umbrella re-package**.

- **One gitops entry.** You install the meta-package (one `OCIRepository` + `HelmRelease`). It renders each component release and the `agent-platform-connectivity` release for you.
- **CRD-before-CR ordering is preserved** — each component ships its own CRDs (app-owned CRDs), and a CR consumer `dependsOn` the component that owns the CRD it needs. A `dependsOn` reference to a component that is toggled off is dropped at render time, so an always-on consumer never blocks on a release that was never rendered. Because the connectivity CRs live in their own release (not in the meta-package's own manifest), they only apply after the CRDs are Established — the meta-package itself ships no CR that could race a CRD.
- **Per-component values keep their blocks; the on/off switch does not** — the existing `muster:`, `agentgateway:`, `kagent:`, `valkey:`, `klausGateway:`, `agentSandbox:`, `agent-platform-mcps:` blocks still drive each component's configuration, and the connectivity wiring blocks (`ingress:`, `gateway:`, `networkPolicy:`, `postgres:`, `extraObjects:`) still drive the wiring. Whether a component is installed at all is `components.<name>.enabled` — see [Enabling and disabling components](#enabling-and-disabling-components). Each `components.<name>` entry names its source block via `valuesFrom` (connectivity uses `forwardAllValues`).
- **Dev vs customer track** — keep the `components.*.versionRange` values **wide** for the internal/dogfooding track (continuous auto-update, the default). **Pin** them to exact versions for a customer **bill-of-materials**; see [`helm/agent-platform/examples/customer-bom.yaml`](helm/agent-platform/examples/customer-bom.yaml). A "product release" is that pinned values snapshot.
- **Dev channel** — `components.<name>.semverFilter` (rendered as `OCIRepository.spec.ref.semverFilter`) is a Go regexp the registry's tags must match *before* `versionRange` is evaluated. With a prerelease-admitting range (`>=3.0.0-0 <4.0.0-0`) and the filter of one branch's dev builds (`.*-dev\.<sanitized-branch>\..*`) a component follows that branch's newest build and never falls back to a stable tag; `gitops.self.semverFilter` does the same for the chart's own source under self-management. See [Dev channel (kagent API v2 line)](#dev-channel-kagent-api-v2-line).

### Enabling and disabling components

`components.<name>.enabled` is the only switch. It decides whether the component gets a release, and the render loop forwards the roster, stripped to enablement, into the `agent-platform-connectivity` release, which reads the same key path. The wiring that chart renders for a component (ClusterRoles, NetworkPolicies, routes, CRs) therefore cannot disagree with whether the component is installed. `<name>` is the `components:` key, which is also the chart name:

| Component | Key | Default |
|---|---|---|
| agentgateway controller | `components.agentgateway.enabled` | `false` |
| Valkey | `components.valkey.enabled` | `true` |
| MCP server CRs | `components.agent-platform-mcps.enabled` | `false` |
| kagent | `components.kagent.enabled` | `false` |
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

- Kubernetes ≥ 1.33 on the install target.
- Flux — **optional**. The chart brings its own engine (the Flux Operator and one `FluxInstance` running source-controller + helm-controller) where a cluster has none; a cluster that already runs Flux installs the chart through it with `components.flux.enabled: false`. See [Installing](#installing).
- Gateway API v1 CRDs (`gateways.gateway.networking.k8s.io`, `httproutes.gateway.networking.k8s.io`, `gatewayclasses.gateway.networking.k8s.io`) installed cluster-wide. The Agent Platform does **not** install them.
- No separate CRD chart to install first — every component ships its own CRDs (`AgentgatewayParameters` / `AgentgatewayPolicy` / `AgentgatewayBackend` with the agentgateway component, `MCPServer` / `Workflow` with muster, the kagent + agent-sandbox CRDs with their components). The meta-package orders each CR consumer after the CRD-owning component for you; see [CRD lifecycle](#crd-lifecycle).
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

`--wait` returns when the platform runs: Helm waits for the component `HelmRelease`s to be Ready (Helm 4 waits on custom resources' `Ready` condition), which on a fresh kind cluster takes about two minutes with the default components. The cluster-shape knobs (Kyverno, network-policy flavor, monitors, the Envoy-only avatar route) default to `auto` and follow what the cluster serves — see [Cluster shape](#cluster-shape-auto). That `helm install` is the last Helm command besides `helm uninstall`: the release now manages itself through the engine it brought — the chart rolls forward inside its major on its own, and a values change is a rewrite of Secret `agent-platform-values`, not a `helm upgrade` (which is refused) — see [Self-management](#self-management). Component versions roll forward on their own inside their `versionRange`s either way.

**The kagent namespace.** `components.kagent.enabled: true` installs the kagent chart into `kagent.namespaceOverride` (`kagent`), a namespace its own `HelmRelease` does not create (it targets the platform namespace like every component) and that the connectivity chart renders — in a release that `dependsOn` kagent. With the bundled engine the install runs one `pre-install,pre-upgrade` hook Job, `<release>-kagent-namespace` (weight -8, as the hook ServiceAccount; see [Uninstalling](#uninstalling) for the hook family), that creates the namespace when it is missing, waits for one that is still terminating to be gone, and leaves an existing one alone — so a first install on a bare cluster converges (giantswarm/agent-platform#306). The namespace is the connectivity release's from then on: it adopts it and labels it, and keeps it on uninstall (`helm.sh/resource-policy: keep` — the agents in it survive the platform's teardown, see [Uninstalling](#uninstalling)). With the engine off nothing renders here; the cluster's own Flux is given the namespace out of band (on Giant Swarm management clusters by the bases).

### The engine

`components.flux.enabled: true` (the default) makes the `flux-engine` subchart (`helm/agent-platform/charts/flux-engine`, released with this chart) part of the release. It carries the seven Flux CRDs of source-controller and helm-controller and the four Flux Operator CRDs in its `crds/`, and renders the [Flux Operator](https://fluxoperator.dev) (web UI off, no network policy; `ghcr.io/controlplaneio-fluxcd/flux-operator`, a Renovate-managed pin) plus one `FluxInstance` named `flux` in the release namespace: `distribution.version: "2.x"`, `components: [source-controller, helm-controller]`, `cluster.multitenant: true` — the multi-tenancy lockdown, under which helm-controller impersonates the ServiceAccount a `HelmRelease` names and refuses cross-namespace references. The platform's own releases therefore run as the tenant identity `agent-platform-flux` (a ServiceAccount in the release namespace bound to `cluster-admin`, rendered by the subchart; `gitops.serviceAccountName` defaults to it whenever the engine is on). The operator keeps Flux current inside the `2.x` range — controllers, CRDs and stored objects — on its own; Helm never upgrades `crds/`, and with the operator it does not have to. The Flux manifests come embedded in the operator image, so a new Flux minor arrives with a new operator tag. The subchart's values sit under `flux-engine:` (the operator image, the distribution registry for a mirror of `ghcr.io/fluxcd`, kustomize patches for the controllers); nothing needs setting for a default installation.

The engine is a value, not detection: Helm and helm-controller evaluate a dependency's `condition` before they collect `crds/`, so `false` drops CRDs and engine together — a chart that always carried Flux CRDs would be force-applied over a cluster's own by helm-controller's default CRD policy, and `crds/` are processed before any template could look at the cluster.

### Self-management

Where the chart brings the engine, the engine also holds the chart. `gitops.self.enabled` defaults to `auto` — on with `components.flux.enabled`, off without it (`true` with the engine off fails the render) — and, on, the release renders its own `OCIRepository` and `HelmRelease`, both named after the release, in the release namespace (`templates/self/`): the `OCIRepository` follows `<gitops.self.repository>/agent-platform` (default `oci://gsoci.azurecr.io/charts/giantswarm`; `gitops.self.insecure` for a plain-HTTP lab registry) at the range `>=<installed version> <next major>.0.0`, derived from the running chart at render time so a release follows patch and minor releases of its own major and never a downgrade (`gitops.self.versionRange` overrides it; `gitops.self.semverFilter` narrows the tags to a dev channel first, see [Dev channel](#dev-channel-kagent-api-v2-line); an offline `helm template` of a checkout derives it from `Chart.yaml`'s placeholder version, the released chart carries its tag); the `HelmRelease` names the release (`releaseName`, `targetNamespace`), runs as the tenant identity `agent-platform-flux` (`gitops.serviceAccountName` overrides it here too), reads its values from Secret `agent-platform-values` (`optional: false`) and sets `disableWait` (helm-controller must not wait on the chart's own `HelmRelease` objects — itself among them). The bundled helm-controller adopts the release the CLI installed with one extra revision — `helm history` shows the install and the adoption (`3.18.90+<oci digest>`, the digest is chart build metadata) and then stays put — and from then on rolls the chart forward inside its major without a `helm upgrade`: a new chart version in the registry is applied within `gitops.self.interval` (default `10m`), the new chart defaults take effect, your values survive. Measured on kind: install `--wait` 87 s, adoption in the same second the release went `deployed`, a pushed version applied 70 s after the push.

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

With the value left at `true` on such a cluster the render fails — under the Helm CLI, `helm upgrade --dry-run=server` and helm-controller alike — with `this cluster runs Flux; set components.flux.enabled=false or install the chart through it`: the guard looks the cluster up for a helm-controller `Deployment` (`app.kubernetes.io/component=helm-controller`) or a `FluxInstance` outside the release namespace. It stops a second, locked-down helm-controller from reconciling every `HelmRelease` in the cluster as the default account, which is what an engine next to a cluster's Flux does (measured). `helm template` sees no cluster and renders the engine; the guard needs a live API. One thing the guard cannot stop under helm-controller: it applies a chart's `crds/` *before* it renders the templates, so a `HelmRelease` whose value is flipped to `true` on such a cluster server-side-applies the vendored Flux CRDs — the same Flux version as the cluster's own, so their content is unchanged and only the field managers gain `helm-controller` (the ATS asserts both) — before the render fails; `upgrade.crds: Skip` on the `HelmRelease` that installs this chart avoids even that. The Helm CLI never touches `crds/` on upgrade. Two more guards: `gitops.namespace` cannot be combined with the bundled engine (the tenant identity lives in the release namespace), and `components.flux.enabled=false` is refused on an installation that runs the engine (an upgrade would delete the operator together with the `FluxInstance` it finalizes and hang — uninstall instead).

### Uninstalling

```bash
helm uninstall agent-platform --namespace agent-platform --wait --timeout 5m
```

Uninstalling the platform uninstalls its engine and everything the engine held, in order. Helm deletes a release's objects in one pass, so on its own `helm uninstall --wait` would remove the operator together with the `FluxInstance` whose finalizer it processes and hang for its timeout (measured). The chart therefore ships pre-delete hook Jobs — restricted pods running one plain `kubectl` command each in `registry.k8s.io/kubectl` (`gitops.hooks.image`), as a hook ServiceAccount bound to `cluster-admin` at weight -10 — that stop a self-management resumer still running (weight -6) and suspend the chart's own `HelmRelease` so it is deleted without uninstalling the release and cannot re-create the platform `HelmRelease`s mid-teardown, removing the hook-written values Secret with it (weight -5; these two run in `alpine/k8s`, `gitops.hooks.helmImage`, as the namespaced identity `<release>-self`, and tolerate a missing `HelmRelease`), delete the platform `HelmRelease`s by name and wait (weight 0), then delete the `FluxInstance` and wait (weight 5). The operator then uninstalls Flux **including the Flux CRDs**, and every `HelmRelease` object in the cluster goes with them: the agents' too, while their workloads, `Agent` objects and Helm storage stay behind, orphaned — the kagent namespace is kept (`helm.sh/resource-policy: keep` on the connectivity release's `Namespace`; the kagent CRDs are app-owned and Helm never deletes `crds/`), so a reinstall finds the agents where it left them. The uninstall returns in well under a minute on kind (measured 20–30 s with kagent and two agents on, 12–16 s without kagent); the four `fluxcd.controlplane.io` CRDs remain (Helm never deletes `crds/`), and a reinstall is clean. "Keep my agents running" is not something an uninstall can offer — the way to keep agents is not to uninstall. A failed hook aborts the uninstall before anything is deleted (the release is left `uninstalling`, the platform untouched); `helm uninstall --no-hooks` skips the ordering and is only safe once the `FluxInstance` is already gone. With the engine off (a cluster's own Flux) none of this applies: that Flux finalizes the platform `HelmRelease`s, the chart renders no hook and never touches it.

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
| `components.kagent.enabled` | `false` | Install the kagent controller component. |
| `components.klaus-gateway.enabled` | `false` | Install [klaus-gateway](https://github.com/giantswarm/klaus-gateway), the channel front door. |
| `agent-platform-mcps.mcpServers` | `[]` | Abstract list of MCP servers rendered into `MCPServer` / `AgentgatewayBackend` CRs. Renders nothing until populated. |
| `components.agent-sandbox.enabled` | `false` | Bundle the [agent-sandbox](https://github.com/kubernetes-sigs/agent-sandbox) controller — the Sandbox runtime kagent's `SandboxAgent` requires. See [Agent sandbox](#agent-sandbox). |
| `components.model-manager.enabled` | `false` | Install [model-manager](https://github.com/giantswarm/model-manager), model inventory / pull / load / unload / delete and kagent `ModelConfig` wiring on an Ollama, Lemonade Server (FastFlowLM on AMD NPUs) or KServe backend, as REST and MCP (`x_model-manager_*` through muster). See [Model manager and agent manager](#model-manager-and-agent-manager). |
| `components.agent-manager.enabled` | `false` | Install [agent-manager](https://github.com/giantswarm/agent-manager), the agent write surface: create / update / delete / inspect kagent agents as Flux `HelmRelease`s of the agent chart, as REST and MCP (`x_agent-manager_*` through muster). Needs kagent and Flux. See [Model manager and agent manager](#model-manager-and-agent-manager). |
| `modelManager.route.enabled`, `agentManager.route.enabled` | `false` | Expose the service's REST API on the agentgateway data plane at `https://agentgateway.<domain>/model-manager` / `/agent-manager` (the portal's path), with an optional JWT policy in front (`…route.jwtAuthentication`). `agentgateway-*` modes only. |
| `dicebear.route.parentRefs` | `[]` | **Required.** The public `Gateway`(s) the avatar `HTTPRoute` attaches to — typically the same as `ingress.parentRefs`. The [dicebear](https://github.com/giantswarm/dicebear) avatar renderer is a force-enabled component (deployed on every install) and its public route is on by default, so the child release fails to render if this is empty. See [giantswarm/giantswarm#37211](https://github.com/giantswarm/giantswarm/issues/37211). |
| `dicebear.route.hostnames` | `[]` | **Required.** Hostname(s) the avatar endpoint is served on, e.g. `avatars.<domain>`. Consumers fetch `https://<hostname>/v1/<agent-name>.png`. |
| `muster.muster.oauth.server.enabled` | `true` | OAuth resource-server protection on the muster API. Requires `baseUrl`, `dex.{issuerUrl,clientId}`, and a Secret carrying `dex-client-secret` / `registration-token` / `oauth-encryption-key` / `valkey-password`. |
| `muster.muster.oauth.server.storage.type` | `valkey` | Muster storage backend default. Pairs with `components.valkey.enabled: true`; flip to `memory` for dev. |
| `muster.muster.oauth.server.storage.valkey.url` | `muster-valkey:6379` | Bundled-valkey Service. Override to point at an out-of-band Valkey. |

Full schema: [`helm/agent-platform/values.schema.json`](./helm/agent-platform/values.schema.json).

### Model manager and agent manager

Two platform services, off by default, each a component with the same shape: a component release (`components.model-manager` / `components.agent-manager`, chart values in the `model-manager:` / `agent-manager:` blocks, forwarded verbatim) plus the wiring the connectivity chart renders for it from the `modelManager:` / `agentManager:` blocks — the agentgateway route and JWT policy (`route.*`, the same shape as `kagent.controllerRoute`), the network policies in both flavors (ingress from the data plane, muster and the kubelet's probes; egress to DNS, the Kubernetes API, the identity provider and the service's own backends), and render-time guards that fail the install with an actionable message instead of shipping a service that cannot work.

- **[model-manager](https://github.com/giantswarm/model-manager)** manages models for the agents: inventory, pull, load/unload, delete, and the kagent `ModelConfig` wiring. `model-manager.backend` selects the serving driver — `ollama` proxies an Ollama reached at `model-manager.ollama.endpoint` (required, as reached from pods), `lemonade` proxies a [Lemonade Server](https://lemonade-server.ai) — FastFlowLM on AMD Ryzen AI NPUs, llama.cpp on GPU/CPU — reached at `model-manager.lemonade.endpoint` (required likewise; Lemonade listens on 13305 by default; its models are wired with kagent's `OpenAI` provider against `<endpoint>/api/v1`), `lmstudio` proxies an [LM Studio](https://lmstudio.ai) — llama.cpp on GPU/CPU, MLX on Apple silicon — reached at `model-manager.lmstudio.endpoint` (required likewise; LM Studio listens on 1234 by default and needs 0.4.0 or newer; its models are wired with kagent's `OpenAI` provider against `<endpoint>/v1`, and it serves no delete, so that call answers `501 unsupported`), `kserve` manages `InferenceService`s on a KServe the cluster already has (this meta-package does not bundle it; the render fails while the `serving.kserve.io` API is missing unless `modelManager.kserve.requireApi: false`). ModelConfigs land in `model-manager.kagent.namespace`, which must be the kagent component's namespace; without kagent set `model-manager.kagent.disableWiring: true`.
- **[agent-manager](https://github.com/giantswarm/agent-manager)** is the agent write surface: it composes a kagent agent the way the portal's create flow does — a Flux `HelmRelease` of the [agent chart](https://github.com/giantswarm/agent) plus the shared per-namespace `OCIRepository` — validates the values against the chart's `values.schema.json` before applying, and reads agents back from the `Agent`, its `HelmRelease` and the workload. It needs the kagent component and Flux's helm and source controllers; the HelmReleases it writes execute as the `kagent-flux` tenant identity the connectivity chart renders (`kagent.fluxServiceAccountName`, see [Tenant identity](#tenant-identity)). An agent whose `HelmRelease` a GitOps Kustomization owns is reported as `managed: gitops` and refused for update/delete unless forced.

Both register their MCP endpoint with muster through the chart's own `MCPServer` CR (tools appear as `x_model-manager_*` / `x_agent-manager_*`), and both **act as the user, not as a ServiceAccount**: `oauth.enabled` makes the service an OAuth 2.1 resource server in front of its MCP endpoint and REST API, muster forwards the session's IdP id_token (the CR's `auth.forwardToken`) and requests `requiredAudiences` at login — the cross-client audience the kube-apiserver trusts (`dex-k8s-authenticator` on Giant Swarm clusters) — so `oauth.downstream` presents the same token to the Kubernetes API and the user's RBAC governs; the ServiceAccount holds no permissions (the charts render no RBAC). The issuer, client, secret and base URL fall back to `global.identity` / `global.domain` inside the charts; an installation that sets no `global.*` names them in the block (`oauth.baseURL`, `oauth.dex.issuerURL`, `oauth.dex.clientID`, `oauth.existingSecret` with the key `dex-client-secret` — the same Dex client muster logs users in with — and that client in `oauth.trustedAudiences`). The guards fail the render when any of them is missing. The cilium network policies open the service's egress to that provider by name: the Dex issuer host, or for `oauth.provider: google` the three Google hosts its discovery, JWKS/userinfo and token endpoints live on (`accounts.google.com`, `www.googleapis.com`, `oauth2.googleapis.com`); further destinations go in `modelManager.networkPolicy.egress` / `agentManager.networkPolicy.egress`.

`make verify-managers` covers the wiring, both flavors and every guard.

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
| `kagent.kagent.*`, `agentgateway.agentgateway.*` (nested subcharts) | `kagent.*`, `agentgateway.*` (the flattened 0.2.x / 2.x charts) |
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
| `agents.kagent.dev`, `modelconfigs…`, `remotemcpservers…`, `toolservers…`, `sandboxagents…`, … plus the `kmcp` CRDs | the **kagent** component chart (`giantswarm/kagent` GS wrapper) |
| `sandboxes.agents.x-k8s.io`, `sandboxtemplates…`, `sandboxclaims…`, `sandboxwarmpools.extensions.agents.x-k8s.io` | the **agent-sandbox** component chart (`giantswarm/agent-sandbox`) |
| `inferenceservices.serving.kserve.io`, `servingruntimes…`, `clusterservingruntimes…`, `clusterstoragecontainers…`, `inferencegraphs…`, `trainedmodels…` | the **kserve-crd** component chart (`giantswarm/kserve`) — as templates, not `crds/`; `keep`-annotated. Off by default. |
| `llminferenceservices.serving.kserve.io`, `llminferenceserviceconfigs…` | the **kserve-llmisvc-crd** component chart (`giantswarm/kserve`) — as templates; `keep`-annotated. Off by default. |
| `clusters.postgresql.cnpg.io`, `backups…`, `scheduledbackups…`, `poolers…`, `databases…`, … | the **cloudnative-pg** component chart (upstream, `crds.create`) — as templates. Off by default; a management cluster runs the CNPG operator as its own app. |

Each component that ships a `crds/` dir sets `crds: CreateReplace` on its `HelmRelease` (rendered by the meta-package), so Flux applies and upgrades those CRDs atomically with the app at the same resolved version — Helm on its own never upgrades `crds/`-dir CRDs. The kserve and cloudnative-pg charts render their CRDs as ordinary templates instead, so their `HelmRelease`s carry no `crds:` policy and Helm applies and upgrades them with every release. All these CRDs carry `helm.sh/resource-policy: keep` (CNPG's through the operator chart's own handling), so they survive a component uninstall (the CRs are never cascade-deleted).

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

The connectivity chart renders four `kyverno.io` objects: two ClusterPolicies that give the bundled declarative agents their securityContext, a PolicyException that lifts the seccomp restriction those agents need, and the agent-sandbox pod-security ClusterPolicy. On a cluster with no Kyverno they would fail the install on an unknown API group, so `kyvernoPolicies.enabled: auto` (the default) renders none of the four where `kyverno.io/v1` is not served; `false` forces that, `true` forces them on.

The PolicyException targets a policy this chart does not own, so its names must match the target cluster. They default to the Giant Swarm names (`policyExceptionNamespace: policy-exceptions`, `seccompPolicyName: restrict-seccomp-strict`, `seccompRuleNames`); a cluster that names its policies differently must override them, or the exception matches nothing.

A cluster that enforces restricted PSS through PSA labels instead of Kyverno must also set `components.agent-sandbox.enabled: false` (see [agent-sandbox](#agent-sandbox)).

## Development

Two layers of checks, both in CI on every PR and both runnable locally.

**Render assertions** (no cluster; `test-ingress-modes`, Helm 3.17.3): `make verify-modes verify-global verify-meta verify-engine verify-self verify-managers verify-kagent-netpol verify-postgres verify-secrets verify-presets verify-components verify-components-charts verify-auto verify-identity verify-wiring` — `Makefile.custom.mk` describes each target. `verify-meta` also walks the two `values.schema.json` files against each other, nested keys included (`tests/verify-schema-symmetry.py`): every key the connectivity chart declares is settable through the meta chart, and every key the meta chart forwards is declared by the connectivity chart — a key missing on either side fails a render, at render time here or on the connectivity release on every installation. The three shapes of the chart map onto them: engine on + self on (the quick start) is `verify-self`'s; engine on + self off (the lab shape, the hand-back) is `verify-engine`'s and `verify-self`'s; engine off (the fleet: `--include-crds` renders no CRD, hook, operator or self object — the pure renderer) is `verify-engine`'s and `verify-meta`'s; the `auto` knobs — the vanilla shape without `--api-versions`, the fleet shape with the API groups passed — are `verify-auto`'s. `pre-commit run -a` regenerates the schemas and the helm-docs READMEs.

**The ATS** (`execute-chart-tests`, a required check; [tests/ats/README.md](tests/ats/README.md)) runs two scenarios on one kind cluster on a 2-vCPU CircleCI machine: the **smoke** — the quick start on a bare cluster (the Gateway API CRDs, a lab Dex and an in-cluster registry the candidate is pushed to, together with the connectivity chart of the checkout, which the roster is pointed at through `components.agent-platform-connectivity.{repository,versionRange,insecure}`; `helm install --wait` with the bundled engine, muster with its OAuth server, dicebear, connectivity, kagent and agent-manager, self-management on against that registry), then the adoption, the auth round trip (the 401 discovery chain, a Dex user through the password grant and through muster's login flow), the agent round trips (a declarative `Agent` Ready; agent-manager's `create_agent` through muster → a `HelmRelease` of the agent chart executed as `kagent-flux` → the `Agent` Ready), the self-management fixpoint and values Secret, the refused `helm upgrade`, and the ordered teardown — and the **own-Flux scenario**: Flux's source-controller and helm-controller from the upstream manifest, the chart through a `HelmRelease` with `components.flux.enabled: false` (no operator, no second helm-controller, the Flux CRDs' field managers untouched, an agent through that Flux) and the render guard when the value is flipped on. The smoke found the fresh-install deadlock on the kagent namespace fixed in this release.

Measured (each test logs `TIMING <phase>`; local = a 24-core kind node with warm image caches, CI = the 2-vCPU `medium` machine with cold pulls): **smoke** — prerequisites 4 s, registry + push 3 s, `helm install --wait` 97–102 s local / 74 s CI, adoption 5–11 s after the install returned, muster healthy 0 s, the auth round trips 1 s, the declarative Agent Ready 5–10 s, agent-manager `create_agent` → HelmRelease Ready → Agent Ready 6 s local and CI, the fixpoint wait fills the rest of two 1-minute intervals after the adoption, `helm upgrade` refused in 1 s, `helm uninstall --wait` 22 s local / 21 s CI (the kagent namespace is kept, so no namespace termination is waited for; 12–16 s without kagent); the smoke's pytest run 4:30 local / 4:22 CI. **functional** — `flux install` 19 s, the chart through the cluster's Flux 112–127 s local / 132 s CI, the agent through that Flux 36 s, the guard firing 16 s local / 52 s CI after the flip, the way back 63–106 s local / 64 s CI; the pytest run 4:46 local / 5:41 CI. The whole `execute-chart-tests` job: **11:09** (kind create 37 s, the ATS image pull and `uv sync` ~1 min, the two pytest runs ~10 min). The long pole of the job is the two platform installs on two cores — every component image pulled once, kagent's controller and bundled Postgres the slowest to become Ready.

## Credit

- [muster](https://github.com/giantswarm/muster) — Giant Swarm, Apache 2.0.
- [agentgateway](https://github.com/agentgateway/agentgateway) — agentgateway authors, Apache 2.0.
