"""Конфигурация клиента XGENT для macOS.

Секреты загружаются из файла .env (рядом со скриптом).
SHARED_KEY ОБЯЗАН быть задан в .env (fail-closed).
"""

from contextlib import contextmanager
import json
import os
import platform
import socket
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path

from dotenv import load_dotenv

# Загружаем .env из каталога скрипта (с поддержкой UTF-8 BOM).
_ENV_FILE = Path(__file__).resolve().parent / ".env"
try:
    load_dotenv(_ENV_FILE, encoding="utf-8-sig")
except TypeError:
    load_dotenv(_ENV_FILE)

# Параметры MQTT-брокера. Используется EMQX Cloud Serverless (TLS), без проброса портов.
MQTT_BROKER = os.getenv("MQTT_BROKER", "").strip()
if not MQTT_BROKER:
    raise SystemExit("FATAL: MQTT_BROKER must be set in .env")
MQTT_PORT = int(os.getenv("MQTT_PORT", "8883"))
MQTT_PREFIX = os.getenv("MQTT_PREFIX", "xgent/v1")
MQTT_TLS = os.getenv("MQTT_TLS", "true").strip().lower() in ("1", "true", "yes")
MQTT_USERNAME = os.getenv("MQTT_USERNAME", "").strip() or None
MQTT_PASSWORD = os.getenv("MQTT_PASSWORD", "").strip() or None
if not MQTT_TLS:
    raise SystemExit("FATAL: MQTT_TLS must be true; plaintext MQTT is disabled.")
if not MQTT_USERNAME or not MQTT_PASSWORD:
    raise SystemExit("FATAL: private MQTT_USERNAME and MQTT_PASSWORD are required for broker ACLs.")
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

# Каталог с локальными данными клиента (~/.xgent): идентификатор и логи.
CONFIG_DIR = Path.home() / ".xgent"
CONFIG_FILE = CONFIG_DIR / "config.json"


_GUARDIAN_STATE_THREAD_LOCK = threading.RLock()


@contextmanager
def _guardian_state_lock():
    """Serialize Guardian-state read/modify/write across threads and processes."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with _GUARDIAN_STATE_THREAD_LOCK:
        with (CONFIG_DIR / "guardian.lock").open("a+b") as lock_file:
            lock_file.seek(0, os.SEEK_END)
            if lock_file.tell() == 0:
                lock_file.write(b"\0")
                lock_file.flush()
            deadline = time.monotonic() + 10
            if os.name == "nt":
                import msvcrt

                while True:
                    lock_file.seek(0)
                    try:
                        msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
                        break
                    except OSError:
                        if time.monotonic() >= deadline:
                            raise TimeoutError("Timed out waiting for the Guardian state lock.")
                        time.sleep(0.05)
                try:
                    yield
                finally:
                    lock_file.seek(0)
                    msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                while True:
                    try:
                        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                        break
                    except BlockingIOError:
                        if time.monotonic() >= deadline:
                            raise TimeoutError("Timed out waiting for the Guardian state lock.")
                        time.sleep(0.05)
                try:
                    yield
                finally:
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def update_guardian_state(*, initial_state: dict | None = None, **updates: object) -> dict:
    """Merge selected fields into guardian.json without losing concurrent changes."""
    state_path = CONFIG_DIR / "guardian.json"
    with _guardian_state_lock():
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
            if not isinstance(state, dict):
                state = {}
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            state = dict(initial_state) if isinstance(initial_state, dict) else {
                "state_version": 2, "auto_restart": True,
                "desired_running": True, "startup_enabled": True,
            }
        state.update(updates)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix="guardian-state.", suffix=".tmp", dir=CONFIG_DIR,
        )
        try:
            if hasattr(os, "fchmod"):
                os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump(state, stream, ensure_ascii=False, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary_name, state_path)
        finally:
            try:
                os.unlink(temporary_name)
            except FileNotFoundError:
                pass
        return state


def set_guardian_desired_running(enabled: bool) -> None:
    """Persist the owner's current-session worker start/stop intent."""
    update_guardian_state(desired_running=bool(enabled))


def set_guardian_startup_enabled(enabled: bool) -> None:
    """Persist autostart independently from the current worker process state."""
    updates: dict[str, object] = {"startup_enabled": bool(enabled)}
    if enabled:
        updates["desired_running"] = True
    update_guardian_state(**updates)

# Версия клиента и строка платформы для статусов.
VERSION = "4.1.4"
PLATFORM = f"macOS {platform.mac_ver()[0]}"


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
