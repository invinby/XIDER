#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

ROLE=siri
LABEL=com.xider.siri
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
STATE="$HOME/.xgent/layer-state.json"
PYTHON="$PWD/venv/bin/python3"
[[ -x "$PYTHON" ]] || PYTHON="$(command -v python3 || true)"
LAYER_APP_BIN="$PWD/XIDER-Layers.app/Contents/MacOS/XIDER-Layers"
RELEASE_MARKER="$PWD/.xider-signed-release"

set_enabled() {
  mkdir -p "$(dirname "$STATE")"
  "$PYTHON" - "$STATE" "$ROLE" "$1" <<'PY'
import json, os, pathlib, sys, tempfile
path, role, value = pathlib.Path(sys.argv[1]), sys.argv[2], sys.argv[3] == "true"
state = {}
try:
    if path.is_symlink(): raise SystemExit("Refusing symlink layer state.")
    if path.is_file() and path.stat().st_size <= 4096:
        loaded = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(loaded, dict): state = loaded
except (OSError, ValueError):
    pass
state[role] = value
fd, temp = tempfile.mkstemp(prefix=".layer-state.", dir=path.parent)
try:
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        json.dump(state, stream, separators=(",", ":")); stream.write("\n")
        stream.flush(); os.fsync(stream.fileno())
    os.replace(temp, path)
finally:
    pathlib.Path(temp).unlink(missing_ok=True)
PY
}

if [[ "${1:-}" == "--status" ]]; then
  if launchctl print "gui/$(id -u)/$LABEL" >/dev/null 2>&1; then
    echo "[OK] XIDER Siri layer loaded"
    echo "[LOG] $HOME/.xgent/siri.log"
    exit 0
  fi
  echo "[STOPPED] XIDER Siri layer is not loaded"
  exit 1
fi
if [[ "${1:-}" == "--stop" ]]; then
  set_enabled false
  if launchctl print "gui/$(id -u)/$LABEL" >/dev/null 2>&1; then
    launchctl bootout "gui/$(id -u)" "$PLIST" >/dev/null 2>&1 || { echo "[ERROR] Could not unload Siri LaunchAgent" >&2; exit 1; }
  fi
  rm -f "$PLIST"
  "$PYTHON" - "$STATE" "$ROLE" <<'PY'
import json, os, pathlib, sys, tempfile, time
state_path, role = pathlib.Path(sys.argv[1]), sys.argv[2]
root = state_path.parent
health = root / f"{role}-health.json"
payload = {"schema": 1, "component": role, "version": "0.1.0", "pid": os.getpid(), "updated_at": time.time(), "state": "stopped", "detail": "disabled by owner"}
fd, temporary = tempfile.mkstemp(prefix=f".{role}-health.", suffix=".tmp", dir=root)
try:
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        json.dump(payload, stream, separators=(",", ":")); stream.write("\n"); stream.flush(); os.fsync(stream.fileno())
    os.replace(temporary, health)
finally:
    pathlib.Path(temporary).unlink(missing_ok=True)
PY
  echo "[STOPPED] XIDER Siri stopped by owner"
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
set_enabled true
mkdir -p "$HOME/Library/LaunchAgents"
"$PYTHON" - "$PLIST" "$PWD" "$PYTHON" "$LAYER_APP_BIN" <<'PY'
import os, pathlib, plistlib, sys
plist, root, python, app_bin = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2]), sys.argv[3], pathlib.Path(sys.argv[4])
if app_bin.is_file() and os.access(app_bin, os.X_OK):
    program = [str(app_bin), "--role", "siri"]
elif (root / ".xider-signed-release").is_file():
    raise SystemExit("Signed release is missing XIDER-Layers.app; refusing source fallback.")
else:
    program = [python, str(root / "xider_layers.py"), "--role", "siri"]
data = {
    "Label": "com.xider.siri",
    "ProgramArguments": program,
    "WorkingDirectory": str(root), "RunAtLoad": True, "KeepAlive": True,
    "ProcessType": "Background",
    "StandardOutPath": "/dev/null",
    "StandardErrorPath": "/dev/null",
}
plist.write_bytes(plistlib.dumps(data))
PY
launchctl bootout "gui/$(id -u)" "$PLIST" >/dev/null 2>&1 || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
launchctl kickstart "gui/$(id -u)/$LABEL"
echo "[OK] XIDER Siri layer registered; owner can stop it with $0 --stop"
