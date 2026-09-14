#!/usr/bin/env python3
"""Assert the gateway's Valkey routing-store defaults (giantswarm/klaus-gateway#252).

With klausGateway.routing.store: valkey the meta chart fills the klaus-gateway
release's routing.valkey block from the platform's own Valkey: url from the
valkey release's Service, existingSecret and passwordKey from the Secret and
key its default user authenticates with. An operator's own value wins, and
with any other store nothing about Valkey is forwarded (the live klaus-gateway
chart of an installation still on 1.5.x would refuse the key).

Reads a rendered meta-package manifest (stdout of `helm template`, PyYAML).
Modes: --memory (no routing.valkey forwarded), --defaults (all three filled),
--own (the operator's url and Secret kept, the key filled).
"""

import sys

import yaml


def gateway_values(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        for doc in yaml.safe_load_all(f):
            if not doc or doc.get("kind") != "HelmRelease":
                continue
            if doc["metadata"]["name"] == "klaus-gateway":
                return doc["spec"].get("values", {})
    sys.exit("FAIL: no klaus-gateway HelmRelease in the render")


def main() -> None:
    mode, path = sys.argv[1], sys.argv[2]
    routing = gateway_values(path).get("routing", {})
    valkey = routing.get("valkey")
    if mode == "--memory":
        if valkey is not None:
            sys.exit(f"FAIL: routing.store memory must forward no routing.valkey, got {valkey!r}")
        print("ok: memory store forwards no routing.valkey")
        return
    if valkey is None:
        sys.exit("FAIL: routing.store valkey without a routing.valkey block")
    want = {
        "--defaults": {"url": "muster-valkey:6379", "existingSecret": "platform-secrets", "passwordKey": "valkey-password"},
        "--own": {"url": "cache.example.internal:6380", "existingSecret": "my-valkey", "passwordKey": "valkey-password"},
    }[mode]
    got = {k: valkey.get(k) for k in want}
    if got != want:
        sys.exit(f"FAIL: routing.valkey {got!r}, want {want!r}")
    print(f"ok: routing.valkey = {got}")


if __name__ == "__main__":
    main()
