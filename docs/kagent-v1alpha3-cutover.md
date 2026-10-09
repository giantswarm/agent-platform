# Cut-over to the kagent line on `api.kagent.dev` (gazelle, glean, graveler)

A checklist for one installation at a time. Nothing here runs by itself; every step is an operator action, and every `kubectl` call names its kubeconfig and context.

## What changes, and why there is downtime

- The kagent line moves from the API group `kagent.dev` to `api.kagent.dev` (version `v1alpha3` unchanged). An agent is now an `Agent` that names its `Harness` (`spec.harnessRef`) and carries its `AgentTemplate` inline or by `templateRef`. A conversation is a `Session` of an `Agent`; the controller serves `SessionService` in place of `AgentInstanceService`.
- The kagent-crds chart of the line renders the `api.kagent.dev` CRDs only. The installed `kagent.dev` CRDs carry `helm.sh/resource-policy: keep`, so they and their objects (AgentTemplates, the Harness, ModelConfigs, ModelProviderConfigs, RemoteMCPServers) stay through the upgrade; nothing reads them any more, and step 7 deletes the six CRDs by hand after the retention period. Agents are unavailable from this step until the producers have re-created their objects at `api.kagent.dev` and the controller is back.
- The line rewrites its initial database migration (`000001_initial.sql`) in place. There is no forward migration: the controller refuses a database that holds the 1.x schema. The kagent database of the installation is dropped and re-created; every session, session share, scheduled-run execution and the klaus-gateway Slack thread bindings that point at sessions are lost. Agents, templates, ModelConfigs and schedules are Kubernetes objects and come back with their producers.
- The meta chart release that selects the line retires the storage-version hooks of the 3.x to 4.x cut-over. Apply it before or with the CRD step: the retired restore hook waited on `modelconfigs.kagent.dev`, which does not exist after the cut-over.

## Before you start

- [ ] Fill in the versions below once the tags exist. `1.4.0` is the first release of giantswarm/kagent-upstream on upstream `bf8afa56` whose quiescence fence the Substrate release takes; `1.5.0` is the Substrate release it was built against (the kagent WorkerPool's worker image is `ateom-gvisor:1.5.0`); `<META_VERSION>` is the agent-platform release whose `components.kagent.versionRange`, `components.kagent-crds.versionRange`, `components.substrate.versionRange` and `components.substrate-crds.versionRange` select them.
- [ ] Producers released and pinned in the meta chart: agent-manager (renders `Agent`, reads `Session`), the Generic agent chart (renders one `Agent` per release), Backstage (Agent and Session shapes), klaus-gateway (`SessionService`, the session id in `x-kagent-agent-instance-id`), agentlab.
- [ ] Per installation, record the current versions for the rollback table at the end:

```sh
kubectl --kubeconfig <kubeconfig> --context <context> -n agent-platform get helmreleases.helm.toolkit.fluxcd.io \
  kagent kagent-crds substrate substrate-crds agent-manager agent-platform-connectivity \
  -o custom-columns=NAME:.metadata.name,CHART:.status.history[0].chartVersion
kubectl --kubeconfig <kubeconfig> --context <context> -n <org namespace> get helmrelease agent-platform -o jsonpath='{.status.history[0].chartVersion}{"\n"}'
```

- [ ] Decide the retention period of the old database and the old Substrate objects (open question, see the end).

## Order, per installation

Run the whole list on gazelle first, then glean, then graveler.

### 1. Announce

- [ ] Post the window in the installation's channel and in the Agent Platform channel: every conversation is lost, agents are unavailable between step 3 and step 6, Slack threads bound to a session stop answering and need a new thread.
- [ ] Suspend klaus-gateway's Slack channel traffic for the window if the installation runs it (scale the deployment to zero or suspend its HelmRelease), so no thread binds to a session that is about to vanish:

```sh
kubectl --kubeconfig <kubeconfig> --context <context> -n agent-platform scale deployment klaus-gateway --replicas=0
```

### 2. Back up and record

- [ ] Take a CNPG backup of the kagent-pg cluster, or a `pg_dump` of the kagent database, and record where it landed:

```sh
kubectl --kubeconfig <kubeconfig> --context <context> -n agent-platform apply -f - <<'YAML'
apiVersion: postgresql.cnpg.io/v1
kind: Backup
metadata:
  name: kagent-pre-v1alpha3
spec:
  cluster:
    name: kagent-pg
YAML
kubectl --kubeconfig <kubeconfig> --context <context> -n agent-platform wait backup/kagent-pre-v1alpha3 --for=jsonpath='{.status.phase}'=completed --timeout=30m
```

- [ ] Export the `kagent.dev` objects for reference (they are deleted in step 3; the producers re-create them at `api.kagent.dev`, this copy is for comparison and for the rollback):

```sh
kubectl --kubeconfig <kubeconfig> --context <context> -n kagent get agenttemplates.kagent.dev,harnesses.kagent.dev,modelconfigs.kagent.dev,modelproviderconfigs.kagent.dev,remotemcpservers.kagent.dev -o yaml > kagent-dev-objects-<installation>.yaml
```

- [ ] Record the Substrate objects the old database references (ActorTemplates, Actors), for the cleanup in step 7:

```sh
kubectl --kubeconfig <kubeconfig> --context <context> -n kagent get actortemplates.ate.dev,actors.ate.dev -o name > substrate-objects-<installation>.txt
```

### 3. Apply the CRD chart (and the meta chart release that selects the line)

- [ ] Move the installation to `<META_VERSION>` (the meta chart's own `versionRange`, or the installation's pin of it in its gitops repository). That release raises `components.kagent-crds.versionRange` to `1.4.0` and retires the storage-version hooks.
- [ ] Wait for the kagent-crds release and check the CRDs: the seven `api.kagent.dev` CRDs established with `helm.sh/resource-policy: keep`; the six `kagent.dev` CRDs still present (step 7 deletes them).

```sh
kubectl --kubeconfig <kubeconfig> --context <context> -n agent-platform wait helmrelease/kagent-crds --for=condition=Ready --timeout=10m
kubectl --kubeconfig <kubeconfig> --context <context> get crd | grep -E '\.(api\.)?kagent\.dev'
```

Expected: `agents`, `agenttemplates`, `harnesses`, `modelconfigs`, `modelproviderconfigs`, `remotemcpservers`, `sandboxtemplates` under `api.kagent.dev`; the `kagent.dev` CRDs still listed until step 7.

### 4. Flip the producers to the new Agent shape

The same meta chart release moves them; this step is verification.

- [ ] agent-manager Ready at its new version, and its `Agent`s re-created from the agents' HelmReleases:

```sh
kubectl --kubeconfig <kubeconfig> --context <context> -n agent-platform wait helmrelease/agent-manager --for=condition=Ready --timeout=10m
kubectl --kubeconfig <kubeconfig> --context <context> -n kagent get helmreleases.helm.toolkit.fluxcd.io
kubectl --kubeconfig <kubeconfig> --context <context> -n kagent get agents.api.kagent.dev
```

Every agent HelmRelease of the Generic agent chart must be on the chart line that renders an `Agent` (the `OCIRepository agent` semver range agent-manager composes); a release still on the old range renders an `AgentTemplate` the controller does not run.

- [ ] The connectivity release started the `agent-manager migrate` run (agent-manager 1.10.0 or later; an older image reads the 2.x range as the 0.x to 1.x path and rewrites nothing). It validates every Generic-chart release against the chart 2.x schema, rewrites the writable ones, moves the namespace's `OCIRepository agent` to the 2.x range, and reports a diff for each GitOps-owned release and source (apply those in their repository). Read the report:

```sh
kubectl --kubeconfig <kubeconfig> --context <context> -n kagent get jobs -l app.kubernetes.io/component=agent-manager-migrate
kubectl --kubeconfig <kubeconfig> --context <context> -n kagent get configmap agent-manager-migrate-report -o jsonpath='{.data.phase}{"\n"}{.data.summary}{"\n"}'
kubectl --kubeconfig <kubeconfig> --context <context> -n kagent get configmap agent-manager-migrate-report -o jsonpath='{.data.report\.yaml}'
```

Expected: `run.path: 1.x -> 2.x`, no release `failed`, phase `wait` until Flux upgrades the releases and the Agents are Ready, then `complete`. A run that ended in `wait` is re-run once the Agents are Ready, which deletes the leftover `kagent.dev` AgentTemplates no release renders: `kubectl -n kagent create job --from=cronjob/agent-platform-connectivity-agent-manager-migrate agent-manager-migrate-rerun-1`. A release's `warnings` name a sub-agent `templateRef` with no `api.kagent.dev` AgentTemplate in the namespace; the parent Agent stays `ResolvedRefs=False` until one exists.

- [ ] Hand-written kagent objects in the installation's gitops repository (ModelConfigs, ModelProviderConfigs, RemoteMCPServers, AgentTemplates the charts do not render) move to `apiVersion: api.kagent.dev/v1alpha3` in the same change: the line's controller resolves only that group, so an Agent naming a `kagent.dev` ModelConfig stays `ResolvedRefs=False` (ModelConfig not found). The schemas are the same; `api.kagent.dev` adds `stream`.
- [ ] The connectivity release re-rendered the catalog at `api.kagent.dev` (ModelConfigs, RemoteMCPServers) and the kagent release its Harness:

```sh
kubectl --kubeconfig <kubeconfig> --context <context> -n kagent get modelconfigs.api.kagent.dev,remotemcpservers.api.kagent.dev,harnesses.api.kagent.dev
```

- [ ] Backstage at the version that reads `Agent`s and `Session`s (the devportal HelmRelease in its own namespace).

### 5. kagent controller on a fresh database, with the new Substrate worker image

- [ ] Suspend the kagent HelmRelease before the configs merge (`kubectl patch hr kagent --type merge -p '{"spec":{"suspend":true}}'`, check `spec.suspend` reads true): otherwise the controller still running the old line lays the 1.x schema on the fresh database before the new one starts.
- [ ] A dev meta chart pin (an rc or a branch build) needs the dev-channel `semverFilter` under `gitops.prereleases`, or the range resolves to nothing.
- [ ] Add the fresh database to the connectivity chart's values for the installation (the agent-platform#346 pattern: a new CNPG `Database` in `postgres.databases`, a new name such as `kagent_v2`, and the kagent release's `KAGENT_POSTGRES_DATABASE_URL` pointing at it). Keep the old database in place for the retention period.
- [ ] An installation on the kagent chart's bundled Postgres (no CNPG Cluster) points `kagent.database.postgres.url` at another database of the bundled instance instead (the empty maintenance database `postgres`, the password from `kagent.controller.envFrom` on the Secret `kagent-postgresql`); UPGRADE.md, "4.120.x → 4.121.0 and later", has the values.
- [ ] Wait for the substrate and kagent releases; the WorkerPool's worker image is `ateom-gvisor:1.5.0` and the controller logs its migrations on the empty database instead of refusing a 1.x schema:

```sh
kubectl --kubeconfig <kubeconfig> --context <context> -n agent-platform wait helmrelease/substrate helmrelease/kagent --for=condition=Ready --timeout=15m
kubectl --kubeconfig <kubeconfig> --context <context> -n kagent get workerpools.ate.dev kagent-default -o jsonpath='{.spec.workerImage}{"\n"}'
kubectl --kubeconfig <kubeconfig> --context <context> -n kagent logs deployment/kagent-controller --tail=100 | grep -i -E 'migrat|schema'
```

A controller that logs the 1.x refusal is still pointed at the old database; fix the URL before anything else.

- [ ] Substrate 1.5.x from a 1.3.x line needs a fresh substrate database too: on the existing schema ate-api-server crash-loops (`relation tuple already exists`). On the platform's CNPG Cluster, add the fresh database and name it in `postgres.substrateDatabase` in the same values change as the kagent database; the previous `substrate` database stays (`reclaimPolicy: retain`) for the retention period:

```yaml
postgres:
  substrateDatabase: substrate-v2
  databases:
    substrate-v2:
      enabled: true
      name: substrate_v2
      component: substrate
      reclaimPolicy: retain
      secretNamespaces: [ate-system]
```

The connectivity release derives `<postgres.clusterName>-substrate-v2-app` into `ate-system` and the meta chart hands it to ate-api-server, which lays its schema on the empty database. An installation on Substrate's bundled Postgres (no CNPG Cluster) recreates the schema by hand instead (`DROP SCHEMA public CASCADE; CREATE SCHEMA public; ALTER SCHEMA public OWNER TO kagent; GRANT ALL ON SCHEMA public TO public;` on the `substrate` database), deletes the crash-looping pod and waits for the substrate HelmRelease Ready.

- [ ] Bring klaus-gateway back (undo step 1's scale to zero) once the controller is Ready.

### 6. Verify with one Agent and one Session

- [ ] One `Agent` Ready per installation (an existing agent-manager agent, or a declarative one):

```sh
kubectl --kubeconfig <kubeconfig> --context <context> -n kagent get agents.api.kagent.dev
kubectl --kubeconfig <kubeconfig> --context <context> -n kagent get agent.api.kagent.dev <name> -o jsonpath='{range .status.conditions[*]}{.type}={.status} {.reason}{"\n"}{end}'
```

Expected: `Accepted=True`, `ResolvedRefs=True`, `Compatible=True`, `Ready=True`, and `status.latestSuccessfulRevision` set.

- [ ] One `Session` with one turn, through the public gateway as a person, with klaus-gateway (a message to the agent in Slack, a new thread) or with `grpcurl` against `SessionService`:

```sh
grpcurl -H "authorization: Bearer <token>" -d '{"agent":{"namespace":"kagent","name":"<name>"},"request_id":"cutover-1"}' \
  agentgateway.<installation domain>:443 kagent.api.v1alpha1.SessionService/CreateSession
```

Then one `SendMessage` on `lf.a2a.v1.A2AService` with the session's `context_id`, and `GetSession` showing `state: RUNTIME_STATE_READY`.

- [ ] Backstage: the agents list shows the Agent, a new session answers one message.
- [ ] Note the verification result and the time in the announcement thread.

### 7. Cleanup, after the retention period

- [ ] Drop the old kagent database (the `Database` object of the old name in `postgres.databases`, then the database itself) once the retention period has passed and nobody asked for a restore.
- [ ] Delete the Substrate objects the old database referenced (the list from step 2: ActorTemplates, parked Actors) and their snapshots and checkpoints in the snapshot store under the old Harness's prefix, if they are not referenced by a new revision. Compare with what the new controller created first:

```sh
kubectl --kubeconfig <kubeconfig> --context <context> -n kagent get actortemplates.ate.dev,actors.ate.dev -o name
```

- [ ] Confirm no `kagent.dev` CRD came back (a gitops repository re-applying an old manifest would re-create one):

```sh
kubectl --kubeconfig <kubeconfig> --context <context> get crd | grep '\.kagent\.dev$' || echo none
```

## Rollback

Possible until step 7 deletes the old database; after that only forward.

| Component | Previous version | How |
|---|---|---|
| agent-platform (meta chart) | `<previous META_VERSION>` from "Before you start" | re-pin the installation to it; the components' ranges follow |
| kagent-crds, kagent | `<previous>` | follow the meta chart; the `kagent.dev` CRDs are re-created by the kagent-crds release of that version (empty) |
| substrate, substrate-crds | `<previous>` | follow the meta chart |
| agent-manager, Generic agent chart range, Backstage, klaus-gateway | `<previous>` | follow the meta chart; agent-manager re-renders `AgentTemplate`s from the agents' HelmReleases |

- [ ] Point the kagent release back at the old database (undo step 5's URL change). The old database is intact: it was never migrated.
- [ ] If the old database was damaged, restore it from the backup of step 2 into a new CNPG cluster or database and point the controller at that.
- [ ] Re-create the `kagent.dev` objects that were not Helm-owned from the export of step 2 (declarative AgentTemplates, hand-made ModelConfigs); the Helm-owned ones come back with their releases.
- [ ] Sessions created on the new line are lost on a rollback the same way the old ones were lost on the way forward; say so in the announcement.

## Open questions

- Retention period of the old database and of the old Substrate objects (step 7). Suggest 14 days unless the installation's owner wants longer.
- Whether the klaus-gateway thread bindings should be exported before step 3 so the affected threads can be told their session is gone.
