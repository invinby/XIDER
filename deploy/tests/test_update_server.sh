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
STATE_FILE="${TEMP_ROOT}/systemctl.stopped"
INCOMING="${TEMP_ROOT}/release.zip"
SERVICE='xider-test.service'

cleanup() {
  rm -rf -- "${TEMP_ROOT}"
}
trap cleanup EXIT

mkdir -p "${APP_DIR}/TG-BOT-SERVER" "${APP_DIR}/XGENT-WDS" \
  "${APP_DIR}/XGENT-MCS" "${APP_DIR}/deploy" "${MOCK_BIN}" "${UNIT_DIR}" "${BACKUP_DIR}"
chmod 0777 "${BACKUP_DIR}"
cp "${FIXTURES}/mock-systemctl.sh" "${MOCK_BIN}/systemctl"
cp "${FIXTURES}/mock-chown.sh" "${MOCK_BIN}/chown"
cp "${ROOT}/deploy/safe_extract.py" "${APP_DIR}/deploy/safe_extract.py"
chmod +x "${MOCK_BIN}/systemctl" "${MOCK_BIN}/chown"
printf 'old\n' >"${APP_DIR}/TG-BOT-SERVER/bot.py"
printf 'test-dependency==1\n' >"${APP_DIR}/TG-BOT-SERVER/requirements.txt"
printf 'keep-runtime-config\n' >"${APP_DIR}/TG-BOT-SERVER/.env"
printf 'keep-runtime-override\n' >"${APP_DIR}/TG-BOT-SERVER/.env.production"
printf 'private-key-fixture\n' >"${APP_DIR}/TG-BOT-SERVER/fixture.key"
printf '[Service]\nExecStart=/old\n' >"${UNIT_DIR}/${SERVICE}"

make_bundle() {
  local body="$1"
  python3 - "${INCOMING}" "${body}" "${ROOT}/deploy/safe_extract.py" <<'PY'
import sys
from zipfile import ZipFile

with ZipFile(sys.argv[1], "w") as archive:
    archive.writestr("TG-BOT-SERVER/bot.py", sys.argv[2] + "\n")
    if sys.argv[2] == "bad":
        archive.writestr("TG-BOT-SERVER/new-feature.py", "pass\n")
    archive.writestr("TG-BOT-SERVER/requirements.txt", "test-dependency==1\n")
    archive.writestr("XGENT-WDS/agent.py", "pass\n")
    archive.writestr("XGENT-MCS/agent.py", "pass\n")
    archive.writestr("deploy/xider-bot.service", "[Service]\nExecStart=/new\n")
    with open(sys.argv[3], encoding="utf-8") as helper:
        archive.writestr("deploy/safe_extract.py", helper.read())
PY
}

run_updater() {
  local operation="${1:-update}"
  sudo -n env \
    "PATH=${MOCK_BIN}:${PATH}" \
    "XIDER_APP_DIR=${APP_DIR}" \
    "XIDER_BACKUP_DIR=${BACKUP_DIR}" \
    "XIDER_UPDATE_BUNDLE=${INCOMING}" \
    "XIDER_SERVICE=${SERVICE}" \
    "XIDER_UNIT_DIR=${UNIT_DIR}" \
    "XIDER_LOCK_FILE=${TEMP_ROOT}/lock/update.lock" \
    "XIDER_HEALTH_ATTEMPTS=2" \
    "XIDER_HEALTH_STABLE_CHECKS=1" \
    "XIDER_TEST_SYSTEMCTL_STATE=${STATE_FILE}" \
    "XIDER_TEST_FAIL_HEALTH=${XIDER_TEST_FAIL_HEALTH:-0}" \
    bash "${SCRIPT}" "${operation}"
}

run_update() { run_updater update; }

make_bundle good
run_update
[[ "$(<"${APP_DIR}/TG-BOT-SERVER/bot.py")" == good ]]
[[ ! -e "${APP_DIR}/TG-BOT-SERVER/new-feature.py" ]]
[[ "$(<"${APP_DIR}/TG-BOT-SERVER/.env")" == keep-runtime-config ]]
[[ "$(<"${APP_DIR}/TG-BOT-SERVER/.env.production")" == keep-runtime-override ]]
[[ "$(<"${APP_DIR}/TG-BOT-SERVER/fixture.key")" == private-key-fixture ]]
grep -q 'ExecStart=/new' "${UNIT_DIR}/${SERVICE}"
backup_file="$(compgen -G "${BACKUP_DIR}/xider-*.tar.gz")"
tar -tzf "${backup_file}" >"${TEMP_ROOT}/backup-files.txt"
! grep -E '\.env|fixture\.key' "${TEMP_ROOT}/backup-files.txt"

make_bundle bad
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
run_update
[[ "$(<"${APP_DIR}/TG-BOT-SERVER/bot.py")" == manual-new ]]
run_updater rollback
[[ "$(<"${APP_DIR}/TG-BOT-SERVER/bot.py")" == good ]]
[[ "$(<"${APP_DIR}/TG-BOT-SERVER/.env")" == keep-runtime-config ]]
[[ "$(<"${APP_DIR}/TG-BOT-SERVER/.env.production")" == keep-runtime-override ]]

echo 'Updater success, automatic/manual rollback, env preservation, and archive validation scenarios passed.'
