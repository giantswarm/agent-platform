{{/* vim: set filetype=mustache: */}}
{{/*
Helpers of the per-installation cut-over Job (templates/kagent/migrate-*.yaml):
`agent-manager migrate` run once per installation as an identity of its own
(giantswarm/agent-platform#346, bumblebee-plans#51 D11).
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
The run's pod template, rendered once: migrate-cronjob.yaml hashes it into
the name of the run the start hook creates (a changed template starts a new
run, an unchanged one finds its run started) and emits it under the CronJob's
jobTemplate. Takes a dict: root (the chart context),
serviceAccountName (the migration's identity), image, args (list), env (list).
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
          ephemeral-storage: {{ include "agent-platform.hooks.tmpRequest" .root }}
        limits:
          memory: 256Mi
          ephemeral-storage: {{ include "agent-platform.hooks.tmpLimit" .root }}
      volumeMounts:
        - name: tmp
          mountPath: /tmp
  volumes:
    - name: tmp
      emptyDir:
        sizeLimit: {{ include "agent-platform.hooks.tmpLimit" .root }}
{{- end -}}

{{/*
agent-manager's image for the Job (agentManager.migration.image; the tag is the
meta chart's BOM pin for the agent-manager component).
*/}}
{{- define "agent-platform.kagent.migration.image" -}}
{{- $i := .Values.agentManager.migration.image -}}
{{- printf "%s/%s:%s" $i.registry $i.repository $i.tag -}}
{{- end -}}

{{/*
The start hook's script (migrate-cronjob.yaml): create the run <job> from the
CronJob <cronJob> in <namespace> unless a Job of that name exists — finished,
running or failed, the run of this pod template has been started. Takes a
dict: namespace, cronJob, job.
*/}}
{{- define "agent-platform.kagent.migration.startScript" -}}
existing=$(kubectl -n {{ .namespace }} get job {{ .job }} --ignore-not-found -o name)
if [ -n "$existing" ]; then
  echo "{{ .job }} exists: this migration was started; nothing to start"
  exit 0
fi
kubectl -n {{ .namespace }} create job {{ .job }} --from=cronjob/{{ .cronJob }}
echo "{{ .job }} started; its outcome: kubectl -n {{ .namespace }} get job {{ .job }}, the ConfigMap agent-manager-migrate-report"
{{- end -}}

{{/*
Truthy when a range (the argument) admits a 3.x release, probed as
agent-manager's migrate probes it (agent-platform.agentChart.majors in the
meta chart): every version the range names with its next patch and minor, so
each alternative of a `||` range counts, and 3.0.0, a late 3.x and the first
and a late patch of every minor below 200.
*/}}
{{- define "agent-platform.kagent.migration.isV3" -}}
{{- $range := toString . -}}
{{- if and (regexMatch "[0-9]" $range) (not (regexMatch "(^|[\\s|,<>=~^])[A-Za-uwyzA-WYZ]" $range)) -}}
{{- $hit := or (semverCompare $range "3.0.0") (semverCompare $range "3.999999.999999") -}}
{{- range $lit := regexFindAll "[0-9]+(\\.[0-9]+)?(\\.[0-9]+)?" $range -1 -}}
{{- $p := splitList "." $lit -}}
{{- if eq (index $p 0) "3" -}}
{{- $min := 0 -}}{{- if gt (len $p) 1 -}}{{- $min = atoi (index $p 1) -}}{{- end -}}
{{- $pat := 0 -}}{{- if gt (len $p) 2 -}}{{- $pat = atoi (index $p 2) -}}{{- end -}}
{{- range $v := list (printf "3.%d.%d" $min $pat) (printf "3.%d.%d" $min (add1 $pat)) (printf "3.%d.0" (add1 $min)) -}}
{{- if and (not $hit) (semverCompare $range $v) -}}{{- $hit = true -}}{{- end -}}
{{- end -}}
{{- end -}}
{{- end -}}
{{- range $n := until 200 -}}
{{- if and (not $hit) (or (semverCompare $range (printf "3.%d.0" $n)) (semverCompare $range (printf "3.%d.999999" $n))) -}}{{- $hit = true -}}{{- end -}}
{{- end -}}
{{- if $hit -}}true{{- end -}}
{{- end -}}
{{- end -}}

{{/*
Truthy when a run of the migration takes the 2.x -> 3.x path and reads the
organization namespaces (migrate-rbac.yaml): agent-manager.agentChart.semver
(as the run takes it) is a 3.x line, or agentManager.migration.preflightSemver
is, for the dry run before the line moves.
*/}}
{{- define "agent-platform.kagent.migration.v3Line" -}}
{{- $am := include "agent-platform.agentManager.chartValues" . | fromJson -}}
{{- if or (include "agent-platform.kagent.migration.isV3" (dig "agentChart" "semver" "2.x" $am)) (include "agent-platform.kagent.migration.isV3" (dig "migration" "preflightSemver" "" (.Values.agentManager | default dict))) -}}true{{- end -}}
{{- end -}}

{{/*
Truthy when the run itself (not the preflight dry run) takes the 2.x -> 3.x
path with organizations on: agent-manager.agentChart.semver is a 3.x line.
Only then the run writes organization releases and its report there
(migrate-rbac.yaml, organizations/migrate-scope.yaml).
*/}}
{{- define "agent-platform.kagent.migration.v3Writes" -}}
{{- $am := include "agent-platform.agentManager.chartValues" . | fromJson -}}
{{- if and (include "agent-platform.organizations.enabled" .) (include "agent-platform.kagent.migration" .) (include "agent-platform.kagent.migration.isV3" (dig "agentChart" "semver" "2.x" $am)) -}}true{{- end -}}
{{- end -}}
