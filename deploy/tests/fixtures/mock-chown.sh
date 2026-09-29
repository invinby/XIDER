#!/usr/bin/env bash
set -eu
if [[ -n "${SUDO_UID:-}" && -n "${SUDO_GID:-}" ]]; then
  shift 2
  for path in "$@"; do
    /bin/chown -R "${SUDO_UID}:${SUDO_GID}" "${path}"
  done
fi
