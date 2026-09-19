{{/* vim: set filetype=mustache: */}}
{{/*
Helpers of the llm-d control plane wiring (templates/kserve/): the guards and
the controller's network policy for the kserve-llmisvc-resources component
(KServe's LLMInferenceService controller), whose CRDs are the
kserve-llmisvc-crd component's and whose well-known LLMInferenceServiceConfigs
are the kserve-runtime-configs component's.
*/}}

{{/*
Truthy when the llm-d controller component is on (components.kserve-llmisvc-resources.enabled).
*/}}
{{- define "agent-platform.kserve.llmisvcEnabled" -}}
{{- include "agent-platform.optionalComponentEnabled" (dict "root" . "name" "kserve-llmisvc-resources") -}}
{{- end -}}

{{/*
The component chart's values block (a dict; empty when unset) — what the
kserve-llmisvc-resources release will see.
*/}}
{{- define "agent-platform.kserve.llmisvcValues" -}}
{{- index .Values "kserve-llmisvc-resources" | default dict | toJson -}}
{{- end -}}

{{/*
Labels of every object the wiring renders for the KServe components.
*/}}
{{- define "agent-platform.kserve.labels" -}}
{{ include "labels.common" . }}
app.kubernetes.io/component: kserve
{{- end -}}
