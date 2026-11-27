# Tool approval

Every tool behind muster, the platform's tool gateway, is **Allow** (runs), **Ask** (a person confirms the exact call first) or **Block** (hidden, refused). muster enforces it on every call, so it holds in the Dev Portal, Slack, Claude Code and an agent's sandbox alike. The chart values, floors and prerequisites are in the chart README ("Tool approval and the governance mode"); upgrade steps are in [UPGRADE.md](../UPGRADE.md).

## Who sets what

| Who | Sets | Where |
|---|---|---|
| Connector admin (`muster.rbac.mcpServerEditor.subjects`, `tenants.platformTeam`) | A `rule` for tools without an entry (new ones included) and a setting per tool | The connector's `MCPServer` `spec.approval`, or the Dev Portal's *Connectors* page |
| Agent builder | The agent's tool list, each entry optionally Allow, Ask or Block, plus a `default` | The agent chart's `tools` and `default` |
| Connector admin only | An exception: Allow for one agent on a tool its connector asks for | agent-manager's `set_agent_approval` |

The stricter of the connector's and the agent's setting holds. A person calling a tool directly gets the connector's setting only. A connector without settings runs every tool and shows as *not reviewed*.

## Trusted and governed

`governance.mode` (`trusted`, the default, or `governed`) adds asking for agent callers; it never asks a person using a tool directly.

- **trusted**: an agent runs what the connector allows. It still asks before it adds or repoints a connector, changes approval settings, grants access or exceptions, authors workflows, or changes muster's configuration (the floors).
- **governed**: besides the floors, an agent asks before any tool of a governed server that is not on its `safe` list in `governedServers`, so a new or renamed tool asks until someone lists it.

Floors for your own servers go under `governance.roleFloors`; they add to the built-in ones and never narrow them.

```yaml
governance:
  mode: governed
  roleFloors:
    exampleManager: [approve_change, merge_change]
governedServers:
  example-manager:
    role: exampleManager
    safe: [get_info, list_changes]
```

Whoever can change these values can switch the mode or shorten a safe list, and nobody else approves it: protect the values repository with required reviews.

## What fails closed

- Asking needs muster's OAuth server with Dex sign-in, Valkey and `muster.muster.oauth.mcpClient.publicUrl`; without them every call that would ask is refused (`approval.unavailable`), never run.
- An agent with nobody to open the approval link runs only what is Allow; the rest expires.
- `governed` fails the render below the release floors (`agent-platform.muster.governanceFloor` and the others in `values.yaml`); a safe list naming a floor, or a `role` declared nowhere, fails muster's start.
- The editor groups default to a ServiceAccount nobody can authenticate as, so until you name your admin groups only cluster admins change approval.
- muster refuses every connector's tools while the `MCPServer` CRD lacks `spec.approval`.

A selection of tools (the agent's [toolset](toolset-presets.md)) is separate from whether a selected tool runs, asks or is hidden.
