"""Read a bounded WLAN AutoConfig event-log window; discard network identity."""
from __future__ import annotations

import subprocess
import datetime
import xml.etree.ElementTree as ET

WINDOWS_SOURCE = "windows_wlan_autoconfig_events"
_LOG_NAME = "Microsoft-Windows-WLAN-AutoConfig/Operational"


def parse_windows_events(text: str) -> dict:
    if not isinstance(text, str):
        return {"state": "unknown", "source": WINDOWS_SOURCE, "events": [], "detail": "invalid_event_log"}
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return {"state": "unknown", "source": WINDOWS_SOURCE, "events": [], "detail": "invalid_event_log"}
    events = []
    candidates = [root] if root.tag.rsplit("}", 1)[-1] == "Event" else root.findall(".//Event")
    for event in candidates:
        event_id = event.findtext("./System/EventID")
        at = event.find("./System/TimeCreated")
        timestamp = at.get("SystemTime") if at is not None else None
        if event_id not in {"8001", "8003"} or not isinstance(timestamp, str):
            continue
        try:
            parsed = datetime.datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                continue
            normalized = parsed.isoformat(timespec="seconds")
            if normalized.endswith("+00:00"):
                normalized = normalized[:-6] + "Z"
        except ValueError:
            continue
        events.append({"at": normalized, "kind": "connected" if event_id == "8001" else "disconnected"})
    events.sort(key=lambda row: row["at"])
    events = events[-20:]
    return {"state": "available" if events else "unsupported", "source": WINDOWS_SOURCE,
            "events": events, "detail": None if events else "no_connection_events"}


def collect_wifi_connection_history(check_output=subprocess.check_output) -> dict:
    query = "*[System[(EventID=8001 or EventID=8003)]]"
    try:
        output = check_output(["wevtutil", "qe", _LOG_NAME, f"/q:{query}", "/rd:true", "/c:100", "/f:xml"],
                              text=True, stderr=subprocess.DEVNULL, timeout=8)
    except Exception:
        return {"state": "unknown", "source": WINDOWS_SOURCE, "events": [], "detail": "event_log_unavailable"}
    return parse_windows_events(output)
