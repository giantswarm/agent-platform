#!/usr/bin/env python3
"""Assert the network path to Substrate's actor-JWT issuer (giantswarm/muster#1572).

ate-idp-server (the Substrate line from 1.13.0) serves the discovery document and
key set of the issuer every actor JWT names, on 8443 behind the Service idp:443.
muster verifies a Session's injected actor JWT against that key set
(muster.credentialProvider.actorBearer). Each case pins one property, in both
flavours:

- with Substrate and muster on: ate-idp-server's ingress admits muster from the
  release namespace on 8443 and nothing else (plus the kubelet's probes, the host
  entity, in the cilium flavour); muster's egress reaches ate-idp-server on 8443
  in ate-system by its own policy, whatever networkPolicy.musterInClusterMcpPorts
  opens;
- with muster off: ate-idp-server admits no muster and no muster egress renders;
- with Substrate off: neither policy renders.

Usage: verify-substrate-idp-netpol.py <connectivity chart dir> <helm flags…>
Deliberately stdlib-only: the CI image has no PyYAML. HELM selects the binary.
"""

import os
import re
import subprocess
import sys

HELM = os.environ.get("HELM", "helm")
NS = "ate-system"
RELEASE_NS = "agent-platform"
IDP = {"cilium": ("CiliumNetworkPolicy", "substrate-ate-idp-server"), "kubernetes": ("NetworkPolicy", "substrate-ate-idp-server-ingress")}
EGRESS = "muster-to-substrate-idp"


def fail(msg: str) -> None:
    sys.exit(f"FAIL: {msg}")


def ok(msg: str) -> None:
    print(f"ok: {msg}")


def documents(chart: str, flags: list) -> dict:
    r = subprocess.run([HELM, "template", "t", chart, "-n", RELEASE_NS, *flags], capture_output=True, text=True)
    if r.returncode != 0:
        fail(f"the render failed: {' '.join(flags)}\n{r.stderr}")
    out = {}
    for doc in r.stdout.split("\n---\n"):
        kind = re.search(r"^kind: (\S+)", doc, re.M)
        name = re.search(r"^  name: (\S+)", doc, re.M)
        ns = re.search(r"^  namespace: (\S+)", doc, re.M)
        if kind and name:
            out[(kind.group(1), name.group(1))] = (ns.group(1) if ns else "", doc)
    return out


def squash(doc: str) -> str:
    """The document without comments and blank lines, for structural matches."""
    return "\n".join(l for l in doc.splitlines() if l.strip() and not l.strip().startswith("#"))


def main() -> int:
    chart, base = sys.argv[1], sys.argv[2:]
    for flavor in ("cilium", "kubernetes"):
        flags = [*base, "--set", f"networkPolicy.flavor={flavor}", "--set", "networkPolicy.musterInClusterMcpPorts=null"]
        docs = documents(chart, flags)
        kind, name = IDP[flavor]
        ns, idp = docs.get((kind, name), ("", ""))
        if not idp:
            fail(f"{flavor}: no {kind} {name} with Substrate and muster on")
        if ns != NS:
            fail(f"{flavor}: {name} is in {ns!r}, not {NS}")
        idp = squash(idp)
        if flavor == "cilium":
            want = (f"  ingress:\n    - fromEndpoints:\n        - matchLabels:\n            app.kubernetes.io/name: muster\n"
                    f"            io.kubernetes.pod.namespace: {RELEASE_NS}\n      toPorts:\n        - ports:\n"
                    f"            - port: \"8443\"\n              protocol: TCP\n    - fromEntities:\n        - host\n"
                    f"      toPorts:\n        - ports:\n            - port: \"8443\"\n              protocol: TCP\n  egress:")
        else:
            want = (f"  ingress:\n    - from:\n        - namespaceSelector:\n            matchLabels:\n"
                    f"              kubernetes.io/metadata.name: {RELEASE_NS}\n          podSelector:\n            matchLabels:\n"
                    f"              app.kubernetes.io/name: muster\n      ports:\n        - port: 8443\n          protocol: TCP")
        if want not in idp or "      app: ate-idp-server" not in idp:
            fail(f"{flavor}: {name} does not select ate-idp-server admitting exactly muster on 8443:\n{idp}")
        ens, egress = docs.get((kind, EGRESS), ("", ""))
        if not egress or ens != RELEASE_NS:
            fail(f"{flavor}: no {kind} {EGRESS} in {RELEASE_NS} with networkPolicy.musterInClusterMcpPorts empty")
        egress = squash(egress)
        target = (f"app: ate-idp-server\n            io.kubernetes.pod.namespace: {NS}" if flavor == "cilium"
                  else f"kubernetes.io/metadata.name: {NS}\n          podSelector:\n            matchLabels:\n              app: ate-idp-server")
        if "app.kubernetes.io/name: muster" not in egress or target not in egress or "8443" not in egress:
            fail(f"{flavor}: {EGRESS} does not open muster's egress to ate-idp-server:8443 in {NS}:\n{egress}")
        ok(f"{flavor}: ate-idp-server admits muster from {RELEASE_NS} on 8443 only (and the kubelet's probes), "
           f"muster's egress reaches it by {EGRESS} with musterInClusterMcpPorts empty")

        off = documents(chart, [*flags, "--set", "muster.enabled=false"])
        _, idp = off.get((kind, name), ("", ""))
        if not idp or "app.kubernetes.io/name: muster" in idp or (kind, EGRESS) in off:
            fail(f"{flavor}: with muster off ate-idp-server still admits muster, or its egress policy renders")
        none = documents(chart, [*flags, "--set", "components.substrate.enabled=false", "--set", "components.substrate-crds.enabled=false", "--set", "components.kagent.enabled=false"])
        if (kind, name) in none or (kind, EGRESS) in none:
            fail(f"{flavor}: with Substrate off a policy for ate-idp-server renders")
        ok(f"{flavor}: muster off admits no muster and renders no egress policy; Substrate off renders neither")
    return 0


if __name__ == "__main__":
    sys.exit(main())
