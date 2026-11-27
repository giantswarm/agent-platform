# Credential API

The optional `components.credential-api` component handles person-bound browser submission and apply-mode Secret writes. It has no MCP endpoint or credential-read API. It runs in `agent-platform-credential-api`, separate from manager workloads. Managers authenticate with TokenRequest tokens issued for `credential-api` and may open operations only under their configured kind prefixes.

The component is disabled by default. Its intended implementation release is 0.2.0, which is not published; provisioning's initial 0.1.0 scaffold is not this service. Keep the integration draft until repository provisioning, the shared `mcp-toolkit/k8s` named-subject authorization release and the credential-api release have completed. The existing `UNRELEASED` annotation blocks an enabled development installation and the release pipeline until that version is available. The existing muster version floor remains unchanged.

```yaml
components:
  credential-api:
    enabled: true
credential-api:
  installation: example
  portalOrigin: https://backstage.platform.example.com
  existingSecret: credential-api
  managers:
    system:serviceaccount:model-manager:model-manager:
    - tenant-credential-model-
    system:serviceaccount:connector-manager:connector-manager:
    - tenant-credential-connector-
```

`global.domain` derives `https://credentials.<domain>`. The identity-provider issuer and first Kubernetes audience follow the existing global identity configuration. The service has its own OAuth client, `credential-api`, registered at the same identity provider. Register both exact redirect URIs: `https://credentials.<domain>/credential-api/callback` and `<portalOrigin>/credential-api/callback`. On Dex, add `credential-api` to the Kubernetes audience client's `trustedPeers`.

Provision the referenced Secret in the dedicated namespace through the installation's existing secret management process. It contains `client-secret` for the separate OAuth client, `session-key` as a random 32-byte base64 encryption key and `valkey-url` as the authenticated URL of the existing platform Valkey. Keep these inputs out of plain chart values and command arguments. The service encrypts server-side sessions and OAuth state in Valkey; no credential values enter either. Its ServiceAccount may manage operation and audit ConfigMaps, review authentication and authorization, and read namespace metadata. It has no Secret permissions.

The service chart owns Gateway API HTTPRoutes. `/credential-api` and `/credentials` at the portal origin go directly to credential-api's Service, with no Backstage backend hop. The standalone host routes directly to the same Service. Configure `credential-api.gatewayAPI.parentRefs` when the existing global or chart-owned Gateway does not apply. Its listener must permit routes from the dedicated namespace. TLS certificates and DNS remain installation inputs. These paths establish a credential-api session even when the person already has a portal session. Existing portal credential consumers are not migrated by enabling this component.

While the component is enabled, the data-plane network policy admits its backend in the selected namespace on port 8080 in both policy flavors. The Valkey policy admits credential-api's labelled pods in that same namespace, retaining the configured store clients and the Kubernetes MCP's own session client. Those permissions are absent while the component is disabled.

Each operation binds the authenticated manager, original request identity, issuer and subject, namespace identity, Secret, fields, consumers and exact destinations. Browser writes require the original sign-in, a same-origin request, a CSRF token and the current confirmation version. An authorization refusal says “Ask an admin”; the admin must initiate an operation with their own sign-in. Manager descriptions render as plain text.

The form supports several primitive fields and file contents. Google service-account files retain their validation. Apply mode uses the person's identity for dry-run create and whole-Secret replacement with an optimistic version precondition. Credential reads and patches are never used. A lost write receipt remains explicitly uncertain; retries cannot silently replace a later value. Operations expire after 15 minutes. Save attempts and their outcomes are recorded without values in audit ConfigMaps. Export and retain these audit records according to the installation's audit policy, and monitor the namespace's quota.

Model credentials belong directly to model-manager in the later migration. The description of giantswarm/giantswarm#39475 still assigns model credential operations to agent-manager, conflicting with that ownership. That issue needs correction before migration; this integration does not implement it. Commit mode, encryption and ciphertext pickup remain giantswarm/giantswarm#39474.
