#!/usr/bin/env python3
"""Assert muster serves the Organizations' MCP servers with tenants.organizations.enabled.

The meta chart derives muster's mcpServersInAllNamespaces (giantswarm/muster#1601)
from the one tenants switch (agent-platform.muster.allNamespaces):
- off (the default): the muster release carries no mcpServersInAllNamespaces,
  and its values are those of the render with the switch on minus that key —
  the switch changes nothing else in muster's values;
- on: muster.mcpServersInAllNamespaces: true (an own true is a no-op), and
  muster.writesAsCaller.kubernetesAudience from mcp-kubernetes.kubernetesAudience,
  the audience the API server trusts (empty, none; an own equal value is a no-op);
- the muster chart of the floor (MUSTER_FLOOR, pulled from the registry)
  accepts the forwarded values and writes the setting and the audience into
  muster's config;
- the render fails, naming the key, for: an own value against the switch
  (either way), an own audience that differs, a muster range below
  MUSTER_FLOOR, muster's OAuth server off or not on dex, and no
  tenants.platform.users.

Needs PyYAML. HELM selects the binary. Network: gsoci.azurecr.io.

Usage: verify-tenant-muster.py META_CHART
"""

import os
import subprocess
import sys
import tempfile

import yaml

HELM = os.environ.get("HELM", "helm")
MUSTER_FLOOR = "5.48.0"
MUSTER_CHART = "oci://gsoci.azurecr.io/charts/giantswarm/muster"

BASE = [
    "--namespace", "agent-platform",
    "--set", "global.domain=example.com",
    "--set", "global.identity.issuerUrl=https://dex.example.com",
    "--set", "global.identity.clientId=agent-platform",
    "--set", "global.identity.existingSecret=agent-platform-idp",
]
ON = ["--set", "tenants.organizations.enabled=true"]


def fail(msg: str) -> None:
    sys.exit(f"FAIL: {msg}")


def helm(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([HELM, *args], capture_output=True, text=True, check=False)


def muster(meta: str, flags: list[str]) -> dict:
    r = helm("template", "t", meta, *BASE, *flags)
    if r.returncode != 0:
        fail(f"meta render {' '.join(flags)} failed\n{r.stderr}")
    for d in yaml.safe_load_all(r.stdout):
        if d and d.get("kind") == "HelmRelease" and d["metadata"]["name"] == "muster":
            return d["spec"]["values"]
    fail(f"no muster HelmRelease in the render {' '.join(flags)}")


def expect(what: str, got, want) -> None:
    if got != want:
        fail(f"{what}: got {got!r}, want {want!r}")


def main() -> None:
    meta = sys.argv[1]

    derived = ("mcpServersInAllNamespaces", "writesAsCaller")
    print("--> off: neither derived key; the switch adds them and changes nothing else")
    off = muster(meta, [])
    on = muster(meta, ON)
    for key in derived:
        if key in off["muster"]:
            fail(f"off: the muster release carries {key}: {off['muster'][key]!r}")
    expect("on: muster.mcpServersInAllNamespaces", on["muster"].get("mcpServersInAllNamespaces"), True)
    expect("on: muster.writesAsCaller", on["muster"].get("writesAsCaller"),
           {"kubernetesAudience": "dex-k8s-authenticator"})
    rest = {**on, "muster": {k: v for k, v in on["muster"].items() if k not in derived}}
    expect("on: muster's values beside the derived keys", rest, off)
    expect("on: an own true", muster(meta, [*ON, "--set", "muster.muster.mcpServersInAllNamespaces=true"]), on)
    print("ok: off and on")

    print("--> on: the audience follows mcp-kubernetes.kubernetesAudience")
    aud = ["--set", "mcp-kubernetes.kubernetesAudience=kubernetes"]
    expect("on, mcp-kubernetes.kubernetesAudience=kubernetes: muster.writesAsCaller",
           muster(meta, [*ON, *aud])["muster"].get("writesAsCaller"), {"kubernetesAudience": "kubernetes"})
    expect("on, an own equal audience: muster.writesAsCaller", muster(
        meta, [*ON, *aud, "--set", "muster.muster.writesAsCaller.kubernetesAudience=kubernetes"]
    )["muster"].get("writesAsCaller"), {"kubernetesAudience": "kubernetes"})
    expect("on, mcp-kubernetes.kubernetesAudience empty: muster.writesAsCaller",
           muster(meta, [*ON, "--set", "mcp-kubernetes.kubernetesAudience="])["muster"].get("writesAsCaller"), None)
    print("ok: audience")

    print(f"--> the muster chart {MUSTER_FLOOR} takes the forwarded values and serves every namespace")
    with tempfile.TemporaryDirectory() as tmp:
        r = helm("pull", MUSTER_CHART, "--version", MUSTER_FLOOR, "--untar", "--untardir", tmp)
        if r.returncode != 0:
            fail(f"could not pull {MUSTER_CHART} {MUSTER_FLOOR}\n{r.stderr}")
        values = os.path.join(tmp, "values.yaml")
        with open(values, "w", encoding="utf-8") as f:
            yaml.safe_dump(on, f)
        r = helm("template", "muster", os.path.join(tmp, "muster"), "-n", "agent-platform", "-f", values,
                 "--set", "muster.oauth.server.baseUrl=https://muster.example.com",
                 "--set", "muster.oauth.server.dex.issuerUrl=https://dex.example.com",
                 "--set", "muster.oauth.server.dex.clientId=agent-platform",
                 "--set", "muster.oauth.server.existingSecret=agent-platform-idp")
        if r.returncode != 0:
            fail(f"muster {MUSTER_FLOOR} rejects the forwarded values\n{r.stderr}")
        configs = [d for d in yaml.safe_load_all(r.stdout) if d and d.get("kind") == "ConfigMap"
                   and "config.yaml" in (d.get("data") or {})]
        if not configs:
            fail(f"muster {MUSTER_FLOOR} renders no ConfigMap with config.yaml")
        config = yaml.safe_load(configs[0]["data"]["config.yaml"])
        expect(f"muster {MUSTER_FLOOR} config.yaml: mcpServersInAllNamespaces",
               config.get("mcpServersInAllNamespaces"), True)
        expect(f"muster {MUSTER_FLOOR} config.yaml: writesAsCaller",
               config.get("writesAsCaller"), {"kubernetesAudience": "dex-k8s-authenticator"})
    print("ok: chart")

    print("--> refusals name what is missing")
    refusals = [
        ("an own true without the switch", ["--set", "muster.muster.mcpServersInAllNamespaces=true"],
         "muster.muster.mcpServersInAllNamespaces is true but tenants.organizations.enabled is not"),
        ("an own false with the switch", [*ON, "--set", "muster.muster.mcpServersInAllNamespaces=false"],
         "muster.muster.mcpServersInAllNamespaces is false but tenants.organizations.enabled is true"),
        ("an own audience that differs", [*ON, "--set", "muster.muster.writesAsCaller.kubernetesAudience=kubernetes"],
         "muster.muster.writesAsCaller.kubernetesAudience (kubernetes) differs from mcp-kubernetes.kubernetesAudience"),
        ("a muster range below the floor", [*ON, "--set", "components.muster.versionRange=>=5.47.0 <6.0.0"],
         f"needs muster {MUSTER_FLOOR} or later"),
        ("muster's OAuth server off", [*ON, "--set", "muster.muster.oauth.server.enabled=false"],
         "needs muster.muster.oauth.server.enabled with the provider dex"),
        ("muster's OAuth server on google", [*ON, "--set", "muster.muster.oauth.server.provider=google"],
         "needs muster.muster.oauth.server.enabled with the provider dex"),
        ("no tenants.platform.users", [*ON, "--set", "tenants.platform.users=null"],
         "needs tenants.platform.users"),
    ]
    for what, flags, message in refusals:
        r = helm("template", "t", meta, *BASE, *flags)
        if r.returncode == 0:
            fail(f"refusal, {what}: the render passed")
        if message not in r.stderr:
            fail(f"refusal, {what}: the render failed without naming it ({message!r})\n{r.stderr}")
        print(f"ok: refused: {what}")


if __name__ == "__main__":
    main()
