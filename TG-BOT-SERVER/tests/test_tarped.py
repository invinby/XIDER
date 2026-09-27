"""Offline checks for the TARPED catalogue, handbook and text styles."""

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
        return [{"tag_name": "v3.3.8", "assets": []}]

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
