---
name: agent-platform-skill-authoring
description: Use when asked to write, review, fix or explain a skill for an agent on the Giant Swarm Agent Platform — the SKILL.md frontmatter (agentskills.io fields), the folder layout, how the runtime loads a skill (load_skill, load_skill_resource, /skills), what a pin is and why a new commit changes nothing until an agent is re-pinned, the writing rules (fetch live, never copy; one topic; short), what in a skill breaks an agent's boot, and how to review one.
metadata:
  version: "2.0.0"
---

# Authoring skills

A skill is a folder with a `SKILL.md` and optional resources, written to the agentskills.io
specification (https://agentskills.io/specification). Giant Swarm's curated skills live in
https://github.com/giantswarm/agent-platform (`skills/`), one directory per skill; the
repositories an installation offers come from `x_agent-manager_get_info`; a team's skills
can live in any git repository or an OCI image. This skill covers writing and reviewing skills;
creating agents and re-pinning their skills is the `agent-platform-agent-management` skill, how
Muster and its tools work is the `agent-platform-tools` skill.

## The format

```markdown
---
name: my-skill
description: Use when … (the trigger: when should the agent load this? — not a summary)
metadata:
  version: "1.0.0"
---
```

- **Frontmatter**: the runtime reads `name`, `description`, `license`, `compatibility`, `metadata`
  (string → string) and `allowed-tools`, nothing else. Current runtimes ignore other fields; older
  ones fail the boot of every agent that mounts the skill on a field such as Claude Code's
  `user-invocable` — stay within the six.
- **`name`**: lowercase letters, digits and single hyphens, ≤ 64 characters, equal to the directory
  name; unique among the agent's skills (a release may mount it under another `name`).
- **`description`**: ≤ 1024 characters, written for the model: the situations that should make it
  load the skill. Name and description are all the agent sees until it loads the skill — the
  trigger carries the decision.
- **Body**: what the model would get wrong without it. Well under 150 lines.
- **Folders**: `references/` (read on demand), `scripts/` (run with the runtime's `bash`) and
  `assets/` (templates, data) are the convention; any layout under the skill directory works.

## How the runtime loads a skill

Skills are materialised read-only at `/skills/<name>` in the agent's golden snapshot when it
compiles. The runtime puts every skill's `name` and `description` into the system prompt with the
instruction to load a relevant skill before acting, and offers `list_skills`, `load_skill(name)`
(the `SKILL.md` body) and `load_skill_resource(name, path)` (one file of the skill, e.g.
`references/values.md`). `read_file`, `write_file`, `edit_file` and `bash` run in a per-session
directory (`/tmp/kagent/<session>/` with `uploads/`, `outputs/` and a `skills` symlink); `bash`
times out after 30 s, Python after 60 s; there is no network. So: the decision-making content
goes into `SKILL.md`; a reference file is named there so the agent knows what to load; a script
gets a one-line usage; nothing may depend on network access or on files outside `/skills` and the
session directory.

## What a pin is

An agent's release lists its skills **pinned**: `{name, path, git: {url, commit}}` with a full
40- or 64-hex commit, or `{name, oci: <ref@sha256:…>}`. The chart refuses a branch, a tag, a short
SHA or an OCI tag. **A new commit of a skill repository changes nothing on any agent** until that
agent is re-pinned — that is what makes an agent version reproducible, and why a skill fix is not
live when it merges. Pinning and re-pinning are the composers' job — the portal's skills step,
agent-manager's write tools, a GitOps commit — and belong to the `agent-platform-agent-management`
skill (the portal flow: https://docs.giantswarm.io/tutorials/agent-platform/create-an-agent/).

The repositories an installation offers and each skill's head commit are live data:
`x_agent-manager_get_info` and `x_agent-manager_list_skills` through `call_tool`. Any other public
repository works by `url` and `path`; it is only not offered. A **private** repository needs a GitOps release with
`skillsGitAuthSecretRef.name` (a Secret in the agent's namespace, key `token`) — the portal and
agent-manager pass no credential, so a private skill chosen there never boots.

## Writing rules

1. **Fetch live, never copy.** Before every line ask: can the agent fetch this? If yes, write the
   one-line recipe, not the content. Custom resources and `HelmRelease`s are read with
   `x_kubernetes_*`; tool names, arguments and what a preset resolves to come from `filter_tools`
   and `describe_tool`; the agent chart's values schema from `x_agent-manager_get_info` and
   `x_agent-manager_validate_agent`; the state of an agent or the platform from a `workflow_<name>` (the
   `agent-platform-tools` skill has the discovery recipe). Tables of tools, values, presets,
   servers or failure modes go stale faster than you can think and cost context on every turn.
2. **Trigger, not summary.** The description answers "when do I load this?"; the body says what the
   model would get wrong without it — contracts, exact names, the order that matters, the rules of
   conduct. Cut what it already knows.
3. **One topic per skill, no overlap.** Several small skills load cheaper than one large one. Where
   the reader needs a neighbouring topic, name the skill that owns it (`the agent-platform-tools
   skill`) instead of explaining it again; a fact stated in two skills drifts.
4. **Short.** `SKILL.md` well under 150 lines; a `references/` file only for a lookup recipe that is
   genuinely long, named in `SKILL.md` with what it answers.
5. **Declarative.** Outcomes and constraints, not numbered procedures — except where the order is
   the point (validate before create; a workflow before raw tools).
6. **Stable names.** A renamed skill or reference file breaks the agents that pin it at their next
   re-pin; treat names as an interface.
7. **Public content only** in a public repository: no customer names, no internal hostnames beyond
   the platform's own, no credentials.

## What breaks an agent's boot

A `SKILL.md` that does not parse (frontmatter not YAML, `name` or `description` missing), an
unknown frontmatter field on an older runtime, or a private repository without a credential: the
golden snapshot never compiles and the agent stays at `Ready=False`. A pin the chart refuses fails
the release before any boot. The evidence: `workflow_agent-status` and the
`agent-platform-agent-management` skill.

## Reviewing a skill

A full review with findings and a score is the `agent-platform-skill-review` skill.

- Frontmatter: only the six fields; `name` = directory; the description is a trigger under 1024
  characters. In giantswarm/agent-platform, `make verify-skills` checks the layout and that `name`
  is the directory, on every pull request — run it before opening one.
- Does any line state something the agent could fetch? Replace it with the recipe.
- Does any section belong to another skill? Replace it with that skill's name.
- The body loads in one read and names which reference answers what.
- Every fact the agent must get exactly right (a name, an argument, a URL) is stated once.
- Nothing requires network, a shell outside the session directory, or a tool the agent's toolset
  does not include.
- Pin: the agent's release points at a commit that contains this version.
