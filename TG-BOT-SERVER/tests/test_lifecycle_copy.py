"""Lifecycle notices keep partial results and escape device-provided details."""

from string import Formatter

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
