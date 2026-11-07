#!/usr/bin/env python3
"""Assert kagent always has a database: the kagent chart deploys no PostgreSQL
from 1.11 on, so the meta chart refuses kagent on a range that admits 1.11 or
later without the platform's CloudNativePG database or an own connection
(agent-platform.validateKagentDatabase).

- refused, naming CloudNativePG as the supported shape: kagent on with
  postgres.enabled off and no own connection, on the chart's range, on a
  range across 1.11 and on a prerelease range;
- refused with postgres.enabled on but postgres.databases.kagent-v2 off;
- admitted: the platform's database (the derived connectionStringSecretRef and
  role reach the kagent release), an own urlFile, an own
  connectionStringSecretRef, a range below 1.11, kagent off.

Usage: verify-kagent-database.py <meta chart dir>
Needs PyYAML.
"""
import subprocess
import sys

import yaml

BASE = ["--set", "ingress.parentRefs[0].name=x", "--set", "kagent.harness.snapshotLocation=s3://ci-agent-snapshots/agents",
        "--set", "components.kagent.enabled=true", "--set", "global.identity.issuerUrl=https://dex.ci.example.com"]
MESSAGE = "whose chart deploys no PostgreSQL: run kagent on the platform's CloudNativePG database, the supported shape"


def render(chart: str, args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(["helm", "template", "agent-platform", chart, "-n", "agent-platform", *BASE, *args],
                          capture_output=True, text=True)


def ranged(rng: str) -> list[str]:
    return ["--set-string", f"components.kagent.versionRange={rng}", "--set-string", f"components.kagent-crds.versionRange={rng}"]


def kagent_database(out: str) -> dict:
    for doc in yaml.safe_load_all(out):
        if isinstance(doc, dict) and doc.get("kind") == "HelmRelease" and doc["metadata"]["name"] == "kagent":
            return ((doc["spec"].get("values") or {}).get("database") or {}).get("postgres") or {}
    sys.exit("FAIL: the render carries no kagent HelmRelease")


def main(chart: str) -> int:
    print("--> refused without a database, naming CloudNativePG")
    for what, args in (
        ("the chart's range", []),
        ("a range across 1.11", ranged(">=1.10.0 <1.12.0")),
        ("a prerelease range", ranged(">=1.11.0-0 <1.12.0-0")),
        ("postgres.enabled with the kagent-v2 database off", ["--set", "postgres.enabled=true", "--set", "postgres.databases.kagent-v2.enabled=false"]),
    ):
        r = render(chart, args)
        if r.returncode == 0 or MESSAGE not in r.stderr:
            sys.exit(f"FAIL: {what}: the render did not fail naming the supported shape\n{r.stderr}")
        print(f"ok: {what}")

    print("--> admitted with a database")
    r = render(chart, ["--set", "postgres.enabled=true"])
    if r.returncode != 0:
        sys.exit(f"FAIL: the platform's database is refused\n{r.stderr}")
    pg = kagent_database(r.stdout)
    if pg.get("connectionStringSecretRef") != {"name": "kagent-pg-kagent-v2-app", "key": "uri"} or pg.get("role") != "kagent":
        sys.exit(f"FAIL: the platform's database does not reach the kagent release: {pg}")
    print("ok: the platform's database, its Secret kagent-pg-kagent-v2-app and role kagent forwarded")
    for what, args in (
        ("an own urlFile", ["--set", "kagent.database.postgres.urlFile=/etc/kagent-db/uri"]),
        ("an own connectionStringSecretRef", ["--set", "kagent.database.postgres.connectionStringSecretRef.name=own-db"]),
        ("a range below 1.11", [*ranged(">=1.10.0 <1.11.0"), "--set", "kagent.controller.image.repository=giantswarm/kagent/controller", "--set", "global.identity.issuerUrl="]),
        ("kagent off", ["--set", "components.kagent.enabled=false"]),
    ):
        r = render(chart, args)
        if r.returncode != 0:
            sys.exit(f"FAIL: {what} is refused\n{r.stderr}")
        print(f"ok: {what}")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    sys.exit(main(sys.argv[1]))
