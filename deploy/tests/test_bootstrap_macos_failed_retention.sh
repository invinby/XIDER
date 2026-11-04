#!/usr/bin/env bash
set -Eeuo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
BOOTSTRAP="$ROOT/deploy/bootstrap.sh"
eval "$(sed -n '/^# XIDER_FAILED_RETENTION_HELPERS_BEGIN$/,/^# XIDER_FAILED_RETENTION_HELPERS_END$/p' "$BOOTSTRAP" | sed '1d;$d')"
FIXTURE="$(mktemp -d "${TMPDIR:-/tmp}/xider-failed-retention-test.XXXXXX")"
trap '[[ "$FIXTURE" == "${TMPDIR:-/tmp}"/xider-failed-retention-test.* ]] && rm -rf -- "$FIXTURE"' EXIT

mkdir -p \
  "$FIXTURE/git-ver.failed.20261001000000/XGENT-MCS" \
  "$FIXTURE/git-ver.failed.20261002000000.2345/XGENT-MCS" \
  "$FIXTURE/git-ver.failed.20261003000000/XGENT-MCS" \
  "$FIXTURE/git-ver.failed.owner-data"
printf '%s\n' keep > "$FIXTURE/git-ver.failed.owner-data/keep"
printf '%s\n' data > "$FIXTURE/target"
ln -s "$FIXTURE/target" "$FIXTURE/git-ver.failed.20261002000000.2345/XGENT-MCS/external-link"
has_real_symlink=0
[[ -L "$FIXTURE/git-ver.failed.20261002000000.2345/XGENT-MCS/external-link" ]] && has_real_symlink=1 || true

prune_failed_checkouts "$FIXTURE" 1

[[ -d "$FIXTURE/git-ver.failed.20261003000000" ]]
[[ ! -e "$FIXTURE/git-ver.failed.20261001000000" ]]
# On filesystems supporting symlinks, the containing failed directory is retained.
# Git Bash may emulate `ln -s` as a regular file when Windows symlink creation is disabled.
if ((has_real_symlink)); then
  [[ -L "$FIXTURE/git-ver.failed.20261002000000.2345/XGENT-MCS/external-link" ]]
else
  [[ ! -e "$FIXTURE/git-ver.failed.20261002000000.2345" ]]
fi
[[ -f "$FIXTURE/git-ver.failed.owner-data/keep" ]]
printf '%s\n' 'macOS failed-checkout retention helper passed.'
