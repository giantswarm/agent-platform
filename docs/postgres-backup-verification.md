# Verify a PostgreSQL backup with a disposable restore

This is an optional one-off check for installations using
`postgres.backup.method: plugin` with an S3-backed Barman Cloud ObjectStore.
It uses [cnpg-drill](https://github.com/danielgaskins/cnpg-drill) chart 0.1.6 to
restore an eligible completed backup, run read-only SQL assertions, and remove
the recovery Cluster and PVCs. The release stays suspended afterward.

## Before running

- Confirm the CNPG operator, Barman Cloud plugin, source Cluster and archive are
  healthy, with a completed plugin Backup within the required recovery window.
  The example uses 25 hours for a daily backup; choose the actual requirement.
- Check capacity for another database copy. Review the source storage class,
  image pull Secrets and extension images. ImageVolume extensions need a
  compatible Kubernetes/container runtime and PostgreSQL image. Confirm the
  source and `<source>-restore` ImageVolume policy exception is installed on
  clusters enforcing volume restrictions. For source-specific affinity or node
  placement, use the native recovery procedure with that placement; cnpg-drill
  does not copy those settings.
- Provision a separate identity that can list the archive and read base backups
  and WAL, including KMS decryption if needed. Verify reads succeed and uploads
  and deletes are denied. Place its credentials in a Secret in the source
  namespace with keys `accessKey` and `secretKey`; use the installation's Secret
  manager. The commands below never print Secret values.
- Review [the values example](examples/postgres-restore-check-values.yaml).
  Its `kagent_v2`/`public` queries target the API v2 Goose migration histories,
  not the retained 0.10 database. Adjust names for the installation. The saved
  conversation check intentionally fails for an empty database: choose a known
  application-data invariant appropriate to the backup instead of removing it.
  The migration checks establish initialized histories, not an exact schema
  version. Add version or data assertions for the recovery requirement.
- Reserve `<sourceCluster>-restore` for one scheduler. This is the name selected
  by the existing CNPG network policies. Do not run this Job alongside another
  native or scheduled restore using that name. A leftover Cluster stops the
  drill; inspect it before retrying.

Use Bash, kubectl, Helm and jq. Set the readable kubeconfig used by both clients
and the installation's context. Run the following steps in the same shell:

```bash
set -euo pipefail
export KUBECONFIG="$HOME/.kube/config"
export DRILL_CONTEXT="REPLACE_WITH_CONTEXT"
export DRILL_NAMESPACE=kagent
export SOURCE_CLUSTER=kagent-pg
export RECOVERY_STORE=kagent-pg-recovery-readonly
export RECOVERY_SECRET=kagent-pg-recovery-s3
export DRILL_RELEASE=pg-backup-check
export DRILL_JOB="pg-backup-check-$(date -u +%Y%m%d%H%M%S)"
export DRILL_VALUES=docs/examples/postgres-restore-check-values.yaml
```

Keep the release name short and dedicated. Use a new Job name for a new drill.
For installations with namespace-wide egress restrictions, allow the drill Job
access to the Kubernetes API. The recovery Pods need API and archive access
under the existing CNPG policies. Do not add general network access.

## 1. Create a read-only recovery ObjectStore

Discover the source's writer store and copy its archive configuration, replacing
its credential references. This example uses static S3 keys. It does not change
the source Cluster, writer store, retention policy, or writer identity.

```bash
set -euo pipefail
WRITER_STORE=$(kubectl --context "$DRILL_CONTEXT" -n "$DRILL_NAMESPACE" \
  get clusters.postgresql.cnpg.io "$SOURCE_CLUSTER" -o json |
  jq -er '[.spec.plugins[]? | select(.name == "barman-cloud.cloudnative-pg.io" and .enabled != false)] |
    if length == 1 then .[0].parameters.barmanObjectName else error("Expected one Barman plugin") end')
[ "$RECOVERY_STORE" != "$WRITER_STORE" ]
kubectl --context "$DRILL_CONTEXT" -n "$DRILL_NAMESPACE" \
  get objectstores.barmancloud.cnpg.io "$WRITER_STORE" -o json |
  jq -e --arg ns "$DRILL_NAMESPACE" --arg name "$RECOVERY_STORE" --arg secret "$RECOVERY_SECRET" '
    if (.spec.configuration.destinationPath | startswith("s3://")) then
      {apiVersion, kind, metadata: {name: $name, namespace: $ns},
       spec: {configuration: (.spec.configuration |
         del(.s3Credentials, .azureCredentials, .googleCredentials) |
         .s3Credentials = {
           accessKeyId: {name: $secret, key: "accessKey"},
           secretAccessKey: {name: $secret, key: "secretKey"}})}}
    else error("This example requires an S3 archive") end' > recovery-objectstore.json
kubectl --context "$DRILL_CONTEXT" -n "$DRILL_NAMESPACE" \
  apply --dry-run=server -f recovery-objectstore.json
kubectl --context "$DRILL_CONTEXT" -n "$DRILL_NAMESPACE" \
  apply -f recovery-objectstore.json
```

For IRSA, provision a separate read-only role trusted by
`system:serviceaccount:<namespace>:<sourceCluster>-restore`. Set the recovery
store's `s3Credentials` to `inheritFromIAMRole: true` and supply that role's ARN
in `recoveryServiceAccountAnnotations.eks.amazonaws.com/role-arn` in the values
file. The Crossplane-generated writer role also trusts restore names, but its
write permissions make it unsuitable for this read-only check. Do not copy it.
Verify the recovery role's access before running. AWS authentication has not
been exercised by the local test described below.

## 2. Preview and install the suspended check

Render chart 0.1.6 and review its image digest, namespace Role, SQL assertions,
source Cluster and recovery store. Then install the dedicated release:

```bash
set -euo pipefail
helm template "$DRILL_RELEASE" oci://ghcr.io/danielgaskins/charts/cnpg-drill \
  --version 0.1.6 --namespace "$DRILL_NAMESPACE" --values "$DRILL_VALUES" \
  --set-string cluster="$SOURCE_CLUSTER" \
  --set-string drillClusterName="${SOURCE_CLUSTER}-restore" \
  --set-string recoveryObjectStore="$RECOVERY_STORE" --set suspended=true \
  --set retainOnFailure=false > recovery-check.yaml
kubectl --context "$DRILL_CONTEXT" -n "$DRILL_NAMESPACE" \
  apply --dry-run=server -f recovery-check.yaml
helm upgrade --install "$DRILL_RELEASE" oci://ghcr.io/danielgaskins/charts/cnpg-drill \
  --version 0.1.6 --namespace "$DRILL_NAMESPACE" --values "$DRILL_VALUES" \
  --set-string cluster="$SOURCE_CLUSTER" \
  --set-string drillClusterName="${SOURCE_CLUSTER}-restore" \
  --set-string recoveryObjectStore="$RECOVERY_STORE" --set suspended=true \
  --set retainOnFailure=false --kube-context "$DRILL_CONTEXT" --wait --timeout 5m
kubectl --context "$DRILL_CONTEXT" -n "$DRILL_NAMESPACE" \
  get cronjob "${DRILL_RELEASE}-cnpg-drill" -o json |
  jq -e '.spec.suspend == true and .spec.concurrencyPolicy == "Forbid"'
```

## 3. Run and validate one restore

```bash
set -euo pipefail
kubectl --context "$DRILL_CONTEXT" -n "$DRILL_NAMESPACE" \
  create job "$DRILL_JOB" --from="cronjob/${DRILL_RELEASE}-cnpg-drill"
drill_deadline=$((SECONDS + 2400))
while true; do
  drill_state=$(kubectl --context "$DRILL_CONTEXT" -n "$DRILL_NAMESPACE" \
    get job "$DRILL_JOB" -o json | jq -r '
      if any(.status.conditions[]?; .type == "Complete" and .status == "True") then "complete"
      elif any(.status.conditions[]?; .type == "Failed" and .status == "True") then "failed"
      else "pending" end')
  [ "$drill_state" = pending ] || break
  [ "$SECONDS" -lt "$drill_deadline" ] || { echo "Drill timed out; inspect the Job and recovery resources" >&2; exit 1; }
  sleep 5
done
kubectl --context "$DRILL_CONTEXT" -n "$DRILL_NAMESPACE" \
  logs "job/$DRILL_JOB" -c drill > recovery-result.json
jq -e -s 'length == 1 and (.[0] |
  .source == (env.DRILL_NAMESPACE + "/" + env.SOURCE_CLUSTER) and
  .drillCluster == (env.SOURCE_CLUSTER + "-restore") and
  .status == "passed" and .cleanup == "cluster-and-pvcs-deleted" and
  (.checks | length > 0) and all(.checks[]; .passed == true))' recovery-result.json
```

A pass covers the selected backup, archive path and configured assertions. It
does not establish regional recovery, restore all Substrate snapshot content,
or compare every application row. The report hashes query results. Save it
with the backup ID, source UID, recovery time and any PITR target.

## 4. Inspect cleanup and keep the report

Confirm `<sourceCluster>-restore`, its Pods and PVCs are gone. Inspect backing
PVs or cloud disks under the storage reclaim policy, and recheck source health.
Keep the Job until its report is saved. The dedicated release remains suspended;
remove it with `helm uninstall` if no longer needed. Do not unsuspend it until
the data checks, credential restrictions, storage cleanup and costs are reviewed.

If the Job fails or times out, save its logs and treat the drill as failed.
`archive_access_denied` and `wal_unavailable` need archive investigation. A
missing report after termination is incomplete evidence. Inspect leftovers
before retrying; remove only confirmed recovery resources, never the source.
A leftover Cluster contains a copy of application data. Reusing a fixed name
before the previous run's storage cleanup finishes can fail that cleanup check.

## Validation scope

The commands and values were tested with a local PostgreSQL fixture on Kubernetes
1.35.8, CNPG 1.30.1, Barman Cloud plugin 0.15.0, PostgreSQL 18.6, pgvector 0.8.2
and RustFS 1.0.0. The fixture applied the API v2 baseline SQL, seeded migration
tracking records and a synthetic conversation context; it did not run a kagent
controller. The published cnpg-drill chart 0.1.6 restored that data and removed
the temporary resources. Giant Swarm's IAM, Cilium policies, admission rules,
private image registry and cloud storage were not tested live. Repeat the first
run in the installation before relying on this result.
