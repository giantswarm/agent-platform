# Upgrading agent-platform

Operator action required between releases. CHANGELOG.md captures the diff; UPGRADE.md captures what an operator has to *do*.

## \<current\> → \<next\> (the Substrate line re-pins to `v0.0.27-gs.3`: the `ate.dev` CRDs are kept on uninstall)

`components.substrate-crds` and `components.substrate` move to `>=0.0.27-gs.3 <0.0.28-0` and `kagent.substrateWorkerPool.workerImage` to `ghcr.io/giantswarm/substrate/ateom-gvisor:0.0.27-gs.3` — the line's third release of upstream 0.0.26, `v0.0.27-gs.2` plus one carried patch (giantswarm/substrate#12): the `substrate-crds` chart's three CRD templates (`workerpools`, `sandboxconfigs`, `csidriverconfigs.ate.dev`) carry `helm.sh/resource-policy: keep`, the convention the kagent line's `kagent-crds` already follows (giantswarm/kagent-upstream#10). Without it, uninstalling the platform on a cluster that runs its own Flux raced the CRD charts: helm-controller finalizes the platform `HelmRelease`s concurrently, `substrate-crds` could go before `substrate`, whose uninstall then failed for good on its `SandboxConfig` and left the meta `HelmRelease` stuck in deletion (giantswarm/agent-platform#385).

### Operator action

- **None to install.** The pins move together (`make verify-components` holds the two ranges, the BOM pin and the worker-image tag to one Substrate version); the release is inert behind the fleet's `<4.0.0` bound like every 4.x release before the cut-over.
- **Uninstall semantics change on Substrate.** Uninstalling the `substrate-crds` release now leaves the three `ate.dev` CRDs in place, as `kagent-crds` leaves the `kagent.dev` ones; their objects go with the releases that own them (the `substrate` release's `SandboxConfig gvisor-default`, the kagent release's `WorkerPool`), and a `WorkerPool` or `SandboxConfig` created by hand survives a Substrate uninstall until deleted explicitly. To remove the CRDs, `kubectl delete crd workerpools.ate.dev sandboxconfigs.ate.dev csidriverconfigs.ate.dev` — it deletes the objects behind them. A reinstall adopts the kept CRDs (the same release name and storage namespace, `ate-system`).
- **On a cluster that runs its own Flux the uninstall order is now the operator's only concern for the agents**: delete the agents' `HelmRelease`s first, while the kagent controller still runs, then the chart's `HelmRelease`; that Flux finalizes the component releases in any order and every one goes clean (README "Clusters that run Flux"). On an installation still on `v0.0.27-gs.2` or earlier, delete the component `HelmRelease`s in reverse dependency order by hand first (`kagent`, `substrate`, then the CRD charts).
- **The bundled engine's ordered teardown is unchanged**: `helm uninstall` still deletes the platform `HelmRelease`s in reverse dependency waves; it no longer depends on that order for Substrate.

## \<current\> → \<next\> (the agentgateway controller reaches an external JWKS host)

The agentgateway controller's network policy opens egress to every external JWKS host the `jwtAuthentication` routes name (giantswarm/agent-platform#312), in both network-policy flavours. The controller fetches each policy's JWKS and pushes the keys to the data plane over xDS, so this is the controller's egress, not the data plane's; the data-plane policies do not change.

New keys: `gateway.jwksEgress.external.fqdns`, `.cidrs` and `.port` (443), for an issuer the routes do not name and for one reached by address — `fqdns` as Cilium FQDN selectors in the cilium flavour, `cidrs` as IP blocks in both.

### Operator action

- **None on an installation whose JWKS hosts are in-cluster.** The whole render is byte-identical to before, in both flavours, for the fleet render and the lab render. `make verify-wiring` asserts that against `origin/main`.
- **An installation with an external issuer can drop its hand-written controller policy.** Name the issuer in the route's `jwtAuthentication.jwks` (host and port) and the chart's controller policy opens it: a `toFQDNs` `matchName` in the cilium flavour, an address block minus `networkPolicy.kubernetes.worldExcludedCIDRs` in the kubernetes flavour. Remove the hand-written `CiliumNetworkPolicy` after the release lands and confirm a request with a valid token is still accepted through the JWT policy.
- **A route on port 443 now originates TLS to the issuer without `jwks.tls.enabled`.** The port serves no plain HTTP, so the key is implied. An installation that set it stays as it is, and an in-cluster Dex on 5556 keeps its plain-HTTP fetch. Before this release, port 443 without the key left the fetch on plain HTTP and every caller read `401 token uses the unknown key`.
- **TLS implied by the port verifies against the system trust, not `global.identity.ca.secretName`.** That key is the CA of the platform's own identity provider, so it is the right default only for a route `jwks.tls.enabled` points at that provider deliberately, and it stays the default there. A public issuer on 443 is verified against the system trust, because a private CA signed no public certificate and pinning it would fail the fetch with the same `401`. A route that needs its own CA names it in `jwks.tls.caSecretName`, which wins in both cases.
- **A `jwks.host` is a hostname, and seven shapes now fail the render**, each of them a green render and that same `401` before. The message names the value to set.
  - An empty host. The JWKS backend renders no host and resolves nothing.
  - An empty `jwks.port`. The backend renders no port and the API server refuses it.
  - A host that carries its port (`dex.example.com:5556`, `[2001:db8::1]:443`). Split it into `jwks.host` and `jwks.port`.
  - An address literal of either family (`198.51.100.7`, `2001:db8::1`, `1.2.3.999`). The controller selects an external issuer by name. To reach one by address, set `jwks.host` to its name and name the blocks in `gateway.jwksEgress.external.cidrs`.
  - A host that is no hostname: a scheme or the JWKS path glued on (`accounts.google.com/keys`), an empty label (`a..b.example.com`), or a label that starts or ends with a hyphen (`keys-.example.com`). The host carries the issuer's name alone; its scheme belongs to `issuer` and the JWKS path to `jwks.path`. The kubernetes flavour opened its wide rule for such a host and the render stayed green, so the fetch failed with nothing to show for it.
  - A host of fewer than three labels (`dex`, `dex.giantswarm`, `okta.com`). It is neither a qualified Service name nor a public issuer, and a short Service name resolves through the pod's search path, which the controller's egress rule cannot follow. Write `<service>.<namespace>.svc.cluster.local` for an in-cluster issuer, or the full host for a public one. This guard follows `networkPolicy.enabled`.
- **`gateway.jwksEgress.enabled` is no longer demanded for an external `jwks.host`.** The render guards asked for it on any JWT policy; they now ask only when the host is in-cluster (`svc` as its third dot-separated label, with nothing, `cluster` or `cluster.local` after it), and only under `networkPolicy.enabled`. A public host that carries an `svc` label elsewhere (`a.b.svc.example.com`) counts as external and gets its own egress rule. An installation that set the key on to satisfy the old guard while pointing the route at an external issuer can turn it off: the in-cluster rule it renders reaches nothing. Leaving it on is harmless.
- **A `jwks.host` is classified in its normalized form: lower case, root dot removed.** `dex.giantswarm.svc.cluster.local.` and `DEX.giantswarm.svc.cluster.local` are the Service they name, and neither renders an external rule. An installation that wrote either form gains nothing to do; before this release the trailing-dot form rendered a `toFQDNs` selector in the cilium flavour and a wide address block in the kubernetes one.
- **In the kubernetes flavour the wide rule is IPv4 only.** A named host renders `0.0.0.0/0` minus `worldExcludedCIDRs`, so an issuer the cluster resolves to an IPv6 address, or one inside a private block, is not reached. Name its blocks in `gateway.jwksEgress.external.cidrs`, which takes either family in both flavours. That rule narrows anything only while `networkPolicy.kubernetes.apiServerCIDR` is a real API-server block: its default is `0.0.0.0/0` on every port, and the policy's first rule carries it.
- **A second issuer the routes do not name goes in `gateway.jwksEgress.external`.** The cilium flavour selects it by name in `external.fqdns` (`- matchName: keys.example.com`, or a `matchPattern`), the kubernetes flavour by address in `external.cidrs`; both open `external.port`. An `external.fqdns` item is a selector object, never a bare string: the schema refuses a string list, which the Cilium CRD would refuse at apply. A cilium installation that carried such an issuer in a hand-written policy can move it here.

## 3.x → 4.0 (the kagent line: kagent API v2)

4.0 replaces the kagent the platform runs. `components.kagent` and the new `components.kagent-crds` deliver **kagent API v2** — `kagent.dev/v1alpha3`: `AgentTemplate`, `Harness`, `ModelConfig`, `ModelProviderConfig`, `RemoteMCPServer`; agents run as Agent Substrate actors in gVisor worker pods — from the kagent line [giantswarm/kagent-upstream](https://github.com/giantswarm/kagent-upstream) (`oci://ghcr.io/giantswarm/kagent/helm`, releases `0.11.0-gs.N`) instead of the `giantswarm/kagent` 0.x wrapper (kagent 0.10, `v1alpha2`) from gsoci. The 4.x line of this chart is the kagent API v2 migration (giantswarm/giantswarm#37705); this release is its first step — the roster and the values — and the connectivity chart's v1alpha3 templates, the platform Harness, Substrate inside the chart and the cut-over of an installation's agents follow in the 4.0.x releases.

### What changes

- `components.kagent`: `oci://ghcr.io/giantswarm/kagent/helm`, range `>=0.11.0-gs.1 <0.11.1-0`, `dependsOn: [kagent-crds]`, no `crds:` policy. New `components.kagent-crds` (same source and range; the CRDs as templates with `helm.sh/resource-policy: keep`; follows `components.kagent` unless switched explicitly — an explicit `enabled: false` with kagent on fails the render). The connectivity release `dependsOn` both.
- `kagent:`: `registry: ghcr.io`, `tag` (one build of the line; upstream's chart falls back to `.Chart.Version`, invalid under helm-controller), the image names `giantswarm/kagent/{controller,golang-adk,ui}`, `controller.substrate.*`, `substrateWorkerPool`, `controller.metrics.enabled: false`, pgvector on the bundled Postgres, the new `kagent.harness.image` (the Go ADK image by digest). Removed: the ten `kagent.<example-agent>` blocks, `kagent.controller.skillsInitImage`, the `METRICS_BIND_ADDRESS` / `METRICS_SECURE` env entries. `kagent.serviceMonitor.enabled` defaults to `false` in both charts.
- Sibling ranges: `agent-platform-connectivity` `>=4.0.0 <5.0.0`, `agent-manager` `1.x`, `backstage` `>=1.0.0 <3.0.0`, `model-manager` `>=0.20.0 <1.0.0`; `agent-manager.agentChart.semver` `1.x`; no `semverFilter` by default.
- **Agent Substrate ships inside the chart**: the new roster entries `components.substrate-crds` and `components.substrate` (the Giant Swarm Substrate line, `oci://ghcr.io/giantswarm/substrate/helm`, range `>=0.0.27-gs.2 <0.0.28-0`, its floor the tag of `kagent.substrateWorkerPool.workerImage`; both follow `components.kagent`; both land in `ate-system` through the new roster key `components.<name>.targetNamespace`), the `substrate:` values block, the connectivity release's `pre-install,pre-upgrade` bootstrap hook (the CA/JWT pools, the trust anchor, ate-api-server's authentication config — minted once, kept for ever), the CNPG `Database` `postgres.databases.substrate` with the derived connection Secret `<clusterName>-substrate-app` (the `post-install,post-upgrade` hook `<release>-postgres-databases`), the Kyverno `PolicyException`s `substrate-atelet`, `substrate-workers`, `substrate-control-plane`, `substrate-podcertificate-controller`, the network policies of Substrate's hops in both flavours, and the new required value `kagent.harness.snapshotLocation`. README "Agent Substrate"; [docs/substrate-security.md](./docs/substrate-security.md).
- **The install order**: `substrate-crds`, `kagent-crds` → `agent-platform-connectivity` → `substrate` → `kagent` → the managers. The connectivity release `dependsOn` the two CRD components (and muster, agentgateway, cloudnative-pg, kserve-resources), **no longer `kagent`**; `kagent` and `substrate` `dependsOn` the connectivity release, whose hooks mint what their pods start against. `components.kagent.installDisableWait` is gone — every component install waits for its workload again.
- **Kyverno**: `kyvernoPolicies.rules` (a map rule → ClusterPolicy of the cluster's Pod Security Standard policies) replaces `kyvernoPolicies.seccompPolicyName`, `seccompRuleNames`, `volumeTypesPolicyName` and `volumeTypesRuleNames`. The `kagent-declarative-seccomp` PolicyException (the v1alpha2 agent Deployments, label `app: kagent`) is gone; `substrate-workers` is its successor. The `app: kagent` agent-pod network policy (`<release>-kagent-agent-muster-egress`) is gone with the pods; the actors' destinations are opened on Substrate's egress gateway (`substrate-atenet-egress`), and the kagent controller admits it.

### Operator action

- **The release is inert until an installation admits it.** Every Giant Swarm installation selects this chart at `>=2.5.5 <4.0.0` (giantswarm/management-cluster-bases#739); nothing changes on an installation until its own bound is lifted — the per-installation cut-over of the migration (giantswarm/management-cluster-bases#737, #738), which destroys the v1alpha2 agents and their conversations (upstream's hard cut) and needs the prerequisites below in place first. Do not lift the bound for this release alone.
- **Cluster prerequisites with kagent on**: Kubernetes 1.35 (Substrate uses `certificates.k8s.io/v1beta1` `PodCertificateRequest`; 1.34 serves it only as `v1alpha1`) with the feature gates `ClusterTrustBundle`, `ClusterTrustBundleProjection` and `PodCertificateRequest` on kube-apiserver, kube-controller-manager **and every kubelet** — giantswarm/cluster#1005 turns them on by default; until that release reaches an installation the cluster chart's `internal.advancedConfiguration.{controlPlane.apiServer,controlPlane.controllerManager,kubelet}.featureGates` sets them (each list replaces the chart's default list: repeat the defaults next to the three gates, on all three components); enabling them rolls the control plane and every node, so it is done ahead of the cut-over. A live render (helm-controller) of this release with kagent on refuses a cluster that does not serve `certificates.k8s.io/v1beta1/PodCertificateRequest`, naming the gates — the kubelet gates it cannot see. Agent Substrate itself comes with the chart now: nothing to install by hand, no `kubectl-ate` step; an installation that ran Substrate from outside the chart (a lab) hands `ate-system` over to the chart's releases (README "Agent Substrate"). A cluster that reaches no public registry needs pull-throughs for `ghcr.io/giantswarm` (the kagent and Substrate images and charts), `docker.io/alpine/k8s` and `docker.io/alpine/openssl` (the hook images; `hooks.kubectlImage`, `hooks.opensslImage`) and the gVisor release asset from `storage.googleapis.com` (the `SandboxConfig`'s `spec.assets`).
- **The `HelmRelease` that installs the meta chart needs `spec.timeout: 12m` or more** (helm-controller's default is 5 minutes). Its install is the Helm SDK's `--wait`, which returns when every component `HelmRelease` is Ready; with kagent on the first 4.x reconcile installs nine of them plus Substrate and kagent in dependency order — about 5–6 minutes through a cluster's own Flux (220 s for the components under `helm install --wait`, Ready through Flux in 289 s in the green run) — so a `HelmRelease` on the default fails its first 4.x reconcile on the timeout and is retried. Measured by the ATS own-Flux scenario of giantswarm/agent-platform#380 (`tests/ats/test_own_flux.py` sets `12m`; CircleCI job 5941 hit the default, job 6033 passed). Giant Swarm's management-cluster base already sets `20m` (`extras/agent-platform/helm-release.yaml` in giantswarm/management-cluster-bases); a per-installation cut-over patch must not lower it. The uninstall through that Flux takes about 30–105 s and has to remove the component releases in reverse-dependency waves (giantswarm/agent-platform#385; README "Uninstalling").
- **`kagent.harness.snapshotLocation` is required with kagent on** (the render fails without it): the Substrate snapshot location the platform Harness writes the actors' snapshots to — `s3://<bucket>/<prefix>` on the installation's S3 bucket (CAPA: provision the bucket and an IRSA role for Substrate's `atelet` and `ate-api-server` ServiceAccounts; the bucket is the installation's infrastructure), or an S3-compatible store with its endpoint and credentials in `substrate.atelet.extraEnv`. Set it in the per-installation values before lifting the bound.
- **Substrate's database** rides the platform's CNPG Cluster where `postgres.enabled` is on (the fleet): the `Database` `<clusterName>-substrate` and the derived Secret `<clusterName>-substrate-app` in `ate-system` appear with the connectivity 4.0 release; nothing to set (`substrate.postgres.enabled: auto`). The Cluster's network policy admits `ate-api-server` on 5432. An installation without the Cluster runs Substrate's bundled single-instance Postgres unless it names `substrate.postgres.connectionString`.
- **Kyverno values to move**: `kyvernoPolicies.seccompPolicyName`, `seccompRuleNames`, `volumeTypesPolicyName`, `volumeTypesRuleNames` → `kyvernoPolicies.rules` (rule → ClusterPolicy; the defaults are upstream kyverno-policies' names, which Giant Swarm clusters use — an installation that never overrode the four keys sets nothing). The schema refuses the old keys.
- **Scheduling**: `atelet` runs on every node the DaemonSet controller admits (on a Giant Swarm cluster the worker nodes — the control-plane taint keeps it off those); pin it with `substrate.atelet.nodeSelector` / `tolerations` / `affinity` where a node pool is dedicated. The WorkerPool is pinned to `amd64` (`kagent.substrateWorkerPool.template.nodeSelector`); an arm64 installation sets `arm64` — never both.
- **Values to drop**: the `kagent.<example-agent>` blocks, `kagent.controller.skillsInitImage` and the `METRICS_*` entries of `kagent.controller.env` — the kagent chart has no schema, so it would carry them on silently, and this chart's schema no longer enumerates them. `components.kagent-crds.enabled: false` next to `components.kagent.enabled: true` fails the render. The fleet template's controller-level `KAGENT_PROPAGATE_TOKEN` entry is inert on the line (the Go ADK reads it from the Harness's environment) and may stay until the fleet values follow (giantswarm/shared-configs#732).
- **Uninstall semantics**: uninstalling the `kagent-crds` release leaves the CRDs and every `AgentTemplate` / `RemoteMCPServer` in place — the line's `keep` policy on its CRD templates, carried by the line from giantswarm/kagent-upstream#10 on; do not uninstall `kagent-crds` on a release of the line that predates it. The removed v1alpha2 CRDs are deleted only by the migration's contract phase.
- **A BOM** pins `kagent` and `kagent-crds` to the same `-gs.N` release (one build); `examples/customer-bom.yaml` shows the shape. A re-pin of the line is one values change: `kagent.tag`, `kagent.harness.image` and, when the upstream base moved, the two ranges (README "Re-pinning the kagent line").
- **Metrics**: the kagent controller `ServiceMonitor` and metrics `Service` are no longer rendered — the line's controller serves no `/metrics`. The fleet's `KagentControllerDown` rule reads kube-state-metrics and needs no change.
## \<current\> → 4.0 (the kagent controller route is a `GRPCRoute` with the JWT policy on by default)

The kagent API v2 controller (giantswarm/giantswarm#37705) serves gRPC, gRPC-Web and A2A v1 on one port and no REST. `kagent.controllerRoute` now renders a `GRPCRoute` matched by gRPC service (`kagent.api.v1alpha1.{AgentInstanceService, AgentTemplateService, ModelService, SystemService}`, `lf.a2a.v1.A2AService`) on the data-plane and the public Gateway, with the JWT policy **on by default** in `Strict` mode and an identity transformation that sets `x-user-id` from the verified `email` claim (giantswarm/agent-platform#345; docs/authentication.md, "The kagent controller route"). The REST route on `/kagent` and `kagent.controllerRoute.pathPrefix` are gone.

### Operator action

- **Remove `kagent.controllerRoute.pathPrefix`** wherever it is set: the render fails naming the key. There is no path prefix any more — clients dial `https://<kagent.controllerRoute.hostname>` (gRPC origin) or `grpc://agentgateway.<release namespace>.svc.cluster.local:8080` in-cluster and append `/<service>/<method>` themselves.
- **Name the issuer and open `gateway.jwksEgress`** on every installation with the route on. The policy takes its issuer from `global.identity.issuerUrl` — the platform's one login provider, which the fleet template does not set today — or from `kagent.controllerRoute.jwtAuthentication.issuer` (`https://dex.<cluster>.<base-domain>`, the URL in the tokens' `iss`); without either the render fails naming both keys. `gateway.jwksEgress.enabled: true` (plus the issuer's `namespace` and `port` when the JWKS is fetched in-cluster) lets the data plane fetch the keys; `kagent.controllerRoute.jwtAuthentication.jwks` names the endpoint (`host`, `port`, `path`; `tls.enabled` with `tls.caSecretName` for an issuer serving it over TLS). The fleet template's `jwksEgress` comment ("not needed in the default setup") described the muster `/mcp` path; for the controller route the JWT layer is the default, and the render refuses the route without the egress. Rendered with the fleet template plus these two keys, the connectivity chart produces both GRPCRoutes, the policy, the Envoy timeout policy and the cilium admission; as-is it stops at the issuer guard. An installation whose issuer is outside the cluster needs #312.
- **Every client of the controller presents a Dex-issued JWT and speaks gRPC.** Opaque muster tokens and Kubernetes ServiceAccount tokens are refused at the gateway (`401`); a token without the `email` claim is refused (`403`). The Dev Portal needs its 1.x release (giantswarm/backstage#2344 — the backend's Connect client against the app-config's `apiBaseUrl`, which is now the gRPC origin without `/kagent/api`); klaus-gateway needs its 1.x release (giantswarm/klaus-gateway#234 — A2A v1 over gRPC). Both are part of the installation's cut-over to 4.0; a 0.x portal or gateway against this route gets `404`s for its REST paths.
- **Drop the fleet's `klausGateway.a2a.url` override** (`http://kagent-controller.kagent.svc.cluster.local:8083/api/a2a/kagent` in shared-configs): it names a REST path that does not exist on the line and bypasses the gateway. The chart default `grpc://agentgateway.agent-platform.svc.cluster.local:8080` is the target; klaus-gateway 1.x reads the scheme (`grpc://` plaintext h2c, `grpcs://` TLS). Adjust the namespace only if the release is deployed outside `agent-platform`. giantswarm/shared-configs#732 drops it fleet-wide.
- **Drop the controller-level `KAGENT_PROPAGATE_TOKEN`** entry from `kagent.controller.env` (shared-configs likewise): on the line the controller only registers the name; the Go ADK reads it from the platform Harness's environment, which the connectivity chart renders (giantswarm/agent-platform#344). Leaving it is harmless but inert.
- **The route matches by service; the agentgateway component must be at chart ≥ 2.1.1** (controller `v1.5.1-gs.3`, which translates a service-only `GRPCRoute` match into the path prefix `/<service>/`; the meta chart's `components.agentgateway.versionRange` 2.x resolves it on every installation). On an agentgateway older than that a service-only match never routes (the MCP catch-all answers `406`): list the RPCs you need under `kagent.controllerRoute.grpc.services.<service>` until the component has moved. An RPC the kagent line adds is reachable without a values change on the service-only shape.
- **Keep `kagent.controller.auth.userIdClaim: email`** (the chart default). It is now read twice: by the controller (`AUTH_USER_ID_CLAIM`, with the line's trusted-proxy authenticator) and by the gateway's identity transformation. Changing it changes both; a claim name that is not a plain identifier fails the render.
- **Network policy:** the controller admits the agentgateway data-plane pods and the kagent UI only. Anything else that called the controller directly on `:8083` (a debugging pod, a custom integration) has to go through the gateway with a token. On the kubernetes flavour the admission narrows from the whole release namespace to the data-plane pods.
- **Envoy front Gateway (the fleet):** the public `GRPCRoute kagent-controller-public` replaces the `HTTPRoute` of the same name and carries HTTP/2 to the agentgateway Service; a `BackendTrafficPolicy kagent-controller-public` renders when `ingress.backendTrafficPolicy.enabled` is on (the fleet sets it), so A2A streaming turns are not cut by Envoy's default route timeout. One Helm upgrade of the connectivity release; no pod rolls; the kagent UI route gets its header filter in the same revision.
- **Local development without a front proxy:** `kagent.controllerRoute.jwtAuthentication.enabled: false` switches the policy and the transformation off; the controller then trusts `x-user-id` as sent. Never the fleet shape.

## 3.x → 4.0 (the connectivity chart renders the kagent catalog at `kagent.dev/v1alpha3`; the shared muster `RemoteMCPServer` is retired; the Kyverno `Agent` mutations are gone)

Connectivity 4.0 renders `kagent.modelConfigs[]` and `kagent.remoteMcpServers[]` as `kagent.dev/v1alpha3` objects in the kagent namespace — the only version the kagent line serves; the spec fields are unchanged, and CI validates every rendered kagent object against the line's CRDs at the pinned release (`make verify-kagent-crds`). The shared `RemoteMCPServer agent-platform/muster` (`allowedNamespaces.from: All` — the cross-namespace contract the Generic `agent` chart 0.x's default `serverRef` depended on) is no longer rendered: on kagent API v2 an `AgentTemplate` binds a `RemoteMCPServer` of its own namespace, and the Generic agent chart 1.x renders one per agent — muster's URL, `STREAMABLE_HTTP`, the `X-Muster-Toolset` header, the `kagent.dev/discovery: disabled` label — and binds it (giantswarm/agent#25). Where muster is becomes a value the platform hands to agent-manager from one helper, `agent-platform.musterMcpUrl`: its chart value `muster.url`, derived by the meta chart next to `flux.helmReleaseServiceAccount`; agent-manager composes it into every agent, and the portal sends none. The two Kyverno ClusterPolicies that mutated the controller's input — `<release>-kagent-declarative-pod-security` (`spec.declarative.deployment.*SecurityContext` on every `Agent`) and `<release>-kagent-srt-settings` (`enableWeakerNestedSandbox` into the per-agent `srt-settings.json` Secret) — are gone: there is no `Agent` CR, no per-agent Deployment and no config Secret on kagent API v2. `kyvernoPolicies` keeps every key: the seccomp `PolicyException` `kagent-declarative-seccomp` still renders (its re-targeting at the Substrate worker pods is giantswarm/agent-platform#342), so do the CNPG exception and `agentSandbox.podSecurity`. The `kagent-flux` tenant identity and the derivation of agent-manager's `flux.helmReleaseServiceAccount` are unchanged. See docs/authentication.md, "The per-agent muster server, and tool discovery by the kagent controller".

### Operator action

- **On the upgrade of the connectivity release Helm deletes the shared `RemoteMCPServer muster` and the two ClusterPolicies with the release** — no resource policy keeps them; the release's Helm diff shows the three removals. The `kyverno.io` objects left are the agent-sandbox ClusterPolicy, the kagent seccomp PolicyException and, with a pgvector extension image, the CNPG ImageVolume PolicyException.
- **Agents that still bind `agent-platform/muster`** — Generic chart 0.x releases created before the installation's migration — do not outlive the cut-over anyway: the 4.0 line serves no `kagent.dev/v1alpha2`, so no v1alpha2 `Agent` remains to bind anything (the `Agent` CRD goes in the migration's contract phase). Every agent on 4.0 is a Generic chart 1.x release with its own server; an installation's releases are rewritten by agent-manager's `migrate` (giantswarm/agent-manager#38; the connectivity migration Job of giantswarm/agent-platform#346). An `AgentTemplate` written by hand must bind a `RemoteMCPServer` of its own namespace — `{kind: RemoteMCPServer, name: muster}` resolves nothing on 4.0.
- **`agent-manager.muster.url`: leave it unset.** The meta chart derives it from `muster.fullnameOverride` and `muster.service.port`; a set value that differs fails the render naming both. On a default install the derived URL equals the agent chart's own default (`http://muster.agent-platform.svc.cluster.local:8090/mcp`), so nothing changes in the composed agents. The Dev Portal sends no muster URL of its own: it creates agents through agent-manager's tools (`create_agent` takes no muster argument), and agent-manager reports the URL it composes in `get_info`.
- **`kagent.modelConfigs[]` and `kagent.remoteMcpServers[]`** keep their values shape; the objects render at `kagent.dev/v1alpha3` — an installation that kept copies of them at `v1alpha2` outside the chart must move them. `tokenSecret` still renders `headersFrom: [{name: Authorization, valueFrom: {type: Secret, name, key: token}}]`, applied by the runtime after the propagated caller token (every agent reaches that server as the credential, not as the person).

## 3.x → 4.0 (the connectivity chart renders one platform `Harness` per managed namespace; the Generic chart's per-agent placement values are gone, capacity is the WorkerPool)

Connectivity 4.0 renders **one `kagent.dev/v1alpha3 Harness`, `kagent`**, in each namespace the platform manages agents in (today the kagent namespace). On kagent API v2 *how* an agent runs is the Harness — the Go ADK runtime image **by digest**, the environment (`KAGENT_PROPAGATE_TOKEN=true`, so a turn reaches muster as the signed-in person) and the Substrate policy (`workerPoolRef` → the WorkerPool the kagent chart creates, `snapshotPolicy.location` → `kagent.harness.snapshotLocation`) — and an `AgentTemplate` becomes Ready only when a Harness admits it. Admission is one label: the Harness's `allowedAgentTemplates.selector` matches `agent-platform.giantswarm.io/harness: kagent` (the value is the Harness's own name), which the Generic agent chart 1.x sets on every template (giantswarm/agent#25). The image is digest-pinned because the CRD demands it (a moving tag would silently re-boot every agent under the Harness), so a tag fails the render naming the requirement; the digest moves with `kagent.tag` on a re-pin. `Harness.status` is never written — readiness is read from `AgentTemplate.status.harnesses[]` (a template without the label reports `harnesses[]` empty with its generation observed, which agent-manager surfaces as "not admitted"). No free list of arbitrary Harness types is a platform value in 4.0; a second runtime (e.g. a `claude` Harness) is a later, separate object (giantswarm/giantswarm#37625). `make verify-kagent-harness` asserts the shape and the guards.

The Generic chart 1.x has **no** `runtime`, `replicas`, `resources`, `nodeSelector` or `tolerations` (giantswarm/agent#25): how and where an agent runs is the Harness and its WorkerPool, not the agent. The only capacity and scheduling knobs are the WorkerPool's `kagent.substrateWorkerPool.replicas` and `kagent.substrateWorkerPool.template` (`resources`, `nodeSelector`, `tolerations`, `nodeAffinity`, `priorityClassName`) — one worker hosts one actor at a time, so a worker's `resources.limits` bound one agent's sandbox (the memory limit its RAM, the CPU limit its vCPU count) and `replicas` bound the concurrently active agents. Defaults: four workers, each requesting `250m`/`512Mi` and limited to `2` vCPU / `2Gi` (README "Agent Substrate: worker capacity"); the pool is pinned to `amd64` (`template.nodeSelector`, giantswarm/agent-platform#342).

### Operator action

- **`kagent.harness.image` is set by the meta chart** (the Go ADK image by digest, next to `kagent.tag`; a re-pin moves both). Do not set it per installation. An installation that renders the connectivity chart on its own must pass a digest — a tag fails the render.
- **Per-agent placement moves to the WorkerPool.** An installation that sized individual agents through the Generic chart's `resources` / `nodeSelector` / `tolerations` / `replicas` sets them once on `kagent.substrateWorkerPool` instead — `template.resources` is the per-agent sandbox size, `replicas` the concurrency. Raise `template.resources.limits` for heavier agents; raise `replicas` for more concurrent agents (budget `replicas × limits` on the node pool). A mixed-architecture pool wedges actors whose golden snapshot is for the other architecture — keep one architecture per pool (`template.nodeSelector`).
- **A hand-written `AgentTemplate` must carry `agent-platform.giantswarm.io/harness: kagent`** to be admitted by the platform Harness; without it the controller leaves `status.harnesses[]` empty and the template never becomes Ready.

## 3.x → 4.0 (the cut-over of an installation's database and agents to the kagent API v2 line)

The kagent API v2 controller starts on a fresh database and refuses the 0.10 one (goose against the 0.10 `schema_migrations` table crash-loops); **no conversation history is migrated** — the Dev Portal tells its users the same. The connectivity chart therefore renders a second CNPG `Database` on the existing `kagent-pg` Cluster, `kagent-pg-kagent-v2` (`spec.name: kagent_v2`, owner `kagent`, the `vector` extension, `databaseReclaimPolicy: retain`; `postgres.databases`), and derives its connection Secret `kagent-pg-kagent-v2-app` from the Cluster's bootstrap Secret `kagent-pg-app` in a `post-install,post-upgrade` hook Job (the database name swapped in `dbname`, `uri`, `jdbc-uri`, `fqdn-uri`, `fqdn-jdbc-uri` and `pgpass`; idempotent — a re-run yields the same Secret). Backups keep covering both databases: same Cluster, same WAL archive. The 0.10 database `kagent` is left untouched for **30 days**, so a bad cut-over can be argued about with data, then dropped.

The agents follow through one Job per installation, `agent-platform-connectivity-agent-manager-migrate-<hash>` in the kagent namespace (a plain Job, not a Helm hook — its failure never fails the connectivity release, which no longer waits on Jobs; the hash is of its image, args and environment, so a changed image renders a new Job), which runs `agent-manager migrate` as the tenant identity `kagent-flux` (`kagent.fluxServiceAccountName`) when connectivity 4.0 first renders it: it rewrites every portal-created Generic-chart release to chart 1.x values (the removed keys dropped, `muster.toolNames` → `muster.tools`, every git skill pinned to a commit, `agent.iconUrl` kept, the per-namespace `agent` OCIRepository moved to `1.x`), emits the same rewrite as a **diff** for every GitOps-owned release (never written), and — once every `AgentTemplate` in the namespace is Ready on the platform Harness and no release is left on 0.x — deletes the leftover `kagent.dev/v1alpha2 Agent` objects and the five removed CRDs (`agents`, `sandboxagents`, `agentharnesses`, `memories`, `toolservers`). Every phase is idempotent and gated on the previous one; a run with nothing left to do changes nothing; a failed phase leaves the v1alpha2 objects in place. The Job, its migration-only bindings (the CRD ClusterRole + ClusterRoleBinding, the per-namespace reads) and this section retire in a later 4.x release once the last installation is cut over.

### Operator action

- **Before the bound is lifted**: the kagent controller's DSN mount must name the derived Secret — `kagent.controller.volumes[cnpg-dsn].secret.secretName: kagent-pg-kagent-v2-app` (the fleet template's `secretName` follows in giantswarm/shared-configs#732; `database.postgres.urlFile: /etc/cnpg/uri` stays). Name the namespaces the GitOps-owned agents are applied from in `agentManager.migration.gitopsNamespaces` (the fleet: `[flux-giantswarm]`). The read token for private skill repositories is the Secret `kagent-skills-token` (key `token`) in the kagent namespace, the one the private-skill agents use today; without it public repositories resolve and private refs are reported as pending. To rehearse first, set `agentManager.migration.dryRun: true` for one upgrade: the report and the diffs, nothing written.
- **Reading the report**: `kubectl -n kagent get configmap agent-manager-migrate-report -o yaml` — per release what changed (each dropped or renamed key, each skill with the commit it was pinned to), what was skipped and why (GitOps-owned with its diff, not the Generic chart, already on 1.x), the phase reached and what gates the next one. A finished Job stays a day: `kubectl -n kagent logs -l app.kubernetes.io/component=agent-manager-migrate --tail=-1`.
- **The emitted diff**: apply it to the owning repository as a pull request (the HelmRelease values, the OCIRepository range `>=0.2.1 <1.0.0` → `1.x`, the removal of the v1alpha2 `driftDetection.ignore` paths). Until it is merged the contract phase does not run and the report names the release as pending.
- **Re-running** (the contract phase once every template is Ready, or after a fix): clone the Job under a new name — `kubectl create job --from` accepts CronJob sources only, so: `J=$(kubectl -n kagent get job -l app.kubernetes.io/component=agent-manager-migrate -o name | head -1); kubectl -n kagent get "$J" -o json | jq 'del(.status, .metadata.uid, .metadata.resourceVersion, .metadata.creationTimestamp, .metadata.managedFields, .spec.selector, .spec.template.metadata.labels["batch.kubernetes.io/controller-uid"], .spec.template.metadata.labels["controller-uid"], .spec.template.metadata.labels["batch.kubernetes.io/job-name"], .spec.template.metadata.labels["job-name"]) | .metadata.name += "-rerun-1"' | kubectl create -f -` (the network policy and the bindings select and bind by label and identity, so the clone has the same rights). A connectivity upgrade that changes the Job's image, args or environment renders a new Job and runs it; one after the completed Job's day-long TTL re-creates and runs it (idempotent). The database Secret derivation is a hook and re-runs on every upgrade — `flux -n agent-platform reconcile helmrelease agent-platform-connectivity --force` forces one.
- **After 30 days**, drop the 0.10 database `kagent`. Where the installation sets `postgres.vector.extensionImage.reference` (the ImageVolume extension mode — the fleet template does), that database is a `Database` object of the chart (`kagent-pg-kagent`, `ensure: present` by CNPG's default), so a `DROP` by hand would be undone on the next reconcile; set `postgres.applicationDatabase.ensure: absent` in the installation's values instead — CNPG drops the database and keeps the object as the record (`kubectl -n kagent get database.postgresql.cnpg.io kagent-pg-kagent` shows `Applied`). Without an extension image there is no such object and the drop is the command: `kubectl -n kagent exec "$(kubectl -n kagent get pod -l cnpg.io/cluster=kagent-pg,cnpg.io/instanceRole=primary -o name)" -c postgres -- psql -c 'DROP DATABASE kagent'` (the primary; `psql` runs as the operator's superuser over the local socket). Either way the drop is final — a backup taken before it is the only way back. The bootstrap Secret `kagent-pg-app` keeps naming the dropped database; nothing reads it after the cut-over.
- **Fresh installations** (no 0.10 history) see the same objects: the `Database`, the derived Secret and a migrate Job that finds nothing to rewrite and no CRD to delete.

## \<current\> → \<next\> (kagent follows the wrapper's 0.x line)

`components.kagent.versionRange` is `>=0.2.0 <1.0.0` (was `0.2.x`): the floor stays at the flattened chart the wiring needs, the ceiling moves to the next major, as for the other 0.x components. The `giantswarm/kagent` wrapper released 0.3.0 and 0.3.1 on 2026-09-09 from CI-only changes — a `feat(ci)` title is a minor bump to git-cliff — with a chart identical to 0.2.2 in templates, values and dependencies; the minor-holding range excluded them, and would have excluded every following wrapper release, the next fix included.

### Operator action

- **None.** On every installation with kagent on, the kagent `OCIRepository` resolves 0.3.1 on its next poll and helm-controller upgrades the release. The rendered difference is two labels on every object — `helm.sh/chart: kagent-0.3.1` and `app.kubernetes.io/version: "0.3.1"` (the wrapper stamps its chart version as the app version) — and the pod annotations that hash the labelled ConfigMaps and Secret, so the kagent controller and UI pods roll once; no CRD, value or object changes otherwise. The meta chart's fleet render differs by the kagent `OCIRepository`'s `semver` line and nothing else.
- A BOM that pins `components.kagent.versionRange` to an exact version is unaffected; the example BOM keeps its `0.2.0` pin.

## \<current\> → \<next\> (`gateway.parameters.dataPlaneResources` is settable through the meta chart)

The meta chart's schema accepts `gateway.parameters.dataPlaneResources` (giantswarm/agent-platform#303): the agentgateway data-plane container's resources, which the connectivity chart reads and applied with its own defaults all along. The meta chart declares the key with those same defaults (`requests.ephemeral-storage: 50Mi`, `limits.ephemeral-storage: 512Mi`).

### Operator action

- **None.** The rendered platform objects are unchanged: the connectivity `HelmRelease`'s values now carry the key at the defaults the connectivity chart applied anyway, so helm-controller runs one upgrade of that release whose manifest is identical — the `AgentgatewayParameters` and the data-plane pods do not change.
- An installation that carried `gateway.parameters.dataPlaneResources` in its values for the standalone chart and dropped it to pass the meta chart's schema can set it again; an override reaches the data-plane container through the connectivity release as before.

## \<current\> → \<next\> (the 3.x meta chart selects its wiring chart below 4.0.0)

`components.agent-platform-connectivity.versionRange` is bounded to `>=1.0.0 <4.0.0` (giantswarm/agent-platform#348). The 4.x line of this repository is the kagent API v2 migration (giantswarm/giantswarm#37705); its connectivity chart renders `kagent.dev/v1alpha3` objects and must not reach a 3.x installation ahead of that installation's cut-over. Both charts release off one tag, and every installation's Flux re-resolves the connectivity range on each reconcile, so the bound has to be on the fleet before the first `v4.0.0` exists.

### Operator action

- **None.** The connectivity `OCIRepository` gets a new `spec.ref.semver` and keeps resolving the current 3.x release; nothing else renders differently.
- An installation moves to 4.x through the meta chart's own range (the fleet's `OCIRepository` bound, lifted per installation); the 4.0 chart resets this range to its own line.

## \<current\> → \<next\> (the kagent controller metrics Service selects kagent's own instance label; a first install with kagent on creates the kagent namespace)

Two fixes from the first lab run of the meta chart (giantswarm/agent-platform#305, #306). The connectivity chart's kagent controller metrics `Service` selects the kagent pods by kagent's own release name (`app.kubernetes.io/instance: kagent`) instead of the connectivity release's, so its `ServiceMonitor` gets endpoints. With the bundled engine (`components.flux.enabled: true`) and kagent on, the meta chart runs a `pre-install,pre-upgrade` hook Job `<release>-kagent-namespace` that creates the `kagent` namespace when it does not exist, so a first install on a bare cluster converges.

### Operator action

- **None.** On a management cluster with kagent and the monitors on, the connectivity release updates the metrics Service's selector (one Helm revision; a Service update, no pod rolls) and kagent controller scraping starts working — the Service shows endpoints, the scrape pool has targets. Where kagent is off, or the monitors are, nothing renders differently.
- **Giant Swarm management clusters (engine off): the meta chart's render is byte-identical.** The namespace hook renders only with the bundled engine; the fleet's bases keep creating the `kagent` namespace out of band and the connectivity release keeps adopting it.
- **An engine-on installation** (the Helm CLI, or self-managed) gains the hook on its next upgrade: on an installation that already has the namespace it finds it and leaves it alone (a `helm upgrade` runs one more short Job); a fresh install no longer needs the namespace pre-created — labs that did so can stop. `helm uninstall` is unchanged: the connectivity release owns the namespace and deletes it with everything in it (the agents' objects and Secrets included), as before; a reinstall that starts while it is still terminating waits for it to be gone.

## \<current\> → \<next\> (the muster `RemoteMCPServer` opts out of controller-side tool discovery)

The connectivity chart labels the shared `RemoteMCPServer agent-platform/muster` with `kagent.dev/discovery: disabled` whenever muster runs with OAuth on (`muster.muster.oauth.server.enabled`, the default). The kagent controller has no credential muster accepts when it lists the server's tools for the CR status, so every installation showed `Accepted=False (ReconcileFailed … Unauthorized)` on that CR (giantswarm/agent-platform#299). A kagent controller that knows the label reports `Accepted=True` (`DiscoveryDisabled`, empty inventory) instead; agents are unaffected either way — they resolve tools at run time with the propagated caller token. See docs/authentication.md, "Tool discovery by the kagent controller".

### Operator action

- **None.** The label is metadata on a CR the chart already owns; no pod rolls. The condition turns green once the installation runs a kagent controller with the opt-out (kagent-dev/kagent#2752; the `0.10.x` backport is kagent-dev/kagent#2753) — on a Giant Swarm management cluster that is the `giantswarm/kagent` wrapper picking up that upstream release under the meta chart's kagent range. Until then the condition stays as it was, and is expected.
- Do **not** add `headersFrom` with a controller credential to the muster server as a shortcut: the kagent runtime applies static headers after the propagated caller token, so every agent would act as that credential (docs/authentication.md explains).
- Installations that run muster **without** OAuth (`muster.muster.oauth.server.enabled: false`) get no label and keep controller-side discovery, which works anonymously there.

## \<current\> → \<next\> (self-management through the bundled Flux, on by default; the Helm CLI is day-0 only)

With the bundled engine on, the release now renders its own `OCIRepository` + `HelmRelease` and is adopted by its helm-controller (`gitops.self.enabled: auto` follows `components.flux.enabled`), a `ValidatingAdmissionPolicy` refuses `helm upgrade` / `helm rollback` from then on, and day-2 changes go through Secret `agent-platform-values`. See README "Self-management".

### Operator action

- **Giant Swarm management clusters: none.** The fleet runs with the engine off (`components.flux.enabled: false`), so `gitops.self.enabled: auto` resolves to off: no self objects, no hook, no policy, no identity. The fleet render is byte-identical to the previous release.
- **A Helm CLI installation with the engine on becomes self-managed on the `helm upgrade` to this version** — that upgrade is the last CLI upgrade it runs. What happens: the upgrade renders the self `HelmRelease` *suspended* (it does not exist yet), the admission policy and the values hook; after Helm applies and waits, the hook writes your `helm get values` into Secret `agent-platform-values` and starts the resumer, which resumes the `HelmRelease` once the release is `deployed`; helm-controller then adopts the release with one more revision (`helm history` shows it as `<version>+<oci digest>`) and follows `>=<version> <next major>.0.0` from `gitops.self.repository` (default `oci://gsoci.azurecr.io/charts/giantswarm`). From then on `helm upgrade` is refused with the reason in Helm's output and no revision written; change values by rewriting the Secret with your complete values file (README). **Requires Kubernetes ≥ 1.30** (the render refuses an older cluster). Pass your complete values file to that upgrade: what the hook writes into the Secret is exactly the upgrade's user-supplied values, and a partial `--set` would make them the whole of the release's values.
- **To opt out**, add `gitops: { self: { enabled: false } }` to the values file *before* that upgrade (and keep it there): the render is then the previous release's plus the two new pre-upgrade/pre-delete hooks (`<release>-self-stop-resumer`, `<release>-self-suspend`, no-ops without a self `HelmRelease`) and their namespaced identity `<release>-self`; the Helm CLI stays the day-2 tool. Installations that install **unreleased** charts (labs, the chart's own kind smoke) must set it: a self `HelmRelease` following the published range would replace the chart under test with the published one.
- **To hand a self-managed release back later**: `kubectl annotate namespace <ns> agent-platform.giantswarm.io/helm-cli=allow`, then `helm upgrade … --set gitops.self.enabled=false --force-conflicts` (once), keep the value in the values file. Without the annotation the upgrade is refused by the policy; with the annotation but the value still on, the render refuses (two writers on one release). Never `helm rollback` a self-managed release.
- **If the upgrade into this version fails** (`--wait` timeout), the self `HelmRelease` stays suspended and the policy is in force: either `helm uninstall --wait` and install again, or finish the bracket by hand — write the Secret and `kubectl -n <ns> patch helmrelease <release> --type merge -p '{"spec":{"suspend":false}}'`.
- `helm uninstall --wait` is unchanged for the operator: two more pre-delete hooks run first (weight -6 stops a resumer, -5 suspends the self `HelmRelease` and removes the values Secret); the uninstall still returns in under a minute on kind.
- Fallback should the admission policy ever be ruled out on a cluster (not shipped, documented for completeness): a pre-upgrade guard Job that refuses the CLI by the `managedFields` manager of the pending storage Secret (`Helm` = the CLI, `helm-controller` = the controller — Flux writes no labels there). It was measured to work but costs a `failed` revision and a controller re-assert per refused attempt, surfaces its reason only in the hook log, and is coupled to the controller's User-Agent; the policy replaced it.

## \<current\> → \<next\> (the chart brings its own Flux engine; `components.flux.enabled` defaults to `true`)

The meta-package gains the `flux-engine` subchart — the Flux Operator, one `FluxInstance` (source-controller + helm-controller under the multi-tenancy lockdown), the tenant identity `agent-platform-flux`, eleven CRDs in its `crds/` — switched by `components.flux.enabled`, **default `true`**, plus a render guard against a cluster that already runs Flux and pre-delete hooks for an ordered `helm uninstall`. With the engine on, every platform `HelmRelease` names `agent-platform-flux` (`gitops.serviceAccountName` default). See README "Installing".

### Operator action

- **Giant Swarm management clusters: none.** The fleet's `HelmRelease` carries `components.flux.enabled: false` since management-cluster-bases#721 (reconciled on every management cluster before this release shipped), and the render with that value is byte-identical to the previous release: no CRD, no operator, no hook, no `serviceAccountName`, the roster already said `flux: {enabled: false}`. The Flux CRDs' field managers on those clusters stay untouched (a disabled dependency's `crds/` are never collected).
- **Any other installation that runs its own Flux — through a `HelmRelease` or with the Helm CLI — MUST set `components.flux.enabled: false` before upgrading to this version.** Without it the render fails with `this cluster runs Flux; set components.flux.enabled=false or install the chart through it` (helm-controller marks the `HelmRelease` not Ready and retries; the Helm CLI refuses the upgrade before touching anything). The failure is the intended outcome: an engine next to a cluster's Flux would put a second, locked-down helm-controller on every `HelmRelease` in the cluster. Set the value and upgrade.
- **A Helm CLI installation on a cluster without Flux did not exist before this version** (the chart rendered Flux objects a Flux-less cluster could not accept), so there is no existing installation that gains the engine on `helm upgrade`. Should one ever turn the engine on for an existing release, `helm upgrade` would not install the subchart's `crds/` (Helm installs `crds/` on install only): apply them first — `helm show crds oci://gsoci.azurecr.io/charts/giantswarm/agent-platform --version <next> | kubectl apply --server-side -f -` — or install fresh. The common path is a fresh `helm install`.
- **Turning the engine off on an installation that runs it is refused** (`components.flux.enabled=false … the upgrade would delete the operator together with the FluxInstance it finalizes and hang`): uninstall the release instead — `helm uninstall --wait` tears it down in order — or delete the `FluxInstance` first.
- `gitops.namespace` cannot be combined with the engine (the tenant identity lives in the release namespace): the fleet's exempt-namespace layout goes with `components.flux.enabled: false`, a CLI installation leaves `gitops.namespace` empty.

## \<current\> → \<next\> (the kagent Namespace follows the kagent component)

The connectivity chart renders the `kagent` Namespace (`kagent.namespaceOverride`, when it differs from the release namespace) only while `components.kagent.enabled` is true.

### Operator action

- **None.** Where kagent is on, nothing changes. Where it is off, the connectivity upgrade deletes the empty, Helm-owned `kagent` namespace — on Giant Swarm management clusters it held nothing but the fleet's hand-written `kagent-flux` ServiceAccount and RoleBinding, which the fleet bases stopped applying before this release (the chart renders the identity itself where kagent runs). An installation that put its own objects into a `kagent` namespace on a cluster without kagent moves them out before upgrading, or turns kagent on.

## \<current\> → \<next\> (the standalone's wiring in the connectivity chart, behind the component toggles)

The connectivity chart renders, gated on `components.backstage`, `components.mcp-kubernetes`, `components.modelServing` (a new feature switch) and the kserve component toggles, what the agent-platform-standalone umbrella wired by hand: the Backstage app-config ConfigMap, route and config-reload hook; the mcp-kubernetes `MCPServer`; the KServe/vLLM model serving layer; the KServe controllers' network policies and guards. The seven values blocks of the extras now reach the connectivity release, the connectivity release `dependsOn` `muster`, and `backstage` `dependsOn` `agent-platform-connectivity`.

### Operator action

**No installation needs to act.**

- Giant Swarm management clusters: every toggle is off. The connectivity `HelmRelease` gets one Helm revision (its values gain the `backstage:`, `mcp-kubernetes:`, `cloudnative-pg:` and `kserve-*:` blocks and the `modelServing: {enabled: false}` roster line — the `modelServing:` block itself travels only while the switch is on — and `muster` in `dependsOn`); the objects it renders are byte-identical, so nothing changes on the cluster and no pod rolls.
- An installation moving from the standalone chart: the standalone's `components.backstage.*` and `components.mcp-kubernetes.*` wiring keys become `backstage.*` / `mcp-kubernetes.*`, `components.modelServing.*` becomes `components.modelServing.enabled` + `modelServing.*`, and `components.kserve.enabled` becomes `components.kserve-crd.enabled` + `components.kserve-resources.enabled` (`.llmisvc.enabled` → `kserve-llmisvc-crd` + `kserve-llmisvc-resources`). The full table is in the README, "Turning on the standalone's extras". The portal's installation name defaults to `agent-platform` (`backstage.installationName`), the standalone's release name; set it to your release name if it differed.
- `components.modelServing.enabled: true` without `components.kserve-crd` and `components.kserve-resources` on fails the render on a cluster that does not serve the `serving.kserve.io` APIs; turn the two on (they order themselves before the connectivity release) or set `modelServing.kserve.requireApi: false` for a KServe installed some other way. `modelServing.policies.enabled: true` with `kyvernoPolicies.enabled` resolving to `false` fails the render; the default `auto` follows Kyverno.
- `kagent.uiRoute.hostname` set while `kagent.oauth2-proxy.extraArgs.redirect-url` still derives from `global.domain` fails the render (the callback would land on the wrong host); set the redirect-url to the route's hostname.

## \<current\> → \<next\> (the chart renders the `kagent-flux` tenant identity)

The connectivity chart renders ServiceAccount `kagent-flux` and its RoleBinding to `cluster-admin` (namespace-scoped) in the kagent namespace whenever kagent is on, and one value — `kagent.fluxServiceAccountName` — names it into agent-manager (`flux.helmReleaseServiceAccount`, derived by the meta chart) and the portal (`agentPlatform.fluxServiceAccountName`). Six template fixes land with it (CHANGELOG, Fixed).

### Operator action

- **Giant Swarm management clusters: none.** The two objects already exist there, hand-written in the management-cluster bases with the same spec. helm-controller takes ownership of them on the upgrade (its default since 1.3) and both writers — the chart and the bases' kustomize-controller — set the same fields, so nothing changes on the cluster and no pod rolls. The hand-written copy is removed from the bases in a follow-up; until then both own the objects without conflict.
- Installations that set `agent-manager.flux.helmReleaseServiceAccount` themselves: drop it, or keep it equal to `kagent.fluxServiceAccountName`. A different value fails the render, naming the key. To rename the identity, set `kagent.fluxServiceAccountName` only.
- `ingress.mode: muster-direct` now fails the render when `kagent.controllerRoute.enabled`, `klausGateway.agentgatewayRoute.enabled`, or agent-platform-mcps' `agentgateway.enabled` with `mcpServers` set is on: those render agentgateway.dev objects a cluster without the agentgateway component cannot apply. Every management cluster runs `agentgateway-muster`; an installation that hits the guard turns the knob off or moves to an agentgateway-* mode.
- A leftover legacy toggle (`kagent.enabled`, `klausGateway.enabled`, …) now fails the render whatever its value, also `true` under a component that is on. Move it to `components.<name>.enabled` (see the section on component toggles below).

## \<current\> → \<next\> (cluster-shape knobs default to `auto`)

`kyvernoPolicies.enabled`, `networkPolicy.flavor`, `global.observability.metrics.serviceMonitor.enabled`, `dicebear.route.enabled` and `agentSandbox.podSecurity.enabled` default to `auto` (they were `true` / `cilium`): the object renders when its API group is served on the cluster (`kyverno.io/v1`, `cilium.io/v2`, `monitoring.coreos.com/v1`, `gateway.envoyproxy.io/v1alpha1`; the pod-security policy follows the resolved Kyverno answer). The meta chart detects once and resolves the knobs — and the component copies left at `auto` (muster's flavor and monitors, valkey's Cilium policy and PodMonitor, kagent's OTel exporters, oauth2-proxy monitor and OTLP header) — before it inlines a component's values.

### Operator action

**No installation needs to act.**

- A management cluster serves every one of those groups, so the render is byte-identical to the previous release: no object changes, no pod rolls.
- A cluster without them (kind, a plain cloud cluster) now renders the vanilla shape from the same chart — Kubernetes `NetworkPolicy` instead of `CiliumNetworkPolicy`, no `kyverno.io` object, no monitor, no avatar route — without a values file that describes the cluster. Values that set those knobs to `false` / `kubernetes` by hand keep working and can be dropped.
- An explicit value keeps winning over detection, on a knob (`networkPolicy.flavor: kubernetes` with Cilium present renders the kubernetes flavor everywhere) and on a component copy (`muster.networkPolicy.flavor: cilium` pins muster alone). `agentSandbox.podSecurity.enabled: true` together with `kyvernoPolicies.enabled: false` still fails the render, as before.
- `helm template` without `--api-versions` renders the vanilla shape. Pass the groups (`--api-versions kyverno.io/v1 --api-versions cilium.io/v2 --api-versions monitoring.coreos.com/v1 --api-versions gateway.networking.k8s.io/v1 --api-versions gateway.envoyproxy.io/v1alpha1`) to render the fleet shape offline; `helm install --dry-run=server` and helm-controller read the live cluster.

## \<current\> → \<next\> (the Argo CD render engine is removed; Flux is the only engine)

`templates/components.yaml` renders a Flux `OCIRepository` + `HelmRelease` per component and nothing else. The `gitops.engine: argo` branch — an Argo CD `Application` per component, ordered by `argocd.argoproj.io/sync-wave`, with `gitops.argo.project` / `gitops.argo.server` as its destination — is gone, and so are the two `gitops.argo.*` keys. It was never verified against a running Argo CD, no installation selected it, and the platform cannot run on Argo CD alone: agents are Flux objects on every path (the Backstage agent create flow and agent-manager write an `OCIRepository` + `HelmRelease` per agent). `gitops.engine` stays a key and accepts `flux` only (`enum: [flux]` in the schema, a template guard behind it).

### Operator action

- **Flux installations (every installation today): none.** The Flux render is byte-identical to the previous release; `gitops.engine: flux` set explicitly keeps rendering exactly the default.
- A values file that sets `gitops.engine: argo` fails the render — the schema first (`gitops.engine` must be `flux`), the template guard behind it with `gitops.engine=argo is not supported; flux is the only engine`. One that sets `gitops.argo.project` or `gitops.argo.server` fails the schema (`gitops` allows no additional property `argo`). Install Flux on the target (the cluster's own Flux, or the Flux Operator) and drop the keys. A non-GitOps install is not this chart's job; should one ever be needed, it is a plan of its own.

## \<current\> → \<next\> (the standalone chart's extras join `components.*`, off by default)

Backstage, mcp-kubernetes, the CloudNativePG operator and the four KServe charts are roster entries of this chart now (`components.backstage`, `components.mcp-kubernetes`, `components.cloudnative-pg`, `components.kserve-crd`, `components.kserve-resources`, `components.kserve-llmisvc-crd`, `components.kserve-llmisvc-resources`), all `enabled: false`, with their values in the new top-level blocks of the same names.

### Operator action

**No installation needs to act.** The default render adds nothing but seven `<name>: {enabled: false}` entries to the component roster inside the `agent-platform-connectivity` `HelmRelease`'s values, so that release gets one Helm revision with unchanged objects; no pod rolls. A management cluster keeps all seven off — it runs each of them as its own app. A BOM-pinned installation has nothing to pin unless it turns one on; `examples/customer-bom.yaml` carries the pins. An installation that turns Backstage or mcp-kubernetes on sets `global.domain` and `global.identity` first (README "Backstage, mcp-kubernetes, CloudNativePG and KServe").

## \<current\> → \<next\> (kagent moves to the flattened 0.2.x chart)

The `kagent` chart 0.2.0 flattened the upstream chart onto its chart root: upstream keys moved from `kagent.*` to the top level. The meta-package no longer nests the forwarded block, drops its own keys from it (`omitKeys`), and tracks the `0.2.x` range.

The top-level `kagent:` values block of THIS chart does not change. It stays flat, as it always was, and the connectivity chart keeps reading `kagent.namespaceOverride`, `kagent.controllerRoute`, `kagent.uiRoute`, `kagent.modelConfigs`, `kagent.remoteMcpServers` and `kagent.serviceMonitor` from it.

### Operator action

- None, if you set only keys inside the top-level `kagent:` block (the fleet default through `shared-configs`). The upgrade is in place: no resource is renamed. The kagent controller and UI Deployments roll once, because `helm.sh/chart`, `app.kubernetes.io/version` and the new `application.giantswarm.io/team` label change on every kagent resource and the pod annotations that hash them change with them.
- Pin `components.kagent.versionRange` to a `0.2.x` version if you pin ranges through a BOM. A `0.1.x` pin with this wiring installs the old chart with un-nested values, and it ignores them.
- `kagent.querydoc` is gone from the values: upstream 0.10.0 ships no querydoc subchart and the flattened chart's schema rejects the key. No installation set it.

## \<current\> → \<next\> (toolset presets; the muster range floors at 5.12.0)

The muster values gain `muster.muster.toolsetPresets` with the platform's two presets, `infrastructure` and `agent-platform`, selecting by the tool-group label (`agent-platform.giantswarm.io/tool-group`) the platform charts stamp on their `MCPServer` CRs. Agents refer to them as `preset:infrastructure` / `preset:agent-platform` in their `toolset`. muster reads the presets at startup, so the muster pod rolls once. See [docs/toolset-presets.md](./docs/toolset-presets.md).

`components.muster.versionRange` moves from `5.x` to `>=5.12.0 <6.0.0`: the `label:` rule exists from muster 5.12.0, and an older muster refuses to start on a preset that uses it. The floor makes the OCIRepository resolve a chart that has the rule before the HelmRelease applies the values that need it.

### Operator action

- **Wide-range installations (the default): none.** Flux resolves the range, the muster release rolls to ≥ 5.12.0 first, then applies the presets.
- **BOM-pinned installations:** pin `components.muster.versionRange` to `5.12.0` or later before or with this upgrade. A pin below 5.12.0 fails the muster pod's start with `toolsetPresets: … unknown rule "label"`. `examples/customer-bom.yaml` carries the current dogfooding snapshot. For the presets to resolve to anything, the label has to be on the CRs: agent-platform-mcps ≥ 0.9.0, agent-manager ≥ 0.3.0, model-manager ≥ 0.18.0; an older chart leaves the preset empty, not broken.
- **Installations that already defined their own `toolsetPresets`:** Helm merges the map — yours stay, the two shipped ones join. If you named one `infrastructure` or `agent-platform`, your definition wins over the shipped one; if you named one `read-only`, `none` or `full`, the render now fails naming it (muster would refuse to start on it either way).

## \<current\> → \<next\> (component toggles move into `components.<name>.enabled`)

A component's on/off switch is now `components.<name>.enabled`, and nothing else. The six per-chart toggles are removed.

`components.<name>.enabled` already existed and already won over the old `enabledFrom` indirection, so a component could be turned off two ways, and the connectivity chart only saw one of them. `components.agentgateway.enabled: false` together with `agentgateway.enabled: true` gave a cluster with no agentgateway release, an agentgateway ListenerSet ClusterRole for a controller that was never installed, and an ingress-mode guard that still believed the controller was there. Both charts now read `components.<name>.enabled`, the meta chart forwards its answers under that same key, and `make verify-meta` fails if the two ever disagree again.

### Operator action

Move each key you set. `<name>` is the `components:` key, which is the chart name, not the values-block name:

| Before | After |
|---|---|
| `agentgateway.enabled` | `components.agentgateway.enabled` |
| `valkey.enabled` | `components.valkey.enabled` |
| `mcps.enabled` | `components.agent-platform-mcps.enabled` |
| `kagent.enabled` | `components.kagent.enabled` |
| `klausGateway.enabled` | `components.klaus-gateway.enabled` |
| `agentSandbox.enabled` | `components.agent-sandbox.enabled` |

Move them **before** you upgrade. A leftover key fails the render with the message above, in both charts, and names the key to move. It is a hard failure on purpose: those value blocks accept unknown keys, so an ignored `enabled` would silently turn the component **off**, not on.

The `mcps:` block held nothing but its toggle and is removed. The chart's own values stay under `agent-platform-mcps:`.

Values that stay where they are: everything else in those blocks, including `agentSandbox.podSecurity.*`, `kagent.uiRoute.*`, `kagent.controllerRoute.*`, `klausGateway.a2a.*` and `klausGateway.obo.*`. Only the `enabled` key moves.

If you install `agent-platform-connectivity` on its own, without the meta chart, set the same `components.<name>.enabled` keys on it. Under the meta chart they are forwarded for you, and anything you set on the child release is overwritten.

## \<current\> → \<next\> (agentgateway moves to the flattened 2.x chart)

The `agentgateway` chart 2.0.0 flattened the upstream controller chart onto its chart root: upstream keys moved from `agentgateway.*` to the top level. The meta-package no longer nests the forwarded block, and it tracks the `2.x` range.

The top-level `agentgateway:` values block of THIS chart stays flat, as it always was, and the connectivity chart keeps reading `agentgateway.proxy.image` from it. The `enabled` toggle moves out of it in the same release — see the section below.

### Operator action

- Move `agentgateway.enabled` to `components.agentgateway.enabled` (see below). Otherwise the upgrade is in place: no resource is renamed, and the rendered output is unchanged apart from three non-selector labels.
- Pin `components.agentgateway.versionRange` to a `2.x` version if you pin ranges through a BOM. A `1.x` pin with this wiring installs the old chart with un-nested values, and its schema rejects them.
- `application.giantswarm.io/team` is new on every resource the agentgateway chart renders. It used to be a pod annotation only.

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

The agentgateway controller hardcodes the data-plane Service to `type: LoadBalancer`. The umbrella overlays `spec.service.spec.type: ClusterIP` via `AgentgatewayParameters` so the data plane stays internal — envoy-gateway-system fronts public traffic. Override with `gateway.parameters.serviceType: LoadBalancer` if running on a cluster without a front Gateway.

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
