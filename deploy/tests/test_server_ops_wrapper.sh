#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
TEMP_ROOT="$(mktemp -d)"
trap 'rm -rf -- "$TEMP_ROOT"' EXIT

# Replace only executable boundaries; the wrapper's real argument checks run.
sed \
  -e 's#/bin/systemctl#/usr/bin/printf#g' \
  -e 's#/usr/bin/journalctl#/usr/bin/printf#g' \
  -e 's#/bin/bash#/usr/bin/printf#g' \
  "${ROOT}/deploy/xider-server-ops.sh" > "${TEMP_ROOT}/xider-server-ops.sh"

for args in 'status extra' 'rollback /tmp/arbitrary.zip' 'restart other.service' 'bogus'; do
  # Split only these fixed test cases; none contains shell metacharacters.
  read -r -a argv <<< "${args}"
  set +e
  output="$(bash "${TEMP_ROOT}/xider-server-ops.sh" "${argv[@]}" 2>&1)"
  status=$?
  set -e
  if [[ "${status}" -ne 2 || "${output}" != *"Usage:"* ]]; then
    echo "wrapper accepted invalid arguments: ${args}; exit=${status}; output=${output}" >&2
    exit 1
  fi
done

echo "Server operations wrapper rejects extra or unknown arguments."
