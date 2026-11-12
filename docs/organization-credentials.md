# Organization credentials

agent-manager never takes a credential value in an MCP call. A missing or replaced credential is answered with an MCP URL elicitation of the credential form when the client declares URL elicitation, and with the form link in `credentials[].next` otherwise. The value is then posted to agent-manager over authenticated HTTPS. The API, authentication and recovery rules are in [agent-manager's documentation](https://github.com/giantswarm/agent-manager/blob/main/docs/organization-credentials.md).

## Credential form

Set `agent-manager.credentials.uiURL` to the HTTPS URL template of the client that serves the form: it names the operation with one `{operation}` placeholder, and may name the organization with `{parent}` and `{organization}`. Which path the client serves is the client's own configuration. For the portal's credentials page, the template is `https://<portal>/organizations/<installation>/{parent}/{organization}/credentials?operation={operation}`; configure it only once the form ships in [giantswarm/backstage#3364](https://github.com/giantswarm/backstage/issues/3364). This chart derives no form URL. `agent-manager.devPortal` is refused.

The form uses its client's authenticated backend path to post the value to agent-manager's REST API as the person; agent-manager exposes no browser CORS exception. A client backend outside the cluster reaches that API through `agentManager.route` (`https://agentgateway.<global.domain>/agent-manager`, `agentManager.route.hostname` overrides the host): turn on `agentManager.route.enabled`, and `agentManager.route.jwtAuthentication.enabled` with the identity provider's issuer. agent-manager validates the token itself (`agent-manager.oauth`), so the audience of the ID token the client sends must be one it trusts (`agent-manager.oauth.trustedAudiences` and the muster audiences it accepts).

Credential operations and `agent-manager.githubTokenExchange` need a floor of `2.22.0` or later in `components.agent-manager.versionRange`. The default floor, `2.24.0`, keeps credentials write-only: `replace_organization_credential` replaces one, and a save whose credential's consumers changed is answered `credential_consumers_changed`. The Dev Portal's credential form needs it ([giantswarm/backstage#3364](https://github.com/giantswarm/backstage/issues/3364)). The organization switch keeps its own `2.20.0` floor. muster reaches the elicitation to the client from `5.61.0` (giantswarm/muster#1812), the floor of `components.muster.versionRange`.

`agent-manager.credentials.uiURL` needs `agentPlatformOrganizations.enabled`: agent-manager serves the form link only with its organization tools and downstream OAuth on.

## GitOps writes

`agent-manager.tenants.commit` (`repository`, `branch`, `objectsPath`, `contentPrefix`) configures where organization changes are committed. It travels beside the derived `tenants.enabled` and does not control the organization switch. The repository needs a SOPS policy and review ownership.

A commit is opened as the person: agent-manager exchanges the person's platform identity for their `https://github.com/apps/giantswarm-agent-manager` grant at an RFC 8693 token endpoint (`agent-manager.githubTokenExchange`: `endpoint`, `clientID`, `audience` and `secretName` are required), with `agent-manager.github.enabled` off. Give its client a dedicated credential, that App as its only target, the canonical identity issuer and an explicit input-audience allowlist.

Configure `agentPlatformOrganizations.fluxNamespace`, `fluxSource` and `fluxDecryptionSecret` so the operator creates each organization's Kustomization and restricted ServiceAccount. The bundled Flux engine needs `kustomize-controller` in `flux-engine.instance.components`.

## Network policy

The manager's egress policy admits the origin of `githubTokenExchange.endpoint` on its port:
- Cilium: a rule for the host name, or for the address when the endpoint is an IP literal.
- Native NetworkPolicy: public addresses on that port, or the single address of an IP literal. A broker on a private network needs its range in `agentManager.networkPolicy.egress.cidrs` or `networkPolicy.additionalEgressCIDRs`.

An endpoint on an in-cluster Service name (`<service>.<namespace>.svc`), or on a name of fewer than three labels (`muster`, `muster.agent-platform`) that the cluster's DNS search path resolves to one, fails the render while network policies are on: neither flavour can select a Service by its DNS name. Use the broker's external hostname or address.

## Replicas

A 2025-11-25 MCP client's session capabilities and elicitation completions live in one manager process, so keep `agent-manager.replicaCount: 1` for such clients. 2026-07-28 clients carry their capabilities on each request and work with several replicas.
