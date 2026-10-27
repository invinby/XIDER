#!/usr/bin/env bash
set -Eeuo pipefail

INSTALL_ROOT="${XIDER_INSTALL_ROOT:-$HOME/XIDER}"
ENV_ROOT="${XIDER_ENV_ROOT:-$HOME/XIDER}"
BRANCH="${XIDER_BRANCH:-main}"
REF="${XIDER_REF:-}"
SERVER_HOST="${XIDER_SERVER_HOST:-16.16.200.207}"
SERVER_USER="${XIDER_SERVER_USER:-ubuntu}"
TARGET="${INSTALL_ROOT}/git-ver"
SOURCE_ARCHIVE="${XIDER_SOURCE_ARCHIVE:-}"
RELEASE_TAG="${XIDER_RELEASE_TAG:-}"

if [[ ! "$BRANCH" =~ ^[A-Za-z0-9._/-]+$ ]]; then
  echo "Invalid XIDER_BRANCH" >&2
  exit 2
fi
if [[ -n "$REF" && ! "$REF" =~ ^[0-9a-fA-F]{40}([0-9a-fA-F]{24})?$ ]]; then
  echo "XIDER_REF must be a full 40- or 64-character commit ID" >&2
  exit 2
fi
if [[ -n "$RELEASE_TAG" && ! "$RELEASE_TAG" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  echo "XIDER_RELEASE_TAG must be vMAJOR.MINOR.PATCH" >&2
  exit 2
fi
command -v curl >/dev/null || { echo "curl is required" >&2; exit 2; }
command -v unzip >/dev/null || { echo "unzip is required" >&2; exit 2; }
command -v python3 >/dev/null || { echo "python3 is required" >&2; exit 2; }
mkdir -p "$INSTALL_ROOT"
INSTALL_ROOT="$(cd "$INSTALL_ROOT" && pwd -P)"
TARGET="${INSTALL_ROOT}/git-ver"
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
if ! python3 - "$stage/source.zip" <<'PY'
from pathlib import PurePosixPath
from stat import S_IFDIR, S_IFREG, S_IFLNK
from unicodedata import normalize
from zipfile import ZipFile
import sys

MAX_UNCOMPRESSED_SIZE = 1024 * 1024 * 1024
MAX_MEMBER_COUNT = 20_000
archive_path = sys.argv[1]
seen = set()
files = set()
implicit_directories = set()
total_size = 0

try:
    with ZipFile(archive_path) as archive:
        members = archive.infolist()
        if len(members) > MAX_MEMBER_COUNT:
            raise ValueError("archive contains too many entries")
        for info in members:
            name = info.filename
            path = PurePosixPath(name)
            raw_parts = name.rstrip("/").split("/")
            if (
                not name
                or "\\" in name
                or ":" in name
                or path.is_absolute()
                or not raw_parts
                or any(part in ("", ".", "..") for part in raw_parts)
            ):
                raise ValueError(f"unsafe archive path: {name!r}")

            mode = (info.external_attr >> 16) & 0o170000
            if mode == S_IFLNK:
                raise ValueError(f"symbolic link is not allowed: {name!r}")
            if mode not in (0, S_IFDIR, S_IFREG):
                raise ValueError(f"special file is not allowed: {name!r}")

            normalized_parts = [normalize("NFD", part).casefold() for part in raw_parts]
            key = "/".join(normalized_parts)
            if key in seen:
                raise ValueError(f"duplicate normalized path: {name!r}")

            for index in range(1, len(normalized_parts)):
                parent = "/".join(normalized_parts[:index])
                if parent in files:
                    raise ValueError(f"file is used as a directory: {name!r}")
                implicit_directories.add(parent)

            is_directory = info.is_dir() or mode == S_IFDIR
            if not is_directory and key in implicit_directories:
                raise ValueError(f"file conflicts with nested paths: {name!r}")
            if not is_directory:
                files.add(key)
            seen.add(key)

            total_size += info.file_size
            if total_size > MAX_UNCOMPRESSED_SIZE:
                raise ValueError("archive exceeds the 1 GiB uncompressed limit")
except Exception as exc:
    print(f"XIDER source archive rejected: {exc}", file=sys.stderr)
    raise SystemExit(1)
PY
then
  echo "Архив XIDER отклонён до распаковки; действующая установка не менялась." >&2
  exit 3
fi
unzip -q "$stage/source.zip" -d "$stage"
downloaded="$(find "$stage" -mindepth 1 -maxdepth 1 -type d ! -name '.xider-install.*' | head -n1)"
[[ -n "$downloaded" && -f "$downloaded/XGENT-MCS/start_agent.sh" ]] || {
  echo "В архиве не найден XGENT-MCS/start_agent.sh" >&2; exit 3;
}
mkdir "$stage/new"
if [[ -n "$RELEASE_TAG" ]]; then
  mkdir -p "$stage/new/XGENT-MCS"
else
  cp -R "$downloaded/." "$stage/new/"
fi

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
  public_env="$stage/public.env"
  ssh_args=(-o BatchMode=yes -o StrictHostKeyChecking=accept-new -o ConnectTimeout=5 -o ServerAliveInterval=5 -o ServerAliveCountMax=1)
  ssh_key="${XIDER_SSH_KEY:-}"
  if [[ -z "$ssh_key" && -r "$HOME/.ssh/xider" ]]; then ssh_key="$HOME/.ssh/xider"; fi
  if [[ -n "$ssh_key" && -r "$ssh_key" ]]; then ssh_args+=(-i "$ssh_key" -o IdentitiesOnly=yes); fi
  if ssh "${ssh_args[@]}" "${SERVER_USER}@${SERVER_HOST}" \
      "sudo -n sh -c 'grep -E \"^XIDER_COMMAND_VERIFY_KEYSET_B64=\" /etc/xider/bot.env || test \$? -eq 1'" \
      2>"$stage/public-ssh.err" | tr -d '\r' | awk '/^XIDER_COMMAND_VERIFY_KEYSET_B64=/ { print }' > "$public_env"; then
    # Merge only public verification data into the staged copy. Existing
    # device credentials and rollback configuration stay authoritative.
    python3 - "$agent_env" "$public_env" <<'PY'
from pathlib import Path
import re
import sys
env, public = map(Path, sys.argv[1:])
pattern = re.compile(r'^\s*(?:export\s+)?XIDER_COMMAND_VERIFY_KEYSET_B64\s*=')
lines = [line for line in env.read_text(encoding='utf-8-sig').splitlines() if not pattern.match(line)]
entries = public.read_text().splitlines()
if len(entries) == 1:
    value = entries[0].partition('=')[2]
    if len(value) <= 1024 and re.fullmatch(r'[A-Za-z0-9_-]+', value):
        lines.append('XIDER_COMMAND_VERIFY_KEYSET_B64=' + value)
env.write_text('\n'.join(lines) + '\n')
PY
  else
    echo 'Публичный keyset сейчас недоступен; использую локальную конфигурацию.'
  fi
elif [[ -n "${XIDER_DOCK_URL:-}" ]]; then
  echo 'Локальной конфигурации нет; подтверждаю X-DOCK-подключение через Telegram.'
  pairing_venv="$stage/x-dock-venv"
  if ! python3 -m venv "$pairing_venv" ||
     ! "$pairing_venv/bin/python3" -m pip install --disable-pip-version-check --quiet 'cryptography>=42.0'; then
    echo 'Не удалось подготовить криптографию X-DOCK; действующая установка не менялась.' >&2
    exit 4
  fi
  if ! "$pairing_venv/bin/python3" "$stage/new/deploy/x_dock_client.py" \
      --base-url "$XIDER_DOCK_URL" --env-output "$agent_env" --timeout 600; then
    echo 'X-DOCK не выдал настройки; действующая установка не менялась.' >&2
    exit 4
  fi
  chmod 600 "$agent_env"
else
  fetched_env="$stage/fetched.env"
  echo "Локального .env нет; читаю только настройки агента с ${SERVER_USER}@${SERVER_HOST}."
  echo "Читаю настройки только через готовый SSH-ключ/ssh-agent; парольный вход отключён."
  ssh_args=(-o BatchMode=yes -o PreferredAuthentications=publickey
    -o PasswordAuthentication=no -o KbdInteractiveAuthentication=no
    -o StrictHostKeyChecking=accept-new -o ConnectTimeout=15
    -o ServerAliveInterval=10 -o ServerAliveCountMax=2)
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
      "sudo -n sh -c 'grep -E \"^(SHARED_KEY|MQTT_BROKER|MQTT_PORT|MQTT_PREFIX|MQTT_TLS|MQTT_USERNAME|MQTT_PASSWORD|ENCRYPT_PAYLOAD|XIDER_COMMAND_VERIFY_KEYSET_B64)=\" /etc/xider/bot.env'" \
      2>"$stage/ssh.err" | tr -d '\r' | awk '/^(SHARED_KEY|MQTT_BROKER|MQTT_PORT|MQTT_PREFIX|MQTT_TLS|MQTT_USERNAME|MQTT_PASSWORD|ENCRYPT_PAYLOAD|XIDER_COMMAND_VERIFY_KEYSET_B64)=/ { print }' > "$fetched_env"; then
    echo "Не удалось получить параметры с VPS без пароля. Нужен готовый локальный .env или настроенный SSH-ключ; парольный запрос отключён." >&2
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

# The signed release installs only compiled apps and their thin control scripts.
# This temporary runtime is for verifying release metadata; it is not linked
# into the active install. Development installs retain their movable-outside
# runtime and source checkout behavior.
if [[ -n "$RELEASE_TAG" ]]; then
  runtime="$(mktemp -d "$stage/runtime.XXXXXX")"
else
  mkdir -p "$INSTALL_ROOT/runtimes"
  runtime="$(mktemp -d "$INSTALL_ROOT/runtimes/macos.XXXXXX")"
fi
echo "Подготавливаю временные инструменты проверки; действующий агент пока не останавливаю."
if ! python3 -m venv "$runtime"; then
  echo "Подготовка окружения не прошла; прежняя установка не менялась." >&2
  echo "Диагностическое окружение сохранено: $runtime" >&2
  exit 5
fi
if [[ -n "$RELEASE_TAG" ]]; then
  if ! "$runtime/bin/python3" -m pip install --disable-pip-version-check --quiet 'cryptography>=42.0'; then
    echo "Не удалось подготовить проверку подписи; прежняя установка не менялась." >&2
    exit 5
  fi
else
  if ! "$runtime/bin/python3" -m pip install --disable-pip-version-check -r "$new_agent/requirements.txt" ||
     ! "$runtime/bin/python3" -m compileall -q "$new_agent"; then
    echo "Подготовка окружения не прошла; прежняя установка не менялась." >&2
    echo "Диагностическое окружение сохранено: $runtime" >&2
    exit 5
  fi
  ln -s "$runtime" "$new_agent/venv"
fi

recovery_slot=''
if [[ -n "$RELEASE_TAG" ]]; then
  echo "Проверяю подписанный macOS-бандл $RELEASE_TAG до остановки текущей версии."
  signed_bundle="$stage/XGENT-MCS-macos-bundle.zip"
  bundle_manifest="$stage/mac-bundle-manifest.json"
  (
    cd "$new_agent"
    PYTHONPATH="$downloaded/XGENT-MCS" "$runtime/bin/python3" - "$bundle_manifest" "$signed_bundle" "$RELEASE_TAG" <<'PY'
from pathlib import Path
import sys
from update_package import download_verified_release_asset

manifest, bundle, tag = sys.argv[1:]
download_verified_release_asset(Path(manifest), Path(bundle), "XGENT-MCS-macos-bundle.zip", release_tag=tag)
PY
  ) || { echo "Подписанный macOS-бандл не прошёл проверку; действующая установка не менялась." >&2; exit 5; }
  "$runtime/bin/python3" - "$signed_bundle" <<'PY'
from pathlib import PurePosixPath
from zipfile import ZipFile
import sys

allowed_apps = {"XIDER.app", "XIDER-Guard-Keeper.app", "XIDER-Layers.app"}
allowed_files = {".env.example"}
allowed = allowed_apps | allowed_files
total = 0
with ZipFile(sys.argv[1]) as archive:
    entries = archive.infolist()
    if not entries or len(entries) > 100_000:
        raise SystemExit("Бандл пустой или содержит слишком много записей.")
    for entry in entries:
        path = PurePosixPath(entry.filename)
        if path.is_absolute() or ".." in path.parts or not path.parts or path.parts[0] not in allowed:
            raise SystemExit(f"Недопустимый путь в macOS-бандле: {entry.filename!r}")
        if len(path.parts) == 1 and path.parts[0] in allowed_apps and not entry.is_dir():
            raise SystemExit(f"Ожидался каталог app: {entry.filename!r}")
        if len(path.parts) > 1 and path.parts[0] in allowed_files:
            raise SystemExit(f"Файл бандла содержит вложенный путь: {entry.filename!r}")
        if entry.flag_bits & 1:
            raise SystemExit("Зашифрованные записи в macOS-бандле запрещены.")
        total += entry.file_size
        if total > 2 * 1024 * 1024 * 1024:
            raise SystemExit("Распакованный macOS-бандл превышает лимит 2 ГБ.")
    if not allowed_apps.issubset({PurePosixPath(entry.filename).parts[0] for entry in entries}):
        raise SystemExit("В подписанном macOS-бандле не хватает обязательного XIDER-компонента.")
PY
  mkdir -p "$stage/macos-bundle"
  ditto -x -k "$signed_bundle" "$stage/macos-bundle"
  if [[ ! -f "$stage/macos-bundle/.env.example" ]]; then
    echo "В подписанном macOS-бандле отсутствует .env.example." >&2
    exit 5
  fi
  cp "$stage/macos-bundle/.env.example" "$new_agent/.env.example"
  for app in XIDER.app XIDER-Guard-Keeper.app XIDER-Layers.app; do
    codesign --verify --deep --strict "$stage/macos-bundle/$app"
    xcrun stapler validate "$stage/macos-bundle/$app"
    mv "$stage/macos-bundle/$app" "$new_agent/$app"
  done
  # The signed release runs the compiled layer supervisor only. Keep the
  # source fallback in development checkouts, never in a release installation.
  rm -f "$new_agent/xider_layers.py"
  : > "$new_agent/.xider-signed-release"
  chmod 600 "$new_agent/.xider-signed-release"
  if find "$new_agent" -type f \( -name '*.py' -o -name '*.pyc' -o -name '*.pyo' -o -name '*.ps1' -o -name '*.psm1' -o -name '*.bat' -o -name '*.cmd' -o -name '*.sh' \) -print -quit | grep -q .; then
    echo "Unexpected plaintext source or helper script in signed macOS install; refusing activation." >&2
    exit 5
  fi
  echo "Подписанные Agent, Guard Keeper и Safari/Siri подготовлены без Python-исходников."
else
  chmod +x "$new_agent/start_agent.sh" "$new_agent/stop_agent.sh" "$new_agent/start_guardian.sh" "$new_agent/start_safari.sh" "$new_agent/start_siri.sh"
  echo "Development-установка: исходники остаются в локальном checkout."
fi

# Keep the prior install for rollback; never recursively delete the live checkout.
backup="${INSTALL_ROOT}/git-ver.previous.$(date +%Y%m%d%H%M%S).$$"
layer_state_snapshot="$stage/layer-state-preserved.json"
python3 - "$HOME/.xgent/layer-state.json" "$layer_state_snapshot" <<'PY'
import json, pathlib, sys
source, destination = map(pathlib.Path, sys.argv[1:])
if source.is_symlink():
    raise SystemExit("Состояние Safari/Siri является symlink; обновление остановлено до замены файлов.")
state = {"safari": True, "siri": True}
if source.is_file():
    if source.stat().st_size > 4096:
        raise SystemExit("Состояние Safari/Siri превышает допустимый размер.")
    try:
        saved = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SystemExit(f"Не удалось прочитать состояние Safari/Siri: {exc}")
    if not isinstance(saved, dict):
        raise SystemExit("Некорректное состояние Safari/Siri.")
    for role in state:
        if role in saved:
            if type(saved[role]) is not bool:
                raise SystemExit(f"Некорректный флаг включения {role}.")
            state[role] = saved[role]
destination.write_text(json.dumps(state, separators=(",", ":")) + "\n", encoding="utf-8")
PY
layer_enabled() {
  "$runtime/bin/python3" - "$layer_state_snapshot" "$1" <<'PY'
import json, pathlib, sys
try:
    state = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
except (OSError, ValueError):
    raise SystemExit(1)
raise SystemExit(0 if isinstance(state, dict) and state.get(sys.argv[2]) is True else 1)
PY
}
start_enabled_layers() {
  local directory="$1"
  local layer_binary="$directory/XIDER-Layers.app/Contents/MacOS/XIDER-Layers"
  if [[ -x "$layer_binary" ]]; then
    if { ! layer_enabled safari || "$layer_binary" --service safari start; } &&
       { ! layer_enabled siri || "$layer_binary" --service siri start; }; then
      return 0
    fi
    echo "Compiled layer control failed; trying only the previous install's bundled helpers." >&2
  fi
  [[ -f "$directory/start_safari.sh" && -f "$directory/start_siri.sh" ]] || return 1
  if layer_enabled safari; then (cd "$directory" && bash ./start_safari.sh) || return 1; fi
  if layer_enabled siri; then (cd "$directory" && bash ./start_siri.sh) || return 1; fi
}
start_runtime_services() {
  local directory="$1"
  local agent_binary="$directory/XIDER.app/Contents/MacOS/XGENT-MCS"
  local guardian_binary="$directory/XIDER-Guard-Keeper.app/Contents/MacOS/XIDER-Guard-Keeper"
  if [[ -x "$agent_binary" && -x "$guardian_binary" ]]; then
    if "$agent_binary" --service start && "$guardian_binary" --service start; then
      return 0
    fi
    echo "Compiled service control failed; trying only the previous install's bundled helpers." >&2
  fi
  [[ -f "$directory/start_agent.sh" && -f "$directory/start_guardian.sh" ]] || return 1
  (cd "$directory" && XIDER_RUNTIME_PREPARED=1 bash ./start_agent.sh && bash ./start_guardian.sh)
}
runtime_services_status() {
  local directory="$1"
  local agent_binary="$directory/XIDER.app/Contents/MacOS/XGENT-MCS"
  local guardian_binary="$directory/XIDER-Guard-Keeper.app/Contents/MacOS/XIDER-Guard-Keeper"
  if [[ -x "$agent_binary" && -x "$guardian_binary" ]]; then
    if "$agent_binary" --service status && "$guardian_binary" --service status; then
      return 0
    fi
    echo "Compiled status probe failed; trying only the previous install's bundled helpers." >&2
  fi
  [[ -f "$directory/start_agent.sh" && -f "$directory/start_guardian.sh" ]] || return 1
  (cd "$directory" && bash ./start_agent.sh --status && bash ./start_guardian.sh --status)
}
stop_loaded_job() {
  local label="$1" service="gui/$(id -u)/$1"
  if launchctl print "$service" >/dev/null 2>&1; then
    if ! launchctl bootout "$service"; then
      echo "Не удалось остановить $label; checkout не заменён." >&2
      return 1
    fi
  fi
}
# Stop from the outermost recovery layer inward. Booting out a job preserves
# the owner's enabled/disabled preference; `--stop` would overwrite it.
if ! stop_loaded_job com.xider.siri || ! stop_loaded_job com.xider.safari ||
   ! stop_loaded_job com.xider.guardian || ! stop_loaded_job com.xgent.agent; then
  if [[ -d "$TARGET/XGENT-MCS/XIDER.app" || -f "$TARGET/XGENT-MCS/start_guardian.sh" ]]; then
    start_runtime_services "$TARGET/XGENT-MCS" || true
    start_enabled_layers "$TARGET/XGENT-MCS" || true
  fi
  exit 5
fi
if [[ -e "$TARGET" ]]; then mv "$TARGET" "$backup"; fi
if ! mv "$stage/new" "$TARGET"; then
  [[ ! -e "$backup" ]] || mv "$backup" "$TARGET"
  if [[ -d "$TARGET/XGENT-MCS/XIDER.app" || -f "$TARGET/XGENT-MCS/start_agent.sh" ]]; then
    start_runtime_services "$TARGET/XGENT-MCS" || true
    start_enabled_layers "$TARGET/XGENT-MCS" || true
  fi
  echo "Не удалось активировать новую папку; прежняя установка сохранена." >&2
  exit 5
fi

if ! (start_runtime_services "$TARGET/XGENT-MCS" &&
      start_enabled_layers "$TARGET/XGENT-MCS" &&
      runtime_services_status "$TARGET/XGENT-MCS"); then
  echo "Запуск не прошёл; возвращаю предыдущую версию." >&2
  if ! stop_loaded_job com.xider.siri || ! stop_loaded_job com.xider.safari ||
     ! stop_loaded_job com.xider.guardian || ! stop_loaded_job com.xgent.agent; then
    echo "Откат остановлен: новая служба не выгрузилась. Папки не перемещал; резервная копия: $backup" >&2
    exit 6
  fi
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
  if [[ -d "$TARGET/XGENT-MCS/XIDER.app" || -x "$TARGET/XGENT-MCS/start_agent.sh" ]]; then
    start_runtime_services "$TARGET/XGENT-MCS" || true
    start_enabled_layers "$TARGET/XGENT-MCS" || true
  fi
  [[ ! -e "$backup" ]] || echo "Предыдущая версия возвращена: $TARGET"
  exit 6
fi

if [[ -n "$recovery_slot" ]]; then
  (cd "$TARGET/XGENT-MCS" && "$runtime/bin/python3" - "$recovery_slot" <<'PY'
from pathlib import Path
import sys
from guardian_recovery import activate_recovery_package
print('Guard Keeper recovery: ' + activate_recovery_package(Path.home() / '.xgent' / 'recovery', sys.argv[1]))
PY
  ) || { echo "Агент запущен, но выбор recovery-пакета не прошёл; проверь диагностику Guard Keeper." >&2; exit 7; }
fi

echo "Установка завершена: $TARGET"
[[ ! -e "$backup" ]] || echo "Предыдущая установка сохранена: $backup"
echo "Проверка: \"$TARGET/XGENT-MCS/XIDER.app/Contents/MacOS/XGENT-MCS\" --service status; \"$TARGET/XGENT-MCS/XIDER-Guard-Keeper.app/Contents/MacOS/XIDER-Guard-Keeper\" --service status"
echo "Запуск процессов подтверждён. Связь MQTT и системные разрешения проверь в карточке устройства в боте."
