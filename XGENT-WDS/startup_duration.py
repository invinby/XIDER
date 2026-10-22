"""Strict parser for measured Windows Diagnostics-Performance boot duration."""

from __future__ import annotations

import json
from datetime import datetime, timezone


SOURCE = "windows_diagnostics_performance_event_100"
MAX_INPUT_CHARS = 32 * 1024
MAX_BOOT_DURATION_MS = 30 * 60 * 1000


def parse_windows_startup_duration(text: str, *, now: datetime | None = None) -> dict:
    """Return only BootTime and the event timestamp; never forward event XML/message."""
    unknown = {"state": "unknown", "source": SOURCE, "duration_ms": None, "completed_at": None}
    if not isinstance(text, str) or not text or len(text) > MAX_INPUT_CHARS:
        return unknown
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        return unknown
    try:
        raw = json.loads(text)
    except (TypeError, json.JSONDecodeError):
        return unknown
    if not isinstance(raw, dict):
        return unknown
    duration, completed = raw.get("duration_ms"), raw.get("completed_at")
    if isinstance(duration, str) and duration.isdecimal():
        duration = int(duration)
    if isinstance(duration, bool) or not isinstance(duration, int) or not 0 < duration <= MAX_BOOT_DURATION_MS:
        return unknown
    if not isinstance(completed, str) or len(completed) > 64:
        return unknown
    try:
        stamp = datetime.fromisoformat(completed.replace("Z", "+00:00"))
    except ValueError:
        return unknown
    if stamp.tzinfo is None or stamp > current.astimezone(timezone.utc):
        return unknown
    return {
        "state": "supported",
        "source": SOURCE,
        "duration_ms": duration,
        "completed_at": stamp.astimezone(timezone.utc).isoformat(timespec="seconds"),
    }
