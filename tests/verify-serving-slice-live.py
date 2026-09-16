#!/usr/bin/env python3
"""The live half of verify-serving-slice that needs no GPU: did the agentgateway
controller fetch the models Gateway's JWKS? (giantswarm/agent-platform#505)

The controller fetches the JWKS of every JWT policy and pushes the keys to the
data plane over xDS. A fetch that fails -- its network policy denies the issuer,
the issuer's CA is not trusted, the host does not resolve -- pushes an EMPTY key
set: the data plane then refuses every token with `401 token uses the unknown
key "<kid>"` and logs nothing about a fetch, because it never fetches. The state
lives in two places this script reads, against the current kubeconfig:

- the policy `<gateway>-jwt`'s Accepted condition on every ancestor: `reason:
  Valid` is a fetched key set; `PartiallyValid` names the JWKS URL the controller
  could not fetch;
- the controller's `jwks-store-<hash>` ConfigMap for the backend's URL (built from
  `<gateway>-jwks`'s static host and port and the policy's jwksPath): `fetchedAt`
  and `nkeys`, which must be positive.

On a failure the controller's `error fetching jwks` lines are printed: their
`error=` is the root cause (`dial tcp ... i/o timeout` = the controller's egress,
`x509` = the issuer's CA, `no such host` = DNS).

Deliberately stdlib-only. kubectl on PATH; --context selects a kubeconfig context.
"""

import argparse
import json
import subprocess
import sys

STORE_LABEL = "app.kubernetes.io/component=jwks-store"


def kubectl(args: list[str], context: str) -> str:
    cmd = ["kubectl", *(["--context", context] if context else []), *args]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        sys.exit(f"ERROR: {' '.join(cmd)}\n{result.stderr.strip()}")
    return result.stdout


def get(kind: str, name: str, namespace: str, context: str) -> dict:
    return json.loads(kubectl(["get", kind, name, "-n", namespace, "-o", "json"], context))


def ok(msg: str) -> None:
    print(f"ok: {msg}")


def controller_errors(args: argparse.Namespace) -> str:
    """The controller's fetch errors of the last hour, the diagnosis of a failed fetch."""
    result = subprocess.run(["kubectl", *(["--context", args.context] if args.context else []), "logs", f"deploy/{args.controller_deployment}",
                             "-n", args.controller_namespace, "--all-containers", "--since=1h"], capture_output=True, text=True, check=False)
    lines = [line for line in result.stdout.splitlines() if "error fetching jwks" in line or "error processing policy" in line]
    if not lines:
        return f"(no `error fetching jwks` line in deploy/{args.controller_deployment} -n {args.controller_namespace} in the last hour)"
    return "\n".join(lines[-5:])


def check_policy(args: argparse.Namespace) -> str:
    """Accepted must be True with reason Valid on every ancestor; returns the jwksPath."""
    policy = get("agentgatewaypolicy", f"{args.gateway}-jwt", args.namespace, args.context)
    providers = policy["spec"].get("traffic", {}).get("jwtAuthentication", {}).get("providers", [])
    if len(providers) != 1 or "remote" not in providers[0].get("jwks", {}):
        sys.exit(f"FAIL: {args.gateway}-jwt carries {len(providers)} providers; expected one with a remote JWKS")
    path = providers[0]["jwks"]["remote"].get("jwksPath", "")
    ancestors = policy.get("status", {}).get("ancestors", [])
    if not ancestors:
        sys.exit(f"FAIL: {args.gateway}-jwt has no status.ancestors: the controller has not reconciled it (is the Gateway {args.gateway} of its class, and the controller running?)")
    for ancestor in ancestors:
        ref = ancestor.get("ancestorRef", {})
        where = f"{ref.get('kind', '?')}/{ref.get('name', '?')}"
        accepted = [c for c in ancestor.get("conditions", []) if c.get("type") == "Accepted"]
        if not accepted:
            sys.exit(f"FAIL: {args.gateway}-jwt on {where} has no Accepted condition")
        cond = accepted[0]
        print(f"  {where}: Accepted={cond.get('status')} reason={cond.get('reason')} message={cond.get('message', '')!r}")
        if cond.get("status") != "True" or cond.get("reason") != "Valid":
            sys.exit(f"FAIL: {args.gateway}-jwt on {where}: Accepted={cond.get('status')} reason={cond.get('reason')} -- {cond.get('message', '')}\n"
                     f"The controller pushed no key set, so every token is refused as 'unknown key'. The controller's fetch errors:\n{controller_errors(args)}")
    ok(f"{args.gateway}-jwt: Accepted=True reason=Valid on {len(ancestors)} ancestor(s)")
    return path


def jwks_url(args: argparse.Namespace, path: str) -> str:
    backend = get("agentgatewaybackend", f"{args.gateway}-jwks", args.namespace, args.context)
    static = backend["spec"].get("static", {})
    if not static.get("host") or not static.get("port"):
        sys.exit(f"FAIL: {args.gateway}-jwks is not a static backend with host and port: {backend['spec']}")
    scheme = "https" if "tls" in backend["spec"].get("policies", {}) else "http"
    return f"{scheme}://{static['host']}:{static['port']}{path}"


def check_store(args: argparse.Namespace, url: str) -> None:
    stores = json.loads(kubectl(["get", "configmap", "-n", args.controller_namespace, "-l", STORE_LABEL, "-o", "json"], args.context))
    entries = []
    for item in stores.get("items", []):
        raw = item.get("data", {}).get("jwks-store")
        if raw:
            entry = json.loads(raw)
            entry["_name"] = item["metadata"]["name"]
            entries.append(entry)
    mine = [e for e in entries if e.get("url") == url]
    if not mine:
        known = ", ".join(sorted(e.get("url", "?") for e in entries)) or "none"
        sys.exit(f"FAIL: the controller holds no key set for {url} (jwks-store ConfigMaps in {args.controller_namespace}: {known}). The controller's fetch errors:\n{controller_errors(args)}")
    entry = mine[0]
    if int(entry.get("nkeys", 0)) < 1:
        sys.exit(f"FAIL: the controller's key set for {url} is empty ({entry['_name']}, fetchedAt {entry.get('fetchedAt')}); every token is refused as 'unknown key'.\n{controller_errors(args)}")
    ok(f"{entry['_name']}: {entry['nkeys']} key(s) for {url}, fetched {entry.get('fetchedAt')}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--namespace", required=True, help="the serving slice's release namespace (the models Gateway, its policy and backend)")
    parser.add_argument("--gateway", default="models", help="modelServing.modelsGateway.name (default models)")
    parser.add_argument("--controller-namespace", default="agent-platform", help="the namespace the agentgateway controller runs in (default agent-platform)")
    parser.add_argument("--controller-deployment", default="agentgateway-controller", help="the controller Deployment (default agentgateway-controller)")
    parser.add_argument("--context", default="", help="kubeconfig context (default: the current one)")
    args = parser.parse_args()
    path = check_policy(args)
    url = jwks_url(args, path)
    check_store(args, url)
    print(f"the controller fetched the models Gateway's JWKS from {url}; a person's id_token can verify at the {args.gateway} Gateway.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
