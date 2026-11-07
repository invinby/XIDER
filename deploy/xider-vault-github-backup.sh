#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

readonly source_dir="${XIDER_VAULT_SOURCE:-/opt/xider}"
readonly state_dir="${XIDER_VAULT_STATE_DIR:-/var/lib/xider}"
readonly env_file="${XIDER_VAULT_ENV_FILE:-/etc/xider/x-vault-github-backup.env}"
readonly recipient_key="${XIDER_VAULT_RECIPIENT_PUBLIC_KEY:-/etc/xider/vault-recipient-public.pem}"
readonly deploy_key="${XIDER_VAULT_GITHUB_DEPLOY_KEY:-/etc/xider/github-backup-deploy-key}"
readonly known_hosts="${XIDER_VAULT_GITHUB_KNOWN_HOSTS:-/etc/xider/github-known-hosts}"
readonly unit_dir="${XIDER_VAULT_UNIT_DIR:-/etc/systemd/system}"
readonly nginx_config="${XIDER_VAULT_NGINX_CONFIG:-/etc/nginx/conf.d/xider-dock.conf}"
readonly backup_branch="${XIDER_VAULT_GITHUB_BRANCH:-xider-vault-backups}"
readonly backup_repo="${XIDER_VAULT_GITHUB_REPOSITORY:-invinby/XIDER-PRIVATE-BACKUP}"
readonly runtime_dir="${XIDER_VAULT_RUNTIME_DIR:-/run/xider-vault-github}"
readonly lock_file="${XIDER_VAULT_LOCK_FILE:-/run/xider/update.lock}"
readonly python="${XIDER_VAULT_PYTHON:-/usr/bin/python3}"
readonly vault_tool="${XIDER_VAULT_TOOL:-/opt/xider/ops/x_vault.py}"
readonly retention_count="${XIDER_VAULT_GITHUB_RETENTION_COUNT:-14}"
readonly max_archive_bytes="${XIDER_VAULT_GITHUB_MAX_ARCHIVE_BYTES:-94371840}"

fail() { printf 'X-VAULT GitHub backup error: %s\n' "$1" >&2; exit 1; }

[[ "${EUID}" -eq 0 ]] || fail 'must run as root'
[[ -d "$source_dir" && ! -L "$source_dir" ]] || fail 'source tree is missing or unsafe'
[[ -d "$state_dir" && ! -L "$state_dir" ]] || fail 'runtime state directory is missing or unsafe'
[[ -x "$python" && -f "$vault_tool" && ! -L "$vault_tool" ]] || fail 'X-VAULT runtime is missing'
[[ -f "$recipient_key" && ! -L "$recipient_key" ]] || fail 'offline-recipient public key is missing'
[[ -f "$deploy_key" && ! -L "$deploy_key" ]] || fail 'GitHub deploy identity is missing'
[[ -s "$known_hosts" && ! -L "$known_hosts" ]] || fail 'pinned GitHub host keys are missing'
[[ "$backup_repo" =~ ^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$ ]] || fail 'invalid GitHub repository'
[[ "$backup_branch" =~ ^[A-Za-z0-9._/-]+$ && "$backup_branch" != *..* ]] || fail 'invalid backup branch'
[[ "$retention_count" =~ ^[1-9][0-9]?$ ]] || fail 'invalid retention count'
[[ "$max_archive_bytes" =~ ^[1-9][0-9]{6,9}$ ]] || fail 'invalid archive-size limit'

for trusted in "$recipient_key" "$deploy_key" "$known_hosts" "$env_file"; do
  [[ -f "$trusted" && ! -L "$trusted" ]] || fail "trusted input is missing or unsafe: $trusted"
  [[ "$(stat -c '%u' -- "$trusted")" == 0 ]] || fail "trusted input must be root-owned: $trusted"
  mode="$(stat -c '%a' -- "$trusted")" || fail 'cannot inspect trusted-input permissions'
  (( (8#${mode} & 022) == 0 )) || fail "trusted input must not be group/world writable: $trusted"
done
[[ "$(stat -c '%a' -- "$deploy_key")" == 600 ]] || fail 'GitHub deploy key must be mode 0600'

install -d -o root -g root -m 0700 "$runtime_dir" "$(dirname "$lock_file")"
[[ ! -L "$lock_file" ]] || fail 'shared deployment lock must not be a symlink'
exec 9>>"$lock_file"
flock -n 9 || fail 'deployment/update is running; retry on the next scheduled run'

cleanup() {
  local result=$?
  trap - EXIT
  find "$runtime_dir" -mindepth 1 -maxdepth 1 -exec rm -rf -- {} +
  exit "$result"
}
trap cleanup EXIT

readonly work="$runtime_dir/work"
readonly payload="$runtime_dir/payload"
readonly previous="$runtime_dir/previous"
readonly repo_dir="$runtime_dir/repo"
readonly retained="$runtime_dir/retained"
mkdir -p -m 0700 "$work" "$payload" "$retained"

db_count="$($python - "$state_dir" "$payload/var/lib/xider" <<'PY'
import shutil
import sqlite3
import sys
from pathlib import Path

source_root = Path(sys.argv[1])
target_root = Path(sys.argv[2])
database_suffixes = {".db", ".sqlite", ".sqlite3"}
database_sidecars = ("-wal", "-shm", "-journal")
database_count = 0

for source in sorted(source_root.rglob("*")):
    if source.is_symlink() or not source.is_file():
        continue
    relative = source.relative_to(source_root)
    destination = target_root / relative
    destination.parent.mkdir(parents=True, exist_ok=True)
    if source.name.lower().endswith(database_sidecars):
        continue
    if source.suffix.lower() in database_suffixes:
        source_db = sqlite3.connect(f"file:{source.as_posix()}?mode=ro", uri=True, timeout=30)
        try:
            target_db = sqlite3.connect(destination)
            try:
                source_db.backup(target_db)
                integrity = target_db.execute("PRAGMA integrity_check").fetchone()
                if not integrity or integrity[0] != "ok":
                    raise RuntimeError(f"SQLite online backup failed integrity check: {relative}")
            finally:
                target_db.close()
        finally:
            source_db.close()
        shutil.copystat(source, destination, follow_symlinks=False)
        database_count += 1
    else:
        shutil.copy2(source, destination, follow_symlinks=False)

if database_count == 0:
    raise RuntimeError("No SQLite databases were found under the XIDER state directory")
print(database_count)
PY
)" || fail 'online SQLite snapshot failed'
[[ "$db_count" =~ ^[1-9][0-9]*$ ]] || fail 'SQLite snapshot did not report any databases'

mkdir -p "$payload/etc/xider" "$payload/etc/systemd/system" "$payload/etc/nginx/conf.d"
cp -a /etc/xider/. "$payload/etc/xider/"
rm -f -- \
  "$payload/etc/xider/github-backup-deploy-key" \
  "$payload/etc/xider/github-backup-deploy-key.pub" \
  "$payload/etc/xider/github-known-hosts" \
  "$payload/etc/xider/vault-recipient-public.pem" \
  "$payload/etc/xider/vault-upload-key" \
  "$payload/etc/xider/vault-known-hosts"
for unit in xider-bot.service xider-ws-firewall.service xider-dock-mqtt.service; do
  [[ ! -f "$unit_dir/$unit" ]] || cp -a "$unit_dir/$unit" "$payload/etc/systemd/system/$unit"
  [[ ! -d "$unit_dir/$unit.d" ]] || cp -a "$unit_dir/$unit.d" "$payload/etc/systemd/system/"
done
[[ ! -f "$nginx_config" ]] || cp -a "$nginx_config" "$payload/etc/nginx/conf.d/xider-dock.conf"
tar -C "$runtime_dir" -czf "$work/xider-server-state.tar.gz" payload
chmod 0600 "$work/xider-server-state.tar.gz"

backup_output="$("$python" "$vault_tool" backup \
  --source "$source_dir" \
  --include-file "$work/xider-server-state.tar.gz" \
  --vault "$work" \
  --recipient-public-key "$recipient_key")" || fail 'encrypted X-VAULT archive creation failed'
archive="$(printf '%s\n' "$backup_output" | sed -n 's/^X-VAULT backup created: //p')"
[[ "$archive" == "$work"/xvault-*.xvlt && -f "$archive" && ! -L "$archive" ]] || fail 'X-VAULT returned an unexpected archive path'
archive_size="$(stat -c '%s' -- "$archive")" || fail 'cannot inspect archive size'
(( archive_size <= max_archive_bytes )) || fail "encrypted archive exceeds the GitHub per-file cap (${max_archive_bytes} bytes)"
archive_name="${archive##*/}"

git_ssh_command="ssh -T -o BatchMode=yes -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes -o UserKnownHostsFile=${known_hosts} -o IdentityFile=${deploy_key} -o ConnectTimeout=15 -o ServerAliveInterval=15 -o ServerAliveCountMax=3"
export GIT_SSH_COMMAND="$git_ssh_command"
remote="git@github.com:${backup_repo}.git"
if git ls-remote "$remote" "refs/heads/$backup_branch" >"$work/remote-ref"; then
  if [[ -s "$work/remote-ref" ]]; then
    git clone --quiet --depth 1 --branch "$backup_branch" "$remote" "$previous" || fail 'cannot fetch previous encrypted backups'
    if [[ -d "$previous/backups" ]]; then
      find "$previous/backups" -maxdepth 1 -type f -name 'xvault-*.xvlt' -exec cp -p -- {} "$retained/" \;
    fi
  fi
else
  fail 'cannot read the private backup repository using the scoped deploy key'
fi

cp -- "$archive" "$retained/$archive_name"
mapfile -t archive_names < <(find "$retained" -maxdepth 1 -type f -name 'xvault-*.xvlt' -printf '%f\n' | sort -r)
for ((index=retention_count; index<${#archive_names[@]}; index++)); do
  rm -f -- "$retained/${archive_names[$index]}"
done

git init --quiet --initial-branch="$backup_branch" "$repo_dir"
git -C "$repo_dir" remote add origin "$remote"
mkdir -p "$repo_dir/backups"
cp -p "$retained"/xvault-*.xvlt "$repo_dir/backups/"
sha256="$(sha256sum "$repo_dir/backups/$archive_name" | cut -d' ' -f1)"
cat >"$repo_dir/README.md" <<'EOF'
# XIDER encrypted VPS backups

This private branch contains the rolling encrypted X-VAULT snapshots of the
XIDER VPS. The archive contents include `/opt/xider`, the online SQLite state
snapshot from `/var/lib/xider`, runtime configuration and XIDER service files.
The archive is encrypted to the owner-held X-VAULT recipient key before upload.

Keep `vault-recipient-private.pem` outside GitHub and outside the VPS. Without
that owner-held key the snapshots cannot be decrypted. Each scheduled run
replaces this branch with one root commit and retains the newest 14 archives.
The source repository mirror on the default branch is separate from these
runtime-data snapshots.
EOF
cat >"$repo_dir/backup-latest.json" <<EOF
{
  "archive": "backups/$archive_name",
  "sha256": "$sha256",
  "size_bytes": $archive_size,
  "created_at_utc": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "sqlite_databases_snapshotted": $db_count,
  "encryption": "X-VAULT/2"
}
EOF
git -C "$repo_dir" add README.md backup-latest.json backups
git -C "$repo_dir" -c user.name='XIDER backup service' -c user.email='xider-backup@users.noreply.github.com' \
  commit --quiet -m "backup: encrypted VPS snapshot $(date -u +%Y-%m-%d)" || fail 'cannot create backup commit'
git -C "$repo_dir" push --quiet --force origin "HEAD:refs/heads/$backup_branch" || fail 'encrypted snapshot push to private GitHub failed'

printf 'X-VAULT GitHub backup uploaded: %s (%s bytes, SHA-256 %s); SQLite databases: %s; retained snapshots: %s\n' \
  "$archive_name" "$archive_size" "$sha256" "$db_count" "$(find "$repo_dir/backups" -maxdepth 1 -type f -name 'xvault-*.xvlt' | wc -l | tr -d ' ')"
