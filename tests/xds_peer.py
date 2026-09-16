#!/usr/bin/env python3
"""Drop the agentgateway controller's GatewayClass xDS peer from a render.

The controller's ingress policy admits xDS from every data plane of the
platform's GatewayClass in any namespace (giantswarm/agent-platform#495) —
rendered unconditionally by both policy flavours, so it is the one intended
difference to a golden ref that predates it. The golden comparisons
(tests/verify-target.py, `make verify-wiring`) pass the head render through
this before diffing. Drop the module once GOLDEN_REF carries the peer.

Usage: python3 tests/xds_peer.py < render > normalized
"""

import re
import sys

XDS_CLASS_PEER = re.compile(
    r"\n {8}# Every other data plane this controller provisions.*?gateway\.networking\.k8s\.io/gateway-class-name: [a-z0-9-]+\n"
    r"(?: {10}matchExpressions:\n {12}- key: k8s:io\.kubernetes\.pod\.namespace\n {14}operator: Exists\n)?",
    re.S,
)


def drop(render: str) -> tuple[str, int]:
    """The render without the peer block(s), and how many were dropped."""
    return XDS_CLASS_PEER.subn("\n", render)


if __name__ == "__main__":
    out, _ = drop(sys.stdin.read())
    sys.stdout.write(out)
