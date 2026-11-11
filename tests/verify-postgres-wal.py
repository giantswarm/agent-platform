#!/usr/bin/env python3
"""Check the rendered CNPG retention contract across the extension modes."""
import sys

import yaml

if sys.argv[1] == "--connectivity-values":
    with open(sys.argv[2]) as stream:
        releases = [
            doc for doc in yaml.safe_load_all(stream)
            if doc and doc.get("kind") == "HelmRelease"
            and doc["metadata"]["name"] == "agent-platform-connectivity"
        ]
    assert len(releases) == 1, "expected exactly one connectivity release"
    yaml.safe_dump(releases[0]["spec"]["values"], sys.stdout)
    sys.exit(0)

with open(sys.argv[1]) as stream:
    clusters = [
        doc for doc in yaml.safe_load_all(stream)
        if doc and doc.get("kind") == "Cluster"
        and doc.get("apiVersion") == "postgresql.cnpg.io/v1"
    ]
assert len(clusters) == 1, "expected exactly one PostgreSQL cluster"
parameters = clusters[0]["spec"].get("postgresql", {}).get("parameters", {})
actual = parameters.get("max_slot_wal_keep_size", "absent")
assert actual == sys.argv[2], f"WAL retention: expected {sys.argv[2]}, got {actual}"
if len(sys.argv) > 3:
    extensions = clusters[0]["spec"].get("postgresql", {}).get("extensions", [])
    assert [extension["name"] for extension in extensions] == [sys.argv[3]], \
        "slot retention must preserve the image-volume extension"
print(f"PostgreSQL WAL retention: {actual}")
