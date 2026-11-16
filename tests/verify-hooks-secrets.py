#!/usr/bin/env python3
"""Prove the connectivity hook identity reads only the Secrets its hooks write
(giantswarm/agent-platform#1269), on a local API server.

The connectivity chart renders, with Substrate, Postgres and the Agent
Platform organizations on, the hook
identity (templates/substrate/hooks-rbac.yaml) and the hook Jobs. This script
renders them, starts an API server (tests/apiserver.py: envtest's etcd and
kube-apiserver, RBAC on, no controllers) and replays each event the way Helm
does, in weight order, as the hook ServiceAccount:

- pre-install: the identity, the substrate-namespaces Job's script (it creates
  ate-system and the podcert namespace), the bootstrap's Roles, then the
  bootstrap's own scripts (the openssl init and the kubectl main container);
- the same again, pre-upgrade, against what the first run left (everything
  present, nothing re-minted);
- post-install: a stand-in for CNPG's <cluster>-app Secret, the databases
  hook's Roles and its script;
- post-upgrade: a sign-in keys Secret an earlier release kept, the chart
  rendered against the API server (its lookup renders the cleanup), the
  cleanup's Role and its script, which must delete that Secret.

Every script must succeed. Then, as the same ServiceAccount, with every hook
Role and the ClusterRole still bound: a workload cluster's kubeconfig in an
org- namespace, a Secret and a ConfigMap no hook writes in each namespace a
hook writes, a list of Secrets in each of those namespaces and across the
cluster, a Secret of any name outside the Substrate bootstrap's namespaces and
the deletion of a Secret the cleanup does not name are all refused by RBAC.
The rendered ClusterRole carries no rule on secrets or configmaps, and every
Role rule names its objects but the bootstrap's create.

Needs helm, openssl, jq, PyYAML and github.com on the first run (the envtest
binaries). HELM selects the binary.

Usage: verify-hooks-secrets.py CONNECTIVITY_CHART
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile

import yaml

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from apiserver import APIServer  # noqa: E402

HELM = os.environ.get("HELM", "helm")
RELEASE = "t"
RELEASE_NS = "agent-platform"
KAGENT_NS = "kagent"
SA = f"system:serviceaccount:{RELEASE_NS}:{RELEASE}-hooks"
SA_GROUPS = ("system:serviceaccounts", f"system:serviceaccounts:{RELEASE_NS}", "system:authenticated")
ORG_NS = "org-acme"

failures = []


def fail(msg):
    failures.append(msg)
    print(f"FAIL: {msg}")


def ok(msg):
    print(f"ok: {msg}")


def helm_template(chart, flags, api=None):
    live = ["--dry-run=server", "--kubeconfig", api.kubeconfig, "--kube-context", "verify"] if api else []
    r = subprocess.run([HELM, "template", RELEASE, chart, "--namespace", RELEASE_NS, *live, *flags], capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit(f"FAIL: helm template:\n{r.stderr}")
    return [d for d in yaml.safe_load_all(r.stdout) if d]


# The sign-in keys cleanup renders from a lookup, so against the API server;
# Helm then refuses a render whose Namespaces exist unowned, hence the smallest
# release that renders the workspace-manager.
CLEANUP_FLAGS = ["--set", "ingress.parentRefs[0].name=x", "--set", "components.workspace-manager.enabled=true",
                 "-f", os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "workspaces-values.yaml"),
                 "--set", "global.domain=ci.example.com", "--set", "global.identity.issuerUrl=https://dex.ci.example.com",
                 "--set", "global.identity.clientId=platform", "--set", "global.identity.existingSecret=platform-oauth"]


def render(chart):
    r = subprocess.run([HELM, "template", RELEASE, chart, "--namespace", RELEASE_NS,
                        "--set", "ingress.parentRefs[0].name=x", "--set", "kagent.harness.snapshotLocation=s3://a/b",
                        "--set", "kagent.database.postgres.urlFile=/etc/kagent-db/uri",
                        "--set", f"kagent.namespaceOverride={KAGENT_NS}", "--set", "components.kagent.enabled=true",
                        "--set", "components.substrate.enabled=true", "--set", "components.substrate-crds.enabled=true",
                        "--set", "postgres.enabled=true",
                        "--set", "agentPlatformOrganizations.enabled=true", "--set", "agentPlatformOrganizations.fluxNamespace=flux-system",
                        "--set", "agentPlatformOrganizations.fluxSource.name=agent-platform-organizations",
                        "--set", "agentPlatformOrganizations.apiHostnames[0]=api.ci.example.com", "--set", "llmRouting.enabled=true",
                        # The operator's names, which the meta chart derives onto this release.
                        "-f", os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "organizations-operator-values.yaml"),
                        "--api-versions", "admissionregistration.k8s.io/v1/MutatingAdmissionPolicy"], capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit(f"FAIL: helm template:\n{r.stderr}")
    return [d for d in yaml.safe_load_all(r.stdout) if d]


def stub_crds(cluster, docs):
    """Permissive CRDs for the kinds the render carries and envtest does not serve: a server-side render maps every object."""
    served = set()
    for line in cluster.admin(["api-resources", "--no-headers"]).splitlines():
        f = line.split()
        served.add((f[-3], f[-1]))
    seen = set()
    for d in docs:
        api, kind = d["apiVersion"], d["kind"]
        if "/" not in api or (api, kind) in served or (api, kind) in seen:
            continue
        seen.add((api, kind))
        group, version = api.split("/")
        plural = kind.lower() + "stubs"
        meta = {"name": f"{plural}.{group}"}
        if group.endswith((".k8s.io", ".kubernetes.io")):
            meta["annotations"] = {"api-approved.kubernetes.io": "unapproved, test stub"}
        cluster.admin(["apply", "--server-side", "-f", "-"], stdin=yaml.safe_dump({"apiVersion": "apiextensions.k8s.io/v1", "kind": "CustomResourceDefinition",
                       "metadata": meta,
                       "spec": {"group": group, "scope": "Namespaced" if d["metadata"].get("namespace") else "Cluster",
                                "names": {"kind": kind, "plural": plural, "singular": kind.lower() + "stub", "listKind": kind + "List"},
                                "versions": [{"name": version, "served": True, "storage": True, "schema": {"openAPIV3Schema": {
                                    "type": "object", "x-kubernetes-preserve-unknown-fields": True}}}]}}))
    cluster.admin(["wait", "--for=condition=Established", "crd", "--all", "--timeout=60s"])


def hook_objects(docs, event):
    """The hook objects of one event in Helm's order: by weight, then kind as Helm installs them."""
    out = []
    for d in docs:
        ann = d["metadata"].get("annotations") or {}
        if event in (ann.get("helm.sh/hook") or "").split(","):
            out.append((int(ann.get("helm.sh/hook-weight", "0")), d))
    return [d for _, d in sorted(out, key=lambda x: x[0])]


def strip(d):
    d = yaml.safe_load(yaml.safe_dump(d))
    d["metadata"].pop("annotations", None)
    return d


class Cluster:
    def __init__(self, api, work):
        self.api = api
        self.work = work
        bindir = os.path.join(work, "bin")
        os.makedirs(bindir)
        kubectl = os.path.join(api.bin, "kubectl")
        groups = " ".join(f"--as-group={g}" for g in SA_GROUPS)
        with open(os.path.join(bindir, "kubectl"), "w") as f:
            f.write(f'#!/bin/sh\nexec "{kubectl}" --kubeconfig "{api.kubeconfig}" --context verify --as={SA} {groups} "$@"\n')
        os.chmod(os.path.join(bindir, "kubectl"), 0o755)
        self.env = dict(os.environ, PATH=bindir + os.pathsep + os.environ["PATH"], HOME=work)

    def admin(self, args, stdin=None):
        rc, out = self.api.kubectl(args, stdin=stdin)
        if rc != 0:
            raise SystemExit(f"FAIL: kubectl {' '.join(args)}: {out}")
        return out

    def apply(self, d):
        self.admin(["apply", "--server-side", "-f", "-"], stdin=yaml.safe_dump(strip(d)))

    def as_sa(self, args):
        return self.api.kubectl(args, as_=SA, groups=SA_GROUPS)

    def run_script(self, label, script):
        """A hook script as its container runs it (sh -eu -c), /work and /tmp moved under the work directory."""
        tmp = os.path.join(self.work, "tmp")
        shutil.rmtree(tmp, ignore_errors=True)
        os.makedirs(tmp)
        script = re.sub(r"(?<![\w./-])/tmp/", tmp + "/", script)
        script = re.sub(r"(?<![\w./-])/work\b", os.path.join(self.work, "work"), script)
        r = subprocess.run(["sh", "-eu", "-c", script], env=self.env, capture_output=True, text=True)
        if r.returncode != 0:
            fail(f"{label} failed as {SA}:\n{r.stdout}{r.stderr}")
        else:
            ok(f"{label} succeeds as the hook identity")
        return r.stdout


def job_scripts(job):
    spec = job["spec"]["template"]["spec"]
    init = [c["args"][0] for c in spec.get("initContainers", [])]
    return init, spec["containers"][0]["args"][0]


def run_event(cluster, docs, event):
    """Replay one event. The API server authorizes from RBAC caches it fills
    on its own time (giantswarm/agent-platform#1412), so run_event() settles
    the API server after the RBAC writes, before the next script and at the
    end of the event: each script, and each refusal after the last event,
    meets every grant written so far. settle() writes only its own
    sentinels, bound to users of its own, so it grants the hook identity
    nothing."""
    rbac_written = False
    for d in hook_objects(docs, event):
        if d["kind"] == "Job":
            if rbac_written:
                cluster.api.settle()
                rbac_written = False
            name = d["metadata"]["name"]
            init, main = job_scripts(d)
            if init:
                shutil.rmtree(os.path.join(cluster.work, "work"), ignore_errors=True)
                os.makedirs(os.path.join(cluster.work, "work"))
                for s in init:
                    cluster.run_script(f"{event} {name} (init)", s)
            cluster.run_script(f"{event} {name}", main)
        elif d["kind"] in ("ServiceAccount", "ClusterRole", "ClusterRoleBinding", "Role", "RoleBinding"):
            cluster.apply(d)
            rbac_written = True
    if rbac_written:
        cluster.api.settle()


def main():
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    docs = render(sys.argv[1])

    cluster_role = next(d for d in docs if d["kind"] == "ClusterRole" and d["metadata"]["name"] == f"{RELEASE}-hooks")
    bad = [r for r in cluster_role["rules"] if {"secrets", "configmaps", "*"} & set(r.get("resources", []))]
    if bad:
        fail(f"the hook ClusterRole still carries {bad}")
    else:
        ok("the hook ClusterRole has no rule on secrets or configmaps")

    roles = [d for d in docs if d["kind"] == "Role" and d["metadata"]["name"].startswith(f"{RELEASE}-hooks")]
    bootstrap = f"{RELEASE}-hooks-substrate-bootstrap"
    for role in roles:
        for rule in role["rules"]:
            if rule.get("resourceNames"):
                continue
            if role["metadata"]["name"] != bootstrap or rule["verbs"] != ["create"]:
                fail(f"Role {role['metadata']['namespace']}/{role['metadata']['name']}: {rule['verbs']} on {rule['resources']} without resourceNames")
    unnamed_create = {r["metadata"]["namespace"] for r in roles if r["metadata"]["name"] == bootstrap}
    written = {r["metadata"]["namespace"] for r in roles}

    api = APIServer()
    work = tempfile.mkdtemp(prefix="verify-hooks-secrets-")
    try:
        cluster = Cluster(api, work)
        for ns in (RELEASE_NS, KAGENT_NS, ORG_NS):
            cluster.admin(["create", "namespace", ns])

        served = cluster.admin(["api-resources", "--api-group=certificates.k8s.io", "--no-headers"])
        if "ClusterTrustBundle" not in served:
            raise SystemExit("FAIL: the local API server serves no ClusterTrustBundle; the bootstrap cannot run")

        run_event(cluster, docs, "pre-install")
        out = cluster.run_script("pre-upgrade substrate-bootstrap (second run)",
                                 job_scripts(next(d for d in docs if d["kind"] == "Job" and d["metadata"]["name"].endswith("-substrate-bootstrap")))[1])
        if ": created" in out:
            fail(f"the bootstrap's second run re-created something:\n{out}")

        # CNPG's initdb Secret, which the databases hook copies per database.
        pg = next(d for d in docs if d["kind"] == "Job" and d["metadata"]["name"].endswith("-postgres-databases"))
        src_ns = KAGENT_NS
        cluster.admin(["-n", src_ns, "create", "secret", "generic", "kagent-pg-app", "--type=kubernetes.io/basic-auth",
                       "--from-literal=username=kagent", "--from-literal=password=pw", "--from-literal=dbname=app",
                       "--from-literal=uri=postgresql://kagent:pw@kagent-pg-rw.kagent:5432/app",
                       "--from-literal=jdbc-uri=jdbc:postgresql://kagent-pg-rw.kagent:5432/app?password=pw",
                       "--from-literal=pgpass=kagent-pg-rw:5432:app:kagent:pw"])
        run_event(cluster, [d for d in docs if d["kind"] != "Job" or d is pg], "post-install")

        # The sign-in keys Secret an earlier release kept: the chart's lookup
        # renders the cleanup only while it is there and this release's.
        orphan = "workspace-manager-signin-keys"
        cluster.admin(["apply", "--server-side", "-f", "-"], stdin=yaml.safe_dump({"apiVersion": "v1", "kind": "Secret", "metadata": {
            "name": orphan, "namespace": RELEASE_NS,
            "annotations": {"meta.helm.sh/release-name": RELEASE, "meta.helm.sh/release-namespace": RELEASE_NS}}}))
        stub_crds(cluster, helm_template(sys.argv[1], CLEANUP_FLAGS))
        live = helm_template(sys.argv[1], CLEANUP_FLAGS, api)
        cleanup = [d for d in live if d["metadata"]["name"].endswith("-signin-keys-cleanup")]
        if not any(d["kind"] == "Job" for d in cleanup):
            fail("the chart rendered against the API server has no sign-in keys cleanup Job")
        written |= {d["metadata"]["namespace"] for d in cleanup if d["kind"] == "Role"}
        run_event(cluster, cleanup, "post-upgrade")
        rc, out = api.kubectl(["-n", RELEASE_NS, "get", "secret", orphan])
        if rc == 0:
            fail(f"the sign-in keys cleanup left {RELEASE_NS}/{orphan}")
        else:
            ok(f"the sign-in keys cleanup deleted {RELEASE_NS}/{orphan}")

        # Decoys: what a management cluster keeps next to the platform.
        cluster.admin(["-n", ORG_NS, "create", "secret", "generic", "acme-kubeconfig", "--from-literal=value=x"])
        written = sorted(written)
        for ns in written:
            cluster.admin(["-n", ns, "create", "secret", "generic", "not-a-hook-secret", "--from-literal=v=x"])
            cluster.admin(["-n", ns, "create", "configmap", "not-a-hook-configmap", "--from-literal=v=x"])

        refused = [(["-n", ORG_NS, "get", "secret", "acme-kubeconfig"], f"a workload cluster kubeconfig in {ORG_NS}"),
                   (["get", "secrets", "-A"], "a list of every Secret")]
        for ns in written:
            refused += [(["-n", ns, "get", "secret", "not-a-hook-secret"], f"a Secret no hook writes in {ns}"),
                        (["-n", ns, "get", "configmap", "not-a-hook-configmap"], f"a ConfigMap no hook writes in {ns}"),
                        (["-n", ns, "get", "secrets"], f"a list of the Secrets in {ns}")]
            if ns not in unnamed_create:
                refused.append((["-n", ns, "create", "secret", "generic", "squatted", "--from-literal=v=x"],
                                f"a Secret of any name in {ns}"))
        refused.append((["-n", RELEASE_NS, "delete", "secret", "not-a-hook-secret"],
                        f"the deletion of a Secret the cleanup does not name in {RELEASE_NS}"))
        for args, what in refused:
            rc, out = cluster.as_sa(args)
            if rc == 0 or "forbidden" not in out.lower():
                fail(f"the hook identity was not refused {what}: {out.strip()}")
            else:
                ok(f"the hook identity is refused {what}")
    finally:
        # An exception on the way (a kubectl the setup needs, or a settle()
        # that timed out) keeps the API server log too.
        exiting = sys.exc_info()[0] is not None
        api.stop(keep_log=bool(failures) or exiting)
        if exiting:
            print(f"the API server log is kept: {api.log.name}")
        shutil.rmtree(work, ignore_errors=True)

    if failures:
        raise SystemExit(f"{len(failures)} failure(s); the API server log is kept: {api.log.name}")
    print("verify-hooks-secrets: ok")


if __name__ == "__main__":
    main()
