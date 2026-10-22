from __future__ import annotations

import json
from datetime import datetime, timezone

from startup_duration import parse_windows_startup_duration


def test_windows_startup_duration_keeps_only_valid_boot_duration_and_timestamp():
    now = datetime(2026, 10, 16, 12, 0, tzinfo=timezone.utc)
    result = parse_windows_startup_duration(
        json.dumps({
            "duration_ms": 42_500,
            "completed_at": "2026-10-16T10:30:00+00:00",
            "private_event_data": "must not escape",
        }),
        now=now,
    )

    assert result == {
        "state": "supported",
        "source": "windows_diagnostics_performance_event_100",
        "duration_ms": 42_500,
        "completed_at": "2026-10-16T10:30:00+00:00",
    }
    assert "private_event_data" not in repr(result)
    assert "must not escape" not in repr(result)


def test_windows_startup_duration_fails_closed_for_missing_invalid_or_future_values():
    now = datetime(2026, 10, 16, 12, 0, tzinfo=timezone.utc)
    expected = {
        "state": "unknown",
        "source": "windows_diagnostics_performance_event_100",
        "duration_ms": None,
        "completed_at": None,
    }
    for payload in (
        "not json", "{}", json.dumps({"duration_ms": 0, "completed_at": "2026-10-16T10:00:00Z"}),
        json.dumps({"duration_ms": 43_000, "completed_at": "2026-10-17T10:00:00Z"}),
        json.dumps({"duration_ms": 43_000, "completed_at": "2026-10-16T10:00:00"}),
        json.dumps({"duration_ms": 31 * 60 * 1000, "completed_at": "2026-10-16T10:00:00Z"}),
    ):
        assert parse_windows_startup_duration(payload, now=now) == expected
