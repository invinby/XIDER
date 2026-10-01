#!/usr/bin/env bash
set -Eeuo pipefail

source_root="$(cd "$(dirname "$0")/../.." && pwd)"
fixture="$(mktemp -d "${TMPDIR:-/tmp}/xider-launch-status.XXXXXX")"
cleanup() {
  rm -f "$fixture/start_agent.sh" "$fixture/start_guardian.sh"
  rmdir "$fixture"
}
trap cleanup EXIT
cp "$source_root/XGENT-MCS/start_agent.sh" "$source_root/XGENT-MCS/start_guardian.sh" "$fixture/"
# A Windows checkout can carry CRLF; CI's Linux checkout normally uses LF.
sed -i 's/\r$//' "$fixture/start_agent.sh" "$fixture/start_guardian.sh"

launchctl() {
  if [[ "${1:-}" == print ]]; then
    printf '%s\n' "${MOCK_LAUNCH_STATE:-}"
    return 0
  fi
  return 1
}
export -f launchctl

for script in start_agent.sh start_guardian.sh; do
  if MOCK_LAUNCH_STATE='state = waiting' bash "$fixture/$script" --status >/dev/null 2>&1; then
    echo "$script falsely reported a waiting LaunchAgent as running" >&2
    exit 1
  fi
  if ! MOCK_LAUNCH_STATE='pid = 12345' bash "$fixture/$script" --status >/dev/null; then
    echo "$script did not recognize a launchd process" >&2
    exit 1
  fi
done
echo 'macOS LaunchAgent status fixture passed.'
