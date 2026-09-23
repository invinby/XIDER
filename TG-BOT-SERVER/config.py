"""Конфигурация Telegram-бота XGENT.

Секреты загружаются из файла .env (рядом со скриптом).
SHARED_KEY, BOT_TOKEN, ADMIN_ID ОБЯЗАНЫ быть заданы в .env (fail-closed).
"""

import os
import sys
from pathlib import Path

from dotenv import load_dotenv

# Загружаем .env из каталога скрипта (с поддержкой UTF-8 BOM).
_ENV_FILE = Path(__file__).resolve().parent / ".env"
try:
    load_dotenv(_ENV_FILE, encoding="utf-8-sig")
except TypeError:
    load_dotenv(_ENV_FILE)


def _require_env(name: str, default: str | None = None) -> str:
    val = (os.getenv(name) or os.getenv(f"\ufeff{name}") or default or "").strip()
    if not val:
        sys.stderr.write(
            f"FATAL: {name} не задан. Создайте .env рядом со скриптом.\n"
        )
        raise SystemExit(2)
    return val


# ID администратора — единственный пользователь, которому разрешено полное управление.
ADMIN_ID = int(_require_env("ADMIN_ID"))

# Гостевые ID — пользователи с ограниченным доступом (только чтение/статусы).
# Список ID через запятую.
_guest_ids_env = os.getenv("GUEST_IDS", "").strip()
GUEST_IDS = [int(x.strip()) for x in _guest_ids_env.split(",") if x.strip().isdigit()]


# Токен бота от @BotFather.
BOT_TOKEN = _require_env("BOT_TOKEN")

# Публичный MQTT-брокер — не нужен проброс портов, но трафик идёт через
# сторонний сервис. Для личного использования приемлемо при подписи сообщений.
MQTT_BROKER = os.getenv("MQTT_BROKER", "broker.emqx.io")
MQTT_PORT = int(os.getenv("MQTT_PORT", "1883"))
MQTT_PREFIX = os.getenv("MQTT_PREFIX", "xgent/v1")
MQTT_TLS = os.getenv("MQTT_TLS", "false").strip().lower() in ("1", "true", "yes")
MQTT_USERNAME = os.getenv("MQTT_USERNAME", "").strip() or None
MQTT_PASSWORD = os.getenv("MQTT_PASSWORD", "").strip() or None

# Шифрование payload (AES-256-GCM) поверх HMAC-подписи.
# Должно быть true ОДНОВРЕМЕННО во всех трёх компонентах.
# При false (по умолчанию) формат сообщений не меняется — обратная совместимость.
ENCRYPT_PAYLOAD = os.getenv("ENCRYPT_PAYLOAD", "false").strip().lower() in ("1", "true", "yes")

# Общий секрет HMAC-SHA256. ДОЛЖЕН совпадать во всех трёх папках проекта:
# TG-BOT-SERVER, XGENT-MCS, XGENT-WDS.
# Fail-closed: без явного значения в .env бот НЕ запустится.
DEFAULT_SHARED_KEY = "XGENT-2026-shared-secret"
_env_key = (os.getenv("SHARED_KEY") or os.getenv("\ufeffSHARED_KEY") or "").strip()
if not _env_key:
    sys.stderr.write(
        "FATAL: SHARED_KEY не задан. Создайте .env рядом со скриптом с "
        "строкой SHARED_KEY=<сильный_секрет> (должен совпадать во всех 3 "
        "компонентах). См. .env.example.\n"
    )
    raise SystemExit(2)
if _env_key == DEFAULT_SHARED_KEY:
    sys.stderr.write(
        "FATAL: SHARED_KEY совпадает с публичным дефолтом. Задайте свой "
        "секрет в .env (XIDER-001).\n"
    )
    raise SystemExit(2)
SHARED_KEY = _env_key
