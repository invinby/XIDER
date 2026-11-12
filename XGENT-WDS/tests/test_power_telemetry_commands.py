import datetime

import pytest

from xg_commands import power_telemetry


@pytest.mark.parametrize(
    ("handler", "topic", "snapshot", "expected_type", "expected_schema", "expected_data"),
    [
        (power_telemetry.thermal_snapshot, "thermal_snapshot",
         {"state": "supported", "source": "windows_wmi", "sensors": [{"c": 42}]},
         "thermal_snapshot", "xider.thermal-snapshot.v1", {"sensors": [{"c": 42}]}),
        (power_telemetry.battery_health, "battery_health",
         {"state": "unknown", "source": "windows_wmi", "batteries": [{"health": None}]},
         "battery_health_snapshot", "xider.battery-health.v1", {"batteries": [{"health": None}]}),
        (power_telemetry.battery_cycles, "battery_cycles",
         {"state": "supported", "source": "windows_wmi", "batteries": [{"cycles": 4}]},
         "battery_cycle_snapshot", "xider.battery-cycles.v1", {"batteries": [{"cycles": 4}]}),
        (power_telemetry.energy_usage, "energy_usage",
         {"state": "unsupported", "source": "windows_srum", "unit": "mWh", "apps": []},
         "energy_usage_snapshot", "xider.energy-usage.v1", {"unit": None, "apps": []}),
    ],
)
def test_power_telemetry_handlers_preserve_response_contract(
    handler, topic, snapshot, expected_type, expected_schema, expected_data
):
    published = []
    handler(
        {"id": "ticket-123"},
        publish_response=lambda actual_topic, response: published.append((actual_topic, response)),
        device_id="device-1",
        read_snapshot=lambda: snapshot,
        datetime_module=datetime,
    )

    actual_topic, response = published[0]
    assert actual_topic == topic
    assert response["type"] == expected_type
    assert response["schema"] == expected_schema
    assert response["device_id"] == "device-1"
    assert response["request_id"] == "ticket-123"
    assert response["state"] == snapshot["state"]
    assert response["source"] == snapshot["source"]
    assert datetime.datetime.fromisoformat(response["checked_at"]).utcoffset() == datetime.timedelta(0)
    assert response["data"] == expected_data


def test_power_telemetry_handlers_drop_invalid_request_ids():
    published = []
    power_telemetry.battery_health(
        {"id": "x" * 129},
        publish_response=lambda _topic, response: published.append(response),
        device_id="device-1",
        read_snapshot=lambda: {"state": "unknown", "source": "none", "batteries": []},
        datetime_module=datetime,
    )

    assert published[0]["request_id"] is None
