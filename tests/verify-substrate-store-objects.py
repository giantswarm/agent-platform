#!/usr/bin/env python3
"""List the objects of Agent Substrate's snapshot store in a rendered chart, by kind and name.

The store (kagent.harness.snapshotStore.crossplane) renders Crossplane managed
resources: on AWS the Bucket quartet and the IAM Role, on CAPZ the storage
Account, Container, UserAssignedIdentity and FederatedIdentityCredential. An
object is the store's own when its apiVersion group, kind and name all match;
an unrelated object that merely carries `agent-platform-substrate` in its name
or tags is not.

Usage: verify-substrate-store-objects.py RENDER
Prints one `kind name` line per store object and exits 1 when there is any, 0
when there is none.
"""
import sys

import yaml

KINDS = {
    "s3.aws.upbound.io": ("Bucket", "BucketLifecycleConfiguration", "BucketPublicAccessBlock", "BucketPolicy"),
    "iam.aws.upbound.io": ("Role",),
    "storage.azure.upbound.io": ("Account", "Container"),
    "managedidentity.azure.upbound.io": ("UserAssignedIdentity", "FederatedIdentityCredential"),
}
# The CI store (ci/test-substrate-store-{aws,capz}-values.yaml).
NAMES = ("giantswarm-ci-substrate", "giantswarmcisubstrate", "giantswarm-ci-substrate-identity")


def main(path):
    found = []
    with open(path) as f:
        for doc in yaml.safe_load_all(f):
            if not isinstance(doc, dict):
                continue
            group = str(doc.get("apiVersion", "")).split("/")[0]
            name = (doc.get("metadata") or {}).get("name")
            if doc.get("kind") in KINDS.get(group, ()) and name in NAMES:
                found.append(f"{doc['kind']} {name}")
    print("\n".join(found))
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
