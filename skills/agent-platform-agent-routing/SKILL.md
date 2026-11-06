---
name: agent-platform-agent-routing
description: Use when a task might suit another agent than you, when someone asks which agent to use or whether one exists for a task, when a task repeats, runs on a schedule or is an automation, or when you write a link that starts a session with an agent.
metadata:
  version: "1.0.0"
---

# Routing a task to the right agent

You do one-off tasks yourself. This skill decides when a task belongs to another agent, finds it, and
hands the person over with a link. Creating an agent is `agent-platform-agent-management`.

## When a task deserves a specialist

Suggest a specialist agent when one of these holds:

- **It repeats**: the person will ask the same thing again (a weekly report, every new incident,
  every pull request).
- **It runs on a schedule or a trigger**: a time, an alert, a Slack message, an event.
- **It is an automation**: it should run without the person in the loop.
- **It needs its own setup**: a narrower or different toolset, its own skills, a prompt that encodes a
  procedure, or a model the default one is not.
- **Others need it**: a team should reach the same behaviour, in the portal or Slack.

Otherwise do the task. Never refuse a one-off task because an agent exists for it: do it, and mention
the agent once.

## Find an existing agent

Fetch the live list, never from memory: `x_agent-manager_list_agents` through `call_tool` (read its
schema with `describe_tool` first). Judge each agent by:

- **Description** and display name (`ui.giantswarm.io/display-name`): what it is for.
- **Skills and toolset**: what it can reach. A toolset narrower than the task rules it out.
- **Readiness**: only a `ready` agent can start a session. Name a not-ready one only as "starting" or
  "failing", never link it.
- **`default: true`**: that is you. Never route to yourself.

`x_agent-manager_get_agent` reads one agent in full when the list leaves a doubt. The list holds only
the agents the person may see; another person may see more or fewer.

One agent fits: recommend it with a start link. Several fit: name at most three, one line each, and
link each. None fits: say so, and offer to create one.

## Hand over to creation

When the person agrees to a new agent, load `agent-platform-agent-management` and follow it. Bring
what you learned: the job in one sentence, how it is triggered, the connectors it needs, and the
skills that fit. The person chooses and confirms the technical name.

A schedule or trigger is not part of the agent itself. Check what the installation offers (workflows:
`agent-platform-tools`; Slack: `agent-platform-overview`) before promising one; the agent comes first.

Once it is `ready`, give its start link.

## The start link

A relative link to the portal's new-session screen (`/`, the home), same origin, which preselects
the agent and prefills the composer:

```text
[Start a session with <Display name>](/?agent=<agent id>&prompt=<brief>)
```

- **`agent`**: the portal's agent id, `<installation>/<namespace>/<name>`, URL-encoded (`/` becomes
  `%2F`). `<installation>` is the installation's Dev Portal name from your system message;
  `<namespace>` and `<name>` (the technical name) come from `x_agent-manager_list_agents`.
- **`prompt`**: optional. A short brief of the task in the person's terms, URL-encoded (space `%20`),
  at most a few hundred characters. It prefills the composer; the person edits and sends it.
- **Never in the brief**: conversation data — mail or message contents, logs, customer data, secrets,
  tokens. Name the task, not its material.
- Display name, else technical name, in the link text.

Example, for an agent `release-notes` in namespace `kagent` on the installation `gazelle`:

```text
[Start a session with Release Notes](/?agent=gazelle%2Fkagent%2Frelease-notes&prompt=Draft%20the%20release%20notes%20for%20agent-platform%20v4.2.0)
```

An unknown or not-startable `agent` falls back to the default agent in the portal: check readiness
before linking. Outside the portal (Slack, an IDE) the link does not resolve; name the agent and where
to find it instead (Dev Portal, **New session**).
