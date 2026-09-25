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

if [[ ! -f "${ENV_ROOT}/XGENT-MCS/.env" && -f "${ENV_ROOT}/git-ver/XGENT-MCS/.env" ]]; then
  ENV_ROOT="${ENV_ROOT}/git-ver"
fi
if [[ ! -f "${ENV_ROOT}/XGENT-MCS/.env" ]]; then
  echo "XIDER скачан в ${INSTALL_ROOT}/git-ver"
  echo "Нужен ${ENV_ROOT}/XGENT-MCS/.env. Укажи каталог через XIDER_ENV_ROOT и запусти снова."
  exit 4
fi
cp "${ENV_ROOT}/XGENT-MCS/.env" "${INSTALL_ROOT}/git-ver/XGENT-MCS/.env"
cd "${INSTALL_ROOT}/git-ver/XGENT-MCS"
./start_agent.sh
echo "XIDER macOS-агент готов."
