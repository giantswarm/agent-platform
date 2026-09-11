# Authentication flow

How a request authenticates against the Agent Platform. This document covers
**only authentication** — TLS termination details and the broader
networking/NetworkPolicy model are described elsewhere (`README.md` →
*Ingress topology* and the `networkpolicy-dataplane-*` templates).

The request topology is selected by `ingress.mode` (see `README.md` →
*Ingress topology*):

- **`muster-direct`** (default) — client → muster directly. There is **one** hop:
  the public Gateway → muster. No agentgateway data plane exists.
- **`agentgateway-muster`** / **`agentgateway-direct`** — client → agentgateway
  `/mcp` → muster (or, in `agentgateway-direct`, the servers). Here a second
  Gateway API hop (agentgateway) sits in front of muster.

This document narrates the **`agentgateway-*`** topology, where agentgateway is
present. In `muster-direct` mode, drop the agentgateway hop: the client reaches
muster directly over the public hop and muster enforces OAuth exactly as
described below.

Each section is one slice of the story with its own diagram:

1. [The request path (who terminates what)](#1-the-request-path)
2. [OAuth discovery — how an unauthenticated client finds the auth server](#2-oauth-discovery)
3. [Token handling at muster — `forward` vs `exchange`](#3-token-handling-at-muster)
4. [Edge JWT validation (`oauthMode: validate`) and JWKS](#4-edge-jwt-validation-and-jwks)

In the `agentgateway-*` modes, the only URL a client is ever given is
**`agentgateway.<cluster>.<base>/mcp`**; muster is a backend implementation
detail and clients never address it for `/mcp`. In `muster-direct` mode the
client is given muster's own `/mcp` URL directly.

---

## 1. The request path

In the `agentgateway-*` modes, two Gateway API hops sit in front of muster. The
**public** hop terminates TLS and owns the hostname; the **agentgateway** hop is
the observability and policy choke point. Authentication itself is still
enforced by muster at the end. (In `muster-direct` mode only the public hop
exists, routing straight to muster.)

```mermaid
flowchart LR
    client["MCP client<br/>(Claude.ai, Claude Code, any SDK)"]

    subgraph envoy["envoy giantswarm-default · envoy-gateway-system"]
        tls["TLS termination<br/>public hostname"]
        btp["route-scoped BackendTrafficPolicy<br/>preserves WWW-Authenticate · requestTimeout 0s"]
    end

    subgraph ns["release namespace"]
        agw["agentgateway proxy :8080<br/>observability choke point<br/>auth.passthrough by default"]
        muster["muster :8090/mcp<br/>OAuth enforcement + MCP aggregation"]
    end

    client -->|"HTTPS  /mcp"| tls
    tls --> btp
    btp -->|"HTTPRoute → Service :8080"| agw
    agw -->|"AgentgatewayBackend"| muster
```

What each component is responsible for, in auth terms:

| Hop | Template | Auth responsibility |
|---|---|---|
| envoy `giantswarm-default` | (cluster ingress, not this chart) | Terminates TLS, owns the public hostname. |
| `HTTPRoute` (`/mcp`) | `templates/agentgateway/httproute.yaml` | Routes `/mcp` to the agentgateway Service:8080. Rendered in the `agentgateway-*` modes (`ingress.mode`); reads `ingress.parentRefs` / `ingress.hostnames`. Without it the agentgateway `/mcp` route does not exist. (muster's public `/` route, `templates/ingress/muster-httproute.yaml`, is always rendered.) |
| `BackendTrafficPolicy` | `templates/agentgateway/backendtrafficpolicy.yaml` (agentgateway `/mcp` route) and `templates/ingress/muster-backendtrafficpolicy.yaml` (muster `/` route) | **Critical for auth:** a cluster-wide error-pages `BackendTrafficPolicy` rewrites 4xx/5xx to branded HTML and strips upstream headers — including `WWW-Authenticate`. A route-scoped policy (enabled via `ingress.backendTrafficPolicy.enabled`) takes precedence and preserves muster's `401 … WWW-Authenticate` challenge, without which clients cannot discover where to authenticate. The umbrella renders one over the agentgateway `/mcp` route (`agentgateway-*` modes only) and a complementary one over muster's `/` route (**all** modes) — the latter matters in `muster-direct`, where muster serves `/mcp` directly. Both also set `requestTimeout: 0s` (`ingress.backendTrafficPolicy.timeout`) so long-lived MCP/SSE streams are not killed. |
| agentgateway proxy | `gateway.yaml` + `agentgatewayparameters.yaml` | By default `auth.passthrough` — forwards the bearer token to muster unvalidated. Optionally validates at the edge (§4). |
| muster | `muster` sub-chart | Enforces OAuth, validates the token, aggregates downstream MCP servers, and performs token exchange where needed (§3). |

---

## 2. OAuth discovery

A fresh client arrives with no token. It must discover the authorization server
before it can authenticate. In the `agentgateway-*` modes the challenge is
served by muster but must survive the journey back through both gateway hops —
that is what the route-scoped `BackendTrafficPolicy` (`ingress.backendTrafficPolicy`)
guarantees. (In `muster-direct` mode the challenge travels only the single
public hop, but muster's `/` route still carries its own route-scoped
`BackendTrafficPolicy` so the same cluster-wide error-pages policy cannot strip
`WWW-Authenticate` from the `401` muster serves on `/mcp`.)

The keystone is `muster.oauth.server.resourceIdentifier`, set in shared-configs
to `agentgateway-host/mcp`. It makes muster advertise the **agentgateway**
resource in its own OAuth metadata, so discovery is consistent regardless of
which hostname the client actually reached muster through.

```mermaid
sequenceDiagram
    autonumber
    participant C as MCP client
    participant A as agentgateway /mcp
    participant M as muster

    C->>A: GET /mcp (no token)
    A->>M: forward
    M-->>A: 401 WWW-Authenticate: Bearer<br/>resource_metadata=muster-host/.well-known/oauth-protected-resource
    A-->>C: 401 (header preserved by route-scoped BTP)

    C->>M: GET /.well-known/oauth-protected-resource
    M-->>C: resource = agentgateway-host/mcp  ← matches the URL dialled

    C->>A: GET /.well-known/oauth-authorization-server
    A->>M: proxied (standard HTTPRoute, agent-platform-mcps)
    M-->>C: auth-server metadata (DCR endpoint, token endpoint, …)

    C->>M: DCR / CIMD directly at muster-host
    M-->>C: client credentials

    C->>A: GET /mcp + Bearer token
    A->>M: forward token
    M-->>C: 200 — tools served
```

Notes:

- muster's OAuth endpoints (`/.well-known/*`, DCR, token) remain publicly
  reachable on `muster-host`. agentgateway only proxies `/mcp` — Gateway API
  path-specificity (`/mcp` beats `/`) keeps every other path on muster directly.
- Step 5 (`oauth-authorization-server` via agentgateway) is the proxy route
  added by [agent-platform-mcps](https://github.com/giantswarm/agent-platform-mcps),
  so the client can do the whole flow against the single agentgateway hostname.

---

## 3. Token handling at muster

Once a valid token reaches muster, muster aggregates many downstream MCP servers
behind one endpoint. Each server entry declares **how** its token is obtained.
This is per-server config in the `agent-platform-mcps` `mcpServers` list, not a
gateway concern.

```mermaid
flowchart TD
    in["inbound Dex token<br/>(validated by muster)"]

    in --> mode{"per-server<br/>auth.mode"}

    mode -->|forward| fwd["token forwarded as-is"]
    mode -->|exchange| exch["RFC 8693 token exchange<br/>via the spoke's Dex<br/>(identityProviders ref)"]

    fwd --> same["same-cluster MCP server<br/>e.g. mcp-kubernetes on this cluster<br/>caller's Dex token already valid"]
    exch --> remote["remote / spoke MCP server<br/>e.g. mcp-kubernetes on a spoke cluster<br/>needs a cluster-specific token"]
```

| `auth.mode` | When | Mechanism |
|---|---|---|
| `forward` | Downstream server trusts the **same** issuer the caller authenticated with (typically same-cluster). | muster passes the inbound bearer token through unchanged. No exchange. |
| `exchange` | Downstream server lives behind a **different** issuer (a spoke/remote cluster). | muster performs an [RFC 8693](https://www.rfc-editor.org/rfc/rfc8693) token exchange against the spoke's Dex `tokenEndpoint`, using credentials from the `identityProviders.<provider>` entry, to mint a token the downstream server accepts. |

Example (`exchange` against a spoke cluster's Dex):

```yaml
agent-platform-mcps:
  mcpServers:
    - cluster: <spoke>
      group: kubernetes
      url: https://mcp-kubernetes.<spoke>.<base>/mcp
      auth:
        mode: exchange
        provider: <spoke>          # ref into identityProviders
  identityProviders:
    <spoke>:
      tokenEndpoint: https://dex.<spoke>.<base>/token
      connectorId: giantswarm-simple-oidc
      credentialsSecret:
        name: <spoke>-token-exchange-credentials
        clientIdKey: client-id
        clientSecretKey: client-secret
```

### On behalf of a user: the user's Dex token, forwarded

A kagent agent acts on behalf of the human who invoked it. kagent propagates the
human's Dex-issued token (`KAGENT_PROPAGATE_TOKEN`) as the only `Authorization`
reaching muster — no static per-agent header, no separate actor token. muster
validates that token and, per the downstream server's `auth.mode`, either
forwards it unchanged (`forward`) or exchanges it at the spoke's Dex (`exchange`,
above). muster never signs a token of its own: Dex is the sole SSO authority
(muster v1.0.0 removed JWT mode), so every downstream server validates against
Dex's JWKS, never muster's. The token carries the human only; the agent's own
identity is not asserted downstream.

For mcp-kubernetes the token is a Dex token with `aud=dex-k8s-authenticator`,
which mcp-kubernetes forwards to the kube-apiserver via downstream OAuth — so
Kubernetes RBAC and the audit log reflect the human directly. No muster-issued
token and no impersonation `ClusterRole` are involved.

### The per-agent muster server, and tool discovery by the kagent controller

On kagent API v2 an `AgentTemplate` binds a `RemoteMCPServer` of its **own
namespace** (`spec.tools[].mcp.server` is a local reference) and the binding
carries no headers, so the connectivity chart renders no shared muster server.
The **Generic agent chart 1.x** renders one `RemoteMCPServer` per agent — named
after the agent, in the agent's namespace — with muster's in-cluster URL
(`spec.url`, the chart value `muster.url`), `STREAMABLE_HTTP`, the toolset
header `X-Muster-Toolset` in `spec.headersFrom` (the selectors the agent was
created with; no toolset, no header, implicit full access) and the discovery
opt-out label below, and binds it. That server is the agent's toolset carrier:
agent-manager and the Dev Portal read an agent's toolset from it.

**Where muster is comes from one helper.** The URL every per-agent server
targets is the platform's — `agent-platform.musterMcpUrl`, defined in both
charts: `http://<muster.fullnameOverride>.<release namespace>.svc.cluster.local:<muster.service.port>/mcp`
while the muster component is on. The meta chart derives agent-manager's chart
value `muster.url` from it (next to `flux.helmReleaseServiceAccount`; a
differing `agent-manager.muster.url` fails the render naming the source), the
connectivity chart renders it into the portal's app-config as
`agentPlatform.musterMcpUrl`; both composers pass it to the agent chart as
`muster.url`. Chart 1.x defaults `muster.url` to the same URL on a default
install (`http://muster.agent-platform.svc.cluster.local:8090/mcp`), so a
composer may omit it; the value exists for an installation whose muster answers
under another name, namespace or port.

**Never a static `Authorization` header on a muster server.** The person's
token propagated by the Harness (`KAGENT_PROPAGATE_TOKEN` in its environment)
is the only `Authorization` that reaches muster (above). The Go ADK applies
`headersFrom` values **last** on every MCP call (`headerRoundTripper.RoundTrip`
in `go/adk/pkg/mcp/registry.go`: static headers take precedence over every
dynamic source), so a static `Authorization` there would replace the propagated
token and make every user of that agent act as one identity. The agent chart
renders only the toolset header. The operator extras
`kagent.remoteMcpServers[].tokenSecret` render a static header on purpose — for
a server that has no notion of the caller — and every agent reaches that server
as that credential, not as the person.

**Tool discovery by the controller: no identity, opted out.** The kagent
controller reconciles a `RemoteMCPServer` by connecting to it and listing its
tools for the CR status. That request is the controller's own: there is no
human behind it, so it carries no bearer. muster is an OAuth resource server
and answers `401`, and the controller would report `Accepted=False
(ReconcileFailed … Unauthorized)` on every agent's server — a permanent red
condition with no effect on agents, which resolve their tool list at run time
as the person. The agent chart therefore labels the per-agent server
`kagent.dev/discovery: disabled` by default (its value
`muster.discovery.enabled` turns the label off for a muster without OAuth); the
kagent line's controller honours the label — `Accepted=True`, reason
`DiscoveryDisabled`, an empty inventory — an opt-out the line carries as a
patch until upstream merges kagent-dev/kagent#2752. With discovery off a
Harness cannot narrow the server to `muster.tools`; it exposes the server and
may report a warning in `status.harnesses[].warnings` — the toolset header is
the enforced narrowing, applied by muster per request.

**Why the controller gets no credential of its own.** The obvious alternative
— a projected ServiceAccount token on the controller, presented through
`spec.headersFrom`, trusted by muster through a `trustedIssuers` entry for the
cluster's OIDC issuer — is rejected for the reason above: `headersFrom` is not
a discovery credential; the runtime applies it on every agent call, so every
agent would call muster as `system:serviceaccount:kagent:kagent-controller`,
the per-caller model of this section would be gone, and each rotation would
roll every agent. The controller's discovery has no identity by design; the
label makes the status say so instead of failing.

---

## 4. Edge JWT validation and JWKS

This section applies only to the `agentgateway-*` modes (in `muster-direct` mode
there is no agentgateway and muster is the sole validator). By default
agentgateway runs `auth.passthrough`: it forwards the token to muster
without inspecting it, and muster is the only validator. Optionally, agentgateway
can validate the JWT **at the edge** (`oauthMode: validate`) as a first layer —
muster still validates downstream as a second layer. Edge JWT validation is the
relevant model for `agentgateway-direct`, where agentgateway must gate traffic
on its own. Token exchange (§3) is
unaffected: agentgateway only ever sees the inbound token; muster's internal
RFC 8693 exchanges happen behind it.

### Edge validation validates against Dex, not muster

The default is `oauthMode: passthrough`: agentgateway forwards the token to
muster unchanged, and muster is the sole validator. muster issues only opaque
tokens — v1.0.0 removed `enableJWTMode`/`jwtSigningKey` and the chart schema now
rejects both — and must never be trusted as an issuer.

If an install turns on edge validation (`oauthMode: validate`), agentgateway
verifies the JWT against the issuer that signed it — **Dex** — by fetching Dex's
`/.well-known/jwks.json`. There is no muster JWT mode and no muster JWKS. muster
still validates downstream as the second layer, and token exchange in §3 is
untouched. `resourceIdentifier` (`agentgateway-host/mcp`) remains the audience
the token is bound to, so agentgateway can check `aud` matches the hostname the
client actually dialled.

Edge validation fetches the JWKS from Dex (the token's issuer), which typically
runs in another namespace on a non-standard port, so it needs an explicit
data-plane egress rule.

```mermaid
flowchart TD
    agw["agentgateway :8080<br/>oauthMode: validate"]
    dex["Dex (the token issuer)<br/>JWKS on a non-standard port<br/>e.g. :5556 in another namespace"]
    agw -->|"cross-namespace, non-80/443 port"| dex
    note["needs gateway.jwksEgress.enabled: true"]
```

### When `gateway.jwksEgress` is required

`gateway.jwksEgress` is an `agentgateway-*` data-plane knob (most relevant to
`agentgateway-direct`, where agentgateway validates JWTs at the edge against an
external key set).

The data-plane NetworkPolicy
(`networkpolicy-dataplane-{cilium,kubernetes}.yaml`) allows the proxy egress to
muster:8090 and the agentgateway controller:9978 by default. Fetching JWKS from
anywhere else is blocked unless you open it explicitly:

- **Default (`oauthMode: passthrough`):** no JWKS fetch — muster is the sole
  validator. Leave `gateway.jwksEgress.enabled: false`.
- **Edge validation (`oauthMode: validate`) against Dex:** Dex's JWKS (typically
  on `:5556`) runs in another namespace on a port the default cluster egress
  rules (80/443) don't cover. Enable the rule:

  ```yaml
  gateway:
    jwksEgress:
      enabled: true
      namespace: giantswarm     # where Dex lives
      port: 5556                # Dex's JWKS port
      podSelector: {}           # optional: narrow beyond namespace
  ```

### Enabling edge validation

1. `oauthMode: validate` on agentgateway, with `jwt.jwksBackendRef` pointing at
   the Dex that issued the tokens (set in shared-configs).
2. `gateway.jwksEgress.enabled: true` with Dex's namespace and JWKS port, so the
   data plane may reach it (see above).

> muster is not involved in edge validation and signs nothing: v1.0.0 removed
> `enableJWTMode`/`jwtSigningKey` and the chart schema rejects them.
