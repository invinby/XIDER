import datetime

import pytest

from xg_commands import inventory_telemetry


@pytest.mark.parametrize(
    ("handler", "topic", "snapshot", "expected_type", "expected_schema", "expected_data"),
    [
        (inventory_telemetry.os_support, "os_support",
         {"state": "supported", "family": "windows", "release": "11", "edition": "Pro"},
         "os_support_snapshot", "xider.os-support.v1",
         {"family": "windows", "release": "11", "edition": "Pro"}),
        (inventory_telemetry.hardware_inventory, "hardware_inventory",
         {"state": "supported", "manufacturer": "Example", "model": "Workstation", "memory_gib": 32},
         "hardware_inventory_snapshot", "xider.hardware-inventory.v1",
         {"manufacturer": "Example", "model": "Workstation", "memory_gib": 32}),
        (inventory_telemetry.driver_inventory, "driver_inventory",
         {"state": "unknown", "drivers": []},
         "driver_inventory_snapshot", "xider.driver-inventory.v1", {"drivers": []}),
    ],
)
def test_inventory_handlers_preserve_cached_response_contract(
    handler, topic, snapshot, expected_type, expected_schema, expected_data
):
    published = []
    cache_calls = []

    def read_inventory_snapshot(name, reader, config_dir, *, force, unknown):
        cache_calls.append((name, reader, config_dir, force, unknown))
        return snapshot

    handler(
        {"id": "0123456789ab", "force": True},
        publish_response=lambda actual_topic, response: published.append((actual_topic, response)),
        device_id="device-1",
        read_inventory_snapshot=read_inventory_snapshot,
        snapshot_reader=lambda: {},
        config_dir="agent-config",
        datetime_module=datetime,
    )

    actual_topic, response = published[0]
    assert actual_topic == topic
    assert response["type"] == expected_type
    assert response["schema"] == expected_schema
    assert response["device_id"] == "device-1"
    assert response["request_id"] == "0123456789ab"
    assert response["state"] == snapshot["state"]
    assert response["data"] == expected_data
    assert datetime.datetime.fromisoformat(response["checked_at"]).utcoffset() == datetime.timedelta(0)
    assert cache_calls[0][2:4] == ("agent-config", True)


def test_inventory_force_requires_json_true_not_integer_one():
    cache_calls = []
    inventory_telemetry.driver_inventory(
        {"force": 1},
        publish_response=lambda *_args: None,
        device_id="device-1",
        read_inventory_snapshot=lambda name, reader, config_dir, *, force, unknown: cache_calls.append(force) or {
            "state": "unknown", "drivers": []
        },
        snapshot_reader=lambda: {},
        config_dir="agent-config",
        datetime_module=datetime,
    )

    assert cache_calls == [False]


@pytest.mark.parametrize(
    ("topic", "response_type", "schema", "feature_id", "source", "data_key", "snapshot_key", "snapshot"),
    [
        ("installed_app_inventory", "installed_app_inventory_snapshot", "xider.installed-app-inventory.v1",
         "D01-F16", "windows_uninstall_registry_projection", "apps", "apps", {"state": "supported", "apps": []}),
        ("startup_inventory", "startup_inventory_snapshot", "xider.startup-inventory.v1",
         "D01-F17", "windows_run_keys_and_startup_folder_names", "entries", "entries", {"state": "supported", "entries": []}),
        ("service_inventory", "service_inventory_snapshot", "xider.service-inventory.v1",
         "D01-F18", "windows_cim_service_status", "services", "services", {"state": "supported", "services": []}),
        ("crash_summary", "crash_summary_snapshot", "xider.crash-summary.v1",
         "D01-F19", "windows_event_log_provider_event_aggregate", "events", "events", {"state": "unknown", "events": []}),
    ],
)
def test_simple_inventory_snapshot_preserves_each_command_contract(
    topic, response_type, schema, feature_id, source, data_key, snapshot_key, snapshot
):
    published = []
    inventory_telemetry.simple_inventory_snapshot(
        {"id": "request-01"},
        publish_response=lambda actual_topic, response: published.append((actual_topic, response)),
        device_id="device-1",
        read_snapshot=lambda: snapshot,
        datetime_module=datetime,
        topic=topic,
        response_type=response_type,
        schema=schema,
        feature_id=feature_id,
        source=source,
        data_key=data_key,
        snapshot_key=snapshot_key,
    )

    actual_topic, response = published[0]
    assert actual_topic == topic
    assert response["type"] == response_type
    assert response["schema"] == schema
    assert response["feature_id"] == feature_id
    assert response["device_id"] == "device-1"
    assert response["request_id"] == "request-01"
    assert response["state"] == snapshot["state"]
    assert response["source"] == source
    assert response["data"] == {data_key: snapshot[snapshot_key]}


@pytest.mark.parametrize(
    ("topic", "response_type", "schema", "feature_id", "snapshot", "expected_data"),
    [
        ("smart_trend", "smart_trend_snapshot", "xider.smart-trend.v1", "D01-F20",
         {"state": "supported", "source": "smartctl", "disks": []}, {"disks": []}),
        ("volume_topology", "volume_topology_snapshot", "xider.volume-topology.v1", "D01-F21",
         {"state": "supported", "source": "windows_storage_api", "volumes": []}, {"volumes": []}),
        ("clock_drift", "clock_drift_snapshot", "xider.clock-drift.v1", "D01-F22",
         {"state": "supported", "source": "windows_time_api", "drift_ms": 5, "sample_ms": 1000},
         {"drift_ms": 5, "sample_ms": 1000}),
    ],
)
def test_dynamic_source_inventory_snapshots_keep_source_and_all_data_fields(
    topic, response_type, schema, feature_id, snapshot, expected_data
):
    published = []
    extra = {"sample_ms": "sample_ms"} if topic == "clock_drift" else None
    inventory_telemetry.simple_inventory_snapshot(
        {"id": "request-02"},
        publish_response=lambda actual_topic, response: published.append((actual_topic, response)),
        device_id="device-2",
        read_snapshot=lambda: snapshot,
        datetime_module=datetime,
        topic=topic,
        response_type=response_type,
        schema=schema,
        feature_id=feature_id,
        source="",
        data_key="drift_ms" if topic == "clock_drift" else "disks" if topic == "smart_trend" else "volumes",
        snapshot_key="drift_ms" if topic == "clock_drift" else "disks" if topic == "smart_trend" else "volumes",
        source_from_snapshot=True,
        additional_data=extra,
    )

    actual_topic, response = published[0]
    assert actual_topic == topic
    assert response["type"] == response_type
    assert response["schema"] == schema
    assert response["feature_id"] == feature_id
    assert response["source"] == snapshot["source"]
    assert response["data"] == expected_data
