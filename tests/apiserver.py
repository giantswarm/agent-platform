"""A local Kubernetes API server for server-side dry runs of rendered objects.

The verify scripts that must know what the API server decides (a CRD's schema,
CEL rules, a ValidatingAdmissionPolicy) start one with `APIServer`: etcd and
kube-apiserver of an envtest release (kubernetes-sigs/controller-tools, the
binaries controller-runtime's envtest runs), downloaded once into .bin/ and
checked against the release index's SHA-512. No controller runs: a deleted
namespace stays Terminating, which is what a policy that reads
namespaceObject.metadata.deletionTimestamp needs. Authorization is RBAC; the
admin token is in system:masters, and `kubectl(..., as_=...)` impersonates a
user or ServiceAccount (with system:masters unless `groups` says otherwise).
The API server applies admission policies, namespaces and RBAC from caches it
fills on its own time: call `settle()` after writing them and before a case
that depends on them.

Needs openssl (the ServiceAccount signing key) and network access to
github.com on the first run. ENVTEST_DIR points at an unpacked envtest release
instead (a directory holding etcd, kube-apiserver and kubectl).
"""
import hashlib
import io
import json
import os
import platform
import shutil
import socket
import subprocess
import tarfile
import tempfile
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENVTEST_VERSION = "v1.37.0"
ENVTEST_URL = "https://github.com/kubernetes-sigs/controller-tools/releases/download/envtest-{v}/envtest-{v}-{p}.tar.gz"
# SHA-512 of each archive, from controller-tools' envtest-releases.yaml.
ENVTEST_SHA512 = {
    "linux-amd64": "1d1c453633b72c161a5d5a886cde7ac850be1a2ac796a9e1d4ffacacc64868295bdd2d57aa66cd0c158f5ce510f5dfe3fbc61ac21bd3dcfb875bd70658aa663a",
    "linux-arm64": "ae6a670502988200b0131c943758cfd3d3a58cf4e6247ef7f0e6a6467f1cd9a333c802e86101f0446b1b92a0e327387224e1e44fe1a4693da0929c6e529cfe9a",
    "darwin-amd64": "19a2a5376a8aa57a7b25ec5198834db29bf2ba0d6ff572d7f45ba683b13fb14c3ebec8069e92db66c39f1c7a9cff1bc2be879e2aacdc9fa8b8a7e861959bdf7b",
    "darwin-arm64": "fb38cfacdd71b5e97a4d4cceac861af5f55069cf783f0e49cf181bfc32eb3e557c2091a534dc5d38f1b92c5ba142bc1979f215a5385b3630ea6a661be6fa161b",
}
ADMIN_TOKEN = "verify-admin"
# APIServer.settle(): the name of its sentinels (and of their namespace), and
# how long it waits for them.
SETTLE_NAME = "verify-settled"
SETTLE_TIMEOUT = 60


def _platform() -> str:
    machine = {"x86_64": "amd64", "aarch64": "arm64"}.get(platform.machine(), platform.machine())
    return f"{platform.system().lower()}-{machine}"


def envtest_dir() -> str:
    """The directory holding etcd, kube-apiserver and kubectl; downloaded once."""
    if os.environ.get("ENVTEST_DIR"):
        return os.environ["ENVTEST_DIR"]
    plat = _platform()
    if plat not in ENVTEST_SHA512:
        raise SystemExit(f"FAIL: no envtest {ENVTEST_VERSION} binaries for {plat}; set ENVTEST_DIR")
    dest = os.path.join(ROOT, ".bin", f"envtest-{ENVTEST_VERSION}-{plat}")
    if os.path.exists(os.path.join(dest, "kube-apiserver")):
        return dest
    url = ENVTEST_URL.format(v=ENVTEST_VERSION, p=plat)
    for attempt in range(3):
        try:
            with urllib.request.urlopen(url, timeout=120) as r:
                data = r.read()
            break
        except OSError as e:
            if attempt == 2:
                raise SystemExit(f"FAIL: download {url}: {e}")
            time.sleep(5)
    digest = hashlib.sha512(data).hexdigest()
    if digest != ENVTEST_SHA512[plat]:
        raise SystemExit(f"FAIL: {url} has SHA-512 {digest}, not the release index's {ENVTEST_SHA512[plat]}")
    tmp = tempfile.mkdtemp(dir=os.path.join(ROOT, ".bin") if os.path.isdir(os.path.join(ROOT, ".bin")) else None)
    with tarfile.open(fileobj=io.BytesIO(data)) as tar:
        for m in tar.getmembers():
            if m.isfile() and os.path.basename(m.name) in ("etcd", "kube-apiserver", "kubectl"):
                m.name = os.path.basename(m.name)
                tar.extract(m, tmp)
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    shutil.rmtree(dest, ignore_errors=True)
    os.rename(tmp, dest)
    return dest


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class APIServer:
    """etcd and kube-apiserver in a temporary directory; stop() removes both."""

    def __init__(self):
        self.bin = envtest_dir()
        self.dir = tempfile.mkdtemp(prefix="verify-apiserver-")
        self.procs = []
        etcd_port, peer_port, api_port = _free_port(), _free_port(), _free_port()
        self.url = f"https://127.0.0.1:{api_port}"
        key = os.path.join(self.dir, "sa.key")
        subprocess.run(["openssl", "genrsa", "-out", key, "2048"], check=True, capture_output=True)
        subprocess.run(["openssl", "rsa", "-in", key, "-pubout", "-out", key + ".pub"], check=True, capture_output=True)
        tokens = os.path.join(self.dir, "tokens.csv")
        with open(tokens, "w") as f:
            f.write(f'{ADMIN_TOKEN},admin,admin,"system:masters"\n')
        self.log = open(os.path.join(self.dir, "apiserver.log"), "w")
        self.procs.append(subprocess.Popen([
            os.path.join(self.bin, "etcd"), "--data-dir", os.path.join(self.dir, "etcd"),
            "--listen-client-urls", f"http://127.0.0.1:{etcd_port}", "--advertise-client-urls", f"http://127.0.0.1:{etcd_port}",
            "--listen-peer-urls", f"http://127.0.0.1:{peer_port}", "--initial-advertise-peer-urls", f"http://127.0.0.1:{peer_port}",
            "--initial-cluster", f"default=http://127.0.0.1:{peer_port}", "--unsafe-no-fsync",
        ], stdout=self.log, stderr=subprocess.STDOUT))
        self.procs.append(subprocess.Popen([
            os.path.join(self.bin, "kube-apiserver"),
            "--etcd-servers", f"http://127.0.0.1:{etcd_port}",
            "--bind-address", "127.0.0.1", "--advertise-address", "127.0.0.1", "--secure-port", str(api_port),
            "--cert-dir", os.path.join(self.dir, "certs"),
            "--token-auth-file", tokens, "--authorization-mode", "RBAC",
            "--service-account-issuer", "https://kubernetes.default.svc",
            "--service-account-key-file", key + ".pub", "--service-account-signing-key-file", key,
            "--service-cluster-ip-range", "10.0.0.0/24", "--disable-admission-plugins", "ServiceAccount",
            "--endpoint-reconciler-type", "none",
        ], stdout=self.log, stderr=subprocess.STDOUT))
        self.kubeconfig = os.path.join(self.dir, "kubeconfig")
        with open(self.kubeconfig, "w") as f:
            f.write(
                "apiVersion: v1\nkind: Config\ncurrent-context: verify\n"
                f"clusters: [{{name: verify, cluster: {{server: '{self.url}', insecure-skip-tls-verify: true}}}}]\n"
                f"users: [{{name: admin, user: {{token: {ADMIN_TOKEN}}}}}]\n"
                "contexts: [{name: verify, context: {cluster: verify, user: admin}}]\n")
        deadline = time.time() + 120
        while time.time() < deadline:
            if any(p.poll() is not None for p in self.procs):
                break
            rc, out = self.kubectl(["get", "--raw", "/readyz"])
            if rc == 0 and out.strip() == "ok":
                # The default namespace and the bootstrap RBAC are written by
                # post-start hooks /readyz already waits for. settle()'s
                # namespace exists before any object of a test: a namespace
                # its cache misses is read live, so settle() changes the
                # label of one the cache has long held.
                rc, out = self.kubectl(["create", "namespace", SETTLE_NAME])
                if rc != 0:
                    self.stop(keep_log=True)
                    raise SystemExit(f"FAIL: create namespace {SETTLE_NAME}:\n{out}")
                return
            time.sleep(1)
        self.stop(keep_log=True)
        raise SystemExit(f"FAIL: the API server did not start; its log: {self.log.name}")

    def kubectl(self, args, stdin=None, as_=None, groups=("system:masters",)):
        cmd = [os.path.join(self.bin, "kubectl"), "--kubeconfig", self.kubeconfig, "--context", "verify"]
        if as_:
            cmd += [f"--as={as_}"] + [f"--as-group={g}" for g in groups]
        r = subprocess.run(cmd + list(args), input=stdin, capture_output=True, text=True)
        return r.returncode, r.stdout + r.stderr

    def settle(self, policies=(), timeout=SETTLE_TIMEOUT):
        """Wait until the API server decides by every object written so far:
        each admission policy and binding, each namespace's labels and
        deletion, and RBAC (giantswarm/agent-platform#1291).

        The API server reads these objects from informer caches, one per kind,
        and each cache sees the writes of its kind in order: the admission
        plugins rebuild their policies from the policy and binding caches
        (k8s.io/apiserver admission/plugin/policy/generic, policy_source.go),
        match namespaces from the namespace cache, and RBAC authorizes from
        the four RBAC caches. So settle() writes one sentinel of each of
        these kinds, after everything else and with a new token, and waits
        until the API server decides by all of them: then every earlier
        object of their kinds is in force too. policies are the
        ValidatingAdmissionPolicies, MutatingAdmissionPolicies and their
        bindings the caller applied: a binding without its policy, or a
        policy without a binding, never loads and fails by name; a
        MutatingAdmissionPolicy among them adds its sentinel, and each
        paramKind of a ValidatingAdmissionPolicy one that waits for the
        informer of its params (those policies are named when it does not
        sync). Fails, naming what it waits for, after timeout seconds."""
        kinds = {"ValidatingAdmissionPolicy": "ValidatingAdmissionPolicyBinding",
                 "MutatingAdmissionPolicy": "MutatingAdmissionPolicyBinding"}
        names = {k: {d["metadata"]["name"] for d in policies if d["kind"] == k} for k in (*kinds, *kinds.values())}
        unbound = []
        for policy, binding in kinds.items():
            bound = {d["spec"]["policyName"] for d in policies if d["kind"] == binding}
            unbound += [f"{binding} {b} (its policy is not applied)" for d in policies if d["kind"] == binding
                        for b in [d["metadata"]["name"]] if d["spec"]["policyName"] not in names[policy]]
            unbound += [f"{policy} {p} (no binding)" for p in sorted(names[policy] - bound)]
        if unbound:
            self._settle_fail(f"these admission policies never load: {'; '.join(unbound)}")
        token = f"t{time.time_ns()}"
        ns, probe, user = SETTLE_NAME, SETTLE_NAME, f"{SETTLE_NAME}-{token}"
        rbac = "rbac.authorization.k8s.io"
        rules = [{"apiGroups": [""], "resources": ["configmaps"], "verbs": ["get"], "resourceNames": [token]}]
        objs = [
            {"apiVersion": "v1", "kind": "Namespace", "metadata": {"name": ns, "labels": {SETTLE_NAME: token}}},
            {"apiVersion": "admissionregistration.k8s.io/v1", "kind": "ValidatingAdmissionPolicy", "metadata": {"name": SETTLE_NAME},
             "spec": {"failurePolicy": "Fail",
                      "matchConstraints": {"namespaceSelector": {"matchLabels": {SETTLE_NAME: token}},
                                           "resourceRules": [{"apiGroups": [""], "apiVersions": ["v1"], "operations": ["CREATE"], "resources": ["configmaps"]}]},
                      "matchConditions": [{"name": "probe", "expression": f"object.metadata.name == '{probe}-v'"}],
                      "validations": [{"expression": "false", "message": f"settled {token}"}]}},
            {"apiVersion": "admissionregistration.k8s.io/v1", "kind": "ValidatingAdmissionPolicyBinding", "metadata": {"name": SETTLE_NAME},
             "spec": {"policyName": SETTLE_NAME, "validationActions": ["Deny"],
                      "matchResources": {"namespaceSelector": {"matchLabels": {SETTLE_NAME: token}}}}},
            {"apiVersion": f"{rbac}/v1", "kind": "Role", "metadata": {"name": SETTLE_NAME, "namespace": ns}, "rules": rules},
            {"apiVersion": f"{rbac}/v1", "kind": "RoleBinding", "metadata": {"name": SETTLE_NAME, "namespace": ns},
             "roleRef": {"apiGroup": rbac, "kind": "Role", "name": SETTLE_NAME},
             "subjects": [{"apiGroup": rbac, "kind": "User", "name": f"{user}-role"}]},
            {"apiVersion": f"{rbac}/v1", "kind": "ClusterRole", "metadata": {"name": SETTLE_NAME}, "rules": rules},
            {"apiVersion": f"{rbac}/v1", "kind": "ClusterRoleBinding", "metadata": {"name": SETTLE_NAME},
             "roleRef": {"apiGroup": rbac, "kind": "ClusterRole", "name": SETTLE_NAME},
             "subjects": [{"apiGroup": rbac, "kind": "User", "name": f"{user}-cluster"}]},
        ]
        mutating = bool(names["MutatingAdmissionPolicy"])
        if mutating:
            objs += [
                {"apiVersion": "admissionregistration.k8s.io/v1", "kind": "MutatingAdmissionPolicy", "metadata": {"name": SETTLE_NAME},
                 "spec": {"failurePolicy": "Fail", "reinvocationPolicy": "Never",
                          "matchConstraints": {"namespaceSelector": {"matchLabels": {SETTLE_NAME: token}},
                                               "resourceRules": [{"apiGroups": [""], "apiVersions": ["v1"], "operations": ["CREATE"], "resources": ["configmaps"]}]},
                          "matchConditions": [{"name": "probe", "expression": f"object.metadata.name == '{probe}-m'"}],
                          "mutations": [{"patchType": "ApplyConfiguration", "applyConfiguration": {
                              "expression": f"Object{{metadata: Object.metadata{{annotations: {{'{SETTLE_NAME}': '{token}'}}}}}}"}}]}},
                {"apiVersion": "admissionregistration.k8s.io/v1", "kind": "MutatingAdmissionPolicyBinding", "metadata": {"name": SETTLE_NAME},
                 "spec": {"policyName": SETTLE_NAME, "matchResources": {"namespaceSelector": {"matchLabels": {SETTLE_NAME: token}}}}},
            ]
        # A policy with a paramKind reads its params from an informer of that
        # kind, which the policy source starts on its own and which refuses
        # ("not yet synced") until it has listed. One sentinel per kind shares
        # it: a binding that selects no param, with parameterNotFoundAction
        # Deny, answers "no params found" only once the informer has synced.
        params = {}
        for d in policies:
            kind = d["kind"] == "ValidatingAdmissionPolicy" and d["spec"].get("paramKind")
            if kind:
                params.setdefault((kind["apiVersion"], kind["kind"]), []).append(d["metadata"]["name"])
        for n, (api_version, kind) in enumerate(sorted(params)):
            name = f"{SETTLE_NAME}-params-{n}"
            objs += [
                {"apiVersion": "admissionregistration.k8s.io/v1", "kind": "ValidatingAdmissionPolicy", "metadata": {"name": name},
                 "spec": {"failurePolicy": "Fail", "paramKind": {"apiVersion": api_version, "kind": kind},
                          "matchConstraints": {"namespaceSelector": {"matchLabels": {SETTLE_NAME: token}},
                                               "resourceRules": [{"apiGroups": [""], "apiVersions": ["v1"], "operations": ["CREATE"], "resources": ["configmaps"]}]},
                          "matchConditions": [{"name": "probe", "expression": f"object.metadata.name == '{probe}-p{n}'"}],
                          "validations": [{"expression": "true"}]}},
                {"apiVersion": "admissionregistration.k8s.io/v1", "kind": "ValidatingAdmissionPolicyBinding", "metadata": {"name": name},
                 "spec": {"policyName": name, "validationActions": ["Deny"],
                          "paramRef": {"selector": {"matchLabels": {SETTLE_NAME: "none"}}, "parameterNotFoundAction": "Deny"},
                          "matchResources": {"namespaceSelector": {"matchLabels": {SETTLE_NAME: token}}}}},
            ]
        for o in objs:
            rc, out = self.kubectl(["apply", "--server-side", "--force-conflicts", "--field-manager", SETTLE_NAME, "-f", "-"], stdin=json.dumps(o))
            if rc != 0:
                self._settle_fail(f"apply the sentinel {o['kind']} {SETTLE_NAME}:\n{out}")

        def configmap(name):
            return json.dumps({"apiVersion": "v1", "kind": "ConfigMap", "metadata": {"name": name, "namespace": ns}})

        def can(as_, namespace):
            return self.kubectl(["auth", "can-i", "get", f"configmaps/{token}", "-n", namespace], as_=as_, groups=())[0] == 0

        waits = {
            "the ValidatingAdmissionPolicies and their bindings, and the namespaces":
                lambda: f"settled {token}" in self.kubectl(["create", "--dry-run=server", "-f", "-"], stdin=configmap(f"{probe}-v"))[1],
            "the Roles and RoleBindings": lambda: can(f"{user}-role", ns),
            "the ClusterRoles and ClusterRoleBindings": lambda: can(f"{user}-cluster", "default"),
        }
        if mutating:
            def mutated():
                rc, out = self.kubectl(["create", "--dry-run=server", "-o", "json", "-f", "-"], stdin=configmap(f"{probe}-m"))
                try:
                    return rc == 0 and (json.loads(out)["metadata"].get("annotations") or {}).get(SETTLE_NAME) == token
                except ValueError:
                    return False
            waits["the MutatingAdmissionPolicies and their bindings"] = mutated

        def no_params(n):
            out = self.kubectl(["create", "--dry-run=server", "-f", "-"], stdin=configmap(f"{probe}-p{n}"))[1]
            return f"{SETTLE_NAME}-params-{n}" in out and "no params found" in out

        for n, key in enumerate(sorted(params)):
            waits[f"the {key[1]} params of {', '.join(sorted(params[key]))}"] = lambda n=n: no_params(n)
        deadline = time.time() + timeout
        while waits and time.time() < deadline:
            waits = {what: ok for what, ok in waits.items() if not ok()}
            if waits:
                time.sleep(0.5)
        if waits:
            self._settle_fail(f"the API server did not decide by {' or by '.join(waits)} within {timeout}s"
                              + (f": one of the {len(policies)} applied policies and bindings is not in force" if policies else ""))

    @staticmethod
    def _settle_fail(msg):
        # The caller's `finally` stops the API server.
        raise SystemExit(f"FAIL: {msg}")

    def stop(self, keep_log=False):
        for p in reversed(self.procs):
            p.terminate()
            try:
                p.wait(timeout=20)
            except subprocess.TimeoutExpired:
                p.kill()
        self.log.close()
        if not keep_log:
            shutil.rmtree(self.dir, ignore_errors=True)
