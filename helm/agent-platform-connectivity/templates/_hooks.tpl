{{/*
The hook Jobs of this release, and their one identity.

Four hooks: the Agent Substrate bootstrap (templates/substrate/bootstrap.yaml,
pre-install + pre-upgrade — mints the CA and JWT pools, the actor-identity trust
anchor and ate-api-server's authentication config the Substrate pods mount and
cannot start without, once, and keeps them), the derived Postgres connection
Secrets of postgres.databases (templates/postgres/databases-hook.yaml,
post-install + post-upgrade — copies the CNPG <cluster>-app Secret per database,
which the operator mints for the initdb database only), the Hugging Face
cache claim of model serving (templates/model-serving/cache-pvc.yaml,
post-install + post-upgrade — applies the consumer-less claim outside Helm's
wait, which would otherwise wait for a Bind that only the first predictor brings;
#483) and the pre-pull cleanup (templates/model-serving/prepull.yaml,
pre-delete — deletes the pre-pull DaemonSet, itself a post-install/post-upgrade
hook object outside Helm's wait, which an uninstall would otherwise leave
behind; #563). All run under the
restricted pod security profile (this chart's Jobs have to be admitted where
restricted PSS is enforced — the hook pods themselves violate nothing) as the
ServiceAccount <release>-hooks (templates/substrate/hooks-rbac.yaml): a
ClusterRole on secrets, configmaps and namespaces (on persistentvolumeclaims,
and get on storageclasses, while the cache claim is this chart's, on the
pre-pull DaemonSet by name while
it renders) — the bootstrap writes into two
namespaces the substrate release has not created yet and the databases hook
into the namespaces of postgres.databases.*.secretNamespaces, none of which
exist when a pre-install hook's Roles would have to — created for each hook
event the rendered Jobs use (agent-platform.hooks.events: the install and
upgrade events for the first three, pre-delete for the cleanup) and removed with
it (hook-succeeded).

Weights: -5 the identity, 0 the Jobs. before-hook-creation clears a previous
run's Job (a failed one is left for inspection until the next attempt),
hook-succeeded removes every hook object once all hooks of the event succeeded.
Helm runs the hooks of one event in weight order and waits for a Job to finish
before the next weight, so the identity exists before the Job and outlives it.

Usage of the Job include (a dict):
  root      the top-level context
  name      Job name suffix (the Job is <release>-<name>)
  hook      the helm.sh/hook event(s), e.g. pre-install,pre-upgrade
  about     one line for the humans reading the manifest
  script    the shell script (run as `sh -eu -c` in hooks.kubectlImage)
  init      optional: a dict {name, image, script} — an init container that
            prepares /work for the main container (the bootstrap's openssl)
  timeout   optional activeDeadlineSeconds (default 600)
*/}}

{{- define "agent-platform.hooks.serviceAccountName" -}}
{{- printf "%s-hooks" .Release.Name -}}
{{- end -}}

{{/* An image reference from a {registry, repository, tag} value block. */}}
{{- define "agent-platform.hooks.imageRef" -}}
{{- printf "%s/%s:%s" .registry .repository (.tag | toString) -}}
{{- end -}}

{{/*
The hook events the identity is created for, comma-separated for the
helm.sh/hook annotation: the install and upgrade events while the bootstrap,
the databases hook or the cache claim hook renders, pre-delete while the
pre-pull DaemonSet does (its cleanup Job runs then). Empty when no hook Job
renders.
*/}}
{{- define "agent-platform.hooks.events" -}}
{{- $events := list -}}
{{- if or (include "agent-platform.substrate.enabled" .) (include "agent-platform.postgres.databases" . | fromJsonArray) (include "agent-platform.modelServing.cacheClaimManaged" .) -}}
{{- $events = list "pre-install" "pre-upgrade" "post-install" "post-upgrade" -}}
{{- end -}}
{{- if include "agent-platform.modelServing.prepull.enabled" . -}}
{{- $events = append $events "pre-delete" -}}
{{- end -}}
{{- $events | join "," -}}
{{- end -}}

{{/*
Whether this release renders any hook Job — and with it the hook identity.
*/}}
{{- define "agent-platform.hooks.enabled" -}}
{{- if include "agent-platform.hooks.events" . -}}true{{- end -}}
{{- end -}}

{{- define "agent-platform.hooks.job" -}}
{{- $root := .root -}}
{{- $timeout := .timeout | default 600 -}}
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
    helm.sh/hook-weight: "0"
    helm.sh/hook-delete-policy: before-hook-creation,hook-succeeded
    agent-platform.giantswarm.io/about: {{ .about | quote }}
spec:
  backoffLimit: 2
  activeDeadlineSeconds: {{ $timeout }}
  # Success cleanup comes from the hook delete policy; the TTL reaps a FAILED
  # Job after its debugging window.
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
      {{- with .init }}
      initContainers:
        - name: {{ .name }}
          image: {{ .image | quote }}
          command: ["/bin/sh", "-eu", "-c"]
          args:
            - |
              {{- .script | nindent 14 }}
          env:
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
            - name: work
              mountPath: /work
      {{- end }}
      containers:
        - name: sh
          image: {{ include "agent-platform.hooks.imageRef" $root.Values.hooks.kubectlImage | quote }}
          command: ["/bin/sh", "-eu", "-c"]
          args:
            - |
              {{- .script | nindent 14 }}
          env:
            # kubectl keeps its discovery cache under $HOME; the root filesystem
            # is read-only.
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
            {{- if .init }}
            - name: work
              mountPath: /work
              readOnly: true
            {{- end }}
      volumes:
        - name: tmp
          emptyDir: {}
        {{- if .init }}
        - name: work
          emptyDir:
            medium: Memory
        {{- end }}
{{- end -}}
