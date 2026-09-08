{{/*
The Helm hook Jobs of this chart. A hook is either ONE plain kubectl command in
registry.k8s.io/kubectl (gitops.hooks.image; the image is distroless, there is
no shell) or a small shell script in alpine/k8s (gitops.hooks.helmImage: kubectl,
helm, jq, /bin/sh) where the hook needs helm or has to tolerate a missing
object. Every hook runs under the restricted pod security profile (the
namespace the FluxInstance labels warns on anything less), as the hook
ServiceAccount (rbac.yaml, cluster-admin, itself a pre-delete hook) or — the
self-management hooks — as the regular ServiceAccount <release>-self
(self/rbac.yaml, a namespaced Role). Hook weights in use:
  -10  the hook ServiceAccount + ClusterRoleBinding (rbac.yaml, pre-delete)
   -6  stop a resumer Job still running from the last operation (hooks/self.yaml;
       pre-delete — and pre-upgrade when self-management is off, the hand-back)
   -5  suspend the chart's own HelmRelease and drop the values Secret
       (hooks/self.yaml; same events as -6)
    0  delete the platform HelmReleases and wait (teardown.yaml, pre-delete);
       write the user-supplied values into the values Secret and start the
       resumer (hooks/self.yaml, post-install + post-upgrade)
    5  delete the FluxInstance and wait (teardown.yaml, pre-delete)
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
  args    kubectl arguments (a list; the entrypoint is kubectl) — OR
  script  a shell script (run as `sh -eu -c` in the helm image)
  image   the image, default gitops.hooks.image (pass the helm image for a script)
  serviceAccountName  default the hook ServiceAccount <release>-hooks
  about   one line for the humans reading the manifest
*/}}
{{- define "agent-platform.hooks.job" -}}
{{- $root := .root -}}
{{- $image := .image | default (include "agent-platform.hooks.image" $root) -}}
{{- $sa := .serviceAccountName | default (include "agent-platform.hooks.serviceAccountName" $root) -}}
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
      serviceAccountName: {{ $sa }}
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
        - name: {{ ternary "sh" "kubectl" (hasKey . "script") }}
          image: {{ $image | quote }}
          {{- if hasKey . "script" }}
          command: ["/bin/sh", "-eu", "-c"]
          args:
            - |
              {{- .script | nindent 14 }}
          {{- else }}
          args:
            {{- range .args }}
            - {{ . | quote }}
            {{- end }}
          {{- end }}
          env:
            # kubectl keeps its discovery cache and helm its cache and config
            # under $HOME; the root filesystem is read-only.
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
