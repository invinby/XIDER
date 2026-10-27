#!/usr/bin/env bash
set -Eeuo pipefail

SERVICE="xider-bot.service"
UPDATE_HELPER="/usr/local/libexec/xider/update-server.sh"

if [[ "$#" -ne 1 ]]; then
  echo "Usage: xider-server-ops {status|logs|restart|update|rollback|enable-x-dock-websockets}" >&2
  exit 2
fi

case "${1:-}" in
  status)
    exec /bin/systemctl show "$SERVICE" --no-page \
      --property=ActiveState,SubState,Unit,MainPID,ExecMainStatus
    ;;
  logs)
    exec /usr/bin/journalctl -u "$SERVICE" -n 80 --no-pager -o short
    ;;
  restart)
    exec /bin/systemctl restart "$SERVICE"
    ;;
  update)
    exec /bin/bash "$UPDATE_HELPER" update
    ;;
  rollback)
    exec /bin/bash "$UPDATE_HELPER" rollback
    ;;
  enable-x-dock-websockets)
    exec /bin/bash /opt/xider/deploy/enable-x-dock-websockets.sh
    ;;
  *)
    echo "Usage: xider-server-ops {status|logs|restart|update|rollback|enable-x-dock-websockets}" >&2
    exit 2
    ;;
esac
