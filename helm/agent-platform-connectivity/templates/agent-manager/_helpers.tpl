{{/* vim: set filetype=mustache: */}}
{{/*
Helpers of the agent-manager component's wiring (templates/agent-manager/).

Two values blocks feed these templates: agentManager (the umbrella wiring —
route, JWT policy, network policy inputs, guards) and agent-manager (the
component chart's own values: the kagent namespace, the agent chart URL, OAuth,
Service name and port; hyphenated, so reached through index), read here the
way the model-manager templates read theirs — a component release's values
cannot be derived at render time, so the wiring reads what the chart will see.
*/}}

{{/*
Truthy when the agent-manager component is on (components.agent-manager.enabled).
*/}}
{{- define "agent-platform.agentManager.enabled" -}}
{{- include "agent-platform.componentEnabled" (dict "root" . "name" "agent-manager") -}}
{{- end -}}

{{/*
The component chart's values block, agent-manager (a dict; empty when unset).
*/}}
{{- define "agent-platform.agentManager.chartValues" -}}
{{- index .Values "agent-manager" | default dict | toJson -}}
{{- end -}}

{{/*
The agent-manager Service name. Single source of truth: the umbrella pins
agent-manager.fullnameOverride (values.yaml), which the component chart uses
verbatim for its Service, and the AgentgatewayBackend host and the network
policies target exactly that name — a misconfiguration fails the render
instead of a silent 503.
*/}}
{{- define "agent-platform.agentManager.fullname" -}}
{{- $chart := include "agent-platform.agentManager.chartValues" . | fromJson -}}
{{- required "agent-manager.fullnameOverride must be set — the umbrella's route and network policies target this exact Service name" (dig "fullnameOverride" "" $chart) -}}
{{- end -}}

{{/*
The port the agent-manager Service listens on (agent-manager.service.port, default 8080).
*/}}
{{- define "agent-platform.agentManager.servicePort" -}}
{{- $chart := include "agent-platform.agentManager.chartValues" . | fromJson -}}
{{- dig "service" "port" 8080 $chart -}}
{{- end -}}

{{/*
The namespace agent-manager creates agents in by default (agent-manager.kagent.namespace).
*/}}
{{- define "agent-platform.agentManager.kagentNamespace" -}}
{{- $chart := include "agent-platform.agentManager.chartValues" . | fromJson -}}
{{- dig "kagent" "namespace" "kagent" $chart -}}
{{- end -}}

{{/*
The host of the agent chart's OCI registry (agent-manager.agentChart.ociUrl),
the one destination the service must reach for the chart's versions and
values schema.
*/}}
{{- define "agent-platform.agentManager.registryHost" -}}
{{- $chart := include "agent-platform.agentManager.chartValues" . | fromJson -}}
{{- $url := dig "agentChart" "ociUrl" "oci://gsoci.azurecr.io/charts/giantswarm/agent" $chart -}}
{{- regexReplaceAll "^oci://([^/]+)/.*$" $url "${1}" -}}
{{- end -}}

{{/*
Truthy when the component validates the caller's identity itself
(agent-manager.oauth.enabled): the network policies then admit egress to the
identity provider.
*/}}
{{- define "agent-platform.agentManager.oauthEnabled" -}}
{{- $chart := include "agent-platform.agentManager.chartValues" . | fromJson -}}
{{- if dig "oauth" "enabled" false $chart }}true{{ end -}}
{{- end -}}

{{/*
The identity provider the component validates tokens with
(agent-manager.oauth.provider): dex (the default) or google.
*/}}
{{- define "agent-platform.agentManager.oauthProvider" -}}
{{- $chart := include "agent-platform.agentManager.chartValues" . | fromJson -}}
{{- dig "oauth" "provider" "dex" $chart -}}
{{- end -}}

{{/*
The issuer URL the component validates tokens against: the dex provider's
agent-manager.oauth.dex.issuerURL, else global.identity.issuerUrl (the chart's
own fallback). Empty for the google provider (whose public endpoints
agent-platform.idpHosts names from the provider alone) and when neither is set.
*/}}
{{- define "agent-platform.agentManager.issuerUrl" -}}
{{- $chart := include "agent-platform.agentManager.chartValues" . | fromJson -}}
{{- if eq (include "agent-platform.agentManager.oauthProvider" .) "dex" -}}
{{- dig "oauth" "dex" "issuerURL" "" $chart | default .Values.global.identity.issuerUrl -}}
{{- end -}}
{{- end -}}

{{/*
The public hostname of the agent-manager route: the override when set, else
agentgateway.<global.domain> — the same hostname as the kagent controller route.
*/}}
{{- define "agent-platform.agentManager.hostname" -}}
{{- $route := .Values.agentManager.route -}}
{{- include "agent-platform.hostname" (dict "ctx" . "prefix" "agentgateway" "override" $route.hostname "key" "agentManager.route.hostname") -}}
{{- end -}}

{{/*
Labels of every object the umbrella renders for the component.
*/}}
{{- define "agent-platform.agentManager.labels" -}}
{{ include "labels.common" . }}
app.kubernetes.io/component: agent-manager
{{- end -}}

{{/*
The selector labels of the agent-manager pods, as the component chart stamps
them (app.kubernetes.io/name from its chart name or nameOverride). The
component runs as its own release, so it is selected by name only, not by a
release-scoped instance label — like the muster policies.
Rendered as YAML mapping entries; the caller provides the indentation.
*/}}
{{- define "agent-platform.agentManager.podSelector" -}}
{{- $chart := include "agent-platform.agentManager.chartValues" . | fromJson -}}
app.kubernetes.io/name: {{ dig "nameOverride" "" $chart | default "agent-manager" }}
{{- end -}}

{{/*
The egress rules to the agent chart's sources — the OCI registry
(agent-manager.agentChart.ociUrl's host; versions, values schema), the storage
front the registry redirects blob downloads to (gsoci.azurecr.io answers a
chart blob GET with a redirect to *.blob.core.windows.net, which a policy that
names the registry alone drops) and the GitHub API skill discovery and pinning
read from — on 443, in the flavor's own dialect. One definition for every pod
that reads the chart: the agent-manager Deployment and the cut-over Job
(templates/kagent/migrate-*.yaml) admit exactly the same destinations
(giantswarm/agent-platform#433: the Job's policy named the registry and GitHub
only, and the migration rewrote nothing on a Cilium installation).
cilium: the registry host and agentManager.networkPolicy.egress.fqdns by name,
agentManager.networkPolicy.egress.cidrs by address. kubernetes: vanilla
NetworkPolicy selects addresses, never names — the cidrs when set, else every
public destination (0.0.0.0/0 minus networkPolicy.kubernetes.worldExcludedCIDRs).
Rendered as YAML list items; the caller provides the indentation.
*/}}
{{- define "agent-platform.agentManager.chartSourcesEgress.cilium" -}}
{{- $egress := .Values.agentManager.networkPolicy.egress -}}
- toFQDNs:
    - matchName: {{ include "agent-platform.agentManager.registryHost" . }}
    {{- range $egress.fqdns }}
    - {{ toYaml . | nindent 6 | trim }}
    {{- end }}
  toPorts:
    - ports:
        - port: "443"
          protocol: TCP
{{- with $egress.cidrs }}
- toCIDR:
    {{- toYaml . | nindent 4 }}
  toPorts:
    - ports:
        - port: "443"
          protocol: TCP
{{- end }}
{{- end -}}

{{- define "agent-platform.agentManager.chartSourcesEgress.kubernetes" -}}
{{- $egress := .Values.agentManager.networkPolicy.egress -}}
{{- if $egress.cidrs -}}
# The chart registry and GitHub by address (agentManager.networkPolicy.egress.cidrs).
- to:
    {{- range $egress.cidrs }}
    - ipBlock:
        cidr: {{ . | quote }}
    {{- end }}
  ports:
    - port: 443
      protocol: TCP
{{- else -}}
# The chart registry, its storage front and GitHub: every public destination.
- to:
    - ipBlock:
        cidr: 0.0.0.0/0
        except: {{ toYaml .Values.networkPolicy.kubernetes.worldExcludedCIDRs | nindent 10 }}
  ports:
    - port: 443
      protocol: TCP
{{- end }}
{{- end -}}
