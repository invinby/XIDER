import asyncio
from types import SimpleNamespace

import pytest
import release_catalog


@pytest.fixture
def signed_keeper_release(signed_catalog_release):
    return signed_catalog_release(release_catalog.Release(
        "v4.2.0", (4, 2, 0), "fixture", "date", "notes",
        frozenset({"Guard-Keeper-Windows.zip", "Guard-Keeper-macOS.zip", "release-manifest.json"}),
    ))


@pytest.mark.parametrize("os_name", ["Windows 11", "macOS 27"])
@pytest.mark.parametrize("record,stamp,want,reason", [
    ({"version": "1.0-companion", "version_source": "guardian"}, 990, "installed", None),
    ({"version": "88.77.66"}, 990, "unknown", "missing_inventory"),
    ({"installed": False}, 990, "not_installed", None),
    ({}, 990, "unknown", "missing_inventory"),
    ({"version": "1.0-companion"}, 100, "unknown", "stale"),
    ({"version": "1.0-companion"}, 1010, "unknown", "stale"),
    ({"version": "1.0-companion", "offline": True}, 990, "unknown", "offline"),
    ({"version": "1.0-companion", "owner_removed": True}, 990, "unknown", "owner_removed"),
    ({"installed": True}, 990, "unknown", "missing_version"),
])
def test_keeper_requires_its_own_fresh_inventory(os_name, record, stamp, want, reason):
    import companion_status
    result = companion_status.component_state("keeper", os_name, record=record, checked_at=stamp, now=1000)
    assert result["state"] == want
    assert result["reason"] == reason
    assert result["version"] == ("1.0-companion" if want == "installed" else None)


@pytest.mark.parametrize("os_name", ["Windows 11", "macOS 27"])
def test_signed_package_does_not_prove_keeper_presence(os_name, signed_keeper_release):
    import companion_status
    result = companion_status.component_state("keeper", os_name, releases=[signed_keeper_release], now=1000)
    assert result["state"] == "unknown"
    assert result["version"] is None and result["available_version"] == "v4.2.0"
    absent = companion_status.component_state("keeper", os_name, record={"installed": False}, checked_at=990, releases=[signed_keeper_release], now=1000)
    assert absent["state"] == "available" and absent["version"] is None


@pytest.mark.parametrize("os_name", ["Windows 11", "macOS 27"])
def test_unsigned_keeper_asset_is_not_available(os_name):
    import companion_status
    release = release_catalog.Release("v4.2.0", (4, 2, 0), "fixture", "date", "notes", frozenset({"Guard-Keeper-Windows.zip", "Guard-Keeper-macOS.zip"}))
    result = companion_status.component_state("keeper", os_name, releases=[release], now=1000)
    assert result["available_version"] is None and result["state"] == "unknown"


def test_darwin_keeper_never_selects_a_windows_package(signed_catalog_release):
    import companion_status
    windows_release = signed_catalog_release(release_catalog.Release("v4.2.0", (4, 2, 0), "fixture", "date", "notes", frozenset({"Guard-Keeper-Windows.zip", "release-manifest.json"})))
    result = companion_status.component_state("keeper", "Darwin", releases=[windows_release], now=1000)
    assert result["component"] == "mac_keeper"
    assert result["available_version"] is None


@pytest.mark.parametrize("name", ["safari", "siri"])
@pytest.mark.parametrize("os_name,record,want,reason", [
    ("Windows 11", None, "unsupported", "unsupported_os"),
    ("Windows 11", {"version": "9.9.9"}, "unsupported", "unsupported_os"),
    ("Windows 11", {"installed": False}, "unsupported", "unsupported_os"),
    ("Windows 11", {"owner_removed": True}, "unsupported", "unsupported_os"),
    ("macOS 27", None, "unknown", "missing_inventory"),
    ("macOS 27", {"version": "9.9.9"}, "unknown", "missing_inventory"),
    ("macOS 27", {"installed": False}, "not_installed", None),
    ("macOS 27", {"owner_removed": True}, "unknown", "owner_removed"),
])
def test_companions_require_component_owned_inventory(name, os_name, record, want, reason, signed_keeper_release):
    import companion_status
    result = companion_status.component_state(name, os_name, record=record, checked_at=990, releases=[signed_keeper_release], now=1000)
    assert result["state"] == want and result["reason"] == reason
    assert result["version"] is None and result["available_version"] is None


def test_device_companion_status_preserves_only_valid_peer_observations():
    import companion_status

    layer = {
        "installed": True, "version_source": "safari", "version": "0.1.0",
        "state": "running", "pid": 123, "last_seen": 990,
        "observed_components": {
            "agent": {"state": "running", "freshness": "fresh"},
            "guardian": {"state": "error", "freshness": "stale"},
            "siri": {"state": "stopped", "freshness": "fresh"},
            "unknown": {"state": "running", "freshness": "fresh"},
        },
    }
    cleaned = companion_status.clean_layer_records({"safari": layer}, now=1000)
    info = {"os": "macOS 27", "companion_records": cleaned}
    safari = next(row for row in companion_status.device_companions(info, now=1000) if row["name"] == "Safari")

    assert safari["observed_components"] == {
        "agent": {"state": "running", "freshness": "fresh"},
        "guardian": {"state": "error", "freshness": "stale"},
        "siri": {"state": "stopped", "freshness": "fresh"},
    }
    import bot
    rendered = bot._companion_row(safari)
    assert "Agent" in rendered and "Guard Keeper" in rendered and "Siri" in rendered
    assert "устарел" in rendered


@pytest.mark.parametrize("name", ["keeper", "safari", "siri"])
def test_unsupported_os_is_explicit(name):
    import companion_status
    result = companion_status.component_state(name, "Linux", record={"version": "1.0"}, checked_at=990, now=1000)
    assert result["state"] == "unsupported" and result["version"] is None


@pytest.fixture
def ui(monkeypatch):
    import bot
    records, cards = {}, []
    monkeypatch.setattr(bot, "SESSION", {"target": "fixture"})
    monkeypatch.setattr(bot, "devices", SimpleNamespace(get=lambda _: records, upsert=lambda _, data: records.update(data)))
    monkeypatch.setattr(bot.time, "time", lambda: 1000)
    monkeypatch.setattr(bot.release_catalog.catalog, "cached", lambda: [])
    monkeypatch.setattr(bot, "get_user_role", lambda _: bot.Role.OWNER)
    monkeypatch.setattr(bot, "target_label", lambda _: "fixture")
    async def render(cq, text, **kwargs):
        cards.append((text, kwargs.get("reply_markup")))
    async def answer(*args, **kwargs):
        pass
    monkeypatch.setattr(bot, "_replace_callback_message", render)
    return bot, records, cards, SimpleNamespace(data="versions:device", from_user=SimpleNamespace(id=100), answer=answer)


@pytest.mark.parametrize("os_name,keeper_version", [("Windows 11", "1.0-windows"), ("macOS 27", "1.0")])
def test_version_page_separates_agent_and_companions(ui, os_name, keeper_version):
    bot, records, cards, cq = ui
    records.update(os=os_name, version="88.77.66", online=True, last_seen=990, guardian={"version": keeper_version, "version_source": "guardian"}, guardian_last_seen=990)
    asyncio.run(bot.on_versions_device(cq))
    text, markup = cards[-1]
    assert "88.77.66" in text
    keeper_row = next(line for line in text.splitlines() if "Guard Keeper:" in line)
    assert keeper_version in keeper_row and "установлен" in keeper_row and "88.77.66" not in keeper_row
    for name in ("Safari", "Siri"):
        row = next(line for line in text.splitlines() if f"{name}:" in line)
        expected = "не поддерживается" if os_name == "Windows 11" else "неизвестно"
        assert expected in row and "88.77.66" not in row
    callbacks = [button.callback_data for row in markup.inline_keyboard for button in row]
    assert callbacks == ["versions:list:agent:0", "versions:list:keeper:0", "back:device"]


@pytest.mark.parametrize("guardian,seen", [({}, 990), ({"version": "1.0", "version_source": "guardian"}, 100), ({"version": "1.0", "version_source": "guardian", "owner_removed": True}, 990), ({"version": "1.0", "version_source": "guardian", "offline": True}, 990)])
def test_keeper_context_never_uses_stale_or_missing_version(ui, guardian, seen):
    bot, records, cards, cq = ui
    records.update(os="macOS 27", version="88.77.66", online=False, last_seen=100, guardian=guardian, guardian_last_seen=seen)
    assert bot._version_context("keeper")[2] == "?"
    asyncio.run(bot.on_versions_device(cq))
    text, markup = cards[-1]
    keeper_row = next(line for line in text.splitlines() if "Guard Keeper:" in line)
    assert "неизвестно" in keeper_row and "88.77.66" not in keeper_row
    assert not any("install" in button.callback_data for row in markup.inline_keyboard for button in row)


def test_guardian_packet_never_falls_back_to_agent_version(ui, monkeypatch):
    bot, records, _, _ = ui
    monkeypatch.setattr(bot, "verify_message_detailed", lambda payload, **_kwargs: (payload, "ok"))
    monkeypatch.setattr(bot.fun_text_collector, "submit", lambda *args: None)
    bot.on_mqtt_message(f"{bot.MQTT_PREFIX}/fixture/guardian", {"type": "guardian", "device_id": "fixture", "ok": True, "version": "88.77.66"})
    assert records["guardian"]["version"] is None


def test_legacy_guardian_cache_with_unproven_version_remains_unknown(ui, monkeypatch, tmp_path):
    from device_store import DeviceStore
    bot, records, cards, cq = ui
    monkeypatch.setattr(DeviceStore, "FILE", tmp_path / "known_devices.json")
    stored = DeviceStore()
    stored.upsert("fixture", {"os": "macOS 27", "version": "88.77.66", "guardian": {"version": "88.77.66"}, "guardian_last_seen": 990})
    reloaded = DeviceStore()
    monkeypatch.setattr(bot, "devices", reloaded)
    assert bot._version_context("keeper")[2] == "?"


def test_fresh_guardian_packet_proves_own_version_even_when_worker_offline(ui, monkeypatch):
    bot, records, _, _ = ui
    records.update(os="macOS 27", version="88.77.66", online=False, last_seen=100)
    monkeypatch.setattr(bot, "verify_message_detailed", lambda payload, **_kwargs: (payload, "ok"))
    monkeypatch.setattr(bot.fun_text_collector, "submit", lambda *args: None)
    bot.on_mqtt_message(f"{bot.MQTT_PREFIX}/fixture/guardian", {"type": "guardian", "device_id": "fixture", "ok": True, "version": "88.77.66", "guardian": "1.0", "guardian_task_registered": False, "desired_running": False})
    assert records["guardian"]["version_source"] == "guardian"
    assert bot._version_context("keeper")[2] == "1.0"


@pytest.mark.parametrize("os_name,own_version", [("Windows 11", "1.0-windows"), ("macOS 27", "1.0")])
def test_signed_keeper_page_keeps_readonly_release_navigation(ui, monkeypatch, signed_keeper_release, os_name, own_version):
    bot, records, cards, cq = ui
    records.update(os=os_name, version="88.77.66", guardian={"version": own_version, "version_source": "guardian"}, guardian_last_seen=990)
    monkeypatch.setattr(bot.release_catalog.catalog, "list", lambda: [signed_keeper_release])
    cq.data = "versions:detail:keeper:v4.2.0"
    asyncio.run(bot.on_versions_detail(cq))
    text, markup = cards[-1]
    assert own_version in text and "88.77.66" not in text
    assert "Состояние установленной версии: installed" in text
    assert [button.callback_data for row in markup.inline_keyboard for button in row] == ["versions:list:keeper:0"]
