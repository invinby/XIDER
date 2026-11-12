"""Bounded telemetry samplers and in-process history projections."""

from __future__ import annotations

import ctypes
import logging
from threading import Lock

log = logging.getLogger(__name__)
_MEMORY_PRESSURE_SOURCE = "windows_memory_resource_notification"


def regional_settings_snapshot(request_id: object = None, *, device_id: str, datetime_module, locale_module, re_module) -> dict:
    """Read timezone and locale independently without mutating OS or process settings."""
    checked_at = datetime_module.datetime.now(datetime_module.timezone.utc).isoformat()
    timezone_data = {"state": "unknown", "source": "python_localtime", "name": None, "utc_offset_minutes": None}
    locale_data = {"state": "unknown", "source": "python_runtime_locale", "name": None}
    try:
        local_now = datetime_module.datetime.now().astimezone()
        name = local_now.tzname()
        offset = local_now.utcoffset()
        offset_minutes = int(offset.total_seconds() // 60) if offset is not None else None
        if isinstance(name, str) and name.strip():
            timezone_data["name"] = name.strip()[:128]
        if offset_minutes is not None and -840 <= offset_minutes <= 840:
            timezone_data["utc_offset_minutes"] = offset_minutes
        if timezone_data["name"] is not None or timezone_data["utc_offset_minutes"] is not None:
            timezone_data["state"] = "supported"
    except (OSError, OverflowError, ValueError):
        pass
    try:
        language, _encoding = locale_module.getlocale()
        if isinstance(language, str) and language.strip():
            locale_data.update(state="supported", name=language.strip()[:128])
    except (TypeError, ValueError, locale_module.Error):
        pass
    return {
        "schema": 1,
        "feature_id": "D01-F23",
        "device_id": device_id,
        "request_id": request_id if isinstance(request_id, str) and re_module.fullmatch(r"[0-9a-fA-F]{8,64}", request_id) else None,
        "source": "local_os_runtime_apis",
        "state": "supported" if "supported" in {timezone_data["state"], locale_data["state"]} else "unknown",
        "checked_at": checked_at,
        "data": {"timezone": timezone_data, "locale": locale_data},
    }


def _read_memory_pressure_sample() -> dict:
    """Read Windows low/high memory-resource notifications."""
    try:
        kernel32 = ctypes.windll.kernel32
        kernel32.CreateMemoryResourceNotification.argtypes = [ctypes.c_int]
        kernel32.CreateMemoryResourceNotification.restype = ctypes.c_void_p
        kernel32.QueryMemoryResourceNotification.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.wintypes.BOOL)]
        kernel32.QueryMemoryResourceNotification.restype = ctypes.wintypes.BOOL
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        kernel32.CloseHandle.restype = ctypes.wintypes.BOOL
        low_handle = kernel32.CreateMemoryResourceNotification(0)
        high_handle = kernel32.CreateMemoryResourceNotification(1)
        if not low_handle or not high_handle:
            if low_handle:
                kernel32.CloseHandle(low_handle)
            if high_handle:
                kernel32.CloseHandle(high_handle)
            return {"state": "unknown", "available_percent": None, "source": _MEMORY_PRESSURE_SOURCE}
        try:
            low = ctypes.wintypes.BOOL()
            high = ctypes.wintypes.BOOL()
            if (not kernel32.QueryMemoryResourceNotification(low_handle, ctypes.byref(low))
                    or not kernel32.QueryMemoryResourceNotification(high_handle, ctypes.byref(high))):
                return {"state": "unknown", "available_percent": None, "source": _MEMORY_PRESSURE_SOURCE}
            state = "low" if low.value else "high" if high.value else "intermediate"
            return {"state": state, "available_percent": None, "source": _MEMORY_PRESSURE_SOURCE}
        finally:
            kernel32.CloseHandle(low_handle)
            kernel32.CloseHandle(high_handle)
    except Exception:
        log.info("Windows memory-resource notification unavailable", exc_info=True)
        return {"state": "unknown", "available_percent": None, "source": _MEMORY_PRESSURE_SOURCE}


def _memory_pressure_history_sample(raw: dict) -> dict:
    if not isinstance(raw, dict) or raw.get("source") != _MEMORY_PRESSURE_SOURCE:
        return {"state": "unknown", "available_percent": None}
    state = raw.get("state")
    if state not in {"low", "intermediate", "high"} or raw.get("available_percent") is not None:
        return {"state": "unknown", "available_percent": None}
    return {"state": state, "available_percent": None}


def volume_history_measurement(
    device: str,
    mountpoint: str,
    total: int,
    used: int,
    now: float,
    *,
    history: dict[str, list[dict[str, float | int]]],
    history_lock: Lock,
    min_span_seconds: int,
    retention_seconds: int,
) -> dict:
    """Compare a live filesystem reading with bounded in-process observations."""
    key = f"{device[:256]}\0{mountpoint[:512]}"
    with history_lock:
        if key not in history and len(history) >= 256:
            oldest_key = min(
                history,
                key=lambda item: max((float(sample.get("at", 0)) for sample in history[item]), default=0),
            )
            history.pop(oldest_key, None)
        samples = history.setdefault(key, [])
        samples[:] = [
            row for row in samples
            if isinstance(row, dict) and 0 <= now - float(row.get("at", -1)) <= retention_seconds
        ]
        previous_capacity = any(
            row.get("total") != total and now - float(row.get("at", now)) >= min_span_seconds
            for row in samples
        )
        baseline = max(
            (row for row in samples
             if row.get("total") == total
             and min_span_seconds <= now - float(row.get("at", now)) <= retention_seconds),
            key=lambda row: float(row.get("at", 0)), default=None,
        )
        if not samples or now - float(samples[-1].get("at", now)) >= 300:
            samples.append({"at": now, "total": total, "used": used})
            del samples[:-128]
        row = {
            "total_bytes": total,
            "used_bytes": used,
            "free_bytes": total - used,
            "used_percent": round(used * 100.0 / total, 1),
            "trend_state": "capacity_changed" if previous_capacity and baseline is None else "insufficient_history",
            "observed_span_seconds": None,
            "delta_used_bytes": None,
            "used_bytes_per_hour": None,
            "estimated_hours_to_full": None,
            "history_scope": "agent_process",
        }
        if baseline is not None:
            elapsed = now - float(baseline["at"])
            delta = used - int(baseline["used"])
            row["observed_span_seconds"] = int(elapsed)
            row["delta_used_bytes"] = delta
            if delta > 0:
                rate = delta * 3600.0 / elapsed
                row.update(
                    trend_state="increasing",
                    used_bytes_per_hour=round(rate),
                    estimated_hours_to_full=round(max(0, total - used) / rate, 1) if rate > 0 else None,
                )
            elif delta < 0:
                row["trend_state"] = "decreasing"
                row["used_bytes_per_hour"] = round(delta * 3600.0 / elapsed)
            else:
                row["trend_state"] = "stable"
                row["used_bytes_per_hour"] = 0
        return row
