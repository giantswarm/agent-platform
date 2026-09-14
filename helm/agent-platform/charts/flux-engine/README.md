# flux-engine

The Flux engine of the agent-platform meta chart: the Flux Operator
(ghcr.io/controlplaneio-fluxcd/flux-operator) and one FluxInstance named
`flux` in the release namespace running source-controller and helm-controller
under the multi-tenancy lockdown, plus the platform's tenant identity — the
ServiceAccount `agent-platform-flux` bound to cluster-admin, which every
platform HelmRelease names. Its crds/ carry the seven Flux CRDs of the two
controllers and the four Flux Operator CRDs. A conditional dependency of the
meta chart (`components.flux.enabled`, default true): a cluster that runs its
own Flux sets the value to false and gets neither the CRDs nor the engine.
Not published on its own.

**Homepage:** <https://github.com/giantswarm/agent-platform>

## Source Code

* <https://github.com/giantswarm/agent-platform>
* <https://github.com/controlplaneio-fluxcd/flux-operator>
* <https://github.com/fluxcd/flux2>

## Values

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| operator.image | object | `{"pullPolicy":"IfNotPresent","registry":"ghcr.io","repository":"controlplaneio-fluxcd/flux-operator","tag":"v0.60.0"}` | The Flux Operator image. The tag is the operator release (appVersion in Chart.yaml); Renovate tracks it as a docker image pin. |
| operator.imagePullSecrets | list | `[]` | Pull secrets for the operator image, in the release namespace (`global.imagePullSecrets` of the meta chart apply as well). |
| operator.logLevel | string | `"info"` | Operator log level: debug, info or error. |
| operator.reportingInterval | string | `"5m"` | How often the operator refreshes the FluxReport. |
| operator.resources | object | `{"limits":{"cpu":"2000m","memory":"1Gi"},"requests":{"cpu":"100m","memory":"64Mi"}}` | Container resources (the upstream chart's defaults). |
| operator.priorityClassName | string | `""` | Pod priority class; the upstream chart recommends system-cluster-critical. |
| operator.nodeSelector | object | `{}` | Scheduling constraints of the operator pod. |
| operator.tolerations | list | `[]` |  |
| operator.affinity.nodeAffinity.requiredDuringSchedulingIgnoredDuringExecution.nodeSelectorTerms[0].matchExpressions[0].key | string | `"kubernetes.io/os"` |  |
| operator.affinity.nodeAffinity.requiredDuringSchedulingIgnoredDuringExecution.nodeSelectorTerms[0].matchExpressions[0].operator | string | `"In"` |  |
| operator.affinity.nodeAffinity.requiredDuringSchedulingIgnoredDuringExecution.nodeSelectorTerms[0].matchExpressions[0].values[0] | string | `"linux"` |  |
| instance.distribution.version | string | `"2.x"` | Flux version range the operator installs and keeps current — the operator upgrades the controllers, the CRDs and the stored objects inside the range on its own (Helm never upgrades crds/). No `artifact` is set, so the manifests come embedded in the operator image (air-gap default): a new Flux minor arrives with a new operator tag. |
| instance.distribution.registry | string | `"ghcr.io/fluxcd"` | Registry the Flux controller images are pulled from. A mirror of ghcr.io/fluxcd goes here. |
| instance.distribution.imagePullSecret | string | `""` | Pull secret for the controller images, in the release namespace. |
| instance.components | list | `["source-controller","helm-controller"]` | The controllers the instance runs. Explicit on purpose: the operator's default list is four controllers (notification-controller included), the platform needs the two that fetch charts and install releases. A future component that needs Kustomizations adds kustomize-controller here. |
| instance.cluster.type | string | `"kubernetes"` | kubernetes, openshift, aws, azure or gcp — the operator adapts the controller manifests to the platform. |
| instance.cluster.multitenant | bool | `true` | no-cross-namespace-refs=true and impersonates the tenant ServiceAccount of every HelmRelease; one without serviceAccountName runs as `tenantDefaultServiceAccount`, which has no rights. The platform's own HelmReleases name `agent-platform-flux` (rendered below). |
| instance.cluster.tenantDefaultServiceAccount | string | `"default"` |  |
| instance.cluster.networkPolicy | bool | `false` | No Flux-managed NetworkPolicy in the release namespace: the meta chart's network policies are the connectivity component's. |
| instance.cluster.domain | string | `"cluster.local"` | Cluster DNS domain. |
| instance.kustomizePatches | list | `[]` | Kustomize patches applied to the Flux manifests, verbatim (FluxInstance.spec.kustomize.patches): controller resources, extra flags. |
| instance.reconcileEvery | string | `"1h"` | Reconcile interval and timeout of the FluxInstance (operator annotations). |
| instance.reconcileTimeout | string | `"5m"` |  |
