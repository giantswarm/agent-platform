# agent-platform-connectivity

Giant Swarm Agent Platform — connectivity / integration layer. Renders the
consumer-side wiring that turns the platform components into a working whole on
a cluster: the public muster route and the agentgateway data-plane Gateway +
AgentgatewayParameters + HTTPRoutes + BackendTrafficPolicies, the NetworkPolicies,
the kagent and klaus-gateway routes, the kagent catalog (ModelConfigs and
RemoteMCPServers at kagent.dev/v1alpha3) and tenant identity, the CloudNativePG
Cluster, and — gated on the component toggles — the Backstage app-config and
route, the mcp-kubernetes MCPServer registration with muster and
the KServe/vLLM model serving layer (runtime, presets, cache, policies). Ships NO
workloads of its own — those are separate releases rendered by the agent-platform
meta-chart. The CRDs these CRs consume are app-owned: each component
(agentgateway, kagent, muster, …) ships its own CRDs, so this chart's HelmRelease
dependsOn those CRD-owning component releases.

**Homepage:** <https://github.com/giantswarm/agent-platform>

## Source Code

* <https://github.com/giantswarm/agent-platform>

## LLM routing

All agent inference traffic can go through the installation's agentgateway, so
that one component observes every model call. The data plane then emits GenAI
metrics for each request — tokens by type, cost in USD, request duration and
time to first token — with the calling agent as a label (`gateway.metricLabels`,
the Gateway's one metrics policy; see [Metric labels](#metric-labels)). The
`LLM usage` board this chart ships (`dashboards/llm-routing/llm-usage.json`) reads them.

The gateway holds no provider credential. Agent pods keep their own
`ANTHROPIC_API_KEY`, and the client `x-api-key` header passes through
untouched, so network reach to the LLM listener grants no spend.

The feature needs the agentgateway data plane (`ingress.mode:
agentgateway-muster`). The render fails when `llmRouting.enabled` is true and
that component is off.

### Rollout

Two values turn the path on, and the order matters.

1. Render the listener and the routing resources:

   ```yaml
   llmRouting:
     enabled: true
   ```

   This adds an `llm` listener to the data-plane Gateway, an
   `AgentgatewayBackend` for the provider, an `HTTPRoute` pinned to that
   listener, one Gateway-scoped `AgentgatewayPolicy` (the route-type map), and
   the model-price ConfigMap the cost counter reads. Nothing routes through it
   yet. The metric labels are not this path's: the Gateway's own `-metrics`
   policy carries them whether or not LLM routing is on (below).

   Verify the listener answers before you continue. From a pod in the release
   namespace:

   ```console
   curl -sS -o /dev/null -w '%{http_code}\n' \
     -X POST http://agentgateway.<release namespace>.svc:8081/v1/messages \
     -H 'content-type: application/json' \
     -H "x-api-key: $ANTHROPIC_API_KEY" \
     -H 'anthropic-version: 2023-06-01' \
     -d '{"model":"claude-sonnet-4-6","max_tokens":16,"messages":[{"role":"user","content":"hi"}]}'
   ```

2. Point kagent's default ModelConfig at the listener. This is the cutover:

   ```yaml
   kagent:
     providers:
       anthropic:
         config:
           baseUrl: http://agentgateway.<release namespace>.svc:8081
   ```

   The base URL lands in each agent's config Secret and the pod template
   carries a config hash, so one rolling update replaces the agent pods.

Roll back by removing the second value. The listener may stay.

### Acceptance

On the installation, after the cutover:

- `agentgateway_gen_ai_client_token_usage` carries `agent` and
  `agent_namespace` labels in the `giantswarm` Mimir tenant.
- `agentgateway_cost_catalog_lookups_total{status="Exact"}` grows. A
  `NoCatalog` or `Missing` status means the model has no price in
  `llmRouting.modelCatalog`.
- The Usage dashboard panels are populated.

### Notes

- The `llm` listener is cluster-internal. The data-plane network policy admits
  world traffic on 443 only, and the LLM route pins itself to its own listener
  by `sectionName`, so it never attaches to the public HTTPS listener in edge
  mode.
- Pinning is not enough on its own: `agent-platform-mcps` renders a catch-all
  `HTTPRoute` (`PathPrefix: /`, no `sectionName`, no hostname) that attaches to
  every listener of the same Gateway. An equal match is broken by creation
  timestamp and then alphabetically, both of which that route wins, so the LLM
  route matches `llmRouting.pathPrefixes` (`/v1`) instead — character count in
  the path match outranks both tiebreaks. A path outside those prefixes reaches
  the MCP backend and answers `mcp: client must accept both application/json
  and text/event-stream`, so `"*": Passthrough` in `llmRouting.routes` covers
  the other provider paths *under* the prefixes, not every path on the port.
- Agent pods keep their `world:443` egress, so a direct call to the provider
  still works. The listener is the paved road, not a wall. Egress tightening is
  a separate change.
- The LLM policy carries no `frontend.metrics`: the data plane honours one
  metrics policy per Gateway, and the labels describe every route, so they live
  in the Gateway's own `-metrics` policy — see [Metric labels](#metric-labels).
- An extra `kagent.modelConfigs[]` entry rides the listener unless it sets its
  own `baseUrl` or names a provider other than `llmRouting.backend.provider`.
  The `baseUrl` lands under the CRD's block for the entry's provider —
  `anthropic`, `openAI`, `sapAICore`, the three `ModelConfigSpec` gives one —
  never the lower-cased provider name, which the API server would prune; the
  render refuses a `baseUrl` on any other provider and a `provider` outside the
  CRD's enum (case-sensitive). `make verify-kagent-crds` sweeps every provider
  of the enum against the kagent line's CRD.
- The data plane is scraped by the packaging chart's own `PodMonitor`, under
  `agentgateway.monitoring.enabled` in the meta chart (the resolved
  `global.observability.metrics.serviceMonitor.enabled`). It selects the
  data-plane pods by GatewayClass and is not gated on `llmRouting.enabled`, so
  the MCP path is scraped too and the monitor exists before the cutover. This
  chart rendered one of its own, selecting by gateway name; it is gone, because
  two monitors of the same pods double every data-plane series.

## Metric labels

The data plane puts custom Prometheus labels on every metric it emits — the
HTTP, MCP and GenAI families alike, whatever route the request took — from one
Gateway-scoped `AgentgatewayPolicy`, `<release>-metrics`
(`templates/agentgateway/metrics-policy.yaml`, `frontend.metrics`), rendered
whenever the data plane is and an entry of `gateway.metricLabels` is enabled.
Each label is a CEL expression evaluated when the request completes; a failed
or empty expression renders `unknown` and keeps the series.

**One policy per Gateway.** agentgateway honours one metrics policy per
Gateway: custom labels replace, they never merge, and of two policies one is
dropped in silence — which one has changed between agentgateway releases, so
nothing may rely on it. Every label the platform wants therefore lives in
`gateway.metricLabels`, whichever route it describes; the labels are not the
LLM path's (`llmRouting.metricLabels` is gone; the schema refuses it) and
render without LLM routing.

**One map, keyed by label name.** Helm merges maps and replaces lists, so an
installation turns one default entry off or adds one without restating the
rest. Each entry is `{expression: <one-line CEL>, enabled: <bool, absent =
true>}`; the expression goes through `tpl`. An entry whose expression reads
`jwt` — `jwt.<claim>` or `jwt["<claim>"]` — is rendered only while a route of
the Gateway verifies a bearer (the kagent controller route with its JWT
policy, agent-manager's, model-manager's); without one it would read `unknown`
on every series.

```yaml
gateway:
  metricLabels:
    user:
      enabled: false          # no person label, no series per person
    team:
      expression: jwt.groups  # one more claim; string-valued claims only
```

**The defaults.** `agent` and `agent_namespace` name the calling workload,
resolved by source IP against the agentgateway controller's workload store —
not cryptographic, adequate for accounting, never for authorization. A caller
with a pod of its own is its ServiceAccount and namespace
(`source.unverifiedWorkload.serviceAccount` / `.namespace`): on the kagent
controller route klaus-gateway or the portal, on the MCP path an agent pod of
a kagent 0.10 installation. A kagent API v2 agent has no pod of its own: it is
a Substrate actor in the shared WorkerPool, and every actor connection leaves
the worker as a mTLS CONNECT through the Substrate egress (`atenet-egress`),
which authenticates the actor at CONNECT time and tunnels the bytes opaquely —
by source IP every agent's model call is the egress pod, and the egress cannot
add a header. The kagent runtime therefore sends the identity on every model
call as request headers, `x-kagent-agent` and `x-kagent-agent-namespace` (the
`AgentTemplate`'s name and namespace), and the default expressions read them
when, and only when, the source workload is the egress — its namespace and
ServiceAccount from the chart's Substrate names
(`agent-platform.substrate.egressCall`):

```cel
(source.unverifiedWorkload.namespace == "ate-system" && source.unverifiedWorkload.serviceAccount == "atenet-egress") ? request.headers["x-kagent-agent"] : source.unverifiedWorkload.serviceAccount
```

Every other caller keeps its own ServiceAccount, so no pod relabels itself by
sending the header; a call from the egress without the header reads `unknown`,
and the egress ServiceAccount itself appears in no sample. The identity is an
accounting identity throughout — the header is client-set and the predicate
reads the unverified workload — never an authorization input. The LLM usage
dashboard and the portal's Cost page read both labels.

`user` (on by default) carries the identity claim the kagent controller route
verifies in `Strict` mode and writes into `x-user-id`
(`kagent.controller.auth.userIdClaim`, `email`; `docs/authentication.md`),
read through `tpl` so the label follows the knob — and the same knob names the
claim on agent-manager's and model-manager's routes, kagent on or off.
klaus-gateway makes every controller call with the linked person's Dex
id_token and never as itself, so on that route every series carries a verified
person: a Slack turn is one `lf.a2a.v1.A2AService/SendStreamingMessage`
stream, its duration the turn's. The portal, a CLI and every other route that
verifies a bearer are labelled the same way. A route without a JWT policy —
the MCP path under `oauthMode: passthrough`, the LLM listener, whose calls
carry the ModelConfig's API key — renders `unknown`, with the one exception
the agent labels have: an actor's model call through the Substrate egress
carries the person of the turn as the kagent runtime sends it in
`x-kagent-user`, read behind the same predicate, so on a kagent API v2
installation tokens and cost carry the person too; a turn the runtime runs for
no person reads `unknown`.

On the installation (`route` is `<namespace>/<name>`):

```promql
sum by (user) (rate(agentgateway_requests_total{route=~".*/kagent-controller"}[1h]))
histogram_quantile(0.95, sum by (le, user) (rate(agentgateway_request_duration_seconds_bucket{route=~".*/kagent-controller"}[1h])))
```

Know what it costs: an email address in Mimir is personal data, and the label
multiplies the verifying routes' HTTP series by the number of people.
`gateway.metricLabels.user.enabled: false` drops the label and its series.

**Guards.** The schema holds every entry to `{expression, enabled}`. The
render fails, naming the entry, on a missing, empty or multi-line expression,
a name that is not a Prometheus label name, one the data plane's own series
carry or the scrape adds, one starting with `__`, and more than 16 enabled
entries. `kagent.controller.auth.userIdClaim` must be a bare identifier
wherever it is read as CEL — the controller route's identity header, the
`user` entry on any verifying route — since an expression that does not
compile takes the whole policy down. `make verify-metric-labels` asserts all
of it, and that no other policy of the chart carries a `frontend.metrics`
section.

## Data-plane availability

Every MCP call and, with LLM routing on, every model call crosses the
agentgateway data plane, a `Deployment` the agentgateway controller reconciles
from the `Gateway`. This chart shapes it through `AgentgatewayParameters`
(`gateway.parameters`):

- `replicas: 2` — two pods survive a node reboot or drain.
- `podDisruptionBudget` (`enabled: true`) — a drain evicts one pod at a time.
  The keys other than `enabled` are the PDB spec as written (`minAvailable`,
  `maxUnavailable`, `unhealthyPodEvictionPolicy`); with neither `minAvailable`
  nor `maxUnavailable` set, the template fills in `maxUnavailable: 1` (that
  default lives in the template so `minAvailable` can be chosen through the
  meta chart, where a null never reaches this chart's defaults — and it fills
  the missing field in rather than replacing the spec, so an
  `unhealthyPodEvictionPolicy` set on its own survives it). The render refuses
  a key that is not one of those three (keeping the two budget fields out of
  `values.yaml` is what leaves the block open in the schema, so a typo would
  otherwise be dropped in silence), both fields together, a string that is not
  a percentage from `0%` to `100%`, a fractional or negative number, an
  `unhealthyPodEvictionPolicy` outside the API's enum, and any budget that
  allows no eviction — an integer `minAvailable` at or above `replicas`, a
  percentage `minAvailable` that rounds up to every replica, a zero
  `maxUnavailable` — since it would hang every node drain.
- `spread` — one `topologySpreadConstraint` per `topologyKeys` entry
  (`kubernetes.io/hostname`; add `topology.kubernetes.io/zone` on a multi-zone
  pool), `whenUnsatisfiable: ScheduleAnyway` so a single-node lab still
  schedules both pods, and `matchLabelKeys: [pod-template-hash]` so a rollout
  spreads the new ReplicaSet against itself instead of against the revision it
  is replacing — without it, once the old pods drain both survivors can be
  left on one node and `ScheduleAnyway` never moves them back. A drain of that
  single node then waits on the budget (the replacement pod cannot schedule);
  turn `podDisruptionBudget.enabled` off there.

`gateway.parameters.podAnnotations` is empty by default. The
`karpenter.sh/do-not-disrupt: "true"` of giantswarm/agent-platform#431 was the
answer to a single data-plane pod; next to the three knobs above it would pin
both pods' nodes against Karpenter's consolidation, drift and expiry and the
budget would never be reached. Set it back on an installation that would rather
keep the streams open on a pod than let Karpenter churn its node.

`replicas`, `spread.maxSkew` and `spread.whenUnsatisfiable` are each refused
when unset. A `null` set through the meta chart does not travel — Helm deletes
the key at that layer — so it arrives here as a missing key, which no schema
keyword can floor; unguarded, `replicas` alone would render `0` and scale the
data plane to zero.

A pod's exit runs inside the data-plane shutdown window the controller's chart
defaults to. Both ends of it are deadlines measured from `SIGTERM`, not phases
that add up: 10 s still accepting so the endpoint is gone first, and draining
until the 55 s deadline, inside a 60 s grace period. Streamable-HTTP MCP
sessions survive a pod change (the session id carries the state, encoded with
the per-Gateway session key both pods share). Legacy SSE sessions are
pod-local, and pod-local costs more at two replicas than it did at one: the
session lives on the pod that answered `/sse`, the ClusterIP Service balances
each later POST on its own, and a POST that lands on the other pod finds no
session — so a legacy SSE client fails against a two-pod data plane whether or
not a pod ever changes. Use streamable-HTTP.
The meta chart forwards `agentgateway.controller.replicaCount: 2`. Leader
election is on (the controller's manager defaults `DisableLeaderElection` to
false), so the two pods do not fight: the status syncer runs on the leader
alone and the xDS syncer on every replica. What the second pod buys is a
pod-level failure and a rolling restart -- xDS keeps being served while one is
gone. It is **not** node redundancy: the packaging chart's schema admits no
`podDisruptionBudget`, no `affinity` and no `topologySpreadConstraints` for the
controller, so the two pods often land on the same node.

## The Backstage component on a default-deny cluster

With `components.backstage.enabled` and `networkPolicy.enabled`, the chart
renders a policy for the portal's own pods next to the config-reload hook's
policy. Without it the app boots into nothing on a default-deny cluster: DNS to
CoreDNS is denied, OIDC discovery to the identity provider times out and the
backend exits, and the route to the portal answers nothing.

The policy selects the backstage chart's pod labels — `app: <backstage.name>`
and `component: backstage`; `app` alone is not unique in the namespace — and
uses `backstage.port` (default 7007) as the app port, the port that chart gives
its container and its probes.

| | cilium flavour | kubernetes flavour |
|---|---|---|
| Objects | one `CiliumNetworkPolicy` | one ingress and one egress `NetworkPolicy` |
| The portal's route | the front Gateway's Envoy pods, in the namespaces the route names as its parents (`backstage.parentRefs`, else `global.gatewayApi.parentRefs`); the agentgateway data plane instead when `gatewayApi.gateway.create` makes this chart own the edge and the route names no parent of its own | the same, as a `namespaceSelector` and `podSelector` pair |
| Kubelet probes | `fromEntities: [host, remote-node]`, the pattern the manager, kserve and model-serving policies use | left to the CNI: vanilla `NetworkPolicy` selects pods, never the node. The bare `namespaceSelector` this flavour renders is the chart's pattern for it, and admits any pod in the cluster on the app port |
| DNS | CoreDNS in `kube-system`, with the proxy clause the FQDN selectors need | CoreDNS in `kube-system` |
| The identity provider | the issuer host by name on 443, plus the `cluster` entity on 443 and 10443 for an issuer behind an in-cluster Gateway | `0.0.0.0/0` minus `networkPolicy.kubernetes.worldExcludedCIDRs` on 443 |
| The kube-apiserver | the `kube-apiserver` entity | `networkPolicy.kubernetes.apiServerCIDR` |
| The edge | the `cluster` entity leg the identity-provider include renders, on 443 and 10443 | the Envoy pods of the Gateways the muster, kagent-controller and model-manager routes attach to, on 443 and 10443, plus the agentgateway data plane on 443 for each of those routes that attaches to it |
| muster | its pods in this namespace, on the muster Service port | the same, as a `podSelector` |
| The portal's database | its CNPG pods by `cnpg.io/cluster`, on 5432, while `backstage.database.engine` is `postgresql` | the same, as a `podSelector` |
| The scaffolder catalog | `github.com`, `api.github.com` and `raw.githubusercontent.com` on 443, while `backstage.catalogs.version` is set | the world rule above |

The app-config addresses muster, the kagent controller and the model manager by
their public hostnames, so those calls leave through the edge rather than
through muster's own pod leg. That edge is the one *those* routes attach to
(`ingress.parentRefs` for muster, `kagent.controllerRoute.parentRef` and
`modelManager.route.parentRef` for the other two, each falling back to the
chart-owned edge, else `global.gatewayApi.parentRefs`) — never
`backstage.parentRefs`, which moves the portal's own route only. The address they resolve to is the edge's
LoadBalancer, which both flavours translate to the proxy pods before the policy
decides: an Envoy Gateway proxy binds the listener's port plus 10000, so a
listener on 443 is a pod on 10443 and both ports are open. In the cilium
flavour that leg is the `cluster` entity the identity-provider include renders
— narrowing that include to the issuer alone would take the portal's calls to
the kagent controller and the model manager with it.

A private identity provider inside one of `worldExcludedCIDRs` needs its
address in `networkPolicy.additionalEgressCIDRs`. The policy names the proxy
pods as Envoy Gateway labels them (`app.kubernetes.io/name: envoy`), so a route
pinned with `backstage.parentRefs` to a Gateway of another implementation needs
that Gateway's pods admitted by a policy of its own.

The backstage chart's own CNPG policy is not this chart's: its missing DNS and
operator legs, and the missing arm64 images, are filed against
giantswarm/backstage.

## The platform Postgres `Cluster`

`postgres.imagePullSecrets` renders `Cluster.spec.imagePullSecrets`. The
bootstrap init container runs the **operator** image, not the operand image, so
a private mirror needs the secret even with `postgres.image.name` left at the
operator's default.

`postgres.affinity` is forwarded verbatim as `Cluster.spec.affinity`. That is
CNPG's own `AffinityConfiguration`, not a core Kubernetes `Affinity`: the
accepted keys are `enablePodAntiAffinity`, `topologyKey`,
`podAntiAffinityType`, `nodeSelector`, `nodeAffinity`, `tolerations`,
`additionalPodAffinity` and `additionalPodAntiAffinity`. Any other key fails
the render, so a typo never reaches the `Cluster` silently. A core
`podAffinity` or `podAntiAffinity` key fails with a message of its own: pass a
core term through `additionalPodAffinity` or `additionalPodAntiAffinity`.

Both schemas take the block as free-form, and the guard is this chart's, so a
typo set on the meta chart passes its own install and fails the
`agent-platform-connectivity` `HelmRelease` instead — that release's message
names the key.

Both render no field while unset, so the operator's own defaults apply.

## Agent Substrate

With `components.substrate` on (the meta chart turns it on with kagent) this
chart renders what Agent Substrate's own chart does not, in the shapes the
platform needs:

- **The bootstrap** (`templates/substrate/bootstrap.yaml`): a
  `pre-install,pre-upgrade` hook Job that mints the CA pools
  `service-dns-ca-pool` and `pod-identity-ca-pool` (`podcertificate-controller-system`),
  the JWT authority pool `actor-id-jwt-pool`, the CA pool `actor-id-ca-pool`
  and the trust anchor `actor-id-ca-certs` derived from it (`ate-system`), and
  the ConfigMap `ate-api-authentication` with the apiserver's issuer read from
  its OpenID discovery document. Key material comes from `openssl` in an init
  container (`hooks.opensslImage`), the objects from `kubectl`
  (`hooks.kubectlImage`); a pool that exists is never touched (a re-run says
  `present`), the two namespaces are created bare when missing. The trust
  anchors are applied server-side as pure functions of their pools on every
  run: `actor-id-ca-certs`, and the podcert signers' cluster-scoped
  `ClusterTrustBundle`s (`servicedns.podcert.ate.dev:identity:primary-bundle`,
  `podidentity.podcert.ate.dev:identity:primary-bundle` — the roots of the two
  podcertificate pools, the objects every Substrate pod projects as its trust
  anchor). The podcertificate-controller publishes and refreshes those bundles
  itself, but a pod reads the bundle that exists when it starts, once; the
  hook runs before the `substrate` release, so a bundle left by a previous
  install never reaches a pod with roots the current pools do not have
  (giantswarm/agent-platform#384; a re-run says `present`, `created` or
  `republished`). Identity: `<release>-hooks`, a ClusterRole on secrets,
  configmaps, namespaces and clustertrustbundles (on persistentvolumeclaims
  while model serving's cache claim is this chart's, [The Hugging Face cache
  claim](#the-hugging-face-cache-claim); `delete` on the pre-pull DaemonSet by
  name while it renders, for its `pre-delete` cleanup Job — [Pre-pulling the
  runtime image](#pre-pulling-the-runtime-image)), with `attest` on the two
  podcert signers (the apiserver's condition for writing a bundle that names a
  signer), created for the events the rendered hook Jobs use and for their
  lifetime (`templates/substrate/hooks-rbac.yaml`; the hook Job include is
  `agent-platform.hooks.job` in `templates/_hooks.tpl`, the event list
  `agent-platform.hooks.events`).
- **The database** (`templates/postgres/databases.yaml`, `databases-hook.yaml`):
  `postgres.databases` is a map of further CNPG `Database`s on the platform
  Cluster, one derived connection Secret `<clusterName>-<key>-app` each (the
  `post-install,post-upgrade` hook waits for CNPG's `<clusterName>-app` and
  rewrites `dbname`, `uri`, `jdbc-uri`, `pgpass`; copied into every
  `secretNamespaces` entry). The shipped `substrate` entry is ate-api-server's
  database while `substrate.postgres.enabled` resolves to the Cluster
  (`agent-platform.substrate.postgresMode`: `auto` — the Cluster with
  `postgres.enabled`, the chart's bundled StatefulSet without it; `true`,
  `false`, or an explicit `connectionString`).
- **Kyverno** (`templates/substrate/policy-exceptions.yaml`): one
  `PolicyException` per Substrate workload — `substrate-atelet`,
  `substrate-workers` (every WorkerPool's pods, label `ate.dev/worker-pool`),
  `substrate-control-plane` (matched by workload name),
  `substrate-podcertificate-controller` — naming
  exactly the restricted-PSS rules the workload violates, each with its
  `autogen-` copy, looked up in `kyvernoPolicies.rules` (rule → ClusterPolicy).
  `make verify-kyverno` computes the violations and holds the lists.
- **Network policies** (`templates/substrate/netpol.yaml`, both flavours):
  Substrate's hops and the actors' destinations on the egress gateway
  `atenet-egress`, where an actor's connections leave (muster, the kagent
  controller, the LLM path, DNS, the OTLP gateway `kagent.otel` names — the
  pods of the endpoint's namespace on its port, the rule the controller's
  egress policy shares through `agent-platform.kagent.otlpEgress`; without
  it every turn ended 3 s late on the Go ADK's pre-response trace flush,
  giantswarm/agent-platform#456); the worker pods reach only the egress
  gateway, the dns and the cluster DNS. The kubernetes flavour renders the
  ingress policies. `make verify-kagent-netpol` asserts the render,
  `make verify-actor-telemetry-egress` the OTLP rules.
- **Guards** (`templates/substrate/validate.yaml`): a Substrate with no
  database, a `postgres.databases` entry whose name is not an identifier or is
  the initdb database's, the Substrate Secret not copied into `ate-system`.

The meta chart's README ("Agent Substrate") has the prerequisites, the version
pin and the snapshot store; `docs/substrate-security.md` the security write-up.

## klaus-gateway — reaching its stores

Three policies select the klaus-gateway pod under `networkPolicy.enabled`
(`templates/klausgateway/netpol.yaml`, both flavours): `-klausgateway-a2a-egress`
(DNS, the agentgateway data plane on 8080; with `klausGateway.a2a.enabled`),
`-klausgateway-obo-egress` (DNS, `world` / `cluster` on 443 and 10443 for
muster's token endpoint; with `klausGateway.obo.enabled`) and
`-klausgateway-store-egress` (DNS + one rule per store), which renders exactly
while the gateway reaches a store beyond its own process:

| Store | Knob | Rule |
|---|---|---|
| The Valkey routing store | `klausGateway.routing.store: valkey`, the valkey component on | the platform's Valkey pods (`agent-platform.valkey.podSelector`, the release namespace) on `valkey.valkey.service.port` |
| The Secret link store | `klausGateway.obo.store: secret`, OBO on | the kube-apiserver (`kube-apiserver` entity; `networkPolicy.kubernetes.apiServerCIDR`) |
| The team-review endpoint's TokenReview (giantswarm/klaus-gateway#273) | `klausGateway.reviews.enabled`, whatever the link store | the kube-apiserver (the same rule, rendered once) |

The keys are the klaus-gateway chart's, forwarded by the meta chart; one left
unset is read with that chart's default (memory, bolt, reviews off), so the
default shape gets no policy. An out-of-band Valkey (`routing.valkey.url` outside the
platform, the component off) gets no rule: nothing in the namespace to select,
the installation adds that egress itself. The valkey release's own policy
admits clients from the whole cluster on 6379, so the client side is the only
missing half.

The first policy that selects the pod puts it in default-deny egress, so a
store without its rule fails at start with `context deadline exceeded` on the
API — not `forbidden`, which would be the Role — or hangs every binding write
and the readiness probe on the Valkey connect. `make verify-klausgateway-netpol`
asserts every gate and both flavours.

## `gateway.jwksEgress` — reaching the issuer's JWKS

The agentgateway controller fetches the JWKS of every `jwtAuthentication`
policy this chart renders and pushes the keys to the data plane over xDS. The
data plane fetches nothing. So the controller's network policy, not the data
plane's, must reach every issuer.

Under `networkPolicy.enabled` the controller's egress is the kube-apiserver,
muster, DNS and the destinations below. Without a rule for the issuer the fetch
is denied on a default-deny cluster and every request that carries a valid
token is answered `401 token uses the unknown key`.

Three route blocks name a JWKS host and port, and the controller policy reads
them directly — there is no second list to keep in sync:

- `kagent.controllerRoute.jwtAuthentication.jwks`
- `modelManager.route.jwtAuthentication.jwks`
- `agentManager.route.jwtAuthentication.jwks`

Only a rendered policy contributes: the component, its route and its
`jwtAuthentication` must all be on.

A `jwks.host` is always a name. An in-cluster issuer is its qualified Service
name; a public issuer is its full host. An address goes in
`gateway.jwksEgress.external.cidrs` below.

| The host | cilium flavour | kubernetes flavour |
|---|---|---|
| In-cluster (`svc` as the third dot-separated label, then nothing, `cluster` or `cluster.local`) | the `gateway.jwksEgress` rule: that namespace on that port | the same, as a namespace selector |
| An external name (`www.googleapis.com`) | a `toFQDNs` `matchName` on the JWKS port, behind the policy's DNS proxy rule | `0.0.0.0/0` minus `networkPolicy.kubernetes.worldExcludedCIDRs`, on the JWKS port |

The kubernetes flavour narrows the controller's egress only while
`networkPolicy.kubernetes.apiServerCIDR` is a real API-server block. It defaults
to `0.0.0.0/0` on every port, and the policy's first rule carries it, so on that
default the controller already reaches every IPv4 destination and the rules
above add nothing.

Every host is classified and selected in its normalized form: lower case, with
the root label's trailing dot removed. `dex.giantswarm.svc.cluster.local.` is
therefore the Service it names, not an external host.

`gateway.jwksEgress.enabled` is required only for an in-cluster host, and its
`namespace` and `port` must be the ones that host names. An external host needs
none of them, and leaving `enabled` on changes nothing else.

It is one rule, so the chart carries one in-cluster issuer. Reach a second one
by address: its pod blocks in `gateway.jwksEgress.external.cidrs`, opened on
`external.port`. With that port equal to the route's `jwks.port`, the namespace
and port guards below stand down for that route — the blocks are the operator's
statement that they are the issuer's.

`gateway.jwksEgress.podSelector` narrows the rule to pods inside the namespace.
No guard reads it, because a hostname carries no pod labels: a selector that
matches no issuer pod renders green and denies the fetch.

Five render guards refuse a host or port that reaches no issuer in any flavour.
Each one is a green render and a runtime `401` without it, and the route
subtrees are open objects in `values.schema.json`, so no schema pattern can hold
them:

| The shape | Why no rule reaches it |
|---|---|
| An empty host | the JWKS backend renders no host and resolves nothing |
| An empty `jwks.port` | the JWKS backend renders no port and the API server refuses it |
| A host that carries a port (`dex.example.com:5556`) | the port belongs in `jwks.port`; both the JWKS backend and the egress rule are built from the two keys |
| An address literal (`198.51.100.7`, `2001:db8::1`, `1.2.3.999`) | the controller selects an external issuer by name; to reach one by address, name its blocks in `gateway.jwksEgress.external.cidrs` |
| A host that is no hostname either (`accounts.google.com/keys`, `a..b.example.com`) | the backend resolves no address; the kubernetes flavour still opens its wide rule, so the render stays green and the fetch never happens |

A host carries the issuer's name alone. Its scheme belongs to `issuer`, and the
JWKS path to `jwks.path`.

Three more depend on the rule that renders, so they follow
`networkPolicy.enabled`:

| The shape | Why no rule reaches it |
|---|---|
| A host of fewer than three labels (`dex`, `dex.giantswarm`, `okta.com`) | it is neither a qualified Service name nor a public issuer. A short Service name resolves through the pod's search path, which the egress rule cannot follow |
| An in-cluster host while `gateway.jwksEgress` is off | nothing opens its port |
| An in-cluster host outside `gateway.jwksEgress.namespace` or off its `port`, and not reached through `external.cidrs` | that key renders one rule, for one namespace on one port |

With no policy rendered, every destination is reachable and neither key decides
anything.

All three routes originate TLS to the issuer when `jwks.port` is 443, which
serves no plain HTTP, or when `jwks.tls.enabled` is set for another port. The
route's `AgentgatewayBackend` then verifies against `jwks.tls.caSecretName`,
else the controller's system trust. `jwks.tls.enabled` adds one fallback the
port alone does not: `global.identity.ca.secretName`, the CA of the platform's
own identity provider. The key names one provider, so it is the right default
only for a route pointed at that provider deliberately; a public issuer on 443
verified against a private CA would fail the fetch and answer every caller
`401 token uses the unknown key`. An in-cluster Dex on 5556 keeps its
plain-HTTP fetch.

`gateway.jwksEgress.external` covers an issuer the routes do not name and an
issuer reached by address. Both
lists open `external.port` (443 by default) as their own rule, and both apply
whether or not `gateway.jwksEgress.enabled` is set:

| The key | The flavour that reads it |
|---|---|
| `external.fqdns` — Cilium FQDN selectors (`matchName`, `matchPattern`) | cilium, behind the policy's DNS proxy rule. The kubernetes flavour selects addresses and never names, so it ignores them |
| `external.cidrs` — IP blocks of either family | both |

An `external.fqdns` item is a selector object (`- matchName: keys.example.com`),
never a bare string. The schema types the item, so a string list fails the
render instead of the apply.

`external.cidrs` also covers the issuer the kubernetes flavour cannot reach by
name at all: the wide rule it renders for a name is `0.0.0.0/0`, so it reaches
public IPv4 destinations only. A private identity provider inside one of
`worldExcludedCIDRs`, and an issuer the cluster resolves over IPv6, belong
there.

The platform's identity provider is always among the controller's external
targets: `global.identity.issuerUrl`'s host on 443 (the URL's port when it
carries one) is opened by name in the cilium flavour and as port 443 of the
wide rule in the kubernetes flavour, in every release that runs the controller
and whatever the routes name. The controller serves the JWT policies of every
release in the cluster, and the serving slice's models Gateway beside the
platform's release takes the issuer's public host on 443 by default — a policy
the platform's release cannot see (giantswarm/agent-platform#505). An
in-cluster issuer URL adds nothing: `gateway.jwksEgress` covers it.

The cilium controller policy renders the DNS proxy clause only while a name is
selected. With an in-cluster issuer and in-cluster route hosts alone it renders
no external rule and no proxy clause, in either flavour; `make verify-wiring`
asserts that against `origin/main`, the controller policies first and then the
whole render, and that a public issuer is opened on 443 with the proxy clause
while every route's host is in-cluster.

## The GPU node pool

A GPU node pool created through the platform (bumblebee-plans#46, the `gpu-node-pool` chart) arrives tainted `nvidia.com/gpu` `NoSchedule` — only accelerator work lands there — and labelled `giantswarm.io/machine-pool=<cluster>-<pool>`. `modelServing.gpuPool` is the serving layer's one input for both (giantswarm/agent-platform#315): `taint` (`key`, `value`, `effect`; the default is the pool chart's taint, tolerated with `operator: Exists` because the pool sets no value — a `value` narrows the toleration to `Equal`, an empty `key` is an untainted pool and renders nothing) and `nodeSelector` (the pool's label; per pool, so the slice release's values set it, empty by default).

A pool node also runs containerd with an **unlimited locked-memory limit** (`LimitMEMLOCK=infinity`, the pool chart's `memlock.conf` drop-in on `containerd.service`; giantswarm/agent-platform#564): a container inherits containerd's `RLIMIT_MEMLOCK` and has no `CAP_IPC_LOCK` to raise it — root included, and granting it is refused under the baseline and restricted Pod Security Standards — so under the unit's 8 MB default a serving runtime that `mlock()`s its weights (a unified-memory stack) dies at engine start with an out-of-memory at a few MiB while the node has a hundred GiB free. A GPU node not created through the platform needs the same drop-in for such a runtime; runtimes that only `cudaMalloc` never notice.

The chart applies the input to everything it renders onto the pool and to nothing else — platform components never carry it:

| Site | Toleration | Node selector |
|---|---|---|
| Every published preset's `scheduling` block (`agent-platform-serving-preset-<name>`) | the pool's first, the preset's own after it (an equal entry once) | the pool's under the preset's own keys |
| The discovery ConfigMap `agent-platform-model-serving`: `spec.gpuPool.taint.{key,value,effect}`, `spec.gpuPool.nodeSelector` | published for model-manager (`>= 0.23.0`, giantswarm/model-manager#86), which schedules the `LLMInferenceService`s it composes, its download Jobs and its inventory scan pods by it; a registered backend document may override it | likewise |

Three render guards: the effect is `NoSchedule`, `PreferNoSchedule` or `NoExecute` (empty tolerates every effect of the key), the key is a qualified name, and every selector value is a string (a label value is a string: quote a number). `make verify-gpu-pool` asserts both sites in the default, pool-selected, valued and untainted shapes, the guards, the meta chart's forwarding of the block, and that an empty taint key leaves the serving render byte-identical to `origin/main` but for the discovery block; `ci/test-model-serving-gpu-pool-values.yaml` is the pool-selected fixture.

### The prewarm placeholder's PriorityClass

A pool created with prewarm (`create_node_pool {prewarm: true}`; the pool chart's `pool.prewarm`) launches its first node with the release: a one-shot placeholder Job holds one GPU at negative priority until the first predictor preempts it. A `PriorityClass` is cluster-scoped, and a pool release is delivered under Flux multi-tenancy as the organisation's tenant ServiceAccount, whose rights are namespaced — a pool chart that rendered the class failed `InstallFailed` on it, and the pool never came up (giantswarm/agent-platform#539). So the pool chart renders none, and **this chart ships the one class every pool of the installation names**: `PriorityClass agent-platform-prewarm-placeholder` — `value: -1000` (below the default priority 0, so any workload preempts the placeholder), `preemptionPolicy: Never` (the placeholder preempts nothing itself), `globalDefault: false`, the `cluster-manager` component label — from `clusterManager.prewarmPriorityClass` (`enabled`, `name`, `value`; `templates/cluster-manager/priorityclass.yaml`). The name is the contract with the pool chart: `pool.prewarm.priorityClassName` defaults to it. The class renders **with the cluster-manager component**, not the model-serving switch: cluster-manager composes the pool releases and is on in exactly one release per installation, the platform's, so the class has one owner — the serving slice, where model serving runs on an installation, never carries it, and a class gated on the switch would arrive after the pool's Job and leave with the slice. `enabled: false` renders none; a placeholder pod naming a class the cluster lacks is rejected at admission, and the pool comes up without a prewarmed node. `value` is immutable on the API: to change it, rename the class (and point the pool chart's value at the new name). Three guards fail the render naming the key: a `value` at or above 0 or fractional, a `name` that is not a DNS-1123 subdomain, the reserved `system-` prefix. The meta chart mirrors the block (`make verify-meta` holds every leaf equal); `make verify-cluster-manager` asserts the class, its knobs, the guards and that nothing renders while the component is off.

### Pre-pulling the runtime image

A predictor pulls its runtime image only when its main container starts — after the storage-initializer has finished the weights — so on a pool scaling from zero the two longest steps of a cold start run one after the other (measured on a fresh node: weights 116 s, then 115 s for the 8.8 GB `llm-d-cuda` image; with prewarm the node is Ready about two minutes before the model is even requested). The image is the same for every served model of an installation, and a node knows it is a GPU node the moment it joins. So `modelServing.prepull` (on by default; giantswarm/agent-platform#545) renders **a DaemonSet in the serving namespace** (`<release>-model-serving-prepull`; `templates/model-serving/prepull.yaml`) with one init container per image of `prepull.images` running `/bin/true` and a pause main container (`prepull.pauseImage`, the cluster's sandbox image mirrored; `prepull.resources` on every container, never a GPU): the kubelet pulls the images the moment the node joins, in parallel with the storage-initializer, and containerd keeps them for the predictor, whose `Pulled` event then reads "already present on machine".

Where it runs: `prepull.nodeSelector` — **an installation's map renders alone**; empty (the default in both charts) renders Karpenter's `karpenter.k8s.aws/instance-gpu-manufacturer: nvidia`, which every GPU instance type carries, fractional-GPU types such as g6f included. The default is the template's (`agent-platform.modelServing.prepull.nodeSelector` in `_helpers.tpl`), not a values default, because Helm merges maps: a default key in `values.yaml` would be kept next to whatever an installation sets — a DaemonSet no node matches — and a `null` for it is coalesced away when the meta chart forwards its values (giantswarm/agent-platform#562). So a GPU node not launched by Karpenter — labelled `nvidia.com/gpu.present: "true"` by the GPU operator's feature discovery, say — is selected by its own labels alone; a label value that is not a string fails the render naming the key. The pool's own label (`gpuPool.nodeSelector`) is merged under either, so a pool release runs the DaemonSet on the pool's nodes alone; the pool's taint is tolerated first and `prepull.tolerations` after it (default `operator: Exists` — every taint, the fleet's DaemonSet convention, so the pull starts under a node's start-up taints and not once they are lifted; an empty list tolerates the pool's taint alone). The default image list is the runtime image of the well-known `LLMInferenceServiceConfig` every served model composes from — named by the `kserve-runtime-configs` chart (giantswarm/kserve), mirrored to gsoci by its `imageRegistry` — pinned here because this chart has no other value for it: a re-pin of that chart's line moves it here, never ahead, and the list is plain references on purpose so no image manager bumps it ahead of the runtime configs (a newer tag pre-pulled is a tag no predictor runs). An empty list with the switch on fails the render naming the key.

**The DaemonSet is a hook object, not a release resource** (`helm.sh/hook: post-install,post-upgrade,post-rollback`, weight 0, `before-hook-creation`; giantswarm/agent-platform#563). A release's wait counts a DaemonSet ready by its pods — helm-controller from 1.6 waits with the kstatus poller, which needs every pod of a DaemonSet Ready; Helm 3's legacy waiter, still on helm-controller 1.4, takes the DaemonSet's `maxUnavailable: 100%` as ready once the pods are scheduled; the fleet runs both — and a pod whose image cannot be pulled is never Ready. As a release resource, one listed image not yet pullable (a runtime image or a `prepull.modelPresets` image still being built, a registry outage) held the pods in `ImagePullBackOff`, timed the wait out and failed the whole connectivity upgrade with every other object applied. A hook object is outside every waiter's set — a hook's `WatchUntilReady` watches Jobs and Pods only, in Helm 3, Helm 4 and helm-controller alike — so the DaemonSet is created after the release's objects are applied and waited for, and nothing waits for its pods: a missing image leaves them retrying on the kubelet's backoff and the release Ready; each event replaces the DaemonSet, and the pods come back on images already present (`helm get hooks` lists it, `helm get manifest` does not). A hook object is not Helm's to delete on uninstall, so a `pre-delete` hook Job (`<release>-model-serving-prepull-cleanup`; the hook include `agent-platform.hooks.job`, the identity `<release>-hooks` created for that event too with `delete` on exactly that DaemonSet — [Agent Substrate](#agent-substrate)) removes it, unlike the cache claim and the serving namespace, which are kept on purpose. The deny-all policy below stays a release resource; nothing waits for a policy.

The pods hold no GPU (no `nvidia.com/gpu` resource, no `runtimeClassName`), mount no ServiceAccount token and need no network — the pull is the kubelet's, not the pod's — so a deny-all policy of their own selects them in the flavour `networkPolicy` resolves (kubernetes: both policy types with no rule; cilium: one empty rule per direction), and they pass the fleet's restricted Pod Security Standard with **no** PolicyException (uid 65534, no privilege escalation, every capability dropped, seccomp `RuntimeDefault`, read-only root). Their selector label `agent-platform.giantswarm.io/model-serving-prepull=true` is carried by no model pod shape, so the serving policies, the cache mutations and the predictors' exception never touch them. `prepull.enabled: false` renders neither object, no cleanup Job and no `daemonsets` rule. The meta chart mirrors the block (`make verify-meta` holds every leaf equal, an empty mapping being a leaf — the forwarded copy shadows this chart's default, and a copy behind it would pre-pull the wrong image, a copy that kept the selector's map would merge into every installation's); `make verify-model-serving-policies` renders the DaemonSet, holds its pod to the fleet's restricted-PSS policies with no exception, asserts the hook shape, the cleanup Job and the identity's rule, the three selector cases and the guard, that the deny-all selects it alone, and that the values the meta chart forwards render the same pod — an installation's selector set on the meta chart reaching the DaemonSet alone.

## Verifying the model images

Every image the charts ship by default is a `gsoci.azurecr.io/…` reference (giantswarm/agent-platform#575). `make verify-images` holds it over the rendered defaults of both charts — with the engine and every component on: every container image of every pod template, every `OCIRepository` url, every registry and reference the meta chart forwards. A reference whose gsoci copy is still being published is tolerated by name with the issue that publishes it (`tests/verify-images.py`, `PENDING`), and an exception that matches nothing fails the check.

On top of that, `modelServing.imageVerification` (giantswarm/agent-platform#552, #575; **on by default**) renders a Kyverno `verifyImages` ClusterPolicy over the model pods of the serving namespace — both pod shapes, Pods at CREATE and UPDATE, every container image of the pod that matches — whose defaults admit exactly what Giant Swarm signs:

- **`images: [gsoci.azurecr.io/giantswarm/*]`** — every image under the platform's registry namespace: the model images, the runtime, the llm-d sidecars. A container image matching no pattern is left alone; an installation that serves from its own registry (`modelServing.modelImages.registry`) adds its pattern.
- **One keyless attestor**, the identity every image built or re-signed by a Giant Swarm CircleCI project carries. The architect orb signs with cosign keyless through Fulcio's CircleCI federation, so the signing certificate's OIDC issuer is `https://oidc.circleci.com` and its subject the pipeline definition that ran — `https://circleci.com/api/v2/projects/<project id>/pipeline-definitions/<definition id>`, two UUIDs: `issuer: https://oidc.circleci.com`, `subjectRegExp: ^https://circleci\.com/api/v2/projects/[a-f0-9-]+/pipeline-definitions/[a-f0-9-]+$`, the pair the orb itself verifies with after signing (`cosign verify --certificate-oidc-issuer-regexp '^https://oidc\.circleci\.com' --certificate-identity-regexp '<the pattern>' <image>`). The subject names no organisation; what scopes the rule to Giant Swarm is `images`, since only Giant Swarm pipelines publish under `gsoci.azurecr.io/giantswarm/`.
- **`type: SigstoreBundle`** — cosign 3 attaches the signature to the image as a Sigstore bundle in an OCI referrer, no `.sig` tag, and Kyverno reads one format per rule: an orb-signed image verified with Kyverno's default `Cosign` type fails with `no signatures found` (measured with `kyverno apply` against a signed gsoci image; `SigstoreBundle` passes). `Cosign` is for an installation whose own signer writes cosign 2's `.sig` tags.
- `mutateDigest: true` pins a verified image to its digest in the pod, `required: true` refuses an image without a signature an attestor accepts, `failureAction: Enforce` denies the pod.

It renders where the cache policies render (Kyverno served, `modelServing.policies.enabled` resolved); enabled with an empty images list, no attestor, a type or a failureAction outside its options or an unknown key fails the render naming the key. Everything a shipped preset's pod runs from the platform's namespace carries the identity — the curated model images giantswarm/models publishes (giantswarm/agent-platform#554), the llm-d runtime and sidecars (the llm-d line signs every mirror and repack since its v0.5.0) and every image retagger copies since giantswarm/retagger#1230 (the KServe storage-initializer and agent among them) —, so the default admits every model pod the platform composes and refuses an image under the pattern that none of these signed. A refused pod never reaches the kubelet: the workload's ReplicaSet reports the admission error, `sigstore bundle verification failed` naming the image, and model-manager surfaces it as the model's phase. An installation whose model pods run such an image — one pushed to the namespace by hand, one re-signed by a signer of its own — adds that signer to `attestors` or sets `enabled: false` before it upgrades; an installation serving from a registry of its own (`modelServing.modelImages.registry`) is verified only once it names its pattern and its signer, since a container image matching no pattern is left alone. `make verify-model-serving-policies` asserts the default render's rules and their knobs, the pass-through of an installation's own block, the guards and, through `kyverno apply`, that Kyverno accepts the policy; the meta chart mirrors the block leaf for leaf (`make verify-meta`).

The verification is Kyverno's admission controller's own work: it fetches the image's Sigstore bundle from the registry (the image's OCI referrers, the bundle blob) and verifies it against the Sigstore trust root — so it needs DNS and TCP 443 to the registry and to the Sigstore hosts. The fleet's Kyverno chart gives the admission controller a `CiliumNetworkPolicy` allowing egress to the API server only, and Cilium's default-deny takes the rest; every fetch failed before it started (`lookup gsoci.azurecr.io: i/o timeout`) and, `verifyImages` running in the mutating webhook with `failurePolicy: Fail`, the error denied every model pod whatever `failureAction` said (giantswarm/agent-platform#599). `modelServing.imageVerification.kyvernoEgress` (on by default) renders, in the cilium network-policy flavor, one `CiliumNetworkPolicy` `<release>-model-serving-image-verification-egress` in Kyverno's namespace (`namespace: kyverno`) selecting the admission controller pods (`podSelector`; empty selects the upstream chart's admission-controller labels, a set one renders alone) with DNS through Cilium's DNS proxy and TCP 443 to `hosts` — the platform's registry, the Azure Storage accounts it redirects a blob read to (`*.blob.core.windows.net`), the Sigstore TUF repository and Rekor, as `toFQDNs` entries —, additive to whatever Kyverno's own policies allow. `enabled: false` renders none for a Kyverno that reaches the registry already; the kubernetes flavor renders none (a `NetworkPolicy` has no names to allow). The guards refuse an empty namespace or host list, a selector that is no mapping, a host that is no `toFQDNs` entry, a nulled block and an unknown key.

## The Hugging Face cache claim

`modelServing.cache.pvc` (`hf-cache`, `100Gi`, on the chart's own StorageClass — below — unless `storageClassName` names a class of the operator's, `"-"` the empty class for a pre-provisioned volume; `volumeName` binds one) is one claim in the serving namespace with one subdirectory per served model; the Kyverno policies mount it into every model pod's storage-initializer and runtime, model-manager's pre-warm downloads land in the same layout. The claim has **no consumer of its own** — the first predictor (or download Job) that mounts it is what Binds it — and under a StorageClass with `volumeBindingMode: WaitForFirstConsumer` (kind's `standard`, the fleet's default `gp3`, the chart's own) it stays `Pending` until then. Helm's wait counts a Pending claim as not ready, so as a release resource it failed every install and upgrade with the switch on (giantswarm/agent-platform#483).

The claim is therefore **applied by a `post-install,post-upgrade` hook Job** (`templates/model-serving/cache-pvc.yaml`; the hook include `agent-platform.hooks.job`, the identity `<release>-hooks` with `get`, `create`, `patch` on `persistentvolumeclaims` while the claim is the chart's, never `delete`), not rendered as a release resource: `kubectl apply --server-side --force-conflicts` under the field manager `agent-platform-connectivity`, the log says `created` or `present` and the claim's phase, nothing waits for a Bind. It binds where its first consumer schedules — the GPU pool's zone, which is what a zonal volume needs. What follows from the claim not being Helm's:

- an uninstall or a `cache.enabled` flip leaves it — the intent of the `helm.sh/resource-policy: keep` it carries (hundreds of gigabytes of downloads outlive the release), now by construction — and the serving namespace with it (below);
- a `size` change is applied by the next upgrade's hook (the StorageClass has to allow expansion; the chart's own does); a change to an immutable field (`storageClassName`, `accessModes`, `volumeName`) fails the hook — and the release — naming the field;
- an installation upgrading from a chart that rendered the claim as a release resource keeps it (the keep policy) and the hook takes its fields over;
- `existingClaim` names a claim of the operator's instead: nothing is applied, the name is published to model-manager and the portal.

### What an uninstall leaves behind

A namespace Helm deletes takes every object in it along, the claim's `keep` notwithstanding — so a Helm-owned serving namespace took the claim and its volume down with every pool teardown, and the next pool downloaded the weights again (giantswarm/agent-platform#537); kept only while `cache.enabled` was on, it took a claim an earlier release had left there down with the first release composed without the cache (giantswarm/agent-platform#565) — the release composing the slice cannot know what an earlier one left in the namespace. The serving namespace therefore carries `helm.sh/resource-policy: keep` whenever the chart creates it (`modelServing.namespace.keep`, default `true`; `templates/model-serving/namespace.yaml`), independent of the cache switch: **the namespace outlives the release, the claim outlives the pool, and removing either is a person's explicit act.** `helm uninstall` leaves the namespace and what in it is not the release's — the claim and its volume — behind, nothing else; the chat-template ConfigMaps, the presets, the policies and the StorageClass go with the release, and the pre-pull DaemonSet — a hook object, not the release's either — is removed by its `pre-delete` hook Job ([Pre-pulling the runtime image](#pre-pulling-the-runtime-image)). A re-install adopts the namespace (its Helm annotations are unchanged) and the hook finds the claim `present`. A GPU pool that comes and goes downloads a model once: the claim's volume is zonal, and a bound claim places the next pool's node in its zone (Karpenter reads the claim's topology). To remove the cache for good: delete the claim (`kubectl delete pvc -n <serving namespace> hf-cache`; the volume goes with it, the class reclaims with `Delete`), then the namespace. `namespace.keep: false` is the one way to have the namespace go with the release — everything in it, a cache claim included, goes too; it is for an installation that wants nothing of the serving layer to outlive the release. `namespace.create: false` leaves a namespace of the operator's alone either way.

### The claim's StorageClass

The cluster's default class is the slowest tier of its kind: the fleet's `gp3` at its baseline (125 MiB/s, 3000 IOPS) reads a served model's weights at exactly that rate — 8.8 GiB in 71 s — and the storage-initializer's download writes into the same cap (giantswarm/agent-platform#537). `modelServing.cache.storageClass` (default `create: true`) renders a class of the claim's own (`templates/model-serving/storageclass.yaml`): cluster-scoped, named `<chart>-<claim>` (`agent-platform-connectivity-hf-cache`) unless `name` says otherwise, `provisioner: ebs.csi.aws.com` with the EBS CSI driver's `parameters` (`type: gp3`, `iops: "4000"`, `throughput: "1000"` — a StorageClass takes strings), `volumeBindingMode: WaitForFirstConsumer` (the volume is provisioned in the zone of the pod that first mounts the claim, the GPU pool's), `allowVolumeExpansion: true` (a `size` change reaches the volume), `reclaimPolicy: Delete` — the claim is the durable object, the volume goes when the claim does. The applied claim references it. The class is Helm-owned, unlike the claim: an uninstall removes it and leaves the claim; a bound volume works on without its class, a claim still Pending binds once the next install renders the class again. The default is AWS's; another cloud sets `provisioner` and `parameters` to its own driver's (a per-provider default is a follow-up), `create: false` with `name` references an existing class and renders none, `create: false` without a name leaves the claim on the cluster's default class. `pvc.storageClassName` still names a class of the operator's (`"-"` the empty class for a pre-provisioned volume) and requires `storageClass.create: false`; the render refuses both. An installation whose claim already exists on another class keeps it — `storageClassName` is immutable, the hook would fail naming it: `storageClass.create: false`, `name: <the claim's class>`.

### vLLM's compile cache and Triton's kernel cache on the claim

vLLM's cache — the torch.compile artifacts of a model, tens of seconds of work per pod — and Triton's — the compiled kernels and, with vLLM's default `TRITON_CACHE_AUTOTUNING=1`, the autotuning results of every `@triton.jit` kernel the model runs, the GDN and Mamba kernels of the hybrid architectures among them — are directories of the claim's own: the redirect rule mounts the claim a second time on the runtime container, at `/mnt/vllm-cache` from the claim-wide subPath `.vllm-cache`, and sets `VLLM_CACHE_ROOT=/mnt/vllm-cache` and `TRITON_CACHE_DIR=/mnt/vllm-cache/triton` on that container in the same rule (not through `modelServing.policies.env`: a pod without the cache keeps vLLM's and Triton's default cache roots instead of an env naming a path nothing mounts). The directory is shared by every model — the artifacts are keyed by model and configuration inside it — so a model's next pod skips the compile (giantswarm/agent-platform#537); in an emptyDir, or in the container's `~/.triton/cache`, they died with the pod. A preset that compiles has Triton's cache on the claim either way — at compile init vLLM redirects `TRITON_CACHE_DIR` in-process to `torch_compile_cache/<hash>/rank_<n>/triton_cache`, so the rule's env is the floor for what Triton compiles before that point; a preset that serves `--enforce-eager` never compiles, that redirect never runs, and without the rule's env every pod recompiled and re-autotuned its kernels during the profiling run (giantswarm/agent-platform#572). It is not a model directory and the storage-initializer never touches it: the initializer's Hugging Face client creates `<model>/.cache/huggingface` as uid 1000, mode 755, during the download, so a cache root under `/mnt/models` (4.31.0) was not writable for the runtime — another uid, gid 1000 through the claim's `fsGroup` — and every cold start crash-looped on `PermissionError: /mnt/models/.cache/vllm` (giantswarm/agent-platform#541). The kubelet creates a subPath directory that does not exist yet with the claim root's group and mode (`root:1000 2775`), which gid 1000 writes; `torch_compile_cache/` and `triton/` below it are created by vLLM and Triton themselves as the runtime's uid — the group write bit of `.vllm-cache` lets the runtime create them, its setgid bit gives them group 1000, and the creating uid owns them, so the model's next pod (the same image, the same uid) writes them again. The runtime's `/mnt/models` mount keeps the attributes KServe declares (the classic predictor's is read-only): the runtime writes nothing into the model's directory.

`make verify-serving-slice` asserts the hook, its claim, the identity, the kept namespace (with the cache on and off; not with `namespace.keep: false`), the StorageClass and the class knobs, and that `cache.enabled: false` or an `existingClaim` render none of the cache; `make verify-model-serving-policies` the cache mount with `VLLM_CACHE_ROOT` and `TRITON_CACHE_DIR` naming it on the runtime container alone (both under the mount, neither on a pod without the cache), the model mount's `readOnly` as KServe declared it and that no mount or env value of the pod names a path under `/mnt/models`, over both pod shapes; `make verify-wiring` the serving shape without a `PersistentVolumeClaim` object; `make verify-meta` that the meta chart mirrors the namespace, cache and policy defaults it forwards.

## The storage-initializer's memory limit

`modelServing.policies.storageInitializerMemoryLimit` (`8Gi`) is the memory limit the `storage-initializer-memory` rule sets on the KServe storage-initializer of every model pod (KServe's own default is `1Gi`, OOM-killed by an 8 GB download; empty leaves it). The limit has to hold more than the download client's in-flight chunks: the file pages the initializer writes are page cache the kernel charges to the writing container's cgroup until write-back has drained them to the disk, so a download that arrives faster than the disk drains accumulates dirty pages inside the limit. On the cache claim and on a large node they stay small. The former `4Gi` default was OOM-killed (`exit 137`) on a 4 vCPU / 16 GiB L4 node a minute into a 9.4 GB download into the emptyDir KServe mounts — the node's local disk — because the download ran **inside the runtime pre-pull's unpack**: the GPU became allocatable about 77 s before the pre-pull had finished gunzipping the 8.8 GB runtime image, so the download and a CPU-bound gunzip shared four vCPUs (load average 30, the page cache filling the node, write-back saturated) and the download's dirty pages stayed charged to the initializer (giantswarm/agent-platform#576). Earlier runs of the same download passed because it started after the pre-pull had ended, and a 16 vCPU node with the same overlap was fine. What removes the collision on the L4 size is the zstd re-layered runtime image from the `llm-d-fast/` prefix (giantswarm/agent-platform#568): its unpack takes about 30 s and the pre-pull ends before the GPU appears. The `8Gi` limit is the headroom for a download that still overlaps an unpack — a bigger model, a slower disk — not the fix. What the value costs on the node: an init container that declares only a limit requests that much while it runs, and init and main containers do not run at once, so the pod's effective request is the larger of the initializer's `8Gi` and the predictor's own — `10Gi` for the smallest preset — and `8Gi` adds nothing to what the node has to hold; it fits the smallest curated L4 size, a 16 GiB node that leaves about 12 GiB to pods after the kubelet's reservations. `make verify-model-serving-policies` asserts the limit on both pod shapes; `make verify-meta` that the meta chart mirrors the default.

## Voluntary disruption

Two objects of this chart guard the platform's single-replica pods against Karpenter consolidation and node drains (giantswarm/agent-platform#431), a third the muster-valkey pod (#439) and a fourth the Substrate worker pool (#472); the other components' guards travel on their own charts' knobs through the meta chart. The four budgets share one spec helper (`agent-platform.podDisruptionBudget.spec`) and its guards.

- `gateway.parameters.podAnnotations` (default `karpenter.sh/do-not-disrupt: "true"`) is merged onto the agentgateway data-plane pod template through `AgentgatewayParameters` `deployment.spec.template.metadata` (strategic merge). Every MCP call, every A2A stream and — with `llmRouting` on — every model stream crosses those pods. Set the value to `"false"` or the map to `{}` to opt out.
- `agentManager.podDisruptionBudget` (default `enabled: true`, `minAvailable: 1`, `unhealthyPodEvictionPolicy: AlwaysAllow`) renders a `PodDisruptionBudget agent-manager` in the release namespace, selecting the agent-manager pods by name the way the component's network policies do — the agent-manager chart has no knob of its own. Exactly one of `minAvailable` / `maxUnavailable` (int or percentage); the render refuses both, neither, and a policy outside the API's enum. Inert while `components.agent-manager.enabled` is false.
- `valkey.podDisruptionBudget` (same defaults and guards; giantswarm/agent-platform#439) renders a `PodDisruptionBudget muster-valkey` — named after `valkey.valkey.fullnameOverride`, which the render requires — selecting the pod the way the valkey subchart labels it (`app.kubernetes.io/name: valkey` and the component's release name, `valkey`). muster's OAuth token store is that one pod on an RWO volume; neither the wrapper nor the upstream subchart has a budget knob. Inert while `components.valkey.enabled` is false; the meta chart never forwards the key to the valkey release.
- `kagent.substrateWorkerPool.podDisruptionBudget` (default `enabled: true`, `maxUnavailable: 1`, `unhealthyPodEvictionPolicy: AlwaysAllow`; giantswarm/agent-platform#472) renders a `PodDisruptionBudget` named after the pool (`kagent.substrateWorkerPool.name`, which the render requires) in the **kagent namespace**, selecting the worker pods by the label Substrate's ate-controller puts on them, `ate.dev/worker-pool: <name>` — the same label the `substrate-workers` network policy selects. The kagent chart renders the `WorkerPool` and has no budget template. Four workers host one actor each, so `maxUnavailable: 1` lets a voluntary drain move one worker at a time instead of the pool. Inert while `components.kagent.enabled` is false; the meta chart never forwards the key to the kagent release.

With one replica, `minAvailable: 1` refuses every voluntary eviction — Karpenter reports `DisruptionBlocked`, a node drain waits for its drain timeout (the fleet's Karpenter NodePools force-terminate after `terminationGracePeriod: 30m`) — and `AlwaysAllow` keeps a pod that is not Ready evictable. `make verify-disruption` asserts the render, the knobs off and the guards; `make verify-workerpool` the worker budget. A spot reclaim is not a voluntary eviction: the placement of the stateful singletons on on-demand capacity is the meta chart's `scheduling.singletons`, which reaches the component releases as their charts' `nodeSelector` / `tolerations` and is never forwarded here.

## The kagent controller's VerticalPodAutoscaler

The kagent chart has no VPA knob, so this chart renders one for the controller the way it renders the muster-valkey budget (giantswarm/agent-platform#455): `VerticalPodAutoscaler kagent-controller` in the kagent namespace, on the Deployment and the container the kagent chart names (`templates/kagent/controller-vpa.yaml`; `make verify-kagent-netpol` ties both names to the kagent chart the range resolves to). The knob is `kagent.controller.vpa`:

- `enabled: auto` — the object renders where the cluster serves `autoscaling.k8s.io/v1`; the VPA CRD is not part of Kubernetes conformance. An explicit `true` / `false` wins. Under the meta chart the knob is resolved once with the other cluster-shape knobs and arrives here as a boolean.
- `updateMode: InPlaceOrRecreate` (VPA 1.5+ on Kubernetes 1.33+ with in-place pod resize). The controller is one replica behind a `PodDisruptionBudget minAvailable: 1`, so an evicting mode (`Auto`, `Recreate`) could never apply. In place, the running pod's requests change with no eviction and no roll; a resize the cluster cannot apply in place falls back to an eviction the budget refuses, and the pod keeps its requests until its next roll. `minReplicas: 1` keeps the single replica eligible whatever the updater's `--min-replicas` says.
- `controlledValues: RequestsOnly` — the chart's limits stay. `minAllowed` is the chart's requests (`100m` / `128Mi`); `maxAllowed` is a step under its limits (`1900m` / `480Mi`) and must stay there: requests equal to the limits on both resources would turn the Burstable pod Guaranteed, and Kubernetes refuses a resize that changes the QoS class.

The kagent block is open in the schema, so the template refuses a key under `kagent.controller.vpa` that is not one of the five above (VPA on or off), an `updateMode` or `controlledValues` outside the API's enum, and names a key that arrives unset — a `null` set through the meta chart deletes the key at that layer rather than passing it on. The knob never reaches the kagent release (`components.kagent.omitKeys`). `kagent.controller.vpa.enabled: false` opts out; a cluster whose VPA predates 1.5 takes `updateMode: Initial` or `Off`.

## Values

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| global.registry | string | `"gsoci.azurecr.io"` |  |
| global.imagePullSecrets | list | `[]` |  |
| global.domain | string | `""` |  |
| global.identity.issuerUrl | string | `""` |  |
| global.identity.clientId | string | `""` |  |
| global.identity.existingSecret | string | `""` |  |
| global.identity.ca.secretName | string | `""` |  |
| global.identity.ca.key | string | `"ca.crt"` |  |
| global.gatewayApi.parentRefs | list | `[]` |  |
| global.observability.metrics.serviceMonitor.enabled | string | `"auto"` | `auto` (default) renders the monitor objects when monitoring.coreos.com/v1 is served on the cluster (an offline `helm template` resolves to false unless the API is passed in); `true` / `false` force them on or off. |
| global.observability.metrics.serviceMonitor.interval | string | `""` |  |
| global.observability.metrics.serviceMonitor.labels | object | `{}` |  |
| global.observability.traces.otlp.endpoint | string | `""` |  |
| global.observability.traces.otlp.protocol | string | `""` |  |
| global.observability.traces.otlp.headers | object | `{}` |  |
| components.muster.enabled | bool | `true` |  |
| components.dicebear.enabled | bool | `true` |  |
| components.agentgateway.enabled | bool | `false` |  |
| components.agent-platform-mcps.enabled | bool | `false` |  |
| components.kagent.enabled | bool | `false` |  |
| components.klaus-gateway.enabled | bool | `false` |  |
| components.agent-sandbox.enabled | bool | `false` |  |
| components.model-manager.enabled | bool | `true` |  |
| components.agent-manager.enabled | bool | `false` |  |
| components.vm-manager.enabled | bool | `false` |  |
| components.cluster-manager.enabled | bool | `false` |  |
| components.backstage.enabled | bool | `false` |  |
| components.mcp-kubernetes.enabled | bool | `false` |  |
| components.cloudnative-pg.enabled | bool | `false` |  |
| components.kserve-llmisvc-crd.enabled | bool | `false` |  |
| components.kserve-llmisvc-resources.enabled | bool | `false` |  |
| components.modelServing.enabled | bool | `false` |  |
| ingress.mode | string | `"muster-direct"` |  |
| ingress.parentRefs | list | `[]` |  |
| ingress.hostnames | list | `[]` |  |
| ingress.httpRoute.annotations | object | `{}` |  |
| ingress.httpRoute.labels | object | `{}` |  |
| ingress.httpRoute.muster.annotations | object | `{}` |  |
| ingress.httpRoute.muster.labels | object | `{}` |  |
| ingress.httpRoute.mcp.annotations | object | `{}` |  |
| ingress.httpRoute.mcp.labels | object | `{}` |  |
| ingress.httpRoute.timeouts | object | `{}` |  |
| ingress.backendTrafficPolicy.enabled | bool | `false` |  |
| ingress.backendTrafficPolicy.timeout | string | `"0s"` |  |
| ingress.backendTrafficPolicy.annotations | object | `{}` |  |
| ingress.backendTrafficPolicy.labels | object | `{}` |  |
| gateway.name | string | `"agentgateway"` |  |
| gateway.gatewayClassName | string | `"agentgateway"` |  |
| gateway.listeners[0].name | string | `"http"` |  |
| gateway.listeners[0].port | int | `8080` |  |
| gateway.listeners[0].protocol | string | `"HTTP"` |  |
| gateway.listeners[0].allowedRoutes.namespaces.from | string | `"Same"` |  |
| gateway.jwksEgress.enabled | bool | `false` |  |
| gateway.jwksEgress.namespace | string | `"giantswarm"` |  |
| gateway.jwksEgress.port | int | `5556` |  |
| gateway.jwksEgress.podSelector | object | `{}` |  |
| gateway.jwksEgress.external.fqdns | list | `[]` |  |
| gateway.jwksEgress.external.cidrs | list | `[]` |  |
| gateway.jwksEgress.external.port | int | `443` |  |
| gateway.parameters.enabled | bool | `true` |  |
| gateway.parameters.name | string | `""` |  |
| gateway.parameters.serviceType | string | `"ClusterIP"` |  |
| gateway.parameters.podSecurityContext.runAsNonRoot | bool | `true` |  |
| gateway.parameters.podSecurityContext.seccompProfile.type | string | `"RuntimeDefault"` |  |
| gateway.parameters.containerSecurityContext.allowPrivilegeEscalation | bool | `false` |  |
| gateway.parameters.containerSecurityContext.readOnlyRootFilesystem | bool | `true` |  |
| gateway.parameters.containerSecurityContext.runAsNonRoot | bool | `true` |  |
| gateway.parameters.containerSecurityContext.capabilities.drop[0] | string | `"ALL"` |  |
| gateway.parameters.containerSecurityContext.seccompProfile.type | string | `"RuntimeDefault"` |  |
| gateway.parameters.dataPlaneEnv[0].name | string | `"OTEL_EXPORTER_OTLP_ENDPOINT"` |  |
| gateway.parameters.dataPlaneEnv[0].value | string | `"http://otlp-gateway.kube-system.svc:4317"` |  |
| gateway.parameters.dataPlaneEnv[1].name | string | `"OTEL_EXPORTER_OTLP_PROTOCOL"` |  |
| gateway.parameters.dataPlaneEnv[1].value | string | `"grpc"` |  |
| gateway.parameters.dataPlaneVolumes | list | `[]` |  |
| gateway.parameters.dataPlaneVolumeMounts | list | `[]` |  |
| gateway.parameters.dataPlaneResources.requests.cpu | string | `"100m"` |  |
| gateway.parameters.dataPlaneResources.requests.memory | string | `"128Mi"` |  |
| gateway.parameters.dataPlaneResources.requests.ephemeral-storage | string | `"50Mi"` |  |
| gateway.parameters.dataPlaneResources.limits.cpu | string | `"2000m"` |  |
| gateway.parameters.dataPlaneResources.limits.memory | string | `"512Mi"` |  |
| gateway.parameters.dataPlaneResources.limits.ephemeral-storage | string | `"512Mi"` |  |
| gateway.parameters.replicas | int | `2` |  |
| gateway.parameters.podDisruptionBudget.enabled | bool | `true` |  |
| gateway.parameters.spread.enabled | bool | `true` |  |
| gateway.parameters.spread.topologyKeys[0] | string | `"kubernetes.io/hostname"` |  |
| gateway.parameters.spread.maxSkew | int | `1` |  |
| gateway.parameters.spread.whenUnsatisfiable | string | `"ScheduleAnyway"` |  |
| gateway.parameters.podAnnotations | object | `{}` |  |
| gateway.parameters.podLabels | object | `{}` |  |
| gateway.metricLabels.agent.enabled | bool | `true` |  |
| gateway.metricLabels.agent.expression | string | `"{{ include \"agent-platform.substrate.egressCall\" . }} ? request.headers[\"x-kagent-agent\"] : source.unverifiedWorkload.serviceAccount"` |  |
| gateway.metricLabels.agent_namespace.enabled | bool | `true` |  |
| gateway.metricLabels.agent_namespace.expression | string | `"{{ include \"agent-platform.substrate.egressCall\" . }} ? request.headers[\"x-kagent-agent-namespace\"] : source.unverifiedWorkload.namespace"` |  |
| gateway.metricLabels.user.enabled | bool | `true` |  |
| gateway.metricLabels.user.expression | string | `"{{ include \"agent-platform.substrate.egressCall\" . }} ? request.headers[\"x-kagent-user\"] : jwt.{{ include \"agent-platform.kagent.userIdClaim\" . }}"` |  |
| gatewayApi.gateway.create | bool | `false` |  |
| gatewayApi.gateway.tls.secretName | string | `""` |  |
| gatewayApi.gateway.serviceType | string | `"LoadBalancer"` |  |
| llmRouting.enabled | bool | `false` |  |
| llmRouting.listener.name | string | `"llm"` |  |
| llmRouting.listener.port | int | `8081` |  |
| llmRouting.backend.name | string | `"anthropic"` |  |
| llmRouting.backend.provider | string | `"anthropic"` |  |
| llmRouting.pathPrefixes[0] | string | `"/v1"` |  |
| llmRouting.routes./v1/messages | string | `"Messages"` |  |
| llmRouting.routes./v1/messages/count_tokens | string | `"AnthropicTokenCount"` |  |
| llmRouting.routes.* | string | `"Passthrough"` |  |
| llmRouting.modelConfigPolicy.enabled | bool | `true` |  |
| llmRouting.modelCatalog.enabled | bool | `true` |  |
| llmRouting.modelCatalog.name | string | `""` |  |
| llmRouting.modelCatalog.key | string | `"catalog.json"` |  |
| llmRouting.modelCatalog.providers.anthropic.models.claude-haiku-4-5.rates.input | string | `"1"` |  |
| llmRouting.modelCatalog.providers.anthropic.models.claude-haiku-4-5.rates.output | string | `"5"` |  |
| llmRouting.modelCatalog.providers.anthropic.models.claude-haiku-4-5.rates.cacheRead | string | `"0.1"` |  |
| llmRouting.modelCatalog.providers.anthropic.models.claude-haiku-4-5.rates.cacheWrite | string | `"1.25"` |  |
| llmRouting.modelCatalog.providers.anthropic.models.claude-opus-4-5.rates.input | string | `"5"` |  |
| llmRouting.modelCatalog.providers.anthropic.models.claude-opus-4-5.rates.output | string | `"25"` |  |
| llmRouting.modelCatalog.providers.anthropic.models.claude-opus-4-5.rates.cacheRead | string | `"0.5"` |  |
| llmRouting.modelCatalog.providers.anthropic.models.claude-opus-4-5.rates.cacheWrite | string | `"6.25"` |  |
| llmRouting.modelCatalog.providers.anthropic.models.claude-opus-5.rates.input | string | `"5"` |  |
| llmRouting.modelCatalog.providers.anthropic.models.claude-opus-5.rates.output | string | `"25"` |  |
| llmRouting.modelCatalog.providers.anthropic.models.claude-opus-5.rates.cacheRead | string | `"0.5"` |  |
| llmRouting.modelCatalog.providers.anthropic.models.claude-opus-5.rates.cacheWrite | string | `"6.25"` |  |
| llmRouting.modelCatalog.providers.anthropic.models.claude-sonnet-4-5.rates.input | string | `"3"` |  |
| llmRouting.modelCatalog.providers.anthropic.models.claude-sonnet-4-5.rates.output | string | `"15"` |  |
| llmRouting.modelCatalog.providers.anthropic.models.claude-sonnet-4-5.rates.cacheRead | string | `"0.3"` |  |
| llmRouting.modelCatalog.providers.anthropic.models.claude-sonnet-4-5.rates.cacheWrite | string | `"3.75"` |  |
| llmRouting.modelCatalog.providers.anthropic.models.claude-sonnet-4-6.rates.input | string | `"3"` |  |
| llmRouting.modelCatalog.providers.anthropic.models.claude-sonnet-4-6.rates.output | string | `"15"` |  |
| llmRouting.modelCatalog.providers.anthropic.models.claude-sonnet-4-6.rates.cacheRead | string | `"0.3"` |  |
| llmRouting.modelCatalog.providers.anthropic.models.claude-sonnet-4-6.rates.cacheWrite | string | `"3.75"` |  |
| llmRouting.modelCatalog.providers.anthropic.models.claude-sonnet-5.rates.input | string | `"2"` |  |
| llmRouting.modelCatalog.providers.anthropic.models.claude-sonnet-5.rates.output | string | `"10"` |  |
| llmRouting.modelCatalog.providers.anthropic.models.claude-sonnet-5.rates.cacheRead | string | `"0.2"` |  |
| llmRouting.modelCatalog.providers.anthropic.models.claude-sonnet-5.rates.cacheWrite | string | `"2.5"` |  |
| llmRouting.modelCatalog.providers.anthropic.models.claude-fable-5-1.rates.input | string | `"10"` |  |
| llmRouting.modelCatalog.providers.anthropic.models.claude-fable-5-1.rates.output | string | `"50"` |  |
| llmRouting.modelCatalog.providers.anthropic.models.claude-fable-5-1.rates.cacheRead | string | `"0.25"` |  |
| llmRouting.modelCatalog.providers.anthropic.models.claude-fable-5-1.rates.cacheWrite | string | `"12.5"` |  |
| networkPolicy.enabled | bool | `true` |  |
| networkPolicy.flavor | string | `"auto"` | `auto` (default) selects `cilium` when cilium.io/v2 is served on the cluster and `kubernetes` otherwise; `cilium` / `kubernetes` force the flavor. |
| networkPolicy.additionalEgressCIDRs | list | `[]` |  |
| networkPolicy.additionalEgressFQDNs | list | `[]` |  |
| networkPolicy.musterInClusterMcpPorts[0] | int | `8080` |  |
| networkPolicy.musterInClusterMcpPorts[1] | int | `8443` |  |
| networkPolicy.kubernetes.apiServerCIDR | string | `"0.0.0.0/0"` |  |
| networkPolicy.kubernetes.worldExcludedCIDRs[0] | string | `"10.0.0.0/8"` |  |
| networkPolicy.kubernetes.worldExcludedCIDRs[1] | string | `"172.16.0.0/12"` |  |
| networkPolicy.kubernetes.worldExcludedCIDRs[2] | string | `"192.168.0.0/16"` |  |
| networkPolicy.kubernetes.worldExcludedCIDRs[3] | string | `"169.254.0.0/16"` |  |
| kyvernoPolicies.enabled | string | `"auto"` | `auto` (default) renders the Kyverno objects when kyverno.io/v1 is served on the cluster (an offline `helm template` resolves to false unless the API is passed in); `true` / `false` force them on or off. |
| kyvernoPolicies.policyExceptionNamespace | string | `"policy-exceptions"` |  |
| kyvernoPolicies.rules.privileged-containers | string | `"disallow-privileged-containers"` |  |
| kyvernoPolicies.rules.host-ports-none | string | `"disallow-host-ports"` |  |
| kyvernoPolicies.rules.host-path | string | `"disallow-host-path"` |  |
| kyvernoPolicies.rules.restricted-volumes | string | `"restrict-volume-types"` |  |
| kyvernoPolicies.rules.adding-capabilities | string | `"disallow-capabilities"` |  |
| kyvernoPolicies.rules.require-drop-all | string | `"disallow-capabilities-strict"` |  |
| kyvernoPolicies.rules.adding-capabilities-strict | string | `"disallow-capabilities-strict"` |  |
| kyvernoPolicies.rules.run-as-non-root | string | `"require-run-as-nonroot"` |  |
| kyvernoPolicies.rules.run-as-non-root-user | string | `"require-run-as-non-root-user"` |  |
| kyvernoPolicies.rules.privilege-escalation | string | `"disallow-privilege-escalation"` |  |
| kyvernoPolicies.rules.check-seccomp | string | `"restrict-seccomp"` |  |
| kyvernoPolicies.rules.check-seccomp-strict | string | `"restrict-seccomp-strict"` |  |
| kyvernoPolicies.rules.app-armor | string | `"restrict-apparmor-profiles"` |  |
| hooks.kubectlImage.registry | string | `"gsoci.azurecr.io"` |  |
| hooks.kubectlImage.repository | string | `"giantswarm/alpine-k8s"` |  |
| hooks.kubectlImage.tag | string | `"1.37.0"` |  |
| hooks.opensslImage.registry | string | `"gsoci.azurecr.io"` |  |
| hooks.opensslImage.repository | string | `"giantswarm/alpine-openssl"` |  |
| hooks.opensslImage.tag | string | `"3.5.8"` |  |
| extraObjects | list | `[]` |  |
| dashboards.enabled | bool | `true` |  |
| dashboards.namespace | string | `""` |  |
| dashboards.organization | string | `"Shared Org"` |  |
| dashboards.folder | string | `"Agent Platform"` |  |
| dicebear | object | `{}` |  |
| muster.enabled | bool | `true` |  |
| muster.image.registry | string | `"gsoci.azurecr.io"` |  |
| muster.fullnameOverride | string | `"muster"` |  |
| muster.crds.install | bool | `false` |  |
| muster.networkPolicy.enabled | bool | `true` |  |
| muster.networkPolicy.flavor | string | `"auto"` |  |
| muster.networkPolicy.cilium.allowClusterIngress | bool | `true` |  |
| muster.podAnnotations."application.giantswarm.io/team" | string | `"bumblebee"` |  |
| muster.gatewayAPI.enabled | bool | `false` |  |
| muster.muster.oauth.server.enabled | bool | `true` |  |
| muster.muster.oauth.server.baseUrl | string | `""` |  |
| muster.muster.oauth.server.dex.issuerUrl | string | `""` |  |
| muster.muster.oauth.server.dex.clientId | string | `""` |  |
| muster.muster.oauth.server.existingSecret | string | `""` |  |
| muster.muster.oauth.server.storage.type | string | `"valkey"` |  |
| muster.muster.oauth.server.storage.valkey.url | string | `"muster-valkey:6379"` |  |
| muster.muster.oauth.server.storage.valkey.secretKeyPassword | string | `"valkey-password"` |  |
| muster.muster.observability.metrics.prometheus.serviceMonitor.enabled | string | `"auto"` |  |
| muster.muster.observability.metrics.prometheus.serviceMonitor.interval | string | `"60s"` |  |
| muster.muster.observability.metrics.prometheus.serviceMonitor.labels."observability.giantswarm.io/tenant" | string | `"giantswarm"` |  |
| valkey.ciliumNetworkPolicy.enabled | string | `"auto"` |  |
| valkey.vpa.enabled | bool | `false` |  |
| valkey.podDisruptionBudget.enabled | bool | `true` |  |
| valkey.podDisruptionBudget.minAvailable | int | `1` |  |
| valkey.podDisruptionBudget.maxUnavailable | string | `nil` |  |
| valkey.podDisruptionBudget.unhealthyPodEvictionPolicy | string | `"AlwaysAllow"` |  |
| valkey.valkey.fullnameOverride | string | `"muster-valkey"` |  |
| valkey.valkey.replicaCount | int | `1` |  |
| valkey.valkey.podAnnotations."karpenter.sh/do-not-disrupt" | string | `"true"` |  |
| valkey.valkey.auth.enabled | bool | `true` |  |
| valkey.valkey.auth.usersExistingSecret | string | `""` |  |
| valkey.valkey.auth.aclUsers.default.permissions | string | `"~* &* +@all"` |  |
| valkey.valkey.auth.aclUsers.default.passwordKey | string | `""` |  |
| valkey.valkey.dataStorage.enabled | bool | `true` |  |
| valkey.valkey.dataStorage.requestedSize | string | `"1Gi"` |  |
| valkey.valkey.resources.requests.cpu | string | `"50m"` |  |
| valkey.valkey.resources.requests.memory | string | `"256Mi"` |  |
| valkey.valkey.resources.limits.cpu | string | `"200m"` |  |
| valkey.valkey.resources.limits.memory | string | `"1Gi"` |  |
| valkey.valkey.podSecurityContext.fsGroup | int | `1000` |  |
| valkey.valkey.podSecurityContext.runAsUser | int | `1000` |  |
| valkey.valkey.podSecurityContext.runAsGroup | int | `1000` |  |
| valkey.valkey.podSecurityContext.seccompProfile.type | string | `"RuntimeDefault"` |  |
| valkey.valkey.securityContext.allowPrivilegeEscalation | bool | `false` |  |
| valkey.valkey.securityContext.capabilities.drop[0] | string | `"ALL"` |  |
| valkey.valkey.securityContext.readOnlyRootFilesystem | bool | `true` |  |
| valkey.valkey.securityContext.runAsNonRoot | bool | `true` |  |
| valkey.valkey.securityContext.runAsUser | int | `1000` |  |
| valkey.valkey.securityContext.seccompProfile.type | string | `"RuntimeDefault"` |  |
| valkey.valkey.metrics.exporter.securityContext.allowPrivilegeEscalation | bool | `false` |  |
| valkey.valkey.metrics.exporter.securityContext.capabilities.drop[0] | string | `"ALL"` |  |
| valkey.valkey.metrics.exporter.securityContext.readOnlyRootFilesystem | bool | `true` |  |
| valkey.valkey.metrics.exporter.securityContext.runAsNonRoot | bool | `true` |  |
| valkey.valkey.metrics.exporter.securityContext.runAsUser | int | `1000` |  |
| valkey.valkey.metrics.exporter.securityContext.seccompProfile.type | string | `"RuntimeDefault"` |  |
| agent-platform-mcps.agentgateway.enabled | bool | `true` |  |
| agent-platform-mcps.agentgateway.viaMuster | bool | `false` |  |
| agent-platform-mcps.agentgateway.musterUrl | string | `"http://muster.agent-platform.svc.cluster.local:8090/mcp"` |  |
| agent-platform-mcps.mcpServers | list | `[]` |  |
| kagent.fullnameOverride | string | `"kagent"` |  |
| kagent.harness.snapshotLocation | string | `""` |  |
| kagent.harness.snapshotStore.prefix | string | `"kagent"` |  |
| kagent.harness.snapshotStore.crossplane.enabled | bool | `false` |  |
| kagent.harness.snapshotStore.crossplane.provider | string | `"aws"` |  |
| kagent.harness.snapshotStore.crossplane.providerConfigRef | string | `""` |  |
| kagent.harness.snapshotStore.crossplane.region | string | `""` |  |
| kagent.harness.snapshotStore.crossplane.observeOnly | bool | `false` |  |
| kagent.harness.snapshotStore.crossplane.tags | object | `{}` |  |
| kagent.harness.snapshotStore.crossplane.aws.bucketName | string | `""` |  |
| kagent.harness.snapshotStore.crossplane.aws.accountId | string | `""` |  |
| kagent.harness.snapshotStore.crossplane.aws.oidcProvider | string | `""` |  |
| kagent.harness.snapshotStore.crossplane.aws.roleName | string | `""` |  |
| kagent.harness.snapshotStore.crossplane.aws.lifecycleDays | int | `30` |  |
| kagent.harness.snapshotStore.crossplane.capz.storageAccountName | string | `""` |  |
| kagent.harness.snapshotStore.crossplane.capz.containerName | string | `""` |  |
| kagent.harness.snapshotStore.crossplane.capz.resourceGroup | string | `""` |  |
| kagent.harness.snapshotStore.crossplane.capz.subscriptionId | string | `""` |  |
| kagent.harness.snapshotStore.crossplane.capz.replicationType | string | `"LRS"` |  |
| kagent.harness.snapshotStore.crossplane.capz.lifecycleDays | int | `30` |  |
| kagent.harness.snapshotStore.crossplane.capz.workloadIdentity.oidcIssuerUrl | string | `""` |  |
| kagent.harness.snapshotStore.crossplane.capz.workloadIdentity.identityName | string | `""` |  |
| kagent.harness.snapshotStore.crossplane.capz.workloadIdentity.providerKubernetes.providerConfigRef | string | `""` |  |
| kagent.harness.snapshotStore.crossplane.capz.workloadIdentity.providerKubernetes.serviceAccount.name | string | `""` |  |
| kagent.harness.snapshotStore.crossplane.capz.workloadIdentity.providerKubernetes.serviceAccount.namespace | string | `"crossplane"` |  |
| kagent.harness.snapshotStore.s3proxy.enabled | bool | `false` |  |
| kagent.harness.snapshotStore.s3proxy.image.repository | string | `"gsoci.azurecr.io/giantswarm/s3proxy"` |  |
| kagent.harness.snapshotStore.s3proxy.image.tag | string | `"4.1.1"` |  |
| kagent.harness.snapshotStore.s3proxy.replicas | int | `2` |  |
| kagent.harness.snapshotStore.s3proxy.javaOpts | string | `"-XX:MaxRAMPercentage=70"` |  |
| kagent.harness.snapshotStore.s3proxy.resources.requests.cpu | string | `"250m"` |  |
| kagent.harness.snapshotStore.s3proxy.resources.requests.memory | string | `"1Gi"` |  |
| kagent.harness.snapshotStore.s3proxy.resources.requests.ephemeral-storage | string | `"256Mi"` |  |
| kagent.harness.snapshotStore.s3proxy.resources.limits.memory | string | `"1Gi"` |  |
| kagent.harness.snapshotStore.s3proxy.resources.limits.ephemeral-storage | string | `"1Gi"` |  |
| kagent.harness.snapshotStore.s3proxy.azure.endpoint | string | `""` |  |
| kagent.harness.snapshotStore.s3proxy.azure.account | string | `""` |  |
| kagent.harness.snapshotStore.s3proxy.azure.container | string | `""` |  |
| kagent.harness.snapshotStore.s3proxy.azure.accountKeySecretRef.name | string | `""` |  |
| kagent.harness.snapshotStore.s3proxy.azure.accountKeySecretRef.key | string | `""` |  |
| kagent.registry | string | `"gsoci.azurecr.io/giantswarm"` |  |
| kagent.controller.image.repository | string | `"kagent-controller"` |  |
| kagent.controller.agentImage.repository | string | `"kagent-app"` |  |
| kagent.controller.skillsInitImage.repository | string | `"kagent-skills-init"` |  |
| kagent.controller.auth.mode | string | `"trusted-proxy"` |  |
| kagent.controller.auth.userIdClaim | string | `"email"` |  |
| kagent.controller.env[0].name | string | `"METRICS_BIND_ADDRESS"` |  |
| kagent.controller.env[0].value | string | `":8080"` |  |
| kagent.controller.env[1].name | string | `"METRICS_SECURE"` |  |
| kagent.controller.env[1].value | string | `"false"` |  |
| kagent.controller.env[2].name | string | `"OTEL_EXPORTER_OTLP_HEADERS"` |  |
| kagent.controller.env[2].value | string | `"X-Scope-OrgID=giantswarm"` |  |
| kagent.controller.vpa.enabled | string | `"auto"` |  |
| kagent.controller.vpa.updateMode | string | `"InPlaceOrRecreate"` |  |
| kagent.controller.vpa.controlledValues | string | `"RequestsOnly"` |  |
| kagent.controller.vpa.minAllowed.cpu | string | `"100m"` |  |
| kagent.controller.vpa.minAllowed.memory | string | `"128Mi"` |  |
| kagent.controller.vpa.maxAllowed.cpu | string | `"1900m"` |  |
| kagent.controller.vpa.maxAllowed.memory | string | `"480Mi"` |  |
| kagent.ui.image.repository | string | `"kagent-ui"` |  |
| kagent.substrateWorkerPool.name | string | `"kagent-default"` |  |
| kagent.substrateWorkerPool.podDisruptionBudget.enabled | bool | `true` |  |
| kagent.substrateWorkerPool.podDisruptionBudget.minAvailable | string | `nil` |  |
| kagent.substrateWorkerPool.podDisruptionBudget.maxUnavailable | int | `1` |  |
| kagent.substrateWorkerPool.podDisruptionBudget.unhealthyPodEvictionPolicy | string | `"AlwaysAllow"` |  |
| kagent.namespaceOverride | string | `"kagent"` |  |
| kagent.podSecurityContext.runAsNonRoot | bool | `true` |  |
| kagent.podSecurityContext.seccompProfile.type | string | `"RuntimeDefault"` |  |
| kagent.securityContext.allowPrivilegeEscalation | bool | `false` |  |
| kagent.securityContext.capabilities.drop[0] | string | `"ALL"` |  |
| kagent.securityContext.seccompProfile.type | string | `"RuntimeDefault"` |  |
| kagent.providers.default | string | `"anthropic"` |  |
| kagent.providers.anthropic.provider | string | `"Anthropic"` |  |
| kagent.providers.anthropic.model | string | `"claude-sonnet-4-6"` |  |
| kagent.providers.anthropic.apiKeySecretRef | string | `"kagent-anthropic"` |  |
| kagent.providers.anthropic.apiKeySecretKey | string | `"ANTHROPIC_API_KEY"` |  |
| kagent.providers.anthropic.apiKey | string | `""` |  |
| kagent.serviceMonitor.enabled | bool | `false` |  |
| kagent.serviceMonitor.interval | string | `"60s"` |  |
| kagent.serviceMonitor.labels."observability.giantswarm.io/tenant" | string | `"giantswarm"` |  |
| kagent.otel.tracing.enabled | string | `"auto"` |  |
| kagent.otel.tracing.exporter.otlp.endpoint | string | `"http://otlp-gateway.kube-system.svc:4317"` |  |
| kagent.otel.tracing.exporter.otlp.protocol | string | `"grpc"` |  |
| kagent.otel.tracing.exporter.otlp.insecure | bool | `true` |  |
| kagent.otel.logging.enabled | string | `"auto"` |  |
| kagent.otel.logging.exporter.otlp.endpoint | string | `"http://otlp-gateway.kube-system.svc:4317"` |  |
| kagent.otel.logging.exporter.otlp.insecure | bool | `true` |  |
| kagent.oauth2-proxy.enabled | bool | `false` |  |
| kagent.oauth2-proxy.fullnameOverride | string | `"kagent-oauth2-proxy"` |  |
| kagent.oauth2-proxy.namespaceOverride | string | `"kagent"` |  |
| kagent.oauth2-proxy.redis.enabled | bool | `false` |  |
| kagent.oauth2-proxy.sessionStorage.type | string | `"cookie"` |  |
| kagent.oauth2-proxy.extraVolumes[0].name | string | `"custom-templates"` |  |
| kagent.oauth2-proxy.extraVolumes[0].configMap.name | string | `"kagent-oauth2-proxy-templates"` |  |
| kagent.oauth2-proxy.extraVolumeMounts[0].name | string | `"custom-templates"` |  |
| kagent.oauth2-proxy.extraVolumeMounts[0].mountPath | string | `"/templates"` |  |
| kagent.oauth2-proxy.extraVolumeMounts[0].readOnly | bool | `true` |  |
| kagent.oauth2-proxy.config.existingSecret | string | `""` |  |
| kagent.oauth2-proxy.config.clientID | string | `""` |  |
| kagent.oauth2-proxy.config.clientSecret | string | `""` |  |
| kagent.oauth2-proxy.config.cookieSecret | string | `""` |  |
| kagent.oauth2-proxy.extraEnv[0].name | string | `"OIDC_ISSUER_URL"` |  |
| kagent.oauth2-proxy.extraEnv[0].value | string | `""` |  |
| kagent.oauth2-proxy.extraEnv[1].name | string | `"OIDC_REDIRECT_URL"` |  |
| kagent.oauth2-proxy.extraEnv[1].value | string | `""` |  |
| kagent.oauth2-proxy.extraEnv[2].name | string | `"UPSTREAM_URL"` |  |
| kagent.oauth2-proxy.extraEnv[2].value | string | `"http://kagent-ui:8080"` |  |
| kagent.oauth2-proxy.extraArgs.provider | string | `"oidc"` |  |
| kagent.oauth2-proxy.extraArgs.oidc-issuer-url | string | `"$(OIDC_ISSUER_URL)"` |  |
| kagent.oauth2-proxy.extraArgs.redirect-url | string | `"$(OIDC_REDIRECT_URL)"` |  |
| kagent.oauth2-proxy.extraArgs.upstream | string | `"$(UPSTREAM_URL)"` |  |
| kagent.oauth2-proxy.extraArgs.email-domain | string | `"*"` |  |
| kagent.oauth2-proxy.extraArgs.pass-authorization-header | bool | `true` |  |
| kagent.oauth2-proxy.extraArgs.set-authorization-header | bool | `true` |  |
| kagent.oauth2-proxy.extraArgs.approval-prompt | string | `"auto"` |  |
| kagent.oauth2-proxy.extraArgs.scope | string | `"openid profile email groups offline_access"` |  |
| kagent.oauth2-proxy.extraArgs.cookie-secure | bool | `true` |  |
| kagent.oauth2-proxy.extraArgs.cookie-samesite | string | `"lax"` |  |
| kagent.oauth2-proxy.extraArgs.cookie-refresh | string | `"10m"` |  |
| kagent.oauth2-proxy.extraArgs.reverse-proxy | bool | `true` |  |
| kagent.oauth2-proxy.extraArgs.skip-jwt-bearer-tokens | bool | `true` |  |
| kagent.oauth2-proxy.extraArgs.skip-auth-route | string | `"^/(health|login)$"` |  |
| kagent.oauth2-proxy.extraArgs.skip-auth-regex | string | `"^/(login|_next/static|_next/image|login-bg\\.(jpg|png|webp)|logo-.*\\.png|favicon\\.ico).*$"` |  |
| kagent.oauth2-proxy.extraArgs.custom-templates-dir | string | `"/templates"` |  |
| kagent.oauth2-proxy.service.type | string | `"ClusterIP"` |  |
| kagent.oauth2-proxy.service.portNumber | int | `4180` |  |
| kagent.oauth2-proxy.metrics.enabled | bool | `true` |  |
| kagent.oauth2-proxy.metrics.serviceMonitor.enabled | string | `"auto"` |  |
| kagent.oauth2-proxy.metrics.serviceMonitor.interval | string | `"60s"` |  |
| kagent.oauth2-proxy.metrics.serviceMonitor.labels."observability.giantswarm.io/tenant" | string | `"giantswarm"` |  |
| kagent.grafana-mcp.enabled | bool | `false` |  |
| kagent.kagent-tools.enabled | bool | `false` |  |
| kagent.querydoc.enabled | bool | `false` |  |
| kagent.k8s-agent.enabled | bool | `false` |  |
| kagent.k8s-agent.namespaceOverride | string | `"kagent"` |  |
| kagent.kgateway-agent.enabled | bool | `false` |  |
| kagent.kgateway-agent.namespaceOverride | string | `"kagent"` |  |
| kagent.istio-agent.enabled | bool | `false` |  |
| kagent.istio-agent.namespaceOverride | string | `"kagent"` |  |
| kagent.promql-agent.enabled | bool | `false` |  |
| kagent.promql-agent.namespaceOverride | string | `"kagent"` |  |
| kagent.observability-agent.enabled | bool | `false` |  |
| kagent.observability-agent.namespaceOverride | string | `"kagent"` |  |
| kagent.argo-rollouts-agent.enabled | bool | `false` |  |
| kagent.argo-rollouts-agent.namespaceOverride | string | `"kagent"` |  |
| kagent.helm-agent.enabled | bool | `false` |  |
| kagent.helm-agent.namespaceOverride | string | `"kagent"` |  |
| kagent.cilium-policy-agent.enabled | bool | `false` |  |
| kagent.cilium-policy-agent.namespaceOverride | string | `"kagent"` |  |
| kagent.cilium-manager-agent.enabled | bool | `false` |  |
| kagent.cilium-manager-agent.namespaceOverride | string | `"kagent"` |  |
| kagent.cilium-debug-agent.enabled | bool | `false` |  |
| kagent.cilium-debug-agent.namespaceOverride | string | `"kagent"` |  |
| kagent.kmcp.enabled | bool | `false` |  |
| kagent.kmcp.namespaceOverride | string | `"kagent"` |  |
| kagent.oauth2ProxyIngress.additionalPeers | list | `[]` |  |
| kagent.fluxServiceAccountName | string | `"kagent-flux"` | The ServiceAccount the agents' Flux `HelmRelease`s execute as. Rendered in the kagent namespace whenever kagent is on, bound to `cluster-admin` by a namespace-scoped RoleBinding (full control of the kagent namespace, nothing outside it), and named from this ONE value into agent-manager (`flux.helmReleaseServiceAccount`, derived by the meta chart) and the portal's `agentPlatform.fluxServiceAccountName` (through the `agent-platform.kagent.fluxServiceAccountName` helper), so the three cannot disagree. Under a Flux multi-tenancy lockdown a `HelmRelease` without it runs as the rights-less default ServiceAccount and fails. Empty renders no identity and hands both callers an empty name. |
| kagent.controllerRoute.enabled | bool | `false` |  |
| kagent.controllerRoute.hostname | string | `""` |  |
| kagent.controllerRoute.parentRef.name | string | `"giantswarm-default"` |  |
| kagent.controllerRoute.parentRef.namespace | string | `"envoy-gateway-system"` |  |
| kagent.controllerRoute.grpc.services."kagent.api.v1alpha1.AgentInstanceService" | list | `[]` |  |
| kagent.controllerRoute.grpc.services."kagent.api.v1alpha1.AgentTemplateService" | list | `[]` |  |
| kagent.controllerRoute.grpc.services."kagent.api.v1alpha1.ModelService" | list | `[]` |  |
| kagent.controllerRoute.grpc.services."kagent.api.v1alpha1.SystemService" | list | `[]` |  |
| kagent.controllerRoute.grpc.services."lf.a2a.v1.A2AService" | list | `[]` |  |
| kagent.controllerRoute.jwtAuthentication.enabled | bool | `true` |  |
| kagent.controllerRoute.jwtAuthentication.mode | string | `"Strict"` |  |
| kagent.controllerRoute.jwtAuthentication.issuer | string | `""` |  |
| kagent.controllerRoute.jwtAuthentication.jwks.host | string | `"dex.giantswarm.svc.cluster.local"` |  |
| kagent.controllerRoute.jwtAuthentication.jwks.port | int | `5556` |  |
| kagent.controllerRoute.jwtAuthentication.jwks.path | string | `"/keys"` |  |
| kagent.controllerRoute.jwtAuthentication.jwks.tls.enabled | bool | `false` |  |
| kagent.controllerRoute.jwtAuthentication.jwks.tls.caSecretName | string | `""` |  |
| kagent.uiRoute.enabled | bool | `false` |  |
| kagent.uiRoute.hostname | string | `""` |  |
| kagent.uiRoute.parentRef.name | string | `"giantswarm-default"` |  |
| kagent.uiRoute.parentRef.namespace | string | `"envoy-gateway-system"` |  |
| kagent.uiRoute.backendTrafficPolicy.enabled | bool | `true` |  |
| kagent.uiRoute.backendTrafficPolicy.timeout | string | `"60s"` |  |
| kagent.uiRoute.backendTrafficPolicy.annotations | object | `{}` |  |
| kagent.uiRoute.backendTrafficPolicy.labels | object | `{}` |  |
| kagent.modelConfigs | list | `[]` |  |
| kagent.remoteMcpServers | list | `[]` |  |
| postgres.enabled | bool | `false` |  |
| postgres.namespace | string | `"kagent"` |  |
| postgres.clusterName | string | `"kagent-pg"` |  |
| postgres.instances | int | `3` |  |
| postgres.storage.size | string | `"20Gi"` |  |
| postgres.storage.storageClass | string | `""` |  |
| postgres.image.name | string | `""` |  |
| postgres.imagePullSecrets | list | `[]` |  |
| postgres.affinity | object | `{}` |  |
| postgres.vector.enabled | bool | `false` |  |
| postgres.vector.extensionImage.reference | string | `""` |  |
| postgres.applicationDatabase.name | string | `"kagent"` |  |
| postgres.applicationDatabase.owner | string | `"kagent"` |  |
| postgres.applicationDatabase.schema | string | `"kagent"` |  |
| postgres.applicationDatabase.ensure | string | `"present"` |  |
| postgres.sessionsDatabase.enabled | bool | `false` |  |
| postgres.sessionsDatabase.name | string | `"sessions"` |  |
| postgres.sessionsDatabase.owner | string | `"sessions"` |  |
| postgres.databases.substrate.enabled | bool | `true` |  |
| postgres.databases.substrate.name | string | `"substrate"` |  |
| postgres.databases.substrate.component | string | `"substrate"` |  |
| postgres.databases.substrate.extensions | list | `[]` |  |
| postgres.databases.substrate.reclaimPolicy | string | `"retain"` |  |
| postgres.databases.substrate.secretNamespaces[0] | string | `"ate-system"` |  |
| postgres.databases.kagent-v2.enabled | bool | `true` |  |
| postgres.databases.kagent-v2.name | string | `"kagent_v2"` |  |
| postgres.databases.kagent-v2.extensions[0] | string | `"vector"` |  |
| postgres.databases.kagent-v2.reclaimPolicy | string | `"retain"` |  |
| postgres.databases.kagent-v2.component | string | `"kagent"` |  |
| postgres.databases.kagent-v2.secretNamespaces | list | `[]` |  |
| postgres.backup.enabled | bool | `false` |  |
| postgres.backup.method | string | `"plugin"` |  |
| postgres.backup.schedule | string | `"0 0 2 * * *"` |  |
| postgres.backup.immediate | bool | `true` |  |
| postgres.backup.suspend | bool | `false` |  |
| postgres.backup.serverName | string | `""` |  |
| postgres.backup.objectStore.existingName | string | `""` |  |
| postgres.backup.objectStore.destinationPath | string | `""` |  |
| postgres.backup.objectStore.endpointURL | string | `""` |  |
| postgres.backup.objectStore.retentionPolicy | string | `"30d"` |  |
| postgres.backup.objectStore.wal.compression | string | `"gzip"` |  |
| postgres.backup.objectStore.wal.maxParallel | int | `1` |  |
| postgres.backup.objectStore.data.compression | string | `"gzip"` |  |
| postgres.backup.objectStore.s3.inheritFromIAMRole | bool | `false` |  |
| postgres.backup.objectStore.s3.accessKeyId.name | string | `""` |  |
| postgres.backup.objectStore.s3.accessKeyId.key | string | `"ACCESS_KEY_ID"` |  |
| postgres.backup.objectStore.s3.secretAccessKey.name | string | `""` |  |
| postgres.backup.objectStore.s3.secretAccessKey.key | string | `"ACCESS_SECRET_KEY"` |  |
| postgres.backup.objectStore.azure.inheritFromAzureAD | bool | `false` |  |
| postgres.backup.objectStore.azure.connectionString.name | string | `""` |  |
| postgres.backup.objectStore.azure.connectionString.key | string | `""` |  |
| postgres.backup.objectStore.azure.storageAccount.name | string | `""` |  |
| postgres.backup.objectStore.azure.storageAccount.key | string | `""` |  |
| postgres.backup.objectStore.azure.storageKey.name | string | `""` |  |
| postgres.backup.objectStore.azure.storageKey.key | string | `""` |  |
| postgres.backup.objectStore.sidecar.resources | object | `{}` |  |
| postgres.backup.volumeSnapshot.className | string | `""` |  |
| postgres.backup.volumeSnapshot.walClassName | string | `""` |  |
| postgres.backup.volumeSnapshot.online | bool | `true` |  |
| postgres.backup.serviceAccount.annotations | object | `{}` |  |
| postgres.backup.networkPolicy.ports[0] | string | `"443"` |  |
| postgres.backup.networkPolicy.fqdns | list | `[]` |  |
| postgres.backup.networkPolicy.cidrs | list | `[]` |  |
| postgres.backup.crossplane.enabled | bool | `false` |  |
| postgres.backup.crossplane.provider | string | `"aws"` |  |
| postgres.backup.crossplane.providerConfigRef | string | `""` |  |
| postgres.backup.crossplane.region | string | `""` |  |
| postgres.backup.crossplane.observeOnly | bool | `false` |  |
| postgres.backup.crossplane.tags | object | `{}` |  |
| postgres.backup.crossplane.aws.bucketName | string | `""` |  |
| postgres.backup.crossplane.aws.accountId | string | `""` |  |
| postgres.backup.crossplane.aws.oidcProvider | string | `""` |  |
| postgres.backup.crossplane.aws.roleName | string | `""` |  |
| postgres.backup.crossplane.aws.lifecycleDays | int | `45` |  |
| postgres.backup.crossplane.azure.storageAccountName | string | `""` |  |
| postgres.backup.crossplane.azure.containerName | string | `""` |  |
| postgres.backup.crossplane.azure.resourceGroup | string | `""` |  |
| postgres.backup.crossplane.azure.replicationType | string | `"LRS"` |  |
| postgres.backup.crossplane.azure.lifecycleDays | int | `45` |  |
| postgres.backup.crossplane.azure.private | bool | `false` |  |
| postgres.backup.crossplane.azure.subscriptionId | string | `""` |  |
| postgres.backup.crossplane.azure.vnetName | string | `""` |  |
| postgres.backup.crossplane.azure.subnetName | string | `"node-subnet"` |  |
| postgres.backup.crossplane.azure.privateDnsZoneRef | string | `""` |  |
| klausGateway.image.registry | string | `"gsoci.azurecr.io"` |  |
| klausGateway.agentgateway.enabled | bool | `false` |  |
| klausGateway.routing.store | string | `"memory"` |  |
| klausGateway.routing.defaultTTL | string | `"24h"` |  |
| klausGateway.lifecycle.driver | string | `"static"` |  |
| klausGateway.lifecycle.staticInstances | string | `""` |  |
| klausGateway.upstream.agentgatewayURL | string | `""` |  |
| klausGateway.observability.otlpEndpoint | string | `""` |  |
| klausGateway.slack.enabled | bool | `false` |  |
| klausGateway.slack.mode | string | `"events"` |  |
| klausGateway.slack.secretName | string | `""` |  |
| klausGateway.obo.enabled | bool | `false` |  |
| klausGateway.obo.musterUrl | string | `""` |  |
| klausGateway.obo.callbackBaseUrl | string | `""` |  |
| klausGateway.obo.storePath | string | `""` |  |
| klausGateway.obo.persistence.enabled | bool | `false` |  |
| klausGateway.obo.persistence.size | string | `"64Mi"` |  |
| klausGateway.obo.stateKey | string | `""` |  |
| klausGateway.obo.storeKey | string | `""` |  |
| klausGateway.obo.connectors.enabled | bool | `false` |  |
| klausGateway.reviews.enabled | bool | `false` |  |
| klausGateway.reviews.audience | string | `"klaus-gateway"` |  |
| klausGateway.reviews.allowedCallers | list | `[]` |  |
| klausGateway.cli.enabled | bool | `false` |  |
| klausGateway.a2a.enabled | bool | `false` |  |
| klausGateway.a2a.defaultAgent | string | `""` |  |
| klausGateway.a2a.url | string | `"grpc://agentgateway.agent-platform.svc.cluster.local:8080"` |  |
| klausGateway.a2a.saToken.enabled | bool | `false` |  |
| klausGateway.a2a.saToken.audience | string | `"kagent"` |  |
| klausGateway.agentgatewayRoute.enabled | bool | `false` |  |
| klausGateway.agentgatewayRoute.hostname | string | `""` |  |
| agentgateway.fullnameOverride | string | `"agentgateway-controller"` |  |
| agentgateway.image.registry | string | `"gsoci.azurecr.io"` |  |
| agentgateway.controller.image.repository | string | `"giantswarm/agentgateway-upstream/controller"` |  |
| agentgateway.controller.image.tag | string | `"2.0.0"` |  |
| agentgateway.proxy.image.registry | string | `"gsoci.azurecr.io"` |  |
| agentgateway.proxy.image.repository | string | `"giantswarm/agentgateway-upstream/agentgateway"` |  |
| agentgateway.proxy.image.tag | string | `"2.0.0"` |  |
| agentgateway.podAnnotations."application.giantswarm.io/team" | string | `"bumblebee"` |  |
| agentgateway.podSecurityContext.runAsNonRoot | bool | `true` |  |
| agentgateway.podSecurityContext.seccompProfile.type | string | `"RuntimeDefault"` |  |
| agentgateway.securityContext.allowPrivilegeEscalation | bool | `false` |  |
| agentgateway.securityContext.readOnlyRootFilesystem | bool | `true` |  |
| agentgateway.securityContext.runAsNonRoot | bool | `true` |  |
| agentgateway.securityContext.capabilities.drop[0] | string | `"ALL"` |  |
| agentgateway.securityContext.seccompProfile.type | string | `"RuntimeDefault"` |  |
| agentgateway.resources.requests.cpu | string | `"50m"` |  |
| agentgateway.resources.requests.memory | string | `"128Mi"` |  |
| agentgateway.resources.limits.cpu | string | `"500m"` |  |
| agentgateway.resources.limits.memory | string | `"512Mi"` |  |
| agentSandbox.podSecurity.enabled | string | `"auto"` |  |
| agentSandbox.podSecurity.namespace | string | `"agent-sandbox-system"` |  |
| agentSandbox.podSecurity.podSecurityContext.runAsNonRoot | bool | `true` |  |
| agentSandbox.podSecurity.podSecurityContext.seccompProfile.type | string | `"RuntimeDefault"` |  |
| agentSandbox.podSecurity.containerSecurityContext.allowPrivilegeEscalation | bool | `false` |  |
| agentSandbox.podSecurity.containerSecurityContext.capabilities.drop[0] | string | `"ALL"` |  |
| agentSandbox.podSecurity.containerSecurityContext.runAsNonRoot | bool | `true` |  |
| agentSandbox.podSecurity.containerSecurityContext.seccompProfile.type | string | `"RuntimeDefault"` |  |
| model-manager.fullnameOverride | string | `"model-manager"` |  |
| model-manager.ollama.endpoint | string | `""` |  |
| model-manager.ollama.agentHost | string | `""` |  |
| model-manager.lemonade.endpoint | string | `""` |  |
| model-manager.lemonade.agentHost | string | `""` |  |
| model-manager.lmstudio.endpoint | string | `""` |  |
| model-manager.lmstudio.agentHost | string | `""` |  |
| model-manager.kagent.namespace | string | `"kagent"` |  |
| model-manager.kagent.disableWiring | bool | `false` |  |
| model-manager.mcp.enabled | bool | `true` |  |
| model-manager.oauth.enabled | bool | `true` |  |
| model-manager.oauth.provider | string | `"dex"` |  |
| model-manager.oauth.dex.allowPrivateURLs | bool | `true` |  |
| model-manager.oauth.sso.allowPrivateIPs | bool | `true` |  |
| model-manager.oauth.downstream.enabled | bool | `true` |  |
| model-manager.muster.mcpServer.enabled | bool | `true` |  |
| model-manager.muster.mcpServer.auth.forwardToken | bool | `true` |  |
| model-manager.muster.mcpServer.auth.requiredAudiences[0] | string | `"dex-k8s-authenticator"` |  |
| model-manager.networkPolicy.enabled | bool | `false` |  |
| modelManager.route.enabled | bool | `false` |  |
| modelManager.route.pathPrefix | string | `"/model-manager"` |  |
| modelManager.route.hostname | string | `""` |  |
| modelManager.route.parentRef.name | string | `"giantswarm-default"` |  |
| modelManager.route.parentRef.namespace | string | `"envoy-gateway-system"` |  |
| modelManager.route.jwtAuthentication.enabled | bool | `false` |  |
| modelManager.route.jwtAuthentication.mode | string | `"Strict"` |  |
| modelManager.route.jwtAuthentication.issuer | string | `""` |  |
| modelManager.route.jwtAuthentication.jwks.host | string | `"dex.giantswarm.svc.cluster.local"` |  |
| modelManager.route.jwtAuthentication.jwks.port | int | `5556` |  |
| modelManager.route.jwtAuthentication.jwks.path | string | `"/keys"` |  |
| modelManager.route.jwtAuthentication.jwks.tls.enabled | bool | `false` |  |
| modelManager.route.jwtAuthentication.jwks.tls.caSecretName | string | `""` |  |
| modelManager.kserve.requireApi | bool | `true` |  |
| modelManager.networkPolicy.ingress.additionalPeers | list | `[]` |  |
| modelManager.networkPolicy.huggingFace.fqdns[0].matchName | string | `"huggingface.co"` |  |
| modelManager.networkPolicy.huggingFace.fqdns[1].matchPattern | string | `"*.huggingface.co"` |  |
| modelManager.networkPolicy.huggingFace.fqdns[2].matchPattern | string | `"*.hf.co"` |  |
| modelManager.networkPolicy.huggingFace.fqdns[3].matchPattern | string | `"*.*.hf.co"` |  |
| modelManager.networkPolicy.huggingFace.fqdns[4].matchPattern | string | `"*.*.*.hf.co"` |  |
| modelManager.networkPolicy.huggingFace.cidrs | list | `[]` |  |
| modelManager.networkPolicy.egress.fqdns | list | `[]` |  |
| modelManager.networkPolicy.egress.cidrs | list | `[]` |  |
| modelManager.networkPolicy.registeredBackends | list | `[]` |  |
| vm-manager.fullnameOverride | string | `"vm-manager"` |  |
| vm-manager.persistence.existingClaim | string | `""` |  |
| vm-manager.persistence.create | bool | `false` |  |
| vm-manager.oauth.enabled | bool | `true` |  |
| vm-manager.oauth.provider | string | `"dex"` |  |
| vm-manager.oauth.dex.allowPrivateURLs | bool | `true` |  |
| vm-manager.oauth.sso.allowPrivateIPs | bool | `true` |  |
| vm-manager.muster.mcpServer.enabled | bool | `true` |  |
| vm-manager.muster.mcpServer.auth.forwardToken | bool | `true` |  |
| vm-manager.muster.mcpServer.auth.requiredAudiences | list | `[]` |  |
| vm-manager.networkPolicy.enabled | bool | `false` |  |
| vmManager.podDisruptionBudget.enabled | bool | `true` |  |
| vmManager.podDisruptionBudget.minAvailable | int | `1` |  |
| vmManager.podDisruptionBudget.maxUnavailable | string | `nil` |  |
| vmManager.podDisruptionBudget.unhealthyPodEvictionPolicy | string | `"AlwaysAllow"` |  |
| vmManager.networkPolicy.ingress.additionalPeers | list | `[]` |  |
| vmManager.networkPolicy.guestEgress.cidrs[0] | string | `"0.0.0.0/0"` |  |
| vmManager.networkPolicy.guestEgress.except | list | `[]` |  |
| agent-manager.fullnameOverride | string | `"agent-manager"` |  |
| agent-manager.kagent.namespace | string | `"kagent"` |  |
| agent-manager.agentChart.ociUrl | string | `"oci://gsoci.azurecr.io/charts/giantswarm/agent"` |  |
| agent-manager.agentChart.semver | string | `">=0.2.1 <1.0.0"` |  |
| agent-manager.skills.repositories[0] | string | `"https://github.com/giantswarm/agent-skills"` |  |
| agent-manager.mcp.enabled | bool | `true` |  |
| agent-manager.oauth.enabled | bool | `true` |  |
| agent-manager.oauth.provider | string | `"dex"` |  |
| agent-manager.oauth.dex.allowPrivateURLs | bool | `true` |  |
| agent-manager.oauth.sso.allowPrivateIPs | bool | `true` |  |
| agent-manager.oauth.downstream.enabled | bool | `true` |  |
| agent-manager.muster.mcpServer.enabled | bool | `true` |  |
| agent-manager.muster.mcpServer.auth.forwardToken | bool | `true` |  |
| agent-manager.muster.mcpServer.auth.requiredAudiences[0] | string | `"dex-k8s-authenticator"` |  |
| agent-manager.networkPolicy.enabled | bool | `false` |  |
| agentManager.route.enabled | bool | `false` |  |
| agentManager.route.pathPrefix | string | `"/agent-manager"` |  |
| agentManager.route.hostname | string | `""` |  |
| agentManager.route.parentRef.name | string | `"giantswarm-default"` |  |
| agentManager.route.parentRef.namespace | string | `"envoy-gateway-system"` |  |
| agentManager.route.jwtAuthentication.enabled | bool | `false` |  |
| agentManager.route.jwtAuthentication.mode | string | `"Strict"` |  |
| agentManager.route.jwtAuthentication.issuer | string | `""` |  |
| agentManager.route.jwtAuthentication.jwks.host | string | `"dex.giantswarm.svc.cluster.local"` |  |
| agentManager.route.jwtAuthentication.jwks.port | int | `5556` |  |
| agentManager.route.jwtAuthentication.jwks.path | string | `"/keys"` |  |
| agentManager.route.jwtAuthentication.jwks.tls.enabled | bool | `false` |  |
| agentManager.route.jwtAuthentication.jwks.tls.caSecretName | string | `""` |  |
| agentManager.podDisruptionBudget.enabled | bool | `true` |  |
| agentManager.podDisruptionBudget.minAvailable | int | `1` |  |
| agentManager.podDisruptionBudget.maxUnavailable | string | `nil` |  |
| agentManager.podDisruptionBudget.unhealthyPodEvictionPolicy | string | `"AlwaysAllow"` |  |
| agentManager.flux.requireApi | bool | `false` |  |
| agentManager.networkPolicy.ingress.additionalPeers | list | `[]` |  |
| agentManager.networkPolicy.egress.fqdns[0].matchPattern | string | `"*.blob.core.windows.net"` |  |
| agentManager.networkPolicy.egress.fqdns[1].matchName | string | `"api.github.com"` |  |
| agentManager.networkPolicy.egress.cidrs | list | `[]` |  |
| agentManager.migration.enabled | bool | `true` |  |
| agentManager.migration.image.registry | string | `"gsoci.azurecr.io"` |  |
| agentManager.migration.image.repository | string | `"giantswarm/agent-manager"` |  |
| agentManager.migration.image.tag | string | `"1.1.7"` |  |
| agentManager.migration.dryRun | bool | `false` | dry-run: the report and the diffs, nothing written — a rehearsal of one installation's cut-over before the real run. |
| agentManager.migration.githubToken.secretName | string | `"kagent-skills-token"` |  |
| agentManager.migration.githubToken.key | string | `"token"` |  |
| agentManager.migration.gitopsNamespaces | list | `[]` |  |
| cluster-manager.fullnameOverride | string | `"cluster-manager"` |  |
| cluster-manager.installation.name | string | `""` |  |
| cluster-manager.mcp.enabled | bool | `true` |  |
| cluster-manager.oauth.enabled | bool | `true` |  |
| cluster-manager.oauth.provider | string | `"dex"` |  |
| cluster-manager.oauth.dex.allowPrivateURLs | bool | `true` |  |
| cluster-manager.oauth.sso.allowPrivateIPs | bool | `true` |  |
| cluster-manager.oauth.downstream.enabled | bool | `true` |  |
| cluster-manager.muster.mcpServer.enabled | bool | `true` |  |
| cluster-manager.muster.mcpServer.auth.forwardToken | bool | `true` |  |
| cluster-manager.muster.mcpServer.auth.requiredAudiences[0] | string | `"dex-k8s-authenticator"` |  |
| clusterManager.flux.requireApi | bool | `false` |  |
| clusterManager.prewarmPriorityClass.enabled | bool | `true` |  |
| clusterManager.prewarmPriorityClass.name | string | `"agent-platform-prewarm-placeholder"` |  |
| clusterManager.prewarmPriorityClass.value | int | `-1000` |  |
| clusterManager.networkPolicy.ingress.additionalPeers | list | `[]` |  |
| clusterManager.networkPolicy.workloadClusters.fqdns | list | `[]` |  |
| clusterManager.networkPolicy.workloadClusters.cidrs | list | `[]` |  |
| clusterManager.networkPolicy.workloadClusters.ports[0] | int | `443` |  |
| clusterManager.networkPolicy.workloadClusters.ports[1] | int | `6443` |  |
| clusterManager.networkPolicy.egress.fqdns | list | `[]` |  |
| clusterManager.networkPolicy.egress.cidrs | list | `[]` |  |
| backstage.hostname | string | `""` |  |
| backstage.parentRefs | list | `[]` |  |
| backstage.installationName | string | `"agent-platform"` |  |
| backstage.extraScopes[0] | string | `"federated:id"` |  |
| backstage.extraScopes[1] | string | `"audience:server:client_id:dex-k8s-authenticator"` |  |
| backstage.startUrlSearchParams | object | `{}` |  |
| backstage.enabledExtensions | list | `[]` |  |
| backstage.disabledExtensions[0] | string | `"page:gs/clusters"` |  |
| backstage.disabledExtensions[1] | string | `"nav-item:gs/clusters"` |  |
| backstage.disabledExtensions[2] | string | `"page:gs/deployments"` |  |
| backstage.disabledExtensions[3] | string | `"nav-item:gs/deployments"` |  |
| backstage.disabledExtensions[4] | string | `"page:gs/installations"` |  |
| backstage.disabledExtensions[5] | string | `"nav-item:gs/installations"` |  |
| backstage.disabledExtensions[6] | string | `"page:flux"` |  |
| backstage.disabledExtensions[7] | string | `"nav-item:flux"` |  |
| backstage.disabledExtensions[8] | string | `"page:ai-chat"` |  |
| backstage.disabledExtensions[9] | string | `"api:ai-chat/service"` |  |
| backstage.disabledExtensions[10] | string | `"api:ai-chat/drawer"` |  |
| backstage.disabledExtensions[11] | string | `"app-root-element:ai-chat/drawer"` |  |
| backstage.skillsRepositories[0] | string | `"https://github.com/giantswarm/agent-skills"` |  |
| backstage.catalogs.version | string | `"v0.6.0"` |  |
| backstage.configReload.enabled | bool | `true` |  |
| backstage.configReload.image.registry | string | `"gsoci.azurecr.io"` |  |
| backstage.configReload.image.name | string | `"giantswarm/kubectl"` |  |
| backstage.configReload.image.version | string | `"v1.37.0"` |  |
| mcp-kubernetes.fullnameOverride | string | `"mcp-kubernetes"` |  |
| mcp-kubernetes.mcpKubernetes.oauth.enabled | bool | `true` |  |
| mcp-kubernetes.kubernetesAudience | string | `"dex-k8s-authenticator"` |  |
| cloudnative-pg | object | `{}` |  |
| kagent-crds | object | `{}` |  |
| substrate.createNamespace | bool | `false` |  |
| substrate.postgres.enabled | string | `"auto"` |  |
| substrate.postgres.connectionString | string | `""` |  |
| substrate.postgres.schema | string | `"public"` |  |
| substrate.rustfs.enabled | bool | `false` |  |
| substrate.atelet.storageBackend | string | `"s3"` |  |
| substrate.atelet.nodeSelector | object | `{}` |  |
| substrate.atelet.tolerations | list | `[]` |  |
| substrate.atelet.affinity | object | `{}` |  |
| substrate.atelet.extraEnv | list | `[]` |  |
| substrate-crds | object | `{}` |  |
| kserve-llmisvc-crd | object | `{}` |  |
| kserve-llmisvc-resources | object | `{}` |  |
| modelServing.kserve.requireApi | bool | `true` | api-versions serving.kserve.io/v1alpha2; false skips it. |
| modelServing.namespace.name | string | `"model-serving"` |  |
| modelServing.namespace.create | bool | `true` |  |
| modelServing.namespace.keep | bool | `true` |  |
| modelServing.namespace.labels | object | `{}` |  |
| modelServing.serving.gpuResourceName | string | `"nvidia.com/gpu"` |  |
| modelServing.serving.runtimeClassName | string | `""` |  |
| modelServing.serving.nodeSelector | object | `{}` |  |
| modelServing.gpuPool.taint.key | string | `"nvidia.com/gpu"` |  |
| modelServing.gpuPool.taint.value | string | `""` |  |
| modelServing.gpuPool.taint.effect | string | `"NoSchedule"` |  |
| modelServing.gpuPool.nodeSelector | object | `{}` |  |
| modelServing.prepull.enabled | bool | `true` |  |
| modelServing.prepull.images[0] | string | `"gsoci.azurecr.io/giantswarm/llm-d-fast/llm-d-cuda:v0.8.0"` |  |
| modelServing.prepull.nodeSelector | object | `{}` |  |
| modelServing.prepull.tolerations[0].operator | string | `"Exists"` |  |
| modelServing.prepull.pauseImage.registry | string | `"gsoci.azurecr.io"` |  |
| modelServing.prepull.pauseImage.repository | string | `"giantswarm/pause"` |  |
| modelServing.prepull.pauseImage.tag | string | `"3.10.1"` |  |
| modelServing.prepull.resources.requests.cpu | string | `"5m"` |  |
| modelServing.prepull.resources.requests.memory | string | `"8Mi"` |  |
| modelServing.prepull.resources.limits.memory | string | `"32Mi"` |  |
| modelServing.prepull.modelPresets | list | `[]` |  |
| modelServing.presets | list | `[]` |  |
| modelServing.shippedPresets.enabled | bool | `true` |  |
| modelServing.shippedPresets.exclude | list | `[]` |  |
| modelServing.modelImages.registry | string | `""` |  |
| modelServing.cache.enabled | bool | `true` |  |
| modelServing.cache.pvc.name | string | `"hf-cache"` |  |
| modelServing.cache.pvc.existingClaim | string | `""` |  |
| modelServing.cache.pvc.size | string | `"100Gi"` |  |
| modelServing.cache.pvc.storageClassName | string | `""` |  |
| modelServing.cache.pvc.volumeName | string | `""` |  |
| modelServing.cache.pvc.accessModes[0] | string | `"ReadWriteOnce"` |  |
| modelServing.cache.storageClass.create | bool | `true` |  |
| modelServing.cache.storageClass.name | string | `""` |  |
| modelServing.cache.storageClass.provisioner | string | `"ebs.csi.aws.com"` |  |
| modelServing.cache.storageClass.parameters.type | string | `"gp3"` |  |
| modelServing.cache.storageClass.parameters.iops | string | `"3000"` |  |
| modelServing.cache.storageClass.parameters.throughput | string | `"500"` |  |
| modelServing.cache.fsGroup | int | `1000` |  |
| modelServing.policyException.enabled | bool | `true` |  |
| modelServing.policies.enabled | string | `"auto"` |  |
| modelServing.policies.storageInitializerMemoryLimit | string | `"8Gi"` |  |
| modelServing.policies.progressDeadlineSeconds | int | `3600` |  |
| modelServing.policies.env[0].name | string | `"HF_HUB_DISABLE_XET"` |  |
| modelServing.policies.env[0].value | string | `"1"` |  |
| modelServing.imageVerification.enabled | bool | `true` |  |
| modelServing.imageVerification.images[0] | string | `"gsoci.azurecr.io/giantswarm/*"` |  |
| modelServing.imageVerification.attestors[0].keyless.issuer | string | `"https://oidc.circleci.com"` |  |
| modelServing.imageVerification.attestors[0].keyless.subjectRegExp | string | `"^https://circleci\\.com/api/v2/projects/[a-f0-9-]+/pipeline-definitions/[a-f0-9-]+$"` |  |
| modelServing.imageVerification.attestors[0].keyless.rekor.url | string | `"https://rekor.sigstore.dev"` |  |
| modelServing.imageVerification.type | string | `"SigstoreBundle"` |  |
| modelServing.imageVerification.mutateDigest | bool | `true` |  |
| modelServing.imageVerification.required | bool | `true` |  |
| modelServing.imageVerification.failureAction | string | `"Enforce"` |  |
| modelServing.imageVerification.kyvernoEgress.enabled | bool | `true` |  |
| modelServing.imageVerification.kyvernoEgress.namespace | string | `"kyverno"` |  |
| modelServing.imageVerification.kyvernoEgress.podSelector | object | `{}` |  |
| modelServing.imageVerification.kyvernoEgress.hosts[0].matchName | string | `"gsoci.azurecr.io"` |  |
| modelServing.imageVerification.kyvernoEgress.hosts[1].matchPattern | string | `"*.blob.core.windows.net"` |  |
| modelServing.imageVerification.kyvernoEgress.hosts[2].matchName | string | `"tuf-repo-cdn.sigstore.dev"` |  |
| modelServing.imageVerification.kyvernoEgress.hosts[3].matchName | string | `"rekor.sigstore.dev"` |  |
| modelServing.networkPolicy.llmisvcWorkload.port | int | `8000` |  |
| modelServing.networkPolicy.additionalIngressNamespaces | list | `[]` |  |
| modelServing.networkPolicy.huggingFace.fqdns[0].matchName | string | `"huggingface.co"` |  |
| modelServing.networkPolicy.huggingFace.fqdns[1].matchPattern | string | `"*.huggingface.co"` |  |
| modelServing.networkPolicy.huggingFace.fqdns[2].matchPattern | string | `"*.hf.co"` |  |
| modelServing.networkPolicy.huggingFace.fqdns[3].matchPattern | string | `"*.*.hf.co"` |  |
| modelServing.networkPolicy.huggingFace.fqdns[4].matchPattern | string | `"*.*.*.hf.co"` |  |
| modelServing.networkPolicy.huggingFace.cidrs | list | `[]` |  |
| modelServing.modelsGateway.enabled | bool | `false` |  |
| modelServing.modelsGateway.name | string | `"models"` |  |
| modelServing.modelsGateway.hostPrefix | string | `"models"` |  |
| modelServing.modelsGateway.gatewayClassName | string | `""` |  |
| modelServing.modelsGateway.tls.secretName | string | `""` |  |
| modelServing.modelsGateway.tls.issuerRef.name | string | `""` |  |
| modelServing.modelsGateway.tls.issuerRef.kind | string | `"ClusterIssuer"` |  |
| modelServing.modelsGateway.tls.issuerRef.group | string | `"cert-manager.io"` |  |
| modelServing.modelsGateway.replicas | int | `1` |  |
| modelServing.modelsGateway.externalDns.enabled | bool | `true` |  |
| modelServing.modelsGateway.externalDns.annotations."giantswarm.io/external-dns" | string | `"managed"` |  |
| modelServing.modelsGateway.jwtAuthentication.mode | string | `"Strict"` |  |
| modelServing.modelsGateway.jwtAuthentication.issuer | string | `""` |  |
| modelServing.modelsGateway.jwtAuthentication.audiences[0] | string | `"dex-k8s-authenticator"` |  |
| modelServing.modelsGateway.jwtAuthentication.jwks.host | string | `""` |  |
| modelServing.modelsGateway.jwtAuthentication.jwks.port | int | `443` |  |
| modelServing.modelsGateway.jwtAuthentication.jwks.path | string | `"/keys"` |  |
| modelServing.modelsGateway.jwtAuthentication.jwks.tls.enabled | bool | `false` |  |
| modelServing.modelsGateway.jwtAuthentication.jwks.tls.caSecretName | string | `""` |  |
