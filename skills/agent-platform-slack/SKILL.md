---
name: agent-platform-slack
description: Use when someone asks how to use agents from Slack — starting a conversation, reaching the default agent or a specific one, threads, signing in, commands like stop or mute, bringing others into a thread — when what they describe happened in Slack, or when you answer in Slack and must know what does not work there.
metadata:
  version: "1.0.0"
---

# Agents in Slack

Where the installation runs klaus-gateway, people talk to agents in Slack: in channels the app is in
and in its direct message. Every turn runs as the person who wrote it, with their permissions, as
in the portal.

## Starting a conversation

A conversation starts only through the agent picker, which preselects the default agent (the
platform setting `klausGateway.a2a.defaultAgent`, unset: the default agent, `default-agent`). Pick another
agent there to reach a specific one.

- **The slash command** (`/swarmgeist [question]` unless the installation renamed it; `help` names
  it): in a channel or the direct message, opens the picker and starts a new thread.
- **Ask an agent here** (a message's ⋯ menu → Apps): starts in that message's thread, for example
  under an alert. The agent reads the thread's earlier messages.
- **`agents`**: lists the agents the person may use, each with **Select**.

A mention of the app in a thread without a conversation starts nothing: it gets the help.

## A thread is a session

One thread is one conversation with one agent, for its whole life. Reply in the thread to go on;
no mention needed. To use another agent, start a new thread. A conversation ends after a long
silence (90 days unless the installation changed it); start a new one then.

- The person who started it owns it. Someone else who writes in the thread is let in only once the
  owner allows them; their turns then run as themselves.
- A reply that starts with another person's mention (`@alice can you check?`) is not for the agent.
- Commands, as the word alone in the thread or the direct message: `stop` (a running turn), `mute`
  (until someone mentions the app), `usage`, `help`.
- Replies do not show the agent's tool calls; **Inspect agent steps** (⋯ → Apps) does, to the person
  alone.
- Questions and approvals come as buttons; the thread shows the agent is waiting.

## Signing in

Two separate sign-ins, each once:

1. **Giant Swarm**: the first message gets a **Sign in** button (`login` asks for it again). It
   links the Slack account to the person's platform identity; the Slack profile email must match.
   The message runs once they are signed in.
2. **A connector's own account** (GitHub and the like): a **Connect** button when an agent first
   needs it. `/swarmgeist connect` authorizes them all in one place.

In a channel, sign-in links go to the person alone and to the app's direct message.

## What does not work in Slack

- **Start links** (`/?agent=…`) are portal links and do not resolve: name the agent and say how to
  pick it (the slash command), or where it is in the portal.
- **Portal pages** (Customize, Organizations, Usage, the credential form for a key): what you can do
  with your tools, do here; for a page the person must open, name it in words
  (`agent-platform-portal`).
