{{/* vim: set filetype=mustache: */}}
{{/*
The in-cluster destinations the actors' egress gateway (atenet-egress) reaches
on 443 and 10443, as a JSON list of {namespace, matchLabels}: the pods of the
public Gateway muster's route attaches to (ingress.parentRefs, else the
chart-owned edge, else global.gatewayApi.parentRefs;
agent-platform.route.edgePeers), since muster's public hostname, which an
actor's OAuth flow reaches, resolves to that Gateway's load balancer and
Cilium checks egress after the service translation: the destination is the
backend pod, on its own port (10443 is the Envoy listener behind an Azure load
balancer's 443). The chart-owned edge is the agentgateway data plane
(gateway.name in this release's namespace); a front Gateway is the proxy
Envoy Gateway runs for that Gateway (agent-platform.route.edgePeers).
Every other in-cluster endpoint stays out of the actors' reach on those ports
(giantswarm/agent-platform#1268). Empty with muster off.

The render check: a load balancer that lands on host networking bypasses the
pod selector (the destination identity is the node's, not the pod's), and the
actors' OAuth flow would stop at the gateway. Where the API server answers
(helm-controller, `helm template --dry-run=server`), the render fails when a
selected Gateway pod runs with hostNetwork. A plain `helm template` reads no
cluster and checks nothing.
Usage: include "agent-platform.substrate.egressEdge" . | fromJsonArray
*/}}
{{- define "agent-platform.substrate.egressEdge" -}}
{{- $peers := list -}}
{{- if and .Values.muster.enabled (include "agent-platform.componentEnabled" (dict "root" . "name" "muster")) -}}
{{- $peers = include "agent-platform.route.edgePeers" (dict "ctx" . "parentRefs" (.Values.ingress.parentRefs | default list)) | fromJsonArray -}}
{{- range $peers -}}
{{- $peer := . -}}
{{- range (dig "items" list (lookup "v1" "Pod" $peer.namespace "")) -}}
{{- $labels := dig "metadata" "labels" dict . -}}
{{- $match := true -}}
{{- range $k, $v := $peer.matchLabels -}}{{- if ne (index $labels $k | default "") $v -}}{{- $match = false -}}{{- end -}}{{- end -}}
{{- if and $match (dig "spec" "hostNetwork" false .) -}}
{{- fail (printf "the actors' egress gateway reaches muster's public hostname at the pods of its Gateway, selected by endpoint (%s in %s), and the pod %s/%s runs with hostNetwork: a load balancer that lands on host networking bypasses that selector. Run the Gateway's proxy on pod networking, or point ingress.parentRefs at a Gateway that does (giantswarm/agent-platform#1268)" (toJson $peer.matchLabels) $peer.namespace $peer.namespace .metadata.name) -}}
{{- end -}}
{{- end -}}
{{- end -}}
{{- end -}}
{{- $peers | toJson -}}
{{- end -}}

{{/*
Fail the render for an entry of networkPolicy.substrate.egressGateway.privateCIDRs
that is not an IPv4 CIDR with a prefix of 1 to 32, or that overlaps the instance
metadata range (169.254.0.0/16) or Azure's WireServer (168.63.129.16): the
actors' egress gateway reaches those on every port. RFC 1918 ranges are what
the list is for. The values schema refuses the shape too; this guard holds
the addresses.
Usage: include "agent-platform.substrate.validatePrivateCIDRs" .
*/}}
{{- define "agent-platform.substrate.validatePrivateCIDRs" -}}
{{- range (dig "substrate" "egressGateway" "privateCIDRs" list .Values.networkPolicy) -}}
{{- $cidr := toString . -}}
{{- if not (regexMatch "^(?:(?:25[0-5]|2[0-4][0-9]|1[0-9][0-9]|[1-9]?[0-9])[.]){3}(?:25[0-5]|2[0-4][0-9]|1[0-9][0-9]|[1-9]?[0-9])/(?:3[0-2]|[12][0-9]|[1-9])$" $cidr) -}}
{{- fail (printf "networkPolicy.substrate.egressGateway.privateCIDRs: %q is not an IPv4 CIDR with a prefix of 1 to 32: the actors' egress gateway reaches each entry on every port" $cidr) -}}
{{- end -}}
{{- $parts := splitList "/" $cidr -}}
{{- $octets := splitList "." (first $parts) -}}
{{- $ip := add (mul (atoi (index $octets 0)) 16777216) (mul (atoi (index $octets 1)) 65536) (mul (atoi (index $octets 2)) 256) (atoi (index $octets 3)) -}}
{{- $prefix := atoi (last $parts) -}}
{{- range $name, $target := dict "the instance metadata range 169.254.0.0/16" (list 2851995648 16) "Azure's WireServer 168.63.129.16" (list 2822734096 32) -}}
{{- /* Two blocks overlap when they agree on the shorter prefix's bits. */ -}}
{{- $bits := min $prefix (index $target 1) -}}
{{- $block := 1 -}}
{{- range until (sub 32 $bits | int) }}{{ $block = mul $block 2 }}{{ end -}}
{{- if eq (div $ip $block) (div (index $target 0) $block) -}}
{{- fail (printf "networkPolicy.substrate.egressGateway.privateCIDRs: %s overlaps %s, which the actors' egress gateway never reaches" $cidr $name) -}}
{{- end -}}
{{- end -}}
{{- end -}}
{{- end -}}

{{/*
Fail the render for an entry of networkPolicy.kubernetes.ipv6.excludedCIDRs that
is not an IPv6 CIDR: it is an `except` of the gateway's ::/0 block, and the API
server refuses an IPv4 one there at apply time, failing the release instead of
the render.
Usage: include "agent-platform.substrate.validateIPv6CIDRs" .
*/}}
{{- define "agent-platform.substrate.validateIPv6CIDRs" -}}
{{- range (dig "kubernetes" "ipv6" "excludedCIDRs" list .Values.networkPolicy) -}}
{{- if not (regexMatch "^[0-9a-fA-F]{0,4}:[0-9a-fA-F:]*/(?:12[0-8]|1[01][0-9]|[1-9]?[0-9])$" (toString .)) -}}
{{- fail (printf "networkPolicy.kubernetes.ipv6.excludedCIDRs: %q is not an IPv6 CIDR: name the cluster's own IPv6 pod and Service CIDRs" (toString .)) -}}
{{- end -}}
{{- end -}}
{{- end -}}
