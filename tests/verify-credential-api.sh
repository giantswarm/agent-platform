#!/usr/bin/env bash
set -euo pipefail

chart_directory=${CHART_DIR:-helm/agent-platform}
helm_binary=${HELM:-helm}
test_directory=$(mktemp -d "${TMPDIR:-$HOME/.cache/tmp}/credential-api-chart.XXXXXX")
trap 'rm -rf "$test_directory"' EXIT

"$helm_binary" template credential-test "$chart_directory" --kube-context credential-api-validation \
  -f "$chart_directory/ci/ci-values.yaml" --set components.flux.enabled=false > "$test_directory/default.yaml"
if grep -Eq '^  name: credential-api$' "$test_directory/default.yaml"; then
  echo 'credential-api must remain disabled by default' >&2
  exit 1
fi
grep -Eq 'encryptionKey: true' "$test_directory/default.yaml"

"$helm_binary" template credential-test "$chart_directory" --kube-context credential-api-validation \
  -f "$chart_directory/ci/ci-values.yaml" --set components.flux.enabled=false \
  --set muster.replicaCount=1 --set muster.muster.oauth.server.encryptionKey=false > "$test_directory/single.yaml"
grep -Eq 'encryptionKey: false' "$test_directory/single.yaml"

"$helm_binary" template credential-test "$chart_directory" --kube-context credential-api-validation \
  -f "$chart_directory/ci/ci-values.yaml" --set components.flux.enabled=false \
  --set components.credential-api.enabled=true --set global.domain=platform.example.test \
  --set global.identity.issuerUrl=https://dex.example.test --set credential-api.installation=example \
  --set credential-api.portalOrigin=https://backstage.platform.example.test > "$test_directory/enabled.yaml"

grep -Eq '^  name: credential-api$' "$test_directory/enabled.yaml"
grep -Eq 'targetNamespace: agent-platform-credential-api' "$test_directory/enabled.yaml"
grep -Eq 'credentialOrigin: https://credentials.platform.example.test' "$test_directory/enabled.yaml"
grep -Eq 'issuer: https://dex.example.test' "$test_directory/enabled.yaml"
grep -Eq 'clientId: credential-api' "$test_directory/enabled.yaml"
grep -Eq 'kubernetesAudience: dex-k8s-authenticator' "$test_directory/enabled.yaml"
grep -Eq 'semver: ">=0.2.0 <0.3.0-0"' "$test_directory/enabled.yaml"
grep -Eq 'encryptionKey: true' "$test_directory/enabled.yaml"
grep -Eq 'app.kubernetes.io/name: credential-api' "$test_directory/enabled.yaml"
grep -Eq 'namespace: agent-platform-credential-api' "$test_directory/enabled.yaml"

for flavor in cilium kubernetes; do
  "$helm_binary" template credential-test helm/agent-platform-connectivity --kube-context credential-api-validation \
    -f helm/agent-platform-connectivity/ci/ci-values.yaml --set components.credential-api.enabled=true \
    --set credentialApi.namespace=credential-isolated --set networkPolicy.flavor="$flavor" \
    --set networkPolicy.kubernetes.apiServerCIDR=10.0.0.1/32 > "$test_directory/network-$flavor.yaml"
  grep -Eq 'app.kubernetes.io/name: credential-api' "$test_directory/network-$flavor.yaml"
  grep -Eq 'credential-isolated' "$test_directory/network-$flavor.yaml"
  "$helm_binary" template credential-test helm/agent-platform-connectivity --kube-context credential-api-validation \
    -f helm/agent-platform-connectivity/ci/ci-values.yaml --set components.credential-api.enabled=false \
    --set networkPolicy.flavor="$flavor" --set networkPolicy.kubernetes.apiServerCIDR=10.0.0.1/32 > "$test_directory/network-off-$flavor.yaml"
  if grep -Eq 'app.kubernetes.io/name: credential-api' "$test_directory/network-off-$flavor.yaml"; then
    echo 'credential-api network permissions must be absent while disabled' >&2
    exit 1
  fi
done
echo 'credential-api: opt-in component, dedicated namespace, host and identity derivation verified'
