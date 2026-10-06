"""Тесты UI-редизайна бота: device_card и новые категорийные меню (2026-09-05)."""

import asyncio
import ast
import base64
import time
from pathlib import Path

import pytest

import bot

NINE_CATEGORIES = {
    "cat:media", "cat:screen", "cat:input", "cat:system",
    "cat:network", "cat:files", "cat:terminal", "cat:power", "cat:pranks",
}


class FakeDevices:
    """Заглушка DeviceStore: без доступа к диску, только нужные методы."""

    def __init__(self, devices=None):
        self._devices = dict(devices or {})

    def all(self):
        return dict(self._devices)

    def get(self, device_id):
        return self._devices.get(device_id)

    def get_favorites(self, device_id):
        info = self._devices.get(device_id) or {}
        return list(info.get("favorites", []))


def _callback_data(markup):
    """Все callback_data из InlineKeyboardMarkup одной строкой."""
    return [b.callback_data for row in markup.inline_keyboard for b in row]


def _setup(monkeypatch, devices):
    monkeypatch.setattr(bot, "devices", FakeDevices(devices))


def test_static_xlex_keys_exist_and_keyboard_labels_are_not_hardcoded():
    tree = ast.parse(Path(bot.__file__).read_text(encoding="utf-8"))
    lex_keys = set()
    nav_keys = set()
    literal_button_labels = set()

    def call_name(node):
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            return node.attr
        return ""

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        function = call_name(node.func)
        if function in {"_lex", "_nav"} and node.args:
            key = node.args[0]
            if isinstance(key, ast.Constant) and isinstance(key.value, str):
                (lex_keys if function == "_lex" else nav_keys).add(key.value)
        if function in {"button", "_action_button"}:
            text_arg = next(
                (item.value for item in node.keywords if item.arg == "text"),
                None,
            )
            if isinstance(text_arg, ast.Constant) and isinstance(text_arg.value, str):
                literal_button_labels.add(text_arg.value)

    assert lex_keys <= set(bot.xlex.COPY)
    assert nav_keys <= set(bot.xlex.NAV_KEYS)
    # Pager glyphs have no wording to translate; all other literal labels belong in X-LEX.
    assert literal_button_labels == {"◀️", "▶️"}


def test_user_fsm_prompts_bind_the_initiating_action_and_device():
    tree = ast.parse(Path(bot.__file__).read_text(encoding="utf-8"))
    bound_states = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
            continue
        if node.func.id != "_start_authorized_input" or len(node.args) < 2:
            continue
        state_arg = node.args[1]
        if isinstance(state_arg, ast.Attribute) and isinstance(state_arg.value, ast.Name):
            if state_arg.value.id == "Form":
                bound_states.add(state_arg.attr)

    # Every user-capable free-text/file input revalidates its original callback
    # and device when the user submits the next message. Admin-only forms such
    # as `wait_admin` are intentionally not in this set.
    assert {
        "wait_open_app", "wait_url", "wait_url_count", "wait_url_favorite_name",
        "wait_text", "wait_sound", "wait_clipset",
        "wait_path", "wait_find", "wait_fun_text", "wait_fun_hotkey",
        "wait_fun_wallpaper", "wait_wallpaper_photo", "wait_fun_spam", "wait_shout",
        "wait_brightness", "wait_prockill",
    } <= bound_states


def test_random_wallpaper_feedback_has_six_distinct_voices():
    keys = (
        "wallpaper_photo_guide",
        "wallpaper_select_device",
        "wallpaper_photo_uploading",
        "wallpaper_random_waiting",
        "wallpaper_random_success",
        "wallpaper_random_sent",
        "wallpaper_random_publish_failed",
        "wallpaper_random_error",
    )
    for key in keys:
        assert key in bot.xlex.COPY
        rendered = {
            style: bot.xlex.render(key, style, device="Laptop", error="offline")
            for style in bot.xlex.STYLES
        }
        assert len(set(rendered.values())) == len(bot.xlex.STYLES), key


def test_wallpaper_photo_flow_reuses_the_user_card_instead_of_sending_status_messages():
    source = Path(bot.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    expected_helpers = {
        "on_wallpaper_photo_guide": "_replace_callback_message",
        "on_photo_wallpaper_message": "_replace_user_card",
    }

    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        expected_helper = expected_helpers.get(node.name)
        if not expected_helper:
            continue
        calls = [
            call.func.id
            for call in ast.walk(node)
            if isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
        ]
        assert expected_helper in calls, node.name
        assert not any(
            isinstance(call, ast.Call)
            and isinstance(call.func, ast.Attribute)
            and call.func.attr == "answer"
            and (
                isinstance(call.func.value, ast.Name)
                and call.func.value.id == "message"
                or isinstance(call.func.value, ast.Attribute)
                and call.func.value.attr == "message"
            )
            for call in ast.walk(node)
        ), node.name

    functions = {
        node.name: node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    guide_calls = {
        call.func.id
        for call in ast.walk(functions["on_wallpaper_photo_guide"])
        if isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
    }
    assert "_start_authorized_input" in guide_calls

    photo_route = next(
        decorator
        for decorator in functions["on_photo_wallpaper_message"].decorator_list
        if isinstance(decorator, ast.Call)
    )
    assert "Form.wait_wallpaper_photo" in ast.get_source_segment(source, photo_route)

    message_handlers = [
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and any(
            isinstance(decorator, ast.Call)
            and isinstance(decorator.func, ast.Attribute)
            and decorator.func.attr == "message"
            for decorator in node.decorator_list
        )
    ]
    assert message_handlers[-1] == "on_denied"


def test_photo_wallpaper_upload_publishes_to_bound_device_when_access_is_live(monkeypatch):
    from types import SimpleNamespace

    cards = []
    published = []

    class FakeState:
        async def get_data(self):
            return {
                "authorization_callback": "cmd:wallpaper_photo_guide",
                "authorization_target": "device-1",
            }

        async def clear(self):
            pass

    class FakeTelegramBot:
        async def get_file(self, _file_id):
            return SimpleNamespace(file_path="photo.jpg")

        async def download_file(self, _path, destination):
            destination.write(b"photo-data")

    class FakeMessage:
        from_user = SimpleNamespace(id=20)
        bot = FakeTelegramBot()
        photo = [SimpleNamespace(file_id="photo-id")]

    class FakeCollector:
        def reset(self):
            pass

        async def wait(self, _timeout):
            return {"ok": True}

    async def replace(_message, text, reply_markup=None):
        cards.append((text, reply_markup))

    monkeypatch.setattr(bot, "_replace_user_card", replace)
    monkeypatch.setattr(bot, "_device_action_still_allowed", lambda *_args: True)
    monkeypatch.setattr(bot, "target_label", lambda target: f"Device {target}")
    monkeypatch.setattr(bot, "wallpaper_menu", lambda: "wallpaper-menu")
    monkeypatch.setattr(bot, "fun_text_collector", FakeCollector())
    monkeypatch.setattr(
        bot,
        "publish",
        lambda action, **kwargs: published.append((action, kwargs)) or True,
    )

    asyncio.run(bot.on_photo_wallpaper_message(FakeMessage(), FakeState()))

    assert len(published) == 1
    action, kwargs = published[0]
    assert action == "wallpaper_set"
    assert kwargs["_target"] == "device-1"
    assert base64.b64decode(kwargs["b64"]) == b"photo-data"
    assert cards[-1][0] == bot._lex_html(
        "wallpaper_photo_installed", device="Device device-1", size=0
    )


def test_photo_wallpaper_upload_aborts_if_access_is_revoked_during_download(monkeypatch):
    from types import SimpleNamespace

    cards = []
    published = []
    allowed = True

    class FakeState:
        async def get_data(self):
            return {
                "authorization_callback": "cmd:wallpaper_photo_guide",
                "authorization_target": "device-1",
            }

        async def clear(self):
            pass

    class FakeTelegramBot:
        async def get_file(self, _file_id):
            return SimpleNamespace(file_path="photo.jpg")

        async def download_file(self, _path, destination):
            nonlocal allowed
            destination.write(b"photo-data")
            allowed = False

    class FakeMessage:
        from_user = SimpleNamespace(id=20)
        bot = FakeTelegramBot()
        photo = [SimpleNamespace(file_id="photo-id")]

    async def replace(_message, text, reply_markup=None):
        cards.append((text, reply_markup))

    monkeypatch.setattr(bot, "_replace_user_card", replace)
    monkeypatch.setattr(
        bot,
        "_device_action_still_allowed",
        lambda *_args: allowed,
    )
    monkeypatch.setattr(bot, "target_label", lambda target: f"Device {target}")
    monkeypatch.setattr(bot, "wallpaper_menu", lambda: "wallpaper-menu")
    monkeypatch.setattr(
        bot,
        "publish",
        lambda action, **kwargs: published.append((action, kwargs)) or True,
    )

    asyncio.run(bot.on_photo_wallpaper_message(FakeMessage(), FakeState()))

    assert published == []
    assert bot._lex("wallpaper_photo_guide_required") in cards[-1][0]


def test_unrequested_photo_is_rejected_without_mqtt_publish(monkeypatch):
    from types import SimpleNamespace

    cards = []

    class FakeMessage:
        from_user = SimpleNamespace(id=20)

    async def replace(_message, text, reply_markup=None):
        cards.append((text, reply_markup))

    monkeypatch.setattr(bot, "_replace_user_card", replace)
    monkeypatch.setattr(
        bot,
        "publish",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("unsolicited photo must never publish")
        ),
    )

    asyncio.run(bot.on_unrequested_photo(FakeMessage()))
    assert bot._lex("wallpaper_photo_guide_required") in cards[0][0]


@pytest.mark.parametrize(
    ("publish_ok", "result", "expected_key"),
    [
        (True, {"ok": True}, "wallpaper_random_success"),
        (True, {"ok": False, "error": "<offline>"}, "wallpaper_random_error"),
        (True, None, "wallpaper_random_sent"),
        (False, None, "wallpaper_random_publish_failed"),
    ],
)
def test_random_wallpaper_updates_one_card_and_escapes_device_data(
    monkeypatch, publish_ok, result, expected_key
):
    from types import SimpleNamespace

    rendered = []
    answered = []

    class FakeCollector:
        def reset(self):
            pass

        async def wait(self, _timeout):
            return result

    class FakeMessage:
        chat = SimpleNamespace(id=1)
        message_id = 2

        async def answer(self, *_args, **_kwargs):
            raise AssertionError("random wallpaper must edit the existing card")

    class FakeCallback:
        data = "cmd:wallpaper_random_meme"
        from_user = SimpleNamespace(id=3)
        message = FakeMessage()

        async def answer(self, *args, **kwargs):
            answered.append((args, kwargs))

    async def replace(_cq, text, **kwargs):
        rendered.append((text, kwargs))

    monkeypatch.setitem(bot.SESSION, "target", "device-1")
    monkeypatch.setattr(bot, "target_label", lambda _target: "Laptop <test>")
    monkeypatch.setattr(bot, "wallpaper_menu", lambda: "wallpaper-menu")
    monkeypatch.setattr(bot, "publish", lambda *_args, **_kwargs: publish_ok)
    monkeypatch.setattr(bot, "fun_text_collector", FakeCollector())
    monkeypatch.setattr(bot, "_replace_callback_message", replace)
    monkeypatch.setattr(
        bot.bot_settings,
        "get",
        lambda key, default=None: "xtech" if key == "ui_style" else default,
    )

    asyncio.run(bot.on_wallpaper_random_meme(FakeCallback()))

    assert [text for text, _kwargs in rendered][0].startswith("🎲 <b>")
    assert len(rendered) == 2
    final_text, final_kwargs = rendered[-1]
    assert "&lt;test&gt;" in final_text
    assert final_kwargs["reply_markup"] == "wallpaper-menu"
    if expected_key == "wallpaper_random_error":
        assert "&lt;offline&gt;" in final_text
    else:
        assert final_text.startswith(
            {
                "wallpaper_random_success": "✅",
                "wallpaper_random_sent": "📤",
                "wallpaper_random_publish_failed": "⚠️",
            }[
                expected_key
            ]
        )
    assert answered == [((), {})]


def test_text_command_flows_reuse_the_existing_chat_card():
    tree = ast.parse(Path(bot.__file__).read_text(encoding="utf-8"))
    target_functions = {
        "on_cmd_shell": "_replace_callback_message",
        "on_shell_ok": "_replace_callback_message",
        "on_shell_input": "_replace_user_card",
        "on_cmd_open_app": "_replace_callback_message",
        "on_openapp_ok": "_replace_callback_message",
        "on_open_app_input": "_replace_user_card",
        "on_cmd_url": "_replace_callback_message",
        "on_url_input": "_replace_user_card",
        "on_cmd_text": "_replace_callback_message",
        "on_text_input": "_replace_user_card",
        "on_cmd_sound": "_replace_callback_message",
        "on_sound_input": "_replace_user_card",
        "on_cmd_clipset": "_replace_callback_message",
        "on_clipset_input": "_replace_user_card",
        "on_fun_type": "_replace_callback_message",
        "on_fun_text_input": "_replace_user_card",
        "on_fun_hotkey": "_replace_callback_message",
        "on_fun_hotkey_input": "_replace_user_card",
        "on_fun_wallpaper": "_replace_callback_message",
        "on_fun_wallpaper_input": "_replace_user_card",
        "on_cmd_brightness": "_replace_callback_message",
        "on_brightness_input": "_replace_user_card",
        "on_cmd_prockillname": "_replace_callback_message",
        "on_prockill_input": "_replace_user_card",
    }
    prompt_functions = {
        "on_cmd_shell", "on_shell_ok", "on_cmd_open_app", "on_openapp_ok",
        "on_cmd_url", "on_cmd_text", "on_cmd_sound",
        "on_cmd_clipset", "on_fun_type", "on_fun_hotkey",
        "on_fun_wallpaper", "on_cmd_brightness", "on_cmd_prockillname",
    }
    input_functions = {
        "on_shell_input", "on_open_app_input", "on_url_input",
        "on_text_input", "on_sound_input",
        "on_clipset_input", "on_fun_text_input", "on_fun_hotkey_input",
        "on_fun_wallpaper_input", "on_brightness_input", "on_prockill_input",
    }
    functions = {
        node.name: node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }

    for name, required_helper in target_functions.items():
        calls = [
            node
            for node in ast.walk(functions[name])
            if isinstance(node, ast.Call)
        ]
        assert any(
            isinstance(call.func, ast.Name) and call.func.id == required_helper
            for call in calls
        ), name
        if name in prompt_functions:
            assert any(
                (
                    isinstance(call.func, ast.Attribute)
                    and call.func.attr == "update_data"
                    and any(keyword.arg == "command_target" for keyword in call.keywords)
                )
                or (
                    isinstance(call.func, ast.Name)
                    and call.func.id == "_start_authorized_input"
                )
                for call in calls
            ), name
            assert any(
                isinstance(call.func, ast.Name)
                and call.func.id == "_lex"
                and call.args
                and isinstance(call.args[0], ast.Constant)
                and call.args[0].value == "single_device_only"
                for call in calls
            ), name
        if name in input_functions:
            assert any(
                isinstance(call.func, ast.Attribute) and call.func.attr == "get_data"
                for call in calls
            ), name
        for call in calls:
            if not isinstance(call.func, ast.Attribute) or call.func.attr != "answer":
                continue
            receiver = call.func.value
            is_chat_send = isinstance(receiver, ast.Name) and receiver.id == "message"
            is_callback_chat_send = (
                isinstance(receiver, ast.Attribute)
                and receiver.attr == "message"
                and isinstance(receiver.value, ast.Name)
                and receiver.value.id == "cq"
            )
            assert not (is_chat_send or is_callback_chat_send), name


@pytest.mark.parametrize(
    ("handler_name", "action", "input_text"),
    [
        ("on_shell_input", "shell", "whoami"),
        ("on_open_app_input", "open_app", "Calculator"),
        ("on_text_input", "notify", "hello"),
        ("on_sound_input", "sound", "beep"),
        ("on_clipset_input", "clipboard_set", "note for clipboard"),
        ("on_fun_text_input", "type_text", "typed text"),
        ("on_fun_hotkey_input", "hotkey", "ctrl+c"),
        ("on_fun_wallpaper_input", "wallpaper_set", "https://example.com/image.png"),
        ("on_brightness_input", "display_brightness", "65"),
        ("on_prockill_input", "proc_kill_name", "example.exe"),
    ],
)
def test_text_command_inputs_keep_the_device_chosen_when_prompt_opened(
    monkeypatch, handler_name, action, input_text
):
    from types import SimpleNamespace

    class FakeState:
        cleared = False

        async def get_data(self):
            return {"command_target": "pinned-device"}

        async def clear(self):
            self.cleared = True

    publications = []
    rendered = []

    async def replace_card(_message, text, reply_markup=None):
        rendered.append(text)

    def publish(command_name, **kwargs):
        publications.append((command_name, kwargs))
        return True

    def publish_tracked(command_name, **kwargs):
        publications.append((command_name, kwargs))
        return True, "command-id"

    async def shell_result(_target, _action, _timeout, _command_id):
        return {"output": "done"}

    class FakeTextCollector:
        def reset(self):
            pass

        async def wait(self, _timeout):
            return {"ok": True, "text": "done"}

        async def wait_for(self, _target, _action, *, timeout, command_id):
            assert timeout > 0
            assert command_id
            return {"text": "done"}

    monkeypatch.setattr(bot, "SESSION", {"target": "selected-later"})
    monkeypatch.setattr(bot, "target_label", lambda device: device)
    monkeypatch.setattr(bot, "_replace_user_card", replace_card)
    monkeypatch.setattr(bot, "back_to_device_kb", lambda: "back")
    monkeypatch.setattr(bot, "publish", publish)
    monkeypatch.setattr(bot, "publish_tracked", publish_tracked)
    monkeypatch.setattr(bot.shell_collector, "wait_for", shell_result)
    monkeypatch.setattr(bot, "fun_text_collector", FakeTextCollector())
    monkeypatch.setattr(bot, "get_user_role", lambda _user_id: bot.Role.OWNER)
    monkeypatch.setattr(bot, "_device_action_still_allowed", lambda *_args: True)

    message = SimpleNamespace(
        text=input_text,
        from_user=SimpleNamespace(id=bot.ADMIN_ID),
    )
    state = FakeState()
    asyncio.run(getattr(bot, handler_name)(message, state))

    assert state.cleared
    assert publications[0][0] == action
    assert publications[0][1]["_target"] == "pinned-device"
    assert rendered
    assert all("pinned-device" in text for text in rendered)
    assert len(rendered) == (
        2
        if handler_name in {"on_shell_input", "on_fun_wallpaper_input", "on_brightness_input"}
        else 1
    )


def test_about_chapter_button_uses_each_xlex_voice():
    title = "01 · Зачем существует XIDER"
    labels = {
        style: bot.xlex.render("about_chapter_button", style, title=title)
        for style in bot.xlex.STYLES
    }

    assert all(title in label for label in labels.values())
    assert len(set(labels.values())) == len(bot.xlex.STYLES)


def test_about_chapter_page_uses_selected_voice_for_title_and_body(monkeypatch):
    shown = []
    monkeypatch.setattr(bot.bot_settings, "get", lambda key, default=None: "xpikmi" if key == "ui_style" else default)

    async def replace_callback(_cq, text, reply_markup=None):
        shown.append((text, reply_markup))

    monkeypatch.setattr(bot, "_replace_callback_message", replace_callback)

    class Callback:
        data = "about:chapter:overview"

        async def answer(self, *args, **kwargs):
            pass

    asyncio.run(bot.on_about_chapter(Callback()))
    _, title, body = bot.info_book.chapter("overview", "xpikmi")
    assert shown[0][0] == f"<b>{title}</b>\n\n{body}"


def test_url_input_waits_for_an_explicit_open_count_and_dispatches_selected_count(monkeypatch):
    class FakeState:
        def __init__(self):
            self.data = {
                "command_target": "device-1",
                "authorization_callback": "cmd:url",
                "authorization_target": "device-1",
            }
            self.current = bot.Form.wait_url
            self.cleared = False

        async def get_data(self):
            return dict(self.data)

        async def update_data(self, **values):
            self.data.update(values)

        async def set_state(self, state):
            self.current = state

        async def get_state(self):
            return self.current

        async def clear(self):
            self.cleared = True
            self.current = None

    state = FakeState()
    shown = []
    published = []
    monkeypatch.setattr(bot, "_pending_device_input_still_allowed", lambda *_args: True)

    async def replace_user(_message, text, reply_markup=None):
        shown.append((text, reply_markup))

    async def replace_callback(_cq, text, reply_markup=None):
        shown.append((text, reply_markup))

    monkeypatch.setattr(bot, "_replace_user_card", replace_user)
    monkeypatch.setattr(bot, "_device_action_still_allowed", lambda *_args: True)
    monkeypatch.setattr(bot, "target_label", lambda target: target)
    monkeypatch.setattr(bot, "publish", lambda action, **kwargs: published.append((action, kwargs)) or True)
    monkeypatch.setattr(bot, "_replace_callback_message", replace_callback)

    class Message:
        text = "example.com/docs"
        from_user = type("User", (), {"id": bot.ADMIN_ID})()

    asyncio.run(bot.on_url_input(Message(), state))
    assert not published
    assert not state.cleared
    assert state.current == bot.Form.wait_url_count
    callbacks = _callback_data(shown[-1][1])
    assert {"url:count:1", "url:count:2", "url:count:3", "url:count:5"} <= set(callbacks)

    class Callback:
        data = "url:count:3"
        from_user = type("User", (), {"id": bot.ADMIN_ID})()

        async def answer(self, *args, **kwargs):
            pass

    asyncio.run(bot.on_url_count_select(Callback(), state))
    assert published == [("open_url", {"_target": "device-1", "url": "https://example.com/docs", "count": 3})]
    assert state.cleared


def test_url_count_keyboard_is_bounded_and_saving_is_owner_only():
    owner_callbacks = set(_callback_data(bot.url_count_keyboard(is_owner=True)))
    user_callbacks = set(_callback_data(bot.url_count_keyboard(is_owner=False)))
    assert {"url:count:1", "url:count:2", "url:count:3", "url:count:5", "url:cancel"} <= owner_callbacks
    assert "url:fav:save" in owner_callbacks
    assert "url:fav:save" not in user_callbacks
    assert "url:count:4" not in owner_callbacks


def test_network_menu_shows_link_tools_to_owner():
    callbacks = set(_callback_data(bot.network_menu()))
    assert "cmd:url" in callbacks
    assert "url:favorites" in callbacks


def test_saved_url_opens_count_picker_on_selected_device(monkeypatch):
    favorite = {"id": "0123456789ab", "name": "Docs", "url": "https://example.com/docs"}
    monkeypatch.setattr(bot.bot_settings, "get", lambda key, default=None: [favorite] if key == "url_favorites" else default)
    monkeypatch.setattr(bot, "devices", FakeDevices({"device-1": {"name": "Test", "os": "macOS"}}))
    session = bot.SessionRegistry()
    session["target"] = "device-1"
    monkeypatch.setattr(bot, "SESSION", session)
    monkeypatch.setattr(bot, "_device_action_still_allowed", lambda *_args: True)
    monkeypatch.setattr(bot, "target_label", lambda target: target)
    shown = []

    async def replace(_cq, text, reply_markup=None):
        shown.append((text, reply_markup))

    monkeypatch.setattr(bot, "_replace_callback_message", replace)

    class FakeState:
        def __init__(self):
            self.data = {}
            self.current = None

        async def set_state(self, value):
            self.current = value

        async def update_data(self, **values):
            self.data.update(values)

    class Callback:
        from_user = type("User", (), {"id": bot.ADMIN_ID})()
        data = "url:fav:open:0123456789ab"

        async def answer(self, *args, **kwargs):
            pass

    state = FakeState()
    asyncio.run(bot.on_url_favorite_open(Callback(), state))
    assert state.current == bot.Form.wait_url_count
    assert state.data["authorization_target"] == "device-1"
    assert state.data["authorization_callback"] == "cmd:url"
    assert state.data["url"] == favorite["url"]
    assert {"url:count:1", "url:count:2", "url:count:3", "url:count:5"} <= set(_callback_data(shown[0][1]))


def test_completed_xlex_screen_copy_has_six_distinct_voices():
    keys = (
        "guest_devices_overview",
        "admin_user_profile",
        "admin_device_access_intro",
        "admin_permissions_intro",
        "style_select_intro",
        "style_enabled_notice",
        "power_menu_intro",
        "power_confirm_prompt",
        "category_media",
        "category_screen",
        "category_input",
        "category_system",
        "category_network",
        "category_terminal",
        "category_power",
        "category_pranks",
        "category_device_settings",
        "events_menu_intro",
        "quiet_hours_intro",
        "digest_menu_intro",
        "admins_menu_intro",
        "favorites_page_intro",
        "wallpaper_guide",
        "prank_page_intro",
        "prank_spam_prompt",
        "prank_shout_prompt",
        "guardian_menu_intro",
        "volume_options_title",
        "mic_duration_prompt",
        "power_action_result",
        "wallpaper_photo_installed",
        "wallpaper_photo_sent",
        "wallpaper_photo_error",
        "device_command_completed",
        "brightness_result",
        "command_waiting",
        "status_waiting",
        "no_devices_yet",
        "admin_invalid_user",
        "admin_user_not_found",
        "admin_invalid_data",
        "device_unavailable",
        "text_edit_prompt",
        "text_saved",
        "admin_message_prompt",
        "admin_role_changed",
        "owner_immutable",
        "admin_user_blocked",
        "admin_user_unblocked",
        "device_rename_prompt",
        "device_delete_confirm",
        "agent_uninstall_confirm",
        "agent_uninstall_pending",
        "agent_uninstall_result",
        "device_confirmed",
        "device_block_confirm",
        "device_blocked_notice",
        "devices_clear_confirm",
        "devices_cleared",
        "manual_device_id_prompt",
        "text_edit_expired",
        "text_save_failed",
        "admin_device_access_changed",
        "admin_permission_changed",
        "admin_message_empty",
        "admin_message_sent",
        "admin_message_failed",
        "device_renamed_result",
        "device_rename_unselected",
        "device_name_empty",
        "device_delete_result",
        "device_unblocked_notice",
        "device_not_blocked",
        "devices_blocked_empty",
        "devices_blocked_heading",
        "device_id_invalid",
        "device_added_manual",
        "device_already_present",
        "agent_uninstall_timeout",
        "agent_uninstall_no_result",
        "command_started",
        "command_result",
        "command_already_running",
        "agent_update_label",
    )
    assert set(keys) <= set(bot.xlex.COPY)
    for key in keys:
        rendered = {
            style: bot.xlex.render(
                key,
                style,
                device="Laptop",
                name="Owner",
                user_id="123",
                role="Пользователь",
                online="1",
                total="2",
                devices="1",
                buttons="3",
                action="перезапустить",
                page="1",
                text="OK",
                level="40",
                command="status",
                size="42",
                error="ошибка",
                emoji="⚡",
                label="Перезапуск",
                current="Old value",
                device_id="a1b2c3d4e5f6",
                permission="cmd:screenshot",
            )
            for style in bot.xlex.STYLES
        }
        assert len(set(rendered.values())) == len(bot.xlex.STYLES), key


def test_simple_command_labels_follow_selected_xlex_voice(monkeypatch):
    values = {"level": "40", "command": "status"}
    for action, (copy_key, _value_name) in bot._SIMPLE_COMMAND_LABELS.items():
        for style in bot.xlex.STYLES:
            monkeypatch.setattr(bot, "bot_settings", {"ui_style": style})
            label = bot._simple_command_label(action, "fallback", values)
            expected_values = {_value_name: values[_value_name]} if _value_name else {}
            assert label == bot.xlex.render(copy_key, style, **expected_values), action


def test_all_stateless_prank_menu_commands_have_xlex_labels():
    actions = {
        callback[4:]
        for page in range(1, 6)
        for callback in _callback_data(bot.pranks_menu(page=page))
        if callback.startswith("cmd:prank_")
    }

    assert actions
    assert actions <= set(bot._SIMPLE_COMMAND_LABELS)
    assert all(
        bot._SIMPLE_COMMAND_LABELS[action][0] in bot.xlex.COPY
        for action in actions
    )


def test_generic_prank_callback_uses_single_card_command_flow(monkeypatch):
    from types import SimpleNamespace

    calls = []

    async def simple_command(cq, action, emoji, label, timeout=12.0, **kwargs):
        calls.append((action, emoji, label, timeout, kwargs))

    class FakeCallback:
        data = "cmd:prank_fake_update"

    monkeypatch.setattr(bot, "simple_command", simple_command)
    monkeypatch.setattr(bot.bot_settings, "get", lambda key, default=None: "xperson" if key == "ui_style" else default)

    asyncio.run(bot.on_cmd_prank_generic(FakeCallback()))

    assert calls == [(
        "prank_fake_update",
        "🎭",
        bot.xlex.render("prank_fake_update_button", "xperson"),
        15.0,
        {},
    )]


def test_simple_command_shows_success_when_agent_ack_has_no_text(monkeypatch):
    from types import SimpleNamespace

    edits = []

    class FakeMessage:
        chat = SimpleNamespace(id=1)
        message_id = 9

        async def edit_text(self, text, **kwargs):
            edits.append(text)

    class FakeCallback:
        data = "cmd:prank_fake_update"
        from_user = SimpleNamespace(id=1)
        message = FakeMessage()

        async def answer(self, *args, **kwargs):
            pass

    class FakeCollector:
        async def wait_for(self, *args, **kwargs):
            return {"ok": True, "id": "cmd-1"}

    monkeypatch.setitem(bot.SESSION, "target", "device-1")
    monkeypatch.setattr(bot, "target_label", lambda _target: "Test device")
    monkeypatch.setattr(bot, "_device_action_still_allowed", lambda *_args: True)
    monkeypatch.setattr(bot, "publish_tracked", lambda *args, **kwargs: (True, "cmd-1"))
    monkeypatch.setattr(bot, "fun_text_collector", FakeCollector())

    asyncio.run(bot._simple_command_unlocked(
        FakeCallback(), "prank_screamer", "🎬", "Скример", timeout=1.0
    ))

    assert len(edits) == 2
    assert bot._lex("device_command_completed") in edits[-1]
    assert bot._lex("device_no_response") not in edits[-1]


def test_simple_command_does_not_create_duplicate_after_transient_edit_errors(monkeypatch):
    from types import SimpleNamespace

    edit_attempts = 0
    successful_edits = []
    deleted = []
    answered = []

    class FakeMessage:
        chat = SimpleNamespace(id=1)
        message_id = 44

        async def edit_text(self, text, **_kwargs):
            nonlocal edit_attempts
            edit_attempts += 1
            if edit_attempts in {1, 3}:
                raise RuntimeError("temporary network failure")
            successful_edits.append(text)

        async def delete(self):
            deleted.append(True)

        async def answer(self, *args, **_kwargs):
            answered.append(args)
            return self

    class FakeCallback:
        data = "cmd:prank_fake_update"
        from_user = SimpleNamespace(id=bot.ADMIN_ID)
        message = FakeMessage()

        async def answer(self, *_args, **_kwargs):
            pass

    class FakeCollector:
        async def wait_for(self, *_args, **_kwargs):
            return {"ok": True}

    monkeypatch.setitem(bot.SESSION, "target", "device-1")
    monkeypatch.setattr(bot, "target_label", lambda _target: "Test device")
    monkeypatch.setattr(bot, "_device_action_still_allowed", lambda *_args: True)
    monkeypatch.setattr(bot, "publish_tracked", lambda *args, **kwargs: (True, "ticket"))
    monkeypatch.setattr(bot, "fun_text_collector", FakeCollector())

    asyncio.run(bot._simple_command_unlocked(
        FakeCallback(), "prank_screamer", "🎬", "Скример", timeout=1.0
    ))

    assert edit_attempts == 4
    assert len(successful_edits) == 2
    assert bot._lex("device_command_completed") in successful_edits[-1]
    assert deleted == []
    assert answered == []


def test_simple_command_serializes_different_actions_for_same_card(monkeypatch):
    from types import SimpleNamespace

    async def scenario():
        started = asyncio.Event()
        release = asyncio.Event()
        dispatched = []
        monkeypatch.setattr(bot, "_ACTIVE_UI_COMMANDS", set())

        async def run_command(_cq, action, *_args, **_kwargs):
            dispatched.append(action)
            if action == "wifi_info":
                started.set()
                await release.wait()
            return action

        monkeypatch.setattr(bot, "_simple_command_unlocked", run_command)

        class FakeCallback:
            def __init__(self):
                self.from_user = SimpleNamespace(id=bot.ADMIN_ID)
                self.message = SimpleNamespace(
                    chat=SimpleNamespace(id=1), message_id=44
                )
                self.answers = []

            async def answer(self, *args, **kwargs):
                self.answers.append((args, kwargs))

        first = FakeCallback()
        second = FakeCallback()
        first_result = asyncio.create_task(
            bot.simple_command(first, "wifi_info", "📶", "Wi-Fi")
        )
        await started.wait()

        second_result = await bot.simple_command(
            second, "battery_status", "🔋", "Battery"
        )

        assert second_result is None
        assert dispatched == ["wifi_info"]
        assert second.answers == [
            ((bot._lex("command_already_running"),), {"show_alert": True})
        ]
        release.set()
        assert await first_result == "wifi_info"
        assert bot._ACTIVE_UI_COMMANDS == set()

    asyncio.run(scenario())


def test_simple_command_pins_captured_device_when_session_changes_during_answer(monkeypatch):
    from types import SimpleNamespace

    published = []
    authorization_checks = []
    edits = []

    class FakeMessage:
        chat = SimpleNamespace(id=1)
        message_id = 10

        async def edit_text(self, text, **_kwargs):
            edits.append(text)

    class FakeCallback:
        data = "cmd:wifi"
        from_user = SimpleNamespace(id=77)
        message = FakeMessage()

        async def answer(self, *_args, **_kwargs):
            # Simulate another callback changing the shared selection while
            # Telegram's callback acknowledgement is in flight.
            bot.SESSION["target"] = "device-2"

    class FakeCollector:
        async def wait_for(self, target, action, *, timeout, command_id):
            assert (target, action, timeout, command_id) == (
                "device-1", "net_wifi_passwords", 1.0, "wifi-ticket"
            )
            return {"ok": True, "text": "visible result"}

    def publish_tracked(action, **kwargs):
        published.append((action, kwargs))
        return True, "wifi-ticket"

    def action_is_allowed(user_id, callback, target):
        authorization_checks.append((user_id, callback, target))
        return True

    monkeypatch.setitem(bot.SESSION, "target", "device-1")
    monkeypatch.setattr(bot, "target_label", lambda target: target)
    monkeypatch.setattr(bot, "publish_tracked", publish_tracked)
    monkeypatch.setattr(bot, "fun_text_collector", FakeCollector())
    monkeypatch.setattr(bot, "_device_action_still_allowed", action_is_allowed)

    asyncio.run(bot._simple_command_unlocked(
        FakeCallback(), "net_wifi_passwords", "📶", "Wi-Fi", timeout=1.0
    ))

    assert bot.SESSION["target"] == "device-2"
    assert published == [("net_wifi_passwords", {"_target": "device-1"})]
    assert authorization_checks == [
        (77, "cmd:wifi", "device-1"),
        (77, "cmd:wifi", "device-1"),
    ]
    assert "visible result" in edits[-1]


def test_simple_command_does_not_publish_if_original_grant_is_revoked_during_answer(monkeypatch):
    from types import SimpleNamespace

    published = []
    cards = []
    checks = []
    allowed = True

    class FakeMessage:
        chat = SimpleNamespace(id=1)
        message_id = 11

    class FakeCallback:
        data = "cmd:wifi"
        from_user = SimpleNamespace(id=78)
        message = FakeMessage()

        async def answer(self, *_args, **_kwargs):
            nonlocal allowed
            allowed = False

    async def replace(_cq, text, reply_markup=None):
        cards.append((text, reply_markup))

    def action_is_allowed(user_id, callback, target):
        checks.append((user_id, callback, target, allowed))
        return allowed

    monkeypatch.setitem(bot.SESSION, "target", "device-1")
    monkeypatch.setattr(bot, "_replace_callback_message", replace)
    monkeypatch.setattr(bot, "publish_tracked", lambda *args, **kwargs: published.append((args, kwargs)))
    monkeypatch.setattr(bot, "_device_action_still_allowed", action_is_allowed)

    asyncio.run(bot._simple_command_unlocked(
        FakeCallback(), "net_wifi_passwords", "📶", "Wi-Fi", timeout=1.0
    ))

    assert checks == [(78, "cmd:wifi", "device-1", False)]
    assert published == []
    assert cards[-1][0] == bot._lex("input_access_revoked")


def test_simple_command_hides_wifi_password_result_if_grant_revoked_while_waiting(monkeypatch):
    from types import SimpleNamespace

    published = []
    checks = []
    edits = []
    cards = []
    allowed = True
    secret = "Home_WiFi: private-password-8472"

    class FakeMessage:
        chat = SimpleNamespace(id=1)
        message_id = 12

        async def edit_text(self, text, **_kwargs):
            edits.append(text)

    class FakeCallback:
        data = "cmd:wifi"
        from_user = SimpleNamespace(id=79)
        message = FakeMessage()

        async def answer(self, *_args, **_kwargs):
            pass

    class FakeCollector:
        async def wait_for(self, target, action, *, timeout, command_id):
            nonlocal allowed
            assert (target, action, command_id) == (
                "device-1", "net_wifi_passwords", "wifi-ticket"
            )
            allowed = False
            return {"ok": True, "text": secret}

    def publish_tracked(action, **kwargs):
        published.append((action, kwargs))
        return True, "wifi-ticket"

    def action_is_allowed(user_id, callback, target):
        checks.append((user_id, callback, target, allowed))
        return allowed

    async def replace(_cq, text, reply_markup=None):
        cards.append((text, reply_markup))

    monkeypatch.setitem(bot.SESSION, "target", "device-1")
    monkeypatch.setattr(bot, "target_label", lambda target: target)
    monkeypatch.setattr(bot, "publish_tracked", publish_tracked)
    monkeypatch.setattr(bot, "fun_text_collector", FakeCollector())
    monkeypatch.setattr(bot, "_device_action_still_allowed", action_is_allowed)
    monkeypatch.setattr(bot, "_replace_callback_message", replace)

    result = asyncio.run(bot._simple_command_unlocked(
        FakeCallback(), "net_wifi_passwords", "📶", "Wi-Fi", timeout=1.0
    ))

    assert published == [("net_wifi_passwords", {"_target": "device-1"})]
    assert checks == [
        (79, "cmd:wifi", "device-1", True),
        (79, "cmd:wifi", "device-1", False),
    ]
    assert result is None
    assert cards[-1][0] == bot._lex("action_result_hidden_after_revoke")
    assert secret not in "\n".join(edits)


@pytest.mark.parametrize(
    ("handler_name", "collector_name", "action", "callback_data", "payload"),
    [
        ("on_cmd_clipboard", "clipboard_collector", "clipboard", "cmd:clipboard", {"text": "private clipboard"}),
        ("on_cmd_mic", "mic_collector", "mic", "cmd:mic", {"audio": "eA=="}),
        ("on_cmd_screenshot", "screenshot_collector", "screenshot", "cmd:screenshot", {"image": "eA=="}),
        ("on_cmd_webcam", "webcam_collector", "webcam", "cmd:webcam", {"image": "eA=="}),
        ("on_cmd_processes", "processes_collector", "processes", "cmd:processes", {"lines": ["worker"]}),
        ("on_cmd_battery", "battery_collector", "battery", "cmd:battery", {"available": True, "percent": 42}),
        ("on_cmd_network", "network_collector", "network", "cmd:network", {"interfaces": []}),
        ("on_cmd_services", "services_collector", "services", "cmd:services", {"total": 4, "running": 2}),
        ("on_cmd_capabilities", "capabilities_collector", "capabilities", "cmd:capabilities", {}),
        ("on_cmd_sysinfo", "sysinfo_collector", "sysinfo", "cmd:sysinfo", {"device_id": "device-1"}),
        ("on_cmd_status", "status_collector", "status_request", "cmd:status", {}),
        ("on_cmd_disks", "disks_collector", "disks", "cmd:disks", {"lines": ["C: 1 GB"]}),
        ("on_mic_dur", "mic_collector", "mic", "micdur:5", {"audio": "eA=="}),
    ],
)
def test_sensitive_callback_hides_result_if_grant_is_revoked_during_wait(
    monkeypatch, handler_name, collector_name, action, callback_data, payload
):
    from types import SimpleNamespace

    allowed = True
    published = []
    replacement_cards = []
    media_sends = []

    class FakeMessage:
        chat = SimpleNamespace(id=77)
        message_id = 99

        async def edit_text(self, *_args, **_kwargs):
            pass

    class FakeCallback:
        from_user = SimpleNamespace(id=77)
        message = FakeMessage()

        async def answer(self, *_args, **_kwargs):
            pass

    class FakeCollector:
        async def wait_for(self, *_args, **_kwargs):
            nonlocal allowed
            allowed = False
            return payload

    def publish_tracked(sent_action, **kwargs):
        published.append((sent_action, kwargs))
        return True, "ticket-1"

    async def replace(_cq, text, reply_markup=None):
        replacement_cards.append((text, reply_markup))

    monkeypatch.setitem(bot.SESSION, "target", "device-1")
    monkeypatch.setattr(bot, "_device_action_still_allowed", lambda *_args: allowed)
    monkeypatch.setattr(bot, "publish_tracked", publish_tracked)
    monkeypatch.setattr(bot, collector_name, FakeCollector())
    monkeypatch.setattr(bot, "_replace_callback_message", replace)
    monkeypatch.setattr(bot.bot, "send_photo", lambda *args, **kwargs: media_sends.append((args, kwargs)))
    monkeypatch.setattr(bot.bot, "send_voice", lambda *args, **kwargs: media_sends.append((args, kwargs)))

    callback = FakeCallback()
    callback.data = callback_data
    asyncio.run(getattr(bot, handler_name)(callback))

    expected_kwargs = {"_target": "device-1"}
    if callback_data == "micdur:5":
        expected_kwargs["duration"] = 5
    assert published == [(action, expected_kwargs)]
    assert replacement_cards[-1][0] == bot._lex("action_result_hidden_after_revoke")
    assert media_sends == []


def test_sensitive_callback_reports_revocation_before_publish_as_not_sent(monkeypatch):
    from types import SimpleNamespace

    allowed = True
    published = []
    replacement_cards = []

    class FakeMessage:
        chat = SimpleNamespace(id=77)
        message_id = 100

    class FakeCallback:
        data = "cmd:clipboard"
        from_user = SimpleNamespace(id=77)
        message = FakeMessage()

        async def answer(self, *_args, **_kwargs):
            nonlocal allowed
            allowed = False

    async def replace(_cq, text, reply_markup=None):
        replacement_cards.append(text)

    monkeypatch.setitem(bot.SESSION, "target", "device-1")
    monkeypatch.setattr(bot, "_device_action_still_allowed", lambda *_args: allowed)
    monkeypatch.setattr(bot, "publish_tracked", lambda *args, **kwargs: published.append((args, kwargs)))
    monkeypatch.setattr(bot, "_replace_callback_message", replace)

    asyncio.run(bot.on_cmd_clipboard(FakeCallback()))

    assert published == []
    assert replacement_cards == [bot._lex("input_access_revoked")]


def test_simple_command_keeps_owner_all_device_volume_action_available(monkeypatch):
    from types import SimpleNamespace

    published = []
    edits = []

    class FakeMessage:
        chat = SimpleNamespace(id=1)
        message_id = 13

        async def edit_text(self, text, **_kwargs):
            edits.append(text)

    class FakeCallback:
        data = "cmd:volume"
        from_user = SimpleNamespace(id=bot.ADMIN_ID)
        message = FakeMessage()

        async def answer(self, *_args, **_kwargs):
            pass

    class FakeCollector:
        async def wait_for(self, target, action, *, timeout, command_id):
            assert (target, action, command_id) == ("all", "volume_toggle", "all-ticket")
            return {"ok": True, "text": "All devices muted"}

    monkeypatch.setitem(bot.SESSION, "target", "all")
    monkeypatch.setattr(bot, "get_user_role", lambda _user_id: bot.Role.OWNER)
    monkeypatch.setattr(
        bot,
        "_device_action_still_allowed",
        lambda *_args: (_ for _ in ()).throw(AssertionError("owner all must not use device grant check")),
    )
    monkeypatch.setattr(bot, "target_label", lambda target: target)
    monkeypatch.setattr(
        bot,
        "publish_tracked",
        lambda action, **kwargs: published.append((action, kwargs)) or (True, "all-ticket"),
    )
    monkeypatch.setattr(bot, "fun_text_collector", FakeCollector())

    asyncio.run(bot._simple_command_unlocked(
        FakeCallback(), "volume_toggle", "🔇", "Mute", timeout=1.0
    ))

    assert published == [("volume_toggle", {"_target": "all"})]
    assert "All devices muted" in edits[-1]


def test_simple_command_rejects_non_owner_all_device_target(monkeypatch):
    from types import SimpleNamespace

    published = []
    cards = []
    grant_checks = []

    class FakeMessage:
        chat = SimpleNamespace(id=1)
        message_id = 14

    class FakeCallback:
        data = "cmd:volume"
        from_user = SimpleNamespace(id=80)
        message = FakeMessage()

        async def answer(self, *_args, **_kwargs):
            pass

    async def replace(_cq, text, reply_markup=None):
        cards.append(text)

    def device_grant(user_id, callback, target):
        grant_checks.append((user_id, callback, target))
        return True

    monkeypatch.setitem(bot.SESSION, "target", "all")
    monkeypatch.setattr(bot, "get_user_role", lambda _user_id: bot.Role.USER)
    monkeypatch.setattr(bot, "_device_action_still_allowed", device_grant)
    monkeypatch.setattr(bot, "_replace_callback_message", replace)
    monkeypatch.setattr(
        bot,
        "publish_tracked",
        lambda *args, **kwargs: published.append((args, kwargs)),
    )

    asyncio.run(bot._simple_command_unlocked(
        FakeCallback(), "volume_toggle", "🔇", "Mute", timeout=1.0
    ))

    assert grant_checks == []
    assert published == []
    assert cards == [bot._lex("input_access_revoked")]


@pytest.mark.parametrize(
    ("handler", "action", "emoji", "label_key", "extra"),
    [
        (bot.on_fun_spam_input, "msgbox_spam", "💬", "prank_spam_windows_button", {"count": 5}),
        (bot.on_prank_shout_input, "prank_shout_tts", "📢", "prank_shout_button", {}),
    ],
)
def test_prompted_prank_input_edits_one_card_and_keeps_captured_target(
    monkeypatch, handler, action, emoji, label_key, extra
):
    from types import SimpleNamespace

    edits = []
    published = []

    class FakeState:
        async def get_data(self):
            return {"target": "captured-device"}

        async def clear(self):
            pass

    class FakeMessage:
        text = "A test phrase"
        from_user = SimpleNamespace(id=bot.ADMIN_ID)

    class FakeCollector:
        async def wait_for(self, *args, **kwargs):
            return {"ok": True, "id": "cmd-1"}

    async def replace_card(_message, text, reply_markup=None):
        edits.append(text)

    monkeypatch.setitem(bot.SESSION, "target", "a-different-current-device")
    monkeypatch.setattr(bot.bot_settings, "get", lambda key, default=None: "xperson" if key == "ui_style" else default)
    monkeypatch.setattr(bot, "target_label", lambda target: target)
    monkeypatch.setattr(bot, "publish_tracked", lambda name, **kwargs: published.append((name, kwargs)) or (True, "cmd-1"))
    monkeypatch.setattr(bot, "fun_text_collector", FakeCollector())
    monkeypatch.setattr(bot, "_replace_user_card", replace_card)
    monkeypatch.setattr(bot, "get_user_role", lambda _user_id: bot.Role.OWNER)
    monkeypatch.setattr(bot, "_device_action_still_allowed", lambda *_args: True)

    asyncio.run(handler(FakeMessage(), FakeState()))

    label = bot.xlex.render(label_key, "xperson")
    assert edits == [
        bot._lex_html(
            "command_waiting",
            emoji=emoji,
            label=label,
            device="captured-device",
        ),
        bot._lex_html(
            "command_result",
            emoji=emoji,
            label=label,
            device="captured-device",
            text=bot.xlex.render("device_command_completed", "xperson"),
        ),
    ]
    assert published == [(
        action,
        {"_target": "captured-device", "text": "A test phrase", **extra},
    )]


def test_xlex_html_helpers_escape_dynamic_device_names():
    rendered = bot._lex_html("category_media", device="<b>not markup</b>")
    assert "<b>not markup</b>" not in rendered
    assert "&lt;b&gt;not markup&lt;/b&gt;" in rendered


def test_uninstall_timeout_does_not_remove_device_or_retarget_other_session(monkeypatch):
    from types import SimpleNamespace

    removed = []
    revoked = []
    published = []
    rendered = []
    answers = []

    class FakeDevices:
        def remove(self, device_id):
            removed.append(device_id)
            return True

    class FakeAccessStore:
        def revoke_devices(self, device_ids, *, actor_id):
            revoked.append((list(device_ids), actor_id))

    class FakeCollector:
        async def wait_for(self, device_id, action_type, *, timeout, command_id):
            assert (device_id, action_type, timeout, command_id) == (
                "device-1", "uninstall_agent", 15.0, "ticket-1"
            )
            return None

    class FakeCallback:
        data = "devmg:uninstok:device-1"
        from_user = SimpleNamespace(id=7)
        message = SimpleNamespace()

        async def answer(self, *args, **kwargs):
            answers.append((args, kwargs))

    async def replace(_cq, text, **kwargs):
        rendered.append((text, kwargs))

    def publish_tracked(action, **kwargs):
        published.append((action, kwargs))
        return True, "ticket-1"

    monkeypatch.setattr(bot, "devices", FakeDevices())
    monkeypatch.setattr(bot, "access_store", FakeAccessStore())
    monkeypatch.setattr(bot, "fun_text_collector", FakeCollector())
    monkeypatch.setattr(bot, "publish_tracked", publish_tracked)
    monkeypatch.setattr(bot, "_replace_callback_message", replace)
    monkeypatch.setattr(bot, "devices_menu", lambda *_args, **_kwargs: "devices-menu")
    monkeypatch.setattr(bot, "target_label", lambda _device_id: "Laptop")
    monkeypatch.setattr(bot, "audit", lambda *_args, **_kwargs: None)
    monkeypatch.setitem(bot.SESSION, "target", "other-device")

    asyncio.run(bot.on_devmg_uninstall_ok(FakeCallback()))

    assert published == [("uninstall_agent", {"_target": "device-1"})]
    assert removed == []
    assert revoked == []
    assert bot.SESSION["target"] == "other-device"
    assert answers == [((bot._lex("agent_uninstall_pending"),), {})]
    assert rendered == [(
        bot._lex_html("agent_uninstall_timeout", device="Laptop"),
        {"reply_markup": "devices-menu"},
    )]


@pytest.mark.parametrize("result_state", [None, "uninstall_prepared", "uninstall_pending"])
def test_uninstall_only_revokes_device_after_correlated_success(monkeypatch, result_state):
    from types import SimpleNamespace

    removed = []
    revoked = []
    rendered = []

    class FakeDevices:
        def remove(self, device_id):
            removed.append(device_id)
            return True

    class FakeAccessStore:
        def revoke_devices(self, device_ids, *, actor_id):
            revoked.append((list(device_ids), actor_id))

    class FakeCollector:
        async def wait_for(self, device_id, action_type, *, timeout, command_id):
            assert (device_id, action_type, command_id) == ("device-1", "uninstall_agent", "ticket-2")
            return {"type": "uninstall_agent", "ok": True, "text": "Removed", "id": command_id, "state": result_state}

    class FakeCallback:
        data = "devmg:uninstok:device-1"
        from_user = SimpleNamespace(id=7)
        message = SimpleNamespace()

        async def answer(self, *_args, **_kwargs):
            pass

    async def replace(_cq, text, **kwargs):
        rendered.append((text, kwargs))

    monkeypatch.setattr(bot, "devices", FakeDevices())
    monkeypatch.setattr(bot, "access_store", FakeAccessStore())
    monkeypatch.setattr(bot, "fun_text_collector", FakeCollector())
    monkeypatch.setattr(bot, "publish_tracked", lambda *_args, **_kwargs: (True, "ticket-2"))
    monkeypatch.setattr(bot, "_replace_callback_message", replace)
    monkeypatch.setattr(bot, "devices_menu", lambda *_args, **_kwargs: "devices-menu")
    monkeypatch.setattr(bot, "target_label", lambda _device_id: "Laptop")
    monkeypatch.setattr(bot, "audit", lambda *_args, **_kwargs: None)
    monkeypatch.setitem(bot.SESSION, "target", "device-1")

    asyncio.run(bot.on_devmg_uninstall_ok(FakeCallback()))

    if result_state is None:
        assert removed == ["device-1"]
        assert revoked == [(["device-1"], 7)]
        assert bot.SESSION["target"] is None
    else:
        assert removed == []
        assert revoked == []
        assert bot.SESSION["target"] == "device-1"
    assert len(rendered) == 1
    assert "Removed" in rendered[0][0]
    assert "Laptop" in rendered[0][0]


def test_device_card_all_shows_online_total(monkeypatch):
    _setup(monkeypatch, {"mac1": {"name": "Mac", "os": "macOS", "last_seen": time.time()},
                         "win1": {"name": "PC", "os": "Windows", "last_seen": 0}})
    card = bot.device_card("all")
    assert "УПРАВЛЕНИЕ ВСЕМИ УСТРОЙСТВАМИ" in card
    assert "В сети" in card


def test_device_card_online_device_fields(monkeypatch):
    _setup(monkeypatch, {"mac1": {"name": "My Mac", "os": "macOS 14",
                                  "version": "1.2", "last_seen": time.time()}})
    card = bot.device_card("mac1")
    assert "My Mac" in card
    assert "macOS 14" in card
    assert "v1.2" in card
    assert "В сети" in card
    assert "ID: mac1" in card


def test_device_card_offline_and_unknown_os(monkeypatch):
    _setup(monkeypatch, {"win1": {"name": "PC", "os": "Windows 11", "last_seen": 0}})
    card = bot.device_card("win1")
    assert "Оффлайн" in card
    assert "🪟" in card  # иконка Windows
    unknown = bot.device_card("missing")
    assert "missing" in unknown  # нет записи — имя = device_id


def test_capabilities_text_separates_support_from_live_health():
    text = bot._format_capabilities({
        "hostname": "<Mac & PC>",
        "platform": "macOS",
        "version": "3.4.0",
        "feature_status": {
            "screenshot": "permission_unverified",
            "geolocation": "approximate",
        },
        "commands": ["screenshot", "geo_location", "DROP TABLE"],
    })

    assert "&lt;Mac &amp; PC&gt;" in text
    assert "разрешение ОС ещё не проверено" in text
    assert "не GPS" in text
    assert "DROP TABLE" not in text
    assert "это не тест реального действия" in text


def test_capabilities_text_marks_legacy_data_unverified():
    text = bot._format_capabilities({
        "features": {"mic": True, "clipboard": False},
        "commands": ["mic"],
    })

    assert "заявлено старым агентом; реальная проверка не выполнена" in text
    assert "нет свежего статуса" in text


def test_capabilities_text_stays_within_telegram_message_limit():
    commands = [f"cmd{i:03d}_" + "x" * 57 for i in range(256)]

    text = bot._format_capabilities({"commands": commands})

    assert len(text) < 4096
    assert "Заявлено команд:</b> 256" in text
    assert "ещё 224" in text


def test_device_menu_has_nine_category_buttons(monkeypatch):
    _setup(monkeypatch, {"dev1": {"name": "Dev"}})
    cbs = _callback_data(bot.device_menu("dev1"))
    assert NINE_CATEGORIES.issubset(set(cbs))


def test_manual_install_is_owner_only_and_uses_published_one_line_commands(monkeypatch):
    from types import SimpleNamespace

    _setup(monkeypatch, {})
    monkeypatch.setattr(
        bot, "get_user_role",
        lambda user_id: bot.Role.OWNER if user_id == 100 else bot.Role.USER,
    )
    assert "install:manual" in _callback_data(bot.devices_menu(100))
    assert "install:manual" not in _callback_data(bot.devices_menu(200))

    expected = {
        "windows": "irm https://invinby.github.io/XIDER/win.ps1 | iex",
        "macos": "curl -fsSL https://invinby.github.io/XIDER/mac | bash",
    }
    for selected_os, command in expected.items():
        screen = bot.manual_install_command_screen(selected_os)
        assert f"<code>{command}</code>" in screen
        assert bot._lex("manual_install_copy_note") in screen
        buttons = bot.manual_install_os_keyboard(selected_os=selected_os)
        callbacks = _callback_data(buttons)
        assert "menu:devices" in callbacks
        assert f"install:{selected_os}" not in callbacks

    with pytest.raises(ValueError, match="Unsupported XIDER installer OS"):
        bot.manual_install_command_screen("linux")


def test_manual_install_copy_has_complete_six_voice_xlex(monkeypatch):
    keys = (
        "manual_install_button",
        "manual_install_pick_os",
        "manual_install_windows",
        "manual_install_macos",
        "manual_install_windows_title",
        "manual_install_macos_title",
        "manual_install_copy_note",
        "manual_install_back",
    )
    for key in keys:
        rendered = {style: bot.xlex.render(key, style) for style in bot.xlex.STYLES}
        assert len(set(rendered.values())) == len(bot.xlex.STYLES), key

    callbacks_by_style = {}
    for style in bot.xlex.STYLES:
        monkeypatch.setattr(
            bot.bot_settings,
            "get",
            lambda key, default=None, selected=style: selected if key == "ui_style" else default,
        )
        keyboard = bot.manual_install_os_keyboard()
        callbacks_by_style[style] = _callback_data(keyboard)
        assert all(
            len(button.text.encode("utf-16-le")) // 2 <= 64
            for row in keyboard.inline_keyboard
            for button in row
        )
    assert all(callbacks == callbacks_by_style[bot.xlex.STYLES[0]] for callbacks in callbacks_by_style.values())


@pytest.mark.parametrize(
    ("callback_data", "expected_text", "expected_callback"),
    [
        (
            "install:windows",
            "irm https://invinby.github.io/XIDER/win.ps1 | iex",
            "install:macos",
        ),
        (
            "install:macos",
            "curl -fsSL https://invinby.github.io/XIDER/mac | bash",
            "install:windows",
        ),
        ("install:manual", "__picker__", "install:windows"),
    ],
)
def test_manual_install_callback_edits_the_active_card(
    monkeypatch, callback_data, expected_text, expected_callback
):
    from types import SimpleNamespace

    edits = []
    answers = []

    class FakeCallback:
        data = callback_data
        from_user = SimpleNamespace(id=bot.ADMIN_ID)

        async def answer(self, *args, **kwargs):
            answers.append((args, kwargs))

    async def replace(_cq, text, reply_markup=None):
        edits.append((text, reply_markup))

    monkeypatch.setattr(bot, "_replace_callback_message", replace)
    asyncio.run(bot.on_manual_install(FakeCallback()))

    assert len(edits) == 1
    if expected_text == "__picker__":
        expected_text = bot._lex("manual_install_pick_os")
    assert expected_text in edits[0][0]
    assert expected_callback in _callback_data(edits[0][1])
    assert answers == [((), {})]


def test_primary_navigation_is_grouped_into_compact_dashboard_rows(monkeypatch):
    _setup(monkeypatch, {"dev1": {"name": "Dev"}})
    main_rows = [
        [button.callback_data for button in row]
        for row in bot.main_menu(bot.ADMIN_ID).inline_keyboard
    ]
    assert main_rows == [
        ["menu:devices", "dev:all"],
        ["menu:server", "ev:menu"],
        ["menu:about", "menu:admin"],
    ]

    device_rows = [
        [button.callback_data for button in row]
        for row in bot.device_menu("dev1").inline_keyboard
    ]
    assert device_rows[:5] == [
        ["cat:media", "cat:screen"],
        ["cat:input", "cat:system"],
        ["cat:network", "cat:files"],
        ["cat:terminal", "cat:power"],
        ["cat:pranks", "cat:device"],
    ]


def test_admin_menu_pairs_access_audit_and_text_controls(monkeypatch):
    _setup(monkeypatch, {})
    admin_rows = [
        [button.callback_data for button in row]
        for row in bot.admin_menu().inline_keyboard
    ]
    assert admin_rows == [
        ["admin:users", "admin:audit"],
        ["admin:texts", "admin:style"],
        ["menu:server", "menu:main"],
    ]


def test_each_category_menu_builds_buttons():
    """Каждый новый категорийный рендерер возвращает клавиатуру с цветными кнопками."""
    builders = (
        bot.media_menu, bot.screen_menu, bot.input_menu, bot.system_menu,
        bot.network_menu, bot.files_menu, bot.terminal_menu, bot.power_menu_new,
        bot.pranks_menu, bot.control_menu, bot.fun_menu,
    )
    for builder in builders:
        cbs = _callback_data(builder())
        assert cbs, f"{builder.__name__} вернул пустую клавиатуру"


def test_power_menu_exposes_guardian_for_windows_and_mac(monkeypatch):
    monkeypatch.setattr(bot, "SESSION", {"target": "win1"})
    _setup(monkeypatch, {"win1": {"name": "PC", "os": "Windows 11"}})
    windows_cbs = _callback_data(bot.power_menu_new())
    assert "cmd:guardian_menu" in windows_cbs

    monkeypatch.setattr(bot, "SESSION", {"target": "mac1"})
    _setup(monkeypatch, {"mac1": {"name": "Mac", "os": "macOS 27"}})
    mac_cbs = _callback_data(bot.power_menu_new())
    assert "cmd:guardian_menu" in mac_cbs


def test_power_menu_places_shutdown_and_autorun_controls_in_clear_groups(monkeypatch):
    monkeypatch.setattr(bot, "SESSION", {"target": "win1"})
    _setup(monkeypatch, {"win1": {"name": "PC", "os": "Windows 11"}})
    rows = [
        [button.callback_data for button in row]
        for row in bot.power_menu_new().inline_keyboard
    ]
    assert rows[:6] == [
        ["cmd:standby_sleep", "cmd:lock"],
        ["power:sleep", "power:reboot"],
        ["power:shutdown", "cmd:wol"],
        ["cmd:autorun_status", "cmd:autorun_enable"],
        ["cmd:autorun_disable", "cmd:guardian_menu"],
        ["cfm:stop", "back:device"],
    ]


def test_event_settings_use_compact_rows_without_changing_callbacks():
    rows = [
        [button.callback_data for button in row]
        for row in bot.events_menu().inline_keyboard
    ]
    assert rows == [
        ["ev:toggle:notify_online", "ev:toggle:notify_offline"],
        ["ev:toggle:notify_battery_low", "ev:quiet"],
        ["ev:digest", "ev:admins"],
        ["ev:server_autostart:toggle", "menu:main"],
    ]
    assert [
        [button.callback_data for button in row]
        for row in bot.quiet_hours_menu().inline_keyboard
    ] == [
        ["quiet::", "quiet:23:8"],
        ["quiet:22:7", "quiet:0:6"],
        ["ev:menu"],
    ]


def test_guardian_stop_requires_confirmation():
    cbs = _callback_data(bot.guardian_menu())
    assert "cfm:guardian_stop" in cbs
    assert "cmd:guardian_stop" not in cbs


def test_guardian_command_includes_dispatch_marker(monkeypatch):
    captured = {}

    async def fake_simple_command(*args, **kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(bot, "simple_command", fake_simple_command)
    asyncio.run(bot._guardian_command(object(), "stop"))

    assert captured["command"] == "stop"
    assert captured["action"] == "guardian"


def test_power_confirmation_dispatches_action_payload_without_duplicate_callback_answer(monkeypatch):
    from types import SimpleNamespace

    published = []
    answers = []
    edits = []

    class FakeCollector:
        def reset(self):
            pass

        async def wait(self, _timeout):
            return None

    class FakeMessage:
        chat = SimpleNamespace(id=10)
        message_id = 30

        async def edit_text(self, text, **kwargs):
            edits.append((text, kwargs))

    class FakeCallback:
        data = "power_confirm:reboot"
        from_user = SimpleNamespace(id=20)
        message = FakeMessage()

        async def answer(self, *args, **kwargs):
            answers.append((args, kwargs))

    def fake_transport_publish(target, command_name, **payload):
        published.append((target, command_name, payload))
        return True

    monkeypatch.setitem(bot.SESSION, "target", "device-A")
    monkeypatch.setattr(bot, "target_label", lambda target: target)
    monkeypatch.setattr(bot, "audit", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(bot, "HISTORY", {})
    monkeypatch.setattr(bot.transport, "publish_command", fake_transport_publish)
    monkeypatch.setattr(bot, "fun_text_collector", FakeCollector())
    monkeypatch.setattr(bot.ui_cards, "set_card", lambda *_args: None)

    asyncio.run(bot.on_power_confirm(FakeCallback()))

    assert published == [("device-A", "power", {"action": "reboot"})]
    assert len(answers) == 1
    assert len(edits) == 1


def test_sleep_confirmation_uses_captured_target_and_replaces_current_card(monkeypatch):
    published = []
    rendered = []
    answers = []

    class FakeCallback:
        async def answer(self, *args, **kwargs):
            answers.append((args, kwargs))

    def fake_transport_publish(target, command_name, **payload):
        published.append((target, command_name, payload))
        return True

    async def replace_card(_cq, text, reply_markup=None):
        rendered.append((text, reply_markup))

    monkeypatch.setitem(bot.SESSION, "target", "device-A")
    monkeypatch.setattr(bot, "target_label", lambda target: target)
    monkeypatch.setattr(bot, "audit", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(bot, "HISTORY", {})
    monkeypatch.setattr(bot.transport, "publish_command", fake_transport_publish)
    monkeypatch.setattr(bot, "_replace_callback_message", replace_card)

    asyncio.run(bot.on_sleep_ok(FakeCallback()))

    assert published == [("device-A", "power", {"action": "sleep"})]
    assert len(rendered) == 1
    assert "Сон отправлен: device-A" in rendered[0][0]
    assert len(answers) == 1


def test_check_update_blocks_old_mac_without_sending_update_or_shell(monkeypatch):
    from types import SimpleNamespace

    monkeypatch.setitem(bot.SESSION, "target", "mac1")
    _setup(monkeypatch, {"mac1": {"name": "Mac", "os": "macOS 27", "version": "4.0.0"}})
    sent = []
    shown = []

    async def no_update(*args, **kwargs):
        sent.append((args, kwargs))

    async def replace(_cq, text, reply_markup=None):
        shown.append((text, reply_markup))

    class Callback:
        async def answer(self, *args, **kwargs):
            pass

    monkeypatch.setattr(bot, "simple_command", no_update)
    monkeypatch.setattr(bot, "publish_tracked", lambda *a, **kw: pytest.fail("must not send shell"))
    monkeypatch.setattr(bot, "_replace_callback_message", replace)
    asyncio.run(bot.on_cmd_check_update(Callback()))

    assert not sent
    assert "4.0.0" in shown[0][0]
    assert "4.0.1" in shown[0][0]
    assert "Ничего не запускал" in shown[0][0]
    assert "<code>curl -fsSL https://invinby.github.io/XIDER/mac | bash</code>" in shown[0][0]


def test_check_update_allows_mac_release_protocol_version(monkeypatch):
    monkeypatch.setitem(bot.SESSION, "target", "mac1")
    _setup(monkeypatch, {"mac1": {
        "name": "Mac", "os": "macOS 27", "version": "4.0.1", "update_mode": "source",
    }})
    sent = []

    async def update(*args, **kwargs):
        sent.append((args, kwargs))

    monkeypatch.setattr(bot, "simple_command", update)
    asyncio.run(bot.on_cmd_check_update(object()))

    assert len(sent) == 1
    assert sent[0][0][1:4] == ("agent_update", "🔄", "Обновить агента")
    assert sent[0][1]["update"] is True


def test_check_update_blocks_old_windows_before_sending_update(monkeypatch):
    from types import SimpleNamespace

    monkeypatch.setitem(bot.SESSION, "target", "win1")
    _setup(monkeypatch, {"win1": {"name": "PC", "os": "Windows 11", "version": "4.0.0"}})
    sent = []
    shown = []

    async def no_update(*args, **kwargs):
        sent.append((args, kwargs))

    async def replace(_cq, text, reply_markup=None):
        shown.append((text, reply_markup))

    class Callback:
        async def answer(self, *args, **kwargs):
            pass

    monkeypatch.setattr(bot, "simple_command", no_update)
    monkeypatch.setattr(bot, "publish_tracked", lambda *a, **kw: pytest.fail("must not send update"))
    monkeypatch.setattr(bot, "_replace_callback_message", replace)

    asyncio.run(bot.on_cmd_check_update(Callback()))

    assert not sent
    assert "4.0.0" in shown[0][0]
    assert "4.0.1" in shown[0][0]
    assert "Ничего не запускал" in shown[0][0]
    assert "<code>irm https://invinby.github.io/XIDER/win.ps1 | iex</code>" in shown[0][0]


def test_check_update_allows_current_windows_source_agent(monkeypatch):
    monkeypatch.setitem(bot.SESSION, "target", "win1")
    _setup(monkeypatch, {"win1": {
        "name": "PC", "os": "Windows 11", "version": "4.0.1", "update_mode": "source",
    }})
    sent = []

    async def update(*args, **kwargs):
        sent.append((args, kwargs))

    monkeypatch.setattr(bot, "simple_command", update)

    asyncio.run(bot.on_cmd_check_update(object()))

    assert len(sent) == 1
    assert sent[0][0][1:4] == ("agent_update", "🔄", "Обновить агента")
    assert sent[0][1]["update"] is True
    assert sent[0][1]["timeout"] == 110.0


@pytest.mark.parametrize(
    ("os_name", "component_asset"),
    [
        ("macOS 27", "XGENT-MCS-macos-bundle.zip"),
        ("Windows 11", "XGENT-WDS-Windows.zip"),
    ],
)
def test_selected_release_install_requires_confirmation_and_dispatches_exact_tag(
    monkeypatch, os_name, component_asset,
):
    import release_catalog
    from types import SimpleNamespace

    session = bot.SessionRegistry()
    session["target"] = "device-1"
    monkeypatch.setattr(bot, "SESSION", session)
    monkeypatch.setattr(bot, "_PENDING_VERSION_INSTALLS", {})
    _setup(monkeypatch, {"device-1": {
        "name": "My device", "os": os_name, "version": "4.0.1",
        "update_mode": "source", "release_update_ready": True,
        "online": True, "last_seen": time.time(),
    }})
    release = release_catalog.Release(
        tag="v4.2.0", version=(4, 2, 0), name="Chosen release",
        published_at="2026-10-01T00:00:00Z", notes="Safe selected release",
        asset_names=frozenset({component_asset, "XIDER-source.zip", "release-manifest.json"}),
    )
    monkeypatch.setattr(bot.release_catalog.catalog, "list", lambda: [release])
    cards = []

    async def replace(_cq, text, reply_markup=None):
        cards.append((text, reply_markup))

    monkeypatch.setattr(bot, "_replace_callback_message", replace)

    class Callback:
        from_user = SimpleNamespace(id=bot.ADMIN_ID)

        def __init__(self, data):
            self.data = data

        async def answer(self, *args, **kwargs):
            pass

    asyncio.run(bot.on_versions_detail(Callback("versions:detail:agent:v4.2.0")))
    detail_buttons = [
        button for row in cards[-1][1].inline_keyboard for button in row
    ]
    install_button = next(button for button in detail_buttons if button.callback_data == "versions:install:agent:v4.2.0")
    assert len(install_button.callback_data) <= 64
    asyncio.run(bot.on_versions_install_select(Callback(install_button.callback_data)))
    confirm_buttons = [
        button for row in cards[-1][1].inline_keyboard for button in row
    ]
    confirm_button = next(button for button in confirm_buttons if button.callback_data == "versions:install_confirm:v4.2.0")
    assert "My device" in cards[-1][0] and "v4.2.0" in cards[-1][0]
    dispatched = []

    async def capture_command(*args, **kwargs):
        dispatched.append((args, kwargs))

    monkeypatch.setattr(bot, "simple_command", capture_command)
    asyncio.run(bot.on_versions_install_confirm(Callback(confirm_button.callback_data)))

    assert dispatched[0][0][1:4] == ("agent_update", "🔄", "Установка v4.2.0")
    assert dispatched[0][1]["update"] is True
    assert dispatched[0][1]["release_tag"] == "v4.2.0"
    assert dispatched[0][1]["timeout"] == 110.0


def test_selected_release_install_fails_closed_without_pinned_trust(monkeypatch):
    import release_catalog
    from types import SimpleNamespace

    info = {
        "os": "macOS", "version": "4.0.1", "update_mode": "source",
        "release_update_ready": False, "online": True, "last_seen": time.time(),
    }
    release = release_catalog.Release(
        tag="v4.2.0", version=(4, 2, 0), name="Chosen release",
        published_at="", notes="",
        asset_names=frozenset({
            "XGENT-MCS-macos-bundle.zip", "XIDER-source.zip", "release-manifest.json",
        }),
    )
    assert bot._version_install_block_reason(
        "agent", release, "mac_agent", info
    ) == "versions_install_trust_missing"


def test_selected_release_confirmation_cannot_follow_target_switch(monkeypatch):
    session = bot.SessionRegistry()
    session["target"] = "device-a"
    monkeypatch.setattr(bot, "SESSION", session)
    monkeypatch.setattr(bot, "_PENDING_VERSION_INSTALLS", {
        bot.ADMIN_ID: {"target": "device-a", "tag": "v4.2.0", "created_at": time.time()},
    })
    session["target"] = "device-b"
    sent = []
    monkeypatch.setattr(bot, "simple_command", lambda *args, **kwargs: sent.append((args, kwargs)))

    class Callback:
        from_user = type("User", (), {"id": bot.ADMIN_ID})()
        data = "versions:install_confirm:v4.2.0"

        async def answer(self, *args, **kwargs):
            self.answer_args = args
            self.answer_kwargs = kwargs

    callback = Callback()
    asyncio.run(bot.on_versions_install_confirm(callback))
    assert not sent
    assert callback.answer_kwargs["show_alert"] is True


def test_selected_release_confirmation_expires_and_stale_cancel_is_rejected(monkeypatch):
    session = bot.SessionRegistry()
    session["target"] = "device-a"
    monkeypatch.setattr(bot, "SESSION", session)
    monkeypatch.setattr(bot, "_PENDING_VERSION_INSTALLS", {
        bot.ADMIN_ID: {"target": "device-a", "tag": "v4.2.0", "created_at": time.time() - 181},
    })

    class Callback:
        from_user = type("User", (), {"id": bot.ADMIN_ID})()

        def __init__(self, data):
            self.data = data
            self.alerts = []

        async def answer(self, *args, **kwargs):
            self.alerts.append((args, kwargs))

    confirm = Callback("versions:install_confirm:v4.2.0")
    asyncio.run(bot.on_versions_install_confirm(confirm))
    assert confirm.alerts[0][1]["show_alert"] is True
    assert bot.ADMIN_ID not in bot._PENDING_VERSION_INSTALLS

    # A stale keyboard must not clear or falsely report cancellation of a newer request.
    bot._PENDING_VERSION_INSTALLS[bot.ADMIN_ID] = {
        "target": "device-a", "tag": "v4.3.0", "created_at": time.time(),
    }
    cancel = Callback("versions:install_cancel:v4.2.0")
    asyncio.run(bot.on_versions_install_cancel(cancel))
    assert cancel.alerts[0][1]["show_alert"] is True
    assert bot._PENDING_VERSION_INSTALLS[bot.ADMIN_ID]["tag"] == "v4.3.0"


@pytest.mark.parametrize("os_name", ["Windows 11", "macOS 27"])
def test_check_update_blocks_frozen_packages_without_sending_command(monkeypatch, os_name):
    monkeypatch.setitem(bot.SESSION, "target", "device1")
    _setup(monkeypatch, {"device1": {
        "name": "Device", "os": os_name, "version": "4.0.1", "update_mode": "frozen",
    }})
    sent = []
    shown = []

    async def no_update(*args, **kwargs):
        sent.append((args, kwargs))

    async def replace(_cq, text, reply_markup=None):
        shown.append((text, reply_markup))

    class Callback:
        async def answer(self, *args, **kwargs):
            pass

    monkeypatch.setattr(bot, "simple_command", no_update)
    monkeypatch.setattr(bot, "publish_tracked", lambda *a, **kw: pytest.fail("must not send update"))
    monkeypatch.setattr(bot, "_replace_callback_message", replace)

    asyncio.run(bot.on_cmd_check_update(Callback()))

    assert not sent
    assert shown and "транзакцион" in shown[0][0]
    command = (
        "curl -fsSL https://invinby.github.io/XIDER/mac | bash"
        if "mac" in os_name.lower()
        else "irm https://invinby.github.io/XIDER/win.ps1 | iex"
    )
    assert f"<code>{command}</code>" in shown[0][0]


def test_guardian_broadcast_stop_uses_guardian_protocol():
    import inspect

    source = inspect.getsource(bot.on_stopall_ok)
    assert 'publish_command("all", "guardian", action="guardian", command="stop")' in source


def test_screenshot_displays_agent_error_without_html_injection(monkeypatch):
    from types import SimpleNamespace

    class FakeCallback:
        data = "cmd:screenshot"
        from_user = SimpleNamespace(id=1)

        async def answer(self, *args, **kwargs):
            pass

    class FakeCollector:
        async def wait_for(self, *args, **kwargs):
            return {"image": None, "error": "Screen Recording <denied>"}

    shown = []

    async def replace(_cq, text, **kwargs):
        shown.append(text)

    monkeypatch.setitem(bot.SESSION, "target", "mac1")
    monkeypatch.setattr(bot, "publish_tracked", lambda *args, **kwargs: (True, "cmd-1"))
    monkeypatch.setattr(bot, "screenshot_collector", FakeCollector())
    monkeypatch.setattr(bot, "_replace_callback_message", replace)
    monkeypatch.setattr(bot, "_device_action_still_allowed", lambda *_args: True)

    asyncio.run(bot.on_cmd_screenshot(FakeCallback()))
    assert "Screen Recording &lt;denied&gt;" in shown[0]
    assert "<denied>" not in shown[0]


@pytest.mark.parametrize("action", ["screenshot", "webcam"])
def test_photo_response_becomes_current_navigation_card(monkeypatch, action):
    from types import SimpleNamespace

    class FakeMessage:
        chat = SimpleNamespace(id=1)
        message_id = 17

        async def delete(self):
            pass

    class FakeCallback:
        data = f"cmd:{action}"
        from_user = SimpleNamespace(id=1)
        message = FakeMessage()

        async def answer(self, *args, **kwargs):
            pass

    class FakeCollector:
        async def wait_for(self, *args, **kwargs):
            return {"image": base64.b64encode(b"fixture-image").decode("ascii")}

    async def send_photo(*args, **kwargs):
        return SimpleNamespace(chat=SimpleNamespace(id=1), message_id=18)

    cards = []
    monkeypatch.setitem(bot.SESSION, "target", "mac1")
    monkeypatch.setattr(bot, "publish_tracked", lambda *args, **kwargs: (True, "cmd-1"))
    monkeypatch.setattr(bot, f"{action}_collector", FakeCollector())
    monkeypatch.setattr(bot, "bot", SimpleNamespace(send_photo=send_photo))
    monkeypatch.setattr(bot.ui_cards, "set_card", lambda *args: cards.append(args))
    monkeypatch.setattr(bot, "_device_action_still_allowed", lambda *_args: True)

    asyncio.run(getattr(bot, f"on_cmd_{action}")(FakeCallback()))
    assert cards == [(1, 1, 18)]


def test_buttons_have_colored_styles(monkeypatch):
    """Проверяет, что кнопки имеют цветные стили (style: success/primary/danger)."""
    _setup(monkeypatch, {"dev1": {"name": "Dev", "os": "macOS", "last_seen": time.time()}})
    markup = bot.device_menu("dev1")
    styles = [b.style for row in markup.inline_keyboard for b in row if b.style]
    assert "success" in styles
    assert "primary" in styles
    assert "danger" in styles


def test_common_process_feedback_uses_selected_xlex_voice(monkeypatch):
    from types import SimpleNamespace
    class FakeCallback:
        data = "cmd:processes"
        from_user = SimpleNamespace(id=bot.ADMIN_ID)
        def __init__(self):
            self.answers = []

        async def answer(self, text=None, **kwargs):
            self.answers.append((text, kwargs))

    shown = []

    async def replace(_cq, text, **kwargs):
        shown.append((text, kwargs))

    class NoResponseCollector:
        async def wait_for(self, *args, **kwargs):
            return None

    monkeypatch.setattr(bot, "_replace_callback_message", replace)
    monkeypatch.setattr(bot, "_device_action_still_allowed", lambda *_args: True)
    monkeypatch.setattr(bot, "publish_tracked", lambda *args, **kwargs: (False, "test"))

    for style in bot.xlex.STYLES:
        monkeypatch.setattr(
            bot.bot_settings,
            "get",
            lambda key, default=None, selected=style: selected if key == "ui_style" else default,
        )
        monkeypatch.setattr(bot, "publish_tracked", lambda *args, **kwargs: (False, "test"))
        callback = FakeCallback()
        monkeypatch.setitem(bot.SESSION, "target", None)
        asyncio.run(bot.on_cmd_processes(callback))
        assert callback.answers[0][0] == bot.xlex.render("target_required", style)

        shown.clear()
        callback = FakeCallback()
        monkeypatch.setitem(bot.SESSION, "target", "device-1")
        asyncio.run(bot.on_cmd_processes(callback))
        assert shown[0][0] == bot.xlex.render("mqtt_disconnected", style)

        shown.clear()
        monkeypatch.setattr(bot, "publish_tracked", lambda *args, **kwargs: (True, "test"))
        monkeypatch.setattr(bot, "processes_collector", NoResponseCollector())
        asyncio.run(bot.on_cmd_processes(FakeCallback()))
        assert shown[0][0] == bot.xlex.render("device_timeout", style, seconds="15")


def test_rendered_menu_callbacks_match_a_registered_route(monkeypatch):
    from types import SimpleNamespace

    devices = FakeDevices({
        "device-1": {
            "name": "Test PC", "os": "Windows", "version": "4.1.0",
            "guardian": {"version": "1.0.0"}, "favorites": ["status"],
        },
    })
    session = bot.SessionRegistry()
    session["target"] = "device-1"
    monkeypatch.setattr(bot, "devices", devices)
    monkeypatch.setattr(bot, "SESSION", session)
    monkeypatch.setattr(
        bot.bot_settings,
        "get",
        lambda key, default=None: "xtech" if key == "ui_style" else default,
    )

    builders = [
        bot.main_menu(0), bot.devices_menu(), bot.device_menu("device-1"), bot.all_menu(),
        bot.back_to_device_kb(), bot.power_menu(), bot.confirm_power_menu("shutdown"),
        bot.rotate_menu(), bot.media_menu(), bot.screen_menu(), bot.wallpaper_menu(),
        bot.input_menu(), bot.system_menu(), bot.network_menu(), bot.files_menu(),
        bot.terminal_menu(), bot.power_menu_new(), bot.guardian_menu(),
        *(bot.pranks_menu(page=page, target="device-1") for page in range(1, 6)),
        bot.device_settings_menu(), bot.mic_options_menu(), bot.vol_options_menu(),
        bot.confirm_kb("stop:ok"), bot.events_menu(), bot.quiet_hours_menu(),
        bot.digest_menu(), bot.admins_menu(), bot.admin_menu(), bot.admin_texts_menu(),
        bot.admin_users_menu(), bot.admin_user_menu(0), bot.admin_devices_menu(0),
        bot.admin_permissions_menu(0), bot.guest_devices_menu(), bot.server_menu(0),
        bot.server_confirm_menu("restart"), bot.blocked_menu(), bot._version_root_menu(),
        bot._version_root_menu(server=True),
    ]
    callbacks = {
        button.callback_data
        for markup in builders
        for row in markup.inline_keyboard
        for button in row
        if button.callback_data
    }
    route_filters = [
        filter_object.callback
        for handler in bot.router.callback_query.handlers
        for filter_object in handler.filters
        if filter_object.magic is not None
    ]

    unmatched = {
        callback
        for callback in callbacks
        if not any(
            route_filter(SimpleNamespace(data=callback)) is True
            for route_filter in route_filters
        )
    }
    assert not unmatched, f"Buttons have no callback route: {sorted(unmatched)}"


def test_pranks_menu_has_stop_all_button(monkeypatch):
    """Каждая страница меню приколов имеет кнопку экстренной остановки 'prank:stop_all'."""
    for style in bot.xlex.STYLES:
        monkeypatch.setattr(
            bot.bot_settings,
            "get",
            lambda key, default=None, selected=style: selected if key == "ui_style" else default,
        )
        for page in range(1, 6):
            markup = bot.pranks_menu(page=page)
            cbs = _callback_data(markup)
            assert "prank:stop_all" in cbs
            texts = [b.text.lower() for row in markup.inline_keyboard for b in row]
            assert any("стоп" in text or "останов" in text for text in texts)


def test_pranks_menu_toggle_states(monkeypatch):
    """Проверяет динамическое изменение кнопок при активных приколах (инверсия мыши, скрытые иконки)."""
    monkeypatch.setattr(
        bot.bot_settings,
        "get",
        lambda key, default=None: "technical" if key == "ui_style" else default,
    )
    target = "pc_test_1"
    monkeypatch.setitem(bot.SESSION, f"mouse_swapped_{target}", False)
    monkeypatch.setitem(bot.SESSION, f"desktop_hidden_{target}", False)

    # Стандартный вид
    m_off_p1 = bot.pranks_menu(page=1, target=target)
    m_off_p3 = bot.pranks_menu(page=3, target=target)
    texts_p1_off = [b.text for row in m_off_p1.inline_keyboard for b in row]
    texts_p3_off = [b.text for row in m_off_p3.inline_keyboard for b in row]
    assert "🫥 Скрыть иконки" in texts_p1_off
    assert "🖱 Инверсия мыши" in texts_p3_off

    # Включаем приколы
    monkeypatch.setitem(bot.SESSION, f"mouse_swapped_{target}", True)
    monkeypatch.setitem(bot.SESSION, f"desktop_hidden_{target}", True)
    m_on_p1 = bot.pranks_menu(page=1, target=target)
    m_on_p3 = bot.pranks_menu(page=3, target=target)
    texts_p1_on = [b.text for row in m_on_p1.inline_keyboard for b in row]
    texts_p3_on = [b.text for row in m_on_p3.inline_keyboard for b in row]
    assert any("[ВКЛ] Иконки скрыты" in t for t in texts_p1_on)
    assert any("[ВКЛ] Инверсия мыши" in t for t in texts_p3_on)


def test_prank_visual_page_uses_all_six_xlex_voices_without_changing_actions(monkeypatch):
    target = "pc_prank_test"
    monkeypatch.setitem(bot.SESSION, f"desktop_hidden_{target}", False)
    callbacks_by_style = {}
    labels_by_style = {}
    hidden_labels_by_style = {}

    for style in bot.xlex.STYLES:
        monkeypatch.setattr(
            bot.bot_settings,
            "get",
            lambda key, default=None, selected=style: selected if key == "ui_style" else default,
        )
        menu = bot.pranks_menu(page=1, target=target)
        callbacks_by_style[style] = _callback_data(menu)
        labels = [button.text for row in menu.inline_keyboard for button in row]
        labels_by_style[style] = labels
        assert all(len(label.encode("utf-16-le")) // 2 <= 64 for label in labels)

        monkeypatch.setitem(bot.SESSION, f"desktop_hidden_{target}", True)
        hidden_menu = bot.pranks_menu(page=1, target=target)
        hidden_labels_by_style[style] = [
            button.text
            for row in hidden_menu.inline_keyboard
            for button in row
            if button.callback_data == "prank:hidedesktop"
        ]
        assert len(hidden_labels_by_style[style]) == 1
        monkeypatch.setitem(bot.SESSION, f"desktop_hidden_{target}", False)

    baseline = callbacks_by_style[bot.xlex.STYLES[0]]
    assert all(callbacks == baseline for callbacks in callbacks_by_style.values())
    assert len({tuple(labels) for labels in labels_by_style.values()}) >= 5
    assert "🎬 Скример (full)" in labels_by_style["xtech"]
    assert "🫥 Скрыть иконки" in labels_by_style["xtech"]
    assert "🔘 [Визуал]" in labels_by_style["xtech"]
    assert "🔘 [Экранчик ♡]" in labels_by_style["xpikmi"]
    assert "🔴 [ВКЛ] Иконки скрыты" in hidden_labels_by_style["xtech"]
    assert hidden_labels_by_style["xtech"] != ["🫥 Скрыть иконки"]


def test_prank_sound_and_input_pages_use_all_six_voices_without_changing_actions(monkeypatch):
    target = "pc_prank_test"
    callbacks_by_style = {2: {}, 3: {}}
    labels_by_style = {2: {}, 3: {}}
    swapped_labels_by_style = {}

    for style in bot.xlex.STYLES:
        monkeypatch.setattr(
            bot.bot_settings,
            "get",
            lambda key, default=None, selected=style: selected if key == "ui_style" else default,
        )
        monkeypatch.setitem(bot.SESSION, f"mouse_swapped_{target}", False)
        for page in (2, 3):
            menu = bot.pranks_menu(page=page, target=target)
            callbacks_by_style[page][style] = _callback_data(menu)
            labels = [button.text for row in menu.inline_keyboard for button in row]
            labels_by_style[page][style] = labels
            assert all(len(label.encode("utf-16-le")) // 2 <= 64 for label in labels)

        monkeypatch.setitem(bot.SESSION, f"mouse_swapped_{target}", True)
        swapped_menu = bot.pranks_menu(page=3, target=target)
        swapped_labels_by_style[style] = [
            button.text
            for row in swapped_menu.inline_keyboard
            for button in row
            if button.callback_data == "prank:swapmouse"
        ]
        assert len(swapped_labels_by_style[style]) == 1

    for page in (2, 3):
        baseline = callbacks_by_style[page][bot.xlex.STYLES[0]]
        assert all(callbacks == baseline for callbacks in callbacks_by_style[page].values())
        assert len({tuple(labels) for labels in labels_by_style[page].values()}) >= 5
    assert "🔊 Сирена тревоги" in labels_by_style[2]["xtech"]
    assert "🖱 Инверсия мыши" in labels_by_style[3]["xtech"]
    assert "🔘 [Звук]" in labels_by_style[2]["xtech"]
    assert "🔘 [Ввод]" in labels_by_style[3]["xtech"]
    assert "🔴 [ВКЛ] Инверсия мыши" in swapped_labels_by_style["xtech"]
    assert swapped_labels_by_style["xtech"] != ["🖱 Инверсия мыши"]


def test_prank_chaos_and_meme_pages_use_all_six_voices_without_changing_actions(monkeypatch):
    callbacks_by_page = {4: {}, 5: {}}
    labels_by_page = {4: {}, 5: {}}

    for style in bot.xlex.STYLES:
        monkeypatch.setattr(
            bot.bot_settings,
            "get",
            lambda key, default=None, selected=style: selected if key == "ui_style" else default,
        )
        for page in (4, 5):
            menu = bot.pranks_menu(page=page)
            callbacks_by_page[page][style] = _callback_data(menu)
            labels = [button.text for row in menu.inline_keyboard for button in row]
            labels_by_page[page][style] = labels
            assert all(len(label.encode("utf-16-le")) // 2 <= 64 for label in labels)

    for page in (4, 5):
        baseline = callbacks_by_page[page][bot.xlex.STYLES[0]]
        assert all(callbacks == baseline for callbacks in callbacks_by_page[page].values())
        assert len({tuple(labels) for labels in labels_by_page[page].values()}) >= 5
    assert "🦠 Вирус Pivko" in labels_by_page[4]["xtech"]
    assert "💀 Удаление System32" in labels_by_page[4]["xtech"]
    assert "🐈 Выкуп котиками" in labels_by_page[5]["xtech"]
    assert "🔘 [Хаос]" in labels_by_page[4]["xtech"]
    assert "🔘 [Мемы]" in labels_by_page[5]["xtech"]
    assert any("фальшив" in label.lower() for label in labels_by_page[5]["xperson"])


def test_screen_menu_nightlight_toggle(monkeypatch):
    """Проверяет переключение текста и стиля ночного света в screen_menu."""
    target = "pc_test_2"
    monkeypatch.setitem(bot.SESSION, f"nightlight_{target}", False)
    m_off = bot.screen_menu(target=target)
    texts_off = [b.text for row in m_off.inline_keyboard for b in row]
    assert any("Включить ночной свет" in text for text in texts_off)

    monkeypatch.setitem(bot.SESSION, f"nightlight_{target}", True)
    m_on = bot.screen_menu(target=target)
    texts_on = [b.text for row in m_on.inline_keyboard for b in row]
    assert any("Выключить ночной свет" in text for text in texts_on)


def test_device_submenus_use_all_six_xlex_voices_without_changing_actions(monkeypatch):
    builders = (
        bot.wallpaper_menu,
        bot.input_menu,
        bot.system_menu,
        bot.network_menu,
        bot.files_menu,
        bot.terminal_menu,
    )
    callbacks_by_style = {}
    labels_by_style = {}

    for style in bot.xlex.STYLES:
        monkeypatch.setattr(
            bot.bot_settings,
            "get",
            lambda key, default=None, selected=style: selected if key == "ui_style" else default,
        )
        menus = [builder() for builder in builders]
        callbacks_by_style[style] = [
            [button.callback_data for row in menu.inline_keyboard for button in row]
            for menu in menus
        ]
        labels = [button.text for menu in menus for row in menu.inline_keyboard for button in row]
        labels_by_style[style] = labels
        assert all(len(label) <= 64 for label in labels)

    baseline = callbacks_by_style[bot.xlex.STYLES[0]]
    assert all(callbacks == baseline for callbacks in callbacks_by_style.values())
    assert len({tuple(labels) for labels in labels_by_style.values()}) >= 5
    assert "🎲 Случайный мем из сети" in labels_by_style["xtech"]
    assert any("обой" in label.lower() for label in labels_by_style["xpikmi"])


def test_settings_and_event_menus_use_all_six_xlex_voices_without_changing_actions(monkeypatch):
    target = "mac_test_1"
    monkeypatch.setitem(bot.SESSION, "target", target)
    monkeypatch.setattr(
        bot.bot_settings,
        "all_settings",
        lambda: {
            "notify_online": True,
            "notify_offline": False,
            "notify_battery_low": True,
            "quiet_from": "23",
            "quiet_to": "8",
            "report_hour": "9",
            "admins": [12345678],
        },
    )
    monkeypatch.setattr(bot, "get_server_autostart_status", lambda: True)

    builders = (
        bot.device_settings_menu,
        bot.events_menu,
        bot.quiet_hours_menu,
        bot.digest_menu,
        bot.admins_menu,
        bot.mic_options_menu,
        bot.vol_options_menu,
    )
    callbacks_by_style = {}
    labels_by_style = {}

    for style in bot.xlex.STYLES:
        monkeypatch.setattr(
            bot.bot_settings,
            "get",
            lambda key, default=None, selected=style: selected if key == "ui_style" else default,
        )
        menus = [builder() for builder in builders]
        callbacks_by_style[style] = [
            [button.callback_data for row in menu.inline_keyboard for button in row]
            for menu in menus
        ]
        labels = [button.text for menu in menus for row in menu.inline_keyboard for button in row]
        labels_by_style[style] = labels
        assert all(len(label.encode("utf-16-le")) // 2 <= 64 for label in labels)

    baseline_callbacks = callbacks_by_style[bot.xlex.STYLES[0]]
    assert all(callbacks == baseline_callbacks for callbacks in callbacks_by_style.values())
    assert len({tuple(labels) for labels in labels_by_style.values()}) >= 5
    assert "Версии агента и Guard Keeper" in labels_by_style["xtech"]
    assert any("тихонечко" in label.lower() for label in labels_by_style["xpikmi"])
    assert "🎙 5 сек" in labels_by_style["xtech"]
    assert "🎚 Уровень · 0%" in labels_by_style["xcore"]
    assert f"⭐ {bot.ADMIN_ID} (владелец)" in labels_by_style["xtech"]
    assert "➖ 12345678" in labels_by_style["xtech"]


def test_device_registry_menus_use_all_six_xlex_voices_without_changing_actions(monkeypatch):
    target = "mac_test_1"
    device = {
        "name": "Test Mac", "os": "macOS", "last_seen": 0, "version": "4.0.1",
        "favorites": ["screenshot"],
    }
    _setup(monkeypatch, {target: device})
    monkeypatch.setattr(
        bot.bot_settings,
        "all_settings",
        lambda: {"blocked_ids": ["blocked_test"]},
    )
    builders = (
        bot.devices_menu,
        lambda: bot.device_menu(target),
        bot.all_menu,
        bot.blocked_menu,
        bot.admin_menu,
        lambda: bot._favorites_kb(target, ["screenshot"]),
    )
    callbacks_by_style = {}
    labels_by_style = {}

    for style in bot.xlex.STYLES:
        monkeypatch.setattr(
            bot.bot_settings,
            "get",
            lambda key, default=None, selected=style: selected if key == "ui_style" else default,
        )
        menus = [builder() for builder in builders]
        callbacks_by_style[style] = [
            [button.callback_data for row in menu.inline_keyboard for button in row]
            for menu in menus
        ]
        labels = [button.text for menu in menus for row in menu.inline_keyboard for button in row]
        labels_by_style[style] = labels
        assert all(len(label.encode("utf-16-le")) // 2 <= 64 for label in labels)

    baseline_callbacks = callbacks_by_style[bot.xlex.STYLES[0]]
    assert all(callbacks == baseline_callbacks for callbacks in callbacks_by_style.values())
    assert len({tuple(labels) for labels in labels_by_style.values()}) >= 5
    assert "➕ Добавить" in labels_by_style["xtech"]
    assert "🔙 К списку устройств" in labels_by_style["xtech"]
    assert any("устройствечко" in label.lower() for label in labels_by_style["xpikmi"])
    assert "➖ Разблокировать blocked_test" in labels_by_style["xtech"]
    assert "⭐ 📸 Скриншот" in labels_by_style["xtech"]
    assert "X-LEX: X-TECH · технический" in labels_by_style["xtech"]


def test_destructive_device_confirm_labels_are_explicit_and_fit_telegram():
    keys = (
        "confirm_delete_device_button",
        "confirm_uninstall_agent_button",
        "confirm_block_device_button",
        "confirm_clear_devices_button",
    )
    technical_expected = (
        "✅ Да, удалить!",
        "🛑 Да, полностью удалить агент!",
        "⛔ Да, заблокировать!",
        "🧹 Да, очистить всё!",
    )
    for key, expected in zip(keys, technical_expected):
        labels = [bot.xlex.render(key, style) for style in bot.xlex.STYLES]
        assert labels[0] == expected
        assert len(set(labels)) >= 4
        assert all(len(label.encode("utf-16-le")) // 2 <= 64 for label in labels)


def test_generic_and_process_confirm_buttons_follow_xlex_style(monkeypatch):
    callbacks_by_style = {}
    labels_by_style = {}
    for style in bot.xlex.STYLES:
        monkeypatch.setattr(
            bot.bot_settings,
            "get",
            lambda key, default=None, selected=style: selected if key == "ui_style" else default,
        )
        menus = (
            bot.confirm_kb("confirm:generic"),
            bot.confirm_kb("confirm:kill", yes_text=bot._lex("confirm_kill_process_button")),
            bot.confirm_kb("confirm:yes", yes_text=bot._lex("confirm_yes_button")),
        )
        callbacks_by_style[style] = [_callback_data(menu) for menu in menus]
        labels_by_style[style] = [
            menu.inline_keyboard[0][0].text for menu in menus
        ]
        assert all(len(label.encode("utf-16-le")) // 2 <= 64 for label in labels_by_style[style])

    baseline = callbacks_by_style[bot.xlex.STYLES[0]]
    assert all(callbacks == baseline for callbacks in callbacks_by_style.values())
    assert labels_by_style["xtech"] == ["✅ Да, выполнить!", "✅ Да, убить!", "✅ Да!"]
    assert len({tuple(labels) for labels in labels_by_style.values()}) >= 5


def test_admin_guest_and_text_preview_rows_use_xlex_and_fit_utf16(monkeypatch):
    _setup(monkeypatch, {
        "device_guest": {"name": "Guest Test", "os": "macOS", "last_seen": 0},
    })
    monkeypatch.setattr(
        bot.access_store,
        "list_users",
        lambda: [{"id": 42, "username": "blocked_user", "display_name": ""}],
    )
    monkeypatch.setattr(bot.access_store, "get_role", lambda user_id, default: bot.Role.BLOCKED)
    monkeypatch.setattr(bot.text_store, "get", lambda key: "A short preview")
    menus_by_style = {}

    for style in bot.xlex.STYLES:
        monkeypatch.setattr(
            bot.bot_settings,
            "get",
            lambda key, default=None, selected=style: selected if key == "ui_style" else default,
        )
        menus = {
            "users": bot.admin_users_menu(),
            "guests": bot.guest_devices_menu(),
            "texts": bot.admin_texts_menu(),
        }
        menus_by_style[style] = menus
        for menu in menus.values():
            labels = [button.text for row in menu.inline_keyboard for button in row]
            assert all(len(label.encode("utf-16-le")) // 2 <= 64 for label in labels)

    for name in ("users", "guests"):
        baseline = _callback_data(menus_by_style["xtech"][name])
        assert all(_callback_data(menus_by_style[style][name]) == baseline for style in bot.xlex.STYLES)
    technical_user_labels = [
        button.text
        for row in menus_by_style["xtech"]["users"].inline_keyboard
        for button in row
    ]
    technical_guest_labels = [
        button.text
        for row in menus_by_style["xtech"]["guests"].inline_keyboard
        for button in row
    ]
    technical_text_labels = [
        button.text
        for row in menus_by_style["xtech"]["texts"].inline_keyboard
        for button in row
    ]
    assert "Заблокирован: @blocked_user — Заблокирован" in technical_user_labels
    assert "Guest Test: офлайн" in technical_guest_labels
    assert any(label.startswith("Приветствие гостя: A short preview") for label in technical_text_labels)
    assert any(
        "доступ закрыт" in button.text.lower()
        for row in menus_by_style["xpikmi"]["users"].inline_keyboard
        for button in row
    )
    assert any(
        "♡" in button.text
        for row in menus_by_style["xpikmi"]["guests"].inline_keyboard
        for button in row
    )
    assert bot._limit_button_label("😀" * 50, 62).endswith("…")
    assert len(bot._limit_button_label("😀" * 50, 62).encode("utf-16-le")) // 2 <= 62


def test_dynamic_device_and_confirmation_buttons_fit_utf16(monkeypatch):
    long_text = "😀" * 80
    _setup(monkeypatch, {
        "device-1": {
            "name": long_text,
            "os": "macOS",
            "version": long_text,
            "guardian": {"version": long_text},
            "last_seen": time.time(),
        },
    })
    monkeypatch.setattr(bot, "_status_dot", lambda _info: "🟢")

    device_labels = [
        button.text
        for row in bot.devices_menu().inline_keyboard
        for button in row
        if button.callback_data == "dev:device-1"
    ]
    detail_labels = [
        button.text
        for row in bot.device_menu("device-1").inline_keyboard
        for button in row
    ]
    confirmation_labels = [
        button.text
        for row in bot.confirm_kb("confirm:test", long_text).inline_keyboard
        for button in row
    ]

    assert len(device_labels) == 1 and device_labels[0].endswith("…")
    assert confirmation_labels[0].endswith("…")
    assert all(
        len(label.encode("utf-16-le")) // 2 <= 64
        for label in [*device_labels, *detail_labels, *confirmation_labels]
    )


def test_user_device_menu_only_shows_granted_devices_and_no_global_controls(monkeypatch):
    _setup(monkeypatch, {
        "device-1": {"name": "Granted Mac", "os": "macOS", "last_seen": time.time()},
        "device-2": {"name": "Private PC", "os": "Windows", "last_seen": time.time()},
    })
    monkeypatch.setattr(
        bot, "get_user_role",
        lambda user_id: bot.Role.USER if user_id == 200 else bot.Role.OWNER,
    )
    monkeypatch.setattr(
        bot.access_store, "get_user",
        lambda user_id: {"permissions": {"devices": ["device-1"]}} if user_id == 200 else None,
    )
    monkeypatch.setattr(bot, "SESSION", {"target": "device-1"})

    user_callbacks = _callback_data(bot.devices_menu(200))
    owner_callbacks = _callback_data(bot.devices_menu(100))
    user_device_settings = _callback_data(bot.device_settings_menu(200))

    assert "dev:device-1" in user_callbacks
    assert "dev:device-2" not in user_callbacks
    assert not any(callback.startswith("devmg:") for callback in user_callbacks)
    assert "devmg:clear_all" in owner_callbacks
    assert {"dev:device-1", "dev:device-2"}.issubset(owner_callbacks)
    assert not any(callback.startswith("devmg:") for callback in user_device_settings)
    assert "versions:device" not in user_device_settings
    assert "cmd:autorun_status" in user_device_settings


def test_single_action_grants_filter_categories_and_submenus(monkeypatch):
    _setup(monkeypatch, {
        "device-1": {
            "name": "Granted Mac", "os": "macOS", "standby": False,
            "favorites": ["screenshot", "webcam"],
        },
    })
    monkeypatch.setattr(bot, "get_user_role", lambda _user_id: bot.Role.USER)
    monkeypatch.setattr(bot, "SESSION", {"target": "device-1"})
    grants = {"cmd:screenshot", "cat:media", "back:device", "menu:main"}
    monkeypatch.setattr(
        bot.access_store, "can_use_callback",
        lambda _user_id, callback, _owner_id, _target: callback in grants,
    )

    device_callbacks = _callback_data(bot.device_menu("device-1", 200))
    media_callbacks = _callback_data(bot.media_menu(200))
    system_callbacks = _callback_data(bot.system_menu(200))
    power_callbacks = _callback_data(bot.power_menu_new(200))

    assert "cat:media" in device_callbacks
    assert not any(callback in device_callbacks for callback in ("cat:system", "cat:power"))
    assert "cmd:screenshot" in media_callbacks
    assert "cmd:webcam" not in media_callbacks
    assert "mic:opts" not in media_callbacks
    assert "cmd:sysinfo" not in system_callbacks
    assert "power:shutdown" not in power_callbacks
    assert "cmd:lock" not in power_callbacks

    grants.update({"cat:power", "cmd:lock"})
    locked_power_callbacks = _callback_data(bot.power_menu_new(200))
    assert "cmd:lock" in locked_power_callbacks
    assert "power:shutdown" not in locked_power_callbacks
    assert "power:reboot" not in locked_power_callbacks


def test_device_status_notices_confirm_transitions_without_heartbeat_starvation(monkeypatch):
    class MemoryDevices:
        def __init__(self):
            self.items = {"mac-1": {"name": "Mac", "online": False, "last_seen": 0}}

        def all(self):
            return dict(self.items)

        def get(self, device_id):
            return self.items.get(device_id)

        def upsert(self, device_id, info):
            self.items[device_id] = {**self.items.get(device_id, {}), **info}

    async def _run():
        notices = []
        store = MemoryDevices()
        monkeypatch.setattr(bot, "devices", store)
        monkeypatch.setattr(bot, "LOOP", asyncio.get_running_loop())
        monkeypatch.setattr(bot, "_STATUS_NOTICE_STATE", {})
        monkeypatch.setattr(bot, "_STATUS_NOTICE_PENDING", {})
        monkeypatch.setattr(bot, "_STATUS_NOTICE_DELAY_SEC", 0.02)
        monkeypatch.setattr(bot, "ENCRYPT_PAYLOAD", False)
        monkeypatch.setattr(bot, "verify_message_detailed", lambda payload: (payload, "ok"))
        monkeypatch.setattr(bot, "audit", lambda *args, **kwargs: None)
        monkeypatch.setattr(bot, "_schedule_admin_notice", lambda text, **kwargs: notices.append(text))
        monkeypatch.setattr(bot.bot_settings, "is_blocked", lambda _device_id: False)
        monkeypatch.setattr(bot.bot_settings, "quiet_active", lambda: False)
        monkeypatch.setattr(
            bot.bot_settings,
            "get",
            lambda key, default=None: True if key in {"notify_online", "notify_offline"} else default,
        )

        def status(value, update_mode="source", release_update_ready=False):
            bot.on_mqtt_message(
                f"{bot.MQTT_PREFIX}/mac-1/status",
                {
                    "type": "status", "status": value, "name": "Mac", "os": "macOS",
                    "version": "test", "update_mode": update_mode,
                    "release_update_ready": release_update_ready,
                },
            )

        status("online", release_update_ready=True)
        assert store.get("mac-1")["update_mode"] == "source"
        assert store.get("mac-1")["release_update_ready"] is True
        status("online", update_mode=[])
        assert store.get("mac-1")["update_mode"] == "unknown"
        assert store.get("mac-1")["release_update_ready"] is False
        await asyncio.sleep(0.05)
        assert len(notices) == 1 and "вернулся онлайн" in notices[0]

        # Repeated heartbeats must neither resend nor push the transition timer out.
        status("online")
        status("online")
        await asyncio.sleep(0.05)
        assert len(notices) == 1

        # A brief broker flap is cancelled when the device returns before confirmation.
        status("offline")
        status("online")
        await asyncio.sleep(0.05)
        assert len(notices) == 1

        status("offline")
        await asyncio.sleep(0.05)
        assert len(notices) == 2 and "ушёл в оффлайн" in notices[1]

        status("online")
        await asyncio.sleep(0.05)
        assert len(notices) == 3 and "вернулся онлайн" in notices[2]

    asyncio.run(_run())


def test_response_collector_wait_for_and_race_safety():
    """Проверяет изолированное тикетное ожидание wait_for без взаимного затирания."""
    async def _async_test():
        collector = bot.ResponseCollector()
        bot.LOOP = asyncio.get_running_loop()

        # Запускаем два параллельных ожидания разных команд
        task_wifi = asyncio.create_task(collector.wait_for("dev1", "net_wifi_passwords", timeout=2.0))
        task_usb = asyncio.create_task(collector.wait_for("dev1", "usb_devices", timeout=2.0))

        await asyncio.sleep(0.01)

        # Приходит ответ от USB первым
        collector.submit("dev1", {"type": "usb_devices", "text": "USB flash drive"})
        # Затем от WiFi
        collector.submit("dev1", {"type": "net_wifi_passwords", "text": "Home_WiFi: 12345"})

        res_usb = await task_usb
        res_wifi = await task_wifi

        assert res_usb is not None and res_usb.get("text") == "USB flash drive"
        assert res_wifi is not None and res_wifi.get("text") == "Home_WiFi: 12345"

    asyncio.run(_async_test())


def test_response_collector_keeps_fast_response_for_command_id():
    """Ответ, пришедший сразу после публикации, не теряется до wait_for."""
    async def _async_test():
        collector = bot.ResponseCollector()
        bot.LOOP = asyncio.get_running_loop()
        collector.submit("dev1", {"type": "battery", "id": "cmd123", "percent": 88})
        result = await collector.wait_for("dev1", "battery", timeout=0.2, command_id="cmd123")
        assert result is not None
        assert result.get("percent") == 88

    asyncio.run(_async_test())


def test_custom_ui_mode_changes_admin_label_and_main_menu(monkeypatch):
    monkeypatch.setattr(bot.bot_settings, "get", lambda key, default=None: "custom" if key == "ui_style" else default)
    markup = bot.main_menu(bot.ADMIN_ID)
    texts = [b.text for row in markup.inline_keyboard for b in row]
    assert "Устройства · кто тут живой?" in texts
    assert "Все устройства · полный состав" in texts


def test_start_sends_visible_card_before_deleting_old_one(monkeypatch):
    from types import SimpleNamespace

    calls = []

    class FakeBot:
        async def edit_message_text(self, *args, **kwargs):
            calls.append(("edit", kwargs["message_id"]))

        async def delete_message(self, *args, **kwargs):
            calls.append(("delete", args[1]))

    class FakeMessage:
        chat = SimpleNamespace(id=10)
        from_user = SimpleNamespace(id=20)

        async def answer(self, *args, **kwargs):
            calls.append(("send", None))
            return SimpleNamespace(chat=self.chat, message_id=31)

    monkeypatch.setattr(bot, "bot", FakeBot())
    monkeypatch.setattr(bot.ui_cards, "get", lambda *args: 30)
    monkeypatch.setattr(bot.ui_cards, "set_card", lambda *args: calls.append(("store", args[2])))

    assert asyncio.run(bot._show_start_card(FakeMessage(), "Главное меню", None)) == 31
    assert calls == [("send", None), ("delete", 30), ("store", 31)]


def test_start_saves_new_id_when_old_card_was_already_deleted(monkeypatch):
    from types import SimpleNamespace

    calls = []

    class FakeBot:
        async def edit_message_text(self, *args, **kwargs):
            raise RuntimeError("message to edit not found")

        async def delete_message(self, *args, **kwargs):
            raise RuntimeError("message to delete not found")

    class FakeMessage:
        chat = SimpleNamespace(id=10)
        from_user = SimpleNamespace(id=20)

        async def answer(self, *args, **kwargs):
            calls.append(("send", None))
            return SimpleNamespace(chat=self.chat, message_id=31)

    monkeypatch.setattr(bot, "bot", FakeBot())
    monkeypatch.setattr(bot.ui_cards, "get", lambda *args: 30)
    monkeypatch.setattr(bot.ui_cards, "set_card", lambda *args: calls.append(("store", args[2])))

    assert asyncio.run(bot._show_start_card(FakeMessage(), "Главное меню", None)) == 31
    assert calls == [("send", None), ("store", 31)]


def test_user_card_result_edits_the_registered_card(monkeypatch):
    from types import SimpleNamespace

    calls = []

    class FakeBot:
        async def edit_message_text(self, text, **kwargs):
            calls.append(("edit", text, kwargs["chat_id"], kwargs["message_id"]))

    class FakeMessage:
        chat = SimpleNamespace(id=10)
        from_user = SimpleNamespace(id=20)

        async def answer(self, *args, **kwargs):
            raise AssertionError("A current card should be edited, not duplicated")

    monkeypatch.setattr(bot, "bot", FakeBot())
    monkeypatch.setattr(bot.ui_cards, "get", lambda *args: 30)

    result = asyncio.run(bot._replace_user_card(FakeMessage(), "Updated", reply_markup="kb"))

    assert result == 30
    assert calls == [("edit", "Updated", 10, 30)]


def test_user_card_does_not_duplicate_on_transient_edit_error(monkeypatch):
    from types import SimpleNamespace

    class FakeBot:
        async def edit_message_text(self, *args, **kwargs):
            raise RuntimeError("temporary network failure")

        async def delete_message(self, *args, **kwargs):
            raise AssertionError("A transient failure must not delete the current card")

    class FakeMessage:
        chat = SimpleNamespace(id=10)
        from_user = SimpleNamespace(id=20)

        async def answer(self, *args, **kwargs):
            raise AssertionError("A transient failure must not send a duplicate")

    monkeypatch.setattr(bot, "bot", FakeBot())
    monkeypatch.setattr(bot.ui_cards, "get", lambda *args: 30)

    assert asyncio.run(bot._replace_user_card(FakeMessage(), "Updated")) == 30


def test_user_card_replaces_media_card_when_text_edit_is_unsupported(monkeypatch):
    from types import SimpleNamespace

    calls = []

    class FakeBot:
        async def edit_message_text(self, *args, **kwargs):
            raise RuntimeError("there is no text in the message to edit")

        async def delete_message(self, chat_id, message_id):
            calls.append(("delete", chat_id, message_id))

    class FakeMessage:
        chat = SimpleNamespace(id=10)
        from_user = SimpleNamespace(id=20)
        message_id = 21

        async def answer(self, text, **kwargs):
            calls.append(("send", text, kwargs.get("reply_markup")))
            return SimpleNamespace(chat=self.chat, message_id=31)

    monkeypatch.setattr(bot, "bot", FakeBot())
    monkeypatch.setattr(bot.ui_cards, "get", lambda *args: 30)
    monkeypatch.setattr(bot.ui_cards, "set_card", lambda *args: calls.append(("store", args[2])))

    result = asyncio.run(bot._replace_user_card(FakeMessage(), "Next page", reply_markup="kb"))

    assert result == 31
    assert calls == [
        ("send", "Next page", "kb"),
        ("delete", 10, 30),
        ("store", 31),
    ]


def test_user_card_replaces_only_a_proven_stale_card(monkeypatch):
    from types import SimpleNamespace

    calls = []

    class FakeBot:
        async def edit_message_text(self, *args, **kwargs):
            raise RuntimeError("message to edit not found")

        async def delete_message(self, *args, **kwargs):
            calls.append(("delete", args[1]))

    class FakeMessage:
        chat = SimpleNamespace(id=10)
        from_user = SimpleNamespace(id=20)

        async def answer(self, *args, **kwargs):
            calls.append(("send", args[0]))
            return SimpleNamespace(message_id=31)

    monkeypatch.setattr(bot, "bot", FakeBot())
    monkeypatch.setattr(bot.ui_cards, "get", lambda *args: 30)
    monkeypatch.setattr(bot.ui_cards, "set_card", lambda *args: calls.append(("store", args[2])))

    assert asyncio.run(bot._replace_user_card(FakeMessage(), "Replacement")) == 31
    assert calls == [("send", "Replacement"), ("store", 31)]


def test_callback_card_does_not_duplicate_on_transient_edit_error(monkeypatch):
    from types import SimpleNamespace

    class FakeMessage:
        chat = SimpleNamespace(id=10)
        message_id = 30

        async def edit_text(self, *args, **kwargs):
            raise RuntimeError("temporary network failure")

        async def answer(self, *args, **kwargs):
            raise AssertionError("A transient failure must not create a second card")

    class FakeCallback:
        from_user = SimpleNamespace(id=20)
        message = FakeMessage()

    asyncio.run(bot._replace_callback_message(FakeCallback(), "Updated"))


def test_callback_media_card_transitions_to_text_without_leaving_old_card(monkeypatch):
    from types import SimpleNamespace

    calls = []

    class FakeBot:
        async def delete_message(self, chat_id, message_id):
            calls.append(("delete", chat_id, message_id))

    class FakeMessage:
        chat = SimpleNamespace(id=10)
        message_id = 30
        photo = [object()]

        async def edit_text(self, *args, **kwargs):
            raise AssertionError("A media card must be replaced instead of edited as text")

        async def answer(self, text, **kwargs):
            calls.append(("send", text, kwargs.get("reply_markup")))
            return SimpleNamespace(chat=self.chat, message_id=31)

    class FakeCallback:
        from_user = SimpleNamespace(id=20)
        message = FakeMessage()

    monkeypatch.setattr(bot, "bot", FakeBot())
    monkeypatch.setattr(bot.ui_cards, "set_card", lambda *args: calls.append(("store", args[2])))

    result = asyncio.run(
        bot._replace_callback_message(FakeCallback(), "Device menu", reply_markup="kb")
    )

    assert result.message_id == 31
    assert calls == [
        ("send", "Device menu", "kb"),
        ("delete", 10, 30),
        ("store", 31),
    ]


def test_media_card_disables_old_keyboard_if_delete_fails(monkeypatch):
    from types import SimpleNamespace

    calls = []

    class FakeBot:
        async def delete_message(self, chat_id, message_id):
            calls.append(("delete", chat_id, message_id))
            raise RuntimeError("temporary delete failure")

        async def edit_message_reply_markup(self, **kwargs):
            calls.append(("disable_markup", kwargs["chat_id"], kwargs["message_id"]))

    class FakeMessage:
        chat = SimpleNamespace(id=10)
        message_id = 30
        photo = [object()]

        async def answer(self, text, **kwargs):
            calls.append(("send", text))
            return SimpleNamespace(chat=self.chat, message_id=31)

    class FakeCallback:
        from_user = SimpleNamespace(id=20)
        message = FakeMessage()

    monkeypatch.setattr(bot, "bot", FakeBot())
    monkeypatch.setattr(bot.ui_cards, "set_card", lambda *args: calls.append(("store", args[2])))

    result = asyncio.run(bot._replace_callback_message(FakeCallback(), "Next"))

    assert result.message_id == 31
    assert calls == [
        ("send", "Next"),
        ("delete", 10, 30),
        ("disable_markup", 10, 30),
        ("store", 31),
    ]


def test_callback_result_media_replaces_text_card_and_stores_media_id(monkeypatch):
    from types import SimpleNamespace

    calls = []

    class FakeBot:
        async def delete_message(self, chat_id, message_id):
            calls.append(("delete", chat_id, message_id))

    class FakeMessage:
        chat = SimpleNamespace(id=10)
        message_id = 30

    class FakeCallback:
        from_user = SimpleNamespace(id=20)
        message = FakeMessage()

    async def send_media(chat_id, **kwargs):
        calls.append(("send", chat_id, kwargs["voice"], kwargs["reply_markup"]))
        return SimpleNamespace(chat=SimpleNamespace(id=chat_id), message_id=31)

    monkeypatch.setattr(bot, "bot", FakeBot())
    monkeypatch.setattr(bot.ui_cards, "set_card", lambda *args: calls.append(("store", args[2])))
    audio = object()

    result = asyncio.run(
        bot._replace_callback_with_media(FakeCallback(), send_media, voice=audio)
    )

    assert result.message_id == 31
    assert calls[0] == ("send", 20, audio, bot.back_to_device_kb())
    assert calls[1:] == [("delete", 10, 30), ("store", 31)]


def test_server_chart_replaces_the_previous_card_with_one_media_card(monkeypatch):
    from types import SimpleNamespace

    calls = []

    class FakeBot:
        async def send_photo(self, chat_id, **kwargs):
            calls.append(("send", chat_id, kwargs["reply_markup"], kwargs["caption"]))
            return SimpleNamespace(chat=SimpleNamespace(id=chat_id), message_id=31)

        async def delete_message(self, chat_id, message_id):
            calls.append(("delete", chat_id, message_id))

    class FakeMessage:
        chat = SimpleNamespace(id=10)
        message_id = 30

    class FakeCallback:
        from_user = SimpleNamespace(id=20)
        message = FakeMessage()

        async def answer(self, *args, **kwargs):
            calls.append(("callback_answer",))

    monkeypatch.setattr(bot, "bot", FakeBot())
    monkeypatch.setattr(bot.server_ops, "metrics", lambda: object())
    monkeypatch.setattr(bot.server_ops, "render_metrics_chart", lambda snapshot: b"png")
    monkeypatch.setattr(bot.access_store, "append_audit", lambda *args, **kwargs: None)
    monkeypatch.setattr(bot.ui_cards, "set_card", lambda *args: calls.append(("store", args[2])))

    asyncio.run(bot.on_server_chart(FakeCallback()))

    assert calls[0][0:2] == ("send", 10)
    assert calls[1] == ("delete", 10, 30)
    assert calls[2] == ("store", 31)
    assert calls[3] == ("callback_answer",)


def test_callback_card_replacement_requires_stale_confirmation(monkeypatch):
    from types import SimpleNamespace

    calls = []

    class FakeBot:
        async def delete_message(self, chat_id, message_id):
            raise RuntimeError("message to delete not found")

    class FakeMessage:
        chat = SimpleNamespace(id=10)
        message_id = 30

        async def edit_text(self, *args, **kwargs):
            raise RuntimeError("message can't be edited")

        async def answer(self, *args, **kwargs):
            calls.append(("send", args[0]))
            return SimpleNamespace(chat=self.chat, message_id=31)

    class FakeCallback:
        from_user = SimpleNamespace(id=20)
        message = FakeMessage()

    monkeypatch.setattr(bot, "bot", FakeBot())
    monkeypatch.setattr(bot.ui_cards, "set_card", lambda *args: calls.append(("store", args[2])))

    asyncio.run(bot._replace_callback_message(FakeCallback(), "Updated"))

    assert calls == []


def test_callback_card_replacement_updates_registry_when_old_card_is_gone(monkeypatch):
    from types import SimpleNamespace

    calls = []

    class FakeMessage:
        chat = SimpleNamespace(id=10)
        message_id = 30

        async def edit_text(self, *args, **kwargs):
            raise RuntimeError("message to edit not found")

        async def answer(self, *args, **kwargs):
            calls.append(("send", args[0]))
            return SimpleNamespace(chat=self.chat, message_id=31)

    class FakeCallback:
        from_user = SimpleNamespace(id=20)
        message = FakeMessage()

    monkeypatch.setattr(bot.ui_cards, "set_card", lambda *args: calls.append(("store", args[2])))

    asyncio.run(bot._replace_callback_message(FakeCallback(), "Updated"))

    assert calls == [("send", "Updated"), ("store", 31)]


def test_file_action_prompt_replaces_current_card(monkeypatch):
    from types import SimpleNamespace

    calls = []

    class FakeState:
        async def set_state(self, state):
            calls.append(("state", state))

        async def update_data(self, **data):
            calls.append(("data", data))

    class FakeCallback:
        data = "files:get"
        from_user = SimpleNamespace(id=1)
        message = SimpleNamespace()

        async def answer(self, *args, **kwargs):
            calls.append(("answer", args, kwargs))

    async def replace(_cq, text, **kwargs):
        calls.append(("replace", text, kwargs))

    monkeypatch.setitem(bot.SESSION, "target", "device-1")
    monkeypatch.setattr(bot, "_replace_callback_message", replace)

    asyncio.run(bot.on_files_get(FakeCallback(), FakeState()))

    assert calls[0][0] == "state"
    assert calls[1] == (
        "data",
        {
            "path_mode": "get",
            "target": "device-1",
            "authorization_callback": "files:get",
            "authorization_target": "device-1",
        },
    )
    assert calls[2][0] == "replace"
    assert calls[2][1] == bot._lex("files_prompt_get")
    assert calls[3][0] == "answer"


def test_transfer_filename_is_one_portable_safe_path_component():
    assert bot._safe_transfer_filename(r"../../report.pdf") == "report.pdf"
    assert bot._safe_transfer_filename(r"..\..\NUL.txt") == "_NUL.txt"
    assert bot._safe_transfer_filename("bad:name?.txt") == "bad_name_.txt"
    assert bot._safe_transfer_filename("../") == "file.bin"
    assert len(bot._safe_transfer_filename("😀" * 200).encode("utf-8")) <= 255


def test_file_upload_uses_sanitized_filename_in_remote_path(monkeypatch):
    from types import SimpleNamespace

    published = []
    rendered = []

    class FakeState:
        async def get_data(self):
            return {
                "path_mode": "put",
                "target": "device-1",
                "authorization_callback": "files:put",
                "authorization_target": "device-1",
            }

        async def clear(self):
            pass

    class FakeBot:
        async def download(self, _document):
            return SimpleNamespace(read=lambda: b"file-content")

    class FakeCollector:
        async def wait_for(self, device_id, action_type, *, timeout, command_id):
            assert device_id == "device-1"
            assert action_type == "file_put"
            assert timeout == 25.0
            assert command_id
            return {"ok": True, "path": "~/Downloads/_NUL.txt"}

    class FakeMessage:
        from_user = SimpleNamespace(id=20)
        text = None
        document = SimpleNamespace(file_name=r"..\..\NUL.txt")

    async def replace(_message, text, **kwargs):
        rendered.append((text, kwargs))

    # Selection may change after the upload prompt; the transfer stays pinned
    # to the device whose file screen was opened.
    monkeypatch.setitem(bot.SESSION, "target", "device-2")
    monkeypatch.setattr(bot, "bot", FakeBot())
    monkeypatch.setattr(bot, "fun_text_collector", FakeCollector())
    monkeypatch.setattr(bot, "_replace_user_card", replace)
    monkeypatch.setattr(bot, "get_user_role", lambda _user_id: bot.Role.USER)
    monkeypatch.setattr(bot, "_device_action_still_allowed", lambda *_args: True)
    monkeypatch.setattr(
        bot,
        "publish",
        lambda action, **kwargs: published.append((action, kwargs)) or True,
    )

    asyncio.run(bot.on_path_input(FakeMessage(), FakeState()))

    assert published[0][0] == "file_put"
    assert published[0][1]["name"] == "_NUL.txt"
    assert published[0][1]["path"] == "~/Downloads/_NUL.txt"
    assert published[0][1]["_target"] == "device-1"
    assert published[0][1]["id"]
    assert b".." not in published[0][1]["path"].encode()
    assert rendered


def test_file_upload_aborts_if_permission_is_revoked_during_download(monkeypatch):
    from types import SimpleNamespace

    cards = []
    published = []
    checks = []
    allowed = True

    class FakeState:
        async def get_data(self):
            return {
                "path_mode": "put",
                "target": "device-1",
                "authorization_callback": "files:put",
                "authorization_target": "device-1",
            }

        async def clear(self):
            pass

    class FakeBot:
        async def download(self, _document):
            nonlocal allowed
            allowed = False
            return SimpleNamespace(read=lambda: b"private file contents")

    class FakeMessage:
        from_user = SimpleNamespace(id=20)
        text = None
        document = SimpleNamespace(file_name="upload.txt")

    async def replace(_message, text, **kwargs):
        cards.append((text, kwargs))

    def action_is_allowed(user_id, callback, target):
        checks.append((user_id, callback, target))
        return allowed

    monkeypatch.setattr(bot, "bot", FakeBot())
    monkeypatch.setattr(bot, "_replace_user_card", replace)
    monkeypatch.setattr(bot, "get_user_role", lambda _user_id: bot.Role.USER)
    monkeypatch.setattr(bot, "_device_action_still_allowed", action_is_allowed)
    monkeypatch.setattr(
        bot,
        "publish",
        lambda action, **kwargs: published.append((action, kwargs)) or True,
    )

    asyncio.run(bot.on_path_input(FakeMessage(), FakeState()))

    assert checks == [(20, "files:put", "device-1")]
    assert published == []
    assert bot._lex("input_access_revoked") in cards[-1][0]


def test_post_publish_revoke_message_is_accurate_in_all_six_styles():
    messages = {
        style: bot.xlex.render("action_result_hidden_after_revoke", style)
        for style in bot.xlex.STYLES
    }

    assert len(set(messages.values())) == 6
    for message in messages.values():
        lowered = message.lower()
        assert "мог" in lowered or "возможно" in lowered
        assert "результат" in lowered
        assert "скрыт" in lowered or "не показываю" in lowered
        assert "не отправ" not in lowered
        assert "не ушла" not in lowered


@pytest.mark.parametrize("operation", ["put", "list", "get", "del", "open", "find"])
def test_file_operation_revoke_after_publish_reports_possible_execution_and_hides_result(
    monkeypatch, operation
):
    from types import SimpleNamespace

    cards = []
    published = []
    checks = []
    allowed = True
    callback = f"files:{operation}"

    class FakeState:
        async def get_data(self):
            return {
                "path_mode": operation,
                "target": "device-1",
                "authorization_callback": callback,
                "authorization_target": "device-1",
            }

        async def clear(self):
            pass

    class FakeBot:
        async def download(self, _document):
            return SimpleNamespace(read=lambda: b"contents")

    class FakeCollector:
        async def wait_for(self, *_args, **_kwargs):
            nonlocal allowed
            allowed = False
            return {
                "ok": True,
                "text": "private directory listing",
                "path": "~/Downloads/upload.txt",
                "data": base64.b64encode(b"private file contents").decode("ascii"),
                "filename": "private.txt",
            }

    class FakeMessage:
        from_user = SimpleNamespace(id=20)
        text = None if operation == "put" else "Documents/report.txt"
        document = SimpleNamespace(file_name="upload.txt") if operation == "put" else None

    async def replace(_message, text, **kwargs):
        cards.append((text, kwargs))

    def action_is_allowed(user_id, granted_callback, target):
        checks.append((user_id, granted_callback, target, allowed))
        return allowed

    monkeypatch.setattr(bot, "bot", FakeBot())
    monkeypatch.setattr(bot, "fun_text_collector", FakeCollector())
    monkeypatch.setattr(bot, "file_collector", FakeCollector())
    monkeypatch.setattr(bot, "_replace_user_card", replace)
    monkeypatch.setattr(
        bot,
        "_replace_user_card_with_document",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("result must be withheld after permission revocation")
        ),
    )
    monkeypatch.setattr(bot, "get_user_role", lambda _user_id: bot.Role.USER)
    monkeypatch.setattr(bot, "_device_action_still_allowed", action_is_allowed)
    monkeypatch.setattr(
        bot,
        "publish_tracked",
        lambda action, **kwargs: published.append((action, kwargs)) or (True, "op-1"),
    )

    handler = bot.on_find_input if operation == "find" else bot.on_path_input
    asyncio.run(handler(FakeMessage(), FakeState()))

    assert len(published) == 1
    assert [check[3] for check in checks] == [True, False]
    assert cards[-1][0] == bot._lex("action_result_hidden_after_revoke")
    assert "не отправ" not in cards[-1][0].lower()
    assert "мог" in cards[-1][0].lower() or "возможно" in cards[-1][0].lower()


def test_user_fsm_handlers_revalidate_before_device_side_effects():
    tree = ast.parse(Path(bot.__file__).read_text(encoding="utf-8"))
    functions = {
        node.name: node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    guarded_handlers = {
        "on_open_app_input",
        "on_url_input",
        "on_text_input",
        "on_sound_input",
        "on_clipset_input",
        "on_path_input",
        "on_find_input",
        "_run_text_prank_input",
        "on_fun_text_input",
        "on_fun_hotkey_input",
        "on_fun_wallpaper_input",
        "on_photo_wallpaper_message",
        "on_brightness_input",
        "on_prockill_input",
    }
    for name in guarded_handlers:
        calls = {
            call.func.id
            for call in ast.walk(functions[name])
            if isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
        }
        assert "_pending_device_input_still_allowed" in calls, name

    # These form wrappers delegate to the common guarded prank-input routine.
    for name in ("on_fun_spam_input", "on_prank_shout_input"):
        assert any(
            isinstance(call, ast.Call)
            and isinstance(call.func, ast.Name)
            and call.func.id == "_run_text_prank_input"
            for call in ast.walk(functions[name])
        ), name


def test_successful_file_put_ack_is_routed_to_text_collector(monkeypatch):
    received = []

    class FakeCollector:
        def submit(self, device_id, payload):
            received.append((device_id, payload))

    monkeypatch.setattr(bot, "ENCRYPT_PAYLOAD", False)
    monkeypatch.setattr(bot, "verify_message_detailed", lambda payload: (payload, "ok"))
    monkeypatch.setattr(bot.bot_settings, "is_blocked", lambda _device_id: False)
    monkeypatch.setattr(bot, "fun_text_collector", FakeCollector())

    bot.on_mqtt_message(
        f"{bot.MQTT_PREFIX}/device-1/file_put",
        {"type": "file_put", "ok": True, "path": "~/Downloads/file.bin", "id": "op-123"},
    )

    assert received == [(
        "device-1",
        {"type": "file_put", "ok": True, "path": "~/Downloads/file.bin", "id": "op-123"},
    )]


@pytest.mark.parametrize("kind,state,ok", [
    ("agent_update", "restart_failed", False),
    ("agent_update", "guardian_restart_failed", True),
    ("uninstall_agent", "uninstall_incomplete", False),
])
def test_deferred_lifecycle_failures_notify_owner_once(monkeypatch, kind, state, ok):
    notices = []
    monkeypatch.setattr(bot, "_LIFECYCLE_NOTICE_LAST", {})
    monkeypatch.setattr(bot.bot_settings, "is_blocked", lambda _device: False)
    monkeypatch.setattr(bot, "target_label", lambda _device: "Mac <owner>")
    monkeypatch.setattr(bot, "_schedule_owner_notice", lambda text: notices.append(text) or True)
    payload = {"type": kind, "state": state, "ok": ok, "id": "op-1", "text": "failure <detail>"}
    bot._schedule_lifecycle_failure_notice("device-1", payload)
    bot._schedule_lifecycle_failure_notice("device-1", payload)
    assert len(notices) == 1
    assert "&lt;owner&gt;" in notices[0]
    assert "&lt;detail&gt;" in notices[0]
    assert state in notices[0]


def test_lifecycle_notice_is_not_consumed_without_running_loop(monkeypatch):
    monkeypatch.setattr(bot, "_LIFECYCLE_NOTICE_LAST", {})
    monkeypatch.setattr(bot, "LOOP", None)
    monkeypatch.setattr(bot.bot_settings, "is_blocked", lambda _device: False)
    bot._schedule_lifecycle_failure_notice("device-1", {
        "type": "agent_update", "state": "restart_failed", "ok": False, "id": "op-2",
    })
    assert not bot._LIFECYCLE_NOTICE_LAST


def test_lifecycle_notice_ignores_prepared_and_bounds_duplicate_cache(monkeypatch):
    notices = []
    monkeypatch.setattr(bot, "_LIFECYCLE_NOTICE_LAST", {})
    monkeypatch.setattr(bot.bot_settings, "is_blocked", lambda _device: False)
    monkeypatch.setattr(bot, "_schedule_owner_notice", lambda text: notices.append(text) or True)
    bot._schedule_lifecycle_failure_notice("device-1", {
        "type": "uninstall_agent", "state": "uninstall_prepared", "ok": True,
    })
    assert not notices
    for index in range(140):
        bot._schedule_lifecycle_failure_notice("device-1", {
            "type": "agent_update", "state": "restart_failed", "ok": False, "id": str(index),
        })
    assert len(bot._LIFECYCLE_NOTICE_LAST) == 128


def test_lifecycle_notice_sends_only_to_owner(monkeypatch):
    sent = []
    class FakeBot:
        async def send_message(self, user_id, text):
            sent.append((user_id, text))
    monkeypatch.setattr(bot, "bot", FakeBot())
    asyncio.run(bot._notify_owner("operation failed"))
    assert sent == [(int(bot.ADMIN_ID), "operation failed")]


def test_server_menu_contains_metrics_chart_and_safe_terminal():
    markup = bot.server_menu(bot.ADMIN_ID)
    callbacks = _callback_data(markup)
    assert {"server:specs", "server:metrics", "server:chart", "server:terminal"}.issubset(set(callbacks))
    rows = [[button.callback_data for button in row] for row in markup.inline_keyboard]
    assert rows == [
        ["versions:server", "server:status"],
        ["server:specs", "server:metrics"],
        ["server:chart", "server:logs"],
        ["server:terminal", "server:approval"],
        ["server:restart", "server:update"],
        ["server:rollback", "menu:main"],
    ]


def test_device_card_shows_guardian_state(monkeypatch):
    monkeypatch.setattr(bot.devices, "get", lambda device_id: {
        "name": "MacBook-Air.local", "os": "macOS 27.0", "version": "3.3.8",
        "online": True, "last_seen": __import__("time").time(),
        "guardian": {"version": "1.0"},
        "guardian_last_seen": __import__("time").time(),
    })
    text = bot.device_card("mac-1")
    assert "Guardian" in text
    assert "v1.0" in text


def test_invalid_mqtt_envelope_logs_reason_without_payload(monkeypatch, caplog):
    monkeypatch.setattr(
        bot,
        "verify_message_detailed",
        lambda _data: (None, "bad_hmac"),
    )

    with caplog.at_level("WARNING", logger="xgent.bot"):
        bot.on_mqtt_message(
            f"{bot.MQTT_PREFIX}/device-1/status",
            {"payload": {"sensitive_text": "must-not-be-logged"}},
        )

    assert "bad_hmac" in caplog.text
    assert "must-not-be-logged" not in caplog.text
