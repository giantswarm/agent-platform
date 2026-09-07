# Toolset presets

A **toolset** is the selector list an agent declares to say which of the gateway's tools it is composed with: the agent chart's `toolset` value (rendered as the `X-Muster-Toolset` header on the agent's muster tool entry), agent-manager's `toolset` argument, or the `toolset` argument of muster's `filter_tools`. muster evaluates it on every request as one more filter on the caller's own catalogue — it never widens access; the invoking human's identity and the backends' own authorization remain the boundary. The grammar, the built-in presets and the error texts are muster's: [docs/reference/toolsets.md](https://github.com/giantswarm/muster/blob/main/docs/reference/toolsets.md).

A **preset** is a named selection an agent refers to as `preset:<name>`. Presets are muster configuration — `toolsetPresets` in muster's `config.yaml`, which the muster chart renders from its `muster.toolsetPresets` value. This meta-package forwards its `muster:` block to the muster release, so the presets an installation ships live under **`muster.muster.toolsetPresets`** in these values (the first `muster` is the component block, the second is the muster chart's own `muster:` key). The standalone chart mirrors the same values through curate.

## The presets muster builds in

| Preset | Selects |
|---|---|
| `read-only` | Every tool its server annotates `readOnlyHint: true`, plus every workflow whose step tools are all read-only. |
| `none` | Nothing. An agent whose whole toolset is `preset:none` gets no muster tool entry at all. |
| `full` | The whole catalogue, including muster's `core_*` tools. |

They cannot be redefined: a `toolsetPresets` entry named like one makes muster refuse to start, naming the preset. The meta chart fails the render first (`make verify-presets` covers it), so the mistake never reaches a cluster.

## The presets this chart ships

Both select by the tool-group label every platform-shipped `MCPServer` CR carries, `agent-platform.giantswarm.io/tool-group: infrastructure | agent-platform`, stamped by the chart that ships the server. The label is read live on every request, so a new manager or a fourth infrastructure family joins its preset with no values change; a server registered through the portal carries no label and is in neither.

```yaml
muster:
  muster:
    toolsetPresets:
      infrastructure:
        description: The servers for the infrastructure underneath the platform (Giant Swarm installations' management clusters) — mcp-kubernetes, mcp-capi, mcp-prometheus.
        include:
          - label: agent-platform.giantswarm.io/tool-group=infrastructure
      agent-platform:
        description: The platform's own management surface — agent-manager, model-manager, cluster-manager and muster's core tools.
        include:
          - label: agent-platform.giantswarm.io/tool-group=agent-platform
          - pattern: core_*
```

| Preset | Resolves to | Label stamped by |
|---|---|---|
| `infrastructure` | The mcp-kubernetes, mcp-capi and mcp-prometheus families — every management cluster's servers, one selector. | agent-platform-mcps ≥ 0.9.0 (`muster.families.<group>.toolGroup`, default `infrastructure`; per-entry `toolGroup` override) |
| `agent-platform` | agent-manager, model-manager (cluster-manager when it ships) and muster's `core_*` tools — the meta agent's preset. | agent-manager ≥ 0.3.0, model-manager ≥ 0.18.0 (fixed `agent-platform`; `muster.mcpServer.labels` can override) |

`core_*` is what makes `agent-platform` the meta agent's preset. No other shipped preset reaches muster's core tools; an agent that needs one names it explicitly (`tool:core_workflow_list`) or uses `preset:full`.

### Requirements

- **muster ≥ 5.12.0.** The `label:` rule arrived there; a 5.11.0 muster refuses to start on a preset that uses it. `components.muster.versionRange` floors at `>=5.12.0 <6.0.0` for that reason, and a BOM that pins muster must pin at least that.
- The labels come with the component versions above. On an installation still running an older agent-platform-mcps, `preset:infrastructure` resolves to nothing until the label arrives — no error, an empty toolset (the failure mode is closed). `filter_tools` reports the selector under `toolset_unmatched`.

## Adding an installation's own presets

Add entries next to the shipped ones in the installation's gitops values. Helm merges the map, so the two platform presets stay and yours join them; `preset:<name>` is then valid for every agent on the installation, and the portal's Tools step lists it. Each preset has a `description`, an `include` list and an optional `exclude` list; every rule sets exactly one key — `tool`, `pattern`, `server`, `workflow`, `readOnly: true`, `label`, or `preset` (composition, `include` only).

A preset for the test clusters' servers, by name:

```yaml
muster:
  muster:
    toolsetPresets:
      test-clusters:
        description: The kubernetes and prometheus servers of the test management clusters, read-only
        include:
          - server: test-01-mcp-kubernetes
          - server: test-02-mcp-kubernetes
          - server: test-01-mcp-prometheus
          - server: test-02-mcp-prometheus
        exclude:
          - pattern: "*_delete"
```

The same, by a label. `label:` matches `metadata.labels` on the `MCPServer` CR, so it fits servers whose CR the installation writes itself — a GitOps-managed `MCPServer`, or a chart with a labels knob such as `muster.mcpServer.labels` on the agent-manager and model-manager charts. agent-platform-mcps (0.9.0) has no free-form labels value: its CRs carry the template's fixed labels plus the tier label from `muster.families.<group>.toolGroup` / `mcpServers[].toolGroup`, so select its servers by `server:` as above, or by the tier label.

```yaml
      test-clusters:
        description: Every server labelled for the test pipeline
        include:
          - label: example.com/pipeline=testing
```

Composition — the infrastructure preset without its writes, plus one workflow:

```yaml
      safe-ops:
        description: Infrastructure tools without apply, patch and delete, plus the incident-triage workflow
        include:
          - preset: infrastructure
          - workflow: incident-triage
        exclude:
          - pattern: "*_apply"
          - pattern: "*_patch"
          - pattern: "*_delete"
```

Rules of the road:

- A `server:` rule names the `MCPServer` CR (or family) as muster exposes it; a `pattern:` is a glob on the exposed tool name (`x_<server>_<tool>`, `workflow_<name>`, `core_*`).
- `include` rules are a union, never an intersection: `preset: infrastructure` next to `readOnly: true` resolves to every infrastructure tool plus every read-only tool of the whole catalogue, not to the infrastructure's read-only tools. Narrow a set with `exclude` (by `pattern:` or `tool:`) as `safe-ops` does; `readOnly: false` is not a rule — muster rejects it at startup.
- Never name a preset `read-only`, `none` or `full` — see above.
- A preset that matches nothing is not an error: the agent simply has no tool from it. An agent naming a preset that does not exist gets an error on every meta-tool call — remove a preset only after no agent's `toolset` names it.

## Verifying on a cluster

With an authenticated muster CLI context:

```bash
muster call --context <installation> -o json filter_tools --json '{"include_presets": true, "toolset": ["preset:infrastructure"]}'
```

`presets` lists the built-ins and the shipped/installation presets with their descriptions; `tools` is what the preset resolves to for the caller (a server the caller has not signed in to contributes nothing); `toolset_unmatched` names selectors that matched nothing. The same call with `["preset:agent-platform"]` returns the managers' tools plus `core_*`.
