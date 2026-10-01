#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

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
    install -d -o "${owner}" -g "${group}" -m "${mode}" "${path}"
  fi
  [[ "$(stat -c '%U:%G' -- "${path}")" == "${owner}:${group}" \
    && "$(stat -c '%a' -- "${path}")" == "${mode}" ]] \
    || fail "existing directory ownership/mode differs; refusing to take it over: ${path}"
}

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
  groupadd --system "${account}"
fi
if ! id -u "${account}" >/dev/null 2>&1; then
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
ensure_directory "${upload_helper_dir}" root "${account}" 750

[[ ! -L "${upload_lock}" ]] || fail "X-VAULT upload lock is a symlink"
if [[ ! -e "${upload_lock}" ]]; then
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
  printf '%s\n' "${tmpfiles_line}" >"${tmpfiles_rule}"
  chown root:root "${tmpfiles_rule}"
  chmod 0644 "${tmpfiles_rule}"
fi
systemd-tmpfiles --create "${tmpfiles_rule}"

ensure_directory "${authorized_dir}" root root 755
candidate="$(mktemp "${authorized_dir}/.xvault-key.XXXXXX")"
trap 'rm -f -- "${candidate}"' EXIT
printf 'restrict,command="/usr/bin/python3 %s receive" %s\n' \
  "${upload_transport}" \
  "$(awk 'NR==1 {print $1, $2, "xider-vault-upload"}' "${public_key_file}")" >"${candidate}"
chmod 0600 "${candidate}"
[[ ! -L "${authorized_file}" ]] || fail "authorized key path is a symlink"
if [[ -e "${authorized_file}" ]]; then
  [[ -f "${authorized_file}" && "$(stat -c '%u:%g:%a' -- "${authorized_file}")" == "0:0:600" ]] \
    || fail "existing uploader authorized_keys must be root-owned mode 0600"
  cmp -s "${candidate}" "${authorized_file}" || fail "uploader key already exists and differs; refusing to rotate it implicitly"
else
  install -o root -g root -m 0600 "${candidate}" "${authorized_file}"
fi

[[ ! -L "${sshd_fragment}" ]] || fail "sshd fragment path is a symlink"
if [[ -e "${sshd_fragment}" ]]; then
  cmp -s "${app_dir}/deploy/xider-vault-upload.sshd.conf" "${sshd_fragment}" \
    || fail "sshd fragment already exists and differs; inspect it before replacing"
else
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
  install -d -o root -g root -m 0750 /etc/xider
  printf 'XIDER_VAULT_RETENTION_DAYS=60\nXIDER_VAULT_MAX_ARCHIVE_BYTES=1073741824\nXIDER_VAULT_MAX_TOTAL_BYTES=10737418240\n' \
    >"${env_file}"
  chown root:root "${env_file}"
  chmod 0600 "${env_file}"
fi

install -d -o root -g root -m 0750 "${helper_dir}"
install -o root -g root -m 0750 \
  "${app_dir}/deploy/promote_x_vault.py" "${helper_dir}/promote_x_vault.py"
install -o root -g root -m 0750 \
  "${app_dir}/deploy/x-vault-promote.sh" "${helper}"
install -o root -g "${account}" -m 0550 \
  "${app_dir}/ops/x_vault_transport.py" "${upload_transport}"
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
