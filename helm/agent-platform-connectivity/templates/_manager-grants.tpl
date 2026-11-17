{{- define "agent-platform.managerGrantBroker" -}}
{{- $broker := dict -}}
{{- $url := dig "github" "tokenExchange" "tokenEndpoint" "" .chart -}}
{{- if and .oauthOn $url -}}
{{- $key := printf "%s.github.tokenExchange.tokenEndpoint" .component -}}
{{- if not (regexMatch "^https://[^/]+" $url) -}}
{{- fail (printf "%s requires an HTTPS broker endpoint" $key) -}}
{{- end -}}
{{- $parsed := urlParse $url -}}
{{- $host := include "agent-platform.jwks.normalizeHost" ($parsed.hostname | default "") -}}
{{- if or (include "agent-platform.jwks.inCluster" $host) (and (not (include "agent-platform.jwks.addrLiteral" $host)) (lt (len (splitList "." $host)) 3)) -}}
{{- fail (printf "%s names an in-cluster or search-expanded Service: use the broker's external hostname or address" $key) -}}
{{- end -}}
{{- $port := regexFind ":[0-9]+$" ($parsed.host | default "") | trimPrefix ":" | default "443" | int -}}
{{- $broker = dict "host" $host "port" $port "cidr" "" -}}
{{- if include "agent-platform.jwks.addrLiteral" $host -}}
{{- $_ := set $broker "cidr" (printf "%s/%s" $host (ternary "128" "32" (contains ":" $host))) -}}
{{- end -}}
{{- end -}}
{{- $broker | toJson -}}
{{- end -}}

{{- define "agent-platform.managerGrantBroker.egress" -}}
{{- $root := .root -}}
{{- $cidrs := .cidrs | default list -}}
{{- with .broker -}}
{{- if eq (include "agent-platform.networkPolicyFlavor" $root) "cilium" -}}
- {{ if .cidr }}toCIDR:
    - {{ .cidr | quote }}
  {{- else }}toFQDNs:
    - matchName: {{ .host | quote }}
  {{- end }}
  toPorts:
    - ports:
        - port: {{ .port | quote }}
          protocol: TCP
{{- else -}}
- to:
    - ipBlock:
        {{- if .cidr }}
        cidr: {{ .cidr | quote }}
        {{- else }}
        cidr: 0.0.0.0/0
        except: {{ toYaml $root.Values.networkPolicy.kubernetes.worldExcludedCIDRs | nindent 10 }}
    {{- range concat $cidrs $root.Values.networkPolicy.additionalEgressCIDRs }}
    - ipBlock:
        cidr: {{ . | quote }}
    {{- end }}
        {{- end }}
  ports:
    - port: {{ .port | int }}
      protocol: TCP
{{- end -}}
{{- end -}}
{{- end -}}
