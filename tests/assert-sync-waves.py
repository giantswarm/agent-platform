#!/usr/bin/env python3
"""Assert every Argo sync wave orders a component behind its dependsOn targets.

Takes two renders of the meta-package made from the SAME values: the flux render,
which carries dependsOn, and the argo render, which carries the derived sync wave.
The flux engine orders on dependsOn directly, so the argo waves are the only place
the install order can silently regress: a component that shares a wave with its
dependency applies at the same time as it.

Usage: assert-sync-waves.py <flux-render.yaml> <argo-render.yaml>
Only the standard library, so it runs on the bare CI image.
"""

import re
import sys

DEP_ENTRY = re.compile(r"^    - name: (\S+)$")


def documents(path):
    with open(path, encoding="utf-8") as handle:
        return handle.read().split("\n---\n")


def flux_dependencies(path):
    """component name -> list of dependsOn names, from the HelmRelease docs."""
    dependencies = {}
    for document in documents(path):
        if "kind: HelmRelease" not in document:
            continue
        name = re.search(r"^  releaseName: (\S+)$", document, re.M)
        if not name:
            sys.exit("FAIL: a HelmRelease carries no releaseName")
        # Anchor on the dependsOn line and take the entries that follow it, so a
        # forwarded values key that also renders `- name:` cannot be mistaken for
        # a dependency.
        names = []
        lines = document.splitlines()
        for index, line in enumerate(lines):
            if line != "  dependsOn:":
                continue
            for entry in lines[index + 1:]:
                match = DEP_ENTRY.match(entry)
                if not match:
                    break
                names.append(match.group(1))
            break
        dependencies[name.group(1)] = names
    return dependencies


def argo_waves(path):
    """component name -> sync wave, from the Application docs."""
    waves = {}
    for document in documents(path):
        if "kind: Application" not in document:
            continue
        name = re.search(r"^  name: (\S+)$", document, re.M)
        wave = re.search(r'^    argocd\.argoproj\.io/sync-wave: "(-?\d+)"$', document, re.M)
        if not name or not wave:
            sys.exit("FAIL: an Application carries no name or no sync-wave annotation")
        waves[name.group(1)] = int(wave.group(1))
    return waves


def main():
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    dependencies = flux_dependencies(sys.argv[1])
    waves = argo_waves(sys.argv[2])
    if not dependencies or not waves:
        sys.exit("FAIL: one of the renders holds no component")
    if set(dependencies) != set(waves):
        sys.exit("FAIL: the two engines render different components: %s" %
                 sorted(set(dependencies) ^ set(waves)))

    failures = []
    checked = 0
    for component, deps in sorted(dependencies.items()):
        for dependency in deps:
            checked += 1
            if waves[component] <= waves[dependency]:
                failures.append(
                    "%s (wave %d) dependsOn %s (wave %d) but does not sync after it"
                    % (component, waves[component], dependency, waves[dependency]))
    if failures:
        sys.exit("FAIL: " + "; ".join(failures) +
                 ". Check the componentWave helper, or set components.<key>.syncWave")
    print("ok: %d dependsOn edge(s) sync in order across %d components"
          % (checked, len(waves)))


if __name__ == "__main__":
    main()
