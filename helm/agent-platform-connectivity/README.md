# agent-platform-connectivity

Giant Swarm Agent Platform — connectivity / integration layer. Renders the
consumer-side wiring that turns the platform components into a working whole on
a cluster: the public muster route and the agentgateway data-plane Gateway +
AgentgatewayParameters + HTTPRoutes + BackendTrafficPolicies, the NetworkPolicies,
the kagent and klaus-gateway routes, the kagent declarative-agent wiring, the
CloudNativePG Cluster, and — gated on the component toggles — the Backstage
app-config and route, the mcp-kubernetes MCPServer registration with muster and
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
- An extra `kagent.modelConfigs[]` entry calls the provider directly unless it
  sets its own `baseUrl`.
- The data-plane `PodMonitor` is gated on the agentgateway component, not on
  `llmRouting.enabled`, so the MCP path is scraped too and the monitor exists
  before the cutover.

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
| components.agentgateway.enabled | bool | `false` |  |
| components.agent-platform-mcps.enabled | bool | `false` |  |
| components.kagent.enabled | bool | `false` |  |
| components.klaus-gateway.enabled | bool | `false` |  |
| components.agent-sandbox.enabled | bool | `false` |  |
| components.model-manager.enabled | bool | `false` |  |
| components.agent-manager.enabled | bool | `false` |  |
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
| kyvernoPolicies.seccompPolicyName | string | `"restrict-seccomp-strict"` |  |
| kyvernoPolicies.seccompRuleNames[0] | string | `"check-seccomp-strict"` |  |
| kyvernoPolicies.seccompRuleNames[1] | string | `"autogen-check-seccomp-strict"` |  |
| kyvernoPolicies.volumeTypesPolicyName | string | `"restrict-volume-types"` |  |
| kyvernoPolicies.volumeTypesRuleNames[0] | string | `"restricted-volumes"` |  |
| kyvernoPolicies.volumeTypesRuleNames[1] | string | `"autogen-restricted-volumes"` |  |
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
| valkey.valkey.fullnameOverride | string | `"muster-valkey"` |  |
| valkey.valkey.replicaCount | int | `1` |  |
| valkey.valkey.auth.enabled | bool | `true` |  |
| valkey.valkey.auth.usersExistingSecret | string | `""` |  |
| valkey.valkey.auth.aclUsers.default.permissions | string | `"~* &* +@all"` |  |
| valkey.valkey.auth.aclUsers.default.passwordKey | string | `""` |  |
| valkey.valkey.dataStorage.enabled | bool | `true` |  |
| valkey.valkey.dataStorage.requestedSize | string | `"1Gi"` |  |
| valkey.valkey.resources.requests.cpu | string | `"50m"` |  |
| valkey.valkey.resources.requests.memory | string | `"64Mi"` |  |
| valkey.valkey.resources.limits.cpu | string | `"200m"` |  |
| valkey.valkey.resources.limits.memory | string | `"256Mi"` |  |
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
| kagent.serviceMonitor.enabled | bool | `true` |  |
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
| kagent.controllerRoute.pathPrefix | string | `"/kagent"` |  |
| kagent.controllerRoute.hostname | string | `""` |  |
| kagent.controllerRoute.parentRef.name | string | `"giantswarm-default"` |  |
| kagent.controllerRoute.parentRef.namespace | string | `"envoy-gateway-system"` |  |
| kagent.controllerRoute.jwtAuthentication.enabled | bool | `false` |  |
| kagent.controllerRoute.jwtAuthentication.mode | string | `"Strict"` |  |
| kagent.controllerRoute.jwtAuthentication.issuer | string | `""` |  |
| kagent.controllerRoute.jwtAuthentication.jwks.host | string | `"dex.giantswarm.svc.cluster.local"` |  |
| kagent.controllerRoute.jwtAuthentication.jwks.port | int | `5556` |  |
| kagent.controllerRoute.jwtAuthentication.jwks.path | string | `"/keys"` |  |
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
| postgres.vector.enabled | bool | `false` |  |
| postgres.vector.extensionImage.reference | string | `""` |  |
| postgres.applicationDatabase.name | string | `"kagent"` |  |
| postgres.applicationDatabase.owner | string | `"kagent"` |  |
| postgres.applicationDatabase.schema | string | `"kagent"` |  |
| postgres.sessionsDatabase.enabled | bool | `false` |  |
| postgres.sessionsDatabase.name | string | `"sessions"` |  |
| postgres.sessionsDatabase.owner | string | `"sessions"` |  |
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
| klausGateway.a2a.url | string | `"http://agentgateway.agent-platform.svc.cluster.local:8080/kagent/api/a2a/kagent"` |  |
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
| agentgateway.proxy.image.tag | string | `"v1.5.0"` |  |
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
| model-manager.backend | string | `"ollama"` |  |
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
| agent-manager.fullnameOverride | string | `"agent-manager"` |  |
| agent-manager.kagent.namespace | string | `"kagent"` |  |
| agent-manager.agentChart.ociUrl | string | `"oci://gsoci.azurecr.io/charts/giantswarm/agent"` |  |
| agent-manager.agentChart.semver | string | `"x.x.x"` |  |
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
| agentManager.flux.requireApi | bool | `false` |  |
| agentManager.networkPolicy.ingress.additionalPeers | list | `[]` |  |
| agentManager.networkPolicy.egress.fqdns[0].matchPattern | string | `"*.blob.core.windows.net"` |  |
| agentManager.networkPolicy.egress.fqdns[1].matchName | string | `"api.github.com"` |  |
| agentManager.networkPolicy.egress.cidrs | list | `[]` |  |
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
| modelServing.policies.cacheInit.image.registry | string | `"gsoci.azurecr.io"` |  |
| modelServing.policies.cacheInit.image.name | string | `"giantswarm/alpine"` |  |
| modelServing.policies.cacheInit.image.version | string | `"3.24.1"` |  |
| modelServing.policies.cacheInit.resources.requests.cpu | string | `"10m"` |  |
| modelServing.policies.cacheInit.resources.requests.memory | string | `"16Mi"` |  |
| modelServing.policies.cacheInit.resources.limits.cpu | string | `"100m"` |  |
| modelServing.policies.cacheInit.resources.limits.memory | string | `"64Mi"` |  |
| modelServing.policies.storageInitializerMemoryLimit | string | `"4Gi"` |  |
| modelServing.policies.progressDeadlineSeconds | int | `3600` |  |
| modelServing.networkPolicy.predictor.port | int | `8080` |  |
| modelServing.networkPolicy.predictor.additionalIngressNamespaces | list | `[]` |  |
| modelServing.networkPolicy.huggingFace.fqdns[0].matchName | string | `"huggingface.co"` |  |
| modelServing.networkPolicy.huggingFace.fqdns[1].matchPattern | string | `"*.huggingface.co"` |  |
| modelServing.networkPolicy.huggingFace.fqdns[2].matchPattern | string | `"*.hf.co"` |  |
| modelServing.networkPolicy.huggingFace.fqdns[3].matchPattern | string | `"*.*.hf.co"` |  |
| modelServing.networkPolicy.huggingFace.cidrs | list | `[]` |  |
