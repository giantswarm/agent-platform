#!/usr/bin/env python3
"""Assert the CloudNativePG instance pods bound their emptyDirs.

Kyverno's require-emptydir-requests-and-limits refuses a container that mounts
an emptyDir without a sizeLimit unless the container requests and limits
ephemeral-storage. The chart renders no pod: the operator builds the instance
pod from the Cluster (and the plugin sidecar from the ObjectStore), as a live
kagent-pg-1 pod shows:

  * scratch-data (sizeLimit: ephemeralVolumesSizeLimit.temporaryData) and shm
    (sizeLimit: ephemeralVolumesSizeLimit.shm), mounted by bootstrap-controller,
    postgres and the plugin sidecar;
  * plugins, with backup.method plugin, no sizeLimit, mounted by postgres and
    the plugin sidecar;
  * bootstrap-controller and postgres take Cluster.spec.resources, the sidecar
    takes ObjectStore.spec.instanceSidecarConfiguration.resources.

The check builds that pod from each render and applies the policy's rule to it.

Usage: verify-postgres-ephemeral-storage.py <render without backup> <render
with the plugin backup> <render with both knobs null> <connectivity values>
<meta values>
Needs PyYAML.
"""
import sys

import yaml


def docs(path, kind):
    return [d for d in yaml.safe_load_all(open(path)) if d and d.get("kind") == kind]


def pod(render):
    cluster = docs(render, "Cluster")
    if len(cluster) != 1:
        raise SystemExit("FAIL: %s: %d CNPG Clusters, want 1" % (render, len(cluster)))
    spec = cluster[0]["spec"]
    sizes = spec.get("ephemeralVolumesSizeLimit", {})
    volumes = {"scratch-data": sizes.get("temporaryData"), "shm": sizes.get("shm")}
    base = ["scratch-data", "shm"]
    containers = [("bootstrap-controller", spec.get("resources", {}), base)]
    if spec.get("plugins"):
        volumes["plugins"] = None
        stores = docs(render, "ObjectStore")
        side = stores[0]["spec"].get("instanceSidecarConfiguration", {}).get("resources", {}) if stores else {}
        containers += [("postgres", spec.get("resources", {}), base + ["plugins"]),
                       ("plugin-barman-cloud", side, base + ["plugins"])]
    else:
        containers.append(("postgres", spec.get("resources", {}), base))
    return volumes, containers


def problems(render):
    volumes, containers = pod(render)
    unbounded = {n for n, size in volumes.items() if not size}
    out = []
    for name, res, mounts in containers:
        if not unbounded & set(mounts):
            continue
        for side in ("requests", "limits"):
            if not (res.get(side) or {}).get("ephemeral-storage"):
                out.append("%s mounts %s without %s.ephemeral-storage"
                           % (name, ", ".join(sorted(unbounded & set(mounts))), side))
    return out


def main(off, plugin, nulled, conn_values, meta_values):
    failed = False
    for render in (off, plugin):
        bad = problems(render)
        cluster = docs(render, "Cluster")[0]["spec"]
        for side in ("requests", "limits"):
            if not cluster.get("resources", {}).get(side, {}).get("ephemeral-storage"):
                bad.append("Cluster.spec.resources.%s.ephemeral-storage is unset" % side)
        for key in ("shm", "temporaryData"):
            if not cluster.get("ephemeralVolumesSizeLimit", {}).get(key):
                bad.append("Cluster.spec.ephemeralVolumesSizeLimit.%s is unset" % key)
        for p in bad:
            print("FAIL: %s: %s" % (render, p))
        failed = failed or bool(bad)
        if not bad:
            print("ok: %s: the instance pod bounds every emptyDir" % render)

    if not problems(nulled):
        print("FAIL: %s: the pod passes with resources and ephemeralVolumesSizeLimit null; the check is vacuous" % nulled)
        failed = True
    else:
        print("ok: %s: without the knobs the pod fails the rule" % nulled)

    conn = yaml.safe_load(open(conn_values))["postgres"]
    meta = yaml.safe_load(open(meta_values))["postgres"]
    for path in (("resources",), ("ephemeralVolumesSizeLimit",), ("backup", "objectStore", "sidecar", "resources")):
        a, b = conn, meta
        for k in path:
            a, b = a[k], b[k]
        if a != b:
            print("FAIL: postgres.%s differs: the meta chart forwards %s, the connectivity default is %s" % (".".join(path), b, a))
            failed = True
    if not failed:
        print("ok: the meta chart forwards the connectivity defaults")
    return 1 if failed else 0


if __name__ == "__main__":
    if len(sys.argv) != 6:
        raise SystemExit(__doc__)
    sys.exit(main(*sys.argv[1:]))
