"""Offline checks for the TARPED catalogue, handbook and text styles."""

import asyncio
from types import SimpleNamespace

import bot
import info_book
import release_catalog as ledger
import text_store
import xlex


def test_six_voices_are_complete_and_keep_confirmations_explicit():
    xlex.validate()
    assert len(xlex.STYLES) == 6
    for style in xlex.STYLES:
        text = xlex.render("danger_confirm", style, action="выключить ПК")
        assert "выключить ПК" in text
        assert text_store.get_for_style("start_owner", style)


def test_pikmi_voice_is_distinct_but_keeps_dangerous_action_clear():
    assert "🎀" in xlex.render("start_owner", "xpikmi")
    assert "устройствечки" in xlex.render("devices_button", "xpikmi")
    text = xlex.render("danger_confirm", "xpikmi", action="выключить ПК")
    assert "выключить ПК" in text
    assert "изменит состояние" in text
    for label in xlex.NAV["xpikmi"].values():
        assert len(label) <= 64


def test_current_and_legacy_style_ids_normalize_to_named_voices():
    assert xlex.STYLES == ("xtech", "xperson", "xpikmi", "xtarped", "xcore", "xadam")
    assert xlex.normalize_style("technical") == "xtech"
    assert xlex.normalize_style("xtexbo") == "xtech"
    assert xlex.normalize_style("conversational") == "xtarped"
    assert xlex.normalize_style("xplain") == "xtarped"
    assert xlex.normalize_style("xnoir") == "xcore"


def test_saved_copy_from_old_style_id_is_not_lost(tmp_path, monkeypatch):
    monkeypatch.setattr(text_store, "FILE", tmp_path / "texts.json")
    (tmp_path / "texts.json").write_text(
        '{"xplain_start_owner":"Старый сохранённый текст"}', encoding="utf-8"
    )
    assert text_store.get_for_style("start_owner", "xtarped") == "Старый сохранённый текст"


def test_legacy_voice_and_owner_copy_migration(tmp_path, monkeypatch):
    monkeypatch.setattr(text_store, "FILE", tmp_path / "texts.json")
    text_store.set_text("custom_start_owner", "Мой старый текст")
    assert text_store.get_for_style("start_owner", "custom") == "Мой старый текст"
    text_store.set_text("xperson_start_owner", "Мой новый текст")
    assert text_store.get_for_style("start_owner", "custom") == "Мой новый текст"


def test_release_catalog_requires_a_real_component_asset():
    rows = [
        {"tag_name": "v4.0.0", "name": "TARPED", "body": "Changes", "assets": [{"name": "XGENT-WDS.exe"}]},
        {"tag_name": "v3.3.8", "name": "Old", "assets": [{"name": "XGENT-MCS-macos-bundle.zip"}]},
        {"tag_name": "v4.1.0", "draft": True, "assets": [{"name": "Guard-Keeper-Windows.zip"}]},
        {"tag_name": "random", "assets": []},
    ]
    releases = ledger.parse_releases(rows)
    assert [item.tag for item in releases] == ["v4.0.0", "v3.3.8"]
    assert releases[0].has_package("windows_agent")
    assert not releases[0].has_package("mac_agent")
    assert not releases[0].has_package("windows_keeper")
    assert ledger.newest_with_package(releases, "mac_agent").tag == "v3.3.8"
    assert ledger.is_older("3.3.8", releases[0])
    assert not ledger.is_older("4.0.0", releases[0])


def test_release_catalog_cache_does_not_fetch_on_every_open():
    calls = []

    def loader():
        calls.append(1)
        # Реальный опубликованный релиз содержит asset; так fixture повторяет
        # ответ GitHub, а не пустой черновой объект.
        return [{"tag_name": "v3.3.8", "assets": [{"name": "XGENT-WDS.exe"}]}]

    catalog = ledger.Catalog(loader)
    assert catalog.list()[0].tag == "v3.3.8"
    assert catalog.list()[0].tag == "v3.3.8"
    assert len(calls) == 1


def test_book_chapters_fit_telegram_and_device_menu_links_to_versions(monkeypatch):
    slugs = [slug for slug, _, _ in info_book.CHAPTERS]
    assert len(slugs) == len(set(slugs))
    assert len(slugs) >= 7
    for slug in slugs:
        _, _, body = info_book.chapter(slug)
        assert len(body) < 3500

    class FakeDevices:
        def get(self, device_id):
            return {"os": "macOS", "version": "3.3.8", "guardian": {"version": "1.0"}}

        def get_favorites(self, device_id):
            return []

    monkeypatch.setattr(bot, "devices", FakeDevices())
    markup = bot.device_menu("mac1")
    buttons = [button for row in markup.inline_keyboard for button in row]
    assert any(button.callback_data == "versions:device" and "3.3.8" in button.text for button in buttons)


def test_all_six_voices_reach_guest_and_user_menus(monkeypatch):
    for style in xlex.STYLES:
        monkeypatch.setattr(
            bot.bot_settings, "get",
            lambda key, default=None, selected=style: selected if key == "ui_style" else default,
        )
        for role, callback, copy_key in (
            (bot.Role.USER, "menu:devices", "my_devices_button"),
            (bot.Role.GUEST, "menu:guest_devices", "guest_devices_button"),
        ):
            monkeypatch.setattr(bot, "get_user_role", lambda user_id, selected=role: selected)
            markup = bot.main_menu(123)
            labels = {
                button.callback_data: button.text
                for row in markup.inline_keyboard for button in row
            }
            assert labels[callback] == xlex.render(copy_key, style)
            assert labels["menu:server"] == xlex.render("server_overview_button", style)
        assert "guest" in xlex.render("role_label", style, role="guest")
        assert "1/2" in xlex.render("online_label", style, online="1", total="2")


def test_style_switch_previews_saved_copy_and_selected_main_menu(monkeypatch):
    selected = {"style": "xtech"}
    shown = {}

    monkeypatch.setattr(
        bot.bot_settings, "get",
        lambda key, default=None: selected["style"] if key == "ui_style" else default,
    )
    monkeypatch.setattr(
        bot.bot_settings, "set_key",
        lambda key, value: selected.__setitem__("style", value),
    )
    monkeypatch.setattr(bot.access_store, "append_audit", lambda *args, **kwargs: None)
    monkeypatch.setattr(bot.text_store, "get_for_style", lambda key, style: "Мой сохранённый текст")
    monkeypatch.setattr(bot, "get_user_role", lambda user_id: bot.Role.OWNER)

    class Message:
        async def edit_text(self, text, *, reply_markup):
            shown["text"] = text
            shown["markup"] = reply_markup

    class Callback:
        data = "admin:style:set:xpikmi"
        from_user = SimpleNamespace(id=bot.ADMIN_ID)
        message = Message()

        async def answer(self, *args, **kwargs):
            pass

    asyncio.run(bot.on_admin_style_set(Callback()))
    assert selected["style"] == "xpikmi"
    assert "Мой сохранённый текст" in shown["text"]
    labels = [button.text for row in shown["markup"].inline_keyboard for button in row]
    assert xlex.render("devices_button", "xpikmi") in labels


def test_server_menu_uses_all_six_voices_without_changing_callbacks(monkeypatch):
    expected = None
    monkeypatch.setattr(bot, "get_user_role", lambda user_id: bot.Role.OWNER)
    for style in xlex.STYLES:
        monkeypatch.setattr(
            bot.bot_settings, "get",
            lambda key, default=None, selected=style: selected if key == "ui_style" else default,
        )
        markup = bot.server_menu(123)
        buttons = {
            button.callback_data: button.text
            for row in markup.inline_keyboard for button in row
        }
        assert buttons["server:status"] == xlex.render("server_status", style)
        assert buttons["server:restart"] == xlex.render("server_restart", style)
        assert "Перезапустить" in buttons["server:restart"]
        assert "Откатить" in buttons["server:rollback"] or "Вернуть" in buttons["server:rollback"]
        assert all(len(label) <= 64 for label in buttons.values())
        if expected is None:
            expected = set(buttons)
        else:
            assert set(buttons) == expected


def test_server_cards_follow_all_six_voices_and_escape_runtime_values(monkeypatch):
    overview_cards = set()
    confirmation_cards = set()
    metrics_cards = set()
    terminal_cards = set()
    monkeypatch.setattr(bot, "XIDER_BUILD_CODE", "<fixture&build>")
    for style in xlex.STYLES:
        monkeypatch.setattr(
            bot.bot_settings, "get",
            lambda key, default=None, selected=style: selected if key == "ui_style" else default,
        )
        overview = bot.server_overview_text(approval=True, connected=False)
        assert "&lt;fixture&amp;build&gt;" in overview
        assert "&lt;fixture&build&gt;" not in overview
        assert xlex.render("server_mqtt_line", style, state="ожидает подключения") in overview
        assert xlex.render("server_approval_line", style, state="включено") in overview
        overview_cards.add(overview)

        confirmation = bot.server_confirmation_text("rollback")
        assert xlex.render("server_action_rollback", style) in confirmation
        assert xlex.render("server_confirm_warning", style) in confirmation
        confirmation_cards.add(confirmation)

        metrics = bot.server_metrics_text({"load": 12.3, "memory": 45.6, "disk": 78.9})
        assert "12.3%" in metrics and "45.6%" in metrics and "78.9%" in metrics
        metrics_cards.add(metrics)

        terminal = bot.server_terminal_text()
        assert "<code>uptime</code>" in terminal
        assert "/cancel" in terminal
        terminal_cards.add(terminal)

        confirm_buttons = {
            button.callback_data: button.text
            for row in bot.server_confirm_menu("update").inline_keyboard
            for button in row
        }
        assert set(confirm_buttons) == {"server_confirm:update", "menu:server"}
        assert all(len(label) <= 64 for label in confirm_buttons.values())
    assert len(overview_cards) > 1
    assert len(confirmation_cards) > 1
    assert len(metrics_cards) > 1
    assert len(terminal_cards) > 1


def test_admin_navigation_and_text_editor_follow_selected_voice(monkeypatch):
    expected_admin_callbacks = None
    expected_text_callbacks = None
    for style in xlex.STYLES:
        monkeypatch.setattr(
            bot.bot_settings, "get",
            lambda key, default=None, selected=style: selected if key == "ui_style" else default,
        )
        monkeypatch.setattr(bot.text_store, "get", lambda key: "fixture preview")
        admin_buttons = {
            button.callback_data: button.text
            for row in bot.admin_menu().inline_keyboard for button in row
        }
        text_buttons = {
            button.callback_data: button.text
            for row in bot.admin_texts_menu().inline_keyboard for button in row
        }
        assert admin_buttons["admin:users"] == xlex.nav("admin_users", style)
        assert admin_buttons["admin:audit"] == xlex.nav("admin_audit", style)
        assert admin_buttons["admin:texts"] == xlex.nav("admin_texts", style)
        assert admin_buttons["menu:server"] == xlex.nav("admin_server", style)
        assert admin_buttons["menu:main"] == xlex.nav("home", style)
        assert text_buttons["admin:style"] == xlex.nav("change_style", style)
        assert text_buttons["menu:admin"] == xlex.nav("back", style)
        assert all(len(label) <= 64 for label in [*admin_buttons.values(), *text_buttons.values()])
        admin_callbacks = set(admin_buttons)
        text_callbacks = {
            callback.removeprefix(f"admin:text:{style}_")
            if callback.startswith("admin:text:") else callback
            for callback in text_buttons
        }
        if expected_admin_callbacks is None:
            expected_admin_callbacks = admin_callbacks
            expected_text_callbacks = text_callbacks
        else:
            assert admin_callbacks == expected_admin_callbacks
            assert text_callbacks == expected_text_callbacks


def test_admin_copy_keeps_owner_protection_clear_in_every_voice():
    for style in xlex.STYLES:
        intro = xlex.render("admin_intro", style).lower()
        assert "владел" in intro
        assert "блок" in intro
        assert any(fragment in intro for fragment in ("пониз", "пониж"))
        assert "удал" in intro


def test_admin_user_actions_follow_voice_without_changing_permissions_callbacks(monkeypatch):
    monkeypatch.setattr(bot.access_store, "get_user", lambda user_id: {"id": user_id})
    callbacks = None
    for style in xlex.STYLES:
        monkeypatch.setattr(
            bot.bot_settings, "get",
            lambda key, default=None, selected=style: selected if key == "ui_style" else default,
        )
        monkeypatch.setattr(bot.access_store, "get_role", lambda user_id, owner_id: bot.Role.GUEST)
        markup = bot.admin_user_menu(123)
        buttons = {
            button.callback_data: button.text
            for row in markup.inline_keyboard for button in row
        }
        assert buttons["admin:role:123:user"] == xlex.nav("make_user", style)
        assert buttons["admin:role:123:guest"] == xlex.nav("make_guest", style)
        assert buttons["admin:devices:123"] == xlex.nav("grant_devices", style)
        assert buttons["admin:perms:123"] == xlex.nav("grant_buttons", style)
        assert buttons["admin:message:123"] == xlex.nav("message_user", style)
        assert buttons["admin:block:123"] == xlex.nav("block_user", style)
        current_callbacks = set(buttons)
        if callbacks is None:
            callbacks = current_callbacks
        else:
            assert current_callbacks == callbacks
        assert all(len(label) <= 64 for label in buttons.values())
        monkeypatch.setattr(bot.access_store, "get_role", lambda user_id, owner_id: bot.Role.BLOCKED)
        blocked_buttons = {
            button.callback_data: button.text
            for row in bot.admin_user_menu(123).inline_keyboard for button in row
        }
        assert blocked_buttons["admin:block:123"] == xlex.nav("unblock_user", style)


def test_device_and_command_grants_follow_voice_without_changing_callbacks(monkeypatch):
    devices = {
        "mac-1": {"name": "MacBook Air"},
        "win-1": {"name": "Windows PC"},
    }
    enabled = {"devices": ["mac-1"], "callbacks": ["cmd:status", "full_device"]}
    monkeypatch.setattr(bot.devices, "all", lambda: devices)
    monkeypatch.setattr(
        bot.access_store, "get_user",
        lambda user_id: {"id": user_id, "permissions": enabled},
    )
    device_callbacks = None
    permission_callbacks = None
    guest_callbacks = None
    for style in xlex.STYLES:
        monkeypatch.setattr(
            bot.bot_settings, "get",
            lambda key, default=None, selected=style: selected if key == "ui_style" else default,
        )
        device_buttons = {
            button.callback_data: button.text
            for row in bot.admin_devices_menu(123).inline_keyboard for button in row
        }
        permission_buttons = {
            button.callback_data: button.text
            for row in bot.admin_permissions_menu(123).inline_keyboard for button in row
        }
        guest_buttons = {
            button.callback_data: button.text
            for row in bot.guest_devices_menu().inline_keyboard for button in row
        }

        assert device_buttons["admin:device:123:mac-1"] == xlex.render(
            "permission_enabled", style, item="MacBook Air"
        )
        assert device_buttons["admin:device:123:win-1"] == xlex.render(
            "permission_disabled", style, item="Windows PC"
        )
        for callback, label_key in bot.USER_PERMISSION_CHOICES.items():
            state = "permission_enabled" if callback in enabled["callbacks"] else "permission_disabled"
            encoded = callback.replace(":", "_")
            assert permission_buttons[f"admin:perm:123:{encoded}"] == xlex.render(
                state, style, item=xlex.nav(label_key, style)
            )
        assert guest_buttons["menu:main"] == xlex.nav("guest_home", style)
        assert all(
            len(label) <= 64
            for label in [*device_buttons.values(), *permission_buttons.values(), *guest_buttons.values()]
        )

        current_device_callbacks = set(device_buttons)
        current_permission_callbacks = set(permission_buttons)
        current_guest_callbacks = set(guest_buttons)
        if device_callbacks is None:
            device_callbacks = current_device_callbacks
            permission_callbacks = current_permission_callbacks
            guest_callbacks = current_guest_callbacks
        else:
            assert current_device_callbacks == device_callbacks
            assert current_permission_callbacks == permission_callbacks
            assert current_guest_callbacks == guest_callbacks


def test_common_navigation_controls_follow_voice_without_changing_callbacks(monkeypatch):
    monkeypatch.setattr(bot.devices, "all", lambda: {})
    for style in xlex.STYLES:
        monkeypatch.setattr(
            bot.bot_settings, "get",
            lambda key, default=None, selected=style: selected if key == "ui_style" else default,
        )
        back_buttons = {
            button.callback_data: button.text
            for row in bot.back_to_device_kb().inline_keyboard for button in row
        }
        confirm_buttons = {
            button.callback_data: button.text
            for row in bot.confirm_power_menu("shutdown").inline_keyboard for button in row
        }
        devices_buttons = {
            button.callback_data: button.text
            for row in bot.devices_menu().inline_keyboard for button in row
        }
        assert back_buttons["back:device"] == xlex.nav("back_device", style)
        assert back_buttons["menu:main"] == xlex.nav("home", style)
        assert confirm_buttons["back:device"] == xlex.nav("cancel", style)
        assert devices_buttons["menu:devices"] == xlex.nav("refresh", style)
        assert devices_buttons["menu:main"] == xlex.nav("home", style)
        assert all(
            len(label) <= 64
            for label in [*back_buttons.values(), *confirm_buttons.values(), *devices_buttons.values()]
        )
