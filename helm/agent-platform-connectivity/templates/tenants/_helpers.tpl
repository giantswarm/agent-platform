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
The worker pods of every namespace labelled giantswarm.io/organization, as one
peer of a policy rule: a Cilium endpoint selector (Cilium carries a
namespace's labels on its pods as io.cilium.k8s.namespace.labels.<key>), or a
NetworkPolicy peer. The namespaces are selected by the label, never listed.
Usage: include "agent-platform.tenants.organizationWorkers" "cilium"
*/}}
{{- define "agent-platform.tenants.organizationWorkers" -}}
{{- if eq . "cilium" -}}
- matchExpressions:
    - key: ate.dev/worker-pool
      operator: Exists
    - key: io.cilium.k8s.namespace.labels.giantswarm.io/organization
      operator: Exists
{{- else -}}
- namespaceSelector:
    matchExpressions:
      - key: giantswarm.io/organization
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
