#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

preflight_only=0
if [[ "${1:-}" == --preflight-only ]]; then
  preflight_only=1
  shift
fi
[[ "$#" -eq 1 ]] || { printf 'Usage: %s [--preflight-only] /path/to/upload-key.pub\n' "$0" >&2; exit 1; }

readonly public_key_file="${1:-}"
readonly vault_root="/srv/xider-vault"
readonly incoming="${vault_root}/incoming"
readonly archive_dir="${vault_root}/archives"
readonly account="xvault-upload"
readonly authorized_dir="/etc/ssh/authorized_keys"
readonly authorized_file="${authorized_dir}/${account}"
readonly sshd_fragment="/etc/ssh/sshd_config.d/90-xider-vault-upload.conf"
readonly helper="/usr/local/sbin/xider-vault-promote"
readonly helper_dir="/usr/local/libexec/xider"
readonly env_file="/etc/xider/x-vault-standby.env"
readonly upload_helper_parent="/usr/local/libexec"
readonly upload_helper_dir="/usr/local/libexec/xider-vault-upload"
readonly upload_transport="${upload_helper_dir}/x_vault_transport.py"
readonly upload_lock="/run/xider-vault-upload.lock"
readonly tmpfiles_rule="/etc/tmpfiles.d/xider-vault-upload.conf"

fail() { printf 'X-VAULT standby setup error: %s\n' "$1" >&2; exit 1; }

ensure_directory() {
  local path="$1" owner="$2" group="$3" mode="$4"
  if [[ -L "${path}" || ( -e "${path}" && ! -d "${path}" ) ]]; then
    fail "directory path is unsafe: ${path}"
  fi
  if [[ ! -e "${path}" ]]; then
    [[ "${preflight_only}" == 0 ]] || fail "required directory is missing: ${path}"
    install -d -o "${owner}" -g "${group}" -m "${mode}" "${path}"
  fi
  [[ "$(stat -c '%U:%G' -- "${path}")" == "${owner}:${group}" \
    && "$(stat -c '%a' -- "${path}")" == "${mode}" ]] \
    || fail "existing directory ownership/mode differs; refusing to take it over: ${path}"
}

# sshd opens AuthorizedKeysFile after switching to the authenticating user.
# Root ownership protects the key, but root:root 0600 makes it unreadable there.
verify_authorized_key() {
  local path="$1" user="$2" expected_line="$3" group_id
  [[ -f "${path}" && ! -L "${path}" ]] || fail "authorized key file is missing or unsafe: ${path}"
  group_id="$(id -g "${user}")" || fail "cannot inspect uploader group"
  [[ "$(stat -c '%u:%g:%a' -- "${path}")" == "0:${group_id}:640" ]] \
    || fail "uploader authorized_keys must be root:${user} mode 0640"
  cmp -s <(printf '%s\n' "${expected_line}") "${path}" \
    || fail "uploader key already exists and differs; refusing to rotate it implicitly"
  command -v runuser >/dev/null || fail "runuser is required to verify SSH key access"
  runuser -u "${user}" -- /bin/sh -c \
    'test -r "$1" && test ! -w "$1" && dd if="$1" of=/dev/null bs=4096 count=1 status=none' \
    sh "${path}" || fail "uploader must be able to read, but not write, authorized_keys; inspect path permissions and ACLs"
}

configure_authorized_key() {
  local path="$1" user="$2" expected_line="$3" current_policy group_id
  [[ ! -L "${path}" ]] || fail "authorized key path is a symlink"
  group_id="$(id -g "${user}")" || fail "cannot inspect uploader group"
  if [[ -e "${path}" ]]; then
    [[ -f "${path}" ]] || fail "authorized key path is not a regular file"
    cmp -s <(printf '%s\n' "${expected_line}") "${path}" \
      || fail "uploader key already exists and differs; refusing to rotate it implicitly"
    current_policy="$(stat -c '%u:%g:%a' -- "${path}")"
    if [[ "${current_policy}" == 0:0:600 && "${preflight_only}" == 0 ]]; then
      # Migrate only the old, root-protected, byte-identical key. Never adopt a
      # user-owned key or silently rotate an existing authorization.
      chown "root:${user}" "${path}"
      chmod 0640 "${path}"
    else
      [[ "${current_policy}" == "0:${group_id}:640" ]] \
        || fail "uploader authorized_keys must be root:${user} mode 0640"
    fi
  else
    [[ "${preflight_only}" == 0 ]] || fail "authorized key file is missing: ${path}"
    install -o root -g "${user}" -m 0640 /dev/null "${path}"
    printf '%s\n' "${expected_line}" >"${path}"
  fi
  verify_authorized_key "${path}" "${user}" "${expected_line}"
}

verify_receiver_access() {
  [[ -f "${upload_transport}" && ! -L "${upload_transport}" \
    && "$(stat -c '%u:%G:%a' -- "${upload_transport}")" == "0:${account}:550" ]] \
    || fail "bounded receiver must be installed root:${account} mode 0550"
  runuser -u "${account}" -- /bin/sh -c \
    'test -r "$1" && test ! -w "$1" && dd if="$1" of=/dev/null bs=4096 count=1 status=none' \
    sh "${upload_transport}" || fail "uploader must be able to read, but not write, the bounded receiver; inspect path permissions and ACLs"
}

# Setup entrypoint; permission helpers above are exercised without live services.
[[ "${EUID}" -eq 0 ]] || fail "run as root"
[[ -n "${public_key_file}" && -f "${public_key_file}" && ! -L "${public_key_file}" ]] || fail "pass a regular SSH public-key file"
[[ "$(head -n1 "${public_key_file}")" =~ ^ssh-ed25519[[:space:]]+[A-Za-z0-9+/]+={0,3}([[:space:]].*)?$ ]] || fail "upload key must be ssh-ed25519"
ssh-keygen -lf "${public_key_file}" >/dev/null || fail "invalid SSH public key"

readonly app_dir="${XIDER_APP_DIR:-/opt/xider}"
for file in \
  "${app_dir}/deploy/x-vault-promote.sh" \
  "${app_dir}/deploy/promote_x_vault.py" \
  "${app_dir}/ops/x_vault_transport.py" \
  "${app_dir}/deploy/xider-vault-promote.service" \
  "${app_dir}/deploy/xider-vault-promote.timer" \
  "${app_dir}/deploy/xider-vault-upload.sshd.conf"; do
  [[ -f "${file}" && ! -L "${file}" ]] || fail "required file missing or unsafe: ${file}"
done

if ! getent group "${account}" >/dev/null; then
  [[ "${preflight_only}" == 0 ]] || fail "restricted uploader group is missing"
  groupadd --system "${account}"
fi
if ! id -u "${account}" >/dev/null 2>&1; then
  [[ "${preflight_only}" == 0 ]] || fail "restricted uploader account is missing"
  useradd --system --gid "${account}" --home-dir /incoming --no-create-home \
    --shell /bin/sh "${account}"
else
  shell="$(getent passwd "${account}" | cut -d: -f7)"
  primary_group="$(id -gn "${account}")"
  [[ "${shell}" == /bin/sh && "${primary_group}" == "${account}" ]] \
    || fail "existing ${account} account does not match the restricted uploader role"
fi

ensure_directory "${vault_root}" root root 755
ensure_directory "${incoming}" "${account}" "${account}" 700
ensure_directory "${archive_dir}" root root 700
ensure_directory "${upload_helper_parent}" root root 755
ensure_directory "${upload_helper_dir}" root "${account}" 750

[[ ! -L "${upload_lock}" ]] || fail "X-VAULT upload lock is a symlink"
if [[ ! -e "${upload_lock}" ]]; then
  [[ "${preflight_only}" == 0 ]] || fail "X-VAULT upload lock is missing"
  install -o root -g "${account}" -m 0660 /dev/null "${upload_lock}"
fi
[[ -f "${upload_lock}" \
  && "$(stat -c '%u:%G:%a' -- "${upload_lock}")" == "0:${account}:660" ]] \
  || fail "X-VAULT upload lock must be root-owned by group ${account}, mode 0660"

tmpfiles_line="f ${upload_lock} 0660 root ${account} -"
[[ ! -L "${tmpfiles_rule}" ]] || fail "X-VAULT tmpfiles rule is a symlink"
if [[ -e "${tmpfiles_rule}" ]]; then
  [[ -f "${tmpfiles_rule}" && "$(stat -c '%u:%a' -- "${tmpfiles_rule}")" == "0:644" ]] \
    && grep -Fxq "${tmpfiles_line}" "${tmpfiles_rule}" \
    || fail "existing X-VAULT tmpfiles rule differs; inspect it before replacing"
else
  [[ "${preflight_only}" == 0 ]] || fail "X-VAULT tmpfiles rule is missing"
  printf '%s\n' "${tmpfiles_line}" >"${tmpfiles_rule}"
  chown root:root "${tmpfiles_rule}"
  chmod 0644 "${tmpfiles_rule}"
fi
if [[ "${preflight_only}" == 0 ]]; then
  systemd-tmpfiles --create "${tmpfiles_rule}"
fi

ensure_directory "${authorized_dir}" root root 755
authorized_key_line="$(printf 'restrict,command="/usr/bin/python3 %s receive" %s' \
  "${upload_transport}" \
  "$(awk 'NR==1 {print $1, $2, "xider-vault-upload"}' "${public_key_file}")")"
configure_authorized_key "${authorized_file}" "${account}" "${authorized_key_line}"

[[ ! -L "${sshd_fragment}" ]] || fail "sshd fragment path is a symlink"
if [[ -e "${sshd_fragment}" ]]; then
  cmp -s "${app_dir}/deploy/xider-vault-upload.sshd.conf" "${sshd_fragment}" \
    || fail "sshd fragment already exists and differs; inspect it before replacing"
else
  [[ "${preflight_only}" == 0 ]] || fail "restricted sshd fragment is missing"
  install -o root -g root -m 0644 \
    "${app_dir}/deploy/xider-vault-upload.sshd.conf" "${sshd_fragment}"
fi
sshd -t || fail "sshd rejected the configuration; refusing to reload"
effective="$(sshd -T -C user=${account},host=localhost,addr=127.0.0.1)" || fail "cannot inspect effective sshd configuration"
grep -qx "forcecommand /usr/bin/python3 ${upload_transport} receive" <<<"${effective}" || fail "bounded SSH receive command is not active"
grep -qx "authorizedkeysfile ${authorized_file}" <<<"${effective}" || fail "restricted authorized_keys path is not active"

[[ ! -L "${env_file}" ]] || fail "standby env path is a symlink"
if [[ -e "${env_file}" ]]; then
  owner="$(stat -c '%u' -- "${env_file}")"
  mode="$(stat -c '%a' -- "${env_file}")"
  [[ "${owner}" == 0 ]] && (( (8#${mode} & 077) == 0 )) \
    || fail "existing standby config must be root-owned and private"
else
  [[ "${preflight_only}" == 0 ]] || fail "standby config is missing"
  install -d -o root -g root -m 0750 /etc/xider
  printf 'XIDER_VAULT_RETENTION_DAYS=60\nXIDER_VAULT_MAX_ARCHIVE_BYTES=1073741824\nXIDER_VAULT_MAX_TOTAL_BYTES=10737418240\n' \
    >"${env_file}"
  chown root:root "${env_file}"
  chmod 0600 "${env_file}"
fi

if [[ "${preflight_only}" == 1 ]]; then
  verify_receiver_access
  printf 'X-VAULT standby local preflight passed, including uploader read-only authorized_keys access; no SSH login was attempted.\n'
  exit 0
fi

install -d -o root -g root -m 0750 "${helper_dir}"
install -o root -g root -m 0750 \
  "${app_dir}/deploy/promote_x_vault.py" "${helper_dir}/promote_x_vault.py"
install -o root -g root -m 0750 \
  "${app_dir}/deploy/x-vault-promote.sh" "${helper}"
install -o root -g "${account}" -m 0550 \
  "${app_dir}/ops/x_vault_transport.py" "${upload_transport}"
verify_receiver_access
install -o root -g root -m 0644 \
  "${app_dir}/deploy/xider-vault-promote.service" \
  /etc/systemd/system/xider-vault-promote.service
install -o root -g root -m 0644 \
  "${app_dir}/deploy/xider-vault-promote.timer" \
  /etc/systemd/system/xider-vault-promote.timer
systemd-analyze verify \
  /etc/systemd/system/xider-vault-promote.service \
  /etc/systemd/system/xider-vault-promote.timer
systemctl daemon-reload
systemctl reload ssh.service
systemctl enable --now xider-vault-promote.timer
printf 'Bounded X-VAULT SSH receiver configured; no archive was received or restored.\n'
