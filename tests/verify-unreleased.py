#!/usr/bin/env python3
"""Assert that a development build waiting for a component release refuses its
install at once, naming the component and the version, and that every other
build installs as before (giantswarm/agent-platform#823).

The branch run of tests/verify-components-charts.py records every range that
admits no published chart in Chart.yaml's annotation
agent-platform.giantswarm.io/unreleased; the meta chart's pre-install hook
(templates/hooks/unreleased-components.yaml) refuses such a build. Offline, on a
copy of the chart with that annotation, the version shapes and the values below:

- the annotation: written as the last line of Chart.yaml's annotations, read
  back as written, and dropped again leaving the file byte-identical;
- a dev build whose agent-manager range waits for 1.10.0, with the bundled
  engine: exactly one hook Job <release>-waits-for-agent-manager-1.10.0,
  pre-install,pre-upgrade at weight -11 (ahead of every other hook), no retry,
  as the default ServiceAccount, whose script, run here, exits non-zero naming
  the component, the version and the range;
- no refusal where nothing waits or the installation chose otherwise: no
  annotation (every range published), a release (X.Y.Z, X.Y.Z-rc.N), the engine
  off, the component off, its versionRange overridden, a semverFilter set — each
  renders no such Job.

HELM selects the binary.
"""

import importlib.util
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile

import yaml

HELM = os.environ.get("HELM", "helm")
HERE = pathlib.Path(__file__).resolve().parent
RELEASE = "agent-platform"
NAMESPACE = "agent-platform"
COMPONENT, RANGE, WAITS_FOR = "agent-manager", ">=1.10.0 <2.0.0", "1.10.0"
DEV = "4.116.1-r7fb489f8t20261006135307h75833ed"
JOB = f"{RELEASE}-waits-for-{COMPONENT}-{WAITS_FOR}"

_spec = importlib.util.spec_from_file_location("components_charts", HERE / "verify-components-charts.py")
cc = importlib.util.module_from_spec(_spec)
sys.path.insert(0, str(HERE))
_spec.loader.exec_module(cc)


def fail(msg: str) -> None:
    sys.exit(f"FAIL: {msg}")


def set_version(chart: str, version: str) -> None:
    path = f"{chart}/Chart.yaml"
    text = open(path, encoding="utf-8").read()
    lines = [f"version: {version}" if line.startswith("version: ") else line for line in text.split("\n")]
    open(path, "w", encoding="utf-8").write("\n".join(lines))


def hooks(chart: str, flags: list[str]) -> list[dict]:
    """The refusal Jobs a render carries."""
    cmd = [HELM, "template", RELEASE, chart, "-n", NAMESPACE, "-f", f"{chart}/ci/ci-values.yaml",
           "--set", f"components.{COMPONENT}.versionRange={RANGE}", *flags]
    r = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if r.returncode != 0:
        fail(f"render failed: {' '.join(cmd)}\n{r.stderr}")
    return [d for d in yaml.safe_load_all(r.stdout)
            if d and d.get("kind") == "Job" and "-waits-for-" in d["metadata"]["name"]]


def main(src: str) -> int:
    waits = {COMPONENT: {"versionRange": RANGE, "waitsFor": WAITS_FOR}}
    with tempfile.TemporaryDirectory() as tmp:
        chart = f"{tmp}/agent-platform"
        shutil.copytree(src, chart)
        original = open(f"{chart}/Chart.yaml", encoding="utf-8").read()
        if cc.recorded_waits(chart):
            cc.write_waits(chart, {})
            original = open(f"{chart}/Chart.yaml", encoding="utf-8").read()

        # --- the annotation round trip
        cc.write_waits(chart, waits)
        if cc.recorded_waits(chart) != waits:
            fail(f"Chart.yaml's {cc.UNRELEASED_ANNOTATION} reads back as {cc.recorded_waits(chart)}, written {waits}")
        written = open(f"{chart}/Chart.yaml", encoding="utf-8").read()
        cc.write_waits(chart, {})
        if open(f"{chart}/Chart.yaml", encoding="utf-8").read() != original:
            fail(f"dropping {cc.UNRELEASED_ANNOTATION} does not give back Chart.yaml as it was")
        print(f"ok: {cc.UNRELEASED_ANNOTATION} is written, read back and dropped without touching the rest of Chart.yaml")

        # --- a fully published dev build: no refusal
        set_version(chart, DEV)
        if found := hooks(chart, []):
            fail(f"a dev build that waits for nothing renders {[d['metadata']['name'] for d in found]}")
        print("ok: a dev build whose every range is published renders no refusal")

        # --- a dev build that waits: refused at once, naming the component and the version
        open(f"{chart}/Chart.yaml", "w", encoding="utf-8").write(written)
        set_version(chart, DEV)
        found = hooks(chart, [])
        if [d["metadata"]["name"] for d in found] != [JOB]:
            fail(f"a dev build waiting for {COMPONENT} {WAITS_FOR} renders {[d['metadata']['name'] for d in found]}, expected [{JOB}]")
        job = found[0]
        ann, spec = job["metadata"]["annotations"], job["spec"]
        pod = spec["template"]["spec"]
        got = (ann["helm.sh/hook"], ann["helm.sh/hook-weight"], spec["backoffLimit"], pod["serviceAccountName"])
        if got != ("pre-install,pre-upgrade", "-11", 0, "default"):
            fail(f"{JOB} is (hook, weight, backoffLimit, serviceAccountName) {got}, expected ('pre-install,pre-upgrade', '-11', 0, 'default')")
        script = pod["containers"][0]["args"][0]
        r = subprocess.run(["sh", "-eu", "-c", script], capture_output=True, text=True, check=False)
        for want in (f"{COMPONENT} {WAITS_FOR}", f'"{RANGE}"', DEV):
            if r.returncode == 0 or want not in r.stderr:
                fail(f"{JOB}'s script exits {r.returncode} without naming {want!r}:\n{r.stderr}")
        print(f"ok: a dev build waiting for {COMPONENT} {WAITS_FOR} is refused by {JOB} (pre-install,pre-upgrade, -11, no retry), naming the component, the version and the range")

        # --- no refusal where the installation is not that dev build's install
        for what, version, flags in [
            ("a release", "4.116.0", []),
            ("a release candidate", "4.116.0-rc.1", []),
            ("the engine off", DEV, ["--set", "components.flux.enabled=false"]),
            (f"{COMPONENT} off", DEV, ["--set", f"components.{COMPONENT}.enabled=false"]),
            (f"{COMPONENT} on another range", DEV, ["--set", f"components.{COMPONENT}.versionRange=>=1.7.0 <2.0.0"]),
            (f"{COMPONENT} on a dev channel", DEV, ["--set", f"components.{COMPONENT}.semverFilter=.*-dev.*"]),
        ]:
            set_version(chart, version)
            if found := hooks(chart, flags):
                fail(f"{what} renders {[d['metadata']['name'] for d in found]}; only a dev build installing the waiting range is refused")
            print(f"ok: {what}: no refusal")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(f"usage: {sys.argv[0]} <meta chart dir>")
    sys.exit(main(sys.argv[1]))
