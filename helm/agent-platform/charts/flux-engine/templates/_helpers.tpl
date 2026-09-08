{{/* vim: set filetype=mustache: */}}
{{/*
Labels on every object of the engine. The subchart's own name identifies the
engine's objects; the release name ties them to the platform release. None of
them carries app.kubernetes.io/component=helm-controller — that label marks a
Flux distribution's helm-controller Deployment and is what the meta chart's
render guard looks for when it decides whether a cluster already runs Flux.
*/}}
{{- define "flux-engine.labels" -}}
app.kubernetes.io/name: {{ .Chart.Name | quote }}
app.kubernetes.io/instance: {{ .Release.Name | quote }}
{{ include "flux-engine.labels.common" . }}
{{- end -}}

{{/*
The labels that do not identify an object: shared by the engine's objects and
by the operator pods, whose name/instance pair is the selector below.
*/}}
{{- define "flux-engine.labels.common" -}}
app.kubernetes.io/managed-by: {{ .Release.Service | quote }}
app.kubernetes.io/part-of: agent-platform
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
application.giantswarm.io/team: {{ index .Chart.Annotations "io.giantswarm.application.team" | quote }}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimAll "-._" | quote }}
{{- end -}}

{{/*
Selector labels of the operator Deployment (immutable once installed).
*/}}
{{- define "flux-engine.operator.selectorLabels" -}}
app.kubernetes.io/name: flux-operator
app.kubernetes.io/instance: {{ .Release.Name | quote }}
{{- end -}}

{{/*
The operator's image reference.
*/}}
{{- define "flux-engine.operator.image" -}}
{{- $i := .Values.operator.image -}}
{{- printf "%s/%s:%s" $i.registry $i.repository $i.tag -}}
{{- end -}}

{{/*
The tenant identity the platform's HelmReleases run under. Fixed: the meta
chart names it on every platform HelmRelease whenever this engine is on, and
the operator's teardown hooks and the self-management of the meta chart build
on the same name.
*/}}
{{- define "flux-engine.tenantServiceAccountName" -}}
agent-platform-flux
{{- end -}}
