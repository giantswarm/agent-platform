#!/usr/bin/env bash
set -euo pipefail

usage() {
  echo "Usage: $0 --context <context> --bucket <bucket> [--apply]"
}

fail() {
  echo "$*" >&2
  exit 1
}

context=''
bucket=''
apply=false
while (($#)); do
  case "$1" in
    --context|--bucket)
      (($# >= 2)) && [[ -n $2 && $2 != --* ]] || { usage >&2; exit 2; }
      case "$1" in
        --context) context=$2 ;;
        --bucket) bucket=$2 ;;
      esac
      shift 2
      ;;
    --apply) apply=true; shift ;;
    --help|-h) usage; exit 0 ;;
    *) usage >&2; exit 2 ;;
  esac
done
[[ -n $context && -n $bucket && $context != -* && $bucket != -* ]] || { usage >&2; exit 2; }
command -v kubectl >/dev/null || fail 'kubectl is required'
command -v jq >/dev/null || fail 'jq is required'
kubernetes=(kubectl --context "$context" --request-timeout=20s)
resource_type=bucketpublicaccessblocks.s3.aws.upbound.io

bucket_resource=$("${kubernetes[@]}" get buckets.s3.aws.upbound.io "$bucket" -o json)
jq -e --arg bucket "$bucket" '
  .metadata.deletionTimestamp == null and
  .metadata.annotations["crossplane.io/external-name"] == $bucket and
  .metadata.annotations["helm.sh/resource-policy"] == "keep" and
  (.spec.managementPolicies | type == "array" and length > 0 and
    index("Delete") == null and index("*") == null)
' <<<"$bucket_resource" >/dev/null || fail 'Bucket must be retained by Helm and protected from Crossplane deletion'

block=$("${kubernetes[@]}" get "$resource_type" "$bucket" --ignore-not-found -o json)
if [[ -z $block ]]; then
  echo 'Public access block managed resource is absent; bucket is retained. Verify the AWS block directly.'
  exit 0
fi
jq -e --arg bucket "$bucket" '
  .metadata.deletionTimestamp != null and
  .metadata.annotations["crossplane.io/external-name"] == $bucket and
  .metadata.labels["app.kubernetes.io/name"] == "agent-platform-connectivity" and
  .spec.forProvider.bucketRef.name == $bucket and
  ([.spec.forProvider, .status.atProvider] | all(.[];
    .blockPublicAcls == true and .blockPublicPolicy == true and
    .ignorePublicAcls == true and .restrictPublicBuckets == true)) and
  any(.status.conditions[]?;
    .status == "False" and
    ((.message // "") | contains("s3:PutBucketPublicAccessBlock") and
      contains("explicit deny in a service control policy")))
' <<<"$block" >/dev/null || fail 'Block must be terminating, chart-owned, fully enabled and blocked by an SCP'

patch=$(jq -c '[
  {op: "test", path: "/metadata/uid", value: .metadata.uid},
  {op: "test", path: "/metadata/resourceVersion", value: .metadata.resourceVersion},
  {op: "add", path: "/spec/deletionPolicy", value: "Orphan"},
  {op: "add", path: "/spec/managementPolicies", value: ["Observe"]}
]' <<<"$block")

echo "Context: $context; bucket: $bucket"
"${kubernetes[@]}" patch "$resource_type" "$bucket" --type=json -p "$patch" --dry-run=server -o name
if ! $apply; then
  echo 'Dry run passed. Add --apply to orphan the AWS block and release its managed resource.'
  exit 0
fi
"${kubernetes[@]}" patch "$resource_type" "$bucket" --type=json -p "$patch" -o name
"${kubernetes[@]}" wait --for=delete "$resource_type/$bucket" --timeout=120s
"${kubernetes[@]}" get buckets.s3.aws.upbound.io "$bucket" -o name
echo 'Managed resource removed; bucket retained. Verify the AWS public access block directly and check the alert.'
