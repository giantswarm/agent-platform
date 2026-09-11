{{/* vim: set filetype=mustache: */}}
{{/*
Helpers of the per-installation cut-over Job (templates/kagent/migrate-*.yaml):
`agent-manager migrate` run once per installation as the tenant identity of the
agents' HelmReleases (giantswarm/agent-platform#346, bumblebee-plans#51 D11).
*/}}

{{/*
Truthy when the migrate Job renders: kagent and agent-manager on, and
agentManager.migration.enabled (the escape hatch, default true).
*/}}
{{- define "agent-platform.kagent.migration" -}}
{{- if and (include "agent-platform.componentEnabled" (dict "root" . "name" "kagent")) (include "agent-platform.agentManager.enabled" .) .Values.agentManager.migration.enabled -}}true{{- end -}}
{{- end -}}

{{/*
The name every object of the migration shares (the network policy, the Role per
GitOps namespace; the CRD ClusterRole and its binding add -crds; the Job adds
the 8-hex hash of its spec).
*/}}
{{- define "agent-platform.kagent.migration.name" -}}
{{- printf "%s-agent-manager-migrate" (include "name" .) -}}
{{- end -}}

{{/*
The labels of the migration's objects; the component label is the pod
selector of its network policy.
*/}}
{{- define "agent-platform.kagent.migration.labels" -}}
{{ include "labels.common" . }}
app.kubernetes.io/component: agent-manager-migrate
{{- end -}}

{{/*
agent-manager's image for the Job (agentManager.migration.image; the tag is the
meta chart's BOM pin for the agent-manager component).
*/}}
{{- define "agent-platform.kagent.migration.image" -}}
{{- $i := .Values.agentManager.migration.image -}}
{{- printf "%s/%s:%s" $i.registry $i.repository $i.tag -}}
{{- end -}}
