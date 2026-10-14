{{/* vim: set filetype=mustache: */}}
{{/*
Helpers of the agent tenants' RBAC (templates/tenants/, giantswarm/agent-platform#706).
*/}}

{{/*
The rules of agent-platform:user: see the tenant's agents, templates,
RemoteMCPServers, ModelConfigs and muster MCPServers; start a Session.
*/}}
{{- define "agent-platform.tenants.userRules" -}}
- apiGroups: ["api.kagent.dev"]
  resources: ["agents", "agenttemplates", "remotemcpservers", "modelconfigs"]
  verbs: ["get", "list", "watch"]
- apiGroups: ["muster.giantswarm.io"]
  resources: ["mcpservers"]
  verbs: ["get", "list", "watch"]
- apiGroups: ["api.kagent.dev"]
  resources: ["sessions"]
  verbs: ["create"]
{{- end -}}

{{/*
The rules agent-platform:editor adds: the writes and reads agent-manager
performs as the caller.
*/}}
{{- define "agent-platform.tenants.editorRules" -}}
- apiGroups: ["helm.toolkit.fluxcd.io"]
  resources: ["helmreleases"]
  verbs: ["get", "list", "watch", "create", "update", "patch", "delete"]
- apiGroups: ["source.toolkit.fluxcd.io"]
  resources: ["ocirepositories"]
  verbs: ["get", "list", "watch", "create", "update", "patch", "delete"]
- apiGroups: ["api.kagent.dev"]
  resources: ["agents", "agenttemplates", "remotemcpservers"]
  verbs: ["create", "update", "patch", "delete"]
- apiGroups: ["api.kagent.dev"]
  resources: ["harnesses"]
  verbs: ["get", "list", "watch"]
- apiGroups: [""]
  resources: ["events"]
  verbs: ["get", "list", "watch"]
{{- end -}}

{{/*
The subjects of a binding, as a YAML list: the lists given, each subject once.
Usage: include "agent-platform.tenants.subjects" (list .Values.tenants.platform.editors .Values.tenants.platformTeam)
*/}}
{{- define "agent-platform.tenants.subjects" -}}
{{- $subjects := list -}}
{{- range . -}}{{- range . -}}{{- if not (has . $subjects) -}}{{- $subjects = append $subjects . -}}{{- end -}}{{- end -}}{{- end -}}
{{- with $subjects }}{{ toYaml . }}{{ end -}}
{{- end -}}
