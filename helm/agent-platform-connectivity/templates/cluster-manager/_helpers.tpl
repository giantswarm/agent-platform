{{/* vim: set filetype=mustache: */}}
{{/*
Helpers of the cluster-manager component's wiring (templates/cluster-manager/).

Two values blocks feed these templates: clusterManager (the umbrella wiring —
network policy inputs, guards) and cluster-manager (the component chart's own
values: OAuth, the muster registration, Service name and port; hyphenated, so
reached through index), read here the way the model-manager and agent-manager
templates read theirs — a component release's values cannot be derived at
render time, so the wiring reads what the chart will see.
*/}}

{{/*
Truthy when the cluster-manager component is on (components.cluster-manager.enabled).
*/}}
{{- define "agent-platform.clusterManager.enabled" -}}
{{- include "agent-platform.componentEnabled" (dict "root" . "name" "cluster-manager") -}}
{{- end -}}

{{/*
The component chart's values block, cluster-manager (a dict; empty when unset).
*/}}
{{- define "agent-platform.clusterManager.chartValues" -}}
{{- index .Values "cluster-manager" | default dict | toJson -}}
{{- end -}}

{{/*
The cluster-manager Service name. Single source of truth: the umbrella pins
cluster-manager.fullnameOverride (values.yaml), which the component chart uses
verbatim for its Service, and the network policies target exactly that name —
a misconfiguration fails the render instead of a silent 503.
*/}}
{{- define "agent-platform.clusterManager.fullname" -}}
{{- $chart := include "agent-platform.clusterManager.chartValues" . | fromJson -}}
{{- required "cluster-manager.fullnameOverride must be set — the umbrella's network policies target this exact Service name" (dig "fullnameOverride" "" $chart) -}}
{{- end -}}

{{/*
The port the cluster-manager Service listens on (cluster-manager.service.port, default 8080).
*/}}
{{- define "agent-platform.clusterManager.servicePort" -}}
{{- $chart := include "agent-platform.clusterManager.chartValues" . | fromJson -}}
{{- dig "service" "port" 8080 $chart -}}
{{- end -}}

{{/*
Truthy when the component validates the caller's identity itself
(cluster-manager.oauth.enabled): the network policies then admit egress to the
identity provider.
*/}}
{{- define "agent-platform.clusterManager.oauthEnabled" -}}
{{- $chart := include "agent-platform.clusterManager.chartValues" . | fromJson -}}
{{- if dig "oauth" "enabled" false $chart }}true{{ end -}}
{{- end -}}

{{/*
The identity provider the component validates tokens with
(cluster-manager.oauth.provider): dex (the default) or google.
*/}}
{{- define "agent-platform.clusterManager.oauthProvider" -}}
{{- $chart := include "agent-platform.clusterManager.chartValues" . | fromJson -}}
{{- dig "oauth" "provider" "dex" $chart -}}
{{- end -}}

{{/*
The issuer URL the component validates tokens against: the dex provider's
cluster-manager.oauth.dex.issuerURL, else global.identity.issuerUrl (the chart's
own fallback). Empty for the google provider and when neither is set.
*/}}
{{- define "agent-platform.clusterManager.issuerUrl" -}}
{{- $chart := include "agent-platform.clusterManager.chartValues" . | fromJson -}}
{{- if eq (include "agent-platform.clusterManager.oauthProvider" .) "dex" -}}
{{- dig "oauth" "dex" "issuerURL" "" $chart | default .Values.global.identity.issuerUrl -}}
{{- end -}}
{{- end -}}

{{/*
Labels of every object the umbrella renders for the component.
*/}}
{{- define "agent-platform.clusterManager.labels" -}}
{{ include "labels.common" . }}
app.kubernetes.io/component: cluster-manager
{{- end -}}

{{/*
The selector labels of the cluster-manager pods, as the component chart stamps
them (app.kubernetes.io/name from its chart name or nameOverride). The
component runs as its own release, so it is selected by name only, not by a
release-scoped instance label — like the muster policies.
Rendered as YAML mapping entries; the caller provides the indentation.
*/}}
{{- define "agent-platform.clusterManager.podSelector" -}}
{{- $chart := include "agent-platform.clusterManager.chartValues" . | fromJson -}}
app.kubernetes.io/name: {{ dig "nameOverride" "" $chart | default "cluster-manager" }}
{{- end -}}

{{/*
The workload clusters' API server ports (clusterManager.networkPolicy.workloadClusters.ports),
rendered as the port entries of a policy rule — quoted for Cilium, integers for
vanilla NetworkPolicy. The caller provides the indentation.
*/}}
{{- define "agent-platform.clusterManager.workloadClusterPorts.cilium" -}}
{{- range .Values.clusterManager.networkPolicy.workloadClusters.ports }}
- port: {{ . | toString | quote }}
  protocol: TCP
{{- end }}
{{- end -}}

{{- define "agent-platform.clusterManager.workloadClusterPorts.kubernetes" -}}
{{- range .Values.clusterManager.networkPolicy.workloadClusters.ports }}
- port: {{ . | int }}
  protocol: TCP
{{- end }}
{{- end -}}
