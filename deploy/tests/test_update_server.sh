#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SCRIPT="${ROOT}/deploy/update-server.sh"
FIXTURES="${ROOT}/deploy/tests/fixtures"
TEMP_ROOT="$(mktemp -d)"
APP_DIR="${TEMP_ROOT}/app"
BACKUP_DIR="${TEMP_ROOT}/backups"
UNIT_DIR="${TEMP_ROOT}/units"
MOCK_BIN="${TEMP_ROOT}/bin"
ROOT_HELPER_DIR="${TEMP_ROOT}/root-helpers"
STATE_FILE="${TEMP_ROOT}/systemctl.stopped"
INCOMING="${TEMP_ROOT}/release.zip"
MANIFEST="${TEMP_ROOT}/release-manifest.json"
SERVICE='xider-test.service'
APP_DIR_MODE=""

cleanup() {
  rm -rf -- "${TEMP_ROOT}"
}
trap cleanup EXIT

mkdir -p "${APP_DIR}/TG-BOT-SERVER" "${APP_DIR}/XGENT-WDS" \
  "${APP_DIR}/XGENT-MCS" "${APP_DIR}/deploy" "${MOCK_BIN}" "${UNIT_DIR}" "${BACKUP_DIR}" \
  "${TEMP_ROOT}/lock"
mkdir -p "${ROOT_HELPER_DIR}"
chmod 0777 "${BACKUP_DIR}"
APP_DIR_MODE="$(stat -c '%a' "${APP_DIR}")"
cp "${FIXTURES}/mock-systemctl.sh" "${MOCK_BIN}/systemctl"
cp "${FIXTURES}/mock-chown.sh" "${MOCK_BIN}/chown"
sed -i 's/\r$//' "${MOCK_BIN}/systemctl" "${MOCK_BIN}/chown"
cp "${ROOT}/deploy/safe_extract.py" "${APP_DIR}/deploy/safe_extract.py"
cp "${ROOT}/deploy/safe_extract.py" "${ROOT_HELPER_DIR}/safe_extract.py"
cat >"${ROOT_HELPER_DIR}/verify_server_bundle.py" <<'PY'
#!/usr/bin/env python3
import sys
from pathlib import Path
if Path(sys.argv[1]).read_text(encoding="utf-8").strip() != "signed-test-manifest":
    raise SystemExit("test manifest rejected")
if not Path(sys.argv[2]).is_file():
    raise SystemExit("test bundle missing")
PY
chmod +x "${MOCK_BIN}/systemctl" "${MOCK_BIN}/chown"
printf 'old\n' >"${APP_DIR}/TG-BOT-SERVER/bot.py"
printf 'test-dependency==1\n' >"${APP_DIR}/TG-BOT-SERVER/requirements.txt"
printf 'keep-runtime-config\n' >"${APP_DIR}/TG-BOT-SERVER/.env"
printf 'keep-runtime-override\n' >"${APP_DIR}/TG-BOT-SERVER/.env.production"
printf 'private-key-fixture\n' >"${APP_DIR}/TG-BOT-SERVER/fixture.key"
printf '[Service]\nExecStart=/old\n' >"${UNIT_DIR}/${SERVICE}"

make_bundle() {
  local body="$1"
  python3 - "${INCOMING}" "${body}" "${ROOT}" <<'PY'
import sys
from pathlib import Path
from zipfile import ZipFile

with ZipFile(sys.argv[1], "w") as archive:
    prefix = "XIDER-source/"
    archive.writestr(prefix + "TG-BOT-SERVER/bot.py", sys.argv[2] + "\n")
    if sys.argv[2] == "bad":
        archive.writestr(prefix + "TG-BOT-SERVER/new-feature.py", "pass\n")
    # Windows checkouts can put CRLF in this text manifest. The updater must
    # compare requirements by lines, not reject an otherwise identical release.
    archive.writestr(prefix + "TG-BOT-SERVER/requirements.txt", "test-dependency==1\r\n")
    archive.writestr(prefix + "XGENT-WDS/agent.py", "pass\n")
    archive.writestr(prefix + "XGENT-MCS/agent.py", "pass\n")
    archive.writestr(prefix + "deploy/xider-bot.service", "[Service]\nExecStart=/new\n")
    for helper in ("update-server.sh", "safe_extract.py", "xider-server-ops.sh"):
        with (Path(sys.argv[3]) / "deploy" / helper).open(encoding="utf-8") as source:
            archive.writestr(prefix + f"deploy/{helper}", source.read())
PY
}

run_updater() {
  local operation="${1:-update}"
  local lock_file="${XIDER_TEST_LOCK_FILE:-${TEMP_ROOT}/lock/update.lock}"
  shift || true
  sudo -n env \
    "PATH=${MOCK_BIN}:${PATH}" \
    "XIDER_APP_DIR=${APP_DIR}" \
    "XIDER_BACKUP_DIR=${BACKUP_DIR}" \
    "XIDER_UPDATE_BUNDLE=${INCOMING}" \
    "XIDER_UPDATE_MANIFEST=${MANIFEST}" \
    "XIDER_ROOT_HELPER_DIR=${ROOT_HELPER_DIR}" \
    "XIDER_SERVICE=${SERVICE}" \
    "XIDER_UNIT_DIR=${UNIT_DIR}" \
    "XIDER_LOCK_FILE=${lock_file}" \
    "XIDER_HEALTH_ATTEMPTS=2" \
    "XIDER_HEALTH_STABLE_CHECKS=1" \
    "XIDER_TEST_SYSTEMCTL_STATE=${STATE_FILE}" \
    "XIDER_TEST_FAIL_HEALTH=${XIDER_TEST_FAIL_HEALTH:-0}" \
    bash "${SCRIPT}" "${operation}" "$@"
}

run_update() { run_updater update; }

make_bundle unsigned-test
printf 'unsigned-manifest\n' >"${MANIFEST}"
if run_update; then
  echo 'Expected an unsigned server bundle to be rejected.' >&2
  exit 1
fi
[[ "$(<"${APP_DIR}/TG-BOT-SERVER/bot.py")" == old ]]

make_bundle good
printf 'signed-test-manifest\n' >"${MANIFEST}"
printf 'lock-target-must-survive\n' >"${TEMP_ROOT}/lock-sentinel"
ln -s "${TEMP_ROOT}/lock-sentinel" "${TEMP_ROOT}/lock/unsafe-update.lock"
if XIDER_TEST_LOCK_FILE="${TEMP_ROOT}/lock/unsafe-update.lock" run_update; then
  echo 'Expected the updater to reject a symlinked update-lock path.' >&2
  exit 1
fi
[[ "$(<"${TEMP_ROOT}/lock-sentinel")" == lock-target-must-survive ]]
[[ "$(<"${APP_DIR}/TG-BOT-SERVER/bot.py")" == old ]]
rm -f -- "${TEMP_ROOT}/lock/unsafe-update.lock"
run_update
old_backup="$(find "${BACKUP_DIR}" -maxdepth 1 -type f -name 'xider-*.tar.gz' -print -quit)"
[[ -n "${old_backup}" ]]
[[ "$(<"${APP_DIR}/TG-BOT-SERVER/bot.py")" == good ]]
[[ ! -e "${APP_DIR}/TG-BOT-SERVER/new-feature.py" ]]
[[ "$(<"${APP_DIR}/TG-BOT-SERVER/.env")" == keep-runtime-config ]]
[[ "$(<"${APP_DIR}/TG-BOT-SERVER/.env.production")" == keep-runtime-override ]]
[[ "$(stat -c '%a' "${APP_DIR}")" == "${APP_DIR_MODE}" ]]
[[ "$(<"${APP_DIR}/TG-BOT-SERVER/fixture.key")" == private-key-fixture ]]
grep -q 'ExecStart=/new' "${UNIT_DIR}/${SERVICE}"
backup_file="$(compgen -G "${BACKUP_DIR}/xider-*.tar.gz")"
tar -tzf "${backup_file}" >"${TEMP_ROOT}/backup-files.txt"
! grep -E '\.env|fixture\.key' "${TEMP_ROOT}/backup-files.txt"

make_bundle bad
printf 'signed-test-manifest\n' >"${MANIFEST}"
if XIDER_TEST_FAIL_HEALTH=1 run_update; then
  echo 'Expected the mocked health check to reject the update.' >&2
  exit 1
fi
[[ "$(<"${APP_DIR}/TG-BOT-SERVER/bot.py")" == good ]]
[[ ! -e "${APP_DIR}/TG-BOT-SERVER/new-feature.py" ]]
[[ "$(<"${APP_DIR}/TG-BOT-SERVER/.env")" == keep-runtime-config ]]
grep -q 'ExecStart=/new' "${UNIT_DIR}/${SERVICE}"

python3 - "${INCOMING}" <<'PY'
import sys
from zipfile import ZipFile

with ZipFile(sys.argv[1], "w") as archive:
    archive.writestr("../xider-update-escape.txt", "must not be extracted")
PY
if run_update; then
  echo 'Expected the path-traversal archive to be rejected.' >&2
  exit 1
fi
[[ ! -e "${TEMP_ROOT}/xider-update-escape.txt" ]]
[[ "$(<"${APP_DIR}/TG-BOT-SERVER/bot.py")" == good ]]

make_bundle manual-new
printf 'signed-test-manifest\n' >"${MANIFEST}"
run_update
[[ "$(<"${APP_DIR}/TG-BOT-SERVER/bot.py")" == manual-new ]]
run_updater rollback
[[ "$(<"${APP_DIR}/TG-BOT-SERVER/bot.py")" == good ]]

make_bundle selected-backup-test
printf 'signed-test-manifest\n' >"${MANIFEST}"
run_update
if run_updater rollback "${APP_DIR}/TG-BOT-SERVER/bot.py"; then
  echo 'Expected rollback to reject a path outside the backup directory.' >&2
  exit 1
fi
[[ "$(<"${APP_DIR}/TG-BOT-SERVER/bot.py")" == selected-backup-test ]]
run_updater rollback "${old_backup}"
[[ "$(<"${APP_DIR}/TG-BOT-SERVER/bot.py")" == old ]]
[[ "$(<"${APP_DIR}/TG-BOT-SERVER/.env")" == keep-runtime-config ]]
[[ "$(<"${APP_DIR}/TG-BOT-SERVER/.env.production")" == keep-runtime-override ]]
[[ "$(stat -c '%a' "${APP_DIR}")" == "${APP_DIR_MODE}" ]]

echo 'Updater success, automatic/latest/selected rollback, env preservation, and archive validation scenarios passed.'
