---
name: agent-platform-organizations
description: Use when someone asks who owns an agent, why they can or cannot see or start one, what an organization (tenant) is, how to share an agent with everyone or with a team, how to join an organization, or who manages model configs and egress policies.
metadata:
  version: "1.1.0"
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
  This also works with organizations disabled: organization and installation
  models share the one `agent-manager.credentials.uiURL` form.
- **An organization**: its admins, on the organization's **Models** and **Egress** tabs (or
  `x_agent-manager_set_organization_model`, `x_agent-manager_set_organization_egress_policy`). A
  model key or credential goes into the portal's credential form and is never shown back, nor passed
  through a tool or chat.
  When the key already exists, Add model completes at once: a live save closes its dialog, a save
  by pull request keeps it open with the pull request. Replace key is offered on every model, one
  from Git too, to a person who may change credentials. Each model's name opens its page, where it
  is edited, set as default or deleted; a model from Git only where saves open a pull request
  (below), and a model of another manager not there. On the credential form, Check again refreshes
  the request and clears entered values; Save and Cancel wait until the refresh finishes.
  Opening another request starts a separate form with no earlier values or result.
  A saved or cancelled request keeps its result without an extra confirmation read.
  Direct credential saves and their recovery tolerate controller status updates.
  Configuration, credential binding or consumer identity changes still require a new preparation. A failed
  save can have written credentials already; refresh its confirmation and follow
  the same operation's recovery instead of starting another request. The form shows **Finish the
  save** only when an earlier save did not end; it needs no value.
- **How the portal saves an organization's content** (models and their deletes, keys and
  credentials, members): the organization decides; the portal offers no choice. An organization
  from Git, where `x_agent-manager_get_info` `capabilities.commit` is true: each save opens a pull
  request as the person, the page shows **Pull request #N opened** with its link, and the change
  applies only after the merge; tell the person so, never that it is live. Edit model says so
  before the save, and after one keeps its button disabled beside a line naming the pull request.
  Every other organization: a live write. To save the other way, open a pull request by hand, use
  `kubectl`, or call the agent-manager tool with its `mode` argument. The tools write live by
  default (`mode: apply`), which puts new content of an organization from Git outside Git: for
  such an organization (`x_agent-manager_list_organizations` marks it `gitops`, or gives it an
  `orphaned` condition) pass `mode: commit`, as the portal does.
- **An organization model's settings**, in the portal (**Token limit**, **Prompt caching** and
  **Cache time** on Connect a model with an organization as owner, Add model and Edit model; the
  Models tab shows them) or through `x_agent-manager_set_organization_model`: its
  display name (`displayName`, every provider; `""` removes it), its token limit (`maxTokens`:
  Anthropic, AnthropicVertexAI, AzureOpenAI, Mistral, OpenAI; `0` returns to the provider's default)
  and prompt caching (`promptCaching`, `cacheTTL` `5m` or `1h`: Anthropic, Bedrock). A new Anthropic
  model, or one changed to Anthropic, has prompt caching on (`cacheTTL: 5m`) unless `promptCaching`
  is `false`. An update that omits a setting keeps it; a change of provider carries only `maxTokens`,
  where the new provider has it. The API server refuses a setting that the provider does not have.
  `x_agent-manager_list_model_configs` shows the settings of each model; to compare an
  organization's model with the platform's, list the organization namespace and the platform
  namespace (one call each).
- **Vertex models**: use native `AnthropicVertexAI` or `GeminiVertexAI` with
  nonsecret `projectID` and `location`. Upload the service-account JSON only in
  the linked credential form when it requests `google-service-account-json`.
  Google calls are direct; LLM gateway metering, rate limits and spend reporting
  are unavailable. Admission refuses one without `apiKeySecret`, so the runtime
  never falls back to the node's Google identity. Never read, paste or
  send a person's credential file through an MCP tool.
- **Organizations themselves**: created and deleted by holders of
  `agent-platform:organizations-admin` (platform staff, or a customer's platform team).
  `x_agent-manager_delete_organization` of an organization from Git takes two steps: `mode: commit`
  without `confirm` opens the pull request that removes it; once merged, the organization shows
  `Orphaned=True` reason `DeleteMerged` (Pending erase), and `mode: commit` with
  `confirm: <parent>/<name>`, typed by the person, erases it.

An agent uses its own namespace's model configs and egress policies. Choosing them for an agent:
`agent-platform-agent-management`.
