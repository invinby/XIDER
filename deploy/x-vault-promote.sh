#!/usr/bin/env bash
set -Eeuo pipefail

readonly helper="${XIDER_VAULT_PROMOTION_HELPER:-/usr/local/libexec/xider/promote_x_vault.py}"
readonly upload_lock="${XIDER_VAULT_UPLOAD_LOCK:-/run/xider-vault-upload.lock}"
[[ "${EUID}" -eq 0 ]] || { printf 'X-VAULT standby error: must run as root\n' >&2; exit 1; }
[[ -f "${helper}" && ! -L "${helper}" ]] || {
  printf 'X-VAULT standby error: promotion helper is missing or unsafe\n' >&2
  exit 1
}
[[ -f "${upload_lock}" && ! -L "${upload_lock}" \
  && "$(stat -c '%u:%a' -- "${upload_lock}")" == "0:660" ]] || {
  printf 'X-VAULT standby error: shared upload lock is missing or unsafe\n' >&2
  exit 1
}
exec 9>>"${upload_lock}"
if ! flock -n 9; then
  printf 'X-VAULT upload is active; skipping promotion this timer tick.\n'
  exit 0
fi
exec /usr/bin/python3 "${helper}"
