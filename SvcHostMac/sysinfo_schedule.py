"""Bounded, opt-in schedule state for D01-F26 resource summaries."""
from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path

ALLOWED_INTERVALS = frozenset({0, 900, 1800, 3600})


def normalize_interval(value: object) -> int:
    if type(value) is not int or value not in ALLOWED_INTERVALS:
        raise ValueError("interval must be one of 0, 900, 1800, or 3600 seconds")
    return value


def next_sample_at(interval: int, *, now: int | None = None) -> int | None:
    interval = normalize_interval(interval)
    return None if interval == 0 else int(time.time() if now is None else now) + interval


def load_schedule(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return {"interval_seconds": 0, "schedule_id": None, "next_sample_at": None}
    if (not isinstance(value, dict) or set(value) != {"interval_seconds", "schedule_id", "next_sample_at"}
            or type(value.get("interval_seconds")) is not int
            or value["interval_seconds"] not in ALLOWED_INTERVALS
            or (value["interval_seconds"] == 0 and value["next_sample_at"] is not None)
            or (value["interval_seconds"] > 0 and
                (not isinstance(value["schedule_id"], str) or len(value["schedule_id"]) != 32
                 or any(c not in "0123456789abcdef" for c in value["schedule_id"])
                 or type(value["next_sample_at"]) is not int or value["next_sample_at"] < 0))):
        return {"interval_seconds": 0, "schedule_id": None, "next_sample_at": None}
    return value


def save_schedule(path: Path, interval: int, schedule_id: str | None, *, now: int | None = None) -> dict:
    interval = normalize_interval(interval)
    if (not isinstance(schedule_id, str) or len(schedule_id) != 32
            or any(c not in "0123456789abcdef" for c in schedule_id)):
        raise ValueError("invalid schedule id")
    state = {"interval_seconds": interval, "schedule_id": schedule_id,
             "next_sample_at": next_sample_at(interval, now=now)}
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix="sysinfo-schedule.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(state, stream, separators=(",", ":"))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
    return state
