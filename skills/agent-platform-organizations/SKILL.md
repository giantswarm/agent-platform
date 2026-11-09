---
name: agent-platform-organizations
description: Use when someone asks who owns an agent, why they can or cannot see or start one, what an organization (tenant) is, how to share an agent with everyone or with a team, how to join an organization, or who manages model configs and egress policies.
metadata:
  version: "1.0.0"
---

# Organizations: who owns an agent, who may use it

Kubernetes RBAC decides everything here; the portal and Slack only show the result. Read the live
answer, never guess: `x_agent-manager_list_agents` and `x_agent-manager_get_agent` name each
agent's namespace, `x_agent-manager_list_my_organizations` the organizations the person belongs to.
Access set-up: https://docs.giantswarm.io/tutorials/agent-platform/access-control/.

## Owners

Every agent lives in one namespace, and that namespace is its owner (agent-manager and Kubernetes say
**tenant**; the portal says **Owner** and never "tenant"):

- **Platform**: the installation's `kagent` namespace. Its agents are available to everyone signed in;
  the portal shows "Owner: Platform", "Available to all organizations". The General Assistant is one.
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

- **Platform**: the installation's operators, through the platform's configuration.
- **An organization**: its admins, on the organization's **Models** and **Egress** tabs (or
  `x_agent-manager_set_organization_model`, `x_agent-manager_set_organization_egress_policy`). A
  model key or credential goes into the portal's credential form and is never shown back, nor passed
  through a tool or chat.
- **Organizations themselves**: created and deleted by holders of
  `agent-platform:organizations-admin` (platform staff, or a customer's platform team).

An agent uses its own namespace's model configs and egress policies. Choosing them for an agent:
`agent-platform-agent-management`.
