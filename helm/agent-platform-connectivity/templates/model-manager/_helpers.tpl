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
The serving backend the chart is configured with (model-manager.backend) — the
one-backend form; with model-manager.backends set, the default (first) backend.
*/}}
{{- define "agent-platform.modelManager.backend" -}}
{{- index (include "agent-platform.modelManager.backends" . | fromJsonArray) 0 -}}
{{- end -}}

{{/*
The serving backends the component runs, as a JSON list: model-manager.backends
when set (one model-manager in front of several servers, e.g. ollama and
lemonade), else [model-manager.backend]. The first is the default backend.
*/}}
{{- define "agent-platform.modelManager.backends" -}}
{{- $chart := include "agent-platform.modelManager.chartValues" . | fromJson -}}
{{- $list := dig "backends" (list) $chart -}}
{{- if $list }}{{ $list | toJson }}{{ else }}{{ list (dig "backend" "ollama" $chart) | toJson }}{{ end -}}
{{- end -}}

{{/*
Truthy when the named driver is among the component's backends.
Usage: include "agent-platform.modelManager.hasBackend" (dict "root" . "name" "kserve")
*/}}
{{- define "agent-platform.modelManager.hasBackend" -}}
{{- if has .name (include "agent-platform.modelManager.backends" .root | fromJsonArray) }}true{{ end -}}
{{- end -}}

{{/*
The Ollama API base URL model-manager dials (model-manager.ollama.endpoint).
*/}}
{{- define "agent-platform.modelManager.ollamaEndpoint" -}}
{{- $chart := include "agent-platform.modelManager.chartValues" . | fromJson -}}
{{- dig "ollama" "endpoint" "" $chart -}}
{{- end -}}

{{/*
The Lemonade Server base URL model-manager dials (model-manager.lemonade.endpoint).
*/}}
{{- define "agent-platform.modelManager.lemonadeEndpoint" -}}
{{- $chart := include "agent-platform.modelManager.chartValues" . | fromJson -}}
{{- dig "lemonade" "endpoint" "" $chart -}}
{{- end -}}

{{/*
The endpoint of a host-proxying backend — ollama: model-manager.ollama.endpoint,
lemonade: model-manager.lemonade.endpoint. Empty for kserve, which has none.
*/}}
{{- define "agent-platform.modelManager.hostEndpoint" -}}
{{- $backend := include "agent-platform.modelManager.backend" . -}}
{{- if eq $backend "ollama" -}}{{ include "agent-platform.modelManager.ollamaEndpoint" . }}{{- else if eq $backend "lemonade" -}}{{ include "agent-platform.modelManager.lemonadeEndpoint" . }}{{- end -}}
{{- end -}}

{{/*
An endpoint URL (the argument) split for network policies, as JSON:
  { "host": "<host>", "port": <int>, "isIP": bool }
The port defaults from the scheme (80 / 443) when the URL carries none.
*/}}
{{- define "agent-platform.modelManager.endpointTarget" -}}
{{- $url := urlParse . -}}
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
The Ollama endpoint split for network policies (endpointTarget of ollamaEndpoint).
*/}}
{{- define "agent-platform.modelManager.ollamaTarget" -}}
{{- include "agent-platform.modelManager.endpointTarget" (include "agent-platform.modelManager.ollamaEndpoint" .) -}}
{{- end -}}

{{/*
The host backend's endpoint split for network policies — ollama or lemonade;
an empty JSON object for kserve.
*/}}
{{- define "agent-platform.modelManager.hostTarget" -}}
{{- $endpoint := include "agent-platform.modelManager.hostEndpoint" . -}}
{{- if $endpoint -}}{{ include "agent-platform.modelManager.endpointTarget" $endpoint }}{{- else -}}{}{{- end -}}
{{- end -}}

{{/*
Every host model server among the component's backends (ollama, lemonade),
split for network policies, as a JSON list of
  { "backend": "<name>", "host": "<host>", "port": <int>, "isIP": bool }
in the order of the backends list. Empty when none is listed (kserve alone).
*/}}
{{- define "agent-platform.modelManager.hostTargets" -}}
{{- $out := list -}}
{{- range $name := include "agent-platform.modelManager.backends" . | fromJsonArray -}}
{{- $endpoint := "" -}}
{{- if eq $name "ollama" -}}{{- $endpoint = include "agent-platform.modelManager.ollamaEndpoint" $ -}}
{{- else if eq $name "lemonade" -}}{{- $endpoint = include "agent-platform.modelManager.lemonadeEndpoint" $ -}}
{{- end -}}
{{- if $endpoint -}}
{{- $out = append $out (merge (dict "backend" $name) (include "agent-platform.modelManager.endpointTarget" $endpoint | fromJson)) -}}
{{- end -}}
{{- end -}}
{{- $out | toJson -}}
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
The identity provider the component validates tokens with
(model-manager.oauth.provider): dex (the default) or google.
*/}}
{{- define "agent-platform.modelManager.oauthProvider" -}}
{{- $chart := include "agent-platform.modelManager.chartValues" . | fromJson -}}
{{- dig "oauth" "provider" "dex" $chart -}}
{{- end -}}

{{/*
The issuer URL the component validates tokens against: the dex provider's
model-manager.oauth.dex.issuerURL, else global.identity.issuerUrl (the chart's
own fallback). Empty for the google provider (whose public endpoints
agent-platform.idpHosts names from the provider alone) and when neither is set.
*/}}
{{- define "agent-platform.modelManager.issuerUrl" -}}
{{- $chart := include "agent-platform.modelManager.chartValues" . | fromJson -}}
{{- if eq (include "agent-platform.modelManager.oauthProvider" .) "dex" -}}
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
