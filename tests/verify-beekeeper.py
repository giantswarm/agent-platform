#!/usr/bin/env python3
"""Beekeeper is opt-in; its authenticated registration follows its release."""
import importlib.util
import sys

import yaml

spec = importlib.util.spec_from_file_location("identity", "tests/verify-manager-identity.py")
identity = importlib.util.module_from_spec(spec)
spec.loader.exec_module(identity)
meta, conn, *base = sys.argv[1:]
base += identity.LOGIN + identity.sets("global.domain=ci.example.com")
rels = identity.releases(meta, base)
assert "beekeeper" not in rels
forwarded = rels["agent-platform-connectivity"]
assert "beekeeper" not in forwarded
assert "beekeeper" not in forwarded["components"]
flags = base + identity.sets("beekeeper.enabled=true", "beekeeper.serve.organization=example", "beekeeper.serve.teams.example=operators")
rels = identity.releases(meta, flags)
child = rels["beekeeper"]
assert "enabled" not in child and "global" not in child
assert child["serve"]["issuer"] == identity.ISSUER
assert child["serve"]["clientIDs"] == [identity.CLIENT]
assert child["serve"]["teams"] == {"example": "operators"}
assert child["serve"]["muster"].endswith("/mcp")
result = identity.connectivity(conn, rels["agent-platform-connectivity"], [])
assert result.returncode == 0, result.stderr
servers = [d for d in yaml.safe_load_all(result.stdout) if d and d.get("kind") == "MCPServer" and d["metadata"]["name"] == "beekeeper"]
assert len(servers) == 1
assert servers[0]["spec"]["auth"] == {"type": "oauth", "forwardToken": True}
assert servers[0]["spec"]["url"] == "http://beekeeper.agent-platform.svc.cluster.local:8080/mcp"
custom = identity.releases(meta, flags + identity.sets("components.beekeeper.targetNamespace=coordination", "beekeeper.service.port=9090", "beekeeper.serve.clientIDs[0]=own-client"))
assert custom["beekeeper"]["serve"]["clientIDs"] == ["own-client"]
result = identity.connectivity(conn, custom["agent-platform-connectivity"], [])
assert result.returncode == 0, result.stderr
assert "http://beekeeper.coordination.svc.cluster.local:9090/mcp" in result.stdout
for extra, message in [(["components.beekeeper.enabled=false"], "disagrees"), (["components.muster.enabled=false", "components.agent-platform-mcps.enabled=false"], "requires components.muster.enabled")]:
    result = identity.helm(meta, flags + identity.sets(*extra))
    assert result.returncode != 0 and message in result.stderr, result.stderr
fleet = identity.releases(meta, flags + identity.sets("gitops.targetNamespace=platform", "components.muster.targetNamespace=tools"))
assert ".tools.svc.cluster.local:" in fleet["beekeeper"]["serve"]["muster"]
assert fleet["agent-platform-connectivity"]["beekeeper"]["namespace"] == "platform"
print("ok: optional beekeeper release, values, identity, registration and guards")
