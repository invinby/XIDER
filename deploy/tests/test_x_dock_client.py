import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "deploy"))
sys.path.insert(0, str(ROOT / "TG-BOT-SERVER"))

import x_dock_client as client
from enrollment_store import EnrollmentStore, seal_response


def test_enrollment_client_uses_owner_approval_and_writes_only_sealed_config(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(client.Path, "home", lambda: tmp_path)
    store = EnrollmentStore(tmp_path / "enrollment.sqlite3", clock=lambda: 1000)
    settings = {
        "SHARED_KEY": "unique-message-key-not-printed",
        "MQTT_BROKER": "broker.example.org",
        "MQTT_PORT": "8883",
        "MQTT_PREFIX": "xgent/v1",
        "MQTT_TLS": "true",
        "MQTT_USERNAME": "device-user",
        "MQTT_PASSWORD": "unique-broker-password-not-printed",
        "ENCRYPT_PAYLOAD": "true",
    }

    def post(url, host, payload, *, timeout=15):
        if url.endswith("/request"):
            store.submit(payload, "a" * 64)
            store.decide(payload["request_id"], 1, 1, approve=True)
            approved = store.claim_approved(payload["request_id"])
            sealed = seal_response(
                payload["request_id"], approved["fingerprint"], approved["encryption_key"], settings,
            )
            assert store.set_sealed_reply(payload["request_id"], sealed)
            return {"state": "pending", "request_id": payload["request_id"]}
        return store.poll(payload["request_id"], payload["nonce"], payload["signature"])

    monkeypatch.setattr(client, "_post_json", post)
    output = tmp_path / "staging" / ".env"
    client.enroll("https://pair.example.org", output, timeout_seconds=5, poll_seconds=0)

    assert "unique-message-key-not-printed" in output.read_text(encoding="utf-8")
    assert "unique-broker-password-not-printed" in output.read_text(encoding="utf-8")
    printed = capsys.readouterr().out
    assert "unique-message-key-not-printed" not in printed
    assert "unique-broker-password-not-printed" not in printed
    assert json.loads((tmp_path / ".xgent" / "config.json").read_text())['device_id']
    if os.name == "posix":
        assert output.stat().st_mode & 0o077 == 0


@pytest.mark.parametrize("url", [
    "http://pair.example.org",
    "https://user:pass@pair.example.org",
    "https://pair.example.org/path?token=secret",
])
def test_enrollment_client_rejects_untrusted_or_ambiguous_endpoint(url):
    with pytest.raises(ValueError, match="HTTPS"):
        client._https_endpoint(url)
