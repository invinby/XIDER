import datetime

import pytest

from xg_commands import system_events


@pytest.mark.parametrize(
    ("handler", "topic", "snapshot", "response_type", "schema", "feature_id", "data"),
    [
        (system_events.sleep_wake_history, "sleep_wake_history",
         {"state": "supported", "source": "windows_events", "events": [{"state": "sleep"}]},
         "sleep_wake_history_snapshot", "xider.sleep-wake-history.v1", "D01-F10",
         {"history_days": 7, "events": [{"state": "sleep"}]}),
        (system_events.startup_duration, "startup_duration",
         {"state": "unknown", "source": "windows_event_100", "duration_ms": None, "completed_at": None},
         "startup_duration_snapshot", "xider.startup-duration.v1", "D01-F11",
         {"duration_ms": None, "completed_at": None}),
        (system_events.pending_restart, "pending_restart",
         {"state": "supported", "source": "windows_registry", "pending": False},
         "pending_restart_snapshot", "xider.pending-restart.v1", "D01-F12",
         {"pending": False, "reasons": [], "completeness": "unknown"}),
    ],
)
def test_system_event_commands_preserve_mqtt_snapshot_contract(
    handler, topic, snapshot, response_type, schema, feature_id, data
):
    published = []
    handler(
        {"id": "event-ticket"},
        publish_response=lambda actual_topic, response: published.append((actual_topic, response)),
        device_id="device-3",
        read_snapshot=lambda: snapshot,
        datetime_module=datetime,
    )

    actual_topic, response = published[0]
    assert actual_topic == topic
    assert response["type"] == response_type
    assert response["schema"] == schema
    assert response["feature_id"] == feature_id
    assert response["device_id"] == "device-3"
    assert response["request_id"] == "event-ticket"
    assert response["state"] == snapshot["state"]
    assert response["source"] == snapshot["source"]
    assert response["data"] == data
    assert datetime.datetime.fromisoformat(response["checked_at"]).utcoffset() == datetime.timedelta(0)
