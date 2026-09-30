#!/usr/bin/env python3
"""Assert the runtime slice (giantswarm/agent-platform#317; giantswarm/giantswarm#37611, Phase 3).

examples/runtime-slice.yaml is the values profile of ONE `<cluster>-agent-platform`
release that runs agents on a workload cluster managed from the installation.
Each case pins one property the slice relies on:

- the profile renders exactly the runtime component set — kagent-crds,
  substrate-crds, substrate, kagent, agentgateway (the workload cluster runs no
  controller of its own) and the connectivity release; no muster, dicebear,
  valkey, Backstage, agent-manager, model-manager or MCP server, the engine off;
- the kagent release carries the profile's snapshot location (the platform
  Harness's snapshotPolicy.location) and the profile without it fails naming
  kagent.harness.snapshotLocation;
- with the target knob (ci/test-target-values.yaml) the same set, every
  HelmRelease carrying the kubeConfig, and no hook Job renders (a hook runs
  where the chart is installed, not on the target);
- the connectivity chart, rendered with the values the profile forwards, renders
  nothing of muster (no HTTPRoute, no policy selecting its pods), the
  Substrate hops' policies and the agentgateway controller's policy.

The live half (an agent placed on the workload cluster by agent-manager becomes
Ready) needs giantswarm/agent-manager#22: README "The runtime slice on workload
clusters". HELM selects the binary.
"""

import os
import re
import subprocess
import sys
import tempfile

HELM = os.environ.get("HELM", "helm")
FLEET_APIS = ["--api-versions", "kyverno.io/v1", "--api-versions", "cilium.io/v2", "--api-versions", "monitoring.coreos.com/v1",
              "--api-versions", "gateway.networking.k8s.io/v1"]
# The installation's platform inputs the profile is layered over.
INSTALLATION = ["--namespace", "org-acme", "--set", "global.domain=wc01.example.com", "--set", "global.identity.issuerUrl=https://dex.mc.example.com"]
RUNTIME = {"kagent-crds", "substrate-crds", "substrate", "kagent", "agentgateway", "agent-platform-connectivity"}
KUBECONFIG = "wc01-kubeconfig"
SNAPSHOTS = "s3://<bucket>/<cluster>"


def helm(chart: str, flags: list[str], expect_failure: str = "") -> str:
    result = subprocess.run([HELM, "template", "t", chart, *flags], capture_output=True, text=True, check=False)
    if expect_failure:
        if result.returncode == 0:
            sys.exit(f"FAIL: render of {chart} {' '.join(flags)} succeeded, a failure naming {expect_failure!r} was expected")
        if expect_failure not in result.stderr:
            sys.exit(f"FAIL: render of {chart} failed without naming {expect_failure!r}:\n{result.stderr}")
        return result.stderr
    if result.returncode != 0:
        sys.exit(f"FAIL: render of {chart} {' '.join(flags)} failed\n{result.stderr}")
    return result.stdout


def documents(manifest: str) -> dict[tuple[str, str], str]:
    docs = {}
    for doc in manifest.split("\n---\n"):
        kind = re.search(r"^kind: (\S+)$", doc, re.M)
        name = re.search(r"^  name: (\S+)$", doc, re.M)
        if kind and name:
            docs[(kind.group(1), name.group(1))] = doc.strip("\n")
    return docs


def releases(docs: dict[tuple[str, str], str]) -> dict[str, str]:
    return {name: doc for (kind, name), doc in docs.items() if kind == "HelmRelease"}


def values_block(doc: str) -> str:
    """The `values:` block of a HelmRelease, de-indented to a values file."""
    lines = doc.splitlines()
    for i, line in enumerate(lines):
        if line == "  values:":
            block = []
            for inner in lines[i + 1:]:
                if inner.strip() == "" or len(inner) - len(inner.lstrip()) > 2:
                    block.append(inner[4:] if inner.strip() else "")
                else:
                    break
            return "\n".join(block) + "\n"
    sys.exit("FAIL: the release carries no values block")


def ok(msg: str) -> None:
    print(f"ok: {msg}")


def check_profile(meta: str, profile: str) -> dict[str, str]:
    hrs = releases(documents(helm(meta, ["-f", profile, *FLEET_APIS, *INSTALLATION])))
    if set(hrs) != RUNTIME:
        sys.exit(f"FAIL: the profile renders {sorted(hrs)}, expected exactly {sorted(RUNTIME)}")
    if f"snapshotLocation: {SNAPSHOTS}" not in hrs["kagent"]:
        sys.exit(f"FAIL: the kagent release does not carry the profile's snapshot location {SNAPSHOTS}:\n{hrs['kagent']}")
    helm(meta, ["-f", profile, "--set", "kagent.harness.snapshotLocation=", *FLEET_APIS, *INSTALLATION], expect_failure="snapshotLocation")
    ok(f"examples/runtime-slice.yaml: exactly {len(RUNTIME)} releases, the engine off; the kagent release carries the snapshot location, and none fails the render")
    return hrs


def check_target(meta: str, profile: str) -> None:
    docs = documents(helm(meta, ["-f", profile, "-f", f"{meta}/ci/test-target-values.yaml", *FLEET_APIS, *INSTALLATION]))
    hrs = releases(docs)
    expected = {f"t-{name}" for name in RUNTIME}
    if set(hrs) != expected:
        sys.exit(f"FAIL: the targeted profile renders {sorted(hrs)}, expected exactly {sorted(expected)}")
    for name, doc in sorted(hrs.items()):
        if f"  kubeConfig:\n    secretRef:\n      name: {KUBECONFIG}" not in doc:
            sys.exit(f"FAIL: {name} does not carry spec.kubeConfig.secretRef {KUBECONFIG}:\n{doc}")
    if jobs := sorted(name for kind, name in docs if kind == "Job"):
        sys.exit(f"FAIL: the targeted profile renders hook Jobs {jobs}; a hook runs where the chart is installed, not on the target")
    ok(f"with the target knob: the runtime set ({len(hrs)} releases), every one targeting {KUBECONFIG}, no hook Job")


def check_connectivity(meta: str, connectivity: str, profile: str) -> None:
    hrs = releases(documents(helm(meta, ["-f", profile, "-f", f"{meta}/ci/test-target-values.yaml", *FLEET_APIS, *INSTALLATION])))
    with tempfile.NamedTemporaryFile("w", suffix=".yaml", encoding="utf-8") as values:
        values.write(values_block(hrs["t-agent-platform-connectivity"]))
        values.flush()
        docs = documents(helm(connectivity, ["-f", values.name, *FLEET_APIS, "--namespace", "org-acme"]))
    if muster := sorted(f"{kind}/{name}" for kind, name in docs if "muster" in name):
        sys.exit(f"FAIL: the connectivity release renders muster's wiring {muster}; the installation's muster serves the slice")
    if routes := sorted(name for kind, name in docs if kind == "HTTPRoute"):
        sys.exit(f"FAIL: the connectivity release renders HTTPRoutes {routes}; the slice has no public route of its own")
    for policy in ("substrate-workers", "substrate-atenet-egress", "agent-platform-connectivity-kagent-controller-egress", "agent-platform-connectivity-controller"):
        if ("CiliumNetworkPolicy", policy) not in docs:
            sys.exit(f"FAIL: the connectivity release lacks the CiliumNetworkPolicy {policy}")
    ok("connectivity with the forwarded values: nothing of muster, no HTTPRoute, the Substrate hops' and the agentgateway controller's policies")


def main() -> None:
    if len(sys.argv) != 3:
        sys.exit(f"usage: {sys.argv[0]} <meta chart dir> <connectivity chart dir>")
    meta, connectivity = sys.argv[1], sys.argv[2]
    profile = f"{meta}/examples/runtime-slice.yaml"
    check_profile(meta, profile)
    check_target(meta, profile)
    check_connectivity(meta, connectivity, profile)


if __name__ == "__main__":
    main()
