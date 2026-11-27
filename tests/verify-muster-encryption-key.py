#!/usr/bin/env python3
"""Assert shared encryption-key Secret selection preserves muster overrides."""

import os
from pathlib import Path
import subprocess
import sys

import yaml


def render(chart, settings):
    command = [os.environ.get("HELM", "helm"), "template", "t", str(chart),
               "-f", str(chart / "ci/ci-values.yaml"),
               "--set", "ingress.parentRefs[0].name=x",
               "--set", "global.identity.existingSecret=platform-identity"]
    for setting in settings:
        command.extend(["--set", setting])
    output = subprocess.check_output(command, text=True)
    release = next(document for document in yaml.safe_load_all(output)
                   if document and document.get("kind") == "HelmRelease"
                   and document["metadata"]["name"] == "muster")
    return release["spec"]["values"]["muster"]["oauth"]["server"]


def main(chart):
    cases = [
        ("shared Secret", [], "platform-identity"),
        ("own Secret", ["existingSecret=muster-identity"], "muster-identity"),
        ("inline encryption key", ["encryptionKeyValue=fixture-key"], None),
        ("encryption off", ["encryptionKey=false"], None),
        ("inline Dex login", ["enabled=true", "dex.clientSecret=fixture-secret"], None),
        ("inline Google login", ["enabled=true", "google.clientSecret=fixture-secret"], None),
        ("inline registration token", ["enabled=true", "registrationToken=fixture-token"], None),
        ("inline initial access token", ["enabled=true", "initialAccessToken=fixture-token"], None),
    ]
    for name, settings, expected in cases:
        server = render(chart, [f"muster.muster.oauth.server.{setting}" for setting in settings])
        if (server.get("existingSecret") or None) != expected:
            sys.exit(f"FAIL: {name}: existingSecret={server.get('existingSecret')!r}, want {expected!r}")
        if name == "shared Secret" and server.get("encryptionKey") is not True:
            sys.exit("FAIL: shared request-state encryption is disabled")
        print(f"ok: muster encryption-key selection: {name}")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
