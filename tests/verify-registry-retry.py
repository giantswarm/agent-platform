#!/usr/bin/env python3
"""Assert that tests/verify-components-charts.py's registry_get survives a
throttled registry and still fails on one that stays throttled.

gsoci.azurecr.io answers 429 Too Many Requests when it throttles anonymous
pulls; a registry_get that raised on the first one turned test-ingress-modes red
on throttling alone (giantswarm/agent-platform#1072). The check runs
registry_get against a local HTTP server that plays the registry, and replaces
time.sleep with a recorder, so the waits are asserted without being slept:

- 429s with `Retry-After: <seconds>`, then 200: the body is returned, each retry
  waited the seconds the answer named;
- 429 with `Retry-After: <HTTP date>`: the wait is the time until that date;
- 429 with a Retry-After above REGISTRY_MAX_WAIT: the wait is capped;
- 429s and 503s without Retry-After: exponential backoff from REGISTRY_BACKOFF;
- the token endpoint throttled during the anonymous challenge: retried too;
- 429 on every call: an HTTPError 429 after exactly REGISTRY_ATTEMPTS calls;
- 404 and 500: raised at once, never retried.

Offline (127.0.0.1 only). The script under test imports PyYAML, which the CI job
installs.
"""

import email.utils
import http.server
import importlib.util
import json
import os
import sys
import threading
import time
import urllib.error

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
_spec = importlib.util.spec_from_file_location("components_charts", os.path.join(HERE, "verify-components-charts.py"))
cc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cc)


class Registry(http.server.BaseHTTPRequestHandler):
    """Answers GETs from the class's script: path -> list of (status, headers),
    the last entry repeating; a 200 carries {"tags": ["ok"]}. A path the script
    does not name is a 404. Every request is recorded."""

    script: dict[str, list[tuple[int, dict[str, str]]]] = {}
    calls: list[str] = []

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        Registry.calls.append(path)
        answers = Registry.script.get(path)
        if not answers:
            status, headers = 404, {}
        else:
            n = Registry.calls.count(path) - 1
            status, headers = answers[min(n, len(answers) - 1)]
        body = json.dumps({"tags": ["ok"], "token": "t"} if status == 200 else {"errors": [{"code": "TOOMANYREQUESTS"}]}).encode()
        self.send_response(status)
        for k, v in headers.items():
            self.send_header(k, v.replace("{port}", str(self.server.server_port)))
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Registry)
threading.Thread(target=server.serve_forever, daemon=True).start()
BASE = f"http://127.0.0.1:{server.server_port}"
REPO = f"oci://127.0.0.1:{server.server_port}/charts/x"

waits: list[float] = []
cc.time.sleep = waits.append  # the module's time is this process's: record, don't sleep
cc.print = lambda *a, **k: None  # the NOTE lines are not under test

failures: list[str] = []


def case(name: str, script, path: str, want_waits=None, want_calls=None, want_error=None):
    Registry.script, Registry.calls = script, []
    waits.clear()
    cc.TOKENS.clear()
    try:
        body, _ = cc.registry_get(REPO, f"{BASE}{path}")
        got = ("ok", body)
    except urllib.error.HTTPError as e:
        got = ("error", e.code)
    want = ("error", want_error) if want_error else ("ok", {"tags": ["ok"], "token": "t"})
    problems = []
    if got != want:
        problems.append(f"got {got}, want {want}")
    if want_waits is not None and [round(w) for w in waits] != want_waits:
        problems.append(f"waited {[round(w, 1) for w in waits]}, want {want_waits}")
    if want_calls is not None and len(Registry.calls) != want_calls:
        problems.append(f"{len(Registry.calls)} registry calls ({Registry.calls}), want {want_calls}")
    if problems:
        failures.append(f"{name}: {'; '.join(problems)}")
    else:
        print(f"ok: {name}")


TAGS = "/v2/charts/x/tags/list"
TOO_MANY = 429

case("429 with Retry-After seconds, then 200: retried after the named waits",
     {TAGS: [(TOO_MANY, {"Retry-After": "3"}), (TOO_MANY, {"Retry-After": "7"}), (200, {})]},
     TAGS, want_waits=[3, 7], want_calls=3)

http_date = email.utils.formatdate(time.time() + 20, usegmt=True)
case("429 with Retry-After as an HTTP date: waits until that date",
     {TAGS: [(TOO_MANY, {"Retry-After": http_date}), (200, {})]},
     TAGS, want_calls=2)
if not waits or not 17 <= waits[0] <= 21:
    failures.append(f"Retry-After HTTP date: waited {waits}, want about 20s")

case("429 with a Retry-After above the cap: waits REGISTRY_MAX_WAIT",
     {TAGS: [(TOO_MANY, {"Retry-After": "3600"}), (200, {})]},
     TAGS, want_waits=[round(cc.REGISTRY_MAX_WAIT)], want_calls=2)

b = cc.REGISTRY_BACKOFF
case("429 and 503 without Retry-After: exponential backoff",
     {TAGS: [(TOO_MANY, {}), (503, {}), (TOO_MANY, {}), (200, {})]},
     TAGS, want_waits=[round(b), round(2 * b), round(4 * b)], want_calls=4)

case("token endpoint throttled during the anonymous challenge: retried",
     {TAGS: [(401, {"Www-Authenticate": 'Bearer realm="http://127.0.0.1:{port}/oauth2/token",service="s",scope="repository:charts/x:pull"'}), (200, {})],
      "/oauth2/token": [(TOO_MANY, {"Retry-After": "1"}), (200, {})]},
     TAGS, want_waits=[1], want_calls=4)

case("429 on every call: fails with 429 after exactly REGISTRY_ATTEMPTS calls",
     {TAGS: [(TOO_MANY, {"Retry-After": "1"})]},
     TAGS, want_waits=[1] * (cc.REGISTRY_ATTEMPTS - 1), want_calls=cc.REGISTRY_ATTEMPTS, want_error=TOO_MANY)

case("404: raised at once, not retried", {}, TAGS, want_waits=[], want_calls=1, want_error=404)

case("500: raised at once, not retried", {TAGS: [(500, {})]}, TAGS, want_waits=[], want_calls=1, want_error=500)

server.shutdown()
if failures:
    sys.exit("FAIL: registry_get retry:\n  " + "\n  ".join(failures))
