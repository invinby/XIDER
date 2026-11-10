from __future__ import annotations

import json
from datetime import datetime, timezone

from sleep_wake_history import parse_windows_sleep_wake_events


def test_windows_sleep_wake_parser_keeps_only_allowlisted_recent_events():
    now = datetime(2026, 10, 16, 12, 0, tzinfo=timezone.utc)
    payload = [
        {"Id": 42, "ProviderName": "Microsoft-Windows-Kernel-Power", "TimeCreated": "2026-10-16T10:00:00+00:00", "Message": "private text"},
        {"Id": 107, "ProviderName": "Microsoft-Windows-Kernel-Power", "TimeCreated": "2026-10-16T11:00:00+00:00", "Message": "private text"},
        {"Id": 1, "ProviderName": "Microsoft-Windows-Power-Troubleshooter", "TimeCreated": "2026-10-16T11:01:00Z", "Message": "private text"},
        {"Id": 1, "ProviderName": "Other-Provider", "TimeCreated": "2026-10-16T11:02:00Z"},
        {"Id": 42, "ProviderName": "Microsoft-Windows-Kernel-Power", "TimeCreated": "2026-10-01T10:00:00Z"},
    ]

    result = parse_windows_sleep_wake_events(json.dumps(payload), now=now)

    assert result == {
        "state": "supported",
        "source": "windows_system_power_events",
        "history_days": 7,
        "events": [
            {"kind": "sleep", "timestamp": "2026-10-16T10:00:00+00:00"},
            {"kind": "wake", "timestamp": "2026-10-16T11:00:00+00:00"},
            {"kind": "wake", "timestamp": "2026-10-16T11:01:00+00:00"},
        ],
    }
    assert "private text" not in repr(result)


def test_windows_sleep_wake_parser_returns_unknown_for_empty_malformed_or_future_data():
    expected = {
        "state": "unknown", "source": "windows_system_power_events",
        "history_days": 7, "events": [],
    }
    now = datetime(2026, 10, 16, 12, 0, tzinfo=timezone.utc)
    assert parse_windows_sleep_wake_events("not json", now=now) == expected
    assert parse_windows_sleep_wake_events("[]", now=now) == expected
    assert parse_windows_sleep_wake_events(json.dumps([
        {"Id": 42, "ProviderName": "Microsoft-Windows-Kernel-Power", "TimeCreated": "2026-10-17T12:00:00Z"},
    ]), now=now) == expected


def test_windows_sleep_wake_parser_bounds_event_count_and_rejects_naive_timestamps():
    now = datetime(2026, 10, 16, 12, 0, tzinfo=timezone.utc)
    rows = [
        {"Id": 107, "ProviderName": "Microsoft-Windows-Kernel-Power", "TimeCreated": f"2026-10-16T11:{minute:02d}:00+00:00"}
        for minute in range(35)
    ]
    rows.append({"Id": 42, "ProviderName": "Microsoft-Windows-Kernel-Power", "TimeCreated": "2026-10-16T10:00:00"})

    result = parse_windows_sleep_wake_events(json.dumps(rows), now=now)

    assert len(result["events"]) == 30
    assert all(event["kind"] == "wake" for event in result["events"])
    assert all("+00:00" in event["timestamp"] for event in result["events"])
