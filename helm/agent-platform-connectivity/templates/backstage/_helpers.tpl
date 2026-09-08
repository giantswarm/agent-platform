{{/* vim: set filetype=mustache: */}}
{{/*
Helpers of the Backstage wiring (templates/backstage/): the portal's Agent
Platform app-config, its public route and the config-reload hook. Two blocks
feed them: the backstage chart's own values (backstage.name, .port,
.database.engine — what the Backstage release will see) and the wiring keys
the standalone umbrella kept under components.backstage (hostname, parentRefs,
installationName, extraScopes, ...), which live in the same `backstage:` block
here and are dropped from the values forwarded to the backstage chart by the
meta chart (components.backstage.omitKeys) — the kagent shape.
*/}}

{{/*
Truthy when the Backstage component is on (components.backstage.enabled).
*/}}
{{- define "agent-platform.backstage.enabled" -}}
{{- include "agent-platform.componentEnabled" (dict "root" . "name" "backstage") -}}
{{- end -}}

{{/*
The backstage values block (a dict; empty when unset).
*/}}
{{- define "agent-platform.backstage.values" -}}
{{- .Values.backstage | default dict | toJson -}}
{{- end -}}

{{/*
The Backstage Deployment / Service name: the backstage chart's `name`
(default backstage).
*/}}
{{- define "agent-platform.backstage.name" -}}
{{- $b := include "agent-platform.backstage.values" . | fromJson -}}
{{- dig "name" "backstage" $b -}}
{{- end -}}

{{/*
The installation the portal shows for this platform: backstage.installationName,
else the release name. It keys gs.installations, the in-cluster Kubernetes
cluster entry, the muster installation and the kagent / model-manager
installations in the app-config, so the portal's pages agree on one name.
*/}}
{{- define "agent-platform.backstage.installationName" -}}
{{- $b := include "agent-platform.backstage.values" . | fromJson -}}
{{- dig "installationName" "" $b | default .Release.Name -}}
{{- end -}}

{{/*
The public hostname of the portal: backstage.hostname, else backstage.<global.domain>.
*/}}
{{- define "agent-platform.backstage.hostname" -}}
{{- $b := include "agent-platform.backstage.values" . | fromJson -}}
{{- include "agent-platform.hostname" (dict "ctx" . "prefix" "backstage" "override" (dig "hostname" "" $b) "key" "backstage.hostname") -}}
{{- end -}}

{{/*
Truthy when the config-reload hook renders (backstage.configReload.enabled,
default true) while the component is on.
*/}}
{{- define "agent-platform.backstage.configReload" -}}
{{- $b := include "agent-platform.backstage.values" . | fromJson -}}
{{- if and (include "agent-platform.backstage.enabled" .) (dig "configReload" "enabled" true $b) -}}true{{- end -}}
{{- end -}}

{{/*
Labels of every object the wiring renders for the component.
*/}}
{{- define "agent-platform.backstage.labels" -}}
{{ include "labels.common" . }}
app.kubernetes.io/component: backstage
{{- end -}}
