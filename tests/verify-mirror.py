#!/usr/bin/env python3
"""Assert the air-gapped mirror (gitops.mirror, giantswarm/agent-platform#1259).

Each case pins one promise:
  * unset (the default), every source renders as written: gsoci.azurecr.io,
    no secretRef, no certSecretRef on any OCIRepository;
  * gitops.mirror.registry set, no gsoci.azurecr.io reference is left in the
    meta chart's render (the component and self-management OCIRepositories,
    every forwarded component value, the hook images) but the bundled engine's
    two images, which its own values move (flux-engine.operator.image.registry,
    flux-engine.instance.distribution.registry): with those set too, none at
    all; every OCIRepository carries the mirror's secretRef and certSecretRef;
  * the path, tag and digest of a reference stay; a per-source key set to
    another registry is left as written; numbers keep their form;
  * a mirror with a scheme or a path fails the render naming the key;
  * the connectivity release receives modelServing.runtimeImages.registry and
    modelImages.registry on the mirror (an own modelImages.registry stands),
    and the connectivity chart publishes every preset's runtime image and OCI
    model image on that host, the path kept; a malformed runtime registry
    fails naming the key.

Deliberately stdlib-only: the CI image has no PyYAML.
"""

import os
import re
import subprocess
import sys

HELM = os.environ.get("HELM", "helm")
SOURCE = "gsoci.azurecr.io"
MIRROR = "mirror.fog.invalid:5000"
AUTH = ["--set", "gitops.mirror.secretRef=mirror-pull", "--set", "gitops.mirror.certSecretRef=mirror-ca"]
ENGINE = [
    "--set", f"flux-engine.operator.image.registry={MIRROR}",
    "--set", f"flux-engine.instance.distribution.registry={MIRROR}/giantswarm/fluxcd",
]


def render(chart, *args, ok=True):
    proc = subprocess.run([HELM, "template", "ap", chart, *args], capture_output=True, text=True)
    if ok and proc.returncode != 0:
        sys.exit(f"FAIL: helm template {chart} {' '.join(args)}:\n{proc.stderr}")
    return proc


def documents(text, kind):
    return [d for d in text.split("\n---") if re.search(rf"^kind: {kind}$", d, re.M)]


def check(cond, msg):
    if not cond:
        sys.exit(f"FAIL: {msg}")
    print(f"ok: {msg}")


def main(chart, connectivity):
    default = render(chart).stdout
    sources = documents(default, "OCIRepository")
    check(sources and all(f"oci://{SOURCE}/" in d for d in sources), "unset, every OCIRepository pulls from gsoci")
    check(not any("secretRef:" in d or "certSecretRef:" in d for d in sources), "unset, no OCIRepository carries a secretRef or certSecretRef")

    mirrored = render(chart, "--set", f"gitops.mirror.registry={MIRROR}", *AUTH).stdout
    left = sorted({line.strip() for line in mirrored.splitlines() if SOURCE in line})
    check(len(left) == 2 and all("flux-operator" in l or "fluxcd" in l for l in left),
          f"on the mirror only the bundled engine's two images stay on gsoci ({left})")
    full = render(chart, "--set", f"gitops.mirror.registry={MIRROR}", *AUTH, *ENGINE).stdout
    check(SOURCE not in full, "with the engine's own keys set, no gsoci.azurecr.io reference is left")

    sources = documents(full, "OCIRepository")
    check(len(sources) == len(documents(default, "OCIRepository")), f"the same {len(sources)} OCIRepositories render")
    check(all(re.search(rf"url: oci://{re.escape(MIRROR)}/", d) for d in sources), "every OCIRepository url is on the mirror")
    check(all("secretRef:\n    name: mirror-pull" in d and "certSecretRef:\n    name: mirror-ca" in d for d in sources),
          "every OCIRepository carries the mirror's secretRef and certSecretRef")
    self_source = [d for d in sources if "app.kubernetes.io/component: self-management" in d]
    check(len(self_source) == 1 and f"url: oci://{MIRROR}/charts/giantswarm/agent-platform" in self_source[0],
          "the self-management OCIRepository pulls the chart from the mirror")
    hooks = re.findall(r'^\s+image: "?([^"\s]+)"?$', "\n".join(documents(full, "Job")), re.M)
    check(hooks and all(i.startswith(f"{MIRROR}/giantswarm/") for i in hooks), f"every hook image is on the mirror ({len(hooks)})")
    check(f"{MIRROR}/giantswarm/kagent/golang-adk" in full or f"registry: {MIRROR}" in full,
          "forwarded component values carry the mirror host with the path kept")

    other = render(chart, "--set", f"gitops.mirror.registry={MIRROR}", "--set", "kagent.registry=registry.other.example").stdout
    check("registry: registry.other.example" in other, "a per-source key set to another registry is left as written")
    check(re.search(r"tokenThreshold: 600000$", full, re.M) is not None, "numbers keep their form (no JSON round trip)")

    for bad in ("https://mirror.example", "mirror.example/path"):
        proc = render(chart, "--set", f"gitops.mirror.registry={bad}", ok=False)
        check(proc.returncode != 0 and "gitops.mirror.registry" in proc.stderr, f"a mirror {bad!r} fails naming gitops.mirror.registry")

    # The serving slice: modelServing travels to connectivity only while it is on.
    serving = ["-f", os.path.join(chart, "examples", "serving-slice.yaml"), "--set", f"gitops.mirror.registry={MIRROR}"]
    conn = [d for d in documents(render(chart, *serving).stdout, "HelmRelease") if "releaseName: agent-platform-connectivity" in d]
    check(len(conn) == 1, "the serving slice renders one connectivity release")
    check(re.search(rf"runtimeImages:\n\s+registry: {re.escape(MIRROR)}", conn[0]) is not None,
          "the connectivity release receives modelServing.runtimeImages.registry on the mirror")
    check(re.search(rf"modelImages:\n\s+registry: {re.escape(MIRROR)}", conn[0]) is not None,
          "the connectivity release receives modelServing.modelImages.registry on the mirror")
    own = render(chart, *serving, "--set", "modelServing.modelImages.registry=models.own.example").stdout
    own_conn = [d for d in documents(own, "HelmRelease") if "releaseName: agent-platform-connectivity" in d][0]
    check("registry: models.own.example" in own_conn, "an own modelServing.modelImages.registry stands")

    ci = os.path.join(connectivity, "ci", "ci-values.yaml")
    plain = render(connectivity, "-f", ci).stdout
    check(f"{SOURCE}/giantswarm/vllm-" in plain, "the connectivity chart alone names the vLLM runtimes on gsoci")
    moved = render(connectivity, "-f", ci, "--set", f"modelServing.runtimeImages.registry={MIRROR}",
                   "--set", f"modelServing.modelImages.registry={MIRROR}").stdout
    check(f"{SOURCE}/giantswarm/vllm-" not in moved and f"{MIRROR}/giantswarm/vllm-" in moved,
          "modelServing.runtimeImages.registry moves every preset's runtime image, the path kept")
    check(f"{SOURCE}/giantswarm/models/" not in moved, "modelServing.modelImages.registry moves every OCI model image")
    proc = render(connectivity, "-f", ci, "--set", "modelServing.runtimeImages.registry=https://x", ok=False)
    check(proc.returncode != 0 and "modelServing.runtimeImages.registry" in proc.stderr,
          "a malformed modelServing.runtimeImages.registry fails naming the key")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
