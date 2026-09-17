#!/usr/bin/env python3
"""Assert tests/verify-serving-slice-live.py reads the agentgateway controller's
jwks-store ConfigMaps in the shape the controller writes them
(giantswarm/agent-platform#515).

The store's `jwks-store` entry is `{requestKey, url, fetchedAt, jwks}` -- the
`Keyset` of the controller's `jwks` package -- with `jwks` the fetched JWKS
document as a JSON string; there is no key-count field. The fixture
tests/fixtures/jwks-store-configmaps.json is the `kubectl get configmap -l
app.kubernetes.io/component=jwks-store -o json` of a controller holding the
models Gateway's issuer (two keys, the in-cluster Dex as seen on gazelle), a
second issuer whose fetch produced an empty key set, and a labelled ConfigMap
without the entry; the names and request keys are derived the way the
controller derives them. Asserted:

- the entries: exactly the two ConfigMaps carrying the entry, named, with the
  writer's four fields and no count field;
- the key count comes from `jwks`: two kids for the issuer, none for the empty set;
- check_store against the fixture (kubectl stubbed): passes for the issuer's URL
  naming the ConfigMap, the count and the kids; fails naming the URL for the
  empty set and for a URL the store does not hold (listing the URLs it does);
- an entry whose `jwks` is not a JWKS document fails naming the ConfigMap.

Deliberately stdlib-only, like the script it covers.
"""

import argparse
import contextlib
import importlib.util
import io
import json
import pathlib
import sys

TESTS = pathlib.Path(__file__).resolve().parent
FIXTURE = TESTS / "fixtures" / "jwks-store-configmaps.json"
ISSUER_URL = "http://dex.giantswarm.svc.cluster.local:5556/keys"
EMPTY_URL = "https://idp.example.invalid/keys"
UNKNOWN_URL = "https://dex.example.invalid/keys"
ISSUER_KIDS = ["6f1c2d9a0b4e8f7c3a5d1e2b9c8d7e6f5a4b3c2d", "a3e5b7c9d1f3a5b7c9d1e3f5a7b9c1d3e5f7a9b1"]
WRITER_FIELDS = {"requestKey", "url", "fetchedAt", "jwks"}


def load_live_module():
    spec = importlib.util.spec_from_file_location("verify_serving_slice_live", TESTS / "verify-serving-slice-live.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def check(cond: bool, msg: str) -> None:
    if not cond:
        sys.exit(f"FAIL: {msg}")
    print(f"ok: {msg}")


def run_check_store(live, fixture_text: str, url: str) -> tuple[str, str | None]:
    """check_store over the fixture with kubectl and the log reader stubbed: (stdout, the FAIL message or None)."""
    live.kubectl = lambda args, context: fixture_text
    live.controller_errors = lambda args: "(controller logs are not read offline)"
    args = argparse.Namespace(controller_namespace="agent-platform", controller_deployment="agentgateway-controller", context="")
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        try:
            live.check_store(args, url)
        except SystemExit as exc:
            return out.getvalue(), str(exc.code)
    return out.getvalue(), None


def main() -> int:
    live = load_live_module()
    fixture_text = FIXTURE.read_text()
    items = json.loads(fixture_text)["items"]
    check(len(items) == 3 and all(i["metadata"]["labels"]["app.kubernetes.io/component"] == "jwks-store" for i in items),
          "the fixture is three jwks-store ConfigMaps (the store's label selector)")

    entries = live.store_entries(items)
    check(len(entries) == 2, f"store_entries skips the ConfigMap without a `{live.STORE_KEY}` entry: {len(entries)} entries")
    for entry in entries:
        fields = set(entry) - {"_name"}
        check(fields == WRITER_FIELDS, f"{entry['_name']}: the writer's fields {sorted(fields)}, no count field")
        check(entry["_name"].startswith("jwks-store-") and len(entry["_name"]) == len("jwks-store-") + 64,
              f"{entry['_name']}: named jwks-store-<sha256>")
    by_url = {e["url"]: e for e in entries}
    check(set(by_url) == {ISSUER_URL, EMPTY_URL}, f"one entry per fetched URL: {sorted(by_url)}")

    check(live.keyset_kids(by_url[ISSUER_URL]) == ISSUER_KIDS, f"the issuer's key set has {len(ISSUER_KIDS)} kids, read from the jwks JSON string")
    check(live.keyset_kids(by_url[EMPTY_URL]) == [], "an empty key set has no kid")

    out, fail = run_check_store(live, fixture_text, ISSUER_URL)
    name = by_url[ISSUER_URL]["_name"]
    check(fail is None and f"ok: {name}: 2 key(s) for {ISSUER_URL}" in out and all(kid in out for kid in ISSUER_KIDS),
          f"check_store passes for the issuer, naming the ConfigMap, the count and the kids: {out.strip()!r} fail={fail!r}")

    out, fail = run_check_store(live, fixture_text, EMPTY_URL)
    check(fail is not None and EMPTY_URL in fail and "empty" in fail and by_url[EMPTY_URL]["_name"] in fail,
          f"check_store fails naming the URL and the ConfigMap for an empty key set: {fail!r}")

    out, fail = run_check_store(live, fixture_text, UNKNOWN_URL)
    check(fail is not None and UNKNOWN_URL in fail and "no key set" in fail and ISSUER_URL in fail and EMPTY_URL in fail,
          f"check_store fails naming the URL the store does not hold and the URLs it does: {fail!r}")

    broken = json.loads(fixture_text)
    for item in broken["items"]:
        raw = item["data"].get(live.STORE_KEY)
        if raw and json.loads(raw)["url"] == ISSUER_URL:
            entry = json.loads(raw)
            entry["jwks"] = "not a JWKS document"
            item["data"][live.STORE_KEY] = json.dumps(entry)
    out, fail = run_check_store(live, json.dumps(broken), ISSUER_URL)
    check(fail is not None and "not a JWKS document" in fail and name in fail,
          f"check_store fails naming the ConfigMap when jwks is not a JWKS document: {fail!r}")

    print("the live check reads the controller's jwks-store in the shape the controller writes it.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
