# Writing an agent's system prompt

The prompt is the agent's role, voice and rules. Knowledge that is long, changes, or is shared
between agents belongs in skills — the runtime injects every skill's name and description into the
context and loads the body on demand, so the prompt stays short and the agent stays cheap. Keep it
under a page; everything longer is a skill.

## The Muster meta-tool paragraph (every tooled agent)

Every agent with tools needs a short paragraph in its prompt that states Muster's `call_tool`
contract — the meta-tools are its only functions, every `x_*`, `workflow_*` and `core_*` is a `name`
passed to `call_tool`, always the full catalogue name from `filter_tools` — and that a workflow comes
first where one fits. The contract itself is the `agent-platform-tools` skill ("The `call_tool`
contract"); write it in your own words there, and give an agent that touches family tools (an
instance argument such as `management_cluster`) that skill rather than a copy.

## A template

```text
You are <Name>, <one sentence of role>. <Who you help and
with what.>

<Voice: two or three sentences. How long a first answer is, when to go deep, what to do when
unsure.>

Your knowledge lives in your skills: load <skill> for <topic>, <skill> for <topic>. Load a
skill the first time its topic comes up instead of guessing.

<Tool rules: what you may read, what you may change, what needs an explicit request; how you
confirm choices you made for the person; that you act with the person's identity and say so.>

<The Muster meta-tool paragraph.>
```

## Rules of thumb

- Say what the agent is *for* and what it is *not* for; the roster and the portal show the
  description, the prompt shapes the behaviour.
- One voice, stated once. Contradictory instructions cost turns.
- Name the skills the agent should load and when; the runtime lists them, but a nudge helps.
- Writes only on explicit request, named target, one at a time; the agent acts as the person and
  may say so when refusing or when a call is `forbidden`.
