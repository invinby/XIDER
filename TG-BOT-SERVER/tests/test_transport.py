import json
from types import SimpleNamespace

from transport import MQTTTransport


def test_retained_status_is_ignored_but_live_status_is_forwarded():
    received = []
    transport = object.__new__(MQTTTransport)
    transport.on_message = lambda topic, payload: received.append((topic, payload))
    topic = "xgent/v1/test-device/status"
    payload = {"sig": "fixture", "payload": {"type": "status", "status": "online"}}

    retained = SimpleNamespace(topic=topic, payload=json.dumps(payload).encode(), retain=True)
    transport._on_message(None, None, retained)
    assert received == []

    live = SimpleNamespace(topic=topic, payload=json.dumps(payload).encode(), retain=False)
    transport._on_message(None, None, live)
    assert received == [(topic, payload)]
