#!/usr/bin/env python3
"""Assert a workspace Session's directory lives as long as its Session (giantswarm/agent-platform#1007).

kagent deletes a Session idle for kagent.controller.sessionIdleTTL and the
workspace-manager removes its directory at once, so the TTL bounds every
directory whatever workspace-manager.sessions.cleanupAfter says. One value
drives both. Each case below pins one property:

- the kagent release carries controller.sessionIdleTTL 336h by default, the 14
  days of inactivity after which a Session goes with its directory (the kagent
  chart's own default is 168h), with workspaces off too;
- with workspaces on the workspace-manager release carries
  sessions.cleanupAfter equal to the TTL: 336h by default, an installation's
  own TTL followed (240h, 1h30m);
- an own cleanupAfter shorter than or equal to the TTL stands (72h, 90m against
  1h30m); a longer one fails the render naming both keys;
- a TTL of 0 never deletes a Session: it reaches kagent and derives no
  cleanupAfter (the manager refuses 0 and keeps its own default);
- a TTL that is not a Go duration (30d) fails the render naming the key.

Deliberately stdlib-only: the CI image has no PyYAML. HELM selects the binary.
"""

import os
import re
import subprocess
import sys

HELM = os.environ.get("HELM", "helm")
CI = ["--set", "components.flux.enabled=false"]
ON = ["--set", "workspaces.enabled=true"]


def fail(msg: str) -> None:
    sys.exit(f"FAIL: {msg}")


def ok(msg: str) -> None:
    print(f"ok: {msg}")


def helm(chart: str, flags: list, expect_fail: bool = False) -> str:
    cmd = [HELM, "template", "t", chart, "-f", f"{chart}/ci/ci-values.yaml", *CI, *flags]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if expect_fail:
        if r.returncode == 0:
            fail(f"the render passed but had to fail: {' '.join(flags)}")
        return r.stderr
    if r.returncode != 0:
        fail(f"the render failed: {' '.join(flags)}\n{r.stderr}")
    return r.stdout


def release(render: str, name: str) -> str:
    """The HelmRelease of that name, or "" when none renders."""
    for doc in render.split("\n---\n"):
        if re.search(r"^kind: HelmRelease$", doc, re.M) and re.search(rf"^  name: {re.escape(name)}$", doc, re.M):
            return doc
    return ""


def session_idle_ttl(render: str) -> str:
    doc = release(render, "kagent")
    if not doc:
        fail("no kagent HelmRelease rendered")
    m = re.search(r"^    controller:\n(?:      .*\n|\s*\n)*?      sessionIdleTTL: \"?([^\"\n]*)\"?$", doc, re.M)
    if not m:
        fail(f"the kagent release carries no controller.sessionIdleTTL:\n{doc}")
    return m.group(1)


def cleanup_after(render: str) -> str:
    """workspace-manager's sessions.cleanupAfter, "" when not forwarded."""
    doc = release(render, "workspace-manager")
    if not doc:
        fail("no workspace-manager HelmRelease rendered")
    m = re.search(r"^    sessions:\n      cleanupAfter: \"?([^\"\n]*)\"?$", doc, re.M)
    return m.group(1) if m else ""


def must_fail(chart: str, flags: list, needles: tuple, what: str) -> None:
    err = helm(chart, flags, expect_fail=True)
    for needle in needles:
        if needle not in err:
            fail(f"{what} failed for another reason (expected {needle!r}):\n{err}")
    ok(f"{what} fails naming {', '.join(needles)}")


def main(chart: str) -> None:
    off = helm(chart, [])
    if session_idle_ttl(off) != "336h":
        fail(f"the kagent release's sessionIdleTTL is {session_idle_ttl(off)!r}, not 336h, with workspaces off")
    if release(off, "workspace-manager"):
        fail("a workspace-manager release renders with workspaces off")
    ok("kagent carries sessionIdleTTL 336h with workspaces off")

    on = helm(chart, ON)
    if (session_idle_ttl(on), cleanup_after(on)) != ("336h", "336h"):
        fail(f"workspaces on: sessionIdleTTL {session_idle_ttl(on)!r}, cleanupAfter {cleanup_after(on)!r}, not 336h and 336h")
    ok("workspaces on: sessionIdleTTL 336h and cleanupAfter 336h, one value")

    for ttl in ("240h", "1h30m"):
        r = helm(chart, [*ON, "--set", f"kagent.controller.sessionIdleTTL={ttl}"])
        if (session_idle_ttl(r), cleanup_after(r)) != (ttl, ttl):
            fail(f"TTL {ttl}: sessionIdleTTL {session_idle_ttl(r)!r}, cleanupAfter {cleanup_after(r)!r}")
        ok(f"an own TTL {ttl} reaches kagent and is the cleanupAfter")

    for ttl, own in (("720h", "72h"), ("1h30m", "90m")):
        r = helm(chart, [*ON, "--set", f"kagent.controller.sessionIdleTTL={ttl}", "--set", f"workspace-manager.sessions.cleanupAfter={own}"])
        if cleanup_after(r) != own:
            fail(f"an own cleanupAfter {own} under TTL {ttl} became {cleanup_after(r)!r}")
        ok(f"an own cleanupAfter {own}, not longer than the TTL {ttl}, stands")

    for ttl, own in (("720h", "1000h"), ("1h30m", "91m")):
        must_fail(chart, [*ON, "--set", f"kagent.controller.sessionIdleTTL={ttl}", "--set", f"workspace-manager.sessions.cleanupAfter={own}"],
                  (f"workspace-manager.sessions.cleanupAfter ({own}) is longer than kagent.controller.sessionIdleTTL ({ttl})",),
                  f"an own cleanupAfter {own} over the TTL {ttl}")

    zero = helm(chart, [*ON, "--set-string", "kagent.controller.sessionIdleTTL=0"])
    if (session_idle_ttl(zero), cleanup_after(zero)) != ("0", ""):
        fail(f"TTL 0: sessionIdleTTL {session_idle_ttl(zero)!r}, cleanupAfter {cleanup_after(zero)!r}, not 0 and none")
    ok("a TTL of 0 reaches kagent and derives no cleanupAfter")

    must_fail(chart, [*ON, "--set", "kagent.controller.sessionIdleTTL=30d"],
              ("kagent.controller.sessionIdleTTL (30d) is not a Go duration",), "a TTL of 30d")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: verify-session-retention.py <chart dir>")
    main(sys.argv[1])
