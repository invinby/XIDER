"""Untrusted capability snapshots must never imply a permission grant."""
import copy
import asyncio
from types import SimpleNamespace

import pytest

import bot


def snapshot(state="needs_live_test", platform="macOS", **changes):
    result = {"schema": 1, "platform": platform, "checked_at": "2026-10-08T10:00:00+00:00",
              "items": [{"id": key, "state": state, "detail": "diagnostic detail",
                         "settings_uri": "https://evil.invalid"}
                        for key in ("screen_recording", "camera", "microphone", "location", "accessibility", "file_access")]}
    result.update(changes)
    return result


@pytest.mark.parametrize("state,label", [
    ("granted", "разрешено"), ("denied", "отказано"),
    ("not_determined", "решение ещё не принято"), ("unavailable", "недоступно"),
    ("needs_live_test", "нужна проверка действием"), ("unknown", "неизвестно"),
])
def test_each_state_is_rendered_with_timestamp_without_mutation(state, label):
    result = snapshot(state)
    before = copy.deepcopy(result)
    text = bot._format_capabilities(result)
    assert label in text
    assert "2026-10-08T10:00:00+00:00" in text
    assert result == before
    assert "evil.invalid" not in text


@pytest.mark.parametrize("changes", [
    {"schema": 2}, {"schema": True}, {"schema": "1"},
    {"platform": "Linux"}, {"checked_at": "invalid"},
    {"checked_at": "2026-10-08T10:00:00"}, {"items": None},
])
def test_invalid_envelope_fails_closed(changes):
    text = bot._format_capabilities(snapshot("granted", **changes))
    assert "неизвестно" in text
    assert "разрешено" not in text


def test_unknown_state_missing_and_unknown_items_fail_closed():
    result = snapshot("granted")
    result["items"] = [
        {"id": "camera", "state": "supported", "detail": "fake grant"},
        {"id": "root_control", "state": "granted", "detail": "injected"},
    ]
    text = bot._format_capabilities(result)
    assert "неизвестно" in text
    assert "разрешено" not in text
    assert "root_control" not in text and "injected" not in text


def test_duplicate_capability_cannot_win_by_order():
    result = snapshot("granted")
    result["items"].append({"id": "camera", "state": "denied", "detail": "duplicate"})
    text = bot._format_capabilities(result)
    camera_line = next(line for line in text.splitlines() if "Камера:" in line)
    assert "неизвестно" in camera_line and "разрешено" not in camera_line


def test_older_agent_is_unknown_even_with_true_legacy_features():
    text = bot._format_capabilities({"features": {"mic": True}, "feature_status": {"microphone": "supported"}})
    assert "неизвестно" in text
    assert "разрешено" not in text


@pytest.mark.parametrize("platform,route,instruction", [
    ("macOS", "x-apple.systempreferences:com.apple.preference.security?Privacy_Camera", "Конфиденциальность и безопасность"),
    ("Windows", "ms-settings:privacy-webcam", "Разрешить классическим приложениям"),
])
def test_settings_are_platform_allowlisted_and_owner_local(platform, route, instruction):
    text = bot._format_capabilities(snapshot("denied", platform))
    assert route in text and instruction in text
    assert "evil.invalid" not in text
    assert "на самом устройстве" in text


def test_limited_diagnostic_explains_permitted_trial_only():
    text = bot._format_capabilities(snapshot())
    assert "нужна проверка действием" in text
    assert "разрешённое пробное действие" in text
    assert "не выдаёт разрешения" in text


def test_recheck_uses_existing_callback_and_no_agent_uri_buttons():
    kb = bot._capabilities_keyboard()
    buttons = [button for row in kb.inline_keyboard for button in row]
    assert any(button.callback_data == "cmd:capabilities" and button.text for button in buttons)
    assert any(button.callback_data == "back:device" for button in buttons)
    assert all(button.url is None for button in buttons)


def test_capabilities_handler_displays_recheck_after_correlated_response(monkeypatch):
    rendered = []
    published = []

    async def answer(*_args, **_kwargs):
        pass

    async def allowed(*_args, **_kwargs):
        return True

    async def render(_cq, text, reply_markup=None):
        rendered.append((text, reply_markup))

    class Collector:
        async def wait_for(self, target, action, timeout, command_id):
            assert (target, action, command_id) == ("dev", "capabilities", "ticket")
            return snapshot()

    def publish(action, **kwargs):
        published.append((action, kwargs))
        return True, "ticket"

    monkeypatch.setattr(bot, "SESSION", {"target": "dev"})
    monkeypatch.setattr(bot, "_callback_result_access_or_report", allowed)
    monkeypatch.setattr(bot, "_replace_callback_message", render)
    monkeypatch.setattr(bot, "publish_tracked", publish)
    monkeypatch.setattr(bot, "capabilities_collector", Collector())
    asyncio.run(bot.on_cmd_capabilities(SimpleNamespace(answer=answer)))
    assert published == [("capabilities", {"_target": "dev"})]
    assert "2026-10-08T10:00:00+00:00" in rendered[0][0]
    callbacks = {button.callback_data for row in rendered[0][1].inline_keyboard for button in row}
    assert callbacks == {"cmd:capabilities", "back:device"}


def test_collector_replaces_stale_grant_when_new_envelope_has_no_commands(monkeypatch, tmp_path):
    from crypto import sign_message
    from device_store import DeviceStore

    monkeypatch.setattr(DeviceStore, "FILE", tmp_path / "devices.json")
    store = DeviceStore()
    monkeypatch.setattr(bot, "devices", store)
    monkeypatch.setattr(bot, "ENCRYPT_PAYLOAD", False)
    topic = f"{bot.MQTT_PREFIX}/dev/capabilities"
    bot.on_mqtt_message(topic, sign_message(snapshot("granted", type="capabilities", commands=["capabilities"])))
    assert store.get("dev")["capabilities"]["snapshot"]["items"][0]["state"] == "granted"
    bot.on_mqtt_message(topic, sign_message({"type": "capabilities", "schema": 9}))
    saved = store.get("dev")["capabilities"]["snapshot"]
    assert all(item["state"] == "unknown" for item in saved["items"])


def test_bounded_untrusted_details_cannot_break_html_or_message_limit():
    result = snapshot()
    for item in result["items"]:
        item["detail"] = "<b>" + "&" * 10000
    text = bot._format_capabilities(result)
    assert "&lt;b&gt;" in text
    assert "<b>&" not in text
    assert len(text) < 4096
