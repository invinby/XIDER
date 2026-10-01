#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

# The systemd unit supplies these values through a root-owned EnvironmentFile.
readonly source_dir="${XIDER_VAULT_SOURCE:-/opt/xider}"
readonly env_file="${XIDER_VAULT_ENV_FILE:-/etc/xider/bot.env}"
readonly service_file="${XIDER_VAULT_SERVICE_FILE:-/etc/systemd/system/xider-bot.service}"
readonly public_key="${XIDER_VAULT_PUBLIC_KEY:-/etc/xider/vault-recipient-public.pem}"
readonly ssh_key="${XIDER_VAULT_SSH_KEY:-/etc/xider/vault-upload-key}"
readonly known_hosts="${XIDER_VAULT_KNOWN_HOSTS:-/etc/xider/vault-known-hosts}"
readonly target="${XIDER_VAULT_TARGET:-}"
readonly remote_dir="${XIDER_VAULT_REMOTE_DIR:-/incoming}"
readonly max_archive_bytes="${XIDER_VAULT_MAX_ARCHIVE_BYTES:-1073741824}"
readonly lock_file="${XIDER_VAULT_LOCK_FILE:-/run/xider/update.lock}"
readonly runtime_dir="${XIDER_VAULT_RUNTIME_DIR:-/run/xider-vault}"
readonly python="${XIDER_VAULT_PYTHON:-/usr/bin/python3}"
readonly vault_tool="${XIDER_VAULT_TOOL:-/usr/local/libexec/xider/x_vault.py}"
readonly transport="${XIDER_VAULT_TRANSPORT:-/usr/local/libexec/xider/x_vault_transport.py}"

fail() { printf 'X-VAULT backup error: %s\n' "$1" >&2; exit 1; }

[[ "${EUID}" -eq 0 ]] || fail "must run as root"
[[ "${source_dir}" == /* && -d "${source_dir}" && ! -L "${source_dir}" ]] || fail "unsafe or missing source directory"
[[ -f "${env_file}" && ! -L "${env_file}" ]] || fail "missing or unsafe bot env file"
[[ -f "${service_file}" && ! -L "${service_file}" ]] || fail "missing or unsafe service unit"
[[ -f "${public_key}" && ! -L "${public_key}" ]] || fail "missing or unsafe recipient public key"
[[ -f "${ssh_key}" && ! -L "${ssh_key}" ]] || fail "missing or unsafe restricted SSH identity file"
[[ -f "${known_hosts}" && ! -L "${known_hosts}" ]] || fail "missing or unsafe pinned known_hosts file"
[[ -x "${python}" && -f "${vault_tool}" && ! -L "${vault_tool}" \
  && -f "${transport}" && ! -L "${transport}" ]] || fail "X-VAULT runtime is missing"
[[ "${target}" =~ ^[A-Za-z0-9._-]+@[A-Za-z0-9.-]+$ ]] || fail "invalid SSH target"
[[ "${remote_dir}" == "/incoming" ]] || fail "X-VAULT receive directory must be /incoming"
[[ "${max_archive_bytes}" =~ ^[1-9][0-9]{6,12}$ ]] \
  && (( max_archive_bytes >= 1048576 && max_archive_bytes <= 1099511627776 )) \
  || fail "invalid maximum archive size"

ssh_mode="$(stat -c '%a' -- "${ssh_key}")" || fail "cannot inspect upload key permissions"
[[ "$(stat -c '%u' -- "${ssh_key}")" == 0 ]] || fail "upload identity must be root-owned"
if (( (8#${ssh_mode} & 077) != 0 )); then
  fail "upload identity must not be accessible by group or others"
fi
for trusted_file in "${env_file}" "${service_file}" "${public_key}" "${known_hosts}"; do
  [[ "$(stat -c '%u' -- "${trusted_file}")" == 0 ]] || fail "trusted backup input must be root-owned: ${trusted_file}"
  trusted_mode="$(stat -c '%a' -- "${trusted_file}")" || fail "cannot inspect trusted backup input permissions"
  (( (8#${trusted_mode} & 022) == 0 )) || fail "trusted backup input must not be group/world writable: ${trusted_file}"
done
[[ -s "${known_hosts}" ]] || fail "pinned known_hosts file is empty"

install -d -o root -g root -m 0700 "${runtime_dir}"
lock_parent="$(dirname "${lock_file}")"
if [[ "${lock_file}" == "/run/xider/update.lock" ]]; then
  install -d -o root -g root -m 0700 "${lock_parent}"
else
  [[ -d "${lock_parent}" && ! -L "${lock_parent}" ]] || fail "custom lock directory is missing or unsafe"
fi
[[ ! -L "${lock_file}" ]] || fail "update lock path must not be a symlink"
if [[ -e "${lock_file}" && ! -f "${lock_file}" ]]; then
  fail "update lock path must be a regular file"
fi
exec 9>>"${lock_file}"
flock -n 9 || fail "XIDER update/deploy is running; retry on the next timer tick"

archive=""
cleanup() {
  local code=$?
  trap - EXIT
  if [[ -n "${archive}" && "${archive}" == "${runtime_dir}"/xvault-*.xvlt && ! -L "${archive}" ]]; then
    rm -f -- "${archive}"
  fi
  exit "${code}"
}
trap cleanup EXIT

backup_output="$("${python}" "${vault_tool}" backup \
  --source "${source_dir}" \
  --include-file "${env_file}" \
  --include-file "${service_file}" \
  --vault "${runtime_dir}" \
  --recipient-public-key "${public_key}")" || fail "encrypted backup creation failed"
archive="$(printf '%s\n' "${backup_output}" | sed -n 's/^X-VAULT backup created: //p')"
[[ "${archive}" == "${runtime_dir}"/xvault-*.xvlt && -f "${archive}" && ! -L "${archive}" ]] || fail "backup tool returned an unexpected path"
name="${archive##*/}"
[[ "${name}" =~ ^xvault-[0-9]{8}T[0-9]{6}Z-[a-f0-9]{6}\.xvlt$ ]] || fail "backup filename failed validation"
archive_size="$(stat -c '%s' -- "${archive}")" || fail "cannot inspect encrypted archive size"
(( archive_size <= max_archive_bytes )) || fail "encrypted archive exceeds the configured standby size limit"

ssh_options=(
  -T
  -o BatchMode=yes
  -o IdentitiesOnly=yes
  -o RequestTTY=no
  -o ClearAllForwardings=yes
  -o PasswordAuthentication=no
  -o KbdInteractiveAuthentication=no
  -o ConnectTimeout=15
  -o ServerAliveInterval=15
  -o ServerAliveCountMax=3
  -o StrictHostKeyChecking=yes
  -o "UserKnownHostsFile=${known_hosts}"
  -o "IdentityFile=${ssh_key}"
)
if ! "${python}" "${transport}" send "${archive}" | ssh "${ssh_options[@]}" "${target}"; then
  fail "bounded SSH transfer did not complete; incomplete staging is discarded"
fi
printf 'X-VAULT backup transferred: %s\n' "${name}"
