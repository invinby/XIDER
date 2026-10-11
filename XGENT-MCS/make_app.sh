#!/usr/bin/env bash
# Build the real signed app, never a shell launcher outside the bundle.
set -euo pipefail
cd "$(dirname "$0")"
AGENT_VERSION="$(sed -n 's/^VERSION = "\([0-9.]*\)"/\1/p' config.py | head -n 1)"
if [[ ! "$AGENT_VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  echo "Could not read the agent version from config.py." >&2
  exit 1
fi
case "${XIDER_MACOS_SIGNING_IDENTITY:-}" in
  "Apple Development:"*|"Developer ID Application:"*) ;;
  *) echo "BLOCKED: Core Location requires a proper non-ad-hoc Apple signing identity." >&2; exit 1 ;;
esac
python3 -m pip install pyinstaller==6.22.3
python3 -m PyInstaller --clean --noconfirm XGENT-MCS.spec
python3 ../tools/verify_macos_bundle.py dist/XIDER.app
echo "Built signed XIDER.app $AGENT_VERSION; physical Core Location acceptance remains required."
echo "Local visible permission setup: dist/XIDER.app/Contents/MacOS/XGENT-MCS --location-setup"
