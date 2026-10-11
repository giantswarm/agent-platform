#!/usr/bin/env python3
"""Assert the workspace grant signing key (giantswarm/agent-platform#935).

The workspace-manager signs the grant a Session carries (`authorize_workspace`) with
the private key of a Secret in the platform's namespace, workspace-manager.grant.signingKey
{name, key}, and publishes every entry of that Secret at <baseURL>/.well-known/jwks.json.
Its chart never renders the key, and the meta chart forwarded an empty reference, so
the manager served no grant and no key set. Each case below pins one property:

- with providers and oauth.enabled the connectivity chart creates its own Secret,
  workspaceManager.grantKey.secretName with the entry .key, when the installation
  references nothing or exactly that Secret and entry: ONE entry, an EC P-256
  private key in PEM (SEC1, prime256v1, a 32-byte scalar: what the manager parses),
  generated per render (two offline renders differ), kept on uninstall, the entry
  following the key id and the Secret the name;
- an installation's own reference to another Secret is its own key: the chart
  creates nothing for it (giantswarm/agent-platform#944), and none renders without
  providers, with oauth off, with grantKey.create false or with the component off;
- the meta chart forwards the installation's own reference unchanged and, with none,
  the created Secret's; nothing without providers, with oauth off or with create
  false; the connectivity release receives the grantKey block it creates the Secret
  from; NOTES prints the key and the key set's URL;
- the guards, in both charts: an own reference without providers or with oauth off
  (the manager refuses to start with it), a half reference, a bad Secret name or
  data key, on the own and the derived reference.

The keep across upgrades (every live entry read back through `lookup`) needs a
cluster: README "Workspaces". Deliberately stdlib-only: the CI image has no PyYAML.
HELM selects the binary.
"""

import base64
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

HELM = os.environ.get("HELM", "helm")
NAME = "workspace-manager"
FIXTURE = "tests/fixtures/workspaces-values.yaml"
BASE = "https://workspaces.ci.example.com"
IDENTITY = [
    "--set", "global.domain=ci.example.com",
    "--set", "global.identity.issuerUrl=https://dex.ci.example.com",
    "--set", "global.identity.clientId=platform",
    "--set", "global.identity.existingSecret=platform-oauth",
]
CONN = ["--set", "ingress.parentRefs[0].name=edge", "--set", "ingress.parentRefs[0].namespace=envoy-gateway-system",
        "--set", f"components.{NAME}.enabled=true", "-f", FIXTURE, *IDENTITY]
META = ["--set", "components.flux.enabled=false", "-f", FIXTURE]
# The fixture's own reference, and the chart's default once it is dropped.
OWN, OWN_KEY = "workspace-manager-keys", "grant"
DEF, DEF_KEY = "workspace-manager-grant-keys", "key-1"
NO_OWN = ["--set", f"{NAME}.grant.signingKey=null"]
# prime256v1 (1.2.840.10045.3.1.7) as the SEC1 ECPrivateKey's parameters carry it.
P256_OID = bytes.fromhex("06082a8648ce3d030107")
# The PEM armour of an EC private key, spelled in parts so no secret scanner reads a
# key into a test that holds none.
PEM_BEGIN, PEM_END = "-----BEGIN " + "EC PRIVATE KEY-----", "-----END " + "EC PRIVATE KEY-----"


def fail(msg: str) -> None:
    sys.exit(f"FAIL: {msg}")


def ok(msg: str) -> None:
    print(f"ok: {msg}")


def run(cmd: list, expect_fail: bool = False) -> str:
    r = subprocess.run(cmd, capture_output=True, text=True)
    if expect_fail:
        if r.returncode == 0:
            fail(f"the render passed but had to fail: {' '.join(cmd[3:])}")
        return r.stderr
    if r.returncode != 0:
        fail(f"the render failed: {' '.join(cmd[3:])}\n{r.stderr}")
    return r.stdout


def helm(chart: str, flags: list, expect_fail: bool = False) -> str:
    return run([HELM, "template", "t", chart, *flags], expect_fail)


def notes(chart: str, flags: list) -> str:
    """The rendered NOTES.txt: `helm template` skips NOTES, and a dry-run install needs
    a cluster on older Helm, so a copy of the chart renders it inside a ConfigMap."""
    with tempfile.TemporaryDirectory() as tmp:
        copy = os.path.join(tmp, os.path.basename(os.path.normpath(chart)))
        shutil.copytree(chart, copy)
        templates = os.path.join(copy, "templates")
        with open(os.path.join(templates, "NOTES.txt"), encoding="utf-8") as f:
            text = f.read()
        os.remove(os.path.join(templates, "NOTES.txt"))
        with open(os.path.join(templates, "_zz_notes.tpl"), "w", encoding="utf-8") as f:
            f.write('{{- define "zz.notes" -}}\n' + text + "{{- end -}}\n")
        with open(os.path.join(templates, "zz-notes.yaml"), "w", encoding="utf-8") as f:
            f.write('apiVersion: v1\nkind: ConfigMap\nmetadata:\n  name: zz-notes\ndata:\n  notes: {{ include "zz.notes" . | toJson }}\n')
        out = helm(copy, [*flags, "--show-only", "templates/zz-notes.yaml"])
    return json.loads(re.search(r"^  notes: (.*)$", out, re.M).group(1)) + "\n"


def must_fail(chart: str, flags: list, needles: tuple, what: str) -> None:
    err = helm(chart, flags, expect_fail=True)
    if not any(n in err for n in needles):
        fail(f"{what} failed for another reason (expected one of {needles!r}):\n{err}")


def documents(render: str) -> dict:
    """(kind, name) -> the document, for every document that has both."""
    out = {}
    for doc in render.split("\n---\n"):
        kind = re.search(r"^kind: (\S+)", doc, re.M)
        name = re.search(r"^  name: (\S+)", doc, re.M)
        if kind and name:
            out[(kind.group(1), name.group(1))] = doc.rstrip("\n") + "\n"
    return out


def secret_data(doc: str) -> list:
    """The Secret's data entries as (key, base64) pairs, in order."""
    return re.findall(r"^  (\S+): (\S+)$", doc.split("\ndata:\n", 1)[1], re.M)


def ec_p256_key(b64: str) -> bool:
    """The entry is a PEM `EC PRIVATE KEY` whose DER is a SEC1 ECPrivateKey: version 1,
    a 32-byte scalar and the prime256v1 parameters — what `genPrivateKey "ecdsa"`
    writes and the manager parses (x509.ParseECPrivateKey)."""
    pem = base64.b64decode(b64).decode()
    m = re.fullmatch(re.escape(PEM_BEGIN) + r"\n([A-Za-z0-9+/=\n]+)" + re.escape(PEM_END) + r"\n?", pem)
    if not m:
        return False
    der = base64.b64decode(m.group(1))
    return der[:1] == b"\x30" and der[2:7] == b"\x02\x01\x01\x04\x20" and P256_OID in der


def the_secret(docs: dict, name: str, key: str, what: str) -> list:
    """ONE entry named `key` holding an EC P-256 key, kept on uninstall, the component's labels."""
    secret = docs.get(("Secret", name))
    if not secret:
        fail(f"{what}: no Secret {name}")
    data = secret_data(secret)
    if [k for k, _ in data] != [key]:
        fail(f"{what}: Secret {name} does not hold exactly the entry {key}: {[k for k, _ in data]}")
    if not ec_p256_key(data[0][1]):
        fail(f"{what}: Secret {name}'s entry {key} is not an EC P-256 private key in PEM (SEC1, prime256v1)")
    if "    helm.sh/resource-policy: keep\n" not in secret:
        fail(f"{what}: Secret {name} is not kept on uninstall")
    if "app.kubernetes.io/component: workspace-manager\n" not in secret:
        fail(f"{what}: Secret {name} lacks the component's labels:\n{secret}")
    return data


def main(meta: str, connectivity: str) -> int:
    # --- the installation's own reference: its own key, nothing created ---------------------------
    docs = documents(helm(connectivity, CONN))
    created = [n for k, n in docs if k == "Secret" and n in (OWN, DEF)]
    if created:
        fail(f"the own reference {OWN}/{OWN_KEY} is the installation's key, yet the chart renders {created} (#944)")
    for name, key in ((OWN, DEF_KEY), (DEF, OWN_KEY)):
        same = [n for k, n in documents(helm(connectivity, [*CONN, "--set", f"{NAME}.grant.signingKey.name={name}", "--set", f"{NAME}.grant.signingKey.key={key}"]))
                if k == "Secret" and n in (OWN, DEF)]
        if same:
            fail(f"an own reference {name}/{key} differs from the chart's own {DEF}/{DEF_KEY} in one part, yet the chart renders {same}")
    exact = [*CONN, "--set", f"{NAME}.grant.signingKey.name={DEF}", "--set", f"{NAME}.grant.signingKey.key={DEF_KEY}"]
    data = the_secret(documents(helm(connectivity, exact)), DEF, DEF_KEY, "an own reference naming the chart's own Secret and entry")
    again = the_secret(documents(helm(connectivity, exact)), DEF, DEF_KEY, "the same, rendered again")
    if again[0][1] == data[0][1]:
        fail("two renders share one key: the entry is a constant, not generated")
    ok(f"the own reference {OWN}/{OWN_KEY}: the installation's key, no Secret rendered (nor for a reference that differs from the chart's own in name or entry); "
       f"an own reference naming exactly {DEF}/{DEF_KEY} renders the chart's Secret, an EC P-256 private key in PEM generated per render, kept on uninstall")

    # --- the Secret of the chart's default -------------------------------------------------------
    default = documents(helm(connectivity, [*CONN, *NO_OWN]))
    the_secret(default, DEF, DEF_KEY, "the default")
    if ("Secret", OWN) in default:
        fail(f"the default still renders the fixture's Secret {OWN}")
    the_secret(documents(helm(connectivity, [*CONN, *NO_OWN, "--set", "workspaceManager.grantKey.key=key-2"])), DEF, "key-2", "a rotation")
    renamed = documents(helm(connectivity, [*CONN, *NO_OWN, "--set", "workspaceManager.grantKey.secretName=grant-keys"]))
    the_secret(renamed, "grant-keys", DEF_KEY, "another Secret name")
    if ("Secret", DEF) in renamed:
        fail(f"grantKey.secretName moved the name but {DEF} still renders")
    ok(f"the default: Secret {DEF} with the entry {DEF_KEY}; grantKey.key names the entry (a rotation), grantKey.secretName the Secret")

    # --- none ------------------------------------------------------------------------------------
    for flags, what in ((["--set", f"{NAME}.providers=null"], "without providers"),
                        (["--set", f"{NAME}.oauth.enabled=false"], "with oauth off"),
                        (["--set", "workspaceManager.grantKey.create=false"], "with grantKey.create false")):
        if any(k == "Secret" and n in (OWN, DEF) for k, n in documents(helm(connectivity, [*CONN, *NO_OWN, *flags]))):
            fail(f"the grant signing key Secret renders {what}")
    if ("Secret", OWN) in documents(helm(connectivity, [*CONN, "--set", "workspaceManager.grantKey.create=false"])):
        fail("grantKey.create false still creates the Secret of the own reference (it is the installation's)")
    off = documents(helm(connectivity, ["--set", "ingress.parentRefs[0].name=edge", "-f", FIXTURE, *IDENTITY,
                                        "--set", f"components.{NAME}.enabled=false"]))
    if any(k == "Secret" and n in (OWN, DEF) for k, n in off):
        fail("the grant signing key Secret renders with the component off")
    ok("none without providers, with oauth off, with grantKey.create false (the own reference included) or with the component off")

    # --- the meta chart: the forward and NOTES ---------------------------------------------------
    hr = documents(helm(meta, META))[("HelmRelease", NAME)]
    own = f"    grant:\n      signingKey:\n        key: {OWN_KEY}\n        name: {OWN}\n"
    if own not in hr:
        fail(f"the {NAME} HelmRelease does not forward the own reference unchanged:\n{hr}")
    derived = documents(helm(meta, [*META, *NO_OWN]))
    hr = derived[("HelmRelease", NAME)]
    if f"    grant:\n      signingKey:\n        key: {DEF_KEY}\n        name: {DEF}\n" not in hr:
        fail(f"with no own reference the {NAME} HelmRelease does not name the created Secret {DEF}/{DEF_KEY}:\n{hr}")
    conn = derived[("HelmRelease", "agent-platform-connectivity")]
    block = re.search(r"^      grantKey:\n((?:        .*\n)+)", conn, re.M)
    if not block or sorted(l.strip() for l in block.group(1).splitlines()) != ["create: true", f"key: {DEF_KEY}", f"secretName: {DEF}"]:
        fail(f"the connectivity HelmRelease does not carry workspaceManager.grantKey as the meta chart declares it:\n{conn}")
    rotated = documents(helm(meta, [*META, *NO_OWN, "--set", "workspaceManager.grantKey.key=key-2"]))[("HelmRelease", NAME)]
    if "        key: key-2\n" not in rotated:
        fail("grantKey.key does not reach the component release's reference")
    for flags, what in ((["--set", f"{NAME}.providers=null"], "without providers"),
                        (["--set", f"{NAME}.oauth.enabled=false"], "with oauth off"),
                        (["--set", "workspaceManager.grantKey.create=false"], "with grantKey.create false")):
        if "signingKey" in documents(helm(meta, [*META, *NO_OWN, *flags]))[("HelmRelease", NAME)]:
            fail(f"the {NAME} HelmRelease names a grant signing key {what}: the manager refuses to start with it")
    text = notes(meta, META)
    if f"  Workspace grants: signed with the key {OWN}/{OWN_KEY} (a Secret in the platform's namespace), published at {BASE}/.well-known/jwks.json\n" not in text:
        fail(f"NOTES does not print the own reference and the key set's URL:\n{text}")
    if f"signed with the key {DEF}/{DEF_KEY}" not in notes(meta, [*META, *NO_OWN]):
        fail("NOTES does not print the created Secret's reference")
    if "Workspace grants" in notes(meta, [*META, *NO_OWN, "--set", f"{NAME}.providers=null"]):
        fail("NOTES prints a grant key without providers")
    ok("meta: the own reference forwarded unchanged; with none, the created Secret's — nothing without providers, with oauth off or with create false; "
       "the connectivity release gets the grantKey block; NOTES prints the key and the key set's URL")

    # --- the guards ------------------------------------------------------------------------------
    for chart, flags in ((meta, META), (connectivity, CONN)):
        for extra, what in ((["--set", f"{NAME}.providers=null"], "without providers"), (["--set", f"{NAME}.oauth.enabled=false"], "with oauth off")):
            must_fail(chart, [*flags, *extra], ("refuses a grant signing key without provider instances and oauth.enabled",), f"{chart}: an own reference {what}")
            helm(chart, [*flags, *NO_OWN, *extra])
        must_fail(chart, [*flags, "--set", f"{NAME}.grant.signingKey.key=null"],
                  ("is a credential: it takes a Secret reference {name, key}", "is not a Secret reference: it takes {name, key}, both set"), f"{chart}: a half reference")
        must_fail(chart, [*flags, "--set", f"{NAME}.grant.signingKey.name=Keys"], ("is not a Secret name",), f"{chart}: a bad Secret name")
        must_fail(chart, [*flags, "--set", f"{NAME}.grant.signingKey.key=key 1"], ("is not a Secret data key",), f"{chart}: a bad data key")
        must_fail(chart, [*flags, *NO_OWN, "--set", "workspaceManager.grantKey.secretName=Keys"], ("is not a Secret name",), f"{chart}: a bad default Secret name")
        must_fail(chart, [*flags, *NO_OWN, "--set", "workspaceManager.grantKey.key=key/1"], ("is not a Secret data key",), f"{chart}: a bad default data key")
    ok("guards, both charts: an own reference without providers or with oauth off, a half reference, a bad Secret name or data key on the own and the derived reference; "
       "no reference without providers or with oauth off passes")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(f"usage: {sys.argv[0]} <meta chart dir> <connectivity chart dir>")
    sys.exit(main(sys.argv[1], sys.argv[2]))
