---
name: agent-platform-analysis
description: Use when asked how the Agent Platform's components are doing over time or where to improve them — failing, slow, flapping or unused connectors, how often a connector or tool is called (the portal's "Calls, last 30 days"), model cost, tokens and latency per agent or model, which agent costs most, or to find what to fix or optimize from metrics.
metadata:
  version: "1.0.0"
---

# Analysing and optimizing the platform from its metrics

The components export metrics to the installation's Prometheus; read them with the `x_prometheus_*`
tools. Current state (an agent's readiness, a connector's state now) is a live read: the
`agent-platform-overview` skill's "Look at the live platform". One session's behaviour is the
`agent-platform-agent-management` skill. Totals per organization (spend, tokens, tool calls) are the
portal's **Usage** page (`agent-platform-portal`): point there instead of rebuilding them.

## Reaching the metrics

- The `x_prometheus_*` tools exist only where the toolset offers them (`filter_tools
  {"pattern": "x_prometheus_*"}`); without them, say the metrics are not reachable from here.
- They are a family: `management_cluster` is required and takes a full server name from the `enum`
  in `describe_tool`; an installation missing from the enum is not reachable.
- One Prometheus can hold several installations: put `cluster_id="<installation>"` into every
  selector; in the queries below, `c` stands for it.
- Metric names and labels change with releases: confirm them first with
  `x_prometheus_list_label_values` (`label: "__name__"`, `matches: ["{__name__=~\"muster_.*\"}"]`).
- The backend behind the Prometheus API is usually Mimir. Its query frontend splits range queries,
  but an instant query over a long window (`increase(…[30d])`) is one long read that fails when the
  store degrades: then use `x_prometheus_execute_range_query` with a `1d` step and sum the steps.
  `org_id` picks the Mimir tenant when the default one lacks a series.
- Output is capped: aggregate (`sum by`, `topk`) instead of listing raw series.

## The signals

- **Connectors (Muster)**: `muster_downstream_tool_calls_total` and
  `muster_downstream_tool_call_duration_seconds` per `mcpserver_name` (the connector, as
  `core_mcpserver_list` names it), `tool` and `outcome`: `ok`, `error` (the call failed before a
  result, on Muster's side or the transport) or `error_result` (the tool answered with `isError`).
  The portal's connector "Calls, last 30 days" sums the same counter in daily steps, so expect small
  differences from an instant query.
- **Connector health (Muster)**: `muster_mcpserver_state` is 1 for each connector's current `state`;
  `muster_mcpserver_state_transitions_total` counts transitions, labelled with the `state` entered
  (`Failed`, `Connecting`, `Connected`, … — list them live).
- **Meta-tools and workflows (Muster)**: `muster_tool_calls_total` per `tool` and `outcome`;
  `muster_workflow_executions_total` per `workflow` and `status`.
- **Models (agentgateway)**: `agentgateway_gen_ai_client_cost_usd_total`,
  `agentgateway_gen_ai_client_token_usage_*` (`gen_ai_token_type`), and the latency histograms
  `agentgateway_gen_ai_server_time_to_first_token_*`, `…_time_per_output_token_*` and
  `…_request_duration_*`, per `agent`, `gen_ai_request_model`, `organization` and `user`.
- **Not per connector**: `agentgateway_mcp_requests_total` sees the agent's call to Muster
  (`server="muster"`, `resource="call_tool"`), not the connector behind it.
- **Only connectors behind Muster are measured.** An organization's own connector is called by the
  agent directly (`agent-platform-tools`) and leaves no Muster series: no series means "not measured
  here", never "zero calls".

## Questions and the query shape

Use a window that fits the question (7d for "now", 30d for usage) and say which one you used. Every
ratio and quantile needs volume behind it: give the call count next to it, and treat a handful of
calls as no evidence.

- **Failing connectors**: `sum by (mcpserver_name) (increase(muster_downstream_tool_calls_total{c,
  outcome!="ok"}[7d]))` divided by the same without the `outcome` filter; then split by `outcome`
  and `tool`.
- **Slow connectors**: `histogram_quantile(0.95, sum by (mcpserver_name, le)
  (increase(muster_downstream_tool_call_duration_seconds_bucket{c}[7d])))`; NaN means no calls.
- **Flapping connectors**: every Muster replica runs its own reconciler and counts the same
  transitions, so never sum across pods: `max by (mcpserver_name) (sum by (mcpserver_name, pod)
  (increase(muster_mcpserver_state_transitions_total{c, state="Failed"}[7d])))`. Pods replaced
  inside the window make this a lower bound: report the order of magnitude.
- **Current state**: `max by (mcpserver_name, state) (muster_mcpserver_state{c}) == 1`.
- **Unused connectors**: `max by (mcpserver_name) (muster_mcpserver_state{c}) unless on
  (mcpserver_name) (sum by (mcpserver_name) (increase(muster_downstream_tool_calls_total{c}[30d])) > 0)`.
- **Cost and tokens**: `sum by (agent, gen_ai_request_model)
  (increase(agentgateway_gen_ai_client_cost_usd_total{c}[30d]))`; tokens by `gen_ai_token_type`.
- **Model latency**: mean time to first token per model is the `_sum` increase over the `_count`
  increase.

## Counting caveats

- Each pod is its own series, and a counter restarts at 0 with its pod. `increase()` handles
  restarts but misses the first value of a series that starts inside the window, so on a
  low-traffic connector it undercounts: report "at least".
- Prometheus retention can be shorter than the window; when an older range comes back empty, say
  the data may not go back that far.
- A connector family has one `mcpserver_name` per instance: match with a regex and sum.

## People in the data

The gateway's `user` label names people. Aggregate it away by default and break down by person only
for the asker's own usage; never list other people's usage unasked.

## From finding to fix

Report each finding with its number, volume, window, query and caveat, then the next step and who
owns it:

- The metrics say which connector and tool fail, not why. Before naming a cause, check the
  connector's state and sign-in (`core_mcpserver_get`, `core_auth_status`; the
  `agent-platform-tools` skill) and, where a read-only tool fails, call it once to read the error.
- `error_result`: the backend answered with an error (rights, input, its own fault). `error` or
  repeated `Failed` transitions: the connection or the server; its owner fixes it.
- An unused connector: a candidate for removal from toolsets, never removed unasked.
- An agent whose cost is high: compare its model and its tool error rate; a cheaper model or fewer
  failing calls are proposals for the `agent-platform-agent-management` skill, confirmed before any
  write.
