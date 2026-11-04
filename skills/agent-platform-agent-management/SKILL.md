---
name: agent-platform-agent-management
description: Use when asked to create, change, inspect, troubleshoot, re-pin the skills of or delete an agent, or to evaluate one of its sessions, on the Giant Swarm Agent Platform — through agent-manager's tools (x_agent-manager_* via call_tool), the developer portal's create flow, or a GitOps HelmRelease of the agent chart. Covers the agent unit on the cluster and where its live reference is, which agents get a pull request instead of a live write, the working shape from validate to ready, the rules for the technical name, model config, toolset, skills and system prompt, the status verdict with its one follow-up, and reading a whole session for an evaluation.
metadata:
  version: "2.1.0"
---

# Managing agents

Public pages: https://docs.giantswarm.io/tutorials/agent-platform/create-an-agent/ (the portal
flow) and https://docs.giantswarm.io/tutorials/agent-platform/troubleshooting/. Muster's meta-tools,
the `call_tool` contract and toolsets are the `agent-platform-tools` skill; the skill format and
what a pin is are the `agent-platform-skill-authoring` skill. The one reference here is
`references/system-prompt.md`: the paragraph every tooled agent's prompt needs, and a template.

## The agent unit

One agent is one Flux `HelmRelease` of the `agent` chart in the agents' namespace that `x_agent-manager_get_info` reports. helm-controller
renders an `Agent` — its template inline (prompt, model config, pinned skills and plugins, tool
bindings) and the `Harness` that runs it by name — and, for a tooled agent, a `RemoteMCPServer` named
after the agent that points at Muster with the agent's toolset. The Harness compiles the golden
snapshot and the Agent reports `Ready`. Any change is a new revision: running sessions keep their
actor, new sessions start from the new one.

Where the reference lives — fetch it, never quote it from memory:

- Chart source and version range, resolved chart and schema version, managed namespaces, Harness,
  skills repositories, API versions and capabilities: `x_agent-manager_get_info`.
- The values schema and the manifests a set of values renders: `x_agent-manager_validate_agent`
  with the intended values returns the composed `HelmRelease` (and `OCIRepository`) and every
  violation, writing nothing. It is also how a GitOps `HelmRelease` is drafted: validate, copy.
- The CRDs and the live objects: `x_kubernetes_api_resources` for the `api.kagent.dev` group, then
  `x_kubernetes_get` of `agents`, `harnesses`, `remotemcpservers`, `modelconfigs` in the agents' namespace and of
  `helmreleases` for the releases; `agenttemplates` when an Agent names one in `spec.templateRef`
  instead of carrying its template inline. The conditions are in the Agent's `status.conditions`.

## Three doors, two ways to change

The portal's create flow, agent-manager's tools and a GitOps repository write the same unit.
`workflow_agent-roster` shows every agent with its management mode: `helmrelease` is written live
through agent-manager; `gitops` is applied by Flux and changes through a **pull request** — a live
write is refused, and a `force`d one is reverted at the next reconcile with a misleading audit
trail; `none` is a bare Agent without a release. Where the pull request goes: the release's
`kustomize.toolkit.fluxcd.io/name` and `/namespace` labels name the Flux `Kustomization` that
applies it, and its `sourceRef` is the repository. The portal shows the Git source on such agents.

## Working with agent-manager

agent-manager's tools are `x_agent-manager_*` through `call_tool`, acting as the person
(`writesAsCaller`). The `name` you pass is always the full catalogue name, as `filter_tools` returns
it — `x_agent-manager_list_agents`, never `list_agents`; a short name is refused as outside the
toolset. Discover them live with `filter_tools` (`pattern`) and read each schema with
`describe_tool` before the first call; never assume an argument. `x_agent-manager_get_info` comes
first. Model configs come from `workflow_model-overview` (or `x_agent-manager_list_model_configs`),
skills from `x_agent-manager_list_skills`, which also shows the head commit a pin resolves to and
each skill's description — a skill you assign is judged from that listing, never loaded
(`load_skill` knows only your own skills). Then `x_agent-manager_validate_agent` →
`x_agent-manager_create_agent` → `workflow_agent-status` until `ready` (about a minute; the golden
snapshot is the long part) → a first turn as the person → `x_agent-manager_update_agent` for
follow-ups. `x_agent-manager_update_agent` replaces `skills` and `toolset` as whole lists; `refreshSkills: true` re-pins every git skill to its default-branch
head. A GitOps agent re-pins through a commit that changes the pinned commit.

## The rules the tools do not tell you

- **Technical name**: DNS-1123, at most 63 characters, chosen by the person, never derived from the
  display name in silence. It names the release, the Agent, the `RemoteMCPServer` and the
  avatar. The display name and the description are what the roster, Slack and the portal show.
- **Model config**: one from the live list, never invented — an agent on a missing or not-accepted
  config never boots.
- **Toolset**: the smallest that does the job; the broadest preset only on an explicit request (list presets with `filter_tools` and `include_presets`). What the
  selectors are and how to see what a preset resolves to: the `agent-platform-tools` skill.
- **Skills**: always pinned, and a private skills repository only through GitOps — what a pin is and
  what the release needs for a private repository: the `agent-platform-skill-authoring` skill.
- **System prompt**: role, voice and rules; knowledge goes into skills. Every agent with tools
  carries the meta-tool paragraph from `references/system-prompt.md`.
- **Icon**: `iconUrl` is `https://avatars.<base domain>/v1/<name>.png` where the installation
  serves avatars; the portal shows it.
- **Confirm every choice you made for the person** — the technical name above all — before
  writing. Delete only on an explicit request that names the agent, one at a time. Never `force` a
  write onto a GitOps release.

## Status and troubleshooting

`workflow_agent-status` (`name`, `namespace` = the agents' namespace) is the one call: the verdict — `ready`,
`progressing`, `failed`, `not_found` — with a summary, the release's condition and the golden-worker
log lines that name the agent. The Agent's own conditions (`Accepted`, `ResolvedRefs`, `Compatible`,
`Ready`) are in its `status.conditions`. `progressing` for about a minute after a write is normal.
The failure that hides is `Ready=False ActorTemplatePending` that does not clear: the Harness retries
a failing golden boot a few times with growing backoff, then gives up with `Ready=False
ActorTemplateFailed`, and the cause — a skill fetch that fails, a Secret that is missing — is only
in those log lines. Name the one follow-up the workflow allows, do it, call the workflow again,
and stop when it says `ready`. For the whole namespace, `workflow_agent-roster`. Beyond the
workflows the live objects above are the evidence; the portal shows the same conditions verbatim.

## Evaluating a session

Why an agent went in circles, guessed arguments or gave up is in its session, and the fix is a
change to its prompt, skills or toolset. agent-manager reads a session as the person: only its
owner can, and the id comes from `x_agent-manager_list_sessions`. Read the arguments of
`x_agent-manager_get_session` and `x_agent-manager_get_session_turn` with `describe_tool` first.

- **In your own context**: `get_session` in the default `summary` to find the turns that matter,
  then `get_session_turn` for one turn, or with `toolCall` for one call's arguments and full
  response.
- **The whole session, for an evaluation**: `get_session` with `detail: full` and a `maxBytes` up
  to the range's maximum answers the session and every task verbatim. That is far more than a
  context window holds: write it to a file, for example
  `muster call x_agent-manager_get_session --json '{"sessionId": "…", "detail": "full", "maxBytes": …}' --output json > session.json`
  after a one-time `muster auth login`. Every turn is read with its own call, so a long session
  takes a while. `truncated` or `omittedTurns` in the answer means it is not whole; a maximum of
  1048576 is an older agent-manager, which answers at most 1 MiB and 200 turns.
- The answer is everything the agent saw, unredacted: customer data. Keep it out of chats,
  tickets and pull requests.
