# Organization credentials

agent-manager never takes a credential value in an MCP call. A missing or replaced credential is answered with an MCP URL elicitation of the credential form when the client declares URL elicitation, and with the form link in `credentials[].next` otherwise. The value is then posted to agent-manager over authenticated HTTPS. The API, authentication and recovery rules are in [agent-manager's documentation](https://github.com/giantswarm/agent-manager/blob/main/docs/organization-credentials.md).

## Credential form

Set `agent-manager.credentials.uiURL` to the HTTPS URL template of the client that serves the form, with one `{operation}` placeholder. This chart derives no form URL. `agent-manager.devPortal` is refused.

Credential operations and `agent-manager.githubTokenExchange` need a floor of `2.22.0-rc.3` or later in `components.agent-manager.versionRange`; a floor on an earlier release candidate is refused. The organization switch keeps its own `2.20.0` floor.

## GitOps writes

`agent-manager.tenants.commit` (`repository`, `branch`, `objectsPath`, `contentPrefix`) configures where organization changes are committed. It travels beside the derived `tenants.enabled` and does not control the organization switch. The repository needs a SOPS policy and review ownership.

A commit is opened as the person: agent-manager exchanges the person's platform identity for their `https://github.com/apps/giantswarm-agent-manager` grant at an RFC 8693 token endpoint (`agent-manager.githubTokenExchange`), with `agent-manager.github.enabled` off. Give its client a dedicated credential, that App as its only target, the canonical identity issuer and an explicit input-audience allowlist.

Configure `agentPlatformOrganizations.fluxNamespace`, `fluxSource` and `fluxDecryptionSecret` so the operator creates each organization's Kustomization and restricted ServiceAccount. The bundled Flux engine needs `kustomize-controller` in `flux-engine.instance.components`.

## Network policy

The manager's egress policy admits the origin of `githubTokenExchange.endpoint` on its port:
- Cilium: a rule for the host name, or for the address when the endpoint is an IP literal.
- Native NetworkPolicy: public addresses on that port, or the single address of an IP literal. A broker on a private network needs its range in `agentManager.networkPolicy.egress.cidrs` or `networkPolicy.additionalEgressCIDRs`.

## Replicas

A 2025-11-25 MCP client's session capabilities and elicitation completions live in one manager process, so keep `agent-manager.replicaCount: 1` for such clients. 2026-07-28 clients carry their capabilities on each request and work with several replicas.
