#!/usr/bin/env python3
"""Hold llmRouting.modelCatalog to an overlay of what the pinned gateway lacks.

The data plane always loads the price catalog its release embeds
(catalog/model-catalog.json of giantswarm/agentgateway-upstream at the release
tag) as the base; the chart's ConfigMap is a source merged over it per model
and per rate. An overlay entry for a model the base already prices keeps
winning after upstream moves the price, so every such entry is a failure: the
pull request that bumps the gateway to a release pricing the model has to drop
the entry.

Checked, in both charts' values.yaml:
- the data plane's tag (agentgateway.proxy.image.tag), the meta chart's
  controller and data-plane tags agree, since the catalog is the data plane's;
- no overlay provider/model is in the pinned release's built-in catalog;
- the platform's default model (kagent.providers.anthropic.model) is priced by
  the built-in catalog or the overlay;
- both charts carry the same overlay.

The catalog is fetched from GitHub at v<tag>; MODEL_CATALOG_FILE points at a
local copy for an offline run.
"""
import json
import os
import sys
import urllib.request

import yaml

CATALOG_URL = "https://raw.githubusercontent.com/giantswarm/agentgateway-upstream/v{tag}/catalog/model-catalog.json"


def fail(msg: str) -> None:
    sys.exit(f"FAIL: {msg}")


def load_catalog(tag: str) -> dict:
    path = os.environ.get("MODEL_CATALOG_FILE")
    if path:
        return json.load(open(path))
    with urllib.request.urlopen(CATALOG_URL.format(tag=tag), timeout=60) as r:
        return json.load(r)


def main(meta_dir: str, connectivity_dir: str) -> None:
    meta = yaml.safe_load(open(os.path.join(meta_dir, "values.yaml")))
    conn = yaml.safe_load(open(os.path.join(connectivity_dir, "values.yaml")))

    tag = str(conn["agentgateway"]["proxy"]["image"]["tag"])
    tags = {
        "connectivity agentgateway.proxy.image.tag": tag,
        "meta agentgateway.proxy.image.tag": str(meta["agentgateway"]["proxy"]["image"]["tag"]),
        "meta agentgateway.controller.image.tag": str(meta["agentgateway"]["controller"]["image"]["tag"]),
    }
    if len(set(tags.values())) != 1:
        fail(f"the gateway tags disagree, so the built-in catalog is ambiguous: {tags}")

    overlay = conn["llmRouting"]["modelCatalog"].get("providers") or {}
    meta_overlay = meta["llmRouting"]["modelCatalog"].get("providers") or {}
    if overlay != meta_overlay:
        fail("llmRouting.modelCatalog.providers differs between the meta and the connectivity chart")

    builtin = load_catalog(tag).get("providers", {})
    errors = []
    for provider, block in overlay.items():
        for model, entry in ((block or {}).get("models") or {}).items():
            base = builtin.get(provider, {}).get("models", {}).get(model)
            if base is None:
                continue
            errors.append(
                f"gateway v{tag} already prices {provider}/{model} "
                f"(built-in {base.get('rates')}, overlay {entry.get('rates')}): drop the overlay entry"
            )
    if errors:
        fail("\n  ".join(["overlay entries shadow the built-in catalog:", *errors]))

    default = meta["kagent"]["providers"]["anthropic"]["model"]
    priced = default in builtin.get("anthropic", {}).get("models", {}) or default in (
        (overlay.get("anthropic") or {}).get("models") or {}
    )
    if not priced:
        fail(f"the platform's default model anthropic/{default} is priced by neither gateway v{tag} nor the overlay")

    print(f"ok: overlay holds only models gateway v{tag} does not price; default model {default} priced")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
