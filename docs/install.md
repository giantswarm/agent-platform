# Installing the Agent Platform

This guide takes a cluster from nothing to a running Agent Platform with one `helm install`. Pick the shape of your cluster, prepare what that shape needs, install with its example values, then register the redirect URIs the install prints. The chart's [README](../README.md) is the reference for every value; this page is the path through it.

## 1. Pick your cluster shape

Each shape has a values file under [`helm/agent-platform/examples/`](../helm/agent-platform/examples/). The file, unchanged apart from your domain, issuer and names, is the values of the first install; CI renders each one with the meta chart and every component chart it turns on (`make verify-examples`).

| Example | Cluster | Public edge | What runs |
|---|---|---|---|
| [`kind-lab-dex.yaml`](../helm/agent-platform/examples/kind-lab-dex.yaml) | kind on a laptop, with the lab Dex | none (port-forward) | muster, the avatar service, the connectivity wiring. What the chart's kind smoke installs. |
| [`own-gateway.yaml`](../helm/agent-platform/examples/own-gateway.yaml) | any cluster with its own public Gateway | your Gateway (`global.gatewayApi.parentRefs`) | muster with its OAuth server and session store, behind `https://muster.<domain>/mcp` |
| [`chart-owned-edge.yaml`](../helm/agent-platform/examples/chart-owned-edge.yaml) | a cluster with a LoadBalancer but no Gateway | the chart's agentgateway data plane (`gatewayApi.gateway.create: true`) | the above, with MCP traffic through agentgateway and the bundled MCP servers |
| [`managed-cloud.yaml`](../helm/agent-platform/examples/managed-cloud.yaml) | EKS, AKS, GKE, … with an external Postgres | your Gateway | the full platform: kagent on Agent Substrate, agent-manager, the portal (Backstage), the Kubernetes MCP server, the kagent UI |

The released chart carries the examples: `helm pull oci://gsoci.azurecr.io/charts/giantswarm/agent-platform --untar` puts them in `agent-platform/examples/`.

## 2. Prerequisites

### Every shape

- **Kubernetes ≥ 1.33** and **Helm 4** (Helm 4's `--wait` waits for the component `HelmRelease`s to be Ready; Helm 3 returns before they are).
- **The Gateway API CRDs**, the one cluster prerequisite the chart does not bring:

  ```bash
  kubectl apply -f https://github.com/kubernetes-sigs/gateway-api/releases/download/v1.5.0/standard-install.yaml
  ```

- **A default StorageClass** (`kubectl get storageclass` marks one `(default)`): the bundled Valkey, kagent's and Substrate's bundled Postgres and the CloudNativePG Cluster claim volumes without naming a class.
- **No Flux of its own**, or install through it: the chart brings the Flux engine where the cluster has none. A cluster that runs Flux sets `components.flux.enabled: false` and installs the chart through that Flux ([README: Clusters that run Flux](../README.md#clusters-that-run-flux)).

### A public Gateway (own-gateway, managed-cloud)

A Gateway API implementation (Envoy Gateway, Cilium, Istio, …) with an HTTPS listener for `*.<domain>` that admits routes from the release namespace, a wildcard certificate, and a DNS record `*.<domain>` pointing at the Gateway's address. With cert-manager and a DNS-01 issuer:

```yaml
apiVersion: cert-manager.io/v1
kind: Certificate
metadata:
  name: platform-wildcard
  namespace: gateway-system
spec:
  secretName: platform-wildcard-tls
  dnsNames: ["*.platform.example.com"]
  issuerRef:                      # a wildcard needs a DNS-01 solver
    kind: ClusterIssuer
    name: letsencrypt-dns01
---
apiVersion: gateway.networking.k8s.io/v1
kind: Gateway
metadata:
  name: public
  namespace: gateway-system
spec:
  gatewayClassName: <your implementation's class>
  listeners:
    - name: https
      protocol: HTTPS
      port: 443
      hostname: "*.platform.example.com"
      tls:
        mode: Terminate
        certificateRefs:
          - name: platform-wildcard-tls
      allowedRoutes:
        namespaces:
          from: All
```

`global.gatewayApi.parentRefs` names this Gateway (`name: public`, `namespace: gateway-system`).

### The chart-owned edge (chart-owned-edge)

No Gateway to prepare: the agentgateway data plane becomes the edge. It needs the wildcard certificate as a `kubernetes.io/tls` Secret **in the release namespace** (`gatewayApi.gateway.tls.secretName`, the Certificate above with `namespace: agent-platform` and `secretName: agent-platform-wildcard-tls`) and a LoadBalancer implementation. After the install, point `*.<domain>` at the address of Service `agent-platform/agentgateway`.

### Agents (managed-cloud)

- **Kubernetes ≥ 1.35 with the `PodCertificateRequest`, `ClusterTrustBundle` and `ClusterTrustBundleProjection` feature gates** on kube-apiserver, kube-controller-manager and every kubelet. Agent Substrate, which runs every agent, takes its identities from them; a live install refuses a cluster that does not serve `certificates.k8s.io/v1beta1` `PodCertificateRequest`.
- **On Cilium with kube-proxy replacement: `socketLB.hostNamespaceOnly: true`** (`bpf-lb-sock-hostns-only`), or the agents cannot resolve names over UDP from their sandboxes ([README: Agent Substrate](../README.md#agent-substrate)).
- **An object store bucket** for the agents' snapshots, `kagent.harness.snapshotLocation` (`s3://<bucket>/<prefix>`), writable from the nodes (IRSA, Workload Identity, or credentials in `substrate.atelet.extraEnv`).
- **A database**, one of three:
  - the bundled single-instance Postgres of kagent and of Substrate (the defaults; a lab or a trial);
  - the platform's CloudNativePG Cluster: `components.cloudnative-pg.enabled: true` and `postgres.enabled: true` ([README: Backstage, mcp-kubernetes, CloudNativePG and KServe](../README.md#backstage-mcp-kubernetes-cloudnativepg-and-kserve));
  - an external Postgres with the `pgvector` extension available (managed-cloud): the connection URL in Secret `kagent-postgres` (key `uri`) in the `kagent` namespace and the connection string in Secret `substrate-postgres` (key `connectionString`) in `ate-system`, both created before the install.
- The portal (Backstage) keeps its database in SQLite, or in a CloudNativePG Cluster with `backstage.database.engine: postgresql` and the `cloudnative-pg` component; it takes no external database.

### Pod Security

The chart sets no Pod Security Admission labels. The release namespace's workloads pass the `restricted` level, the agentgateway data plane included (its `net.ipv4.ip_unprivileged_port_start` sysctl is one Pod Security counts as safe). With kagent on, a cluster that enforces a level by default exempts `ate-system` as `privileged`: Substrate's `atelet` DaemonSet runs privileged to start the agents' sandboxes on each node. With `components.agent-sandbox.enabled: true` on such a cluster, set it back to `false`: its pods get their security context from a Kyverno policy.

### The identity provider

One OIDC provider signs in every person, **Dex only today** (Keycloak and Entra ID are not supported yet: the platform relies on Dex's cross-client audiences). What to register:

| Client | Redirect URI | When |
|---|---|---|
| `global.identity.clientId` (e.g. `agent-platform`) | `https://muster.<domain>/oauth/callback` | always (muster's OAuth server) |
| the same client | `https://backstage.<domain>/api/auth/oidc-agent-platform/handler/frame` | `components.backstage` |
| a client of its own for the kagent UI's oauth2-proxy | `https://kagent.<domain>/oauth2/callback` | `kagent.uiRoute` with `kagent.oauth2-proxy` |
| `dex-k8s-authenticator` (the audience your kube-apiserver trusts), `trustedPeers: [<global.identity.clientId>]`, no redirect URI | — | model-manager, agent-manager and mcp-kubernetes: muster forwards the person's token to them with this audience, and they act on the cluster as that person |

In Dex's config:

```yaml
staticClients:
  - id: agent-platform
    name: Agent Platform
    secret: <dex-client-secret>
    redirectURIs:
      - https://muster.platform.example.com/oauth/callback
      - https://backstage.platform.example.com/api/auth/oidc-agent-platform/handler/frame
  - id: kagent-ui
    name: kagent UI
    secret: <the kagent UI's client secret>
    redirectURIs:
      - https://kagent.platform.example.com/oauth2/callback
  - id: dex-k8s-authenticator      # the kube-apiserver's OIDC client
    name: Kubernetes
    secret: <its secret>
    trustedPeers:
      - agent-platform
```

The kube-apiserver has to trust the same issuer (`--oidc-issuer-url`, `--oidc-client-id=dex-k8s-authenticator`) for the forwarded tokens to carry a person's RBAC; a cluster whose apiserver trusts another audience sets it in `mcp-kubernetes.kubernetesAudience` and in each manager's `muster.mcpServer.auth.requiredAudiences`.

The platform Secret, `global.identity.existingSecret`, in the release namespace:

```bash
kubectl create namespace agent-platform
kubectl -n agent-platform create secret generic agent-platform-idp \
  --from-literal=dex-client-secret='<the agent-platform client secret>' \
  --from-literal=registration-token="$(openssl rand -hex 32)" \
  --from-literal=oauth-encryption-key="$(openssl rand -base64 32)" \
  --from-literal=valkey-password="$(openssl rand -hex 24)" \
  --from-literal=backstage-session-secret="$(openssl rand -hex 32)"   # with components.backstage
```

| Key | Read by |
|---|---|
| `dex-client-secret` | muster, the portal, mcp-kubernetes: the client secret of `global.identity.clientId` |
| `registration-token` | muster: the bearer an MCP client presents to its dynamic client registration endpoint |
| `oauth-encryption-key` | muster: encrypts the tokens it stores |
| `valkey-password` | the bundled Valkey and muster, its session store |
| `backstage-session-secret` | the portal: signs its session cookie |

The kagent UI's oauth2-proxy reads Secret `kagent-oauth2-proxy` in the `kagent` namespace (`client-id`, `client-secret`, `cookie-secret`: `openssl rand -base64 32 | head -c 32`).

**The lab Dex is not an identity provider.** [`tests/ats/lab-dex.yaml`](../tests/ats/lab-dex.yaml), what the kind example uses, runs Dex with static password users, world-readable client secrets and a self-signed certificate, and rewrites the cluster's CoreDNS. It exists for a throwaway kind cluster and nothing else.

## 3. The inputs: `global.*`

The meta chart injects `global` into every component release; these keys are the installation's contract.

| Key | Default | Meaning |
|---|---|---|
| `global.domain` | `""` | The one hostname input: `muster.`, `backstage.`, `kagent.`, `agentgateway.` and `avatars.<domain>` derive from it, each overridable next to its route. TLS and DNS for `*.<domain>` stay outside the chart. |
| `global.identity.issuerUrl` | `""` | The OIDC issuer exactly as it appears in the tokens' `iss` claim. |
| `global.identity.clientId` | `""` | The platform's OAuth client at the provider. |
| `global.identity.existingSecret` | `""` | The platform Secret in the release namespace (keys above). |
| `global.identity.ca.secretName`, `.key` | unset, `ca.crt` | The CA of a provider with a private certificate, a Secret in the release namespace. |
| `global.gatewayApi.parentRefs` | `[]` | The public Gateway every route attaches to, unless a route names its own or `gatewayApi.gateway.create` makes the chart's data plane the edge. |
| `global.registry` | `gsoci.azurecr.io` | The registry every component pulls from; a mirror goes here ([README: Private registry overrides](../README.md#private-registry-overrides)). |
| `global.imagePullSecrets` | `[]` | Pull secrets for every component. |
| `global.observability.metrics.serviceMonitor.enabled` | `auto` | Monitor objects where `monitoring.coreos.com/v1` is served. |
| `global.observability.traces.otlp.endpoint` | `http://otlp-gateway.kube-system.svc:4317` | The collector every exporter sends traces to; the default is a Giant Swarm cluster's OTLP gateway. Elsewhere name your collector, or `""` to export nothing (the examples do). |

muster's OAuth server reads its own keys and must agree with `global.identity` (the render fails when they differ): every example sets `muster.muster.oauth.server.{baseUrl, dex.issuerUrl, dex.clientId, existingSecret}` and `muster.muster.oauth.mcpClient.publicUrl` next to it.

## 4. Install

```bash
helm install agent-platform oci://gsoci.azurecr.io/charts/giantswarm/agent-platform \
  --namespace agent-platform --create-namespace \
  -f own-gateway.yaml --wait --timeout 10m
```

`--wait` returns when every component `HelmRelease` is Ready: about two minutes for muster alone, four to six with kagent and Substrate. That `helm install` is the last Helm command besides `helm uninstall`: the release manages itself through the Flux engine it brought, rolls forward inside its major, and takes a values change as a rewrite of Secret `agent-platform-values` ([README: Self-management](../README.md#self-management)). The kind example turns self-management off and stays with `helm upgrade`.

The kind example, from a checkout of this repository:

```bash
kind create cluster
kubectl apply -f https://github.com/kubernetes-sigs/gateway-api/releases/download/v1.5.0/standard-install.yaml
kubectl apply -f tests/ats/lab-dex.yaml
kubectl -n agent-platform wait --for=condition=complete job/lab-dex-cert-gen --timeout=5m
kubectl -n kube-system rollout restart deployment coredns
helm install agent-platform oci://gsoci.azurecr.io/charts/giantswarm/agent-platform \
  --namespace agent-platform -f helm/agent-platform/examples/kind-lab-dex.yaml --wait --timeout 10m
kubectl -n agent-platform port-forward svc/muster 8090:8090   # muster on http://localhost:8090/mcp
```

## 5. After the install

The install prints its next steps: the URLs, and the redirect URIs to register for exactly the components it turned on. `helm get notes agent-platform -n agent-platform` prints them again. Then:

```bash
kubectl -n agent-platform get helmreleases            # every component Ready
curl -s https://muster.platform.example.com/.well-known/oauth-protected-resource   # names your issuer
```

Point an MCP client at `https://muster.<domain>/mcp`; it registers itself and sends you through the sign-in.

Upgrades, and what an operator does when a release changes CRDs: [UPGRADE.md](../UPGRADE.md). Removing the platform: [README: Uninstalling](../README.md#uninstalling).
