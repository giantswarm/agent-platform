{{/* vim: set filetype=mustache: */}}
{{/*
Helpers of the Agent Platform organizations' role and admission policies
(templates/organizations/, giantswarm/agent-platform#1267). The names are the
agent-platform-operator's (giantswarm/agent-platform-operator, api/v1alpha1
and internal/controller): the labels it puts on an organization namespace,
the label of what it writes there, and the generateName prefix.
*/}}

{{/*
"true" when the organizations' role and policies render
(agentPlatformOrganizations.enabled), else empty. They need kagent: the
organizations-admin role binds kagent's three tenant roles, and the
HelmRelease lock names kagent.fluxServiceAccountName.
*/}}
{{- define "agent-platform.organizations.enabled" -}}
{{- if dig "enabled" false (.Values.agentPlatformOrganizations | default dict) -}}
{{- if not (include "agent-platform.componentEnabled" (dict "root" . "name" "kagent")) -}}
{{- fail "agentPlatformOrganizations.enabled needs kagent (components.kagent.enabled): the organizations-admin role binds agent-platform:user, editor and admin, which render with kagent" -}}
{{- end -}}
true
{{- end -}}
{{- end -}}

{{/* The label that marks an organization namespace: the AgentPlatformOrganization's name. */}}
{{- define "agent-platform.organizations.label" -}}
agent-platform.giantswarm.io/organization
{{- end -}}

{{/* The three labels of an organization namespace, as a JSON list. */}}
{{- define "agent-platform.organizations.labels" -}}
{{- list "agent-platform.giantswarm.io/organization" "agent-platform.giantswarm.io/parent" "agent-platform.giantswarm.io/organization-uid" | toJson -}}
{{- end -}}

{{/*
The label of the objects the operator keeps for one organization outside its
namespace (the Kustomization and Flux ServiceAccount in fluxNamespace): the
organization's UID, the label it finds the namespace by.
*/}}
{{- define "agent-platform.organizations.uidLabel" -}}
agent-platform.giantswarm.io/organization-uid
{{- end -}}

{{/* The label of what the operator writes in an organization namespace (its copies and mirrors). */}}
{{- define "agent-platform.organizations.objectLabel" -}}
agent-platform.giantswarm.io/organization-object
{{- end -}}

{{/* The annotation that names the object a copy was made from. */}}
{{- define "agent-platform.organizations.copiedFromAnnotation" -}}
agent-platform.giantswarm.io/copied-from
{{- end -}}

{{/* The operator's ServiceAccount, as a username. */}}
{{- define "agent-platform.organizations.operatorUser" -}}
{{- $sa := dig "operator" "serviceAccount" dict (.Values.agentPlatformOrganizations | default dict) -}}
{{- printf "system:serviceaccount:%s:%s" ($sa.namespace | default .Release.Namespace) (required "agentPlatformOrganizations.operator.serviceAccount.name must be set" $sa.name) -}}
{{- end -}}

{{/*
The agent chart's OCIRepository in an organization namespace: the last
segment of agent-manager.agentChart.ociUrl, the name the operator and
agent-manager both give it.
*/}}
{{- define "agent-platform.organizations.chartSource" -}}
{{- $chart := include "agent-platform.agentManager.chartValues" . | fromJson -}}
{{- base (dig "agentChart" "ociUrl" "oci://gsoci.azurecr.io/charts/giantswarm/agent" $chart) -}}
{{- end -}}

{{/*
The namespace selector of the organization namespaces, as YAML: the label,
never a list of names.
*/}}
{{- define "agent-platform.organizations.namespaces" -}}
matchExpressions:
  - key: {{ include "agent-platform.organizations.label" . }}
    operator: Exists
{{- end -}}
