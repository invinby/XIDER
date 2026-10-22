from __future__ import annotations

from datetime import datetime, timezone

from sleep_wake_history import parse_pmset_sleep_wake_log


def test_pmset_parser_returns_recent_normalized_sleep_and_wake_without_raw_log_text():
    now = datetime(2026, 10, 16, 12, 0, tzinfo=timezone.utc)
    result = parse_pmset_sleep_wake_log(
        "2026-10-16 10:00:00 +0000 Sleep Entering Sleep state due to 'Clamshell Sleep'\n"
        "2026-10-16 11:00:00 +0000 Wake Wake from Normal Sleep [CDNVA]\n"
        "2026-10-16 11:01:00 +0000 DarkWake DarkWake from Standby [CDN]\n"
        "2026-10-01 10:00:00 +0000 Sleep Entering Sleep state\n"
        "2026-10-16 11:02:00 +0000 Notification private-user-detail\n",
        now=now,
    )

    assert result == {
        "state": "supported",
        "source": "macos_pmset_log",
        "history_days": 7,
        "events": [
            {"kind": "sleep", "timestamp": "2026-10-16T10:00:00+00:00"},
            {"kind": "wake", "timestamp": "2026-10-16T11:00:00+00:00"},
        ],
    }
    assert "Clamshell" not in repr(result)
    assert "private-user-detail" not in repr(result)


def test_pmset_parser_fails_closed_for_unknown_locale_or_empty_log():
    expected = {"state": "unknown", "source": "macos_pmset_log", "history_days": 7, "events": []}
    now = datetime(2026, 10, 16, 12, 0, tzinfo=timezone.utc)
    assert parse_pmset_sleep_wake_log("", now=now) == expected
    assert parse_pmset_sleep_wake_log("Fri 16 Oct 2026 Sleep Entering Sleep state", now=now) == expected


def test_pmset_parser_bounds_input_and_event_count():
    now = datetime(2026, 10, 16, 12, 0, tzinfo=timezone.utc)
    lines = [f"2026-10-16 11:{minute:02d}:00 +0000 Wake Wake from Normal Sleep" for minute in range(35)]
    parsed = parse_pmset_sleep_wake_log("\n".join(lines), now=now)
    oversized = parse_pmset_sleep_wake_log("x" * (1024 * 1024 + 1), now=now)

    assert parsed["state"] == "supported"
    assert len(parsed["events"]) == 30
    assert oversized["state"] == "unknown"
