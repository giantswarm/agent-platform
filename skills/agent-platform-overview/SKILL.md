---
name: agent-platform-overview
description: Use when someone asks what the Giant Swarm Agent Platform is, how it is built or how it works — its components, the three groups of MCP servers, how people reach agents (portal, Slack, IDE, CLI), how identity flows, how an agent runs, how to look at the live platform, or how you yourself run on it. Not for discovering tools and toolsets (agent-platform-tools), creating or changing agents (agent-platform-agent-management) or writing skills (agent-platform-skill-authoring).
metadata:
  version: "2.0.0"
---

# The Giant Swarm Agent Platform

Concepts: https://docs.giantswarm.io/overview/agent-platform/ (`architecture/`, `security/`,
`authentication/`, `platform-integration/`); how-tos: https://docs.giantswarm.io/tutorials/agent-platform/
(`install/`, `create-an-agent/`, `access-control/`, `multi-cluster-access/`, `troubleshooting/`). Answer in
layers — the paragraph first, then the layer the question needs — and link the page instead of reproducing
it. Tools, toolsets, MCP servers and workflows are the `agent-platform-tools` skill; creating, changing and
troubleshooting agents is `agent-platform-agent-management`; writing skills is `agent-platform-skill-authoring`.

## The platform in one paragraph

The Agent Platform is Giant Swarm's open, sovereign environment for running AI agents in production on
the customer's own Kubernetes clusters. It has two capabilities on one foundation: **governed tool
access** — every tool call, from an IDE, the portal, Slack or an agent on the cluster, goes through one
gateway path (agentgateway in front of **Muster**, the MCP aggregator that enforces sign-in and fans out
to the MCP servers behind it) — and an **agent runtime** that runs agents as Kubernetes resources,
versioned as one unit (prompt, model, skills, toolset), and surfaces them in the developer portal and in
chat. The foundation is the cluster and the customer's existing identity provider: single sign-on,
Kubernetes RBAC and GitOps apply to agents exactly as they apply to people. An agent never holds a
credential of its own on the tool path; it acts with the identity of the person it works for.

## The components

| Component | What it does |
|---|---|
| **Muster** | The MCP gateway: one OAuth-protected endpoint that aggregates every MCP server, enforces sign-in, evaluates toolsets and runs workflows |
| **agentgateway** | The data plane in front of Muster and the gRPC path to the agent runtime — where tool traffic becomes observable and policy applies |
| **Edge Gateway** | TLS termination and the public hostnames; a Gateway API gateway, the cluster's shared one on Giant Swarm installations |
| **Dex** | The installation's OIDC identity provider, federating to the customer's upstream (GitHub, Entra ID, Google, …); the only token authority |
| **Agent runtime** | kagent on Agent Substrate: agents as Kubernetes resources, every conversation a sandboxed actor restored onto a pool of warm workers |
| **agent-manager** | Creates, inspects, changes and deletes agents as the caller; behind the portal's create flow and the `x_agent-manager_*` tools |
| **model-manager** | The models an installation serves itself and wires into the runtime's `ModelConfig`s; optional, only where a serving backend exists |
| **mcp-kubernetes, mcp-capi, mcp-prometheus** | The management clusters' resources, Cluster API objects and metrics through MCP, one instance per cluster |
| **Developer portal** | The Agent Platform section of the installation's Backstage: servers, tool explorer, workflows, agents, sessions, models |
| **klaus-gateway** | The Slack front door: a thread is a session, replies carry the agent's name, tools run as the person |

Everything ships as one Helm chart, `giantswarm/agent-platform`, rendering one Flux `HelmRelease` per
component with a version range. Giant Swarm management clusters run it through their own Flux; any
other cluster installs it with one `helm install` (`tutorials/agent-platform/install/`).

## Three groups of MCP servers

The portal, the presets and the docs use the same three names:

- **Agent Platform** — the platform's own management surface: agent-manager, model-manager and
  Muster's `core_*` tools.
- **Infrastructure** — the servers for the management clusters underneath: mcp-kubernetes, mcp-capi,
  mcp-prometheus. Across a fleet each appears once, with `management_cluster` selecting the cluster.
- **Registered servers** — everything an installation or a person registers: issue trackers, source
  control, chat, internal services, vendor APIs.

## How people reach agents

- **Developer portal** → Agent Platform → Agents: pick an agent, start a session, watch the tool calls,
  answer its questions; sessions are private to the signed-in person. Every installation has a portal at
  its own URL (https://docs.giantswarm.io/overview/developer-portal/agent-platform/).
- **Slack**, where the installation runs klaus-gateway: the installation's Slack app; one thread is one
  session; a one-time sign-in links the Slack account to the person.
- **IDE and CLI**: Claude Code, Cursor, VS Code, the `muster` CLI and any MCP client with remote OAuth
  connect to `https://muster.<installation>.<base domain>/mcp`, the endpoint the agents use too
  (https://docs.giantswarm.io/getting-started/ai-agent-setup/).

## Identity in five sentences

Every person signs in once through the installation's Dex, and the portal, Slack and IDE clients all
forward that person's identity. Agents hold no credential of their own on the tool path and act with
the person's token end to end. Muster shows each person only the tools of the servers they are signed
in to, and each backend's own authorization — Kubernetes RBAC for mcp-kubernetes, a service's own
accounts — decides what a call may do. The audit log names the person, and the platform records which
agent acted for them. What an agent's model may discover and call is bounded by its toolset, which never
widens what the person may reach (the `agent-platform-tools` skill has the mechanics).

## How an agent is built and runs

An agent is one Flux `HelmRelease` of the `agent` chart — the unit the `agent-platform-agent-management`
skill creates and changes, rendered into an `Agent` that carries its template inline (`spec.template`) and
names the `Harness` that runs it (`spec.harnessRef`). The Harness boots it once on a worker, materialises
the skills and takes a **golden snapshot**; the Agent reports `Ready`. Every conversation is an
`AgentInstance`: an actor restored from that snapshot onto a worker pod, running the turn in a sandbox,
sending its tool calls to Muster with the person's forwarded token and the toolset header, checkpointed
between turns. The transcript lives in the runtime's Postgres, the actor's memory in the snapshot.

## Look at the live platform

Every `x_*` and `workflow_*` name here is the `name` argument of Muster's `call_tool` meta-tool, never
a function of its own; the contract, the name shapes and the `management_cluster` argument are in the
`agent-platform-tools` skill. Prefer the workflow where one exists: one call, one shaped answer.

- **The components**: `x_kubernetes_list` with `resourceType: helmreleases`,
  `apiGroup: helm.toolkit.fluxcd.io`, `allNamespaces: true` — the release named `agent-platform` is the
  chart; its neighbours in that namespace are the components with their resolved versions.
- **The agents**: `workflow_agent-roster` — every agent with management mode, readiness, model, toolset
  and skills.
- **The servers**: `workflow_platform-servers` — every MCP server with its state and tool group.
- **The models**: `workflow_model-overview` — the `ModelConfig`s and, where model-manager runs, its
  backends, models and capacity.
- **The runtime's resources**: `x_kubernetes_list` with `apiGroup: api.kagent.dev` and `resourceType`
  `agents`, `agenttemplates`, `harnesses`, `remotemcpservers` or `modelconfigs`, in the namespace `x_agent-manager_get_info`
  reports (it also names the agent chart, the Harness and the skill repositories).
- **Yourself**: `x_agent-manager_get_agent` with your **technical** name — the release name, not the
  `<name>_<namespace>` the runtime calls you; `x_agent-manager_list_agents` when unsure.

## Facts to keep straight

- The platform never mints identity tokens: Dex issues every token that carries identity; Muster's own
  access tokens are opaque keys into its session store.
- Machine identity for autonomous agents does not exist yet; every turn has a person behind it.
- The portal is a peer client of the Kubernetes API, not a privileged control panel: what it applies,
  `kubectl` reads back, and GitOps-managed resources are read-only in it.
- In Slack every participant of a thread must be signed in before anything is dispatched (no
  service-account fallback) and Slack guests cannot use the app; the agent a bare mention reaches is a
  platform setting (`klausGateway.a2a.defaultAgent`).
