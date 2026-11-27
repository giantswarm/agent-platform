---
name: agent-platform-organizations
description: Use when someone asks who owns an agent, why they can or cannot see or start one, what an organization (tenant) is, how to share an agent with everyone or with a team, how to join an organization, who manages model configs and egress policies, how an organization or its admins are created, changed or deleted, or why an agent-manager tool refuses an organization.
metadata:
  version: "1.2.0"
---

# Organizations: who owns an agent, who may use it

Kubernetes RBAC decides everything here; the portal and Slack only show the result. Read the live
answer, never guess: `x_agent-manager_list_agents` and `x_agent-manager_get_agent` name each
agent's `organization` (none for a Platform agent), `x_agent-manager_list_organizations` the
organizations the person manages or belongs to.
Access set-up: https://docs.giantswarm.io/tutorials/agent-platform/access-control/.

## Owners

Every agent lives in one namespace, and that namespace is its owner (agent-manager and Kubernetes say
**tenant**; the portal says **Owner** and never "tenant"):

- **Platform**: the installation's `kagent` namespace. Its agents are available to everyone signed in;
  the portal shows "Owner: Platform", "Available to all organizations". The default agent (`default-agent`) is one.
- **An organization**: a team or customer with its own members, models, connectors and egress
  policies. Its agents are for its members only. It is a Giant Swarm `Organization` with the Agent
  Platform enabled (`spec.capabilities.agentPlatform.enabled`); agent-platform-operator creates its namespace.

Organizations are an installation's opt-in. Without them every agent is the platform's. An
`Organization` without the Agent Platform is not listed; its `org-<name>` namespace holds its
workload clusters, not its agents.

## Naming an organization in the tools

Every agent-manager tool takes an organization by its name, as `x_agent-manager_list_organizations`
gives it; agent-manager finds the namespace. The namespace in an answer is a detail to show, never
an argument.

| You mean | Arguments |
|---|---|
| An organization's agents, members, models, connectors, egress policies, credentials | `organization: <name>` |
| The Platform: the installation's own agents (`kagent`) and models (cluster admins) | no `organization` |
| An organization's agent on one of its workload clusters | `organization: <name>` with `cluster` |
| The organization itself (`get_organization`, `create_organization`, `update_organization`, `delete_organization`) | `name: <name>` |

A tool that cannot use the organization refuses and writes nothing. Act on its state:

- **Not found** (`not_found`, "organization <name> not found"): no such organization, or the person
  has no role in it. Take a name from `x_agent-manager_list_organizations`; with no fitting one, the
  person needs a role (below).
- **The Agent Platform is not enabled for it** (`conflict`): its `Organization` exists without the
  switch on. Tell the person; the platform team enables it.
- **Not ready yet** (`conflict`): agent-platform-operator has not reported its namespace, as just
  after a create. Tell the person and call again shortly; `x_agent-manager_list_organizations` shows
  the namespace once it is ready.

An `invalid_request` that names `namespace` asks for `organization` in its place.

## Who sees and starts an agent

A role bound in the agent's namespace:

| Role | May |
|---|---|
| `agent-platform:user` | see the agents and start sessions with them |
| `agent-platform:editor` | the above, and create, change and delete agents |
| `agent-platform:admin` | the above, and manage models, egress policies, credentials and members (users and editors) |

- **Platform namespace**: everyone signed in is a user (the installation can narrow it to a group);
  the platform's operators and the customer's platform team are editors.
- **Organization**: its admins add users and editors (portal: **Organizations** → the organization →
  **Members**; `x_agent-manager_add_organization_member` and `x_agent-manager_remove_organization_member`
  with `role` `editor` or `user`, a `group` first). The admins themselves are the organization's
  `spec.admins`, which the platform team sets with `x_agent-manager_update_organization` (below);
  `x_agent-manager_list_organization_members` marks such an admin with `from`. A person in no
  organization sees who can add them on the **Organizations** page.

Each list holds only what the person may see, so two people get different lists. An agent someone
cannot find is a missing role, not a bug: name the owner, and who grants the role there.

## Making an agent available to everyone

Only agents owned by the Platform reach every organization. Creating one needs the editor role in the
platform namespace, usually the platform team. In the **New agent** form, set **Owner** to
**Platform**. Editing never moves an agent: to share an organization's agent platform-wide, create it
again with Owner **Platform** and delete the old one once the person confirms.

To share with one team instead, create it in that team's organization.

## Who manages models and egress policies

The optional credential-api component provides person-bound browser submission on `credentials.<installation domain>`, with direct portal-origin routes to the same service. Managers pass metadata and exact destinations only. Its own OAuth session must match the operation's issuer and subject; another person or another identity cannot save, including an admin using someone else's link. On “Ask an admin”, the admin starts setup with their own sign-in. Check the installation, initiating actor, every consumer and destination before entering a value. Never send credentials through a tool or chat. See [credential-api configuration](../../docs/credential-api.md) for the unpublished dependency gates. Existing consumers keep their current flow until their migration slices; model-manager owns model credentials in that migration.

- **Platform models**: cluster admins use the same model and credential tools without
  `organization`, or the installation's configuration in git. The
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
  **Cache time** on Add a model with an organization as owner, Add model and Edit model; the
  Models tab shows them) or through `x_agent-manager_set_organization_model`: its
  display name (`displayName`, every provider; `""` removes it), its token limit (`maxTokens`:
  Anthropic, AnthropicVertexAI, AzureOpenAI, Mistral, OpenAI; `0` returns to the provider's default)
  and prompt caching (`promptCaching`, `cacheTTL` `5m` or `1h`: Anthropic, Bedrock). A new Anthropic
  model, or one changed to Anthropic, has prompt caching on (`cacheTTL: 5m`) unless `promptCaching`
  is `false`. An update that omits a setting keeps it; a change of provider carries only `maxTokens`,
  where the new provider has it. The API server refuses a setting that the provider does not have.
  `x_agent-manager_list_model_configs` shows the settings of each model; to compare an
  organization's model with the platform's, list with `organization` and without it (one call
  each).
- **Vertex models**: use native `AnthropicVertexAI` or `GeminiVertexAI` with
  nonsecret `projectID` and `location`. Upload the service-account JSON only in
  the linked credential form when it requests `google-service-account-json`.
  Google calls are direct; LLM gateway metering, rate limits and spend reporting
  are unavailable. Admission refuses one without `apiKeySecret`, so the runtime
  never falls back to the node's Google identity. Never read, paste or
  send a person's credential file through an MCP tool.
- **Organizations themselves**: created, changed and deleted by holders of
  `agent-platform:organizations-admin`, bound cluster-wide (platform staff, or a customer's
  platform team).
  - `x_agent-manager_create_organization` takes the `name` and the admins: `adminGroups` (prefer a
    group) and `adminUsers`, both optional. It writes the `Organization` and answers at once:
    agent-platform-operator then creates the namespace and binds the admins. Until
    `x_agent-manager_list_organizations` shows the namespace, the organization is not ready yet.
  - `x_agent-manager_update_organization` changes the admins: `adminGroups` replaces the admin
    groups and `adminUsers` the admin users (`[]` removes them); an omitted one stays.
  - `x_agent-manager_delete_organization` erases it. Without `confirm` it answers what the erase
    deletes: show it to the person, who types the organization's name, then call again with that
    name as `confirm`. An organization from Git takes two steps: `mode: commit` without `confirm`
    opens the pull request that removes it; once merged, the organization shows `Orphaned=True`
    reason `DeleteMerged` (Pending erase), and `mode: commit` with `confirm: <name>`, typed by the
    person, erases it.

An agent uses its own namespace's model configs and egress policies and, from agent-manager 3.3.0,
its organization's own connectors. Choosing them for an agent: `agent-platform-agent-management`.
