"""Lifecycle notices keep partial results and escape device-provided details."""

from string import Formatter
import asyncio
from types import SimpleNamespace

import pytest

import bot
import xlex


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
    monkeypatch.setattr(bot, "verify_message_detailed", lambda payload: (payload, "ok"))
    monkeypatch.setattr(bot, "audit", lambda *args, **kwargs: None)
    monkeypatch.setattr(bot, "_STATUS_NOTICE_STATE", {})
    monkeypatch.setattr(bot.bot_settings, "get", lambda _key, default=None: default)
    monkeypatch.setattr(bot.bot_settings, "is_blocked", lambda _device: False)
    items = {}
    monkeypatch.setattr(bot, "devices", SimpleNamespace(
        all=lambda: items.copy(), get=lambda key: items.get(key),
        upsert=lambda key, value: items.update({key: value}),
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
