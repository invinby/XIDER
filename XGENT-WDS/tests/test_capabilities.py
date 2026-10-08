"""Snapshot contract tests without accessing OS permissions or hardware."""
import datetime

import xgent_wds as agent


def test_snapshot_inventory_is_complete_and_never_assumes_permission(monkeypatch):
    monkeypatch.setattr(agent, "_module_available", lambda _name: True)
    monkeypatch.setattr(agent, "_detect_features", lambda: {
        "screenshot": True, "clipboard": True, "battery": False,
    })
    client = object.__new__(agent.XgentClient)
    sent = []
    client._publish_response = lambda topic, payload: sent.append((topic, payload))
    client._do_capabilities({})
    topic, snapshot = sent[0]
    assert topic == "capabilities"
    assert snapshot.get("schema") == 1
    assert snapshot["platform"] == agent.PLATFORM
    assert datetime.datetime.fromisoformat(snapshot["checked_at"]).utcoffset() == datetime.timedelta(0)
    items = {item["id"]: item for item in snapshot["items"]}
    assert set(items) == {"screen_recording", "camera", "microphone", "location", "accessibility", "file_access"}
    assert all(item["state"] in {"needs_live_test", "unavailable", "unknown"} for item in items.values())
    assert all(item["detail"] and "settings_uri" in item for item in items.values())
    assert "UAC" in items["accessibility"]["detail"]
    assert "source=ip_estimate" in items["location"]["detail"]
    assert "uncertainty=" in items["location"]["detail"]
    assert "features" in snapshot and "feature_status" in snapshot


def test_missing_dependencies_are_unavailable(monkeypatch):
    monkeypatch.setattr(agent, "_module_available", lambda _name: False)
    monkeypatch.setattr(agent, "_detect_features", lambda: {
        "screenshot": False, "clipboard": False, "battery": False,
    })
    client = object.__new__(agent.XgentClient)
    sent = []
    client._publish_response = lambda _topic, payload: sent.append(payload)
    client._do_capabilities({})
    assert sent[0].get("schema") == 1
    items = {item["id"]: item for item in sent[0]["items"]}
    assert items["camera"]["state"] == "unavailable"
    assert items["microphone"]["state"] == "unavailable"
    assert items["screen_recording"]["state"] == "unavailable"
