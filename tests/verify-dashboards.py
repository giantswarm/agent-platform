#!/usr/bin/env python3
"""Assert every dashboard's PromQL parses at the JSON level.

Grafana sends each target's expr to Mimir as written. A backslash before a
quote inside the expr (the JSON string holds a literal \\") reaches PromQL as
an escaped quote inside a label matcher, which Mimir refuses with
"unexpected character inside braces". The panel then shows an error, not
data, on every installation.

usage: verify-dashboards.py <connectivity chart dir>
"""
import json
import pathlib
import sys

CONN = pathlib.Path(sys.argv[1])
BAD = []
COUNT = 0
for path in sorted((CONN / "dashboards").rglob("*.json")):
    board = json.loads(path.read_text())
    panels = list(board.get("panels", []))
    panels += [nested for panel in panels for nested in panel.get("panels", [])]
    for panel in panels:
        for target in panel.get("targets", []):
            expr = target.get("expr")
            if not expr:
                continue
            COUNT += 1
            if '\\"' in expr or "\\'" in expr:
                BAD.append(f"{path.relative_to(CONN)}: panel {panel.get('title')!r}: {expr}")
if BAD:
    print("FAIL: escaped quotes inside PromQL exprs:")
    print("\n".join(BAD))
    sys.exit(1)
print(f"ok: {COUNT} exprs across the connectivity chart's dashboards carry no escaped quote")
