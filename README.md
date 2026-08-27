[![CircleCI](https://dl.circleci.com/status-badge/img/gh/giantswarm/agent-platform/tree/main.svg?style=svg)](https://dl.circleci.com/status-badge/redirect/gh/giantswarm/agent-platform/tree/main)
[![OpenSSF Scorecard](https://api.securityscorecards.dev/projects/github.com/giantswarm/agent-platform/badge)](https://securityscorecards.dev/viewer/?uri=github.com/giantswarm/agent-platform)

# agent-platform

The Agent Platform is Giant Swarm's MCP gateway deploy unit. It ships [muster](https://github.com/giantswarm/muster) (MCP gateway / aggregator) and [agentgateway](https://github.com/agentgateway/agentgateway) (MCP data plane) together, plus the consumer-side `Gateway`, `AgentgatewayParameters`, and network policies that wire them up.

Owner: team-bumblebee.

This repo publishes **two** charts:

| Chart | What it is |
|---|---|
| `agent-platform` | the **meta-package** — an app-of-apps that renders each component and the connectivity layer as Flux `OCIRepository` + `HelmRelease` (or Argo `Application`). The single thing you install. |
| `agent-platform-connectivity` | the consumer-side **wiring** the meta-package renders as a child release: the public muster route, the agentgateway data-plane `Gateway` + `AgentgatewayParameters` + `HTTPRoute`s + `BackendTrafficPolicy`s, the `NetworkPolicy`s, the kagent/klaus-gateway routes, the admin-owned kagent shared resources (the shared muster `RemoteMCPServer`, `ModelConfig`s), and the CNPG `Cluster`. Agents themselves are created with the generic [`agent` chart](https://github.com/giantswarm/agent), one release per agent. |

> **CRDs are app-owned.** There is no longer a standalone `agent-platform-crds` bundle chart — each component (muster, agentgateway, kagent, agent-sandbox) ships its own CRDs in its chart's `crds/` dir and upgrades them atomically with the app via Flux `CreateReplace`. A CR consumer `dependsOn` the component that owns the CRD it needs. See [CRD lifecycle](#crd-lifecycle).

## Meta-package release flow

> Implements [giantswarm/giantswarm#36875](https://github.com/giantswarm/giantswarm/issues/36875). Concept write-up: klaus-lab `architecture/agent-platform-meta-package.md`.

The `agent-platform` chart no longer bundles its components as pinned Helm subcharts. It is an **app-of-apps meta-package**: `templates/components.yaml` renders, per entry in `.Values.components`, a Flux `OCIRepository` + `HelmRelease` — or an Argo `Application` when `gitops.engine: argo`. It emits **only** those objects (a *pure* renderer — no raw CRs of its own).

The decisive change: each component's version is a **constraint expressed as a value** (`components.<name>.versionRange`), not a `Chart.yaml` pin. Flux re-resolves the range on every reconcile, so a new component release rolls forward **with no PR to this chart and no umbrella re-package**.

- **One gitops entry.** You install the meta-package (one `OCIRepository` + `HelmRelease`). It renders each component release and the `agent-platform-connectivity` release for you.
- **CRD-before-CR ordering is preserved** — each component ships its own CRDs (app-owned CRDs), and a CR consumer `dependsOn` the component that owns the CRD it needs (Flux) / orders after it via `argocd.argoproj.io/sync-wave` (Argo). A `dependsOn` reference to a component that is toggled off is dropped at render time, so an always-on consumer never blocks on a release that was never rendered. Because the connectivity CRs live in their own release (not in the meta-package's own manifest), they only apply after the CRDs are Established — the meta-package itself ships no CR that could race a CRD.
- **Per-component values keep their blocks; the on/off switch does not** — the existing `muster:`, `agentgateway:`, `kagent:`, `valkey:`, `klausGateway:`, `agentSandbox:`, `agent-platform-mcps:` blocks still drive each component's configuration, and the connectivity wiring blocks (`ingress:`, `gateway:`, `networkPolicy:`, `postgres:`, `extraObjects:`) still drive the wiring. Whether a component is installed at all is `components.<name>.enabled` — see [Enabling and disabling components](#enabling-and-disabling-components). Each `components.<name>` entry names its source block via `valuesFrom` (connectivity uses `forwardAllValues`).
- **Dev vs customer track** — keep the `components.*.versionRange` values **wide** for the internal/dogfooding track (continuous auto-update, the default). **Pin** them to exact versions for a customer **bill-of-materials**; see [`helm/agent-platform/examples/customer-bom.yaml`](helm/agent-platform/examples/customer-bom.yaml). A "product release" is that pinned values snapshot.

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

A component with no `enabled` key is always installed (`muster`, `dicebear`, `agent-platform-connectivity`). The `agentgateway:`, `kagent:`, `valkey:`, `klausGateway:`, `agentSandbox:` and `agent-platform-mcps:` blocks hold that component's values and no longer hold an `enabled` key; `make verify-meta` fails if the two ever diverge again.

```bash
helm template r helm/agent-platform -f helm/agent-platform/ci/ci-values.yaml                          # flux objects, wide ranges
helm template r helm/agent-platform -f helm/agent-platform/ci/ci-values.yaml --set gitops.engine=argo
helm template r helm/agent-platform -f helm/agent-platform/ci/ci-values.yaml -f helm/agent-platform/examples/customer-bom.yaml
make verify-meta verify-modes
```

## Prerequisites

- Kubernetes ≥ 1.33 on the install target.
- Gateway API v1 CRDs (`gateways.gateway.networking.k8s.io`, `httproutes.gateway.networking.k8s.io`, `gatewayclasses.gateway.networking.k8s.io`) installed cluster-wide. The Agent Platform does **not** install them.
- No separate CRD chart to install first — every component ships its own CRDs (`AgentgatewayParameters` / `AgentgatewayPolicy` / `AgentgatewayBackend` with the agentgateway component, `MCPServer` / `Workflow` with muster, the kagent + agent-sandbox CRDs with their components). The meta-package orders each CR consumer after the CRD-owning component for you; see [CRD lifecycle](#crd-lifecycle).
- A `GatewayClass` CR named `agentgateway` (`status.conditions[type=Accepted]=True`). The bundled `agentgateway` sub-chart creates it on install; operators managing the controller out-of-band must ensure the `GatewayClass` exists.
- Kyverno for the four `kyverno.io` objects the connectivity chart renders (default on). Clusters without Kyverno: set `kyvernoPolicies.enabled: false`, and `components.agent-sandbox.enabled: false` too where restricted PSS is enforced through PSA labels; see [Kyverno](#kyverno).
- Cilium CNI for `networkPolicy.flavor: cilium` (default). Vanilla Kubernetes clusters: set `networkPolicy.flavor: kubernetes` **AND** `muster.networkPolicy.flavor: kubernetes` **AND** `valkey.ciliumNetworkPolicy.enabled: false` (the bundled valkey wrapper's CNP has no kubernetes-flavor counterpart). Opt out entirely with `networkPolicy.enabled: false` + `muster.networkPolicy.enabled: false` + `valkey.ciliumNetworkPolicy.enabled: false`.

## Installing

**One gitops entry.** Install the `agent-platform` meta-package; it renders the per-component and `agent-platform-connectivity` releases for you (each component ships its own CRDs, and a CR consumer `dependsOn` the CRD-owning component so CRDs Establish before any CR applies). Flux (`gitops.engine: flux`, default) or Argo (`gitops.engine: argo`) is required on the install target — the meta-package's output is Flux/Argo objects.

### Flux

```yaml
apiVersion: source.toolkit.fluxcd.io/v1
kind: OCIRepository
metadata:
  name: agent-platform
  namespace: muster
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
  namespace: muster
spec:
  interval: 10m
  chartRef: { kind: OCIRepository, name: agent-platform }
  install:
    createNamespace: true
  valuesFrom:
    - kind: Secret
      name: agent-platform-values
```

The meta-package then renders, in the same namespace: an `OCIRepository` + `HelmRelease` for each enabled component and for `agent-platform-connectivity`. Each component ships its own CRDs. Component `versionRange`s default to **wide** (continuous auto-update); pin them in your values for a reproducible release.

### Raw Helm (no GitOps controller)

The meta-package renders Flux/Argo objects, so a raw `helm install` of it needs a controller present. For a controller-free install, drive the components directly from a pinned bill-of-materials — install each component chart (which ships its own CRDs) then `agent-platform-connectivity`, at the exact versions in [`examples/customer-bom.yaml`](helm/agent-platform/examples/customer-bom.yaml):

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

> Helm's `crds/` directory is install-only: `helm upgrade` never re-applies or upgrades CRDs from `crds/`. The meta-package solves this for GitOps installs by setting `crds: CreateReplace` on each app-owned component's `HelmRelease`. For a raw-Helm install you must apply CRD schema changes out of band (`kubectl apply`/`replace`) on a component upgrade.

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
| `networkPolicy.flavor` | `cilium` | `cilium` → CiliumNetworkPolicy; `kubernetes` → vanilla NetworkPolicy (best-effort, no entity selectors / FQDN egress). |
| `extraObjects` | `[]` | Arbitrary manifests rendered through `tpl` alongside the chart. |
| `muster.*` | passes through to muster | See [muster chart README](https://github.com/giantswarm/muster/blob/main/helm/muster/README.md). |
| `agentgateway.*` | passes through to upstream agentgateway | See [agentgateway docs](https://agentgateway.dev). |
| `components.valkey.enabled` | `true` | Bundle [giantswarm/valkey-app](https://github.com/giantswarm/valkey-app) for muster OAuth session storage. |
| `components.agent-platform-mcps.enabled` | `false` | Bundle [giantswarm/agent-platform-mcps](https://github.com/giantswarm/agent-platform-mcps) to render the platform's MCP server CRs. See [Bundled MCP servers](#bundled-mcp-servers). |
| `components.kagent.enabled` | `false` | Install the kagent controller component. |
| `components.klaus-gateway.enabled` | `false` | Install [klaus-gateway](https://github.com/giantswarm/klaus-gateway), the channel front door. |
| `agent-platform-mcps.mcpServers` | `[]` | Abstract list of MCP servers rendered into `MCPServer` / `AgentgatewayBackend` CRs. Renders nothing until populated. |
| `components.agent-sandbox.enabled` | `false` | Bundle the [agent-sandbox](https://github.com/kubernetes-sigs/agent-sandbox) controller — the Sandbox runtime kagent's `SandboxAgent` requires. See [Agent sandbox](#agent-sandbox). |
| `dicebear.route.parentRefs` | `[]` | **Required.** The public `Gateway`(s) the avatar `HTTPRoute` attaches to — typically the same as `ingress.parentRefs`. The [dicebear](https://github.com/giantswarm/dicebear) avatar renderer is a force-enabled component (deployed on every install) and its public route is on by default, so the child release fails to render if this is empty. See [giantswarm/giantswarm#37211](https://github.com/giantswarm/giantswarm/issues/37211). |
| `dicebear.route.hostnames` | `[]` | **Required.** Hostname(s) the avatar endpoint is served on, e.g. `avatars.<domain>`. Consumers fetch `https://<hostname>/v1/<agent-name>.png`. |
| `muster.muster.oauth.server.enabled` | `true` | OAuth resource-server protection on the muster API. Requires `baseUrl`, `dex.{issuerUrl,clientId}`, and a Secret carrying `dex-client-secret` / `registration-token` / `oauth-encryption-key` / `valkey-password`. |
| `muster.muster.oauth.server.storage.type` | `valkey` | Muster storage backend default. Pairs with `components.valkey.enabled: true`; flip to `memory` for dev. |
| `muster.muster.oauth.server.storage.valkey.url` | `muster-valkey:6379` | Bundled-valkey Service. Override to point at an out-of-band Valkey. |

Full schema: [`helm/agent-platform/values.schema.json`](./helm/agent-platform/values.schema.json).

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
| **`extraObjects`** | Umbrella-level `extraObjects: []` list (this chart) + muster `existingSecret` pointed at the rendered Secret | Single Helm release ships Secret + values together. Non-Flux operators (ArgoCD, vanilla Helm) who want one `helm upgrade` to manage everything. |

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

### Agent sandbox

`components.agent-sandbox.enabled: true` bundles the [agent-sandbox](https://github.com/kubernetes-sigs/agent-sandbox) controller, which reconciles `Sandbox` resources into isolated pods. This is the runtime **kagent's `SandboxAgent` delegates pod isolation to** — the `SandboxAgent` CRD ships with the kagent component and the `Sandbox*` CRDs ship with the agent-sandbox component (app-owned CRDs), but the feature is inert until this controller runs, so enabling it is the prerequisite for sandboxed agents.

```yaml
agentSandbox:
  enabled: true
```

The toggle is `components.agent-sandbox.enabled`; the `agentSandbox:` block holds only values the connectivity chart reads, because the bundled `agent-sandbox` chart's `values.schema.json` is strict (`additionalProperties: false`) and rejects everything it does not declare. The agent-sandbox CRDs (`Sandbox` / `SandboxTemplate` / `SandboxClaim` / `SandboxWarmPool`) ship with the agent-sandbox component chart itself (app-owned CRDs), so enabling the controller installs them too.

The agent-sandbox chart is kept vendor-agnostic and the upstream controller exposes no `securityContext` knob, so the umbrella injects restricted-PSS fields into the controller Deployment at admission via a Kyverno mutate policy (`agentSandbox.podSecurity.*`). Override `agentSandbox.podSecurity.enabled: false` to drop the policy, or tune the `podSecurityContext` / `containerSecurityContext` blocks. The policy matches the `agent-sandbox-controller` Deployment in `agentSandbox.podSecurity.namespace` (default `agent-sandbox-system`), which must match the chart's namespace.

That policy is the controller's only source of a `securityContext`. So `kyvernoPolicies.enabled: false` (see [Kyverno](#kyverno)) fails the render while `agentSandbox.podSecurity.enabled` is on, and the failure names the two ways out: `components.agent-sandbox.enabled: false` on a cluster that enforces restricted PSS, or `agentSandbox.podSecurity.enabled: false` where nothing enforces it and a controller pod without a `securityContext` is acceptable. On the flux engine the `agent-sandbox` release `dependsOn` the connectivity release, so Kyverno holds the policy before the Deployment applies.

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

Muster does not yet support OTLP push. Its `/metrics` endpoint is scraped via `ServiceMonitor` (`muster.serviceMonitor.enabled: true`).

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

Each component sets `crds: CreateReplace` on its `HelmRelease` (rendered by the meta-package), so Flux applies and upgrades the `crds/`-dir CRDs atomically with the app at the same resolved version — Helm on its own never upgrades `crds/`-dir CRDs. All these CRDs carry `helm.sh/resource-policy: keep`, so they survive a component uninstall (the CRs are never cascade-deleted).

`helm uninstall agent-platform` (the meta-package) leaves everything intact — it owns no CRDs or CRs. Uninstalling a **component** release leaves its `keep`-annotated CRDs (and their CRs) in place; to remove a CRD you must delete it explicitly.

### Upgrading CRDs

CRDs migrate with their owning component release — a new component version (resolved by `components.<name>.versionRange`) ships the matching CRD schema and `CreateReplace` applies it. No separate CRD-chart upgrade step.

> **History.** Through `1.9.x` these CRDs shipped in a standalone `agent-platform-crds` bundle chart that every release `dependsOn`. That bundle has been retired in favour of app-owned CRDs; the staged, non-destructive migration (the live CRDs were first re-annotated with `helm.sh/resource-policy: keep` so dropping the bundle never cascade-deletes a CR) is documented in [UPGRADE.md](./UPGRADE.md).

## Compatibility

Targets Giant Swarm workload clusters running Kubernetes ≥ 1.33 with Cilium and Kyverno. The `kubernetes` NetworkPolicy flavor works on any CNI but is best-effort (no entity selectors, no FQDN egress). Kyverno is optional, see below.

### Kyverno

The connectivity chart renders four `kyverno.io` objects: two ClusterPolicies that give the bundled declarative agents their securityContext, a PolicyException that lifts the seccomp restriction those agents need, and the agent-sandbox pod-security ClusterPolicy. On a cluster with no Kyverno the install fails on an unknown API group, so set `kyvernoPolicies.enabled: false` there. It renders none of the four.

The PolicyException targets a policy this chart does not own, so its names must match the target cluster. They default to the Giant Swarm names (`policyExceptionNamespace: policy-exceptions`, `seccompPolicyName: restrict-seccomp-strict`, `seccompRuleNames`); a cluster that names its policies differently must override them, or the exception matches nothing.

A cluster that enforces restricted PSS through PSA labels instead of Kyverno must also set `components.agent-sandbox.enabled: false` (see [agent-sandbox](#agent-sandbox)).

## Credit

- [muster](https://github.com/giantswarm/muster) — Giant Swarm, Apache 2.0.
- [agentgateway](https://github.com/agentgateway/agentgateway) — agentgateway authors, Apache 2.0.
