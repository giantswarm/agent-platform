# Agent Substrate on an installation: what changes, what it needs, what protects it

For the owner of an installation and for a security reviewer. Meta chart 4.0 ships [Agent Substrate](https://github.com/kagent-dev/substrate) — kagent API v2's runtime — as two components next to kagent (README "Agent Substrate"). This page is the honest account of what that puts on the cluster and its nodes, which of it steps outside the restricted Pod Security Standard and why, what compensates, and which risks are open upstream.

## What Substrate is, in one paragraph

Every agent of the platform runs as a Substrate **actor**: a gVisor sandbox inside a long-running **worker** pod of a `WorkerPool`, resumed from a snapshot when a request arrives and suspended back to the object store when idle. A control plane in `ate-system` — `ate-api-server` (the actor and worker registry, backed by Postgres), `ate-controller` (turns WorkerPools into worker Deployments), the `atenet` router (a turn's ingress: it asks ate-api which worker hosts the actor and tunnels the request to it), the `atenet` egress gateway (every connection an actor opens leaves through it) and dns — and a per-node agent, **`atelet`**, which restores and snapshots sandboxes, pulls the actors' images and the gVisor runtime, and registers the CSI and device plugins the workers use. Identities between all of these are pod certificates (`PodCertificateRequest`, a Kubernetes 1.35 beta API) signed by a `podcertificate-controller` from CA pools the chart bootstraps; actors authenticate to ate-api with the cluster's ServiceAccount tokens.

## Node-level and cluster-level changes

| Change | Where | Why |
|---|---|---|
| **Kubernetes 1.35 with three feature gates** (`PodCertificateRequest`, `ClusterTrustBundle`, `ClusterTrustBundleProjection`) on kube-apiserver, kube-controller-manager and every kubelet | the cluster (giantswarm/cluster#1005, or the cluster chart's `internal.advancedConfiguration.*.featureGates` until then) | the pod identities of every Substrate component and worker are `PodCertificateRequest`s the kubelet issues and projects; the trust anchors are `ClusterTrustBundle`s. Enabling the gates rolls the control plane and every node. |
| A **privileged DaemonSet** (`atelet`) on every schedulable node, with hostPorts 8085 (its gRPC API, mTLS) and 9090 (metrics) and hostPath mounts of `/var/lib/ateom-gvisor` (the sandboxes' state, shared with the workers), `/var/lib/kubelet/plugins`, `/var/lib/kubelet/device-plugins` (the CSI and device plugins it registers) and `/dev` (read-only) | `ate-system` | it runs gVisor on the node's behalf: creates and restores sandboxes, mounts their filesystems, wires their network into the worker pod. There is no unprivileged form of that on today's Substrate. |
| **Worker pods as root** with the capability set an unprivileged gVisor sandbox needs (`NET_ADMIN`, `SYS_ADMIN`, `SYS_CHROOT`, `SYS_PTRACE`, `SETUID`, `SETGID`, `SETPCAP`, `DAC_OVERRIDE`, `FOWNER`, `CHOWN`, `MKNOD`, `NET_RAW`, `SETFCAP`), AppArmor and seccomp `Unconfined`, a hostPath of `/var/lib/ateom-gvisor` with `HostToContainer` propagation — **not** privileged | the kagent namespace (every WorkerPool) | `runsc` installs its own seccomp filters and needs the capabilities to build the sandbox; the propagation shares the node agent's mounts into the worker. The sandbox, not the pod, is the isolation boundary for the agent's code. |
| A **cluster-scoped `SandboxConfig`** naming the gVisor release asset `atelet` downloads (`gs://gvisor/releases/…`, verified by sha256) and a **`ValidatingAdmissionPolicy`** that holds `SandboxConfig`s to a valid shape | cluster scope | the runtime binary is not an image; a proxied installation mirrors the asset and points `spec.assets` at the mirror. |
| Three **CRDs** (`workerpools`, `sandboxconfigs`, `csidriverconfigs.ate.dev`), ClusterRoles for `ate-controller` (pods, Deployments, WorkerPools), `atelet` (pods, `ClusterTrustBundle`s, `SandboxConfig`s, read-only) and `ate-api-server`, and the `podcertificate-controller`'s signer role | cluster scope | |
| Two **namespaces**, `ate-system` and `podcertificate-controller-system`, holding the CA and JWT pools (Secrets) the platform bootstraps once and keeps | | the pools are the roots of every identity Substrate issues; access to those two namespaces' Secrets is access to those roots. |
| Egress from the nodes to `storage.googleapis.com` (the gVisor asset), the image registries of the actors' images, and the snapshot store | `atelet` | |

## What the Pod Security Standard refuses, and the exceptions

A Giant Swarm cluster enforces the restricted standard through Kyverno. The connectivity chart ships one `PolicyException` per Substrate workload, each naming **exactly the rules that workload violates** — `make verify-kyverno` computes the violations from the rendered pod specs and fails the build when an exception is broader or narrower:

| Workload | Rules excepted |
|---|---|
| `atelet` (DaemonSet, `ate-system`) | `privileged-containers`, `host-ports-none`, `host-path`, `restricted-volumes`, `require-drop-all`, `run-as-non-root`, `privilege-escalation`, `check-seccomp-strict` |
| the worker pods (every WorkerPool, label `ate.dev/worker-pool`, the kagent namespace) | `host-path`, `restricted-volumes`, `adding-capabilities`, `adding-capabilities-strict`, `run-as-non-root`, `run-as-non-root-user`, `privilege-escalation`, `check-seccomp`, `check-seccomp-strict`, `app-armor` |
| the control plane (`ate-api-server`, `ate-controller`, `atenet-router`, `atenet-egress`, `dns`) | `require-drop-all`, `run-as-non-root`, `privilege-escalation`, `check-seccomp-strict` — the Deployments declare no securityContext; the images are distroless and run as non-root users, the fields are what the standard checks |
| `podcertificate-controller` | `run-as-non-root`, `check-seccomp-strict` |

Nothing else is excepted: no `app: kagent` selector remains (the v1alpha2 agent Deployments' exception is gone with them), the exceptions are scoped to the two Substrate namespaces and the WorkerPool label in the kagent namespace, and the hook Jobs of the connectivity release run under the restricted profile themselves.

## Compensating controls

- **The sandbox is the boundary.** An agent's code runs under gVisor (`runsc`), a user-space kernel; the worker pod's capabilities serve the sandbox's construction, not the agent. The worker pod is a long-lived shell that hosts one actor at a time and is replaced between actors' lifetimes by the controller; it carries a `default` ServiceAccount token that nothing inside the sandbox can reach without escaping it.
- **Every hop is authenticated.** Pod certificates (mTLS) between `atelet`, `ate-api-server`, the router and the egress gateway; actors authenticate to ate-api with the cluster's ServiceAccount tokens for the audience `api.ate-system.svc`, verified against the apiserver's issuer; the router authorizes a request against the actor assigned to the worker before it tunnels it; the egress gateway resolves the connecting actor and applies the egress policy in the request path.
- **Network policies in both flavours** (`templates/substrate/netpol.yaml`): the control plane accepts only its peers; the worker pods reach only the egress gateway, the dns and the cluster DNS — an actor's outbound connections all leave through `atenet-egress`, where the actors' allow-list lives (muster, the kagent controller, the LLM provider or the agentgateway LLM listener, DNS); the kagent controller admits the gateway on its API port and nothing else of Substrate's. Rendered on every installation; enforced where Cilium enforces (agentlab does not).
- **Namespace scoping.** Substrate's control plane and its pools live in two namespaces of their own; the WorkerPool and the workers in the kagent namespace with the agents' other objects; the hook that bootstraps the pools holds its rights for the seconds it runs (a ClusterRole on secrets, configmaps and namespaces, created and removed with the hook).
- **Roots that are minted once.** The CA and JWT pools are created when missing and never rotated by the chart — a rotation is an operational procedure of Substrate that re-issues every dependent identity, not something an upgrade may do by accident.
- **One architecture per pool.** The WorkerPool is pinned to the installation's CPU architecture: a mixed pool wedges the actors whose snapshot was taken on the other.
- **Render-time refusals.** The chart refuses kagent without Substrate, Substrate without its CRDs or a database, kagent without a snapshot store, and — where the render is live — a cluster that does not serve `certificates.k8s.io/v1beta1/PodCertificateRequest`.

## Open upstream items (state at the pin)

Substrate is an early project; its own [threat model](https://github.com/kagent-dev/substrate/blob/main/docs/threat-model.md) states that it "has little to no security hardening at this time". What matters for an installation:

- **Worker-pod host escape** — an open **Critical** in the threat model: an actor that escapes gVisor lands in a root pod with `SYS_ADMIN` and a host mount. The mitigating invariant upstream names (privileged operations moved out of the worker pod, or the sandbox's construction done by the node agent) is not implemented at the pin; the network policies and the pod's placement (a WorkerPool node pool, `kagent.substrateWorkerPool.template`) are what an installation can add today.
- **No authorization in ate-api.** Every authenticated caller of ate-api has full control of every atespace, actor, template and worker; the kagent controller is the only intended caller, the network policies keep it so, and the JWT provider admits only the cluster's ServiceAccount tokens for the ate-api audience.
- **Control plane next to the workers.** The threat model recommends running the control plane and the egress gateway on nodes the sandboxes do not share; `substrate.atelet.nodeSelector` and the WorkerPool's `template.nodeSelector` make that a node-pool decision of the installation.
- **The gVisor runtime as a downloaded asset.** `atelet` fetches `runsc` from `storage.googleapis.com` at prewarm, verified by the sha256 the `SandboxConfig` names; the asset is not an image and not scanned by the registry's scanner.
- **Egress for an actor while it resumes** — the carried patch of the Substrate line that lets a harness fetch its skills before it is ready; complete with the egress dataplane the pinned release runs (the agentgateway line's `v1.5.1-gs.2`, which authorizes a resuming actor at CONNECT time as a frontend policy; the line's ledger, giantswarm/giantswarm#37742 rows 8 and 13).
- Everything the platform carries against upstream, with its upstream exit: giantswarm/giantswarm#37742 (the Substrate rows) and the line's `FORK.md`.

## What an installation owner does

1. Kubernetes 1.35 and the three gates on all three components, ahead of the cut-over (UPGRADE.md).
2. The snapshot store: an S3 bucket and an IRSA role for `atelet` and `ate-api-server` (CAPA), or an S3-compatible store with its credentials in a Secret; `kagent.harness.snapshotLocation` names it.
3. Node placement, when the installation dedicates a node pool to the sandboxes: `substrate.atelet.nodeSelector` / `tolerations` and `kagent.substrateWorkerPool.template`.
4. Nothing else: the bootstrap, the database on the platform's CNPG Cluster, the exceptions and the policies come with the chart.
