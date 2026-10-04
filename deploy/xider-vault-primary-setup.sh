#!/usr/bin/env bash
set -Eeuo pipefail

preflight_only=0
if [[ "${1:-}" == --preflight-only ]]; then
  preflight_only=1
  shift
fi
[[ "$#" -eq 0 ]] || { printf 'Usage: %s [--preflight-only]\n' "$0" >&2; exit 1; }

readonly app_dir="${XIDER_APP_DIR:-/opt/xider}"
readonly config="/etc/xider/x-vault-backup.env"
readonly upload_key="/etc/xider/vault-upload-key"
readonly known_hosts="/etc/xider/vault-known-hosts"
readonly recipient_key="/etc/xider/vault-recipient-public.pem"
readonly helper_dir="/usr/local/libexec/xider"
readonly unit_dir="/etc/systemd/system"

fail() { printf 'X-VAULT setup error: %s\n' "$1" >&2; exit 1; }

[[ "${EUID}" -eq 0 ]] || fail "run as root"
[[ -d "${app_dir}" && ! -L "${app_dir}" ]] || fail "XIDER source tree is missing"
for file in \
  "${app_dir}/ops/x_vault.py" \
  "${app_dir}/ops/x_vault_transport.py" \
  "${app_dir}/deploy/x-vault-backup.sh" \
  "${app_dir}/deploy/xider-vault-backup.service" \
  "${app_dir}/deploy/xider-vault-backup.timer" \
  "${config}" "${upload_key}" "${known_hosts}" "${recipient_key}"; do
  [[ -f "${file}" && ! -L "${file}" ]] || fail "required file missing or unsafe: ${file}"
done

for file in "${config}" "${upload_key}"; do
  owner="$(stat -c '%u' -- "${file}")"
  mode="$(stat -c '%a' -- "${file}")"
  [[ "${owner}" == 0 ]] || fail "${file} must be root-owned"
  (( (8#${mode} & 077) == 0 )) || fail "${file} must not be accessible by group or others"
done
for file in "${known_hosts}" "${recipient_key}"; do
  owner="$(stat -c '%u' -- "${file}")"
  mode="$(stat -c '%a' -- "${file}")"
  [[ "${owner}" == 0 ]] || fail "${file} must be root-owned"
  (( (8#${mode} & 022) == 0 )) || fail "${file} must not be writable by group or others"
done
[[ -s "${known_hosts}" ]] || fail "pinned known_hosts file is empty"
[[ "$(grep -c '^XIDER_VAULT_TARGET=' "${config}")" == 1 ]] || fail "set XIDER_VAULT_TARGET exactly once in ${config}"
[[ "$(grep -c '^XIDER_VAULT_REMOTE_DIR=' "${config}")" == 1 ]] || fail "set XIDER_VAULT_REMOTE_DIR exactly once in ${config}"
grep -Eq '^XIDER_VAULT_TARGET=[A-Za-z0-9._-]+@[A-Za-z0-9.-]+$' "${config}" || fail "invalid XIDER_VAULT_TARGET in ${config}"
grep -Eq '^XIDER_VAULT_TARGET=xvault-upload@[A-Za-z0-9.-]+$' "${config}" || fail "XIDER_VAULT_TARGET must use the restricted xvault-upload account"
grep -qx 'XIDER_VAULT_REMOTE_DIR=/incoming' "${config}" || fail "XIDER_VAULT_REMOTE_DIR must be /incoming"
grep -q '^-----BEGIN PUBLIC KEY-----' "${recipient_key}" || fail "recipient public key is not PEM"
"/usr/bin/python3" -c 'from cryptography.hazmat.primitives import serialization; from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PublicKey; import pathlib,sys; key=serialization.load_pem_public_key(pathlib.Path(sys.argv[1]).read_bytes()); sys.exit(0 if isinstance(key,X25519PublicKey) else 1)' "${recipient_key}" \
  || fail "recipient key must be a valid X25519 public key"

if [[ "${preflight_only}" == 1 ]]; then
  printf 'X-VAULT primary local preflight passed; standby key permissions and SSH authentication are not verified.\n'
  exit 0
fi

install -d -o root -g root -m 0750 "${helper_dir}"
install -o root -g root -m 0750 "${app_dir}/deploy/x-vault-backup.sh" "${helper_dir}/x-vault-backup.sh"
install -o root -g root -m 0750 "${app_dir}/ops/x_vault.py" "${helper_dir}/x_vault.py"
install -o root -g root -m 0750 "${app_dir}/ops/x_vault_transport.py" "${helper_dir}/x_vault_transport.py"
install -o root -g root -m 0644 "${app_dir}/deploy/xider-vault-backup.service" "${unit_dir}/xider-vault-backup.service"
install -o root -g root -m 0644 "${app_dir}/deploy/xider-vault-backup.timer" "${unit_dir}/xider-vault-backup.timer"
systemd-analyze verify "${unit_dir}/xider-vault-backup.service" "${unit_dir}/xider-vault-backup.timer"
systemctl daemon-reload
systemctl enable --now xider-vault-backup.timer
printf 'X-VAULT primary timer enabled. First run and health are not implied; inspect with systemctl status xider-vault-backup.timer.\n'
