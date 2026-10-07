"""Offline checks for the TARPED catalogue, handbook and text styles."""

import asyncio
import ast
from types import SimpleNamespace
from pathlib import Path

import pytest
import bot
import info_book
import release_catalog as ledger
import text_store
import xlex
import url_favorites


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


def test_web_url_validation_and_named_favorites_reject_unsafe_or_duplicate_entries():
    assert url_favorites.normalize_web_url("example.com/docs") == "https://example.com/docs"
    assert url_favorites.normalize_web_url("http://example.com") == "http://example.com"
    for value in (
        "javascript:alert(1)",
        "file:///etc/passwd",
        "https://user:pass@example.com/",
        "https:///missing-host",
    ):
        with pytest.raises(ValueError):
            url_favorites.normalize_web_url(value)

    saved = url_favorites.add_favorite([], "Docs", "example.com/docs")
    assert saved[0]["name"] == "Docs"
    assert saved[0]["url"] == "https://example.com/docs"
    with pytest.raises(ValueError):
        url_favorites.add_favorite(saved, "docs", "https://other.example")
    assert url_favorites.remove_favorite(saved, saved[0]["id"]) == []


def test_common_command_feedback_has_six_distinct_xlex_voices():
    keys = (
        "target_required",
        "device_required",
        "device_no_response",
        "device_timeout",
        "single_device_only",
        "mqtt_disconnected",
        "mqtt_publish_failed",
        "send_failed",
        "empty_text",
        "target_and_text_required",
        "mqtt_photo_failed",
    )
    for key in keys:
        values = {"seconds": "15"} if key == "device_timeout" else {}
        phrases = [xlex.render(key, style, **values) for style in xlex.STYLES]
        assert len(set(phrases)) == len(xlex.STYLES), key

    assert all("MQTT" in xlex.render("mqtt_disconnected", style) for style in xlex.STYLES)
    assert all("MQTT" in xlex.render("mqtt_publish_failed", style) for style in xlex.STYLES)
    assert all("MQTT" in xlex.render("mqtt_photo_failed", style) for style in xlex.STYLES)
    assert "одно" in xlex.render("single_device_only", "xcore")
    assert all("15" in xlex.render("device_timeout", style, seconds="15") for style in xlex.STYLES)


def test_text_command_flow_copy_has_six_distinct_voices_and_truthful_delivery():
    keys = (
        "shell_prompt",
        "open_app_prompt",
        "url_prompt",
        "notify_text_prompt",
        "sound_prompt",
    )
    for key in keys:
        phrases = [xlex.render(key, style) for style in xlex.STYLES]
        assert len(set(phrases)) == len(xlex.STYLES), key

    arguments = {
        "device": "Test <device>",
        "url": "https://example.com/?a=1&b=2",
        "app": "Calculator <test>",
    }
    result_keys = (
        "url_request_sent",
        "notify_text_request_sent",
        "sound_request_sent",
        "app_launch_request_sent",
    )
    for key in result_keys:
        phrases = [xlex.render(key, style, **arguments) for style in xlex.STYLES]
        assert len(set(phrases)) == len(xlex.STYLES), key

    launch = xlex.render("app_launch_request_sent", "xadam", **arguments)
    assert "подтверждение устройства" in launch
    safe_url = bot._lex_html("url_request_sent", **arguments)
    assert "&amp;" in safe_url
    assert "<device>" not in safe_url


def test_file_workflow_copy_has_six_complete_distinct_voices(monkeypatch):
    keys = (
        "files_menu_title", "files_menu_description", "files_prompt_list",
        "files_prompt_find", "files_prompt_get", "files_prompt_put",
        "files_prompt_delete", "files_prompt_open", "files_document_required",
        "files_size_limit", "files_upload_saved", "files_upload_failed",
        "files_empty_path", "files_list_result", "files_download_timeout",
        "files_download_failed", "files_download_corrupt", "files_download_caption",
        "files_delete_done", "files_delete_failed", "files_open_done",
        "files_open_failed", "files_find_empty", "files_find_no_response",
        "files_find_result",
    )
    arguments = {
        "device": "Test device",
        "limit": "30",
        "path": "/tmp/report.txt",
        "error": "permission denied",
        "text": "report.txt",
    }

    xlex.validate()
    for key in keys:
        phrases = [xlex.render(key, style, **arguments) for style in xlex.STYLES]
        assert len(set(phrases)) == len(xlex.STYLES), key

    monkeypatch.setattr(
        bot.bot_settings,
        "get",
        lambda key, default=None: "xtech" if key == "ui_style" else default,
    )
    escaped = bot._lex_html("files_upload_saved", path="<script>alert('x')</script>")
    assert "&lt;script&gt;" in escaped
    assert "<script>" not in escaped


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
        {"tag_name": "v" + ("9" * 5000) + ".0.0", "assets": [{"name": "X-STAB-server.zip"}]},
        {"tag_name": "random", "assets": []},
    ]
    releases = ledger.parse_releases(rows)
    assert [item.tag for item in releases] == ["v4.0.0", "v3.3.8"]
    assert releases[0].has_package("windows_agent")
    assert not releases[0].has_package("mac_agent")
    assert not releases[0].has_package("windows_keeper")
    assert not releases[0].has_source_update_payload("windows_agent")
    assert ledger.newest_with_package(releases, "mac_agent").tag == "v3.3.8"
    assert ledger.is_older("3.3.8", releases[0])
    assert not ledger.is_older("4.0.0", releases[0])


def test_release_catalog_preserves_complete_release_notes():
    notes = "X" * 7000 + "END-OF-NOTES"

    [release] = ledger.parse_releases([
        {"tag_name": "v4.2.0", "name": "Long notes", "body": notes, "assets": []},
    ])

    assert release.notes == notes


def test_release_source_update_requires_platform_bundle_archive_and_manifest():
    release = ledger.Release(
        tag="v4.1.0", version=(4, 1, 0), name="release", published_at="", notes="",
        asset_names=frozenset({
            "XGENT-WDS-Windows.zip", "XIDER-source.zip", "release-manifest.json",
        }),
    )
    assert release.has_package("windows_agent")
    assert release.has_source_update_payload("windows_agent")
    assert not release.has_source_update_payload("mac_agent")


def test_release_manifest_updater_requires_a_known_supported_agent_version():
    assert ledger.supports_release_manifest_update("4.0.1")
    assert ledger.supports_release_manifest_update("v4.0.1")
    assert ledger.supports_release_manifest_update("4.1.0")
    assert not ledger.supports_release_manifest_update("4.0.0")
    assert not ledger.supports_release_manifest_update("3.3.8")
    assert not ledger.supports_release_manifest_update("")
    assert not ledger.supports_release_manifest_update("unknown")


def test_bot_and_agent_source_versions_stay_aligned():
    root = Path(__file__).resolve().parents[2]

    def read_version(relative_path):
        module = ast.parse((root / relative_path).read_text(encoding="utf-8"))
        for node in module.body:
            if isinstance(node, ast.Assign) and any(
                isinstance(target, ast.Name) and target.id == "VERSION"
                for target in node.targets
            ):
                if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                    return node.value.value
        raise AssertionError(f"VERSION is missing in {relative_path}")

    assert read_version("XGENT-WDS/config.py") == bot.VERSION
    assert read_version("XGENT-MCS/config.py") == bot.VERSION


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


def test_release_notes_split_by_escaped_html_size_without_losing_text():
    notes = "<&' \n" * 1000
    chunks = bot._split_html_escaped_text(notes)
    assert len(chunks) > 1
    assert "".join(chunks) == notes
    assert all(len(bot.html.escape(chunk)) <= 2500 for chunk in chunks)


def test_version_screens_follow_six_voices_and_keep_release_callbacks(monkeypatch):
    selected = bot.SessionRegistry()
    selected["target"] = "mac-1"
    monkeypatch.setattr(bot, "SESSION", selected)

    class FakeDevices:
        def get(self, device_id):
            return {"os": "macOS", "version": "3.3.8", "guardian": {"version": "1.2.0"}}

    monkeypatch.setattr(bot, "devices", FakeDevices())
    release = ledger.Release(
        tag="v4.0.2",
        version=(4, 0, 2),
        name="<release & notes>",
        published_at="2026-09-30T12:00:00Z",
        notes="<&' \n" * 1000,
        asset_names=frozenset({"XGENT-MCS-macos-bundle.zip"}),
    )
    monkeypatch.setattr(bot.release_catalog.catalog, "list", lambda: [release])
    shown = {}

    async def replace_card(_cq, text, reply_markup=None):
        shown["text"] = text
        shown["markup"] = reply_markup

    monkeypatch.setattr(bot, "_replace_callback_message", replace_card)

    class Callback:
        from_user = SimpleNamespace(id=123)

        def __init__(self, data):
            self.data = data

        async def answer(self, *args, **kwargs):
            pass

    expected_callbacks = None
    rendered_lists = set()
    rendered_details = set()
    for style in xlex.STYLES:
        monkeypatch.setattr(
            bot.bot_settings, "get",
            lambda key, default=None, selected=style: selected if key == "ui_style" else default,
        )
        root = {
            button.callback_data: button.text
            for row in bot._version_root_menu().inline_keyboard for button in row
        }
        assert root["versions:list:agent:0"] == xlex.render("versions_agent_button", style, version="3.3.8")
        assert root["versions:list:keeper:0"] == xlex.render("versions_keeper_button", style, version="1.2.0")
        assert set(root) == {"versions:list:agent:0", "versions:list:keeper:0", "back:device"}
        assert all(len(label) <= 64 for label in root.values())
        server_root = {
            button.callback_data: button.text
            for row in bot._version_root_menu(server=True).inline_keyboard for button in row
        }
        assert server_root["versions:list:server:0"] == xlex.render(
            "versions_server_root_button", style, version=bot.VERSION
        )
        assert set(server_root) == {"versions:list:server:0", "menu:server"}

        asyncio.run(bot.on_versions_list(Callback("versions:list:agent:0")))
        assert "&lt;release &amp; notes&gt;" in shown["text"]
        assert xlex.render("versions_list_title", style, title="X-EDGE-M") in shown["text"]
        rendered_lists.add(shown["text"])
        list_callbacks = {
            button.callback_data
            for row in shown["markup"].inline_keyboard for button in row
        }
        assert "versions:detail:agent:v4.0.2" in list_callbacks
        assert "versions:device" in list_callbacks
        if expected_callbacks is None:
            expected_callbacks = list_callbacks
        else:
            assert list_callbacks == expected_callbacks

        asyncio.run(bot.on_versions_detail(Callback("versions:detail:agent:v4.0.2:0")))
        escaped_note = shown["text"].split("<pre>", 1)[1].split("</pre>", 1)[0]
        assert len(escaped_note) <= 2500
        assert len(shown["text"]) < 4096
        assert xlex.render(
            "versions_detail_title", style, title="X-EDGE-M", tag="v4.0.2"
        ) in shown["text"]
        rendered_details.add(shown["text"])
        detail_callbacks = {
            button.callback_data
            for row in shown["markup"].inline_keyboard for button in row
        }
        assert "versions:detail:agent:v4.0.2:1" in detail_callbacks
        assert "versions:list:agent:0" in detail_callbacks
        assert not any(callback.startswith("versions:install:") for callback in detail_callbacks)
        assert xlex.render("versions_install_payload_missing", style) in shown["text"]

    assert len(rendered_lists) > 1
    assert len(rendered_details) > 1


def test_release_detail_can_navigate_past_page_twenty(monkeypatch):
    notes = "release-note-line\n" * 5000
    release = ledger.Release(
        tag="v4.2.1",
        version=(4, 2, 1),
        name="Long release",
        published_at="2026-10-06T12:00:00Z",
        notes=notes,
        asset_names=frozenset({"XGENT-WDS-Windows.zip"}),
    )

    class FakeDevices:
        def get(self, device_id):
            return {"os": "Windows", "version": "4.1.4"}

    class Callback:
        from_user = SimpleNamespace(id=123)

        def __init__(self, data):
            self.data = data

        async def answer(self, *args, **kwargs):
            pass

    session = bot.SessionRegistry()
    session["target"] = "device1"
    monkeypatch.setattr(bot, "SESSION", session)
    monkeypatch.setattr(bot, "devices", FakeDevices())
    monkeypatch.setattr(bot.release_catalog.catalog, "list", lambda: [release])
    monkeypatch.setattr(
        bot.bot_settings,
        "get",
        lambda key, default=None: "xtech" if key == "ui_style" else default,
    )
    shown = {}

    async def replace_card(_cq, text, reply_markup=None):
        shown["text"] = text
        shown["markup"] = reply_markup

    monkeypatch.setattr(bot, "_replace_callback_message", replace_card)

    async def render_all_pages():
        rendered = []
        for page in range(100):
            await bot.on_versions_detail(
                Callback(f"versions:detail:agent:v4.2.1:{page}")
            )
            rendered.append(
                shown["text"].split("<pre>", 1)[1].split("</pre>", 1)[0]
            )
            callbacks = {
                button.callback_data
                for row in shown["markup"].inline_keyboard
                for button in row
            }
            next_page = f"versions:detail:agent:v4.2.1:{page + 1}"
            if next_page not in callbacks:
                break
        return rendered

    rendered = asyncio.run(render_all_pages())

    expected_chunks = bot._split_html_escaped_text(notes.strip())
    assert len(expected_chunks) > 21
    assert rendered == expected_chunks


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


def test_handbook_chapter_titles_and_bodies_follow_the_selected_voice():
    required_facts = {
        "overview": ("telegram", "windows", "macos"),
        "modules": ("xider link", "guard keeper"),
        "updates": ("twinshift", "a/b"),
    }
    for slug, facts in required_facts.items():
        rendered = [info_book.chapter(slug, style) for style in xlex.STYLES]
        assert len({title for _, title, _ in rendered}) == len(xlex.STYLES), slug
        assert len({body for _, _, body in rendered}) == len(xlex.STYLES), slug
        for _, title, body in rendered:
            assert len(title) < 160
            assert len(body) < 3500
            combined = f"{title}\n{body}".casefold()
            assert all(fact in combined for fact in facts), (slug, facts)
    _, _, pikmi_modules = info_book.chapter("modules", "xpikmi")
    assert "подружки" in pikmi_modules.casefold()
    assert "бантик" in pikmi_modules.casefold()


def test_handbook_defines_x_route_and_its_open_count_contract():
    bodies = [info_book.chapter("modules", style)[2] for style in xlex.STYLES]
    assert all("X-ROUTE" in body for body in bodies)
    assert all("HTTP" in body for body in bodies)
    assert all(any(token in body.casefold() for token in ("1, 2, 3", "1/2/3", "пять")) for body in bodies)


def test_handbook_distinguishes_transactional_updater_from_future_twinshift():
    _, _, versions = info_book.chapter("versions")
    _, _, updates = info_book.chapter("updates")
    assert "Закреплённый в исходниках ключ ещё не означает" in versions
    assert "Кнопка появляется только для устройства" in versions
    assert "транзакционный file-update" in updates
    assert "не настоящее A/B" in updates
    assert "TwinShift" in updates


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
        chat = SimpleNamespace(id=bot.ADMIN_ID)
        message_id = 123

        async def edit_text(self, text, *, reply_markup):
            shown["text"] = text
            shown["markup"] = reply_markup

    class Callback:
        data = "admin:style:set:xpikmi"
        from_user = SimpleNamespace(id=bot.ADMIN_ID)
        message = Message()

        async def answer(self, *args, **kwargs):
            pass

    monkeypatch.setattr(bot.ui_cards, "set_card", lambda *args, **kwargs: None)
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


def test_device_action_menus_follow_all_six_voices_without_changing_callbacks(monkeypatch):
    monkeypatch.setattr(bot, "SESSION", {})
    expected = {
        "all": {
            "all:status": "all_status_button",
            "all:screenshot": "all_screenshot_button",
            "cmd:lock": "all_lock_button",
            "cmd:volume": "all_mute_button",
            "cfm:stop_all": "all_stop_button",
        },
        "power": {
            "power:reboot": "power_reboot_button",
            "power:shutdown": "power_shutdown_button",
        },
        "confirm": {"power_confirm:shutdown": "power_confirm_button"},
        "rotate": {
            "rotate:0": "rotate_standard_button",
            "rotate:90": "rotate_right_button",
            "rotate:180": "rotate_inverted_button",
            "rotate:270": "rotate_left_button",
            "cat:screen": "rotate_screen_button",
        },
        "media": {
            "cmd:screenshot": "media_screenshot_button",
            "cmd:webcam": "media_webcam_button",
            "mic:opts": "media_mic_button",
            "cmd:sound": "media_speak_button",
            "vol:opts": "media_volume_button",
            "cmd:volume": "media_mute_button",
        },
        "screen": {
            "fun:screenoff": "screen_off_button",
            "fun:screensaver": "screen_saver_button",
            "menu:wallpaper": "screen_wallpaper_button",
            "cmd:brightness": "screen_brightness_button",
            "cmd:rotate": "screen_rotate_button",
        },
    }
    action_values = {"power_confirm:shutdown": {"action": "ВЫКЛЮЧИТЬ"}}
    first_callbacks = {}
    voice_fingerprints = set()

    for style in xlex.STYLES:
        monkeypatch.setattr(
            bot.bot_settings, "get",
            lambda key, default=None, selected=style: selected if key == "ui_style" else default,
        )
        menus = {
            "all": bot.all_menu(),
            "power": bot.power_menu(),
            "confirm": bot.confirm_power_menu("shutdown"),
            "rotate": bot.rotate_menu(),
            "media": bot.media_menu(),
            "screen": bot.screen_menu("test-device"),
        }
        fingerprint = []
        for name, markup in menus.items():
            buttons = {
                button.callback_data: button.text
                for row in markup.inline_keyboard for button in row
            }
            if name not in first_callbacks:
                first_callbacks[name] = set(buttons)
            else:
                assert set(buttons) == first_callbacks[name]
            for callback, key in expected[name].items():
                assert buttons[callback] == xlex.render(
                    key, style, **action_values.get(callback, {})
                )
            assert all(len(label) <= 64 for label in buttons.values())
            fingerprint.extend((name, callback, buttons[callback]) for callback in sorted(expected[name]))
        voice_fingerprints.add(tuple(fingerprint))

    assert len(voice_fingerprints) == len(xlex.STYLES)


def test_nightlight_button_labels_describe_the_action_for_each_voice(monkeypatch):
    for style in xlex.STYLES:
        monkeypatch.setattr(
            bot.bot_settings, "get",
            lambda key, default=None, selected=style: selected if key == "ui_style" else default,
        )
        monkeypatch.setattr(bot, "SESSION", {"nightlight_test-device": True})
        enabled = {
            button.callback_data: button.text
            for row in bot.screen_menu("test-device").inline_keyboard for button in row
        }
        assert enabled["cmd:nightlight"] == xlex.render("screen_nightlight_enabled_button", style)
        monkeypatch.setattr(bot, "SESSION", {"nightlight_test-device": False})
        disabled = {
            button.callback_data: button.text
            for row in bot.screen_menu("test-device").inline_keyboard for button in row
        }
        assert disabled["cmd:nightlight"] == xlex.render("screen_nightlight_disabled_button", style)


def test_power_and_guardian_menus_use_all_six_voices_without_changing_callbacks(monkeypatch):
    device_info = {"standby": False}
    monkeypatch.setattr(bot, "SESSION", {"target": "demo"})
    monkeypatch.setattr(bot, "devices", SimpleNamespace(get=lambda _device_id: device_info))

    power_keys = {
        "cmd:lock": "power_lock_screen_button",
        "power:sleep": "power_sleep_device_button",
        "power:reboot": "power_reboot_device_button",
        "power:shutdown": "power_shutdown_device_button",
        "cmd:autorun_status": "power_autorun_status_button",
        "cmd:autorun_enable": "power_autorun_enable_button",
        "cmd:autorun_disable": "power_autorun_disable_button",
        "cmd:guardian_menu": "power_guardian_menu_button",
        "cmd:wol": "power_wol_button",
        "cfm:stop": "power_stop_agent_button",
        "back:device": None,
        "menu:main": None,
    }
    guardian_keys = {
        "cmd:guardian_status": "guardian_status_button",
        "cmd:guardian_start": "guardian_start_agent_button",
        "cfm:guardian_stop": "guardian_stop_agent_button",
        "cmd:guardian_restart": "guardian_restart_agent_button",
        "cmd:guardian_auto_on": "guardian_auto_enable_button",
        "cmd:guardian_auto_off": "guardian_auto_disable_button",
        "cat:power": "guardian_back_power_button",
    }
    callback_sets = {}
    fingerprints = set()

    for style in xlex.STYLES:
        monkeypatch.setattr(
            bot.bot_settings, "get",
            lambda key, default=None, selected=style: selected if key == "ui_style" else default,
        )
        fingerprint = []

        for standby in (False, True):
            device_info["standby"] = standby
            power_buttons = {
                button.callback_data: button.text
                for row in bot.power_menu_new().inline_keyboard for button in row
            }
            dynamic_callback = "cmd:wake" if standby else "cmd:standby_sleep"
            expected_power = {
                **power_keys,
                dynamic_callback: "power_wake_agent_button" if standby else "power_standby_agent_button",
            }
            assert set(power_buttons) == set(expected_power)
            power_state = f"power:{standby}"
            if power_state not in callback_sets:
                callback_sets[power_state] = set(power_buttons)
            else:
                assert set(power_buttons) == callback_sets[power_state]
            for callback, key in expected_power.items():
                if key is not None:
                    assert power_buttons[callback] == xlex.render(key, style)
            assert all(len(label) <= 64 for label in power_buttons.values())
            fingerprint.extend(("power", callback, power_buttons[callback]) for callback in sorted(power_buttons))

        guardian_buttons = {
            button.callback_data: button.text
            for row in bot.guardian_menu().inline_keyboard for button in row
        }
        assert set(guardian_buttons) == set(guardian_keys)
        if "guardian" not in callback_sets:
            callback_sets["guardian"] = set(guardian_buttons)
        else:
            assert set(guardian_buttons) == callback_sets["guardian"]
        for callback, key in guardian_keys.items():
            assert guardian_buttons[callback] == xlex.render(key, style)
        assert all(len(label) <= 64 for label in guardian_buttons.values())
        fingerprint.extend(("guardian", callback, guardian_buttons[callback]) for callback in sorted(guardian_buttons))
        fingerprints.add(tuple(fingerprint))

    assert len(fingerprints) == len(xlex.STYLES)
