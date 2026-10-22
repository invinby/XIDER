from datetime import datetime, timezone, timedelta

from media_request_control import MediaRequestRegistry


def test_owner_cancel_sets_only_a_registered_cooperative_media_request():
    requests = MediaRequestRegistry()
    cancel_event = requests.begin("capture-1", "camera_series", cancellable=True)

    assert cancel_event is not None and not cancel_event.is_set()
    assert requests.cancel("capture-1") == "cancelled"
    assert cancel_event.is_set()
    assert requests.finish("capture-1") == "cancelled"


def test_completed_uncancellable_and_unknown_media_requests_are_reported_truthfully():
    requests = MediaRequestRegistry()
    assert requests.begin("capture-2", "webcam", cancellable=False) is not None
    assert requests.cancel("capture-2") == "unavailable"
    assert requests.finish("capture-2") == "completed"
    assert requests.cancel("capture-2") == "already_completed"
    assert requests.cancel("missing") == "unavailable"


def test_registry_rejects_duplicate_ids_and_expires_old_terminal_receipts():
    requests = MediaRequestRegistry()
    start = datetime(2026, 10, 19, tzinfo=timezone.utc)
    assert requests.begin("same", "camera_series", cancellable=True, now=start) is not None
    assert requests.begin("same", "webcam", cancellable=True, now=start) is None
    assert requests.finish("same", now=start) == "completed"
    assert requests.cancel("same", now=start + timedelta(seconds=121)) == "unavailable"


def test_agent_media_cancel_returns_correlated_truthful_receipt(monkeypatch):
    import xgent_wds as wds

    requests = MediaRequestRegistry()
    event = requests.begin("media-123", "camera_series", cancellable=True)
    monkeypatch.setattr(wds, "MEDIA_REQUESTS", requests)
    client = object.__new__(wds.XgentClient)
    responses = []
    monkeypatch.setattr(client, "_publish_response", lambda topic, payload: responses.append((topic, payload)))

    client._do_media_cancel({"id": "cancel-456", "request_id": "media-123"})

    assert event is not None and event.is_set()
    topic, payload = responses[0]
    assert topic == "media_cancel"
    assert payload["type"] == "media_cancel_result"
    assert payload["schema"] == "xider.media-cancel.v1"
    assert payload["feature_id"] == "D02-F22"
    assert payload["request_id"] == "media-123"
    assert payload["state"] == "cancelled"
