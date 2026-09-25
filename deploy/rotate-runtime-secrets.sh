#!/usr/bin/env bash
set -Eeuo pipefail

ENV_FILE="${ENV_FILE:-/etc/xider/bot.env}"
BACKUP_DIR="${BACKUP_DIR:-/var/backups/xider/secrets}"
if [[ "${EUID}" -ne 0 ]]; then echo "Run as root." >&2; exit 2; fi
[[ -r "${ENV_FILE}" ]] || { echo "Missing ${ENV_FILE}." >&2; exit 3; }
if [[ -z "${NEW_BOT_TOKEN:-}" && -z "${NEW_SHARED_KEY:-}" && -z "${NEW_MQTT_PASSWORD:-}" ]]; then
  echo "Set at least one of NEW_BOT_TOKEN, NEW_SHARED_KEY, NEW_MQTT_PASSWORD." >&2
  exit 4
fi

mkdir -p "${BACKUP_DIR}"
backup="${BACKUP_DIR}/bot.env.$(date -u +%Y%m%dT%H%M%SZ)"
install -m 0600 "${ENV_FILE}" "${backup}"

python3 - "${ENV_FILE}" <<'PY'
import os, pathlib, sys
path = pathlib.Path(sys.argv[1])
replacements = {
    "BOT_TOKEN": os.environ.get("NEW_BOT_TOKEN"),
    "SHARED_KEY": os.environ.get("NEW_SHARED_KEY"),
    "MQTT_PASSWORD": os.environ.get("NEW_MQTT_PASSWORD"),
}
lines = path.read_text(encoding="utf-8").splitlines()
for key, value in replacements.items():
    if value:
        for i, line in enumerate(lines):
            if line.startswith(key + "="):
                lines[i] = key + "=" + value
                break
        else:
            lines.append(key + "=" + value)
tmp = path.with_suffix(path.suffix + ".tmp")
tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
tmp.chmod(0o640)
tmp.replace(path)
PY

systemctl restart xider-bot.service
sleep 3
if ! systemctl is-active --quiet xider-bot.service; then
  install -m 0640 "${backup}" "${ENV_FILE}"
  systemctl restart xider-bot.service || true
  echo "Rotation failed; previous env restored from ${backup}." >&2
  exit 5
fi
echo "Secrets rotated. Previous env backup: ${backup}"
