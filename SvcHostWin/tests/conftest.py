"""Pytest configuration for SvcHostWin.

Задаёт SHARED_KEY до импорта модуля (config.py читает его в момент import),
чтобы импорт не падал и не срабатывало предупреждение о слабом секрете.
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("SHARED_KEY", "test-secret-not-real-0123456789")
os.environ.setdefault("MQTT_BROKER", "localhost")
os.environ.setdefault("MQTT_PORT", "8883")
os.environ.setdefault("MQTT_TLS", "true")
os.environ.setdefault("MQTT_USERNAME", "svchost-test-user")
os.environ.setdefault("MQTT_PASSWORD", "svchost-test-password-not-real")
os.environ.setdefault("MQTT_PREFIX", "xgent/v1")
# Тесты парсят payload в открытом виде — шифрование принудительно выключаем,
# независимо от настроек локального .env (ENCRYPT_PAYLOAD=true).
os.environ["ENCRYPT_PAYLOAD"] = "false"

import svchost_win  # noqa: E402  (импортируем после установки env)
