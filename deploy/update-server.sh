#!/usr/bin/env bash
set -Eeuo pipefail

APP_DIR="${XIDER_APP_DIR:-/opt/xider}"
SERVICE="${XIDER_SERVICE:-xider-bot.service}"
BACKUP_DIR="${XIDER_BACKUP_DIR:-/var/backups/xider}"
INCOMING="${XIDER_UPDATE_BUNDLE:-${APP_DIR}/incoming/xider-source.zip}"
UNIT_DIR="${XIDER_UNIT_DIR:-/etc/systemd/system}"
LOCK_FILE="${XIDER_LOCK_FILE:-/run/lock/xider-update.lock}"
HEALTH_ATTEMPTS="${XIDER_HEALTH_ATTEMPTS:-15}"
HEALTH_STABLE_CHECKS="${XIDER_HEALTH_STABLE_CHECKS:-3}"
UNIT_FILE="${UNIT_DIR}/${SERVICE}"
EXTRACT_HELPER="${XIDER_EXTRACT_HELPER:-${APP_DIR}/deploy/safe_extract.py}"
COMPONENTS=(TG-BOT-SERVER XGENT-WDS XGENT-MCS deploy)
STAMP="$(date -u +%Y%m%dT%H%M%SZ)-$$"
STAGE=""
RESTORE_STAGE=""
BACKUP=""
MUTATING=0

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run as root (the bot uses sudo)." >&2
  exit 2
fi
if [[ "${APP_DIR}" != /* || "${APP_DIR}" == "/" || "${APP_DIR}" == "/opt" ]]; then
  echo "Refusing unsafe APP_DIR: ${APP_DIR}" >&2
  exit 2
fi
if [[ "${UNIT_DIR}" != /* || "${UNIT_DIR}" == "/" ]]; then
  echo "Refusing unsafe UNIT_DIR: ${UNIT_DIR}" >&2
  exit 2
fi
if [[ ! -d "${APP_DIR}" ]]; then
  echo "Missing app directory: ${APP_DIR}" >&2
  exit 2
fi

install -d -m 0750 "${BACKUP_DIR}"
install -d -m 0755 "${UNIT_DIR}"
install -d -m 0755 "$(dirname "${LOCK_FILE}")"
exec 9>"${LOCK_FILE}"
if ! flock -n 9; then
  echo "Another XIDER update is already running." >&2
  exit 2
fi

stamp() { date -u +%Y%m%dT%H%M%SZ; }

backup_current() {
  local out="${BACKUP_DIR}/xider-$(stamp)-$$.tar.gz"
  local temp="${out}.tmp"
  if ! tar -czf "${temp}" \
    --exclude='./incoming' \
    --exclude='./TG-BOT-SERVER/venv' \
    --exclude='*.env*' \
    --exclude='*.key' \
    --exclude='*.pem' \
    --exclude='*.p12' \
    --exclude='*.pfx' \
    --exclude='*.crt' \
    --exclude='_secrets_embed.py' \
    -C "${APP_DIR}" .; then
    rm -f -- "${temp}"
    return 1
  fi
  if ! tar -tzf "${temp}" >/dev/null; then
    rm -f -- "${temp}"
    return 1
  fi
  mv -- "${temp}" "${out}"
  printf '%s\n' "${out}"
}

latest_backup() {
  find "${BACKUP_DIR}" -maxdepth 1 -type f -name 'xider-*.tar.gz' -printf '%T@ %p\n' \
    | sort -nr | head -n1 | cut -d' ' -f2-
}

wait_healthy() {
  local attempt pid previous_pid="" stable=0
  for ((attempt = 1; attempt <= HEALTH_ATTEMPTS; attempt++)); do
    if systemctl is-enabled --quiet "${SERVICE}" && systemctl is-active --quiet "${SERVICE}"; then
      pid="$(systemctl show --property=MainPID --value "${SERVICE}" 2>/dev/null || true)"
      if [[ "${pid}" =~ ^[1-9][0-9]*$ ]]; then
        if [[ "${pid}" == "${previous_pid}" ]]; then
          stable=$((stable + 1))
        else
          previous_pid="${pid}"
          stable=1
        fi
        if (( stable >= HEALTH_STABLE_CHECKS )); then
          return 0
        fi
      else
        previous_pid=""
        stable=0
      fi
    else
      previous_pid=""
      stable=0
    fi
    sleep 1
  done
  return 1
}

restore_snapshot() {
  local archive="$1" unit_backup="${1%.tar.gz}.service"
  systemctl stop "${SERVICE}" >/dev/null 2>&1 || true
  RESTORE_STAGE="$(mktemp -d "${APP_DIR}/.xider-restore.XXXXXX")"
  tar -xzf "${archive}" -C "${RESTORE_STAGE}"
  # Remove source files introduced by the failed release, while preserving
  # runtime configuration and virtual environments intentionally omitted from
  # the backup archive.
  local component current relative
  for component in "${COMPONENTS[@]}"; do
    if [[ -d "${APP_DIR}/${component}" ]]; then
      while IFS= read -r -d '' current; do
        relative="${current#"${APP_DIR}/"}"
        [[ -e "${RESTORE_STAGE}/${relative}" ]] && continue
        case "/${relative}/" in
          */TG-BOT-SERVER/venv/*|*/TG-BOT-SERVER/.venv/*|*/XGENT-WDS/venv/*|*/XGENT-MCS/venv/*) continue ;;
        esac
        case "$(basename "${relative}")" in
          .env|.env.*|*.key|*.pem|*.p12|*.pfx) continue ;;
        esac
        rm -f -- "${current}"
      done < <(find "${APP_DIR}/${component}" -type f -print0)
    fi
  done
  overlay_contents "${RESTORE_STAGE}"
  rm -rf -- "${RESTORE_STAGE}"
  RESTORE_STAGE=""
  if [[ -f "${unit_backup}" ]]; then
    install -m 0644 "${unit_backup}" "${UNIT_FILE}"
  elif [[ -f "${APP_DIR}/deploy/xider-bot.service" ]]; then
    install -m 0644 "${APP_DIR}/deploy/xider-bot.service" "${UNIT_FILE}"
  fi
  chown -R xider:xider "${APP_DIR}/TG-BOT-SERVER" "${APP_DIR}/XGENT-WDS" "${APP_DIR}/XGENT-MCS" "${APP_DIR}/deploy" 2>/dev/null || true
  systemctl daemon-reload || true
  systemctl restart "${SERVICE}" || true
}

cleanup() {
  if [[ -n "${STAGE}" && -d "${STAGE}" ]]; then
    rm -rf -- "${STAGE}"
  fi
  if [[ -n "${RESTORE_STAGE}" && -d "${RESTORE_STAGE}" ]]; then
    rm -rf -- "${RESTORE_STAGE}"
  fi
}

overlay_contents() {
  local source_dir="$1" entry
  while IFS= read -r -d '' entry; do
    cp -a "${entry}" "${APP_DIR}/"
  done < <(find "${source_dir}" -mindepth 1 -maxdepth 1 -print0)
}

on_error() {
  local code=$?
  trap - ERR
  if [[ "${MUTATING}" -eq 1 && -n "${BACKUP}" && -f "${BACKUP}" ]]; then
    echo "Update failed; restoring previous source from ${BACKUP}." >&2
    restore_snapshot "${BACKUP}" || echo "Automatic rollback also failed; preserve ${BACKUP} for recovery." >&2
    MUTATING=0
  fi
  cleanup
  exit "${code}"
}
trap on_error ERR
trap cleanup EXIT

do_update() {
  local component unit_backup
  [[ -f "${INCOMING}" ]] || { echo "No release bundle at ${INCOMING}; upload a bundle first." >&2; return 3; }
  [[ -r "${EXTRACT_HELPER}" ]] || { echo "Missing safe archive extractor: ${EXTRACT_HELPER}" >&2; return 3; }
  systemctl is-enabled --quiet "${SERVICE}" || { echo "${SERVICE} is not enabled." >&2; return 4; }
  systemctl is-active --quiet "${SERVICE}" || { echo "${SERVICE} is not active; refusing unattended update." >&2; return 4; }
  [[ -f "${APP_DIR}/TG-BOT-SERVER/requirements.txt" ]] || { echo "Current install is incomplete." >&2; return 4; }

  BACKUP="$(backup_current)"
  unit_backup="${BACKUP%.tar.gz}.service"
  if [[ -f "${UNIT_FILE}" ]]; then
    cp -a "${UNIT_FILE}" "${unit_backup}"
  fi
  STAGE="$(mktemp -d "${APP_DIR}/.xider-update.XXXXXX")"
  python3 "${EXTRACT_HELPER}" "${INCOMING}" "${STAGE}"
  find "${STAGE}" -type f -name '*.sh' -exec sed -i 's/\r$//' {} +
  [[ -f "${STAGE}/TG-BOT-SERVER/bot.py" && -f "${STAGE}/TG-BOT-SERVER/requirements.txt" ]] || {
    echo "Release bundle is missing the Telegram bot." >&2; return 5;
  }
  for component in "${COMPONENTS[@]}"; do
    [[ -d "${STAGE}/${component}" ]] || { echo "Release bundle is missing ${component}." >&2; return 5; }
  done
  if ! cmp -s "${APP_DIR}/TG-BOT-SERVER/requirements.txt" "${STAGE}/TG-BOT-SERVER/requirements.txt"; then
    echo "Dependency changes are not yet supported by this in-place updater; current install is unchanged." >&2
    return 6
  fi
  python3 -m compileall -q "${STAGE}/TG-BOT-SERVER" "${STAGE}/XGENT-WDS" "${STAGE}/XGENT-MCS"

  MUTATING=1
  systemctl stop "${SERVICE}"
  overlay_contents "${STAGE}"
  install -m 0644 "${APP_DIR}/deploy/xider-bot.service" "${UNIT_FILE}"
  chown -R xider:xider "${APP_DIR}/TG-BOT-SERVER" "${APP_DIR}/XGENT-WDS" "${APP_DIR}/XGENT-MCS" "${APP_DIR}/deploy"
  systemctl daemon-reload
  systemctl restart "${SERVICE}"
  if ! wait_healthy; then
    echo "Health check failed; restoring ${BACKUP}." >&2
    restore_snapshot "${BACKUP}"
    MUTATING=0
    return 7
  fi
  MUTATING=0
  echo "Update installed; service is enabled, active, and has a MainPID. Backup: ${BACKUP}"
}

do_rollback() {
  local archive unit_backup
  archive="$(latest_backup)"
  [[ -n "${archive}" && -f "${archive}" ]] || { echo "No XIDER backup found." >&2; return 8; }
  BACKUP="$(backup_current)"
  unit_backup="${BACKUP%.tar.gz}.service"
  if [[ -f "${UNIT_FILE}" ]]; then
    cp -a "${UNIT_FILE}" "${unit_backup}"
  fi
  MUTATING=1
  restore_snapshot "${archive}"
  if ! wait_healthy; then
    echo "Rollback did not pass health check; the pre-rollback source is saved at ${BACKUP}." >&2
    return 9
  fi
  MUTATING=0
  echo "Rollback restored ${archive}; service health check passed."
}

case "${1:-}" in
  update) do_update ;;
  rollback) do_rollback ;;
  backup) backup_current ;;
  *) echo "Usage: $0 {update|rollback|backup}" >&2; exit 2 ;;
esac
