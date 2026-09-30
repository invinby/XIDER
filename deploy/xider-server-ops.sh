#!/usr/bin/env bash
set -Eeuo pipefail

SERVICE="${XIDER_SERVICE:-xider-bot.service}"
APP_DIR="${XIDER_APP_DIR:-/opt/xider}"

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
    exec /bin/bash "$APP_DIR/deploy/update-server.sh" "$1"
    ;;
  rollback)
    shift
    exec /bin/bash "$APP_DIR/deploy/update-server.sh" rollback "$@"
    ;;
  *)
    echo "Usage: xider-server-ops {status|logs|restart|update|rollback [backup-archive]}" >&2
    exit 2
    ;;
esac
