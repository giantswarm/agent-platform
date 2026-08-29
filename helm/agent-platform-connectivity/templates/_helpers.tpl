{{/* vim: set filetype=mustache: */}}
{{/*
Expand the name of the chart.
*/}}
{{- define "name" -}}
{{- .Chart.Name | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{/*
Create chart name and version as used by the chart label.
*/}}
{{- define "chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimAll "-." -}}
{{- end -}}

{{/*
Common labels
*/}}
{{- define "labels.common" -}}
app: {{ include "name" . | quote }}
{{ include "labels.selector" . }}
app.kubernetes.io/managed-by: {{ .Release.Service | quote }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
application.giantswarm.io/team: {{ index .Chart.Annotations "io.giantswarm.application.team" | quote }}
helm.sh/chart: {{ include "chart" . | quote }}
{{- end -}}

{{/*
Selector labels
*/}}
{{- define "labels.selector" -}}
app.kubernetes.io/name: {{ include "name" . | quote }}
app.kubernetes.io/instance: {{ .Release.Name | quote }}
{{- end -}}

{{/*
Whether a component has a release on this cluster — reads
`components.<name>.enabled`, the single on/off switch. The meta chart forwards
this map (see components: in values.yaml) with the same key path it reads
itself, so the two can never disagree. A component with no `enabled` key is
force-enabled. Emits "true" when on, empty string otherwise.
Usage: include "agent-platform.componentEnabled" (dict "root" $ "name" "kagent")
*/}}
{{- define "agent-platform.componentEnabled" -}}
{{- $root := .root -}}
{{- $c := index $root.Values.components .name -}}
{{- if $c -}}
{{- $on := true -}}
{{- if hasKey $c "enabled" }}{{- $on = $c.enabled }}{{- end }}
{{- if $on }}true{{- end -}}
{{- else -}}
true
{{- end -}}
{{- end -}}

{{/*
Fail the render when a component's on/off toggle is still set the old way, inside
the component's own values block. Those blocks are additionalProperties: true, so
a leftover `enabled` key validates and is then ignored — the component silently
falls back to the `components.<name>.enabled` default, which is off for five of
the six. This turns that into a loud failure naming the new key.
The probe is coalescing-safe: in a layout where a block feeds a real Helm
dependency (the standalone umbrella copies this helper), Helm coalesces that
chart's own top-level `enabled: true` default into the block once the dependency
is on (klaus-gateway ships one), so hasKey cannot tell an operator-set value from
the chart default. A legacy key is therefore reported when it is provably the
operator's: always while the component is off (a disabled dependency's block is
never coalesced), and while it is on when the value is an explicit false (the
only coalesced default is true) — exactly the case where the operator believes
the component is off while it runs anyway. On + true is indistinguishable from a
coalesced default and passes. The removed `mcps:` block needs no entry: the root
schema rejects it already.
*/}}
{{- define "agent-platform.validateLegacyToggles" -}}
{{- $moved := list
      (list "agentgateway" "components.agentgateway.enabled")
      (list "valkey" "components.valkey.enabled")
      (list "kagent" "components.kagent.enabled")
      (list "klausGateway" "components.klaus-gateway.enabled")
      (list "agentSandbox" "components.agent-sandbox.enabled") -}}
{{- $found := list -}}
{{- range $moved -}}
{{- $block := index $.Values (first .) | default dict -}}
{{- if hasKey $block "enabled" -}}
{{- $on := include "agent-platform.componentEnabled" (dict "root" $ "name" (index (splitList "." (last .)) 1)) -}}
{{- if or (not $on) (not (index $block "enabled")) -}}
{{- $found = append $found (printf "%s.enabled -> %s" (first .) (last .)) -}}
{{- end -}}
{{- end -}}
{{- end -}}
{{- with $found -}}
{{- fail (printf "component toggles moved into components.<name>.enabled and the old keys are ignored; move %s (see UPGRADE.md)" (join ", " .)) -}}
{{- end -}}
{{- end -}}

{{/*
Name of the AgentgatewayParameters CR — defaults to release name.
*/}}
{{- define "agent-platform.parametersName" -}}
{{- default .Release.Name .Values.gateway.parameters.name -}}
{{- end -}}

{{/*
Truthy (emits "true") when the request topology routes through agentgateway,
i.e. ingress.mode is agentgateway-muster or agentgateway-direct. Otherwise
emits nothing (empty string = falsy). Gated templates use:
  {{- if (include "agent-platform.ingress.agentgateway" .) }}
*/}}
{{- define "agent-platform.ingress.agentgateway" -}}
{{- if or (eq .Values.ingress.mode "agentgateway-muster") (eq .Values.ingress.mode "agentgateway-direct") -}}true{{- end -}}
{{- end -}}

{{/*
Fully-qualified name of the muster service. Single source of truth: the umbrella
pins muster.fullnameOverride (see values.yaml), which the muster sub-chart uses
verbatim for its Service name. Reading that same key here — rather than
re-deriving the sub-chart's release-name naming algorithm — guarantees the
public route's backendRef and the agent-platform-mcps musterUrl always target
the real muster Service, and turns a misconfiguration into a loud render-time
failure instead of a silent 503.
*/}}
{{- define "agent-platform.musterFullname" -}}
{{- required "muster.fullnameOverride must be set — the umbrella owns muster's public route and its backendRef targets this exact Service name" .Values.muster.fullnameOverride -}}
{{- end -}}

{{/*
Port muster listens on; defaults to 8090. nil-safe: the muster service tree is
owned by the muster release now (not merged into this chart's values), so
.Values.muster.service may be unset.
*/}}
{{- define "agent-platform.musterServicePort" -}}
{{- dig "service" "port" 8090 (.Values.muster | default dict) -}}
{{- end -}}

{{/*
Merged HTTPRoute labels for a named route. The shared base
(ingress.httpRoute.labels) applies to every route; optional per-route overrides
(ingress.httpRoute.<route>.labels) win on key collision, letting a downstream
diverge one route without forking the whole block. Emits nothing when both are
empty. Usage:
  {{- include "agent-platform.httpRouteLabels" (dict "ctx" . "route" "muster") }}
*/}}
{{- define "agent-platform.httpRouteLabels" -}}
{{- $h := .ctx.Values.ingress.httpRoute -}}
{{- $merged := merge (deepCopy (dig .route "labels" dict $h)) ($h.labels | default dict) -}}
{{- with $merged }}{{- toYaml . }}{{- end -}}
{{- end -}}

{{/*
Merged HTTPRoute annotations for a named route — same precedence as
httpRouteLabels (per-route ingress.httpRoute.<route>.annotations override the
shared ingress.httpRoute.annotations). Emits nothing when both are empty.
*/}}
{{- define "agent-platform.httpRouteAnnotations" -}}
{{- $h := .ctx.Values.ingress.httpRoute -}}
{{- $merged := merge (deepCopy (dig .route "annotations" dict $h)) ($h.annotations | default dict) -}}
{{- with $merged }}{{- toYaml . }}{{- end -}}
{{- end -}}

{{/*
Validate the ingress.mode selector and the dependent toggles it implies.
Fails the render with an actionable message when the configuration is
inconsistent. Rendered exactly once via templates/validate.yaml.
*/}}
{{- define "agent-platform.validateIngress" -}}
{{- $mode := .Values.ingress.mode -}}
{{- if not (or (eq $mode "muster-direct") (eq $mode "agentgateway-muster") (eq $mode "agentgateway-direct")) -}}
{{- fail (printf "ingress.mode=%v is invalid; must be one of: muster-direct, agentgateway-muster, agentgateway-direct" $mode) -}}
{{- end -}}
{{- if eq $mode "agentgateway-direct" -}}
{{- fail "ingress.mode=agentgateway-direct requires a DCR-capable IdP (RFC 7591/8707), e.g. Zitadel; not yet supported" -}}
{{- end -}}
{{- $isAgentgateway := or (eq $mode "agentgateway-muster") (eq $mode "agentgateway-direct") -}}
{{- /* The muster `/` route needs a Gateway in every mode; the helper fails the
render when neither ingress.parentRefs, the chart-owned edge nor
global.gatewayApi.parentRefs names one — an empty result would render a route
bound to no Gateway, leaving muster unreachable while install reports success. */ -}}
{{- $_ := include "agent-platform.parentRefs" (dict "ctx" . "override" .Values.ingress.parentRefs "key" "ingress.parentRefs") -}}
{{- /* viaMuster only matters when the mcps sub-chart is installed; with no MCP
servers there is nothing to route, so the consistency check is scoped to the
agent-platform-mcps component. */ -}}
{{- if (include "agent-platform.componentEnabled" (dict "root" . "name" "agent-platform-mcps")) -}}
{{- $mcpsVals := index .Values "agent-platform-mcps" | default dict -}}
{{- $viaMuster := dig "agentgateway" "viaMuster" false $mcpsVals -}}
{{- if eq $mode "agentgateway-muster" -}}
{{- if not (or (eq $viaMuster true) (eq (toString $viaMuster) "true")) -}}
{{- fail "ingress.mode=agentgateway-muster requires agent-platform-mcps.agentgateway.viaMuster=true" -}}
{{- end -}}
{{- else if eq $mode "agentgateway-direct" -}}
{{- if not (or (eq $viaMuster false) (eq (toString $viaMuster) "false")) -}}
{{- fail "ingress.mode=agentgateway-direct requires agent-platform-mcps.agentgateway.viaMuster=false" -}}
{{- end -}}
{{- end -}}
{{- end -}}
{{- $agentgatewayEnabled := include "agent-platform.componentEnabled" (dict "root" . "name" "agentgateway") -}}
{{- if and $isAgentgateway (not $agentgatewayEnabled) -}}
{{- fail "components.agentgateway.enabled must be true in agentgateway-* modes; the controller dependency condition must match ingress.mode" -}}
{{- end -}}
{{- if and (eq $mode "muster-direct") $agentgatewayEnabled -}}
{{- fail "components.agentgateway.enabled must be false in muster-direct mode; the controller dependency condition must match ingress.mode" -}}
{{- end -}}
{{- end -}}

{{/*
global.domain, or a render failure naming the override key the caller could set
instead. Usage: include "agent-platform.domain" (dict "ctx" . "for" "ingress.hostnames")
*/}}
{{- define "agent-platform.domain" -}}
{{- required (printf "global.domain is empty and %s is not set: set global.domain (hostnames derive from it) or %s" .for .for) .ctx.Values.global.domain -}}
{{- end -}}

{{/*
A public hostname: the per-component override when set, else <prefix>.<global.domain>.
Usage: include "agent-platform.hostname" (dict "ctx" . "prefix" "kagent" "override" $h "key" "kagent.uiRoute.hostname")
*/}}
{{- define "agent-platform.hostname" -}}
{{- if .override -}}
{{- .override -}}
{{- else -}}
{{- printf "%s.%s" .prefix (include "agent-platform.domain" (dict "ctx" .ctx "for" .key)) -}}
{{- end -}}
{{- end -}}

{{/*
Truthy when the chart-owned agentgateway Gateway is also the public edge
(gatewayApi.gateway.create). Every public route then attaches to its HTTPS
listener, and the layer-1 routes that forward a front Gateway to the
agentgateway Service are not rendered: the data plane would proxy to itself.
*/}}
{{- define "agent-platform.edgeIsDataPlane" -}}
{{- if .Values.gatewayApi.gateway.create -}}true{{- end -}}
{{- end -}}

{{/*
The parentRefs of a public route, as a YAML list: the per-route override when
set, else the chart-owned edge Gateway, else global.gatewayApi.parentRefs.
The edge ref pins the route to the HTTPS listener via sectionName so the
plaintext :8080 listener never serves public hostnames through the edge's
LoadBalancer Service.
Usage: include "agent-platform.parentRefs" (dict "ctx" . "override" $list "key" "ingress.parentRefs")
*/}}
{{- define "agent-platform.parentRefs" -}}
{{- if .override -}}
{{- toYaml .override -}}
{{- else if (include "agent-platform.edgeIsDataPlane" .ctx) -}}
- name: {{ .ctx.Values.gateway.name }}
  namespace: {{ .ctx.Release.Namespace }}
  group: gateway.networking.k8s.io
  kind: Gateway
  sectionName: https
{{- else if .ctx.Values.global.gatewayApi.parentRefs -}}
{{- toYaml .ctx.Values.global.gatewayApi.parentRefs -}}
{{- else -}}
{{- fail (printf "no public Gateway for %s: set global.gatewayApi.parentRefs (the cluster's Gateway), or gatewayApi.gateway.create: true (the chart creates the edge), or %s" .key .key) -}}
{{- end -}}
{{- end -}}

{{/*
HTTPRoute rule timeouts shared by the umbrella-owned routes (ingress.httpRoute.timeouts).
Emits nothing when unset. Usage:
  {{- with (include "agent-platform.routeTimeouts" .) }}
  {{- . | nindent 6 }}
  {{- end }}
*/}}
{{- define "agent-platform.routeTimeouts" -}}
{{- with .Values.ingress.httpRoute.timeouts }}
timeouts:
  {{- toYaml . | nindent 2 }}
{{- end }}
{{- end -}}

{{/*
Truthy when the umbrella renders its ServiceMonitor / PodMonitor objects
(global.observability.metrics.serviceMonitor.enabled, default true). The
per-component keys underneath (kagent.serviceMonitor.*) keep working.
*/}}
{{- define "agent-platform.serviceMonitor" -}}
{{- if .Values.global.observability.metrics.serviceMonitor.enabled -}}true{{- end -}}
{{- end -}}

{{/*
OTEL exporter env for the agentgateway data-plane container, from
global.observability.traces.otlp. Emits nothing when the endpoint is empty.
Rendered as YAML list items.
*/}}
{{- define "agent-platform.otlpEnv" -}}
{{- with .Values.global.observability.traces.otlp }}
{{- if .endpoint }}
- name: OTEL_EXPORTER_OTLP_ENDPOINT
  value: {{ .endpoint | quote }}
{{- with .protocol }}
- name: OTEL_EXPORTER_OTLP_PROTOCOL
  value: {{ . | quote }}
{{- end }}
{{- if .headers }}
{{- $pairs := list }}
{{- range $key, $value := .headers }}
{{- $pairs = append $pairs (printf "%s=%s" $key $value) }}
{{- end }}
- name: OTEL_EXPORTER_OTLP_HEADERS
  value: {{ join "," $pairs | quote }}
{{- end }}
{{- end }}
{{- end }}
{{- end -}}

{{/*
global.identity.issuerUrl, or a render failure. Usage:
  include "agent-platform.issuerUrl" (dict "ctx" . "for" "the kagent JWT policy")
*/}}
{{- define "agent-platform.issuerUrl" -}}
{{- required (printf "global.identity.issuerUrl is empty but %s needs the login issuer" .for) .ctx.Values.global.identity.issuerUrl -}}
{{- end -}}

{{/*
Guards on the global.* contract. Rendered once via templates/validate.yaml.
*/}}
{{- define "agent-platform.validateGlobal" -}}
{{- if .Values.gatewayApi.gateway.create -}}
{{- if not (include "agent-platform.ingress.agentgateway" .) -}}
{{- fail "gatewayApi.gateway.create is true but ingress.mode is muster-direct: the chart-owned edge is the agentgateway data-plane Gateway, so set ingress.mode: agentgateway-muster and components.agentgateway.enabled: true" -}}
{{- end -}}
{{- if not .Values.gatewayApi.gateway.tls.secretName -}}
{{- fail "gatewayApi.gateway.create is true but gatewayApi.gateway.tls.secretName is empty: the HTTPS listener for *.<global.domain> needs the wildcard certificate Secret" -}}
{{- end -}}
{{- $_ := include "agent-platform.domain" (dict "ctx" . "for" "gatewayApi.gateway.create") -}}
{{- end -}}
{{- /* The muster chart reads its own OIDC keys; a value that disagrees with
global.identity would give two components two different logins. Checked only
where both sides are set, so installs that ignore global.identity are
untouched. */ -}}
{{- if .Values.muster.enabled -}}
{{- $server := dig "muster" "oauth" "server" dict (.Values.muster | default dict) -}}
{{- if and ($server.enabled | default false) .Values.global.identity.issuerUrl -}}
{{- $dex := $server.dex | default dict -}}
{{- if and $dex.issuerUrl (ne $dex.issuerUrl .Values.global.identity.issuerUrl) -}}
{{- fail (printf "muster.muster.oauth.server.dex.issuerUrl (%s) differs from global.identity.issuerUrl (%s); the platform has one login provider" $dex.issuerUrl .Values.global.identity.issuerUrl) -}}
{{- end -}}
{{- if and $dex.clientId .Values.global.identity.clientId (ne $dex.clientId .Values.global.identity.clientId) -}}
{{- fail (printf "muster.muster.oauth.server.dex.clientId (%s) differs from global.identity.clientId (%s)" $dex.clientId .Values.global.identity.clientId) -}}
{{- end -}}
{{- if and $server.existingSecret .Values.global.identity.existingSecret (ne $server.existingSecret .Values.global.identity.existingSecret) -}}
{{- fail (printf "muster.muster.oauth.server.existingSecret (%s) differs from global.identity.existingSecret (%s)" $server.existingSecret .Values.global.identity.existingSecret) -}}
{{- end -}}
{{- end -}}
{{- end -}}
{{- end -}}

{{/*
Cilium DNS egress rule for kube-dns and node-local-dns.
Rendered as a YAML list item; the caller must provide the surrounding `egress:` key.
*/}}
{{- define "agent-platform.dnsEgress" -}}
- toEndpoints:
    - matchLabels:
        io.kubernetes.pod.namespace: kube-system
        k8s-app: coredns
    - matchLabels:
        io.kubernetes.pod.namespace: kube-system
        k8s-app: k8s-dns-node-cache
  toPorts:
    - ports:
        - port: "1053"
          protocol: UDP
        - port: "1053"
          protocol: TCP
        - port: "53"
          protocol: UDP
        - port: "53"
          protocol: TCP
{{- end -}}
