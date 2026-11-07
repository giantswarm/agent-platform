# Organization credentials

Organization credential operations use MCP URL elicitation for nonsecret intent and authenticated HTTPS for values. The client owns its form and routes. Set `agent-manager.credentials.uiURL` explicitly to an HTTPS template with one `{operation}` placeholder; enabling Backstage does not derive a URL. The removed `agent-manager.devPortal` block is refused with a migration message.

Credential operations and `agent-manager.githubTokenExchange` need `components.agent-manager.versionRange` with a floor of `2.22.0-rc.3` or later. The organization switch retains its separate `2.20.0` floor for ClusterRole ownership. Run a compatible manager before enabling `agentPlatformOrganizations`, so the manager and connectivity charts do not both own `agent-platform-tenant-flux` during the handoff.

GitOps credential saves authenticate the person with the platform identity. Configure the manager's confidential RFC 8693 client to retrieve only the person's `https://github.com/apps/giantswarm-agent-manager` grant from muster, and disable `agent-manager.github.enabled`. Muster 5.58.0 provides the provider App and grant-release target mechanisms. Give the client a dedicated credential, a single target allowlist, the canonical Dex subject-token issuer and an explicit input-audience allowlist. Keep the provider App registered independently of the manager MCP registration so consent and refresh remain available. Do not enable the profile with independent identity issuers sharing raw-sub grant ownership.

Keep credential values in the HTTPS request only. Verify create, replacement, confirmation version checks and cross-user refusal with synthetic values before rollout. A commit receipt identifies a GitHub pull request opened as the person; verify Flux reconciliation separately. The UI and API contracts, authentication requirements and recovery rules are documented in [agent-manager](https://github.com/giantswarm/agent-manager/blob/main/docs/organization-credentials.md).

Configure organization GitOps writes through `agent-manager.tenants.commit`: `repository`, `branch`, `objectsPath` and `contentPrefix` use the released manager chart's contract. This block travels beside the derived `tenants.enabled`; it does not control the organization switch. Its repository must have the required SOPS policy and review ownership. The bundled Kubernetes MCP's per-server OAuth contract requires muster 5.59.0 or later on this chart line, even though grant release is available from 5.58.0.

The manager's network policy admits the configured `githubTokenExchange.endpoint` origin on its HTTPS port. Cilium gets an explicit DNS rule even when the broker and Dex share an ingress address. Native NetworkPolicy uses the public address exclusions for DNS origins; private broker ranges must be in `agentManager.networkPolicy.egress.cidrs` or `networkPolicy.additionalEgressCIDRs`. An IP literal is admitted as a single address. Verify a fresh pod can reach the broker before enabling the profile.

Legacy MCP 2025-11-25 elicitation retains session capabilities and completion streams in one manager process. Keep the released manager chart's single-replica default for that client profile:

```yaml
agent-manager:
  replicaCount: 1
```

This topology supports URL elicitation, HTTPS save, completion notification and retry through the public gateway. Multiple unpinned replicas lose negotiated capabilities and are unsupported for legacy clients. Shared credential-operation metadata does not share MCP sessions or completion streams; reconnect and initialize a new session after a manager restart. Use multiple replicas with legacy clients only after independently verifying session routing and recovery. MCP 2026-07-28 carries capabilities on each request and supports the tested two-replica credential flow.

For GitOps application, configure `agentPlatformOrganizations.fluxNamespace`, `fluxSource` and `fluxDecryptionSecret` so the operator creates each organization's Kustomization and restricted ServiceAccount. The bundled Flux engine needs `kustomize-controller` in `flux-engine.instance.components`; verify its image is available in the configured distribution registry. A synthetic GitHub fixture plus an OCI source proves encryption and Flux application separately from actual GitHub App consent, repository permissions and Git source access.
