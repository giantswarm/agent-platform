# Upgrading agent-platform

Operator action required between releases. CHANGELOG.md captures the diff; UPGRADE.md captures what an operator has to *do*.

## \<current\> → \<next\> (the `agentic-platform-mcps` values key is removed)

The transition fallback from the chart rename is gone. Overrides under `agentic-platform-mcps` are no longer merged over `agent-platform-mcps`; both `values.schema.json` files reject the key.

### Operator action

- Move any remaining overrides from `agentic-platform-mcps:` to `agent-platform-mcps:` **before** upgrading. A leftover key fails the HelmRelease schema check, so the upgrade stops rather than dropping the values silently.

## 2.5.4 → 2.6.0 (chart renamed agentic-platform → agent-platform)

The product is now consistently named **Giant Swarm Agent Platform**. The charts follow: `agentic-platform` → `agent-platform`, `agentic-platform-connectivity` → `agent-platform-connectivity`, and the separately released `agentic-platform-mcps` → `agent-platform-mcps` (first new-name release 0.7.0). New OCI paths: `oci://gsoci.azurecr.io/charts/giantswarm/agent-platform{,-connectivity}`. Old-name releases stay in the catalog but receive no further versions.

### Operator action

- Point your `OCIRepository`/`HelmRelease` (or Argo `Application`) at the new chart name and OCI URL. Helm and Flux treat the renamed release as **uninstall + install**, not an in-place upgrade, plan a maintenance window and coordinate the release name, target namespace, and values source in one change.
- Rename the mcps values block `agentic-platform-mcps:` → `agent-platform-mcps:`. The old key is rejected as of the release above.
- The default namespace baked into `agent-platform-mcps.muster.musterUrl` and `kagent.a2a.url` is now `agent-platform`. If you deploy to a different namespace you already override these; update the values if you follow the namespace rename.
- kagent derives agent IDs from the namespace (`agentic_platform__NS__…` → `agent_platform__NS__…`). Renaming the namespace changes every agent ID and orphans existing kagent sessions, external references to old agent IDs (saved links, integrations) must be updated.
- If `postgres.clusterName` is unset, the rendered default changes from `agentic-platform-pg` to `agent-platform-pg`; a fresh namespace means a fresh Postgres cluster regardless, back up and restore data you need to keep.

## \<current\> → \<next\> (retire the agentic-platform-crds bundle — app-owned CRDs everywhere)

The standalone **`agentic-platform-crds` bundle chart is retired**. The three components that still rode it — `agentgateway`, `kagent`, `agent-sandbox` — now own their CRDs in their own chart's `crds/` dir (joining `muster`, which moved earlier). The `agentic-platform-crds` component is removed from the meta-package; its `OCIRepository` + `HelmRelease` are no longer rendered.

### What changed

- `components.agentic-platform-crds` is removed from `agentic-platform` values.
- `agentgateway`, `kagent`, `agent-sandbox` drop `dependsOn: [agentic-platform-crds]` (they already set `crds: CreateReplace` and own their CRDs as of release A).
- The CR consumers repoint to the CRD-owning **components**:
  - `agentic-platform-mcps`: `dependsOn: [muster, agentgateway]` (was `[muster, agentic-platform-crds]`).
  - `agentic-platform-connectivity`: `dependsOn: [agentgateway, kagent]` (was `[agentic-platform-crds]`).
- The meta-package now **drops a `dependsOn` reference to a component that is toggled off** at render time, so the always-on connectivity release does not block on `agentgateway`/`kagent` in `muster-direct` deployments where they are disabled.
- The `agentic-platform-crds` chart, its CircleCI build/test/push jobs, and the now-dead Renovate helmv3 lockstep rules are deleted.

### Operator action: none (non-destructive automatic handoff)

Release A already overwrote the live agentgateway / kagent / kmcp CRDs with the wrapper charts' `helm.sh/resource-policy: keep` copies (agent-sandbox + muster CRDs already carried `keep`). Because every live CRD now carries `keep`, Flux pruning the retired `agentic-platform-crds` `HelmRelease` (Helm uninstall) does **not** delete the CRDs — the prune is blocked by `keep`, and no `agentgateway.dev` / `kagent.dev` / `agents.x-k8s.io` CR cascade occurs. The component releases continue to own and upgrade the CRDs via `CreateReplace`.

**Prerequisite gate:** confirm the live CRDs carry `keep` before this release rolls out (release A must be applied and reconciled first):

```bash
for crd in $(kubectl get crd -o name | grep -E 'agentgateway\.dev$|kagent\.dev$|kmcp\.dev$|agents\.x-k8s\.io$'); do
  kubectl get "$crd" -o jsonpath='{.metadata.name}{"\t"}{.metadata.annotations.helm\.sh/resource-policy}{"\n"}'
done   # every row must print "keep"
```

Verify afterwards (CRDs survived, CRs intact, the bundle release is gone):

```bash
kubectl get crd | grep -E 'agentgateway\.dev|kagent\.dev|kmcp\.dev|agents\.x-k8s\.io'
kubectl get helmrelease -A | grep agentic-platform-crds   # expect: no rows
```

## \<current\> → \<next\> (muster app-owned CRDs)

muster's CRDs (`MCPServer` / `Workflow`) move from the `agentic-platform-crds`
bundle into muster's own app chart (app-owned CRDs). The `muster` component now
sets `crds: CreateReplace` and no longer `dependsOn` the bundle; `agentic-platform-crds`
drops its `muster-crds` dependency (it keeps agentgateway / kagent / agent-sandbox CRDs).

### What changed

- `agentic-platform-crds` no longer ships `mcpservers` / `workflows.muster.giantswarm.io`.
- The `agentic-platform` `muster` component renders `spec.install.crds: CreateReplace`
  and `spec.upgrade.crds: CreateReplace`, so muster's release applies and upgrades
  its own CRDs (from the muster chart's `crds/` dir) atomically with the app.
- `agentic-platform-mcps` now `dependsOn: [muster, agentic-platform-crds]` (its
  `MCPServer` CRs need the muster-owned CRD; its agentgateway CRs need the bundle).

### Operator action: none (non-destructive automatic handoff)

The live muster CRDs carry `helm.sh/resource-policy: keep` (injected by the prior
bundle handoff), so dropping the `muster-crds` dependency does **not** delete them —
Helm's prune is blocked by `keep`, and no `MCPServer` / `Workflow` CR cascade occurs.
On the next reconcile the muster release applies the (identical-content) CRDs via
`CreateReplace` and owns their upgrades thereafter. Verify afterwards:

```bash
kubectl get crd mcpservers.muster.giantswarm.io workflows.muster.giantswarm.io
helm get manifest <muster-release> -n <ns> | grep -c 'kind: CustomResourceDefinition'  # CRDs now ride muster
```

Prerequisite: a muster app chart version that ships its CRDs in `crds/` (resolved by
the `muster` component's `versionRange`).

## 0.2.0 → \<next\> (two-chart CRD split)

CRDs are no longer bundled in `agentic-platform`. They now ship in the companion **`agentic-platform-crds`** chart, which must be installed (and Established) **before** `agentic-platform`. There are now **two releases** from this repo, in order.

### What changed

- The `agentgateway-crds` sub-chart dependency is **removed** from `agentic-platform`. (`agentgateway-crds.enabled` in your values is now a no-op — drop it.)
- `muster.crds.install` is set to `false` by the umbrella, so the bundled `muster` sub-chart renders no CRDs.
- The five CRDs (3 × `agentgateway.dev`, 2 × `muster.giantswarm.io`) are provided by `agentic-platform-crds`.
- `helm template agentic-platform` now emits **zero** `CustomResourceDefinition` objects (CI guards this).

### Install ordering

```bash
helm upgrade --install agentic-platform-crds \
  oci://gsoci.azurecr.io/charts/giantswarm/agentic-platform-crds \
  --version <crds-chart-version> -n muster --create-namespace

kubectl wait --for=condition=Established \
  crd/agentgatewayparameters.agentgateway.dev \
  crd/mcpservers.muster.giantswarm.io

helm upgrade --install agentic-platform \
  oci://gsoci.azurecr.io/charts/giantswarm/agentic-platform \
  --version <chart-version> -n muster -f values.yaml
```

Flux users: add `dependsOn: [{ name: agentic-platform-crds }]` to the `agentic-platform` HelmRelease (see README).

### One-time CRD ownership handoff (required)

The `0.2.0` `agentic-platform` release **owned** the agentgateway and muster CRDs (Helm metadata `meta.helm.sh/release-name=<your-platform-release>`). The new `agentic-platform-crds` release will refuse to adopt CRDs owned by a different release. Re-annotate the existing CRDs so the CRDs release takes ownership and they survive future platform uninstalls. Run this **before** installing `agentic-platform-crds`:

```bash
# Replace `muster` with the namespace your agentic-platform-crds release installs into.
for crd in $(kubectl get crd -o name | grep -E 'agentgateway\.dev$|muster\.giantswarm\.io$'); do
  kubectl annotate "$crd" \
    meta.helm.sh/release-name=agentic-platform-crds \
    meta.helm.sh/release-namespace=muster --overwrite
  kubectl label "$crd" app.kubernetes.io/managed-by=Helm --overwrite
done
```

After the handoff, the muster CRDs become `helm.sh/resource-policy: keep`-protected via `agentic-platform-crds`. The agentgateway CRDs remain unprotected (upstream gap — see README "CRD lifecycle"); uninstalling `agentic-platform-crds` still deletes them and cascades to all agentgateway CRs.

### muster / muster-crds version alignment

`agentic-platform-crds` pins `muster-crds`; `agentic-platform` pins `muster`. Keep the two muster versions aligned so the CRD schemas match the controller. An identical-content `muster-crds` bump is a no-op upgrade.

## 0.0.0 → 0.1.0 (first stable release — pending)

### OTel defaults added for agentgateway data plane

`gateway.parameters.dataPlaneEnv` now defaults to:

```yaml
- name: OTEL_EXPORTER_OTLP_ENDPOINT
  value: http://otlp-gateway.kube-system.svc:4317
- name: OTEL_EXPORTER_OTLP_PROTOCOL
  value: grpc
```

This requires an `otlp-gateway` Service in `kube-system` (provided by the Giant Swarm observability platform). On clusters without it, the agentgateway data-plane logs connection errors to the exporter but starts normally. Disable with:

```yaml
gateway:
  parameters:
    dataPlaneEnv: []
```

### Bundled Valkey + OAuth server are ON by default

`valkey.enabled` and `muster.muster.oauth.server.enabled` both default to `true`. Operators must supply per-cluster fields up-front or the muster sub-chart's fail-guards reject install:

| Field | Why |
|---|---|
| `muster.muster.oauth.server.baseUrl` | OAuth issuer URL (HTTPS, public muster hostname). |
| `muster.muster.oauth.server.dex.issuerUrl` | Dex issuer URL on this cluster. |
| `muster.muster.oauth.server.dex.clientId` | OAuth client pre-registered in Dex. |
| `muster.muster.oauth.server.existingSecret` | Secret carrying `dex-client-secret`, `registration-token`, `oauth-encryption-key`, `valkey-password`. |
| `valkey.valkey.auth.usersExistingSecret` | Same Secret (key `valkey-password`) — drives ACL auth on the bundled Valkey. Conventionally the same name as the muster OAuth Secret. |

For dev installs that don't need OAuth: set `muster.muster.oauth.server.enabled: false`, `muster.muster.oauth.server.storage.type: memory`, and (optionally) `valkey.enabled: false`.

### muster 0.1.193 → 0.1.197

`muster.ciliumNetworkPolicy.*` is removed. Migrate:

```yaml
muster:
  networkPolicy:
    enabled: true            # was: ciliumNetworkPolicy.enabled
    flavor: cilium           # new — mirrors umbrella's networkPolicy.flavor
    cilium:
      allowClusterIngress: true  # was: ciliumNetworkPolicy.allowClusterIngress
```

The muster sub-chart now also ships a `kubernetes` flavor (vanilla `networking.k8s.io/v1 NetworkPolicy`) with the same CIDR replacements as the umbrella (`apiServerCIDR`, `clusterCIDR`, `worldExcludedCIDRs`). Muster's CiliumNetworkPolicy egress now covers the agentgateway data-plane on 8080 in the release namespace (upstream-proxy path) in addition to the existing Valkey egress on 6379.

### agentgateway-crds is a cluster prerequisite

Install `agentgateway-crds` before the agentic-platform release. Upstream `agentgateway` ships the controller and CRDs as separate charts at `oci://cr.agentgateway.dev/charts/` — we have no choice but to install both. Muster's CRDs continue to ship inside the umbrella via the muster sub-chart's `templates/crds.yaml`.

```
helm install agentgateway-crds \
  oci://cr.agentgateway.dev/charts/agentgateway-crds --version v1.2.1 \
  -n muster --create-namespace
```

#### Adopting pre-existing agentgateway CRDs

If a previous install applied agentgateway CRDs without Helm metadata, the new `agentgateway-crds` release refuses to take ownership. One-time adoption:

```bash
for crd in $(kubectl get crd -o name | grep -E 'agentgateway\.dev$'); do
  kubectl annotate "$crd" \
    meta.helm.sh/release-name=agentgateway-crds \
    meta.helm.sh/release-namespace=muster --overwrite
  kubectl label "$crd" app.kubernetes.io/managed-by=Helm --overwrite
done
```

### Public HTTPRoute is now operator-mandated

`muster.gatewayAPI.httpRoute.parentRefs` and `.hostnames` no longer default to the umbrella's internal `agentgateway` Gateway — that Gateway is for the data plane and is not exposed publicly. The muster sub-chart's fail-guard rejects install until both fields are set:

```yaml
muster:
  gatewayAPI:
    enabled: true
    httpRoute:
      parentRefs:
        - name: giantswarm-default
          namespace: envoy-gateway-system
          group: gateway.networking.k8s.io
          kind: Gateway
      hostnames:
        - muster.<cluster>.<base-domain>
```

### Data-plane Service forced to ClusterIP

The agentgateway controller hardcodes the data-plane Service to `type: LoadBalancer`. The umbrella overlays `spec.service.type: ClusterIP` via `AgentgatewayParameters` so the data plane stays internal — envoy-gateway-system fronts public traffic. Override with `gateway.parameters.serviceType: LoadBalancer` if running on a cluster without a front Gateway.

### NetworkPolicy flavors

`networkPolicy.flavor` now accepts `cilium` (default) or `kubernetes`. The previous `none` value is removed — opt out via `networkPolicy.enabled: false`. The `kubernetes` flavor renders vanilla `networking.k8s.io/v1 NetworkPolicy` but is best-effort: no entity selectors (`cluster`, `world`, `kube-apiserver` become CIDR ranges via `networkPolicy.kubernetes.{apiServerCIDR,worldExcludedCIDRs}`), no FQDN egress (`additionalEgressFQDNs` is ignored).

**Cross-subchart flavor switch.** Muster 0.1.197 ships the same `networkPolicy.{enabled,flavor,cilium.*,kubernetes.*}` shape as the umbrella, so the flavor switch is consistent across both. When selecting the `kubernetes` flavor (or running on a non-Cilium cluster), set:

```yaml
networkPolicy:
  flavor: kubernetes
muster:
  networkPolicy:
    flavor: kubernetes
valkey:
  ciliumNetworkPolicy:
    enabled: false   # giantswarm/valkey-app has no kubernetes-flavor CNP yet
```

Previous `muster.ciliumNetworkPolicy.{enabled,allowClusterIngress}` keys are gone — migrate to `muster.networkPolicy.{enabled,flavor,cilium.allowClusterIngress}`.

### Controller CiliumNetworkPolicy added

The umbrella now ships a separate policy for the agentgateway **controller pod** in addition to the data-plane pod. Previously the controller was unprotected (upstream agentgateway chart ships no policies). Data-plane selector switched to the Gateway-API standard label `gateway.networking.k8s.io/gateway-name=<gateway.name>`; controller selector matches the controller's `app.kubernetes.io/instance=<release>` triple.

### Bundled Valkey — giantswarm/valkey-app, ACL auth, default-on for muster

`valkey.enabled: true` now bundles [giantswarm/valkey-app](https://github.com/giantswarm/valkey-app) (wraps upstream `valkey-io/valkey-helm`) instead of `bitnami/valkey`. Differences operators must adopt:

- **Service name.** Writable endpoint is `muster-valkey.<namespace>.svc:6379` (single Deployment + Service — no primary/replica split, no `-primary` suffix).
- **Default muster wiring.** `muster.muster.oauth.server.storage.type` defaults to `valkey` and `storage.valkey.url` defaults to `muster-valkey:6379` at the umbrella level. Enabling OAuth + the bundled valkey requires no further override. Set `storage.type: memory` for dev, or override the `url:` for an out-of-band Valkey.
- **Auth model.** ACL-based, not flat-password. The bundled chart provisions a `default` user with `~* &* +@all` and reads the cleartext password from the `valkey-password` key of `valkey.valkey.auth.usersExistingSecret`. Muster sends `AUTH <password>` against the default user — standard backwards-compatible form.
- **Values shape.** Wrapper exposes upstream values under `valkey.valkey.*`:

  | Old (bitnami) | New (valkey-app) |
  |---|---|
  | `valkey.fullnameOverride: muster-valkey` | `valkey.valkey.fullnameOverride: muster-valkey` |
  | `valkey.image.tag: "9.0.4"` | `valkey.valkey.image.tag` (defaults to chart appVersion 8.1.4) |
  | `valkey.auth.existingSecret: <name>` | `valkey.valkey.auth.usersExistingSecret: <name>` |
  | `valkey.primary.persistence.{enabled,size}` | `valkey.valkey.dataStorage.{enabled,requestedSize}` |
  | `valkey.primary.resources` | `valkey.valkey.resources` |

- **Migration from bitnami.** Re-pointing `storage.valkey.url` from `muster-valkey-primary.<ns>.svc:6379` to `muster-valkey.<ns>.svc:6379` is sufficient at the URL layer; the previous bitnami StatefulSet's PVC is not consumed by the new Deployment-backed PVC (different name). Treat session storage as ephemeral when cutting over.

### `bootstrap.oauth.*` removed

The Helm `lookup`-based OAuth bootstrap Secret has been removed. Three Secret-injection paths remain: inline values, `existingSecret`, and the new umbrella-level `extraObjects: []`. See README "OAuth secrets".

### `extraObjects` added

New top-level umbrella key. Each entry is rendered through `tpl` and emitted alongside the chart. Useful for shipping the muster OAuth Secret manifest in the same Helm release.

### `gateway.parameters.dataPlane{Env,Volumes,VolumeMounts}` (unchanged from earlier draft)

Strategic-merge knobs on the AgentgatewayParameters template. Use when pushing OTel env vars or mounting cert-manager-issued CA bundles for `controller.xds.mode: tls` into the dynamically-rendered data-plane container.

## Template

```
## <previous-version> → <new-version>

### <Short title of breaking change>

<What changed, why, what operators must do. Always cite the value /
template / file involved so reviewers can grep for the change.>
```
