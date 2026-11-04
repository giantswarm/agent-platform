{{/* vim: set filetype=mustache: */}}
{{/*
Helpers of the erasure's access (templates/organization-erasure/,
giantswarm/giantswarm#38988).
*/}}

{{/* agent-platform-operator's ServiceAccount, as JSON {name, namespace}. */}}
{{- define "agent-platform.organizationErasure.serviceAccount" -}}
{{- $sa := dig "operatorServiceAccount" dict (.Values.organizationErasure | default dict) -}}
{{- dict "name" (required "organizationErasure.operatorServiceAccount.name must be set" $sa.name) "namespace" ($sa.namespace | default .Release.Namespace) | toJson -}}
{{- end -}}

{{/* The operator's IRSA role: the snapshot store's role name and -operator, at most 64 characters. */}}
{{- define "agent-platform.organizationErasure.awsRoleName" -}}
{{- printf "%s-operator" (include "agent-platform.substrateStore.awsRoleName" .) | trunc 64 | trimSuffix "-" -}}
{{- end -}}

{{/*
The first segment of an organization's snapshot keys: its AgentPlatformOrganization's
UID (36 characters, 8-4-4-4-12), as an IAM wildcard. `?` is one character, so
the pattern matches a UID and no shorter or longer segment: the platform
Harness's own prefix (kagent.harness.snapshotStore.prefix) never matches.
*/}}
{{- define "agent-platform.organizationErasure.uidPattern" -}}
????????-????-????-????-????????????
{{- end -}}
