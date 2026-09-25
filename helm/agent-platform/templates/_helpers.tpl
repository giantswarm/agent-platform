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
and `_` goes — a branch build's long prerelease version (the superseded abs
shape `3.19.1-dev.<branch>.<date>.h<sha>`; gitsemver 3's
`X.Y.Z-r<branch-hash>t<time>h<sha>` holds neither `.` nor `-`, so only the `_`
is left to land on) made the cut land on the `_` once, and the apiserver
rejected every object of the release. tests/verify-labels.py.
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
The layer an OCIRepository of this chart takes from the artifact: the Helm
chart, copied as-is for the HelmRelease's chartRef (the form Flux documents).
Without a selector source-controller extracts layers[0], and a signed chart
carries a second layer, its provenance, which `helm push` orders by digest: on
roughly every other signed release the provenance comes first and the
OCIRepository fails with "requires gzip-compressed body" (cloudnative-pg 0.29.1,
giantswarm/agent-platform#649). The artifact revision stays <tag>@<manifest
digest>, so the chart version helm-controller derives from it does not change.
*/}}
{{- define "agent-platform.chartLayerSelector" -}}
layerSelector:
  mediaType: application/vnd.cncf.helm.chart.content.v1.tar+gzip
  operation: copy
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
  klaus-gateway: with klausGateway.routing.store: valkey, the platform's own
    Valkey fills in what the routing.valkey block leaves unset — url from the
    valkey release's Service (agent-platform.valkeyAddress), existingSecret and
    passwordKey from the Secret and key the valkey release authenticates its
    default user with (agent-platform.valkeySecretName / valkeyPasswordKey), so
    the switch is one line. These are defaults, not the single-source rule
    above: an operator's own url (an out-of-band Valkey), Secret or key wins.
  kagent: harness.snapshotLocation from kagent.harness.snapshotStore while the
    store block renders the bucket (agent-platform.kagent.snapshotLocation);
    substrateWorkerPool.workerImage from the Substrate release THIS chart pins
    (agent-platform.substrate.workerImage: the substrate block's image.registry
    and image.repository, ateom-gvisor, the floor of components.substrate.versionRange) — never the
    worker the kagent build was published against, so the worker and the
    atelet are one Substrate release whatever kagent build the range admits
    (giantswarm/agent-platform#466). An installation's own workerImage stands
    only while its tag is that release (a mirror by another path).
  model-manager: kagent.disableWiring: true while components.kagent is off —
    on by default with no backend (giantswarm/agent-platform#329), the release
    must not wire ModelConfigs into a kagent the installation does not run;
    with kagent on the block's own value stands. Likewise muster.mcpServer.enabled:
    false while components.muster is off — the MCPServer CRD ships with muster,
    so the MCP surface follows it (the connectivity chart's muster peer policy
    reads the same toggle); the REST API stays. And oauth.enabled: false while
    muster's OAuth server is off (muster.muster.oauth.server.enabled, the
    platform's one login): a platform without a login has no issuer for a
    resource server to trust — the lab shape of examples/kind-lab-dex.yaml —
    the way the muster discovery label derives from the same toggle. The
    managers' login itself (oauth.dex.issuerURL, clientID, existingSecret,
    trustedAudiences, baseURL) is not derived here but on the shaped values
    (agent-platform.identity.apply), so the connectivity release guards and
    wires the same block.
  substrate: atelet.serviceAccount.annotations and
    ateApiServer.serviceAccount.annotations gain eks.amazonaws.com/role-arn,
    the IRSA role the store block renders, while it does (a differing explicit
    role fails the render); atelet.extraEnv and ateApiServer.extraEnv gain the
    S3 environment of the s3proxy façade (capz, or the façade alone) next to
    an installation's own entries (one of the derived names fails the render);
    postgres.connectionStringSecretRef, on the platform Cluster —
    the derived CNPG connection Secret <postgres.clusterName>-substrate-app
    (key uri) the connectivity release's hook writes into ate-system for
    postgres.databases.substrate (agent-platform.substrate.postgresMode; the
    `auto` of substrate.postgres.enabled itself is resolved by
    agent-platform.shape.apply, with the other cluster-shape knobs). Its
    atelet.imageCache.pinnedImages is NOT derived here: the kagent release's
    ConfigMap kagent-images feeds it through the HelmRelease's valuesFrom
    (components.substrate.valuesFromRefs), so no copy of a digest lives here.
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
{{- if eq .name "cluster-manager" -}}
{{- /* model-manager's namespace: where the model-manager component lands — the
platform's own namespace (gitops.targetNamespace, else the release namespace).
create_node_pool writes the kserve backend ConfigMap of model-manager's
runtime-registration contract there. */ -}}
{{- $ns := .root.Values.gitops.targetNamespace | default .root.Release.Namespace -}}
{{- $own := dig "modelManager" "namespace" "" (index .root.Values "cluster-manager" | default dict) -}}
{{- if and $own (ne $own $ns) -}}
{{- fail (printf "cluster-manager.modelManager.namespace (%s) differs from the platform's namespace (%s), where the model-manager component lands: cluster-manager registers a serving cluster's kserve backend through model-manager's ConfigMap there — leave cluster-manager.modelManager.namespace unset" $own $ns) -}}
{{- end -}}
{{- $_ := set $derived "modelManager" (dict "namespace" $ns) -}}
{{- end -}}
{{- if eq .name "klaus-gateway" -}}
{{- $kg := .root.Values.klausGateway | default dict -}}
{{- if and (eq (dig "routing" "store" "" $kg) "valkey") (include "agent-platform.componentEnabled" (dict "root" .root "name" "valkey")) -}}
{{- $own := dig "routing" "valkey" dict $kg -}}
{{- $valkey := dict -}}
{{- range $k, $v := dict "url" (include "agent-platform.valkeyAddress" .root) "existingSecret" (include "agent-platform.valkeySecretName" .root) "passwordKey" (include "agent-platform.valkeyPasswordKey" .root) -}}
{{- if and $v (not (dig $k "" $own)) -}}{{- $_ := set $valkey $k $v -}}{{- end -}}
{{- end -}}
{{- if $valkey -}}{{- $_ := set $derived "routing" (dict "valkey" $valkey) -}}{{- end -}}
{{- end -}}
{{- end -}}
{{- if and (eq .name "kagent") (include "agent-platform.substrateStore.mode" .root) -}}
{{- $_ := set $derived "harness" (dict "snapshotLocation" (include "agent-platform.kagent.snapshotLocation" .root)) -}}
{{- end -}}
{{- if eq .name "kagent" -}}
{{- /* The worker image follows the chart's Substrate pin, not the kagent build's
stamp (giantswarm/agent-platform#466): a 0.0.30 worker under a 0.0.27 atelet
booted no golden actor (bundles/pause became bundles/_pause) and nothing named
the skew. The derived value lands over the forwarded block's copy; an own
value stands only while its tag is the pinned release. */ -}}
{{- $pin := include "agent-platform.substrate.pinnedVersion" .root -}}
{{- $image := include "agent-platform.substrate.workerImage" .root -}}
{{- $own := dig "substrateWorkerPool" "workerImage" "" (.root.Values.kagent | default dict) -}}
{{- if $own -}}
{{- $ownTag := regexFind ":[^:/@]+(@sha256:[0-9a-f]+)?$" $own | trimPrefix ":" | splitList "@" | first -}}
{{- if ne $ownTag $pin -}}
{{- fail (printf "kagent.substrateWorkerPool.workerImage (%s) does not carry the Substrate release this chart pins (%s, the floor of components.substrate.versionRange): the worker and the atelet are one Substrate release — leave it unset (the chart derives %s; a mirror sets substrate.image.registry) or name an ateom-gvisor image tagged %s" $own $pin $image $pin) -}}
{{- end -}}
{{- else -}}
{{- $_ := set $derived "substrateWorkerPool" (dict "workerImage" $image) -}}
{{- end -}}
{{- end -}}
{{- if and (eq .name "model-manager") (not (include "agent-platform.componentEnabled" (dict "root" .root "name" "kagent"))) -}}
{{- $_ := set $derived "kagent" (dict "disableWiring" true) -}}
{{- end -}}
{{- if and (eq .name "model-manager") (not (include "agent-platform.componentEnabled" (dict "root" .root "name" "muster"))) -}}
{{- $_ := set $derived "muster" (dict "mcpServer" (dict "enabled" false)) -}}
{{- end -}}
{{- if and (eq .name "model-manager") (not (dig "muster" "oauth" "server" "enabled" true (.root.Values.muster | default dict))) -}}
{{- $_ := set $derived "oauth" (dict "enabled" false) -}}
{{- end -}}
{{- if and (eq .name "substrate") (eq (include "agent-platform.substrateStore.crossplane" .root) "aws") -}}
{{- $arn := include "agent-platform.substrateStore.awsRoleArn" .root -}}
{{- range $key := list "atelet" "ateApiServer" -}}
{{- $own := dig $key "serviceAccount" "annotations" "eks.amazonaws.com/role-arn" "" ($.root.Values.substrate | default dict) -}}
{{- if and $own (ne $own $arn) -}}
{{- fail (printf "substrate.%s.serviceAccount.annotations[eks.amazonaws.com/role-arn] (%s) differs from the IRSA role kagent.harness.snapshotStore renders (%s): the store block names the role (crossplane.aws.roleName, else the bucket name) — leave the annotation unset" $key $own $arn) -}}
{{- end -}}
{{- $_ := set $derived $key (dict "serviceAccount" (dict "annotations" (dict "eks.amazonaws.com/role-arn" $arn))) -}}
{{- end -}}
{{- end -}}
{{- if and (eq .name "substrate") (include "agent-platform.substrateStore.s3proxy" .root) -}}
{{- $env := include "agent-platform.substrateStore.s3proxyEnv" .root | fromJsonArray -}}
{{- $names := list -}}{{- range $env -}}{{- $names = append $names .name -}}{{- end -}}
{{- range $key := list "atelet" "ateApiServer" -}}
{{- $own := dig $key "extraEnv" list ($.root.Values.substrate | default dict) -}}
{{- range $own -}}
{{- if has .name $names -}}
{{- fail (printf "substrate.%s.extraEnv names %s, which kagent.harness.snapshotStore derives for the s3proxy façade (AWS_ENDPOINT_URL, AWS_REGION, AWS_S3_USE_PATH_STYLE and the key pair from the Secret substrate-s3proxy) — leave the S3 variables to the store block" $key .name) -}}
{{- end -}}
{{- end -}}
{{- $_ := set $derived $key (dict "extraEnv" (concat $own $env)) -}}
{{- end -}}
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
{{- if and (eq .name "kserve-llmisvc-resources") (include "agent-platform.componentEnabled" (dict "root" .root "name" "modelServing")) -}}
{{- /* The models Gateway (modelServing.modelsGateway, rendered by the
connectivity release in the platform's namespace) is the Gateway every
LLMInferenceService route attaches to: the llm-d controller reads it from the
shared inferenceservice-config ConfigMap its own release renders. */ -}}
{{- $mg := dig "modelsGateway" dict (.root.Values.modelServing | default dict) -}}
{{- if $mg.enabled -}}
{{- $gw := printf "%s/%s" (.root.Values.gitops.targetNamespace | default .root.Release.Namespace) ($mg.name | default "models") -}}
{{- $own := dig "kserve" "controller" "gateway" "ingressGateway" "kserveGateway" "" (index .root.Values "kserve-llmisvc-resources" | default dict) -}}
{{- if and $own (ne $own $gw) -}}
{{- fail (printf "kserve-llmisvc-resources.kserve.controller.gateway.ingressGateway.kserveGateway (%s) differs from the models Gateway the connectivity release renders (%s): every LLMInferenceService route attaches to modelServing.modelsGateway — set modelServing.modelsGateway.name, or modelsGateway.enabled: false to bring a Gateway of your own, and leave the kserve-llmisvc-resources copy unset" $own $gw) -}}
{{- end -}}
{{- $_ := set $derived "kserve" (dict "controller" (dict "gateway" (dict "ingressGateway" (dict "kserveGateway" $gw)))) -}}
{{- end -}}
{{- end -}}
{{- $derived | toJson -}}
{{- end -}}

{{/*
Drop the keys named by dotted `paths` from `vals` when their value is empty (an
empty string, list or map), at any depth; a parent left empty goes with it. For a component chart that stamps a
default at publish and would take an empty override as THE value (the kagent
chart's substrateWorkerPool.workerImage, harness.image), or for a HelmRelease
whose spec.values must not shadow what its valuesFrom supplies (Flux lets
spec.values win: the substrate release's atelet.imageCache.pinnedImages).
Emits JSON. Usage: include "agent-platform.omitEmpty" (dict "vals" $vals "paths" $c.omitEmptyKeys) | fromJson
*/}}
{{- define "agent-platform.omitEmpty" -}}
{{- $vals := .vals -}}
{{- range .paths -}}
{{- $vals = include "agent-platform.omitEmptyPath" (dict "vals" $vals "segs" (splitList "." .)) | fromJson -}}
{{- end -}}
{{- $vals | toJson -}}
{{- end -}}

{{/*
Drop the key at a dotted path from `vals` (components.yaml omitKeys): a
top-level key, or a nested one — `harness.snapshotStore` — whose parent then
stays only while it still holds other keys. Emits the JSON of the result.
Usage: include "agent-platform.omitPath" (dict "vals" $vals "path" "a.b")
*/}}
{{- define "agent-platform.omitPath" -}}
{{- $segs := splitList "." .path -}}
{{- $vals := .vals -}}
{{- if eq (len $segs) 1 -}}
{{- $vals = omit $vals (first $segs) -}}
{{- else if kindIs "map" (index $vals (first $segs)) -}}
{{- $child := include "agent-platform.omitPath" (dict "vals" (index $vals (first $segs)) "path" (join "." (rest $segs))) | fromJson -}}
{{- if empty $child }}{{- $vals = omit $vals (first $segs) }}{{- else }}{{- $_ := set $vals (first $segs) $child }}{{- end -}}
{{- end -}}
{{- $vals | toJson -}}
{{- end -}}

{{- define "agent-platform.omitEmptyPath" -}}
{{- $vals := .vals -}}
{{- $key := first .segs -}}
{{- if hasKey $vals $key -}}
{{- if eq (len .segs) 1 -}}
{{- if empty (index $vals $key) }}{{- $vals = omit $vals $key }}{{- end -}}
{{- else if kindIs "map" (index $vals $key) -}}
{{- $child := include "agent-platform.omitEmptyPath" (dict "vals" (index $vals $key) "segs" (rest .segs)) | fromJson -}}
{{- if empty $child }}{{- $vals = omit $vals $key }}{{- else }}{{- $_ := set $vals $key $child }}{{- end -}}
{{- end -}}
{{- end -}}
{{- $vals | toJson -}}
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
Agent Substrate's snapshot store, kagent.harness.snapshotStore: the connectivity
chart renders the S3 bucket and the IRSA role, or the Azure account behind the
s3proxy façade (its templates/substrate/
crossplane-aws.yaml, the same helpers there); this chart derives what the two
consumers read from it — kagent.harness.snapshotLocation for the kagent release,
the role annotation of the substrate release's atelet and ate-api-server
ServiceAccounts (componentDerivedValues).
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

{{/* The role's ARN: arn:aws (arn:aws-cn in the China partition), the account, aws.roleName or the bucket name. */}}
{{- define "agent-platform.substrateStore.awsRoleArn" -}}
{{- $xp := (include "agent-platform.substrateStore.block" . | fromJson).crossplane -}}
{{- if not (regexMatch "^[0-9]{12}$" (toString $xp.aws.accountId)) -}}
{{- fail (printf "kagent.harness.snapshotStore.crossplane.aws.accountId (%v) must be the 12-digit AWS account id, quoted as a string" $xp.aws.accountId) -}}
{{- end -}}
{{- $partition := "arn:aws" -}}{{- if hasPrefix "cn-" $xp.region }}{{- $partition = "arn:aws-cn" }}{{- end -}}
{{- printf "%s:iam::%s:role/%s" $partition $xp.aws.accountId ($xp.aws.roleName | default $xp.aws.bucketName) -}}
{{- end -}}

{{/*
The platform Harness's snapshot location: kagent.harness.snapshotLocation when
set, else the one kagent.harness.snapshotStore renders (the bucket on aws, the
container behind the façade on capz or with s3proxy alone); "" with neither
(validateSubstrate refuses that with kagent on). An explicit value that
disagrees with the store's fails the render (agent-platform.substrateStore.validate).
*/}}
{{- define "agent-platform.kagent.snapshotLocation" -}}
{{- if include "agent-platform.substrateStore.mode" . -}}
{{- include "agent-platform.substrateStore.location" . -}}
{{- else -}}
{{- dig "harness" "snapshotLocation" "" (.Values.kagent | default dict) -}}
{{- end -}}
{{- end -}}

{{/*
Agent Substrate is kagent API v2's runtime: refuse the shapes that install a
kagent with nothing to run agents on, or a Substrate with nothing to start
against, at render time — and, where the render is live, a cluster that cannot
run it.
  * kagent on with substrate or substrate-crds switched off (both follow kagent
    unless switched explicitly).
  * kagent on without a snapshot location — neither kagent.harness.snapshotLocation
    nor kagent.harness.snapshotStore.crossplane: the platform Harness's
    snapshotPolicy.location is the installation's snapshot store — an S3 bucket
    on CAPA with IRSA (the store block provisions it), an S3-compatible store
    with its endpoint in substrate.atelet.extraEnv, a lab's in-cluster store —
    and has no default.
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
{{- include "agent-platform.substrateStore.validate" . -}}
{{- $kagent := eq (include "agent-platform.componentEnabled" (dict "root" . "name" "kagent")) "true" -}}
{{- $substrate := eq (include "agent-platform.componentEnabled" (dict "root" . "name" "substrate")) "true" -}}
{{- $crds := eq (include "agent-platform.componentEnabled" (dict "root" . "name" "substrate-crds")) "true" -}}
{{- if and $kagent (not (and $substrate $crds)) -}}
{{- fail "components.kagent.enabled is true but components.substrate.enabled or components.substrate-crds.enabled is not: kagent API v2 runs every agent as an Agent Substrate actor and has no runtime without it; turn both on (they follow components.kagent when left unset)" -}}
{{- end -}}
{{- if and $substrate (not $crds) -}}
{{- fail "components.substrate.enabled is true but components.substrate-crds.enabled is not: the substrate chart's WorkerPool, SandboxConfig and CSIDriverConfig objects need the ate.dev CRDs the substrate-crds chart renders; turn both on" -}}
{{- end -}}
{{- if and $kagent (not (include "agent-platform.kagent.snapshotLocation" .)) -}}
{{- fail "kagent.harness.snapshotLocation is required when components.kagent is on: the Substrate snapshot location the platform Harness writes the actors' snapshots to (snapshotPolicy.location), an object-store URL such as s3://<bucket>/<prefix> — the installation's S3 bucket (IRSA on CAPA; kagent.harness.snapshotStore.crossplane provisions it and derives the location), an S3-compatible store with its endpoint and credentials in substrate.atelet.extraEnv, or a lab's in-cluster store (substrate.rustfs.enabled: true, s3://ate-snapshots/<prefix>)" -}}
{{- end -}}
{{- if $substrate -}}
{{- include "agent-platform.substrate.validateRange" . -}}
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
The floor of a Flux semver range as the roster carries it (components.<name>
.versionRange): the version of its `>=` (or `>`, `^`, `~`, `=`) term, or the
range itself when it is one exact version (a BOM pin, `1.0.0`). Empty for
a range without a floor (`0.x`, `*`, a `<` term alone) — the caller decides what
that means. Terms are separated by spaces or commas (Masterminds/semver, which
source-controller uses).
Usage: include "agent-platform.semverRangeFloor" "<range>"
*/}}
{{- define "agent-platform.semverRangeFloor" -}}
{{- $floor := "" -}}
{{- range splitList " " (. | replace "," " " | trim) -}}
{{- $term := trim . -}}
{{- if and (not $floor) $term (not (hasPrefix "<" $term)) -}}
{{- $v := $term | trimPrefix ">=" | trimPrefix ">" | trimPrefix "^" | trimPrefix "~" | trimPrefix "=" | trimPrefix "v" -}}
{{- if regexMatch "^[0-9]+\\.[0-9]+\\.[0-9]+(-[0-9A-Za-z.-]+)?$" $v -}}
{{- $floor = $v -}}
{{- end -}}
{{- end -}}
{{- end -}}
{{- $floor -}}
{{- end -}}

{{/*
The Substrate release this chart pins: the floor of components.substrate
.versionRange (an exact pin is its own floor). The gVisor worker image every
kagent WorkerPool runs is derived from it (agent-platform.substrate.workerImage),
so a range without a floor fails the render here, naming the shape the range
takes (giantswarm/agent-platform#466).
Usage: include "agent-platform.substrate.pinnedVersion" $root
*/}}
{{- define "agent-platform.substrate.pinnedVersion" -}}
{{- $range := dig "substrate" "versionRange" "" .Values.components -}}
{{- $floor := include "agent-platform.semverRangeFloor" $range -}}
{{- if not $floor -}}
{{- fail (printf "components.substrate.versionRange %q has no floor: the Substrate worker image the kagent WorkerPool runs (ateom-gvisor) is derived from the range's floor, so the range is one exact version or `>=X.Y.Z <X.(Y+1).0` (giantswarm/agent-platform#466)" $range) -}}
{{- end -}}
{{- $floor -}}
{{- end -}}

{{/*
The gVisor worker image of the Substrate release this chart pins:
<substrate.image.registry>/<substrate.image.repository>/ateom-gvisor:<agent-platform.substrate.pinnedVersion>
— the registry and repository the substrate block names for the control
plane's images (a mirror sets them there, once, for both), the tag the
atelet's. Every release of the line publishes atelet and ateom-gvisor under
the same tag.
Usage: include "agent-platform.substrate.workerImage" $root
*/}}
{{- define "agent-platform.substrate.workerImage" -}}
{{- $image := dig "image" (dict) (.Values.substrate | default dict) -}}
{{- $registry := dig "registry" "gsoci.azurecr.io" $image -}}
{{- $repository := dig "repository" "giantswarm/substrate" $image -}}
{{- printf "%s/%s/ateom-gvisor:%s" (trimSuffix "/" $registry) (trimAll "/" $repository) (include "agent-platform.substrate.pinnedVersion" .) -}}
{{- end -}}

{{/*
Fail the render when components.substrate.versionRange could resolve to a
Substrate release of another runtime contract than the one it pins
(giantswarm/agent-platform#466). The worker image follows the range's FLOOR and
the atelet follows what Flux RESOLVES, so the two are one runtime only while the
range confines one contract — and the line changes one only in a minor: a patch
release is carried patches or a rebuild on the same upstream pin (the worker and
atelet bundle layout stays), a re-pin onto another upstream release is at least
a minor. So the range is an exact version (a BOM pin, `1.0.0`) or a floor with
the ceiling of its own minor, `>=X.Y.Z <X.(Y+1).0` — and no `-0` anywhere: Flux's
Masterminds semver skips every prerelease while no bound of a range carries one
and evaluates them all once one does, so `<1.1.0-0` would admit the line's dev
builds. A later patch of the pinned minor may reach the control plane ahead of
the worker, a `1.1.0` never. `0.x`, `~1.0.0`, `^1.0.0`, `<1.1.0`, a `-0` bound, a
`<=` ceiling, a patch ceiling and the former `>=X.Y.Z-gs.N <X.Y.(Z+1)-0` are
refused. Called by
agent-platform.validateSubstrate while components.substrate is on.
*/}}
{{- define "agent-platform.substrate.validateRange" -}}
{{- $range := dig "substrate" "versionRange" "" .Values.components | replace "," " " | trim -}}
{{- $terms := list -}}
{{- range splitList " " $range -}}{{- if . -}}{{- $terms = append $terms . -}}{{- end -}}{{- end -}}
{{- $version := "^[0-9]+\\.[0-9]+\\.[0-9]+(-[0-9A-Za-z.-]+)?$" -}}
{{- $ok := false -}}
{{- if and (eq (len $terms) 1) (regexMatch $version (first $terms)) -}}
{{- $ok = true -}}
{{- else if and (eq (len $terms) 2) (hasPrefix ">=" (first $terms)) (hasPrefix "<" (last $terms)) (not (hasPrefix "<=" (last $terms))) -}}
{{- $floor := trimPrefix ">=" (first $terms) -}}
{{- if regexMatch $version $floor -}}
{{- $parts := splitList "." (first (splitList "-" $floor)) -}}
{{- $ok = eq (trimPrefix "<" (last $terms)) (printf "%s.%d.0" (index $parts 0) (add1 (atoi (index $parts 1)))) -}}
{{- end -}}
{{- end -}}
{{- if not $ok -}}
{{- fail (printf "components.substrate.versionRange %q does not confine one Substrate release and its patches: the kagent WorkerPool's worker image (ateom-gvisor) follows the range's floor, the atelet follows the release Flux resolves, and only a minor of the line changes the runtime contract the two share — so the range is one exact version (1.0.0) or a floor with the ceiling of its own minor and no -0 bound (>=1.0.0 <1.1.0 — a -0 anywhere makes Flux evaluate the line's dev builds against the range); a worker and an atelet of different contracts boot no golden actor (giantswarm/agent-platform#466)" $range) -}}
{{- end -}}
{{- end -}}

{{/*
The first release of the Substrate line (giantswarm/substrate, the chart
components.substrate pins) whose WorkerPool CRD carries spec.template
.topologySpreadConstraints and spec.template.podAntiAffinity — the carried patch
tracked as giantswarm/giantswarm#37797 (#37742 row 46), in every release of the
line's stable semver. agent-platform.validateWorkerPool refuses the two keys
while components.substrate.versionRange's floor is below it — every earlier
release prunes them silently — and forwards them verbatim from it on
(giantswarm/agent-platform#472). One line, no comment inside the define:
tests/verify-workerpool.py reads the value from this file.
*/}}
{{- define "agent-platform.substrate.workerPoolSpreadFloor" -}}1.0.0{{- end -}}

{{/*
Fail the render when kagent.substrateWorkerPool.template would not reach the
cluster as written (giantswarm/agent-platform#457, #472). The kagent chart
forwards the template verbatim into WorkerPool.spec.template (toYaml), and the
WorkerPool CRD is a structural schema without preserve-unknown-fields, so what
the schema does not know is PRUNED at admission — the values look applied and
do nothing — and what it knows but cannot type fails only when helm-controller
applies the kagent release, on every installation that carries it. Named here,
at the render, instead:
  - a nodeSelector value that is not a string — the CPU generation pin written
    `karpenter.k8s.aws/instance-generation: 6` instead of "6" (nodeSelector is
    map[string]string);
  - `topologySpreadConstraints` and `podAntiAffinity`, which the Substrate line
    carries only from the release agent-platform.substrate.workerPoolSpreadFloor
    names (1.0.0): refused while components.substrate.versionRange's floor
    is below it, forwarded verbatim from it on;
  - any other key WorkerPool.spec.template does not have (labels, annotations,
    nodeSelector, tolerations, priorityClassName, nodeAffinity, resources are
    the fields of the pinned line; a typo such as `nodeSelectors` would be
    pruned in silence).
*/}}
{{- define "agent-platform.validateWorkerPool" -}}
{{- $template := dig "substrateWorkerPool" "template" (dict) .Values.kagent -}}
{{- $known := list "labels" "annotations" "nodeSelector" "tolerations" "priorityClassName" "nodeAffinity" "resources" -}}
{{- $gated := list "topologySpreadConstraints" "podAntiAffinity" -}}
{{- $spreadFloor := include "agent-platform.substrate.workerPoolSpreadFloor" . -}}
{{- $range := dig "substrate" "versionRange" "" .Values.components -}}
{{- $floor := include "agent-platform.semverRangeFloor" $range -}}
{{- range $key, $value := $template -}}
{{- if has $key $gated -}}
{{- if not $spreadFloor -}}
{{- fail (printf "kagent.substrateWorkerPool.template.%s is set, but no release of the Substrate line (components.substrate.versionRange %q, giantswarm/substrate) carries WorkerPool.spec.template.%s yet: the apiserver prunes the value silently (the CRD is a structural schema), so the render refuses it until the release that carries the field is out (giantswarm/agent-platform#472, giantswarm/giantswarm#37797); remove the key" $key $range $key) -}}
{{- else if not $floor -}}
{{- fail (printf "kagent.substrateWorkerPool.template.%s needs the Substrate line at %s or later (the release whose WorkerPool CRD carries spec.template.%s), and components.substrate.versionRange %q has no floor to check that against: pin a range with a >= floor (README \"Agent Substrate\") or remove the key" $key $spreadFloor $key $range) -}}
{{- else if lt ((semver $floor).Compare (semver $spreadFloor)) 0 -}}
{{- fail (printf "kagent.substrateWorkerPool.template.%s needs the Substrate line at %s or later (the release whose WorkerPool CRD carries spec.template.%s), and components.substrate.versionRange %q has the floor %s: below it the apiserver prunes the value silently. Move components.substrate.versionRange and components.substrate-crds.versionRange to that release (with the kagent pin, README \"Agent Substrate\") or remove the key" $key $spreadFloor $key $range $floor) -}}
{{- end -}}
{{- else if not (has $key $known) -}}
{{- fail (printf "kagent.substrateWorkerPool.template.%s is not a WorkerPool.spec.template field of the Substrate line (labels, annotations, nodeSelector, tolerations, priorityClassName, nodeAffinity, resources): the apiserver would prune it silently; remove or rename the key" $key) -}}
{{- end -}}
{{- end -}}
{{- range $key, $value := dig "nodeSelector" (dict) $template -}}
{{- if not (kindIs "string" $value) -}}
{{- fail (printf "kagent.substrateWorkerPool.template.nodeSelector.%s is %v (%s), not a string: a nodeSelector value is a string (WorkerPool.spec.template.nodeSelector is map[string]string), so quote it — the CPU generation pin is karpenter.k8s.aws/instance-generation: \"6\"" $key $value (kindOf $value)) -}}
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
{{- define "agent-platform.validateRemovedComponents" -}}
{{- /* The classic KServe controller (InferenceService, ClusterServingRuntime)
went with the classic serving path: every served model is an
LLMInferenceService on the llm-d control plane (components.kserve-llmisvc-crd,
kserve-llmisvc-resources, kserve-runtime-configs). A roster entry or a values
block for the removed components is refused, not ignored — an entry without a
chart would otherwise pass as a feature switch and silently install nothing
where the operator asked for a controller. */ -}}
{{- $found := list -}}
{{- range $name := list "kserve-crd" "kserve-resources" -}}
{{- if hasKey ($.Values.components | default dict) $name -}}
{{- $found = append $found (printf "components.%s" $name) -}}
{{- end -}}
{{- if hasKey $.Values $name -}}
{{- $found = append $found (printf "%s (the chart's values block)" $name) -}}
{{- end -}}
{{- end -}}
{{- with $found -}}
{{- fail (printf "the classic KServe controller was removed with the classic InferenceService serving path and its keys are refused: %s. The llm-d control plane is components.kserve-llmisvc-crd + components.kserve-llmisvc-resources (which renders the shared inferenceservice-config, Issuer and ClusterStorageContainer itself) + components.kserve-runtime-configs; drop the keys (see UPGRADE.md)" (join ", " .)) -}}
{{- end -}}
{{- end -}}

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
kagent release dependsOn, the kserve-llmisvc-crd shape). kagent on with kagent-crds off
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
slack.secretName / obo.existingSecret, model-manager, agent-manager and vm-manager
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
      (list "agent-manager" (list "oauth" "dex" "clientSecret"))
      (list "vm-manager" (list "oauth" "dex" "clientSecret"))
      (list "cluster-manager" (list "oauth" "dex" "clientSecret")) -}}
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
{{- fail (printf "gitops.forbidInlineSecrets is true but these values carry credentials inline, which would land in clear text in the component HelmReleases and in Helm release storage: %s. Move each into a pre-created Secret and reference it (kagent providers.<name>.apiKeySecretRef with an empty apiKey, kagent.oauth2-proxy.config.existingSecret, muster.muster.oauth.server.existingSecret and .storage.valkey.existingSecret, valkey.valkey.auth.usersExistingSecret, klausGateway.slack.secretName with an empty botToken, klausGateway.obo.existingSecret, model-manager/agent-manager/vm-manager oauth.existingSecret), or set gitops.forbidInlineSecrets: false" .) -}}
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
{{/*
The platform's Valkey as a client in the release namespace reaches it:
<valkey.valkey.fullnameOverride>:<service port>, the bare Service name muster's
own storage.valkey.url uses. Usage: include "agent-platform.valkeyAddress" .
*/}}
{{- define "agent-platform.valkeyAddress" -}}
{{- $v := .Values.valkey | default dict -}}
{{- printf "%s:%v" (dig "valkey" "fullnameOverride" "muster-valkey" $v) (dig "valkey" "service" "port" 6379 $v) -}}
{{- end -}}

{{/*
The Secret the valkey release authenticates its default user from
(valkey.valkey.auth.usersExistingSecret), else the one muster reads the same
password from, else the platform Secret (global.identity.existingSecret).
Empty when none is named. Usage: include "agent-platform.valkeySecretName" .
*/}}
{{- define "agent-platform.valkeySecretName" -}}
{{- $v := .Values.valkey | default dict -}}
{{- $m := .Values.muster | default dict -}}
{{- coalesce (dig "valkey" "auth" "usersExistingSecret" "" $v) (dig "muster" "oauth" "server" "storage" "valkey" "existingSecret" "" $m) (dig "muster" "oauth" "server" "existingSecret" "" $m) (dig "identity" "existingSecret" "" (.Values.global | default dict)) "" -}}
{{- end -}}

{{/*
The key of the default user's password in that Secret
(valkey.valkey.auth.aclUsers.default.passwordKey, else muster's
storage.valkey.secretKeyPassword, else valkey-password).
Usage: include "agent-platform.valkeyPasswordKey" .
*/}}
{{- define "agent-platform.valkeyPasswordKey" -}}
{{- $v := .Values.valkey | default dict -}}
{{- $m := .Values.muster | default dict -}}
{{- coalesce (dig "valkey" "auth" "aclUsers" "default" "passwordKey" "" $v) (dig "muster" "oauth" "server" "storage" "valkey" "secretKeyPassword" "" $m) "valkey-password" -}}
{{- end -}}

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
while the muster component is on, "" otherwise. ONE helper, one consumer, one
name in both charts: this copy derives agent-manager's chart value muster.url
(componentDerivedValues, next to flux.helmReleaseServiceAccount); agent-manager
hands it to the Generic agent chart 1.x as muster.url on every agent it
composes and reports it in get_info. The portal sends none (create_agent takes
no muster argument), so the connectivity chart's app-config carries no muster
URL. The agent chart's own default is the same URL on a default install.
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
kagent.controller.vpa.enabled resolved: "true" when the connectivity chart
renders the kagent controller's VerticalPodAutoscaler. `auto` follows whether
autoscaling.k8s.io/v1 is served — the VPA CRD is not part of Kubernetes
conformance; an explicit true / false wins. The key is the connectivity
chart's (components.kagent.omitKeys holds it back from the kagent release).
*/}}
{{- define "agent-platform.shape.kagentControllerVpa" -}}
{{- $vpa := dig "controller" "vpa" nil (.Values.kagent | default dict) -}}
{{- /* The block itself is the switch: an installation that deletes
kagent.controller.vpa — or the whole kagent.controller block — has no knob to
resolve and gets no object, the same answer as enabled: false. A null set at
either layer deletes the key rather than passing it on, so this is the shape
the chart sees, and reading .enabled off it would dereference nothing. */ -}}
{{- if not (kindIs "map" $vpa) -}}
false
{{- else -}}
{{- include "agent-platform.shape.resolve" (dict "root" . "key" "kagent.controller.vpa.enabled" "value" (dig "enabled" "auto" $vpa) "api" "autoscaling.k8s.io/v1") -}}
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
                               OTLP gateway they export to is part of that platform),
                               agentgateway.monitoring.enabled (the packaging
                               chart's own gate over its controller ServiceMonitor,
                               proxy PodMonitor and dashboard ConfigMap),
                               mcp-kubernetes.mcpKubernetes.instrumentation
                               .serviceMonitor.enabled and
                               mcp-kubernetes.grafanaDashboards.enabled (its
                               monitor and the three boards it ships),
                               klausGateway.serviceMonitor.enabled (the chart's
                               own monitor; the chart takes a boolean),
                               vm-manager.serviceMonitor.enabled,
                               kserve-llmisvc-resources.kserve.llmisvc
                               .controller.serviceMonitor.enabled,
                               substrate.metrics.podMonitor.enabled (the six
                               workloads of the Substrate control plane),
                               kagent.controller.metrics.serviceMonitor.enabled
                               (the controller's own, from the line's 1.0.2)
Two leaves have no `auto` form and are derived directly, off only:
  valkey.valkey.metrics.podMonitor.enabled — the valkey chart's own default is
      on; written false when monitors are off, left absent otherwise so the
      fleet's HelmRelease values are unchanged. An explicit value is kept.
  kagent.controller.env[name=OTEL_EXPORTER_OTLP_HEADERS] — the tenant header
      of the OTLP gateway; dropped when both kagent OTel exporters resolve off.
  kagent.harness.env — the actors' copies: OTEL_EXPORTER_OTLP_HEADERS dropped
      the same way, OTEL_LOGGING_ENABLED (the actors' log exporter) dropped
      when kagent.otel.logging resolves off (agent-platform.shape.dropEnv).
  klausGateway.observability.otlpEndpoint / .otlpHeaders — emptied when
      klausGateway.observability.enabled (auto | true | false; auto follows the
      monitors) resolves off, so the klaus-gateway release exports nothing; the
      knob itself is dropped from that release by components.klaus-gateway
      .omitKeys (the chart's observability block is closed).
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
{{- $controllerVpa := eq (include "agent-platform.shape.kagentControllerVpa" $root) "true" -}}
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
{{- if kindIs "map" (dig "controller" "vpa" nil (index $v "kagent" | default dict)) -}}
{{- $_ := set (index $v "kagent" "controller" "vpa") "enabled" $controllerVpa -}}
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
{{- include "agent-platform.shape.derive" (dict "values" $v "path" (list "agentgateway" "monitoring" "enabled") "value" $monitors) -}}
{{- include "agent-platform.shape.derive" (dict "values" $v "path" (list "mcp-kubernetes" "mcpKubernetes" "instrumentation" "serviceMonitor" "enabled") "value" $monitors) -}}
{{- include "agent-platform.shape.derive" (dict "values" $v "path" (list "mcp-kubernetes" "grafanaDashboards" "enabled") "value" $monitors) -}}
{{- include "agent-platform.shape.derive" (dict "values" $v "path" (list "substrate" "metrics" "podMonitor" "enabled") "value" $monitors) -}}
{{- include "agent-platform.shape.derive" (dict "values" $v "path" (list "kagent" "controller" "metrics" "serviceMonitor" "enabled") "value" $monitors) -}}
{{- include "agent-platform.shape.derive" (dict "values" $v "path" (list "kagent" "otel" "tracing" "enabled") "value" $monitors) -}}
{{- include "agent-platform.shape.derive" (dict "values" $v "path" (list "kagent" "otel" "logging" "enabled") "value" $monitors) -}}
{{- include "agent-platform.shape.derive" (dict "values" $v "path" (list "vm-manager" "serviceMonitor" "enabled") "value" $monitors) -}}
{{- include "agent-platform.shape.derive" (dict "values" $v "path" (list "kserve-llmisvc-resources" "kserve" "llmisvc" "controller" "serviceMonitor" "enabled") "value" $monitors) -}}
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
{{- /* kagent OTLP env: the tenant header on the controller and on the Harness
(the actors) is gone when neither OTel exporter is on; the actors' log exporter
(OTEL_LOGGING_ENABLED on the Harness) when the logging exporter is off. */ -}}
{{- $kagent := index $v "kagent" | default dict -}}
{{- if kindIs "map" $kagent -}}
{{- $tracing := eq (toString (dig "otel" "tracing" "enabled" false $kagent)) "true" -}}
{{- $logging := eq (toString (dig "otel" "logging" "enabled" false $kagent)) "true" -}}
{{- $drop := list -}}
{{- if and (not $tracing) (not $logging) -}}
{{- $drop = append $drop "OTEL_EXPORTER_OTLP_HEADERS" -}}
{{- end -}}
{{- include "agent-platform.shape.dropEnv" (dict "owner" (index $kagent "controller") "names" $drop) -}}
{{- if not $logging -}}
{{- $drop = append $drop "OTEL_LOGGING_ENABLED" -}}
{{- end -}}
{{- include "agent-platform.shape.dropEnv" (dict "owner" (index $kagent "harness") "names" $drop) -}}
{{- end -}}
{{- /* klaus-gateway's trace export follows the same answer: the knob resolved
from auto, and the endpoint and headers the klaus-gateway chart reads emptied
when it is off (giantswarm/klaus-gateway#263). */ -}}
{{- include "agent-platform.shape.derive" (dict "values" $v "path" (list "klausGateway" "observability" "enabled") "value" $monitors) -}}
{{- include "agent-platform.shape.derive" (dict "values" $v "path" (list "klausGateway" "serviceMonitor" "enabled") "value" $monitors) -}}
{{- $kgObs := dig "observability" nil (index $v "klausGateway" | default dict) -}}
{{- if and (kindIs "map" $kgObs) (hasKey $kgObs "enabled") (ne (toString (index $kgObs "enabled")) "true") -}}
{{- $_ := set $kgObs "otlpEndpoint" "" -}}
{{- $_ := set $kgObs "otlpHeaders" dict -}}
{{- end -}}
{{- end -}}

{{/*
Drop the entries of .owner.env (a list of name/value maps — kagent.controller.env,
kagent.harness.env) whose name is in .names, in place on the shaped values tree.
A missing owner or env, or no names, changes nothing. Emits nothing.
*/}}
{{- define "agent-platform.shape.dropEnv" -}}
{{- $owner := .owner -}}
{{- if and .names (kindIs "map" $owner) (kindIs "slice" (index $owner "env")) -}}
{{- $env := list -}}
{{- range (index $owner "env") -}}
{{- if not (and (kindIs "map" .) (has (toString (index . "name")) $.names)) -}}
{{- $env = append $env . -}}
{{- end -}}
{{- end -}}
{{- $_ := set $owner "env" $env -}}
{{- end -}}
{{- end -}}

{{/*
Placement of the stateful singletons (giantswarm/agent-platform#439): merge
scheduling.singletons.nodeSelector into, and append scheduling.singletons.tolerations
to, the scheduling knobs of the four single-replica stateful components on the
shaped values tree — muster (the muster chart's nodeSelector / tolerations),
muster-valkey (valkey.valkey.*, the upstream subchart's), the kagent controller
(kagent.controller.*) and klaus-gateway (klausGateway.*). A key a component's
own nodeSelector already holds wins (sprig merge: the destination's keys stay);
the component's own tolerations come first. Empty knobs write nothing, so the
default render is byte-identical to a chart without the block. Runs on the
$shaped copy in components.yaml after agent-platform.shape.apply, so every
component release — and the connectivity release, which sees the components'
blocks — reads the merged copies; scheduling itself is held back from the
connectivity release (components.agent-platform-connectivity.omitKeys).
Usage: include "agent-platform.scheduling.apply" (dict "values" $shaped)
*/}}
{{- define "agent-platform.scheduling.apply" -}}
{{- $v := .values -}}
{{- $singletons := dig "singletons" dict (index $v "scheduling" | default dict) -}}
{{- $selector := index $singletons "nodeSelector" | default dict -}}
{{- $tolerations := index $singletons "tolerations" | default list -}}
{{- if or $selector $tolerations -}}
{{- range $path := list (list "muster") (list "valkey" "valkey") (list "kagent" "controller") (list "klausGateway") -}}
{{- $node := $v -}}
{{- range $key := $path -}}
{{- if not (kindIs "map" (index $node $key)) }}{{ $_ := set $node $key dict }}{{ end -}}
{{- $node = index $node $key -}}
{{- end -}}
{{- with $selector }}{{ $_ := set $node "nodeSelector" (merge (deepCopy (index $node "nodeSelector" | default dict)) .) }}{{ end -}}
{{- with $tolerations }}{{ $_ := set $node "tolerations" (concat (index $node "tolerations" | default list) .) }}{{ end -}}
{{- end -}}
{{- end -}}
{{- end -}}

{{/*
The platform's one login in the managers' OAuth blocks
(giantswarm/agent-platform#484). model-manager, agent-manager, vm-manager and
cluster-manager are OAuth resource servers of the login muster's OAuth server
names. Each chart resolves its issuer, client, Secret and trusted audiences
from its own oauth block, else global.identity; what both leave unset is filled
here from muster.muster.oauth.server — oauth.dex.issuerURL from dex.issuerUrl,
oauth.dex.clientID from dex.clientId (and oauth.trustedAudiences [that client],
the charts' own default from global.identity.clientId), oauth.existingSecret
from existingSecret (key dex-client-secret, the key every manager chart and
muster read) — so an installation that names muster's login names the
managers' too, a customer's own Dex client included, which a fleet-wide
global.identity.clientId cannot carry. Defaults, not the single-source rule: a
manager's own value wins, and so does global.identity (the connectivity chart
fails the render when either disagrees with muster's). Only for the dex
provider, while muster's OAuth server is on (with it off, model-manager's oauth
is off: componentDerivedValues).
oauth.baseURL of the two routed managers is https://<host><route.pathPrefix>
under an agentgateway-* ingress.mode, host being the hostname the connectivity
chart gives the route (modelManager.route.hostname / agentManager.route.hostname,
else agentgateway.<global.domain>); without one it stays unset and the
connectivity chart's guard names the key.
Runs on the $shaped copy in components.yaml after scheduling.apply, so each
manager release and the connectivity release — its guards, the managers'
identity-provider egress — read the same filled block. Emits nothing.
Usage: include "agent-platform.identity.apply" (dict "values" $shaped)
*/}}
{{- define "agent-platform.identity.apply" -}}
{{- $v := .values -}}
{{- $global := index $v "global" | default dict -}}
{{- $identity := dig "identity" dict $global -}}
{{- $domain := dig "domain" "" $global -}}
{{- $mode := dig "ingress" "mode" "" $v -}}
{{- $server := dig "muster" "oauth" "server" dict (index $v "muster" | default dict) -}}
{{- $login := dict -}}
{{- if and (dig "enabled" true $server) (eq (toString (dig "provider" "dex" $server)) "dex") -}}
{{- $login = dict "issuerURL" (dig "dex" "issuerUrl" "" $server) "clientID" (dig "dex" "clientId" "" $server) "existingSecret" (dig "existingSecret" "" $server) -}}
{{- end -}}
{{- range $name, $wiring := dict "model-manager" "modelManager" "agent-manager" "agentManager" "vm-manager" "" "cluster-manager" "" -}}
{{- $block := index $v $name -}}
{{- if and (kindIs "map" $block) (kindIs "map" (index $block "oauth")) (dig "oauth" "enabled" false $block) -}}
{{- $oauth := index $block "oauth" -}}
{{- if and $login (eq (toString (dig "provider" "dex" $oauth)) "dex") -}}
{{- if not (kindIs "map" (index $oauth "dex")) }}{{ $_ := set $oauth "dex" dict }}{{ end -}}
{{- $dex := index $oauth "dex" -}}
{{- if and $login.issuerURL (not $dex.issuerURL) (not $identity.issuerUrl) }}{{ $_ := set $dex "issuerURL" $login.issuerURL }}{{ end -}}
{{- if and $login.clientID (not $dex.clientID) (not $identity.clientId) -}}
{{- $_ := set $dex "clientID" $login.clientID -}}
{{- if not $oauth.trustedAudiences }}{{ $_ := set $oauth "trustedAudiences" (list $login.clientID) }}{{ end -}}
{{- end -}}
{{- if and $login.existingSecret (not $oauth.existingSecret) (not $dex.clientSecret) (not $identity.existingSecret) }}{{ $_ := set $oauth "existingSecret" $login.existingSecret }}{{ end -}}
{{- end -}}
{{- if and $wiring (not $oauth.baseURL) (or (eq $mode "agentgateway-muster") (eq $mode "agentgateway-direct")) -}}
{{- $route := dig $wiring "route" dict $v -}}
{{- $host := $route.hostname -}}
{{- if and (not $host) $domain }}{{ $host = printf "agentgateway.%s" $domain }}{{ end -}}
{{- if and $host $route.pathPrefix }}{{ $_ := set $oauth "baseURL" (printf "https://%s%s" $host $route.pathPrefix) }}{{ end -}}
{{- end -}}
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
The namespace the kagent component's objects live in: kagent.namespaceOverride,
else the namespace the platform HelmReleases target (gitops.targetNamespace,
else the release namespace). The storage-version hooks keep their record there
(hooks/kagent-crds-storage-version.yaml).
Usage: include "agent-platform.kagent.namespace" .
*/}}
{{- define "agent-platform.kagent.namespace" -}}
{{- dig "namespaceOverride" "" (.Values.kagent | default dict) | default (.Values.gitops.targetNamespace | default .Release.Namespace) -}}
{{- end -}}

{{/*
Whether the kagent CRDs' storage-version hooks render
(hooks/kagent-crds-storage-version.yaml, giantswarm/agent-platform#396): whenever
the kagent line's CRD component is on — with or without the bundled engine. A
cluster's own Flux runs this chart's hooks too, and every installation that ran
kagent 0.10 needs the step; the other hooks stay the engine's. Never with
gitops.target set: a hook Job runs where the chart is installed, and there the
kagent CRDs are another release's (the platform's own) — the target cluster
starts on the kagent API v2 line and has no cut-over to run. Emits "true" or "".
*/}}
{{- define "agent-platform.kagent.storageVersionHooks" -}}
{{- if and (include "agent-platform.componentEnabled" (dict "root" . "name" "kagent-crds")) (not (include "agent-platform.targetSecretName" .)) }}true{{ end -}}
{{- end -}}

{{/*
The Helm hook events the hook ServiceAccount + ClusterRoleBinding (hooks/rbac.yaml)
are created for, in Helm's order: pre-install,pre-upgrade while the kagent
namespace hook or the storage-version backup hook renders (they run as that
account — creating a namespace or deleting a CRD is cluster-scoped, the
namespaced <release>-self identity cannot), post-install,post-upgrade while the
storage-version restore hook renders, pre-delete for the ordered teardown
(the bundled engine), and the serving teardown's event while it renders
(agent-platform.serving.teardownEvent: pre-delete, or pre-upgrade when the
slice is switched off in place). Empty when none of them renders — rbac.yaml
renders nothing then.
*/}}
{{- define "agent-platform.hooks.serviceAccountEvents" -}}
{{- $events := list -}}
{{- if or (include "agent-platform.kagent.hookNamespace" .) (include "agent-platform.kagent.storageVersionHooks" .) }}{{ $events = concat $events (list "pre-install" "pre-upgrade") }}{{ end -}}
{{- if include "agent-platform.kagent.storageVersionHooks" . }}{{ $events = concat $events (list "post-install" "post-upgrade") }}{{ end -}}
{{- if eq (include "agent-platform.engineEnabled" .) "true" }}{{ $events = append $events "pre-delete" }}{{ end -}}
{{- with include "agent-platform.serving.teardownEvent" . }}{{ $events = append $events . }}{{ end -}}
{{- join "," (uniq $events) -}}
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
The target cluster's kubeconfig Secret (gitops.target.kubeConfig.secretRef.name,
giantswarm/agent-platform#328): its name when this release installs its
components into another cluster, "" otherwise. One release of this chart per
target cluster — the slices are toggles in its values, never two releases of the
chart on one cluster (both need the same cluster-scoped CRDs).
*/}}
{{- define "agent-platform.targetSecretName" -}}
{{- dig "target" "kubeConfig" "secretRef" "name" "" (.Values.gitops | default dict) -}}
{{- end -}}

{{/*
Render guard of the target knob: the components install into the target through
the installation's helm-controller, so the bundled engine has no place in such a
release — no Flux is ever installed into a workload cluster, nor into a cluster
that runs one.
*/}}
{{- define "agent-platform.validateTarget" -}}
{{- with (include "agent-platform.targetSecretName" .) -}}
{{- if eq (include "agent-platform.engineEnabled" $) "true" -}}
{{- fail (printf "gitops.target.kubeConfig.secretRef.name=%s cannot be combined with the bundled Flux engine: the components install into the target cluster through the installation's helm-controller, and no Flux is installed into a workload cluster or into a cluster that runs one. Set components.flux.enabled=false" .) -}}
{{- end -}}
{{- end -}}
{{- end -}}

{{/*
One owner per cluster-scoped component (components.<name>.ownedCrds): a CRD the
component installs that already exists and whose Flux labels name another
HelmRelease than the one this render produces (<gitops.namespace | release
namespace>/<chart>) fails the render naming that release. helm-controller stamps
helm.toolkit.fluxcd.io/name and /namespace on every object of a release, the
crds/ directory's CRDs included, so the labels are the owner. A CRD without them
is left to Helm as before (adoption is not this chart's). Skipped with
gitops.target set: the lookups see the installation while the components land
on the target, so there the detection is the composer's. `lookup` is empty
under `helm template`, where this guard is therefore silent.
*/}}
{{- define "agent-platform.validateCrdOwners" -}}
{{- if not (include "agent-platform.targetSecretName" .) -}}
{{- $ns := .Values.gitops.namespace | default .Release.Namespace -}}
{{- $foreign := list -}}
{{- range $key, $c := .Values.components -}}
{{- if and (include "agent-platform.componentEnabled" (dict "root" $ "name" $key)) (hasKey $c "chart") -}}
{{- range ($c.ownedCrds | default list) -}}
{{- with (lookup "apiextensions.k8s.io/v1" "CustomResourceDefinition" "" .) -}}
{{- $owner := dig "metadata" "labels" "helm.toolkit.fluxcd.io/name" "" . -}}
{{- $ownerNs := dig "metadata" "labels" "helm.toolkit.fluxcd.io/namespace" "" . -}}
{{- if and $owner (or (ne $owner $c.chart) (ne $ownerNs $ns)) -}}
{{- $foreign = append $foreign (printf "%s (components.%s) belongs to HelmRelease %s/%s" .metadata.name $key $ownerNs $owner) -}}
{{- end -}}
{{- end -}}
{{- end -}}
{{- end -}}
{{- end -}}
{{- with $foreign -}}
{{- fail (printf "a cluster-scoped component has exactly one owner per cluster, and release %s (HelmReleases in %s) would be a second one: %s. Remove that release first, or set components.<name>.enabled=false here — a slice release beside the platform's own leaves the controller and its CRDs to the platform's release" $.Release.Name $ns (join "; " .)) -}}
{{- end -}}
{{- end -}}
{{- end -}}

{{/*
One GPU operator per cluster (components.gpu-operator, giantswarm/agent-platform#327).
NVIDIA's operator owns one ClusterPolicy and one set of DaemonSets, so with the
component on the render fails when the cluster it renders against already runs an
operator that is not this release's: a ClusterPolicy whose owner — the Flux labels
helm.toolkit.fluxcd.io/name + /namespace, else Helm's meta.helm.sh/release-name +
/release-namespace annotations — is another release or none; a HelmRelease of the
gpu-operator chart under another name or in another namespace (cluster-manager's
<cluster>-gpu-operator, before or after its ClusterPolicy exists); an App of it.
The message names what was seen and the handover: delete that release
(cluster-manager's detection then sees this component and never re-creates it),
then switch the toggle on — or leave it off. Adopting the running objects is not
this chart's. Skipped with the target knob, as agent-platform.validateCrdOwners
is: the lookups see the installation while the component lands on the target,
where the detection is cluster-manager's. Each lookup is gated on its API being
served, and `lookup` is empty under `helm template`, where the guard is silent —
tests/fixtures/gpu-operator-foreign-owner.yaml on a cluster and --dry-run=server
show it (README, "The GPU operator").
*/}}
{{- define "agent-platform.validateGpuOperatorOwner" -}}
{{- $c := index .Values.components "gpu-operator" | default dict -}}
{{- if and $c (eq (include "agent-platform.componentEnabled" (dict "root" . "name" "gpu-operator")) "true") (not (include "agent-platform.targetSecretName" .)) -}}
{{- $ns := .Values.gitops.namespace | default .Release.Namespace -}}
{{- $release := $c.chart -}}
{{- $releaseNs := $c.targetNamespace | default .Values.gitops.targetNamespace | default .Release.Namespace -}}
{{- $foreign := list -}}
{{- if .Capabilities.APIVersions.Has "nvidia.com/v1" -}}
{{- range ((lookup "nvidia.com/v1" "ClusterPolicy" "" "").items | default list) -}}
{{- $fluxName := dig "metadata" "labels" "helm.toolkit.fluxcd.io/name" "" . -}}
{{- $fluxNs := dig "metadata" "labels" "helm.toolkit.fluxcd.io/namespace" "" . -}}
{{- $helmName := dig "metadata" "annotations" "meta.helm.sh/release-name" "" . -}}
{{- $helmNs := dig "metadata" "annotations" "meta.helm.sh/release-namespace" "" . -}}
{{- if $fluxName -}}
{{- if or (ne $fluxName $release) (ne $fluxNs $ns) -}}
{{- $foreign = append $foreign (printf "ClusterPolicy %s belongs to HelmRelease %s/%s (labels helm.toolkit.fluxcd.io/name=%s, helm.toolkit.fluxcd.io/namespace=%s)" .metadata.name $fluxNs $fluxName $fluxName $fluxNs) -}}
{{- end -}}
{{- else if $helmName -}}
{{- if or (ne $helmName $release) (ne $helmNs $releaseNs) -}}
{{- $foreign = append $foreign (printf "ClusterPolicy %s belongs to the Helm release %s in %s (annotations meta.helm.sh/release-name=%s, meta.helm.sh/release-namespace=%s — installed by hand or as an App)" .metadata.name $helmName $helmNs $helmName $helmNs) -}}
{{- end -}}
{{- else -}}
{{- $foreign = append $foreign (printf "ClusterPolicy %s carries no owner (no helm.toolkit.fluxcd.io/name label, no meta.helm.sh/release-name annotation)" .metadata.name) -}}
{{- end -}}
{{- end -}}
{{- end -}}
{{- if .Capabilities.APIVersions.Has "helm.toolkit.fluxcd.io/v2" -}}
{{- range ((lookup "helm.toolkit.fluxcd.io/v2" "HelmRelease" "" "").items | default list) -}}
{{- $chart := dig "spec" "chart" "spec" "chart" "" . -}}
{{- $chartRef := dig "spec" "chartRef" "name" "" . -}}
{{- if and (or (eq $chart $release) (eq $chartRef $release) (hasSuffix "-gpu-operator" $chartRef) (hasSuffix "-gpu-operator" .metadata.name)) (not (and (eq .metadata.name $release) (eq .metadata.namespace $ns))) -}}
{{- $foreign = append $foreign (printf "HelmRelease %s/%s installs the operator" .metadata.namespace .metadata.name) -}}
{{- end -}}
{{- end -}}
{{- end -}}
{{- if .Capabilities.APIVersions.Has "application.giantswarm.io/v1alpha1" -}}
{{- range ((lookup "application.giantswarm.io/v1alpha1" "App" "" "").items | default list) -}}
{{- if eq (dig "spec" "name" "" .) $release -}}
{{- $foreign = append $foreign (printf "App %s/%s installs the operator" .metadata.namespace .metadata.name) -}}
{{- end -}}
{{- end -}}
{{- end -}}
{{- with $foreign -}}
{{- fail (printf "components.gpu-operator.enabled=true, but this cluster already runs a GPU operator, and NVIDIA's operator is one per cluster (one ClusterPolicy, one set of DaemonSets): %s. This release would be a second owner (HelmRelease %s/%s, the Helm release %s in %s). Hand the operator over first: delete that release — cluster-manager's <cluster>-gpu-operator, whose detection then sees this component and never re-creates it, or the operator installed by hand — then switch the toggle on; or leave components.gpu-operator.enabled=false and keep the operator where it is. Adopting the running objects is not this chart's. The component configures the operator from the gpu-operator block, one of two rows: Flatcar — driver and toolkit off (the default); nodes with a pre-installed driver — gpu-operator.toolkit.enabled=true" (join "; " .) $ns $release $release $releaseNs) -}}
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
{{- printf ">=%s <%d.0.0" (include "agent-platform.chartVersion" .) (add1 (semver .Chart.Version).Major) -}}
{{- end -}}
{{- end -}}

{{/*
This chart's own version as its releases are published: <major>.<minor>.<patch>
with a pre-release kept (a dev build is X.Y.Z-r<branch-hash>t<time>h<sha>, one
version for the two charts of a commit) and build metadata dropped
(helm-controller renders the chart as <version>+<oci digest>). The floor of
the self range above, and the exact version of every component released off
the same tag as this chart (components.<name>.releasedWithChart).
*/}}
{{- define "agent-platform.chartVersion" -}}
{{- $v := semver .Chart.Version -}}
{{- $out := printf "%d.%d.%d" $v.Major $v.Minor $v.Patch -}}
{{- with $v.Prerelease }}{{ $out = printf "%s-%s" $out . }}{{ end -}}
{{- $out -}}
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
