{{/* vim: set filetype=mustache: */}}
{{/*
Expand the name of the chart.
*/}}
{{- define "name" -}}
{{- .Chart.Name | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{/*
Create chart name and version as used by the chart label.
*/}}
{{- define "chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimAll "-." -}}
{{- end -}}

{{/*
Common labels
*/}}
{{- define "labels.common" -}}
app: {{ include "name" . | quote }}
{{ include "labels.selector" . }}
app.kubernetes.io/managed-by: {{ .Release.Service | quote }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
application.giantswarm.io/team: {{ index .Chart.Annotations "io.giantswarm.application.team" | quote }}
helm.sh/chart: {{ include "chart" . | quote }}
{{- end -}}

{{/*
Selector labels
*/}}
{{- define "labels.selector" -}}
app.kubernetes.io/name: {{ include "name" . | quote }}
app.kubernetes.io/instance: {{ .Release.Name | quote }}
{{- end -}}

{{/*
Whether a component is enabled — reads `components.<name>.enabled`, the single
on/off switch. `name` is the components.<key> name, which equals the component's
chart name and is therefore what a dependsOn entry references. A component with
no `enabled` key is force-enabled. Emits "true" when on, empty string otherwise.

Used to drop a dependsOn reference to a component that is toggled off, so a
consumer does not wait forever on a HelmRelease that was never rendered. With
app-owned CRDs a CR consumer dependsOn the component that ships the CRD (e.g.
connectivity dependsOn agentgateway + kagent), but those components are opt-in —
in the default muster-direct topology they are off and render no HelmRelease, so
an unfiltered dependsOn would block the always-on consumer indefinitely. An
unknown name (not in components) is kept rather than silently dropped.
Usage: include "agent-platform.componentEnabled" (dict "root" $root "name" "agentgateway")
*/}}
{{- define "agent-platform.componentEnabled" -}}
{{- $root := .root -}}
{{- $c := index $root.Values.components .name -}}
{{- if $c -}}
{{- $on := true -}}
{{- if hasKey $c "enabled" }}{{- $on = $c.enabled }}{{- end }}
{{- if $on }}true{{- end -}}
{{- else -}}
true
{{- end -}}
{{- end -}}

{{/*
The tenant identity of the agents' Flux HelmReleases: kagent.fluxServiceAccountName
while the kagent component is on, "" otherwise. The connectivity chart renders
the ServiceAccount and its RoleBinding from the same value (a helper of the same
name there) and exports it to the portal's app-config; this copy derives
agent-manager's flux.helmReleaseServiceAccount (componentDerivedValues), so the
three consumers cannot disagree.
Usage: include "agent-platform.kagent.fluxServiceAccountName" .
*/}}
{{- define "agent-platform.kagent.fluxServiceAccountName" -}}
{{- if (include "agent-platform.componentEnabled" (dict "root" . "name" "kagent")) -}}
{{- dig "fluxServiceAccountName" "" (.Values.kagent | default dict) -}}
{{- end -}}
{{- end -}}

{{/*
Values this chart derives for a component from a block another component owns,
merged OVER the component's forwarded values (templates/components.yaml) so one
value drives every consumer. Emits a JSON object; {} for a component with
nothing derived. A value the component's own block sets must agree with the
derived one, otherwise the render fails naming the single key to set — a silent
overwrite would hide a values file that still spells the old key.
  agent-manager: flux.helmReleaseServiceAccount from kagent.fluxServiceAccountName.
Usage: include "agent-platform.componentDerivedValues" (dict "root" $root "name" $key) | fromJson
*/}}
{{- define "agent-platform.componentDerivedValues" -}}
{{- $derived := dict -}}
{{- if eq .name "agent-manager" -}}
{{- $sa := include "agent-platform.kagent.fluxServiceAccountName" .root -}}
{{- $own := dig "flux" "helmReleaseServiceAccount" "" (index .root.Values "agent-manager" | default dict) -}}
{{- if and $own (ne $own $sa) -}}
{{- fail (printf "agent-manager.flux.helmReleaseServiceAccount (%s) differs from kagent.fluxServiceAccountName (%s): the agents' HelmReleases have one tenant identity — set kagent.fluxServiceAccountName and leave agent-manager.flux.helmReleaseServiceAccount unset" $own $sa) -}}
{{- end -}}
{{- $_ := set $derived "flux" (dict "helmReleaseServiceAccount" $sa) -}}
{{- end -}}
{{- $derived | toJson -}}
{{- end -}}

{{/*
Fail the render when a component's on/off toggle is still set the old way, inside
the component's own values block. Those blocks are additionalProperties: true, so
a leftover `enabled` key validates and is then ignored — the component silently
falls back to the `components.<name>.enabled` default, which is off for five of
the six. This turns that into a loud failure naming the new key.
Neither this chart nor the connectivity chart has a Helm dependency, so no chart
default is ever coalesced into these blocks: a legacy key can only be the
operator's and is reported whatever its value, whether the component is on or
off. (An umbrella that feeds these blocks to real Helm dependencies sees
klaus-gateway's own `enabled: true` default coalesced in while that dependency is
on and has to special-case it; nothing here does.) The removed `mcps:` block
needs no entry: the root schema rejects it already.
*/}}
{{- define "agent-platform.validateLegacyToggles" -}}
{{- $moved := list
      (list "agentgateway" "components.agentgateway.enabled")
      (list "valkey" "components.valkey.enabled")
      (list "kagent" "components.kagent.enabled")
      (list "klausGateway" "components.klaus-gateway.enabled")
      (list "agentSandbox" "components.agent-sandbox.enabled") -}}
{{- $found := list -}}
{{- range $moved -}}
{{- if hasKey (index $.Values (first .) | default dict) "enabled" -}}
{{- $found = append $found (printf "%s.enabled -> %s" (first .) (last .)) -}}
{{- end -}}
{{- end -}}
{{- with $found -}}
{{- fail (printf "component toggles moved into components.<name>.enabled and the old keys are ignored; move %s (see UPGRADE.md)" (join ", " .)) -}}
{{- end -}}
{{- end -}}

{{/*
Key paths (dot-joined, "block.path") of credentials set INLINE in the values,
joined by ", ". Empty when none is set. Only the paths are emitted, never the
values, so the string is safe to print in a fail message.

A component's credentials belong in a pre-created Secret the component chart
references (kagent providers.<name>.apiKeySecretRef / oauth2-proxy
config.existingSecret, muster oauth.server.existingSecret /
storage.valkey.existingSecret, valkey auth.usersExistingSecret, klaus-gateway
slack.secretName / obo.existingSecret, model-manager and agent-manager
oauth.existingSecret). Set inline, they are forwarded verbatim into that
component's HelmRelease spec.values and into Helm's release storage, readable
by anyone allowed to get HelmReleases there.
*/}}
{{- define "agent-platform.inlineSecretPaths" -}}
{{- $v := .Values -}}
{{- $found := list -}}
{{- /* Fixed paths: the top-level block, then the path inside it. */ -}}
{{- $paths := list
      (list "kagent" (list "oauth2-proxy" "config" "clientSecret"))
      (list "kagent" (list "oauth2-proxy" "config" "cookieSecret"))
      (list "muster" (list "muster" "oauth" "server" "dex" "clientSecret"))
      (list "muster" (list "muster" "oauth" "server" "google" "clientSecret"))
      (list "muster" (list "muster" "oauth" "server" "registrationToken"))
      (list "muster" (list "muster" "oauth" "server" "encryptionKeyValue"))
      (list "muster" (list "muster" "oauth" "server" "storage" "valkey" "password"))
      (list "klausGateway" (list "slack" "botToken"))
      (list "klausGateway" (list "slack" "signingSecret"))
      (list "klausGateway" (list "slack" "appToken"))
      (list "klausGateway" (list "obo" "stateKey"))
      (list "klausGateway" (list "obo" "storeKey"))
      (list "model-manager" (list "oauth" "dex" "clientSecret"))
      (list "agent-manager" (list "oauth" "dex" "clientSecret")) -}}
{{- range $paths -}}
{{- $cur := index $v (first .) | default dict -}}
{{- $ok := kindIs "map" $cur -}}
{{- range (last .) -}}
{{- if and $ok (kindIs "map" $cur) (hasKey $cur .) -}}
{{- $cur = index $cur . -}}
{{- else -}}
{{- $ok = false -}}
{{- end -}}
{{- end -}}
{{- if and $ok $cur -}}
{{- $found = append $found (printf "%s.%s" (first .) (join "." (last .))) -}}
{{- end -}}
{{- end -}}
{{- /* Every kagent model provider: providers.<name>.apiKey (providers.default is a string). */ -}}
{{- range $name, $p := (dig "providers" dict (index $v "kagent" | default dict)) -}}
{{- if and (kindIs "map" $p) (hasKey $p "apiKey") (index $p "apiKey") -}}
{{- $found = append $found (printf "kagent.providers.%s.apiKey" $name) -}}
{{- end -}}
{{- end -}}
{{- /* Every valkey ACL user: valkey.auth.aclUsers.<user>.password. */ -}}
{{- range $user, $spec := (dig "valkey" "auth" "aclUsers" dict (index $v "valkey" | default dict)) -}}
{{- if and (kindIs "map" $spec) (hasKey $spec "password") (index $spec "password") -}}
{{- $found = append $found (printf "valkey.valkey.auth.aclUsers.%s.password" $user) -}}
{{- end -}}
{{- end -}}
{{- join ", " $found -}}
{{- end -}}

{{/*
gitops.forbidInlineSecrets: fail the render when a credential is set inline.
The message names the key paths only.
*/}}
{{- define "agent-platform.validateInlineSecrets" -}}
{{- if .Values.gitops.forbidInlineSecrets -}}
{{- with (include "agent-platform.inlineSecretPaths" .) -}}
{{- fail (printf "gitops.forbidInlineSecrets is true but these values carry credentials inline, which would land in clear text in the component HelmReleases and in Helm release storage: %s. Move each into a pre-created Secret and reference it (kagent providers.<name>.apiKeySecretRef with an empty apiKey, kagent.oauth2-proxy.config.existingSecret, muster.muster.oauth.server.existingSecret and .storage.valkey.existingSecret, valkey.valkey.auth.usersExistingSecret, klausGateway.slack.secretName with an empty botToken, klausGateway.obo.existingSecret, model-manager/agent-manager oauth.existingSecret), or set gitops.forbidInlineSecrets: false" .) -}}
{{- end -}}
{{- end -}}
{{- end -}}

{{/*
muster.muster.toolsetPresets: a preset named like one of muster's built-ins
(read-only, none, full) makes the muster pod refuse to start, out of sight in
Flux. Fail the render here instead, naming the preset.
*/}}
{{- define "agent-platform.validateToolsetPresets" -}}
{{- $presets := dig "muster" "toolsetPresets" dict (.Values.muster | default dict) -}}
{{- $clash := list -}}
{{- range $name, $_ := $presets -}}
{{- if has $name (list "read-only" "none" "full") -}}
{{- $clash = append $clash $name -}}
{{- end -}}
{{- end -}}
{{- with $clash -}}
{{- fail (printf "muster.muster.toolsetPresets redefines %s, which is built into muster and cannot be redefined by configuration (the muster pod would refuse to start, naming it); rename the preset" (join ", " .)) -}}
{{- end -}}
{{- end -}}

{{/*
Name of the AgentgatewayParameters CR — defaults to release name.
*/}}
{{- define "agent-platform.parametersName" -}}
{{- default .Release.Name .Values.gateway.parameters.name -}}
{{- end -}}

{{/*
Truthy (emits "true") when the request topology routes through agentgateway,
i.e. ingress.mode is agentgateway-muster or agentgateway-direct. Otherwise
emits nothing (empty string = falsy). Gated templates use:
  {{- if (include "agent-platform.ingress.agentgateway" .) }}
*/}}
{{- define "agent-platform.ingress.agentgateway" -}}
{{- if or (eq .Values.ingress.mode "agentgateway-muster") (eq .Values.ingress.mode "agentgateway-direct") -}}true{{- end -}}
{{- end -}}

{{/*
Fully-qualified name of the muster service. Single source of truth: the umbrella
pins muster.fullnameOverride (see values.yaml), which the muster sub-chart uses
verbatim for its Service name. Reading that same key here — rather than
re-deriving the sub-chart's release-name naming algorithm — guarantees the
public route's backendRef and the agent-platform-mcps musterUrl always target
the real muster Service, and turns a misconfiguration into a loud render-time
failure instead of a silent 503.
*/}}
{{- define "agent-platform.musterFullname" -}}
{{- required "muster.fullnameOverride must be set — the umbrella owns muster's public route and its backendRef targets this exact Service name" .Values.muster.fullnameOverride -}}
{{- end -}}

{{/*
Port muster listens on; defaults to 8090 when unset from parent context.
*/}}
{{- define "agent-platform.musterServicePort" -}}
{{- .Values.muster.service.port | default 8090 -}}
{{- end -}}

{{/*
Merged HTTPRoute labels for a named route. The shared base
(ingress.httpRoute.labels) applies to every route; optional per-route overrides
(ingress.httpRoute.<route>.labels) win on key collision, letting a downstream
diverge one route without forking the whole block. Emits nothing when both are
empty. Usage:
  {{- include "agent-platform.httpRouteLabels" (dict "ctx" . "route" "muster") }}
*/}}
{{- define "agent-platform.httpRouteLabels" -}}
{{- $h := .ctx.Values.ingress.httpRoute -}}
{{- $merged := merge (deepCopy (dig .route "labels" dict $h)) ($h.labels | default dict) -}}
{{- with $merged }}{{- toYaml . }}{{- end -}}
{{- end -}}

{{/*
Merged HTTPRoute annotations for a named route — same precedence as
httpRouteLabels (per-route ingress.httpRoute.<route>.annotations override the
shared ingress.httpRoute.annotations). Emits nothing when both are empty.
*/}}
{{- define "agent-platform.httpRouteAnnotations" -}}
{{- $h := .ctx.Values.ingress.httpRoute -}}
{{- $merged := merge (deepCopy (dig .route "annotations" dict $h)) ($h.annotations | default dict) -}}
{{- with $merged }}{{- toYaml . }}{{- end -}}
{{- end -}}

{{/*
Validate the ingress.mode selector and the dependent toggles it implies.
Fails the render with an actionable message when the configuration is
inconsistent. Rendered exactly once via templates/validate.yaml.
*/}}
{{- define "agent-platform.validateIngress" -}}
{{- $mode := .Values.ingress.mode -}}
{{- if not (or (eq $mode "muster-direct") (eq $mode "agentgateway-muster") (eq $mode "agentgateway-direct")) -}}
{{- fail (printf "ingress.mode=%v is invalid; must be one of: muster-direct, agentgateway-muster, agentgateway-direct" $mode) -}}
{{- end -}}
{{- if eq $mode "agentgateway-direct" -}}
{{- fail "ingress.mode=agentgateway-direct requires a DCR-capable IdP (RFC 7591/8707), e.g. Zitadel; not yet supported" -}}
{{- end -}}
{{- $isAgentgateway := or (eq $mode "agentgateway-muster") (eq $mode "agentgateway-direct") -}}
{{- if not .Values.ingress.parentRefs -}}
{{- fail "ingress.parentRefs is required in all modes — the umbrella-owned muster `/` route (and the agentgateway `/mcp` route in agentgateway-* modes) attaches to it; an empty parentRefs renders a route bound to no Gateway, leaving muster unreachable while install reports success" -}}
{{- end -}}
{{- /* viaMuster only matters when the mcps sub-chart is installed; with no MCP
servers there is nothing to route, so the consistency check is scoped to the
agent-platform-mcps component. */ -}}
{{- if include "agent-platform.componentEnabled" (dict "root" . "name" "agent-platform-mcps") -}}
{{- $mcpsVals := index .Values "agent-platform-mcps" | default dict -}}
{{- $viaMuster := dig "agentgateway" "viaMuster" false $mcpsVals -}}
{{- if eq $mode "agentgateway-muster" -}}
{{- if not (or (eq $viaMuster true) (eq (toString $viaMuster) "true")) -}}
{{- fail "ingress.mode=agentgateway-muster requires agent-platform-mcps.agentgateway.viaMuster=true" -}}
{{- end -}}
{{- else if eq $mode "agentgateway-direct" -}}
{{- if not (or (eq $viaMuster false) (eq (toString $viaMuster) "false")) -}}
{{- fail "ingress.mode=agentgateway-direct requires agent-platform-mcps.agentgateway.viaMuster=false" -}}
{{- end -}}
{{- end -}}
{{- end -}}
{{- $agentgatewayEnabled := include "agent-platform.componentEnabled" (dict "root" . "name" "agentgateway") -}}
{{- if and $isAgentgateway (not $agentgatewayEnabled) -}}
{{- fail "components.agentgateway.enabled must be true in agentgateway-* modes; the controller dependency condition must match ingress.mode" -}}
{{- end -}}
{{- if and (eq $mode "muster-direct") $agentgatewayEnabled -}}
{{- fail "components.agentgateway.enabled must be false in muster-direct mode; the controller dependency condition must match ingress.mode" -}}
{{- end -}}
{{- end -}}

{{/*
Cilium DNS egress rule for kube-dns and node-local-dns.
CoreDNS is labeled k8s-app: kube-dns upstream (kubeadm) and k8s-app: coredns
on Giant Swarm clusters; match both so either fleet shape resolves.
Rendered as a YAML list item; the caller must provide the surrounding `egress:` key.
*/}}
{{- define "agent-platform.dnsEgress" -}}
- toEndpoints:
    - matchLabels:
        io.kubernetes.pod.namespace: kube-system
        k8s-app: kube-dns
    - matchLabels:
        io.kubernetes.pod.namespace: kube-system
        k8s-app: coredns
    - matchLabels:
        io.kubernetes.pod.namespace: kube-system
        k8s-app: k8s-dns-node-cache
  toPorts:
    - ports:
        - port: "1053"
          protocol: UDP
        - port: "1053"
          protocol: TCP
        - port: "53"
          protocol: UDP
        - port: "53"
          protocol: TCP
{{- end -}}

{{/*
=== Cluster shape ===

The knobs that describe what the cluster can admit — Kyverno policies, the
network-policy flavor, ServiceMonitors/PodMonitors, dicebear's Envoy route
filter, the agent-sandbox pod-security policy — accept `auto` (the default):
the object renders when its API group is served. `.Capabilities.APIVersions` is
the live discovery under helm-controller, the Helm CLI and `--dry-run=server`;
under `helm template` it is Helm's built-in set unless `--api-versions` names
more, so an offline render resolves every `auto` to the vanilla shape. An
explicit `true|false` (or `cilium|kubernetes`) always wins over detection.

The meta chart resolves each knob ONCE (agent-platform.shape.apply) before it
inlines a component's values, and derives the component-level copies from that
same answer, so a render can never hand one component the cilium flavor and
another the kubernetes one. The connectivity chart carries the same helpers
for renders without the meta chart; the meta chart forwards resolved values,
so the two cannot disagree on one cluster.
*/}}

{{/*
Resolve one `auto|true|false` knob to the string "true" or "false". `auto`
follows whether .api is served; an explicit boolean (or its string form from
--set-string) is returned as is; anything else fails the render naming .key.
Usage: include "agent-platform.shape.resolve" (dict "root" $ "key" "kyvernoPolicies.enabled" "value" .Values.kyvernoPolicies.enabled "api" "kyverno.io/v1")
*/}}
{{- define "agent-platform.shape.resolve" -}}
{{- $v := .value -}}
{{- if or (kindIs "invalid" $v) (and (kindIs "string" $v) (eq $v "auto")) -}}
{{- if .root.Capabilities.APIVersions.Has .api }}true{{ else }}false{{ end -}}
{{- else if kindIs "bool" $v -}}
{{- if $v }}true{{ else }}false{{ end -}}
{{- else if or (eq (toString $v) "true") (eq (toString $v) "false") -}}
{{- toString $v -}}
{{- else -}}
{{- fail (printf "%s must be one of auto, true, false (got %v)" .key $v) -}}
{{- end -}}
{{- end -}}

{{/*
kyvernoPolicies.enabled resolved: "true" when Kyverno policies render (auto:
kyverno.io/v1 served).
*/}}
{{- define "agent-platform.shape.kyvernoPolicies" -}}
{{- include "agent-platform.shape.resolve" (dict "root" . "key" "kyvernoPolicies.enabled" "value" .Values.kyvernoPolicies.enabled "api" "kyverno.io/v1") -}}
{{- end -}}

{{/*
networkPolicy.flavor resolved: "cilium" or "kubernetes" (auto: cilium when
cilium.io/v2 is served, else kubernetes).
*/}}
{{- define "agent-platform.shape.networkPolicyFlavor" -}}
{{- $f := .Values.networkPolicy.flavor -}}
{{- if or (kindIs "invalid" $f) (eq (toString $f) "auto") -}}
{{- if .Capabilities.APIVersions.Has "cilium.io/v2" }}cilium{{ else }}kubernetes{{ end -}}
{{- else if or (eq (toString $f) "cilium") (eq (toString $f) "kubernetes") -}}
{{- toString $f -}}
{{- else -}}
{{- fail (printf "networkPolicy.flavor must be one of auto, cilium, kubernetes (got %v)" $f) -}}
{{- end -}}
{{- end -}}

{{/*
global.observability.metrics.serviceMonitor.enabled resolved: "true" when the
monitor objects render (auto: monitoring.coreos.com/v1 served).
*/}}
{{- define "agent-platform.shape.serviceMonitor" -}}
{{- include "agent-platform.shape.resolve" (dict "root" . "key" "global.observability.metrics.serviceMonitor.enabled" "value" .Values.global.observability.metrics.serviceMonitor.enabled "api" "monitoring.coreos.com/v1") -}}
{{- end -}}

{{/*
dicebear.route.enabled resolved: "true" when the avatar HTTPRoute and its Envoy
Gateway HTTPRouteFilters render (auto: gateway.envoyproxy.io/v1alpha1 served).
*/}}
{{- define "agent-platform.shape.dicebearRoute" -}}
{{- include "agent-platform.shape.resolve" (dict "root" . "key" "dicebear.route.enabled" "value" (dig "route" "enabled" "auto" (.Values.dicebear | default dict)) "api" "gateway.envoyproxy.io/v1alpha1") -}}
{{- end -}}

{{/*
agentSandbox.podSecurity.enabled resolved: "true" when the agent-sandbox
pod-security ClusterPolicy renders. It is a Kyverno mutate policy, so `auto`
follows the RESOLVED kyvernoPolicies.enabled (an explicit
kyvernoPolicies.enabled: false switches it off with the rest; the
"podSecurity requires kyvernoPolicies" guard then never fires on auto).
*/}}
{{- define "agent-platform.shape.agentSandboxPodSecurity" -}}
{{- $v := dig "podSecurity" "enabled" "auto" (.Values.agentSandbox | default dict) -}}
{{- if or (kindIs "invalid" $v) (and (kindIs "string" $v) (eq $v "auto")) -}}
{{- include "agent-platform.shape.kyvernoPolicies" . -}}
{{- else -}}
{{- include "agent-platform.shape.resolve" (dict "root" . "key" "agentSandbox.podSecurity.enabled" "value" $v "api" "kyverno.io/v1") -}}
{{- end -}}
{{- end -}}

{{/*
Write .value into .values at .path (a list of keys) when the leaf there is
`auto`. A leaf that is absent or set explicitly is left alone — explicit
overrides win, and a block an operator emptied is not re-created. Emits nothing.
Usage: include "agent-platform.shape.derive" (dict "values" $v "path" (list "muster" "networkPolicy" "flavor") "value" "cilium")
*/}}
{{- define "agent-platform.shape.derive" -}}
{{- $cur := .values -}}
{{- $ok := true -}}
{{- range (initial .path) -}}
{{- if and $ok (kindIs "map" $cur) (hasKey $cur .) -}}
{{- $cur = index $cur . -}}
{{- else -}}
{{- $ok = false -}}
{{- end -}}
{{- end -}}
{{- if and $ok (kindIs "map" $cur) -}}
{{- $leaf := last .path -}}
{{- if and (hasKey $cur $leaf) (eq (toString (index $cur $leaf)) "auto") -}}
{{- $_ := set $cur $leaf .value -}}
{{- end -}}
{{- end -}}
{{- end -}}

{{/*
Resolve every cluster-shape knob in .values (a deep copy of .Values) IN PLACE,
once, before the component loop inlines them. Emits nothing.

The five knobs are written with their resolved value. The component-level
copies the standalone overlay used to flip by hand are derived from the same
answers, but only where the leaf is left at `auto`:
  networkPolicy.flavor      -> muster.networkPolicy.flavor,
                               valkey.ciliumNetworkPolicy.enabled (cilium only)
  serviceMonitor.enabled    -> muster.muster.observability.metrics.prometheus.serviceMonitor.enabled,
                               .prometheus.prometheusRule.enabled,
                               muster.muster.observability.grafanaDashboard.enabled
                               (the dashboard ConfigMap is only picked up by the
                               same observability platform),
                               kagent.oauth2-proxy.metrics.serviceMonitor.enabled,
                               kagent.otel.tracing.enabled / .logging.enabled (the
                               OTLP gateway they export to is part of that platform)
Two leaves have no `auto` form and are derived directly, off only:
  valkey.valkey.metrics.podMonitor.enabled — the valkey chart's own default is
      on; written false when monitors are off, left absent otherwise so the
      fleet's HelmRelease values are unchanged. An explicit value is kept.
  kagent.controller.env[name=OTEL_EXPORTER_OTLP_HEADERS] — the tenant header
      of the OTLP gateway; dropped when both kagent OTel exporters resolve off.
mcp-kubernetes' Cilium policy joins this list once mcp-kubernetes is a component.
Usage: include "agent-platform.shape.apply" (dict "root" $ "values" $shaped)
*/}}
{{- define "agent-platform.shape.apply" -}}
{{- $root := .root -}}
{{- $v := .values -}}
{{- $kyverno := eq (include "agent-platform.shape.kyvernoPolicies" $root) "true" -}}
{{- $flavor := include "agent-platform.shape.networkPolicyFlavor" $root -}}
{{- $monitors := eq (include "agent-platform.shape.serviceMonitor" $root) "true" -}}
{{- $dicebearRoute := eq (include "agent-platform.shape.dicebearRoute" $root) "true" -}}
{{- $podSecurity := eq (include "agent-platform.shape.agentSandboxPodSecurity" $root) "true" -}}
{{- /* The knobs themselves: written resolved whatever they held. */ -}}
{{- $_ := set $v.kyvernoPolicies "enabled" $kyverno -}}
{{- $_ := set $v.networkPolicy "flavor" $flavor -}}
{{- $_ := set $v.global.observability.metrics.serviceMonitor "enabled" $monitors -}}
{{- if kindIs "map" (dig "route" nil (index $v "dicebear" | default dict)) -}}
{{- $_ := set (index $v "dicebear" "route") "enabled" $dicebearRoute -}}
{{- end -}}
{{- if kindIs "map" (dig "podSecurity" nil (index $v "agentSandbox" | default dict)) -}}
{{- $_ := set (index $v "agentSandbox" "podSecurity") "enabled" $podSecurity -}}
{{- end -}}
{{- /* Derived component copies: only a leaf left at auto is written. */ -}}
{{- include "agent-platform.shape.derive" (dict "values" $v "path" (list "muster" "networkPolicy" "flavor") "value" $flavor) -}}
{{- include "agent-platform.shape.derive" (dict "values" $v "path" (list "valkey" "ciliumNetworkPolicy" "enabled") "value" (eq $flavor "cilium")) -}}
{{- include "agent-platform.shape.derive" (dict "values" $v "path" (list "muster" "muster" "observability" "metrics" "prometheus" "serviceMonitor" "enabled") "value" $monitors) -}}
{{- include "agent-platform.shape.derive" (dict "values" $v "path" (list "muster" "muster" "observability" "metrics" "prometheus" "prometheusRule" "enabled") "value" $monitors) -}}
{{- include "agent-platform.shape.derive" (dict "values" $v "path" (list "muster" "muster" "observability" "grafanaDashboard" "enabled") "value" $monitors) -}}
{{- include "agent-platform.shape.derive" (dict "values" $v "path" (list "kagent" "oauth2-proxy" "metrics" "serviceMonitor" "enabled") "value" $monitors) -}}
{{- include "agent-platform.shape.derive" (dict "values" $v "path" (list "kagent" "otel" "tracing" "enabled") "value" $monitors) -}}
{{- include "agent-platform.shape.derive" (dict "values" $v "path" (list "kagent" "otel" "logging" "enabled") "value" $monitors) -}}
{{- /* valkey PodMonitor: the chart's own default is on, so only "off" is written. */ -}}
{{- if not $monitors -}}
{{- $metrics := dig "valkey" "metrics" nil (index $v "valkey" | default dict) -}}
{{- if kindIs "map" $metrics -}}
{{- $pm := index $metrics "podMonitor" -}}
{{- if kindIs "invalid" $pm -}}
{{- $_ := set $metrics "podMonitor" (dict "enabled" false) -}}
{{- else if and (kindIs "map" $pm) (not (hasKey $pm "enabled")) -}}
{{- $_ := set $pm "enabled" false -}}
{{- end -}}
{{- end -}}
{{- end -}}
{{- /* kagent OTLP tenant header: gone when neither OTel exporter is on. */ -}}
{{- $kagent := index $v "kagent" | default dict -}}
{{- if kindIs "map" $kagent -}}
{{- $tracing := dig "otel" "tracing" "enabled" false $kagent -}}
{{- $logging := dig "otel" "logging" "enabled" false $kagent -}}
{{- $ctrl := index $kagent "controller" -}}
{{- if and (not $tracing) (not $logging) (kindIs "map" $ctrl) (kindIs "slice" (index $ctrl "env")) -}}
{{- $env := list -}}
{{- range (index $ctrl "env") -}}
{{- if not (and (kindIs "map" .) (eq (toString (index . "name")) "OTEL_EXPORTER_OTLP_HEADERS")) -}}
{{- $env = append $env . -}}
{{- end -}}
{{- end -}}
{{- $_ := set $ctrl "env" $env -}}
{{- end -}}
{{- end -}}
{{- end -}}

{{/*
Whether the bundled Flux engine is on — components.flux.enabled, read through
the same helper as every other roster entry (a missing entry counts as on, as
Helm treats a dependency whose condition path is absent). Emits "true" or "".
*/}}
{{- define "agent-platform.engineEnabled" -}}
{{- include "agent-platform.componentEnabled" (dict "root" . "name" "flux") -}}
{{- end -}}

{{/*
The tenant ServiceAccount the platform HelmReleases run under: the one the
flux-engine subchart renders (agent-platform-flux) whenever the engine is on,
nothing otherwise. gitops.serviceAccountName overrides it either way (see
components.yaml). The name is fixed on both sides — the subchart renders it,
this helper spells it — so the two cannot drift apart through a value.
*/}}
{{- define "agent-platform.tenantServiceAccountName" -}}
{{- if eq (include "agent-platform.engineEnabled" .) "true" -}}agent-platform-flux{{- end -}}
{{- end -}}

{{/*
Render guard of the bundled engine. Two refusals, both only with the engine on:

1. A cluster that already runs Flux. A second, locked-down helm-controller would
   watch every namespace and reconcile every HelmRelease in the cluster as the
   default account (measured), and the operator would take over the cluster's
   Flux CRDs. Foreign = an apps/v1 Deployment labelled
   app.kubernetes.io/component=helm-controller outside the release namespace
   (the label Flux's distribution and the operator's manifests stamp — the
   engine's own helm-controller lives in the release namespace), or a
   FluxInstance outside the release namespace (the engine's is `flux` in the
   release namespace; the FluxInstance kind is looked up only when the API is
   served, so a cluster without the operator CRDs is not an error). `lookup` is
   live under install/upgrade, --dry-run=server and helm-controller, and empty
   under `helm template`, where this guard is therefore silent.
2. gitops.namespace set to another namespace: the platform HelmReleases would
   then name a tenant ServiceAccount (agent-platform-flux) that exists only in
   the release namespace. The exempt-namespace layout is the fleet's, and the
   fleet runs with the engine off.

And one with the engine off: turning it off on an installation that runs it.
`helm upgrade` deletes the objects that left the manifest in one pass — the
operator together with the FluxInstance whose finalizer it processes — and
hangs like an unordered uninstall would; the pre-delete hooks do not run on an
upgrade. Looked up only when the FluxInstance API is served and gitops.namespace
is empty (a CLI installation with the engine never sets it, see 2.), so the
fleet's render — engine off, exempt namespace — makes no API call at all.
*/}}
{{- define "agent-platform.validateEngine" -}}
{{- if ne (include "agent-platform.engineEnabled" .) "true" -}}
{{- if and (not .Values.gitops.namespace) (.Capabilities.APIVersions.Has "fluxcd.controlplane.io/v1") -}}
{{- $own := lookup "fluxcd.controlplane.io/v1" "FluxInstance" .Release.Namespace "flux" -}}
{{- if and $own (eq (dig "metadata" "labels" "app.kubernetes.io/instance" "" $own) .Release.Name) -}}
{{- fail (printf "components.flux.enabled=false on an installation that runs the bundled Flux engine (FluxInstance %s/flux belongs to release %s): the upgrade would delete the operator together with the FluxInstance it finalizes and hang. Uninstall the release instead (helm uninstall --wait tears it down in order), or delete the FluxInstance first" .Release.Namespace .Release.Name) -}}
{{- end -}}
{{- end -}}
{{- else -}}
{{- $ns := .Values.gitops.namespace -}}
{{- if and $ns (ne $ns .Release.Namespace) -}}
{{- fail (printf "gitops.namespace=%s cannot be combined with the bundled Flux engine: the platform HelmReleases run as the tenant ServiceAccount agent-platform-flux, which the engine renders in the release namespace (%s). Leave gitops.namespace empty, or set components.flux.enabled=false on a cluster that runs its own Flux" $ns .Release.Namespace) -}}
{{- end -}}
{{- $foreign := list -}}
{{- range ((lookup "apps/v1" "Deployment" "" "").items | default list) -}}
{{- if and (eq (dig "metadata" "labels" "app.kubernetes.io/component" "" .) "helm-controller") (ne .metadata.namespace $.Release.Namespace) -}}
{{- $foreign = append $foreign (printf "Deployment %s/%s" .metadata.namespace .metadata.name) -}}
{{- end -}}
{{- end -}}
{{- if .Capabilities.APIVersions.Has "fluxcd.controlplane.io/v1" -}}
{{- range ((lookup "fluxcd.controlplane.io/v1" "FluxInstance" "" "").items | default list) -}}
{{- if ne .metadata.namespace $.Release.Namespace -}}
{{- $foreign = append $foreign (printf "FluxInstance %s/%s" .metadata.namespace .metadata.name) -}}
{{- end -}}
{{- end -}}
{{- end -}}
{{- with $foreign -}}
{{- fail (printf "this cluster runs Flux; set components.flux.enabled=false or install the chart through it (found %s)" (join ", " .)) -}}
{{- end -}}
{{- end -}}
{{- end -}}

{{/*
Names of the platform HelmReleases this chart renders (every enabled roster
entry with a chart), in roster order. The teardown hook deletes exactly these.
Usage: include "agent-platform.platformReleaseNames" . | fromYamlArray
*/}}
{{- define "agent-platform.platformReleaseNames" -}}
{{- $names := list -}}
{{- range $key, $c := .Values.components -}}
{{- if and (include "agent-platform.componentEnabled" (dict "root" $ "name" $key)) (hasKey $c "chart") -}}
{{- $names = append $names $c.chart -}}
{{- end -}}
{{- end -}}
{{- toYaml $names -}}
{{- end -}}
