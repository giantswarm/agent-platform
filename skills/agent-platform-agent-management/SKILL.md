---
name: agent-platform-agent-management
description: Use when asked to create, change, inspect, troubleshoot, re-pin or delete an agent on the Agent Platform, to choose its model, skills, toolset or egress, or to read or evaluate one of its sessions.
metadata:
  version: "3.0.0"
---

# Managing agents and their building blocks

Public pages: https://docs.giantswarm.io/tutorials/agent-platform/create-an-agent/ and
https://docs.giantswarm.io/tutorials/agent-platform/troubleshooting/. Muster's meta-tools, the
`call_tool` contract, toolsets and connectors are the `agent-platform-tools` skill; the skill format
and what a pin is are `agent-platform-skill-authoring`; whether a task needs a new agent, and its
start link, are `agent-platform-agent-routing`. The one reference here is
`references/system-prompt.md`: the paragraph every tooled agent's prompt needs, and a template.

## The agent unit

One agent is one Flux `HelmRelease` of the `agent` chart in a namespace agent-manager manages.
helm-controller renders a kagent `Agent` — prompt, model config, pinned skills and plugins, toolset,
egress, and the `Harness` that runs it — and, for a tooled agent, a `RemoteMCPServer` pointing at
Muster. The Harness boots the agent into a golden snapshot and the Agent reports `Ready`. Any change is
a new revision: running sessions keep theirs, new sessions start from the new one.

Its building blocks are separate resources the agent names: a `ModelConfig` (the model), skills from
the configured skill sources, a toolset (Muster selectors and presets), `EgressPolicy`s (the origins
it may reach), a Harness. agent-manager picks and pins them; it never writes model configs or egress
policies.

Where the reference lives — fetch it, never quote it from memory:

- Managed namespaces, chart and version, the platform Harness, skill-manager (`skillManager`), the
  skills credential, API versions and `capabilities`: `x_agent-manager_get_info`. Skill sources and
  their skills: `agent-platform-skill-authoring`.
- The values schema and the manifests a set of values renders: `x_agent-manager_validate_agent` with
  the intended values returns the composed manifests and every violation, writing nothing. It is also
  how a GitOps `HelmRelease` is drafted: validate, copy.

## How an agent is managed

`x_agent-manager_list_agents` reports `managed` per agent:

- **`helmrelease`** — written live through agent-manager, as you.
- **`gitops`** — applied by Flux from a repository. A live write is refused; with `mode: commit`
  (where `get_info` `capabilities.commit` is true) agent-manager opens the pull request as you.
  Never `force` a write onto it.
- **`platform`** — ships with the Agent Platform (the default agent itself). Every update, re-pin and
  delete is refused; it changes only through the platform's configuration and releases. Say so.
- **`none`** — a bare Agent without a release.

## Working with agent-manager

agent-manager's tools are `x_agent-manager_*` through `call_tool`, acting as the person. Discover
them with `filter_tools` (`pattern`) and read each schema with `describe_tool` before its first call;
never assume an argument. The order:

1. `x_agent-manager_get_info`, then the choices from live lists: `x_agent-manager_list_model_configs`,
   `x_skill-manager_list_skills` (per source: ref, the commit it resolves to, path, readOnly, each
   skill's description, and each plugin with its `format`: `agent-plugins` needs a `skills` selection
   from its listed skills, `claude-code` loads whole — judge a skill from that listing, or read it with
   `x_skill-manager_get_skill`; `load_skill` knows only your own skills),
   `x_agent-manager_list_egress_policies`, presets from `filter_tools {"include_presets": true}`.
2. `x_agent-manager_validate_agent` → `x_agent-manager_create_agent` →
   `x_agent-manager_get_agent_status` until `ready` (about a minute; the golden snapshot is the long
   part) → a first turn as the person (`x_agent-manager_start_session`, where offered).
3. `x_agent-manager_update_agent` for follow-ups: only the fields passed change; `skills`, `plugins`,
   `egress` and `toolset` replace their whole list. `refreshSkills: true` re-pins every git skill and
   plugin to the head of its ref, as you: it fails when you cannot read one of the agent's
   repositories.

## The rules the tools do not tell you

- **Technical name**: DNS-1123, at most 63 characters, chosen by the person, never derived from the
  display name in silence. It names the release and the Agent; the display name and description are
  what the portal and Slack show.
- **Model**: the cheapest config from the live list that can do the job, never invented — an agent
  on a missing or not-accepted config never boots. No suitable model: `agent-platform-tools` (model-manager).
- **Toolset**: the smallest that does the job; `preset:none` for chat only; the broadest preset only
  on an explicit request. An own-account (OAuth) connector without `grantScope: subject` needs a
  sign-in in each of the agent's conversations (`agent-platform-tools`); say so before choosing it.
- **Toolchains and workspace**: `defaultToolchains` and `defaultWorkspace`, the smallest set the job
  needs, and only where the installation offers them (both are opt-in: `x_toolchain-manager_*`,
  `x_workspace-manager_*` in `filter_tools`); otherwise leave them out.
- **Skills**: always pinned (the tools pin at write time); pins and private repositories:
  `agent-platform-skill-authoring`.
- **Egress**: none unless the job needs an origin; a named `EgressPolicy` where one fits, raw origins otherwise.
  Tenant admins write policies; a change to one reaches its agents from their next session.
- **System prompt**: role, voice and rules; knowledge goes into skills. Every agent with tools carries
  the meta-tool paragraph from `references/system-prompt.md`.
- **Icon**: `iconUrl` only where the installation serves avatars; omit it otherwise.
- **Confirm every choice you made for the person** — the technical name above all — before writing.
  Delete only on an explicit request that names the agent, one at a time.

## Status and troubleshooting

`x_agent-manager_get_agent_status` is the one call: a verdict — `ready`, `progressing`, `failed`,
`unknown` — with a one-line summary, from the Agent's conditions, the release's conditions and recent
Warning events. `progressing` for about a minute after a write is normal. The failure that hides is
`Ready=False` with reason `ActorTemplatePending` that does not clear: the golden boot is failing — a
skill fetch, a missing credential Secret — and gives up as `ActorTemplateFailed`. The Agent's
`status.conditions` (`Accepted`, `ResolvedRefs`, `Compatible`, `Ready`) and `x_agent-manager_get_agent`
are the evidence; the portal shows the same conditions. Name the one fix, do it, call the status
again, stop at `ready`.

## Reading and evaluating a session

Why an agent went in circles, guessed arguments or gave up is in its session; the fix is a change to
its prompt, skills or toolset. agent-manager reads sessions as the person:
`x_agent-manager_list_sessions` (your own; `allCreators` where you are authorized), then
`x_agent-manager_get_session` and `x_agent-manager_get_session_turn` — read their schemas first.

- **In your own context**: `get_session` in the default `summary` to find the turns that matter, then
  `get_session_turn` for one turn, or with `toolCall` for one call's arguments and full response.
- **The whole session, for an evaluation**: `get_session` with `detail: full` and a large `maxBytes`
  (the schema gives the range) answers every turn verbatim — far more than a context window holds.
  Write it to a file from a laptop with the `muster` CLI (not from inside an agent):
  `muster call x_agent-manager_get_session --json '{"sessionId": "…", "detail": "full", "maxBytes": …}' --output json > session.json`
  after `muster auth login`. `omittedTurns` in the answer means it is not whole.
- The answer is everything the agent saw, unredacted: customer data. Keep it out of chats, tickets
  and pull requests.
