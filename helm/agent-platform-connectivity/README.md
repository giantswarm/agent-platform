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
time to first token — with the calling agent as a label. The `agent-platform`
Grafana dashboard in `giantswarm/dashboards` reads them.

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
   listener, one Gateway-scoped `AgentgatewayPolicy` (the route-type map and
   the metric labels), and the model-price ConfigMap the cost counter reads.
   Nothing routes through it yet.

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
- Only one metrics policy may target a Gateway: custom labels replace rather
  than merge, and when two policies target the same Gateway the one with the
  lexicographically lowest policy key wins while the other is silently dropped.
- An extra `kagent.modelConfigs[]` entry rides the listener unless it sets its
  own `baseUrl` or names a provider other than `llmRouting.backend.provider`.
  The `baseUrl` lands under the CRD's block for the entry's provider —
  `anthropic`, `openAI`, `sapAICore`, the three `ModelConfigSpec` gives one —
  never the lower-cased provider name, which the API server would prune; the
  render refuses a `baseUrl` on any other provider and a `provider` outside the
  CRD's enum (case-sensitive). `make verify-kagent-crds` sweeps every provider
  of the enum against the kagent line's CRD.
- The data-plane `PodMonitor` is gated on the agentgateway component, not on
  `llmRouting.enabled`, so the MCP path is scraped too and the monitor exists
  before the cutover.

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
  configmaps, namespaces and clustertrustbundles (and on persistentvolumeclaims
  while model serving's cache claim is this chart's, [The Hugging Face cache
  claim](#the-hugging-face-cache-claim)), with `attest` on the two
  podcert signers (the apiserver's condition for writing a bundle that names a
  signer), for the hook's lifetime
  (`templates/substrate/hooks-rbac.yaml`; the hook Job include is
  `agent-platform.hooks.job` in `templates/_hooks.tpl`).
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
| The configmap / crd routing stores, the embedded ChannelRoute controller | `klausGateway.routing.store: configmap` / `crd`, `klausGateway.controller.enabled` | the kube-apiserver, as above |

The keys are the klaus-gateway chart's, forwarded by the meta chart; one left
unset is read with that chart's default (memory, bolt, off), so the default
shape gets no policy. An out-of-band Valkey (`routing.valkey.url` outside the
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

The cilium controller policy renders the DNS proxy clause only while a name is
selected. With in-cluster hosts alone it renders no external rule and no proxy
clause, in either flavour; `make verify-wiring` asserts that against
`origin/main`, the controller policies first and then the whole render.

## The GPU node pool

A GPU node pool created through the platform (bumblebee-plans#46, the `gpu-node-pool` chart) arrives tainted `nvidia.com/gpu` `NoSchedule` — only accelerator work lands there — and labelled `giantswarm.io/machine-pool=<cluster>-<pool>`. `modelServing.gpuPool` is the serving layer's one input for both (giantswarm/agent-platform#315): `taint` (`key`, `value`, `effect`; the default is the pool chart's taint, tolerated with `operator: Exists` because the pool sets no value — a `value` narrows the toleration to `Equal`, an empty `key` is an untainted pool and renders nothing) and `nodeSelector` (the pool's label; per pool, so the slice release's values set it, empty by default).

The chart applies the input to everything it renders onto the pool and to nothing else — platform components never carry it:

| Site | Toleration | Node selector |
|---|---|---|
| The `ClusterServingRuntime` (`modelServing.runtime`; KServe copies both onto every predictor pod of the runtime) | the pool's first, `runtime.tolerations` after it (an equal entry once) | the pool's under `runtime.nodeSelector` (the runtime's keys win) |
| Every published preset's `scheduling` block (`agent-platform-serving-preset-<name>`) | the pool's first, the preset's own after it (an equal entry once) | the pool's under the preset's own keys |
| The discovery ConfigMap `agent-platform-model-serving`: `spec.gpuPool.taint.{key,value,effect}`, `spec.gpuPool.nodeSelector` | published for model-manager (`>= 0.23.0`, giantswarm/model-manager#86), which schedules the `LLMInferenceService`s it composes, its download Jobs and its inventory scan pods by it; a registered backend document may override it | likewise |

Three render guards: the effect is `NoSchedule`, `PreferNoSchedule` or `NoExecute` (empty tolerates every effect of the key), the key is a qualified name, and every selector value is a string (a label value is a string: quote a number). `make verify-gpu-pool` asserts the three sites in the default, pool-selected, valued and untainted shapes, the guards, the meta chart's forwarding of the block, and that an empty taint key leaves the serving render byte-identical to `origin/main` but for the discovery block; `ci/test-model-serving-gpu-pool-values.yaml` is the pool-selected fixture.

## The Hugging Face cache claim

`modelServing.cache.pvc` (`hf-cache`, `500Gi`, the cluster's default StorageClass unless `storageClassName` names one — `"-"` is the empty class for a pre-provisioned volume, `volumeName` binds one) is one claim in the serving namespace with one subdirectory per InferenceService; the Kyverno policies mount it into every predictor's storage-initializer and runtime, model-manager's pre-warm downloads land in the same layout. The claim has **no consumer of its own** — the first predictor (or download Job) that mounts it is what Binds it — and under a StorageClass with `volumeBindingMode: WaitForFirstConsumer` (kind's `standard`, the fleet's default `gp3`) it stays `Pending` until then. Helm's wait counts a Pending claim as not ready, so as a release resource it failed every install and upgrade with the switch on (giantswarm/agent-platform#483).

The claim is therefore **applied by a `post-install,post-upgrade` hook Job** (`templates/model-serving/cache-pvc.yaml`; the hook include `agent-platform.hooks.job`, the identity `<release>-hooks` with `get`, `create`, `patch` on `persistentvolumeclaims` while the claim is the chart's, never `delete`), not rendered as a release resource: `kubectl apply --server-side --force-conflicts` under the field manager `agent-platform-connectivity`, the log says `created` or `present` and the claim's phase, nothing waits for a Bind. It binds where its first consumer schedules — the GPU pool's zone, which is what a zonal volume needs. What follows from the claim not being Helm's:

- an uninstall or a `cache.enabled` flip leaves it — the intent of the `helm.sh/resource-policy: keep` it carries (hundreds of gigabytes of downloads outlive the release), now by construction;
- a `size` change is applied by the next upgrade's hook (the StorageClass has to allow expansion); a change to an immutable field (`storageClassName`, `accessModes`, `volumeName`) fails the hook — and the release — naming the field;
- an installation upgrading from a chart that rendered the claim as a release resource keeps it (the keep policy) and the hook takes its fields over;
- `existingClaim` names a claim of the operator's instead: nothing is applied, the name is published to model-manager and the portal.

`make verify-serving-slice` asserts the hook, its claim, the identity and the knobs, and that `cache.enabled: false` or an `existingClaim` render none of it; `make verify-wiring` the serving shape without a `PersistentVolumeClaim` object.

## Voluntary disruption

Two objects of this chart guard the platform's single-replica pods against Karpenter consolidation and node drains (giantswarm/agent-platform#431), a third the muster-valkey pod (#439) and a fourth the Substrate worker pool (#472); the other components' guards travel on their own charts' knobs through the meta chart. The four budgets share one spec helper (`agent-platform.podDisruptionBudget.spec`) and its guards.

- `gateway.parameters.podAnnotations` (default `karpenter.sh/do-not-disrupt: "true"`) is merged onto the agentgateway data-plane pod template through `AgentgatewayParameters` `deployment.spec.template.metadata` (strategic merge). Every MCP call, every A2A stream and — with `llmRouting` on — every model stream crosses those pods. Set the value to `"false"` or the map to `{}` to opt out.
- `agentManager.podDisruptionBudget` (default `enabled: true`, `minAvailable: 1`, `unhealthyPodEvictionPolicy: AlwaysAllow`) renders a `PodDisruptionBudget agent-manager` in the release namespace, selecting the agent-manager pods by name the way the component's network policies do — the agent-manager chart has no knob of its own. Exactly one of `minAvailable` / `maxUnavailable` (int or percentage); the render refuses both, neither, and a policy outside the API's enum. Inert while `components.agent-manager.enabled` is false.
- `valkey.podDisruptionBudget` (same defaults and guards; giantswarm/agent-platform#439) renders a `PodDisruptionBudget muster-valkey` — named after `valkey.valkey.fullnameOverride`, which the render requires — selecting the pod the way the valkey subchart labels it (`app.kubernetes.io/name: valkey` and the component's release name, `valkey`). muster's OAuth token store is that one pod on an RWO volume; neither the wrapper nor the upstream subchart has a budget knob. Inert while `components.valkey.enabled` is false; the meta chart never forwards the key to the valkey release.
- `kagent.substrateWorkerPool.podDisruptionBudget` (default `enabled: true`, `maxUnavailable: 1`, `unhealthyPodEvictionPolicy: AlwaysAllow`; giantswarm/agent-platform#472) renders a `PodDisruptionBudget` named after the pool (`kagent.substrateWorkerPool.name`, which the render requires) in the **kagent namespace**, selecting the worker pods by the label Substrate's ate-controller puts on them, `ate.dev/worker-pool: <name>` — the same label the `substrate-workers` network policy selects. The kagent chart renders the `WorkerPool` and has no budget template. Four workers host one actor each, so `maxUnavailable: 1` lets a voluntary drain move one worker at a time instead of the pool. Inert while `components.kagent.enabled` is false; the meta chart never forwards the key to the kagent release.

With one replica, `minAvailable: 1` refuses every voluntary eviction — Karpenter reports `DisruptionBlocked`, a node drain waits for its drain timeout (the fleet's Karpenter NodePools force-terminate after `terminationGracePeriod: 30m`) — and `AlwaysAllow` keeps a pod that is not Ready evictable. `make verify-disruption` asserts the render, the knobs off and the guards; `make verify-workerpool` the worker budget. A spot reclaim is not a voluntary eviction: the placement of the stateful singletons on on-demand capacity is the meta chart's `scheduling.singletons`, which reaches the component releases as their charts' `nodeSelector` / `tolerations` and is never forwarded here.

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
| components.kserve-crd.enabled | bool | `false` |  |
| components.kserve-resources.enabled | bool | `false` |  |
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
| gateway.parameters.dataPlaneResources.requests.ephemeral-storage | string | `"50Mi"` |  |
| gateway.parameters.dataPlaneResources.limits.ephemeral-storage | string | `"512Mi"` |  |
| gateway.parameters.replicas | int | `2` |  |
| gateway.parameters.podDisruptionBudget.enabled | bool | `true` |  |
| gateway.parameters.spread.enabled | bool | `true` |  |
| gateway.parameters.spread.topologyKeys[0] | string | `"kubernetes.io/hostname"` |  |
| gateway.parameters.spread.maxSkew | int | `1` |  |
| gateway.parameters.spread.whenUnsatisfiable | string | `"ScheduleAnyway"` |  |
| gateway.parameters.podAnnotations | object | `{}` |  |
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
| llmRouting.metricLabels[0].name | string | `"agent"` |  |
| llmRouting.metricLabels[0].expression | string | `"source.unverifiedWorkload.serviceAccount"` |  |
| llmRouting.metricLabels[1].name | string | `"agent_namespace"` |  |
| llmRouting.metricLabels[1].expression | string | `"source.unverifiedWorkload.namespace"` |  |
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
| hooks.kubectlImage.registry | string | `"docker.io"` |  |
| hooks.kubectlImage.repository | string | `"alpine/k8s"` |  |
| hooks.kubectlImage.tag | string | `"1.37.0"` |  |
| hooks.opensslImage.registry | string | `"docker.io"` |  |
| hooks.opensslImage.repository | string | `"alpine/openssl"` |  |
| hooks.opensslImage.tag | string | `"3.5.8"` |  |
| extraObjects | list | `[]` |  |
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
| klausGateway.crd.install | bool | `true` |  |
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
| agentgateway.image.tag | string | `"v1.2.1"` |  |
| agentgateway.controller.image.repository | string | `"giantswarm/agentgateway-controller"` |  |
| agentgateway.proxy.image.registry | string | `"gsoci.azurecr.io"` |  |
| agentgateway.proxy.image.repository | string | `"giantswarm/agentgateway"` |  |
| agentgateway.proxy.image.tag | string | `"v1.5.1-gs.4"` |  |
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
| kserve-crd | object | `{}` |  |
| kserve-resources | object | `{}` |  |
| kserve-llmisvc-crd | object | `{}` |  |
| kserve-llmisvc-resources | object | `{}` |  |
| modelServing.kserve.requireApi | bool | `true` |  |
| modelServing.namespace.name | string | `"model-serving"` |  |
| modelServing.namespace.create | bool | `true` |  |
| modelServing.namespace.labels | object | `{}` |  |
| modelServing.runtime.name | string | `"kserve-vllm"` |  |
| modelServing.runtime.image.registry | string | `"docker.io"` |  |
| modelServing.runtime.image.name | string | `"vllm/vllm-openai"` |  |
| modelServing.runtime.image.version | string | `"v0.29.0"` |  |
| modelServing.runtime.args[0] | string | `"--model"` |  |
| modelServing.runtime.args[1] | string | `"/mnt/models"` |  |
| modelServing.runtime.args[2] | string | `"--port"` |  |
| modelServing.runtime.args[3] | string | `"8080"` |  |
| modelServing.runtime.args[4] | string | `"--served-model-name"` |  |
| modelServing.runtime.args[5] | string | `"{{.Name}}"` |  |
| modelServing.runtime.env[0].name | string | `"HF_HUB_ENABLE_HF_TRANSFER"` |  |
| modelServing.runtime.env[0].value | string | `"1"` |  |
| modelServing.runtime.env[1].name | string | `"VLLM_CONFIG_ROOT"` |  |
| modelServing.runtime.env[1].value | string | `"/tmp"` |  |
| modelServing.runtime.resources.requests.cpu | string | `"2"` |  |
| modelServing.runtime.resources.requests.memory | string | `"16Gi"` |  |
| modelServing.runtime.resources.limits.cpu | string | `"8"` |  |
| modelServing.runtime.resources.limits.memory | string | `"64Gi"` |  |
| modelServing.runtime.shmSize | string | `"16Gi"` |  |
| modelServing.runtime.startupProbe.initialDelaySeconds | int | `300` |  |
| modelServing.runtime.startupProbe.periodSeconds | int | `30` |  |
| modelServing.runtime.startupProbe.failureThreshold | int | `360` |  |
| modelServing.runtime.annotations."prometheus.kserve.io/path" | string | `"/metrics"` |  |
| modelServing.runtime.annotations."prometheus.kserve.io/port" | string | `"8080"` |  |
| modelServing.runtime.supportedModelFormats[0].name | string | `"vLLM"` |  |
| modelServing.runtime.supportedModelFormats[0].version | string | `"1"` |  |
| modelServing.runtime.supportedModelFormats[0].autoSelect | bool | `true` |  |
| modelServing.runtime.supportedModelFormats[0].priority | int | `1` |  |
| modelServing.runtime.nodeSelector | object | `{}` |  |
| modelServing.runtime.tolerations | list | `[]` |  |
| modelServing.serving.gpuResourceName | string | `"nvidia.com/gpu"` |  |
| modelServing.serving.runtimeClassName | string | `""` |  |
| modelServing.serving.nodeSelector | object | `{}` |  |
| modelServing.serving.deploymentStrategyType | string | `"Recreate"` |  |
| modelServing.serving.timeoutSeconds | int | `1800` |  |
| modelServing.gpuPool.taint.key | string | `"nvidia.com/gpu"` |  |
| modelServing.gpuPool.taint.value | string | `""` |  |
| modelServing.gpuPool.taint.effect | string | `"NoSchedule"` |  |
| modelServing.gpuPool.nodeSelector | object | `{}` |  |
| modelServing.presets | list | `[]` |  |
| modelServing.shippedPresets.enabled | bool | `true` |  |
| modelServing.shippedPresets.exclude | list | `[]` |  |
| modelServing.cache.enabled | bool | `true` |  |
| modelServing.cache.pvc.name | string | `"hf-cache"` |  |
| modelServing.cache.pvc.existingClaim | string | `""` |  |
| modelServing.cache.pvc.size | string | `"500Gi"` |  |
| modelServing.cache.pvc.storageClassName | string | `""` |  |
| modelServing.cache.pvc.volumeName | string | `""` |  |
| modelServing.cache.pvc.accessModes[0] | string | `"ReadWriteOnce"` |  |
| modelServing.policies.enabled | string | `"auto"` |  |
| modelServing.policyException.enabled | bool | `true` |  |
| modelServing.policyException.cacheInit.image.registry | string | `"gsoci.azurecr.io"` |  |
| modelServing.policyException.cacheInit.image.name | string | `"giantswarm/alpine"` |  |
| modelServing.policyException.cacheInit.image.version | string | `"3.24.1"` |  |
| modelServing.policyException.cacheInit.resources.requests.cpu | string | `"10m"` |  |
| modelServing.policyException.cacheInit.resources.requests.memory | string | `"16Mi"` |  |
| modelServing.policyException.cacheInit.resources.limits.cpu | string | `"100m"` |  |
| modelServing.policyException.cacheInit.resources.limits.memory | string | `"64Mi"` |  |
| modelServing.policyException.storageInitializerMemoryLimit | string | `"4Gi"` |  |
| modelServing.policyException.progressDeadlineSeconds | int | `3600` |  |
| modelServing.networkPolicy.predictor.port | int | `8080` |  |
| modelServing.networkPolicy.predictor.additionalIngressNamespaces | list | `[]` |  |
| modelServing.networkPolicy.huggingFace.fqdns[0].matchName | string | `"huggingface.co"` |  |
| modelServing.networkPolicy.huggingFace.fqdns[1].matchPattern | string | `"*.huggingface.co"` |  |
| modelServing.networkPolicy.huggingFace.fqdns[2].matchPattern | string | `"*.hf.co"` |  |
| modelServing.networkPolicy.huggingFace.fqdns[3].matchPattern | string | `"*.*.hf.co"` |  |
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
