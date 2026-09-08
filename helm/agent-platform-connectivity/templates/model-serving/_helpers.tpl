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
The cache claim every predictor pod mounts: the pre-existing claim when named,
else the PVC this chart renders.
*/}}
{{- define "agent-platform.modelServing.claimName" -}}
{{- $pvc := .Values.modelServing.cache.pvc -}}
{{- $pvc.existingClaim | default $pvc.name -}}
{{- end -}}

{{/*
Labels of every object the wiring renders.
*/}}
{{- define "agent-platform.modelServing.labels" -}}
{{ include "labels.common" . }}
app.kubernetes.io/component: model-serving
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
with the --chat-template flag appended to args. Returns JSON:
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
