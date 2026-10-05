# Cut-over to the kagent line on `api.kagent.dev` (gazelle, glean, graveler)

A checklist for one installation at a time. Nothing here runs by itself; every step is an operator action, and every `kubectl` call names its kubeconfig and context.

## What changes, and why there is downtime

- The kagent line moves from the API group `kagent.dev` to `api.kagent.dev` (version `v1alpha3` unchanged). An agent is now an `Agent` that names its `Harness` (`spec.harnessRef`) and carries its `AgentTemplate` inline or by `templateRef`. A conversation is a `Session` of an `Agent`; the controller serves `SessionService` in place of `AgentInstanceService`.
- The kagent-crds chart of the line renders the `api.kagent.dev` CRDs only. The `kagent.dev` CRDs carry no `helm.sh/resource-policy: keep`, so Helm deletes them when the kagent-crds release upgrades, and the API server deletes every `kagent.dev` object with them: AgentTemplates, the Harness, ModelConfigs, ModelProviderConfigs, RemoteMCPServers. Agents are unavailable from this step until the producers have re-created their objects at `api.kagent.dev` and the controller is back.
- The line rewrites its initial database migration (`000001_initial.sql`) in place. There is no forward migration: the controller refuses a database that holds the 1.x schema. The kagent database of the installation is dropped and re-created; every session, session share, scheduled-run execution and the klaus-gateway Slack thread bindings that point at sessions are lost. Agents, templates, ModelConfigs and schedules are Kubernetes objects and come back with their producers.
- The meta chart release that selects the line retires the storage-version hooks of the 3.x to 4.x cut-over. Apply it before or with the CRD step: the retired restore hook waited on `modelconfigs.kagent.dev`, which does not exist after the cut-over.

## Before you start

- [ ] Fill in the versions below once the tags exist. `1.3.0` is the first release of giantswarm/kagent-upstream on upstream `bf8afa56`; `1.4.0` is the Substrate release it was built against (the kagent WorkerPool's worker image is `ateom-gvisor:1.4.0`); `<META_VERSION>` is the agent-platform release whose `components.kagent.versionRange`, `components.kagent-crds.versionRange`, `components.substrate.versionRange` and `components.substrate-crds.versionRange` select them.
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

- [ ] Move the installation to `<META_VERSION>` (the meta chart's own `versionRange`, or the installation's pin of it in its gitops repository). That release raises `components.kagent-crds.versionRange` to `1.3.0` and retires the storage-version hooks.
- [ ] Wait for the kagent-crds release and check the CRDs: the seven `api.kagent.dev` CRDs established, the five `kagent.dev` CRDs gone.

```sh
kubectl --kubeconfig <kubeconfig> --context <context> -n agent-platform wait helmrelease/kagent-crds --for=condition=Ready --timeout=10m
kubectl --kubeconfig <kubeconfig> --context <context> get crd | grep -E '\.(api\.)?kagent\.dev'
```

Expected: `agents`, `agenttemplates`, `harnesses`, `modelconfigs`, `modelproviderconfigs`, `remotemcpservers`, `sandboxtemplates` under `api.kagent.dev`, nothing under `kagent.dev`. If a `kagent.dev` CRD is still there (an object with a finalizer holds it), find the object and remove the finalizer.

### 4. Flip the producers to the new Agent shape

The same meta chart release moves them; this step is verification.

- [ ] agent-manager Ready at its new version, and its `Agent`s re-created from the agents' HelmReleases:

```sh
kubectl --kubeconfig <kubeconfig> --context <context> -n agent-platform wait helmrelease/agent-manager --for=condition=Ready --timeout=10m
kubectl --kubeconfig <kubeconfig> --context <context> -n kagent get helmreleases.helm.toolkit.fluxcd.io
kubectl --kubeconfig <kubeconfig> --context <context> -n kagent get agents.api.kagent.dev
```

Every agent HelmRelease of the Generic agent chart must be on the chart line that renders an `Agent` (the `OCIRepository agent` semver range agent-manager composes); a release still on the old range renders an `AgentTemplate` the controller does not run.

- [ ] The connectivity release re-rendered the catalog at `api.kagent.dev` (ModelConfigs, RemoteMCPServers) and the kagent release its Harness:

```sh
kubectl --kubeconfig <kubeconfig> --context <context> -n kagent get modelconfigs.api.kagent.dev,remotemcpservers.api.kagent.dev,harnesses.api.kagent.dev
```

- [ ] Backstage at the version that reads `Agent`s and `Session`s (the devportal HelmRelease in its own namespace).

### 5. kagent controller on a fresh database, with the new Substrate worker image

- [ ] Add the fresh database to the connectivity chart's values for the installation (the agent-platform#346 pattern: a new CNPG `Database` in `postgres.databases`, a new name such as `kagent_v2`, and the kagent release's `KAGENT_POSTGRES_DATABASE_URL` pointing at it). Keep the old database in place for the retention period.
- [ ] Wait for the substrate and kagent releases; the WorkerPool's worker image is `ateom-gvisor:1.4.0` and the controller logs its migrations on the empty database instead of refusing a 1.x schema:

```sh
kubectl --kubeconfig <kubeconfig> --context <context> -n agent-platform wait helmrelease/substrate helmrelease/kagent --for=condition=Ready --timeout=15m
kubectl --kubeconfig <kubeconfig> --context <context> -n kagent get workerpools.ate.dev kagent-default -o jsonpath='{.spec.workerImage}{"\n"}'
kubectl --kubeconfig <kubeconfig> --context <context> -n kagent logs deployment/kagent-controller --tail=100 | grep -i -E 'migrat|schema'
```

A controller that logs the 1.x refusal is still pointed at the old database; fix the URL before anything else.

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
