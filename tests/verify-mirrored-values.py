#!/usr/bin/env python3
"""Assert the meta chart's copy of every mirrored network-policy default equals
the connectivity chart's own (giantswarm/agent-platform#522, #525).

The meta chart forwards its whole values tree to the connectivity release
(templates/components.yaml, forwardAllValues), so every default it declares
under a path the connectivity chart also declares is a supplied value there,
and a supplied value shadows the child's default. A list that moves in one
chart and not the other is therefore a bug by construction: #523 added the
Hugging Face download CDN's pattern (`*.*.*.hf.co`) to the connectivity
chart's `modelServing.networkPolicy.huggingFace.fqdns` and
`modelManager.networkPolicy.huggingFace.fqdns`, the meta chart's copies kept
the four old entries, and every installation's connectivity release rendered
the old lists (gazelle, 4.28.17: Helm storage's supplied `.config` with four
entries beside the chart's five).

The check walks the connectivity chart's values for every `fqdns` and `cidrs`
list and every `port` under a `networkPolicy` block — the egress allow-lists
and the admitted ports whose defaults the meta chart mirrors (#525 gave the
llm-d workload shape its own `modelServing.networkPolicy.llmisvcWorkload.port`,
a value the meta chart had to carry too or its forwarded tree would have kept
the connectivity release on the old port) — and for every leaf of the
model-serving cache and policy blocks (`modelServing.cache`,
`modelServing.policies`; #537 gave the claim its StorageClass block, and a
forwarded `env` list short of an entry would drop it from every installation's
render) and of the prewarm placeholder's PriorityClass block
(`clusterManager.prewarmPriorityClass`; #539 — the class name is the
gpu-node-pool chart's default `pool.prewarm.priorityClassName`, and a forwarded
copy under another name would rename the class every pool of the installation
expects) and of the pre-pull block (`modelServing.prepull`; #545 — the image list
is the runtime image the predictors run, and a forwarded copy short of it or
behind it would pre-pull the wrong image on every installation; #551 added the
model presets to pre-pull; #562 emptied `prepull.nodeSelector` in both charts —
the default is the template's, so an installation's map replaces it — and an
empty mapping counts as a leaf so a copy that kept the map is drift), of the model-images block (`modelServing.modelImages`;
#551 — a forwarded registry host that differs from the connectivity default would
rewrite every oci:// preset of every installation) and of the
image-verification block (`modelServing.imageVerification`; #552 — a forwarded
copy with the switch on, or with other knobs, would verify on every
installation what the connectivity default does not) and of the serving
defaults block (`modelServing.serving`; the GPU resource name and RuntimeClass
every composed LLMInferenceService gets from the discovery ConfigMap) and of the serving
namespace block (`modelServing.namespace`; #565 — the namespace's keep policy
is what stands between a slice composed without the cache and a cache claim an
earlier slice left in the namespace, and a forwarded copy without `keep` or
with it off would strip the policy from every installation's render) — and
holds the meta chart's value at the same path equal to it,
naming the path and both values when they differ or the meta chart lacks the
path. It refuses to pass
vacuously: the two `huggingFace.fqdns` lists, the model-serving port, the
StorageClass provisioner, the policies' env, the PriorityClass name, the
pre-pull image and model-preset lists and its (empty) selector, the model-images registry, the image
verification's failureAction, the serving defaults' GPU resource name and the
namespace's keep switch must be among the paths it compared.
`--meta-values FILE` compares another meta values file (the negative
controls in `make verify-meta`).
"""

import argparse
import sys

import yaml

REQUIRED = {"modelServing.networkPolicy.huggingFace.fqdns", "modelManager.networkPolicy.huggingFace.fqdns",
            "modelServing.networkPolicy.llmisvcWorkload.port",
            "modelServing.cache.storageClass.provisioner", "modelServing.policies.env", "clusterManager.prewarmPriorityClass.name",
            "modelServing.prepull.images", "modelServing.prepull.modelPresets", "modelServing.prepull.nodeSelector", "modelServing.modelImages.registry",
            "modelServing.imageVerification.failureAction", "modelServing.imageVerification.images", "modelServing.imageVerification.attestors",
            "modelServing.serving.gpuResourceName",
            "modelServing.namespace.keep"}
MIRRORED = ("fqdns", "cidrs", "port")
# Blocks the meta chart mirrors leaf for leaf (#537, #539, #545, #551, #552, #565).
SUBTREES = (("modelServing", "namespace"), ("modelServing", "serving"), ("modelServing", "cache"), ("modelServing", "policies"), ("modelServing", "prepull"), ("modelServing", "modelImages"),
            ("modelServing", "imageVerification"),
            ("clusterManager", "prewarmPriorityClass"))


def leaves(tree: dict, path: tuple[str, ...] = ()):
    for key, value in tree.items():
        if isinstance(value, dict) and value:
            yield from leaves(value, (*path, key))
        else:
            # An empty mapping is a leaf too: `{}` on one side and a filled map on the other is drift (#562 moved the
            # pre-pull selector's default out of both values files; a copy that kept the map would shadow the empty
            # default on every installation and merge into whatever an installation sets).
            yield (*path, key), value


def lookup(tree: dict, path: tuple[str, ...]):
    for key in path:
        if not isinstance(tree, dict) or key not in tree:
            return None, False
        tree = tree[key]
    return tree, True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("meta", help="the meta chart directory (helm/agent-platform)")
    parser.add_argument("connectivity", help="the connectivity chart directory (helm/agent-platform-connectivity)")
    parser.add_argument("--meta-values", help="a meta values file to compare instead of <meta>/values.yaml")
    args = parser.parse_args()
    with open(args.meta_values or f"{args.meta}/values.yaml") as f:
        meta = yaml.safe_load(f)
    with open(f"{args.connectivity}/values.yaml") as f:
        connectivity = yaml.safe_load(f)

    compared: set[str] = set()
    drift: list[str] = []
    for path, default in leaves(connectivity):
        if not ((path[-1] in MIRRORED and "networkPolicy" in path[:-1]) or any(path[:len(s)] == s for s in SUBTREES)):
            continue
        dotted = ".".join(path)
        compared.add(dotted)
        mirrored, present = lookup(meta, path)
        if not present:
            drift.append(f"{dotted}: the meta chart has no such path; the connectivity default is {default}")
        elif mirrored != default:
            drift.append(f"{dotted}: meta {mirrored} != connectivity {default}")
    if missing := REQUIRED - compared:
        sys.exit(f"FAIL: the connectivity chart no longer declares {sorted(missing)}; this check would pass vacuously")
    if drift:
        sys.exit("FAIL: a mirrored default differs between the charts — the meta chart forwards its copy, which shadows the connectivity default:\n  " + "\n  ".join(drift))
    print(f"ok: {len(compared)} mirrored defaults (networkPolicy fqdns/cidrs lists and ports, the modelServing namespace, serving, cache, policies, prepull, modelImages and imageVerification blocks, the clusterManager prewarmPriorityClass block) are equal in both charts ({', '.join(sorted(compared))})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
