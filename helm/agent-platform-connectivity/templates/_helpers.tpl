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
Whether an OPTIONAL component is on: "true" when `components.<name>` exists and
is enabled, empty otherwise — unlike componentEnabled, a name absent from the
roster is NOT force-on. For components a chart of this version may not carry
yet (the KServe control plane, model serving), so a guard can defer to them
without asserting they exist.
Usage: include "agent-platform.optionalComponentEnabled" (dict "root" $ "name" "kserve-resources")
*/}}
{{- define "agent-platform.optionalComponentEnabled" -}}
{{- if hasKey (.root.Values.components | default dict) .name -}}
{{- include "agent-platform.componentEnabled" . -}}
{{- end -}}
{{- end -}}

{{/*
The tenant identity of the agents' Flux HelmReleases: the ServiceAccount name
(kagent.fluxServiceAccountName) while the kagent component is on, "" otherwise.
ONE value, three consumers: templates/kagent/flux-service-account.yaml renders
the ServiceAccount and its RoleBinding from it, the meta chart derives
agent-manager's flux.helmReleaseServiceAccount from the same key, and the
portal's app-config (agentPlatform.fluxServiceAccountName) reads this helper.
Under a multi-tenancy lockdown (helm-controller with --no-cross-namespace-refs
and a rights-less default ServiceAccount; the Flux multi-tenancy admission
policy on Giant Swarm management clusters) a HelmRelease executes as the
ServiceAccount it names and fails without one.
Usage: include "agent-platform.kagent.fluxServiceAccountName" .
*/}}
{{- define "agent-platform.kagent.fluxServiceAccountName" -}}
{{- if (include "agent-platform.componentEnabled" (dict "root" . "name" "kagent")) -}}
{{- dig "fluxServiceAccountName" "" (.Values.kagent | default dict) -}}
{{- end -}}
{{- end -}}

{{/*
The kagent component's Helm release name — the value of the pods'
app.kubernetes.io/instance label the kagent chart stamps, and the name its
fullname helper falls back to when kagent.fullnameOverride is empty. The meta
chart names every component's release after its roster key (components.yaml:
`releaseName: <key>`; the kagent entry's key, chart and values block are all
`kagent`), and this chart is only ever installed by that meta chart. It is NOT
this chart's .Release.Name: under the retired standalone umbrella every subchart
shared one release name, so a selector on .Release.Name happened to match
kagent's pods; under the meta chart each component is its own release and such
a selector matches nothing (the kagent controller metrics Service had no
endpoints, giantswarm/agent-platform#305). Every selector, Service name or
hostname this chart derives for kagent's OWN objects goes through this helper
or agent-platform.kagent.fullname, never through .Release.Name.
Usage: include "agent-platform.kagent.releaseName" .
*/}}
{{- define "agent-platform.kagent.releaseName" -}}
kagent
{{- end -}}

{{/*
The kagent chart's fullname — what it prefixes its Services with
(`<fullname>-controller`, `<fullname>-ui`): kagent.fullnameOverride (pinned to
`kagent` in values.yaml), or, empty, the release name (the chart's fullname
helper collapses `<release>-<chart>` to the release name when the two match, as
they do under the meta chart).
Usage: include "agent-platform.kagent.fullname" .
*/}}
{{- define "agent-platform.kagent.fullname" -}}
{{- .Values.kagent.fullnameOverride | default (include "agent-platform.kagent.releaseName" .) -}}
{{- end -}}

{{/*
Fail the render when a component's on/off toggle is still set the old way, inside
the component's own values block. Those blocks are additionalProperties: true, so
a leftover `enabled` key validates and is then ignored — the component silently
falls back to the `components.<name>.enabled` default, which is off for five of
the six. This turns that into a loud failure naming the new key.
Neither this chart nor the meta chart has a Helm dependency, so no chart default
is ever coalesced into these blocks: a legacy key can only be the operator's and
is reported whatever its value, whether the component is on or off. (An umbrella
that feeds these blocks to real Helm dependencies sees klaus-gateway's own
`enabled: true` default coalesced in while that dependency is on and has to
special-case it; nothing here does.) The removed `mcps:` block needs no entry:
the root schema rejects it already.
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
{{- if hasKey (index $.Values (first .) | default dict) "enabled" -}}
{{- $found = append $found (printf "%s.enabled -> %s" (first .) (last .)) -}}
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
{{- /* muster-direct runs without the agentgateway component, so its CRDs are
not on the cluster: anything that renders an agentgateway.dev object or attaches
to the agentgateway Gateway must fail here, naming the knob, instead of shipping
objects the API server rejects (the model-manager / agent-manager routes already
guard themselves the same way). */ -}}
{{- if eq $mode "muster-direct" -}}
{{- $mcpsValues := index .Values "agent-platform-mcps" | default dict -}}
{{- if and (include "agent-platform.componentEnabled" (dict "root" . "name" "agent-platform-mcps")) (dig "agentgateway" "enabled" false $mcpsValues) (dig "mcpServers" (list) $mcpsValues) -}}
{{- fail "muster-direct mode cannot serve the agentgateway.dev resources agent-platform-mcps renders per MCP server; set agent-platform-mcps.agentgateway.enabled=false to reach the MCP servers through muster" -}}
{{- end -}}
{{- if and (include "agent-platform.componentEnabled" (dict "root" . "name" "kagent")) .Values.kagent.controllerRoute.enabled -}}
{{- fail "kagent.controllerRoute renders agentgateway.dev resources on the agentgateway Gateway; it requires an agentgateway-* ingress.mode" -}}
{{- end -}}
{{- if and (include "agent-platform.componentEnabled" (dict "root" . "name" "klaus-gateway")) .Values.klausGateway.agentgatewayRoute.enabled -}}
{{- fail "klausGateway.agentgatewayRoute renders agentgateway.dev resources on the agentgateway Gateway; it requires an agentgateway-* ingress.mode" -}}
{{- end -}}
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
=== Cluster shape ===

The knobs that describe what the cluster can admit — Kyverno policies, the
network-policy flavor, ServiceMonitors/PodMonitors, the agent-sandbox
pod-security policy — accept `auto` (the default): the object renders when its
API group is served. `.Capabilities.APIVersions` is the live discovery under
helm-controller, the Helm CLI and `--dry-run=server`; under `helm template` it
is Helm's built-in set unless `--api-versions` names more, so an offline render
resolves every `auto` to the vanilla shape. An explicit `true|false` (or
`cilium|kubernetes`) always wins over detection.

Under the meta chart (agent-platform) these knobs arrive resolved: it detects
once with the same helpers and forwards concrete values, so the wiring rendered
here and every component's own copy come from one answer. The helpers below
are what a render of this chart on its own uses; on the same cluster they give
the same answer. Templates read the truthy wrappers underneath
(agent-platform.kyvernoPolicies, .networkPolicyFlavor, .serviceMonitor,
.agentSandboxPodSecurity, .modelServingPolicies), never the raw values.
*/}}

{{/*
Resolve one `auto|true|false` knob to the string "true" or "false". `auto`
follows whether .api is served; an explicit boolean (or its string form from
--set-string) is returned as is; anything else fails the render naming .key.
Usage: include "agent-platform.shape.resolve" (dict "root" $ "key" "kyvernoPolicies.enabled" "value" .Values.kyvernoPolicies.enabled "api" "kyverno.io/v1")
*/}}
{{- define "agent-platform.shape.resolve" -}}
{{- $v := .value -}}
{{- if or (kindIs "invalid" $v) (and (kindIs "string" $v) (eq $v "auto")) -}}
{{- if .root.Capabilities.APIVersions.Has .api }}true{{ else }}false{{ end -}}
{{- else if kindIs "bool" $v -}}
{{- if $v }}true{{ else }}false{{ end -}}
{{- else if or (eq (toString $v) "true") (eq (toString $v) "false") -}}
{{- toString $v -}}
{{- else -}}
{{- fail (printf "%s must be one of auto, true, false (got %v)" .key $v) -}}
{{- end -}}
{{- end -}}

{{/*
kyvernoPolicies.enabled resolved: "true" when Kyverno policies render (auto:
kyverno.io/v1 served).
*/}}
{{- define "agent-platform.shape.kyvernoPolicies" -}}
{{- include "agent-platform.shape.resolve" (dict "root" . "key" "kyvernoPolicies.enabled" "value" .Values.kyvernoPolicies.enabled "api" "kyverno.io/v1") -}}
{{- end -}}

{{/*
networkPolicy.flavor resolved: "cilium" or "kubernetes" (auto: cilium when
cilium.io/v2 is served, else kubernetes).
*/}}
{{- define "agent-platform.shape.networkPolicyFlavor" -}}
{{- $f := .Values.networkPolicy.flavor -}}
{{- if or (kindIs "invalid" $f) (eq (toString $f) "auto") -}}
{{- if .Capabilities.APIVersions.Has "cilium.io/v2" }}cilium{{ else }}kubernetes{{ end -}}
{{- else if or (eq (toString $f) "cilium") (eq (toString $f) "kubernetes") -}}
{{- toString $f -}}
{{- else -}}
{{- fail (printf "networkPolicy.flavor must be one of auto, cilium, kubernetes (got %v)" $f) -}}
{{- end -}}
{{- end -}}

{{/*
global.observability.metrics.serviceMonitor.enabled resolved: "true" when the
monitor objects render (auto: monitoring.coreos.com/v1 served).
*/}}
{{- define "agent-platform.shape.serviceMonitor" -}}
{{- include "agent-platform.shape.resolve" (dict "root" . "key" "global.observability.metrics.serviceMonitor.enabled" "value" .Values.global.observability.metrics.serviceMonitor.enabled "api" "monitoring.coreos.com/v1") -}}
{{- end -}}

{{/*
agentSandbox.podSecurity.enabled resolved: "true" when the agent-sandbox
pod-security ClusterPolicy renders. It is a Kyverno mutate policy, so `auto`
follows the RESOLVED kyvernoPolicies.enabled (an explicit
kyvernoPolicies.enabled: false switches it off with the rest; the
"podSecurity requires kyvernoPolicies" guard then never fires on auto).
*/}}
{{- define "agent-platform.shape.agentSandboxPodSecurity" -}}
{{- $v := dig "podSecurity" "enabled" "auto" (.Values.agentSandbox | default dict) -}}
{{- if or (kindIs "invalid" $v) (and (kindIs "string" $v) (eq $v "auto")) -}}
{{- include "agent-platform.shape.kyvernoPolicies" . -}}
{{- else -}}
{{- include "agent-platform.shape.resolve" (dict "root" . "key" "agentSandbox.podSecurity.enabled" "value" $v "api" "kyverno.io/v1") -}}
{{- end -}}
{{- end -}}

{{/*
modelServing.policies.enabled resolved: "true" when the model-serving Kyverno
cache policies render. They are Kyverno mutate policies, so `auto` follows the
RESOLVED kyvernoPolicies.enabled, like the agent-sandbox pod-security policy;
an explicit true with kyvernoPolicies.enabled false fails the render
(templates/model-serving/validate.yaml).
*/}}
{{- define "agent-platform.shape.modelServingPolicies" -}}
{{- $v := dig "policies" "enabled" "auto" (.Values.modelServing | default dict) -}}
{{- if or (kindIs "invalid" $v) (and (kindIs "string" $v) (eq $v "auto")) -}}
{{- include "agent-platform.shape.kyvernoPolicies" . -}}
{{- else -}}
{{- include "agent-platform.shape.resolve" (dict "root" . "key" "modelServing.policies.enabled" "value" $v "api" "kyverno.io/v1") -}}
{{- end -}}
{{- end -}}

{{/*
Truthy (emits "true") when the kyverno.io objects render. Gated templates use:
  {{- if (include "agent-platform.kyvernoPolicies" .) }}
*/}}
{{- define "agent-platform.kyvernoPolicies" -}}
{{- if eq (include "agent-platform.shape.kyvernoPolicies" .) "true" -}}true{{- end -}}
{{- end -}}

{{/*
The network-policy flavor every policy template branches on: "cilium" or
"kubernetes". Gated templates use:
  {{- if eq (include "agent-platform.networkPolicyFlavor" .) "cilium" }}
*/}}
{{- define "agent-platform.networkPolicyFlavor" -}}
{{- include "agent-platform.shape.networkPolicyFlavor" . -}}
{{- end -}}

{{/*
Truthy when the umbrella renders its ServiceMonitor / PodMonitor objects
(global.observability.metrics.serviceMonitor.enabled, default auto). The
per-component keys underneath (kagent.serviceMonitor.*) keep working.
*/}}
{{- define "agent-platform.serviceMonitor" -}}
{{- if eq (include "agent-platform.shape.serviceMonitor" .) "true" -}}true{{- end -}}
{{- end -}}

{{/*
Truthy when the agent-sandbox pod-security ClusterPolicy renders
(agentSandbox.podSecurity.enabled, default auto).
*/}}
{{- define "agent-platform.agentSandboxPodSecurity" -}}
{{- if eq (include "agent-platform.shape.agentSandboxPodSecurity" .) "true" -}}true{{- end -}}
{{- end -}}

{{/*
Truthy when the model-serving Kyverno cache policies render
(modelServing.policies.enabled, default auto).
*/}}
{{- define "agent-platform.modelServingPolicies" -}}
{{- if eq (include "agent-platform.shape.modelServingPolicies" .) "true" -}}true{{- end -}}
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
CoreDNS is labeled k8s-app: kube-dns upstream (kubeadm) and k8s-app: coredns
on Giant Swarm clusters; match both so either fleet shape resolves.
Rendered as a YAML list item; the caller must provide the surrounding `egress:` key.
*/}}
{{- define "agent-platform.dnsEgress" -}}
- toEndpoints:
    - matchLabels:
        io.kubernetes.pod.namespace: kube-system
        k8s-app: kube-dns
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

{{/*
Cilium DNS egress rule with the DNS proxy clause. Same selectors as dnsEgress,
plus `rules.dns` so Cilium learns the name -> address mappings the policy's
toFQDNs selectors need; without the clause a toFQDNs rule matches nothing on a
cluster that has no cluster-wide DNS visibility policy. Rendered as a YAML list
item; the caller must provide the surrounding `egress:` key.
*/}}
{{- define "agent-platform.dnsEgressWithProxy" -}}
- toEndpoints:
    - matchLabels:
        io.kubernetes.pod.namespace: kube-system
        k8s-app: kube-dns
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
      rules:
        dns:
          - matchPattern: "*"
{{- end -}}

{{/*
Kubernetes NetworkPolicy DNS egress rule (kube-dns / coredns / node-local cache
in kube-system on 53 and 1053), the kubernetes flavor of dnsEgress. Rendered as
a YAML list item; the caller must provide the surrounding `egress:` key.
*/}}
{{- define "agent-platform.dnsEgress.kubernetes" -}}
- to:
    - namespaceSelector:
        matchLabels:
          kubernetes.io/metadata.name: kube-system
      podSelector:
        matchExpressions:
          - key: k8s-app
            operator: In
            values: [kube-dns, coredns, k8s-dns-node-cache]
  ports:
    - port: 53
      protocol: UDP
    - port: 53
      protocol: TCP
    - port: 1053
      protocol: UDP
    - port: 1053
      protocol: TCP
{{- end -}}

{{/*
The host of an issuer URL (scheme and port stripped); empty when the URL is
empty or has no host. Usage: include "agent-platform.urlHost" $url
*/}}
{{- define "agent-platform.urlHost" -}}
{{- if . -}}
{{- $u := urlParse . -}}
{{- regexReplaceAll ":[0-9]+$" ($u.host | default "") "" -}}
{{- end -}}
{{- end -}}

{{/*
The hosts a platform service that validates tokens itself (mcp-oauth) reaches
at its login identity provider, as a JSON list: the discovery document, the
JWKS (validating the id_tokens muster and the portal forward), userinfo and
the token endpoint. The dex provider serves all of them from the issuer host.
Google spreads them over three hosts, none of which its issuer URL
(https://accounts.google.com) names: accounts.google.com (discovery,
authorization), www.googleapis.com (JWKS /oauth2/v3/certs, userinfo) and
oauth2.googleapis.com (token, revocation) — the endpoints mcp-oauth's google
provider dials. Empty for the dex provider without an issuer.
Usage: include "agent-platform.idpHosts" (dict "provider" "dex" "issuerUrl" $url)
*/}}
{{- define "agent-platform.idpHosts" -}}
{{- if eq (.provider | default "dex") "google" -}}
{{- list "accounts.google.com" "www.googleapis.com" "oauth2.googleapis.com" | toJson -}}
{{- else -}}
{{- $hosts := list -}}
{{- with (include "agent-platform.urlHost" .issuerUrl) }}{{- $hosts = list . -}}{{- end -}}
{{- $hosts | toJson -}}
{{- end -}}
{{- end -}}

{{/*
Cilium egress rules from a platform service to the login identity provider:
the hosts of agent-platform.idpHosts (discovery, JWKS, userinfo, token) by
name on 443 (through the DNS proxy rule the caller renders), plus the cluster
entity on 443 and 10443 — an issuer served through an in-cluster Gateway whose
LoadBalancer address Cilium translates to the data-plane pods (the Envoy edge
listens on 10443), or an in-cluster issuer Service, is `cluster` at policy
time, not the name. Mirrors the klaus-gateway OBO and oauth2-proxy egress.
Rendered as YAML list items; the caller provides `egress:` and the indentation.
Usage: include "agent-platform.idpEgress.cilium" (dict "provider" "dex" "issuerUrl" $url)
*/}}
{{- define "agent-platform.idpEgress.cilium" -}}
{{- with (include "agent-platform.idpHosts" . | fromJsonArray) }}
- toFQDNs:
    {{- range . }}
    - matchName: {{ . }}
    {{- end }}
  toPorts:
    - ports:
        - port: "443"
          protocol: TCP
{{- end }}
- toEntities:
    - cluster
  toPorts:
    - ports:
        - port: "443"
          protocol: TCP
        - port: "10443"
          protocol: TCP
{{- end -}}

{{/*
Postgres backup helpers (templates/postgres/*). backupEnabled is non-empty when
the Cluster renders AND postgres.backup.enabled is set.
*/}}
{{- define "agent-platform.postgres.backupEnabled" -}}
{{- if and .Values.postgres.enabled .Values.postgres.backup.enabled -}}true{{- end -}}
{{- end -}}

{{/* "aws" or "azure" while postgres.backup.crossplane renders the store, else "". */}}
{{- define "agent-platform.postgres.crossplane" -}}
{{- $b := .Values.postgres.backup -}}
{{- if and (include "agent-platform.postgres.backupEnabled" .) (eq $b.method "plugin") $b.crossplane.enabled -}}
{{- $b.crossplane.provider -}}
{{- end -}}
{{- end -}}

{{/* The ObjectStore the Cluster's plugin entry names. */}}
{{- define "agent-platform.postgres.objectStoreName" -}}
{{- .Values.postgres.backup.objectStore.existingName | default (printf "%s-backup" .Values.postgres.clusterName) -}}
{{- end -}}

{{/* The Secret the Crossplane Azure Account writes its connection strings to. */}}
{{- define "agent-platform.postgres.azureAccountSecret" -}}
{{- printf "%s-backup-store" .Values.postgres.clusterName -}}
{{- end -}}

{{/* arn:aws, or arn:aws-cn in the China partition. */}}
{{- define "agent-platform.postgres.awsPartition" -}}
{{- if hasPrefix "cn-" .Values.postgres.backup.crossplane.region -}}arn:aws-cn{{- else -}}arn:aws{{- end -}}
{{- end -}}

{{/* The IAM role the Crossplane AWS block renders. */}}
{{- define "agent-platform.postgres.awsRoleName" -}}
{{- $aws := .Values.postgres.backup.crossplane.aws -}}
{{- $aws.roleName | default $aws.bucketName -}}
{{- end -}}

{{- define "agent-platform.postgres.awsRoleArn" -}}
{{- printf "%s:iam::%s:role/%s" (include "agent-platform.postgres.awsPartition" .) .Values.postgres.backup.crossplane.aws.accountId (include "agent-platform.postgres.awsRoleName" .) -}}
{{- end -}}

{{/*
destinationPath: the explicit value, else derived from the Crossplane store
(s3://<bucket>/ or https://<account>.blob.core.windows.net/<container>/).
*/}}
{{- define "agent-platform.postgres.destinationPath" -}}
{{- $b := .Values.postgres.backup -}}
{{- $xp := include "agent-platform.postgres.crossplane" . -}}
{{- if $b.objectStore.destinationPath -}}
{{- $b.objectStore.destinationPath -}}
{{- else if eq $xp "aws" -}}
{{- printf "s3://%s/" $b.crossplane.aws.bucketName -}}
{{- else if eq $xp "azure" -}}
{{- printf "https://%s.blob.core.windows.net/%s/" $b.crossplane.azure.storageAccountName $b.crossplane.azure.containerName -}}
{{- end -}}
{{- end -}}

{{/*
Annotations for the Cluster's ServiceAccount: the values, plus the IRSA role
annotation when the Crossplane AWS block renders the role. YAML map or "".
*/}}
{{- define "agent-platform.postgres.serviceAccountAnnotations" -}}
{{- $ann := deepCopy (.Values.postgres.backup.serviceAccount.annotations | default dict) -}}
{{- if eq (include "agent-platform.postgres.crossplane" .) "aws" -}}
{{- $_ := set $ann "eks.amazonaws.com/role-arn" (include "agent-platform.postgres.awsRoleArn" .) -}}
{{- end -}}
{{- if $ann -}}{{- toYaml $ann -}}{{- end -}}
{{- end -}}

{{/* Crossplane managementPolicies: everything, or Observe only. */}}
{{- define "agent-platform.postgres.crossplaneManagementPolicies" -}}
{{- if .Values.postgres.backup.crossplane.observeOnly -}}
- Observe
{{- else -}}
- "*"
{{- end -}}
{{- end -}}

{{/* Same, for data-bearing objects: never Delete, so an uninstall keeps the data. */}}
{{- define "agent-platform.postgres.crossplaneManagementPoliciesNoDelete" -}}
{{- if .Values.postgres.backup.crossplane.observeOnly -}}
- Observe
{{- else -}}
- Create
- Update
- LateInitialize
- Observe
{{- end -}}
{{- end -}}

{{/* Tags on the cloud resources: chart defaults under the installation's own. */}}
{{- define "agent-platform.postgres.crossplaneTags" -}}
{{- $tags := dict "app" "agent-platform-postgres" "managed-by" "crossplane" "name" (include "agent-platform.postgres.crossplaneStoreName" .) -}}
{{- $tags = merge (deepCopy (.Values.postgres.backup.crossplane.tags | default dict)) $tags -}}
{{- if eq .Values.postgres.backup.crossplane.provider "azure" -}}
{{- $clean := dict -}}
{{- range $k, $v := $tags -}}{{- $_ := set $clean ($k | replace "-" "_") $v -}}{{- end -}}
{{- $tags = $clean -}}
{{- end -}}
{{- toYaml $tags -}}
{{- end -}}

{{/* The bucket (aws) or container (azure) name. */}}
{{- define "agent-platform.postgres.crossplaneStoreName" -}}
{{- $xp := .Values.postgres.backup.crossplane -}}
{{- if eq $xp.provider "azure" -}}{{- $xp.azure.containerName -}}{{- else -}}{{- $xp.aws.bucketName -}}{{- end -}}
{{- end -}}
