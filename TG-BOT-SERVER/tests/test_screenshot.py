import asyncio
import base64
import io
from types import SimpleNamespace
import pytest
from PIL import Image
import bot


@pytest.mark.parametrize("state,instruction", [
    ("permission_denied", "Запись экрана"), ("session_unavailable", "сеанс"),
    ("tool_missing", "компонент"), ("command_failed", "команда"),
    ("file_empty", "пуст"), ("file_oversized", "размер"),
    ("file_unreadable", "прочитать"), ("timeout", "время"),
    ("transport_failed", "достав"), ("unknown", "неизвест"),
])
def test_handler_maps_failure_to_safe_help(monkeypatch, state, instruction):
    shown = []
    async def answer(*args, **kwargs): pass
    async def allowed(*args, **kwargs): return True
    async def render(cq, text, **kwargs): shown.append(text)
    class Collector:
        async def wait_for(self, *args):
            return {"state": state, "detail": "<diagnostic>", "platform": "macOS", "image": None}
    monkeypatch.setattr(bot, "SESSION", {"target": "mac"})
    monkeypatch.setattr(bot, "publish_tracked", lambda *args, **kwargs: (True, "ticket"))
    monkeypatch.setattr(bot, "screenshot_collector", Collector())
    monkeypatch.setattr(bot, "_callback_result_access_or_report", allowed)
    monkeypatch.setattr(bot, "_replace_callback_message", render)
    asyncio.run(bot.on_cmd_screenshot(SimpleNamespace(answer=answer)))
    assert instruction in shown[0]
    assert "&lt;diagnostic&gt;" in shown[0]
    assert "разрешено" not in shown[0]
    if state == "permission_denied":
        assert "Конфиденциальность и безопасность" in shown[0]
        assert "могут отличаться" in shown[0]
        assert "на самом устройстве" in shown[0]


def test_response_timeout_does_not_assert_device_offline(monkeypatch):
    shown = []
    async def answer(*args, **kwargs): pass
    async def allowed(*args, **kwargs): return True
    async def render(cq, text, **kwargs): shown.append(text)
    class Collector:
        async def wait_for(self, *args): return None
    monkeypatch.setattr(bot, "SESSION", {"target": "mac"})
    monkeypatch.setattr(bot, "publish_tracked", lambda *args, **kwargs: (True, "ticket"))
    monkeypatch.setattr(bot, "screenshot_collector", Collector())
    monkeypatch.setattr(bot, "_callback_result_access_or_report", allowed)
    monkeypatch.setattr(bot, "_replace_callback_message", render)
    asyncio.run(bot.on_cmd_screenshot(SimpleNamespace(answer=answer)))
    assert "офлайн" not in shown[0]
    assert "достав" in shown[0]


@pytest.mark.parametrize("case", ["invalid", "empty", "oversized", "delivery", "success", "failure_with_image"])
def test_modern_image_validation_and_delivery(monkeypatch, case):
    shown, photos = [], []
    buffer = io.BytesIO()
    Image.new("RGB", (10, 10)).save(buffer, format="JPEG")
    encoded = base64.b64encode(buffer.getvalue()).decode()
    if case == "invalid": encoded = base64.b64encode(b"not an image").decode()
    if case == "empty": encoded = ""
    if case == "oversized": encoded = "A" * (40 * 1024 * 1024 + 5)
    async def answer(*args, **kwargs): pass
    async def allowed(*args, **kwargs): return True
    async def render(cq, text, **kwargs): shown.append(text)
    async def media(cq, sender, **kwargs):
        if case == "delivery": raise RuntimeError("Telegram delivery failed")
        photos.append(kwargs["photo"].data)
    class Collector:
        async def wait_for(self, *args):
            return {"state": "command_failed" if case == "failure_with_image" else "success", "detail": "diagnostic", "image": encoded}
    monkeypatch.setattr(bot, "SESSION", {"target": "mac"})
    monkeypatch.setattr(bot, "publish_tracked", lambda *args, **kwargs: (True, "ticket"))
    monkeypatch.setattr(bot, "screenshot_collector", Collector())
    monkeypatch.setattr(bot, "_callback_result_access_or_report", allowed)
    monkeypatch.setattr(bot, "_replace_callback_message", render)
    monkeypatch.setattr(bot, "_replace_callback_with_media", media)
    asyncio.run(bot.on_cmd_screenshot(SimpleNamespace(answer=answer)))
    if case == "success":
        assert photos == [buffer.getvalue()]
    else:
        assert not photos
        assert shown
        if case == "invalid":
            assert "прочитать" in shown[0]
        if case == "delivery":
            assert "достав" in shown[0]


def test_bulk_failures_are_owner_visible(monkeypatch):
    shown = []
    async def answer(*args, **kwargs): pass
    async def render(cq, text, **kwargs): shown.append(text)
    async def results(*args):
        return [{"device_id": "mac", "state": "permission_denied", "platform": "macOS", "detail": "denied", "image": None}]
    monkeypatch.setattr(bot.transport, "publish_command", lambda *args: True)
    monkeypatch.setattr(bot.multi_screenshot, "reset", lambda: None)
    monkeypatch.setattr(bot.multi_screenshot, "wait_window", results)
    monkeypatch.setattr(bot, "_replace_callback_message", render)
    monkeypatch.setattr(bot, "target_label", lambda value: value)
    asyncio.run(bot.on_all_screenshot(SimpleNamespace(answer=answer)))
    assert "mac" in shown[0]
    assert "Запись экрана" in shown[0]
    assert "могут отличаться" in shown[0]
