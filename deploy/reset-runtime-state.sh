#!/usr/bin/env bash
# Reset only runtime state. Environment secrets stay in /etc/xider/bot.env.
set -Eeuo pipefail

APP_DIR="${APP_DIR:-/opt/xider}"
RUNTIME_DIR="${APP_DIR}/TG-BOT-SERVER"
BACKUP_DIR="${BACKUP_DIR:-/var/backups/xider}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run as root via sudo." >&2
  exit 2
fi
if [[ ! -d "${RUNTIME_DIR}" ]]; then
  echo "Runtime directory does not exist: ${RUNTIME_DIR}" >&2
  exit 3
fi

install -d -m 0700 "${BACKUP_DIR}"
files=(known_devices.json bot_settings.json access_control.json admin_audit.jsonl audit.log)
present=()
for file in "${files[@]}"; do
  [[ -f "${RUNTIME_DIR}/${file}" ]] && present+=("${file}")
done
if ((${#present[@]})); then
  tar -C "${RUNTIME_DIR}" -czf "${BACKUP_DIR}/xider-runtime-${STAMP}.tar.gz" "${present[@]}"
  chmod 0600 "${BACKUP_DIR}/xider-runtime-${STAMP}.tar.gz"
fi

systemctl stop xider-bot.service || true
printf '{}\n' > "${RUNTIME_DIR}/known_devices.json"
cat > "${RUNTIME_DIR}/bot_settings.json" <<'EOF'
{
  "notify_online": true,
  "notify_offline": true,
  "notify_battery_low": true,
  "quiet_from": "",
  "quiet_to": "",
  "report_hour": "",
  "admins": [],
  "blocked_ids": [],
  "ui_style": "technical",
  "require_device_approval": true
}
EOF
printf '{"users": {}}\n' > "${RUNTIME_DIR}/access_control.json"
: > "${RUNTIME_DIR}/admin_audit.jsonl"
: > "${RUNTIME_DIR}/audit.log"
chown xider:xider "${RUNTIME_DIR}"/{known_devices.json,bot_settings.json,access_control.json,admin_audit.jsonl,audit.log}
chmod 0600 "${RUNTIME_DIR}"/{known_devices.json,bot_settings.json,access_control.json,admin_audit.jsonl,audit.log}

echo "Runtime state reset. Backup: ${BACKUP_DIR}/xider-runtime-${STAMP}.tar.gz"
echo "Environment files and credentials were not changed."
