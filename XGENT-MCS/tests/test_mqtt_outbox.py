"""MQTT response retry tests; no broker or device hardware is used."""

import json
import threading
from pathlib import Path
from types import SimpleNamespace

import xgent_mcs as mcs
import xider_guardian as guardian_module
from mqtt_outbox import MqttResponseOutbox
from update_package import AGENT_UPDATE_FILES
from guardian_recovery import RECOVERY_FILES


def test_outbox_copies_payload_and_removes_only_after_acceptance():
    outbox = MqttResponseOutbox(max_entries=2, max_total_bytes=128, max_entry_bytes=128)
    payload = {"id": "request-1", "value": "original"}

    accepted, reason, expired = outbox.enqueue("output", payload)

    assert (accepted, reason, expired) == (True, None, 0)
    payload["value"] = "mutated"
    entry, expired = outbox.peek()
    assert expired == 0
    assert entry.payload["value"] == "original"
    assert len(outbox) == 1
    assert outbox.remove(entry.token)
    assert len(outbox) == 0


def test_outbox_bounds_payload_and_expires_old_entries():
    now = [100.0]
    outbox = MqttResponseOutbox(
        max_entries=1, max_total_bytes=64, max_entry_bytes=64,
        ttl_seconds=10, clock=lambda: now[0],
    )
    assert outbox.enqueue("output", {"id": "old"})[0]
    assert outbox.enqueue("output", {"blob": "x" * 80})[:2] == (False, "entry_too_large")
    now[0] += 10
    assert outbox.enqueue("output", {"id": "new"}) == (True, None, 1)
    entry, expired = outbox.peek()
    assert expired == 0
    assert entry.payload["id"] == "new"


def test_agent_queues_disconnect_reply_and_signs_only_when_flushed(monkeypatch):
    sent = []
    signed = []
    client = object.__new__(mcs.XgentClient)
    client._command_context = threading.local()
    client._mqtt_connected = False
    client._pending_responses = MqttResponseOutbox()
    client._pending_response_flush_lock = threading.Lock()
    client._pending_response_flush_wake = threading.Event()
    client._running = threading.Event()
    client._running.set()
    client._client = SimpleNamespace(publish=lambda *args, **kwargs: sent.append((args, kwargs)) or SimpleNamespace(
        rc=0, wait_for_publish=lambda timeout: None, is_published=lambda: True,
    ))
    monkeypatch.setattr(mcs, "DEVICE_ID", "mac-test")
    monkeypatch.setattr(mcs, "MQTT_PREFIX", "xgent/v1")
    monkeypatch.setattr(mcs, "ENCRYPT_PAYLOAD", False)
    monkeypatch.setattr(mcs, "sign_message", lambda body: signed.append(dict(body)) or {"payload": body})

    assert client._publish_response("output", {"type": "sysinfo", "id": "request-1"})
    assert sent == []
    assert signed == []
    assert len(client._pending_responses) == 1

    client._mqtt_connected = True
    client._flush_pending_responses()

    assert len(sent) == 1
    assert sent[0][0][0] == "xgent/v1/mac-test/output"
    assert sent[0][1]["qos"] == 1
    assert signed[0]["id"] == "request-1"
    assert len(client._pending_responses) == 0


def test_guardian_queues_only_command_replies_and_heartbeat_refreshes(monkeypatch):
    sent = []
    instance = object.__new__(guardian_module.Guardian)
    instance._mqtt_connected = False
    instance._pending_responses = MqttResponseOutbox()
    instance._pending_response_flush_lock = threading.Lock()
    instance._pending_response_flush_wake = threading.Event()
    instance.stop_event = threading.Event()
    instance.client = SimpleNamespace(publish=lambda *args, **kwargs: sent.append((args, kwargs)) or SimpleNamespace(
        rc=0, wait_for_publish=lambda timeout: None, is_published=lambda: True,
    ))
    instance._envelope = lambda body: json.dumps(body)
    instance.status_payload = lambda: {"type": "guardian", "ok": True}
    monkeypatch.setattr(guardian_module, "DEVICE_ID", "mac-test")
    monkeypatch.setattr(guardian_module, "MQTT_PREFIX", "xgent/v1")

    assert instance._publish({"ok": True, "id": "request-2"}, queue_if_disconnected=True)
    assert not instance._publish(instance.status_payload())
    assert len(instance._pending_responses) == 1

    instance._mqtt_connected = True
    instance._flush_pending_responses()
    assert len(sent) == 1
    assert sent[0][1]["qos"] == 1
    assert len(instance._pending_responses) == 0

    instance._last_status_publish = 100.0
    instance.status_payload = lambda: {"type": "guardian", "ok": True}
    published = []
    instance._publish_status = lambda: published.append(instance.status_payload()) or True
    assert not instance._publish_status_if_due(now=159.0)
    assert instance._publish_status_if_due(now=160.0)
    assert not instance._publish_status_if_due(now=161.0)
    assert published == [{"type": "guardian", "ok": True}]


def test_outbox_module_is_shipped_by_agent_updates_and_guardian_recovery():
    assert "mqtt_outbox.py" in AGENT_UPDATE_FILES
    assert "mqtt_outbox.py" in RECOVERY_FILES


def test_agent_status_uses_ephemeral_qos_zero(monkeypatch):
    client = object.__new__(mcs.XgentClient)
    client._mqtt_connected = True
    client._standby = False
    client._mqtt_session_id = "a" * 32
    published = []
    client._client = SimpleNamespace(publish=lambda *args, **kwargs: published.append((args, kwargs)) or SimpleNamespace(rc=0))
    monkeypatch.setattr(mcs, "DEVICE_ID", "mac-test")
    monkeypatch.setattr(mcs, "MQTT_PREFIX", "xgent/v1")
    monkeypatch.setattr(mcs, "DEVICE_NAME", "Mac test")
    monkeypatch.setattr(mcs, "PLATFORM", "macOS")
    monkeypatch.setattr(mcs, "VERSION", "4.3.8")
    monkeypatch.setattr(mcs, "_BUNDLED_RUNTIME", False)
    monkeypatch.setattr(mcs, "trusted_release_keys_ready", lambda: True)
    monkeypatch.setattr(mcs, "_command_approval_protocol_enabled", lambda _client: False)
    monkeypatch.setattr(mcs, "read_agent_update_result", lambda _path: None)
    monkeypatch.setattr(mcs, "CONFIG_DIR", Path("."))
    monkeypatch.setattr(mcs, "_layer_health_snapshot", lambda: {})
    monkeypatch.setattr(mcs, "ENCRYPT_PAYLOAD", False)

    client._publish_status()

    assert published[0][0][0] == "xgent/v1/mac-test/status"
    assert published[0][1]["qos"] == 0


def test_guardian_status_uses_ephemeral_qos_zero(monkeypatch):
    published = []
    instance = object.__new__(guardian_module.Guardian)
    instance._mqtt_connected = True
    instance.client = SimpleNamespace(
        publish=lambda *args, **kwargs: published.append((args, kwargs)) or SimpleNamespace(rc=0),
    )
    instance._envelope = lambda body: json.dumps(body)
    instance.status_payload = lambda: {"type": "guardian", "ok": True}
    monkeypatch.setattr(guardian_module, "DEVICE_ID", "mac-test")
    monkeypatch.setattr(guardian_module, "MQTT_PREFIX", "xgent/v1")

    assert instance._publish_status()
    assert published[0][0][0] == "xgent/v1/mac-test/guardian"
    assert published[0][1]["qos"] == 0
