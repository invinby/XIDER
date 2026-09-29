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
