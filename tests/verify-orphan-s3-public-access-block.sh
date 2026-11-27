#!/usr/bin/env bash
set -euo pipefail

scratch=$(mktemp -d)
trap 'rm -rf "$scratch"' EXIT
export ORPHAN_TEST_STATE=$scratch
mkdir "$scratch/bin"
cat >"$scratch/bucket.json" <<'JSON'
{"metadata":{"annotations":{"crossplane.io/external-name":"test-backups","helm.sh/resource-policy":"keep"}},"spec":{"managementPolicies":["Create","Update","LateInitialize","Observe"]}}
JSON
cat >"$scratch/block.json" <<'JSON'
{"metadata":{"uid":"test-uid","resourceVersion":"42","deletionTimestamp":"2026-01-01T00:00:00Z","annotations":{"crossplane.io/external-name":"test-backups"},"labels":{"app.kubernetes.io/name":"agent-platform-connectivity"}},"spec":{"forProvider":{"bucketRef":{"name":"test-backups"},"blockPublicAcls":true,"blockPublicPolicy":true,"ignorePublicAcls":true,"restrictPublicBuckets":true}},"status":{"atProvider":{"blockPublicAcls":true,"blockPublicPolicy":true,"ignorePublicAcls":true,"restrictPublicBuckets":true},"conditions":[{"status":"False","message":"s3:PutBucketPublicAccessBlock with an explicit deny in a service control policy"}]}}
JSON
cp "$scratch/block.json" "$scratch/original-block.json"
cp "$scratch/bucket.json" "$scratch/original-bucket.json"
cat >"$scratch/bin/kubectl" <<'BASH'
#!/usr/bin/env bash
set -euo pipefail
[[ $1 == --context && $2 == test-context && $3 == --request-timeout=20s ]]
shift 3
echo "$*" >>"$ORPHAN_TEST_STATE/calls"
case "$1" in
  get)
    if [[ $2 == buckets.s3.aws.upbound.io ]]; then
      cat "$ORPHAN_TEST_STATE/bucket.json"
    elif [[ ! -f $ORPHAN_TEST_STATE/deleted ]]; then
      cat "$ORPHAN_TEST_STATE/block.json"
    fi
    ;;
  patch)
    [[ $2 == bucketpublicaccessblocks.s3.aws.upbound.io && $3 == test-backups && $4 == --type=json && $5 == -p ]]
    jq -e '. == [
      {op:"test",path:"/metadata/uid",value:"test-uid"},
      {op:"test",path:"/metadata/resourceVersion",value:"42"},
      {op:"add",path:"/spec/deletionPolicy",value:"Orphan"},
      {op:"add",path:"/spec/managementPolicies",value:["Observe"]}
    ]' <<<"$6" >/dev/null
    if [[ ${ORPHAN_TEST_FAIL_DRY_RUN:-false} == true && " $* " == *' --dry-run=server '* ]]; then
      exit 1
    fi
    if [[ " $* " != *' --dry-run=server '* ]]; then
      touch "$ORPHAN_TEST_STATE/deleted"
    fi
    ;;
  wait) [[ -f $ORPHAN_TEST_STATE/deleted ]] ;;
  *) exit 1 ;;
esac
BASH
chmod +x "$scratch/bin/kubectl"
export PATH="$scratch/bin:$PATH"
script=hack/orphan-s3-public-access-block.sh

bash "$script" --context test-context --bucket test-backups >"$scratch/output"
[[ ! -f $scratch/deleted ]]
grep -q -- '--dry-run=server' "$scratch/calls"

for mutation in \
  'del(.metadata.deletionTimestamp)' \
  '.metadata.labels["app.kubernetes.io/name"] = "another-chart"' \
  '.metadata.annotations["crossplane.io/external-name"] = "another-bucket"' \
  '.spec.forProvider.bucketRef.name = "another-bucket"' \
  '.spec.forProvider.blockPublicAcls = false' \
  '.status.atProvider.restrictPublicBuckets = false' \
  '.status.conditions[0].message = "permission denied"'; do
  jq "$mutation" "$scratch/original-block.json" >"$scratch/block.json"
  : >"$scratch/calls"
  if bash "$script" --context test-context --bucket test-backups --apply >"$scratch/output" 2>&1; then
    echo "FAIL: accepted unsafe block: $mutation" >&2
    exit 1
  fi
  ! grep -q '^patch ' "$scratch/calls"
done
cp "$scratch/original-block.json" "$scratch/block.json"

for mutation in \
  '.metadata.deletionTimestamp = "2026-01-01T00:00:00Z"' \
  'del(.metadata.annotations["helm.sh/resource-policy"])' \
  '.spec.managementPolicies = ["*"]' \
  '.spec.managementPolicies = ["Observe","Delete"]' \
  '.spec.managementPolicies = []'; do
  jq "$mutation" "$scratch/original-bucket.json" >"$scratch/bucket.json"
  : >"$scratch/calls"
  if bash "$script" --context test-context --bucket test-backups --apply >"$scratch/output" 2>&1; then
    echo "FAIL: accepted unsafe bucket: $mutation" >&2
    exit 1
  fi
  ! grep -q '^patch ' "$scratch/calls"
done
cp "$scratch/original-bucket.json" "$scratch/bucket.json"

if ORPHAN_TEST_FAIL_DRY_RUN=true bash "$script" --context test-context --bucket test-backups --apply >"$scratch/output" 2>&1; then
  echo 'FAIL: ignored a rejected server-side dry run' >&2
  exit 1
fi
[[ ! -f $scratch/deleted ]]
bash "$script" --context test-context --bucket test-backups --apply >"$scratch/output"
[[ -f $scratch/deleted ]]
: >"$scratch/calls"
bash "$script" --context test-context --bucket test-backups --apply >"$scratch/output"
! grep -q '^patch ' "$scratch/calls"
if bash "$script" --bucket test-backups >"$scratch/output" 2>&1; then
  echo 'FAIL: accepted an implicit Kubernetes context' >&2
  exit 1
fi
echo 'Public access block remediation verified.'
