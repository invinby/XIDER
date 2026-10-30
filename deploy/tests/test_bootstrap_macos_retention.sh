#!/usr/bin/env bash
set -Eeuo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
BOOTSTRAP="$ROOT/deploy/bootstrap.sh"
eval "$(sed -n '/^# XIDER_RETENTION_HELPERS_BEGIN$/,/^# XIDER_RETENTION_HELPERS_END$/p' "$BOOTSTRAP" | sed '1d;$d')"
FIXTURE="$(mktemp -d "${TMPDIR:-/tmp}/xider-retention-test.XXXXXX")"
trap '[[ "$FIXTURE" == "${TMPDIR:-/tmp}"/xider-retention-test.* ]] && rm -rf -- "$FIXTURE"' EXIT

names=(
  git-ver.previous.20261001000000.1111
  git-ver.previous.20261002000000.2222
  git-ver.previous.20261003000000.3333
  git-ver.previous.20261004000000.4444
)
for name in "${names[@]}"; do
  mkdir -p "$FIXTURE/$name/XGENT-MCS"
  printf '%s\n' "$name" > "$FIXTURE/$name/XGENT-MCS/rollback-marker"
done
mkdir -p "$FIXTURE/git-ver.previous.owner-data"
printf '%s\n' owner-data > "$FIXTURE/git-ver.previous.owner-data/keep"

prune_previous_checkouts "$FIXTURE" "$FIXTURE/${names[3]}"

[[ -d "$FIXTURE/${names[3]}" ]]
[[ -d "$FIXTURE/${names[2]}" ]]
[[ ! -e "$FIXTURE/${names[0]}" && ! -e "$FIXTURE/${names[1]}" ]]
[[ -f "$FIXTURE/git-ver.previous.owner-data/keep" ]]
printf '%s\n' 'macOS checkout retention helper passed.'
