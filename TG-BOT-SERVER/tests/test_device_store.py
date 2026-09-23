"""Тесты DeviceStore: запись на диск только при изменениях, rename/remove."""

import json

from device_store import DeviceStore


def test_upsert_writes_only_on_change(tmp_path, monkeypatch):
    f = tmp_path / "dev.json"
    monkeypatch.setattr(DeviceStore, "FILE", f, raising=False)
    store = DeviceStore()

    store.upsert("a1", {"name": "Alpha", "os": "test"})
    assert json.loads(f.read_text(encoding="utf-8"))["a1"]["name"] == "Alpha"

    # Heartbeat без изменений — файл НЕ перезаписывается
    mtime_before = f.stat().st_mtime_ns
    store.upsert("a1", {"name": "Alpha", "os": "test"})
    assert f.stat().st_mtime_ns == mtime_before

    # Изменившиеся данные — файл перезаписан
    store.upsert("a1", {"name": "Alpha-2"})
    assert f.stat().st_mtime_ns != mtime_before
    assert json.loads(f.read_text(encoding="utf-8"))["a1"]["name"] == "Alpha-2"


def test_rename_and_remove(tmp_path, monkeypatch):
    f = tmp_path / "dev.json"
    monkeypatch.setattr(DeviceStore, "FILE", f, raising=False)
    store = DeviceStore()
    store.upsert("b2", {"name": "Old"})

    assert store.rename("b2", "New") is True
    assert store.get("b2")["name"] == "New"
    assert json.loads(f.read_text(encoding="utf-8"))["b2"]["name"] == "New"

    assert store.rename("missing", "X") is False

    assert store.remove("b2") is True
    assert store.get("b2") is None
    assert json.loads(f.read_text(encoding="utf-8")) == {}
    assert store.remove("b2") is False


def test_toggle_favorites(tmp_path, monkeypatch):
    f = tmp_path / "dev.json"
    monkeypatch.setattr(DeviceStore, "FILE", f, raising=False)
    store = DeviceStore()
    store.upsert("c3", {"name": "X"})

    assert store.get_favorites("c3") == []
    assert store.toggle_favorite("c3", "screenshot") is True
    assert store.get_favorites("c3") == ["screenshot"]
    assert store.toggle_favorite("c3", "screenshot") is False
    assert store.get_favorites("c3") == []
    assert store.toggle_favorite("missing", "screenshot") is False

