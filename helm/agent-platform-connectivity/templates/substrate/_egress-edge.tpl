{{/* vim: set filetype=mustache: */}}
{{/*
The in-cluster destinations the actors' egress gateway (atenet-egress) reaches
on 443 and 10443, as a JSON list of {namespace, matchLabels}: the pods of the
public Gateway muster's route attaches to (ingress.parentRefs, else the
chart-owned edge, else global.gatewayApi.parentRefs;
agent-platform.route.edgeNamespaces), since muster's public hostname, which an
actor's OAuth flow reaches, resolves to that Gateway's load balancer and
Cilium checks egress after the service translation: the destination is the
backend pod, on its own port (10443 is the Envoy listener behind an Azure load
balancer's 443). The chart-owned edge is the agentgateway data plane
(gateway.name in this release's namespace); a front Gateway is Envoy
Gateway's proxy (app.kubernetes.io/name: envoy) in each of its namespaces.
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
{{- $refs := .Values.ingress.parentRefs | default list -}}
{{- if include "agent-platform.route.toDataPlane" (dict "ctx" . "parentRefs" $refs) -}}
{{- $peers = append $peers (dict "namespace" .Release.Namespace "matchLabels" (dict "gateway.networking.k8s.io/gateway-name" .Values.gateway.name)) -}}
{{- else -}}
{{- range (include "agent-platform.route.edgeNamespaces" (dict "ctx" . "parentRefs" $refs) | fromJsonArray) -}}
{{- $peers = append $peers (dict "namespace" . "matchLabels" (dict "app.kubernetes.io/name" "envoy")) -}}
{{- end -}}
{{- end -}}
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
