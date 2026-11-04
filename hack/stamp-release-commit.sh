#!/usr/bin/env bash
# Stamp a release's commit into the chart: every `[[ .SHA ]]` placeholder of the
# agents' values files (helm/agent-platform/agents/) becomes the full commit id
# the agent chart pins the platform's skills to (it refuses a tag). Run by the
# tag pipeline (.circleci/custom.yml, stamp-release-commit) before the chart is
# packaged; make verify-agent-manager-assistant runs it on a copy.
#
# usage: hack/stamp-release-commit.sh [<commit>] [<chart dir>]
#   commit     40-hex commit id; default: git rev-parse HEAD
#   chart dir  default: helm/agent-platform
set -euo pipefail

commit=${1:-$(git rev-parse HEAD)}
chart=${2:-helm/agent-platform}
placeholder='[[ .SHA ]]'

if ! [[ $commit =~ ^[0-9a-f]{40}$ ]]; then
  echo "stamp-release-commit: '$commit' is not a full 40-hex commit id" >&2
  exit 1
fi

files=$(grep -rlF "$placeholder" "$chart/agents" || true)
if [ -z "$files" ]; then
  echo "stamp-release-commit: no '$placeholder' under $chart/agents: nothing to stamp" >&2
  exit 1
fi
for f in $files; do
  # The placeholder holds no regex metacharacter sed would read once escaped.
  sed -i.bak "s/\[\[ \.SHA \]\]/$commit/g" "$f" && rm -f "$f.bak"
  echo "stamped $commit into $f"
done
if grep -rqF "$placeholder" "$chart/agents"; then
  echo "stamp-release-commit: a placeholder is left under $chart/agents" >&2
  exit 1
fi
