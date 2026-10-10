{{/* vim: set filetype=mustache: */}}
{{/*
Helpers of the workspace-manager component's wiring (templates/workspace-manager/).

Two values blocks feed these templates: workspaceManager (the umbrella wiring —
network policy inputs) and workspace-manager (the component chart's own
values: OAuth, the muster registration, the provider instances, Service name
and port; hyphenated, so reached through index), read the way the
cluster-manager templates read theirs.
*/}}

{{/*
Truthy when the workspace-manager component is on
(components.workspace-manager.enabled). The meta chart forwards the entry only
while workspaces are on, so a missing entry is off here, unlike the roster's
other entries.
*/}}
{{- define "agent-platform.workspaceManager.enabled" -}}
{{- if dig "workspace-manager" "enabled" false (.Values.components | default dict) }}true{{ end -}}
{{- end -}}

{{/*
The component chart's values block, workspace-manager (a dict; empty when unset).
*/}}
{{- define "agent-platform.workspaceManager.chartValues" -}}
{{- index .Values "workspace-manager" | default dict | toJson -}}
{{- end -}}

{{/*
The workspace-manager Service name: workspace-manager.fullnameOverride, which
the component chart uses verbatim for its Service and the network policies
target.
*/}}
{{- define "agent-platform.workspaceManager.fullname" -}}
{{- $chart := include "agent-platform.workspaceManager.chartValues" . | fromJson -}}
{{- required "workspace-manager.fullnameOverride must be set — the umbrella's network policies target this exact Service name" (dig "fullnameOverride" "" $chart) -}}
{{- end -}}

{{/*
The port the workspace-manager Service listens on (workspace-manager.service.port, default 8080).
*/}}
{{- define "agent-platform.workspaceManager.servicePort" -}}
{{- $chart := include "agent-platform.workspaceManager.chartValues" . | fromJson -}}
{{- dig "service" "port" 8080 $chart -}}
{{- end -}}

{{/*
Truthy when the component validates the caller's identity itself
(workspace-manager.oauth.enabled).
*/}}
{{- define "agent-platform.workspaceManager.oauthEnabled" -}}
{{- $chart := include "agent-platform.workspaceManager.chartValues" . | fromJson -}}
{{- if dig "oauth" "enabled" false $chart }}true{{ end -}}
{{- end -}}

{{/*
The identity provider the component validates tokens with: dex (the default) or google.
*/}}
{{- define "agent-platform.workspaceManager.oauthProvider" -}}
{{- $chart := include "agent-platform.workspaceManager.chartValues" . | fromJson -}}
{{- dig "oauth" "provider" "dex" $chart -}}
{{- end -}}

{{/*
The issuer URL the component validates tokens against: the dex provider's
workspace-manager.oauth.dex.issuerURL, else global.identity.issuerUrl.
*/}}
{{- define "agent-platform.workspaceManager.issuerUrl" -}}
{{- $chart := include "agent-platform.workspaceManager.chartValues" . | fromJson -}}
{{- if eq (include "agent-platform.workspaceManager.oauthProvider" .) "dex" -}}
{{- dig "oauth" "dex" "issuerURL" "" $chart | default .Values.global.identity.issuerUrl -}}
{{- end -}}
{{- end -}}

{{/*
The namespace the workspaces and every Session live in: kagent's.
*/}}
{{- define "agent-platform.workspaceManager.kagentNamespace" -}}
{{- .Values.kagent.namespaceOverride | default .Release.Namespace -}}
{{- end -}}

{{/*
Labels of every object the umbrella renders for the component.
*/}}
{{- define "agent-platform.workspaceManager.labels" -}}
{{ include "labels.common" . }}
app.kubernetes.io/component: workspace-manager
{{- end -}}

{{/*
The selector labels of the workspace-manager pods, as the component chart
stamps them (app.kubernetes.io/name from its chart name or nameOverride).
Rendered as YAML mapping entries; the caller provides the indentation.
*/}}
{{- define "agent-platform.workspaceManager.podSelector" -}}
{{- $chart := include "agent-platform.workspaceManager.chartValues" . | fromJson -}}
app.kubernetes.io/name: {{ dig "nameOverride" "" $chart | default "workspace-manager" }}
{{- end -}}

{{/*
The hosts each provider instance (workspace-manager.providers) is reached at:
the host of its values.url and values.apiURL, on the URL's port (443 unless it
names one); a github instance without url is github.com, whose API is
api.github.com. An instance of another kind without either URL fails the
render: its hosts are unknown here. Emits a JSON list of {host, port}, each
pair once, in instance order.
*/}}
{{- define "agent-platform.workspaceManager.providerHosts" -}}
{{- $chart := include "agent-platform.workspaceManager.chartValues" . | fromJson -}}
{{- $out := list -}}
{{- range $i, $p := (dig "providers" list $chart) -}}
{{- $v := $p.values | default dict -}}
{{- $urls := list -}}
{{- range $k := list "url" "apiURL" -}}{{- with (index $v $k) }}{{ $urls = append $urls (toString .) }}{{ end -}}{{- end -}}
{{- if and (not $urls) (eq (toString $p.kind) "github") -}}
{{- $urls = list "https://github.com" "https://api.github.com" -}}
{{- end -}}
{{- if not $urls -}}
{{- fail (printf "workspace-manager.providers[%d] (%s, kind %s) names neither values.url nor values.apiURL: the workspace-manager's egress opens each provider instance's hosts, so name the instance's url" $i (toString $p.name) (toString $p.kind)) -}}
{{- end -}}
{{- range $urls -}}
{{- $u := urlParse . -}}
{{- $hostport := splitList ":" $u.host -}}
{{- $host := first $hostport -}}
{{- if or (not (has $u.scheme (list "https" "http"))) (not $host) -}}
{{- fail (printf "workspace-manager.providers[%d] (%s): %q is not an http(s) URL with a host" $i (toString $p.name) .) -}}
{{- end -}}
{{- $port := ternary (last $hostport) (ternary "443" "80" (eq $u.scheme "https")) (eq (len $hostport) 2) -}}
{{- $entry := dict "host" $host "port" $port -}}
{{- if not (has $entry $out) }}{{ $out = append $out $entry }}{{ end -}}
{{- end -}}
{{- end -}}
{{- $out | toJson -}}
{{- end -}}
