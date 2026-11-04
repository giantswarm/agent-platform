---
name: agent-platform-tools
description: Use when a question is about how tools work on the Giant Swarm Agent Platform — how Muster's meta-tools and call_tool work, how to find or run any tool, what a toolset or preset resolves to for the caller, why a tool is missing or a call was refused, MCP servers and their state, workflows and why they are cheaper, or what model-manager can do. Carries the mechanics and the fetch recipes, never a list of tools, presets or servers — those are read live.
metadata:
  version: "2.1.0"
---

# Tools on the Agent Platform

Docs: https://docs.giantswarm.io/overview/agent-platform/meta-tools/ (the concept),
https://docs.giantswarm.io/reference/muster/meta-tools/ (every meta-tool's fields),
https://docs.giantswarm.io/overview/agent-platform/toolsets/ and
https://docs.giantswarm.io/reference/muster/toolsets/ (toolsets and the selector grammar). Defining
presets, writing workflows and registering servers are tutorials, linked per section. What the platform
is belongs to the `agent-platform-overview` skill; creating, changing and troubleshooting agents to
`agent-platform-agent-management`.

## Muster is the only tool surface

Every agent, IDE and portal session talks to one MCP endpoint, Muster, and sees a small fixed set of
**meta-tools** instead of the hundreds of tools behind it: `filter_tools`, `describe_tool` and `call_tool`
for discovery and execution; `list_tools` and `list_core_tools` for whole listings (large — prefer
`filter_tools`); and a list/describe/get trio each for MCP resources and prompts. The set you have is
your own MCP tool list; the reference page documents each.

**The `call_tool` contract.** Every tool behind Muster runs as
`call_tool {"name": "<exact name>", "arguments": {…}}` — the name is a string argument, never a function
of its own, and always the full catalogue name as `filter_tools` returns it (`x_agent-manager_list_agents`,
never `list_agents`): a short name from a skill, a prompt or memory is refused as outside the toolset. A model that emits `x_kubernetes_list` as a function name gets "tool not found": the Go
runtime hands the error back for the next attempt, the Python runtime ends the turn. `describe_tool`'s
`invocation` field repeats this for every tool. Arguments follow the tool's `inputSchema`, so read
`describe_tool` before the first call of a tool you have not used.

The reverse holds too: the meta-tools and the runtime's built-ins (`load_skill`, `read_file`, `bash`, …)
are functions, never a `name` for `call_tool` — `call_tool {"name": "filter_tools"}` or
`{"name": "call_tool"}` is refused as outside the toolset, and `describe_tool` knows catalogue tools
only. `arguments` carries exactly the tool's `inputSchema` properties, with every required one set;
never nest a second `name`/`arguments` pair inside it.

**Name shapes.** `x_<server>_<tool>` is an external MCP server's tool, prefixed with the server's
registered name; `core_<area>_<tool>` is one of Muster's own; `workflow_<name>` is a workflow. Servers in
a **family** (one instance per management cluster — the infrastructure servers) appear once under the
family name and take the instance argument `management_cluster`, whose value is the **full server
name**, `<mc>-mcp-kubernetes`. The valid values are the `enum` on the tool's own schema: take them from
`describe_tool`, never guess or shorten them.

## What you see: the catalogue, filtered per request

The catalogue is computed on every request, statelessly, as three filters in order: the servers **this
person is signed in to** (a server they have not signed in to has no tools for them), the **workflows**
available, and the **toolset** — the selector list on the agent's release (`preset:<name>`,
`server:<name>`, `workflow:<name>`, `tool:<name>`, exact names), which the runtime sends as the
`X-Muster-Toolset` header. `filter_tools`, `describe_tool` and `list_tools` answer within it; `call_tool`
on a tool outside it is refused with an error naming the toolset. An unknown preset is an error on every
meta-tool call, never a fall-back to everything; a request without the header sees the unfiltered
catalogue — an agent without a toolset has implicit full access to whatever the person can reach.

**Composition, not authorization.** The toolset bounds the model. The only credential on the path is the
**person's forwarded token**, so every backend authorizes the person (Kubernetes RBAC, a service's own
accounts): an agent always acts within *its toolset ∩ the person's access*, and two people driving the
same agent can see different tools.

## Finding and running a tool

1. A workflow first — `filter_tools {"query": "<the question in words>"}` or
   `{"pattern": "workflow_*", "description_filter": "<topic>"}` — because one call answers a whole question.
2. Otherwise shortlist with `filter_tools`: `pattern` (glob on names), `query` (ranked natural language),
   `description_filter` (substring), `labels`; `limit`/`offset` page and `truncated` says there is more;
   `include_schema: true` only for the final candidates.
3. `describe_tool {"name": …}` for the authoritative description and schema, then `call_tool`.

What a preset or toolset resolves to **for this caller**:
`filter_tools {"include_presets": true, "toolset": ["preset:<name>"]}` — `presets` lists every preset
Muster knows with its description (built-ins first), `tools` what the selectors resolve to,
`toolset_unmatched` the selectors that selected nothing. Inside an agent this resolves within its own
toolset and never widens it. Defining presets: https://docs.giantswarm.io/tutorials/agent-platform/toolset-presets/.

## MCP servers

Servers are registered as `MCPServer` resources (`muster.giantswarm.io`) in the platform's namespace on
the management cluster. `workflow_platform-servers` shows them with state and tool group in one call;
the raw view is `call_tool` → `x_kubernetes_list` with `resourceType: mcpservers`,
`apiGroup: muster.giantswarm.io`, `allNamespaces: true` (state in `status.state`, group in the label
`agent-platform.giantswarm.io/tool-group`; no label means a person registered it). **`Auth Required` is
healthy** — reachable, waiting for a sign-in; only `Failed` is an outage. Tools register live when a
server connects, no client restart. Adding servers:
https://docs.giantswarm.io/tutorials/agent-platform/managing-mcp-servers/,
https://docs.giantswarm.io/tutorials/agent-platform/connecting-custom-mcp-servers/,
https://docs.giantswarm.io/tutorials/agent-platform/multi-cluster-access/.

## Workflows

A `Workflow` resource is a list of tool calls Muster runs server-side, returning one shaped JSON document;
it is registered as `workflow_<name>` and called like any tool. One call replaces the agent's
discover–query–correlate loop — an order of magnitude fewer tool calls and tokens than the same
investigation with raw tools. `core_workflow_list` lists them (or `filter_tools` with `pattern:
workflow_*`); `describe_tool` shows a workflow's arguments and its description, the only text an agent
ever sees of it. The platform's own discovery workflows: `workflow_agent-status` (one agent's verdict),
`workflow_agent-roster` (every agent), `workflow_model-overview` (model configs and, where it runs,
model-manager's backends and capacity), `workflow_platform-servers` (MCP servers). A workflow whose steps
are all read-only is read-only itself and part of the built-in read-only preset. Authoring:
https://docs.giantswarm.io/tutorials/agent-platform/authoring-workflows/ and
https://docs.giantswarm.io/tutorials/agent-platform/saving-tokens-with-workflows/.

## model-manager

model-manager is the platform's MCP server for the models an installation serves: it knows the configured
serving backends and their inventory, pulls, loads, unloads and deletes models, and wires a served model
into a kagent `ModelConfig` so agents can run on it; on GPU installations it also offers presets, hub
search, a fit check and node capacity. Its tools appear as `x_model-manager_*`:
`filter_tools {"pattern": "x_model-manager_*"}`, then `describe_tool`.

An **empty result** means model-manager is not deployed on this installation (off by default in the
platform chart; it needs a serving backend) or you are not signed in to it. Model configs then come from
`workflow_model-overview`, `x_agent-manager_list_model_configs`, or the `ModelConfig` resources
(`api.kagent.dev/v1alpha3`) in the runtime's namespace via `x_kubernetes_list`. Bringing a model to an
installation: https://docs.giantswarm.io/tutorials/agent-platform/bring-your-own-model/.

## When a tool is missing or refused — the live checks

- `filter_tools {"include_presets": true, "toolset": [<the agent's selectors>]}`: `toolset_unmatched`
  names the selectors that select nothing (a server you are not signed in to, a renamed tool, an empty
  preset); `presets` shows whether a named preset exists at all.
- An error **naming the toolset** on `call_tool` is composition — the tool is outside the agent's
  toolset. Change the agent (`agent-platform-agent-management`), not the call.
- `forbidden` or `Unauthorized` from a backend is the **person's** authorization (RBAC on that cluster,
  the service's account); no toolset change affects it.
- `Unauthorized` from muster itself — `sending "tools/call": Unauthorized`, or `calling "initialize": …
  Unauthorized` on the reconnect — is the turn's own credential: the person's token that came with their
  message ran out while the turn was still working. Nothing inside the turn renews it, and
  `core_auth_login` fails the same way. Stop, say what is done and what is not, and ask the person to send
  a message to continue: the next one carries a fresh token. It is not a backend sign-in.
- A server in `Auth Required` whose tools you cannot see: sign in — `core_auth_login` for that server,
  the portal's Servers page, or `muster auth login` on the CLI (`muster auth status` shows the state).
  Repeated re-authentication prompts point at an expired session or an audience mismatch on that
  server's `MCPServer` resource.
- "tool `x_…` not found" from the runtime: a backend tool was emitted as a function — go through `call_tool`.
- Everything else: https://docs.giantswarm.io/tutorials/agent-platform/troubleshooting/.
