#!/usr/bin/env bash
set -Eeuo pipefail

APP_DIR="${APP_DIR:-/opt/xider}"
REPO_URL="${REPO_URL:-https://github.com/invinby/XIDER.git}"
BRANCH="${BRANCH:-main}"
ENV_FILE="${ENV_FILE:-/etc/xider/bot.env}"

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run as root (the wrapper uses sudo)." >&2
  exit 2
fi

required=(SHARED_KEY BOT_TOKEN ADMIN_ID MQTT_BROKER MQTT_PORT MQTT_TLS MQTT_USERNAME MQTT_PASSWORD ENCRYPT_PAYLOAD)
if [[ ! -r "${ENV_FILE}" ]]; then
  echo "Missing ${ENV_FILE}; upload the runtime env first." >&2
  exit 3
fi

# Windows PowerShell 5.1 writes UTF-8 with a BOM. Normalize only the first
# byte sequence so SHARED_KEY remains readable by grep and dotenv alike.
sed -i '1s/^\xEF\xBB\xBF//' "${ENV_FILE}"
sed -i 's/\r$//' "${ENV_FILE}"

for key in "${required[@]}"; do
  if ! grep -Eq "^[[:space:]]*${key}=[^#[:space:]]+" "${ENV_FILE}"; then
    echo "Missing ${key} in ${ENV_FILE}." >&2
    exit 4
  fi
done

tls="$(sed -n 's/^[[:space:]]*MQTT_TLS=//p' "${ENV_FILE}" | tail -n 1 | tr '[:upper:]' '[:lower:]')"
encrypt="$(sed -n 's/^[[:space:]]*ENCRYPT_PAYLOAD=//p' "${ENV_FILE}" | tail -n 1 | tr '[:upper:]' '[:lower:]')"
if [[ "${tls}" != "true" && "${tls}" != "1" && "${tls}" != "yes" ]]; then
  echo "Refusing to start: MQTT_TLS must be true." >&2
  exit 5
fi
if [[ "${encrypt}" != "true" && "${encrypt}" != "1" && "${encrypt}" != "yes" ]]; then
  echo "Refusing to start: ENCRYPT_PAYLOAD must be true." >&2
  exit 6
fi

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y --no-install-recommends git python3 python3-venv python3-pip ca-certificates

install -d -m 0750 /etc/xider
if ! id -u xider >/dev/null 2>&1; then
  useradd --system --home-dir "${APP_DIR}" --create-home --shell /usr/sbin/nologin xider
fi

if [[ "${SKIP_REPO_SYNC:-0}" != "1" ]]; then
  if [[ ! -d "${APP_DIR}/.git" ]]; then
    git clone --depth 1 --branch "${BRANCH}" "${REPO_URL}" "${APP_DIR}"
  else
    git -C "${APP_DIR}" fetch --depth 1 origin "${BRANCH}"
    git -C "${APP_DIR}" merge --ff-only "origin/${BRANCH}"
  fi
fi
if [[ ! -f "${APP_DIR}/TG-BOT-SERVER/requirements.txt" ]]; then
  echo "XIDER source is missing under ${APP_DIR}." >&2
  exit 7
fi

python3 -m venv "${APP_DIR}/TG-BOT-SERVER/venv"
"${APP_DIR}/TG-BOT-SERVER/venv/bin/python" -m pip install --upgrade pip
"${APP_DIR}/TG-BOT-SERVER/venv/bin/pip" install --requirement "${APP_DIR}/TG-BOT-SERVER/requirements.txt"

install -m 0644 "${APP_DIR}/deploy/xider-bot.service" /etc/systemd/system/xider-bot.service
chown -R xider:xider "${APP_DIR}"
chown root:xider "${ENV_FILE}"
chmod 0640 "${ENV_FILE}"

systemctl daemon-reload
systemctl enable xider-bot.service
systemctl restart xider-bot.service
systemctl --no-pager --full status xider-bot.service --lines=20
