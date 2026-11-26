#!/usr/bin/env bash
set -euo pipefail

render=$1
mode=$2
expected='Create
LateInitialize
Observe
Update'
if [[ $mode == observe ]]; then
  expected=Observe
fi

for bucket in giantswarm-ci-kagent-pg giantswarm-ci-substrate; do
  for kind in Bucket BucketPublicAccessBlock BucketPolicy BucketLifecycleConfiguration Role; do
    document=$(python3 tests/pick-doc.py "$render" "$kind" "$bucket")
    policies=$(awk '/^  managementPolicies:/{selected=1; next} selected && /^  [a-zA-Z]/{exit} selected' <<<"$document" |
      sed -n 's/^ *- *"\{0,1\}\([^" ]*\)"\{0,1\}$/\1/p' | sort)
    case "$kind" in
      Bucket|BucketPublicAccessBlock|BucketPolicy)
        grep -q '^    helm.sh/resource-policy: keep$' <<<"$document"
        [[ $policies == "$expected" ]] || { echo "FAIL: $kind/$bucket can delete or has the wrong management policies" >&2; exit 1; }
        ;;
      *)
        if grep -q 'helm.sh/resource-policy:' <<<"$document"; then
          echo "FAIL: $kind/$bucket is retained" >&2
          exit 1
        fi
        expected_transient='*'
        if [[ $mode == observe ]]; then expected_transient=Observe; fi
        [[ $policies == "$expected_transient" ]] || { echo "FAIL: $kind/$bucket has the wrong management policies" >&2; exit 1; }
        ;;
    esac
    if [[ $kind == BucketPublicAccessBlock ]]; then
      for flag in blockPublicAcls blockPublicPolicy ignorePublicAcls restrictPublicBuckets; do
        grep -q "^    $flag: true$" <<<"$document"
      done
    elif [[ $kind == BucketPolicy ]]; then
      grep -q '"aws:SecureTransport": "false"' <<<"$document"
      grep -q '"Effect": "Deny"' <<<"$document"
    fi
  done
done
echo "S3 retention verified ($mode)."
