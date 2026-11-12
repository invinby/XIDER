import xgent_wds as wds


def test_storage_smart_projects_available_health_counters(monkeypatch):
    calls = []
    monkeypatch.setattr(wds, "DEVICE_ID", "win-storage")
    monkeypatch.setattr(wds, "_run_without_console", lambda runner, args, **kwargs: (
        calls.append((runner, args, kwargs)) or '{"FriendlyName":"Disk A","MediaType":"SSD","Health":"Healthy","Temp":41,"Wear":7}'
    ))
    client = object.__new__(wds.XgentClient)
    published = []
    client._publish_response = lambda topic, payload: published.append((topic, dict(payload)))

    client._do_storage_smart({})

    assert len(calls) == 1
    assert calls[0][1][:3] == ["powershell", "-NoProfile", "-Command"]
    assert calls[0][2]["timeout"] == 20
    assert published == [("storage_smart", {
        "type": "storage_smart", "device_id": "win-storage", "ok": True,
        "text": "🩺 SMART и здоровье накопителей:\n• Disk A [SSD] — Healthy, 41°C, износ 7%",
    })]


def test_storage_smart_treats_no_physical_disks_as_success(monkeypatch):
    monkeypatch.setattr(wds, "_run_without_console", lambda *_args, **_kwargs: "")
    client = object.__new__(wds.XgentClient)
    published = []
    client._publish_response = lambda topic, payload: published.append((topic, dict(payload)))

    client._do_storage_smart({})

    assert published[0][0] == "storage_smart"
    assert published[0][1]["ok"] is True
    assert published[0][1]["text"] == "🩺 Накопители не найдены."
