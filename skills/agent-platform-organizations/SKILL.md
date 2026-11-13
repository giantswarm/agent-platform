---
name: agent-platform-organizations
description: Use when someone asks who owns an agent, why they can or cannot see or start one, what an organization (tenant) is, how to share an agent with everyone or with a team, how to join an organization, or who manages model configs and egress policies.
metadata:
  version: "1.0.0"
---

# Organizations: who owns an agent, who may use it

Kubernetes RBAC decides everything here; the portal and Slack only show the result. Read the live
answer, never guess: `x_agent-manager_list_agents` and `x_agent-manager_get_agent` name each
agent's namespace, `x_agent-manager_list_organizations` the organizations the person manages or belongs to.
Access set-up: https://docs.giantswarm.io/tutorials/agent-platform/access-control/.

## Owners

Every agent lives in one namespace, and that namespace is its owner (agent-manager and Kubernetes say
**tenant**; the portal says **Owner** and never "tenant"):

- **Platform**: the installation's `kagent` namespace. Its agents are available to everyone signed in;
  the portal shows "Owner: Platform", "Available to all organizations". The default agent (`default-agent`) is one.
- **An organization**: a team or customer with its own namespace, members, models, connectors and
  egress policies. Its agents are for its members only. The platform creates the namespace
  (`agents-<name>-<random>`); take it from the tools, never build it from the name.

Organizations are an installation's opt-in. Without them every agent is the platform's. A Giant Swarm
`org-<name>` namespace is not an Agent Platform organization.

## Who sees and starts an agent

A role bound in the agent's namespace:

| Role | May |
|---|---|
| `agent-platform:user` | see the agents and start sessions with them |
| `agent-platform:editor` | the above, and create, change and delete agents |
| `agent-platform:admin` | the above, and manage models, egress policies, credentials and members |

- **Platform namespace**: everyone signed in is a user (the installation can narrow it to a group);
  the platform's operators and the customer's platform team are editors.
- **Organization**: its admins add members by role (portal: **Organizations** → the organization →
  **Members**). A person in no organization sees who can add them on the **Organizations** page.

Each list holds only what the person may see, so two people get different lists. An agent someone
cannot find is a missing role, not a bug: name the owner, and who grants the role there.

## Making an agent available to everyone

Only agents owned by the Platform reach every organization. Creating one needs the editor role in the
platform namespace, usually the platform team. In the **New agent** form, set **Owner** to
**Platform**. Editing never moves an agent: to share an organization's agent platform-wide, create it
again with Owner **Platform** and delete the old one once the person confirms.

To share with one team instead, create it in that team's organization.

## Who manages models and egress policies

- **Platform models**: cluster admins use the same model and credential tools with
  the installation namespace, or the installation's configuration in git. The
  credential operation declares `scope: platform`; it is not an organization.
  This also works with organizations disabled: the installation form uses
  `agent-manager.credentials.platformUIURL` and downstream OAuth independently.
- **An organization**: its admins, on the organization's **Models** and **Egress** tabs (or
  `x_agent-manager_set_organization_model`, `x_agent-manager_set_organization_egress_policy`). A
  model key or credential goes into the portal's credential form and is never shown back, nor passed
  through a tool or chat.
  When the key already exists, Add model completes immediately and closes its dialog.
  Managed models do not offer Replace key. On the credential form, Check again refreshes
  the request and clears entered values; Save and Cancel wait until the refresh finishes.
  Opening another request starts a separate form with no earlier values or result.
  A saved or cancelled request keeps its result without an extra confirmation read.
  Direct credential saves and their recovery tolerate controller status updates.
  Configuration, credential binding or consumer identity changes still require a new preparation. A failed
  save can have written credentials already; refresh its confirmation and follow
  the same operation's recovery instead of starting another request.
- **Vertex models**: use native `AnthropicVertexAI` or `GeminiVertexAI` with
  nonsecret `projectID` and `location`. Upload the service-account JSON only in
  the linked credential form when it requests `google-service-account-json`.
  Google calls are direct; LLM gateway metering, rate limits and spend reporting
  are unavailable. Never read, paste or
  send a person's credential file through an MCP tool.
- **Organizations themselves**: created and deleted by holders of
  `agent-platform:organizations-admin` (platform staff, or a customer's platform team).

An agent uses its own namespace's model configs and egress policies. Choosing them for an agent:
`agent-platform-agent-management`.
