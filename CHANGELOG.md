# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed

- The `kagent-pg` CNPG Cluster now sets `inheritedMetadata.labels` with
  `observability.giantswarm.io/tenant: giantswarm`, which the operator copies
  onto the PodMonitor it generates. Without it the PodMonitor carried only
  CNPG's stock labels and matched neither of alloy-metrics' discovery
  selectors, so it was created and silently never scraped (zero `cnpg_*`
  series in Mimir). The previous `podMonitorRelabelings` attempt is removed:
  it rewrote scraped series, not the PodMonitor object's labels, so it could
  never make the monitor discoverable.

### Changed

- `components.muster.versionRange` default changed from `">=4.1.0 <5.0.0"` to `"5.x"`, so the meta-package resolves the current muster line (`v5.1.0`). The `>=4.1.0` floor is subsumed by the major: it existed because the muster values here set `observability.metrics.prometheus.prometheusRule`, and every `5.x` accepts that key. **No installation needs to act.** muster `v5.0.0` ([muster#1078](https://github.com/giantswarm/muster/pull/1078)) is breaking only for muster instances running *without* their own OAuth: with no bearer there was no token family, so the transport injected the `default-user` placeholder and every concurrent MCP client collapsed onto one session — one client's authenticated backend became visible and callable in every other client's session, on the first client's token. Such sessions are now keyed by MCP transport session, which means a session ends with the connection and a reconnecting client re-runs `core_auth_login`. Every installation of this umbrella runs `muster.muster.oauth.server.enabled: true` (the default here, overridden in no `*-configs` installation patch across the 14 deployments), so a real token-family session ID still wins and the crossing is a no-op for the fleet; stdio is unaffected either way. muster `v5.1.0` ([muster#1079](https://github.com/giantswarm/muster/pull/1079)) additionally fails the render on an explicitly emptied `rbac.mcpServerEditor.subjects` / `rbac.workflowEditor.subjects`; both are bound to the Giant Swarm admin groups by this chart (since #232) and overridden nowhere, so the guard is satisfied. The crossing also picks up [muster#1077](https://github.com/giantswarm/muster/pull/1077) (the chart values-schema fix), which the never-published `v4.1.1` chart left unreachable.

- `components.muster.versionRange` default changed from `"3.x"` to `"4.x"`. muster `v4.0.0` ([muster#1075](https://github.com/giantswarm/muster/pull/1075), [muster#1069](https://github.com/giantswarm/muster/issues/1069)) routes Workflow spec mutations (`core_workflow_create`/`_update`/`_delete`) through the same caller-identity gate MCPServer writes got in `v2.0.0`: in Kubernetes mode the write carries the session's own dex id_token, authorized by k8s RBAC via the chart-shipped `workflow-editor` role (bound to all authenticated users by default, mirroring `mcpserver-editor`). **No installation needs to act:** sessions already must carry the dex-k8s-authenticator audience for MCPServer writes since the `2.x` crossing (same gate, same token), the `workflow-editor` Role/RoleBinding ships in the muster chart itself, and no fleet installation overrides `rbac.mcpServerEditor` or disables muster `rbac.create` (checked via org-wide code search). The crossing also *fixes* `core_workflow_update` on Kubernetes installations — the old path sent an update without a `resourceVersion`, which the apiserver rejects for CRs, so the tool never worked there.

### Removed

- The deprecated `agentic-platform-mcps` values key. Overrides must live under `agent-platform-mcps`. The `legacyValuesFrom` component field that carried the fallback is gone too, along with the free-form `agentic-platform-mcps` property in both `values.schema.json` files: setting the old key is now a schema error rather than a silent override.

### Changed

- Charts renamed: `agentic-platform` is now `agent-platform` and `agentic-platform-connectivity` is now `agent-platform-connectivity`. The published OCI paths change to `oci://gsoci.azurecr.io/charts/giantswarm/agent-platform` and `.../agent-platform-connectivity`; the last release under the old names is 2.5.4. The GitHub repository is now `giantswarm/agent-platform`.
- The mcps values key is renamed from `agentic-platform-mcps` to `agent-platform-mcps`, following the `agent-platform-mcps` chart rename (first new-name release 0.7.0, still within the `0.x` range).
- Namespace-derived defaults now assume the `agent-platform` namespace: `agent-platform-mcps.muster.musterUrl` and `kagent.a2a.url`. Deployments in a different namespace must override them (unchanged requirement, new default value).
- The default Postgres cluster name (when `postgres.clusterName` is unset) is `agent-platform-pg` instead of `agentic-platform-pg`.

### Fixed

- The agentgateway component now resolves from the `1.x` chart line. The wrapper chart left `0.x` at `0.1.0`; every release since is `1.0.x`, so the `0.x` range held the component on a chart that vendors a `v1.3.1` controller. `1.0.11` is the first release that vendors `v1.4.1`.
- The agentgateway controller and proxy image tags are no longer pinned here. Both now come from the `agentgateway` wrapper chart: the controller from the vendored subchart's appVersion (`v1.4.1`), the proxy from that chart's own `proxy.image.tag`. The `gsoci.azurecr.io` registry overrides stay. Gateway API `1.6` (shipped by `gateway-api-bundle` `1.19.0`) serves `TCPRoute` at `v1` only, and `v1.2.1` watches `v1alpha2`: its `TCPRoutes` informer never syncs, `/readyz` never answers, and the install fails the startup probe.
- kagent images are no longer held at `0.9.9`. `kagent.tag: "0.9.9"` was still pinned in the meta-package values, overriding the wrapper chart and keeping `kagent-controller` and `kagent-ui` on `0.9.9` even though `kagent-app` has vendored upstream `0.9.12` since chart `0.1.32`. The pin was a workaround for pulling the upstream chart directly via `OCIRepository`, where helm-controller appended the OCI digest to the chart version and `+` is illegal in an image tag; now that `kagent-app` vendors the upstream chart as a subchart, its `.Chart.Version` stays clean and the upstream tag coalesce resolves correctly on its own.
- The example customer BOM pinned `kagent` to `versionRange: "0.9.9"`, a version the wrapper chart never publishes (`kagent-app` ships `0.1.x`), so the example could not resolve. Pinned to `0.1.33`.
- muster metrics are now actually scraped. Both charts (umbrella + connectivity) passed `serviceMonitor.enabled: true` at the top level of the muster values — a key the muster chart never had, so it was silently ignored: no `/metrics` endpoint, no metrics port, no ServiceMonitor on any installation. The toggle now lives at the real path, `muster.observability.metrics.prometheus.serviceMonitor.enabled` (muster ≥ `v1.4.0` derives the prometheus exporter from it), with a `60s` interval and the `observability.giantswarm.io/tenant: giantswarm` label so the series land in the giantswarm Mimir tenant.

### Changed

- `klausGateway.*` forwarded sub-blocks in both `values.schema.json` (umbrella + connectivity) relaxed to `additionalProperties: true`: `crd`, `observability`, `slack`, `cli`, `agentgatewayRoute`, `obo`, `obo.persistence`, `obo.connectors`. These mirror klaus-gateway subchart config forwarded via `forwardAllValues`, so the subchart's own schema is the source of truth; the umbrella no longer re-validates them. New klaus-gateway keys stop failing the meta-package HelmRelease schema check before they can be mirrored by hand (the recurring `Additional property ... is not allowed` break, e.g. [#209](https://github.com/giantswarm/agentic-platform/pull/209)/[#210](https://github.com/giantswarm/agentic-platform/pull/210)/[#211](https://github.com/giantswarm/agentic-platform/pull/211)). This aligns `klausGateway` with the other forwarded components (`kagent`, `muster`, `mcps`, `agentgateway`), which are already permissive or undeclared. Meta-package-owned blocks (`postgres`, `gateway`, `ingress`, `networkPolicy`, `global`) stay `additionalProperties: false`.
- `components.muster.versionRange` default changed from `"0.x"` to `"3.x"`. muster crossed three majors inside this unreleased window; the range now resolves the current line. **No installation needs to act for any of them** — the specifics below. First crossing: muster crossed a major with [#968](https://github.com/giantswarm/muster/pull/968) (`feat(oauth)!: remove JWT mode`) — muster no longer signs tokens; dex is the sole SSO authority. The `0.x` default has kept every non-overriding installation frozen on muster `0.24.0` since the `1.0.0` bump, so they never received the `1.x` line (including the `#37164` SSO re-exchange deauth fix first released in muster `v1.0.7`). Every fleet installation already runs dex-only (`shared-configs` sets none of the removed keys — `enableJWTMode`/`jwtSigningKey`/`localMint`/M2M), and alligator already runs `1.0.7` healthy, so the jump is safe. **Upgrade note:** crossing into muster `1.0.7+` re-authenticates every user exactly once (the mcp-oauth bump changes the Valkey token-storage layout with no legacy read-fallback); existing sessions do not carry over.
  Second crossing, muster `v2.0.0` ([muster#1064](https://github.com/giantswarm/muster/pull/1064)): the `writesAsCaller` transition gate is gone, so in Kubernetes mode session-initiated MCPServer mutations (create/update/delete and CR-driven start/stop/restart) always write with the caller's own dex id_token, authorized by k8s RBAC via the chart-shipped `mcpserver-editor` role (bound to all authenticated users by default). Sessions must carry the dex-k8s-authenticator audience and the apiserver must trust dex OIDC — already true fleet-wide. The gate lived two hours and no installation ever set it.
  Third crossing, muster `v3.0.0` ([muster#1072](https://github.com/giantswarm/muster/pull/1072), [muster#1067](https://github.com/giantswarm/muster/issues/1067)): `type: stdio` MCPServers are rejected in Kubernetes mode — a stdio server is started as a subprocess of the muster pod, so accepting one made "may write MCPServer resources" mean "may execute code in the muster pod as muster's ServiceAccount". Validate/create/update fail the tool call; a stdio CR applied with `kubectl` reconciles to `status.state: Failed` and is never started. **Nothing to migrate:** no gitops manifest in the org and no live installation declares a `type: stdio` MCPServer (all are `streamable-http`), which is also why this crossing did not need to be a major. Should one ever be needed, run the MCP server as its own workload and register it with `type: streamable-http` or `sse`; `stdio` still works when muster runs as a local CLI.
- `kagent.controller.auth.userIdClaim` default changed from `sub` to `email` (umbrella + connectivity `values.yaml`). The kagent controller now keys sessions and identity on the dex `email` claim instead of dex's opaque, connector-prefixed `sub`. This matches muster's trusted-issuer `subjectClaim: email` and makes the UI path (oauth2-proxy forwards the same dex id_token) and the A2A path resolve to the same identity. Existing kagent sessions keyed by old `sub` values are orphaned by this change (session history resets); the claim falls back to `sub` in the controller when `email` is absent from the token.

### Added

- `/connectors/complete` added to the `klausgateway-obo` HTTPRoute (connectivity chart), routing straight to the klaus-gateway Service on the OBO public hostname alongside `/auth/slack/`. This is the connector-login completion landing: after a connector sign-in muster redirects the browser here (klaus-gateway#175), so it must bypass the agentgateway JWT policy that guards the channel paths (the browser carries no JWT at that point). Only rendered when `klausGateway.obo.enabled` and `ingress.parentRefs` are set. Without it a `postLoginRedirectAllowlist` entry pointing at this path would 404 at the data plane and the flow would degrade to muster's static success page.
- `klausGateway.obo.connectors` (umbrella + connectivity `values.yaml` and `values.schema.json`) is forwarded to the klaus-gateway subchart, which enables the reactive Slack connector UX (`obo.connectors.enabled` -> `KLAUS_GATEWAY_OBO_CONNECTORS_ENABLED`): the gateway detects a `core_auth_login` challenge in the agent's response stream and offers a "Connect <backend>" button from the login link the agent relays. Disabled by default. The `obo` block in both schemas is `additionalProperties: false`, so a cluster setting this key on 2.4.2 fails the HelmRelease schema check (`Additional property connectors is not allowed`); this entry closes that gap.
- `kagent.controllerRoute.jwtAuthentication` (umbrella + connectivity `values.yaml` and `values.schema.json`): opt-in JWT validation on the inner `kagent-controller` HTTPRoute. When enabled, the connectivity chart renders an `AgentgatewayPolicy` (dex issuer, no required audiences) and a static `AgentgatewayBackend` (`kagent-controller-jwks`) for the JWKS endpoint, so agentgateway verifies the bearer's signature, issuer, and expiry against dex before forwarding to the kagent controller. `kagent.controllerRoute.jwtAuthentication.mode` selects the validation strictness (enum `Strict`, `Optional`, `Permissive`; default `Strict`): `Strict` rejects requests with a missing or invalid token, `Optional` validates a token when present but allows requests with none, `Permissive` validates without rejecting so an install can confirm real traffic carries valid dex tokens before enforcing. This makes the trusted-proxy assumption of `kagent.controller.auth.mode: trusted-proxy` hold for A2A callers routed through agentgateway; previously no policy existed and any parseable JWT passed. Intra-namespace agent pods (`app: kagent`) that call the controller directly on 8083 (allowed by the kagent NetworkPolicy) bypass agentgateway and this policy, and are out of scope. Off by default: enabling it rejects callers that do not present a dex-issued JWT (opaque muster tokens, Kubernetes ServiceAccount tokens), so enable it per install only once all A2A callers forward dex JWTs. Requires `gateway.jwksEgress.enabled: true` so the data plane can reach dex. A `ci/test-kagent-jwt-policy-values.yaml` render test covers the new resources, and a render-time guard fails when it is enabled without an issuer.
- `dicebear` avatar renderer as a force-enabled meta-package component (umbrella `values.yaml` `components.dicebear` + a forwarded top-level `dicebear:` block, `values.schema.json`, `examples/customer-bom.yaml` pinned to `0.3.0`). Wires the self-hosted [giantswarm/dicebear](https://github.com/giantswarm/dicebear) chart into the bundle so the DiceBear avatar API is deployed on **every** cluster that runs the platform, per [giantswarm/giantswarm#37211](https://github.com/giantswarm/giantswarm/issues/37211). Rendered as an OCIRepository + HelmRelease like every other component (`injectGlobal: false` — the chart's strict schema consumes `registry.domain`, not `global`). Its public avatar `HTTPRoute` is **on by default**, so every installation must set `dicebear.route.parentRefs` (typically the same public Gateway as `ingress.parentRefs`) and `dicebear.route.hostnames` (e.g. `avatars.<domain>`) — the chart hard-fails otherwise, by design, matching the existing `ingress.*` per-cluster contract.
- Shared muster `RemoteMCPServer` for tenant agents. The connectivity chart now renders a single `RemoteMCPServer` named `muster` in the release namespace (`agentic-platform`) with `allowedNamespaces: {from: All}`, so agents in any namespace can reference the muster gateway cross-namespace — this is the admin-owned server the generic [`agent` chart](https://github.com/giantswarm/agent) defaults its `serverRef` to (`muster`/`agentic-platform`), per the [creating-agents PRD](https://github.com/giantswarm/bumblebee-plans/blob/main/creating-agents/PRD.md). On-behalf-of only: the server sets no `headersFrom`, so the caller token forwarded by kagent (`KAGENT_PROPAGATE_TOKEN`) is the only `Authorization` reaching muster, and muster authenticates every call. Rendered unconditionally whenever `kagent.enabled` and `muster.enabled` — the CR is inert until an agent references it — and the name is fixed by the template: name/namespace are the platform contract the agent chart's default `serverRef` depends on, so neither is a value.
- `agents.models[].displayName` (umbrella + connectivity `values.yaml` and `values.schema.json`): an optional human-friendly name for a kagent `ModelConfig`. When set, it is rendered as the `ui.giantswarm.io/display-name` annotation on the generated `ModelConfig` CR so the UI can show a friendly label; when omitted, no `annotations:` block is emitted. This change also adds the previously-missing `agents.models` schema (required `name`/`provider`/`model`/`apiKeySecret`, optional `displayName`/`apiKeySecretKey`, `additionalProperties: false`) so model entries are now validated.
- `klausGateway.obo.persistence` (umbrella + connectivity `values.yaml` and `values.schema.json`) is forwarded to the klaus-gateway subchart, which backs the encrypted link store with a ReadWriteOnce PVC so a human's Slack<->muster link survives gateway pod recreation (rollouts, node drains), not just in-process restarts. Disabled by default (emptyDir, unchanged). Requires the klaus-gateway chart that supports it (giantswarm/klaus-gateway#118, v0.10.3). Pairs with that release's store-key fix, which accepts the SOPS-staged base64 `obo.storeKey` directly so `obo.storePath` can finally be set without the gateway crashing on boot.
- klausgateway Slack OBO (on-behalf-of) wiring. The `klausGateway.obo.*` block (`enabled`, `musterUrl`, `callbackBaseUrl`, `storePath`, `stateKey`, `storeKey`) is now declared and documented in both the umbrella and connectivity `values.yaml` (with matching `values.schema.json` entries) so it is forwarded to the klaus-gateway subchart. The connectivity chart renders a new public `HTTPRoute` (`klausgateway-obo`) that exposes the unauthenticated OAuth-bootstrap paths `/auth/slack/link`, `/auth/slack/callback`, and the CIMD document `/auth/slack/client.json` on the gateway's public hostname (derived from `obo.callbackBaseUrl`), routing straight to the klaus-gateway Service so they bypass the JWT policy that guards the channel paths. Both the linking browser and muster (CIMD fetch) reach the gateway over this route. Disabled by default. A `ci/test-klausgateway-obo-values.yaml` render test covers the new route.
- `klausGateway.slack.dmOnly` (umbrella `values.yaml` + `values.schema.json`) is forwarded to the klaus-gateway subchart, which renders it as `SLACK_DM_ONLY` to restrict the Slack adapter to direct messages (channel messages and @-mentions ignored). Recommended for the OBO gateway, which is a DM-only bot. Default false. Requires the klaus-gateway chart that renders the env (giantswarm/klaus-gateway#113).
- `klausGateway.slack.botToken` / `signingSecret` / `appToken` (umbrella `values.yaml` + `values.schema.json`) are forwarded to the klaus-gateway subchart, which renders the `slack.secretName` Secret (keys `bot-token` / `signing-secret` / `app-token`) from them. This lets the per-cluster `secret-values.yaml.patch` stage SOPS-encrypted Slack credentials that become the `slack-credentials` Secret the gateway consumes. Requires the klaus-gateway chart that renders the Secret (giantswarm/klaus-gateway#114).

### Fixed

- Removed duplicate `channelMode` / `channelAllowlist` / `dmMode` entries in the umbrella `values.schema.json` `klausGateway.slack` block (each was declared twice).
- `klausGateway.slack.channelMode` / `channelAllowlist` / `dmMode` added to the umbrella `values.schema.json`. The klaus-gateway subchart (0.20.x) introduced channel-mode routing (`channelMode`: `all`/`allowlist`/`none` with `channelAllowlist`, and `dmMode`: `serve`/`redirect`/`ignore`), but the umbrella schema's `klausGateway.slack` block (`additionalProperties: false`) was never extended, so any installation setting these keys — e.g. gazelle's swarmgeist channel pilot ([giantswarm-configs#762](https://github.com/giantswarm/giantswarm-configs/pull/762)) — failed the helm upgrade at schema validation (`Additional property channelMode is not allowed`), leaving the HelmRelease stuck in `Failed` and blocking all pending upgrades (PagerDuty incident #6917). The umbrella now mirrors the subchart's schema for these three keys.
- The same `channelMode` / `channelAllowlist` / `dmMode` keys are declared in the `agentic-platform-connectivity` `values.schema.json` too. The umbrella forwards its whole `klausGateway` block to the connectivity HelmRelease via `forwardAllValues`, so fixing only the umbrella would relocate the `Additional property channelMode is not allowed` failure to the connectivity release once the umbrella upgrade reconciles.
- `agentic-platform-connectivity`: the `kagent-srt-settings` Kyverno policy now reads the existing `srt-settings.json` from the Secret's `data` (base64-decoded) instead of `stringData`. The API server folds `stringData` into `data` before admission webhooks run (`Convert_v1_Secret_To_core_Secret`), so the policy's merge source was always empty and the mutation replaced the controller-generated settings with `{"enableWeakerNestedSandbox":true}` alone. srt then rejected the file (`network: Required, filesystem: Required`), which broke sandboxed skill execution and left agents without their muster MCP tools ("Tool not found" on `filter_tools`/`call_tool`/`describe_tool`). The merged file now keeps the controller's `network` and `filesystem` sections alongside the flag. Existing agent config Secrets are only re-mutated on their next write; restart the kagent controller after deploying to force a rewrite.
- kagent A2A routing: the inner `kagent-controller` HTTPRoute (on the agentgateway data-plane Gateway) is no longer hostname-scoped. It was rendered with `hostnames: [<public hostname>]` from `kagent.controllerRoute.hostname`, but in-cluster A2A callers — notably klaus-gateway's `--a2a-url`, which targets the agentgateway Service's cluster-DNS name (`agentgateway.<ns>.svc.cluster.local`) — send that Service host, which did not match the public hostname. The request fell through to the catch-all MCP route (`agentic-platform-mcps`, path `/`), was handled as MCP Streamable-HTTP, and was rejected with `406 Not Acceptable ("mcp: client must accept both application/json and text/event-stream")`, so the Slack OBO sre-agent turn never reached the agent (it had passed JWT validation — the human token was forwarded — but died at content negotiation on the wrong route). The inner hop is internal and must match any Host; the `/kagent` prefix is more specific than the MCP `/` route, so a hostname-less route wins for every Host. The outer `kagent-controller-public` route keeps the public `hostname` (required on the shared TLS Gateway).
- klausgateway channel routing: a new `*-dataplane-to-klausgateway` egress policy (cilium + kubernetes flavors, rendered when `klausGateway.agentgatewayRoute.enabled`) lets the agentgateway data-plane reach the `klaus-gateway` Service on `:8080`. The channel paths (`/v1`, `/web`, `/cli/v1`, and `/channels/slack` when `slack.enabled`) are served on the data-plane Gateway and forwarded to klaus-gateway, but the data-plane runs in default-deny egress (the `-dataplane` policy) whose cluster allowance only covers `80`/`443`. klaus-gateway listens on `8080`, so the forward was dropped and every channel request — including inbound Slack events delivered to the public hostname — failed with `503 UpstreamFailure ("Connect: deadline has elapsed")`, so the Slack bot never replied. Mirrors the existing `-dataplane-to-kagent` allowance.
- `agentic-platform-connectivity` `values.schema.json`: allow `klausGateway.slack.{dmOnly,botToken,signingSecret,appToken}`. The umbrella forwards its whole `klausGateway` block to the connectivity HelmRelease via `forwardAllValues`, but the connectivity `slack` schema had `additionalProperties: false` and only declared `enabled`/`mode`/`secretName`, so a real install (gazelle) failed the HelmRelease upgrade with `Additional property dmOnly/botToken/signingSecret/appToken is not allowed`. These keys are consumed by the klaus-gateway subchart, not this chart; they are now declared (and documented as forwarded-only) so validation passes.
- klausgateway Slack OBO egress: a new `klausgateway-obo-egress` NetworkPolicy (cilium + kubernetes flavors, rendered when `klausGateway.obo.enabled`) lets the klaus-gateway pod reach the muster authorization server on 443/10443 for RFC 8414 discovery and the OAuth token exchange. The gateway is put into default-deny egress by the `klausgateway-a2a-egress` policy, which only allowed DNS + the agentgateway data plane; without this allowance the OBO token call to muster's public issuer host (which resolves to the public NLB / private LB VIP) was dropped. Mirrors the existing kagent-agent and oauth2-proxy `world`+`cluster` 443/10443 egress.
- klausgateway connectivity route: the `AgentgatewayBackend` `.spec.static.host` now defaults to the correct `klaus-gateway` Service name (the klaus-gateway chart's default, matching `templates/klausgateway/netpol.yaml`) instead of `klausgateway`, which resolved to a non-existent Service when `klausGateway.fullnameOverride` was unset.

### Removed

- **Agent creation and the whole `agents.*` values key.** Agents are no longer created by this chart — install one release of the generic [`agent` chart](https://github.com/giantswarm/agent) per agent instead, per the [creating-agents PRD](https://github.com/giantswarm/bumblebee-plans/blob/main/creating-agents/PRD.md). The `agents.definitions` block is removed from the umbrella and connectivity `values.yaml`, along with the templates it drove in `agentic-platform-connectivity`: the kagent `Agent` CR + chart-owned `ServiceAccount` (`agents.yaml`), the per-agent `muster-<name>` `RemoteMCPServer` loop (the `agent` chart references the shared `muster` server cross-namespace instead), the per-agent A2A `AgentgatewayBackend` + `/agents/<name>` `HTTPRoute` (`agentgateway-backends.yaml` — not part of the PRD's chart scope and unused: in-cluster A2A callers go through the kagent controller route; use `extraObjects` for one-offs), and the now-unused `_agents.tpl` helpers. The two admin-owned lists that remained (`agents.models`, `agents.remoteMcpServers`) moved into the `kagent` block — see Changed — dissolving the `agents` key entirely. Leftover `agents.*` keys in per-cluster values are silently ignored (the root schema is deliberately permissive for `forwardAllValues`), which enables a zero-gap migration on the auto-updating dev track: add the `kagent.*` keys alongside the old ones, let the chart roll out, then delete the old keys. The rest of the admin-owned surface the `agent` chart depends on stays: the shared `muster` `RemoteMCPServer` and the kagent-namespace policies (muster-egress CNP, Kyverno exception, declarative-agent pod security), which apply to agent pods regardless of which chart created them. **Migration:** any cluster with `agents.definitions.sre-agent.enabled: true` must have an `agent`-chart-based replacement (carrying the eval-tuned `systemMessage`) deployed *before* upgrading, or the live Agent CR is deleted; per-cluster values setting `agents.models`/`agents.remoteMcpServers` should add the equivalent `kagent.modelConfigs`/`kagent.remoteMcpServers` keys **before** the chart rolls out (old keys can be removed afterwards).
- **The per-agent muster token machinery and the `agents.definitions.<agent>.obo` flag.** The `muster-token-init` Job, the `muster-refresh` CronJob, the shared token pod-spec helper, the Secret-writer Role/RoleBinding, the static `headersFrom` `Authorization` on the per-agent muster `RemoteMCPServer`, and the `agents.muster.{kubectlImage,busyboxImage,tokenExpirationSeconds,tokenRefreshSchedule}` values are removed from `agentic-platform-connectivity` (and the umbrella defaults). With M2M gone, acting on behalf of the user is the only mode: the RemoteMCPServer carries no static credentials and kagent's propagated user token (`KAGENT_PROPAGATE_TOKEN`) is the sole `Authorization` reaching muster, so the `obo` toggle has nothing left to switch. `agents.remoteMcpServers[].tokenSecret` now only accepts a pre-existing Secret; the chart no longer writes one.
- **M2M agent impersonation RBAC.** The `agents.<name>.m2m` block (and the `impersonation.yaml` / `_agent-impersonation.tpl` templates it rendered) is removed from `agentic-platform-connectivity`. Agents no longer get a deputy `Role`/`ClusterRole` letting mcp-kubernetes impersonate the agent's own identity or bind its groups to ClusterRoles. The only supported flow is on-behalf-of: muster mints a token with the human as `sub` and the agent SA in the RFC 8693 `act` claim, and the mcp-kubernetes chart's `*-obo-impersonate` ClusterRole authorizes impersonating the human. Requires mcp-kubernetes with M2M removed and muster with the matching broker config.
- **Retired the `agentic-platform-crds` bundle chart** — every component now owns its CRDs (app-owned CRDs). The standalone chart (`helm/agentic-platform-crds/`), its CircleCI build/test/push jobs, and the now-dead Renovate `helmv3` lockstep `packageRules` are deleted. There is no longer a `components.agentic-platform-crds` entry in the meta-package.
- `klausGateway.slack.dmOnly` (umbrella `values.yaml` and both `values.schema.json`). klaus-gateway removed the `SLACK_DM_ONLY` env and the `slack.dmOnly` value, replacing them with `dmMode` + `channelMode`, and already ignores a leftover `dmOnly`. The umbrella drops the dead default and, with `additionalProperties: false`, no longer accepts the key. **Migration:** `dmOnly: true` becomes `dmMode: serve` + `channelMode: none`; the old `dmOnly: false` (channels served, DMs redirected) becomes `dmMode: redirect` + `channelMode: all`. An install still setting `dmOnly` (e.g. graveler [giantswarm-configs#742](https://github.com/giantswarm/giantswarm-configs/pull/742)) must migrate before this merges.

### Changed

- **`agents.models` → `kagent.modelConfigs`, `agents.remoteMcpServers` → `kagent.remoteMcpServers`** (umbrella + connectivity `values.yaml` and `values.schema.json`). With agent creation gone (see Removed), the leftover `agents` key only held admin-owned kagent CRs, which was misleading; both lists now live in the `kagent` block next to `providers` (whose `anthropic` entry already yields the subchart-created `default-model-config`), under the same "read by connectivity templates, not the subchart" convention as `controllerRoute`/`uiRoute`. `models` is renamed to `modelConfigs` to match the CR kind. The rendering templates moved up from `templates/kagent/agents/` to `templates/kagent/`, and `kagent.remoteMcpServers` gains the schema validation it never had (required `name`/`url`, optional `description`/`tokenSecret`, `additionalProperties: false`). Note these keys ride into the kagent component's HelmRelease values (ignored by that chart today, like the routing keys) — if upstream kagent ever introduces same-named values, they must be renamed here.
- `kagent.oauth2-proxy`: request the `offline_access` scope and set `cookie-refresh: "10m"` so oauth2-proxy refreshes the upstream Dex id_token before it expires. Without this, a session older than the Dex id_token TTL forwarded an expired bearer as the on-behalf-of subject token, muster rejected it (`subject_token_validation_failed`, "token is expired"), and the agent silently fell back to its ServiceAccount (M2M) identity. Applied to both the umbrella `agentic-platform` and the `agentic-platform-connectivity` `values.yaml` (kept in sync; the umbrella copy is the one deployed in meta-package installs).
- `agents.definitions.sre-agent`: applied the eval-tuned muster `systemMessage` to the **umbrella** `agentic-platform` chart's `values.yaml` as well. The umbrella forwards its own values to the `agentic-platform-connectivity` HelmRelease via `forwardAllValues`, so in meta-package installs (every real deployment, e.g. glean) the umbrella's copy shadows the connectivity sub-chart default — without this the previous change had no effect on deployed clusters. The two `values.yaml` files now carry a reciprocal keep-in-sync note.
- `agents.definitions.sre-agent`: list muster's meta-tools explicitly under `toolNames` (was empty = all) so the kagent Agent Details UI renders them by name instead of a single "Unknown Tool" entry. The agent's available tools are unchanged — backend tools (`x_kubernetes_*`, `x_prometheus_*`, workflows) are still reached through `call_tool`. Applied to both the umbrella `agentic-platform` and `agentic-platform-connectivity` `values.yaml` (kept in sync).
- `agents.definitions.sre-agent`: the default `systemMessage` now carries the muster meta-tool contract that was eval-tuned for the Backstage devportal AI chat ([giantswarm/backstage#1775](https://github.com/giantswarm/backstage/issues/1775)) — prefer a single-call `workflow_<name>` (discovered via `filter_tools(query=…)`), the exact `x_kubernetes_*` argument contract (`management_cluster: <mc>-mcp-kubernetes`, `resourceType`, `podName`, `tailLines`), and the cheap-listing recipe (`summary`, `fieldSelector: status.phase!=Running`, `reason=BackOff` for CrashLoopBackOff). The agent shares muster's meta-tool interface with the devportal chat, so the same guidance reduces wrong-arg retries and avoidable tool round trips. Clusters that enable the agent without overriding `systemMessage` (e.g. glean) inherit it.
- `agentic-platform-connectivity`: the agentgateway data-plane container now sets `ephemeral-storage` requests/limits (new `gateway.parameters.dataPlaneResources` value, merged onto the generated proxy container via `AgentgatewayParameters`). The agentgateway controller injects a writable `/tmp` emptyDir (readOnlyRootFilesystem) without a `sizeLimit`, which tripped the `require-emptydir-requests-and-limits` Kyverno policy; declaring ephemeral-storage on the mounting container clears the audit warning. Part of the namespace-wide Kyverno cleanup (giantswarm/giantswarm#36885).
- The bundled `valkey` (`muster-valkey`) component now documents that its RDB-only persistence is a deliberate cache-only choice and warns against enabling AOF to "fix" data loss: AOF lives on the same PVC so it does not survive PVC loss/recreation (the failure mode that actually occurs), and flipping `appendonly` in config + restart is a data-loss footgun (Valkey loads the empty AOF dir and ignores the RDB). Comment-only; no behaviour change. See [giantswarm/muster#884](https://github.com/giantswarm/muster/issues/884).
- `agentic-platform-connectivity`: the kagent controller `NetworkPolicy` (both `cilium` and `kubernetes` flavors) scopes intra-namespace ingress to agent pods (`app: kagent`) and the kagent UI on the controller API port (8083), instead of allowing every pod in the kagent namespace on all ports.
- `agentic-platform-connectivity`: kagent agent pods may egress to the cluster Envoy LB VIP on 443/10443, so the STS plugin can reach muster's OAuth token endpoint (served on the public ingress hostname, which resolves to a private cluster VIP) for on-behalf-of token exchange.
- **App-owned CRDs, release B + bundle retirement** (completes the staged migration begun in release A). `agentgateway`, `kagent`, and `agent-sandbox` drop their `dependsOn: [agentic-platform-crds]` (they already ship their CRDs with `crds: CreateReplace`). The CR consumers repoint to the CRD-owning components: `agentic-platform-mcps` now `dependsOn: [muster, agentgateway]` and `agentic-platform-connectivity` now `dependsOn: [agentgateway, kagent]` (both were `agentic-platform-crds`). The handoff is non-destructive: release A had already overwritten the live agentgateway/kagent/kmcp CRDs with the wrapper charts' `helm.sh/resource-policy: keep` copies, so Flux pruning the retired bundle `HelmRelease` cannot delete the CRDs or cascade to any CR.
- The meta-package's generic component loop now **drops a `dependsOn` reference to a component that is toggled off** (new `agentic-platform.componentEnabled` helper). With app-owned CRDs a CR consumer dependsOn the opt-in component that ships the CRD it needs; in the default `muster-direct` topology `agentgateway`/`kagent` are off and render no `HelmRelease`, so without this filtering the always-on `agentic-platform-connectivity` release would block forever on a dependency that was never rendered.

### Added

- `agents.sreAgent`: opt-in bundled Declarative kagent `Agent` (`sre-agent`) that reaches muster machine-to-machine through the static `muster` RemoteMCPServer (SA Bearer token from the `<name>-kagent-muster-token` Secret). `spec.declarative.deployment.serviceAccountName` is omitted so the kagent controller auto-creates the `sre-agent` SA. Configurable `modelConfig` (default `default-model-config`), `systemMessage`, `toolServer` (default `muster`), `toolNames`, `allowedHeaders`, `deployment.resources`, and `deployment.env`. `allowedHeaders` is inert: forwarding a request header onto muster tool calls additionally needs kagent-dev/kagent#2044, absent from the pinned kagent 0.9.9. Disabled by default.
- `agents.sreAgent.m2m`: opt-in impersonation RBAC for the agent's M2M identity. `m2m.impersonators` (list of `{name, namespace}` SAs, default `mcp-kubernetes`) get a `Role`/`ClusterRole` allowing them to impersonate `m2m.granted`. A `system:serviceaccount:…` `granted.user` renders a namespaced `serviceaccounts` Role + groups `ClusterRole`; a plain username renders a single `users`+`groups` ClusterRole. `m2m.granted.clusterRoles` (default `[]`) binds the granted groups to the listed ClusterRoles (the agent's K8s authz, e.g. `read-all`), one ClusterRoleBinding each. The matching muster `workloadGroupGrant` and mcp-kubernetes `impersonateUser`/`impersonateGroups` are configured per cluster and must agree with `m2m.granted`. Disabled by default.

### Removed

- `agents.muster.userServer` and the header-less `muster-user` RemoteMCPServer. On the pinned kagent 0.9.9 it lists zero tools (no static `Authorization` for controller discovery) and the forwarded-token override it depended on (kagent-dev/kagent#2044) is absent, so it could not impersonate the end user. User impersonation returns once kagent ships #2044 and muster carries the human identity (`email`/`groups`).

### Changed

- muster CRDs (`MCPServer` / `Workflow`) now follow the **app-owned CRDs** pattern instead of riding the `agentic-platform-crds` bundle. The meta-package's generic component loop gains an optional `crds` policy field (rendered into the child `HelmRelease`'s `spec.install.crds` / `spec.upgrade.crds`); the `muster` component sets `crds: CreateReplace` and no longer `dependsOn` the bundle, so its CRDs travel with the muster app chart at the same resolved version and upgrade atomically on every release (Flux `CreateReplace` overcomes Helm's "never upgrades `crds/`-dir CRDs" limitation). `agentic-platform-mcps` now `dependsOn: [muster, agentic-platform-crds]` (its `MCPServer` CRs need the muster-owned CRD; its agentgateway CRs still need the bundle). The `agentic-platform-crds` bundle drops its `muster-crds` dependency (it still ships agentgateway / kagent / agent-sandbox CRDs). The handoff is non-destructive: the live muster CRDs carry `helm.sh/resource-policy: keep`, so dropping the bundle dependency does not delete them — the muster release adopts and upgrades them on its next reconcile. Requires a muster app chart that ships its CRDs in `crds/` (tracked separately).

- **App-owned CRDs, release A** for the three remaining bundle components. `agentgateway` and `kagent` are repointed from their upstream chart repositories to the Giant Swarm wrapper charts (`oci://gsoci.azurecr.io/charts/giantswarm/{agentgateway,kagent}`, from `giantswarm/agentgateway` and `giantswarm/kagent-app`) that vendor the upstream chart as a subchart and ship the `agentgateway.dev` / `kagent.dev` + `kmcp` CRDs in their own `crds/` dir with `helm.sh/resource-policy: keep`. `agent-sandbox` already pointed at its GS chart. All three now set `crds: CreateReplace`. This is the **non-destructive first step** of the staged handoff: every component KEEPS its `dependsOn: [agentic-platform-crds]` and the bundle keeps shipping the CRDs, so `CreateReplace` overwrites the live (un-annotated) CRDs with the `keep`-annotated copies before any bundle dependency is dropped (release B). The meta-package's generic component loop gains an optional `valuesKey` field: the `agentgateway` / `kagent` blocks are nested under the subchart key when forwarded to the wrapper chart, leaving the flat top-level blocks (and every per-cluster override path and the connectivity chart's reads of them) unchanged. Wrapping the upstream kagent chart as a subchart keeps its `.Chart.Version` a clean `0.9.9`, so the `app.kubernetes.io/version` label is no longer corrupted by the OCI `+digest` — the `kagent` version pin (`0.9.9` -> `0.x`) and the `postRenderers` kustomize label-sanitization hack are both removed.

- Bump `muster` sub-chart `0.8.4` -> `0.9.0`, which adds a cheap, ranked, faceted tool-discovery tier (giantswarm/muster#868): `filter_tools` is now a discovery tier distinct from execution, so finding a tool no longer scales with the full descriptive weight of every candidate. It gains `limit`/`offset` (default page 25) returning `total` + `truncated`, defaults to a one-line `summary` per tool with no input schema (full description/schema stay available via `describe_tool` or `include_schema=true`), adds a BM25-ranked `query` mode that returns matches best-first with a `score`, and exposes `Workflow` CRD `metadata.labels` as a filterable `labels` facet. Against a ~280-workflow fleet a broad `filter_tools(pattern="*workflow*")` call drops from a ~330 KB full-catalogue dump to a ~3 KB summary page (~100x smaller). The companion `muster-crds` / `agentic-platform-crds` chart is intentionally NOT bumped: #868 propagates existing Kubernetes labels onto tool metadata and changes no CRD schema, so muster 0.9.0 runs against the existing 0.8.4 CRDs.

- Bump `muster` (and `muster-crds`) sub-chart `0.7.5` -> `0.8.4`, which adds workflow control flow (giantswarm/muster#865): `forEach` (sequential loops over a list), `parallel` (concurrent sub-step groups with isolated contexts), workflow-level `onFailure` (best-effort cleanup/rollback), and `condition.template` (boolean Go-template step gates). The `muster-crds` chart carries the matching `Workflow` CRD schema (new `spec.steps[].forEach`/`parallel`, `spec.onFailure`, `condition.template`; the dead `step.outputs` field is removed in favour of `store: true` + `{{ .results.<step_id> }}`), so the CRDs chart must be upgraded alongside the app chart. Also fixes camelCase field-name parsing (`allowFailure`/`fromStep`/`expectNot`/`jsonPath`) in the structured `workflow_create`/`update`/`validate` tool path. The `0.8.4` bump (over `0.8.0`) carries giantswarm/muster#871: the `0.8.0` `Workflow` CRD was rejected by the Kubernetes API server on k8s >= 1.34 because its "exactly one of" CEL guards used a `[...].filter(x, x).size() == 1` form whose estimated cost exceeded the schema-wide validation budget (3.2x over), which made `agentic-platform-crds` fail to install/upgrade and roll back. Rewriting the guards to the cheap additive form fixes the install.

- klausgateway: the agentgateway HTTPRoute now includes `/channels/slack` when `klausGateway.slack.enabled` is true, exposing the Slack Events API webhook endpoint through the data-plane.

### Fixed

- agent-sandbox: the controller Deployment install no longer loops on Giant Swarm
  clusters that enforce restricted-PSS via Kyverno (fail-closed). The Kyverno mutate
  policy that injects the controller's `securityContext` ships in the same Helm
  release as the Deployment, so Kyverno could not load it before the Deployment was
  admitted — admission was denied, the release rolled back (deleting the policy too),
  and Flux retried indefinitely. The policy now carries `helm.sh/resource-policy: keep`
  so it survives the first-install rollback; Kyverno then loads it and the next upgrade
  retry re-applies the (now-mutated) Deployment, which passes admission.

### Changed

- Bump `muster` sub-chart `0.5.6` -> `0.5.7`, which bumps `mcp-oauth` to `v0.4.2`, making the token-exchange broker's trusted-issuer JWKS cache rotation-safe: it now refetches the issuer JWKS when it encounters an unknown `kid` (giantswarm/muster#847). Without this, a routine Dex signing-key rotation left the broker serving a pre-rotation JWKS and rejecting **every** current user token with `subject_token_validation_failed` until muster was restarted — a fleet-wide devportal logout.

- Bump `muster` sub-chart `0.5.5` -> `0.5.6`, which bumps `mcp-oauth` to `v0.4.1`. The token-exchange broker and MCP OAuth client now RFC 6749-encode client credentials before HTTP Basic auth, so token-exchange client secrets containing `+` (and other reserved characters) no longer fail Dex client authentication with `invalid_client`. Unblocks the tunnelport private-cluster devportal rollout (giantswarm#36880) for clusters whose `muster-token-exchange-<mc>` secret contains `+` without per-cluster secret rotation.

### Fixed

- `agentic-platform-kagent-agent-muster-egress` CNP was missing an egress rule for kagent agent pods to reach the kagent-controller on port 8083. Agent pods had no path to dial into the controller, blocking agent-to-controller communication.

- muster token hook Job (`kagent-muster-token-init`): no longer depends on a shell in the `kubectl` image, which is distroless (`registry.k8s.io/kubectl`) and crash-looped `BackoffLimitExceeded` on `/bin/sh: no such file or directory`, failing post-upgrade. The token is now minted via a projected `serviceAccountToken` volume, the `Bearer <token>` Secret manifest is rendered by a busybox init container, and `kubectl` is invoked with args only to apply it. The Job runs as `kagent-muster-client` (the identity muster trusts); the `serviceaccounts/token` create RBAC is dropped.

### Changed

- `agents.muster`: replaced `tokenDuration` (Go duration) with `tokenExpirationSeconds` (integer, fed to the projected token volume); added `busyboxImage`.

- `klausGateway.a2a.url` now routes through the agentgateway data-plane Service (`http://agentgateway.agentic-platform.svc.cluster.local:8080/kagent/api/a2a/kagent`) instead of hitting `kagent-controller:8083` directly, so A2A egress is authenticated and observed by agentgateway. Requires a klaus-gateway release that forwards the caller's bearer token.

### Added

- `templates/klausgateway/netpol.yaml`: NetworkPolicy (cilium + kubernetes flavors) allowing klaus-gateway egress to the agentgateway data-plane Gateway on port 8080, rendered when `klausGateway.enabled` and `klausGateway.a2a.enabled`.

- Bundle the [agent-sandbox](https://github.com/kubernetes-sigs/agent-sandbox) controller
  (opt-in via `agentSandbox.enabled`, default off) and its CRDs (Sandbox / SandboxTemplate /
  SandboxClaim / SandboxWarmPool, shipped via `agentic-platform-crds`, keep-protected). This
  is the Sandbox runtime kagent's `SandboxAgent` delegates pod isolation to — `agentic-platform-crds`
  already shipped the `SandboxAgent` CRD, but nothing installed the controller it requires.
  Restricted-PSS securityContext is injected into the controller Deployment via an umbrella
  Kyverno mutate policy, since the vendored upstream chart exposes no securityContext knob.
  A render-time guard (`templates/agent-sandbox/validate.yaml`) fails the install early if
  `agentSandbox.podSecurity.namespace` drifts from the controller's actual namespace, which
  would otherwise leave the Kyverno policy silently targeting the wrong namespace.

### Security

- The kagent muster M2M token now defaults to a 1-hour TTL (`agents.muster.tokenExpirationSeconds: 3600`, was 365 days) and is re-minted by a new `muster-token-refresh` CronJob on `agents.muster.tokenRefreshSchedule` (default every 15 minutes). The bootstrap Job still seeds the Secret on install/upgrade; kagent's RemoteMCPServer picks up the rotated Secret without restarting the agent. Removes the year-long at-rest bearer token.

## [1.1.33] - 2026-06-16

### Changed

- Bump `agentic-platform-mcps` sub-chart `0.3.0` -> `0.4.0`, which adds
  `identityProviders.<name>.expectedIssuer` (rendered into the muster MCPServer
  `tokenExchange.expectedIssuer`). Required to repoint tunneled MCP servers at
  tunnelport `:8443` in-cluster Services, where the exchanged token's `iss` stays
  the public Dex issuer and must be pinned (giantswarm#36883).

## [1.1.32] - 2026-06-16

### Fixed

- muster token hook Job (`kagent-muster-token-init`): no longer depends on a shell in the `kubectl` image, which is distroless (`registry.k8s.io/kubectl`) and crash-looped `BackoffLimitExceeded` on `/bin/sh: no such file or directory`, failing post-upgrade. The token is now minted via a projected `serviceAccountToken` volume, the `Bearer <token>` Secret manifest is rendered by a busybox init container, and `kubectl` is invoked with args only to apply it. The Job runs as `kagent-muster-client` (the identity muster trusts); the `serviceaccounts/token` create RBAC is dropped.

### Changed

- `agents.muster`: replaced `tokenDuration` (Go duration) with `tokenExpirationSeconds` (integer, fed to the projected token volume); added `busyboxImage`.

## [1.1.31] - 2026-06-16

### Fixed

- `networkPolicy.musterInClusterMcpPorts`: add `8443` to the default so muster's supplementary
  egress CNP permits reaching tunnelport RemoteApp tunnels (ghostunnel terminates TLS on `8443`).
  Without it, muster's connection to in-cluster tunnel backends
  (e.g. `mcp-kubernetes-garm.agentic-platform.svc:8443`) was silently dropped by Cilium and timed
  out (`context deadline exceeded`).

## [1.1.30] - 2026-06-16

### Changed

- Bump `klaus-gateway` subchart to `0.1.5`.

## [1.1.29] - 2026-06-15

### Fixed

- `klausGateway.a2a.saToken` and `klausGateway.upstream`: set `additionalProperties: true` in the
  values schema so the klaus-gateway subchart's own pass-through keys
  (`a2a.saToken.{expirationSeconds,mountPath}`, `upstream.url`) validate. These nested objects were
  still `additionalProperties: false` after the `1.1.27` `a2a` fix, leaving the `agentic-platform`
  HelmRelease stuck `UpgradeFailed` on management clusters whose rendered config supplies them.

## [1.1.28] - 2026-06-15

### Changed

- Bump `klaus-gateway` subchart from `0.1.1` to `0.1.2`.

## [1.1.27] - 2026-06-15

### Fixed

- `klausGateway.a2a`: set `additionalProperties: true` in the values schema so the klaus-gateway
  subchart's own a2a keys (`url`, `saToken`, …) pass through validation. This was missed when
  `klausGateway.routing`/`lifecycle` were widened, leaving the `agentic-platform` HelmRelease stuck
  `UpgradeFailed` on management clusters whose rendered config supplies `klausGateway.a2a.{url,saToken}`.
- `kagent.controllerRoute`: add outer public HTTPRoute (`kagent-controller-public`) on the Envoy
  Gateway so the kagent A2A endpoint is reachable at `https://<hostname>/kagent/...`. The inner
  `kagent-controller` route (agentgateway data plane) was already present, but the missing outer
  route caused Envoy to return 404 for all requests to the hostname before they reached the
  agentgateway pod. Configurable via `kagent.controllerRoute.parentRef` (defaults to
  `giantswarm-default` / `envoy-gateway-system`).
- `AgentgatewayBackend/kagent`: add `spec.policies.auth.passthrough: {}` so the validated muster JWT
  is forwarded to kagent-controller. The agentgateway JWT filter strips the Authorization header
  after validation; without passthrough the controller's `AUTH_MODE=trusted-proxy` receives no
  header and returns 401 for every authenticated request.
- `helm.sh/chart` label: truncating long dev-build versions at 63 characters could leave a trailing `.`, producing an invalid label value and failing the Helm install/upgrade. The `chart` helper now trims trailing `.` as well as `-`.
- `templates/kagent/agents/remotemcpservers.yaml`: `headersFrom[].valueFrom` used a `secretKeyRef` block, but the kagent v1alpha2 `ValueSource` schema is flat (`type`/`name`/`key` with `type: Secret`). The CRs failed admission with `valueFrom.key: Required value`.
- `templates/kagent/agents/muster-token-job.yaml`: mint the muster token via the TokenRequest API (`kubectl create token`, duration `agents.muster.tokenDuration`, default 8760h) instead of a legacy `kubernetes.io/service-account-token` Secret. Legacy Secret tokens carry `iss: kubernetes/serviceaccount` and no `exp`, so muster's `trustedIssuers` JWT validation (cluster OIDC issuer + required expiry) can never accept them; every RemoteMCPServer call failed with `Unauthorized`. The legacy token Secret is removed; the token now rotates on every install/upgrade.
- `templates/kagent/agents/muster-sa.yaml`: the token-init Role granted `create` on Secrets restricted by `resourceNames`, which Kubernetes RBAC never matches for create requests (the object name is not part of the authorization attributes). The hook Job failed with `secrets is forbidden` on first install. `create` is now an unrestricted (namespace-scoped) rule; `get`/`patch` stay name-restricted.
- `templates/kagent/declarative-agent-pod-security.yaml`: remove `seccompProfile: RuntimeDefault` from the injected pod and container security contexts. `RuntimeDefault` blocks `clone(CLONE_NEWUSER)` which bwrap (used by srt internally) requires for user-namespace isolation, causing every shell/bash tool invocation to fail with `bwrap: No permissions to create a new namespace`.
- `templates/kagent/policy-exception.yaml` (new): `PolicyException` in the `policy-exceptions` namespace exempting pods and deployments labelled `app: kagent` in the kagent namespace from the cluster-wide `restrict-seccomp-strict` Kyverno policy. Required because the cluster enforces seccomp profiles and we cannot simply omit `seccompProfile` without an exception.
- `templates/kagent/declarative-agent-srt-settings.yaml` (new): Kyverno policy that patches the `srt-settings.json` key in agent config Secrets (labelled `app: kagent`) on admission to include `"enableWeakerNestedSandbox": true`. Without this flag, bwrap attempts to bind-mount `/proc` into its new PID namespace, which fails inside unprivileged containers with `bwrap: Can't mount proc on /newroot/proc: Operation not permitted`. The flag is the documented srt workaround for container environments (`sandbox/linux-sandbox-utils.ts`); the kagent controller does not expose it through any CR field.
- `templates/kagent/declarative-agent-pod-security.yaml`: extend the security context mutation to `SandboxAgent` CRs. `SandboxAgent` is a distinct kind from `Agent` and was not covered by the existing rule; pods created via the `agents.x-k8s.io` Sandbox backend were blocked by `disallow-capabilities-strict`, `disallow-privilege-escalation`, and `require-run-as-nonroot` Kyverno policies.

## [1.1.26] - 2026-06-12

### Fixed

- Update muster to 0.4.1 (mcp-oauth v0.3.1): forwarded ID tokens (`trustedAudiences`) are no longer hard-rejected when the same issuer is also configured in `trustedIssuers` for the token-exchange broker — fixes Backstage AI-chat SSO forwarding returning 401 behind the agentgateway.

## [1.1.25] - 2026-06-11

### Fixed

- `gateway.jwksEgress` now also opens egress on the agentgateway **controller** network policy
  (Cilium and kubernetes flavors). The controller fetches remote JWKS centrally and distributes
  keys to the data plane via xDS, so the data-plane-only rule left JWKS fetches timing out
  (e.g. Dex on giantswarm/dex:5556 for extra JWT providers).

## [1.1.24] - 2026-06-11

### Changed

- Update agentic-platform-mcps to v0.3.0: new `agentgateway.jwt.extraProviders` value lets the
  inbound agentgateway JWT policy accept tokens from additional issuers (e.g. Dex-issued ID tokens
  forwarded by Backstage AI chat alongside muster-issued JWTs, giantswarm#36840). Also fixes the
  `identityProviders` values schema that rejected every populated provider map.

### Fixed

- `templates/kagent/declarative-agent-pod-security.yaml`: retarget the Kyverno mutate from `Deployment` (controller output) to `Agent` CR (controller input). The previous policy patched the Deployment after the kagent controller had already stamped `privileged: true` on the git-skills path, causing the API server to reject the Deployment as self-contradictory (`privileged: true` + `allowPrivilegeEscalation: false`). Mutating the Agent CR instead sets `allowPrivilegeEscalation: false` on the controller input, which trips the controller's own guard and prevents `privileged: true` from being set in the first place. A `(type): "Declarative"` condition anchor scopes the mutation to Declarative agents only.

## [1.1.23] - 2026-06-11

### Changed

- Update muster and muster-crds to v0.4.0 (via v0.3.14): muster now supports brokered RFC 8693 token exchange (giantswarm/muster#831) — external confidential clients can exchange a trusted-issuer subject token plus an `audience` parameter at `/oauth/token` for a token minted by the audience's downstream Dex. New `muster.oauth.server.tokenExchangeBroker` values block (per-client audience allowlist, audience → downstream Dex target mapping); mcp-oauth bumped to v0.3.0. Inert by default.

## [1.1.22] - 2026-06-11

### Changed

- Update muster and muster-crds to v0.3.13. Muster's OAuth server (mcp-oauth v0.2.199) now defaults the JWT access token `aud` claim to its resource identifier when a client omits the RFC 8707 `resource` parameter, so tokens from generic OAuth clients (e.g. Backstage auth providers) pass the agentgateway's strict JWT audience validation on `/mcp` instead of failing with `401 InvalidAudience`.

## [1.1.21] - 2026-06-10

### Fixed

- Downgrade bundled agentgateway and agentgateway-crds charts from `v2.2.1` back to `v1.2.1`. The upstream project released `v2.2.x` before resetting semver to `v1.0.0`; `v2.2.1` is older than `v1.0.0` and was incorrectly treated as an upgrade (agentgateway/agentgateway#1249). Block the bogus `v2.2.x` range in `renovate.json5`.

- `templates/agentgateway/agentgatewayparameters.yaml`: data-plane image override moved from the deployment container spec to `spec.image.registry/repository/tag`. Drops `AGW_XDS_SERVICE_NAME` from `controller.extraEnv` and removes the explicit controller image tag pin: the v1.2.1 chart already sets `AGW_XDS_SERVICE_NAME: agentgateway-controller` correctly via `fullnameOverride`, and the controller image tag defaults to the chart's `appVersion` (`v1.2.1`) when unset.

- `templates/kagent/ui-httproute.yaml`: oauth2-proxy backend name now resolves `oauth2-proxy.fullnameOverride` from values (falling back to `<release>-oauth2-proxy`), so the `HTTPRoute` points at the correct service when `fullnameOverride: kagent-oauth2-proxy` is set.

- Bundled kagent declarative agents (`cilium-policy-agent`, `promql-agent`, and all others) now deploy into the `kagent` namespace. The kagent `kagent.namespace` helper resolves `namespaceOverride` from the subchart's own `.Values` scope; the parent chart's `kagent.namespaceOverride: kagent` is not visible there, so agents were landing in the Helm release namespace (`agentic-platform`). Both `default-model-config` (ModelConfig) and `kagent-tool-server` (RemoteMCPServer) live in `kagent` and are resolved by bare name within the agent's own namespace, causing every agent to show `Accepted=False`. Each bundled agent subchart block now sets `namespaceOverride: kagent` explicitly.

- `templates/kagent/ui-backendtrafficpolicy.yaml`: route-level `BackendTrafficPolicy` for the kagent UI `HTTPRoute`. The cluster-wide `gateway-giantswarm-default-error-pages` policy replaces all 4xx/5xx bodies with static HTML; without a route-level override, Envoy replaces oauth2-proxy's 403 sign-in page body (which meta-refreshes to `/login`) with that static page, breaking the login flow entirely. Any route-level `BackendTrafficPolicy` overrides the gateway-wide one, so this policy is rendered with `enabled: true` by default whenever `kagent.uiRoute.enabled: true`.

### Added

- `klausGateway.enabled` (default `false`) adds `giantswarm/klaus-gateway` as an opt-in conditional dependency (`condition: klausGateway.enabled`, alias `klausGateway`). With the flag unset or false the rendered output is byte-identical to the previous chart version. When enabled, the sub-chart installs the Klaus Gateway Deployment, Service, RBAC, and ChannelRoute CRD (via `crd.install: true`). The sub-chart's own agentgateway dependency is disabled (`klausGateway.agentgateway.enabled: false`) so the umbrella's bundled agentgateway is reused.
- Single `ingress.mode` topology selector (`muster-direct` | `agentgateway-muster` | `agentgateway-direct`) that declares the whole request topology in one place. The umbrella now owns **both** public routes — muster's `/` catch-all (new `templates/ingress/muster-httproute.yaml`, rendered in all modes) and the agentgateway `/mcp` interception route — fed from a single shared `ingress.parentRefs` / `ingress.hostnames`, so the two routes can no longer drift. A template-time guard (`templates/validate.yaml`) fails fast on an invalid mode, on `ingress.parentRefs` empty in **any** mode (the umbrella-owned muster `/` route attaches to it — an empty `parentRefs` would otherwise render a route bound to no Gateway), and on `agentgateway.enabled` / `agentic-platform-mcps.agentgateway.viaMuster` disagreeing with the mode.
- `agentgateway.enabled` (default `false`) gates the agentgateway controller dependency via `condition: agentgateway.enabled` in `Chart.yaml`. In the default `muster-direct` mode the controller, its `GatewayClass`, the data-plane `Gateway`/`AgentgatewayParameters`, and the data-plane NetworkPolicies are **not installed**.
- `agentgateway-direct` mode is modelled but **fail-guarded** — install is blocked with a clear message until a DCR-capable IdP (RFC 7591/8707) lands.
- `make verify-modes` target (wired into a new CircleCI branch test job) asserts the fail-guards fire; `ci/test-full-stack-values.yaml` now exercises the previously-untested `agentgateway-muster` path.
- Route-scoped `BackendTrafficPolicy` for muster's `/` route (new `templates/ingress/muster-backendtrafficpolicy.yaml`), rendered in **all** modes when `ingress.backendTrafficPolicy.enabled` is set — not just the agentgateway `/mcp` route. This preserves muster's `401 … WWW-Authenticate` challenge against the cluster-wide error-pages policy in `muster-direct` mode (where muster serves `/mcp` directly) and restores the pre-refactor `muster.gatewayAPI.backendTrafficPolicy` behavior on muster's own route.
- Per-route `ingress.httpRoute.muster.{annotations,labels}` and `ingress.httpRoute.mcp.{annotations,labels}` overrides, merged on top of the shared `ingress.httpRoute.{annotations,labels}` (per-route keys win on collision). Lets a downstream diverge one route — e.g. a different cert-manager issuer or Envoy route policy per route — without forking the shared block.
- `kagent-crds` (`v0.9.5`, `oci://ghcr.io/kagent-dev/kagent/helm`) bundled as a sub-chart. Installs the kagent CRDs (`Agent`, `AgentHarness`, `ModelConfig`, `MCPServer`, `RemoteMCPServer`, `Memory`, `ToolServer`, `SandboxAgent`). Must be installed before the `agentic-platform` chart when `kagent.enabled: true`. Note: upstream does not mark these CRDs `helm.sh/resource-policy: keep`; `helm uninstall agentic-platform-crds` will remove them and cascade to all kagent CRs.
- `kagent` (`v0.9.5`, `oci://ghcr.io/kagent-dev/kagent/helm`) bundled as a conditional sub-chart (`kagent.enabled`, default `false`). All kagent resources land in `kagent.namespaceOverride` (default `kagent`) so they stay separate from the umbrella's release namespace. The `kagent-crds` chart is added to `agentic-platform-crds` as a prerequisite. Enabling kagent requires `agentic-platform-crds` to be installed first.
- `kagent.oauth2-proxy.metrics.serviceMonitor.enabled: true` — Prometheus `ServiceMonitor` for the oauth2-proxy metrics endpoint (`:44180`), labelled `observability.giantswarm.io/tenant: giantswarm`.
- `templates/kagent/netpol.yaml`: oauth2-proxy ingress CNP extended to allow scraping of the metrics port (`:44180`) from any cluster-entity source, and oauth2-proxy egress extended to include `cluster` alongside `world` on port 443 — required when the Dex hostname resolves to an internal LB VIP (private-range IP classified as `cluster` by Cilium, not `world`).
- `postgres` block: opt-in CloudNativePG `Cluster` CR (`postgres.enabled`, default `false`) provisioning the kagent application database in a named `kagent` schema (not `public`). Supports pgvector via `postInitTemplateSQL` (any CNPG version, bundled image) or the ImageVolume approach (`postgres.vector.extensionImage.reference`, CNPG 1.29+/PG18). The CNPG operator and its CRDs remain a cluster-level prerequisite. An optional Klaus sessions database (`postgres.sessionsDatabase.enabled`, default `false`) is templated but left off pending the core-runtime persistence decision.
- `templates/namespace.yaml`: renders the `kagent` `Namespace` when `kagent.namespaceOverride` differs from the release namespace, so fresh installs do not require manual namespace pre-creation.
- `templates/kagent/controller-route.yaml`: opt-in `AgentgatewayBackend` + `HTTPRoute` (`kagent.controllerRoute.enabled`) exposing the kagent controller API through agentgateway with JWT validation.
- `templates/kagent/netpol.yaml`: cross-namespace network policies for kagent (cilium and kubernetes flavors, gated on `networkPolicy.flavor`). Cilium: egress from agentgateway data-plane to kagent controller (port 8083) + ingress policy in the kagent namespace. Kubernetes: `NetworkPolicy` restricting kagent controller ingress to the release and kagent namespaces, preventing direct access that would bypass agentgateway JWT validation.
- `templates/kagent/ui-httproute.yaml`: opt-in HTTPRoute (`kagent.uiRoute.enabled`) exposing the kagent UI on the public Gateway. When `oauth2-proxy.enabled: true` routes through oauth2-proxy (port 4180); otherwise routes directly to the UI (dev only). Placed in the kagent namespace to avoid cross-namespace backend refs.
- `ci/test-postgres-values.yaml`: CI values file exercising the kagent+postgres path through `helm template`/lint.
- `ci/test-kagent-routing-values.yaml`: CI values file exercising controllerRoute + uiRoute + oauth2-proxy.
- Kagent defaults hardened for GS clusters: restricted-PSS `securityContext` applied at umbrella level (Kyverno requirement); bundled agents/tools disabled with comments explaining why and under what conditions to re-enable; Anthropic set as the default model provider (`claude-sonnet-4-6`); OTel traces and logs routed to `otlp-gateway.kube-system.svc:4317`; `controller.auth.mode: trusted-proxy` (agentgateway validates the JWT upstream and the netpols fence the controller); `oauth2-proxy` values pre-wired for Dex OIDC integration (`enabled: false` until Dex client credentials are provided).

### Fixed

- In-cluster MCP backends that listen on a non-80/443 port (e.g. the bundled `pro` / `runbooks` MCP servers on `8080`, reached via a ClusterIP) were unreachable from muster — its sub-chart CNP permits cluster-entity egress only on 80/443, so those connections timed out and the corresponding `MCPServer`s went `Failed`. The umbrella now renders a supplementary `CiliumNetworkPolicy` (`<muster>-mcp-egress`) that widens muster's egress to `networkPolicy.musterInClusterMcpPorts` (default `[8080]`; cilium flavor only; Cilium policies are additive, so no sub-chart fork). Set to `[]` to disable.
- **Muster Service name is now pinned** via `muster.fullnameOverride: agentic-platform-muster`, read directly by the umbrella's `agentic-platform.musterFullname` helper instead of re-deriving the muster sub-chart's release-name algorithm. The route `backendRef`, the `BackendTrafficPolicy` target, and `agentic-platform-mcps.musterUrl` now reference one source of truth that stays in lockstep with the sub-chart's Service regardless of release name or any future muster naming change. A blank override fails the render loudly rather than silently pointing the route at a non-existent Service (503).

### Changed

- Bumped bundled `muster` to `0.2.6` (includes the muster#772 JWT signing-key wiring fix + `jwt_key.go` enabling edge JWT validation, and the CNP ingress-gateway egress fix from muster#788).
- Bumped bundled `agentic-platform-mcps` to `0.2.4` — corrects the `identityProviders` value schema (it was `additionalProperties: false` with no properties, forbidding every provider key and making `auth.mode: exchange` unconfigurable). Unblocks multi-cluster token-exchange consumers; `forward`-only installs are unaffected.

### Removed

- **Breaking:** `gateway.enabled`, `gateway.httpRoute.*`, and `gateway.backendTrafficPolicy.*` are removed — replaced by `ingress.mode` (topology switch), `ingress.parentRefs` / `ingress.hostnames` (shared route attachment), and `ingress.backendTrafficPolicy.*`. The retained `gateway.*` keys now hold data-plane *infrastructure* only and apply in `agentgateway-*` modes. `muster.gatewayAPI.enabled` is now `false` (the umbrella renders muster's public route) — set `ingress.parentRefs` / `ingress.hostnames` instead of `muster.gatewayAPI.httpRoute.*`.

## [0.5.0] - 2026-05-29

### Added

- `agentic-platform-mcps` bundled as a conditional sub-chart (`condition: mcps.enabled`, default `false`). It renders the platform's MCP server CRs (muster `MCPServer` and/or agentgateway `AgentgatewayBackend` + `AgentgatewayPolicy`) from one abstract `agentic-platform-mcps.mcpServers` list. The toggle lives in a separate top-level `mcps:` block because the sub-chart's strict `values.schema.json` (`additionalProperties: false`) rejects an `enabled` key. The CRs consume CRDs shipped by the companion `agentic-platform-crds` chart — the umbrella still installs **zero** CRDs. New `ci/test-mcps-values.yaml` exercises the path.

## [0.4.1] - 2026-05-28

### Fixed

- Extended restricted-PSS `securityContext` defaults to the bundled valkey **metrics exporter sidecar** (`valkey.valkey.metrics.exporter.securityContext`). 0.4.0 hardened the main valkey container and its init container, but the `redis_exporter` sidecar (`containers[1]`, port 9121) lives under a separate values key and was still rejected by Kyverno's `disallow-privilege-escalation` policy.

## [0.4.0] - 2026-05-28

### Fixed

- Restored restricted-PSS compliant `securityContext`/`podSecurityContext` defaults on the bundled valkey sub-chart, which were inadvertently dropped during the CRD-chart split (0.3.0). Without them, Kyverno's `disallow-privilege-escalation` and `restrict-seccomp-strict` policies reject the `muster-valkey` Deployment because the init container reuses the main container's `securityContext` and the upstream defaults omit `allowPrivilegeEscalation: false` and `seccompProfile: RuntimeDefault`.

## [0.3.0] - 2026-05-28

### Added

- New `agentic-platform-crds` chart (`helm/agentic-platform-crds/`): a CRD-only umbrella that vendors the upstream `agentgateway-crds` (`v1.2.1`) and `muster-crds` sub-charts, shipping all five CRDs the platform's CRs consume. Install it **before** `agentic-platform`; CRD and workload lifecycles are now decoupled (two releases in sequence, Flux/Argo-agnostic). The muster CRDs are `helm.sh/resource-policy: keep`-protected via `muster-crds.crds.annotations`.
- Second CircleCI `architect/push-to-app-catalog` job (`package-and-push-crds-chart`) building and publishing `agentic-platform-crds` alongside `agentic-platform` on git tags.

### Changed

- All CRDs moved out of `agentic-platform` into the new `agentic-platform-crds` chart. `agentic-platform` now installs **zero** CRDs — it renders only the CRs (`Gateway`, `AgentgatewayParameters`, plus muster's). `helm template agentic-platform` emitting any `CustomResourceDefinition` is a regression.
- `muster.crds.install` defaulted to `false` in `agentic-platform` values, so the bundled `muster` sub-chart renders no CRDs (the currently pinned muster `0.1.197` still defaults `crds.install: true`).
- `Chart.yaml` description: CRDs now ship in the companion `agentic-platform-crds` chart.

### Removed

- `agentgateway-crds` sub-chart dependency (`condition: agentgateway-crds.enabled`) and the `agentgateway-crds:` values block from `agentic-platform`. The CRDs are provided by `agentic-platform-crds`.

## [0.2.0] - 2026-05-27

### Added

- `agentgateway-crds` bundled as a conditional sub-chart (`condition: agentgateway-crds.enabled`, default `true`) so the umbrella is self-contained. The chart now ships the `AgentgatewayParameters` / `AgentgatewayPolicy` / `AgentgatewayBackend` CRDs alongside the `AgentgatewayParameters` CR it renders; Helm applies CRDs ahead of the CR via install ordering. Disable on clusters that manage these CRDs out of band.
- Agentgateway data-plane OTel env defaults: `OTEL_EXPORTER_OTLP_ENDPOINT=http://otlp-gateway.kube-system.svc:4317` and `OTEL_EXPORTER_OTLP_PROTOCOL=grpc` added to `gateway.parameters.dataPlaneEnv`. Override to point at a different backend.
- `muster.serviceMonitor.enabled: true` default. OTel push to otlp-gateway is not yet supported by muster; ServiceMonitor is the primary observability path until muster gains an `otlpEndpoint` knob.
- `ci/test-full-stack-values.yaml` CI values file satisfying all fail-guards for a complete `helm template` render (OAuth off, valkey off, parentRefs stubbed).

### Changed

- `agentgateway-crds` is no longer a cluster prerequisite (now bundled). Muster's CRDs continue to ship via the muster sub-chart. **Caveat:** the upstream `agentgateway-crds` CRDs are not yet annotated `helm.sh/resource-policy: keep`, so uninstalling the release deletes them and cascades to all agentgateway CRs cluster-wide. An upstream change to parameterize the CRD annotations is pending; the `keep` annotation will be added here once it lands.

## [0.1.0] - 2026-05-26

### Added

- Initial agentic-platform chart bundling `muster` 0.1.197 and `agentgateway` v1.2.1.
- `Gateway` (name `agentgateway`) and `AgentgatewayParameters` overlay injecting restricted-PSS `securityContext` on the controller-rendered data-plane pod.
- `gateway.parameters.serviceType` (default `ClusterIP`) overlays `AgentgatewayParameters.spec.service.type` so the data-plane Service stays internal; the controller hardcodes `LoadBalancer`.
- `gateway.parameters.dataPlaneEnv`, `dataPlaneVolumes`, `dataPlaneVolumeMounts` strategic-merge lists on the AgentgatewayParameters overlay.
- `CiliumNetworkPolicy` for the agentgateway **controller pod** in addition to the data-plane pod (upstream agentgateway chart ships no policies).
- `networking.k8s.io/v1 NetworkPolicy` rendering when `networkPolicy.flavor: kubernetes` — best-effort (no entity selectors, no FQDN egress).
- `networkPolicy.kubernetes.{apiServerCIDR,worldExcludedCIDRs}` for the `kubernetes` flavor.
- Top-level `extraObjects: []` rendering arbitrary manifests through `tpl` alongside the chart.
- `values.schema.json` covering top-level keys with a cross-field combo check (muster valkey storage requires `valkey.enabled` or an explicit URL).
- `UPGRADE.md` documenting the breaking changes for the first stable release.

### Changed

- CRD lifecycle: `agentgateway-crds` is a cluster prerequisite (upstream agentgateway ships controller + CRDs as separate charts). Muster's CRDs continue to ship inside the umbrella via the muster sub-chart's `templates/crds.yaml`.
- Data-plane policy selector switched to the Gateway-API standard label `gateway.networking.k8s.io/gateway-name=<gateway.name>` (was `app.kubernetes.io/name=agentgateway`, which matched both the controller and the data plane).
- Controller policy selector uses the agentgateway sub-chart's selector triple (`agentgateway: agentgateway` + `app.kubernetes.io/name=agentgateway` + `app.kubernetes.io/instance=<release>`).
- Data-plane CNP gains xDS egress to the controller on TCP 9978; controller CNP gains xDS ingress from data-plane pods.
- `CiliumNetworkPolicy` egress covers `kube-dns`, `coredns`, `k8s-dns-node-cache` on 53 + 1053 (UDP + TCP); world 80/443; cluster 80/443 for in-cluster ingress (Dex / MCPServers); muster on 8090.
- Muster sub-chart's NetworkPolicy values migrate from `ciliumNetworkPolicy.*` to `networkPolicy.{enabled,flavor,cilium.allowClusterIngress,kubernetes.*}` (muster 0.1.197). Umbrella overrides `enabled: true`, `flavor: cilium`, `cilium.allowClusterIngress: true`.
- `valkey.enabled` and `muster.muster.oauth.server.enabled` default to `true`. Operators must provide `oauth.server.baseUrl`, `oauth.server.dex.{issuerUrl,clientId}`, `oauth.server.existingSecret`, and `valkey.valkey.auth.usersExistingSecret` — muster's template-time fail-guards reject install otherwise.
- `muster.gatewayAPI.httpRoute.parentRefs` / `.hostnames` no longer default to the data-plane Gateway. Muster's HTTPRoute must attach to the cluster's public Gateway (typically `envoy-gateway-system/giantswarm-default`); the muster fail-guard enforces this.
- Bundled `giantswarm/valkey-app` 0.1.2 as a conditional sub-chart (`condition: valkey.enabled`). Single Deployment + PVC; Service at `muster-valkey.<namespace>.svc:6379`. ACL-based auth: a `default` user with full permissions reads its password from `valkey-password` in `valkey.valkey.auth.usersExistingSecret`.
- Muster wired to the bundled valkey by default: `muster.muster.oauth.server.storage.type=valkey` and `storage.valkey.url=muster-valkey:6379`. Inert while `oauth.server.enabled: false`; kicks in the moment OAuth is enabled.
- `networkPolicy.flavor` enum changed from `cilium | none` to `cilium | kubernetes`. Opt out via `networkPolicy.enabled: false`.
- README is now Flux HelmRelease-first (no Giant Swarm App platform). Adds a "Gateway API CR ownership" section clarifying that only `AgentgatewayParameters` is vendor-specific to agentgateway.
- Templates grouped under `templates/agentgateway/` (Gateway, AgentgatewayParameters, four NetworkPolicy variants).
- `Chart.yaml` description reflects the runtime contract (muster + agentgateway + opt-in Valkey; CRDs as cluster prerequisite). `appVersion` stays at the umbrella's own `0.1.0`.

### Removed

- `bootstrap.oauth.*` values and the `templates/oauth-bootstrap-secret.yaml` Helm `lookup`-based Secret generator. Use `extraObjects` to ship the Secret in the same release, or pre-create it out of band and reference via `muster.muster.oauth.server.existingSecret`.

[Unreleased]: https://github.com/giantswarm/agentic-platform/compare/v1.1.33...HEAD
[1.1.33]: https://github.com/giantswarm/agentic-platform/compare/v1.1.32...v1.1.33
[1.1.32]: https://github.com/giantswarm/agentic-platform/compare/v1.1.31...v1.1.32
[1.1.31]: https://github.com/giantswarm/agentic-platform/compare/v1.1.30...v1.1.31
[1.1.30]: https://github.com/giantswarm/agentic-platform/compare/v1.1.30...v1.1.30
[1.1.30]: https://github.com/giantswarm/agentic-platform/compare/v1.1.29...v1.1.30
[1.1.29]: https://github.com/giantswarm/agentic-platform/compare/v1.1.28...v1.1.29
[1.1.28]: https://github.com/giantswarm/agentic-platform/compare/v1.1.27...v1.1.28
[1.1.27]: https://github.com/giantswarm/agentic-platform/compare/v1.1.26...v1.1.27
[1.1.26]: https://github.com/giantswarm/agentic-platform/compare/v1.1.25...v1.1.26
[1.1.25]: https://github.com/giantswarm/agentic-platform/compare/v1.1.24...v1.1.25
[1.1.24]: https://github.com/giantswarm/agentic-platform/compare/v1.1.23...v1.1.24
[1.1.23]: https://github.com/giantswarm/agentic-platform/compare/v1.1.22...v1.1.23
[1.1.22]: https://github.com/giantswarm/agentic-platform/compare/v1.1.21...v1.1.22
[1.1.21]: https://github.com/giantswarm/agentic-platform/compare/v0.5.0...v1.1.21
[0.5.0]: https://github.com/giantswarm/agentic-platform/compare/v0.4.1...v0.5.0
[0.4.1]: https://github.com/giantswarm/agentic-platform/compare/v0.4.0...v0.4.1
[0.4.0]: https://github.com/giantswarm/agentic-platform/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/giantswarm/agentic-platform/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/giantswarm/agentic-platform/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/giantswarm/agentic-platform/releases/tag/v0.1.0
