"""Bounded, redacted parser for macOS pmset sleep/wake history."""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone


SOURCE = "macos_pmset_log"
HISTORY_DAYS = 7
MAX_INPUT_CHARS = 1024 * 1024
MAX_LINES = 10_000
MAX_EVENTS = 30
_LINE = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} [+-]\d{4})\s+(.*)$")


def parse_pmset_sleep_wake_log(text: str, *, now: datetime | None = None) -> dict:
    """Normalize only Sleep/Wake event type and timestamp; drop all log details."""
    unknown = {"state": "unknown", "source": SOURCE, "history_days": HISTORY_DAYS, "events": []}
    if not isinstance(text, str) or not text or len(text) > MAX_INPUT_CHARS:
        return unknown
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        return unknown
    lines = text.splitlines()
    if len(lines) > MAX_LINES:
        return unknown
    cutoff = current.astimezone(timezone.utc) - timedelta(days=HISTORY_DAYS)
    events = []
    for line in lines:
        match = _LINE.match(line)
        if not match:
            continue
        try:
            stamp = datetime.strptime(match.group(1), "%Y-%m-%d %H:%M:%S %z")
        except ValueError:
            continue
        summary = match.group(2).casefold()
        if "entering sleep state" in summary:
            kind = "sleep"
        elif re.search(r"\bwake from\b", summary):
            kind = "wake"
        else:
            continue
        stamp = stamp.astimezone(timezone.utc)
        if cutoff <= stamp <= current.astimezone(timezone.utc):
            events.append({"kind": kind, "timestamp": stamp.isoformat(timespec="seconds")})
    events.sort(key=lambda event: event["timestamp"])
    events = events[-MAX_EVENTS:]
    if not events:
        return unknown
    return {"state": "supported", "source": SOURCE, "history_days": HISTORY_DAYS, "events": events}
