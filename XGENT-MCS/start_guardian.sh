#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

PLIST="$HOME/Library/LaunchAgents/com.xider.guardian.plist"
GUARDIAN_APP_BIN="$PWD/XIDER-Guard-Keeper.app/Contents/MacOS/XIDER-Guard-Keeper"
GUARDIAN_SCRIPT="$PWD/xider_guardian.py"

mkdir -p "$HOME/Library/LaunchAgents"
chmod +x "$0"

if [[ "${1:-}" == "--status" || "${1:-}" == "-s" ]]; then
  LAUNCH_STATE="$(launchctl print "gui/$(id -u)/com.xider.guardian" 2>/dev/null || true)"
  if printf '%s\n' "$LAUNCH_STATE" | grep -Eq 'pid = [0-9]+|state = running'; then
    echo "[OK] XIDER Guardian работает через LaunchAgent"
    echo "[LOG] $PWD/guardian.log"
    exit 0
  fi
  echo "[STOPPED] XIDER Guardian не запущен (или LaunchAgent ещё ожидает процесс)"
  exit 1
fi

if [[ "${1:-}" == "--stop" || "${1:-}" == "-x" ]]; then
  PYTHON="$PWD/venv/bin/python3"
  [[ -x "$PYTHON" ]] || PYTHON="$(command -v python3 || true)"
  [[ -n "$PYTHON" ]] || { echo "[ERROR] Нужен Python 3, чтобы сохранить команду остановки Guardian." >&2; exit 1; }
  "$PYTHON" - "$HOME/.xgent/guardian.json" <<'PY'
import json, os, pathlib, sys, tempfile

path = pathlib.Path(sys.argv[1])
path.parent.mkdir(parents=True, exist_ok=True)
if path.is_symlink():
    raise SystemExit("[ERROR] Состояние Guardian является symlink; остановка отменена.")
state = {}
if path.exists():
    if not path.is_file() or path.stat().st_size > 64 * 1024:
        raise SystemExit("[ERROR] Состояние Guardian повреждено или слишком велико; остановка отменена.")
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SystemExit(f"[ERROR] Не удалось прочитать состояние Guardian; остановка отменена: {exc}")
    if not isinstance(state, dict):
        raise SystemExit("[ERROR] Состояние Guardian имеет неверный формат; остановка отменена.")
state["desired_running"] = False
fd, temporary = tempfile.mkstemp(prefix="guardian-state.", suffix=".tmp", dir=path.parent)
try:
    os.fchmod(fd, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        json.dump(state, stream, ensure_ascii=False, separators=(",", ":"))
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
finally:
    pathlib.Path(temporary).unlink(missing_ok=True)
PY
  if launchctl print "gui/$(id -u)/com.xider.guardian" >/dev/null 2>&1; then
    launchctl bootout "gui/$(id -u)" "$PLIST" >/dev/null 2>&1 || {
      echo "[ERROR] Не удалось выгрузить Guardian; его намерение остановиться сохранено." >&2
      exit 1
    }
  fi
  rm -f "$PLIST"
  echo "[STOPPED] XIDER Guardian остановлен; Safari не будет поднимать его без команды владельца"
  exit 0
fi

xml_escape() {
  printf '%s' "$1" | sed -e 's/\&/\&amp;/g' -e 's/</\&lt;/g' -e 's/>/\&gt;/g'
}

if [[ -x "$GUARDIAN_APP_BIN" ]]; then
  guardian_argument="<string>$(xml_escape "$GUARDIAN_APP_BIN")</string>"
elif [[ -f "$GUARDIAN_SCRIPT" ]]; then
  PYTHON="$PWD/venv/bin/python3"
  [[ -x "$PYTHON" ]] || PYTHON="$(command -v python3 || true)"
  [[ -n "$PYTHON" ]] || { echo "[ERROR] Не найден XIDER-Guard-Keeper.app и python3 для исходного Guardian." >&2; exit 1; }
  guardian_argument="<string>$(xml_escape "$PYTHON")</string><string>$(xml_escape "$GUARDIAN_SCRIPT")</string>"
else
  echo "[ERROR] Не найден XIDER-Guard-Keeper.app или xider_guardian.py." >&2
  exit 1
fi

cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.xider.guardian</string>
  <key>ProgramArguments</key>
  <array>$guardian_argument</array>
  <key>WorkingDirectory</key><string>$PWD</string>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ProcessType</key><string>Background</string>
  <key>StandardOutPath</key><string>$PWD/guardian.log</string>
  <key>StandardErrorPath</key><string>$PWD/guardian.log</string>
</dict>
</plist>
EOF

launchctl bootout "gui/$(id -u)" "$PLIST" >/dev/null 2>&1 || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
for attempt in {1..10}; do
  LAUNCH_STATE="$(launchctl print "gui/$(id -u)/com.xider.guardian" 2>/dev/null || true)"
  if printf '%s\n' "$LAUNCH_STATE" | grep -Eq 'pid = [0-9]+|state = running'; then
    echo "[OK] XIDER Guardian зарегистрирован и запущен"
    exit 0
  fi
  sleep 1
done
echo "[ERROR] XIDER Guardian зарегистрирован, но процесс не запустился. Проверь guardian.log." >&2
exit 1
