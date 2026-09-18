{{/* vim: set filetype=mustache: */}}
{{/*
Helpers of the modelServing wiring (templates/model-serving/): KServe/vLLM model
serving — the ClusterServingRuntime, the serving presets and their discovery
ConfigMap, the Hugging Face cache claim, the Kyverno cache policies and the
network policies of the serving namespace. Ported from the standalone umbrella,
where it was a component of its own; here it is a feature switch of the roster
(components.modelServing.enabled, no chart behind it) plus the modelServing:
values block, and it runs on the KServe control plane the kserve-crd and
kserve-resources components install (or a KServe the cluster already serves).
*/}}

{{/*
Truthy when the modelServing feature is on (components.modelServing.enabled).
Optional: a roster that does not carry the entry is off, never force-on.
*/}}
{{- define "agent-platform.modelServing.enabled" -}}
{{- include "agent-platform.optionalComponentEnabled" (dict "root" . "name" "modelServing") -}}
{{- end -}}

{{/*
Truthy when the platform installs the KServe control plane itself: the
kserve-crd AND kserve-resources components are on.
*/}}
{{- define "agent-platform.modelServing.kserveBundled" -}}
{{- if and (include "agent-platform.optionalComponentEnabled" (dict "root" . "name" "kserve-crd")) (include "agent-platform.optionalComponentEnabled" (dict "root" . "name" "kserve-resources")) -}}true{{- end -}}
{{- end -}}

{{/*
Truthy when the cluster serves the KServe serving APIs (ClusterServingRuntime,
InferenceService). Helm fills .Capabilities.APIVersions from the cluster on
install/upgrade; an offline `helm template` has to be told with --api-versions.
*/}}
{{- define "agent-platform.modelServing.kserveApiPresent" -}}
{{- if and (.Capabilities.APIVersions.Has "serving.kserve.io/v1alpha1") (.Capabilities.APIVersions.Has "serving.kserve.io/v1beta1") -}}true{{- end -}}
{{- end -}}

{{/*
The serving namespace: where InferenceServices run, the cache PVC and the
chat-template ConfigMaps live and the Kyverno policies match. Empty falls back
to the release namespace.
*/}}
{{- define "agent-platform.modelServing.namespace" -}}
{{- .Values.modelServing.namespace.name | default .Release.Namespace -}}
{{- end -}}

{{/*
Truthy when this chart manages the Hugging Face cache claim: model serving on,
modelServing.cache.enabled and no existingClaim. The claim is applied by the
hook Job of templates/model-serving/cache-pvc.yaml, never rendered as a release
resource (giantswarm/agent-platform#483).
*/}}
{{- define "agent-platform.modelServing.cacheClaimManaged" -}}
{{- if and (include "agent-platform.modelServing.enabled" .) .Values.modelServing.cache.enabled (not .Values.modelServing.cache.pvc.existingClaim) -}}true{{- end -}}
{{- end -}}

{{/*
The cache claim every predictor pod mounts: the pre-existing claim when named,
else the claim this chart applies (cache-pvc.yaml).
*/}}
{{- define "agent-platform.modelServing.claimName" -}}
{{- $pvc := .Values.modelServing.cache.pvc -}}
{{- $pvc.existingClaim | default $pvc.name -}}
{{- end -}}

{{/*
Truthy when this chart renders the cache claim's StorageClass
(templates/model-serving/storageclass.yaml): the claim is this chart's
(cacheClaimManaged) and modelServing.cache.storageClass.create. Refuses a
pvc.storageClassName next to it — two values would name the claim's class.
*/}}
{{- define "agent-platform.modelServing.cacheStorageClassManaged" -}}
{{- $cache := .Values.modelServing.cache -}}
{{- if and (include "agent-platform.modelServing.cacheClaimManaged" .) $cache.storageClass.create -}}
{{- with $cache.pvc.storageClassName -}}
{{- fail (printf "modelServing.cache.pvc.storageClassName (%q) and modelServing.cache.storageClass.create: true both name the cache claim's class; set storageClass.create: false to keep the named class (\"-\" is the empty class), or drop pvc.storageClassName for the class the chart renders" .) -}}
{{- end -}}
true
{{- end -}}
{{- end -}}

{{/*
The name of the cache claim's StorageClass, rendered or referenced:
modelServing.cache.storageClass.name, else <chart>-<claim>
(agent-platform-connectivity-hf-cache) — cluster-scoped, so named after the
chart like the serving ClusterPolicies.
*/}}
{{- define "agent-platform.modelServing.cacheStorageClass.name" -}}
{{- $cache := .Values.modelServing.cache -}}
{{- $cache.storageClass.name | default (printf "%s-%s-%s" (include "name" .) $cache.pvc.name (include "agent-platform.modelServing.cacheStorageClass.digest" .)) -}}
{{- end -}}

{{- /*
The digest a default class name carries (giantswarm/agent-platform#570): eight
hex characters of the provisioner and its parameters (sorted, key=value), so the
class is named by what the API forbids changing on it. A parameter change renders
a new class; the old Helm-owned one goes with the upgrade instead of failing it
on a forbidden update. A name set in storageClass.name carries none: the
operator owns it.
*/ -}}
{{- define "agent-platform.modelServing.cacheStorageClass.digest" -}}
{{- $sc := .Values.modelServing.cache.storageClass -}}
{{- $spec := $sc.provisioner | toString -}}
{{- range $k, $v := $sc.parameters }}{{- $spec = printf "%s;%s=%s" $spec $k (toString $v) -}}{{ end -}}
{{- $spec | sha256sum | trunc 8 -}}
{{- end -}}

{{/*
The storageClassName the applied claim carries: pvc.storageClassName when set
("-" included — the caller renders it as the empty class), else the chart's
class when it renders one or storageClass.name names an existing one, else
nothing (empty string = falsy): the cluster's default class.
*/}}
{{- define "agent-platform.modelServing.cacheClaim.storageClassName" -}}
{{- $cache := .Values.modelServing.cache -}}
{{- if $cache.pvc.storageClassName -}}
{{- $cache.pvc.storageClassName -}}
{{- else if or (include "agent-platform.modelServing.cacheStorageClassManaged" .) $cache.storageClass.name -}}
{{- include "agent-platform.modelServing.cacheStorageClass.name" . -}}
{{- end -}}
{{- end -}}

{{/*
The GPU node pool input (modelServing.gpuPool) as the scheduling it puts on a
workload the chart renders onto the pool, as JSON:
  { "tolerations": [<the toleration of the pool taint>] | [], "nodeSelector": {...} }
The toleration tolerates the pool taint with operator Exists (no value — the
gpu-node-pool chart's taint) or Equal (taint.value set); an empty taint.key
yields none. One source for the runtime, the presets and the discovery
ConfigMap, so the three sites never disagree.
Usage: $pool := include "agent-platform.modelServing.gpuPool" . | fromJson
*/}}
{{- define "agent-platform.modelServing.gpuPool" -}}
{{- $gp := .Values.modelServing.gpuPool | default dict -}}
{{- $taint := get $gp "taint" | default dict -}}
{{- $tolerations := list -}}
{{- if get $taint "key" -}}
{{- $tol := dict "key" (get $taint "key") "operator" "Exists" -}}
{{- with get $taint "value" -}}
{{- $_ := set $tol "operator" "Equal" -}}
{{- $_ := set $tol "value" . -}}
{{- end -}}
{{- with get $taint "effect" -}}
{{- $_ := set $tol "effect" . -}}
{{- end -}}
{{- $tolerations = list $tol -}}
{{- end -}}
{{- dict "tolerations" $tolerations "nodeSelector" (get $gp "nodeSelector" | default dict) | toJson -}}
{{- end -}}

{{/*
Merges the pool's scheduling under a workload's own: the pool toleration first
and the workload's after it (an entry equal to the pool's once), the pool
selector under the workload's selector (the workload's keys win). Returns JSON
  { "tolerations": [...], "nodeSelector": {...} }
both empty when neither side has anything, so the caller renders nothing then.
Usage: include "agent-platform.modelServing.poolScheduling" (dict "root" $ "tolerations" $list "nodeSelector" $map) | fromJson
*/}}
{{- define "agent-platform.modelServing.poolScheduling" -}}
{{- $pool := include "agent-platform.modelServing.gpuPool" .root | fromJson -}}
{{- $tolerations := $pool.tolerations -}}
{{- range (.tolerations | default list) -}}
{{- if not (has . $tolerations) -}}
{{- $tolerations = append $tolerations . -}}
{{- end -}}
{{- end -}}
{{- $selector := merge (deepCopy (.nodeSelector | default dict)) $pool.nodeSelector -}}
{{- dict "tolerations" $tolerations "nodeSelector" $selector | toJson -}}
{{- end -}}

{{/*
The ClusterServingRuntimes the chart renders (templates/model-serving/
clusterservingruntime.yaml), as a JSON object with one key, runtimes — the
list: the default (modelServing.runtime) first, then every modelServing.additionalRuntimes entry in order
(giantswarm/agent-platform#550), each deep-merged over the default's values so
a field the entry leaves unset is the default's — a mapping field by field, a
list (args, env, tolerations, supportedModelFormats) whole; Helm's merge keeps
the default where the entry's value is empty. name is required, a DNS-1123
subdomain, and unique across the default and the list.
Usage: $runtimes := (include "agent-platform.modelServing.runtimes" . | fromJson).runtimes
*/}}
{{- define "agent-platform.modelServing.runtimes" -}}
{{- $default := .Values.modelServing.runtime -}}
{{- $out := list $default -}}
{{- $names := list $default.name -}}
{{- range $i, $entry := .Values.modelServing.additionalRuntimes -}}
{{- if not (kindIs "map" $entry) -}}
{{- fail (printf "modelServing.additionalRuntimes[%d]: a runtime is a mapping with a name" $i) -}}
{{- end -}}
{{- $name := get $entry "name" | default "" | toString -}}
{{- if not $name -}}
{{- fail (printf "modelServing.additionalRuntimes[%d]: name is required" $i) -}}
{{- end -}}
{{- if not (regexMatch "^[a-z0-9]([-a-z0-9.]{0,251}[a-z0-9])?$" $name) -}}
{{- fail (printf "modelServing.additionalRuntimes[%d]: name %q must be a lowercase DNS-1123 subdomain (it names the ClusterServingRuntime a preset selects with spec.runtime)" $i $name) -}}
{{- end -}}
{{- if has $name $names -}}
{{- fail (printf "modelServing.additionalRuntimes[%d]: runtime %q is rendered already (modelServing.runtime and every entry need a name of their own)" $i $name) -}}
{{- end -}}
{{- $names = append $names $name -}}
{{- $out = append $out (mergeOverwrite (deepCopy $default) $entry) -}}
{{- end -}}
{{- dict "runtimes" $out | toJson -}}
{{- end -}}

{{/*
Labels of every object the wiring renders.
*/}}
{{- define "agent-platform.modelServing.labels" -}}
{{ include "labels.common" . }}
app.kubernetes.io/component: model-serving
{{- end -}}

{{/*
The selector label of the pre-pull DaemonSet's pods (templates/model-serving/
prepull.yaml, giantswarm/agent-platform#545): the DaemonSet's selector and its
deny-all network policy match it; no model pod shape (podShapes below) and no
policy of a shape carries it, so the pods stay outside every rule written for
a served model.
*/}}
{{- define "agent-platform.modelServing.prepull.selectorLabels" -}}
agent-platform.giantswarm.io/model-serving-prepull: "true"
{{- end -}}

{{/*
The registry host of modelServing.modelImages.registry (giantswarm/agent-platform#551),
validated: a host with an optional port (registry.example.com,
registry.example.com:5000, an in-cluster Service name), no scheme, no path.
Empty when unset: every oci:// reference is published as written.
*/}}
{{- define "agent-platform.modelServing.modelImages.registry" -}}
{{- $registry := toString (dig "modelImages" "registry" "" .Values.modelServing) -}}
{{- if and $registry (not (regexMatch "^[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?(:[0-9]{1,5})?$" $registry)) -}}
{{- fail (printf "modelServing.modelImages.registry %q must be a registry host — host or host:port, no scheme, no path; it replaces the host of every oci:// preset's storageUri, the path stays" $registry) -}}
{{- end -}}
{{- $registry -}}
{{- end -}}

{{/*
The storageUri of a preset served from an OCI model image, as published: the
reference's registry host — the segment before the first / — replaced by
modelServing.modelImages.registry when that is set, the path, tag or digest as
written; the reference as written when the registry is empty. A reference
without a host (oci://<name>:<tag>) is refused: the host is what an
installation swaps, and the pre-pull derives the image from the published form.
Usage: include "agent-platform.modelServing.publishedOciUri" (dict "root" $ "where" $where "storageUri" $uri)
*/}}
{{- define "agent-platform.modelServing.publishedOciUri" -}}
{{- $ref := trimPrefix "oci://" .storageUri -}}
{{- if or (not (contains "/" $ref)) (hasPrefix "/" $ref) -}}
{{- fail (printf "%s: spec.model.storageUri %q names no registry host; an OCI model image is oci://<registry>/<path>[:tag|@digest] — the host is what modelServing.modelImages.registry replaces" .where .storageUri) -}}
{{- end -}}
{{- $registry := include "agent-platform.modelServing.modelImages.registry" .root -}}
{{- if $registry -}}
{{- printf "oci://%s/%s" $registry (rest (splitList "/" $ref) | join "/") -}}
{{- else -}}
{{- .storageUri -}}
{{- end -}}
{{- end -}}

{{/*
The serving presets in effect, as a JSON object keyed by preset name:
  { "<name>": { "source": "shipped" | "values", "preset": <ServingPreset> } }
The shipped set (files/model-serving/presets/*.yaml, unless
shippedPresets.enabled is false) minus shippedPresets.exclude, then the
modelServing.presets entries, a same-named entry replacing the shipped one.
Names are checked here; the document shape is checked by
"agent-platform.modelServing.resolvePreset".
Usage: $presets := include "agent-platform.modelServing.presets" . | fromJson
*/}}
{{- define "agent-platform.modelServing.presets" -}}
{{- $ms := .Values.modelServing -}}
{{- $out := dict -}}
{{- $shipped := list -}}
{{- range $path, $_ := .Files.Glob "files/model-serving/presets/*.yaml" -}}
{{- $doc := $.Files.Get $path | fromYaml -}}
{{- if hasKey $doc "Error" -}}
{{- fail (printf "%s: not a YAML mapping: %s" $path $doc.Error) -}}
{{- end -}}
{{- $name := dig "metadata" "name" "" $doc -}}
{{- $stem := $path | base | trimSuffix ".yaml" -}}
{{- if ne $name $stem -}}
{{- fail (printf "%s: metadata.name (%q) must equal the file name (%q)" $path $name $stem) -}}
{{- end -}}
{{- $shipped = append $shipped $name -}}
{{- if and $ms.shippedPresets.enabled (not (has $name $ms.shippedPresets.exclude)) -}}
{{- $_ := set $out $name (dict "source" "shipped" "preset" $doc) -}}
{{- end -}}
{{- end -}}
{{- range $ms.shippedPresets.exclude -}}
{{- if not (has . $shipped) -}}
{{- fail (printf "modelServing.shippedPresets.exclude names %q, which is not a shipped preset (shipped: %s)" . (join ", " $shipped)) -}}
{{- end -}}
{{- end -}}
{{- $seen := list -}}
{{- range $i, $doc := $ms.presets -}}
{{- if not (kindIs "map" $doc) -}}
{{- fail (printf "modelServing.presets[%d]: a preset is a ServingPreset mapping" $i) -}}
{{- end -}}
{{- $name := dig "metadata" "name" "" $doc -}}
{{- if not $name -}}
{{- fail (printf "modelServing.presets[%d]: metadata.name is required" $i) -}}
{{- end -}}
{{- if has $name $seen -}}
{{- fail (printf "modelServing.presets: preset %q is listed twice" $name) -}}
{{- end -}}
{{- $seen = append $seen $name -}}
{{- $_ := set $out $name (dict "source" "values" "preset" $doc) -}}
{{- end -}}
{{- $out | toJson -}}
{{- end -}}

{{/*
Validates one preset and resolves it into the published form the portal and
model-manager read: runtime defaulted to the component's, model.format to vLLM,
resources.gpus to 1, requirements.overheadGiB to 30, and the chat template (one
of file, content, existingConfigMap) resolved to the ConfigMap that holds it,
with the --chat-template flag appended to args, the GPU node pool's
toleration and selector merged under spec.scheduling (modelServing.gpuPool), and
an oci:// storageUri's registry host swapped for modelServing.modelImages.registry.
Returns JSON:
  { "preset": <published ServingPreset>,
    "chatTemplate": { "render": bool, "name": string, "key": string, "content": string } }
Usage: include "agent-platform.modelServing.resolvePreset" (dict "root" $ "name" $name "entry" $entry) | fromJson
*/}}
{{- define "agent-platform.modelServing.resolvePreset" -}}
{{- $root := .root -}}
{{- $ms := $root.Values.modelServing -}}
{{- $name := .name -}}
{{- $where := printf "serving preset %q (%s)" $name .entry.source -}}
{{- $doc := deepCopy .entry.preset -}}
{{- if ne (dig "apiVersion" "" $doc) "agent-platform.giantswarm.io/v1alpha1" -}}
{{- fail (printf "%s: apiVersion must be agent-platform.giantswarm.io/v1alpha1" $where) -}}
{{- end -}}
{{- if ne (dig "kind" "" $doc) "ServingPreset" -}}
{{- fail (printf "%s: kind must be ServingPreset" $where) -}}
{{- end -}}
{{- if not (regexMatch "^[a-z0-9]([-a-z0-9]{0,28}[a-z0-9])?$" $name) -}}
{{- fail (printf "%s: metadata.name must be a lowercase DNS-1123 label of at most 30 characters (it names the InferenceService and the preset ConfigMaps)" $where) -}}
{{- end -}}
{{- $spec := get $doc "spec" | default dict -}}
{{- if not (kindIs "map" $spec) -}}
{{- fail (printf "%s: spec must be a mapping" $where) -}}
{{- end -}}
{{- if not (get $spec "displayName") -}}
{{- fail (printf "%s: spec.displayName is required" $where) -}}
{{- end -}}
{{- $model := get $spec "model" | default dict -}}
{{- if not (kindIs "map" $model) -}}
{{- fail (printf "%s: spec.model must be a mapping" $where) -}}
{{- end -}}
{{- if not (get $model "id") -}}
{{- fail (printf "%s: spec.model.id (the Hugging Face repository) is required" $where) -}}
{{- end -}}
{{- if not (get $model "storageUri") -}}
{{- fail (printf "%s: spec.model.storageUri is required" $where) -}}
{{- end -}}
{{- /* An OCI model image (oci://…): the registry host published is the
       installation's (modelServing.modelImages.registry), the path kept —
       here, so the preset ConfigMap, the pre-pull DaemonSet and every
       consumer read one value (#551). */ -}}
{{- $storageUri := toString (get $model "storageUri") -}}
{{- if hasPrefix "oci://" $storageUri -}}
{{- $_ := set $model "storageUri" (include "agent-platform.modelServing.publishedOciUri" (dict "root" $root "where" $where "storageUri" $storageUri)) -}}
{{- end -}}
{{- $_ := set $model "format" (get $model "format" | default "vLLM") -}}
{{- $_ := set $spec "model" $model -}}
{{- $_ := set $spec "runtime" (get $spec "runtime" | default $ms.runtime.name) -}}
{{- $resources := get $spec "resources" | default dict -}}
{{- if not (kindIs "map" $resources) -}}
{{- fail (printf "%s: spec.resources must be a mapping" $where) -}}
{{- end -}}
{{- if not (hasKey $resources "gpus") -}}
{{- $_ := set $resources "gpus" 1 -}}
{{- end -}}
{{- $_ := set $spec "resources" $resources -}}
{{- $requirements := get $spec "requirements" | default dict -}}
{{- if not (kindIs "map" $requirements) -}}
{{- fail (printf "%s: spec.requirements must be a mapping" $where) -}}
{{- end -}}
{{- if not (hasKey $requirements "weightsGiB") -}}
{{- fail (printf "%s: spec.requirements.weightsGiB is required (the fit check adds overheadGiB to it)" $where) -}}
{{- end -}}
{{- if not (hasKey $requirements "overheadGiB") -}}
{{- $_ := set $requirements "overheadGiB" 30 -}}
{{- end -}}
{{- $_ := set $spec "requirements" $requirements -}}
{{- $args := get $spec "args" | default list -}}
{{- /* Every argument reaches vLLM through the well-known runtime template's
       entrypoint, which re-parses the arguments with a shell (eval "… $@"):
       a bare JSON is split at its whitespace and loses its double quotes, an
       unbalanced quote fails the whole command, a metacharacter expands.
       Outside single-quoted spans an argument therefore carries none of them;
       a value that needs them is single-quoted inside one argument
       (--default-chat-template-kwargs='{"enable_thinking": false}'). */ -}}
{{- range $args -}}
{{- $arg := toString . -}}
{{- if regexMatch "[[:space:]\"'$`\\\\;&|<>(){}\\[\\]*?]" (regexReplaceAll "'[^']*'" $arg "") -}}
{{- fail (printf "%s: spec.args %s carries whitespace, a quote or a shell metacharacter outside single quotes; the runtime template re-parses every argument through a shell (eval \"… $@\"), which splits it there and eats the quotes — write the value single-quoted inside one argument, e.g. --default-chat-template-kwargs='{\"enable_thinking\": false}'" $where (quote $arg)) -}}
{{- end -}}
{{- end -}}
{{- $chatTemplate := get $spec "chatTemplate" | default dict -}}
{{- $render := dict "render" false "name" "" "key" "" "content" "" -}}
{{- if $chatTemplate -}}
{{- range $args -}}
{{- if hasPrefix "--chat-template" (toString .) -}}
{{- fail (printf "%s: spec.args carries %s but spec.chatTemplate is set; the chart appends the --chat-template flag itself" $where .) -}}
{{- end -}}
{{- end -}}
{{- $sources := list -}}
{{- range $source := list "file" "content" "existingConfigMap" -}}
{{- if get $chatTemplate $source -}}
{{- $sources = append $sources $source -}}
{{- end -}}
{{- end -}}
{{- if ne (len $sources) 1 -}}
{{- fail (printf "%s: spec.chatTemplate needs exactly one of file (shipped under files/model-serving/chat-templates/), content (inline) or existingConfigMap (pre-created in the serving namespace); got %d" $where (len $sources)) -}}
{{- end -}}
{{- $key := get $chatTemplate "key" | default "chat-template.jinja" -}}
{{- $mountPath := get $chatTemplate "mountPath" | default "/mnt/chat-template" -}}
{{- $configMap := printf "agent-platform-chat-template-%s" $name -}}
{{- $content := "" -}}
{{- if get $chatTemplate "file" -}}
{{- $content = $root.Files.Get (printf "files/model-serving/chat-templates/%s" (get $chatTemplate "file")) -}}
{{- if not $content -}}
{{- fail (printf "%s: spec.chatTemplate.file %q is not shipped under files/model-serving/chat-templates/" $where (get $chatTemplate "file")) -}}
{{- end -}}
{{- else if get $chatTemplate "content" -}}
{{- $content = get $chatTemplate "content" -}}
{{- else -}}
{{- $configMap = get $chatTemplate "existingConfigMap" -}}
{{- end -}}
{{- $render = dict "render" (ne $content "") "name" $configMap "key" $key "content" $content -}}
{{- $_ := set $spec "chatTemplate" (dict "configMap" $configMap "key" $key "mountPath" $mountPath) -}}
{{- $args = append $args (printf "--chat-template=%s/%s" $mountPath $key) -}}
{{- end -}}
{{- $_ := set $spec "args" $args -}}
{{- /* The GPU node pool (modelServing.gpuPool): its toleration first and its
       selector under the preset's own scheduling block; nothing when both
       sides are empty. */ -}}
{{- $scheduling := get $spec "scheduling" | default dict -}}
{{- if not (kindIs "map" $scheduling) -}}
{{- fail (printf "%s: spec.scheduling must be a mapping" $where) -}}
{{- end -}}
{{- $pool := include "agent-platform.modelServing.poolScheduling" (dict "root" $root "tolerations" (get $scheduling "tolerations") "nodeSelector" (get $scheduling "nodeSelector")) | fromJson -}}
{{- with $pool.tolerations -}}
{{- $_ := set $scheduling "tolerations" . -}}
{{- end -}}
{{- with $pool.nodeSelector -}}
{{- $_ := set $scheduling "nodeSelector" . -}}
{{- end -}}
{{- if $scheduling -}}
{{- $_ := set $spec "scheduling" $scheduling -}}
{{- end -}}
{{- $_ := set $doc "spec" $spec -}}
{{- dict "preset" $doc "chatTemplate" $render | toJson -}}
{{- end -}}

{{/*
Cilium egress rules to the model download endpoints on TCP 443: the Hugging
Face FQDN selectors (modelServing.networkPolicy.huggingFace.fqdns), the CIDR
list (huggingFace.cidrs) and networkPolicy's additionalEgressCIDRs /
additionalEgressFQDNs. Shared by the predictor and download-Job policies. The
caller pipes the output through trim and nindent.
*/}}
{{- define "agent-platform.modelServing.huggingFaceEgress.cilium" -}}
{{- $hf := .Values.modelServing.networkPolicy.huggingFace -}}
{{- $np := .Values.networkPolicy -}}
{{- with $hf.fqdns }}
# Hugging Face by name (resolved through the DNS proxy rule above).
- toFQDNs:
    {{- toYaml . | nindent 4 }}
  toPorts:
    - ports:
        - port: "443"
          protocol: TCP
{{- end }}
{{- with $hf.cidrs }}
# Hugging Face by address (a mirror, a proxy, an S3 endpoint).
- toCIDR:
    {{- toYaml . | nindent 4 }}
  toPorts:
    - ports:
        - port: "443"
          protocol: TCP
{{- end }}
{{- with $np.additionalEgressCIDRs }}
- toCIDR:
    {{- toYaml . | nindent 4 }}
  toPorts:
    - ports:
        - port: "443"
          protocol: TCP
{{- end }}
{{- with $np.additionalEgressFQDNs }}
- toFQDNs:
    {{- toYaml . | nindent 4 }}
  toPorts:
    - ports:
        - port: "443"
          protocol: TCP
{{- end }}
{{- end -}}

{{/*
Kubernetes NetworkPolicy egress rules to the model download endpoints on TCP
443 (the kubernetes flavor of huggingFaceEgress.cilium). Vanilla
NetworkPolicy selects IP blocks, never names: with huggingFace.cidrs empty,
every public destination is admitted on 443 (0.0.0.0/0 minus
networkPolicy.kubernetes.worldExcludedCIDRs, the data plane's own rule); a
CIDR list replaces that with exactly those blocks. The caller pipes the
output through trim and nindent.
*/}}
{{- define "agent-platform.modelServing.huggingFaceEgress.kubernetes" -}}
{{- $hf := .Values.modelServing.networkPolicy.huggingFace -}}
{{- $np := .Values.networkPolicy -}}
{{- if $hf.cidrs }}
# Hugging Face by address (huggingFace.cidrs): these blocks only.
- to:
    {{- range $hf.cidrs }}
    - ipBlock:
        cidr: {{ . | quote }}
    {{- end }}
  ports:
    - port: 443
      protocol: TCP
{{- else }}
# Hugging Face: vanilla NetworkPolicy has no FQDN selector, so every public
# destination on 443 (huggingFace.cidrs narrows it to a mirror or proxy).
- to:
    - ipBlock:
        cidr: 0.0.0.0/0
        except: {{ toYaml $np.kubernetes.worldExcludedCIDRs | nindent 10 }}
  ports:
    - port: 443
      protocol: TCP
{{- end }}
{{- with $np.additionalEgressCIDRs }}
- to:
    {{- range . }}
    - ipBlock:
        cidr: {{ . | quote }}
    {{- end }}
  ports:
    - port: 443
      protocol: TCP
{{- end }}
{{- end -}}

{{/*
Truthy when the models Gateway renders (giantswarm/agent-platform#326): the
modelServing switch and the llm-d controller component on, and
modelServing.modelsGateway.enabled.
*/}}
{{- define "agent-platform.modelServing.modelsGateway.enabled" -}}
{{- if and (include "agent-platform.modelServing.enabled" .) (include "agent-platform.kserve.llmisvcEnabled" .) (.Values.modelServing.modelsGateway).enabled -}}true{{- end -}}
{{- end -}}

{{/* The models Gateway's public hostname: <hostPrefix>.<global.domain>. */}}
{{- define "agent-platform.modelServing.modelsGateway.host" -}}
{{- printf "%s.%s" .Values.modelServing.modelsGateway.hostPrefix (include "agent-platform.domain" (dict "ctx" . "for" "modelServing.modelsGateway.hostPrefix")) -}}
{{- end -}}

{{/* The models Gateway's issuer: the block's own, else global.identity.issuerUrl (or a render failure). */}}
{{- define "agent-platform.modelServing.modelsGateway.issuer" -}}
{{- $issuer := .Values.modelServing.modelsGateway.jwtAuthentication.issuer -}}
{{- if not $issuer -}}
{{- $issuer = include "agent-platform.issuerUrl" (dict "ctx" . "for" "modelServing.modelsGateway.jwtAuthentication") -}}
{{- end -}}
{{- $issuer -}}
{{- end -}}

{{/*
The models Gateway's JWKS block with the host resolved (JSON): the block's own
host, else the issuer's hostname — the public issuer on 443.
*/}}
{{- define "agent-platform.modelServing.modelsGateway.jwks" -}}
{{- $jwks := deepCopy .Values.modelServing.modelsGateway.jwtAuthentication.jwks -}}
{{- if not $jwks.host -}}
{{- $_ := set $jwks "host" (urlParse (include "agent-platform.modelServing.modelsGateway.issuer" .)).hostname -}}
{{- end -}}
{{- $jwks | toJson -}}
{{- end -}}

{{/*
The TLS Secret of the models Gateway's listener: tls.secretName, else <name>-tls
while a Certificate renders (tls.issuerRef.name set), else the platform's
wildcard (gatewayApi.gateway.tls.secretName).
*/}}
{{- define "agent-platform.modelServing.modelsGateway.tlsSecretName" -}}
{{- $mg := .Values.modelServing.modelsGateway -}}
{{- if $mg.tls.secretName -}}{{- $mg.tls.secretName -}}
{{- else if $mg.tls.issuerRef.name -}}{{- printf "%s-tls" $mg.name -}}
{{- else -}}{{- .Values.gatewayApi.gateway.tls.secretName -}}
{{- end -}}
{{- end -}}

{{/*
The pods KServe runs for a served model come in two shapes, and their labels
share nothing: the classic InferenceService predictor carries
serving.kserve.io/inferenceservice=<name> and serves from kserve-container; the
LLMInferenceService workload pod the llm-d controller creates carries
kserve.io/component=workload with app.kubernetes.io/part-of=llminferenceservice
and app.kubernetes.io/name=<name>, and serves from main
(giantswarm/agent-platform#506). Every selector of the serving namespace's
model pods — the Kyverno mutations, the network policies, the PolicyException —
renders from this list, one entry per shape, so they never disagree. JSON:
  [ { "name": "predictor" | "llmisvc-workload",   the object name suffix
      "kind": "InferenceService" | "LLMInferenceService",
      "nameLabel": <the label that carries the served model's name>,
      "runtimeContainer": <the container that serves>,
      "port": <the port the pod is reached on, its Service's target: modelServing.networkPolicy.<shape>.port>,
      "matchExpressions": [<the label selector of the shape>] } ]
Usage: $shapes := include "agent-platform.modelServing.podShapes" . | fromJsonArray
*/}}
{{- define "agent-platform.modelServing.podShapes" -}}
{{- $np := .Values.modelServing.networkPolicy -}}
{{- $classic := dict "name" "predictor" "kind" "InferenceService" "nameLabel" "serving.kserve.io/inferenceservice" "runtimeContainer" "kserve-container" "port" (int $np.predictor.port) -}}
{{- $_ := set $classic "matchExpressions" (list (dict "key" "serving.kserve.io/inferenceservice" "operator" "Exists")) -}}
{{- $llmisvc := dict "name" "llmisvc-workload" "kind" "LLMInferenceService" "nameLabel" "app.kubernetes.io/name" "runtimeContainer" "main" "port" (int $np.llmisvcWorkload.port) -}}
{{- $_ := set $llmisvc "matchExpressions" (list (dict "key" "kserve.io/component" "operator" "In" "values" (list "workload")) (dict "key" "app.kubernetes.io/part-of" "operator" "In" "values" (list "llminferenceservice"))) -}}
{{- list $classic $llmisvc | toJson -}}
{{- end -}}

{{/*
The Kyverno `match` entries selecting the model pods of the serving namespace
at CREATE, one per pod shape (agent-platform.modelServing.podShapes, or the
`shapes` subset given); the caller nests them under `match.any`. Kinds default
to Pod; the operations to CREATE (a pod's init containers are immutable, and
the filter keeps later updates untouched).
Usage: include "agent-platform.modelServing.kyvernoMatch" (dict "root" $ "kinds" (list "Deployment") "operations" (list "CREATE" "UPDATE"))
       include "agent-platform.modelServing.kyvernoMatch" (dict "root" $ "shapes" (list $shape))
*/}}
{{- define "agent-platform.modelServing.kyvernoMatch" -}}
{{- $ns := include "agent-platform.modelServing.namespace" .root -}}
{{- range (.shapes | default (include "agent-platform.modelServing.podShapes" .root | fromJsonArray)) }}
# {{ .kind }}
- resources:
    kinds:
      {{- toYaml ($.kinds | default (list "Pod")) | nindent 6 }}
    namespaces:
      - {{ $ns }}
    operations:
      {{- toYaml ($.operations | default (list "CREATE")) | nindent 6 }}
    selector:
      matchExpressions:
        {{- toYaml .matchExpressions | nindent 8 }}
{{- end }}
{{- end -}}

{{/*
The JMESPath (bare, no delimiters) of the served model's name on a model pod,
whatever its shape: the first of the shapes' name labels the pod carries. It
names the pod's cache subdirectory (<claim>/<model>). Kyverno evaluates it at
admission; the caller wraps it in its delimiters (and `length(... || '')` for a
precondition that the pod carries one at all).
*/}}
{{- define "agent-platform.modelServing.modelNamePath" -}}
{{- $labels := list -}}
{{- range (include "agent-platform.modelServing.podShapes" . | fromJsonArray) -}}
{{- $labels = append $labels (printf "request.object.metadata.labels.%q" .nameLabel) -}}
{{- end -}}
{{- join " || " $labels -}}
{{- end -}}
