---
name: agent-platform-agent-management
description: Use when asked to create, change, inspect, troubleshoot, re-pin or delete an agent on the Agent Platform, to choose its model, skills, toolset (its tool list and Allow/Ask/Block settings) or egress, or to read or evaluate one of its sessions.
metadata:
  version: "3.4.0"
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
the configured skill sources, a toolset (Muster selectors and presets), its organization's own
connectors (`connectors`), `EgressPolicy`s (the origins it may reach), a Harness. agent-manager picks
and pins them; it never writes model configs or egress policies.

Runtime compute limits belong to the Harness. Platform owners set
`kagent.harness.resources` or each `kagent.harnesses[].resources`; CPU and memory
independently fall back to `kagent.config.runtimeDefaults` (1 CPU and 1Gi).
Session limits stay pinned across Harness sizing changes. Create a Session to use
new limits, and budget the shared worker pool for its actors and runtime overhead.

A toolset's selector count is the `maxItems` of the `toolset` property in
`x_agent-manager_create_agent`'s input schema (`describe_tool`); read it there. From Muster 5.63.7 and
agent chart 2.6.1 it is 128 selectors and 8192 UTF-8 bytes joined with commas; before that, 32.
agent-manager refuses a toolset over the limits: use a preset. The complete serialized Session binding
must fit its 16 KiB JSON body limit, including escaped names and other fields.

Where the reference lives — fetch it, never quote it from memory:

- Managed namespaces, chart and version, the platform Harness, skill-manager (`skillManager`), the
  skills credential, API versions and `capabilities`: `x_agent-manager_get_info`. Skill sources and
  their skills: `agent-platform-skill-authoring`.
- The values schema and the manifests a set of values renders: `x_agent-manager_validate_agent` with
  the intended values returns the composed manifests and every violation, writing nothing. It is also
  how a GitOps `HelmRelease` is drafted: validate, copy. A repeated skill name within one plugin's
  selection produces one violation; name collisions between plugins or with standalone skills are
  also refused.

## How an agent is managed

`x_agent-manager_list_agents` reports `managed` per agent:

- **`helmrelease`** — written live through agent-manager, as you.
- **`gitops`** — applied by Flux from a repository. A live write is refused; with `mode: commit`
  (where `get_info` `capabilities.commit` is true) agent-manager opens the pull request as you.
  Without your grant of its GitHub App, the commit answers with that App's sign-in link.
  Never `force` a write onto it.
- **`platform`** — ships with the Agent Platform (the default agent itself). Every update, re-pin and
  delete is refused; it changes only through the platform's configuration and releases. Say so.
  Its model is set once from the installation's default; `defaultAgent.modelConfig` pins or changes it.
- **`none`** — a bare Agent without a release.
- **`unknown`**: a required Flux read was forbidden, so management details are unavailable.
  `releaseWithheld` explains which release namespace is involved. This is a successful partial
  read, not a sign-in failure. It does not establish that the agent has no release. The editor
  role grants Flux reads in the site's Flux namespace; it does not grant access to a release in
  another namespace such as `flux-giantswarm`. A readable platform label keeps `managed: platform`.

## Working with agent-manager

agent-manager's tools are `x_agent-manager_*` through `call_tool`, acting as the person. Discover
them with `filter_tools` (`pattern`) and read each schema with `describe_tool` before its first call;
never assume an argument. The agent tools drop an unknown key without an error: the prompt is
`systemMessage`, a skill is `{name, path, git: {url, ref}}`, never a bare name. The order:

1. `x_agent-manager_get_info` and the owner (`x_agent-manager_list_organizations`: each
   organization's `namespace`), then the choices from live lists: `x_agent-manager_list_model_configs`
   for that `namespace` and for `kagent` (without `namespace` it lists `kagent` only),
   `x_skill-manager_list_skills` before any prompt is drafted (per source: ref, the commit it resolves
   to, path, readOnly, each skill's description, and each plugin with its `format`: `agent-plugins`
   needs a `skills` selection from its listed skills, `claude-code` loads whole — judge a skill from
   that listing, or read it with `x_skill-manager_get_skill`; `load_skill` knows only your own skills),
   `x_agent-manager_list_egress_policies`, presets from `filter_tools {"include_presets": true}`. A
   skill whose name or description matches the agent's purpose settles what the purpose means:
   propose pinning it.
2. `x_agent-manager_validate_agent` → the plan written out and confirmed (below) →
   `x_agent-manager_create_agent` →
   `x_agent-manager_get_agent_status` until `ready` (about a minute; the golden snapshot is the long
   part) → a first turn as the person (`x_agent-manager_start_session`, where offered). `valid: true`
   is not enough: `manifests.values` holds every field you meant to set, or a key was wrong. Poll the
   status about every 30 s for up to about five minutes; still `progressing`, tell the person its summary and call it
   again at the start of your next turn.
3. `x_agent-manager_update_agent` for follow-ups: only the fields passed change; `skills`, `plugins`,
   `egress` and `toolset` replace their whole list. `refreshSkills: true` re-pins every git skill and
   plugin to the head of its ref, as you: it fails when you cannot read one of the agent's
   repositories.

## The rules the tools do not tell you

- Workspace CLI configuration belongs to the Harness's literal environment.
  The platform supplies inert git values with workspaces enabled and forge CLI
  values for its configured GitHub and GitLab providers;
  `kagent.harness.env` and each `kagent.harnesses[].env` override them by name.
  One GitLab provider supplies its host; with several, explicitly select
  `GITLAB_HOST` on every Harness. Never put a real provider token
  there: the Session grant and gateway binding control access independently.

- **Technical name**: DNS-1123, at most 63 characters, chosen by the person, never derived from the
  display name in silence. It names the release and the Agent; the display name and description are
  what the portal and Slack show.
- **Model**: the cheapest config from the live list that can do the job, never invented — an agent
  on a missing or not-accepted config never boots. No suitable model: a hosted API model with a key is
  `x_agent-manager_set_organization_model` (organization admin, or cluster admin
  for the installation namespace; the key goes into the credential form it
  links). Native Vertex models also take nonsecret `projectID` and `location`;
  upload their JSON only in that credential form. Direct Vertex calls are
  unmetered by the LLM gateway. A local or Hugging Face model is model-manager (`agent-platform-tools`); a
  subscription (OAuth) model — `openAI.credentialType: chatgptOAuth`, `apiFormat: responses` — is the
  platform's configuration in git, which no tool writes.
- **Toolset**: the smallest that does the job; `preset:none` for chat only; the broadest preset only
  on an explicit request. An own-account (OAuth) connector without `grantScope: subject` needs a
  sign-in in each of the agent's conversations (`agent-platform-tools`); say so before choosing it.
- **Tool approval** (https://docs.giantswarm.io/overview/agent-platform/governing-tool-calls/):
  today an agent's toolset only selects tools; their Allow/Ask/Block settings are the connectors'
  (`agent-platform-tools`). On a **governed** platform every agent-manager tool that is not a read
  (`create_agent`, `update_agent`, `delete_agent`, `start_session`, `delete_session`, the
  organization writes) asks the person when an agent calls it; on a trusted one (the default) they
  run, except the floors that ask in both modes (`set_organization_connector`,
  `add_organization_member`, `set_organization_model`, `set_organization_egress_policy`,
  `replace_organization_credential`). Check the `filter_tools` `approval` of the tool before you
  promise an outcome. **Only once agent-manager ships per-agent tool lists** (its `create_agent`
  schema has `tools`, and `x_agent-manager_update_agent_access` and
  `x_agent-manager_set_agent_approval` exist; describe them first): the toolset becomes the agent's
  **tool list**, the same selectors each optionally `Allow`, `Ask` or `Block` plus a `default`, at
  most 32 entries, an unlisted tool invisible; an agent's settings only tighten its connectors'
  (the strictest wins); `Allow` over a connector's `Ask` is an **exception** only a connector admin
  of that connector grants, through `set_agent_approval`, which asks the person whenever an agent
  calls it, and a save drops the exceptions the saver is not connector admin for (say so before
  saving). Until then `update_agent` still takes `toolset`, skills, plugins and egress. A governed
  platform's asks cannot be lifted per agent. `muster.requireApproval: true` keeps asking before
  every call until agent chart 3.0 moves it to `default: Ask`; never set it together with tool
  settings.
- **An organization's connectors**: a kagent `RemoteMCPServer` in the organization namespace
  (`x_agent-manager_set_organization_connector`), not a Muster server, so no `toolset` selector
  reaches it. From agent-manager 3.3.0 (`connectors` in `x_agent-manager_create_agent`'s schema;
  without it the agent takes none), pass the names of the connectors that
  `x_agent-manager_list_organization_connectors` gives as `connectors` to `create_agent`,
  `validate_agent` or `update_agent`: each name alone, once. On update the list replaces the
  agent's connectors, `[]` removes them, and an absent argument keeps them; `get_agent` and
  `list_agents` return them. The agent calls the connector itself with the connector's credential;
  its host needs no `EgressPolicy`. Choose only a usable one (`agent-platform-tools`). Refused: a
  name that does not exist, an agent on a coding Harness, in the platform namespace or on a
  workload cluster, and an agent's binding to Muster: the `RemoteMCPServer` that one HelmRelease
  renders with an agent of its name, which the list marks `musterBindingOf` from agent-manager
  3.3.4 (how to tell it without the field: `agent-platform-tools`). From agent-manager 3.3.4,
  `update_agent` and `validate_agent` refuse `connectors`, `[]` too, on an agent whose HelmRelease
  has `spec.valuesFrom` (the message names it): tell the person that this agent's connectors are
  set where its `valuesFrom` values are managed, not through the tool.
- **Toolchains and workspace**: `defaultToolchains` and `defaultWorkspace`, the smallest set the job
  needs, and only where the installation offers them (both are opt-in: `x_toolchain-manager_*`,
  `x_workspace-manager_*` in `filter_tools`); otherwise leave them out.
- **Skills**: always pinned (the tools pin at write time); pins and private repositories:
  `agent-platform-skill-authoring`.
- **Egress**: none unless the job needs an origin; a named `EgressPolicy` where one fits, raw origins otherwise.
  Tenant admins write policies; a change to one reaches its agents from their next session.
- **Credentials**: an existing model key or egress credential is named in the lists —
  `x_agent-manager_list_model_configs` (each model with a key) and
  `x_agent-manager_list_egress_policies` (each destination with a credential) give its Secret as
  `credentialSecret` and `credentialKey`: names only, never a value. Pass them as `secret` and `key`
  to `x_agent-manager_replace_organization_credential`, or as `credentialSecret` and `credentialKey`
  to `x_agent-manager_set_organization_model` and `x_agent-manager_set_organization_egress_policy`;
  never ask the person for them. A change of a model's provider, or of a Vertex model's project,
  needs a new `credentialSecret` that does not exist yet.
- **System prompt**: role, voice and rules; knowledge goes into skills. Every agent with tools carries
  the meta-tool paragraph from `references/system-prompt.md`.
- **Icon**: `iconUrl` only where the installation serves avatars; omit it otherwise.
- **Confirm every choice you made for the person** — the technical name above all, and the display
  name, description and system prompt verbatim — and wait for a yes before writing. Write the plan in
  your reply text first: owner (the organization), name, display name, description, harness, model,
  toolset, connectors, skills, egress and system prompt. The person sees neither your tool calls' arguments nor `validate_agent`'s output,
  and an `ask_user` question carries no plan, so never point at "the plan above" you have not written
  out; when the person says they see none, write it out instead of asking again. When a tool cannot
  express what the person confirmed, stop and say so; never run a different write instead.
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

When `releaseWithheld` is present, the verdict comes from the Agent alone and the summary states
that HelmRelease state was not read. Report that limitation: a ready Agent does not establish that
its owning release is healthy or that a requested release update has completed. Use the readable
conditions as evidence and leave unavailable release details unknown. Writes still require access
to the owning release.

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
