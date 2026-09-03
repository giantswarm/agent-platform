{{/* vim: set filetype=mustache: */}}
{{/*
Helpers of the model-manager component's wiring (templates/model-manager/).

Two values blocks feed these templates: modelManager (the umbrella wiring —
route, JWT policy, network policy inputs, guards) and model-manager (the
component chart's own values: backend, endpoint, kagent namespace, OAuth,
Service name and port; hyphenated, so reached through index), read here the
way the kagent templates read kagent.namespaceOverride — a component release's
values cannot be derived at render time, so the wiring reads what the chart
will see.
*/}}

{{/*
Truthy when the model-manager component is on (components.model-manager.enabled).
*/}}
{{- define "agent-platform.modelManager.enabled" -}}
{{- include "agent-platform.componentEnabled" (dict "root" . "name" "model-manager") -}}
{{- end -}}

{{/*
The component chart's values block, model-manager (a dict; empty when unset).
*/}}
{{- define "agent-platform.modelManager.chartValues" -}}
{{- index .Values "model-manager" | default dict | toJson -}}
{{- end -}}

{{/*
The model-manager Service name. Single source of truth: the umbrella pins
model-manager.fullnameOverride (values.yaml), which the component chart uses
verbatim for its Service, and the AgentgatewayBackend host and the network
policies target exactly that name — a misconfiguration fails the render
instead of a silent 503.
*/}}
{{- define "agent-platform.modelManager.fullname" -}}
{{- $chart := include "agent-platform.modelManager.chartValues" . | fromJson -}}
{{- required "model-manager.fullnameOverride must be set — the umbrella's route and network policies target this exact Service name" (dig "fullnameOverride" "" $chart) -}}
{{- end -}}

{{/*
The port the model-manager Service listens on (model-manager.service.port, default 8080).
*/}}
{{- define "agent-platform.modelManager.servicePort" -}}
{{- $chart := include "agent-platform.modelManager.chartValues" . | fromJson -}}
{{- dig "service" "port" 8080 $chart -}}
{{- end -}}

{{/*
The serving backend the chart is configured with (model-manager.backend).
*/}}
{{- define "agent-platform.modelManager.backend" -}}
{{- $chart := include "agent-platform.modelManager.chartValues" . | fromJson -}}
{{- dig "backend" "ollama" $chart -}}
{{- end -}}

{{/*
The Ollama API base URL model-manager dials (model-manager.ollama.endpoint).
*/}}
{{- define "agent-platform.modelManager.ollamaEndpoint" -}}
{{- $chart := include "agent-platform.modelManager.chartValues" . | fromJson -}}
{{- dig "ollama" "endpoint" "" $chart -}}
{{- end -}}

{{/*
The Ollama endpoint split for network policies, as JSON:
  { "host": "<host>", "port": <int>, "isIP": bool }
The port defaults from the scheme (80 / 443) when the URL carries none.
*/}}
{{- define "agent-platform.modelManager.ollamaTarget" -}}
{{- $endpoint := include "agent-platform.modelManager.ollamaEndpoint" . -}}
{{- $url := urlParse $endpoint -}}
{{- $hostport := $url.host | default "" -}}
{{- $host := $hostport -}}
{{- $port := 80 -}}
{{- if eq $url.scheme "https" }}{{- $port = 443 -}}{{- end -}}
{{- if contains ":" $hostport -}}
{{- $host = regexReplaceAll ":[0-9]+$" $hostport "" -}}
{{- $port = regexFind "[0-9]+$" $hostport | int -}}
{{- end -}}
{{- dict "host" $host "port" $port "isIP" (regexMatch `^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$` $host) | toJson -}}
{{- end -}}

{{/*
The namespace model-manager wires ModelConfigs into (model-manager.kagent.namespace).
*/}}
{{- define "agent-platform.modelManager.kagentNamespace" -}}
{{- $chart := include "agent-platform.modelManager.chartValues" . | fromJson -}}
{{- dig "kagent" "namespace" "kagent" $chart -}}
{{- end -}}

{{/*
Truthy when the component validates the caller's identity itself
(model-manager.oauth.enabled): the network policies then admit egress to the
identity provider.
*/}}
{{- define "agent-platform.modelManager.oauthEnabled" -}}
{{- $chart := include "agent-platform.modelManager.chartValues" . | fromJson -}}
{{- if dig "oauth" "enabled" false $chart }}true{{ end -}}
{{- end -}}

{{/*
The issuer URL the component validates tokens against: the dex provider's
model-manager.oauth.dex.issuerURL, else global.identity.issuerUrl (the chart's
own fallback). Empty for the google provider (public Google endpoints) and
when neither is set.
*/}}
{{- define "agent-platform.modelManager.issuerUrl" -}}
{{- $chart := include "agent-platform.modelManager.chartValues" . | fromJson -}}
{{- if eq (dig "oauth" "provider" "dex" $chart) "dex" -}}
{{- dig "oauth" "dex" "issuerURL" "" $chart | default .Values.global.identity.issuerUrl -}}
{{- end -}}
{{- end -}}

{{/*
The public hostname of the model-manager route: the override when set, else
agentgateway.<global.domain> — the same hostname as the kagent controller route.
*/}}
{{- define "agent-platform.modelManager.hostname" -}}
{{- $route := .Values.modelManager.route -}}
{{- include "agent-platform.hostname" (dict "ctx" . "prefix" "agentgateway" "override" $route.hostname "key" "modelManager.route.hostname") -}}
{{- end -}}

{{/*
Labels of every object the umbrella renders for the component.
*/}}
{{- define "agent-platform.modelManager.labels" -}}
{{ include "labels.common" . }}
app.kubernetes.io/component: model-manager
{{- end -}}

{{/*
The selector labels of the model-manager pods, as the component chart stamps
them (app.kubernetes.io/name from its chart name or nameOverride). The
component runs as its own release, so it is selected by name only, not by a
release-scoped instance label — like the muster policies.
Rendered as YAML mapping entries; the caller provides the indentation.
*/}}
{{- define "agent-platform.modelManager.podSelector" -}}
{{- $chart := include "agent-platform.modelManager.chartValues" . | fromJson -}}
app.kubernetes.io/name: {{ dig "nameOverride" "" $chart | default "model-manager" }}
{{- end -}}
