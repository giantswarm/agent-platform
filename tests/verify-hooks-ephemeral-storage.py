#!/usr/bin/env python3
"""Assert every Helm hook Job of a render bounds its emptyDirs.

Kyverno's require-emptydir-requests-and-limits refuses a container mounting an
emptyDir without ephemeral-storage requests and limits. For every Job carrying
helm.sh/hook in each rendered file given:

  * every container and init container that mounts an emptyDir requests and
    limits ephemeral-storage;
  * every emptyDir of the pod carries a sizeLimit.

Usage: verify-hooks-ephemeral-storage.py <render>=<expected Job names, comma-separated> ...
The hook Jobs of each render must be exactly the expected set, so a render
that stops producing a hook fails instead of passing vacuously.
Needs PyYAML.
"""
import sys

import yaml


def problems(job):
    spec = job["spec"]["template"]["spec"]
    empty = {v["name"] for v in spec.get("volumes", []) if "emptyDir" in v}
    out = []
    for v in spec.get("volumes", []):
        if "emptyDir" in v and not (v["emptyDir"] or {}).get("sizeLimit"):
            out.append("emptyDir %s has no sizeLimit" % v["name"])
    for c in spec.get("initContainers", []) + spec["containers"]:
        if not {m["name"] for m in c.get("volumeMounts", [])} & empty:
            continue
        res = c.get("resources", {})
        for kind in ("requests", "limits"):
            if "ephemeral-storage" not in res.get(kind, {}):
                out.append("container %s mounts an emptyDir without ephemeral-storage %s" % (c["name"], kind))
    return out


def main(args):
    failed = False
    for arg in args:
        path, _, names = arg.partition("=")
        want = set(filter(None, names.split(",")))
        bad = False
        jobs = [d for d in yaml.safe_load_all(open(path))
                if d and d.get("kind") == "Job" and "helm.sh/hook" in d["metadata"].get("annotations", {})]
        got = {j["metadata"]["name"] for j in jobs}
        if got != want:
            print("FAIL: %s: hook Jobs %s, want %s" % (path, sorted(got), sorted(want)))
            bad = True
        for j in jobs:
            for p in problems(j):
                print("FAIL: %s: %s: %s" % (path, j["metadata"]["name"], p))
                bad = True
        failed = failed or bad
        if not bad:
            print("ok: %s: %d hook Jobs bound their emptyDirs: %s" % (path, len(jobs), ", ".join(sorted(got))))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
