#!/usr/bin/env bash
set -Eeuo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
if [[ "$EUID" != 0 ]]; then
  echo 'Run as root in a disposable Linux fixture; tests use temporary paths only.' >&2
  exit 2
fi
python3 "$ROOT/deploy/tests/test_provision_command_signing_key.py"
