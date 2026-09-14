{{/* vim: set filetype=mustache: */}}
{{/*
Helpers of the vm-manager component's wiring (templates/vm-manager/).

Two values blocks feed these templates: vmManager (the umbrella wiring —
disruption budget, network policy inputs, guards) and vm-manager (the component
chart's own values: OAuth, the muster registration, Service name and port;
hyphenated, so reached through index), read here the way the model-manager and
agent-manager templates read theirs — a component release's values cannot be
derived at render time, so the wiring reads what the chart will see.
*/}}

{{/*
Truthy when the vm-manager component is on (components.vm-manager.enabled).
*/}}
{{- define "agent-platform.vmManager.enabled" -}}
{{- include "agent-platform.componentEnabled" (dict "root" . "name" "vm-manager") -}}
{{- end -}}

{{/*
The component chart's values block, vm-manager (a dict; empty when unset).
*/}}
{{- define "agent-platform.vmManager.chartValues" -}}
{{- index .Values "vm-manager" | default dict | toJson -}}
{{- end -}}

{{/*
The vm-manager Service name. Single source of truth: the umbrella pins
vm-manager.fullnameOverride (values.yaml), which the component chart uses
verbatim for its Service, and the network policies and the budget target
exactly that name — a misconfiguration fails the render instead of a silent
503.
*/}}
{{- define "agent-platform.vmManager.fullname" -}}
{{- $chart := include "agent-platform.vmManager.chartValues" . | fromJson -}}
{{- required "vm-manager.fullnameOverride must be set — the umbrella's network policies target this exact Service name" (dig "fullnameOverride" "" $chart) -}}
{{- end -}}

{{/*
The port the vm-manager Service listens on (vm-manager.service.port, default 8080).
*/}}
{{- define "agent-platform.vmManager.servicePort" -}}
{{- $chart := include "agent-platform.vmManager.chartValues" . | fromJson -}}
{{- dig "service" "port" 8080 $chart -}}
{{- end -}}

{{/*
Truthy when the component validates the caller's identity itself
(vm-manager.oauth.enabled): the network policies then admit egress to the
identity provider.
*/}}
{{- define "agent-platform.vmManager.oauthEnabled" -}}
{{- $chart := include "agent-platform.vmManager.chartValues" . | fromJson -}}
{{- if dig "oauth" "enabled" false $chart }}true{{ end -}}
{{- end -}}

{{/*
The identity provider the component validates tokens with
(vm-manager.oauth.provider): dex (the default) or google.
*/}}
{{- define "agent-platform.vmManager.oauthProvider" -}}
{{- $chart := include "agent-platform.vmManager.chartValues" . | fromJson -}}
{{- dig "oauth" "provider" "dex" $chart -}}
{{- end -}}

{{/*
The issuer URL the component validates tokens against: the dex provider's
vm-manager.oauth.dex.issuerURL, else global.identity.issuerUrl (the chart's own
fallback). Empty for the google provider and when neither is set.
*/}}
{{- define "agent-platform.vmManager.issuerUrl" -}}
{{- $chart := include "agent-platform.vmManager.chartValues" . | fromJson -}}
{{- if eq (include "agent-platform.vmManager.oauthProvider" .) "dex" -}}
{{- dig "oauth" "dex" "issuerURL" "" $chart | default .Values.global.identity.issuerUrl -}}
{{- end -}}
{{- end -}}

{{/*
Labels of every object the umbrella renders for the component.
*/}}
{{- define "agent-platform.vmManager.labels" -}}
{{ include "labels.common" . }}
app.kubernetes.io/component: vm-manager
{{- end -}}

{{/*
The selector labels of the vm-manager pod, as the component chart stamps them
(app.kubernetes.io/name from its chart name or nameOverride). The component
runs as its own release, so it is selected by name only, not by a
release-scoped instance label — like the muster policies.
Rendered as YAML mapping entries; the caller provides the indentation.
*/}}
{{- define "agent-platform.vmManager.podSelector" -}}
{{- $chart := include "agent-platform.vmManager.chartValues" . | fromJson -}}
app.kubernetes.io/name: {{ dig "nameOverride" "" $chart | default "vm-manager" }}
{{- end -}}
