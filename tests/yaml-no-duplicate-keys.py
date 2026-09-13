#!/usr/bin/env python3
"""Fail when a rendered multi-document YAML stream repeats a mapping key.

`helm template` writes what the templates emit and never parses it back, so a
template that sets the same label twice (a common-labels helper next to a
selector's own `app.kubernetes.io/name`) renders fine and fails only at install,
where the Kubernetes YAML decoder refuses the duplicate. The verify-* targets
feed their renders through this to catch it offline.

Usage: yaml-no-duplicate-keys.py <rendered.yaml>
"""
import sys

import yaml


class StrictLoader(yaml.SafeLoader):
    pass


def construct_mapping(loader, node):
    seen = set()
    for key_node, _ in node.value:
        key = loader.construct_object(key_node)
        if key in seen:
            raise yaml.constructor.ConstructorError(None, None, f"duplicate mapping key {key!r}", key_node.start_mark)
        seen.add(key)
    return loader.construct_mapping(node)


StrictLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, construct_mapping)

if __name__ == "__main__":
    with open(sys.argv[1]) as f:
        for _ in yaml.load_all(f, Loader=StrictLoader):
            pass
