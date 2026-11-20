# Workspace CLI configuration

With `workspaces.enabled`, the platform configures git, gh and glab through the
existing upstream `Harness.spec.env` mechanism. The default and additional
Harnesses receive only inert credential values. Git receives a credential
helper. A configured GitHub provider adds gh's token value; a configured GitLab
provider adds glab's token, OAuth mode, HTTPS git protocol and host. Unconfigured
providers add no CLI defaults. The explicit git protocol preserves HTTPS
cloning; glab's default is SSH.
The Go ADK also needs the generic declared-shell-environment implementation
tracked by kagent-dev/kagent#3002; native Harnesses already receive their declared
environment.

The gateway's Session grant, actor binding, destination, path and authorization
header decide real credential access. These environment values grant nothing.
Keep model, MCP, source-artifact and per-turn gateway bindings independently.

`kagent.harness.env` and each `kagent.harnesses[].env` override defaults by name.
Customize the canonical `GH_TOKEN` and `GITLAB_TOKEN` entries when changing
token values; another variable name does not replace those defaults.
Empty values stay empty. One configured GitLab provider supplies its host,
including a non-default port. With no GitLab provider, no GitLab values are
added. With multiple GitLab hosts, every Harness must explicitly select its
`GITLAB_HOST`; use separate Harness configurations for distinct default hosts.
Custom git environment configuration must supply a consistent
`GIT_CONFIG_COUNT` and its indexed keys and values.

```yaml
kagent:
  harnesses:
    - name: claude
      runtime: claude
      snapshotLocation: s3://snapshots/claude
      env:
        - name: GITLAB_HOST
          value: gitlab.example.com
```

Tool binaries come from digest-pinned toolchains. Image binaries precede mounted
tools on PATH, so a proof of a pinned CLI selects its mounted absolute path.
The platform adds no additional OAuth sign-in for attachments; the GitHub upload
service restriction is independent of ordinary git and API requests.
