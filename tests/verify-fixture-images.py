#!/usr/bin/env python3
"""Assert every container image the test fixtures name is one gsoci.azurecr.io
publishes (giantswarm/agent-platform#882).

A fixture pod stands for a pod a live cluster runs: the model-serving policies
are proven over it. An image no registry serves, or one off gsoci, is a pod the
chart's image-verification policy refuses and the kubelet cannot pull, so the
suite would pass over a pod no cluster can run. Every YAML and JSON document
under tests/fixtures is walked, and every `image` of a containers /
initContainers / ephemeralContainers entry, wherever the pod spec sits, must:

  * name gsoci.azurecr.io — the one registry the platform pulls from and the
    image-verification policy trusts;
  * resolve: the registry serves its manifest, by digest where the reference
    carries one (what the runtime pulls), else by tag; a renamed repository or
    a tag nobody published fails naming the fixture and the container.

Network: gsoci.azurecr.io. Needs PyYAML.
Usage: verify-fixture-images.py <fixtures dir>
"""
import glob
import importlib.util
import json
import os
import sys

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))


def load(name: str):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), os.path.join(HERE, f"{name}.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


images = load("verify-substrate-images")

REGISTRY = images.REGISTRY


def documents(path: str):
    with open(path) as f:
        if path.endswith(".json"):
            yield json.load(f)
        else:
            yield from (d for d in yaml.safe_load_all(f) if d is not None)


def containers(node, where: str = ""):
    """(location, container name, image) of every container entry under node."""
    if isinstance(node, dict):
        for key, value in node.items():
            if key in images.CONTAINER_LISTS and isinstance(value, list):
                for c in value:
                    if isinstance(c, dict) and isinstance(c.get("image"), str):
                        yield f"{where}/{key}", c.get("name", "?"), c["image"]
            yield from containers(value, f"{where}/{key}")
    elif isinstance(node, list):
        for i, item in enumerate(node):
            yield from containers(item, f"{where}[{i}]")


def main(fixtures: str) -> int:
    paths = sorted(glob.glob(os.path.join(fixtures, "*.yaml")) + glob.glob(os.path.join(fixtures, "*.json")))
    if not paths:
        images.cc.fail(f"no fixtures under {fixtures}")
    seen, failures = 0, []
    for path in paths:
        name = os.path.basename(path)
        for doc in documents(path):
            for where, container, ref in containers(doc):
                seen += 1
                if images.host_of(ref) != REGISTRY:
                    failures.append(f"{name} {where} {container}: {ref} is not a {REGISTRY} reference")
                elif not images.published(ref):
                    failures.append(f"{name} {where} {container}: {REGISTRY} does not publish {ref}")
                else:
                    print(f"ok: {name} {container}: {ref}")
    if failures:
        images.cc.fail("fixture images that no cluster can pull:\n  " + "\n  ".join(failures))
    if not seen:
        images.cc.fail(f"no container image in any fixture under {fixtures}: the walk found nothing to check")
    print(f"every fixture image ({seen}) resolves on {REGISTRY}")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__.strip().splitlines()[-1])
    sys.exit(main(sys.argv[1]))
