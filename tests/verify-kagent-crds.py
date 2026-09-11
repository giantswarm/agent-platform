#!/usr/bin/env python3
"""Validate every kagent.dev object the connectivity chart renders against the
kagent line's CRDs, and assert that no render of the chart carries a
kagent.dev/v1alpha2 object.

The chart renders the platform's kagent catalog — `kagent.modelConfigs[]` as
ModelConfigs, `kagent.remoteMcpServers[]` as RemoteMCPServers — at
kagent.dev/v1alpha3, the only version kagent API v2 serves. `helm template`
cannot tell a field the CRD prunes from one it keeps, so this test checks the
rendered objects against the CRDs' openAPIV3Schema the way the API server would
at admission: every field known, every required field present, enums, types,
lengths; plus the handful of the CRDs' CEL rules the rendered shapes can trip
(a headersFrom entry carries exactly one of value / valueFrom; a provider block
belongs to its provider; apiKeySecret and apiKeySecretKey come together; no
spec.tls on an http:// url). A field the schema does not know is a failure — the
API server would drop it silently and the object would lose that setting.

The CRDs are the kagent line's at its pinned release: the plain-YAML templates
`helm/kagent-crds/templates/kagent.dev_*.yaml` of giantswarm/kagent-upstream at
KAGENT_LINE_REF (the same files the meta chart's `components.kagent-crds`
installs). Fetched into a temp dir by default; KAGENT_CRDS_DIR points at a
directory holding them for an offline run.

Shapes rendered: every helm/agent-platform-connectivity/ci/*.yaml as-is, and a
catalog shape with kagent on, one ModelConfig routed through the LLM listener
and two operator RemoteMCPServers (one with tokenSecret). The last section is a
self-test: a deliberately wrong ModelConfig and RemoteMCPServer must fail, so a
validator that accepts everything cannot pass.

Usage: verify-kagent-crds.py <connectivity chart dir>
"""
import glob
import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.request

import yaml

# The kagent line's release the platform pins (meta chart: components.kagent /
# components.kagent-crds range, kagent.tag). Move it with the pin.
KAGENT_LINE_REF = "v0.11.0-gs.1"
CRD_URL = "https://raw.githubusercontent.com/giantswarm/kagent-upstream/{ref}/helm/kagent-crds/templates/{file}"
CRD_FILES = {
    "ModelConfig": "kagent.dev_modelconfigs.yaml",
    "RemoteMCPServer": "kagent.dev_remotemcpservers.yaml",
}
API_VERSION = "kagent.dev/v1alpha3"
# ModelConfigSpec's per-provider blocks: key -> the spec.provider it belongs to
# (the CRD's `provider.<key> must be nil if the provider is not <Provider>` rules).
PROVIDER_BLOCKS = {
    "anthropic": "Anthropic", "openAI": "OpenAI", "azureOpenAI": "AzureOpenAI", "ollama": "Ollama",
    "gemini": "Gemini", "geminiVertexAI": "GeminiVertexAI", "anthropicVertexAI": "AnthropicVertexAI",
    "bedrock": "Bedrock", "sapAICore": "SAPAICore", "foundry": "Foundry",
}

CATALOG_SHAPE = [
    "--set", "ingress.parentRefs[0].name=x",
    "--set", "components.kagent.enabled=true",
    "--set", "kagent.namespaceOverride=kagent",
    "--set-json", 'kagent.modelConfigs=[{"name":"anthropic-sonnet","displayName":"Anthropic Sonnet 4.6","provider":"Anthropic","model":"claude-sonnet-4-6","apiKeySecret":"kagent-anthropic","baseUrl":"http://agentgateway.default.svc:8081"}]',
    "--set-json", 'kagent.remoteMcpServers=[{"name":"external","url":"https://external.example/mcp","tokenSecret":"external-token"},{"name":"open","url":"http://open.tools.svc:8080/mcp","description":"an MCP server reached with the propagated token"}]',
]


def fail(msg: str) -> None:
    sys.exit(f"FAIL: {msg}")


def run(args: list[str]) -> str:
    r = subprocess.run(args, capture_output=True, text=True)
    if r.returncode != 0:
        fail(f"{' '.join(args)}\n{r.stderr}")
    return r.stdout


def load_crds() -> dict[str, dict]:
    """kind -> the v1alpha3 openAPIV3Schema of the pinned CRD."""
    src = os.environ.get("KAGENT_CRDS_DIR")
    out = {}
    with tempfile.TemporaryDirectory() as d:
        for kind, file in CRD_FILES.items():
            path = os.path.join(src, file) if src else os.path.join(d, file)
            if not src:
                url = CRD_URL.format(ref=KAGENT_LINE_REF, file=file)
                with urllib.request.urlopen(url, timeout=60) as r:
                    open(path, "wb").write(r.read())
            crd = yaml.safe_load(open(path))
            versions = {v["name"]: v for v in crd["spec"]["versions"]}
            if API_VERSION.split("/")[1] not in versions:
                fail(f"{file} at {KAGENT_LINE_REF} serves {sorted(versions)}, not {API_VERSION}")
            if crd["spec"]["names"]["kind"] != kind:
                fail(f"{file} defines kind {crd['spec']['names']['kind']}, expected {kind}")
            out[kind] = versions[API_VERSION.split("/")[1]]["schema"]["openAPIV3Schema"]
    return out


def check(schema: dict, value, path: str, errors: list[str]) -> None:
    """Structural validation of one value against an openAPIV3Schema node, the
    way the API server's structural-schema validation and pruning see it."""
    if schema.get("x-kubernetes-preserve-unknown-fields"):
        return
    if schema.get("x-kubernetes-int-or-string"):
        if not isinstance(value, (int, str)) or isinstance(value, bool):
            errors.append(f"{path}: expected an integer or a string, got {type(value).__name__}")
        return
    t = schema.get("type")
    if value is None:
        if t is not None:
            errors.append(f"{path}: null is not a {t}")
        return
    if t == "object":
        if not isinstance(value, dict):
            errors.append(f"{path}: expected an object, got {type(value).__name__}")
            return
        props = schema.get("properties", {})
        extra = schema.get("additionalProperties")
        for k, v in value.items():
            if k in props:
                check(props[k], v, f"{path}.{k}", errors)
            elif isinstance(extra, dict):
                check(extra, v, f"{path}.{k}", errors)
            elif extra is True:
                continue
            else:
                errors.append(f"{path}.{k}: not a field of the CRD — the API server would prune it")
        for k in schema.get("required", []):
            if k not in value:
                errors.append(f"{path}.{k}: required by the CRD, missing")
    elif t == "array":
        if not isinstance(value, list):
            errors.append(f"{path}: expected an array, got {type(value).__name__}")
            return
        for i, v in enumerate(value):
            check(schema.get("items", {}), v, f"{path}[{i}]", errors)
    elif t == "string":
        if not isinstance(value, str):
            errors.append(f"{path}: expected a string, got {type(value).__name__} ({value!r}) — quote it")
            return
        if "enum" in schema and value not in schema["enum"]:
            errors.append(f"{path}: {value!r} not in {schema['enum']}")
        if "minLength" in schema and len(value) < schema["minLength"]:
            errors.append(f"{path}: shorter than minLength {schema['minLength']}")
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            errors.append(f"{path}: longer than maxLength {schema['maxLength']}")
        if "pattern" in schema and not re.search(schema["pattern"], value):
            errors.append(f"{path}: {value!r} does not match {schema['pattern']}")
    elif t == "integer":
        if isinstance(value, bool) or not isinstance(value, int):
            errors.append(f"{path}: expected an integer, got {type(value).__name__} ({value!r})")
            return
        if "minimum" in schema and value < schema["minimum"]:
            errors.append(f"{path}: below minimum {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            errors.append(f"{path}: above maximum {schema['maximum']}")
    elif t == "number":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            errors.append(f"{path}: expected a number, got {type(value).__name__}")
    elif t == "boolean":
        if not isinstance(value, bool):
            errors.append(f"{path}: expected a boolean, got {type(value).__name__} ({value!r})")


def check_rules(kind: str, spec: dict, path: str, errors: list[str]) -> None:
    """The CRDs' CEL rules a rendered catalog object can trip (the API server
    evaluates the full set at admission; the lab proof covers that)."""
    if kind == "RemoteMCPServer":
        for i, h in enumerate(spec.get("headersFrom") or []):
            if ("value" in h) == ("valueFrom" in h):
                errors.append(f"{path}.headersFrom[{i}]: exactly one of value or valueFrom must be set")
        if str(spec.get("url", "")).startswith("http://") and "tls" in spec:
            errors.append(f"{path}.tls: must be unset when spec.url has the http:// scheme")
    if kind == "ModelConfig":
        provider = spec.get("provider", "OpenAI")
        for key, owner in PROVIDER_BLOCKS.items():
            if key in spec and provider != owner:
                errors.append(f"{path}.{key}: the {owner} block on a {provider} ModelConfig (the CRD refuses it)")
        if "apiKeySecretKey" in spec and "apiKeySecret" not in spec:
            errors.append(f"{path}.apiKeySecretKey: set without apiKeySecret")
        if "apiKeySecret" in spec and "apiKeySecretKey" not in spec and provider not in ("Bedrock", "SAPAICore"):
            errors.append(f"{path}.apiKeySecret: set without apiKeySecretKey")
        if spec.get("apiKeyPassthrough") and spec.get("apiKeySecret"):
            errors.append(f"{path}: apiKeyPassthrough and apiKeySecret are mutually exclusive")


def validate(doc: dict, crds: dict[str, dict], where: str) -> list[str]:
    errors: list[str] = []
    kind = doc.get("kind")
    name = doc.get("metadata", {}).get("name", "?")
    path = f"{where}: {kind}/{name}"
    if doc.get("apiVersion") != API_VERSION:
        errors.append(f"{path}: apiVersion {doc.get('apiVersion')!r}, the line serves {API_VERSION} only")
        return errors
    if kind not in crds:
        errors.append(f"{path}: kind {kind} is not one the chart is meant to render (no CRD pinned for it)")
        return errors
    if not doc.get("metadata", {}).get("namespace"):
        errors.append(f"{path}: no metadata.namespace — the catalog lives in the kagent namespace")
    check(crds[kind]["properties"]["spec"], doc.get("spec"), f"{path}.spec", errors)
    if isinstance(doc.get("spec"), dict):
        check_rules(kind, doc["spec"], f"{path}.spec", errors)
    return errors


def render(chart: str, args: list[str]) -> list[dict]:
    out = run(["helm", "template", "t", chart, *args])
    return [d for d in yaml.safe_load_all(out) if isinstance(d, dict)]


def kagent_docs(docs: list[dict]) -> list[dict]:
    return [d for d in docs if str(d.get("apiVersion", "")).startswith("kagent.dev/")]


def main(chart: str) -> int:
    crds = load_crds()
    print(f"ok: CRDs {sorted(CRD_FILES)} of giantswarm/kagent-upstream at {KAGENT_LINE_REF} serve {API_VERSION}")

    shapes: list[tuple[str, list[str]]] = [(f"ci/{os.path.basename(f)}", ["-f", f]) for f in sorted(glob.glob(f"{chart}/ci/*.yaml"))]
    shapes.append(("catalog (kagent on, one ModelConfig, two operator RemoteMCPServers)", CATALOG_SHAPE))
    total = 0
    for where, args in shapes:
        docs = kagent_docs(render(chart, args))
        errors = [e for d in docs for e in validate(d, crds, where)]
        if errors:
            fail("\n  ".join(["rendered kagent objects the CRDs would refuse or prune:", *errors]))
        kinds = sorted(f"{d['kind']}/{d['metadata']['name']}" for d in docs)
        print(f"ok: {where}: {len(docs)} kagent.dev object(s) valid against {KAGENT_LINE_REF}" + (f" — {', '.join(kinds)}" if kinds else ""))
        total += len(docs)
    if total == 0:
        fail("no shape rendered a kagent.dev object; the catalog shape must")

    catalog = kagent_docs(render(chart, CATALOG_SHAPE))
    by_name = {(d["kind"], d["metadata"]["name"]): d for d in catalog}
    if any(d["metadata"]["namespace"] != "kagent" for d in catalog):
        fail("a catalog object is not in the kagent namespace (kagent.namespaceOverride)")
    if ("RemoteMCPServer", "muster") in by_name or any(d["kind"] == "RemoteMCPServer" and "muster" in json.dumps(d["spec"]) for d in catalog):
        fail("a RemoteMCPServer for muster is back in the platform's render: the Generic agent chart 1.x renders one per agent")
    external = by_name[("RemoteMCPServer", "external")]["spec"]
    if external.get("headersFrom") != [{"name": "Authorization", "valueFrom": {"type": "Secret", "name": "external-token", "key": "token"}}]:
        fail(f"tokenSecret no longer renders the v1alpha3 headersFrom shape: {external.get('headersFrom')}")
    if "headersFrom" in by_name[("RemoteMCPServer", "open")]["spec"]:
        fail("an operator RemoteMCPServer without tokenSecret carries headersFrom")
    print("ok: catalog objects in the kagent namespace, no muster RemoteMCPServer, tokenSecret → headersFrom valueFrom Secret")

    # Self-test: the validator must bite.
    bad_mc = {"apiVersion": API_VERSION, "kind": "ModelConfig", "metadata": {"name": "bad", "namespace": "kagent"},
              "spec": {"provider": "OpenAI", "model": "gpt", "apiKeySecret": "s", "apiKeySecretKey": "k", "openai": {"baseUrl": "http://x"}, "anthropic": {"baseUrl": "http://y"}}}
    bad_rms = {"apiVersion": API_VERSION, "kind": "RemoteMCPServer", "metadata": {"name": "bad", "namespace": "kagent"},
               "spec": {"url": "http://x/mcp", "protocol": "GRPC", "headersFrom": [{"name": "X", "value": "a", "valueFrom": {"type": "Secret", "name": "s", "key": "k"}}]}}
    e1, e2 = validate(bad_mc, crds, "self-test"), validate(bad_rms, crds, "self-test")
    want1 = ["spec.openai: not a field", "spec.anthropic: the Anthropic block on a OpenAI"]
    want2 = ["spec.description: required", "protocol: 'GRPC' not in", "exactly one of value or valueFrom"]
    for want, got in ((want1, e1), (want2, e2)):
        for w in want:
            if not any(w in g for g in got):
                fail(f"self-test: the validator did not report {w!r}; got {got}")
    old = {"apiVersion": "kagent.dev/v1alpha2", "kind": "RemoteMCPServer", "metadata": {"name": "old", "namespace": "kagent"}, "spec": {"description": "d", "url": "http://x"}}
    if not any("v1alpha3 only" in e for e in validate(old, crds, "self-test")):
        fail("self-test: a kagent.dev/v1alpha2 object passed")
    print("ok: self-test — an unknown field, a foreign provider block, a missing required field, a bad enum, a two-source header and a v1alpha2 object are refused")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    sys.exit(main(sys.argv[1]))
