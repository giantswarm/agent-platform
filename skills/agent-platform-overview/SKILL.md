---
name: agent-platform-overview
description: Use when someone asks what the Agent Platform is, how it is built or works — components, connectors, how people reach agents (Dev Portal, Slack, IDE), identity, how an agent runs, what the default agent is, or how to look at the live platform or yourself.
metadata:
  version: "3.0.0"
---

# The Agent Platform

Concepts: https://docs.giantswarm.io/overview/agent-platform/ (`architecture/`, `security/`,
`authentication/`, `platform-integration/`); how-tos: https://docs.giantswarm.io/tutorials/agent-platform/
(`install/`, `create-an-agent/`, `access-control/`, `troubleshooting/`). Answer in layers — the
paragraph first, then the layer the question needs — and link the page instead of reproducing it.
Tools, toolsets and connectors are the `agent-platform-tools` skill; agents and their building blocks
are `agent-platform-agent-management`; which agent fits a task and start links are
`agent-platform-agent-routing`; skills are `agent-platform-skill-authoring`.

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
is installation configuration — Muster and agentgateway (the tool path), kagent on Agent Substrate
(the runtime), agent-manager and model-manager (agents and models), the Dev Portal and klaus-gateway
(the browser and Slack front doors). What runs here, with resolved versions: `x_kubernetes_list` with
`resourceType: helmreleases`, `apiGroup: helm.toolkit.fluxcd.io`, `allNamespaces: true` where
mcp-kubernetes is available; the release named `agent-platform` is the chart, its neighbours the
components. Otherwise `x_agent-manager_get_info` names the service version, chart and namespaces.

## Connectors

The Dev Portal calls MCP servers **connectors**; the platform's own are grouped into the
`agent-platform` and `infrastructure` presets, everything else is registered by the installation or
a person. Groups, states and sign-in: the `agent-platform-tools` skill; the live presets:
`filter_tools {"include_presets": true}`.

## How people reach agents

- **Dev Portal**: the rail has **New session**, **Sessions**, **Customize** (tabs Agents, Skills,
  Connectors, Models, Workflows, Workspaces, Toolchains) and **Usage**. Sessions are private to the
  signed-in person; a session's page is `/sessions/<installation>/<session id>`.
- **Slack**, where the installation runs klaus-gateway: one thread is one session; a bare mention
  reaches the default agent unless the platform setting `klausGateway.a2a.defaultAgent` names another.
- **IDE and CLI**: Claude Code, Cursor, VS Code, the `muster` CLI and any MCP client with remote OAuth
  connect to the installation's Muster `/mcp` endpoint, the one agents use
  (https://docs.giantswarm.io/getting-started/ai-agent-setup/).

## The default agent

The platform ships one agent of its own, `default-agent` ("General Assistant"), in the runtime's namespace
whenever kagent, Muster and agent-manager run, labelled `agent-platform.giantswarm.io/default: "true"`.
It is where people start: it does one-off tasks with their connectors (`preset:full`, as the person),
recommends an existing agent or helps create a specialist (`agent-platform-agent-routing`), and
manages agents and their building blocks, with the platform's skills pinned to the release. The portal's assist
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
- **The models**: `x_agent-manager_list_model_configs`; serving: the `agent-platform-tools` skill.
- **The skills**: `x_skill-manager_list_skills`; their sources: `x_skill-manager_list_skill_sources`.
- **Capabilities**: `x_agent-manager_get_info`.
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
