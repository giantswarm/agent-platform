{{/* vim: set filetype=mustache: */}}
{{/*
Helpers of the per-installation cut-over Job (templates/kagent/migrate-*.yaml):
`agent-manager migrate` run once per installation as the tenant identity of the
agents' HelmReleases (giantswarm/agent-platform#346, bumblebee-plans#51 D11).
*/}}

{{/*
Truthy when the migrate Job renders: kagent and agent-manager on, and
agentManager.migration.enabled (the escape hatch, default true).
*/}}
{{- define "agent-platform.kagent.migration" -}}
{{- if and (include "agent-platform.componentEnabled" (dict "root" . "name" "kagent")) (include "agent-platform.agentManager.enabled" .) .Values.agentManager.migration.enabled -}}true{{- end -}}
{{- end -}}

{{/*
The name every object of the migration shares (the network policy, the Role per
GitOps namespace; the CRD ClusterRole and its binding add -crds; the Job adds
the 8-hex hash of its spec).
*/}}
{{- define "agent-platform.kagent.migration.name" -}}
{{- printf "%s-agent-manager-migrate" (include "name" .) -}}
{{- end -}}

{{/*
The labels of the migration's objects; the component label is the pod
selector of its network policy.
*/}}
{{- define "agent-platform.kagent.migration.labels" -}}
{{ include "labels.common" . }}
app.kubernetes.io/component: agent-manager-migrate
{{- end -}}

{{/*
The labels of the Job's pod template: the stable subset only — never
helm.sh/chart or app.kubernetes.io/version, which change on every chart
release while the Job's name (a hash of its image, args and environment) does
not. Job.spec.template is immutable: a chart release that re-applied the
completed Job with the new chart version in its pod template was refused by
the apiserver (`spec.template: Invalid value: … field is immutable`), and the
connectivity upgrade stalled until the Job's TTL removed it
(giantswarm/agent-platform#399). The component label keeps the pods under the
migration's network policy.
*/}}
{{- define "agent-platform.kagent.migration.podLabels" -}}
{{ include "labels.selector" . }}
app.kubernetes.io/component: agent-manager-migrate
application.giantswarm.io/team: {{ index .Chart.Annotations "io.giantswarm.application.team" | quote }}
{{- end -}}

{{/*
The Job's pod template, rendered once: migrate-job.yaml hashes it into the
Job's name (the name follows everything Job.spec.template makes immutable, so
a changed template renders a new Job and an unchanged one is re-applied as is)
and emits it under spec.template. Takes a dict: root (the chart context),
serviceAccountName (the tenant identity), image, args (list), env (list).
*/}}
{{- define "agent-platform.kagent.migration.podTemplate" -}}
metadata:
  labels:
    {{- include "agent-platform.kagent.migration.podLabels" .root | nindent 4 }}
spec:
  restartPolicy: Never
  serviceAccountName: {{ .serviceAccountName }}
  securityContext:
    runAsNonRoot: true
    runAsUser: 1000
    runAsGroup: 1000
    seccompProfile:
      type: RuntimeDefault
  {{- with .root.Values.global.imagePullSecrets }}
  imagePullSecrets:
    {{- range . }}
    - name: {{ if kindIs "map" . }}{{ .name }}{{ else }}{{ . }}{{ end }}
    {{- end }}
  {{- end }}
  containers:
    - name: migrate
      image: {{ .image | quote }}
      args:
        {{- toYaml .args | nindent 8 }}
      env:
        {{- toYaml .env | nindent 8 }}
      securityContext:
        allowPrivilegeEscalation: false
        readOnlyRootFilesystem: true
        capabilities:
          drop: ["ALL"]
      resources:
        requests:
          cpu: 50m
          memory: 64Mi
        limits:
          memory: 256Mi
      volumeMounts:
        - name: tmp
          mountPath: /tmp
  volumes:
    - name: tmp
      emptyDir: {}
{{- end -}}

{{/*
agent-manager's image for the Job (agentManager.migration.image; the tag is the
meta chart's BOM pin for the agent-manager component).
*/}}
{{- define "agent-platform.kagent.migration.image" -}}
{{- $i := .Values.agentManager.migration.image -}}
{{- printf "%s/%s:%s" $i.registry $i.repository $i.tag -}}
{{- end -}}
