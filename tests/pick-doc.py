#!/usr/bin/env python3
"""Print the documents of a rendered manifest that are of one kind and name.

    python3 tests/pick-doc.py <render.yaml> <Kind> <name> [<namespace>]

The Makefile's render assertions pick one object out of a `helm template`
output before grepping its fields; an awk range over `kind:` … `---` picks the
first object of a kind, which is the wrong one as soon as a render carries two
Jobs or two Roles. Standard library only (CI has no PyYAML): a document is
selected by the exact `kind: <Kind>` and `  name: <name>` lines (and
`  namespace: <namespace>` when given), the way tests/verify-kagent-wiring.py
selects a HelmRelease; a name ending in `*` matches by prefix (a Job whose name
carries a hash of its spec). Exits 1 when nothing matches, so a `|| FAIL` reads
right.
"""

import sys


def main(argv: list[str]) -> int:
    if len(argv) not in (4, 5):
        sys.exit(__doc__)
    path, kind, name = argv[1:4]
    namespace = argv[4] if len(argv) == 5 else None
    wanted = {f"kind: {kind}"}
    if namespace is not None:
        wanted.add(f"  namespace: {namespace}")
    found = False
    for doc in open(path, encoding="utf-8").read().split("\n---\n"):
        lines = set(doc.split("\n"))
        if name.endswith("*"):
            named = any(l.startswith(f"  name: {name[:-1]}") for l in lines)
        else:
            named = f"  name: {name}" in lines
        if named and wanted <= lines:
            found = True
            print(doc.strip("\n"))
            print("---")
    return 0 if found else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
