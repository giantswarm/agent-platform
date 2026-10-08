# Who may call an MCP backend

Every MCP backend of the platform sits behind an ingress policy that admits a
fixed list of callers and drops the rest. Two charts write that policy, in the
same shape:

- the connectivity chart, for the in-cluster backends an installation registers
  with muster (`networkPolicy.mcpBackends`, one policy per entry,
  `templates/networkpolicy-mcp-backend-ingress.yaml`);
- the mcp-* server charts (mcp-kubernetes, mcp-prometheus, mcp-capi, …), each
  for its own server (`ciliumNetworkPolicy.ingress`).

A caller missing from the list does not fail loudly: its connection is dropped
and the client sees a timeout. Each caller below is one the platform depends
on; a policy that leaves one out cuts that path.

## The callers

| Caller | Why it calls | mcp-* server chart | `networkPolicy.mcpBackends.<key>` |
|---|---|---|---|
| muster | every tool call a person or an agent makes through muster | `ingress.muster` (the release namespace of the agent platform) | always admitted, from this release's namespace, `app.kubernetes.io/name: muster` |
| The Gateway's proxies | a backend with a public route (an HTTPRoute on a Gateway), the OAuth endpoints included | `ingress.gatewayPeers`, admitted while `gatewayAPI.enabled` (Envoy Gateway's data plane, `envoy-gateway-system`, `app.kubernetes.io/name: envoy`) | `additionalPeers` with the same namespace and labels |
| teleport-kube-agent | Teleport application access: a muster on another cluster reaches the backend through the Teleport agent of the backend's cluster, and the call arrives from that agent's pod | `ingress.teleportPeers` (`kube-system`, `app: teleport-kube-agent`) | `additionalPeers` with the same namespace and labels |
| alloy-metrics | the observability platform scrapes the backend's metrics | `ingress.metricsScrapers`, on the metrics port while metrics are served | `metrics: true` admits `networkPolicy.mcpBackendScrapers` (`kube-system`, `app.kubernetes.io/instance: alloy-metrics`) on the entry's ports |
| The kubelet | liveness and readiness probes, often on another port than the MCP port | the host entity on every port | the host entity on every port (cilium flavour); the CNI's business in the kubernetes flavour |
| The backend's own namespace | a store, UI or sidecar deployed beside the server | — | every pod of the entry's namespace, on every port |

Anything else (an ingress controller in front of a legacy `Ingress`, a second
gateway) goes into `additionalPeers` in either chart.

A muster-only policy is the tempting minimum and the wrong one: it passes every
local test and drops the Teleport path in production, where a muster on one
cluster serves backends on others.

## What a lab cannot show

A local lab has no Teleport: no teleport-kube-agent, no application access, no
second cluster whose muster calls in. A policy that drops the Teleport agent
passes every lab proof. That path is proven only on an installation where a
remote muster reaches the backend through Teleport: a tool call through it
answers, and the backend's policy names `teleportPeers` (or the
`additionalPeers` entry) with the agent's labels as they are on that cluster.
A change to a backend's policy says in its pull request which callers it kept
and how the Teleport path was verified.

## Rolling a policy change

A chart released as a stable version without a release-candidate line rolls
to every installation that tracks its range on the merge that tags it: there is
no candidate to prove first. A change that narrows a backend's policy is
therefore checked against this list before the merge, not after the rollout.

## The Valkey stores

The Valkey charts (valkey-app v0.3.3 and later) admit the store's consumers on
the Valkey port through `valkey.ciliumNetworkPolicy.ingress.clients`, every pod
of the release namespace by default. Least privilege names the consumers: for
the platform's Valkey, muster (`app.kubernetes.io/name: muster`) and
klaus-gateway (`app.kubernetes.io/name: klaus-gateway`) in the release
namespace; for an mcp-* server's store, that server's pods.
