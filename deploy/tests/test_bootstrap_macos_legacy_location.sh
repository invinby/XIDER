#!/usr/bin/env bash
set -Eeuo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
BOOTSTRAP="$ROOT/deploy/bootstrap.sh"
eval "$(sed -n '/^# XIDER_LEGACY_MIGRATION_HELPERS_BEGIN$/,/^# XIDER_LEGACY_MIGRATION_HELPERS_END$/p' "$BOOTSTRAP" | sed '1d;$d')"
FIXTURE="$(mktemp -d "${TMPDIR:-/tmp}/svchost-legacy-location.XXXXXX")"
trap '[[ "$FIXTURE" == "${TMPDIR:-/tmp}"/svchost-legacy-location.* ]] && rm -rf -- "$FIXTURE"' EXIT

install_root="$FIXTURE/home/Library/Application Support/SvcHost"
legacy_root="$FIXTURE/home/XIDER"
current_backup="$install_root/git-ver.previous.20261104110000.1111"
mkdir -p "$install_root/git-ver/SvcHostMac" "$current_backup/SvcHostMac"
mkdir -p "$legacy_root/git-ver/SvcHostMac" "$legacy_root/git-ver.previous.20261103110000.3333/SvcHostMac"
mkdir -p "$legacy_root/git-ver.failed.20261103120000.4444/SvcHostMac"
printf '%s\n' active > "$install_root/git-ver/SvcHostMac/marker"
printf '%s\n' current-rollback > "$current_backup/SvcHostMac/marker"
printf '%s\n' old > "$legacy_root/git-ver/SvcHostMac/marker"
printf '%s\n' previous > "$legacy_root/git-ver.previous.20261103110000.3333/SvcHostMac/marker"
printf '%s\n' failed > "$legacy_root/git-ver.failed.20261103120000.4444/SvcHostMac/marker"
mkdir -p "$legacy_root/SvcHostMac"
printf '%s\n' direct-install > "$legacy_root/SvcHostMac/svchost_mac.py"
printf '%s\n' owner-data > "$legacy_root/notes.txt"

preserve_legacy_visible_install "$install_root" "$legacy_root" "$current_backup"

[[ -d "$install_root/git-ver/SvcHostMac" ]]
[[ -f "$current_backup/SvcHostMac/marker" ]]
[[ ! -e "$legacy_root/git-ver" ]]
[[ ! -e "$legacy_root/git-ver.previous.20261103110000.3333" ]]
[[ ! -e "$legacy_root/git-ver.failed.20261103120000.4444" ]]
[[ -f "$legacy_root/notes.txt" ]]
[[ -f "$install_root/git-ver.previous.20261104110000.1111/SvcHostMac/marker" ]]
[[ -f "$install_root/git-ver.previous.20261103110000.3333/SvcHostMac/marker" ]]
[[ -f "$install_root/git-ver.failed.20261103120000.4444/SvcHostMac/marker" ]]
[[ ! -e "$legacy_root/SvcHostMac" ]]
direct_migrated=0
for migrated in "$install_root"/git-ver.previous.*; do
  if [[ -f "$migrated/SvcHostMac/svchost_mac.py" ]]; then direct_migrated=1; fi
done
[[ "$direct_migrated" -eq 1 ]]

printf '%s\n' 'macOS legacy install location fixture passed.'
