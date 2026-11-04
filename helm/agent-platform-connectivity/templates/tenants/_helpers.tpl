{{/* vim: set filetype=mustache: */}}
{{/*
Helpers of the agent tenants' RBAC (templates/tenants/, giantswarm/agent-platform#706).
*/}}

{{/*
The rules of agent-platform:user: see the tenant's agents, templates,
RemoteMCPServers, ModelConfigs, EgressPolicies (what its Agents may select)
and muster MCPServers; start a Session. The kagent namespace binds
agent-platform:platform-user instead, without the EgressPolicies.
*/}}
{{- define "agent-platform.tenants.userRules" -}}
{{ include "agent-platform.tenants.platformUserRules" . }}
- apiGroups: ["api.kagent.dev"]
  resources: ["egresspolicies"]
  verbs: ["get", "list", "watch"]
{{- end -}}

{{/*
The rules of agent-platform:platform-user, what every signed-in person holds
in the kagent namespace (platform-tenant.yaml): agent-platform:user's but the
EgressPolicies. The platform tenant's policies name the platform's egress
origins and the Secrets they authenticate with; its editors read them.
*/}}
{{- define "agent-platform.tenants.platformUserRules" -}}
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
performs as the caller. The agent chart's OCIRepository is read only: its
writer is the namespace's keeper (agent-platform-operator in an organization
namespace, agent-manager's tenant reconciler in a tenant namespace), never an
editor (giantswarm/agent-platform#1267).
*/}}
{{- define "agent-platform.tenants.editorRules" -}}
- apiGroups: ["helm.toolkit.fluxcd.io"]
  resources: ["helmreleases"]
  verbs: ["get", "list", "watch", "create", "update", "patch", "delete"]
- apiGroups: ["source.toolkit.fluxcd.io"]
  resources: ["ocirepositories"]
  verbs: ["get", "list", "watch"]
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
The prefix of the Secrets a tenant's credential references may name
(giantswarm/agent-platform#1040): the tenant's own credentials. Every other
Secret of a tenant namespace, the platform's copied model key among them, is
the platform's.
*/}}
{{- define "agent-platform.tenants.credentialPrefix" -}}
tenant-credential-
{{- end -}}

{{/*
The key Secret of the default ModelConfig, kagent.providers.<default>.apiKeySecretRef:
the platform Secret agent-manager's tenant reconciler copies into a tenant
namespace that holds an agent (the meta chart derives the reconciler's
tenants.modelConfig.apiKeySecretName from the same key). Empty when unset.
*/}}
{{- define "agent-platform.tenants.modelSecretName" -}}
{{- $providers := dig "providers" dict (.Values.kagent | default dict) | default dict -}}
{{- dig (toString (dig "default" "" $providers)) "apiKeySecretRef" "" $providers -}}
{{- end -}}

{{/*
The name of the default ModelConfig agent-manager's tenant reconciler copies
into a tenant namespace that holds an agent: the ModelConfig the kagent chart
renders from kagent.providers (the meta chart derives the reconciler's
tenants.modelConfig.name with the same name).
*/}}
{{- define "agent-platform.tenants.modelConfigName" -}}
default-model-config
{{- end -}}

{{/*
"true" when a tenant's own ModelConfigs are admitted under the platform's
rules (kagent on and either mode of agent-platform.orgNamespaces.enabled;
giantswarm/agent-platform#759, templates/tenants/modelconfig-admission.yaml),
else empty.
*/}}
{{- define "agent-platform.tenants.modelConfigs" -}}
{{- if and (include "agent-platform.componentEnabled" (dict "root" . "name" "kagent")) (include "agent-platform.orgNamespaces.enabled" .) }}true{{ end -}}
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

{{/*
"true" when the worker pods of the Organizations' namespaces are admitted
(tenants.organizations.enabled; giantswarm/agent-platform#1036), else empty.
*/}}
{{- define "agent-platform.tenants.organizations" -}}
{{- if dig "organizations" "enabled" false (.Values.tenants | default dict) }}true{{ end -}}
{{- end -}}

{{/*
The organizations' namespaces, by mode. Two modes select them, never both at
once (the render fails, agent-platform.organizations.enabled): the tenants
mode (tenants.organizations.enabled, agent-manager's tenant reconciler over the
Giant Swarm Organizations' org-<name> namespaces, labelled
giantswarm.io/organization) and the organizations mode
(agentPlatformOrganizations.enabled, agent-platform-operator's organization
namespaces, labelled agent-platform.giantswarm.io/organization). Every policy
that holds or admits an organization's namespace selects it by the label of
the mode that is on; with neither on, by the organizations mode's label, so
nothing selects giantswarm.io/organization outside the tenants mode.
*/}}

{{/* "true" when either mode is on, else empty. */}}
{{- define "agent-platform.orgNamespaces.enabled" -}}
{{- if or (include "agent-platform.tenants.organizations" .) (include "agent-platform.organizations.enabled" .) }}true{{ end -}}
{{- end -}}

{{/* The label of the organizations' namespaces (see above). */}}
{{- define "agent-platform.orgNamespaces.label" -}}
{{- if include "agent-platform.tenants.organizations" . -}}
giantswarm.io/organization
{{- else -}}
{{- include "agent-platform.organizations.label" . -}}
{{- end -}}
{{- end -}}

{{/* The switch of the mode that is on, for the render's messages. */}}
{{- define "agent-platform.orgNamespaces.switch" -}}
{{- if include "agent-platform.tenants.organizations" . -}}
tenants.organizations.enabled
{{- else -}}
agentPlatformOrganizations.enabled
{{- end -}}
{{- end -}}

{{/*
The organizations' namespaces but the kagent namespace (the platform tenant),
as a namespaceSelector in YAML.
*/}}
{{- define "agent-platform.orgNamespaces.selector" -}}
matchExpressions:
  - key: {{ include "agent-platform.orgNamespaces.label" . }}
    operator: Exists
  - key: kubernetes.io/metadata.name
    operator: NotIn
    values: [{{ .Values.kagent.namespaceOverride | default .Release.Namespace | quote }}]
{{- end -}}

{{/*
The worker pods of every organization namespace, as one peer of a policy
rule: a Cilium endpoint selector (Cilium carries a namespace's labels on its
pods as io.cilium.k8s.namespace.labels.<key>), or a NetworkPolicy peer. The
namespaces are selected by the label (agent-platform.orgNamespaces.label),
never listed.
Usage: include "agent-platform.tenants.organizationWorkers" (dict "root" $ "flavor" "cilium")
*/}}
{{- define "agent-platform.tenants.organizationWorkers" -}}
{{- $label := include "agent-platform.orgNamespaces.label" .root -}}
{{- if eq .flavor "cilium" -}}
- matchExpressions:
    - key: ate.dev/worker-pool
      operator: Exists
    - key: io.cilium.k8s.namespace.labels.{{ $label }}
      operator: Exists
{{- else -}}
- namespaceSelector:
    matchExpressions:
      - key: {{ $label }}
        operator: Exists
  podSelector:
    matchExpressions:
      - key: ate.dev/worker-pool
        operator: Exists
{{- end -}}
{{- end -}}

{{/*
The writers of the Substrate workers in the Organizations' namespaces
(tenants.organizations.enabled), by kind, as JSON {kind: [ServiceAccount
subject]}: Substrate's controller chain. ate-controller writes the Deployment
(and marks the busy workers' pods), the deployment controller the ReplicaSet
(and the Deployment's revision), the ReplicaSet controller the Pods. The
substrate-workers PolicyException excepts only these writers there, and the
admission policy agent-platform-tenant-workers lets no one else write a worker
there.
*/}}
{{- define "agent-platform.tenants.workerWriters" -}}
{{- $ateController := dict "kind" "ServiceAccount" "name" "ate-controller" "namespace" (include "agent-platform.substrate.namespace" .) -}}
{{- $deploymentController := dict "kind" "ServiceAccount" "name" "deployment-controller" "namespace" "kube-system" -}}
{{- $replicaSetController := dict "kind" "ServiceAccount" "name" "replicaset-controller" "namespace" "kube-system" -}}
{{- dict "Deployment" (list $ateController $deploymentController) "ReplicaSet" (list $deploymentController) "Pod" (list $replicaSetController $ateController) | toJson -}}
{{- end -}}
