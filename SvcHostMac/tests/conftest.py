"""Safe fake MQTT config for isolated macOS-agent tests."""

import os

os.environ.setdefault("SHARED_KEY", "svchost-test-only-shared-key-0123456789")
os.environ.setdefault("MQTT_BROKER", "localhost")
os.environ.setdefault("MQTT_PORT", "8883")
os.environ.setdefault("MQTT_PREFIX", "xgent/test")
os.environ.setdefault("MQTT_TLS", "true")
os.environ.setdefault("MQTT_USERNAME", "svchost-test-user")
os.environ.setdefault("MQTT_PASSWORD", "svchost-test-password-not-real")
os.environ.setdefault("ENCRYPT_PAYLOAD", "false")
