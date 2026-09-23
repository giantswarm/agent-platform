# agent-platform

Giant Swarm Agent Platform — MCP gateway deploy unit, packaged as an
app-of-apps meta-package. Renders each component (muster, agentgateway, kagent,
klaus-gateway, valkey, agent-platform-mcps, agent-sandbox, …) and the consumer-side
connectivity layer as a Flux OCIRepository + HelmRelease (Flux is the only
render engine), with each component's version expressed as a value RANGE rather
than a Chart.yaml pin — so a component release rolls forward with no PR to this
chart. Brings its own Flux engine (the flux-engine subchart: Flux Operator +
FluxInstance) where a cluster has none, so `helm install` yields a running
platform; a cluster that runs Flux sets components.flux.enabled=false. Each
component ships its own CRDs (app-owned CRDs, upgraded via Flux CreateReplace);
the Gateway API CRDs and GatewayClass remain cluster-level prerequisites — see
README.

**Homepage:** <https://github.com/giantswarm/agent-platform>

## Source Code

* <https://github.com/giantswarm/agent-platform>

## Requirements

| Repository | Name | Version |
|------------|------|---------|
|  | flux-engine | 0.1.0 |

## One release per target cluster

The serving slice ([#326](https://github.com/giantswarm/agent-platform/issues/326)) and the runtime slice
([#317](https://github.com/giantswarm/agent-platform/issues/317)) are values profiles of this chart that cluster-manager
installs as **one `<cluster>-agent-platform` release per target cluster**, the slices as toggles in its values — created
with the first slice, its values updated in place when the second is switched on, never two releases of the chart on one
cluster (both slices need agentgateway's cluster-scoped CRDs, which have exactly one owner). Such a release runs beside
the platform's own release on the installation's cluster, or installs onto a workload cluster that runs no Flux
([#328](https://github.com/giantswarm/agent-platform/issues/328), bumblebee-plans#46). Four knobs serve that shape:

- **`gitops.target.kubeConfig.secretRef`** — the target cluster. Set `name` (and `key` when the Secret's key is not
  helm-controller's default) to the kubeconfig Secret of the target in the release namespace — the fleet's
  `<cluster>-kubeconfig` — and every component HelmRelease this chart renders carries `spec.kubeConfig.secretRef`
  verbatim, the connectivity release included: the installation's helm-controller installs each component into the
  target from the OCIRepositories in the release namespace, `dependsOn` intact, `install.createNamespace` creating the
  target namespaces there, the connectivity chart's `lookup` / Capabilities guards evaluated against the target. Unset,
  the render is byte-identical to a chart without the key. The bundled engine is refused with it (`components.flux.enabled`
  must be `false`: no Flux is installed into a workload cluster, nor into a cluster that runs one), and no Helm hook of
  this chart renders with it (a hook Job runs where the chart is installed, not on the target).
- **`components.muster.enabled`, `components.dicebear.enabled`** (default `true`) — a slice release turns them off: the
  installation's muster serves every agent, the avatars are the portal's. The connectivity chart reads the same roster
  and renders none of their wiring while they are off (muster's `/mcp` route and egress policy, every policy rule that
  selects its pods, the avatars host in the portal's CSP). `components.agentgateway.enabled` follows the target: **on**
  for a workload cluster, **off** beside the platform's release, which owns the controller and its CRDs.
- **`components.<name>.ownedCrds`** — one owner per cluster-scoped component. When a CRD a component installs already
  exists on the cluster the chart renders against and its Flux labels (`helm.toolkit.fluxcd.io/name`, `/namespace` —
  helm-controller stamps them on every object of a release, `crds/` included) name another HelmRelease than the one this
  render produces, the render fails naming that release: remove it first, or turn the component off in this release.
  Skipped with the target knob, where the render's lookups see the installation while the components land on the target
  (what the target already runs is the composer's to detect). A CRD without the labels is left to Helm as before.
  `components.gpu-operator` adds its own guard on the same principle — a `ClusterPolicy`, HelmRelease or App of the
  operator that is not this release's fails the render naming the handover (root README, "The GPU operator").

`make verify-target` asserts the offline half (`tests/verify-target.py`): the knob's stamp and byte-identity, the
toggles on both charts, the engine guard, the hooks, the serving- and runtime-shaped toggle sets alone, combined and
targeted (`ci/test-slice-*-values.yaml`, `ci/test-target-values.yaml`). The two `lookup` guards need a cluster — on one
that runs Flux (the agentlab, an installation), from a checkout:

```sh
# a foreign helm-controller with the bundled engine on → "this cluster runs Flux; set components.flux.enabled=false …"
helm install t helm/agent-platform -n ap-guard --create-namespace --dry-run=server -f helm/agent-platform/ci/ci-values.yaml
# a second owner of a component's CRDs → "… has exactly one owner per cluster … belongs to HelmRelease <ns>/<name>. Remove that release first …"
helm install t helm/agent-platform -n ap-guard --create-namespace --dry-run=server -f helm/agent-platform/ci/test-slice-serving-values.yaml --set components.agentgateway.enabled=true
```

## Agent Substrate: worker capacity

On kagent API v2 an agent runs as an Agent Substrate actor inside a gVisor worker
pod of a `WorkerPool`. The connectivity chart renders one platform `Harness` that
names this pool (`kagent.substrateWorkerPool.name`, default `kagent-default`); the
pool is the platform's only capacity and scheduling knob, since the Generic agent
chart 1.x carries no per-agent `runtime`, `replicas`, `resources`, `nodeSelector`
or `tolerations`.

- **One worker hosts one actor at a time.** `kagent.substrateWorkerPool.replicas`
  (default `4`) therefore bounds the number of agents that can be *active at once*;
  an idle agent holds no worker. Raise it for more concurrency — the node pool
  budgets `replicas × the worker's limits`.
- **A worker's resources are one agent's sandbox.**
  `kagent.substrateWorkerPool.template.resources.limits` bound a single agent: the
  memory limit its RAM, the CPU limit its vCPU count. The defaults request
  `250m` / `512Mi` and limit to `2` vCPU / `2Gi`. Measured in agentlab: an idle
  worker holds ~16Mi and a golden-booted actor ~20Mi; a Go ADK turn's working set
  (the model client, tool calls and gVisor overhead) stays well under `2Gi`.
  Raise the limits for heavier agents.
- **One CPU feature set per pool — one vendor and generation, not just one
  architecture.** An actor's golden snapshot is a gVisor checkpoint, and gVisor
  restores it only on a host whose CPU offers every feature the checkpoint
  recorded; a worker on a CPU that lacks one fails every restore placed on it
  (`incompatible FeatureSet: missing features: …` in its log every 30 s, the
  turn `request timed out`) and the actor never recovers.
  `kagent.substrateWorkerPool.template.nodeSelector` pins the pool as precisely
  as the provider's node labels allow: the architecture (`kubernetes.io/arch:
  amd64` by default; an arm64 installation sets `arm64`), and on CAPA — where
  Karpenter consolidation may replace a node with another vendor's at any time —
  the vendor, `karpenter.k8s.aws/instance-cpu-manufacturer: amd`, and the CPU
  generation, `karpenter.k8s.aws/instance-generation: "6"` (quoted — a label
  value is a string, and the render refuses a bare number), both rendered by
  the fleet template from the installation's values: a snapshot taken on a
  newer generation of one vendor does not restore on an older one either
  (gazelle, 2026-09-14: goldens from `m7a` workers, generation 7, failed on
  `c5ad`, generation 5, `missing features: … avx512f …`;
  giantswarm/agent-platform#457). Pin a generation that is one CPU model
  across its families (for AMD on AWS 6, `c6a`/`m6a`/`r6a`, or 7,
  `c7a`/`m7a`/`r7a`; 5 mixes Naples `m5a`/`r5a` with Rome `c5a`/`c5ad`) and
  one the goldens restore on — an older generation's golden restores on a
  newer one, never the reverse — and check the spot pool it leaves (the
  generation's families × the NodePool's sizes; three families is the floor).
  The family (`karpenter.k8s.aws/instance-family`) narrows to one type where
  even that matters; on CAPZ and the other providers the node pool
  (`giantswarm.io/machine-pool`) or `node.kubernetes.io/instance-type` pins
  it. Recognise a mixed pool by `kubectl get nodes -L <the label>` showing two
  values under it (giantswarm/agent-platform#429, #457). `make
  verify-workerpool` asserts the pin reaches the WorkerPool as written.

`kagent.substrateWorkerPool.template` also accepts `labels`, `annotations`,
`tolerations`, `nodeAffinity` and `priorityClassName` — the
`WorkerPool.spec.template` fields of the pinned Substrate line, and nothing else:
the CRD is a structural schema, so a key it does not know is **pruned at
admission, silently**, and the render refuses it instead
(`agent-platform.validateWorkerPool`). See UPGRADE.md, "the Generic chart's
per-agent placement values are gone, capacity is the WorkerPool".

### The worker image follows the chart's Substrate pin (giantswarm/agent-platform#466)

The pool's `workerImage` — the gVisor worker every actor runs in — is **derived by
this chart** from `components.substrate.versionRange`'s floor:
`<substrate.image.registry>/ateom-gvisor:<floor>` (`gsoci.azurecr.io/giantswarm/
substrate/ateom-gvisor:1.0.0` today), merged over the kagent block the chart forwards.
The kagent chart stamps a worker of its own at publish (the Substrate its build was
published against), and that stamp never reaches the cluster: the atelet the
substrate release installs and the worker the WorkerPool runs are one Substrate
release **whatever kagent build the kagent range admits**. Chart 4.15.2 showed
why — its open kagent range admitted a build that stamped the next Substrate's
worker under the chart's older atelet; that Substrate had renamed the pause bundle,
every golden boot failed on `bundles/_pause/config.json`, every AgentTemplate
stayed `Ready=False … compiling`, and nothing on any hop named the skew.

What follows from it:

- **`kagent.substrateWorkerPool.workerImage` stays empty.** An installation that
  sets it names an `ateom-gvisor` image **tagged with the pinned release** (a mirror
  by a path other than `substrate.image.registry`, which the derived image already
  follows); another tag, or a digest alone, fails the render naming the key, the
  release and the derived image.
- **The Substrate range confines one runtime contract**: an exact version (the
  BOM's `1.0.0`) or `>=X.Y.Z <X.(Y+1).0`, the ceiling of the pinned minor and no
  `-0` anywhere — Flux's Masterminds semver skips every prerelease while no bound
  of a range carries one and evaluates them all once one does, so `<1.1.0-0`
  would admit the line's dev builds. A patch of the line is carried patches or a
  rebuild on the same upstream pin and never changes the worker and atelet bundle
  layout; a re-pin onto another upstream release is at least a minor. The worker
  follows the floor and the atelet follows what Flux resolves, so a later patch
  of the pinned minor may reach the control plane ahead of the worker and a
  `1.1.0` never does. `0.x`, `~`, `^`, a `-0` bound, a `<=` ceiling, a patch
  ceiling, a floor alone or the former `>=X.Y.Z-gs.N <X.Y.(Z+1)-0` fail the
  render (`agent-platform.substrate.validateRange`).
- **A Substrate re-pin is one values change**, `components.substrate.versionRange`
  and `components.substrate-crds.versionRange` together; it **rolls the pool once**
  (the WorkerPool's `workerImage` changes; one worker at a time under the budget, a
  turn in flight on a replaced worker is lost). The kagent range moves on its own,
  when the kagent line's pin moves — `make verify-worker-image` fails the day the
  kagent build the range admits was published against another Substrate release
  than the chart pins (its `Chart.yaml` names it), so the two are moved together.

### The one pool is the failure domain (giantswarm/agent-platform#472)

Every agent runs on this one pool, and a worker that goes loses the turn in flight
on it and every session paused on it (a pause checkpoint is node-local,
giantswarm/giantswarm#37795). On a Karpenter installation all four workers may
bin-pack onto one spot node (gazelle, 2026-09-15). The knobs, in the order they
help:

- **`kagent.substrateWorkerPool.podDisruptionBudget`** (`enabled: true`,
  `maxUnavailable: 1`, `unhealthyPodEvictionPolicy: AlwaysAllow` — **on by
  default**): a `PodDisruptionBudget` named after the pool in the kagent
  namespace, rendered by the connectivity chart (the kagent chart has no budget
  template), selecting `ate.dev/worker-pool: <name>`, the label Substrate's
  ate-controller puts on every worker pod. A voluntary drain — Karpenter
  consolidation, a node roll, `kubectl drain` — moves one worker at a time
  (`ALLOWED DISRUPTIONS 1` with four Ready workers) instead of the whole pool.
  Exactly one of `minAvailable` / `maxUnavailable`; the render refuses both,
  neither, and a policy outside the API's enum. `enabled: false` removes it. The
  knob never reaches the kagent release (`components.kagent.omitKeys`).
- **`kagent.substrateWorkerPool.template.annotations`**
  `karpenter.sh/do-not-disrupt: "true"` (**off**; an installation's choice):
  Karpenter's consolidation, drift and expiry never drain a worker node. Caveat:
  a drift or expiry roll then waits on that node until the NodePool's
  `terminationGracePeriod` — the fleet's NodePools set `30m`, a NodePool without
  one never rolls the node while a worker is on it; Substrate's own upgrade
  runbook wants worker nodes rolled deliberately anyway. Karpenter-only, harmless
  elsewhere (an annotation nothing reads).
- **`kagent.substrateWorkerPool.template.nodeSelector`**
  `karpenter.sh/capacity-type: on-demand` (**off**; the fleet template renders it
  from an installation's `agentPlatform.workerPoolCapacityType`): the only knob
  that covers a **spot interruption** — an involuntary disruption the budget and
  the annotation cannot stop: the instance goes two minutes after the notice —
  at on-demand prices for the worker nodes. Karpenter-only: **never on CAPZ,
  on-prem or a CAPA pool that cluster-aws node pools provision** — no node
  carries the label there and the workers stay `Pending`.

**Every change to `template`** — a label, an annotation, a selector, the
resources — rolls the pool's Deployment once (`RollingUpdate` 25 %/25 %, one
worker at a time under the budget; about a minute per worker on a node that is
up, about three when Karpenter has to launch one). A turn in flight on a replaced
worker is lost, a session paused on it too; the goldens are untouched (a template
change re-snapshots nothing). Land it in a quiet window.

**Spread.** `WorkerPool.spec.template` carries `topologySpreadConstraints` and
`podAntiAffinity` from the Substrate line's `1.0.0` on (the carried patch
giantswarm/giantswarm#37797) — every release before it prunes both silently. The
render **refuses the two keys** naming the key, the floor and the range while
`components.substrate.versionRange`'s floor is below that release
(`agent-platform.substrate.workerPoolSpreadFloor` names it), and forwards them
verbatim from it on. Recommended: hostname `maxSkew: 1`, `minDomains: 2`,
`whenUnsatisfiable: DoNotSchedule` (the provisioner adds the second node) and
zone `maxSkew: 1`, `ScheduleAnyway`, both with a `labelSelector` on
`ate.dev/worker-pool: <name>` — what the fleet template renders behind its knob.
A spread is a template change and rolls the pool's Deployment once (above).
`make verify-workerpool` asserts all of it: the budget, the two knobs reaching the
kagent release and the `WorkerPool` verbatim, the spread refused below the floor
and forwarded at it, still exactly one `WorkerPool`.

## Voluntary disruption

The platform's core runs as single replicas — the agentgateway data plane, muster, the kagent controller, klaus-gateway, agent-manager — and each carries long-lived streams (MCP sessions, A2A and portal SSE turns, the LLM listener). A voluntary eviction (Karpenter consolidation, a node drain) cuts them mid-turn (giantswarm/agent-platform#431). Two guards, both on by default:

- **`karpenter.sh/do-not-disrupt: "true"`** on the pods that carry live streams — the agentgateway data plane (`gateway.parameters.podAnnotations`, rendered by the connectivity chart through `AgentgatewayParameters`), muster (`muster.podAnnotations`), the kagent controller (`kagent.controller.podAnnotations`) and klaus-gateway (`klausGateway.podAnnotations`). Karpenter's consolidation, drift and expiry work around the node until the NodePool's `terminationGracePeriod`; an involuntary disruption (spot interruption, node failure) is not affected. Set the value to `"false"` (or drop the key) to opt out per component.
- **`PodDisruptionBudget minAvailable: 1`** on muster (`muster.podDisruptionBudget`, the muster chart's knob), the kagent controller (`kagent.controller.pdb`, the kagent chart's knob), klaus-gateway (`klausGateway.podDisruptionBudget`, the klaus-gateway chart's knob, 1.1.0+) and agent-manager (`agentManager.podDisruptionBudget`, rendered by the connectivity chart). With one replica the budget refuses every voluntary eviction: Karpenter reports `DisruptionBlocked … pdb`, a node drain waits for its drain timeout (the fleet's Karpenter NodePools force-terminate after `terminationGracePeriod: 30m`; a MachineDeployment after its `nodeDrainTimeout`). `unhealthyPodEvictionPolicy: AlwaysAllow` where the chart supports it keeps a pod that is not Ready evictable, so a crash-looping component never wedges a drain. `enabled: false` on a knob removes that budget. The data plane's budget is `gateway.parameters.podDisruptionBudget` (with two replicas, so a drain can still proceed one pod at a time).

Two replicas are the long-term answer for the stateless components; the budgets on one replica are the interim guard. Backstage's budget is the backstage chart's (`maxUnavailable: 1` today, which protects nothing with one replica) — a change there, not here.

muster-valkey — muster's OAuth token store, one replica on an RWO volume — carries the same two guards (giantswarm/agent-platform#439): `karpenter.sh/do-not-disrupt` through the valkey subchart's `valkey.valkey.podAnnotations`, and a `PodDisruptionBudget muster-valkey` (`valkey.podDisruptionBudget`, `minAvailable: 1`, `AlwaysAllow`) rendered by the connectivity chart, since neither the wrapper nor the upstream subchart has a budget knob. The budget key never reaches the valkey release (`components.valkey.omitKeys`).

The Substrate worker pool — four workers, one actor each, every agent's runtime — carries a budget of a different shape (giantswarm/agent-platform#472): `kagent.substrateWorkerPool.podDisruptionBudget`, `maxUnavailable: 1`, rendered by the connectivity chart in the kagent namespace on the pods labelled `ate.dev/worker-pool: <pool>`, so a voluntary drain moves one worker at a time and the pool keeps three. No `karpenter.sh/do-not-disrupt` by default — it stays a documented knob of the pool template, with the on-demand capacity-type selector, in "The one pool is the failure domain" above.

## Placement of the stateful singletons

The guards above hold off Karpenter's *voluntary* disruption. A spot reclaim is involuntary: the instance goes two minutes after the notice whatever the budget says, and a PDB-blocked pod is then killed with its termination grace instead of being moved first — on gazelle (fifteen spot workers) one reclaim wave took muster-valkey down for two minutes (every token refresh blocked inside muster for up to 27 s) and killed klaus-gateway mid-turn, whose replacement then waited four minutes on a `Multi-Attach` error for its volume (giantswarm/agent-platform#439). On an installation whose workers are Karpenter spot capacity, pin the four single-replica stateful pods — muster, muster-valkey, the kagent controller, klaus-gateway — to on-demand capacity:

```yaml
scheduling:
  singletons:
    nodeSelector:
      karpenter.sh/capacity-type: on-demand
```

The map is merged into each component's own `nodeSelector` (`muster.nodeSelector`, `valkey.valkey.nodeSelector`, `kagent.controller.nodeSelector`, `klausGateway.nodeSelector`; a key a component sets itself wins) and `scheduling.singletons.tolerations` is appended to each one's `tolerations` (for a dedicated, tainted pool). Karpenter launches one small on-demand node for them from a NodePool that admits `on-demand` in the volumes' zone — the fleet's NodePools admit both capacity types and carry no taint — and, with the guards above, leaves it alone. Cost: one `xlarge`-class on-demand instance per such installation. Empty (the default) forwards nothing and every installation renders as before; set it only where nodes carry the label (a cluster without Karpenter — CAPZ, on-prem — would leave the four pods Pending). Enabling rolls each of the four pods once; the two with a `Recreate` strategy (muster-valkey, klaus-gateway) are down until the node is up, about two minutes on AWS. klaus-gateway takes the keys from chart 1.3.3 on (giantswarm/klaus-gateway#253; earlier 1.x schemas refused every key under `nodeSelector`), which the `1.x` range resolves. The block is the meta chart's alone: it is merged before any release renders and held back from the connectivity release. `make verify-disruption` asserts the merge, the precedence and the default.

## The kagent controller's requests follow a VerticalPodAutoscaler

`kagent.controller.vpa` (giantswarm/agent-platform#455) puts a `VerticalPodAutoscaler` on the kagent controller, rendered by the connectivity chart in the kagent namespace (the kagent chart has no VPA knob; the same pattern as the muster-valkey budget). `enabled: auto` renders it where the cluster serves `autoscaling.k8s.io/v1` — resolved once here with the other cluster-shape knobs, an explicit `true` / `false` wins. The mode is `InPlaceOrRecreate`: with one replica behind the budget above an evicting mode could never apply, so the running pod's requests are resized in place, with no eviction and no roll; a resize that cannot apply in place falls back to an eviction the budget refuses, and the pod keeps its requests until its next roll. Only requests move (`controlledValues: RequestsOnly`), between the chart's requests (`minAllowed` 100m / 128Mi) and a step under its limits (`maxAllowed` 1900m / 1280Mi, under the `2` / `1536Mi` limits of `kagent.controller.resources` — requests equal to the limits would change the pod's QoS class, which no resize may do). The key is held back from the kagent release (`components.kagent.omitKeys`); the connectivity chart's README carries the guards and the opt-outs.

## Values

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| global.registry | string | `"gsoci.azurecr.io"` |  |
| global.imagePullSecrets | list | `[]` |  |
| global.domain | string | `""` |  |
| global.identity.issuerUrl | string | `""` |  |
| global.identity.clientId | string | `""` |  |
| global.identity.existingSecret | string | `""` |  |
| global.gatewayApi.parentRefs | list | `[]` |  |
| global.observability.metrics.serviceMonitor.enabled | string | `"auto"` | `auto` (default) renders the monitor objects when monitoring.coreos.com/v1 is served on the cluster, detected once by the meta chart (an offline `helm template` resolves to false unless the API is passed in); `true` / `false` force them on or off. |
| global.observability.metrics.serviceMonitor.interval | string | `""` |  |
| global.observability.metrics.serviceMonitor.labels | object | `{}` |  |
| global.observability.traces.otlp.endpoint | string | `""` |  |
| global.observability.traces.otlp.protocol | string | `""` |  |
| global.observability.traces.otlp.headers | object | `{}` |  |
| gitops.engine | string | `"flux"` |  |
| gitops.interval | string | `"10m"` |  |
| gitops.namespace | string | `""` |  |
| gitops.targetNamespace | string | `""` |  |
| gitops.serviceAccountName | string | `""` |  |
| gitops.target.kubeConfig.secretRef.name | string | `""` |  |
| gitops.target.kubeConfig.secretRef.key | string | `""` |  |
| gitops.hooks.image.registry | string | `"gsoci.azurecr.io"` |  |
| gitops.hooks.image.repository | string | `"giantswarm/kubectl"` |  |
| gitops.hooks.image.tag | string | `"v1.37.0"` |  |
| gitops.hooks.helmImage.registry | string | `"gsoci.azurecr.io"` |  |
| gitops.hooks.helmImage.repository | string | `"giantswarm/alpine-k8s"` |  |
| gitops.hooks.helmImage.tag | string | `"1.37.0"` |  |
| gitops.self.enabled | string | `"auto"` |  |
| gitops.self.repository | string | `"oci://gsoci.azurecr.io/charts/giantswarm"` |  |
| gitops.self.insecure | bool | `false` |  |
| gitops.self.versionRange | string | `""` |  |
| gitops.self.semverFilter | string | `""` |  |
| gitops.self.interval | string | `"10m"` |  |
| gitops.retries | int | `5` |  |
| gitops.forbidInlineSecrets | bool | `false` |  |
| gitops.forbidPinnedLoginConnector | bool | `false` |  |
| components.flux.enabled | bool | `true` |  |
| components.muster.chart | string | `"muster"` |  |
| components.muster.repository | string | `"oci://gsoci.azurecr.io/charts/giantswarm"` |  |
| components.muster.versionRange | string | `">=5.12.0 <6.0.0"` |  |
| components.muster.valuesFrom | string | `"muster"` |  |
| components.muster.crds | string | `"CreateReplace"` |  |
| components.muster.driftDetection.mode | string | `"enabled"` |  |
| components.muster.enabled | bool | `true` |  |
| components.muster.ownedCrds[0] | string | `"mcpservers.muster.giantswarm.io"` |  |
| components.agentgateway.chart | string | `"agentgateway"` |  |
| components.agentgateway.repository | string | `"oci://gsoci.azurecr.io/charts/giantswarm"` |  |
| components.agentgateway.versionRange | string | `">=2.4.0 <3.0.0"` |  |
| components.agentgateway.valuesFrom | string | `"agentgateway"` |  |
| components.agentgateway.enabled | bool | `false` |  |
| components.agentgateway.ownedCrds[0] | string | `"agentgatewaypolicies.agentgateway.dev"` |  |
| components.agentgateway.crds | string | `"CreateReplace"` |  |
| components.valkey.chart | string | `"valkey"` |  |
| components.valkey.repository | string | `"oci://gsoci.azurecr.io/charts/giantswarm"` |  |
| components.valkey.versionRange | string | `"0.x"` |  |
| components.valkey.valuesFrom | string | `"valkey"` |  |
| components.valkey.enabled | bool | `true` |  |
| components.valkey.omitKeys[0] | string | `"podDisruptionBudget"` |  |
| components.agent-platform-mcps.chart | string | `"agent-platform-mcps"` |  |
| components.agent-platform-mcps.repository | string | `"oci://gsoci.azurecr.io/charts/giantswarm"` |  |
| components.agent-platform-mcps.versionRange | string | `"0.x"` |  |
| components.agent-platform-mcps.valuesFrom | string | `"agent-platform-mcps"` |  |
| components.agent-platform-mcps.enabled | bool | `false` |  |
| components.agent-platform-mcps.injectGlobal | bool | `false` |  |
| components.agent-platform-mcps.dependsOn[0] | string | `"muster"` |  |
| components.agent-platform-mcps.dependsOn[1] | string | `"agentgateway"` |  |
| components.kagent.chart | string | `"kagent"` |  |
| components.kagent.repository | string | `"oci://gsoci.azurecr.io/giantswarm/kagent/helm"` |  |
| components.kagent.versionRange | string | `">=1.0.0 <1.1.0"` |  |
| components.kagent.valuesFrom | string | `"kagent"` |  |
| components.kagent.dependsOn[0] | string | `"kagent-crds"` |  |
| components.kagent.dependsOn[1] | string | `"substrate-crds"` |  |
| components.kagent.dependsOn[2] | string | `"substrate"` |  |
| components.kagent.dependsOn[3] | string | `"agent-platform-connectivity"` |  |
| components.kagent.driftDetection.mode | string | `"enabled"` |  |
| components.kagent.omitKeys[0] | string | `"controllerRoute"` |  |
| components.kagent.omitKeys[1] | string | `"controller.vpa"` |  |
| components.kagent.omitKeys[2] | string | `"fluxServiceAccountName"` |  |
| components.kagent.omitKeys[3] | string | `"harness.snapshotStore"` |  |
| components.kagent.omitKeys[4] | string | `"modelConfigs"` |  |
| components.kagent.omitKeys[5] | string | `"oauth2ProxyIngress"` |  |
| components.kagent.omitKeys[6] | string | `"remoteMcpServers"` |  |
| components.kagent.omitKeys[7] | string | `"serviceMonitor"` |  |
| components.kagent.omitKeys[8] | string | `"uiRoute"` |  |
| components.kagent.omitKeys[9] | string | `"substrateWorkerPool.podDisruptionBudget"` |  |
| components.kagent.omitEmptyKeys[0] | string | `"substrateWorkerPool.workerImage"` |  |
| components.kagent.omitEmptyKeys[1] | string | `"harness.image"` |  |
| components.kagent.enabled | bool | `false` |  |
| components.kagent-crds.chart | string | `"kagent-crds"` |  |
| components.kagent-crds.repository | string | `"oci://gsoci.azurecr.io/giantswarm/kagent/helm"` |  |
| components.kagent-crds.versionRange | string | `">=1.0.0 <1.1.0"` |  |
| components.kagent-crds.valuesFrom | string | `"kagent-crds"` |  |
| components.kagent-crds.injectGlobal | bool | `false` |  |
| components.kagent-crds.ownedCrds[0] | string | `"modelconfigs.kagent.dev"` |  |
| components.substrate-crds.chart | string | `"substrate-crds"` |  |
| components.substrate-crds.repository | string | `"oci://gsoci.azurecr.io/giantswarm/substrate/helm"` |  |
| components.substrate-crds.versionRange | string | `">=1.0.0 <1.1.0"` |  |
| components.substrate-crds.valuesFrom | string | `"substrate-crds"` |  |
| components.substrate-crds.injectGlobal | bool | `false` |  |
| components.substrate-crds.targetNamespace | string | `"ate-system"` |  |
| components.substrate.chart | string | `"substrate"` |  |
| components.substrate.repository | string | `"oci://gsoci.azurecr.io/giantswarm/substrate/helm"` |  |
| components.substrate.versionRange | string | `">=1.0.0 <1.1.0"` |  |
| components.substrate.valuesFrom | string | `"substrate"` |  |
| components.substrate.injectGlobal | bool | `false` |  |
| components.substrate.targetNamespace | string | `"ate-system"` |  |
| components.substrate.dependsOn[0] | string | `"substrate-crds"` |  |
| components.substrate.dependsOn[1] | string | `"agent-platform-connectivity"` |  |
| components.substrate.valuesFromRefs[0].kind | string | `"ConfigMap"` |  |
| components.substrate.valuesFromRefs[0].name | string | `"kagent-images"` |  |
| components.substrate.valuesFromRefs[0].valuesKey | string | `"substrate-values.yaml"` |  |
| components.substrate.valuesFromRefs[0].optional | bool | `true` |  |
| components.substrate.omitEmptyKeys[0] | string | `"atelet.imageCache.pinnedImages"` |  |
| components.klaus-gateway.chart | string | `"klaus-gateway"` |  |
| components.klaus-gateway.repository | string | `"oci://gsoci.azurecr.io/charts/giantswarm"` |  |
| components.klaus-gateway.versionRange | string | `">=2.0.0 <4.0.0"` |  |
| components.klaus-gateway.valuesFrom | string | `"klausGateway"` |  |
| components.klaus-gateway.omitKeys[0] | string | `"observability.enabled"` |  |
| components.klaus-gateway.enabled | bool | `false` |  |
| components.agent-sandbox.chart | string | `"agent-sandbox"` |  |
| components.agent-sandbox.repository | string | `"oci://gsoci.azurecr.io/charts/giantswarm"` |  |
| components.agent-sandbox.versionRange | string | `"0.x"` |  |
| components.agent-sandbox.enabled | bool | `false` |  |
| components.agent-sandbox.injectGlobal | bool | `false` |  |
| components.agent-sandbox.crds | string | `"CreateReplace"` |  |
| components.agent-sandbox.dependsOn[0] | string | `"agent-platform-connectivity"` |  |
| components.model-manager.chart | string | `"model-manager"` |  |
| components.model-manager.repository | string | `"oci://gsoci.azurecr.io/charts/giantswarm"` |  |
| components.model-manager.versionRange | string | `">=0.23.0 <2.0.0"` |  |
| components.model-manager.valuesFrom | string | `"model-manager"` |  |
| components.model-manager.enabled | bool | `true` |  |
| components.model-manager.dependsOn[0] | string | `"muster"` |  |
| components.model-manager.dependsOn[1] | string | `"kagent"` |  |
| components.model-manager.dependsOn[2] | string | `"kserve-llmisvc-resources"` |  |
| components.agent-manager.chart | string | `"agent-manager"` |  |
| components.agent-manager.repository | string | `"oci://gsoci.azurecr.io/charts/giantswarm"` |  |
| components.agent-manager.versionRange | string | `"1.x"` |  |
| components.agent-manager.valuesFrom | string | `"agent-manager"` |  |
| components.agent-manager.enabled | bool | `false` |  |
| components.agent-manager.dependsOn[0] | string | `"muster"` |  |
| components.agent-manager.dependsOn[1] | string | `"kagent"` |  |
| components.vm-manager.chart | string | `"vm-manager"` |  |
| components.vm-manager.repository | string | `"oci://gsoci.azurecr.io/charts/giantswarm"` |  |
| components.vm-manager.versionRange | string | `">=0.22.0 <1.0.0"` |  |
| components.vm-manager.valuesFrom | string | `"vm-manager"` |  |
| components.vm-manager.enabled | bool | `false` |  |
| components.vm-manager.dependsOn[0] | string | `"muster"` |  |
| components.vm-manager.gatedValues[0] | string | `"vm-manager"` |  |
| components.vm-manager.gatedValues[1] | string | `"vmManager"` |  |
| components.cluster-manager.chart | string | `"cluster-manager"` |  |
| components.cluster-manager.repository | string | `"oci://gsoci.azurecr.io/charts/giantswarm"` |  |
| components.cluster-manager.versionRange | string | `">=0.4.2 <1.0.0"` |  |
| components.cluster-manager.valuesFrom | string | `"cluster-manager"` |  |
| components.cluster-manager.enabled | bool | `false` |  |
| components.cluster-manager.dependsOn[0] | string | `"muster"` |  |
| components.cluster-manager.gatedValues[0] | string | `"cluster-manager"` |  |
| components.cluster-manager.gatedValues[1] | string | `"clusterManager"` |  |
| components.backstage.chart | string | `"backstage"` |  |
| components.backstage.repository | string | `"oci://gsoci.azurecr.io/charts/giantswarm"` |  |
| components.backstage.versionRange | string | `">=1.0.0 <3.0.0"` |  |
| components.backstage.valuesFrom | string | `"backstage"` |  |
| components.backstage.omitKeys[0] | string | `"hostname"` |  |
| components.backstage.omitKeys[1] | string | `"parentRefs"` |  |
| components.backstage.omitKeys[2] | string | `"installationName"` |  |
| components.backstage.omitKeys[3] | string | `"extraScopes"` |  |
| components.backstage.omitKeys[4] | string | `"startUrlSearchParams"` |  |
| components.backstage.omitKeys[5] | string | `"enabledExtensions"` |  |
| components.backstage.omitKeys[6] | string | `"disabledExtensions"` |  |
| components.backstage.omitKeys[7] | string | `"skillsRepositories"` |  |
| components.backstage.omitKeys[8] | string | `"catalogs"` |  |
| components.backstage.omitKeys[9] | string | `"configReload"` |  |
| components.backstage.enabled | bool | `false` |  |
| components.backstage.dependsOn[0] | string | `"cloudnative-pg"` |  |
| components.backstage.dependsOn[1] | string | `"agent-platform-connectivity"` |  |
| components.mcp-kubernetes.chart | string | `"mcp-kubernetes"` |  |
| components.mcp-kubernetes.repository | string | `"oci://gsoci.azurecr.io/charts/giantswarm"` |  |
| components.mcp-kubernetes.versionRange | string | `">=1.1.1 <2.0.0"` |  |
| components.mcp-kubernetes.valuesFrom | string | `"mcp-kubernetes"` |  |
| components.mcp-kubernetes.omitKeys[0] | string | `"kubernetesAudience"` |  |
| components.mcp-kubernetes.enabled | bool | `false` |  |
| components.cloudnative-pg.chart | string | `"cloudnative-pg"` |  |
| components.cloudnative-pg.repository | string | `"oci://ghcr.io/cloudnative-pg/charts"` |  |
| components.cloudnative-pg.versionRange | string | `"0.29.x"` |  |
| components.cloudnative-pg.valuesFrom | string | `"cloudnative-pg"` |  |
| components.cloudnative-pg.enabled | bool | `false` |  |
| components.kserve-llmisvc-crd.chart | string | `"kserve-llmisvc-crd"` |  |
| components.kserve-llmisvc-crd.repository | string | `"oci://gsoci.azurecr.io/charts/giantswarm"` |  |
| components.kserve-llmisvc-crd.versionRange | string | `"0.5.x"` |  |
| components.kserve-llmisvc-crd.valuesFrom | string | `"kserve-llmisvc-crd"` |  |
| components.kserve-llmisvc-crd.enabled | bool | `false` |  |
| components.kserve-llmisvc-crd.ownedCrds[0] | string | `"llminferenceservices.serving.kserve.io"` |  |
| components.kserve-llmisvc-resources.chart | string | `"kserve-llmisvc-resources"` |  |
| components.kserve-llmisvc-resources.repository | string | `"oci://gsoci.azurecr.io/charts/giantswarm"` |  |
| components.kserve-llmisvc-resources.versionRange | string | `"0.5.x"` |  |
| components.kserve-llmisvc-resources.valuesFrom | string | `"kserve-llmisvc-resources"` |  |
| components.kserve-llmisvc-resources.enabled | bool | `false` |  |
| components.kserve-llmisvc-resources.dependsOn[0] | string | `"kserve-llmisvc-crd"` |  |
| components.kserve-runtime-configs.chart | string | `"kserve-runtime-configs"` |  |
| components.kserve-runtime-configs.repository | string | `"oci://gsoci.azurecr.io/charts/giantswarm"` |  |
| components.kserve-runtime-configs.versionRange | string | `"0.5.x"` |  |
| components.kserve-runtime-configs.valuesFrom | string | `"kserve-runtime-configs"` |  |
| components.kserve-runtime-configs.enabled | bool | `false` |  |
| components.kserve-runtime-configs.dependsOn[0] | string | `"kserve-llmisvc-crd"` |  |
| components.modelServing.enabled | bool | `false` |  |
| components.gpu-operator.chart | string | `"gpu-operator"` |  |
| components.gpu-operator.repository | string | `"oci://gsoci.azurecr.io/charts/giantswarm"` |  |
| components.gpu-operator.versionRange | string | `"1.x"` |  |
| components.gpu-operator.valuesFrom | string | `"gpu-operator"` |  |
| components.gpu-operator.valuesKey | string | `"gpu-operator"` |  |
| components.gpu-operator.injectGlobal | bool | `false` |  |
| components.gpu-operator.enabled | bool | `false` |  |
| components.gpu-operator.targetNamespace | string | `"kube-system"` |  |
| components.gpu-operator.crds | string | `"CreateReplace"` |  |
| components.gpu-operator.ownedCrds[0] | string | `"clusterpolicies.nvidia.com"` |  |
| components.dicebear.chart | string | `"dicebear"` |  |
| components.dicebear.repository | string | `"oci://gsoci.azurecr.io/charts/giantswarm"` |  |
| components.dicebear.versionRange | string | `"0.x"` |  |
| components.dicebear.valuesFrom | string | `"dicebear"` |  |
| components.dicebear.injectGlobal | bool | `false` |  |
| components.dicebear.enabled | bool | `true` |  |
| components.agent-platform-connectivity.chart | string | `"agent-platform-connectivity"` |  |
| components.agent-platform-connectivity.repository | string | `"oci://gsoci.azurecr.io/charts/giantswarm"` |  |
| components.agent-platform-connectivity.releasedWithChart | bool | `true` |  |
| components.agent-platform-connectivity.versionRange | string | `""` |  |
| components.agent-platform-connectivity.forwardAllValues | bool | `true` |  |
| components.agent-platform-connectivity.disableWaitForJobs | bool | `true` |  |
| components.agent-platform-connectivity.omitKeys[0] | string | `"flux-engine"` |  |
| components.agent-platform-connectivity.omitKeys[1] | string | `"scheduling"` |  |
| components.agent-platform-connectivity.omitKeys[2] | string | `"gpu-operator"` |  |
| components.agent-platform-connectivity.omitKeys[3] | string | `"kserve-runtime-configs"` |  |
| components.agent-platform-connectivity.dependsOn[0] | string | `"muster"` |  |
| components.agent-platform-connectivity.dependsOn[1] | string | `"agentgateway"` |  |
| components.agent-platform-connectivity.dependsOn[2] | string | `"substrate-crds"` |  |
| components.agent-platform-connectivity.dependsOn[3] | string | `"kagent-crds"` |  |
| components.agent-platform-connectivity.dependsOn[4] | string | `"cloudnative-pg"` |  |
| flux-engine | object | `{}` |  |
| dicebear.route.enabled | string | `"auto"` |  |
| dicebear.route.parentRefs | list | `[]` |  |
| dicebear.route.hostnames | list | `[]` |  |
| ingress.mode | string | `"muster-direct"` |  |
| ingress.parentRefs | list | `[]` |  |
| ingress.hostnames | list | `[]` |  |
| ingress.httpRoute.annotations | object | `{}` |  |
| ingress.httpRoute.labels | object | `{}` |  |
| ingress.httpRoute.muster.annotations | object | `{}` |  |
| ingress.httpRoute.muster.labels | object | `{}` |  |
| ingress.httpRoute.mcp.annotations | object | `{}` |  |
| ingress.httpRoute.mcp.labels | object | `{}` |  |
| ingress.httpRoute.timeouts | object | `{}` |  |
| ingress.backendTrafficPolicy.enabled | bool | `false` |  |
| ingress.backendTrafficPolicy.timeout | string | `"0s"` |  |
| ingress.backendTrafficPolicy.annotations | object | `{}` |  |
| ingress.backendTrafficPolicy.labels | object | `{}` |  |
| gateway.name | string | `"agentgateway"` |  |
| gateway.gatewayClassName | string | `"agentgateway"` |  |
| gateway.listeners[0].name | string | `"http"` |  |
| gateway.listeners[0].port | int | `8080` |  |
| gateway.listeners[0].protocol | string | `"HTTP"` |  |
| gateway.listeners[0].allowedRoutes.namespaces.from | string | `"Same"` |  |
| gateway.jwksEgress.enabled | bool | `false` |  |
| gateway.jwksEgress.namespace | string | `"giantswarm"` |  |
| gateway.jwksEgress.port | int | `5556` |  |
| gateway.jwksEgress.podSelector | object | `{}` |  |
| gateway.jwksEgress.external.fqdns | list | `[]` |  |
| gateway.jwksEgress.external.cidrs | list | `[]` |  |
| gateway.jwksEgress.external.port | int | `443` |  |
| gateway.parameters.enabled | bool | `true` |  |
| gateway.parameters.name | string | `""` |  |
| gateway.parameters.serviceType | string | `"ClusterIP"` |  |
| gateway.parameters.podSecurityContext.runAsNonRoot | bool | `true` |  |
| gateway.parameters.podSecurityContext.seccompProfile.type | string | `"RuntimeDefault"` |  |
| gateway.parameters.containerSecurityContext.allowPrivilegeEscalation | bool | `false` |  |
| gateway.parameters.containerSecurityContext.readOnlyRootFilesystem | bool | `true` |  |
| gateway.parameters.containerSecurityContext.runAsNonRoot | bool | `true` |  |
| gateway.parameters.containerSecurityContext.capabilities.drop[0] | string | `"ALL"` |  |
| gateway.parameters.containerSecurityContext.seccompProfile.type | string | `"RuntimeDefault"` |  |
| gateway.parameters.dataPlaneEnv[0].name | string | `"OTEL_EXPORTER_OTLP_ENDPOINT"` |  |
| gateway.parameters.dataPlaneEnv[0].value | string | `"http://otlp-gateway.kube-system.svc:4317"` |  |
| gateway.parameters.dataPlaneEnv[1].name | string | `"OTEL_EXPORTER_OTLP_PROTOCOL"` |  |
| gateway.parameters.dataPlaneEnv[1].value | string | `"grpc"` |  |
| gateway.parameters.dataPlaneVolumes | list | `[]` |  |
| gateway.parameters.dataPlaneVolumeMounts | list | `[]` |  |
| gateway.parameters.dataPlaneResources.requests.cpu | string | `"100m"` |  |
| gateway.parameters.dataPlaneResources.requests.memory | string | `"128Mi"` |  |
| gateway.parameters.dataPlaneResources.requests.ephemeral-storage | string | `"50Mi"` |  |
| gateway.parameters.dataPlaneResources.limits.cpu | string | `"2000m"` |  |
| gateway.parameters.dataPlaneResources.limits.memory | string | `"512Mi"` |  |
| gateway.parameters.dataPlaneResources.limits.ephemeral-storage | string | `"512Mi"` |  |
| gateway.parameters.replicas | int | `2` |  |
| gateway.parameters.podDisruptionBudget.enabled | bool | `true` |  |
| gateway.parameters.spread.enabled | bool | `true` |  |
| gateway.parameters.spread.topologyKeys[0] | string | `"kubernetes.io/hostname"` |  |
| gateway.parameters.spread.maxSkew | int | `1` |  |
| gateway.parameters.spread.whenUnsatisfiable | string | `"ScheduleAnyway"` |  |
| gateway.parameters.podAnnotations | object | `{}` |  |
| gateway.parameters.podLabels."observability.giantswarm.io/tenant" | string | `"giantswarm"` |  |
| gateway.http.maxBufferSize | string | `"8Mi"` |  |
| gateway.metricLabels.agent.enabled | bool | `true` |  |
| gateway.metricLabels.agent.expression | string | `"{{ include \"agent-platform.substrate.egressCall\" . }} ? request.headers[\"x-kagent-agent\"] : source.unverifiedWorkload.serviceAccount"` |  |
| gateway.metricLabels.agent_namespace.enabled | bool | `true` |  |
| gateway.metricLabels.agent_namespace.expression | string | `"{{ include \"agent-platform.substrate.egressCall\" . }} ? request.headers[\"x-kagent-agent-namespace\"] : source.unverifiedWorkload.namespace"` |  |
| gateway.metricLabels.user.enabled | bool | `true` |  |
| gateway.metricLabels.user.expression | string | `"{{ include \"agent-platform.substrate.egressCall\" . }} ? request.headers[\"x-kagent-user\"] : jwt.{{ include \"agent-platform.kagent.userIdClaim\" . }}"` |  |
| gatewayApi.gateway.create | bool | `false` |  |
| gatewayApi.gateway.tls.secretName | string | `""` |  |
| gatewayApi.gateway.serviceType | string | `"LoadBalancer"` |  |
| llmRouting.enabled | bool | `false` |  |
| llmRouting.listener.name | string | `"llm"` |  |
| llmRouting.listener.port | int | `8081` |  |
| llmRouting.backend.name | string | `"anthropic"` |  |
| llmRouting.backend.provider | string | `"anthropic"` |  |
| llmRouting.pathPrefixes[0] | string | `"/v1"` |  |
| llmRouting.routes./v1/messages | string | `"Messages"` |  |
| llmRouting.routes./v1/messages/count_tokens | string | `"AnthropicTokenCount"` |  |
| llmRouting.routes.* | string | `"Passthrough"` |  |
| llmRouting.modelConfigPolicy.enabled | bool | `true` |  |
| llmRouting.modelCatalog.enabled | bool | `true` |  |
| llmRouting.modelCatalog.name | string | `""` |  |
| llmRouting.modelCatalog.key | string | `"catalog.json"` |  |
| llmRouting.modelCatalog.providers.anthropic.models.claude-haiku-4-5.rates.input | string | `"1"` |  |
| llmRouting.modelCatalog.providers.anthropic.models.claude-haiku-4-5.rates.output | string | `"5"` |  |
| llmRouting.modelCatalog.providers.anthropic.models.claude-haiku-4-5.rates.cacheRead | string | `"0.1"` |  |
| llmRouting.modelCatalog.providers.anthropic.models.claude-haiku-4-5.rates.cacheWrite | string | `"1.25"` |  |
| llmRouting.modelCatalog.providers.anthropic.models.claude-opus-4-5.rates.input | string | `"5"` |  |
| llmRouting.modelCatalog.providers.anthropic.models.claude-opus-4-5.rates.output | string | `"25"` |  |
| llmRouting.modelCatalog.providers.anthropic.models.claude-opus-4-5.rates.cacheRead | string | `"0.5"` |  |
| llmRouting.modelCatalog.providers.anthropic.models.claude-opus-4-5.rates.cacheWrite | string | `"6.25"` |  |
| llmRouting.modelCatalog.providers.anthropic.models.claude-opus-5.rates.input | string | `"5"` |  |
| llmRouting.modelCatalog.providers.anthropic.models.claude-opus-5.rates.output | string | `"25"` |  |
| llmRouting.modelCatalog.providers.anthropic.models.claude-opus-5.rates.cacheRead | string | `"0.5"` |  |
| llmRouting.modelCatalog.providers.anthropic.models.claude-opus-5.rates.cacheWrite | string | `"6.25"` |  |
| llmRouting.modelCatalog.providers.anthropic.models.claude-opus-5-5.rates.input | string | `"4"` |  |
| llmRouting.modelCatalog.providers.anthropic.models.claude-opus-5-5.rates.output | string | `"20"` |  |
| llmRouting.modelCatalog.providers.anthropic.models.claude-opus-5-5.rates.cacheRead | string | `"0.2"` |  |
| llmRouting.modelCatalog.providers.anthropic.models.claude-opus-5-5.rates.cacheWrite | string | `"5"` |  |
| llmRouting.modelCatalog.providers.anthropic.models.claude-sonnet-4-5.rates.input | string | `"3"` |  |
| llmRouting.modelCatalog.providers.anthropic.models.claude-sonnet-4-5.rates.output | string | `"15"` |  |
| llmRouting.modelCatalog.providers.anthropic.models.claude-sonnet-4-5.rates.cacheRead | string | `"0.3"` |  |
| llmRouting.modelCatalog.providers.anthropic.models.claude-sonnet-4-5.rates.cacheWrite | string | `"3.75"` |  |
| llmRouting.modelCatalog.providers.anthropic.models.claude-sonnet-4-6.rates.input | string | `"3"` |  |
| llmRouting.modelCatalog.providers.anthropic.models.claude-sonnet-4-6.rates.output | string | `"15"` |  |
| llmRouting.modelCatalog.providers.anthropic.models.claude-sonnet-4-6.rates.cacheRead | string | `"0.3"` |  |
| llmRouting.modelCatalog.providers.anthropic.models.claude-sonnet-4-6.rates.cacheWrite | string | `"3.75"` |  |
| llmRouting.modelCatalog.providers.anthropic.models.claude-sonnet-5.rates.input | string | `"2"` |  |
| llmRouting.modelCatalog.providers.anthropic.models.claude-sonnet-5.rates.output | string | `"10"` |  |
| llmRouting.modelCatalog.providers.anthropic.models.claude-sonnet-5.rates.cacheRead | string | `"0.2"` |  |
| llmRouting.modelCatalog.providers.anthropic.models.claude-sonnet-5.rates.cacheWrite | string | `"2.5"` |  |
| llmRouting.modelCatalog.providers.anthropic.models.claude-fable-5-1.rates.input | string | `"10"` |  |
| llmRouting.modelCatalog.providers.anthropic.models.claude-fable-5-1.rates.output | string | `"50"` |  |
| llmRouting.modelCatalog.providers.anthropic.models.claude-fable-5-1.rates.cacheRead | string | `"0.25"` |  |
| llmRouting.modelCatalog.providers.anthropic.models.claude-fable-5-1.rates.cacheWrite | string | `"12.5"` |  |
| networkPolicy.enabled | bool | `true` |  |
| networkPolicy.flavor | string | `"auto"` | `auto` (default) selects `cilium` when cilium.io/v2 is served on the cluster and `kubernetes` otherwise; `cilium` / `kubernetes` force the flavor. |
| networkPolicy.additionalEgressCIDRs | list | `[]` |  |
| networkPolicy.additionalEgressFQDNs | list | `[]` |  |
| networkPolicy.musterInClusterMcpPorts[0] | int | `8080` |  |
| networkPolicy.musterInClusterMcpPorts[1] | int | `8443` |  |
| networkPolicy.kubernetes.apiServerCIDR | string | `"0.0.0.0/0"` |  |
| networkPolicy.kubernetes.worldExcludedCIDRs[0] | string | `"10.0.0.0/8"` |  |
| networkPolicy.kubernetes.worldExcludedCIDRs[1] | string | `"172.16.0.0/12"` |  |
| networkPolicy.kubernetes.worldExcludedCIDRs[2] | string | `"192.168.0.0/16"` |  |
| networkPolicy.kubernetes.worldExcludedCIDRs[3] | string | `"169.254.0.0/16"` |  |
| kyvernoPolicies.enabled | string | `"auto"` | `auto` (default) renders the Kyverno objects when kyverno.io/v1 is served on the cluster, detected once by the meta chart (an offline `helm template` resolves to false unless the API is passed in); `true` / `false` force them on or off. |
| kyvernoPolicies.policyExceptionNamespace | string | `"policy-exceptions"` |  |
| kyvernoPolicies.rules.privileged-containers | string | `"disallow-privileged-containers"` |  |
| kyvernoPolicies.rules.host-ports-none | string | `"disallow-host-ports"` |  |
| kyvernoPolicies.rules.host-path | string | `"disallow-host-path"` |  |
| kyvernoPolicies.rules.restricted-volumes | string | `"restrict-volume-types"` |  |
| kyvernoPolicies.rules.adding-capabilities | string | `"disallow-capabilities"` |  |
| kyvernoPolicies.rules.require-drop-all | string | `"disallow-capabilities-strict"` |  |
| kyvernoPolicies.rules.adding-capabilities-strict | string | `"disallow-capabilities-strict"` |  |
| kyvernoPolicies.rules.run-as-non-root | string | `"require-run-as-nonroot"` |  |
| kyvernoPolicies.rules.run-as-non-root-user | string | `"require-run-as-non-root-user"` |  |
| kyvernoPolicies.rules.privilege-escalation | string | `"disallow-privilege-escalation"` |  |
| kyvernoPolicies.rules.check-seccomp | string | `"restrict-seccomp"` |  |
| kyvernoPolicies.rules.check-seccomp-strict | string | `"restrict-seccomp-strict"` |  |
| kyvernoPolicies.rules.app-armor | string | `"restrict-apparmor-profiles"` |  |
| scheduling.singletons.nodeSelector | object | `{}` | Node labels the four stateful singletons (muster, muster-valkey, the kagent controller, klaus-gateway) must land on, merged into each component's own nodeSelector (its keys win). On a Karpenter spot installation: `karpenter.sh/capacity-type: on-demand`. Empty = as before. |
| scheduling.singletons.tolerations | list | `[]` | Tolerations appended to the four singletons' own, for a dedicated, tainted on-demand pool. The fleet's NodePools carry no taint. |
| extraObjects | list | `[]` |  |
| muster.enabled | bool | `true` |  |
| muster.image.registry | string | `"gsoci.azurecr.io"` |  |
| muster.fullnameOverride | string | `"muster"` |  |
| muster.crds.install | bool | `false` |  |
| muster.rbac.mcpServerEditor.subjects[0].apiGroup | string | `"rbac.authorization.k8s.io"` |  |
| muster.rbac.mcpServerEditor.subjects[0].kind | string | `"Group"` |  |
| muster.rbac.mcpServerEditor.subjects[0].name | string | `"giantswarm-ad:giantswarm-admins"` |  |
| muster.rbac.mcpServerEditor.subjects[1].apiGroup | string | `"rbac.authorization.k8s.io"` |  |
| muster.rbac.mcpServerEditor.subjects[1].kind | string | `"Group"` |  |
| muster.rbac.mcpServerEditor.subjects[1].name | string | `"giantswarm-github:giantswarm:giantswarm-admins"` |  |
| muster.rbac.workflowEditor.subjects[0].apiGroup | string | `"rbac.authorization.k8s.io"` |  |
| muster.rbac.workflowEditor.subjects[0].kind | string | `"Group"` |  |
| muster.rbac.workflowEditor.subjects[0].name | string | `"giantswarm-ad:giantswarm-admins"` |  |
| muster.rbac.workflowEditor.subjects[1].apiGroup | string | `"rbac.authorization.k8s.io"` |  |
| muster.rbac.workflowEditor.subjects[1].kind | string | `"Group"` |  |
| muster.rbac.workflowEditor.subjects[1].name | string | `"giantswarm-github:giantswarm:giantswarm-admins"` |  |
| muster.networkPolicy.enabled | bool | `true` |  |
| muster.networkPolicy.flavor | string | `"auto"` |  |
| muster.networkPolicy.cilium.allowClusterIngress | bool | `true` |  |
| muster.podAnnotations."application.giantswarm.io/team" | string | `"bumblebee"` |  |
| muster.podAnnotations."karpenter.sh/do-not-disrupt" | string | `"true"` |  |
| muster.podDisruptionBudget.enabled | bool | `true` |  |
| muster.podDisruptionBudget.minAvailable | int | `1` |  |
| muster.gatewayAPI.enabled | bool | `false` |  |
| muster.muster.oauth.server.enabled | bool | `true` |  |
| muster.muster.oauth.server.baseUrl | string | `""` |  |
| muster.muster.oauth.server.dex.issuerUrl | string | `""` |  |
| muster.muster.oauth.server.dex.clientId | string | `""` |  |
| muster.muster.oauth.server.existingSecret | string | `""` |  |
| muster.muster.oauth.server.storage.type | string | `"valkey"` |  |
| muster.muster.oauth.server.storage.valkey.url | string | `"muster-valkey:6379"` |  |
| muster.muster.oauth.server.storage.valkey.secretKeyPassword | string | `"valkey-password"` |  |
| muster.muster.toolsetPresets.infrastructure.description | string | `"The servers for the infrastructure underneath the platform (Giant Swarm installations' management clusters) — mcp-kubernetes, mcp-capi, mcp-prometheus."` |  |
| muster.muster.toolsetPresets.infrastructure.include[0].label | string | `"agent-platform.giantswarm.io/tool-group=infrastructure"` |  |
| muster.muster.toolsetPresets.agent-platform.description | string | `"The platform's own management surface — agent-manager, model-manager, vm-manager, cluster-manager and muster's core tools."` |  |
| muster.muster.toolsetPresets.agent-platform.include[0].label | string | `"agent-platform.giantswarm.io/tool-group=agent-platform"` |  |
| muster.muster.toolsetPresets.agent-platform.include[1].pattern | string | `"core_*"` |  |
| muster.muster.observability.otel.endpoint | string | `"http://otlp-gateway.kube-system.svc:4317"` |  |
| muster.muster.observability.otel.protocol | string | `"grpc"` |  |
| muster.muster.observability.otel.headers | string | `"X-Scope-OrgID=giantswarm"` |  |
| muster.muster.observability.metrics.prometheus.serviceMonitor.enabled | string | `"auto"` |  |
| muster.muster.observability.metrics.prometheus.serviceMonitor.interval | string | `"60s"` |  |
| muster.muster.observability.metrics.prometheus.serviceMonitor.labels."observability.giantswarm.io/tenant" | string | `"giantswarm"` |  |
| muster.muster.observability.metrics.prometheus.prometheusRule.enabled | string | `"auto"` |  |
| muster.muster.observability.metrics.prometheus.prometheusRule.labels."observability.giantswarm.io/tenant" | string | `"giantswarm"` |  |
| muster.muster.observability.grafanaDashboard.enabled | string | `"auto"` |  |
| muster.muster.observability.grafanaDashboard.folder | string | `"Agent Platform"` |  |
| muster.muster.observability.grafanaDashboard.giantswarm.enabled | bool | `true` |  |
| muster.muster.observability.grafanaDashboard.giantswarm.organization | string | `"Shared Org"` |  |
| valkey.ciliumNetworkPolicy.enabled | string | `"auto"` |  |
| valkey.vpa.enabled | bool | `false` |  |
| valkey.podDisruptionBudget.enabled | bool | `true` |  |
| valkey.podDisruptionBudget.minAvailable | int | `1` |  |
| valkey.podDisruptionBudget.maxUnavailable | string | `nil` |  |
| valkey.podDisruptionBudget.unhealthyPodEvictionPolicy | string | `"AlwaysAllow"` |  |
| valkey.valkey.fullnameOverride | string | `"muster-valkey"` |  |
| valkey.valkey.replicaCount | int | `1` |  |
| valkey.valkey.deploymentStrategy | string | `"Recreate"` |  |
| valkey.valkey.podAnnotations."karpenter.sh/do-not-disrupt" | string | `"true"` |  |
| valkey.valkey.auth.enabled | bool | `true` |  |
| valkey.valkey.auth.usersExistingSecret | string | `""` |  |
| valkey.valkey.auth.aclUsers.default.permissions | string | `"~* &* +@all"` |  |
| valkey.valkey.auth.aclUsers.default.passwordKey | string | `""` |  |
| valkey.valkey.dataStorage.enabled | bool | `true` |  |
| valkey.valkey.dataStorage.requestedSize | string | `"1Gi"` |  |
| valkey.valkey.valkeyConfig | string | `"maxmemory 640mb\nmaxmemory-policy volatile-lru\n"` |  |
| valkey.valkey.resources.requests.cpu | string | `"50m"` |  |
| valkey.valkey.resources.requests.memory | string | `"256Mi"` |  |
| valkey.valkey.resources.limits.cpu | string | `"200m"` |  |
| valkey.valkey.resources.limits.memory | string | `"1Gi"` |  |
| valkey.valkey.podSecurityContext.fsGroup | int | `1000` |  |
| valkey.valkey.podSecurityContext.runAsUser | int | `1000` |  |
| valkey.valkey.podSecurityContext.runAsGroup | int | `1000` |  |
| valkey.valkey.podSecurityContext.seccompProfile.type | string | `"RuntimeDefault"` |  |
| valkey.valkey.securityContext.allowPrivilegeEscalation | bool | `false` |  |
| valkey.valkey.securityContext.capabilities.drop[0] | string | `"ALL"` |  |
| valkey.valkey.securityContext.readOnlyRootFilesystem | bool | `true` |  |
| valkey.valkey.securityContext.runAsNonRoot | bool | `true` |  |
| valkey.valkey.securityContext.runAsUser | int | `1000` |  |
| valkey.valkey.securityContext.seccompProfile.type | string | `"RuntimeDefault"` |  |
| valkey.valkey.metrics.exporter.securityContext.allowPrivilegeEscalation | bool | `false` |  |
| valkey.valkey.metrics.exporter.securityContext.capabilities.drop[0] | string | `"ALL"` |  |
| valkey.valkey.metrics.exporter.securityContext.readOnlyRootFilesystem | bool | `true` |  |
| valkey.valkey.metrics.exporter.securityContext.runAsNonRoot | bool | `true` |  |
| valkey.valkey.metrics.exporter.securityContext.runAsUser | int | `1000` |  |
| valkey.valkey.metrics.exporter.securityContext.seccompProfile.type | string | `"RuntimeDefault"` |  |
| agent-platform-mcps.agentgateway.enabled | bool | `true` |  |
| agent-platform-mcps.agentgateway.viaMuster | bool | `false` |  |
| agent-platform-mcps.agentgateway.musterUrl | string | `"http://muster.agent-platform.svc.cluster.local:8090/mcp"` |  |
| agent-platform-mcps.mcpServers | list | `[]` |  |
| kagent.fullnameOverride | string | `"kagent"` |  |
| kagent.registry | string | `"gsoci.azurecr.io"` |  |
| kagent.controller.image.repository | string | `"giantswarm/kagent/controller"` |  |
| kagent.controller.agentImage.repository | string | `"giantswarm/kagent/golang-adk"` |  |
| kagent.controller.substrate.enabled | bool | `true` |  |
| kagent.controller.substrate.ateApiEndpoint | string | `"dns:///api.ate-system.svc:443"` |  |
| kagent.controller.substrate.atenetRouterURL | string | `"http://atenet-router.ate-system.svc:80"` |  |
| kagent.controller.substrate.defaultWorkerPool.name | string | `"kagent-default"` |  |
| kagent.controller.auth.mode | string | `"trusted-proxy"` |  |
| kagent.controller.auth.userIdClaim | string | `"email"` |  |
| kagent.controller.podAnnotations."karpenter.sh/do-not-disrupt" | string | `"true"` |  |
| kagent.controller.pdb.enabled | bool | `true` |  |
| kagent.controller.pdb.minAvailable | int | `1` |  |
| kagent.controller.pdb.maxUnavailable | string | `""` |  |
| kagent.controller.pdb.unhealthyPodEvictionPolicy | string | `"AlwaysAllow"` |  |
| kagent.controller.resources.requests.cpu | string | `"100m"` |  |
| kagent.controller.resources.requests.memory | string | `"128Mi"` |  |
| kagent.controller.resources.limits.cpu | int | `2` |  |
| kagent.controller.resources.limits.memory | string | `"1536Mi"` |  |
| kagent.controller.vpa.enabled | string | `"auto"` |  |
| kagent.controller.vpa.updateMode | string | `"InPlaceOrRecreate"` |  |
| kagent.controller.vpa.controlledValues | string | `"RequestsOnly"` |  |
| kagent.controller.vpa.minAllowed.cpu | string | `"100m"` |  |
| kagent.controller.vpa.minAllowed.memory | string | `"128Mi"` |  |
| kagent.controller.vpa.maxAllowed.cpu | string | `"1900m"` |  |
| kagent.controller.vpa.maxAllowed.memory | string | `"1280Mi"` |  |
| kagent.controller.metrics.enabled | bool | `false` |  |
| kagent.controller.env[0].name | string | `"OTEL_EXPORTER_OTLP_HEADERS"` |  |
| kagent.controller.env[0].value | string | `"X-Scope-OrgID=giantswarm"` |  |
| kagent.ui.image.repository | string | `"giantswarm/kagent/ui"` |  |
| kagent.substrateWorkerPool.create | bool | `true` |  |
| kagent.substrateWorkerPool.name | string | `"kagent-default"` |  |
| kagent.substrateWorkerPool.replicas | int | `4` |  |
| kagent.substrateWorkerPool.workerImage | string | `""` |  |
| kagent.substrateWorkerPool.sandboxClass | string | `"gvisor"` |  |
| kagent.substrateWorkerPool.podDisruptionBudget.enabled | bool | `true` |  |
| kagent.substrateWorkerPool.podDisruptionBudget.minAvailable | string | `nil` |  |
| kagent.substrateWorkerPool.podDisruptionBudget.maxUnavailable | int | `1` |  |
| kagent.substrateWorkerPool.podDisruptionBudget.unhealthyPodEvictionPolicy | string | `"AlwaysAllow"` |  |
| kagent.substrateWorkerPool.template.nodeSelector."kubernetes.io/arch" | string | `"amd64"` |  |
| kagent.substrateWorkerPool.template.resources.requests.cpu | string | `"250m"` |  |
| kagent.substrateWorkerPool.template.resources.requests.memory | string | `"512Mi"` |  |
| kagent.substrateWorkerPool.template.resources.limits.cpu | string | `"2"` |  |
| kagent.substrateWorkerPool.template.resources.limits.memory | string | `"2Gi"` |  |
| kagent.database.postgres.vectorEnabled | bool | `true` |  |
| kagent.database.postgres.bundled.image.repository | string | `"pgvector"` |  |
| kagent.database.postgres.bundled.image.name | string | `"pgvector"` |  |
| kagent.database.postgres.bundled.image.tag | string | `"pg18-trixie"` |  |
| kagent.namespaceOverride | string | `"kagent"` |  |
| kagent.podSecurityContext.runAsNonRoot | bool | `true` |  |
| kagent.podSecurityContext.seccompProfile.type | string | `"RuntimeDefault"` |  |
| kagent.securityContext.allowPrivilegeEscalation | bool | `false` |  |
| kagent.securityContext.capabilities.drop[0] | string | `"ALL"` |  |
| kagent.securityContext.seccompProfile.type | string | `"RuntimeDefault"` |  |
| kagent.providers.default | string | `"anthropic"` |  |
| kagent.providers.anthropic.provider | string | `"Anthropic"` |  |
| kagent.providers.anthropic.model | string | `"claude-sonnet-4-6"` |  |
| kagent.providers.anthropic.apiKeySecretRef | string | `"kagent-anthropic"` |  |
| kagent.providers.anthropic.apiKeySecretKey | string | `"ANTHROPIC_API_KEY"` |  |
| kagent.providers.anthropic.apiKey | string | `""` |  |
| kagent.providers.anthropic.config.promptCaching | bool | `true` |  |
| kagent.providers.anthropic.config.cacheTTL | string | `"5m"` |  |
| kagent.serviceMonitor.enabled | bool | `false` |  |
| kagent.serviceMonitor.interval | string | `"60s"` |  |
| kagent.serviceMonitor.labels."observability.giantswarm.io/tenant" | string | `"giantswarm"` |  |
| kagent.otel.tracing.enabled | string | `"auto"` |  |
| kagent.otel.tracing.exporter.otlp.endpoint | string | `"http://otlp-gateway.kube-system.svc:4317"` |  |
| kagent.otel.tracing.exporter.otlp.protocol | string | `"grpc"` |  |
| kagent.otel.tracing.exporter.otlp.insecure | bool | `true` |  |
| kagent.otel.logging.enabled | string | `"auto"` |  |
| kagent.otel.logging.exporter.otlp.endpoint | string | `"http://otlp-gateway.kube-system.svc:4317"` |  |
| kagent.otel.logging.exporter.otlp.insecure | bool | `true` |  |
| kagent.oauth2-proxy.enabled | bool | `false` |  |
| kagent.oauth2-proxy.fullnameOverride | string | `"kagent-oauth2-proxy"` |  |
| kagent.oauth2-proxy.namespaceOverride | string | `"kagent"` |  |
| kagent.oauth2-proxy.redis.enabled | bool | `false` |  |
| kagent.oauth2-proxy.sessionStorage.type | string | `"cookie"` |  |
| kagent.oauth2-proxy.extraVolumes[0].name | string | `"custom-templates"` |  |
| kagent.oauth2-proxy.extraVolumes[0].configMap.name | string | `"kagent-oauth2-proxy-templates"` |  |
| kagent.oauth2-proxy.extraVolumeMounts[0].name | string | `"custom-templates"` |  |
| kagent.oauth2-proxy.extraVolumeMounts[0].mountPath | string | `"/templates"` |  |
| kagent.oauth2-proxy.extraVolumeMounts[0].readOnly | bool | `true` |  |
| kagent.oauth2-proxy.config.existingSecret | string | `""` |  |
| kagent.oauth2-proxy.config.clientID | string | `""` |  |
| kagent.oauth2-proxy.config.clientSecret | string | `""` |  |
| kagent.oauth2-proxy.config.cookieSecret | string | `""` |  |
| kagent.oauth2-proxy.extraEnv[0].name | string | `"OIDC_ISSUER_URL"` |  |
| kagent.oauth2-proxy.extraEnv[0].value | string | `""` |  |
| kagent.oauth2-proxy.extraEnv[1].name | string | `"OIDC_REDIRECT_URL"` |  |
| kagent.oauth2-proxy.extraEnv[1].value | string | `""` |  |
| kagent.oauth2-proxy.extraEnv[2].name | string | `"UPSTREAM_URL"` |  |
| kagent.oauth2-proxy.extraEnv[2].value | string | `"http://kagent-ui:8080"` |  |
| kagent.oauth2-proxy.extraArgs.provider | string | `"oidc"` |  |
| kagent.oauth2-proxy.extraArgs.oidc-issuer-url | string | `"$(OIDC_ISSUER_URL)"` |  |
| kagent.oauth2-proxy.extraArgs.redirect-url | string | `"$(OIDC_REDIRECT_URL)"` |  |
| kagent.oauth2-proxy.extraArgs.upstream | string | `"$(UPSTREAM_URL)"` |  |
| kagent.oauth2-proxy.extraArgs.email-domain | string | `"*"` |  |
| kagent.oauth2-proxy.extraArgs.pass-authorization-header | bool | `true` |  |
| kagent.oauth2-proxy.extraArgs.set-authorization-header | bool | `true` |  |
| kagent.oauth2-proxy.extraArgs.approval-prompt | string | `"auto"` |  |
| kagent.oauth2-proxy.extraArgs.scope | string | `"openid profile email groups offline_access"` |  |
| kagent.oauth2-proxy.extraArgs.cookie-secure | bool | `true` |  |
| kagent.oauth2-proxy.extraArgs.cookie-samesite | string | `"lax"` |  |
| kagent.oauth2-proxy.extraArgs.cookie-refresh | string | `"10m"` |  |
| kagent.oauth2-proxy.extraArgs.reverse-proxy | bool | `true` |  |
| kagent.oauth2-proxy.extraArgs.skip-jwt-bearer-tokens | bool | `true` |  |
| kagent.oauth2-proxy.extraArgs.skip-auth-route | string | `"^/(health|login)$"` |  |
| kagent.oauth2-proxy.extraArgs.skip-auth-regex | string | `"^/(login|_next/static|_next/image|login-bg\\.(jpg|png|webp)|logo-.*\\.png|favicon\\.ico).*$"` |  |
| kagent.oauth2-proxy.extraArgs.custom-templates-dir | string | `"/templates"` |  |
| kagent.oauth2-proxy.service.type | string | `"ClusterIP"` |  |
| kagent.oauth2-proxy.service.portNumber | int | `4180` |  |
| kagent.oauth2-proxy.metrics.enabled | bool | `true` |  |
| kagent.oauth2-proxy.metrics.serviceMonitor.enabled | string | `"auto"` |  |
| kagent.oauth2-proxy.metrics.serviceMonitor.interval | string | `"60s"` |  |
| kagent.oauth2-proxy.metrics.serviceMonitor.labels."observability.giantswarm.io/tenant" | string | `"giantswarm"` |  |
| kagent.grafana-mcp.enabled | bool | `false` |  |
| kagent.kagent-tools.enabled | bool | `false` |  |
| kagent.kagent-tools.namespaceOverride | string | `"kagent"` |  |
| kagent.kmcp.enabled | bool | `false` |  |
| kagent.kmcp.namespaceOverride | string | `"kagent"` |  |
| kagent.fluxServiceAccountName | string | `"kagent-flux"` | The ServiceAccount the agents' Flux `HelmRelease`s execute as. The connectivity chart renders it in the kagent namespace whenever kagent is on, bound to `cluster-admin` by a namespace-scoped RoleBinding (full control of the kagent namespace, nothing outside it); this chart derives agent-manager's `flux.helmReleaseServiceAccount` from it and the portal's `agentPlatform.fluxServiceAccountName` is rendered from the same value — ONE value, three consumers, so they cannot disagree. Under a Flux multi-tenancy lockdown a `HelmRelease` without it runs as the rights-less default ServiceAccount and fails. Empty renders no identity and hands both callers an empty name. |
| kagent.harness.create | bool | `true` |  |
| kagent.harness.image | string | `""` |  |
| kagent.harness.snapshotLocation | string | `""` |  |
| kagent.harness.snapshotStore.prefix | string | `"kagent"` |  |
| kagent.harness.snapshotStore.crossplane.enabled | bool | `false` |  |
| kagent.harness.snapshotStore.crossplane.provider | string | `"aws"` |  |
| kagent.harness.snapshotStore.crossplane.providerConfigRef | string | `""` |  |
| kagent.harness.snapshotStore.crossplane.region | string | `""` |  |
| kagent.harness.snapshotStore.crossplane.observeOnly | bool | `false` |  |
| kagent.harness.snapshotStore.crossplane.tags | object | `{}` |  |
| kagent.harness.snapshotStore.crossplane.aws.bucketName | string | `""` |  |
| kagent.harness.snapshotStore.crossplane.aws.accountId | string | `""` |  |
| kagent.harness.snapshotStore.crossplane.aws.oidcProvider | string | `""` |  |
| kagent.harness.snapshotStore.crossplane.aws.roleName | string | `""` |  |
| kagent.harness.snapshotStore.crossplane.aws.lifecycleDays | int | `30` |  |
| kagent.harness.snapshotStore.crossplane.capz.storageAccountName | string | `""` |  |
| kagent.harness.snapshotStore.crossplane.capz.containerName | string | `""` |  |
| kagent.harness.snapshotStore.crossplane.capz.resourceGroup | string | `""` |  |
| kagent.harness.snapshotStore.crossplane.capz.subscriptionId | string | `""` |  |
| kagent.harness.snapshotStore.crossplane.capz.replicationType | string | `"LRS"` |  |
| kagent.harness.snapshotStore.crossplane.capz.lifecycleDays | int | `30` |  |
| kagent.harness.snapshotStore.crossplane.capz.workloadIdentity.oidcIssuerUrl | string | `""` |  |
| kagent.harness.snapshotStore.crossplane.capz.workloadIdentity.identityName | string | `""` |  |
| kagent.harness.snapshotStore.crossplane.capz.workloadIdentity.providerKubernetes.providerConfigRef | string | `""` |  |
| kagent.harness.snapshotStore.crossplane.capz.workloadIdentity.providerKubernetes.serviceAccount.name | string | `""` |  |
| kagent.harness.snapshotStore.crossplane.capz.workloadIdentity.providerKubernetes.serviceAccount.namespace | string | `"crossplane"` |  |
| kagent.harness.snapshotStore.s3proxy.enabled | bool | `false` |  |
| kagent.harness.snapshotStore.s3proxy.image.repository | string | `"gsoci.azurecr.io/giantswarm/s3proxy"` |  |
| kagent.harness.snapshotStore.s3proxy.image.tag | string | `"4.1.1"` |  |
| kagent.harness.snapshotStore.s3proxy.replicas | int | `2` |  |
| kagent.harness.snapshotStore.s3proxy.javaOpts | string | `"-XX:MaxRAMPercentage=70"` |  |
| kagent.harness.snapshotStore.s3proxy.resources.requests.cpu | string | `"250m"` |  |
| kagent.harness.snapshotStore.s3proxy.resources.requests.memory | string | `"1Gi"` |  |
| kagent.harness.snapshotStore.s3proxy.resources.requests.ephemeral-storage | string | `"256Mi"` |  |
| kagent.harness.snapshotStore.s3proxy.resources.limits.memory | string | `"1Gi"` |  |
| kagent.harness.snapshotStore.s3proxy.resources.limits.ephemeral-storage | string | `"1Gi"` |  |
| kagent.harness.snapshotStore.s3proxy.azure.endpoint | string | `""` |  |
| kagent.harness.snapshotStore.s3proxy.azure.account | string | `""` |  |
| kagent.harness.snapshotStore.s3proxy.azure.container | string | `""` |  |
| kagent.harness.snapshotStore.s3proxy.azure.accountKeySecretRef.name | string | `""` |  |
| kagent.harness.snapshotStore.s3proxy.azure.accountKeySecretRef.key | string | `""` |  |
| kagent.harness.env[0].name | string | `"KAGENT_PROPAGATE_TOKEN"` |  |
| kagent.harness.env[0].value | string | `"true"` |  |
| kagent.harness.env[1].name | string | `"OTEL_LOGGING_ENABLED"` |  |
| kagent.harness.env[1].value | string | `"true"` |  |
| kagent.harness.env[2].name | string | `"OTEL_EXPORTER_OTLP_HEADERS"` |  |
| kagent.harness.env[2].value | string | `"X-Scope-OrgID=giantswarm"` |  |
| kagent.harness.env[3].name | string | `"KAGENT_TRACE_FLUSH_TIMEOUT_MS"` |  |
| kagent.harness.env[3].value | string | `"500"` |  |
| kagent.harness.allowedAgentTemplates.selector.matchLabels."agent-platform.giantswarm.io/harness" | string | `"kagent"` |  |
| kagent.harness.allowedAgentTemplates.selector.matchLabels."kagent.dev/harness" | string | `""` |  |
| kagent.controllerRoute.enabled | bool | `false` |  |
| kagent.controllerRoute.hostname | string | `""` |  |
| kagent.controllerRoute.parentRef.name | string | `"giantswarm-default"` |  |
| kagent.controllerRoute.parentRef.namespace | string | `"envoy-gateway-system"` |  |
| kagent.controllerRoute.grpc.services."kagent.api.v1alpha1.AgentInstanceService" | list | `[]` |  |
| kagent.controllerRoute.grpc.services."kagent.api.v1alpha1.AgentTemplateService" | list | `[]` |  |
| kagent.controllerRoute.grpc.services."kagent.api.v1alpha1.ModelService" | list | `[]` |  |
| kagent.controllerRoute.grpc.services."kagent.api.v1alpha1.SystemService" | list | `[]` |  |
| kagent.controllerRoute.grpc.services."lf.a2a.v1.A2AService" | list | `[]` |  |
| kagent.controllerRoute.jwtAuthentication.enabled | bool | `true` |  |
| kagent.controllerRoute.jwtAuthentication.mode | string | `"Strict"` |  |
| kagent.controllerRoute.jwtAuthentication.issuer | string | `""` |  |
| kagent.controllerRoute.jwtAuthentication.jwks.host | string | `"dex.giantswarm.svc.cluster.local"` |  |
| kagent.controllerRoute.jwtAuthentication.jwks.port | int | `5556` |  |
| kagent.controllerRoute.jwtAuthentication.jwks.path | string | `"/keys"` |  |
| kagent.controllerRoute.jwtAuthentication.jwks.tls.enabled | bool | `false` |  |
| kagent.controllerRoute.jwtAuthentication.jwks.tls.caSecretName | string | `""` |  |
| kagent.uiRoute.enabled | bool | `false` |  |
| kagent.uiRoute.hostname | string | `""` |  |
| kagent.uiRoute.parentRef.name | string | `"giantswarm-default"` |  |
| kagent.uiRoute.parentRef.namespace | string | `"envoy-gateway-system"` |  |
| kagent.uiRoute.backendTrafficPolicy.enabled | bool | `true` |  |
| kagent.uiRoute.backendTrafficPolicy.timeout | string | `"60s"` |  |
| kagent.uiRoute.backendTrafficPolicy.annotations | object | `{}` |  |
| kagent.uiRoute.backendTrafficPolicy.labels | object | `{}` |  |
| kagent.modelConfigs | list | `[]` |  |
| kagent.remoteMcpServers | list | `[]` |  |
| dashboards.enabled | bool | `true` |  |
| dashboards.namespace | string | `""` |  |
| dashboards.organization | string | `"Shared Org"` |  |
| dashboards.folder | string | `"Agent Platform"` |  |
| postgres.enabled | bool | `false` |  |
| postgres.namespace | string | `"kagent"` |  |
| postgres.clusterName | string | `"kagent-pg"` |  |
| postgres.instances | int | `3` |  |
| postgres.storage.size | string | `"20Gi"` |  |
| postgres.storage.storageClass | string | `""` |  |
| postgres.image.name | string | `""` |  |
| postgres.imagePullSecrets | list | `[]` |  |
| postgres.affinity | object | `{}` |  |
| postgres.vector.enabled | bool | `false` |  |
| postgres.vector.extensionImage.reference | string | `""` |  |
| postgres.applicationDatabase.name | string | `"kagent"` |  |
| postgres.applicationDatabase.owner | string | `"kagent"` |  |
| postgres.applicationDatabase.schema | string | `"kagent"` |  |
| postgres.applicationDatabase.ensure | string | `"present"` |  |
| postgres.sessionsDatabase.enabled | bool | `false` |  |
| postgres.sessionsDatabase.name | string | `"sessions"` |  |
| postgres.sessionsDatabase.owner | string | `"sessions"` |  |
| postgres.databases.substrate.enabled | bool | `true` |  |
| postgres.databases.substrate.name | string | `"substrate"` |  |
| postgres.databases.substrate.component | string | `"substrate"` |  |
| postgres.databases.substrate.extensions | list | `[]` |  |
| postgres.databases.substrate.reclaimPolicy | string | `"retain"` |  |
| postgres.databases.substrate.secretNamespaces[0] | string | `"ate-system"` |  |
| postgres.databases.kagent-v2.enabled | bool | `true` |  |
| postgres.databases.kagent-v2.name | string | `"kagent_v2"` |  |
| postgres.databases.kagent-v2.extensions[0] | string | `"vector"` |  |
| postgres.databases.kagent-v2.reclaimPolicy | string | `"retain"` |  |
| postgres.databases.kagent-v2.component | string | `"kagent"` |  |
| postgres.databases.kagent-v2.secretNamespaces | list | `[]` |  |
| postgres.backup.enabled | bool | `false` |  |
| postgres.backup.method | string | `"plugin"` |  |
| postgres.backup.schedule | string | `"0 0 2 * * *"` |  |
| postgres.backup.immediate | bool | `true` |  |
| postgres.backup.suspend | bool | `false` |  |
| postgres.backup.serverName | string | `""` |  |
| postgres.backup.objectStore.existingName | string | `""` |  |
| postgres.backup.objectStore.destinationPath | string | `""` |  |
| postgres.backup.objectStore.endpointURL | string | `""` |  |
| postgres.backup.objectStore.retentionPolicy | string | `"30d"` |  |
| postgres.backup.objectStore.wal.compression | string | `"gzip"` |  |
| postgres.backup.objectStore.wal.maxParallel | int | `1` |  |
| postgres.backup.objectStore.data.compression | string | `"gzip"` |  |
| postgres.backup.objectStore.s3.inheritFromIAMRole | bool | `false` |  |
| postgres.backup.objectStore.s3.accessKeyId.name | string | `""` |  |
| postgres.backup.objectStore.s3.accessKeyId.key | string | `"ACCESS_KEY_ID"` |  |
| postgres.backup.objectStore.s3.secretAccessKey.name | string | `""` |  |
| postgres.backup.objectStore.s3.secretAccessKey.key | string | `"ACCESS_SECRET_KEY"` |  |
| postgres.backup.objectStore.azure.inheritFromAzureAD | bool | `false` |  |
| postgres.backup.objectStore.azure.connectionString.name | string | `""` |  |
| postgres.backup.objectStore.azure.connectionString.key | string | `""` |  |
| postgres.backup.objectStore.azure.storageAccount.name | string | `""` |  |
| postgres.backup.objectStore.azure.storageAccount.key | string | `""` |  |
| postgres.backup.objectStore.azure.storageKey.name | string | `""` |  |
| postgres.backup.objectStore.azure.storageKey.key | string | `""` |  |
| postgres.backup.objectStore.sidecar.resources | object | `{}` |  |
| postgres.backup.volumeSnapshot.className | string | `""` |  |
| postgres.backup.volumeSnapshot.walClassName | string | `""` |  |
| postgres.backup.volumeSnapshot.online | bool | `true` |  |
| postgres.backup.serviceAccount.annotations | object | `{}` |  |
| postgres.backup.networkPolicy.ports[0] | string | `"443"` |  |
| postgres.backup.networkPolicy.fqdns | list | `[]` |  |
| postgres.backup.networkPolicy.cidrs | list | `[]` |  |
| postgres.backup.crossplane.enabled | bool | `false` |  |
| postgres.backup.crossplane.provider | string | `"aws"` |  |
| postgres.backup.crossplane.providerConfigRef | string | `""` |  |
| postgres.backup.crossplane.region | string | `""` |  |
| postgres.backup.crossplane.observeOnly | bool | `false` |  |
| postgres.backup.crossplane.tags | object | `{}` |  |
| postgres.backup.crossplane.aws.bucketName | string | `""` |  |
| postgres.backup.crossplane.aws.accountId | string | `""` |  |
| postgres.backup.crossplane.aws.oidcProvider | string | `""` |  |
| postgres.backup.crossplane.aws.roleName | string | `""` |  |
| postgres.backup.crossplane.aws.lifecycleDays | int | `45` |  |
| postgres.backup.crossplane.azure.storageAccountName | string | `""` |  |
| postgres.backup.crossplane.azure.containerName | string | `""` |  |
| postgres.backup.crossplane.azure.resourceGroup | string | `""` |  |
| postgres.backup.crossplane.azure.replicationType | string | `"LRS"` |  |
| postgres.backup.crossplane.azure.lifecycleDays | int | `45` |  |
| postgres.backup.crossplane.azure.private | bool | `false` |  |
| postgres.backup.crossplane.azure.subscriptionId | string | `""` |  |
| postgres.backup.crossplane.azure.vnetName | string | `""` |  |
| postgres.backup.crossplane.azure.subnetName | string | `"node-subnet"` |  |
| postgres.backup.crossplane.azure.privateDnsZoneRef | string | `""` |  |
| klausGateway.image.registry | string | `"gsoci.azurecr.io"` |  |
| klausGateway.podAnnotations."karpenter.sh/do-not-disrupt" | string | `"true"` |  |
| klausGateway.podDisruptionBudget.enabled | bool | `true` |  |
| klausGateway.podDisruptionBudget.minAvailable | int | `1` |  |
| klausGateway.podDisruptionBudget.unhealthyPodEvictionPolicy | string | `"AlwaysAllow"` |  |
| klausGateway.routing.store | string | `"memory"` |  |
| klausGateway.observability.enabled | string | `"auto"` |  |
| klausGateway.observability.otlpEndpoint | string | `"http://otlp-gateway.kube-system.svc:4317"` |  |
| klausGateway.observability.otlpHeaders.X-Scope-OrgID | string | `"giantswarm"` |  |
| klausGateway.serviceMonitor.enabled | string | `"auto"` |  |
| klausGateway.serviceMonitor.labels."observability.giantswarm.io/tenant" | string | `"giantswarm"` |  |
| klausGateway.slack.enabled | bool | `false` |  |
| klausGateway.slack.mode | string | `"events"` |  |
| klausGateway.slack.secretName | string | `""` |  |
| klausGateway.slack.dmMode | string | `""` |  |
| klausGateway.slack.channelMode | string | `""` |  |
| klausGateway.slack.channelAllowlist | list | `[]` |  |
| klausGateway.slack.botToken | string | `""` |  |
| klausGateway.slack.signingSecret | string | `""` |  |
| klausGateway.slack.appToken | string | `""` |  |
| klausGateway.obo.enabled | bool | `false` |  |
| klausGateway.obo.musterUrl | string | `""` |  |
| klausGateway.obo.callbackBaseUrl | string | `""` |  |
| klausGateway.obo.storePath | string | `""` |  |
| klausGateway.obo.persistence.enabled | bool | `false` |  |
| klausGateway.obo.persistence.size | string | `"64Mi"` |  |
| klausGateway.obo.existingSecret | string | `""` |  |
| klausGateway.obo.stateKey | string | `""` |  |
| klausGateway.obo.storeKey | string | `""` |  |
| klausGateway.obo.connectors.enabled | bool | `false` |  |
| klausGateway.a2a.enabled | bool | `false` |  |
| klausGateway.a2a.defaultAgent | string | `""` |  |
| klausGateway.a2a.url | string | `"grpc://agentgateway.agent-platform.svc.cluster.local:8080"` |  |
| klausGateway.a2a.fallbackIconUrlTemplate | string | `""` |  |
| klausGateway.agentgatewayRoute.enabled | bool | `false` |  |
| klausGateway.agentgatewayRoute.hostname | string | `""` |  |
| agentgateway.fullnameOverride | string | `"agentgateway-controller"` |  |
| agentgateway.image.registry | string | `"gsoci.azurecr.io"` |  |
| agentgateway.controller.image.repository | string | `"giantswarm/agentgateway-upstream/controller"` |  |
| agentgateway.controller.image.tag | string | `"2.1.0"` |  |
| agentgateway.controller.replicaCount | int | `2` |  |
| agentgateway.proxy.image.registry | string | `"gsoci.azurecr.io"` |  |
| agentgateway.proxy.image.repository | string | `"giantswarm/agentgateway-upstream/agentgateway"` |  |
| agentgateway.proxy.image.tag | string | `"2.1.0"` |  |
| agentgateway.podAnnotations."application.giantswarm.io/team" | string | `"bumblebee"` |  |
| agentgateway.podSecurityContext.runAsNonRoot | bool | `true` |  |
| agentgateway.podSecurityContext.seccompProfile.type | string | `"RuntimeDefault"` |  |
| agentgateway.securityContext.allowPrivilegeEscalation | bool | `false` |  |
| agentgateway.securityContext.readOnlyRootFilesystem | bool | `true` |  |
| agentgateway.securityContext.runAsNonRoot | bool | `true` |  |
| agentgateway.securityContext.capabilities.drop[0] | string | `"ALL"` |  |
| agentgateway.securityContext.seccompProfile.type | string | `"RuntimeDefault"` |  |
| agentgateway.resources.requests.cpu | string | `"50m"` |  |
| agentgateway.resources.requests.memory | string | `"128Mi"` |  |
| agentgateway.resources.limits.cpu | string | `"500m"` |  |
| agentgateway.resources.limits.memory | string | `"512Mi"` |  |
| agentgateway.monitoring.enabled | string | `"auto"` |  |
| agentgateway.monitoring.serviceMonitor.enabled | bool | `true` |  |
| agentgateway.monitoring.serviceMonitor.interval | string | `"60s"` |  |
| agentgateway.monitoring.serviceMonitor.extraLabels."observability.giantswarm.io/tenant" | string | `"giantswarm"` |  |
| agentgateway.monitoring.grafanaDashboard.enabled | bool | `true` |  |
| agentgateway.monitoring.grafanaDashboard.labels."app.giantswarm.io/kind" | string | `"dashboard"` |  |
| agentgateway.monitoring.grafanaDashboard.annotations."observability.giantswarm.io/organization" | string | `"Shared Org"` |  |
| agentgateway.monitoring.grafanaDashboard.annotations."observability.giantswarm.io/folder" | string | `"Agent Platform"` |  |
| agentSandbox.podSecurity.enabled | string | `"auto"` |  |
| agentSandbox.podSecurity.namespace | string | `"agent-sandbox-system"` |  |
| agentSandbox.podSecurity.podSecurityContext.runAsNonRoot | bool | `true` |  |
| agentSandbox.podSecurity.podSecurityContext.seccompProfile.type | string | `"RuntimeDefault"` |  |
| agentSandbox.podSecurity.containerSecurityContext.allowPrivilegeEscalation | bool | `false` |  |
| agentSandbox.podSecurity.containerSecurityContext.capabilities.drop[0] | string | `"ALL"` |  |
| agentSandbox.podSecurity.containerSecurityContext.runAsNonRoot | bool | `true` |  |
| agentSandbox.podSecurity.containerSecurityContext.seccompProfile.type | string | `"RuntimeDefault"` |  |
| model-manager.fullnameOverride | string | `"model-manager"` |  |
| model-manager.ollama.endpoint | string | `""` |  |
| model-manager.ollama.agentHost | string | `""` |  |
| model-manager.lemonade.endpoint | string | `""` |  |
| model-manager.lemonade.agentHost | string | `""` |  |
| model-manager.lmstudio.endpoint | string | `""` |  |
| model-manager.lmstudio.agentHost | string | `""` |  |
| model-manager.kagent.namespace | string | `"kagent"` |  |
| model-manager.kagent.apiVersion | string | `"v1alpha3"` |  |
| model-manager.kagent.disableWiring | bool | `false` |  |
| model-manager.mcp.enabled | bool | `true` |  |
| model-manager.oauth.enabled | bool | `true` |  |
| model-manager.oauth.provider | string | `"dex"` |  |
| model-manager.oauth.dex.allowPrivateURLs | bool | `true` |  |
| model-manager.oauth.sso.allowPrivateIPs | bool | `true` |  |
| model-manager.oauth.downstream.enabled | bool | `true` |  |
| model-manager.muster.mcpServer.enabled | bool | `true` |  |
| model-manager.muster.mcpServer.auth.forwardToken | bool | `true` |  |
| model-manager.muster.mcpServer.auth.requiredAudiences[0] | string | `"dex-k8s-authenticator"` |  |
| model-manager.networkPolicy.enabled | bool | `false` |  |
| modelManager.route.enabled | bool | `false` |  |
| modelManager.route.pathPrefix | string | `"/model-manager"` |  |
| modelManager.route.hostname | string | `""` |  |
| modelManager.route.parentRef.name | string | `"giantswarm-default"` |  |
| modelManager.route.parentRef.namespace | string | `"envoy-gateway-system"` |  |
| modelManager.route.jwtAuthentication.enabled | bool | `false` |  |
| modelManager.route.jwtAuthentication.mode | string | `"Strict"` |  |
| modelManager.route.jwtAuthentication.issuer | string | `""` |  |
| modelManager.route.jwtAuthentication.jwks.host | string | `"dex.giantswarm.svc.cluster.local"` |  |
| modelManager.route.jwtAuthentication.jwks.port | int | `5556` |  |
| modelManager.route.jwtAuthentication.jwks.path | string | `"/keys"` |  |
| modelManager.route.jwtAuthentication.jwks.tls.enabled | bool | `false` |  |
| modelManager.route.jwtAuthentication.jwks.tls.caSecretName | string | `""` |  |
| modelManager.kserve.requireApi | bool | `true` |  |
| modelManager.networkPolicy.ingress.additionalPeers | list | `[]` |  |
| modelManager.networkPolicy.huggingFace.fqdns[0].matchName | string | `"huggingface.co"` |  |
| modelManager.networkPolicy.huggingFace.fqdns[1].matchPattern | string | `"*.huggingface.co"` |  |
| modelManager.networkPolicy.huggingFace.fqdns[2].matchPattern | string | `"*.hf.co"` |  |
| modelManager.networkPolicy.huggingFace.fqdns[3].matchPattern | string | `"*.*.hf.co"` |  |
| modelManager.networkPolicy.huggingFace.fqdns[4].matchPattern | string | `"*.*.*.hf.co"` |  |
| modelManager.networkPolicy.huggingFace.cidrs | list | `[]` |  |
| modelManager.networkPolicy.egress.fqdns | list | `[]` |  |
| modelManager.networkPolicy.egress.cidrs | list | `[]` |  |
| modelManager.networkPolicy.registeredBackends | list | `[]` |  |
| vm-manager.fullnameOverride | string | `"vm-manager"` |  |
| vm-manager.persistence.existingClaim | string | `""` |  |
| vm-manager.persistence.create | bool | `false` |  |
| vm-manager.oauth.enabled | bool | `true` |  |
| vm-manager.oauth.provider | string | `"dex"` |  |
| vm-manager.oauth.dex.allowPrivateURLs | bool | `true` |  |
| vm-manager.oauth.sso.allowPrivateIPs | bool | `true` |  |
| vm-manager.muster.mcpServer.enabled | bool | `true` |  |
| vm-manager.muster.mcpServer.auth.forwardToken | bool | `true` |  |
| vm-manager.muster.mcpServer.auth.requiredAudiences | list | `[]` |  |
| vm-manager.networkPolicy.enabled | bool | `false` |  |
| vm-manager.serviceMonitor.enabled | string | `"auto"` |  |
| vm-manager.serviceMonitor.interval | string | `"60s"` |  |
| vm-manager.serviceMonitor.labels."observability.giantswarm.io/tenant" | string | `"giantswarm"` |  |
| vmManager.podDisruptionBudget.enabled | bool | `true` |  |
| vmManager.podDisruptionBudget.minAvailable | int | `1` |  |
| vmManager.podDisruptionBudget.maxUnavailable | string | `nil` |  |
| vmManager.podDisruptionBudget.unhealthyPodEvictionPolicy | string | `"AlwaysAllow"` |  |
| vmManager.networkPolicy.ingress.additionalPeers | list | `[]` |  |
| vmManager.networkPolicy.guestEgress.cidrs[0] | string | `"0.0.0.0/0"` |  |
| vmManager.networkPolicy.guestEgress.except | list | `[]` |  |
| agent-manager.fullnameOverride | string | `"agent-manager"` |  |
| agent-manager.kagent.namespace | string | `"kagent"` |  |
| agent-manager.kagent.apiVersion | string | `"v1alpha3"` |  |
| agent-manager.agentChart.ociUrl | string | `"oci://gsoci.azurecr.io/charts/giantswarm/agent"` |  |
| agent-manager.agentChart.semver | string | `"1.x"` |  |
| agent-manager.skills.repositories[0] | string | `"https://github.com/giantswarm/agent-skills"` |  |
| agent-manager.mcp.enabled | bool | `true` |  |
| agent-manager.oauth.enabled | bool | `true` |  |
| agent-manager.oauth.provider | string | `"dex"` |  |
| agent-manager.oauth.dex.allowPrivateURLs | bool | `true` |  |
| agent-manager.oauth.sso.allowPrivateIPs | bool | `true` |  |
| agent-manager.oauth.downstream.enabled | bool | `true` |  |
| agent-manager.muster.mcpServer.enabled | bool | `true` |  |
| agent-manager.muster.mcpServer.auth.forwardToken | bool | `true` |  |
| agent-manager.muster.mcpServer.auth.requiredAudiences[0] | string | `"dex-k8s-authenticator"` |  |
| agent-manager.networkPolicy.enabled | bool | `false` |  |
| agentManager.route.enabled | bool | `false` |  |
| agentManager.route.pathPrefix | string | `"/agent-manager"` |  |
| agentManager.route.hostname | string | `""` |  |
| agentManager.route.parentRef.name | string | `"giantswarm-default"` |  |
| agentManager.route.parentRef.namespace | string | `"envoy-gateway-system"` |  |
| agentManager.route.jwtAuthentication.enabled | bool | `false` |  |
| agentManager.route.jwtAuthentication.mode | string | `"Strict"` |  |
| agentManager.route.jwtAuthentication.issuer | string | `""` |  |
| agentManager.route.jwtAuthentication.jwks.host | string | `"dex.giantswarm.svc.cluster.local"` |  |
| agentManager.route.jwtAuthentication.jwks.port | int | `5556` |  |
| agentManager.route.jwtAuthentication.jwks.path | string | `"/keys"` |  |
| agentManager.route.jwtAuthentication.jwks.tls.enabled | bool | `false` |  |
| agentManager.route.jwtAuthentication.jwks.tls.caSecretName | string | `""` |  |
| agentManager.podDisruptionBudget.enabled | bool | `true` |  |
| agentManager.podDisruptionBudget.minAvailable | int | `1` |  |
| agentManager.podDisruptionBudget.maxUnavailable | string | `nil` |  |
| agentManager.podDisruptionBudget.unhealthyPodEvictionPolicy | string | `"AlwaysAllow"` |  |
| agentManager.flux.requireApi | bool | `false` |  |
| agentManager.networkPolicy.ingress.additionalPeers | list | `[]` |  |
| agentManager.networkPolicy.egress.fqdns[0].matchPattern | string | `"*.blob.core.windows.net"` |  |
| agentManager.networkPolicy.egress.fqdns[1].matchName | string | `"api.github.com"` |  |
| agentManager.networkPolicy.egress.cidrs | list | `[]` |  |
| agentManager.migration.enabled | bool | `true` |  |
| agentManager.migration.image.registry | string | `"gsoci.azurecr.io"` |  |
| agentManager.migration.image.repository | string | `"giantswarm/agent-manager"` |  |
| agentManager.migration.image.tag | string | `"1.1.7"` |  |
| agentManager.migration.dryRun | bool | `false` |  |
| agentManager.migration.githubToken.secretName | string | `"kagent-skills-token"` |  |
| agentManager.migration.githubToken.key | string | `"token"` |  |
| agentManager.migration.gitopsNamespaces | list | `[]` |  |
| cluster-manager.fullnameOverride | string | `"cluster-manager"` |  |
| cluster-manager.installation.name | string | `""` |  |
| cluster-manager.mcp.enabled | bool | `true` |  |
| cluster-manager.oauth.enabled | bool | `true` |  |
| cluster-manager.oauth.provider | string | `"dex"` |  |
| cluster-manager.oauth.dex.allowPrivateURLs | bool | `true` |  |
| cluster-manager.oauth.sso.allowPrivateIPs | bool | `true` |  |
| cluster-manager.oauth.downstream.enabled | bool | `true` |  |
| cluster-manager.muster.mcpServer.enabled | bool | `true` |  |
| cluster-manager.muster.mcpServer.auth.forwardToken | bool | `true` |  |
| cluster-manager.muster.mcpServer.auth.requiredAudiences[0] | string | `"dex-k8s-authenticator"` |  |
| clusterManager.flux.requireApi | bool | `false` |  |
| clusterManager.prewarmPriorityClass.enabled | bool | `true` |  |
| clusterManager.prewarmPriorityClass.name | string | `"agent-platform-prewarm-placeholder"` |  |
| clusterManager.prewarmPriorityClass.value | int | `-1000` |  |
| clusterManager.networkPolicy.ingress.additionalPeers | list | `[]` |  |
| clusterManager.networkPolicy.workloadClusters.fqdns | list | `[]` |  |
| clusterManager.networkPolicy.workloadClusters.cidrs | list | `[]` |  |
| clusterManager.networkPolicy.workloadClusters.ports[0] | int | `443` |  |
| clusterManager.networkPolicy.workloadClusters.ports[1] | int | `6443` |  |
| clusterManager.networkPolicy.egress.fqdns | list | `[]` |  |
| clusterManager.networkPolicy.egress.cidrs | list | `[]` |  |
| backstage.hostname | string | `""` |  |
| backstage.parentRefs | list | `[]` |  |
| backstage.installationName | string | `"agent-platform"` |  |
| backstage.extraScopes[0] | string | `"federated:id"` |  |
| backstage.extraScopes[1] | string | `"audience:server:client_id:dex-k8s-authenticator"` |  |
| backstage.startUrlSearchParams | object | `{}` |  |
| backstage.enabledExtensions | list | `[]` |  |
| backstage.disabledExtensions[0] | string | `"page:gs/clusters"` |  |
| backstage.disabledExtensions[1] | string | `"nav-item:gs/clusters"` |  |
| backstage.disabledExtensions[2] | string | `"page:gs/deployments"` |  |
| backstage.disabledExtensions[3] | string | `"nav-item:gs/deployments"` |  |
| backstage.disabledExtensions[4] | string | `"page:gs/installations"` |  |
| backstage.disabledExtensions[5] | string | `"nav-item:gs/installations"` |  |
| backstage.disabledExtensions[6] | string | `"page:flux"` |  |
| backstage.disabledExtensions[7] | string | `"nav-item:flux"` |  |
| backstage.disabledExtensions[8] | string | `"page:ai-chat"` |  |
| backstage.disabledExtensions[9] | string | `"api:ai-chat/service"` |  |
| backstage.disabledExtensions[10] | string | `"api:ai-chat/drawer"` |  |
| backstage.disabledExtensions[11] | string | `"app-root-element:ai-chat/drawer"` |  |
| backstage.skillsRepositories[0] | string | `"https://github.com/giantswarm/agent-skills"` |  |
| backstage.catalogs.version | string | `"v0.6.0"` |  |
| backstage.configReload.enabled | bool | `true` |  |
| backstage.configReload.image.registry | string | `"gsoci.azurecr.io"` |  |
| backstage.configReload.image.name | string | `"giantswarm/kubectl"` |  |
| backstage.configReload.image.version | string | `"v1.37.0"` |  |
| backstage.ingress.enabled | bool | `false` |  |
| backstage.resources.verticalPodAutoscaler.enabled | bool | `false` |  |
| backstage.backstage.args[0] | string | `"--config"` |  |
| backstage.backstage.args[1] | string | `"app-config.yaml"` |  |
| backstage.backstage.args[2] | string | `"--config"` |  |
| backstage.backstage.args[3] | string | `"app-config.production.yaml"` |  |
| backstage.backstage.extraAppConfig[0].filename | string | `"app-config.agent-platform.yaml"` |  |
| backstage.backstage.extraAppConfig[0].configMapRef | string | `"agent-platform-backstage-app-config"` |  |
| backstage.backstage.extraEnvVars[0].name | string | `"AUTH_SESSION_SECRET"` |  |
| backstage.backstage.extraEnvVars[0].valueFrom.secretKeyRef.name | string | `"{{ .Values.global.identity.existingSecret }}"` |  |
| backstage.backstage.extraEnvVars[0].valueFrom.secretKeyRef.key | string | `"backstage-session-secret"` |  |
| backstage.backstage.extraEnvVars[1].name | string | `"AGENT_PLATFORM_OIDC_CLIENT_SECRET"` |  |
| backstage.backstage.extraEnvVars[1].valueFrom.secretKeyRef.name | string | `"{{ .Values.global.identity.existingSecret }}"` |  |
| backstage.backstage.extraEnvVars[1].valueFrom.secretKeyRef.key | string | `"dex-client-secret"` |  |
| backstage.backstage.extraEnvVars[2].name | string | `"NODE_EXTRA_CA_CERTS"` |  |
| backstage.backstage.extraEnvVars[2].value | string | `"{{ with (dig \"identity\" \"ca\" \"secretName\" \"\" .Values.global) }}/etc/agent-platform/idp-ca/{{ dig \"identity\" \"ca\" \"key\" \"ca.crt\" $.Values.global }}{{ end }}"` |  |
| backstage.backstage.extraVolumes[0].name | string | `"idp-ca"` |  |
| backstage.backstage.extraVolumes[0].secret.secretName | string | `"{{ dig \"identity\" \"ca\" \"secretName\" \"\" .Values.global | default \"agent-platform-idp-ca\" }}"` |  |
| backstage.backstage.extraVolumes[0].secret.optional | bool | `true` |  |
| backstage.backstage.extraVolumeMounts[0].name | string | `"idp-ca"` |  |
| backstage.backstage.extraVolumeMounts[0].mountPath | string | `"/etc/agent-platform/idp-ca"` |  |
| backstage.backstage.extraVolumeMounts[0].readOnly | bool | `true` |  |
| mcp-kubernetes.fullnameOverride | string | `"mcp-kubernetes"` |  |
| mcp-kubernetes.mcpKubernetes.instrumentation.serviceMonitor.enabled | string | `"auto"` |  |
| mcp-kubernetes.mcpKubernetes.instrumentation.serviceMonitor.labels."observability.giantswarm.io/tenant" | string | `"giantswarm"` |  |
| mcp-kubernetes.mcpKubernetes.oauth.enabled | bool | `true` |  |
| mcp-kubernetes.mcpKubernetes.oauth.provider | string | `"dex"` |  |
| mcp-kubernetes.mcpKubernetes.oauth.allowPrivateURLs | bool | `true` |  |
| mcp-kubernetes.mcpKubernetes.oauth.sso.allowPrivateIPs | bool | `true` |  |
| mcp-kubernetes.mcpKubernetes.oauth.enableDownstreamOAuth | bool | `true` |  |
| mcp-kubernetes.grafanaDashboards.enabled | string | `"auto"` |  |
| mcp-kubernetes.grafanaDashboards.folder | string | `"Agent Platform"` |  |
| mcp-kubernetes.grafanaDashboards.giantswarm.enabled | bool | `true` |  |
| mcp-kubernetes.grafanaDashboards.giantswarm.organization | string | `"Shared Org"` |  |
| mcp-kubernetes.kubernetesAudience | string | `"dex-k8s-authenticator"` |  |
| cloudnative-pg | object | `{}` |  |
| kagent-crds.kmcp.enabled | bool | `false` |  |
| kagent-crds.substrate.enabled | bool | `false` |  |
| substrate.createNamespace | bool | `false` |  |
| substrate.image.registry | string | `"gsoci.azurecr.io/giantswarm/substrate"` |  |
| substrate.postgres.enabled | string | `"auto"` |  |
| substrate.postgres.connectionString | string | `""` |  |
| substrate.postgres.schema | string | `"public"` |  |
| substrate.rustfs.enabled | bool | `false` |  |
| substrate.otel.endpoint | string | `"http://otlp-gateway.kube-system.svc:4317"` |  |
| substrate.images.postgres | string | `"gsoci.azurecr.io/giantswarm/postgres:18.4-alpine@sha256:9a8afca54e7861fd90fab5fdf4c42477a6b1cb7d293595148e674e0a3181de15"` |  |
| substrate.images.rustfs | string | `"gsoci.azurecr.io/giantswarm/rustfs:1.0.0-beta.3@sha256:378642b05b7dcb4849fb77ebe6aca4ced1c3f66e7e504247df95a5c9018d3358"` |  |
| substrate.images.awsCli | string | `"amazon/aws-cli:2.17.0@sha256:643507c10ada7964ca6157b3d799f030b90577643da9955d319a77399ed80d73"` |  |
| substrate.images.agentgateway | string | `"gsoci.azurecr.io/giantswarm/agentgateway-upstream/agentgateway:2.0.0"` |  |
| substrate.atelet.storageBackend | string | `"s3"` |  |
| substrate.atelet.nodeSelector | object | `{}` |  |
| substrate.atelet.tolerations | list | `[]` |  |
| substrate.atelet.affinity | object | `{}` |  |
| substrate.atelet.extraEnv | list | `[]` |  |
| substrate.atelet.imageCache.pinnedImages | list | `[]` |  |
| substrate-crds | object | `{}` |  |
| hooks.kubectlImage.registry | string | `"gsoci.azurecr.io"` |  |
| hooks.kubectlImage.repository | string | `"giantswarm/alpine-k8s"` |  |
| hooks.kubectlImage.tag | string | `"1.37.0"` |  |
| hooks.opensslImage.registry | string | `"gsoci.azurecr.io"` |  |
| hooks.opensslImage.repository | string | `"giantswarm/alpine-openssl"` |  |
| hooks.opensslImage.tag | string | `"3.5.8"` |  |
| kserve-llmisvc-crd | object | `{}` |  |
| kserve-llmisvc-resources.kserve.createSharedResources | bool | `true` |  |
| kserve-llmisvc-resources.kserve.controller.deploymentMode | string | `"Standard"` |  |
| kserve-llmisvc-resources.kserve.controller.gateway.disableIngressCreation | bool | `true` |  |
| kserve-llmisvc-resources.kserve.llmisvc.createGIECRDs | bool | `true` |  |
| kserve-llmisvc-resources.kserve.llmisvc.controller.metricsSecure | bool | `false` |  |
| kserve-llmisvc-resources.kserve.llmisvc.controller.serviceMonitor.enabled | string | `"auto"` |  |
| kserve-llmisvc-resources.kserve.llmisvc.controller.serviceMonitor.interval | string | `"60s"` |  |
| kserve-llmisvc-resources.kserve.llmisvc.controller.serviceMonitor.labels."observability.giantswarm.io/tenant" | string | `"giantswarm"` |  |
| kserve-runtime-configs.kserve.llmisvcConfigs.enabled | bool | `true` |  |
| kserve-runtime-configs.kserve.llmisvcConfigs.imageRegistry | string | `"gsoci.azurecr.io/giantswarm/llm-d-fast/"` |  |
| kserve-runtime-configs.kserve.servingruntime.enabled | bool | `false` |  |
| gpu-operator.driver.enabled | bool | `false` |  |
| gpu-operator.toolkit.enabled | bool | `false` |  |
| gpu-operator.dcgmExporter.serviceMonitor.additionalLabels."observability.giantswarm.io/tenant" | string | `"giantswarm"` |  |
| modelServing.kserve.requireApi | bool | `true` | api-versions serving.kserve.io/v1alpha2; false skips it. |
| modelServing.namespace.name | string | `"model-serving"` |  |
| modelServing.namespace.create | bool | `true` |  |
| modelServing.namespace.keep | bool | `true` |  |
| modelServing.namespace.labels | object | `{}` |  |
| modelServing.serving.gpuResourceName | string | `"nvidia.com/gpu"` |  |
| modelServing.serving.runtimeClassName | string | `""` |  |
| modelServing.serving.nodeSelector | object | `{}` |  |
| modelServing.gpuPool.taint.key | string | `"nvidia.com/gpu"` |  |
| modelServing.gpuPool.taint.value | string | `""` |  |
| modelServing.gpuPool.taint.effect | string | `"NoSchedule"` |  |
| modelServing.gpuPool.nodeSelector | object | `{}` |  |
| modelServing.prepull.enabled | bool | `true` |  |
| modelServing.prepull.images[0] | string | `"gsoci.azurecr.io/giantswarm/llm-d-fast/llm-d-cuda:v0.8.0"` |  |
| modelServing.prepull.nodeSelector | object | `{}` |  |
| modelServing.prepull.tolerations[0].operator | string | `"Exists"` |  |
| modelServing.prepull.pauseImage.registry | string | `"gsoci.azurecr.io"` |  |
| modelServing.prepull.pauseImage.repository | string | `"giantswarm/pause"` |  |
| modelServing.prepull.pauseImage.tag | string | `"3.10.1"` |  |
| modelServing.prepull.resources.requests.cpu | string | `"5m"` |  |
| modelServing.prepull.resources.requests.memory | string | `"8Mi"` |  |
| modelServing.prepull.resources.limits.memory | string | `"32Mi"` |  |
| modelServing.prepull.modelPresets | list | `[]` |  |
| modelServing.presets | list | `[]` |  |
| modelServing.shippedPresets.enabled | bool | `true` |  |
| modelServing.shippedPresets.exclude | list | `[]` |  |
| modelServing.modelImages.registry | string | `""` |  |
| modelServing.cache.enabled | bool | `true` |  |
| modelServing.cache.pvc.name | string | `"hf-cache"` |  |
| modelServing.cache.pvc.existingClaim | string | `""` |  |
| modelServing.cache.pvc.size | string | `"100Gi"` |  |
| modelServing.cache.pvc.storageClassName | string | `""` |  |
| modelServing.cache.pvc.volumeName | string | `""` |  |
| modelServing.cache.pvc.accessModes[0] | string | `"ReadWriteOnce"` |  |
| modelServing.cache.storageClass.create | bool | `true` |  |
| modelServing.cache.storageClass.name | string | `""` |  |
| modelServing.cache.storageClass.provisioner | string | `"ebs.csi.aws.com"` |  |
| modelServing.cache.storageClass.parameters.type | string | `"gp3"` |  |
| modelServing.cache.storageClass.parameters.iops | string | `"3000"` |  |
| modelServing.cache.storageClass.parameters.throughput | string | `"500"` |  |
| modelServing.cache.fsGroup | int | `1000` |  |
| modelServing.policies.enabled | string | `"auto"` |  |
| modelServing.policies.storageInitializerMemoryLimit | string | `"8Gi"` |  |
| modelServing.policies.progressDeadlineSeconds | int | `3600` |  |
| modelServing.policies.env[0].name | string | `"HF_HUB_DISABLE_XET"` |  |
| modelServing.policies.env[0].value | string | `"1"` |  |
| modelServing.imageVerification.enabled | bool | `true` |  |
| modelServing.imageVerification.images[0] | string | `"gsoci.azurecr.io/giantswarm/*"` |  |
| modelServing.imageVerification.attestors[0].keyless.issuer | string | `"https://oidc.circleci.com"` |  |
| modelServing.imageVerification.attestors[0].keyless.subjectRegExp | string | `"^https://circleci\\.com/api/v2/projects/[a-f0-9-]+/pipeline-definitions/[a-f0-9-]+$"` |  |
| modelServing.imageVerification.attestors[0].keyless.rekor.url | string | `"https://rekor.sigstore.dev"` |  |
| modelServing.imageVerification.type | string | `"SigstoreBundle"` |  |
| modelServing.imageVerification.mutateDigest | bool | `true` |  |
| modelServing.imageVerification.required | bool | `true` |  |
| modelServing.imageVerification.failureAction | string | `"Enforce"` |  |
| modelServing.imageVerification.kyvernoEgress.enabled | bool | `true` |  |
| modelServing.imageVerification.kyvernoEgress.namespace | string | `"kyverno"` |  |
| modelServing.imageVerification.kyvernoEgress.podSelector | object | `{}` |  |
| modelServing.imageVerification.kyvernoEgress.hosts[0].matchName | string | `"gsoci.azurecr.io"` |  |
| modelServing.imageVerification.kyvernoEgress.hosts[1].matchPattern | string | `"*.blob.core.windows.net"` |  |
| modelServing.imageVerification.kyvernoEgress.hosts[2].matchName | string | `"tuf-repo-cdn.sigstore.dev"` |  |
| modelServing.imageVerification.kyvernoEgress.hosts[3].matchName | string | `"rekor.sigstore.dev"` |  |
| modelServing.networkPolicy.llmisvcWorkload.port | int | `8000` |  |
| modelServing.networkPolicy.additionalIngressNamespaces | list | `[]` |  |
| modelServing.networkPolicy.huggingFace.fqdns[0].matchName | string | `"huggingface.co"` |  |
| modelServing.networkPolicy.huggingFace.fqdns[1].matchPattern | string | `"*.huggingface.co"` |  |
| modelServing.networkPolicy.huggingFace.fqdns[2].matchPattern | string | `"*.hf.co"` |  |
| modelServing.networkPolicy.huggingFace.fqdns[3].matchPattern | string | `"*.*.hf.co"` |  |
| modelServing.networkPolicy.huggingFace.fqdns[4].matchPattern | string | `"*.*.*.hf.co"` |  |
| modelServing.networkPolicy.huggingFace.cidrs | list | `[]` |  |
| modelServing.modelsGateway.enabled | bool | `false` |  |
| modelServing.modelsGateway.name | string | `"models"` |  |
| modelServing.modelsGateway.hostPrefix | string | `"models"` |  |
| modelServing.modelsGateway.gatewayClassName | string | `""` |  |
| modelServing.modelsGateway.tls.secretName | string | `""` |  |
| modelServing.modelsGateway.tls.issuerRef.name | string | `""` |  |
| modelServing.modelsGateway.tls.issuerRef.kind | string | `"ClusterIssuer"` |  |
| modelServing.modelsGateway.tls.issuerRef.group | string | `"cert-manager.io"` |  |
| modelServing.modelsGateway.externalDns.enabled | bool | `true` |  |
| modelServing.modelsGateway.jwtAuthentication.mode | string | `"Strict"` |  |
| modelServing.modelsGateway.jwtAuthentication.issuer | string | `""` |  |
| modelServing.modelsGateway.jwtAuthentication.audiences[0] | string | `"dex-k8s-authenticator"` |  |
| modelServing.modelsGateway.jwtAuthentication.jwks.host | string | `""` |  |
| modelServing.modelsGateway.jwtAuthentication.jwks.port | int | `443` |  |
| modelServing.modelsGateway.jwtAuthentication.jwks.path | string | `"/keys"` |  |
| modelServing.modelsGateway.jwtAuthentication.jwks.tls.enabled | bool | `false` |  |
| modelServing.modelsGateway.jwtAuthentication.jwks.tls.caSecretName | string | `""` |  |
