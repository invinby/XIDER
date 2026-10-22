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


def test_masked_screenshot_covers_normalized_rectangle_without_mutating_source():
    source = io.BytesIO()
    Image.new("RGB", (20, 10), (240, 30, 20)).save(source, format="PNG")
    original = source.getvalue()

    masked = bot._mask_screenshot_regions(original, [(0.25, 0.2, 0.75, 0.8)])

    with Image.open(io.BytesIO(masked)) as result:
        assert result.size == (20, 10)
        assert result.convert("RGB").getpixel((5, 2)) == (0, 0, 0)
        assert result.convert("RGB").getpixel((14, 7)) == (0, 0, 0)
        assert result.convert("RGB").getpixel((4, 2)) == (240, 30, 20)
        assert result.convert("RGB").getpixel((15, 2)) == (240, 30, 20)
    assert source.getvalue() == original


def test_mask_coordinate_parser_accepts_bounded_normalized_rectangles():
    assert bot._parse_screenshot_mask_regions("0.1,0.2,0.4,0.6; 0.55,0.1,0.9,0.3") == [
        (0.1, 0.2, 0.4, 0.6), (0.55, 0.1, 0.9, 0.3),
    ]


@pytest.mark.parametrize("value", [
    "", "0.1,0.2,0.3", "0.1,0.2,0.4,0.3;", "0,0,1,1;0,0,1,1;0,0,1,1;0,0,1,1;0,0,1,1;0,0,1,1;0,0,1,1;0,0,1,1;0,0,1,1",
    "-0.1,0.2,0.4,0.6", "0.1,0.2,1.01,0.6", "NaN,0.2,0.4,0.6",
])
def test_mask_coordinate_parser_rejects_malformed_or_unbounded_regions(value):
    with pytest.raises(ValueError):
        bot._parse_screenshot_mask_regions(value)


@pytest.mark.parametrize("regions", [
    [],
    [(0.4, 0.2, 0.3, 0.8)],
    [(-0.1, 0.2, 0.3, 0.8)],
    [(0.1, 0.2, 1.1, 0.8)],
    [(0.1, 0.2, float("nan"), 0.8)],
    [(0.1, 0.2, 0.8, 0.9)] * 9,
])
def test_masked_screenshot_rejects_empty_invalid_or_excessive_regions(regions):
    source = io.BytesIO()
    Image.new("RGB", (20, 10), (240, 30, 20)).save(source, format="PNG")

    with pytest.raises(ValueError):
        bot._mask_screenshot_regions(source.getvalue(), regions)


def test_masked_screenshot_rejects_oversized_input_before_image_decode():
    with pytest.raises(ValueError, match="size limit"):
        bot._mask_screenshot_regions(b"x" * (30 * 1024 * 1024 + 1), [(0.1, 0.1, 0.2, 0.2)])


def test_mask_cache_is_owner_token_phase_bound_and_expires(monkeypatch):
    now = [100.0]
    monkeypatch.setattr(bot.time, "time", lambda: now[0])
    bot._clear_screenshot_mask_cache(123)
    bot._store_screenshot_mask_cache(123, "device-a", "token-a", b"masked", phase="preview")

    assert bot._get_screenshot_mask_cache(123, "token-a", phase="preview")["image"] == b"masked"
    assert bot._get_screenshot_mask_cache(124, "token-a", phase="preview") is None
    assert bot._get_screenshot_mask_cache(123, "wrong", phase="preview") is None
    assert bot._get_screenshot_mask_cache(123, "token-a", phase="raw") is None
    now[0] += bot._SCREENSHOT_MASK_TTL_SECONDS + 1
    assert bot._get_screenshot_mask_cache(123, "token-a", phase="preview") is None


def test_owner_mask_flow_keeps_raw_image_out_of_telegram_and_previews_only_masked_copy(monkeypatch):
    source = io.BytesIO()
    Image.new("RGB", (20, 10), (220, 10, 10)).save(source, format="PNG")
    raw = source.getvalue()
    encoded = base64.b64encode(raw).decode("ascii")

    class State:
        data = {}
        current = None
        async def clear(self):
            self.data = {}
            self.current = None
        async def set_state(self, value):
            self.current = value.state
        async def update_data(self, **values):
            self.data.update(values)
        async def get_data(self):
            return dict(self.data)
        async def get_state(self):
            return self.current

    class Collector:
        async def wait_for(self, *_args):
            return {"state": "success", "image": encoded}

    state = State()
    rendered, previews = [], []
    async def answer(*_args, **_kwargs): pass
    async def allowed(*_args, **_kwargs): return True
    async def render(_cq, text, **kwargs): rendered.append((text, kwargs))
    async def replace_photo(_message, image_bytes, **kwargs):
        previews.append((image_bytes, kwargs))
        return 99
    monkeypatch.setattr(bot, "SESSION", {"target": "device-a"})
    monkeypatch.setattr(bot, "publish_tracked", lambda *_args, **_kwargs: (True, "request-a"))
    monkeypatch.setattr(bot, "screenshot_collector", Collector())
    monkeypatch.setattr(bot, "_callback_result_access_or_report", allowed)
    monkeypatch.setattr(bot, "_replace_callback_message", render)
    monkeypatch.setattr(bot, "_pending_device_input_still_allowed", lambda *_args: True)
    monkeypatch.setattr(bot, "_replace_user_card_with_photo", replace_photo)
    monkeypatch.setattr(bot.devices, "get", lambda _target: {"os": "Windows"})
    monkeypatch.setattr(bot.bot, "send_photo", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("raw screenshot must not be sent")))
    bot._clear_screenshot_mask_cache(bot.ADMIN_ID)

    callback = SimpleNamespace(
        answer=answer, from_user=SimpleNamespace(id=bot.ADMIN_ID), data="screenshot:mask",
        message=SimpleNamespace(chat=SimpleNamespace(id=77)),
    )
    asyncio.run(bot.on_screenshot_mask_start(callback, state))
    token = state.data["mask_token"]
    assert bot._get_screenshot_mask_cache(bot.ADMIN_ID, token, phase="raw")["image"] == raw
    assert not previews

    message = SimpleNamespace(from_user=SimpleNamespace(id=bot.ADMIN_ID), text="0.25,0.2,0.75,0.8", chat=SimpleNamespace(id=77))
    asyncio.run(bot.on_screenshot_mask_input(message, state))

    assert state.current == bot.Form.wait_screenshot_mask_confirm.state
    assert len(previews) == 1
    with Image.open(io.BytesIO(previews[0][0])) as preview:
        assert preview.size == (20, 10)
        assert preview.convert("RGB").getpixel((5, 2)) == (0, 0, 0)
        assert preview.convert("RGB").getpixel((1, 1)) != (0, 0, 0)
    entry = bot._get_screenshot_mask_cache(bot.ADMIN_ID, token, phase="preview")
    assert entry and entry["final_image"] and "исходник" not in str(entry["image"]).lower()
    assert "не отправлен" in rendered[0][0]
    callbacks = {button.callback_data for row in previews[0][1]["reply_markup"].inline_keyboard for button in row}
    assert f"screenshot:mask:send:{token}" in callbacks
    assert "screenshot:mask:cancel" in callbacks

    delivered = []
    async def edit_media(**kwargs): delivered.append(kwargs)
    monkeypatch.setattr(bot, "_simple_command_still_allowed", lambda *_args: True)
    monkeypatch.setattr(bot.bot, "edit_message_media", edit_media)
    monkeypatch.setattr(bot.ui_cards, "set_card", lambda *_args: None)
    confirm = SimpleNamespace(
        answer=answer, from_user=SimpleNamespace(id=bot.ADMIN_ID),
        data=f"screenshot:mask:send:{token}",
        message=SimpleNamespace(chat=SimpleNamespace(id=77), message_id=99),
    )
    asyncio.run(bot.on_screenshot_mask_send(confirm, state))
    assert len(delivered) == 1
    final_bytes = delivered[0]["media"].media.data
    with Image.open(io.BytesIO(final_bytes)) as final_image:
        assert final_image.convert("RGB").getpixel((5, 2)) == (0, 0, 0)
        assert final_image.convert("RGB").getpixel((1, 1)) == (220, 10, 10)
    assert bot._get_screenshot_mask_cache(bot.ADMIN_ID, token, phase="preview") is None


@pytest.mark.parametrize("state,instruction", [
    ("permission_denied", "Запись экрана"), ("session_unavailable", "сеанс"),
    ("tool_missing", "компонент"), ("command_failed", "команда"),
    ("file_empty", "пуст"), ("file_oversized", "размер"),
    ("file_unreadable", "прочитать"), ("timeout", "время"),
    ("transport_failed", "достав"), ("unknown", "неизвест"),
])
def test_handler_maps_failure_to_safe_help(monkeypatch, state, instruction):
    shown, rendered = [], []
    async def answer(*args, **kwargs): pass
    async def allowed(*args, **kwargs): return True
    async def render(cq, text, **kwargs):
        shown.append(text)
        rendered.append(kwargs)
    class Collector:
        async def wait_for(self, *args):
            return {"state": state, "detail": "<diagnostic>", "platform": "macOS", "image": None}
    monkeypatch.setattr(bot, "SESSION", {"target": "mac"})
    monkeypatch.setattr(bot, "publish_tracked", lambda *args, **kwargs: (True, "ticket"))
    monkeypatch.setattr(bot, "screenshot_collector", Collector())
    monkeypatch.setattr(bot, "_callback_result_access_or_report", allowed)
    monkeypatch.setattr(bot, "_replace_callback_message", render)
    monkeypatch.setattr(bot, "get_user_role", lambda _user: bot.Role.OWNER)
    monkeypatch.setattr(
        bot.devices, "get",
        lambda _target: {"os": "macOS 27", "capabilities": {"commands": ["permission_settings"]}},
    )
    asyncio.run(bot.on_cmd_screenshot(SimpleNamespace(answer=answer, from_user=SimpleNamespace(id=bot.ADMIN_ID), data="cmd:screenshot")))
    assert instruction in shown[0]
    assert "&lt;diagnostic&gt;" in shown[0]
    assert "разрешено" not in shown[0]
    if state == "permission_denied":
        assert "Конфиденциальность и безопасность" in shown[0]
        assert "могут отличаться" in shown[0]
        assert "на самом устройстве" in shown[0]
        callbacks = [
            button.callback_data
            for row in rendered[0]["reply_markup"].inline_keyboard
            for button in row
        ]
        assert "permsettings:screen_recording" in callbacks


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


@pytest.mark.parametrize("role,os_name,state,commands,expected", [
    ("owner", "macOS 27", "permission_denied", ["permission_settings"], True),
    ("owner", "macOS 27", "file_empty", ["permission_settings"], True),
    ("owner", "macOS 27", "permission_denied", [], False),
    ("owner", "Windows 11", "permission_denied", ["permission_settings"], False),
    ("user", "macOS 27", "permission_denied", ["permission_settings"], False),
    ("owner", "macOS 27", "timeout", ["permission_settings"], False),
])
def test_mac_capture_failures_offer_owner_only_screen_recording_settings(
    monkeypatch, role, os_name, state, commands, expected,
):
    monkeypatch.setattr(bot, "get_user_role", lambda _user: getattr(bot.Role, role.upper()))
    monkeypatch.setattr(
        bot.devices, "get",
        lambda _target: {"os": os_name, "capabilities": {"commands": commands}},
    )

    markup = bot._screenshot_failure_keyboard(
        {"state": state}, target="mac-a", user_id=100,
    )
    callbacks = [button.callback_data for row in markup.inline_keyboard for button in row]

    assert ("permsettings:screen_recording" in callbacks) is expected
    assert callbacks[-1] == "back:device"


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
