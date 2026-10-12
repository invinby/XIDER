import asyncio
import base64
import io
import json
import struct
import zipfile
import zlib
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
    asyncio.run(bot.on_cmd_screenshot(SimpleNamespace(answer=answer, from_user=SimpleNamespace(id=bot.ADMIN_ID), data="cmd:screenshot")))
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
    asyncio.run(bot.on_cmd_screenshot(SimpleNamespace(answer=answer, from_user=SimpleNamespace(id=bot.ADMIN_ID), data="cmd:screenshot")))
    assert "офлайн" not in shown[0]
    assert "достав" in shown[0]


@pytest.mark.parametrize("case", ["invalid", "empty", "oversized", "delivery", "success", "png", "failure_with_image"])
def test_modern_image_validation_and_delivery(monkeypatch, case):
    shown, photos = [], []
    buffer = io.BytesIO()
    image_format = "PNG" if case == "png" else "JPEG"
    Image.new("RGB", (10, 10)).save(buffer, format=image_format)
    encoded = base64.b64encode(buffer.getvalue()).decode()
    if case == "invalid": encoded = base64.b64encode(b"not an image").decode()
    if case == "empty": encoded = ""
    if case == "oversized": encoded = "A" * (40 * 1024 * 1024 + 5)
    async def answer(*args, **kwargs): pass
    async def allowed(*args, **kwargs): return True
    async def render(cq, text, **kwargs): shown.append(text)
    async def media(cq, sender, **kwargs):
        if case == "delivery": raise RuntimeError("Telegram delivery failed")
        photos.append((kwargs["photo"].data, kwargs["photo"].filename))
    class Collector:
        async def wait_for(self, *args):
            return {"state": "command_failed" if case == "failure_with_image" else "success", "detail": "diagnostic", "image": encoded}
    monkeypatch.setattr(bot, "SESSION", {"target": "mac"})
    monkeypatch.setattr(bot, "publish_tracked", lambda *args, **kwargs: (True, "ticket"))
    monkeypatch.setattr(bot, "screenshot_collector", Collector())
    monkeypatch.setattr(bot, "_callback_result_access_or_report", allowed)
    monkeypatch.setattr(bot, "_replace_callback_message", render)
    monkeypatch.setattr(bot, "_replace_callback_with_media", media)
    asyncio.run(bot.on_cmd_screenshot(SimpleNamespace(answer=answer, from_user=SimpleNamespace(id=bot.ADMIN_ID), data="cmd:screenshot")))
    if case in {"success", "png"}:
        expected_filename = "screenshot.png" if case == "png" else "screenshot.jpg"
        assert photos == [(buffer.getvalue(), expected_filename)]
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
    asyncio.run(bot.on_all_screenshot(SimpleNamespace(answer=answer, from_user=SimpleNamespace(id=bot.ADMIN_ID), data="cmd:screenshot")))
    assert "mac" in shown[0]
    assert "Запись экрана" in shown[0]
    assert "могут отличаться" in shown[0]


def screenshot_bytes(case):
    buffer = io.BytesIO()
    Image.new("RGB", (100, 100)).save(buffer, format="JPEG")
    if case == "truncated":
        return buffer.getvalue()[:650]
    if case in {"bomb", "dimensions"}:
        png = io.BytesIO()
        Image.new("RGB", (1, 1)).save(png, format="PNG")
        data = png.getvalue()
        dimensions = (100000, 100000) if case == "bomb" else (10000, 5000)
        ihdr = struct.pack(">II", *dimensions) + data[24:29]
        return data[:16] + ihdr + struct.pack(">I", zlib.crc32(b"IHDR" + ihdr)) + data[33:]
    return buffer.getvalue()


@pytest.mark.parametrize("case", ["truncated", "bomb", "dimensions", "valid", "legacy"])
def test_single_full_decode_and_dimension_gate(monkeypatch, case):
    shown, photos, decoded_sizes = [], [], []
    data = screenshot_bytes(case)
    original_load = Image.Image.load
    def load(image, *args, **kwargs):
        decoded_sizes.append(image.size)
        return original_load(image, *args, **kwargs)
    monkeypatch.setattr(Image.Image, "load", load)
    async def answer(*args, **kwargs): pass
    async def allowed(*args, **kwargs): return True
    async def render(cq, text, **kwargs): shown.append(text)
    async def media(cq, sender, **kwargs): photos.append(kwargs["photo"].data)
    class Collector:
        async def wait_for(self, *args):
            value = {"image": base64.b64encode(data).decode()}
            if case != "legacy": value.update(state="success", detail="captured")
            return value
    monkeypatch.setattr(bot, "SESSION", {"target": "mac"})
    monkeypatch.setattr(bot, "publish_tracked", lambda *args, **kwargs: (True, "ticket"))
    monkeypatch.setattr(bot, "screenshot_collector", Collector())
    monkeypatch.setattr(bot, "_callback_result_access_or_report", allowed)
    monkeypatch.setattr(bot, "_replace_callback_message", render)
    monkeypatch.setattr(bot, "_replace_callback_with_media", media)
    asyncio.run(bot.on_cmd_screenshot(SimpleNamespace(answer=answer, from_user=SimpleNamespace(id=bot.ADMIN_ID), data="cmd:screenshot")))
    if case in {"valid", "legacy"}:
        assert photos == [data]
    else:
        assert not photos
        assert "прочитать" in shown[0] or "размер" in shown[0]
    if case == "dimensions":
        assert (10000, 5000) not in decoded_sizes, "huge dimensions reached pixel decode"


@pytest.mark.parametrize("bad", ["truncated", "bomb", "dimensions"])
def test_bulk_bad_image_keeps_valid_peer(monkeypatch, bad):
    deliveries = []
    good = screenshot_bytes("valid")
    async def answer(*args, **kwargs): pass
    async def media(cq, sender, **kwargs): deliveries.append(kwargs)
    async def results(*args):
        return [{"device_id": "bad", "state": "success", "detail": "captured", "image": base64.b64encode(screenshot_bytes(bad)).decode()},
                {"device_id": "good", "state": "success", "detail": "captured", "image": base64.b64encode(good).decode()}]
    monkeypatch.setattr(bot.transport, "publish_command", lambda *args: True)
    monkeypatch.setattr(bot.multi_screenshot, "reset", lambda: None)
    monkeypatch.setattr(bot.multi_screenshot, "wait_window", results)
    monkeypatch.setattr(bot, "_replace_callback_with_media", media)
    monkeypatch.setattr(bot, "target_label", lambda value: value)
    asyncio.run(bot.on_all_screenshot(SimpleNamespace(answer=answer, message=SimpleNamespace(chat=SimpleNamespace(id=1)))))
    assert len(deliveries) == 1
    with zipfile.ZipFile(io.BytesIO(deliveries[0]["document"].data)) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        assert [item["device_id"] for item in manifest] == ["good"]
        assert archive.read(manifest[0]["file"]) == good
    assert "bad" in deliveries[0]["caption"]
    assert "прочитать" in deliveries[0]["caption"] or "размер" in deliveries[0]["caption"]
