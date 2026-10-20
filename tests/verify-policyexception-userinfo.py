#!/usr/bin/env python3
"""Assert no rendered Kyverno PolicyException matches on user info while background scans apply it.

Kyverno refuses a PolicyException whose match (any or all) names `subjects`,
`roles` or `clusterRoles` while `spec.background` is true, its default
(api/kyverno/v2 PolicyException.Validate, ValidateNoUserInfo): a background
scan has no request, so no writer to match. The admission webhook denies the
object, and the release that carries it fails its upgrade.

Every PolicyException of the meta and connectivity charts is rendered here, in
the values sets that turn each one on: Agent Substrate with
tenants.organizations off and on, the pgvector ImageVolume, model serving, and
the workspaces' CSI companion per preset (the meta chart). An exception that
matches on user info must carry `background: false`; each one that does not
fails, named with the offending entries. Every chart template that renders a
PolicyException must render one in some set, so a new template is held too.

Usage:
  verify-policyexception-userinfo.py META_CHART CONNECTIVITY_CHART
  verify-policyexception-userinfo.py --manifest FILE...   (check rendered manifests)

Needs PyYAML. HELM selects the binary.
"""

import os
import pathlib
import subprocess
import sys

import yaml

HELM = os.environ.get("HELM", "helm")
USER_INFO = ("subjects", "roles", "clusterRoles")
FLEET_APIS = ["--api-versions", "kyverno.io/v1", "--api-versions", "cilium.io/v2", "--api-versions", "monitoring.coreos.com/v1",
              "--api-versions", "gateway.networking.k8s.io/v1", "--api-versions", "gateway.envoyproxy.io/v1alpha1"]
KAGENT = ["--set", "components.kagent.enabled=true", "--set", "kagent.harness.snapshotLocation=s3://ci-agent-snapshots/agents",
          "--set", "kagent.database.postgres.urlFile=/etc/kagent-db/uri"]
KYVERNO = ["--set", "kyvernoPolicies.enabled=true"]
CONNECTIVITY = ["--namespace", "agent-platform", "--set", "ingress.parentRefs[0].name=x", *KAGENT, *KYVERNO, *FLEET_APIS]
SUBSTRATE = ["--set", "components.substrate.enabled=true", "--set", "components.substrate-crds.enabled=true"]
# tenants.organizations.enabled needs the llm listener and its routing policy (giantswarm/agent-platform#759).
TENANTS = ["--set", "tenants.organizations.enabled=true", "--set", "llmRouting.enabled=true",
           "--api-versions", "admissionregistration.k8s.io/v1/MutatingAdmissionPolicy"]
PGVECTOR = ["--set", "postgres.enabled=true", "--set", "postgres.vector.enabled=true",
            "--set", "postgres.vector.extensionImage.reference=gsoci.azurecr.io/giantswarm/pgvector:0.8.2-18-bookworm"]
MODEL_SERVING = ["--set", "components.modelServing.enabled=true", "--set", "components.kserve-llmisvc-crd.enabled=true",
                 "--set", "components.kserve-llmisvc-resources.enabled=true", "--set", "modelServing.namespace.name=llm-serving"]
# muster's credential provider releases an instance while workspaces and Substrate are on
# (agent-platform.validateWorkspaceAuth): no provider instance to pin here, so the renders name it.
RELEASABLE = ["--set", "muster.muster.credentialProvider.releasable.github=https://github.com/apps/ci-agents"]


def connectivity_sets():
    sets = {
        "substrate": SUBSTRATE,
        "substrate, tenants.organizations on": SUBSTRATE + TENANTS,
        "postgres pgvector ImageVolume": PGVECTOR,
        "model serving": MODEL_SERVING,
        "everything, tenants.organizations on": SUBSTRATE + TENANTS + PGVECTOR + MODEL_SERVING,
    }
    return {label: CONNECTIVITY + args for label, args in sets.items()}


def meta_sets(meta):
    base = ["--namespace", "agent-platform", "-f", f"{meta}/ci/ci-values.yaml", "--set", "components.flux.enabled=false",
            *KAGENT, *KYVERNO, *FLEET_APIS, *RELEASABLE]
    return {
        "ci-values": base,
        "ci-values, tenants.organizations on": base + TENANTS,
        "CSI companion efs": base + ["-f", "tests/fixtures/workspaces-csi-companion-efs-values.yaml"],
        "CSI companion azureFiles": base + ["-f", "tests/fixtures/workspaces-csi-companion-azure-files-values.yaml"],
    }


def fail(msg):
    sys.exit(f"FAIL: {msg}")


def documents(text):
    """(source template, document) of every PolicyException in a rendered stream."""
    out = []
    for chunk in text.split("\n---"):
        source = next((line.split(":", 1)[1].strip() for line in chunk.splitlines() if line.startswith("# Source:")), None)
        doc = yaml.safe_load(chunk)
        if isinstance(doc, dict) and doc.get("kind") == "PolicyException":
            out.append((source, doc))
    return out


def offences(doc):
    """The match entries of an exception that name user info while background scans apply it."""
    spec = doc.get("spec") or {}
    if spec.get("background", True) is False:
        return []
    match = spec.get("match") or {}
    found = []
    for block in ("any", "all"):
        for i, entry in enumerate(match.get(block) or []):
            for field in USER_INFO:
                if (entry or {}).get(field):
                    found.append(f"spec.match.{block}[{i}].{field}")
    return found


def check(label, exceptions):
    bad = []
    for _, doc in exceptions:
        fields = offences(doc)
        if fields:
            meta = doc.get("metadata") or {}
            name = f"{meta.get('namespace', '')}/{meta.get('name')}".lstrip("/")
            background = (doc.get("spec") or {}).get("background")
            background = "unset (true)" if background is None else str(background).lower()
            bad.append(f"  PolicyException {name} ({label}): background {background} with {', '.join(fields)}")
    return bad


def render(chart, args):
    r = subprocess.run([HELM, "template", "t", chart, *args], capture_output=True, text=True)
    if r.returncode != 0:
        fail(f"helm template {chart} {' '.join(args)}:\n{r.stderr}")
    return r.stdout


def templates_with_exceptions(chart):
    root = pathlib.Path(chart)
    name = yaml.safe_load((root / "Chart.yaml").read_text())["name"]
    return {f"{name}/{p.relative_to(root)}" for p in (root / "templates").rglob("*.yaml") if "kind: PolicyException" in p.read_text()}


def report(bad):
    if bad:
        fail("Kyverno refuses a PolicyException that matches on user info (subjects, roles, clusterRoles) while background "
             "scans apply it; set background: false on it, or match without user info:\n" + "\n".join(bad))


def main():
    if sys.argv[1:2] == ["--manifest"]:
        bad, seen = [], 0
        for path in sys.argv[2:]:
            exceptions = documents(pathlib.Path(path).read_text())
            seen += len(exceptions)
            bad += check(path, exceptions)
        report(bad)
        print(f"ok: {seen} PolicyExceptions, none matches on user info with background scans on")
        return
    meta, connectivity = sys.argv[1], sys.argv[2]
    bad, rendered, names = [], set(), set()
    for chart, sets in ((connectivity, connectivity_sets()), (meta, meta_sets(meta))):
        for label, args in sets.items():
            exceptions = documents(render(chart, args))
            rendered |= {source for source, _ in exceptions}
            names |= {d["metadata"]["name"] for _, d in exceptions}
            bad += check(f"{pathlib.Path(chart).name}: {label}", exceptions)
    report(bad)
    missing = (templates_with_exceptions(meta) | templates_with_exceptions(connectivity)) - rendered
    if missing:
        fail(f"no values set here renders a PolicyException of {sorted(missing)}; add one that turns it on")
    print(f"ok: {len(names)} PolicyExceptions of the meta and connectivity charts ({', '.join(sorted(names))}), "
          "none matches on user info with background scans on")


if __name__ == "__main__":
    main()
