"""Read-only system-event command response builders."""

from __future__ import annotations


def _request_id(payload: dict) -> str | None:
    value = payload.get("id")
    return value if isinstance(value, str) and 1 <= len(value) <= 128 else None


def sleep_wake_history(payload: dict, *, publish_response, device_id: str, read_snapshot, datetime_module) -> None:
    request_id = _request_id(payload)
    snapshot = read_snapshot()
    publish_response("sleep_wake_history", {
        "type": "sleep_wake_history_snapshot", "schema": "xider.sleep-wake-history.v1",
        "feature_id": "D01-F10", "device_id": device_id, "request_id": request_id,
        "state": snapshot["state"], "source": snapshot["source"],
        "checked_at": datetime_module.datetime.now(datetime_module.timezone.utc).isoformat(timespec="seconds"),
        "data": {"history_days": 7, "events": snapshot["events"]},
    })


def startup_duration(payload: dict, *, publish_response, device_id: str, read_snapshot, datetime_module) -> None:
    request_id = _request_id(payload)
    snapshot = read_snapshot()
    publish_response("startup_duration", {
        "type": "startup_duration_snapshot", "schema": "xider.startup-duration.v1",
        "feature_id": "D01-F11", "device_id": device_id, "request_id": request_id,
        "state": snapshot["state"], "source": snapshot["source"],
        "checked_at": datetime_module.datetime.now(datetime_module.timezone.utc).isoformat(timespec="seconds"),
        "data": {"duration_ms": snapshot.get("duration_ms"), "completed_at": snapshot.get("completed_at")},
    })


def pending_restart(payload: dict, *, publish_response, device_id: str, read_snapshot, datetime_module) -> None:
    request_id = _request_id(payload)
    snapshot = read_snapshot()
    publish_response("pending_restart", {
        "type": "pending_restart_snapshot", "schema": "xider.pending-restart.v1",
        "feature_id": "D01-F12", "device_id": device_id, "request_id": request_id,
        "state": snapshot["state"], "source": snapshot["source"],
        "checked_at": datetime_module.datetime.now(datetime_module.timezone.utc).isoformat(timespec="seconds"),
        "data": {
            "pending": snapshot.get("pending"),
            "reasons": snapshot.get("reasons", []),
            "completeness": snapshot.get("completeness", "unknown"),
        },
    })
