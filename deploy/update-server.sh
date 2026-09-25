#!/usr/bin/env bash
set -Eeuo pipefail

APP_DIR="${XIDER_APP_DIR:-/opt/xider}"
SERVICE="${XIDER_SERVICE:-xider-bot.service}"
BACKUP_DIR="${XIDER_BACKUP_DIR:-/var/backups/xider}"
INCOMING="${XIDER_UPDATE_BUNDLE:-${APP_DIR}/incoming/xider-source.zip}"
mkdir -p "${BACKUP_DIR}"

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run as root (the bot uses sudo)." >&2
  exit 2
fi

stamp() { date -u +%Y%m%dT%H%M%SZ; }
health() {
  systemctl is-enabled --quiet "${SERVICE}" && systemctl is-active --quiet "${SERVICE}"
}

backup_current() {
  local out="${BACKUP_DIR}/xider-$(stamp).tar.gz"
  tar -czf "${out}" -C "${APP_DIR}" --exclude='TG-BOT-SERVER/venv' --exclude='*.env' .
  echo "Backup created: ${out}"
}

latest_backup() {
  find "${BACKUP_DIR}" -maxdepth 1 -type f -name 'xider-*.tar.gz' -printf '%T@ %p\n' \
    | sort -nr | head -n1 | cut -d' ' -f2-
}

do_update() {
  [[ -f "${INCOMING}" ]] || { echo "No release bundle at ${INCOMING}; upload a bundle first."; exit 3; }
  local old backup stage
  old="$(latest_backup || true)"
  backup_current
  backup="$(latest_backup)"
  stage="$(mktemp -d /opt/xider-update.XXXXXX)"
  trap 'rm -rf "${stage}"' RETURN
  unzip -q "${INCOMING}" -d "${stage}"
  [[ -f "${stage}/TG-BOT-SERVER/requirements.txt" ]] || { echo "Invalid release bundle"; exit 4; }
  python3 -m compileall -q "${stage}/TG-BOT-SERVER" "${stage}/XGENT-WDS" "${stage}/XGENT-MCS"
  cp -a "${stage}/." "${APP_DIR}/"
  chown -R xider:xider "${APP_DIR}"
  systemctl daemon-reload
  systemctl restart "${SERVICE}"
  sleep 3
  if health; then
    echo "Update installed and health check passed."
    return 0
  fi
  echo "Health check failed; restoring ${backup}" >&2
  rm -rf "${APP_DIR}/TG-BOT-SERVER" "${APP_DIR}/XGENT-WDS" "${APP_DIR}/XGENT-MCS" "${APP_DIR}/deploy"
  tar -xzf "${backup}" -C "${APP_DIR}"
  chown -R xider:xider "${APP_DIR}"
  systemctl daemon-reload
  systemctl restart "${SERVICE}" || true
  exit 5
}

do_rollback() {
  local backup
  backup="$(latest_backup || true)"
  [[ -n "${backup}" && -f "${backup}" ]] || { echo "No XIDER backup found."; exit 6; }
  systemctl stop "${SERVICE}" || true
  rm -rf "${APP_DIR}/TG-BOT-SERVER" "${APP_DIR}/XGENT-WDS" "${APP_DIR}/XGENT-MCS" "${APP_DIR}/deploy"
  tar -xzf "${backup}" -C "${APP_DIR}"
  chown -R xider:xider "${APP_DIR}"
  systemctl daemon-reload
  systemctl restart "${SERVICE}"
  sleep 3
  health || { echo "Rollback health check failed."; exit 7; }
  echo "Rollback restored ${backup}."
}

case "${1:-}" in
  update) do_update ;;
  rollback) do_rollback ;;
  backup) backup_current ;;
  *) echo "Usage: $0 {update|rollback|backup}" >&2; exit 2 ;;
esac
