{{/* vim: set filetype=mustache: */}}
{{/*
Helpers of the erasure's access (templates/organization-erasure/,
giantswarm/giantswarm#38988), which renders with agentPlatformOrganizations.enabled.
*/}}

{{/*
The operator's IRSA role: the snapshot store's role name and -operator. IAM
takes 64 characters; a longer name fails the render rather than be cut, which
could give it Substrate's role's name or one the operator's chart is not told.
*/}}
{{- define "agent-platform.organizationErasure.awsRoleName" -}}
{{- $name := printf "%s-operator" (include "agent-platform.substrateStore.awsRoleName" .) -}}
{{- if gt (len $name) 64 -}}
{{- fail (printf "agentPlatformOrganizations: the operator's IAM role %s is longer than 64 characters: set a shorter kagent.harness.snapshotStore.crossplane.aws.roleName (at most 55)" $name) -}}
{{- end -}}
{{- $name -}}
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
