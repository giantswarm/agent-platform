{{/*
The serving slice's ordered teardown (giantswarm/agent-platform#527). The
llm-d controller (kserve-llmisvc-resources) puts
serving.kserve.io/llmisvcconfig-finalizer on every well-known
LLMInferenceServiceConfig of kserve-runtime-configs, is the only thing that
clears it, and its validating webhook denies every delete of such a config
while it runs. Helm deletes both child HelmReleases in one pass, so either
order breaks: the configs' uninstall first is denied (the child stays
UninstallFailed, its release Secret at `uninstalling`); the controller first
leaves the configs terminating under a finalizer nothing clears, and the next
slice installed into the namespace adopts and loses them (ConfigNotFound).
The hook removes the two in the one order that completes — cluster-manager's
teardown order: the controller's release, waited for until its Deployment and
the webhook configuration are gone; the configs of kserve-runtime-configs,
deleted through the CRD's storage version (the one request that needs no
conversion: the CRD's conversion webhook went with the controller) with the
finalizer taken off; then the configs' release, whose uninstall has nothing
left to delete. A config drift correction re-creates in that window carries no
finalizer (no controller adds one) and goes with the uninstall.

The event it runs at, empty where it does not render:
  pre-delete   both releases are this release's (the components on): uninstalling
               the release, with or without the bundled engine (before the
               engine's teardown waves, which then find the two gone);
  pre-upgrade  kserve-runtime-configs goes off while its live HelmRelease is this
               release's (a lookup: live under the Helm CLI and helm-controller,
               empty under `helm template`) — the slice switched off in place.
               kserve-llmisvc-resources staying on then is refused: its
               controller's webhook denies the configs' delete for as long as
               it runs.
Not with gitops.target.kubeConfig: the children run in the target cluster,
which the hook identity cannot reach (its network policy admits this
cluster's apiserver only). The release that shape is made for — a
cluster-manager serving slice — is torn down in this order by cluster-manager
itself (delete_node_pool, disable_model_serving); README "Uninstalling" names
the manual order for any other.
*/}}
{{- define "agent-platform.serving.teardownEvent" -}}
{{- if not (include "agent-platform.targetSecretName" .) -}}
{{- $configs := include "agent-platform.componentEnabled" (dict "root" . "name" "kserve-runtime-configs") -}}
{{- $controller := include "agent-platform.componentEnabled" (dict "root" . "name" "kserve-llmisvc-resources") -}}
{{- if $configs -}}
{{- if $controller }}pre-delete{{ end -}}
{{- else -}}
{{- $ns := .Values.gitops.namespace | default .Release.Namespace -}}
{{- $live := lookup "helm.toolkit.fluxcd.io/v2" "HelmRelease" $ns (index .Values.components "kserve-runtime-configs").chart -}}
{{- $ann := dig "metadata" "annotations" dict $live -}}
{{- if and (eq (get $ann "meta.helm.sh/release-name") .Release.Name) (eq (get $ann "meta.helm.sh/release-namespace") .Release.Namespace) -}}
{{- if $controller -}}
{{- fail "components.kserve-runtime-configs goes off while components.kserve-llmisvc-resources stays on: the llm-d controller's webhook denies every delete of a well-known LLMInferenceServiceConfig while it runs, so the configs' release could never be uninstalled. Switch both off together (the hook then removes them in order), or keep kserve-runtime-configs on" -}}
{{- end -}}
pre-upgrade
{{- end -}}
{{- end -}}
{{- end -}}
{{- end -}}

{{/*
The serving teardown hook's script (a shell script in the helm image: kubectl,
jq). Every step tolerates what is gone already, so a retry of the Job resumes
where the last attempt stopped.
*/}}
{{- define "agent-platform.serving.teardownScript" -}}
{{- $ns := .Values.gitops.namespace | default .Release.Namespace -}}
{{- $controller := index .Values.components "kserve-llmisvc-resources" -}}
{{- $configs := index .Values.components "kserve-runtime-configs" -}}
{{- $targetNs := include "agent-platform.targetNamespace" . -}}
{{- $controllerNs := $controller.targetNamespace | default $targetNs -}}
{{- $configsNs := $configs.targetNamespace | default $targetNs -}}
hr=helmreleases.helm.toolkit.fluxcd.io
finalizer=serving.kserve.io/llmisvcconfig-finalizer
# gone <what> <kubectl get arguments>: wait up to five minutes until the get lists nothing.
gone() {
  what=$1
  shift
  i=0
  while [ -n "$(kubectl get "$@" --ignore-not-found -o name)" ]; do
    i=$((i + 1))
    if [ "$i" -gt 150 ]; then
      echo "$what still there after five minutes" >&2
      return 1
    fi
    sleep 2
  done
}
# release <name>: delete the child HelmRelease and wait for helm-controller to
# uninstall it. With Flux's HelmRelease API gone (a retried uninstall after the
# bundled engine's teardown removed Flux) no child release is left to order.
release() {
  if [ -n "$(kubectl get customresourcedefinitions "$hr" --ignore-not-found -o name)" ]; then
    kubectl delete "$hr" --namespace {{ $ns }} "$1" --ignore-not-found --wait --timeout=5m
  else
    echo "no $hr API: $1 is not a HelmRelease any more"
  fi
}
echo "1/3 the llm-d controller's release {{ $controller.chart }}: its webhook denies every delete of a well-known config while it runs"
release {{ $controller.chart }}
gone "the llm-d controller" deployments --namespace {{ $controllerNs }} --selector control-plane=llmisvc-controller-manager
gone "the llmisvc webhook" validatingwebhookconfigurations llminferenceserviceconfig.serving.kserve.io
echo "2/3 the configs of {{ $configs.chart }} in {{ $configsNs }}, through the CRD's storage version, with $finalizer taken off"
version=$(kubectl get customresourcedefinitions llminferenceserviceconfigs.serving.kserve.io --ignore-not-found -o jsonpath='{.spec.versions[?(@.storage==true)].name}')
if [ -n "$version" ]; then
  res="llminferenceserviceconfigs.$version.serving.kserve.io"
  names=$(kubectl get "$res" --namespace {{ $configsNs }} -o json | jq -r --arg release {{ $configs.chart }} '.items[] | select(.metadata.annotations["meta.helm.sh/release-name"] == $release) | .metadata.name')
  for name in $names; do
    kubectl delete "$res" "$name" --namespace {{ $configsNs }} --ignore-not-found --wait=false
    rest=$(kubectl get "$res" "$name" --namespace {{ $configsNs }} --ignore-not-found -o json | jq -c --arg f "$finalizer" '[.metadata.finalizers // [] | .[] | select(. != $f)]')
    if [ -n "$rest" ]; then
      kubectl patch "$res" "$name" --namespace {{ $configsNs }} --type=merge --patch "{\"metadata\":{\"finalizers\":$rest}}" \
        || [ -z "$(kubectl get "$res" "$name" --namespace {{ $configsNs }} --ignore-not-found -o name)" ]
    fi
  done
  if [ -n "$names" ]; then
    # shellcheck disable=SC2086 # one argument per config name
    gone "the configs" "$res" --namespace {{ $configsNs }} $names
  fi
fi
echo "3/3 the configs' release {{ $configs.chart }}"
release {{ $configs.chart }}
{{- end -}}
