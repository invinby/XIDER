"""Bounded metadata-only journal of local media operations."""

from __future__ import annotations

import json
import os
import tempfile
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


MEDIA_ACTIONS = frozenset({
    "screenshot", "display_region_screenshot", "window_screenshot", "display_contact_sheet",
    "webcam", "camera_series", "camera_quality", "microphone_catalog", "mic",
    "microphone_level", "microphone_balance", "microphone_test", "audio_output_catalog",
    "audio_output_switch", "media_temp_cleanup",
})
_OUTCOMES = frozenset({"captured", "completed", "unavailable", "denied", "unknown", "failed"})
_MEDIA_FIELDS = frozenset({"audio", "image", "screenshot", "photo", "frame", "frames"})
_LOCK = threading.RLock()
_MAX_FILE_BYTES = 128 * 1024


def _utc(value: datetime | None) -> datetime:
    current = value or datetime.now(timezone.utc)
    if current.tzinfo is None or current.utcoffset() is None:
        raise ValueError("timestamp_must_be_timezone_aware")
    return current.astimezone(timezone.utc)


def _load(path: Path) -> list[dict[str, str]]:
    try:
        if not path.is_file() or path.stat().st_size > _MAX_FILE_BYTES:
            return []
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return []
    if not isinstance(value, list):
        return []
    rows: list[dict[str, str]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        action, outcome, raw_at = item.get("action"), item.get("outcome"), item.get("at")
        if (not isinstance(action, str) or action not in MEDIA_ACTIONS
                or not isinstance(outcome, str) or outcome not in _OUTCOMES
                or not isinstance(raw_at, str)):
            continue
        try:
            at = datetime.fromisoformat(raw_at)
            if at.tzinfo is None or at.utcoffset() is None:
                continue
            rows.append({"at": at.astimezone(timezone.utc).isoformat(), "action": action, "outcome": outcome})
        except ValueError:
            continue
    return rows


def _outcome(payload: dict[str, Any]) -> str:
    state = payload.get("state")
    if not isinstance(state, str):
        state = ""
    if state in {"denied", "unavailable", "unknown", "failed", "error"}:
        return "failed" if state in {"failed", "error"} else state
    if any(payload.get(field) not in (None, "", [], {}) for field in _MEDIA_FIELDS):
        return "captured"
    if payload.get("ok") is False or payload.get("error"):
        return "failed"
    if payload.get("ok") is True or state in {"supported", "completed", "success", "ok"}:
        return "completed"
    return "unknown"


def record_response(
    path: str | os.PathLike[str], action: str, payload: dict[str, Any], *,
    now: datetime | None = None, max_entries: int = 100, retention_days: int = 30,
) -> bool:
    """Persist only action/time/outcome; never copy a response payload field."""
    if action not in MEDIA_ACTIONS or not isinstance(payload, dict):
        return False
    if not isinstance(max_entries, int) or isinstance(max_entries, bool) or not 1 <= max_entries <= 100:
        raise ValueError("invalid_media_history_limit")
    if not isinstance(retention_days, int) or isinstance(retention_days, bool) or not 1 <= retention_days <= 30:
        raise ValueError("invalid_media_history_retention")
    current = _utc(now)
    event = {"at": current.isoformat(), "action": action, "outcome": _outcome(payload)}
    target = Path(path)
    with _LOCK:
        rows = _load(target)
        cutoff = current - timedelta(days=retention_days)
        fresh = []
        for row in rows:
            try:
                at = datetime.fromisoformat(row["at"])
            except ValueError:
                continue
            if cutoff <= at <= current:
                fresh.append(row)
        fresh.append(event)
        fresh = fresh[-max_entries:]
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=".media-history-", suffix=".tmp", dir=target.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
                json.dump(fresh, stream, ensure_ascii=False, separators=(",", ":"))
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, target)
        finally:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
    return True


def read_events(
    path: str | os.PathLike[str], *, now: datetime | None = None, limit: int = 20,
    retention_days: int = 30,
) -> list[dict[str, str]]:
    """Read a newest-first bounded view of metadata without mutating history."""
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 100:
        raise ValueError("invalid_media_history_limit")
    if not isinstance(retention_days, int) or isinstance(retention_days, bool) or not 1 <= retention_days <= 30:
        raise ValueError("invalid_media_history_retention")
    current = _utc(now)
    cutoff = current - timedelta(days=retention_days)
    with _LOCK:
        rows = _load(Path(path))
    fresh = []
    for row in rows:
        try:
            at = datetime.fromisoformat(row["at"])
        except ValueError:
            continue
        if cutoff <= at <= current:
            fresh.append(row)
    return list(reversed(fresh[-limit:]))
