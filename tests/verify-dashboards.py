#!/usr/bin/env python3
"""Assert every Grafana board under the connectivity chart's dashboards/ directory.

observability-operator imports a board straight from its ConfigMap, so a file
that Grafana cannot import fails only on the cluster, silently. The checks:

  * a schema Grafana imports: the classic v1 (a numeric `schemaVersion`) or
    the v2 envelope (`apiVersion: dashboard.grafana.app/v2*` with a `.spec`).
    The "JSON model" Grafana shows under Settings is the v2 spec with the
    envelope stripped (`.elements` at the top, no `apiVersion`) and does not
    import;
  * a uid (`.uid`, or `.metadata.uid` on v2), so a link to the board survives a
    re-import, and no uid twice across the directory;
  * an `owner:` tag, what giantswarm/dashboards requires of every board.

Usage: verify-dashboards.py <dashboards directory>
"""
import glob
import json
import os
import sys


def check(path, uids):
    try:
        with open(path, encoding="utf-8") as handle:
            board = json.load(handle)
    except (OSError, ValueError) as exc:
        return f"invalid JSON: {exc}"
    v2 = str(board.get("apiVersion", "")).startswith("dashboard.grafana.app/v2")
    if v2 and not isinstance(board.get("spec"), dict):
        return "v2 apiVersion without a .spec envelope"
    if not v2 and isinstance(board.get("elements"), dict):
        return "unwrapped v2 JSON model (export the full dashboard, not Settings -> JSON model)"
    if not v2 and not isinstance(board.get("schemaVersion"), int):
        return "neither a v1 schemaVersion nor a v2 apiVersion"
    uid = (board.get("metadata", {}).get("uid") if v2 else board.get("uid")) or ""
    if not uid:
        return "no uid; Grafana would mint one per import and every link to the board would break"
    if uid in uids:
        return f"uid {uid} is already {uids[uid]}"
    uids[uid] = path
    tags = (board.get("spec", {}) if v2 else board).get("tags") or []
    if not any(str(tag).startswith("owner:") for tag in tags):
        return "no owner: tag"
    print(f"ok: {path} ({'v2' if v2 else 'v1'}, uid {uid})")
    return None


def main():
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    files = sorted(glob.glob(os.path.join(sys.argv[1], "**", "*.json"), recursive=True))
    if not files:
        print(f"FAIL: no board under {sys.argv[1]}")
        return 1
    uids = {}
    failures = 0
    for path in files:
        problem = check(path, uids)
        if problem:
            print(f"FAIL: {path}: {problem}")
            failures += 1
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
