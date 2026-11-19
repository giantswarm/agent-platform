---
name: agent-platform-agent-routing
description: Use when a task might suit another agent than the one in this session, when someone asks which agent to use or whether one exists for a task, when a task repeats, or when you write a link that starts a session with an agent.
metadata:
  version: "1.1.0"
---

# Routing a task to the right agent

A one-off task stays in the current session when the agent there can do it. This skill decides when a
task belongs to another agent, finds it, and hands the person over with a link. Creating an agent is
`agent-platform-agent-management`.

## When a task deserves a specialist

Suggest a specialist agent when one of these holds:

- **It repeats**: the person will ask the same thing again (a weekly report, every new incident,
  every pull request).
- **It needs its own setup**: a narrower or different toolset, its own skills, a prompt that encodes a
  procedure, or a model the default one is not.
- **Others need it**: a team should reach the same behaviour, in the portal or Slack.

Otherwise do the task where your toolset allows it. Never refuse a one-off task because an agent
exists for it: do it, and mention the agent once. A task outside your toolset goes to an agent that
can do it, the default agent included.

## Why a specialist agent

- **Cost**: it runs on the cheapest model that does its job and carries only the skills and
  instructions that job needs, so each turn has less context to pay for.
- **Security**: least privilege — a toolset and toolchains limited to the job, a model fit for the
  data it handles, and an owner who answers for it.

To a newcomer, in two sentences: "An agent built for this job does it better and cheaper, because it
carries only what the job needs. It also reaches only the tools the job needs, so it can do less harm."

Why the default agent is broad: it handles a wide range of simple tasks, so someone with a one-off
task needn't create an agent and start a new session first. That is convenience over efficiency; a
specialist does better and costs less on the tasks it is built for.

## Find an existing agent

Fetch the live list, never from memory: `x_agent-manager_list_agents` through `call_tool` (read its
schema with `describe_tool` first). Judge each agent by:

- **Description** and display name (`ui.giantswarm.io/display-name`): what it is for.
- **Skills and toolset**: what it can reach. A toolset narrower than the task rules it out.
- **Readiness**: only a `ready` agent can start a session. Name a not-ready one only as "starting" or
  "failing", never link it.
- **`default: true`**: the default agent. Never route to the agent you are (how to look yourself up:
  `agent-platform-overview`).

`x_agent-manager_get_agent` reads one agent in full when the list leaves a doubt. The list holds only
the agents the person may see; another person may see more or fewer.

`releaseWithheld` means the Agent is readable but release details are unavailable. Use the Agent's
actual readiness when deciding whether it can start a session; do not infer a missing release from
`managed: unknown`, or describe the release as healthy from an Agent-only verdict.

One agent fits: recommend it with a start link. Several fit: name at most three, one line each, and
link each. None fits: say so, and offer to create one.

## Hand over to creation

When the person agrees to a new agent, load `agent-platform-agent-management` and follow it. Bring
what you learned: the job in one sentence, the connectors it needs, and the skills that fit. The
person chooses and confirms the technical name.

Once it is `ready`, give its start link.

## The start link

A relative link to the portal's new-session screen (`/`, the home), which preselects the agent and
prefills the composer. It needs a portal that resolves an agent by namespace and name (Dev Portal
3.38.0 or later):

```text
[Start a session with <Display name>](/?agent=<namespace>%2F<name>#prompt=<brief>)
```

- **`agent`**: `<namespace>/<name>`, URL-encoded (`/` becomes `%2F`). Both come from
  `x_agent-manager_list_agents`; `<name>` is the technical name. Leave the installation out: the
  portal finds the agent by namespace and name across its installations, and the installation name
  it uses may not be the one agent-manager reports.
- **`prompt`**: optional, after `#` so it stays out of server logs. A short brief of the task in the
  person's terms, URL-encoded as a URI component (space `%20`, `#` `%23`, `&` `%26`), at most a few
  hundred characters; the portal keeps 2000. It prefills the composer; the person edits and sends it.
- **Never in the brief**: conversation data — mail or message contents, logs, customer data, secrets,
  tokens. Name the task, not its material.
- Display name, else technical name, in the link text.
- **Relative, never a guessed host**: the link opens in the portal the person is reading it in.

Example, for an agent `release-notes` in namespace `kagent`:

```text
[Start a session with Release Notes](/?agent=kagent%2Frelease-notes#prompt=Draft%20the%20release%20notes%20for%20agent-platform%20v4.2.0)
```

When the portal cannot select the linked agent (not found, several agents with that namespace and
name, cannot start, or its environment unreachable), it keeps its default agent, says above the
composer why, and the person picks one. Check readiness before linking.

Outside the portal (Slack, an IDE) a relative link does not resolve. Give the path and say it is
relative to the person's Dev Portal, or name the agent and where to find it (**New session**);
never guess the portal's address.
