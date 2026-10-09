# Operator reference

How the Agent Platform charts are installed, configured and operated. New here? Start with the [README](../README.md) and the [installation guide](install.md). Every value is documented in [`values.yaml`](../helm/agent-platform/values.yaml); this page explains the ones that need more than a comment.

## How the chart works

To install, follow [docs/install.md](install.md). It covers cluster shapes, prerequisites, the identity provider and the `global.*` inputs.

`agent-platform` is an app-of-apps chart. For each enabled `components.<name>` entry it renders a Flux `OCIRepository` and a `HelmRelease`. `agent-platform-connectivity` is one of those entries: it holds the wiring (routes, gateway, network policies, kagent catalog, Postgres). Agents are separate releases of the [`agent` chart](https://github.com/giantswarm/agent).

- **Versions are ranges.** `components.<name>.versionRange` is re-resolved on every reconcile, so components roll forward without a chart release. For a customer bill-of-materials, pin exact versions: [`examples/customer-bom.yaml`](../helm/agent-platform/examples/customer-bom.yaml).
- **Connectivity matches the chart.** `agent-platform-connectivity` is released from the same tag (`releasedWithChart: true`), so its version is this chart's version.
- **CRDs come with their components.** A consumer `dependsOn` the component that owns the CRDs it uses. A dependency on a disabled component is dropped. See [CRD lifecycle](#crd-lifecycle).
- **Values.** Each component reads its own values block, named by `components.<name>.valuesFrom`.

### Dev channel

Every branch push publishes both charts as `X.Y.Z-r<branch-hash>t<YYYYMMDDHHMMSS>h<sha7>`. Component repositories, the kagent line included, publish dev builds in the same shape. To follow a branch:

- Set `components.<name>.semverFilter` to `^.*-r<branch-hash>t[0-9]{14}h[0-9a-f]{7}$`. Combine it with a range that admits prereleases, such as `>=3.0.0-0 <4.0.0-0`.
- Set `gitops.self.semverFilter` to do the same for the chart's own source.
- Get `<branch-hash>` from `gitsemver branch-hash <branch>`.
- Or pin the exact dev tag as `versionRange`, with no filter.

By default there is no filter and no prerelease.

### Enabling and disabling components

`components.<name>.enabled` is the only switch. The connectivity release receives the same roster, so its wiring always matches what is installed.

| Component | Key | Default |
|---|---|---|
| muster | `components.muster.enabled` | `true` |
| dicebear (avatars) | `components.dicebear.enabled` | `true` |
| agentgateway controller | `components.agentgateway.enabled` | `true` |
| Valkey | `components.valkey.enabled` | `true` |
| model-manager | `components.model-manager.enabled` | `true` |
| bundled Flux engine (feature switch) | `components.flux.enabled` | `true` |
| MCP server CRs | `components.agent-platform-mcps.enabled` | `false` |
| kagent | `components.kagent.enabled` | `false` |
| kagent CRDs, Agent Substrate and its CRDs | `components.kagent-crds.enabled`, `components.substrate.enabled`, `components.substrate-crds.enabled` | follow `components.kagent` |
| klaus-gateway | `components.klaus-gateway.enabled` | `false` |
| agent-sandbox | `components.agent-sandbox.enabled` | `false` |
| agent-manager | `components.agent-manager.enabled` | `false` |
| vm-manager | `components.vm-manager.enabled` | `false` |
| cluster-manager | `components.cluster-manager.enabled` | `false` |
| Backstage | `components.backstage.enabled` | `false` |
| mcp-kubernetes | `components.mcp-kubernetes.enabled` | `false` |
| CloudNativePG operator | `components.cloudnative-pg.enabled` | `false` |
| LLMInferenceService CRDs | `components.kserve-llmisvc-crd.enabled` | `false` |
| LLMInferenceService controller | `components.kserve-llmisvc-resources.enabled` | `false` |
| Well-known LLMInferenceServiceConfigs | `components.kserve-runtime-configs.enabled` | `false` |
| Model serving (feature switch) | `components.modelServing.enabled` | `false` |
| NVIDIA GPU operator | `components.gpu-operator.enabled` | `false` |
| Gateway API CRDs | `components.gateway-api-crds.enabled` | `false` |

Notes on the table:

- `agent-platform-connectivity` has no switch and is always installed.
- `flux` and `modelServing` have no chart. `flux` controls the `flux-engine` subchart. `modelServing` controls the serving objects that connectivity renders.
- If kagent is on, you cannot set the kagent CRDs or Substrate to `false`. The render fails.

## Prerequisites

The per-shape details are in [install.md](install.md#2-prerequisites).

- **Kubernetes ≥ 1.35.** kagent also needs the `ClusterTrustBundle`, `ClusterTrustBundleProjection` and `PodCertificateRequest` feature gates. See [Agent Substrate](#agent-substrate). Self-management needs Kubernetes 1.30 or later.
- **Gateway API v1 CRDs.** Install them, or set `components.gateway-api-crds.enabled: true`.
- **Flux is optional.** The chart brings its own engine. On a cluster that already runs Flux, set `components.flux.enabled: false`.
- **Kyverno and Cilium are optional.** `kyvernoPolicies.enabled` and `networkPolicy.flavor` default to `auto` and follow what the cluster serves. See [Cluster shape](#cluster-shape-auto).
- **Cilium with kube-proxy replacement.** With kagent on, set `socketLB.hostNamespaceOnly: true`.
- **cert-manager.** Needed only for `components.kserve-llmisvc-resources`.

## Installing

Run `helm install --wait` on a cluster without Flux. The result is a running platform. The command and example values are in [install.md §4](install.md#4-install).

### The engine

With `components.flux.enabled: true`, the `flux-engine` subchart installs the following:

- The Flux Operator.
- One `FluxInstance` named `flux` (Flux `2.x`, running source-controller and helm-controller, with `cluster.multitenant: true`).
- The tenant ServiceAccount `agent-platform-flux`, bound to `cluster-admin`. Platform releases run as this account.

The operator keeps Flux current within `2.x`. A pre-install/pre-upgrade hook re-applies the operator's CRDs on every upgrade. Engine settings go under `flux-engine:`, for example a mirror for `instance.distribution.registry`. A default install needs no settings. See the [flux-engine README](../helm/agent-platform/charts/flux-engine/README.md).

### Self-management

When the engine is on, the chart also manages itself. `gitops.self.enabled: auto` follows `components.flux.enabled`.

- **What it renders.** The release renders its own `OCIRepository` and `HelmRelease`, which follow `<gitops.self.repository>/agent-platform`.
- **Version range.** By default the range is `>=<installed> <next major>.0.0`. Override it with `gitops.self.versionRange`.
- **Update timing.** New patch and minor versions apply within `gitops.self.interval` (`10m`). Your values carry over.
- **First adoption.** At install time the self `HelmRelease` is created suspended. A `<release>-self-resume` Job resumes it once the install is `deployed`. Because of this, `helm template` output shows `suspend: true`.

**The Helm CLI is for day 0 only.** After the install, a `ValidatingAdmissionPolicy` (`<release>-self-managed-<namespace>`) refuses `helm upgrade` and `helm rollback`. The refusal happens before Helm writes a revision, and the error message says what to do instead. `helm uninstall` still works.

**On day 2, change values through the Secret.** Secret `agent-platform-values` (key `values.yaml`) holds the release's user-supplied values. Rewrite it with your complete values file. A partial file resets everything it leaves out to the chart defaults.

```bash
kubectl -n agent-platform create secret generic agent-platform-values \
  --from-file=values.yaml=values.yaml --dry-run=client -o yaml \
  | kubectl apply --server-side --force-conflicts -f -
```

To move to the next major version, set `gitops.self.versionRange` (for example `">=4.0.0 <5.0.0"`) in that file.

**Escape hatch: hand the release back to the Helm CLI.**

```bash
kubectl annotate namespace agent-platform agent-platform.giantswarm.io/helm-cli=allow
helm upgrade agent-platform oci://gsoci.azurecr.io/charts/giantswarm/agent-platform \
  --namespace agent-platform -f values.yaml --set gitops.self.enabled=false --force-conflicts --wait
```

Keep `gitops.self.enabled: false` in your values file afterwards. Otherwise the next upgrade puts the release back under self-management.

**Traps.**

- **Failed install.** If an install fails, the self `HelmRelease` stays suspended. Either `helm uninstall --wait` and reinstall, or write the Secret yourself and patch the `HelmRelease` with `spec.suspend: false`.
- **Rollback.** Never run `helm rollback` on a self-managed release.
- **Testing unreleased charts.** Set `gitops.self.enabled: false`. Otherwise the published chart replaces the one under test.

### Clusters that run Flux

On a cluster that already runs Flux (every Giant Swarm management cluster), set `components.flux.enabled: false` and install the chart through that Flux. No engine object, hook or tenant identity is rendered, and self-management is off.

```yaml
apiVersion: source.toolkit.fluxcd.io/v1
kind: OCIRepository
metadata: { name: agent-platform, namespace: flux-giantswarm }
spec:
  interval: 1h
  url: oci://gsoci.azurecr.io/charts/giantswarm/agent-platform
  layerSelector: { mediaType: application/vnd.cncf.helm.chart.content.v1.tar+gzip, operation: copy }
  ref: { semver: ">=1.0.0" }   # pin a tag for a customer release
---
apiVersion: helm.toolkit.fluxcd.io/v2
kind: HelmRelease
metadata: { name: agent-platform, namespace: flux-giantswarm }
spec:
  interval: 10m
  timeout: 12m          # the 5m default is too short for a first install with kagent
  chartRef: { kind: OCIRepository, name: agent-platform }
  install: { createNamespace: true }
  values:
    components: { flux: { enabled: false } }
    gitops:
      namespace: flux-giantswarm       # exempt from the tenancy policy
      targetNamespace: agent-platform
  valuesFrom: [{ kind: Secret, name: agent-platform-values }]
```

The render refuses these cases:

- `components.flux.enabled: true` on a cluster where it finds a foreign helm-controller or `FluxInstance`. The error is `this cluster runs Flux; set components.flux.enabled=false or install the chart through it`.
- `gitops.namespace` combined with the bundled engine.
- Switching the engine off on an installation that runs it. Uninstall instead.

On upgrade, helm-controller applies `crds/` before the guard runs. Set `upgrade.crds: Skip` to avoid that.

**Uninstalling through that Flux.**

1. Delete the agents' `HelmRelease`s first, while the kagent controller still runs.
2. Delete the chart's `HelmRelease`. The component releases are then removed concurrently.

The CRD charts keep their CRDs, so this order is safe. What survives is the same as in [Uninstalling](#uninstalling).

### Uninstalling

```bash
helm uninstall agent-platform --namespace agent-platform --wait --timeout 5m
```

Hook Jobs tear the platform down in order, using `gitops.hooks.image` (`gsoci.azurecr.io/giantswarm/kubectl`) and `gitops.hooks.helmImage` (`gsoci.azurecr.io/giantswarm/alpine-k8s`):

1. Suspend and delete the self `HelmRelease`.
2. Delete the platform `HelmRelease`s in reverse dependency order.
3. Delete the `FluxInstance`, then the operator. The operator removes Flux and its CRDs, so every `HelmRelease` in the cluster goes too, agents included.

**What stays behind:**

- the kagent namespace, with the agents' `AgentTemplate`s and `RemoteMCPServer`s
- the `api.kagent.dev` and `ate.dev` CRDs
- Substrate's `ate-system` and `podcertificate-controller-system` namespaces and the two `podcert.ate.dev` `ClusterTrustBundle`s
- the four `fluxcd.controlplane.io` CRDs

A reinstall reuses all of these. For a clean slate, run `kubectl delete namespace ate-system podcertificate-controller-system` and delete those two bundles. To keep your agents running, do not uninstall.

**Recovery.**

- **Failed pre-delete hook.** The release is left untouched. Retry `helm uninstall`.
- **After `--no-hooks`.** The engine keeps running. Delete the `FluxInstance`, then the operator's `Deployment`, `ServiceAccount` and `ClusterRoleBinding` (`agent-platform-flux-operator`).

**Model serving teardown.** While `kserve-llmisvc-resources` and `kserve-runtime-configs` are on, a `<release>-serving-teardown` hook removes the llm-d controller, then the `LLMInferenceServiceConfig`s. It runs on uninstall and when the slice is switched off. You cannot turn `kserve-runtime-configs` off while `kserve-llmisvc-resources` stays on. With `gitops.target.kubeConfig`, no hook renders: tear the slice down in the target cluster in that same order.

### Helm versions

Helm 4 is required for `helm install --wait` to wait on the component `HelmRelease`s. Helm 3 returns before they are Ready. CI renders with Helm 3.17.3. Installing through a cluster's own Flux (helm-controller 1.5 or later) is verified on every release.

## Configuration

These are the main non-component settings. Component switches are in [Enabling and disabling components](#enabling-and-disabling-components). The full list is in the [chart README](../helm/agent-platform/README.md#values) and [`values.schema.json`](../helm/agent-platform/values.schema.json).

| Key | Default | Purpose |
|---|---|---|
| `global.domain` | `""` | Base domain. The public hostnames (`muster.`, `kagent.`, `agentgateway.`) derive from it. |
| `global.identity.{issuerUrl,clientId,existingSecret}` | `""` | The platform's one OIDC provider. See [install.md §2](install.md#the-identity-provider). |
| `global.gatewayApi.parentRefs` | `[]` | The public Gateway that routes attach to by default. |
| `global.registry` | `gsoci.azurecr.io` | Container image registry. |
| `ingress.mode` | `agentgateway-muster` | Request topology. `muster-direct` is deprecated. `agentgateway-direct` is not supported. See [Ingress topology](#ingress-topology). |
| `ingress.parentRefs` / `ingress.hostnames` | `[]` | Overrides for muster's route. Empty means `global.gatewayApi.parentRefs` (or the chart-owned edge) and `muster.<global.domain>`. |
| `ingress.backendTrafficPolicy.enabled` | `false` | Route-scoped `BackendTrafficPolicy` (keeps `WWW-Authenticate`, sets `requestTimeout: 0s`). |
| `gateway.name` / `gateway.gatewayClassName` | `agentgateway` | Name and class of the data-plane `Gateway`. |
| `gateway.listeners` | `[{name: http, port: 8080, protocol: HTTP}]` | Data-plane listeners. |
| `gateway.parameters.serviceType` | `ClusterIP` | Data-plane Service type. |
| `networkPolicy.enabled` | `true` | Master switch for network policies. |
| `networkPolicy.flavor` | `auto` | `cilium` where `cilium.io/v2` is served, otherwise `kubernetes`. muster and Valkey follow. |
| `kyvernoPolicies.enabled` | `auto` | Renders the Kyverno objects where `kyverno.io/v1` is served. |
| `global.observability.metrics.serviceMonitor.enabled` | `auto` | Renders monitors where `monitoring.coreos.com/v1` is served. See [Observability](#observability). |
| `agentManager.route.enabled` | `false` | Exposes agent-manager's REST API at `https://agentgateway.<domain>/agent-manager`. |
| `kagent.controllerRoute.enabled` | `false` | Exposes the kagent controller's gRPC API as a `GRPCRoute` on the data plane. The route requires a JWT (`Strict`, on by default). See [Authentication](authentication.md#5-the-kagent-controller-route). |
| `dicebear.route.enabled` | `auto` | The avatar route. Renders where `gateway.envoyproxy.io/v1alpha1` is served. When it renders, `dicebear.route.parentRefs` and `dicebear.route.hostnames` (for example `avatars.<domain>`) are required. |
| `muster.muster.oauth.server.enabled` | `true` | OAuth protection on the muster API. Storage defaults to the bundled Valkey (`muster-valkey:6379`). |
| `agent-platform-mcps.mcpServers` | `[]` | MCP servers rendered as `MCPServer` / `AgentgatewayBackend` CRs. |
| `extraObjects` | `[]` | Extra manifests, rendered through `tpl`. |
| `gitops.hooks.image` / `gitops.hooks.helmImage` | `gsoci.azurecr.io/giantswarm/kubectl` / `alpine-k8s` | Images for the hook Jobs. |

## Components

### Model manager and agent manager

Each one is a component release. `components.model-manager` is on by default and `components.agent-manager` is off. The chart values go in the `model-manager:` / `agent-manager:` blocks and are forwarded verbatim. The connectivity chart renders the wiring from the `modelManager:` / `agentManager:` blocks:

- `route.*`: an optional path-prefixed `HTTPRoute` on the agentgateway data plane, with an optional JWT policy.
- `networkPolicy.*`: network policies in both flavours.
- Render guards that fail the install, with a message, when an input is missing.

**[model-manager](https://github.com/giantswarm/model-manager)** manages models: inventory, pull, load/unload, delete, and the kagent `ModelConfig` wiring.

- `model-manager.backend` selects the driver: `ollama`, `lemonade`, `lmstudio` or `kserve`. Each driver except `kserve` needs its `model-manager.<backend>.endpoint`, as reached from pods.
- `kserve` needs the `serving.kserve.io` API on the cluster. The render fails without it unless `modelManager.kserve.requireApi: false`.
- Backends can also be registered at runtime (the `add_backend` tool, the portal, cluster-manager). List their destinations in `modelManager.networkPolicy.registeredBackends`, one `{cidr, port}` or (cilium only) `{fqdn, port}` per backend. Otherwise the egress policy blocks them.
- ModelConfigs land in `model-manager.kagent.namespace`, which must be the kagent component's namespace. Without kagent, the meta chart sets `model-manager.kagent.disableWiring: true` by itself.

**[agent-manager](https://github.com/giantswarm/agent-manager)** is the agent write surface. It writes each agent as a Flux `HelmRelease` of the [agent chart](https://github.com/giantswarm/agent) and validates the values against the chart's schema first.

- It needs the kagent component and Flux's helm and source controllers.
- The HelmReleases it writes run as the `kagent-flux` tenant identity (`kagent.fluxServiceAccountName`, see [Tenant identity](#tenant-identity)).
- It refuses to update or delete an agent owned by a GitOps Kustomization unless forced.

Both services register with muster through their own `MCPServer` CR, so their tools appear as `x_model-manager_*` / `x_agent-manager_*`. Both **act as the user**:

- muster forwards the user's id_token.
- The service presents that token to the Kubernetes API.
- The user's RBAC decides. The ServiceAccount holds no permissions.

The OAuth issuer, client, secret and base URL come from the block, else from `global.identity` / `global.domain`. Anything still unset is filled from muster's OAuth server block (`muster.muster.oauth.server.dex.*`, `.existingSecret`). The render fails if anything is still missing. Further egress destinations go in `modelManager.networkPolicy.egress` / `agentManager.networkPolicy.egress`.

`make verify-managers` covers the wiring and the guards.

### Cluster manager

**[cluster-manager](https://github.com/giantswarm/cluster-manager)** is the cluster write surface. It lists clusters and node pools, creates and deletes GPU node pools, and switches model serving on a cluster.

- Every write takes `dryRun` and `mode: apply | commit`. `commit` writes a pull request into the cluster's GitOps repository.
- MCP is its only surface: no REST API and no route. The portal calls `x_cluster-manager_*` through muster as the signed-in user.
- Where a cluster has none, it composes [the GPU operator](#the-gpu-operator) and the serving slice. It registers the cluster's `kserve` backend with model-manager.

Configuration:

- `components.cluster-manager` is off by default and `dependsOn: [muster]`. The chart values go in the `cluster-manager:` block. The connectivity wiring goes in `clusterManager:`.
- Egress to the workload clusters' API servers is `clusterManager.networkPolicy.workloadClusters`: FQDN patterns (cilium) or CIDRs, on ports 443 and 6443.
- It acts as the person, like the managers above. The ServiceAccount has no RBAC.
- `cluster-manager.installation.name` names the installation's own `Cluster`.
- `cluster-manager.modelManager.namespace` is derived from the platform's namespace. A different value fails the render.
- The connectivity chart ships the `PriorityClass` `agent-platform-prewarm-placeholder` that prewarmed pools use (`clusterManager.prewarmPriorityClass`). See the connectivity chart README, [The prewarm placeholder's PriorityClass](../helm/agent-platform-connectivity/README.md#the-prewarm-placeholders-priorityclass).

`make verify-cluster-manager` covers it.

### The kagent line

`components.kagent.enabled: true` installs **kagent API v2** (`api.kagent.dev/v1alpha3`). Agents run as [Agent Substrate](#agent-substrate) actors in gVisor worker pods. The build comes from the line Giant Swarm maintains, [giantswarm/kagent-upstream](https://github.com/giantswarm/kagent-upstream) (branch `giantswarm`, ledger in `FORK.md`). It publishes the charts `kagent` and `kagent-crds` to `oci://gsoci.azurecr.io/giantswarm/kagent/helm`, under the line's own semver.

- **Two components.** `components.kagent` and `components.kagent-crds`. `kagent-crds`, `substrate-crds` and `substrate` follow `components.kagent` unless switched explicitly. The render refuses kagent without them.
- **One version range.** `components.kagent.versionRange` and `components.kagent-crds.versionRange` are always equal. The range shape is `>=X.Y.Z <X.(Y+1).0`, with no `-0`, because a prerelease bound makes Flux consider dev builds.
- **Re-pinning.** A new kagent release is a values change: move both floors to the release's version. Take it from [`builds.md`](https://github.com/giantswarm/kagent-upstream/blob/ledger/builds.md) on the fork's `ledger` branch. Move `components.substrate*` too when the line's own Substrate pin moved.
- **Image references travel inside the chart.** The controller and UI tag and the Go ADK digest of the platform `Harness` are stamped into the chart at publish time. The meta chart forwards none of them.
  - `kagent.harness.image` is an override, by digest. It is forwarded only when set.
  - `kagent.substrateWorkerPool.workerImage` is **derived from the floor of `components.substrate.versionRange`**, never taken from the kagent chart's stamp. An own value must carry that tag, or the render fails. See the meta chart README, [The worker image follows the chart's Substrate pin](../helm/agent-platform/README.md#the-worker-image-follows-the-charts-substrate-pin-giantswarmagent-platform466).
- **The `kagent:` block** uses upstream's chart shape, keys at the root. The connectivity keys are dropped before the values reach the kagent release (`components.kagent.omitKeys`): `controllerRoute`, `controller.vpa`, `fluxServiceAccountName`, `harness.snapshotStore`, `modelConfigs`, `oauth2ProxyIngress`, `remoteMcpServers`, `uiRoute`, `substrateWorkerPool.podDisruptionBudget`.
- **Drift detection.** The kagent, muster and kserve-runtime-configs releases set `spec.driftDetection.mode: enabled` (`components.<name>.driftDetection`). helm-controller re-applies anything that drifted on every reconcile. An object can opt out with `helm.toolkit.fluxcd.io/driftDetection: disabled`. If you turn on `muster.autoscaling.enabled`, add an `ignore` rule for the Deployment's `spec.replicas`.

`make verify-meta`, `make verify-components-charts` and `make verify-kagent-crds` hold the pin and the forwarded blocks.

**Private skill repositories.** The credential belongs to each source on the `AgentTemplate` (`spec.skills[].source.git.credentialRef`, and the same under `spec.plugins[]`), never on the `Harness`.

- The Secret lives in the template's namespace. Its key holds `base64("<username>:<token>")`; on GitHub that is `x-access-token:<token>`.
- The source URL must be `https://`.
- Use one credential per host. The label `ui.giantswarm.io/agent-skills-git-auth: "true"` makes the portal offer the Secret.
- A namespace other than `kagent` also needs an entry in `substrate.credentialProvider.namespacePolicies`.
- To rotate, replace the Secret's contents and keep its name.
- A missing or wrong credential fails only the templates that name it: `Ready=False`, `ActorTemplateRetrying`, then `ActorTemplateFailed`.

**Prerequisites.** Those of [Agent Substrate](#agent-substrate). Upgrading: [UPGRADE.md](../UPGRADE.md).

### Agent Substrate

kagent API v2 runs every agent as a Substrate *actor* in a gVisor *worker* pod of a `WorkerPool`. The chart ships Substrate from the [Giant Swarm Substrate line](https://github.com/giantswarm/substrate) as two components in `ate-system`:

- `components.substrate-crds`: the `ate.dev` CRDs.
- `components.substrate`: the control plane, the per-node `atelet` DaemonSet and the `podcertificate-controller`.

What it changes on a cluster, the Pod Security exceptions and the compensating controls: [docs/substrate-security.md](substrate-security.md).

- **Version.** Both components carry the same `versionRange`, of the shape `>=X.Y.Z <X.(Y+1).0`, or an exact version. Any other shape fails the render. Stable releases only. The kagent WorkerPool's worker image is derived from the range's floor, so moving the floor rolls the pool. Capacity, worker image and failure domain: [Agent Substrate: worker capacity](../helm/agent-platform/README.md#agent-substrate-worker-capacity) in the meta chart README.
- **Order.** `substrate-crds` and `kagent-crds` first, then `agent-platform-connectivity`, then `substrate`, then `kagent`, then the managers. The connectivity release's pre-install hook creates any missing CA and JWT pools and the authentication ConfigMap that Substrate mounts. It never touches a pool that already exists.
- **Database.** `substrate.postgres.enabled: auto`. With the platform's CNPG Cluster on (`postgres.enabled`), Substrate uses the CNPG database named by `postgres.substrateDatabase` (default `substrate`, an entry of `postgres.databases`), and the meta chart derives its DSN and the roles ate-api-server switches to. Otherwise the bundled StatefulSet runs. For an external database, set `substrate.postgres.readWriteConnectionString` (or `connectionStringSecretRef`) plus `readWriteRole` and `ownerRole`, roles its user may assume. The old `substrate.postgres.connectionString` fails the render.
- **Snapshot store.** `kagent.harness.snapshotLocation` is **required whenever kagent is on**. It is an object-store URL such as `s3://<bucket>/<prefix>`.
  - `kagent.harness.snapshotStore.crossplane` provisions the store and derives the location. On CAPA that is an S3 bucket plus an IRSA role. On CAPZ (`provider: capz`) it is an Azure Blob container behind the s3proxy façade.
  - Without it, bring your own bucket and credentials (`substrate.atelet.extraEnv`, `substrate.ateApiServer.extraEnv`).
  - A lab can use `substrate.rustfs.enabled: true`.
  - Nothing in the store expires by age. Every object is a live snapshot, so do not add age-based lifecycle rules.
- **Scheduling.**
  - `substrate.atelet.{nodeSelector,tolerations,affinity}` place atelet.
  - Pin `kagent.substrateWorkerPool.template.nodeSelector` to one CPU vendor and generation, not just `kubernetes.io/arch`. A snapshot does not restore on a CPU that lacks a feature it recorded. On CAPA, for example: `karpenter.k8s.aws/instance-cpu-manufacturer: amd`, `karpenter.k8s.aws/instance-generation: "6"`.
  - A `PodDisruptionBudget` (`kagent.substrateWorkerPool.podDisruptionBudget`, on by default) drains workers one at a time.
- **Kyverno.** The connectivity chart ships one `PolicyException` per Substrate workload, mapped through `kyvernoPolicies.rules`. `make verify-kyverno` holds them to the rendered pod specs.
- **Network policies** (`networkPolicy.flavor`) cover Substrate's internal hops. They also cover the actors' destinations on the egress gateway: muster, the kagent controller, the LLM provider and DNS. Workers reach only the egress gateway and DNS. `make verify-kagent-netpol` checks them.
- **Images.** The substrate chart's third-party images come from its own `images:` map. The router and egress data plane follow the Substrate release, so the meta chart forwards no `substrate.images.agentgateway`. `make verify-substrate-images` checks every release the range admits.

**Prerequisites** (also in [install.md](install.md#agents-managed-cloud)):

- **Kubernetes 1.35** with the feature gates `ClusterTrustBundle`, `ClusterTrustBundleProjection` and `PodCertificateRequest` on kube-apiserver, kube-controller-manager and every kubelet. A live render refuses a cluster that does not serve `certificates.k8s.io/v1beta1` `PodCertificateRequest`.
- **Cilium with kube-proxy replacement needs `socketLB.hostNamespaceOnly: true`** (`bpf-lb-sock-hostns-only: "true"`; the Giant Swarm default). Without it, actors cannot resolve DNS from their nested network namespace, and git-sourced skills fail with `Could not resolve host`.
- **A proxied installation** mirrors:
  - `oci://gsoci.azurecr.io/giantswarm/substrate/helm` (`components.substrate.repository`);
  - the images (`substrate.image.registry`);
  - the hook images (`hooks.kubectlImage`, `hooks.opensslImage`);
  - the gVisor release asset that `atelet` fetches from `storage.googleapis.com` (override `spec.assets` on the `SandboxConfig`).

Upgrading: [UPGRADE.md](../UPGRADE.md).

### Backstage, mcp-kubernetes, CloudNativePG and KServe

Optional components for a cluster that has none of these. All are **off by default**: a management cluster runs them as its own apps. Each renders as one `OCIRepository` + `HelmRelease`. Its values block, named after the component, is forwarded with `global` injected. The current `versionRange` of each is in [`values.yaml`](../helm/agent-platform/values.yaml) under `components.<name>`.

| Component | Chart source | `dependsOn` |
|---|---|---|
| `backstage` | `oci://gsoci.azurecr.io/charts/giantswarm` | `cloudnative-pg`, `agent-platform-connectivity` |
| `mcp-kubernetes` | `oci://gsoci.azurecr.io/charts/giantswarm` | — |
| `cloudnative-pg` | `oci://gsoci.azurecr.io/giantswarm/cloudnative-pg/charts` (one chart minor = one operator line; moving it rolls every instance) | — |
| `kserve-llmisvc-crd` | `oci://gsoci.azurecr.io/charts/giantswarm` (the three kserve charts move together) | — |
| `kserve-llmisvc-resources` | `oci://gsoci.azurecr.io/charts/giantswarm` | `kserve-llmisvc-crd` |
| `kserve-runtime-configs` | `oci://gsoci.azurecr.io/charts/giantswarm` | `kserve-llmisvc-crd` |

- **Inputs.**
  - Backstage and mcp-kubernetes need `global.domain` and `global.identity` (`issuerUrl`, `clientId`, `existingSecret` with `dex-client-secret`, plus `backstage-session-secret` for Backstage).
  - `kserve-llmisvc-resources` needs cert-manager and is the platform's one KServe controller.
  - On a cluster without Cilium, set `mcp-kubernetes.ciliumNetworkPolicy.enabled: false`.
- **Order.**
  - `agent-platform-connectivity` dependsOn `cloudnative-pg` (for the CNPG `Cluster`) and `muster` (for the `MCPServer` CRD).
  - `model-manager` dependsOn `kserve-llmisvc-resources`.
  - A reference to a component that is off is dropped at render time.
- **CRDs** are ordinary templates, with no `crds:` policy. See [CRD lifecycle](#crd-lifecycle).

The wiring is in the connectivity chart: [Wiring for the optional components](#wiring-for-the-optional-components). `make verify-components` and `make verify-components-charts` check the roster and the forwarded values.

### The serving slice and the models Gateway

Model serving on a GPU cluster is its own release of this chart: the **serving slice**, values profile [`examples/serving-slice.yaml`](../helm/agent-platform/examples/serving-slice.yaml).

- It renders only `kserve-llmisvc-crd`, `kserve-llmisvc-resources`, `kserve-runtime-configs` and the `modelServing` switch. It sets `modelServing.serving.runtimeClassName: nvidia` and `modelServing.modelsGateway.enabled: true`.
- Deploy one release per target cluster. See [One release per target cluster](../helm/agent-platform/README.md#one-release-per-target-cluster).
  - Beside the platform's own release: use the profile as is.
  - On a workload cluster: add `gitops.target.kubeConfig.secretRef` and `components.agentgateway.enabled: true`.

**The well-known configs.** `kserve-runtime-configs` installs the `LLMInferenceServiceConfig`s every served model composes from, into the release namespace. `kserve-runtime-configs.kserve.llmisvcConfigs.imageRegistry` is the prefix of every runtime image; the default is `gsoci.azurecr.io/giantswarm/llm-d-fast/`. If you change it, change the `llm-d-cuda` entry of `modelServing.prepull.images` to match; `make verify-serving-slice` holds the two in step. Image pulls and the pre-pull: [docs/model-serving-image-pulls.md](model-serving-image-pulls.md) and [Pre-pulling the runtime image](../helm/agent-platform-connectivity/README.md#pre-pulling-the-runtime-image).

**The cache claim.** `modelServing.cache.pvc` (`hf-cache`, `100Gi`, gp3) is applied by a post-install/post-upgrade hook, not as a release resource. It is kept on uninstall. An existing claim keeps its size and class. See [The claim's StorageClass](../helm/agent-platform-connectivity/README.md#the-claims-storageclass) and [The claim's tier](../helm/agent-platform-connectivity/README.md#the-claims-tier).

**The models Gateway.** `modelServing.modelsGateway` is off by default; the slice turns it on. It is one agentgateway `Gateway` named `models` on `<hostPrefix>.<global.domain>`.

- TLS comes from `gatewayApi.gateway.tls.secretName`, or a cert-manager `Certificate` via `tls.issuerRef.name`.
- The LoadBalancer Service is `internet-facing` by default (`service.annotations`). Set the scheme to `internal` for a private host.
- KServe's ingress gateway is derived onto `kserve-llmisvc-resources`, so every model route attaches to this Gateway. A model answers at:

```
https://models.<global.domain>/<namespace>/<model>/v1/chat/completions
```

A `Strict` `AgentgatewayPolicy` on the Gateway verifies the bearer against `global.identity.issuerUrl` and accepts only the audience `dex-k8s-authenticator`. No route can weaken it. See [Authentication: the models Gateway](authentication.md#6-the-models-gateway).

**Presets.** The connectivity chart ships the presets as files under [`files/model-serving/presets/`](../helm/agent-platform-connectivity/files/model-serving/presets/), one ConfigMap each. Add or replace presets under `modelServing.presets`. Recipes, sizing, GPU generation (`requirements.minComputeCapability`) and the checks a new preset needs (`make verify-preset-weights`, `make verify-serving-slice`): [docs/serving-presets.md](serving-presets.md). Rules the render enforces:

- Every served model is an `LLMInferenceService` that model-manager composes on the well-known configs. The chart renders no `ClusterServingRuntime` and no `InferenceService`. A preset with `spec.runtime` or `spec.predictor` fails the render.
- Preset arguments must not contain whitespace, quotes or shell metacharacters outside single quotes. A JSON value goes single-quoted inside one argument.
- `--disable-fastapi-docs` is refused. model-manager discovers a model's APIs from its `/openapi.json`, so presets never declare them.
- `spec.router.scheduler` (boolean) switches the llm-d endpoint picker for one preset.
- `resources.gpus` must equal the preset's `--tensor-parallel-size` (1 without the flag).

**Models as OCI images.** A preset whose `spec.model.storageUri` is `oci://…` is served from a model image (KServe's modelcar). The node pulls the image, and the cache claim is not used.

- The image is busybox-based, with the weights under `/models`.
- The runtime runs as a non-root uid, so set `HOME`, `HF_HOME` and `VLLM_CACHE_ROOT` under `/tmp`.
- `modelServing.modelImages.registry` swaps the registry host of every `oci://` preset and keeps the path.
- `modelServing.prepull.modelPresets` pre-pulls the listed presets' images onto every serving node.
- `make verify-model-images` checks the rewrite.

**Verifying model images.** `modelServing.imageVerification` is on by default. It renders one Kyverno `verifyImages` ClusterPolicy over the model pods of the serving namespace, wherever Kyverno is served.

- The defaults admit images under `gsoci.azurecr.io/giantswarm/*` signed by Giant Swarm's CircleCI keyless identity (Sigstore bundle). They pin the image to its digest and enforce.
- An image matching no pattern is not checked. If you serve from your own registry, add its pattern and signer under `images` / `attestors`.
- If your model pods run an image under the pattern that no attestor signed, add the signer or turn verification off before you upgrade. Attestor entries are Kyverno's, passed through as written.

**Network policies.** model-manager's egress reaches the serving namespace's workload port. A GPU node pool's slice admits model-manager through `modelServing.networkPolicy.additionalIngressNamespaces`. `make verify-model-serving-policies` checks both.

```bash
helm template r helm/agent-platform -f helm/agent-platform/examples/serving-slice.yaml \
  --set global.domain=wc01.example.com --set global.identity.issuerUrl=https://dex.mc.example.com \
  --set gatewayApi.gateway.tls.secretName=wildcard-tls --set 'ingress.parentRefs[0].name=x'
make verify-serving-slice
make live-serving-slice NAMESPACE=<the slice's>   # on a cluster: the models-jwt policy and the controller's JWKS fetch
```

### The runtime slice on workload clusters

A CPU workload cluster runs agents managed from the installation: the **runtime slice**, values profile [`examples/runtime-slice.yaml`](../helm/agent-platform/examples/runtime-slice.yaml).

- **What it renders.** `kagent-crds`, `substrate-crds`, `substrate`, `kagent`, `agentgateway` and the connectivity release. No muster, dicebear, Valkey, model-manager or Flux engine. The agents' tools come from the installation's muster.
- **How it is delivered.** One `<cluster>-agent-platform` release per target cluster, a `HelmRelease` in the cluster's organization namespace on the installation, with `gitops.target.kubeConfig.secretRef` naming `<cluster>-kubeconfig`. The installation's Flux installs everything, and no hook of this chart renders. See [One release per target cluster](../helm/agent-platform/README.md#one-release-per-target-cluster).
- **Placement.** The slice never runs beside the platform's own release: `components.kagent-crds.ownedCrds` refuses a second owner of kagent's CRDs. A GPU cluster that also serves models turns the serving toggles on in the same release.

Preconditions on the workload cluster:

- **Identity.** `global.identity` is the installation's Dex issuer, and the workload cluster's apiserver trusts that issuer too.
- **Egress.** The installation's muster host and the agents' model endpoints are reachable on 443.
- **Substrate.** Its [prerequisites](#agent-substrate) apply, and `kagent.harness.snapshotLocation` needs its own prefix per cluster.

```bash
helm template r helm/agent-platform -f helm/agent-platform/examples/runtime-slice.yaml \
  --set global.domain=wc01.example.com --set global.identity.issuerUrl=https://dex.mc.example.com
make verify-runtime-slice
```

### Wiring for the optional components

The connectivity chart renders the platform wiring for the optional components when their toggles are on. With every toggle off, nothing renders.

You need:

- the Gateway API CRDs and a public Gateway (`global.gatewayApi.parentRefs`, or `gatewayApi.gateway.create: true`);
- `global.identity` (`issuerUrl`, `clientId`, `existingSecret`; `global.identity.ca.secretName` for a private CA);
- `global.domain`.

| Toggle | What the connectivity chart renders | Inputs |
|---|---|---|
| `components.backstage.enabled` | The app-config ConfigMap `agent-platform-backstage-app-config`, the `HTTPRoute` `backstage.<domain>`, and a config-reload hook that rolls the Deployment when the config changes. | `backstage.{hostname, parentRefs, installationName, extraScopes, startUrlSearchParams, enabledExtensions, disabledExtensions, skillsRepositories, configReload}`, dropped before the backstage chart (`components.backstage.omitKeys`). |
| `components.mcp-kubernetes.enabled` | The `MCPServer` registering the server with muster, with the user's token forwarded and the label `agent-platform.giantswarm.io/tool-group: infrastructure`. Needs muster. `mcp-kubernetes.mcpServer.managementCluster: <name>` makes it a member of muster's `kubernetes` family; `enabled: false` renders no CR. | `mcp-kubernetes.kubernetesAudience` (default `dex-k8s-authenticator`, `""` for Google), `mcp-kubernetes.mcpServer`. |
| `components.modelServing.enabled` (with the three kserve components) | The serving presets and the discovery ConfigMap `agent-platform-model-serving`, the serving `Namespace`, the cache claim, the Kyverno cache policies (`modelServing.policies.enabled: auto`) and the serving namespace's network policies. Fails without the kserve components or the `serving.kserve.io` API (`modelServing.kserve.requireApi`). | `modelServing.*` |
| `components.kserve-llmisvc-resources.enabled` | The llm-d controller's network policy, and guards: `kserve.controller.deploymentMode: Standard`, `kserve.createSharedResources: true`. The removed `components.kserve-crd` / `components.kserve-resources` are refused. | the component's own block |

```bash
make verify-wiring
```

### The GPU operator

NVIDIA's GPU operator runs **once per cluster**. It reaches a cluster in one of two ways:

- **`components.gpu-operator.enabled: true`.** This is the catalog's `gpu-operator` wrapper chart into `kube-system`, with `crds: CreateReplace` and no `dependsOn`. Its values go in the `gpu-operator:` block. It is off by default and independent of model serving.
- **`<cluster>-gpu-operator`, created by cluster-manager** with the first GPU pool where no operator runs, and removed with the last pool if cluster-manager created it.

The `gpu-operator:` block supports two configurations:

| Nodes | `gpu-operator.driver.enabled` | `gpu-operator.toolkit.enabled` |
|---|---|---|
| Flatcar: every Giant Swarm CAPA / CAPZ node (the default) | `false` | `false` |
| Pre-installed driver (`nvidia.com/gpu.deploy.driver` pre-set to anything but `true`, e.g. Ubuntu) | `false` | `true` |

- The operator creates the `nvidia` RuntimeClass in both configurations, and GPU pods use `runtimeClassName: nvidia`.
- The operator's chart renders `CiliumNetworkPolicy` objects with no switch, so it needs Cilium.
- **One owner per cluster.** With the toggle on, a live render refuses a cluster whose operator belongs to another release: cluster-manager's, or one installed by hand. To hand it over, delete that release, then turn the toggle on. `helm template` does not check this. The negative test fixture is `tests/fixtures/gpu-operator-foreign-owner.yaml`.

`make verify-gpu-operator` covers the offline half.

## Identity and secrets

### Tenant identity

Agents are Flux `HelmRelease`s in the kagent namespace. Under a Flux multi-tenancy lockdown, a `HelmRelease` runs as the ServiceAccount it names. Whenever kagent is on, the connectivity chart renders that ServiceAccount: `kagent-flux`, bound to `cluster-admin` by a RoleBinding, so it has full rights in the kagent namespace only.

`kagent.fluxServiceAccountName` (default `kagent-flux`) is the single name. The meta chart derives agent-manager's `flux.helmReleaseServiceAccount` and the portal's setting from it. A different value set in the `agent-manager:` block fails the render. `make verify-identity` asserts this.

The platform's own component `HelmRelease`s need no tenant identity in an exempt namespace (`gitops.namespace: flux-giantswarm` on Giant Swarm management clusters). With the bundled Flux engine they run as `agent-platform-flux`.

### OAuth secrets

The platform Secret `global.identity.existingSecret` carries muster's OAuth keys: `dex-client-secret`, `registration-token`, `oauth-encryption-key` and `valkey-password`. See [install.md](install.md) for its keys and the Dex client. Pre-create the Secret, or ship it in the same release through the top-level `extraObjects: []` list. Inline credentials end up in the `HelmRelease` values. `gitops.forbidInlineSecrets: true` refuses them.

## Bundled components

Every component is switched by `components.<name>.enabled`. The old `valkey.enabled`, `agentgateway.enabled`, `agentSandbox.enabled`, `kagent.enabled` and `klausGateway.enabled` keys fail the render.

### Valkey

`components.valkey.enabled` is on by default. It runs one pod behind the `muster-valkey` Service, which muster's storage URL already points at. Valkey and muster read the password from the same Secret and key (`valkey-password`). Valkey reads the password only at start: when you rotate the Secret, change `valkey.valkey.auth.usersExistingSecretChecksum` in the same change to restart the pod. For an external Valkey, turn the component off and set `muster.muster.oauth.server.storage.valkey.url`.

### MCP servers

`components.agent-platform-mcps.enabled: true` renders the platform's MCP server CRs from `agent-platform-mcps.mcpServers`. It renders nothing while that list is empty. Everything under `agent-platform-mcps.*` is passed to the [chart](https://github.com/giantswarm/agent-platform-mcps) verbatim, and its schema is strict.

```yaml
components:
  agent-platform-mcps:
    enabled: true
agent-platform-mcps:
  mcpServers:
    - cluster: <cluster>
      group: kubernetes
      url: https://mcp.<cluster>.<base-domain>/mcp
```

### Toolset presets

The chart ships two presets, `infrastructure` and `agent-platform`, under `muster.muster.toolsetPresets`. muster adds `read-only`, `none` and `full` itself. See [toolset-presets.md](toolset-presets.md). `make verify-presets` asserts them.

### Postgres backups

`postgres.enabled` renders the CloudNativePG `Cluster` for kagent. The CNPG operator is a prerequisite. Without `postgres.backup.enabled: true` the database has no backup, and NOTES warns about it.

- `method: plugin` (default) uses the Barman Cloud plugin, which you install next to the operator. It gives continuous WAL archiving and a daily `ScheduledBackup` to `postgres.backup.objectStore`.
- `method: volumeSnapshot` takes CSI snapshots and keeps no WAL archive.
- `postgres.backup.crossplane.*` provisions the object store on AWS or Azure.
- To restore, bootstrap a second Cluster named `<clusterName>-restore` from the same store. The network policy and the AWS role expect that name.

`make verify-postgres` asserts this.

### Agent sandbox

`components.agent-sandbox.enabled: true` installs the agent-sandbox controller and its `Sandbox*` CRDs. The controller has no `securityContext` knob, so a Kyverno mutate policy injects restricted-PSS fields (`agentSandbox.podSecurity.*`, default `auto`, which follows Kyverno). `agentSandbox.podSecurity.enabled: true` without Kyverno fails the render. Where restricted PSS is enforced without Kyverno, leave the component off.

## Ingress topology

`ingress.mode` selects the request path. For the full auth flow, see [authentication.md](authentication.md).

| `ingress.mode` | Path |
|---|---|
| `agentgateway-muster` (default) | `/mcp` → agentgateway → muster; everything else → muster |
| `muster-direct` | everything → muster. Needs `components.agentgateway.enabled: false`, and no agentgateway objects from agent-platform-mcps, the kagent controller route or the klaus-gateway route. |
| `agentgateway-direct` | not supported: the render fails |

Both routes attach to `ingress.parentRefs`. If that is empty they fall back to the chart-owned edge (`gatewayApi.gateway.create`), then to `global.gatewayApi.parentRefs`. With none set, the render fails. Hostnames are `ingress.hostnames`, or `muster.<global.domain>` when unset. `ingress.httpRoute` labels and annotations apply to both routes, with per-route overrides under `muster` and `mcp`.

## Observability

All exporters send to `global.observability.traces.otlp`. By default that is `http://otlp-gateway.kube-system.svc:4317` over gRPC with tenant `giantswarm`. The tenant goes out as the `X-Scope-OrgID` header, or as the pod label `observability.giantswarm.io/tenant` for Substrate and the agentgateway data plane. A component key left at `auto` takes the global value, and an explicit value wins. Egress policies follow the resolved endpoints.

- An empty `endpoint` exports nothing.
- An `X-Scope-OrgID` in `headers` that differs from `tenant` fails the render.
- klaus-gateway and Substrate speak only gRPC, so `http/protobuf` fails while either one takes the endpoint.

`make verify-otlp-global` asserts this.

The kagent controller serves Prometheus metrics on `:8080` (`kagent.controller.metrics`). The kagent chart's own ServiceMonitor follows `global.observability.metrics.serviceMonitor.enabled`.

## Private registry overrides

`global.registry` (default `gsoci.azurecr.io`) and `global.imagePullSecrets` are injected into every component release that takes `global`. These components are not injected (`injectGlobal: false`), and you set their own image keys:

- agent-platform-mcps, kagent-crds, substrate-crds, substrate, agent-sandbox, gpu-operator, gateway-api-crds and dicebear.
- Substrate: `substrate.image.registry`, plus `components.substrate.repository` for its chart.
- agent-sandbox: `agent-sandbox.agent-sandbox.image.repository`. The controller sits under a nested key.

These charts read their own `image.registry` keys:

- muster: `muster.image.registry`.
- agentgateway controller: `agentgateway.controller.image` in both charts.
- agentgateway data plane: `agentgateway.proxy.image` in the connectivity chart. Move it together with the controller.

To mirror the charts themselves, set `components.<name>.repository`. `make verify-images` asserts that every default image reference is on gsoci.

## Security

The controller renders the agentgateway data-plane pod, not Helm. The chart patches it through `AgentgatewayParameters` with restricted-PSS `securityContext` fields and `gateway.parameters.serviceType: ClusterIP`. Set `LoadBalancer` there when no front Gateway exposes the data plane.

## CRD lifecycle

CRDs are app-owned. Each component ships its own CRDs, and they upgrade with its release (`components.<name>.versionRange`). The meta chart installs no CRDs.

| CRDs | Shipped by |
|---|---|
| Gateway API | a cluster prerequisite; optional `components.gateway-api-crds` (off by default) |
| `agentgateway.dev` | agentgateway |
| `muster.giantswarm.io` | muster |
| `api.kagent.dev` | kagent-crds, which follows `components.kagent` |
| `ate.dev` | substrate-crds, which follows `components.kagent` |
| `agents.x-k8s.io` | agent-sandbox |
| `serving.kserve.io` | kserve-llmisvc-crd (off by default) |
| `postgresql.cnpg.io` | cloudnative-pg (off by default) |
| `nvidia.com` | gpu-operator (off by default) |

Charts that ship a `crds/` directory get `crds: CreateReplace` on their `HelmRelease`. kagent-crds, substrate-crds, kserve-llmisvc-crd and cloudnative-pg render CRDs as templates instead. The CRDs carry `helm.sh/resource-policy: keep`. Uninstalling a component leaves its CRDs and CRs in place, so delete a CRD explicitly. `helm uninstall` of the meta chart removes no CRD. For migrations from the old standalone CRD chart, see [UPGRADE.md](../UPGRADE.md).

## Compatibility

The platform needs Kubernetes 1.35 or later. Agent Substrate also needs the PodCertificateRequest API, and the render checks for it. The `kubernetes` NetworkPolicy flavor works on any CNI, but it has no entity selectors and no FQDN egress.

### Cluster shape (`auto`)

These knobs default to `auto` and resolve from the API groups the cluster serves:

- `kyvernoPolicies.enabled`: on when `kyverno.io/v1` is served.
- `networkPolicy.flavor`: `cilium` when `cilium.io/v2` is served, otherwise `kubernetes`.
- `global.observability.metrics.serviceMonitor.enabled`: on when `monitoring.coreos.com/v1` is served.
- `dicebear.route.enabled`: on when Envoy Gateway's `gateway.envoyproxy.io/v1alpha1` is served.
- `agentSandbox.podSecurity.enabled`: follows Kyverno.

The meta chart resolves them once and passes the answer to every component. An explicit value wins. `helm template` sees only Helm's built-in APIs, so add `--api-versions` for each group to render the fleet shape offline. `make verify-auto` asserts this.

### Kyverno

The connectivity chart renders Kyverno objects only where Kyverno is enabled:

- the agent-sandbox pod-security policy
- five Substrate PolicyExceptions
- the model-serving policies and exception
- the Postgres exception, when `postgres.vector.extensionImage.reference` is set

Exceptions name policies this chart does not own. `kyvernoPolicies.rules` maps each rule to its ClusterPolicy, using the default Giant Swarm names. A cluster with other policy names overrides the map, and a rule missing from it fails the render. Exceptions go to `kyvernoPolicies.policyExceptionNamespace` (default `policy-exceptions`).

## Development

`make verify-all` runs every offline render assertion, as CI's `test-ingress-modes` job does. Use Helm 3.17.3: `make pinned-helm` downloads it and prints the `HELM=` to pass. `pre-commit run -a` regenerates the schemas and READMEs. The kind-cluster ATS, a required CI check (`execute-chart-tests`), is described in [tests/ats/README.md](../tests/ats/README.md).
