#!/usr/bin/env bash
set -Eeuo pipefail

SERVICE="xider-bot.service"
UPDATE_HELPER="/usr/local/libexec/xider/update-server.sh"

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
    shift
    exec /bin/bash "$UPDATE_HELPER" rollback "$@"
    ;;
  *)
    echo "Usage: xider-server-ops {status|logs|restart|update|rollback [backup-archive]}" >&2
    exit 2
    ;;
esac
