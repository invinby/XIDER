#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

ROLE=safari
LABEL=com.xider.safari
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
PYTHON="$PWD/venv/bin/python3"
[[ -x "$PYTHON" ]] || PYTHON="$(command -v python3 || true)"
LAYER_APP_BIN="$PWD/XIDER-Layers.app/Contents/MacOS/XIDER-Layers"
RELEASE_MARKER="$PWD/.xider-signed-release"

layer_cli() {
  if [[ -x "$LAYER_APP_BIN" ]]; then
    "$LAYER_APP_BIN" "$@"
  elif [[ -f "$RELEASE_MARKER" ]]; then
    echo "[ERROR] Signed XIDER-Layers.app is missing; owner state was not changed." >&2
    return 1
  elif [[ -f "$PWD/xider_layers.py" && -n "$PYTHON" ]]; then
    "$PYTHON" "$PWD/xider_layers.py" "$@"
  else
    echo "[ERROR] XIDER-Layers runtime is unavailable; owner state was not changed." >&2
    return 1
  fi
}

set_enabled() { layer_cli --set-enabled "$ROLE" "$1"; }

if [[ "${1:-}" == "--status" ]]; then
  layer_cli --service "$ROLE" status
  exit $?
fi
if [[ "${1:-}" == "--stop" ]]; then
  layer_cli --owner-stop "$ROLE"
  if launchctl print "gui/$(id -u)/$LABEL" >/dev/null 2>&1; then
    launchctl bootout "gui/$(id -u)" "$PLIST" >/dev/null 2>&1 || { echo "[ERROR] Could not unload Safari LaunchAgent" >&2; exit 1; }
  fi
  rm -f "$PLIST"
  echo "[STOPPED] XIDER Safari stopped by owner"
  exit 0
fi

if [[ -f "$RELEASE_MARKER" && ! -x "$LAYER_APP_BIN" ]]; then
  echo "[ERROR] Signed XIDER-Layers.app is missing from this release install; source fallback is disabled." >&2
  exit 1
fi
if [[ ! -x "$LAYER_APP_BIN" && ! -f "$PWD/xider_layers.py" ]]; then
  echo "[ERROR] XIDER-Layers.app or the development source is required." >&2
  exit 1
fi
if [[ ! -x "$LAYER_APP_BIN" && -z "$PYTHON" ]]; then
  echo "[ERROR] Python 3 is required for the development source fallback." >&2
  exit 1
fi
set_enabled start
mkdir -p "$HOME/Library/LaunchAgents"
layer_cli --write-launch-agent "$ROLE" "$PWD" "$PYTHON" "$LAYER_APP_BIN" "$RELEASE_MARKER" "$PLIST"
launchctl bootout "gui/$(id -u)" "$PLIST" >/dev/null 2>&1 || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
launchctl kickstart "gui/$(id -u)/$LABEL"
echo "[OK] XIDER Safari layer registered; owner can stop it with $0 --stop"
