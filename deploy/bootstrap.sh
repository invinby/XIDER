#!/usr/bin/env bash
set -Eeuo pipefail

INSTALL_ROOT="${XIDER_INSTALL_ROOT:-$HOME/XIDER}"
ENV_ROOT="${XIDER_ENV_ROOT:-$HOME/XIDER}"
BRANCH="${XIDER_BRANCH:-main}"
SERVER_HOST="${XIDER_SERVER_HOST:-141.145.152.174}"
SERVER_USER="${XIDER_SERVER_USER:-ubuntu}"
TARGET="${INSTALL_ROOT}/git-ver"

if [[ ! "$BRANCH" =~ ^[A-Za-z0-9._/-]+$ ]]; then
  echo "Invalid XIDER_BRANCH" >&2
  exit 2
fi
command -v curl >/dev/null || { echo "curl is required" >&2; exit 2; }
command -v unzip >/dev/null || { echo "unzip is required" >&2; exit 2; }
mkdir -p "$INSTALL_ROOT"
stage="$(mktemp -d "${INSTALL_ROOT}/.xider-install.XXXXXX")"
trap 'rm -rf "$stage"' EXIT

echo "Получаю XIDER, branch=$BRANCH"
curl -fsSL "https://github.com/invinby/XIDER/archive/refs/heads/${BRANCH}.zip" -o "$stage/source.zip"
unzip -q "$stage/source.zip" -d "$stage"
downloaded="$(find "$stage" -mindepth 1 -maxdepth 1 -type d ! -name '.xider-install.*' | head -n1)"
[[ -n "$downloaded" && -f "$downloaded/XGENT-MCS/start_agent.sh" ]] || {
  echo "В архиве не найден XGENT-MCS/start_agent.sh" >&2; exit 3;
}
mkdir "$stage/new"
cp -R "$downloaded/." "$stage/new/"

# Берём существующий локальный .env, не трогая ключи и состояние в ~/.xgent.
preserved_env="$stage/agent.env"
for candidate in \
  "${TARGET}/XGENT-MCS/.env" \
  "${ENV_ROOT}/XGENT-MCS/.env" \
  "${ENV_ROOT}/git-ver/XGENT-MCS/.env"; do
  if [[ -f "$candidate" ]]; then cp "$candidate" "$preserved_env"; break; fi
done

agent_env="$stage/new/XGENT-MCS/.env"
if [[ -f "$preserved_env" ]]; then
  cp "$preserved_env" "$agent_env"
else
  fetched_env="$stage/fetched.env"
  echo "Локального .env нет; читаю только настройки агента с ${SERVER_USER}@${SERVER_HOST}."
  echo "Если спросит пароль SSH — введи пароль учётной записи VPS."
  if ! ssh -o StrictHostKeyChecking=accept-new -o ConnectTimeout=15 \
      -o PreferredAuthentications=password -o PubkeyAuthentication=no \
      "${SERVER_USER}@${SERVER_HOST}" \
      "sudo -n sh -c 'grep -E \"^(SHARED_KEY|MQTT_BROKER|MQTT_PORT|MQTT_PREFIX|MQTT_TLS|MQTT_USERNAME|MQTT_PASSWORD|ENCRYPT_PAYLOAD)=\" /etc/xider/bot.env'" \
      2>"$stage/ssh.err" | tr -d '\r' | awk '/^(SHARED_KEY|MQTT_BROKER|MQTT_PORT|MQTT_PREFIX|MQTT_TLS|MQTT_USERNAME|MQTT_PASSWORD|ENCRYPT_PAYLOAD)=/ { print }' > "$fetched_env"; then
    echo "Не удалось получить параметры с VPS:" >&2
    cat "$stage/ssh.err" >&2
    exit 4
  fi
  if ! grep -q '^MQTT_PREFIX=' "$fetched_env"; then printf 'MQTT_PREFIX=xgent/v1\n' >> "$fetched_env"; fi
  for required in SHARED_KEY MQTT_BROKER MQTT_PORT MQTT_TLS ENCRYPT_PAYLOAD; do
    if ! grep -q "^${required}=..*" "$fetched_env"; then
      echo "В настройках VPS отсутствует ${required}; ничего не устанавливал." >&2
      exit 4
    fi
  done
  cp "$fetched_env" "$agent_env"
fi

# Пин обновлений этого Mac на ту же ветку, откуда сейчас ставится агент.
awk -v branch="$BRANCH" '
  BEGIN { written=0 }
  /^XIDER_UPDATE_BRANCH=/ { if (!written) print "XIDER_UPDATE_BRANCH=" branch; written=1; next }
  { print }
  END { if (!written) print "XIDER_UPDATE_BRANCH=" branch }
' "$agent_env" > "$stage/env.updated"
mv "$stage/env.updated" "$agent_env"
chmod 600 "$agent_env"

new_agent="$stage/new/XGENT-MCS"
chmod +x "$new_agent/start_agent.sh" "$new_agent/stop_agent.sh" "$new_agent/start_guardian.sh"

# Keep the prior install for rollback; never recursively delete the live checkout.
backup="${INSTALL_ROOT}/git-ver.previous.$(date +%Y%m%d%H%M%S).$$"
launchctl bootout "gui/$(id -u)" "$HOME/Library/LaunchAgents/com.xgent.agent.plist" >/dev/null 2>&1 || true
launchctl bootout "gui/$(id -u)" "$HOME/Library/LaunchAgents/com.xider.guardian.plist" >/dev/null 2>&1 || true
if [[ -e "$TARGET" ]]; then mv "$TARGET" "$backup"; fi
if ! mv "$stage/new" "$TARGET"; then
  [[ ! -e "$backup" ]] || mv "$backup" "$TARGET"
  echo "Не удалось активировать новую папку; прежняя установка сохранена." >&2
  exit 5
fi

if ! (cd "$TARGET/XGENT-MCS" && bash ./start_agent.sh && bash ./start_guardian.sh); then
  echo "Запуск не прошёл; возвращаю предыдущую версию." >&2
  launchctl bootout "gui/$(id -u)" "$HOME/Library/LaunchAgents/com.xgent.agent.plist" >/dev/null 2>&1 || true
  launchctl bootout "gui/$(id -u)" "$HOME/Library/LaunchAgents/com.xider.guardian.plist" >/dev/null 2>&1 || true
  mv "$TARGET" "${TARGET}.failed.$(date +%Y%m%d%H%M%S)"
  [[ ! -e "$backup" ]] || mv "$backup" "$TARGET"
  if [[ -x "$TARGET/XGENT-MCS/start_agent.sh" ]]; then
    (cd "$TARGET/XGENT-MCS" && bash ./start_agent.sh || true; bash ./start_guardian.sh || true)
  fi
  exit 6
fi

echo "Установка завершена. Исходники: $TARGET"
[[ ! -e "$backup" ]] || echo "Предыдущая установка сохранена: $backup"
echo "Проверка: cd \"$TARGET/XGENT-MCS\" && bash ./start_agent.sh --status && bash ./start_guardian.sh --status"
