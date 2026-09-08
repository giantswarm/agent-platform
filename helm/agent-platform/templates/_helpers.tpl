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
Fail the render when a component's on/off toggle is still set the old way, inside
the component's own values block. Those blocks are additionalProperties: true, so
a leftover `enabled` key validates and is then ignored — the component silently
falls back to the `components.<name>.enabled` default, which is off for five of
the six. This turns that into a loud failure naming the new key.
The probe is coalescing-safe: in a layout where a block feeds a real Helm
dependency (the standalone umbrella copies this helper), Helm coalesces that
chart's own top-level `enabled: true` default into the block once the dependency
is on (klaus-gateway ships one), so hasKey cannot tell an operator-set value from
the chart default. A legacy key is therefore reported when it is provably the
operator's: always while the component is off (a disabled dependency's block is
never coalesced), and while it is on when the value is an explicit false (the
only coalesced default is true) — exactly the case where the operator believes
the component is off while it runs anyway. On + true is indistinguishable from a
coalesced default and passes. The removed `mcps:` block needs no entry: the root
schema rejects it already.
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
{{- $block := index $.Values (first .) | default dict -}}
{{- if hasKey $block "enabled" -}}
{{- $on := include "agent-platform.componentEnabled" (dict "root" $ "name" (index (splitList "." (last .)) 1)) -}}
{{- if or (not $on) (not (index $block "enabled")) -}}
{{- $found = append $found (printf "%s.enabled -> %s" (first .) (last .)) -}}
{{- end -}}
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
