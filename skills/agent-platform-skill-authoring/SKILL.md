---
name: agent-platform-skill-authoring
description: Use when asked to write, fix or explain a skill for an Agent Platform agent — SKILL.md frontmatter and layout, where skills come from, how the runtime loads them, what a pin is, private skill repositories, or what in a skill breaks an agent's boot.
metadata:
  version: "3.0.0"
---

# Authoring skills

A skill is a folder with a `SKILL.md` and optional resources, written to the agentskills.io
specification (https://agentskills.io/specification). Creating agents and re-pinning their skills is
the `agent-platform-agent-management` skill; how Muster and its tools work is `agent-platform-tools`;
a scored review is `agent-platform-skill-review`.

## Where skills come from

An installation offers skills from its **skill sources**, which skill-manager serves (tools
`x_skill-manager_*`, as you): `x_skill-manager_list_skill_sources` lists them (`url`, `ref`, `path`,
`readOnly`, the commit the ref resolves to), `x_skill-manager_list_skills` their skills (name,
description, path, tags; `source` reads any other GitHub repository, `refresh` bypasses the cache).
The first source is the platform's own, `giantswarm/agent-platform` (`skills/`), pinned to the
platform release and **read-only**: the portal proposes no change to it and a fix goes upstream; the
installation configures the rest (`skillManager.sources`). A skill outside every source — any other
git repository or an OCI image — can still be pinned on an agent by its URL and path; it just is not
listed. A new skill goes into a writable source, below its `path`. `x_agent-manager_list_skills` is
deprecated: where `get_info` reports `skillManager`, it only proxies skill-manager's.

## The format

```markdown
---
name: my-skill
description: Use when … (the trigger: when should the agent load this? — not a summary)
metadata:
  version: "1.0.0"
---
```

- **Frontmatter**: the runtime knows `name`, `description`, `license`, `compatibility`, `metadata`
  (string → string) and `allowed-tools`. The current runtime drops any other field; older ones failed
  the boot of every agent that mounts the skill on one (Claude Code's `user-invocable`, a top-level
  `version`) — stay within the six.
- **`name`**: lowercase letters, digits and single hyphens, ≤ 64 characters, equal to the directory
  name; unique among the agent's skills.
- **`description`**: ≤ 1024 characters, written for the model: the situations that should make it
  load the skill. Name and description are all the agent sees until it loads the skill.
- **Body**: what the model would get wrong without it. Well under 150 lines.
- **Folders**: `references/` (read on demand), `scripts/` and `assets/` are the convention; any layout
  under the skill directory works.

## How the runtime loads a skill

Skills are materialised read-only when the agent's golden snapshot compiles. The runtime puts every
skill's `name` and `description` into the context and loads a body on demand (`load_skill`). Its file
tools and `bash` work in the turn's working directory and a per-session scratch directory, where
`skills/` links to the skills, `uploads/` holds attached files and `outputs/` the files handed back;
each tool's description states its limits (the `bash` timeout among them). Network reaches only the
origins the agent's egress allows. So: decision-making content goes into `SKILL.md`; a reference file
is named there with what it answers; a script gets a one-line usage; nothing depends on network or on
files outside the skill and the session.

## What a pin is

An agent's release lists its skills **pinned**: `{name, path, git: {url, commit}}` with a full 40- or
64-hex commit, or `{name, oci: <ref@sha256:…>}`; the chart refuses a branch, a tag, a short SHA or an
OCI tag. agent-manager's tools take a ref — none means the ref of the source holding the path, else
the default branch — and pin it at write time through skill-manager as the person; it refuses a
repository the person cannot read and a path missing at the commit. `x_skill-manager_resolve_skills`
shows what a ref pins to, writing nothing. **A new commit of a skill repository changes nothing on any
agent** until that agent is re-pinned — that makes an agent version reproducible, and is why a skill
fix is not live when it merges. Re-pinning belongs to the
`agent-platform-agent-management` skill; the platform's own skills move with a platform release.

A **private** repository needs the person's read access to pin it, and a credential Secret in the
agent's namespace to boot: the agent's `gitAuthSecretName`, else the installation's (`get_info`).
Without the Secret the skill fetch fails and the agent never boots.

## Writing rules

1. **Fetch live, never copy.** Before every line ask: can the agent fetch this? If yes, write the
   one-line recipe, not the content. Tool names and arguments come from `filter_tools` and
   `describe_tool`; what a preset resolves to from `filter_tools` with `include_presets`; the agent
   chart's values from `x_agent-manager_get_info` and `x_agent-manager_validate_agent`; the state of an
   agent from `x_agent-manager_get_agent_status`. Tables of tools, values, presets, connectors or
   failure modes go stale and cost context on every turn.
2. **Trigger, not summary.** The description answers "when do I load this?"; the body says what the
   model would get wrong without it — contracts, exact names, the order that matters, rules of
   conduct. Cut what it already knows.
3. **One topic per skill, no overlap.** Several small skills load cheaper than one large one. Name the
   skill that owns a neighbouring topic instead of explaining it again; a fact stated twice drifts.
4. **Short.** `SKILL.md` well under 150 lines; a `references/` file only for a genuinely long lookup,
   named in `SKILL.md` with what it answers.
5. **Declarative.** Outcomes and constraints, not numbered procedures — except where the order is the
   point (validate before create).
6. **Stable names.** A renamed skill or reference file breaks the agents that pin it at their next
   re-pin; treat names as an interface.
7. **No installation specifics** in a shared skill: no customer names, internal hostnames,
   credentials or assumptions about one installation's connectors or workflows — fetch them.

## What breaks an agent's boot

A `SKILL.md` that does not parse (frontmatter not YAML, `name` or `description` missing), an unknown
frontmatter field on an older runtime, or a private repository without a credential (above): the golden
snapshot never compiles and the agent stays `Ready=False`. A pin the chart refuses fails the release
before any boot. The evidence: `x_agent-manager_get_agent_status` and the
`agent-platform-agent-management` skill.

## Checking a skill before proposing it

- Frontmatter: only the six fields; `name` = directory; the description is a trigger under 1024
  characters. In giantswarm/agent-platform, `make verify-skills` checks the layout and that `name`
  is the directory.
- No line states what the agent could fetch; no section belongs to another skill.
- Every fact the agent must get exactly right (a name, an argument, a URL) is stated once.
- Nothing requires a tool the agent's toolset does not include.
- The agent's pin points at a commit that contains this version.
