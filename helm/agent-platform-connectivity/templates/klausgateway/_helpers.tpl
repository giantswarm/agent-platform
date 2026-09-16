{{/* vim: set filetype=mustache: */}}
{{/*
Helpers of the klaus-gateway component's wiring (templates/klausgateway/).

One values block feeds these templates: klausGateway — the klaus-gateway
chart's own values as the meta chart forwards them (the routing store, Slack,
OBO and A2A knobs) plus the umbrella's wiring keys (agentgatewayRoute). A
component release's values cannot be derived at render time, so the wiring
reads what the chart will see; a knob the block does not declare is read with
the klaus-gateway chart's own default. The Valkey store's
target is the platform's own Valkey (components.valkey, the valkey block), the
way the meta chart fills routing.valkey from it.
*/}}

{{/*
The klaus-gateway pod's app.kubernetes.io/name label value: the chart's
fullnameOverride, else its chart name. Every policy selecting the gateway pod
uses this, so a renamed release moves them all.
*/}}
{{- define "agent-platform.klausGateway.fullname" -}}
{{- .Values.klausGateway.fullnameOverride | default "klaus-gateway" -}}
{{- end -}}

{{/*
Truthy (emits "true") when the gateway pod reads or writes the Kubernetes API,
so its egress needs the kube-apiserver: the Secret link store
(klausGateway.obo.store: secret with OBO on — the gateway's own gate,
klaus-gateway's obo.secretStore helper) only. The routing stores that reached
the API (configmap, crd) and the embedded ChannelRoute controller are gone from
klaus-gateway (giantswarm/klaus-gateway#271). Empty otherwise: the default
shape — memory routing, the bolt link store — never touches the API and gets
no rule. The store keys are the klaus-gateway chart's; when the block leaves
one unset its chart default applies (bolt, memory).
*/}}
{{- define "agent-platform.klausGateway.apiServerEgress" -}}
{{- $kg := .Values.klausGateway -}}
{{- if and (dig "obo" "enabled" false $kg) (eq (dig "obo" "store" "bolt" $kg) "secret") -}}true{{- end -}}
{{- end -}}

{{/*
Truthy (emits "true") when the gateway pod keeps its routes in the platform's
Valkey: klausGateway.routing.store: valkey with the valkey component on
(components.valkey.enabled — the pods the rule selects). Empty otherwise; an
out-of-band Valkey (routing.valkey.url set, the component off) is not in this
namespace and gets no rule here — its egress is the installation's to add.
*/}}
{{- define "agent-platform.klausGateway.valkeyEgress" -}}
{{- if and (eq (dig "routing" "store" "memory" .Values.klausGateway) "valkey") (include "agent-platform.valkey.enabled" .) -}}true{{- end -}}
{{- end -}}

{{/*
The port the platform's Valkey Service listens on (valkey.valkey.service.port,
default 6379) — the one the meta chart puts into the gateway's routing.valkey.url.
*/}}
{{- define "agent-platform.klausGateway.valkeyPort" -}}
{{- dig "valkey" "service" "port" 6379 (.Values.valkey | default dict) -}}
{{- end -}}

{{/*
Truthy (emits "true") when the gateway pod reaches any store beyond its own
process — the gate of the -klausgateway-store-egress policy: the Valkey
routing store or anything on the Kubernetes API.
*/}}
{{- define "agent-platform.klausGateway.storeEgress" -}}
{{- if or (include "agent-platform.klausGateway.valkeyEgress" .) (include "agent-platform.klausGateway.apiServerEgress" .) -}}true{{- end -}}
{{- end -}}
