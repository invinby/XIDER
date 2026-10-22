"""Read recent airportd unified-log events without returning network identity."""
from __future__ import annotations

import datetime
import json
import subprocess

MACOS_SOURCE = "macos_unified_airportd_events"


def _parse_timestamp(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.datetime.fromisoformat(value.replace("Z", "+00:00").replace(" +", "+").replace(" -", "-"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    result = parsed.isoformat(timespec="seconds")
    return result[:-6] + "Z" if result.endswith("+00:00") else result


def parse_macos_events(text: str) -> dict:
    if not isinstance(text, str):
        return {"state": "unknown", "source": MACOS_SOURCE, "events": [], "detail": "invalid_event_log"}
    try:
        rows = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return {"state": "unknown", "source": MACOS_SOURCE, "events": [], "detail": "invalid_event_log"}
    if not isinstance(rows, list):
        return {"state": "unknown", "source": MACOS_SOURCE, "events": [], "detail": "invalid_event_log"}
    events = []
    for row in rows:
        if not isinstance(row, dict) or row.get("process") != "airportd":
            continue
        message = row.get("eventMessage")
        if not isinstance(message, str):
            continue
        folded = message.casefold()
        if any(token in folded for token in ("joined network", "associated with network", "connected to network")):
            kind = "connected"
        elif any(token in folded for token in ("disassociated from network", "disconnected from network", "left network")):
            kind = "disconnected"
        else:
            continue
        at = _parse_timestamp(row.get("timestamp"))
        if at is not None:
            events.append({"at": at, "kind": kind})
    events.sort(key=lambda row: row["at"])
    events = events[-20:]
    return {"state": "available" if events else "unsupported", "source": MACOS_SOURCE,
            "events": events, "detail": None if events else "no_connection_events"}


def collect_wifi_connection_history(check_output=subprocess.check_output) -> dict:
    try:
        output = check_output(
            ["log", "show", "--last", "24h", "--style", "json", "--max-events", "500",
             "--predicate", "process == 'airportd'"],
            text=True, stderr=subprocess.DEVNULL, timeout=10,
        )
    except Exception:
        return {"state": "unknown", "source": MACOS_SOURCE, "events": [], "detail": "event_log_unavailable"}
    return parse_macos_events(output)
