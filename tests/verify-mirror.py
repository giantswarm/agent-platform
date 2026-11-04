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
    fails naming the key;
  * a preset's runtime image moves only from gsoci.azurecr.io: an image on
    another registry and a Docker Hub short name stay as written;
  * DNS names are no sources: no Cilium matchName or matchPattern carries the
    mirror's host:port; model-manager's registry egress takes the mirror's
    host, without the port, and its port (443 when it names none); an own
    list without gsoci stands.

Deliberately stdlib-only: the CI image has no PyYAML.
"""

import os
import re
import subprocess
import sys
import tempfile

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
    conn_full = [d for d in documents(full, "HelmRelease") if "releaseName: agent-platform-connectivity" in d]
    check(len(conn_full) == 1 and re.search(
        rf"image:\n\s+registry: {re.escape(MIRROR)}\n\s+repository: giantswarm/agent-manager\n", conn_full[0]) is not None,
          "a forwarded image reference (connectivity's agentManager.migration.image) is on the mirror, the path kept")

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

    # An installation's own preset: only gsoci images move.
    with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as f:
        f.write(OWN_PRESET)
    try:
        own_preset = render(connectivity, "-f", ci, "-f", f.name, "--set", f"modelServing.runtimeImages.registry={MIRROR}").stdout
    finally:
        os.unlink(f.name)
    check("vllm/vllm-openai:v0.10.0" in own_preset and f"{MIRROR}/vllm-openai" not in own_preset,
          "a Docker Hub short name in an own preset stays as written")
    check("quay.io/org/sidecar:1" in own_preset and f"{MIRROR}/org/sidecar" not in own_preset,
          "an own preset's image on another registry stays as written")
    check(f"{MIRROR}/giantswarm/own-runtime:1" in own_preset and f"{SOURCE}/giantswarm/own-runtime" not in own_preset,
          "an own preset's image on gsoci moves to the mirror, the path kept")

    # DNS names are no sources.
    check(re.search(r"match(Name|Pattern): \S*:\d+", mirrored) is None, "no Cilium matchName or matchPattern carries a port")
    def registry_egress(out):
        conn = [d for d in documents(out, "HelmRelease") if "releaseName: agent-platform-connectivity" in d]
        m = re.search(r"\n(\s+)registry:\n((?:\1\s+.*\n)+)", conn[0]) if conn else None
        return m.group(2) if m else ""
    reg = registry_egress(mirrored)
    check("matchName: mirror.fog.invalid\n" in reg and re.search(r"ports:\n\s+- 5000", reg) is not None and SOURCE not in reg,
          "model-manager's registry egress takes the mirror's host without the port, and its port")
    reg = registry_egress(render(chart, "--set", "gitops.mirror.registry=mirror.fog.invalid").stdout)
    check("matchName: mirror.fog.invalid\n" in reg and re.search(r"ports:\n\s+- 443", reg) is not None,
          "a mirror without a port keeps the registry egress on 443")
    reg = registry_egress(render(chart, "--set", f"gitops.mirror.registry={MIRROR}",
                                 "--set", "modelManager.networkPolicy.registry.fqdns[0].matchName=registry.own.example").stdout)
    check("matchName: registry.own.example" in reg and "mirror.fog.invalid" not in reg, "an own registry egress list without gsoci stands")


OWN_PRESET = """modelServing:
  presets:
    - apiVersion: agent-platform.giantswarm.io/v1alpha1
      kind: ServingPreset
      metadata:
        name: own-runtimes
      spec:
        displayName: An installation's own runtimes
        model:
          id: org/Own-Model
          storageUri: hf://org/Own-Model
        template:
          containers:
            - name: main
              image: vllm/vllm-openai:v0.10.0
          initContainers:
            - name: sidecar
              image: quay.io/org/sidecar:1
            - name: own
              image: gsoci.azurecr.io/giantswarm/own-runtime:1
        requirements:
          weightsGiB: 1
"""


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
