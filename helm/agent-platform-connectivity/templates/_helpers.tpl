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
{{- /*
The helm.sh/chart label: <name>-<version> as a valid label value. A label is at
most 63 characters and must end on an alphanumeric: Helm's `+` build metadata
(helm-controller appends the OCI digest to every chart version it installs,
`3.20.0+8c89e1be4cbf`) becomes `_`, and after the cut every trailing `-`, `.`
and `_` goes — a branch build's long prerelease version (abs:
`3.19.1-dev.<branch>.<date>.h<sha>`) made the cut land on the `_` once, and the
apiserver rejected every object of the release. tests/verify-labels.py.
*/ -}}
{{- define "chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimAll "-._" -}}
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
Whether a component has a release on this cluster — reads
`components.<name>.enabled`, the single on/off switch. The meta chart forwards
this map (see components: in values.yaml) with the same key path it reads
itself, so the two can never disagree. A component with no `enabled` key is
force-enabled. Emits "true" when on, empty string otherwise.
Usage: include "agent-platform.componentEnabled" (dict "root" $ "name" "kagent")
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
Whether an OPTIONAL component is on: "true" when `components.<name>` exists and
is enabled, empty otherwise — unlike componentEnabled, a name absent from the
roster is NOT force-on. For components a chart of this version may not carry
yet (the KServe control plane, model serving), so a guard can defer to them
without asserting they exist.
Usage: include "agent-platform.optionalComponentEnabled" (dict "root" $ "name" "kserve-resources")
*/}}
{{- define "agent-platform.optionalComponentEnabled" -}}
{{- if hasKey (.root.Values.components | default dict) .name -}}
{{- include "agent-platform.componentEnabled" . -}}
{{- end -}}
{{- end -}}

{{/*
The spec fields of a PodDisruptionBudget this chart renders from a knob of the
shape {enabled, minAvailable, maxUnavailable, unhealthyPodEvictionPolicy}
(agentManager.podDisruptionBudget, valkey.podDisruptionBudget,
vmManager.podDisruptionBudget, kagent.substrateWorkerPool.podDisruptionBudget),
with the knob's guards: exactly one of minAvailable / maxUnavailable (an int or a
percentage), and a policy inside the API's enum. The render fails naming the
knob (`path`) — a budget with both fields, or with neither, is refused by the
apiserver only on apply, long after a silent render. Rendered as YAML mapping
entries under `spec:`; the caller provides the indentation and the selector.
Usage: include "agent-platform.podDisruptionBudget.spec" (dict "path" "valkey.podDisruptionBudget" "pdb" .Values.valkey.podDisruptionBudget)
*/}}
{{- define "agent-platform.podDisruptionBudget.spec" -}}
{{- $pdb := .pdb -}}
{{- $hasMin := not (kindIs "invalid" $pdb.minAvailable) -}}
{{- $hasMax := not (kindIs "invalid" $pdb.maxUnavailable) -}}
{{- if and $hasMin $hasMax -}}
{{- fail (printf "%s sets both minAvailable and maxUnavailable; a PodDisruptionBudget takes exactly one" .path) -}}
{{- end -}}
{{- if not (or $hasMin $hasMax) -}}
{{- fail (printf "%s.enabled is true but neither minAvailable nor maxUnavailable is set; set exactly one" .path) -}}
{{- end -}}
{{- with $pdb.unhealthyPodEvictionPolicy -}}
{{- if not (has . (list "IfHealthyBudget" "AlwaysAllow")) -}}
{{- fail (printf "%s.unhealthyPodEvictionPolicy=%q is not a PodDisruptionBudget eviction policy (IfHealthyBudget, AlwaysAllow)" $.path .) -}}
{{- end -}}
{{- end -}}
{{- if $hasMin -}}
minAvailable: {{ $pdb.minAvailable }}
{{- else -}}
maxUnavailable: {{ $pdb.maxUnavailable }}
{{- end }}
{{- with $pdb.unhealthyPodEvictionPolicy }}
unhealthyPodEvictionPolicy: {{ . }}
{{- end }}
{{- end -}}

{{/*
The tenant identity of the agents' Flux HelmReleases: the ServiceAccount name
(kagent.fluxServiceAccountName) while the kagent component is on, "" otherwise.
ONE value, three consumers: templates/kagent/flux-service-account.yaml renders
the ServiceAccount and its RoleBinding from it, the meta chart derives
agent-manager's flux.helmReleaseServiceAccount from the same key, and the
portal's app-config (agentPlatform.fluxServiceAccountName) reads this helper.
Under a multi-tenancy lockdown (helm-controller with --no-cross-namespace-refs
and a rights-less default ServiceAccount; the Flux multi-tenancy admission
policy on Giant Swarm management clusters) a HelmRelease executes as the
ServiceAccount it names and fails without one.
Usage: include "agent-platform.kagent.fluxServiceAccountName" .
*/}}
{{- define "agent-platform.kagent.fluxServiceAccountName" -}}
{{- if (include "agent-platform.componentEnabled" (dict "root" . "name" "kagent")) -}}
{{- dig "fluxServiceAccountName" "" (.Values.kagent | default dict) -}}
{{- end -}}
{{- end -}}

{{/*
The kagent component's Helm release name — the value of the pods'
app.kubernetes.io/instance label the kagent chart stamps, and the name its
fullname helper falls back to when kagent.fullnameOverride is empty. The meta
chart names every component's release after its roster key (components.yaml:
`releaseName: <key>`; the kagent entry's key, chart and values block are all
`kagent`), and this chart is only ever installed by that meta chart. It is NOT
this chart's .Release.Name: under the retired standalone umbrella every subchart
shared one release name, so a selector on .Release.Name happened to match
kagent's pods; under the meta chart each component is its own release and such
a selector matches nothing (the kagent controller metrics Service had no
endpoints, giantswarm/agent-platform#305). Every selector, Service name or
hostname this chart derives for kagent's OWN objects goes through this helper
or agent-platform.kagent.fullname, never through .Release.Name.
Usage: include "agent-platform.kagent.releaseName" .
*/}}
{{- define "agent-platform.kagent.releaseName" -}}
kagent
{{- end -}}

{{/*
The kagent chart's fullname — what it prefixes its Services with
(`<fullname>-controller`, `<fullname>-ui`): kagent.fullnameOverride (pinned to
`kagent` in values.yaml), or, empty, the release name (the chart's fullname
helper collapses `<release>-<chart>` to the release name when the two match, as
they do under the meta chart).
Usage: include "agent-platform.kagent.fullname" .
*/}}
{{- define "agent-platform.kagent.fullname" -}}
{{- .Values.kagent.fullnameOverride | default (include "agent-platform.kagent.releaseName" .) -}}
{{- end -}}

{{/*
Fail the render when a component's on/off toggle is still set the old way, inside
the component's own values block. Those blocks are additionalProperties: true, so
a leftover `enabled` key validates and is then ignored — the component silently
falls back to the `components.<name>.enabled` default, which is off for five of
the six. This turns that into a loud failure naming the new key.
Neither this chart nor the meta chart has a Helm dependency, so no chart default
is ever coalesced into these blocks: a legacy key can only be the operator's and
is reported whatever its value, whether the component is on or off. (An umbrella
that feeds these blocks to real Helm dependencies sees klaus-gateway's own
`enabled: true` default coalesced in while that dependency is on and has to
special-case it; nothing here does.) The removed `mcps:` block needs no entry:
the root schema rejects it already.
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
Truthy when the agentgateway controller runs in this release: an agentgateway-*
ingress mode, or the component on with muster off — the serving slice on a
workload cluster (components.agentgateway.enabled: true, ingress.mode at its
muster-direct default, no edge Gateway), whose controller serves the models
Gateway and fetches its JWKS. The controller's network policies follow this,
not the ingress mode: a controller without its policy has no egress rule for
the issuer, and one with the edge's policy alone has no route to the public
issuer either. Usage:
  {{- if (include "agent-platform.agentgateway.controller" .) }}
*/}}
{{- define "agent-platform.agentgateway.controller" -}}
{{- if or (include "agent-platform.ingress.agentgateway" .) (include "agent-platform.componentEnabled" (dict "root" . "name" "agentgateway")) -}}true{{- end -}}
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
Port muster listens on; defaults to 8090. nil-safe: the muster service tree is
owned by the muster release now (not merged into this chart's values), so
.Values.muster.service may be unset.
*/}}
{{- define "agent-platform.musterServicePort" -}}
{{- dig "service" "port" 8090 (.Values.muster | default dict) -}}
{{- end -}}

{{/*
The in-cluster MCP URL of the platform's muster, the endpoint every agent's own
RemoteMCPServer targets: http://<muster Service>.<release namespace>.svc.cluster.local:<port>/mcp
while the muster component is on, "" otherwise. ONE helper, one consumer, one
name in both charts: the meta chart derives agent-manager's chart value
muster.url from its copy (componentDerivedValues, next to
flux.helmReleaseServiceAccount); agent-manager hands it to the Generic agent
chart 1.x as muster.url on every agent it composes and reports it in get_info.
The portal sends none (create_agent takes no muster argument), so the app-config
this chart renders carries no muster URL. The agent chart's own default is the
same URL on a default install (muster.fullnameOverride "muster", release
namespace agent-platform, port 8090) — the value exists so an installation
whose muster answers elsewhere changes it in one place.
Usage: include "agent-platform.musterMcpUrl" .
*/}}
{{- define "agent-platform.musterMcpUrl" -}}
{{- if (include "agent-platform.componentEnabled" (dict "root" . "name" "muster")) -}}
{{- printf "http://%s.%s.svc.cluster.local:%v/mcp" (include "agent-platform.musterFullname" .) .Release.Namespace (include "agent-platform.musterServicePort" .) -}}
{{- end -}}
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
{{- $musterEnabled := include "agent-platform.componentEnabled" (dict "root" . "name" "muster") -}}
{{- /* The muster `/` route needs a Gateway in every mode; the helper fails the
render when neither ingress.parentRefs, the chart-owned edge nor
global.gatewayApi.parentRefs names one — an empty result would render a route
bound to no Gateway, leaving muster unreachable while install reports success.
Only while muster is on: a release without muster (the serving slice,
examples/serving-slice.yaml) renders no route that could be left unbound and
has no edge of its own — its Gateway is the models Gateway (#490). */ -}}
{{- if $musterEnabled -}}
{{- $_ := include "agent-platform.parentRefs" (dict "ctx" . "override" .Values.ingress.parentRefs "key" "ingress.parentRefs") -}}
{{- end -}}
{{- /* viaMuster only matters when the mcps sub-chart is installed; with no MCP
servers there is nothing to route, so the consistency check is scoped to the
agent-platform-mcps component. */ -}}
{{- if (include "agent-platform.componentEnabled" (dict "root" . "name" "agent-platform-mcps")) -}}
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
{{- /* Without muster there is no muster ingress the agentgateway toggle has to
agree with: the serving slice on a workload cluster runs agentgateway (the
target has no controller of its own) in the default mode. */ -}}
{{- if and (eq $mode "muster-direct") $agentgatewayEnabled $musterEnabled -}}
{{- fail "components.agentgateway.enabled must be false in muster-direct mode; the controller dependency condition must match ingress.mode" -}}
{{- end -}}
{{- /* muster-direct runs without the agentgateway component, so its CRDs are
not on the cluster: anything that renders an agentgateway.dev object or attaches
to the agentgateway Gateway must fail here, naming the knob, instead of shipping
objects the API server rejects (the model-manager / agent-manager routes already
guard themselves the same way). */ -}}
{{- if eq $mode "muster-direct" -}}
{{- $mcpsValues := index .Values "agent-platform-mcps" | default dict -}}
{{- if and (include "agent-platform.componentEnabled" (dict "root" . "name" "agent-platform-mcps")) (dig "agentgateway" "enabled" false $mcpsValues) (dig "mcpServers" (list) $mcpsValues) -}}
{{- fail "muster-direct mode cannot serve the agentgateway.dev resources agent-platform-mcps renders per MCP server; set agent-platform-mcps.agentgateway.enabled=false to reach the MCP servers through muster" -}}
{{- end -}}
{{- if and (include "agent-platform.componentEnabled" (dict "root" . "name" "kagent")) .Values.kagent.controllerRoute.enabled -}}
{{- fail "kagent.controllerRoute renders agentgateway.dev resources on the agentgateway Gateway; it requires an agentgateway-* ingress.mode" -}}
{{- end -}}
{{- if and (include "agent-platform.componentEnabled" (dict "root" . "name" "klaus-gateway")) .Values.klausGateway.agentgatewayRoute.enabled -}}
{{- fail "klausGateway.agentgatewayRoute renders agentgateway.dev resources on the agentgateway Gateway; it requires an agentgateway-* ingress.mode" -}}
{{- end -}}
{{- end -}}
{{- end -}}

{{/*
global.domain, or a render failure naming the override key the caller could set
instead. Usage: include "agent-platform.domain" (dict "ctx" . "for" "ingress.hostnames")
*/}}
{{- define "agent-platform.domain" -}}
{{- required (printf "global.domain is empty and %s is not set: set global.domain (hostnames derive from it) or %s" .for .for) .ctx.Values.global.domain -}}
{{- end -}}

{{/*
A public hostname: the per-component override when set, else <prefix>.<global.domain>.
Usage: include "agent-platform.hostname" (dict "ctx" . "prefix" "kagent" "override" $h "key" "kagent.uiRoute.hostname")
*/}}
{{- define "agent-platform.hostname" -}}
{{- if .override -}}
{{- .override -}}
{{- else -}}
{{- printf "%s.%s" .prefix (include "agent-platform.domain" (dict "ctx" .ctx "for" .key)) -}}
{{- end -}}
{{- end -}}

{{/*
Truthy when the chart-owned agentgateway Gateway is also the public edge
(gatewayApi.gateway.create). Every public route then attaches to its HTTPS
listener, and the layer-1 routes that forward a front Gateway to the
agentgateway Service are not rendered: the data plane would proxy to itself.
*/}}
{{- define "agent-platform.edgeIsDataPlane" -}}
{{- if .Values.gatewayApi.gateway.create -}}true{{- end -}}
{{- end -}}

{{/*
The parentRefs of a public route, as a YAML list: the per-route override when
set, else the chart-owned edge Gateway, else global.gatewayApi.parentRefs.
The edge ref pins the route to the HTTPS listener via sectionName so the
plaintext :8080 listener never serves public hostnames through the edge's
LoadBalancer Service.
Usage: include "agent-platform.parentRefs" (dict "ctx" . "override" $list "key" "ingress.parentRefs")
*/}}
{{- define "agent-platform.parentRefs" -}}
{{- if .override -}}
{{- toYaml .override -}}
{{- else if (include "agent-platform.edgeIsDataPlane" .ctx) -}}
- name: {{ .ctx.Values.gateway.name }}
  namespace: {{ .ctx.Release.Namespace }}
  group: gateway.networking.k8s.io
  kind: Gateway
  sectionName: https
{{- else if .ctx.Values.global.gatewayApi.parentRefs -}}
{{- toYaml .ctx.Values.global.gatewayApi.parentRefs -}}
{{- else -}}
{{- fail (printf "no public Gateway for %s: set global.gatewayApi.parentRefs (the cluster's Gateway), or gatewayApi.gateway.create: true (the chart creates the edge), or %s" .key .key) -}}
{{- end -}}
{{- end -}}

{{/*
HTTPRoute rule timeouts shared by the umbrella-owned routes (ingress.httpRoute.timeouts).
Emits nothing when unset. Usage:
  {{- with (include "agent-platform.routeTimeouts" .) }}
  {{- . | nindent 6 }}
  {{- end }}
*/}}
{{- define "agent-platform.routeTimeouts" -}}
{{- with .Values.ingress.httpRoute.timeouts }}
timeouts:
  {{- toYaml . | nindent 2 }}
{{- end }}
{{- end -}}

{{/*
=== Cluster shape ===

The knobs that describe what the cluster can admit — Kyverno policies, the
network-policy flavor, ServiceMonitors/PodMonitors, the agent-sandbox
pod-security policy — accept `auto` (the default): the object renders when its
API group is served. `.Capabilities.APIVersions` is the live discovery under
helm-controller, the Helm CLI and `--dry-run=server`; under `helm template` it
is Helm's built-in set unless `--api-versions` names more, so an offline render
resolves every `auto` to the vanilla shape. An explicit `true|false` (or
`cilium|kubernetes`) always wins over detection.

Under the meta chart (agent-platform) these knobs arrive resolved: it detects
once with the same helpers and forwards concrete values, so the wiring rendered
here and every component's own copy come from one answer. The helpers below
are what a render of this chart on its own uses; on the same cluster they give
the same answer. Templates read the truthy wrappers underneath
(agent-platform.kyvernoPolicies, .networkPolicyFlavor, .serviceMonitor,
.agentSandboxPodSecurity, .modelServingPolicies), never the raw values.
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
modelServing.policies.enabled resolved: "true" when the model-serving Kyverno
cache policies render. They are Kyverno mutate policies, so `auto` follows the
RESOLVED kyvernoPolicies.enabled, like the agent-sandbox pod-security policy;
an explicit true with kyvernoPolicies.enabled false fails the render
(templates/model-serving/validate.yaml).
*/}}
{{- define "agent-platform.shape.modelServingPolicies" -}}
{{- $v := dig "policies" "enabled" "auto" (.Values.modelServing | default dict) -}}
{{- if or (kindIs "invalid" $v) (and (kindIs "string" $v) (eq $v "auto")) -}}
{{- include "agent-platform.shape.kyvernoPolicies" . -}}
{{- else -}}
{{- include "agent-platform.shape.resolve" (dict "root" . "key" "modelServing.policies.enabled" "value" $v "api" "kyverno.io/v1") -}}
{{- end -}}
{{- end -}}

{{/*
Truthy (emits "true") when the kyverno.io objects render. Gated templates use:
  {{- if (include "agent-platform.kyvernoPolicies" .) }}
*/}}
{{- define "agent-platform.kyvernoPolicies" -}}
{{- if eq (include "agent-platform.shape.kyvernoPolicies" .) "true" -}}true{{- end -}}
{{- end -}}

{{/*
The network-policy flavor every policy template branches on: "cilium" or
"kubernetes". Gated templates use:
  {{- if eq (include "agent-platform.networkPolicyFlavor" .) "cilium" }}
*/}}
{{- define "agent-platform.networkPolicyFlavor" -}}
{{- include "agent-platform.shape.networkPolicyFlavor" . -}}
{{- end -}}

{{/*
Truthy when the umbrella renders its ServiceMonitor / PodMonitor objects
(global.observability.metrics.serviceMonitor.enabled, default auto). The
per-component keys underneath (kagent.serviceMonitor.*) keep working.
*/}}
{{- define "agent-platform.serviceMonitor" -}}
{{- if eq (include "agent-platform.shape.serviceMonitor" .) "true" -}}true{{- end -}}
{{- end -}}

{{/*
Truthy when the agent-sandbox pod-security ClusterPolicy renders
(agentSandbox.podSecurity.enabled, default auto).
*/}}
{{- define "agent-platform.agentSandboxPodSecurity" -}}
{{- if eq (include "agent-platform.shape.agentSandboxPodSecurity" .) "true" -}}true{{- end -}}
{{- end -}}

{{/*
Truthy when the model-serving Kyverno cache policies render
(modelServing.policies.enabled, default auto).
*/}}
{{- define "agent-platform.modelServingPolicies" -}}
{{- if eq (include "agent-platform.shape.modelServingPolicies" .) "true" -}}true{{- end -}}
{{- end -}}

{{/*
OTEL exporter env for the agentgateway data-plane container, from
global.observability.traces.otlp. Emits nothing when the endpoint is empty.
Rendered as YAML list items.
*/}}
{{- define "agent-platform.otlpEnv" -}}
{{- with .Values.global.observability.traces.otlp }}
{{- if .endpoint }}
- name: OTEL_EXPORTER_OTLP_ENDPOINT
  value: {{ .endpoint | quote }}
{{- with .protocol }}
- name: OTEL_EXPORTER_OTLP_PROTOCOL
  value: {{ . | quote }}
{{- end }}
{{- if .headers }}
{{- $pairs := list }}
{{- range $key, $value := .headers }}
{{- $pairs = append $pairs (printf "%s=%s" $key $value) }}
{{- end }}
- name: OTEL_EXPORTER_OTLP_HEADERS
  value: {{ join "," $pairs | quote }}
{{- end }}
{{- end }}
{{- end }}
{{- end -}}

{{/*
global.identity.issuerUrl, or a render failure. Usage:
  include "agent-platform.issuerUrl" (dict "ctx" . "for" "the kagent JWT policy")
*/}}
{{- define "agent-platform.issuerUrl" -}}
{{- required (printf "global.identity.issuerUrl is empty but %s needs the login issuer" .for) .ctx.Values.global.identity.issuerUrl -}}
{{- end -}}

{{/*
Guards on the global.* contract. Rendered once via templates/validate.yaml.
*/}}
{{- define "agent-platform.validateGlobal" -}}
{{- if .Values.gatewayApi.gateway.create -}}
{{- if not (include "agent-platform.ingress.agentgateway" .) -}}
{{- fail "gatewayApi.gateway.create is true but ingress.mode is muster-direct: the chart-owned edge is the agentgateway data-plane Gateway, so set ingress.mode: agentgateway-muster and components.agentgateway.enabled: true" -}}
{{- end -}}
{{- if not .Values.gatewayApi.gateway.tls.secretName -}}
{{- fail "gatewayApi.gateway.create is true but gatewayApi.gateway.tls.secretName is empty: the HTTPS listener for *.<global.domain> needs the wildcard certificate Secret" -}}
{{- end -}}
{{- $_ := include "agent-platform.domain" (dict "ctx" . "for" "gatewayApi.gateway.create") -}}
{{- end -}}
{{- /* The muster chart reads its own OIDC keys; a value that disagrees with
global.identity would give two components two different logins. Checked only
where both sides are set, so installs that ignore global.identity are
untouched. */ -}}
{{- if .Values.muster.enabled -}}
{{- $server := dig "muster" "oauth" "server" dict (.Values.muster | default dict) -}}
{{- if and ($server.enabled | default false) .Values.global.identity.issuerUrl -}}
{{- $dex := $server.dex | default dict -}}
{{- if and $dex.issuerUrl (ne $dex.issuerUrl .Values.global.identity.issuerUrl) -}}
{{- fail (printf "muster.muster.oauth.server.dex.issuerUrl (%s) differs from global.identity.issuerUrl (%s); the platform has one login provider" $dex.issuerUrl .Values.global.identity.issuerUrl) -}}
{{- end -}}
{{- if and $dex.clientId .Values.global.identity.clientId (ne $dex.clientId .Values.global.identity.clientId) -}}
{{- fail (printf "muster.muster.oauth.server.dex.clientId (%s) differs from global.identity.clientId (%s)" $dex.clientId .Values.global.identity.clientId) -}}
{{- end -}}
{{- if and $server.existingSecret .Values.global.identity.existingSecret (ne $server.existingSecret .Values.global.identity.existingSecret) -}}
{{- fail (printf "muster.muster.oauth.server.existingSecret (%s) differs from global.identity.existingSecret (%s)" $server.existingSecret .Values.global.identity.existingSecret) -}}
{{- end -}}
{{- end -}}
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
Truthy (emits "true") when LLM routing is on, i.e. the chart renders the `llm`
listener and the routing resources that carry agent inference traffic to the
provider. Otherwise emits nothing (empty string = falsy). Gated templates use:
  {{- if (include "agent-platform.llmRouting" .) }}
*/}}
{{- define "agent-platform.llmRouting" -}}
{{- if .Values.llmRouting.enabled -}}true{{- end -}}
{{- end -}}

{{/*
In-cluster URL of the LLM listener on the data-plane Gateway. The host is
gateway.name: the agentgateway controller provisions the data-plane Service
under the Gateway's own name.
*/}}
{{- define "agent-platform.llmBaseUrl" -}}
{{- printf "http://%s.%s.svc:%d" .Values.gateway.name .Release.Namespace (.Values.llmRouting.listener.port | int) -}}
{{- end -}}

{{/*
The spec.provider of a kagent.modelConfigs[] entry, checked against the
ModelConfig CRD's enum (kagent.dev/v1alpha3). The enum is case-sensitive and
the API server refuses any other value at admission, after the render has said
nothing; failing here names the entry and the ten values instead. Takes the
entry.
*/}}
{{- define "agent-platform.modelConfigProvider" -}}
{{- $enum := list "Anthropic" "OpenAI" "AzureOpenAI" "Ollama" "Gemini" "GeminiVertexAI" "AnthropicVertexAI" "Bedrock" "SAPAICore" "Foundry" -}}
{{- $provider := required "kagent.modelConfigs[].provider is required" .provider -}}
{{- if not (has $provider $enum) -}}
{{- fail (printf "kagent.modelConfigs[].provider %q on %q is not a ModelConfig provider; the CRD's enum is %s (case-sensitive)" $provider .name (join ", " $enum)) -}}
{{- end -}}
{{- $provider -}}
{{- end -}}

{{/*
Key of the ModelConfigSpec provider block that carries baseUrl, for a
spec.provider value in any case (the CRD's spelling from a catalog entry, the
lower-cased agentgateway name from llmRouting.backend.provider). The key is not
the lower-cased provider name (openAI, sapAICore), and only three of the ten
providers have a baseUrl at all — Ollama names its host, AzureOpenAI and Foundry
an endpoint, the rest a region or a project: a block the CRD does not know is
pruned at admission and the model would keep the direct path in silence.
Emits nothing for every other provider (empty string = falsy).
*/}}
{{- define "agent-platform.modelConfigBaseUrlKey" -}}
{{- $keys := dict "anthropic" "anthropic" "openai" "openAI" "sapaicore" "sapAICore" -}}
{{- if hasKey $keys (lower .) -}}{{- index $keys (lower .) -}}{{- end -}}
{{- end -}}

{{/*
Key of the ModelConfigSpec provider block that carries promptCaching and
cacheTTL, for a spec.provider value in any case: Anthropic (anthropic) and
Bedrock (bedrock), the two providers whose adapters mark cache_control
breakpoints (kagent line 0.11.0-gs.15+, kagent-dev/kagent#2788). Emits nothing
for every other provider (empty string = falsy): the API server would prune the
fields at admission and the model would stay uncached in silence, so the
template refuses them instead.
*/}}
{{- define "agent-platform.modelConfigCacheKey" -}}
{{- $keys := dict "anthropic" "anthropic" "bedrock" "bedrock" -}}
{{- if hasKey $keys (lower .) -}}{{- index $keys (lower .) -}}{{- end -}}
{{- end -}}

{{/*
Name of the model-price ConfigMap — defaults to <release>-model-catalog.
*/}}
{{- define "agent-platform.modelCatalogName" -}}
{{- default (printf "%s-model-catalog" .Release.Name) .Values.llmRouting.modelCatalog.name -}}
{{- end -}}

{{/*
Truthy when the model-price ConfigMap is rendered and referenced: LLM routing
is on, the block is enabled, and it names at least one provider. An empty
provider map would mount an empty catalog, which reports NoCatalog on every
lookup — the same state as no ConfigMap at all, but with an object to explain.
*/}}
{{- define "agent-platform.modelCatalog" -}}
{{- if and (include "agent-platform.llmRouting" .) .Values.llmRouting.modelCatalog.enabled .Values.llmRouting.modelCatalog.providers -}}true{{- end -}}
{{- end -}}

{{/*
Validate the LLM routing block. Rendered exactly once via templates/validate.yaml.

The feature has no data plane of its own: it adds a listener to the
agentgateway Gateway and a policy the agentgateway controller reconciles. With
the component off (which includes the default ingress.mode: muster-direct) the
listener would exist in values only, kagent's base URL would point at nothing,
and every agent would lose inference at the cutover. Fail the render instead.
*/}}
{{- define "agent-platform.validateLlmRouting" -}}
{{- if (include "agent-platform.llmRouting" .) -}}
{{- if not (include "agent-platform.ingress.agentgateway" .) -}}
{{- fail (printf "llmRouting.enabled requires the agentgateway data plane: ingress.mode is %s, set it to agentgateway-muster" .Values.ingress.mode) -}}
{{- end -}}
{{- if not (include "agent-platform.componentEnabled" (dict "root" . "name" "agentgateway")) -}}
{{- fail "llmRouting.enabled requires components.agentgateway.enabled: true; the LLM listener is reconciled by the agentgateway controller" -}}
{{- end -}}
{{- $port := .Values.llmRouting.listener.port | int -}}
{{- range .Values.gateway.listeners -}}
{{- if eq (.port | int) $port -}}
{{- fail (printf "llmRouting.listener.port %d is already taken by the %s listener in gateway.listeners" $port .name) -}}
{{- end -}}
{{- end -}}
{{- /* An empty list renders a route that matches nothing; a bare "/" ties with
the agent-platform-mcps catch-all route on the same Gateway and loses the
tiebreak, so every inference call would reach the MCP backend instead. */ -}}
{{- if not .Values.llmRouting.pathPrefixes -}}
{{- fail "llmRouting.pathPrefixes must list at least one prefix; an empty list renders an LLM route that matches nothing" -}}
{{- end -}}
{{- range .Values.llmRouting.pathPrefixes -}}
{{- if eq . "/" -}}
{{- fail "llmRouting.pathPrefixes must be more specific than \"/\": the agent-platform-mcps catch-all route attaches to the same Gateway and wins an equal match, so every inference call would reach the MCP backend" -}}
{{- end -}}
{{- if not (hasPrefix "/" .) -}}
{{- fail (printf "llmRouting.pathPrefixes entry %q must start with /" .) -}}
{{- end -}}
{{- end -}}
{{- end -}}
{{- end -}}

{{/*
Guards on the platform Postgres Cluster's operator-supplied blocks. Both keys
reach the CNPG Cluster verbatim, where a wrong shape is a rejected apply with
the CRD's own message; these fail the render instead, naming the key.
*/}}
{{- define "agent-platform.validatePostgres" -}}
{{- range $i, $secret := .Values.postgres.imagePullSecrets -}}
{{- if not (dig "name" "" $secret) -}}
{{- fail (printf "postgres.imagePullSecrets[%d] has no name: every entry is {name: <Secret in the Cluster's namespace>}" $i) -}}
{{- end -}}
{{- end -}}
{{- $affinityKeys := list "additionalPodAffinity" "additionalPodAntiAffinity" "enablePodAntiAffinity" "nodeAffinity" "nodeSelector" "podAntiAffinityType" "tolerations" "topologyKey" -}}
{{- range $key, $_ := .Values.postgres.affinity -}}
{{- if has $key (list "podAffinity" "podAntiAffinity") -}}
{{- fail (printf "postgres.affinity.%s is a core Kubernetes Affinity key, which Cluster.spec.affinity rejects: it is CNPG's AffinityConfiguration. Use enablePodAntiAffinity, topologyKey and podAntiAffinityType for the operator's own anti-affinity, or additionalPodAffinity / additionalPodAntiAffinity to pass a core term through" $key) -}}
{{- else if not (has $key $affinityKeys) -}}
{{- fail (printf "postgres.affinity.%s is not a key of CNPG's AffinityConfiguration, which accepts %s" $key (join ", " $affinityKeys)) -}}
{{- end -}}
{{- end -}}
{{- end -}}

{{/*
Cilium DNS egress rule with the DNS proxy clause. Same selectors as dnsEgress,
plus `rules.dns` so Cilium learns the name -> address mappings the policy's
toFQDNs selectors need; without the clause a toFQDNs rule matches nothing on a
cluster that has no cluster-wide DNS visibility policy. Rendered as a YAML list
item; the caller must provide the surrounding `egress:` key.
*/}}
{{- define "agent-platform.dnsEgressWithProxy" -}}
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
      rules:
        dns:
          - matchPattern: "*"
{{- end -}}

{{/*
Kubernetes NetworkPolicy DNS egress rule (kube-dns / coredns / node-local cache
in kube-system on 53 and 1053), the kubernetes flavor of dnsEgress. Rendered as
a YAML list item; the caller must provide the surrounding `egress:` key.
*/}}
{{- define "agent-platform.dnsEgress.kubernetes" -}}
- to:
    - namespaceSelector:
        matchLabels:
          kubernetes.io/metadata.name: kube-system
      podSelector:
        matchExpressions:
          - key: k8s-app
            operator: In
            values: [kube-dns, coredns, k8s-dns-node-cache]
  ports:
    - port: 53
      protocol: UDP
    - port: 53
      protocol: TCP
    - port: 1053
      protocol: UDP
    - port: 1053
      protocol: TCP
{{- end -}}

{{/*
The host of an issuer URL (scheme and port stripped); empty when the URL is
empty or has no host. Usage: include "agent-platform.urlHost" $url
*/}}
{{- define "agent-platform.urlHost" -}}
{{- if . -}}
{{- $u := urlParse . -}}
{{- regexReplaceAll ":[0-9]+$" ($u.host | default "") "" -}}
{{- end -}}
{{- end -}}

{{/*
The hosts a platform service that validates tokens itself (mcp-oauth) reaches
at its login identity provider, as a JSON list: the discovery document, the
JWKS (validating the id_tokens muster and the portal forward), userinfo and
the token endpoint. The dex provider serves all of them from the issuer host.
Google spreads them over three hosts, none of which its issuer URL
(https://accounts.google.com) names: accounts.google.com (discovery,
authorization), www.googleapis.com (JWKS /oauth2/v3/certs, userinfo) and
oauth2.googleapis.com (token, revocation) — the endpoints mcp-oauth's google
provider dials. Empty for the dex provider without an issuer.
Usage: include "agent-platform.idpHosts" (dict "provider" "dex" "issuerUrl" $url)
*/}}
{{- define "agent-platform.idpHosts" -}}
{{- if eq (.provider | default "dex") "google" -}}
{{- list "accounts.google.com" "www.googleapis.com" "oauth2.googleapis.com" | toJson -}}
{{- else -}}
{{- $hosts := list -}}
{{- with (include "agent-platform.urlHost" .issuerUrl) }}{{- $hosts = list . -}}{{- end -}}
{{- $hosts | toJson -}}
{{- end -}}
{{- end -}}

{{/*
True when the portal's route attaches to the chart-owned data plane, in the
precedence agent-platform.parentRefs resolves the route's parents with:
`backstage.parentRefs` wins over gatewayApi.gateway.create, so a pinned route
keeps the front Gateway as its peer even while the chart owns the edge.
Usage: include "agent-platform.backstage.routeToDataPlane" .
*/}}
{{- define "agent-platform.backstage.routeToDataPlane" -}}
{{- $backstage := .Values.backstage | default dict -}}
{{- if not $backstage.parentRefs -}}
{{- include "agent-platform.edgeIsDataPlane" . -}}
{{- end -}}
{{- end -}}

{{/*
The edge the portal's app-config reaches by public hostname, as a JSON object
`{"dataPlane": bool, "namespaces": [...]}`: the parents of the routes that
serve those hostnames — muster (`ingress.parentRefs`), the kagent controller
and the model manager (their own `parentRef`, each counted only while its route
is enabled) — resolved in the precedence agent-platform.parentRefs uses. A
route that resolves to the chart-owned data plane sets `dataPlane`; every other
one contributes its Gateway's namespace. `backstage.parentRefs` is not among
them: it moves the portal's own route, not the routes the portal calls.
Usage: include "agent-platform.backstage.appConfigEdge" . | fromJson
*/}}
{{- define "agent-platform.backstage.appConfigEdge" -}}
{{- $global := .Values.global.gatewayApi.parentRefs | default list -}}
{{- $overrides := list (.Values.ingress.parentRefs | default list) -}}
{{- $kagentRoute := dig "controllerRoute" dict (.Values.kagent | default dict) -}}
{{- if and $kagentRoute.enabled (dig "parentRef" "name" "" $kagentRoute) -}}
{{- $overrides = append $overrides (list $kagentRoute.parentRef) -}}
{{- else if $kagentRoute.enabled -}}
{{- $overrides = append $overrides list -}}
{{- end -}}
{{- $mmRoute := dig "route" dict (.Values.modelManager | default dict) -}}
{{- if and $mmRoute.enabled (dig "parentRef" "name" "" $mmRoute) -}}
{{- $overrides = append $overrides (list $mmRoute.parentRef) -}}
{{- else if $mmRoute.enabled -}}
{{- $overrides = append $overrides list -}}
{{- end -}}
{{- $dataPlane := false -}}
{{- $namespaces := list -}}
{{- range $refs := $overrides -}}
{{- if $refs -}}
{{- range $refs -}}
{{- $namespaces = append $namespaces (dig "namespace" "" . | default $.Release.Namespace) -}}
{{- end -}}
{{- else if (include "agent-platform.edgeIsDataPlane" $) -}}
{{- $dataPlane = true -}}
{{- else -}}
{{- range $global -}}
{{- $namespaces = append $namespaces (dig "namespace" "" . | default $.Release.Namespace) -}}
{{- end -}}
{{- end -}}
{{- end -}}
{{- dict "dataPlane" $dataPlane "namespaces" ($namespaces | uniq | sortAlpha) | toJson -}}
{{- end -}}

{{/*
The namespaces of the front Gateway the portal's route attaches to, as a JSON
list, in the order the route names its parents: `backstage.parentRefs` when the
route is pinned, else `global.gatewayApi.parentRefs`. A parentRef without a
namespace attaches to a Gateway of the route's own namespace (Gateway API), so
that is what an absent key means here too. Empty when the route attaches to the
chart-owned data plane — the data plane is then the peer, selected by the
Gateway's own name.
Usage: include "agent-platform.backstage.edgeNamespaces" . | fromJsonArray
*/}}
{{- define "agent-platform.backstage.edgeNamespaces" -}}
{{- $backstage := .Values.backstage | default dict -}}
{{- $refs := $backstage.parentRefs | default .Values.global.gatewayApi.parentRefs | default list -}}
{{- $namespaces := list -}}
{{- if not (include "agent-platform.backstage.routeToDataPlane" .) -}}
{{- range $refs -}}
{{- $namespaces = append $namespaces (dig "namespace" $.Release.Namespace .) -}}
{{- end -}}
{{- end -}}
{{- $namespaces | uniq | toJson -}}
{{- end -}}

{{/*
Cilium egress rules from a platform service to the login identity provider:
the hosts of agent-platform.idpHosts (discovery, JWKS, userinfo, token) by
name on 443 (through the DNS proxy rule the caller renders), plus the cluster
entity on 443 and 10443 — an issuer served through an in-cluster Gateway whose
LoadBalancer address Cilium translates to the data-plane pods (the Envoy edge
listens on 10443), or an in-cluster issuer Service, is `cluster` at policy
time, not the name. Mirrors the klaus-gateway OBO and oauth2-proxy egress.
Rendered as YAML list items; the caller provides `egress:` and the indentation.
Usage: include "agent-platform.idpEgress.cilium" (dict "provider" "dex" "issuerUrl" $url)
*/}}
{{- define "agent-platform.idpEgress.cilium" -}}
{{- with (include "agent-platform.idpHosts" . | fromJsonArray) }}
- toFQDNs:
    {{- range . }}
    - matchName: {{ . }}
    {{- end }}
  toPorts:
    - ports:
        - port: "443"
          protocol: TCP
{{- end }}
- toEntities:
    - cluster
  toPorts:
    - ports:
        - port: "443"
          protocol: TCP
        - port: "10443"
          protocol: TCP
{{- end -}}

{{/*
A JWKS host in the one form every classifier and every selector reads: lower
case, with the root label's trailing dot removed. DNS names are
case-insensitive, and a Cilium toFQDNs matchName is matched in this form too.
Usage: include "agent-platform.jwks.normalizeHost" $host
*/}}
{{- define "agent-platform.jwks.normalizeHost" -}}
{{- . | toString | lower | trimSuffix "." -}}
{{- end -}}

{{/*
Truthy ("true") when a JWKS host is served from inside the cluster, so
gateway.jwksEgress covers it and no external rule is needed: a qualified Service
name, whose third dot-separated label is `svc` and whose remaining labels are
empty, `cluster` or `cluster.local`. Every other host is external, a public name
that carries an `svc` label elsewhere (a.b.svc.example.com) included.
Usage: include "agent-platform.jwks.inCluster" $host
*/}}
{{- define "agent-platform.jwks.inCluster" -}}
{{- $labels := splitList "." (include "agent-platform.jwks.normalizeHost" .) -}}
{{- if ge (len $labels) 3 -}}
{{- if eq (index $labels 2) "svc" -}}
{{- $tail := join "." (slice $labels 3 (len $labels)) -}}
{{- if or (eq $tail "") (eq $tail "cluster") (eq $tail "cluster.local") -}}true{{- end -}}
{{- end -}}
{{- end -}}
{{- end -}}

{{/*
Truthy ("true") when a JWKS host is an address literal rather than a name: four
all-digit labels, or any host carrying a colon. No FQDN selector matches an
address, and this chart selects external issuers by name only, so the render
guard refuses one and points at gateway.jwksEgress.external.cidrs.
Usage: include "agent-platform.jwks.addrLiteral" $host
*/}}
{{- define "agent-platform.jwks.addrLiteral" -}}
{{- $host := include "agent-platform.jwks.normalizeHost" . | trimPrefix "[" | trimSuffix "]" -}}
{{- if or (contains ":" $host) (regexMatch "^[0-9]+([.][0-9]+){3}$" $host) -}}true{{- end -}}
{{- end -}}

{{/*
Truthy ("true") when a JWKS host carries a port (dex.example.com:5556,
[2001:db8::1]:443), the one malformed shape with an obvious repair: the port
belongs in jwks.port.
Usage: include "agent-platform.jwks.hostCarriesPort" $host
*/}}
{{- define "agent-platform.jwks.hostCarriesPort" -}}
{{- $host := include "agent-platform.jwks.normalizeHost" . -}}
{{- if or (regexMatch "^\\[[0-9A-Fa-f:.]+\\]:[0-9]+$" $host) (regexMatch "^[^:]+:[0-9]+$" $host) -}}true{{- end -}}
{{- end -}}

{{/*
Truthy ("true") when a JWKS host is not a valid hostname: a scheme or a path
glued on (accounts.google.com/keys), an empty label (a..b.example.com), a label
that starts or ends with a hyphen, a character no hostname carries, or a name
past 253 characters. Such a host resolves nothing and the matchName it renders
selects a name DNS never answers. Addresses are left to addrLiteral and
hostCarriesPort, which name their own repair. Underscores pass — Cilium's
matchName grammar carries them and no Service name can.
Usage: include "agent-platform.jwks.malformedName" $host
*/}}
{{- define "agent-platform.jwks.malformedName" -}}
{{- $host := include "agent-platform.jwks.normalizeHost" . -}}
{{- if not (include "agent-platform.jwks.addrLiteral" $host) -}}
{{- $label := "[a-z0-9_]([a-z0-9_-]{0,61}[a-z0-9_])?" -}}
{{- if or (gt (len $host) 253) (not (regexMatch (printf "^%s([.]%s)*$" $label $label) $host)) -}}true{{- end -}}
{{- end -}}
{{- end -}}

{{/*
Truthy ("true") when the controller must originate TLS to fetch a route's JWKS:
jwks.tls.enabled, or port 443, which serves no plain HTTP. Without it the fetch
speaks plain HTTP to a TLS endpoint and the controller holds no keys, which
reaches every caller as `401 token uses the unknown key`. The port wins:
jwks.tls.enabled false on 443 still originates TLS.
Usage: include "agent-platform.jwks.tlsEnabled" $jwks
*/}}
{{- define "agent-platform.jwks.tlsEnabled" -}}
{{- $jwks := . | default dict -}}
{{- if or ($jwks.tls).enabled (eq ($jwks.port | default 443 | int) 443) -}}true{{- end -}}
{{- end -}}

{{/*
The CA Secret the JWKS backend verifies the issuer's certificate against, empty
for the system trust of the agentgateway controller, which fetches the key set.
jwks.tls.caSecretName when set; otherwise global.identity.ca.secretName, but
only while jwks.tls.enabled asks for TLS.
The global key is the CA of ONE identity provider — the platform's — so it is
the right default only for a route pointed at that provider deliberately. TLS
implied by port 443 carries no such statement: a public issuer verified against
a private CA fails the fetch, which is the failure the implied-TLS rule removes.
Usage: include "agent-platform.jwks.caSecretName" (dict "ctx" . "jwks" $jwks)
*/}}
{{- define "agent-platform.jwks.caSecretName" -}}
{{- $jwks := .jwks | default dict -}}
{{- $ca := ($jwks.tls).caSecretName -}}
{{- if and (not $ca) ($jwks.tls).enabled -}}
{{- $ca = dig "identity" "ca" "secretName" "" .ctx.Values.global -}}
{{- end -}}
{{- $ca -}}
{{- end -}}

{{/*
The render guards on one route's jwtAuthentication.jwks. Each failure they catch
otherwise surfaces at runtime as `401 token uses the unknown key` on every
request, because the controller fetches no keys.

Always, because a host or port of the wrong shape reaches nothing with or
without a network policy: an empty host, an empty port, a host that carries its
port, an address literal, and a host that is no valid hostname.

Only under networkPolicy.enabled, which is what renders the controller policy
that carries the egress. With no policy rendered every destination is reachable
and neither key decides anything: a host of fewer than three labels, which is
neither a qualified Service name nor a public issuer; an in-cluster host while
gateway.jwksEgress is off, so nothing opens its port; and an in-cluster host in
a namespace or on a port gateway.jwksEgress does not name, so the one rule it
renders reaches a different endpoint. The last one stands down for a host
reached by address: gateway.jwksEgress.external.cidrs non-empty on
external.port equal to the route's jwks.port, which is the chart's one way to
carry a second in-cluster issuer.
Usage: include "agent-platform.jwks.validate" (dict "ctx" . "path" "kagent.controllerRoute.jwtAuthentication" "jwks" $jwks)
*/}}
{{- define "agent-platform.jwks.validate" -}}
{{- $ctx := .ctx -}}
{{- $jwks := .jwks | default dict -}}
{{- $host := $jwks.host | default "" -}}
{{- if not $host -}}
{{- fail (printf "%s.enabled is true but %s.jwks.host is empty. The JWKS backend renders no host, the controller fetches no keys and signature validation fails closed. Set %s.jwks.host to the issuer's JWKS host." .path .path .path) -}}
{{- end -}}
{{- if not $jwks.port -}}
{{- fail (printf "%s.enabled is true but %s.jwks.port is empty. The JWKS backend renders no port, the API server refuses it and the controller fetches no keys, so signature validation fails closed. Set %s.jwks.port to the port the issuer serves its JWKS on (443 for a public issuer, 5556 for the in-cluster Dex)." .path .path .path) -}}
{{- end -}}
{{- if include "agent-platform.jwks.hostCarriesPort" $host -}}
{{- fail (printf "%s.jwks.host is %q, which carries a port. The host and the port are separate keys, and both the JWKS backend and the controller's egress rule are built from the two. Set %s.jwks.host to the name alone and %s.jwks.port to the port." .path $host .path .path) -}}
{{- end -}}
{{- if include "agent-platform.jwks.addrLiteral" $host -}}
{{- fail (printf "%s.jwks.host is %q, an address literal. The controller selects an external issuer by name, so no rule reaches an address named here and signature validation fails closed. Set %s.jwks.host to the issuer's hostname; to reach it by address, name its blocks in gateway.jwksEgress.external.cidrs and open them on gateway.jwksEgress.external.port." .path $host .path) -}}
{{- end -}}
{{- if include "agent-platform.jwks.malformedName" $host -}}
{{- fail (printf "%s.jwks.host is %q, which is not a valid hostname: every dot-separated label is 1 to 63 characters of letters, digits, `-` or `_`, and neither starts nor ends with `-`. The JWKS backend resolves nothing and the controller's egress rule selects a name DNS never answers, so signature validation fails closed. Set %s.jwks.host to the issuer's host alone, with no scheme and no path; the JWKS path belongs in %s.jwks.path." .path $host .path .path) -}}
{{- end -}}
{{- if $ctx.Values.networkPolicy.enabled -}}
{{- $egress := $ctx.Values.gateway.jwksEgress -}}
{{- if lt (len (splitList "." (include "agent-platform.jwks.normalizeHost" $host))) 3 -}}
{{- fail (printf "%s.jwks.host is %q, which has fewer than three labels: it is neither a qualified Service name nor a public issuer. A short Service name resolves through the pod's search path, which the controller's egress rule cannot follow, so the controller would hold no keys and signature validation would fail closed. Write the Service's qualified name (<service>.<namespace>.svc.cluster.local) for an in-cluster issuer, or the issuer's full host for a public one." .path $host) -}}
{{- end -}}
{{- if and (include "agent-platform.jwks.inCluster" $host) (not $egress.enabled) -}}
{{- fail (printf "%s.enabled is true with an in-cluster jwks.host (%q) but gateway.jwksEgress.enabled is false. The agentgateway controller cannot reach the JWKS endpoint to fetch the keys, so signature validation fails closed. Set gateway.jwksEgress.enabled: true (and its namespace/port to the issuer's)." .path $host) -}}
{{- end -}}
{{- if include "agent-platform.jwks.inCluster" $host -}}
{{- /* gateway.jwksEgress renders one rule, for one namespace on one port. An
in-cluster host is qualified, so its second label is the namespace it resolves
in and a mismatch is decidable here rather than at runtime. An external.cidrs
block opened on the route's own port is the operator's statement that those
addresses are this issuer's, so it stands in for the rule and both checks step
aside. */ -}}
{{- $ns := index (splitList "." (include "agent-platform.jwks.normalizeHost" $host)) 1 -}}
{{- $egressNs := $egress.namespace | toString -}}
{{- $egressPort := $egress.port | int -}}
{{- $port := $jwks.port | int -}}
{{- $external := $egress.external | default dict -}}
{{- $byAddress := and ($external.cidrs | default list) (eq ($external.port | default 443 | int) $port) -}}
{{- if not $byAddress -}}
{{- if ne $ns $egressNs -}}
{{- fail (printf "%s.jwks.host is %q, which resolves in namespace %q, but gateway.jwksEgress.namespace is %q. The controller's only in-cluster JWKS rule opens the namespace that key names, so the fetch is denied and signature validation fails closed. Set gateway.jwksEgress.namespace: %s. This chart carries one in-cluster issuer: if another route already names an in-cluster host in a different namespace, reach this one by address instead, with its pod blocks in gateway.jwksEgress.external.cidrs on gateway.jwksEgress.external.port: %d." .path $host $ns $egressNs $ns $port) -}}
{{- end -}}
{{- if ne $port $egressPort -}}
{{- fail (printf "%s.jwks.port is %d but gateway.jwksEgress.port is %d. The controller's only in-cluster JWKS rule opens the port that key names, so the fetch is denied and signature validation fails closed. Set gateway.jwksEgress.port: %d." .path $port $egressPort $port) -}}
{{- end -}}
{{- end -}}
{{- end -}}
{{- end -}}
{{- end -}}

{{/*
The platform's identity provider as a JWKS target, { "host": "<host>", "port":
<int> } from global.identity.issuerUrl (the URL's port, else 443 — the port the
models Gateway's default JWKS source dials); empty while the URL is unset.
Usage: include "agent-platform.jwks.issuerTarget" .
*/}}
{{- define "agent-platform.jwks.issuerTarget" -}}
{{- with .Values.global.identity.issuerUrl -}}
{{- $u := urlParse . -}}
{{- $port := regexFind ":[0-9]+$" ($u.host | default "") | trimPrefix ":" | default "443" | int -}}
{{- with $u.hostname -}}
{{- dict "host" . "port" $port | toJson -}}
{{- end -}}
{{- end -}}
{{- end -}}

{{/*
The external JWKS endpoints the agentgateway controller fetches, as a JSON list
of { "host": "<host>", "port": <int> }, deduplicated on host and port. The
controller fetches the JWKS of every jwtAuthentication policy of its
GatewayClass and pushes the keys to the data plane over xDS (a failed fetch
pushes an empty key set: every token is refused as "unknown key"), so its
egress needs each of them; the routes already name host and port, and no
second knob restates them. A route contributes while its policy renders: the
component, its route and its jwtAuthentication are all on. The platform's
identity provider (global.identity.issuerUrl on 443) contributes whatever the
routes name: the controller serves the JWT policies of every release in the
cluster, and the serving slice's models policy beside the platform's release
(giantswarm/agent-platform#505) takes the issuer's public host on 443 by
default — a release the platform's controller policy cannot see. In-cluster
hosts are absent — gateway.jwksEgress covers those. Each host is normalized,
the form a Cilium toFQDNs matchName is matched in.
*/}}
{{- define "agent-platform.jwks.externalTargets" -}}
{{- $out := list -}}
{{- $seen := dict -}}
{{- $sources := list -}}
{{- with (include "agent-platform.jwks.issuerTarget" .) -}}
{{- $sources = append $sources (. | fromJson) -}}
{{- end -}}
{{- if and (include "agent-platform.componentEnabled" (dict "root" . "name" "kagent")) (.Values.kagent.controllerRoute).enabled (.Values.kagent.controllerRoute.jwtAuthentication).enabled -}}
{{- $sources = append $sources .Values.kagent.controllerRoute.jwtAuthentication.jwks -}}
{{- end -}}
{{- if and (include "agent-platform.modelManager.enabled" .) (.Values.modelManager.route).enabled (.Values.modelManager.route.jwtAuthentication).enabled -}}
{{- $sources = append $sources .Values.modelManager.route.jwtAuthentication.jwks -}}
{{- end -}}
{{- if and (include "agent-platform.agentManager.enabled" .) (.Values.agentManager.route).enabled (.Values.agentManager.route.jwtAuthentication).enabled -}}
{{- $sources = append $sources .Values.agentManager.route.jwtAuthentication.jwks -}}
{{- end -}}
{{- if (include "agent-platform.modelServing.modelsGateway.enabled" .) -}}
{{- $sources = append $sources (include "agent-platform.modelServing.modelsGateway.jwks" . | fromJson) -}}
{{- end -}}
{{- range $sources -}}
{{- $host := include "agent-platform.jwks.normalizeHost" (.host | default "") -}}
{{- $port := .port | default 443 | int -}}
{{- $key := printf "%s:%d" $host $port -}}
{{- if and $host (not (include "agent-platform.jwks.inCluster" $host)) (not (hasKey $seen $key)) -}}
{{- $seen = set $seen $key true -}}
{{- $out = append $out (dict "host" $host "port" $port) -}}
{{- end -}}
{{- end -}}
{{- $out | toJson -}}
{{- end -}}

{{/*
The distinct ports of agent-platform.jwks.externalTargets, as a JSON list of
ints. The kubernetes flavour has no FQDN selector, so those hosts share one
address-block rule and this is its port list.
*/}}
{{- define "agent-platform.jwks.externalPorts" -}}
{{- $ports := list -}}
{{- range (include "agent-platform.jwks.externalTargets" . | fromJsonArray) -}}
{{- $ports = append $ports (.port | int) -}}
{{- end -}}
{{- $ports | uniq | toJson -}}
{{- end -}}

{{/*
Postgres backup helpers (templates/postgres/*). backupEnabled is non-empty when
the Cluster renders AND postgres.backup.enabled is set.
*/}}
{{- define "agent-platform.postgres.backupEnabled" -}}
{{- if and .Values.postgres.enabled .Values.postgres.backup.enabled -}}true{{- end -}}
{{- end -}}

{{/* "aws" or "azure" while postgres.backup.crossplane renders the store, else "". */}}
{{- define "agent-platform.postgres.crossplane" -}}
{{- $b := .Values.postgres.backup -}}
{{- if and (include "agent-platform.postgres.backupEnabled" .) (eq $b.method "plugin") $b.crossplane.enabled -}}
{{- $b.crossplane.provider -}}
{{- end -}}
{{- end -}}

{{/* The ObjectStore the Cluster's plugin entry names. */}}
{{- define "agent-platform.postgres.objectStoreName" -}}
{{- .Values.postgres.backup.objectStore.existingName | default (printf "%s-backup" .Values.postgres.clusterName) -}}
{{- end -}}

{{/* The Secret the Crossplane Azure Account writes its connection strings to. */}}
{{- define "agent-platform.postgres.azureAccountSecret" -}}
{{- printf "%s-backup-store" .Values.postgres.clusterName -}}
{{- end -}}

{{/* arn:aws, or arn:aws-cn in the China partition. */}}
{{- define "agent-platform.postgres.awsPartition" -}}
{{- include "agent-platform.crossplane.awsPartition" (dict "xp" .Values.postgres.backup.crossplane) -}}
{{- end -}}

{{/* The IAM role the Crossplane AWS block renders. */}}
{{- define "agent-platform.postgres.awsRoleName" -}}
{{- $aws := .Values.postgres.backup.crossplane.aws -}}
{{- $aws.roleName | default $aws.bucketName -}}
{{- end -}}

{{- define "agent-platform.postgres.awsRoleArn" -}}
{{- printf "%s:iam::%s:role/%s" (include "agent-platform.postgres.awsPartition" .) .Values.postgres.backup.crossplane.aws.accountId (include "agent-platform.postgres.awsRoleName" .) -}}
{{- end -}}

{{/*
destinationPath: the explicit value, else derived from the Crossplane store
(s3://<bucket>/ or https://<account>.blob.core.windows.net/<container>/).
*/}}
{{- define "agent-platform.postgres.destinationPath" -}}
{{- $b := .Values.postgres.backup -}}
{{- $xp := include "agent-platform.postgres.crossplane" . -}}
{{- if $b.objectStore.destinationPath -}}
{{- $b.objectStore.destinationPath -}}
{{- else if eq $xp "aws" -}}
{{- printf "s3://%s/" $b.crossplane.aws.bucketName -}}
{{- else if eq $xp "azure" -}}
{{- printf "https://%s.blob.core.windows.net/%s/" $b.crossplane.azure.storageAccountName $b.crossplane.azure.containerName -}}
{{- end -}}
{{- end -}}

{{/*
Annotations for the Cluster's ServiceAccount: the values, plus the IRSA role
annotation when the Crossplane AWS block renders the role. YAML map or "".
*/}}
{{- define "agent-platform.postgres.serviceAccountAnnotations" -}}
{{- $ann := deepCopy (.Values.postgres.backup.serviceAccount.annotations | default dict) -}}
{{- if eq (include "agent-platform.postgres.crossplane" .) "aws" -}}
{{- $_ := set $ann "eks.amazonaws.com/role-arn" (include "agent-platform.postgres.awsRoleArn" .) -}}
{{- end -}}
{{- if $ann -}}{{- toYaml $ann -}}{{- end -}}
{{- end -}}

{{/*
Crossplane helpers shared by every store this chart provisions (the kagent-pg
backup bucket, postgres.backup.crossplane; Agent Substrate's snapshot store,
kagent.harness.snapshotStore.crossplane). Each takes the store's crossplane
block as `xp`; the postgres.* and substrateStore.* wrappers below bind it.
*/}}

{{/* Crossplane managementPolicies: everything, or Observe only. */}}
{{- define "agent-platform.crossplane.managementPolicies" -}}
{{- if .xp.observeOnly -}}
- Observe
{{- else -}}
- "*"
{{- end -}}
{{- end -}}

{{/* Same, for data-bearing objects: never Delete, so an uninstall keeps the data. */}}
{{- define "agent-platform.crossplane.managementPoliciesNoDelete" -}}
{{- if .xp.observeOnly -}}
- Observe
{{- else -}}
- Create
- Update
- LateInitialize
- Observe
{{- end -}}
{{- end -}}

{{/* arn:aws, or arn:aws-cn in the China partition. */}}
{{- define "agent-platform.crossplane.awsPartition" -}}
{{- if hasPrefix "cn-" .xp.region -}}arn:aws-cn{{- else -}}arn:aws{{- end -}}
{{- end -}}

{{/*
Tags on the cloud resources: the chart's (app, managed-by, name) under the
installation's own. Azure tag keys take no dash.
Usage: include "agent-platform.crossplane.tags" (dict "xp" $xp "app" "agent-platform-postgres" "name" $bucket)
*/}}
{{- define "agent-platform.crossplane.tags" -}}
{{- $tags := dict "app" .app "managed-by" "crossplane" "name" .name -}}
{{- $tags = merge (deepCopy (.xp.tags | default dict)) $tags -}}
{{- if has .xp.provider (list "azure" "capz") -}}
{{- $clean := dict -}}
{{- range $k, $v := $tags -}}{{- $_ := set $clean ($k | replace "-" "_") $v -}}{{- end -}}
{{- $tags = $clean -}}
{{- end -}}
{{- toYaml $tags -}}
{{- end -}}

{{- define "agent-platform.postgres.crossplaneManagementPolicies" -}}
{{- include "agent-platform.crossplane.managementPolicies" (dict "xp" .Values.postgres.backup.crossplane) -}}
{{- end -}}

{{- define "agent-platform.postgres.crossplaneManagementPoliciesNoDelete" -}}
{{- include "agent-platform.crossplane.managementPoliciesNoDelete" (dict "xp" .Values.postgres.backup.crossplane) -}}
{{- end -}}

{{- define "agent-platform.postgres.crossplaneTags" -}}
{{- include "agent-platform.crossplane.tags" (dict "xp" .Values.postgres.backup.crossplane "app" "agent-platform-postgres" "name" (include "agent-platform.postgres.crossplaneStoreName" .)) -}}
{{- end -}}

{{/* The bucket (aws) or container (azure) name. */}}
{{- define "agent-platform.postgres.crossplaneStoreName" -}}
{{- $xp := .Values.postgres.backup.crossplane -}}
{{- if eq $xp.provider "azure" -}}{{- $xp.azure.containerName -}}{{- else -}}{{- $xp.aws.bucketName -}}{{- end -}}
{{- end -}}

{{/*
Agent Substrate's snapshot store (templates/substrate/crossplane-aws.yaml):
kagent.harness.snapshotStore renders the S3 bucket and the IRSA role the
Harness's snapshotPolicy.location points at, the postgres.backup.crossplane
shape. The meta chart carries the same helpers: it derives
kagent.harness.snapshotLocation for the kagent release and the role annotation
for the substrate release's two ServiceAccounts from them.
*/}}

{{/* The provider while the Crossplane block renders the store (kagent on, crossplane on): aws or capz; else "". */}}
{{- define "agent-platform.substrateStore.crossplane" -}}
{{- $xp := dig "harness" "snapshotStore" "crossplane" dict (.Values.kagent | default dict) -}}
{{- if and (include "agent-platform.componentEnabled" (dict "root" . "name" "kagent")) $xp.enabled -}}
{{- $xp.provider -}}
{{- end -}}
{{- end -}}

{{- define "agent-platform.substrateStore.block" -}}
{{- dig "harness" "snapshotStore" dict (.Values.kagent | default dict) | toJson -}}
{{- end -}}

{{/*
How the platform reaches the snapshot store while kagent is on: "aws" (the
Crossplane S3 bucket, IRSA), "capz" (the Crossplane Azure account behind the
s3proxy façade, Workload Identity), "s3proxy" (the façade alone, in front of an
account provisioned by hand or a lab's Azurite, an account key); "" when the
installation names its own store.
*/}}
{{- define "agent-platform.substrateStore.mode" -}}
{{- $store := include "agent-platform.substrateStore.block" . | fromJson -}}
{{- if include "agent-platform.componentEnabled" (dict "root" . "name" "kagent") -}}
{{- if dig "crossplane" "enabled" false $store -}}{{- $store.crossplane.provider -}}
{{- else if dig "s3proxy" "enabled" false $store -}}s3proxy{{- end -}}
{{- end -}}
{{- end -}}

{{/* Truthy while the s3proxy façade renders: mode capz or s3proxy. */}}
{{- define "agent-platform.substrateStore.s3proxy" -}}
{{- $mode := include "agent-platform.substrateStore.mode" . -}}
{{- if or (eq $mode "capz") (eq $mode "s3proxy") -}}true{{- end -}}
{{- end -}}

{{/*
The Azure Blob store behind the façade, as JSON {endpoint, account, container}:
with provider capz the Crossplane block's account and container
(https://<account>.blob.core.windows.net) — an explicit s3proxy.azure.* that
disagrees fails the render; with the façade alone, s3proxy.azure.* verbatim.
*/}}
{{- define "agent-platform.substrateStore.azure" -}}
{{- $store := include "agent-platform.substrateStore.block" . | fromJson -}}
{{- $own := dig "s3proxy" "azure" dict $store -}}
{{- $az := dict "endpoint" ($own.endpoint | default "") "account" ($own.account | default "") "container" ($own.container | default "") -}}
{{- if eq (include "agent-platform.substrateStore.mode" .) "capz" -}}
{{- $capz := $store.crossplane.capz -}}
{{- $derived := dict "endpoint" (printf "https://%s.blob.core.windows.net" $capz.storageAccountName) "account" $capz.storageAccountName "container" $capz.containerName -}}
{{- range $k, $v := $derived -}}
{{- $o := index $az $k -}}
{{- if and $o (ne $o $v) -}}
{{- fail (printf "kagent.harness.snapshotStore.s3proxy.azure.%s (%s) differs from what kagent.harness.snapshotStore.crossplane.capz renders (%s): the capz block names the account and the container — leave s3proxy.azure.%s unset" $k $o $v $k) -}}
{{- end -}}
{{- end -}}
{{- $az = $derived -}}
{{- end -}}
{{- $az | toJson -}}
{{- end -}}

{{/* The snapshot location the store implies: s3://<bucket, or the container behind the façade>/<prefix> (no prefix: s3://<name>). */}}
{{- define "agent-platform.substrateStore.location" -}}
{{- $store := include "agent-platform.substrateStore.block" . | fromJson -}}
{{- $prefix := $store.prefix | default "" | trimAll "/" -}}
{{- $name := "" -}}
{{- if include "agent-platform.substrateStore.s3proxy" . -}}
{{- $name = (include "agent-platform.substrateStore.azure" . | fromJson).container -}}
{{- else -}}
{{- $name = $store.crossplane.aws.bucketName -}}
{{- end -}}
{{- printf "s3://%s" $name -}}{{- with $prefix }}/{{ . }}{{- end -}}
{{- end -}}

{{/* The façade's one name: its Deployment, Service, ServiceAccount, PodDisruptionBudget and the key-pair Secret (in the release namespace and in ate-system). */}}
{{- define "agent-platform.substrateStore.s3proxyName" -}}substrate-s3proxy{{- end -}}

{{/* The URL Substrate reaches the façade at: its Service in the release namespace, port 80. */}}
{{- define "agent-platform.substrateStore.s3proxyUrl" -}}
{{- printf "http://%s.%s.svc:80" (include "agent-platform.substrateStore.s3proxyName" .) .Release.Namespace -}}
{{- end -}}

{{/*
The S3 environment Substrate's atelet and ate-api-server get for the façade —
the shape the substrate chart gives them for its bundled store (AWS_REGION
names the SigV4 scope only; s3proxy reads it from the request), the key pair
from the Secret in ate-system. A JSON list of EnvVars.
*/}}
{{- define "agent-platform.substrateStore.s3proxyEnv" -}}
{{- $secret := include "agent-platform.substrateStore.s3proxyName" . -}}
{{- list
  (dict "name" "AWS_REGION" "value" "us-east-1")
  (dict "name" "AWS_ENDPOINT_URL" "value" (include "agent-platform.substrateStore.s3proxyUrl" .))
  (dict "name" "AWS_S3_USE_PATH_STYLE" "value" "true")
  (dict "name" "AWS_ACCESS_KEY_ID" "valueFrom" (dict "secretKeyRef" (dict "name" $secret "key" "accessKeyId")))
  (dict "name" "AWS_SECRET_ACCESS_KEY" "valueFrom" (dict "secretKeyRef" (dict "name" $secret "key" "secretAccessKey")))
  | toJson -}}
{{- end -}}

{{/* The capz identity's name: workloadIdentity.identityName, else <containerName>-identity. */}}
{{- define "agent-platform.substrateStore.capzIdentityName" -}}
{{- $capz := (include "agent-platform.substrateStore.block" . | fromJson).crossplane.capz -}}
{{- $capz.workloadIdentity.identityName | default (printf "%s-identity" $capz.containerName) -}}
{{- end -}}

{{/* The Secret provider-kubernetes writes the identity's clientId and tenantId into; the s3proxy pods read it. */}}
{{- define "agent-platform.substrateStore.capzIdentitySecret" -}}
{{- printf "%s-azure-identity" (include "agent-platform.substrateStore.s3proxyName" .) -}}
{{- end -}}

{{/*
The store block's guards, the same in both charts: the provider, the inputs
each provider and the façade require, the account name's shape, the endpoint's
scheme, the bundled store off while the façade is on, an explicit
snapshotLocation agreeing with the derived one.
*/}}
{{- define "agent-platform.substrateStore.validate" -}}
{{- $store := include "agent-platform.substrateStore.block" . | fromJson -}}
{{- $xp := $store.crossplane | default dict -}}
{{- $mode := include "agent-platform.substrateStore.mode" . -}}
{{- if $xp.enabled -}}
{{- if not (has $xp.provider (list "aws" "capz")) -}}
{{- fail (printf "kagent.harness.snapshotStore.crossplane.provider=%s is not supported; the chart provisions the snapshot store on aws (S3 + IRSA) and capz (Azure Blob behind the s3proxy façade, Workload Identity) — elsewhere name the store in kagent.harness.snapshotLocation and its access in substrate.atelet.extraEnv / substrate.ateApiServer.extraEnv, or front an Azure Blob account provisioned by hand with kagent.harness.snapshotStore.s3proxy" $xp.provider) -}}
{{- end -}}
{{- range $k := list "providerConfigRef" "region" -}}
{{- if not (index $xp $k) -}}
{{- fail (printf "kagent.harness.snapshotStore.crossplane.%s is required when kagent.harness.snapshotStore.crossplane.enabled" $k) -}}
{{- end -}}
{{- end -}}
{{- if eq $xp.provider "aws" -}}
{{- range $k := list "bucketName" "accountId" "oidcProvider" -}}
{{- if not (index $xp.aws $k) -}}
{{- fail (printf "kagent.harness.snapshotStore.crossplane.aws.%s is required for provider aws" $k) -}}
{{- end -}}
{{- end -}}
{{- if not (regexMatch "^[0-9]{12}$" (toString $xp.aws.accountId)) -}}
{{- fail (printf "kagent.harness.snapshotStore.crossplane.aws.accountId (%v) must be the 12-digit AWS account id, quoted as a string" $xp.aws.accountId) -}}
{{- end -}}
{{- end -}}
{{- if eq $xp.provider "capz" -}}
{{- $capz := $xp.capz | default dict -}}
{{- range $k := list "storageAccountName" "containerName" "resourceGroup" "subscriptionId" -}}
{{- if not (index $capz $k) -}}
{{- fail (printf "kagent.harness.snapshotStore.crossplane.capz.%s is required for provider capz" $k) -}}
{{- end -}}
{{- end -}}
{{- if not (regexMatch "^[a-z0-9]{3,24}$" $capz.storageAccountName) -}}
{{- fail (printf "kagent.harness.snapshotStore.crossplane.capz.storageAccountName (%s) must be 3 to 24 lowercase letters and digits (an Azure storage account name)" $capz.storageAccountName) -}}
{{- end -}}
{{- if not (dig "workloadIdentity" "oidcIssuerUrl" "" $capz) -}}
{{- fail "kagent.harness.snapshotStore.crossplane.capz.workloadIdentity.oidcIssuerUrl is required for provider capz: the cluster's service-account issuer the FederatedIdentityCredential trusts (the apiserver's --service-account-issuer)" -}}
{{- end -}}
{{- if not (dig "workloadIdentity" "providerKubernetes" "providerConfigRef" "" $capz) -}}
{{- fail "kagent.harness.snapshotStore.crossplane.capz.workloadIdentity.providerKubernetes.providerConfigRef is required for provider capz: provider-kubernetes bridges the identity's generated ids into the RoleAssignment and into the s3proxy pods' Secret" -}}
{{- end -}}
{{- end -}}
{{- end -}}
{{- if and $xp.enabled (eq $xp.provider "aws") (dig "s3proxy" "enabled" false $store) -}}
{{- fail "kagent.harness.snapshotStore.s3proxy.enabled is on next to crossplane.provider aws: the façade fronts Azure Blob and has no place in front of an S3 bucket — turn it off (it is on by itself with provider capz)" -}}
{{- end -}}
{{- if include "agent-platform.substrateStore.s3proxy" . -}}
{{- $az := include "agent-platform.substrateStore.azure" . | fromJson -}}
{{- $keyRef := dig "s3proxy" "azure" "accountKeySecretRef" dict $store -}}
{{- range $k := list "endpoint" "account" "container" -}}
{{- if not (index $az $k) -}}
{{- fail (printf "kagent.harness.snapshotStore.s3proxy.azure.%s is required while kagent.harness.snapshotStore.s3proxy is on without the capz Crossplane block: the façade needs the Azure Blob account it fronts" $k) -}}
{{- end -}}
{{- end -}}
{{- if eq $mode "capz" -}}
{{- if or $keyRef.name $keyRef.key -}}
{{- fail "kagent.harness.snapshotStore.s3proxy.azure.accountKeySecretRef is set next to crossplane.provider capz: the façade runs as the Workload Identity the capz block renders and never reads an account key — leave accountKeySecretRef unset" -}}
{{- end -}}
{{- else -}}
{{- if not (and $keyRef.name $keyRef.key) -}}
{{- fail "kagent.harness.snapshotStore.s3proxy.azure.accountKeySecretRef.name and .key are required while the façade runs without the capz Crossplane block: it reaches the account with an account key from that Secret (release namespace)" -}}
{{- end -}}
{{- end -}}
{{- if not (regexMatch "^https?://" $az.endpoint) -}}
{{- fail (printf "kagent.harness.snapshotStore.s3proxy.azure.endpoint (%s) must be an http(s) URL (https://<account>.blob.core.windows.net)" $az.endpoint) -}}
{{- end -}}
{{- if dig "rustfs" "enabled" false (.Values.substrate | default dict) -}}
{{- fail "substrate.rustfs.enabled is on while kagent.harness.snapshotStore.s3proxy renders the façade: the substrate chart sets the S3 environment for its bundled store and the derived one for the façade would repeat the variables — turn substrate.rustfs.enabled off" -}}
{{- end -}}
{{- end -}}
{{- if $mode -}}
{{- $explicit := dig "harness" "snapshotLocation" "" (.Values.kagent | default dict) -}}
{{- $derived := include "agent-platform.substrateStore.location" . -}}
{{- if and $explicit (ne $explicit $derived) -}}
{{- fail (printf "kagent.harness.snapshotLocation (%s) differs from the location kagent.harness.snapshotStore renders (%s): the store block names the bucket and the prefix — leave kagent.harness.snapshotLocation unset, or turn kagent.harness.snapshotStore.crossplane.enabled off and name an existing store" $explicit $derived) -}}
{{- end -}}
{{- end -}}
{{- end -}}

{{/* The IAM role the block renders: aws.roleName, else the bucket name. */}}
{{- define "agent-platform.substrateStore.awsRoleName" -}}
{{- $aws := (include "agent-platform.substrateStore.block" . | fromJson).crossplane.aws -}}
{{- $aws.roleName | default $aws.bucketName -}}
{{- end -}}

{{- define "agent-platform.substrateStore.awsRoleArn" -}}
{{- $xp := (include "agent-platform.substrateStore.block" . | fromJson).crossplane -}}
{{- printf "%s:iam::%s:role/%s" (include "agent-platform.crossplane.awsPartition" (dict "xp" $xp)) $xp.aws.accountId (include "agent-platform.substrateStore.awsRoleName" .) -}}
{{- end -}}

{{/*
The two Substrate ServiceAccounts that read and write snapshots, as the
substrate chart names them under the release the meta chart creates
(releaseName `substrate`, which the chart's fullname helper leaves unprefixed):
atelet (the node agent) and ate-api-server.
*/}}
{{- define "agent-platform.substrateStore.serviceAccounts" -}}
{{- list "atelet" "ate-api-server" | toJson -}}
{{- end -}}

{{/*
The public hostname of the kagent controller route: kagent.controllerRoute.hostname
when set, else agentgateway.<global.domain>; a render failure with neither. The
gRPC origin the Dev Portal dials (app-config apiBaseUrl) and the hostname of the
public GRPCRoute.
Usage: include "agent-platform.kagent.controllerHostname" .
*/}}
{{- define "agent-platform.kagent.controllerHostname" -}}
{{- include "agent-platform.hostname" (dict "ctx" . "prefix" "agentgateway" "override" .Values.kagent.controllerRoute.hostname "key" "kagent.controllerRoute.hostname") -}}
{{- end -}}

{{/*
The JWT claim the caller's identity is taken from — kagent.controller.auth.userIdClaim
(default email), the ONE value both authentication layers read: the controller's
AUTH_USER_ID_CLAIM (kagent chart) and the gateway's x-user-id transformation
(templates/kagent/controller-jwt-policy.yaml), so the two cannot disagree.
Usage: include "agent-platform.kagent.userIdClaim" .
*/}}
{{- define "agent-platform.kagent.userIdClaim" -}}
{{- dig "controller" "auth" "userIdClaim" "email" (.Values.kagent | default dict) -}}
{{- end -}}

{{/*
The rules of the kagent controller GRPCRoute (a `rules:` list), from
kagent.controllerRoute.grpc.services: one rule per service, forwarding to the
backend given as `.backendRefs` (a YAML string). A service with an EMPTY method
list gets one service-only match — every RPC of the service, translated by the
agentgateway controller (chart >= 2.1.1) into the path prefix "/<service>/";
a service with methods listed gets one exact service/method match per RPC — the
shape an older controller needs, and a way to expose a subset. Either shape
outranks the MCP catch-all's PathPrefix /.
Usage: include "agent-platform.kagent.grpcRules" (dict "ctx" . "backendRefs" $refs) | nindent 4
*/}}
{{- define "agent-platform.kagent.grpcRules" -}}
{{- $services := dig "controllerRoute" "grpc" "services" (dict) .ctx.Values.kagent }}
{{- if not $services }}
{{- fail "kagent.controllerRoute.grpc.services is empty: the controller GRPCRoute needs at least one service" }}
{{- end }}
{{- range $svc, $methods := $services }}
- matches:
{{- if $methods }}
{{- range $methods }}
    - method:
        type: Exact
        service: {{ $svc }}
        method: {{ . }}
{{- end }}
{{- else }}
    - method:
        type: Exact
        service: {{ $svc }}
{{- end }}
  backendRefs:
{{ $.backendRefs | indent 4 }}
{{- end }}
{{- end -}}

{{/*
=== Agent Substrate ===

Substrate's namespaces are fixed: the substrate chart's Roles, Service names and
the kagent controller's ate-api / atenet-router endpoints name ate-system
(upstream's canonical render), and the chart renders the
podcertificate-controller into podcertificate-controller-system. The meta chart
targets its two Substrate releases at the same names.
*/}}
{{- define "agent-platform.substrate.namespace" -}}ate-system{{- end -}}
{{- define "agent-platform.substrate.podcertNamespace" -}}podcertificate-controller-system{{- end -}}

{{/*
Truthy when the platform runs Agent Substrate: the substrate component is on
(absent from the roster = off, a chart that predates it renders none of this).
*/}}
{{- define "agent-platform.substrate.enabled" -}}
{{- include "agent-platform.optionalComponentEnabled" (dict "root" . "name" "substrate") -}}
{{- end -}}

{{/*
kagent's built-in tool server: the kagent-tools subchart of the kagent chart
(kagent.kagent-tools). The subchart renders its Deployment and Services into its
own namespaceOverride, else its release namespace, and the kagent chart composes
the kagent-tool-server RemoteMCPServer URL from the same two inputs
(http://<tools fullname>.<that namespace>:<port>/mcp). The egress rules this
chart opens to the server — the kagent controller's tool discovery
(templates/kagent/netpol.yaml) and the actors' calls through Substrate's egress
gateway (templates/substrate/netpol.yaml) — derive the namespace the same way,
from the same values block and the same release namespace (every platform
HelmRelease installs into gitops.targetNamespace), so a rule names the namespace
the server is rendered into whether or not an installation pins the override.
Never the kagent namespace by assumption: kagent.namespaceOverride moves the
controller, not the subchart (#421). The port is the tools Service's targetPort.
*/}}
{{- define "agent-platform.kagentTools.enabled" -}}
{{- $tools := index .Values.kagent "kagent-tools" | default dict -}}
{{- if $tools.enabled }}true{{- end -}}
{{- end -}}
{{- define "agent-platform.kagentTools.namespace" -}}
{{- $tools := index .Values.kagent "kagent-tools" | default dict -}}
{{- $tools.namespaceOverride | default .Release.Namespace -}}
{{- end -}}
{{- define "agent-platform.kagentTools.port" -}}
{{- $tools := index .Values.kagent "kagent-tools" | default dict -}}
{{- dig "service" "ports" "tools" "targetPort" "8084" $tools | toString -}}
{{- end -}}

{{/*
Where Substrate's control-plane database lives: "bundled" (the substrate chart's
StatefulSet — substrate.postgres.enabled true, or `auto` while neither of the
other two applies), "external" (an explicit substrate.postgres.connectionString),
"cnpg" (the platform's CNPG Cluster, through postgres.databases.substrate and its
derived Secret), or "" for none — the meta chart refuses the last and resolves
`auto` to the boolean the substrate chart takes; this chart's guard says the
same on its own render.
*/}}
{{- define "agent-platform.substrate.postgresMode" -}}
{{- $sub := .Values.substrate | default dict -}}
{{- $bundled := dig "postgres" "enabled" "auto" $sub | toString -}}
{{- $conn := dig "postgres" "connectionString" "" $sub -}}
{{- $cnpg := and .Values.postgres.enabled (ne (dig "databases" "substrate" "enabled" true .Values.postgres) false) -}}
{{- if not (has $bundled (list "auto" "true" "false")) -}}
{{- fail (printf "substrate.postgres.enabled must be one of auto, true, false (got %s)" $bundled) -}}
{{- end -}}
{{- if or (eq $bundled "true") (and (eq $bundled "auto") (not $conn) (not $cnpg)) -}}bundled
{{- else if $conn -}}external
{{- else if $cnpg -}}cnpg
{{- end -}}
{{- end -}}

{{/*
The derived CNPG connection Secret of postgres.databases.substrate, the DSN the
meta chart hands ate-api-server: <postgres.clusterName>-substrate-app.
*/}}
{{- define "agent-platform.substrate.databaseSecretName" -}}
{{- printf "%s-substrate-app" .Values.postgres.clusterName -}}
{{- end -}}

{{/*
postgres.databases resolved: a JSON array of the enabled entries whose
component (if any) is on, while the platform Cluster renders — each with key,
name (spec.name; default the key with - as _), owner (the application role),
cluster, namespace (postgres.namespace), crName (<cluster>-<key>),
reclaimPolicy, extensions, secretNamespaces (postgres.namespace first, then the
entry's, deduplicated). Empty array otherwise. Consumers: the Database CRs, the
derived-Secret hook, the guards, the policies that open Postgres to a consumer.
Usage: include "agent-platform.postgres.databases" . | fromJsonArray
*/}}
{{- define "agent-platform.postgres.databases" -}}
{{- $out := list -}}
{{- if .Values.postgres.enabled -}}
{{- $pg := .Values.postgres -}}
{{- $ns := $pg.namespace | default .Release.Namespace -}}
{{- range $key, $db := ($pg.databases | default dict) -}}
{{- if not (kindIs "map" $db) -}}
{{- fail (printf "postgres.databases.%s must be a map (enabled, name, component, extensions, reclaimPolicy, secretNamespaces)" $key) -}}
{{- end -}}
{{- $component := dig "component" "" $db -}}
{{- $on := and (ne (dig "enabled" true $db) false) (or (not $component) (include "agent-platform.optionalComponentEnabled" (dict "root" $ "name" $component))) -}}
{{- if $on -}}
{{- $targets := list $ns -}}
{{- range (dig "secretNamespaces" list $db) -}}{{- if not (has . $targets) -}}{{- $targets = append $targets . -}}{{- end -}}{{- end -}}
{{- $out = append $out (dict
      "key" $key
      "name" (dig "name" (replace "-" "_" $key) $db)
      "owner" ($pg.applicationDatabase.owner | default "kagent")
      "cluster" $pg.clusterName
      "namespace" $ns
      "crName" (printf "%s-%s" $pg.clusterName $key)
      "reclaimPolicy" (dig "reclaimPolicy" "retain" $db)
      "extensions" (dig "extensions" list $db)
      "component" $component
      "secretNamespaces" $targets) -}}
{{- end -}}
{{- end -}}
{{- end -}}
{{- $out | toJson -}}
{{- end -}}

{{/*
=== Kyverno PolicyExceptions ===

The `exceptions:` list of a PolicyException from the rules one workload
violates: each rule is looked up in kyvernoPolicies.rules (rule → the
ClusterPolicy of the cluster's PSS set; a rule without an entry fails the
render naming it), the rules are grouped by policy, and each rule is cited
with its autogen-<rule> — the copy Kyverno generates for the controller kinds
(Deployment, DaemonSet, ...) a pod-level rule matches through. Rendered as YAML
list items; the caller provides the `exceptions:` key.
Usage: include "agent-platform.kyverno.exceptions" (dict "root" $ "rules" (list "host-path" "privileged-containers"))
*/}}
{{- define "agent-platform.kyverno.exceptions" -}}
{{- $root := .root -}}
{{- $byPolicy := dict -}}
{{- range .rules -}}
{{- $policy := index ($root.Values.kyvernoPolicies.rules | default dict) . -}}
{{- if not $policy -}}
{{- fail (printf "kyvernoPolicies.rules names no ClusterPolicy for the rule %q; add `%s: <policy>` (the PSS policy of the cluster's kyverno-policies chart that carries the rule)" . .) -}}
{{- end -}}
{{- $_ := set $byPolicy $policy (append (index $byPolicy $policy | default list) .) -}}
{{- end -}}
{{- range $policy, $rules := $byPolicy }}
- policyName: {{ $policy }}
  ruleNames:
  {{- range $rules }}
    - {{ . }}
    - autogen-{{ . }}
  {{- end }}
{{- end }}
{{- end -}}

{{/*
kagent.otel.<signal>.enabled resolved to "true" or "false" — .signal is
"tracing" or "logging". `auto` (the chart default) follows the resolved
global.observability.metrics.serviceMonitor.enabled, the rule the meta chart
applies before forwarding (the OTLP gateway the exporters send to is part of
the observability platform whose monitoring.coreos.com/v1 CRDs that knob
detects); an explicit true / false wins.
Usage: include "agent-platform.kagent.otelSignal" (dict "root" $ "signal" "tracing")
*/}}
{{- define "agent-platform.kagent.otelSignal" -}}
{{- $v := dig "otel" .signal "enabled" "auto" (.root.Values.kagent | default dict) -}}
{{- if or (kindIs "invalid" $v) (and (kindIs "string" $v) (eq $v "auto")) -}}
{{- include "agent-platform.shape.serviceMonitor" .root -}}
{{- else -}}
{{- include "agent-platform.shape.resolve" (dict "root" .root "key" (printf "kagent.otel.%s.enabled" .signal) "value" $v "api" "monitoring.coreos.com/v1") -}}
{{- end -}}
{{- end -}}

{{/*
One OTLP destination for a network policy, from an exporter endpoint read
the way the SDKs read it: a JSON {endpoint, namespace, port}. A port left
out is the OTLP default, 4317, or 4318 for the http/protobuf protocol and
443 for an https URL. An endpoint at an in-cluster Service address
(<service>.<namespace>.svc[.cluster.local]) yields its namespace, whose
pods the rule selects; any other host yields an empty namespace and the
rule falls back to the cluster entity on that port. Shared by kagent's
exporters (agent-platform.kagent.otlpTargets) and klaus-gateway's
(templates/klausgateway/netpol.yaml).
Usage: include "agent-platform.otlpTarget" (dict "endpoint" $e "protocol" $p) | fromJson
*/}}
{{- define "agent-platform.otlpTarget" -}}
{{- $endpoint := .endpoint | toString | trim -}}
{{- $protocol := .protocol | default "grpc" | toString | lower -}}
{{- $scheme := "" -}}
{{- $rest := $endpoint -}}
{{- if contains "://" $endpoint -}}
{{- $parts := splitList "://" $endpoint -}}
{{- $scheme = first $parts | lower -}}
{{- $rest = rest $parts | join "://" -}}
{{- end -}}
{{- $hostport := splitList "/" $rest | first -}}
{{- $host := $hostport -}}
{{- $port := "" -}}
{{- if regexMatch ":[0-9]+$" $hostport -}}
{{- $host = regexReplaceAll ":[0-9]+$" $hostport "" -}}
{{- $port = regexFind "[0-9]+$" $hostport -}}
{{- end -}}
{{- if not $port -}}
{{- if eq $scheme "https" -}}{{- $port = "443" -}}
{{- else if eq $protocol "http/protobuf" -}}{{- $port = "4318" -}}
{{- else -}}{{- $port = "4317" -}}
{{- end -}}
{{- end -}}
{{- $ns := "" -}}
{{- if regexMatch "^[a-z0-9]([-a-z0-9]*[a-z0-9])?\\.[a-z0-9]([-a-z0-9]*[a-z0-9])?\\.svc(\\.cluster\\.local)?\\.?$" $host -}}
{{- $ns = index (splitList "." $host) 1 -}}
{{- end -}}
{{- dict "endpoint" $endpoint "namespace" $ns "port" $port | toJson -}}
{{- end -}}

{{/*
One cilium egress rule to an OTLP destination (agent-platform.otlpTarget):
the pods of the Service's namespace on the endpoint's port, or the cluster
entity on that port for an endpoint that is not an in-cluster Service
address. .who names the sender in the rule's comment. Rendered as a YAML
list item; include with nindent under `egress:`.
*/}}
{{- define "agent-platform.otlpEgressRule" -}}
# The OTLP gateway {{ .who }} ({{ .target.endpoint }}).
{{- if .target.namespace }}
- toEndpoints:
    - matchLabels:
        io.kubernetes.pod.namespace: {{ .target.namespace }}
{{- else }}
- toEntities:
    - cluster
{{- end }}
  toPorts:
    - ports:
        - port: {{ .target.port | quote }}
          protocol: TCP
{{- end -}}

{{/*
The OTLP gateways kagent's exporters send to, for a network policy: a JSON
list of {endpoint, namespace, port} (agent-platform.otlpTarget), one per
distinct destination of the signals that are on (kagent.otel.tracing /
.logging: exporter.otlp.endpoint). Empty when both signals are off: no
export, no rule.
Usage: include "agent-platform.kagent.otlpTargets" . | fromJsonArray
*/}}
{{- define "agent-platform.kagent.otlpTargets" -}}
{{- $targets := list -}}
{{- $seen := dict -}}
{{- $kagent := .Values.kagent | default dict -}}
{{- $protocol := dig "otel" "tracing" "exporter" "otlp" "protocol" "grpc" $kagent | toString | lower -}}
{{- range $signal := list "tracing" "logging" -}}
{{- if eq (include "agent-platform.kagent.otelSignal" (dict "root" $ "signal" $signal)) "true" -}}
{{- $endpoint := dig "otel" $signal "exporter" "otlp" "endpoint" "" $kagent | toString | trim -}}
{{- if $endpoint -}}
{{- $t := include "agent-platform.otlpTarget" (dict "endpoint" $endpoint "protocol" $protocol) | fromJson -}}
{{- $key := printf "%s:%s" $t.namespace $t.port -}}
{{- if not (hasKey $seen $key) -}}
{{- $_ := set $seen $key true -}}
{{- $targets = append $targets $t -}}
{{- end -}}
{{- end -}}
{{- end -}}
{{- end -}}
{{- $targets | toJson -}}
{{- end -}}

{{/*
The cilium egress rules to the OTLP gateways kagent's exporters send to
(agent-platform.kagent.otlpTargets), one per destination
(agent-platform.otlpEgressRule). Nothing when both signals are off (an empty
string, so `with` gates a caller's comment). Include with nindent under
`egress:`.
*/}}
{{- define "agent-platform.kagent.otlpEgress" -}}
{{- range $i, $t := include "agent-platform.kagent.otlpTargets" . | fromJsonArray -}}
{{- if $i }}
{{ end -}}
{{- include "agent-platform.otlpEgressRule" (dict "target" $t "who" "kagent's exporters send to") -}}
{{- end }}
{{- end -}}
