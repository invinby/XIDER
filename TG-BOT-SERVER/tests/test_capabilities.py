"""Untrusted capability snapshots must never imply a permission grant."""
import copy
import asyncio
import datetime
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


def test_shell_runner_diagnostic_never_turns_missing_or_malformed_fields_into_ready():
    ready = {
        "schema": 1, "protocol_state": "ready", "executor_state": "available",
        "runner_state": "available", "permission_state": "available",
    }
    text = bot._format_shell_runner_diagnostic(ready, link_state="fresh", authorization_state="allowed")
    assert "Протокол подтверждений: готов" in text
    assert "Модуль исполнения: доступно" in text
    assert "Связь: свежий ответ получен" in text
    assert "Права владельца на shell-действие: разрешены" in text
    malformed = dict(ready, protocol_state="invented", permission_state="available", runner_state="available")
    text = bot._format_shell_runner_diagnostic(malformed, link_state="unknown", authorization_state="denied")
    assert "Протокол подтверждений: неизвестно" in text
    assert "Связь: неизвестно" in text
    assert "Права владельца на shell-действие: нет" in text


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


@pytest.mark.parametrize("platform,instruction", [
    ("macOS", "Конфиденциальность и безопасность"),
    ("Windows", "Разрешить классическим приложениям"),
])
def test_settings_summary_uses_human_steps_without_raw_system_uris(platform, instruction):
    text = bot._format_capabilities(snapshot("denied", platform))
    assert instruction in text
    assert "ms-settings:" not in text
    assert "x-apple.systempreferences:" not in text
    assert "evil.invalid" not in text
    assert "на самом устройстве" in text


def test_limited_diagnostic_explains_permitted_trial_only():
    text = bot._format_capabilities(snapshot())
    assert "нужна проверка действием" in text
    assert "разрешённое пробное действие" in text
    assert "не выдаёт разрешения" in text


@pytest.mark.parametrize("platform", ("macOS", "Windows"))
@pytest.mark.parametrize("permission", (
    "screen_recording", "camera", "microphone", "location", "accessibility", "file_access",
))
def test_permission_help_exists_for_every_capability_and_never_claims_a_grant(platform, permission):
    text = bot._format_permission_help(platform, permission)

    assert isinstance(text, str) and text
    assert bot._SNAPSHOT_LABELS[permission] in text
    assert "не выдаёт разрешение" in text


def test_permission_help_is_platform_specific_and_does_not_invent_windows_screen_toggle():
    mac_camera = bot._format_permission_help("macOS", "camera")
    win_camera = bot._format_permission_help("Windows", "camera")
    win_screen = bot._format_permission_help("Windows", "screen_recording")

    assert "Системные настройки → Конфиденциальность и безопасность → Камера" in mac_camera
    assert "Параметры → Конфиденциальность и безопасность → Камера" in win_camera
    assert "Разрешить классическим приложениям" in win_camera
    assert "нет универсального переключателя" in win_screen
    assert "Системные настройки" not in win_screen


def test_macos_location_help_explains_first_grant_when_xider_is_missing_from_settings():
    text = bot._format_permission_help("macOS", "location")

    assert "Если XIDER ещё нет в списке" in text
    assert "./XIDER.app/Contents/MacOS/XGENT-MCS --location-setup" in text
    assert "корректно подписанным приложением" in text
    assert "Бот не может запросить согласие удалённо" in text


@pytest.mark.parametrize("platform,permission", [("Linux", "camera"), ("macOS", "shell"), ("Windows", "" )])
def test_permission_help_rejects_unknown_platform_or_capability(platform, permission):
    assert bot._format_permission_help(platform, permission) is None


def test_recheck_uses_existing_callback_and_no_agent_uri_buttons():
    kb = bot._capabilities_keyboard()
    buttons = [button for row in kb.inline_keyboard for button in row]
    assert any(button.callback_data == "cmd:capabilities" and button.text for button in buttons)
    assert any(button.callback_data == "back:device" for button in buttons)
    assert all(button.url is None for button in buttons)


def test_mac_capability_card_keeps_permission_settings_available_after_grant():
    result = snapshot("needs_live_test")
    result["commands"] = ["capabilities", "permission_settings"]
    result["items"] = [
        {**item, "state": "granted" if item["id"] == "camera" else item["state"]}
        for item in result["items"]
    ]

    kb = bot._capabilities_keyboard(result, user_id=bot.ADMIN_ID)
    buttons = [button for row in kb.inline_keyboard for button in row]
    callbacks = {button.callback_data for button in buttons}

    assert "permsettings:screen_recording" in callbacks
    assert "permsettings:microphone" in callbacks
    assert "permsettings:location" in callbacks
    assert "permsettings:accessibility" in callbacks
    assert "permsettings:file_access" in callbacks
    assert "permsettings:camera" in callbacks
    assert all(button.url is None for button in buttons)


def test_owner_can_reopen_permission_settings_after_grant_to_revoke_access():
    result = snapshot("granted", platform="Windows 11")
    result["commands"] = ["capabilities", "permission_settings"]
    result["items"] = [
        {**item, "state": "granted"} if item["id"] == "camera" else item
        for item in result["items"]
    ]

    kb = bot._capabilities_keyboard(result, user_id=bot.ADMIN_ID)
    buttons = [button for row in kb.inline_keyboard for button in row]

    assert any(button.callback_data == "permsettings:camera" for button in buttons)


def test_permission_card_has_one_action_per_permission_and_hides_raw_settings_uris():
    result = snapshot("needs_live_test", platform="Windows 11")
    result["commands"] = ["capabilities", "permission_settings"]

    text = bot._format_capabilities(result)
    kb = bot._capabilities_keyboard(result, user_id=bot.ADMIN_ID)
    callbacks = [button.callback_data for row in kb.inline_keyboard for button in row]

    assert "ms-settings:" not in text
    assert "x-apple.systempreferences:" not in text
    assert callbacks.count("permsettings:camera") == 1
    assert "permhelp:camera" not in callbacks
    assert "permhelp:screen_recording" in callbacks


def test_owner_capability_card_has_direct_settings_actions_and_other_roles_do_not(monkeypatch):
    result = snapshot()
    result["commands"] = ["capabilities", "permission_settings"]

    owner_kb = bot._capabilities_keyboard(result, user_id=bot.ADMIN_ID)
    owner_callbacks = {
        button.callback_data
        for row in owner_kb.inline_keyboard
        for button in row
    }
    assert "permsettings:camera" in owner_callbacks
    assert not any(callback.startswith("permhelp:") for callback in owner_callbacks)

    monkeypatch.setattr(bot, "get_user_role", lambda _uid: bot.Role.USER)
    user_kb = bot._capabilities_keyboard(result, user_id=200)
    user_callbacks = {
        button.callback_data
        for row in user_kb.inline_keyboard
        for button in row
    }
    assert not any(callback.startswith("permhelp:") for callback in user_callbacks)
    assert not any(callback.startswith("permsettings:") for callback in user_callbacks)


def test_mac_capability_card_hides_remote_settings_buttons_until_agent_supports_them():
    result = snapshot("needs_live_test")
    result["commands"] = ["capabilities"]

    kb = bot._capabilities_keyboard(result, user_id=bot.ADMIN_ID)
    callbacks = {
        button.callback_data
        for row in kb.inline_keyboard
        for button in row
    }

    assert not any(callback.startswith("permsettings:") for callback in callbacks)


def test_windows_capability_card_offers_allowlisted_settings_even_after_grant():
    result = snapshot("needs_live_test", platform="Windows 11")
    result["commands"] = ["capabilities", "permission_settings"]
    result["items"] = [
        {**item, "state": "granted" if item["id"] == "camera" else item["state"]}
        for item in result["items"]
    ]

    kb = bot._capabilities_keyboard(result, user_id=bot.ADMIN_ID)
    callbacks = {button.callback_data for row in kb.inline_keyboard for button in row}

    assert "permsettings:camera" in callbacks
    assert "permsettings:microphone" in callbacks
    assert "permsettings:location" in callbacks
    assert "permsettings:screen_recording" not in callbacks
    assert "permsettings:accessibility" not in callbacks
    assert "permsettings:file_access" not in callbacks


@pytest.mark.parametrize("style", ("xtech", "xperson", "xpikmi", "xtarped", "xcore", "xadam"))
def test_permission_settings_button_uses_neutral_copy_without_changing_xlex(style, monkeypatch):
    result = snapshot("denied")
    result["commands"] = ["capabilities", "permission_settings"]
    monkeypatch.setattr(
        bot.bot_settings,
        "get",
        lambda key, default=None: style if key == "ui_style" else default,
    )

    kb = bot._capabilities_keyboard(result, user_id=bot.ADMIN_ID)
    button = next(
        button
        for row in kb.inline_keyboard
        for button in row
        if button.callback_data == "permsettings:camera"
    )

    assert button.text == "⚙️ Разрешить в настройках: Камера"


def test_windows_permission_settings_dispatch_requires_owner_and_known_permission(monkeypatch):
    called = []

    async def answer(*_args, **_kwargs):
        pass

    async def simple_command(*args, **kwargs):
        called.append((args, kwargs))

    monkeypatch.setattr(bot, "SESSION", {"target": "win-1"})
    monkeypatch.setattr(bot.devices, "get", lambda _target: {
        "os": "Windows 11",
        "capabilities": {"commands": ["permission_settings"]},
    })
    monkeypatch.setattr(bot, "_device_action_still_allowed", lambda *_args: True)
    monkeypatch.setattr(bot, "simple_command", simple_command)
    cq = SimpleNamespace(
        answer=answer,
        from_user=SimpleNamespace(id=bot.ADMIN_ID),
        data="permsettings:camera",
    )

    asyncio.run(bot.on_permission_settings(cq))

    assert called == [((cq, "permission_settings", "⚙️", "Камера"), {
        "timeout": 15.0,
        "permission": "camera",
    })]


def test_remote_permission_settings_callback_dispatches_only_known_mac_permission(monkeypatch):
    called = []

    async def answer(*_args, **_kwargs):
        pass

    async def simple_command(*args, **kwargs):
        called.append((args, kwargs))

    monkeypatch.setattr(bot, "SESSION", {"target": "mac-1"})
    monkeypatch.setattr(bot.devices, "get", lambda _target: {
        "os": "macOS 27.0",
        "capabilities": {"commands": ["permission_settings"]},
    })
    monkeypatch.setattr(bot, "_device_action_still_allowed", lambda *_args: True)
    monkeypatch.setattr(bot, "simple_command", simple_command)
    cq = SimpleNamespace(
        answer=answer,
        from_user=SimpleNamespace(id=bot.ADMIN_ID),
        data="permsettings:camera",
    )

    asyncio.run(bot.on_permission_settings(cq))

    assert called == [((cq, "permission_settings", "⚙️", "Камера"), {
        "timeout": 100.0,
        "permission": "camera",
    })]


def test_remote_permission_settings_callback_rejects_untrusted_permission_value(monkeypatch):
    alerts = []
    called = []

    async def answer(*_args, **kwargs):
        alerts.append(kwargs)

    async def simple_command(*args, **kwargs):
        called.append((args, kwargs))

    monkeypatch.setattr(bot, "SESSION", {"target": "mac-1"})
    monkeypatch.setattr(bot.devices, "get", lambda _target: {"os": "macOS 27.0"})
    monkeypatch.setattr(bot, "simple_command", simple_command)
    cq = SimpleNamespace(
        answer=answer,
        from_user=SimpleNamespace(id=bot.ADMIN_ID),
        data="permsettings:file:///tmp/evil",
    )

    asyncio.run(bot.on_permission_settings(cq))

    assert called == []
    assert alerts and alerts[0].get("show_alert") is True


@pytest.mark.parametrize(("fresh_state", "expected_state"), [
    ("denied", "Камера: отказано"),
    ("granted", "Камера: разрешено"),
])
def test_permission_settings_success_offers_one_shot_fresh_check_bound_to_permission(
        monkeypatch, fresh_state, expected_state):
    rendered = []
    published = []
    alerts = []

    async def answer(*_args, **kwargs):
        alerts.append(kwargs)

    async def render(_cq, text, reply_markup=None, **_kwargs):
        rendered.append((text, reply_markup))

    async def simple_command(*_args, **_kwargs):
        return {"ok": True, "text": "Открываю раздел системных настроек."}

    class Collector:
        async def wait_for(self, target, action, timeout, command_id):
            assert (target, action, command_id) == ("mac-1", "capabilities", "fresh-ticket")
            return snapshot(fresh_state)

    monkeypatch.setattr(bot, "SESSION", {"target": "mac-1"})
    monkeypatch.setattr(bot.devices, "get", lambda _target: {
        "os": "macOS 27.0",
        "capabilities": {"commands": ["capabilities", "permission_settings"]},
    })
    monkeypatch.setattr(bot, "_device_action_still_allowed", lambda *_args: True)
    monkeypatch.setattr(bot, "simple_command", simple_command)
    monkeypatch.setattr(bot, "_replace_callback_message", render)
    monkeypatch.setattr(bot, "_callback_result_access_or_report", lambda *_args, **_kwargs: _true())
    monkeypatch.setattr(bot, "publish_tracked", lambda action, **kwargs: published.append((action, kwargs)) or (True, "fresh-ticket"))
    monkeypatch.setattr(bot, "capabilities_collector", Collector())

    async def exercise():
        await bot.on_permission_settings(SimpleNamespace(
            answer=answer,
            from_user=SimpleNamespace(id=bot.ADMIN_ID),
            data="permsettings:camera",
        ))
        assert len(rendered) == 1
        text, markup = rendered[0]
        assert "Открываю раздел системных настроек." in text
        check_button = next(
            button for row in markup.inline_keyboard for button in row
            if button.callback_data.startswith("permverify:")
        )
        await bot.on_permission_recheck(SimpleNamespace(
            answer=answer,
            from_user=SimpleNamespace(id=bot.ADMIN_ID),
            data=check_button.callback_data,
        ))
        await bot.on_permission_recheck(SimpleNamespace(
            answer=answer,
            from_user=SimpleNamespace(id=bot.ADMIN_ID),
            data=check_button.callback_data,
        ))

    async def _true():
        return True

    asyncio.run(exercise())

    assert published == [("capabilities", {"_target": "mac-1"})]
    assert expected_state in rendered[-1][0]
    assert "2026-10-08T10:00:00+00:00" in rendered[-1][0]
    assert any("show_alert" in kwargs for kwargs in alerts)


def test_permission_help_replaces_current_card_and_offers_allowlisted_settings_button(monkeypatch):
    rendered = []

    async def answer(*_args, **_kwargs):
        pass

    async def render(_cq, text, reply_markup=None, **_kwargs):
        rendered.append((text, reply_markup))

    monkeypatch.setattr(bot, "SESSION", {"target": "mac-1"})
    monkeypatch.setattr(bot.devices, "get", lambda _target: {
        "os": "macOS 27.0",
        "capabilities": {"commands": ["permission_settings"]},
    })
    monkeypatch.setattr(bot, "_replace_callback_message", render)
    cq = SimpleNamespace(
        answer=answer,
        from_user=SimpleNamespace(id=bot.ADMIN_ID),
        data="permhelp:camera",
    )

    asyncio.run(bot.on_permission_help(cq))

    assert len(rendered) == 1
    text, markup = rendered[0]
    assert "Системные настройки → Конфиденциальность и безопасность → Камера" in text
    callbacks = {button.callback_data for row in markup.inline_keyboard for button in row}
    assert "permsettings:camera" in callbacks
    assert "cmd:capabilities" in callbacks
    assert "back:device" in callbacks


def test_permission_help_rejects_unknown_callback_without_rendering(monkeypatch):
    alerts = []
    rendered = []

    async def answer(*_args, **kwargs):
        alerts.append(kwargs)

    async def render(*args, **kwargs):
        rendered.append((args, kwargs))

    monkeypatch.setattr(bot, "SESSION", {"target": "mac-1"})
    monkeypatch.setattr(bot.devices, "get", lambda _target: {"os": "macOS 27.0"})
    monkeypatch.setattr(bot, "_replace_callback_message", render)
    cq = SimpleNamespace(
        answer=answer,
        from_user=SimpleNamespace(id=bot.ADMIN_ID),
        data="permhelp:file:///tmp/evil",
    )

    asyncio.run(bot.on_permission_help(cq))

    assert alerts and alerts[0].get("show_alert") is True
    assert rendered == []


def test_capabilities_handler_displays_recheck_after_correlated_response(monkeypatch):
    rendered = []
    published = []

    async def answer(*_args, **_kwargs):
        pass

    async def allowed(*_args, **_kwargs):
        return True

    async def render(_cq, text, reply_markup=None, **_kwargs):
        rendered.append((text, reply_markup))

    class Collector:
        async def wait_for(self, target, action, timeout, command_id):
            assert (target, action, command_id) == ("dev", "capabilities", "ticket")
            result = snapshot()
            result["commands"] = ["capabilities", "permission_settings"]
            return result

    def publish(action, **kwargs):
        published.append((action, kwargs))
        return True, "ticket"

    monkeypatch.setattr(bot, "SESSION", {"target": "dev"})
    monkeypatch.setattr(bot, "_callback_result_access_or_report", allowed)
    monkeypatch.setattr(bot, "_replace_callback_message", render)
    monkeypatch.setattr(bot, "publish_tracked", publish)
    monkeypatch.setattr(bot, "capabilities_collector", Collector())
    asyncio.run(bot.on_cmd_capabilities(SimpleNamespace(answer=answer, from_user=SimpleNamespace(id=bot.ADMIN_ID), data="cmd:capabilities")))
    assert published == [("capabilities", {"_target": "dev"})]
    assert "2026-10-08T10:00:00+00:00" in rendered[0][0]
    callbacks = {button.callback_data for row in rendered[0][1].inline_keyboard for button in row}
    assert {"cmd:capabilities", "back:device"} <= callbacks
    assert "permsettings:camera" in callbacks
    assert not any(callback.startswith("permhelp:") for callback in callbacks)


def test_capabilities_timeout_keeps_retry_on_the_same_card(monkeypatch):
    rendered = []

    async def answer(*_args, **_kwargs):
        pass

    async def allowed(*_args, **_kwargs):
        return True

    async def render(_cq, text, reply_markup=None, **_kwargs):
        rendered.append((text, reply_markup))

    class Collector:
        async def wait_for(self, *_args, **_kwargs):
            return None

    monkeypatch.setattr(bot, "SESSION", {"target": "dev"})
    monkeypatch.setattr(bot, "_callback_result_access_or_report", allowed)
    monkeypatch.setattr(bot, "_replace_callback_message", render)
    monkeypatch.setattr(bot, "publish_tracked", lambda *_args, **_kwargs: (True, "ticket"))
    monkeypatch.setattr(bot, "capabilities_collector", Collector())

    asyncio.run(bot.on_cmd_capabilities(SimpleNamespace(
        answer=answer,
        from_user=SimpleNamespace(id=bot.ADMIN_ID),
        data="cmd:capabilities",
    )))

    assert len(rendered) == 1
    assert rendered[0][0] == bot._lex("device_no_response")
    callbacks = {
        button.callback_data
        for row in rendered[0][1].inline_keyboard
        for button in row
    }
    assert {"cmd:capabilities", "back:device"} <= callbacks


def test_sysinfo_renders_only_reported_timezone_and_locale_as_escaped_values():
    result = {
        "device_id": "mac-1",
        "cpu_percent": 3.5, "ram_used_gb": 1, "ram_total_gb": 8, "ram_percent": 12.5,
        "disk_used_gb": 2, "disk_total_gb": 100, "disk_percent": 2, "uptime": "2h",
        "regional_settings": {
            "schema": 1, "feature_id": "D01-F23", "device_id": "mac-1",
            "request_id": "0123456789ab", "source": "local_os_runtime_apis",
            "state": "supported", "checked_at": "2026-10-16T03:00:00+00:00",
            "data": {
                "timezone": {"state": "supported", "name": "Asia/Tashkent", "utc_offset_minutes": 300},
                "locale": {"state": "supported", "name": "ru_UZ"},
            },
        },
    }
    text = bot._format_sysinfo(result, "<Mac>", expected_request_id="0123456789ab")

    assert "&lt;Mac&gt;" in text
    assert "Asia/Tashkent" in text
    assert "UTC+05:00" in text
    assert "ru_UZ" in text
    assert "D01-F23" in text

    stale = bot._format_sysinfo(result, "Mac", expected_request_id="different-ticket")
    assert "Asia/Tashkent" not in stale
    assert "агент не передал отдельный снимок" in stale


def test_sysinfo_shows_explainable_resource_health_instead_of_an_opaque_score():
    result = {
        "device_id": "mac-1",
        "cpu_percent": 98.0,
        "ram_used_gb": 7.2,
        "ram_total_gb": 8.0,
        "ram_percent": 90.0,
        "disk_used_gb": 47.0,
        "disk_total_gb": 100.0,
        "disk_percent": 47.0,
        "uptime": "2h",
    }

    text = bot._format_sysinfo(result, "Mac", expected_request_id="fresh-ticket")

    assert "D01-F01" in text
    assert "CPU: критично — 98.0%" in text
    assert "короткая выборка" in text
    assert "<blockquote expandable>" in text
    assert "эвристика" in text.lower()
    assert "RAM: внимание — 90.0%" in text
    assert "Диск: норма — 47.0%" in text
    assert "единый балл не вычисляется" in text


def test_sysinfo_marks_missing_or_invalid_health_values_unknown():
    result = {
        "device_id": "mac-1",
        "cpu_percent": float("nan"),
        "ram_percent": 101,
        "disk_percent": None,
    }

    text = bot._format_sysinfo(result, "Mac", expected_request_id="fresh-ticket")

    assert "CPU: неизвестно" in text
    assert "RAM: неизвестно" in text
    assert "Диск: неизвестно" in text
    assert "корректный замер не получен" in text
    assert "nan%" not in text.lower()


def test_sysinfo_does_not_call_health_values_fresh_without_request_correlation():
    result = {
        "device_id": "mac-1",
        "cpu_percent": 1.0,
        "ram_percent": 2.0,
        "disk_percent": 3.0,
    }

    text = bot._format_sysinfo(result, "Mac")

    assert "D01-F01" in text
    assert "CPU: неизвестно" in text
    assert "коррелированный снимок не получен" in text


def test_sysinfo_uncertainty_distinguishes_measured_estimated_missing_and_stale(monkeypatch):
    now = 1_792_239_060
    checked_at = "2026-10-17T12:10:00+00:00"
    result = {
        "type": "sysinfo", "device_id": "mac-1", "os": "macOS 27",
        "cpu_percent": 12.5, "ram_percent": 40.0, "disk_percent": None,
        "metric_quality": {"cpu_percent": "estimated", "ram_percent": "measured"},
        "regional_settings": {
            "schema": 1, "feature_id": "D01-F23", "device_id": "mac-1",
            "request_id": "fresh-ticket", "source": "local_os_runtime_apis",
            "state": "supported", "checked_at": checked_at,
        },
    }
    current = bot._format_sysinfo_uncertainty(
        result, "Mac", expected_device_id="mac-1", expected_request_id="fresh-ticket", now=now,
    )

    assert "CPU: оценочное" in current
    assert "RAM: измерено (measured) — 40%" in current
    assert "Диск: нет данных" in current
    assert checked_at in current

    stale_result = copy.deepcopy(result)
    stale_result["regional_settings"]["checked_at"] = "2026-10-17T11:00:00+00:00"
    stale = bot._format_sysinfo_uncertainty(
        stale_result, "Mac", expected_device_id="mac-1", expected_request_id="fresh-ticket", now=now,
    )
    assert "CPU: устарело" in stale
    assert "12.5%" not in stale


def test_sysinfo_uncertainty_rejects_uncorrelated_or_malformed_provenance():
    result = {
        "type": "sysinfo", "device_id": "mac-1", "cpu_percent": 12.5,
        "ram_percent": 40.0, "disk_percent": 30.0,
        "regional_settings": {
            "schema": 1, "feature_id": "D01-F23", "device_id": "mac-1",
            "request_id": "other-ticket", "source": "local_os_runtime_apis",
            "state": "supported", "checked_at": "2026-10-17T12:10:00+00:00",
        },
    }

    text = bot._format_sysinfo_uncertainty(
        result, "Mac", expected_device_id="mac-1", expected_request_id="fresh-ticket",
    )

    assert "коррелированный замер не получен" in text
    assert "12.5%" not in text
    assert "неизвестно" in text


def test_sysinfo_uncertainty_does_not_infer_quality_for_omitted_quality_map_fields():
    result = {
        "type": "sysinfo", "device_id": "mac-1", "cpu_percent": 12.5,
        "ram_percent": 40.0, "disk_percent": 30.0,
        "metric_quality": {"ram_percent": "measured"},
        "regional_settings": {
            "schema": 1, "feature_id": "D01-F23", "device_id": "mac-1",
            "request_id": "fresh-ticket", "source": "local_os_runtime_apis",
            "state": "supported", "checked_at": "2026-10-17T12:10:00+00:00",
        },
    }

    text = bot._format_sysinfo_uncertainty(
        result, "Mac", expected_device_id="mac-1", expected_request_id="fresh-ticket",
        now=1_792_239_060,
    )

    assert "CPU: неизвестно — метка качества отсутствует" in text
    assert "RAM: измерено (measured) — 40%" in text
    assert "Диск: неизвестно — метка качества отсутствует" in text


def test_camera_quality_formatter_shows_only_correlated_metrics_and_explains_heuristic():
    now = int(datetime.datetime.now(datetime.timezone.utc).timestamp())
    result = {
        "type": "camera_quality_snapshot", "schema": "xider.camera-quality.v1",
        "feature_id": "D02-F13", "device_id": "mac-1", "request_id": "camera-ticket",
        "state": "supported", "source": "opencv_laplacian_local",
        "checked_at": datetime.datetime.fromtimestamp(now, datetime.timezone.utc).isoformat(),
        "data": {
            "width_px": 1280, "height_px": 720, "sharpness_variance": 123.4,
            "mean_luma": 110.0, "dark_clipped_percent": 1.0, "bright_clipped_percent": 3.0,
        },
    }

    text = bot._format_camera_quality(
        result, "Mac", expected_device_id="mac-1", expected_request_id="camera-ticket", now=now,
    )

    assert "D02-F13" in text
    assert "1280×720" in text
    assert "123.4" in text
    assert "110" in text
    assert "не являются гарантией резкости" in text
    assert "image" not in text.lower()


def test_camera_quality_formatter_hides_stale_and_uncorrelated_metrics():
    now = int(datetime.datetime.now(datetime.timezone.utc).timestamp())
    result = {
        "type": "camera_quality_snapshot", "schema": "xider.camera-quality.v1",
        "feature_id": "D02-F13", "device_id": "mac-1", "request_id": "camera-ticket",
        "state": "supported", "source": "opencv_laplacian_local",
        "checked_at": datetime.datetime.fromtimestamp(now - 600, datetime.timezone.utc).isoformat(),
        "data": {
            "width_px": 1280, "height_px": 720, "sharpness_variance": 123.4,
            "mean_luma": 110.0, "dark_clipped_percent": 1.0, "bright_clipped_percent": 3.0,
        },
    }

    stale = bot._format_camera_quality(
        result, "Mac", expected_device_id="mac-1", expected_request_id="camera-ticket", now=now,
    )
    uncorrelated = bot._format_camera_quality(
        result, "Mac", expected_device_id="other-device", expected_request_id="camera-ticket", now=now,
    )

    assert "устарел" in stale and "123.4" not in stale
    assert "не совпал" in uncorrelated and "123.4" not in uncorrelated


def test_cpu_history_renders_only_a_correlated_selected_period():
    result = {
        "type": "cpu_history", "schema": "xider.cpu-history.v1", "feature_id": "D01-F02",
        "device_id": "mac-1", "request_id": "cpu-ticket", "state": "supported",
        "source": "psutil.cpu_percent", "checked_at": "2026-10-16T08:00:00+00:00",
        "data": {"period_seconds": 15, "sample_interval_seconds": 5, "samples": [
            {"offset_seconds": 5, "percent": 10.0},
            {"offset_seconds": 10, "percent": 35.5},
            {"offset_seconds": 15, "percent": 80.0},
        ]},
    }

    text = bot._format_cpu_history(result, "Mac", expected_device_id="mac-1", expected_request_id="cpu-ticket")

    assert "D01-F02" in text
    assert "15 секунд" in text
    assert "10%" in text and "35.5%" in text and "80%" in text
    assert "\u043b\u043e\u043a\u0430\u043b\u044c\u043d\u044b\u0435 \u0438\u0437\u043c\u0435\u0440\u0435\u043d\u0438\u044f" in text.lower()


def test_cpu_history_hides_samples_for_stale_or_malformed_results():
    result = {
        "schema": "xider.cpu-history.v1", "feature_id": "D01-F02", "device_id": "mac-1",
        "request_id": "old-ticket", "state": "supported", "source": "psutil.cpu_percent",
        "data": {"period_seconds": 15, "sample_interval_seconds": 5,
                 "samples": [{"offset_seconds": 5, "percent": 99.0}]},
    }

    stale = bot._format_cpu_history(result, "Mac", expected_device_id="mac-1", expected_request_id="new-ticket")
    malformed = bot._format_cpu_history(
        {**result, "request_id": "new-ticket", "data": {"period_seconds": 15, "sample_interval_seconds": 5,
                                                           "samples": [{"offset_seconds": 5, "percent": 101}]}},
        "Mac", expected_device_id="mac-1", expected_request_id="new-ticket",
    )

    assert "неизвестно" in stale.lower()
    assert "99" not in stale
    assert "неизвестно" in malformed.lower()
    assert "101" not in malformed


def test_cpu_history_agent_support_is_unknown_until_a_command_list_is_advertised():
    assert bot._device_declares_command({"capabilities": {"commands": ["cpu_history"]}}, "cpu_history") is True
    assert bot._device_declares_command({"capabilities": {"commands": ["sysinfo"]}}, "cpu_history") is False
    assert bot._device_declares_command({"capabilities": {"schema": 1}}, "cpu_history") is None
    assert bot._device_declares_command({}, "cpu_history") is None


def test_memory_history_renders_only_a_correlated_bounded_ram_and_swap_series():
    result = {
        "type": "memory_history", "schema": "xider.memory-history.v1", "feature_id": "D01-F03",
        "device_id": "mac-1", "request_id": "memory-ticket", "state": "supported",
        "source": "psutil.virtual_memory+psutil.swap_memory", "checked_at": "2026-10-16T08:00:00+00:00",
        "data": {"period_seconds": 15, "sample_interval_seconds": 5, "swap_state": "supported", "samples": [
            {"offset_seconds": 5, "ram_percent": 24.0, "swap_percent": 10.0},
            {"offset_seconds": 10, "ram_percent": 36.5, "swap_percent": 20.0},
            {"offset_seconds": 15, "ram_percent": 48.0, "swap_percent": 30.0},
        ]},
    }

    text = bot._format_memory_history(result, "Mac", expected_device_id="mac-1", expected_request_id="memory-ticket")

    assert "D01-F03" in text and "RAM" in text and "swap" in text.lower()
    assert "36.5%" in text and "20%" in text
    assert "неизвестно" not in text.lower()


def test_memory_history_does_not_invent_swap_zero_or_render_stale_samples():
    result = {
        "type": "memory_history", "schema": "xider.memory-history.v1", "feature_id": "D01-F03",
        "device_id": "mac-1", "request_id": "memory-ticket", "state": "supported",
        "source": "psutil.virtual_memory+psutil.swap_memory", "checked_at": "2026-10-16T08:00:00+00:00",
        "data": {"period_seconds": 15, "sample_interval_seconds": 5, "swap_state": "unsupported", "samples": [
            {"offset_seconds": 5, "ram_percent": 24.0, "swap_percent": None},
            {"offset_seconds": 10, "ram_percent": 36.5, "swap_percent": None},
            {"offset_seconds": 15, "ram_percent": 48.0, "swap_percent": None},
        ]},
    }
    text = bot._format_memory_history(result, "Mac", expected_device_id="mac-1", expected_request_id="memory-ticket")
    stale = bot._format_memory_history(result, "Mac", expected_device_id="mac-1", expected_request_id="other-ticket")

    assert "swap" in text.lower() and "не поддерживается" in text.lower()
    assert "0%" not in text
    assert "неизвестно" in stale.lower()
    assert "48%" not in stale


def test_memory_history_keeps_ram_when_swap_series_is_partial_and_marks_swap_unknown():
    result = {
        "type": "memory_history", "schema": "xider.memory-history.v1", "feature_id": "D01-F03",
        "device_id": "mac-1", "request_id": "memory-ticket", "state": "supported",
        "source": "psutil.virtual_memory+psutil.swap_memory", "checked_at": "2026-10-16T08:00:00+00:00",
        "data": {"period_seconds": 15, "sample_interval_seconds": 5, "swap_state": "unknown", "samples": [
            {"offset_seconds": 5, "ram_percent": 24.0, "swap_percent": 10.0},
            {"offset_seconds": 10, "ram_percent": 36.5, "swap_percent": None},
            {"offset_seconds": 15, "ram_percent": 48.0, "swap_percent": 30.0},
        ]},
    }

    text = bot._format_memory_history(result, "Mac", expected_device_id="mac-1", expected_request_id="memory-ticket")

    assert "36.5%" in text
    assert "swap: неизвестно" in text.lower()
    assert "swap 10%" not in text and "swap 30%" not in text


def test_volume_history_renders_correlated_live_usage_and_measured_forecast():
    result = {
        "type": "volume_history", "schema": "xider.volume-history.v1", "feature_id": "D01-F04",
        "device_id": "win-1", "request_id": "volume-ticket", "state": "supported",
        "source": "psutil.disk_partitions+psutil.disk_usage", "checked_at": "2026-10-16T08:00:00+00:00",
        "data": {"history_minimum_span_seconds": 21600, "history_scope": "agent_process", "volumes": [{
            "mountpoint": "C:\\", "filesystem": "NTFS", "state": "supported", "total_bytes": 1_000_000,
            "used_bytes": 600_000, "free_bytes": 400_000, "used_percent": 60.0,
            "trend_state": "increasing", "observed_span_seconds": 28_800,
            "delta_used_bytes": 200_000, "used_bytes_per_hour": 25_000,
            "estimated_hours_to_full": 16.0, "history_scope": "agent_process",
        }]},
    }

    text = bot._format_volume_history(
        result, "Laptop", expected_device_id="win-1", expected_request_id="volume-ticket",
    )

    assert "D01-F04" in text and "C:\\" in text
    assert "60%" in text and "16.0 ч" in text
    assert "6 часов" in text and "перезапуска" in text.lower()


def test_volume_history_hides_stale_or_unreliable_forecast_values():
    result = {
        "type": "volume_history", "schema": "xider.volume-history.v1", "feature_id": "D01-F04",
        "device_id": "win-1", "request_id": "old-ticket", "state": "supported",
        "source": "psutil.disk_partitions+psutil.disk_usage", "checked_at": "2026-10-16T08:00:00+00:00",
        "data": {"history_minimum_span_seconds": 21600, "history_scope": "agent_process", "volumes": [{
            "mountpoint": "C:\\", "filesystem": "NTFS", "state": "supported", "total_bytes": 1_000_000,
            "used_bytes": 999_999, "free_bytes": 1, "used_percent": 100.0,
            "trend_state": "insufficient_history", "observed_span_seconds": None,
            "delta_used_bytes": None, "used_bytes_per_hour": None,
            "estimated_hours_to_full": None, "history_scope": "agent_process",
        }]},
    }

    stale = bot._format_volume_history(
        result, "Laptop", expected_device_id="win-1", expected_request_id="new-ticket",
    )
    malformed = bot._format_volume_history(
        {**result, "request_id": "new-ticket", "data": {**result["data"], "volumes": [{
            **result["data"]["volumes"][0], "estimated_hours_to_full": -1,
        }]}},
        "Laptop", expected_device_id="win-1", expected_request_id="new-ticket",
    )

    assert "неизвестно" in stale.lower() and "999999" not in stale
    assert "неизвестно" in malformed.lower() and "999999" not in malformed


def test_memory_pressure_renderer_preserves_platform_source_and_metric_meaning():
    mac = {
        "type": "memory_pressure_history", "schema": "xider.memory-pressure-history.v1",
        "feature_id": "D01-F05", "device_id": "mac-1", "request_id": "pressure-ticket",
        "state": "supported", "source": "macos_memory_pressure_utility_free_percent",
        "checked_at": "2026-10-16T08:00:00+00:00",
        "data": {"period_seconds": 15, "sample_interval_seconds": 5, "samples": [
            {"offset_seconds": 5, "state": "supported", "available_percent": 61.0},
            {"offset_seconds": 10, "state": "supported", "available_percent": 52.0},
            {"offset_seconds": 15, "state": "supported", "available_percent": 42.0},
        ]},
    }
    windows = {
        **mac, "device_id": "win-1", "source": "windows_memory_resource_notification",
        "data": {"period_seconds": 15, "sample_interval_seconds": 5, "samples": [
            {"offset_seconds": 5, "state": "high", "available_percent": None},
            {"offset_seconds": 10, "state": "intermediate", "available_percent": None},
            {"offset_seconds": 15, "state": "low", "available_percent": None},
        ]},
    }
    mac_text = bot._format_memory_pressure_history(
        mac, "Mac", expected_device_id="mac-1", expected_request_id="pressure-ticket",
    )
    win_text = bot._format_memory_pressure_history(
        windows, "Laptop", expected_device_id="win-1", expected_request_id="pressure-ticket",
    )

    assert "D01-F05" in mac_text and "61%" in mac_text and "42%" in mac_text
    assert "Activity Monitor" in mac_text and "free" in mac_text.lower()
    assert "high" in win_text.lower() and "low" in win_text.lower() and "промежуточ" in win_text.lower()


def test_memory_pressure_renderer_hides_stale_and_malformed_samples():
    result = {
        "type": "memory_pressure_history", "schema": "xider.memory-pressure-history.v1",
        "feature_id": "D01-F05", "device_id": "mac-1", "request_id": "old-ticket",
        "state": "supported", "source": "macos_memory_pressure_utility_free_percent",
        "checked_at": "2026-10-16T08:00:00+00:00",
        "data": {"period_seconds": 15, "sample_interval_seconds": 5, "samples": [
            {"offset_seconds": 5, "state": "supported", "available_percent": 61.0},
            {"offset_seconds": 10, "state": "supported", "available_percent": 52.0},
            {"offset_seconds": 15, "state": "supported", "available_percent": 42.0},
        ]},
    }
    stale = bot._format_memory_pressure_history(
        result, "Mac", expected_device_id="mac-1", expected_request_id="new-ticket",
    )
    malformed = bot._format_memory_pressure_history(
        {**result, "request_id": "new-ticket", "data": {**result["data"], "samples": [
            *result["data"]["samples"][:2], {**result["data"]["samples"][2], "available_percent": 1000},
        ]}}, "Mac", expected_device_id="mac-1", expected_request_id="new-ticket",
    )

    assert "неизвестно" in stale.lower() and "61%" not in stale
    assert "неизвестно" in malformed.lower() and "42%" not in malformed


def test_thermal_snapshot_renderer_distinguishes_sensor_data_and_unsupported_platform():
    supported = {
        "type": "thermal_snapshot", "schema": "xider.thermal-snapshot.v1", "feature_id": "D01-F06",
        "device_id": "win-1", "request_id": "thermal-ticket", "state": "supported",
        "source": "windows_wmi_MSAcpi_ThermalZoneTemperature", "checked_at": "2026-10-16T08:00:00+00:00",
        "data": {"sensors": [
            {"label": "Thermal zone 1", "temperature_c": 60.1, "quality": "native_provider"},
            {"label": "Thermal zone 2", "temperature_c": 37.1, "quality": "native_provider"},
        ]},
    }
    unsupported = {
        **supported, "device_id": "mac-1", "state": "unsupported",
        "source": "macos_public_temperature_sensor_api_unavailable", "data": {"sensors": []},
    }

    text = bot._format_thermal_snapshot(
        supported, "Laptop", expected_device_id="win-1", expected_request_id="thermal-ticket",
    )
    unsupported_text = bot._format_thermal_snapshot(
        unsupported, "Mac", expected_device_id="mac-1", expected_request_id="thermal-ticket",
    )
    stale = bot._format_thermal_snapshot(
        supported, "Laptop", expected_device_id="win-1", expected_request_id="other-ticket",
    )

    assert "60.1°C" in text and "37.1°C" in text and "WMI" in text
    assert "не поддерживаются" in unsupported_text.lower() and "0°C" not in unsupported_text
    assert "неизвестно" in stale.lower() and "60.1°C" not in stale


def test_battery_health_renderer_separates_wear_from_charge_and_rejects_bad_data():
    result = {
        "type": "battery_health_snapshot", "schema": "xider.battery-health.v1",
        "feature_id": "D01-F07", "device_id": "win-1", "request_id": "battery-ticket",
        "state": "supported", "source": "windows_wmi_battery_capacity",
        "checked_at": "2026-10-16T08:00:00+00:00", "data": {"batteries": [{
            "label": "Battery 1", "design_capacity_mwh": 50000,
            "full_charge_capacity_mwh": 42000, "health_percent": 84.0,
            "quality": "native_provider",
        }]},
    }

    text = bot._format_battery_health_snapshot(
        result, "Laptop", expected_device_id="win-1", expected_request_id="battery-ticket",
    )
    unknown = bot._format_battery_health_snapshot(
        {**result, "state": "unknown", "data": None}, "Laptop",
        expected_device_id="win-1", expected_request_id="battery-ticket",
    )
    stale = bot._format_battery_health_snapshot(
        result, "Laptop", expected_device_id="win-2", expected_request_id="battery-ticket",
    )

    assert "84.0%" in text and "50 000" in text and "42 000" in text
    assert "не текущий заряд" in text.lower()
    assert "неизвестно" in unknown.lower() and "0%" not in unknown
    assert "неизвестно" in stale.lower()


def test_battery_cycle_renderer_reports_only_correlated_provider_counts():
    result = {
        "type": "battery_cycle_snapshot", "schema": "xider.battery-cycles.v1",
        "feature_id": "D01-F08", "device_id": "win-1", "request_id": "cycles-ticket",
        "state": "supported", "source": "windows_wmi_battery_cycle_count",
        "checked_at": "2026-10-16T08:00:00+00:00",
        "data": {"batteries": [{"label": "Battery 1", "cycle_count": 321, "quality": "native_provider"}]},
    }

    text = bot._format_battery_cycle_snapshot(
        result, "Laptop", expected_device_id="win-1", expected_request_id="cycles-ticket",
    )
    bad_zero = bot._format_battery_cycle_snapshot(
        {**result, "data": {"batteries": [{"label": "Battery 1", "cycle_count": 0, "quality": "native_provider"}]}},
        "Laptop", expected_device_id="win-1", expected_request_id="cycles-ticket",
    )
    stale = bot._format_battery_cycle_snapshot(
        result, "Laptop", expected_device_id="mac-1", expected_request_id="cycles-ticket",
    )

    assert "321" in text and "не текущий заряд" in text.lower()
    assert "неизвестно" in bad_zero.lower()
    assert "неизвестно" in stale.lower()


def test_energy_usage_renderer_shows_only_correlated_estimates_and_redacts_paths():
    result = {
        "type": "energy_usage_snapshot", "schema": "xider.energy-usage.v1", "feature_id": "D01-F09",
        "device_id": "win-1", "request_id": "energy-ticket", "state": "supported",
        "source": "windows_srum_energy_estimation", "checked_at": "2026-10-16T08:00:00+00:00",
        "data": {"unit": "mJ", "apps": [
            {"name": "browser", "energy_mj": 150, "quality": "estimated"},
            {"name": "editor", "energy_mj": 30, "quality": "estimated"},
        ]},
    }
    text = bot._format_energy_usage_snapshot(
        result, "Laptop", expected_device_id="win-1", expected_request_id="energy-ticket",
    )
    stale = bot._format_energy_usage_snapshot(
        result, "Laptop", expected_device_id="win-2", expected_request_id="energy-ticket",
    )
    unsupported = bot._format_energy_usage_snapshot(
        {**result, "state": "unsupported", "source": "macos_public_per_app_energy_api_unavailable",
         "data": {"unit": None, "apps": []}},
        "Mac", expected_device_id="win-1", expected_request_id="energy-ticket",
    )
    malicious = bot._format_energy_usage_snapshot(
        {**result, "data": {"unit": "mJ", "apps": [
            {"name": "<script>", "energy_mj": 10, "quality": "estimated"},
        ]}},
        "Laptop", expected_device_id="win-1", expected_request_id="energy-ticket",
    )

    assert "browser" in text and "150 mJ" in text and "оцен" in text.lower()
    assert "не точное" in text.lower() or "не измерение" in text.lower()
    assert "неизвестно" in stale.lower() and "150" not in stale
    assert "agent" in unsupported.lower() or "агент" in unsupported.lower()
    assert "public" in unsupported.lower() or "публичный" in unsupported.lower()
    assert "<script>" not in malicious and "&lt;script&gt;" in malicious


def test_sleep_wake_renderer_accepts_only_correlated_recent_allowlisted_events():
    result = {
        "type": "sleep_wake_history_snapshot", "schema": "xider.sleep-wake-history.v1",
        "feature_id": "D01-F10", "device_id": "mac-1", "request_id": "sleep-ticket",
        "state": "supported", "source": "macos_pmset_log", "checked_at": "2026-10-16T11:59:00+00:00",
        "data": {"history_days": 7, "events": [
            {"kind": "sleep", "timestamp": "2026-10-16T10:00:00+00:00", "private": "must drop"},
            {"kind": "wake", "timestamp": "2026-10-16T11:00:00+00:00"},
        ]},
    }

    text = bot._format_sleep_wake_history_snapshot(
        result, "Mac", expected_device_id="mac-1", expected_request_id="sleep-ticket",
    )
    stale = bot._format_sleep_wake_history_snapshot(
        {**result, "device_id": "other-device"}, "Mac",
        expected_device_id="mac-1", expected_request_id="sleep-ticket",
    )
    malformed = bot._format_sleep_wake_history_snapshot(
        {**result, "data": {"history_days": 7, "events": [{"kind": "launch", "timestamp": "2026-10-16T10:00:00+00:00"}]}},
        "Mac", expected_device_id="mac-1", expected_request_id="sleep-ticket",
    )
    unsupported = bot._format_sleep_wake_history_snapshot(
        {**result, "state": "unsupported", "source": "windows_system_power_events",
         "data": {"history_days": 7, "events": []}},
        "Windows", expected_device_id="mac-1", expected_request_id="sleep-ticket",
    )

    assert "сон" in text.lower() and "пробуж" in text.lower()
    assert "private" not in text and "must drop" not in text
    assert "неизвестно" in stale.lower() and "неизвестно" in malformed.lower()
    assert "недоступно" in unsupported.lower() or "unsupported" in unsupported.lower()


def test_startup_duration_renderer_uses_measured_boot_duration_not_uptime():
    result = {
        "type": "startup_duration_snapshot", "schema": "xider.startup-duration.v1",
        "feature_id": "D01-F11", "device_id": "win-1", "request_id": "boot-ticket",
        "state": "supported", "source": "windows_diagnostics_performance_event_100",
        "checked_at": "2026-10-16T11:59:00+00:00",
        "data": {"duration_ms": 42_500, "completed_at": "2026-10-16T11:30:00+00:00", "extra": "drop"},
    }
    text = bot._format_startup_duration_snapshot(result, "Laptop", expected_device_id="win-1", expected_request_id="boot-ticket")
    stale = bot._format_startup_duration_snapshot({**result, "request_id": "old"}, "Laptop", expected_device_id="win-1", expected_request_id="boot-ticket")
    too_long = bot._format_startup_duration_snapshot({**result, "data": {"duration_ms": 31 * 60 * 1000, "completed_at": "2026-10-16T11:30:00+00:00"}}, "Laptop", expected_device_id="win-1", expected_request_id="boot-ticket")
    unsupported = bot._format_startup_duration_snapshot({**result, "state": "unsupported", "source": "macos_boot_duration_api_unavailable", "data": {"duration_ms": None, "completed_at": None}}, "Mac", expected_device_id="win-1", expected_request_id="boot-ticket")

    assert "42" in text and "сек" in text.lower() and "это не uptime" in text.lower()
    assert "extra" not in text
    assert "неизвестно" in stale.lower() and "неизвестно" in too_long.lower()
    assert "недоступно" in unsupported.lower()


def test_pending_restart_renderer_requires_fresh_correlation_and_fixed_reason_ids():
    result = {
        "type": "pending_restart_snapshot", "schema": "xider.pending-restart.v1",
        "feature_id": "D01-F12", "device_id": "win-1", "request_id": "restart-ticket",
        "state": "supported", "source": "windows_pending_restart_registry",
        "checked_at": "2026-10-16T11:59:00+00:00",
        "data": {"pending": True, "reasons": ["windows_update"], "completeness": "complete"},
    }
    text = bot._format_pending_restart_snapshot(
        result, "Laptop", expected_device_id="win-1", expected_request_id="restart-ticket",
    )
    stale = bot._format_pending_restart_snapshot(
        {**result, "request_id": "old"}, "Laptop",
        expected_device_id="win-1", expected_request_id="restart-ticket",
    )
    raw = bot._format_pending_restart_snapshot(
        {**result, "data": {"pending": True, "reasons": ["C:\\private\\secret"], "completeness": "complete"}},
        "Laptop", expected_device_id="win-1", expected_request_id="restart-ticket",
    )
    unsupported = bot._format_pending_restart_snapshot(
        {**result, "state": "unsupported", "source": "macos_pending_restart_flag_unavailable",
         "data": {"pending": None, "reasons": [], "completeness": "unknown"}},
        "Mac", expected_device_id="win-1", expected_request_id="restart-ticket",
    )

    assert "перезагруз" in text.lower() and "Windows Update" in text
    assert "неизвестно" in stale.lower() and "windows_update" not in stale
    assert "C:\\private" not in raw and "неизвестно" in raw.lower()
    assert "недоступно" in unsupported.lower()


def test_os_support_renderer_is_dated_source_labelled_and_correlation_bound():
    import datetime
    checked = datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat()
    result = {
        "type": "os_support_snapshot", "schema": "xider.os-support.v1", "feature_id": "D01-F13",
        "device_id": "win-1", "request_id": "os-ticket", "state": "supported",
        "source": "windows_current_version_registry", "checked_at": checked,
        "data": {"family": "windows", "release": "11|24H2", "edition": "Home"},
    }
    text = bot._format_os_support_snapshot(
        result, "Laptop", expected_device_id="win-1", expected_request_id="os-ticket",
    )
    stale = bot._format_os_support_snapshot(
        {**result, "device_id": "other"}, "Laptop", expected_device_id="win-1", expected_request_id="os-ticket",
    )
    unsupported = bot._format_os_support_snapshot(
        {**result, "state": "unsupported", "source": "macos_platform_version",
         "data": {"family": "macos", "release": "15.7.9", "edition": None}},
        "Mac", expected_device_id="win-1", expected_request_id="os-ticket",
    )

    assert "24H2" in text and "2026-10-13" in text and "2026-10-16" in text
    assert "Microsoft Lifecycle" in text
    assert "неизвестно" in stale.lower()
    assert "недоступно" in unsupported.lower()


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


def test_camera_series_response_dispatches_only_to_its_correlated_collector(monkeypatch):
    from crypto import sign_message

    monkeypatch.setattr(bot, "ENCRYPT_PAYLOAD", False)
    submitted = []
    monkeypatch.setattr(bot.camera_series_collector, "submit", lambda device_id, payload: submitted.append((device_id, payload)))
    payload = {
        "type": "camera_series_snapshot", "schema": "xider.camera-series.v1",
        "feature_id": "D02-F14", "device_id": "dev", "request_id": "series-ticket",
    }

    envelope = sign_message(payload)
    bot.on_mqtt_message(f"{bot.MQTT_PREFIX}/dev/camera_series", envelope)

    assert len(submitted) == 1
    assert submitted[0][0] == "dev"
    assert {key: value for key, value in submitted[0][1].items() if key in payload} == payload


def test_bounded_untrusted_details_cannot_break_html_or_message_limit():
    result = snapshot()
    for item in result["items"]:
        item["detail"] = "<b>" + "&" * 10000
    text = bot._format_capabilities(result)
    assert "&lt;b&gt;" in text
    assert "<b>&" not in text
    assert len(text) < 4096
