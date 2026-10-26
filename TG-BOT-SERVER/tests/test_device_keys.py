import json
from types import SimpleNamespace

import pytest

import bot
import crypto
import device_keys
import transport
import xgencrypto


def test_device_key_is_loaded_outside_source_tree_and_legacy_still_works(tmp_path, monkeypatch):
    monkeypatch.setenv("XIDER_DEVICE_KEYS_DIR", str(tmp_path))
    assert device_keys.key_for_device("legacy1") == crypto.SHARED_KEY
    (tmp_path / "macbook1.key").write_text("m" * 64, encoding="ascii")
    assert device_keys.key_for_device("macbook1") == "m" * 64
    with pytest.raises(ValueError):
        device_keys.key_for_device("../other")
    (tmp_path / "broken1.key").write_text("bad", encoding="ascii")
    with pytest.raises(ValueError):
        device_keys.key_for_device("broken1")


def test_per_device_hmac_and_encryption_reject_legacy_key():
    key = "d" * 64
    encrypted = xgencrypto.encrypt_payload({"type": "status", "device_id": "macbook1"}, key=key)
    envelope = crypto.sign_message(encrypted, key=key)
    signed, reason = crypto.verify_message_detailed(envelope, key=key)
    assert reason == "ok"
    assert xgencrypto.decrypt_payload(signed, key=key)["device_id"] == "macbook1"
    assert crypto.verify_message_detailed(envelope)[1] == "bad_hmac"
    assert xgencrypto.decrypt_payload(signed) is None


def test_bot_verifies_key_selected_by_topic_before_routing(tmp_path, monkeypatch):
    monkeypatch.setenv("XIDER_DEVICE_KEYS_DIR", str(tmp_path))
    (tmp_path / "macbook1.key").write_text("d" * 64, encoding="ascii")
    received = []
    monkeypatch.setattr(bot, "verify_message_detailed", lambda _data, *, key: received.append(key) or (None, "bad_hmac"))
    bot.on_mqtt_message(f"{bot.MQTT_PREFIX}/macbook1/status", {})
    assert received == ["d" * 64]
    bot.on_mqtt_message(f"{bot.MQTT_PREFIX}/../status", {})
    assert received == ["d" * 64]


def test_transport_signs_device_command_with_device_key(tmp_path, monkeypatch):
    monkeypatch.setenv("XIDER_DEVICE_KEYS_DIR", str(tmp_path))
    (tmp_path / "macbook1.key").write_text("d" * 64, encoding="ascii")
    sent = []
    conn = transport.MQTTTransport(lambda *_args: None)
    conn.connected.set()
    conn._client = SimpleNamespace(publish=lambda topic, body, qos: sent.append((topic, body, qos)) or SimpleNamespace(rc=0))
    assert conn.publish_command("macbook1", "sysinfo") is True
    topic, body, qos = sent[0]
    assert topic == f"{bot.MQTT_PREFIX}/macbook1/cmd" and qos == 1
    assert crypto.verify_message_detailed(json.loads(body), key="d" * 64)[1] == "ok"
