#!/usr/bin/env bash
set -Eeuo pipefail

INSTALL_ROOT="${XIDER_INSTALL_ROOT:-$HOME/XIDER}"
ENV_ROOT="${XIDER_ENV_ROOT:-$HOME/XIDER}"
BRANCH="${XIDER_BRANCH:-main}"
REF="${XIDER_REF:-}"
SERVER_HOST="${XIDER_SERVER_HOST:-141.145.152.174}"
SERVER_USER="${XIDER_SERVER_USER:-ubuntu}"
TARGET="${INSTALL_ROOT}/git-ver"
SOURCE_ARCHIVE="${XIDER_SOURCE_ARCHIVE:-}"

if [[ ! "$BRANCH" =~ ^[A-Za-z0-9._/-]+$ ]]; then
  echo "Invalid XIDER_BRANCH" >&2
  exit 2
fi
if [[ -n "$REF" && ! "$REF" =~ ^[0-9a-fA-F]{40}([0-9a-fA-F]{24})?$ ]]; then
  echo "XIDER_REF must be a full 40- or 64-character commit ID" >&2
  exit 2
fi
command -v curl >/dev/null || { echo "curl is required" >&2; exit 2; }
command -v unzip >/dev/null || { echo "unzip is required" >&2; exit 2; }
mkdir -p "$INSTALL_ROOT"
stage="$(mktemp -d "${INSTALL_ROOT}/.xider-install.XXXXXX")"
trap 'rm -rf "$stage"' EXIT

if [[ -n "$REF" ]]; then
  echo "Получаю XIDER из зафиксированного commit=$REF"
else
  echo "Получаю XIDER, branch=$BRANCH"
fi
if [[ -n "$SOURCE_ARCHIVE" ]]; then
  [[ -f "$SOURCE_ARCHIVE" ]] || { echo "XIDER_SOURCE_ARCHIVE не найден" >&2; exit 2; }
  cp "$SOURCE_ARCHIVE" "$stage/source.zip"
else
  if [[ -n "$REF" ]]; then
    archive_url="https://github.com/invinby/XIDER/archive/${REF}.zip"
  else
    archive_url="https://github.com/invinby/XIDER/archive/refs/heads/${BRANCH}.zip"
  fi
  curl --fail --silent --show-error --location --max-time 90 \
    "$archive_url" -o "$stage/source.zip"
fi
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
  echo "Сначала пробую SSH-ключ или ssh-agent; если их нет — введи пароль VPS при запросе."
  ssh_args=(-o StrictHostKeyChecking=accept-new -o ConnectTimeout=15)
  ssh_key="${XIDER_SSH_KEY:-}"
  if [[ -z "$ssh_key" && -r "$HOME/.ssh/xider" ]]; then
    ssh_key="$HOME/.ssh/xider"
  fi
  if [[ -n "$ssh_key" ]]; then
    [[ -r "$ssh_key" ]] || { echo "SSH-ключ не найден или недоступен: $ssh_key" >&2; exit 4; }
    ssh_args+=(-i "$ssh_key" -o IdentitiesOnly=yes)
  fi
  if ! ssh "${ssh_args[@]}" \
      "${SERVER_USER}@${SERVER_HOST}" \
      "sudo -n sh -c 'grep -E \"^(SHARED_KEY|MQTT_BROKER|MQTT_PORT|MQTT_PREFIX|MQTT_TLS|MQTT_USERNAME|MQTT_PASSWORD|ENCRYPT_PAYLOAD)=\" /etc/xider/bot.env'" \
      2>"$stage/ssh.err" | tr -d '\r' | awk '/^(SHARED_KEY|MQTT_BROKER|MQTT_PORT|MQTT_PREFIX|MQTT_TLS|MQTT_USERNAME|MQTT_PASSWORD|ENCRYPT_PAYLOAD)=/ { print }' > "$fetched_env"; then
    echo "Не удалось получить параметры с VPS:" >&2
    cat "$stage/ssh.err" >&2
    exit 4
  fi
  for required in SHARED_KEY MQTT_BROKER MQTT_PORT MQTT_TLS ENCRYPT_PAYLOAD; do
    if ! grep -q "^${required}=..*" "$fetched_env"; then
      echo "В настройках VPS отсутствует ${required}; ничего не устанавливал." >&2
      exit 4
    fi
  done
  cp "$fetched_env" "$agent_env"
fi

read_env_value() {
  local name="$1" line value='' count=0 first last
  while IFS= read -r line || [[ -n "$line" ]]; do
    line="${line%$'\r'}"
    [[ "$line" == "$name="* ]] || continue
    count=$((count + 1))
    value="${line#*=}"
  done < "$agent_env"
  [[ "$count" -eq 1 ]] || return 1
  value="${value#"${value%%[![:space:]]*}"}"
  value="${value%"${value##*[![:space:]]}"}"
  if [[ ${#value} -ge 2 ]]; then
    first="${value:0:1}"
    last="${value: -1}"
    if [[ ( "$first" == '"' && "$last" == '"' ) || ( "$first" == "'" && "$last" == "'" ) ]]; then
      value="${value:1:${#value}-2}"
    fi
  fi
  [[ -n "${value//[[:space:]]/}" ]] || return 1
  printf '%s' "$value"
}

for required in SHARED_KEY MQTT_BROKER MQTT_PORT MQTT_TLS MQTT_USERNAME MQTT_PASSWORD ENCRYPT_PAYLOAD; do
  if ! value="$(read_env_value "$required")"; then
    echo "В конфигурации агента параметр ${required} отсутствует, пустой или указан несколько раз; ничего не устанавливал." >&2
    exit 4
  fi
done

mqtt_tls="$(read_env_value MQTT_TLS | tr '[:upper:]' '[:lower:]')"
if [[ "$mqtt_tls" != true && "$mqtt_tls" != 1 && "$mqtt_tls" != yes ]]; then
  echo "Для установки агента требуется MQTT_TLS=true; ничего не устанавливал." >&2
  exit 4
fi
encrypt_payload="$(read_env_value ENCRYPT_PAYLOAD | tr '[:upper:]' '[:lower:]')"
if [[ "$encrypt_payload" != true && "$encrypt_payload" != 1 && "$encrypt_payload" != yes ]]; then
  echo "Для установки агента требуется ENCRYPT_PAYLOAD=true; ничего не устанавливал." >&2
  exit 4
fi
mqtt_port="$(read_env_value MQTT_PORT)"
if [[ ! "$mqtt_port" =~ ^[0-9]{1,5}$ ]] || (( mqtt_port < 1 || mqtt_port > 65535 )); then
  echo "MQTT_PORT должен быть числом от 1 до 65535; ничего не устанавливал." >&2
  exit 4
fi
shared_key="$(read_env_value SHARED_KEY)"
if [[ "$shared_key" == XGENT-2026-shared-secret ]]; then
  echo "В конфигурации указан публичный тестовый SHARED_KEY; ничего не устанавливал." >&2
  exit 4
fi

# Match the same topic-prefix default as bot/config.py and both agents. An
# explicit empty or duplicated value is rejected instead of silently routing
# the client to a different MQTT namespace.
prefix_count="$(grep -c '^MQTT_PREFIX=' "$agent_env" || true)"
if [[ "$prefix_count" -eq 0 ]]; then
  printf 'MQTT_PREFIX=xgent/v1\n' >> "$agent_env"
elif [[ "$prefix_count" -ne 1 ]]; then
  echo "MQTT_PREFIX указан несколько раз; установку не менял." >&2
  exit 4
else
  prefix_value="$(sed -n 's/^MQTT_PREFIX=//p' "$agent_env" | sed 's/^[[:space:]]*//; s/[[:space:]]*$//')"
  if [[ "$prefix_value" == \"*\" || "$prefix_value" == \'*\' ]]; then
    prefix_value="${prefix_value:1:${#prefix_value}-2}"
  fi
  if [[ ! "$prefix_value" =~ ^[A-Za-z0-9._/-]+$ ]]; then
    echo "MQTT_PREFIX пустой или имеет неподдерживаемый формат; установку не менял." >&2
    exit 4
  fi
fi
for required in SHARED_KEY MQTT_BROKER MQTT_PORT MQTT_PREFIX MQTT_TLS ENCRYPT_PAYLOAD; do
  if ! grep -q "^${required}=..*" "$agent_env"; then
    echo "В конфигурации агента отсутствует ${required}; ничего не устанавливал." >&2
    exit 4
  fi
done

# Remove the retired mutable-branch updater setting from carried-forward envs.
awk '!/^XIDER_UPDATE_BRANCH=/' "$agent_env" > "$stage/env.updated"
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

if ! (cd "$TARGET/XGENT-MCS" && bash ./start_agent.sh && bash ./start_guardian.sh &&
      bash ./start_agent.sh --status && bash ./start_guardian.sh --status); then
  echo "Запуск не прошёл; возвращаю предыдущую версию." >&2
  launchctl bootout "gui/$(id -u)" "$HOME/Library/LaunchAgents/com.xgent.agent.plist" >/dev/null 2>&1 || true
  launchctl bootout "gui/$(id -u)" "$HOME/Library/LaunchAgents/com.xider.guardian.plist" >/dev/null 2>&1 || true
  failed="${TARGET}.failed.$(date +%Y%m%d%H%M%S)"
  if mv "$TARGET" "$failed"; then
    rm -f "$failed/XGENT-MCS/.env" || echo "Предупреждение: не удалось убрать .env из $failed" >&2
  else
    echo "Не удалось изолировать неисправную версию $TARGET; автоматический откат может быть неполным." >&2
  fi
  if [[ -e "$backup" && ! -e "$TARGET" ]]; then
    if ! mv "$backup" "$TARGET"; then
      echo "КРИТИЧНО: не удалось вернуть резервную версию из $backup" >&2
    fi
  fi
  if [[ -x "$TARGET/XGENT-MCS/start_agent.sh" ]]; then
    (cd "$TARGET/XGENT-MCS" && bash ./start_agent.sh || true; bash ./start_guardian.sh || true)
  fi
  [[ ! -e "$backup" ]] || echo "Предыдущая версия возвращена: $TARGET"
  exit 6
fi

echo "Установка завершена. Исходники: $TARGET"
[[ ! -e "$backup" ]] || echo "Предыдущая установка сохранена: $backup"
echo "Проверка: cd \"$TARGET/XGENT-MCS\" && bash ./start_agent.sh --status && bash ./start_guardian.sh --status"
