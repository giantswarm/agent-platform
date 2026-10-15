# The ModelConfigs of the kagent namespace, reconciled to llmRouting: one merge
# patch per ModelConfig that needs one, as {name, patch} lines (jq -c).
#
# Input: kubectl get modelconfigs -o json. Arguments:
#   $mode    on | off: llmRouting.enabled
#   $served  JSON list of the lower-cased providers the listener serves
#            (agent-platform.llmRouting.providers); empty when off
#   $origin  the listener origin (agent-platform.llmBaseUrl); an OpenAI
#            ModelConfig reaches it at $origin/v1
#   $mark    the annotation that records the baseUrl the chart wrote
#
# A baseUrl belongs to the chart when it equals the mark, or, unmarked, is the
# listener URL on an object Helm does not manage: what the
# modelconfig-llm-routing policies wrote before they marked it. Every other
# baseUrl belongs to the operator (a Helm-managed one is a release value) and
# is never touched.
#
# Off, or a provider the listener no longer serves: a chart baseUrl is removed
# with its mark, so the ModelConfig calls its provider directly again. On: a
# chart baseUrl becomes the current listener URL, marked. A ModelConfig without
# a baseUrl stays as it is: the policy routes it at its next write, and the
# cutover of the default ModelConfig stays the second step of the README.
#
# No single quote anywhere in this file: the hook passes it to jq inside one.

def urlKey: {"anthropic": "anthropic", "mistral": "mistral", "openai": "openAI", "sapaicore": "sapAICore"}[ascii_downcase];
def listenerUrl: $origin + (if ascii_downcase == "openai" then "/v1" else "" end);

.items[]
| . as $mc
| (.spec.provider // "") as $provider
| ($provider | urlKey) as $key
| select($key != null)
| (.spec[$key].baseUrl // null) as $current
| ((.metadata.annotations // {})[$mark] // null) as $marked
| (((.metadata.labels // {})["app.kubernetes.io/managed-by"] // "") == "Helm") as $helm
| ($current != null and ($current == $marked or ($marked == null and ($helm | not) and ($current == $origin or $current == $origin + "/v1")))) as $chart
| (if $mode == "on" and any($served[]; . == ($provider | ascii_downcase)) then ($provider | listenerUrl) else null end) as $want
| select($chart and ($want != $current or $marked == null))
| $want
| {name: $mc.metadata.name, patch: {metadata: {resourceVersion: $mc.metadata.resourceVersion, annotations: {($mark): .}}, spec: {($key): {baseUrl: .}}}}
