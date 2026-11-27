---
name: agent-platform-tools
description: Use when a question is about tools on the Agent Platform — Muster's meta-tools and call_tool, finding or running a tool, what a toolset or preset resolves to, why a tool is missing or refused, connectors and signing in to one, workflows, or model-manager.
metadata:
  version: "3.3.3"
---

# Tools on the Agent Platform

Docs: https://docs.giantswarm.io/overview/agent-platform/meta-tools/ (the concept),
https://docs.giantswarm.io/reference/muster/meta-tools/ (every meta-tool's fields),
https://docs.giantswarm.io/overview/agent-platform/toolsets/ and
https://docs.giantswarm.io/reference/muster/toolsets/ (toolsets and the selector grammar). What the
platform is belongs to the `agent-platform-overview` skill; agents and their building blocks to
`agent-platform-agent-management`.

## Muster is the tool surface

Every agent, IDE and portal session talks to one MCP endpoint, Muster, and sees a small fixed set of
**meta-tools** instead of the hundreds of tools behind it: `filter_tools`, `describe_tool` and
`call_tool` for discovery and execution; `list_tools` and `list_core_tools` for whole listings (large —
prefer `filter_tools`); and list/filter/describe/get tools for MCP resources and prompts. The set you
have is your own tool list. The one exception: an agent's organization connectors (below) are
tools of their own beside the meta-tools, called as functions, not through `call_tool`.

**The `call_tool` contract.** Every tool behind Muster runs as
`call_tool {"name": "<exact name>", "arguments": {…}}`, `name` first. The name is a string argument,
never a function of its own, and always the full catalogue name as `filter_tools` returns it
(`x_agent-manager_list_agents`, never `list_agents`): a short name from a skill, a prompt or memory
is not in the catalogue. A backend tool emitted as a function name fails with "tool not found".
`describe_tool`'s `invocation` line repeats this for every tool. Arguments follow the tool's
`inputSchema`: read `describe_tool` before the first call of a tool you have not used.

The reverse holds too: the meta-tools and the runtime's built-ins (`load_skill`, `read_file`, `bash`,
…) are functions, never a `name` for `call_tool`. `arguments` carries exactly the tool's
`inputSchema` properties, every required one set; never nest a second `name`/`arguments` pair in it.

**Name shapes.** `x_<server>_<tool>` is a connector's tool, prefixed with the server's registered
name; `core_<area>_<tool>` is one of Muster's own; `workflow_<name>` is a workflow. Servers of a
**family** (one instance per cluster or region) appear once under the family name and take an
instance argument (for example `management_cluster`); its valid values are the `enum` on the tool's
schema — take them from `describe_tool`, never guess or shorten them. Another installation's
management cluster is reachable only when it is in that `enum`; otherwise say so.

## What you see: the catalogue, filtered per request

The catalogue is computed on every request as three filters in order: the servers **this person is
signed in to**, the **workflows** available, and the **toolset** — the agent's selector list
(`preset:<name>`, `server:<name>`, `workflow:<name>`, `tool:<name>`; a server in an organization
namespace is `server:<namespace>/<name>`; a bare name selects only a server of muster's own
namespace; an organization's own connector is no Muster server and no selector reaches it), sent as the `X-Muster-Toolset` header or bound to the session when it starts. `filter_tools`, `describe_tool`, `list_tools` and
`call_tool` all answer within it; a call outside it is refused with an error naming the toolset. An
unknown preset is an error on every meta-tool call, never a fall-back to everything. A request without
any toolset sees the person's whole catalogue — an agent without a toolset has implicit full access.

Inline toolsets allow at most 128 selectors and 8192 UTF-8 bytes when joined with commas, from Muster
5.63.7 (32 selectors before; the `toolset.maxItems` of `x_agent-manager_create_agent` gives the current
count). These limits apply to the header, `filter_tools` argument and Session binding. Larger selections use an
existing preset or an admin-managed `toolsetPresets` entry; selecting All for a connector also
includes its future tools. Count limits do not limit the number of tools a preset resolves to.

**Composition, not authorization.** The toolset bounds the model. Every backend authorizes the
**person** (Kubernetes RBAC, a service's own accounts): an agent acts within *its toolset ∩ the
person's access*, and two people driving the same agent can see different tools.

## Finding and running a tool

1. A workflow first, where one fits: `filter_tools {"query": "<the question in words>"}` or
   `{"pattern": "workflow_*"}` — one call answers a whole question.
2. Otherwise shortlist with `filter_tools`: `pattern` (glob on names), `query` (ranked natural
   language), `description_filter`, `labels`; `limit`/`offset` page; `include_schema: true` only for
   the final candidates.
3. `describe_tool {"name": …}` for the authoritative description and schema, then `call_tool`.

What a preset or toolset resolves to **for this caller**:
`filter_tools {"include_presets": true, "toolset": ["preset:<name>"]}` — `presets` lists every preset
with its description, `toolset_unmatched` the selectors that selected nothing, `toolset` (or
`toolset_bound`) what bounds you. It never widens your own toolset. Defining presets:
https://docs.giantswarm.io/tutorials/agent-platform/toolset-presets/.

## URL interactions and retries

Muster and its downstream managers can ask for a URL interaction, including sign-in,
consent or credential setup. Preserve the producer's message and show the URL for the
person's consent; never open or accept it automatically. Acceptance agrees to navigation
and does not prove the browser flow completed. Let the MCP client carry sealed
`requestState` and `inputResponses` on the same call, without changing its arguments.
These are protocol fields, never tool arguments. Decline or cancel stops that attempt.
An invalid-state or sealing error is a failure, not permission to retry without state.

## Connectors (MCP servers)

"Connector" names two things. A **Muster connector** is a Muster `MCPServer`, and an agent reaches
its tools through its `toolset`. An **organization's own connector** is a kagent `RemoteMCPServer`
in the organization namespace, and an agent of that organization takes it by name with
`connectors` (`agent-platform-agent-management`); its calls go from the agent straight to the
connector with the connector's credential, not through Muster.

The Dev Portal calls MCP servers **connectors** (Customize → Connectors). Each is an `MCPServer`
resource (`muster.giantswarm.io`); `core_mcpserver_list` shows them with their state. A
platform-shipped one carries the label `agent-platform.giantswarm.io/tool-group` (`agent-platform`:
the managers and Muster's `core_*` tools; `infrastructure`: the clusters underneath), and the presets
of those names select by it; no label means an installation or a person registered it. States:

- **`Auth Required`** is healthy: reachable, waiting for a person's sign-in.
- **`Awaiting Session`**: healthy, used with each caller's own identity; no sign-in to run. It turns
  `Connected` when a session calls it with its own token; a call that still fails is the person's
  token or access — sign in to the platform again, then check `status.conditions`.
- **`Failed`** is the outage; `status.lastError` says why.

An organization's connector with a credential (`x_agent-manager_set_organization_connector`,
organization admin): create it with a credential field — `credentialHeader` (default
`Authorization`; Secret `tenant-credential-<name>`, key `token`). Without one it has no credential:
`replaceCredential` on it does nothing and `x_agent-manager_replace_organization_credential` refuses
it, as nothing uses that Secret — set `credentialHeader` with `set_organization_connector`. The
answer is a URL elicitation of the credential form, link in `credentials[].next`; the connector is
written once the person saves the form. No link: give the person the raw answer, never an
explanation or a hand-made Secret. Check with `x_agent-manager_list_organization_connectors`
(non-empty `credentials`); `core_mcpserver_detect` probes the URL.

Whether an organization's connector is usable, from agent-manager 3.3.0: in
`x_agent-manager_list_organization_connectors`, each credential's `state` is `present` or
`missing` (whether its Secret exists, checked by a dry-run create as you; not whether it holds the
key) or `unknown` (the check failed, or you may not create that Secret: an editor gets `unknown`),
and each connector's `accepted`, `reason` and `message` are kagent's tool discovery. Usable:
`accepted: true` and no credential `missing`; `unknown` does not stop it. `accepted: false` is a
connector that does not work (a missing key, say): tell the person its `message`.
`accepted: null`: not reported yet. The list also holds each agent's binding to Muster: the
`RemoteMCPServer` that one HelmRelease renders with an agent of its name. It is no connector:
offer and use only the entries without `musterBindingOf`, which marks it with the agent's name
from agent-manager 3.3.4 (`musterBindingOf` in the tool's description). When no entry carries
the field (an older agent-manager, or you may not list the namespace's Agents), leave out each
entry named like an agent of the namespace.

To replace a model key or an egress credential, read its Secret from the list:
`x_agent-manager_list_model_configs` (each model with a key) and
`x_agent-manager_list_egress_policies` (each destination with a credential) name it by
`credentialSecret` and `credentialKey` — names only, never a value. Pass them as `secret` and `key`
to `x_agent-manager_replace_organization_credential`, or as `credentialSecret` and `credentialKey`
to `x_agent-manager_set_organization_model` and `x_agent-manager_set_organization_egress_policy`;
never ask the person for them.

Tools register live when a server connects. Adding connectors:
https://docs.giantswarm.io/tutorials/agent-platform/managing-mcp-servers/ and
https://docs.giantswarm.io/tutorials/agent-platform/connecting-custom-mcp-servers/.

## Workflows

A `Workflow` resource is a list of tool calls Muster runs server-side, returning one shaped answer; it
is registered as `workflow_<name>` and called like any tool — far fewer calls and tokens than the same
investigation with raw tools. Which workflows exist is installation-specific: `core_workflow_list` or
`filter_tools {"pattern": "workflow_*"}`; `describe_tool` shows a workflow's arguments and its
description. A workflow whose steps are all read-only is itself read-only and part of the built-in
`read-only` preset. Authoring: https://docs.giantswarm.io/tutorials/agent-platform/authoring-workflows/.

## model-manager

model-manager is the platform's MCP server for the models an installation serves itself: serving
backends and their inventory; pulling, loading, unloading and deleting models; wiring a served model
into a kagent `ModelConfig` agents can run on. It is on by default with no backend; a backend is
registered at runtime. Its tools: `filter_tools {"pattern": "x_model-manager_*"}`, then `describe_tool`.
An empty result means it is not deployed here or you are not signed in to it. The model configs
agents can use: `x_agent-manager_list_model_configs`. Bringing a model:
https://docs.giantswarm.io/tutorials/agent-platform/bring-your-own-model/.

## Confidential OAuth clients

External integrations can register as separate confidential clients of muster's OAuth server using
`client_secret_basic`, `client_secret_post` or `private_key_jwt`, once the supporting muster release
is deployed. Provisioning needs the operator's registration bearer token; never request or display
it in a conversation. Use authorization code with PKCE S256, request the registered muster scope
(`mcp` where configured), and send the canonical MCP `resource` in authorization and token requests.
The authenticated client ID identifies the integration; each person's grant remains separate.
For `private_key_jwt`, sign each request with a fresh assertion whose sole `aud` is the authorization
server's discovered issuer identifier. Multiple replicas require shared Valkey replay protection.
Upgrading existing Valkey storage requires stopping OAuth traffic and old writers, migrating the
family revocation index, then restarting every replica on the new release before restoring traffic.
Use the linked operator procedure; old binaries must not write to migrated storage.
Muster keeps the upstream identity-provider credentials and forwards or exchanges them according
to the backend's configuration. Never send a muster access token directly to a backend.

The bundled Kubernetes server is registered through its in-cluster Service URL.
Its canonical OAuth resource may differ from that transport URL. Muster's warning release
accepts valid cross-origin identifiers for operator-managed backends with a `DEPRECATED:`
warning. Verify ownership and the transport mapping; exact RFC 9728 resource matching
is not enforced on that discovery path. Resource identifiers are unchanged.

Client configuration management is opt-in through
`muster.oauth.server.enableClientManagementEndpoint`. Store the registration access token in a
secret manager and use the returned `registration_client_uri`, not a constructed management URL.
Rotate by registering and verifying a replacement, then deleting the old registration with its
own registration access token. With client deletion checks on every replica, deletion rejects
existing access tokens and refresh requests across replicas. These guarantees apply to client-bound
tokens; self-issued exchange JWTs without a client binding remain valid until expiry.
After Valkey loss, provision a new
registration and obtain fresh user authorizations; restoring an old snapshot can restore retired
clients. Procedure and version prerequisites:
[Confidential clients](https://github.com/giantswarm/muster/blob/main/docs/how-to/confidential-clients.md).

## When a tool is missing or refused

- After a Valkey-backed installation enables muster encryption, old sign-ins and actor bindings
  can be unreadable. Once the rollout finishes, sign in again and start a new Session so
  agent-manager binds its actor. Unreadable bindings grant no credentials and can be replaced
  without waiting for their TTL. Operator guidance:
  [Encryption upgrades](https://github.com/giantswarm/muster/blob/main/docs/operations/installation.md#more-than-one-replica).
- `filter_tools` with a `pattern` names, under `servers_requiring_auth`, each connector whose tools
  a sign-in would unlock, with its `auth_tool`: call `core_auth_login` with that `server` from this
  conversation and give the person the sign-in link it returns. An entry with an `error` and no
  `auth_tool` cannot be signed in to from this session: tell the person its `error` as is and do
  not call `core_auth_login`, which refuses it the same way.
- A sign-in elsewhere (the Dev Portal's Customize → Connectors, `muster auth login` on the CLI)
  reaches this conversation only for a connector with `grantScope: subject` (`core_mcpserver_get`:
  `spec.auth.grantScope` or `spec.auth.authorizationServer.grantScope`). An own-account (OAuth)
  connector without it signs in per session: the portal shows it as "Portal only", and every agent
  conversation needs its own sign-in. Never send the person to the portal for such a connector.
- `filter_tools {"include_presets": true, "toolset": [<the agent's selectors>]}`: `toolset_unmatched`
  names selectors that select nothing (a server not signed in to, a renamed tool, an empty preset).
- An error **naming the toolset** on `call_tool` is composition — the tool is outside the toolset.
  Change the agent (`agent-platform-agent-management`), not the call.
- `auth_required` naming a provider App (structured `apps`, each with its `authUrl`) answers a call
  that needs the person's grant of that GitHub App: a commit (`mode: commit` on agent-manager,
  cluster-manager or model-manager), or pinning or reading a private skill. Where the manager
  fetches the grant per call (its `github.tokenExchange`), only that call asks and everything else
  works without it; a manager still pinned to its App asks at sign-in, before any call. Give the
  person the link and call again once they signed in.
- An agent acting for a person never receives the person's GitHub grant: its commit, and its pin or
  read of a private skill, are refused (`forbidden`). The person does it in their own session.
- `forbidden` or `Unauthorized` from a backend concerns its authorization; a toolset change cannot
  grant backend permissions. A successful agent-manager read with `releaseWithheld` is a partial
  answer, not an error: use the readable Agent data and explain which release details are unavailable.
  Do not send an authenticated person through sign-in again or blame them for this platform limit.
- "tool `x_…` not found" from the runtime: a backend tool was emitted as a function — go through
  `call_tool`.
- An issuer discovery or TLS error is installation connectivity, not a request for the person to
  sign in again. The managers' `oauth.dex.allowPrivateURLs` (workspace-manager's included) stays `false`: Dex discovery
  reaches private addresses through the normal proxy settings without that opt-in. If a manager
  refuses an old `true`, remove it. Each server fetches its own Dex's key set from a private address
  without a setting: `oauth.sso.allowPrivateIPs` and muster's `dex.allowPrivateIPJWKS` (alias
  `dex.allowPrivateIPOIDC`) are deprecated; do not set them to repair discovery. Operator checks and migration:
  [OAuth issuer connectivity](https://github.com/giantswarm/agent-platform/blob/main/docs/authentication.md#oauth-issuer-connectivity).
- A `DEPRECATED:` warning about a backend's advertised `resource` naming another origin can
  indicate an operator-managed private tunnel whose URL differs from the canonical resource.
  Have the operator verify ownership and the tunnel mapping; do not change resource identifiers
  or pin an authorization-server issuer to hide the warning. Cross-origin acceptance is a
  documented RFC 9728 compatibility exception, not proof of safe token reuse. Tenant resource
  checks, discovered subject-scoped grant binding and TLS verification remain enforced.
  Browser Origin warnings concern the operator's allowed origins, not the person's credentials.
  Configuration and migration:
  [Muster browser origins and discovery](https://github.com/giantswarm/muster/blob/main/docs/reference/configuration.md#browser-origins-and-mcp-discovery).
- Everything else: https://docs.giantswarm.io/tutorials/agent-platform/troubleshooting/.
