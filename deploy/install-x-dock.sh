#!/usr/bin/env bash
set -Eeuo pipefail

# Install the isolated X-DOCK broker and HTTPS enrollment endpoint on an
# already-configured XIDER bot VPS. The MQTT listener stays on loopback until
# Dynamic Security has been initialized and the demo identity is removed.

APP_DIR="${XIDER_APP_DIR:-/opt/xider}"
BOT_ENV="${XIDER_BOT_ENV:-/etc/xider/bot.env}"
PUBLIC_IP="${XIDER_PUBLIC_IP:-}"
CERT_EMAIL="${XIDER_CERT_EMAIL:-}"
DOCK_DIR=/etc/xider-dock
DATA_DIR=/var/lib/xider-dock
WEBROOT=/var/www/xider-dock-acme
BROKER_SERVICE=xider-dock-mqtt.service
BROKER_UNIT=/etc/systemd/system/$BROKER_SERVICE
WS_GUARD_UNIT=/etc/systemd/system/xider-ws-firewall.service
WS_GUARD_SCRIPT=/usr/local/sbin/xider-ws-firewall
WS_BROKER_DROPIN=/etc/systemd/system/$BROKER_SERVICE.d/20-ws-firewall.conf
NGINX_SITE=/etc/nginx/conf.d/xider-dock.conf
BROKER_CONFIG=$DOCK_DIR/mosquitto.conf
BOT_SERVICE=xider-bot.service
BOT_ENV_BACKUP="$BOT_ENV.pre-x-dock"
CERT_NAME=""
CERT_LINEAGE=""
CERT_RENEWAL=""
CERTBOT_BIN="$(command -v certbot || true)"
CERT_HOOK=/etc/letsencrypt/renewal-hooks/deploy/50-xider-dock
RENEW_SERVICE=/etc/systemd/system/xider-dock-cert-renew.service
RENEW_TIMER=/etc/systemd/system/xider-dock-cert-renew.timer
STAGE=""
BOT_ENV_CHANGED=0
MOSQUITTO_RUNTIME_MASKED=0
MOSQUITTO_WAS_INSTALLED=0
NGINX_RUNTIME_MASKED=0
NGINX_WAS_INSTALLED=0
NGINX_WAS_ACTIVE=0
NGINX_WAS_ENABLED=0
NGINX_STARTED_BY_XIDER=0
SETUP_COMPLETE=0
DOCK_DIR_PREEXISTED=0
DATA_DIR_PREEXISTED=0
WEBROOT_PREEXISTED=0
RENEW_TIMER_INSTALLED=0
SNAPD_PACKAGE_PREEXISTED=0
SNAPD_SOCKET_ACTIVE_BEFORE=0
SNAPD_SOCKET_ENABLED_BEFORE=0
SNAPD_SERVICE_ACTIVE_BEFORE=0
SNAPD_SERVICE_ENABLED_BEFORE=0
SNAP_CERTBOT_PREEXISTED=0
SNAP_CERTBOT_REVISION_BEFORE=""
SNAP_CERTBOT_INSTALLED_BY_XIDER=0
UFW_HTTP_RULE_ADDED=0
APPARMOR_PROFILE=/etc/apparmor.d/mosquitto
APPARMOR_LOCAL=/etc/apparmor.d/local/mosquitto
APPARMOR_WAS_ACTIVE=0
APPARMOR_OVERRIDE_CHANGED=0
APPARMOR_LOCAL_PREEXISTED=0

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run this installer as root." >&2
  exit 2
fi
if [[ ! "$PUBLIC_IP" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}$ ]] || ! python3 - "$PUBLIC_IP" <<'PY'
import ipaddress, sys
try:
    ipaddress.IPv4Address(sys.argv[1])
except ValueError:
    raise SystemExit(1)
PY
then
  echo "Set XIDER_PUBLIC_IP to the VPS's stable public IPv4 address." >&2
  exit 2
fi
if [[ ! "$CERT_EMAIL" =~ ^[^[:space:]@]+@[^[:space:]@]+\.[^[:space:]@]+$ ]]; then
  echo "Set XIDER_CERT_EMAIL to a reachable certificate-expiry contact." >&2
  exit 2
fi
CERT_NAME="xider-dock-$PUBLIC_IP"
CERT_LINEAGE="/etc/letsencrypt/live/$CERT_NAME"
CERT_RENEWAL="/etc/letsencrypt/renewal/$CERT_NAME.conf"
if [[ ! -d "$APP_DIR/broker" || ! -f "$APP_DIR/deploy/xider-bot.service" || \
      ! -x "$APP_DIR/TG-BOT-SERVER/venv/bin/python" || ! -f "$BOT_ENV" || ! -r "$BOT_ENV" || -L "$BOT_ENV" ]]; then
  echo "An installed XIDER bot and readable $BOT_ENV are required." >&2
  exit 2
fi
if ! getent passwd xider >/dev/null || ! getent group xider >/dev/null; then
  echo "The xider service account and group must exist before X-DOCK setup." >&2
  exit 2
fi
for path in "$DATA_DIR" "$DOCK_DIR" "$WEBROOT" "$NGINX_SITE" "/etc/nginx/conf.d"; do
  if [[ -L "$path" ]]; then
    echo "Refusing symlink at X-DOCK-managed path: $path" >&2
    exit 2
  fi
done
if systemctl is-active --quiet apparmor.service; then
  APPARMOR_WAS_ACTIVE=1
  if [[ ! -r "$APPARMOR_PROFILE" ]] || ! grep -Fq 'include if exists <local/mosquitto>' "$APPARMOR_PROFILE"; then
    echo "Active AppArmor has no supported Mosquitto local-profile include; refusing to weaken confinement." >&2
    exit 2
  fi
  if [[ -L "$APPARMOR_LOCAL" ]]; then
    echo "Refusing symlink at AppArmor local profile path: $APPARMOR_LOCAL" >&2
    exit 2
  fi
  if [[ -e "$APPARMOR_LOCAL" ]]; then
    APPARMOR_LOCAL_PREEXISTED=1
    if grep -Fq '# XIDER X-DOCK BEGIN' "$APPARMOR_LOCAL"; then
      echo "X-DOCK AppArmor rules already exist; refusing to duplicate them." >&2
      exit 2
    fi
  fi
fi
[[ -d "$DOCK_DIR" ]] && DOCK_DIR_PREEXISTED=1
[[ -d "$DATA_DIR" ]] && DATA_DIR_PREEXISTED=1
[[ -d "$WEBROOT" ]] && WEBROOT_PREEXISTED=1
if ! systemctl is-enabled --quiet "$BOT_SERVICE" || ! systemctl is-active --quiet "$BOT_SERVICE"; then
  echo "$BOT_SERVICE must be enabled and active before X-DOCK setup." >&2
  exit 2
fi
if grep -Eiq '^[[:space:]]*(export[[:space:]]+)?XIDER_DOCK_ENABLED[[:space:]]*=[[:space:]]*(1|true|yes)([[:space:]]|$)' "$BOT_ENV"; then
  echo "X-DOCK is already enabled in $BOT_ENV; refusing to replace broker credentials." >&2
  exit 2
fi
if [[ -e "$DATA_DIR" && -n "$(find "$DATA_DIR" -mindepth 1 -maxdepth 1 -print -quit)" ]]; then
  echo "X-DOCK data directory is not empty; refusing to replace broker state." >&2
  exit 2
fi
if [[ -e "$DOCK_DIR" && -n "$(find "$DOCK_DIR" -mindepth 1 -maxdepth 1 -print -quit)" ]]; then
  echo "X-DOCK config directory is not empty; refusing to replace broker credentials." >&2
  exit 2
fi
if [[ -e "$WEBROOT" && -n "$(find "$WEBROOT" -mindepth 1 -maxdepth 1 -print -quit)" ]]; then
  echo "X-DOCK ACME webroot is not empty; refusing to replace existing files." >&2
  exit 2
fi
if [[ -e "$BROKER_UNIT" || -e "$NGINX_SITE" || -L "$BROKER_UNIT" || -L "$NGINX_SITE" || \
      -e "$RENEW_SERVICE" || -e "$RENEW_TIMER" || -L "$RENEW_SERVICE" || -L "$RENEW_TIMER" || \
      -e "$WS_GUARD_UNIT" || -e "$WS_GUARD_SCRIPT" || -e "$WS_BROKER_DROPIN" || \
      -e "$CERT_HOOK" || -L "$CERT_HOOK" || -e "$CERT_RENEWAL" || \
      -e "/etc/letsencrypt/live/$CERT_NAME" || -L "/etc/letsencrypt/live/$CERT_NAME" || \
      -e "/etc/letsencrypt/archive/$CERT_NAME" || -L "/etc/letsencrypt/archive/$CERT_NAME" ]]; then
  echo "X-DOCK broker state already exists; refusing to overwrite it." >&2
  exit 2
fi
if [[ -e "$BOT_ENV_BACKUP" || -L "$BOT_ENV_BACKUP" ]]; then
  echo "Backup path $BOT_ENV_BACKUP already exists; move it before setup." >&2
  exit 2
fi
if ! python3 - "$BOT_ENV" <<'PY'
import pathlib, re, sys
matches = []
for line in pathlib.Path(sys.argv[1]).read_text(encoding="utf-8-sig").splitlines():
    match = re.match(r"^\s*(?:export\s+)?MQTT_PREFIX\s*=\s*(.*?)\s*$", line)
    if match:
        value = match.group(1).split("#", 1)[0].strip().strip("'\"")
        matches.append(value)
raise SystemExit(0 if matches == ["xgent/v1"] else 1)
PY
then
  echo "The bot must have exactly one MQTT_PREFIX=xgent/v1 setting for this X-DOCK broker." >&2
  exit 2
fi
listeners="$(ss -H -ltn | awk '{print $4}')"
if printf '%s\n' "$listeners" | grep -Eq ':(8787|8884|18884)$'; then
  echo "One of TCP/8787, 8884, or 18884 is already listening; resolve the conflict before setup." >&2
  exit 2
fi
if ! systemctl is-active --quiet nginx.service && printf '%s\n' "$listeners" | grep -Eq ':(80|443)$'; then
  echo "TCP/80 or 443 is occupied while Nginx is inactive; resolve the conflict before setup." >&2
  exit 2
fi
if dpkg-query -W -f='${Status}' mosquitto 2>/dev/null | grep -q 'install ok installed'; then
  MOSQUITTO_WAS_INSTALLED=1
  if systemctl is-active --quiet mosquitto.service || systemctl is-enabled --quiet mosquitto.service; then
    echo "The stock Mosquitto service is active or enabled; refusing to modify an existing broker." >&2
    exit 2
  fi
fi
if dpkg-query -W -f='${Status}' nginx 2>/dev/null | grep -q 'install ok installed'; then
  NGINX_WAS_INSTALLED=1
  systemctl is-active --quiet nginx.service && NGINX_WAS_ACTIVE=1 || true
  systemctl is-enabled --quiet nginx.service && NGINX_WAS_ENABLED=1 || true
fi

# Snapshot snapd state before the transaction. Installing the newer Certbot
# snap may start/enable snapd even when a later X-DOCK step fails.
if dpkg-query -W -f='${Status}' snapd 2>/dev/null | grep -q 'install ok installed'; then
  SNAPD_PACKAGE_PREEXISTED=1
fi
systemctl is-active --quiet snapd.socket && SNAPD_SOCKET_ACTIVE_BEFORE=1 || true
systemctl is-enabled --quiet snapd.socket && SNAPD_SOCKET_ENABLED_BEFORE=1 || true
systemctl is-active --quiet snapd.service && SNAPD_SERVICE_ACTIVE_BEFORE=1 || true
systemctl is-enabled --quiet snapd.service && SNAPD_SERVICE_ENABLED_BEFORE=1 || true
if command -v snap >/dev/null 2>&1; then
  SNAP_CERTBOT_REVISION_BEFORE="$(snap list certbot 2>/dev/null | awk 'NR == 2 { print $3 }' || true)"
  if [[ -n "$SNAP_CERTBOT_REVISION_BEFORE" ]]; then SNAP_CERTBOT_PREEXISTED=1; fi
fi

cleanup_on_exit() {
  local code=$?
  trap - EXIT ERR INT TERM HUP
  if [[ "$SETUP_COMPLETE" -eq 1 ]]; then return "$code"; fi
  if [[ "$UFW_HTTP_RULE_ADDED" -eq 1 ]] && command -v ufw >/dev/null 2>&1; then
    ufw --force delete allow 80/tcp >/dev/null 2>&1 || \
      echo "Could not remove the temporary X-DOCK HTTP firewall rule; review UFW manually." >&2
  fi
  if [[ "$BOT_ENV_CHANGED" -eq 1 && -n "$BOT_ENV_BACKUP" && -f "$BOT_ENV_BACKUP" ]]; then
    if cp -a "$BOT_ENV_BACKUP" "$BOT_ENV"; then
      rm -f -- "$BOT_ENV_BACKUP"
      systemctl restart "$BOT_SERVICE" >/dev/null 2>&1 || true
    else
      systemctl stop "$BOT_SERVICE" >/dev/null 2>&1 || true
      echo "Could not restore $BOT_ENV; saved copy remains at $BOT_ENV_BACKUP." >&2
    fi
  elif [[ -n "$BOT_ENV_BACKUP" ]]; then
    rm -f -- "$BOT_ENV_BACKUP"
  fi
  if [[ "$RENEW_TIMER_INSTALLED" -eq 1 ]]; then
    systemctl disable --now xider-dock-cert-renew.timer >/dev/null 2>&1 || true
    rm -f -- "$RENEW_SERVICE" "$RENEW_TIMER"
  fi
  systemctl disable --now "$BROKER_SERVICE" >/dev/null 2>&1 || true
  rm -f -- "$BROKER_UNIT"
  systemctl disable --now xider-ws-firewall.service >/dev/null 2>&1 || true
  rm -f -- "$WS_GUARD_UNIT" "$WS_GUARD_SCRIPT" "$WS_BROKER_DROPIN"
  if [[ "$APPARMOR_OVERRIDE_CHANGED" -eq 1 ]]; then
    if [[ "$APPARMOR_LOCAL_PREEXISTED" -eq 1 && -f "$STAGE/apparmor-local.backup" ]]; then
      install -o root -g root -m 0644 "$STAGE/apparmor-local.backup" "$APPARMOR_LOCAL"
    else
      rm -f -- "$APPARMOR_LOCAL"
    fi
    if [[ "$APPARMOR_WAS_ACTIVE" -eq 1 ]] && ! apparmor_parser -r "$APPARMOR_PROFILE" >/dev/null 2>&1; then
      echo "Could not reload the restored Mosquitto AppArmor profile; review it before retrying." >&2
    fi
  fi
  rm -f -- "$CERT_HOOK" /usr/local/sbin/xider-dock-cert-deploy "$NGINX_SITE"
  systemctl daemon-reload >/dev/null 2>&1 || true
  if [[ "$NGINX_WAS_ACTIVE" -eq 1 ]]; then
    if command -v nginx >/dev/null 2>&1 && nginx -t >/dev/null 2>&1; then
      systemctl reload nginx >/dev/null 2>&1 || true
    fi
  elif [[ "$NGINX_STARTED_BY_XIDER" -eq 1 ]]; then
    systemctl stop nginx.service >/dev/null 2>&1 || true
    if [[ "$NGINX_WAS_ENABLED" -eq 0 ]]; then
      systemctl disable nginx.service >/dev/null 2>&1 || true
    fi
  fi
  if [[ -f "$CERT_RENEWAL" && -n "$CERTBOT_BIN" && -x "$CERTBOT_BIN" ]]; then
    "$CERTBOT_BIN" delete --non-interactive --cert-name "$CERT_NAME" >/dev/null 2>&1 || true
  fi
  local snap_cleanup_ok=1
  if [[ "$SNAP_CERTBOT_INSTALLED_BY_XIDER" -eq 1 ]] && command -v snap >/dev/null 2>&1; then
    if snap list certbot >/dev/null 2>&1 && ! snap remove certbot --purge >/dev/null 2>&1; then
      snap_cleanup_ok=0
      echo "Could not remove the Certbot snap installed by this attempt; leaving snapd intact." >&2
    fi
  elif [[ "$SNAP_CERTBOT_PREEXISTED" -eq 1 && -n "$SNAP_CERTBOT_REVISION_BEFORE" ]] && command -v snap >/dev/null 2>&1; then
    current_certbot_revision="$(snap list certbot 2>/dev/null | awk 'NR == 2 { print $3 }' || true)"
    if [[ -n "$current_certbot_revision" && "$current_certbot_revision" != "$SNAP_CERTBOT_REVISION_BEFORE" ]] &&
       ! snap revert certbot >/dev/null 2>&1; then
      echo "Could not revert the Certbot snap refresh; review 'snap changes' manually." >&2
    fi
  fi
  if [[ "$snap_cleanup_ok" -eq 1 ]]; then
    if [[ "$SNAPD_SOCKET_ACTIVE_BEFORE" -eq 0 ]]; then
      systemctl stop snapd.socket >/dev/null 2>&1 || true
    fi
    if [[ "$SNAPD_SOCKET_ENABLED_BEFORE" -eq 0 ]]; then
      systemctl disable snapd.socket >/dev/null 2>&1 || true
    fi
    if [[ "$SNAPD_SERVICE_ACTIVE_BEFORE" -eq 0 ]]; then
      systemctl stop snapd.service >/dev/null 2>&1 || true
    fi
    if [[ "$SNAPD_SERVICE_ENABLED_BEFORE" -eq 0 ]]; then
      systemctl disable snapd.service >/dev/null 2>&1 || true
    fi
    if [[ "$SNAPD_PACKAGE_PREEXISTED" -eq 0 ]]; then
      if ! apt-get purge -y -- snapd >/dev/null 2>&1; then
        echo "Could not remove snapd installed by this attempt; review the package state manually." >&2
      fi
    fi
  fi
  systemctl daemon-reload >/dev/null 2>&1 || true
  rm -rf -- "/etc/letsencrypt/live/$CERT_NAME" "/etc/letsencrypt/archive/$CERT_NAME"
  rm -f -- "$CERT_RENEWAL"
  rm -f -- "$DOCK_DIR/admin-password" "$DOCK_DIR/bootstrap.key" \
    "$DOCK_DIR/bootstrap.crt" "$DOCK_DIR/mosquitto-bootstrap.conf" \
    "$DOCK_DIR/mosquitto.conf" "$DOCK_DIR/tls/fullchain.pem" "$DOCK_DIR/tls/privkey.pem"
  rmdir "$DOCK_DIR/tls" 2>/dev/null || true
  if [[ "$DOCK_DIR_PREEXISTED" -eq 0 ]]; then rmdir "$DOCK_DIR" 2>/dev/null || true; fi
  rm -f -- "$DATA_DIR/dynamic-security.json"
  rm -rf -- "$DATA_DIR/data"
  if [[ "$DATA_DIR_PREEXISTED" -eq 0 ]]; then rmdir "$DATA_DIR" 2>/dev/null || true; fi
  rmdir "$WEBROOT/.well-known/acme-challenge" "$WEBROOT/.well-known" 2>/dev/null || true
  if [[ "$WEBROOT_PREEXISTED" -eq 0 ]]; then rmdir "$WEBROOT" 2>/dev/null || true; fi
  if [[ "$MOSQUITTO_WAS_INSTALLED" -eq 0 ]]; then
    systemctl disable mosquitto.service >/dev/null 2>&1 || true
    systemctl stop mosquitto.service >/dev/null 2>&1 || true
  fi
  if [[ "$MOSQUITTO_RUNTIME_MASKED" -eq 1 ]]; then
    systemctl unmask --runtime mosquitto.service >/dev/null 2>&1 || true
  fi
  if [[ "$NGINX_RUNTIME_MASKED" -eq 1 ]]; then
    systemctl unmask --runtime nginx.service >/dev/null 2>&1 || true
  fi
  if [[ -n "$STAGE" && -d "$STAGE" ]]; then rm -rf -- "$STAGE"; fi
  echo "X-DOCK setup stopped; generated services and partial state were rolled back. See the error above." >&2
  exit "$code"
}
trap cleanup_on_exit EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
trap 'exit 129' HUP

export DEBIAN_FRONTEND=noninteractive
# Prevent dpkg's service helper from briefly launching the stock listener on
# 1883 while installing Mosquitto. The runtime mask is removed immediately
# after package configuration and does not alter the persistent unit state.
if [[ "$(systemctl show mosquitto.service -p LoadState --value 2>/dev/null || true)" != "masked" ]]; then
  systemctl mask --runtime mosquitto.service
  MOSQUITTO_RUNTIME_MASKED=1
fi
if [[ "$NGINX_WAS_INSTALLED" -eq 0 && "$(systemctl show nginx.service -p LoadState --value 2>/dev/null || true)" != "masked" ]]; then
  systemctl mask --runtime nginx.service
  NGINX_RUNTIME_MASKED=1
fi
apt-get update
apt-get install -y --no-install-recommends mosquitto mosquitto-clients nginx openssl ca-certificates
if [[ "$MOSQUITTO_RUNTIME_MASKED" -eq 1 ]]; then
  systemctl unmask --runtime mosquitto.service
  MOSQUITTO_RUNTIME_MASKED=0
fi
if [[ "$NGINX_RUNTIME_MASKED" -eq 1 ]]; then
  systemctl unmask --runtime nginx.service
  NGINX_RUNTIME_MASKED=0
fi
if [[ "$MOSQUITTO_WAS_INSTALLED" -eq 0 ]]; then
  systemctl disable --now mosquitto.service >/dev/null 2>&1 || true
else
  systemctl stop mosquitto.service >/dev/null 2>&1 || true
fi
if [[ "$NGINX_WAS_INSTALLED" -eq 0 ]]; then
  systemctl disable --now nginx.service >/dev/null 2>&1 || true
fi

certbot_supports_ip_webroot() {
  local candidate="$1"
  [[ -n "$candidate" && -x "$candidate" ]] || return 1
  "$candidate" --version 2>/dev/null \
    | awk '{split($2,v,"."); if ((v[1]+0)>5 || ((v[1]+0)==5 && (v[2]+0)>=4)) ok=1} END {exit !ok}' \
    || return 1
  "$candidate" --help all 2>&1 | grep -q -- '--ip-address'
}

certbot_scheduler_active() {
  if [[ "$CERTBOT_BIN" == /snap/bin/certbot ]]; then
    systemctl is-active --quiet snap.certbot.renew.timer && return 0
    systemctl is-enabled --quiet snap.certbot.renew.timer && return 0
    return 1
  fi
  systemctl is-active --quiet certbot.timer && return 0
  systemctl is-enabled --quiet certbot.timer && return 0
  grep -RqsE '(^|[[:space:]/])certbot([[:space:]]|$).*renew' \
    /etc/crontab /etc/cron.d /etc/cron.hourly /etc/cron.daily /etc/cron.weekly 2>/dev/null
}

if ! certbot_supports_ip_webroot "$CERTBOT_BIN"; then
  # Ubuntu's apt repository can lag behind the Certbot release that added IP
  # webroot certificates. The official snap is the recommended install path;
  # install the official snap alongside any existing OS package. Existing
  # unrelated certificates must not lose their system Certbot installation.
  if ! command -v snap >/dev/null 2>&1; then
    apt-get install -y --no-install-recommends snapd
  fi
  if ! command -v snap >/dev/null 2>&1; then
    echo "Snap is unavailable and the installed Certbot lacks IP webroot support; refusing an untrusted fallback." >&2
    exit 3
  fi
  systemctl enable --now snapd.socket
  if dpkg-query -W -f='${Status}' certbot 2>/dev/null | grep -q 'install ok installed'; then
    echo "Keeping the existing OS Certbot package; X-DOCK will use the snap binary." >&2
  fi
  if snap list certbot >/dev/null 2>&1; then
    snap refresh certbot
  else
    SNAP_CERTBOT_INSTALLED_BY_XIDER=1
    snap install --classic certbot
  fi
  CERTBOT_BIN=/snap/bin/certbot
fi
if ! certbot_supports_ip_webroot "$CERTBOT_BIN"; then
  echo "Certbot 5.4+ with --ip-address support is required; refusing an untrusted fallback." >&2
  exit 3
fi
PLUGIN_PATH="$(dpkg -L mosquitto 2>/dev/null | awk '/\/mosquitto_dynamic_security\.so$/ {print; exit}')"
if [[ -z "$PLUGIN_PATH" || ! -r "$PLUGIN_PATH" ]]; then
  echo "Mosquitto Dynamic Security plugin was not found in the installed package." >&2
  exit 3
fi
install -d -o root -g mosquitto -m 0750 "$DOCK_DIR" "$DOCK_DIR/tls"
install -d -o mosquitto -g mosquitto -m 0750 "$DATA_DIR" "$DATA_DIR/data"
install -d -o root -g root -m 0755 "$WEBROOT/.well-known/acme-challenge"
STAGE="$(mktemp -d /var/tmp/xider-dock.XXXXXX)"
chmod 0700 "$STAGE"
if [[ "$APPARMOR_LOCAL_PREEXISTED" -eq 1 ]]; then
  cp -a "$APPARMOR_LOCAL" "$STAGE/apparmor-local.backup"
fi

ADMIN_PASSWORD="$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')"
BOT_PASSWORD="$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')"
install -o root -g mosquitto -m 0640 /dev/null "$DOCK_DIR/admin-password"
printf '%s\n' "$ADMIN_PASSWORD" > "$DOCK_DIR/admin-password"
chmod 0640 "$DOCK_DIR/admin-password"
chown root:mosquitto "$DOCK_DIR/admin-password"
# Dynamic Security requires its initial JSON database to exist before the
# broker starts. Create it offline so the bootstrap admin secret never needs
# an exposed listener or a password-init side channel.
mosquitto_ctrl dynsec init "$DATA_DIR/dynamic-security.json" admin "$ADMIN_PASSWORD"
chown mosquitto:mosquitto "$DATA_DIR/dynamic-security.json"
chmod 0600 "$DATA_DIR/dynamic-security.json"

openssl req -x509 -newkey rsa:3072 -nodes -sha256 -days 2 \
  -keyout "$DOCK_DIR/bootstrap.key" -out "$DOCK_DIR/bootstrap.crt" \
  -subj '/CN=localhost' \
  -addext 'subjectAltName=DNS:localhost,IP:127.0.0.1' \
  -addext 'basicConstraints=critical,CA:TRUE' \
  -addext 'keyUsage=critical,digitalSignature,keyEncipherment,keyCertSign' \
  -addext 'extendedKeyUsage=serverAuth' >/dev/null 2>&1
chown root:mosquitto "$DOCK_DIR/bootstrap.key" "$DOCK_DIR/bootstrap.crt"
chmod 0640 "$DOCK_DIR/bootstrap.key" "$DOCK_DIR/bootstrap.crt"

python3 "$APP_DIR/broker/x_dock_config.py" \
  --plugin "$PLUGIN_PATH" --security-file "$DATA_DIR/dynamic-security.json" \
  --data-dir "$DATA_DIR/data" --cert-file "$DOCK_DIR/bootstrap.crt" \
  --key-file "$DOCK_DIR/bootstrap.key" --port 18884 --bind-address 127.0.0.1 \
  --output "$DOCK_DIR/mosquitto-bootstrap.conf"
chown root:mosquitto "$DOCK_DIR/mosquitto-bootstrap.conf"
chmod 0640 "$DOCK_DIR/mosquitto-bootstrap.conf"

if [[ "$APPARMOR_WAS_ACTIVE" -eq 1 ]]; then
  install -d -o root -g root -m 0755 "$(dirname "$APPARMOR_LOCAL")"
  APPARMOR_OVERRIDE_CHANGED=1
  cat >> "$APPARMOR_LOCAL" <<'EOF'

# XIDER X-DOCK BEGIN
/etc/xider-dock/ r,
/etc/xider-dock/** r,
/var/lib/xider-dock/ r,
/var/lib/xider-dock/** rwk,
# XIDER X-DOCK END
EOF
  chown root:root "$APPARMOR_LOCAL"
  chmod 0644 "$APPARMOR_LOCAL"
  apparmor_parser -r "$APPARMOR_PROFILE"
fi

cat > "$STAGE/xider-dock-mqtt.service" <<EOF
[Unit]
Description=XIDER X-DOCK per-device MQTT broker
Requires=xider-ws-firewall.service
After=xider-ws-firewall.service
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=mosquitto
Group=mosquitto
ExecStart=/usr/sbin/mosquitto -c $DOCK_DIR/mosquitto-bootstrap.conf
Restart=on-failure
RestartSec=3
UMask=0077
NoNewPrivileges=true
PrivateTmp=true
ProtectHome=true
ProtectSystem=strict
ReadWritePaths=$DATA_DIR
RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6

[Install]
WantedBy=multi-user.target
EOF
install -o root -g root -m 0644 "$STAGE/xider-dock-mqtt.service" "$BROKER_UNIT"
install -o root -g root -m 0750 "$APP_DIR/deploy/xider-ws-firewall.sh" "$WS_GUARD_SCRIPT"
install -o root -g root -m 0644 "$APP_DIR/deploy/xider-ws-firewall.service" "$WS_GUARD_UNIT"
systemctl daemon-reload
systemctl enable --now xider-ws-firewall.service
"$WS_GUARD_SCRIPT"
systemctl enable --now "$BROKER_SERVICE"

for attempt in {1..20}; do
  if ss -ltn | awk '{print $4}' | grep -q '127.0.0.1:18884$'; then break; fi
  sleep 1
done
ss -ltn | awk '{print $4}' | grep -q '127.0.0.1:18884$'

export XIDER_DOCK_ADMIN_USERNAME=admin
export XIDER_DOCK_ADMIN_PASSWORD="$ADMIN_PASSWORD"
export XIDER_DOCK_BOT_USERNAME=xider-bot
export XIDER_DOCK_BOT_PASSWORD="$BOT_PASSWORD"
PYTHONPATH="$APP_DIR${PYTHONPATH:+:$PYTHONPATH}" \
  "$APP_DIR/TG-BOT-SERVER/venv/bin/python" "$APP_DIR/broker/x_dock_provision.py" \
  --host 127.0.0.1 --port 18884 --ca-file "$DOCK_DIR/bootstrap.crt"
rm -f -- "$DOCK_DIR/admin-password" "$DOCK_DIR/bootstrap.key" "$DOCK_DIR/bootstrap.crt" "$DOCK_DIR/mosquitto-bootstrap.conf"

cat > "$STAGE/nginx-http.conf" <<EOF
server {
    listen 80;
    listen [::]:80;
    server_name $PUBLIC_IP;
    location ^~ /.well-known/acme-challenge/ {
        root $WEBROOT;
        default_type text/plain;
        try_files \$uri =404;
    }
    location / { return 404; }
}
EOF
install -o root -g root -m 0644 "$STAGE/nginx-http.conf" "$NGINX_SITE"
nginx -t
if [[ "$NGINX_WAS_ACTIVE" -eq 1 ]]; then
  systemctl reload nginx
else
  NGINX_STARTED_BY_XIDER=1
  systemctl enable --now nginx
fi
if command -v ufw >/dev/null 2>&1 && ufw status | grep -q '^Status: active$'; then
  if ! ufw status | awk '$1 == "80/tcp" && $2 != "(v6)" && /ALLOW/ && /Anywhere/ { found = 1 } END { exit !found }'; then
    # ACME HTTP-01 must reach Nginx before the public HTTPS listener exists.
    # Track only a rule added by this transaction so rollback preserves prior UFW state.
    UFW_HTTP_RULE_ADDED=1
    ufw allow 80/tcp
  fi
fi
"$CERTBOT_BIN" certonly --non-interactive --agree-tos --email "$CERT_EMAIL" \
  --webroot --webroot-path "$WEBROOT" --ip-address "$PUBLIC_IP" \
  --preferred-profile shortlived --cert-name "$CERT_NAME"
[[ -s "$CERT_LINEAGE/fullchain.pem" && -s "$CERT_LINEAGE/privkey.pem" ]]
openssl x509 -in "$CERT_LINEAGE/fullchain.pem" -noout -ext subjectAltName \
  | grep -Fq "IP Address:$PUBLIC_IP"

install -o root -g mosquitto -m 0640 "$CERT_LINEAGE/fullchain.pem" "$DOCK_DIR/tls/fullchain.pem"
install -o root -g mosquitto -m 0640 "$CERT_LINEAGE/privkey.pem" "$DOCK_DIR/tls/privkey.pem"
python3 "$APP_DIR/broker/x_dock_config.py" \
  --plugin "$PLUGIN_PATH" --security-file "$DATA_DIR/dynamic-security.json" \
  --data-dir "$DATA_DIR/data" --cert-file "$DOCK_DIR/tls/fullchain.pem" \
  --key-file "$DOCK_DIR/tls/privkey.pem" --port 8884 --bind-address 127.0.0.1 \
  --websocket-port 18885 \
  --output "$STAGE/mosquitto-public.conf"
install -o root -g mosquitto -m 0640 "$STAGE/mosquitto-public.conf" "$BROKER_CONFIG"
sed -i "s#ExecStart=/usr/sbin/mosquitto -c .*#ExecStart=/usr/sbin/mosquitto -c $BROKER_CONFIG#" "$BROKER_UNIT"
systemctl daemon-reload
systemctl restart "$BROKER_SERVICE"

cat > "$STAGE/nginx-public.conf" <<EOF
server {
    listen 80;
    listen [::]:80;
    server_name $PUBLIC_IP;
    location ^~ /.well-known/acme-challenge/ {
        root $WEBROOT;
        default_type text/plain;
        try_files \$uri =404;
    }
    location / { return 404; }
}

server {
    listen 443 ssl;
    listen [::]:443 ssl;
    server_name $PUBLIC_IP;
    ssl_certificate $DOCK_DIR/tls/fullchain.pem;
    ssl_certificate_key $DOCK_DIR/tls/privkey.pem;
    ssl_protocols TLSv1.2 TLSv1.3;
    client_max_body_size 4k;
    location = /x-dock/v1/request {
        limit_except POST { deny all; }
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-Proto https;
        proxy_pass http://127.0.0.1:8787;
    }
    location = /x-dock/v1/poll {
        limit_except POST { deny all; }
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-Proto https;
        proxy_pass http://127.0.0.1:8787;
    }
    location = /mqtt {
        proxy_http_version 1.1;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_read_timeout 1h;
        proxy_buffering off;
        proxy_pass http://127.0.0.1:18885;
    }
    location / { return 404; }
}
EOF
install -o root -g root -m 0644 "$STAGE/nginx-public.conf" "$NGINX_SITE"
nginx -t
systemctl reload nginx

cat > "$STAGE/xider-dock-cert-deploy" <<EOF
#!/usr/bin/env bash
set -Eeuo pipefail
LINEAGE="\${RENEWED_LINEAGE:-}"
[[ -n "\$LINEAGE" && -s "\$LINEAGE/fullchain.pem" && -s "\$LINEAGE/privkey.pem" ]]
if [[ "\$LINEAGE" != "/etc/letsencrypt/live/$CERT_NAME" ]]; then exit 0; fi
install -o root -g mosquitto -m 0640 "\$LINEAGE/fullchain.pem" $DOCK_DIR/tls/fullchain.pem
install -o root -g mosquitto -m 0640 "\$LINEAGE/privkey.pem" $DOCK_DIR/tls/privkey.pem
systemctl restart $BROKER_SERVICE
nginx -t
systemctl reload nginx
EOF
install -o root -g root -m 0750 "$STAGE/xider-dock-cert-deploy" /usr/local/sbin/xider-dock-cert-deploy
install -d -o root -g root -m 0755 /etc/letsencrypt/renewal-hooks/deploy
ln -s /usr/local/sbin/xider-dock-cert-deploy \
  /etc/letsencrypt/renewal-hooks/deploy/50-xider-dock

# Prefer the scheduler installed by Certbot (apt or snap). Only add an X-DOCK
# timer when this host has no active or enabled Certbot timer/cron, avoiding duplicates.
if ! certbot_scheduler_active; then
  cat > "$STAGE/xider-dock-cert-renew.service" <<EOF
[Unit]
Description=Renew the XIDER X-DOCK short-lived IP certificate

[Service]
Type=oneshot
ExecStart=$CERTBOT_BIN renew --quiet --cert-name $CERT_NAME
EOF
  cat > "$STAGE/xider-dock-cert-renew.timer" <<'EOF'
[Unit]
Description=Check the XIDER X-DOCK short-lived certificate renewal window

[Timer]
OnCalendar=*-*-* 00,06,12,18:17:00
RandomizedDelaySec=15m
Persistent=true

[Install]
WantedBy=timers.target
EOF
  RENEW_TIMER_INSTALLED=1
  install -o root -g root -m 0644 "$STAGE/xider-dock-cert-renew.service" "$RENEW_SERVICE"
  install -o root -g root -m 0644 "$STAGE/xider-dock-cert-renew.timer" "$RENEW_TIMER"
  systemctl daemon-reload
  systemctl enable --now xider-dock-cert-renew.timer
fi

install -d -o xider -g xider -m 0700 /etc/xider/device-keys /var/lib/xider
cp -a "$BOT_ENV" "$BOT_ENV.pre-x-dock"
export XIDER_DOCK_ENABLED=true
export XIDER_DOCK_DB=/var/lib/xider/enrollment.sqlite3
export XIDER_DEVICE_KEYS_DIR=/etc/xider/device-keys
export XIDER_DOCK_BROKER_HOST="$PUBLIC_IP"
export XIDER_DOCK_BROKER_PORT=8884
export XIDER_DOCK_WS_PORT=18885
export XIDER_DOCK_PUBLIC_PORT=443
export XIDER_DOCK_BOT_USERNAME=xider-bot
export XIDER_DOCK_BOT_PASSWORD="$BOT_PASSWORD"
export XIDER_DOCK_ADMIN_USERNAME=admin
export XIDER_DOCK_ADMIN_PASSWORD="$ADMIN_PASSWORD"
export XIDER_DOCK_HTTP_PORT=8787
BOT_ENV_CHANGED=1
python3 - "$BOT_ENV" <<'PY'
import os, pathlib, re, sys, tempfile
path = pathlib.Path(sys.argv[1])
keys = (
    "XIDER_DOCK_ENABLED", "XIDER_DOCK_DB", "XIDER_DEVICE_KEYS_DIR",
    "XIDER_DOCK_BROKER_HOST", "XIDER_DOCK_BROKER_PORT",
    "XIDER_DOCK_WS_PORT", "XIDER_DOCK_PUBLIC_PORT",
    "XIDER_DOCK_BOT_USERNAME", "XIDER_DOCK_BOT_PASSWORD",
    "XIDER_DOCK_ADMIN_USERNAME", "XIDER_DOCK_ADMIN_PASSWORD",
    "XIDER_DOCK_HTTP_PORT",
)
values = {key: os.environ[key] for key in keys}
lines = path.read_text(encoding="utf-8-sig").splitlines()
for key in keys:
    if sum(bool(re.match(rf"^\s*(?:export\s+)?{re.escape(key)}\s*=", line)) for line in lines) > 1:
        raise SystemExit(f"duplicate {key} in bot env")
for key in keys:
    pattern = re.compile(rf"^\s*(?:export\s+)?{re.escape(key)}\s*=")
    lines = [line for line in lines if not pattern.match(line)]
lines.extend(f"{key}={values[key]}" for key in keys)
fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".x-dock.tmp", dir=path.parent)
temporary = pathlib.Path(temporary_name)
try:
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
        stream.write("\n".join(lines) + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.chmod(temporary, 0o640)
    os.chown(temporary, 0, os.stat(path).st_gid)
    os.replace(temporary, path)
finally:
    temporary.unlink(missing_ok=True)
PY
unset BOT_PASSWORD ADMIN_PASSWORD XIDER_DOCK_BOT_PASSWORD XIDER_DOCK_ADMIN_PASSWORD
chown root:xider "$BOT_ENV"
chmod 0640 "$BOT_ENV"
systemctl restart "$BOT_SERVICE"
for attempt in {1..20}; do
  if systemctl is-active --quiet "$BOT_SERVICE"; then break; fi
  sleep 1
done
systemctl is-active --quiet "$BOT_SERVICE"
nginx -t
ss -ltn | awk '{print $4}' | grep -q ':8884$'
ss -ltn | awk '{print $4}' | grep -qE '(0\.0\.0\.0|\*):18885$'
"$WS_GUARD_SCRIPT"
ss -ltn | awk '{print $4}' | grep -q ':443$'
rm -rf -- "$STAGE"
STAGE=""
SETUP_COMPLETE=1
trap - EXIT INT TERM HUP
if command -v ufw >/dev/null 2>&1 && ufw status | grep -q '^Status: active$'; then
  ufw allow 443/tcp || echo "[WARN] Could not open TCP/443 in UFW; configure the host firewall manually." >&2
fi
echo "[OK] X-DOCK is enabled. HTTPS enrollment URL: https://$PUBLIC_IP"
echo "[INFO] Allow inbound TCP/80 and TCP/443 in the VPS security group; MQTT stays on loopback."
echo "[INFO] Certificate renewal is scheduled; deploy hook reloads X-DOCK services after renewal."
