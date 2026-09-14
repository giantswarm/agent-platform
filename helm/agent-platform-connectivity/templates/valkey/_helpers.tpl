{{/* vim: set filetype=mustache: */}}
{{/*
Helpers of the valkey component's wiring (templates/valkey/): the muster-valkey
budget (giantswarm/agent-platform#439). The values block is `valkey` — the
wrapper chart's values as the meta chart forwards them (valkey.valkey.* is the
upstream subchart's block) plus valkey.podDisruptionBudget, which is this
chart's alone (components.valkey.omitKeys keeps it off the valkey release).
*/}}

{{/*
Truthy when the valkey component is on (components.valkey.enabled).
*/}}
{{- define "agent-platform.valkey.enabled" -}}
{{- include "agent-platform.componentEnabled" (dict "root" . "name" "valkey") -}}
{{- end -}}

{{/*
The valkey component's Helm release name — the value of the pods'
app.kubernetes.io/instance label the upstream subchart stamps
(valkey.selectorLabels). The meta chart names every component's release after
its roster key (components.yaml: `releaseName: <key>`; the valkey entry's key,
chart and values block are all `valkey`), and this chart is only ever
installed by that meta chart — never .Release.Name here (see
agent-platform.kagent.releaseName for why).
*/}}
{{- define "agent-platform.valkey.releaseName" -}}
valkey
{{- end -}}

{{/*
The muster-valkey Deployment's name. Single source of truth: the umbrella pins
valkey.valkey.fullnameOverride (values.yaml), which the subchart uses verbatim
for its Deployment and Service (muster's storage URL names it) — a
misconfiguration fails the render instead of leaving a budget on nothing.
*/}}
{{- define "agent-platform.valkey.fullname" -}}
{{- required "valkey.valkey.fullnameOverride must be set — muster's storage URL and this chart's budget target this exact name" (dig "valkey" "fullnameOverride" "" .Values.valkey) -}}
{{- end -}}

{{/*
Labels of every object the umbrella renders for the component.
*/}}
{{- define "agent-platform.valkey.labels" -}}
{{ include "labels.common" . }}
app.kubernetes.io/component: valkey
{{- end -}}

{{/*
The selector labels of the muster-valkey pod, as the upstream subchart stamps
them (valkey.selectorLabels: the chart name and the release name). Rendered
as YAML mapping entries; the caller provides the indentation.
*/}}
{{- define "agent-platform.valkey.podSelector" -}}
app.kubernetes.io/name: valkey
app.kubernetes.io/instance: {{ include "agent-platform.valkey.releaseName" . }}
{{- end -}}
