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
Whether a component is enabled — reads `components.<name>.enabled`, the single
on/off switch. `name` is the components.<key> name, which equals the component's
chart name and is therefore what a dependsOn entry references. A component with
no `enabled` key is force-enabled — except kagent-crds, which then follows
components.kagent. Emits "true" when on, empty string otherwise.

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
{{- if hasKey $c "enabled" }}{{- $on = $c.enabled }}
{{- else if has .name (list "kagent-crds" "substrate" "substrate-crds") }}
{{- /* The kagent line ships its CRDs as their own chart and has no runtime
       without Agent Substrate; without an explicit switch these three follow
       components.kagent, so a consumer turns on kagent and gets its CRDs and
       its runtime (an explicit false with kagent on is refused by
       agent-platform.validateKagentCrds / agent-platform.validateSubstrate). */ -}}
{{- $on = eq (include "agent-platform.componentEnabled" (dict "root" $root "name" "kagent")) "true" }}
{{- end }}
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
  agent-manager: flux.helmReleaseServiceAccount from kagent.fluxServiceAccountName;
                 muster.url from the muster Service (agent-platform.musterMcpUrl).
  substrate: postgres.connectionStringSecretRef, on the platform Cluster —
    the derived CNPG connection Secret <postgres.clusterName>-substrate-app
    (key uri) the connectivity release's hook writes into ate-system for
    postgres.databases.substrate (agent-platform.substrate.postgresMode; the
    `auto` of substrate.postgres.enabled itself is resolved by
    agent-platform.shape.apply, with the other cluster-shape knobs).
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
{{- $url := include "agent-platform.musterMcpUrl" .root -}}
{{- $ownUrl := dig "muster" "url" "" (index .root.Values "agent-manager" | default dict) -}}
{{- if and $ownUrl (ne $ownUrl $url) -}}
{{- fail (printf "agent-manager.muster.url (%s) differs from the platform's muster MCP URL (%s): agent-manager composes every agent's RemoteMCPServer against the muster this chart installs — the URL follows muster.fullnameOverride and muster.service.port; leave agent-manager.muster.url unset" $ownUrl $url) -}}
{{- end -}}
{{- $_ := set $derived "muster" (dict "url" $url) -}}
{{- end -}}
{{- if and (eq .name "substrate") (eq (include "agent-platform.substrate.postgresMode" .root) "cnpg") -}}
{{- $ref := dict "name" (include "agent-platform.substrate.databaseSecretName" .root) "key" "uri" -}}
{{- $own := dig "postgres" "connectionStringSecretRef" dict (.root.Values.substrate | default dict) -}}
{{- $ownName := dig "name" "" $own -}}
{{- $ownKey := dig "key" "" $own -}}
{{- if or (and $ownName (ne $ownName $ref.name)) (and $ownKey (ne $ownKey $ref.key)) -}}
{{- fail (printf "substrate.postgres.connectionStringSecretRef (%s/%s) differs from the Secret the connectivity release derives for postgres.databases.substrate (%s/%s): leave it unset — it follows postgres.clusterName — or name an external database in substrate.postgres.connectionString" $ownName $ownKey $ref.name $ref.key) -}}
{{- end -}}
{{- $_ := set $derived "postgres" (dict "connectionStringSecretRef" $ref) -}}
{{- end -}}
{{- $derived | toJson -}}
{{- end -}}

{{/*
Where Agent Substrate's control-plane database lives: "bundled" (the substrate
chart's single-instance StatefulSet — substrate.postgres.enabled true, or `auto`
while neither of the other two applies), "external" (an explicit
substrate.postgres.connectionString), "cnpg" (the platform's CNPG Cluster,
postgres.enabled, through postgres.databases.substrate and the derived Secret),
or "" when none of the three holds (substrate.postgres.enabled false without a
Cluster or a connection string) — which validateSubstrate refuses. The
connectivity chart carries the same helper and resolves `auto` the same way.
Usage: include "agent-platform.substrate.postgresMode" .
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
The derived CNPG connection Secret of postgres.databases.substrate, as the
connectivity release names it: <postgres.clusterName>-substrate-app.
*/}}
{{- define "agent-platform.substrate.databaseSecretName" -}}
{{- printf "%s-substrate-app" .Values.postgres.clusterName -}}
{{- end -}}

{{/*
Agent Substrate is kagent API v2's runtime: refuse the shapes that install a
kagent with nothing to run agents on, or a Substrate with nothing to start
against, at render time — and, where the render is live, a cluster that cannot
run it.
  * kagent on with substrate or substrate-crds switched off (both follow kagent
    unless switched explicitly).
  * kagent on without kagent.harness.snapshotLocation: the platform Harness's
    snapshotPolicy.location is the installation's snapshot store — an S3 bucket
    on CAPA with IRSA, an S3-compatible store with its endpoint in
    substrate.atelet.extraEnv, a lab's in-cluster store — and has no default.
  * Substrate on with no control-plane database: neither the bundled
    StatefulSet, nor an explicit connectionString, nor the platform's CNPG
    Cluster with postgres.databases.substrate.
  * Substrate on under a LIVE render (the Helm CLI, --dry-run=server,
    helm-controller: .Capabilities.APIVersions then lists kinds, which Helm's
    offline set never does — so `helm template` and CI, which see no cluster,
    are never refused) of a cluster that does not serve
    certificates.k8s.io/v1beta1 PodCertificateRequest: Substrate's atelet,
    ate-api-server and atenet get their identities through it. That is
    Kubernetes 1.35 with the PodCertificateRequest, ClusterTrustBundle and
    ClusterTrustBundleProjection feature gates on kube-apiserver and
    kube-controller-manager; the same three gates on every kubelet cannot be
    seen from the apiserver, the message says so.
*/}}
{{- define "agent-platform.validateSubstrate" -}}
{{- $kagent := eq (include "agent-platform.componentEnabled" (dict "root" . "name" "kagent")) "true" -}}
{{- $substrate := eq (include "agent-platform.componentEnabled" (dict "root" . "name" "substrate")) "true" -}}
{{- $crds := eq (include "agent-platform.componentEnabled" (dict "root" . "name" "substrate-crds")) "true" -}}
{{- if and $kagent (not (and $substrate $crds)) -}}
{{- fail "components.kagent.enabled is true but components.substrate.enabled or components.substrate-crds.enabled is not: kagent API v2 runs every agent as an Agent Substrate actor and has no runtime without it; turn both on (they follow components.kagent when left unset)" -}}
{{- end -}}
{{- if and $substrate (not $crds) -}}
{{- fail "components.substrate.enabled is true but components.substrate-crds.enabled is not: the substrate chart's WorkerPool, SandboxConfig and CSIDriverConfig objects need the ate.dev CRDs the substrate-crds chart renders; turn both on" -}}
{{- end -}}
{{- if and $kagent (not (dig "harness" "snapshotLocation" "" (.Values.kagent | default dict))) -}}
{{- fail "kagent.harness.snapshotLocation is required when components.kagent is on: the Substrate snapshot location the platform Harness writes the actors' snapshots to (snapshotPolicy.location), an object-store URL such as s3://<bucket>/<prefix> — the installation's S3 bucket (IRSA on CAPA), an S3-compatible store with its endpoint and credentials in substrate.atelet.extraEnv, or a lab's in-cluster store (substrate.rustfs.enabled: true, s3://ate-snapshots/<prefix>)" -}}
{{- end -}}
{{- if and $substrate (not (include "agent-platform.substrate.postgresMode" .)) -}}
{{- fail "components.substrate is on but Agent Substrate's control plane has no database: turn postgres.enabled on (the platform's CNPG Cluster; postgres.databases.substrate renders the Database and the connectivity release derives the connection Secret), or substrate.postgres.enabled (the chart's bundled single-instance StatefulSet, a lab's shape), or name an external database in substrate.postgres.connectionString" -}}
{{- end -}}
{{- if and $substrate (.Capabilities.APIVersions.Has "v1/Namespace") -}}
{{- if not (.Capabilities.APIVersions.Has "certificates.k8s.io/v1beta1/PodCertificateRequest") -}}
{{- fail (printf "Agent Substrate (components.substrate) needs a cluster that serves certificates.k8s.io/v1beta1 PodCertificateRequest, and this one (Kubernetes %s) does not: Substrate's atelet, ate-api-server and atenet take their identities from it. That is Kubernetes 1.35 with the feature gates PodCertificateRequest, ClusterTrustBundle and ClusterTrustBundleProjection on kube-apiserver and kube-controller-manager — and on every kubelet, which the apiserver cannot show; turn all three on for all three components (on a Giant Swarm cluster the cluster chart's internal.advancedConfiguration.{controlPlane.apiServer,controlPlane.controllerManager,kubelet}.featureGates until giantswarm/cluster#1005 is the default) and let the nodes roll before turning kagent on" .Capabilities.KubeVersion.Version) -}}
{{- end -}}
{{- end -}}
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
The kagent line ships its CRDs as the kagent-crds chart (a roster entry the
kagent release dependsOn, the kserve-crd shape). kagent on with kagent-crds off
would install a controller without its CRDs and fail every kagent CR the
connectivity release renders at apply time ("no matches for kind"); refuse it
at render time instead.
*/}}
{{- define "agent-platform.validateKagentCrds" -}}
{{- if and (eq (include "agent-platform.componentEnabled" (dict "root" . "name" "kagent")) "true")
           (ne (include "agent-platform.componentEnabled" (dict "root" . "name" "kagent-crds")) "true") -}}
{{- fail "components.kagent.enabled is true but components.kagent-crds.enabled is not: the kagent line ships its CRDs as the kagent-crds chart, which the kagent release and the connectivity release's kagent CRs depend on; turn both on" -}}
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
gitops.forbidPinnedLoginConnector: fail the render when muster's Dex login is
pinned to one connector. Without a connectorId mcp-oauth sends no connector_id
and Dex shows its connector chooser; a pin hides every other connector of a Dex
that serves several identity providers and hands people from the others a token
without the groups their allowlists are written for. The message names the key.
*/}}
{{- define "agent-platform.validatePinnedLoginConnector" -}}
{{- if .Values.gitops.forbidPinnedLoginConnector -}}
{{- $pin := dig "muster" "oauth" "server" "dex" "connectorId" "" (.Values.muster | default dict) -}}
{{- if $pin -}}
{{- fail "gitops.forbidPinnedLoginConnector is true but muster.muster.oauth.server.dex.connectorId is set: muster would append connector_id to every Dex authorization request and the Dex connector chooser would never appear, so people from the installation's other identity providers could not sign in or would receive a token without the groups their allowlists use. Remove the key (the muster chart omits it when empty and Dex then offers every connector), or set gitops.forbidPinnedLoginConnector: false" -}}
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
Port muster listens on; defaults to 8090. nil-safe: the muster service tree is
the muster release's own, so .Values.muster.service is normally unset here.
*/}}
{{- define "agent-platform.musterServicePort" -}}
{{- dig "service" "port" 8090 (.Values.muster | default dict) -}}
{{- end -}}

{{/*
The in-cluster MCP URL of the platform's muster, the endpoint every agent's own
RemoteMCPServer targets: http://<muster Service>.<release namespace>.svc.cluster.local:<port>/mcp
while the muster component is on, "" otherwise. ONE helper, two consumers, one
name in both charts: this copy derives agent-manager's chart value muster.url
(componentDerivedValues, next to flux.helmReleaseServiceAccount); the
connectivity chart's copy renders the portal's app-config key
agentPlatform.musterMcpUrl. Both composers hand it to the Generic agent chart
1.x as muster.url, whose own default is the same URL on a default install.
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
filter, the agent-sandbox pod-security policy, the model-serving cache
policies — accept `auto` (the default):
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
modelServing.policies.enabled resolved: "true" when the model-serving Kyverno
cache policies render (a Kyverno mutate policy, so `auto` follows the RESOLVED
kyvernoPolicies.enabled like the agent-sandbox pod-security policy).
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

The six knobs (the five above and modelServing.policies.enabled, a Kyverno
mutate policy that follows the resolved kyvernoPolicies.enabled) are written
with their resolved value. The component-level
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
{{- $servingPolicies := eq (include "agent-platform.shape.modelServingPolicies" $root) "true" -}}
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
{{- if kindIs "map" (dig "policies" nil (index $v "modelServing" | default dict)) -}}
{{- $_ := set (index $v "modelServing" "policies") "enabled" $servingPolicies -}}
{{- end -}}
{{- /* substrate.postgres.enabled: `auto` resolved to the boolean the substrate
chart takes — bundled iff neither the platform Cluster nor a connection string
holds (agent-platform.substrate.postgresMode); the substrate release and the
connectivity release both read the resolved value. */ -}}
{{- if kindIs "map" (dig "postgres" nil (index $v "substrate" | default dict)) -}}
{{- $_ := set (index $v "substrate" "postgres") "enabled" (eq (include "agent-platform.substrate.postgresMode" $root) "bundled") -}}
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
The namespace the kagent component installs its workloads into, when it is one
the install would not otherwise create — the bundled engine's pre-install /
pre-upgrade hook creates it (templates/hooks/kagent-namespace.yaml). Empty
unless ALL of: the bundled engine is on (with the engine off this chart is a
pure renderer for a cluster's own Flux, and that cluster creates the namespace
out of band — the fleet's bases do), the kagent component is on,
kagent.namespaceOverride is set, and it differs from the namespace the platform
HelmReleases target (gitops.targetNamespace, else the release namespace — that
one helm-controller creates itself, install.createNamespace).
Why a hook, and why here: the kagent chart renders its objects into
kagent.namespaceOverride while its HelmRelease targets the platform namespace,
so helm-controller's createNamespace never creates `kagent`; the one chart
object that does — the connectivity chart's Namespace — sits in a release that
dependsOn kagent. A first install on a cluster without the namespace failed
every kagent attempt with `namespaces "kagent" not found` until the retries
were exhausted, and everything behind kagent waited
(giantswarm/agent-platform#306). The Namespace is deliberately NOT an object of
this release: the connectivity release renders and tracks it (adopting the
existing one on its install), and two Helm releases must never track one
object — a second tracked owner flips meta.helm.sh/release-name and the other
release's next upgrade fails on ownership metadata. A hook resource is not a
release object, and a Job that runs `kubectl create namespace` when it is
missing leaves exactly what the bases and the lab leave: a bare Namespace the
connectivity release adopts.
Usage: include "agent-platform.kagent.hookNamespace" .
*/}}
{{- define "agent-platform.kagent.hookNamespace" -}}
{{- if and (eq (include "agent-platform.engineEnabled" .) "true") (include "agent-platform.componentEnabled" (dict "root" . "name" "kagent")) -}}
{{- $ns := dig "namespaceOverride" "" (.Values.kagent | default dict) -}}
{{- $target := .Values.gitops.targetNamespace | default .Release.Namespace -}}
{{- if and $ns (ne $ns $target) }}{{ $ns }}{{ end -}}
{{- end -}}
{{- end -}}

{{/*
The Helm hook events the hook ServiceAccount + ClusterRoleBinding (hooks/rbac.yaml)
are created for: pre-delete for the ordered teardown, and pre-install,pre-upgrade
too while the kagent namespace hook renders — it runs as that account (creating
a namespace is cluster-scoped, the namespaced <release>-self identity cannot).
*/}}
{{- define "agent-platform.hooks.serviceAccountEvents" -}}
{{- if include "agent-platform.kagent.hookNamespace" . }}pre-install,pre-upgrade,pre-delete{{ else }}pre-delete{{ end -}}
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

{{/*
The platform HelmReleases in teardown order: waves of release names, each wave
the releases no release still standing dependsOn — so a release is uninstalled
only after every release whose objects are its CRs. Helm's uninstall deletes a
release's objects and fails ("failed to delete release: <name>") when one of
their kinds is already gone — its CRD chart uninstalled first — and
helm-controller then retries that uninstall until the teardown hook times out
(measured on the ATS kind smoke: substrate's SandboxConfig racing
substrate-crds). The releases of one wave uninstall concurrently. The graph is
the roster's dependsOn, filtered as components.yaml filters it (a reference to a
toggled-off component names no release); a cycle fails the render.
Usage: include "agent-platform.teardownWaves" . | fromYamlArray  (a list of lists)
*/}}
{{- define "agent-platform.teardownWaves" -}}
{{- $root := . -}}
{{- $deps := dict -}}
{{- range $key, $c := .Values.components -}}
{{- if and (include "agent-platform.componentEnabled" (dict "root" $root "name" $key)) (hasKey $c "chart") -}}
{{- $on := list -}}
{{- range ($c.dependsOn | default list) -}}
{{- if and (hasKey $root.Values.components .) (include "agent-platform.componentEnabled" (dict "root" $root "name" .)) (hasKey (index $root.Values.components .) "chart") -}}
{{- $on = append $on (index $root.Values.components .).chart -}}
{{- end -}}
{{- end -}}
{{- $_ := set $deps $c.chart $on -}}
{{- end -}}
{{- end -}}
{{- $waves := list -}}
{{- $remaining := keys $deps | sortAlpha -}}
{{- range until (len $deps) -}}
{{- if $remaining -}}
{{- $wave := list -}}
{{- range $name := $remaining -}}
{{- $needed := false -}}
{{- range $other := $remaining -}}
{{- if has $name (index $deps $other) -}}{{- $needed = true -}}{{- end -}}
{{- end -}}
{{- if not $needed -}}{{- $wave = append $wave $name -}}{{- end -}}
{{- end -}}
{{- if not $wave -}}{{- fail (printf "components.*.dependsOn is cyclic among %s; the ordered teardown needs an acyclic graph" (join ", " $remaining)) -}}{{- end -}}
{{- $waves = append $waves $wave -}}
{{- $next := list -}}
{{- range $name := $remaining -}}{{- if not (has $name $wave) -}}{{- $next = append $next $name -}}{{- end -}}{{- end -}}
{{- $remaining = $next -}}
{{- end -}}
{{- end -}}
{{- toYaml $waves -}}
{{- end -}}

{{/*
The teardown-releases hook's script: one `kubectl delete --wait` per wave of
agent-platform.teardownWaves, in gitops.namespace (the release namespace with
the engine on). Each wave's releases uninstall concurrently; the next wave
starts when helm-controller has removed the last HelmRelease of the previous one.
*/}}
{{- define "agent-platform.teardownScript" -}}
{{- $ns := .Values.gitops.namespace | default .Release.Namespace -}}
# The platform HelmReleases in reverse dependency order (a CRD chart's release
# only after the releases whose objects are its CRs); kubectl waits for
# helm-controller to uninstall a wave before the next one starts.
{{- range $i, $wave := include "agent-platform.teardownWaves" . | fromYamlArray }}
echo "wave {{ add1 $i }}: {{ join " " $wave }}"
kubectl delete helmreleases.helm.toolkit.fluxcd.io --namespace {{ $ns }} --ignore-not-found --wait --timeout=5m {{ join " " $wave }}
{{- end }}
{{- end -}}

{{/*
Self-management resolved: "true" when this release renders its own
OCIRepository + HelmRelease (templates/self/) and the admission policy that
makes the Helm CLI day-0 only, empty otherwise. gitops.self.enabled is
`auto` (follows the bundled engine: components.flux.enabled), `true` or
`false`. `true` without the engine is refused: on a cluster that runs its own
Flux that Flux holds the chart's HelmRelease (README, "Clusters that run
Flux"), and a self HelmRelease under a foreign helm-controller would run as
whatever account that controller impersonates.
*/}}
{{- define "agent-platform.selfEnabled" -}}
{{- $v := (.Values.gitops.self | default dict).enabled -}}
{{- $engine := eq (include "agent-platform.engineEnabled" .) "true" -}}
{{- if or (kindIs "invalid" $v) (and (kindIs "string" $v) (eq $v "auto")) -}}
{{- if $engine }}true{{ end -}}
{{- else if eq (toString $v) "true" -}}
{{- if not $engine -}}
{{- fail "gitops.self.enabled=true needs the bundled Flux engine (components.flux.enabled=true): with the engine off, the cluster's own Flux holds this chart's HelmRelease (README, Clusters that run Flux). Set gitops.self.enabled to auto (the default) or false" -}}
{{- end -}}
true
{{- else if eq (toString $v) "false" -}}
{{- else -}}
{{- fail (printf "gitops.self.enabled=%v is not one of auto, true, false" $v) -}}
{{- end -}}
{{- end -}}

{{/*
The identity the self-management hooks run as: a ServiceAccount in the release
namespace with a namespaced Role (templates/self/rbac.yaml) — a regular chart
object, not a hook: the detached resumer Job runs AFTER the post-install hooks
completed, when a hook-managed ServiceAccount (hook-succeeded) is already gone.
*/}}
{{- define "agent-platform.self.serviceAccountName" -}}
{{- printf "%s-self" .Release.Name -}}
{{- end -}}

{{/*
The Secret the self HelmRelease reads its values from (valuesFrom, optional:
false) and the values hook writes (the USER-SUPPLIED values of the release —
`helm get values` — never the merged tree, which would pin every component
versionRange at first-install time). Fixed name: the admission policy's message
and the README name it.
*/}}
{{- define "agent-platform.self.valuesSecretName" -}}
agent-platform-values
{{- end -}}

{{/*
The ValidatingAdmissionPolicy (cluster-scoped) and its binding that make the
Helm CLI day-0 only, one pair per release: <release>-self-managed-<namespace>.
*/}}
{{- define "agent-platform.self.policyName" -}}
{{- printf "%s-self-managed-%s" .Release.Name .Release.Namespace -}}
{{- end -}}

{{/*
The hand-back annotation on the release namespace: with it present the
admission policy admits the Helm CLI's storage write again, so
`helm upgrade --set gitops.self.enabled=false --force-conflicts` can hand the
release back to the CLI.
*/}}
{{- define "agent-platform.self.handBackAnnotation" -}}
agent-platform.giantswarm.io/helm-cli
{{- end -}}

{{/*
The ServiceAccount the self HelmRelease runs as and the only identity the
admission policy admits to write this release's Helm storage: the bundled
engine's tenant identity, or gitops.serviceAccountName when set (the same
resolution the platform HelmReleases use in components.yaml).
*/}}
{{- define "agent-platform.self.releaseServiceAccountName" -}}
{{- .Values.gitops.serviceAccountName | default (include "agent-platform.tenantServiceAccountName" .) -}}
{{- end -}}

{{/*
Semver range the self OCIRepository follows. gitops.self.versionRange when set;
otherwise derived from the running chart's version: `>=<version> <next
major>.0.0` — a release follows patch and minor releases of its own major, never
a downgrade (a fixed floor would let source-controller pick a LOWER tag than the
one the CLI just installed), and the range moves forward with every version
the controller applies. Build metadata (helm-controller renders the chart as
<version>+<oci digest>) is dropped; a pre-release floor is kept.
*/}}
{{- define "agent-platform.self.versionRange" -}}
{{- with .Values.gitops.self.versionRange -}}
{{- . -}}
{{- else -}}
{{- $v := semver .Chart.Version -}}
{{- $floor := printf "%d.%d.%d" $v.Major $v.Minor $v.Patch -}}
{{- with $v.Prerelease }}{{ $floor = printf "%s-%s" $floor . }}{{ end -}}
{{- printf ">=%s <%d.0.0" $floor (add1 $v.Major) -}}
{{- end -}}
{{- end -}}

{{/*
Render guards of self-management, evaluated only when it is on:

1. Kubernetes >= 1.30: the admission policy is admissionregistration.k8s.io/v1
   ValidatingAdmissionPolicy (GA in 1.30). An older apiserver would fail the
   install at apply time with `no matches for kind`; the render says why and
   names the way out (gitops.self.enabled=false keeps the Helm CLI as the
   day-2 tool). `helm template --kube-version` exercises it offline.
2. The hand-back annotation together with self-management on: the policy would
   admit the Helm CLI while the bundled helm-controller holds the release — two
   writers on one release, which is what the whole shape forbids (a CLI
   revision pending while the controller reconciles is unlocked and upgraded
   with whatever the values Secret holds). The hand-back upgrade carries
   gitops.self.enabled=false; a stale annotation is removed. `lookup` is live
   under the Helm CLI, --dry-run=server and helm-controller (where the guard
   surfaces on the self HelmRelease for the duration of a hand-back), empty
   under `helm template`.
*/}}
{{- define "agent-platform.validateSelf" -}}
{{- if eq (include "agent-platform.selfEnabled" .) "true" -}}
{{- if semverCompare "<1.30.0-0" .Capabilities.KubeVersion.Version -}}
{{- fail (printf "self-management (gitops.self.enabled) needs Kubernetes >= 1.30 for its ValidatingAdmissionPolicy (admissionregistration.k8s.io/v1); this cluster reports %s. Set gitops.self.enabled=false to install without it — the Helm CLI then stays the day-2 tool" .Capabilities.KubeVersion.Version) -}}
{{- end -}}
{{- $ns := lookup "v1" "Namespace" "" .Release.Namespace -}}
{{- $ann := include "agent-platform.self.handBackAnnotation" . -}}
{{- if and $ns (eq (dig "metadata" "annotations" $ann "" $ns) "allow") -}}
{{- fail (printf "namespace %s carries the hand-back annotation %s=allow while gitops.self.enabled resolves to true: the Helm CLI and the bundled helm-controller would both write release %s. Finish the hand-back — helm upgrade … --set gitops.self.enabled=false --force-conflicts — or remove the annotation to keep the release self-managed" .Release.Namespace $ann .Release.Name) -}}
{{- end -}}
{{- end -}}
{{- end -}}

{{/*
The helm-and-shell image the hooks that need helm run (gitops.hooks.helmImage).
*/}}
{{- define "agent-platform.hooks.helmImage" -}}
{{- $i := .Values.gitops.hooks.helmImage -}}
{{- printf "%s/%s:%s" $i.registry $i.repository $i.tag -}}
{{- end -}}
