"""Read-only battery and thermal command response builders."""

from __future__ import annotations


def _request_id(payload: dict) -> str | None:
    value = payload.get("id")
    return value if isinstance(value, str) and 1 <= len(value) <= 128 else None


def thermal_snapshot(payload: dict, *, publish_response, device_id: str, read_snapshot, datetime_module) -> None:
    request_id = _request_id(payload)
    snapshot = read_snapshot()
    result = {
        "type": "thermal_snapshot", "schema": "xider.thermal-snapshot.v1",
        "feature_id": "D01-F06", "device_id": device_id, "request_id": request_id,
        "state": snapshot["state"], "source": snapshot["source"],
        "checked_at": datetime_module.datetime.now(datetime_module.timezone.utc).isoformat(timespec="seconds"),
        "data": {"sensors": snapshot["sensors"]},
    }
    publish_response("thermal_snapshot", result)


def battery_health(payload: dict, *, publish_response, device_id: str, read_snapshot, datetime_module) -> None:
    request_id = _request_id(payload)
    snapshot = read_snapshot()
    result = {
        "type": "battery_health_snapshot", "schema": "xider.battery-health.v1",
        "feature_id": "D01-F07", "device_id": device_id, "request_id": request_id,
        "state": snapshot["state"], "source": snapshot["source"],
        "checked_at": datetime_module.datetime.now(datetime_module.timezone.utc).isoformat(timespec="seconds"),
        "data": {"batteries": snapshot["batteries"]},
    }
    publish_response("battery_health", result)


def battery_cycles(payload: dict, *, publish_response, device_id: str, read_snapshot, datetime_module) -> None:
    request_id = _request_id(payload)
    snapshot = read_snapshot()
    publish_response("battery_cycles", {
        "type": "battery_cycle_snapshot", "schema": "xider.battery-cycles.v1",
        "feature_id": "D01-F08", "device_id": device_id, "request_id": request_id,
        "state": snapshot["state"], "source": snapshot["source"],
        "checked_at": datetime_module.datetime.now(datetime_module.timezone.utc).isoformat(timespec="seconds"),
        "data": {"batteries": snapshot["batteries"]},
    })


def energy_usage(payload: dict, *, publish_response, device_id: str, read_snapshot, datetime_module) -> None:
    request_id = _request_id(payload)
    snapshot = read_snapshot()
    publish_response("energy_usage", {
        "type": "energy_usage_snapshot", "schema": "xider.energy-usage.v1",
        "feature_id": "D01-F09", "device_id": device_id, "request_id": request_id,
        "state": snapshot["state"], "source": snapshot["source"],
        "checked_at": datetime_module.datetime.now(datetime_module.timezone.utc).isoformat(timespec="seconds"),
        "data": {
            "unit": snapshot.get("unit") if snapshot["state"] == "supported" else None,
            "apps": snapshot.get("apps", []),
        },
    })
