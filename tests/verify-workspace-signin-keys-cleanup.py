#!/usr/bin/env python3
"""Assert the cleanup of the workspace-manager sign-in keys Secret (giantswarm/agent-platform#1095).

Earlier releases of the connectivity chart created the Secret
workspace-manager-signin-keys with helm.sh/resource-policy: keep; the
workspace-manager keeps no sign-in store, and the upgrade that stopped rendering
the Secret left it behind. Each case below pins one property:

- the chart renders no sign-in keys Secret, with providers and oauth.enabled;
- offline (no cluster to look up) no cleanup Job and no delete rule render, so
  an install or an upgrade without the Secret carries nothing for it;
- on a cluster (LIVE_CONTEXT=<kube context>, else skipped): with the Secret
  there and this release's (meta.helm.sh/release-name and -namespace), ONE
  post-install,post-upgrade hook Job deletes exactly it as the hook identity,
  whose ClusterRole gains delete on exactly that Secret and is created for the
  post events; a Secret of the name another release holds renders neither.

Deliberately stdlib-only: the CI image has no PyYAML. HELM selects the binary.
"""

import os
import re
import subprocess
import sys

HELM = os.environ.get("HELM", "helm")
LIVE_CONTEXT = os.environ.get("LIVE_CONTEXT", "")
SECRET = "workspace-manager-signin-keys"
RELEASE = "t"
JOB = f"{RELEASE}-workspace-manager-signin-keys-cleanup"
FIXTURE = "tests/fixtures/workspaces-values.yaml"
CONN = ["--set", "ingress.parentRefs[0].name=x", "--set", "components.workspace-manager.enabled=true", "-f", FIXTURE,
        "--set", "global.domain=ci.example.com", "--set", "global.identity.issuerUrl=https://dex.ci.example.com",
        "--set", "global.identity.clientId=platform", "--set", "global.identity.existingSecret=platform-oauth"]


def fail(msg: str) -> None:
    sys.exit(f"FAIL: {msg}")


def ok(msg: str) -> None:
    print(f"ok: {msg}")


def helm(chart: str, flags: list, namespace: str = "", live: bool = False) -> str:
    cmd = [HELM, "template", RELEASE, chart, *flags]
    if namespace:
        cmd += ["--namespace", namespace]
    if live:
        cmd += ["--dry-run=server", "--kube-context", LIVE_CONTEXT]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        fail(f"the render failed: {' '.join(flags)}\n{r.stderr}")
    return r.stdout


def documents(render: str) -> dict:
    """(kind, name) -> the document, for every document that has both."""
    out = {}
    for doc in render.split("\n---\n"):
        kind = re.search(r"^kind: (\S+)", doc, re.M)
        name = re.search(r"^  name: (\S+)", doc, re.M)
        if kind and name:
            out[(kind.group(1), name.group(1))] = doc.rstrip("\n") + "\n"
    return out


def delete_rule(docs: dict) -> bool:
    role = docs.get(("ClusterRole", f"{RELEASE}-hooks"), "")
    return f'resourceNames: ["{SECRET}"]\n    verbs: ["delete"]' in role


def live(connectivity: str) -> None:
    if not LIVE_CONTEXT:
        print("skip: LIVE_CONTEXT is empty; the cleanup renders from a lookup of the live Secret, which needs a cluster")
        return
    kubectl = ["kubectl", "--context", LIVE_CONTEXT]
    ns = "ap-verify-signin-keys-cleanup"
    subprocess.run([*kubectl, "create", "namespace", ns], check=True, capture_output=True)
    try:
        def secret(release: str) -> None:
            manifest = (f"apiVersion: v1\nkind: Secret\nmetadata:\n  name: {SECRET}\n  namespace: {ns}\n"
                        f"  annotations:\n    helm.sh/resource-policy: keep\n    meta.helm.sh/release-name: {release}\n"
                        f"    meta.helm.sh/release-namespace: {ns}\ntype: Opaque\ndata:\n  key-1: AAAA\n")
            subprocess.run([*kubectl, "apply", "-f", "-"], input=manifest, text=True, check=True, capture_output=True)

        secret(RELEASE)
        docs = documents(helm(connectivity, CONN, namespace=ns, live=True))
        job = docs.get(("Job", JOB))
        if not job:
            fail(f"the release's own {SECRET} is there but no cleanup Job {JOB} renders")
        for line in ("helm.sh/hook: post-install,post-upgrade", f"serviceAccountName: {RELEASE}-hooks",
                     f'kubectl -n "{ns}" delete secret "{SECRET}" --ignore-not-found'):
            if line not in job:
                fail(f"the cleanup Job lacks {line!r}:\n{job}")
        if not delete_rule(docs):
            fail(f"the hook identity's ClusterRole lacks delete on exactly {SECRET}:\n{docs.get(('ClusterRole', f'{RELEASE}-hooks'))}")
        if "post-upgrade" not in docs[("ServiceAccount", f"{RELEASE}-hooks")]:
            fail("the hook identity is not created for post-upgrade")
        if any(k == "Secret" and n == SECRET for k, n in docs):
            fail(f"the render carries {SECRET} itself")
        ok(f"live ({LIVE_CONTEXT}): the release's own {SECRET} renders ONE post-install,post-upgrade Job deleting exactly it as the hook identity, with delete on exactly it")

        secret("another-release")
        docs = documents(helm(connectivity, CONN, namespace=ns, live=True))
        if ("Job", JOB) in docs or delete_rule(docs):
            fail(f"a {SECRET} another release holds renders the cleanup")
        ok(f"live ({LIVE_CONTEXT}): a {SECRET} another release holds is left alone")
    finally:
        subprocess.run([*kubectl, "delete", "namespace", ns, "--ignore-not-found", "--wait=false"], check=True, capture_output=True)


def main(connectivity: str) -> int:
    docs = documents(helm(connectivity, CONN))
    if ("Deployment", "workspace-manager") in docs:
        fail("the connectivity chart renders the manager itself")
    if any(k == "Secret" and n == SECRET for k, n in docs):
        fail(f"the connectivity chart renders {SECRET} with providers and oauth on")
    ok(f"no {SECRET} Secret renders, with providers and oauth on")
    if ("Job", JOB) in docs or delete_rule(docs):
        fail("offline (no Secret to look up) the cleanup Job or its delete rule renders")
    ok("offline: no cleanup Job and no delete rule")
    live(connectivity)
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: verify-workspace-signin-keys-cleanup.py <connectivity chart>")
    sys.exit(main(sys.argv[1]))
