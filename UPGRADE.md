# Upgrading agent-platform

Operator action required between releases. CHANGELOG.md captures the diff; UPGRADE.md captures what an operator has to *do*.

## \<current\> → \<next\> (the portal's app-config carries no `agentPlatform.modelManager`)

giantswarm/agent-platform#318: the connectivity chart no longer renders `agentPlatform.modelManager.installations.<installation>.apiBaseUrl` into the Backstage app-config. Since giantswarm/backstage#2294 (Backstage 2.19.0 and later) the Models pages call model-manager's `x_model-manager_*` tools through muster as the signed-in person, and the portal backend has no model-manager client that would read the key. The `muster.installations` entry and model-manager's `MCPServer` are unchanged; the model-manager route (`modelManager.route`) stays for REST clients.

### Operator action

- **None** for an installation on Backstage 2.19.0 or later (the chart's range admits `>=1.0.0 <3.0.0`; the Models pages already use muster). The app-config changes, so the config-reload hook rolls the portal once.
- **An installation that added a Backstage peer to `modelManager.networkPolicy.ingress.additionalPeers`** for the portal's REST path can drop it: the portal no longer calls model-manager directly.
- **Recognising it worked**: `kubectl -n <release namespace> get configmap agent-platform-backstage-app-config -o yaml` shows no `modelManager:` under `agentPlatform`, and the Models pages list the installation's models.

## \<current\> → \<next\> (the dev channel's filter reads gitsemver 3's tag shape)

architect-orb 10.10.0 (2026-09-23) tags dev builds `X.Y.Z-r<branch-hash>t<YYYYMMDDHHMMSS>h<sha7>` (gitsemver 3) instead of `X.Y.Z-dev.<branch>.<YYYY-MM-DD>.<HH-MM-SS>.h<sha7>`; this repository, the kagent line and the Substrate line build with it. A `semverFilter` written for the superseded shape matches no build made since and keeps its channel on the last one.

### Operator action

- **None** for an installation on the defaults: no default carries a filter, and the stable ranges carry no `-0`, so they refuse a dev build of either shape (at one `X.Y.Z` a current tag sorts above the `-gs.N` and `-dev.` tags and, for a hash starting `0`–`b`, below `-rc.N`).
- **An installation on a dev channel** (`components.<name>.semverFilter` or `gitops.self.semverFilter` set): replace the filter with `^.*-r<branch-hash>t[0-9]{14}h[0-9a-f]{7}$`, where `gitsemver branch-hash <branch>` prints the hash (`588f3d76` for the kagent and Substrate lines' branch `giantswarm`), and keep the range's floor below the branch's base (`>=0.0.0-0` for a line whose re-pin restarted its count). The channel moves to the branch's newest current build on the next reconcile, even where a superseded build carries a higher base.
- **Recognising it worked**: `kubectl -n <release namespace> get ocirepository <name> -o jsonpath='{.status.artifact.revision}'` names a `-r…t…h…` tag.

## \<current\> → \<next\> (the `klausGateway` keys the Slack-only gateway ignores are no longer forwarded)

giantswarm/klaus-gateway#319: klaus-gateway 2.0.0 serves Slack only, `components.klaus-gateway.versionRange` is `>=2.0.0 <3.0.0`, and this chart stops forwarding the six keys that served the removed paths — `klausGateway.cli`, `klausGateway.lifecycle`, `klausGateway.upstream`, `klausGateway.agentgateway`, `klausGateway.routing.defaultTTL` and `klausGateway.a2a.saToken`. The klaus-gateway `HTTPRoute` the connectivity chart renders (`klausGateway.agentgatewayRoute.enabled`) drops `/v1`, `/web` and `/cli/v1`, which answer 404 on 2.x, and carries `/channels/slack` alone; with `klausGateway.slack.enabled` off it now renders nothing at all, the route having nothing left to publish. The OBO route (`/auth/slack/`, `/connectors/complete`) is untouched.

### Operator action

- **None** for an installation on the defaults. The klaus-gateway release loses six values it did nothing with; the pod does not roll on them alone.
- **An installation whose own values still set one of the six keys** (the `agent-platform` patch in giantswarm-configs): the key keeps travelling to the release and is still accepted as a no-op by the 2.x chart. Drop it from your values before klaus-gateway's next major, which deletes the keys from its schema — a values file that still sets one then fails the release.
- **An installation on the BOM** (`examples/customer-bom.yaml`): the pin moves from `1.20.0` to `2.0.0`, the Slack-only release. Flux upgrades the klaus-gateway release once; a Slack turn in flight ends with the pod, so land it in a quiet window.
- **An installation with its own pin below `2.0.0`** (`components.klaus-gateway.versionRange` set in your values, which replaces the chart's range — the render accepts it, nothing checks it): **move the pin to `2.0.0`**, and act before you take this release. A 1.x chart reads the six keys, and with none forwarded it falls back to its own defaults — `lifecycle.driver: operator` with no `operatorMCPURL`, where this chart forwarded `static` — and the pod does not start. Moving the pin is the fix; `klausGateway.lifecycle.driver: static` in your own values holds a 1.x gateway up in the meantime. The other five defaults match what this chart forwarded, so they change nothing.
- **Recognising it worked**: `kubectl -n <release namespace> get helmrelease klaus-gateway -o jsonpath='{.spec.values.cli}{.spec.values.lifecycle}{.spec.values.upstream}{.spec.values.agentgateway}{.spec.values.routing.defaultTTL}{.spec.values.a2a.saToken}'` prints nothing, and with the route on, `kubectl -n <release namespace> get httproute klausgateway -o jsonpath='{.spec.rules[*].matches[*].path.value}'` prints `/channels/slack`.

## \<current\> → \<next\> (the kagent controller's memory limit is `1536Mi`; the VPA's cap is `1280Mi`)

The meta chart sets `kagent.controller.resources`: the kagent chart's defaults, with the memory limit raised from `512Mi` to `1536Mi`. `kagent.controller.vpa.maxAllowed.memory` moves from `480Mi` to `1280Mi` in both charts, a step under the new limit.

### Operator action

- **None** for an installation on the defaults. The change to the Deployment's pod template rolls the controller pod once. A turn in flight on the controller is lost, so land it in a quiet window.
- **An installation that sets `kagent.controller.resources` itself** keeps its own values. If its memory limit is `1280Mi` or less, also set `kagent.controller.vpa.maxAllowed.memory` under that limit.
- **Recognising it worked**: `kubectl -n kagent get deploy kagent-controller -o jsonpath='{.spec.template.spec.containers[0].resources.limits.memory}'` reads `1536Mi`, and `kubectl -n kagent get vpa kagent-controller -o jsonpath='{.spec.resourcePolicy.containerPolicies[0].maxAllowed.memory}'` reads `1280Mi`.

## \<current\> → \<next\> (the kserve components on `0.5.x`, the llm-d controller's ServiceMonitor)

giantswarm/giantswarm#36711: `components.kserve-llmisvc-crd`, `components.kserve-llmisvc-resources` and `components.kserve-runtime-configs` move from `0.4.x` to `0.5.x`. The meta chart forwards `kserve.llmisvc.controller.metricsSecure` and `.serviceMonitor` to the controller, and the `0.4.x` schema rejects both keys.

### Operator action

- **None** for an installation on the defaults, or with the kserve components off.
- **A BOM pin** (`components.kserve-*.versionRange` at a `0.4.x` release): pin `0.5.0` for all three. A `0.4.x` pin makes the `kserve-llmisvc-resources` release fail on the new keys.
- **Recognising it worked**: `kubectl -n <release namespace> get servicemonitor llmisvc-controller-manager` exists when the cluster serves `monitoring.coreos.com/v1`, and `up{job="llmisvc-controller-manager-service"}` is `1` in Mimir for the `giantswarm` tenant.

## \<current\> → \<next\> (the three upstream lines at their decoupled releases: kagent `1.0.0`, Substrate `1.0.0`, agentgateway `2.0.0`)

giantswarm/agent-platform#608: the kagent line, the Substrate line and the agentgateway line release stable semver of their own, decoupled from the upstream versions their `FORK.md`s record, and publish nothing more under the former `vX.Y.Z-gs.N` scheme. `components.kagent*.versionRange` and `components.substrate*.versionRange` are `>=1.0.0 <1.1.0`; the agentgateway line's `2.0.0` is named in full under its nested names — `agentgateway.controller.image` `giantswarm/agentgateway-upstream/controller:2.0.0` and `agentgateway.proxy.image` `giantswarm/agentgateway-upstream/agentgateway:2.0.0` in both charts, `substrate.images.agentgateway` the same data plane for Substrate's egress gateway — and `components.agentgateway.versionRange` is `>=2.2.2 <3.0.0`, the packaging release that renders a bare image tag as written. The range shape follows the scheme: a patch of a line is carried patches or a rebuild on the same upstream pin and never changes a runtime contract, a re-pin onto another upstream release is at least a minor, so a range's ceiling is the next minor with no `-0` — `agent-platform.substrate.validateRange` admits an exact version or `>=X.Y.Z <X.(Y+1).0` and refuses a `-0` bound and the former `>=X.Y.Z-gs.N <X.Y.(Z+1)-0`, and `agent-platform.substrate.workerPoolSpreadFloor` is `1.0.0`.

### Operator action

- **None** for an installation on the defaults. The four `OCIRepository` objects resolve `1.0.0`: the kagent controller and UI, the Substrate control plane and the atelet DaemonSet restart onto that release's images, the `kagent-default` WorkerPool rolls its workers onto `ateom-gvisor:1.0.0` (running agent turns end with their actor, as on every WorkerPool change) and every golden snapshot is retaken on it; the agentgateway controller, every data plane and Substrate's egress gateway move to the line's `2.0.0`. Expect a quiet window of a few minutes.
- **A BOM pin** (`components.kagent*.versionRange` or `components.substrate*.versionRange` at a `-gs.N` release, `components.agentgateway.versionRange` below `2.2.2`): pin `1.0.0` for the four and `2.2.2` for agentgateway in the same change (`examples/customer-bom.yaml`); a Substrate range of the former shape fails the render naming the shape it takes.
- **`agentgateway.controller.image` or `agentgateway.proxy.image` set in your values** (a mirror): they stand; name the nested paths `giantswarm/agentgateway-upstream/{controller,agentgateway}` and the bare tag `2.0.0` — the flattened `giantswarm/agentgateway-controller` and `giantswarm/agentgateway` carry the retagger's copies of upstream's releases only, never the line's.
- **The dev channel** (`components.<name>.semverFilter` selecting a line's `-dev.giantswarm.` builds next to a prerelease-admitting range such as `>=1.0.0-0 <1.1.0-0`): unchanged. The stable defaults carry no `-0`: Flux's Masterminds semver skips every prerelease while no bound of a range carries one and evaluates them all once one does, so a `-0` added to a default ceiling would put every installation on the line's newest dev build.
- **Recognising it worked**: `kubectl -n <release namespace> get ocirepository kagent kagent-crds substrate substrate-crds -o custom-columns='NAME:.metadata.name,REVISION:.status.artifact.revision'` shows `1.0.0@sha256:…` for each; `kubectl -n <release namespace> get deploy agentgateway-controller -o jsonpath='{.spec.template.spec.containers[0].image}'` prints `gsoci.azurecr.io/giantswarm/agentgateway-upstream/controller:2.0.0`; `kubectl -n <kagent namespace> get workerpool kagent-default -o jsonpath='{.spec.workerImage}'` prints `gsoci.azurecr.io/giantswarm/substrate/ateom-gvisor:1.0.0`.

## \<current\> → \<next\> (the kagent line's and the Substrate line's charts from gsoci; the lines move to `v0.11.0-gs.22` and `v0.0.30-gs.5`)

giantswarm/agent-platform#580: `components.kagent.repository` and `components.kagent-crds.repository` are `oci://gsoci.azurecr.io/giantswarm/kagent/helm`, `components.substrate.repository` and `components.substrate-crds.repository` are `oci://gsoci.azurecr.io/giantswarm/substrate/helm` — the two lines publish their charts and images to the org's registry from CircleCI, signed with the Giant Swarm identity, releases and dev builds alike — and the floors are each line's first release published there: `components.kagent*.versionRange` is `>=0.11.0-gs.22 <0.11.1-0` (the patches of gs.21), `components.substrate*.versionRange` is `>=0.0.30-gs.5 <0.0.31-0` (the patches of gs.4). Nothing of kagent or Substrate is pulled from ghcr.io any more.

### Operator action

- **None** for an installation on the defaults. Flux re-resolves the four `OCIRepository` objects against gsoci and the kagent and Substrate releases upgrade once to the newest release each range admits there — the first release each line published natively: the kagent controller and UI, the Substrate control plane and the atelet DaemonSet restart onto that release's images, the `kagent-default` WorkerPool rolls its workers onto `ateom-gvisor:0.0.30-gs.5`, the new floor (running agent turns end with their actor, as on every WorkerPool change). A cluster that reaches no public registry no longer mirrors anything of the two lines from ghcr.io.
- **`components.kagent*.repository` or `components.substrate*.repository` set in your values** (a mirror of the two chart paths): they stand; point the mirror's source at `gsoci.azurecr.io/giantswarm/kagent/helm` and `gsoci.azurecr.io/giantswarm/substrate/helm` — the paths under the registry are the same, and the ghcr.io publication ends with the lines' move.
- **A BOM pin** (`components.kagent*.versionRange: 0.11.0-gs.21` or older, `components.substrate*.versionRange: 0.0.30-gs.4` or older): those releases exist on ghcr.io only; pin `0.11.0-gs.22` and `0.0.30-gs.5` together with the repository change, or the OCIRepository resolves nothing.
- **The dev channel** (a `components.<name>.semverFilter` selecting the kagent or Substrate line's `-dev.giantswarm.` builds): remove a `kagent.registry: ghcr.io` or `substrate.image.registry: ghcr.io/giantswarm/substrate` set beside the filter — dev builds are published to gsoci like releases, and ghcr.io stops receiving them.
- **Recognising it worked**: `kubectl -n <release namespace> get ocirepository kagent kagent-crds substrate substrate-crds -o custom-columns='NAME:.metadata.name,URL:.spec.url,READY:.status.conditions[?(@.type=="Ready")].status'` lists the four `oci://gsoci.azurecr.io/giantswarm/…` URLs, each `True`; `kubectl -n <release namespace> get helmrelease kagent substrate -o custom-columns='NAME:.metadata.name,REVISION:.status.lastAttemptedRevision'` names a release each line published to gsoci.

## \<current\> → \<next\> (image verification renders the egress Kyverno's admission controller needs; 4.44.4 names the registry's blob storage)

giantswarm/agent-platform#599 (4.44.3; 4.44.4 replaces the data-endpoint pattern by `*.blob.core.windows.net`, the host the registry redirects the bundle's blob to — proven on an installation, where 4.44.3 got past DNS and the registry and stopped at the blob): a `verifyImages` rule is evaluated by Kyverno's admission controller itself — it fetches the image's Sigstore bundle from the registry and verifies it against the Sigstore trust root. A Kyverno that runs under a network policy allowing egress to the API server only (the fleet's Kyverno chart ships exactly that `CiliumNetworkPolicy`; Cilium's default-deny does the rest, DNS included) fails every fetch before it starts (`lookup <registry>: i/o timeout`), and since `verifyImages` runs in the mutating webhook with `failurePolicy: Fail`, the error denies the pod whatever `failureAction` says: with `modelServing.imageVerification` on (the default since 4.43.0) every model pod on such an installation was refused, the `LLMInferenceService` stayed `MinimumReplicasUnavailable` and no GPU node was launched. The connectivity chart now renders, next to the image-verification `ClusterPolicy` and in the cilium network-policy flavor, one `CiliumNetworkPolicy` in Kyverno's namespace selecting the admission controller pods with the egress the verification needs — DNS through Cilium's DNS proxy and TCP 443 to the registry hosts (the platform's registry and the Azure Storage accounts it redirects a blob read to, `*.blob.core.windows.net`) and the Sigstore hosts (the TUF repository, Rekor) —, additive to whatever Kyverno's own policies allow: `modelServing.imageVerification.kyvernoEgress` (`enabled: true`, `namespace: kyverno`, `podSelector: {}` — empty selects the upstream chart's admission-controller labels, a set one renders alone —, `hosts` the four names as `toFQDNs` entries), mirrored in both charts.

### Operator action

- **None** for a Giant Swarm installation: the policy renders with the upgrade, the admission controller's next verification fetches the bundle, the `ReplicaSet` of a model stuck on the denial creates its pod on its next retry (the controller backs off up to five minutes; delete the `ReplicaSet`'s pods' owner `Deployment` reconciliation is not needed — `kubectl -n <serving namespace> rollout restart deployment <model>-kserve` forces the retry).
- **A Kyverno in another namespace or with other labels**: set `kyvernoEgress.namespace` and `kyvernoEgress.podSelector` (a set selector replaces the default, nothing is merged); a Kyverno whose own policies already reach the registry and Sigstore sets `kyvernoEgress.enabled: false`.
- **Images verified from another registry** (`imageVerification.images` names it): add its hosts to `kyvernoEgress.hosts`, as `matchName` entries or a `matchPattern` (`*` matches one label of a name) — the registry itself and wherever it redirects blob reads (a registry with dedicated data endpoints: `*.*.data.azurecr.io`).
- **The kubernetes network-policy flavor** (`networkPolicy.flavor: kubernetes`, or no Cilium API): nothing renders — a `NetworkPolicy` has no names to allow; open the egress for Kyverno's admission controller yourself, or set `modelServing.imageVerification.enabled: false`.
- **Recognising it worked**: `kubectl -n kyverno get ciliumnetworkpolicy <release>-model-serving-image-verification-egress` exists; a newly created model pod is admitted with its images pinned to digests (`kubectl -n <serving namespace> get pod <pod> -o jsonpath='{.metadata.annotations.kyverno\.io/verify-images}'`), and Kyverno's admission-controller log carries no `failed to fetch bundles` for the registry.

## \<current\> → \<next\> (the 24 GB presets of the September 2026 line-up replace the Qwen3 small presets)

giantswarm/agent-platform#591: the connectivity chart ships four 24 GB presets served from signed model images — `gpt-oss-20b`, `gemma-4-12b`, `qwen3-5-9b-fp8` and `qwen3-5-4b` — and no longer ships `qwen3-4b-instruct`, `qwen3-8b-fp8` and `qwen3-14b`. The upgrade removes the three retired presets' ConfigMaps from the serving namespace (they carry no keep policy); the discovery ConfigMap lists twelve presets.

### Operator action

- **None** for an installation that serves none of the three. A model already served from one of them keeps serving: the `LLMInferenceService` model-manager composed is not the preset's object and is not touched by the upgrade. The retired preset can no longer be picked to serve anew; the line-up's successors are `qwen3-5-4b` for `qwen3-4b-instruct`, `qwen3-5-9b-fp8` or `gemma-4-12b` for `qwen3-8b-fp8`, and `gpt-oss-20b` or `gemma-4-12b` for `qwen3-14b`.
- **To keep a retired preset**: copy its file from the previous release into `modelServing.presets`; a values preset renders like a shipped one (`spec.runtime` and `spec.predictor` stay out).
- **The new presets pull their weights as images** from `gsoci.azurecr.io/giantswarm/models/…` (an installation serving from a registry of its own that holds the same paths sets `modelServing.modelImages.registry`) and can be pre-pulled onto the serving nodes with `modelServing.prepull.modelPresets`; they need no Hugging Face egress and no cache claim.
- **Recognising it worked**: `kubectl -n <serving namespace> get configmaps -l agent-platform.giantswarm.io/serving-preset=true` lists `agent-platform-serving-preset-gpt-oss-20b`, `…-gemma-4-12b`, `…-qwen3-5-9b-fp8` and `…-qwen3-5-4b` and none of the three retired names.

## \<current\> → \<next\> (the substrate chart's third-party images from gsoci: `substrate.images`)

giantswarm/agent-platform#575, #580: the meta chart's forwarded `substrate:` block pins the substrate chart's third-party image defaults to their `gsoci.azurecr.io/giantswarm/…` copies — the bundled control-plane database (`substrate.images.postgres`), the bundled snapshot store (`substrate.images.rustfs`) and the agentgateway build the atenet router and egress run (`substrate.images.agentgateway`) — at the same digests the chart pinned on Docker Hub and ghcr. `substrate.images.awsCli`, the rustfs-bucket-init Job's image, is forwarded at the chart's own value unchanged.

### Operator action

- **None** for an installation on the defaults. The upgrade rolls `atenet-router` and `atenet-egress` once (their agentgateway container's reference changes, the bits do not); with the bundled database on (`substrate.postgres.enabled` `true`, or `auto` without the platform's CNPG Cluster) `postgres-0` restarts once onto the same digest; with the bundled store on (`substrate.rustfs.enabled: true`) the `rustfs` Deployment rolls once. The completed `rustfs-bucket-init` Job is untouched: its pod template is immutable, and the forwarded value is the chart's own, so the upgrade does not attempt to change it. A cluster that reaches no public registry no longer mirrors `docker.io/library/postgres`, `docker.io/rustfs/rustfs` and `ghcr.io/giantswarm/agentgateway-upstream/agentgateway` for the substrate release — `docker.io/amazon/aws-cli` stays until the Substrate line ships a bucket-init Job that can be recreated.
- **An installation that overrides `substrate.images.*` itself** (a mirror, or the fully qualified `docker.io/…` names set against CRI-O's short-name mode) keeps its own values — Helm merges the block, an explicit value wins over the chart's default. Drop the `postgres` and `rustfs` overrides to take the gsoci copies; keep an `awsCli` override that differs from the chart's default `amazon/aws-cli:2.17.0@sha256:…` unless you delete the completed `rustfs-bucket-init` Job in `ate-system` in the same step — the upgrade fails with `Job.batch "rustfs-bucket-init" is invalid: spec.template: … field is immutable` otherwise.
- **Recognising it worked**: `kubectl -n ate-system get pods -o jsonpath='{range .items[*]}{.metadata.name}{" "}{.spec.containers[*].image}{"\n"}{end}'` names `gsoci.azurecr.io/giantswarm/postgres:18.4-alpine@…`, `…/rustfs:1.0.0-beta.3@…` and `…/agentgateway:v1.5.1-gs.4` next to the line's own `…/substrate/*` images.

## \<current\> → \<next\> (the classic KServe `InferenceService` path is removed: llm-d is the one serving path)

giantswarm/agent-platform#574: the platform serves models through llm-d alone. Every served model is an `LLMInferenceService` model-manager composes from a published preset onto the well-known `LLMInferenceServiceConfig`s; the connectivity chart renders no `serving.kserve.io` object of its own any more, and the meta chart runs one KServe controller, the llm-d one. Gone, and **refused by the render naming the key** (no alias, no fallback): the components `kserve-crd` and `kserve-resources` (the classic controller and its CRDs) with their values blocks; `modelServing.runtime` (the `kserve-vllm` `ClusterServingRuntime`) and `modelServing.additionalRuntimes`; `modelServing.networkPolicy.predictor` (the classic pod shape's port and callers); `modelServing.serving.deploymentStrategyType` and `.timeoutSeconds` (classic predictor defaults); the preset fields `spec.runtime` and `spec.predictor`; the discovery ConfigMap's `spec.runtime` and `spec.runtimes`. `modelServing.kserve.requireApi` and `modelManager.kserve.requireApi` check the `serving.kserve.io/v1alpha2` `LLMInferenceService` API (`--api-versions serving.kserve.io/v1alpha2` for an offline render). The llm-d controller's release (`kserve-llmisvc-resources`) renders the control plane's shared objects itself — `kserve.createSharedResources: true` is the default and a `false` is refused —, carries `controller.deploymentMode: Standard` and `gateway.disableIngressCreation: true` (the two values that lived on `kserve-resources`), receives the derived models-Gateway reference, and depends on `kserve-llmisvc-crd` alone; `model-manager` depends on it instead of on `kserve-resources`, and its range widens to `>=0.23.0 <2.0.0` (model-manager 1.0.0 is the release that composes `LLMInferenceService`s only). The Kyverno policies, network policies and the `PolicyException` of the serving namespace select the `LLMInferenceService` workload pod alone; `additionalIngressNamespaces` moved from `modelServing.networkPolicy.predictor` to `modelServing.networkPolicy`.

### Operator action

- **None** for an installation whose values carry none of the removed keys and whose models are served on llm-d (a Giant Swarm installation with the serving slice as `examples/serving-slice.yaml` composes it). The connectivity release drops the `kserve-vllm` `ClusterServingRuntime` (a model served as an `LLMInferenceService` never referenced it) and re-renders the serving namespace's policies for one pod shape.
- **The serving slice** (a `<cluster>-agent-platform` release, composed by hand or by cluster-manager): drop `components.kserve-crd` and `components.kserve-resources` from its values — the render refuses them — and, if set, the `kserve-resources:` block; its `deploymentMode` and `disableIngressCreation` are `kserve-llmisvc-resources.kserve.controller.*` now. On the upgrade the `kserve-crd` and `kserve-resources` `HelmRelease`s are uninstalled: the classic controller goes, the classic CRDs stay on the cluster (`helm.sh/resource-policy: keep`) and are the operator's to delete once no `InferenceService` is left (`kubectl get inferenceservices -A`); the shared `inferenceservice-config` ConfigMap, `Issuer` and `ClusterStorageContainer` are re-rendered by the `kserve-llmisvc-resources` release with the same names — its first reconcile races the uninstall (`timeout waiting for: [ConfigMap/<namespace>/inferenceservice-config status: 'NotFound']`: the uninstall deleted the ConfigMap the llm-d release had just rendered) and the next one succeeds; `flux reconcile hr kserve-llmisvc-resources -n <namespace> --force` skips the wait. The `clusterstoragecontainers.serving.kserve.io` CRD stays behind annotated `meta.helm.sh/release-name: kserve-crd`; both CRD charts carry it behind a `lookup` guard that renders it only for its own release, so hand it to the llm-d CRD release or its next schema change never reaches the cluster: `kubectl annotate crd clusterstoragecontainers.serving.kserve.io meta.helm.sh/release-name=kserve-llmisvc-crd meta.helm.sh/release-namespace=<namespace> --overwrite`, then `flux reconcile hr kserve-llmisvc-crd -n <namespace> --force` (its inventory lists the CRD afterwards).
- **A model still served on the classic path** (an `InferenceService` on the `kserve-vllm` runtime): serve it as an `LLMInferenceService` first — through model-manager's `load_model` of its preset on a cluster that runs the llm-d control plane — then move the release; the removal keeps no runtime for it.
- **A preset of your own** (`modelServing.presets`) with `spec.runtime` or `spec.predictor`: drop both; `LLMInferenceService` template fields go under `spec.template`. A model that needs another serving image gets it through the well-known configs' image (`kserve-runtime-configs`), never through a preset's runtime.
- **`modelServing.networkPolicy.predictor.additionalIngressNamespaces` set**: it is `modelServing.networkPolicy.additionalIngressNamespaces`.
- **Recognising it worked**: `kubectl -n <release namespace> get helmreleases` lists `kserve-llmisvc-crd`, `kserve-llmisvc-resources` and `kserve-runtime-configs` and no `kserve-crd` / `kserve-resources`; `kubectl get clusterservingruntimes` names no `kserve-vllm`; the discovery ConfigMap `agent-platform-model-serving` carries no `runtime` key; a loaded model is an `LLMInferenceService` (`kubectl -n <serving namespace> get llminferenceservices`).

## \<current\> → \<next\> (`modelServing.imageVerification` is on by default)

giantswarm/agent-platform#575: both charts' defaults carry `modelServing.imageVerification.enabled: true`. On an installation with the serving switch on and Kyverno served, the connectivity release renders the ClusterPolicy `<release>-model-serving-image-verification` on this upgrade: every container image of a model pod (the model pods of the serving namespace, one rule per pod shape, at CREATE and UPDATE) that matches `gsoci.azurecr.io/giantswarm/*` must carry a Sigstore bundle signature from the Giant Swarm CircleCI identity (issuer `https://oidc.circleci.com`, subject a pipeline definition), is pinned to its digest, and a pod whose image fails is denied. Every image a shipped preset's pod runs carries it — the curated model images, the llm-d runtime and sidecars, the KServe storage-initializer —, so a model served from the shipped presets on the platform's images is admitted as before. Running pods are untouched until they are recreated.

### Operator action

- **None** for an installation serving the shipped presets on the platform's images, and none for an installation that serves from a registry of its own (`modelServing.modelImages.registry`, a `kserve-runtime-configs` image override off `gsoci.azurecr.io/giantswarm/`): an image matching no pattern is left alone.
- **A model pod that runs an image under `gsoci.azurecr.io/giantswarm/` that the Giant Swarm CircleCI identity did not sign** — one pushed to the namespace by hand, one re-signed by a signer of your own — is refused after the upgrade. Before upgrading, either add that signer as an `attestors` entry (keyless `issuer` + `subject` or `subjectRegExp`, or `keys.publicKeys`; the fleet's entry stays beside it, any one signature admits) or set `modelServing.imageVerification.enabled: false`; the block is mirrored in both charts, set it on the release you compose from.
- **Recognising a refusal**: the model's Deployment stays at zero ready replicas and its ReplicaSet's `ReplicaFailure` condition (or `kubectl -n <serving namespace> describe rs`) carries Kyverno's admission error, `verify-model-images-<shape> failed to verify image <reference>: … sigstore bundle verification failed: no matching signatures found`; model-manager surfaces it as the model's phase. A pod that was admitted carries the annotation `kyverno.io/verify-images` listing every verified image with its digest, and its container images are digest references.
- **Recognising it worked**: `kubectl get clusterpolicy <release>-model-serving-image-verification` exists on a cluster with the serving switch on and is Ready; a model loaded after the upgrade runs digest-pinned images.

## \<current\> → \<next\> (the kagent line's, the Substrate line's and the Flux controllers' images from gsoci)

giantswarm/agent-platform#580: `kagent.registry` is `gsoci.azurecr.io`, `substrate.image.registry` is `gsoci.azurecr.io/giantswarm/substrate` and `flux-engine.instance.distribution.registry` is `gsoci.azurecr.io/giantswarm/fluxcd` — retagger's copies of the two lines' releases and of the Flux 2.9+ controllers under their upstream paths, the same digests as on ghcr.io (giantswarm/retagger#1229). The chart sources of the kagent and Substrate components stay on ghcr.io for now.

### Operator action

- **None** for an installation on the defaults. The kagent controller and UI, every Substrate control-plane pod, the atelet DaemonSet and the Flux controllers restart once onto the same image content from the other registry; the `kagent-default` WorkerPool rolls its workers (the worker image's registry changed) — running agent turns end with their actor, as on every WorkerPool change. A cluster that reaches no public registry no longer mirrors the two lines' images or `ghcr.io/fluxcd`; it still mirrors the two lines' charts (`components.kagent*.repository`, `components.substrate*.repository`).
- **`kagent.registry`, `substrate.image.registry` or `flux-engine.instance.distribution.registry` set in your values** (a mirror): they stand; nothing changes for you. A mirror of `ghcr.io/giantswarm/kagent` or `ghcr.io/giantswarm/substrate` can point at gsoci instead — the paths under the registry are the same.
- **The dev channel** (a `components.<name>.semverFilter` selecting the kagent or Substrate line's `-dev.giantswarm.` builds): dev builds are not copied to gsoci. Set `kagent.registry: ghcr.io` and `substrate.image.registry: ghcr.io/giantswarm/substrate` beside the filter, or the pods fail to pull.
- **Recognising it worked**: `kubectl -n <kagent namespace> get deploy kagent-controller -o jsonpath='{.spec.template.spec.containers[*].image}'` names `gsoci.azurecr.io/giantswarm/kagent/controller:<version>`; `kubectl -n ate-system get ds atelet -o jsonpath='{.spec.template.spec.containers[*].image}'` names `gsoci.azurecr.io/giantswarm/substrate/atelet:<version>`; `kubectl -n <release namespace> get fluxinstance flux -o jsonpath='{.spec.distribution.registry}'` reads `gsoci.azurecr.io/giantswarm/fluxcd`; `crictl images` on a node lists no `ghcr.io/giantswarm/kagent`, `ghcr.io/giantswarm/substrate` or `ghcr.io/fluxcd` image once the old pods are gone.

## \<current\> → \<next\> (image defaults from gsoci; `modelServing.imageVerification` defaults to the Giant Swarm identity in the Sigstore bundle format)

giantswarm/agent-platform#575: the hook Jobs' images of both charts (`gitops.hooks.image`, `gitops.hooks.helmImage`; `hooks.kubectlImage`, `hooks.opensslImage`) and the bundled Flux engine's operator image (`flux-engine.operator.image`) come from `gsoci.azurecr.io`; `modelServing.imageVerification` (off by default) gains defaults — `images: [gsoci.azurecr.io/giantswarm/*]`, one keyless attestor for the Giant Swarm CircleCI signing identity (issuer `https://oidc.circleci.com`, subject the pipeline definition that ran) and `type: SigstoreBundle`, the signature format cosign 3 writes and the architect orb produces.

### Operator action

- **None** for an installation on the defaults. A cluster that reaches no public registry no longer mirrors `registry.k8s.io/kubectl`, `docker.io/alpine/k8s`, `docker.io/alpine/openssl` and `ghcr.io/controlplaneio-fluxcd/flux-operator` for the two charts' hooks and the bundled engine. An installation with the KServe components on rolls the KServe controllers once onto the `0.4.x` charts (their image defaults move to gsoci; a served model's pods are untouched until they are recreated, and a new pod's `storage-initializer` and `agent` come from gsoci); a BOM pins the five at `0.4.1` or later (from 0.4.1 the storage-initializer's resources follow `kserve.storage.resources`).
- **`modelServing.imageVerification.enabled: true` with attestors of your own**: the rule now reads Sigstore bundles. If your signer writes cosign 2's `.sig` tags, set `modelServing.imageVerification.type: Cosign`; a bundle-format signature (cosign 3, the architect orb) needs no change. A block that left `images` or `attestors` empty never rendered (the guards refuse it), so no render changes silently.
- **Recognising it worked**: both charts' hook Jobs and the `flux-operator` Deployment name `gsoci.azurecr.io/giantswarm/…` images; with the switch on, the `verifyImages` rule of `<release>-model-serving-image-verification` carries `type: SigstoreBundle` and the CircleCI attestor.

## \<current\> → \<next\> (the pre-pull DaemonSet is a hook object; its selector replaces the default)

giantswarm/agent-platform#562, #563: `modelServing.prepull.nodeSelector` defaults to `{}` in both charts and the template holds Karpenter's `karpenter.k8s.aws/instance-gpu-manufacturer: nvidia`, so a selector an installation sets renders alone (the pool's label still merged under it); and the pre-pull DaemonSet is created by the `post-install,post-upgrade,post-rollback` hooks instead of being a release resource, so nothing waits for its pods — an image that cannot be pulled yet leaves them retrying and the connectivity release Ready. A `pre-delete` hook Job removes the DaemonSet on uninstall.

### Operator action

- **None** for an installation on the defaults. The first upgrade deletes the release-owned DaemonSet and the post-upgrade hook creates it again — its pods restart once, on images already present — and so does every later upgrade (a hook object is replaced, not patched).
- **`modelServing.prepull.nodeSelector` set in your values**: it stands and is now the whole selector; Karpenter's key is no longer merged next to it. Remove a post-renderer or a `null` that stripped the default key. An installation with GPU nodes of both kinds selects them by a label they share — the GPU operator's `nvidia.com/gpu.present: "true"`, say — since a `nodeSelector` is a conjunction.
- **A workaround for the wait** — `disableWait` on the connectivity HelmRelease, a longer `timeout`, a post-renderer taking the DaemonSet out — can go: nothing waits for the DaemonSet any more.
- **Recognising it worked**: `kubectl -n <serving namespace> get daemonset <release>-model-serving-prepull -o jsonpath='{.metadata.annotations.helm\.sh/hook}'` reads `post-install,post-upgrade,post-rollback`; `helm get manifest` of the connectivity release no longer lists the DaemonSet (`helm get hooks` does); the HelmRelease reaches Ready while a pre-pull pod is still `Init:ImagePullBackOff`.

## \<current\> → \<next\> (the serving slice's llm-d images come from the `llm-d-fast/` prefix)

giantswarm/agent-platform#568: the meta chart's `kserve-runtime-configs:` block passes `kserve.llmisvcConfigs.imageRegistry: gsoci.azurecr.io/giantswarm/llm-d-fast/`, and `modelServing.prepull.images` names `gsoci.azurecr.io/giantswarm/llm-d-fast/llm-d-cuda:v0.8.0` — the same images as the byte-identical mirror at `gsoci.azurecr.io/giantswarm/`, re-layered (zstd, layers of at most 1.2 GB) so a node pulls them over several streams.

### Operator action

- **None** for an installation that follows the chart's defaults. The well-known configs change their image references, so a served `LLMInferenceService` rolls once onto the re-layered image on its next reconcile; the pre-pull DaemonSet rolls with it and warms the new reference on every pool node.
- **`kserve-runtime-configs.kserve.llmisvcConfigs.imageRegistry` set in your values** (a private registry): it stands. Set `modelServing.prepull.images` to the matching `llm-d-cuda` reference under your prefix, and mirror the `llm-d-fast/` set — or the byte-identical one — there.
- **To stay on the byte-identical mirror or on upstream**: `kserve-runtime-configs.kserve.llmisvcConfigs.imageRegistry: gsoci.azurecr.io/giantswarm/` (or `ghcr.io/llm-d/`) and `modelServing.prepull.images: [gsoci.azurecr.io/giantswarm/llm-d-cuda:v0.8.0]` (or the `ghcr.io/llm-d/` reference). The two must carry the same prefix: the pre-pull warms exactly the image the predictor runs.
- **Recognising it worked**: the pre-pull pod's init container and the model pod's runtime container name an image under `…/llm-d-fast/`, and the predictor's `Pulled` event reads "already present on machine".

## \<current\> → \<next\> (the cache claim's defaults are 100Gi at 500 MiB/s; the default class is named with a digest of its parameters)

giantswarm/agent-platform#570: `modelServing.cache.pvc.size` defaults to `100Gi` (was `500Gi`) and the claim's gp3 class to `throughput: "500"`, `iops: "3000"` (was 1000 / 4000): the loader reads at about 300 MB/s on either tier, the download writes at the Hub's ≈ 200 MB/s, and two served models need well under 40 GiB. A StorageClass's parameters are immutable, so the default class is now `agent-platform-connectivity-hf-cache-<digest>` (eight hex characters of provisioner and parameters); the previous `agent-platform-connectivity-hf-cache` goes with the upgrade.

### Operator action

- **None.** An installation with a claim keeps it as it is: the hook applies the claim on the class it has and at its size (a claim never shrinks), and a bound volume works on without the removed class. A fresh installation gets a `100Gi` claim on the new class. Expect one Helm revision of the connectivity release (the class replaced, the hook re-run).
- **To move an existing installation to the new defaults**: delete the claim while no model is served (`kubectl -n <serving namespace> delete pvc hf-cache` — the volume and the downloaded weights go with it); the next upgrade applies it at `100Gi` on the new class, and the next served model downloads its weights once more.
- **To keep growing an existing claim that sits on the removed class** (a claim grows only on a class the cluster has): render that class again under its old name — `modelServing.cache.storageClass.name: agent-platform-connectivity-hf-cache` with `parameters` `type: gp3`, `iops: "4000"`, `throughput: "1000"` — and the claim is on the rendered class as before.
- **To keep the previous tier on a fresh installation**: the same `parameters` (the class then renders under that tier's digest) and `pvc.size: 500Gi`.
- **Recognising it worked**: `kubectl get storageclass` lists `agent-platform-connectivity-hf-cache-<digest>` and no longer the digest-free name (unless you named it), and `kubectl -n <serving namespace> get pvc hf-cache` shows the size and class the claim had before — or `100Gi` on the new class where none existed.

## \<current\> → \<next\> (the Substrate worker image follows the chart's Substrate pin; the Substrate line moves to `v0.0.30-gs.4`)

giantswarm/agent-platform#466: the kagent WorkerPool's `workerImage` is now **derived from `components.substrate.versionRange`'s floor** — `<substrate.image.registry>/ateom-gvisor:<floor>`, `ghcr.io/giantswarm/substrate/ateom-gvisor:0.0.30-gs.4` — and merged over the kagent block the chart forwards. The kagent chart's own stamp (the Substrate its build was published against) no longer reaches the cluster, so the atelet and the worker are one Substrate release whatever kagent build the kagent range admits. Chart 4.15.2 had admitted kagent `0.11.0-gs.14` (a `0.0.30` worker) under its `0.0.27-gs.9` atelet and booted no golden actor. `components.substrate` / `components.substrate-crds` move to `>=0.0.30-gs.4 <0.0.31-0` (the bounded golden boot, giantswarm/substrate#39 — the worker the fleet's WorkerPools already run through kagent `0.11.0-gs.20`'s stamp).

### Operator action

- **None** for an installation that follows the current chart: the resolved kagent (`0.11.0-gs.20`) already stamps the `0.0.30-gs.4` worker, so the WorkerPool's `spec.workerImage` is unchanged and **the pool does not roll**; the substrate releases resolve `0.0.30-gs.4` as before. An installation whose WorkerPool runs another worker (an exact older kagent pin) rolls the pool once, one worker at a time under the budget — a turn in flight on a replaced worker is lost, a session paused on it too.
- **`kagent.substrateWorkerPool.workerImage` set in your values**: leave it unset (the chart derives it; a mirror sets `substrate.image.registry`, which the derived image follows), or make sure it names an `ateom-gvisor` image **tagged `0.0.30-gs.4`** — another tag, or a digest alone, now fails the render naming the key, the release and the derived image.
- **`components.substrate.versionRange` set in your values**: it must confine one release — an exact version, or `>=X.Y.Z-gs.N <X.Y.(Z+1)-0` (what `values.yaml` and the fleet template render). `0.x`, `~`, `^`, a `<=` ceiling or a floor alone fail the render naming the range.
- **Re-pinning Substrate from now on** is the two `versionRange`s alone; the worker follows. Expect the pool to roll once per re-pin. `make verify-worker-image` fails when the kagent build the kagent range admits was published against another Substrate release than the chart pins — move the kagent range and the Substrate range together then.
- **Recognising it worked**: `kubectl -n kagent get workerpool kagent-default -o jsonpath='{.spec.workerImage}'` and `kubectl -n ate-system get ds atelet -o jsonpath='{.spec.template.spec.containers[0].image}'` name the same Substrate version.
## \<current\> → \<next\> (klaus-gateway drops the crd and configmap routing stores; the meta chart stops setting `klausGateway.crd.install`)

giantswarm/klaus-gateway#271 removes the `crd` and `configmap` routing stores, the `ChannelRoute` CRD and the embedded controller from klaus-gateway, and that chart's `values.schema.json` is closed (`additionalProperties: false`), so it refuses `crd.*` and `controller.*`. The meta chart forwards the whole `klausGateway` block to the klaus-gateway release and its defaults carried `klausGateway.crd.install: true`, so this release has to be on an installation **before** the klaus-gateway release that drops those keys rolls — the component range `>=1.10.0 <2.0.0` takes it with no PR, and a values file the schema refuses fails the Helm upgrade: the klaus-gateway HelmRelease sticks, with its pods up on the old revision. The connectivity chart's Kubernetes API egress for the gateway pod (`-klausgateway-store-egress`) now has one gate, the Secret link store (`klausGateway.obo.store: secret` with OBO on); the Valkey rule and every other policy are unchanged.

### Operator action

- **None** for an installation on `klausGateway.routing.store: valkey` — all three are. The klaus-gateway release gets one Helm revision (a values change: `crd.install` leaves the forwarded block, and the current klaus-gateway chart defaults it to `true`, so the rendered objects do not change) and no pod rolls.
- **An installation that set `klausGateway.crd.*` or `klausGateway.controller.*` in its own values must remove them** before the next klaus-gateway chart rolls. Left in place they reach the release through the whole-block forward, the closed schema refuses them and the klaus-gateway HelmRelease fails validation (`values don't meet the specifications of the schema`) and stays on its last revision. None of the fleet's installations sets either.
- **An installation that ran `klausGateway.routing.store: crd` or `configmap`** switches to `valkey` first (the platform's own Valkey, `components.valkey`; the meta chart fills `routing.valkey` from that release) — the two stores are gone from the gateway. None did.
- **Recognising it worked**: in the meta chart's namespace (`flux-giantswarm` on the fleet), `kubectl -n flux-giantswarm get helmrelease klaus-gateway -o jsonpath='{.spec.values.crd}'` is empty, the release is `Ready`, and the gateway pod's age is unchanged.

## \<current\> → \<next\> (the agentgateway data plane runs two replicas behind a `PodDisruptionBudget`, spread across nodes)

`gateway.parameters` gains `replicas` (`2`), `podDisruptionBudget` (`enabled: true`; the spec is `maxUnavailable: 1` unless `minAvailable` or `maxUnavailable` is set) and `spread` (`enabled: true`, `topologyKeys: [kubernetes.io/hostname]`, `maxSkew: 1`, `whenUnsatisfiable: ScheduleAnyway`, and `matchLabelKeys: [pod-template-hash]` so a rollout spreads the new `ReplicaSet` against itself rather than against the revision it replaces), rendered into the `AgentgatewayParameters` the agentgateway controller reconciles the data-plane `Deployment` from (`templates/agentgateway/agentgatewayparameters.yaml`). The meta chart declares the same keys at the same defaults and forwards `agentgateway.controller.replicaCount: 2` to the controller release.

### Operator action

- **None** for the default shape. Every installation with the agentgateway component on rolls the data plane once (the second pod, the `PodDisruptionBudget` and the spread constraint arrive in one Deployment revision; the default `RollingUpdate` surges first) and the controller once (a second pod behind the chart's leader election). Budget two more pods of the data plane's and the controller's size on the node pool.
- Two nodes are not required to schedule: `whenUnsatisfiable: ScheduleAnyway` lets a single-node cluster place both pods on its one node. A **drain of that single node then waits on the budget** (the first eviction succeeds, the replacement pod cannot schedule on the cordoned node, the second eviction is refused until the drain times out); set `gateway.parameters.podDisruptionBudget.enabled: false` on a single-node cluster. `DoNotSchedule` pins the spread; the second pod then stays Pending on one node.
- A multi-zone node pool spreads across zones too with a second key: `gateway.parameters.spread.topologyKeys: [kubernetes.io/hostname, topology.kubernetes.io/zone]`.
- **The second controller pod is not node redundancy.** Leader election is on (the controller's manager defaults `DisableLeaderElection` to false, the chart grants the lease and sets no `AGW_DISABLE_LEADER_ELECTION`), so the two pods do not both reconcile the data plane or write `Gateway` status — the status syncer runs on the leader alone, the xDS syncer on every replica. But the packaging chart's schema admits no `controller.podDisruptionBudget`, no `affinity` and no `topologySpreadConstraints`, so the two pods cannot be spread and often land on one node: what the second replica covers is a pod-level failure and a rolling restart, not a node reboot. A budget and a spread for the controller follow a schema fix there (giantswarm/agentgateway#51).
- `minAvailable` is set as such (`gateway.parameters.podDisruptionBudget.minAvailable: 1`, or a percentage) — through the meta chart too, with no `maxUnavailable: null` next to it: the `maxUnavailable: 1` default is emitted by the connectivity template only when neither field is set.
- `gateway.parameters.podAnnotations` **loses its `karpenter.sh/do-not-disrupt: "true"` default** (4.15.0, #431). That annotation was the answer to a single data-plane pod; with two replicas, the budget and the spread it would pin both pods' nodes against Karpenter's consolidation, drift and expiry indefinitely, and the budget — what actually holds a drain to one pod — would never be consulted. Karpenter may now consolidate or drift a data-plane node, one pod at a time. What the annotation still bought is the streams open on the evicted pod: those are cut at the shutdown window (10 s still accepting, draining to the 55 s deadline) instead of carried over. An installation that would rather have the node churn stop sets `gateway.parameters.podAnnotations."karpenter.sh/do-not-disrupt": "true"` back.
- The previous shape is `gateway.parameters.replicas: 1`, `gateway.parameters.podDisruptionBudget.enabled: false`, `gateway.parameters.spread.enabled: false` (and `agentgateway.controller.replicaCount: 1` through the meta chart).
- `unhealthyPodEvictionPolicy` is the third key the budget passes through (`gateway.parameters.podDisruptionBudget.unhealthyPodEvictionPolicy: AlwaysAllow` to let a drain evict a data-plane pod that is not Ready). Setting it alone keeps the `maxUnavailable: 1` default next to it.
- The render refuses `podDisruptionBudget` with both `minAvailable` and `maxUnavailable`, a key that is not one of the three it passes through (the block is open in the schema, so a misspelt `minAvailabe` would otherwise be dropped in silence), a string that is not a percentage from `0%` to `100%`, a fractional or negative number, an `unhealthyPodEvictionPolicy` outside `IfHealthyBudget`/`AlwaysAllow`, any budget that allows no eviction (an integer `minAvailable` at or above `replicas`, a percentage `minAvailable` that rounds up to every replica — above 50% at two replicas —, a zero `maxUnavailable`), `spread.enabled` with an empty `topologyKeys` or an empty key in it, and `replicas` or `maxSkew` below 1.
- **Do not null out `replicas`, `spread.maxSkew` or `spread.whenUnsatisfiable`** to "get the default": Helm deletes the key at the layer where the null is set, so the chart sees a missing key rather than a value, and no schema minimum or enum can catch it. Each is refused with a message naming the key. (Unguarded, `replicas` alone rendered `deployment.spec.replicas: 0` and scaled the data plane to zero.)
- MCP clients that still use the legacy **SSE** transport through the data plane hold pod-local sessions, and two replicas break them outright: the session lives on the pod that answered `/sse`, the ClusterIP Service balances each later POST on its own, and a POST that lands on the other pod finds no session. This is not limited to pod changes. Move such clients to streamable-HTTP (the platform's own `RemoteMCPServer`s already use it and survive a pod change), or run that installation at `gateway.parameters.replicas: 1`.

## \<current\> → \<next\> (model-manager ships with every installation by default, with no backend; the model-manager line moves to `>=0.22.0`)

giantswarm/agent-platform#329 (bumblebee-plans#46 round 3, D3/D4/D5/D7; epic giantswarm/giantswarm#37639): `components.model-manager.enabled` now defaults to `true`, and the block the meta chart forwards names **no backend** — neither `model-manager.backend` (the old default, `ollama`) nor `model-manager.backends`. The release starts with zero backends (model-manager 0.22.0's zero-backend start-up, range `>=0.22.0 <1.0.0`); backends are registered at runtime as labelled ConfigMaps in model-manager's namespace — by a person through the `add_backend` tool or the portal's Serving page, by cluster-manager for a GPU node pool — and appear on the Serving page without a chart change. See model-manager's [docs/backends.md](https://github.com/giantswarm/model-manager/blob/main/docs/backends.md) for the document, its labels and who may write it.

- **An installation that set nothing for model-manager** gains exactly the model-manager objects on its next reconcile: the OCIRepository and HelmRelease, and in the connectivity release the MCPServer CR, the JWT policy and the network policies, rendered from `global.identity` and the shared OAuth secret as for the other resource servers — no per-installation value is required. The default egress opens the identity provider only: no model server, no Hub.
- **An installation with a static backend** (`model-manager.backend: ollama` with `model-manager.ollama.endpoint`, or `model-manager.backends: [...]`) renders byte-identically: the guards demand an endpoint only for a backend that *is* listed, exactly as before, and `backend: ollama` / `backends: [ollama]` without an endpoint still fail the render.
- **With `components.kagent` off** the meta chart derives `kagent.disableWiring: true` for the model-manager release — the release must not wire ModelConfigs into a kagent the installation does not run. With kagent on, `model-manager.kagent.disableWiring` stands as set. **With `components.muster` off** it derives `muster.mcpServer.enabled: false`: the MCPServer CRD ships with muster, so model-manager's MCP surface follows it and the REST API stays. **With muster's OAuth server off** (`muster.muster.oauth.server.enabled: false`, the lab shape of `examples/kind-lab-dex.yaml`) it derives `oauth.enabled: false`: a platform without a login has no issuer for model-manager to trust.
- **Model-manager's OAuth guard** follows the muster guard's convention: a `model-manager.oauth.dex.issuerURL` / `dex.clientID` / `existingSecret` that disagrees with `global.identity` fails the render (the platform has one login provider), checked only where both sides are set; a missing input is reported by the model-manager release itself, as for muster — a render that sets `global.domain` or `global.identity.issuerUrl` alone is untouched; every fleet installation sets the contract and keeps the actionable message.
- **A backend registered at runtime and an enforcing network policy** (giantswarm/agent-platform#478): the connectivity chart's egress policy for model-manager is rendered from the static inputs, so a backend registered at runtime is not opened by it — under the cilium flavour an in-cluster Ollama on `:11434` is dropped, under the kubernetes flavour everything but 443. `modelManager.networkPolicy.registeredBackends` is the one input: a list of the destinations the installation allows — `{cidr, port}` entries in both flavours, `{fqdn, port}` under cilium (a name for an in-cluster Service: Cilium's CIDR rules match neither pods nor nodes) — rendered as one more egress rule each next to the static-backend rules; empty (the default) renders nothing.

### Operator action

- **None to install.** Flux resolves the new default and the new range on its next reconcile.
- **To opt out** set `components.model-manager.enabled: false` — the render then carries nothing of model-manager's, as before the flip.
- **A slice release beside the platform's release** (the serving or runtime profile of #326/#317, `ci/test-slice-*-values.yaml`) sets `components.model-manager.enabled: false` like muster, dicebear and valkey: the platform's release owns model-manager, and the slice's backends are registered with it at runtime.
- **Recognising it worked**: `kubectl -n agent-platform get helmrelease model-manager` is `Ready`; `list_backends` through the platform's MCP answers an empty list until a backend is registered.
- **Registering a backend under an enforcing network policy**: put its destination in `modelManager.networkPolicy.registeredBackends` in the same change as the registration — the in-cluster Ollama of the Serving page as `- {fqdn: ollama.models.svc.cluster.local, port: 11434}` (cilium) or `- {cidr: <its Service's or pods' block>, port: 11434}` (kubernetes), an LM Studio on the LAN as `- {cidr: 192.0.2.7/32, port: 1234}`. Without it `add_backend` succeeds, the backend shows on the Serving page and every model call fails with a connection timeout — a policy drop, not a model-manager error. A `cidr` that does not parse, a missing `port` and an `fqdn` under the kubernetes flavour fail the render naming the entry; `make verify-managers` asserts the rule in both flavours.

## \<current\> → \<next\> (the Substrate line moves to `v0.0.30-gs.2`: the worker pool can be spread over nodes and zones)

giantswarm/agent-platform#472, second half: Substrate `v0.0.30-gs.2` (the carried patch giantswarm/giantswarm#37797) is the first release whose `WorkerPool` CRD carries `spec.template.topologySpreadConstraints` and `spec.template.podAntiAffinity`. `components.substrate` / `components.substrate-crds` move to `>=0.0.30-gs.2 <0.0.31-0` and `agent-platform.substrate.workerPoolSpreadFloor` names that release, so `kagent.substrateWorkerPool.template.topologySpreadConstraints` / `podAntiAffinity` now **pass the render and reach the `WorkerPool` verbatim**; an installation that pins the Substrate range below `0.0.30-gs.2` still has them refused, naming the key, the floor and the range. The release also carries giantswarm/substrate#35: a paused actor whose node is gone fails its resume fast (`LOCAL_SNAPSHOT_GONE`) instead of hanging for the workflow deadline. The worker image the kagent chart stamps (`ateom-gvisor:0.0.30-gs.1`) is unchanged and runs under the gs.2 control plane — the release touches ate-controller, the CRD and ate-api only.

### Operator action

- **None to install.** Flux resolves the new range on its next reconcile: the `substrate-crds` release brings the two fields, the Substrate control plane rolls (`ate-api-server`, `ate-controller`, the `atenet` router, egress and dns, the `atelet` DaemonSet), the worker pods and the goldens are untouched — the `WorkerPool` template did not change. A turn placed during the control-plane roll may wait a few seconds on ate-api.
- **Spreading the pool** is a values change on `kagent.substrateWorkerPool.template` — recommended: hostname `maxSkew: 1`, `minDomains: 2`, `whenUnsatisfiable: DoNotSchedule` (the provisioner adds the second node) and zone `maxSkew: 1`, `ScheduleAnyway`, both with a `labelSelector` on `ate.dev/worker-pool: <pool name>` (README "Agent Substrate", "Spread"; the fleet template renders exactly that behind a knob). It is a `spec.template` change and **rolls the pool's Deployment once** (one worker at a time under the budget; a turn in flight on a replaced worker is lost, a session paused on it too; the goldens are untouched) — land it in a quiet window, per installation.
- **Recognising it worked**: `kubectl -n kagent get workerpool kagent-default -o jsonpath='{.spec.template.topologySpreadConstraints}'` echoes the constraints (below gs.2 the apiserver dropped them and the field read empty), `kubectl -n kagent get pods -l ate.dev/worker-pool=kagent-default -o wide` shows the workers on at least two nodes once a spread is set, and `make verify-workerpool` forwards the spread at the floor and refuses it below.

## \<current\> → \<next\> (the Substrate worker pool gets a `PodDisruptionBudget`; the Karpenter knobs of the pool template are documented; a spread value is refused until the Substrate line carries it)

giantswarm/agent-platform#472: the platform runs every agent on the one Substrate `WorkerPool` (`kagent.substrateWorkerPool`, four gVisor workers, one actor each), and nothing guarded its workers against voluntary disruption — no budget selected them, no annotation kept Karpenter's consolidation off their node — while on gazelle (2026-09-15) Karpenter had bin-packed all four onto one spot node. Decided (2026-09-15): the pool **stays on spot** and the workers get a **`PodDisruptionBudget` only**; `karpenter.sh/do-not-disrupt` and `karpenter.sh/capacity-type: on-demand` stay documented knobs an installation may set, unset by default.

- **`kagent.substrateWorkerPool.podDisruptionBudget`** — new, **on by default** (`maxUnavailable: 1`, `unhealthyPodEvictionPolicy: AlwaysAllow`): a `PodDisruptionBudget` named after the pool (`kagent-default`) in the kagent namespace, rendered by the connectivity chart, selecting the worker pods by Substrate's `ate.dev/worker-pool: <pool>` label. A voluntary drain (consolidation, a node roll, `kubectl drain`) moves one worker at a time; `ALLOWED DISRUPTIONS 1` with four Ready workers. The key never reaches the kagent release (`components.kagent.omitKeys`).
- **The two Karpenter knobs of `kagent.substrateWorkerPool.template`** are documented in values.yaml and the README with their effect and caveats: `annotations: {karpenter.sh/do-not-disrupt: "true"}` keeps consolidation, drift and expiry off a worker node (a drift roll then waits for the NodePool's `terminationGracePeriod` — a NodePool without one never rolls the node while a worker is on it); `nodeSelector: {karpenter.sh/capacity-type: on-demand}` is the only knob that covers a spot interruption (Karpenter-only — never on CAPZ, on-prem or a cluster-aws node pool, where the workers would stay `Pending`). Both forward verbatim; `make verify-workerpool` asserts they reach the `WorkerPool` as written.
- **`template.topologySpreadConstraints` and `template.podAntiAffinity` are refused at the render** — and so is any other key `WorkerPool.spec.template` does not have (`labels`, `annotations`, `nodeSelector`, `tolerations`, `priorityClassName`, `nodeAffinity`, `resources` are its fields): the CRD is a structural schema that prunes unknown keys silently, so a value that looked applied did nothing. The two spread keys are admitted once `components.substrate.versionRange`'s floor is the Substrate release that carries them (giantswarm/giantswarm#37797; `agent-platform.substrate.workerPoolSpreadFloor` in `_helpers.tpl` names it — empty until that release is out).
- **`kagent.controller.substrate.defaultWorkerPool` is inert on kagent API v2** and values.yaml now says so: the kagent chart renders it into the controller's `SUBSTRATE_DEFAULT_WORKERPOOL_*` env and nothing in the line reads them — every `ActorTemplate` is pinned to the pool of the Harness that admitted it (`kagent.harness`, whose `workerPoolRef` follows `substrateWorkerPool.name`).

### Operator action

- **None to take the budget.** It is a new object in the kagent namespace; no pod rolls. `kubectl -n kagent get pdb kagent-default` reads `MAX UNAVAILABLE 1`, `ALLOWED DISRUPTIONS 1` with four Ready workers. Opt out with `kagent.substrateWorkerPool.podDisruptionBudget.enabled: false`. **Know what it does not do:** a spot reclaim is not a voluntary eviction — the node goes two minutes after the notice whatever the budget says, and every worker on it with its turns in flight and its paused sessions (giantswarm/giantswarm#37795).
- **Decide the two knobs per installation** — both change `spec.template`, and **every `spec.template` change rolls the pool's Deployment once** (`RollingUpdate` 25 %/25 %, one worker at a time under the budget; about a minute per worker on a node that is up, about three when Karpenter launches one): a turn in flight on a replaced worker is lost, a session paused on it too, the goldens are untouched. Land it in a quiet window. On the fleet the capacity type is `agentPlatform.workerPoolCapacityType` in the installation's config (`shared-configs` default `apps/agent-platform`; giantswarm/shared-configs#738 adds the annotation knob).
- **An installation that already carries a spread constraint in `kagent.substrateWorkerPool.template`** — none is known — fails the render with a message naming the key and the range; the value was doing nothing (pruned at admission). Remove it until the Substrate range's floor is the release that carries the field, then put it back.
- **Recognising the old state**: `kubectl -n kagent get pdb` lists `kagent-controller` and the database budget but nothing selecting `ate.dev/worker-pool`; `kubectl -n kagent get workerpool kagent-default -o jsonpath='{.spec.template}'` shows no key the CRD does not carry (the apiserver dropped it), whatever the values said. After the upgrade the budget is there and `make verify-workerpool` (in CI) holds the forwarding.
## \<current\> → \<next\> (the kagent controller gets a VerticalPodAutoscaler: `kagent.controller.vpa`, `InPlaceOrRecreate`)

giantswarm/agent-platform#455: the connectivity chart renders a `VerticalPodAutoscaler kagent-controller` in the kagent namespace on the controller Deployment wherever the cluster serves `autoscaling.k8s.io/v1` (`kagent.controller.vpa.enabled: auto`). Its update mode is `InPlaceOrRecreate`: the running pod's CPU and memory requests are resized in place, no eviction and no roll. The chart's limits stay (`controlledValues: RequestsOnly`); the recommendation is held between the chart's requests (100m / 128Mi) and a step under its limits (1900m / 480Mi). Requests equal to the limits on both resources would turn the Burstable pod Guaranteed, and Kubernetes refuses a resize that changes the QoS class — so `maxAllowed` must stay under the limits.

### Operator action

- **None** on a Giant Swarm management cluster (VPA 1.5.1 from vertical-pod-autoscaler-app 6.1.2 on Kubernetes 1.35; graveler verified): the connectivity release adds the one object, and the recommender applies its first recommendation in place after it has gathered history. The controller pod is not rolled.
- **A cluster whose VPA is older than 1.5.0**: the `InPlaceOrRecreate` mode is behind a feature gate in 1.4 and unknown to the 1.3 CRD, so the connectivity release would fail on the object. Set `kagent.controller.vpa.updateMode: Initial` (applied on the pod's next roll) or `Off` (recommendations only), or upgrade the VPA first.
- **A cluster without the VPA CRD** (`auto` resolves off): nothing renders. `kagent.controller.vpa.enabled: false` opts out on a cluster that has it.
- A resize the cluster cannot apply in place (the node out of room; a `maxAllowed` raised to the limits, which would change the QoS class) makes the VPA fall back to an eviction, which the controller's `PodDisruptionBudget minAvailable: 1` refuses. The pod then keeps its requests until its next roll; nothing is stuck. Only requests move, and a request decrease applies in place.

## \<current\> → \<next\> (the kagent line moves to `v0.11.0-gs.16`: context compaction for every platform agent)

`components.kagent` / `components.kagent-crds` move to `>=0.11.0-gs.16 <0.11.1-0` — the release that carries kagent-dev/kagent#2790: `spec.context.compaction` on the AgentTemplate CRD and the Go ADK runtime honouring it (giantswarm/giantswarm#37792). The compaction default itself is the agent chart's (giantswarm/agent `1.3.0`, `context.compaction`: tail retention at 24 000 prompt tokens, the last four events kept, the summaries on the agent's own model), which every agent HelmRelease follows on range `1.x`; this chart moves the CRD floor under it.

### Operator action

- **None to install.** Flux resolves the new range on its next reconcile: the kagent-crds release brings the field, the kagent controller rolls, the platform Harness moves to the release's Go ADK digest and **every admitted `AgentTemplate` recompiles** (a new golden snapshot each, about 20 s per template, the pool's workers busy meanwhile). When the agent chart `1.3.0` is admitted (its OCIRepository range `1.x`, ten minutes at most), every agent's template gains `spec.context.compaction` and recompiles once more. The order matters and is the chart's: the CRDs move before the agents render the field (`components.kagent` `dependsOn` `kagent-crds`; the agent chart is a separate release that reconciles on its own interval), so an agent rendered before the CRD moved would have had the field pruned in silence — helm-controller's drift detection re-applies it on the release's next reconcile once the CRD knows it.
- **Opting out, per agent**: `context.compaction.enabled: false` on the agent's HelmRelease values (the portal keeps keys it does not own); `tokenThreshold` / `eventRetentionSize` to move the point; `summarizer.modelConfig` for a cheaper summariser. There is no platform-wide switch: the default is the chart's, and a GitOps-owned agent sets its own values.
- **Recognising it worked**: the actor's startup log (`kubectl -n kagent logs <worker pod> -c ateom | grep 'context compaction'`, or the golden boot's log through Substrate) reads `context compaction enabled token_threshold=24000 event_retention_size=4 …`; on a turn that crosses the threshold agentgateway's LLM listener log (`listener=llm`) shows one extra model call — the summary, with the transcript as its input — and the following calls' `gen_ai.usage.input_tokens` back near the prefix plus the retained tail instead of the whole history. `kubectl -n kagent get agenttemplate <name> -o jsonpath='{.spec.context}'` shows the field; empty on an agent the CRD pruned it from.

## \<current\> → \<next\> (`agent-platform-connectivity` follows the meta chart's own version; a pin on it is refused)

giantswarm/agent-platform#450: the wiring chart is published off the same tag as this chart, but its `versionRange` was a range of its own (`>=4.0.0 <5.0.0`) that a BOM pinned separately — and every BOM's connectivity lagged the moment the meta chart moved. Three releases in a row forwarded a key the pinned connectivity's closed schema refused (#431, #441, #339), failing the release on every BOM-pinned installation. `components.agent-platform-connectivity` is now `releasedWithChart: true` with an empty `versionRange`: the OCIRepository carries the meta chart's own exact version, so the connectivity release rolls exactly when the meta chart rolls.

### Operator action

- **None** for an installation on the chart's defaults: the connectivity OCIRepository's `semver` changes from the range to the exact version of the meta chart it runs. For an installation on the current release that is the chart the range resolved to already; for one whose connectivity had lagged (a BOM that pinned it) it is an upgrade to the meta chart's version, which is the pair that was tested together.
- **A BOM that pins `components.agent-platform-connectivity.versionRange`** must drop the pin: the render refuses it (`pins a chart that is released with this chart`). The example BOM no longer carries one; `tests/verify-components.py` refuses one in it.
- **A lab that follows a branch's connectivity build** keeps its knobs: a `versionRange` together with a `semverFilter` (the dev channel) or with another `repository` (a chart pushed by hand) is admitted as before. A lab that installs the meta chart from a checkout (`platform.chartPath`) renders connectivity at the checkout's placeholder version, which no registry publishes — set the dev channel of your branch on the connectivity entry, the way the agentlab skill's loop C does.
- **Recognising the old state**: `kubectl -n <flux namespace> get ocirepository agent-platform-connectivity -o jsonpath='{.spec.ref.semver}'` prints a range (`>=4.0.0 <5.0.0`) or a pin; after the upgrade it prints the meta chart's version.

## \<current\> → \<next\> (the actors reach the OTLP gateway; the pre-response trace flush is capped at 0.5 s)

giantswarm/agent-platform#456: every turn ended 3 s after its task had completed — the Go ADK's pre-response trace flush (`KAGENT_PRE_RESPONSE_TRACE_FLUSH`, set by the controller on Substrate actors) spent its full 3 s deadline because `substrate-atenet-egress`, the actors' egress allow-list, opened nothing towards the OTLP gateway. The connectivity chart now opens the gateway kagent.otel names on the egress gateway and on the controller's policy (the pods of the endpoint's namespace on its port — kube-system:4317 by default; the controller's rule was the `cluster` entity on 4317, now the namespace), and the platform Harness's env carries `OTEL_LOGGING_ENABLED=true`, `OTEL_EXPORTER_OTLP_HEADERS=X-Scope-OrgID=giantswarm` and `KAGENT_TRACE_FLUSH_TIMEOUT_MS=500`.

### Operator action

- **None** for an installation on the chart's `kagent.otel` defaults: the two policies gain a rule on the next reconcile, the Harness's env changes and the kagent controller recompiles every admitted template (a new golden snapshot each; the agents stay Ready, a turn during the recompile waits for it). A turn's "done" (Swarmgeist's ✅, the portal's receipt) follows the answer within about half a second instead of 3–3.6 s.
- **An installation that overrides `kagent.harness.env`** replaces the whole list (Helm list semantics): carry the three new entries next to `KAGENT_PROPAGATE_TOKEN`, or the flush stays at the ADK's 3 s and the actors' logs keep posting to the gRPC port.
- **An installation whose OTLP endpoint is not an in-cluster Service address** (`kagent.otel.*.exporter.otlp.endpoint` on a plain hostname or an IP) gets the `cluster` entity on the endpoint's port, as before; an endpoint outside the cluster needs the installation's own rule.
- **Recognising the old state**: the worker log (`{namespace="kagent", pod=~"kagent-default-.*"}`) shows `traces export: context deadline exceeded` exactly 3.000 s after a task's `TASK_STATE_COMPLETED`, and `Post "…:4317/v1/logs": EOF`; the controller's `rpc completed … SendStreamingMessage … duration_ms` minus the task's `status_timestamp` in `agent_instance_task` is 3.3–3.6 s. After the upgrade neither line appears during a turn and the gap is under 0.5 s; the actors' spans (`a2a-server`, the invocation, the model and tool calls) appear in Tempo under the controller's `SendStreamingMessage` trace.

## \<current\> → \<next\> (the three upstream lines re-pin together: Substrate `v0.0.30-gs.1` on upstream v0.0.29, kagent `v0.11.0-gs.14` on upstream main 800015de, agentgateway `v1.5.1-gs.4` on upstream main c1d24607)

`components.substrate` / `components.substrate-crds` move to `>=0.0.30-gs.1 <0.0.31-0`, `components.kagent` / `components.kagent-crds` to `>=0.11.0-gs.14 <0.11.1-0`, the connectivity chart's `agentgateway.proxy.image.tag` to `v1.5.1-gs.4`. The three are one move: kagent past kagent-dev/kagent#2802 addresses an actor by the `ate-target-actor` header, which only a Substrate router from upstream v0.0.28 on knows, and the v0.0.29 Substrate chart's egress gateway speaks the frontend-policy shape (`substrateEgressActorResolution`, agentgateway#3318) and the substrate ingress header (agentgateway#3409) of an agentgateway past v1.5.0. A range that admits one line's new release without the others' breaks every turn: a BOM pins all three together (`examples/customer-bom.yaml`).

### Operator action

- **None to install.** Flux resolves the new ranges on the next reconcile; the Substrate control plane rolls (`ate-api-server`, `ate-controller`, the `atenet` router, egress gateway and dns, the `atelet` DaemonSet), the `WorkerPool` rolls its pods to the `0.0.30-gs.1` worker image (the kagent chart's stamp), the kagent controller and UI move to `0.11.0-gs.14`, the agentgateway data plane to `v1.5.1-gs.4`. Every golden snapshot is retaken on the new worker image (a Harness-wide recompile: each admitted `AgentTemplate` boots once; the templates report `Ready=False ActorTemplatePending` for those seconds); an actor mid-turn on a rolled worker loses that turn.
- **A BOM** moves `kagent` and `kagent-crds` to `0.11.0-gs.14` and `substrate` and `substrate-crds` to `0.0.30-gs.1` in the same change (`examples/customer-bom.yaml`); the agentgateway data plane follows this chart, the controller the `giantswarm/agentgateway` packaging chart's release that pins `v1.5.1-gs.4`.
- **Recognising a half-moved installation** (one line's range moved, another's not): every turn ends in `actor "…" request timed out` or `request timed out` at the portal while the templates are `Ready=True`; `kubectl -n ate-system logs deploy/atenet-router` shows the router refusing or misrouting the connect (no `ate-target-actor` on a 0.0.27 router; on a 0.0.30 router, a kagent before gs.14 dials an authority the router no longer routes). The fix is the other range.

## \<current\> → \<next\> (Anthropic prompt caching on for the platform's ModelConfigs; the kagent line moves to `v0.11.0-gs.15`)

`components.kagent` / `components.kagent-crds` move to `>=0.11.0-gs.15 <0.11.1-0` — the release that carries kagent-dev/kagent#2788: `spec.anthropic.promptCaching` / `cacheTTL` on the ModelConfig CRD and the `cache_control` breakpoints in the Go ADK — and `kagent.providers.anthropic.config` sets `promptCaching: true`, `cacheTTL: "5m"`, which the kagent chart renders into the default ModelConfig and the connectivity chart's Anthropic `kagent.modelConfigs[]` entries inherit (giantswarm/giantswarm#37788).

### Operator action

- **None to install.** Flux resolves the new range on its next reconcile: the kagent controller rolls, the platform Harness moves to the release's Go ADK digest and **every admitted `AgentTemplate` recompiles** (a new golden snapshot each, about 20 s per template, the pool's workers busy meanwhile). The default ModelConfig and every Anthropic catalog entry then gain `promptCaching: true` / `cacheTTL: 5m`, which recompiles the templates that reference them once more. The order is the chart's: the CRDs and the controller move before the values reach the ModelConfigs (`components.kagent` `dependsOn` `kagent-crds`; the range floor), so an older CRD never prunes the fields, and the controller that compiles the cached configuration is the one that knows the knob — a template compiled by the previous controller sends no markers until something recompiles it.
- **The cost shape changes, in the bill's favour.** The first model call of a turn writes the cache (its input billed at 1.25×), every call after reads its prefix at 0.1× — a turn of more than one call is cheaper from the second call on, and a follow-up turn reads what the previous one wrote while the 5-minute window lasts (each hit refreshes it). A model whose prefix is below Anthropic's minimum cacheable length (1 024-4 096 tokens by model) is unaffected: the markers are ignored.
- **Opting out, per model or for all**: `promptCaching: false` on a `kagent.modelConfigs[]` entry; `kagent.providers.anthropic.config.promptCaching: false` for the default model and the inherited default. `cacheTTL: "1h"` for agents whose calls are more than five minutes apart (dearer writes, so only then).
- **A BOM** moves `kagent` and `kagent-crds` to `0.11.0-gs.15` (`examples/customer-bom.yaml`).
- **Recognising it worked**: with the LLM listener on, agentgateway's per-request log (`listener=llm`) shows `gen_ai.usage.cache_creation.input_tokens > 0` on a turn's first call and `gen_ai.usage.cache_read.input_tokens > 0` on the following ones; `kubectl -n kagent get modelconfig <name> -o jsonpath='{.spec.anthropic}'` shows both fields. An agent whose calls show neither after the roll was compiled by the previous controller: any change to its `AgentTemplate` or its ModelConfig recompiles it.

## \<current\> → \<next\> (`components.vm-manager` from gsoci and the giantswarm catalog; the guest image is a fetched artifact, no node paths)

vm-manager releases through the generated CircleCI pipeline from 0.20.x (giantswarm/vm-manager#49, #50, #51): the chart from `oci://gsoci.azurecr.io/charts/giantswarm` like model-manager's, the image from gsoci, the guest image as an OCI artifact. `components.vm-manager.repository` moves off ghcr.io and the range's floor to `0.20.2`, the first release whose three artifacts all exist. The chart takes no image directory from a claim or a node path any more (`vm-manager.images.*` is gone) and mounts no device hostPaths (`vm-manager.host.devices` is gone): the guest image is the OCI artifact `gsoci.azurecr.io/giantswarm/vm-manager-guest-image:<version>` every release publishes, fetched into the state volume by an init container at pod start (`vm-manager.guestImage`, the tag defaulting to the chart appVersion), and a privileged container has the node's devices from the runtime.

### Operator action

- **None** while the component is off (the fleet): the roster forwarded to the connectivity release is unchanged.
- **Component on** (a lab; an installation that turned it on with 4.11.x): the `vm-manager.images` and `vm-manager.host` keys are no longer in the chart's closed schema — an installation that set `images.hostPath`, `images.existingClaim` or `host.devices.*` removes them, or the release fails validation naming them. The OCIRepository re-resolves against gsoci and the pod rolls once; its init container fetches the guest image (about 1.4 GB) before the server starts, so the first start after the upgrade takes the pull. Turn on a state claim (`vm-manager.persistence.create: true` or `existingClaim`) with the component: it carries VM records and disks across restarts and keeps the fetched image and the golden PCR values `image golden` records into its `policy.json`; on an emptyDir every pod start fetches again and forgets the golden values. Golden values recorded into a checkout's `policy.json` before are recorded once more inside the pod after a learn-mode boot (`vm-manager.vm.learnGolden: true`): `kubectl -n <namespace> exec deploy/vm-manager -- vm-manager image golden <id>_<version> --from-vm <id> --token <id_token>`.
- The 0.19.x releases on ghcr.io stay where they are; nothing pins them after this release.

## \<current\> → \<next\> (muster-valkey gets a memory bound of its own: `maxmemory 640mb`, `volatile-lru`)

giantswarm/agent-platform#446: the valkey release ran Valkey without a `maxmemory`, so the kernel — not Valkey — bounded the store, and a store grown past the container limit crash-looped (gazelle 2026-09-14: 19 OOM kills in 90 minutes, the muster connector down meanwhile; the 1Gi limit below is the other half). `valkey.valkey.valkeyConfig` now sets `maxmemory 640mb` and `maxmemory-policy volatile-lru`.

### Operator action

- **None.** The valkey release re-renders the Deployment (the config checksum changes the pod template) and `muster-valkey` rolls once under its `Recreate` strategy: a few seconds without the store, during which muster's token refreshes wait; the RDB on the PVC carries every record across.
- **Watch after the roll**: `redis_memory_used_bytes / redis_memory_max_bytes` for the `muster-valkey` pod. Above 90 % Valkey evicts the least recently used TTL-carrying keys (a person's refresh token → one re-sign-in; a session's capability cache → one re-list on the next connect) instead of dying. A store that sits near the bound on a fresh muster is worth a look at what fills it (`valkey-cli --scan | cut -d: -f1,2 | sort | uniq -c`) before the limit is raised; on 2026-09-14 the filler was muster's per-session capability cache (giantswarm/muster#1217).
- **Own fragment**: an installation that sets `valkey.valkey.valkeyConfig` itself replaces the chart's fragment whole (a string is not merged) — carry `maxmemory` and the policy in it, and keep `maxmemory` at or under two thirds of `resources.limits.memory`. Never add `appendonly yes` to it (the persistence note in values.yaml: it wipes the live store on the enabling deploy).
## \<current\> → \<next\> (klaus-gateway's egress reaches its stores: the platform's Valkey and the Kubernetes API)

giantswarm/agent-platform#443: the connectivity chart renders a third network policy for the klaus-gateway pod, `-klausgateway-store-egress`, exactly while one of the gateway's stores or its controller reaches beyond the pod, one rule per store: the platform's Valkey pods on their Service port with `klausGateway.routing.store: valkey` (the valkey component on), the kube-apiserver (`networkPolicy.kubernetes.apiServerCIDR` in the kubernetes flavour) with `klausGateway.obo.store: secret` and OBO on, `klausGateway.routing.store: configmap` or `crd`, or `klausGateway.controller.enabled: true`; DNS as in the gateway's other two policies. Without it those two leave the pod in default-deny towards both stores: on 2026-09-14 the switch to the Secret link store crash-looped the gateway of a Cilium installation at start (`read secret …: context deadline exceeded`) until the values were reverted.

### Operator action

- **None** on the default shape (memory routing, the bolt link store, no controller): nothing new renders and the existing policies are unchanged.
- **Switching a store** — `klausGateway.obo.store: secret` (the move off the ReadWriteOnce volume, klaus-gateway's UPGRADE.md) or `klausGateway.routing.store: valkey` (this release's routing-store defaults, above) — is possible once the installation runs this release. The one values change reaches both releases — the connectivity release renders the policy, the klaus-gateway release rolls the pod — and the two reconcile independently: should the pod roll before the policy is applied, its first starts fail on the store (the Secret read, the Valkey connect) and the next restart succeeds once the policy is there (seconds to a minute). `kubectl -n agent-platform get cnp agent-platform-connectivity-klausgateway-store-egress -o yaml` shows the rules; `hubble observe --verdict DROPPED --pod agent-platform/<klaus-gateway pod>` shows no drop towards the store afterwards.
- **An out-of-band Valkey** (`klausGateway.routing.valkey.url` pointing outside the platform, `components.valkey.enabled: false`) gets no rule — nothing in the release namespace to select; the installation adds the egress to that Valkey itself (a policy next to the chart's, or `networkPolicy.additionalEgressCIDRs` if it listens on 443).
- The kubernetes flavour opens `networkPolicy.kubernetes.apiServerCIDR` (default `0.0.0.0/0`) for the pod on the API-reaching stores, as the other API-reaching policies of that flavour do; narrow it to the API server's address on such a cluster.

## \<current\> → \<next\> (the Postgres `Cluster` knobs, the Backstage app policy)

Two gaps found on a vanilla cluster with Cilium `policyEnforcementMode: always`, mixed architectures and a private image mirror (giantswarm/agent-platform#311). The platform Postgres `Cluster` takes `postgres.imagePullSecrets` and `postgres.affinity`, and the chart renders a network policy for the Backstage app pods in both flavours.

### Operator action

- **None on the fleet.** `components.backstage` is off there, so no Backstage policy renders; with `postgres.imagePullSecrets` and `postgres.affinity` unset the `Cluster` renders no new field and the whole render is byte-identical in either flavour.
- **Set `postgres.imagePullSecrets` when the CNPG images come through a private mirror.** The bootstrap init container runs the operator image, so the secret is needed even when `postgres.image.name` is left at the operator's default.
- **Set `postgres.affinity.nodeSelector` when the nodes have mixed architectures.** Use CNPG's `AffinityConfiguration` keys, not a core Kubernetes `Affinity`: any other key now fails the render, naming the key. The guard lives in the connectivity chart, and both schemas take the block as free-form, so on a GitOps installation a typo passes the meta chart's own install and surfaces as a failing `agent-platform-connectivity` `HelmRelease` — read the key's name from that release's message, not from the meta chart's.
- **Drop the hand-written portal policies when Backstage runs on a default-deny cluster.** Remove them after this release lands and confirm the portal still serves: the pod passes its probes, sign-in completes, the Agent Platform pages load, and the agent create flow reaches its deploy step.
- **A route pinned with `backstage.parentRefs` moves the policy's Gateway with it.** The policy's ingress admits the proxy pods of the namespaces the portal's route names as its parents (`backstage.parentRefs`, else `global.gatewayApi.parentRefs`), or the agentgateway data plane while `gatewayApi.gateway.create` makes this chart the edge and the route names no parent of its own. `backstage.parentRefs` wins over the chart-owned edge for the policy exactly as it does for the route. The egress leg to the edge follows the routes the portal *calls* instead (`ingress.parentRefs` for muster, `kagent.controllerRoute.parentRef` and `modelManager.route.parentRef`), so pinning the portal's own route does not move it. A Gateway whose proxy pods are not Envoy Gateway's (`app.kubernetes.io/name: envoy`) still needs a policy of its own.
- A private identity provider inside one of `networkPolicy.kubernetes.worldExcludedCIDRs` needs its address in `networkPolicy.additionalEgressCIDRs`; the kubernetes-flavour egress subtracts those blocks from its world rule.

## \<current\> → \<next\> (`components.vm-manager`: the platform's VM provisioner as a pod of a KVM node)

A new component, off by default: [vm-manager](https://github.com/giantswarm/vm-manager) — KVM virtual machines with an instance metadata service, a vTPM with measured boot, an immutable image and attestation, as REST and MCP (`x_vm-manager_<tool>` through its own `MCPServer` CR in the `agent-platform` tool group, next to agent-manager and model-manager). The chart comes from `oci://ghcr.io/giantswarm/vm-manager/helm` (the repository publishes from GitHub Actions to ghcr.io, like the kagent and Substrate lines), `>=0.19.0 <1.0.0`; the values block `vm-manager:` is forwarded to the release and `vmManager:` (a PodDisruptionBudget, network policy inputs) is read by the connectivity chart, which renders the pod's ingress (muster, the probes), egress (DNS, the identity provider, the guests' destinations — the VMs' traffic is userspace NAT out of the pod) and muster's egress to it. The `agent-platform` toolset preset's description names it.

### Operator action

- **None** unless the installation turns the component on. Everything renders inert while `components.vm-manager.enabled` is false; the roster forwarded to the connectivity release carries the new key (`components.vm-manager.enabled: false`), which a connectivity chart of this release accepts.
- **To turn it on**: a node with `/dev/kvm` and `/dev/vhost-vsock` (`vm-manager.nodeSelector` pins the pod there), the pod runs **privileged** (a hostPath device in an unprivileged container is denied by the device cgroup — a cluster whose admission forbids privileged pods in the platform namespace needs an exception for this Deployment), and an image directory: a claim filled with the output of `make -C images` in a vm-manager checkout (`vm-manager.images.existingClaim`) — without one `list_images` is empty and `create_vm` has nothing to boot. `vm-manager.persistence.create: true` (or `existingClaim`) keeps VM records and disks across pod restarts; the VMs themselves end with the pod (no service manager in it).
- **Network policies on**: the pod's egress admits `vmManager.networkPolicy.guestEgress.cidrs` (default `0.0.0.0/0`) on every port — the guests reach whatever the pod reaches; `except` carves out the node and pod networks where the guests must not reach them.

## \<current\> → \<next\> (the s3proxy façade declares its ephemeral storage and bounds its emptyDirs)

The `substrate-s3proxy` container (`kagent.harness.snapshotStore.s3proxy`, on with provider `capz` or `s3proxy.enabled`) carries `resources.requests.ephemeral-storage` (256Mi) and `resources.limits.ephemeral-storage` (1Gi), and its two emptyDirs (`/tmp`, `/data`) a `sizeLimit` equal to the limit (giantswarm/agent-platform#438). Kyverno's `require-emptydir-requests-and-limits` stops reporting the Deployment on every rollout, and a cluster that enforces the policy admits the façade.

### Operator action

- None: the connectivity release re-renders the Deployment and the façade's two pods roll once. The rolling update surges a new pod before it takes an old one down (two replicas, the default strategy) and a draining pod answers `/healthz` 503 for its 5 s preStop, so the Service keeps one ready replica throughout.
- An installation's own `s3proxy.resources` entries merge over the defaults, so both `ephemeral-storage` fields stay unless set to `null` — which the render refuses, naming the key (`kagent.harness.snapshotStore.s3proxy.resources.<requests|limits>.ephemeral-storage is required`). A bigger `limits.ephemeral-storage` moves both emptyDirs' `sizeLimit` with it.

## \<current\> → \<next\> (`muster-valkey` gets the memory a grown token store needs)

`valkey.valkey.resources` moves from `requests.memory: 64Mi` / `limits.memory: 256Mi` to **`256Mi` / `1Gi`**. The store holds every OAuth token, session and cached capability of every person and agent (encrypted at rest), and Valkey loads the whole RDB into memory on every start. On gazelle the RDB had grown to 247 MB (5237 keys) after three weeks; when 4.12.0 rolled the pod once (the new `karpenter.sh/do-not-disrupt` annotation), every start was OOM-killed at the 256Mi limit — `CrashLoopBackOff`, `Last State: OOMKilled (137)`, `RDB memory usage when created 246.66 Mb` in the container log — and muster's token endpoint was down until the limit was raised by hand (13:22–13:27 CEST, 2026-09-14). graveler and glean, with smaller stores, rolled cleanly.

### Operator action

- **None to take the new size**; the `muster-valkey` pod rolls once (a Recreate: the token store is gone for the seconds of the restart, every token refresh in flight blocks for them). An installation that raised the limit by hand (gazelle) is overwritten with the same values.
- **Check the store's size on your installation** before any planned restart of `muster-valkey`: `kubectl -n agent-platform logs deploy/muster-valkey -c muster-valkey | grep 'RDB memory usage'` — if it approaches the limit, raise `valkey.valkey.resources.limits.memory` (and the request) in the installation's values first. A `muster-valkey` pod in `CrashLoopBackOff` with `OOMKilled` after a roll is this; the recovery is `kubectl -n agent-platform set resources deploy/muster-valkey -c muster-valkey --limits=memory=1Gi` until the values catch up.
- The store's growth itself (about 47 KB per key on gazelle) is a muster / mcp-oauth question, not the chart's; the size here buys headroom, not a fix.

## \<current\> → \<next\> (the stateful singletons can be pinned to on-demand capacity; muster-valkey takes the #431 guards)

giantswarm/agent-platform#439: on gazelle, whose fifteen workers are Karpenter spot capacity, one spot-interruption wave (2026-09-14, three nodes in ten minutes) force-evicted `muster-valkey` — muster's OAuth token store, one replica on an RWO volume, with none of #431's guards — for two minutes, during which every token refresh blocked inside muster (a Slack turn waited 27 s before the bot reacted, another was aborted on the client's 30 s timeout); and killed `klaus-gateway` mid-turn on the same node, whose replacement then waited four minutes on a `Multi-Attach` error for its volume. The guards from #431 (`karpenter.sh/do-not-disrupt`, `PodDisruptionBudget minAvailable: 1`) address Karpenter's **voluntary** disruption — consolidation, drift, expiry. A spot reclaim is involuntary: Karpenter's `CordonAndDrain` on the interruption notice cannot evict a PDB-blocked pod, and the instance terminates two minutes after the notice regardless — for these pods the guards turn a graceful two-minute move into a hard kill with the chart's termination grace. Two changes:

- **`scheduling.singletons`** — a new meta-chart block, **empty by default** (no installation changes behaviour until it is set): `nodeSelector` is merged into, and `tolerations` appended to, the scheduling knobs of the four single-replica stateful pods — muster (`muster.nodeSelector`), muster-valkey (`valkey.valkey.nodeSelector`), the kagent controller (`kagent.controller.nodeSelector`) and klaus-gateway (`klausGateway.nodeSelector`) — before their releases render; a key a component sets itself wins. The block is the meta chart's alone and is held back from the connectivity release (`components.agent-platform-connectivity.omitKeys`).
- **muster-valkey takes the #431 guards**, on by default like the others: `karpenter.sh/do-not-disrupt: "true"` through the valkey subchart's `valkey.valkey.podAnnotations`, and a `PodDisruptionBudget muster-valkey` (`valkey.podDisruptionBudget`: `minAvailable: 1`, `unhealthyPodEvictionPolicy: AlwaysAllow`) rendered by the connectivity chart in the release namespace — neither the wrapper nor the upstream subchart has a budget knob. They still help against consolidation; they do not cover a spot reclaim either.

### Operator action

- **None to take the valkey guards.** The `muster-valkey` pod rolls **once** (a pod-template change; `Recreate`, so the token store is gone for the restart — every token refresh in flight blocks for those seconds, as on any rollout of it); the budget is a new object. Opt out per knob: `valkey.valkey.podAnnotations.karpenter.sh/do-not-disrupt: "false"`, `valkey.podDisruptionBudget.enabled: false`.
- **Decide the placement on installations whose workers are Karpenter spot capacity** — and only there:

  ```yaml
  scheduling:
    singletons:
      nodeSelector:
        karpenter.sh/capacity-type: on-demand
  ```

  Karpenter launches one small on-demand node for the four pods from a NodePool that admits `on-demand` in the volumes' zone (the fleet's NodePools admit `spot,on-demand` and carry no taint, so no toleration is needed; `scheduling.singletons.tolerations` is for a dedicated, tainted pool) and, with the guards, leaves it alone. **Cost: one `xlarge`-class on-demand instance per such installation** (the four pods request about one vCPU and a gigabyte together; Karpenter picks the smallest instance the NodePool admits that fits them and the daemonsets). Enabling **rolls each of the four pods once**; muster-valkey and klaus-gateway (`Recreate`) are down until the node is up — about two minutes on AWS — so pick a quiet moment; their RWO volumes move with them as long as the new node is in their zone (Karpenter honours the volumes' topology). Once the node exists the four land on it together: the do-not-disrupt annotation and the budgets keep consolidation away from it, and an on-demand instance is not reclaimed.
- **Do not set it on a cluster without Karpenter** (CAPZ, on-prem): no node carries `karpenter.sh/capacity-type`, and the four pods would stay `Pending`. An installation whose workers are already on-demand gains nothing from it and loses nothing either (the pods are pinned to the capacity they run on).
- **klaus-gateway needs chart 1.3.3 or later** (giantswarm/klaus-gateway#253): the earlier 1.x charts' generated schema declared `nodeSelector` and `affinity` with `additionalProperties: false`, so with the knob set the klaus-gateway release failed validation (`at '/nodeSelector': additional properties 'karpenter.sh/capacity-type' not allowed`). The meta chart's `components.klaus-gateway.versionRange: "1.x"` resolves 1.3.3; an installation that pins an older chart lifts the pin before setting the knob. The muster, valkey and kagent charts took the keys all along.
- **Know what remains:** a spot reclaim still restarts everything else on the node (agent workers restore from checkpoints, the edge proxy has two replicas); an on-demand node can still fail. The component-side answers — klaus-gateway surviving a restart mid-turn and moving its link store off the RWO volume, mcp-oauth's token endpoint failing fast while Valkey is down — are tracked in those repos and complement the placement rather than replace it.
- `make verify-disruption` asserts the merge (the four releases, a component's own keys first), the default (nothing forwarded), the hold-back from the connectivity release and the valkey guards.


## \<current\> → \<next\> (the Substrate line re-pins to `v0.0.27-gs.9`, the kagent line to `v0.11.0-gs.12`: a superseded template's crashed golden actor is collected and frees its worker)

`components.kagent` / `components.kagent-crds` move to `>=0.11.0-gs.12 <0.11.1-0` and `components.substrate` / `components.substrate-crds` to `>=0.0.27-gs.9 <0.0.28-0`. `v0.11.0-gs.12` stamps the Substrate worker image `0.0.27-gs.9` into the kagent chart's `substrateWorkerPool.workerImage`; `v0.0.27-gs.9` = `gs.8` + giantswarm/substrate#30 (the gVisor worker's terminate collects a workload whose runsc containers are already gone). Before it, an `AgentTemplate` changed while its previous revision's golden boot was crash-looping (a skill that fails to load, an image the registry refuses after the first pull) left the superseded revision uncollectable: the kagent controller logged `failed to collect runtime revision … delete unreferenced ActorTemplate kagent/<agent>-kagent-<rev>: … while running `runsc state`: exit status 128` every minute per template, the golden actor stayed `ACTOR_STATE_DELETING` with its worker assignment intact, and **one worker of the pool was pinned per such template** (gazelle 2026-09-13: two of four workers; giantswarm/giantswarm#37773). The new revision itself was Ready and unaffected.

### Operator action

- **None to install.** The Substrate control plane moves within its range on its own; the `WorkerPool` rolls its pods once when the kagent release lands (`kagent.substrateWorkerPool.workerImage` follows the chart's stamp) — a worker roll suspends nothing that is not already suspended, but an actor mid-turn on a rolled worker loses that turn.
- **A pinned worker is freed by the roll itself**: Substrate's delete workflow treats an actor whose worker pod is gone as terminated, so the stuck templates of an installation on 4.10.x are collected within a minute of the roll without any hand-work. A BOM pins `kagent`/`kagent-crds` to `0.11.0-gs.12` and `substrate`/`substrate-crds` to `0.0.27-gs.9` or later (`examples/customer-bom.yaml`).
- **Recognising the condition on an installation that has not rolled yet** (or on any Substrate release, for a golden actor stuck for another reason): the controller log above, and through ate-api-server with `kubectl-ate` (`go install ./cmd/kubectl-ate` from giantswarm/substrate; it port-forwards to `ate-api-server` in `ate-system` and authenticates with a ServiceAccount token; `--context <installation>`):

  ```sh
  kubectl ate get actors -a ate-golden      # STATE ACTOR_STATE_DELETING (or CRASHED) with an ATEOM POD set = a golden pinning a worker
  kubectl ate get workers -n kagent         # ASSIGNED(1/1) on the pool's workers that host them
  ```

  A healthy superseded golden is `ACTOR_STATE_SUSPENDED` with no worker; the kagent controller collects it on its next sweep.
- **Removing a stuck template by hand** (`v0.0.27-gs.8` and earlier): the delete cannot succeed while the worker pod that hosted the crashed golden is alive — the same `runsc state` failure answers `kubectl ate delete actor <name> -a ate-golden`. Delete that worker pod (`kubectl -n kagent delete pod <ATEOM POD>`; the `WorkerPool` replaces it): the next controller sweep, within a minute, deletes the golden actor (worker gone = terminated), the `ActorTemplate` and the runtime revision, the log line stops and the replacement worker registers `FREE`. Nothing is edited in Substrate's store. With `0.0.27-gs.9` workers the sweep succeeds on the live worker and this recipe is not needed.

## \<current\> → \<next\> (the s3proxy façade on CAPZ authenticates with Workload Identity)

The `capz` branch of the façade (`kagent.harness.snapshotStore.crossplane.provider: capz`) sets `JCLOUDS_CREDENTIAL` to the empty string explicitly (giantswarm/agent-platform#436). Before, the variable was left unset and the image's own default (`remote-credential`, a Dockerfile `ENV` of `gaul/s3proxy`) filled it: s3proxy then signed as the account with that literal as the shared key instead of deferring to `DefaultAzureCredential`, and every snapshot request failed on the base64 decode — on glean every golden boot crashed before its snapshot was taken. The account-key branch (`s3proxy.azure.accountKeySecretRef`) is unchanged.

### Operator action

- None: the connectivity release re-renders the `substrate-s3proxy` Deployment on the upgrade and the façade authenticates with the identity the chart federated. An installation that bridged the defect by patching the Deployment by hand (`JCLOUDS_CREDENTIAL=""`) is overwritten with the same value.
- An installation on 4.10.0–4.10.3 with provider `capz` whose `AgentTemplate`s read `ActorTemplateRetrying: golden boot N of 6 failed (GoldenActorCrashed …)` while the façade logs `'base64Key' was not a valid Base64 scheme`: this is the cause; once the release is on the cluster the next boot succeeds (a template that exhausted its six boots — `ActorTemplateFailed … golden boots crashed` — is re-tried by a change to the template, an annotation suffices).

## \<current\> → \<next\> (the chart provisions Agent Substrate's snapshot store on CAPZ: Crossplane storage account + Workload Identity behind an s3proxy façade)

`kagent.harness.snapshotStore.crossplane.provider: capz` renders the Azure half of the store the CAPA one got in the previous entry: the connectivity chart renders the storage `Account` (TLS-only, no public blobs, soft delete; never deleted by Crossplane, kept by Helm), the blob `Container` with a lifecycle `ManagementPolicy` (`capz.lifecycleDays`, 30), a `UserAssignedIdentity` federated for the s3proxy ServiceAccount with Storage Blob Data Contributor on the container (provider-kubernetes bridges the generated ids), and the **s3proxy façade** — Substrate speaks S3 only, so a stateless `substrate-s3proxy` Deployment in the release namespace translates to Azure Blob as that identity. The meta chart derives `kagent.harness.snapshotLocation` = `s3://<capz.containerName>/<prefix>` and hands the substrate release the façade's S3 environment on `atelet.extraEnv` and `ateApiServer.extraEnv` (README "Agent Substrate: the snapshot store").

### Operator action

- None by default: the block is off; `provider: aws` and an installation that names its store by hand render exactly as before.
- To provision the store on CAPZ: set `kagent.harness.snapshotStore.crossplane` (`enabled: true`, `provider: capz`, `providerConfigRef`, `region`, `capz.storageAccountName` — 3 to 24 lowercase letters and digits, globally unique — `capz.containerName`, `capz.resourceGroup`, `capz.subscriptionId`, `capz.workloadIdentity.oidcIssuerUrl` — the cluster's service-account issuer — and `capz.workloadIdentity.providerKubernetes.providerConfigRef`; `providerKubernetes.serviceAccount.name` when the chart is to grant the provider the RoleAssignment rights) and **drop** `kagent.harness.snapshotLocation`. **Hard prerequisites on the cluster**: the upbound Azure provider, provider-kubernetes, and the azure-workload-identity **mutating webhook** — the façade's pods carry `azure.workload.identity/use: "true"` and rely on the webhook to inject `AZURE_FEDERATED_TOKEN_FILE`, `AZURE_AUTHORITY_HOST` and the projected token that `DefaultAzureCredential` exchanges (the Loki chart's shape, fleet-wide); without the webhook the façade cannot authenticate and every snapshot request fails. The storage account is **network-reachable**: no network rules and no private endpoint are rendered, so its endpoint answers the internet and the protection is Entra ID / RBAC (the identity's container-scoped role) — "no public blob access" only closes anonymous reads. Turn `substrate.rustfs.enabled` off and leave the S3 variables out of `substrate.*.extraEnv` — a derived name in there fails the render.
- The façade's image is the gsoci mirror `gsoci.azurecr.io/giantswarm/s3proxy:4.1.1` (`kagent.harness.snapshotStore.s3proxy.image`, pinned in `examples/customer-bom.yaml`; giantswarm/retagger mirrors gaul/s3proxy for amd64 and arm64).
- An Azure Blob account provisioned by hand takes the façade alone: `kagent.harness.snapshotStore.s3proxy.enabled: true` with `s3proxy.azure.{endpoint,account,container}` and an account key in `s3proxy.azure.accountKeySecretRef` (a Secret in the release namespace).
## \<current\> → \<next\> (the platform Harness's selector survives the patch of a pre-existing kagent release; the kagent line re-pins to `v0.11.0-gs.9`)

The platform Harness `kagent` admits templates by `agent-platform.giantswarm.io/harness: kagent` alone. Through 4.9.3 the meta chart removed the kagent chart's own default selector key `kagent.dev/harness` with a null in the kagent `HelmRelease`'s values; a null deletes the key on coalesce, but an installation upgraded from 3.x has that `HelmRelease` already and the upgrade patches it, and a JSON merge patch removes a null key instead of storing it — the chart default came back and the Harness required both labels, so no `AgentTemplate` rendered by the Generic agent chart 1.x was admitted (giantswarm/agent-platform#418). The meta chart now forwards `kagent.dev/harness: ""`, and the line's Harness template (`0.11.0-gs.9`+) drops a selector label whose value is empty. `components.kagent`/`kagent-crds` move to `>=0.11.0-gs.9 <0.11.1-0`.

### Operator action

- Nothing for the upgrade itself: the kagent release's values change, helm-controller upgrades the release and the Harness's selector becomes the platform label alone within one reconcile — on a fresh 4.x installation and on one upgraded from 3.x alike. `kubectl -n kagent get harness kagent -o jsonpath='{.spec.allowedAgentTemplates}'` shows one `matchLabels` key.
- An agent release that carried the interim workaround — `labels: {kagent.dev/harness: kagent}` in the Generic agent chart's values, stamping the second label on the template — can drop it; it is harmless while it stays.
- A BOM pins `kagent`/`kagent-crds` to `0.11.0-gs.9` or later (`examples/customer-bom.yaml`).

## \<current\> → \<next\> (kagent's built-in tool server lives in the kagent namespace; the egress rules to it follow the kagent chart)

`kagent.kagent-tools.namespaceOverride` defaults to `kagent` (giantswarm/agent-platform#421): the kagent chart renders the kagent-tools subchart into that key, else the kagent release's namespace (`agent-platform` on the fleet), and composes the `kagent-tool-server` RemoteMCPServer URL from it; the connectivity chart's egress rules to the server (the controller's discovery, the actors' calls through Substrate's egress gateway) now derive their namespace the same way instead of assuming the kagent namespace.

### Operator action

- None with the tool server off (the default; every installation but graveler).
- With `kagent.kagent-tools.enabled: true`: the tools Deployment, both Services, the ServiceAccount and the ClusterRoleBinding subject move from the release namespace to `kagent` on the kagent release's next reconcile (Helm delete + create; the RemoteMCPServer's URL follows and the connectivity rules match it). No agent of the platform binds this server, so nothing else rolls. An installation that wants the server elsewhere sets `kagent.kagent-tools.namespaceOverride` to that namespace — never `kagent.namespaceOverride`, which moves the controller only.

## \<current\> → \<next\> (the chart provisions Agent Substrate's snapshot store on CAPA: Crossplane S3 bucket + IRSA role, the location derived)

`kagent.harness.snapshotStore.crossplane` renders the Substrate snapshot store the way `postgres.backup.crossplane` renders the kagent-pg backup bucket: the connectivity chart renders the S3 `Bucket` (lifecycle expiration `aws.lifecycleDays`, 30 days by default — an expired snapshot costs one cold start from the golden image, nothing else; public-access block; TLS-only policy; never deleted by Crossplane, kept by Helm) and the IAM `Role` trusted by Substrate's `atelet` and `ate-api-server` ServiceAccounts in `ate-system` through the installation's IRSA OIDC provider, with a list/get/put/delete policy on the bucket. The meta chart derives `kagent.harness.snapshotLocation` = `s3://<aws.bucketName>/<prefix>` for the kagent release and forwards the role's ARN as `eks.amazonaws.com/role-arn` on the substrate release's `atelet.serviceAccount.annotations` and `ateApiServer.serviceAccount.annotations` (README "Agent Substrate: the snapshot store"). `components.substrate` / `components.substrate-crds` move to `>=0.0.27-gs.8 <0.0.28-0`, the Substrate release that carries the two annotation keys and `ateApiServer.extraEnv`.

### Operator action

- None by default: the block is off; an installation that names `kagent.harness.snapshotLocation` by hand renders exactly as before (the new default keys reach the connectivity release, whose `kagent` block is open).
- To let the chart provision the store on CAPA: set `kagent.harness.snapshotStore.crossplane` (`enabled: true`, `providerConfigRef`, `region`, `aws.bucketName`, `aws.accountId` — quoted, a string — `aws.oidcProvider`; `prefix` `kagent` by default) and **drop** `kagent.harness.snapshotLocation` — an explicit value that differs from `s3://<bucketName>/<prefix>` fails the render naming both keys. The AWS account's Crossplane policy must admit the bucket name. A bucket that already exists is adopted (`observeOnly: true` to adopt without changes).
- Do not set `eks.amazonaws.com/role-arn` on `substrate.atelet.serviceAccount.annotations` / `substrate.ateApiServer.serviceAccount.annotations` yourself while the block is on — the derived one wins and a differing one fails the render; other annotations of yours stay.
- A BOM pins `substrate`/`substrate-crds` to `0.0.27-gs.8` or later (`examples/customer-bom.yaml`).

## \<current\> → \<next\> (the muster release detects and corrects drift: a CiliumNetworkPolicy edited away from its manifest comes back on the next reconcile)

The muster HelmRelease carries `spec.driftDetection.mode: enabled` (`components.muster.driftDetection`), the second release to carry it after kagent. On every reconcile of the release — `gitops.interval`, 10 minutes by default — helm-controller compares the manifest in the Helm storage with the cluster and re-applies what differs or is missing, as a server-side apply, without a Helm revision. The reason is the muster chart's own `CiliumNetworkPolicy` `muster`, whose egress selectors name the release namespace: on a management cluster that carried the platform through the `agentic-platform` → `agent-platform` rename the live object kept the old namespace while the release manifest named the new one. Helm's three-way merge patches only what changed **between two release manifests**, so a drifted live object stays drifted across every upgrade — muster's egress to Valkey stayed denied (`Policy denied DROPPED` in Hubble), its OAuth server never started, and `/.well-known/oauth-protected-resource` answered 503 for hours after a routine pod restart (giantswarm/agent-platform#287). Now the policy is back on its manifest within one interval.

### Operator action

- None. A cluster whose live muster objects drifted is corrected on the first reconcile after the upgrade; check it once with `kubectl -n <gitops.targetNamespace, the release namespace by default> get ciliumnetworkpolicy muster -o yaml` if the cluster came through the rename.
- A hand edit to an object of the muster release (`kubectl edit` on the Deployment, the Service, the policy, …) is reverted on the next reconcile; make the change in the values. An object of the chart opts out with the annotation `helm.toolkit.fluxcd.io/driftDetection: disabled`.
- **`muster.autoscaling.enabled: true`**: add an ignore rule before you turn the HPA on, or the release and the HPA overwrite each other's `spec.replicas` on every interval — the muster chart renders `spec.replicas` whether or not the HPA is on. Set `components.muster.driftDetection.ignore` to `[{paths: ["/spec/replicas"], target: {kind: Deployment, name: muster}}]`. The fleet leaves the HPA off, so nothing needs the rule today.
- `components.<name>.driftDetection` is available for every component and set for kagent and muster only; turning it on for another release is a decision per release (its objects must tolerate the re-apply).

## \<current\> → \<next\> (the kagent release detects and corrects drift: a deleted platform Harness comes back on the next reconcile)

The kagent HelmRelease carries `spec.driftDetection.mode: enabled` (`components.kagent.driftDetection`; the key is a passthrough every component entry takes, set for kagent only). On every reconcile of the release — `gitops.interval`, 10 minutes by default — helm-controller compares the manifest in the Helm storage with the cluster and re-applies what differs or is missing, as a server-side apply, without a Helm revision. The reason is the platform `Harness` `kagent`, the runtime of every agent: a consumer whose exactly pinned connectivity chart skipped 4.7.19's `keep` lost it on the 4.8.0 upgrade, and a kagent release without drift detection never recreated it — a plain reconcile of an unchanged release reports "in-sync" (600 s observed, every template unadmitted; giantswarm/agent-platform#409). Now it is back within one interval and the templates it admits are Ready again ~25 s later; the ATS smoke deletes it on every PR and sees it back.

### Operator action

- None. The forced reconcile the 4.8.0 note below names is not needed from this release on.
- A hand edit to an object of the kagent release (`kubectl edit` on the controller Deployment, the WorkerPool, the Harness, …) is reverted on the next reconcile; make the change in the values. An object of the chart opts out with the annotation `helm.toolkit.fluxcd.io/driftDetection: disabled`; a field another controller owns is ignored through `components.kagent.driftDetection.ignore` (JSON Pointer paths, an optional target selector). Neither is needed for the kagent chart today: nothing of the platform edits its objects in place.
- `components.<name>.driftDetection` is available for every component; turning it on for another release is a decision per release (its objects must tolerate the re-apply).

## 4.7.x → 4.8.0 (the kagent chart names its own images; the platform Harness is the kagent release's)

The kagent line stamps its image references into the chart at publish (`0.11.0-gs.6`+: `tag`, `controller.agentImage.digest`, `substrateWorkerPool.workerImage`, a ConfigMap `kagent-images` of the runtime digests for atelet's image cache). The meta chart stops naming a build: `components.kagent`/`kagent-crds` move to `>=0.11.0-gs.6 <0.11.1-0`, `components.substrate`/`substrate-crds` to `>=0.0.27-gs.7 <0.0.28-0` (the Substrate version the gs.6 chart's worker image names). `kagent.tag` is **removed**; `kagent.substrateWorkerPool.workerImage` and `kagent.harness.image` default to empty and reach the kagent release only when set. The platform `Harness` `kagent` is rendered by the kagent chart (`kagent.harness.create: true`, forwarded with the GS policy: snapshot location, `KAGENT_PROPAGATE_TOKEN`, the admission label), no longer by the connectivity chart. The `substrate` release reads `atelet.imageCache.pinnedImages` from the kagent release's ConfigMap `kagent-images` (`valuesFrom`, optional) — this supersedes 4.7.x's derivation of the pinned set from `kagent.harness.image`; `substrate.atelet.imageCache.pinnedImages` is forwarded only when set and then **replaces** the ConfigMap's list (Flux lets `spec.values` win).

### Operator action

- **In order, via 4.7.19** (the default path): nothing. 4.7.19 put `helm.sh/resource-policy: keep` on the connectivity chart's Harness; on the 4.8.0 upgrade the connectivity release drops the template and Helm leaves the object, the kagent release (which `dependsOn` connectivity) renders the same object and helm-controller adopts it — its take-ownership default, which the meta chart never disables — so the Harness changes owner in place and no agent under it loses its runtime. The kagent release's Harness carries `keep` itself from then on.
- **Skipping 4.7.19** (from 4.7.18 or earlier straight to 4.8.0): the connectivity upgrade deletes the Harness (no `keep` in the previous manifest). On 4.8.0 itself the kagent release does **not** recreate it on its own: its chart and values are unchanged, so a plain reconcile reports the release in-sync and runs no upgrade (giantswarm/agent-platform#409), and every template stays unadmitted until a forced reconcile — `kubectl -n <gitops.namespace, the release namespace by default> annotate helmrelease kagent reconcile.fluxcd.io/requestedAt=$(date -u +%Y-%m-%dT%H:%M:%SZ) reconcile.fluxcd.io/forceAt=$(date -u +%Y-%m-%dT%H:%M:%SZ) --overwrite` (the Harness is back in a second, the templates Ready ~25 s later; snapshots stay in the store) — or the next kagent values or chart change. From the release above on, the kagent release detects and corrects drift and recreates it within one `gitops.interval`. Upgrade through 4.7.19 to avoid the gap altogether.
- Drop `kagent.tag` from your values (the schema still accepts the key under `kagent`, but a set tag overrides the chart's stamp for every release the range admits). Drop `kagent.harness.image` unless you run a locally built runtime image, and `kagent.substrateWorkerPool.workerImage` unless you deliberately run a different worker; both travel verbatim when set.
- A BOM pins `kagent`/`kagent-crds` to `0.11.0-gs.6` or later and `substrate`/`substrate-crds` to `0.0.27-gs.7` (`examples/customer-bom.yaml`); nothing else names a build any more.
- `substrate.atelet.imageCache.pinnedImages`: leave it empty unless you pin extra images — a set list replaces the runtime images the ConfigMap pins (repeat them in your list if you need both).

## \<current\> → \<next\> (the Substrate line re-pins to `v0.0.27-gs.4`: the `ate.dev` CRDs are kept on uninstall; a golden boot whose image cannot be pulled fails with the reason)

`components.substrate-crds` and `components.substrate` move to `>=0.0.27-gs.4 <0.0.28-0` and `kagent.substrateWorkerPool.workerImage` to `ghcr.io/giantswarm/substrate/ateom-gvisor:0.0.27-gs.4` (`v0.0.27-gs.4` = `gs.3` + giantswarm/substrate#14: an actor whose image the registry refuses is crashed with the registry's answer and its template's golden boot fails as `ActorTemplateFailed` with the cause, instead of pending forever with a worker pinned) — `gs.3` is the line's third release of upstream 0.0.26, `v0.0.27-gs.2` plus one carried patch (giantswarm/substrate#12): the `substrate-crds` chart's three CRD templates (`workerpools`, `sandboxconfigs`, `csidriverconfigs.ate.dev`) carry `helm.sh/resource-policy: keep`, the convention the kagent line's `kagent-crds` already follows (giantswarm/kagent-upstream#10). Without it, uninstalling the platform on a cluster that runs its own Flux raced the CRD charts: helm-controller finalizes the platform `HelmRelease`s concurrently, `substrate-crds` could go before `substrate`, whose uninstall then failed for good on its `SandboxConfig` and left the meta `HelmRelease` stuck in deletion (giantswarm/agent-platform#385).

### Operator action

- **None to install.** The pins move together (`make verify-components` holds the two ranges, the BOM pin and the worker-image tag to one Substrate version); the release is inert behind the fleet's `<4.0.0` bound like every 4.x release before the cut-over.
- **Uninstall semantics change on Substrate.** Uninstalling the `substrate-crds` release now leaves the three `ate.dev` CRDs in place, as `kagent-crds` leaves the `kagent.dev` ones; their objects go with the releases that own them (the `substrate` release's `SandboxConfig gvisor-default`, the kagent release's `WorkerPool`), and a `WorkerPool` or `SandboxConfig` created by hand survives a Substrate uninstall until deleted explicitly. To remove the CRDs, `kubectl delete crd workerpools.ate.dev sandboxconfigs.ate.dev csidriverconfigs.ate.dev` — it deletes the objects behind them. A reinstall adopts the kept CRDs (the same release name and storage namespace, `ate-system`).
- **On a cluster that runs its own Flux the uninstall order is now the operator's only concern for the agents**: delete the agents' `HelmRelease`s first, while the kagent controller still runs, then the chart's `HelmRelease`; that Flux finalizes the component releases in any order and every one goes clean (README "Clusters that run Flux"). On an installation still on `v0.0.27-gs.2` or earlier, delete the component `HelmRelease`s in reverse dependency order by hand first (`kagent`, `substrate`, then the CRD charts).
- **The bundled engine's ordered teardown is unchanged**: `helm uninstall` still deletes the platform `HelmRelease`s in reverse dependency waves; it no longer depends on that order for Substrate.
## \<current\> → \<next\> (a reinstall after `helm uninstall` keeps Agent Substrate's trust chain)

The `substrate` release no longer deletes `podcertificate-controller-system` on uninstall — the Substrate line's chart keeps the namespace and the two CA pools in it (`helm.sh/resource-policy: keep`, 0.0.27-gs.5, the pin of this release) — and the connectivity bootstrap hook publishes the podcert signers' `ClusterTrustBundle`s from the pools before the Substrate pods start and on every upgrade (giantswarm/agent-platform#384).

### Operator action

- **None on a running installation.** The upgrade re-runs the bootstrap hook: the pools are `present`, the two bundles come out `present` (their content is what the podcertificate-controller already published); the hook's ClusterRole gains `clustertrustbundles` (get, list, watch, create, patch) and `attest` on the two podcert signers.
- **A reinstall on the same cluster works as is**, with what an uninstall now leaves behind: the `ate-system` pools, ate-api-server's authentication config and the bundled Postgres's claim; the `podcertificate-controller-system` namespace with its two CA pools; the two `podcert.ate.dev` `ClusterTrustBundle`s. Nothing has to be removed first (README "Uninstalling").
- **A clean slate** (new roots, a fresh Substrate) is `kubectl delete namespace ate-system podcertificate-controller-system` plus the two `podcert.ate.dev` `ClusterTrustBundle`s, then the install. Removing the namespaces alone is safe too: the hook republishes the bundles from the new pools before any Substrate pod starts — before this release that shape left every Substrate client failing `x509: certificate signed by unknown authority` against ate-api-server until restarted.

## \<current\> → \<next\> (the agentgateway controller reaches an external JWKS host)

The agentgateway controller's network policy opens egress to every external JWKS host the `jwtAuthentication` routes name (giantswarm/agent-platform#312), in both network-policy flavours. The controller fetches each policy's JWKS and pushes the keys to the data plane over xDS, so this is the controller's egress, not the data plane's; the data-plane policies do not change.

New keys: `gateway.jwksEgress.external.fqdns`, `.cidrs` and `.port` (443), for an issuer the routes do not name and for one reached by address — `fqdns` as Cilium FQDN selectors in the cilium flavour, `cidrs` as IP blocks in both.

### Operator action

- **None on an installation whose JWKS hosts are in-cluster.** The whole render is byte-identical to before, in both flavours, for the fleet render and the lab render. `make verify-wiring` asserts that against `origin/main`.
- **An installation with an external issuer can drop its hand-written controller policy.** Name the issuer in the route's `jwtAuthentication.jwks` (host and port) and the chart's controller policy opens it: a `toFQDNs` `matchName` in the cilium flavour, an address block minus `networkPolicy.kubernetes.worldExcludedCIDRs` in the kubernetes flavour. Remove the hand-written `CiliumNetworkPolicy` after the release lands and confirm a request with a valid token is still accepted through the JWT policy.
- **A route on port 443 now originates TLS to the issuer without `jwks.tls.enabled`.** The port serves no plain HTTP, so the key is implied. An installation that set it stays as it is, and an in-cluster Dex on 5556 keeps its plain-HTTP fetch. Before this release, port 443 without the key left the fetch on plain HTTP and every caller read `401 token uses the unknown key`.
- **TLS implied by the port verifies against the system trust, not `global.identity.ca.secretName`.** That key is the CA of the platform's own identity provider, so it is the right default only for a route `jwks.tls.enabled` points at that provider deliberately, and it stays the default there. A public issuer on 443 is verified against the system trust, because a private CA signed no public certificate and pinning it would fail the fetch with the same `401`. A route that needs its own CA names it in `jwks.tls.caSecretName`, which wins in both cases.
- **A `jwks.host` is a hostname, and seven shapes now fail the render**, each of them a green render and that same `401` before. The message names the value to set.
  - An empty host. The JWKS backend renders no host and resolves nothing.
  - An empty `jwks.port`. The backend renders no port and the API server refuses it.
  - A host that carries its port (`dex.example.com:5556`, `[2001:db8::1]:443`). Split it into `jwks.host` and `jwks.port`.
  - An address literal of either family (`198.51.100.7`, `2001:db8::1`, `1.2.3.999`). The controller selects an external issuer by name. To reach one by address, set `jwks.host` to its name and name the blocks in `gateway.jwksEgress.external.cidrs`.
  - A host that is no hostname: a scheme or the JWKS path glued on (`accounts.google.com/keys`), an empty label (`a..b.example.com`), or a label that starts or ends with a hyphen (`keys-.example.com`). The host carries the issuer's name alone; its scheme belongs to `issuer` and the JWKS path to `jwks.path`. The kubernetes flavour opened its wide rule for such a host and the render stayed green, so the fetch failed with nothing to show for it.
  - A host of fewer than three labels (`dex`, `dex.giantswarm`, `okta.com`). It is neither a qualified Service name nor a public issuer, and a short Service name resolves through the pod's search path, which the controller's egress rule cannot follow. Write `<service>.<namespace>.svc.cluster.local` for an in-cluster issuer, or the full host for a public one. This guard follows `networkPolicy.enabled`.
- **`gateway.jwksEgress.enabled` is no longer demanded for an external `jwks.host`.** The render guards asked for it on any JWT policy; they now ask only when the host is in-cluster (`svc` as its third dot-separated label, with nothing, `cluster` or `cluster.local` after it), and only under `networkPolicy.enabled`. A public host that carries an `svc` label elsewhere (`a.b.svc.example.com`) counts as external and gets its own egress rule. An installation that set the key on to satisfy the old guard while pointing the route at an external issuer can turn it off: the in-cluster rule it renders reaches nothing. Leaving it on is harmless.
- **A `jwks.host` is classified in its normalized form: lower case, root dot removed.** `dex.giantswarm.svc.cluster.local.` and `DEX.giantswarm.svc.cluster.local` are the Service they name, and neither renders an external rule. An installation that wrote either form gains nothing to do; before this release the trailing-dot form rendered a `toFQDNs` selector in the cilium flavour and a wide address block in the kubernetes one.
- **In the kubernetes flavour the wide rule is IPv4 only.** A named host renders `0.0.0.0/0` minus `worldExcludedCIDRs`, so an issuer the cluster resolves to an IPv6 address, or one inside a private block, is not reached. Name its blocks in `gateway.jwksEgress.external.cidrs`, which takes either family in both flavours. That rule narrows anything only while `networkPolicy.kubernetes.apiServerCIDR` is a real API-server block: its default is `0.0.0.0/0` on every port, and the policy's first rule carries it.
- **A second issuer the routes do not name goes in `gateway.jwksEgress.external`.** The cilium flavour selects it by name in `external.fqdns` (`- matchName: keys.example.com`, or a `matchPattern`), the kubernetes flavour by address in `external.cidrs`; both open `external.port`. An `external.fqdns` item is a selector object, never a bare string: the schema refuses a string list, which the Cilium CRD would refuse at apply. A cilium installation that carried such an issuer in a hand-written policy can move it here.
- **Under `networkPolicy.enabled`, an in-cluster `jwks.host` must sit in `gateway.jwksEgress.namespace` and answer on its `port`.** That key renders one rule, for one namespace on one port, so a host elsewhere reached nothing while the render stayed green. The render now fails and names the value to set. An installation whose issuer is `dex.giantswarm.svc.cluster.local` on 5556, the default, is unaffected. One rule means one in-cluster issuer across every route: two routes that name in-cluster hosts in different namespaces cannot both be satisfied. Reach the second by address, with its pod blocks in `gateway.jwksEgress.external.cidrs` opened on `external.port`; with that port equal to the route's `jwks.port` the two guards stand down for that route. `gateway.jwksEgress.podSelector` is outside both guards — a hostname carries no pod labels, so a selector that matches no issuer pod still renders green and still denies the fetch.

## 3.x → 4.0 (the kagent line: kagent API v2)

4.0 replaces the kagent the platform runs. `components.kagent` and the new `components.kagent-crds` deliver **kagent API v2** — `kagent.dev/v1alpha3`: `AgentTemplate`, `Harness`, `ModelConfig`, `ModelProviderConfig`, `RemoteMCPServer`; agents run as Agent Substrate actors in gVisor worker pods — from the kagent line [giantswarm/kagent-upstream](https://github.com/giantswarm/kagent-upstream) (`oci://ghcr.io/giantswarm/kagent/helm`, releases `0.11.0-gs.N`) instead of the `giantswarm/kagent` 0.x wrapper (kagent 0.10, `v1alpha2`) from gsoci. The 4.x line of this chart is the kagent API v2 migration (giantswarm/giantswarm#37705); this release is its first step — the roster and the values — and the connectivity chart's v1alpha3 templates, the platform Harness, Substrate inside the chart and the cut-over of an installation's agents follow in the 4.0.x releases.

### What changes

- `components.kagent`: `oci://ghcr.io/giantswarm/kagent/helm`, range `>=0.11.0-gs.1 <0.11.1-0`, `dependsOn: [kagent-crds]`, no `crds:` policy. New `components.kagent-crds` (same source and range; the CRDs as templates with `helm.sh/resource-policy: keep`; follows `components.kagent` unless switched explicitly — an explicit `enabled: false` with kagent on fails the render). The connectivity release `dependsOn` both.
- `kagent:`: `registry: ghcr.io`, `tag` (one build of the line; upstream's chart falls back to `.Chart.Version`, invalid under helm-controller), the image names `giantswarm/kagent/{controller,golang-adk,ui}`, `controller.substrate.*`, `substrateWorkerPool`, `controller.metrics.enabled: false`, pgvector on the bundled Postgres, the new `kagent.harness.image` (the Go ADK image by digest). Removed: the ten `kagent.<example-agent>` blocks, `kagent.controller.skillsInitImage`, the `METRICS_BIND_ADDRESS` / `METRICS_SECURE` env entries. `kagent.serviceMonitor.enabled` defaults to `false` in both charts.
- Sibling ranges: `agent-platform-connectivity` `>=4.0.0 <5.0.0`, `agent-manager` `1.x`, `backstage` `>=1.0.0 <3.0.0`, `klaus-gateway` `1.x`, `model-manager` `>=0.20.0 <1.0.0`; `agent-manager.agentChart.semver` `1.x`; no `semverFilter` by default.
- **Agent Substrate ships inside the chart**: the new roster entries `components.substrate-crds` and `components.substrate` (the Giant Swarm Substrate line, `oci://ghcr.io/giantswarm/substrate/helm`, range `>=0.0.27-gs.4 <0.0.28-0`, its floor the tag of `kagent.substrateWorkerPool.workerImage`; both follow `components.kagent`; both land in `ate-system` through the new roster key `components.<name>.targetNamespace`), the `substrate:` values block, the connectivity release's `pre-install,pre-upgrade` bootstrap hook (the CA/JWT pools, the trust anchor, ate-api-server's authentication config — minted once, kept for ever), the CNPG `Database` `postgres.databases.substrate` with the derived connection Secret `<clusterName>-substrate-app` (the `post-install,post-upgrade` hook `<release>-postgres-databases`), the Kyverno `PolicyException`s `substrate-atelet`, `substrate-workers`, `substrate-control-plane`, `substrate-podcertificate-controller`, the network policies of Substrate's hops in both flavours, and the new required value `kagent.harness.snapshotLocation`. README "Agent Substrate"; [docs/substrate-security.md](./docs/substrate-security.md).
- **The install order**: `substrate-crds`, `kagent-crds` → `agent-platform-connectivity` → `substrate` → `kagent` → the managers. The connectivity release `dependsOn` the two CRD components (and muster, agentgateway, cloudnative-pg, kserve-resources), **no longer `kagent`**; `kagent` and `substrate` `dependsOn` the connectivity release, whose hooks mint what their pods start against. `components.kagent.installDisableWait` is gone — every component install waits for its workload again.
- **Kyverno**: `kyvernoPolicies.rules` (a map rule → ClusterPolicy of the cluster's Pod Security Standard policies) replaces `kyvernoPolicies.seccompPolicyName`, `seccompRuleNames`, `volumeTypesPolicyName` and `volumeTypesRuleNames`. The `kagent-declarative-seccomp` PolicyException (the v1alpha2 agent Deployments, label `app: kagent`) is gone; `substrate-workers` is its successor. The `app: kagent` agent-pod network policy (`<release>-kagent-agent-muster-egress`) is gone with the pods; the actors' destinations are opened on Substrate's egress gateway (`substrate-atenet-egress`), and the kagent controller admits it.

### Operator action

- **The release is inert until an installation admits it.** Every Giant Swarm installation selects this chart at `>=2.5.5 <4.0.0` (giantswarm/management-cluster-bases#739); nothing changes on an installation until its own bound is lifted — the per-installation cut-over of the migration (giantswarm/management-cluster-bases#737, #738), which destroys the v1alpha2 agents and their conversations (upstream's hard cut) and needs the prerequisites below in place first. Do not lift the bound for this release alone.
- **Cluster prerequisites with kagent on**: Kubernetes 1.35 (Substrate uses `certificates.k8s.io/v1beta1` `PodCertificateRequest`; 1.34 serves it only as `v1alpha1`) with the feature gates `ClusterTrustBundle`, `ClusterTrustBundleProjection` and `PodCertificateRequest` on kube-apiserver, kube-controller-manager **and every kubelet** — giantswarm/cluster#1005 turns them on by default; until that release reaches an installation the cluster chart's `internal.advancedConfiguration.{controlPlane.apiServer,controlPlane.controllerManager,kubelet}.featureGates` sets them (each list replaces the chart's default list: repeat the defaults next to the three gates, on all three components); enabling them rolls the control plane and every node, so it is done ahead of the cut-over. A live render (helm-controller) of this release with kagent on refuses a cluster that does not serve `certificates.k8s.io/v1beta1/PodCertificateRequest`, naming the gates — the kubelet gates it cannot see. Agent Substrate itself comes with the chart now: nothing to install by hand, no `kubectl-ate` step; an installation that ran Substrate from outside the chart (a lab) hands `ate-system` over to the chart's releases (README "Agent Substrate"). A cluster that reaches no public registry needs pull-throughs for `ghcr.io/giantswarm` (the kagent and Substrate images and charts), `docker.io/alpine/k8s` and `docker.io/alpine/openssl` (the hook images; `hooks.kubectlImage`, `hooks.opensslImage`) and the gVisor release asset from `storage.googleapis.com` (the `SandboxConfig`'s `spec.assets`).
- **The `HelmRelease` that installs the meta chart needs `spec.timeout: 12m` or more** (helm-controller's default is 5 minutes). Its install is the Helm SDK's `--wait`, which returns when every component `HelmRelease` is Ready; with kagent on the first 4.x reconcile installs nine of them plus Substrate and kagent in dependency order — about 5–6 minutes through a cluster's own Flux (220 s for the components under `helm install --wait`, Ready through Flux in 289 s in the green run) — so a `HelmRelease` on the default fails its first 4.x reconcile on the timeout and is retried. Measured by the ATS own-Flux scenario of giantswarm/agent-platform#380 (`tests/ats/test_own_flux.py` sets `12m`; CircleCI job 5941 hit the default, job 6033 passed). Giant Swarm's management-cluster base already sets `20m` (`extras/agent-platform/helm-release.yaml` in giantswarm/management-cluster-bases); a per-installation cut-over patch must not lower it. The uninstall through that Flux takes about 30–105 s and has to remove the component releases in reverse-dependency waves (giantswarm/agent-platform#385; README "Uninstalling").
- **`kagent.harness.snapshotLocation` is required with kagent on** (the render fails without it): the Substrate snapshot location the platform Harness writes the actors' snapshots to — `s3://<bucket>/<prefix>` on the installation's S3 bucket (CAPA: provision the bucket and an IRSA role for Substrate's `atelet` and `ate-api-server` ServiceAccounts; the bucket is the installation's infrastructure), or an S3-compatible store with its endpoint and credentials in `substrate.atelet.extraEnv`. Set it in the per-installation values before lifting the bound.
- **Substrate's database** rides the platform's CNPG Cluster where `postgres.enabled` is on (the fleet): the `Database` `<clusterName>-substrate` and the derived Secret `<clusterName>-substrate-app` in `ate-system` appear with the connectivity 4.0 release; nothing to set (`substrate.postgres.enabled: auto`). The Cluster's network policy admits `ate-api-server` on 5432. An installation without the Cluster runs Substrate's bundled single-instance Postgres unless it names `substrate.postgres.connectionString`.
- **Kyverno values to move**: `kyvernoPolicies.seccompPolicyName`, `seccompRuleNames`, `volumeTypesPolicyName`, `volumeTypesRuleNames` → `kyvernoPolicies.rules` (rule → ClusterPolicy; the defaults are upstream kyverno-policies' names, which Giant Swarm clusters use — an installation that never overrode the four keys sets nothing). The schema refuses the old keys.
- **Scheduling**: `atelet` runs on every node the DaemonSet controller admits (on a Giant Swarm cluster the worker nodes — the control-plane taint keeps it off those); pin it with `substrate.atelet.nodeSelector` / `tolerations` / `affinity` where a node pool is dedicated. The WorkerPool is pinned to one CPU feature set (`kagent.substrateWorkerPool.template.nodeSelector`): `amd64` — an arm64 installation sets `arm64`, never both — and on CAPA the vendor, `karpenter.k8s.aws/instance-cpu-manufacturer: amd`, because Karpenter consolidation mixes AMD and Intel nodes under an architecture-only pin and a golden snapshot (a gVisor checkpoint) restores only on a CPU that offers every feature it recorded (giantswarm/agent-platform#429) — and the CPU generation, `karpenter.k8s.aws/instance-generation: "6"` (quoted; the render refuses a bare number), because a newer generation's snapshot does not restore on an older one of the same vendor (goldens from `m7a` workers failed on `c5ad`, giantswarm/agent-platform#457); the fleet template renders both from the installation's values.
- **Values to drop**: the `kagent.<example-agent>` blocks, `kagent.controller.skillsInitImage` and the `METRICS_*` entries of `kagent.controller.env` — the kagent chart has no schema, so it would carry them on silently, and this chart's schema no longer enumerates them. `components.kagent-crds.enabled: false` next to `components.kagent.enabled: true` fails the render. The fleet template's controller-level `KAGENT_PROPAGATE_TOKEN` entry is inert on the line (the Go ADK reads it from the Harness's environment) and may stay until the fleet values follow (giantswarm/shared-configs#732).
- **The storage version of three CRDs is migrated by the chart** (giantswarm/agent-platform#396). Kagent 0.10 stored `modelconfigs`, `modelproviderconfigs` and `remotemcpservers.kagent.dev` at `v1alpha2` (`status.storedVersions`); the line's `kagent-crds` chart declares `v1alpha3` only, and the apiserver refuses the update (`status.storedVersions[0]: Invalid value: "v1alpha2": missing from spec.versions; v1alpha2 was previously a storage version, and must remain in spec.versions until a storage migration …`) — without this step `kagent-crds` never installs and the connectivity, `substrate` and `kagent` releases behind it never move. Two hook Jobs of this chart do the step on every upgrade that finds a CRD still stored at `v1alpha2`, with the engine and through a cluster's own Flux alike: `<release>-kagent-storage-version-backup` (`pre-upgrade`) records every object of those CRDs into ConfigMap **`kagent-storage-version-migration`** in the kagent namespace (`modelconfigs.json`, `modelproviderconfigs.json`, `remotemcpservers.json`, `recorded-at`), stops whoever applied each CRD from applying it again — the `HelmRelease` its `helm.toolkit.fluxcd.io/name` + `namespace` labels name, the 0.x kagent wrapper release (`crds: CreateReplace`), gets `spec.install.crds` and `spec.upgrade.crds` patched to `Skip`; the 4.x manifest carries no crds policy for the kagent release, so helm-controller's client-side apply drops the patch, and a server-side apply leaves it inert on a release whose chart ships no `crds/` (giantswarm/agent-platform#416) — deletes the CRDs, their objects with them, watches them stay absent for 60 s (one that comes back is deleted again and the hook fails naming the owner; Flux retries the upgrade and the next backup finds them gone), and re-points the three kinds to `kagent.dev/v1alpha3` in every Helm release manifest that still names them at `v1alpha2` — the 3.x connectivity release's catalog, the kagent 0.x release's default `ModelConfig` and tool-server `RemoteMCPServer`; Helm reads a release's stored manifest back through a served version on every upgrade, and without the re-point those two releases fail with `unable to build kubernetes objects from current release manifest: no matches for kind "RemoteMCPServer" in version "kagent.dev/v1alpha2"` (the same edit `helm mapkubeapis` makes for a removed API; the release Secrets are patched in place, nothing else in them changes); `<release>-kagent-storage-version-restore` (`post-upgrade`) waits for `kagent-crds` to serve `v1alpha3` and re-creates every recorded **`ModelConfig` that no Helm release owned** at `kagent.dev/v1alpha3` with the same name, namespace, labels, annotations and `spec` (the `ModelConfig` spec is identical in both versions), then marks the ConfigMap `restored-at`. What comes back and from where: the chart-rendered `ModelConfig`s from their releases — the kagent release's `providers`, the connectivity release's `kagent.modelConfigs[]` — at `v1alpha3`; the `ModelConfig`s model-manager manages from model-manager (`reconcileWiring`; a restored one is updated in place); the hand-created `ModelConfig`s from the restore hook. What does not come back: `RemoteMCPServer`s and `ModelProviderConfig`s — the 4.x charts render their own (`kagent.remoteMcpServers[]`, one `RemoteMCPServer` per agent from the Generic chart 1.x; the shared `RemoteMCPServer muster` is retired, see below) — and the record says which existed: `kubectl -n kagent get configmap kagent-storage-version-migration -o jsonpath='{.data.remotemcpservers\.json}' | jq .` (the same for `modelproviderconfigs.json`); on Giant Swarm installations the only hand-added objects beside the agents are `ModelConfig`s, so nothing is lost. **Downtime**: the 0.10 agents lose their `ModelConfig` when the CRD goes and get the `v1alpha3` one back once the restore hook and the migrate Job have run — the same window in which the cut-over replaces them (no conversation continuity is promised across it). Read the hooks: `kubectl -n <release namespace> logs job/<release>-kagent-storage-version-backup` and `…-restore` (a succeeded hook Job is removed by Helm; a failed one stays for an hour, the upgrade is reported failed and the record is kept — fix the cause and reconcile the `HelmRelease` again, the restore is idempotent and re-creates only what is still missing). Once the cut-over is done, drop the record: `kubectl -n kagent delete configmap kagent-storage-version-migration` — a `ModelConfig` you remove by hand afterwards would otherwise be listed there for ever (the restore never runs twice, `restored-at` gates it). A fresh 4.x installation sees both hooks do nothing.
- **model-manager and agent-manager roll once on the cut-over, with the pinned API version** (giantswarm/agent-platform#401): `model-manager.kagent.apiVersion` and `agent-manager.kagent.apiVersion` are `v1alpha3` on the 4.x line — the one version it serves — where the managers' charts default to `auto`, discovered once at start-up. A manager pod that started under kagent 0.10 would otherwise keep `v1alpha2` across an in-place upgrade (nothing in its values changed, so its Deployment never rolled) and fail every ModelConfig call with `the server could not find the requested resource` until a restart; the pin is that values change, and it rolls both Deployments after `kagent-crds` is Ready. Nothing to do; an installation that overrides either key drops the override.
- **Uninstall semantics**: uninstalling the `kagent-crds` release leaves the CRDs and every `AgentTemplate` / `RemoteMCPServer` in place — the line's `keep` policy on its CRD templates, carried by the line from giantswarm/kagent-upstream#10 on; do not uninstall `kagent-crds` on a release of the line that predates it. The removed v1alpha2 CRDs are deleted only by the migration's contract phase.
- **A BOM** pins `kagent` and `kagent-crds` to the same `-gs.N` release (one build); `examples/customer-bom.yaml` shows the shape. A re-pin of the line is one values change: the two ranges' floor (README "Re-pinning the kagent line"; since 4.8.0 the chart carries the build's tag and digests).
- **Metrics**: the kagent controller `ServiceMonitor` and metrics `Service` are no longer rendered — the line's controller serves no `/metrics`. The fleet's `KagentControllerDown` rule reads kube-state-metrics and needs no change.
## \<current\> → 4.0 (the kagent controller route is a `GRPCRoute` with the JWT policy on by default)

The kagent API v2 controller (giantswarm/giantswarm#37705) serves gRPC, gRPC-Web and A2A v1 on one port and no REST. `kagent.controllerRoute` now renders a `GRPCRoute` matched by gRPC service (`kagent.api.v1alpha1.{AgentInstanceService, AgentTemplateService, ModelService, SystemService}`, `lf.a2a.v1.A2AService`) on the data-plane and the public Gateway, with the JWT policy **on by default** in `Strict` mode and an identity transformation that sets `x-user-id` from the verified `email` claim (giantswarm/agent-platform#345; docs/authentication.md, "The kagent controller route"). The REST route on `/kagent` and `kagent.controllerRoute.pathPrefix` are gone.

### Operator action

- **Remove `kagent.controllerRoute.pathPrefix`** wherever it is set: the render fails naming the key. There is no path prefix any more — clients dial `https://<kagent.controllerRoute.hostname>` (gRPC origin) or `grpc://agentgateway.<release namespace>.svc.cluster.local:8080` in-cluster and append `/<service>/<method>` themselves.
- **Name the issuer and open `gateway.jwksEgress`** on every installation with the route on. The policy takes its issuer from `global.identity.issuerUrl` — the platform's one login provider, which the fleet template does not set today — or from `kagent.controllerRoute.jwtAuthentication.issuer` (`https://dex.<cluster>.<base-domain>`, the URL in the tokens' `iss`); without either the render fails naming both keys. `gateway.jwksEgress.enabled: true` (plus the issuer's `namespace` and `port` when the JWKS is fetched in-cluster) lets the data plane fetch the keys; `kagent.controllerRoute.jwtAuthentication.jwks` names the endpoint (`host`, `port`, `path`; `tls.enabled` with `tls.caSecretName` for an issuer serving it over TLS). The fleet template's `jwksEgress` comment ("not needed in the default setup") described the muster `/mcp` path; for the controller route the JWT layer is the default, and the render refuses the route without the egress. Rendered with the fleet template plus these two keys, the connectivity chart produces both GRPCRoutes, the policy, the Envoy timeout policy and the cilium admission; as-is it stops at the issuer guard. An installation whose issuer is outside the cluster needs #312.
- **Every client of the controller presents a Dex-issued JWT and speaks gRPC.** Opaque muster tokens and Kubernetes ServiceAccount tokens are refused at the gateway (`401`); a token without the `email` claim is refused (`403`). The Dev Portal needs its 1.x release (giantswarm/backstage#2344 — the backend's Connect client against the app-config's `apiBaseUrl`, which is now the gRPC origin without `/kagent/api`); klaus-gateway needs its 1.x release (giantswarm/klaus-gateway#234 — A2A v1 over gRPC). Both are part of the installation's cut-over to 4.0; a 0.x portal or gateway against this route gets `404`s for its REST paths.
- **Drop the fleet's `klausGateway.a2a.url` override** (`http://kagent-controller.kagent.svc.cluster.local:8083/api/a2a/kagent` in shared-configs): it names a REST path that does not exist on the line and bypasses the gateway. The chart default `grpc://agentgateway.agent-platform.svc.cluster.local:8080` is the target; klaus-gateway 1.x reads the scheme (`grpc://` plaintext h2c, `grpcs://` TLS). Adjust the namespace only if the release is deployed outside `agent-platform`. giantswarm/shared-configs#732 drops it fleet-wide.
- **Drop the controller-level `KAGENT_PROPAGATE_TOKEN`** entry from `kagent.controller.env` (shared-configs likewise): on the line the controller only registers the name; the Go ADK reads it from the platform Harness's environment, which the connectivity chart renders (giantswarm/agent-platform#344). Leaving it is harmless but inert.
- **The route matches by service; the agentgateway component must be at chart ≥ 2.1.1** (controller `v1.5.1-gs.3`, which translates a service-only `GRPCRoute` match into the path prefix `/<service>/`; the meta chart's `components.agentgateway.versionRange` 2.x resolves it on every installation). On an agentgateway older than that a service-only match never routes (the MCP catch-all answers `406`): list the RPCs you need under `kagent.controllerRoute.grpc.services.<service>` until the component has moved. An RPC the kagent line adds is reachable without a values change on the service-only shape.
- **Keep `kagent.controller.auth.userIdClaim: email`** (the chart default). It is now read twice: by the controller (`AUTH_USER_ID_CLAIM`, with the line's trusted-proxy authenticator) and by the gateway's identity transformation. Changing it changes both; a claim name that is not a plain identifier fails the render.
- **Network policy:** the controller admits the agentgateway data-plane pods and the kagent UI only. Anything else that called the controller directly on `:8083` (a debugging pod, a custom integration) has to go through the gateway with a token. On the kubernetes flavour the admission narrows from the whole release namespace to the data-plane pods.
- **Envoy front Gateway (the fleet):** the public `GRPCRoute kagent-controller-public` replaces the `HTTPRoute` of the same name and carries HTTP/2 to the agentgateway Service; a `BackendTrafficPolicy kagent-controller-public` renders when `ingress.backendTrafficPolicy.enabled` is on (the fleet sets it), so A2A streaming turns are not cut by Envoy's default route timeout. One Helm upgrade of the connectivity release; no pod rolls; the kagent UI route gets its header filter in the same revision.
- **Local development without a front proxy:** `kagent.controllerRoute.jwtAuthentication.enabled: false` switches the policy and the transformation off; the controller then trusts `x-user-id` as sent. Never the fleet shape.

## 3.x → 4.0 (the connectivity chart renders the kagent catalog at `kagent.dev/v1alpha3`; the shared muster `RemoteMCPServer` is retired; the Kyverno `Agent` mutations are gone)

Connectivity 4.0 renders `kagent.modelConfigs[]` and `kagent.remoteMcpServers[]` as `kagent.dev/v1alpha3` objects in the kagent namespace — the only version the kagent line serves; the spec fields are unchanged, and CI validates every rendered kagent object against the line's CRDs at the pinned release (`make verify-kagent-crds`). The shared `RemoteMCPServer agent-platform/muster` (`allowedNamespaces.from: All` — the cross-namespace contract the Generic `agent` chart 0.x's default `serverRef` depended on) is no longer rendered: on kagent API v2 an `AgentTemplate` binds a `RemoteMCPServer` of its own namespace, and the Generic agent chart 1.x renders one per agent — muster's URL, `STREAMABLE_HTTP`, the `X-Muster-Toolset` header, the `kagent.dev/discovery: disabled` label — and binds it (giantswarm/agent#25). Where muster is becomes a value the platform hands to agent-manager from one helper, `agent-platform.musterMcpUrl`: its chart value `muster.url`, derived by the meta chart next to `flux.helmReleaseServiceAccount`; agent-manager composes it into every agent, and the portal sends none. The two Kyverno ClusterPolicies that mutated the controller's input — `<release>-kagent-declarative-pod-security` (`spec.declarative.deployment.*SecurityContext` on every `Agent`) and `<release>-kagent-srt-settings` (`enableWeakerNestedSandbox` into the per-agent `srt-settings.json` Secret) — are gone: there is no `Agent` CR, no per-agent Deployment and no config Secret on kagent API v2. `kyvernoPolicies` keeps every key: the seccomp `PolicyException` `kagent-declarative-seccomp` still renders (its re-targeting at the Substrate worker pods is giantswarm/agent-platform#342), so do the CNPG exception and `agentSandbox.podSecurity`. The `kagent-flux` tenant identity and the derivation of agent-manager's `flux.helmReleaseServiceAccount` are unchanged. See docs/authentication.md, "The per-agent muster server, and tool discovery by the kagent controller".

### Operator action

- **On the upgrade of the connectivity release Helm deletes the shared `RemoteMCPServer muster` and the two ClusterPolicies with the release** — no resource policy keeps them; the release's Helm diff shows the three removals. The `kyverno.io` objects left are the agent-sandbox ClusterPolicy, the kagent seccomp PolicyException and, with a pgvector extension image, the CNPG ImageVolume PolicyException.
- **Agents that still bind `agent-platform/muster`** — Generic chart 0.x releases created before the installation's migration — do not outlive the cut-over anyway: the 4.0 line serves no `kagent.dev/v1alpha2`, so no v1alpha2 `Agent` remains to bind anything (the `Agent` CRD goes in the migration's contract phase). Every agent on 4.0 is a Generic chart 1.x release with its own server; an installation's releases are rewritten by agent-manager's `migrate` (giantswarm/agent-manager#38; the connectivity migration Job of giantswarm/agent-platform#346). An `AgentTemplate` written by hand must bind a `RemoteMCPServer` of its own namespace — `{kind: RemoteMCPServer, name: muster}` resolves nothing on 4.0.
- **`agent-manager.muster.url`: leave it unset.** The meta chart derives it from `muster.fullnameOverride` and `muster.service.port`; a set value that differs fails the render naming both. On a default install the derived URL equals the agent chart's own default (`http://muster.agent-platform.svc.cluster.local:8090/mcp`), so nothing changes in the composed agents. The Dev Portal sends no muster URL of its own: it creates agents through agent-manager's tools (`create_agent` takes no muster argument), and agent-manager reports the URL it composes in `get_info`.
- **`kagent.modelConfigs[]` and `kagent.remoteMcpServers[]`** keep their values shape; the objects render at `kagent.dev/v1alpha3` — an installation that kept copies of them at `v1alpha2` outside the chart must move them. `tokenSecret` still renders `headersFrom: [{name: Authorization, valueFrom: {type: Secret, name, key: token}}]`, applied by the runtime after the propagated caller token (every agent reaches that server as the credential, not as the person).

## 3.x → 4.0 (the connectivity chart renders one platform `Harness` per managed namespace; the Generic chart's per-agent placement values are gone, capacity is the WorkerPool)

Connectivity 4.0 renders **one `kagent.dev/v1alpha3 Harness`, `kagent`**, in each namespace the platform manages agents in (today the kagent namespace). On kagent API v2 *how* an agent runs is the Harness — the Go ADK runtime image **by digest**, the environment (`KAGENT_PROPAGATE_TOKEN=true`, so a turn reaches muster as the signed-in person) and the Substrate policy (`workerPoolRef` → the WorkerPool the kagent chart creates, `snapshotPolicy.location` → `kagent.harness.snapshotLocation`) — and an `AgentTemplate` becomes Ready only when a Harness admits it. Admission is one label: the Harness's `allowedAgentTemplates.selector` matches `agent-platform.giantswarm.io/harness: kagent` (the value is the Harness's own name), which the Generic agent chart 1.x sets on every template (giantswarm/agent#25). The image is digest-pinned because the CRD demands it (a moving tag would silently re-boot every agent under the Harness), so a tag fails the render naming the requirement; the digest moves with `kagent.tag` on a re-pin. `Harness.status` is never written — readiness is read from `AgentTemplate.status.harnesses[]` (a template without the label reports `harnesses[]` empty with its generation observed, which agent-manager surfaces as "not admitted"). No free list of arbitrary Harness types is a platform value in 4.0; a second runtime (e.g. a `claude` Harness) is a later, separate object (giantswarm/giantswarm#37625). `make verify-kagent-harness` asserts the shape and the guards.

The Generic chart 1.x has **no** `runtime`, `replicas`, `resources`, `nodeSelector` or `tolerations` (giantswarm/agent#25): how and where an agent runs is the Harness and its WorkerPool, not the agent. The only capacity and scheduling knobs are the WorkerPool's `kagent.substrateWorkerPool.replicas` and `kagent.substrateWorkerPool.template` (`resources`, `nodeSelector`, `tolerations`, `nodeAffinity`, `priorityClassName`) — one worker hosts one actor at a time, so a worker's `resources.limits` bound one agent's sandbox (the memory limit its RAM, the CPU limit its vCPU count) and `replicas` bound the concurrently active agents. Defaults: four workers, each requesting `250m`/`512Mi` and limited to `2` vCPU / `2Gi` (README "Agent Substrate: worker capacity"); the pool is pinned to one CPU feature set (`template.nodeSelector`: `amd64`, and on CAPA the vendor; giantswarm/agent-platform#342, #429).

### Operator action

- **`kagent.harness.image` is not set** since 4.8.0 (the kagent chart's stamped Go ADK digest is the Harness image; through 4.7.19 the meta chart pinned it next to `kagent.tag`). Set it only for a locally built runtime image — by digest, a tag fails the kagent chart's render.
- **Per-agent placement moves to the WorkerPool.** An installation that sized individual agents through the Generic chart's `resources` / `nodeSelector` / `tolerations` / `replicas` sets them once on `kagent.substrateWorkerPool` instead — `template.resources` is the per-agent sandbox size, `replicas` the concurrency. Raise `template.resources.limits` for heavier agents; raise `replicas` for more concurrent agents (budget `replicas × limits` on the node pool). A pool mixing CPU feature sets — architectures, but also vendors or generations of one architecture — wedges the actors whose golden snapshot was taken on the other one; keep one feature set per pool (`template.nodeSelector`: the architecture, and on CAPA the vendor label `karpenter.k8s.aws/instance-cpu-manufacturer`; README "Scheduling").
- **A hand-written `AgentTemplate` must carry `agent-platform.giantswarm.io/harness: kagent`** to be admitted by the platform Harness; without it the controller leaves `status.harnesses[]` empty and the template never becomes Ready.

## 3.x → 4.0 (the cut-over of an installation's database and agents to the kagent API v2 line)

The kagent API v2 controller starts on a fresh database and refuses the 0.10 one (goose against the 0.10 `schema_migrations` table crash-loops); **no conversation history is migrated** — the Dev Portal tells its users the same. The connectivity chart therefore renders a second CNPG `Database` on the existing `kagent-pg` Cluster, `kagent-pg-kagent-v2` (`spec.name: kagent_v2`, owner `kagent`, the `vector` extension, `databaseReclaimPolicy: retain`; `postgres.databases`), and derives its connection Secret `kagent-pg-kagent-v2-app` from the Cluster's bootstrap Secret `kagent-pg-app` in a `post-install,post-upgrade` hook Job (the database name swapped in `dbname`, `uri`, `jdbc-uri`, `fqdn-uri`, `fqdn-jdbc-uri` and `pgpass`; idempotent — a re-run yields the same Secret). Backups keep covering both databases: same Cluster, same WAL archive. The 0.10 database `kagent` is left untouched for **30 days**, so a bad cut-over can be argued about with data, then dropped.

The agents follow through one Job per installation, `agent-platform-connectivity-agent-manager-migrate-<hash>` in the kagent namespace (a plain Job, not a Helm hook — its failure never fails the connectivity release, which no longer waits on Jobs; the hash is of its pod template — image, args, environment, identity, labels — so a changed template renders a new Job and an unchanged one is re-applied as is), which runs `agent-manager migrate` as the tenant identity `kagent-flux` (`kagent.fluxServiceAccountName`) when connectivity 4.0 first renders it: it rewrites every portal-created Generic-chart release to chart 1.x values (the removed keys dropped, `muster.toolNames` → `muster.tools`, every git skill pinned to a commit, `agent.iconUrl` kept, the per-namespace `agent` OCIRepository moved to `1.x`), emits the same rewrite as a **diff** for every GitOps-owned release (never written), and — once every `AgentTemplate` in the namespace is Ready on the platform Harness and no release is left on 0.x — deletes the leftover `kagent.dev/v1alpha2 Agent` objects and the five removed CRDs (`agents`, `sandboxagents`, `agentharnesses`, `memories`, `toolservers`). Every phase is idempotent and gated on the previous one; a run with nothing left to do changes nothing; a failed phase leaves the v1alpha2 objects in place. The Job, its migration-only bindings (the CRD ClusterRole + ClusterRoleBinding, the per-namespace reads) and this section retire in a later 4.x release once the last installation is cut over.

### Operator action

- **Before the bound is lifted**: the kagent controller's DSN mount must name the derived Secret — `kagent.controller.volumes[cnpg-dsn].secret.secretName: kagent-pg-kagent-v2-app` (the fleet template's `secretName` follows in giantswarm/shared-configs#732; `database.postgres.urlFile: /etc/cnpg/uri` stays). Name the namespaces the GitOps-owned agents are applied from in `agentManager.migration.gitopsNamespaces` (the fleet: `[flux-giantswarm]`). The read token for private skill repositories is the Secret `kagent-skills-token` (key `token`) in the kagent namespace, the one the private-skill agents use today; without it public repositories resolve and private refs are reported as pending. To rehearse first, set `agentManager.migration.dryRun: true` for one upgrade: the report and the diffs, nothing written.
- **The Job's egress** (giantswarm/agent-platform#433): on a Cilium installation the Job's `CiliumNetworkPolicy` admits DNS, the API server and, by name on 443, the agent chart's sources — the registry (`gsoci.azurecr.io`), the storage front the registry redirects chart blob downloads to (`*.blob.core.windows.net`; Azure Container Registry answers a blob `GET` with a redirect there) and `api.github.com` — the same destinations agent-manager's own egress policy names, from one helper: `agentManager.networkPolicy.egress.fqdns` and `.cidrs` add to both (a mirror or a proxy in place of the public registry goes there), `networkPolicy.additionalEgressFQDNs` / `.additionalEgressCIDRs` too. The kubernetes flavour opens 443 to every public destination (or to `.cidrs` when set) and needs nothing. Connectivity 4.10.1 and earlier named the registry and GitHub only: the Job logged `agent chart registry unavailable … blob.core.windows.net … context deadline exceeded` every 30 s and completed its expand phase with `0 release(s) rewritten` (gazelle, 2026-09-13). An installation whose Job ran under that policy re-runs it (below) once the release carrying the fix is live — the policy changed, not the Job's pod template, so the completed Job is re-applied as is and does not run again by itself.
- **Reading the report**: `kubectl -n kagent get configmap agent-manager-migrate-report -o yaml` — per release what changed (each dropped or renamed key, each skill with the commit it was pinned to), what was skipped and why (GitOps-owned with its diff, not the Generic chart, already on 1.x), the phase reached and what gates the next one. A finished Job stays a day: `kubectl -n kagent logs -l app.kubernetes.io/component=agent-manager-migrate --tail=-1`.
- **The emitted diff**: apply it to the owning repository as a pull request (the HelmRelease values, the OCIRepository range `>=0.2.1 <1.0.0` → `1.x`, the removal of the v1alpha2 `driftDetection.ignore` paths). Until it is merged the contract phase does not run and the report names the release as pending.
- **Re-running** (the contract phase once every template is Ready, or after a fix): clone the Job under a new name — `kubectl create job --from` accepts CronJob sources only, so: `J=$(kubectl -n kagent get job -l app.kubernetes.io/component=agent-manager-migrate -o name | head -1); kubectl -n kagent get "$J" -o json | jq 'del(.status, .metadata.uid, .metadata.resourceVersion, .metadata.creationTimestamp, .metadata.managedFields, .spec.selector, .spec.template.metadata.labels["batch.kubernetes.io/controller-uid"], .spec.template.metadata.labels["controller-uid"], .spec.template.metadata.labels["batch.kubernetes.io/job-name"], .spec.template.metadata.labels["job-name"]) | .metadata.name += "-rerun-1"' | kubectl create -f -` (the network policy and the bindings select and bind by label and identity, so the clone has the same rights). A connectivity upgrade that changes the Job's image, args or environment renders a new Job and runs it; a chart release that changes nothing of its pod template re-applies the completed Job unchanged — the template carries no chart-version label, so the immutable `spec.template` is never touched and the release does not stall on it (the 4.x releases before this had the chart version in the template: the first release after it renders the Job once more under a new name and runs it, idempotent); one after the completed Job's day-long TTL re-creates and runs it (idempotent). The database Secret derivation is a hook and re-runs on every upgrade — `flux -n agent-platform reconcile helmrelease agent-platform-connectivity --force` forces one.
- **After 30 days**, drop the 0.10 database `kagent`. Where the installation sets `postgres.vector.extensionImage.reference` (the ImageVolume extension mode — the fleet template does), that database is a `Database` object of the chart (`kagent-pg-kagent`, `ensure: present` by CNPG's default), so a `DROP` by hand would be undone on the next reconcile; set `postgres.applicationDatabase.ensure: absent` in the installation's values instead — CNPG drops the database and keeps the object as the record (`kubectl -n kagent get database.postgresql.cnpg.io kagent-pg-kagent` shows `Applied`). Without an extension image there is no such object and the drop is the command: `kubectl -n kagent exec "$(kubectl -n kagent get pod -l cnpg.io/cluster=kagent-pg,cnpg.io/instanceRole=primary -o name)" -c postgres -- psql -c 'DROP DATABASE kagent'` (the primary; `psql` runs as the operator's superuser over the local socket). Either way the drop is final — a backup taken before it is the only way back. The bootstrap Secret `kagent-pg-app` keeps naming the dropped database; nothing reads it after the cut-over.
- **The order of the two fleet PRs is a race the chart absorbs** (giantswarm/agent-platform#416). The fleet procedure (giantswarm/management-cluster-bases `extras/agent-platform`, "Step 1 / Step 2") lands the flag — `agentPlatform.kagentApiV2: true`, the 4.x values — first and the bound lift second. Between the two the still-installed 0.x kagent release refuses its values on every retry (`additional properties 'harness' not allowed`) and, being `crds: CreateReplace`, re-applies its chart's `crds/` before each refusal; graveler (2026-09-13) re-created the three CRDs one second after the backup hook had deleted them, `kagent-crds` failed six times on `status.storedVersions` and stalled, and the CRDs were deleted by hand. The backup hook now sets that release's crds policy to `Skip` before it deletes and fails loudly, naming the release, if the CRDs come back (see "The storage version of three CRDs is migrated by the chart" above). Landing both PRs within one Flux interval still avoids the loop altogether; a stalled `kagent-crds` after a cut-over is read as `kubectl get crd modelconfigs.kagent.dev -o jsonpath='{.metadata.creationTimestamp} {.metadata.labels}'` — a `creationTimestamp` after the record's `recorded-at` with `helm.toolkit.fluxcd.io/name: kagent` is this race.
- **Fresh installations** (no 0.10 history) see the same objects: the `Database`, the derived Secret and a migrate Job that finds nothing to rewrite and no CRD to delete.

## \<current\> → \<next\> (kagent follows the wrapper's 0.x line)

`components.kagent.versionRange` is `>=0.2.0 <1.0.0` (was `0.2.x`): the floor stays at the flattened chart the wiring needs, the ceiling moves to the next major, as for the other 0.x components. The `giantswarm/kagent` wrapper released 0.3.0 and 0.3.1 on 2026-09-09 from CI-only changes — a `feat(ci)` title is a minor bump to git-cliff — with a chart identical to 0.2.2 in templates, values and dependencies; the minor-holding range excluded them, and would have excluded every following wrapper release, the next fix included.

### Operator action

- **None.** On every installation with kagent on, the kagent `OCIRepository` resolves 0.3.1 on its next poll and helm-controller upgrades the release. The rendered difference is two labels on every object — `helm.sh/chart: kagent-0.3.1` and `app.kubernetes.io/version: "0.3.1"` (the wrapper stamps its chart version as the app version) — and the pod annotations that hash the labelled ConfigMaps and Secret, so the kagent controller and UI pods roll once; no CRD, value or object changes otherwise. The meta chart's fleet render differs by the kagent `OCIRepository`'s `semver` line and nothing else.
- A BOM that pins `components.kagent.versionRange` to an exact version is unaffected; the example BOM keeps its `0.2.0` pin.

## \<current\> → \<next\> (the platform's single-replica pods refuse voluntary disruption)

giantswarm/agent-platform#431: on graveler a Karpenter consolidation evicted the agentgateway data plane, Backstage and Dex within one second; a streaming portal turn lost its model stream and stayed `working`, a Stop answered 401 from the restarting Backstage. The charts carried no guard. Now, by default:

- `karpenter.sh/do-not-disrupt: "true"` on the agentgateway data-plane pods (`gateway.parameters.podAnnotations`, through `AgentgatewayParameters`), muster (`muster.podAnnotations`), the kagent controller (`kagent.controller.podAnnotations`) and klaus-gateway (`klausGateway.podAnnotations`).
- `PodDisruptionBudget minAvailable: 1` on muster (`muster.podDisruptionBudget`), the kagent controller (`kagent.controller.pdb`, `unhealthyPodEvictionPolicy: AlwaysAllow`), klaus-gateway (`klausGateway.podDisruptionBudget`, `AlwaysAllow`) and agent-manager (`agentManager.podDisruptionBudget`, `AlwaysAllow`, rendered by the connectivity chart).

### Operator action

- **None to take the guards.** Each annotated component rolls its pod **once** (a pod-template change: the agentgateway data plane, muster, the kagent controller, klaus-gateway); the budgets are new objects. Streams open at the moment of that roll are cut once, as on any rollout.
- **Know what a budget on one replica means:** Karpenter's consolidation reports `DisruptionBlocked … pdb` for these pods and works around their nodes; a node drain (`kubectl drain`, a Karpenter drift or expiry replacement, a MachineDeployment roll) **waits** on them until the pool's drain timeout — `terminationGracePeriod: 30m` on the fleet's Karpenter NodePools, the `nodeDrainTimeout` of a MachineDeployment. Crash-looping pods stay evictable (`AlwaysAllow`) where the chart supports the policy (muster's does not). To drain a node on purpose without the wait, delete the pod (`kubectl delete pod`, not an eviction) or set the component's `…enabled: false` for the operation.
- **klaus-gateway needs chart 1.1.0 or later** (giantswarm/klaus-gateway#241 added `podAnnotations` and `podDisruptionBudget`); the meta chart's `components.klaus-gateway.versionRange: "1.x"` resolves it. An installation that pins an older 1.x chart must drop `klausGateway.podAnnotations` / `klausGateway.podDisruptionBudget` (set them to `null`) or lift the pin — the older chart's strict schema refuses the keys.
- **Opting out per component:** set the annotation's value to `"false"` (Karpenter treats only `"true"` as set) and `…podDisruptionBudget.enabled: false` / `kagent.controller.pdb.enabled: false`. An installation that already set `muster.podAnnotations` or `kagent.controller.podAnnotations` keeps its own map — Helm merges the maps key by key, so the default key is added next to yours unless you set it to `null`.
- Backstage keeps its chart's `maxUnavailable: 1` budget (no protection with one replica) — a change in giantswarm/backstage; the agentgateway data plane's own budget and second replica are `gateway.parameters.podDisruptionBudget` / `replicas` (giantswarm/agent-platform#373).

## \<current\> → \<next\> (`gateway.parameters.dataPlaneResources` is settable through the meta chart)

The meta chart's schema accepts `gateway.parameters.dataPlaneResources` (giantswarm/agent-platform#303): the agentgateway data-plane container's resources, which the connectivity chart reads and applied with its own defaults all along. The meta chart declares the key with those same defaults (`requests.ephemeral-storage: 50Mi`, `limits.ephemeral-storage: 512Mi`).

### Operator action

- **None.** The rendered platform objects are unchanged: the connectivity `HelmRelease`'s values now carry the key at the defaults the connectivity chart applied anyway, so helm-controller runs one upgrade of that release whose manifest is identical — the `AgentgatewayParameters` and the data-plane pods do not change.
- An installation that carried `gateway.parameters.dataPlaneResources` in its values for the standalone chart and dropped it to pass the meta chart's schema can set it again; an override reaches the data-plane container through the connectivity release as before.

## \<current\> → \<next\> (the 3.x meta chart selects its wiring chart below 4.0.0)

`components.agent-platform-connectivity.versionRange` is bounded to `>=1.0.0 <4.0.0` (giantswarm/agent-platform#348). The 4.x line of this repository is the kagent API v2 migration (giantswarm/giantswarm#37705); its connectivity chart renders `kagent.dev/v1alpha3` objects and must not reach a 3.x installation ahead of that installation's cut-over. Both charts release off one tag, and every installation's Flux re-resolves the connectivity range on each reconcile, so the bound has to be on the fleet before the first `v4.0.0` exists.

### Operator action

- **None.** The connectivity `OCIRepository` gets a new `spec.ref.semver` and keeps resolving the current 3.x release; nothing else renders differently.
- An installation moves to 4.x through the meta chart's own range (the fleet's `OCIRepository` bound, lifted per installation); the 4.0 chart resets this range to its own line.

## \<current\> → \<next\> (the kagent controller metrics Service selects kagent's own instance label; a first install with kagent on creates the kagent namespace)

Two fixes from the first lab run of the meta chart (giantswarm/agent-platform#305, #306). The connectivity chart's kagent controller metrics `Service` selects the kagent pods by kagent's own release name (`app.kubernetes.io/instance: kagent`) instead of the connectivity release's, so its `ServiceMonitor` gets endpoints. With the bundled engine (`components.flux.enabled: true`) and kagent on, the meta chart runs a `pre-install,pre-upgrade` hook Job `<release>-kagent-namespace` that creates the `kagent` namespace when it does not exist, so a first install on a bare cluster converges.

### Operator action

- **None.** On a management cluster with kagent and the monitors on, the connectivity release updates the metrics Service's selector (one Helm revision; a Service update, no pod rolls) and kagent controller scraping starts working — the Service shows endpoints, the scrape pool has targets. Where kagent is off, or the monitors are, nothing renders differently.
- **Giant Swarm management clusters (engine off): the meta chart's render is byte-identical.** The namespace hook renders only with the bundled engine; the fleet's bases keep creating the `kagent` namespace out of band and the connectivity release keeps adopting it.
- **An engine-on installation** (the Helm CLI, or self-managed) gains the hook on its next upgrade: on an installation that already has the namespace it finds it and leaves it alone (a `helm upgrade` runs one more short Job); a fresh install no longer needs the namespace pre-created — labs that did so can stop. `helm uninstall` is unchanged: the connectivity release owns the namespace and deletes it with everything in it (the agents' objects and Secrets included), as before; a reinstall that starts while it is still terminating waits for it to be gone.

## \<current\> → \<next\> (the muster `RemoteMCPServer` opts out of controller-side tool discovery)

The connectivity chart labels the shared `RemoteMCPServer agent-platform/muster` with `kagent.dev/discovery: disabled` whenever muster runs with OAuth on (`muster.muster.oauth.server.enabled`, the default). The kagent controller has no credential muster accepts when it lists the server's tools for the CR status, so every installation showed `Accepted=False (ReconcileFailed … Unauthorized)` on that CR (giantswarm/agent-platform#299). A kagent controller that knows the label reports `Accepted=True` (`DiscoveryDisabled`, empty inventory) instead; agents are unaffected either way — they resolve tools at run time with the propagated caller token. See docs/authentication.md, "Tool discovery by the kagent controller".

### Operator action

- **None.** The label is metadata on a CR the chart already owns; no pod rolls. The condition turns green once the installation runs a kagent controller with the opt-out (kagent-dev/kagent#2752; the `0.10.x` backport is kagent-dev/kagent#2753) — on a Giant Swarm management cluster that is the `giantswarm/kagent` wrapper picking up that upstream release under the meta chart's kagent range. Until then the condition stays as it was, and is expected.
- Do **not** add `headersFrom` with a controller credential to the muster server as a shortcut: the kagent runtime applies static headers after the propagated caller token, so every agent would act as that credential (docs/authentication.md explains).
- Installations that run muster **without** OAuth (`muster.muster.oauth.server.enabled: false`) get no label and keep controller-side discovery, which works anonymously there.

## \<current\> → \<next\> (self-management through the bundled Flux, on by default; the Helm CLI is day-0 only)

With the bundled engine on, the release now renders its own `OCIRepository` + `HelmRelease` and is adopted by its helm-controller (`gitops.self.enabled: auto` follows `components.flux.enabled`), a `ValidatingAdmissionPolicy` refuses `helm upgrade` / `helm rollback` from then on, and day-2 changes go through Secret `agent-platform-values`. See README "Self-management".

### Operator action

- **Giant Swarm management clusters: none.** The fleet runs with the engine off (`components.flux.enabled: false`), so `gitops.self.enabled: auto` resolves to off: no self objects, no hook, no policy, no identity. The fleet render is byte-identical to the previous release.
- **A Helm CLI installation with the engine on becomes self-managed on the `helm upgrade` to this version** — that upgrade is the last CLI upgrade it runs. What happens: the upgrade renders the self `HelmRelease` *suspended* (it does not exist yet), the admission policy and the values hook; after Helm applies and waits, the hook writes your `helm get values` into Secret `agent-platform-values` and starts the resumer, which resumes the `HelmRelease` once the release is `deployed`; helm-controller then adopts the release with one more revision (`helm history` shows it as `<version>+<oci digest>`) and follows `>=<version> <next major>.0.0` from `gitops.self.repository` (default `oci://gsoci.azurecr.io/charts/giantswarm`). From then on `helm upgrade` is refused with the reason in Helm's output and no revision written; change values by rewriting the Secret with your complete values file (README). **Requires Kubernetes ≥ 1.30** (the render refuses an older cluster). Pass your complete values file to that upgrade: what the hook writes into the Secret is exactly the upgrade's user-supplied values, and a partial `--set` would make them the whole of the release's values.
- **To opt out**, add `gitops: { self: { enabled: false } }` to the values file *before* that upgrade (and keep it there): the render is then the previous release's plus the two new pre-upgrade/pre-delete hooks (`<release>-self-stop-resumer`, `<release>-self-suspend`, no-ops without a self `HelmRelease`) and their namespaced identity `<release>-self`; the Helm CLI stays the day-2 tool. Installations that install **unreleased** charts (labs, the chart's own kind smoke) must set it: a self `HelmRelease` following the published range would replace the chart under test with the published one.
- **To hand a self-managed release back later**: `kubectl annotate namespace <ns> agent-platform.giantswarm.io/helm-cli=allow`, then `helm upgrade … --set gitops.self.enabled=false --force-conflicts` (once), keep the value in the values file. Without the annotation the upgrade is refused by the policy; with the annotation but the value still on, the render refuses (two writers on one release). Never `helm rollback` a self-managed release.
- **If the upgrade into this version fails** (`--wait` timeout), the self `HelmRelease` stays suspended and the policy is in force: either `helm uninstall --wait` and install again, or finish the bracket by hand — write the Secret and `kubectl -n <ns> patch helmrelease <release> --type merge -p '{"spec":{"suspend":false}}'`.
- `helm uninstall --wait` is unchanged for the operator: two more pre-delete hooks run first (weight -6 stops a resumer, -5 suspends the self `HelmRelease` and removes the values Secret); the uninstall still returns in under a minute on kind.
- Fallback should the admission policy ever be ruled out on a cluster (not shipped, documented for completeness): a pre-upgrade guard Job that refuses the CLI by the `managedFields` manager of the pending storage Secret (`Helm` = the CLI, `helm-controller` = the controller — Flux writes no labels there). It was measured to work but costs a `failed` revision and a controller re-assert per refused attempt, surfaces its reason only in the hook log, and is coupled to the controller's User-Agent; the policy replaced it.

## \<current\> → \<next\> (the chart brings its own Flux engine; `components.flux.enabled` defaults to `true`)

The meta-package gains the `flux-engine` subchart — the Flux Operator, one `FluxInstance` (source-controller + helm-controller under the multi-tenancy lockdown), the tenant identity `agent-platform-flux`, eleven CRDs in its `crds/` — switched by `components.flux.enabled`, **default `true`**, plus a render guard against a cluster that already runs Flux and pre-delete hooks for an ordered `helm uninstall`. With the engine on, every platform `HelmRelease` names `agent-platform-flux` (`gitops.serviceAccountName` default). See README "Installing".

### Operator action

- **Giant Swarm management clusters: none.** The fleet's `HelmRelease` carries `components.flux.enabled: false` since management-cluster-bases#721 (reconciled on every management cluster before this release shipped), and the render with that value is byte-identical to the previous release: no CRD, no operator, no hook, no `serviceAccountName`, the roster already said `flux: {enabled: false}`. The Flux CRDs' field managers on those clusters stay untouched (a disabled dependency's `crds/` are never collected).
- **Any other installation that runs its own Flux — through a `HelmRelease` or with the Helm CLI — MUST set `components.flux.enabled: false` before upgrading to this version.** Without it the render fails with `this cluster runs Flux; set components.flux.enabled=false or install the chart through it` (helm-controller marks the `HelmRelease` not Ready and retries; the Helm CLI refuses the upgrade before touching anything). The failure is the intended outcome: an engine next to a cluster's Flux would put a second, locked-down helm-controller on every `HelmRelease` in the cluster. Set the value and upgrade.
- **A Helm CLI installation on a cluster without Flux did not exist before this version** (the chart rendered Flux objects a Flux-less cluster could not accept), so there is no existing installation that gains the engine on `helm upgrade`. Should one ever turn the engine on for an existing release, `helm upgrade` would not install the subchart's `crds/` (Helm installs `crds/` on install only): apply them first — `helm show crds oci://gsoci.azurecr.io/charts/giantswarm/agent-platform --version <next> | kubectl apply --server-side -f -` — or install fresh. The common path is a fresh `helm install`.
- **Turning the engine off on an installation that runs it is refused** (`components.flux.enabled=false … the upgrade would delete the operator together with the FluxInstance it finalizes and hang`): uninstall the release instead — `helm uninstall --wait` tears it down in order — or delete the `FluxInstance` first.
- `gitops.namespace` cannot be combined with the engine (the tenant identity lives in the release namespace): the fleet's exempt-namespace layout goes with `components.flux.enabled: false`, a CLI installation leaves `gitops.namespace` empty.

## \<current\> → \<next\> (the kagent Namespace follows the kagent component)

The connectivity chart renders the `kagent` Namespace (`kagent.namespaceOverride`, when it differs from the release namespace) only while `components.kagent.enabled` is true.

### Operator action

- **None.** Where kagent is on, nothing changes. Where it is off, the connectivity upgrade deletes the empty, Helm-owned `kagent` namespace — on Giant Swarm management clusters it held nothing but the fleet's hand-written `kagent-flux` ServiceAccount and RoleBinding, which the fleet bases stopped applying before this release (the chart renders the identity itself where kagent runs). An installation that put its own objects into a `kagent` namespace on a cluster without kagent moves them out before upgrading, or turns kagent on.

## \<current\> → \<next\> (the standalone's wiring in the connectivity chart, behind the component toggles)

The connectivity chart renders, gated on `components.backstage`, `components.mcp-kubernetes`, `components.modelServing` (a new feature switch) and the kserve component toggles, what the agent-platform-standalone umbrella wired by hand: the Backstage app-config ConfigMap, route and config-reload hook; the mcp-kubernetes `MCPServer`; the KServe/vLLM model serving layer; the KServe controllers' network policies and guards. The seven values blocks of the extras now reach the connectivity release, the connectivity release `dependsOn` `muster`, and `backstage` `dependsOn` `agent-platform-connectivity`.

### Operator action

**No installation needs to act.**

- Giant Swarm management clusters: every toggle is off. The connectivity `HelmRelease` gets one Helm revision (its values gain the `backstage:`, `mcp-kubernetes:`, `cloudnative-pg:` and `kserve-*:` blocks and the `modelServing: {enabled: false}` roster line — the `modelServing:` block itself travels only while the switch is on — and `muster` in `dependsOn`); the objects it renders are byte-identical, so nothing changes on the cluster and no pod rolls.
- An installation moving from the standalone chart: the standalone's `components.backstage.*` and `components.mcp-kubernetes.*` wiring keys become `backstage.*` / `mcp-kubernetes.*`, `components.modelServing.*` becomes `components.modelServing.enabled` + `modelServing.*`, and `components.kserve.enabled` becomes `components.kserve-crd.enabled` + `components.kserve-resources.enabled` (`.llmisvc.enabled` → `kserve-llmisvc-crd` + `kserve-llmisvc-resources`). The full table is in the README, "Turning on the standalone's extras". The portal's installation name defaults to `agent-platform` (`backstage.installationName`), the standalone's release name; set it to your release name if it differed.
- `components.modelServing.enabled: true` without `components.kserve-crd` and `components.kserve-resources` on fails the render on a cluster that does not serve the `serving.kserve.io` APIs; turn the two on (they order themselves before the connectivity release) or set `modelServing.kserve.requireApi: false` for a KServe installed some other way. `modelServing.policies.enabled: true` with `kyvernoPolicies.enabled` resolving to `false` fails the render; the default `auto` follows Kyverno.
- `kagent.uiRoute.hostname` set while `kagent.oauth2-proxy.extraArgs.redirect-url` still derives from `global.domain` fails the render (the callback would land on the wrong host); set the redirect-url to the route's hostname.

## \<current\> → \<next\> (the chart renders the `kagent-flux` tenant identity)

The connectivity chart renders ServiceAccount `kagent-flux` and its RoleBinding to `cluster-admin` (namespace-scoped) in the kagent namespace whenever kagent is on, and one value — `kagent.fluxServiceAccountName` — names it into agent-manager (`flux.helmReleaseServiceAccount`, derived by the meta chart) and the portal (`agentPlatform.fluxServiceAccountName`). Six template fixes land with it (CHANGELOG, Fixed).

### Operator action

- **Giant Swarm management clusters: none.** The two objects already exist there, hand-written in the management-cluster bases with the same spec. helm-controller takes ownership of them on the upgrade (its default since 1.3) and both writers — the chart and the bases' kustomize-controller — set the same fields, so nothing changes on the cluster and no pod rolls. The hand-written copy is removed from the bases in a follow-up; until then both own the objects without conflict.
- Installations that set `agent-manager.flux.helmReleaseServiceAccount` themselves: drop it, or keep it equal to `kagent.fluxServiceAccountName`. A different value fails the render, naming the key. To rename the identity, set `kagent.fluxServiceAccountName` only.
- `ingress.mode: muster-direct` now fails the render when `kagent.controllerRoute.enabled`, `klausGateway.agentgatewayRoute.enabled`, or agent-platform-mcps' `agentgateway.enabled` with `mcpServers` set is on: those render agentgateway.dev objects a cluster without the agentgateway component cannot apply. Every management cluster runs `agentgateway-muster`; an installation that hits the guard turns the knob off or moves to an agentgateway-* mode.
- A leftover legacy toggle (`kagent.enabled`, `klausGateway.enabled`, …) now fails the render whatever its value, also `true` under a component that is on. Move it to `components.<name>.enabled` (see the section on component toggles below).

## \<current\> → \<next\> (cluster-shape knobs default to `auto`)

`kyvernoPolicies.enabled`, `networkPolicy.flavor`, `global.observability.metrics.serviceMonitor.enabled`, `dicebear.route.enabled` and `agentSandbox.podSecurity.enabled` default to `auto` (they were `true` / `cilium`): the object renders when its API group is served on the cluster (`kyverno.io/v1`, `cilium.io/v2`, `monitoring.coreos.com/v1`, `gateway.envoyproxy.io/v1alpha1`; the pod-security policy follows the resolved Kyverno answer). The meta chart detects once and resolves the knobs — and the component copies left at `auto` (muster's flavor and monitors, valkey's Cilium policy and PodMonitor, kagent's OTel exporters, oauth2-proxy monitor and OTLP header) — before it inlines a component's values.

### Operator action

**No installation needs to act.**

- A management cluster serves every one of those groups, so the render is byte-identical to the previous release: no object changes, no pod rolls.
- A cluster without them (kind, a plain cloud cluster) now renders the vanilla shape from the same chart — Kubernetes `NetworkPolicy` instead of `CiliumNetworkPolicy`, no `kyverno.io` object, no monitor, no avatar route — without a values file that describes the cluster. Values that set those knobs to `false` / `kubernetes` by hand keep working and can be dropped.
- An explicit value keeps winning over detection, on a knob (`networkPolicy.flavor: kubernetes` with Cilium present renders the kubernetes flavor everywhere) and on a component copy (`muster.networkPolicy.flavor: cilium` pins muster alone). `agentSandbox.podSecurity.enabled: true` together with `kyvernoPolicies.enabled: false` still fails the render, as before.
- `helm template` without `--api-versions` renders the vanilla shape. Pass the groups (`--api-versions kyverno.io/v1 --api-versions cilium.io/v2 --api-versions monitoring.coreos.com/v1 --api-versions gateway.networking.k8s.io/v1 --api-versions gateway.envoyproxy.io/v1alpha1`) to render the fleet shape offline; `helm install --dry-run=server` and helm-controller read the live cluster.

## \<current\> → \<next\> (the Argo CD render engine is removed; Flux is the only engine)

`templates/components.yaml` renders a Flux `OCIRepository` + `HelmRelease` per component and nothing else. The `gitops.engine: argo` branch — an Argo CD `Application` per component, ordered by `argocd.argoproj.io/sync-wave`, with `gitops.argo.project` / `gitops.argo.server` as its destination — is gone, and so are the two `gitops.argo.*` keys. It was never verified against a running Argo CD, no installation selected it, and the platform cannot run on Argo CD alone: agents are Flux objects on every path (the Backstage agent create flow and agent-manager write an `OCIRepository` + `HelmRelease` per agent). `gitops.engine` stays a key and accepts `flux` only (`enum: [flux]` in the schema, a template guard behind it).

### Operator action

- **Flux installations (every installation today): none.** The Flux render is byte-identical to the previous release; `gitops.engine: flux` set explicitly keeps rendering exactly the default.
- A values file that sets `gitops.engine: argo` fails the render — the schema first (`gitops.engine` must be `flux`), the template guard behind it with `gitops.engine=argo is not supported; flux is the only engine`. One that sets `gitops.argo.project` or `gitops.argo.server` fails the schema (`gitops` allows no additional property `argo`). Install Flux on the target (the cluster's own Flux, or the Flux Operator) and drop the keys. A non-GitOps install is not this chart's job; should one ever be needed, it is a plan of its own.

## \<current\> → \<next\> (the standalone chart's extras join `components.*`, off by default)

Backstage, mcp-kubernetes, the CloudNativePG operator and the four KServe charts are roster entries of this chart now (`components.backstage`, `components.mcp-kubernetes`, `components.cloudnative-pg`, `components.kserve-crd`, `components.kserve-resources`, `components.kserve-llmisvc-crd`, `components.kserve-llmisvc-resources`), all `enabled: false`, with their values in the new top-level blocks of the same names.

### Operator action

**No installation needs to act.** The default render adds nothing but seven `<name>: {enabled: false}` entries to the component roster inside the `agent-platform-connectivity` `HelmRelease`'s values, so that release gets one Helm revision with unchanged objects; no pod rolls. A management cluster keeps all seven off — it runs each of them as its own app. A BOM-pinned installation has nothing to pin unless it turns one on; `examples/customer-bom.yaml` carries the pins. An installation that turns Backstage or mcp-kubernetes on sets `global.domain` and `global.identity` first (README "Backstage, mcp-kubernetes, CloudNativePG and KServe").

## \<current\> → \<next\> (kagent moves to the flattened 0.2.x chart)

The `kagent` chart 0.2.0 flattened the upstream chart onto its chart root: upstream keys moved from `kagent.*` to the top level. The meta-package no longer nests the forwarded block, drops its own keys from it (`omitKeys`), and tracks the `0.2.x` range.

The top-level `kagent:` values block of THIS chart does not change. It stays flat, as it always was, and the connectivity chart keeps reading `kagent.namespaceOverride`, `kagent.controllerRoute`, `kagent.uiRoute`, `kagent.modelConfigs`, `kagent.remoteMcpServers` and `kagent.serviceMonitor` from it.

### Operator action

- None, if you set only keys inside the top-level `kagent:` block (the fleet default through `shared-configs`). The upgrade is in place: no resource is renamed. The kagent controller and UI Deployments roll once, because `helm.sh/chart`, `app.kubernetes.io/version` and the new `application.giantswarm.io/team` label change on every kagent resource and the pod annotations that hash them change with them.
- Pin `components.kagent.versionRange` to a `0.2.x` version if you pin ranges through a BOM. A `0.1.x` pin with this wiring installs the old chart with un-nested values, and it ignores them.
- `kagent.querydoc` is gone from the values: upstream 0.10.0 ships no querydoc subchart and the flattened chart's schema rejects the key. No installation set it.

## \<current\> → \<next\> (toolset presets; the muster range floors at 5.12.0)

The muster values gain `muster.muster.toolsetPresets` with the platform's two presets, `infrastructure` and `agent-platform`, selecting by the tool-group label (`agent-platform.giantswarm.io/tool-group`) the platform charts stamp on their `MCPServer` CRs. Agents refer to them as `preset:infrastructure` / `preset:agent-platform` in their `toolset`. muster reads the presets at startup, so the muster pod rolls once. See [docs/toolset-presets.md](./docs/toolset-presets.md).

`components.muster.versionRange` moves from `5.x` to `>=5.12.0 <6.0.0`: the `label:` rule exists from muster 5.12.0, and an older muster refuses to start on a preset that uses it. The floor makes the OCIRepository resolve a chart that has the rule before the HelmRelease applies the values that need it.

### Operator action

- **Wide-range installations (the default): none.** Flux resolves the range, the muster release rolls to ≥ 5.12.0 first, then applies the presets.
- **BOM-pinned installations:** pin `components.muster.versionRange` to `5.12.0` or later before or with this upgrade. A pin below 5.12.0 fails the muster pod's start with `toolsetPresets: … unknown rule "label"`. `examples/customer-bom.yaml` carries the current dogfooding snapshot. For the presets to resolve to anything, the label has to be on the CRs: agent-platform-mcps ≥ 0.9.0, agent-manager ≥ 0.3.0, model-manager ≥ 0.18.0; an older chart leaves the preset empty, not broken.
- **Installations that already defined their own `toolsetPresets`:** Helm merges the map — yours stay, the two shipped ones join. If you named one `infrastructure` or `agent-platform`, your definition wins over the shipped one; if you named one `read-only`, `none` or `full`, the render now fails naming it (muster would refuse to start on it either way).

## \<current\> → \<next\> (component toggles move into `components.<name>.enabled`)

A component's on/off switch is now `components.<name>.enabled`, and nothing else. The six per-chart toggles are removed.

`components.<name>.enabled` already existed and already won over the old `enabledFrom` indirection, so a component could be turned off two ways, and the connectivity chart only saw one of them. `components.agentgateway.enabled: false` together with `agentgateway.enabled: true` gave a cluster with no agentgateway release, an agentgateway ListenerSet ClusterRole for a controller that was never installed, and an ingress-mode guard that still believed the controller was there. Both charts now read `components.<name>.enabled`, the meta chart forwards its answers under that same key, and `make verify-meta` fails if the two ever disagree again.

### Operator action

Move each key you set. `<name>` is the `components:` key, which is the chart name, not the values-block name:

| Before | After |
|---|---|
| `agentgateway.enabled` | `components.agentgateway.enabled` |
| `valkey.enabled` | `components.valkey.enabled` |
| `mcps.enabled` | `components.agent-platform-mcps.enabled` |
| `kagent.enabled` | `components.kagent.enabled` |
| `klausGateway.enabled` | `components.klaus-gateway.enabled` |
| `agentSandbox.enabled` | `components.agent-sandbox.enabled` |

Move them **before** you upgrade. A leftover key fails the render with the message above, in both charts, and names the key to move. It is a hard failure on purpose: those value blocks accept unknown keys, so an ignored `enabled` would silently turn the component **off**, not on.

The `mcps:` block held nothing but its toggle and is removed. The chart's own values stay under `agent-platform-mcps:`.

Values that stay where they are: everything else in those blocks, including `agentSandbox.podSecurity.*`, `kagent.uiRoute.*`, `kagent.controllerRoute.*`, `klausGateway.a2a.*` and `klausGateway.obo.*`. Only the `enabled` key moves.

If you install `agent-platform-connectivity` on its own, without the meta chart, set the same `components.<name>.enabled` keys on it. Under the meta chart they are forwarded for you, and anything you set on the child release is overwritten.

## \<current\> → \<next\> (agentgateway moves to the flattened 2.x chart)

The `agentgateway` chart 2.0.0 flattened the upstream controller chart onto its chart root: upstream keys moved from `agentgateway.*` to the top level. The meta-package no longer nests the forwarded block, and it tracks the `2.x` range.

The top-level `agentgateway:` values block of THIS chart stays flat, as it always was, and the connectivity chart keeps reading `agentgateway.proxy.image` from it. The `enabled` toggle moves out of it in the same release — see the section below.

### Operator action

- Move `agentgateway.enabled` to `components.agentgateway.enabled` (see below). Otherwise the upgrade is in place: no resource is renamed, and the rendered output is unchanged apart from three non-selector labels.
- Pin `components.agentgateway.versionRange` to a `2.x` version if you pin ranges through a BOM. A `1.x` pin with this wiring installs the old chart with un-nested values, and its schema rejects them.
- `application.giantswarm.io/team` is new on every resource the agentgateway chart renders. It used to be a pod annotation only.

## \<current\> → \<next\> (the `agentic-platform-mcps` values key is removed)

The transition fallback from the chart rename is gone. Overrides under `agentic-platform-mcps` are no longer merged over `agent-platform-mcps`; both `values.schema.json` files reject the key.

### Operator action

- Move any remaining overrides from `agentic-platform-mcps:` to `agent-platform-mcps:` **before** upgrading. A leftover key fails the HelmRelease schema check, so the upgrade stops rather than dropping the values silently.

## 2.5.4 → 2.6.0 (chart renamed agentic-platform → agent-platform)

The product is now consistently named **Giant Swarm Agent Platform**. The charts follow: `agentic-platform` → `agent-platform`, `agentic-platform-connectivity` → `agent-platform-connectivity`, and the separately released `agentic-platform-mcps` → `agent-platform-mcps` (first new-name release 0.7.0). New OCI paths: `oci://gsoci.azurecr.io/charts/giantswarm/agent-platform{,-connectivity}`. Old-name releases stay in the catalog but receive no further versions.

### Operator action

- Point your `OCIRepository`/`HelmRelease` (or Argo `Application`) at the new chart name and OCI URL. Helm and Flux treat the renamed release as **uninstall + install**, not an in-place upgrade, plan a maintenance window and coordinate the release name, target namespace, and values source in one change.
- Rename the mcps values block `agentic-platform-mcps:` → `agent-platform-mcps:`. The old key is rejected as of the release above.
- The default namespace baked into `agent-platform-mcps.muster.musterUrl` and `kagent.a2a.url` is now `agent-platform`. If you deploy to a different namespace you already override these; update the values if you follow the namespace rename.
- kagent derives agent IDs from the namespace (`agentic_platform__NS__…` → `agent_platform__NS__…`). Renaming the namespace changes every agent ID and orphans existing kagent sessions, external references to old agent IDs (saved links, integrations) must be updated.
- If `postgres.clusterName` is unset, the rendered default changes from `agentic-platform-pg` to `agent-platform-pg`; a fresh namespace means a fresh Postgres cluster regardless, back up and restore data you need to keep.

## \<current\> → \<next\> (retire the agentic-platform-crds bundle — app-owned CRDs everywhere)

The standalone **`agentic-platform-crds` bundle chart is retired**. The three components that still rode it — `agentgateway`, `kagent`, `agent-sandbox` — now own their CRDs in their own chart's `crds/` dir (joining `muster`, which moved earlier). The `agentic-platform-crds` component is removed from the meta-package; its `OCIRepository` + `HelmRelease` are no longer rendered.

### What changed

- `components.agentic-platform-crds` is removed from `agentic-platform` values.
- `agentgateway`, `kagent`, `agent-sandbox` drop `dependsOn: [agentic-platform-crds]` (they already set `crds: CreateReplace` and own their CRDs as of release A).
- The CR consumers repoint to the CRD-owning **components**:
  - `agentic-platform-mcps`: `dependsOn: [muster, agentgateway]` (was `[muster, agentic-platform-crds]`).
  - `agentic-platform-connectivity`: `dependsOn: [agentgateway, kagent]` (was `[agentic-platform-crds]`).
- The meta-package now **drops a `dependsOn` reference to a component that is toggled off** at render time, so the always-on connectivity release does not block on `agentgateway`/`kagent` in `muster-direct` deployments where they are disabled.
- The `agentic-platform-crds` chart, its CircleCI build/test/push jobs, and the now-dead Renovate helmv3 lockstep rules are deleted.

### Operator action: none (non-destructive automatic handoff)

Release A already overwrote the live agentgateway / kagent / kmcp CRDs with the wrapper charts' `helm.sh/resource-policy: keep` copies (agent-sandbox + muster CRDs already carried `keep`). Because every live CRD now carries `keep`, Flux pruning the retired `agentic-platform-crds` `HelmRelease` (Helm uninstall) does **not** delete the CRDs — the prune is blocked by `keep`, and no `agentgateway.dev` / `kagent.dev` / `agents.x-k8s.io` CR cascade occurs. The component releases continue to own and upgrade the CRDs via `CreateReplace`.

**Prerequisite gate:** confirm the live CRDs carry `keep` before this release rolls out (release A must be applied and reconciled first):

```bash
for crd in $(kubectl get crd -o name | grep -E 'agentgateway\.dev$|kagent\.dev$|kmcp\.dev$|agents\.x-k8s\.io$'); do
  kubectl get "$crd" -o jsonpath='{.metadata.name}{"\t"}{.metadata.annotations.helm\.sh/resource-policy}{"\n"}'
done   # every row must print "keep"
```

Verify afterwards (CRDs survived, CRs intact, the bundle release is gone):

```bash
kubectl get crd | grep -E 'agentgateway\.dev|kagent\.dev|kmcp\.dev|agents\.x-k8s\.io'
kubectl get helmrelease -A | grep agentic-platform-crds   # expect: no rows
```

## \<current\> → \<next\> (muster app-owned CRDs)

muster's CRDs (`MCPServer` / `Workflow`) move from the `agentic-platform-crds`
bundle into muster's own app chart (app-owned CRDs). The `muster` component now
sets `crds: CreateReplace` and no longer `dependsOn` the bundle; `agentic-platform-crds`
drops its `muster-crds` dependency (it keeps agentgateway / kagent / agent-sandbox CRDs).

### What changed

- `agentic-platform-crds` no longer ships `mcpservers` / `workflows.muster.giantswarm.io`.
- The `agentic-platform` `muster` component renders `spec.install.crds: CreateReplace`
  and `spec.upgrade.crds: CreateReplace`, so muster's release applies and upgrades
  its own CRDs (from the muster chart's `crds/` dir) atomically with the app.
- `agentic-platform-mcps` now `dependsOn: [muster, agentic-platform-crds]` (its
  `MCPServer` CRs need the muster-owned CRD; its agentgateway CRs need the bundle).

### Operator action: none (non-destructive automatic handoff)

The live muster CRDs carry `helm.sh/resource-policy: keep` (injected by the prior
bundle handoff), so dropping the `muster-crds` dependency does **not** delete them —
Helm's prune is blocked by `keep`, and no `MCPServer` / `Workflow` CR cascade occurs.
On the next reconcile the muster release applies the (identical-content) CRDs via
`CreateReplace` and owns their upgrades thereafter. Verify afterwards:

```bash
kubectl get crd mcpservers.muster.giantswarm.io workflows.muster.giantswarm.io
helm get manifest <muster-release> -n <ns> | grep -c 'kind: CustomResourceDefinition'  # CRDs now ride muster
```

Prerequisite: a muster app chart version that ships its CRDs in `crds/` (resolved by
the `muster` component's `versionRange`).

## 0.2.0 → \<next\> (two-chart CRD split)

CRDs are no longer bundled in `agentic-platform`. They now ship in the companion **`agentic-platform-crds`** chart, which must be installed (and Established) **before** `agentic-platform`. There are now **two releases** from this repo, in order.

### What changed

- The `agentgateway-crds` sub-chart dependency is **removed** from `agentic-platform`. (`agentgateway-crds.enabled` in your values is now a no-op — drop it.)
- `muster.crds.install` is set to `false` by the umbrella, so the bundled `muster` sub-chart renders no CRDs.
- The five CRDs (3 × `agentgateway.dev`, 2 × `muster.giantswarm.io`) are provided by `agentic-platform-crds`.
- `helm template agentic-platform` now emits **zero** `CustomResourceDefinition` objects (CI guards this).

### Install ordering

```bash
helm upgrade --install agentic-platform-crds \
  oci://gsoci.azurecr.io/charts/giantswarm/agentic-platform-crds \
  --version <crds-chart-version> -n muster --create-namespace

kubectl wait --for=condition=Established \
  crd/agentgatewayparameters.agentgateway.dev \
  crd/mcpservers.muster.giantswarm.io

helm upgrade --install agentic-platform \
  oci://gsoci.azurecr.io/charts/giantswarm/agentic-platform \
  --version <chart-version> -n muster -f values.yaml
```

Flux users: add `dependsOn: [{ name: agentic-platform-crds }]` to the `agentic-platform` HelmRelease (see README).

### One-time CRD ownership handoff (required)

The `0.2.0` `agentic-platform` release **owned** the agentgateway and muster CRDs (Helm metadata `meta.helm.sh/release-name=<your-platform-release>`). The new `agentic-platform-crds` release will refuse to adopt CRDs owned by a different release. Re-annotate the existing CRDs so the CRDs release takes ownership and they survive future platform uninstalls. Run this **before** installing `agentic-platform-crds`:

```bash
# Replace `muster` with the namespace your agentic-platform-crds release installs into.
for crd in $(kubectl get crd -o name | grep -E 'agentgateway\.dev$|muster\.giantswarm\.io$'); do
  kubectl annotate "$crd" \
    meta.helm.sh/release-name=agentic-platform-crds \
    meta.helm.sh/release-namespace=muster --overwrite
  kubectl label "$crd" app.kubernetes.io/managed-by=Helm --overwrite
done
```

After the handoff, the muster CRDs become `helm.sh/resource-policy: keep`-protected via `agentic-platform-crds`. The agentgateway CRDs remain unprotected (upstream gap — see README "CRD lifecycle"); uninstalling `agentic-platform-crds` still deletes them and cascades to all agentgateway CRs.

### muster / muster-crds version alignment

`agentic-platform-crds` pins `muster-crds`; `agentic-platform` pins `muster`. Keep the two muster versions aligned so the CRD schemas match the controller. An identical-content `muster-crds` bump is a no-op upgrade.

## 0.0.0 → 0.1.0 (first stable release — pending)

### OTel defaults added for agentgateway data plane

`gateway.parameters.dataPlaneEnv` now defaults to:

```yaml
- name: OTEL_EXPORTER_OTLP_ENDPOINT
  value: http://otlp-gateway.kube-system.svc:4317
- name: OTEL_EXPORTER_OTLP_PROTOCOL
  value: grpc
```

This requires an `otlp-gateway` Service in `kube-system` (provided by the Giant Swarm observability platform). On clusters without it, the agentgateway data-plane logs connection errors to the exporter but starts normally. Disable with:

```yaml
gateway:
  parameters:
    dataPlaneEnv: []
```

### Bundled Valkey + OAuth server are ON by default

`valkey.enabled` and `muster.muster.oauth.server.enabled` both default to `true`. Operators must supply per-cluster fields up-front or the muster sub-chart's fail-guards reject install:

| Field | Why |
|---|---|
| `muster.muster.oauth.server.baseUrl` | OAuth issuer URL (HTTPS, public muster hostname). |
| `muster.muster.oauth.server.dex.issuerUrl` | Dex issuer URL on this cluster. |
| `muster.muster.oauth.server.dex.clientId` | OAuth client pre-registered in Dex. |
| `muster.muster.oauth.server.existingSecret` | Secret carrying `dex-client-secret`, `registration-token`, `oauth-encryption-key`, `valkey-password`. |
| `valkey.valkey.auth.usersExistingSecret` | Same Secret (key `valkey-password`) — drives ACL auth on the bundled Valkey. Conventionally the same name as the muster OAuth Secret. |

For dev installs that don't need OAuth: set `muster.muster.oauth.server.enabled: false`, `muster.muster.oauth.server.storage.type: memory`, and (optionally) `valkey.enabled: false`.

### muster 0.1.193 → 0.1.197

`muster.ciliumNetworkPolicy.*` is removed. Migrate:

```yaml
muster:
  networkPolicy:
    enabled: true            # was: ciliumNetworkPolicy.enabled
    flavor: cilium           # new — mirrors umbrella's networkPolicy.flavor
    cilium:
      allowClusterIngress: true  # was: ciliumNetworkPolicy.allowClusterIngress
```

The muster sub-chart now also ships a `kubernetes` flavor (vanilla `networking.k8s.io/v1 NetworkPolicy`) with the same CIDR replacements as the umbrella (`apiServerCIDR`, `clusterCIDR`, `worldExcludedCIDRs`). Muster's CiliumNetworkPolicy egress now covers the agentgateway data-plane on 8080 in the release namespace (upstream-proxy path) in addition to the existing Valkey egress on 6379.

### agentgateway-crds is a cluster prerequisite

Install `agentgateway-crds` before the agentic-platform release. Upstream `agentgateway` ships the controller and CRDs as separate charts at `oci://cr.agentgateway.dev/charts/` — we have no choice but to install both. Muster's CRDs continue to ship inside the umbrella via the muster sub-chart's `templates/crds.yaml`.

```
helm install agentgateway-crds \
  oci://cr.agentgateway.dev/charts/agentgateway-crds --version v1.2.1 \
  -n muster --create-namespace
```

#### Adopting pre-existing agentgateway CRDs

If a previous install applied agentgateway CRDs without Helm metadata, the new `agentgateway-crds` release refuses to take ownership. One-time adoption:

```bash
for crd in $(kubectl get crd -o name | grep -E 'agentgateway\.dev$'); do
  kubectl annotate "$crd" \
    meta.helm.sh/release-name=agentgateway-crds \
    meta.helm.sh/release-namespace=muster --overwrite
  kubectl label "$crd" app.kubernetes.io/managed-by=Helm --overwrite
done
```

### Public HTTPRoute is now operator-mandated

`muster.gatewayAPI.httpRoute.parentRefs` and `.hostnames` no longer default to the umbrella's internal `agentgateway` Gateway — that Gateway is for the data plane and is not exposed publicly. The muster sub-chart's fail-guard rejects install until both fields are set:

```yaml
muster:
  gatewayAPI:
    enabled: true
    httpRoute:
      parentRefs:
        - name: giantswarm-default
          namespace: envoy-gateway-system
          group: gateway.networking.k8s.io
          kind: Gateway
      hostnames:
        - muster.<cluster>.<base-domain>
```

### Data-plane Service forced to ClusterIP

The agentgateway controller hardcodes the data-plane Service to `type: LoadBalancer`. The umbrella overlays `spec.service.spec.type: ClusterIP` via `AgentgatewayParameters` so the data plane stays internal — envoy-gateway-system fronts public traffic. Override with `gateway.parameters.serviceType: LoadBalancer` if running on a cluster without a front Gateway.

### NetworkPolicy flavors

`networkPolicy.flavor` now accepts `cilium` (default) or `kubernetes`. The previous `none` value is removed — opt out via `networkPolicy.enabled: false`. The `kubernetes` flavor renders vanilla `networking.k8s.io/v1 NetworkPolicy` but is best-effort: no entity selectors (`cluster`, `world`, `kube-apiserver` become CIDR ranges via `networkPolicy.kubernetes.{apiServerCIDR,worldExcludedCIDRs}`), no FQDN egress (`additionalEgressFQDNs` is ignored).

**Cross-subchart flavor switch.** Muster 0.1.197 ships the same `networkPolicy.{enabled,flavor,cilium.*,kubernetes.*}` shape as the umbrella, so the flavor switch is consistent across both. When selecting the `kubernetes` flavor (or running on a non-Cilium cluster), set:

```yaml
networkPolicy:
  flavor: kubernetes
muster:
  networkPolicy:
    flavor: kubernetes
valkey:
  ciliumNetworkPolicy:
    enabled: false   # giantswarm/valkey-app has no kubernetes-flavor CNP yet
```

Previous `muster.ciliumNetworkPolicy.{enabled,allowClusterIngress}` keys are gone — migrate to `muster.networkPolicy.{enabled,flavor,cilium.allowClusterIngress}`.

### Controller CiliumNetworkPolicy added

The umbrella now ships a separate policy for the agentgateway **controller pod** in addition to the data-plane pod. Previously the controller was unprotected (upstream agentgateway chart ships no policies). Data-plane selector switched to the Gateway-API standard label `gateway.networking.k8s.io/gateway-name=<gateway.name>`; controller selector matches the controller's `app.kubernetes.io/instance=<release>` triple.

### Bundled Valkey — giantswarm/valkey-app, ACL auth, default-on for muster

`valkey.enabled: true` now bundles [giantswarm/valkey-app](https://github.com/giantswarm/valkey-app) (wraps upstream `valkey-io/valkey-helm`) instead of `bitnami/valkey`. Differences operators must adopt:

- **Service name.** Writable endpoint is `muster-valkey.<namespace>.svc:6379` (single Deployment + Service — no primary/replica split, no `-primary` suffix).
- **Default muster wiring.** `muster.muster.oauth.server.storage.type` defaults to `valkey` and `storage.valkey.url` defaults to `muster-valkey:6379` at the umbrella level. Enabling OAuth + the bundled valkey requires no further override. Set `storage.type: memory` for dev, or override the `url:` for an out-of-band Valkey.
- **Auth model.** ACL-based, not flat-password. The bundled chart provisions a `default` user with `~* &* +@all` and reads the cleartext password from the `valkey-password` key of `valkey.valkey.auth.usersExistingSecret`. Muster sends `AUTH <password>` against the default user — standard backwards-compatible form.
- **Values shape.** Wrapper exposes upstream values under `valkey.valkey.*`:

  | Old (bitnami) | New (valkey-app) |
  |---|---|
  | `valkey.fullnameOverride: muster-valkey` | `valkey.valkey.fullnameOverride: muster-valkey` |
  | `valkey.image.tag: "9.0.4"` | `valkey.valkey.image.tag` (defaults to chart appVersion 8.1.4) |
  | `valkey.auth.existingSecret: <name>` | `valkey.valkey.auth.usersExistingSecret: <name>` |
  | `valkey.primary.persistence.{enabled,size}` | `valkey.valkey.dataStorage.{enabled,requestedSize}` |
  | `valkey.primary.resources` | `valkey.valkey.resources` |

- **Migration from bitnami.** Re-pointing `storage.valkey.url` from `muster-valkey-primary.<ns>.svc:6379` to `muster-valkey.<ns>.svc:6379` is sufficient at the URL layer; the previous bitnami StatefulSet's PVC is not consumed by the new Deployment-backed PVC (different name). Treat session storage as ephemeral when cutting over.

### `bootstrap.oauth.*` removed

The Helm `lookup`-based OAuth bootstrap Secret has been removed. Three Secret-injection paths remain: inline values, `existingSecret`, and the new umbrella-level `extraObjects: []`. See README "OAuth secrets".

### `extraObjects` added

New top-level umbrella key. Each entry is rendered through `tpl` and emitted alongside the chart. Useful for shipping the muster OAuth Secret manifest in the same Helm release.

### `gateway.parameters.dataPlane{Env,Volumes,VolumeMounts}` (unchanged from earlier draft)

Strategic-merge knobs on the AgentgatewayParameters template. Use when pushing OTel env vars or mounting cert-manager-issued CA bundles for `controller.xds.mode: tls` into the dynamically-rendered data-plane container.

## Template

```
## <previous-version> → <new-version>

### <Short title of breaking change>

<What changed, why, what operators must do. Always cite the value /
template / file involved so reviewers can grep for the change.>
```
