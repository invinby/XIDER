"""Permission settings can only open a fixed Windows privacy page."""
import xgent_wds as wds


def test_permission_settings_opens_fixed_camera_page_and_correlates_response(monkeypatch):
    opened = []
    responses = []
    monkeypatch.setattr(wds.os, "startfile", lambda uri: opened.append(uri), raising=False)
    client = wds.XgentClient.__new__(wds.XgentClient)
    monkeypatch.setattr(client, "_publish_response", lambda topic, payload: responses.append((topic, payload)))

    result = client._do_permission_settings({"permission": "camera", "id": "request-17"})

    assert opened == ["ms-settings:privacy-webcam"]
    assert result["ok"] is True
    assert result["permission"] == "camera"
    assert result["id"] == "request-17"
    assert responses == [("permission_settings", result)]


def test_permission_settings_rejects_arbitrary_or_unsupported_target(monkeypatch):
    opened = []
    client = wds.XgentClient.__new__(wds.XgentClient)
    monkeypatch.setattr(wds.os, "startfile", lambda uri: opened.append(uri), raising=False)
    monkeypatch.setattr(client, "_publish_response", lambda *_args: None)

    result = client._do_permission_settings({"permission": "ms-settings:startupapps"})

    assert opened == []
    assert result["ok"] is False
    assert result["permission"] == "unknown"
