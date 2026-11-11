#!/usr/bin/env python3
"""Prove pending consent and access tokens survive Kubernetes MCP pod loss.

Run against a leased agentlab with two MCP replicas and the candidate chart.
Feed the DCR registration token on stdin through the credential broker.
Only public lab fixture users are accepted. No credentials are printed.
"""

import argparse
import base64
import hashlib
import secrets
import subprocess
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit

import requests

sys.path.insert(0, str(Path(__file__).parent / "ats"))
from oauth_browser import follow_authorization  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--issuer-url", required=True)
    parser.add_argument("--ca", required=True)
    parser.add_argument("--kubeconfig", required=True)
    parser.add_argument("--namespace", default="agent-platform")
    args = parser.parse_args()
    base = args.base_url.rstrip("/")
    session = requests.Session()
    session.verify = args.ca
    callback = "http://127.0.0.1:48763/callback"
    registration_token = sys.stdin.read().strip()
    assert registration_token, "registration token missing on stdin"
    registration = session.post(base + "/oauth/register", timeout=30,
                                headers={"Authorization": "Bearer " + registration_token},
                                json={"client_name": "oauth-recovery-proof", "redirect_uris": [callback],
                                      "token_endpoint_auth_method": "client_secret_basic",
                                      "grant_types": ["authorization_code"], "response_types": ["code"]})
    assert registration.status_code in (200, 201), f"DCR status {registration.status_code}"
    client = registration.json()
    print("DCR registered", flush=True)
    discovery = session.get(base + "/.well-known/oauth-protected-resource", timeout=30)
    discovery.raise_for_status()
    resource = discovery.json()["resource"]
    verifier = secrets.token_urlsafe(32)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state = secrets.token_urlsafe(24)
    kube = ["kubectl", "--kubeconfig", args.kubeconfig, "-n", args.namespace]
    replacements = []

    def replace_replicas():
        print("Replacing MCP replicas", flush=True)
        deployment = subprocess.run(kube + ["get", "deploy", "mcp-kubernetes", "-o", "jsonpath={.spec.replicas}"],
                                    check=True, capture_output=True, text=True).stdout
        assert int(deployment) >= 2, "proof needs at least two MCP replicas"
        subprocess.run(kube + ["delete", "pods", "-l", "app.kubernetes.io/name=mcp-kubernetes", "--wait=true"], check=True)
        subprocess.run(kube + ["rollout", "status", "deployment/mcp-kubernetes", "--timeout=180s"], check=True)
        replacements.append(True)

    authorize = base + "/oauth/authorize?" + urlencode({
        "response_type": "code", "client_id": client["client_id"], "redirect_uri": callback,
        "state": state, "code_challenge": challenge, "code_challenge_method": "S256",
        "scope": "openid profile email groups", "resource": resource})
    completed = follow_authorization(session, authorize, callback, base, args.issuer_url,
                                     "admin@lab.local", "password", before_consent=replace_replicas)
    assert replacements, "no pending consent was observed"
    print("Consent completed after replica replacement", flush=True)
    query = parse_qs(urlsplit(completed).query)
    assert query.get("state") == [state] and "code" in query, "consent did not complete"
    token = session.post(base + "/oauth/token", timeout=30, auth=(client["client_id"], client["client_secret"]),
                         data={"grant_type": "authorization_code", "code": query["code"][0],
                               "redirect_uri": callback, "client_id": client["client_id"],
                               "code_verifier": verifier, "resource": resource})
    assert token.status_code == 200, f"token exchange status {token.status_code}"
    bearer = token.json()["access_token"]

    def initialize():
        response = session.post(base + "/mcp", timeout=30,
                                headers={"Authorization": "Bearer " + bearer, "Accept": "application/json, text/event-stream"},
                                json={"jsonrpc": "2.0", "id": 1, "method": "initialize",
                                      "params": {"protocolVersion": "2025-03-26", "capabilities": {},
                                                 "clientInfo": {"name": "oauth-recovery-proof", "version": "1"}}})
        assert response.status_code == 200, f"MCP initialize status {response.status_code}"
        assert '"serverInfo"' in response.text, "MCP initialize returned no server info"

    initialize()
    replace_replicas()
    initialize()
    print("PASS: pending consent completed after all replicas were replaced; issued access token survived a second replacement")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        # HTTP exceptions can carry URLs or page contents with OAuth credentials.
        sys.exit(f"FAIL: recovery proof stopped ({type(error).__name__}); inspect server diagnostics")
