"""Location formatter must preserve source and uncertainty without trusting text."""
import copy
from unittest.mock import Mock
import bot

def render(result):
    formatter = getattr(bot, "format_location_result", None)
    assert callable(formatter), "Missing pure location formatter"
    return formatter(result)

def test_ip_provider_coordinates_never_reach_telegram():
    value = {"source": "ip_estimate", "state": "granted", "latitude": 41.311, "longitude": 69.279,
             "accuracy_m": 1, "label": "<city>", "checked_at": "2026-10-08T10:00:00+00:00",
             "text": "provider GPS 41.311, 69.279"}
    before = copy.deepcopy(value)
    text = render(value)
    assert "41.311" not in text and "69.279" not in text
    assert "IP" in text and "GPS" in text and "неизвестна" in text
    assert "&lt;city&gt;" in text and value == before

def test_system_fix_shows_actual_uncertainty():
    text = render({"source": "system", "state": "granted", "latitude": 41.311, "longitude": 69.279,
                   "accuracy_m": 80, "label": "system", "checked_at": "2026-10-08T10:00:00+00:00"})
    assert "41.311" in text and "80" in text
    assert "систем" in text.lower() and "погреш" in text.lower()
    assert "2026-10-08" in text

def test_comparison_result_formats_each_source_truthfully():
    text = render({
        "source": "comparison", "state": "granted",
        "checked_at": "2026-10-08T10:00:00+00:00",
        "system_location": {
            "source": "system", "state": "granted", "latitude": 41.311,
            "longitude": 69.279, "accuracy_m": 80, "checked_at": "2026-10-08T10:00:00+00:00",
        },
        "ip_estimate": {
            "source": "ip_estimate", "state": "granted", "latitude": None,
            "longitude": None, "accuracy_m": None, "label": "Tashkent",
            "checked_at": "2026-10-08T10:00:01+00:00",
        },
    })

    assert "Сравнение источников" in text
    assert "41.311" in text and "80 м" in text
    assert "IP estimate" in text and "не GPS" in text and "Tashkent" in text

def test_comparison_keyboard_and_callback_are_owner_only(monkeypatch):
    import asyncio
    from types import SimpleNamespace
    handler = getattr(bot, "on_cmd_geo_location_compare", None)
    assert callable(handler), "Owner-only comparison handler missing"
    monkeypatch.setattr(bot, "get_user_role", lambda _uid: bot.Role.OWNER)
    keyboard = bot._location_result_keyboard({"state": "granted"}, bot.ADMIN_ID)
    assert any(button.callback_data == "location:compare"
               for row in keyboard.inline_keyboard for button in row)
    published = []
    async def simple(cq, action, emoji, label, **kwargs):
        published.append((action, kwargs))
    monkeypatch.setattr(bot, "simple_command", simple)
    asyncio.run(handler(SimpleNamespace()))
    assert published == [("geo_location", {"timeout": 16.0, "compare_sources": True})]
    assert bot._is_read_only_check("location:compare", "geo_location", {"compare_sources": True}) is False
    monkeypatch.setattr(bot, "get_user_role", lambda _uid: bot.Role.USER)
    user_keyboard = bot._location_result_keyboard({"state": "granted"}, 200)
    assert not any(button.callback_data == "location:compare"
                   for row in user_keyboard.inline_keyboard for button in row)

def test_unknown_source_and_invalid_fix_do_not_claim_granted():
    for changes in ({"source": "gps"}, {"latitude": 91}, {"accuracy_m": -1}, {"checked_at": "invalid"},
                    {"state": "supported"}, {"latitude": float("nan")}):
        value = {"source": "system", "state": "granted", "latitude": 41.311, "longitude": 69.279,
                 "accuracy_m": 80, "label": "test", "checked_at": "2026-10-08T10:00:00+00:00", **changes}
        text = render(value)
        assert "неизвестно" in text and "41.311" not in text

def test_limited_diagnostic_shows_owner_local_guidance():
    text = render({"source": "system", "state": "needs_live_test",
                   "label": "Настройки на самом устройстве: ms-settings:privacy-location",
                   "checked_at": "2026-10-08T10:00:00+00:00"})
    assert "ms-settings:privacy-location" in text and "на самом устройстве" in text

def test_audit_redacts_location_fields(monkeypatch):
    logged = []
    monkeypatch.setattr(bot.audit_log, "info", lambda *args: logged.append(str(args)))
    bot.audit("location", latitude=41.311, longitude=69.279, coordinates="41.311,69.279",
              location={"latitude": 41.311}, state="granted", source="system")
    assert "41.311" not in logged[0] and "69.279" not in logged[0]

def test_ip_fallback_requires_separate_owner_button_and_explicit_boolean(monkeypatch):
    import asyncio
    from types import SimpleNamespace
    keyboard = getattr(bot, "_location_result_keyboard", None)
    handler = getattr(bot, "on_cmd_geo_location_ip", None)
    assert callable(keyboard) and callable(handler), "Explicit IP fallback UI missing"
    monkeypatch.setattr(bot, "get_user_role", lambda uid: bot.Role.OWNER)
    buttons = [b for row in keyboard({"state": "needs_live_test"}, 1).inline_keyboard for b in row]
    assert any(b.callback_data == "location:ip" and "IP" in b.text for b in buttons)
    published = []
    async def simple(cq, action, emoji, label, **kwargs):
        published.append((action, kwargs))
    monkeypatch.setattr(bot, "simple_command", simple)
    asyncio.run(handler(SimpleNamespace()))
    assert published == [("geo_location", {"timeout": 16.0, "ip_fallback": True})]
