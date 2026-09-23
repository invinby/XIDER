"""Тесты настроек бота (JSON-хранилище флагов уведомлений)."""

import bot_settings as bs


def test_defaults_and_set(tmp_path, monkeypatch):
    monkeypatch.setattr(bs, "FILE", tmp_path / "settings.json", raising=False)
    s = bs.all_settings()
    assert s["notify_online"] is True
    assert s["notify_offline"] is True
    assert s["notify_battery_low"] is True

    bs.set_key("notify_online", False)
    assert bs.get("notify_online") is False
    assert bs.all_settings()["notify_online"] is False


def test_toggle(tmp_path, monkeypatch):
    monkeypatch.setattr(bs, "FILE", tmp_path / "settings.json", raising=False)
    assert bs.toggle("notify_battery_low") is False
    assert bs.toggle("notify_battery_low") is True
    assert bs.get("notify_battery_low") is True
