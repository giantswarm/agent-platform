#!/usr/bin/env python3
"""Assert agent-platform-operator's names have one source, and the meta chart
and the connectivity chart agree on every name they share for the operator
(giantswarm/agent-platform#1278).

The two charts are separate Helm charts: neither can include the other's
templates, and only the meta chart hands values to the other. So:

One source. The meta chart installs the operator and makes its names in one
helper (agent-platform.organizations.operator): its ServiceAccount
(serviceAccountName, the operator chart's name), its pods' selector labels
(podLabels), with RustFS the Secret of RustFS's keys rendered for it
(snapshotSecret) and with the Crossplane AWS store its IRSA role
(awsRoleName, <store role>-operator). It tells the operator these names and
derives them onto the connectivity release (agentPlatformOrganizations.operator),
whose templates hold no copy:
- the operator chart at the floor of components.agent-platform-operator
  .versionRange names its ServiceAccount and its pods' selector label as the
  meta chart derives them (network: gsoci.azurecr.io);
- off, the connectivity release carries no agentPlatformOrganizations.operator;
  on, exactly the derivation (snapshotSecret only with RustFS, awsRoleName only
  with the AWS store);
- tests/fixtures/organizations-operator-values.yaml, which the tests that
  render the connectivity chart alone pass, equals the derivation;
- an own agentPlatformOrganizations.operator value fails the meta chart's
  render, naming it; a store role name with no room for -operator in IAM's 64
  characters fails it naming kagent.harness.snapshotStore.crossplane.aws.roleName
  (55 characters fit);
- the connectivity chart with the switch on and without the derived names fails
  its render naming the key, and its templates write none of the operator's
  names as a literal.

Cross-check. The other names the two charts share for the operator belong to
the objects of another chart (Substrate, kagent, the connectivity chart's own
platform objects) or to the operator's API (its labels): the meta chart tells
the operator them and the connectivity chart renders or matches them. For
each shape (both network-policy flavors, the Crossplane AWS store, RustFS,
the connectivity release in another namespace) the meta chart is rendered,
then the connectivity chart with the values that render hands it, and the two
must agree on:
- the operator's namespace: its release's targetNamespace is the connectivity
  release's; the erasure ClusterRoleBinding, the IRSA trust and the admission
  policies name system:serviceaccount:<that namespace>:<serviceAccountName>;
- its pods: the kagent release admits podLabels in that namespace, and the
  connectivity chart's operator network policies select podLabels;
- the RustFS Secret the operator reads (snapshots.credentials.existingSecret)
  renders in its namespace; the IRSA role its ServiceAccount names
  (eks.amazonaws.com/role-arn) is the IAM Role the connectivity chart renders;
- the workers' network policies it copies (networkPolicy.names) are policies
  the connectivity chart renders in the kagent namespace, of its flavor;
- the Secret readers it binds (serviceAccountBindings) are the pairs the
  admission policy admits;
- the ClusterTrustBundles it trusts (erasure.substrate.caClusterTrustBundle)
  are the signer and label the connectivity chart's bootstrap publishes;
- the kagent controller's API port and ate-api-server's port it calls
  (erasure.*.target) are the ports its egress policy opens;
- the agent chart's OCIRepository name (the last segment of agentChart.ociUrl)
  is the one the admission policy admits; commit mode's source, path prefix
  and SOPS key Secret are the ones it holds the Kustomizations to; the Helm
  ServiceAccount (helmReleaseServiceAccount) is the one the HelmRelease lock
  names;
- the organization label kagent's authorizer selects namespaces by is the
  label the connectivity chart's policies select them by.
Each check reads the one object that must match (the named policy, rule or
CEL variable), never a string anywhere in the render. Teeth: on a copy of the
kubernetes shape's connectivity render, a changed ate-api-server egress port,
ingress namespace, swapped Secret-reader roles, a renamed workers' policy,
other operator pod labels and another commit-mode source each fail it.

Needs PyYAML. HELM selects the binary. Network: gsoci.azurecr.io (the operator
chart).

Usage: verify-operator-names.py META_CHART CONNECTIVITY_CHART
"""

import copy
import os
import re
import subprocess
import sys
import tempfile

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import floors  # noqa: E402

HELM = os.environ.get("HELM", "helm")
FIXTURE = os.path.join(HERE, "fixtures", "organizations-operator-values.yaml")
RELEASE_NS = "agent-platform"
MAP_API = ["--api-versions", "admissionregistration.k8s.io/v1/MutatingAdmissionPolicy"]
ON = ["--set", "agentPlatformOrganizations.enabled=true", "--set", "agentPlatformOrganizations.fluxNamespace=flux-giantswarm",
      "--set", "agentPlatformOrganizations.fluxSource.name=giantswarm-config"]
RUSTFS = ["--set", "substrate.rustfs.enabled=true", "--set", "substrate.rustfs.accessKey=proof-access",
          "--set", "substrate.rustfs.secretKey=proof-secret"]


def fail(msg):
    sys.exit(f"FAIL: {msg}")


def ok(msg):
    print(f"ok: {msg}")


def expect(cond, what):
    if not cond:
        fail(what)
    ok(what)


def run(cmd):
    return subprocess.run(cmd, capture_output=True, text=True)


def docs(r, what):
    if r.returncode != 0:
        fail(f"{what}: the render failed:\n{r.stderr}")
    return [d for d in yaml.safe_load_all(r.stdout) if isinstance(d, dict)]


def find(objs, kind, name):
    return next((d for d in objs if d.get("kind") == kind and d["metadata"]["name"] == name), None)


def release(objs, name):
    hr = find(objs, "HelmRelease", name)
    return hr["spec"] if hr else None


def meta(chart, *extra):
    return run([HELM, "template", "t", chart, "-n", RELEASE_NS, "-f", f"{chart}/ci/ci-values.yaml",
                "--set", "kagent.fluxServiceAccountName=kagent-flux", "--set", "llmRouting.enabled=true",
                "--set", "global.domain=ci.example.com", *MAP_API, *extra])


def aws_store(conn):
    """The Crossplane AWS store of the connectivity chart's ci/test-substrate-store-aws-values.yaml (its bucket, no role name)."""
    aws = yaml.safe_load(open(f"{conn}/ci/test-substrate-store-aws-values.yaml"))["kagent"]["harness"]["snapshotStore"]["crossplane"]
    return ["--set", "kagent.harness.snapshotLocation=", "--set", "kagent.harness.snapshotStore.crossplane.enabled=true",
            "--set", "kagent.harness.snapshotStore.crossplane.provider=aws", "--set", "kagent.harness.snapshotStore.crossplane.providerConfigRef=ci",
            "--set", "kagent.harness.snapshotStore.crossplane.region=eu-west-1",
            "--set", f"kagent.harness.snapshotStore.crossplane.aws.bucketName={aws['aws']['bucketName']}",
            "--set-string", "kagent.harness.snapshotStore.crossplane.aws.accountId=123456789012",
            "--set", "kagent.harness.snapshotStore.crossplane.aws.oidcProvider=irsa.ci.example.com"]


def connectivity_of(conn, objs, what):
    """The connectivity chart, rendered with the values the meta render hands its release."""
    hr = release(objs, "agent-platform-connectivity")
    with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as f:
        yaml.safe_dump(hr["values"], f)
    try:
        # The meta chart's CI shape leaves the edge and the platform client to
        # the installation; the connectivity chart refuses to render without them.
        return docs(run([HELM, "template", "agent-platform-connectivity", conn, "-n", hr["targetNamespace"],
                         "-f", f"{conn}/ci/ci-values.yaml", "-f", f.name,
                         "--set", "global.identity.clientId=ci", "--set", "global.identity.existingSecret=ci",
                         "--set", "gatewayApi.gateway.create=true", "--set", "gatewayApi.gateway.tls.secretName=ci",
                         "--api-versions", "kyverno.io/v2/PolicyException", *MAP_API]), f"connectivity ({what})")
    finally:
        os.unlink(f.name)


def operator_chart(meta_chart):
    """The operator chart at the floor of its range: its ServiceAccount's name and its pods' selector labels."""
    values = yaml.safe_load(open(os.path.join(meta_chart, "values.yaml")))
    c = values["components"]["agent-platform-operator"]
    floor = floors.floor("agent-platform-operator", meta_chart)
    objs = docs(run([HELM, "template", "operator", f"{c['repository']}/{c['chart']}", "--version", floor, "-n", RELEASE_NS]),
                f"the operator chart {floor}")
    dep = next(d for d in objs if d.get("kind") == "Deployment")
    sa = next(d for d in objs if d.get("kind") == "ServiceAccount")
    return floor, sa["metadata"]["name"], dep["spec"]["template"]["spec"]["serviceAccountName"], dep["spec"]["selector"]["matchLabels"], dep["spec"]["template"]["metadata"]["labels"]


def derived(objs):
    return (release(objs, "agent-platform-connectivity")["values"].get("agentPlatformOrganizations") or {}).get("operator")


# ---- one source ---------------------------------------------------------------

def check_source(chart, conn):
    off = docs(meta(chart), "meta, off")
    expect(derived(off) is None, "off, the connectivity release carries no agentPlatformOrganizations.operator")

    on = docs(meta(chart, *ON), "meta, on")
    d = derived(on)
    name = yaml.safe_load(open(os.path.join(chart, "values.yaml")))["components"]["agent-platform-operator"]["chart"]
    expect(d == {"serviceAccountName": name, "podLabels": {"app.kubernetes.io/name": name}},
           f"on, the connectivity release carries the operator's ServiceAccount and pod labels, from its chart's name {name} ({d})")
    rustfs = derived(docs(meta(chart, *ON, *RUSTFS), "meta, RustFS"))
    aws = derived(docs(meta(chart, *ON, *aws_store(conn)), "meta, AWS store"))
    expect(rustfs.get("snapshotSecret") and "awsRoleName" not in rustfs, f"with RustFS, snapshotSecret and no awsRoleName ({rustfs})")
    expect(aws.get("awsRoleName") and "snapshotSecret" not in aws, f"with the AWS store, awsRoleName and no snapshotSecret ({aws})")

    fixture = yaml.safe_load(open(FIXTURE))["agentPlatformOrganizations"]["operator"]
    want = {**d, "snapshotSecret": rustfs["snapshotSecret"], "awsRoleName": aws["awsRoleName"]}
    expect(fixture == want, f"{os.path.relpath(FIXTURE)} equals the meta chart's derivation ({want})")

    floor, sa, dep_sa, selector, pod_labels = operator_chart(chart)
    expect(sa == dep_sa == d["serviceAccountName"],
           f"the operator chart {floor} runs as the ServiceAccount the meta chart derives ({sa})")
    expect(selector == {**selector, **d["podLabels"]} and pod_labels == {**pod_labels, **d["podLabels"]},
           f"the operator chart {floor} selects and labels its pods with the derived podLabels ({selector})")

    for key, value in (("serviceAccountName", "mine"), ("podLabels.team", "a")):
        r = meta(chart, *ON, "--set", f"agentPlatformOrganizations.operator.{key}={value}")
        expect(r.returncode != 0 and f"agentPlatformOrganizations.operator.{key.split('.')[0]} is set" in r.stderr,
               f"an own agentPlatformOrganizations.operator.{key} fails the render, naming it")
    r = meta(chart, *ON, *aws_store(conn), "--set", f"kagent.harness.snapshotStore.crossplane.aws.roleName={'r' * 56}")
    expect(r.returncode != 0 and "kagent.harness.snapshotStore.crossplane.aws.roleName" in r.stderr,
           "a store role name with no room for -operator in IAM's 64 characters fails the render, naming the key")
    long = derived(docs(meta(chart, *ON, *aws_store(conn), "--set", f"kagent.harness.snapshotStore.crossplane.aws.roleName={'r' * 55}"), "meta, 55"))
    expect(long["awsRoleName"] == "r" * 55 + "-operator", "a store role name of 55 characters gives <name>-operator")

    base = [HELM, "template", "t", conn, "-n", RELEASE_NS, "-f", f"{conn}/ci/test-substrate-values.yaml",
            "-f", f"{conn}/ci/test-llm-routing-values.yaml", "--set", "agentPlatformOrganizations.enabled=true",
            "--set", "kagent.fluxServiceAccountName=kagent-flux", "--set", "global.domain=ci.example.com", *MAP_API]
    r = run(base)
    expect(r.returncode != 0 and "agentPlatformOrganizations.operator.serviceAccountName is required" in r.stderr,
           "the connectivity chart with the switch on and without the derived names fails, naming the key")
    r = run([*base, "--set", "agentPlatformOrganizations.operator.serviceAccountName=x"])
    expect(r.returncode != 0 and "agentPlatformOrganizations.operator.podLabels is required" in r.stderr,
           "the connectivity chart without the derived pod labels fails, naming the key")
    r = run([*base, "-f", FIXTURE, "-f", f"{conn}/ci/test-substrate-store-aws-values.yaml", "--set", "kagent.harness.snapshotLocation=",
             "--set", "agentPlatformOrganizations.operator.awsRoleName="])
    expect(r.returncode != 0 and "agentPlatformOrganizations.operator.awsRoleName is required" in r.stderr,
           "the connectivity chart with the AWS store and without the derived role fails, naming the key")

    literals = ['"agent-platform-operator"', "name: agent-platform-operator", "substrate-rustfs-operator", '"%s-operator"']
    found = []
    for root, _, files in os.walk(os.path.join(conn, "templates")):
        for f in files:
            text = open(os.path.join(root, f)).read()
            found += [f"{os.path.relpath(os.path.join(root, f), conn)}: {lit}" for lit in literals if lit in text]
    expect(not found, f"the connectivity chart's templates write none of the operator's names ({found})")


# ---- cross-check --------------------------------------------------------------

def policies(objs, namespace):
    return {d["metadata"]["name"] for d in objs
            if d["kind"] in ("NetworkPolicy", "CiliumNetworkPolicy") and d["metadata"].get("namespace") == namespace}


def check_shape(chart, conn, what, extra):
    m = docs(meta(chart, *ON, *extra), f"meta ({what})")
    c = connectivity_of(conn, m, what)
    results = agreement(m, c)
    for good, msg in results:
        expect(good, f"{what}: {msg}")
    return m, c


def flat(obj):
    """An object as YAML on one line, one space between words: the CEL of folded scalars compares as written."""
    return " ".join(yaml.safe_dump(obj, width=1 << 20).split())


def peers(rule, key, flavor):
    """The (namespace, labels) peers and the ports of a network-policy rule, in either flavor."""
    out = set()
    if flavor == "cilium":
        for e in rule.get(f"{key}Endpoints", []):
            labels = dict(e["matchLabels"])
            out.add((labels.pop("io.kubernetes.pod.namespace", None), frozenset(labels.items())))
        ports = {str(p["port"]) for tp in rule.get("toPorts", []) for p in tp.get("ports", [])}
    else:
        for e in rule.get(key, []):
            ns = (e.get("namespaceSelector") or {}).get("matchLabels", {}).get("kubernetes.io/metadata.name")
            if "podSelector" in e:
                out.add((ns, frozenset(e["podSelector"].get("matchLabels", {}).items())))
        ports = {str(p["port"]) for p in rule.get("ports", [])}
    return out, ports


def target(url):
    """grpc(s)://<service>.<namespace>.svc:<port> -> (namespace, port)."""
    host, port = re.match(r"^\w+://([^/:]+):(\d+)$", url).groups()
    return host.split(".")[1], port


def agreement(m, c):
    """(holds, what) for every name the meta render tells the operator and the connectivity render must match."""
    out = []
    op = release(m, "agent-platform-operator")
    ov = op["values"]
    names = derived(m)
    ns = op["targetNamespace"]
    out.append((ns == release(m, "agent-platform-connectivity")["targetNamespace"],
                f"the operator installs into the connectivity release's namespace ({ns})"))
    user = f"system:serviceaccount:{ns}:{names['serviceAccountName']}"
    erasure = find(c, "ClusterRoleBinding", "agent-platform:organization-erasure")
    out.append((bool(erasure) and erasure["subjects"] == [{"kind": "ServiceAccount", "name": names["serviceAccountName"], "namespace": ns}],
                "the erasure binding names the operator's ServiceAccount in its namespace"))
    vaps = {d["metadata"]["name"]: d for d in c if d["kind"] == "ValidatingAdmissionPolicy"}
    for name in ("agent-platform-organization-operator-scope", "agent-platform-organization-operator-objects",
                 "agent-platform-organization-labels", "agent-platform-organization-copies"):
        out.append((name in vaps and f"'{user}'" in flat(vaps[name]), f"{name} names the operator as {user}"))

    peer = {"namespaceSelector": {"matchLabels": {"kubernetes.io/metadata.name": ns}}, "podSelector": {"matchLabels": names["podLabels"]}}
    out.append((peer in release(m, "kagent")["values"]["controller"]["networkPolicy"]["from"],
                "the kagent release admits the operator's pods by podLabels in its namespace"))
    flavor = ov["networkPolicy"]["flavor"] or "kubernetes"
    kind = "CiliumNetworkPolicy" if flavor == "cilium" else "NetworkPolicy"
    egress = find(c, kind, "agent-platform-connectivity-operator-egress")
    selector = (egress or {}).get("spec", {}).get("endpointSelector" if flavor == "cilium" else "podSelector", {}).get("matchLabels")
    out.append((bool(egress) and egress["metadata"]["namespace"] == ns and selector == names["podLabels"],
                "the connectivity chart's operator egress policy selects podLabels in its namespace"))
    operator = (ns, frozenset(names["podLabels"].items()))
    for call, policy, whom in ((ov["erasure"]["kagent"]["target"], "operator-to-kagent-controller", "the kagent controller"),
                               (ov["erasure"]["substrate"]["target"], "operator-to-ate-api", "ate-api-server")):
        callee_ns, port = target(call)
        ingress = find(c, kind, f"agent-platform-connectivity-{policy}")
        callee = None
        admitted = False
        if ingress and ingress["metadata"]["namespace"] == callee_ns:
            callee = (callee_ns, frozenset(ingress["spec"]["endpointSelector" if flavor == "cilium" else "podSelector"]["matchLabels"].items()))
            for rule in ingress["spec"].get("ingress", []):
                p, ports = peers(rule, "from", flavor)
                admitted |= operator in p and ports == {port}
        out.append((admitted, f"{whom}'s ingress in {callee_ns} admits the operator's pods on {port}, where it calls ({call})"))
        opened = any(callee in p and port in ports for p, ports in (peers(r, "to", flavor) for r in (egress or {}).get("spec", {}).get("egress", [])))
        out.append((callee is not None and opened, f"the operator's egress opens {whom} in {callee_ns} on {port}, where it calls"))

    kagent_ns = ov["platformNamespace"]
    copied = ov["networkPolicy"]["names"]
    out.append((bool(copied) and not set(copied) - policies(c, kagent_ns),
                f"the workers' policies the operator copies ({copied}) render in {kagent_ns}"))

    readers = next((v["expression"] for v in vaps.get("agent-platform-organization-operator-objects", {}).get("spec", {}).get("variables", [])
                    if v["name"] == "readers"), "")
    admitted = dict(re.findall(r"'([^']+)':\s*'([^']+)'", readers))
    for b in ov.get("serviceAccountBindings") or []:
        pair = f"{b['serviceAccount']['namespace']}/{b['serviceAccount']['name']}"
        out.append((admitted.get(pair) == b["clusterRole"], f"the admission policy admits the operator's binding {pair} -> {b['clusterRole']}"))

    ctb = ov["erasure"]["substrate"]["caClusterTrustBundle"]
    script = "\n".join(a for d in c if d["kind"] == "Job" and "substrate-bootstrap" in d["metadata"]["name"]
                       for ct in d["spec"]["template"]["spec"]["containers"] for a in ct.get("command", []) + ct.get("args", []))
    labels = "".join(f'"{k}": "{v}"' for k, v in ctb["matchLabels"].items())
    out.append((f" {ctb['signerName']}\n" in script + "\n" and f"labels: {{{labels}}}" in script,
                f"the bootstrap publishes ClusterTrustBundles of {ctb['signerName']} labelled {ctb['matchLabels']}, the ones the operator trusts"))

    helmreleases = flat(vaps.get("agent-platform-organization-helmreleases", {}))
    source = ov["agentChart"]["ociUrl"].rsplit("/", 1)[1]
    out.append((f"variables.chartRef.name == '{source}'" in helmreleases, f"the HelmRelease policy admits the agent chart's OCIRepository {source}"))
    helm_sa = ov["helmReleaseServiceAccount"]
    out.append((f"variables.spec.serviceAccountName == '{helm_sa}'" in helmreleases, f"the HelmRelease policy names the Helm ServiceAccount {helm_sa}"))
    cm = ov.get("commitMode")
    if cm:
        objects = flat(vaps.get("agent-platform-organization-operator-objects", {}))
        prefix = cm["path"].split("{parent}")[0]
        for cel, which in ((f"object.spec.sourceRef.kind == '{cm['sourceRef']['kind']}'", "source kind"),
                           (f"object.spec.sourceRef.name == '{cm['sourceRef']['name']}'", "source name"),
                           (f"object.spec.path == '{prefix}' +", "path prefix"),
                           (f"object.spec.decryption.secretRef.name == '{cm['decryptionSecret']}'", "SOPS key Secret")):
            out.append((cel in objects, f"the admission policy holds the operator's Kustomizations to its {which} ({cel})"))

    label = release(m, "kagent")["values"]["controller"]["authorization"]["namespaceSelector"]
    held = vaps.get("agent-platform-organization-copies", {}).get("spec", {}).get("matchConstraints", {}).get("namespaceSelector", {})
    out.append(({"key": label, "operator": "Exists"} in held.get("matchExpressions", []),
                f"the connectivity chart selects organization namespaces by {label}, kagent's authorizer's label"))

    if names.get("snapshotSecret"):
        secret = (ov.get("snapshots", {}).get("credentials") or {}).get("existingSecret")
        s = find(c, "Secret", secret)
        out.append((secret == names["snapshotSecret"] and bool(s) and s["metadata"]["namespace"] == ns,
                    f"the RustFS Secret the operator reads ({secret}) renders in its namespace"))
    if names.get("awsRoleName"):
        arn = ov.get("serviceAccount", {}).get("annotations", {}).get("eks.amazonaws.com/role-arn") or ""
        role = find(c, "Role", names["awsRoleName"])
        out.append((arn.endswith(f":role/{names['awsRoleName']}") and bool(role) and role["apiVersion"].startswith("iam.aws"),
                    f"the IRSA role the operator's ServiceAccount names ({arn}) is the IAM Role the connectivity chart renders"))
        out.append((bool(role) and f'"{user}"' in role["spec"]["forProvider"]["assumeRolePolicy"], "the IAM role trusts the operator's ServiceAccount"))
    return out


def check_teeth(m, c):
    """Each disagreement, made on a copy of the kubernetes shape's connectivity render, fails the cross-check."""
    def mutated(edit):
        objs = copy.deepcopy(c)
        edit(objs)
        return [msg for good, msg in agreement(m, objs) if not good]

    def ate_egress_port(objs):
        for r in find(objs, "NetworkPolicy", "agent-platform-connectivity-operator-egress")["spec"]["egress"]:
            if any(p.get("podSelector", {}).get("matchLabels") == {"app": "ate-api-server"} for p in r.get("to", [])):
                r["ports"] = [{"port": 8443, "protocol": "TCP"}]

    def ate_ingress_namespace(objs):
        find(objs, "NetworkPolicy", "agent-platform-connectivity-operator-to-ate-api")["metadata"]["namespace"] = "ate-x"

    def swapped_readers(objs):
        for v in find(objs, "ValidatingAdmissionPolicy", "agent-platform-organization-operator-objects")["spec"]["variables"]:
            if v["name"] == "readers":
                roles = re.findall(r":\s*'([^']+)'", v["expression"])
                v["expression"] = re.sub(r":\s*'([^']+)'", lambda mt, it=iter(reversed(roles)): f": '{next(it)}'", v["expression"])

    def renamed_worker_policy(objs):
        next(d for d in objs if d["kind"] == "NetworkPolicy" and d["metadata"]["name"] == "substrate-workers-egress")["metadata"]["name"] = "workers-egress"

    def other_pod_labels(objs):
        find(objs, "NetworkPolicy", "agent-platform-connectivity-operator-egress")["spec"]["podSelector"]["matchLabels"] = {"app": "operator"}

    def other_source(objs):
        for v in find(objs, "ValidatingAdmissionPolicy", "agent-platform-organization-operator-objects")["spec"]["validations"]:
            v["expression"] = v["expression"].replace("'giantswarm-config'", "'other'")

    for what, edit in (("ate-api-server's egress port", ate_egress_port), ("ate-api-server's ingress namespace", ate_ingress_namespace),
                       ("the Secret readers' roles swapped", swapped_readers), ("a worker policy renamed", renamed_worker_policy),
                       ("the operator egress policy's pod labels", other_pod_labels), ("commit mode's source", other_source)):
        failed = mutated(edit)
        expect(failed, f"teeth: {what} changed in the connectivity render fails the cross-check ({failed[:1]})")


def main():
    chart, conn = sys.argv[1:3]
    check_source(chart, conn)
    first = None
    for what, extra in (("kubernetes", []),
                        ("cilium", ["--set", "networkPolicy.flavor=cilium"]),
                        ("AWS store", aws_store(conn)),
                        ("RustFS", RUSTFS),
                        ("connectivity in apo", ["--set", "components.agent-platform-connectivity.targetNamespace=apo"])):
        shape = check_shape(chart, conn, what, extra)
        first = first or shape
    check_teeth(*first)


if __name__ == "__main__":
    main()
