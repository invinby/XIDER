"""Pytest configuration for TG-BOT-SERVER crypto tests.

Задаёт SHARED_KEY до импорта config/crypto (config.py fail-closed при импорте).
"""

import os
import sys
from pathlib import Path

os.environ.setdefault("SHARED_KEY", "test-secret-not-real-0123456789")
os.environ.setdefault("BOT_TOKEN", "123456:TEST-TOKEN-NOT-REAL")
os.environ.setdefault("ADMIN_ID", "0")
os.environ.setdefault("MQTT_BROKER", "localhost")
os.environ.setdefault("MQTT_PORT", "1883")
os.environ.setdefault("MQTT_PREFIX", "xgent/v1")

# Добавляем папку TG-BOT-SERVER в sys.path, чтобы `import crypto` работал.
BOT_DIR = Path(__file__).resolve().parents[1]
if str(BOT_DIR) not in sys.path:
    sys.path.insert(0, str(BOT_DIR))