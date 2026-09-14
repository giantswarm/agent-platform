{{/*
The hook Jobs of this release, and their one identity.

Two hooks: the Agent Substrate bootstrap (templates/substrate/bootstrap.yaml,
pre-install + pre-upgrade — mints the CA and JWT pools, the actor-identity trust
anchor and ate-api-server's authentication config the Substrate pods mount and
cannot start without, once, and keeps them) and the derived Postgres connection
Secrets of postgres.databases (templates/postgres/databases-hook.yaml,
post-install + post-upgrade — copies the CNPG <cluster>-app Secret per database,
which the operator mints for the initdb database only). Both run under the
restricted pod security profile (this chart's Jobs have to be admitted where
restricted PSS is enforced — the hook pods themselves violate nothing) as the
ServiceAccount <release>-hooks (templates/substrate/hooks-rbac.yaml): a
ClusterRole on secrets, configmaps and namespaces — the bootstrap writes into
two namespaces the substrate release has not created yet and the databases
hook into the namespaces of postgres.databases.*.secretNamespaces, none of
which exist when a pre-install hook's Roles would have to — created for each
hook event and removed with it (hook-succeeded).

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
Whether this release renders any hook Job — and with it the hook identity.
*/}}
{{- define "agent-platform.hooks.enabled" -}}
{{- if or (include "agent-platform.substrate.enabled" .) (include "agent-platform.postgres.databases" . | fromJsonArray) -}}true{{- end -}}
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
