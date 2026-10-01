#!/usr/bin/env bash
set -Eeuo pipefail

if [[ "${EUID}" -ne 0 ]]; then
  if command -v sudo >/dev/null 2>&1 && sudo -n true 2>/dev/null; then
    exec sudo -n bash "${BASH_SOURCE[0]}" "$@"
  fi
  echo "SKIP: X-VAULT pipeline fixture requires root or passwordless sudo."
  exit 0
fi

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
tmp="$(mktemp -d "${TMPDIR:-/tmp}/xider-vault-pipeline.XXXXXX")"
[[ "${tmp}" == "${TMPDIR:-/tmp}"/xider-vault-pipeline.* ]] || {
  echo "Unexpected temporary path; refusing cleanup." >&2
  exit 1
}
cleanup() {
  local code=$?
  trap - EXIT
  rm -rf -- "${tmp}"
  exit "${code}"
}
trap cleanup EXIT

mkdir -p "${tmp}/source" "${tmp}/bin" "${tmp}/remote-incoming" "${tmp}/incoming" "${tmp}/archives"
chmod 0700 "${tmp}/remote-incoming"
chmod 0700 "${tmp}/archives"
touch "${tmp}/upload.lock"
chmod 0660 "${tmp}/upload.lock"
printf 'safe source\n' >"${tmp}/source/bot.py"
printf 'BOT_TOKEN=recipient-encrypted-marker\n' >"${tmp}/bot.env"
printf '[Service]\nExecStart=/opt/xider/bot\n' >"${tmp}/xider-bot.service"
printf 'fake-private-key\n' >"${tmp}/upload-key"
chmod 0600 "${tmp}/upload-key"
printf 'standby.example ssh-ed25519 AAAA\n' >"${tmp}/known_hosts"

python3 "${ROOT}/deploy/tests/test_x_vault_transport.py"
python3 "${ROOT}/ops/x_vault.py" keygen --directory "${tmp}/offline-key" >"${tmp}/keygen.out"
public_key="${tmp}/offline-key/vault-recipient-public.pem"
private_key="${tmp}/offline-key/vault-recipient-private.pem"
grep -q '^fingerprint=' "${tmp}/keygen.out"
! grep -q 'PRIVATE KEY' "${tmp}/keygen.out"

cat >"${tmp}/bin/ssh" <<'MOCK_SSH'
#!/usr/bin/env bash
set -Eeuo pipefail
strict=0
pinned=0
identity=0
batch=0
non_tty=0
for ((index = 1; index <= $#; index++)); do
  value="${!index}"
  case "${value}" in
    -T) non_tty=1 ;;
    -o)
      next=$((index + 1))
      option="${!next}"
      case "${option}" in
        BatchMode=yes) batch=1 ;;
        StrictHostKeyChecking=yes) strict=1 ;;
        UserKnownHostsFile=*) pinned=1 ;;
        IdentityFile=*) identity=1 ;;
      esac
      ;;
  esac
done
[[ "${batch}" == 1 && "${strict}" == 1 && "${pinned}" == 1 && "${identity}" == 1 && "${non_tty}" == 1 ]]
[[ "${!#}" == "${XIDER_VAULT_TARGET}" ]]
exec python3 -c 'import os,sys; sys.path.insert(0, os.environ["TEST_OPS_DIR"]); from x_vault_transport import receive_upload; receive_upload(sys.stdin.buffer, os.environ["TEST_REMOTE_INCOMING"], os.environ["TEST_UPLOAD_LOCK"])'
MOCK_SSH
chmod 0755 "${tmp}/bin/ssh"

export PATH="${tmp}/bin:${PATH}"
export TEST_REMOTE_INCOMING="${tmp}/remote-incoming"
export TEST_UPLOAD_LOCK="${tmp}/upload.lock"
export TEST_OPS_DIR="${ROOT}/ops"
export XIDER_VAULT_SOURCE="${tmp}/source"
export XIDER_VAULT_ENV_FILE="${tmp}/bot.env"
export XIDER_VAULT_SERVICE_FILE="${tmp}/xider-bot.service"
export XIDER_VAULT_PUBLIC_KEY="${public_key}"
export XIDER_VAULT_SSH_KEY="${tmp}/upload-key"
export XIDER_VAULT_KNOWN_HOSTS="${tmp}/known_hosts"
export XIDER_VAULT_TARGET="xvault-upload@standby.example"
export XIDER_VAULT_REMOTE_DIR="/incoming"
export XIDER_VAULT_LOCK_FILE="${tmp}/xider-update.lock"
export XIDER_VAULT_UPLOAD_LOCK="${TEST_UPLOAD_LOCK}"
export XIDER_VAULT_RUNTIME_DIR="${tmp}/runtime"
export XIDER_VAULT_PYTHON="$(command -v python3)"
export XIDER_VAULT_TOOL="${ROOT}/ops/x_vault.py"
export XIDER_VAULT_TRANSPORT="${ROOT}/ops/x_vault_transport.py"
export XIDER_VAULT_PROMOTION_HELPER="${ROOT}/deploy/promote_x_vault.py"

bash "${ROOT}/deploy/x-vault-backup.sh"
mapfile -t uploads < <(find "${tmp}/remote-incoming" -maxdepth 1 -type f -name 'xvault-*.xvlt' -print)
[[ "${#uploads[@]}" == 1 ]]
if grep -a -q 'recipient-encrypted-marker' "${uploads[0]}"; then
  echo "Plaintext secret marker leaked into X-VAULT archive." >&2
  exit 1
fi
python3 "${ROOT}/ops/x_vault.py" verify --archive "${uploads[0]}" --private-key "${private_key}" \
  >"${tmp}/verified.json"
grep -q 'X-VAULT/2' "${tmp}/verified.json"

# Exercise the same update lock used by deploy/update-server.sh.
exec 8>"${XIDER_VAULT_LOCK_FILE}"
flock -n 8
if bash "${ROOT}/deploy/x-vault-backup.sh" >"${tmp}/locked.out" 2>&1; then
  echo "Backup unexpectedly started while XIDER updater held its lock." >&2
  exit 1
fi
[[ "$(find "${tmp}/remote-incoming" -maxdepth 1 -type f -name 'xvault-*.xvlt' | wc -l)" -eq 1 ]]
flock -u 8
exec 8>&-

archive_name="${uploads[0]##*/}"
cp -- "${uploads[0]}" "${tmp}/incoming/${archive_name}"
XIDER_VAULT_INCOMING="${tmp}/incoming" \
XIDER_VAULT_ARCHIVE_DIR="${tmp}/archives" \
bash "${ROOT}/deploy/x-vault-promote.sh"
[[ ! -e "${tmp}/incoming/${archive_name}" ]]
[[ "$(stat -c '%u:%a' -- "${tmp}/archives/${archive_name}")" == "0:400" ]]

# The promoter must leave even a complete upload queued while a receiver owns
# the shared lock, so it cannot race the receiver's final atomic publication.
cp -- "${tmp}/archives/${archive_name}" "${tmp}/incoming/${archive_name}"
exec 7>"${TEST_UPLOAD_LOCK}"
flock -n 7
XIDER_VAULT_INCOMING="${tmp}/incoming" \
XIDER_VAULT_ARCHIVE_DIR="${tmp}/archives" \
bash "${ROOT}/deploy/x-vault-promote.sh" >"${tmp}/busy-promoter.out"
grep -q 'upload is active; skipping promotion' "${tmp}/busy-promoter.out"
[[ -f "${tmp}/incoming/${archive_name}" ]]
flock -u 7
exec 7>&-
rm -f -- "${tmp}/incoming/${archive_name}"

# A compromised uploader controls incoming pathnames. A symlink with a valid
# archive name must never make the root promotion job chmod or chown its target.
printf 'do-not-change-this-file\n' >"${tmp}/sentinel"
chmod 0644 "${tmp}/sentinel"
ln -s "${tmp}/sentinel" "${tmp}/unsafe-update.lock"
if XIDER_VAULT_LOCK_FILE="${tmp}/unsafe-update.lock" \
  bash "${ROOT}/deploy/x-vault-backup.sh" >"${tmp}/unsafe-lock.out" 2>&1; then
  echo "Backup unexpectedly accepted a symlinked shared lock." >&2
  exit 1
fi
[[ "$(<"${tmp}/sentinel")" == 'do-not-change-this-file' ]]
[[ "$(stat -c '%a' -- "${tmp}/sentinel")" == "644" ]]
rm -f -- "${tmp}/unsafe-update.lock"
ln -s "${tmp}/sentinel" "${tmp}/incoming/xvault-20261001T000000Z-deadbe.xvlt"
archive_hash_before="$(sha256sum "${tmp}/archives/${archive_name}" | cut -d' ' -f1)"
XIDER_VAULT_INCOMING="${tmp}/incoming" \
XIDER_VAULT_ARCHIVE_DIR="${tmp}/archives" \
bash "${ROOT}/deploy/x-vault-promote.sh" >"${tmp}/symlink.out" 2>"${tmp}/symlink.err"
[[ "$(<"${tmp}/sentinel")" == 'do-not-change-this-file' ]]
[[ "$(stat -c '%a' -- "${tmp}/sentinel")" == "644" ]]
[[ ! -L "${tmp}/incoming/xvault-20261001T000000Z-deadbe.xvlt" ]]
[[ "$(sha256sum "${tmp}/archives/${archive_name}" | cut -d' ' -f1)" == "${archive_hash_before}" ]]

# The archive store cap keeps a new transfer queued rather than allowing
# incoming uploads to replace or evict a still-retained root-owned archive.
filler="${tmp}/archives/xvault-20200101T000000Z-f00baa.xvlt"
truncate -s 1047700 "${filler}"
cp -- "${tmp}/archives/${archive_name}" "${tmp}/incoming/${archive_name}"
XIDER_VAULT_MAX_ARCHIVE_BYTES=1048576 \
XIDER_VAULT_MAX_TOTAL_BYTES=1048576 \
XIDER_VAULT_INCOMING="${tmp}/incoming" \
XIDER_VAULT_ARCHIVE_DIR="${tmp}/archives" \
bash "${ROOT}/deploy/x-vault-promote.sh" >"${tmp}/capacity.out" 2>"${tmp}/capacity.err"
[[ -e "${tmp}/incoming/${archive_name}" ]]
[[ "$(sha256sum "${tmp}/archives/${archive_name}" | cut -d' ' -f1)" == "${archive_hash_before}" ]]
rm -f -- "${filler}"

# A duplicate final name is discarded from incoming without replacing the
# previously promoted root-owned archive.
XIDER_VAULT_INCOMING="${tmp}/incoming" \
XIDER_VAULT_ARCHIVE_DIR="${tmp}/archives" \
bash "${ROOT}/deploy/x-vault-promote.sh" >"${tmp}/duplicate.out" 2>"${tmp}/duplicate.err"
[[ ! -e "${tmp}/incoming/${archive_name}" ]]
[[ "$(sha256sum "${tmp}/archives/${archive_name}" | cut -d' ' -f1)" == "${archive_hash_before}" ]]

# The root-owned standby copy ages out through retention, but the uploader
# cannot write into the archive directory due to its root-only directory mode.
touch -d '90 days ago' "${tmp}/archives/${archive_name}"
XIDER_VAULT_INCOMING="${tmp}/incoming" \
XIDER_VAULT_ARCHIVE_DIR="${tmp}/archives" \
bash "${ROOT}/deploy/x-vault-promote.sh" >"${tmp}/retention.out"
[[ ! -e "${tmp}/archives/${archive_name}" ]]

grep -q 'ForceCommand /usr/bin/python3 /usr/local/libexec/xider-vault-upload/x_vault_transport.py receive' "${ROOT}/deploy/xider-vault-upload.sshd.conf"
! grep -q 'ChrootDirectory' "${ROOT}/deploy/xider-vault-upload.sshd.conf"
grep -q 'readonly upload_helper_dir="/usr/local/libexec/xider-vault-upload"' "${ROOT}/deploy/xider-vault-standby-setup.sh"
grep -q 'readonly upload_transport="${upload_helper_dir}/x_vault_transport.py"' "${ROOT}/deploy/xider-vault-standby-setup.sh"
grep -q 'install -o root -g "${account}" -m 0550' "${ROOT}/deploy/xider-vault-standby-setup.sh"
grep -q 'AllowTcpForwarding no' "${ROOT}/deploy/xider-vault-upload.sshd.conf"
echo "X-VAULT bounded SSH sender/receiver, inbox and archive limits, update-lock coordination, no-follow root promotion, capacity, duplicate rejection, and retention fixtures passed."
