"""Конфигурация клиента XGENT для Windows.

Секреты загружаются из файла .env (рядом со скриптом).
SHARED_KEY ОБЯЗАН быть задан в .env (fail-closed).
"""

import json
import os
import platform
import socket
import sys
import uuid
from pathlib import Path

from dotenv import load_dotenv

# Каталог для поиска .env: рядом с exe (PyInstaller) или рядом со скриптом.
# В onefile-exe __file__ указывает во временную папку распаковки, поэтому
# ориентируемся на расположение самого исполняемого файла.
if getattr(sys, "frozen", False):
    _BASE_DIR = Path(sys.executable).resolve().parent
else:
    _BASE_DIR = Path(__file__).resolve().parent
_ENV_FILE = _BASE_DIR / ".env"
try:
    load_dotenv(_ENV_FILE, encoding="utf-8-sig")
except TypeError:
    load_dotenv(_ENV_FILE)

# Значения, вшитые при сборке exe (файл генерирует build_exe.bat из .env
# на момент сборки; в git не попадает). Приоритет: переменные окружения
# и .env рядом с exe выше встроенных значений.
# Параметры MQTT-брокера. Используется EMQX Cloud Serverless (TLS), без проброса портов.
MQTT_BROKER = os.getenv("MQTT_BROKER", "").strip()
if not MQTT_BROKER:
    raise SystemExit("FATAL: MQTT_BROKER must be set in .env")
MQTT_PORT = int(os.getenv("MQTT_PORT", "1883"))
MQTT_PREFIX = os.getenv("MQTT_PREFIX", "xgent/v1")
MQTT_TLS = os.getenv("MQTT_TLS", "false").strip().lower() in ("1", "true", "yes")
MQTT_USERNAME = os.getenv("MQTT_USERNAME", "").strip() or None
MQTT_PASSWORD = os.getenv("MQTT_PASSWORD", "").strip() or None

# Шифрование payload (AES-256-GCM) поверх HMAC-подписи.
# Должно быть true ОДНОВРЕМЕННО во всех трёх компонентах.
ENCRYPT_PAYLOAD = os.getenv("ENCRYPT_PAYLOAD", "false").strip().lower() in ("1", "true", "yes")

# Общий секрет HMAC-SHA256. ДОЛЖЕН совпадать во всех трёх папках проекта:
# TG-BOT-SERVER, XGENT-MCS, XGENT-WDS.
# Fail-closed: без явного значения в .env клиент НЕ запустится.
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

# Интервал heartbeat-сообщений о статусе (секунды).
HEARTBEAT_INTERVAL = 60

# Каталог с локальными данными клиента (%USERPROFILE%\.xgent).
CONFIG_DIR = Path.home() / ".xgent"
CONFIG_FILE = CONFIG_DIR / "config.json"

# Версия клиента и строка платформы для статусов.
VERSION = "3.3.5"
PLATFORM = f"Windows {platform.win32_ver()[0]}"


def _load_or_create() -> dict:
    """Загрузить идентификатор устройства или создать новый при первом запуске."""
    try:
        data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        if data.get("device_id") and data.get("name"):
            return data
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    data = {
        "device_id": uuid.uuid4().hex[:12],
        "name": socket.gethostname(),
    }
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return data


# Глобальные значения, используемые остальными модулями клиента.
DEVICE = _load_or_create()
DEVICE_ID = DEVICE["device_id"]
DEVICE_NAME = DEVICE["name"]
