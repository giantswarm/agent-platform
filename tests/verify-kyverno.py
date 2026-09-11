#!/usr/bin/env python3
"""Assert the Kyverno PolicyExceptions of Agent Substrate name exactly what
each workload violates, and nothing else.

A Giant Swarm cluster enforces the restricted Pod Security Standard through
Kyverno (the upstream kyverno-policies chart, podSecurityStandard restricted:
the baseline and restricted ClusterPolicies). Agent Substrate's pods violate
it — the per-node atelet is privileged with hostPorts and hostPath mounts, the
gVisor worker pods run as root with the capabilities a sandbox needs and
AppArmor and seccomp Unconfined, the control plane declares no securityContext
— so the connectivity chart ships one PolicyException per workload
(templates/substrate/policy-exceptions.yaml). The rule lists in those
exceptions are hand-written; this check computes them.

  * The substrate chart is pulled at the version the meta chart pins
    (components.substrate.versionRange, resolved as Flux resolves it) and
    rendered with the values the meta chart forwards to the substrate release,
    the platform Cluster on (the fleet shape: bundled Postgres and store off).
    Every Deployment, DaemonSet and StatefulSet in it is a workload.
  * The worker pod of a WorkerPool is rendered by ate-controller at run time,
    not by any chart; WORKER_POD below is that pod's spec as
    cmd/atecontroller/internal/controllers/workerpool_apply.go renders it at
    the pinned version (labels ate.dev/worker-pool=<pool>, the ateom container
    as root with the gVisor capability set, seccomp and AppArmor Unconfined, a
    hostPath of /var/lib/ateom-gvisor with HostToContainer propagation). A
    Substrate re-pin re-reads that file.
  * For each workload the restricted-PSS rules its pod spec violates are
    computed (violations()) — the checks of the kyverno-policies rules in
    kyvernoPolicies.rules — and the PolicyExceptions of the connectivity
    render (kagent + Substrate on, Kyverno served) that match the workload
    (kind, namespace, labels) must name exactly those rules, each with its
    autogen-<rule> copy for the controller kind. A workload with violations
    and no exception fails (admission would reject it); an exception that
    names a rule the workload does not violate fails (an over-broad
    exception); an exception that matches no Substrate workload fails unless
    it is the CNPG ImageVolume exception, whose one rule is checked too.
  * No exception may select app: kagent (the v1alpha2 agent Deployments' label;
    nothing carries it on kagent API v2).

Needs PyYAML (the CI job installs it) and network to ghcr.io for the chart.
"""

import importlib.util
import pathlib
import re
import subprocess
import sys
import tempfile

import yaml

import fluxsemver

HERE = pathlib.Path(__file__).resolve().parent
# registry_tags() of verify-components-charts.py: the tag list of an OCI
# repository, for a pin that is a range rather than an exact version.
_spec = importlib.util.spec_from_file_location("charts", HERE / "verify-components-charts.py")
charts = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(charts)

FLEET_APIS = ["--api-versions", "kyverno.io/v1", "--api-versions", "cilium.io/v2", "--api-versions", "monitoring.coreos.com/v1",
              "--api-versions", "gateway.networking.k8s.io/v1", "--api-versions", "gateway.envoyproxy.io/v1alpha1"]
ON = [
    "--set", "components.kagent.enabled=true",
    "--set", "kagent.harness.snapshotLocation=s3://ci-agent-snapshots/agents",
    "--set", "postgres.enabled=true",
]
CONNECTIVITY_ON = [
    "--set", "ingress.parentRefs[0].name=x",
    "--set", "components.substrate.enabled=true",
    "--set", "components.substrate-crds.enabled=true",
    "--set", "kyvernoPolicies.enabled=true",
    *ON,
]
SUBSTRATE_NAMESPACE = "ate-system"
KAGENT_NAMESPACE = "kagent"
# Exceptions that select something other than a Substrate workload, with the
# one rule each may name.
OTHER_EXCEPTIONS = {"kagent-pg-image-volume": {"restricted-volumes"}}

# The worker pod, as ate-controller renders it (see the module docstring).
WORKER_POD = {
    "name": "kagent-default (the WorkerPool's worker pods, rendered by ate-controller)",
    "kind": "Deployment",
    "namespace": KAGENT_NAMESPACE,
    "labels": {"ate.dev/worker-pool": "kagent-default"},
    "spec": {
        "securityContext": {"runAsUser": 0, "runAsGroup": 0},
        "containers": [{
            "name": "ateom",
            "image": "ghcr.io/giantswarm/substrate/ateom-gvisor",
            "securityContext": {
                "privileged": False,
                "runAsUser": 0,
                "runAsGroup": 0,
                "appArmorProfile": {"type": "Unconfined"},
                "seccompProfile": {"type": "Unconfined"},
                "capabilities": {
                    "drop": ["ALL"],
                    "add": ["NET_ADMIN", "SYS_ADMIN", "SYS_CHROOT", "SYS_PTRACE", "SETUID", "SETGID", "SETPCAP",
                            "DAC_OVERRIDE", "FOWNER", "CHOWN", "MKNOD", "NET_RAW", "SETFCAP"],
                },
            },
            "ports": [{"containerPort": 443}, {"containerPort": 8443}, {"containerPort": 8080}],
            "volumeMounts": [
                {"name": "run-ateom", "mountPath": "/var/lib/ateom-gvisor", "mountPropagation": "HostToContainer"},
            ],
        }],
        "volumes": [
            {"name": "ateom-capacity", "downwardAPI": {}},
            {"name": "run-ateom", "hostPath": {"path": "/var/lib/ateom-gvisor", "type": "DirectoryOrCreate"}},
            {"name": "atunnel-identity", "projected": {}},
            {"name": "atunnel-egress-trust", "projected": {}},
        ],
    },
}

# Pod Security Standards, as the kyverno-policies rules check them.
BASELINE_CAPABILITIES = {"AUDIT_WRITE", "CHOWN", "DAC_OVERRIDE", "FOWNER", "FSETID", "KILL", "MKNOD",
                         "NET_BIND_SERVICE", "SETFCAP", "SETGID", "SETPCAP", "SETUID", "SYS_CHROOT"}
RESTRICTED_VOLUMES = {"configMap", "csi", "downwardAPI", "emptyDir", "ephemeral", "persistentVolumeClaim", "projected", "secret"}
SECCOMP_OK = {"RuntimeDefault", "Localhost"}


def fail(msg: str) -> None:
    sys.exit(f"FAIL: {msg}")


def run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, check=False)


def render(chart: str, flags: list[str]) -> list[dict]:
    r = run(["helm", "template", "t", chart, *flags])
    if r.returncode != 0:
        fail(f"render of {chart} failed\n{r.stderr}")
    return [d for d in yaml.safe_load_all(r.stdout) if d]


def containers(spec: dict) -> list[dict]:
    return [*spec.get("initContainers", []), *spec.get("containers", [])]


def violations(spec: dict) -> set[str]:
    """The restricted-PSS rules a pod spec violates (rule names of kyvernoPolicies.rules)."""
    v = set()
    pod_sc = spec.get("securityContext") or {}
    cs = containers(spec)
    if any((c.get("securityContext") or {}).get("privileged") for c in cs):
        v.add("privileged-containers")
    if any(p.get("hostPort") for c in cs for p in c.get("ports", [])):
        v.add("host-ports-none")
    for vol in spec.get("volumes", []):
        types = set(vol) - {"name"}
        if "hostPath" in types:
            v.add("host-path")
        if types - RESTRICTED_VOLUMES:
            v.add("restricted-volumes")
    for c in cs:
        sc = c.get("securityContext") or {}
        caps = sc.get("capabilities") or {}
        add = set(caps.get("add") or [])
        if add - BASELINE_CAPABILITIES:
            v.add("adding-capabilities")
        if add - {"NET_BIND_SERVICE"}:
            v.add("adding-capabilities-strict")
        if "ALL" not in (caps.get("drop") or []):
            v.add("require-drop-all")
        if sc.get("allowPrivilegeEscalation") is not False:
            v.add("privilege-escalation")
        non_root = sc.get("runAsNonRoot", pod_sc.get("runAsNonRoot"))
        if non_root is not True:
            v.add("run-as-non-root")
        user = sc.get("runAsUser", pod_sc.get("runAsUser"))
        if user == 0:
            v.add("run-as-non-root-user")
        seccomp = (sc.get("seccompProfile") or pod_sc.get("seccompProfile") or {}).get("type")
        if seccomp == "Unconfined":
            v.add("check-seccomp")
        if seccomp not in SECCOMP_OK:
            v.add("check-seccomp-strict")
        apparmor = (sc.get("appArmorProfile") or pod_sc.get("appArmorProfile") or {}).get("type")
        if apparmor == "Unconfined":
            v.add("app-armor")
    return v


def selector_matches(selector: dict, labels: dict) -> bool:
    for k, val in (selector.get("matchLabels") or {}).items():
        if labels.get(k) != val:
            return False
    for expr in selector.get("matchExpressions") or []:
        key, op, values = expr["key"], expr["operator"], expr.get("values") or []
        if op == "Exists" and key not in labels:
            return False
        if op == "DoesNotExist" and key in labels:
            return False
        if op == "In" and labels.get(key) not in values:
            return False
        if op == "NotIn" and labels.get(key) in values:
            return False
    return True


def exception_matches(pe: dict, workload: dict) -> bool:
    for entry in pe["spec"]["match"].get("any", []):
        res = entry.get("resources", {})
        kinds = set(res.get("kinds", []))
        if "Pod" not in kinds or workload["kind"] not in kinds:
            continue
        if workload["namespace"] not in res.get("namespaces", []):
            continue
        if selector_matches(res.get("selector") or {}, workload["labels"]):
            return True
    return False


def rules_named(pe: dict) -> set[str]:
    """The rules an exception names, each required to come with its autogen copy."""
    named = set()
    for ex in pe["spec"]["exceptions"]:
        rules = set(ex["ruleNames"])
        base = {r for r in rules if not r.startswith("autogen-")}
        for r in base:
            if f"autogen-{r}" not in rules:
                fail(f"PolicyException {pe['metadata']['name']} names {r} without its autogen-{r} copy (Kyverno evaluates a Deployment or DaemonSet through the autogen rule)")
        named |= base
    return named


def substrate_release(meta: str) -> tuple[str, str, str]:
    """The substrate component's OCI url, version constraint and forwarded values."""
    docs = render(meta, ["-f", f"{meta}/ci/ci-values.yaml", "--set", "components.flux.enabled=false", *ON])
    oci = next(d for d in docs if d["kind"] == "OCIRepository" and d["metadata"]["name"] == "substrate")
    hr = next(d for d in docs if d["kind"] == "HelmRelease" and d["metadata"]["name"] == "substrate")
    return oci["spec"]["url"], oci["spec"]["ref"]["semver"], yaml.safe_dump(hr["spec"]["values"])


def substrate_workloads(url: str, constraint: str, values: str) -> list[dict]:
    tags = charts.registry_tags(url)
    version = fluxsemver.resolve(tags, constraint)
    if not version:
        fail(f"no published substrate chart satisfies {constraint!r} at {url}")
    with tempfile.TemporaryDirectory() as d:
        r = run(["helm", "pull", url, "--version", version, "--untar", "--untardir", d])
        if r.returncode != 0:
            fail(f"could not pull {url} {version}\n{r.stderr}")
        vf = pathlib.Path(d) / "values.yaml"
        vf.write_text(values)
        r = run(["helm", "template", "substrate", f"{d}/substrate", "-n", SUBSTRATE_NAMESPACE, "-f", str(vf)])
        if r.returncode != 0:
            fail(f"the substrate chart {version} rejects the values the meta chart forwards\n{r.stderr}")
        docs = [x for x in yaml.safe_load_all(r.stdout) if x]
    workloads = []
    for doc in docs:
        if doc["kind"] not in ("Deployment", "DaemonSet", "StatefulSet"):
            continue
        tmpl = doc["spec"]["template"]
        workloads.append({
            "name": f"{doc['kind']} {doc['metadata'].get('namespace', SUBSTRATE_NAMESPACE)}/{doc['metadata']['name']} (substrate {version})",
            "kind": doc["kind"],
            "namespace": doc["metadata"].get("namespace", SUBSTRATE_NAMESPACE),
            "labels": tmpl["metadata"].get("labels", {}),
            "spec": tmpl["spec"],
        })
    print(f"ok: substrate {version} rendered with the forwarded values — {len(workloads)} workloads")
    return workloads


def main(meta: str, connectivity: str) -> int:
    url, constraint, values = substrate_release(meta)
    workloads = [*substrate_workloads(url, constraint, values), WORKER_POD]
    pes = [d for d in render(connectivity, [*CONNECTIVITY_ON, *FLEET_APIS]) if d["kind"] == "PolicyException"]
    if not pes:
        fail("the connectivity render carries no PolicyException with kagent, Substrate and Kyverno on")
    used = set()
    for w in workloads:
        v = violations(w["spec"])
        matching = [pe for pe in pes if exception_matches(pe, w)]
        named = set()
        for pe in matching:
            used.add(pe["metadata"]["name"])
            named |= rules_named(pe)
        if v and not matching:
            fail(f"{w['name']} violates {sorted(v)} and no PolicyException selects it: admission would reject it")
        if not v and matching:
            fail(f"{w['name']} violates nothing but {[pe['metadata']['name'] for pe in matching]} except it")
        if named != v:
            fail(f"{w['name']}: the exceptions {[pe['metadata']['name'] for pe in matching]} name {sorted(named)}, "
                 f"the pod spec violates {sorted(v)} (missing {sorted(v - named)}, over-broad {sorted(named - v)})")
        print(f"ok: {w['name']}: {sorted(v) or 'violates nothing'}"
              + (f" — excepted by {', '.join(pe['metadata']['name'] for pe in matching)}" if matching else ""))
    for pe in pes:
        name = pe["metadata"]["name"]
        if name in used:
            continue
        if name not in OTHER_EXCEPTIONS:
            fail(f"PolicyException {name} matches no Substrate workload")
        if rules_named(pe) != OTHER_EXCEPTIONS[name]:
            fail(f"PolicyException {name} names {sorted(rules_named(pe))}, expected {sorted(OTHER_EXCEPTIONS[name])}")
        print(f"ok: {name}: {sorted(OTHER_EXCEPTIONS[name])} (not a Substrate workload)")
    for pe in pes:
        if re.search(r"\bapp: kagent\b", yaml.safe_dump(pe["spec"]["match"])):
            fail(f"PolicyException {pe['metadata']['name']} selects app: kagent, the v1alpha2 agent Deployments' label; nothing carries it on kagent API v2")
    print("ok: no exception selects app: kagent")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2]))
