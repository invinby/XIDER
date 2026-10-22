import json
from datetime import datetime, timezone

import media_history
import xgent_wds as wds


def test_media_history_records_only_allowlisted_metadata_not_payload(tmp_path):
    path = tmp_path / "media-history.json"
    now = datetime(2026, 10, 19, 10, 0, tzinfo=timezone.utc)

    assert media_history.record_response(
        path,
        "mic",
        {"audio": "VERY-PRIVATE-AUDIO", "id": "request-123", "device_id": "device-1"},
        now=now,
    ) is True

    raw = path.read_text(encoding="utf-8")
    assert "VERY-PRIVATE-AUDIO" not in raw and "request-123" not in raw and "device-1" not in raw
    assert json.loads(raw) == [{
        "at": "2026-10-19T10:00:00+00:00", "action": "mic", "outcome": "captured",
    }]


def test_media_history_rejects_unlisted_action_and_normalizes_failure(tmp_path):
    path = tmp_path / "media-history.json"
    now = datetime(2026, 10, 19, 10, 0, tzinfo=timezone.utc)

    assert media_history.record_response(path, "shell", {"state": "supported"}, now=now) is False
    assert media_history.record_response(path, "webcam", {"state": "denied", "detail": "private"}, now=now) is True

    assert media_history.read_events(path, now=now) == [{
        "at": "2026-10-19T10:00:00+00:00", "action": "webcam", "outcome": "denied",
    }]


def test_media_history_ignores_malformed_states_and_rows(tmp_path):
    path = tmp_path / "history.json"
    assert media_history.record_response(path, "mic", {"state": ["not", "hashable"]}) is True
    assert media_history.read_events(path)[0]["outcome"] == "unknown"
    path.write_text('[{"action": [], "outcome": {}, "at": "bad"}]', encoding="utf-8")
    assert media_history.read_events(path) == []


def test_media_history_is_bounded_and_expires_old_events(tmp_path):
    path = tmp_path / "media-history.json"
    base = datetime(2026, 10, 1, tzinfo=timezone.utc)
    for day in range(35):
        media_history.record_response(
            path, "screenshot", {"image": "not-persisted"},
            now=base.replace(day=day + 1) if day < 31 else datetime(2026, 11, day - 30, tzinfo=timezone.utc),
            max_entries=4,
        )

    now = datetime(2026, 11, 5, tzinfo=timezone.utc)
    events = media_history.read_events(path, now=now, limit=10)
    assert len(events) == 4
    assert [item["at"] for item in events] == [
        "2026-11-04T00:00:00+00:00", "2026-11-03T00:00:00+00:00",
        "2026-11-02T00:00:00+00:00", "2026-11-01T00:00:00+00:00",
    ]


def test_agent_media_history_command_returns_only_correlated_metadata(monkeypatch, tmp_path):
    monkeypatch.setattr(wds, "CONFIG_DIR", tmp_path)
    media_history.record_response(tmp_path / "media-history.json", "mic", {"audio": "private"})
    published = []
    client = object.__new__(wds.XgentClient)
    client._publish_response = lambda topic, payload: published.append((topic, payload))
    monkeypatch.setattr(wds, "DEVICE_ID", "windows-1")

    client._do_media_history({"id": "history-ticket", "limit": 5})

    assert "media_history" in wds.SUPPORTED_COMMANDS
    assert published[0][0] == "media_history"
    result = published[0][1]
    assert result["feature_id"] == "D02-F24" and result["request_id"] == "history-ticket"
    assert result["data"]["events"][0]["action"] == "mic"
    assert "private" not in json.dumps(result)
