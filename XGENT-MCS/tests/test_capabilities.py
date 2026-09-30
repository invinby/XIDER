"""Capability reporting tests; no camera, microphone, or screen is accessed."""

import xgent_mcs as mcs


def test_feature_report_never_claims_live_permissions(monkeypatch):
    monkeypatch.setattr(mcs.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(mcs, "_module_available", lambda _name: True)
    monkeypatch.setattr(mcs.psutil, "sensors_battery", lambda: None)

    status = mcs._detect_feature_status()

    assert status["screenshot"] == "permission_unverified"
    assert status["webcam"] == "permission_unverified"
    assert status["microphone"] == "permission_unverified"
    assert status["battery"] == "unavailable"
    assert status["geolocation"] == "approximate"
    assert status["shell"] == "supported"


def test_feature_report_marks_missing_macos_tools_and_dependencies(monkeypatch):
    monkeypatch.setattr(mcs.shutil, "which", lambda _name: None)
    monkeypatch.setattr(mcs, "_module_available", lambda _name: False)
    monkeypatch.setattr(mcs.psutil, "sensors_battery", lambda: None)

    status = mcs._detect_feature_status()

    assert status["screenshot"] == "unavailable"
    assert status["webcam"] == "dependency_missing"
    assert status["microphone"] == "dependency_missing"
    assert status["clipboard"] == "unavailable"


def test_capabilities_response_includes_status_and_legacy_fields(monkeypatch):
    client = object.__new__(mcs.XgentClient)
    sent = []
    client._publish_response = lambda topic, payload: sent.append((topic, payload))
    monkeypatch.setattr(mcs, "_detect_feature_status", lambda: {
        "shell": "supported",
        "open_app": "supported",
        "battery": "unavailable",
        "microphone": "permission_unverified",
        "clipboard": "device_unverified",
        "screenshot": "permission_unverified",
        "webcam": "dependency_missing",
        "geolocation": "approximate",
    })

    client._do_capabilities({})

    topic, payload = sent[0]
    assert topic == "capabilities"
    assert payload["features"]["mic"] is True
    assert payload["features"]["clipboard"] is True
    assert payload["features"]["battery"] is False
    assert payload["feature_status"]["screenshot"] == "permission_unverified"
    assert "SHARED_KEY" not in payload
