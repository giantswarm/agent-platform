#!/usr/bin/env python3
"""Assert Substrate's credential provider follows tenants.organizations.enabled (giantswarm/agent-platform#1041).

The meta chart derives the substrate release's credentialProvider.namespacePolicies
(agent-platform.componentDerivedValues) from the switch that admits the
Organizations' workers (giantswarm/agent-platform#1036):
- off (the default): the policies are the chart's two static entries, and the
  substrate release's values are those of the render with the switch on but for
  the policies — the switch changes nothing else;
- on: an ownNamespaceSelector entry on the label giantswarm.io/organization
  (each Organization's namespace is read by the atespace of its own name only)
  and the same selector as allowedNamespaceSelector on the ate-golden entry
  (every template's golden actor boots there);
- an installation's own policies stay: its entries keep their place and fields,
  an ate-golden entry gains the selector (one is added when none names
  ate-golden), a derived entry it already carries is not repeated, and an
  ate-golden entry selecting other namespaces fails the render naming the key;
- a Substrate floor below 1.12.0 fails the render with the switch on: the
  older provider refuses the selectors at start;
- the Substrate chart at the range's floor (while the floor is unpublished,
  the chart verify-components-charts renders against: UNRELEASED,
  RENDER_AGAINST) takes the
  forwarded values: its policy file carries both entries and it grants the
  provider list/watch on namespaces.

Needs PyYAML. HELM selects the binary. Network: gsoci.azurecr.io.

Usage: verify-tenant-credentials.py META_CHART
"""

import importlib.util
import json
import os
import subprocess
import sys
import tempfile

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location("components_charts", os.path.join(HERE, "verify-components-charts.py"))
cc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cc)

HELM = os.environ.get("HELM", "helm")

BASE = [
    "--namespace", "agent-platform",
    "--set", "global.domain=example.com",
    "--set", "global.identity.issuerUrl=https://dex.example.com",
    "--set", "global.identity.clientId=agent-platform",
    "--set", "global.identity.existingSecret=agent-platform-idp",
    "--set", "kagent.harness.snapshotLocation=s3://ci-agent-snapshots/agents",
    "--set", "kagent.database.postgres.urlFile=/etc/kagent-db/uri",
    "--set", "postgres.enabled=true",
    "--set", "components.kagent.enabled=true",
]
ON = ["--set", "tenants.organizations.enabled=true"]

ORGS = {"matchExpressions": [{"key": "giantswarm.io/organization", "operator": "Exists"}]}
STATIC = [
    {"atespace": "kagent", "allowedNamespaces": ["kagent"]},
    {"atespace": "ate-golden", "allowedNamespaces": ["kagent"]},
]
TENANTS = [
    {"atespace": "kagent", "allowedNamespaces": ["kagent"]},
    {"atespace": "ate-golden", "allowedNamespaces": ["kagent"], "allowedNamespaceSelector": ORGS},
    {"ownNamespaceSelector": ORGS},
]


def fail(msg: str) -> None:
    sys.exit(f"FAIL: {msg}")


def helm(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([HELM, *args], capture_output=True, text=True, check=False)


def own_policies(tmp: str, name: str, policies: list) -> list[str]:
    path = os.path.join(tmp, f"{name}.yaml")
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump({"substrate": {"credentialProvider": {"namespacePolicies": policies}}}, f)
    return ["-f", path]


def substrate(meta: str, flags: list[str]) -> dict:
    r = helm("template", "t", meta, *BASE, *flags)
    if r.returncode != 0:
        fail(f"meta render {' '.join(flags)} failed\n{r.stderr}")
    for d in yaml.safe_load_all(r.stdout):
        if d and d.get("kind") == "HelmRelease" and d["metadata"]["name"] == "substrate":
            return d["spec"]["values"]
    fail(f"no substrate HelmRelease in the render {' '.join(flags)}")


def policies(values: dict) -> list:
    return (values.get("credentialProvider") or {}).get("namespacePolicies")


def expect(what: str, got, want) -> None:
    if got != want:
        fail(f"{what}:\n got  {json.dumps(got)}\n want {json.dumps(want)}")


def without_policies(values: dict) -> dict:
    out = json.loads(json.dumps(values))
    out.get("credentialProvider", {}).pop("namespacePolicies", None)
    return out


def main() -> None:
    meta = sys.argv[1]

    print("--> off: the static policies; the switch changes nothing else in the substrate release")
    off = substrate(meta, [])
    on = substrate(meta, ON)
    expect("off: namespacePolicies", policies(off), STATIC)
    expect("the substrate release's values beside namespacePolicies, on against off", without_policies(on), without_policies(off))
    print("ok: off")

    print("--> on: the Organizations' namespaces by label, own namespace and ate-golden")
    expect("on: namespacePolicies", policies(on), TENANTS)
    print("ok: on")

    with tempfile.TemporaryDirectory() as tmp:
        print("--> on: an installation's own policies stay, each derived entry once")
        team = {"atespace": "team-a", "allowedNamespaces": ["team-a"]}
        golden = {"atespace": "ate-golden", "allowedNamespaces": ["kagent", "team-a"], "secretSelector": {"matchLabels": {"agents": "true"}}}
        expect("on, own policies with ate-golden", policies(substrate(meta, [*ON, *own_policies(tmp, "own", [team, golden])])),
               [team, {**golden, "allowedNamespaceSelector": ORGS}, {"ownNamespaceSelector": ORGS}])
        expect("on, own policies without ate-golden", policies(substrate(meta, [*ON, *own_policies(tmp, "no-golden", [team])])),
               [team, {"atespace": "ate-golden", "allowedNamespaceSelector": ORGS}, {"ownNamespaceSelector": ORGS}])
        expect("on, own policies already carrying the derived entries", policies(substrate(meta, [*ON, *own_policies(tmp, "same", TENANTS)])), TENANTS)
        print("ok: own policies")

        print("--> on: an ate-golden entry selecting other namespaces, and a Substrate floor below 1.12.0, are refused")
        other = {"atespace": "ate-golden", "allowedNamespaceSelector": {"matchLabels": {"team": "a"}}}
        r = helm("template", "t", meta, *BASE, *ON, *own_policies(tmp, "other", [other]))
        if r.returncode == 0:
            fail("an ate-golden allowedNamespaceSelector other than the Organizations' renders")
        if "substrate.credentialProvider.namespacePolicies[0] (ate-golden) has an allowedNamespaceSelector" not in r.stderr:
            fail(f"an ate-golden allowedNamespaceSelector other than the Organizations' failed without naming the key\n{r.stderr}")
        r = helm("template", "t", meta, *BASE, *ON, "--set", "components.substrate.versionRange=>=1.11.4 <1.12.0")
        if r.returncode == 0 or "tenants.organizations.enabled needs Substrate 1.12.0 or later" not in r.stderr:
            fail(f"a Substrate floor below 1.12.0 with the switch on is not refused naming the floor\n{r.stderr}")
        print("ok: refused")

        rendered = cc.docs(cc.render_meta(meta, [*BASE[2:], *ON]))
        url, rng = cc.source(rendered[("OCIRepository", "substrate")])
        tags = cc.registry_tags(url)
        version = cc.floor(rng)
        if version not in tags:
            version = cc.fallback("substrate", url, rng, tags, "")
        print(f"--> the Substrate chart {version} takes the forwarded policies")
        cc.pull(url, version, tmp)
        values = os.path.join(tmp, "forwarded.yaml")
        with open(values, "w", encoding="utf-8") as f:
            yaml.safe_dump(on, f)
        r = helm("template", "substrate", os.path.join(tmp, "substrate"), "-n", "ate-system", "-f", values)
        if r.returncode != 0:
            fail(f"the Substrate chart {version} rejects the forwarded values\n{r.stderr}")
        docs = [d for d in yaml.safe_load_all(r.stdout) if d]
        files = [f for d in docs if d.get("kind") == "ConfigMap" for v in (d.get("data") or {}).values()
                 if isinstance(f := yaml.safe_load(v), dict) and list(f) == ["policies"]]
        if [f["policies"] for f in files] != [TENANTS]:
            fail(f"the Substrate chart {version} writes no policy file with the forwarded policies: {files}")
        reader = [d for d in docs if d.get("kind") == "ClusterRole" and d["metadata"]["name"].endswith("k8s-credential-provider-namespace-reader")]
        if not reader or {"apiGroups": [""], "resources": ["namespaces"], "verbs": ["list", "watch"]} not in reader[0].get("rules", []):
            fail(f"the Substrate chart {version} grants the credential provider no list/watch on namespaces: {reader}")
        print(f"ok: the Substrate chart {version} writes both entries and grants list/watch on namespaces")


if __name__ == "__main__":
    main()
