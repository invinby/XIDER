"""Read-only cached inventory command response builders."""

from __future__ import annotations


def _request_id(payload: dict) -> str | None:
    value = payload.get("id")
    return value if isinstance(value, str) and 1 <= len(value) <= 128 else None


def os_support(
    payload: dict, *, publish_response, device_id: str, read_inventory_snapshot,
    snapshot_reader, config_dir, datetime_module,
) -> None:
    request_id = _request_id(payload)
    identity = read_inventory_snapshot(
        "os-support", snapshot_reader, config_dir,
        force=payload.get("force") is True,
        unknown={"state": "unknown", "family": "windows", "release": None, "edition": None},
    )
    publish_response("os_support", {
        "type": "os_support_snapshot", "schema": "xider.os-support.v1",
        "feature_id": "D01-F13", "device_id": device_id, "request_id": request_id,
        "state": identity["state"], "source": "windows_current_version_registry",
        "checked_at": datetime_module.datetime.now(datetime_module.timezone.utc).isoformat(timespec="seconds"),
        "data": {"family": identity["family"], "release": identity["release"], "edition": identity["edition"]},
    })


def hardware_inventory(
    payload: dict, *, publish_response, device_id: str, read_inventory_snapshot,
    snapshot_reader, config_dir, datetime_module,
) -> None:
    request_id = _request_id(payload)
    inventory = read_inventory_snapshot(
        "hardware", snapshot_reader, config_dir,
        force=payload.get("force") is True,
        unknown={"state": "unknown", "manufacturer": None, "model": None, "cpu": None,
                 "architecture": None, "memory_gib": None, "graphics": []},
    )
    publish_response("hardware_inventory", {
        "type": "hardware_inventory_snapshot", "schema": "xider.hardware-inventory.v1",
        "feature_id": "D01-F14", "device_id": device_id, "request_id": request_id,
        "state": inventory["state"], "source": "windows_cim_hardware_inventory",
        "checked_at": datetime_module.datetime.now(datetime_module.timezone.utc).isoformat(timespec="seconds"),
        "data": {key: value for key, value in inventory.items() if key != "state"},
    })


def driver_inventory(
    payload: dict, *, publish_response, device_id: str, read_inventory_snapshot,
    snapshot_reader, config_dir, datetime_module,
) -> None:
    request_id = _request_id(payload)
    inventory = read_inventory_snapshot(
        "drivers", snapshot_reader, config_dir,
        force=payload.get("force") is True,
        unknown={"state": "unknown", "drivers": []},
    )
    publish_response("driver_inventory", {
        "type": "driver_inventory_snapshot", "schema": "xider.driver-inventory.v1",
        "feature_id": "D01-F15", "device_id": device_id, "request_id": request_id,
        "state": inventory["state"], "source": "windows_cim_signed_drivers",
        "checked_at": datetime_module.datetime.now(datetime_module.timezone.utc).isoformat(timespec="seconds"),
        "data": {"drivers": inventory["drivers"]},
    })


def simple_inventory_snapshot(
    payload: dict, *, publish_response, device_id: str, read_snapshot,
    datetime_module, topic: str, response_type: str, schema: str,
    feature_id: str, source: str, data_key: str, snapshot_key: str,
    source_from_snapshot: bool = False,
    additional_data: dict[str, str] | None = None,
) -> None:
    request_id = _request_id(payload)
    snapshot = read_snapshot()
    publish_response(topic, {
        "type": response_type, "schema": schema,
        "feature_id": feature_id, "device_id": device_id, "request_id": request_id,
        "state": snapshot["state"], "source": snapshot["source"] if source_from_snapshot else source,
        "checked_at": datetime_module.datetime.now(datetime_module.timezone.utc).isoformat(timespec="seconds"),
        "data": {
            data_key: snapshot[snapshot_key],
            **{key: snapshot[value] for key, value in (additional_data or {}).items()},
        },
    })
