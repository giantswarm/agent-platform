#!/usr/bin/env python3
"""Assert the resource muster's OAuth server advertises and binds tokens to.

mcp-oauth names oauth.server.resourceIdentifier in muster's protected resource
metadata (RFC 9728) and binds tokens to it. Left empty it falls back to the
issuer, oauth.server.baseUrl, which is not the URL clients call (baseUrl +
/mcp), and RFC 9728 §3.3 has clients discard such metadata. The meta chart
therefore derives baseUrl + /mcp, without a doubled slash, and an
installation's own resourceIdentifier stands.

Reads a rendered meta-package manifest (stdout of `helm template`, PyYAML) and
the expected resourceIdentifier, or "" for none.
"""

import sys

import yaml


def main() -> None:
    path, want = sys.argv[1], sys.argv[2]
    with open(path, encoding="utf-8") as fh:
        docs = list(yaml.safe_load_all(fh))
    for doc in docs:
        if doc and doc.get("kind") == "HelmRelease" and doc["metadata"]["name"] == "muster":
            server = doc["spec"]["values"].get("muster", {}).get("oauth", {}).get("server", {})
            have = server.get("resourceIdentifier") or ""
            if have != want:
                sys.exit(f"FAIL: muster's resourceIdentifier is {have!r} for baseUrl {server.get('baseUrl')!r}, expected {want!r}")
            print(f"ok: baseUrl {server.get('baseUrl')!r} -> resourceIdentifier {have!r}")
            return
    sys.exit("FAIL: no muster HelmRelease in the render")


if __name__ == "__main__":
    main()
