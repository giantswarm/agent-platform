{{/* vim: set filetype=mustache: */}}
{{/*
Helpers of the KServe control plane wiring (templates/kserve/): the guards and
the controllers' network policies for the kserve-resources and
kserve-llmisvc-resources components. The standalone umbrella switched both
controllers (and the CRDs) with one components.kserve toggle; here each is a
component of its own (components.kserve-crd, kserve-resources,
kserve-llmisvc-crd, kserve-llmisvc-resources) and the wiring gates on those.
*/}}

{{/*
Truthy when the KServe controller component is on (components.kserve-resources.enabled).
*/}}
{{- define "agent-platform.kserve.enabled" -}}
{{- include "agent-platform.optionalComponentEnabled" (dict "root" . "name" "kserve-resources") -}}
{{- end -}}

{{/*
Truthy when the llm-d controller component is on (components.kserve-llmisvc-resources.enabled).
*/}}
{{- define "agent-platform.kserve.llmisvcEnabled" -}}
{{- include "agent-platform.optionalComponentEnabled" (dict "root" . "name" "kserve-llmisvc-resources") -}}
{{- end -}}

{{/*
The component charts' values blocks (dicts; empty when unset) — what the
kserve-resources / kserve-llmisvc-resources releases will see.
*/}}
{{- define "agent-platform.kserve.resourcesValues" -}}
{{- index .Values "kserve-resources" | default dict | toJson -}}
{{- end -}}
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
