{{/*
CLI values are inert configuration. Session grants and destination-scoped
gateway bindings own access. Explicit Harness entries, including empty values,
override these defaults by name. Git's indexed configuration is one unit: any
explicit GIT_CONFIG_* entry replaces all git defaults.
*/}}
{{- define "agent-platform.kagent.cliEnvironment" -}}
{{- $hosts := list -}}
{{- $schemes := dict -}}
{{- $kinds := dict -}}
{{- range .providers -}}
{{- $_ := set $kinds .kind true -}}
{{- if eq .kind "gitlab" -}}
{{- $url := urlParse .values.url -}}
{{- $host := $url.host -}}
{{- $_ := set $schemes $host $url.scheme -}}
{{- if not (has $host $hosts) -}}
{{- $hosts = append $hosts $host -}}
{{- end -}}
{{- end -}}
{{- end -}}
{{- $gitlabHost := "" -}}
{{- if eq (len $hosts) 1 -}}
{{- $gitlabHost = first $hosts -}}
{{- end -}}
{{- $git := list
  (dict "name" "GIT_CONFIG_COUNT" "value" "1")
  (dict "name" "GIT_CONFIG_KEY_0" "value" "credential.helper")
  (dict "name" "GIT_CONFIG_VALUE_0" "value" "!f() { test \"$1\" = get && printf 'username=x-access-token\\npassword=kagent-credential-injected\\n'; }; f")
-}}
{{- $defaults := list -}}
{{- if hasKey $kinds "github" -}}
{{- $defaults = append $defaults (dict "name" "GH_TOKEN" "value" "ghu_kagent-credential-injected") -}}
{{- end -}}
{{- if hasKey $kinds "gitlab" -}}
{{- $defaults = concat $defaults (list
  (dict "name" "GITLAB_TOKEN" "value" "kagent-credential-injected")
  (dict "name" "GLAB_IS_OAUTH2" "value" "true")
  (dict "name" "GLAB_GIT_PROTOCOL" "value" "https")
  (dict "name" "GITLAB_HOST" "value" $gitlabHost)
  (dict "name" "GLAB_CHECK_UPDATE" "value" "false")
) -}}
{{- if eq (get $schemes $gitlabHost) "http" -}}
{{- $defaults = append $defaults (dict "name" "GLAB_API_PROTOCOL" "value" "http") -}}
{{- end -}}
{{- end -}}
{{- range concat (list .values.harness) (.values.harnesses | default list) -}}
{{- $configured := .env | default list -}}
{{- $names := dict -}}
{{- range $configured -}}
{{- $_ := set $names .name true -}}
{{- end -}}
{{- if and (gt (len $hosts) 1) (not (hasKey $names "GITLAB_HOST")) -}}
{{- fail "multiple GitLab hosts require an explicit GITLAB_HOST in kagent.harness.env and each kagent.harnesses[].env" -}}
{{- end -}}
{{- $environment := list -}}
{{- $gitConfigured := false -}}
{{- range $configured -}}
{{- if hasPrefix "GIT_CONFIG_" .name -}}
{{- $gitConfigured = true -}}
{{- end -}}
{{- end -}}
{{- if not $gitConfigured -}}
{{- $environment = concat $environment (deepCopy $git) -}}
{{- end -}}
{{- range $defaults -}}
{{- if not (hasKey $names .name) -}}
{{- $environment = append $environment (deepCopy .) -}}
{{- end -}}
{{- end -}}
{{- $_ := set . "env" (concat $environment $configured) -}}
{{- end -}}
{{- end -}}
