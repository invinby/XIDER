#!/usr/bin/env bash
set -Eeuo pipefail

if [[ "$EUID" -ne 0 ]]; then
  echo "Run through sudo." >&2
  exit 2
fi
NFT="$(command -v nft || true)"
[[ -x "$NFT" ]] || { echo "nftables is required to protect the X-DOCK WebSocket listener." >&2; exit 3; }

if ! "$NFT" list table inet xider_ws_guard >/dev/null 2>&1; then
  "$NFT" add table inet xider_ws_guard
fi
if ! "$NFT" list chain inet xider_ws_guard input >/dev/null 2>&1; then
  "$NFT" add chain inet xider_ws_guard input '{ type filter hook input priority 10; policy accept; }'
fi
if ! "$NFT" list chain inet xider_ws_guard input | grep -Fq 'tcp dport 18885 drop'; then
  "$NFT" add rule inet xider_ws_guard input iifname '!=' lo tcp dport 18885 drop
fi

"$NFT" list chain inet xider_ws_guard input | grep -Fq 'tcp dport 18885 drop'
