#!/usr/bin/env bash
set -Eeuo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
BOOTSTRAP="$ROOT/deploy/bootstrap.sh"
eval "$(sed -n '/^# XIDER_LEGACY_MIGRATION_HELPERS_BEGIN$/,/^# XIDER_LEGACY_MIGRATION_HELPERS_END$/p' "$BOOTSTRAP" | sed '1d;$d')"
FIXTURE="$(mktemp -d "${TMPDIR:-/tmp}/xider-legacy-location.XXXXXX")"
trap '[[ "$FIXTURE" == "${TMPDIR:-/tmp}"/xider-legacy-location.* ]] && rm -rf -- "$FIXTURE"' EXIT

install_root="$FIXTURE/home/Library/Application Support/XIDER"
legacy_root="$FIXTURE/home/XIDER"
current_backup="$install_root/git-ver.previous.20261104110000.1111"
mkdir -p "$install_root/git-ver/XGENT-MCS" "$current_backup/XGENT-MCS"
mkdir -p "$legacy_root/git-ver/XGENT-MCS" "$legacy_root/git-ver.previous.20261103110000.3333/XGENT-MCS"
mkdir -p "$legacy_root/git-ver.failed.20261103120000.4444/XGENT-MCS"
printf '%s\n' active > "$install_root/git-ver/XGENT-MCS/marker"
printf '%s\n' current-rollback > "$current_backup/XGENT-MCS/marker"
printf '%s\n' old > "$legacy_root/git-ver/XGENT-MCS/marker"
printf '%s\n' previous > "$legacy_root/git-ver.previous.20261103110000.3333/XGENT-MCS/marker"
printf '%s\n' failed > "$legacy_root/git-ver.failed.20261103120000.4444/XGENT-MCS/marker"
mkdir -p "$legacy_root/XGENT-MCS"
printf '%s\n' direct-install > "$legacy_root/XGENT-MCS/xgent_mcs.py"
printf '%s\n' owner-data > "$legacy_root/notes.txt"

preserve_legacy_visible_install "$install_root" "$legacy_root" "$current_backup"

[[ -d "$install_root/git-ver/XGENT-MCS" ]]
[[ -f "$current_backup/XGENT-MCS/marker" ]]
[[ ! -e "$legacy_root/git-ver" ]]
[[ ! -e "$legacy_root/git-ver.previous.20261103110000.3333" ]]
[[ ! -e "$legacy_root/git-ver.failed.20261103120000.4444" ]]
[[ -f "$legacy_root/notes.txt" ]]
[[ -f "$install_root/git-ver.previous.20261104110000.1111/XGENT-MCS/marker" ]]
[[ -f "$install_root/git-ver.previous.20261103110000.3333/XGENT-MCS/marker" ]]
[[ -f "$install_root/git-ver.failed.20261103120000.4444/XGENT-MCS/marker" ]]
[[ ! -e "$legacy_root/XGENT-MCS" ]]
direct_migrated=0
for migrated in "$install_root"/git-ver.previous.*; do
  if [[ -f "$migrated/XGENT-MCS/xgent_mcs.py" ]]; then direct_migrated=1; fi
done
[[ "$direct_migrated" -eq 1 ]]

printf '%s\n' 'macOS legacy install location fixture passed.'
