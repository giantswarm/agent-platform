{{/* vim: set filetype=mustache: */}}
{{/*
Helpers of the erasure's access (templates/organization-erasure/,
giantswarm/giantswarm#38988), which renders with agentPlatformOrganizations.enabled.
*/}}

{{/*
The operator's IRSA role, with the Crossplane AWS snapshot store: the name the
meta chart derives (agentPlatformOrganizations.operator.awsRoleName, the store's
role name and -operator) and names on the operator's ServiceAccount.
*/}}
{{- define "agent-platform.organizationErasure.awsRoleName" -}}
{{- required "agentPlatformOrganizations.operator.awsRoleName is required with agentPlatformOrganizations.enabled and the Crossplane AWS snapshot store: the meta chart derives it (agent-platform.organizations.operator), the IAM role it names on agent-platform-operator's ServiceAccount" (include "agent-platform.organizations.operator" . | fromJson).awsRoleName -}}
{{- end -}}

{{/*
The Secret of RustFS's keys the operator reads its snapshots with, with RustFS
(substrate.rustfs.enabled): the name the meta chart derives
(agentPlatformOrganizations.operator.snapshotSecret) and gives the operator as
snapshots.credentials.existingSecret.
*/}}
{{- define "agent-platform.organizationErasure.snapshotSecret" -}}
{{- required "agentPlatformOrganizations.operator.snapshotSecret is required with agentPlatformOrganizations.enabled and RustFS: the meta chart derives it (agent-platform.organizations.operator), the Secret it names in agent-platform-operator's snapshots.credentials.existingSecret" (include "agent-platform.organizations.operator" . | fromJson).snapshotSecret -}}
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
