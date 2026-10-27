#!/usr/bin/env bash
set -Eeuo pipefail

# Keep the updater integration fixture hermetic: its service-owned state must
# stay under the fixture root instead of creating /var/lib/xider or /etc/xider.
uid="${SUDO_UID:-$(id -u)}"
gid="${SUDO_GID:-$(id -g)}"
root="${XIDER_TEST_ROOT:?XIDER_TEST_ROOT is required by the updater fixture}"
args=()
expect_owner=0
expect_group=0

for arg in "$@"; do
  append=1
  if (( expect_owner )); then
    [[ "$arg" == xider ]] && arg="$uid"
    (( EUID == 0 )) || append=0
    expect_owner=0
  elif (( expect_group )); then
    [[ "$arg" == xider ]] && arg="$gid"
    (( EUID == 0 )) || append=0
    expect_group=0
  else
    case "$arg" in
      -o) expect_owner=1; (( EUID == 0 )) || append=0 ;;
      -g) expect_group=1; (( EUID == 0 )) || append=0 ;;
      /var/lib/xider) arg="$root/var/lib/xider" ;;
      /etc/xider/device-keys) arg="$root/etc/xider/device-keys" ;;
    esac
  fi
  if (( append )); then args+=("$arg"); fi
done

exec /usr/bin/install "${args[@]}"
