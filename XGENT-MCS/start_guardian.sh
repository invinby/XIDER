#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

PLIST="$HOME/Library/LaunchAgents/com.xider.guardian.plist"
PYTHON="$PWD/venv/bin/python3"
[[ -x "$PYTHON" ]] || PYTHON="$(command -v python3)"

mkdir -p "$HOME/Library/LaunchAgents"
chmod +x "$0"

if [[ "${1:-}" == "--status" || "${1:-}" == "-s" ]]; then
  if launchctl print "gui/$(id -u)/com.xider.guardian" >/dev/null 2>&1; then
    echo "[OK] XIDER Guardian работает через LaunchAgent"
    echo "[LOG] $PWD/guardian.log"
    exit 0
  fi
  echo "[STOPPED] XIDER Guardian не запущен"
  exit 1
fi

if [[ "${1:-}" == "--stop" || "${1:-}" == "-x" ]]; then
  launchctl bootout "gui/$(id -u)" "$PLIST" >/dev/null 2>&1 || true
  rm -f "$PLIST"
  echo "[STOPPED] XIDER Guardian остановлен"
  exit 0
fi

cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.xider.guardian</string>
  <key>ProgramArguments</key>
  <array><string>$PYTHON</string><string>$PWD/xider_guardian.py</string></array>
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
echo "[OK] XIDER Guardian зарегистрирован и запущен"
