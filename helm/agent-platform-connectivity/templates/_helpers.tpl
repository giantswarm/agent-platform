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
{{- if not .Values.ingress.parentRefs -}}
{{- fail "ingress.parentRefs is required in all modes — the umbrella-owned muster `/` route (and the agentgateway `/mcp` route in agentgateway-* modes) attaches to it; an empty parentRefs renders a route bound to no Gateway, leaving muster unreachable while install reports success" -}}
{{- end -}}
{{- /* viaMuster only matters when the mcps sub-chart is installed; with no MCP
servers there is nothing to route, so the consistency check is scoped to mcps.enabled. */ -}}
{{- if .Values.mcps.enabled -}}
{{- /* Overrides under the deprecated `agentic-platform-mcps` key win over the
`agent-platform-mcps` block, mirroring the umbrella's legacyValuesFrom merge. */ -}}
{{- $mcpsVals := mergeOverwrite (deepCopy (index .Values "agent-platform-mcps" | default dict)) (index .Values "agentic-platform-mcps" | default dict) -}}
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
{{- $agentgatewayEnabled := or (eq .Values.agentgateway.enabled true) (eq (toString .Values.agentgateway.enabled) "true") -}}
{{- if and $isAgentgateway (not $agentgatewayEnabled) -}}
{{- fail "agentgateway.enabled must be true in agentgateway-* modes; the controller dependency condition must match ingress.mode" -}}
{{- end -}}
{{- if and (eq $mode "muster-direct") $agentgatewayEnabled -}}
{{- fail "agentgateway.enabled must be false in muster-direct mode; the controller dependency condition must match ingress.mode" -}}
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
