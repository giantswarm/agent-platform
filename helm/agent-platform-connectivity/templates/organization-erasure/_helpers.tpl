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

{{/* The operator uses the same endpoint and credential references as the snapshot store. */}}
{{- define "agent-platform.operator.snapshots" -}}
{{- $snapshots := dict -}}
{{- $substrate := .Values.substrate | default dict -}}
{{- $namespace := dig "substrate" "targetNamespace" "ate-system" .Values.components -}}
{{- if include "agent-platform.substrateStore.s3proxy" . -}}
{{- $_ := set $snapshots "endpoint" (include "agent-platform.substrateStore.s3proxyUrl" .) -}}
{{- $_ := set $snapshots "usePathStyle" true -}}
{{- $_ := set $snapshots "region" "us-east-1" -}}
{{- $_ := set $snapshots "credentials" (dict "existingSecret" (include "agent-platform.substrateStore.s3proxyName" .)) -}}
{{- else if dig "rustfs" "enabled" false $substrate -}}
{{- $_ := set $snapshots "endpoint" (printf "http://rustfs.%s.svc:9000" $namespace) -}}
{{- $_ := set $snapshots "usePathStyle" true -}}
{{- $_ := set $snapshots "region" "us-east-1" -}}
{{- $_ := set $snapshots "credentials" (dict "existingSecret" "substrate-rustfs-operator") -}}
{{- else -}}
{{- range dig "ateApiServer" "extraEnv" list $substrate -}}
{{- if eq .name "AWS_ENDPOINT_URL" -}}
{{- $_ := set $snapshots "endpoint" (required "substrate.ateApiServer.extraEnv AWS_ENDPOINT_URL needs a value" .value) -}}
{{- else if eq .name "AWS_S3_USE_PATH_STYLE" -}}
{{- $_ := set $snapshots "usePathStyle" (eq .value "true") -}}
{{- else if eq .name "AWS_REGION" -}}
{{- $_ := set $snapshots "region" .value -}}
{{- end -}}
{{- end -}}
{{- end -}}
{{- $snapshots | toJson -}}
{{- end -}}
