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
os.environ.setdefault("MQTT_PORT", "8883")
os.environ.setdefault("MQTT_TLS", "true")
os.environ.setdefault("MQTT_USERNAME", "xider-test-user")
os.environ.setdefault("MQTT_PASSWORD", "xider-test-password-not-real")
os.environ.setdefault("MQTT_PREFIX", "xgent/v1")

# Добавляем папку TG-BOT-SERVER в sys.path, чтобы `import crypto` работал.
BOT_DIR = Path(__file__).resolve().parents[1]
if str(BOT_DIR) not in sys.path:
    sys.path.insert(0, str(BOT_DIR))


import pytest


@pytest.fixture
def signed_catalog_release(monkeypatch):
    """Sign inventory with an ephemeral test publisher, never a production key."""
    import dataclasses
    import release_catalog
    import release_signature
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    key = Ed25519PrivateKey.generate()
    public = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    monkeypatch.setattr(release_signature, "TRUSTED_RELEASE_KEYS", {release_signature.release_key_id(public): public})
    def sign(release):
        assets = []
        for name in sorted(release.asset_names - {"release-manifest.json"}):
            component = next((component for component, names in release_catalog._ASSET_NAMES.items() if name in names), "source")
            assets.append({"name": name, "component": component, "platform": release_catalog._PLATFORMS.get(component, "all"), "size": 3, "sha256": "a" * 64})
        manifest = release_signature.sign_manifest({"schema": 1, "release": release.tag, "assets": assets}, key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
        return dataclasses.replace(release, manifest=manifest)
    return sign
