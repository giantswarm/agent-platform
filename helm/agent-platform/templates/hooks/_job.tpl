{{/*
The Helm hook Jobs of this chart. Every hook runs ONE plain kubectl command in
registry.k8s.io/kubectl (gitops.hooks.image) — the image is distroless, there is
no shell — as the hook ServiceAccount (rbac.yaml), under the restricted pod
security profile (the namespace the FluxInstance labels warns on anything
less). Hook weights in use:
  -10  the hook ServiceAccount + ClusterRoleBinding (rbac.yaml)
   -6, -5  reserved for the self-management hooks (stop a resumer, suspend the
        chart's own HelmRelease) that a later release adds in this directory
    0  delete the platform HelmReleases and wait (teardown.yaml)
    5  delete the FluxInstance and wait (teardown.yaml)
before-hook-creation clears a previous run's Job (a failed one is left in place
for inspection until the next attempt), hook-succeeded removes every hook
object once ALL hooks of the event succeeded — Helm applies that policy after
the last hook, so the ServiceAccount outlives the Jobs that need it.
*/}}

{{/*
Common hook ServiceAccount name (one per release; cluster-scoped binding of the
same name).
*/}}
{{- define "agent-platform.hooks.serviceAccountName" -}}
{{- printf "%s-hooks" .Release.Name -}}
{{- end -}}

{{/*
The kubectl image the hooks run.
*/}}
{{- define "agent-platform.hooks.image" -}}
{{- $i := .Values.gitops.hooks.image -}}
{{- printf "%s/%s:%s" $i.registry $i.repository $i.tag -}}
{{- end -}}

{{/*
One hook Job. Arguments (a dict):
  root    the top-level context
  name    Job name suffix (the Job is <release>-<name>)
  hook    the helm.sh/hook event(s), e.g. pre-delete
  weight  the helm.sh/hook-weight
  args    kubectl arguments (a list; the entrypoint is kubectl)
  about   one line for the humans reading the manifest
*/}}
{{- define "agent-platform.hooks.job" -}}
{{- $root := .root -}}
apiVersion: batch/v1
kind: Job
metadata:
  name: {{ printf "%s-%s" $root.Release.Name .name }}
  namespace: {{ $root.Release.Namespace }}
  labels:
    {{- include "labels.common" $root | nindent 4 }}
    app.kubernetes.io/component: hooks
  annotations:
    helm.sh/hook: {{ .hook }}
    helm.sh/hook-weight: {{ .weight | quote }}
    helm.sh/hook-delete-policy: before-hook-creation,hook-succeeded
    agent-platform.giantswarm.io/about: {{ .about | quote }}
spec:
  # kubectl waits up to 5 minutes for the deletions it requested; the Job gives
  # up after 10 and Helm's --timeout bounds the whole event. Two retries cover a
  # transient API error; a hook that still fails aborts the operation before
  # Helm deletes anything (a failed pre-delete hook leaves the release
  # `uninstalling` and the platform untouched).
  backoffLimit: 2
  activeDeadlineSeconds: 600
  # hook-succeeded removes a finished Job; a failed one is kept for an hour.
  ttlSecondsAfterFinished: 3600
  template:
    metadata:
      labels:
        {{- include "labels.common" $root | nindent 8 }}
        app.kubernetes.io/component: hooks
    spec:
      serviceAccountName: {{ include "agent-platform.hooks.serviceAccountName" $root }}
      restartPolicy: Never
      securityContext:
        runAsNonRoot: true
        runAsUser: 65532
        runAsGroup: 65532
        seccompProfile:
          type: RuntimeDefault
      {{- with $root.Values.global.imagePullSecrets }}
      imagePullSecrets:
        {{- range . }}
        - name: {{ if kindIs "map" . }}{{ .name }}{{ else }}{{ . }}{{ end }}
        {{- end }}
      {{- end }}
      containers:
        - name: kubectl
          image: {{ include "agent-platform.hooks.image" $root | quote }}
          args:
            {{- range .args }}
            - {{ . | quote }}
            {{- end }}
          env:
            # kubectl keeps its discovery cache under $HOME; the root filesystem is read-only.
            - name: HOME
              value: /tmp
          securityContext:
            allowPrivilegeEscalation: false
            readOnlyRootFilesystem: true
            capabilities:
              drop:
                - ALL
          resources:
            requests:
              cpu: 10m
              memory: 32Mi
            limits:
              memory: 128Mi
          volumeMounts:
            - name: tmp
              mountPath: /tmp
      volumes:
        - name: tmp
          emptyDir: {}
{{- end -}}
