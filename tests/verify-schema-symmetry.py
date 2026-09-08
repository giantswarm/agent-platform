#!/usr/bin/env python3
"""Assert the two values schemas agree on every key the meta chart forwards.

`components.agent-platform-connectivity.forwardAllValues` hands the meta chart's
whole values tree (minus `gitops`, the roster's own keys and the blocks named in
that entry's `omitKeys`) to the connectivity release. Both charts generate their
`values.schema.json` from their `values.yaml` with `additionalProperties: false`
on every object, so a key one chart declares and the other does not fails a
render — at different places, both wrong:

- a key the CONNECTIVITY chart reads but the META chart does not declare is
  refused by the meta chart's schema at render time ("additional properties
  'dataPlaneResources' not allowed"), although the forward would carry it
  unchanged (#303 — `gateway.parameters.dataPlaneResources`);
- a key the META chart declares but the CONNECTIVITY chart does not is forwarded
  and refused by the connectivity release's schema on every installation, for
  as long as the fleet's connectivity `OCIRepository` has not re-resolved to a
  chart that declares it.

The earlier check compared the two schemas' top-level keys only; a nested key
like `gateway.parameters.dataPlaneResources` slipped through. This one walks the
connectivity schema's `properties` (and array `items`) under every top-level
block the meta chart forwards and fails on any key the meta schema rejects at
that path, then walks the meta schema the other way. A schema node that takes
arbitrary keys (`additionalProperties` absent, `true`, or a schema; the
`# @schema additionalProperties: true` annotation) accepts everything beneath
it and ends that branch.

The roster is the one block the forward transforms: of `components.<name>` only
`enabled` travels, so the meta → connectivity walk checks that key alone there
(the connectivity chart gates a component's wiring on it). Feature switches
(`components.modelServing`) work the same way.

`--meta-schema` / `--connectivity-schema` point the walk at another file — the
Makefile's negative cases feed it a schema with one nested key removed and
expect the failure naming that path, so the check demonstrably has teeth.

Deliberately stdlib-only: the CI image has no PyYAML.
"""

import argparse
import json
import re
import sys

ROSTER_KEYS = {"enabled"}


def load(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def held_back(meta_values: str) -> set[str]:
    """Top-level blocks the connectivity entry's omitKeys holds back."""
    text = open(meta_values, encoding="utf-8").read()
    start = text.index("  agent-platform-connectivity:")
    omit = re.search(r"^    omitKeys:\n((?:      .*\n)+)", text[start:], re.M)
    return set(re.findall(r"^      - (\S+)$", omit.group(1), re.M)) if omit else set()


def accepts_any(node: dict) -> bool:
    """JSON Schema: additionalProperties absent / true / a schema admits keys not in properties."""
    return node.get("additionalProperties", True) is not False


def is_object(node: dict) -> bool:
    t = node.get("type")
    return t is None or t == "object" or (isinstance(t, list) and "object" in t)


def walk(src: dict, dst: dict, path: str, rejected: list[str], visited: list[str]) -> None:
    """Every key `src` declares under `properties` (recursively, through array
    `items`) must be accepted by `dst` at the same path."""
    if "items" in src and isinstance(src["items"], dict):
        items = dst.get("items")
        if isinstance(items, dict):
            walk(src["items"], items, path + "[]", rejected, visited)
        return
    props = src.get("properties") or {}
    if not props:
        return
    if not is_object(dst):
        rejected.append(f"{path} (declared as an object with keys {sorted(props)} on one side, typed {dst.get('type')!r} on the other)")
        return
    dst_props = dst.get("properties") or {}
    for key, sub in props.items():
        here = f"{path}.{key}" if path else key
        visited.append(here)
        if key in dst_props:
            walk(sub, dst_props[key], here, rejected, visited)
        elif not accepts_any(dst):
            rejected.append(here)


def roster_view(components: dict) -> dict:
    """The connectivity release sees components.<name>.enabled and nothing else of the roster."""
    out = {k: v for k, v in components.items() if k != "properties"}
    out["properties"] = {
        name: {**c, "properties": {k: v for k, v in (c.get("properties") or {}).items() if k in ROSTER_KEYS}}
        for name, c in (components.get("properties") or {}).items()
    }
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("meta", help="the meta chart directory (helm/agent-platform)")
    ap.add_argument("connectivity", help="the connectivity chart directory (helm/agent-platform-connectivity)")
    ap.add_argument("--meta-schema", help="use this values.schema.json for the meta chart (negative fixtures)")
    ap.add_argument("--connectivity-schema", help="use this values.schema.json for the connectivity chart (negative fixtures)")
    args = ap.parse_args()

    meta = load(args.meta_schema or f"{args.meta}/values.schema.json")
    conn = load(args.connectivity_schema or f"{args.connectivity}/values.schema.json")
    held = held_back(f"{args.meta}/values.yaml")
    meta_props, conn_props = meta["properties"], conn["properties"]

    # --- connectivity → meta: everything connectivity reads is settable through the meta chart ---
    missing_top = sorted(set(conn_props) - set(meta_props))
    if missing_top:
        sys.exit(
            "FAIL: connectivity top-level keys the meta chart's schema rejects (its root is additionalProperties: "
            "false, so forwardAllValues cannot reach them): " + ", ".join(missing_top)
            + ". Declare each block in helm/agent-platform/values.yaml and regenerate the schema (pre-commit run -a)."
        )
    rejected: list[str] = []
    visited: list[str] = []
    for top, sub in conn_props.items():
        walk(sub, meta_props[top], top, rejected, visited)
    if rejected:
        sys.exit(
            "FAIL: connectivity keys the meta chart's schema rejects — forwardAllValues would carry them to the "
            "connectivity release unchanged, but the meta chart's values.schema.json refuses them at render time "
            "(additionalProperties: false at that path):\n  " + "\n  ".join(rejected)
            + "\nDeclare each key in helm/agent-platform/values.yaml with the connectivity chart's default (so the "
            "render is unchanged) and regenerate the schema (pre-commit run -a)."
        )
    print(
        f"ok: every key the connectivity schema declares is accepted by the meta schema "
        f"({len(visited)} paths under {len(conn_props)} forwarded blocks, nested keys included)"
    )

    # --- meta → connectivity: everything the meta chart forwards is declared by connectivity ---
    forwarded = {k: v for k, v in meta_props.items() if k != "gitops" and k not in held}
    if "components" in forwarded:
        forwarded["components"] = roster_view(forwarded["components"])
    extra_top = sorted(set(forwarded) - set(conn_props))
    if extra_top:
        sys.exit(
            "FAIL: meta-chart top-level keys the connectivity schema does not declare: " + ", ".join(extra_top)
            + ". forwardAllValues hands them to the connectivity release, whose root schema is additionalProperties: "
            "false — declare them in the connectivity values.yaml or hold them back with "
            "components.agent-platform-connectivity.omitKeys."
        )
    rejected, visited = [], []
    for top, sub in forwarded.items():
        walk(sub, conn_props[top], top, rejected, visited)
    if rejected:
        sys.exit(
            "FAIL: meta-chart keys the connectivity schema does not declare — forwardAllValues hands them to the "
            "connectivity release, whose schema is additionalProperties: false at that path, so an installation "
            "that sets one fails that release:\n  " + "\n  ".join(rejected)
            + "\nDeclare each key in helm/agent-platform-connectivity/values.yaml (and regenerate the schema), or "
            "hold the block back with components.agent-platform-connectivity.omitKeys until the live chart declares it."
        )
    print(
        f"ok: every key the meta chart forwards is declared by the connectivity schema "
        f"({len(visited)} paths under {len(forwarded)} blocks; gitops and {sorted(held)} never travel, "
        f"of the roster only enabled does)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
