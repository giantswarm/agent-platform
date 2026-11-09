---
name: agent-platform-portal
description: Use when someone asks where something is in the Dev Portal or how to do it there — starting a session, the rail, the Customize tabs (Agents, Skills, Connectors, Models, Workflows, Workspaces, Toolchains), Usage, Organizations, the assist buttons that open a session with you — or when you point a person to a portal page or give a start link.
metadata:
  version: "1.0.0"
---

# The Dev Portal

The portal's agent pages are the browser front door: sessions with agents, and the place to set up
what agents use. Everything there acts as the signed-in person; what they may not do is not offered
or is refused. Some parts appear only where the installation runs them, noted below. Point to a
place by its label in **bold**, the path behind it after an arrow: **Customize** → **Connectors**.

## The rail

- **New session** (`/`): pick an agent, write the first message. Where the installation offers them,
  it also picks a workspace and toolchains (`agent-platform-building-blocks`).
- **Search** (⌘K): find sessions, agents, skills, connectors, models and workflows.
- **Sessions**: pinned ones, those waiting for the person (a question or an approval), then the
  latest; **All sessions** (`/sessions`) lists the rest. Only the person's own, never anyone else's.
- **Customize** (`/customize`): the tabs below.
- **Usage** (`/usage`): model spend, tokens and tool calls over 7, 30 or 90 days or a calendar month,
  for the whole platform, by organization, and for the person's own sessions.
- **Profile and settings** (a menu): **Organizations**, the person's profile, sign out. The bell
  beside it is the notification inbox: sessions that need the person and connectors waiting for
  their sign-in.
- **Platform Portal**: back to the classic portal, only where the installation serves it.

## Customize

| Tab | What a person does there |
|---|---|
| **Agents** | Browse and favourite agents, open one, **Use this agent** to start a session, **New agent** (a wizard: Details, Skills, Tools, Review). Edit, update skills or delete, where allowed. |
| **Skills** | Browse the skill catalogue; **Add skill** and **Edit** open a pull request as the person. |
| **Connectors** | The MCP servers (`agent-platform-building-blocks`): sign in to one, browse and run its tools, **Add connector**. |
| **Models** | The models agents run on; **Connect a model** (OpenAI-compatible, Anthropic, Gemini, Ollama). |
| **Workflows** | Read and run workflows; they are created by agents and Git, not in the portal. |
| **Workspaces** | Only where the installation runs workspaces: choose which of an organization's repositories sessions work on. |
| **Toolchains** | Only where the installation runs toolchains: browse them and their sources, **New toolchain** (a pull request). |

An environment selector appears when the portal serves more than one installation.

**Organizations** (Profile menu): the person's organizations, each with **Members**, **Models**,
**Connectors** and **Egress** tabs (`agent-platform-organizations`).

## The assist buttons

These open a session, or a side chat, with an agent already briefed; each names its agent, by
default you.

- **Agents**: the banner "Not sure which agent fits?", **Ask … about agents**.
- **New agent**: **Set up the agent with …** drafts the instructions and suggests skills in a side
  chat; the form changes only when the person accepts.
- **Skills**: **Write complex skills with …** on Add and Edit skill; **Evaluate with …** on a
  skill's page.
- **Workflows**: **Create with …**, and **Edit with …** on a workflow's page.

The session's first message says which task it is; follow the skill for it.

## Start links

A link that opens **New session** with an agent preselected and the first message prefilled; the
person reads and sends it:

```text
[Start a session with <Display name>](/?agent=<installation>%2F<namespace>%2F<name>#prompt=<url-encoded brief>)
```

Format, checks and what never goes into the brief: `agent-platform-agent-routing`. The link works only
inside the portal; in Slack or an IDE, name the agent and say where to find it.
