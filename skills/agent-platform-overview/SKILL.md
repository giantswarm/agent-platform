---
name: agent-platform-overview
description: Use when someone asks what the Agent Platform is, how it is built or how it works — its components, the groups of MCP servers (connectors), how people reach agents (Dev Portal, Slack, IDE, CLI), how identity flows, how an agent runs, what the Agent Manager is, how to look at the live platform, or how you yourself run on it. Not for discovering tools and toolsets (agent-platform-tools), creating or changing agents and their building blocks (agent-platform-agent-management) or writing skills (agent-platform-skill-authoring).
metadata:
  version: "3.0.0"
---

# The Agent Platform

Concepts: https://docs.giantswarm.io/overview/agent-platform/ (`architecture/`, `security/`,
`authentication/`, `platform-integration/`); how-tos: https://docs.giantswarm.io/tutorials/agent-platform/
(`install/`, `create-an-agent/`, `access-control/`, `troubleshooting/`). Answer in layers — the
paragraph first, then the layer the question needs — and link the page instead of reproducing it.
Tools, toolsets and connectors are the `agent-platform-tools` skill; agents and their building blocks
are `agent-platform-agent-management`; skills are `agent-platform-skill-authoring`.

## The platform in one paragraph

The Agent Platform runs AI agents in production on the installation's own Kubernetes clusters. It has
two capabilities on one foundation: **governed tool access** — every tool call, from an IDE, the Dev
Portal, Slack or an agent, goes through one gateway path (agentgateway in front of **Muster**, the MCP
aggregator that enforces sign-in and fans out to the MCP servers behind it) — and an **agent runtime**
that runs agents as Kubernetes resources, each versioned as one unit (prompt, model, skills, toolset).
The foundation is the cluster and the installation's identity provider: single sign-on, Kubernetes RBAC
and GitOps apply to agents as they apply to people. On the tool path an agent holds no credential of
its own; it acts with the identity of the person it works for.

## The components

One Helm chart, `agent-platform`, renders one Flux `HelmRelease` per component; which components run
is installation configuration. The ones to know by name:

- **Muster** — the MCP gateway: one OAuth-protected `/mcp` endpoint, toolsets, workflows.
- **agentgateway** — the data plane in front of Muster and the runtime.
- **kagent on Agent Substrate** — the agent runtime.
- **agent-manager** — creates, inspects, changes and deletes agents as the caller.
- **model-manager** — model-serving backends and the `ModelConfig`s agents run on.
- **Dev Portal** (Backstage) — the browser front end; **klaus-gateway** — the Slack front door.
- **The Agent Manager** — the platform's own agent (below).

What runs on this installation, with resolved versions: `x_kubernetes_list` with
`resourceType: helmreleases`, `apiGroup: helm.toolkit.fluxcd.io`, `allNamespaces: true` where
mcp-kubernetes is available; the release named `agent-platform` is the chart, its neighbours the
components. Otherwise `x_agent-manager_get_info` names the service version, chart and namespaces.

## Groups of MCP servers

The Dev Portal calls MCP servers **connectors**. Every server the platform ships carries the label
`agent-platform.giantswarm.io/tool-group`, and the presets of the same names select by it:

- **agent-platform** — the platform's own management surface (agent-manager, model-manager and the
  other managers the installation runs) plus Muster's `core_*` tools.
- **infrastructure** — servers for the clusters underneath (mcp-kubernetes and its peers), where the
  installation runs them.
- **Unlabelled** — everything an installation or a person registers.

The live list with descriptions: `filter_tools {"include_presets": true}`.

## How people reach agents

- **Dev Portal**: the rail has **New session**, **Sessions**, **Customize** (tabs Agents, Skills,
  Connectors, Models, Workflows, Workspaces, Toolchains) and **Usage**. Sessions are private to the
  signed-in person; a session's page is `/sessions/<installation>/<session id>`.
- **Slack**, where the installation runs klaus-gateway: one thread is one session; the agent a bare
  mention reaches is the platform setting `klausGateway.a2a.defaultAgent`.
- **IDE and CLI**: Claude Code, Cursor, VS Code, the `muster` CLI and any MCP client with remote OAuth
  connect to the installation's Muster `/mcp` endpoint, the one agents use
  (https://docs.giantswarm.io/getting-started/ai-agent-setup/).

## The Agent Manager

The platform ships one agent of its own, `agent-manager-assistant` ("Agent Manager"), in the runtime's
namespace whenever kagent, Muster and agent-manager run. It manages agents **and their building
blocks** — skills, models, toolsets, connectors, egress policies — as the person asking, with the
`preset:agent-platform` toolset and the platform's skills pinned to the release. The portal's assist
buttons ("Create with …", "Edit with …", "Evaluate with …") start sessions with it. It is
`managed: platform`: agent-manager refuses every change or delete of it; it changes only through the
platform's configuration and releases.

## Identity in five sentences

Every person signs in once through the installation's identity provider (Dex, federating to the
upstream one), and the portal, Slack and IDE clients all forward that person's identity. Agents hold
no credential of their own on the tool path and act with the person's identity end to end. Muster shows
each person only the tools of the servers they are signed in to, and each backend's own authorization —
Kubernetes RBAC, a service's own accounts — decides what a call may do. The audit log names the person.
What an agent's model may discover and call is bounded by its toolset, which never widens what the
person may reach (the `agent-platform-tools` skill has the mechanics).

## How an agent is built and runs

An agent is one Flux `HelmRelease` of the `agent` chart, rendered into a kagent `Agent` that carries its
template and names the `Harness` that runs it. The Harness boots it once and takes a **golden
snapshot**; the Agent reports `Ready`. Every conversation is an actor restored from that snapshot onto a
Substrate worker, running its turns in a sandbox and sending tool calls to Muster as the person, within
the agent's toolset. Any change is a new revision: running sessions keep theirs, new sessions start
from the new one.

## Look at the live platform

Every `x_*`, `core_*` and `workflow_*` name is the `name` argument of Muster's `call_tool`, never a
function of its own (contract: the `agent-platform-tools` skill). Read each schema with `describe_tool`.

- **The agents**: `x_agent-manager_list_agents` — model, Harness, toolset, readiness and `managed`.
- **One agent's readiness**: `x_agent-manager_get_agent_status`.
- **The connectors**: `core_mcpserver_list` — every MCP server with its state.
- **The models**: `x_agent-manager_list_model_configs`; serving backends through `x_model-manager_*`.
- **Skill sources and capabilities**: `x_agent-manager_get_info`.
- **Workflows** the installation offers for whole questions: `filter_tools {"pattern": "workflow_*"}`.
- **Yourself**: `x_agent-manager_get_agent` with your **technical** name — the release name, not the
  runtime's `<name>_<namespace>`; `x_agent-manager_list_agents` when unsure.

## Facts to keep straight

- The platform never mints identity tokens: the identity provider issues every token that carries
  identity.
- The portal is a peer client of the Kubernetes API, not a privileged control panel: what it applies,
  `kubectl` reads back, and GitOps-managed agents change through a pull request.
- A tool that is missing for one person and present for another is sign-in or authorization, not a bug
  of the agent.
