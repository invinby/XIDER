"""Lifecycle notices keep partial results and escape device-provided details."""

import hashlib
from pathlib import Path
from string import Formatter
import asyncio
from types import SimpleNamespace

import pytest

import bot
import xlex


def test_xlex_bytes_remain_equal_to_the_approved_main_baseline():
    source = Path(__file__).resolve().parents[1] / "xlex.py"
    canonical_source = source.read_bytes().replace(b"\r\n", b"\n")
    assert hashlib.sha256(canonical_source).hexdigest() == (
        "f89314d1930cd6fedf6b29c6c13be718a888bf2c24ed48397837a8d2c1fb0d02"
    )


def test_camera_action_button_copy_does_not_extend_xlex(monkeypatch):
    labels = {
        "camera_preview_keep_button": "✅ Оставить кадр в чате",
        "camera_preview_discard_button": "🗑 Удалить кадр из чата",
        "camera_capture_button": "📷 Сделать один кадр",
    }
    assert set(labels).isdisjoint(xlex.COPY)
    for key, expected in labels.items():
        assert bot._camera_action_button_label(key) == expected
    assert bot._camera_action_button_label("camera_series_button", count="3") == "🎞 Серия · 3 кадра"


def test_lifecycle_failure_notice_has_six_distinct_voices_and_required_values():
    variants = xlex.COPY["lifecycle_failure_notice"]
    assert set(variants) == set(xlex.STYLES)
    rendered = []
    for style, template in variants.items():
        fields = {field for _, field, _, _ in Formatter().parse(template) if field}
        assert fields == {"device", "state", "text"}
        assert "переустанов" not in template.lower()
        rendered.append(xlex.render(
            "lifecycle_failure_notice", style, device="Mac", state="restart_failed", text="exec failed",
        ))
    assert len(set(rendered)) == len(xlex.STYLES)
    xlex.validate()


@pytest.mark.parametrize("style", xlex.STYLES)
def test_lifecycle_failure_notice_escapes_all_device_fields(monkeypatch, style):
    monkeypatch.setattr(bot.bot_settings, "get", lambda *_args, **_kwargs: style)
    rendered = bot._lex_html(
        "lifecycle_failure_notice", device="<b>Mac & owner</b>",
        state="<restart_failed>", text="<a href='unsafe'>agent detail</a>",
    )

    assert "<b>Mac & owner</b>" not in rendered
    assert "<restart_failed>" not in rendered
    assert "<a href='unsafe'>" not in rendered
    assert "&lt;b&gt;Mac &amp; owner&lt;/b&gt;" in rendered
    assert "&lt;restart_failed&gt;" in rendered
    assert "&lt;a href=&#x27;unsafe&#x27;&gt;agent detail&lt;/a&gt;" in rendered


@pytest.mark.parametrize("style", xlex.STYLES)
def test_guardian_restart_warning_preserves_confirmed_worker_health(style):
    detail = "Обновление агента подтверждено, MQTT работает. Перезапуск Guardian не выполнен."
    rendered = xlex.render(
        "lifecycle_failure_notice", style, device="Mac", state="guardian_restart_failed", text=detail,
    )

    assert detail in rendered
    assert "guardian_restart_failed" in rendered
    assert "обновление агента не выполнено" not in rendered.lower()


def test_battery_notice_updates_owner_card_without_separate_alert(monkeypatch, tmp_path):
    monkeypatch.setattr(bot, "ADMIN_ID", 100)
    monkeypatch.setattr(bot.ui_cards, "FILE", tmp_path / "cards.json")
    bot.ui_cards.set_card(100, 100, 42)
    monkeypatch.setattr(bot, "LAST_BATTERY_ALERT", {})
    monkeypatch.setattr(bot, "target_label", lambda _device: "Mac")
    monkeypatch.setattr(bot.bot_settings, "get", lambda _key, default=None: default)
    monkeypatch.setattr(bot.bot_settings, "quiet_active", lambda: False)
    edits = []
    class Telegram:
        async def edit_message_text(self, text, **kwargs):
            edits.append((text, kwargs["message_id"]))
        async def send_message(self, *args, **kwargs):
            pytest.fail("Routine battery feedback must use the owner card")
    monkeypatch.setattr(bot, "bot", Telegram())
    async def scenario():
        monkeypatch.setattr(bot, "LOOP", asyncio.get_running_loop())
        bot._maybe_battery_alert("mac", {"available": True, "percent": 19})
        await asyncio.sleep(0.03)
        assert len(edits) == 1
        assert edits[0][1] == 42 and "19%" in edits[0][0]
    asyncio.run(scenario())


def test_new_device_approval_uses_owner_card_and_keeps_action_keyboard(monkeypatch, tmp_path):
    monkeypatch.setattr(bot, "ADMIN_ID", 100)
    monkeypatch.setattr(bot.ui_cards, "FILE", tmp_path / "cards.json")
    bot.ui_cards.set_card(100, 100, 42)
    monkeypatch.setattr(bot, "ENCRYPT_PAYLOAD", False)
    monkeypatch.setattr(bot, "verify_message_detailed", lambda payload, **_kwargs: (payload, "ok"))
    monkeypatch.setattr(bot, "audit", lambda *args, **kwargs: None)
    monkeypatch.setattr(bot, "_STATUS_NOTICE_STATE", {})
    monkeypatch.setattr(bot.bot_settings, "get", lambda _key, default=None: default)
    monkeypatch.setattr(bot.bot_settings, "is_blocked", lambda _device: False)
    items = {}
    monkeypatch.setattr(bot, "devices", SimpleNamespace(
        all=lambda: items.copy(), get=lambda key: items.get(key),
        upsert=lambda key, value, **_kwargs: items.update({key: value}),
    ))
    edits = []
    class Telegram:
        async def edit_message_text(self, text, **kwargs):
            edits.append((text, kwargs))
        async def send_message(self, *args, **kwargs):
            pytest.fail("Device approval must use the indexed owner card")
    monkeypatch.setattr(bot, "bot", Telegram())
    async def scenario():
        monkeypatch.setattr(bot, "LOOP", asyncio.get_running_loop())
        bot.on_mqtt_message(f"{bot.MQTT_PREFIX}/mac/status", {"type": "status", "status": "online", "name": "Mac"})
        await asyncio.sleep(0.03)
        assert len(edits) == 1
        text, options = edits[0]
        assert options["chat_id"] == 100 and options["message_id"] == 42
        callbacks = [button.callback_data for row in options["reply_markup"].inline_keyboard for button in row]
        assert callbacks == ["devmg:allow:mac", "devmg:block:mac"]
    asyncio.run(scenario())
