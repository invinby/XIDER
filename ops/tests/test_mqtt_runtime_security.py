"""Verify every runtime config fails closed for plaintext or anonymous MQTT."""

import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[2]
COMPONENTS = (
    ROOT / "TG-BOT-SERVER",
    ROOT / "XGENT-WDS",
    ROOT / "XGENT-MCS",
)


def _run_config(component: Path, *, tls="true", username="test-user", password="test-password-not-real"):
    env = os.environ.copy()
    env.update({
        "SHARED_KEY": "xider-test-only-shared-key-0123456789",
        "BOT_TOKEN": "123456:TEST-TOKEN-NOT-REAL",
        "ADMIN_ID": "0",
        "MQTT_BROKER": "localhost",
        "MQTT_PORT": "8883",
        "MQTT_PREFIX": "xgent/test",
        "MQTT_TLS": tls,
        "MQTT_USERNAME": username,
        "MQTT_PASSWORD": password,
        "ENCRYPT_PAYLOAD": "false",
    })
    return subprocess.run(
        [
            sys.executable,
            "-c",
            "import config; assert config.MQTT_TLS; assert config.MQTT_PORT == 8883",
        ],
        cwd=component,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=15,
    )


def test_all_components_reject_plaintext_mqtt():
    for component in COMPONENTS:
        result = _run_config(component, tls="false")
        assert result.returncode != 0, component.name
        assert "MQTT_TLS must be true" in result.stderr, component.name


def test_all_components_reject_missing_acl_credentials():
    for component in COMPONENTS:
        result = _run_config(component, username="", password="")
        assert result.returncode != 0, component.name
        assert "MQTT_USERNAME and MQTT_PASSWORD are required" in result.stderr, component.name


def test_runtime_defaults_are_tls_and_private_mqtt_port():
    for component in COMPONENTS:
        env = os.environ.copy()
        env.update({
            "SHARED_KEY": "xider-test-only-shared-key-0123456789",
            "BOT_TOKEN": "123456:TEST-TOKEN-NOT-REAL",
            "ADMIN_ID": "0",
            "MQTT_BROKER": "localhost",
            "MQTT_USERNAME": "xider-test-user",
            "MQTT_PASSWORD": "xider-test-password-not-real",
        })
        for name in ("MQTT_PORT", "MQTT_TLS"):
            env.pop(name, None)
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "import config; assert config.MQTT_TLS is True; assert config.MQTT_PORT == 8883",
            ],
            cwd=component,
            env=env,
            capture_output=True,
            text=True,
            check=False,
            timeout=15,
        )
        assert result.returncode == 0, f"{component.name}: {result.stderr}"
