{{/* vim: set filetype=mustache: */}}
{{/*
Helpers of the klaus-gateway component's wiring (templates/klausgateway/).

One values block feeds these templates: klausGateway — the klaus-gateway
chart's own values as the meta chart forwards them (the routing store, Slack,
OBO and A2A knobs) plus the umbrella's wiring keys (agentgatewayRoute). A
component release's values cannot be derived at render time, so the wiring
reads what the chart will see; a knob the block does not declare is read with
the klaus-gateway chart's own default. The Valkey store's
target is the platform's own Valkey (components.valkey, the valkey block), the
way the meta chart fills routing.valkey from it.
*/}}

{{/* The namespace of the klaus-gateway component's Helm release. */}}
{{- define "agent-platform.klausGateway.namespace" -}}
{{- dig "klaus-gateway" "targetNamespace" "" (.Values.components | default dict) | default .Release.Namespace -}}
{{- end -}}

{{/*
The klaus-gateway pod's app.kubernetes.io/name label value: the chart's
fullnameOverride, else its chart name. Every policy selecting the gateway pod
uses this, so a renamed release moves them all.
*/}}
{{- define "agent-platform.klausGateway.fullname" -}}
{{- .Values.klausGateway.fullnameOverride | default "klaus-gateway" -}}
{{- end -}}

{{/*
Truthy (emits "true") when the gateway pod reads or writes the Kubernetes API,
so its egress needs the kube-apiserver: the A2A server's organization namespace
lookup, the Secret link store
(klausGateway.obo.store: secret with OBO on — the gateway's own gate,
klaus-gateway's obo.secretStore helper), and the team-review endpoint
(klausGateway.reviews.enabled; giantswarm/klaus-gateway#273), which verifies
every caller of POST /reviews and POST /notices with an
authentication.k8s.io/v1 TokenReview — whatever the link store, so an
installation on the bolt store gets the rule too (giantswarm/agent-platform#511;
without it every review times out at the API and the endpoint answers 503).
The routing stores that reached the API (configmap, crd) and the embedded
ChannelRoute controller are gone from klaus-gateway (giantswarm/klaus-gateway#271).
Empty otherwise: the default shape — memory routing, the bolt link store, no
reviews or A2A server — never touches the API and gets no rule. The keys are the
klaus-gateway chart's; when the block leaves one unset its chart default
applies (bolt, memory, reviews off).
*/}}
{{- define "agent-platform.klausGateway.apiServerEgress" -}}
{{- $kg := .Values.klausGateway -}}
{{- if or (dig "reviews" "enabled" false $kg) (and (dig "obo" "enabled" false $kg) (eq (dig "obo" "store" "bolt" $kg) "secret")) (include "agent-platform.klausGateway.refusalsOn" .) (include "agent-platform.klausGateway.a2aServerEgressOn" .) -}}true{{- end -}}
{{- end -}}

{{/*
Truthy (emits "true") while klaus-gateway runs the egress refusal notices
(giantswarm/giantswarm#38667) — the meta chart's define of the same name,
which this chart must agree with: klaus-gateway with Slack and
klausGateway.refusals.enabled on — which the meta chart resolves from auto,
the floors of the klaus-gateway and kagent ranges included — kagent and
Substrate on, the controller in the kubernetes authorization mode. The gateway then reads the Kubernetes API
(the egress's EndpointSlices, the Agents), the stats port of Substrate's
atenet-egress and the kagent controller's API port
(-klausgateway-refusals-egress).
*/}}
{{- define "agent-platform.klausGateway.refusalsOn" -}}
{{- $kg := .Values.klausGateway | default dict -}}
{{- $mode := dig "controller" "authorization" "mode" "" (.Values.kagent | default dict) -}}
{{- if and (include "agent-platform.componentEnabled" (dict "root" . "name" "klaus-gateway")) (dig "slack" "enabled" false $kg) (dig "refusals" "enabled" false $kg) (include "agent-platform.componentEnabled" (dict "root" . "name" "kagent")) (include "agent-platform.componentEnabled" (dict "root" . "name" "substrate")) (eq (toString $mode) "kubernetes") -}}true{{- end -}}
{{- end -}}

{{/*
Truthy (emits "true") when the gateway pod keeps its routes in the platform's
Valkey: klausGateway.routing.store: valkey with the valkey component on
(components.valkey.enabled — the pods the rule selects). Empty otherwise; an
out-of-band Valkey (routing.valkey.url set, the component off) is not in this
namespace and gets no rule here — its egress is the installation's to add.
*/}}
{{- define "agent-platform.klausGateway.valkeyEgress" -}}
{{- if and (eq (dig "routing" "store" "memory" .Values.klausGateway) "valkey") (include "agent-platform.valkey.enabled" .) -}}true{{- end -}}
{{- end -}}

{{/*
The port the platform's Valkey Service listens on (valkey.valkey.service.port,
default 6379) — the one the meta chart puts into the gateway's routing.valkey.url.
*/}}
{{- define "agent-platform.klausGateway.valkeyPort" -}}
{{- dig "valkey" "service" "port" 6379 (.Values.valkey | default dict) -}}
{{- end -}}

{{/*
Truthy (emits "true") when the gateway pod reaches any store beyond its own
process — the gate of the -klausgateway-store-egress policy: the Valkey
routing store or anything on the Kubernetes API (the Secret link store, the
team-review endpoint's TokenReview, the refusal notices' reads).
*/}}
{{- define "agent-platform.klausGateway.storeEgress" -}}
{{- if or (include "agent-platform.klausGateway.valkeyEgress" .) (include "agent-platform.klausGateway.apiServerEgress" .) -}}true{{- end -}}
{{- end -}}

{{/*
Truthy (emits "true") while the A2A server's agentgateway entry renders:
klaus-gateway on and klausGateway.agentgatewayRoute.a2aServer.enabled.
*/}}
{{- define "agent-platform.klausGateway.a2aServerOn" -}}
{{- if and (include "agent-platform.componentEnabled" (dict "root" . "name" "klaus-gateway")) (dig "agentgatewayRoute" "a2aServer" "enabled" false (.Values.klausGateway | default dict)) -}}true{{- end -}}
{{- end -}}

{{/* The A2A server needs its own JWKS and muster regardless of Slack OBO. */}}
{{- define "agent-platform.klausGateway.a2aServerEgressOn" -}}
{{- if and (include "agent-platform.componentEnabled" (dict "root" . "name" "klaus-gateway")) (or (dig "a2aServer" "enabled" false (.Values.klausGateway | default dict)) (include "agent-platform.klausGateway.a2aServerOn" .)) -}}
{{- if and .Values.networkPolicy.enabled (eq (include "agent-platform.networkPolicyFlavor" .) "kubernetes") -}}
{{- $cidr := .Values.networkPolicy.kubernetes.apiServerCIDR | trim -}}
{{- if or (not $cidr) (hasSuffix "/0" $cidr) -}}
{{- fail "networkPolicy.kubernetes.apiServerCIDR must name the API server's explicit address range when klausGateway's A2A server is enabled: its organization lookup needs API access, and a /0 range would allow all egress" -}}
{{- end -}}
{{- end -}}
true
{{- end -}}
{{- end -}}

{{/*
The A2A server's JWKS and muster endpoints, deduplicated by host and port.
Service addresses carry their namespace for pod selectors; other addresses
use FQDN or CIDR rules. URLs without a port use their HTTP scheme's default.
*/}}
{{- define "agent-platform.klausGateway.a2aServerEgressTargets" -}}
{{- $gateway := .Values.klausGateway | default dict -}}
{{- $token := dig "a2aServer" "token" dict $gateway -}}
{{- $jwks := $token.jwksUrl | default "" -}}
{{- if not $jwks -}}
{{- with ($token.issuer | default .Values.global.identity.issuerUrl) -}}
{{- $jwks = printf "%s/keys" (trimSuffix "/" .) -}}
{{- end -}}
{{- end -}}
{{- $muster := dig "obo" "musterUrl" "" $gateway | default (dig "muster" "oauth" "server" "baseUrl" "" (.Values.muster | default dict)) -}}
{{- $targets := include "agent-platform.klausGateway.urlEgressTargets" (list $jwks $muster) | fromJsonArray -}}
{{- $targets = concat $targets (include "agent-platform.klausGateway.a2aProxyTargets" (dict "ctx" . "role" "gateway") | fromJsonArray) -}}
{{- $targets | uniq | toJson -}}
{{- end -}}

{{/* HTTP endpoint targets for network policy, deduplicated by host and port. */}}
{{- define "agent-platform.klausGateway.urlEgressTargets" -}}
{{- $targets := list -}}
{{- $seen := dict -}}
{{- range $url := . -}}
{{- if $url -}}
{{- $parsed := urlParse $url -}}
{{- $scheme := lower $parsed.scheme -}}
{{- $host := include "agent-platform.jwks.normalizeHost" ($parsed.hostname | default "") -}}
{{- if or (not $host) (not (has $scheme (list "http" "https"))) -}}
{{- fail "klausGateway A2A egress targets require HTTP or HTTPS URLs" -}}
{{- end -}}
{{- $port := regexFind ":[0-9]+$" $parsed.host | trimPrefix ":" | default (ternary "80" "443" (eq $scheme "http")) | int -}}
{{- $namespace := "" -}}
{{- if include "agent-platform.jwks.inCluster" $host -}}
{{- $namespace = index (splitList "." $host) 1 -}}
{{- end -}}
{{- $key := printf "%s:%d" $host $port -}}
{{- if not (hasKey $seen $key) -}}
{{- $_ := set $seen $key true -}}
{{- $targets = append $targets (dict "host" $host "port" $port "namespace" $namespace) -}}
{{- end -}}
{{- end -}}
{{- end -}}
{{- $targets | toJson -}}
{{- end -}}

{{/* Native BackendTunnel URLs used by each A2A request hop. */}}
{{- define "agent-platform.klausGateway.a2aProxyTargets" -}}
{{- $urls := list -}}
{{- if include "agent-platform.klausGateway.a2aServerOn" .ctx -}}
{{- $entry := .ctx.Values.klausGateway.agentgatewayRoute.a2aServer -}}
{{- if or (eq .role "controller") (eq .role "gateway") -}}
{{- range $entry.issuers -}}
{{- with dig "jwks" "tunnel" "url" "" . -}}{{- $urls = append $urls . -}}{{- end -}}
{{- end -}}
{{- end -}}
{{- if or (eq .role "dataplane") (eq .role "gateway") -}}
{{- with dig "exchange" "endpoint" "tunnel" "url" "" $entry -}}{{- $urls = append $urls . -}}{{- end -}}
{{- end -}}
{{- end -}}
{{- include "agent-platform.klausGateway.urlEgressTargets" $urls -}}
{{- end -}}

{{/* The data plane's Dex token endpoint and its configured CONNECT proxy. */}}
{{- define "agent-platform.klausGateway.a2aExchangeTargets" -}}
{{- $targets := list -}}
{{- if include "agent-platform.klausGateway.a2aServerOn" . -}}
{{- $endpoint := (include "agent-platform.klausGateway.a2aServer" . | fromJson).exchange.endpoint -}}
{{- $namespace := "" -}}
{{- if $endpoint.inCluster -}}{{- $namespace = index (splitList "." (include "agent-platform.jwks.normalizeHost" $endpoint.host)) 1 -}}{{- end -}}
{{- $targets = append $targets (dict "host" (include "agent-platform.jwks.normalizeHost" $endpoint.host) "port" ($endpoint.port | int) "namespace" $namespace) -}}
{{- $targets = concat $targets (include "agent-platform.klausGateway.a2aProxyTargets" (dict "ctx" . "role" "dataplane") | fromJsonArray) -}}
{{- end -}}
{{- $targets | uniq | toJson -}}
{{- end -}}

{{/* A CA key follows its Secret: inherited names use the global key. */}}
{{- define "agent-platform.klausGateway.a2aCASecretKey" -}}
{{- $tls := .jwks.tls | default dict -}}
{{- $key := $tls.caSecretKey | default "" -}}
{{- if and (not $key) (not $tls.caSecretName) -}}
{{- $key = dig "identity" "ca" "key" "" .ctx.Values.global -}}
{{- end -}}
{{- $key | default "ca.crt" -}}
{{- end -}}

{{/* Cilium rules for known HTTP destinations, using their own TCP ports. */}}
{{- define "agent-platform.klausGateway.egress.cilium" -}}
{{- $externalPorts := list -}}
{{- range .targets }}
{{- if .namespace }}
- toEndpoints:
    - matchLabels:
        io.kubernetes.pod.namespace: {{ .namespace | quote }}
{{- else if include "agent-platform.jwks.addrLiteral" .host }}
- toCIDR:
    - {{ printf "%s/%d" .host (ternary 128 32 (contains ":" .host)) | quote }}
{{- else }}
- toFQDNs:
    - matchName: {{ .host | quote }}
{{- end }}
  toPorts:
    - ports:
        - port: {{ .port | toString | quote }}
          protocol: TCP
{{- if not .namespace }}{{- $externalPorts = append $externalPorts (.port | int) }}{{- end }}
{{- end }}
{{- with .ctx.Values.gateway.jwksEgress.external.cidrs }}
{{- if $externalPorts }}
- toCIDR:
    {{- toYaml . | nindent 4 }}
  toPorts:
    - ports:
        {{- range (uniq $externalPorts) }}
        - port: {{ . | toString | quote }}
          protocol: TCP
        {{- end }}
{{- end }}
{{- end }}
{{- end -}}

{{/* Kubernetes rules for known HTTP destinations and configured private CIDRs. */}}
{{- define "agent-platform.klausGateway.egress.kubernetes" -}}
{{- $externalPorts := list -}}
{{- range .targets }}
{{- if .namespace }}
- to:
    - namespaceSelector:
        matchLabels:
          kubernetes.io/metadata.name: {{ .namespace | quote }}
{{- else if include "agent-platform.jwks.addrLiteral" .host }}
- to:
    - ipBlock:
        cidr: {{ printf "%s/%d" .host (ternary 128 32 (contains ":" .host)) | quote }}
{{- else }}
- to:
    - ipBlock:
        cidr: 0.0.0.0/0
        except: {{ toYaml $.ctx.Values.networkPolicy.kubernetes.worldExcludedCIDRs | nindent 10 }}
{{- end }}
  ports:
    - port: {{ .port | int }}
      protocol: TCP
{{- if not .namespace }}{{- $externalPorts = append $externalPorts (.port | int) }}{{- end }}
{{- end }}
{{- with .ctx.Values.gateway.jwksEgress.external.cidrs }}
{{- if $externalPorts }}
- to:
    {{- range . }}
    - ipBlock:
        cidr: {{ . | quote }}
    {{- end }}
  ports:
    {{- range (uniq $externalPorts) }}
    - port: {{ . | int }}
      protocol: TCP
    {{- end }}
{{- end }}
{{- end }}
{{- end -}}

{{/*
The A2A server's entry, resolved and checked, as JSON: the
klausGateway.agentgatewayRoute.a2aServer block with hostname and
exchange.endpoint defaulted, and the endpoint's CA Secret and whether it is
in-cluster resolved. Fails the render on a block agentgateway could not
serve: a request it would refuse, or exchange at no connector, fails closed at
runtime with no other trace than a 401.
Usage: include "agent-platform.klausGateway.a2aServer" . | fromJson
*/}}
{{- define "agent-platform.klausGateway.a2aServer" -}}
{{- $route := .Values.klausGateway.agentgatewayRoute | default dict -}}
{{- $a2a := deepCopy ($route.a2aServer | default dict) -}}
{{- $path := "klausGateway.agentgatewayRoute.a2aServer" -}}
{{- if not (include "agent-platform.ingress.agentgateway" .) -}}
{{- fail (printf "%s.enabled is true but muster is off: the A2A server's token check and exchange run on the agentgateway data plane, which this release renders only in front of muster" $path) -}}
{{- end -}}
{{- $_ := set $a2a "hostname" ($a2a.hostname | default $route.hostname | default "") -}}
{{- if not $a2a.hostname -}}
{{- fail (printf "%s.enabled is true but neither %s.hostname nor klausGateway.agentgatewayRoute.hostname is set: the A2A server needs a public hostname" $path $path) -}}
{{- end -}}
{{- if not $a2a.issuers -}}
{{- fail (printf "%s.enabled is true but %s.issuers is empty: agentgateway would accept no A2A client token" $path $path) -}}
{{- end -}}
{{- $names := dict -}}
{{- $issuers := dict -}}
{{- range $i, $issuer := $a2a.issuers -}}
{{- range $k := list "name" "issuer" "audience" "connectorId" -}}
{{- if not (index $issuer $k) -}}
{{- fail (printf "%s.issuers[%d].%s is empty: every issuer needs name, issuer, audience, allowedClients, connectorId and jwks" $path $i $k) -}}
{{- end -}}
{{- end -}}
{{- if not $issuer.allowedClients -}}
{{- fail (printf "%s.issuers[%d].allowedClients is empty: agentgateway checks the client that requested the token (azp) against it, and an empty list would refuse every token" $path $i) -}}
{{- end -}}
{{- if has $a2a.exchange.clientId $issuer.allowedClients -}}
{{- fail (printf "%s.issuers[%d].allowedClients names the exchange client %s: its tokens are the platform tokens agentgateway issues, never an A2A client's" $path $i $a2a.exchange.clientId) -}}
{{- end -}}
{{- if hasKey $names $issuer.name -}}
{{- fail (printf "%s.issuers[%d].name %q is not unique" $path $i $issuer.name) -}}
{{- end -}}
{{- if hasKey $issuers $issuer.issuer -}}
{{- fail (printf "%s.issuers[%d].issuer %q is not unique" $path $i $issuer.issuer) -}}
{{- end -}}
{{- $_ := set $names $issuer.name true -}}
{{- $_ := set $issuers $issuer.issuer true -}}
{{- $_ := include "agent-platform.jwks.validate" (dict "ctx" $ "path" (printf "%s.issuers[%d]" $path $i) "jwks" $issuer.jwks) -}}
{{- end -}}
{{- $exchange := $a2a.exchange | default dict -}}
{{- if not $exchange.clientId -}}
{{- fail (printf "%s.exchange.clientId is empty: the exchange authenticates at Dex as this client" $path) -}}
{{- end -}}
{{- if not $exchange.secretName -}}
{{- fail (printf "%s.exchange.secretName is empty: name the Secret in this release's namespace that holds the secret of the Dex client %s" $path $exchange.clientId) -}}
{{- end -}}
{{- if not (and (kindIs "slice" $exchange.audiences) $exchange.audiences) -}}
{{- fail (printf "%s.exchange.audiences must be a nonempty list of audience strings" $path) -}}
{{- end -}}
{{- range $index, $audience := $exchange.audiences -}}
{{- if or (not (kindIs "string" $audience)) (not (regexMatch `^[\x21\x23-\x5b\x5d-\x7e]+$` (toString $audience))) -}}
{{- fail (printf "%s.exchange.audiences[%d] must be a nonempty string using OAuth scope-token characters to form a Dex cross-client scope" $path $index) -}}
{{- end -}}
{{- end -}}
{{- if not (has (toString $exchange.subjectTokenType) (list "IdToken" "AccessToken")) -}}
{{- fail (printf "%s.exchange.subjectTokenType is %q: IdToken or AccessToken" $path (toString $exchange.subjectTokenType)) -}}
{{- end -}}
{{- $endpoint := deepCopy ($exchange.endpoint | default dict) -}}
{{- $tls := deepCopy ($endpoint.tls | default dict) -}}
{{- if not $endpoint.host -}}
{{- $issuerUrl := include "agent-platform.issuerUrl" (dict "ctx" . "for" (printf "%s.exchange.endpoint.host" $path)) | trimSuffix "/" -}}
{{- $u := urlParse $issuerUrl -}}
{{- if ne $u.scheme "https" -}}
{{- fail (printf "%s.exchange.endpoint.host is empty and the issuer %q is not https: the exchange sends the client secret and the client's token, so set %s.exchange.endpoint to Dex's TLS token endpoint" $path $issuerUrl $path) -}}
{{- end -}}
{{- $hostPort := splitList ":" $u.host -}}
{{- $_ := set $endpoint "host" (first $hostPort) -}}
{{- $_ := set $endpoint "port" (ternary (last $hostPort | int) 443 (eq (len $hostPort) 2)) -}}
{{- $_ := set $endpoint "path" (printf "%s/token" (trimSuffix "/" $u.path)) -}}
{{- $_ := set $tls "enabled" true -}}
{{- end -}}
{{- $_ := set $endpoint "tls" $tls -}}
{{- if not $endpoint.port -}}
{{- fail (printf "%s.exchange.endpoint.port is empty: the port Dex serves its token endpoint on over TLS" $path) -}}
{{- end -}}
{{- if or (include "agent-platform.jwks.hostCarriesPort" $endpoint.host) (include "agent-platform.jwks.addrLiteral" $endpoint.host) (include "agent-platform.jwks.malformedName" $endpoint.host) -}}
{{- fail (printf "%s.exchange.endpoint.host is %q: Dex's hostname alone, with no scheme, port, path or address literal" $path $endpoint.host) -}}
{{- end -}}
{{- if not (hasPrefix "/" (toString $endpoint.path)) -}}
{{- fail (printf "%s.exchange.endpoint.path is %q: the token endpoint's path, starting with /" $path (toString $endpoint.path)) -}}
{{- end -}}
{{- if not (include "agent-platform.jwks.tlsEnabled" $endpoint) -}}
{{- fail (printf "%s.exchange.endpoint is not TLS (port %v, tls.enabled false): the exchange sends the client secret and the client's token, so set %s.exchange.endpoint.tls.enabled: true" $path $endpoint.port $path) -}}
{{- end -}}
{{- $_ := set $endpoint "caSecretName" (include "agent-platform.jwks.caSecretName" (dict "ctx" . "jwks" $endpoint)) -}}
{{- $_ := set $endpoint "inCluster" (include "agent-platform.jwks.inCluster" $endpoint.host) -}}
{{- $_ := set $exchange "endpoint" $endpoint -}}
{{- $_ := set $a2a "exchange" $exchange -}}
{{- $a2a | toJson -}}
{{- end -}}

{{/*
The CEL rule agentgateway requires of an A2A client's verified token: one
clause per issuer, its iss and the client that requested it among the issuer's
allowedClients. The requesting client is azp, else client_id (RFC 9068), else
the only aud, which a JWT may carry as a string or a one-item list.
Usage: include "agent-platform.klausGateway.a2aClientRule" $issuers
*/}}
{{- define "agent-platform.klausGateway.a2aClientRule" -}}
{{- $client := "(has(jwt.azp) ? jwt.azp : (has(jwt.client_id) ? jwt.client_id : (type(jwt.aud) == string ? jwt.aud : (size(jwt.aud) == 1 ? jwt.aud[0] : \"\"))))" -}}
{{- $clauses := list -}}
{{- range . -}}
{{- $clauses = append $clauses (printf "(jwt.iss == %s && %s in %s)" (toJson .issuer) $client (toJson .allowedClients)) -}}
{{- end -}}
{{- join " || " $clauses -}}
{{- end -}}

{{/*
The CEL expression of the exchange's connector_id: the Dex connector of the
verified token's issuer. Every iss reaching it matched a provider of the same
policy, so the final empty string is never sent.
Usage: include "agent-platform.klausGateway.a2aConnectorExpr" $issuers
*/}}
{{- define "agent-platform.klausGateway.a2aConnectorExpr" -}}
{{- $expr := "\"\"" -}}
{{- range reverse . -}}
{{- $expr = printf "jwt.iss == %s ? %s : (%s)" (toJson .issuer) (toJson .connectorId) $expr -}}
{{- end -}}
{{- $expr -}}
{{- end -}}
