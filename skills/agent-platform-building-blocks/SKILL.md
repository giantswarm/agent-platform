---
name: agent-platform-building-blocks
description: Use when someone new asks what connectors, toolsets, toolchains or workspaces are, how they differ, or which one they need — or when a person's goal needs one they have not set up, so you can suggest it before the mechanics.
metadata:
  version: "1.0.0"
---

# Connectors, toolsets, toolchains, workspaces

Four things decide what an agent can reach and work on. Explain them in the person's terms, then
suggest the one that gets them to their goal. Mechanics (names, sign-in, selectors):
`agent-platform-tools`; setting them on an agent: `agent-platform-agent-management`.

## In one line each

| | What it is | Where it lives |
|---|---|---|
| **Connector** | A service the platform can call, as an MCP server: GitHub, Kubernetes, a ticket system. | **Customize** → **Connectors** |
| **Toolset** | Which of those tools an agent's model may call. | Part of each agent |
| **Toolchain** | Command-line tools (`go`, `node`, …) mounted into a coding agent's sandbox. | **Customize** → **Toolchains** |
| **Workspace** | An organization's repositories, mirrored so a session can work on them. | **Customize** → **Workspaces** |

## Connectors

Each connector offers tools behind Muster, the platform's one tool gateway. Every call runs as the
person: what they may do in the service is what the agent may do there. Some connectors ask the
person to sign in once with their own account; until then their tools are hidden for that person.

**Suggest one** when the goal needs a system no connector reaches yet ("read our Jira tickets"):
someone registers it with **Add connector**. A connector that exists but whose tools are missing
for this person usually needs their sign-in.

## Toolsets

A toolset is the agent's list of allowed tools: whole presets (`preset:read-only`), servers or single
tools. It narrows, never widens: an agent reaches only what is in its toolset **and** what the person
may use. You run with the full set, as the person.

**Suggest one** when a specialist agent should do less than everything: read-only for reporting,
one server for one job. Smaller toolsets are safer and cheaper.

## Toolchains

A toolchain is a versioned set of binaries, mounted read-only into a coding agent's sandbox at
`/opt/kagent/tools/<name>`, with its `bin/` on the `PATH`. It is how an agent gets a compiler, a
CLI or a linter without a custom image. Toolchains come from git repositories (sources) and are
built by their CI. A session can pick toolchains at start; an agent can name default ones.

**Suggest one** when a coding task needs a tool the sandbox lacks ("build this Go service", "run
terraform plan"). **New toolchain** opens the pull request that adds one.

## Workspaces

A workspace is a set of an organization's repositories, chosen by name, language or topic and
mirrored by the platform. A session started in a workspace gets its own working copy. The person
must be able to read every repository in it, checked at start with their own account. An agent can
name a default workspace; New session and the Slack picker let the person choose.

**Suggest one** when the task spans the team's code ("find every service that still uses the old
client", "change this across our repos"): the code is already there, and access is the person's own.

## How they fit

Connectors and toolsets are about **services**: what the agent may call, remotely. Toolchains and
workspaces are about **the sandbox**: which programs and which code the agent works with locally. A
coding task often needs both: a workspace for the code, a toolchain to build it, a GitHub connector
to open the pull request.

Toolchains and workspaces are opt-in per installation: their **Customize** tab appears and their
tools (`x_toolchain-manager_*`, `x_workspace-manager_*` in `filter_tools`) exist only where it runs
them. Absent: say so, and that the installation's operators can turn it on.
