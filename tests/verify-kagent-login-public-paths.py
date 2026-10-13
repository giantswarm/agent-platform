#!/usr/bin/env python3
"""Assert kagent's oauth2-proxy lets the sign-in page load without a session and
nothing else (giantswarm/agent-platform#989).

kagent's UI is a Vite build: /login is index.html, which loads its bundle from
/assets/<name>-<hash>.js, the runtime settings from /env-config.js and its icon
from /favicon.svg. oauth2-proxy answers 403 to every path its skip-auth routes
do not admit, so a missing entry leaves the page blank without its script.

The check renders the meta chart with kagent on, reads the oauth2-proxy
extraArgs the kagent HelmRelease carries, requires the connectivity chart's
values to carry the same skip-auth arguments, and matches both against the
paths the sign-in page fetches (admitted) and the app's own paths (refused).
oauth2-proxy matches each pattern against the request's path; Python's re
reads these patterns as Go's RE2 does.

Usage: verify-kagent-login-public-paths.py <meta chart dir> <connectivity chart dir>
Needs PyYAML.
"""
import importlib.util
import os
import re
import sys

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))


def load(name: str):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), os.path.join(HERE, f"{name}.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


cc = load("verify-components-charts")

KAGENT_ON = ["--set", "components.kagent.enabled=true", "--set", "ingress.parentRefs[0].name=x"]
KEYS = ("skip-auth-route", "skip-auth-regex")

ADMITTED = [
    "/login",
    "/env-config.js",
    "/favicon.svg",
    "/assets/index-CLR-viT1.js",
    "/assets/rolldown-runtime-hePW80VL.js",
    "/assets/index-Dk3x9aQe.css",
    "/health",
]
REFUSED = [
    "/",
    "/agents",
    "/agents/kagent/sre-agent",
    "/api/me",
    "/api/agents",
    "/api/sessions/abc",
    "/api/assets/x.js",
    "/assets/",
    "/assets/sub/x.js",
    "/assets/x.json",
    "/env-config.js.map",
    "/login/x",
    "/loginx",
    "/favicon.svgx",
]


def skip_args(extra_args: dict, where: str) -> dict:
    args = {k: extra_args.get(k) for k in KEYS}
    missing = [k for k, v in args.items() if not v]
    if missing:
        cc.fail(f"{where} carries no oauth2-proxy {', '.join(missing)}")
    return args


def public(args: dict, path: str) -> bool:
    return any(re.search(pattern, path) for pattern in args.values())


def main(meta: str, connectivity: str) -> int:
    rendered = cc.docs(cc.render_meta(meta, [*cc.QUICKSTART, *KAGENT_ON]))
    if ("HelmRelease", "kagent") not in rendered:
        cc.fail("the meta chart renders no kagent HelmRelease with components.kagent on")
    proxy = yaml.safe_load(cc.hr_values(rendered[("HelmRelease", "kagent")])).get("oauth2-proxy") or {}
    args = skip_args(proxy.get("extraArgs") or {}, "the kagent HelmRelease")

    with open(os.path.join(connectivity, "values.yaml"), encoding="utf-8") as f:
        own = ((yaml.safe_load(f).get("kagent") or {}).get("oauth2-proxy") or {}).get("extraArgs") or {}
    if skip_args(own, "the connectivity chart's values.yaml") != args:
        cc.fail("the connectivity chart's kagent.oauth2-proxy skip-auth arguments differ from the meta chart's")

    closed = [p for p in ADMITTED if not public(args, p)]
    if closed:
        cc.fail(f"the sign-in page cannot load without a session: oauth2-proxy refuses {', '.join(closed)} ({args})")
    opened = [p for p in REFUSED if public(args, p)]
    if opened:
        cc.fail(f"oauth2-proxy serves {', '.join(opened)} without a session ({args})")
    print(f"ok: without a session oauth2-proxy admits the sign-in page ({', '.join(ADMITTED)}) and refuses {', '.join(REFUSED)}")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    sys.exit(main(sys.argv[1], sys.argv[2]))
