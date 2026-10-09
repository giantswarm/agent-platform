<div align="center">

# Agent Platform

**Run AI agents on your own Kubernetes: models, agents and tools in one Helm install, with every call made as the person who asked.**

[![Release](https://img.shields.io/github/v/release/giantswarm/agent-platform?label=release)](https://github.com/giantswarm/agent-platform/releases)
[![CircleCI](https://dl.circleci.com/status-badge/img/gh/giantswarm/agent-platform/tree/main.svg?style=shield)](https://dl.circleci.com/status-badge/redirect/gh/giantswarm/agent-platform/tree/main)
[![OpenSSF Scorecard](https://api.securityscorecards.dev/projects/github.com/giantswarm/agent-platform/badge)](https://securityscorecards.dev/viewer/?uri=github.com/giantswarm/agent-platform)
[![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)

</div>

---

The Agent Platform takes proven open-source projects, such as [kagent](https://github.com/kagent-dev/kagent), [llm-d](https://github.com/llm-d/llm-d), [agentgateway](https://github.com/agentgateway/agentgateway) and [Flux](https://fluxcd.io), and packages them as a single platform for running AI agents in production. One OIDC identity spans the whole stack, so agents call tools and models as the person who invoked them, never as a broad service account.

It is the agent platform that runs on every [Giant Swarm](https://www.giantswarm.io) management cluster. The same chart installs on kind, EKS, AKS, GKE or any conformant cluster.

<div align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/img/architecture-dark.svg">
    <img alt="Agent Platform architecture. MCP clients such as Claude Code, the Backstage portal and Slack (through klaus-gateway) reach the platform. Tool Access: agentgateway in front of muster, the one MCP endpoint with per-user sign-in and toolsets, which fans out to MCP servers such as Kubernetes, agent-manager and model-manager. Agent Runtime: kagent agents run on Agent Substrate, each in its own gVisor sandbox, suspended to object storage when idle. Agents call tools through muster as the person who asked, and call models either at hosted providers or through the models gateway in front of llm-d on KServe on your GPUs. One OIDC identity spans everything, and Flux delivers and updates every component." src="docs/img/architecture-light.svg" width="900">
  </picture>
</div>

## Three pillars

Every component can be switched on or off on its own. A default install runs Tool Access (muster, agentgateway and Valkey) plus model-manager. You add agents, model serving and the portal when you need them.

### Agent Runtime

Declarative agents that each run in their own sandbox, are suspended to object storage when idle and resume on the next request.

| Component | What it does |
|---|---|
| [kagent](https://github.com/kagent-dev/kagent) | Kubernetes-native agent framework: agents, templates and model configs as CRDs, plus the controller that runs them. The platform runs [Giant Swarm's kagent line](https://github.com/giantswarm/kagent-upstream). |
| [Agent Substrate](https://github.com/kagent-dev/substrate) | The actor runtime under kagent. Each agent is a [gVisor](https://github.com/google/gvisor) sandbox in a worker pod, with its state snapshotted to object storage. |
| [agent-manager](https://github.com/giantswarm/agent-manager) | Creates, updates and deletes agents as GitOps releases of the [agent chart](https://github.com/giantswarm/agent), over MCP and REST, as the calling person. |
| [klaus-gateway](https://github.com/giantswarm/klaus-gateway) | Slack front door. A Slack thread becomes an agent session, and the agent acts as the Slack user. |
| [agent-sandbox](https://github.com/kubernetes-sigs/agent-sandbox) | The Kubernetes `Sandbox` API for isolated, stateful single-pod workloads. |

### Tool Access

One MCP endpoint for every tool. Each call carries the caller's own token down to the backend, and toolsets narrow what an agent sees without ever widening it.

| Component | What it does |
|---|---|
| [muster](https://github.com/giantswarm/muster) | MCP gateway that aggregates all MCP servers behind one endpoint, with OAuth sign-in per server, token forwarding, toolsets and workflows. |
| [agentgateway](https://github.com/agentgateway/agentgateway) | Data plane for MCP, A2A, gRPC and LLM traffic, with JWT validation, routing policies and telemetry at the edge. |
| [mcp-kubernetes](https://github.com/giantswarm/mcp-kubernetes) | The Kubernetes API as MCP tools, called with the signed-in user's own RBAC. |
| [agent-platform-mcps](https://github.com/giantswarm/agent-platform-mcps) | Registers the platform's MCP servers with muster and agentgateway from one list. |
| [Valkey](https://github.com/valkey-io/valkey) | Session and token store for muster's OAuth server. |

### Model Runtime

Serve open-weight models on your own GPUs, or use hosted providers such as Anthropic, OpenAI and Gemini. Either way, models are wired into agents for you.

| Component | What it does |
|---|---|
| [llm-d](https://github.com/llm-d/llm-d) | Distributed inference on vLLM with cache-aware routing. The platform ships curated, benchmarked serving presets for it. |
| [KServe](https://github.com/kserve/kserve) | The `LLMInferenceService` API and controller that run llm-d deployments. |
| [NVIDIA GPU Operator](https://github.com/NVIDIA/gpu-operator) | Device plugin, GPU feature discovery and DCGM monitoring on GPU nodes. |
| [model-manager](https://github.com/giantswarm/model-manager) | Model inventory, pull, load and unload across KServe, Ollama, LM Studio and Lemonade, plus the agents' model configs. |
| [cluster-manager](https://github.com/giantswarm/cluster-manager) | Creates GPU node pools and switches model serving on per cluster, as the calling person. Giant Swarm clusters only. |

### Across all three

| Component | What it does |
|---|---|
| [Backstage](https://github.com/giantswarm/backstage) | The portal for people: browse tools, create and chat with agents, manage models. |
| [Flux](https://github.com/fluxcd/flux2) | Delivers every component and keeps it current. The chart brings its own [Flux Operator](https://github.com/controlplaneio-fluxcd/flux-operator) where a cluster has none. |
| [CloudNativePG](https://github.com/cloudnative-pg/cloudnative-pg) | Postgres for agent sessions and the Substrate control plane, with backups. |
| [vm-manager](https://github.com/giantswarm/vm-manager) | KVM virtual machines with a vTPM and attestation, created over MCP. |
| Any OIDC provider | One identity for people, MCP clients and agents, for example [Dex](https://github.com/dexidp/dex). |

## Why Agent Platform

- **Identity end to end.** One OIDC provider signs people in to the portal, MCP clients and agents. The caller's token reaches the Kubernetes API and every other backend, so RBAC you already have applies to agents too.
- **Sandboxed by default.** Every agent runs as an isolated actor in a gVisor sandbox, with egress through a controlled gateway and network policies on every hop. The threat model is in [Agent Substrate security](docs/substrate-security.md).
- **One install, then GitOps.** `helm install` brings its own Flux where the cluster has none, and from then on the release manages itself. Components follow version ranges and roll forward without a new chart release. You can also pin them to a bill of materials.
- **Adapts to the cluster.** The chart detects Cilium, Kyverno and Prometheus and renders for what the cluster actually serves, so a laptop and a production fleet install the same chart.
- **Spans clusters.** A GPU cluster can run only the serving slice, and a workload cluster only the agent runtime slice, both managed from one installation.

## Quick start

**On your laptop:** [AgentLab](https://github.com/giantswarm/agentlab) runs the whole platform on kind with one binary, including the portal, agents, models and a bundled identity provider:

```bash
go install github.com/giantswarm/agentlab@latest
agentlab up
```

**On a cluster:** you need Kubernetes ≥ 1.35, Helm 4, the Gateway API CRDs and an OIDC provider. Start from the [example values](helm/agent-platform/examples/) for your cluster shape:

```bash
kubectl apply -f https://github.com/kubernetes-sigs/gateway-api/releases/download/v1.5.0/standard-install.yaml
helm install agent-platform oci://gsoci.azurecr.io/charts/giantswarm/agent-platform \
  --namespace agent-platform --create-namespace -f values.yaml --wait --timeout 10m
```

Then point any MCP client at `https://muster.<your-domain>/mcp`. Agents also need the Substrate feature gates, a database and an object store. The [installation guide](docs/install.md) covers each cluster shape step by step.

## Documentation

| Page | What it covers |
|---|---|
| [Installation guide](docs/install.md) | Cluster shapes, prerequisites, identity provider setup, first install |
| [Operator reference](docs/reference.md) | Every component and value: GitOps engine, self-management, serving and runtime slices, network policies, observability, CRD lifecycle |
| [Authentication](docs/authentication.md) | The request path, OAuth discovery, token forwarding and exchange, edge JWT validation |
| [Toolset presets](docs/toolset-presets.md) | Choosing which tools an agent gets |
| [Serving presets](docs/serving-presets.md) | Adding and benchmarking a model recipe |
| [Agent Substrate security](docs/substrate-security.md) | What the agent runtime changes on the cluster, and what protects it |
| [Upgrading](UPGRADE.md) | Version-to-version upgrade notes |

## Contributing

Each component is developed in its own repository, linked above. This repository holds the integration that turns them into one platform, and it welcomes contributions too:

- **[`helm/agent-platform`](helm/agent-platform)** is the chart you install. It holds the component roster with version ranges, the bundled Flux engine, self-management, and the install and upgrade hooks.
- **[`helm/agent-platform-connectivity`](helm/agent-platform-connectivity)** wires the components together: routes and gateways, network policies for Cilium and plain Kubernetes, Kyverno exceptions, the kagent model catalog, [serving presets](docs/serving-presets.md) and Grafana dashboards.
- **[`tests`](tests)** holds the render assertions behind `make verify-*` and an end-to-end install on kind ([tests/ats](tests/ats/README.md)).

```bash
make verify-all   # the render assertions CI runs: Helm 3.17.3, no cluster needed
```

Issues and pull requests are welcome. Please sign off your commits under the [DCO](DCO).

## Security

Please report vulnerabilities through [Giant Swarm's responsible disclosure](https://www.giantswarm.io/responsible-disclosure). See [SECURITY.md](SECURITY.md).

## License

[Apache 2.0](LICENSE).
