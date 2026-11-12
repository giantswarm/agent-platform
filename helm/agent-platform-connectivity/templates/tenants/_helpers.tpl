{{/* vim: set filetype=mustache: */}}
{{/*
Helpers of the agent tenants' RBAC (templates/tenants/, giantswarm/agent-platform#706).
*/}}

{{/*
The rules of agent-platform:user: see the tenant's agents, templates,
RemoteMCPServers, ModelConfigs, Harnesses, EgressPolicies (what its Agents may
select) and muster MCPServers; start a Session; read the tenant's members
(giantswarm/agent-platform#1368): agent-manager's list_organization_members
lists the namespace's RoleBindings as the caller and keeps those to the three
roles. RBAC cannot narrow a list by roleRef, so a member reads every
RoleBinding of the tenant namespace: its subjects and roleRefs, no credential.
get and list only, no watch. The kagent namespace binds
agent-platform:platform-user instead, without the EgressPolicies and the
RoleBindings.
*/}}
{{- define "agent-platform.tenants.userRules" -}}
{{ include "agent-platform.tenants.platformUserRules" . }}
- apiGroups: ["api.kagent.dev"]
  resources: ["egresspolicies"]
  verbs: ["get", "list", "watch"]
- apiGroups: ["rbac.authorization.k8s.io"]
  resources: ["rolebindings"]
  verbs: ["get", "list"]
{{- end -}}

{{/*
The rules of agent-platform:platform-user, what every signed-in person holds
in the kagent namespace (platform-tenant.yaml): agent-platform:user's but the
EgressPolicies and the RoleBindings. The platform tenant's policies name the
platform's egress origins and the Secrets they authenticate with, and its
RoleBindings the platform's own identities; its editors read them.
*/}}
{{- define "agent-platform.tenants.platformUserRules" -}}
- apiGroups: ["api.kagent.dev"]
  resources: ["agents", "agenttemplates", "remotemcpservers", "modelconfigs", "harnesses"]
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
namespace), never an
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
"true" when a tenant's own ModelConfigs are admitted under the platform's
rules (kagent on and agentPlatformOrganizations.enabled;
giantswarm/agent-platform#759, templates/tenants/modelconfig-admission.yaml),
else empty.
*/}}
{{- define "agent-platform.tenants.modelConfigs" -}}
{{- if and (include "agent-platform.componentEnabled" (dict "root" . "name" "kagent")) (include "agent-platform.orgNamespaces.enabled" .) }}true{{ end -}}
{{- end -}}

{{/*
The providers an organization's ModelConfig reaches the LLM listener with, as a
JSON list: agent-platform.llmRouting.providers, plus openai when model-manager
serves (agent-platform.modelManager.serves). Its models attach to the listener
as AgentgatewayModels and are called in the OpenAI format, at the listener's
/v1. The extra openai is the organizations' only: in the kagent namespace an
OpenAI ModelConfig without a baseUrl keeps calling its provider directly, as
the listener has no route to the OpenAI API unless llmRouting.models names it.
Usage: include "agent-platform.tenants.providers" . | fromJsonArray
*/}}
{{- define "agent-platform.tenants.providers" -}}
{{- $out := include "agent-platform.llmRouting.providers" . | fromJsonArray -}}
{{- if and (include "agent-platform.modelManager.serves" .) (not (has "openai" $out)) -}}
{{- $out = append $out "openai" -}}
{{- end -}}
{{- toJson $out -}}
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
The subjects of muster's MCP-server viewers (muster-mcpservers.yaml), as a
YAML list, each subject once: musterMcpServerViewers, or
tenants.platform.users while musterMcpServerViewers is unset (null), so an
installation's viewers stay as they are until it sets the key. An empty list
binds nobody. giantswarm/agent-platform#1331: the platform tenant's
tenants.platform.* can then go without muster's viewers. The meta chart's
guard (agent-platform.muster.organizations) resolves the viewers the same way:
change both together, and drop the fallback in both when tenants.platform.*
is retired (giantswarm/giantswarm#38990, giantswarm/agent-manager#166).
*/}}
{{- define "agent-platform.muster.mcpServerViewers" -}}
{{- $viewers := .Values.musterMcpServerViewers -}}
{{- if kindIs "invalid" $viewers -}}
{{- $viewers = dig "platform" "users" list (.Values.tenants | default dict) -}}
{{- end -}}
{{- include "agent-platform.tenants.subjects" (list $viewers) -}}
{{- end -}}

{{/*
The organizations' namespaces: agent-platform-operator's organization
namespaces, labelled agent-platform.giantswarm.io/organization, while
agentPlatformOrganizations.enabled is on. Every policy that holds or admits an
organization's namespace selects it by that label, never by a list of names.
*/}}

{{/* "true" while agentPlatformOrganizations.enabled is on, else empty. The
policies of the organizations' namespaces (substrate/, tenants/, llm/) name
these two helpers, so the namespaces they hold have one definition here. */}}
{{- define "agent-platform.orgNamespaces.enabled" -}}
{{- include "agent-platform.organizations.enabled" . -}}
{{- end -}}

{{/* The label of the organizations' namespaces. */}}
{{- define "agent-platform.orgNamespaces.label" -}}
{{- include "agent-platform.organizations.label" . -}}
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
The writers of the Substrate workers in the organizations' namespaces
(agentPlatformOrganizations.enabled), by kind, as JSON {kind: [ServiceAccount
subject]}: Substrate's controller chain. ate-controller writes the Deployment
(and marks the busy workers' pods), the deployment controller the ReplicaSet
(and the Deployment's revision), the ReplicaSet controller the Pods. The
substrate-workers PolicyException excepts only these writers there, and the
admission policy agent-platform-organization-workers lets no one else write a worker
there.
*/}}
{{- define "agent-platform.tenants.workerWriters" -}}
{{- $ateController := dict "kind" "ServiceAccount" "name" "ate-controller" "namespace" (include "agent-platform.substrate.namespace" .) -}}
{{- $deploymentController := dict "kind" "ServiceAccount" "name" "deployment-controller" "namespace" "kube-system" -}}
{{- $replicaSetController := dict "kind" "ServiceAccount" "name" "replicaset-controller" "namespace" "kube-system" -}}
{{- dict "Deployment" (list $ateController $deploymentController) "ReplicaSet" (list $deploymentController) "Pod" (list $replicaSetController $ateController) | toJson -}}
{{- end -}}

{{/*
The HelmRelease spec fields an agent release never sets, as a CEL list
literal: through them helm-controller acts as itself (spec.chart's
HelmChart, valuesFrom, postRenderers, kubeConfig) or moves the release out
of its namespace (targetNamespace, storageNamespace). Held in the kagent
namespace (platform-helmreleases.yaml) and in the organization namespaces
(organizations/admission.yaml).
*/}}
{{- define "agent-platform.helmReleases.forbiddenFields" -}}
['chart', 'valuesFrom', 'postRenderers', 'kubeConfig', 'targetNamespace', 'storageNamespace']
{{- end -}}
