"""Bounded, redacted parsing for Windows sleep/resume event history."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone


SOURCE = "windows_system_power_events"
HISTORY_DAYS = 7
MAX_INPUT_CHARS = 1024 * 1024
MAX_EVENTS = 30


def _timestamp(value: object) -> datetime | None:
    if not isinstance(value, str) or len(value) > 64:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def parse_windows_sleep_wake_events(text: str, *, now: datetime | None = None) -> dict:
    """Keep only event kind and UTC time; discard message and other event data."""
    unknown = {"state": "unknown", "source": SOURCE, "history_days": HISTORY_DAYS, "events": []}
    if not isinstance(text, str) or not text or len(text) > MAX_INPUT_CHARS:
        return unknown
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        return unknown
    try:
        raw = json.loads(text)
    except (TypeError, json.JSONDecodeError):
        return unknown
    rows = raw if isinstance(raw, list) else [raw] if isinstance(raw, dict) else []
    if len(rows) > 500:
        return unknown
    cutoff = current.astimezone(timezone.utc) - timedelta(days=HISTORY_DAYS)
    events = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        event_id, provider = row.get("Id"), row.get("ProviderName")
        if provider == "Microsoft-Windows-Kernel-Power" and event_id == 42:
            kind = "sleep"
        elif provider == "Microsoft-Windows-Kernel-Power" and event_id == 107:
            kind = "wake"
        elif provider == "Microsoft-Windows-Power-Troubleshooter" and event_id == 1:
            kind = "wake"
        else:
            continue
        stamp = _timestamp(row.get("TimeCreated"))
        if stamp is None:
            continue
        stamp = stamp.astimezone(timezone.utc)
        if cutoff <= stamp <= current.astimezone(timezone.utc):
            events.append({"kind": kind, "timestamp": stamp.isoformat(timespec="seconds")})
    events.sort(key=lambda event: event["timestamp"])
    events = events[-MAX_EVENTS:]
    if not events:
        return unknown
    return {"state": "supported", "source": SOURCE, "history_days": HISTORY_DAYS, "events": events}
