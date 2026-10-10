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

{{/*
The manager's public base URL, without a trailing slash: workspace-manager.oauth.baseURL,
else https://<fullnameOverride>.<global.domain> — the derivation the component
chart makes, and the URL the meta chart forwards to it. Empty when neither is
set.
*/}}
{{- define "agent-platform.workspaceManager.baseURL" -}}
{{- $chart := include "agent-platform.workspaceManager.chartValues" . | fromJson -}}
{{- $url := dig "oauth" "baseURL" "" $chart -}}
{{- if and (not $url) .Values.global.domain -}}
{{- $url = printf "https://%s.%s" (include "agent-platform.workspaceManager.fullname" .) .Values.global.domain -}}
{{- end -}}
{{- $url | trimSuffix "/" -}}
{{- end -}}

{{/*
Truthy when the public sign-in route renders: the component on and
workspaceManager.route.enabled.
*/}}
{{- define "agent-platform.workspaceManager.routeEnabled" -}}
{{- if and (include "agent-platform.workspaceManager.enabled" .) (dig "route" "enabled" true .Values.workspaceManager) }}true{{ end -}}
{{- end -}}

{{/*
The route's pinned parents, a JSON list: workspaceManager.route.parentRefs,
else muster's (ingress.parentRefs), the public Gateway the platform's login
already runs through; empty leaves the choice to agent-platform.parentRefs
(the chart-owned edge, else global.gatewayApi.parentRefs).
*/}}
{{- define "agent-platform.workspaceManager.routeParentRefs" -}}
{{- dig "route" "parentRefs" list .Values.workspaceManager | default .Values.ingress.parentRefs | default list | toJson -}}
{{- end -}}

{{/*
Truthy when this chart creates the sign-in keys Secret: the component on with
provider instances, oauth.enabled and workspaceManager.signinKeys.create — the
case in which the manager requires signin.keys.
*/}}
{{- define "agent-platform.workspaceManager.signinKeysEnabled" -}}
{{- $chart := include "agent-platform.workspaceManager.chartValues" . | fromJson -}}
{{- if and (include "agent-platform.workspaceManager.enabled" .) (dig "providers" list $chart) (include "agent-platform.workspaceManager.oauthEnabled" .) (dig "signinKeys" "create" true .Values.workspaceManager) }}true{{ end -}}
{{- end -}}

{{/*
The callback guard: a provider instance whose OAuth client is set
(values.oauth.clientID) needs a callback a browser reaches —
<baseURL>/callback/<instance> on a public host this chart routes
(workspaceManager.route.enabled), or on a base URL the installation set and
routes itself (workspace-manager.oauth.baseURL with the route off). The base
URL is https (http only on a loopback host) and its host is not a cluster
name. Emits nothing; fails naming the instance and the fix.
*/}}
{{- define "agent-platform.workspaceManager.validateCallback" -}}
{{- $chart := include "agent-platform.workspaceManager.chartValues" . | fromJson -}}
{{- $own := dig "oauth" "baseURL" "" $chart -}}
{{- $base := include "agent-platform.workspaceManager.baseURL" . -}}
{{- $route := include "agent-platform.workspaceManager.routeEnabled" . -}}
{{- range $i, $p := (dig "providers" list $chart) -}}
{{- $oauth := (($p.values | default dict).oauth | default dict) -}}
{{- if and (kindIs "map" $oauth) ($oauth.clientID | default "") -}}
{{- $at := printf "workspace-manager.providers[%d] (%s) sets an OAuth client (values.oauth.clientID)" $i (toString $p.name) -}}
{{- if not $base -}}
{{- fail (printf "%s but its callback has no host: workspace-manager.oauth.baseURL is empty and global.domain is not set. Set global.domain (the callback is then https://%s.<global.domain>/callback/%s) or workspace-manager.oauth.baseURL, and register that URL with the provider's OAuth client" $at (include "agent-platform.workspaceManager.fullname" $) (toString $p.name)) -}}
{{- end -}}
{{- if and (not $route) (not $own) -}}
{{- fail (printf "%s but workspaceManager.route.enabled is false and workspace-manager.oauth.baseURL is empty: nothing routes %s/callback/%s. Turn the route on, or set workspace-manager.oauth.baseURL to the URL the installation's own route serves" $at $base (toString $p.name)) -}}
{{- end -}}
{{- $u := urlParse $base -}}
{{- $host := include "agent-platform.urlHost" $base -}}
{{- $loopback := or (eq $host "localhost") (hasSuffix ".localhost" $host) (eq $host "127.0.0.1") (eq $host "::1") -}}
{{- if or (regexMatch "(^|\\.)(svc|cluster\\.local)$" $host) (not $host) (not (or (eq $u.scheme "https") (and (eq $u.scheme "http") $loopback))) -}}
{{- fail (printf "%s but the base URL %q is not reachable from the person's browser: the provider redirects the browser to <baseURL>/callback/%s, so the base URL is https on a public host (http only on a loopback host), never a cluster-internal name. Set workspace-manager.oauth.baseURL to the public URL, or leave it empty to derive https://%s.<global.domain>" $at $base (toString $p.name) (include "agent-platform.workspaceManager.fullname" $)) -}}
{{- end -}}
{{- end -}}
{{- end -}}
{{- end -}}

{{/*
The sign-in keys guard: with providers and oauth.enabled the manager requires
signin.keys (a Secret name and the id of the key that seals, a lower-case DNS
label of at most 32 characters). Emits nothing; fails naming the key.
*/}}
{{- define "agent-platform.workspaceManager.validateSigninKeys" -}}
{{- $chart := include "agent-platform.workspaceManager.chartValues" . | fromJson -}}
{{- if and (dig "providers" list $chart) (include "agent-platform.workspaceManager.oauthEnabled" .) -}}
{{- $keys := dig "signin" "keys" dict $chart -}}
{{- if not ($keys.secretName | default "") -}}
{{- fail "workspace-manager.signin.keys.secretName is empty: with providers and oauth.enabled the workspace-manager seals each person's provider sign-ins with the keys of that Secret; name it (workspaceManager.signinKeys.create renders it)" -}}
{{- end -}}
{{- if not (regexMatch "^[a-z0-9]([-a-z0-9]{0,30}[a-z0-9])?$" (toString ($keys.current | default ""))) -}}
{{- fail (printf "workspace-manager.signin.keys.current %q is not a key id: a lower-case DNS label of at most 32 characters, the data key of the sealing key in Secret %s" (toString ($keys.current | default "")) $keys.secretName) -}}
{{- end -}}
{{- end -}}
{{- end -}}

{{/*
The host the public sign-in route serves: the base URL's, while the route
renders; empty otherwise (route off, or no base URL).
*/}}
{{- define "agent-platform.workspaceManager.routeHost" -}}
{{- if (include "agent-platform.workspaceManager.routeEnabled" .) -}}
{{- include "agent-platform.urlHost" (include "agent-platform.workspaceManager.baseURL" .) -}}
{{- end -}}
{{- end -}}
