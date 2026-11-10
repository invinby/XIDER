"""Explicit, bounded per-interface usage-threshold schedule for D04-F19."""
from __future__ import annotations

import json
import os
import re
import tempfile
import time
from pathlib import Path

ALLOWED_INTERVALS = frozenset({0, 900, 1800, 3600})
ALLOWED_THRESHOLDS = frozenset({100_000_000, 500_000_000, 1_000_000_000, 5_000_000_000})
_DEFAULT = {"interval_seconds": 0, "schedule_id": None, "interface_id": None,
            "threshold_bytes": None, "next_sample_at": None}


def _valid(value: object) -> bool:
    if not isinstance(value, dict) or set(value) != set(_DEFAULT):
        return False
    interval, schedule_id = value.get("interval_seconds"), value.get("schedule_id")
    interface_id, threshold = value.get("interface_id"), value.get("threshold_bytes")
    next_at = value.get("next_sample_at")
    if type(interval) is not int or interval not in ALLOWED_INTERVALS:
        return False
    if not isinstance(schedule_id, str) or re.fullmatch(r"[0-9a-f]{32}", schedule_id) is None:
        return False
    if not isinstance(interface_id, str) or re.fullmatch(r"[0-9a-f]{20}", interface_id) is None:
        return False
    if type(threshold) is not int or threshold not in ALLOWED_THRESHOLDS:
        return False
    return next_at is None if interval == 0 else type(next_at) is int and next_at >= 0


def load_schedule(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return dict(_DEFAULT)
    return value if _valid(value) else dict(_DEFAULT)


def save_schedule(path: Path, interval_seconds: int, schedule_id: str,
                  interface_id: str, threshold_bytes: int, *, now: int | None = None,
                  first_sample: bool = False) -> dict:
    if type(interval_seconds) is not int or interval_seconds not in ALLOWED_INTERVALS:
        raise ValueError("unsupported network traffic interval")
    if not isinstance(schedule_id, str) or re.fullmatch(r"[0-9a-f]{32}", schedule_id) is None:
        raise ValueError("invalid network traffic schedule id")
    if not isinstance(interface_id, str) or re.fullmatch(r"[0-9a-f]{20}", interface_id) is None:
        raise ValueError("invalid network interface reference")
    if type(threshold_bytes) is not int or threshold_bytes not in ALLOWED_THRESHOLDS:
        raise ValueError("unsupported network traffic threshold")
    current = int(time.time() if now is None else now)
    if current < 0:
        raise ValueError("invalid schedule clock")
    if type(first_sample) is not bool:
        raise ValueError("invalid first-sample mode")
    value = {"interval_seconds": interval_seconds, "schedule_id": schedule_id,
             "interface_id": interface_id, "threshold_bytes": threshold_bytes,
             "next_sample_at": (current if first_sample else current + interval_seconds) if interval_seconds else None}
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix="network-traffic-schedule.", suffix=".tmp", dir=path.parent)
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
    return value
