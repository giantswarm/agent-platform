{{/*
The hook Jobs of this release, and their one identity.

Seven hooks: the Substrate namespaces (templates/substrate/bootstrap.yaml,
pre-install + pre-upgrade at weight -4 — creates the two namespaces the
bootstrap writes into when they are missing, so its Roles have somewhere to
be), the Agent Substrate bootstrap (templates/substrate/bootstrap.yaml,
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
behind; #563) and the ModelConfig reconcile of llmRouting
(templates/llm/modelconfig-routing-hook.yaml, post-install + post-upgrade —
takes the listener URL the policies wrote back off the ModelConfigs once
routing is off, which Helm cannot: it never owned the baseUrl) and the sign-in
keys cleanup (templates/workspace-manager/signin-keys-cleanup.yaml,
post-install + post-upgrade — deletes the workspace-manager sign-in keys Secret
an earlier release kept). All run under the
restricted pod security profile (this chart's Jobs have to be admitted where
restricted PSS is enforced — the hook pods themselves violate nothing) as the
ServiceAccount <release>-hooks (templates/substrate/hooks-rbac.yaml): a
ClusterRole on what is cluster-scoped or has no namespace of its own (the
Substrate namespaces, the podcert ClusterTrustBundles, persistentvolumeclaims
and get on storageclasses while the cache claim is this chart's, the pre-pull
DaemonSet by name while it renders), created for each hook event the rendered
Jobs use (agent-platform.hooks.events: the install and upgrade events for the
first three, pre-delete for the cleanup) and removed with it (hook-succeeded),
and no read of a Secret or ConfigMap: those are Roles, one per namespace a hook
writes, at the events of that hook only, with get, list, watch, patch and
delete on the names it writes (create has no name to narrow by). The bootstrap
writes into the two Substrate namespaces, which the substrate release has not
created on a first install, so the substrate-namespaces Job creates them first.
One more hook Job comes from this include with an identity of its own (identity below): the start of the agent-manager migrate
run (templates/kagent/migrate-cronjob.yaml, post-install + post-upgrade).

Weights: -5 the identity, -4 the substrate-namespaces Job, -3 the Roles, 0
the other Jobs. before-hook-creation clears a previous
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
  memory    optional: the main container's memory limit (default 128Mi, which
            holds one kubectl run and its shell). A hook whose script keeps
            more resident at once raises it for itself and says why in its
            template (the bootstrap: 256Mi). Every request stays 10m/32Mi, and
            the init container keeps 128Mi.
  timeout   optional activeDeadlineSeconds (default 600)
  weight    optional helm.sh/hook-weight (default 0)
  identity  optional: a dict {namespace, serviceAccountName, component} — a
            hook that acts with an identity of the release instead of the
            hook identity, in that identity's namespace, under the network
            policy that selects that component label (the agent-manager
            migrate start: the migration's identity in the kagent namespace,
            templates/kagent/migrate-cronjob.yaml)
*/}}

{{- define "agent-platform.hooks.serviceAccountName" -}}
{{- printf "%s-hooks" .Release.Name -}}
{{- end -}}

{{/* An image reference from a {registry, repository, tag} value block. */}}
{{- define "agent-platform.hooks.imageRef" -}}
{{- printf "%s/%s:%s" .registry .repository (.tag | toString) -}}
{{- end -}}

{{/*
The ephemeral-storage request and limit of every hook container and of the
agent-manager migrate run, and the sizeLimit of the /tmp emptyDir each mounts
(the limit). Kyverno's require-emptydir-requests-and-limits refuses a
container mounting an emptyDir without them. /tmp holds kubectl's discovery
and HTTP cache and a script's few small files; migrate keeps its chart schema
and skill lookups in memory and writes nothing there. Nothing in it scales
with the cluster.
*/}}
{{- define "agent-platform.hooks.tmpRequest" -}}16Mi{{- end -}}
{{- define "agent-platform.hooks.tmpLimit" -}}256Mi{{- end -}}

{{/*
The hook events the identity is created for, comma-separated for the
helm.sh/hook annotation: the install and upgrade events while the bootstrap,
the databases hook or the cache claim hook renders, pre-delete while the
pre-pull DaemonSet does (its cleanup Job runs then), the post events alone
while only the ModelConfig reconcile of llmRouting
(templates/llm/modelconfig-routing-hook.yaml) or the sign-in keys cleanup
(templates/workspace-manager/signin-keys-cleanup.yaml) renders. Empty when no
hook Job renders.
*/}}
{{- define "agent-platform.hooks.events" -}}
{{- $events := list -}}
{{- if or (include "agent-platform.substrate.enabled" .) (include "agent-platform.postgres.databases" . | fromJsonArray) (include "agent-platform.modelServing.cacheClaimManaged" .) -}}
{{- $events = list "pre-install" "pre-upgrade" "post-install" "post-upgrade" -}}
{{- else if or (include "agent-platform.llmRouting.modelConfigs" .) (include "agent-platform.workspaceManager.signinKeysOrphan" .) -}}
{{- $events = list "post-install" "post-upgrade" -}}
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
{{- $memory := .memory | default "128Mi" -}}
{{- $identity := .identity | default (dict "namespace" $root.Release.Namespace "serviceAccountName" (include "agent-platform.hooks.serviceAccountName" $root) "component" "hooks") -}}
apiVersion: batch/v1
kind: Job
metadata:
  name: {{ printf "%s-%s" $root.Release.Name .name }}
  namespace: {{ $identity.namespace }}
  labels:
    {{- include "labels.common" $root | nindent 4 }}
    app.kubernetes.io/component: {{ $identity.component }}
  annotations:
    helm.sh/hook: {{ .hook }}
    helm.sh/hook-weight: {{ .weight | default 0 | quote }}
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
        app.kubernetes.io/component: {{ $identity.component }}
    spec:
      serviceAccountName: {{ $identity.serviceAccountName }}
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
              ephemeral-storage: {{ include "agent-platform.hooks.tmpRequest" $root }}
            limits:
              memory: 128Mi
              ephemeral-storage: {{ include "agent-platform.hooks.tmpLimit" $root }}
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
              ephemeral-storage: {{ include "agent-platform.hooks.tmpRequest" $root }}
            limits:
              memory: {{ $memory }}
              ephemeral-storage: {{ include "agent-platform.hooks.tmpLimit" $root }}
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
          emptyDir:
            sizeLimit: {{ include "agent-platform.hooks.tmpLimit" $root }}
        {{- if .init }}
        # In memory, so it counts against the containers' memory limits; the
        # key material it carries is a few KiB.
        - name: work
          emptyDir:
            medium: Memory
            sizeLimit: 16Mi
        {{- end }}
{{- end -}}

{{/*
The Secrets and ConfigMaps each hook Job reads or writes, by namespace: the
Roles of the hook identity (templates/substrate/hooks-rbac.yaml). A JSON array
of {purpose, namespace, events, rules: [{resource, names, verbs}]}; a rule
without names is create, which the apiserver cannot narrow by name.
*/}}
{{- define "agent-platform.hooks.secretGrants" -}}
{{- $out := list -}}
{{- $create := dict "resource" "secrets" "names" list "verbs" (list "create") -}}
{{- if include "agent-platform.substrate.enabled" . -}}
{{- $events := "pre-install,pre-upgrade" -}}
{{- $out = append $out (dict "purpose" "substrate-bootstrap" "namespace" (include "agent-platform.substrate.podcertNamespace" .) "events" $events "rules" (list
      (dict "resource" "secrets" "names" (list "service-dns-ca-pool" "pod-identity-ca-pool") "verbs" (list "get"))
      $create)) -}}
{{- $out = append $out (dict "purpose" "substrate-bootstrap" "namespace" (include "agent-platform.substrate.namespace" .) "events" $events "rules" (list
      (dict "resource" "secrets" "names" (list "actor-id-jwt-pool" "actor-id-ca-pool" "egress-mitm-ca-pool") "verbs" (list "get"))
      (dict "resource" "secrets" "names" (list "actor-id-ca-certs") "verbs" (list "get" "patch"))
      $create
      (dict "resource" "configmaps" "names" (list "ate-api-authentication") "verbs" (list "get"))
      (dict "resource" "configmaps" "names" list "verbs" (list "create")))) -}}
{{- end -}}
{{- $databases := include "agent-platform.postgres.databases" . | fromJsonArray -}}
{{- if $databases -}}
{{- $source := "" -}}
{{- $sourceNs := "" -}}
{{- $targets := dict -}}
{{- $order := list -}}
{{- range $databases -}}
{{- $source = printf "%s-app" .cluster -}}
{{- $sourceNs = .namespace -}}
{{- $derived := printf "%s-%s-app" .cluster .key -}}
{{- range .secretNamespaces -}}
{{- if not (hasKey $targets .) -}}{{- $order = append $order . -}}{{- end -}}
{{- $_ := set $targets . (append (get $targets . | default list) $derived) -}}
{{- end -}}
{{- end -}}
{{- if not (hasKey $targets $sourceNs) -}}{{- $order = prepend $order $sourceNs -}}{{- $_ := set $targets $sourceNs list -}}{{- end -}}
{{- range $ns := $order -}}
{{- $rules := list -}}
{{- if eq $ns $sourceNs -}}
{{- $rules = append $rules (dict "resource" "secrets" "names" (list $source) "verbs" (list "get" "list" "watch")) -}}
{{- end -}}
{{- with get $targets $ns -}}
{{- $rules = append $rules (dict "resource" "secrets" "names" (. | uniq) "verbs" (list "get" "patch")) -}}
{{- $rules = append $rules $create -}}
{{- end -}}
{{- $out = append $out (dict "purpose" "postgres-databases" "namespace" $ns "events" "post-install,post-upgrade" "rules" $rules) -}}
{{- end -}}
{{- end -}}
{{- with include "agent-platform.workspaceManager.signinKeysOrphan" . -}}
{{- $out = append $out (dict "purpose" "signin-keys-cleanup" "namespace" $.Release.Namespace "events" "post-install,post-upgrade" "rules" (list
      (dict "resource" "secrets" "names" (list .) "verbs" (list "delete")))) -}}
{{- end -}}
{{- $out | toJson -}}
{{- end -}}
