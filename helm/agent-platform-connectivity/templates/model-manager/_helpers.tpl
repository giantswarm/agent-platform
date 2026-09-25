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
The ServiceAccount model-manager runs as, the model-manager chart's rule:
serviceAccount.name, else the fullname while the chart creates it, else default.
*/}}
{{- define "agent-platform.modelManager.serviceAccountName" -}}
{{- $chart := include "agent-platform.modelManager.chartValues" . | fromJson -}}
{{- if dig "serviceAccount" "create" true $chart -}}
{{- dig "serviceAccount" "name" "" $chart | default (include "agent-platform.modelManager.fullname" .) -}}
{{- else -}}
{{- dig "serviceAccount" "name" "" $chart | default "default" -}}
{{- end -}}
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
Empty when no backend is configured statically (the default: backends are
registered at runtime, model-manager docs/backends.md).
*/}}
{{- define "agent-platform.modelManager.backend" -}}
{{- with include "agent-platform.modelManager.backends" . | fromJsonArray }}{{ index . 0 }}{{ end -}}
{{- end -}}

{{/*
The serving backends the component runs statically, as a JSON list:
model-manager.backends when set (one model-manager in front of several servers,
e.g. ollama and lemonade), else [model-manager.backend] when that is set, else
[] — the default: model-manager starts with no backend and the backends are
registered at runtime as labelled ConfigMaps in its namespace (model-manager
docs/backends.md), which no guard or policy here can see. The first is the
default backend.
*/}}
{{- define "agent-platform.modelManager.backends" -}}
{{- $chart := include "agent-platform.modelManager.chartValues" . | fromJson -}}
{{- $list := dig "backends" (list) $chart -}}
{{- $one := dig "backend" "" $chart -}}
{{- if $list }}{{ $list | toJson }}{{ else if $one }}{{ list $one | toJson }}{{ else }}[]{{ end -}}
{{- end -}}

{{/*
Truthy when the named driver is among the component's backends.
Usage: include "agent-platform.modelManager.hasBackend" (dict "root" . "name" "kserve")
*/}}
{{- define "agent-platform.modelManager.hasBackend" -}}
{{- if has .name (include "agent-platform.modelManager.backends" .root | fromJsonArray) }}true{{ end -}}
{{- end -}}

{{/*
Truthy when model-manager serves through a kserve backend: a static kserve
entry of model-manager.backends, or components.cluster-manager on, which
registers the backend at runtime (its model-backend-kserve ConfigMap, invisible
to the chart) for every cluster it gives a serving slice.
*/}}
{{- define "agent-platform.modelManager.kserveOn" -}}
{{- if or (include "agent-platform.modelManager.hasBackend" (dict "root" . "name" "kserve")) (eq (include "agent-platform.componentEnabled" (dict "root" . "name" "cluster-manager")) "true") }}true{{ end -}}
{{- end -}}

{{/*
Truthy when model-manager is on and serves models it then reads and routes:
this release's serving slice, or a kserve backend whose slice is elsewhere — a
GPU node pool brings its slice as a second release of this chart, where
model-manager is off, while this release's modelServing stays off.
*/}}
{{- define "agent-platform.modelManager.serves" -}}
{{- if and (include "agent-platform.modelManager.enabled" .) (or (include "agent-platform.modelServing.enabled" .) (include "agent-platform.modelManager.kserveOn" .)) }}true{{ end -}}
{{- end -}}

{{/*
The namespace the models model-manager serves run in: this release's serving
namespace with the slice on (model-serving-validate.yaml holds the two equal),
else model-manager's kserve.namespace (model-serving, cluster-manager's
default too).
*/}}
{{- define "agent-platform.modelManager.servingNamespace" -}}
{{- if include "agent-platform.modelServing.enabled" . }}{{ include "agent-platform.modelServing.namespace" . }}{{ else }}{{ dig "kserve" "namespace" "" (include "agent-platform.modelManager.chartValues" . | fromJson) | default "model-serving" }}{{ end -}}
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
The LM Studio base URL model-manager dials (model-manager.lmstudio.endpoint).
*/}}
{{- define "agent-platform.modelManager.lmstudioEndpoint" -}}
{{- $chart := include "agent-platform.modelManager.chartValues" . | fromJson -}}
{{- dig "lmstudio" "endpoint" "" $chart -}}
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
Every host model server among the component's backends (ollama, lemonade,
lmstudio), split for network policies, as a JSON list of
  { "backend": "<name>", "host": "<host>", "port": <int>, "isIP": bool }
in the order of the backends list. Empty when none is listed (kserve alone).

Usage: include "agent-platform.modelManager.hostTargets" (dict "root" . "key" "endpoint")

The key names which address of each backend to read, so the two policies that
need these targets share one list of host backends instead of spelling it out
in a dialect each: "endpoint" is what model-manager itself dials, "agentHost"
what it writes into the ModelConfigs for the agent pods to dial, which falls
back to the endpoint where it is unset.
*/}}
{{- define "agent-platform.modelManager.hostTargets" -}}
{{- $root := .root -}}
{{- $key := .key -}}
{{- $chart := include "agent-platform.modelManager.chartValues" $root | fromJson -}}
{{- $out := list -}}
{{- range $name := include "agent-platform.modelManager.backends" $root | fromJsonArray -}}
{{- if has $name (list "ollama" "lemonade" "lmstudio") -}}
{{- $address := dig $name $key "" $chart | default (dig $name "endpoint" "" $chart) -}}
{{- /* urlParse errors hard on some malformed values (a host:port with no
       scheme), and template render order is not ours to rely on, so an address
       the guards reject is skipped rather than parsed here: validate.yaml
       rejects the same value with a message naming the key, and it renders in
       every network-policy flavour, so a render that completes has passed it. */ -}}
{{- if regexMatch "^https?://[^/]+" $address -}}
{{- $out = append $out (merge (dict "backend" $name) (include "agent-platform.modelManager.endpointTarget" $address | fromJson)) -}}
{{- end -}}
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
