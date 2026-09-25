#!/usr/bin/env bash
set -Eeuo pipefail

INSTALL_ROOT="${XIDER_INSTALL_ROOT:-$HOME/XIDER}"
ENV_ROOT="${XIDER_ENV_ROOT:-$HOME/XIDER}"
ARCHIVE="${TMPDIR:-/tmp}/xider-main.zip"
URL="https://github.com/invinby/XIDER/archive/refs/heads/main.zip"

command -v curl >/dev/null || { echo "curl is required" >&2; exit 2; }
command -v unzip >/dev/null || { echo "unzip is required" >&2; exit 2; }
mkdir -p "${INSTALL_ROOT}"
curl -fsSL "$URL" -o "$ARCHIVE"
rm -rf "${INSTALL_ROOT}/git-ver"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp" "$ARCHIVE"' EXIT
unzip -q "$ARCHIVE" -d "$tmp"
downloaded="$(find "$tmp" -mindepth 1 -maxdepth 1 -type d | head -n1)"
[[ -n "$downloaded" ]] || { echo "GitHub archive is empty" >&2; exit 3; }
mv "$downloaded" "${INSTALL_ROOT}/git-ver"

agent_env="${INSTALL_ROOT}/git-ver/XGENT-MCS/.env"
if [[ -f "${ENV_ROOT}/XGENT-MCS/.env" ]]; then
  cp "${ENV_ROOT}/XGENT-MCS/.env" "${agent_env}"
elif [[ -f "${ENV_ROOT}/git-ver/XGENT-MCS/.env" ]]; then
  cp "${ENV_ROOT}/git-ver/XGENT-MCS/.env" "${agent_env}"
else
  server_host="${XIDER_SERVER_HOST:-141.145.152.174}"
  server_user="${XIDER_SERVER_USER:-ubuntu}"
  fetched_env="${tmp}/xider-agent.env"
  echo "Локальный .env не найден. Получаю настройки агента с ${server_user}@${server_host}."
  echo "Введи пароль SSH при запросе."

  if ! ssh -o StrictHostKeyChecking=accept-new \
      -o ConnectTimeout=15 \
      -o PreferredAuthentications=password \
      -o PubkeyAuthentication=no \
      "${server_user}@${server_host}" \
      "sudo -n sh -c 'grep -E \"^(SHARED_KEY|MQTT_BROKER|MQTT_PORT|MQTT_PREFIX|MQTT_TLS|MQTT_USERNAME|MQTT_PASSWORD|ENCRYPT_PAYLOAD)=\" /etc/xider/bot.env'" \
      2>"${tmp}/xider-ssh.err" \
      | tr -d '\r' \
      | awk '/^(SHARED_KEY|MQTT_BROKER|MQTT_PORT|MQTT_PREFIX|MQTT_TLS|MQTT_USERNAME|MQTT_PASSWORD|ENCRYPT_PAYLOAD)=/ { print }' \
      > "${fetched_env}"; then
    echo "Не удалось получить настройки с VPS:" >&2
    cat "${tmp}/xider-ssh.err" >&2
    exit 4
  fi

  if ! grep -q '^MQTT_PREFIX=' "${fetched_env}"; then
    printf 'MQTT_PREFIX=xgent/v1\n' >> "${fetched_env}"
  fi
  for required in SHARED_KEY MQTT_BROKER MQTT_PORT MQTT_TLS ENCRYPT_PAYLOAD; do
    if ! grep -q "^${required}=..*" "${fetched_env}"; then
      echo "На VPS отсутствует обязательный параметр ${required}." >&2
      exit 4
    fi
  done
  cp "${fetched_env}" "${agent_env}"
fi
chmod 600 "${agent_env}"
cd "${INSTALL_ROOT}/git-ver/XGENT-MCS"
./start_agent.sh
echo "XIDER macOS-агент готов."
