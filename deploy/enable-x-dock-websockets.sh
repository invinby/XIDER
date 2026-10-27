#!/usr/bin/env bash
set -Eeuo pipefail

APP_DIR="${XIDER_APP_DIR:-/opt/xider}"
BROKER_CONFIG=/etc/xider-dock/mosquitto.conf
NGINX_SITE=/etc/nginx/conf.d/xider-dock.conf
BOT_ENV=/etc/xider/bot.env
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
BACKUP_DIR="/var/backups/xider/x-dock-websockets-${STAMP}"
STAGE="$(mktemp -d /run/xider-dock-ws.XXXXXX)"
BROKER_CHANGED=0
NGINX_CHANGED=0
ENV_CHANGED=0

cleanup() { rm -rf -- "$STAGE"; }
rollback() {
  local status=$?
  if [[ "$status" -ne 0 ]]; then
    echo "X-DOCK WebSocket migration failed; restoring changed files." >&2
    if [[ "$NGINX_CHANGED" -eq 1 && -s "$BACKUP_DIR/nginx.conf" ]]; then
      install -o root -g root -m 0644 "$BACKUP_DIR/nginx.conf" "$NGINX_SITE"
      nginx -t && systemctl reload nginx || true
    fi
    if [[ "$BROKER_CHANGED" -eq 1 && -s "$BACKUP_DIR/mosquitto.conf" ]]; then
      install -o root -g mosquitto -m 0640 "$BACKUP_DIR/mosquitto.conf" "$BROKER_CONFIG"
      systemctl restart xider-dock-mqtt || true
    fi
    if [[ "$ENV_CHANGED" -eq 1 && -s "$BACKUP_DIR/bot.env" ]]; then
      install -o root -g xider -m 0640 "$BACKUP_DIR/bot.env" "$BOT_ENV"
      systemctl restart xider-bot || true
    fi
  fi
  cleanup
  exit "$status"
}
trap rollback EXIT INT TERM HUP

if [[ "$EUID" -ne 0 ]]; then
  echo "Run through sudo." >&2
  exit 2
fi
for path in "$BROKER_CONFIG" "$NGINX_SITE" "$BOT_ENV"; do
  [[ -f "$path" && ! -L "$path" ]] || { echo "Missing or unsafe X-DOCK path: $path" >&2; exit 3; }
done
systemctl is-active --quiet xider-dock-mqtt
systemctl is-active --quiet nginx
systemctl is-active --quiet xider-bot
grep -Fq 'XIDER_DOCK_ENABLED=true' "$BOT_ENV"
grep -Fq 'transport="websockets"' "$APP_DIR/TG-BOT-SERVER/bot.py"
grep -Fq -- '--websocket-port' "$APP_DIR/broker/x_dock_config.py"
ldd /usr/sbin/mosquitto | grep -q 'libwebsockets' || {
  echo "Mosquitto was built without WebSocket support." >&2; exit 4;
}

install -d -o root -g root -m 0700 "$BACKUP_DIR"
cp -a "$BROKER_CONFIG" "$BACKUP_DIR/mosquitto.conf"
cp -a "$NGINX_SITE" "$BACKUP_DIR/nginx.conf"
cp -a "$BOT_ENV" "$BACKUP_DIR/bot.env"

python3 "$APP_DIR/broker/x_dock_config.py" \
  --plugin /usr/lib/x86_64-linux-gnu/mosquitto_dynamic_security.so \
  --security-file /var/lib/xider-dock/dynamic-security.json \
  --data-dir /var/lib/xider-dock/data \
  --cert-file /etc/xider-dock/tls/fullchain.pem \
  --key-file /etc/xider-dock/tls/privkey.pem \
  --port 8884 --bind-address 127.0.0.1 --websocket-port 18885 \
  --output "$STAGE/mosquitto.conf"
mosquitto -c "$STAGE/mosquitto.conf" -t
install -o root -g mosquitto -m 0640 "$STAGE/mosquitto.conf" "$BROKER_CONFIG"
BROKER_CHANGED=1
systemctl restart xider-dock-mqtt
for attempt in {1..20}; do
  if ss -ltn | awk '{print $4}' | grep -q '127.0.0.1:18885$' && \
     ss -ltn | awk '{print $4}' | grep -q '127.0.0.1:8884$'; then break; fi
  sleep 1
done
ss -ltn | awk '{print $4}' | grep -q '127.0.0.1:18885$'
ss -ltn | awk '{print $4}' | grep -q '127.0.0.1:8884$'

python3 - "$NGINX_SITE" "$STAGE/nginx.conf" <<'PY'
import pathlib, sys
source = pathlib.Path(sys.argv[1]).read_text(encoding="utf-8")
matches = []
cursor = 0
while True:
    start = source.find("server {", cursor)
    if start < 0:
        break
    opening = source.find("{", start)
    depth = 0
    end = None
    for offset in range(opening, len(source)):
        if source[offset] == "{":
            depth += 1
        elif source[offset] == "}":
            depth -= 1
            if depth == 0:
                end = offset + 1
                break
    if end is None:
        raise SystemExit("unterminated nginx server block")
    if "listen 443 ssl;" in source[start:end]:
        matches.append((start, end, source[start:end]))
    cursor = end
if len(matches) != 1:
    raise SystemExit("expected exactly one HTTPS X-DOCK server block")
start, end, block = matches[0]
fallback = "    location / { return 404; }"
if "location = /mqtt" in block:
    pathlib.Path(sys.argv[2]).write_text(source, encoding="utf-8")
    raise SystemExit(0)
if block.count(fallback) != 1:
    raise SystemExit("HTTPS server block does not have the expected closed fallback")
replacement = '''    location = /mqtt {
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header X-Real-IP $remote_addr;
        proxy_read_timeout 1h;
        proxy_buffering off;
        proxy_pass http://127.0.0.1:18885;
    }
    location / { return 404; }'''
updated = block.replace(fallback, replacement, 1)
pathlib.Path(sys.argv[2]).write_text(source[:start] + updated + source[end:], encoding="utf-8")
PY
install -o root -g root -m 0644 "$STAGE/nginx.conf" "$NGINX_SITE"
NGINX_CHANGED=1
nginx -t
systemctl reload nginx

python3 - "$BOT_ENV" <<'PY'
import os, pathlib, re, sys, tempfile
path = pathlib.Path(sys.argv[1])
keys = {"XIDER_DOCK_WS_PORT": "18885", "XIDER_DOCK_PUBLIC_PORT": "443"}
lines = path.read_text(encoding="utf-8-sig").splitlines()
for key, value in keys.items():
    pattern = re.compile(rf"^\s*(?:export\s+)?{re.escape(key)}\s*=")
    lines = [line for line in lines if not pattern.match(line)]
    lines.append(f"{key}={value}")
fd, name = tempfile.mkstemp(prefix=".bot-env-ws-", dir=path.parent)
try:
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
        stream.write("\n".join(lines) + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.chmod(name, 0o640)
    os.chown(name, 0, path.stat().st_gid)
    os.replace(name, path)
finally:
    pathlib.Path(name).unlink(missing_ok=True)
PY
ENV_CHANGED=1
systemctl restart xider-bot
systemctl is-active --quiet xider-bot
systemctl is-active --quiet xider-dock-mqtt
systemctl is-active --quiet nginx
PUBLIC_IP="$(awk -F= '/^XIDER_DOCK_BROKER_HOST=/{print $2}' "$BOT_ENV" | tail -n 1)"
[[ "$PUBLIC_IP" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}$ ]]
HTTP_STATUS="$(curl --silent --show-error --max-time 8 \
  --resolve "$PUBLIC_IP:443:127.0.0.1" "https://$PUBLIC_IP/x-dock/v1/poll" \
  -H 'Content-Type: application/json' -d '{}' -o /dev/null -w '%{http_code}')"
[[ "$HTTP_STATUS" != 000 && "$HTTP_STATUS" != 404 && "$HTTP_STATUS" != 502 ]]

echo "[OK] X-DOCK now has a loopback-only MQTT WebSocket listener on 18885 behind HTTPS /mqtt:443."
echo "[OK] Raw MQTT listener 8884 is now loopback-only; no AWS port rule is needed."
echo "[OK] Backups: $BACKUP_DIR"
trap - EXIT INT TERM HUP
cleanup
