"""Explicitly enabled, bounded history of verified network-interface states."""
from __future__ import annotations

import datetime
import hashlib
import json
import os
import re
import tempfile
from pathlib import Path

SOURCE = "macos_psutil_interface_status"
_ID_RE = re.compile(r"^[0-9a-f]{20}$")
_MAX_EVENTS = 120
_ALLOWED_INTERVALS = frozenset({60, 300, 900})
_MONITOR_TTL = 6 * 60 * 60
_DEFAULT = {"enabled": False, "interface_id": None, "history_interface_id": None,
            "interval_seconds": 0, "expires_at": None, "events": []}


def _interface_id(name: str) -> str:
    return hashlib.sha256(b"xider-netif-v1\0" + name.encode("utf-8", "replace")).hexdigest()[:20]


def _timestamp(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(datetime.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _valid(value: object) -> bool:
    if not isinstance(value, dict) or set(value) != set(_DEFAULT):
        return False
    if type(value["enabled"]) is not bool or type(value["interval_seconds"]) is not int:
        return False
    if value["enabled"]:
        if not isinstance(value["interface_id"], str) or not _ID_RE.fullmatch(value["interface_id"]):
            return False
        if value["interval_seconds"] not in _ALLOWED_INTERVALS:
            return False
        if type(value["expires_at"]) is not int or value["expires_at"] <= 0:
            return False
    elif value["interface_id"] is not None or value["interval_seconds"] != 0 or value["expires_at"] is not None:
        return False
    history_id = value["history_interface_id"]
    if history_id is not None and (not isinstance(history_id, str) or not _ID_RE.fullmatch(history_id)):
        return False
    events = value["events"]
    if not isinstance(events, list) or len(events) > _MAX_EVENTS:
        return False
    previous = ""
    for event in events:
        if (not isinstance(event, dict) or set(event) != {"at", "state", "kind"}
                or event["state"] not in {"online", "offline"}
                or event["kind"] not in {"baseline", "transition"}):
            return False
        at = _timestamp(event["at"])
        if at is None or at < previous:
            return False
        previous = at
    return True


def load_monitor(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return {**_DEFAULT, "events": []}
    return value if _valid(value) else {**_DEFAULT, "events": []}


def _save(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix="interface-status-history.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, separators=(",", ":"))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def configure_monitor(path: Path, interface_id: str, *, interval_seconds: int = 300, now: int | None = None) -> dict:
    if not isinstance(interface_id, str) or not _ID_RE.fullmatch(interface_id):
        raise ValueError("invalid network interface reference")
    if type(interval_seconds) is not int or interval_seconds not in _ALLOWED_INTERVALS:
        raise ValueError("unsupported interface-status interval")
    current = load_monitor(path)
    current_time = int(datetime.datetime.now(datetime.timezone.utc).timestamp()) if now is None else now
    if type(current_time) is not int or current_time < 0:
        raise ValueError("invalid monitor clock")
    same_interface = current.get("history_interface_id") == interface_id
    value = {"enabled": True, "interface_id": interface_id, "history_interface_id": interface_id,
             "interval_seconds": interval_seconds, "expires_at": current_time + _MONITOR_TTL,
             "events": current["events"] if same_interface else []}
    _save(path, value)
    return value


def stop_monitor(path: Path) -> dict:
    current = load_monitor(path)
    value = {"enabled": False, "interface_id": None, "history_interface_id": current.get("history_interface_id"),
             "interval_seconds": 0, "expires_at": None, "events": current["events"]}
    _save(path, value)
    return value


def record_sample(path: Path, interface_id: str, sample: object, *, now: int | None = None) -> dict:
    current = load_monitor(path)
    result = {"event": None, "history": current["events"]}
    current_time = int(datetime.datetime.now(datetime.timezone.utc).timestamp()) if now is None else now
    if (not current["enabled"] or current_time >= current["expires_at"]
            or interface_id != current["interface_id"] or not isinstance(sample, dict)
            or sample.get("state") not in {"online", "offline"}):
        return result
    at = _timestamp(sample.get("sampled_at"))
    if at is None:
        return result
    events = list(current["events"])
    if not events or events[-1]["state"] != sample["state"]:
        kind = "baseline" if not events else "transition"
        if events and at < events[-1]["at"]:
            return result
        events.append({"at": at, "state": sample["state"], "kind": kind})
        events = events[-_MAX_EVENTS:]
        current["events"] = events
        _save(path, current)
        result["event"] = "baseline" if kind == "baseline" else sample["state"]
    result["history"] = events
    return result


def read_history(path: Path, interface_id: str) -> dict:
    current = load_monitor(path)
    if not isinstance(interface_id, str) or not _ID_RE.fullmatch(interface_id):
        return {"state": "unknown", "source": SOURCE, "events": [], "detail": "invalid_interface"}
    events = current["events"] if current.get("history_interface_id") == interface_id else []
    return {"state": "available" if events else "unsupported", "source": SOURCE, "events": events,
            "detail": None if events else "no_observed_transitions"}


def collect_catalog(stats: object = None) -> dict:
    try:
        if stats is None:
            import psutil
            stats = psutil.net_if_stats()
        if not isinstance(stats, dict):
            raise TypeError("invalid interface status")
    except Exception:
        return {"state": "unknown", "source": SOURCE, "interfaces": [], "detail": "status_source_unavailable"}
    rows = [{"interface_id": _interface_id(name), "label": " ".join(str(name).split())[:64]}
            for name in stats if isinstance(name, str) and name.strip()]
    rows.sort(key=lambda row: (row["label"].casefold(), row["interface_id"]))
    rows = rows[:32]
    return {"state": "available" if rows else "unsupported", "source": SOURCE, "interfaces": rows,
            "detail": None if rows else "no_interfaces"}


def collect_interface_status(interface_id: str, stats: object = None, *, sampled_at: datetime.datetime | None = None) -> dict:
    base = {"state": "unknown", "source": SOURCE, "interface_id": interface_id if isinstance(interface_id, str) else None,
            "label": None, "sampled_at": None, "detail": "interface_status_unavailable"}
    if not isinstance(interface_id, str) or not _ID_RE.fullmatch(interface_id):
        base["detail"] = "invalid_interface"
        return base
    try:
        if stats is None:
            import psutil
            stats = psutil.net_if_stats()
    except Exception:
        return base
    matches = [(name, row) for name, row in stats.items() if isinstance(name, str) and _interface_id(name) == interface_id]
    if len(matches) != 1:
        base["detail"] = "interface_not_found"
        return base
    name, row = matches[0]
    is_up = getattr(row, "isup", None)
    if type(is_up) is not bool:
        return base
    stamp = sampled_at or datetime.datetime.now(datetime.timezone.utc)
    normalized = _timestamp(stamp.isoformat()) if isinstance(stamp, datetime.datetime) and stamp.tzinfo else None
    if normalized is None:
        return base
    return {"state": "online" if is_up else "offline", "source": SOURCE, "interface_id": interface_id,
            "label": " ".join(name.split())[:64], "sampled_at": normalized, "detail": None}
