"""Тесты UI-редизайна бота: device_card и новые категорийные меню (2026-09-05)."""

import asyncio
import ast
import base64
import datetime
import json
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

import bot


@pytest.mark.parametrize("style", ("xtech", "xperson", "xpikmi", "xtarped", "xcore", "xadam"))
def test_network_traffic_threshold_button_uses_neutral_catalog_copy(style, monkeypatch):
    monkeypatch.setattr(bot.bot_settings, "get", lambda _key, default=None: style)
    markup = bot._network_traffic_markup("device-1", interface_id="a" * 20)
    labels = [button.text for row in markup.inline_keyboard for button in row]

    assert bot.feature_catalog.button_text("traffic_threshold") == "🔔 Порог трафика"
    assert "🔔 Порог трафика" in labels
    assert "network_traffic_alert_setup" not in bot.xlex.COPY


def test_expiring_grant_reminder_sends_one_plain_action_and_marks_sent(monkeypatch):
    row = {"user_id": 200, "kind": "callback", "item": "cmd:status", "expires_at": 1_800_000_000}
    monkeypatch.setattr(bot.access_store, "list_expiring_grants", lambda **kwargs: [row])
    monkeypatch.setattr(bot.access_store, "claim_expiry_reminder", lambda **kwargs: True)
    outcomes = []
    monkeypatch.setattr(bot.access_store, "finish_expiry_reminder", lambda **kwargs: outcomes.append(kwargs) or True)
    sent = []

    async def send_message(user_id, text):
        sent.append((user_id, text))

    monkeypatch.setattr(bot.bot, "send_message", send_message)
    assert asyncio.run(bot._notify_expiring_grants_once(now=1_799_999_100)) == 1
    assert sent[0][0] == 200
    assert "временное право доступа" in sent[0][1]
    assert "cmd:status" in sent[0][1]
    assert "UTC" in sent[0][1]
    assert outcomes == [{**row, "sent": True, "now": 1_799_999_100}]


def test_expiring_grant_delivery_failure_releases_claim_for_retry(monkeypatch):
    row = {"user_id": 201, "kind": "device", "item": "mac-air", "expires_at": 1_800_000_000}
    monkeypatch.setattr(bot.access_store, "list_expiring_grants", lambda **kwargs: [row])
    monkeypatch.setattr(bot.access_store, "claim_expiry_reminder", lambda **kwargs: True)
    outcomes = []
    monkeypatch.setattr(bot.access_store, "finish_expiry_reminder", lambda **kwargs: outcomes.append(kwargs) or True)

    async def fail_send(user_id, text):
        raise RuntimeError("telegram unavailable")

    monkeypatch.setattr(bot.bot, "send_message", fail_send)
    assert asyncio.run(bot._notify_expiring_grants_once(now=1_799_999_100)) == 0
    assert outcomes == [{**row, "sent": False, "now": 1_799_999_100}]
import roles


def test_prank_stop_all_ack_does_not_claim_effects_are_already_stopped(monkeypatch):
    answers = []
    commands = []
    session_state = {}
    original_session = dict(bot.SESSION)

    class Callback:
        data = "prank:stop_all"
        from_user = SimpleNamespace(id=101)
        message = SimpleNamespace(
            text="Розыгрыши", chat=SimpleNamespace(id=10), message_id=20,
            edit_reply_markup=_async_noop,
        )

        async def answer(self, text, **kwargs):
            answers.append((text, kwargs))

    async def scenario():
        bot.SESSION["target"] = "device-1"
        bot.SESSION["mouse_swapped_device-1"] = True
        bot.SESSION["desktop_hidden_device-1"] = True
        async def simple_command(callback, action, emoji, label, timeout=12.0, **kwargs):
            commands.append((callback, action, emoji, label, timeout, kwargs))

        monkeypatch.setattr(bot, "simple_command", simple_command)
        await bot.on_prank_stop_all(Callback())
        session_state["mouse_swapped"] = bot.SESSION["mouse_swapped_device-1"]
        session_state["desktop_hidden"] = bot.SESSION["desktop_hidden_device-1"]

    try:
        asyncio.run(scenario())
    finally:
        bot.SESSION.clear()
        bot.SESSION.update(original_session)

    assert len(commands) == 1
    assert commands[0][1:] == (
        "prank_stop_all", "🛑", "Остановить активные приколы", 12.0, {},
    )
    assert bot._simple_command_label("prank_stop_all", "fallback", {}) == bot._lex("prank_stop_all_button")
    assert session_state == {"mouse_swapped": True, "desktop_hidden": True}
    assert answers == []  # simple_command owns progress and final result on the same card.


async def _async_noop(*args, **kwargs):
    return None


def _jpeg_test_image() -> bytes:
    import io
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (2, 2), color="white").save(buffer, format="JPEG")
    return buffer.getvalue()


def _jpeg_test_image_b64() -> str:
    return base64.b64encode(_jpeg_test_image()).decode("ascii")


@pytest.mark.parametrize("reply", [{"output": "done", "returncode": 0, "status": "completed"}, None])
def test_shell_ambiguous_publish_waits_same_request_without_resubmission(monkeypatch, tmp_path, reply):
    Callback, request, published, rendered = _shell_fixture(monkeypatch, tmp_path)
    waits, audits = [], []
    def publish(*args, **kwargs):
        published.append((args, kwargs))
        return None
    async def wait(*args):
        waits.append(args)
        return reply
    monkeypatch.setattr(bot.transport, "publish_command", publish)
    monkeypatch.setattr(bot.shell_collector, "wait_for", wait)
    monkeypatch.setattr(bot.access_store, "_write_audit_row", lambda row: audits.append(row["status"]) if row.get("kind") == "command_execution" else None)
    async def scenario():
        pending = await request()
        await bot.on_shell_approve(Callback(f"cmd:shell-approve:{pending.request_id}"))
        if reply is not None:
            preview = next(button.callback_data for row in rendered[-1][1].inline_keyboard for button in row
                           if str(button.callback_data).startswith("cmd:shell-preview:"))
            await bot.on_shell_result_preview(Callback(preview))
        return pending
    pending = asyncio.run(scenario())
    assert len(published) == 1
    assert waits == [("a", "shell", 20.0, pending.request_id)]
    assert audits == ["attempting", "publish_unknown", "completed" if reply else "result_timeout"]
    texts = "\n".join(item[0] for item in rendered)
    assert "Доставка команды пока не подтверждена" in texts
    if reply is None:
        assert "Команда могла выполниться" in texts
    else:
        assert "done" in texts


@pytest.mark.parametrize("flow", ["callback_text", "media", "document", "user_text"])
def test_private_card_rechecks_delivery_grant_after_waiting_for_lock(monkeypatch, tmp_path, flow):
    telegram = _card_telegram(monkeypatch, tmp_path)
    allowed = True
    async def scenario():
        nonlocal allowed
        async with bot._card_update(10, 20):
            guard = lambda: allowed
            if flow == "callback_text":
                operation = bot._replace_callback_message(telegram.callback(), "PRIVATE", delivery_guard=guard)
            elif flow == "media":
                operation = bot._replace_callback_with_media(telegram.callback(), telegram.send_photo, photo=b"PRIVATE", delivery_guard=guard)
            elif flow == "document":
                operation = bot._replace_user_card_with_document(telegram.message(), b"PRIVATE", "PRIVATE", delivery_guard=guard)
            else:
                operation = bot._replace_user_card(telegram.message(), "PRIVATE", delivery_guard=guard)
            task = asyncio.create_task(operation)
            await asyncio.sleep(0)
            allowed = False
        await task
    asyncio.run(scenario())
    assert not telegram.sent
    assert "PRIVATE" not in str(telegram.cards)
    assert telegram.cards[30]["text"] == bot._lex("action_result_hidden_after_revoke")


@pytest.mark.parametrize("handler,collector,callback,result", [
    ("on_cmd_clipboard", "clipboard_collector", "cmd:clipboard", {"text": "PRIVATE"}),
    ("on_cmd_screenshot", "screenshot_collector", "cmd:screenshot", {"image": "cHJpdmF0ZQ=="}),
    ("on_camera_capture", "webcam_collector", "camera:capture", {"image": "cHJpdmF0ZQ=="}),
    ("simple", "fun_text_collector", "cmd:wifi", {"text": "PRIVATE"}),
    ("simple", "fun_text_collector", "cmd:geo", {"text": "PRIVATE"}),
])
def test_device_handler_guard_survives_collector_to_delivery_lock_race(monkeypatch, tmp_path, handler, collector, callback, result):
    telegram = _card_telegram(monkeypatch, tmp_path)
    if handler == "on_cmd_screenshot":
        result = {"image": _jpeg_test_image_b64()}
    allowed = True
    monkeypatch.setattr(bot, "SESSION", {"target": "device-1"})
    if handler == "on_camera_capture":
        monkeypatch.setattr(bot, "_CAMERA_CATALOGS", {
            (20, "device-1"): {
                "expires_at": time.time() + 60,
                "cameras": {"0123456789abcdef": "Test camera"},
                "selected": "0123456789abcdef",
            },
        })
    monkeypatch.setattr(bot, "_simple_command_still_allowed", lambda *args: allowed)
    monkeypatch.setattr(bot, "publish_tracked", lambda *args, **kwargs: (True, "request-1"))
    monkeypatch.setattr(bot, "target_label", lambda *args: "Device")
    monkeypatch.setattr(bot, "_decode_screenshot_image", lambda *args: _jpeg_test_image())
    async def scenario():
        nonlocal allowed
        held, release = asyncio.Event(), asyncio.Event()
        async def hold():
            async with bot._card_update(10, 20):
                held.set()
                await release.wait()
        holders = []
        async def wait(*args, **kwargs):
            holders.append(asyncio.create_task(hold()))
            await held.wait()
            return result
        from types import SimpleNamespace
        monkeypatch.setattr(bot, collector, SimpleNamespace(wait_for=wait))
        cq = telegram.callback(callback)
        operation = (bot.simple_command(cq, "net_wifi_passwords" if callback == "cmd:wifi" else "geo_location", "", "Result")
                     if handler == "simple" else getattr(bot, handler)(cq))
        task = asyncio.create_task(operation)
        await held.wait()
        await asyncio.sleep(0)
        allowed = False
        release.set()
        await task
        await asyncio.gather(*holders)
    asyncio.run(scenario())
    assert "PRIVATE" not in str(telegram.cards)
    assert not telegram.sent
    assert telegram.cards[30]["text"] == bot._lex("action_result_hidden_after_revoke")


def test_private_text_retry_rechecks_revoked_permission(monkeypatch, tmp_path):
    telegram = _card_telegram(monkeypatch, tmp_path)
    allowed = True
    original = telegram.edit_message_text
    async def edit(text, **kwargs):
        nonlocal allowed
        if text == "PRIVATE":
            allowed = False
            raise RuntimeError("transient Telegram failure")
        return await original(text, **kwargs)
    monkeypatch.setattr(telegram, "edit_message_text", edit)
    asyncio.run(bot._replace_callback_message(telegram.callback(), "PRIVATE", delivery_guard=lambda: allowed))
    assert not telegram.sent
    assert telegram.cards[30]["text"] == bot._lex("action_result_hidden_after_revoke")


@pytest.mark.parametrize("mode", ["list", "get", "find"])
def test_file_result_rechecks_fsm_grant_inside_delivery_lock(monkeypatch, tmp_path, mode):
    from types import SimpleNamespace
    telegram = _card_telegram(monkeypatch, tmp_path)
    allowed = True
    monkeypatch.setattr(bot, "get_user_role", lambda *args: bot.Role.USER)
    monkeypatch.setattr(bot, "_device_action_still_allowed", lambda *args: allowed)
    monkeypatch.setattr(bot, "publish_tracked", lambda *args, **kwargs: (True, "file-request"))
    monkeypatch.setattr(bot, "target_label", lambda *args: "Device")
    class State:
        async def get_data(self):
            return dict(path_mode=mode, target="device-1", authorization_target="device-1", authorization_callback=f"files:{mode}")
        async def clear(self):
            pass
    async def scenario():
        nonlocal allowed
        held, release = asyncio.Event(), asyncio.Event()
        async def hold():
            async with bot._card_update(10, 20):
                held.set()
                await release.wait()
        holders = []
        async def wait(*args, **kwargs):
            holders.append(asyncio.create_task(hold()))
            await held.wait()
            return dict(ok=True, text="PRIVATE", data=base64.b64encode(b"PRIVATE").decode(), filename="private.txt")
        monkeypatch.setattr(bot, "fun_text_collector", SimpleNamespace(wait_for=wait))
        monkeypatch.setattr(bot, "file_collector", SimpleNamespace(wait_for=wait))
        message = telegram.message()
        message.text = "Documents/private.txt"
        task = asyncio.create_task((bot.on_find_input if mode == "find" else bot.on_path_input)(message, State()))
        await held.wait()
        await asyncio.sleep(0)
        allowed = False
        release.set()
        await task
        await asyncio.gather(*holders)
    asyncio.run(scenario())
    assert "PRIVATE" not in str(telegram.cards)
    assert not telegram.sent
    assert telegram.cards[30]["text"] == bot._lex("action_result_hidden_after_revoke")


@pytest.mark.parametrize("actor", [100, 200])
def test_shell_input_routes_exact_preview_to_owner_without_publish(monkeypatch, tmp_path, actor):
    from types import SimpleNamespace
    Callback, rendered = _admin_fixture(monkeypatch, tmp_path)
    bot.access_store.set_role(100, 200, bot.Role.USER, 100)
    bot.access_store.toggle_device(100, 200, "a", 100)
    bot.access_store.toggle_callback(100, 200, "shell_request", 100)
    _setup(monkeypatch, {"a": {"name": "Laptop", "command_approval_protocol": 1}})
    monkeypatch.setattr(bot, "SESSION", {"target": "a"})
    notices = []
    async def notify(text, reply_markup=None, **kwargs):
        notices.append((text, reply_markup))
        return True
    monkeypatch.setattr(bot, "_notify_owner", notify)
    monkeypatch.setattr(bot, "publish_tracked", lambda *a, **kw: pytest.fail("raw command published"))
    class State:
        async def get_data(self):
            return {"command_target": "a"}
        async def clear(self):
            pass
    command = "  printf '<exact>'\nwhoami  "
    asyncio.run(bot.on_shell_input(SimpleNamespace(text=command, from_user=SimpleNamespace(id=actor)), State()))
    assert len(notices) == 1
    assert "  printf &#x27;&lt;exact&gt;&#x27;\nwhoami  " in notices[0][0]
    assert f"{actor}" in notices[0][0]
    actions = _callback_data(notices[0][1])
    assert any(value.startswith("cmd:shell-approve:") for value in actions)
    assert any(value.startswith("cmd:shell-cancel:") for value in actions)


def test_shell_running_card_offers_owner_stop_and_reports_only_correlated_confirmation(monkeypatch, tmp_path):
    Callback, rendered = _admin_fixture(monkeypatch, tmp_path)
    published = []
    monkeypatch.setattr(bot.transport, "publish_command", lambda *args, **kwargs: published.append((args, kwargs)) or True)
    monkeypatch.setattr(bot, "active_shell_requests", {"req-123": {
        "device_id": "a", "owner_id": 100, "requester_id": 200,
        "approver_id": 100, "command_hash": "a" * 64,
    }})
    async def confirmed(device_id, msg_type, timeout, command_id):
        assert (device_id, msg_type, timeout, command_id) == ("a", "shell_cancel", 8.0, published[0][1]["id"])
        return {"type": "shell_cancel", "device_id": "a", "id": command_id,
                "target_request_id": "req-123", "status": "cancelled"}
    monkeypatch.setattr(bot.shell_cancel_collector, "wait_for", confirmed)

    async def scenario():
        markup = bot._shell_running_kb("req-123")
        assert markup.inline_keyboard[0][0].callback_data == "cmd:shell-stop:req-123"
        await bot.on_shell_stop(Callback("cmd:shell-stop:req-123"))
    asyncio.run(scenario())

    assert published == [( ("a", "shell_cancel"), {
        "id": published[0][1]["id"], "target_request_id": "req-123",
    })]
    assert "подтвердил остановку" in rendered[-1][0]


def test_shell_stop_unknown_when_response_is_missing(monkeypatch, tmp_path):
    Callback, rendered = _admin_fixture(monkeypatch, tmp_path)
    monkeypatch.setattr(bot.transport, "publish_command", lambda *args, **kwargs: True)
    monkeypatch.setattr(bot, "active_shell_requests", {"req-123": {
        "device_id": "a", "owner_id": 100, "requester_id": 200,
        "approver_id": 100, "command_hash": "a" * 64,
    }})
    async def missing(*args):
        return None
    monkeypatch.setattr(bot.shell_cancel_collector, "wait_for", missing)

    asyncio.run(bot.on_shell_stop(Callback("cmd:shell-stop:req-123")))

    assert "остановка неизвестна" in rendered[-1][0].lower()


def test_shell_stop_callback_route_is_owner_gated_and_classified():
    callback = "cmd:shell-stop:req-123"
    routes = _callback_routes(callback)
    assert len(routes) == 1
    _assert_expected_callback_gate(callback, routes[0])
    metadata = _descriptive_callback_metadata(callback, routes[0])
    assert metadata["access_class"] == "protected OwnerFilter"
    assert metadata["risk"] == "owner-only cancellation of exact active approved process"


def test_shell_dry_run_replaces_preview_then_requires_fresh_owner_approval(monkeypatch, tmp_path):
    Callback, _request, published, rendered = _shell_fixture(monkeypatch, tmp_path)
    now = 1_800_100_000
    monkeypatch.setattr(bot.time, "time", lambda: now)
    original = bot.command_approval.create_pending(200, 100, "a", "git clean -fd", now)

    async def scenario():
        await bot.on_shell_dry_run(Callback(f"cmd:shell-dryrun:{original.request_id}"))
        current = bot.command_approval.list_pending(100, now=now)
        assert len(current) == 1
        replacement = current[0]
        assert replacement.request_id != original.request_id
        assert replacement.command_text == "git clean -n -fd"
        assert replacement.expires_at == original.expires_at
        callbacks = _callback_data(rendered[-1][1])
        assert f"cmd:shell-approve:{replacement.request_id}" in callbacks
        assert f"cmd:shell-cancel:{replacement.request_id}" in callbacks
        assert "ничего не запускалось" in rendered[-1][0].lower()
        await bot.on_shell_approve(Callback(f"cmd:shell-approve:{replacement.request_id}"))
        return replacement

    replacement = asyncio.run(scenario())
    assert bot.command_approval.get_pending(original.request_id) is None
    assert len(published) == 1
    assert published[0][0] == ("a", "shell")
    assert published[0][1]["command"] == replacement.command_text


def test_shell_approval_offers_dry_run_only_for_supported_mutating_commands(monkeypatch, tmp_path):
    from types import SimpleNamespace
    _Callback, _request, published, rendered = _shell_fixture(monkeypatch, tmp_path)

    class State:
        async def get_data(self):
            return {"command_target": "a"}
        async def clear(self):
            pass

    message = SimpleNamespace(text="git clean -fd", from_user=SimpleNamespace(id=200))
    asyncio.run(bot.on_shell_input(message, State()))
    pending = next(iter(bot.command_approval._PENDING_COMMANDS.values()))
    callbacks = _callback_data(rendered[-1][1])
    assert f"cmd:shell-approve:{pending.request_id}" in callbacks
    assert f"cmd:shell-dryrun:{pending.request_id}" in callbacks
    assert pending.command_text == "git clean -fd"
    assert not published


def test_shell_dry_run_callback_route_is_owner_gated_and_classified():
    callback = "cmd:shell-dryrun:req-123"
    routes = _callback_routes(callback)
    assert len(routes) == 1
    _assert_expected_callback_gate(callback, routes[0])
    metadata = _descriptive_callback_metadata(callback, routes[0])
    assert metadata["access_class"] == "protected OwnerFilter"
    assert "fresh one-time approval" in metadata["risk"]


def test_runbook_confirmation_creates_only_first_exact_owner_approval(monkeypatch, tmp_path):
    Callback, _request, published, rendered = _shell_fixture(monkeypatch, tmp_path)
    now = 1_800_200_000
    monkeypatch.setattr(bot.time, "time", lambda: now)

    class State:
        async def get_data(self):
            return {"command_target": "a"}
        async def clear(self):
            pass

    message = SimpleNamespace(text="printf first\nprintf second", from_user=SimpleNamespace(id=100))

    async def scenario():
        await bot.on_shell_runbook_input(message, State())
        draft = next(iter(bot.shell_runbook._PLANS.values()))
        confirm = next(button.callback_data for row in rendered[-1][1].inline_keyboard for button in row
                       if str(button.callback_data).startswith("cmd:shell-runbook-confirm:"))
        await bot.on_shell_runbook_confirm(Callback(confirm))
        pending = next(iter(bot.command_approval._PENDING_COMMANDS.values()))
        return draft, pending

    draft, pending = asyncio.run(scenario())
    assert pending.command_text == "printf first"
    assert bot.shell_runbook.find_by_request(pending.request_id, now=now).state == "awaiting_step"
    assert not published
    assert f"cmd:shell-approve:{pending.request_id}" in _callback_data(rendered[-1][1])


def test_shell_runbook_template_library_is_owner_only_and_visible_in_terminal_menu(monkeypatch, tmp_path):
    Callback, _request, _published, rendered = _shell_fixture(monkeypatch, tmp_path)
    monkeypatch.setattr(bot.DeviceStore, "FILE", tmp_path / "devices.json", raising=False)
    monkeypatch.setattr(bot.DeviceStore, "CATALOG_FILE", tmp_path / "catalog.json", raising=False)
    store = bot.DeviceStore()
    store.upsert("a", {"name": "Laptop", "command_approval_protocol": 1})
    saved = store.create_shell_runbook_template(
        100, 100, "Connectivity", ["printf first", "printf second"], now=1_800_200_100,
    )
    monkeypatch.setattr(bot, "devices", store)
    monkeypatch.setattr(bot, "SESSION", {"target": "a"})
    monkeypatch.setattr(bot, "get_user_role", lambda user_id: bot.Role.OWNER if user_id == 100 else bot.Role.GUEST)

    owner_callbacks = _callback_data(bot.terminal_menu(100))
    assert "cmd:shell_runbook_templates" in owner_callbacks
    assert "cmd:shell_runbook_templates" not in _callback_data(bot.terminal_menu(200))

    asyncio.run(bot.on_cmd_shell_runbook_templates(Callback("cmd:shell_runbook_templates")))
    assert any(f"cmd:shell-template-use:{saved['template_id']}" in _callback_data(markup)
               for _text, markup in rendered)


def test_shell_metadata_export_is_owner_only_registry_navigation(monkeypatch, tmp_path):
    _Callback, _request, _published, _rendered = _shell_fixture(monkeypatch, tmp_path)
    store = bot.DeviceStore()
    store.upsert("a", {"name": "Laptop", "command_approval_protocol": 1})
    monkeypatch.setattr(bot, "devices", store)
    monkeypatch.setattr(bot, "SESSION", {"target": "a"})
    monkeypatch.setattr(bot, "get_user_role", lambda user_id: bot.Role.OWNER if user_id == 100 else bot.Role.GUEST)

    owner_menu = bot.terminal_menu(100)
    guest_menu = bot.terminal_menu(200)

    assert "cmd:shell_metadata_export" in _callback_data(owner_menu)
    assert "cmd:shell_metadata_export" not in _callback_data(guest_menu)
    button = next(button for row in owner_menu.inline_keyboard for button in row
                  if button.callback_data == "cmd:shell_metadata_export")
    assert button.text == "📤 Экспорт shell-метаданных"
    assert bot.access_store.explain_callback_access(200, "cmd:shell_metadata_export", 100, "a") == {
        "allowed": False, "reason": "owner_only",
    }


def test_saving_runbook_template_does_not_confirm_or_dispatch_its_plan(monkeypatch, tmp_path):
    Callback, _request, published, _rendered = _shell_fixture(monkeypatch, tmp_path)
    now = 1_800_200_102
    monkeypatch.setattr(bot.time, "time", lambda: now)
    monkeypatch.setattr(bot.DeviceStore, "FILE", tmp_path / "devices.json", raising=False)
    monkeypatch.setattr(bot.DeviceStore, "CATALOG_FILE", tmp_path / "catalog.json", raising=False)
    store = bot.DeviceStore()
    store.upsert("a", {"name": "Laptop", "command_approval_protocol": 1})
    monkeypatch.setattr(bot, "devices", store)
    monkeypatch.setattr(bot, "SESSION", {"target": "a"})
    plan = bot.shell_runbook.create_plan(100, 100, "a", "printf first\nprintf second", now=now)

    class State:
        def __init__(self):
            self.data = {}
        async def clear(self):
            self.data = {}
        async def update_data(self, **kwargs):
            self.data.update(kwargs)
        async def set_state(self, value):
            self.state = value
        async def get_data(self):
            return dict(self.data)

    state = State()
    asyncio.run(bot.on_shell_runbook_template_save_start(Callback(f"cmd:shell-template-save:{plan.plan_id}"), state))
    asyncio.run(bot.on_shell_runbook_template_save_name(
        SimpleNamespace(text="Connectivity", from_user=SimpleNamespace(id=100)), state,
    ))

    templates = store.list_shell_runbook_templates(100, 100)
    assert len(templates) == 1
    assert templates[0]["commands"] == list(plan.steps)
    assert bot.shell_runbook.get_plan(plan.plan_id, owner_id=100, now=now).state == "draft"
    assert not published


def test_deleting_runbook_template_requires_owner_confirmation(monkeypatch, tmp_path):
    Callback, _request, published, rendered = _shell_fixture(monkeypatch, tmp_path)
    monkeypatch.setattr(bot.DeviceStore, "FILE", tmp_path / "devices.json", raising=False)
    monkeypatch.setattr(bot.DeviceStore, "CATALOG_FILE", tmp_path / "catalog.json", raising=False)
    store = bot.DeviceStore()
    store.upsert("a", {"name": "Laptop", "command_approval_protocol": 1})
    saved = store.create_shell_runbook_template(100, 100, "Connectivity", ["printf a", "printf b"], now=100)
    monkeypatch.setattr(bot, "devices", store)
    monkeypatch.setattr(bot, "SESSION", {"target": "a"})

    asyncio.run(bot.on_shell_runbook_template_delete_start(Callback(f"cmd:shell-tpl-del:{saved['template_id']}")))

    assert store.get_shell_runbook_template(100, 100, saved["template_id"]) == saved
    confirm = next(
        button.callback_data for row in rendered[-1][1].inline_keyboard for button in row
        if str(button.callback_data).startswith("cmd:shell-tpl-del-ok:")
    )
    asyncio.run(bot.on_shell_runbook_template_delete_confirm(Callback(confirm)))

    assert store.get_shell_runbook_template(100, 100, saved["template_id"]) is None
    assert not published


def test_using_saved_runbook_template_only_creates_draft_and_keeps_step_approval(monkeypatch, tmp_path):
    Callback, _request, published, rendered = _shell_fixture(monkeypatch, tmp_path)
    monkeypatch.setattr(bot.DeviceStore, "FILE", tmp_path / "devices.json", raising=False)
    monkeypatch.setattr(bot.DeviceStore, "CATALOG_FILE", tmp_path / "catalog.json", raising=False)
    store = bot.DeviceStore()
    store.upsert("a", {"name": "Laptop", "command_approval_protocol": 1})
    saved = store.create_shell_runbook_template(
        100, 100, "Connectivity", ["printf first", "printf second"], now=1_800_200_101,
    )
    monkeypatch.setattr(bot, "devices", store)
    monkeypatch.setattr(bot, "SESSION", {"target": "a"})

    asyncio.run(bot.on_shell_runbook_template_use(Callback(f"cmd:shell-template-use:{saved['template_id']}")))

    assert not published
    draft = next(iter(bot.shell_runbook._PLANS.values()))
    assert draft.steps == tuple(saved["commands"])
    assert draft.state == "draft"
    assert f"cmd:shell-runbook-confirm:{draft.plan_id}" in _callback_data(rendered[-1][1])


def test_shell_runbook_template_library_paginates_with_bounded_owner_controls():
    rows = [
        {"template_id": f"{index:032x}", "name": f"Runbook {index}", "commands": ["echo a", "echo b"],
         "updated_at": index}
        for index in range(1, 12)
    ]

    first = bot._shell_runbook_template_library_keyboard(rows[:10], offset=0, total=11)
    second = bot._shell_runbook_template_library_keyboard(rows[10:], offset=10, total=11)

    assert len(first.inline_keyboard) <= 24
    assert len(second.inline_keyboard) <= 6
    assert "cmd:shell-tpl-page:10" in _callback_data(first)
    assert "cmd:shell-tpl-page:0" in _callback_data(second)
    page_routes = _callback_routes("cmd:shell-tpl-page:10")
    assert page_routes
    _assert_expected_callback_gate("cmd:shell-tpl-page:10", page_routes[0])


def test_runbook_success_requires_fresh_approval_for_next_step_and_failure_stops(monkeypatch, tmp_path):
    Callback, _request, published, rendered = _shell_fixture(monkeypatch, tmp_path)
    now = 1_800_200_100
    monkeypatch.setattr(bot.time, "time", lambda: now)
    monkeypatch.setattr(bot.shell_collector, "wait_for", lambda *args: asyncio.sleep(0, result={
        "output": "ok", "status": "completed", "returncode": 0,
    }))

    class State:
        async def get_data(self):
            return {"command_target": "a"}
        async def clear(self):
            pass

    async def prepare():
        message = SimpleNamespace(text="printf first\nprintf second", from_user=SimpleNamespace(id=100))
        await bot.on_shell_runbook_input(message, State())
        plan = next(iter(bot.shell_runbook._PLANS.values()))
        confirm = next(button.callback_data for row in rendered[-1][1].inline_keyboard for button in row
                       if str(button.callback_data).startswith("cmd:shell-runbook-confirm:"))
        await bot.on_shell_runbook_confirm(Callback(confirm))
        first = next(iter(bot.command_approval._PENDING_COMMANDS.values()))
        await bot.on_shell_approve(Callback(f"cmd:shell-approve:{first.request_id}"))
        return plan

    plan = asyncio.run(prepare())
    pending = list(bot.command_approval._PENDING_COMMANDS.values())
    assert len(published) == 1
    assert len(pending) == 1
    assert pending[0].command_text == "printf second"
    assert f"cmd:shell-approve:{pending[0].request_id}" in _callback_data(rendered[-1][1])
    assert bot.shell_runbook.get_plan(plan.plan_id, owner_id=100, now=now).state == "awaiting_step"
    assert "Шаг 1: completed · 0 с · exit=0" in rendered[-1][0]
    assert "Шаг 2: ожидает подтверждения" in rendered[-1][0]


def test_runbook_nonzero_exit_stops_without_creating_next_pending_step(monkeypatch, tmp_path):
    Callback, _request, published, rendered = _shell_fixture(monkeypatch, tmp_path)
    now = 1_800_200_200
    monkeypatch.setattr(bot.time, "time", lambda: now)
    async def failed_result(*args):
        return {"output": "failed", "status": "completed", "returncode": 1}
    monkeypatch.setattr(bot.shell_collector, "wait_for", failed_result)

    class State:
        async def get_data(self):
            return {"command_target": "a"}
        async def clear(self):
            pass

    async def prepare():
        await bot.on_shell_runbook_input(
            SimpleNamespace(text="printf first\nprintf second", from_user=SimpleNamespace(id=100)), State(),
        )
        plan = next(iter(bot.shell_runbook._PLANS.values()))
        confirm = next(button.callback_data for row in rendered[-1][1].inline_keyboard for button in row
                       if str(button.callback_data).startswith("cmd:shell-runbook-confirm:"))
        await bot.on_shell_runbook_confirm(Callback(confirm))
        first = next(iter(bot.command_approval._PENDING_COMMANDS.values()))
        await bot.on_shell_approve(Callback(f"cmd:shell-approve:{first.request_id}"))
        return plan

    plan = asyncio.run(prepare())
    assert len(published) == 1
    assert bot.command_approval.list_pending(100, now=now) == ()
    assert bot.shell_runbook.get_plan(plan.plan_id, owner_id=100, now=now).state == "failed", rendered[-1][0]
    assert "Runbook остановлен" in rendered[-1][0]
    assert "Шаг 1: failed · 0 с · exit=1" in rendered[-1][0]
    assert "Шаг 2: не запускался" in rendered[-1][0]


def _shell_fixture(monkeypatch, tmp_path):
    from types import SimpleNamespace
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives import serialization
    Callback, rendered = _admin_fixture(monkeypatch, tmp_path)
    monkeypatch.setattr(bot.shell_runbook, "_PLANS", {})
    monkeypatch.setattr(bot.shell_runbook, "_REQUESTS", {})
    bot.access_store.set_role(100, 200, bot.Role.USER, 100)
    bot.access_store.toggle_device(100, 200, "a", 100)
    bot.access_store.toggle_callback(100, 200, "shell_request", 100)
    _setup(monkeypatch, {"a": {"name": "Laptop", "command_approval_protocol": 1}})
    monkeypatch.setattr(bot, "SESSION", {"target": "a"})
    monkeypatch.setattr(bot.command_approval, "_PENDING_COMMANDS", {})
    key = Ed25519PrivateKey.generate()
    keyfile = tmp_path / "signing.pem"
    keyfile.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    import hashlib
    import json
    public = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    key_id = hashlib.sha256(public).hexdigest()[:16]
    encoded_public = base64.urlsafe_b64encode(public).decode().rstrip("=")
    keyset = json.dumps({key_id: encoded_public}, sort_keys=True, separators=(",", ":")).encode()
    monkeypatch.setenv("XIDER_COMMAND_VERIFY_KEYSET_B64", base64.urlsafe_b64encode(keyset).decode().rstrip("="))
    monkeypatch.setattr(bot.command_signing, "_PRIVATE_KEY_PATH", keyfile)
    published = []
    monkeypatch.setattr(bot.transport, "publish_command", lambda *args, **kw: published.append((args, kw)) or True)
    async def result(*args):
        return {"output": "done"}
    monkeypatch.setattr(bot.shell_collector, "wait_for", result)
    monkeypatch.setattr(bot, "publish", lambda *args, **kw: pytest.fail("generic publish leaks command into logs/history"))
    async def notify(text, reply_markup=None, **kwargs):
        rendered.append((text, reply_markup))
        return True
    monkeypatch.setattr(bot, "_notify_owner", notify)
    class State:
        async def get_data(self):
            return {"command_target": "a"}
        async def clear(self):
            pass
    async def request(actor=200):
        await bot.on_shell_input(SimpleNamespace(text="  whoami  ", from_user=SimpleNamespace(id=actor)), State())
        return next(iter(bot.command_approval._PENDING_COMMANDS.values()), None)
    return Callback, request, published, rendered


@pytest.mark.parametrize("failure", ["guest", "blocked", "ungranted", "device", "old_protocol", "changed_target"])
def test_shell_request_denies_invalid_current_authority(monkeypatch, tmp_path, failure):
    Callback, request, published, rendered = _shell_fixture(monkeypatch, tmp_path)
    if failure == "guest":
        bot.access_store.set_role(100, 200, bot.Role.GUEST, 100)
    elif failure == "blocked":
        bot.access_store.set_blocked(100, 200, True, 100)
    elif failure == "ungranted":
        bot.access_store.toggle_callback(100, 200, "shell_request", 100)
    elif failure == "device":
        bot.access_store.toggle_device(100, 200, "a", 100)
    elif failure == "old_protocol":
        bot.devices.get("a").pop("command_approval_protocol")
    elif failure == "changed_target":
        bot.SESSION["target"] = "b"
    assert asyncio.run(request()) is None
    assert not published


@pytest.mark.parametrize("failure", ["actor", "owner", "guest", "blocked", "grant", "device", "expiry", "command", "hash", "callback", "key", "keyset"])
def test_shell_approval_invalid_paths_never_publish(monkeypatch, tmp_path, failure):
    Callback, request, published, rendered = _shell_fixture(monkeypatch, tmp_path)
    pending = asyncio.run(request())
    actor = 200 if failure == "actor" else 100
    if failure == "owner":
        monkeypatch.setattr(bot, "ADMIN_ID", 300)
    elif failure == "guest":
        bot.access_store.set_role(100, 200, bot.Role.GUEST, 100)
    elif failure == "blocked":
        bot.access_store.set_blocked(100, 200, True, 100)
    elif failure == "grant":
        bot.access_store.toggle_callback(100, 200, "shell_request", 100)
    elif failure == "device":
        bot.access_store.toggle_device(100, 200, "a", 100)
    elif failure == "expiry":
        monkeypatch.setattr(bot.time, "time", lambda: pending.expires_at)
    elif failure == "command":
        object.__setattr__(pending, "_command_text", "different")
    elif failure == "hash":
        object.__setattr__(pending, "command_hash", "0" * 64)
    elif failure == "key":
        bot.command_signing._PRIVATE_KEY_PATH.unlink()
    elif failure == "keyset":
        monkeypatch.delenv("XIDER_COMMAND_VERIFY_KEYSET_B64")
    callback = f"cmd:shell-approve:{pending.request_id}" + (":b" if failure == "callback" else "")
    asyncio.run(bot.on_shell_approve(Callback(callback, actor)))
    assert not published


def test_shell_owner_approval_is_signed_atomic_and_bound_to_preview(monkeypatch, tmp_path, caplog):
    Callback, request, published, rendered = _shell_fixture(monkeypatch, tmp_path)
    async def scenario():
        pending = await request()
        bot.SESSION["target"] = "other-owner-screen"
        callback = f"cmd:shell-approve:{pending.request_id}"
        await asyncio.gather(bot.on_shell_approve(Callback(callback)), bot.on_shell_approve(Callback(callback)))
        await bot.on_shell_approve(Callback(callback))
        return pending
    pending = asyncio.run(scenario())
    assert len(published) == 1
    args, payload = published[0]
    assert args == ("a", "shell")
    assert payload["command"] == "  whoami  "
    assert payload["approval"]["requester_id"] == 200
    assert payload["approval"]["approver_id"] == 100
    assert payload["approval"]["command_hash"] == pending.command_hash
    assert "signature" in payload["approval"]
    assert bot.command_signing.verify_approved_command(payload["approval"], command=payload["command"], expected_device_id="a", now=int(time.time())) is not None


def test_approved_shell_commands_for_one_device_wait_in_fifo_and_show_position(monkeypatch, tmp_path, caplog):
    Callback, request, published, rendered = _shell_fixture(monkeypatch, tmp_path)
    first_waiting_for_result = asyncio.Event()
    release_first_result = asyncio.Event()
    second_queued_notice = asyncio.Event()
    second_published = asyncio.Event()

    async def replace(cq, text, reply_markup=None, **kwargs):
        rendered.append((text, reply_markup))
        if "очередь" in text.casefold():
            second_queued_notice.set()

    monkeypatch.setattr(bot, "_replace_callback_message", replace)

    def publish(*args, **kwargs):
        published.append((args, kwargs))
        if kwargs.get("id") == second.request_id:
            second_published.set()
        return True

    async def wait_for(device_id, command_type, timeout, request_id):
        if request_id == first.request_id:
            first_waiting_for_result.set()
            await release_first_result.wait()
        return {"stdout": "done", "stderr": "", "status": "completed", "returncode": 0}

    monkeypatch.setattr(bot.transport, "publish_command", publish)
    monkeypatch.setattr(bot.shell_collector, "wait_for", wait_for)

    async def scenario():
        nonlocal first, second
        first = await request()
        second = bot.command_approval.create_pending(200, 100, "a", "echo second", int(time.time()))
        first_task = asyncio.create_task(bot.on_shell_approve(Callback(f"cmd:shell-approve:{first.request_id}")))
        await asyncio.wait_for(first_waiting_for_result.wait(), timeout=1)
        second_task = asyncio.create_task(bot.on_shell_approve(Callback(f"cmd:shell-approve:{second.request_id}")))
        await asyncio.wait_for(second_queued_notice.wait(), timeout=1)
        assert not second_published.is_set()
        assert "2" in rendered[-1][0]
        release_first_result.set()
        await asyncio.gather(first_task, second_task)

    first = second = None
    asyncio.run(scenario())
    assert [kwargs["id"] for _, kwargs in published] == [first.request_id, second.request_id]
    assert "whoami" not in caplog.text
    assert bot.command_approval.get_pending(first.request_id) is None


def test_shell_tasks_across_devices_wait_for_owner_capacity_in_same_card(monkeypatch, tmp_path):
    Callback, request, published, rendered = _shell_fixture(monkeypatch, tmp_path)
    bot.access_store.toggle_device(100, 200, "b", 100)
    _setup(monkeypatch, {
        "a": {"name": "Laptop A", "command_approval_protocol": 1},
        "b": {"name": "Laptop B", "command_approval_protocol": 1},
    })
    monkeypatch.setattr(bot.shell_command_queue, "MAX_CONCURRENT_OWNER_TASKS", 1)
    first_waiting_for_result = asyncio.Event()
    release_first = asyncio.Event()
    capacity_notice = asyncio.Event()
    second_published = asyncio.Event()

    async def replace(cq, text, reply_markup=None, **kwargs):
        rendered.append((text, reply_markup))
        if "Общий лимит параллельных задач" in text:
            capacity_notice.set()

    def publish(*args, **kwargs):
        published.append((args, kwargs))
        if kwargs.get("id") == second.request_id:
            second_published.set()
        return True

    async def wait_for(device_id, command_type, timeout, request_id):
        if request_id == first.request_id:
            first_waiting_for_result.set()
            await release_first.wait()
        return {"stdout": "done", "stderr": "", "status": "completed", "returncode": 0}

    monkeypatch.setattr(bot, "_replace_callback_message", replace)
    monkeypatch.setattr(bot.transport, "publish_command", publish)
    monkeypatch.setattr(bot.shell_collector, "wait_for", wait_for)

    async def scenario():
        nonlocal first, second
        first = await request()
        second = bot.command_approval.create_pending(200, 100, "b", "echo second", int(time.time()))
        first_task = asyncio.create_task(bot.on_shell_approve(Callback(f"cmd:shell-approve:{first.request_id}")))
        await asyncio.wait_for(first_waiting_for_result.wait(), timeout=1)
        second_task = asyncio.create_task(bot.on_shell_approve(Callback(f"cmd:shell-approve:{second.request_id}")))
        await asyncio.wait_for(capacity_notice.wait(), timeout=1)
        assert not second_published.is_set()
        assert "1" in rendered[-1][0]
        release_first.set()
        await asyncio.gather(first_task, second_task)

    first = second = None
    asyncio.run(scenario())
    assert [kwargs["id"] for _, kwargs in published] == [first.request_id, second.request_id]


def test_shell_cancel_requires_current_owner_and_prevents_later_approval(monkeypatch, tmp_path):
    Callback, request, published, rendered = _shell_fixture(monkeypatch, tmp_path)
    async def scenario():
        pending = await request()
        callback = f"cmd:shell-cancel:{pending.request_id}"
        await bot.on_shell_cancel(Callback(callback, 200))
        assert bot.command_approval.get_pending(pending.request_id) is pending
        await bot.on_shell_cancel(Callback(callback))
        await bot.on_shell_approve(Callback(f"cmd:shell-approve:{pending.request_id}"))
    asyncio.run(scenario())
    assert not published


def test_shell_old_owner_cannot_cancel(monkeypatch, tmp_path):
    Callback, request, published, rendered = _shell_fixture(monkeypatch, tmp_path)
    pending = asyncio.run(request())
    monkeypatch.setattr(bot, "ADMIN_ID", 300)
    asyncio.run(bot.on_shell_cancel(Callback(f"cmd:shell-cancel:{pending.request_id}", 100)))
    assert bot.command_approval.get_pending(pending.request_id) is pending
    assert not published


def test_shell_transport_refuses_unsigned_and_tampered_messages(monkeypatch, tmp_path):
    from transport import MQTTTransport
    Callback, request, published, rendered = _shell_fixture(monkeypatch, tmp_path)
    pending = asyncio.run(request())
    envelope = bot.command_signing.sign_approved_command(
        request_id=pending.request_id, requester_id=200, approver_id=100, device_id="a",
        command=pending.command_text, issued_at=pending.created_at, expires_at=pending.expires_at,
        nonce=pending.nonce, key_id=bot.command_signing.signing_key_id(),
    )
    transport = MQTTTransport(lambda *args: None)
    transport.connected.set()
    boundary = []
    from types import SimpleNamespace
    transport._client.publish = lambda *args, **kw: boundary.append((args, kw)) or SimpleNamespace(rc=0)
    assert not transport.publish_command("a", "shell", command=pending.command_text)
    assert not transport.publish_command("a", "shell", command="different", approval=envelope)
    assert not boundary
    assert transport.publish_command("a", "shell", command=pending.command_text, approval=envelope)
    assert len(boundary) == 1


def test_shell_request_grant_is_reachable_and_never_inferred_from_full_device(monkeypatch, tmp_path):
    Callback, request, published, rendered = _shell_fixture(monkeypatch, tmp_path)
    assert "cmd:shell" in _callback_data(bot.terminal_menu(200))
    assert "shell_request" in bot.USER_PERMISSION_CHOICES
    bot.access_store.toggle_callback(100, 200, "shell_request", 100)
    bot.access_store.toggle_callback(100, 200, "full_device", 100)
    assert not bot.access_store.can_use_callback(200, "cmd:shell", 100, "a")


def test_signed_status_preserves_protocol_and_legacy_report_revokes_it(monkeypatch, tmp_path):
    from device_store import DeviceStore
    monkeypatch.setattr(DeviceStore, "FILE", tmp_path / "devices.json")
    monkeypatch.setattr(bot, "devices", DeviceStore())
    monkeypatch.setattr(bot, "verify_message_detailed", lambda data, **_kwargs: (data, "ok"))
    monkeypatch.setattr(bot, "ENCRYPT_PAYLOAD", False)
    monkeypatch.setattr(bot.bot_settings, "is_blocked", lambda _id: False)
    topic = f"{bot.MQTT_PREFIX}/a/status"
    bot.on_mqtt_message(topic, {"type": "status", "command_approval_protocol": 1})
    assert bot._shell_request_allowed(bot.ADMIN_ID, "a")
    bot.on_mqtt_message(topic, {"type": "status"})
    assert not bot._shell_request_allowed(bot.ADMIN_ID, "a")


def test_stale_mqtt_rejection_logs_signed_timestamp_delta_without_payload(monkeypatch, caplog):
    import logging

    monkeypatch.setattr(bot.server_ops, "observe", lambda *_args: None)
    monkeypatch.setattr(bot, "verify_message_detailed", lambda _data, **_kwargs: (None, "stale_timestamp"))
    monkeypatch.setattr(bot.time, "time", lambda: 1000.0)
    caplog.set_level(logging.WARNING, logger="xgent.bot")

    bot.on_mqtt_message(
        f"{bot.MQTT_PREFIX}/device-1/status",
        {"sig": "signature-not-for-log", "payload": {"ts": 700, "credential": "never-log-this"}},
    )

    assert "timestamp_delta_s=300" in caplog.text
    assert "never-log-this" not in caplog.text
    assert "signature-not-for-log" not in caplog.text


def test_shell_approval_waits_for_matching_result_on_owner_card(monkeypatch, tmp_path):
    Callback, request, published, rendered = _shell_fixture(monkeypatch, tmp_path)
    async def result(device, action, timeout, request_id):
        assert (device, action, timeout) == ("a", "shell", 20.0)
        assert request_id == published[0][1]["id"]
        return {"output": "result <exact>"}
    monkeypatch.setattr(bot.shell_collector, "wait_for", result)
    async def scenario():
        pending = await request()
        await bot.on_shell_approve(Callback(f"cmd:shell-approve:{pending.request_id}"))
        preview = next(button.callback_data for row in rendered[-1][1].inline_keyboard for button in row
                       if str(button.callback_data).startswith("cmd:shell-preview:"))
        await bot.on_shell_result_preview(Callback(preview))
    asyncio.run(scenario())
    assert "result &lt;exact&gt;" in rendered[-1][0]


def test_shell_results_redact_both_streams_and_audit_only_metadata(monkeypatch, tmp_path):
    import json
    Callback, request, published, rendered = _shell_fixture(monkeypatch, tmp_path)
    secrets = ["synthetic-password", "synthetic-token", "private-fixture"]
    async def result(*args):
        return {"stdout": "good line\npassword=synthetic-password\n",
                "stderr": "token=synthetic-token\n-----BEGIN PRIVATE KEY-----\nprivate-fixture",
                "stdout_truncated": False, "stderr_truncated": True,
                "status": "failed", "returncode": 3}
    monkeypatch.setattr(bot.shell_collector, "wait_for", result)
    async def scenario():
        pending = await request()
        await bot.on_shell_approve(Callback(f"cmd:shell-approve:{pending.request_id}"))
        preview = next(button.callback_data for row in rendered[-1][1].inline_keyboard for button in row
                       if str(button.callback_data).startswith("cmd:shell-preview:"))
        await bot.on_shell_result_preview(Callback(preview))
        return pending
    pending = asyncio.run(scenario())
    card = rendered[-1][0]
    rows = bot.access_store.recent_audit()
    row = next(row for row in rows if row["kind"] == "command_execution")
    assert "good line" in card
    assert "STDERR" in card and "8192" in card
    assert all(secret not in card + json.dumps(rows) for secret in secrets)
    assert row["request_id"] == pending.request_id
    assert row["command_hash"] == pending.command_hash
    assert row["status"] == "failed" and row["exit_code"] == 3
    assert row["requester_id"] == 200 and row["approver_id"] == 100
    assert row["device_id"] == "a"
    assert "whoami" not in json.dumps(rows)


@pytest.mark.parametrize("raw,secret", [
    ('password=[REDACTED]synthetic-second', 'synthetic-second'),
    ('password=[REDACTED] first synthetic-second', 'synthetic-second'),
])
def test_shell_rendering_does_not_trust_raw_redaction_marker(monkeypatch, tmp_path, raw, secret):
    Callback, request, published, rendered = _shell_fixture(monkeypatch, tmp_path)
    async def result(*args):
        return {"stdout": raw, "stderr": raw, "status": "completed", "returncode": 0}
    monkeypatch.setattr(bot.shell_collector, "wait_for", result)
    async def scenario():
        pending = await request()
        await bot.on_shell_approve(Callback(f"cmd:shell-approve:{pending.request_id}"))
    asyncio.run(scenario())
    assert secret not in rendered[-1][0]


def test_shell_result_reports_only_redaction_counts_and_categories(monkeypatch, tmp_path):
    Callback, request, published, rendered = _shell_fixture(monkeypatch, tmp_path)
    raw_output = "password=private-shell-fixture\nBearer private-bearer-fixture"
    async def result(*args):
        return {"stdout": raw_output, "stderr": "", "status": "completed", "returncode": 0}
    monkeypatch.setattr(bot.shell_collector, "wait_for", result)
    async def scenario():
        pending = await request()
        await bot.on_shell_approve(Callback(f"cmd:shell-approve:{pending.request_id}"))
    asyncio.run(scenario())
    card = rendered[-1][0]
    assert "private-shell-fixture" not in card and "private-bearer-fixture" not in card
    assert "Redactor" in card and "2" in card
    assert "секретные поля" in card and "Bearer-токены" in card


def test_long_shell_result_pages_through_the_same_owner_card(monkeypatch, tmp_path):
    Callback, request, published, rendered = _shell_fixture(monkeypatch, tmp_path)
    raw_output = "A" * 2200 + "B" * 2200 + "C" * 500
    async def result(*args):
        return {"stdout": raw_output, "stderr": "", "status": "completed", "returncode": 0}
    monkeypatch.setattr(bot.shell_collector, "wait_for", result)
    async def scenario():
        pending = await request()
        await bot.on_shell_approve(Callback(f"cmd:shell-approve:{pending.request_id}"))
    asyncio.run(scenario())

    hidden_text, hidden_markup = rendered[-1]
    assert "A" * 200 not in hidden_text
    preview_callback = next(
        button.callback_data for row in hidden_markup.inline_keyboard for button in row
        if str(button.callback_data).startswith("cmd:shell-preview:")
    )
    asyncio.run(bot.on_shell_result_preview(Callback(preview_callback)))
    first_text, first_markup = rendered[-1]
    assert "A" * 200 in first_text and "B" * 200 not in first_text
    assert "Страница 1/3" in first_text
    next_callback = next(
        button.callback_data for row in first_markup.inline_keyboard for button in row
        if str(button.callback_data).startswith("cmd:shell-page:")
    )
    asyncio.run(bot.on_shell_result_page(Callback(next_callback)))
    middle_text, middle_markup = rendered[-1]
    assert "B" * 200 in middle_text and "A" * 200 not in middle_text
    assert "Страница 2/3" in middle_text
    next_callback = next(
        button.callback_data for row in middle_markup.inline_keyboard for button in row
        if str(button.callback_data).endswith(":2")
    )
    asyncio.run(bot.on_shell_result_page(Callback(next_callback)))
    last_text = rendered[-1][0]
    assert "C" * 200 in last_text and "B" * 200 not in last_text
    assert "Страница 3/3" in last_text


def test_shell_redacted_output_requires_owner_preview_tap(monkeypatch, tmp_path):
    Callback, request, published, rendered = _shell_fixture(monkeypatch, tmp_path)
    async def result(*args):
        return {"stdout": "status ready\npassword=preview-secret", "stderr": "", "status": "completed", "returncode": 0}
    monkeypatch.setattr(bot.shell_collector, "wait_for", result)

    async def scenario():
        pending = await request()
        await bot.on_shell_approve(Callback(f"cmd:shell-approve:{pending.request_id}"))
    asyncio.run(scenario())

    hidden_text, hidden_markup = rendered[-1]
    assert "Redactor" in hidden_text
    assert "status ready" not in hidden_text
    assert "preview-secret" not in hidden_text
    preview_callback = next(
        button.callback_data for row in hidden_markup.inline_keyboard for button in row
        if str(button.callback_data).startswith("cmd:shell-preview:")
    )
    asyncio.run(bot.on_shell_result_preview(Callback(preview_callback)))
    shown_text = rendered[-1][0]
    assert "status ready" in shown_text
    assert "password=[REDACTED]" in shown_text
    assert "preview-secret" not in shown_text


def test_shell_result_comparison_requires_two_manual_owner_selections(monkeypatch, tmp_path):
    Callback, rendered = _admin_fixture(monkeypatch, tmp_path)
    import shell_result_compare
    import shell_result_pager

    monkeypatch.setattr(bot.time, "time", lambda: 100)
    first = shell_result_pager.create(owner_id=100, device_label="Laptop",
                                      text="ready=true\npassword=first-secret", footer="done", now=100)
    second = shell_result_pager.create(owner_id=100, device_label="Laptop",
                                       text="ready=false\npassword=second-secret", footer="done", now=101)
    first_button = next(button.callback_data for row in bot._shell_result_preview_keyboard(first).inline_keyboard
                        for button in row if str(button.callback_data).startswith("cmd:shell-compare-select:"))
    second_button = next(button.callback_data for row in bot._shell_result_preview_keyboard(second).inline_keyboard
                         for button in row if str(button.callback_data).startswith("cmd:shell-compare-select:"))

    async def scenario():
        await bot.on_shell_result_compare_select(Callback(first_button))
        assert not rendered
        await bot.on_shell_result_compare_select(Callback(second_button))

    asyncio.run(scenario())
    assert "-ready=true" in rendered[-1][0] and "+ready=false" in rendered[-1][0]
    assert "first-secret" not in rendered[-1][0] and "second-secret" not in rendered[-1][0]
    assert shell_result_compare.select(second.token, owner_id=100, now=102).state == "waiting_for_second"


def test_shell_result_compare_callback_route_is_owner_gated():
    callback = "cmd:shell-compare-select:abcdefghijklmnop"
    routes = _callback_routes(callback)
    assert len(routes) == 1
    _assert_expected_callback_gate(callback, routes[0])
    metadata = _descriptive_callback_metadata(callback, routes[0])
    assert metadata["access_class"] == "protected OwnerFilter"


def test_shell_repeat_creates_a_fresh_approval_without_publishing(monkeypatch, tmp_path):
    Callback, request, published, rendered = _shell_fixture(monkeypatch, tmp_path)
    monkeypatch.setattr(bot.shell_repeat, "_CANDIDATES", {})

    async def result(*args):
        return {"output": "done", "returncode": 0, "status": "completed"}

    monkeypatch.setattr(bot.shell_collector, "wait_for", result)

    async def scenario():
        original = await request()
        await bot.on_shell_approve(Callback(f"cmd:shell-approve:{original.request_id}"))
        repeat_callback = next(
            button.callback_data for row in rendered[-1][1].inline_keyboard for button in row
            if str(button.callback_data).startswith("cmd:shell-repeat:")
        )
        await bot.on_shell_result_repeat(Callback(repeat_callback))
        return original.request_id

    original_request_id = asyncio.run(scenario())
    pending = next(iter(bot.command_approval._PENDING_COMMANDS.values()))
    assert pending.command_text == "  whoami  "
    assert pending.request_id != original_request_id
    assert len(published) == 1
    assert "Повтор · новый запрос" in rendered[-1][0]
    assert "не запускалась" in rendered[-1][0]
    callbacks = _callback_data(rendered[-1][1])
    assert f"cmd:shell-approve:{pending.request_id}" in callbacks
    assert f"cmd:shell-cancel:{pending.request_id}" in callbacks


def test_shell_repeat_callback_route_is_owner_gated():
    callback = "cmd:shell-repeat:abcdefghijklmnop"
    routes = _callback_routes(callback)
    assert len(routes) == 1
    _assert_expected_callback_gate(callback, routes[0])
    metadata = _descriptive_callback_metadata(callback, routes[0])
    assert metadata["access_class"] == "protected OwnerFilter"


def test_shell_repeat_is_not_offered_when_prior_execution_outcome_is_unknown(monkeypatch, tmp_path):
    Callback, request, published, rendered = _shell_fixture(monkeypatch, tmp_path)
    monkeypatch.setattr(bot.shell_repeat, "_CANDIDATES", {})

    async def result(*args):
        return {"output": "partial", "status": "timeout", "returncode": None}

    monkeypatch.setattr(bot.shell_collector, "wait_for", result)

    async def scenario():
        pending = await request()
        await bot.on_shell_approve(Callback(f"cmd:shell-approve:{pending.request_id}"))

    asyncio.run(scenario())
    callbacks = _callback_data(rendered[-1][1])
    assert not any(value.startswith("cmd:shell-repeat:") for value in callbacks)
    assert not bot.shell_repeat._CANDIDATES


def test_valid_json_shell_result_can_switch_between_fields_and_redacted_source(monkeypatch, tmp_path):
    Callback, request, published, rendered = _shell_fixture(monkeypatch, tmp_path)
    raw_output = '{"service":{"name":"worker","ready":true},"count":3}'

    async def result(*args):
        return {"stdout": raw_output, "stderr": "", "status": "completed", "returncode": 0}

    monkeypatch.setattr(bot.shell_collector, "wait_for", result)

    async def scenario():
        pending = await request()
        await bot.on_shell_approve(Callback(f"cmd:shell-approve:{pending.request_id}"))

    asyncio.run(scenario())
    _, preview_markup = rendered[-1]
    preview_callback = next(
        button.callback_data for row in preview_markup.inline_keyboard for button in row
        if str(button.callback_data).startswith("cmd:shell-preview:")
    )
    asyncio.run(bot.on_shell_result_preview(Callback(preview_callback)))

    _, source_markup = rendered[-1]
    fields_callback = next(
        button.callback_data for row in source_markup.inline_keyboard for button in row
        if str(button.callback_data).startswith("cmd:shell-json:")
    )
    asyncio.run(bot.on_shell_result_json(Callback(fields_callback)))
    fields_text, fields_markup = rendered[-1]
    assert "service.name" in fields_text and "worker" in fields_text
    assert "service.ready" in fields_text and "true" in fields_text
    source_callback = next(
        button.callback_data for row in fields_markup.inline_keyboard for button in row
        if str(button.callback_data).startswith("cmd:shell-source:")
    )
    asyncio.run(bot.on_shell_result_source(Callback(source_callback)))
    source_text = rendered[-1][0]
    assert '&quot;service&quot;' in source_text and '&quot;count&quot;:3' in source_text


def test_secret_redaction_happens_before_json_view_detection(monkeypatch, tmp_path):
    Callback, request, published, rendered = _shell_fixture(monkeypatch, tmp_path)
    secret = "json-secret-fixture"

    async def result(*args):
        return {"stdout": '{"password":"' + secret + '","ready":true}',
                "stderr": "", "status": "completed", "returncode": 0}

    monkeypatch.setattr(bot.shell_collector, "wait_for", result)

    async def scenario():
        pending = await request()
        await bot.on_shell_approve(Callback(f"cmd:shell-approve:{pending.request_id}"))

    asyncio.run(scenario())
    _, preview_markup = rendered[-1]
    preview_callback = next(
        button.callback_data for row in preview_markup.inline_keyboard for button in row
        if str(button.callback_data).startswith("cmd:shell-preview:")
    )
    asyncio.run(bot.on_shell_result_preview(Callback(preview_callback)))
    source_text, markup = rendered[-1]
    assert secret not in source_text
    assert not any(str(button.callback_data).startswith("cmd:shell-json:")
                   for row in markup.inline_keyboard for button in row)


def test_shell_capture_boundary_token_fragment_never_reaches_telegram(monkeypatch, tmp_path):
    import importlib.util
    import io
    root = Path(__file__).resolve().parents[2]
    spec = importlib.util.spec_from_file_location("boundary_shell_fixture", root / "XGENT-MCS" / "shell_execution.py")
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    raw = ('password="' + 'a' * 8165 + '"\n123456:abcdefghijklmnopqrstuvwxyz').encode()
    safe, truncated = helper._read_stream(io.BytesIO(raw))
    Callback, request, published, rendered = _shell_fixture(monkeypatch, tmp_path)
    async def result(*args):
        return {"stdout": safe, "stdout_truncated": truncated,
                "status": "completed", "returncode": 0}
    monkeypatch.setattr(bot.shell_collector, "wait_for", result)
    async def scenario():
        pending = await request()
        await bot.on_shell_approve(Callback(f"cmd:shell-approve:{pending.request_id}"))
    asyncio.run(scenario())
    assert "123456:" not in rendered[-1][0]


@pytest.mark.parametrize("captured", [False, True], ids=["bot-redaction", "agent-capture"])
@pytest.mark.parametrize("hyphens", ["-", "---"])
def test_shell_hyphen_token_beyond_output_cap_never_reaches_telegram(monkeypatch, tmp_path, captured, hyphens):
    import importlib.util
    import io
    suffix = "abcdefghijklmnopqrs" + hyphens
    raw = 'password="' + 'a' * 8240 + '"\n123456:' + suffix
    safe, truncated = raw, True
    if captured:
        root = Path(__file__).resolve().parents[2]
        spec = importlib.util.spec_from_file_location("hyphen_shell_fixture", root / "XGENT-MCS" / "shell_execution.py")
        helper = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(helper)
        safe, truncated = helper._read_stream(io.BytesIO(raw.encode()))
    Callback, request, published, rendered = _shell_fixture(monkeypatch, tmp_path)
    async def result(*args):
        return {"stdout": safe, "stderr": safe, "output": safe,
                "stdout_truncated": truncated, "stderr_truncated": truncated,
                "status": "completed", "returncode": 0}
    monkeypatch.setattr(bot.shell_collector, "wait_for", result)
    async def scenario():
        pending = await request()
        await bot.on_shell_approve(Callback(f"cmd:shell-approve:{pending.request_id}"))
    asyncio.run(scenario())
    assert "123456" not in rendered[-1][0]
    assert suffix not in rendered[-1][0]


@pytest.mark.parametrize("outcome", ["timeout", "publish_failed", "signing_unavailable"])
def test_shell_failure_outcomes_keep_metadata_audit(monkeypatch, tmp_path, outcome):
    Callback, request, published, rendered = _shell_fixture(monkeypatch, tmp_path)
    if outcome == "publish_failed":
        monkeypatch.setattr(bot.transport, "publish_command", lambda *a, **kw: False)
    elif outcome == "signing_unavailable":
        monkeypatch.setattr(bot.command_signing, "signing_key_id", lambda: (_ for _ in ()).throw(
            bot.command_signing.CommandSigningUnavailable("password=hidden-fixture")))
    async def no_result(*args):
        return None
    monkeypatch.setattr(bot.shell_collector, "wait_for", no_result)
    async def scenario():
        pending = await request()
        await bot.on_shell_approve(Callback(f"cmd:shell-approve:{pending.request_id}"))
        return pending
    pending = asyncio.run(scenario())
    rows = bot.access_store.recent_audit()
    row = next(row for row in rows if row["kind"] == "command_execution")
    assert row["status"] == ("result_timeout" if outcome == "timeout" else outcome)
    assert row["request_id"] == pending.request_id
    assert row["exit_code"] is None


def test_shell_sent_audit_survives_interrupted_telegram_card(monkeypatch, tmp_path):
    Callback, request, published, rendered = _shell_fixture(monkeypatch, tmp_path)
    async def interrupted(*args, **kwargs):
        raise asyncio.CancelledError()
    async def scenario():
        pending = await request()
        monkeypatch.setattr(bot, "_replace_callback_message", interrupted)
        with pytest.raises(asyncio.CancelledError):
            await bot.on_shell_approve(Callback(f"cmd:shell-approve:{pending.request_id}"))
        return pending
    pending = asyncio.run(scenario())
    assert len(published) == 1
    row = next(row for row in bot.access_store.recent_audit() if row["kind"] == "command_execution")
    assert row["status"] == "sent" and row["request_id"] == pending.request_id


def test_shell_malformed_result_status_does_not_enter_audit_or_response(monkeypatch, tmp_path):
    Callback, request, published, rendered = _shell_fixture(monkeypatch, tmp_path)
    async def result(*args):
        return {"output": "done", "returncode": 0, "status": ["password=malformed-fixture"]}
    monkeypatch.setattr(bot.shell_collector, "wait_for", result)
    async def scenario():
        pending = await request()
        await bot.on_shell_approve(Callback(f"cmd:shell-approve:{pending.request_id}"))
    asyncio.run(scenario())
    assert "malformed-fixture" not in rendered[-1][0]
    assert bot.access_store.recent_audit()[0]["status"] == "completed"


def test_shell_audit_attempt_precedes_publication(monkeypatch, tmp_path):
    Callback, request, published, rendered = _shell_fixture(monkeypatch, tmp_path)
    def publish(*args, **kwargs):
        rows = bot.access_store.recent_audit()
        row = next(row for row in rows if row["kind"] == "command_execution")
        assert row["status"] == "attempting"
        assert row["request_id"] == kwargs["id"]
        published.append((args, kwargs))
        return True
    monkeypatch.setattr(bot.transport, "publish_command", publish)
    async def scenario():
        pending = await request()
        await bot.on_shell_approve(Callback(f"cmd:shell-approve:{pending.request_id}"))
    asyncio.run(scenario())
    assert len(published) == 1


def test_shell_audit_write_failure_prevents_publication(monkeypatch, tmp_path):
    Callback, request, published, rendered = _shell_fixture(monkeypatch, tmp_path)
    async def scenario():
        pending = await request()
        def unavailable(row):
            raise OSError("synthetic unavailable audit storage")
        monkeypatch.setattr(bot.access_store, "_write_audit_row", unavailable)
        await bot.on_shell_approve(Callback(f"cmd:shell-approve:{pending.request_id}"))
    asyncio.run(scenario())
    assert not published


def test_shell_failed_owner_preview_discards_pending_request(monkeypatch, tmp_path):
    Callback, request, published, rendered = _shell_fixture(monkeypatch, tmp_path)
    async def undelivered(*args, **kwargs):
        return False
    monkeypatch.setattr(bot, "_notify_owner", undelivered)
    assert asyncio.run(request()) is None
    assert not published


def test_shell_strict_owner_preview_reports_failed_telegram_delivery(monkeypatch):
    async def failed(*args, **kwargs):
        raise RuntimeError("boundary failed")
    monkeypatch.setattr(bot.bot, "send_message", failed)
    assert asyncio.run(bot._notify_owner("exact preview", strict_delivery=True)) is False


def test_transport_rejects_shell_type_override_before_mqtt(monkeypatch):
    from transport import MQTTTransport
    from types import SimpleNamespace
    transport = MQTTTransport(lambda *args: None)
    transport.connected.set()
    boundary = []
    monkeypatch.setattr(transport._client, "publish", lambda *args, **kwargs: boundary.append((args, kwargs)) or SimpleNamespace(rc=0))
    assert transport.publish_command("a", "status_request", type="shell", command="whoami") is False
    assert boundary == []


@pytest.mark.parametrize("boundary", ["requester", "owner"])
@pytest.mark.parametrize("failure", ["cancel", "exception"])
def test_shell_interrupted_preview_discards_request_and_cannot_approve(monkeypatch, tmp_path, boundary, failure):
    Callback, request, published, rendered = _shell_fixture(monkeypatch, tmp_path)
    async def scenario():
        reached = asyncio.Event()
        captured = []
        async def interrupted(*args, **kwargs):
            captured.append(next(iter(bot.command_approval._PENDING_COMMANDS.values())))
            reached.set()
            if failure == "exception":
                raise RuntimeError("preview boundary failure")
            await asyncio.Event().wait()
        monkeypatch.setattr(bot, "_replace_user_card" if boundary == "requester" else "_notify_owner", interrupted)
        task = asyncio.create_task(request())
        await reached.wait()
        pending = captured[0]
        if failure == "cancel":
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            with pytest.raises(RuntimeError, match="preview boundary failure"):
                await task
        assert bot.command_approval.get_pending(pending.request_id) is None
        await bot.on_shell_approve(Callback(f"cmd:shell-approve:{pending.request_id}"))
        assert not published
    asyncio.run(scenario())


@pytest.fixture(autouse=True)
def isolated_card_index(monkeypatch, tmp_path):
    monkeypatch.setattr(bot.ui_cards, "FILE", tmp_path / "ui-cards.json")


def _admin_fixture(monkeypatch, tmp_path):
    from types import SimpleNamespace
    monkeypatch.setattr(bot, "ADMIN_ID", 100)
    monkeypatch.setattr(bot.access_store, "FILE", tmp_path / "access.json")
    monkeypatch.setattr(bot.access_store, "AUDIT_FILE", tmp_path / "audit.jsonl")
    bot.access_store.register_start(SimpleNamespace(id=200, username="u", first_name="User", last_name=""))
    _setup(monkeypatch, {"a": {"name": "Laptop"}})
    rendered = []
    async def render(_obj, text, reply_markup=None, **kwargs):
        rendered.append((text, reply_markup))
    monkeypatch.setattr(bot, "_replace_callback_message", render)
    monkeypatch.setattr(bot, "_replace_user_card", render)
    class Callback:
        def __init__(self, data, actor=100):
            self.data = data
            self.from_user = SimpleNamespace(id=actor)
        async def answer(self, *args, **kwargs):
            pass
    return Callback, rendered


def test_admin_grant_menus_show_expired_permissions_as_disabled(monkeypatch, tmp_path):
    _Callback, rendered = _admin_fixture(monkeypatch, tmp_path)
    assert bot.access_store.set_role(100, 200, bot.Role.USER, 100)
    monkeypatch.setattr(bot.time, "time", lambda: 100)
    assert bot.access_store.toggle_device(100, 200, "a", 100, expires_at=200)
    assert bot.access_store.toggle_callback(100, 200, "cmd:status", 100, expires_at=200)
    monkeypatch.setattr(bot.time, "time", lambda: 201)

    device_button = next(
        button
        for row in bot.admin_devices_menu(200).inline_keyboard
        for button in row
        if button.callback_data == "admin:device:200:a"
    )
    permission_button = next(
        button
        for row in bot.admin_permissions_menu(200).inline_keyboard
        for button in row
        if button.callback_data == "admin:perm:200:cmd_status"
    )

    assert device_button.style == "primary"
    assert permission_button.style == "primary"


def test_admin_grant_menus_are_registry_driven_and_owner_scoped(monkeypatch, tmp_path):
    _admin_fixture(monkeypatch, tmp_path)
    registry = bot.feature_catalog.load_registry()
    pages = registry["navigation"]
    assert any(page["page_id"] == "admin.devices.grant.row" for page in pages)
    assert any(page["page_id"] == "admin.permissions.grant.row" for page in pages)

    owner_device_menu = bot.admin_devices_menu(200, actor_id=100)
    non_owner_device_menu = bot.admin_devices_menu(200, actor_id=200)
    owner_permission_menu = bot.admin_permissions_menu(200, actor_id=100)
    non_owner_permission_menu = bot.admin_permissions_menu(200, actor_id=200)

    assert "admin:device:200:a" in _callback_data(owner_device_menu)
    assert "admin:perm:200:cmd_status" in _callback_data(owner_permission_menu)
    assert not _callback_data(non_owner_device_menu)
    assert not _callback_data(non_owner_permission_menu)

    without_device_row = dict(registry)
    without_device_row["navigation"] = [
        page for page in pages if page["page_id"] != "admin.devices.grant.row"
    ]
    without_permission_row = dict(registry)
    without_permission_row["navigation"] = [
        page for page in pages if page["page_id"] != "admin.permissions.grant.row"
    ]
    monkeypatch.setattr(bot.feature_catalog, "load_registry", lambda: without_device_row)
    assert "admin:device:200:a" not in _callback_data(bot.admin_devices_menu(200, actor_id=100))
    monkeypatch.setattr(bot.feature_catalog, "load_registry", lambda: without_permission_row)
    assert "admin:perm:200:cmd_status" not in _callback_data(bot.admin_permissions_menu(200, actor_id=100))


def test_admin_grant_menus_offer_owner_only_temporary_grant_entry(monkeypatch, tmp_path):
    _admin_fixture(monkeypatch, tmp_path)
    owner_devices = _callback_data(bot.admin_devices_menu(200, actor_id=100))
    owner_permissions = _callback_data(bot.admin_permissions_menu(200, actor_id=100))
    other_devices = _callback_data(bot.admin_devices_menu(200, actor_id=200))
    other_permissions = _callback_data(bot.admin_permissions_menu(200, actor_id=200))

    assert "admin:devicetemp:200:a" in owner_devices
    assert "admin:permtemp:200:cmd_status" in owner_permissions
    assert "admin:devicetemp:200:a" not in other_devices
    assert "admin:permtemp:200:cmd_status" not in other_permissions


def test_callback_access_denial_shows_the_rbac_reason_without_running_action(monkeypatch, tmp_path):
    _admin_fixture(monkeypatch, tmp_path)
    responses = []

    class DeniedCallback:
        data = "cmd:screenshot"
        from_user = SimpleNamespace(id=200)

        async def answer(self, text=None, **kwargs):
            responses.append((text, kwargs))

    monkeypatch.setattr(roles, "CallbackQuery", DeniedCallback)
    denied = asyncio.run(roles.AdminFilter()(DeniedCallback()))
    assert denied is False
    assert responses == [("Для этой функции нужна роль «пользователь» или grant владельца.", {"show_alert": True})]


def test_admin_filter_records_only_the_grant_used_by_an_allowed_callback(monkeypatch, tmp_path):
    Callback, _rendered = _admin_fixture(monkeypatch, tmp_path)
    assert bot.access_store.set_role(100, 200, bot.Role.USER, 100)
    monkeypatch.setattr(bot.time, "time", lambda: 800)
    assert bot.access_store.toggle_callback(100, 200, "cmd:screenshot", 100)
    monkeypatch.setattr(bot.time, "time", lambda: 1_000)
    assert bot.access_store.toggle_device(100, 200, "a", 100)
    assert bot.access_store.toggle_callback(100, 200, "cmd:status", 100)
    monkeypatch.setattr(roles, "ADMIN_ID", 100)
    bot.SESSION["target"] = "a"
    monkeypatch.setattr(roles, "CallbackQuery", Callback)

    assert asyncio.run(roles.AdminFilter()(Callback("cmd:status"))) is True
    rows = bot.access_store.review_grants(100, 100, unused_after_s=100, now=1_050)
    states = {(row["kind"], row["item"]): row["state"] for row in rows}
    assert states[("device", "a")] == "active"
    assert states[("permission", "cmd:status")] == "active"
    assert states[("permission", "cmd:screenshot")] == "unused"


def test_admin_user_list_surfaces_expired_grants_for_owner_review(monkeypatch, tmp_path):
    Callback, rendered = _admin_fixture(monkeypatch, tmp_path)
    assert bot.access_store.set_role(100, 200, bot.Role.USER, 100)
    monkeypatch.setattr(bot.time, "time", lambda: 1_000)
    assert bot.access_store.toggle_callback(100, 200, "cmd:status", 100, expires_at=1_050)
    monkeypatch.setattr(bot.time, "time", lambda: 1_051)

    asyncio.run(bot.on_admin_users(Callback("admin:users")))

    assert "Права на пересмотр" in rendered[-1][0]
    assert "истёк срок" in rendered[-1][0]
    assert "cmd:status" in rendered[-1][0]


@pytest.mark.parametrize(("handler", "data", "expected"), [
    ("on_admin_device_temporary_grant", "admin:devicetemp:200:a", {"callbacks": [], "devices": ["a"]}),
    ("on_admin_permission_temporary_grant", "admin:permtemp:200:cmd_status", {"callbacks": ["cmd:status"], "devices": []}),
])
def test_temporary_admin_grant_requires_owner_confirmation_and_expires(
    monkeypatch, tmp_path, handler, data, expected,
):
    Callback, rendered = _admin_fixture(monkeypatch, tmp_path)
    assert bot.access_store.set_role(100, 200, bot.Role.USER, 100)
    monkeypatch.setattr(bot.time, "time", lambda: 1_000)

    async def scenario():
        await getattr(bot, handler)(Callback(data, actor=200))
        assert bot.access_store.get_effective_grants(200) == {"callbacks": [], "devices": []}
        await getattr(bot, handler)(Callback(data, actor=100))
        confirmation = rendered[-1][1].inline_keyboard[0][0].callback_data
        assert confirmation.startswith("admin:confirm:")
        await bot.on_admin_confirm(Callback(confirmation, actor=100))

    asyncio.run(scenario())
    assert bot.access_store.get_effective_grants(200) == expected
    assert set(bot.access_store.get_user(200)["permission_expirations"].values()) == {87_400}
    monkeypatch.setattr(bot.time, "time", lambda: 87_400)
    assert bot.access_store.get_effective_grants(200) == {"callbacks": [], "devices": []}


def test_admin_text_editor_navigation_is_registry_driven_and_owner_scoped(monkeypatch, tmp_path):
    _admin_fixture(monkeypatch, tmp_path)
    registry = bot.feature_catalog.load_registry()
    pages = registry["navigation"]
    assert any(page["page_id"] == "admin.text.preview" for page in pages)

    owner_menu = bot.admin_texts_menu(actor_id=100)
    non_owner_menu = bot.admin_texts_menu(actor_id=200)
    assert "admin:style" in _callback_data(owner_menu)
    assert "menu:admin" in _callback_data(owner_menu)
    assert any(value.startswith("admin:text:") for value in _callback_data(owner_menu))
    assert not _callback_data(non_owner_menu)

    without_preview = dict(registry)
    without_preview["navigation"] = [page for page in pages if page["page_id"] != "admin.text.preview"]
    monkeypatch.setattr(bot.feature_catalog, "load_registry", lambda: without_preview)
    assert not any(value.startswith("admin:text:") for value in _callback_data(bot.admin_texts_menu(actor_id=100)))


def test_admin_style_picker_uses_registry_and_keeps_xlex_style_choices(monkeypatch, tmp_path):
    _admin_fixture(monkeypatch, tmp_path)
    registry = bot.feature_catalog.load_registry()
    pages = registry["navigation"]
    assert any(page["page_id"] == "admin.style.choice.xtech" for page in pages)

    owner_menu = bot.admin_style_menu(actor_id=100)
    non_owner_menu = bot.admin_style_menu(actor_id=200)
    expected = {f"admin:style:set:{style}" for style in bot.xlex.STYLES}
    actual = {value for value in _callback_data(owner_menu) if value.startswith("admin:style:set:")}
    assert actual == expected
    assert "menu:admin" in _callback_data(owner_menu)
    assert not _callback_data(non_owner_menu)

    without_xtech = dict(registry)
    without_xtech["navigation"] = [page for page in pages if page["page_id"] != "admin.style.choice.xtech"]
    monkeypatch.setattr(bot.feature_catalog, "load_registry", lambda: without_xtech)
    assert "admin:style:set:xtech" not in _callback_data(bot.admin_style_menu(actor_id=100))


def test_admin_user_list_hides_expired_permissions(monkeypatch, tmp_path):
    Callback, rendered = _admin_fixture(monkeypatch, tmp_path)
    assert bot.access_store.set_role(100, 200, bot.Role.USER, 100)
    monkeypatch.setattr(bot.time, "time", lambda: 100)
    assert bot.access_store.toggle_device(100, 200, "a", 100, expires_at=200)
    assert bot.access_store.toggle_callback(100, 200, "cmd_status", 100, expires_at=200)
    monkeypatch.setattr(bot.time, "time", lambda: 201)

    asyncio.run(bot.on_admin_users(Callback("admin:users")))

    assert "Устройства: a" not in rendered[-1][0]
    assert "Действия: cmd_status" not in rendered[-1][0]


def test_admin_confirmation_preview_uses_effective_grants_after_expiry(monkeypatch, tmp_path):
    Callback, rendered = _admin_fixture(monkeypatch, tmp_path)
    assert bot.access_store.set_role(100, 200, bot.Role.USER, 100)
    monkeypatch.setattr(bot.time, "time", lambda: 100)
    assert bot.access_store.toggle_callback(100, 200, "cmd_status", 100, expires_at=200)
    monkeypatch.setattr(bot.time, "time", lambda: 201)

    asyncio.run(bot.on_admin_permission_toggle(Callback("admin:perm:200:cmd_status")))

    assert "Выдать: cmd:status" in rendered[-1][0]
    assert "Отозвать: cmd:status" not in rendered[-1][0]
    assert "Telegram не изменятся" in rendered[-1][0]


@pytest.mark.parametrize(("handler", "data", "expected"), [
    ("on_admin_permission_toggle", "admin:perm:200:cmd_status", "Laptop"),
    ("on_admin_device_toggle", "admin:device:200:a", "action_label"),
])
def test_admin_revocation_confirmation_shows_affected_grants_without_mutating(
    monkeypatch, tmp_path, handler, data, expected,
):
    Callback, rendered = _admin_fixture(monkeypatch, tmp_path)
    assert bot.access_store.set_role(100, 200, bot.Role.USER, 100)
    assert bot.access_store.toggle_device(100, 200, "a", 100)
    assert bot.access_store.toggle_callback(100, 200, "cmd:status", 100)
    assert bot.access_store.toggle_callback(100, 200, "cmd:screenshot", 100)
    before = bot.access_store.get_user(200)

    asyncio.run(getattr(bot, handler)(Callback(data)))

    text = rendered[-1][0]
    assert "Отозвать" in text
    assert (bot._nav(bot.USER_PERMISSION_CHOICES["cmd:status"]) if expected == "action_label" else expected) in text
    assert "Подтверждение действует 2 минуты" in text
    assert bot.access_store.get_user(200) == before


def test_admin_confirmation_expires_if_grant_state_changes_with_time(monkeypatch, tmp_path):
    Callback, rendered = _admin_fixture(monkeypatch, tmp_path)
    assert bot.access_store.set_role(100, 200, bot.Role.USER, 100)
    now = [199]
    monkeypatch.setattr(bot.time, "time", lambda: now[0])
    assert bot.access_store.toggle_callback(100, 200, "cmd:status", 100, expires_at=200)
    before = bot.access_store.get_user(200)

    async def scenario():
        await bot.on_admin_permission_toggle(Callback("admin:perm:200:cmd_status"))
        confirm = next(
            value for value in _callback_data(rendered[-1][1])
            if value.startswith("admin:confirm:")
        )
        now[0] = 201
        await bot.on_admin_confirm(Callback(confirm))

    asyncio.run(scenario())

    assert bot.access_store.get_user(200) == before


def test_admin_user_profile_hides_expired_permissions(monkeypatch, tmp_path):
    Callback, rendered = _admin_fixture(monkeypatch, tmp_path)
    assert bot.access_store.set_role(100, 200, bot.Role.USER, 100)
    monkeypatch.setattr(bot.time, "time", lambda: 100)
    assert bot.access_store.toggle_device(100, 200, "a", 100, expires_at=200)
    assert bot.access_store.toggle_callback(100, 200, "cmd:status", 100, expires_at=200)
    monkeypatch.setattr(bot.time, "time", lambda: 201)

    asyncio.run(bot.on_admin_user(Callback("admin:user:200")))

    assert "Устройства: a" not in rendered[-1][0]
    assert "Действия: cmd:status" not in rendered[-1][0]


def test_admin_user_profile_simulates_only_effective_device_actions(monkeypatch, tmp_path):
    Callback, rendered = _admin_fixture(monkeypatch, tmp_path)
    _setup(monkeypatch, {"a": {"name": "Laptop", "os": "Windows 11"}})
    assert bot.access_store.set_role(100, 200, bot.Role.USER, 100)
    assert bot.access_store.toggle_device(100, 200, "a", 100)
    assert bot.access_store.toggle_callback(100, 200, "cmd:screenshot", 100)

    asyncio.run(bot.on_admin_user(Callback("admin:user:200")))

    report = rendered[-1][0]
    assert "Симуляция доступности" in report
    assert "Laptop" in report
    simulation = report.split("Симуляция доступности", 1)[1]
    assert "cmd:screenshot" in simulation
    assert bot._lex("media_screenshot_button") in simulation
    assert "cmd:geo_location" not in simulation
    assert "действия не выполняются" in simulation

    assert not bot.access_store.toggle_callback(100, 200, "cmd:screenshot", 100)
    asyncio.run(bot.on_admin_user(Callback("admin:user:200")))
    simulation_without_action_grant = rendered[-1][0].split("Симуляция доступности", 1)[1]
    assert "cmd:screenshot" not in simulation_without_action_grant


def test_admin_user_profile_simulation_denies_blocked_user(monkeypatch, tmp_path):
    Callback, rendered = _admin_fixture(monkeypatch, tmp_path)
    _setup(monkeypatch, {"a": {"name": "Laptop", "os": "Windows 11"}})
    assert bot.access_store.set_role(100, 200, bot.Role.USER, 100)
    assert bot.access_store.toggle_device(100, 200, "a", 100)
    assert bot.access_store.toggle_callback(100, 200, "cmd:screenshot", 100)
    assert bot.access_store.set_blocked(100, 200, True, 100)

    asyncio.run(bot.on_admin_user(Callback("admin:user:200")))

    report = rendered[-1][0]
    assert "Симуляция доступности" in report
    simulation = report.split("Симуляция доступности", 1)[1]
    assert "Аккаунт заблокирован" in simulation
    assert "cmd:screenshot" not in simulation


def test_admin_user_access_simulation_matches_guest_read_only_device_menu(monkeypatch, tmp_path):
    _Callback, _rendered = _admin_fixture(monkeypatch, tmp_path)
    _setup(monkeypatch, {"a": {"name": "Laptop", "os": "Windows 11"}})

    simulation = bot._admin_user_access_simulation(200)

    assert "menu:guest_devices" in simulation
    assert "Laptop" in simulation
    assert "guest:readonly" in simulation
    assert "cat:screen" not in simulation
    assert "cmd:screenshot" not in simulation


def test_admin_user_access_simulation_matches_owner_device_actions(monkeypatch, tmp_path):
    _admin_fixture(monkeypatch, tmp_path)
    monkeypatch.setattr(roles, "ADMIN_ID", 100)
    assert bot.get_user_role(100) == bot.Role.OWNER
    _setup(monkeypatch, {"a": {"name": "Laptop", "os": "Windows 11"}})

    simulation = bot._admin_user_access_simulation(100)

    assert "menu:admin" in simulation
    assert "cat:screen" in simulation
    assert "cmd:screenshot" in simulation


@pytest.mark.parametrize("handler,data", [
    ("on_admin_role", "admin:role:200:user"),
    ("on_admin_block", "admin:block:200"),
    ("on_admin_device_toggle", "admin:device:200:a"),
    ("on_admin_permission_toggle", "admin:perm:200:cmd_status"),
])
def test_admin_mutations_require_confirm_and_reject_cancel_expiry_replay(monkeypatch, tmp_path, handler, data):
    Callback, rendered = _admin_fixture(monkeypatch, tmp_path)
    before = bot.access_store.get_user(200)
    now = [1000.0]
    monkeypatch.setattr(bot.time, "monotonic", lambda: now[0])
    async def scenario():
        await getattr(bot, handler)(Callback(data))
        assert bot.access_store.get_user(200) == before
        actions = _callback_data(rendered[-1][1])
        confirm = next(value for value in actions if value.startswith("admin:confirm:"))
        cancel = next(value for value in actions if value.startswith("admin:cancel:"))
        await bot.on_admin_confirm(Callback(confirm, actor=200))
        assert bot.access_store.get_user(200) == before
        await bot.on_admin_cancel(Callback(cancel))
        await bot.on_admin_confirm(Callback(confirm))
        assert bot.access_store.get_user(200) == before
        await getattr(bot, handler)(Callback(data))
        confirm = next(value for value in _callback_data(rendered[-1][1]) if value.startswith("admin:confirm:"))
        now[0] += 121
        await bot.on_admin_confirm(Callback(confirm))
        assert bot.access_store.get_user(200) == before
        await getattr(bot, handler)(Callback(data))
        confirm = next(value for value in _callback_data(rendered[-1][1]) if value.startswith("admin:confirm:"))
        await bot.on_admin_confirm(Callback(confirm))
        changed = bot.access_store.get_user(200)
        assert changed != before
        await bot.on_admin_confirm(Callback(confirm))
        assert bot.access_store.get_user(200) == changed
    asyncio.run(scenario())


def test_admin_unblock_requires_confirmation_and_restores_exact_prior_access(monkeypatch, tmp_path):
    Callback, rendered = _admin_fixture(monkeypatch, tmp_path)
    assert bot.access_store.set_role(100, 200, bot.Role.USER, 100)
    assert bot.access_store.toggle_device(100, 200, "a", 100)
    assert bot.access_store.toggle_callback(100, 200, "cmd:status", 100)
    assert bot.access_store.set_blocked(100, 200, True, 100)
    before = bot.access_store.get_user(200)
    now = [1000.0]
    monkeypatch.setattr(bot.time, "monotonic", lambda: now[0])

    async def pending_confirmation():
        await bot.on_admin_block(Callback("admin:block:200"))
        assert bot.access_store.get_user(200) == before
        actions = _callback_data(rendered[-1][1])
        return next(value for value in actions if value.startswith("admin:confirm:")), next(
            value for value in actions if value.startswith("admin:cancel:")
        )

    async def scenario():
        confirm, cancel = await pending_confirmation()
        await bot.on_admin_cancel(Callback(cancel))
        await bot.on_admin_confirm(Callback(confirm))
        assert bot.access_store.get_user(200) == before

        confirm, _ = await pending_confirmation()
        now[0] += 121
        await bot.on_admin_confirm(Callback(confirm))
        assert bot.access_store.get_user(200) == before

        confirm, _ = await pending_confirmation()
        await bot.on_admin_confirm(Callback(confirm))
        restored = bot.access_store.get_user(200)
        assert restored["role"] == bot.Role.USER
        assert restored["permissions"] == before["permissions"]
        assert bot.access_store.get_role(200, 100) == bot.Role.USER
        await bot.on_admin_confirm(Callback(confirm))
        assert bot.access_store.get_user(200) == restored

    asyncio.run(scenario())


@pytest.mark.parametrize("actor", [200, 300, 400])
@pytest.mark.parametrize("handler,data", [
    ("on_admin_users", "admin:users"), ("on_admin_user", "admin:user:200"),
    ("on_admin_audit", "admin:audit"), ("on_admin_devices", "admin:devices:200"),
    ("on_admin_permissions", "admin:perms:200"), ("on_admin_texts", "admin:texts"),
    ("on_admin_role", "admin:role:200:user"), ("on_admin_block", "admin:block:200"),
])
def test_direct_admin_calls_deny_user_guest_blocked_without_private_reads(monkeypatch, tmp_path, actor, handler, data):
    Callback, rendered = _admin_fixture(monkeypatch, tmp_path)
    if actor == 200:
        bot.access_store.set_role(100, actor, bot.Role.USER, 100)
    if actor == 400:
        from types import SimpleNamespace
        bot.access_store.register_start(SimpleNamespace(id=actor))
        bot.access_store.set_blocked(100, actor, True, 100)
    def private(*args, **kwargs):
        pytest.fail("unauthorized admin path read private data")
    monkeypatch.setattr(bot.access_store, "get_user", private)
    monkeypatch.setattr(bot.access_store, "list_users", private)
    monkeypatch.setattr(bot.access_store, "recent_audit", private)
    asyncio.run(getattr(bot, handler)(Callback(data, actor)))
    assert not rendered


@pytest.mark.parametrize("handler,data", [
    ("on_admin_role", "admin:role:999:user"), ("on_admin_devices", "admin:devices:999"),
    ("on_admin_permissions", "admin:perms:999"), ("on_admin_block", "admin:block:999"),
])
def test_unknown_admin_target_neither_created_nor_rendered(monkeypatch, tmp_path, handler, data):
    Callback, rendered = _admin_fixture(monkeypatch, tmp_path)
    asyncio.run(getattr(bot, handler)(Callback(data)))
    assert bot.access_store.get_user(999) is None
    assert not rendered


def test_admin_log_omits_payload_fields_and_redacts_nested_credentials(monkeypatch):
    rows = []
    monkeypatch.setattr(bot.audit_log, "info", lambda *args: rows.append(str(args)))
    bot.audit("operation", latitude="41.123", longitude="69.456", stdout="raw-result",
              shell="rm -rf /", file_bytes=b"private-file", detail="MQTT_PASSWORD=credential")
    logged = " ".join(rows)
    for private in ("41.123", "69.456", "raw-result", "rm -rf", "private-file", "credential"):
        assert private not in logged


def test_start_notification_deduplicates_concurrent_updates(monkeypatch, tmp_path):
    from types import SimpleNamespace
    Callback, rendered = _admin_fixture(monkeypatch, tmp_path)
    monkeypatch.setattr(bot, "_START_NOTIFY_LAST", {})
    monkeypatch.setattr(bot, "get_user_role", lambda _id: bot.Role.GUEST)
    notices = []
    entered = asyncio.Event()
    release = asyncio.Event()
    async def notify(text, **kwargs):
        notices.append(text)
        entered.set()
        await release.wait()
        return True
    async def card(*args, **kwargs):
        pass
    monkeypatch.setattr(bot, "_notify_admins", notify)
    monkeypatch.setattr(bot, "_show_start_card", card)
    class State:
        async def clear(self):
            pass
    message = SimpleNamespace(from_user=SimpleNamespace(id=200, username="u"), chat=SimpleNamespace(id=200))
    async def scenario():
        first = asyncio.create_task(bot.cmd_start(message, State()))
        await entered.wait()
        second = asyncio.create_task(bot.cmd_start(message, State()))
        await asyncio.sleep(0)
        release.set()
        await asyncio.gather(first, second)
    asyncio.run(scenario())
    assert len(notices) == 1


def test_start_retries_failed_owner_delivery_through_real_notification_helper(monkeypatch, tmp_path):
    from types import SimpleNamespace
    _admin_fixture(monkeypatch, tmp_path)
    monkeypatch.setattr(bot.ui_cards, "FILE", tmp_path / "cards.json")
    monkeypatch.setattr(bot, "_START_NOTIFY_LAST", {})
    monkeypatch.setattr(bot.bot_settings, "get", lambda _key, default=None: default)
    attempts = []
    async def send(target, text, **kwargs):
        attempts.append(target)
        if len(attempts) == 1:
            raise RuntimeError("Telegram temporarily unavailable")
    monkeypatch.setattr(bot.bot, "send_message", send)
    class State:
        async def clear(self):
            pass
    class Message:
        from_user = SimpleNamespace(id=200, username="u")
        chat = SimpleNamespace(id=200)
        async def answer(self, *args, **kwargs):
            return SimpleNamespace(message_id=30, chat=self.chat)
    async def scenario():
        await bot.cmd_start(Message(), State())
        assert 200 not in bot._START_NOTIFY_LAST
        await bot.cmd_start(Message(), State())
        assert attempts == [100, 100]
        assert 200 in bot._START_NOTIFY_LAST
        await bot.cmd_start(Message(), State())
        assert attempts == [100, 100]
    asyncio.run(scenario())


def test_admin_text_and_outbound_message_need_owner_decision(monkeypatch, tmp_path):
    from types import SimpleNamespace
    Callback, rendered = _admin_fixture(monkeypatch, tmp_path)
    monkeypatch.setattr(bot.text_store, "FILE", tmp_path / "texts.json")
    class Message:
        def __init__(self, actor, text):
            self.from_user = SimpleNamespace(id=actor)
            self.text = text
    monkeypatch.setattr(bot, "Message", Message)
    class State:
        def __init__(self, data):
            self.data = data
        async def get_data(self):
            return self.data.copy()
        async def clear(self):
            self.data.clear()
    sent = []
    async def send(target, text):
        sent.append((target, text))
    monkeypatch.setattr(bot.bot, "send_message", send)
    key = "xtech_start_guest"
    original = bot.text_store.get(key)
    async def scenario():
        for actor in (200, 300):
            await bot.on_admin_text_value(Message(actor, "Unapproved"), State({"bot_text_key": key}))
            await bot.on_admin_message_text(Message(actor, "Unapproved"), State({"admin_message_user": 200}))
        assert not rendered and not sent
        assert bot.text_store.get(key) == original
        await bot.on_admin_text_value(Message(100, "Approved text"), State({"bot_text_key": key}))
        assert bot.text_store.get(key) == original
        confirm = next(value for value in _callback_data(rendered[-1][1]) if value.startswith("admin:confirm:"))
        await bot.on_admin_confirm(Callback(confirm))
        assert bot.text_store.get(key) == "Approved text"
        await bot.on_admin_message_text(Message(100, "Sensitive <text>"), State({"admin_message_user": 200}))
        assert not sent
        confirm = next(value for value in _callback_data(rendered[-1][1]) if value.startswith("admin:confirm:"))
        await bot.on_admin_confirm(Callback(confirm))
        await bot.on_admin_confirm(Callback(confirm))
        assert sent == [(200, "Sensitive &lt;text&gt;")]
        assert all("Sensitive" not in row["detail"] for row in bot.access_store.recent_audit())
    asyncio.run(scenario())


def test_admin_preview_is_invalidated_by_intervening_access_change(monkeypatch, tmp_path):
    Callback, rendered = _admin_fixture(monkeypatch, tmp_path)
    async def scenario():
        await bot.on_admin_role(Callback("admin:role:200:user"))
        confirm = next(value for value in _callback_data(rendered[-1][1]) if value.startswith("admin:confirm:"))
        bot.access_store.set_blocked(100, 200, True, 100)
        await bot.on_admin_confirm(Callback(confirm))
        assert bot.access_store.get_role(200, 100) == bot.Role.BLOCKED
    asyncio.run(scenario())


def test_admin_block_confirmation_shows_the_access_it_will_revoke(monkeypatch, tmp_path):
    Callback, rendered = _admin_fixture(monkeypatch, tmp_path)
    assert bot.access_store.set_role(100, 200, bot.Role.USER, 100)
    assert bot.access_store.toggle_device(100, 200, "a", 100)
    assert bot.access_store.toggle_callback(100, 200, "cmd_status", 100)

    asyncio.run(bot.on_admin_block(Callback("admin:block:200")))

    preview = rendered[-1][0]
    assert "Доступ будет закрыт" in preview
    assert "Устройства: 1" in preview
    assert "Действия: 1" in preview
    assert "cmd_status" in preview and "a" in preview
    assert "После разблокировки вернётся роль Пользователь" in preview


def test_admin_role_confirmation_explains_preserved_grants_for_guest(monkeypatch, tmp_path):
    Callback, rendered = _admin_fixture(monkeypatch, tmp_path)
    assert bot.access_store.set_role(100, 200, bot.Role.USER, 100)
    assert bot.access_store.toggle_device(100, 200, "a", 100)
    assert bot.access_store.toggle_callback(100, 200, "cmd_status", 100)

    asyncio.run(bot.on_admin_role(Callback("admin:role:200:guest")))

    preview = rendered[-1][0]
    assert f"{bot._role_label(bot.Role.USER)} → {bot._role_label(bot.Role.GUEST)}" in preview
    assert "Сохранённые права" in preview
    assert "Устройства: 1" in preview
    assert "Действия: 1" in preview
    assert "гост" in preview.lower()


def test_admin_confirmation_is_invalidated_when_only_a_grant_expiry_changes(monkeypatch, tmp_path):
    Callback, rendered = _admin_fixture(monkeypatch, tmp_path)
    assert bot.access_store.set_role(100, 200, bot.Role.USER, 100)
    monkeypatch.setattr(bot.time, "time", lambda: 1_000)
    assert bot.access_store.toggle_callback(100, 200, "cmd_status", 100, expires_at=2_000)
    async def scenario():
        await bot.on_admin_role(Callback("admin:role:200:guest"))
        confirm = next(value for value in _callback_data(rendered[-1][1]) if value.startswith("admin:confirm:"))
        assert bot.access_store.toggle_callback(100, 200, "cmd_status", 100) is False
        assert bot.access_store.toggle_callback(100, 200, "cmd_status", 100, expires_at=3_000) is True
        await bot.on_admin_confirm(Callback(confirm))
        assert bot.access_store.get_role(200, 100) == bot.Role.USER
    asyncio.run(scenario())


def test_owner_user_card_includes_activity_and_actual_grants(monkeypatch, tmp_path):
    Callback, rendered = _admin_fixture(monkeypatch, tmp_path)
    bot.access_store.toggle_device(100, 200, "a", 100)
    bot.access_store.toggle_callback(100, 200, "cmd:status", 100)
    asyncio.run(bot.on_admin_user(Callback("admin:user:200")))
    assert "Последнее посещение" in rendered[-1][0]
    assert "Устройства: a" in rendered[-1][0]
    assert "Действия: cmd:status" in rendered[-1][0]
    asyncio.run(bot.on_admin_users(Callback("admin:users")))
    assert "Последнее посещение" in rendered[-1][0]
    assert "cmd:status" in rendered[-1][0]


def test_owner_cannot_stage_self_role_block_or_grants(monkeypatch, tmp_path):
    Callback, rendered = _admin_fixture(monkeypatch, tmp_path)
    async def scenario():
        for handler, data in (
            (bot.on_admin_role, "admin:role:100:guest"),
            (bot.on_admin_block, "admin:block:100"),
            (bot.on_admin_device_toggle, "admin:device:100:a"),
            (bot.on_admin_permission_toggle, "admin:perm:100:cmd_status"),
        ):
            await handler(Callback(data))
    asyncio.run(scenario())
    assert not rendered
    assert bot.access_store.get_role(100, 100) == bot.Role.OWNER


def test_middleware_audits_metadata_instead_of_message_or_callback_payload(monkeypatch, tmp_path):
    from types import SimpleNamespace
    _admin_fixture(monkeypatch, tmp_path)
    logged = []
    monkeypatch.setattr(bot.log, "info", lambda *args: logged.append(str(args)))
    user = SimpleNamespace(id=200, username="u")
    async def handler(event, data):
        return "ok"
    async def scenario():
        await bot._live_log_middleware(handler, SimpleNamespace(
            message=SimpleNamespace(from_user=user, text="41.123 69.456 raw-file whoami", content_type="text"),
            callback_query=None), {})
        await bot._live_log_middleware(handler, SimpleNamespace(message=None,
            callback_query=SimpleNamespace(from_user=user, data="admin:role:200:user secret payload")), {})
    asyncio.run(scenario())
    data = " ".join(logged) + str(bot.access_store.recent_audit())
    assert "41.123" not in data and "raw-file" not in data and "whoami" not in data
    assert "secret payload" not in data

DEVICE_SECTIONS = {
    "cat:system", "cat:screen", "cat:files", "cat:network", "cat:terminal",
    "cat:power", "versions:device", "cmd:capabilities", "cat:pranks",
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

    def get_device_relations(self, actor_id, owner_id, device_id):
        return list(getattr(self, "_relations", {}).get(device_id, [])) if int(actor_id) == int(owner_id) else None

    def set_device_relation(self, actor_id, owner_id, device_a, device_b, linked):
        return int(actor_id) == int(owner_id)


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
    # New feature labels stay local to registered renderers because this approved
    # implementation freezes X-LEX contents and behavior.
    assert literal_button_labels == {
        "◀️", "▶️", "🧾 Показать хронологию", "⏹ Остановить наблюдение",
            "📄 Скачать JSON-отчёт", "➕ Создать набор", "⏹ Выключить",
            "🧩 Поля JSON", "🧾 Исходный текст",
        "🆚 Выбрать для сравнения",
        "🔁 Повторить как запрос",
        "Удалить шаблон",
        "Отмена",
    }


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
        "on_cmd_shell", "on_cmd_open_app", "on_openapp_ok",
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
    waits = []
    monkeypatch.setattr(bot, "_pending_device_input_still_allowed", lambda *_args: True)

    async def replace_user(_message, text, reply_markup=None):
        shown.append((text, reply_markup))

    async def replace_callback(_cq, text, reply_markup=None):
        shown.append((text, reply_markup))

    monkeypatch.setattr(bot, "_replace_user_card", replace_user)
    monkeypatch.setattr(bot, "_device_action_still_allowed", lambda *_args: True)
    monkeypatch.setattr(bot, "target_label", lambda target: target)
    monkeypatch.setattr(
        bot, "publish_tracked",
        lambda action, **kwargs: published.append((action, kwargs)) or (True, "open-operation-1"),
    )
    monkeypatch.setattr(bot, "publish", lambda *_args, **_kwargs: pytest.fail("URL flow must retain its operation id"))

    async def wait_for(device_id, action_type, timeout, command_id):
        waits.append((device_id, action_type, timeout, command_id))
        return {
            "type": "open_url",
            "id": command_id,
            "ok": True,
            "requested_count": 3,
            "accepted_count": 3,
        }

    monkeypatch.setattr(bot.fun_text_collector, "wait_for", wait_for)
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
    assert waits and waits[0][:2] == ("device-1", "open_url")
    assert waits[0][3] == "open-operation-1"
    assert "3 из 3" in shown[-1][0]
    assert "MQTT" in shown[-1][0]
    assert state.cleared


def test_revalidates_revoked_grant(monkeypatch):
    class FakeState:
        def __init__(self):
            self.current = bot.Form.wait_url_count
            self.cleared = False

        async def get_state(self):
            return self.current

        async def get_data(self):
            allowed[0] = False
            return {
                "url": "https://example.com/docs",
                "authorization_target": "device-1",
                "authorization_callback": "cmd:url",
            }

        async def clear(self):
            self.cleared = True

    allowed = [True]
    checks = []
    shown = []
    published = []
    state = FakeState()
    monkeypatch.setattr(
        bot, "_device_action_still_allowed",
        lambda *_args: checks.append(allowed[0]) or allowed[0],
    )
    monkeypatch.setattr(bot, "publish_tracked", lambda *args, **kwargs: published.append((args, kwargs)))

    async def replace(_cq, text, reply_markup=None, **_kwargs):
        shown.append(text)

    monkeypatch.setattr(bot, "_replace_callback_message", replace)

    class Callback:
        data = "url:count:3"
        from_user = type("User", (), {"id": bot.ADMIN_ID})()

        async def answer(self, *args, **kwargs):
            pass

    asyncio.run(bot.on_url_count_select(Callback(), state))

    assert checks == [False]
    assert published == []
    assert shown[-1] == bot._lex("input_access_revoked")
    assert state.cleared


def test_duplicate_operation_id_launches_once(monkeypatch):
    class FakeState:
        def __init__(self):
            self.current = bot.Form.wait_url_count
            self.data = {
                "url": "https://example.com/docs",
                "authorization_target": "device-1",
                "authorization_callback": "cmd:url",
            }
            self.cleared = False
            self.data_reads = 0
            self.first_data_read = asyncio.Event()
            self.second_arrived = asyncio.Event()
            self.release_data = asyncio.Event()

        async def get_state(self):
            return self.current

        async def get_data(self):
            self.data_reads += 1
            if self.data_reads == 1:
                self.first_data_read.set()
            elif self.data_reads == 2:
                self.second_arrived.set()
            await self.release_data.wait()
            return dict(self.data)

        async def clear(self):
            self.cleared = True
            self.current = None

    published = []
    state = FakeState()
    monkeypatch.setattr(bot, "_ACTIVE_UI_COMMANDS", set())
    monkeypatch.setattr(bot, "_device_action_still_allowed", lambda *_args: True)
    monkeypatch.setattr(
        bot, "publish_tracked",
        lambda action, **kwargs: published.append((action, kwargs)) or (True, "same-operation-id"),
    )
    monkeypatch.setattr(bot, "publish", lambda *_args, **_kwargs: pytest.fail("URL flow must use tracked MQTT ids"))
    monkeypatch.setattr(
        bot.fun_text_collector,
        "wait_for",
        lambda *_args, **_kwargs: asyncio.sleep(0, result={
            "type": "open_url", "id": "same-operation-id", "ok": True,
            "requested_count": 1, "accepted_count": 1,
        }),
    )
    monkeypatch.setattr(bot, "target_label", lambda target: target)

    async def replace(*_args, **_kwargs):
        pass

    monkeypatch.setattr(bot, "_replace_callback_message", replace)

    class Callback:
        def __init__(self):
            self.data = "url:count:1"
            self.from_user = type("User", (), {"id": bot.ADMIN_ID})()
            self.state = state
            self.message = type("Message", (), {
                "chat": type("Chat", (), {"id": 1})(),
                "message_id": 44,
            })()
            self.answers = []

        async def answer(self, *args, **kwargs):
            self.answers.append((args, kwargs))
            self.state.second_arrived.set()

    async def scenario():
        first = Callback()
        second = Callback()
        first_task = asyncio.create_task(bot.on_url_count_select(first, state))
        await state.first_data_read.wait()
        second_task = asyncio.create_task(bot.on_url_count_select(second, state))
        await asyncio.wait_for(state.second_arrived.wait(), 2)
        state.release_data.set()
        await asyncio.gather(first_task, second_task)
        await bot.on_url_count_select(second, state)
        return second

    second = asyncio.run(scenario())

    assert published == [("open_url", {"_target": "device-1", "url": "https://example.com/docs", "count": 1})]
    assert second.answers[0] == ((bot._lex("command_already_running"),), {"show_alert": True})
    assert second.answers[1] == ((bot._lex("url_request_expired"),), {"show_alert": True})


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


def test_network_menu_exposes_path_diagnostic_only_to_owner():
    owner = bot.network_menu(user_id=bot.ADMIN_ID)
    guest = bot.network_menu(user_id=bot.ADMIN_ID + 1)
    owner_buttons = {button.callback_data: button.text for row in owner.inline_keyboard for button in row}
    guest_callbacks = set(_callback_data(guest))

    assert owner_buttons["cmd:path_diagnostic"] == "🧭 Где обрыв?"
    assert "cmd:path_diagnostic" not in guest_callbacks
    assert _expected_callback_gate("cmd:path_diagnostic") == "OwnerFilter"


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

    async def replace(_cq, text, reply_markup=None, **_kwargs):
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
    assert len(authorization_checks) >= 2
    assert set(authorization_checks) == {(77, "cmd:wifi", "device-1")}
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

    async def replace(_cq, text, reply_markup=None, **_kwargs):
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

    async def replace(_cq, text, reply_markup=None, **_kwargs):
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
        ("on_cmd_screenshot", "screenshot_collector", "screenshot", "cmd:screenshot", {"image": "eA=="}),
        ("on_camera_capture", "webcam_collector", "webcam", "camera:capture", {"image": "eA=="}),
        ("on_cmd_processes", "processes_collector", "processes", "cmd:processes", {"lines": ["worker"]}),
        ("on_cmd_battery", "battery_collector", "battery", "cmd:battery", {"available": True, "percent": 42}),
        ("on_cmd_network", "network_collector", "network", "cmd:network", {"interfaces": []}),
        ("on_cmd_services", "services_collector", "services", "cmd:services", {"total": 4, "running": 2}),
        ("on_cmd_capabilities", "capabilities_collector", "capabilities", "cmd:capabilities", {}),
        ("on_cmd_sysinfo", "sysinfo_collector", "sysinfo", "cmd:sysinfo", {"device_id": "device-1"}),
        ("on_cmd_status", "status_collector", "status_request", "cmd:status", {}),
        ("on_cmd_disks", "disks_collector", "disks", "cmd:disks", {"lines": ["C: 1 GB"]}),
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

    async def replace(_cq, text, reply_markup=None, **_kwargs):
        replacement_cards.append((text, reply_markup))

    monkeypatch.setitem(bot.SESSION, "target", "device-1")
    if handler_name == "on_camera_capture":
        monkeypatch.setattr(bot, "_CAMERA_CATALOGS", {
            (77, "device-1"): {
                "expires_at": time.time() + 60,
                "cameras": {"0123456789abcdef": "Camera"},
                "selected": "0123456789abcdef",
            },
        })
        monkeypatch.setattr(bot, "_simple_command_still_allowed", lambda *_args: allowed)
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
    if handler_name == "on_camera_capture":
        expected_kwargs["camera_id"] = "0123456789abcdef"
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

    async def replace(_cq, text, reply_markup=None, **_kwargs):
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

    async def replace(_cq, text, reply_markup=None, **_kwargs):
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
    markup = bot.device_menu("dev1")
    cbs = _callback_data(markup)
    assert DEVICE_SECTIONS.issubset(set(cbs))
    capabilities_button = next(
        button for row in markup.inline_keyboard for button in row
        if button.callback_data == "cmd:capabilities"
    )
    assert capabilities_button.text.startswith("🔐 Разрешения · ")
    assert "cat:media" not in cbs
    assert "cat:input" not in cbs


def test_device_tags_entry_is_owner_only_and_registry_backed(monkeypatch):
    _setup(monkeypatch, {"dev1": {"name": "Dev", "os": "Windows"}})
    roles = {100: bot.Role.OWNER, 200: bot.Role.USER}
    monkeypatch.setattr(bot, "get_user_role", lambda user_id: roles[user_id])
    monkeypatch.setattr(bot, "SESSION", {"target": "dev1"})
    monkeypatch.setattr(bot.access_store, "get_effective_grants", lambda _user: {"callbacks": [], "devices": []})
    monkeypatch.setattr(bot.access_store, "can_use_callback", lambda user_id, callback, *_args, **_kwargs: user_id == 100)

    owner_callbacks = _callback_data(bot.device_menu("dev1", 100))
    user_callbacks = _callback_data(bot.device_menu("dev1", 200))

    assert "catalog:tags:list" in owner_callbacks
    assert "catalog:tags:list" not in user_callbacks


def test_pinned_device_entry_and_toggle_are_owner_only_and_registry_backed(monkeypatch):
    _setup(monkeypatch, {"dev1": {"name": "Desk PC", "os": "Windows"}})
    roles = {100: bot.Role.OWNER, 200: bot.Role.USER}
    monkeypatch.setattr(bot, "get_user_role", lambda user_id: roles[user_id])
    monkeypatch.setattr(bot.access_store, "get_effective_grants", lambda _user: {"callbacks": [], "devices": []})
    monkeypatch.setattr(bot.access_store, "can_use_callback", lambda user_id, *_args, **_kwargs: user_id == 100)
    monkeypatch.setattr(bot.devices, "list_pinned_devices", lambda actor_id, owner_id: ["dev1"] if actor_id == 100 else [], raising=False)

    owner_device = _callback_data(bot.device_menu("dev1", 100))
    user_device = _callback_data(bot.device_menu("dev1", 200))
    owner_list = _callback_data(bot.devices_menu(100))
    user_list = _callback_data(bot.devices_menu(200))

    assert "catalog:pinned:toggle:dev1" in owner_device
    assert "catalog:pinned:toggle:dev1" not in user_device
    assert "catalog:pinned:list" in owner_list
    assert "catalog:pinned:list" not in user_list


def test_device_menu_never_emits_oversized_pinned_toggle_callback(monkeypatch):
    device_id = "x" * 43
    _setup(monkeypatch, {device_id: {"name": "Long stable ID", "os": "Windows"}})
    monkeypatch.setattr(bot, "get_user_role", lambda _user: bot.Role.OWNER)
    monkeypatch.setattr(bot.access_store, "get_effective_grants", lambda _user: {"callbacks": [], "devices": []})
    monkeypatch.setattr(bot.access_store, "can_use_callback", lambda *_args, **_kwargs: True)

    callbacks = _callback_data(bot.device_menu(device_id, 100))

    assert all(len(callback.encode("utf-8")) <= 64 for callback in callbacks)
    assert not any(callback.startswith("catalog:pinned:toggle:") for callback in callbacks)


def test_pinned_device_list_opens_only_currently_visible_devices(monkeypatch):
    _setup(monkeypatch, {
        "visible": {"name": "Desk PC", "os": "Windows"},
        "hidden": {"name": "Private Mac", "os": "macOS"},
    })
    monkeypatch.setattr(bot, "get_user_role", lambda _user: bot.Role.OWNER)
    monkeypatch.setattr(bot.access_store, "get_effective_grants", lambda _user: {"callbacks": [], "devices": []})
    monkeypatch.setattr(bot.access_store, "can_use_callback", lambda _user, callback, *_args, **_kwargs: callback != "dev:hidden")
    monkeypatch.setattr(bot.devices, "list_pinned_devices", lambda **_kwargs: ["visible", "hidden", "removed"], raising=False)

    rows, markup = bot.device_pinned_catalog(100)

    assert [device_id for device_id, _ in rows] == ["visible"]
    assert _callback_data(markup) == ["dev:visible", "menu:devices", "menu:main"]


def test_device_tags_editor_persists_owner_input_and_replaces_one_card(monkeypatch):
    import asyncio
    from types import SimpleNamespace

    _setup(monkeypatch, {"dev1": {"name": "Dev", "os": "Windows"}})
    monkeypatch.setattr(bot, "get_user_role", lambda _user: bot.Role.OWNER)
    monkeypatch.setattr(bot, "SESSION", {"target": "dev1"})
    monkeypatch.setattr(bot.access_store, "get_effective_grants", lambda _user: {"callbacks": [], "devices": []})
    saved = []
    monkeypatch.setattr(bot.devices, "set_device_tags", lambda actor, owner, device, tags: saved.append((actor, owner, device, tags)) or True, raising=False)
    monkeypatch.setattr(bot.devices, "get_device_tags", lambda _actor, _owner, _device: ["test", "home"], raising=False)
    rendered = []

    async def replace(message, text, reply_markup=None, **_kwargs):
        rendered.append((message, text, reply_markup))

    monkeypatch.setattr(bot, "_replace_user_card", replace)

    class State:
        data = {"device_tags_target": "dev1"}
        async def get_data(self): return dict(self.data)
        async def clear(self): self.data = {}

    message = SimpleNamespace(from_user=SimpleNamespace(id=bot.ADMIN_ID), text=" test, home, test ")
    asyncio.run(bot.on_device_tags_input(message, State()))

    assert saved == [(bot.ADMIN_ID, bot.ADMIN_ID, "dev1", ["test", "home", "test"])]
    assert len(rendered) == 1
    assert "test" in rendered[0][1] and "home" in rendered[0][1]
    assert rendered[0][2] is not None


def test_navigation_uses_the_approved_root_and_device_hierarchy(monkeypatch):
    _setup(monkeypatch, {"dev1": {"name": "Dev", "os": "Windows"}})
    monkeypatch.setattr(
        bot, "get_user_role",
        lambda user_id: {
            100: bot.Role.OWNER,
            200: bot.Role.USER,
            300: bot.Role.GUEST,
            400: bot.Role.BLOCKED,
        }[user_id],
    )
    monkeypatch.setattr(bot, "SESSION", {"target": "dev1"})
    monkeypatch.setattr(
        bot.access_store, "can_use_callback",
        lambda user_id, callback, _owner_id, target=None:
            user_id == 100 or (
                user_id == 200 and target == "dev1" and callback in {
                    "cat:system", "cat:screen", "cat:files", "cat:network",
                    "cat:terminal", "cat:power", "cat:pranks", "cmd:capabilities",
                    "cmd:screenshot", "files:list", "cmd:apps", "proc:0", "dev:dev1",
                }
            ),
    )

    assert _callback_data(bot.main_menu(100)) == [
        "menu:devices", "menu:server", "menu:admin", "catalog:areas", "menu:about",
    ]
    assert _callback_data(bot.main_menu(200)) == [
        "menu:devices", "menu:server", "access:request", "catalog:areas", "menu:about",
    ]
    assert _callback_data(bot.main_menu(300)) == [
        "menu:guest_devices", "menu:server", "catalog:areas", "menu:about",
    ]
    assert _callback_data(bot.main_menu(400)) == []

    expected_owner_sections = {
        "cat:system", "cat:screen", "cat:files", "cat:network", "cat:terminal",
        "cat:power", "versions:device", "cmd:capabilities", "cat:pranks",
    }
    owner_device = set(_callback_data(bot.device_menu("dev1", 100)))
    assert expected_owner_sections <= owner_device
    assert "cat:device" not in owner_device
    assert "cfm:stop" not in owner_device
    assert "dev:all" in _callback_data(bot.devices_menu(100))
    assert "ev:menu" in _callback_data(bot.admin_menu())
    user_device = set(_callback_data(bot.device_menu("dev1", 200)))
    assert {
        "cat:system", "cat:screen", "cat:files", "cat:network", "cat:terminal",
        "cat:power", "cat:pranks", "cmd:capabilities",
    } <= user_device
    assert "cat:device" not in user_device
    assert "versions:device" not in user_device
    assert not any(callback.startswith(("devmg:", "admin:", "server:")) for callback in user_device)


def test_user_permission_request_is_submitted_and_not_granted(monkeypatch, tmp_path):
    Callback, rendered = _admin_fixture(monkeypatch, tmp_path)
    _setup(monkeypatch, {"mac-a": {"name": "Mac"}})
    assert bot.access_store.set_role(100, 200, bot.Role.USER, 100)
    before = bot.access_store.get_effective_grants(200)
    notices = []

    class State:
        async def clear(self):
            pass
        async def set_state(self, value):
            self.value = value

    class Message:
        text = "callback:cmd:screenshot"
        from_user = SimpleNamespace(id=200, username="u")

    async def send_message(chat_id, text, **kwargs):
        notices.append((chat_id, text, kwargs))

    monkeypatch.setattr(bot.bot, "send_message", send_message)

    async def scenario():
        await bot.on_access_request_start(Callback("access:request", actor=200), State())
        await bot.on_access_request_input(Message(), State())

    asyncio.run(scenario())

    assert "access:request" in _callback_data(bot.main_menu(200))
    assert bot.access_store.get_effective_grants(200) == before
    pending = bot.access_store.list_permission_requests(100, 100)
    assert len(pending) == 1
    assert pending[0]["item"] == "cmd:screenshot"
    assert len(notices) == 1 and notices[0][0] == 100
    assert "Право ещё не выдано" in notices[0][1]
    assert "Заявка передана владельцу" in rendered[-1][0]


def test_permissions_and_device_settings_are_reachable_with_exact_device_grants(
    monkeypatch, tmp_path,
):
    from types import SimpleNamespace
    import roles
    from roles import AdminFilter

    _setup(monkeypatch, {
        "mac-a": {"name": "Granted Mac", "os": "macOS"},
        "pc-b": {"name": "Other PC", "os": "Windows"},
    })
    monkeypatch.setattr(bot, "ADMIN_ID", 100)
    monkeypatch.setattr(roles, "ADMIN_ID", 100)
    owner_id, user_id, guest_id = bot.ADMIN_ID, 200, 300
    monkeypatch.setattr(bot.access_store, "FILE", tmp_path / "access.json")
    monkeypatch.setattr(bot.access_store, "AUDIT_FILE", tmp_path / "audit.jsonl")
    for identity in (user_id, guest_id):
        bot.access_store.register_start(SimpleNamespace(
            id=identity, username="test", first_name="Test", last_name="User",
        ))
    assert bot.access_store.set_role(owner_id, user_id, bot.Role.USER, owner_id)
    assert bot.access_store.toggle_device(owner_id, user_id, "mac-a", owner_id)
    assert bot.access_store.toggle_callback(owner_id, user_id, "cmd:capabilities", owner_id)
    assert bot.access_store.toggle_callback(owner_id, user_id, "cmd:autorun_status", owner_id)
    monkeypatch.setattr(bot, "SESSION", {"target": "mac-a"})

    user_root = set(_callback_data(bot.device_menu("mac-a", user_id)))
    assert "cmd:capabilities" in user_root
    assert "cat:pranks" in user_root
    assert "dev:mac-a" in _callback_data(bot.devices_menu(user_id))
    assert "dev:pc-b" not in _callback_data(bot.devices_menu(user_id))
    assert bot.access_store.can_use_callback(user_id, "cat:device", owner_id, "mac-a")

    async def filter_callback(user: int, callback: str):
        class Callback:
            data = callback
            from_user = SimpleNamespace(id=user)
        return await AdminFilter()(Callback())

    assert asyncio.run(filter_callback(user_id, "cmd:capabilities")) is True
    assert asyncio.run(filter_callback(user_id, "cmd:screenshot")) is False
    assert bot.access_store.toggle_callback(owner_id, user_id, "cmd:capabilities", owner_id) is False
    assert "cmd:capabilities" not in _callback_data(bot.device_menu("mac-a", user_id))
    assert asyncio.run(filter_callback(user_id, "cmd:capabilities")) is False
    monkeypatch.setattr(bot, "SESSION", {"target": "pc-b"})
    assert asyncio.run(filter_callback(user_id, "cmd:capabilities")) is False
    assert "cmd:capabilities" not in _callback_data(bot.device_menu("pc-b", user_id))
    assert "cat:pranks" not in _callback_data(bot.device_menu("pc-b", user_id))

    monkeypatch.setattr(bot, "SESSION", {"target": "mac-a"})
    assert "cat:device" in _callback_data(bot.pranks_menu(user_id=user_id, target="mac-a"))
    assert "cmd:autorun_status" in _callback_data(bot.device_settings_menu(user_id))
    assert _callback_data(bot.main_menu(guest_id)) == [
        "menu:guest_devices", "menu:server", "catalog:areas", "menu:about",
    ]
    guest_callbacks = _callback_data(bot.guest_devices_menu())
    assert guest_callbacks[-1] == "menu:main"
    assert set(guest_callbacks) == {"guest:readonly", "menu:main"}
    assert asyncio.run(filter_callback(guest_id, "cmd:capabilities")) is False
    assert bot.access_store.set_blocked(owner_id, user_id, True, owner_id)
    assert _callback_data(bot.main_menu(user_id)) == []
    assert asyncio.run(filter_callback(user_id, "cmd:capabilities")) is False
    assert asyncio.run(filter_callback(owner_id, "cmd:capabilities")) is True


def test_feature_catalog_uses_readonly_page_and_edits_existing_card(monkeypatch):
    from types import SimpleNamespace

    _setup(monkeypatch, {})
    monkeypatch.setattr(bot, "get_user_role", lambda _user: bot.Role.OWNER)
    calls = []

    class Callback:
        data = "catalog:areas"
        from_user = SimpleNamespace(id=100)

        async def answer(self, *args, **kwargs):
            calls.append(("answer", args, kwargs))

    async def replace(cq, text, *, reply_markup=None, **_kwargs):
        calls.append(("edit", text, _callback_data(reply_markup)))

    monkeypatch.setattr(bot, "_replace_callback_message", replace)

    asyncio.run(bot.on_feature_catalog_areas(Callback()))

    edit = next(call for call in calls if call[0] == "edit")
    assert "17 разделов" in edit[1]
    assert len(edit[2]) == 24
    assert "catalog:search:exact" in edit[2]
    assert "catalog:search:outcome" in edit[2]
    assert "catalog:search:fuzzy" in edit[2]
    assert "catalog:filter:result" in edit[2]
    assert "catalog:filter:permission" in edit[2]
    assert "catalog:filter:access" in edit[2]
    assert "catalog:area:B01:0" in edit[2]
    assert all(callback.startswith(("catalog:", "menu:")) for callback in edit[2])


def test_feature_catalog_result_type_filter_is_registry_backed_and_role_filtered(monkeypatch):
    _setup(monkeypatch, {})
    monkeypatch.setattr(bot, "get_user_role", lambda _user_id: bot.Role.OWNER)
    monkeypatch.setattr(bot.access_store, "get_effective_grants", lambda _user_id: {"callbacks": []})

    type_callbacks = _callback_data(bot.feature_catalog_result_type_keyboard(100))
    body, result_markup = bot.feature_catalog_result_type_page(100, "image")
    result_callbacks = _callback_data(result_markup)

    assert set(type_callbacks) >= {
        "catalog:filter:result:text", "catalog:filter:result:image",
        "catalog:filter:result:file", "catalog:filter:result:status",
    }
    assert "D02-F01" in body and "D02-F06" in body
    assert "D01-F01" not in body
    assert "catalog:feature:D02-F01" in result_callbacks
    assert "catalog:areas" in result_callbacks


def test_feature_catalog_result_type_filter_hides_owner_rows_from_guest(monkeypatch):
    _setup(monkeypatch, {})
    monkeypatch.setattr(bot, "get_user_role", lambda _user_id: bot.Role.GUEST)
    monkeypatch.setattr(bot.access_store, "get_effective_grants", lambda _user_id: {"callbacks": []})

    body, markup = bot.feature_catalog_result_type_page(300, "file")

    assert "D01-F29" not in body
    assert not any(value.startswith("catalog:feature:") for value in _callback_data(markup))


def test_feature_catalog_result_type_filter_does_not_drop_matches_after_twenty(monkeypatch):
    _setup(monkeypatch, {})
    monkeypatch.setattr(bot, "get_user_role", lambda _user_id: bot.Role.OWNER)
    monkeypatch.setattr(bot.access_store, "get_effective_grants", lambda _user_id: {"callbacks": []})

    body, markup = bot.feature_catalog_result_type_page(100, "text")

    assert "B01-F05" in body
    assert "D01-F30" in body
    assert "catalog:feature:B01-F05" in _callback_data(markup)
    assert "catalog:feature:D01-F30" in _callback_data(markup)


def test_feature_catalog_permission_filter_uses_fresh_device_snapshot_and_preserves_rbac(monkeypatch):
    from datetime import datetime, timezone

    checked_at = datetime.now(timezone.utc).isoformat()
    _setup(monkeypatch, {"win1": {
        "name": "PC", "os": "Windows 11", "capabilities": {"snapshot": {
            "schema": 1, "platform": "Windows 11", "checked_at": checked_at,
            "items": [
                {"id": "screen_recording", "state": "denied", "detail": "off"},
                {"id": "camera", "state": "granted", "detail": "on"},
                {"id": "microphone", "state": "granted", "detail": "on"},
                {"id": "location", "state": "unknown", "detail": "unknown"},
                {"id": "accessibility", "state": "unknown", "detail": "unknown"},
                {"id": "file_access", "state": "unknown", "detail": "unknown"},
            ],
        }},
    }})
    monkeypatch.setitem(bot.SESSION, "target", "win1")
    monkeypatch.setattr(bot, "get_user_role", lambda _user_id: bot.Role.OWNER)
    monkeypatch.setattr(bot.access_store, "get_effective_grants", lambda _user_id: {"callbacks": []})
    monkeypatch.setattr(bot.access_store, "can_use_callback", lambda *_args: True)

    body, markup = bot.feature_catalog_permission_page(100)

    callbacks = _callback_data(markup)
    assert "D02-F11" in body and "catalog:feature:D02-F11" in callbacks
    assert "D02-F01" not in body and "D02-F15" in body
    assert "только по свежей проверке" in body


def test_feature_catalog_permission_filter_does_not_treat_stale_snapshot_as_granted(monkeypatch):
    _setup(monkeypatch, {"win1": {
        "name": "PC", "os": "Windows 11", "capabilities": {"snapshot": {
            "schema": 1, "platform": "Windows 11", "checked_at": "2000-01-01T00:00:00+00:00",
            "items": [{"id": key, "state": "granted", "detail": "on"} for key in (
                "screen_recording", "camera", "microphone", "location", "accessibility", "file_access",
            )],
        }},
    }})
    monkeypatch.setitem(bot.SESSION, "target", "win1")
    monkeypatch.setattr(bot, "get_user_role", lambda _user_id: bot.Role.OWNER)
    monkeypatch.setattr(bot.access_store, "get_effective_grants", lambda _user_id: {"callbacks": []})
    monkeypatch.setattr(bot.access_store, "can_use_callback", lambda *_args: True)

    body, _markup = bot.feature_catalog_permission_page(100)

    assert "D02-F01" not in body
    assert "снимок устарел" in body


def test_feature_catalog_permission_filter_callback_edits_catalog_card(monkeypatch):
    from types import SimpleNamespace

    _setup(monkeypatch, {})
    monkeypatch.setattr(bot, "_catalog_navigation_state", lambda _user_id: {
        "page_key": "catalog:areas", "page_cursor": 0, "stack": [],
    })
    saved = []
    edits = []
    monkeypatch.setattr(bot, "_save_catalog_navigation", lambda *args: saved.append(args))
    monkeypatch.setattr(bot, "feature_catalog_permission_page", lambda _user_id: (
        "permission results", SimpleNamespace(inline_keyboard=[]),
    ))

    async def replace(_cq, text, *, reply_markup=None):
        edits.append((text, reply_markup))

    monkeypatch.setattr(bot, "_replace_callback_message", replace)

    class Callback:
        from_user = SimpleNamespace(id=100)
        data = "catalog:filter:permission"

        async def answer(self):
            pass

    asyncio.run(bot.on_feature_catalog_permission_filter(Callback()))

    assert len(edits) == 1 and edits[0][0] == "permission results"
    assert saved == [(100, "catalog:filter:permission", 0, [{"page": "catalog:areas", "cursor": 0}])]


def test_feature_catalog_access_filter_uses_current_grants_and_selected_device(monkeypatch):
    _setup(monkeypatch, {"win1": {"name": "PC", "os": "Windows 11"}})
    monkeypatch.setitem(bot.SESSION, "target", "win1")
    monkeypatch.setattr(bot, "get_user_role", lambda _user_id: bot.Role.USER)
    monkeypatch.setattr(bot.access_store, "get_effective_grants", lambda _user_id: {
        "callbacks": ["cmd:status"],
    })
    monkeypatch.setattr(bot.access_store, "can_use_callback", lambda _user_id, callback, *_args: callback == "dev:win1")
    registry = bot.feature_catalog.load_registry()
    registry["features"].extend([
        {"feature_id": "TST-F01", "title": "System status", "role": ["user"],
         "grant": {"kind": "delegable", "key": "cmd:status"}, "platforms": ["windows"],
         "support_state": "needs_device_test"},
        {"feature_id": "TST-F02", "title": "Owner only", "role": ["owner"],
         "grant": {"kind": "owner_only", "key": None}, "platforms": ["windows"],
         "support_state": "implemented"},
        {"feature_id": "MAC-F01", "title": "Mac only", "role": ["user"],
         "grant": {"kind": "delegable", "key": "cmd:status"}, "platforms": ["macos"],
         "support_state": "implemented"},
        {"feature_id": "TST-F03", "title": "Not ready", "role": ["user"],
         "grant": {"kind": "delegable", "key": "cmd:status"}, "platforms": ["windows"],
         "support_state": "planned"},
    ])
    monkeypatch.setattr(bot.feature_catalog, "load_registry", lambda: registry)

    body, markup = bot.feature_catalog_access_page(100)

    assert "TST-F01" in body and "needs_device_test" in body
    assert "TST-F02" not in body and "MAC-F01" not in body and "TST-F03" not in body
    assert "cmd:status" not in body
    assert "catalog:feature:TST-F01" in _callback_data(markup)


def test_feature_catalog_access_filter_requires_selected_authorized_device(monkeypatch):
    _setup(monkeypatch, {"win1": {"name": "PC", "os": "Windows 11"}})
    monkeypatch.setattr(bot, "get_user_role", lambda _user_id: bot.Role.USER)
    monkeypatch.setattr(bot.access_store, "get_effective_grants", lambda _user_id: {"callbacks": ["cmd:status"]})
    monkeypatch.setattr(bot.access_store, "can_use_callback", lambda *_args: False)
    monkeypatch.setitem(bot.SESSION, "target", "win1")

    body, markup = bot.feature_catalog_access_page(100)

    assert "Сначала выбери устройство" in body
    assert not any(value.startswith("catalog:feature:") for value in _callback_data(markup))


def test_feature_catalog_access_filter_callback_edits_catalog_card(monkeypatch):
    from types import SimpleNamespace

    _setup(monkeypatch, {})
    monkeypatch.setattr(bot, "_catalog_navigation_state", lambda _user_id: {
        "page_key": "catalog:areas", "page_cursor": 0, "stack": [],
    })
    saved = []
    edits = []
    monkeypatch.setattr(bot, "_save_catalog_navigation", lambda *args: saved.append(args))
    monkeypatch.setattr(bot, "feature_catalog_access_page", lambda _user_id: (
        "access results", SimpleNamespace(inline_keyboard=[]),
    ))

    async def replace(_cq, text, *, reply_markup=None):
        edits.append((text, reply_markup))

    monkeypatch.setattr(bot, "_replace_callback_message", replace)

    class Callback:
        from_user = SimpleNamespace(id=100)
        data = "catalog:filter:access"

        async def answer(self):
            pass

    asyncio.run(bot.on_feature_catalog_access_filter(Callback()))

    assert len(edits) == 1 and edits[0][0] == "access results"
    assert saved == [(100, "catalog:filter:access", 0, [{"page": "catalog:areas", "cursor": 0}])]


def test_feature_catalog_result_type_callback_edits_one_card(monkeypatch):
    from types import SimpleNamespace

    _setup(monkeypatch, {})
    monkeypatch.setattr(bot, "get_user_role", lambda _user_id: bot.Role.OWNER)
    monkeypatch.setattr(bot.access_store, "get_effective_grants", lambda _user_id: {"callbacks": []})
    edits = []

    async def replace(_cq, text, reply_markup=None):
        edits.append((text, _callback_data(reply_markup)))

    monkeypatch.setattr(bot, "_replace_callback_message", replace)

    class Callback:
        from_user = SimpleNamespace(id=100)
        def __init__(self, data):
            self.data = data
        async def answer(self, *args, **kwargs):
            pass

    asyncio.run(bot.on_feature_catalog_result_filter_start(Callback("catalog:filter:result")))
    asyncio.run(bot.on_feature_catalog_result_filter_select(Callback("catalog:filter:result:image")))

    assert len(edits) == 2
    assert set(edits[0][1]) >= {
        "catalog:filter:result:text", "catalog:filter:result:image",
        "catalog:filter:result:file", "catalog:filter:result:status",
    }
    assert "D02-F01" in edits[1][0]
    assert "catalog:feature:D02-F01" in edits[1][1]


def test_feature_catalog_area_menu_uses_registry_route_metadata(monkeypatch):
    _setup(monkeypatch, {})
    monkeypatch.setattr(bot, "get_user_role", lambda _user: bot.Role.OWNER)
    registry = bot.feature_catalog.load_registry()
    exact = next(row for row in registry["navigation"] if row["page_id"] == "catalog.search.exact")
    exact["callback"] = "catalog:search:exact:registry-test"
    fuzzy = next(row for row in registry["navigation"] if row["page_id"] == "catalog.search.fuzzy")
    fuzzy["callback"] = "catalog:search:fuzzy:registry-test"
    monkeypatch.setattr(bot.feature_catalog, "load_registry", lambda: registry)

    callbacks = _callback_data(bot.feature_catalog_areas_menu(100))

    assert "catalog:search:exact:registry-test" in callbacks
    assert "catalog:search:exact" not in callbacks
    assert "catalog:search:fuzzy:registry-test" in callbacks


def test_feature_catalog_page_controls_use_registry_route_metadata(monkeypatch):
    _setup(monkeypatch, {})
    monkeypatch.setattr(bot, "get_user_role", lambda _user: bot.Role.OWNER)
    registry = bot.feature_catalog.load_registry()
    next_page = next(row for row in registry["navigation"] if row["page_id"] == "catalog.page.next")
    next_page["callback"] = "catalog:search:exact"
    monkeypatch.setattr(bot.feature_catalog, "load_registry", lambda: registry)

    _body, markup = bot.feature_catalog_area_page(100, "B01", 0)

    assert "catalog:search:exact" in _callback_data(markup)
    assert "catalog:area:B01:1" not in _callback_data(markup)


def test_feature_catalog_area_page_filters_to_selected_device_platform(monkeypatch):
    _setup(monkeypatch, {"mac-a": {"name": "MacBook", "os": "macOS 15.1"}})
    monkeypatch.setattr(bot, "get_user_role", lambda _user_id: bot.Role.OWNER)
    monkeypatch.setattr(bot.access_store, "can_use_callback", lambda *_args: True)
    monkeypatch.setattr(bot, "SESSION", {"target": "mac-a"})

    body, markup = bot.feature_catalog_area_page(100, "D01", 0)

    registry = bot.feature_catalog.load_registry()
    expected = [
        row["feature_id"] for row in registry["features"]
        if row.get("area") == "D01" and "macos" in row.get("platforms", [])
    ][:8]
    assert all(feature_id in body for feature_id in expected)
    assert "Фильтр платформы: <code>macos</code>" in body
    assert all(feature_id not in body for feature_id in (
        row["feature_id"] for row in registry["features"]
        if row.get("area") == "D01" and "macos" not in row.get("platforms", [])
    ))
    assert all(f"catalog:feature:{feature_id}" in _callback_data(markup) for feature_id in expected)


def test_feature_catalog_detail_controls_use_registry_route_metadata(monkeypatch):
    _setup(monkeypatch, {})
    monkeypatch.setattr(bot, "get_user_role", lambda _user: bot.Role.OWNER)
    registry = bot.feature_catalog.load_registry()
    back = next(row for row in registry["navigation"] if row["page_id"] == "catalog.feature.back")
    back["callback"] = "catalog:back:registry-test"
    monkeypatch.setattr(bot.feature_catalog, "load_registry", lambda: registry)

    callbacks = _callback_data(bot.feature_catalog_detail_keyboard(100, "B01-F03"))

    assert callbacks == [
        "catalog:back:registry-test", "menu:main", "catalog:synonym:add:B01-F03",
    ]


def test_owner_catalog_synonym_flow_edits_one_card_and_only_changes_exact_search(
    monkeypatch, tmp_path,
):
    from types import SimpleNamespace
    from device_store import DeviceStore

    monkeypatch.setattr(DeviceStore, "FILE", tmp_path / "devices.json")
    monkeypatch.setattr(DeviceStore, "CATALOG_FILE", tmp_path / "catalog.json")
    store = DeviceStore()
    monkeypatch.setattr(bot, "devices", store)
    monkeypatch.setattr(bot, "_visible_device_items", lambda _user: store.all())
    monkeypatch.setattr(bot, "ADMIN_ID", 100)
    monkeypatch.setattr(
        bot, "get_user_role",
        lambda user_id: bot.Role.OWNER if user_id == 100 else bot.Role.USER,
    )
    monkeypatch.setattr(bot.access_store, "get_effective_grants", lambda _user: {"callbacks": []})
    edits = []
    audit = []
    monkeypatch.setattr(
        bot, "_replace_callback_message",
        lambda _cq, text, reply_markup=None, **_kw: edits.append((text, reply_markup)) or asyncio.sleep(0),
    )
    monkeypatch.setattr(
        bot, "_replace_user_card",
        lambda _message, text, reply_markup=None, **_kw: edits.append((text, reply_markup)) or asyncio.sleep(0),
    )
    monkeypatch.setattr(bot, "audit", lambda event, **fields: audit.append((event, fields)))

    class FakeState:
        def __init__(self):
            self.data = {}
            self.state = None
        async def set_state(self, value):
            self.state = value
        async def update_data(self, **values):
            self.data.update(values)
        async def get_data(self):
            return dict(self.data)
        async def clear(self):
            self.data.clear()
            self.state = None

    class Callback:
        from_user = SimpleNamespace(id=100)
        message = SimpleNamespace(chat=SimpleNamespace(id=100), from_user=SimpleNamespace(id=100))
        data = "catalog:synonym:add:B01-F03"
        async def answer(self, *args, **kwargs):
            pass

    class Message:
        from_user = SimpleNamespace(id=100)
        chat = SimpleNamespace(id=100)
        def __init__(self, text):
            self.text = text

    async def scenario():
        state = FakeState()
        await bot.on_feature_catalog_synonym_add(Callback(), state)
        assert state.state == bot.Form.wait_catalog_synonym
        assert state.data["catalog_synonym_feature_id"] == "B01-F03"
        await bot.on_feature_catalog_synonym_input(Message("мой быстрый поиск"), state)
        assert state.state is None
        assert store.get_feature_search_synonyms(owner_id=100) == {
            "B01-F03": ["мой быстрый поиск"],
        }
        registry = bot.feature_catalog.load_registry()
        assert [row["feature_id"] for row in bot.feature_catalog.search_features(
            registry, "мой быстрый поиск", mode="exact",
            synonyms=store.get_feature_search_synonyms(owner_id=100),
        )] == ["B01-F03"]
        assert bot.feature_catalog.search_features(
            registry, "мой быстрый поиск", mode="outcome",
            synonyms=store.get_feature_search_synonyms(owner_id=100),
        ) == []
        await bot.on_feature_catalog_synonym_add(Callback(), state)
        await bot.on_feature_catalog_synonym_input(Message("- МОЙ БЫСТРЫЙ ПОИСК"), state)
        assert store.get_feature_search_synonyms(owner_id=100) == {}

    asyncio.run(scenario())

    assert len(edits) == 4  # prompt/add prompt/remove all replace the same Telegram card
    assert "мой быстрый поиск" in edits[1][0]  # only the owner card shows the saved list
    assert all("мой быстрый поиск" not in str(fields) for _event, fields in audit)
    owner_callbacks = _callback_data(bot.feature_catalog_detail_keyboard(100, "B01-F03"))
    user_callbacks = _callback_data(bot.feature_catalog_detail_keyboard(200, "B01-F03"))
    assert "catalog:synonym:add:B01-F03" in owner_callbacks
    assert "catalog:synonym:add:B01-F03" not in user_callbacks
    routes = _callback_routes("catalog:synonym:add:B01-F03")
    assert len(routes) == 1
    _assert_expected_callback_gate("catalog:synonym:add:B01-F03", routes[0])


def test_feature_catalog_search_controls_use_registry_route_metadata(monkeypatch):
    _setup(monkeypatch, {})
    monkeypatch.setattr(bot, "get_user_role", lambda _user: bot.Role.OWNER)
    registry = bot.feature_catalog.load_registry()
    sections = next(
        row for row in registry["navigation"]
        if row["page_id"] == "catalog.search.prompt.sections"
    )
    sections["callback"] = "catalog:search:cancel:registry-test"
    monkeypatch.setattr(bot.feature_catalog, "load_registry", lambda: registry)

    prompt = _callback_data(bot.feature_catalog_search_keyboard(100, parent="catalog.search.prompt"))
    results = _callback_data(bot.feature_catalog_search_keyboard(100, parent="catalog.search.results"))

    assert prompt == ["catalog:search:cancel:registry-test", "menu:main"]
    assert results == ["catalog:areas", "menu:main"]


def test_feature_catalog_search_keyboard_rechecks_each_result_grant(monkeypatch):
    _setup(monkeypatch, {})
    monkeypatch.setattr(bot, "get_user_role", lambda _user: bot.Role.USER)
    monkeypatch.setattr(bot.access_store, "get_effective_grants", lambda _user: {"callbacks": []})
    registry = bot.feature_catalog.load_registry()
    owner_only = next(row for row in registry["features"] if row["feature_id"] == "B01-F01")
    monkeypatch.setattr(bot.feature_catalog, "load_registry", lambda: registry)

    markup = bot.feature_catalog_search_keyboard(
        200, parent="catalog.search.results", features=[owner_only],
    )

    assert not any(
        button.callback_data == "catalog:feature:B01-F01"
        for row in markup.inline_keyboard
        for button in row
    )


def test_feature_catalog_persists_area_page_and_back_stack(monkeypatch, tmp_path):
    from types import SimpleNamespace
    from device_store import DeviceStore

    monkeypatch.setattr(DeviceStore, "FILE", tmp_path / "devices.json")
    monkeypatch.setattr(DeviceStore, "CATALOG_FILE", tmp_path / "catalog.json")
    store = DeviceStore()
    monkeypatch.setattr(bot, "devices", store)
    monkeypatch.setattr(bot, "ADMIN_ID", 100)
    monkeypatch.setattr(bot, "get_user_role", lambda _user: bot.Role.OWNER)
    async def replace(*args, **kwargs):
        pass
    monkeypatch.setattr(bot, "_replace_callback_message", replace)

    class Callback:
        from_user = SimpleNamespace(id=100)
        def __init__(self, data):
            self.data = data
        async def answer(self, *args, **kwargs):
            pass

    async def scenario():
        await bot.on_feature_catalog_areas(Callback("catalog:areas"))
        await bot.on_feature_catalog_area(Callback("catalog:area:B01:2"))

    asyncio.run(scenario())
    saved = store.get_navigation_state(100)
    assert saved["page_key"] == "catalog:area:B01"
    assert saved["page_cursor"] == 2
    assert saved["stack"] == [{"page": "catalog:areas", "cursor": 0}]


def test_feature_catalog_detail_back_restores_previous_page_cursor(monkeypatch, tmp_path):
    from types import SimpleNamespace
    from device_store import DeviceStore

    monkeypatch.setattr(DeviceStore, "FILE", tmp_path / "devices.json")
    monkeypatch.setattr(DeviceStore, "CATALOG_FILE", tmp_path / "catalog.json")
    store = DeviceStore()
    monkeypatch.setattr(bot, "devices", store)
    monkeypatch.setattr(bot, "get_user_role", lambda _user: bot.Role.OWNER)
    edits = []
    async def replace(_cq, body, reply_markup=None):
        edits.append((body, _callback_data(reply_markup)))
    monkeypatch.setattr(bot, "_replace_callback_message", replace)

    class Callback:
        from_user = SimpleNamespace(id=101)
        def __init__(self, data):
            self.data = data
        async def answer(self, *args, **kwargs):
            pass

    async def scenario():
        await bot.on_feature_catalog_areas(Callback("catalog:areas"))
        await bot.on_feature_catalog_area(Callback("catalog:area:B01:2"))
        await bot.on_feature_catalog_detail(Callback("catalog:feature:B01-F18"))
        await bot.on_feature_catalog_back(Callback("catalog:back"))

    asyncio.run(scenario())
    saved = store.get_navigation_state(101)
    assert saved["page_key"] == "catalog:area:B01"
    assert saved["page_cursor"] == 2
    assert saved["stack"] == [{"page": "catalog:areas", "cursor": 0}]
    assert "catalog:area:B01:1" in edits[-1][1]
    assert "catalog:feature:B01-F18" in edits[-1][1]


def test_catalog_does_not_restore_stale_device_from_global_session(monkeypatch, tmp_path):
    from device_store import DeviceStore

    monkeypatch.setattr(DeviceStore, "FILE", tmp_path / "devices.json")
    monkeypatch.setattr(DeviceStore, "CATALOG_FILE", tmp_path / "catalog.json")
    store = DeviceStore()
    store.upsert("mac-a", {"name": "Mac"})
    store.save_navigation_state(102, "mac-a", "catalog:area:B01", 0, [])
    store.remove("mac-a")
    monkeypatch.setattr(bot, "devices", store)
    monkeypatch.setattr(bot, "SESSION", {"target": "mac-a"})
    monkeypatch.setattr(bot, "get_user_role", lambda _user: bot.Role.OWNER)

    assert bot._catalog_selected_device(102) is None


def test_feature_catalog_detail_resolves_last_row_beyond_first_page(monkeypatch):
    monkeypatch.setattr(bot, "get_user_role", lambda _user: bot.Role.OWNER)
    monkeypatch.setattr(bot, "_catalog_selected_device", lambda _user: None)
    monkeypatch.setattr(bot, "_visible_device_items", lambda _user: {})
    monkeypatch.setattr(bot.access_store, "get_effective_grants", lambda _user: {"callbacks": []})

    detail = bot.feature_catalog_detail(100, "B01-F30")

    assert detail is not None
    assert "B01-F30" in detail


def test_long_feature_button_prioritizes_human_title_over_metadata(monkeypatch):
    monkeypatch.setattr(bot, "get_user_role", lambda _user: bot.Role.OWNER)
    monkeypatch.setattr(bot, "_visible_device_items", lambda _user: {})
    monkeypatch.setattr(bot.access_store, "get_effective_grants", lambda _user: {"callbacks": []})

    title = next(
        item["title"] for item in bot.feature_catalog.load_registry()["features"]
        if item["feature_id"] == "A01-F17"
    )
    _body, markup = bot.feature_catalog_area_page(100, "A01", 2)
    target = next(
        button for row in markup.inline_keyboard for button in row
        if button.callback_data == "catalog:feature:A01-F17"
    )

    assert target.text.startswith(title)
    assert len(target.text.encode("utf-16-le")) // 2 <= 64


def test_device_catalog_search_matches_name_id_and_hostname_only_in_visible_catalog():
    visible = {
        "mac-7a": {"name": "Studio Mac", "hostname": "studio-air.local"},
        "pc-2b": {"name": "Office PC", "hostname": "workstation-2"},
    }

    assert list(bot.search_visible_devices("studio", visible)) == ["mac-7a"]
    assert list(bot.search_visible_devices("PC-2B", visible)) == ["pc-2b"]
    assert list(bot.search_visible_devices("STUDIO-AIR.LOCAL", visible)) == ["mac-7a"]
    assert bot.search_visible_devices("hidden-device", visible) == {}


def test_device_catalog_os_filter_matches_only_reported_platform_and_version():
    visible = {
        "mac-new": {"os": "macOS 15.2"},
        "mac-old": {"os": "macOS 14.6"},
        "win": {"os": "Windows 11"},
        "darwin": {"os": "Darwin 23"},
        "linux": {"os": "Linux 6.8"},
    }

    assert list(bot.filter_visible_devices_by_platform_version("Windows", None, visible)) == ["win"]
    assert list(bot.filter_visible_devices_by_platform_version("macOS", "15.2", visible)) == ["mac-new"]
    assert bot.filter_visible_devices_by_platform_version("Windows", "10", visible) == {}
    assert "darwin" not in bot.filter_visible_devices_by_platform_version("Windows", None, visible)
    assert bot.filter_visible_devices_by_platform_version("Linux", None, visible) == {}


def test_device_catalog_os_filter_entry_is_owner_only(monkeypatch):
    _setup(monkeypatch, {"device-1": {"os": "Windows 11"}})
    roles = {100: bot.Role.OWNER, 200: bot.Role.USER, 300: bot.Role.GUEST, 400: bot.Role.BLOCKED}
    monkeypatch.setattr(bot, "get_user_role", lambda user_id: roles[user_id])
    monkeypatch.setattr(bot.access_store, "get_effective_grants", lambda _user: {"callbacks": [], "devices": []})
    monkeypatch.setattr(bot.access_store, "can_use_callback", lambda user, *_args, **_kwargs: user == 100)

    menus = {user_id: _callback_data(bot.devices_menu(user_id)) for user_id in roles}
    assert "devices:filter-os" in menus[100]
    assert "devices:filter-os" not in menus[200]
    assert "devices:filter-os" not in menus[300]
    assert "devices:filter-os" not in menus[400]


def test_device_catalog_os_filter_updates_existing_card_and_shows_only_matching_devices(monkeypatch):
    from types import SimpleNamespace

    _setup(monkeypatch, {
        "mac-new": {"name": "New Mac", "os": "macOS 15.2"},
        "mac-old": {"name": "Old Mac", "os": "macOS 14.6"},
        "win": {"name": "Work PC", "os": "Windows 11"},
    })
    monkeypatch.setattr(bot, "get_user_role", lambda _user: bot.Role.OWNER)
    monkeypatch.setattr(bot, "_visible_device_items", lambda _user: {
        "mac-new": {"name": "New Mac", "os": "macOS 15.2"},
        "mac-old": {"name": "Old Mac", "os": "macOS 14.6"},
        "win": {"name": "Work PC", "os": "Windows 11"},
    })
    edits = []

    async def replace(_source, text, reply_markup=None, **_kwargs):
        edits.append((text, reply_markup))

    monkeypatch.setattr(bot, "_replace_callback_message", replace)
    monkeypatch.setattr(bot, "_replace_user_card", replace)

    class State:
        def __init__(self):
            self.current = None
        async def set_state(self, value):
            self.current = value
        async def clear(self):
            self.current = None

    class Callback:
        from_user = SimpleNamespace(id=100)
        async def answer(self, *args, **kwargs):
            pass

    class Message:
        from_user = SimpleNamespace(id=100)
        chat = SimpleNamespace(id=100)
        text = "macOS 15.2"

    async def scenario():
        state = State()
        await bot.on_device_filter_os_start(Callback(), state)
        await bot.on_device_filter_os_input(Message(), state)
        assert state.current is None

    asyncio.run(scenario())
    assert len(edits) == 2
    callbacks = _callback_data(edits[1][1])
    assert "dev:mac-new" in callbacks
    assert "dev:mac-old" not in callbacks
    assert "dev:win" not in callbacks


def test_device_catalog_heartbeat_age_filter_handles_boundaries_and_unknown_times():
    visible = {
        "fresh": {"last_seen": 19700},
        "boundary": {"last_seen": 19700 - 300},
        "stale": {"last_seen": 19700 - 3600},
        "missing": {"last_seen": None},
        "malformed": {"last_seen": "not-a-time"},
        "future": {"last_seen": 19701},
    }

    assert list(bot.filter_visible_devices_by_heartbeat_age("<= 5m", visible, now=19700)) == [
        "fresh", "boundary",
    ]
    assert list(bot.filter_visible_devices_by_heartbeat_age(">= 1h", visible, now=19700)) == ["stale"]
    assert bot.filter_visible_devices_by_heartbeat_age("<= 0m", visible, now=19700) == {}
    assert bot.filter_visible_devices_by_heartbeat_age(">= 400d", visible, now=19700) == {}


def test_device_catalog_heartbeat_filter_entry_is_owner_only(monkeypatch):
    _setup(monkeypatch, {"device-1": {"last_seen": 19700}})
    roles = {100: bot.Role.OWNER, 200: bot.Role.USER, 300: bot.Role.GUEST, 400: bot.Role.BLOCKED}
    monkeypatch.setattr(bot, "get_user_role", lambda user_id: roles[user_id])
    monkeypatch.setattr(bot.access_store, "get_effective_grants", lambda _user: {"callbacks": [], "devices": []})
    monkeypatch.setattr(bot.access_store, "can_use_callback", lambda user, *_args, **_kwargs: user == 100)

    menus = {user_id: _callback_data(bot.devices_menu(user_id)) for user_id in roles}
    assert "devices:filter-heartbeat" in menus[100]
    assert "devices:filter-heartbeat" not in menus[200]
    assert "devices:filter-heartbeat" not in menus[300]
    assert "devices:filter-heartbeat" not in menus[400]


def test_device_catalog_heartbeat_filter_edits_one_card_with_only_matching_device(monkeypatch):
    from types import SimpleNamespace

    _setup(monkeypatch, {})
    monkeypatch.setattr(bot, "get_user_role", lambda _user: bot.Role.OWNER)
    current = time.time()
    monkeypatch.setattr(bot, "_visible_device_items", lambda _user: {
        "fresh": {"name": "Fresh Mac", "last_seen": current - 60},
        "stale": {"name": "Quiet PC", "last_seen": current - 3600},
        "unknown": {"name": "Unknown PC", "last_seen": None},
    })
    edits = []

    async def replace(_source, text, reply_markup=None, **_kwargs):
        edits.append((text, reply_markup))

    monkeypatch.setattr(bot, "_replace_callback_message", replace)
    monkeypatch.setattr(bot, "_replace_user_card", replace)

    class State:
        def __init__(self):
            self.current = None
        async def set_state(self, value):
            self.current = value
        async def clear(self):
            self.current = None

    class Callback:
        from_user = SimpleNamespace(id=100)
        async def answer(self, *args, **kwargs):
            pass

    class Message:
        from_user = SimpleNamespace(id=100)
        chat = SimpleNamespace(id=100)
        text = ">= 1h"

    async def scenario():
        state = State()
        await bot.on_device_filter_heartbeat_start(Callback(), state)
        await bot.on_device_filter_heartbeat_input(Message(), state)
        assert state.current is None

    asyncio.run(scenario())
    assert len(edits) == 2
    callbacks = _callback_data(edits[1][1])
    assert "dev:stale" in callbacks
    assert "dev:fresh" not in callbacks
    assert "dev:unknown" not in callbacks


def test_device_catalog_component_version_filter_uses_exact_reported_component_version():
    now = 20000
    visible = {
        "agent-match": {"version": "3.3.8", "os": "Windows"},
        "keeper-match": {
            "version": "3.3.8", "os": "Windows",
            "guardian": {"installed": True, "version": "1.2.0", "version_source": "guardian"},
            "guardian_last_seen": now - 10,
        },
        "legacy-worker-version": {
            "version": "3.3.8", "os": "Windows",
            "guardian": {"installed": True, "version": "1.2.0", "version_source": "worker"},
            "guardian_last_seen": now - 10,
        },
        "stale-keeper": {
            "version": "3.3.8", "os": "Windows",
            "guardian": {"installed": True, "version": "1.2.0", "version_source": "guardian"},
            "guardian_last_seen": now - 1000,
        },
        "safari-match": {"version": "3.3.8", "safari": {"version": "0.8.0", "version_source": "safari"}},
    }

    assert list(bot.filter_visible_devices_by_component_version("agent 3.3.8", visible, now=now)) == ["agent-match", "keeper-match", "legacy-worker-version", "stale-keeper", "safari-match"]
    assert list(bot.filter_visible_devices_by_component_version("guardkeeper 1.2.0", visible, now=now)) == ["keeper-match"]
    assert list(bot.filter_visible_devices_by_component_version("safari 0.8.0", visible, now=now)) == ["safari-match"]
    assert bot.filter_visible_devices_by_component_version("guardkeeper 1.2", visible, now=now) == {}
    assert bot.filter_visible_devices_by_component_version("unknown 3.3.8", visible, now=now) == {}


def test_device_catalog_component_version_filter_updates_existing_card(monkeypatch):
    from types import SimpleNamespace

    _setup(monkeypatch, {})
    monkeypatch.setattr(bot, "get_user_role", lambda _user: bot.Role.OWNER)
    now = time.time()
    monkeypatch.setattr(bot, "_visible_device_items", lambda _user: {
        "agent-only": {"name": "Agent Match", "version": "3.3.8", "os": "Windows"},
        "keeper-match": {
            "name": "Keeper Match", "version": "3.3.8", "os": "Windows",
            "guardian": {"installed": True, "version": "1.2.0", "version_source": "guardian"},
            "guardian_last_seen": now - 10,
        },
    })
    edits = []

    async def replace(_source, text, reply_markup=None, **_kwargs):
        edits.append((text, reply_markup))

    monkeypatch.setattr(bot, "_replace_callback_message", replace)
    monkeypatch.setattr(bot, "_replace_user_card", replace)

    class State:
        def __init__(self):
            self.current = None
        async def set_state(self, value):
            self.current = value
        async def clear(self):
            self.current = None

    class Callback:
        from_user = SimpleNamespace(id=100)
        async def answer(self, *args, **kwargs):
            pass

    class Message:
        from_user = SimpleNamespace(id=100)
        chat = SimpleNamespace(id=100)
        text = "guardkeeper 1.2.0"

    async def scenario():
        state = State()
        await bot.on_device_filter_version_start(Callback(), state)
        await bot.on_device_filter_version_input(Message(), state)
        assert state.current is None

    asyncio.run(scenario())
    assert len(edits) == 2
    callbacks = _callback_data(edits[1][1])
    assert "dev:keeper-match" in callbacks
    assert "dev:agent-only" not in callbacks


def test_device_catalog_component_version_filter_entry_is_owner_only(monkeypatch):
    _setup(monkeypatch, {"device-1": {"version": "3.3.8"}})
    roles = {100: bot.Role.OWNER, 200: bot.Role.USER, 300: bot.Role.GUEST, 400: bot.Role.BLOCKED}
    monkeypatch.setattr(bot, "get_user_role", lambda user_id: roles[user_id])
    monkeypatch.setattr(bot.access_store, "get_effective_grants", lambda _user: {"callbacks": [], "devices": []})
    monkeypatch.setattr(bot.access_store, "can_use_callback", lambda user, *_args, **_kwargs: user == 100)

    menus = {user_id: _callback_data(bot.devices_menu(user_id)) for user_id in roles}
    assert "devices:filter-version" in menus[100]
    assert all("devices:filter-version" not in menus[user_id] for user_id in (200, 300, 400))


def test_device_catalog_capability_filter_uses_only_fresh_declarations():
    now = 1_000_000.0
    visible = {
        "fresh": {"capabilities_seen": now - 30, "capabilities": {"commands": ["screenshot", "location"], "features": {"screen_capture": True}}},
        "stale": {"capabilities_seen": now - 901, "capabilities": {"commands": ["screenshot"], "features": {}}},
        "unknown": {"capabilities": {"commands": ["screenshot"], "features": {}}},
        "negative": {"capabilities_seen": now - 30, "capabilities": {"commands": ["shell"], "features": {"screen_capture": False}}},
        "malformed": {"capabilities_seen": now - 30, "capabilities": {"commands": ["ScreenShot"], "features": {"screen_capture": "yes"}}},
    }

    assert list(bot.filter_visible_devices_by_declared_capability("screenshot", visible, now=now)) == ["fresh"]
    assert list(bot.filter_visible_devices_by_declared_capability("screen capture", visible, now=now)) == ["fresh"]
    assert list(bot.filter_visible_devices_by_declared_capability("shell", visible, now=now)) == ["negative"]
    assert bot.filter_visible_devices_by_declared_capability("", visible, now=now) == {}


def test_device_catalog_capability_filter_menu_is_owner_only(monkeypatch):
    _setup(monkeypatch, {"device-1": {"name": "PC"}})
    roles = {100: bot.Role.OWNER, 200: bot.Role.USER, 300: bot.Role.GUEST, 400: bot.Role.BLOCKED}
    monkeypatch.setattr(bot, "get_user_role", lambda user_id: roles[user_id])
    monkeypatch.setattr(bot.access_store, "get_effective_grants", lambda _user: {"callbacks": [], "devices": []})
    monkeypatch.setattr(bot.access_store, "can_use_callback", lambda user, *_args, **_kwargs: user == 100)

    menus = {user_id: _callback_data(bot.devices_menu(user_id)) for user_id in roles}
    assert "devices:filter-capability" in menus[100]
    assert all("devices:filter-capability" not in menus[user_id] for user_id in (200, 300, 400))


def test_device_catalog_capability_filter_updates_existing_card_with_fresh_matches(monkeypatch):
    from types import SimpleNamespace

    _setup(monkeypatch, {})
    monkeypatch.setattr(bot, "get_user_role", lambda _user: bot.Role.OWNER)
    now = time.time()
    monkeypatch.setattr(bot, "_visible_device_items", lambda _user: {
        "fresh": {"name": "Fresh PC", "capabilities_seen": now, "capabilities": {"commands": ["screenshot"], "features": {}}},
        "stale": {"name": "Old Mac", "capabilities_seen": now - 901, "capabilities": {"commands": ["screenshot"], "features": {}}},
    })
    edits = []

    async def replace(_source, text, reply_markup=None, **_kwargs):
        edits.append((text, reply_markup))

    monkeypatch.setattr(bot, "_replace_callback_message", replace)
    monkeypatch.setattr(bot, "_replace_user_card", replace)

    class State:
        async def set_state(self, _value):
            pass
        async def clear(self):
            pass

    class Callback:
        from_user = SimpleNamespace(id=100)
        async def answer(self, *args, **kwargs):
            pass

    class Message:
        from_user = SimpleNamespace(id=100)
        chat = SimpleNamespace(id=100)
        text = "screenshot"

    async def scenario():
        state = State()
        await bot.on_device_filter_capability_start(Callback(), state)
        await bot.on_device_filter_capability_input(Message(), state)

    asyncio.run(scenario())
    assert len(edits) == 2
    callbacks = _callback_data(edits[1][1])
    assert "dev:fresh" in callbacks
    assert "dev:stale" not in callbacks
    assert "do not prove hardware operation" in edits[1][0]


def test_device_catalog_inventory_age_filter_uses_snapshot_time_not_heartbeat():
    now = 2_000_000.0
    visible = {
        "recent-snapshot": {"last_seen": now - 100 * 86_400},
        "old-snapshot": {"last_seen": now},
        "missing-snapshot": {"last_seen": now - 10},
        "future-snapshot": {"last_seen": now - 10},
    }
    inventory_times = {
        "recent-snapshot": now - 3_600,
        "old-snapshot": now - 10 * 86_400,
        "future-snapshot": now + 60,
    }

    assert list(bot.filter_visible_devices_by_inventory_age("<= 2h", visible, inventory_times, now=now)) == ["recent-snapshot"]
    assert list(bot.filter_visible_devices_by_inventory_age(">= 7d", visible, inventory_times, now=now)) == ["old-snapshot"]
    assert bot.filter_visible_devices_by_inventory_age("<= 0h", visible, inventory_times, now=now) == {}
    assert bot.filter_visible_devices_by_inventory_age(">= 400d", visible, inventory_times, now=now) == {}


def test_device_catalog_inventory_age_filter_updates_existing_card(monkeypatch):
    from types import SimpleNamespace

    _setup(monkeypatch, {})
    monkeypatch.setattr(bot, "get_user_role", lambda _user: bot.Role.OWNER)
    now = int(time.time())
    monkeypatch.setattr(bot, "_visible_device_items", lambda _user: {
        "stale": {"name": "Stale inventory", "last_seen": now},
        "fresh": {"name": "Fresh inventory", "last_seen": now - 100 * 86_400},
        "never": {"name": "No inventory", "last_seen": now},
    })
    snapshots = {
        "stale": [{"captured_at": now - 10 * 86_400}],
        "fresh": [{"captured_at": now - 2 * 86_400}],
        "never": [],
    }
    monkeypatch.setattr(bot.devices, "list_hardware_inventory_snapshots", lambda device_id, limit=1: snapshots[device_id], raising=False)
    edits = []

    async def replace(_source, text, reply_markup=None, **_kwargs):
        edits.append((text, reply_markup))

    monkeypatch.setattr(bot, "_replace_callback_message", replace)
    monkeypatch.setattr(bot, "_replace_user_card", replace)

    class State:
        async def set_state(self, _value):
            pass
        async def clear(self):
            pass

    class Callback:
        from_user = SimpleNamespace(id=100)
        async def answer(self, *args, **kwargs):
            pass

    class Message:
        from_user = SimpleNamespace(id=100)
        chat = SimpleNamespace(id=100)
        text = ">= 7d"

    async def scenario():
        state = State()
        await bot.on_device_filter_inventory_age_start(Callback(), state)
        await bot.on_device_filter_inventory_age_input(Message(), state)

    asyncio.run(scenario())
    assert len(edits) == 2
    callbacks = _callback_data(edits[1][1])
    assert "dev:stale" in callbacks
    assert "dev:fresh" not in callbacks
    assert "dev:never" not in callbacks
    assert "heartbeat is not used" in edits[1][0]


def test_device_catalog_search_entry_respects_roles_and_existing_device_visibility(monkeypatch):
    _setup(monkeypatch, {
        "device-1": {"name": "Granted Mac", "os": "macOS", "last_seen": time.time()},
        "device-2": {"name": "Private PC", "os": "Windows", "last_seen": time.time()},
    })
    roles = {
        100: bot.Role.OWNER,
        200: bot.Role.USER,
        300: bot.Role.GUEST,
        400: bot.Role.BLOCKED,
    }
    monkeypatch.setattr(bot, "get_user_role", lambda user_id: roles[user_id])
    monkeypatch.setattr(
        bot.access_store, "get_effective_grants",
        lambda _user: {"callbacks": ["devices:search"], "devices": ["device-1"]},
    )
    monkeypatch.setattr(
        bot.access_store, "can_use_callback",
        lambda user, callback, _owner, _target=None:
            user == 100 or callback in {"devices:search", "dev:device-1"},
    )

    owner = _callback_data(bot.devices_menu(100))
    user = _callback_data(bot.devices_menu(200))
    guest = _callback_data(bot.devices_menu(300))
    blocked = _callback_data(bot.devices_menu(400))

    assert "devices:search" in owner
    assert "devices:search" in user
    assert "devices:search" not in guest
    assert "devices:search" not in blocked
    assert "dev:device-1" in user and "dev:device-2" not in user


def test_device_catalog_search_edits_one_card_and_never_includes_ungranted_matches(monkeypatch):
    from types import SimpleNamespace

    _setup(monkeypatch, {
        "device-visible": {
            "name": "Studio Mac", "hostname": "studio-air.local", "os": "macOS",
        },
        "device-private": {
            "name": "Studio Backup", "hostname": "studio-air-backup.local", "os": "macOS",
        },
    })
    monkeypatch.setattr(bot, "get_user_role", lambda _user: bot.Role.USER)
    monkeypatch.setattr(
        bot.access_store, "get_effective_grants",
        lambda _user: {"callbacks": ["devices:search", "dev:device-visible"], "devices": ["device-visible"]},
    )
    monkeypatch.setattr(
        bot.access_store, "can_use_callback",
        lambda _user, callback, _owner, _target=None:
            callback in {"devices:search", "dev:device-visible"},
    )
    edits = []

    async def replace(_source, text, reply_markup=None, **_kwargs):
        edits.append((text, reply_markup))

    monkeypatch.setattr(bot, "_replace_callback_message", replace)
    monkeypatch.setattr(bot, "_replace_user_card", replace)

    class State:
        def __init__(self):
            self.data = {}
            self.current = None
        async def set_state(self, value):
            self.current = value
        async def update_data(self, **values):
            self.data.update(values)
        async def clear(self):
            self.current = None
            self.data.clear()

    class Callback:
        from_user = SimpleNamespace(id=200)
        data = "devices:search"
        async def answer(self, *args, **kwargs):
            pass

    class Message:
        from_user = SimpleNamespace(id=200)
        chat = SimpleNamespace(id=200)
        text = "studio-air"

    async def scenario():
        state = State()
        await bot.on_device_search_start(Callback(), state)
        await bot.on_device_search_input(Message(), state)
        assert state.current is None

    asyncio.run(scenario())
    assert len(edits) == 2
    labels = [button.text for row in edits[-1][1].inline_keyboard for button in row]
    assert any("Studio Mac" in label for label in labels)
    assert all("Studio Backup" not in label for label in labels)
    assert _callback_data(edits[-1][1]) == ["dev:device-visible", "menu:devices", "menu:main"]


def test_feature_catalog_detail_explains_access_state_and_test_requirements(monkeypatch):
    monkeypatch.setattr(bot, "get_user_role", lambda _user: bot.Role.OWNER)
    monkeypatch.setattr(bot.access_store, "get_effective_grants", lambda _user: {"callbacks": []})

    detail = bot.feature_catalog_detail(100, "B01-F18")

    assert detail is not None
    assert "Роли: owner" in detail
    assert "Подтверждение: <code>owner_only</code>" in detail
    assert "Разрешения: <code>owner_role_or_explicit_grant</code>" in detail
    assert "bot: planned" in detail
    assert "needs_novelty_audit" in detail
    assert "Ссылки на тесты: <code>2</code>" in detail
    assert "Проверки: <code>unit, integration, device, live</code>" in detail
    assert "не команда для запуска" in detail


def test_owner_feature_detail_explains_selected_device_os_and_permission_blockers(monkeypatch):
    monkeypatch.setattr(bot, "get_user_role", lambda _user: bot.Role.OWNER)
    monkeypatch.setattr(bot.access_store, "get_effective_grants", lambda _user: {"callbacks": []})
    monkeypatch.setattr(bot, "_catalog_selected_device", lambda _user: "mac-1")
    monkeypatch.setattr(bot, "_catalog_selected_platform", lambda _user: "windows")
    monkeypatch.setattr(bot, "_catalog_current_permission_states", lambda _user: {"camera": "denied"})
    registry = {
        "features": [{
            "feature_id": "D02-F14", "area": "D02", "title": "Camera quality",
            "description": "Read camera quality", "outcome": "Read camera quality",
            "role": ["owner", "user"],
            "grant": {"kind": "delegable", "key": "camera_capture"},
            "platforms": ["macos"], "permissions": ["camera"],
            "component_min_versions": {}, "acceptance_level": ["unit"],
            "baseline_evidence": [], "test_refs": [], "support_state": "needs_device_test",
            "handler_id": "d02.f14.camera.quality",
        }],
    }
    monkeypatch.setattr(bot.feature_catalog, "load_registry", lambda: registry)

    detail = bot.feature_catalog_detail(100, "D02-F14")

    assert "Диагностика для выбранного устройства" in detail
    assert "ОС не поддерживается" in detail
    assert "camera: denied" in detail


def test_catalog_permission_diagnostic_rejects_snapshot_from_different_platform(monkeypatch):
    checked_at = bot.datetime.datetime.now(bot.datetime.timezone.utc).isoformat()
    snapshot = {
        "schema": 1,
        "platform": "Windows 11",
        "checked_at": checked_at,
        "items": [{"id": "camera", "state": "granted", "detail": "Reported by OS"}],
    }
    monkeypatch.setattr(bot, "_catalog_selected_device", lambda _user: "mac-1")
    monkeypatch.setattr(bot, "_visible_device_items", lambda _user: {
        "mac-1": {"os": "macOS 27.0", "capabilities": {"snapshot": snapshot}},
    })

    assert bot._catalog_current_permission_states(100) == {}


def test_feature_catalog_detail_displays_registry_description(monkeypatch):
    monkeypatch.setattr(bot, "get_user_role", lambda _user: bot.Role.OWNER)
    monkeypatch.setattr(bot.access_store, "get_effective_grants", lambda _user: {"callbacks": []})
    registry = bot.feature_catalog.load_registry()
    feature = next(row for row in registry["features"] if row["feature_id"] == "B01-F18")
    feature["description"] = "Custom indexed feature description"
    feature["outcome"] = "Different outcome field text"
    monkeypatch.setattr(bot.feature_catalog, "load_registry", lambda: registry)

    detail = bot.feature_catalog_detail(100, "B01-F18")

    assert detail is not None
    assert "Custom indexed feature description" in detail
    assert "Different outcome field text" not in detail


@pytest.mark.parametrize(
    ("role", "has_settings_action"),
    [(bot.Role.OWNER, True), (bot.Role.USER, False)],
)
def test_mac_screenshot_command_failure_offers_owner_settings_without_claiming_denial(
    monkeypatch, role, has_settings_action,
):
    monkeypatch.setattr(bot, "get_user_role", lambda _user: role)
    monkeypatch.setattr(bot.devices, "get", lambda _device: {
        "os": "macOS",
        "capabilities": {"commands": ["permission_settings"]},
    })
    result = {"state": "command_failed", "platform": "macOS", "detail": "capture exit 1"}

    help_text = bot._screenshot_failure_help(result)
    markup = bot._screenshot_failure_keyboard(result, target="mac-1", user_id=100)
    callbacks = _callback_data(markup)

    assert "не подтверждает отказ TCC" in help_text
    assert ("permsettings:screen_recording" in callbacks) is has_settings_action


@pytest.mark.parametrize(
    ("mode", "query", "expected"),
    [
        ("exact", "Battery map", "B01-F01"),
        ("outcome", "find the weak cell", "B01-F01"),
        ("fuzzy", "batteri map", "B01-F01"),
    ],
)
def test_feature_catalog_search_is_available_in_bot_and_edits_one_card(
    monkeypatch, mode, query, expected,
):
    from types import SimpleNamespace

    monkeypatch.setattr(bot, "get_user_role", lambda _user: bot.Role.OWNER)
    monkeypatch.setattr(bot, "_visible_device_items", lambda _user: {})
    monkeypatch.setattr(bot.access_store, "get_effective_grants", lambda _user: {"callbacks": []})
    monkeypatch.setattr(bot.feature_catalog, "load_registry", lambda: {
        "areas": [{"area_id": "B01", "description": "Bot"}],
            "navigation": [
                {
                    "page_id": "catalog.search.prompt.sections", "parent": "catalog.search.prompt",
                    "label_id": "catalog.sections", "callback": "catalog:search:cancel",
                    "roles": ["owner", "user", "guest"], "grants": [],
                    "platforms": ["telegram"], "permissions": [], "support_state": "implemented",
                    "style": "primary", "presentation": "catalog_action",
                },
                {
                    "page_id": "catalog.search.prompt.home", "parent": "catalog.search.prompt",
                    "label_id": "navigation.home", "callback": "menu:main",
                    "roles": ["owner", "user", "guest"], "grants": [],
                    "platforms": ["telegram"], "permissions": [], "support_state": "implemented",
                    "style": "primary", "presentation": "nav",
                },
                {
                    "page_id": "catalog.search.results.sections", "parent": "catalog.search.results",
                    "label_id": "catalog.sections", "callback": "catalog:areas",
                    "roles": ["owner", "user", "guest"], "grants": [],
                    "platforms": ["telegram"], "permissions": [], "support_state": "implemented",
                    "style": "primary", "presentation": "catalog_action",
                },
                {
                    "page_id": "catalog.search.results.home", "parent": "catalog.search.results",
                    "label_id": "navigation.home", "callback": "menu:main",
                    "roles": ["owner", "user", "guest"], "grants": [],
                    "platforms": ["telegram"], "permissions": [], "support_state": "implemented",
                    "style": "primary", "presentation": "nav",
                },
                {
                    "page_id": "catalog.search.results.feature", "parent": "catalog.search.results",
                    "label_id": "feature.catalog.result.title", "callback": "catalog:feature:*",
                    "roles": ["owner", "user", "guest"], "grants": [],
                    "platforms": ["telegram"], "permissions": [], "support_state": "implemented",
                    "style": "primary", "presentation": "catalog_feature",
                },
            ],
        "features": [{
            "feature_id": "B01-F01", "area": "B01", "label_id": "feature.b01.f01.title",
            "title": "Battery map", "outcome": "Find the weak cell",
            "platforms": ["telegram"], "component_min_versions": {}, "role": ["owner"],
            "grant": {"kind": "owner_only", "key": None}, "permissions": [],
            "support_state": "planned", "baseline_evidence": [], "handler_id": None,
            "test_refs": [], "acceptance_level": ["unit"],
        }],
    })
    edits = []

    async def replace(_message, text, reply_markup=None, **_kwargs):
        edits.append((text, reply_markup))

    monkeypatch.setattr(bot, "_replace_user_card", replace)
    monkeypatch.setattr(bot, "_replace_callback_message", replace)

    class FakeState:
        def __init__(self):
            self.data = {}
            self.state = None
        async def set_state(self, value):
            self.state = value
        async def update_data(self, **values):
            self.data.update(values)
        async def get_data(self):
            return dict(self.data)
        async def clear(self):
            self.data.clear()
            self.state = None

    class Callback:
        from_user = SimpleNamespace(id=100)
        message = SimpleNamespace(chat=SimpleNamespace(id=100), from_user=SimpleNamespace(id=100))
        data = f"catalog:search:{mode}"
        async def answer(self, *args, **kwargs):
            pass

    class Message:
        from_user = SimpleNamespace(id=100)
        chat = SimpleNamespace(id=100)
        text = query

    async def scenario():
        state = FakeState()
        await bot.on_feature_catalog_search_start(Callback(), state)
        await bot.on_feature_catalog_search_input(Message(), state)
        assert state.state is None

    asyncio.run(scenario())
    assert len(edits) == 2
    assert expected in edits[-1][0]
    assert "Battery map" in edits[-1][0]
    assert "planned" in edits[-1][0]
    result_callback = f"catalog:feature:{expected}"
    assert result_callback in _callback_data(edits[-1][1])
    result_button = next(
        button
        for row in edits[-1][1].inline_keyboard
        for button in row
        if button.callback_data == result_callback
    )
    assert result_button.text == "Battery map"


def test_feature_catalog_search_cancel_has_exactly_one_route():
    routes = _callback_routes("catalog:search:cancel")

    assert len(routes) == 1
    assert routes[0].callback.__name__ == "on_feature_catalog_search_cancel"


def test_non_owner_action_screens_require_the_current_device_action_grant(monkeypatch):
    _setup(monkeypatch, {
        "device-1": {"name": "Granted Mac", "os": "macOS", "favorites": ["screenshot"]},
        "device-2": {"name": "Other PC", "os": "Windows"},
    })
    monkeypatch.setattr(bot, "SESSION", {"target": "device-1"})
    monkeypatch.setattr(
        bot, "get_user_role",
        lambda user_id: bot.Role.OWNER if user_id == 100 else
        bot.Role.GUEST if user_id == 300 else bot.Role.BLOCKED if user_id == 400 else bot.Role.USER,
    )
    grants = {"cmd:screenshot", "cat:media", "back:device", "menu:main"}
    grant_devices = {"device-1"}

    def can_use(user_id, callback, _owner_id, target=None):
        if user_id == 100:
            return True
        return (
            user_id == 200
            and target in grant_devices
            and (callback in grants or callback == "dev:device-1")
        )

    monkeypatch.setattr(bot.access_store, "can_use_callback", can_use)

    media = set(_callback_data(bot.media_menu(200)))
    assert "cmd:screenshot" in media
    assert "cmd:webcam" not in media
    assert "mic:opts" not in media
    assert "cmd:sound" not in media
    assert "dev:device-1" in _callback_data(bot.devices_menu(200))
    assert "dev:device-2" not in _callback_data(bot.devices_menu(200))

    # Revoking the action hides it on the next render, even while the selected
    # device remains the same. A different target cannot inherit the grant.
    grants.clear()
    assert "cmd:screenshot" not in _callback_data(bot.media_menu(200))
    monkeypatch.setattr(bot, "SESSION", {"target": "device-2"})
    grants.add("cmd:screenshot")
    assert "cmd:screenshot" not in _callback_data(bot.media_menu(200))
    assert not any(
        button.callback_data.startswith("cmd:")
        for row in bot.media_menu(300).inline_keyboard for button in row
    )
    assert not any(
        button.callback_data.startswith("cmd:")
        for row in bot.media_menu(400).inline_keyboard for button in row
    )


def test_nightlight_is_hidden_and_direct_callback_rejected_on_macos(monkeypatch):
    from types import SimpleNamespace

    _setup(monkeypatch, {"mac1": {"name": "Mac", "os": "macOS 27"}})
    monkeypatch.setattr(bot, "SESSION", {"target": "mac1"})
    monkeypatch.setattr(bot, "get_user_role", lambda _user_id: bot.Role.OWNER)
    published = []
    answers = []

    class FakeCollector:
        async def wait_for(self, *args, **kwargs):
            return {"ok": True, "text": "unexpected"}

    class FakeCallback:
        data = "cmd:nightlight"
        from_user = SimpleNamespace(id=bot.ADMIN_ID)

        async def answer(self, *args, **kwargs):
            answers.append((args, kwargs))

    monkeypatch.setattr(
        bot, "publish_tracked",
        lambda *args, **kwargs: published.append((args, kwargs)) or (True, "op-1"),
    )
    monkeypatch.setattr(bot.fun_text_collector, "wait_for", FakeCollector().wait_for)
    monkeypatch.setattr(bot, "_replace_callback_message", lambda *args, **kwargs: asyncio.sleep(0))
    assert "cmd:nightlight" not in _callback_data(bot.screen_menu("mac1"))

    asyncio.run(bot.on_cmd_nightlight(FakeCallback()))

    assert published == []
    assert answers


def test_granted_user_shell_confirm_cannot_publish_or_approve(monkeypatch):
    from types import SimpleNamespace
    from roles import AdminFilter, OwnerFilter
    import roles

    _setup(monkeypatch, {"device-1": {"name": "PC", "os": "Windows"}})
    monkeypatch.setattr(bot, "SESSION", {"target": "device-1"})
    monkeypatch.setattr(roles, "get_user_role", lambda user_id: bot.Role.USER if user_id == 200 else bot.Role.OWNER)
    monkeypatch.setattr(
        bot.access_store, "can_use_callback",
        lambda user_id, callback, _owner_id, target=None:
            user_id == 200 and target == "device-1" and callback == "cfm:shell",
    )
    published = []
    rendered = []

    class FakeCallback:
        data = "cfm:shell"
        from_user = SimpleNamespace(id=200)

        async def answer(self, *args, **kwargs):
            pass

    async def replace(_cq, text, reply_markup=None, **_kwargs):
        rendered.append((text, reply_markup))

    monkeypatch.setattr(bot, "publish_tracked", lambda *args, **kwargs: published.append((args, kwargs)))
    monkeypatch.setattr(bot, "_replace_callback_message", replace)

    callback = FakeCallback()
    assert asyncio.run(AdminFilter()(callback)) is True
    asyncio.run(bot.on_confirm_action(callback))
    assert "shell:ok" not in _callback_data(rendered[-1][1])
    assert asyncio.run(OwnerFilter()(callback)) is False

    callback.data = "shell:ok"
    assert asyncio.run(OwnerFilter()(callback)) is False
    assert published == []


def test_manual_install_is_owner_only_and_uses_published_one_line_commands(monkeypatch):
    from types import SimpleNamespace

    _setup(monkeypatch, {})
    monkeypatch.setattr(
        bot, "get_user_role",
        lambda user_id: bot.Role.OWNER if user_id == 100 else bot.Role.USER,
    )
    monkeypatch.setattr(
        bot.access_store, "can_use_callback",
        lambda user_id, _callback, _owner_id, _target=None: user_id == 100,
    )
    assert "install:manual" in _callback_data(bot.devices_menu(100))
    assert "install:manual" not in _callback_data(bot.devices_menu(200))

    expected = {
        "windows": "irm https://invinby.github.io/XIDER/win.txt | iex",
        "macos": "curl -fsSL https://invinby.github.io/XIDER/mac | bash",
    }
    for selected_os, command in expected.items():
        screen = bot.manual_install_command_screen(selected_os)
        assert f"<code>{command}</code>" in screen
        assert bot._lex("manual_install_copy_note") in screen
        buttons = bot.manual_install_os_keyboard(user_id=100, selected_os=selected_os)
        callbacks = _callback_data(buttons)
        assert "menu:devices" in callbacks
        assert f"install:{selected_os}" not in callbacks
    with pytest.raises(ValueError, match="Unsupported XIDER installer OS"):
        bot.manual_install_command_screen("linux")


def test_manual_install_os_picker_uses_owner_only_registry_navigation(monkeypatch):
    _setup(monkeypatch, {})
    monkeypatch.setattr(
        bot, "get_user_role",
        lambda user_id: bot.Role.OWNER if user_id == 100 else bot.Role.USER,
    )
    monkeypatch.setattr(bot.access_store, "get_effective_grants", lambda _user_id: {"callbacks": []})
    original = bot.feature_catalog.visible_navigation_pages
    calls = []

    def observe(registry, *, parent, role, grants, platform=None, state=None):
        calls.append((parent, role, platform))
        return original(
            registry, parent=parent, role=role, grants=grants, platform=platform, state=state,
        )

    monkeypatch.setattr(bot.feature_catalog, "visible_navigation_pages", observe)

    windows_markup = bot.manual_install_os_keyboard(user_id=100, selected_os="windows")
    guest_markup = bot.manual_install_os_keyboard(user_id=200)
    windows_callbacks = _callback_data(windows_markup)

    assert calls[0] == ("install.os", "owner", "telegram")
    assert windows_callbacks == ["install:macos", "menu:devices"]
    assert _callback_data(guest_markup) == []


def test_devices_menu_uses_registry_actions_and_keeps_owner_controls_private(monkeypatch):
    _setup(monkeypatch, {})
    monkeypatch.setattr(bot, "ADMIN_ID", 100)
    monkeypatch.setattr(
        bot, "get_user_role",
        lambda user_id: bot.Role.OWNER if user_id == 100 else bot.Role.USER,
    )
    monkeypatch.setattr(
        bot, "_visible_device_items",
        lambda _user_id: {"dev-a": {"name": "Mac", "os": "macOS", "online": True}},
    )
    monkeypatch.setattr(
        bot.access_store, "can_use_callback",
        lambda user_id, callback, _owner_id, target=None: (
            user_id == 100
            or (user_id == 200 and callback in {"devices:search", "dev:dev-a", "menu:devices", "menu:main"})
        ),
    )
    original = bot.feature_catalog.visible_navigation_pages
    seen = []

    def observe(registry, *, parent, role, grants, platform=None, state=None):
        if parent == "devices":
            seen.append((role, platform))
        return original(registry, parent=parent, role=role, grants=grants, platform=platform, state=state)

    monkeypatch.setattr(bot.feature_catalog, "visible_navigation_pages", observe)

    owner_markup = bot.devices_menu(100)
    user_markup = bot.devices_menu(200)
    owner_callbacks = _callback_data(owner_markup)
    user_callbacks = _callback_data(user_markup)
    owner_buttons = {
        button.callback_data: button
        for row in owner_markup.inline_keyboard
        for button in row
    }

    assert seen == [("owner", "telegram"), ("user", "telegram")]
    assert owner_callbacks == [
        "dev:dev-a", "devices:search", "devices:filter-os", "devices:filter-heartbeat", "devices:filter-version", "devices:filter-capability", "devices:filter-inventory-age", "catalog:snapshot:list", "catalog:collection:list", "catalog:saved-filter:list", "devices:sort", "catalog:order:edit", "catalog:pinned:list", "catalog:inactivity:list", "catalog:export", "catalog:import", "catalog:archive:list", "dev:all",
        "devmg:manualadd", "install:manual", "devmg:blocked", "devmg:clear_all",
        "menu:devices", "menu:main",
    ]
    assert user_callbacks == ["dev:dev-a", "devices:search", "menu:devices", "menu:main"]
    assert owner_buttons["install:manual"].style == "success"
    assert owner_buttons["devmg:blocked"].style == "danger"
    assert owner_buttons["devmg:clear_all"].style == "danger"


def test_all_devices_menu_uses_owner_only_registry_actions(monkeypatch):
    _setup(monkeypatch, {})
    monkeypatch.setattr(bot, "get_user_role", lambda _user_id: bot.Role.OWNER)
    monkeypatch.setattr(bot.access_store, "get_effective_grants", lambda _user_id: {"callbacks": set()})
    monkeypatch.setattr(bot.access_store, "can_use_callback", lambda *_args, **_kwargs: True)
    calls = []
    original = bot.feature_catalog.visible_navigation_pages

    def observe(registry, *, parent, role, grants, platform=None, state=None):
        calls.append((parent, role, platform))
        return original(
            registry, parent=parent, role=role, grants=grants,
            platform=platform, state=state,
        )

    monkeypatch.setattr(bot.feature_catalog, "visible_navigation_pages", observe)
    markup = bot.all_menu(user_id=100)

    assert calls == [("all.devices", "owner", "telegram")]
    assert _callback_data(markup) == [
        "all:status", "all:screenshot", "cmd:geo_location", "cmd:lock",
        "cmd:volume", "cfm:stop_all", "menu:target", "menu:main",
    ]
    buttons = {button.callback_data: button for row in markup.inline_keyboard for button in row}
    assert buttons["all:screenshot"].style == "success"
    assert buttons["cfm:stop_all"].style == "danger"


def test_all_devices_menu_hides_owner_actions_for_non_owner(monkeypatch):
    _setup(monkeypatch, {})
    monkeypatch.setattr(bot, "get_user_role", lambda _user_id: bot.Role.USER)

    assert _callback_data(bot.all_menu(user_id=200)) == []


def test_common_response_navigation_uses_registry_and_preserves_callbacks(monkeypatch):
    _setup(monkeypatch, {})
    monkeypatch.setattr(bot, "get_user_role", lambda _user_id: bot.Role.OWNER)
    monkeypatch.setattr(bot.access_store, "get_effective_grants", lambda _user_id: {"callbacks": set()})
    monkeypatch.setattr(bot.access_store, "can_use_callback", lambda *_args, **_kwargs: True)
    calls = []
    original = bot.feature_catalog.visible_navigation_pages

    def observe(registry, *, parent, role, grants, platform=None, state=None):
        calls.append((parent, role, platform))
        return original(
            registry, parent=parent, role=role, grants=grants,
            platform=platform, state=state,
        )

    monkeypatch.setattr(bot.feature_catalog, "visible_navigation_pages", observe)

    markup = bot.back_to_device_kb(user_id=100)

    assert calls == [("device.response", "owner", "telegram")]
    assert _callback_data(markup) == ["back:device", "menu:main"]


def test_saved_snapshot_detail_and_delete_confirmation_menus_are_registry_backed(monkeypatch):
    _setup(monkeypatch, {})
    monkeypatch.setattr(bot, "get_user_role", lambda _user_id: bot.Role.OWNER)
    monkeypatch.setattr(bot.access_store, "get_effective_grants", lambda _user_id: {"callbacks": set()})
    monkeypatch.setattr(bot.access_store, "can_use_callback", lambda *_args, **_kwargs: True)
    snapshot_id = "a" * 32

    detail = bot.device_snapshot_detail_keyboard(100, snapshot_id)
    confirm = bot.device_snapshot_detail_keyboard(100, snapshot_id, confirming_delete=True)

    assert _callback_data(detail) == [
        f"catalog:snapshot:delete:{snapshot_id}", "catalog:snapshot:list", "menu:main",
    ]
    assert _callback_data(confirm) == [
        f"catalog:snapshot:delete:confirm:{snapshot_id}",
        f"catalog:snapshot:view:{snapshot_id}", "menu:main",
    ]


def test_file_inbox_is_owner_only_and_uses_registered_actions(monkeypatch):
    _setup(monkeypatch, {"device-1": {"name": "Laptop", "os": "Windows", "online": True}})
    monkeypatch.setattr(bot, "get_user_role", lambda user_id: bot.Role.OWNER if user_id == 100 else bot.Role.USER)
    monkeypatch.setattr(bot.access_store, "get_effective_grants", lambda _user_id: {"callbacks": set()})
    monkeypatch.setattr(bot.access_store, "can_use_callback", lambda *_args, **_kwargs: True)
    previous_target = bot.SESSION.get("target")
    bot.SESSION["target"] = "device-1"
    try:
        owner_files = bot.files_menu(100)
        user_files = bot.files_menu(200)
        owner_inbox = bot._file_inbox_keyboard(
            "files.inbox", 100,
            items=[{"item_id": "a" * 32, "name": "report.txt", "size": 42}],
        )
        user_inbox = bot._file_inbox_keyboard("files.inbox", 200, items=[])
    finally:
        if previous_target is None:
            bot.SESSION.pop("target", None)
        else:
            bot.SESSION["target"] = previous_target

    assert "files:inbox" in _callback_data(owner_files)
    assert "files:inbox" not in _callback_data(user_files)
    assert _callback_data(owner_inbox) == [
        "files:inbox:upload", f"files:inbox:view:{'a' * 32}", "cat:files", "menu:main",
    ]
    assert _callback_data(user_inbox) == []


def test_staged_file_placement_requires_preview_and_explicit_confirmation(monkeypatch, tmp_path):
    from types import SimpleNamespace
    from file_inbox import FileInbox

    Callback, rendered = _admin_fixture(monkeypatch, tmp_path)
    monkeypatch.setattr(bot, "get_user_role", lambda _user_id: bot.Role.OWNER)
    monkeypatch.setattr(bot.access_store, "get_effective_grants", lambda _user: {"callbacks": []})
    monkeypatch.setattr(bot.access_store, "can_use_callback", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(bot, "SESSION", {"target": "a"})
    inbox = FileInbox(tmp_path / "staged-files")
    item = inbox.stage(actor_id=100, owner_id=100, name="notes.txt", data=b"staged bytes")
    monkeypatch.setattr(bot, "file_inbox", inbox)
    published = []
    monkeypatch.setattr(bot, "publish_tracked", lambda action, **payload: (
        published.append((action, payload)) or (True, "request-1")
    ))

    async def wait_for(target, action, *, timeout, command_id):
        assert (target, action, timeout, command_id) == ("a", "file_put", 25.0, "request-1")
        return {"ok": True, "path": "/tmp/notes.txt"}

    monkeypatch.setattr(bot.fun_text_collector, "wait_for", wait_for)

    class State:
        def __init__(self):
            self.values = {}
            self.current = None

        async def clear(self):
            self.values.clear()
            self.current = None

        async def set_state(self, value):
            self.current = value

        async def get_state(self):
            return self.current

        async def update_data(self, **values):
            self.values.update(values)

        async def get_data(self):
            return dict(self.values)

    class Message:
        from_user = SimpleNamespace(id=100)
        text = "/tmp/notes.txt"

        async def delete(self):
            pass

    state = State()

    async def scenario():
        await bot.on_file_inbox_place_prompt(Callback(f"files:inbox:place:{item['item_id']}"), state)
        assert published == []
        await bot.on_file_inbox_path(Message(), state)
        assert published == []
        assert "Подтверди размещение" in rendered[-1][0]
        assert "/tmp/notes.txt" in rendered[-1][0]
        assert f"files:inbox:place:confirm:{item['item_id']}" in _callback_data(rendered[-1][1])
        await bot.on_file_inbox_place_confirm(
            Callback(f"files:inbox:place:confirm:{item['item_id']}"), state,
        )

    asyncio.run(scenario())

    assert len(published) == 1
    action, payload = published[0]
    assert action == "file_put"
    assert payload["_target"] == "a"
    assert payload["name"] == "notes.txt"
    assert payload["path"] == "/tmp/notes.txt"
    assert payload["b64"] == "c3RhZ2VkIGJ5dGVz"
    assert inbox.get(actor_id=100, owner_id=100, item_id=item["item_id"]) is not None


def test_corrupt_staged_file_still_has_owner_delete_path(monkeypatch, tmp_path):
    from file_inbox import FileInbox

    Callback, rendered = _admin_fixture(monkeypatch, tmp_path)
    monkeypatch.setattr(
        bot, "get_user_role",
        lambda user_id: bot.Role.OWNER if user_id == 100 else bot.Role.USER,
    )
    monkeypatch.setattr(bot.access_store, "get_effective_grants", lambda _user_id: {"callbacks": set()})
    monkeypatch.setattr(bot.access_store, "can_use_callback", lambda *_args, **_kwargs: True)
    inbox = FileInbox(tmp_path / "file-inbox")
    item = inbox.stage(actor_id=100, owner_id=100, name="report.txt", data=b"original")
    (tmp_path / "file-inbox" / f"{item['item_id']}.bin").write_bytes(b"tampered")
    monkeypatch.setattr(bot, "file_inbox", inbox)

    async def scenario():
        await bot.on_file_inbox_view(Callback(f"files:inbox:view:{item['item_id']}"))
        assert len(rendered) == 1
        text, markup = rendered[-1]
        callbacks = _callback_data(markup)
        assert "sha-256" in text.lower()
        assert "\u041a\u043e\u043d\u0442\u0440\u043e\u043b\u044c\u043d\u0430\u044f \u0441\u0443\u043c\u043c\u0430" in text
        assert f"files:inbox:delete:{item['item_id']}" in callbacks
        assert f"files:inbox:place:{item['item_id']}" not in callbacks
        await bot.on_file_inbox_delete_prompt(Callback(f"files:inbox:delete:{item['item_id']}"))
        assert len(rendered) == 2
        assert "\u0423\u0434\u0430\u043b\u0438\u0442\u044c" in rendered[-1][0]
        await bot.on_file_inbox_delete_confirm(
            Callback(f"files:inbox:delete:confirm:{item['item_id']}")
        )

    asyncio.run(scenario())
    assert inbox.list_items(actor_id=100, owner_id=100) == []


def test_selected_device_snapshot_ui_entry_is_owner_only(monkeypatch):
    _setup(monkeypatch, {"device-1": {"name": "Laptop", "os": "Windows"}})
    monkeypatch.setattr(
        bot, "get_user_role",
        lambda user_id: bot.Role.OWNER if user_id == 100 else bot.Role.USER,
    )
    monkeypatch.setattr(
        bot.access_store, "can_use_callback",
        lambda user_id, _callback, _owner_id, _target=None: user_id == 100,
    )

    assert "catalog:snapshot:list" in _callback_data(bot.devices_menu(100))
    assert "catalog:snapshot:list" not in _callback_data(bot.devices_menu(200))


def test_device_archive_controls_are_owner_only_and_registry_backed(monkeypatch):
    _setup(monkeypatch, {"device-1": {"name": "Laptop", "os": "Windows"}})
    monkeypatch.setattr(bot, "ADMIN_ID", 100)
    monkeypatch.setattr(bot, "get_user_role", lambda user_id: bot.Role.OWNER if user_id == 100 else bot.Role.USER)
    monkeypatch.setattr(bot.access_store, "get_effective_grants", lambda _user: {"callbacks": [], "devices": []})
    monkeypatch.setattr(bot.access_store, "can_use_callback", lambda user_id, *_args, **_kwargs: user_id == 100)
    monkeypatch.setattr(bot, "_device_compare_inventory", lambda _user: ([("device-1", {"name": "Laptop", "os": "Windows"})], "aabbccdd"))
    monkeypatch.setattr(bot.devices, "list_archived_devices", lambda **_kwargs: [], raising=False)
    monkeypatch.setattr(bot.devices, "get_device_archive", lambda *_args: None, raising=False)
    owner_device = _callback_data(bot.device_menu("device-1", 100))
    user_device = _callback_data(bot.device_menu("device-1", 200))
    owner_catalog = _callback_data(bot.devices_menu(100))
    user_catalog = _callback_data(bot.devices_menu(200))

    assert "catalog:archive:set:aabbccdd:0" in owner_device
    assert not any(value.startswith("catalog:archive:") for value in user_device)
    assert "catalog:archive:list" in owner_catalog
    assert "catalog:archive:list" not in user_catalog


def test_device_archive_and_restore_actions_are_local_and_same_card(monkeypatch, tmp_path):
    import asyncio
    from types import SimpleNamespace
    from device_store import DeviceStore

    monkeypatch.setattr(DeviceStore, "FILE", tmp_path / "devices.json", raising=False)
    monkeypatch.setattr(DeviceStore, "CATALOG_FILE", tmp_path / "catalog.json", raising=False)
    store = DeviceStore()
    store.upsert("device-1", {"name": "Laptop", "os": "Windows"})
    monkeypatch.setattr(bot, "devices", store)
    monkeypatch.setattr(bot, "ADMIN_ID", 100)
    monkeypatch.setattr(bot, "get_user_role", lambda _user: bot.Role.OWNER)
    monkeypatch.setattr(bot.access_store, "get_effective_grants", lambda _user: {"callbacks": [], "devices": []})
    monkeypatch.setattr(bot.access_store, "can_use_callback", lambda *_args, **_kwargs: True)
    audits, rendered, remote = [], [], []
    monkeypatch.setattr(bot, "audit", lambda *args, **kwargs: audits.append((args, kwargs)))
    monkeypatch.setattr(bot.transport, "publish_command", lambda *args, **kwargs: remote.append((args, kwargs)))

    async def replace(*args, **kwargs):
        rendered.append((args, kwargs))

    monkeypatch.setattr(bot, "_replace_callback_message", replace)

    class Callback:
        from_user = SimpleNamespace(id=100)
        async def answer(self, *_args, **_kwargs):
            pass

    archive = Callback()
    archive.data = f"catalog:archive:set:{bot._device_compare_inventory(100)[1]}:0"
    asyncio.run(bot.on_device_archive_set(archive))
    assert store.get("device-1") is not None
    assert store.get_device_archive(100, 100, "device-1") is not None
    assert "device-1" not in bot._visible_device_items(100)
    assert "не отправлял" in rendered[-1][0][1]
    restore_callback = next(
        button.callback_data for row in rendered[-1][1]["reply_markup"].inline_keyboard for button in row
    )
    restore = Callback()
    restore.data = restore_callback
    asyncio.run(bot.on_device_archive_restore(restore))
    assert store.get_device_archive(100, 100, "device-1") is None
    assert "device-1" in bot._visible_device_items(100)
    assert audits == [
        (("device_record_archived",), {"actor_id": 100, "device_id": "device-1"}),
        (("device_record_restored",), {"actor_id": 100, "device_id": "device-1"}),
    ]
    assert remote == []


def test_device_collection_entry_is_owner_only_and_registry_backed(monkeypatch):
    _setup(monkeypatch, {"device-1": {"name": "Laptop", "os": "Windows"}})
    monkeypatch.setattr(bot.devices, "list_device_collections", lambda **_kwargs: [], raising=False)
    monkeypatch.setattr(bot, "get_user_role", lambda user_id: bot.Role.OWNER if user_id == 100 else bot.Role.USER)
    monkeypatch.setattr(bot.access_store, "can_use_callback", lambda user_id, *_args, **_kwargs: user_id == 100)
    assert "catalog:collection:list" in _callback_data(bot.devices_menu(100))
    assert "catalog:collection:list" not in _callback_data(bot.devices_menu(200))
    rows, markup = bot.device_collection_catalog(100)
    assert rows == []
    assert _callback_data(markup) == ["catalog:collection:create", "menu:devices", "menu:main"]


def test_saved_device_filter_entry_is_owner_only_and_registry_backed(monkeypatch):
    _setup(monkeypatch, {"device-1": {"name": "Laptop", "os": "Windows"}})
    monkeypatch.setattr(bot.devices, "list_saved_device_filters", lambda **_kwargs: [], raising=False)
    monkeypatch.setattr(bot, "get_user_role", lambda user_id: bot.Role.OWNER if user_id == 100 else bot.Role.USER)
    monkeypatch.setattr(bot.access_store, "can_use_callback", lambda user_id, *_args, **_kwargs: user_id == 100)
    assert "catalog:saved-filter:list" in _callback_data(bot.devices_menu(100))
    assert "catalog:saved-filter:list" not in _callback_data(bot.devices_menu(200))
    rows, markup = bot.device_saved_filter_catalog(100)
    assert rows == []
    assert _callback_data(markup) == ["catalog:saved-filter:create", "menu:devices", "menu:main"]


def test_device_catalog_sort_entry_is_owner_only_and_registry_backed(monkeypatch):
    _setup(monkeypatch, {"device-1": {"name": "Laptop", "os": "Windows"}})
    monkeypatch.setattr(bot, "get_user_role", lambda user_id: bot.Role.OWNER if user_id == 100 else bot.Role.USER)
    monkeypatch.setattr(bot.access_store, "can_use_callback", lambda user_id, *_args, **_kwargs: user_id == 100)
    monkeypatch.setattr(bot.devices, "get_device_catalog_sort", lambda *_args: "name", raising=False)
    assert "devices:sort" in _callback_data(bot.devices_menu(100))
    assert "devices:sort" not in _callback_data(bot.devices_menu(200))
    keyboard = bot.device_sort_keyboard(100)
    assert _callback_data(keyboard) == [
        "devices:sort:name", "devices:sort:platform", "devices:sort:version", "devices:sort:last_seen",
        "devices:sort:custom", "menu:devices",
    ]


def test_device_catalog_custom_order_is_owner_only_and_registry_backed(monkeypatch):
    _setup(monkeypatch, {"device-1": {"name": "Laptop"}})
    monkeypatch.setattr(bot, "get_user_role", lambda user_id: bot.Role.OWNER if user_id == 100 else bot.Role.USER)
    monkeypatch.setattr(bot.access_store, "can_use_callback", lambda user_id, *_args, **_kwargs: user_id == 100)
    monkeypatch.setattr(bot.devices, "get_device_catalog_sort", lambda *_args: "custom", raising=False)
    assert "catalog:order:edit" in _callback_data(bot.devices_menu(100))
    assert "catalog:order:edit" not in _callback_data(bot.devices_menu(200))


def test_device_catalog_custom_order_move_updates_same_card_and_rejects_stale_callback(monkeypatch, tmp_path):
    import asyncio
    from types import SimpleNamespace
    from device_store import DeviceStore

    monkeypatch.setattr(DeviceStore, "FILE", tmp_path / "devices.json", raising=False)
    monkeypatch.setattr(DeviceStore, "CATALOG_FILE", tmp_path / "catalog.json", raising=False)
    store = DeviceStore()
    store.upsert("device-a", {"name": "Alpha"})
    store.upsert("device-b", {"name": "Beta"})
    store.upsert("device-c", {"name": "Gamma"})
    store.set_device_catalog_sort(100, 100, "custom")
    store.set_device_catalog_order(100, 100, ["device-a", "device-b", "device-c"])
    monkeypatch.setattr(bot, "devices", store)
    monkeypatch.setattr(bot, "_visible_device_items", lambda _user: store.all())
    monkeypatch.setattr(bot, "ADMIN_ID", 100)
    monkeypatch.setattr(bot, "get_user_role", lambda _user: bot.Role.OWNER)
    monkeypatch.setattr(bot.access_store, "can_use_callback", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(bot.access_store, "get_effective_grants", lambda _user: {"callbacks": [], "devices": []})
    monkeypatch.setattr(bot.access_store, "append_audit", lambda *_args, **_kwargs: None)
    rendered = []

    class Callback:
        from_user = SimpleNamespace(id=100)
        message = SimpleNamespace()
        async def answer(self, *_args, **_kwargs): pass

    async def replace(source, text, reply_markup=None, **_kwargs):
        rendered.append((source, text, reply_markup))

    monkeypatch.setattr(bot, "_replace_callback_message", replace)
    keyboard = bot.device_catalog_order_keyboard(100)
    assert "catalog:order:up:1:" in " ".join(_callback_data(keyboard))
    assert _callback_data(bot.devices_menu(100))[:3] == ["dev:device-a", "dev:device-b", "dev:device-c"]
    stale = Callback()
    stale.data = "catalog:order:up:1:deadbeef"
    asyncio.run(bot.on_device_catalog_order_move(stale))
    assert store.get_device_catalog_order(100, 100, ["device-a", "device-b", "device-c"]) == ["device-a", "device-b", "device-c"]
    assert rendered == []

    import hashlib
    token = hashlib.sha256("\0".join(("device-a", "device-b", "device-c")).encode()).hexdigest()[:8]
    current = Callback()
    current.data = f"catalog:order:up:1:{token}"
    asyncio.run(bot.on_device_catalog_order_move(current))
    assert store.get_device_catalog_order(100, 100, ["device-a", "device-b", "device-c"]) == ["device-b", "device-a", "device-c"]
    assert _callback_data(bot.devices_menu(100))[:3] == ["dev:device-b", "dev:device-a", "dev:device-c"]
    assert len(rendered) == 1
    assert any("Beta" in button.text for row in rendered[0][2].inline_keyboard for button in row)


def test_pinned_device_toggle_persists_and_updates_the_same_device_card(monkeypatch, tmp_path):
    import asyncio
    from types import SimpleNamespace
    from device_store import DeviceStore

    monkeypatch.setattr(DeviceStore, "FILE", tmp_path / "devices.json", raising=False)
    monkeypatch.setattr(DeviceStore, "CATALOG_FILE", tmp_path / "catalog.json", raising=False)
    store = DeviceStore()
    store.upsert("dev1", {"name": "Desk PC", "os": "Windows"})
    monkeypatch.setattr(bot, "devices", store)
    monkeypatch.setattr(bot, "ADMIN_ID", 100)
    monkeypatch.setattr(bot, "get_user_role", lambda _user: bot.Role.OWNER)
    monkeypatch.setattr(bot.access_store, "can_use_callback", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(bot.access_store, "get_effective_grants", lambda _user: {"callbacks": [], "devices": []})
    audit = []
    monkeypatch.setattr(bot.access_store, "append_audit", lambda action, **fields: audit.append((action, fields)))
    rendered = []

    class Callback:
        from_user = SimpleNamespace(id=100)
        data = "catalog:pinned:toggle:dev1"
        message = SimpleNamespace()
        async def answer(self, *_args, **_kwargs): pass

    async def replace(source, text, reply_markup=None, **_kwargs):
        rendered.append((source, text, reply_markup))

    monkeypatch.setattr(bot, "_replace_callback_message", replace)
    callback = Callback()
    asyncio.run(bot.on_device_pin_toggle(callback))

    assert store.list_pinned_devices(actor_id=100, owner_id=100) == ["dev1"]
    assert audit == [("device_pinned", {"actor_id": 100, "device_id": "dev1"})]
    assert len(rendered) == 1 and "Desk PC" in rendered[0][1]
    assert "catalog:pinned:toggle:dev1" in _callback_data(rendered[0][2])


def test_device_catalog_sorting_is_owner_configurable_and_orders_unknowns_last():
    rows = {
        "unknown": {},
        "a": {"name": "zeta", "os": "Windows", "version": "2.0", "last_seen": 10},
        "b": {"name": "alpha", "os": "macOS", "version": "1.0", "last_seen": 20},
    }
    assert [key for key, _ in bot.sort_device_inventory(rows, "name")] == ["b", "a", "unknown"]
    # Platform values are sorted alphabetically (macOS before Windows), with
    # devices that do not report a platform kept at the end.
    assert [key for key, _ in bot.sort_device_inventory(rows, "platform")] == ["b", "a", "unknown"]
    assert [key for key, _ in bot.sort_device_inventory(rows, "version")] == ["b", "a", "unknown"]
    assert [key for key, _ in bot.sort_device_inventory(rows, "last_seen")] == ["b", "a", "unknown"]


def test_owner_can_create_and_apply_saved_device_filter_in_one_card(monkeypatch, tmp_path):
    import asyncio
    from types import SimpleNamespace
    from device_store import DeviceStore

    monkeypatch.setattr(DeviceStore, "FILE", tmp_path / "devices.json", raising=False)
    monkeypatch.setattr(DeviceStore, "CATALOG_FILE", tmp_path / "catalog.json", raising=False)
    store = DeviceStore()
    store.upsert("mac-a", {"name": "MacBook", "os": "macOS 15.2"})
    store.upsert("win-b", {"name": "PC", "os": "Windows 11"})
    monkeypatch.setattr(bot, "devices", store)
    monkeypatch.setattr(bot, "ADMIN_ID", 100)
    monkeypatch.setattr(bot, "get_user_role", lambda _user: bot.Role.OWNER)
    monkeypatch.setattr(bot, "_visible_device_items", lambda _user: store.all())
    monkeypatch.setattr(bot.access_store, "append_audit", lambda *_args, **_kwargs: None)
    rendered = []

    class State:
        value = None
        async def clear(self): self.value = None
        async def set_state(self, state): self.value = state.state

    class Callback:
        from_user = SimpleNamespace(id=100)
        data = "catalog:saved-filter:create"
        async def answer(self, *_args, **_kwargs): pass

    class Message:
        from_user = SimpleNamespace(id=100)
        text = "Mac fleet | os | macOS"

    async def replace(_source, text, reply_markup=None, **_kwargs):
        rendered.append((text, reply_markup))

    monkeypatch.setattr(bot, "_replace_callback_message", replace)
    monkeypatch.setattr(bot, "_replace_user_card", replace)
    state = State()

    async def scenario():
        await bot.on_device_saved_filter_create(Callback(), state)
        await bot.on_device_saved_filter_create_input(Message(), state)
        saved = store.list_saved_device_filters(actor_id=100, owner_id=100)[0]
        Callback.data = f"catalog:saved-filter:apply:{saved['filter_id']}"
        await bot.on_device_saved_filter_apply(Callback())

    asyncio.run(scenario())
    saved = store.list_saved_device_filters(actor_id=100, owner_id=100)
    assert len(saved) == 1 and saved[0]["filter_type"] == "os"
    assert "1 совпадений" in rendered[-1][0]
    assert _callback_data(rendered[-1][1]) == ["dev:mac-a", "catalog:saved-filter:list", "menu:main"]
    assert len(rendered) == 3


def test_owner_can_create_and_edit_persistent_device_collection_in_one_card(monkeypatch, tmp_path):
    import asyncio
    from types import SimpleNamespace
    from device_store import DeviceStore

    monkeypatch.setattr(DeviceStore, "FILE", tmp_path / "devices.json", raising=False)
    monkeypatch.setattr(DeviceStore, "CATALOG_FILE", tmp_path / "catalog.json", raising=False)
    store = DeviceStore()
    store.upsert("mac-a", {"name": "MacBook"})
    store.upsert("win-b", {"name": "PC"})
    monkeypatch.setattr(bot, "devices", store)
    monkeypatch.setattr(bot, "ADMIN_ID", 100)
    monkeypatch.setattr(bot, "get_user_role", lambda _user: bot.Role.OWNER)
    monkeypatch.setattr(bot, "_visible_device_items", lambda _user: store.all())
    monkeypatch.setattr(bot.access_store, "append_audit", lambda *_args, **_kwargs: None)
    rendered = []

    class State:
        value = None
        data = {}
        async def clear(self): self.value, self.data = None, {}
        async def set_state(self, state): self.value = state.state
        async def update_data(self, **values): self.data.update(values)
        async def get_data(self): return dict(self.data)

    class Callback:
        from_user = SimpleNamespace(id=100)
        data = "catalog:collection:create"
        async def answer(self, *_args, **_kwargs): pass

    class Message:
        from_user = SimpleNamespace(id=100)
        text = "Lab fleet"

    async def replace(_source, text, reply_markup=None, **_kwargs):
        rendered.append((text, reply_markup))

    monkeypatch.setattr(bot, "_replace_callback_message", replace)
    monkeypatch.setattr(bot, "_replace_user_card", replace)
    state = State()

    async def scenario():
        await bot.on_device_collection_create(Callback(), state)
        await bot.on_device_collection_create_name(Message(), state)
        Message.text = "mac-a, win-b"
        await bot.on_device_collection_create_members(Message(), state)
        collection = store.list_device_collections(actor_id=100, owner_id=100)[0]
        Callback.data = f"catalog:collection:edit:{collection['collection_id']}"
        await bot.on_device_collection_edit_prompt(Callback(), state)
        Message.text = "win-b"
        await bot.on_device_collection_edit_members(Message(), state)

    asyncio.run(scenario())
    rows = store.list_device_collections(actor_id=100, owner_id=100)
    assert len(rows) == 1
    assert rows[0]["name"] == "Lab fleet"
    assert rows[0]["device_ids"] == ["win-b"]
    assert "expires_at" not in rows[0]
    assert len(rendered) == 5
    assert "обновлён" in rendered[-1][0]


def test_inactivity_history_entry_is_owner_only(monkeypatch):
    _setup(monkeypatch, {"mac-a": {"name": "Mac"}})
    monkeypatch.setattr(
        bot, "get_user_role",
        lambda user_id: bot.Role.OWNER if user_id == 100 else bot.Role.USER,
    )
    monkeypatch.setattr(
        bot.access_store, "can_use_callback",
        lambda user_id, _callback, _owner_id, _target=None: user_id == 100,
    )

    assert "catalog:inactivity:list" in _callback_data(bot.devices_menu(100))
    assert "catalog:inactivity:list" not in _callback_data(bot.devices_menu(200))


def test_typed_inventory_editor_entry_is_owner_only(monkeypatch, tmp_path):
    _admin_fixture(monkeypatch, tmp_path)
    monkeypatch.setattr(bot, "get_user_role", lambda user_id: bot.Role.OWNER if user_id == 100 else bot.Role.USER)
    bot.access_store.set_role(100, 200, bot.Role.USER, 100)
    monkeypatch.setattr(bot, "_filter_device_keyboard", lambda markup, *_args, **_kwargs: markup)

    owner_callbacks = _callback_data(bot.device_menu("a", 100))
    user_callbacks = _callback_data(bot.device_menu("a", 200))

    assert "catalog:fields:list" in owner_callbacks
    assert "catalog:fields:list" not in user_callbacks


def test_typed_inventory_editor_lists_owner_schema_and_values(monkeypatch, tmp_path):
    from device_store import DeviceStore

    Callback, rendered = _admin_fixture(monkeypatch, tmp_path)
    monkeypatch.setattr(DeviceStore, "FILE", tmp_path / "devices.json")
    monkeypatch.setattr(DeviceStore, "CATALOG_FILE", tmp_path / "device_catalog.json")
    inventory = DeviceStore()
    inventory.upsert("mac-a", {"name": "MacBook", "os": "macOS"})
    inventory.define_typed_field(100, 100, "purpose", "enum", choices=["personal", "test"])
    inventory.set_typed_field(100, 100, "mac-a", "purpose", "test")
    monkeypatch.setattr(bot, "devices", inventory)
    monkeypatch.setattr(bot, "SESSION", {"target": "mac-a"})

    class State:
        async def clear(self):
            self.data = {}
        async def update_data(self, **kwargs):
            self.data = kwargs

    asyncio.run(bot.on_typed_fields_list(Callback("catalog:fields:list"), State()))

    text, markup = rendered[-1]
    assert "MacBook" in text
    assert "purpose" in text and "test" in text
    assert "catalog:fields:create" in _callback_data(markup)
    assert any(value.startswith("catalog:fields:edit:") for value in _callback_data(markup))


def test_typed_inventory_editor_saves_enum_boolean_and_date_values(monkeypatch, tmp_path):
    from types import SimpleNamespace
    from device_store import DeviceStore

    Callback, rendered = _admin_fixture(monkeypatch, tmp_path)
    monkeypatch.setattr(DeviceStore, "FILE", tmp_path / "devices.json")
    monkeypatch.setattr(DeviceStore, "CATALOG_FILE", tmp_path / "device_catalog.json")
    inventory = DeviceStore()
    inventory.upsert("mac-a", {"name": "MacBook", "os": "macOS"})
    inventory.define_typed_field(100, 100, "purpose", "enum", choices=["personal", "test"])
    inventory.define_typed_field(100, 100, "is_spare", "boolean")
    inventory.define_typed_field(100, 100, "reviewed_on", "date")
    monkeypatch.setattr(bot, "devices", inventory)
    monkeypatch.setattr(bot, "SESSION", {"target": "mac-a"})

    class State:
        value = None
        data = {}
        async def clear(self):
            self.value, self.data = None, {}
        async def update_data(self, **values):
            self.data.update(values)
        async def set_state(self, value):
            self.value = value.state
        async def get_data(self):
            return dict(self.data)

    state = State()
    async def scenario():
        await bot.on_typed_fields_list(Callback("catalog:fields:list"), state)
        await bot.on_typed_field_edit(Callback("catalog:fields:edit:1"), state)
        await bot.on_typed_field_value_choice(Callback("catalog:fields:value:1"), state)
        await bot.on_typed_field_edit(Callback("catalog:fields:edit:0"), state)
        await bot.on_typed_field_value_choice(Callback("catalog:fields:value:1"), state)
        await bot.on_typed_field_edit(Callback("catalog:fields:edit:2"), state)
        await bot.on_typed_field_date_input(
            SimpleNamespace(text="2026-10-15", from_user=SimpleNamespace(id=100)), state,
        )

    asyncio.run(scenario())

    assert inventory.typed_fields(100, 100, "mac-a") == {
        "purpose": "test", "is_spare": True, "reviewed_on": "2026-10-15",
    }
    assert "test" in rendered[-1][0]
    assert "Да" in rendered[-1][0]
    assert "2026-10-15" in rendered[-1][0]


def test_typed_inventory_field_definition_rejects_invalid_format_and_stays_in_form(monkeypatch, tmp_path):
    from types import SimpleNamespace
    from device_store import DeviceStore

    _Callback, rendered = _admin_fixture(monkeypatch, tmp_path)
    monkeypatch.setattr(DeviceStore, "FILE", tmp_path / "devices.json")
    monkeypatch.setattr(DeviceStore, "CATALOG_FILE", tmp_path / "device_catalog.json")
    inventory = DeviceStore()
    inventory.upsert("a", {"name": "Laptop"})
    monkeypatch.setattr(bot, "devices", inventory)
    class State:
        data = {"typed_field_device": "a"}
        value = "wait_typed_field_definition"
        async def get_data(self):
            return dict(self.data)
        async def get_state(self):
            return self.value
        async def set_state(self, value):
            self.value = value.state
        async def clear(self):
            self.data, self.value = {}, None

    state = State()
    message = SimpleNamespace(text="purpose | enum", from_user=SimpleNamespace(id=100))

    asyncio.run(bot.on_typed_field_definition_input(message, state))

    assert state.value == "wait_typed_field_definition"
    assert "enum fields require 2-50" in rendered[-1][0]


def test_typed_inventory_owner_can_define_new_enum_schema_in_telegram(monkeypatch, tmp_path):
    from types import SimpleNamespace
    from device_store import DeviceStore

    Callback, rendered = _admin_fixture(monkeypatch, tmp_path)
    monkeypatch.setattr(DeviceStore, "FILE", tmp_path / "devices.json")
    monkeypatch.setattr(DeviceStore, "CATALOG_FILE", tmp_path / "device_catalog.json")
    inventory = DeviceStore()
    inventory.upsert("mac-a", {"name": "MacBook"})
    monkeypatch.setattr(bot, "devices", inventory)
    monkeypatch.setattr(bot, "SESSION", {"target": "mac-a"})

    class State:
        value = None
        data = {}
        async def clear(self):
            self.value, self.data = None, {}
        async def update_data(self, **values):
            self.data.update(values)
        async def set_state(self, value):
            self.value = value.state
        async def get_data(self):
            return dict(self.data)

    state = State()
    async def scenario():
        await bot.on_typed_fields_list(Callback("catalog:fields:list"), state)
        await bot.on_typed_field_create(Callback("catalog:fields:create"), state)
        await bot.on_typed_field_definition_input(
            SimpleNamespace(text="purpose | enum | personal, test", from_user=SimpleNamespace(id=100)),
            state,
        )

    asyncio.run(scenario())

    assert inventory.typed_field_schemas(100, 100) == {
        "purpose": {"type": "enum", "choices": ["personal", "test"]}
    }
    assert "purpose" in rendered[-1][0]
    assert "catalog:fields:edit:0" in _callback_data(rendered[-1][1])


def test_owner_can_capture_and_compare_aggregate_inactivity_history(monkeypatch, tmp_path):
    from device_store import DeviceStore

    Callback, rendered = _admin_fixture(monkeypatch, tmp_path)
    monkeypatch.setattr(DeviceStore, "FILE", tmp_path / "devices.json")
    monkeypatch.setattr(DeviceStore, "CATALOG_FILE", tmp_path / "device_catalog.json")
    inventory = DeviceStore()
    inventory.upsert("win-b", {"name": "PC", "os": "Windows", "last_seen": 100})
    monkeypatch.setattr(bot, "devices", inventory)
    now = [100]
    monkeypatch.setattr(bot.time, "time", lambda: now[0])

    async def scenario():
        await bot.on_inactivity_capture(Callback("catalog:inactivity:capture"))
        now[0] += 8 * 86_400
        await bot.on_inactivity_capture(Callback("catalog:inactivity:capture"))
        await bot.on_inactivity_compare(Callback("catalog:inactivity:compare"))

    asyncio.run(scenario())

    assert "windows" in rendered[-1][0]
    assert "under_7_days: -1" in rendered[-1][0]
    assert "7_to_30_days: +1" in rendered[-1][0]
    assert "win-b" not in rendered[-1][0]


def test_owner_can_save_exact_device_set_from_telegram_flow(monkeypatch, tmp_path):
    from types import SimpleNamespace
    from device_store import DeviceStore

    monkeypatch.setattr(DeviceStore, "FILE", tmp_path / "devices.json", raising=False)
    monkeypatch.setattr(DeviceStore, "CATALOG_FILE", tmp_path / "catalog.json", raising=False)
    store = DeviceStore()
    store.upsert("mac-a", {"name": "Mac", "os": "macOS"})
    store.upsert("win-b", {"name": "PC", "os": "Windows"})
    monkeypatch.setattr(bot, "devices", store)
    monkeypatch.setattr(bot, "ADMIN_ID", 100)
    monkeypatch.setattr(bot, "get_user_role", lambda _user: bot.Role.OWNER)
    monkeypatch.setattr(bot, "_visible_device_items", lambda _user: store.all())
    monkeypatch.setattr(bot.access_store, "append_audit", lambda *args, **kwargs: None)
    rendered = []

    class State:
        value = None
        data = {}
        async def clear(self):
            self.value, self.data = None, {}
        async def set_state(self, state):
            self.value = state.state
        async def update_data(self, **values):
            self.data.update(values)
        async def get_data(self):
            return dict(self.data)
        async def get_state(self):
            return self.value

    class Callback:
        from_user = SimpleNamespace(id=100)
        data = "catalog:snapshot:start"
        async def answer(self, *args, **kwargs):
            pass

    class Message:
        from_user = SimpleNamespace(id=100)
        text = "Travel kit"

    async def replace(_source, text, reply_markup=None, **kwargs):
        rendered.append((text, reply_markup))

    monkeypatch.setattr(bot, "_replace_callback_message", replace)
    monkeypatch.setattr(bot, "_replace_user_card", replace)
    state = State()

    async def scenario():
        await bot.on_device_snapshot_start(Callback(), state)
        # Sorted device IDs make index 0 the explicit `mac-a` selection.
        toggle = Callback()
        toggle.data = "catalog:snapshot:toggle:0"
        await bot.on_device_snapshot_toggle(toggle, state)
        name = Callback()
        name.data = "catalog:snapshot:name"
        await bot.on_device_snapshot_name_prompt(name, state)
        await bot.on_device_snapshot_name_input(Message(), state)

    asyncio.run(scenario())
    rows = store.list_selected_snapshots(actor_id=bot.ADMIN_ID, owner_id=bot.ADMIN_ID)
    assert len(rows) == 1
    assert rows[0]["name"] == "Travel kit"
    assert rows[0]["device_ids"] == ["mac-a"]
    assert state.value is None
    assert "<b>Travel kit</b>" in rendered[-1][0]


def test_snapshot_selection_aborts_if_device_index_order_changes(monkeypatch):
    from types import SimpleNamespace

    monkeypatch.setattr(bot, "ADMIN_ID", 100)
    monkeypatch.setattr(bot, "get_user_role", lambda _user: bot.Role.OWNER)
    visible = {"mac-a": {"name": "Mac"}, "win-b": {"name": "PC"}}
    monkeypatch.setattr(bot, "_visible_device_items", lambda _user: dict(visible))
    answers = []

    class State:
        value = None
        data = {}
        async def clear(self):
            self.value, self.data = None, {}
        async def set_state(self, state):
            self.value = state.state
        async def update_data(self, **values):
            self.data.update(values)
        async def get_data(self):
            return dict(self.data)
        async def get_state(self):
            return self.value

    class Callback:
        from_user = SimpleNamespace(id=100)
        data = "catalog:snapshot:start"
        async def answer(self, *args, **kwargs):
            answers.append((args, kwargs))

    async def replace(*args, **kwargs):
        pass
    monkeypatch.setattr(bot, "_replace_callback_message", replace)
    state = State()

    async def scenario():
        await bot.on_device_snapshot_start(Callback(), state)
        visible["a-new"] = {"name": "New device"}
        toggle = Callback()
        toggle.data = "catalog:snapshot:toggle:1"
        await bot.on_device_snapshot_toggle(toggle, state)

    asyncio.run(scenario())
    assert state.value is None
    assert answers[-1][1].get("show_alert") is True

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
            "irm https://invinby.github.io/XIDER/win.txt | iex",
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

    async def replace(_cq, text, reply_markup=None, **_kwargs):
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
        ["menu:devices", "menu:server"],
        ["menu:admin", "catalog:areas"],
        ["menu:about"],
    ]

    device_rows = [
        [button.callback_data for button in row]
        for row in bot.device_menu("dev1").inline_keyboard
    ]
    assert device_rows[:5] == [
        ["cat:system", "cat:screen"],
        ["cat:files", "cat:network"],
        ["cat:terminal", "cat:power"],
        ["versions:device", "cmd:capabilities"],
        ["cat:pranks", "catalog:tags:list"],
    ]
    assert device_rows[5] == ["catalog:pinned:toggle:dev1"]
    assert device_rows[6] == ["catalog:fields:list"]
    assert device_rows[7] == ["cmd:agent_logs"]
    assert device_rows[-1] == ["menu:target", "menu:main"]


def test_device_action_screens_keep_back_and_home_navigation(monkeypatch):
    _setup(monkeypatch, {"device-1": {"name": "PC", "os": "Windows"}})
    monkeypatch.setattr(bot, "SESSION", {"target": "device-1"})
    builders = (
        bot.screen_menu, bot.input_menu, bot.system_menu, bot.network_menu,
        bot.files_menu, bot.terminal_menu, bot.power_menu, bot.power_menu_new,
        bot.guardian_menu, bot.pranks_menu, bot.device_settings_menu,
        bot.mic_options_menu, bot.vol_options_menu, bot.wallpaper_menu,
        lambda: bot._favorites_kb("device-1", []),
        lambda: bot.confirm_power_menu("shutdown"),
    )

    for builder in builders:
        callbacks = set(_callback_data(builder()))
        # Nested screens may preserve their established exact callback contract;
        # their Back route leads to a parent screen that exposes Home.
        assert callbacks & {"menu:main", "back:device", "cat:power"}, builder
        assert callbacks.intersection({
            "back:device", "menu:devices", "menu:target", "cat:screen",
            "cat:media", "cat:power",
        }), f"{builder} has no back route"


def test_mic_and_volume_option_menus_are_rendered_from_registry(monkeypatch):
    _setup(monkeypatch, {"device-1": {"name": "PC", "os": "Windows"}})
    monkeypatch.setattr(bot, "SESSION", {"target": "device-1"})
    registry = bot.feature_catalog.load_registry()
    expected_by_parent = {
        "mic.options": ["micdur:5", "micdur:15", "micdur:30", "micdur:60", "mic:level", "mic:balance", "media:history", "audio:outputs", "audio:switch", "mic:test", "mic:preview", "cat:media", "menu:main"],
        "volume.options": ["volq:0", "volq:25", "volq:50", "volq:75", "volq:100", "cat:media", "menu:main"],
    }

    for parent, expected in expected_by_parent.items():
        rows = [row for row in registry["navigation"] if row["parent"] == parent]
        assert [row["callback"] for row in rows] == expected

    assert _callback_data(bot.mic_options_menu()) == expected_by_parent["mic.options"]
    assert _callback_data(bot.vol_options_menu()) == expected_by_parent["volume.options"]
    assert all(row["roles"] == ["owner"] for row in registry["navigation"] if row["parent"] == "mic.options" and row["callback"].startswith(("micdur:", "mic:level", "mic:balance", "audio:outputs")))

    # A row removed from the declarative source must no longer be rendered.
    registry["navigation"] = [
        row for row in registry["navigation"]
        if row["page_id"] != "mic.options.duration.15"
    ]
    monkeypatch.setattr(bot.feature_catalog, "load_registry", lambda: registry)
    assert "micdur:15" not in _callback_data(bot.mic_options_menu())


def test_rotate_menu_uses_registered_options_and_back_controls(monkeypatch):
    _setup(monkeypatch, {"device-1": {"name": "PC", "os": "Windows"}})
    monkeypatch.setattr(bot, "SESSION", {"target": "device-1"})
    registry = bot.feature_catalog.load_registry()
    expected = ["rotate:0", "rotate:90", "rotate:180", "rotate:270", "cat:screen", "menu:main"]
    rows = [row for row in registry["navigation"] if row["parent"] == "rotate.options"]

    assert [row["callback"] for row in rows] == expected
    assert _callback_data(bot.rotate_menu()) == expected

    registry["navigation"] = [
        row for row in registry["navigation"] if row["page_id"] != "rotate.options.angle.90"
    ]
    monkeypatch.setattr(bot.feature_catalog, "load_registry", lambda: registry)
    assert "rotate:90" not in _callback_data(bot.rotate_menu())


def test_wallpaper_menu_uses_registered_actions_and_navigation(monkeypatch):
    _setup(monkeypatch, {"device-1": {"name": "PC", "os": "Windows"}})
    monkeypatch.setattr(bot, "SESSION", {"target": "device-1"})
    registry = bot.feature_catalog.load_registry()
    expected = [
        "cmd:wallpaper_random_meme", "cmd:wallpaper_photo_guide", "fun:wallpaper",
        "cmd:prank_restore_wallpaper", "cat:screen", "menu:main",
    ]
    rows = [row for row in registry["navigation"] if row["parent"] == "wallpaper.options"]

    assert [row["callback"] for row in rows] == expected
    assert _callback_data(bot.wallpaper_menu()) == expected

    registry["navigation"] = [
        row for row in registry["navigation"] if row["page_id"] != "wallpaper.options.url"
    ]
    monkeypatch.setattr(bot.feature_catalog, "load_registry", lambda: registry)
    assert "fun:wallpaper" not in _callback_data(bot.wallpaper_menu())


def test_url_count_keyboard_uses_registered_options_and_navigation(monkeypatch):
    _setup(monkeypatch, {"device-1": {"name": "PC", "os": "Windows"}})
    monkeypatch.setattr(bot, "SESSION", {"target": "device-1"})
    registry = bot.feature_catalog.load_registry()
    expected = [
        "url:count:1", "url:count:2", "url:count:3", "url:count:5",
        "url:fav:save", "url:cancel", "menu:main",
    ]
    rows = [row for row in registry["navigation"] if row["parent"] == "url.count.options"]

    assert [row["callback"] for row in rows] == expected
    assert _callback_data(bot.url_count_keyboard(is_owner=True)) == expected

    registry["navigation"] = [
        row for row in registry["navigation"]
        if row["page_id"] != "url.count.options.3"
    ]
    monkeypatch.setattr(bot.feature_catalog, "load_registry", lambda: registry)
    assert "url:count:3" not in _callback_data(bot.url_count_keyboard(is_owner=True))


def test_power_confirmation_uses_registered_action_and_rejects_unknown_action(monkeypatch):
    _setup(monkeypatch, {"device-1": {"name": "PC", "os": "Windows"}})
    monkeypatch.setattr(bot, "SESSION", {"target": "device-1"})
    registry = bot.feature_catalog.load_registry()
    expected = [
        "power_confirm:shutdown", "power_confirm:reboot", "power_confirm:sleep",
        "back:device", "menu:main",
    ]
    rows = [row for row in registry["navigation"] if row["parent"] == "power.confirm"]

    assert [row["callback"] for row in rows] == expected
    assert _callback_data(bot.confirm_power_menu("reboot")) == [
        "power_confirm:reboot", "back:device", "menu:main",
    ]
    assert _callback_data(bot.confirm_power_menu("arbitrary-text")) == [
        "back:device", "menu:main",
    ]

    registry["navigation"] = [
        row for row in registry["navigation"]
        if row["page_id"] != "power.confirm.reboot"
    ]
    monkeypatch.setattr(bot.feature_catalog, "load_registry", lambda: registry)
    assert "power_confirm:reboot" not in _callback_data(bot.confirm_power_menu("reboot"))


def test_power_action_shows_effect_and_unsaved_work_limit_before_confirmation(monkeypatch, tmp_path):
    telegram = _card_telegram(monkeypatch, tmp_path)
    _setup(monkeypatch, {"device-1": {"name": "Mac & Air", "os": "macOS 27.0"}})
    monkeypatch.setattr(bot, "SESSION", {"target": "device-1"})
    published = []
    monkeypatch.setattr(bot, "publish", lambda *args, **kwargs: published.append((args, kwargs)) or True)

    asyncio.run(bot.on_power_action(telegram.callback("power:shutdown")))

    assert list(telegram.cards) == [30]
    body = telegram.cards[30]["text"]
    assert "Предпросмотр действия" in body
    assert "полностью выключит устройство" in body
    assert "несохранённые документы" in body
    assert "не может надёжно определить" in body
    assert "Mac &amp; Air" in body
    callbacks = {button.callback_data for row in telegram.cards[30]["markup"].inline_keyboard for button in row}
    assert "power_confirm:shutdown" in callbacks
    assert published == []


def test_url_favorites_keyboard_uses_registered_templates_and_navigation(monkeypatch):
    _setup(monkeypatch, {"device-1": {"name": "PC", "os": "Windows"}})
    monkeypatch.setattr(bot, "SESSION", {"target": "device-1"})
    favorite = {"id": "0123456789ab", "name": "Docs", "url": "https://example.com/docs"}
    monkeypatch.setattr(
        bot.bot_settings, "get",
        lambda key, default=None: [favorite] if key == "url_favorites" else default,
    )
    registry = bot.feature_catalog.load_registry()
    expected = [
        "url:fav:open:*", "url:fav:delete:*", "back:device", "menu:main",
    ]
    rows = [row for row in registry["navigation"] if row["parent"] == "url.favorites"]

    assert [row["callback"] for row in rows] == expected
    assert _callback_data(bot.url_favorites_keyboard(is_owner=True)) == [
        "url:fav:open:0123456789ab", "url:fav:delete:0123456789ab",
        "back:device", "menu:main",
    ]

    registry["navigation"] = [
        row for row in registry["navigation"]
        if row["page_id"] != "url.favorites.delete"
    ]
    monkeypatch.setattr(bot.feature_catalog, "load_registry", lambda: registry)
    assert "url:fav:delete:0123456789ab" not in _callback_data(
        bot.url_favorites_keyboard(is_owner=True)
    )


def test_device_snapshot_selection_keyboard_uses_registered_templates(monkeypatch):
    _setup(monkeypatch, {})
    monkeypatch.setattr(bot, "get_user_role", lambda _user_id: bot.Role.OWNER)
    monkeypatch.setattr(bot.access_store, "get_effective_grants", lambda _user_id: {"callbacks": []})
    available = {
        "mac-a": {"name": "MacBook Air", "os": "macOS"},
        "win-b": {"name": "Windows PC", "os": "Windows"},
    }
    monkeypatch.setattr(bot, "_visible_device_items", lambda _user_id: available)
    registry = bot.feature_catalog.load_registry()
    expected_templates = [
        "catalog:snapshot:toggle:*", "catalog:snapshot:name",
        "catalog:snapshot:cancel",
    ]
    rows = [row for row in registry["navigation"] if row["parent"] == "catalog.snapshot.selection"]

    assert [row["callback"] for row in rows] == expected_templates
    assert _callback_data(bot.device_snapshot_selection_menu(100, ["mac-a"])) == [
        "catalog:snapshot:toggle:0", "catalog:snapshot:toggle:1",
        "catalog:snapshot:name", "catalog:snapshot:cancel",
    ]
    monkeypatch.setattr(bot, "get_user_role", lambda _user_id: bot.Role.USER)
    assert _callback_data(bot.device_snapshot_selection_menu(200, ["mac-a"])) == []
    monkeypatch.setattr(bot, "get_user_role", lambda _user_id: bot.Role.OWNER)

    registry["navigation"] = [
        row for row in registry["navigation"] if row["page_id"] != "catalog.snapshot.toggle"
    ]
    monkeypatch.setattr(bot.feature_catalog, "load_registry", lambda: registry)
    assert _callback_data(bot.device_snapshot_selection_menu(100, ["mac-a"])) == [
        "catalog:snapshot:name", "catalog:snapshot:cancel",
    ]


def test_typed_inventory_keyboard_uses_registered_templates(monkeypatch):
    _setup(monkeypatch, {})
    monkeypatch.setattr(bot, "get_user_role", lambda _user_id: bot.Role.OWNER)
    monkeypatch.setattr(bot.access_store, "get_effective_grants", lambda _user_id: {"callbacks": []})
    schemas = {"color": {"type": "enum"}, "travel_ready": {"type": "bool"}}
    values = {"color": "blue", "travel_ready": True}
    monkeypatch.setattr(bot.devices, "typed_field_schemas", lambda _actor, _owner: schemas, raising=False)
    monkeypatch.setattr(bot.devices, "typed_fields", lambda _actor, _owner, _device: values, raising=False)
    registry = bot.feature_catalog.load_registry()
    expected_templates = [
        "catalog:fields:edit:*", "catalog:fields:create", "catalog:fields:back", "menu:main",
    ]
    rows = [row for row in registry["navigation"] if row["parent"] == "catalog.fields.list"]

    assert [row["callback"] for row in rows] == expected_templates
    schemas_result, values_result, keys, markup = bot.typed_fields_menu(100, "mac-a")
    assert schemas_result == schemas and values_result == values
    assert keys == ["color", "travel_ready"]
    assert _callback_data(markup) == [
        "catalog:fields:edit:0", "catalog:fields:edit:1", "catalog:fields:create",
        "catalog:fields:back", "menu:main",
    ]
    monkeypatch.setattr(bot, "get_user_role", lambda _user_id: bot.Role.USER)
    assert _callback_data(bot.typed_fields_menu(200, "mac-a")[3]) == []
    monkeypatch.setattr(bot, "get_user_role", lambda _user_id: bot.Role.OWNER)

    registry["navigation"] = [
        row for row in registry["navigation"] if row["page_id"] != "catalog.fields.edit"
    ]
    monkeypatch.setattr(bot.feature_catalog, "load_registry", lambda: registry)
    assert _callback_data(bot.typed_fields_menu(100, "mac-a")[3]) == [
        "catalog:fields:create", "catalog:fields:back", "menu:main",
    ]


def test_saved_device_set_catalog_uses_registered_templates(monkeypatch):
    _setup(monkeypatch, {})
    monkeypatch.setattr(bot, "get_user_role", lambda _user_id: bot.Role.OWNER)
    monkeypatch.setattr(bot.access_store, "get_effective_grants", lambda _user_id: {"callbacks": []})
    snapshot_id = "a" * 32
    snapshots = [{"snapshot_id": snapshot_id, "name": "Travel kit", "device_ids": ["mac-a", "win-b"]}]
    monkeypatch.setattr(
        bot.devices, "list_selected_snapshots", lambda *, actor_id, owner_id: snapshots, raising=False
    )
    registry = bot.feature_catalog.load_registry()
    expected_templates = [
        "catalog:snapshot:start", "catalog:snapshot:view:*", "menu:devices", "menu:main",
    ]
    rows = [row for row in registry["navigation"] if row["parent"] == "catalog.snapshot.list"]

    assert [row["callback"] for row in rows] == expected_templates
    listed, markup = bot.device_snapshot_catalog(100)
    assert listed == snapshots
    assert _callback_data(markup) == [
        "catalog:snapshot:start", f"catalog:snapshot:view:{snapshot_id}", "menu:devices", "menu:main",
    ]

    monkeypatch.setattr(bot, "get_user_role", lambda _user_id: bot.Role.USER)
    assert _callback_data(bot.device_snapshot_catalog(200)[1]) == []

    registry["navigation"] = [
        row for row in registry["navigation"] if row["page_id"] != "catalog.snapshot.row"
    ]
    monkeypatch.setattr(bot.feature_catalog, "load_registry", lambda: registry)
    monkeypatch.setattr(bot, "get_user_role", lambda _user_id: bot.Role.OWNER)
    assert f"catalog:snapshot:view:{snapshot_id}" not in _callback_data(bot.device_snapshot_catalog(100)[1])


def test_inactivity_history_and_compare_screens_use_registered_templates(monkeypatch, tmp_path):
    Callback, rendered = _admin_fixture(monkeypatch, tmp_path)
    snapshots = [
        {"snapshot_id": "1" * 32, "captured_at": 1_800_000_000, "counts": {}},
        {"snapshot_id": "2" * 32, "captured_at": 1_800_000_100, "counts": {}},
    ]
    monkeypatch.setattr(bot.devices, "list_inactivity_snapshots", lambda *, limit: snapshots[:limit], raising=False)
    monkeypatch.setattr(bot.devices, "compare_inactivity_snapshots", lambda *_ids: {}, raising=False)
    registry = bot.feature_catalog.load_registry()
    history = [row for row in registry["navigation"] if row["parent"] == "catalog.inactivity.list"]
    compare = [row for row in registry["navigation"] if row["parent"] == "catalog.inactivity.compare"]

    assert [row["callback"] for row in history] == [
        "catalog:inactivity:capture", "catalog:inactivity:compare", "menu:devices", "menu:main",
    ]
    assert [row["callback"] for row in compare] == ["catalog:inactivity:list", "menu:main"]
    assert _callback_data(bot.inactivity_history_screen()[2]) == [
        "catalog:inactivity:capture", "catalog:inactivity:compare", "menu:devices", "menu:main",
    ]
    monkeypatch.setattr(
        bot, "get_user_role",
        lambda user_id: bot.Role.OWNER if user_id == 100 else bot.Role.USER,
    )
    assert _callback_data(bot.inactivity_history_screen(100)[2]) == [
        "catalog:inactivity:capture", "catalog:inactivity:compare", "menu:devices", "menu:main",
    ]
    assert _callback_data(bot.inactivity_history_screen(200)[2]) == []

    registry["navigation"] = [
        row for row in registry["navigation"] if row["page_id"] != "catalog.inactivity.compare"
    ]
    monkeypatch.setattr(bot.feature_catalog, "load_registry", lambda: registry)
    assert _callback_data(bot.inactivity_history_screen(100)[2]) == [
        "catalog:inactivity:capture", "menu:devices", "menu:main",
    ]

    async def scenario():
        await bot.on_inactivity_compare(Callback("catalog:inactivity:compare"))

    asyncio.run(scenario())
    assert _callback_data(rendered[-1][1]) == ["catalog:inactivity:list", "menu:main"]


def test_guest_device_list_uses_registry_template_and_home(monkeypatch):
    _setup(monkeypatch, {
        "device-1": {"name": "Guest PC", "os": "Windows", "online": True},
    })
    monkeypatch.setattr(bot, "get_user_role", lambda _user_id: bot.Role.GUEST)
    registry = bot.feature_catalog.load_registry()
    expected = ["guest:readonly", "menu:main"]
    rows = [row for row in registry["navigation"] if row["parent"] == "guest.devices"]

    assert [row["callback"] for row in rows] == expected
    assert _callback_data(bot.guest_devices_menu()) == expected

    registry["navigation"] = [
        row for row in registry["navigation"] if row["page_id"] != "guest.devices.row"
    ]
    monkeypatch.setattr(bot.feature_catalog, "load_registry", lambda: registry)
    assert _callback_data(bot.guest_devices_menu()) == ["menu:main"]


def test_admin_users_menu_uses_owner_registry_templates(monkeypatch):
    monkeypatch.setattr(
        bot.access_store, "list_users",
        lambda: [{"id": 42, "username": "blocked_user", "display_name": ""}],
    )
    monkeypatch.setattr(bot.access_store, "get_role", lambda _uid, _owner: bot.Role.BLOCKED)
    monkeypatch.setattr(bot, "get_user_role", lambda uid: bot.Role.OWNER if uid == bot.ADMIN_ID else bot.Role.USER)
    registry = bot.feature_catalog.load_registry()
    expected = ["admin:user:*", "noop", "menu:admin"]
    rows = [row for row in registry["navigation"] if row["parent"] == "admin.users"]

    assert [row["callback"] for row in rows] == expected
    assert _callback_data(bot.admin_users_menu()) == ["admin:user:42", "menu:admin"]
    assert _callback_data(bot.admin_users_menu(user_id=200)) == []

    registry["navigation"] = [
        row for row in registry["navigation"] if row["page_id"] != "admin.users.row"
    ]
    monkeypatch.setattr(bot.feature_catalog, "load_registry", lambda: registry)
    assert _callback_data(bot.admin_users_menu()) == ["menu:admin"]


def test_admin_user_detail_actions_use_owner_registry_templates(monkeypatch):
    monkeypatch.setattr(bot, "ADMIN_ID", 100)
    monkeypatch.setattr(bot, "get_user_role", lambda uid: bot.Role.OWNER if uid == 100 else bot.Role.BLOCKED)
    monkeypatch.setattr(bot.access_store, "get_role", lambda uid, _owner: bot.Role.BLOCKED if uid == 200 else bot.Role.OWNER)
    registry = bot.feature_catalog.load_registry()
    expected = [
        "admin:role:*", "admin:block:*", "admin:devices:*", "admin:perms:*",
        "admin:blocktemp:*", "admin:message:*", "admin:role-history:*", "admin:grant-history:*", "admin:revoke-user:*", "admin:pause-user:*", "admin:owner-invariant", "admin:policy-snapshot", "admin:policy-compare", "admin:grant-undo:*", "admin:users",
    ]
    expected.insert(10, "admin:profiles:*")
    rows = [row for row in registry["navigation"] if row["parent"] == "admin.user.detail"]

    assert [row["callback"] for row in rows] == expected
    assert _callback_data(bot.admin_user_menu(200, actor_id=100)) == [
        "admin:role:200:user", "admin:role:200:guest", "admin:block:200",
        "admin:devices:200", "admin:perms:200", "admin:blocktemp:200",
            "admin:message:200", "admin:role-history:200", "admin:grant-history:200", "admin:revoke-user:200", "admin:pause-user:200", "admin:profiles:200", "admin:grant-undo:200", "admin:users",
    ]
    assert _callback_data(bot.admin_user_menu(200, actor_id=300)) == []
    assert _callback_data(bot.admin_user_menu(100, actor_id=100)) == ["admin:owner-invariant", "admin:policy-snapshot", "admin:policy-compare", "admin:users"]


def test_admin_user_detail_has_owner_only_role_history_route(monkeypatch):
    monkeypatch.setattr(bot, "ADMIN_ID", 100)
    monkeypatch.setattr(bot, "get_user_role", lambda uid: bot.Role.OWNER if uid == 100 else bot.Role.GUEST)
    registry = bot.feature_catalog.load_registry()
    rows = [row for row in registry["navigation"] if row["parent"] == "admin.user.detail"]

    assert any(row["callback"] == "admin:role-history:*" and row["roles"] == ["owner"] for row in rows)
    assert "admin:role-history:200" in _callback_data(bot.admin_user_menu(200, actor_id=100))
    assert "admin:role-history:200" not in _callback_data(bot.admin_user_menu(200, actor_id=200))


def test_admin_role_history_renders_only_target_role_events_on_same_card(monkeypatch, tmp_path):
    Callback, rendered = _admin_fixture(monkeypatch, tmp_path)
    monkeypatch.setattr(bot.access_store, "get_role_history", lambda actor, target, owner, limit=20: [
        {"at": 100, "event": "role_set", "role": bot.Role.USER, "actor_id": 100},
        {"at": 90, "event": "user_block_expired", "role": "unknown", "actor_id": None},
    ])

    asyncio.run(bot.on_admin_role_history(Callback("admin:role-history:200")))

    assert "Журнал роли одного пользователя" in rendered[-1][0]
    assert "Пользователь" in rendered[-1][0]
    assert "роль на тот момент неизвестна" in rendered[-1][0]
    assert _callback_data(rendered[-1][1]) == ["admin:user:200"]


def test_admin_user_detail_has_owner_only_grant_history_route(monkeypatch):
    monkeypatch.setattr(bot, "ADMIN_ID", 100)
    monkeypatch.setattr(bot, "get_user_role", lambda uid: bot.Role.OWNER if uid == 100 else bot.Role.GUEST)
    registry = bot.feature_catalog.load_registry()
    rows = [row for row in registry["navigation"] if row["parent"] == "admin.user.detail"]

    assert any(row["callback"] == "admin:grant-history:*" and row["roles"] == ["owner"] for row in rows)
    assert "admin:grant-history:200" in _callback_data(bot.admin_user_menu(200, actor_id=100))
    assert "admin:grant-history:200" not in _callback_data(bot.admin_user_menu(200, actor_id=200))


def test_admin_user_detail_exposes_owner_only_confirmed_revoke_all_grants(monkeypatch, tmp_path):
    monkeypatch.setattr(bot, "ADMIN_ID", 100)
    monkeypatch.setattr(bot, "get_user_role", lambda uid: bot.Role.OWNER if uid == 100 else bot.Role.GUEST)
    registry = bot.feature_catalog.load_registry()
    rows = [row for row in registry["navigation"] if row["parent"] == "admin.user.detail"]

    assert any(row["callback"] == "admin:revoke-user:*" and row["roles"] == ["owner"] for row in rows)
    assert "admin:revoke-user:200" in _callback_data(bot.admin_user_menu(200, actor_id=100))
    assert "admin:revoke-user:200" not in _callback_data(bot.admin_user_menu(200, actor_id=200))


def test_owner_revokes_all_user_grants_through_one_use_confirmation(monkeypatch, tmp_path):
    from types import SimpleNamespace
    _Callback, rendered = _admin_fixture(monkeypatch, tmp_path)
    assert bot.access_store.set_role(100, 200, bot.Role.USER, 100)
    assert bot.access_store.toggle_device(100, 200, "mac-a", 100)
    assert bot.access_store.toggle_callback(100, 200, "cmd:status", 100)

    class Callback:
        def __init__(self, data, actor=100):
            self.data = data
            self.from_user = SimpleNamespace(id=actor)
        async def answer(self, *args, **kwargs):
            self.answered = args

    async def scenario():
        await bot.on_admin_revoke_user_grants(Callback("admin:revoke-user:200"))
        assert "cmd:status" in rendered[-1][0]
        assert "mac-a" in rendered[-1][0]
        confirm_data = next(
            data for data in _callback_data(rendered[-1][1])
            if data.startswith("admin:confirm:")
        )
        await bot.on_admin_confirm(Callback(confirm_data))

    asyncio.run(scenario())

    assert bot.access_store.get_effective_grants(200) == {"callbacks": [], "devices": []}
    assert bot.access_store.get_role(200, 100) == bot.Role.USER
    assert "отозваны" in rendered[-1][0].lower()


def test_revoke_all_preview_lists_stored_expired_grants_separately(monkeypatch, tmp_path):
    Callback, rendered = _admin_fixture(monkeypatch, tmp_path)
    assert bot.access_store.set_role(100, 200, bot.Role.USER, 100)
    monkeypatch.setattr(bot.time, "time", lambda: 100)
    assert bot.access_store.toggle_callback(100, 200, "cmd:status", 100, expires_at=200)
    monkeypatch.setattr(bot.time, "time", lambda: 201)

    asyncio.run(bot.on_admin_revoke_user_grants(Callback("admin:revoke-user:200")))

    assert "cmd:status" in rendered[-1][0]
    assert "действуют: устройств 0; действий 0" in rendered[-1][0]


def test_non_owner_cannot_preview_all_grant_revocation(monkeypatch, tmp_path):
    from types import SimpleNamespace
    _Callback, rendered = _admin_fixture(monkeypatch, tmp_path)
    assert bot.access_store.set_role(100, 200, bot.Role.USER, 100)
    assert bot.access_store.toggle_callback(100, 200, "cmd:status", 100)

    class Callback:
        data = "admin:revoke-user:200"
        from_user = SimpleNamespace(id=200)
        async def answer(self, *args, **kwargs):
            self.answered = args

    callback = Callback()
    asyncio.run(bot.on_admin_revoke_user_grants(callback))

    assert bot.access_store.get_effective_grants(200)["callbacks"] == ["cmd:status"]
    assert callback.answered and callback.answered[0]


def test_admin_user_detail_exposes_owner_only_confirmed_grant_pause(monkeypatch, tmp_path):
    monkeypatch.setattr(bot, "ADMIN_ID", 100)
    monkeypatch.setattr(bot, "get_user_role", lambda uid: bot.Role.OWNER if uid == 100 else bot.Role.GUEST)
    registry = bot.feature_catalog.load_registry()
    rows = [row for row in registry["navigation"] if row["parent"] == "admin.user.detail"]

    assert any(row["callback"] == "admin:pause-user:*" and row["roles"] == ["owner"] for row in rows)
    assert "admin:pause-user:200" in _callback_data(bot.admin_user_menu(200, actor_id=100))
    assert "admin:pause-user:200" not in _callback_data(bot.admin_user_menu(200, actor_id=200))


def test_owner_pauses_and_resumes_user_grants_through_exact_one_use_preview(monkeypatch, tmp_path):
    from types import SimpleNamespace
    Callback, rendered = _admin_fixture(monkeypatch, tmp_path)
    assert bot.access_store.set_role(100, 200, bot.Role.USER, 100)
    assert bot.access_store.toggle_device(100, 200, "a", 100)
    assert bot.access_store.toggle_callback(100, 200, "cmd:status", 100)

    async def scenario():
        await bot.on_admin_pause_user_grants(Callback("admin:pause-user:200"))
        assert "приостанов" in rendered[-1][0].lower()
        assert "cmd:status" in rendered[-1][0]
        confirm_data = next(data for data in _callback_data(rendered[-1][1]) if data.startswith("admin:confirm:"))
        await bot.on_admin_confirm(Callback(confirm_data))
        assert bot.access_store.get_effective_grants(200) == {"callbacks": [], "devices": []}
        assert bot.access_store.get_user(200)["permissions_paused"] is True

        await bot.on_admin_pause_user_grants(Callback("admin:pause-user:200"))
        assert "возобнов" in rendered[-1][0].lower()
        confirm_data = next(data for data in _callback_data(rendered[-1][1]) if data.startswith("admin:confirm:"))
        await bot.on_admin_confirm(Callback(confirm_data))
        assert bot.access_store.get_effective_grants(200) == {"callbacks": ["cmd:status"], "devices": ["a"]}
        assert bot.access_store.get_user(200)["permissions_paused"] is False

    asyncio.run(scenario())


def test_non_owner_cannot_pause_user_grants(monkeypatch, tmp_path):
    from types import SimpleNamespace
    _Callback, rendered = _admin_fixture(monkeypatch, tmp_path)
    assert bot.access_store.set_role(100, 200, bot.Role.USER, 100)
    assert bot.access_store.toggle_callback(100, 200, "cmd:status", 100)

    class Callback:
        data = "admin:pause-user:200"
        from_user = SimpleNamespace(id=200)
        async def answer(self, *args, **kwargs):
            self.answered = args

    callback = Callback()
    asyncio.run(bot.on_admin_pause_user_grants(callback))

    assert bot.access_store.get_effective_grants(200)["callbacks"] == ["cmd:status"]
    assert callback.answered and callback.answered[0]
    assert not rendered


def test_resume_preview_warns_that_missing_device_grants_need_reissue(monkeypatch, tmp_path):
    Callback, rendered = _admin_fixture(monkeypatch, tmp_path)
    assert bot.access_store.set_role(100, 200, bot.Role.USER, 100)
    assert bot.access_store.toggle_device(100, 200, "a", 100)
    assert bot.access_store.toggle_device(100, 200, "old-device", 100)
    pause = bot.access_store.preview_user_grant_pause(100, 200, 100, available_device_ids={"a"}, now=2_000)
    assert bot.access_store.apply_user_grant_pause(
        100, 200, 100, paused=True, available_device_ids={"a"},
        expected_snapshot=pause["snapshot"], now=2_000,
    )

    async def scenario():
        await bot.on_admin_pause_user_grants(Callback("admin:pause-user:200"))
        assert "old-device" in rendered[-1][0]
        assert "потребуется выдать заново" in rendered[-1][0].lower()
        confirm_data = next(data for data in _callback_data(rendered[-1][1]) if data.startswith("admin:confirm:"))
        await bot.on_admin_confirm(Callback(confirm_data))

    asyncio.run(scenario())
    assert bot.access_store.get_user(200)["permissions"]["devices"] == ["a"]


def test_access_navigation_diff_reports_added_and_removed_buttons(monkeypatch):
    registry = {"navigation": [{"parent": "main"}, {"parent": "screen"}]}
    monkeypatch.setattr(bot.feature_catalog, "load_registry", lambda: registry)

    def visible(_registry, *, parent, role, grants, platform):
        assert platform == "telegram"
        if parent == "main" and role in {bot.Role.USER, bot.Role.GUEST}:
            return [{"page_id": "main.devices", "label_id": "my_devices"}]
        if parent == "screen" and role == bot.Role.USER and "cmd:screenshot" in grants:
            return [{"page_id": "screen.capture", "label_id": "screen_capture"}]
        return []

    monkeypatch.setattr(bot.feature_catalog, "visible_navigation_pages", visible)

    added = bot.access_navigation_diff(bot.Role.GUEST, set(), bot.Role.USER, {"cmd:screenshot"})
    removed = bot.access_navigation_diff(bot.Role.USER, {"cmd:screenshot"}, bot.Role.GUEST, set())

    assert added == {"added": ["screen.capture"], "removed": []}
    assert removed == {"added": [], "removed": ["screen.capture"]}


def test_owner_invariant_report_detects_owner_substitution_and_stored_block():
    healthy = bot.owner_invariant_report(
        configured_owner_id=100, effective_owner_id=100,
        stored_record={"id": 100, "role": "guest", "blocked": False},
    )
    unhealthy = bot.owner_invariant_report(
        configured_owner_id=100, effective_owner_id=200,
        stored_record={"id": 100, "role": "blocked", "blocked": True},
    )

    assert healthy == {
        "ok": True, "configured_owner_id": 100, "effective_owner_id": 100,
        "stored_record_present": True, "issues": [],
    }
    assert unhealthy["ok"] is False
    assert unhealthy["issues"] == ["runtime_owner_mismatch", "stored_owner_blocked"]


def test_owner_invariant_is_owner_only_in_selected_user_menu(monkeypatch):
    monkeypatch.setattr(bot, "ADMIN_ID", 100)
    monkeypatch.setattr(bot, "get_user_role", lambda uid: bot.Role.OWNER if uid == 100 else bot.Role.USER)

    registry = bot.feature_catalog.load_registry()
    rows = [row for row in registry["navigation"] if row["parent"] == "admin.user.detail"]
    assert any(row["callback"] == "admin:owner-invariant" and row["roles"] == ["owner"] for row in rows)
    assert "admin:owner-invariant" in _callback_data(bot.admin_user_menu(100, actor_id=100))
    assert "admin:owner-invariant" not in _callback_data(bot.admin_user_menu(100, actor_id=200))
    assert "admin:owner-invariant" not in _callback_data(bot.admin_user_menu(200, actor_id=100))


def test_owner_invariant_handler_reports_only_owner_configuration_state(monkeypatch, tmp_path):
    Callback, rendered = _admin_fixture(monkeypatch, tmp_path)
    monkeypatch.setattr(bot.access_store, "get_user", lambda uid: {
        "id": 100, "role": bot.Role.BLOCKED, "blocked": True,
    } if uid == 100 else None)

    asyncio.run(bot.on_admin_owner_invariant(Callback("admin:owner-invariant")))

    assert "ID конфигурации" in rendered[-1][0]
    assert "ID текущего владельца" in rendered[-1][0]
    assert "⚠️" in rendered[-1][0]
    assert "stored_owner_blocked" not in rendered[-1][0]
    assert _callback_data(rendered[-1][1]) == ["admin:users"]


def test_rbac_policy_snapshot_is_owner_only_and_creates_same_card_receipt(monkeypatch, tmp_path):
    Callback, rendered = _admin_fixture(monkeypatch, tmp_path)
    monkeypatch.setattr(bot, "get_user_role", lambda uid: bot.Role.OWNER if uid == 100 else bot.Role.USER)

    assert "admin:policy-snapshot" in _callback_data(bot.admin_user_menu(100, actor_id=100))
    assert "admin:policy-snapshot" not in _callback_data(bot.admin_user_menu(200, actor_id=100))
    asyncio.run(bot.on_admin_policy_snapshot(Callback("admin:policy-snapshot")))

    assert "RBAC" in rendered[-1][0]
    assert "SHA-256" in rendered[-1][0]
    assert "Схема" in rendered[-1][0]
    assert "Записей" in rendered[-1][0]
    assert _callback_data(rendered[-1][1]) == ["admin:users"]


def test_rbac_policy_comparison_is_owner_only_and_reports_latest_two_on_same_card(monkeypatch, tmp_path):
    Callback, rendered = _admin_fixture(monkeypatch, tmp_path)
    monkeypatch.setattr(bot, "get_user_role", lambda uid: bot.Role.OWNER if uid == 100 else bot.Role.USER)
    monkeypatch.setattr(bot.access_store, "list_rbac_policy_snapshots", lambda actor, owner, limit=10: [
        {"snapshot_id": "b" * 24, "created_at": 200, "entry_count": 3, "schema_version": 1},
        {"snapshot_id": "a" * 24, "created_at": 100, "entry_count": 2, "schema_version": 1},
    ])
    monkeypatch.setattr(bot.access_store, "compare_rbac_policy_snapshots", lambda actor, owner, left, right: {
        "from_snapshot_id": left, "to_snapshot_id": right,
        "from_created_at": 100, "to_created_at": 200,
        "changes": [{"user_id": 200, "role": {"before": "guest", "after": "user"},
                     "stored_callbacks": {"added": ["cmd:status"], "removed": []}}],
    })

    assert "admin:policy-compare" in _callback_data(bot.admin_user_menu(100, actor_id=100))
    assert "admin:policy-compare" not in _callback_data(bot.admin_user_menu(200, actor_id=100))
    asyncio.run(bot.on_admin_policy_compare(Callback("admin:policy-compare")))

    assert "Сравнение снимков RBAC" in rendered[-1][0]
    assert "guest → user" in rendered[-1][0]
    assert "cmd:status" in rendered[-1][0]
    assert _callback_data(rendered[-1][1]) == ["admin:users"]


def test_admin_grant_undo_requires_preview_then_one_use_confirmation(monkeypatch, tmp_path):
    Callback, rendered = _admin_fixture(monkeypatch, tmp_path)
    assert bot.access_store.set_role(100, 200, bot.Role.USER, 100)
    assert bot.access_store.toggle_callback(100, 200, "cmd:status", 100) is True
    ref = bot.access_store.get_latest_grant_change(100, 200, 100)["reference"]

    asyncio.run(bot.on_admin_grant_undo(Callback("admin:grant-undo:200")))

    assert "Последнее изменение grants" in rendered[-1][0]
    assert "cmd:status" in rendered[-1][0]
    confirm = _callback_data(rendered[-1][1])[0]
    assert confirm == f"admin:grant-undo-confirm:200:{ref}"
    asyncio.run(bot.on_admin_grant_undo_confirm(Callback(confirm)))

    assert "отменено" in rendered[-1][0].lower()
    assert "cmd:status" not in bot.access_store.get_effective_grants(200)["callbacks"]


def test_admin_grant_history_renders_typed_access_events_on_same_card(monkeypatch, tmp_path):
    Callback, rendered = _admin_fixture(monkeypatch, tmp_path)
    monkeypatch.setattr(bot.access_store, "get_grant_history", lambda actor, target, owner, limit=30: [
        {"at": 100, "kind": "callback", "item": "cmd:status", "action": "revoked", "actor_id": 100, "expires_at": None},
        {"at": 90, "kind": "device", "item": "MacBook-Air.local", "action": "granted", "actor_id": 100, "expires_at": 400},
    ])

    asyncio.run(bot.on_admin_grant_history(Callback("admin:grant-history:200")))

    assert "История grants" in rendered[-1][0]
    assert "cmd:status" in rendered[-1][0]
    assert "истёк" in rendered[-1][0]
    assert _callback_data(rendered[-1][1]) == ["admin:user:200"]


def test_temporary_user_block_requires_reason_preview_and_one_use_confirmation(monkeypatch, tmp_path):
    Callback, rendered = _admin_fixture(monkeypatch, tmp_path)

    class State:
        def __init__(self):
            self.data = {}
            self.current = None
        async def set_state(self, value):
            self.current = value
        async def update_data(self, **values):
            self.data.update(values)
        async def get_data(self):
            return dict(self.data)
        async def clear(self):
            self.data.clear()
            self.current = None

    class Message:
        def __init__(self, actor, text):
            from types import SimpleNamespace
            self.from_user = SimpleNamespace(id=actor)
            self.text = text

    monkeypatch.setattr(bot, "Message", Message)
    state = State()
    async def scenario():
        await bot.on_admin_temporary_block(Callback("admin:blocktemp:200"), state)
        choices = _callback_data(rendered[-1][1])
        assert "admin:blocktemp:200:3600" in choices
        await bot.on_admin_temporary_block(Callback("admin:blocktemp:200:3600"), state)
        assert state.current == bot.Form.wait_block_reason
        await bot.on_admin_temporary_block_reason(Message(100, "spam review"), state)
        assert state.current is None
        assert bot.access_store.get_role(200, 100) == bot.Role.GUEST
        assert "spam review" in rendered[-1][0]
        assert "1 час" in rendered[-1][0]
        assert "После срока автоматически вернётся роль Гость" in rendered[-1][0]
        confirmation = next(
            value for value in _callback_data(rendered[-1][1])
            if value.startswith("admin:confirm:")
        )
        await bot.on_admin_confirm(Callback(confirmation))
        assert bot.access_store.get_role(200, 100) == bot.Role.BLOCKED
        record = bot.access_store.get_user(200)
        assert record["block_reason"] == "spam review"
        assert record["blocked_until"] > int(bot.time.time())
        await bot.on_admin_confirm(Callback(confirmation))
        assert bot.access_store.get_user(200) == record
    asyncio.run(scenario())


def test_guest_navigation_shows_only_safe_device_and_server_overview_controls(monkeypatch):
    _setup(monkeypatch, {
        "device-1": {
            "name": "Guest-visible PC", "os": "Windows", "version": "4.1.0",
            "location": {"lat": 51.5, "lon": -0.1},
        },
    })
    monkeypatch.setattr(bot, "get_user_role", lambda _user_id: bot.Role.GUEST)

    guest_devices = _callback_data(bot.guest_devices_menu())
    guest_server = _callback_data(bot.server_menu(300))
    overview = bot.server_overview_text(approval=True, connected=True)

    assert guest_devices == ["guest:readonly", "menu:main"]
    assert guest_server == ["menu:main"]
    assert "51.5" not in overview and "-0.1" not in overview
    assert not any(
        callback.startswith(("cmd:", "files:", "server:", "versions:"))
        for callback in [*guest_devices, *guest_server]
    )


def test_process_results_filter_kill_controls_and_keep_granted_pagination(monkeypatch):
    from types import SimpleNamespace

    _setup(monkeypatch, {"device-1": {"name": "PC", "os": "Windows"}})
    monkeypatch.setattr(bot, "SESSION", {"target": "device-1"})
    monkeypatch.setattr(bot, "get_user_role", lambda user_id: bot.Role.USER if user_id == 200 else bot.Role.OWNER)
    monkeypatch.setattr(
        bot.access_store, "can_use_callback",
        lambda user_id, callback, _owner_id, target=None: (
            user_id == 100 or (
                user_id == 200 and target == "device-1" and (
                    callback in {"proc:0", "back:device", "menu:main"}
                    or callback == "proc:refresh"
                    or callback == "proc:1"
                )
            )
        ),
    )
    monkeypatch.setattr(bot, "LAST_PROCESSES", {
        "device-1": {
            "ts": time.time(),
            "items": [{"pid": pid, "name": f"process-{pid}", "mem_percent": 1} for pid in range(10)],
        },
    })
    rendered = []

    class FakeCallback:
        data = "proc:0"
        from_user = SimpleNamespace(id=200)

        async def answer(self, *args, **kwargs):
            pass

    async def replace(_cq, text, reply_markup=None, **_kwargs):
        rendered.append((text, reply_markup))

    monkeypatch.setattr(bot, "_replace_callback_message", replace)
    asyncio.run(bot.on_proc_page(FakeCallback()))

    callbacks = set(_callback_data(rendered[-1][1]))
    assert "process-0" in rendered[-1][0]
    assert "<code>0</code>" in rendered[-1][0]
    assert "1%" in rendered[-1][0]
    assert "process-8" not in rendered[-1][0]
    assert "proc:1" in callbacks
    assert "proc:refresh" in callbacks
    assert "back:device" in callbacks
    assert "menu:main" in callbacks
    assert not any(callback.startswith("killq:") for callback in callbacks)


def test_process_details_remain_visible_with_only_one_specific_kill_grant(monkeypatch):
    from types import SimpleNamespace

    _setup(monkeypatch, {"device-1": {"name": "PC", "os": "Windows"}})
    monkeypatch.setattr(bot, "SESSION", {"target": "device-1"})
    monkeypatch.setattr(bot, "get_user_role", lambda _user_id: bot.Role.USER)
    monkeypatch.setattr(
        bot.access_store, "can_use_callback",
        lambda user_id, callback, _owner_id, target=None: (
            user_id == 200 and target == "device-1" and callback in {
                "proc:0", "killq:42", "kill:42", "back:device", "menu:main",
            }
        ),
    )
    monkeypatch.setattr(bot, "LAST_PROCESSES", {
        "device-1": {
            "ts": time.time(),
            "items": [
                {"pid": 42, "name": "worker-alpha", "mem_percent": 4.5},
                {"pid": 43, "name": "worker-beta", "mem_percent": 2.0},
            ],
        },
    })
    rendered = []

    class FakeCallback:
        data = "proc:0"
        from_user = SimpleNamespace(id=200)

        async def answer(self, *args, **kwargs):
            pass

    async def replace(_cq, text, reply_markup=None, **_kwargs):
        rendered.append((text, reply_markup))

    monkeypatch.setattr(bot, "_replace_callback_message", replace)
    asyncio.run(bot.on_proc_page(FakeCallback()))

    text, markup = rendered[-1]
    callbacks = set(_callback_data(markup))
    assert "worker-alpha" in text and "worker-beta" in text
    assert "<code>42</code>" in text and "4.5%" in text
    assert "killq:42" in callbacks
    assert "killq:43" not in callbacks


def test_narrow_input_grant_is_reachable_from_terminal_section(monkeypatch, tmp_path):
    from types import SimpleNamespace
    from roles import AdminFilter

    _setup(monkeypatch, {"device-1": {"name": "PC", "os": "Windows"}})
    monkeypatch.setattr(bot, "ADMIN_ID", 100)
    monkeypatch.setattr(bot, "SESSION", {"target": "device-1"})
    monkeypatch.setattr(bot.access_store, "FILE", tmp_path / "access.json")
    monkeypatch.setattr(bot.access_store, "AUDIT_FILE", tmp_path / "audit.jsonl")
    owner_id, user_id = int(bot.ADMIN_ID), 200
    bot.access_store.register_start(SimpleNamespace(
        id=user_id, username="narrow", first_name="Narrow", last_name="User",
    ))
    assert bot.access_store.set_role(owner_id, user_id, bot.Role.USER, owner_id)
    assert bot.access_store.toggle_device(owner_id, user_id, "device-1", owner_id)
    assert bot.access_store.toggle_callback(owner_id, user_id, "cmd:clipset", owner_id)
    monkeypatch.setattr(
        bot, "get_user_role",
        lambda current_user: bot.access_store.get_role(current_user, owner_id),
    )

    rendered = []

    class FakeCallback:
        from_user = SimpleNamespace(id=user_id)

        def __init__(self, data):
            self.data = data

        async def answer(self, *args, **kwargs):
            pass

    async def replace(_cq, _text, reply_markup=None):
        rendered.append(reply_markup)

    monkeypatch.setattr(bot, "_replace_callback_message", replace)
    device_callbacks = set(_callback_data(bot.device_menu("device-1", user_id)))
    assert "cat:terminal" in device_callbacks
    assert "cat:input" not in device_callbacks

    terminal_callback = FakeCallback("cat:terminal")
    assert asyncio.run(AdminFilter()(terminal_callback)) is True
    asyncio.run(bot.on_cat_terminal(terminal_callback))
    terminal_callbacks = set(_callback_data(rendered[-1]))
    assert "cat:input" in terminal_callbacks

    input_callback = FakeCallback("cat:input")
    assert asyncio.run(AdminFilter()(input_callback)) is True
    asyncio.run(bot.on_cat_input(input_callback))
    input_callbacks = set(_callback_data(rendered[-1]))
    assert "cmd:clipset" in input_callbacks
    assert "cmd:clipboard" not in input_callbacks
    assert "fun:type" not in input_callbacks
    assert "fun:hotkey" not in input_callbacks
    assert asyncio.run(AdminFilter()(FakeCallback("cmd:clipset"))) is True
    assert asyncio.run(AdminFilter()(FakeCallback("cmd:clipboard"))) is False
    assert asyncio.run(AdminFilter()(FakeCallback("fun:type"))) is False


def test_process_refresh_rechecks_grant_immediately_before_publish(monkeypatch):
    from types import SimpleNamespace

    _setup(monkeypatch, {"device-1": {"name": "PC", "os": "Windows"}})
    monkeypatch.setattr(bot, "SESSION", {"target": "device-1"})
    checks = []
    published = []
    rendered = []

    def still_allowed(user_id, callback, target):
        checks.append((user_id, callback, target))
        return len(checks) == 1

    class FakeCallback:
        data = "proc:refresh"
        from_user = SimpleNamespace(id=200)

        async def answer(self, *args, **kwargs):
            pass

    class FakeCollector:
        def reset(self):
            pass

        async def wait(self, _timeout):
            return {"top": []}

    async def replace(_cq, text, reply_markup=None, **_kwargs):
        rendered.append((text, reply_markup))

    monkeypatch.setattr(bot, "_device_action_still_allowed", still_allowed)
    monkeypatch.setattr(bot, "processes_collector", FakeCollector())
    monkeypatch.setattr(bot, "_replace_callback_message", replace)
    monkeypatch.setattr(
        bot.transport, "publish_command",
        lambda *args, **kwargs: published.append((args, kwargs)) or True,
    )

    asyncio.run(bot.on_proc_page(FakeCallback()))

    assert checks == [
        (200, "proc:refresh", "device-1"),
        (200, "proc:refresh", "device-1"),
    ]
    assert published == []
    assert rendered


def test_process_refresh_hides_results_if_grant_is_revoked_while_waiting(monkeypatch):
    from types import SimpleNamespace

    _setup(monkeypatch, {"device-1": {"name": "PC", "os": "Windows"}})
    monkeypatch.setattr(bot, "SESSION", {"target": "device-1"})
    checks = []
    rendered = []

    def still_allowed(user_id, callback, target):
        checks.append((user_id, callback, target))
        return len(checks) < 3

    class FakeCallback:
        data = "proc:refresh"
        from_user = SimpleNamespace(id=200)

        async def answer(self, *args, **kwargs):
            pass

    class FakeCollector:
        def reset(self):
            pass

        async def wait(self, _timeout):
            return {"top": [{"pid": 42, "name": "secret-process", "mem_percent": 1}]}

    async def replace(_cq, text, reply_markup=None, **_kwargs):
        rendered.append((text, reply_markup))

    monkeypatch.setattr(bot, "_device_action_still_allowed", still_allowed)
    monkeypatch.setattr(bot, "processes_collector", FakeCollector())
    monkeypatch.setattr(bot, "_replace_callback_message", replace)
    monkeypatch.setattr(bot.transport, "publish_command", lambda *_args, **_kwargs: True)

    asyncio.run(bot.on_proc_page(FakeCallback()))

    assert checks == [(200, "proc:refresh", "device-1")] * 3
    assert len(rendered) == 1
    assert "secret-process" not in rendered[0][0]
    assert bot._lex("action_result_hidden_after_revoke").lower() in rendered[0][0].lower()


def test_admin_menu_pairs_access_audit_and_text_controls(monkeypatch):
    _setup(monkeypatch, {})
    admin_rows = [
        [button.callback_data for button in row]
        for row in bot.admin_menu().inline_keyboard
    ]
    assert admin_rows == [
        ["admin:users", "admin:audit"],
        ["admin:stale-grants", "admin:access-review"],
        ["admin:access-requests", "admin:bulk-grant"],
        ["admin:bulk-revoke", "admin:texts"],
        ["admin:style", "ev:menu"],
        ["menu:server", "menu:main"],
    ]


def test_bulk_grant_admin_entry_uses_registry_label_and_exact_callback(monkeypatch):
    _setup(monkeypatch, {})
    buttons = {
        button.callback_data: button.text
        for row in bot.admin_menu().inline_keyboard
        for button in row
    }
    assert buttons["admin:bulk-grant"] == "👥 Массовая выдача прав"
    assert buttons["admin:bulk-revoke"] == "🧹 Массовый отзыв прав"


def test_owner_permission_request_queue_reviews_and_applies_exact_expiring_grant(monkeypatch, tmp_path):
    from types import SimpleNamespace
    _Callback, rendered = _admin_fixture(monkeypatch, tmp_path)
    _setup(monkeypatch, {"mac-a": {"name": "Mac"}})
    assert bot.access_store.set_role(100, 200, bot.Role.USER, 100)
    request = bot.access_store.submit_permission_request(
        200, 100, "callback", "cmd:screenshot", available_device_ids={"mac-a"},
    )
    assert request

    class Callback:
        def __init__(self, data, actor=100):
            self.data = data
            self.from_user = SimpleNamespace(id=actor)
        async def answer(self, *args, **kwargs):
            pass

    queue = _callback_data(bot.admin_permission_requests_keyboard(100))
    assert f"admin:access-request:view:{request['request_id']}" in queue
    detail = _callback_data(bot.admin_permission_request_detail_keyboard(100, request["request_id"]))
    assert f"admin:access-request:approve:{request['request_id']}" in detail
    assert f"admin:access-request:reject:{request['request_id']}" in detail

    async def scenario():
        await bot.on_admin_permission_request_view(Callback(f"admin:access-request:view:{request['request_id']}"))
        assert "cmd:screenshot" in rendered[-1][0]
        await bot.on_admin_permission_request_decision(Callback(f"admin:access-request:approve:{request['request_id']}"))

    asyncio.run(scenario())

    assert bot.access_store.get_effective_grants(200)["callbacks"] == ["cmd:screenshot"]
    decided = bot.access_store._load()["permission_requests"][request["request_id"]]
    assert decided["status"] == "approved"
    assert type(decided["grant_expires_at"]) is int
    assert "24" in rendered[-1][0]


def test_non_owner_cannot_decide_permission_request_through_callback(monkeypatch, tmp_path):
    from types import SimpleNamespace
    _admin_fixture(monkeypatch, tmp_path)
    assert bot.access_store.set_role(100, 200, bot.Role.USER, 100)
    request = bot.access_store.submit_permission_request(
        200, 100, "callback", "cmd:screenshot", available_device_ids=set(),
    )

    class Callback:
        data = f"admin:access-request:approve:{request['request_id']}"
        from_user = SimpleNamespace(id=200)
        async def answer(self, *args, **kwargs):
            self.answered = args

    asyncio.run(bot.on_admin_permission_request_decision(Callback()))
    assert bot.access_store.get_effective_grants(200)["callbacks"] == []
    assert bot.access_store.list_permission_requests(100, 100)


def test_bulk_grant_input_stages_exact_allowlisted_targets_before_confirmation(monkeypatch, tmp_path):
    from types import SimpleNamespace
    _Callback, rendered = _admin_fixture(monkeypatch, tmp_path)
    assert bot.access_store.set_role(100, 200, bot.Role.USER, 100)
    staged = []

    async def stage(message, action, user_id=0, value=None):
        staged.append((action, user_id, value))

    class State:
        async def clear(self):
            self.cleared = True

    message = SimpleNamespace(
        from_user=SimpleNamespace(id=100), text="200 | cmd:status | a",
    )
    state = State()
    monkeypatch.setattr(bot, "_admin_stage", stage)
    asyncio.run(bot.on_admin_bulk_grant_text(message, state))

    assert staged == [("bulk_grant", 0, {
        "targets": [200], "callbacks": ["cmd:status"], "devices": ["a"],
    })]
    assert state.cleared is True
    assert rendered == []


def test_bulk_grant_input_accepts_an_expiry_choice_before_preview(monkeypatch, tmp_path):
    from types import SimpleNamespace
    _Callback, rendered = _admin_fixture(monkeypatch, tmp_path)
    assert bot.access_store.set_role(100, 200, bot.Role.USER, 100)
    staged = []

    async def stage(message, action, user_id=0, value=None):
        staged.append((action, user_id, value))

    class State:
        async def clear(self):
            self.cleared = True

    before = int(bot.time.time())
    message = SimpleNamespace(
        from_user=SimpleNamespace(id=100), text="200 | cmd:status | - | 24h",
    )
    state = State()
    monkeypatch.setattr(bot, "_admin_stage", stage)
    asyncio.run(bot.on_admin_bulk_grant_text(message, state))

    action, _target, value = staged[0]
    assert action == "bulk_grant"
    assert value["targets"] == [200]
    assert before + 86_400 <= value["expires_at"] <= int(bot.time.time()) + 86_400
    assert state.cleared is True
    assert rendered == []


def test_bulk_grant_confirmation_applies_once_and_reports_exact_counts(monkeypatch, tmp_path):
    Callback, rendered = _admin_fixture(monkeypatch, tmp_path)
    assert bot.access_store.set_role(100, 200, bot.Role.USER, 100)
    value = {"targets": [200], "callbacks": ["cmd:status"], "devices": ["a"]}

    async def scenario():
        await bot._admin_stage(Callback("admin:bulk-grant"), "bulk_grant", value=value)
        preview, markup = rendered[-1]
        assert "Получатели (1): <code>200</code>" in preview
        assert "cmd:status" in preview and "<code>a</code>" in preview
        confirm = next(
            button.callback_data
            for row in markup.inline_keyboard for button in row
            if button.callback_data.startswith("admin:confirm:")
        )
        await bot.on_admin_confirm(Callback(confirm))
        first_result, _ = rendered[-1]
        await bot.on_admin_confirm(Callback(confirm))
        return first_result

    result = asyncio.run(scenario())
    assert "Получателей: 1; новых прав: 2 (действий 1, устройств 1)" in result
    grants = bot.access_store.get_effective_grants(200)
    assert grants["callbacks"] == ["cmd:status"]
    assert grants["devices"] == ["a"]
    assert sum(row.get("kind") == "bulk_grant_applied" for row in bot.access_store.recent_audit(100)) == 1


def test_bulk_grant_confirmation_applies_shared_expiry_and_reports_deadline(monkeypatch, tmp_path):
    Callback, rendered = _admin_fixture(monkeypatch, tmp_path)
    assert bot.access_store.set_role(100, 200, bot.Role.USER, 100)
    value = {
        "targets": [200], "callbacks": ["cmd:status"], "devices": ["a"],
        "expires_at": int(bot.time.time()) + 3_600,
    }

    async def scenario():
        await bot._admin_stage(Callback("admin:bulk-grant"), "bulk_grant", value=value)
        preview, markup = rendered[-1]
        assert "Истекают:" in preview
        confirm = next(
            button.callback_data
            for row in markup.inline_keyboard for button in row
            if button.callback_data.startswith("admin:confirm:")
        )
        await bot.on_admin_confirm(Callback(confirm))
        return rendered[-1][0]

    result = asyncio.run(scenario())
    assert "Массовая выдача применена" in result
    record = bot.access_store.get_user(200)
    assert record["permission_expirations"]["callback:cmd:status"] == value["expires_at"]
    assert record["permission_expirations"]["device:a"] == value["expires_at"]
    assert bot.access_store.get_effective_grants(200, now=value["expires_at"])["callbacks"] == []
    assert bot.access_store.get_effective_grants(200, now=value["expires_at"])["devices"] == []


def test_bulk_revoke_input_stages_exact_callbacks_for_dry_run(monkeypatch, tmp_path):
    from types import SimpleNamespace
    _Callback, rendered = _admin_fixture(monkeypatch, tmp_path)
    for user_id in (200, 201):
        if user_id != 200:
            bot.access_store.register_start(SimpleNamespace(id=user_id, username=f"u{user_id}"))
        assert bot.access_store.set_role(100, user_id, bot.Role.USER, 100)
    staged = []

    async def stage(message, action, user_id=0, value=None):
        staged.append((action, user_id, value))

    class State:
        async def clear(self):
            self.cleared = True

    message = SimpleNamespace(from_user=SimpleNamespace(id=100), text="200,201 | cmd:status,cmd:screenshot")
    state = State()
    monkeypatch.setattr(bot, "_admin_stage", stage)
    asyncio.run(bot.on_admin_bulk_revoke_text(message, state))

    assert staged == [("bulk_revoke", 0, {
        "targets": [200, 201], "callbacks": ["cmd:screenshot", "cmd:status"],
    })]
    assert state.cleared is True
    assert rendered == []


def test_bulk_revoke_confirmation_applies_exact_dry_run_once(monkeypatch, tmp_path):
    Callback, rendered = _admin_fixture(monkeypatch, tmp_path)
    for user_id in (200, 201):
        if user_id != 200:
            bot.access_store.register_start(SimpleNamespace(id=user_id, username=f"u{user_id}"))
        assert bot.access_store.set_role(100, user_id, bot.Role.USER, 100)
    for callback in ("cmd:status", "cmd:screenshot"):
        assert bot.access_store.toggle_callback(100, 200, callback, 100)
    assert bot.access_store.toggle_callback(100, 201, "cmd:status", 100)
    assert bot.access_store.toggle_callback(100, 201, "cmd:sysinfo", 100)
    value = {"targets": [200, 201], "callbacks": ["cmd:status", "cmd:screenshot"]}

    async def scenario():
        await bot._admin_stage(Callback("admin:bulk-revoke"), "bulk_revoke", value=value)
        preview, markup = rendered[-1]
        assert "<code>200</code>" in preview and "<code>201</code>" in preview
        assert "cmd:screenshot" in preview and "cmd:status" in preview
        assert "затронет" in preview
        confirm = next(
            button.callback_data
            for row in markup.inline_keyboard for button in row
            if button.callback_data.startswith("admin:confirm:")
        )
        await bot.on_admin_confirm(Callback(confirm))
        success, _ = rendered[-1]
        await bot.on_admin_confirm(Callback(confirm))
        return success

    result = asyncio.run(scenario())
    assert "Пользователей: 2; прав отозвано: 3" in result
    assert bot.access_store.get_effective_grants(200)["callbacks"] == []
    assert bot.access_store.get_effective_grants(201)["callbacks"] == ["cmd:sysinfo"]
    assert sum(row.get("kind") == "bulk_revoke_applied" for row in bot.access_store.recent_audit(100)) == 1


def test_admin_home_action_is_registry_driven(monkeypatch):
    _setup(monkeypatch, {})
    registry = bot.feature_catalog.load_registry()
    pages = registry["navigation"]
    assert any(page["page_id"] == "admin.home" for page in pages)
    assert "menu:main" in _callback_data(bot.admin_menu())

    without_home = dict(registry)
    without_home["navigation"] = [page for page in pages if page["page_id"] != "admin.home"]
    monkeypatch.setattr(bot.feature_catalog, "load_registry", lambda: without_home)
    assert "menu:main" not in _callback_data(bot.admin_menu())


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


@pytest.mark.parametrize(
    ("standby", "conditional_callback", "hidden_callback"),
    [
        (False, "cmd:standby_sleep", "cmd:wake"),
        (True, "cmd:wake", "cmd:standby_sleep"),
    ],
)
def test_power_menu_renders_exact_registry_actions_for_device_state(
    monkeypatch, standby, conditional_callback, hidden_callback,
):
    monkeypatch.setattr(bot, "SESSION", {"target": "device-1"})
    _setup(monkeypatch, {"device-1": {"name": "Laptop", "os": "macOS", "standby": standby}})
    monkeypatch.setattr(bot, "get_user_role", lambda _user_id: bot.Role.OWNER)
    monkeypatch.setattr(bot.access_store, "can_use_callback", lambda *_args: True)

    rows = bot.feature_catalog.visible_navigation_pages(
        bot.feature_catalog.load_registry(), parent="power", role="owner", grants=set(),
        platform="macos", state="standby" if standby else "awake",
    )
    declared = {str(row["callback"]) for row in rows}
    rendered = set(_callback_data(bot.power_menu_new(user_id=100))) - {"back:device", "menu:main"}

    assert rendered == declared
    assert conditional_callback in rendered
    assert hidden_callback not in rendered


def test_guardian_menu_actions_are_backed_by_declarative_navigation(monkeypatch):
    _setup(monkeypatch, {"device-1": {"name": "Laptop", "os": "Windows 11"}})
    monkeypatch.setattr(bot, "SESSION", {"target": "device-1"})
    monkeypatch.setattr(bot, "CURRENT_TG_USER", __import__("contextvars").ContextVar("guardian_test_user", default=100))
    monkeypatch.setattr(bot, "get_user_role", lambda _user_id: bot.Role.OWNER)
    monkeypatch.setattr(bot.access_store, "can_use_callback", lambda *_args: True)
    rows = bot.feature_catalog.visible_navigation_pages(
        bot.feature_catalog.load_registry(), parent="guardian", role="owner", grants=set(), platform="windows",
    )

    assert {str(row["callback"]) for row in rows} == (
        set(_callback_data(bot.guardian_menu())) - {"cat:power"}
    )


def test_device_settings_menu_uses_registry_and_keeps_owner_only_device_actions(monkeypatch):
    _setup(monkeypatch, {"device-1": {"name": "Laptop", "os": "Windows 11"}})
    monkeypatch.setattr(bot, "SESSION", {"target": "device-1"})
    monkeypatch.setattr(bot, "CURRENT_TG_USER", __import__("contextvars").ContextVar("settings_test_user", default=100))
    monkeypatch.setattr(bot.access_store, "can_use_callback", lambda *_args: True)
    registry = bot.feature_catalog.load_registry()

    monkeypatch.setattr(bot, "get_user_role", lambda _user_id: bot.Role.OWNER)
    owner_rows = bot.feature_catalog.visible_navigation_pages(
        registry, parent="device.settings", role="owner", grants=set(), platform="windows",
    )
    owner_declared = {
        str(row["callback"]).replace("*", "device-1") for row in owner_rows
    }
    owner_rendered = set(_callback_data(bot.device_settings_menu(user_id=100))) - {"back:device", "menu:main"}
    assert owner_declared == owner_rendered
    assert {"devmg:rename:device-1", "devmg:delete:device-1", "devmg:block:device-1", "devmg:uninstall:device-1"} <= owner_rendered

    monkeypatch.setattr(bot, "get_user_role", lambda _user_id: bot.Role.USER)
    user_rows = bot.feature_catalog.visible_navigation_pages(
        registry, parent="device.settings", role="user", grants=set(), platform="windows",
    )
    user_declared = {
        str(row["callback"]).replace("*", "device-1") for row in user_rows
    }
    user_rendered = set(_callback_data(bot.device_settings_menu(user_id=200))) - {"back:device", "menu:main"}
    assert user_declared == user_rendered
    assert not any(callback.startswith("devmg:") for callback in user_rendered)


def test_events_menu_actions_are_backed_by_owner_only_navigation_registry(monkeypatch):
    _setup(monkeypatch, {})
    monkeypatch.setattr(bot, "get_user_role", lambda _user_id: bot.Role.OWNER)
    monkeypatch.setattr(bot.access_store, "can_use_callback", lambda *_args: True)
    rows = bot.feature_catalog.visible_navigation_pages(
        bot.feature_catalog.load_registry(), parent="events", role="owner", grants=set(), platform="telegram",
    )

    assert {str(row["callback"]) for row in rows} == set(_callback_data(bot.events_menu()))


@pytest.mark.parametrize(
    ("parent", "builder", "expected"),
    [
        ("events.quiet", "quiet_hours_menu", {"quiet::", "quiet:23:8", "quiet:22:7", "quiet:0:6", "ev:menu"}),
        ("events.digest", "digest_menu", {"digest:", "digest:9", "digest:21", "ev:menu"}),
    ],
)
def test_event_submenus_are_backed_by_declarative_navigation(monkeypatch, parent, builder, expected):
    _setup(monkeypatch, {})
    monkeypatch.setattr(bot, "get_user_role", lambda _user_id: bot.Role.OWNER)
    monkeypatch.setattr(bot.access_store, "can_use_callback", lambda *_args: True)
    rows = bot.feature_catalog.visible_navigation_pages(
        bot.feature_catalog.load_registry(), parent=parent, role="owner", grants=set(), platform="telegram",
    )

    assert {str(row["callback"]) for row in rows} == expected
    assert expected == set(_callback_data(getattr(bot, builder)()))


def test_admins_menu_uses_registry_for_owner_and_dynamic_admin_ids(monkeypatch):
    _setup(monkeypatch, {})
    monkeypatch.setattr(bot, "CURRENT_TG_USER", __import__("contextvars").ContextVar("admins_test_user", default=100))
    monkeypatch.setattr(bot, "get_user_role", lambda _user_id: bot.Role.OWNER)
    monkeypatch.setattr(bot.access_store, "can_use_callback", lambda *_args: True)
    monkeypatch.setattr(bot.bot_settings, "all_settings", lambda: {"admins": [222, 333]})
    rows = bot.feature_catalog.visible_navigation_pages(
        bot.feature_catalog.load_registry(), parent="events.admins", role="owner", grants=set(), platform="telegram",
    )
    declared = {str(row["callback"]).replace("*", "222") for row in rows if str(row["callback"]) != "adm:rm:*"}
    declared.update({"adm:rm:222", "adm:rm:333"} if any(row["callback"] == "adm:rm:*" for row in rows) else set())

    assert declared == set(_callback_data(bot.admins_menu(user_id=100)))


def test_admins_menu_non_owner_home_route_is_registry_backed(monkeypatch):
    _setup(monkeypatch, {})
    monkeypatch.setattr(bot, "get_user_role", lambda _user_id: bot.Role.USER)
    monkeypatch.setattr(bot.access_store, "can_use_callback", lambda *_args: True)

    rows = bot.feature_catalog.visible_navigation_pages(
        bot.feature_catalog.load_registry(), parent="events.admins", role="user", grants=set(), platform="telegram",
    )

    assert {str(row["callback"]) for row in rows} == {"menu:main"}
    assert set(_callback_data(bot.admins_menu(user_id=200))) == {"menu:main"}


def test_admin_root_menu_is_registry_backed(monkeypatch):
    _setup(monkeypatch, {})
    monkeypatch.setattr(bot, "CURRENT_TG_USER", __import__("contextvars").ContextVar("admin_root_test_user", default=100))
    monkeypatch.setattr(bot, "get_user_role", lambda _user_id: bot.Role.OWNER)
    monkeypatch.setattr(bot.access_store, "can_use_callback", lambda *_args: True)
    rows = bot.feature_catalog.visible_navigation_pages(
        bot.feature_catalog.load_registry(), parent="admin", role="owner", grants=set(), platform="telegram",
    )

    assert {str(row["callback"]) for row in rows} == (
        set(_callback_data(bot.admin_menu()))
    )


def test_server_menu_is_registry_backed_and_guest_sees_no_server_actions(monkeypatch):
    _setup(monkeypatch, {})
    monkeypatch.setattr(bot.access_store, "can_use_callback", lambda *_args: True)
    owner_rows = bot.feature_catalog.visible_navigation_pages(
        bot.feature_catalog.load_registry(), parent="server", role="owner", grants=set(), platform="telegram",
    )
    owner_rendered = set(_callback_data(bot.server_menu(bot.ADMIN_ID))) - {"menu:main"}
    assert {str(row["callback"]) for row in owner_rows} == owner_rendered

    guest_rendered = set(_callback_data(bot.server_menu(300)))
    assert guest_rendered == {"menu:main"}


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


def test_tracked_guardian_dispatch_can_include_action_metadata(monkeypatch):
    captured = {}
    monkeypatch.setattr(bot.uuid, "uuid4", lambda: SimpleNamespace(hex="a" * 32))
    monkeypatch.setattr(
        bot, "publish",
        lambda command_type, **kwargs: captured.update(command_type=command_type, **kwargs) or True,
    )

    sent, command_id = bot.publish_tracked(
        "guardian", _target="mac-1", action="guardian", command="logs",
    )

    assert sent is True
    assert command_id == "a" * 12
    assert captured == {
        "command_type": "guardian", "_target": "mac-1", "action": "guardian",
        "command": "logs", "id": "a" * 12,
    }


def test_agent_logs_falls_back_to_guardian_when_agent_lacks_log_command(monkeypatch):
    rendered, waits, published = [], [], []

    async def answer(*args, **kwargs):
        return None

    async def replace(_cq, text, **kwargs):
        rendered.append(text)

    async def allowed(*args, **kwargs):
        return True

    async def wait_for(target, action, timeout, command_id):
        waits.append((target, action, command_id))
        return {
            "type": "guardian", "device_id": "mac-1", "id": command_id,
            "state": "available", "lines": 1, "text": "[siri] recovered Safari",
        }

    monkeypatch.setattr(bot, "SESSION", {"target": "mac-1"})
    monkeypatch.setattr(bot.devices, "get", lambda _target: {"name": "Mac", "os": "macOS"})
    monkeypatch.setattr(bot, "_callback_result_access_or_report", allowed)
    monkeypatch.setattr(bot, "_callback_delivery_guard", lambda *_args: lambda: True)
    monkeypatch.setattr(bot, "_replace_callback_message", replace)
    monkeypatch.setattr(bot, "_device_declares_command", lambda *_args: False)
    monkeypatch.setattr(
        bot, "publish_tracked",
        lambda command_type, **kwargs: (published.append((command_type, kwargs)) or (True, "guardian-id")),
    )
    monkeypatch.setattr(bot.fun_text_collector, "wait_for", wait_for)
    monkeypatch.setattr(
        bot.access_store, "redact_shell_output_with_summary",
        lambda text: (text, {"total": 0}),
    )
    monkeypatch.setattr(bot.access_store, "append_audit", lambda *args, **kwargs: None)
    cq = SimpleNamespace(
        from_user=SimpleNamespace(id=bot.ADMIN_ID), data="cmd:agent_logs",
        answer=answer, message=SimpleNamespace(chat=SimpleNamespace(id=1), message_id=2),
    )

    asyncio.run(bot.on_cmd_agent_logs(cq))

    assert published and published[0][0] == "guardian"
    assert published[0][1]["command"] == "logs"
    assert waits == [("mac-1", "guardian", "guardian-id")]
    assert "[siri] recovered Safari" in rendered[-1]
    assert "Источник: <code>Guardian</code>" in rendered[-1]


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

    async def replace(_cq, text, reply_markup=None, **_kwargs):
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

    async def replace(_cq, text, reply_markup=None, **_kwargs):
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
    assert "<code>irm https://invinby.github.io/XIDER/win.txt | iex</code>" in shown[0][0]


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
    monkeypatch, os_name, component_asset, signed_catalog_release,
):
    import release_catalog
    from types import SimpleNamespace

    session = bot.SessionRegistry()
    session["target"] = "device-1"
    monkeypatch.setattr(bot, "SESSION", session)
    monkeypatch.setattr(bot, "_PENDING_VERSION_INSTALLS", {})
    _setup(monkeypatch, {"device-1": {
        "name": "My device", "os": os_name, "version": "4.0.1",
        "update_mode": "source", "release_update_ready": True, "update_manifest_binding_protocol": 1,
        "online": True, "last_seen": time.time(),
    }})
    release = release_catalog.Release(
        tag="v4.2.0", version=(4, 2, 0), name="Chosen release",
        published_at="2026-10-01T00:00:00Z", notes="Safe selected release",
        asset_names=frozenset({component_asset, "XIDER-source.zip", "release-manifest.json"}),
    )
    release = signed_catalog_release(release)
    monkeypatch.setattr(bot.release_catalog.catalog, "list", lambda **kwargs: [release])
    cards = []

    async def replace(_cq, text, reply_markup=None, **_kwargs):
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


def test_canary_version_detail_selects_current_device_first_without_publish(monkeypatch, tmp_path, signed_catalog_release):
    import release_catalog
    Callback, rendered = _admin_fixture(monkeypatch, tmp_path)
    monkeypatch.setattr(bot, 'get_user_role', lambda user: bot.Role.OWNER)
    monkeypatch.setattr(bot, 'SESSION', {'target': 'mac2'})
    monkeypatch.setattr(bot, '_PENDING_VERSION_INSTALLS', {})
    monkeypatch.setattr(bot, '_DISPLAYED_VERSION_RELEASES', {})
    info = {'name': 'Mac', 'os': 'macOS 27', 'version': '4.1.0', 'update_mode': 'source', 'release_update_ready': True, 'update_manifest_binding_protocol': 1, 'online': True, 'last_seen': time.time()}
    _setup(monkeypatch, {'mac1': info, 'mac2': info, 'offline': {**info, 'online': False}, 'win': {**info, 'os': 'Windows 11'}})
    release = signed_catalog_release(release_catalog.Release('v4.2.0', (4, 2, 0), 'release', 'date', 'notes', frozenset({'XGENT-MCS-macos-bundle.zip', 'XIDER-source.zip', 'release-manifest.json'})))
    monkeypatch.setattr(bot.release_catalog.catalog, 'list', lambda **kwargs: [release])
    monkeypatch.setattr(bot, 'publish', lambda *a, **kw: pytest.fail('selection published'))
    asyncio.run(bot.on_versions_detail(Callback('versions:detail:agent:v4.2.0')))
    assert 'versions:canary:v4.2.0' in _callback_data(rendered[-1][1])
    asyncio.run(bot.on_versions_canary_select(Callback('versions:canary:v4.2.0')))
    assert bot._PENDING_VERSION_INSTALLS[100]['update_devices'] == ['mac2', 'mac1']
    assert 'mac2' in rendered[-1][0] and 'mac1' in rendered[-1][0]


def test_agent_release_catalog_has_a_direct_install_action(monkeypatch, signed_catalog_release):
    import release_catalog
    from types import SimpleNamespace

    session = bot.SessionRegistry()
    session["target"] = "device-1"
    monkeypatch.setattr(bot, "SESSION", session)
    _setup(monkeypatch, {"device-1": {
        "name": "My device", "os": "macOS 27", "version": "4.2.0",
        "update_mode": "source", "release_update_ready": True, "update_manifest_binding_protocol": 1,
        "online": True, "last_seen": time.time(),
    }})
    release = release_catalog.Release(
        tag="v4.2.0", version=(4, 2, 0), name="Chosen release",
        published_at="2026-10-07T00:00:00Z", notes="Signed release",
        asset_names=frozenset({
            "XGENT-MCS-macos-bundle.zip", "XIDER-source.zip", "release-manifest.json",
        }),
    )
    release = signed_catalog_release(release)
    monkeypatch.setattr(bot.release_catalog.catalog, "list", lambda **kwargs: [release])
    cards = []

    async def replace(_cq, text, reply_markup=None, **_kwargs):
        cards.append((text, reply_markup))

    monkeypatch.setattr(bot, "_replace_callback_message", replace)

    class Callback:
        from_user = SimpleNamespace(id=bot.ADMIN_ID)
        data = "versions:list:agent:0"

        async def answer(self, *args, **kwargs):
            pass

    asyncio.run(bot.on_versions_list(Callback()))
    callbacks = _callback_data(cards[-1][1])
    assert "versions:detail:agent:v4.2.0" in callbacks
    assert "versions:install:agent:v4.2.0" in callbacks


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


def test_legacy_agent_version_page_offers_owner_bootstrap_command(monkeypatch):
    import release_catalog
    from types import SimpleNamespace

    session = bot.SessionRegistry()
    session["target"] = "old-mac"
    monkeypatch.setattr(bot, "SESSION", session)
    _setup(monkeypatch, {"old-mac": {
        "name": "Old Mac", "os": "macOS 27", "version": "3.3.8",
        "online": True, "last_seen": time.time(),
    }})
    release = release_catalog.Release(
        tag="v4.2.0", version=(4, 2, 0), name="Current release",
        published_at="2026-10-07T00:00:00Z", notes="Current signed release",
        asset_names=frozenset({
            "XGENT-MCS-macos-bundle.zip", "XIDER-source.zip", "release-manifest.json",
        }),
    )
    monkeypatch.setattr(bot.release_catalog.catalog, "list", lambda: [release])
    cards = []

    async def replace(_cq, text, reply_markup=None, **_kwargs):
        cards.append((text, reply_markup))

    monkeypatch.setattr(bot, "_replace_callback_message", replace)

    class Callback:
        from_user = SimpleNamespace(id=bot.ADMIN_ID)

        def __init__(self, data):
            self.data = data

        async def answer(self, *args, **kwargs):
            pass

    asyncio.run(bot.on_versions_list(Callback("versions:list:agent:0")))
    catalog_callbacks = _callback_data(cards[-1][1])
    assert "versions:bootstrap:v4.2.0" in catalog_callbacks

    asyncio.run(bot.on_versions_detail(Callback("versions:detail:agent:v4.2.0")))
    detail_buttons = [button for row in cards[-1][1].inline_keyboard for button in row]
    bootstrap_button = next(
        button for button in detail_buttons
        if button.callback_data == "versions:bootstrap:v4.2.0"
    )
    assert len(bootstrap_button.callback_data) <= 64

    asyncio.run(bot.on_versions_bootstrap(Callback(bootstrap_button.callback_data)))
    body, keyboard = cards[-1]
    assert "подписан" in body
    assert "не" in body and "v4.2.0" in body
    assert "Old Mac" in body and "терминал" in body
    assert "curl -fsSL https://invinby.github.io/XIDER/mac | bash" in body
    assert keyboard.inline_keyboard[0][0].callback_data == "versions:detail:agent:v4.2.0"


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

    async def replace(_cq, text, reply_markup=None, **_kwargs):
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
        else "irm https://invinby.github.io/XIDER/win.txt | iex"
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


def test_photo_response_becomes_current_navigation_card(monkeypatch):
    action = "screenshot"
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
            return {"image": _jpeg_test_image_b64(),
                    "audio": base64.b64encode(b"fixture-audio").decode("ascii")}

    async def send_photo(*args, **kwargs):
        return SimpleNamespace(chat=SimpleNamespace(id=1), message_id=18)

    cards = []
    monkeypatch.setitem(bot.SESSION, "target", "mac1")
    monkeypatch.setattr(bot, "publish_tracked", lambda *args, **kwargs: (True, "cmd-1"))
    monkeypatch.setattr(bot, f"{action}_collector", FakeCollector())
    monkeypatch.setattr(bot, "bot", SimpleNamespace(send_photo=send_photo, send_voice=send_photo))
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


def _rendered_callback_inventory(monkeypatch):
    from contextvars import ContextVar
    from types import SimpleNamespace

    devices = FakeDevices({
        "device-1": {
            "name": "Test PC", "os": "Windows", "version": "4.1.0",
            "guardian": {"version": "1.0.0"}, "favorites": ["status"],
        },
        "device-2": {"name": "Test Mac", "os": "macOS", "version": "4.1.0"},
    })
    devices._relations = {"device-1": ["device-2"], "device-2": ["device-1"]}
    session = bot.SessionRegistry()
    session["target"] = "device-1"
    monkeypatch.setattr(bot, "devices", devices)
    monkeypatch.setattr(bot, "SESSION", session)
    monkeypatch.setattr(bot, "CURRENT_TG_USER", ContextVar("inventory_owner", default=bot.ADMIN_ID))
    monkeypatch.setattr(bot.bot_settings, "all_settings", lambda: {
        "admins": [200], "blocked_ids": ["blocked-device"],
    })
    monkeypatch.setattr(bot.access_store, "list_users", lambda: [
        {"id": 200, "role": "guest", "permissions": {}},
    ])
    monkeypatch.setattr(
        bot.update_coordinator.store,
        "batch",
        lambda _batch_id: [SimpleNamespace(device_id="device-1"), SimpleNamespace(device_id="device-2")],
    )
    url_shortcuts = [{"id": "012345abcdef", "name": "Example", "url": "https://example.com"}]
    monkeypatch.setattr(
        bot.bot_settings,
        "get",
        lambda key, default=None: "xtech" if key == "ui_style" else
        url_shortcuts if key == "url_favorites" else default,
    )

    builders = [
        bot.main_menu(bot.ADMIN_ID), bot.main_menu(200), bot.devices_menu(bot.ADMIN_ID),
        bot.device_menu("device-1", bot.ADMIN_ID), bot.all_menu(),
        bot.device_relations_keyboard(bot.ADMIN_ID, "aabbccdd", 0, "device-1"),
        bot.back_to_device_kb(), bot.power_menu(), bot.confirm_power_menu("shutdown"),
        bot.rotate_menu(), bot.media_menu(), bot.screen_menu(), bot.wallpaper_menu(),
        bot.input_menu(), bot.system_menu(), bot.network_menu(), bot.files_menu(),
        bot.terminal_menu(), bot.power_menu_new(), bot.guardian_menu(),
        *(bot.pranks_menu(page=page, target="device-1") for page in range(1, 6)),
        bot.device_settings_menu(), bot.mic_options_menu(), bot.vol_options_menu(),
        bot.feature_catalog_areas_menu(bot.ADMIN_ID),
        bot.confirm_kb("stop:ok"), bot.events_menu(), bot.quiet_hours_menu(),
        bot.digest_menu(), bot.admins_menu(), bot.admin_menu(), bot.admin_texts_menu(),
        bot.admin_users_menu(), bot.admin_user_menu(0), bot.admin_user_menu(200), bot.admin_devices_menu(200),
        bot.admin_permission_profiles_menu(200, actor_id=bot.ADMIN_ID),
        bot.admin_permissions_menu(0), bot.guest_devices_menu(), bot.server_menu(bot.ADMIN_ID),
        *(bot.server_confirm_menu(action) for action in ("restart", "update", "rollback")),
        *(bot.confirm_power_menu(action) for action in ("shutdown", "reboot", "sleep")),
        bot.blocked_menu(), bot._version_root_menu(),
        bot._version_root_menu(server=True),
        bot.manual_install_os_keyboard(), bot.manual_install_os_keyboard(selected_os="windows"),
        bot.manual_install_os_keyboard(selected_os="macos"),
        bot.url_count_keyboard(), bot.url_count_keyboard(is_owner=True),
        bot.url_favorites_keyboard(), bot.url_favorites_keyboard(is_owner=True),
        bot._network_check_sets_keyboard(bot.ADMIN_ID, [{
            "set_id": "0123456789abcdef0123456789abcdef", "name": "Home", "endpoints": [],
        }]),
        bot._favorites_kb("device-1", ["status"]),
        bot._version_install_confirmation_kb("v4.1.0"),
        bot._capabilities_keyboard({
            "schema": 1,
            "platform": "macOS 27.0",
            "checked_at": "2026-10-08T10:00:00+00:00",
            "commands": ["capabilities", "permission_settings"],
            "items": [
                {"id": permission, "state": "denied", "detail": "test"}
                for permission in (
                    "screen_recording", "camera", "microphone", "location", "accessibility", "file_access",
                )
            ],
        }, user_id=bot.ADMIN_ID),
        bot._location_result_keyboard({"state": "denied"}, bot.ADMIN_ID),
        bot._update_batch_keyboard(
            SimpleNamespace(batch_id="inventory-batch", release_tag="v4.1.0"), bot.ADMIN_ID,
        ),
        *(bot.confirm_kb(callback) for callback in (
            "shell:ok", "openapp:ok", "stop:ok", "stopall:ok", "guardianstop:ok", "sleep:ok", "kill:123",
        )),
    ]
    # Exercise state-dependent keyboard branches, including standby wake.
    devices._devices["device-1"]["standby"] = True
    builders.append(bot.power_menu_new())
    devices._devices["device-1"]["standby"] = False
    for style in bot.xlex.STYLES:
        monkeypatch.setattr(bot.bot_settings, "get", lambda key, default=None, selected=style:
                            selected if key == "ui_style" else default)
        builders.append(bot.admin_texts_menu())
    callbacks = {
        button.callback_data
        for markup in builders
        for row in markup.inline_keyboard
        for button in row
        if button.callback_data
    }
    # Previously omitted keyboard branches must participate in route coverage.
    assert {"install:windows", "install:macos", "fav:toggle:screenshot",
            "versions:install_confirm:v4.1.0", "versions:install_cancel:v4.1.0",
            "catalog:filter:access"} <= callbacks
    # Some screens build their keyboard inside a handler. Evaluate only the
    # callback expressions with controlled samples, never execute the handler
    # or its network/disk/publish side effects. Every expression is included,
    # including confirmation, paging and owner notification branches.
    tree = ast.parse(Path(bot.__file__).read_text(encoding="utf-8"))
    context = {
        "message": SimpleNamespace(from_user=SimpleNamespace(id=200)),
        "user_id": 200,
        "target_id": 200,
        "raw_target": "200",
        "latest": {"reference": "0123456789abcdef0123"},
        "seconds": 3_600,
        "device_id": "device-1", "info_book": bot.info_book, "index": 1,
        "it": {"pid": 123}, "page": 1, "i": 0, "notes_page": 1,
        "token": "0123456789abcdef0123456789abcdef",
        "preview_token": "0123456789ab",
        "entry": {"token": "0123456789ab", "request_id": "late-request"},
        "row": {"request_id": "0123456789abcdef0123456789abcdef"},
        "pending": SimpleNamespace(request_id="0123456789abcdef0123456789"),
        "replacement": SimpleNamespace(request_id="fedcba9876543210fedcba9876543210"),
        "command_id": "media-series-sample",
        "approve_callback": "cmd:shell-approve:sample", "cancel_callback": "cmd:shell-cancel:sample",
        "permission": "camera",
        "feature_id": "D01-F01",
        "request_id": "0123456789abcdef0123456789abcdef",
        "release": SimpleNamespace(tag="v4.1.0"),
        "bootstrap_release": SimpleNamespace(tag="v4.1.0"), "tag": "v4.1.0",
        "plan": SimpleNamespace(plan_id="0123456789abcdef0123456789"),
        "template_id": "0123456789abcdef0123456789abcdef",
        "cancel": next(
            row for row in bot.feature_catalog.load_registry()["navigation"]
            if row.get("page_id") == "admin.permission_profiles.cancel"
        ),
    }
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        # Standalone builders above are rendered; inline handler expressions
        # cannot be rendered independently without performing remote actions.
        if not isinstance(node, ast.AsyncFunctionDef) and node.name != "on_mqtt_message":
            continue
        for call in ast.walk(node):
            if not isinstance(call, ast.Call):
                continue
            for keyword in call.keywords:
                if keyword.arg != "callback_data":
                    continue
                expression = compile(ast.Expression(keyword.value), str(bot.__file__), "eval")
                for kind in ("server", "agent", "keeper"):
                    for style in bot.xlex.STYLES:
                        for slug, _title, _body in bot.info_book.CHAPTERS:
                            callbacks.add(eval(expression, {"__builtins__": {}},
                                               dict(context, kind=kind, style=style, slug=slug)))
    return callbacks


def _callback_routes(callback):
    from types import SimpleNamespace

    return [
        handler for handler in bot.router.callback_query.handlers
        if any(item.magic is not None and item.callback(SimpleNamespace(data=callback)) is True
               for item in handler.filters)
    ]


def test_rendered_menu_callbacks_match_a_registered_route(monkeypatch):
    from types import SimpleNamespace

    callbacks = _rendered_callback_inventory(monkeypatch)
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


def test_ip_fallback_keyboard_is_in_inventory_and_owner_gated(monkeypatch):
    monkeypatch.setattr(
        bot,
        "get_user_role",
        lambda user_id: bot.Role.OWNER if user_id == bot.ADMIN_ID else bot.Role.GUEST,
    )
    owner_markup = bot._location_result_keyboard({"state": "denied"}, bot.ADMIN_ID)
    guest_markup = bot._location_result_keyboard({"state": "denied"}, 200)
    owner_callbacks = {
        button.callback_data for row in owner_markup.inline_keyboard for button in row
    }
    guest_callbacks = {
        button.callback_data for row in guest_markup.inline_keyboard for button in row
    }
    assert "location:ip" in owner_callbacks
    assert "location:ip" not in guest_callbacks

    callbacks = _rendered_callback_inventory(monkeypatch)
    assert "location:ip" in callbacks, "Location result keyboard is missing from callback inventory"
    route = _callback_routes("location:ip")
    assert len(route) == 1
    _assert_expected_callback_gate("location:ip", route[0])
    metadata = _descriptive_callback_metadata("location:ip", route[0])
    assert metadata["access_class"] == "protected OwnerFilter"
    assert metadata["category"] == "network"
    assert metadata["wire_action"] == "geo_location"
    assert metadata["timeout"] == 16.0
    assert metadata["result_sensitivity"] == "approximate city/region"
    assert metadata["risk"] == "explicit external IP lookup"


def test_network_check_set_callbacks_are_owner_only_and_registered(monkeypatch):
    monkeypatch.setattr(bot, "get_user_role", lambda uid: bot.Role.OWNER if uid == bot.ADMIN_ID else bot.Role.GUEST)
    owner_menu = bot.network_menu(bot.ADMIN_ID)
    assert "cmd:network_check_sets" in _callback_data(owner_menu)
    assert "cmd:network_check_sets" not in _callback_data(bot.network_menu(200))

    markup = bot._network_check_sets_keyboard(bot.ADMIN_ID, [{
        "set_id": "0123456789abcdef0123456789abcdef", "name": "Home", "endpoints": [],
    }])
    expected = {
        "network-set:run:0123456789abcdef0123456789abcdef",
        "network-set:delete:0123456789abcdef0123456789abcdef",
        "network-set:create",
    }
    assert expected <= set(_callback_data(markup))
    for callback in expected:
        routes = _callback_routes(callback)
        assert len(routes) == 1
        _assert_expected_callback_gate(callback, routes[0])


def test_external_ip_watch_callbacks_are_owner_only_and_registered(monkeypatch):
    monkeypatch.setattr(bot, "get_user_role", lambda uid: bot.Role.OWNER if uid == bot.ADMIN_ID else bot.Role.GUEST)
    owner_menu = bot.network_menu(bot.ADMIN_ID)
    guest_menu = bot.network_menu(200)
    assert "cmd:external_ip_watch" in _callback_data(owner_menu)
    assert "cmd:external_ip_watch" not in _callback_data(guest_menu)
    markup = bot._external_ip_watch_keyboard(bot.ADMIN_ID, True)
    expected = {"network-ip-watch:set:900", "network-ip-watch:set:1800", "network-ip-watch:set:3600", "network-ip-watch:off"}
    assert expected <= set(_callback_data(markup))
    assert all(len(item.encode("utf-8")) <= 64 for item in _callback_data(markup))
    for callback in {"cmd:external_ip_watch", *expected}:
        routes = _callback_routes(callback)
        assert len(routes) == 1
        _assert_expected_callback_gate(callback, routes[0])


def test_external_ip_response_requires_fresh_correlation_and_global_address():
    now = datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0)
    response = {
        "type": "ext_ip", "schema": "xider.external-ip.v1", "feature_id": "D04-F30",
        "device_id": "device-1", "request_id": "ticket-1", "state": "available",
        "source": "ipify", "ip": "8.8.8.8", "checked_at": now.isoformat(),
    }
    assert bot._validated_external_ip_response(response, device_id="device-1", command_id="ticket-1", now=now.timestamp()) == {
        "ip": "8.8.8.8", "checked_at": int(now.timestamp()), "source": "ipify",
    }
    for changes in ({"request_id": "old"}, {"device_id": "other"}, {"ip": "192.168.1.1"},
                    {"checked_at": (now - datetime.timedelta(minutes=5)).isoformat()}):
        assert bot._validated_external_ip_response({**response, **changes}, device_id="device-1",
                                                   command_id="ticket-1", now=now.timestamp()) is None


def test_response_collector_accepts_typed_request_id_correlation():
    collector = bot.ResponseCollector()
    response = {"type": "ext_ip", "request_id": "typed-ticket", "ip": "8.8.8.8"}
    collector.submit("device-1", response)
    async def get_result():
        return await collector.wait_for("device-1", "ext_ip", timeout=0.1, command_id="typed-ticket")
    assert asyncio.run(get_result()) == {"device_id": "device-1", **response}


def test_external_ip_watch_scheduler_baselines_then_notifies_and_acks(monkeypatch, tmp_path):
    from types import SimpleNamespace
    from device_store import DeviceStore

    monkeypatch.setattr(bot, "ADMIN_ID", 20)
    monkeypatch.setattr(DeviceStore, "FILE", tmp_path / "devices.json")
    monkeypatch.setattr(DeviceStore, "CATALOG_FILE", tmp_path / "catalog.json")
    store = DeviceStore()
    store.upsert("device-1", {"online": True, "name": "Laptop", "capabilities": {
        "commands": ["ext_ip"], "feature_status": {"external_ip_watch": "supported"},
    }})
    store.set_external_ip_watch(20, 20, "device-1", 900, now=1_699_999_100)
    monkeypatch.setattr(bot, "devices", store)

    command_ids = iter(("ticket-1", "ticket-2"))
    monkeypatch.setattr(bot, "publish_tracked", lambda *_a, **_k: (True, next(command_ids)))
    samples = iter(("8.8.8.8", "1.1.1.1"))
    async def wait_for(device_id, action, timeout, command_id):
        checked_at = datetime.datetime.fromtimestamp(
            1_700_000_010 if command_id == "ticket-1" else 1_700_000_910,
            datetime.timezone.utc,
        ).isoformat()
        return {
            "type": "ext_ip", "schema": "xider.external-ip.v1", "feature_id": "D04-F30",
            "device_id": device_id, "request_id": command_id, "state": "available",
            "source": "ipify", "ip": next(samples), "checked_at": checked_at,
        }
    monkeypatch.setattr(bot, "external_ip_collector", SimpleNamespace(wait_for=wait_for))
    delivered = []
    async def send_message(chat_id, text):
        delivered.append((chat_id, text))
    monkeypatch.setattr(bot, "bot", SimpleNamespace(send_message=send_message))

    async def exercise():
        await bot._external_ip_watch_once(now=1_700_000_000)
        assert delivered == []
        await bot._external_ip_watch_once(now=1_700_000_900)

    asyncio.run(exercise())
    assert len(delivered) == 1 and delivered[0][0] == 20
    assert "8.8.8.8" in delivered[0][1] and "1.1.1.1" in delivered[0][1]
    assert "2023-11-14" in delivered[0][1]
    assert store.pending_external_ip_alerts(20, 20, "device-1") == []


def test_network_check_set_run_sends_only_saved_targets_and_validates_each_result(monkeypatch, tmp_path):
    from types import SimpleNamespace

    telegram = _card_telegram(monkeypatch, tmp_path)
    endpoints = [
        {"protocol": "tcp", "host": "192.168.1.1", "port": 22},
        {"protocol": "tls", "host": "example.com", "port": 443},
    ]
    saved = {"set_id": "0123456789abcdef0123456789abcdef", "owner_id": 20,
             "name": "Home", "endpoints": endpoints, "created_at": 1}
    _setup(monkeypatch, {"device-1": {"name": "Laptop", "os": "Windows 11", "capabilities": {
        "commands": ["service_probe"],
    }}})
    monkeypatch.setattr(bot, "ADMIN_ID", 20)
    monkeypatch.setattr(bot, "SESSION", {"target": "device-1"})
    monkeypatch.setattr(bot, "get_user_role", lambda _uid: bot.Role.OWNER)
    monkeypatch.setattr(bot, "_callback_result_access_or_report", lambda *_a, **_k: asyncio.sleep(0, result=True))
    monkeypatch.setattr(bot.devices, "get_network_check_set", lambda actor, owner, key: saved if key == saved["set_id"] else None, raising=False)
    monkeypatch.setattr(bot.devices, "list_network_check_sets", lambda *_args: [saved], raising=False)
    published = []
    def publish(*args, **kwargs):
        command_id = f"probe-{len(published) + 1}"
        published.append((args, kwargs, command_id))
        return True, command_id
    monkeypatch.setattr(bot, "publish_tracked", publish)
    async def wait_for(target, command, timeout, command_id):
        index = int(command_id.rsplit("-", 1)[1]) - 1
        endpoint = endpoints[index]
        return {
            "type": "service_probe", "schema": "xider.service-probe.v1", "feature_id": "D04-F14",
            "device_id": target, "request_id": command_id, **endpoint,
            "source": "single_endpoint_tcp_tls", "state": "reachable", "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat(), "detail": None,
        }
    monkeypatch.setattr(bot, "service_probe_collector", SimpleNamespace(wait_for=wait_for))
    rendered = []
    async def replace(_cq, text, reply_markup=None, **kwargs):
        rendered.append((text, reply_markup))
    monkeypatch.setattr(bot, "_replace_callback_message", replace)

    cq = telegram.callback("network-set:run:" + saved["set_id"])
    asyncio.run(bot.on_network_check_set_run(cq))

    assert len(published) == 2
    assert [row[1] for row in published] == [
        {"_target": "device-1", **endpoints[0]}, {"_target": "device-1", **endpoints[1]},
    ]
    assert len(rendered) == 2 and "Home" in rendered[-1][0]
    assert "192.168.1.1:22" in rendered[-1][0] and "example.com:443" in rendered[-1][0]
    assert rendered[-1][0].count("доступен, проверка успешна") == 2


def _expected_callback_gate(callback):
    """Independent route contract: never inferred from the installed filter."""
    if callback.startswith(("cmd:shell-page:", "cmd:shell-preview:", "cmd:shell-json:", "cmd:shell-source:", "cmd:shell-compare-select:", "cmd:shell-repeat:", "cmd:shell-dryrun:", "cmd:shell-runbook-confirm:", "cmd:shell-runbook-cancel:", "cmd:shell-template-", "cmd:shell-tpl-")):
        return "OwnerFilter"
    if callback in {"cmd:shell_late_results", "cmd:shell_runbook_templates", "cmd:shell_metadata_export", "cmd:shell_runbook", "cmd:route_trace", "cmd:route_lookup", "cmd:shell_timing_report", "cmd:shell_runner_diagnostic", "cmd:shell_approval_inbox", "cmd:path_mtu", "cmd:path_diagnostic", "cmd:network_check_sets", "cmd:external_ip_watch", "network-set:create", "network-ip-watch:set:900", "network-ip-watch:set:1800", "network-ip-watch:set:3600", "network-ip-watch:off", "cmd:captive_portal", "cmd:ipv6_connectivity", "cmd:tls_certificate", "cmd:service_probe", "cmd:latency_series", "cmd:packet_loss_history", "cmd:network_jitter", "cmd:network_throughput", "cmd:network_link_speed", "cmd:wifi_quality_history", "cmd:wifi_channel_summary", "cmd:wifi_connection_history", "cmd:proxy_status", "cmd:vpn_route_status", "cmd:endpoint_batch", "cmd:dnssec_check", "cmd:network_traffic"} or callback.startswith(("traffic:sample:", "traffic:alert-", "network-report:export:", "network-set:run:", "network-set:delete:", "network-link-speed:", "cmd:shell-late:view:", "cmd:shell-inbox:view:")):
        return "OwnerFilter"
    if callback in {"camera:catalog", "camera:capture", "camera:preview:keep", "camera:preview:discard", "cmd:mic", "mic:level", "mic:balance", "mic:preview", "media:history", "audio:outputs", "audio:switch", "mic:test"} or callback.startswith(("camera:select:", "camera:series:", "media:cancel:", "mic:preview:", "mic:input:", "mic:level:", "mic:balance:", "micdur:", "audio:previewout:", "audio:testout:", "audio:setout:", "cmd:webcam")):
        return "OwnerFilter"
    if callback in {"devices:filter-os", "devices:filter-os:cancel", "devices:filter-heartbeat", "devices:filter-heartbeat:cancel", "devices:filter-version", "devices:filter-version:cancel", "devices:filter-capability", "devices:filter-capability:cancel", "devices:filter-inventory-age", "devices:filter-inventory-age:cancel", "devices:sort", "catalog:pinned:list", "catalog:order:edit", "catalog:archive:list", "catalog:export", "catalog:import", "catalog:import:cancel"} or callback.startswith(("devices:sort:", "catalog:pinned:toggle:", "catalog:order:up:", "catalog:order:down:", "catalog:archive:", "catalog:export:", "catalog:link:")):
        return "OwnerFilter"
    if callback in {"devices:search", "devices:search:cancel"}:
        return "AdminFilter"
    if callback in {"shell:ok", "menu:admin", "menu:target", "url:favorites", "versions:server", "cpu:history", "memory:history", "volume:history", "volume:topology", "clock:drift", "sysinfo:history", "sysinfo:compare", "sysinfo:export", "sysinfo:interval", "sysinfo:threshold", "sysinfo:aggregate", "sysinfo:uncertainty", "camera:quality", "media:temp:cleanup", "display:catalog", "display:resolution", "pressure:history", "thermal:snapshot", "battery:health", "battery:cycles", "energy:usage", "sleep:history", "boot:duration", "reboot:status", "os:support", "hardware:history", "drivers:history", "apps:history", "startup:history", "services:history", "crash:summary", "smart:trend", "cmd:dns_resolution", "cmd:dns_settings", "cmd:dns_comparison", "cmd:gateway_probe", "cmd:agent_logs"} or callback.startswith(
        ("screenshot:mask", "screenshot:preview", "display:", "window:", "admin:", "devmg:", "install:", "server:", "server_confirm:", "url:fav:", "versions:bootstrap:", "versions:canary:", "versions:rollout:", "cmd:shell-approve:", "cmd:shell-cancel:", "cmd:shell-stop:", "cmd:shell-dryrun:", "permhelp:", "permsettings:", "permverify:", "location:", "dock:", "catalog:synonym:", "catalog:snapshot:", "catalog:collection:", "catalog:saved-filter:", "catalog:inactivity:", "catalog:fields:", "catalog:tags:", "catalog:order:", "catalog:note:", "catalog:place:", "catalog:review:", "catalog:purpose:", "files:inbox", "cpu:history:", "memory:history:", "pressure:history:", "sysinfo:export:", "sysinfo:interval:", "sysinfo:threshold:", "sysinfo:aggregate:")
    ):
        return "OwnerFilter"
    if callback in {"menu:main", "menu:about", "menu:server", "menu:guest_devices", "guest:readonly", "url:cancel", "catalog:areas", "catalog:back"} or callback.startswith(("about:chapter:", "url:count:", "catalog:area:", "catalog:feature:", "catalog:search:", "catalog:filter:")):
        return "ReadOnlyFilter"
    if callback in {"cmd:net_ping", "cmd:geo_location"}:
        return "AnyAccessFilter"
    assert callback == "noop" or callback.startswith((
        "cmd:", "cat:", "dev:", "back:", "all:", "ev:", "adm:", "quiet:", "digest:",
        "files:", "media:", "fun:", "power:", "power_confirm:", "proc:", "kill:", "killq:", "rotate:",
        "volq:", "vol:", "mic:", "micdur:", "prank:", "prankpage:", "fav:", "hist:", "rep:",
        "cfm:", "openapp:", "stop:", "stopall:", "guardianstop:", "sleep:", "menu:", "versions:",
    )), f"Unclassified access contract: {callback}"
    return "AdminFilter"


# Review-maintained literal contracts, not derived from production declarations:
# route, category, wire command, collector seconds, sensitivity, risk.
# 0 means no collector wait; prompted input has no user-response deadline.
# Windows/macOS below is dispatch declaration, never live hardware acceptance.
_ACTION_CONTRACTS = {
    name: (category, command, float(seconds), sensitivity.replace("_", " "), risk.replace("_", " "))
    for name, category, command, seconds, sensitivity, risk in (
        line.split() for line in """
on_cmd_screenshot media screenshot 15 private_image privacy_read
on_screenshot_mask_start media screenshot 15 private_image privacy_read
on_display_catalog media display_catalog 15 display_metadata privacy_read
on_window_catalog media window_catalog 15 private_window_metadata privacy_read
on_window_capture media window_screenshot 20 selected_window_image privacy_read
on_display_contact_sheet media display_contact_sheet 20 selected_display_images privacy_read
on_media_temp_cleanup media media_temp_cleanup 15 bounded_temp_inventory privacy_cleanup
on_cmd_webcam media camera_catalog 15 camera_metadata privacy_read
on_camera_catalog media camera_catalog 15 camera_metadata privacy_read
on_camera_capture media webcam 20 private_image privacy_read
on_cmd_clipboard input clipboard 10 private_clipboard privacy_read
on_cmd_processes terminal processes 15 process_list privacy_read
on_proc_page terminal processes 15 process_list privacy_read
on_cmd_battery system battery 15 device_telemetry read
on_cmd_network network network 15 network_configuration privacy_read
on_cmd_services terminal services 15 service_list privacy_read
on_cmd_capabilities system capabilities 15 device_capabilities read
on_cmd_sysinfo system sysinfo 12 device_identifiers privacy_read
on_sysinfo_uncertainty system sysinfo 12 device_telemetry privacy_read
on_camera_quality media camera_quality 15 camera_diagnostics privacy_read
on_cmd_status system status_request 12 device_telemetry read
on_cmd_disks files disks 15 storage_information privacy_read
on_cmd_volume media volume_toggle 8 acknowledgment audio_change
on_vol_set media volume_set 8 acknowledgment audio_change
on_cmd_lock power lock 0 acknowledgment session_interruption
on_cmd_apps system sys_installed_apps 15 installed_applications privacy_read
on_cmd_wifi network net_wifi_passwords 12 credentials credential_disclosure
on_cmd_bluetooth network net_bluetooth_list 10 device_identifiers privacy_read
on_cmd_usb network usb_devices 10 device_identifiers privacy_read
on_cmd_netstat network netstat 10 network_connections privacy_read
on_cmd_startup terminal startup_list 10 startup_applications privacy_read
on_cmd_cmdhistory terminal sys_history_cmd 10 private_command_history privacy_read
on_cmd_smart system storage_smart 12 storage_diagnostics read
on_cmd_sys_uptime system sys_uptime 10 device_telemetry read
on_cmd_sys_clean_temp system sys_clean_temp 15 cleanup_result file_deletion
on_cmd_net_ping network net_ping 12 network_telemetry network_request
on_dns_resolution_input network dns_resolution 9 dns_records privacy_read
on_dns_comparison_input network dns_comparison 9 dns_answer_comparison privacy_read
on_cmd_dns_settings network dns_settings 9 configured_DNS_resolver_addresses privacy_read
on_cmd_gateway_probe network gateway_probe 9 local_gateway_status network_probe
on_cmd_captive_portal network captive_portal 8 credential_free_portal_status privacy_read
on_cmd_ipv6_connectivity network ipv6_connectivity 8 fixed_ipv6_dot_status privacy_read
on_cmd_tls_certificate network tls_certificate 8 verified_tls_certificate_status privacy_read
on_tls_certificate_input network tls_certificate 8 verified_tls_certificate_status privacy_read
on_cmd_service_probe network service_probe 0 owner_selected_host_port_protocol privacy_read
on_service_probe_input network service_probe 8 bounded_tcp_tls_status privacy_read
on_cmd_wifi_quality_history network wifi_quality_history 8 owner_requested_signal_sample_and_trend privacy_read
on_cmd_wifi_channel_summary network wifi_channel_summary 8 owner_requested_channel_and_width privacy_read
on_cmd_wifi_connection_history network wifi_connection_history 8 owner_requested_redacted_local_event_history privacy_read
on_cmd_proxy_status network proxy_status 8 proxy_source_categories privacy_read
on_cmd_vpn_route_status network vpn_route_status 8 aggregate_tunnel_interface_hints privacy_read
on_cmd_endpoint_batch network endpoint_batch 12 fixed_three_public_tcp_targets network_probe
on_cmd_dnssec_check network dnssec_check 9 single_configured_dns_resolver_dnssec_ad privacy_read
on_dnssec_check_input network dnssec_check 9 single_configured_dns_resolver_dnssec_ad privacy_read
on_cmd_network_traffic network network_traffic 8 owner_requested_manual_interface_traffic_sample privacy_read
on_cmd_route_trace network route_trace 18 bounded_route_trace privacy_read
on_route_trace_input network route_trace 18 bounded_route_trace privacy_read
on_cmd_path_mtu network path_mtu 0 owner_selected_IP privacy_read
on_path_mtu_input network path_mtu 20 icmp_path_MTU_estimate network_probe
on_cmd_geo_location network geo_location 16 approximate_location privacy_read
on_cmd_autorun_status power autorun_status 10 startup_status read
on_cmd_autorun_enable power autorun_enable 10 acknowledgment persistence_change
on_cmd_autorun_disable power autorun_disable 10 acknowledgment persistence_change
on_cmd_standby_sleep power standby_sleep 8 acknowledgment agent_interruption
on_cmd_wake power wake 8 acknowledgment agent_state_change
on_kill terminal kill_process 0 acknowledgment process_termination
on_rotate_select screen display_rotate 8 acknowledgment display_change
on_cmd_nightlight screen display_night_light 8 acknowledgment display_change
on_cmd_brightness screen display_brightness 8 acknowledgment display_change
on_fun_extip network ext_ip 10 public_IP_address privacy_read
on_fun_screenoff screen screen_off 0 acknowledgment display_interruption
on_fun_screensaver screen screensaver_on 0 acknowledgment display_change
on_prank_screamer pranks prank_screamer 12 acknowledgment sensory_disruption
on_prank_rickroll pranks prank_rickroll 18 acknowledgment browser_disruption
on_prank_matrix pranks prank_matrix 18 acknowledgment display_disruption
on_prank_siren pranks prank_siren 14 acknowledgment audio_disruption
on_prank_crazycursor pranks prank_crazy_cursor 14 acknowledgment input_disruption
on_prank_dancewin pranks prank_dancing_windows 14 acknowledgment display_disruption
on_prank_blackscreen pranks prank_black_screen 18 acknowledgment display_interruption
on_prank_randomsite pranks prank_random_site 14 acknowledgment browser_disruption
on_prank_swapmouse pranks prank_swap_mouse 0 acknowledgment input_disruption
on_prank_hidedesktop pranks prank_hide_desktop 0 acknowledgment display_disruption
on_all_status system status_request 6 all_device_telemetry bulk_read
on_all_screenshot media screenshot 14 all_device_private_images bulk_privacy_read
on_power_confirm power power 5 acknowledgment power_interruption
on_files_list files dir_list 15 private_paths privacy_read
on_files_find files find_file 20 private_paths privacy_read
on_files_get files file_get 30 private_file_contents data_disclosure
on_files_put files file_put 25 acknowledgment file_write
on_files_del files file_del 15 acknowledgment file_deletion
on_files_open files path_open 10 acknowledgment application_launch
on_fun_wallpaper screen wallpaper_set 20 acknowledgment desktop_change
on_wallpaper_random_meme screen wallpaper_set 15 acknowledgment desktop_change
on_wallpaper_photo_guide screen wallpaper_set 20 user_image desktop_change
on_cmd_clipset input clipboard_set 0 user_clipboard_text clipboard_write
on_cmd_url network open_url 0 user_URL browser_launch
on_url_count_select network open_url 0 user_URL browser_launch
on_url_favorite_open network open_url 0 saved_URL browser_launch
on_cmd_open_app terminal open_app 0 user_executable_path application_launch
on_openapp_ok terminal open_app 0 user_executable_path application_launch
on_fun_type input type_text 0 user_text input_injection
on_fun_hotkey input hotkey 0 user_hotkey input_injection
on_cmd_prockillname terminal proc_kill_name 0 user_process_name process_termination
on_fun_spam pranks msgbox_spam 15 user_text display_disruption
on_prank_shout pranks prank_shout_tts 15 user_text audio_disruption
on_cmd_text input notify 0 user_text display_injection
on_cmd_sound media sound 0 user_text audio_change
on_devmg_uninstall_ok power uninstall_agent 15 acknowledgment agent_removal
on_versions_install_confirm power agent_update 110 update_result executable_replacement
on_prank_stop_all pranks prank_stop_all 0 acknowledgment restore_desktop_state
on_sleep_ok power power 0 acknowledgment power_interruption
""".strip().splitlines()
    )
}


_NAVIGATION_ROUTES = frozenset("""
on_about_chapter on_menu_about on_menu_main on_menu_devices on_menu_server on_menu_admin on_menu_target
on_feature_catalog_areas on_feature_catalog_result_filter_start on_feature_catalog_permission_filter
on_feature_catalog_access_filter on_feature_catalog_result_filter_select on_feature_catalog_search_start
on_feature_catalog_search_cancel on_feature_catalog_area on_feature_catalog_detail
on_feature_catalog_synonym_add on_feature_catalog_synonym_cancel on_feature_catalog_back
on_permission_help
on_cpu_history_open
on_memory_history_open
on_memory_pressure_history_open
on_sleep_wake_history
on_startup_duration
on_pending_restart on_os_support on_hardware_inventory_history
on_guest_devices on_guest_readonly on_noop on_back_to_device on_dev on_cat_device on_cat_files on_files_menu
on_cat_input on_cat_media on_cat_network on_cat_power on_cat_pranks on_cat_screen on_cat_system on_cat_terminal
on_cmd_guardian_menu on_cmd_rotate on_menu_wallpaper on_fav_menu on_mic_opts on_microphone_preview_start on_vol_opts on_prank_page
on_ev_menu on_ev_quiet on_ev_digest on_ev_admins on_admin_users on_admin_user on_admin_devices
on_admin_permissions on_admin_texts on_admin_style on_admin_device_temporary_grant on_admin_permission_temporary_grant on_devmg_blocked on_manual_install on_versions_device
on_versions_server on_versions_list on_versions_detail on_url_favorites on_history on_kill_confirm
on_power_action on_server_dangerous_request on_devmg_delete on_devmg_uninstall on_devmg_block
on_devmg_clear_all on_versions_install_select on_versions_canary_select on_versions_install_cancel on_url_cancel
on_screenshot_mask_cancel on_screenshot_mask_send
on_device_search_start on_device_search_cancel on_device_filter_os_start on_device_filter_os_cancel
on_device_filter_heartbeat_start on_device_filter_heartbeat_cancel
on_device_filter_version_start on_device_filter_version_cancel
on_device_filter_capability_start on_device_filter_capability_cancel
on_device_filter_inventory_age_start on_device_filter_inventory_age_cancel
on_device_tags_list on_device_tags_edit on_device_tags_cancel
on_device_collection_list on_device_collection_create on_device_collection_view on_device_collection_edit_prompt
on_device_saved_filter_list on_device_saved_filter_create on_device_saved_filter_view
on_device_saved_filter_apply on_device_saved_filter_delete_prompt on_device_saved_filter_delete_confirm
on_device_sort_menu on_device_sort_choice on_device_catalog_order_edit on_device_catalog_order_move
on_device_compare_start on_device_compare_page on_device_compare_select on_device_duplicate_compare on_device_completeness on_device_registration_origin
on_device_inventory_wizard_start on_device_inventory_wizard_skip on_device_inventory_wizard_cancel
on_device_private_note_view on_device_private_note_edit on_device_private_note_cancel
on_device_place_label_edit on_device_place_label_cancel
on_device_review_date_edit on_device_review_date_cancel on_device_maintenance_edit on_device_maintenance_cancel
on_device_relation_manage on_device_relation_add on_device_relation_cancel on_device_relation_unlink
on_device_archive_list on_device_archive_page on_device_archive_restore on_device_archive_set
on_device_catalog_export on_device_catalog_import_start on_device_catalog_import_cancel on_device_catalog_import_document
on_device_purpose_view on_device_purpose_set on_device_purpose_clear
on_device_inventory_template_save on_device_inventory_template_name on_device_inventory_template_save_cancel
on_device_inventory_template_apply on_device_inventory_template_preview on_device_inventory_template_confirm
on_device_inventory_template_back on_device_inventory_template_cancel
on_pinned_devices_list on_device_pin_toggle
""".split())


_DEPENDENT_ACTION_CONTRACTS = {
    "on_shell_late_results": dict(category="terminal", os_support=("Telegram",), timeout=0.0,
                                   result_sensitivity="one correlated late shell result",
                                   risk="owner-only exact correlation; one-use view; never reruns command"),
    "on_shell_late_result_view": dict(category="terminal", os_support=("Telegram",), timeout=0.0,
                                       result_sensitivity="redacted shell stdout/stderr",
                                       risk="owner-only one-use reference; device/request must match; no repeat control"),
    "on_shell_runner_diagnostic": dict(
        category="terminal", os_support=("Windows", "macOS"), timeout=15.0,
        result_sensitivity="bounded shell-runner availability metadata",
        risk="owner-only passive diagnostic; no command or process is executed",
    ),
    "on_cmd_network_jitter": dict(category="network", os_support=("Telegram",), timeout=0.0,
                                   result_sensitivity="owner-selected literal IP input", risk="owner-only bounded ICMP RTT diagnostic prompt"),
    "on_network_jitter_input": dict(category="network", wire_action="network_jitter",
                                      os_support=("Windows", "macOS"), timeout=30.0,
                                      result_sensitivity="ten ICMP RTT samples and adjacent jitter estimate",
                                      risk="one owner-selected literal IP; bounded probes; no scanning"),
    "on_cmd_network_throughput": dict(category="network", os_support=("Telegram",), timeout=0.0,
                                       result_sensitivity="owner-selected HTTPS test endpoint", risk="owner-only bounded throughput probe"),
    "on_cmd_network_link_speed": dict(category="network", os_support=("Telegram",), timeout=0.0,
                                       result_sensitivity="selected adapter link speed", risk="owner-only read-only network metadata"),
    "on_shell_timing_report": dict(category="terminal", os_support=("Telegram",), timeout=0.0,
                                    result_sensitivity="aggregate shell timing from typed audit metadata",
                                    risk="owner-only read; no command text/output or approval-wait duration"),
    "on_shell_approval_inbox": dict(category="terminal", os_support=("Telegram",), timeout=0.0,
                                     result_sensitivity="owner-selected pending shell preview",
                                     risk="owner-only read; listing never approves or executes a command"),
    "on_shell_approval_inbox_view": dict(category="terminal", os_support=("Telegram",), timeout=0.0,
                                          result_sensitivity="exact pending shell command preview",
                                          risk="owner-only read; explicit existing one-use approve/cancel remains required"),
    "on_cmd_route_lookup": dict(category="network", os_support=("Telegram",), timeout=0.0,
                                  result_sensitivity="owner-selected literal IP prompt", risk="owner-only read-only route lookup"),
    "on_route_lookup_input": dict(category="network", wire_action="route_lookup",
                                    os_support=("Windows", "macOS"), timeout=8.0,
                                    result_sensitivity="selected OS route, gateway and interface",
                                    risk="one owner-selected literal IP; local route-table read; no network probes"),
    "on_network_link_speed_select": dict(category="network", wire_action="network_link_speed",
                                          os_support=("Windows", "macOS", "Telegram"), timeout=8.0,
                                          result_sensitivity="selected adapter link speed", risk="owner-only read-only network metadata"),
    "on_network_throughput_input": dict(category="network", wire_action="network_throughput",
                                         os_support=("Windows", "macOS"), timeout=30.0,
                                         result_sensitivity="bounded upload/download byte counts and durations",
                                         risk="single owner-selected HTTPS endpoint; 256 KiB download and 64 KiB zero-only upload"),
    "on_cmd_packet_loss_history": dict(category="network", os_support=("Telegram",), timeout=0.0,
                                         result_sensitivity="owner-selected literal IP input", risk="owner-only bounded ICMP diagnostic prompt"),
    "on_packet_loss_history_input": dict(category="network", wire_action="packet_loss_history",
                                            os_support=("Windows", "macOS"), timeout=30.0,
                                            result_sensitivity="ten bounded ICMP reply/loss outcomes and observed loss percentage",
                                            risk="one owner-selected literal IP; ten single-packet attempts; no scanning"),
    "on_cmd_latency_series": dict(category="network", os_support=("Telegram",), timeout=0.0,
                                    result_sensitivity="owner-selected host and port input", risk="owner-only bounded TCP diagnostic prompt"),
    "on_latency_series_input": dict(category="network", wire_action="latency_series",
                                      os_support=("Windows", "macOS"), timeout=10.0,
                                      result_sensitivity="five bounded TCP connect durations and aggregate statistics",
                                      risk="one owner-selected endpoint; five sequential connection attempts; no port-range scan"),
    "on_cmd_dns_resolution": dict(category="network", os_support=("Telegram",), timeout=0.0,
                                    result_sensitivity="owner-selected hostname input", risk="owner-only read-only diagnostic prompt"),
    "on_cmd_dns_settings": dict(category="network", wire_action="dns_settings",
                                  os_support=("Windows", "macOS"), timeout=9.0,
                                  result_sensitivity="configured DNS resolver addresses",
                                  risk="owner-only read of local resolver settings"),
    "on_cmd_dns_comparison": dict(category="network", os_support=("Telegram",), timeout=0.0,
                                    result_sensitivity="owner-selected resolver IP addresses",
                                    risk="owner-only prompt for bounded DNS queries"),
    "on_cmd_route_trace": dict(category="network", os_support=("Telegram",), timeout=0.0,
                                 result_sensitivity="owner-selected literal IP address",
                                 risk="owner-only prompt for bounded route trace"),
    "on_cmd_path_mtu": dict(category="network", os_support=("Telegram",), timeout=0.0,
                              result_sensitivity="owner-selected IPv4 literal",
                              risk="owner-only prompt for bounded ICMP DF probes"),
    "on_cmd_tls_certificate": dict(category="network", os_support=("Telegram",), timeout=0.0,
                                     result_sensitivity="owner-selected TLS hostname/IP and port",
                                     risk="owner-only prompt for one bounded TLS certificate check"),
    "on_tls_certificate_input": dict(category="network", wire_action="tls_certificate",
                                       os_support=("Windows", "macOS"), timeout=8.0,
                                       result_sensitivity="TLS leaf validity dates and SHA-256 fingerprint",
                                       risk="one owner-selected host/port; verified TLS first, no private key material"),
    "on_cmd_service_probe": dict(category="network", os_support=("Telegram",), timeout=0.0,
                                   result_sensitivity="owner-selected host, port and TCP/TLS mode",
                                   risk="owner-only prompt for a single endpoint; no port-range scanning"),
    "on_service_probe_input": dict(category="network", wire_action="service_probe",
                                     os_support=("Windows", "macOS"), timeout=8.0,
                                     result_sensitivity="single endpoint reachability state",
                                     risk="one owner-selected host/port; bounded TCP connect or verified TLS handshake"),
    "on_cmd_wifi_quality_history": dict(category="network", wire_action="wifi_quality_history",
                                           os_support=("Windows", "macOS"), timeout=8.0,
                                           result_sensitivity="Wi-Fi signal percentage or RSSI only",
                                           risk="one owner-requested sample; no SSID, password or background sampling"),
    "on_cmd_wifi_channel_summary": dict(category="network", wire_action="wifi_channel_summary",
                                           os_support=("Windows", "macOS"), timeout=8.0,
                                           result_sensitivity="current Wi-Fi channel and optional channel width",
                                           risk="single owner-requested read-only query; network identity is discarded"),
    "on_cmd_wifi_connection_history": dict(category="network", wire_action="wifi_connection_history",
                                              os_support=("Windows", "macOS"), timeout=8.0,
                                              result_sensitivity="connection event type and timestamp only",
                                              risk="owner-requested 24-hour OS log query; SSID and raw event text are discarded"),
    "on_cmd_proxy_status": dict(category="network", wire_action="proxy_status",
                                  os_support=("Windows", "macOS"), timeout=8.0,
                                  result_sensitivity="enabled proxy source categories only; no endpoints or credentials",
                                  risk="owner-only read-only OS settings summary; raw proxy values are discarded"),
    "on_cmd_vpn_route_status": dict(category="network", wire_action="vpn_route_status",
                                     os_support=("Windows", "macOS"), timeout=8.0,
                                     result_sensitivity="aggregate active/inactive tunnel-like interface counts only",
                                     risk="owner-only read-only heuristic; no interface names and no claim that all traffic is protected"),
    "on_cmd_endpoint_batch": dict(category="network", wire_action="endpoint_batch",
                                   os_support=("Windows", "macOS"), timeout=12.0,
                                   result_sensitivity="three fixed target labels and bounded reachability states only",
                                   risk="owner-only; one TCP attempt per compiled-in public target; no user-supplied hosts or scanning"),
    "on_cmd_path_diagnostic": dict(category="network", wire_actions=("gateway_probe", "dns_resolution", "route_trace", "endpoint_batch"),
                                    os_support=("Windows", "macOS"), timeout=18.0,
                                    result_sensitivity="four validated stage states; gateway address, DNS answer IPs and route hops are not displayed",
                                    risk="owner-only; reuses one-shot gateway, system DNS for example.com, route trace to 1.1.1.1 and three fixed TCP/443 checks"),
    "on_network_path_report_export": dict(category="network", wire_actions=(),
                                           os_support=("Windows", "macOS"), timeout=3.0,
                                           result_sensitivity="allowlisted stage states only; no Wi-Fi keys, cookies, tokens, IPs or route hops",
                                           risk="owner-only, one-use, ten-minute in-memory export from the exact current diagnostic card"),
    "on_cmd_dnssec_check": dict(category="network", wire_action="dnssec_check",
                                 os_support=("Windows", "macOS"), timeout=9.0,
                                 result_sensitivity="one owner-entered domain, configured resolver IP and bounded AD/rcode assessment",
                                 risk="owner-only; resolver must match current OS DNS settings; no domain enumeration"),
    "on_dnssec_check_input": dict(category="network", wire_action="dnssec_check",
                                   os_support=("Windows", "macOS"), timeout=9.0,
                                   result_sensitivity="one owner-entered domain, configured resolver IP and bounded AD/rcode assessment",
                                   risk="owner-only; resolver must match current OS DNS settings; no domain enumeration"),
    "on_cmd_network_traffic": dict(category="network", wire_action="network_traffic",
                                     os_support=("Windows", "macOS"), timeout=8.0,
                                     result_sensitivity="selected interface byte counters and manual delta",
                                     risk="owner-selected interface; manual samples only, no background polling"),
    "on_path_mtu_input": dict(category="network", wire_action="path_mtu",
                                os_support=("Windows", "macOS"), timeout=20.0,
                                result_sensitivity="ICMP path-MTU evidence",
                                risk="owner-selected IPv4; bounded non-fragmenting echo probes"),
    "on_route_trace_input": dict(category="network", wire_action="route_trace",
                                   os_support=("Windows", "macOS"), timeout=18.0,
                                   result_sensitivity="numeric intermediate route IPs",
                                   risk="owner-selected unicast address; bounded 12 hops and one correlated response"),
    "on_dns_comparison_input": dict(category="network", wire_action="dns_comparison",
                                      os_support=("Windows", "macOS"), timeout=9.0,
                                      result_sensitivity="answers from two owner-selected DNS resolvers",
                                      risk="owner-only direct A/AAAA DNS queries to literal resolver IPs"),
    "on_camera_series": dict(category="media", wire_action="camera_series",
                              os_support=("Windows", "macOS"), timeout=25.0,
                              result_sensitivity="private contact sheet from 2-4 explicitly requested local camera frames",
                              risk="owner-only explicit camera activation; bounded frames held in memory and released locally"),
    "on_media_request_cancel": dict(category="media", wire_action="media_cancel",
                                     os_support=("Windows", "macOS"), timeout=8.0,
                                     result_sensitivity="correlated media cancellation state",
                                     risk="owner-only cooperative cancellation; completed preview withheld after cancel request"),
    "on_device_catalog_export": dict(category="devices", os_support=("Telegram",), timeout=0.0,
                                      result_sensitivity="owner-selected allowlisted device inventory file",
                                      risk="owner-only local inventory export with explicit fields"),
    "on_device_catalog_import_start": dict(category="devices", os_support=("Telegram",), timeout=0.0,
                                            result_sensitivity="owner-only import preview prompt",
                                            risk="owner-only read-only catalog reconciliation"),
    "on_device_catalog_import_cancel": dict(category="devices", os_support=("Telegram",), timeout=0.0,
                                             result_sensitivity="catalog import session metadata",
                                             risk="owner-only import preview cancellation"),
    "on_device_catalog_import_document": dict(category="devices", os_support=("Telegram",), timeout=6.0,
                                               result_sensitivity="owner-only bounded catalog comparison",
                                               risk="read-only import preview; no device or agent mutation"),
    "on_device_catalog_order_edit": dict(category="devices", os_support=("Telegram",), timeout=0.0,
                                           result_sensitivity="owner-scoped device ordering controls",
                                           risk="owner-only catalog preference change"),
    "on_device_catalog_order_move": dict(category="devices", os_support=("Telegram",), timeout=0.0,
                                           result_sensitivity="owner-scoped device ordering result",
                                           risk="owner-only catalog preference change"),
    "on_device_compare_start": dict(category="devices", os_support=("Telegram",), timeout=0.0,
                                      result_sensitivity="owner-selected device comparison screen",
                                      risk="owner-only read of stored device inventory"),
    "on_device_compare_page": dict(category="devices", os_support=("Telegram",), timeout=0.0,
                                     result_sensitivity="owner-selected comparison page",
                                     risk="owner-only read of current visible inventory"),
    "on_device_compare_select": dict(category="devices", os_support=("Telegram",), timeout=0.0,
                                       result_sensitivity="read-only comparison of reported device fields",
                                       risk="owner-only read of stored device reports"),
    "on_device_duplicate_compare": dict(category="devices", os_support=("Telegram",), timeout=0.0,
                                          result_sensitivity="owner-requested read-only likely-duplicate comparison",
                                          risk="owner-only comparison suggestion; no merge or device command"),
    "on_device_completeness": dict(category="devices", os_support=("Telegram",), timeout=0.0,
                                      result_sensitivity="read-only card completeness and source timestamps",
                                      risk="owner-only read of saved device-card metadata"),
    "on_device_registration_origin": dict(category="devices", os_support=("Telegram",), timeout=0.0,
                                             result_sensitivity="read-only registration provenance metadata",
                                             risk="owner-only read of saved registration metadata"),
    "on_device_inventory_wizard_start": dict(category="devices", os_support=("Telegram",), timeout=0.0,
                                                result_sensitivity="owner-local device inventory edit flow",
                                                risk="owner-only write to saved device catalog metadata"),
    "on_device_inventory_wizard_skip": dict(category="devices", os_support=("Telegram",), timeout=0.0,
                                               result_sensitivity="owner-local inventory wizard progress",
                                               risk="owner-only non-remote inventory action"),
    "on_device_inventory_wizard_cancel": dict(category="devices", os_support=("Telegram",), timeout=0.0,
                                                 result_sensitivity="owner-local inventory wizard cancellation",
                                                 risk="owner-only non-remote inventory action"),
    "on_device_private_note_view": dict(category="devices", os_support=("Telegram",), timeout=0.0,
                                          result_sensitivity="private owner note for selected device",
                                          risk="owner-only private metadata read"),
    "on_device_private_note_edit": dict(category="devices", os_support=("Telegram",), timeout=0.0,
                                          result_sensitivity="private owner note edit prompt",
                                          risk="owner-only private metadata update request"),
    "on_device_private_note_cancel": dict(category="devices", os_support=("Telegram",), timeout=0.0,
                                            result_sensitivity="private owner note edit cancellation",
                                            risk="owner-only non-remote inventory action"),
    "on_device_place_label_edit": dict(category="devices", os_support=("Telegram",), timeout=0.0,
                                         result_sensitivity="owner-entered device place label",
                                         risk="owner-only local inventory metadata update"),
    "on_device_place_label_cancel": dict(category="devices", os_support=("Telegram",), timeout=0.0,
                                           result_sensitivity="manual place-label edit cancellation",
                                           risk="owner-only non-remote inventory action"),
    "on_device_review_date_edit": dict(category="devices", os_support=("Telegram",), timeout=0.0,
                                         result_sensitivity="owner-selected manual review-date edit prompt",
                                         risk="owner-only local catalog date update"),
    "on_device_review_date_cancel": dict(category="devices", os_support=("Telegram",), timeout=0.0,
                                           result_sensitivity="manual review-date edit cancellation",
                                           risk="owner-only non-remote catalog action"),
    "on_device_maintenance_edit": dict(category="devices", os_support=("Telegram",), timeout=0.0,
                                          result_sensitivity="temporary owner catalog maintenance period",
                                          risk="owner-only local metadata prompt; no agent control"),
    "on_device_maintenance_cancel": dict(category="devices", os_support=("Telegram",), timeout=0.0,
                                            result_sensitivity="maintenance-period edit cancellation",
                                            risk="owner-only non-remote catalog action"),
    "on_device_relation_manage": dict(category="devices", os_support=("Telegram",), timeout=0.0,
                                         result_sensitivity="owner-only linked device IDs",
                                         risk="owner-only local device catalog read"),
    "on_device_relation_add": dict(category="devices", os_support=("Telegram",), timeout=0.0,
                                      result_sensitivity="owner-entered linked device ID",
                                      risk="owner-only local catalog relationship change"),
    "on_device_relation_cancel": dict(category="devices", os_support=("Telegram",), timeout=0.0,
                                         result_sensitivity="device relation edit cancellation",
                                         risk="owner-only non-remote catalog action"),
    "on_device_relation_unlink": dict(category="devices", os_support=("Telegram",), timeout=0.0,
                                         result_sensitivity="owner-selected catalog relationship",
                                         risk="owner-only local catalog relationship change; no agent command"),
    "on_device_archive_list": dict(category="devices", os_support=("Telegram",), timeout=0.0,
                                     result_sensitivity="owner-only archived device catalog",
                                     risk="owner-only read of bot-local archive metadata"),
    "on_device_archive_page": dict(category="devices", os_support=("Telegram",), timeout=0.0,
                                     result_sensitivity="owner-only archived device page",
                                     risk="owner-only read of bot-local archive metadata"),
    "on_device_archive_restore": dict(category="devices", os_support=("Telegram",), timeout=0.0,
                                        result_sensitivity="owner-requested device record restoration",
                                        risk="owner-only reversible catalog visibility change"),
    "on_device_archive_set": dict(category="devices", os_support=("Telegram",), timeout=0.0,
                                     result_sensitivity="owner-requested device record archive",
                                     risk="owner-only reversible catalog visibility change; no agent command"),
    "on_device_purpose_view": dict(category="devices", os_support=("Telegram",), timeout=0.0,
                                     result_sensitivity="owner-selected device purpose controls",
                                     risk="owner-only inventory metadata read"),
    "on_device_purpose_set": dict(category="devices", os_support=("Telegram",), timeout=0.0,
                                    result_sensitivity="normalized device-purpose result",
                                    risk="owner-only local inventory metadata update"),
    "on_device_purpose_clear": dict(category="devices", os_support=("Telegram",), timeout=0.0,
                                      result_sensitivity="cleared device-purpose result",
                                      risk="owner-only local inventory metadata update"),
    "on_device_inventory_template_save": dict(category="devices", os_support=("Telegram",), timeout=0.0,
                                                result_sensitivity="owner-selected local inventory template source",
                                                risk="owner-only local metadata capture request"),
    "on_device_inventory_template_name": dict(category="devices", os_support=("Telegram",), timeout=0.0,
                                                 result_sensitivity="owner-provided inventory template name",
                                                 risk="owner-only local metadata template creation"),
    "on_device_inventory_template_save_cancel": dict(category="devices", os_support=("Telegram",), timeout=0.0,
                                                       result_sensitivity="inventory template creation cancellation",
                                                       risk="owner-only non-remote action"),
    "on_device_inventory_template_apply": dict(category="devices", os_support=("Telegram",), timeout=0.0,
                                                 result_sensitivity="owner-local template selection screen",
                                                 risk="owner-only metadata preview"),
    "on_device_inventory_template_preview": dict(category="devices", os_support=("Telegram",), timeout=0.0,
                                                   result_sensitivity="owner-local inventory template preview",
                                                   risk="owner-only metadata preview"),
    "on_device_inventory_template_confirm": dict(category="devices", os_support=("Telegram",), timeout=0.0,
                                                   result_sensitivity="owner-confirmed local inventory metadata update",
                                                   risk="owner-only local metadata update; no agent command"),
    "on_device_inventory_template_back": dict(category="devices", os_support=("Telegram",), timeout=0.0,
                                                 result_sensitivity="inventory template list revisit",
                                                 risk="owner-only non-remote action"),
    "on_device_inventory_template_cancel": dict(category="devices", os_support=("Telegram",), timeout=0.0,
                                                   result_sensitivity="inventory template application cancellation",
                                                   risk="owner-only non-remote action"),
    "on_file_inbox_open": dict(category="files", os_support=("Telegram",), timeout=0.0,
                                result_sensitivity="owner-scoped staged file metadata",
                                risk="owner-only local file-inbox read"),
    "on_file_inbox_upload_start": dict(category="files", os_support=("Telegram",), timeout=0.0,
                                        result_sensitivity="owner upload prompt",
                                        risk="owner-only staged file intake request"),
    "on_file_inbox_view": dict(category="files", os_support=("Telegram",), timeout=0.0,
                                result_sensitivity="owner-scoped staged file metadata and integrity state",
                                risk="owner-only local staged-file read"),
    "on_file_inbox_delete_prompt": dict(category="files", os_support=("Telegram",), timeout=0.0,
                                         result_sensitivity="staged file deletion confirmation prompt",
                                         risk="owner-only staging deletion request"),
    "on_file_inbox_delete_confirm": dict(category="files", os_support=("Telegram",), timeout=0.0,
                                          result_sensitivity="staging copy deletion result",
                                          risk="owner-confirmed deletion of staged copy"),
    "on_file_inbox_place_prompt": dict(category="files", os_support=("Telegram",), timeout=0.0,
                                        result_sensitivity="selected device and destination prompt",
                                        risk="owner-only file placement request"),
    "on_file_inbox_place_confirm": dict(category="files", wire_action="file_put",
                                         os_support=("Windows", "macOS"), timeout=25.0,
                                         result_sensitivity="owner-confirmed file placement result",
                                         risk="explicit owner-confirmed file write"),
    "on_window_toggle": dict(category="media", os_support=("Telegram",), timeout=0.0,
                              result_sensitivity="explicit single-window selection metadata", risk="non-action"),
    "on_window_cancel": dict(category="media", os_support=("Telegram",), timeout=0.0,
                              result_sensitivity="window selection discarded", risk="non-action"),
    "on_display_toggle": dict(category="media", os_support=("Telegram",), timeout=0.0,
                               result_sensitivity="explicit display selection metadata", risk="non-action"),
    "on_display_cancel": dict(category="media", os_support=("Telegram",), timeout=0.0,
                               result_sensitivity="selection discarded", risk="non-action"),
    "on_screenshot_mask_cancel": dict(category="media", os_support=("Windows", "macOS"), timeout=0.0,
                                        result_sensitivity="temporary image discarded", risk="non-action"),
    "on_screenshot_mask_send": dict(category="media", os_support=("Windows", "macOS"), timeout=0.0,
                                      result_sensitivity="owner-confirmed masked image", risk="owner-only image delivery"),
    "on_permission_settings": dict(category="system", os_support=("Windows", "macOS"), timeout=15.0,
                                    result_sensitivity="settings launch acknowledgment", risk="local settings navigation"),
    "on_permission_recheck": dict(category="system", wire_action="capabilities",
                                    os_support=("Windows", "macOS"), timeout=15.0,
                                    result_sensitivity="one-shot permission state after settings transition",
                                    risk="owner-only device capability read"),
    "on_cmd_geo_location_ip": dict(category="network", wire_action="geo_location",
                                    os_support=("Windows", "macOS"), timeout=16.0,
                                    result_sensitivity="approximate city/region",
                                    risk="explicit external IP lookup"),
    "on_cmd_geo_location_compare": dict(category="location", wire_action="geo_location",
                                         os_support=("Windows", "macOS"), timeout=16.0,
                                         result_sensitivity="one-shot system location and IP-derived city/region",
                                         risk="owner-approved location comparison"),
    "on_cpu_history_period": dict(category="system", wire_action="cpu_history",
                                   os_support=("Windows", "macOS"), timeout=(25.0, 40.0, 70.0),
                                   result_sensitivity="correlated local CPU utilization history",
                                   risk="bounded owner-only system telemetry read"),
    "on_memory_history_period": dict(category="system", wire_action="memory_history",
                                      os_support=("Windows", "macOS"), timeout=(25.0, 40.0, 70.0),
                                      result_sensitivity="correlated local RAM and swap utilization history",
                                      risk="bounded owner-only system telemetry read"),
    "on_volume_history": dict(category="system", wire_action="volume_history",
                               os_support=("Windows", "macOS"), timeout=12.0,
                               result_sensitivity="correlated filesystem capacity and measured usage trend",
                               risk="owner-only local storage telemetry read"),
    "on_memory_pressure_period": dict(category="system", wire_action="memory_pressure_history",
                                        os_support=("Windows", "macOS"), timeout=(25.0, 40.0, 70.0),
                                        result_sensitivity="correlated native memory-pressure telemetry",
                                        risk="bounded owner-only system telemetry read"),
    "on_thermal_snapshot": dict(category="system", wire_action="thermal_snapshot",
                                  os_support=("Windows", "macOS"), timeout=15.0,
                                  result_sensitivity="one-shot native thermal sensor readings",
                                  risk="owner-only local sensor telemetry read"),
    "on_battery_health": dict(category="system", wire_action="battery_health",
                                os_support=("Windows", "macOS"), timeout=15.0,
                                result_sensitivity="matched design/full battery capacity and derived wear estimate",
                                risk="owner-only local battery telemetry read"),
    "on_battery_cycles": dict(category="system", wire_action="battery_cycles",
                                os_support=("Windows", "macOS"), timeout=15.0,
                                result_sensitivity="system-reported battery cycle count",
                                risk="owner-only local battery telemetry read"),
    "on_energy_usage": dict(category="system", wire_action="energy_usage",
                              os_support=("Windows", "macOS"), timeout=15.0,
                              result_sensitivity="bounded app basenames and local estimated energy totals",
                              risk="owner-only read of local energy-estimation history"),
    "on_sleep_wake_history": dict(category="system", wire_action="sleep_wake_history",
                                     os_support=("Windows", "macOS"), timeout=15.0,
                                     result_sensitivity="bounded sleep/resume event timestamps",
                                     risk="owner-only local activity-history read"),
    "on_startup_duration": dict(category="system", wire_action="startup_duration",
                                  os_support=("Windows Diagnostics-Performance event 100", "macOS unsupported without a reliable public duration source"),
                                  timeout=15.0, result_sensitivity="one measured OS boot-duration value and event timestamp",
                                  risk="owner-only local system telemetry read"),
    "on_driver_inventory_history": dict(category="system", wire_action="driver_inventory",
                                          os_support=("Windows", "macOS"),
                                          timeout=15.0, result_sensitivity="bounded local driver names and versions",
                                          risk="owner-only local system telemetry read"),
    "on_installed_app_history": dict(category="system", wire_action="installed_app_inventory",
                                       os_support=("Windows", "macOS"), timeout=15.0,
                                       result_sensitivity="bounded local app names, publishers and versions",
                                       risk="owner-only local software inventory read"),
    "on_startup_inventory_history": dict(category="system", wire_action="startup_inventory",
                                           os_support=("Windows", "macOS"), timeout=15.0,
                                           result_sensitivity="bounded local startup entry names and scopes",
                                           risk="owner-only local startup configuration read"),
    "on_service_inventory_history": dict(category="system", wire_action="service_inventory",
                                           os_support=("Windows", "macOS"), timeout=15.0,
                                           result_sensitivity="bounded local service names, states and start types",
                                           risk="owner-only local service telemetry read"),
    "on_crash_summary": dict(category="system", wire_action="crash_summary",
                              os_support=("Windows", "macOS"), timeout=15.0,
                              result_sensitivity="aggregated local critical/error event metadata only",
                              risk="owner-only local event-log aggregate read"),
    "on_smart_trend": dict(category="system", wire_action="smart_trend",
                             os_support=("Windows", "macOS"),
                             timeout=15.0, result_sensitivity="allowlisted SMART counter snapshots without disk serials",
                             risk="owner-only local disk telemetry read"),
    "on_volume_topology": dict(category="system", wire_action="volume_topology",
                                os_support=("Windows", "macOS"), timeout=15.0,
                                result_sensitivity="hashed volume identifiers and bounded mount aliases",
                                risk="owner-only local storage topology read"),
    "on_clock_drift": dict(category="system", wire_action="clock_drift",
                             os_support=("Windows", "macOS"), timeout=15.0,
                             result_sensitivity="short local wall-clock versus monotonic elapsed comparison",
                             risk="owner-only local clock telemetry read"),
    "on_system_snapshot_history": dict(category="system", wire_action="sysinfo",
                                         os_support=("Windows", "macOS"), timeout=12.0,
                                         result_sensitivity="allowlisted CPU/RAM/disk percentages and platform, retained 30 days",
                                         risk="owner-only explicit local system summary history"),
    "on_system_snapshot_comparison": dict(category="system",
                                            os_support=("Windows", "macOS"), timeout=0.0,
                                            result_sensitivity="allowlisted differences between two saved local system summaries",
                                            risk="owner-only local snapshot comparison"),
    "on_system_snapshot_export": dict(category="system", os_support=("Windows", "macOS"), timeout=0.0,
                                       result_sensitivity="metadata-only selection of a retained summary snapshot",
                                       risk="owner-only local summary export selection"),
    "on_system_snapshot_export_selected": dict(category="system", os_support=("Windows", "macOS"), timeout=0.0,
                                                result_sensitivity="JSON file containing only date, platform and resource percentages",
                                                risk="owner-only explicit summary snapshot export"),
    "on_system_summary_interval": dict(category="system", os_support=("Windows", "macOS"), timeout=0.0,
                                        result_sensitivity="current owner-selected local metrics schedule",
                                        risk="owner-only read of local system-summary schedule"),
    "on_system_summary_interval_selected": dict(category="system", wire_action="sysinfo_interval",
                                                 os_support=("Windows", "macOS"), timeout=12.0,
                                                 result_sensitivity="platform and CPU/RAM/disk percentages at owner-selected interval",
                                                 risk="owner opt-in bounded system telemetry collection"),
    "on_system_summary_thresholds": dict(category="system", os_support=("Windows", "macOS"), timeout=0.0,
                                         result_sensitivity="current owner-configured CPU/RAM/disk/available thermal thresholds",
                                         risk="owner-only local alert configuration read"),
    "on_system_summary_threshold_selected": dict(category="system", os_support=("Windows", "macOS"), timeout=0.0,
                                                  result_sensitivity="allowlisted owner-selected resource alert threshold",
                                                  risk="owner-only local alert configuration change"),
    "on_system_summary_aggregate": dict(category="system", os_support=("Windows", "macOS"), timeout=0.0,
                                         result_sensitivity="scheduled CPU/RAM/disk sample counts and bounded aggregates",
                                         risk="owner-only local aggregate of previously scheduled telemetry"),
    "on_system_summary_aggregate_selected": dict(category="system", os_support=("Windows", "macOS"), timeout=0.0,
                                                  result_sensitivity="scheduled CPU/RAM/disk sample counts and bounded aggregates",
                                                  risk="owner-only local aggregate of previously scheduled telemetry"),
    "on_sysinfo_uncertainty": dict(category="system", wire_action="sysinfo",
                                    os_support=("Windows", "macOS"), timeout=12.0,
                                    result_sensitivity="fresh CPU/RAM/disk reading with capture time and quality labels",
                                    risk="owner-only one-shot local telemetry read"),
    "on_camera_quality": dict(category="media", wire_action="camera_quality",
                               os_support=("Windows", "macOS"), timeout=15.0,
                               result_sensitivity="single-frame local camera quality metrics without image content",
                               risk="owner-only explicit camera activation; frame released locally"),
    "on_pending_restart": dict(category="system", wire_action="pending_restart",
                                os_support=("Windows", "macOS"), timeout=15.0,
                                result_sensitivity="one-shot OS reboot-required marker and fixed reason IDs",
                                risk="owner-only local system telemetry read"),
    "on_os_support": dict(category="system", wire_action="os_support",
                           os_support=("Windows", "macOS"), timeout=15.0,
                           result_sensitivity="OS release identity checked against a dated offline lifecycle reference",
                           risk="owner-only local system telemetry read"),
    "on_hardware_inventory_history": dict(category="system", wire_action="hardware_inventory",
                                            os_support=("Windows", "macOS"), timeout=15.0,
                                            result_sensitivity="allowlisted hardware history without serial numbers",
                                            risk="owner-only local hardware inventory read"),
    "on_repeat": dict(category="history", os_support="unknown: selected HISTORY entry determines wire action",
                      timeout=0.0, result_sensitivity="unknown: selected HISTORY entry determines output",
                      risk="unknown: selected HISTORY entry determines side effect"),
    "on_cmd_wol": dict(category="power", os_support=("WoL-capable network adapter",), timeout=(0.0, 12.0),
                       result_sensitivity="network adapter address", risk="remote wake"),
    "on_server_status": dict(category="server", os_support=("Linux/systemd",), timeout=20.0,
                             result_sensitivity="redacted service status", risk="server read"),
    "on_server_logs": dict(category="server", os_support=("Linux/systemd",), timeout=25.0,
                           result_sensitivity="private server logs", risk="privacy read"),
    "on_server_metrics": dict(category="server", os_support=("server host",), timeout=0.0,
                              result_sensitivity="server telemetry", risk="read and metrics history write"),
    "on_server_chart": dict(category="server", os_support=("server host",), timeout=0.0,
                            result_sensitivity="server telemetry", risk="read and metrics history write"),
    "on_server_specs": dict(category="server", os_support=("Linux",), timeout=(15.0, 6 * 10.0),
                            result_sensitivity="bounded server hardware/OS information", risk="server read"),
    "on_server_terminal": dict(category="server", os_support=("Linux/systemd",), timeout=0.0,
                               result_sensitivity="prompt; allowlisted command output after input", risk="owner server command request"),
    "on_versions_bootstrap": dict(category="installation", os_support=("Windows", "macOS"), timeout=6.0,
                                  result_sensitivity="public signed installer instructions", risk="non-action"),
    "on_feature_catalog_areas": dict(category="catalog", os_support=("Telegram",), timeout=0.0,
                                      result_sensitivity="public feature metadata", risk="read-only navigation"),
    "on_feature_catalog_area": dict(category="catalog", os_support=("Telegram",), timeout=0.0,
                                     result_sensitivity="public feature metadata", risk="read-only navigation"),
    "on_feature_catalog_detail": dict(category="catalog", os_support=("Telegram",), timeout=0.0,
                                       result_sensitivity="public feature metadata", risk="read-only navigation"),
    "on_feature_catalog_back": dict(category="catalog", os_support=("Telegram",), timeout=0.0,
                                    result_sensitivity="public feature metadata", risk="read-only navigation"),
    "on_feature_catalog_search_start": dict(category="catalog", os_support=("Telegram",), timeout=0.0,
                                            result_sensitivity="public feature metadata", risk="read-only navigation"),
    "on_feature_catalog_search_cancel": dict(category="catalog", os_support=("Telegram",), timeout=0.0,
                                             result_sensitivity="public feature metadata", risk="read-only navigation"),
}
for _guardian_route in (
    "on_cmd_guardian_status", "on_cmd_guardian_start", "on_cmd_guardian_restart",
    "on_cmd_guardian_auto_on", "on_cmd_guardian_auto_off", "on_guardian_stop_ok", "on_stop_ok",
):
    _DEPENDENT_ACTION_CONTRACTS[_guardian_route] = dict(
        category="power", os_support=("Windows", "macOS"), timeout=12.0,
        result_sensitivity="guardian status/acknowledgment", risk="guardian read" if _guardian_route == "on_cmd_guardian_status" else "agent lifecycle/persistence change",
    )
_DEPENDENT_ACTION_CONTRACTS["on_stopall_ok"] = dict(
    category="power", os_support=("Windows", "macOS"), timeout=0.0,
    result_sensitivity="acknowledgment", risk="all-device agent interruption",
)
_DEPENDENT_ACTION_CONTRACTS["on_shell_approve"] = dict(
    category="terminal", os_support=("Windows", "macOS"), timeout=20.0,
    result_sensitivity="private shell output", risk="arbitrary code execution",
)
_DEPENDENT_ACTION_CONTRACTS["on_shell_cancel"] = dict(
    category="terminal", os_support=("server host",), timeout=0.0,
    result_sensitivity="request metadata", risk="non-action",
)
_DEPENDENT_ACTION_CONTRACTS["on_shell_dry_run"] = dict(
    category="terminal", os_support=("Telegram",), timeout=0.0,
    result_sensitivity="exact transformed shell command preview",
    risk="owner-only dry-run preparation; fresh one-time approval required",
)
_DEPENDENT_ACTION_CONTRACTS["on_cmd_shell_runbook"] = dict(
    category="terminal", os_support=("Telegram",), timeout=0.0,
    result_sensitivity="owner-entered ordered command plan", risk="owner-only staging; no command dispatch",
)
_DEPENDENT_ACTION_CONTRACTS["on_cmd_shell_runbook_templates"] = dict(
    category="terminal", os_support=("Telegram",), timeout=0.0,
    result_sensitivity="owner-managed runbook names and step counts",
    risk="owner-only local template library; no command dispatch",
)
_DEPENDENT_ACTION_CONTRACTS["on_shell_metadata_export_prompt"] = dict(
    category="terminal", os_support=("Telegram",), timeout=0.0,
    result_sensitivity="bounded selected shell execution metadata",
    risk="owner-only read-only export; command text, hashes and output excluded",
)
_DEPENDENT_ACTION_CONTRACTS["on_shell_metadata_export_text"] = dict(
    category="terminal", os_support=("Telegram",), timeout=0.0,
    result_sensitivity="bounded selected shell execution metadata",
    risk="owner-only normalized export; does not dispatch or repeat commands",
)
_DEPENDENT_ACTION_CONTRACTS["on_shell_runbook_template_page"] = dict(
    category="terminal", os_support=("Telegram",), timeout=0.0,
    result_sensitivity="owner-managed runbook names and step counts",
    risk="owner-only bounded local template library page",
)
_DEPENDENT_ACTION_CONTRACTS["on_shell_runbook_template_use"] = dict(
    category="terminal", os_support=("Telegram",), timeout=0.0,
    result_sensitivity="exact owner-authored command preview",
    risk="template selection creates only a draft; separate one-time approval remains required",
)
_DEPENDENT_ACTION_CONTRACTS["on_shell_runbook_template_save_start"] = dict(
    category="terminal", os_support=("Telegram",), timeout=0.0,
    result_sensitivity="owner-authored command template name prompt",
    risk="owner-only local library write; does not change execution audit or dispatch commands",
)
_DEPENDENT_ACTION_CONTRACTS["on_shell_runbook_template_rename_start"] = dict(
    category="terminal", os_support=("Telegram",), timeout=0.0,
    result_sensitivity="owner-authored template rename prompt",
    risk="owner-only local library rename; no execution or audit mutation",
)
_DEPENDENT_ACTION_CONTRACTS["on_shell_runbook_template_delete_start"] = dict(
    category="terminal", os_support=("Telegram",), timeout=0.0,
    result_sensitivity="owner-authored template deletion confirmation",
    risk="owner-only staged deletion; explicit second confirmation required",
)
_DEPENDENT_ACTION_CONTRACTS["on_shell_runbook_template_delete_confirm"] = dict(
    category="terminal", os_support=("Telegram",), timeout=0.0,
    result_sensitivity="template library deletion outcome",
    risk="owner-only confirmed local library deletion; execution audit remains unchanged",
)
_DEPENDENT_ACTION_CONTRACTS["on_shell_runbook_confirm"] = dict(
    category="terminal", os_support=("Telegram",), timeout=0.0,
    result_sensitivity="exact next shell command preview", risk="plan consent does not execute; fresh exact approval required per step",
)
_DEPENDENT_ACTION_CONTRACTS["on_shell_runbook_cancel"] = dict(
    category="terminal", os_support=("Telegram",), timeout=0.0,
    result_sensitivity="runbook cancellation state", risk="owner-only cancellation before execution",
)
_DEPENDENT_ACTION_CONTRACTS["on_shell_stop"] = dict(
    category="terminal", os_support=("Windows", "macOS"), timeout=8.0,
    result_sensitivity="correlated shell stop outcome", risk="owner-only cancellation of exact active approved process",
)
_DEPENDENT_ACTION_CONTRACTS["on_shell_result_page"] = dict(
    category="terminal", os_support=("server host",), timeout=0.0,
    result_sensitivity="private shell output", risk="owner-only bounded display",
)
_DEPENDENT_ACTION_CONTRACTS["on_shell_result_preview"] = dict(
    category="terminal", os_support=("server host",), timeout=0.0,
    result_sensitivity="private shell output", risk="owner-only redacted preview",
)
_DEPENDENT_ACTION_CONTRACTS["on_shell_result_json"] = dict(
    category="terminal", os_support=("server host",), timeout=0.0,
    result_sensitivity="private redacted shell fields", risk="owner-only bounded display",
)
_DEPENDENT_ACTION_CONTRACTS["on_shell_result_source"] = dict(
    category="terminal", os_support=("server host",), timeout=0.0,
    result_sensitivity="private shell output", risk="owner-only redacted source display",
)
_DEPENDENT_ACTION_CONTRACTS["on_shell_result_compare_select"] = dict(
    category="terminal", os_support=("Telegram",), timeout=0.0,
    result_sensitivity="diff of two owner-selected redacted outputs",
    risk="owner-only; both result records must still be fresh; no commands or original raw values are stored or executed",
)
_DEPENDENT_ACTION_CONTRACTS["on_shell_result_repeat"] = dict(
    category="terminal", os_support=("Telegram",), timeout=0.0,
    result_sensitivity="exact command text from one fresh prior shell result",
    risk="owner-only; creates a new one-use approval preview and never executes on selection",
)
_DEPENDENT_ACTION_CONTRACTS["on_versions_rollout"] = dict(
    category="power", os_support=("Windows", "macOS"), timeout=6.0,
    result_sensitivity="bounded update operation state", risk="executable replacement after canary health",
    wire_action="agent_update",
)
_DEPENDENT_ACTION_CONTRACTS["on_server_diagnostics_export"] = dict(
    category="server", os_support=("server host",), timeout=0.0,
    result_sensitivity="sanitized diagnostic metadata document", risk="owner-only privacy read",
)
_DEPENDENT_ACTION_CONTRACTS["on_screenshot_preview"] = dict(
    category="media", wire_action="screenshot", os_support=("Windows", "macOS"), timeout=15.0,
    result_sensitivity="private screenshot preview", risk="privacy read before explicit delivery",
)
_DEPENDENT_ACTION_CONTRACTS["on_screenshot_preview_compare"] = dict(
    category="media", wire_action="screenshot", os_support=("Windows", "macOS"), timeout=15.0,
    result_sensitivity="two private screenshot previews and visual difference",
    risk="owner-only privacy read before confirmed comparison delivery",
)
_DEPENDENT_ACTION_CONTRACTS["on_screenshot_preview_comparison_send"] = dict(
    category="media", os_support=("server host",), timeout=0.0,
    result_sensitivity="private screenshot comparison",
    risk="owner-confirmed privacy delivery",
)
_DEPENDENT_ACTION_CONTRACTS["on_screenshot_preview_send"] = dict(
    category="media", os_support=("server host",), timeout=0.0,
    result_sensitivity="private screenshot", risk="owner-confirmed privacy delivery",
)
_DEPENDENT_ACTION_CONTRACTS["on_screenshot_preview_annotate"] = dict(
    category="media", os_support=("server host",), timeout=0.0,
    result_sensitivity="private screenshot annotation input", risk="owner-only image transformation",
)
_DEPENDENT_ACTION_CONTRACTS["on_screenshot_preview_annotated_send"] = dict(
    category="media", os_support=("server host",), timeout=0.0,
    result_sensitivity="private annotated screenshot copy", risk="owner-confirmed privacy delivery",
)
_DEPENDENT_ACTION_CONTRACTS["on_screenshot_preview_cancel"] = dict(
    category="media", os_support=("server host",), timeout=0.0,
    result_sensitivity="staged screenshot discarded", risk="non-action",
)
_DEPENDENT_ACTION_CONTRACTS["on_microphone_test_start"] = dict(
    category="media", wire_action="microphone_catalog", os_support=("Windows", "macOS"), timeout=15.0,
    result_sensitivity="bounded microphone input choices",
    risk="owner-only explicit local input enumeration; recording has not started",
)
_DEPENDENT_ACTION_CONTRACTS["on_microphone_test_output"] = dict(
    category="media", wire_action="microphone_test", os_support=("Windows", "macOS"), timeout=10.0,
    result_sensitivity="local audio test completion metadata; no audio payload",
    risk="owner-selected microphone captures at most 0.75 seconds and plays only to the selected local output",
)
_DEPENDENT_ACTION_CONTRACTS["on_microphone_preview_output"] = dict(
    category="media", wire_action="microphone_preview", os_support=("Windows", "macOS"), timeout=25.0,
    result_sensitivity="owner-selected explicit voice sample after local playback",
    risk="owner confirms before Telegram delivery; local temp is removed",
)
_DEPENDENT_ACTION_CONTRACTS["on_microphone_preview_send"] = dict(
    category="media", os_support=("Telegram",), timeout=0.0,
    result_sensitivity="owner-confirmed voice delivery", risk="one-use owner decision",
)
_DEPENDENT_ACTION_CONTRACTS["on_microphone_preview_discard"] = dict(
    category="media", os_support=("Telegram",), timeout=0.0,
    result_sensitivity="staged voice discarded", risk="non-action",
)
_DEPENDENT_ACTION_CONTRACTS["on_audio_output_switch_start"] = dict(
    category="media", wire_action="audio_output_catalog", os_support=("Windows", "macOS"), timeout=8.0,
    result_sensitivity="fresh local audio output names",
    risk="owner-only enumeration; no system output change until an output is selected",
)
_DEPENDENT_ACTION_CONTRACTS["on_audio_output_switch_select"] = dict(
    category="media", wire_action="audio_output_switch", os_support=("Windows", "macOS"), timeout=8.0,
    result_sensitivity="system audio output change status and friendly names",
    risk="one-use owner selection changes the multimedia output and agent verifies or rolls back",
)
_DEPENDENT_ACTION_CONTRACTS["on_cmd_network_check_sets"] = dict(
    category="network", os_support=("Telegram",), timeout=0.0,
    result_sensitivity="owner-saved host/port/protocol definitions",
    risk="owner-only local configuration; no checks until an explicit run action",
)
_DEPENDENT_ACTION_CONTRACTS["on_cmd_external_ip_watch"] = dict(
    category="network", os_support=("Telegram",), timeout=0.0,
    result_sensitivity="owner-only stored public-IP check status",
    risk="owner-only read of explicit monitoring configuration",
)
_DEPENDENT_ACTION_CONTRACTS["on_external_ip_watch_enable"] = dict(
    category="network", os_support=("Telegram", "Windows", "macOS"), timeout=10.0,
    result_sensitivity="correlated public IP observation and change notification",
    risk="explicit owner opt-in; one public-IP lookup per selected device at a bounded interval",
)
_DEPENDENT_ACTION_CONTRACTS["on_external_ip_watch_disable"] = dict(
    category="network", os_support=("Telegram",), timeout=0.0,
    result_sensitivity="monitoring disabled status",
    risk="owner-only removal of external-IP monitoring and queued alerts",
)
_DEPENDENT_ACTION_CONTRACTS["on_network_check_set_create"] = dict(
    category="network", os_support=("Telegram",), timeout=0.0,
    result_sensitivity="owner-entered named endpoint set",
    risk="owner-only bounded local configuration; maximum 5 individual targets",
)
_DEPENDENT_ACTION_CONTRACTS["on_network_check_set_name"] = dict(
    category="network", os_support=("Telegram",), timeout=0.0,
    result_sensitivity="owner-entered set name",
    risk="owner-only FSM-bound local configuration",
)
_DEPENDENT_ACTION_CONTRACTS["on_network_check_set_endpoints"] = dict(
    category="network", os_support=("Telegram",), timeout=0.0,
    result_sensitivity="owner-selected individual host/port/protocol entries",
    risk="owner-only bounded local configuration; URL/range/port-range input rejected",
)
_DEPENDENT_ACTION_CONTRACTS["on_network_check_set_delete"] = dict(
    category="network", os_support=("Telegram",), timeout=0.0,
    result_sensitivity="saved network check-set metadata",
    risk="owner-only removal of saved endpoint definition",
)
_DEPENDENT_ACTION_CONTRACTS["on_network_check_set_run"] = dict(
    category="network", wire_action="service_probe", os_support=("Windows", "macOS"), timeout=40.0,
    result_sensitivity="bounded reachability states for at most five owner-selected targets",
    risk="owner-only explicit one-time run; one request per saved endpoint, no ranges or recurring scan",
)
_DEPENDENT_ACTION_CONTRACTS["on_dock_decision"] = dict(
    category="enrollment", os_support=("Telegram",), timeout=0.0,
    result_sensitivity="pairing request status and device fingerprint",
    risk="owner-only explicit device enrollment decision; private keys and broker credentials stay server-side",
)
_DEPENDENT_ACTION_CONTRACTS["on_cmd_agent_logs"] = dict(
    category="administration", wire_action="agent_logs", os_support=("Windows", "macOS"), timeout=18.0,
    result_sensitivity="bounded redacted XIDER component logs",
    risk="owner-only local diagnostic read; output redacted before Telegram delivery",
)


def _descriptive_callback_metadata(callback, route):
    """Independent descriptive contract, never consumed by authorization/UI.

    Sensitivity/risk describe the result and side effect; platforms are source
    dispatch declarations. Only a history-selected action has unknowable fields.
    """
    gate_name = _expected_callback_gate(callback)
    if callback.startswith("url:count:"):
        permission = "access_store.can_use_callback"
        grants = "owner or cmd:url grant on FSM-bound device; live recheck"
    elif gate_name == "OwnerFilter":
        permission, grants = "protected OwnerFilter", "protected owner"
    elif gate_name == "ReadOnlyFilter":
        permission, grants = "public ReadOnlyFilter", "owner/user/guest; blocked denied"
    else:
        permission = "access_store.can_use_callback"
        grants = "owner or user with matching callback/device authority decision"
        if callback.startswith(("all:", "ev:", "adm:", "quiet:", "digest:", "versions:")) or callback in {"cfm:stop_all", "stopall:ok"}:
            grants = "owner; nonowner rejected by access_store"
    metadata = {
        "callback": callback, "route": route.callback.__name__,
        "category": callback.split(":", 1)[0], "access_class": permission,
        "required_permission": permission,
        "allowed_roles_grants": grants, "os_support": ("server host",),
        "timeout": 0.0, "result_sensitivity": "UI navigation", "risk": "non-action",
    }
    if permission == "access_store.can_use_callback":
        metadata["required_permission"] = f"device grant: {callback}"
        if callback.startswith("url:count:"):
            metadata["required_permission"] = "device grant: cmd:url (FSM-bound device)"
        elif grants == "owner; nonowner rejected by access_store":
            metadata["required_permission"] = f"owner authority: {callback} (access_store)"
        elif callback in {"menu:main", "menu:devices", "menu:about", "back:device", "devices:search", "devices:search:cancel"}:
            metadata["required_permission"] = f"user navigation: {callback}"
        elif callback.startswith("dev:"):
            metadata["required_permission"] = f"device selection grant: {callback}"
    name = route.callback.__name__
    if name in _ACTION_CONTRACTS:
        category, command, timeout, sensitivity, risk = _ACTION_CONTRACTS[name]
        metadata.update(category=category, wire_action=command, timeout=timeout,
                        os_support=("Windows", "macOS"), result_sensitivity=sensitivity, risk=risk)
    elif name in {"on_network_traffic_alert_menu", "on_network_traffic_alert_set", "on_network_traffic_alert_off"}:
        metadata.update(category="network", wire_action="network_traffic_interval" if name != "on_network_traffic_alert_menu" else None,
                        os_support=("Windows", "macOS") if name != "on_network_traffic_alert_menu" else ("Telegram",),
                        timeout=8.0 if name != "on_network_traffic_alert_menu" else 0.0,
                        result_sensitivity="selected interface usage threshold and sample schedule",
                        risk="owner-only explicit opt-in; selected adapter only; owner-only alert; no address or identity data")
    elif name == "on_cmd_prank_generic":
        metadata.update(category="pranks", wire_action=callback[4:], timeout=15.0,
                        os_support=("Windows", "macOS"), result_sensitivity="acknowledgment",
                        risk="desktop/audio/input disruption")
    elif name in {"on_confirm_action", "on_shell_ok", "on_cmd_shell"}:
        metadata.update(category="terminal" if "shell" in callback else "power",
                        result_sensitivity="confirmation request")
        if callback == "cfm:shell":
            metadata.update(access_class="owner-approved request", required_permission="request grant: cfm:shell; approval: protected owner", allowed_roles_grants=
                            "owner or granted cfm:shell request; shell:ok approval requires owner")
        if callback in {"shell:ok", "cmd:shell"}:
            metadata.update(result_sensitivity="private shell output after input", risk="arbitrary code execution",
                            os_support=("Windows", "macOS"), timeout=20.0, wire_action="shell")
    elif name in _NAVIGATION_ROUTES:
        metadata["category"] = "navigation"
        if name in {"on_versions_list", "on_versions_detail", "on_versions_install_select"}:
            metadata.update(timeout=6.0, result_sensitivity="public release metadata", risk="catalog network read")
        if name == "on_manual_install":
            metadata.update(category="installation", os_support=("Windows", "macOS"),
                            result_sensitivity="public signed installer instructions")
        if name in {"on_admin_users", "on_admin_user", "on_admin_devices", "on_admin_permissions", "on_admin_texts"}:
            metadata.update(category="administration", result_sensitivity="private identities/device grants/settings",
                            risk="privacy read")
        if name in {"on_admin_device_temporary_grant", "on_admin_permission_temporary_grant"}:
            metadata.update(category="administration", result_sensitivity="private staged grant details",
                            risk="owner-only permission change after one-use confirmation")
        if name == "on_history":
            metadata.update(category="history", result_sensitivity="private command history", risk="privacy read")
        if name in {"on_menu_devices", "on_guest_devices", "on_dev", "on_cat_device"}:
            metadata["result_sensitivity"] = "role-filtered device inventory"
    elif name in {"on_mic_dur", "on_mic_level"}:
        metadata.update(category="media", wire_action="microphone_catalog", os_support=("Windows", "macOS"),
                        timeout=15.0, result_sensitivity="bounded local microphone input metadata",
                        risk="owner-only device enumeration; no recording")
    elif name == "on_audio_output_catalog":
        metadata.update(category="media", wire_action="audio_output_catalog", os_support=("Windows", "macOS"),
                        timeout=8.0, result_sensitivity="bounded local audio output metadata",
                        risk="owner-only device enumeration; no audio capture or system changes")
    elif name == "on_mic_input":
        metadata.update(category="media", wire_action="mic", os_support=("Windows", "macOS"),
                        timeout=72.0, result_sensitivity="private microphone audio",
                        risk="owner explicitly selects the local input before recording")
    elif name == "on_mic_level_input":
        metadata.update(category="media", wire_action="microphone_level", os_support=("Windows", "macOS"),
                        timeout=8.0, result_sensitivity="bounded aggregate microphone level; no audio payload",
                        risk="owner-selected one-second local input measurement")
    elif name == "on_mic_balance":
        metadata.update(category="media", wire_action="microphone_catalog", os_support=("Windows", "macOS"),
                        timeout=15.0, result_sensitivity="bounded local stereo microphone input metadata",
                        risk="owner-only device enumeration before a one-second local measurement")
    elif name == "on_mic_balance_input":
        metadata.update(category="media", wire_action="microphone_balance", os_support=("Windows", "macOS"),
                        timeout=8.0, result_sensitivity="bounded left/right RMS levels and balance; no audio payload",
                        risk="owner-selected one-second local stereo input measurement")
    elif name == "on_media_history":
        metadata.update(category="media", wire_action="media_history", os_support=("Windows", "macOS"),
                        timeout=8.0, result_sensitivity="bounded local media action times and outcomes; no media content",
                        risk="owner-only local metadata history read")
    elif name == "on_server_confirm":
        metadata.update(category="server", os_support=("Linux/systemd",),
                        timeout={"restart": 30.0, "update": 180.0, "rollback": 180.0}[callback.split(":")[1]],
                        result_sensitivity="server operation output", risk="server restart/executable replacement")
    elif name.startswith(("on_admin_", "on_adm_", "on_ev_")) or name in {
        "on_quiet_set", "on_digest_set", "on_fav_toggle", "on_server_approval", "on_server_autostart_toggle",
        "on_url_favorite_delete", "on_url_favorite_save_prompt", "on_devmg_allow", "on_devmg_rename",
        "on_devmg_delete_ok", "on_devmg_blockok", "on_devmg_unblock", "on_devmg_clear_all_confirm", "on_devmg_manualadd",
        "on_device_snapshot_list", "on_device_snapshot_start", "on_device_snapshot_toggle",
        "on_device_snapshot_name_prompt", "on_device_snapshot_name_input", "on_device_snapshot_view",
        "on_device_snapshot_cancel", "on_inactivity_list", "on_inactivity_capture", "on_inactivity_compare",
        "on_typed_fields_list", "on_typed_fields_back", "on_typed_field_create",
        "on_typed_field_definition_input", "on_typed_field_edit", "on_typed_field_value_choice",
        "on_typed_field_date_input",
    }:
        metadata.update(category="administration", result_sensitivity="identities/settings/device records",
                        risk="local configuration/access mutation")
        if name.startswith("on_inactivity_"):
            metadata.update(result_sensitivity="aggregate device activity counts",
                            risk="owner-only local inventory history")
        if name.startswith("on_typed_field") or name == "on_typed_fields_list":
            metadata.update(result_sensitivity="private typed inventory metadata",
                            risk="owner-only local inventory read/update")
        if name == "on_server_autostart_toggle":
            metadata.update(os_support=("Windows",), risk="server persistence change")
        if name == "on_admin_audit":
            metadata.update(result_sensitivity="private audit records", risk="privacy read")
        if name == "on_admin_message":
            metadata.update(result_sensitivity="user-directed message text", risk="owner messaging request")
    else:
        assert name in _DEPENDENT_ACTION_CONTRACTS, f"Missing independent action profile: {callback}/{name}"
        metadata.update(_DEPENDENT_ACTION_CONTRACTS[name])
    if callback in {"noop", "guest:readonly", "url:cancel"}:
        metadata["access_class"] = "explicitly non-action"
    return metadata


def _assert_expected_callback_gate(callback, route):
    gates = [item.callback for item in route.filters if item.magic is None]
    assert len(gates) == 1, (callback, route.callback.__name__, gates)
    assert type(gates[0]).__name__ == _expected_callback_gate(callback), (callback, route.callback.__name__)


def test_inventory_routes_have_explicit_server_access_filters(monkeypatch):
    # Losing a gate or accidentally registering an action as public must fail,
    # even if its keyboard remains hidden from a user.
    for callback in _rendered_callback_inventory(monkeypatch):
        for route in _callback_routes(callback):
            _assert_expected_callback_gate(callback, route)
            # Descriptive classes only: AdminFilter/AnyAccessFilter delegate
            # the actual grant decision to access_store.can_use_callback.
            metadata = _descriptive_callback_metadata(callback, route)
            assert {
                "callback", "route", "category", "required_permission", "allowed_roles_grants",
                "os_support", "timeout", "result_sensitivity", "risk",
            } <= set(metadata)
            assert isinstance(metadata["timeout"], (float, int, tuple)), callback
            if isinstance(metadata["timeout"], (float, int)):
                assert metadata["timeout"] >= 0
            assert metadata["risk"] != "unknown", callback
            assert metadata["os_support"] != "unknown", callback
            assert metadata["result_sensitivity"] != "unknown", callback
            assert metadata["access_class"] in {
                "protected OwnerFilter", "public ReadOnlyFilter", "access_store.can_use_callback", "owner-approved request",
                "explicitly non-action",
            }
            if metadata["access_class"] == "access_store.can_use_callback":
                assert metadata["required_permission"] == (
                    "device grant: cmd:url (FSM-bound device)" if callback.startswith("url:count:") else
                    f"owner authority: {callback} (access_store)" if callback.startswith(("all:", "ev:", "adm:", "quiet:", "digest:", "versions:")) or callback in {"cfm:stop_all", "stopall:ok"} else
                    f"user navigation: {callback}" if callback in {"menu:main", "menu:devices", "menu:about", "back:device", "devices:search", "devices:search:cancel"} else
                    f"device selection grant: {callback}" if callback.startswith("dev:") else f"device grant: {callback}"
                )
            for field in ("os_support", "result_sensitivity", "risk"):
                if isinstance(metadata[field], str) and metadata[field].startswith("unknown:"):
                    assert route.callback.__name__ == "on_repeat", (callback, field)
                    assert metadata[field] == {
                        "os_support": "unknown: selected HISTORY entry determines wire action",
                        "result_sensitivity": "unknown: selected HISTORY entry determines output",
                        "risk": "unknown: selected HISTORY entry determines side effect",
                    }[field]


def test_action_contract_records_source_proven_values():
    route = _callback_routes("cmd:screenshot")[0]
    metadata = _descriptive_callback_metadata("cmd:screenshot", route)
    assert metadata["category"] == "media"
    assert metadata["os_support"] == ("Windows", "macOS")
    assert metadata["timeout"] == 15.0
    assert metadata["result_sensitivity"] == "private image"
    assert metadata["risk"] == "privacy read"
    assert metadata["required_permission"] == "device grant: cmd:screenshot"


def test_action_contract_platforms_match_client_dispatch_declarations(monkeypatch):
    declared = {}
    root = Path(bot.__file__).resolve().parent.parent
    for platform, path in (("Windows", root / "XGENT-WDS/xgent_wds.py"),
                           ("macOS", root / "XGENT-MCS/xgent_mcs.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        assignment = next(node for node in tree.body if isinstance(node, ast.Assign)
                          and any(isinstance(target, ast.Name) and target.id == "SUPPORTED_COMMANDS"
                                  for target in node.targets))
        declared[platform] = set(ast.literal_eval(assignment.value))
    for callback in _rendered_callback_inventory(monkeypatch):
        for route in _callback_routes(callback):
            metadata = _descriptive_callback_metadata(callback, route)
            commands = metadata.get("wire_actions") or ((metadata["wire_action"],) if metadata.get("wire_action") else ())
            for command in commands:
                for platform in metadata["os_support"]:
                    assert command in declared[platform], (callback, command, platform)


@pytest.mark.parametrize("callback,expected_timeout,collector_name", [
    ("cmd:screenshot", 15.0, "screenshot_collector"),
    ("cmd:clipboard", 10.0, "clipboard_collector"),
    ("cmd:sysinfo", 12.0, "sysinfo_collector"),
    ("cmd:wifi", 12.0, "fun_text_collector"),
    ("cmd:apps", 15.0, "fun_text_collector"),
    ("cmd:volume", 8.0, "fun_text_collector"),
])
def test_source_proven_action_deadlines_reach_real_collectors(monkeypatch, callback, expected_timeout, collector_name):
    from aiogram.types import CallbackQuery, Chat, Message, User
    from datetime import datetime, timezone
    from types import SimpleNamespace

    owner = bot.ADMIN_ID
    monkeypatch.setattr(bot, "SESSION", bot.SessionRegistry())
    bot.SESSION["target"] = "device-1"
    monkeypatch.setattr(bot, "devices", FakeDevices({"device-1": {"name": "PC", "os": "Windows"}}))
    deadlines = []

    async def wait_for(target, action, timeout=12.0, command_id=None):
        assert target == "device-1"
        assert command_id == "contract-command"
        deadlines.append(timeout)
        return None

    async def noop(*args, **kwargs):
        return SimpleNamespace(chat=SimpleNamespace(id=owner), message_id=1)

    monkeypatch.setattr(getattr(bot, collector_name), "wait_for", wait_for)
    monkeypatch.setattr(bot, "publish_tracked", lambda *args, **kwargs: (True, "contract-command"))
    monkeypatch.setattr(CallbackQuery, "answer", noop)
    monkeypatch.setattr(bot, "_replace_callback_message", noop)
    monkeypatch.setattr(bot, "_replace_message_card", noop)
    user = User(id=owner, is_bot=False, first_name="Owner")
    message = Message(message_id=1, date=datetime.now(timezone.utc), chat=Chat(id=owner, type="private"), from_user=user)
    cq = CallbackQuery(id="deadline", from_user=user, chat_instance="test", data=callback, message=message)
    asyncio.run(bot.router.callback_query.trigger(cq))
    assert deadlines == [expected_timeout]
    assert _descriptive_callback_metadata(callback, _callback_routes(callback)[0])["timeout"] == expected_timeout


@pytest.mark.parametrize("callback", ["admin:users", "server:logs", "devmg:allow:device-1", "shell:ok"])
def test_owner_filter_downgrade_is_detected_by_independent_contract(monkeypatch, callback):
    from roles import AdminFilter

    route = _callback_routes(callback)[0]
    owner_filter = next(item for item in route.filters if item.magic is None)
    assert type(owner_filter.callback).__name__ == _expected_callback_gate(callback) == "OwnerFilter"
    monkeypatch.setattr(owner_filter, "callback", AdminFilter())
    with pytest.raises(AssertionError):
        _assert_expected_callback_gate(callback, route)


def test_shell_request_and_owner_approval_have_distinct_access_contracts():
    request = _descriptive_callback_metadata("cfm:shell", _callback_routes("cfm:shell")[0])
    approval = _descriptive_callback_metadata("shell:ok", _callback_routes("shell:ok")[0])
    assert _expected_callback_gate("cfm:shell") == "AdminFilter"
    assert request["access_class"] == "owner-approved request"
    assert request["required_permission"] == "request grant: cfm:shell; approval: protected owner"
    assert request["risk"] == "non-action"
    assert approval["required_permission"] == "protected OwnerFilter"
    assert approval["risk"] == "arbitrary code execution"
    assert approval["timeout"] == 20.0


@pytest.mark.parametrize("identity", ["owner", "full_device_user"])
def test_invalid_registered_prank_suffix_is_rejected_before_publish(monkeypatch, tmp_path, identity):
    from aiogram.types import CallbackQuery, User
    from contextvars import ContextVar
    from types import SimpleNamespace
    import roles

    monkeypatch.setattr(bot, "ADMIN_ID", 100)
    monkeypatch.setattr(roles, "ADMIN_ID", 100)
    owner, user = bot.ADMIN_ID, 200
    actor = owner if identity == "owner" else user
    monkeypatch.setattr(bot.access_store, "FILE", tmp_path / "access.json")
    monkeypatch.setattr(bot.access_store, "AUDIT_FILE", tmp_path / "audit.jsonl")
    bot.access_store.register_start(SimpleNamespace(id=user, username="test", first_name="Test", last_name=""))
    bot.access_store.set_role(owner, user, bot.Role.USER, owner)
    bot.access_store.toggle_device(owner, user, "device-1", owner)
    bot.access_store.toggle_callback(owner, user, "full_device", owner)
    monkeypatch.setattr(bot, "CURRENT_TG_USER", ContextVar("invalid_prank_actor", default=actor))
    monkeypatch.setattr(bot, "SESSION", bot.SessionRegistry())
    bot.SESSION["target"] = "device-1"
    monkeypatch.setattr(bot, "devices", FakeDevices({"device-1": {"name": "PC", "os": "Windows"}}))
    answers = []

    async def answer(_cq, text=None, **kwargs):
        answers.append((text, kwargs))

    def unexpected_publish(*args, **kwargs):
        pytest.fail(f"Invalid prank suffix reached publication for {identity}: {args}")

    monkeypatch.setattr(CallbackQuery, "answer", answer)
    monkeypatch.setattr(bot, "publish", unexpected_publish)
    monkeypatch.setattr(bot, "publish_tracked", unexpected_publish)
    data = "cmd:prank_not_a_real_action"
    assert bot.access_store.can_use_callback(actor, data, owner, "device-1") is True
    assert [r.callback.__name__ for r in _callback_routes(data)] == ["on_cmd_prank_generic"]
    callback = CallbackQuery(id="invalid-prank", from_user=User(id=actor, is_bot=False,
                             first_name="Test"), chat_instance="test", data=data)
    asyncio.run(bot.router.callback_query.trigger(callback))
    assert answers == [(bot._lex("send_failed"), {"show_alert": True})]


def test_unregistered_action_callback_has_no_dispatch_or_publish(monkeypatch):
    from aiogram.dispatcher.event.bases import UNHANDLED
    from aiogram.types import CallbackQuery, User

    def unexpected_publish(*args, **kwargs):
        pytest.fail("Unregistered callback reached a publish path")

    monkeypatch.setattr(bot, "publish", unexpected_publish)
    monkeypatch.setattr(bot, "publish_tracked", unexpected_publish)
    # A broad full_device family grant is not a registered route. Exercise the
    # actual observer with owner identity so authorization cannot hide a route.
    for data in ("cmd:unknown_action", "unknown:action", "server:unknown_action"):
        callback = CallbackQuery(id="inventory", from_user=User(id=bot.ADMIN_ID,
                                 is_bot=False, first_name="Owner"), chat_instance="inventory", data=data)
        assert asyncio.run(bot.router.callback_query.trigger(callback)) is UNHANDLED


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


def test_prank_action_rows_are_backed_by_declarative_navigation(monkeypatch):
    _setup(monkeypatch, {"pc": {"name": "PC", "os": "Windows"}})
    monkeypatch.setattr(bot, "SESSION", {"target": "pc"})
    monkeypatch.setattr(bot, "get_user_role", lambda _user_id: bot.Role.OWNER)
    monkeypatch.setattr(bot.access_store, "can_use_callback", lambda *_args: True)
    expected = {
        "prank.visual": {
            "prank:stop_all", "prank:screamer", "prank:rickroll50", "prank:matrix",
            "cmd:prank_bsod", "cmd:prank_fake_update", "cmd:prank_invert_screen",
            "prank:hidedesktop", "prank:dancewin", "prank:blackscreen", "cmd:prank_shake_window",
        },
        "prank.sound": {
            "prank:stop_all", "prank:siren", "prank:shout", "cmd:prank_beep_morse",
            "cmd:prank_sound_spooky", "cmd:prank_sound_fart", "cmd:prank_volume_jump",
            "cmd:prank_speak_time", "cmd:prank_say_whisper", "cmd:prank_laugh_track", "cmd:prank_random_beeps",
        },
        "prank.input": {
            "prank:stop_all", "prank:swapmouse", "prank:crazycursor", "cmd:prank_slow_mouse",
            "cmd:prank_glitch_cursor", "cmd:prank_random_clicks", "cmd:prank_keyboard_disco",
            "cmd:prank_caps_disco", "cmd:prank_cursor_circle", "cmd:prank_open_notepad_type", "cmd:prank_hacker_typer",
        },
        "prank.chaos": {
            "prank:stop_all", "fun:spam", "cmd:prank_open_calc_spam", "cmd:prank_fake_virus",
            "cmd:prank_alert_loop", "cmd:prank_paste_clipboard_spam", "cmd:prank_type_reversed",
            "cmd:prank_fake_error_spam", "cmd:prank_fake_delete_sys32", "cmd:prank_ghost_typer", "cmd:prank_earthquake",
        },
        "prank.memes": {
            "prank:stop_all", "cmd:prank_meme_wallpaper", "cmd:prank_cat_invaders",
            "cmd:prank_fake_ransom_cats", "cmd:prank_nyan_stream", "cmd:prank_confetti_winner",
            "cmd:prank_low_battery_fake", "cmd:prank_fbi_lock", "cmd:prank_open_browser_memes",
            "cmd:prank_rickroll_terminal", "prank:randomsite",
        },
    }

    for page_number, parent in enumerate(expected, start=1):
        rows = bot.feature_catalog.visible_navigation_pages(
            bot.feature_catalog.load_registry(), parent=parent, role="owner", grants=set(), platform="windows",
        )
        declared = {str(row["callback"]) for row in rows}
        rendered = set(_callback_data(bot.pranks_menu(page=page_number, target="pc", user_id=100)))
        action_callbacks = rendered - {
            f"prankpage:{index}" for index in range(1, 6)
        } - {"cat:device", "back:device", "menu:main"}
        assert declared == expected[parent]
        assert action_callbacks == declared

    tab_rows = bot.feature_catalog.visible_navigation_pages(
        bot.feature_catalog.load_registry(), parent="prank.tabs", role="owner", grants=set(), platform="windows",
    )
    assert {str(row["callback"]) for row in tab_rows} == {
        "prankpage:1", "prankpage:2", "prankpage:3", "prankpage:4", "prankpage:5",
    }


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


def test_long_button_label_ends_at_a_word_boundary():
    assert bot._limit_button_label("Configure screen recording access immediately", 26) == "Configure screen…"


def test_long_button_label_does_not_split_combining_grapheme():
    assert bot._limit_button_label("abcdefghij e\u0301clair details", 13) == "abcdefghij…"


def test_long_button_label_does_not_split_zwj_emoji_grapheme():
    family = "👨‍👩‍👧‍👦"
    assert bot._limit_button_label(f"abcdefghij {family} details", 14) == "abcdefghij…"


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
        bot.access_store, "can_use_callback",
        lambda user_id, callback, _owner_id, target=None: (
            user_id == 100
            or (
                user_id == 200
                and callback in {"menu:devices", "menu:main", "back:device", "cmd:autorun_status"}
                and target == "device-1"
            )
            or (user_id == 200 and callback == "dev:device-1" and target == "device-1")
        ),
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
    grants = {"cmd:screenshot", "cat:screen", "back:device", "menu:main"}
    monkeypatch.setattr(
        bot.access_store, "can_use_callback",
        lambda _user_id, callback, _owner_id, _target: callback in grants,
    )

    device_callbacks = _callback_data(bot.device_menu("device-1", 200))
    media_callbacks = _callback_data(bot.media_menu(200))
    system_callbacks = _callback_data(bot.system_menu(200))
    power_callbacks = _callback_data(bot.power_menu_new(200))

    assert "cat:screen" in device_callbacks
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

    # Legacy action submenus and confirmations must not leak guessed actions
    # even when a user reaches them from a stale card or hand-crafted callback.
    from contextvars import ContextVar
    monkeypatch.setattr(bot, "CURRENT_TG_USER", ContextVar("ui_user", default=200))
    assert "cmd:wallpaper_random_meme" not in _callback_data(bot.wallpaper_menu())
    assert "fav:toggle:shutdown" not in _callback_data(bot._favorites_kb("device-1", []))
    assert "shell:ok" not in _callback_data(bot.confirm_kb("shell:ok"))


def test_url_count_screen_uses_the_fsm_bound_url_grant(monkeypatch):
    from contextvars import ContextVar

    _setup(monkeypatch, {"device-1": {"name": "PC", "os": "Windows"}})
    monkeypatch.setattr(bot, "CURRENT_TG_USER", ContextVar("ui_user", default=200))
    monkeypatch.setattr(bot, "SESSION", {"target": "device-1"})
    monkeypatch.setattr(
        bot, "get_user_role",
        lambda user_id: bot.Role.OWNER if user_id == 100 else bot.Role.USER,
    )
    granted = True
    monkeypatch.setattr(
        bot.access_store, "can_use_callback",
        lambda user_id, callback, _owner_id, target=None:
            user_id == 100 or (
                user_id == 200 and (
                    callback == "menu:main"
                    or (
                        target == "device-1"
                        and callback == "cmd:url"
                        and granted
                    )
                )
            ),
    )

    visible = set(_callback_data(bot.url_count_keyboard(
        is_owner=False, user_id=200, target="device-1",
    )))
    assert {
        "url:count:1", "url:count:2", "url:count:3", "url:count:5",
        "url:cancel", "menu:main",
    } <= visible

    granted = False
    revoked = set(_callback_data(bot.url_count_keyboard(
        is_owner=False, user_id=200, target="device-1",
    )))
    assert "url:count:1" not in revoked
    assert revoked == {"url:cancel", "menu:main"}


def test_device_status_notices_confirm_transitions_without_heartbeat_starvation(monkeypatch):
    class MemoryDevices:
        def __init__(self):
            self.items = {"mac-1": {"name": "Mac", "online": False, "last_seen": 0}}

        def all(self):
            return dict(self.items)

        def get(self, device_id):
            return self.items.get(device_id)

        def upsert(self, device_id, info, **_kwargs):
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
        monkeypatch.setattr(bot, "verify_message_detailed", lambda payload, **_kwargs: (payload, "ok"))
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
        for _ in range(20):
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

        for _ in range(20):
            status("offline")
        await asyncio.sleep(0.05)
        assert len(notices) == 2

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
    assert "Серверная · сердце этой штуки" in texts


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
    assert calls[0] == ("send", 10, audio, bot.back_to_device_kb())
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
    monkeypatch.setattr(bot, "verify_message_detailed", lambda payload, **_kwargs: (payload, "ok"))
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
    from types import SimpleNamespace
    sent = []
    class FakeBot:
        async def send_message(self, user_id, text, **kwargs):
            sent.append((user_id, text))
            return SimpleNamespace(chat=SimpleNamespace(id=user_id), message_id=80)
    monkeypatch.setattr(bot, "bot", FakeBot())
    asyncio.run(bot._notify_owner("operation failed"))
    assert sent == [(int(bot.ADMIN_ID), "operation failed")]
    assert bot.ui_cards.get(int(bot.ADMIN_ID), int(bot.ADMIN_ID)) == 80


def test_server_menu_contains_metrics_chart_and_safe_terminal():
    markup = bot.server_menu(bot.ADMIN_ID)
    callbacks = _callback_data(markup)
    assert {"server:specs", "server:metrics", "server:chart", "server:terminal"}.issubset(set(callbacks))
    rows = [[button.callback_data for button in row] for row in markup.inline_keyboard]
    assert rows == [
        ["versions:server", "server:status"],
        ["server:specs", "server:metrics"],
        ["server:chart", "server:logs"],
        ["server:diagnostics", "server:terminal"],
        ["server:approval", "server:restart"],
        ["server:update", "server:rollback"],
        ["menu:main"],
    ]


def test_device_card_shows_guardian_state(monkeypatch):
    monkeypatch.setattr(bot.devices, "get", lambda device_id: {
        "name": "MacBook-Air.local", "os": "macOS 27.0", "version": "3.3.8",
        "online": True, "last_seen": __import__("time").time(),
        "guardian": {"version": "1.0", "version_source": "guardian"},
        "guardian_last_seen": __import__("time").time(),
    })
    text = bot.device_card("mac-1")
    assert "Guard Keeper" in text
    assert "v1.0" in text


def test_invalid_mqtt_envelope_logs_reason_without_payload(monkeypatch, caplog):
    monkeypatch.setattr(
        bot,
        "verify_message_detailed",
        lambda _data, **_kwargs: (None, "bad_hmac"),
    )

    with caplog.at_level("WARNING", logger="xgent.bot"):
        bot.on_mqtt_message(
            f"{bot.MQTT_PREFIX}/device-1/status",
            {"payload": {"sensitive_text": "must-not-be-logged"}},
        )

    assert "bad_hmac" in caplog.text
    assert "must-not-be-logged" not in caplog.text


def test_screenshot_mqtt_response_logs_status_without_image_payload(monkeypatch, caplog):
    encoded_image = "PRIVATE_SCREENSHOT_PAYLOAD"
    payload = {
        "type": "screenshot",
        "device_id": "device-1",
        "id": "request-1",
        "state": "success",
        "platform": "macOS",
        "image": encoded_image,
    }
    submitted = []
    monkeypatch.setattr(bot, "verify_message_detailed", lambda data, **_kwargs: (data, None))
    monkeypatch.setattr(bot.server_ops, "observe", lambda *args: None)
    monkeypatch.setattr(bot.screenshot_collector, "submit", lambda *args: submitted.append(("single", args)))
    monkeypatch.setattr(bot.multi_screenshot, "submit", lambda *args: submitted.append(("multi", args)))

    with caplog.at_level("INFO", logger="xgent.bot"):
        bot.on_mqtt_message(f"{bot.MQTT_PREFIX}/device-1/screenshot", payload)

    assert [kind for kind, _args in submitted] == ["single", "multi"]
    assert "state=success" in caplog.text
    assert f"image_b64_chars={len(encoded_image)}" in caplog.text
    assert encoded_image not in caplog.text


class CardTelegram:
    """Telegram boundary double; registry and card-routing logic remain real."""

    def __init__(self):
        self.cards = {30: {"text": "Menu", "media": False, "markup": "buttons"}}
        self.sent = []
        self.edited = []
        self.fail_edit = self.fail_send = self.fail_delete = 0

    async def edit_message_text(self, text, *, chat_id, message_id, reply_markup=None):
        if self.fail_edit:
            self.fail_edit -= 1
            raise RuntimeError("temporary network failure")
        if message_id not in self.cards:
            raise RuntimeError("message to edit not found")
        if self.cards[message_id]["media"]:
            raise RuntimeError("there is no text in the message to edit")
        self.cards[message_id].update(text=text, markup=reply_markup)
        self.edited.append(message_id)

    async def send_message(self, chat_id, text, **kwargs):
        return await self.send(chat_id, text, False, kwargs.get("reply_markup"))

    async def send_document(self, chat_id, document, **kwargs):
        return await self.send(chat_id, kwargs.get("caption"), True, kwargs.get("reply_markup"), document)

    async def send_photo(self, chat_id, photo, **kwargs):
        return await self.send(chat_id, kwargs.get("caption"), True, kwargs.get("reply_markup"), photo)

    async def send_voice(self, chat_id, voice, **kwargs):
        return await self.send(chat_id, kwargs.get("caption"), True, kwargs.get("reply_markup"), voice)

    async def send(self, chat_id, text, media, markup, document=None):
        from types import SimpleNamespace
        await asyncio.sleep(0)
        if self.fail_send:
            self.fail_send -= 1
            raise RuntimeError("temporary send failure")
        message_id = 31 + len(self.sent)
        self.cards[message_id] = dict(text=text, media=media, markup=markup, document=document)
        self.sent.append(message_id)
        return self.message(message_id, chat_id)

    async def delete_message(self, chat_id, message_id):
        if self.fail_delete:
            self.fail_delete -= 1
            raise RuntimeError("temporary delete failure")
        self.cards.pop(message_id, None)

    async def edit_message_reply_markup(self, *, chat_id, message_id, reply_markup):
        self.cards[message_id]["markup"] = reply_markup

    def message(self, message_id=30, chat_id=10, user_id=20, media=False):
        from types import SimpleNamespace
        async def edit(text, **kwargs):
            await self.edit_message_text(text, chat_id=chat_id, message_id=message_id, **kwargs)
        async def answer(text, **kwargs):
            return await self.send_message(chat_id, text, **kwargs)
        async def document(value, **kwargs):
            return await self.send_document(chat_id, value, **kwargs)
        return SimpleNamespace(chat=SimpleNamespace(id=chat_id), from_user=SimpleNamespace(id=user_id),
                               message_id=message_id, photo=[object()] if media else None,
                               edit_text=edit, answer=answer, answer_document=document)

    def callback(self, data="menu:main", message_id=30, media=False):
        from types import SimpleNamespace
        async def answer(*args, **kwargs):
            pass
        return SimpleNamespace(data=data, from_user=SimpleNamespace(id=20),
                               message=self.message(message_id, media=media), answer=answer)


def _card_telegram(monkeypatch, tmp_path):
    telegram = CardTelegram()
    monkeypatch.setattr(bot, "bot", telegram)
    monkeypatch.setattr(bot.ui_cards, "FILE", tmp_path / "cards.json")
    bot.ui_cards.set_card(10, 20, 30)
    return telegram


def _successful_screenshot_fixture(monkeypatch, tmp_path):
    from types import SimpleNamespace
    telegram = _card_telegram(monkeypatch, tmp_path)
    monkeypatch.setattr(bot, "SESSION", {"target": "mac1"})
    monkeypatch.setattr(bot, "_simple_command_still_allowed", lambda *args: True)
    monkeypatch.setattr(bot, "publish_tracked", lambda *args, **kwargs: (True, "screenshot-request"))
    async def wait(*args):
        return {"state": "success", "image": _jpeg_test_image_b64()}
    monkeypatch.setattr(bot, "screenshot_collector", SimpleNamespace(wait_for=wait))
    return telegram


def test_screenshot_sender_failure_renders_diagnostic_in_existing_card(monkeypatch, tmp_path):
    telegram = _successful_screenshot_fixture(monkeypatch, tmp_path)
    _setup(monkeypatch, {"mac1": {"name": "Mac", "os": "macOS"}})
    async def fail(*args, **kwargs):
        raise RuntimeError("Telegram <network> delivery failed")
    monkeypatch.setattr(telegram, "send_photo", fail)
    asyncio.run(bot.on_cmd_screenshot(telegram.callback("cmd:screenshot")))
    assert bot.ui_cards.get(10, 20) == 30
    assert list(telegram.cards) == [30]
    assert not telegram.sent
    assert "Не подтверждена доставка результата" in telegram.cards[30]["text"]
    assert "Telegram &lt;network&gt; delivery failed" in telegram.cards[30]["text"]


def test_media_sender_failure_keeps_default_card_recovery(monkeypatch, tmp_path):
    telegram = _card_telegram(monkeypatch, tmp_path)
    async def fail(*args, **kwargs):
        raise RuntimeError("Telegram send failed")
    result = asyncio.run(bot._replace_callback_with_media(telegram.callback(), fail, photo=b"PRIVATE"))
    assert result.message_id == 30
    assert bot.ui_cards.get(10, 20) == 30
    assert list(telegram.cards) == [30]
    assert telegram.cards[30]["text"] == "Menu"
    assert not telegram.sent


def test_screenshot_caption_escapes_device_label(monkeypatch, tmp_path):
    telegram = _successful_screenshot_fixture(monkeypatch, tmp_path)
    monkeypatch.setattr(bot, "target_label", lambda target: 'Mac <owner> & "guest"')
    captions = []
    original_send = telegram.send_photo
    async def send(*args, **kwargs):
        captions.append(kwargs["caption"])
        return await original_send(*args, **kwargs)
    monkeypatch.setattr(telegram, "send_photo", send)
    asyncio.run(bot.on_cmd_screenshot(telegram.callback("cmd:screenshot")))
    assert captions == ["📸 <b>Mac &lt;owner&gt; &amp; &quot;guest&quot;</b>"]
    assert bot.ui_cards.get(10, 20) != 30


def test_screenshot_preview_stages_original_until_one_use_owner_confirmation(monkeypatch, tmp_path):
    telegram = _successful_screenshot_fixture(monkeypatch, tmp_path)
    _setup(monkeypatch, {"mac1": {"name": "Mac", "os": "macOS"}})
    expected_original = __import__("base64").b64decode(_jpeg_test_image_b64())
    async def allowed(*args, **kwargs):
        return True
    monkeypatch.setattr(bot, "_callback_result_access_or_report", allowed)
    media_edits = []

    async def edit_media(**kwargs):
        media_edits.append(kwargs)
        telegram.cards[kwargs["message_id"]].update(
            text=kwargs["media"].caption,
            media=True,
            markup=kwargs.get("reply_markup"),
        )

    monkeypatch.setattr(telegram, "edit_message_media", edit_media, raising=False)

    asyncio.run(bot.on_screenshot_preview(telegram.callback("screenshot:preview")))

    assert len(telegram.sent) == 1
    preview_id = telegram.sent[0]
    preview_card = telegram.cards[preview_id]
    assert "Preview" in preview_card["text"]
    callbacks = _callback_data(preview_card["markup"])
    send_callback = next(value for value in callbacks if value.startswith("screenshot:preview:send:"))
    cancel_callback = next(value for value in callbacks if value.startswith("screenshot:preview:cancel:"))
    token = send_callback.rsplit(":", 1)[-1]
    assert cancel_callback.endswith(token)
    assert bot._SCREENSHOT_DELIVERY_CACHE[20]["original"] == expected_original
    assert not media_edits

    asyncio.run(bot.on_screenshot_preview_send(telegram.callback(send_callback, message_id=preview_id, media=True)))

    assert len(media_edits) == 1
    assert media_edits[0]["media"].media.data == expected_original
    assert _callback_data(telegram.cards[preview_id]["markup"]) == _callback_data(bot.back_to_device_kb())
    assert 20 not in bot._SCREENSHOT_DELIVERY_CACHE
    # A replayed confirmation has no staged original to release again.
    asyncio.run(bot.on_screenshot_preview_send(telegram.callback(send_callback, message_id=preview_id, media=True)))
    assert len(media_edits) == 1


def test_screenshot_preview_cancel_discards_staged_original(monkeypatch, tmp_path):
    telegram = _successful_screenshot_fixture(monkeypatch, tmp_path)
    _setup(monkeypatch, {"mac1": {"name": "Mac", "os": "macOS"}})
    async def allowed(*args, **kwargs):
        return True
    monkeypatch.setattr(bot, "_callback_result_access_or_report", allowed)
    asyncio.run(bot.on_screenshot_preview(telegram.callback("screenshot:preview")))
    preview_id = telegram.sent[0]
    cancel_callback = next(
        value for value in _callback_data(telegram.cards[preview_id]["markup"])
        if value.startswith("screenshot:preview:cancel:")
    )

    asyncio.run(bot.on_screenshot_preview_cancel(telegram.callback(cancel_callback, message_id=preview_id, media=True)))

    assert 20 not in bot._SCREENSHOT_DELIVERY_CACHE
    assert bot.ui_cards.get(10, 20) != preview_id
    assert preview_id not in telegram.cards
    assert not telegram.cards[bot.ui_cards.get(10, 20)]["media"]


def test_screenshot_preview_callbacks_are_owner_only():
    assert _expected_callback_gate("screenshot:preview") == "OwnerFilter"
    assert _expected_callback_gate("screenshot:preview:annotate:0123456789abcdef") == "OwnerFilter"
    assert _expected_callback_gate("screenshot:preview:send:0123456789abcdef") == "OwnerFilter"
    assert _expected_callback_gate("screenshot:preview:annotated:send:0123456789abcdef") == "OwnerFilter"
    assert _expected_callback_gate("screenshot:preview:cancel:0123456789abcdef") == "OwnerFilter"
    assert _expected_callback_gate("screenshot:preview:compare:0123456789abcdef") == "OwnerFilter"
    assert _expected_callback_gate("screenshot:preview:comparison:send:0123456789abcdef") == "OwnerFilter"


def test_screenshot_preview_keyboard_offers_non_destructive_annotation_and_confirmed_copy():
    plain = _callback_data(bot._screenshot_preview_keyboard("a" * 16))
    marked = _callback_data(bot._screenshot_preview_keyboard("a" * 16, has_annotation=True))
    assert f"screenshot:preview:annotate:{'a' * 16}" in plain
    assert f"screenshot:preview:send:{'a' * 16}" in plain
    assert not any(value.startswith("screenshot:preview:annotated:send:") for value in plain)
    assert f"screenshot:preview:annotated:send:{'a' * 16}" in marked


def test_screenshot_annotation_updates_the_same_card_and_preserves_original(monkeypatch, tmp_path):
    from types import SimpleNamespace

    telegram = _successful_screenshot_fixture(monkeypatch, tmp_path)
    _setup(monkeypatch, {"mac1": {"name": "Mac", "os": "macOS"}})
    monkeypatch.setattr(bot, "ADMIN_ID", 20)
    monkeypatch.setattr(bot, "get_user_role", lambda user_id: bot.Role.OWNER if user_id == 20 else bot.Role.GUEST)
    monkeypatch.setattr(bot, "_device_action_still_allowed", lambda *_args: True)
    async def allowed(*args, **kwargs):
        return True
    monkeypatch.setattr(bot, "_callback_result_access_or_report", allowed)
    expected_original = __import__("base64").b64decode(_jpeg_test_image_b64())
    media_edits = []
    captions = []

    async def edit_caption(**kwargs):
        captions.append(kwargs)

    async def edit_media(**kwargs):
        media_edits.append(kwargs)
        telegram.cards[kwargs["message_id"]].update(text=kwargs["media"].caption, media=True, markup=kwargs.get("reply_markup"))

    monkeypatch.setattr(telegram, "edit_message_caption", edit_caption, raising=False)
    monkeypatch.setattr(telegram, "edit_message_media", edit_media, raising=False)

    class State:
        def __init__(self):
            self.data = {}
            self.current = None
        async def set_state(self, value): self.current = value
        async def update_data(self, **kwargs): self.data.update(kwargs)
        async def get_data(self): return dict(self.data)
        async def clear(self): self.current = None; self.data.clear()

    state = State()
    asyncio.run(bot.on_screenshot_preview(telegram.callback("screenshot:preview")))
    card_id = telegram.sent[0]
    token = next(value.rsplit(":", 1)[-1] for value in _callback_data(telegram.cards[card_id]["markup"]) if value.startswith("screenshot:preview:annotate:"))
    asyncio.run(bot.on_screenshot_preview_annotate(telegram.callback(f"screenshot:preview:annotate:{token}", message_id=card_id, media=True), state))
    assert captions and "arrow 0.1,0.1,0.8,0.8" in captions[-1]["caption"]
    removed = []
    async def delete_input(): removed.append(True)
    message = SimpleNamespace(
        from_user=SimpleNamespace(id=20), chat=SimpleNamespace(id=10), message_id=99,
        text="arrow 0.1,0.1,0.8,0.8", delete=delete_input,
    )
    asyncio.run(bot.on_screenshot_annotation_input(message, state))

    entry = bot._SCREENSHOT_DELIVERY_CACHE[20]
    assert entry["original"] == expected_original
    assert entry["annotated"] != expected_original
    assert media_edits and media_edits[-1]["message_id"] == card_id
    assert media_edits[-1]["media"].media.data == entry["preview"]
    assert any(value.startswith("screenshot:preview:annotated:send:") for value in _callback_data(media_edits[-1]["reply_markup"]))
    assert removed == [True]
    assert telegram.sent == [card_id]


def test_invalid_screenshot_annotation_input_is_deleted_and_keeps_preview_retryable(monkeypatch):
    from types import SimpleNamespace

    monkeypatch.setattr(bot, "ADMIN_ID", 20)
    monkeypatch.setattr(bot, "get_user_role", lambda user_id: bot.Role.OWNER if user_id == 20 else bot.Role.GUEST)
    monkeypatch.setattr(bot, "_device_action_still_allowed", lambda *_args: True)
    image = __import__("base64").b64decode(_jpeg_test_image_b64())
    token = "a" * 16
    bot._store_screenshot_delivery_cache(20, "mac1", token, image, image, chat_id=10, message_id=30)
    captions = []
    monkeypatch.setattr(bot.bot, "edit_message_caption", lambda **kw: asyncio.sleep(0, result=captions.append(kw)), raising=False)
    removed = []

    class State:
        current = bot.Form.wait_screenshot_annotation_input.state
        data = {
            "annotation_token": token, "annotation_chat_id": 10, "annotation_message_id": 30,
            "authorization_callback": "screenshot:preview", "authorization_target": "mac1",
        }
        async def get_data(self): return dict(self.data)
        async def clear(self): self.current = None; self.data.clear()

    async def delete_input(): removed.append(True)
    message = SimpleNamespace(
        from_user=SimpleNamespace(id=20), chat=SimpleNamespace(id=10), message_id=90,
        text="unsupported and confusing input", delete=delete_input,
    )

    asyncio.run(bot.on_screenshot_annotation_input(message, State()))

    assert removed == [True]
    assert captions and "Попробуй ещё раз" in captions[-1]["caption"]
    assert bot._SCREENSHOT_DELIVERY_CACHE[20]["original"] == image


def test_screenshot_comparison_captures_second_image_and_sends_only_confirmed_diff(monkeypatch, tmp_path):
    from PIL import Image, ImageDraw
    import base64
    import io

    def image(mark: bool) -> bytes:
        source = Image.new("RGB", (320, 200), "white")
        if mark:
            ImageDraw.Draw(source).rectangle((40, 40, 120, 120), fill="black")
        output = io.BytesIO()
        source.save(output, format="PNG")
        return output.getvalue()

    before, after = image(False), image(True)
    telegram = _successful_screenshot_fixture(monkeypatch, tmp_path)
    _setup(monkeypatch, {"mac1": {"name": "Mac", "os": "macOS"}})
    monkeypatch.setattr(bot, "ADMIN_ID", 20)
    monkeypatch.setattr(bot, "get_user_role", lambda user_id: bot.Role.OWNER if user_id == 20 else bot.Role.GUEST)
    async def allowed(*args, **kwargs): return True
    monkeypatch.setattr(bot, "_callback_result_access_or_report", allowed)
    requests = []
    responses = {"before": before, "after": after}
    def publish(*args, **kwargs):
        request_id = "before" if not requests else "after"
        requests.append((args, kwargs, request_id))
        return True, request_id
    async def wait_for(_target, _kind, _timeout, request_id):
        return {"state": "success", "image": base64.b64encode(responses[request_id]).decode()}
    monkeypatch.setattr(bot, "publish_tracked", publish)
    monkeypatch.setattr(bot.screenshot_collector, "wait_for", wait_for)
    media_edits = []
    async def edit_media(**kwargs):
        media_edits.append(kwargs)
        telegram.cards[kwargs["message_id"]].update(text=kwargs["media"].caption, media=True, markup=kwargs.get("reply_markup"))
    monkeypatch.setattr(telegram, "edit_message_media", edit_media, raising=False)

    asyncio.run(bot.on_screenshot_preview(telegram.callback("screenshot:preview")))
    card_id = telegram.sent[0]
    initial_buttons = _callback_data(telegram.cards[card_id]["markup"])
    token = next(value.rsplit(":", 1)[-1] for value in initial_buttons if value.startswith("screenshot:preview:compare:"))
    asyncio.run(bot.on_screenshot_preview_compare(telegram.callback(f"screenshot:preview:compare:{token}", message_id=card_id, media=True)))

    entry = bot._SCREENSHOT_DELIVERY_CACHE[20]
    assert len(requests) == 2 and requests[1][0][0] == "screenshot"
    assert entry["original"] == before
    assert entry["comparison"] != before and entry["comparison"] != after
    assert media_edits[-1]["message_id"] == card_id
    assert "красным" in media_edits[-1]["media"].caption.lower()
    send_diff = next(value for value in _callback_data(media_edits[-1]["reply_markup"]) if value.startswith("screenshot:preview:comparison:send:"))

    asyncio.run(bot.on_screenshot_preview_comparison_send(telegram.callback(send_diff, message_id=card_id, media=True)))

    assert media_edits[-1]["media"].media.data == entry["comparison"]
    assert 20 not in bot._SCREENSHOT_DELIVERY_CACHE
    assert len(telegram.sent) == 1


def test_screenshot_comparison_mqtt_failure_answers_callback_once_and_keeps_preview(monkeypatch, tmp_path):
    telegram = _successful_screenshot_fixture(monkeypatch, tmp_path)
    _setup(monkeypatch, {"mac1": {"name": "Mac", "os": "macOS"}})
    monkeypatch.setattr(bot, "ADMIN_ID", 20)
    monkeypatch.setattr(bot, "get_user_role", lambda _user_id: bot.Role.OWNER)

    async def allowed(*args, **kwargs): return True
    monkeypatch.setattr(bot, "_callback_result_access_or_report", allowed)
    asyncio.run(bot.on_screenshot_preview(telegram.callback("screenshot:preview")))
    card_id = telegram.sent[0]
    buttons = _callback_data(telegram.cards[card_id]["markup"])
    token = next(value.rsplit(":", 1)[-1] for value in buttons if value.startswith("screenshot:preview:compare:"))
    monkeypatch.setattr(bot, "publish_tracked", lambda *args, **kwargs: (False, "req"))

    cq = telegram.callback(f"screenshot:preview:compare:{token}", message_id=card_id, media=True)
    answers = []

    async def answer(*args, **kwargs):
        answers.append((args, kwargs))

    cq.answer = answer
    edits = []

    async def edit_caption(**kwargs):
        edits.append(kwargs)

    monkeypatch.setattr(telegram, "edit_message_caption", edit_caption, raising=False)
    asyncio.run(bot.on_screenshot_preview_compare(cq))

    assert len(answers) == 1
    assert len(edits) == 1 and "preview" in edits[0]["caption"]
    assert bot._SCREENSHOT_DELIVERY_CACHE[20]["token"] == token


def test_stale_media_callback_and_operation_result_keep_current_index(monkeypatch, tmp_path):
    telegram = _card_telegram(monkeypatch, tmp_path)
    telegram.cards[30]["media"] = True
    telegram.fail_delete = 1
    stale = telegram.callback(media=True)
    async def scenario():
        current = await bot._replace_callback_message(stale, "Device")
        assert current.message_id == 31
        for _ in range(5):
            await bot._replace_callback_message(stale, "Home")
            await bot._replace_message_card(stale.message, 20, "Result")
        assert bot.ui_cards.get(10, 20) == 31
        assert telegram.sent == [31]
        assert telegram.cards[30]["markup"] is None
        assert telegram.cards[31]["text"] == "Result"
    asyncio.run(scenario())


def test_missing_card_send_failure_recovers_without_repeated_duplicates(monkeypatch, tmp_path):
    telegram = _card_telegram(monkeypatch, tmp_path)
    telegram.cards.clear()
    telegram.fail_send = 1
    stale = telegram.callback()
    async def scenario():
        await bot._replace_callback_message(stale, "Home")
        assert bot.ui_cards.get(10, 20) == 30
        await bot._replace_callback_message(stale, "Home")
        for _ in range(5):
            await bot._replace_callback_message(stale, "Home")
        assert bot.ui_cards.get(10, 20) == 31
        assert list(telegram.cards) == [31]
        assert telegram.sent == [31]
    asyncio.run(scenario())


def test_owner_lifecycle_feedback_edits_indexed_card(monkeypatch, tmp_path):
    telegram = _card_telegram(monkeypatch, tmp_path)
    monkeypatch.setattr(bot, "ADMIN_ID", 20)
    bot.ui_cards.set_card(20, 20, 30)
    asyncio.run(bot._notify_owner("Deferred restart failed"))
    assert telegram.sent == []
    assert telegram.cards[30]["text"] == "Deferred restart failed"
    assert bot.ui_cards.get(20, 20) == 30


def test_bulk_screenshots_deliver_one_indexed_archive_with_device_names(monkeypatch, tmp_path):
    import io
    import json
    import zipfile
    telegram = _card_telegram(monkeypatch, tmp_path)
    monkeypatch.setattr(bot, "ADMIN_ID", 20)
    monkeypatch.setattr(bot.transport, "publish_command", lambda *args: True)
    monkeypatch.setattr(bot.multi_screenshot, "reset", lambda: None)
    async def results(*args):
        return [{"device_id": "a", "image": base64.b64encode(b"image-a").decode()},
                {"device_id": "b", "image": base64.b64encode(b"image-b").decode()},
                {"device_id": "c", "image": base64.b64encode(b"image-c").decode()}]
    monkeypatch.setattr(bot.multi_screenshot, "wait_window", results)
    labels = {"a": "Laptop", "b": "Desktop", "c": "Ноут/../" + "🖥" * 100}
    monkeypatch.setattr(bot, "target_label", lambda value: labels[value])
    asyncio.run(bot.on_all_screenshot(telegram.callback("all:screenshot")))
    assert telegram.sent == [31]
    assert list(telegram.cards) == [31]
    assert bot.ui_cards.get(10, 20) == 31
    document = telegram.cards[31]["document"]
    with zipfile.ZipFile(io.BytesIO(document.data)) as archive:
        assert archive.read("01-a-Laptop.jpg") == b"image-a"
        assert archive.read("02-b-Desktop.jpg") == b"image-b"
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest[2]["device_id"] == "c"
        assert manifest[2]["device_name"] == labels["c"]
        assert "/" not in manifest[2]["file"] and "\\" not in manifest[2]["file"]
        assert archive.read(manifest[2]["file"]) == b"image-c"


def test_concurrent_missing_card_callbacks_create_one_replacement(monkeypatch, tmp_path):
    telegram = _card_telegram(monkeypatch, tmp_path)
    telegram.cards.clear()
    async def scenario():
        await asyncio.gather(*(bot._replace_callback_message(telegram.callback(), "Home") for _ in range(8)))
        assert telegram.sent == [31]
        assert bot.ui_cards.get(10, 20) == 31
        assert list(telegram.cards) == [31]
    asyncio.run(scenario())


@pytest.mark.parametrize("category", ["media", "screen", "input", "system", "network", "files",
                                     "terminal", "power", "pranks", "device"])
def test_navigation_category_operation_back_home_stays_one_card(monkeypatch, tmp_path, category):
    telegram = _card_telegram(monkeypatch, tmp_path)
    monkeypatch.setattr(bot, "ADMIN_ID", 20)
    _setup(monkeypatch, {"a": {"name": "Laptop", "os": "Windows", "online": True}})
    monkeypatch.setattr(bot, "get_user_role", lambda _user: bot.Role.OWNER)
    monkeypatch.setattr(bot, "_simple_command_still_allowed", lambda *_args: True)
    monkeypatch.setattr(bot, "publish_tracked", lambda *_args, **_kwargs: (True, "op-1"))
    class Collector:
        async def wait_for(self, *_args, **_kwargs):
            assert "Laptop" in telegram.cards[30]["text"]
            assert telegram.sent == []
            return {"ok": True, "text": "Operation complete"}
    monkeypatch.setattr(bot, "fun_text_collector", Collector())
    async def scenario():
        await bot.on_menu_main(telegram.callback())
        await bot.on_menu_devices(telegram.callback("menu:devices"))
        await bot.on_dev(telegram.callback("dev:a"))
        await getattr(bot, f"on_cat_{category}")(telegram.callback(f"cat:{category}"))
        await getattr(bot, f"on_cat_{category}")(telegram.callback(f"cat:{category}"))
        await bot.simple_command(telegram.callback("cmd:status"), "status", "⚙️", "Status", timeout=1)
        assert "Operation complete" in telegram.cards[30]["text"]
        await bot.on_back_to_device(telegram.callback("back:device"))
        assert "Laptop" in telegram.cards[30]["text"]
        await bot.on_menu_main(telegram.callback())
        assert bot.SESSION["target"] is None
        assert telegram.sent == []
        assert list(telegram.cards) == [30]
        assert bot.ui_cards.get(10, 20) == 30
    asyncio.run(scenario())


def test_versions_and_permission_check_round_trip_stays_one_card(monkeypatch, tmp_path):
    telegram = _card_telegram(monkeypatch, tmp_path)
    monkeypatch.setattr(bot, "ADMIN_ID", 20)
    _setup(monkeypatch, {
        "mac-a": {
            "name": "MacBook", "os": "macOS 14", "version": "4.2.1",
            "capabilities": {"commands": ["capabilities", "permission_settings"]},
        },
    })
    monkeypatch.setattr(bot, "SESSION", {"target": None})
    monkeypatch.setattr(bot, "get_user_role", lambda _user_id: bot.Role.OWNER)
    monkeypatch.setattr(bot, "_visible_device_items", lambda _user_id: bot.devices.all())
    async def allow_result(*_args, **_kwargs):
        return True

    monkeypatch.setattr(bot, "_callback_result_access_or_report", allow_result)
    monkeypatch.setattr(bot, "_device_action_still_allowed", lambda *_args: True)
    monkeypatch.setattr(bot.release_catalog.catalog, "cached", lambda: [])
    monkeypatch.setattr(bot, "publish_tracked", lambda *_args, **_kwargs: (True, "caps-1"))
    snapshot = {
        "schema": 1, "platform": "macOS 14", "checked_at": "2026-10-12T10:00:00+00:00",
        "hostname": "MacBook", "version": "4.2.1", "commands": ["permission_settings"],
        "items": [
            {"id": "camera", "state": "permission_unverified", "detail": "Not yet tested."},
        ],
    }

    class Capabilities:
        async def wait_for(self, *_args, **_kwargs):
            return snapshot

    monkeypatch.setattr(bot, "capabilities_collector", Capabilities())
    settings_calls = []

    async def settings_command(cq, action, emoji, label, **kwargs):
        settings_calls.append((action, kwargs))
        return await bot._replace_callback_message(cq, "Settings handoff result", reply_markup=bot.back_to_device_kb())

    monkeypatch.setattr(bot, "simple_command", settings_command)

    async def scenario():
        await bot.on_menu_main(telegram.callback("menu:main"))
        await bot.on_menu_devices(telegram.callback("menu:devices"))
        await bot.on_dev(telegram.callback("dev:mac-a"))
        assert "cmd:capabilities" in _callback_data(bot.device_menu("mac-a", 20))

        await bot.on_versions_device(telegram.callback("versions:device"))
        assert "versions:list:agent:0" in _callback_data(telegram.cards[30]["markup"])
        await bot.on_back_to_device(telegram.callback("back:device"))

        await bot.on_cmd_capabilities(telegram.callback("cmd:capabilities"))
        callbacks = _callback_data(telegram.cards[30]["markup"])
        assert "permhelp:camera" in callbacks
        assert "permsettings:camera" in callbacks

        await bot.on_permission_help(telegram.callback("permhelp:camera"))
        assert "Системные настройки" in telegram.cards[30]["text"]
        await bot.on_permission_settings(telegram.callback("permsettings:camera"))
        assert settings_calls == [("permission_settings", {"timeout": 15.0, "permission": "camera"})]

        await bot.on_back_to_device(telegram.callback("back:device"))
        await bot.on_menu_main(telegram.callback("menu:main"))
        assert telegram.sent == []
        assert list(telegram.cards) == [30]
        assert bot.ui_cards.get(10, 20) == 30

    asyncio.run(scenario())


@pytest.mark.parametrize("flow", ["callback", "input", "media", "document"])
def test_transient_card_failure_and_next_callback_recover_current_card(monkeypatch, tmp_path, flow):
    telegram = _card_telegram(monkeypatch, tmp_path)
    async def scenario():
        if flow in {"callback", "input"}:
            telegram.fail_edit = 2
            if flow == "callback":
                await bot._replace_callback_message(telegram.callback(), "Progress")
            else:
                await bot._replace_user_card(telegram.message(99), "Progress")
        elif flow == "media":
            telegram.fail_send = 1
            await bot._replace_callback_with_media(telegram.callback(), telegram.send_document,
                                                  document=bot.BufferedInputFile(b"data", filename="file.bin"))
        else:
            telegram.fail_send = 1
            await bot._replace_user_card_with_document(telegram.message(99),
                                                       bot.BufferedInputFile(b"data", filename="file.bin"), "Result")
        assert telegram.sent == []
        assert bot.ui_cards.get(10, 20) == 30
        telegram.fail_edit = 0
        await bot._replace_callback_message(telegram.callback(), "Recovered")
        assert telegram.cards[30]["text"] == "Recovered"
        assert telegram.sent == []
    asyncio.run(scenario())


def test_document_to_text_recovers_after_delete_and_neutralization_errors(monkeypatch, tmp_path):
    telegram = _card_telegram(monkeypatch, tmp_path)
    async def broken_markup(**kwargs):
        raise RuntimeError("temporary markup failure")
    monkeypatch.setattr(telegram, "edit_message_reply_markup", broken_markup)
    telegram.fail_delete = 2
    async def scenario():
        result = await bot._replace_user_card_with_document(
            telegram.message(99), bot.BufferedInputFile(b"data", filename="file.bin"), "Result", "buttons"
        )
        assert result == 31
        assert bot.ui_cards.get(10, 20) == 31
        await bot._replace_user_card(telegram.message(99), "Home")
        for _ in range(5):
            await bot._replace_callback_message(telegram.callback(), "Home")
        assert telegram.sent == [31, 32]
        assert bot.ui_cards.get(10, 20) == 32
        assert telegram.cards[32]["text"] == "Home"
    asyncio.run(scenario())


def test_callback_media_targets_callback_chat_and_deletes_indexed_card(monkeypatch, tmp_path):
    telegram = _card_telegram(monkeypatch, tmp_path)
    telegram.cards[40] = dict(text="Current", media=False, markup="buttons")
    bot.ui_cards.set_card(10, 20, 40)
    # Distinct chat/user IDs model a group chat and also catch stale callback IDs.
    sent_targets = []
    async def sender(chat_id, **kwargs):
        sent_targets.append(chat_id)
        return await telegram.send(chat_id, "Result", True, "buttons")
    asyncio.run(bot._replace_callback_with_media(telegram.callback(), sender, photo=object()))
    assert sent_targets == [10]
    assert 40 not in telegram.cards
    assert bot.ui_cards.get(10, 20) == 31


def test_screen_action_buttons_are_backed_by_declarative_navigation(monkeypatch):
    _setup(monkeypatch, {})
    monkeypatch.setattr(bot, "get_user_role", lambda _user_id: bot.Role.OWNER)
    monkeypatch.setattr(bot.access_store, "can_use_callback", lambda *_args: True)
    rows = bot.feature_catalog.visible_navigation_pages(
        bot.feature_catalog.load_registry(),
        parent="screen",
        role="owner",
        grants=set(),
        platform="windows",
    )
    declared_callbacks = {str(row["callback"]) for row in rows}
    rendered_callbacks = set(_callback_data(bot.screen_menu(user_id=100)))
    rendered_actions = rendered_callbacks - {"back:device", "menu:main"}
    assert declared_callbacks == rendered_actions


def test_system_action_buttons_are_backed_by_declarative_navigation(monkeypatch):
    _setup(monkeypatch, {"device-1": {"name": "PC", "os": "Windows 11"}})
    monkeypatch.setattr(bot, "SESSION", {"target": "device-1"})
    monkeypatch.setattr(bot, "get_user_role", lambda _user_id: bot.Role.OWNER)
    monkeypatch.setattr(bot.access_store, "can_use_callback", lambda *_args: True)
    rows = bot.feature_catalog.visible_navigation_pages(
        bot.feature_catalog.load_registry(),
        parent="system",
        role="owner",
        grants=set(),
        platform="windows",
    )
    declared_callbacks = {str(row["callback"]) for row in rows}
    rendered_callbacks = set(_callback_data(bot.system_menu(user_id=100)))
    rendered_actions = rendered_callbacks - {"back:device", "menu:main"}
    assert declared_callbacks == rendered_actions


def test_filevault_status_is_mac_only_owner_only_and_registry_backed(monkeypatch):
    _setup(monkeypatch, {"mac": {"name": "MacBook", "os": "macOS 27.0"},
                        "win": {"name": "PC", "os": "Windows 11"}})
    monkeypatch.setattr(bot, "SESSION", {"target": "mac"})
    monkeypatch.setattr(bot, "get_user_role", lambda _user_id: bot.Role.OWNER)
    monkeypatch.setattr(bot.access_store, "can_use_callback", lambda *_args: True)

    mac_rows = bot.feature_catalog.visible_navigation_pages(
        bot.feature_catalog.load_registry(), parent="system", role="owner",
        grants=set(), platform="macos",
    )
    win_rows = bot.feature_catalog.visible_navigation_pages(
        bot.feature_catalog.load_registry(), parent="system", role="owner",
        grants=set(), platform="windows",
    )
    assert "filevault:status" in {str(row["callback"]) for row in mac_rows}
    assert "filevault:status" in set(_callback_data(bot.system_menu(user_id=100)))
    assert "filevault:status" not in {str(row["callback"]) for row in win_rows}

    monkeypatch.setattr(bot, "get_user_role", lambda _user_id: bot.Role.GUEST)
    assert "filevault:status" not in set(_callback_data(bot.system_menu(user_id=300)))


def test_filevault_status_dispatches_only_to_supported_macos_agent(monkeypatch, tmp_path):
    telegram = _card_telegram(monkeypatch, tmp_path)
    _setup(monkeypatch, {"mac": {"name": "MacBook", "os": "macOS 27.0",
                                "capabilities": {"commands": ["filevault_status"]}},
                        "win": {"name": "PC", "os": "Windows 11",
                                "capabilities": {"commands": ["filevault_status"]}},
                        "oldmac": {"name": "Old Mac", "os": "macOS 13",
                                   "capabilities": {"commands": ["sysinfo"]}}})
    monkeypatch.setattr(bot, "SESSION", {"target": "mac"})
    monkeypatch.setattr(bot, "_callback_result_access_or_report",
                        lambda *_args, **_kwargs: asyncio.sleep(0, result=True))
    commands = []
    monkeypatch.setattr(bot, "simple_command", lambda *args, **kwargs:
                        asyncio.sleep(0, result=commands.append((args, kwargs))))

    async def scenario():
        await bot.on_filevault_status(telegram.callback("filevault:status"))
        assert len(commands) == 1
        assert commands[0][0][1:] == ("filevault_status", "🔐", "Статус FileVault")
        bot.SESSION["target"] = "win"
        await bot.on_filevault_status(telegram.callback("filevault:status"))
        bot.SESSION["target"] = "oldmac"
        await bot.on_filevault_status(telegram.callback("filevault:status"))

    asyncio.run(scenario())
    assert len(commands) == 1
    assert "не объявляет поддержку filevault_status" in telegram.cards[30]["text"]


def test_sip_status_is_mac_only_owner_only_and_registry_backed(monkeypatch):
    _setup(monkeypatch, {"mac": {"name": "MacBook", "os": "macOS 27.0"},
                        "win": {"name": "PC", "os": "Windows 11"}})
    monkeypatch.setattr(bot, "SESSION", {"target": "mac"})
    monkeypatch.setattr(bot, "get_user_role", lambda _user_id: bot.Role.OWNER)
    monkeypatch.setattr(bot.access_store, "can_use_callback", lambda *_args: True)

    mac_rows = bot.feature_catalog.visible_navigation_pages(
        bot.feature_catalog.load_registry(), parent="system", role="owner",
        grants=set(), platform="macos",
    )
    win_rows = bot.feature_catalog.visible_navigation_pages(
        bot.feature_catalog.load_registry(), parent="system", role="owner",
        grants=set(), platform="windows",
    )
    assert "sip:status" in {str(row["callback"]) for row in mac_rows}
    assert "sip:status" in set(_callback_data(bot.system_menu(user_id=100)))
    assert "sip:status" not in {str(row["callback"]) for row in win_rows}

    monkeypatch.setattr(bot, "get_user_role", lambda _user_id: bot.Role.GUEST)
    assert "sip:status" not in set(_callback_data(bot.system_menu(user_id=300)))


def test_sip_status_dispatches_only_to_supported_macos_agent(monkeypatch, tmp_path):
    telegram = _card_telegram(monkeypatch, tmp_path)
    _setup(monkeypatch, {"mac": {"name": "MacBook", "os": "macOS 27.0",
                                "capabilities": {"commands": ["sip_status"]}},
                        "win": {"name": "PC", "os": "Windows 11",
                                "capabilities": {"commands": ["sip_status"]}},
                        "oldmac": {"name": "Old Mac", "os": "macOS 13",
                                   "capabilities": {"commands": ["sysinfo"]}}})
    monkeypatch.setattr(bot, "SESSION", {"target": "mac"})
    monkeypatch.setattr(bot, "_callback_result_access_or_report",
                        lambda *_args, **_kwargs: asyncio.sleep(0, result=True))
    commands = []
    monkeypatch.setattr(bot, "simple_command", lambda *args, **kwargs:
                        asyncio.sleep(0, result=commands.append((args, kwargs))))

    async def scenario():
        await bot.on_sip_status(telegram.callback("sip:status"))
        assert len(commands) == 1
        assert commands[0][0][1:] == ("sip_status", "🛡", "Статус SIP")
        bot.SESSION["target"] = "win"
        await bot.on_sip_status(telegram.callback("sip:status"))
        bot.SESSION["target"] = "oldmac"
        await bot.on_sip_status(telegram.callback("sip:status"))

    asyncio.run(scenario())
    assert len(commands) == 1
    assert "не объявляет поддержку sip_status" in telegram.cards[30]["text"]


def test_gatekeeper_status_is_mac_only_owner_only_and_registry_backed(monkeypatch):
    _setup(monkeypatch, {"mac": {"name": "MacBook", "os": "macOS 27.0"},
                        "win": {"name": "PC", "os": "Windows 11"}})
    monkeypatch.setattr(bot, "SESSION", {"target": "mac"})
    monkeypatch.setattr(bot, "get_user_role", lambda _user_id: bot.Role.OWNER)
    monkeypatch.setattr(bot.access_store, "can_use_callback", lambda *_args: True)

    mac_rows = bot.feature_catalog.visible_navigation_pages(
        bot.feature_catalog.load_registry(), parent="system", role="owner",
        grants=set(), platform="macos",
    )
    win_rows = bot.feature_catalog.visible_navigation_pages(
        bot.feature_catalog.load_registry(), parent="system", role="owner",
        grants=set(), platform="windows",
    )
    assert "gatekeeper:status" in {str(row["callback"]) for row in mac_rows}
    assert "gatekeeper:status" in set(_callback_data(bot.system_menu(user_id=100)))
    assert "gatekeeper:status" not in {str(row["callback"]) for row in win_rows}

    monkeypatch.setattr(bot, "get_user_role", lambda _user_id: bot.Role.GUEST)
    assert "gatekeeper:status" not in set(_callback_data(bot.system_menu(user_id=300)))


def test_gatekeeper_status_dispatches_only_to_supported_macos_agent(monkeypatch, tmp_path):
    telegram = _card_telegram(monkeypatch, tmp_path)
    _setup(monkeypatch, {"mac": {"name": "MacBook", "os": "macOS 27.0",
                                "capabilities": {"commands": ["gatekeeper_status"]}},
                        "win": {"name": "PC", "os": "Windows 11",
                                "capabilities": {"commands": ["gatekeeper_status"]}},
                        "oldmac": {"name": "Old Mac", "os": "macOS 13",
                                   "capabilities": {"commands": ["sysinfo"]}}})
    monkeypatch.setattr(bot, "SESSION", {"target": "mac"})
    monkeypatch.setattr(bot, "_callback_result_access_or_report",
                        lambda *_args, **_kwargs: asyncio.sleep(0, result=True))
    commands = []
    monkeypatch.setattr(bot, "simple_command", lambda *args, **kwargs:
                        asyncio.sleep(0, result=commands.append((args, kwargs))))

    async def scenario():
        await bot.on_gatekeeper_status(telegram.callback("gatekeeper:status"))
        assert len(commands) == 1
        assert commands[0][0][1:] == ("gatekeeper_status", "🛡", "Статус Gatekeeper")
        bot.SESSION["target"] = "win"
        await bot.on_gatekeeper_status(telegram.callback("gatekeeper:status"))
        bot.SESSION["target"] = "oldmac"
        await bot.on_gatekeeper_status(telegram.callback("gatekeeper:status"))

    asyncio.run(scenario())
    assert len(commands) == 1
    assert "не объявляет поддержку gatekeeper_status" in telegram.cards[30]["text"]


def test_cpu_history_period_picker_is_owner_only_and_registry_backed(monkeypatch):
    _setup(monkeypatch, {"device-1": {"name": "Laptop", "os": "Windows 11"}})
    monkeypatch.setattr(bot, "SESSION", {"target": "device-1"})
    monkeypatch.setattr(bot, "get_user_role", lambda _user_id: bot.Role.OWNER)
    monkeypatch.setattr(bot.access_store, "can_use_callback", lambda *_args: True)

    owner_rows = bot.feature_catalog.visible_navigation_pages(
        bot.feature_catalog.load_registry(), parent="system.cpu_history", role="owner",
        grants=set(), platform="windows",
    )
    system_callbacks = set(_callback_data(bot.system_menu(user_id=100)))
    period_callbacks = set(_callback_data(bot.cpu_history_period_keyboard(user_id=100)))

    assert "cpu:history" in system_callbacks
    assert {str(row["callback"]) for row in owner_rows} == {
        "cpu:history:15", "cpu:history:30", "cpu:history:60", "cat:system", "menu:main",
    }
    assert period_callbacks == {str(row["callback"]) for row in owner_rows}

    guest_rows = bot.feature_catalog.visible_navigation_pages(
        bot.feature_catalog.load_registry(), parent="system.cpu_history", role="guest",
        grants=set(), platform="windows",
    )
    assert guest_rows == []
    monkeypatch.setattr(bot, "get_user_role", lambda _user_id: bot.Role.GUEST)
    assert "cpu:history" not in set(_callback_data(bot.system_menu(user_id=300)))


def test_cpu_history_does_not_wait_for_agent_that_advertises_no_command(monkeypatch, tmp_path):
    telegram = _card_telegram(monkeypatch, tmp_path)
    _setup(monkeypatch, {
        "device-1": {
            "name": "Laptop", "os": "Windows 11",
            "capabilities": {"commands": ["sysinfo", "screenshot"]},
        },
    })
    monkeypatch.setattr(bot, "ADMIN_ID", 20)
    monkeypatch.setattr(bot, "SESSION", {"target": "device-1"})
    monkeypatch.setattr(bot, "get_user_role", lambda _user_id: bot.Role.OWNER)
    monkeypatch.setattr(bot, "_callback_result_access_or_report", lambda *_args, **_kwargs: asyncio.sleep(0, result=True))
    published = []
    monkeypatch.setattr(bot, "publish_tracked", lambda *args, **kwargs: published.append((args, kwargs)) or (True, "request-1"))

    asyncio.run(bot.on_cpu_history_period(telegram.callback("cpu:history:15")))

    assert published == []
    assert "обнови агент" in telegram.cards[30]["text"].lower()


def test_memory_history_period_picker_is_owner_only_and_registry_backed(monkeypatch):
    _setup(monkeypatch, {"device-1": {"name": "Laptop", "os": "Windows 11"}})
    monkeypatch.setattr(bot, "SESSION", {"target": "device-1"})
    monkeypatch.setattr(bot, "get_user_role", lambda _user_id: bot.Role.OWNER)
    monkeypatch.setattr(bot.access_store, "can_use_callback", lambda *_args: True)

    owner_rows = bot.feature_catalog.visible_navigation_pages(
        bot.feature_catalog.load_registry(), parent="system.memory_history", role="owner",
        grants=set(), platform="windows",
    )
    system_callbacks = set(_callback_data(bot.system_menu(user_id=100)))
    period_callbacks = set(_callback_data(bot.memory_history_period_keyboard(user_id=100)))

    assert "memory:history" in system_callbacks
    assert {str(row["callback"]) for row in owner_rows} == {
        "memory:history:15", "memory:history:30", "memory:history:60", "cat:system", "menu:main",
    }
    assert period_callbacks == {str(row["callback"]) for row in owner_rows}

    monkeypatch.setattr(bot, "get_user_role", lambda _user_id: bot.Role.GUEST)
    assert "memory:history" not in set(_callback_data(bot.system_menu(user_id=300)))


def test_memory_history_does_not_wait_for_agent_that_advertises_no_command(monkeypatch, tmp_path):
    telegram = _card_telegram(monkeypatch, tmp_path)
    _setup(monkeypatch, {
        "device-1": {"name": "Laptop", "os": "Windows 11", "capabilities": {"commands": ["sysinfo"]}},
    })
    monkeypatch.setattr(bot, "ADMIN_ID", 20)
    monkeypatch.setattr(bot, "SESSION", {"target": "device-1"})
    monkeypatch.setattr(bot, "get_user_role", lambda _user_id: bot.Role.OWNER)
    monkeypatch.setattr(bot, "_callback_result_access_or_report", lambda *_args, **_kwargs: asyncio.sleep(0, result=True))
    published = []
    monkeypatch.setattr(bot, "publish_tracked", lambda *args, **kwargs: published.append((args, kwargs)) or (True, "request-1"))

    asyncio.run(bot.on_memory_history_period(telegram.callback("memory:history:15")))

    assert published == []
    assert "обнови агент" in telegram.cards[30]["text"].lower()


def test_memory_history_updates_the_existing_card_with_its_correlated_result(monkeypatch, tmp_path):
    telegram = _card_telegram(monkeypatch, tmp_path)
    _setup(monkeypatch, {
        "device-1": {"name": "Laptop", "os": "Windows 11", "capabilities": {"commands": ["memory_history"]}},
    })
    monkeypatch.setattr(bot, "ADMIN_ID", 20)
    monkeypatch.setattr(bot, "SESSION", {"target": "device-1"})
    monkeypatch.setattr(bot, "get_user_role", lambda _user_id: bot.Role.OWNER)
    monkeypatch.setattr(bot, "_callback_result_access_or_report", lambda *_args, **_kwargs: asyncio.sleep(0, result=True))
    published = []
    waited = []
    monkeypatch.setattr(bot, "publish_tracked", lambda *args, **kwargs: published.append((args, kwargs)) or (True, "request-1"))

    async def wait_for(*args):
        waited.append(args)
        return {
            "type": "memory_history", "schema": "xider.memory-history.v1", "feature_id": "D01-F03",
            "device_id": "device-1", "request_id": "request-1", "state": "supported",
            "source": "psutil.virtual_memory+psutil.swap_memory", "checked_at": "2026-10-16T08:00:00+00:00",
            "data": {"period_seconds": 15, "sample_interval_seconds": 5, "swap_state": "supported", "samples": [
                {"offset_seconds": 5, "ram_percent": 24.0, "swap_percent": 10.0},
                {"offset_seconds": 10, "ram_percent": 36.5, "swap_percent": 20.0},
                {"offset_seconds": 15, "ram_percent": 48.0, "swap_percent": 30.0},
            ]},
        }

    monkeypatch.setattr(bot.memory_history_collector, "wait_for", wait_for)
    asyncio.run(bot.on_memory_history_period(telegram.callback("memory:history:15")))

    assert published == [(("memory_history",), {"_target": "device-1", "period_seconds": 15})]
    assert waited == [("device-1", "memory_history", 25.0, "request-1")]
    assert list(telegram.cards) == [30]
    assert not telegram.sent
    assert "36.5%" in telegram.cards[30]["text"]
    assert "20%" in telegram.cards[30]["text"]


def test_volume_history_is_owner_only_and_registry_backed(monkeypatch):
    _setup(monkeypatch, {"device-1": {"name": "Laptop", "os": "Windows 11"}})
    monkeypatch.setattr(bot, "SESSION", {"target": "device-1"})
    monkeypatch.setattr(bot, "get_user_role", lambda _user_id: bot.Role.OWNER)
    monkeypatch.setattr(bot.access_store, "can_use_callback", lambda *_args: True)

    rows = bot.feature_catalog.visible_navigation_pages(
        bot.feature_catalog.load_registry(), parent="system", role="owner", grants=set(), platform="windows",
    )
    callbacks = set(_callback_data(bot.system_menu(user_id=100)))

    assert "volume:history" in callbacks
    assert any(row["callback"] == "volume:history" and row["roles"] == ["owner"] for row in rows)
    monkeypatch.setattr(bot, "get_user_role", lambda _user_id: bot.Role.GUEST)
    assert "volume:history" not in set(_callback_data(bot.system_menu(user_id=300)))


def test_volume_history_updates_the_existing_card_with_correlated_result(monkeypatch, tmp_path):
    telegram = _card_telegram(monkeypatch, tmp_path)
    _setup(monkeypatch, {"device-1": {"name": "Laptop", "os": "Windows 11",
                                    "capabilities": {"commands": ["volume_history"]}}})
    monkeypatch.setattr(bot, "ADMIN_ID", 20)
    monkeypatch.setattr(bot, "SESSION", {"target": "device-1"})
    monkeypatch.setattr(bot, "get_user_role", lambda _user_id: bot.Role.OWNER)
    monkeypatch.setattr(bot, "_callback_result_access_or_report", lambda *_args, **_kwargs: asyncio.sleep(0, result=True))
    published, waited = [], []
    monkeypatch.setattr(bot, "publish_tracked", lambda *args, **kwargs: published.append((args, kwargs)) or (True, "request-1"))

    async def wait_for(*args):
        waited.append(args)
        return {
            "type": "volume_history", "schema": "xider.volume-history.v1", "feature_id": "D01-F04",
            "device_id": "device-1", "request_id": "request-1", "state": "supported",
            "source": "psutil.disk_partitions+psutil.disk_usage", "checked_at": "2026-10-16T08:00:00+00:00",
            "data": {"history_minimum_span_seconds": 21600, "history_scope": "agent_process", "volumes": [{
                "mountpoint": "C:\\", "filesystem": "NTFS", "state": "supported", "total_bytes": 1_000_000,
                "used_bytes": 600_000, "free_bytes": 400_000, "used_percent": 60.0,
                "trend_state": "increasing", "observed_span_seconds": 28_800,
                "delta_used_bytes": 200_000, "used_bytes_per_hour": 25_000,
                "estimated_hours_to_full": 16.0, "history_scope": "agent_process",
            }]},
        }

    monkeypatch.setattr(bot.volume_history_collector, "wait_for", wait_for)
    asyncio.run(bot.on_volume_history(telegram.callback("volume:history")))

    assert published == [(("volume_history",), {"_target": "device-1"})]
    assert waited == [("device-1", "volume_history", 12.0, "request-1")]
    assert list(telegram.cards) == [30] and not telegram.sent
    assert "16.0 ч" in telegram.cards[30]["text"]


def test_memory_pressure_history_is_owner_only_and_registry_backed(monkeypatch):
    _setup(monkeypatch, {"device-1": {"name": "Laptop", "os": "Windows 11"}})
    monkeypatch.setattr(bot, "SESSION", {"target": "device-1"})
    monkeypatch.setattr(bot, "get_user_role", lambda _user_id: bot.Role.OWNER)
    monkeypatch.setattr(bot.access_store, "can_use_callback", lambda *_args: True)

    rows = bot.feature_catalog.visible_navigation_pages(
        bot.feature_catalog.load_registry(), parent="system.memory_pressure_history", role="owner",
        grants=set(), platform="windows",
    )
    callbacks = set(_callback_data(bot.system_menu(user_id=100)))
    periods = set(_callback_data(bot.memory_pressure_history_period_keyboard(user_id=100)))

    assert "pressure:history" in callbacks
    assert {str(row["callback"]) for row in rows} == {
        "pressure:history:15", "pressure:history:30", "pressure:history:60", "cat:system", "menu:main",
    }
    assert periods == {str(row["callback"]) for row in rows}
    monkeypatch.setattr(bot, "get_user_role", lambda _user_id: bot.Role.GUEST)
    assert "pressure:history" not in set(_callback_data(bot.system_menu(user_id=300)))


def test_memory_pressure_history_does_not_dispatch_to_agent_without_the_command(monkeypatch, tmp_path):
    telegram = _card_telegram(monkeypatch, tmp_path)
    _setup(monkeypatch, {"device-1": {
        "name": "Laptop", "os": "Windows 11", "capabilities": {"commands": ["memory_history"]},
    }})
    monkeypatch.setattr(bot, "ADMIN_ID", 20)
    monkeypatch.setattr(bot, "SESSION", {"target": "device-1"})
    monkeypatch.setattr(bot, "get_user_role", lambda _user_id: bot.Role.OWNER)
    monkeypatch.setattr(bot, "_callback_result_access_or_report", lambda *_args, **_kwargs: asyncio.sleep(0, result=True))
    published = []
    monkeypatch.setattr(bot, "publish_tracked", lambda *args, **kwargs: published.append((args, kwargs)) or (True, "request-1"))

    asyncio.run(bot.on_memory_pressure_period(telegram.callback("pressure:history:15")))

    assert published == []
    assert "обнови агент" in telegram.cards[30]["text"].lower()


def test_memory_pressure_history_edits_the_existing_card(monkeypatch, tmp_path):
    telegram = _card_telegram(monkeypatch, tmp_path)
    _setup(monkeypatch, {"device-1": {"name": "Mac", "os": "macOS", "capabilities": {
        "commands": ["memory_pressure_history"],
    }}})
    monkeypatch.setattr(bot, "ADMIN_ID", 20)
    monkeypatch.setattr(bot, "SESSION", {"target": "device-1"})
    monkeypatch.setattr(bot, "get_user_role", lambda _user_id: bot.Role.OWNER)
    monkeypatch.setattr(bot, "_callback_result_access_or_report", lambda *_args, **_kwargs: asyncio.sleep(0, result=True))
    published, waited = [], []
    monkeypatch.setattr(bot, "publish_tracked", lambda *args, **kwargs: published.append((args, kwargs)) or (True, "request-1"))

    async def wait_for(*args):
        waited.append(args)
        return {
            "type": "memory_pressure_history", "schema": "xider.memory-pressure-history.v1",
            "feature_id": "D01-F05", "device_id": "device-1", "request_id": "request-1",
            "state": "supported", "source": "macos_memory_pressure_utility_free_percent",
            "checked_at": "2026-10-16T08:00:00+00:00",
            "data": {"period_seconds": 15, "sample_interval_seconds": 5, "samples": [
                {"offset_seconds": 5, "state": "supported", "available_percent": 61.0},
                {"offset_seconds": 10, "state": "supported", "available_percent": 52.0},
                {"offset_seconds": 15, "state": "supported", "available_percent": 42.0},
            ]},
        }

    monkeypatch.setattr(bot.memory_pressure_history_collector, "wait_for", wait_for)
    asyncio.run(bot.on_memory_pressure_period(telegram.callback("pressure:history:15")))

    assert published == [(("memory_pressure_history",), {"_target": "device-1", "period_seconds": 15})]
    assert waited == [("device-1", "memory_pressure_history", 25.0, "request-1")]
    assert list(telegram.cards) == [30] and not telegram.sent
    assert "42%" in telegram.cards[30]["text"]


def test_thermal_snapshot_action_is_owner_only_registry_backed_and_edits_existing_card(monkeypatch, tmp_path):
    telegram = _card_telegram(monkeypatch, tmp_path)
    _setup(monkeypatch, {"device-1": {"name": "Laptop", "os": "Windows 11", "capabilities": {
        "commands": ["thermal_snapshot"],
    }}})
    monkeypatch.setattr(bot, "ADMIN_ID", 20)
    monkeypatch.setattr(bot, "SESSION", {"target": "device-1"})
    monkeypatch.setattr(bot, "get_user_role", lambda _user_id: bot.Role.OWNER)
    monkeypatch.setattr(bot.access_store, "can_use_callback", lambda *_args: True)
    monkeypatch.setattr(bot, "_callback_result_access_or_report", lambda *_args, **_kwargs: asyncio.sleep(0, result=True))
    published, waited = [], []
    monkeypatch.setattr(bot, "publish_tracked", lambda *args, **kwargs: published.append((args, kwargs)) or (True, "thermal-1"))

    async def wait_for(*args):
        waited.append(args)
        return {
            "type": "thermal_snapshot", "schema": "xider.thermal-snapshot.v1", "feature_id": "D01-F06",
            "device_id": "device-1", "request_id": "thermal-1", "state": "supported",
            "source": "windows_wmi_MSAcpi_ThermalZoneTemperature", "checked_at": "2026-10-16T08:00:00+00:00",
            "data": {"sensors": [{"label": "Thermal zone 1", "temperature_c": 60.1, "quality": "native_provider"}]},
        }

    monkeypatch.setattr(bot.thermal_snapshot_collector, "wait_for", wait_for)
    asyncio.run(bot.on_thermal_snapshot(telegram.callback("thermal:snapshot")))

    assert published == [(("thermal_snapshot",), {"_target": "device-1"})]
    assert waited == [("device-1", "thermal_snapshot", 15.0, "thermal-1")]
    assert list(telegram.cards) == [30] and not telegram.sent
    assert "60.1°C" in telegram.cards[30]["text"]


def test_battery_health_action_is_owner_only_registry_backed_and_edits_existing_card(monkeypatch, tmp_path):
    telegram = _card_telegram(monkeypatch, tmp_path)
    _setup(monkeypatch, {"device-1": {"name": "Laptop", "os": "Windows 11", "capabilities": {
        "commands": ["battery_health"],
    }}})
    monkeypatch.setattr(bot, "ADMIN_ID", 20)
    monkeypatch.setattr(bot, "SESSION", {"target": "device-1"})
    monkeypatch.setattr(bot, "get_user_role", lambda _user_id: bot.Role.OWNER)
    monkeypatch.setattr(bot.access_store, "can_use_callback", lambda *_args: True)
    monkeypatch.setattr(bot, "_callback_result_access_or_report", lambda *_args, **_kwargs: asyncio.sleep(0, result=True))
    published, waited = [], []
    monkeypatch.setattr(bot, "publish_tracked", lambda *args, **kwargs: published.append((args, kwargs)) or (True, "battery-1"))

    async def wait_for(*args):
        waited.append(args)
        return {
            "type": "battery_health_snapshot", "schema": "xider.battery-health.v1", "feature_id": "D01-F07",
            "device_id": "device-1", "request_id": "battery-1", "state": "supported",
            "source": "windows_wmi_battery_capacity", "checked_at": "2026-10-16T08:00:00+00:00",
            "data": {"batteries": [{"label": "Battery 1", "design_capacity_mwh": 50000,
                      "full_charge_capacity_mwh": 42000, "health_percent": 84.0,
                      "quality": "native_provider"}]},
        }

    monkeypatch.setattr(bot.battery_health_collector, "wait_for", wait_for)
    asyncio.run(bot.on_battery_health(telegram.callback("battery:health")))

    assert published == [(("battery_health",), {"_target": "device-1"})]
    assert waited == [("device-1", "battery_health", 15.0, "battery-1")]
    assert list(telegram.cards) == [30] and not telegram.sent
    assert "84.0%" in telegram.cards[30]["text"]


def test_battery_cycles_action_is_owner_only_registry_backed_and_edits_existing_card(monkeypatch, tmp_path):
    telegram = _card_telegram(monkeypatch, tmp_path)
    _setup(monkeypatch, {"device-1": {"name": "Laptop", "os": "Windows 11", "capabilities": {
        "commands": ["battery_cycles"],
    }}})
    monkeypatch.setattr(bot, "ADMIN_ID", 20)
    monkeypatch.setattr(bot, "SESSION", {"target": "device-1"})
    monkeypatch.setattr(bot, "get_user_role", lambda _user_id: bot.Role.OWNER)
    monkeypatch.setattr(bot.access_store, "can_use_callback", lambda *_args: True)
    monkeypatch.setattr(bot, "_callback_result_access_or_report", lambda *_args, **_kwargs: asyncio.sleep(0, result=True))
    published, waited = [], []
    monkeypatch.setattr(bot, "publish_tracked", lambda *args, **kwargs: published.append((args, kwargs)) or (True, "cycles-1"))

    async def wait_for(*args):
        waited.append(args)
        return {
            "type": "battery_cycle_snapshot", "schema": "xider.battery-cycles.v1", "feature_id": "D01-F08",
            "device_id": "device-1", "request_id": "cycles-1", "state": "supported",
            "source": "windows_wmi_battery_cycle_count", "checked_at": "2026-10-16T08:00:00+00:00",
            "data": {"batteries": [{"label": "Battery 1", "cycle_count": 321, "quality": "native_provider"}]},
        }

    monkeypatch.setattr(bot.battery_cycle_collector, "wait_for", wait_for)
    asyncio.run(bot.on_battery_cycles(telegram.callback("battery:cycles")))

    assert published == [(("battery_cycles",), {"_target": "device-1"})]
    assert waited == [("device-1", "battery_cycles", 15.0, "cycles-1")]
    assert list(telegram.cards) == [30] and not telegram.sent
    assert "321" in telegram.cards[30]["text"]


def test_battery_cycles_does_not_dispatch_to_an_agent_without_the_command(monkeypatch, tmp_path):
    telegram = _card_telegram(monkeypatch, tmp_path)
    _setup(monkeypatch, {"device-1": {"name": "Legacy", "os": "Windows 10", "capabilities": {
        "commands": ["battery_health"],
    }}})
    monkeypatch.setattr(bot, "SESSION", {"target": "device-1"})
    monkeypatch.setattr(bot, "get_user_role", lambda _user_id: bot.Role.OWNER)
    monkeypatch.setattr(bot.access_store, "can_use_callback", lambda *_args: True)
    published = []
    monkeypatch.setattr(bot, "publish_tracked", lambda *args, **kwargs: published.append((args, kwargs)) or (True, "must-not-run"))

    asyncio.run(bot.on_battery_cycles(telegram.callback("battery:cycles")))

    assert not published
    assert "команда не отправлялась" in telegram.cards[30]["text"]


def test_energy_usage_action_is_owner_only_registry_backed_and_edits_existing_card(monkeypatch, tmp_path):
    telegram = _card_telegram(monkeypatch, tmp_path)
    _setup(monkeypatch, {"device-1": {"name": "Laptop", "os": "Windows 11", "capabilities": {
        "commands": ["energy_usage"],
    }}})
    monkeypatch.setattr(bot, "ADMIN_ID", 20)
    monkeypatch.setattr(bot, "SESSION", {"target": "device-1"})
    monkeypatch.setattr(bot, "get_user_role", lambda _user_id: bot.Role.OWNER)
    monkeypatch.setattr(bot.access_store, "can_use_callback", lambda *_args: True)
    monkeypatch.setattr(bot, "_callback_result_access_or_report", lambda *_args, **_kwargs: asyncio.sleep(0, result=True))
    published, waited = [], []
    monkeypatch.setattr(bot, "publish_tracked", lambda *args, **kwargs: published.append((args, kwargs)) or (True, "energy-1"))

    async def wait_for(*args):
        waited.append(args)
        return {
            "type": "energy_usage_snapshot", "schema": "xider.energy-usage.v1", "feature_id": "D01-F09",
            "device_id": "device-1", "request_id": "energy-1", "state": "supported",
            "source": "windows_srum_energy_estimation", "checked_at": "2026-10-16T08:00:00+00:00",
            "data": {"unit": "mJ", "apps": [{"name": "browser", "energy_mj": 150, "quality": "estimated"}]},
        }

    monkeypatch.setattr(bot.energy_usage_collector, "wait_for", wait_for)
    asyncio.run(bot.on_energy_usage(telegram.callback("energy:usage")))

    assert published == [(("energy_usage",), {"_target": "device-1"})]
    assert waited == [("device-1", "energy_usage", 15.0, "energy-1")]
    assert list(telegram.cards) == [30] and not telegram.sent
    assert "browser" in telegram.cards[30]["text"] and "150 mJ" in telegram.cards[30]["text"]


def test_energy_usage_does_not_dispatch_to_an_agent_without_the_command(monkeypatch, tmp_path):
    telegram = _card_telegram(monkeypatch, tmp_path)
    _setup(monkeypatch, {"device-1": {"name": "Legacy", "os": "Windows 10", "capabilities": {
        "commands": ["battery_cycles"],
    }}})
    monkeypatch.setattr(bot, "SESSION", {"target": "device-1"})
    monkeypatch.setattr(bot, "get_user_role", lambda _user_id: bot.Role.OWNER)
    monkeypatch.setattr(bot.access_store, "can_use_callback", lambda *_args: True)
    published = []
    monkeypatch.setattr(bot, "publish_tracked", lambda *args, **kwargs: published.append((args, kwargs)) or (True, "must-not-run"))

    asyncio.run(bot.on_energy_usage(telegram.callback("energy:usage")))

    assert not published
    assert "energy_usage" in telegram.cards[30]["text"] and "не отправлялся" in telegram.cards[30]["text"]


def test_sleep_wake_history_is_owner_only_registry_backed_and_edits_existing_card(monkeypatch, tmp_path):
    telegram = _card_telegram(monkeypatch, tmp_path)
    _setup(monkeypatch, {"device-1": {"name": "Laptop", "os": "Windows 11", "capabilities": {
        "commands": ["sleep_wake_history"],
    }}})
    monkeypatch.setattr(bot, "ADMIN_ID", 20)
    monkeypatch.setattr(bot, "SESSION", {"target": "device-1"})
    monkeypatch.setattr(bot, "get_user_role", lambda _user_id: bot.Role.OWNER)
    monkeypatch.setattr(bot.access_store, "can_use_callback", lambda *_args: True)
    monkeypatch.setattr(bot, "_callback_result_access_or_report", lambda *_args, **_kwargs: asyncio.sleep(0, result=True))
    published, waited = [], []
    monkeypatch.setattr(bot, "publish_tracked", lambda *args, **kwargs: published.append((args, kwargs)) or (True, "sleep-1"))

    async def wait_for(*args):
        waited.append(args)
        return {
            "type": "sleep_wake_history_snapshot", "schema": "xider.sleep-wake-history.v1", "feature_id": "D01-F10",
            "device_id": "device-1", "request_id": "sleep-1", "state": "supported",
            "source": "windows_system_power_events", "checked_at": "2026-10-16T11:59:00+00:00",
            "data": {"history_days": 7, "events": [
                {"kind": "sleep", "timestamp": "2026-10-16T10:00:00+00:00"},
                {"kind": "wake", "timestamp": "2026-10-16T11:00:00+00:00"},
            ]},
        }

    monkeypatch.setattr(bot, "sleep_wake_history_collector", type("Collector", (), {"wait_for": staticmethod(wait_for)})())
    menu = bot.system_menu(user_id=20)
    buttons = {button.callback_data: button.text for row in menu.inline_keyboard for button in row}
    asyncio.run(bot.on_sleep_wake_history(telegram.callback("sleep:history")))

    assert buttons["sleep:history"] and buttons["sleep:history"] != "D01-F10"
    assert published == [(('sleep_wake_history',), {"_target": "device-1"})]
    assert waited == [("device-1", "sleep_wake_history", 15.0, "sleep-1")]
    assert list(telegram.cards) == [30] and not telegram.sent
    assert "сон" in telegram.cards[30]["text"].lower() and "пробуж" in telegram.cards[30]["text"].lower()


def test_sleep_wake_history_fails_fast_for_legacy_agent(monkeypatch, tmp_path):
    telegram = _card_telegram(monkeypatch, tmp_path)
    _setup(monkeypatch, {"device-1": {"name": "Legacy", "os": "Windows 10", "capabilities": {
        "commands": ["battery_cycles"],
    }}})
    monkeypatch.setattr(bot, "SESSION", {"target": "device-1"})
    monkeypatch.setattr(bot, "get_user_role", lambda _user_id: bot.Role.OWNER)
    monkeypatch.setattr(bot.access_store, "can_use_callback", lambda *_args: True)
    published = []
    monkeypatch.setattr(bot, "publish_tracked", lambda *args, **kwargs: published.append((args, kwargs)) or (True, "must-not-run"))

    asyncio.run(bot.on_sleep_wake_history(telegram.callback("sleep:history")))

    assert not published
    assert "sleep_wake_history" in telegram.cards[30]["text"] and "не отправлялся" in telegram.cards[30]["text"]


def test_startup_duration_action_is_owner_only_registry_backed_and_edits_existing_card(monkeypatch, tmp_path):
    telegram = _card_telegram(monkeypatch, tmp_path)
    _setup(monkeypatch, {"device-1": {"name": "Laptop", "os": "Windows 11", "capabilities": {
        "commands": ["startup_duration"],
    }}})
    monkeypatch.setattr(bot, "ADMIN_ID", 20)
    monkeypatch.setattr(bot, "SESSION", {"target": "device-1"})
    monkeypatch.setattr(bot, "get_user_role", lambda _user_id: bot.Role.OWNER)
    monkeypatch.setattr(bot.access_store, "can_use_callback", lambda *_args: True)
    monkeypatch.setattr(bot, "_callback_result_access_or_report", lambda *_args, **_kwargs: asyncio.sleep(0, result=True))
    published, waited = [], []
    monkeypatch.setattr(bot, "publish_tracked", lambda *args, **kwargs: published.append((args, kwargs)) or (True, "boot-1"))

    async def wait_for(*args):
        waited.append(args)
        return {
            "type": "startup_duration_snapshot", "schema": "xider.startup-duration.v1", "feature_id": "D01-F11",
            "device_id": "device-1", "request_id": "boot-1", "state": "supported",
            "source": "windows_diagnostics_performance_event_100", "checked_at": "2026-10-16T11:59:00+00:00",
            "data": {"duration_ms": 42_500, "completed_at": "2026-10-16T11:30:00+00:00"},
        }

    monkeypatch.setattr(bot, "startup_duration_collector", type("Collector", (), {"wait_for": staticmethod(wait_for)})())
    menu = bot.system_menu(user_id=20)
    buttons = {button.callback_data: button.text for row in menu.inline_keyboard for button in row}
    asyncio.run(bot.on_startup_duration(telegram.callback("boot:duration")))

    assert buttons["boot:duration"] and buttons["boot:duration"] != "D01-F11"
    assert published == [(('startup_duration',), {"_target": "device-1"})]
    assert waited == [("device-1", "startup_duration", 15.0, "boot-1")]
    assert list(telegram.cards) == [30] and not telegram.sent
    assert "загруз" in telegram.cards[30]["text"].lower()


def test_startup_duration_fails_fast_for_legacy_agent(monkeypatch, tmp_path):
    telegram = _card_telegram(monkeypatch, tmp_path)
    _setup(monkeypatch, {"device-1": {"name": "Legacy", "os": "Windows 10", "capabilities": {
        "commands": ["battery_cycles"],
    }}})
    monkeypatch.setattr(bot, "SESSION", {"target": "device-1"})
    monkeypatch.setattr(bot, "get_user_role", lambda _user_id: bot.Role.OWNER)
    monkeypatch.setattr(bot.access_store, "can_use_callback", lambda *_args: True)
    published = []
    monkeypatch.setattr(bot, "publish_tracked", lambda *args, **kwargs: published.append((args, kwargs)) or (True, "must-not-run"))

    asyncio.run(bot.on_startup_duration(telegram.callback("boot:duration")))

    assert not published
    assert "startup_duration" in telegram.cards[30]["text"]
    assert "не отправлялся" in telegram.cards[30]["text"]


def test_pending_restart_action_is_owner_only_registry_backed_and_edits_existing_card(monkeypatch, tmp_path):
    telegram = _card_telegram(monkeypatch, tmp_path)
    _setup(monkeypatch, {"device-1": {"name": "Laptop", "os": "Windows 11", "capabilities": {
        "commands": ["pending_restart"],
    }}})
    monkeypatch.setattr(bot, "ADMIN_ID", 20)
    monkeypatch.setattr(bot, "SESSION", {"target": "device-1"})
    monkeypatch.setattr(bot, "get_user_role", lambda _user_id: bot.Role.OWNER)
    monkeypatch.setattr(bot.access_store, "can_use_callback", lambda *_args: True)
    monkeypatch.setattr(bot, "_callback_result_access_or_report", lambda *_args, **_kwargs: asyncio.sleep(0, result=True))
    published, waited = [], []
    monkeypatch.setattr(bot, "publish_tracked", lambda *args, **kwargs: published.append((args, kwargs)) or (True, "restart-1"))

    async def wait_for(*args):
        waited.append(args)
        return {
            "type": "pending_restart_snapshot", "schema": "xider.pending-restart.v1", "feature_id": "D01-F12",
            "device_id": "device-1", "request_id": "restart-1", "state": "supported",
            "source": "windows_pending_restart_registry", "checked_at": "2026-10-16T11:59:00+00:00",
            "data": {"pending": False, "reasons": [], "completeness": "complete"},
        }

    monkeypatch.setattr(bot, "pending_restart_collector", type("Collector", (), {"wait_for": staticmethod(wait_for)})())
    menu = bot.system_menu(user_id=20)
    buttons = {button.callback_data: button.text for row in menu.inline_keyboard for button in row}
    asyncio.run(bot.on_pending_restart(telegram.callback("reboot:status")))

    assert buttons["reboot:status"] and buttons["reboot:status"] != "D01-F12"
    assert published == [(("pending_restart",), {"_target": "device-1"})]
    assert waited == [("device-1", "pending_restart", 15.0, "restart-1")]
    assert list(telegram.cards) == [30] and not telegram.sent
    assert "перезагруз" in telegram.cards[30]["text"].lower()


def test_os_support_action_is_owner_only_registry_backed_and_edits_existing_card(monkeypatch, tmp_path):
    import datetime

    telegram = _card_telegram(monkeypatch, tmp_path)
    _setup(monkeypatch, {"device-1": {"name": "Laptop", "os": "Windows 11", "capabilities": {
        "commands": ["os_support"],
    }}})
    monkeypatch.setattr(bot, "ADMIN_ID", 20)
    monkeypatch.setattr(bot, "SESSION", {"target": "device-1"})
    monkeypatch.setattr(bot, "get_user_role", lambda _user_id: bot.Role.OWNER)
    monkeypatch.setattr(bot.access_store, "can_use_callback", lambda *_args: True)
    monkeypatch.setattr(bot, "_callback_result_access_or_report", lambda *_args, **_kwargs: asyncio.sleep(0, result=True))
    published, waited = [], []
    monkeypatch.setattr(bot, "publish_tracked", lambda *args, **kwargs: published.append((args, kwargs)) or (True, "os-1"))

    async def wait_for(*args):
        waited.append(args)
        return {
            "type": "os_support_snapshot", "schema": "xider.os-support.v1", "feature_id": "D01-F13",
            "device_id": "device-1", "request_id": "os-1", "state": "supported",
            "source": "windows_current_version_registry",
            "checked_at": datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat(),
            "data": {"family": "windows", "release": "11|26H2", "edition": "Pro"},
        }

    monkeypatch.setattr(bot, "os_support_collector", type("Collector", (), {"wait_for": staticmethod(wait_for)})())
    menu = bot.system_menu(user_id=20)
    buttons = {button.callback_data: button.text for row in menu.inline_keyboard for button in row}
    asyncio.run(bot.on_os_support(telegram.callback("os:support")))

    assert buttons["os:support"] and buttons["os:support"] != "D01-F13"
    assert published == [(("os_support",), {"_target": "device-1"})]
    assert waited == [("device-1", "os_support", 15.0, "os-1")]
    assert list(telegram.cards) == [30] and not telegram.sent
    assert "2028-10-09" in telegram.cards[30]["text"]


def test_hardware_inventory_history_is_correlated_owner_only_and_redacted(monkeypatch, tmp_path):
    import datetime

    telegram = _card_telegram(monkeypatch, tmp_path)
    _setup(monkeypatch, {"device-1": {"name": "Laptop", "os": "Windows 11", "capabilities": {
        "commands": ["hardware_inventory"],
    }}})
    monkeypatch.setattr(bot, "ADMIN_ID", 20)
    monkeypatch.setattr(bot, "SESSION", {"target": "device-1"})
    monkeypatch.setattr(bot, "get_user_role", lambda _user_id: bot.Role.OWNER)
    monkeypatch.setattr(bot.access_store, "can_use_callback", lambda *_args: True)
    monkeypatch.setattr(bot, "_callback_result_access_or_report", lambda *_args, **_kwargs: asyncio.sleep(0, result=True))
    published, waited, saved = [], [], []
    monkeypatch.setattr(bot, "publish_tracked", lambda *args, **kwargs: published.append((args, kwargs)) or (True, "inventory-1"))
    monkeypatch.setattr(bot.devices, "save_hardware_inventory_snapshot", lambda device, data: saved.append((device, data)), raising=False)
    monkeypatch.setattr(bot.devices, "compare_hardware_inventory_snapshots", lambda _device: {
        "before": {"captured_at": 1}, "after": {"captured_at": 2},
        "changes": [{"field": "graphics", "before": ["Old GPU"], "after": ["New GPU"]}],
    }, raising=False)

    async def wait_for(*args):
        waited.append(args)
        return {
            "type": "hardware_inventory_snapshot", "schema": "xider.hardware-inventory.v1",
            "feature_id": "D01-F14", "device_id": "device-1", "request_id": "inventory-1",
            "source": "windows_cim_hardware_inventory", "state": "supported",
            "checked_at": datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat(),
            "data": {"manufacturer": "Example", "model": "Board A", "cpu": "CPU A", "architecture": "x64",
                     "memory_gib": 16, "graphics": ["New GPU"], "serial_number": "MUST_NOT_RENDER"},
        }

    monkeypatch.setattr(bot, "hardware_inventory_collector", type("Collector", (), {"wait_for": staticmethod(wait_for)})())
    menu = bot.system_menu(user_id=20)
    buttons = {button.callback_data: button.text for row in menu.inline_keyboard for button in row}
    asyncio.run(bot.on_hardware_inventory_history(telegram.callback("hardware:history")))

    assert buttons["hardware:history"] and buttons["hardware:history"] != "D01-F14"
    assert published == [(("hardware_inventory",), {"_target": "device-1"})]
    assert waited == [("device-1", "hardware_inventory", 15.0, "inventory-1")]
    assert saved and saved[0][0] == "device-1" and "serial_number" not in saved[0][1]
    assert list(telegram.cards) == [30] and not telegram.sent
    assert "Old GPU" in telegram.cards[30]["text"] and "New GPU" in telegram.cards[30]["text"]
    assert "MUST_NOT_RENDER" not in telegram.cards[30]["text"]


def test_driver_inventory_history_is_correlated_owner_only_and_redacted(monkeypatch, tmp_path):
    import datetime

    telegram = _card_telegram(monkeypatch, tmp_path)
    _setup(monkeypatch, {"device-1": {"name": "Laptop", "os": "Windows 11", "capabilities": {
        "commands": ["driver_inventory"],
    }}})
    monkeypatch.setattr(bot, "ADMIN_ID", 20)
    monkeypatch.setattr(bot, "SESSION", {"target": "device-1"})
    monkeypatch.setattr(bot, "get_user_role", lambda _user_id: bot.Role.OWNER)
    monkeypatch.setattr(bot.access_store, "can_use_callback", lambda *_args: True)
    monkeypatch.setattr(bot, "_callback_result_access_or_report", lambda *_args, **_kwargs: asyncio.sleep(0, result=True))
    published, waited, saved = [], [], []
    monkeypatch.setattr(bot, "publish_tracked", lambda *args, **kwargs: published.append((args, kwargs)) or (True, "drivers-1"))
    monkeypatch.setattr(bot.devices, "save_driver_inventory_snapshot", lambda device, rows: saved.append((device, rows)), raising=False)
    monkeypatch.setattr(bot.devices, "compare_driver_inventory_snapshots", lambda _device: {
        "before": {"captured_at": 1}, "after": {"captured_at": 2},
        "changes": [{"kind": "changed", "device_name": "Display Adapter", "provider": "Vendor A",
                     "before": {"version": "1.0"}, "after": {"version": "1.2"}}],
    }, raising=False)

    async def wait_for(*args):
        waited.append(args)
        return {
            "type": "driver_inventory_snapshot", "schema": "xider.driver-inventory.v1", "feature_id": "D01-F15",
            "device_id": "device-1", "request_id": "drivers-1", "source": "windows_cim_signed_drivers",
            "state": "supported",
            "checked_at": datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat(),
            "data": {"drivers": [{"device_name": "Display Adapter", "provider": "Vendor A", "version": "1.2",
                                   "driver_date": "2026-04-01", "hardware_id": "MUST_NOT_RENDER"}]},
        }

    monkeypatch.setattr(bot, "driver_inventory_collector", type("Collector", (), {"wait_for": staticmethod(wait_for)})())
    menu = bot.system_menu(user_id=20)
    buttons = {button.callback_data: button.text for row in menu.inline_keyboard for button in row}
    asyncio.run(bot.on_driver_inventory_history(telegram.callback("drivers:history")))

    assert buttons["drivers:history"] and buttons["drivers:history"] != "D01-F15"
    assert published == [(("driver_inventory",), {"_target": "device-1"})]
    assert waited == [("device-1", "driver_inventory", 15.0, "drivers-1")]
    assert saved and saved[0][0] == "device-1" and "hardware_id" not in saved[0][1][0]
    assert list(telegram.cards) == [30] and not telegram.sent
    assert "1.0" in telegram.cards[30]["text"] and "1.2" in telegram.cards[30]["text"]
    assert "MUST_NOT_RENDER" not in telegram.cards[30]["text"]


def test_installed_app_history_is_correlated_owner_only_and_redacted(monkeypatch, tmp_path):
    import datetime

    telegram = _card_telegram(monkeypatch, tmp_path)
    _setup(monkeypatch, {"device-1": {"name": "Laptop", "os": "Windows 11", "capabilities": {
        "commands": ["installed_app_inventory"],
    }}})
    monkeypatch.setattr(bot, "ADMIN_ID", 20)
    monkeypatch.setattr(bot, "SESSION", {"target": "device-1"})
    monkeypatch.setattr(bot, "get_user_role", lambda _user_id: bot.Role.OWNER)
    monkeypatch.setattr(bot.access_store, "can_use_callback", lambda *_args: True)
    monkeypatch.setattr(bot, "_callback_result_access_or_report", lambda *_args, **_kwargs: asyncio.sleep(0, result=True))
    published, waited, saved = [], [], []
    monkeypatch.setattr(bot, "publish_tracked", lambda *args, **kwargs: published.append((args, kwargs)) or (True, "apps-1"))
    monkeypatch.setattr(bot.devices, "save_installed_app_snapshot", lambda device, rows: saved.append((device, rows)), raising=False)
    monkeypatch.setattr(bot.devices, "compare_installed_app_snapshots", lambda _device: {
        "changes": [{"kind": "changed", "name": "Example App", "publisher": "Vendor",
                     "before": "1.0", "after": "1.1"}],
    }, raising=False)

    async def wait_for(*args):
        waited.append(args)
        return {
            "type": "installed_app_inventory_snapshot", "schema": "xider.installed-app-inventory.v1",
            "feature_id": "D01-F16", "device_id": "device-1", "request_id": "apps-1",
            "source": "windows_uninstall_registry_projection", "state": "supported",
            "checked_at": datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat(),
            "data": {"apps": [{"name": "Example App", "publisher": "Vendor", "version": "1.1",
                               "install_path": "MUST_NOT_RENDER"}]},
        }

    monkeypatch.setattr(bot, "installed_app_inventory_collector", type("Collector", (), {"wait_for": staticmethod(wait_for)})())
    menu = bot.system_menu(user_id=20)
    buttons = {button.callback_data: button.text for row in menu.inline_keyboard for button in row}
    asyncio.run(bot.on_installed_app_history(telegram.callback("apps:history")))

    assert buttons["apps:history"] and buttons["apps:history"] != "D01-F16"
    assert published == [(("installed_app_inventory",), {"_target": "device-1"})]
    assert waited == [("device-1", "installed_app_inventory", 15.0, "apps-1")]
    assert saved and saved[0][0] == "device-1" and "install_path" not in saved[0][1][0]
    assert list(telegram.cards) == [30] and not telegram.sent
    assert "1.0" in telegram.cards[30]["text"] and "1.1" in telegram.cards[30]["text"]
    assert "MUST_NOT_RENDER" not in telegram.cards[30]["text"]


def test_startup_inventory_history_is_correlated_owner_only_and_redacted(monkeypatch, tmp_path):
    import datetime

    telegram = _card_telegram(monkeypatch, tmp_path)
    _setup(monkeypatch, {"device-1": {"name": "Laptop", "os": "Windows 11", "capabilities": {
        "commands": ["startup_inventory"],
    }}})
    monkeypatch.setattr(bot, "ADMIN_ID", 20)
    monkeypatch.setattr(bot, "SESSION", {"target": "device-1"})
    monkeypatch.setattr(bot, "get_user_role", lambda _user_id: bot.Role.OWNER)
    monkeypatch.setattr(bot.access_store, "can_use_callback", lambda *_args: True)
    monkeypatch.setattr(bot, "_callback_result_access_or_report", lambda *_args, **_kwargs: asyncio.sleep(0, result=True))
    published, waited, saved = [], [], []
    monkeypatch.setattr(bot, "publish_tracked", lambda *args, **kwargs: published.append((args, kwargs)) or (True, "startup-1"))
    monkeypatch.setattr(bot.devices, "save_startup_snapshot", lambda device, entries: saved.append((device, entries)), raising=False)
    monkeypatch.setattr(bot.devices, "compare_startup_snapshots", lambda _device: {
        "changes": [{"kind": "added", "name": "Updater", "scope": "user_run"}],
    }, raising=False)

    async def wait_for(*args):
        waited.append(args)
        return {
            "type": "startup_inventory_snapshot", "schema": "xider.startup-inventory.v1", "feature_id": "D01-F17",
            "device_id": "device-1", "request_id": "startup-1", "source": "windows_run_keys_and_startup_folder_names",
            "state": "supported",
            "checked_at": datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat(),
            "data": {"entries": [{"name": "Updater", "scope": "user_run", "command": "MUST_NOT_RENDER"}]},
        }

    monkeypatch.setattr(bot, "startup_inventory_collector", type("Collector", (), {"wait_for": staticmethod(wait_for)})())
    menu = bot.system_menu(user_id=20)
    buttons = {button.callback_data: button.text for row in menu.inline_keyboard for button in row}
    asyncio.run(bot.on_startup_inventory_history(telegram.callback("startup:history")))

    assert buttons["startup:history"] and buttons["startup:history"] != "D01-F17"
    assert published == [(("startup_inventory",), {"_target": "device-1"})]
    assert waited == [("device-1", "startup_inventory", 15.0, "startup-1")]
    assert saved and saved[0][0] == "device-1" and "command" not in saved[0][1][0]
    assert list(telegram.cards) == [30] and not telegram.sent
    assert "Updater" in telegram.cards[30]["text"]
    assert "MUST_NOT_RENDER" not in telegram.cards[30]["text"]


def test_service_inventory_history_is_correlated_owner_only_and_redacted(monkeypatch, tmp_path):
    import datetime

    telegram = _card_telegram(monkeypatch, tmp_path)
    _setup(monkeypatch, {"device-1": {"name": "Laptop", "os": "Windows 11", "capabilities": {
        "commands": ["service_inventory"],
    }}})
    monkeypatch.setattr(bot, "ADMIN_ID", 20)
    monkeypatch.setattr(bot, "SESSION", {"target": "device-1"})
    monkeypatch.setattr(bot, "get_user_role", lambda _user_id: bot.Role.OWNER)
    monkeypatch.setattr(bot.access_store, "can_use_callback", lambda *_args: True)
    monkeypatch.setattr(bot, "_callback_result_access_or_report", lambda *_args, **_kwargs: asyncio.sleep(0, result=True))
    published, waited, saved = [], [], []
    monkeypatch.setattr(bot, "publish_tracked", lambda *args, **kwargs: published.append((args, kwargs)) or (True, "services-1"))
    monkeypatch.setattr(bot.devices, "save_service_snapshot", lambda device, services: saved.append((device, services)), raising=False)
    monkeypatch.setattr(bot.devices, "compare_service_snapshots", lambda _device: {
        "changes": [{"kind": "changed", "name": "ExampleSvc", "before": {"state": "Stopped", "start_type": "Manual"},
                     "after": {"state": "Running", "start_type": "Auto"}}],
    }, raising=False)

    async def wait_for(*args):
        waited.append(args)
        return {
            "type": "service_inventory_snapshot", "schema": "xider.service-inventory.v1", "feature_id": "D01-F18",
            "device_id": "device-1", "request_id": "services-1", "source": "windows_cim_service_status",
            "state": "supported",
            "checked_at": datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat(),
            "data": {"services": [{"name": "ExampleSvc", "state": "Running", "start_type": "Auto",
                                   "binary_path": "MUST_NOT_RENDER"}]},
        }

    monkeypatch.setattr(bot, "service_inventory_collector", type("Collector", (), {"wait_for": staticmethod(wait_for)})())
    menu = bot.system_menu(user_id=20)
    buttons = {button.callback_data: button.text for row in menu.inline_keyboard for button in row}
    asyncio.run(bot.on_service_inventory_history(telegram.callback("services:history")))

    assert buttons["services:history"] and buttons["services:history"] != "D01-F18"
    assert published == [(("service_inventory",), {"_target": "device-1"})]
    assert waited == [("device-1", "service_inventory", 15.0, "services-1")]
    assert saved and saved[0][0] == "device-1" and "binary_path" not in saved[0][1][0]
    assert list(telegram.cards) == [30] and not telegram.sent
    assert "ExampleSvc" in telegram.cards[30]["text"] and "MUST_NOT_RENDER" not in telegram.cards[30]["text"]


def test_crash_summary_is_correlated_owner_only_and_redacted(monkeypatch, tmp_path):
    import datetime

    telegram = _card_telegram(monkeypatch, tmp_path)
    _setup(monkeypatch, {"device-1": {"name": "Laptop", "os": "Windows 11", "capabilities": {
        "commands": ["crash_summary"],
    }}})
    monkeypatch.setattr(bot, "ADMIN_ID", 20)
    monkeypatch.setattr(bot, "SESSION", {"target": "device-1"})
    monkeypatch.setattr(bot, "get_user_role", lambda _user_id: bot.Role.OWNER)
    monkeypatch.setattr(bot.access_store, "can_use_callback", lambda *_args: True)
    monkeypatch.setattr(bot, "_callback_result_access_or_report", lambda *_args, **_kwargs: asyncio.sleep(0, result=True))
    published, waited = [], []
    monkeypatch.setattr(bot, "publish_tracked", lambda *args, **kwargs: published.append((args, kwargs)) or (True, "crash-1"))

    async def wait_for(*args):
        waited.append(args)
        return {
            "type": "crash_summary_snapshot", "schema": "xider.crash-summary.v1", "feature_id": "D01-F19",
            "device_id": "device-1", "request_id": "crash-1", "source": "windows_event_log_provider_event_aggregate",
            "state": "supported",
            "checked_at": datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat(),
            "data": {"events": [{"provider": "Application Error", "event_id": 1000, "level": 2, "count": 3,
                                  "message": "MUST_NOT_RENDER"}]},
        }

    monkeypatch.setattr(bot, "crash_summary_collector", type("Collector", (), {"wait_for": staticmethod(wait_for)})())
    menu = bot.system_menu(user_id=20)
    buttons = {button.callback_data: button.text for row in menu.inline_keyboard for button in row}
    asyncio.run(bot.on_crash_summary(telegram.callback("crash:summary")))

    assert buttons["crash:summary"] and buttons["crash:summary"] != "D01-F19"
    assert published == [(("crash_summary",), {"_target": "device-1"})]
    assert waited == [("device-1", "crash_summary", 15.0, "crash-1")]
    assert list(telegram.cards) == [30] and not telegram.sent
    assert "событие 1000" in telegram.cards[30]["text"] and "MUST_NOT_RENDER" not in telegram.cards[30]["text"]


def test_smart_trend_is_owner_only_correlated_and_persists_two_device_snapshots(monkeypatch, tmp_path):
    import datetime
    from device_store import DeviceStore
    checked_at = datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0)

    telegram = _card_telegram(monkeypatch, tmp_path)
    _setup(monkeypatch, {"device-1": {"name": "Laptop", "os": "Windows", "capabilities": {
        "commands": ["smart_trend"],
    }}})
    monkeypatch.setattr(DeviceStore, "FILE", tmp_path / "known.json", raising=False)
    monkeypatch.setattr(DeviceStore, "CATALOG_FILE", tmp_path / "catalog.json", raising=False)
    monkeypatch.setattr(bot, "devices", DeviceStore())
    bot.devices.upsert("device-1", {"name": "Laptop", "os": "Windows", "capabilities": {
        "commands": ["smart_trend"],
    }})
    monkeypatch.setattr(bot, "ADMIN_ID", 20)
    monkeypatch.setattr(bot, "SESSION", {"target": "device-1"})
    monkeypatch.setattr(bot, "get_user_role", lambda _user_id: bot.Role.OWNER)
    monkeypatch.setattr(bot.access_store, "can_use_callback", lambda *_args: True)
    monkeypatch.setattr(bot, "_callback_result_access_or_report", lambda *_args, **_kwargs: asyncio.sleep(0, result=True))
    published, waited = [], []
    monkeypatch.setattr(bot, "publish_tracked", lambda *args, **kwargs: published.append((args, kwargs)) or (True, "smart-1"))
    responses = [
        {"type": "smart_trend_snapshot", "schema": "xider.smart-trend.v1", "feature_id": "D01-F20",
         "device_id": "device-1", "request_id": "smart-1", "source": "windows_storage_reliability_counters",
         "state": "supported", "checked_at": (checked_at - datetime.timedelta(seconds=3)).isoformat(),
         "data": {"disks": [{"label": "Model A [SSD]", "counters": {"wear_percent": 7}}]}},
        {"type": "smart_trend_snapshot", "schema": "xider.smart-trend.v1", "feature_id": "D01-F20",
         "device_id": "device-1", "request_id": "smart-1", "source": "windows_storage_reliability_counters",
         "state": "supported", "checked_at": checked_at.isoformat(),
         "data": {"disks": [{"label": "Model A [SSD]", "counters": {"wear_percent": 9}}]}},
    ]
    async def wait_for(*args):
        waited.append(args)
        return responses.pop(0)
    monkeypatch.setattr(bot, "smart_trend_collector", type("Collector", (), {"wait_for": staticmethod(wait_for)})())

    menu = bot.system_menu(user_id=20)
    buttons = {button.callback_data: button.text for row in menu.inline_keyboard for button in row}
    asyncio.run(bot.on_smart_trend(telegram.callback("smart:trend")))
    monkeypatch.setattr(bot, "publish_tracked", lambda *args, **kwargs: published.append((args, kwargs)) or (True, "smart-2"))
    responses[0]["request_id"] = "smart-2"
    asyncio.run(bot.on_smart_trend(telegram.callback("smart:trend")))

    assert buttons["smart:trend"] and buttons["smart:trend"] != "D01-F20"
    assert _expected_callback_gate("smart:trend") == "OwnerFilter"
    assert [row[0] for row in published] == [("smart_trend",), ("smart_trend",)]
    assert [row[-1] for row in waited] == ["smart-1", "smart-2"]
    assert len(bot.devices.list_smart_trend_snapshots("device-1")) == 2
    assert "+2" in telegram.cards[30]["text"]
    assert not telegram.sent


def test_volume_topology_is_owner_only_correlated_and_updates_one_card(monkeypatch, tmp_path):
    import datetime
    telegram = _card_telegram(monkeypatch, tmp_path)
    _setup(monkeypatch, {"device-1": {"name": "Laptop", "os": "Windows 11", "capabilities": {
        "commands": ["volume_topology"],
    }}})
    monkeypatch.setattr(bot, "ADMIN_ID", 20)
    monkeypatch.setattr(bot, "SESSION", {"target": "device-1"})
    monkeypatch.setattr(bot, "get_user_role", lambda _user_id: bot.Role.OWNER)
    monkeypatch.setattr(bot.access_store, "can_use_callback", lambda *_args: True)
    monkeypatch.setattr(bot, "_callback_result_access_or_report", lambda *_args, **_kwargs: asyncio.sleep(0, result=True))
    published, waited = [], []
    monkeypatch.setattr(bot, "publish_tracked", lambda *args, **kwargs: published.append((args, kwargs)) or (True, "topology-1"))

    async def wait_for(*args):
        waited.append(args)
        return {
            "type": "volume_topology_snapshot", "schema": "xider.volume-topology.v1", "feature_id": "D01-F21",
            "device_id": "device-1", "request_id": "topology-1", "source": "windows_get_volume_partition_access_paths",
            "state": "supported", "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "data": {"volumes": [{"volume_key": "vol-0123456789abcdef",
                                    "mount_paths": ["C:\\\\", "C:\\\\Mounts\\\\Data"], "state": "supported"}]},
        }

    monkeypatch.setattr(bot, "volume_topology_collector", type("Collector", (), {"wait_for": staticmethod(wait_for)})())
    menu = bot.system_menu(user_id=20)
    buttons = {button.callback_data: button.text for row in menu.inline_keyboard for button in row}
    asyncio.run(bot.on_volume_topology(telegram.callback("volume:topology")))

    assert "volume:topology" in buttons and buttons["volume:topology"] != "D01-F21"
    assert _expected_callback_gate("volume:topology") == "OwnerFilter"
    assert published == [(("volume_topology",), {"_target": "device-1"})]
    assert waited == [("device-1", "volume_topology", 15.0, "topology-1")]
    assert list(telegram.cards) == [30] and not telegram.sent
    assert "C:\\\\Mounts\\\\Data" in telegram.cards[30]["text"]


def test_clock_drift_is_owner_only_correlated_and_explains_local_estimate(monkeypatch, tmp_path):
    import datetime
    telegram = _card_telegram(monkeypatch, tmp_path)
    _setup(monkeypatch, {"device-1": {"name": "Laptop", "os": "Windows 11", "capabilities": {
        "commands": ["clock_drift"],
    }}})
    monkeypatch.setattr(bot, "ADMIN_ID", 20)
    monkeypatch.setattr(bot, "SESSION", {"target": "device-1"})
    monkeypatch.setattr(bot, "get_user_role", lambda _user_id: bot.Role.OWNER)
    monkeypatch.setattr(bot.access_store, "can_use_callback", lambda *_args: True)
    monkeypatch.setattr(bot, "_callback_result_access_or_report", lambda *_args, **_kwargs: asyncio.sleep(0, result=True))
    published, waited = [], []
    monkeypatch.setattr(bot, "publish_tracked", lambda *args, **kwargs: published.append((args, kwargs)) or (True, "clock-1"))

    async def wait_for(*args):
        waited.append(args)
        return {
            "type": "clock_drift_snapshot", "schema": "xider.clock-drift.v1", "feature_id": "D01-F22",
            "device_id": "device-1", "request_id": "clock-1", "source": "wall_vs_monotonic_elapsed",
            "state": "supported", "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "data": {"drift_ms": -123, "sample_ms": 2000},
        }

    monkeypatch.setattr(bot, "clock_drift_collector", type("Collector", (), {"wait_for": staticmethod(wait_for)})())
    menu = bot.system_menu(user_id=20)
    buttons = {button.callback_data: button.text for row in menu.inline_keyboard for button in row}
    asyncio.run(bot.on_clock_drift(telegram.callback("clock:drift")))

    assert "clock:drift" in buttons and buttons["clock:drift"] != "D01-F22"
    assert _expected_callback_gate("clock:drift") == "OwnerFilter"
    assert published == [(("clock_drift",), {"_target": "device-1"})]
    assert waited == [("device-1", "clock_drift", 15.0, "clock-1")]
    assert list(telegram.cards) == [30] and not telegram.sent
    assert "−123 мс" in telegram.cards[30]["text"] or "-123 мс" in telegram.cards[30]["text"]
    assert "монотон" in telegram.cards[30]["text"].lower()


def test_display_resolution_is_owner_only_correlated_and_queries_only_the_selected_monitor(monkeypatch):
    import datetime
    from types import SimpleNamespace
    _setup(monkeypatch, {"device-1": {"name": "Laptop", "os": "Windows 11", "capabilities": {
        "commands": ["display_catalog", "display_resolution"],
    }}})
    monkeypatch.setattr(bot, "ADMIN_ID", 20)
    monkeypatch.setattr(bot, "SESSION", {"target": "device-1"})
    monkeypatch.setattr(bot, "get_user_role", lambda _user_id: bot.Role.OWNER)
    monkeypatch.setattr(bot.access_store, "can_use_callback", lambda *_args: True)
    monkeypatch.setattr(bot, "_callback_result_access_or_report", lambda *_args, **_kwargs: asyncio.sleep(0, result=True))
    monkeypatch.setattr(bot, "_pending_device_input_still_allowed", lambda *_args: True)
    monkeypatch.setattr(bot, "_simple_command_still_allowed", lambda *_args: True)
    published, waited, replaced = [], [], []
    def publish(command, **kwargs):
        published.append((command, kwargs))
        return True, "catalog-1" if command == "display_catalog" else "resolution-1"
    monkeypatch.setattr(bot, "publish_tracked", publish)
    async def replace(cq, body, **kwargs): replaced.append((cq, body, kwargs))
    monkeypatch.setattr(bot, "_replace_callback_message", replace)

    class State:
        def __init__(self): self.data = {}
        async def set_state(self, value): self.state = value
        async def update_data(self, **values): self.data.update(values)
        async def get_data(self): return dict(self.data)
        async def get_state(self): return bot.Form.wait_display_selection.state
        async def clear(self): self.data = {}; self.state = None

    async def catalog_wait(*args):
        waited.append(args)
        return {"type": "display_catalog", "schema": "xider.display-catalog.v1", "feature_id": "D02-F06",
                "device_id": "device-1", "id": "catalog-1", "request_id": "catalog-1", "state": "success",
                "source": "windows_enum_display_monitors", "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                "data": {"topology_id": "a" * 16, "displays": [
                    {"id": "display-1", "width": 1280, "height": 720, "primary": True},
                    {"id": "display-2", "width": 1920, "height": 1080, "primary": False},
                ]}}
    async def resolution_wait(*args):
        waited.append(args)
        return {"type": "display_resolution", "schema": "xider.display-resolution.v1", "feature_id": "D02-F10",
                "device_id": "device-1", "id": "resolution-1", "request_id": "resolution-1",
                "display_id": "display-2", "source": "windows_gdi_monitor_caps", "state": "success",
                "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                "data": {"source": "windows_gdi_monitor_caps", "topology_id": "a" * 16, "displays": [
                    {"id": "display-2", "logical_width": 1280, "logical_height": 720,
                     "backing_width": 2560, "backing_height": 1440, "scale_x": 2.0, "scale_y": 2.0, "primary": False}]}}
    monkeypatch.setattr(bot, "display_catalog_collector", SimpleNamespace(wait_for=catalog_wait))
    monkeypatch.setattr(bot, "display_resolution_collector", SimpleNamespace(wait_for=resolution_wait))
    async def answer(*_args, **_kwargs): pass
    cq = SimpleNamespace(from_user=SimpleNamespace(id=20), answer=answer,
                         data="display:resolution", message=SimpleNamespace(chat=SimpleNamespace(id=77), message_id=30))
    menu = bot.screen_menu(user_id=20)
    buttons = {button.callback_data: button.text for row in menu.inline_keyboard for button in row}
    state = State()
    asyncio.run(bot.on_display_catalog(cq, state))
    asyncio.run(state.update_data(display_selected=["display-2"]))
    # The callback builds a one-screen-only action after an explicit owner selection.
    resolution_markup = bot._display_selection_keyboard(state.data["display_catalog"]["displays"], ["display-2"], mode="resolution")
    resolution_callbacks = [button.callback_data for row in resolution_markup.inline_keyboard for button in row]
    assert "display:resolution" in buttons and buttons["display:resolution"] != "D02-F10"
    assert "display:resolution:check" in resolution_callbacks
    assert _expected_callback_gate("display:resolution") == "OwnerFilter"
    assert _expected_callback_gate("display:resolution:check") == "OwnerFilter"
    asyncio.run(bot.on_display_resolution_check(cq, state))

    assert published == [
        ("display_catalog", {"_target": "device-1"}),
        ("display_resolution", {"_target": "device-1", "display_id": "display-2", "topology_id": "a" * 16}),
    ]
    assert waited == [
        ("device-1", "display_catalog", 15.0, "catalog-1"),
        ("device-1", "display_resolution", 15.0, "resolution-1"),
    ]
    assert len(replaced) == 3
    assert "2560×1440" in replaced[-1][1] and "2×" in replaced[-1][1]
    assert "display-1" not in replaced[-1][1]


def test_display_resolution_validator_rejects_forged_scale_and_stale_or_mismatched_sources():
    import datetime
    now = datetime.datetime.now(datetime.timezone.utc)
    base = {
        "type": "display_resolution", "schema": "xider.display-resolution.v1", "feature_id": "D02-F10",
        "device_id": "device-1", "id": "r1", "request_id": "r1", "source": "windows_gdi_monitor_caps",
        "display_id": "display-1",
        "state": "success", "checked_at": now.isoformat(),
        "data": {"source": "windows_gdi_monitor_caps", "topology_id": "a" * 16, "displays": [
            {"id": "display-1", "logical_width": 1280, "logical_height": 720,
             "backing_width": 2560, "backing_height": 1440, "scale_x": 2.0, "scale_y": 2.0, "primary": True},
        ]},
    }
    args = dict(expected_device_id="device-1", expected_request_id="r1", expected_display_id="display-1", expected_source="windows_gdi_monitor_caps", now=now)
    assert bot._validate_display_resolution_result(base, **args)[0]["scale_x"] == 2.0
    for invalid in (
        {**base, "source": "macos_coregraphics_pixels"},
        {**base, "checked_at": (now - datetime.timedelta(seconds=121)).isoformat()},
        {**base, "data": {**base["data"], "displays": [{**base["data"]["displays"][0], "scale_x": 1.0}]}},
    ):
        try:
            bot._validate_display_resolution_result(invalid, **args)
        except ValueError:
            pass
        else:
            raise AssertionError("stale, cross-platform, or inconsistent measurement must be rejected")


def test_system_snapshot_history_is_owner_only_correlated_and_edits_one_card(monkeypatch, tmp_path):
    import datetime
    from device_store import DeviceStore
    telegram = _card_telegram(monkeypatch, tmp_path)
    _setup(monkeypatch, {"device-1": {"name": "Laptop", "os": "Windows 11", "capabilities": {
        "commands": ["sysinfo"],
    }}})
    monkeypatch.setattr(DeviceStore, "FILE", tmp_path / "known.json", raising=False)
    monkeypatch.setattr(DeviceStore, "CATALOG_FILE", tmp_path / "catalog.json", raising=False)
    monkeypatch.setattr(bot, "devices", DeviceStore())
    bot.devices.upsert("device-1", {"name": "Laptop", "os": "Windows 11", "capabilities": {"commands": ["sysinfo"]}})
    monkeypatch.setattr(bot, "ADMIN_ID", 20)
    monkeypatch.setattr(bot, "SESSION", {"target": "device-1"})
    monkeypatch.setattr(bot, "get_user_role", lambda _user_id: bot.Role.OWNER)
    monkeypatch.setattr(bot.access_store, "can_use_callback", lambda *_args: True)
    monkeypatch.setattr(bot, "_callback_result_access_or_report", lambda *_args, **_kwargs: asyncio.sleep(0, result=True))
    published, waited = [], []
    monkeypatch.setattr(bot, "publish_tracked", lambda *args, **kwargs: published.append((args, kwargs)) or (True, "abcdef1234567890"))

    async def wait_for(*args):
        waited.append(args)
        checked_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
        return {
            "type": "sysinfo", "device_id": "device-1", "os": "Windows 11",
            "hostname": "MUST_NOT_PERSIST", "mac": "MUST_NOT_PERSIST",
            "cpu_percent": 12.5, "ram_percent": 48.0, "disk_percent": 61.0,
            "regional_settings": {
                "schema": 1, "feature_id": "D01-F23", "device_id": "device-1",
                "request_id": "abcdef1234567890", "source": "local_os_runtime_apis",
                "state": "supported", "checked_at": checked_at,
                "data": {"timezone": {"state": "supported"}, "locale": {"state": "supported"}},
            },
        }

    monkeypatch.setattr(bot, "sysinfo_collector", type("Collector", (), {"wait_for": staticmethod(wait_for)})())
    menu = bot.system_menu(user_id=20)
    buttons = {button.callback_data: button.text for row in menu.inline_keyboard for button in row}
    asyncio.run(bot.on_system_snapshot_history(telegram.callback("sysinfo:history")))

    persisted = (tmp_path / "catalog.json").read_text(encoding="utf-8")
    assert "sysinfo:history" in buttons and buttons["sysinfo:history"] != "D01-F24"
    assert _expected_callback_gate("sysinfo:history") == "OwnerFilter"
    assert published == [(("sysinfo",), {"_target": "device-1"})]
    assert waited == [("device-1", "sysinfo", 12.0, "abcdef1234567890")]
    assert list(telegram.cards) == [30] and not telegram.sent
    assert "CPU 12.5%" in telegram.cards[30]["text"]
    assert "MUST_NOT_PERSIST" not in persisted


def test_system_snapshot_comparison_is_owner_only_and_edits_one_card(monkeypatch, tmp_path):
    import time
    from device_store import DeviceStore
    telegram = _card_telegram(monkeypatch, tmp_path)
    _setup(monkeypatch, {"device-1": {"name": "Laptop", "os": "Windows 11"}})
    monkeypatch.setattr(DeviceStore, "FILE", tmp_path / "known.json", raising=False)
    monkeypatch.setattr(DeviceStore, "CATALOG_FILE", tmp_path / "catalog.json", raising=False)
    monkeypatch.setattr(bot, "devices", DeviceStore())
    bot.devices.upsert("device-1", {"name": "Laptop", "os": "Windows 11"})
    now = int(time.time())
    bot.devices.save_system_summary_snapshot("device-1", {
        "platform": "Windows", "cpu_percent": 10, "ram_percent": 40, "disk_percent": 60,
    }, captured_at=now - 100, now=now)
    bot.devices.save_system_summary_snapshot("device-1", {
        "platform": "Windows", "cpu_percent": 20, "ram_percent": 40, "disk_percent": 55,
    }, captured_at=now, now=now)
    monkeypatch.setattr(bot, "ADMIN_ID", 20)
    monkeypatch.setattr(bot, "SESSION", {"target": "device-1"})
    monkeypatch.setattr(bot, "get_user_role", lambda _user_id: bot.Role.OWNER)
    monkeypatch.setattr(bot.access_store, "can_use_callback", lambda *_args: True)
    monkeypatch.setattr(bot, "_callback_result_access_or_report", lambda *_args, **_kwargs: asyncio.sleep(0, result=True))

    menu = bot.system_menu(user_id=20)
    buttons = {button.callback_data: button.text for row in menu.inline_keyboard for button in row}
    asyncio.run(bot.on_system_snapshot_comparison(telegram.callback("sysinfo:compare")))

    assert "sysinfo:compare" in buttons and buttons["sysinfo:compare"] != "D01-F25"
    assert _expected_callback_gate("sysinfo:compare") == "OwnerFilter"
    assert list(telegram.cards) == [30] and not telegram.sent
    assert "CPU" in telegram.cards[30]["text"] and "10%" in telegram.cards[30]["text"] and "20%" in telegram.cards[30]["text"]


def test_system_snapshot_export_selects_allowlisted_snapshot_and_keeps_one_card(monkeypatch, tmp_path):
    import time
    from device_store import DeviceStore
    telegram = _card_telegram(monkeypatch, tmp_path)
    _setup(monkeypatch, {"device-1": {"name": "Laptop", "os": "Windows 11"}})
    monkeypatch.setattr(DeviceStore, "FILE", tmp_path / "known.json", raising=False)
    monkeypatch.setattr(DeviceStore, "CATALOG_FILE", tmp_path / "catalog.json", raising=False)
    monkeypatch.setattr(bot, "devices", DeviceStore())
    bot.devices.upsert("device-1", {"name": "Laptop", "os": "Windows 11"})
    now = int(time.time())
    saved = bot.devices.save_system_summary_snapshot("device-1", {
        "platform": "Windows 11", "cpu_percent": 25.0, "ram_percent": 50.0,
        "disk_percent": 70.0, "hostname": "MUST_NOT_EXPORT", "mac": "MUST_NOT_EXPORT",
    }, captured_at=now, now=now)
    monkeypatch.setattr(bot, "ADMIN_ID", 20)
    monkeypatch.setattr(bot, "SESSION", {"target": "device-1"})
    monkeypatch.setattr(bot, "get_user_role", lambda _user_id: bot.Role.OWNER)
    monkeypatch.setattr(bot.access_store, "can_use_callback", lambda *_args: True)
    monkeypatch.setattr(bot, "_callback_result_access_or_report", lambda *_args, **_kwargs: asyncio.sleep(0, result=True))

    menu = bot.system_menu(user_id=20)
    buttons = {button.callback_data: button.text for row in menu.inline_keyboard for button in row}
    asyncio.run(bot.on_system_snapshot_export(telegram.callback("sysinfo:export")))
    choices = telegram.cards[30]["markup"].inline_keyboard
    selected_callback = next(button.callback_data for row in choices for button in row
                             if button.callback_data == f"sysinfo:export:{saved['snapshot_id']}")
    asyncio.run(bot.on_system_snapshot_export_selected(telegram.callback(selected_callback)))

    assert "sysinfo:export" in buttons and buttons["sysinfo:export"] != "D01-F29"
    assert _expected_callback_gate("sysinfo:export") == "OwnerFilter"
    assert _expected_callback_gate(selected_callback) == "OwnerFilter"
    assert len(telegram.sent) == 1 and telegram.sent[0] == 31
    document = telegram.cards[31]["document"]
    assert document.filename.startswith("xider-system-summary-")
    assert b"MUST_NOT_EXPORT" not in document.data
    assert b"cpu_percent" in document.data and b"captured_at" in document.data
    assert 30 not in telegram.cards


def test_scheduled_system_summary_aggregate_is_owner_only_and_updates_one_card(monkeypatch, tmp_path):
    import time
    from device_store import DeviceStore
    telegram = _card_telegram(monkeypatch, tmp_path)
    _setup(monkeypatch, {"device-1": {"name": "Laptop", "os": "Windows 11"}})
    monkeypatch.setattr(DeviceStore, "FILE", tmp_path / "known.json", raising=False)
    monkeypatch.setattr(DeviceStore, "CATALOG_FILE", tmp_path / "catalog.json", raising=False)
    monkeypatch.setattr(bot, "devices", DeviceStore())
    bot.devices.upsert("device-1", {"name": "Laptop", "os": "Windows 11"})
    now = int(time.time())
    bot.devices.save_system_summary_schedule(
        "device-1", interval_seconds=900, schedule_id="a" * 32,
        next_sample_at=now + 900, now=now,
    )
    for captured_at, cpu, ram in ((now - 1_800, 20, 30), (now - 300, 40, 50)):
        bot.devices.save_scheduled_system_summary_snapshot(
            "device-1", schedule_id="a" * 32,
            summary={"platform": "Windows", "cpu_percent": cpu, "ram_percent": ram, "disk_percent": None},
            captured_at=captured_at, now=now,
        )
    monkeypatch.setattr(bot, "ADMIN_ID", 20)
    monkeypatch.setattr(bot, "SESSION", {"target": "device-1"})
    monkeypatch.setattr(bot, "get_user_role", lambda _user_id: bot.Role.OWNER)
    monkeypatch.setattr(bot.access_store, "can_use_callback", lambda *_args: True)
    monkeypatch.setattr(bot, "_callback_result_access_or_report", lambda *_args, **_kwargs: asyncio.sleep(0, result=True))

    menu = bot.system_menu(user_id=20)
    buttons = {button.callback_data: button.text for row in menu.inline_keyboard for button in row}
    asyncio.run(bot.on_system_summary_aggregate(telegram.callback("sysinfo:aggregate")))
    period_buttons = telegram.cards[30]["markup"].inline_keyboard
    selected = next(button.callback_data for row in period_buttons for button in row
                    if button.callback_data == "sysinfo:aggregate:3600")
    asyncio.run(bot.on_system_summary_aggregate_selected(telegram.callback(selected)))

    assert "sysinfo:aggregate" in buttons and buttons["sysinfo:aggregate"] != "D01-F28"
    assert _expected_callback_gate("sysinfo:aggregate") == "OwnerFilter"
    assert _expected_callback_gate(selected) == "OwnerFilter"
    assert list(telegram.cards) == [30] and not telegram.sent
    assert "Число плановых замеров: 2" in telegram.cards[30]["text"]
    assert "CPU" in telegram.cards[30]["text"] and "30%" in telegram.cards[30]["text"]
    assert "RAM" in telegram.cards[30]["text"] and "40%" in telegram.cards[30]["text"]
    assert "Диск" in telegram.cards[30]["text"] and "нет данных" in telegram.cards[30]["text"]


def test_sysinfo_uncertainty_is_owner_only_correlated_and_updates_one_card(monkeypatch, tmp_path):
    telegram = _card_telegram(monkeypatch, tmp_path)
    _setup(monkeypatch, {"device-1": {"name": "Laptop", "os": "Windows 11", "commands": ["sysinfo"]}})
    monkeypatch.setattr(bot, "ADMIN_ID", 20)
    monkeypatch.setattr(bot, "SESSION", {"target": "device-1"})
    monkeypatch.setattr(bot, "get_user_role", lambda user_id: bot.Role.OWNER if user_id == 20 else bot.Role.GUEST)
    monkeypatch.setattr(bot.access_store, "can_use_callback", lambda *_args: True)
    monkeypatch.setattr(bot, "_callback_result_access_or_report", lambda *_args, **_kwargs: asyncio.sleep(0, result=True))
    monkeypatch.setattr(bot, "publish_tracked", lambda *args, **kwargs: (True, "fresh-ticket"))
    checked_at = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")

    async def wait_for(device_id, response_type, timeout, request_id):
        assert (device_id, response_type, request_id) == ("device-1", "sysinfo", "fresh-ticket")
        return {
            "type": "sysinfo", "device_id": "device-1", "os": "Windows 11",
            "cpu_percent": 22.0, "ram_percent": None, "disk_percent": 70.0,
            "regional_settings": {
                "schema": 1, "feature_id": "D01-F23", "device_id": "device-1",
                "request_id": request_id, "source": "local_os_runtime_apis",
                "state": "supported", "checked_at": checked_at,
            },
        }

    monkeypatch.setattr(bot.sysinfo_collector, "wait_for", wait_for)
    owner_buttons = {button.callback_data for row in bot.system_menu(user_id=20).inline_keyboard for button in row}
    guest_buttons = {button.callback_data for row in bot.system_menu(user_id=200).inline_keyboard for button in row}

    asyncio.run(bot.on_sysinfo_uncertainty(telegram.callback("sysinfo:uncertainty")))

    assert "sysinfo:uncertainty" in owner_buttons
    assert "sysinfo:uncertainty" not in guest_buttons
    assert _expected_callback_gate("sysinfo:uncertainty") == "OwnerFilter"
    assert list(telegram.cards) == [30] and not telegram.sent
    assert "CPU: измерено (measured) — 22%" in telegram.cards[30]["text"]
    assert "RAM: нет данных (unavailable)" in telegram.cards[30]["text"]
    assert "Диск: измерено (measured) — 70%" in telegram.cards[30]["text"]


def test_camera_quality_is_owner_only_uses_one_correlated_request_and_edits_one_card(monkeypatch, tmp_path):
    telegram = _card_telegram(monkeypatch, tmp_path)
    _setup(monkeypatch, {"device-1": {"name": "Laptop", "os": "Windows 11", "commands": ["camera_quality"]}})
    monkeypatch.setattr(bot, "ADMIN_ID", 20)
    monkeypatch.setattr(bot, "SESSION", {"target": "device-1"})
    monkeypatch.setattr(bot, "get_user_role", lambda user_id: bot.Role.OWNER if user_id == 20 else bot.Role.GUEST)
    monkeypatch.setattr(bot.access_store, "can_use_callback", lambda *_args: True)
    monkeypatch.setattr(bot, "_callback_result_access_or_report", lambda *_args, **_kwargs: asyncio.sleep(0, result=True))
    published = []
    monkeypatch.setattr(bot, "publish_tracked", lambda *args, **kwargs: (published.append((args, kwargs)) or (True, "camera-ticket")))
    checked_at = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")

    async def wait_for(device_id, response_type, timeout, request_id):
        assert (device_id, response_type, request_id) == ("device-1", "camera_quality_snapshot", "camera-ticket")
        assert timeout == 15.0
        return {
            "type": "camera_quality_snapshot", "schema": "xider.camera-quality.v1",
            "feature_id": "D02-F13", "device_id": device_id, "request_id": request_id,
            "state": "supported", "source": "opencv_laplacian_local", "checked_at": checked_at,
            "data": {
                "width_px": 1280, "height_px": 720, "sharpness_variance": 123.4,
                "mean_luma": 110.0, "dark_clipped_percent": 1.0, "bright_clipped_percent": 3.0,
            },
        }

    monkeypatch.setattr(bot.camera_quality_collector, "wait_for", wait_for)
    owner_buttons = {button.callback_data for row in bot.screen_menu(user_id=20).inline_keyboard for button in row}
    guest_buttons = {button.callback_data for row in bot.screen_menu(user_id=200).inline_keyboard for button in row}

    asyncio.run(bot.on_camera_quality(telegram.callback("camera:quality")))

    assert "camera:quality" in owner_buttons
    assert "camera:quality" not in guest_buttons
    assert _expected_callback_gate("camera:quality") == "OwnerFilter"
    assert published == [(('camera_quality',), {"_target": "device-1"})]
    assert list(telegram.cards) == [30] and not telegram.sent
    assert "1280×720" in telegram.cards[30]["text"]
    assert "кадр не сохранён" in telegram.cards[30]["text"]
    assert "image" not in telegram.cards[30]["text"].lower()


def test_camera_catalog_is_owner_only_and_requires_separate_capture_confirmation(monkeypatch, tmp_path):
    from types import SimpleNamespace

    telegram = _card_telegram(monkeypatch, tmp_path)
    _setup(monkeypatch, {"device-1": {"name": "Laptop", "os": "Windows 11", "capabilities": {"commands": ["camera_catalog", "webcam", "camera_series"]}}})
    monkeypatch.setattr(bot, "ADMIN_ID", 20)
    monkeypatch.setattr(bot, "SESSION", {"target": "device-1"})
    monkeypatch.setattr(bot, "get_user_role", lambda user_id: bot.Role.OWNER if user_id == 20 else bot.Role.GUEST)
    monkeypatch.setattr(bot.access_store, "can_use_callback", lambda *_args: True)
    monkeypatch.setattr(bot, "_callback_result_access_or_report", lambda *_args, **_kwargs: asyncio.sleep(0, result=True))
    published = []
    monkeypatch.setattr(bot, "publish_tracked", lambda *args, **kwargs: (published.append((args, kwargs)) or (True, "catalog-ticket")))
    checked_at = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")

    async def catalog_wait(device_id, response_type, timeout, request_id):
        assert (device_id, response_type, request_id, timeout) == ("device-1", "camera_catalog", "catalog-ticket", 15.0)
        return {
            "type": "camera_catalog", "schema": "xider.camera-catalog.v1",
            "feature_id": "D02-F11", "device_id": device_id, "request_id": request_id,
            "state": "supported", "source": "opencv_enumerate_cameras", "checked_at": checked_at,
            "data": {"cameras": [{"camera_id": "0123456789abcdef", "name": "Built-in Camera"}]},
        }

    monkeypatch.setattr(bot.camera_catalog_collector, "wait_for", catalog_wait)
    async def webcam_wait(device_id, response_type, timeout, request_id):
        assert (device_id, response_type, timeout, request_id) == ("device-1", "webcam", 20.0, "capture-ticket")
        return {"type": "webcam", "device_id": device_id, "id": request_id, "image": base64.b64encode(b"jpeg-bytes").decode()}
    original_publish = bot.publish_tracked
    def publish(action, **kwargs):
        if action == "webcam":
            published.append(((action,), kwargs))
            return True, "capture-ticket"
        return original_publish(action, **kwargs)
    monkeypatch.setattr(bot, "publish_tracked", publish)
    monkeypatch.setattr(bot.webcam_collector, "wait_for", webcam_wait)
    owner_buttons = {button.callback_data for row in bot.screen_menu(user_id=20).inline_keyboard for button in row}
    guest_buttons = {button.callback_data for row in bot.screen_menu(user_id=200).inline_keyboard for button in row}
    asyncio.run(bot.on_camera_catalog(telegram.callback("camera:catalog")))

    assert "camera:catalog" in owner_buttons
    assert "camera:catalog" not in guest_buttons
    assert _expected_callback_gate("camera:catalog") == "OwnerFilter"
    assert published == [(('camera_catalog',), {"_target": "device-1"})]
    assert list(telegram.cards) == [30] and not telegram.sent
    selection = next(button.callback_data for row in telegram.cards[30]["markup"].inline_keyboard for button in row
                     if button.callback_data.startswith("camera:select:"))
    assert "Built-in Camera" in next(button.text for row in telegram.cards[30]["markup"].inline_keyboard for button in row
                                      if button.callback_data == selection)
    asyncio.run(bot.on_camera_select(telegram.callback(selection)))
    assert not published[1:]
    assert "camera:capture" in _callback_data(telegram.cards[30]["markup"])
    assert {"camera:series:2", "camera:series:3", "camera:series:4"} <= set(_callback_data(telegram.cards[30]["markup"]))
    asyncio.run(bot.on_camera_capture(telegram.callback("camera:capture")))
    assert published[1] == (("webcam",), {"_target": "device-1", "camera_id": "0123456789abcdef"})
    assert len(telegram.cards) == 1
    assert next(iter(telegram.cards.values())).get("media") is True
    preview = next(iter(telegram.cards.values()))
    preview_buttons = set(_callback_data(preview["markup"]))
    assert {"camera:preview:keep", "camera:preview:discard"} <= preview_buttons
    assert "Предпросмотр" in preview["text"]
    assert "не сохранён на устройстве" in preview["text"]


def test_camera_series_is_owner_only_correlated_bounded_and_uses_one_preview_card(monkeypatch, tmp_path):
    telegram = _card_telegram(monkeypatch, tmp_path)
    _setup(monkeypatch, {"device-1": {"name": "Laptop", "os": "Windows 11", "capabilities": {"commands": ["webcam", "camera_series"]}}})
    monkeypatch.setattr(bot, "ADMIN_ID", 20)
    monkeypatch.setattr(bot, "SESSION", {"target": "device-1"})
    monkeypatch.setattr(bot, "get_user_role", lambda user_id: bot.Role.OWNER if user_id == 20 else bot.Role.GUEST)
    monkeypatch.setattr(bot.access_store, "can_use_callback", lambda *_args: True)
    monkeypatch.setattr(bot, "_callback_result_access_or_report", lambda *_args, **_kwargs: asyncio.sleep(0, result=True))
    monkeypatch.setattr(bot, "_CAMERA_CATALOGS", {
        (20, "device-1"): {
            "expires_at": time.time() + 60,
            "cameras": {"0123456789abcdef": "Built-in Camera"},
            "selected": "0123456789abcdef",
        },
    })
    sent = []
    monkeypatch.setattr(bot, "publish_tracked", lambda action, **kwargs: (sent.append((action, kwargs)) or (True, "series-ticket")))
    checked_at = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
    async def wait(device_id, response_type, timeout, request_id):
        assert (device_id, response_type, timeout, request_id) == ("device-1", "camera_series_snapshot", 25.0, "series-ticket")
        return {
            "type": "camera_series_snapshot", "schema": "xider.camera-series.v1", "feature_id": "D02-F14",
            "device_id": "device-1", "request_id": "series-ticket", "state": "supported",
            "frame_count": 3, "image": _jpeg_test_image_b64(), "checked_at": checked_at,
        }
    monkeypatch.setattr(bot.camera_series_collector, "wait_for", wait)

    asyncio.run(bot.on_camera_series(telegram.callback("camera:series:3")))

    assert _expected_callback_gate("camera:series:3") == "OwnerFilter"
    assert sent == [("camera_series", {"_target": "device-1", "camera_id": "0123456789abcdef", "series_count": 3})]
    assert len(telegram.cards) == 1
    preview = next(iter(telegram.cards.values()))
    assert preview["media"] is True
    assert "3 кадр" in preview["text"]
    assert {"camera:preview:keep", "camera:preview:discard"} <= set(_callback_data(preview["markup"]))


def test_media_series_cancel_is_owner_only_correlated_and_suppresses_preview(monkeypatch, tmp_path):
    telegram = _card_telegram(monkeypatch, tmp_path)
    _setup(monkeypatch, {"device-1": {"name": "Laptop", "os": "Windows", "capabilities": {
        "commands": ["camera_series", "media_cancel"]}}})
    monkeypatch.setattr(bot, "ADMIN_ID", 20)
    monkeypatch.setattr(bot, "SESSION", {"target": "device-1"})
    monkeypatch.setattr(bot, "get_user_role", lambda user_id: bot.Role.OWNER if user_id == 20 else bot.Role.GUEST)
    monkeypatch.setattr(bot.access_store, "can_use_callback", lambda *_args: True)
    monkeypatch.setattr(bot, "_callback_result_access_or_report", lambda *_args, **_kwargs: asyncio.sleep(0, result=True))
    monkeypatch.setattr(bot, "_CAMERA_CATALOGS", {
        (20, "device-1"): {
            "expires_at": time.time() + 60,
            "cameras": {"0123456789abcdef": "Built-in Camera"},
            "selected": "0123456789abcdef",
        },
    })
    requests = {}
    monkeypatch.setattr(bot, "_MEDIA_SERIES_REQUESTS", requests)
    sent = []
    monkeypatch.setattr(bot, "publish_tracked", lambda action, **kwargs: (sent.append((action, kwargs)) or (True, "cancel-ticket")))
    checked_at = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")

    async def cancel_wait(device_id, response_type, timeout, request_id):
        assert (device_id, response_type, timeout, request_id) == ("device-1", "media_cancel_result", 8.0, "cancel-ticket")
        return {
            "type": "media_cancel_result", "schema": "xider.media-cancel.v1", "feature_id": "D02-F22",
            "device_id": "device-1", "request_id": "series-ticket", "state": "cancelled",
            "checked_at": checked_at,
        }

    monkeypatch.setattr(bot.media_cancel_collector, "wait_for", cancel_wait)
    requests["series-ticket"] = {
        "user_id": 20, "device_id": "device-1", "created_at": time.monotonic(),
        "cancel_requested": False, "cancel_state": None, "cancel_done": asyncio.Event(),
    }

    asyncio.run(bot.on_media_request_cancel(telegram.callback("media:cancel:series-ticket")))

    assert _expected_callback_gate("media:cancel:series-ticket") == "OwnerFilter"
    assert sent == [("media_cancel", {"_target": "device-1", "request_id": "series-ticket"})]
    assert requests["series-ticket"]["cancel_state"] == "cancelled"
    assert requests["series-ticket"]["cancel_done"].is_set()


def test_camera_series_cancellation_withholds_completed_preview(monkeypatch, tmp_path):
    telegram = _card_telegram(monkeypatch, tmp_path)
    _setup(monkeypatch, {"device-1": {"name": "Laptop", "os": "Windows", "capabilities": {
        "commands": ["camera_series", "media_cancel"]}}})
    monkeypatch.setattr(bot, "ADMIN_ID", 20)
    monkeypatch.setattr(bot, "SESSION", {"target": "device-1"})
    monkeypatch.setattr(bot, "get_user_role", lambda _user_id: bot.Role.OWNER)
    monkeypatch.setattr(bot.access_store, "can_use_callback", lambda *_args: True)
    monkeypatch.setattr(bot, "_callback_result_access_or_report", lambda *_args, **_kwargs: asyncio.sleep(0, result=True))
    monkeypatch.setattr(bot, "_CAMERA_CATALOGS", {
        (20, "device-1"): {
            "expires_at": time.time() + 60,
            "cameras": {"0123456789abcdef": "Built-in Camera"},
            "selected": "0123456789abcdef",
        },
    })
    requests = {}
    monkeypatch.setattr(bot, "_MEDIA_SERIES_REQUESTS", requests)
    monkeypatch.setattr(bot, "publish_tracked", lambda *_args, **_kwargs: (True, "series-ticket"))
    checked_at = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")

    async def wait(*_args):
        assert "media:cancel:series-ticket" in _callback_data(telegram.cards[30]["markup"])
        entry = requests["series-ticket"]
        entry["cancel_requested"] = True
        entry["cancel_state"] = "already_completed"
        entry["cancel_done"].set()
        return {
            "type": "camera_series_snapshot", "schema": "xider.camera-series.v1", "feature_id": "D02-F14",
            "device_id": "device-1", "request_id": "series-ticket", "state": "supported",
            "frame_count": 3, "image": _jpeg_test_image_b64(), "checked_at": checked_at,
        }

    monkeypatch.setattr(bot.camera_series_collector, "wait_for", wait)

    asyncio.run(bot.on_camera_series(telegram.callback("camera:series:3")))

    assert not telegram.sent
    assert len(telegram.cards) == 1
    assert "до отмены" in telegram.cards[30]["text"]


def test_camera_series_is_hidden_for_old_agent_and_stale_callback_sends_nothing(monkeypatch, tmp_path):
    telegram = _card_telegram(monkeypatch, tmp_path)
    _setup(monkeypatch, {"device-1": {"name": "Old Laptop", "os": "Windows", "capabilities": {"commands": ["webcam"]}}})
    monkeypatch.setattr(bot, "ADMIN_ID", 20)
    monkeypatch.setattr(bot, "SESSION", {"target": "device-1"})
    monkeypatch.setattr(bot, "get_user_role", lambda _user_id: bot.Role.OWNER)
    monkeypatch.setattr(bot.access_store, "can_use_callback", lambda *_args: True)
    monkeypatch.setattr(bot, "_callback_result_access_or_report", lambda *_args, **_kwargs: asyncio.sleep(0, result=True))
    monkeypatch.setattr(bot, "_CAMERA_CATALOGS", {
        (20, "device-1"): {
            "expires_at": time.time() + 60,
            "cameras": {"0123456789abcdef": "Built-in Camera"},
            "selected": "0123456789abcdef",
        },
    })
    published = []
    monkeypatch.setattr(bot, "publish_tracked", lambda *args, **kwargs: (published.append((args, kwargs)) or (True, "unexpected")))

    asyncio.run(bot.on_camera_select(telegram.callback("camera:select:0123456789abcdef")))
    assert not any(str(data).startswith("camera:series:") for data in _callback_data(telegram.cards[30]["markup"]))
    bot._CAMERA_CATALOGS[(20, "device-1")]["selected"] = "0123456789abcdef"

    asyncio.run(bot.on_camera_series(telegram.callback("camera:series:3")))

    assert published == []
    assert "не поддерживает" in telegram.cards[30]["text"]
    assert _expected_callback_gate("camera:series:3") == "OwnerFilter"


def test_mic_duration_requires_fresh_catalog_then_owner_selects_input(monkeypatch, tmp_path):
    telegram = _card_telegram(monkeypatch, tmp_path)
    _setup(monkeypatch, {"device-1": {"name": "Laptop", "os": "Windows", "capabilities": {
        "commands": ["microphone_catalog", "mic"]}}})
    monkeypatch.setattr(bot, "ADMIN_ID", 20)
    monkeypatch.setattr(bot, "SESSION", {"target": "device-1"})
    monkeypatch.setattr(bot, "get_user_role", lambda user_id: bot.Role.OWNER if user_id == 20 else bot.Role.GUEST)
    monkeypatch.setattr(bot.access_store, "can_use_callback", lambda *_args: True)
    monkeypatch.setattr(bot, "_callback_result_access_or_report", lambda *_args, **_kwargs: asyncio.sleep(0, result=True))
    monkeypatch.setattr(bot, "_MIC_INPUT_CATALOGS", {})
    checked_at = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
    published = []
    def publish(action, **kwargs):
        ticket = "mic-catalog-ticket" if action == "microphone_catalog" else "mic-record-ticket"
        published.append((action, kwargs, ticket))
        return True, ticket
    monkeypatch.setattr(bot, "publish_tracked", publish)

    async def catalog_wait(device_id, response_type, timeout, request_id):
        assert (device_id, response_type, timeout, request_id) == (
            "device-1", "microphone_catalog", 15.0, "mic-catalog-ticket")
        return {
            "type": "microphone_catalog", "schema": "xider.microphone-catalog.v1",
            "feature_id": "D02-F15", "device_id": device_id, "request_id": request_id,
            "state": "supported", "checked_at": checked_at,
            "data": {"inputs": [{"input_id": "1", "name": "USB Mic", "channels": 1, "sample_rate": 48000}]},
        }
    async def mic_wait(device_id, response_type, timeout, request_id):
        assert (device_id, response_type, timeout, request_id) == ("device-1", "mic", 17.0, "mic-record-ticket")
        return {"type": "mic", "device_id": device_id, "id": request_id,
                "audio": base64.b64encode(b"ogg-audio").decode("ascii")}
    monkeypatch.setattr(bot.microphone_catalog_collector, "wait_for", catalog_wait)
    monkeypatch.setattr(bot.mic_collector, "wait_for", mic_wait)

    asyncio.run(bot.on_mic_dur(telegram.callback("micdur:5")))

    assert published == [("microphone_catalog", {"_target": "device-1"}, "mic-catalog-ticket")]
    selection = next(button.callback_data for row in telegram.cards[30]["markup"].inline_keyboard
                     for button in row if button.callback_data.startswith("mic:input:"))
    assert "USB Mic" in next(button.text for row in telegram.cards[30]["markup"].inline_keyboard
                              for button in row if button.callback_data == selection)
    assert _expected_callback_gate(selection) == "OwnerFilter"

    asyncio.run(bot.on_mic_input(telegram.callback(selection)))

    assert published[1] == ("mic", {"_target": "device-1", "duration": 5, "input_device_id": "1"}, "mic-record-ticket")
    assert len(telegram.cards) == 1 and next(iter(telegram.cards.values())).get("media") is True

    asyncio.run(bot.on_mic_input(telegram.callback(selection)))
    assert len(published) == 2  # A consumed picker token cannot trigger a second recording.


def test_mic_picker_fails_closed_for_old_agent_without_catalog_command(monkeypatch, tmp_path):
    telegram = _card_telegram(monkeypatch, tmp_path)
    _setup(monkeypatch, {"device-1": {"name": "Old laptop", "os": "macOS", "capabilities": {"commands": ["mic"]}}})
    monkeypatch.setattr(bot, "ADMIN_ID", 20)
    monkeypatch.setattr(bot, "SESSION", {"target": "device-1"})
    monkeypatch.setattr(bot, "get_user_role", lambda _user_id: bot.Role.OWNER)
    monkeypatch.setattr(bot.access_store, "can_use_callback", lambda *_args: True)
    monkeypatch.setattr(bot, "_callback_result_access_or_report", lambda *_args, **_kwargs: asyncio.sleep(0, result=True))
    published = []
    monkeypatch.setattr(bot, "publish_tracked", lambda *args, **kwargs: (published.append((args, kwargs)) or (True, "unexpected")))

    asyncio.run(bot.on_mic_dur(telegram.callback("micdur:5")))

    assert published == []
    assert "microphone_catalog" in telegram.cards[30]["text"]
    assert _expected_callback_gate("micdur:5") == "OwnerFilter"


def test_legacy_mic_action_opens_duration_picker_instead_of_recording(monkeypatch, tmp_path):
    telegram = _card_telegram(monkeypatch, tmp_path)
    _setup(monkeypatch, {"device-1": {"name": "Laptop", "os": "Windows", "capabilities": {
        "commands": ["mic", "microphone_catalog"]}}})
    monkeypatch.setattr(bot, "ADMIN_ID", 20)
    monkeypatch.setattr(bot, "SESSION", {"target": "device-1"})
    monkeypatch.setattr(bot, "get_user_role", lambda _user_id: bot.Role.OWNER)
    monkeypatch.setattr(bot.access_store, "can_use_callback", lambda *_args: True)
    published = []
    monkeypatch.setattr(bot, "publish_tracked", lambda *args, **kwargs: (published.append((args, kwargs)) or (True, "unexpected")))

    asyncio.run(bot.on_cmd_mic(telegram.callback("cmd:mic")))

    assert published == []
    callbacks = _callback_data(telegram.cards[30]["markup"])
    assert {"micdur:5", "micdur:15", "micdur:30", "micdur:60"}.issubset(callbacks)


def test_microphone_level_is_owner_selected_fresh_correlated_and_never_records_audio(monkeypatch, tmp_path):
    telegram = _card_telegram(monkeypatch, tmp_path)
    _setup(monkeypatch, {"device-1": {"name": "Laptop", "os": "macOS", "capabilities": {
        "commands": ["microphone_catalog", "microphone_level"]}}})
    monkeypatch.setattr(bot, "ADMIN_ID", 20)
    monkeypatch.setattr(bot, "SESSION", {"target": "device-1"})
    monkeypatch.setattr(bot, "get_user_role", lambda user_id: bot.Role.OWNER if user_id == 20 else bot.Role.GUEST)
    monkeypatch.setattr(bot.access_store, "can_use_callback", lambda *_args: True)
    monkeypatch.setattr(bot, "_callback_result_access_or_report", lambda *_args, **_kwargs: asyncio.sleep(0, result=True))
    checked_at = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
    published = []
    def publish(action, **kwargs):
        ticket = "level-catalog" if action == "microphone_catalog" else "level-meter"
        published.append((action, kwargs, ticket))
        return True, ticket
    monkeypatch.setattr(bot, "publish_tracked", publish)

    async def catalog_wait(device_id, response_type, timeout, request_id):
        assert (device_id, response_type, timeout, request_id) == ("device-1", "microphone_catalog", 15.0, "level-catalog")
        return {
            "type": "microphone_catalog", "schema": "xider.microphone-catalog.v1",
            "feature_id": "D02-F15", "device_id": device_id, "request_id": request_id,
            "state": "supported", "checked_at": checked_at,
            "data": {"inputs": [{"input_id": "0", "name": "Built-in Mic", "channels": 1, "sample_rate": 44100}]},
        }
    async def level_wait(device_id, response_type, timeout, request_id):
        assert (device_id, response_type, timeout, request_id) == ("device-1", "microphone_level", 8.0, "level-meter")
        return {
            "type": "microphone_level", "schema": "xider.microphone-level.v1",
            "feature_id": "D02-F16", "device_id": device_id, "request_id": request_id,
            "state": "supported", "source": "sounddevice_input_stream_level", "checked_at": checked_at,
            "data": {"rms_dbfs": -21.4, "peak_dbfs": -3.2, "sample_count": 44100, "observation_ms": 1000},
        }
    monkeypatch.setattr(bot.microphone_catalog_collector, "wait_for", catalog_wait)
    monkeypatch.setattr(bot.microphone_level_collector, "wait_for", level_wait, raising=False)

    asyncio.run(bot.on_mic_level(telegram.callback("mic:level")))
    assert published == [("microphone_catalog", {"_target": "device-1"}, "level-catalog")]
    selection = next(button.callback_data for row in telegram.cards[30]["markup"].inline_keyboard
                     for button in row if button.callback_data.startswith("mic:level:"))
    assert _expected_callback_gate(selection) == "OwnerFilter"

    asyncio.run(bot.on_mic_level_input(telegram.callback(selection)))
    assert published[1] == ("microphone_level", {"_target": "device-1", "input_device_id": "0"}, "level-meter")
    assert len(telegram.cards) == 1 and "-21.4 dBFS" in telegram.cards[30]["text"]
    assert all(action != "mic" for action, _kwargs, _ticket in published)

    asyncio.run(bot.on_mic_level_input(telegram.callback(selection)))
    assert len(published) == 2


def test_microphone_balance_is_owner_selected_correlated_and_edits_one_card(monkeypatch, tmp_path):
    telegram = _card_telegram(monkeypatch, tmp_path)
    _setup(monkeypatch, {"device-1": {"name": "Laptop", "os": "Windows", "capabilities": {
        "commands": ["microphone_catalog", "microphone_balance"]}}})
    monkeypatch.setattr(bot, "ADMIN_ID", 20)
    monkeypatch.setattr(bot, "SESSION", {"target": "device-1"})
    monkeypatch.setattr(bot, "get_user_role", lambda user_id: bot.Role.OWNER if user_id == 20 else bot.Role.GUEST)
    monkeypatch.setattr(bot.access_store, "can_use_callback", lambda *_args: True)
    monkeypatch.setattr(bot, "_callback_result_access_or_report", lambda *_args, **_kwargs: asyncio.sleep(0, result=True))
    monkeypatch.setattr(bot, "_MIC_INPUT_CATALOGS", {"a1b2c3d4e5f6": {
        "owner_id": 20, "target": "device-1", "duration": None, "purpose": "balance",
        "chat_id": 10, "message_id": 30, "expires_at": time.time() + 60,
        "inputs": {"0": "Stereo mic"},
    }})
    checked_at = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
    published = []
    monkeypatch.setattr(bot, "publish_tracked", lambda action, **kwargs: (
        published.append((action, kwargs)) or (True, "balance-ticket")))

    async def balance_wait(device_id, response_type, timeout, request_id):
        assert (device_id, response_type, timeout, request_id) == (
            "device-1", "microphone_balance", 8.0, "balance-ticket")
        return {
            "type": "microphone_balance", "schema": "xider.microphone-balance.v1",
            "feature_id": "D02-F21", "device_id": device_id, "request_id": request_id,
            "state": "supported", "source": "sounddevice_stereo_input_rms", "checked_at": checked_at,
            "data": {"duration_ms": 1000, "sample_rate": 48000, "left_dbfs": -6.02,
                     "right_dbfs": -12.04, "difference_db": 6.02},
        }
    monkeypatch.setattr(bot, "microphone_balance_collector", type("Collector", (), {
        "wait_for": staticmethod(balance_wait),
    })(), raising=False)

    callbacks = set(_callback_data(bot.mic_options_menu(20)))
    assert "mic:balance" in callbacks
    selection = "mic:balance:a1b2c3d4e5f6:0"
    asyncio.run(bot.on_mic_balance_input(telegram.callback(selection)))

    assert published == [("microphone_balance", {
        "_target": "device-1", "input_device_id": "0", "input_device_name": "Stereo mic",
    })]
    assert len(telegram.cards) == 1 and "6.02 dB" in telegram.cards[30]["text"]
    assert "audio" not in telegram.cards[30]["text"].lower()
    assert _expected_callback_gate(selection) == "OwnerFilter"


def test_media_history_is_owner_only_correlated_metadata_and_edits_one_card(monkeypatch, tmp_path):
    telegram = _card_telegram(monkeypatch, tmp_path)
    _setup(monkeypatch, {"device-1": {"name": "Laptop", "os": "macOS", "capabilities": {
        "commands": ["media_history"]}}})
    monkeypatch.setattr(bot, "ADMIN_ID", 20)
    monkeypatch.setattr(bot, "SESSION", {"target": "device-1"})
    monkeypatch.setattr(bot, "get_user_role", lambda user_id: bot.Role.OWNER if user_id == 20 else bot.Role.GUEST)
    monkeypatch.setattr(bot.access_store, "can_use_callback", lambda *_args: True)
    monkeypatch.setattr(bot, "_callback_result_access_or_report", lambda *_args, **_kwargs: asyncio.sleep(0, result=True))
    published = []
    monkeypatch.setattr(bot, "publish_tracked", lambda action, **kwargs: (
        published.append((action, kwargs)) or (True, "history-ticket")))
    checked_at = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")

    async def history_wait(device_id, response_type, timeout, request_id):
        assert (device_id, response_type, timeout, request_id) == (
            "device-1", "media_history", 8.0, "history-ticket")
        return {
            "type": "media_history", "schema": "xider.media-history.v1", "feature_id": "D02-F24",
                "device_id": device_id, "request_id": request_id, "id": request_id, "state": "supported",
            "source": "local_metadata_journal", "checked_at": checked_at,
            "data": {"events": [{"at": checked_at, "action": "mic", "outcome": "captured"}]},
        }
    monkeypatch.setattr(bot, "media_history_collector", type("Collector", (), {
        "wait_for": staticmethod(history_wait),
    })(), raising=False)

    callbacks = set(_callback_data(bot.mic_options_menu(20)))
    assert "media:history" in callbacks
    asyncio.run(bot.on_media_history(telegram.callback("media:history")))

    assert published == [("media_history", {"_target": "device-1", "limit": 20})]
    assert len(telegram.cards) == 1
    assert "микрофон" in telegram.cards[30]["text"] and "захвачено" in telegram.cards[30]["text"]
    assert "PRIVATE" not in telegram.cards[30]["text"]
    assert _expected_callback_gate("media:history") == "OwnerFilter"


def test_audio_output_catalog_is_owner_only_fresh_correlated_and_edits_one_card(monkeypatch, tmp_path):
    telegram = _card_telegram(monkeypatch, tmp_path)
    _setup(monkeypatch, {"device-1": {"name": "Laptop", "os": "macOS", "capabilities": {
        "commands": ["audio_output_catalog"]}}})
    monkeypatch.setattr(bot, "ADMIN_ID", 20)
    monkeypatch.setattr(bot, "SESSION", {"target": "device-1"})
    monkeypatch.setattr(bot, "get_user_role", lambda user_id: bot.Role.OWNER if user_id == 20 else bot.Role.GUEST)
    monkeypatch.setattr(bot.access_store, "can_use_callback", lambda *_args: True)
    monkeypatch.setattr(bot, "_callback_result_access_or_report", lambda *_args, **_kwargs: asyncio.sleep(0, result=True))
    checked_at = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
    published = []
    monkeypatch.setattr(bot, "publish_tracked", lambda action, **kwargs: (published.append((action, kwargs)) or (True, "outputs-ticket")))

    async def output_wait(device_id, response_type, timeout, request_id):
        assert (device_id, response_type, timeout, request_id) == (
            "device-1", "audio_output_catalog", 8.0, "outputs-ticket")
        return {
            "type": "audio_output_catalog", "schema": "xider.audio-output-catalog.v1",
            "feature_id": "D02-F19", "device_id": device_id, "request_id": request_id,
            "state": "supported", "source": "sounddevice_query_devices", "checked_at": checked_at,
            "data": {"outputs": [{"output_id": "2", "name": "Headphones", "channels": 2, "sample_rate": 48000}]},
        }
    monkeypatch.setattr(bot.audio_output_catalog_collector, "wait_for", output_wait, raising=False)

    asyncio.run(bot.on_audio_output_catalog(telegram.callback("audio:outputs")))

    assert published == [("audio_output_catalog", {"_target": "device-1"})]
    assert len(telegram.cards) == 1
    assert "Headphones" in telegram.cards[30]["text"]
    assert "48 kHz" in telegram.cards[30]["text"]
    assert _expected_callback_gate("audio:outputs") == "OwnerFilter"


def test_audio_output_switch_requires_fresh_owner_selection_and_edits_one_card(monkeypatch, tmp_path):
    telegram = _card_telegram(monkeypatch, tmp_path)
    _setup(monkeypatch, {"device-1": {"name": "Laptop", "os": "macOS", "capabilities": {
        "commands": ["audio_output_catalog", "audio_output_switch"]}}})
    monkeypatch.setattr(bot, "ADMIN_ID", 20)
    monkeypatch.setattr(bot, "SESSION", {"target": "device-1"})
    monkeypatch.setattr(bot, "get_user_role", lambda user_id: bot.Role.OWNER if user_id == 20 else bot.Role.GUEST)
    monkeypatch.setattr(bot.access_store, "can_use_callback", lambda *_args: True)
    monkeypatch.setattr(bot, "_callback_result_access_or_report", lambda *_args, **_kwargs: asyncio.sleep(0, result=True))
    monkeypatch.setattr(bot, "_MIC_INPUT_CATALOGS", {})
    checked_at = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
    tickets = {"audio_output_catalog": "catalog-1", "audio_output_switch": "switch-1"}
    published = []
    monkeypatch.setattr(bot, "publish_tracked", lambda action, **kwargs: (
        published.append((action, kwargs)) or (True, tickets[action])))

    async def catalog_wait(device_id, response_type, timeout, request_id):
        assert (device_id, response_type, timeout, request_id) == (
            "device-1", "audio_output_catalog", 8.0, "catalog-1")
        return {"type": "audio_output_catalog", "schema": "xider.audio-output-catalog.v1",
                "feature_id": "D02-F19", "device_id": device_id, "request_id": request_id,
                "state": "supported", "checked_at": checked_at,
                "data": {"outputs": [{"output_id": "2", "name": "Headphones", "channels": 2, "sample_rate": 48000}]}}

    async def switch_wait(device_id, response_type, timeout, request_id):
        assert (device_id, response_type, timeout, request_id) == (
            "device-1", "audio_output_switch", 8.0, "switch-1")
        return {"type": "audio_output_switch", "schema": "xider.audio-output-switch.v1",
                "feature_id": "D02-F18", "device_id": device_id, "request_id": request_id,
                "state": "changed", "checked_at": checked_at,
                "data": {"output_name": "Headphones", "previous_output_name": "Speakers"}}

    monkeypatch.setattr(bot.audio_output_catalog_collector, "wait_for", catalog_wait, raising=False)
    monkeypatch.setattr(bot.audio_output_switch_collector, "wait_for", switch_wait, raising=False)

    asyncio.run(bot.on_audio_output_switch_start(telegram.callback("audio:switch")))
    choice = next(button.callback_data for row in telegram.cards[30]["markup"].inline_keyboard
                  for button in row if button.callback_data.startswith("audio:setout:"))
    assert "одноразовый" in telegram.cards[30]["text"]
    asyncio.run(bot.on_audio_output_switch_select(telegram.callback(choice)))

    assert published == [
        ("audio_output_catalog", {"_target": "device-1"}),
        ("audio_output_switch", {"_target": "device-1", "output_device_id": "2",
                                  "output_device_name": "Headphones"}),
    ]
    assert len(telegram.cards) == 1 and not telegram.sent
    assert "Системный выход переключён" in telegram.cards[30]["text"]
    assert _expected_callback_gate("audio:switch") == "OwnerFilter"
    assert _expected_callback_gate(choice) == "OwnerFilter"
    asyncio.run(bot.on_audio_output_switch_select(telegram.callback(choice)))
    assert len(published) == 2


def test_microphone_roundtrip_is_owner_selected_fresh_and_edits_one_card(monkeypatch, tmp_path):
    telegram = _card_telegram(monkeypatch, tmp_path)
    _setup(monkeypatch, {"device-1": {"name": "Laptop", "os": "macOS", "capabilities": {
        "commands": ["microphone_catalog", "audio_output_catalog", "microphone_test"]}}})
    monkeypatch.setattr(bot, "ADMIN_ID", 20)
    monkeypatch.setattr(bot, "SESSION", {"target": "device-1"})
    monkeypatch.setattr(bot, "get_user_role", lambda user_id: bot.Role.OWNER if user_id == 20 else bot.Role.GUEST)
    monkeypatch.setattr(bot.access_store, "can_use_callback", lambda *_args: True)
    monkeypatch.setattr(bot, "_callback_result_access_or_report", lambda *_args, **_kwargs: asyncio.sleep(0, result=True))
    monkeypatch.setattr(bot, "_MIC_INPUT_CATALOGS", {})
    checked_at = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
    published = []
    tickets = {"microphone_catalog": "mic-catalog", "audio_output_catalog": "output-catalog", "microphone_test": "roundtrip"}
    monkeypatch.setattr(bot, "publish_tracked", lambda action, **kwargs: (published.append((action, kwargs)) or (True, tickets[action])))

    async def wait_for(catalog, device_id, response_type, timeout, request_id):
        assert device_id == "device-1"
        if catalog == "microphone":
            assert (response_type, timeout, request_id) == ("microphone_catalog", 15.0, "mic-catalog")
            return {"type": "microphone_catalog", "schema": "xider.microphone-catalog.v1",
                    "feature_id": "D02-F15", "device_id": device_id, "request_id": request_id,
                    "state": "supported", "checked_at": checked_at,
                    "data": {"inputs": [{"input_id": "0", "name": "Built-in Mic", "channels": 1, "sample_rate": 48000}]}}
        assert (response_type, timeout, request_id) == ("audio_output_catalog", 8.0, "output-catalog")
        return {"type": "audio_output_catalog", "schema": "xider.audio-output-catalog.v1",
                "feature_id": "D02-F19", "device_id": device_id, "request_id": request_id,
                "state": "supported", "checked_at": checked_at,
                "data": {"outputs": [{"output_id": "2", "name": "Headphones", "channels": 2, "sample_rate": 48000}]}}

    async def microphone_wait(device_id, response_type, timeout, request_id):
        assert (device_id, response_type, timeout, request_id) == ("device-1", "microphone_test", 10.0, "roundtrip")
        return {"type": "microphone_test", "schema": "xider.microphone-test.v1", "feature_id": "D02-F17",
                "device_id": device_id, "request_id": request_id, "state": "played", "checked_at": checked_at,
                "data": {"duration_ms": 750, "sample_rate": 48000}}

    monkeypatch.setattr(bot.microphone_catalog_collector, "wait_for", lambda *args: wait_for("microphone", *args))
    monkeypatch.setattr(bot.audio_output_catalog_collector, "wait_for", lambda *args: wait_for("output", *args), raising=False)
    monkeypatch.setattr(bot.microphone_test_collector, "wait_for", microphone_wait, raising=False)

    asyncio.run(bot.on_microphone_test_start(telegram.callback("mic:test")))
    input_callback = next(button.callback_data for row in telegram.cards[30]["markup"].inline_keyboard
                          for button in row if button.callback_data.startswith("mic:input:"))
    asyncio.run(bot.on_mic_input(telegram.callback(input_callback)))
    output_callback = next(button.callback_data for row in telegram.cards[30]["markup"].inline_keyboard
                           for button in row if button.callback_data.startswith("audio:testout:"))
    asyncio.run(bot.on_microphone_test_output(telegram.callback(output_callback)))

    assert published == [
        ("microphone_catalog", {"_target": "device-1"}),
        ("audio_output_catalog", {"_target": "device-1"}),
        ("microphone_test", {"_target": "device-1", "input_device_id": "0", "input_device_name": "Built-in Mic",
                              "output_device_id": "2", "output_device_name": "Headphones"}),
    ]
    assert len(telegram.cards) == 1 and not telegram.sent
    assert "\u043d\u0435 \u0434\u043e\u043a\u0430\u0437\u044b\u0432\u0430\u0435\u0442" in telegram.cards[30]["text"]
    assert _expected_callback_gate("mic:test") == "OwnerFilter"
    assert _expected_callback_gate(output_callback) == "OwnerFilter"

    asyncio.run(bot.on_microphone_test_output(telegram.callback(output_callback)))
    assert len(published) == 3


def test_microphone_preview_is_owner_selected_correlated_and_requires_confirmation(monkeypatch, tmp_path):
    telegram = _card_telegram(monkeypatch, tmp_path)
    _setup(monkeypatch, {"device-1": {"name": "Laptop", "os": "Windows", "capabilities": {
        "commands": ["microphone_catalog", "audio_output_catalog", "microphone_preview"]}}})
    monkeypatch.setattr(bot, "ADMIN_ID", 20)
    monkeypatch.setattr(bot, "SESSION", {"target": "device-1"})
    monkeypatch.setattr(bot, "get_user_role", lambda user_id: bot.Role.OWNER if user_id == 20 else bot.Role.GUEST)
    monkeypatch.setattr(bot.access_store, "can_use_callback", lambda *_args: True)
    monkeypatch.setattr(bot, "_callback_result_access_or_report", lambda *_args, **_kwargs: asyncio.sleep(0, result=True))
    checked_at = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
    published = []
    tickets = {"microphone_catalog": "input-ticket", "audio_output_catalog": "output-ticket",
               "microphone_preview": "preview-ticket"}
    def publish(action, **kwargs):
        published.append((action, kwargs))
        return True, tickets[action]
    monkeypatch.setattr(bot, "publish_tracked", publish)

    async def catalog_wait(device_id, response_type, timeout, request_id):
        if request_id == "input-ticket":
            assert (device_id, response_type, timeout) == ("device-1", "microphone_catalog", 15.0)
            return {"type": "microphone_catalog", "schema": "xider.microphone-catalog.v1",
                    "feature_id": "D02-F15", "device_id": device_id, "request_id": request_id,
                    "state": "supported", "checked_at": checked_at,
                    "data": {"inputs": [{"input_id": "0", "name": "Built-in Mic", "channels": 1, "sample_rate": 48000}]}}
        assert (device_id, response_type, timeout, request_id) == ("device-1", "audio_output_catalog", 8.0, "output-ticket")
        return {"type": "audio_output_catalog", "schema": "xider.audio-output-catalog.v1",
                "feature_id": "D02-F19", "device_id": device_id, "request_id": request_id,
                "state": "supported", "checked_at": checked_at,
                "data": {"outputs": [{"output_id": "2", "name": "Headphones", "channels": 2, "sample_rate": 48000}]}}

    async def preview_wait(device_id, response_type, timeout, request_id):
        assert (device_id, response_type, timeout, request_id) == ("device-1", "microphone_preview", 25.0, "preview-ticket")
        return {"type": "microphone_preview", "schema": "xider.microphone-preview.v1",
                "feature_id": "D02-F20", "device_id": device_id, "request_id": request_id,
                "state": "previewed", "source": "local_playback_before_delivery", "checked_at": checked_at,
                "duration_ms": 5000, "audio": base64.b64encode(b"ogg-sample").decode("ascii")}

    monkeypatch.setattr(bot.microphone_catalog_collector, "wait_for", catalog_wait)
    monkeypatch.setattr(bot.audio_output_catalog_collector, "wait_for", catalog_wait)
    monkeypatch.setattr(bot.microphone_preview_collector, "wait_for", preview_wait)

    asyncio.run(bot.on_microphone_preview_start(telegram.callback("mic:preview")))
    input_callback = next(value for value in _callback_data(telegram.cards[30]["markup"]) if value.startswith("mic:input:"))
    asyncio.run(bot.on_mic_input(telegram.callback(input_callback)))
    output_callback = next(value for value in _callback_data(telegram.cards[30]["markup"]) if value.startswith("audio:previewout:"))
    assert _expected_callback_gate("mic:preview") == _expected_callback_gate(input_callback) == _expected_callback_gate(output_callback) == "OwnerFilter"
    asyncio.run(bot.on_microphone_preview_output(telegram.callback(output_callback)))

    assert published == [
        ("microphone_catalog", {"_target": "device-1"}),
        ("audio_output_catalog", {"_target": "device-1"}),
        ("microphone_preview", {"_target": "device-1", "duration": 5,
                                 "input_device_id": "0", "input_device_name": "Built-in Mic",
                                 "output_device_id": "2", "output_device_name": "Headphones"}),
    ]
    assert not telegram.sent
    assert "ещё не отправлена" in telegram.cards[30]["text"]
    send_callback = next(value for value in _callback_data(telegram.cards[30]["markup"]) if value.startswith("mic:preview:send:"))
    assert _expected_callback_gate(send_callback) == "OwnerFilter"
    asyncio.run(bot.on_microphone_preview_send(telegram.callback(send_callback)))
    assert len(telegram.cards) == 1 and next(iter(telegram.cards.values())).get("media") is True


def test_camera_catalog_rejects_stale_or_untrusted_camera_entries(monkeypatch, tmp_path):
    telegram = _card_telegram(monkeypatch, tmp_path)
    _setup(monkeypatch, {"device-1": {"name": "Laptop", "os": "macOS", "capabilities": {"commands": ["camera_catalog"]}}})
    monkeypatch.setattr(bot, "ADMIN_ID", 20)
    monkeypatch.setattr(bot, "SESSION", {"target": "device-1"})
    monkeypatch.setattr(bot, "get_user_role", lambda _user_id: bot.Role.OWNER)
    monkeypatch.setattr(bot.access_store, "can_use_callback", lambda *_args: True)
    monkeypatch.setattr(bot, "_callback_result_access_or_report", lambda *_args, **_kwargs: asyncio.sleep(0, result=True))
    monkeypatch.setattr(bot, "publish_tracked", lambda *_args, **_kwargs: (True, "catalog-ticket"))
    old = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=5)).isoformat(timespec="seconds")

    async def wait(*_args):
        return {
            "type": "camera_catalog", "schema": "xider.camera-catalog.v1",
            "feature_id": "D02-F11", "device_id": "device-1", "request_id": "catalog-ticket",
            "state": "supported", "checked_at": old,
            "data": {"cameras": [{"camera_id": "0123456789abcdef", "name": "Camera", "path": "/private/device/path"}]},
        }

    monkeypatch.setattr(bot.camera_catalog_collector, "wait_for", wait)
    asyncio.run(bot.on_camera_catalog(telegram.callback("camera:catalog")))
    assert "camera:select:0123456789abcdef" not in _callback_data(telegram.cards[30]["markup"])
    assert "устар" in telegram.cards[30]["text"].lower()


def test_camera_catalog_explains_full_update_when_camera_enumerator_is_missing(monkeypatch, tmp_path):
    telegram = _card_telegram(monkeypatch, tmp_path)
    _setup(monkeypatch, {"device-1": {"name": "Laptop", "os": "macOS", "capabilities": {"commands": ["camera_catalog"]}}})
    monkeypatch.setattr(bot, "ADMIN_ID", 20)
    monkeypatch.setattr(bot, "SESSION", {"target": "device-1"})
    monkeypatch.setattr(bot, "get_user_role", lambda _user_id: bot.Role.OWNER)
    monkeypatch.setattr(bot.access_store, "can_use_callback", lambda *_args: True)
    monkeypatch.setattr(bot, "_callback_result_access_or_report", lambda *_args, **_kwargs: asyncio.sleep(0, result=True))
    monkeypatch.setattr(bot, "publish_tracked", lambda *_args, **_kwargs: (True, "catalog-ticket"))
    checked_at = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")

    async def wait(*_args):
        return {
            "type": "camera_catalog", "schema": "xider.camera-catalog.v1",
            "feature_id": "D02-F11", "device_id": "device-1", "request_id": "catalog-ticket",
            "state": "unsupported", "source": "opencv_camera_enumeration_dependency_unavailable",
            "checked_at": checked_at, "data": {"cameras": []},
        }

    monkeypatch.setattr(bot.camera_catalog_collector, "wait_for", wait)
    asyncio.run(bot.on_camera_catalog(telegram.callback("camera:catalog")))

    assert len(telegram.cards) == 1 and not telegram.sent
    assert "полная установка" in telegram.cards[30]["text"].lower()
    assert "зависимост" in telegram.cards[30]["text"].lower()
    assert "camera:select:" not in _callback_data(telegram.cards[30]["markup"])


def test_camera_preview_keep_is_owner_only_bound_to_one_message_and_one_use(monkeypatch, tmp_path):
    telegram = _card_telegram(monkeypatch, tmp_path)
    _setup(monkeypatch, {"device-1": {"name": "Laptop", "os": "macOS"}})
    monkeypatch.setattr(bot, "ADMIN_ID", 20)
    monkeypatch.setattr(bot, "SESSION", {"target": "device-1"})
    monkeypatch.setattr(bot, "get_user_role", lambda user_id: bot.Role.OWNER if user_id == 20 else bot.Role.GUEST)
    monkeypatch.setattr(bot, "_callback_result_access_or_report", lambda *_args, **_kwargs: asyncio.sleep(0, result=True))
    sent = asyncio.run(telegram.send_photo(10, photo=b"preview", caption="preview"))
    bot.ui_cards.set_card(10, 20, sent.message_id)
    bot._CAMERA_PREVIEWS[(20, "device-1")] = {
        "chat_id": 10, "message_id": sent.message_id, "expires_at": time.time() + 60,
        "camera_id": "0123456789abcdef", "request_id": "capture-ticket",
    }
    edits = []

    async def edit_caption(**kwargs):
        edits.append(kwargs)
        telegram.cards[kwargs["message_id"]].update(text=kwargs["caption"], markup=kwargs["reply_markup"])

    monkeypatch.setattr(telegram, "edit_message_caption", edit_caption, raising=False)
    asyncio.run(bot.on_camera_preview_keep(telegram.callback("camera:preview:keep", message_id=sent.message_id, media=True)))

    assert _expected_callback_gate("camera:preview:keep") == "OwnerFilter"
    assert "camera:preview:keep" not in {button.callback_data for row in bot.back_to_device_kb().inline_keyboard for button in row}
    assert len(edits) == 1 and edits[0]["message_id"] == sent.message_id
    assert "Фото оставлено в чате" in telegram.cards[sent.message_id]["text"]
    assert bot._CAMERA_PREVIEWS == {}

    asyncio.run(bot.on_camera_preview_keep(telegram.callback("camera:preview:keep", message_id=sent.message_id, media=True)))
    assert len(edits) == 1


def test_camera_preview_discard_removes_only_the_selected_preview_card(monkeypatch, tmp_path):
    telegram = _card_telegram(monkeypatch, tmp_path)
    _setup(monkeypatch, {"device-1": {"name": "Laptop", "os": "macOS"}})
    monkeypatch.setattr(bot, "ADMIN_ID", 20)
    monkeypatch.setattr(bot, "SESSION", {"target": "device-1"})
    monkeypatch.setattr(bot, "get_user_role", lambda _user_id: bot.Role.OWNER)
    monkeypatch.setattr(bot.access_store, "can_use_callback", lambda *_args: True)
    monkeypatch.setattr(bot, "_callback_result_access_or_report", lambda *_args, **_kwargs: asyncio.sleep(0, result=True))
    sent = asyncio.run(telegram.send_photo(10, photo=b"preview", caption="preview"))
    bot.ui_cards.set_card(10, 20, sent.message_id)
    bot._CAMERA_PREVIEWS[(20, "device-1")] = {
        "chat_id": 10, "message_id": sent.message_id, "expires_at": time.time() + 60,
        "camera_id": "0123456789abcdef", "request_id": "capture-ticket",
    }

    asyncio.run(bot.on_camera_preview_discard(telegram.callback("camera:preview:discard", message_id=sent.message_id, media=True)))

    assert _expected_callback_gate("camera:preview:discard") == "OwnerFilter"
    assert sent.message_id not in telegram.cards
    replacement_cards = [card for message_id, card in telegram.cards.items() if message_id != 30]
    assert len(replacement_cards) == 1
    assert "Предпросмотр удалён" in replacement_cards[0]["text"]
    assert bot._CAMERA_PREVIEWS == {}


def test_screenshot_mask_navigation_is_owner_only_and_registry_backed(monkeypatch):
    _setup(monkeypatch, {})
    monkeypatch.setattr(bot, "get_user_role", lambda user_id: bot.Role.OWNER if user_id == 20 else bot.Role.GUEST)
    monkeypatch.setattr(bot.access_store, "can_use_callback", lambda *_args: True)
    owner = {button.callback_data for row in bot.screen_menu(user_id=20).inline_keyboard for button in row}
    guest = {button.callback_data for row in bot.screen_menu(user_id=200).inline_keyboard for button in row}

    assert "screenshot:mask" in owner
    assert "screenshot:mask" not in guest
    assert "screenshot:preview" in owner
    assert "screenshot:preview" not in guest
    assert _expected_callback_gate("screenshot:mask") == "OwnerFilter"
    assert _expected_callback_gate("screenshot:mask:cancel") == "OwnerFilter"
    assert _expected_callback_gate("screenshot:mask:send:0123456789abcdef") == "OwnerFilter"
    assert _expected_callback_gate("screenshot:preview") == "OwnerFilter"
    assert _expected_callback_gate("screenshot:preview:cancel:0123456789abcdef") == "OwnerFilter"
    assert _expected_callback_gate("screenshot:preview:send:0123456789abcdef") == "OwnerFilter"


def test_media_temp_cleanup_is_owner_only_and_rejects_uncorrelated_reports(monkeypatch):
    _setup(monkeypatch, {})
    monkeypatch.setattr(bot, "get_user_role", lambda user_id: bot.Role.OWNER if user_id == 20 else bot.Role.GUEST)
    monkeypatch.setattr(bot.access_store, "can_use_callback", lambda *_args: True)
    owner = {button.callback_data for row in bot.screen_menu(user_id=20).inline_keyboard for button in row}
    guest = {button.callback_data for row in bot.screen_menu(user_id=200).inline_keyboard for button in row}
    assert "media:temp:cleanup" in owner
    assert "media:temp:cleanup" not in guest
    assert _expected_callback_gate("media:temp:cleanup") == "OwnerFilter"

    result = {
        "type": "media_temp_cleanup", "schema": "xider.media-temp-cleanup.v1",
        "feature_id": "D02-F23", "device_id": "device-1", "id": "request-1",
        "request_id": "request-1", "state": "success", "source": "xider_owned_media_temp_v1",
        "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "data": {"state": "success", "scanned": 4, "deleted": 2, "skipped": 2, "failed": 0},
    }
    rendered = bot._format_media_temp_cleanup_result(result, expected_device_id="device-1", expected_request_id="request-1")
    assert "удалено просроченных: 2" in rendered
    assert "Пути и содержимое файлов не собирались" in rendered
    assert "не совпал" in bot._format_media_temp_cleanup_result(
        result, expected_device_id="device-other", expected_request_id="request-1",
    )
    stale = dict(result, checked_at="2020-01-01T00:00:00+00:00")
    assert "устарел" in bot._format_media_temp_cleanup_result(
        stale, expected_device_id="device-1", expected_request_id="request-1",
    )


def test_network_action_buttons_are_backed_by_declarative_navigation(monkeypatch):
    _setup(monkeypatch, {})
    monkeypatch.setattr(bot, "get_user_role", lambda _user_id: bot.Role.OWNER)
    monkeypatch.setattr(bot.access_store, "can_use_callback", lambda *_args: True)
    rows = bot.feature_catalog.visible_navigation_pages(
        bot.feature_catalog.load_registry(),
        parent="network",
        role="owner",
        grants=set(),
        platform="windows",
    )
    declared_callbacks = {str(row["callback"]) for row in rows}
    rendered_callbacks = set(_callback_data(bot.network_menu(user_id=100)))
    rendered_actions = rendered_callbacks - {"back:device", "menu:main"}
    assert declared_callbacks == rendered_actions


def test_file_action_buttons_are_backed_by_declarative_navigation(monkeypatch):
    _setup(monkeypatch, {})
    monkeypatch.setattr(bot, "get_user_role", lambda _user_id: bot.Role.OWNER)
    monkeypatch.setattr(bot.access_store, "can_use_callback", lambda *_args: True)
    rows = bot.feature_catalog.visible_navigation_pages(
        bot.feature_catalog.load_registry(),
        parent="files",
        role="owner",
        grants=set(),
        platform="windows",
    )
    declared_callbacks = {str(row["callback"]) for row in rows}
    rendered_callbacks = set(_callback_data(bot.files_menu()))
    rendered_actions = rendered_callbacks - {"back:device", "menu:main"}
    assert declared_callbacks == rendered_actions


def test_terminal_action_buttons_are_backed_by_declarative_navigation(monkeypatch):
    _setup(monkeypatch, {})
    monkeypatch.setattr(bot, "get_user_role", lambda _user_id: bot.Role.OWNER)
    monkeypatch.setattr(bot.access_store, "can_use_callback", lambda *_args: True)
    rows = bot.feature_catalog.visible_navigation_pages(
        bot.feature_catalog.load_registry(),
        parent="terminal",
        role="owner",
        grants=set(),
        platform="windows",
    )
    declared_callbacks = {str(row["callback"]) for row in rows}
    rendered_callbacks = set(_callback_data(bot.terminal_menu(user_id=100)))
    rendered_actions = rendered_callbacks - {"back:device", "menu:main"}
    assert declared_callbacks == rendered_actions


def test_terminal_registry_menu_still_hides_shell_without_explicit_callback_grant(monkeypatch):
    _setup(monkeypatch, {})
    monkeypatch.setattr(bot, "get_user_role", lambda _user_id: bot.Role.USER)
    monkeypatch.setattr(
        bot.access_store, "can_use_callback",
        lambda user_id, callback, _owner, _target=None: user_id == 200 and callback == "proc:0",
    )

    callbacks = _callback_data(bot.terminal_menu(user_id=200))

    assert "proc:0" in callbacks
    assert "cmd:shell" not in callbacks


def test_input_action_buttons_are_backed_by_declarative_navigation(monkeypatch):
    _setup(monkeypatch, {})
    monkeypatch.setattr(bot, "get_user_role", lambda _user_id: bot.Role.OWNER)
    monkeypatch.setattr(bot.access_store, "can_use_callback", lambda *_args: True)
    rows = bot.feature_catalog.visible_navigation_pages(
        bot.feature_catalog.load_registry(),
        parent="input",
        role="owner",
        grants=set(),
        platform="windows",
    )
    declared_callbacks = {str(row["callback"]) for row in rows}
    rendered_callbacks = set(_callback_data(bot.input_menu(user_id=100)))
    rendered_actions = rendered_callbacks - {"back:device", "menu:main"}
    assert declared_callbacks == rendered_actions
def test_system_summary_interval_is_owner_only_and_applies_correlated_agent_ack(monkeypatch, tmp_path):
    import time
    from device_store import DeviceStore
    telegram = _card_telegram(monkeypatch, tmp_path)
    _setup(monkeypatch, {"device-1": {"name": "Laptop", "os": "Windows 11", "commands": ["sysinfo_interval"]}})
    monkeypatch.setattr(DeviceStore, "FILE", tmp_path / "known.json", raising=False)
    monkeypatch.setattr(DeviceStore, "CATALOG_FILE", tmp_path / "catalog.json", raising=False)
    monkeypatch.setattr(bot, "devices", DeviceStore())
    bot.devices.upsert("device-1", {"name": "Laptop", "os": "Windows 11", "commands": ["sysinfo_interval"]})
    monkeypatch.setattr(bot, "ADMIN_ID", 20)
    monkeypatch.setattr(bot, "SESSION", {"target": "device-1"})
    monkeypatch.setattr(bot, "get_user_role", lambda _user_id: bot.Role.OWNER)
    monkeypatch.setattr(bot.access_store, "can_use_callback", lambda *_args: True)
    monkeypatch.setattr(bot, "_callback_result_access_or_report", lambda *_args, **_kwargs: asyncio.sleep(0, result=True))
    published = []
    monkeypatch.setattr(bot, "publish_tracked", lambda *args, **kwargs: (published.append((args, kwargs)) or (True, "request-1")))

    async def wait_for(device_id, response_type, timeout, request_id):
        assert (device_id, response_type, request_id) == ("device-1", "sysinfo_schedule_status", "request-1")
        return {"schema": "xider.sysinfo-schedule.v1", "feature_id": "D01-F26",
                "device_id": "device-1", "request_id": request_id, "state": "supported",
                "interval_seconds": 900, "schedule_id": published[0][1]["schedule_id"],
                "next_sample_at": int(time.time()) + 900}
    monkeypatch.setattr(bot, "sysinfo_schedule_collector", type("Collector", (), {"wait_for": staticmethod(wait_for)})())
    menu = bot.system_menu(user_id=20)
    buttons = {button.callback_data: button.text for row in menu.inline_keyboard for button in row}
    asyncio.run(bot.on_system_summary_interval_selected(telegram.callback("sysinfo:interval:900")))
    assert "sysinfo:interval" in buttons and buttons["sysinfo:interval"] != "D01-F26"
    assert _expected_callback_gate("sysinfo:interval") == "OwnerFilter"
    assert _expected_callback_gate("sysinfo:interval:900") == "OwnerFilter"
    assert len(published) == 1 and published[0][0] == ("sysinfo_interval",)
    assert published[0][1]["_target"] == "device-1"
    assert published[0][1]["interval_seconds"] == 900
    assert bot.devices.get_system_summary_schedule("device-1")["schedule_id"] == published[0][1]["schedule_id"]
    assert bot.devices.get_system_summary_schedule("device-1")["interval_seconds"] == 900
    assert list(telegram.cards) == [30] and not telegram.sent


def test_system_summary_threshold_settings_are_owner_only_and_replace_one_card(monkeypatch, tmp_path):
    from device_store import DeviceStore

    telegram = _card_telegram(monkeypatch, tmp_path)
    _setup(monkeypatch, {"device-1": {"name": "Laptop", "os": "Windows 11", "commands": []}})
    monkeypatch.setattr(DeviceStore, "FILE", tmp_path / "known.json", raising=False)
    monkeypatch.setattr(DeviceStore, "CATALOG_FILE", tmp_path / "catalog.json", raising=False)
    monkeypatch.setattr(bot, "devices", DeviceStore())
    bot.devices.upsert("device-1", {"name": "Laptop", "os": "Windows 11", "commands": []})
    monkeypatch.setattr(bot, "ADMIN_ID", 20)
    monkeypatch.setattr(bot, "SESSION", {"target": "device-1"})
    monkeypatch.setattr(bot, "get_user_role", lambda user_id: bot.Role.OWNER if user_id == 20 else bot.Role.GUEST)
    monkeypatch.setattr(bot.access_store, "can_use_callback", lambda *_args: True)
    monkeypatch.setattr(bot, "_callback_result_access_or_report", lambda *_args, **_kwargs: asyncio.sleep(0, result=True))

    menu = bot.system_menu(user_id=20)
    buttons = {button.callback_data: button.text for row in menu.inline_keyboard for button in row}
    guest_menu = bot.system_menu(user_id=200)
    guest_callbacks = {button.callback_data for row in guest_menu.inline_keyboard for button in row}
    asyncio.run(bot.on_system_summary_thresholds(telegram.callback("sysinfo:threshold")))
    asyncio.run(bot.on_system_summary_threshold_selected(telegram.callback("sysinfo:threshold:cpu_percent")))
    asyncio.run(bot.on_system_summary_threshold_selected(telegram.callback("sysinfo:threshold:cpu_percent:80")))

    assert "sysinfo:threshold" in buttons and "D01-F27" not in buttons["sysinfo:threshold"]
    assert "sysinfo:threshold" not in guest_callbacks
    assert _expected_callback_gate("sysinfo:threshold") == "OwnerFilter"
    assert _expected_callback_gate("sysinfo:threshold:cpu_percent") == "OwnerFilter"
    assert _expected_callback_gate("sysinfo:threshold:cpu_percent:80") == "OwnerFilter"
    assert bot.devices.get_system_summary_thresholds("device-1")["cpu_percent"] == 80.0
    assert list(telegram.cards) == [30] and not telegram.sent


def test_scheduled_threshold_notice_requires_two_samples_and_valid_sensor_provenance(monkeypatch, tmp_path):
    import datetime
    from device_store import DeviceStore

    base = int(time.time())
    monkeypatch.setattr(DeviceStore, "FILE", tmp_path / "known.json", raising=False)
    monkeypatch.setattr(DeviceStore, "CATALOG_FILE", tmp_path / "catalog.json", raising=False)
    store = DeviceStore()
    store.upsert("device-1", {"name": "Laptop", "os": "Windows 11"})
    store.save_system_summary_schedule("device-1", interval_seconds=900, schedule_id="a" * 32,
                                       next_sample_at=base + 900, now=base)
    store.set_system_summary_threshold("device-1", "cpu_percent", 80)
    store.set_system_summary_threshold("device-1", "temperature_celsius", 70)
    monkeypatch.setattr(bot, "devices", store)
    monkeypatch.setattr(bot, "ENCRYPT_PAYLOAD", False)
    monkeypatch.setattr(bot, "verify_message_detailed", lambda payload, **_kwargs: (payload, "ok"))
    monkeypatch.setattr(bot.bot_settings, "is_blocked", lambda _device_id: False)
    monkeypatch.setattr(bot, "target_label", lambda _device_id: "Laptop")
    notices = []
    monkeypatch.setattr(bot, "_schedule_owner_notice", lambda text, **_kwargs: notices.append(text) or True)

    def sample(offset, *, source="windows_wmi_MSAcpi_ThermalZoneTemperature"):
        checked_at = datetime.datetime.fromtimestamp(base + offset, datetime.timezone.utc).isoformat()
        bot.on_mqtt_message(
            f"{bot.MQTT_PREFIX}/device-1/sysinfo_schedule",
            {"type": "sysinfo_summary_sample", "schema": "xider.sysinfo-summary.v1",
             "feature_id": "D01-F26", "device_id": "device-1", "schedule_id": "a" * 32,
             "checked_at": checked_at, "next_sample_at": base + 900,
             "summary": {"platform": "Windows", "cpu_percent": 82, "ram_percent": 25, "disk_percent": 30},
             "temperature": {"state": "supported", "source": source, "max_celsius": 72}},
        )

    sample(1, source="untrusted_source")
    assert notices == []
    sample(2)
    assert len(notices) == 1
    assert "CPU" in notices[0] and "82%" in notices[0]
    sample(3)
    assert len(notices) == 2
    assert "температур" in notices[1].lower() and "72°C" in notices[1]
    sample(4)
    assert len(notices) == 2


def test_scheduled_thresholds_ignore_samples_older_than_active_schedule_window(monkeypatch, tmp_path):
    import datetime
    from device_store import DeviceStore

    base = int(time.time())
    monkeypatch.setattr(DeviceStore, "FILE", tmp_path / "known.json", raising=False)
    monkeypatch.setattr(DeviceStore, "CATALOG_FILE", tmp_path / "catalog.json", raising=False)
    store = DeviceStore()
    store.upsert("device-1", {"name": "Laptop", "os": "Windows 11"})
    store.save_system_summary_schedule("device-1", interval_seconds=900, schedule_id="b" * 32,
                                       next_sample_at=base + 900, now=base)
    store.set_system_summary_threshold("device-1", "cpu_percent", 80)
    monkeypatch.setattr(bot, "devices", store)
    monkeypatch.setattr(bot, "ENCRYPT_PAYLOAD", False)
    monkeypatch.setattr(bot, "verify_message_detailed", lambda payload, **_kwargs: (payload, "ok"))
    monkeypatch.setattr(bot.bot_settings, "is_blocked", lambda _device_id: False)
    notices = []
    monkeypatch.setattr(bot, "_schedule_owner_notice", lambda text, **_kwargs: notices.append(text) or True)

    for offset in (-3000, -2101):
        checked_at = datetime.datetime.fromtimestamp(base + offset, datetime.timezone.utc).isoformat()
        bot.on_mqtt_message(
            f"{bot.MQTT_PREFIX}/device-1/sysinfo_schedule",
            {"type": "sysinfo_summary_sample", "schema": "xider.sysinfo-summary.v1",
             "feature_id": "D01-F26", "device_id": "device-1", "schedule_id": "b" * 32,
             "checked_at": checked_at, "next_sample_at": base + 900,
             "summary": {"platform": "Windows", "cpu_percent": 95, "ram_percent": 10, "disk_percent": 10}},
        )

    assert notices == []
    monitor = store._catalog["system_summary_thresholds"]["device-1"]["monitor"]["cpu_percent"]
    assert monitor["streak"] == 0 and monitor["last_sample_at"] is None
    assert store.get_system_summary_schedule("device-1")["next_sample_at"] == base + 900
    assert store.list_system_summary_snapshots("device-1", now=base) == []


def test_display_contact_sheet_navigation_is_owner_only_and_supports_explicit_selection(monkeypatch):
    from contextvars import ContextVar

    devices = FakeDevices({"device-1": {"name": "Test PC", "os": "Windows 11"}})
    session = bot.SessionRegistry()
    session["target"] = "device-1"
    monkeypatch.setattr(bot, "devices", devices)
    monkeypatch.setattr(bot, "SESSION", session)
    monkeypatch.setattr(bot, "CURRENT_TG_USER", ContextVar("display_owner", default=bot.ADMIN_ID))
    monkeypatch.setattr(bot, "get_user_role", lambda user_id: bot.Role.OWNER if user_id == bot.ADMIN_ID else bot.Role.GUEST)
    monkeypatch.setattr(bot.access_store, "get_effective_grants", lambda _user_id: {"callbacks": []})

    owner = bot.screen_menu(user_id=bot.ADMIN_ID)
    guest = bot.screen_menu(user_id=200)
    owner_callbacks = {button.callback_data for row in owner.inline_keyboard for button in row}
    guest_callbacks = {button.callback_data for row in guest.inline_keyboard for button in row}
    assert "display:catalog" in owner_callbacks
    assert "display:catalog" not in guest_callbacks
    assert "window:screenshot" in owner_callbacks
    assert "window:screenshot" not in guest_callbacks
    assert "display:screenshot" in owner_callbacks
    assert "display:screenshot" not in guest_callbacks
    assert "display:resolution" in owner_callbacks
    assert "display:resolution" not in guest_callbacks
    assert _expected_callback_gate("display:catalog") == "OwnerFilter"
    assert _expected_callback_gate("display:screenshot") == "OwnerFilter"
    assert _expected_callback_gate("display:resolution") == "OwnerFilter"
    assert _expected_callback_gate("display:resolution:check") == "OwnerFilter"
    assert _expected_callback_gate("display:toggle:display-1") == "OwnerFilter"
    assert _expected_callback_gate("display:send") == "OwnerFilter"
    assert _expected_callback_gate("display:region:begin") == "OwnerFilter"

    window_markup = bot._window_selection_keyboard(
        [{"id": "window-1", "title": "Editor", "app": "editor.exe", "width": 800, "height": 600}],
        "window-1",
    )
    window_callbacks = [button.callback_data for row in window_markup.inline_keyboard for button in row]
    assert window_callbacks == ["window:toggle:window-1", "window:capture", "window:screenshot", "window:cancel"]

    markup = bot._display_selection_keyboard(
        [{"id": "display-1", "width": 1920, "height": 1080, "primary": True}],
        ["display-1"],
    )
    callbacks = [button.callback_data for row in markup.inline_keyboard for button in row]
    assert callbacks == ["display:toggle:display-1", "display:send", "display:catalog", "display:cancel"]

    single_markup = bot._display_selection_keyboard(
        [{"id": "display-1", "width": 1920, "height": 1080, "primary": True}],
        ["display-1"], mode="single",
    )
    single_callbacks = [button.callback_data for row in single_markup.inline_keyboard for button in row]
    assert single_callbacks == [
        "display:toggle:display-1", "display:single", "display:region:begin", "display:screenshot", "display:cancel",
    ]
    resolution_markup = bot._display_selection_keyboard(
        [{"id": "display-1", "width": 1920, "height": 1080, "primary": True}],
        ["display-1"], mode="resolution",
    )
    resolution_callbacks = [button.callback_data for row in resolution_markup.inline_keyboard for button in row]
    assert resolution_callbacks == [
        "display:toggle:display-1", "display:resolution:check", "display:resolution", "display:cancel",
    ]


def test_display_region_parser_requires_four_bounded_integer_coordinates():
    parsed = bot._parse_display_region_input("2, 1, 30, 20", width=100, height=80)
    assert parsed == {"x": 2, "y": 1, "width": 30, "height": 20}
    for invalid in ("-1,0,1,1", "0,0,0,1", "0,0,101,1", "1,2,3,4,5", "__import__('os')"):
        try:
            bot._parse_display_region_input(invalid, width=100, height=80)
        except ValueError:
            pass
        else:
            raise AssertionError("invalid display-region input must be rejected")


def test_display_region_result_requires_exact_request_and_cropped_dimensions():
    import base64
    import datetime
    import io
    from PIL import Image

    now = datetime.datetime.now(datetime.timezone.utc)
    output = io.BytesIO()
    Image.new("RGB", (30, 20), "orange").save(output, format="PNG")
    expected = {"id": "display-2", "x": 2, "y": 1, "width": 30, "height": 20}
    result = {
        "type": "display_region_screenshot", "schema": "xider.display-region-screenshot.v1",
        "feature_id": "D02-F03", "device_id": "device-1", "id": "region-1", "request_id": "region-1",
        "state": "success", "source": "windows_enum_display_monitors", "checked_at": now.isoformat(),
        "data": expected, "image": base64.b64encode(output.getvalue()).decode("ascii"),
    }
    decoded = bot._validate_display_region_screenshot_result(
        result, expected_device_id="device-1", expected_request_id="region-1",
        expected_region=expected, expected_source="windows_enum_display_monitors", now=now,
    )
    with Image.open(io.BytesIO(decoded)) as image:
        assert image.size == (30, 20)
    forged = {**result, "data": {**expected, "width": 31}}
    try:
        bot._validate_display_region_screenshot_result(
            forged, expected_device_id="device-1", expected_request_id="region-1",
            expected_region=expected, expected_source="windows_enum_display_monitors", now=now,
        )
    except ValueError:
        pass
    else:
        raise AssertionError("uncorrelated region manifest must be rejected")


def test_display_region_owner_input_publishes_exact_crop_and_replaces_one_card(monkeypatch):
    import asyncio
    import base64
    import datetime
    import io
    from types import SimpleNamespace
    from PIL import Image

    output = io.BytesIO()
    Image.new("RGB", (30, 20), "orange").save(output, format="PNG")
    response = {
        "type": "display_region_screenshot", "schema": "xider.display-region-screenshot.v1",
        "feature_id": "D02-F03", "device_id": "device-1", "id": "region-1", "request_id": "region-1",
        "state": "success", "source": "windows_enum_display_monitors",
        "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "data": {"id": "display-2", "x": 2, "y": 1, "width": 30, "height": 20},
        "image": base64.b64encode(output.getvalue()).decode("ascii"),
    }

    class State:
        data = {
            "authorization_target": "device-1", "authorization_callback": "display:screenshot",
            "display_region_id": "display-2", "display_region_dimensions": {"width": 100, "height": 80},
            "display_catalog": {"topology_id": "a" * 16, "source": "windows_enum_display_monitors"},
        }
        async def get_data(self): return dict(self.data)
        async def get_state(self): return bot.Form.wait_display_region_input.state
        async def clear(self): self.data = {}

    class Collector:
        async def wait_for(self, target, topic, timeout, request_id):
            assert (target, topic, request_id) == ("device-1", "display_region_screenshot", "region-1")
            assert timeout <= 20
            return response

    published, edits, photos = [], [], []
    async def replace_card(_message, text, **kwargs): edits.append((text, kwargs))
    async def replace_photo(_message, image, **kwargs):
        photos.append((image, kwargs))
        assert kwargs["delivery_guard"]()

    monkeypatch.setattr(bot, "devices", FakeDevices({"device-1": {"os": "Windows 11", "capabilities": {"commands": ["display_region_screenshot"]}}}))
    monkeypatch.setattr(bot, "display_region_screenshot_collector", Collector())
    monkeypatch.setattr(bot, "publish_tracked", lambda command, **kwargs: published.append((command, kwargs)) or (True, "region-1"))
    monkeypatch.setattr(bot, "_pending_device_input_still_allowed", lambda *_args: True)
    monkeypatch.setattr(bot, "_replace_user_card", replace_card)
    monkeypatch.setattr(bot, "_replace_user_card_with_photo", replace_photo)
    message = SimpleNamespace(
        from_user=SimpleNamespace(id=bot.ADMIN_ID),
        chat=SimpleNamespace(id=77), text="2, 1, 30, 20",
    )

    asyncio.run(bot.on_display_region_input(message, State()))

    assert published == [("display_region_screenshot", {
        "_target": "device-1", "display_id": "display-2", "topology_id": "a" * 16,
        "region": {"x": 2, "y": 1, "width": 30, "height": 20},
    })]
    assert len(edits) == 1 and "Снимаю" in edits[0][0]
    assert len(photos) == 1 and photos[0][0] == output.getvalue()
    assert "30×20" in photos[0][1]["caption"]


def test_display_catalog_formatter_requires_fresh_correlated_explicit_catalog():
    import datetime

    now = datetime.datetime.now(datetime.timezone.utc)
    base = {
        "type": "display_catalog", "schema": "xider.display-catalog.v1", "feature_id": "D02-F06",
        "device_id": "device-1", "id": "request-1", "request_id": "request-1", "state": "success",
        "source": "windows_enum_display_monitors", "checked_at": now.isoformat(),
        "data": {"topology_id": "a" * 16, "displays": [
            {"id": "display-1", "width": 1920, "height": 1080, "primary": True},
            {"id": "display-2", "width": 2560, "height": 1440, "primary": False},
        ]},
    }
    parsed = bot._parse_display_catalog_result(
        base, expected_device_id="device-1", expected_request_id="request-1", now=now,
    )
    assert parsed["topology_id"] == "a" * 16
    assert [item["id"] for item in parsed["displays"]] == ["display-1", "display-2"]
    for field, value in (("device_id", "device-2"), ("request_id", "old"), ("source", "unknown")):
        invalid = {**base, field: value}
        try:
            bot._parse_display_catalog_result(
                invalid, expected_device_id="device-1", expected_request_id="request-1", now=now,
            )
        except ValueError:
            pass
        else:
            raise AssertionError(f"forged {field} must be rejected")
    stale = {**base, "checked_at": (now - datetime.timedelta(minutes=5)).isoformat()}
    try:
        bot._parse_display_catalog_result(
            stale, expected_device_id="device-1", expected_request_id="request-1", now=now,
        )
    except ValueError:
        pass
    else:
        raise AssertionError("stale display catalog must be rejected")


def test_display_contact_sheet_result_is_bound_to_exact_selection_and_platform():
    import base64
    import datetime
    import io
    from PIL import Image

    now = datetime.datetime.now(datetime.timezone.utc)
    output = io.BytesIO()
    Image.new("RGB", (32, 24), (10, 20, 30)).save(output, format="JPEG")
    base = {
        "type": "display_contact_sheet", "schema": "xider.display-contact-sheet.v1", "feature_id": "D02-F06",
        "device_id": "device-1", "id": "request-1", "request_id": "request-1", "state": "success",
        "source": "macos_coregraphics_active_displays", "checked_at": now.isoformat(),
        "data": {"displays": [{"id": "display-2", "width": 2560, "height": 1440, "primary": False}]},
        "image": base64.b64encode(output.getvalue()).decode("ascii"),
    }
    assert bot._validate_display_contact_sheet_result(
        base, expected_device_id="device-1", expected_request_id="request-1",
        expected_selected=["display-2"], expected_source="macos_coregraphics_active_displays", now=now,
    ).startswith(b"\xff\xd8\xff")
    for changed in (
        {**base, "source": "windows_enum_display_monitors"},
        {**base, "data": {"displays": [{"id": "display-1", "width": 2560, "height": 1440, "primary": False}]}},
        {**base, "request_id": "another-request"},
    ):
        try:
            bot._validate_display_contact_sheet_result(
                changed, expected_device_id="device-1", expected_request_id="request-1",
                expected_selected=["display-2"], expected_source="macos_coregraphics_active_displays", now=now,
            )
        except ValueError:
            pass
        else:
            raise AssertionError("contact sheet response mismatch must be rejected")


def test_single_display_result_is_fresh_correlated_and_bound_to_one_display():
    import base64
    import datetime
    import io
    from PIL import Image

    now = datetime.datetime.now(datetime.timezone.utc)
    output = io.BytesIO()
    Image.new("RGB", (640, 900), "blue").save(output, format="PNG")
    base = {
        "type": "display_screenshot", "schema": "xider.display-screenshot.v1",
        "feature_id": "D02-F01", "device_id": "device-1", "id": "request-1",
        "request_id": "request-1", "state": "success", "source": "windows_enum_display_monitors",
        "checked_at": now.isoformat(),
        "data": {"id": "display-2", "width": 640, "height": 900, "primary": False},
        "image": base64.b64encode(output.getvalue()).decode("ascii"),
    }
    decoded = bot._validate_display_screenshot_result(
        base, expected_device_id="device-1", expected_request_id="request-1",
        expected_display_id="display-2", expected_source="windows_enum_display_monitors", now=now,
    )
    with Image.open(io.BytesIO(decoded)) as opened:
        assert opened.format == "PNG"
        assert opened.size == (640, 900)
        assert opened.getpixel((320, 450)) == (0, 0, 255)

    for field, value in (("device_id", "device-2"), ("request_id", "old"), ("feature_id", "D02-F06")):
        invalid = {**base, field: value}
        try:
            bot._validate_display_screenshot_result(
                invalid, expected_device_id="device-1", expected_request_id="request-1",
                expected_display_id="display-2", expected_source="windows_enum_display_monitors", now=now,
            )
        except ValueError:
            pass
        else:
            raise AssertionError(f"forged {field} must be rejected")


def test_single_display_callback_dispatches_only_the_selected_display_and_replaces_one_card(monkeypatch):
    import asyncio
    import base64
    import datetime
    import io
    from types import SimpleNamespace
    from PIL import Image

    output = io.BytesIO()
    Image.new("RGB", (64, 48), "navy").save(output, format="PNG")
    request = {
        "type": "display_screenshot", "schema": "xider.display-screenshot.v1",
        "feature_id": "D02-F01", "device_id": "device-1", "id": "shot-1", "request_id": "shot-1",
        "state": "success", "source": "windows_enum_display_monitors",
        "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "data": {"id": "display-2", "width": 64, "height": 48, "primary": False},
        "image": base64.b64encode(output.getvalue()).decode("ascii"),
    }

    class State:
        data = {"authorization_target": "device-1", "display_mode": "single",
                "display_catalog": {"topology_id": "a" * 16, "source": "windows_enum_display_monitors"},
                "display_selected": ["display-2"]}
        async def get_data(self): return dict(self.data)
        async def get_state(self): return bot.Form.wait_display_selection.state
        async def clear(self): self.data = {}

    class Collector:
        async def wait_for(self, target, topic, timeout, request_id):
            assert (target, topic, request_id) == ("device-1", "display_screenshot", "shot-1")
            assert timeout <= 20
            return request

    published, replaced = [], []
    async def answer(*_args, **_kwargs): pass
    async def replace_media(cq, sender, **kwargs):
        replaced.append((cq, sender, kwargs))
        assert kwargs["delivery_guard"]()

    monkeypatch.setattr(bot, "SESSION", {"target": "device-1"})
    monkeypatch.setattr(bot, "devices", FakeDevices({"device-1": {"name": "Test PC", "os": "Windows 11"}}))
    monkeypatch.setattr(bot, "display_screenshot_collector", Collector())
    monkeypatch.setattr(bot, "publish_tracked", lambda command, **kwargs: published.append((command, kwargs)) or (True, "shot-1"))
    monkeypatch.setattr(bot, "_pending_device_input_still_allowed", lambda *_args: True)
    monkeypatch.setattr(bot, "_simple_command_still_allowed", lambda *_args: True)
    monkeypatch.setattr(bot, "_replace_callback_with_media", replace_media)
    monkeypatch.setattr(bot, "_replace_callback_message", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("unexpected fallback")))
    cq = SimpleNamespace(from_user=SimpleNamespace(id=bot.ADMIN_ID), answer=answer, message=SimpleNamespace(chat=SimpleNamespace(id=77), message_id=9))

    asyncio.run(bot.on_display_screenshot(cq, State()))

    assert published == [("display_screenshot", {"_target": "device-1", "display_id": "display-2", "topology_id": "a" * 16})]
    assert len(replaced) == 1
    assert replaced[0][2]["photo"].filename == "display-2.png"


def test_window_catalog_validator_requires_fresh_correlated_bounded_rows():
    import datetime

    now = datetime.datetime.now(datetime.timezone.utc)
    result = {
        "type": "window_catalog", "schema": "xider.window-catalog.v1", "feature_id": "D02-F02",
        "device_id": "device-1", "id": "list-1", "request_id": "list-1", "state": "success",
        "source": "windows_enum_windows", "checked_at": now.isoformat(),
        "data": {"catalog_id": "a" * 16, "windows": [
            {"id": "window-1", "title": "Editor", "app": "editor.exe", "width": 1280, "height": 720},
        ]},
    }
    parsed = bot._parse_window_catalog_result(
        result, expected_device_id="device-1", expected_request_id="list-1",
        expected_source="windows_enum_windows", now=now,
    )
    assert parsed["windows"][0]["id"] == "window-1"
    for field, value in (("device_id", "other"), ("request_id", "old"), ("source", "wrong")):
        invalid = {**result, field: value}
        try:
            bot._parse_window_catalog_result(invalid, expected_device_id="device-1", expected_request_id="list-1",
                                             expected_source="windows_enum_windows", now=now)
        except ValueError:
            pass
        else:
            raise AssertionError(f"forged {field} must be rejected")


def test_window_capture_is_owner_only_correlated_and_replaces_the_current_card(monkeypatch):
    import asyncio
    import base64
    import datetime
    import io
    from types import SimpleNamespace
    from PIL import Image

    output = io.BytesIO()
    Image.new("RGB", (40, 30), "purple").save(output, format="PNG")
    response = {
        "type": "window_screenshot", "schema": "xider.window-screenshot.v1", "feature_id": "D02-F02",
        "device_id": "device-1", "id": "capture-1", "request_id": "capture-1", "state": "success",
        "source": "windows_print_window", "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "data": {"id": "window-2", "title": "Private editor", "app": "editor.exe", "width": 40, "height": 30},
        "image": base64.b64encode(output.getvalue()).decode("ascii"),
    }

    class State:
        data = {"authorization_target": "device-1", "window_selected": "window-2",
                "window_catalog": {"catalog_id": "b" * 16, "source": "windows_enum_windows",
                                   "windows": [{"id": "window-2", "title": "Private editor", "app": "editor.exe", "width": 40, "height": 30}]}}
        async def get_data(self): return dict(self.data)
        async def get_state(self): return bot.Form.wait_window_selection.state
        async def clear(self): self.data = {}

    class Collector:
        async def wait_for(self, target, topic, timeout, request_id):
            assert (target, topic, request_id) == ("device-1", "window_screenshot", "capture-1")
            assert timeout <= 20
            return response

    published, replaced = [], []
    async def answer(*_args, **_kwargs): pass
    async def replace_message(*_args, **_kwargs): pass
    async def replace_media(cq, sender, **kwargs):
        replaced.append((cq, sender, kwargs))
        assert kwargs["delivery_guard"]()

    monkeypatch.setattr(bot, "SESSION", {"target": "device-1"})
    monkeypatch.setattr(bot, "devices", FakeDevices({"device-1": {"name": "Test PC", "os": "Windows 11", "commands": ["window_screenshot"]}}))
    monkeypatch.setattr(bot, "window_screenshot_collector", Collector())
    monkeypatch.setattr(bot, "publish_tracked", lambda command, **kwargs: published.append((command, kwargs)) or (True, "capture-1"))
    monkeypatch.setattr(bot, "_pending_device_input_still_allowed", lambda *_args: True)
    monkeypatch.setattr(bot, "_simple_command_still_allowed", lambda *_args: True)
    monkeypatch.setattr(bot, "_replace_callback_with_media", replace_media)
    monkeypatch.setattr(bot, "_replace_callback_message", replace_message)
    cq = SimpleNamespace(from_user=SimpleNamespace(id=bot.ADMIN_ID), answer=answer,
                         message=SimpleNamespace(chat=SimpleNamespace(id=77), message_id=9))

    asyncio.run(bot.on_window_capture(cq, State()))

    assert published == [("window_screenshot", {"_target": "device-1", "window_id": "window-2", "catalog_id": "b" * 16})]
    assert len(replaced) == 1
    assert replaced[0][2]["photo"].filename == "window-2.png"
    assert "Private editor" in replaced[0][2]["caption"]

    assert _expected_callback_gate("window:screenshot") == "OwnerFilter"
    assert _expected_callback_gate("window:capture") == "OwnerFilter"
def test_device_comparison_formats_only_reported_fields_and_marks_stale_capabilities_unknown(monkeypatch):
    now = 1_800_000_000
    left = {
        "name": "Mac <owner>", "os": "macOS 27.0", "version": "4.2.1",
        "guardian": {"installed": True, "version": "1.2.0", "version_source": "guardian"},
        "capabilities_seen": now - 60,
        "capabilities": {"commands": ["screenshot", "location"], "features": {"screen_capture": True}},
    }
    right = {
        "name": "Windows PC", "os": "Windows 11", "version": "4.1.0",
        "capabilities_seen": now - 901,
        "capabilities": {"commands": ["shell"], "features": {"screen_capture": False}},
    }

    rendered = bot.format_device_comparison("mac-1", left, "win-1", right, now=now)

    assert "Mac &lt;owner&gt;" in rendered
    assert "macOS 27.0" in rendered and "Windows 11" in rendered
    assert "Агент: 4.2.1" in rendered and "Агент: 4.1.0" in rendered
    assert "Guard Keeper: 1.2.0" in rendered
    assert "Возможности: 2 команды" in rendered
    assert "Возможности: неизвестно (отчёт устарел)" in rendered
    assert "shell" not in rendered


def test_device_comparison_marks_missing_values_unknown_and_bounds_reported_capabilities():
    huge = [f"cmd-{index}" for index in range(100)]
    rendered = bot.format_device_comparison(
        "a", {"name": "A", "capabilities_seen": time.time(), "capabilities": {"commands": huge}},
        "b", {"name": "B"},
    )

    assert "ОС: неизвестно" in rendered
    assert "Агент: неизвестно" in rendered
    assert "Guard Keeper: неизвестно" in rendered
    assert "Возможности: неизвестно" in rendered
    assert "cmd-7" in rendered and "cmd-8" not in rendered


def test_device_compare_entry_is_owner_only_and_uses_bounded_snapshot_index(monkeypatch, tmp_path):
    from device_store import DeviceStore

    monkeypatch.setattr(DeviceStore, "FILE", tmp_path / "devices.json", raising=False)
    monkeypatch.setattr(DeviceStore, "CATALOG_FILE", tmp_path / "catalog.json", raising=False)
    store = DeviceStore()
    store.upsert("mac-1", {"name": "Mac", "os": "macOS"})
    store.upsert("win-2", {"name": "PC", "os": "Windows"})
    monkeypatch.setattr(bot, "devices", store)
    monkeypatch.setattr(bot, "ADMIN_ID", 100)
    monkeypatch.setattr(bot, "get_user_role", lambda _user: bot.Role.OWNER)
    monkeypatch.setattr(bot.access_store, "get_effective_grants", lambda _user: {"callbacks": [], "devices": []})
    monkeypatch.setattr(bot.access_store, "can_use_callback", lambda *_args, **_kwargs: True)

    markup = bot.device_menu("mac-1", user_id=100)
    callbacks = _callback_data(markup)

    compare = [item for item in callbacks if item.startswith("catalog:compare:start:")]
    assert len(compare) == 1
    assert len(compare[0].encode("utf-8")) <= 64
    assert compare[0].endswith(":0")


def test_device_compare_entry_is_not_rendered_for_non_owner(monkeypatch, tmp_path):
    from device_store import DeviceStore

    monkeypatch.setattr(DeviceStore, "FILE", tmp_path / "devices.json", raising=False)
    monkeypatch.setattr(DeviceStore, "CATALOG_FILE", tmp_path / "catalog.json", raising=False)
    store = DeviceStore()
    store.upsert("mac-1", {"name": "Mac", "os": "macOS"})
    store.upsert("win-2", {"name": "PC", "os": "Windows"})
    monkeypatch.setattr(bot, "devices", store)
    monkeypatch.setattr(bot, "ADMIN_ID", 100)
    monkeypatch.setattr(bot, "get_user_role", lambda _user: bot.Role.USER)
    monkeypatch.setattr(bot.access_store, "get_effective_grants", lambda _user: {"callbacks": [], "devices": []})
    monkeypatch.setattr(bot.access_store, "can_use_callback", lambda *_args, **_kwargs: True)

    markup = bot.device_menu("mac-1", user_id=101)

    assert not any(value.startswith(("catalog:compare:", "catalog:completeness:", "catalog:registration:")) for value in _callback_data(markup))


def test_device_compare_start_rejects_stale_inventory_fingerprint(monkeypatch):
    import asyncio
    from types import SimpleNamespace

    monkeypatch.setattr(bot, "ADMIN_ID", 100)
    monkeypatch.setattr(bot, "get_user_role", lambda _user: bot.Role.OWNER)
    monkeypatch.setattr(bot, "_visible_device_items", lambda _user: {"a": {"name": "A"}, "b": {"name": "B"}})
    answers, rendered = [], []

    class Callback:
        from_user = SimpleNamespace(id=100)
        data = "catalog:compare:start:00000000:0"
        async def answer(self, text="", **_kwargs): answers.append(text)

    async def replace(*args, **kwargs): rendered.append((args, kwargs))
    monkeypatch.setattr(bot, "_replace_callback_message", replace)

    asyncio.run(bot.on_device_compare_start(Callback()))

    assert answers and not rendered


def test_device_compare_selection_edits_one_card_and_uses_current_inventory(monkeypatch, tmp_path):
    import asyncio
    from types import SimpleNamespace
    from device_store import DeviceStore

    monkeypatch.setattr(DeviceStore, "FILE", tmp_path / "devices.json", raising=False)
    monkeypatch.setattr(DeviceStore, "CATALOG_FILE", tmp_path / "catalog.json", raising=False)
    store = DeviceStore()
    store.upsert("mac-1", {"name": "MacBook", "os": "macOS 27", "version": "4.2.1"})
    store.upsert("win-2", {"name": "Desktop", "os": "Windows 11", "version": "4.1.0"})
    monkeypatch.setattr(bot, "devices", store)
    monkeypatch.setattr(bot, "ADMIN_ID", 100)
    monkeypatch.setattr(bot, "get_user_role", lambda _user: bot.Role.OWNER)
    monkeypatch.setattr(bot.access_store, "get_effective_grants", lambda _user: {"callbacks": [], "devices": []})
    monkeypatch.setattr(bot.access_store, "can_use_callback", lambda *_args, **_kwargs: True)
    rendered, answers = [], []

    class Callback:
        from_user = SimpleNamespace(id=100)
        data = ""
        async def answer(self, *args, **_kwargs): answers.append(args)

    async def replace(_source, text, reply_markup=None, **_kwargs):
        rendered.append((text, reply_markup))

    monkeypatch.setattr(bot, "_replace_callback_message", replace)
    rows, token = bot._device_compare_inventory(100)
    first_index = next(index for index, item in enumerate(rows) if item[0] == "mac-1")
    callback = Callback()
    callback.data = f"catalog:compare:start:{token}:{first_index}"

    async def scenario():
        await bot.on_device_compare_start(callback)
        second = next(value for value in _callback_data(rendered[-1][1]) if value.startswith("catalog:compare:second:"))
        assert len(second.encode("utf-8")) <= 64
        callback.data = second
        await bot.on_device_compare_select(callback)

    asyncio.run(scenario())

    assert len(rendered) == 2
    assert "MacBook" in rendered[-1][0] and "Desktop" in rendered[-1][0]
    assert "ОС: macOS 27" in rendered[-1][0] and "ОС: Windows 11" in rendered[-1][0]


def test_device_completeness_lists_missing_fields_and_truthful_observation_sources():
    now = 1_800_000_000
    report = bot.format_device_completeness("mac-1", {
        "name": "Mac <home>", "os": "macOS 27", "version": "4.2.1",
        "last_seen": now - 60,
        "capabilities": {"commands": ["screenshot"]}, "capabilities_seen": now - 120,
        "guardian": {"version": "1.2.0"}, "guardian_last_seen": now - 300,
    }, now=now)

    assert "Mac &lt;home&gt;" in report
    assert "Не заполнено: Hostname, Safari version" in report
    assert "Agent version · есть · heartbeat · 2027-01-15T07:59:00+00:00" in report
    assert "Capabilities · есть · agent report · 2027-01-15T07:58:00+00:00" in report
    assert "Safari version · нет · source not reported · unknown" in report


def test_device_completeness_does_not_invent_timestamps_or_treat_future_as_observed():
    report = bot.format_device_completeness("dev-1", {
        "name": "PC", "version": "4.0", "last_seen": 1_800_000_060,
        "capabilities": {"commands": []}, "capabilities_seen": "not-a-timestamp",
    }, now=1_800_000_000)

    assert "Device name · есть · heartbeat · unknown" in report
    assert "Agent version · есть · heartbeat · unknown" in report
    assert "Capabilities · есть · agent report · unknown" in report
    assert "2027-01-15" not in report


def test_device_completeness_entry_is_owner_only_and_edits_the_current_card(monkeypatch, tmp_path):
    import asyncio
    from types import SimpleNamespace
    from device_store import DeviceStore

    monkeypatch.setattr(DeviceStore, "FILE", tmp_path / "devices.json", raising=False)
    monkeypatch.setattr(DeviceStore, "CATALOG_FILE", tmp_path / "catalog.json", raising=False)
    store = DeviceStore()
    store.upsert("mac-1", {"name": "MacBook", "os": "macOS 27", "version": "4.2.1", "last_seen": 1_800_000_000})
    store.upsert("win-2", {"name": "Desktop", "os": "Windows 11", "version": "4.1.0", "last_seen": 1_800_000_000})
    monkeypatch.setattr(bot, "devices", store)
    monkeypatch.setattr(bot, "ADMIN_ID", 100)
    monkeypatch.setattr(bot, "get_user_role", lambda _user: bot.Role.OWNER)
    monkeypatch.setattr(bot.access_store, "get_effective_grants", lambda _user: {"callbacks": [], "devices": []})
    monkeypatch.setattr(bot.access_store, "can_use_callback", lambda *_args, **_kwargs: True)
    rendered, answers = [], []

    class Callback:
        from_user = SimpleNamespace(id=100)
        data = ""
        async def answer(self, *args, **_kwargs): answers.append(args)

    async def replace(_source, text, reply_markup=None, **_kwargs):
        rendered.append((text, reply_markup))

    monkeypatch.setattr(bot, "_replace_callback_message", replace)
    card = bot.device_menu("mac-1", user_id=100)
    callbacks = _callback_data(card)
    action = next((value for value in callbacks if value.startswith("catalog:completeness:")), None)
    assert action is not None, callbacks
    callback = Callback()
    callback.data = action

    asyncio.run(bot.on_device_completeness(callback))

    assert len(rendered) == 1
    assert "MacBook" in rendered[0][0] and "Agent version · есть" in rendered[0][0]
    assert len(action.encode("utf-8")) <= 64


def test_device_completeness_rejects_stale_inventory_fingerprint(monkeypatch):
    import asyncio
    from types import SimpleNamespace

    monkeypatch.setattr(bot, "ADMIN_ID", 100)
    monkeypatch.setattr(bot, "get_user_role", lambda _user: bot.Role.OWNER)
    monkeypatch.setattr(bot, "_visible_device_items", lambda _user: {"a": {"name": "A", "last_seen": 10}})
    answers, rendered = [], []

    class Callback:
        from_user = SimpleNamespace(id=100)
        data = "catalog:completeness:00000000:0"
        async def answer(self, text="", **_kwargs): answers.append(text)

    async def replace(*args, **kwargs): rendered.append((args, kwargs))
    monkeypatch.setattr(bot, "_replace_callback_message", replace)

    asyncio.run(bot.on_device_completeness(Callback()))

    assert answers and not rendered


def test_device_registration_origin_is_owner_only_and_edits_the_current_card(monkeypatch, tmp_path):
    import asyncio
    from types import SimpleNamespace
    from device_store import DeviceStore

    monkeypatch.setattr(DeviceStore, "FILE", tmp_path / "devices.json", raising=False)
    monkeypatch.setattr(DeviceStore, "CATALOG_FILE", tmp_path / "catalog.json", raising=False)
    store = DeviceStore()
    store.upsert("mac-1", {"name": "Mac <home>", "os": "macOS"}, registration_event="heartbeat", now=1_700_000_000)
    store.upsert("mac-1", {"online": True}, registration_event="re_registration", now=1_700_000_100)
    monkeypatch.setattr(bot, "devices", store)
    monkeypatch.setattr(bot, "ADMIN_ID", 100)
    monkeypatch.setattr(bot, "get_user_role", lambda _user: bot.Role.OWNER)
    monkeypatch.setattr(bot.access_store, "get_effective_grants", lambda _user: {"callbacks": [], "devices": []})
    monkeypatch.setattr(bot.access_store, "can_use_callback", lambda *_args, **_kwargs: True)
    rendered, answers = [], []

    class Callback:
        from_user = SimpleNamespace(id=100)
        data = ""
        async def answer(self, *args, **_kwargs): answers.append(args)

    async def replace(_source, text, reply_markup=None, **_kwargs):
        rendered.append((text, reply_markup))

    monkeypatch.setattr(bot, "_replace_callback_message", replace)
    card = bot.device_menu("mac-1", user_id=100)
    action = next(value for value in _callback_data(card) if value.startswith("catalog:registration:"))
    assert len(action.encode("utf-8")) <= 64
    callback = Callback()
    callback.data = action
    asyncio.run(bot.on_device_registration_origin(callback))

    assert len(rendered) == 1
    assert "Mac &lt;home&gt;" in rendered[0][0]
    assert "Источник первой учтённой регистрации: heartbeat агента" in rendered[0][0]
    assert "Наблюдаемых регистраций: 2" in rendered[0][0]
    assert "Последнее событие: повторная регистрация" in rendered[0][0]


def test_device_registration_origin_does_not_infer_legacy_source():
    report = bot.format_device_registration_origin("legacy-id", {"name": "Legacy"})
    assert "Источник первой учтённой регистрации: неизвестно" in report
    assert "Первое учтённое время: неизвестно" in report
    assert "Наблюдаемых регистраций: неизвестно" in report


def test_device_registration_origin_rejects_stale_inventory_fingerprint(monkeypatch):
    import asyncio
    from types import SimpleNamespace

    monkeypatch.setattr(bot, "ADMIN_ID", 100)
    monkeypatch.setattr(bot, "get_user_role", lambda _user: bot.Role.OWNER)
    monkeypatch.setattr(bot, "_visible_device_items", lambda _user: {"a": {"name": "A"}})
    answers, rendered = [], []

    class Callback:
        from_user = SimpleNamespace(id=100)
        data = "catalog:registration:00000000:0"
        async def answer(self, text="", **_kwargs): answers.append(text)

    async def replace(*args, **kwargs): rendered.append((args, kwargs))
    monkeypatch.setattr(bot, "_replace_callback_message", replace)

    asyncio.run(bot.on_device_registration_origin(Callback()))

    assert answers and not rendered


def test_device_inventory_wizard_is_owner_only_and_registered(monkeypatch, tmp_path):
    from device_store import DeviceStore

    monkeypatch.setattr(DeviceStore, "FILE", tmp_path / "devices.json", raising=False)
    monkeypatch.setattr(DeviceStore, "CATALOG_FILE", tmp_path / "catalog.json", raising=False)
    store = DeviceStore()
    store.upsert("mac-1", {"name": "Mac", "os": "macOS"})
    monkeypatch.setattr(bot, "devices", store)
    monkeypatch.setattr(bot, "ADMIN_ID", 100)
    monkeypatch.setattr(bot, "get_user_role", lambda user: bot.Role.OWNER if user == 100 else bot.Role.USER)
    monkeypatch.setattr(bot.access_store, "get_effective_grants", lambda _user: {"callbacks": [], "devices": []})
    monkeypatch.setattr(bot.access_store, "can_use_callback", lambda *_args, **_kwargs: True)

    owner_callbacks = _callback_data(bot.device_menu("mac-1", user_id=100))
    user_callbacks = _callback_data(bot.device_menu("mac-1", user_id=101))
    assert any(value.startswith("catalog:wizard:start:") for value in owner_callbacks)
    assert not any(value.startswith("catalog:wizard:") for value in user_callbacks)


def test_device_inventory_wizard_guides_tags_and_typed_fields_without_remote_actions(monkeypatch, tmp_path):
    import asyncio
    from types import SimpleNamespace
    from device_store import DeviceStore

    monkeypatch.setattr(DeviceStore, "FILE", tmp_path / "devices.json", raising=False)
    monkeypatch.setattr(DeviceStore, "CATALOG_FILE", tmp_path / "catalog.json", raising=False)
    store = DeviceStore()
    store.upsert("mac-1", {"name": "Mac", "os": "macOS"})
    store.define_typed_field(100, 100, "purpose", "enum", choices=["personal", "test"])
    store.define_typed_field(100, 100, "purchased", "date")
    store.define_typed_field(100, 100, "loaned", "boolean")
    monkeypatch.setattr(bot, "devices", store)
    monkeypatch.setattr(bot, "ADMIN_ID", 100)
    monkeypatch.setattr(bot, "get_user_role", lambda _user: bot.Role.OWNER)
    monkeypatch.setattr(bot.access_store, "get_effective_grants", lambda _user: {"callbacks": [], "devices": []})
    monkeypatch.setattr(bot.access_store, "can_use_callback", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(bot, "audit", lambda *_args, **_kwargs: None)
    remote_calls = []
    monkeypatch.setattr(bot.transport, "publish_command", lambda *args, **kwargs: remote_calls.append((args, kwargs)))
    rendered = []

    class State:
        value = None
        data = {}
        async def clear(self):
            self.value, self.data = None, {}
        async def update_data(self, **values):
            self.data.update(values)
        async def set_state(self, value):
            self.value = value.state
        async def get_data(self):
            return dict(self.data)
        async def get_state(self):
            return self.value

    class Callback:
        from_user = SimpleNamespace(id=100)
        message = SimpleNamespace(chat=SimpleNamespace(id=100), message_id=900)
        data = ""
        async def answer(self, *_args, **_kwargs):
            pass

    async def replace(_source, text, reply_markup=None, **_kwargs):
        rendered.append((text, reply_markup))

    async def replace_user(_message, text, reply_markup=None, **_kwargs):
        rendered.append((text, reply_markup))

    monkeypatch.setattr(bot, "_replace_callback_message", replace)
    monkeypatch.setattr(bot, "_replace_user_card", replace_user)
    state, callback = State(), Callback()
    entry = next(value for value in _callback_data(bot.device_menu("mac-1", user_id=100)) if value.startswith("catalog:wizard:start:"))
    callback.data = entry

    async def scenario():
        await bot.on_device_inventory_wizard_start(callback, state)
        tag_message = SimpleNamespace(text="home, travel", from_user=SimpleNamespace(id=100))
        await bot.on_device_inventory_wizard_tags(tag_message, state)
        await bot.on_device_inventory_wizard_value(SimpleNamespace(text="yes", from_user=SimpleNamespace(id=100)), state)
        await bot.on_device_inventory_wizard_value(SimpleNamespace(text="2025-08-12", from_user=SimpleNamespace(id=100)), state)
        await bot.on_device_inventory_wizard_value(SimpleNamespace(text="personal", from_user=SimpleNamespace(id=100)), state)

    asyncio.run(scenario())

    assert store.get_device_tags(100, 100, "mac-1") == ["home", "travel"]
    assert store.typed_fields(100, 100, "mac-1") == {"purpose": "personal", "purchased": "2025-08-12", "loaned": True}
    assert any("Инвентаризация завершена" in text for text, _markup in rendered)
    assert not remote_calls


def test_private_device_note_is_owner_only_and_audit_excludes_note_text(monkeypatch):
    import asyncio
    from types import SimpleNamespace

    _setup(monkeypatch, {"dev1": {"name": "Desk PC", "os": "Windows"}})
    roles = {100: bot.Role.OWNER, 200: bot.Role.USER}
    monkeypatch.setattr(bot, "ADMIN_ID", 100)
    monkeypatch.setattr(bot, "get_user_role", lambda user_id: roles[user_id])
    monkeypatch.setattr(bot.access_store, "get_effective_grants", lambda _user: {"callbacks": [], "devices": []})
    monkeypatch.setattr(bot.access_store, "can_use_callback", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(bot, "SESSION", {"target": "dev1"})

    owner_callbacks = _callback_data(bot.device_menu("dev1", 100))
    user_callbacks = _callback_data(bot.device_menu("dev1", 200))
    assert any(value.startswith("catalog:note:view:") for value in owner_callbacks)
    assert not any(value.startswith("catalog:note:") for value in user_callbacks)

    secret_note = "Secret locker code is 9876"
    saved, audit_calls, rendered = [], [], []
    monkeypatch.setattr(bot.devices, "set_private_device_note", lambda actor, owner, device, note: saved.append((actor, owner, device, note)) or True, raising=False)
    monkeypatch.setattr(bot.devices, "get_private_device_note", lambda _actor, _owner, _device: secret_note, raising=False)
    monkeypatch.setattr(bot, "audit", lambda *args, **kwargs: audit_calls.append((args, kwargs)))
    async def replace_user(*args, **kwargs):
        rendered.append((args, kwargs))
    monkeypatch.setattr(bot, "_replace_user_card", replace_user)

    class State:
        async def get_data(self):
            return {"private_note_device": "dev1", "private_note_token": "a1b2c3d4", "private_note_started_at": bot.time.time()}
        async def set_state(self, _value):
            pass
        async def clear(self):
            pass

    message = SimpleNamespace(from_user=SimpleNamespace(id=100), text=secret_note)
    asyncio.run(bot.on_device_private_note_input(message, State()))

    assert saved == [(100, 100, "dev1", secret_note)]
    assert audit_calls == [(("device_private_note_updated",), {"actor_id": 100, "device_id": "dev1"})]
    assert secret_note not in repr(audit_calls)
    assert len(rendered) == 1 and secret_note in rendered[0][0][1]


def test_manual_device_place_label_is_owner_only_and_explicitly_not_gps(monkeypatch):
    import asyncio
    from types import SimpleNamespace

    _setup(monkeypatch, {"dev1": {"name": "Desk PC", "os": "Windows"}})
    roles = {100: bot.Role.OWNER, 200: bot.Role.USER}
    monkeypatch.setattr(bot, "ADMIN_ID", 100)
    monkeypatch.setattr(bot, "get_user_role", lambda user_id: roles[user_id])
    monkeypatch.setattr(bot.access_store, "get_effective_grants", lambda _user: {"callbacks": [], "devices": []})
    monkeypatch.setattr(bot.access_store, "can_use_callback", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(bot.devices, "get_device_place_label", lambda *_args: "Office", raising=False)

    owner_callbacks = _callback_data(bot.device_menu("dev1", 100))
    user_callbacks = _callback_data(bot.device_menu("dev1", 200))
    assert any(value.startswith("catalog:place:edit:") for value in owner_callbacks)
    assert not any(value.startswith("catalog:place:") for value in user_callbacks)

    label = "Home office"
    saved, audit_calls, rendered, remote_calls = [], [], [], []
    monkeypatch.setattr(bot.devices, "set_device_place_label", lambda actor, owner, device, value: saved.append((actor, owner, device, value)) or True, raising=False)
    monkeypatch.setattr(bot.devices, "get_device_place_label", lambda _actor, _owner, _device: label, raising=False)
    monkeypatch.setattr(bot, "audit", lambda *args, **kwargs: audit_calls.append((args, kwargs)))
    monkeypatch.setattr(bot.transport, "publish_command", lambda *args, **kwargs: remote_calls.append((args, kwargs)))

    async def replace_user(*args, **kwargs):
        rendered.append((args, kwargs))
    monkeypatch.setattr(bot, "_replace_user_card", replace_user)

    class State:
        async def get_data(self):
            return {"place_label_device": "dev1", "place_label_token": "a1b2c3d4", "place_label_started_at": bot.time.time()}
        async def clear(self):
            pass

    message = SimpleNamespace(from_user=SimpleNamespace(id=100), text=label)
    asyncio.run(bot.on_device_place_label_input(message, State()))

    assert saved == [(100, 100, "dev1", label)]
    assert audit_calls == [(("device_manual_place_label_updated",), {"actor_id": 100, "device_id": "dev1"})]
    assert label not in repr(audit_calls)
    assert remote_calls == []
    assert len(rendered) == 1 and "не GPS-координаты" in rendered[0][0][1]


def test_device_purpose_entry_is_owner_only_and_distinct_from_tags(monkeypatch):
    _setup(monkeypatch, {"dev1": {"name": "Desk PC", "os": "Windows"}})
    roles = {100: bot.Role.OWNER, 200: bot.Role.USER}
    monkeypatch.setattr(bot, "ADMIN_ID", 100)
    monkeypatch.setattr(bot, "get_user_role", lambda user_id: roles[user_id])
    monkeypatch.setattr(bot.access_store, "get_effective_grants", lambda _user: {"callbacks": [], "devices": []})
    monkeypatch.setattr(bot.access_store, "can_use_callback", lambda user_id, *_args, **_kwargs: user_id == 100)
    monkeypatch.setattr(bot.devices, "get_device_purpose", lambda *_args: None, raising=False)
    monkeypatch.setattr(bot, "_device_compare_inventory", lambda _user: ([("dev1", {"name": "Desk PC"})], "aabbccdd"))

    owner_callbacks = _callback_data(bot.device_menu("dev1", 100))
    user_callbacks = _callback_data(bot.device_menu("dev1", 200))

    assert "catalog:purpose:view:aabbccdd:0" in owner_callbacks
    assert not any(value.startswith("catalog:purpose:") for value in user_callbacks)


def test_device_review_date_entry_is_owner_only_and_shows_saved_date(monkeypatch):
    _setup(monkeypatch, {"dev1": {"name": "Desk PC", "os": "Windows"}})
    roles = {100: bot.Role.OWNER, 200: bot.Role.USER}
    monkeypatch.setattr(bot, "ADMIN_ID", 100)
    monkeypatch.setattr(bot, "get_user_role", lambda user_id: roles[user_id])
    monkeypatch.setattr(bot.access_store, "get_effective_grants", lambda _user: {"callbacks": [], "devices": []})
    monkeypatch.setattr(bot.access_store, "can_use_callback", lambda user_id, *_args, **_kwargs: user_id == 100)
    monkeypatch.setattr(bot, "_device_compare_inventory", lambda _user: ([('dev1', {"name": "Desk PC"})], "aabbccdd"))
    monkeypatch.setattr(bot.devices, "get_device_review_date", lambda *_args: "2027-01-31", raising=False)

    owner_markup = bot.device_menu("dev1", 100)
    owner_callbacks = _callback_data(owner_markup)
    user_callbacks = _callback_data(bot.device_menu("dev1", 200))

    assert "catalog:review:edit:aabbccdd:0" in owner_callbacks
    review_button = next(
        button for row in owner_markup.inline_keyboard for button in row
        if button.callback_data == "catalog:review:edit:aabbccdd:0"
    )
    assert "2027-01-31" in review_button.text
    assert not any(value.startswith("catalog:review:") for value in user_callbacks)


def test_device_maintenance_entry_is_owner_only_and_shows_expiry(monkeypatch):
    _setup(monkeypatch, {"dev1": {"name": "Desk PC", "os": "Windows"}})
    roles = {100: bot.Role.OWNER, 200: bot.Role.USER}
    monkeypatch.setattr(bot, "ADMIN_ID", 100)
    monkeypatch.setattr(bot, "get_user_role", lambda user_id: roles[user_id])
    monkeypatch.setattr(bot.access_store, "get_effective_grants", lambda _user: {"callbacks": [], "devices": []})
    monkeypatch.setattr(bot.access_store, "can_use_callback", lambda user_id, *_args, **_kwargs: user_id == 100)
    monkeypatch.setattr(bot, "_device_compare_inventory", lambda _user: ([('dev1', {"name": "Desk PC"})], "aabbccdd"))
    monkeypatch.setattr(bot.devices, "get_device_maintenance", lambda *_args, **_kwargs: {
        "until_date": "2027-01-31", "exclude_inactivity": True, "active": True,
    }, raising=False)

    owner_markup = bot.device_menu("dev1", 100)
    owner_callbacks = _callback_data(owner_markup)
    user_callbacks = _callback_data(bot.device_menu("dev1", 200))

    assert "catalog:maintenance:edit:aabbccdd:0" in owner_callbacks
    button = next(button for row in owner_markup.inline_keyboard for button in row
                  if button.callback_data == "catalog:maintenance:edit:aabbccdd:0")
    assert "2027-01-31" in button.text
    assert not any(value.startswith("catalog:maintenance:") for value in user_callbacks)


def test_device_relations_entry_is_owner_only_and_shows_link_count(monkeypatch):
    _setup(monkeypatch, {"dev1": {"name": "Desk PC", "os": "Windows"}})
    roles = {100: bot.Role.OWNER, 200: bot.Role.USER}
    monkeypatch.setattr(bot, "ADMIN_ID", 100)
    monkeypatch.setattr(bot, "get_user_role", lambda user_id: roles[user_id])
    monkeypatch.setattr(bot.access_store, "get_effective_grants", lambda _user: {"callbacks": [], "devices": []})
    monkeypatch.setattr(bot.access_store, "can_use_callback", lambda user_id, *_args, **_kwargs: user_id == 100)
    monkeypatch.setattr(bot, "_device_compare_inventory", lambda _user: ([('dev1', {"name": "Desk PC"}), ('dev2', {"name": "Test Mac"})], "aabbccdd"))
    monkeypatch.setattr(bot.devices, "get_device_relations", lambda actor, owner, _device: ["dev2"] if actor == 100 else None, raising=False)

    owner_markup = bot.device_menu("dev1", 100)
    owner_callbacks = _callback_data(owner_markup)
    user_callbacks = _callback_data(bot.device_menu("dev1", 200))
    relation_button = next(button for row in owner_markup.inline_keyboard for button in row
                           if button.callback_data.startswith("catalog:link:manage:"))

    assert relation_button.callback_data == "catalog:link:manage:aabbccdd:0"
    assert "1" in relation_button.text
    assert not any(value.startswith("catalog:link:") for value in user_callbacks)


def test_device_relation_input_links_only_a_visible_peer_without_remote_command(monkeypatch):
    import asyncio
    from types import SimpleNamespace

    _setup(monkeypatch, {"dev1": {"name": "Desk PC", "os": "Windows"}, "dev2": {"name": "Test Mac", "os": "macOS"}})
    monkeypatch.setattr(bot, "ADMIN_ID", 100)
    monkeypatch.setattr(bot, "get_user_role", lambda _user: bot.Role.OWNER)
    monkeypatch.setattr(bot, "_device_compare_inventory", lambda _user: ([
        ("dev1", {"name": "Desk PC"}), ("dev2", {"name": "Test Mac"}),
    ], "aabbccdd"))
    saved, audits, rendered, remote, deleted = [], [], [], [], []
    monkeypatch.setattr(bot.devices, "set_device_relation", lambda actor, owner, left, right, linked: saved.append((actor, owner, left, right, linked)) or True, raising=False)
    monkeypatch.setattr(bot.devices, "get_device_relations", lambda *_args: ["dev2"], raising=False)
    monkeypatch.setattr(bot, "audit", lambda *args, **kwargs: audits.append((args, kwargs)))
    monkeypatch.setattr(bot.transport, "publish_command", lambda *args, **kwargs: remote.append((args, kwargs)))

    async def replace_user(*args, **kwargs):
        rendered.append((args, kwargs))

    monkeypatch.setattr(bot, "_replace_user_card", replace_user)

    class State:
        async def get_data(self):
            return {"device_relation_device": "dev1", "device_relation_token": "aabbccdd", "device_relation_started_at": bot.time.time()}
        async def clear(self):
            pass

    async def delete_message():
        deleted.append(True)

    message = SimpleNamespace(from_user=SimpleNamespace(id=100), text="dev2", delete=delete_message)
    asyncio.run(bot.on_device_relation_input(message, State()))

    assert saved == [(100, 100, "dev1", "dev2", True)]
    assert audits == [(("device_relation_linked",), {"actor_id": 100, "device_a": "dev1", "device_b": "dev2"})]
    assert remote == []
    assert deleted == [True]
    assert len(rendered) == 1 and "Desk PC" in rendered[0][0][1] and "dev2" in rendered[0][0][1]


def test_device_relation_input_rejects_self_and_unknown_ids(monkeypatch):
    import asyncio
    from types import SimpleNamespace

    _setup(monkeypatch, {"dev1": {"name": "Desk PC", "os": "Windows"}})
    monkeypatch.setattr(bot, "ADMIN_ID", 100)
    monkeypatch.setattr(bot, "get_user_role", lambda _user: bot.Role.OWNER)
    monkeypatch.setattr(bot, "_device_compare_inventory", lambda _user: ([("dev1", {"name": "Desk PC"})], "aabbccdd"))
    saved, rendered = [], []
    monkeypatch.setattr(bot.devices, "set_device_relation", lambda *args: saved.append(args) or True, raising=False)
    monkeypatch.setattr(bot.devices, "get_device_relations", lambda *_args: [], raising=False)

    async def replace_user(*args, **kwargs):
        rendered.append((args, kwargs))

    monkeypatch.setattr(bot, "_replace_user_card", replace_user)

    class State:
        async def get_data(self):
            return {"device_relation_device": "dev1", "device_relation_token": "aabbccdd", "device_relation_started_at": bot.time.time()}
        async def clear(self):
            pass
        async def set_state(self, _state):
            pass

    message = SimpleNamespace(from_user=SimpleNamespace(id=100), text="not-visible")
    asyncio.run(bot.on_device_relation_input(message, State()))

    assert saved == []
    assert len(rendered) == 1 and "не найден" in rendered[0][0][1].lower()


def test_device_relation_unlink_is_owner_local_and_replaces_the_current_card(monkeypatch):
    import asyncio
    from types import SimpleNamespace

    _setup(monkeypatch, {"dev1": {"name": "Desk PC", "os": "Windows"}, "dev2": {"name": "Test Mac", "os": "macOS"}})
    monkeypatch.setattr(bot, "ADMIN_ID", 100)
    monkeypatch.setattr(bot, "get_user_role", lambda _user: bot.Role.OWNER)
    monkeypatch.setattr(bot, "_device_compare_inventory", lambda _user: ([
        ("dev1", {"name": "Desk PC"}), ("dev2", {"name": "Test Mac"}),
    ], "aabbccdd"))
    monkeypatch.setattr(bot.devices, "get_device_relations", lambda *_args: ["dev2"], raising=False)
    changed, audits, rendered, remote = [], [], [], []

    def set_relation(actor, owner, left, right, linked):
        changed.append((actor, owner, left, right, linked))
        return True

    monkeypatch.setattr(bot.devices, "set_device_relation", set_relation, raising=False)
    monkeypatch.setattr(bot, "audit", lambda *args, **kwargs: audits.append((args, kwargs)))
    monkeypatch.setattr(bot.transport, "publish_command", lambda *args, **kwargs: remote.append((args, kwargs)))

    async def replace(cq, text, reply_markup=None, **kwargs):
        rendered.append((text, reply_markup))

    monkeypatch.setattr(bot, "_replace_callback_message", replace)
    monkeypatch.setattr(bot, "device_relations_keyboard", lambda *_args: "relation keyboard")

    class State:
        async def clear(self):
            pass

    async def answer(*_args, **_kwargs):
        pass

    cq = SimpleNamespace(
        data="catalog:link:unlink:aabbccdd:0:1",
        from_user=SimpleNamespace(id=100),
        answer=answer,
    )
    asyncio.run(bot.on_device_relation_unlink(cq, State()))

    assert changed == [(100, 100, "dev1", "dev2", False)]
    assert audits == [(("device_relation_unlinked",), {"actor_id": 100, "device_a": "dev1", "device_b": "dev2"})]
    assert remote == []
    assert len(rendered) == 1 and "Desk PC" in rendered[0][0] and "dev2" in rendered[0][0]


def test_device_maintenance_input_sets_owner_metadata_without_remote_command(monkeypatch):
    import asyncio
    from types import SimpleNamespace

    _setup(monkeypatch, {"dev1": {"name": "Desk PC", "os": "Windows"}})
    monkeypatch.setattr(bot, "ADMIN_ID", 100)
    monkeypatch.setattr(bot, "get_user_role", lambda _user: bot.Role.OWNER)
    saved, audits, rendered, remote = [], [], [], []
    monkeypatch.setattr(bot.devices, "set_device_maintenance", lambda actor, owner, device, until, **kwargs: saved.append((actor, owner, device, until, kwargs)) or True, raising=False)
    monkeypatch.setattr(bot.devices, "get_device_maintenance", lambda *_args, **_kwargs: {
        "until_date": "2027-01-31", "exclude_inactivity": True, "active": True,
    }, raising=False)
    monkeypatch.setattr(bot, "audit", lambda *args, **kwargs: audits.append((args, kwargs)))
    monkeypatch.setattr(bot.transport, "publish_command", lambda *args, **kwargs: remote.append((args, kwargs)))

    async def replace_user(*args, **kwargs):
        rendered.append((args, kwargs))

    monkeypatch.setattr(bot, "_replace_user_card", replace_user)

    class State:
        async def get_data(self):
            return {"maintenance_device": "dev1", "maintenance_token": "a1b2c3d4", "maintenance_started_at": bot.time.time()}
        async def clear(self):
            pass

    message = SimpleNamespace(from_user=SimpleNamespace(id=100), text="2027-01-31")
    asyncio.run(bot.on_device_maintenance_input(message, State()))

    assert saved == [(100, 100, "dev1", "2027-01-31", {"exclude_inactivity": True})]
    assert audits == [(("device_maintenance_updated",), {"actor_id": 100, "device_id": "dev1"})]
    assert remote == []
    assert len(rendered) == 1 and "не останавливает агента" in rendered[0][0][1]


def test_device_review_date_input_saves_owner_metadata_without_remote_command(monkeypatch):
    import asyncio
    from types import SimpleNamespace

    _setup(monkeypatch, {"dev1": {"name": "Desk PC", "os": "Windows"}})
    monkeypatch.setattr(bot, "ADMIN_ID", 100)
    monkeypatch.setattr(bot, "get_user_role", lambda _user: bot.Role.OWNER)
    saved, audits, rendered, remote = [], [], [], []
    monkeypatch.setattr(bot.devices, "set_device_review_date", lambda actor, owner, device, value: saved.append((actor, owner, device, value)) or True, raising=False)
    monkeypatch.setattr(bot.devices, "get_device_review_date", lambda *_args: "2027-01-31", raising=False)
    monkeypatch.setattr(bot, "audit", lambda *args, **kwargs: audits.append((args, kwargs)))
    monkeypatch.setattr(bot.transport, "publish_command", lambda *args, **kwargs: remote.append((args, kwargs)))

    async def replace_user(*args, **kwargs):
        rendered.append((args, kwargs))

    monkeypatch.setattr(bot, "_replace_user_card", replace_user)

    class State:
        async def get_data(self):
            return {"review_date_device": "dev1", "review_date_token": "a1b2c3d4", "review_date_started_at": bot.time.time()}
        async def clear(self):
            pass

    message = SimpleNamespace(from_user=SimpleNamespace(id=100), text="2027-01-31")
    asyncio.run(bot.on_device_review_date_input(message, State()))

    assert saved == [(100, 100, "dev1", "2027-01-31")]
    assert audits == [(("device_manual_review_date_updated",), {"actor_id": 100, "device_id": "dev1"})]
    assert remote == []
    assert len(rendered) == 1 and "фоновая проверка" in rendered[0][0][1]


def test_device_purpose_callback_sets_only_allowlisted_classification(monkeypatch):
    import asyncio
    from types import SimpleNamespace

    _setup(monkeypatch, {"dev1": {"name": "Desk PC", "os": "Windows"}})
    monkeypatch.setattr(bot, "ADMIN_ID", 100)
    monkeypatch.setattr(bot, "get_user_role", lambda _user: bot.Role.OWNER)
    monkeypatch.setattr(bot, "_device_compare_inventory", lambda _user: ([ ("dev1", {"name": "Desk PC"}) ], "aabbccdd"))
    saved, audit_calls, rendered, remote_calls = [], [], [], []
    monkeypatch.setattr(
        bot.devices, "set_device_purpose",
        lambda actor, owner, device, purpose: saved.append((actor, owner, device, purpose)) or True,
        raising=False,
    )
    monkeypatch.setattr(bot.devices, "get_device_purpose", lambda *_args: "work", raising=False)
    monkeypatch.setattr(bot, "audit", lambda *args, **kwargs: audit_calls.append((args, kwargs)))
    monkeypatch.setattr(bot.transport, "publish_command", lambda *args, **kwargs: remote_calls.append((args, kwargs)))

    async def replace(*args, **kwargs):
        rendered.append((args, kwargs))

    monkeypatch.setattr(bot, "_replace_callback_message", replace)

    class Callback:
        data = "catalog:purpose:set:work:aabbccdd:0"
        from_user = SimpleNamespace(id=100)
        async def answer(self, *_args, **_kwargs):
            pass

    asyncio.run(bot.on_device_purpose_set(Callback()))

    assert saved == [(100, 100, "dev1", "work")]
    assert audit_calls == [(("device_purpose_updated",), {"actor_id": 100, "device_id": "dev1", "purpose": "work"})]
    assert remote_calls == []
    assert len(rendered) == 1 and "Рабочее" in rendered[0][0][1]


def test_named_permission_profiles_are_owner_only_and_reuse_the_confirmed_grant_flow(monkeypatch, tmp_path):
    import asyncio
    from types import SimpleNamespace

    Callback, rendered = _admin_fixture(monkeypatch, tmp_path)
    monkeypatch.setattr(bot, "get_user_role", lambda user_id: bot.Role.OWNER if int(user_id) == 100 else bot.access_store.get_role(int(user_id), 100))
    assert bot.access_store.set_role(100, 200, bot.Role.USER, 100)
    bot.access_store.register_start(SimpleNamespace(id=300, username="target", first_name="Target", last_name=""))
    assert bot.access_store.set_role(100, 300, bot.Role.USER, 100)
    assert bot.access_store.toggle_device(100, 200, "a", 100)
    assert bot.access_store.toggle_callback(100, 200, "cmd:screenshot", 100)

    owner_menu = bot.admin_user_menu(200, actor_id=100)
    non_owner_menu = bot.admin_user_menu(200, actor_id=200)
    assert "admin:profiles:200" in _callback_data(owner_menu)
    assert not any(value.startswith("admin:profiles:") for value in _callback_data(non_owner_menu))

    class State:
        value = None
        data = {}
        async def set_state(self, value):
            self.value = value
        async def update_data(self, **values):
            self.data.update(values)
        async def get_data(self):
            return dict(self.data)
        async def clear(self):
            self.value = None
            self.data = {}

    state = State()
    profile_id = None

    async def scenario():
        nonlocal profile_id
        await bot.on_admin_permission_profiles(Callback("admin:profiles:200"))
        assert "admin:profile:save:200" in _callback_data(rendered[-1][1])
        await bot.on_admin_permission_profile_save(Callback("admin:profile:save:200"), state)
        assert state.value == bot.Form.wait_admin_profile_name
        message = SimpleNamespace(from_user=SimpleNamespace(id=100), text="Support laptop")
        await bot.on_admin_permission_profile_name(message, state)
        profile = bot.access_store.list_permission_profiles(100, 100)[0]
        profile_id = profile["profile_id"]
        assert profile["callbacks"] == ["cmd:screenshot"]
        assert profile["devices"] == ["a"]

        await bot.on_admin_permission_profile_apply(
            Callback(f"admin:profile:apply:300:{profile_id}")
        )
        assert bot.access_store.get_effective_grants(300) == {"callbacks": [], "devices": []}
        assert "cmd:screenshot" in rendered[-1][0]
        confirmation = next(
            button.callback_data for row in rendered[-1][1].inline_keyboard for button in row
            if button.callback_data.startswith("admin:confirm:")
        )
        await bot.on_admin_confirm(Callback(confirmation))

    asyncio.run(scenario())
    assert bot.access_store.get_effective_grants(300) == {
        "callbacks": ["cmd:screenshot"], "devices": ["a"],
    }


def test_inventory_template_apply_is_owner_only_and_uses_preview_confirmation(monkeypatch):
    import asyncio
    from types import SimpleNamespace

    _setup(monkeypatch, {"dev1": {"name": "New laptop", "os": "macOS", "version": "9.1"}})
    monkeypatch.setattr(bot, "ADMIN_ID", 100)
    monkeypatch.setattr(bot, "get_user_role", lambda _user: bot.Role.OWNER)
    monkeypatch.setattr(bot, "_device_compare_inventory", lambda _user: ([("dev1", {"name": "New laptop", "os": "macOS", "version": "9.1"})], "aabbccdd"))
    templates = [{
        "template_id": "a" * 32, "owner_id": 100, "name": "Lab Mac",
        "tags": ["lab", "loaner"], "purpose": "test",
        "typed_values": {"team": "QA"}, "created_at": 100,
    }]
    applied, audit_calls, rendered, remote_calls = [], [], [], []
    monkeypatch.setattr(bot.devices, "list_device_inventory_templates", lambda *_args: templates, raising=False)
    monkeypatch.setattr(
        bot.devices, "apply_device_inventory_template",
        lambda actor, owner, template_id, destination: applied.append((actor, owner, template_id, destination)) or True,
        raising=False,
    )
    monkeypatch.setattr(bot.devices, "get_device_tags", lambda *_args: [], raising=False)
    monkeypatch.setattr(bot.devices, "get_device_purpose", lambda *_args: None, raising=False)
    monkeypatch.setattr(bot.devices, "typed_fields", lambda *_args: {}, raising=False)
    monkeypatch.setattr(bot, "audit", lambda *args, **kwargs: audit_calls.append((args, kwargs)))
    monkeypatch.setattr(bot.transport, "publish_command", lambda *args, **kwargs: remote_calls.append((args, kwargs)))

    async def replace(*args, **kwargs):
        rendered.append((args, kwargs))

    monkeypatch.setattr(bot, "_replace_callback_message", replace)

    class Callback:
        from_user = SimpleNamespace(id=100)
        async def answer(self, *_args, **_kwargs):
            pass

    start = Callback()
    start.data = "catalog:template:apply:aabbccdd:0"
    asyncio.run(bot.on_device_inventory_template_apply(start))
    select_markup = rendered[-1][1]["reply_markup"]
    select_data = _callback_data(select_markup)[0]
    assert "Lab Mac" in rendered[-1][0][1]
    assert "catalog:template:preview:" in select_data

    preview = Callback()
    preview.data = select_data
    asyncio.run(bot.on_device_inventory_template_preview(preview))
    confirm_data = next(value for value in _callback_data(rendered[-1][1]["reply_markup"]) if value.startswith("catalog:template:confirm:"))
    assert "lab" in rendered[-1][0][1] and "QA" in rendered[-1][0][1]

    confirm = Callback()
    confirm.data = confirm_data
    asyncio.run(bot.on_device_inventory_template_confirm(confirm))
    assert applied == [(100, 100, "a" * 32, "dev1")]
    assert audit_calls and audit_calls[-1][0] == ("device_inventory_template_applied",)
    assert remote_calls == []


def test_inventory_template_actions_are_only_shown_to_owner(monkeypatch):
    _setup(monkeypatch, {"dev1": {"name": "Desk PC", "os": "Windows"}})
    roles = {100: bot.Role.OWNER, 200: bot.Role.USER}
    monkeypatch.setattr(bot, "ADMIN_ID", 100)
    monkeypatch.setattr(bot, "get_user_role", lambda user_id: roles[user_id])
    monkeypatch.setattr(bot.access_store, "get_effective_grants", lambda _user: {"callbacks": [], "devices": []})
    monkeypatch.setattr(bot.access_store, "can_use_callback", lambda user_id, *_args, **_kwargs: user_id == 100)
    monkeypatch.setattr(bot, "_device_compare_inventory", lambda _user: ([("dev1", {"name": "Desk PC"})], "aabbccdd"))
    monkeypatch.setattr(bot.devices, "get_device_tags", lambda *_args: [], raising=False)
    monkeypatch.setattr(bot.devices, "get_device_purpose", lambda *_args: None, raising=False)
    monkeypatch.setattr(bot.devices, "typed_fields", lambda *_args: {}, raising=False)

    owner_callbacks = _callback_data(bot.device_menu("dev1", 100))
    user_callbacks = _callback_data(bot.device_menu("dev1", 200))

    assert "catalog:template:save:aabbccdd:0" in owner_callbacks
    assert "catalog:template:apply:aabbccdd:0" in owner_callbacks
    assert not any(value.startswith("catalog:template:") for value in user_callbacks)


def test_inventory_template_preview_rejects_template_catalog_change(monkeypatch):
    import asyncio
    from types import SimpleNamespace

    _setup(monkeypatch, {"dev1": {"name": "New laptop", "os": "Windows"}})
    monkeypatch.setattr(bot, "ADMIN_ID", 100)
    monkeypatch.setattr(bot, "get_user_role", lambda _user: bot.Role.OWNER)
    monkeypatch.setattr(bot, "_device_compare_inventory", lambda _user: ([("dev1", {"name": "New laptop"})], "aabbccdd"))
    templates = [{"template_id": "b" * 32, "owner_id": 100, "name": "First", "tags": ["lab"], "purpose": None, "typed_values": {}, "created_at": 100}]
    monkeypatch.setattr(bot.devices, "list_device_inventory_templates", lambda *_args: templates, raising=False)
    monkeypatch.setattr(bot.devices, "get_device_tags", lambda *_args: [], raising=False)
    monkeypatch.setattr(bot.devices, "get_device_purpose", lambda *_args: None, raising=False)
    monkeypatch.setattr(bot.devices, "typed_fields", lambda *_args: {}, raising=False)
    applied, rendered, answers = [], [], []
    monkeypatch.setattr(bot.devices, "apply_device_inventory_template", lambda *args: applied.append(args) or True, raising=False)

    async def replace(*args, **kwargs):
        rendered.append((args, kwargs))

    monkeypatch.setattr(bot, "_replace_callback_message", replace)

    class Callback:
        from_user = SimpleNamespace(id=100)
        async def answer(self, *args, **kwargs):
            answers.append((args, kwargs))

    start = Callback()
    start.data = "catalog:template:apply:aabbccdd:0"
    asyncio.run(bot.on_device_inventory_template_apply(start))
    preview_data = next(value for value in _callback_data(rendered[-1][1]["reply_markup"]) if value.startswith("catalog:template:preview:"))
    templates[0]["name"] = "Changed after list"
    preview = Callback()
    preview.data = preview_data
    asyncio.run(bot.on_device_inventory_template_preview(preview))

    assert rendered[-1][0][1].startswith("<b>🧩 Шаблоны инвентаря")
    assert answers[-1][1].get("show_alert") is True
    assert applied == []


def test_inventory_template_save_flow_stores_owner_metadata_without_remote_command(monkeypatch):
    import asyncio
    from types import SimpleNamespace

    _setup(monkeypatch, {"dev1": {"name": "Lab PC", "os": "Windows"}})
    monkeypatch.setattr(bot, "ADMIN_ID", 100)
    monkeypatch.setattr(bot, "get_user_role", lambda _user: bot.Role.OWNER)
    monkeypatch.setattr(bot, "_device_compare_inventory", lambda _user: ([("dev1", {"name": "Lab PC"})], "aabbccdd"))

    class State:
        def __init__(self):
            self.data = {}
            self.value = None
        async def clear(self):
            self.data.clear()
            self.value = None
        async def update_data(self, **kwargs):
            self.data.update(kwargs)
        async def set_state(self, value):
            self.value = value
        async def get_data(self):
            return dict(self.data)

    state = State()
    rendered, created, audit_calls, remote_calls = [], [], [], []
    monkeypatch.setattr(
        bot.devices, "create_device_inventory_template",
        lambda actor, owner, name, source: created.append((actor, owner, name, source)) or {
            "template_id": "d" * 32, "name": name,
        }, raising=False,
    )
    monkeypatch.setattr(bot, "audit", lambda *args, **kwargs: audit_calls.append((args, kwargs)))
    monkeypatch.setattr(bot.transport, "publish_command", lambda *args, **kwargs: remote_calls.append((args, kwargs)))
    monkeypatch.setattr(bot, "device_menu", lambda *_args: "device-menu")

    async def replace(*args, **kwargs):
        rendered.append((args, kwargs))

    monkeypatch.setattr(bot, "_replace_callback_message", replace)
    monkeypatch.setattr(bot, "_replace_user_card", replace)

    class Callback:
        data = "catalog:template:save:aabbccdd:0"
        from_user = SimpleNamespace(id=100)
        async def answer(self, *_args, **_kwargs):
            pass

    class Message:
        from_user = SimpleNamespace(id=100)
        text = "QA loaner profile"

    asyncio.run(bot.on_device_inventory_template_save(Callback(), state))
    assert state.value == bot.Form.wait_device_inventory_template_name
    assert any(value.startswith("catalog:template:save-cancel:") for value in _callback_data(rendered[-1][1]["reply_markup"]))

    asyncio.run(bot.on_device_inventory_template_name(Message(), state))

    assert created == [(100, 100, "QA loaner profile", "dev1")]
    assert audit_calls == [(("device_inventory_template_created",), {"actor_id": 100, "device_id": "dev1", "template_id": "d" * 32})]
    assert "QA loaner profile" not in repr(audit_calls)
    assert remote_calls == []
    assert state.data == {} and state.value is None


def test_duplicate_candidate_is_owner_only_and_opens_read_only_comparison(monkeypatch):
    import asyncio
    from types import SimpleNamespace

    devices = {
        "device-a": {"name": "Office PC", "hostname": "build-07", "os": "Windows 11", "version": "4.2.1"},
        "device-b": {"name": "Copied install", "hostname": "build-07", "os": "Windows 10", "version": "4.2.1"},
    }
    _setup(monkeypatch, devices)
    roles = {100: bot.Role.OWNER, 200: bot.Role.USER}
    monkeypatch.setattr(bot, "ADMIN_ID", 100)
    monkeypatch.setattr(bot, "get_user_role", lambda user_id: roles[user_id])
    monkeypatch.setattr(bot.access_store, "get_effective_grants", lambda _user: {"callbacks": [], "devices": []})
    monkeypatch.setattr(bot.access_store, "can_use_callback", lambda user_id, *_args, **_kwargs: user_id == 100)
    rows = [(key, value) for key, value in sorted(devices.items())]
    monkeypatch.setattr(bot, "_device_compare_inventory", lambda _user: (rows, "aabbccdd"))
    monkeypatch.setattr(bot.devices, "find_likely_duplicate_device_pairs", lambda actor, owner: [("device-a", "device-b")] if actor == owner == 100 else None, raising=False)

    owner_callbacks = _callback_data(bot.device_menu("device-a", 100))
    user_callbacks = _callback_data(bot.device_menu("device-a", 200))

    assert "catalog:duplicate:compare:aabbccdd:0:1" in owner_callbacks
    assert not any(value.startswith("catalog:duplicate:") for value in user_callbacks)

    rendered, remote_calls = [], []

    async def replace(*args, **kwargs):
        rendered.append((args, kwargs))

    monkeypatch.setattr(bot, "_replace_callback_message", replace)
    monkeypatch.setattr(bot.transport, "publish_command", lambda *args, **kwargs: remote_calls.append((args, kwargs)))

    class Callback:
        data = "catalog:duplicate:compare:aabbccdd:0:1"
        from_user = SimpleNamespace(id=100)
        async def answer(self, *_args, **_kwargs):
            pass

    asyncio.run(bot.on_device_duplicate_compare(Callback()))

    assert len(rendered) == 1
    assert "Office PC" in rendered[0][0][1] and "Copied install" in rendered[0][0][1]
    assert "Одинаковый сообщённый hostname" in rendered[0][0][1]
    assert "build-07" in rendered[0][0][1]
    assert remote_calls == []


def test_device_catalog_export_includes_only_explicit_allowlisted_fields():
    import json

    rows = [
        ("device-1", {
            "name": "Laptop", "os": "Windows 11", "version": "4.2.1",
            "online": True, "last_seen": 123, "token": "SECRET_TOKEN",
            "hostname": "PRIVATE_HOST", "capabilities": {"secret": "SECRET_CAPABILITY"},
        }),
    ]
    filename, payload = bot._build_device_catalog_export(rows, ["name", "os"], "json")

    assert filename.endswith(".json")
    assert json.loads(payload) == [{"name": "Laptop", "os": "Windows 11"}]
    assert b"SECRET_TOKEN" not in payload
    assert b"PRIVATE_HOST" not in payload
    assert b"SECRET_CAPABILITY" not in payload


def test_device_catalog_export_csv_uses_only_selected_fields_and_stable_header():
    rows = [("device-1", {"name": "Laptop", "online": True, "last_seen": 123})]

    filename, payload = bot._build_device_catalog_export(rows, ["device_id", "online"], "csv")

    assert filename.endswith(".csv")
    assert payload.decode("utf-8-sig").splitlines() == ["device_id,online", "device-1,True"]


def test_device_catalog_export_csv_neutralizes_spreadsheet_formulas():
    import csv
    import io

    rows = [("=HYPERLINK(\"https://example.invalid\")", {"name": "@SUM(A1:A2)"})]

    _filename, payload = bot._build_device_catalog_export(rows, ["device_id", "name"], "csv")

    records = list(csv.reader(io.StringIO(payload.decode("utf-8-sig"))))
    assert records == [["device_id", "name"], ["'=HYPERLINK(\"https://example.invalid\")", "'@SUM(A1:A2)"]]


def test_device_catalog_export_maps_invalid_status_and_unrepresentable_timestamp_to_unknown():
    import json

    filename, payload = bot._build_device_catalog_export(
        [("device-1", {"online": "true", "last_seen": 10 ** 1000})],
        ["online", "last_seen"], "json",
    )

    assert filename.endswith(".json")
    assert json.loads(payload) == [{"online": None, "last_seen": None}]


@pytest.mark.parametrize("fields, export_format", [([], "json"), (["token"], "json"), (["name", "name"], "csv"), (["name"], "xml")])
def test_device_catalog_export_rejects_empty_private_duplicate_or_unknown_selection(fields, export_format):
    with pytest.raises(ValueError):
        bot._build_device_catalog_export([("device-1", {"name": "Laptop"})], fields, export_format)


def test_device_catalog_export_is_owner_only_field_selected_and_replaces_the_current_card(monkeypatch, tmp_path):
    from device_store import DeviceStore

    telegram = _card_telegram(monkeypatch, tmp_path)
    monkeypatch.setattr(DeviceStore, "FILE", tmp_path / "known.json", raising=False)
    monkeypatch.setattr(DeviceStore, "CATALOG_FILE", tmp_path / "catalog.json", raising=False)
    store = DeviceStore()
    store.upsert("device-1", {
        "name": "Laptop", "os": "Windows 11", "version": "4.2.1", "online": True,
        "token": "DO_NOT_EXPORT", "hostname": "PRIVATE_HOST",
    })
    monkeypatch.setattr(bot, "devices", store)
    monkeypatch.setattr(bot, "ADMIN_ID", 20)
    monkeypatch.setattr(bot, "get_user_role", lambda user_id: bot.Role.OWNER if user_id == 20 else bot.Role.USER)
    monkeypatch.setattr(bot.access_store, "can_use_callback", lambda user_id, *_args: user_id == 20)
    audits = []
    monkeypatch.setattr(bot, "audit", lambda *args, **kwargs: audits.append((args, kwargs)))
    bot.DEVICE_EXPORT_SESSIONS.clear()

    asyncio.run(bot.on_device_catalog_export(telegram.callback("catalog:export")))
    assert len(telegram.cards) == 1 and telegram.sent == []
    first = telegram.cards[30]
    assert "выбери поля" in first["text"].lower()
    callbacks = [button.callback_data for row in first["markup"].inline_keyboard for button in row]
    assert "catalog:export:field:name" in callbacks
    assert "catalog:export:field:os" in callbacks
    assert "catalog:export:format:csv" in callbacks

    asyncio.run(bot.on_device_catalog_export(telegram.callback("catalog:export:field:name")))
    asyncio.run(bot.on_device_catalog_export(telegram.callback("catalog:export:field:os")))
    asyncio.run(bot.on_device_catalog_export(telegram.callback("catalog:export:format:csv")))
    asyncio.run(bot.on_device_catalog_export(telegram.callback("catalog:export:run")))

    assert telegram.sent == [31]
    assert 30 not in telegram.cards and list(telegram.cards) == [31]
    document = telegram.cards[31]["document"]
    assert document.filename.endswith(".csv")
    assert document.data.decode("utf-8-sig").splitlines() == ["name,os", "Laptop,Windows 11"]
    assert b"DO_NOT_EXPORT" not in document.data and b"PRIVATE_HOST" not in document.data
    assert not bot.DEVICE_EXPORT_SESSIONS
    assert audits == [(('device_catalog_exported',), {
        "actor_id": 20, "fields": "name,os", "export_format": "csv", "row_count": 1,
    })]


def test_device_catalog_export_rejects_non_owner_without_starting_a_session(monkeypatch, tmp_path):
    from types import SimpleNamespace

    telegram = _card_telegram(monkeypatch, tmp_path)
    monkeypatch.setattr(bot, "ADMIN_ID", 20)
    monkeypatch.setattr(bot, "get_user_role", lambda _user_id: bot.Role.USER)
    monkeypatch.setattr(bot.access_store, "can_use_callback", lambda *_args: False)
    denied = telegram.callback("catalog:export")
    denied.from_user = SimpleNamespace(id=99)

    asyncio.run(bot.on_device_catalog_export(denied))

    assert not bot.DEVICE_EXPORT_SESSIONS
    assert not telegram.sent
    assert list(telegram.cards) == [30]


def test_device_catalog_import_preview_is_owner_only_and_replaces_current_card(monkeypatch, tmp_path):
    from types import SimpleNamespace
    from io import BytesIO

    telegram = _card_telegram(monkeypatch, tmp_path)
    monkeypatch.setattr(bot, "ADMIN_ID", 20)
    monkeypatch.setattr(bot, "get_user_role", lambda user_id: bot.Role.OWNER if user_id == 20 else bot.Role.USER)
    monkeypatch.setattr(bot.access_store, "can_use_callback", lambda user_id, *_args: user_id == 20)
    monkeypatch.setattr(bot, "_device_export_snapshot", lambda _user: (
        [("device-1", {"name": "Current", "os": "Windows", "version": "4.2.1", "online": True})], "fresh"
    ))

    class State:
        async def get_data(self):
            return {"message_id": 30, "chat_id": 10, "inventory_token": "fresh", "expires_at": time.time() + 60}
        async def clear(self):
            self.cleared = True

    message = SimpleNamespace(
        from_user=SimpleNamespace(id=20), chat=SimpleNamespace(id=10),
        document=SimpleNamespace(file_size=100, file_name="catalog.json"),
        delete=lambda: asyncio.sleep(0),
        answer=lambda *_args, **_kwargs: asyncio.sleep(0),
    )
    monkeypatch.setattr(telegram, "download", lambda _doc: asyncio.sleep(0, result=BytesIO(
        b'[{"device_id":"device-1","name":"Imported"}]'
    )), raising=False)
    monkeypatch.setattr(bot, "audit", lambda *_args, **_kwargs: None)

    asyncio.run(bot.on_device_catalog_import_document(message, State()))

    assert list(telegram.cards) == [30]
    assert "device-1" in telegram.cards[30]["text"]
    assert "name" in telegram.cards[30]["text"]
    assert "предпросмотр" in telegram.cards[30]["text"].lower()
    assert "не измен" in telegram.cards[30]["text"].lower()
    assert not telegram.sent


def test_device_catalog_import_start_rejects_non_owner(monkeypatch, tmp_path):
    from types import SimpleNamespace

    telegram = _card_telegram(monkeypatch, tmp_path)
    monkeypatch.setattr(bot, "ADMIN_ID", 20)
    monkeypatch.setattr(bot, "get_user_role", lambda _user: bot.Role.USER)
    monkeypatch.setattr(bot.access_store, "can_use_callback", lambda *_args: False)

    class State:
        async def set_state(self, _state):
            raise AssertionError("unauthorized user must not start an import session")
        async def set_data(self, _data):
            raise AssertionError("unauthorized user must not store import state")

    callback = telegram.callback("catalog:import")
    callback.from_user = SimpleNamespace(id=99)
    asyncio.run(bot.on_device_catalog_import_start(callback, State()))
    assert list(telegram.cards) == [30]
    assert not telegram.sent


@pytest.mark.parametrize(
    ("user_id", "role", "expected"),
    [(20, bot.Role.OWNER, True), (21, bot.Role.USER, False), (22, bot.Role.GUEST, False)],
)
def test_device_catalog_import_menu_entry_is_owner_only(monkeypatch, tmp_path, user_id, role, expected):
    _card_telegram(monkeypatch, tmp_path)
    monkeypatch.setattr(bot, "ADMIN_ID", 20)
    monkeypatch.setattr(bot, "get_user_role", lambda _user: role)
    monkeypatch.setattr(bot, "_visible_device_items", lambda _user: {})
    monkeypatch.setattr(bot.access_store, "get_effective_grants", lambda _user: {"callbacks": set()})

    markup = bot.devices_menu(user_id)
    callbacks = [button.callback_data for row in markup.inline_keyboard for button in row]

    assert ("catalog:import" in callbacks) is expected


def test_audit_filter_parser_accepts_optional_exact_filters():
    parsed = bot._parse_audit_filter_input("2026-10-18 | 100 | device_online | mac-a")
    assert parsed == {
        "since": 1_792_281_600, "until": 1_792_367_999,
        "actor_id": 100, "event_type": "device_online", "device_id": "mac-a",
    }


def test_audit_export_parser_requires_bounded_inclusive_utc_date_range():
    parsed = bot._parse_audit_export_input("2026-10-01 | 2026-10-18 | 100 | command_execution | mac-a")
    assert parsed == {
        "since": 1_790_812_800, "until": 1_792_367_999,
        "actor_id": 100, "event_type": "command_execution", "device_id": "mac-a",
    }
    assert bot._parse_audit_export_input("2026-10-01 | 2026-11-05 | - | - | -") is None
    assert bot._parse_audit_export_input("bad | 2026-10-18 | - | - | -") is None
    assert bot._parse_audit_filter_input("- | - | - | -") == {
        "since": None, "until": None, "actor_id": None,
        "event_type": None, "device_id": None,
    }


def test_shell_metadata_export_parser_requires_bounded_date_range_and_known_statuses():
    parsed = bot._parse_shell_metadata_export_input("2026-10-01 | 2026-10-18 | completed, failed")
    assert parsed == {
        "since": 1_790_812_800,
        "until": 1_792_367_999,
        "statuses": ["completed", "failed"],
    }
    assert bot._parse_shell_metadata_export_input("2026-10-01 | 2026-11-05 | completed") is None
    assert bot._parse_shell_metadata_export_input("2026-10-01 | 2026-10-02 | completed,unknown") is None
    assert bot._parse_shell_metadata_export_input("2026-10-02 | 2026-10-01 | failed") is None


def test_shell_metadata_export_delivers_owner_scoped_filtered_json_without_command_data(monkeypatch):
    calls = []
    audits = []
    monkeypatch.setattr(bot, "ADMIN_ID", 100)
    monkeypatch.setattr(bot, "_admin_owner", lambda _obj: asyncio.sleep(0, result=True))
    def export(actor, owner, **filters):
        calls.append((actor, owner, filters))
        return {
            "schema": "xider.shell-audit-export.v1",
            "range": {"since": 1_790_812_800, "until": 1_792_367_999},
            "filters": {"statuses": ["completed", "failed"], "device_id": "mac-a"},
            "truncated": False,
            "events": [{"at": 1_790_812_801, "status": "completed", "actor_id": 100,
                        "target_id": None, "device_id": "mac-a", "requester_id": 200,
                        "approver_id": 100, "exit_code": 0}],
        }
    monkeypatch.setattr(bot.access_store, "export_shell_command_audit", export)
    monkeypatch.setattr(bot.access_store, "append_audit", lambda kind, **fields: audits.append((kind, fields)))
    async def deliver(_message, document, caption, reply_markup=None):
        calls.append((document, caption, reply_markup))
    monkeypatch.setattr(bot, "_replace_user_card_with_document", deliver)
    class State:
        async def get_data(self):
            return {"device_id": "mac-a"}
        async def clear(self):
            calls.append("cleared")
    message = SimpleNamespace(text="2026-10-01 | 2026-10-18 | completed, failed",
                              from_user=SimpleNamespace(id=100))

    asyncio.run(bot.on_shell_metadata_export_text(message, State()))

    assert calls[0] == "cleared"
    actor, owner, filters = calls[1]
    assert (actor, owner, filters["statuses"], filters["device_id"]) == (
        100, 100, ["completed", "failed"], "mac-a",
    )
    document, caption, _markup = calls[2]
    assert document.filename == "xider-shell-audit-20261001-20261018.json"
    assert "shell" in caption.lower() and "1" in caption
    async def collect_payload():
        chunks = []
        async for chunk in document.read(bot.bot):
            chunks.append(chunk)
        return b"".join(chunks)
    payload = asyncio.run(collect_payload())
    assert b"xider.shell-audit-export.v1" in payload and b"completed" in payload
    assert b"command_hash" not in payload and b"request_id" not in payload
    assert b"command" not in payload and b"output" not in payload
    assert audits == [("shell_metadata_export_requested", {
        "actor_id": 100,
        "detail": "since=1790812800;until=1792367999;statuses=completed,failed;device=mac-a;events=1;truncated=False",
    })]


def test_audit_filter_keyboard_is_registry_driven_and_has_owner_back_route(monkeypatch):
    markup = bot.admin_audit_keyboard(actor_id=bot.ADMIN_ID)
    callbacks = _callback_data(markup)
    assert callbacks == ["admin:audit:filter", "admin:audit:export", "admin:audit:back"]
    assert all(button.text for row in markup.inline_keyboard for button in row)


def test_admin_audit_export_delivers_only_bounded_normalized_json(monkeypatch):
    calls = []
    audits = []
    monkeypatch.setattr(bot, "ADMIN_ID", 100)
    monkeypatch.setattr(bot, "_admin_owner", lambda _obj: asyncio.sleep(0, result=True))
    monkeypatch.setattr(bot.access_store, "export_audit", lambda actor, owner, **filters: {
        "schema": "xider.audit-export.v1", "range": {"since": 1_790_812_800, "until": 1_792_367_999},
        "truncated": False, "events": [{"kind": "command_execution", "outcome": "completed"}],
    })
    monkeypatch.setattr(bot.access_store, "append_audit", lambda kind, **fields: audits.append((kind, fields)))
    async def deliver(_message, document, caption, reply_markup=None):
        calls.append((document, caption, reply_markup))
    monkeypatch.setattr(bot, "_replace_user_card_with_document", deliver)
    class State:
        async def clear(self):
            calls.append("cleared")
    message = SimpleNamespace(
        from_user=SimpleNamespace(id=100),
        text="2026-10-01 | 2026-10-18 | - | - | -",
    )

    asyncio.run(bot.on_admin_audit_export_text(message, State()))

    assert calls[0] == "cleared"
    document, caption, _markup = calls[1]
    assert document.filename == "xider-audit-20261001-20261018.json"
    assert "redacted" in caption
    async def collect_payload():
        chunks = []
        async for chunk in document.read(bot.bot):
            chunks.append(chunk)
        return b"".join(chunks)
    payload = asyncio.run(collect_payload())
    assert b"xider.audit-export.v1" in payload and b"completed" in payload
    assert b"command_hash" not in payload and b"private-id" not in payload
    assert audits == [("audit_export_requested", {
        "actor_id": 100,
        "detail": "since=1790812800;until=1792367999;events=1;truncated=False",
    })]


def test_admin_grant_confirmation_previews_overlapping_access_before_owner_decision(monkeypatch):
    calls = []
    monkeypatch.setattr(bot, "ADMIN_ID", 100)
    monkeypatch.setattr(bot, "_admin_snapshot", lambda _user: {"role": "user", "permissions": {}})
    monkeypatch.setattr(bot.access_store, "get_effective_grants", lambda _user: {"callbacks": [], "devices": []})
    monkeypatch.setattr(bot.access_store, "preview_grant_conflicts", lambda actor, user, kind, item, owner: {
        "target_id": user, "kind": kind, "item": item, "reason": "broader_grant_overlaps_existing",
        "conflict": True, "overlapping_grants": ["cmd:status"], "blocked": False,
    })
    async def replace(_cq, text, reply_markup=None):
        calls.append((text, reply_markup))
    async def answer():
        calls.append("answered")
    monkeypatch.setattr(bot, "_replace_callback_message", replace)
    callback = SimpleNamespace(from_user=SimpleNamespace(id=100), answer=answer)

    asyncio.run(bot._admin_stage(callback, "permission", 200, "full_device"))

    assert "Пересечение" in calls[0][0]
    assert "Статус устройства" in calls[0][0]
    assert "2 минуты" in calls[0][0]
    assert calls[1] == "answered"


def test_admin_stale_device_grants_screen_shows_only_missing_registry_ids(monkeypatch):
    calls = []
    monkeypatch.setattr(bot, "ADMIN_ID", 100)
    monkeypatch.setattr(bot, "_admin_owner", lambda _obj: asyncio.sleep(0, result=True))
    monkeypatch.setattr(bot.devices, "all", lambda: {"mac-live": {"name": "Live Mac"}})
    monkeypatch.setattr(bot.access_store, "find_stale_device_grants", lambda actor, owner, inventory: {
        "rows": [{"target_id": 200, "device_id": "mac-gone", "blocked": True, "expires_at": None}],
        "truncated": False,
    })
    async def replace(_cq, text, reply_markup=None):
        calls.append((text, reply_markup))
    async def answer():
        calls.append("answered")
    monkeypatch.setattr(bot, "_replace_callback_message", replace)
    callback = SimpleNamespace(from_user=SimpleNamespace(id=100), data="admin:stale-grants", answer=answer)

    asyncio.run(bot.on_admin_stale_device_grants(callback))

    assert "mac-gone" in calls[0][0]
    assert "200" in calls[0][0] and "заблокирован" in calls[0][0]
    assert "ничего не отозвано" in calls[0][0]
    assert _callback_data(calls[0][1]) == ["admin:stale-grants:refresh", "menu:admin"]
    assert calls[1] == "answered"


def test_monthly_access_review_is_owner_only_paginated_and_read_only(monkeypatch):
    calls = []
    monkeypatch.setattr(bot, "ADMIN_ID", 100)
    monkeypatch.setattr(bot, "_admin_owner", lambda _obj: asyncio.sleep(0, result=True))
    monkeypatch.setattr(bot.access_store, "monthly_access_review", lambda actor, owner, *, page: {
        "generated_at": 1_792_281_600, "page": page, "page_size": 4, "total": 5, "pages": 2,
        "rows": [{"target_id": 201, "role": "user", "blocked": False,
                  "devices": ["mac-one"], "callbacks": ["cmd:status"]}],
    })
    async def replace(_cq, text, reply_markup=None):
        calls.append((text, reply_markup))
    async def answer(*_args, **_kwargs):
        calls.append("answered")
    monkeypatch.setattr(bot, "_replace_callback_message", replace)
    callback = SimpleNamespace(from_user=SimpleNamespace(id=100), data="admin:access-review:page:0", answer=answer)

    asyncio.run(bot.on_admin_monthly_access_review(callback))

    assert "Ежемесячный обзор ролей и прав" in calls[0][0]
    assert "ID <code>201</code> · user" in calls[0][0]
    assert "mac-one" in calls[0][0] and "cmd:status" in calls[0][0]
    assert "ничего не меняет" in calls[0][0]
    assert _callback_data(calls[0][1]) == [
        "admin:access-review", "admin:access-review:page:1", "menu:admin",
    ]
    assert calls[1] == "answered"


def test_audit_keyboard_adds_opaque_event_buttons_without_raw_detail():
    row = {"at": 1_792_281_600, "kind": "command_execution", "actor_id": 100,
           "device_id": "mac-a", "detail": "password=never-show-this"}
    markup = bot.admin_audit_keyboard(actor_id=bot.ADMIN_ID, event_rows=[row])
    buttons = [button for line in markup.inline_keyboard for button in line]
    event = next(button for button in buttons if button.callback_data.startswith("admin:audit:event:"))
    assert len(event.callback_data.rsplit(":", 1)[-1]) == 20
    assert "never-show-this" not in event.text
    assert "mac-a" in event.text and "command_execution" in event.text


def test_admin_audit_event_opens_only_normalized_owner_projection(monkeypatch):
    from types import SimpleNamespace

    calls = []
    monkeypatch.setattr(bot, "ADMIN_ID", 100)
    monkeypatch.setattr(bot, "_admin_owner", lambda _obj: asyncio.sleep(0, result=True))
    monkeypatch.setattr(bot.access_store, "get_audit_event", lambda actor, owner, ref: {
        "event_id": ref, "at": 1_792_281_600, "kind": "command_execution",
        "actor_id": 100, "target_id": None, "device_id": "mac-a", "outcome": "completed",
        "fields": {"requester_id": 200, "approver_id": 100, "exit_code": 0},
    })
    async def replace(_cq, text, reply_markup=None):
        calls.append((text, reply_markup))
    async def answer(*_args, **_kwargs):
        calls.append("answered")
    monkeypatch.setattr(bot, "_replace_callback_message", replace)
    callback = SimpleNamespace(from_user=SimpleNamespace(id=100), data="admin:audit:event:" + "a" * 20, answer=answer)

    asyncio.run(bot.on_admin_audit_event(callback))

    assert "completed" in calls[0][0] and "requester_id" in calls[0][0]
    assert "command_hash" not in calls[0][0] and "secret" not in calls[0][0]
    assert calls[1] == "answered"


def test_admin_audit_back_clears_pending_filter_state_and_returns_to_admin(monkeypatch):
    from types import SimpleNamespace

    calls = []
    async def replace(_cq, text, reply_markup=None):
        calls.append((text, reply_markup))
    async def answer(*_args, **_kwargs):
        calls.append("answered")
    class State:
        async def clear(self):
            calls.append("cleared")
    callback = SimpleNamespace(from_user=SimpleNamespace(id=bot.ADMIN_ID), answer=answer)
    monkeypatch.setattr(bot, "_admin_owner", lambda _obj: asyncio.sleep(0, result=True))
    monkeypatch.setattr(bot, "_replace_callback_message", replace)
    monkeypatch.setattr(bot, "admin_menu", lambda user_id: ("admin-menu", user_id))

    asyncio.run(bot.on_admin_audit_back(callback, State()))

    assert calls[0] == "cleared"
    assert calls[1] == ("<b>Администрирование</b>", ("admin-menu", bot.ADMIN_ID))
    assert calls[2] == "answered"


def test_audit_renderer_skips_bad_timestamps_escapes_values_and_bounds_card():
    rows = [{"at": 10 ** 1000, "kind": "bad"}]
    rows.extend({
        "at": 1_792_281_600, "kind": "<event>", "actor_id": 100,
        "device_id": "<mac>", "detail": "x" * 280,
    } for _ in range(30))
    rendered = bot._render_audit_rows(rows)
    assert "&lt;event&gt;" in rendered and "&lt;mac&gt;" in rendered
    assert "<event>" not in rendered and "<mac>" not in rendered
    assert len(rendered) <= 3800


def test_admin_audit_filter_text_edits_one_owner_card_with_filtered_rows(monkeypatch):
    from types import SimpleNamespace

    calls = []
    monkeypatch.setattr(bot, "ADMIN_ID", 100)
    monkeypatch.setattr(bot.access_store, "filter_audit", lambda actor, owner, **filters: (
        calls.append((actor, owner, filters)) or [{
            "at": 1_792_281_600, "kind": "device_online", "actor_id": 100,
            "device_id": "mac-a", "detail": "connected",
        }]
    ))
    monkeypatch.setattr(bot, "_replace_user_card", lambda message, text, reply_markup=None: (
        calls.append((text, reply_markup)) or asyncio.sleep(0)
    ))

    class State:
        async def clear(self):
            calls.append("cleared")

    message = SimpleNamespace(
        from_user=SimpleNamespace(id=100), text="2026-10-18 | 100 | device_online | mac-a",
    )
    asyncio.run(bot.on_admin_audit_filter_text(message, State()))

    assert (100, 100, {
        "since": 1_792_281_600, "until": 1_792_367_999,
        "actor_id": 100, "event_type": "device_online", "device_id": "mac-a", "limit": 30,
    }) in calls
    rendered = next(item for item in calls if isinstance(item, tuple) and len(item) == 2 and isinstance(item[0], str))
    assert "device_online" in rendered[0] and "mac-a" in rendered[0]
    assert "cleared" in calls


def test_dns_resolution_parser_accepts_idna_and_optional_expected_addresses():
    assert bot._parse_dns_resolution_input("BÜCHER.example. | 203.0.113.10, 2001:db8::10") == {
        "hostname": "xn--bcher-kva.example",
        "expected_addresses": ["203.0.113.10", "2001:db8::10"],
    }
    assert bot._parse_dns_resolution_input("example.com") == {
        "hostname": "example.com", "expected_addresses": None,
    }


@pytest.mark.parametrize("value", ["", "127.0.0.1", "bad host", "example.com | not-an-ip", "example.com | 192.0.2.1,192.0.2.1"])
def test_dns_resolution_parser_rejects_invalid_or_ambiguous_input(value):
    assert bot._parse_dns_resolution_input(value) is None


def test_dns_resolution_is_owner_only_correlated_and_edits_one_card(monkeypatch, tmp_path):
    telegram = _card_telegram(monkeypatch, tmp_path)
    _setup(monkeypatch, {"device-1": {"name": "Laptop", "os": "Windows 11", "capabilities": {
        "commands": ["dns_resolution"],
    }}})
    monkeypatch.setattr(bot, "ADMIN_ID", 20)
    monkeypatch.setattr(bot, "SESSION", {"target": "device-1"})
    monkeypatch.setattr(bot, "get_user_role", lambda user_id: bot.Role.OWNER if user_id == 20 else bot.Role.GUEST)
    monkeypatch.setattr(bot.access_store, "can_use_callback", lambda *_args: True)
    published, waited = [], []
    monkeypatch.setattr(bot, "publish_tracked", lambda *args, **kwargs: published.append((args, kwargs)) or (True, "dns-1"))

    async def wait_for(*args):
        waited.append(args)
        return {
            "type": "dns_resolution", "schema": "xider.dns-resolution.v1", "feature_id": "D04-F05",
            "device_id": "device-1", "request_id": "dns-1", "source": "system_getaddrinfo",
            "state": "supported", "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "data": {"hostname": "example.com", "addresses": ["203.0.113.10"],
                     "truncated": False, "expected_match": True}, "detail": None,
        }

    monkeypatch.setattr(bot, "dns_resolution_collector", SimpleNamespace(wait_for=wait_for))

    class State:
        async def get_data(self):
            return {"authorization_callback": "cmd:dns_resolution", "authorization_target": "device-1"}
        async def clear(self):
            pass

    message = telegram.message(message_id=45, chat_id=10, user_id=20)
    message.text = "example.com | 203.0.113.10"
    asyncio.run(bot.on_dns_resolution_input(message, State()))

    menu = bot.network_menu(user_id=20)
    guest = bot.network_menu(user_id=21)
    owner_buttons = {button.callback_data: button.text for row in menu.inline_keyboard for button in row}
    guest_callbacks = {button.callback_data for row in guest.inline_keyboard for button in row}
    assert owner_buttons["cmd:dns_resolution"] == "🌐 DNS-проверка"
    assert "cmd:dns_resolution" not in guest_callbacks
    assert _expected_callback_gate("cmd:dns_resolution") == "OwnerFilter"
    assert published == [(('dns_resolution',), {
        "_target": "device-1", "hostname": "example.com", "expected_addresses": ["203.0.113.10"],
    })]
    assert waited == [("device-1", "dns_resolution", 9.0, "dns-1")]
    assert list(telegram.cards) == [30] and not telegram.sent
    assert "203.0.113.10" in telegram.cards[30]["text"]
    assert "совпадает" in telegram.cards[30]["text"]


def test_dns_settings_is_owner_only_correlated_and_edits_one_card(monkeypatch, tmp_path):
    telegram = _card_telegram(monkeypatch, tmp_path)
    _setup(monkeypatch, {"device-1": {"name": "Laptop", "os": "Windows 11", "capabilities": {
        "commands": ["dns_settings"],
    }}})
    monkeypatch.setattr(bot, "ADMIN_ID", 20)
    monkeypatch.setattr(bot, "SESSION", {"target": "device-1"})
    monkeypatch.setattr(bot, "get_user_role", lambda user_id: bot.Role.OWNER if user_id == 20 else bot.Role.GUEST)
    monkeypatch.setattr(bot.access_store, "can_use_callback", lambda *_args: True)
    monkeypatch.setattr(bot, "_simple_command_still_allowed", lambda *_args: True)
    published, waited = [], []
    monkeypatch.setattr(bot, "publish_tracked", lambda *args, **kwargs: published.append((args, kwargs)) or (True, "dns-settings-1"))

    async def wait_for(*args):
        waited.append(args)
        return {
            "type": "dns_settings", "schema": "xider.dns-settings.v1", "feature_id": "D04-F06",
            "device_id": "device-1", "request_id": "dns-settings-1", "source": "windows_get_dns_client_server_address",
            "state": "supported", "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "data": {"resolvers": [{"interface_index": 12, "addresses": ["1.1.1.1", "2001:4860:4860::8888"]}],
                     "search_domains": ["must-not-be-rendered.example"]},
            "private_note": "must-not-be-rendered",
        }

    monkeypatch.setattr(bot, "dns_settings_collector", SimpleNamespace(wait_for=wait_for))
    async def answer(*_args, **_kwargs): pass
    async def edit_text(text, **_kwargs):
        telegram.cards[30]["text"] = text
    message = SimpleNamespace(chat=SimpleNamespace(id=10), message_id=30, edit_text=edit_text)
    cq = SimpleNamespace(from_user=SimpleNamespace(id=20), data="cmd:dns_settings", answer=answer,
                         message=message)
    asyncio.run(bot.on_cmd_dns_settings(cq))

    menu = bot.network_menu(user_id=20)
    guest = bot.network_menu(user_id=21)
    owner_buttons = {button.callback_data: button.text for row in menu.inline_keyboard for button in row}
    guest_callbacks = {button.callback_data for row in guest.inline_keyboard for button in row}
    assert owner_buttons["cmd:dns_settings"] == "🧭 DNS-настройки"
    assert "cmd:dns_settings" not in guest_callbacks
    assert _expected_callback_gate("cmd:dns_settings") == "OwnerFilter"
    assert published == [(("dns_settings",), {"_target": "device-1"})]
    assert waited == [("device-1", "dns_settings", 9.0, "dns-settings-1")]
    assert list(telegram.cards) == [30] and not telegram.sent
    rendered = telegram.cards[30]["text"]
    assert "1.1.1.1" in rendered and "2001:4860:4860::8888" in rendered
    assert "must-not-be-rendered" not in rendered and "private_note" not in rendered


def test_dns_settings_validator_rejects_mismatched_device_request_and_suffix_fields():
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    result = {
        "type": "dns_settings", "schema": "xider.dns-settings.v1", "feature_id": "D04-F06",
        "device_id": "device-1", "request_id": "fresh", "source": "macos_scutil_dns", "state": "supported",
        "checked_at": now,
        "data": {"resolvers": [{"interface_index": 14, "addresses": ["192.0.2.53"]}]},
    }
    parsed = bot._validated_dns_settings(result, target="device-1", command_id="fresh", platform="macos")
    assert parsed["resolvers"] == [{"interface_index": 14, "addresses": ["192.0.2.53"]}]
    for key, value in (("device_id", "other"), ("request_id", "stale"), ("source", "wrong")):
        assert bot._validated_dns_settings({**result, key: value}, target="device-1", command_id="fresh", platform="macos") is None
    malformed = {**result, "data": {"resolvers": [{"interface_index": 14, "addresses": ["192.0.2.53"], "service_name": "en0"}]}}
    assert bot._validated_dns_settings(malformed, target="device-1", command_id="fresh", platform="macos") is None


def test_dns_comparison_parser_requires_domain_and_two_literal_resolvers():
    assert bot._parse_dns_comparison_input("xn--bcher-kva.example | 1.1.1.1,2001:4860:4860::8888") == {
        "hostname": "xn--bcher-kva.example", "resolvers": ["1.1.1.1", "2001:4860:4860::8888"],
    }
    for value in ("", "127.0.0.1 | 1.1.1.1,8.8.8.8", "example.com | 1.1.1.1",
                  "example.com | dns.example,8.8.8.8", "example.com | 1.1.1.1,1.1.1.1",
                  "example.com | 1.1.1.1,fe80::1"):
        assert bot._parse_dns_comparison_input(value) is None


def test_dns_comparison_is_owner_only_correlated_and_edits_one_card(monkeypatch, tmp_path):
    telegram = _card_telegram(monkeypatch, tmp_path)
    _setup(monkeypatch, {"device-1": {"name": "Laptop", "os": "Windows 11", "capabilities": {
        "commands": ["dns_comparison"],
    }}})
    monkeypatch.setattr(bot, "ADMIN_ID", 20)
    monkeypatch.setattr(bot, "SESSION", {"target": "device-1"})
    monkeypatch.setattr(bot, "get_user_role", lambda user_id: bot.Role.OWNER if user_id == 20 else bot.Role.GUEST)
    monkeypatch.setattr(bot.access_store, "can_use_callback", lambda *_args: True)
    monkeypatch.setattr(bot, "_pending_device_input_still_allowed", lambda *_args: True)
    published, waited = [], []
    monkeypatch.setattr(bot, "publish_tracked", lambda *args, **kwargs: published.append((args, kwargs)) or (True, "dns-compare-1"))

    async def wait_for(*args):
        waited.append(args)
        return {
            "type": "dns_comparison", "schema": "xider.dns-comparison.v1", "feature_id": "D04-F07",
            "device_id": "device-1", "request_id": "dns-compare-1",
            "source": "direct_udp_dns_to_owner_selected_resolvers", "state": "supported",
            "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat(), "detail": None,
            "data": {"hostname": "example.com", "comparison": "same", "resolvers": [
                {"address": "1.1.1.1", "status": "complete", "records": {
                    "A": {"rcode": "NOERROR", "addresses": ["203.0.113.8"]},
                    "AAAA": {"rcode": "NOERROR", "addresses": ["2001:db8::8"]},
                }},
                {"address": "8.8.8.8", "status": "complete", "records": {
                    "A": {"rcode": "NOERROR", "addresses": ["203.0.113.8"]},
                    "AAAA": {"rcode": "NOERROR", "addresses": ["2001:db8::8"]},
                }},
            ]},
        }

    monkeypatch.setattr(bot, "dns_comparison_collector", SimpleNamespace(wait_for=wait_for))
    class State:
        async def get_data(self):
            return {"authorization_callback": "cmd:dns_comparison", "authorization_target": "device-1"}
        async def clear(self): pass

    message = telegram.message(message_id=46, chat_id=10, user_id=20)
    message.text = "example.com | 1.1.1.1,8.8.8.8"
    asyncio.run(bot.on_dns_comparison_input(message, State()))

    owner_menu = bot.network_menu(user_id=20)
    guest_menu = bot.network_menu(user_id=21)
    owner_buttons = {button.callback_data: button.text for row in owner_menu.inline_keyboard for button in row}
    guest_callbacks = {button.callback_data for row in guest_menu.inline_keyboard for button in row}
    assert owner_buttons["cmd:dns_comparison"] == "🧪 Сравнить DNS"
    assert "cmd:dns_comparison" not in guest_callbacks
    assert _expected_callback_gate("cmd:dns_comparison") == "OwnerFilter"
    assert published == [(("dns_comparison",), {
        "_target": "device-1", "hostname": "example.com", "resolvers": ["1.1.1.1", "8.8.8.8"],
    })]
    assert waited == [("device-1", "dns_comparison", 9.0, "dns-compare-1")]
    assert list(telegram.cards) == [30] and not telegram.sent
    assert "ответы совпадают" in telegram.cards[30]["text"]
    assert "203.0.113.8" in telegram.cards[30]["text"]


def test_dns_comparison_validator_recomputes_comparison_and_rejects_stale_reply():
    result = {
        "type": "dns_comparison", "schema": "xider.dns-comparison.v1", "feature_id": "D04-F07",
        "device_id": "device-1", "request_id": "fresh", "source": "direct_udp_dns_to_owner_selected_resolvers",
        "state": "supported", "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "data": {"hostname": "example.com", "comparison": "same", "resolvers": [
            {"address": "1.1.1.1", "status": "complete", "records": {
                "A": {"rcode": "NOERROR", "addresses": ["203.0.113.8"]}, "AAAA": {"rcode": "NOERROR", "addresses": []}}},
            {"address": "8.8.8.8", "status": "complete", "records": {
                "A": {"rcode": "NOERROR", "addresses": ["203.0.113.8"]}, "AAAA": {"rcode": "NOERROR", "addresses": []}}},
        ]},
    }
    parsed = bot._validated_dns_comparison(result, target="device-1", command_id="fresh", hostname="example.com",
                                           resolvers=["1.1.1.1", "8.8.8.8"])
    assert parsed["comparison"] == "same"
    assert bot._validated_dns_comparison({**result, "request_id": "old"}, target="device-1", command_id="fresh",
                                         hostname="example.com", resolvers=["1.1.1.1", "8.8.8.8"]) is None
    mismatch = {**result, "data": {**result["data"], "comparison": "different"}}
    assert bot._validated_dns_comparison(mismatch, target="device-1", command_id="fresh", hostname="example.com",
                                         resolvers=["1.1.1.1", "8.8.8.8"]) is None


def test_gateway_probe_validator_requires_fresh_correlated_truthful_reply():
    result = {
        "type": "gateway_probe", "schema": "xider.gateway-probe.v1", "feature_id": "D04-F08",
        "device_id": "device-1", "request_id": "fresh",
        "source": "active_ipv4_default_route_and_single_icmp_echo", "state": "reachable",
        "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "data": {"gateway": "192.168.1.1"}, "detail": None,
    }
    parsed = bot._validated_gateway_probe(result, target="device-1", command_id="fresh")
    assert parsed["state"] == "reachable" and parsed["gateway"] == "192.168.1.1"
    for key, value in (("device_id", "other"), ("request_id", "stale"), ("source", "invented")):
        assert bot._validated_gateway_probe({**result, key: value}, target="device-1", command_id="fresh") is None
    # A failed ICMP echo cannot be promoted to a confirmed router outage.
    no_echo = {**result, "state": "unreachable", "data": {"gateway": "192.168.1.1"}}
    assert bot._validated_gateway_probe(no_echo, target="device-1", command_id="fresh") is None
    bad_ip = {**result, "data": {"gateway": "gateway.example"}}
    assert bot._validated_gateway_probe(bad_ip, target="device-1", command_id="fresh") is None


def test_route_trace_parser_accepts_only_one_literal_unicast_ip():
    assert bot._parse_route_trace_input("203.0.113.9") == {"target": "203.0.113.9"}
    assert bot._parse_route_trace_input("2001:4860:4860::8888") == {"target": "2001:4860:4860::8888"}
    for value in ("example.com", "203.0.113.9 8.8.8.8", "0.0.0.0", "224.0.0.1", "169.254.1.2"):
        assert bot._parse_route_trace_input(value) is None


def test_route_trace_validator_recomputes_completion_and_rejects_untrusted_hops():
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    result = {
        "type": "route_trace", "schema": "xider.route-trace.v1", "feature_id": "D04-F09",
        "device_id": "device-1", "request_id": "fresh", "source": "windows_tracert_icmp",
        "checked_at": now, "state": "complete", "detail": None,
        "data": {"target": "203.0.113.9", "reached": True, "hops": [
            {"index": 1, "address": "192.168.1.1"}, {"index": 2, "address": "203.0.113.9"},
        ]},
    }
    parsed = bot._validated_route_trace(result, target="device-1", command_id="fresh",
                                        requested_target="203.0.113.9", platform="windows")
    assert parsed["state"] == "complete" and parsed["reached"] is True
    mismatch = {**result, "data": {**result["data"], "reached": False}}
    assert bot._validated_route_trace(mismatch, target="device-1", command_id="fresh",
                                      requested_target="203.0.113.9", platform="windows") is None
    bad_hop = {**result, "data": {**result["data"], "hops": [{"index": 1, "address": "router.local"}]}}
    assert bot._validated_route_trace(bad_hop, target="device-1", command_id="fresh",
                                      requested_target="203.0.113.9", platform="windows") is None


def test_route_trace_is_owner_only_correlated_and_edits_one_card(monkeypatch, tmp_path):
    from types import SimpleNamespace

    telegram = _card_telegram(monkeypatch, tmp_path)
    _setup(monkeypatch, {"device-1": {"name": "Laptop", "os": "Windows 11", "capabilities": {
        "commands": ["route_trace"],
    }}})
    monkeypatch.setattr(bot, "ADMIN_ID", 20)
    monkeypatch.setattr(bot, "SESSION", {"target": "device-1"})
    monkeypatch.setattr(bot, "get_user_role", lambda uid: bot.Role.OWNER if uid == 20 else bot.Role.GUEST)
    monkeypatch.setattr(bot.access_store, "can_use_callback", lambda *_args: True)
    monkeypatch.setattr(bot, "_callback_result_access_or_report", lambda *_args: asyncio.sleep(0, result=True))
    monkeypatch.setattr(bot, "_pending_device_input_still_allowed", lambda *_args: True)
    published, waited = [], []
    monkeypatch.setattr(bot, "publish_tracked", lambda *args, **kwargs: published.append((args, kwargs)) or (True, "trace-1"))

    async def wait_for(*args):
        waited.append(args)
        return {
            "type": "route_trace", "schema": "xider.route-trace.v1", "feature_id": "D04-F09",
            "device_id": "device-1", "request_id": "trace-1", "source": "windows_tracert_icmp",
            "state": "complete", "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "detail": None, "data": {"target": "203.0.113.9", "reached": True,
                                      "hops": [{"index": 1, "address": "203.0.113.9"}]},
        }

    monkeypatch.setattr(bot, "route_trace_collector", SimpleNamespace(wait_for=wait_for))

    class State:
        data = {}
        async def set_state(self, value): self.current = value
        async def update_data(self, **kwargs): self.data.update(kwargs)
        async def get_data(self): return dict(self.data)
        async def clear(self): self.data = {}

    state = State()
    async def answer(*_args, **_kwargs): pass
    cq = telegram.callback("cmd:route_trace")
    asyncio.run(bot.on_cmd_route_trace(cq, state))
    message = SimpleNamespace(from_user=SimpleNamespace(id=20), text="203.0.113.9",
                              chat=SimpleNamespace(id=10), message_id=30)
    asyncio.run(bot.on_route_trace_input(message, state))

    owner_menu = bot.network_menu(user_id=20)
    guest_menu = bot.network_menu(user_id=21)
    owner_buttons = {button.callback_data: button.text for row in owner_menu.inline_keyboard for button in row}
    guest_callbacks = {button.callback_data for row in guest_menu.inline_keyboard for button in row}
    assert owner_buttons["cmd:route_trace"] == "🧭 Трассировка маршрута"
    assert "cmd:route_trace" not in guest_callbacks
    assert _expected_callback_gate("cmd:route_trace") == "OwnerFilter"
    assert published == [(("route_trace",), {"_target": "device-1", "target": "203.0.113.9"})]
    assert waited == [("device-1", "route_trace", 18.0, "trace-1")]


def test_path_mtu_parser_and_validator_reject_stale_or_inconsistent_evidence():
    assert bot._parse_path_mtu_input("203.0.113.9") == {"target": "203.0.113.9"}
    assert bot._parse_path_mtu_input("example.com") is None
    assert bot._parse_path_mtu_input("224.0.0.1") is None
    stamp = datetime.datetime.now(datetime.timezone.utc).isoformat()
    result = {
        "type": "path_mtu", "schema": "xider.path-mtu.v1", "feature_id": "D04-F10",
        "device_id": "device-1", "request_id": "mtu-1", "source": "windows_ping_ipv4_df",
        "checked_at": stamp, "state": "supported", "data": {
            "target": "203.0.113.9", "mtu": 1428, "max_tested": False,
        }, "detail": None,
    }
    assert bot._validated_path_mtu(result, target="device-1", command_id="mtu-1",
                                  requested_target="203.0.113.9", platform="windows")["mtu"] == 1428
    mismatched = {**result, "data": {**result["data"], "target": "203.0.113.10"}}
    assert bot._validated_path_mtu(mismatched, target="device-1", command_id="mtu-1",
                                  requested_target="203.0.113.9", platform="windows") is None
    untrusted = {**result, "data": {**result["data"], "mtu": 5000}}
    assert bot._validated_path_mtu(untrusted, target="device-1", command_id="mtu-1",
                                  requested_target="203.0.113.9", platform="windows") is None


def test_path_mtu_is_owner_only_correlated_and_edits_one_card(monkeypatch, tmp_path):
    from types import SimpleNamespace

    telegram = _card_telegram(monkeypatch, tmp_path)
    _setup(monkeypatch, {"device-1": {"name": "Laptop", "os": "Windows 11", "capabilities": {
        "commands": ["path_mtu"],
    }}})
    monkeypatch.setattr(bot, "ADMIN_ID", 20)
    monkeypatch.setattr(bot, "SESSION", {"target": "device-1"})
    monkeypatch.setattr(bot, "get_user_role", lambda uid: bot.Role.OWNER if uid == 20 else bot.Role.GUEST)
    monkeypatch.setattr(bot.access_store, "can_use_callback", lambda *_args: True)
    monkeypatch.setattr(bot, "_callback_result_access_or_report", lambda *_args: asyncio.sleep(0, result=True))
    monkeypatch.setattr(bot, "_pending_device_input_still_allowed", lambda *_args: True)
    published, waited = [], []
    monkeypatch.setattr(bot, "publish_tracked", lambda *args, **kwargs: published.append((args, kwargs)) or (True, "mtu-1"))
    async def wait_for(*args):
        waited.append(args)
        return {
            "type": "path_mtu", "schema": "xider.path-mtu.v1", "feature_id": "D04-F10",
            "device_id": "device-1", "request_id": "mtu-1", "source": "windows_ping_ipv4_df",
            "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "state": "supported", "data": {"target": "203.0.113.9", "mtu": 1428, "max_tested": False},
            "detail": None,
        }
    monkeypatch.setattr(bot, "path_mtu_collector", SimpleNamespace(wait_for=wait_for))
    class State:
        data = {}
        async def set_state(self, value): self.current = value
        async def update_data(self, **kwargs): self.data.update(kwargs)
        async def get_data(self): return dict(self.data)
        async def clear(self): self.data = {}
    state = State()
    cq = telegram.callback("cmd:path_mtu")
    asyncio.run(bot.on_cmd_path_mtu(cq, state))
    message = SimpleNamespace(from_user=SimpleNamespace(id=20), text="203.0.113.9",
                              chat=SimpleNamespace(id=10), message_id=30)
    asyncio.run(bot.on_path_mtu_input(message, state))
    owner_menu = bot.network_menu(user_id=20)
    guest_menu = bot.network_menu(user_id=21)
    owner_buttons = {button.callback_data: button.text for row in owner_menu.inline_keyboard for button in row}
    guest_callbacks = {button.callback_data for row in guest_menu.inline_keyboard for button in row}
    assert owner_buttons["cmd:path_mtu"] == "🧭 Диагностика MTU-пути"
    assert "cmd:path_mtu" not in guest_callbacks
    assert published == [(("path_mtu",), {"_target": "device-1", "target": "203.0.113.9"})]
    assert waited == [("device-1", "path_mtu", 20.0, "mtu-1")]
    assert _expected_callback_gate("cmd:path_mtu") == "OwnerFilter"
    assert list(telegram.cards) == [30] and not telegram.sent
    assert "1428" in telegram.cards[30]["text"]


def test_gateway_probe_is_owner_only_correlated_and_edits_one_card(monkeypatch, tmp_path):
    telegram = _card_telegram(monkeypatch, tmp_path)
    _setup(monkeypatch, {"device-1": {"name": "Laptop", "os": "Windows 11", "capabilities": {
        "commands": ["gateway_probe"],
    }}})
    monkeypatch.setattr(bot, "ADMIN_ID", 20)
    monkeypatch.setattr(bot, "SESSION", {"target": "device-1"})
    monkeypatch.setattr(bot, "get_user_role", lambda user_id: bot.Role.OWNER if user_id == 20 else bot.Role.GUEST)
    monkeypatch.setattr(bot.access_store, "can_use_callback", lambda *_args: True)
    published, waited = [], []
    monkeypatch.setattr(bot, "publish_tracked", lambda *args, **kwargs: published.append((args, kwargs)) or (True, "gateway-1"))

    async def wait_for(*args):
        waited.append(args)
        return {
            "type": "gateway_probe", "schema": "xider.gateway-probe.v1", "feature_id": "D04-F08",
            "device_id": "device-1", "request_id": "gateway-1",
            "source": "active_ipv4_default_route_and_single_icmp_echo", "state": "reachable",
            "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "data": {"gateway": "192.168.1.1"}, "detail": None,
        }

    monkeypatch.setattr(bot, "gateway_probe_collector", SimpleNamespace(wait_for=wait_for))
    async def answer(*_args, **_kwargs): pass
    async def edit_text(text, **_kwargs): telegram.cards[30]["text"] = text
    message = SimpleNamespace(chat=SimpleNamespace(id=10), message_id=30, edit_text=edit_text)
    cq = SimpleNamespace(from_user=SimpleNamespace(id=20), data="cmd:gateway_probe", answer=answer, message=message)
    asyncio.run(bot.on_cmd_gateway_probe(cq))

    owner_menu = bot.network_menu(user_id=20)
    guest_menu = bot.network_menu(user_id=21)
    owner_buttons = {button.callback_data: button.text for row in owner_menu.inline_keyboard for button in row}
    guest_callbacks = {button.callback_data for row in guest_menu.inline_keyboard for button in row}
    assert owner_buttons["cmd:gateway_probe"] == "🛜 Проверить шлюз"
    assert "cmd:gateway_probe" not in guest_callbacks
    assert _expected_callback_gate("cmd:gateway_probe") == "OwnerFilter"
    assert published == [(("gateway_probe",), {"_target": "device-1"})]
    assert waited == [("device-1", "gateway_probe", 9.0, "gateway-1")]
    assert list(telegram.cards) == [30] and not telegram.sent
    assert "192.168.1.1" in telegram.cards[30]["text"] and "отвечает" in telegram.cards[30]["text"]


def test_captive_portal_validator_accepts_only_correlated_sanitized_evidence():
    now = datetime.datetime.now(datetime.timezone.utc)
    result = {
        "type": "captive_portal", "schema": "xider.captive-portal.v1", "feature_id": "D04-F11",
        "device_id": "device-1", "request_id": "portal-1", "source": "http_connect_test",
        "state": "suspected", "checked_at": now.isoformat(),
        "data": {"portal_origin": "https://login.example"}, "detail": "redirected",
    }
    assert bot._validated_captive_portal(result, target="device-1", command_id="portal-1") == {
        "state": "suspected", "portal_origin": "https://login.example", "detail": "redirected",
        "checked_at": now.isoformat(),
    }
    leaked = {**result, "data": {"portal_origin": "https://login.example/login?token=secret"}}
    assert bot._validated_captive_portal(leaked, target="device-1", command_id="portal-1") is None
    mismatched = {**result, "request_id": "old-request"}
    assert bot._validated_captive_portal(mismatched, target="device-1", command_id="portal-1") is None


def test_captive_portal_is_owner_only_correlated_and_edits_one_card(monkeypatch, tmp_path):
    telegram = _card_telegram(monkeypatch, tmp_path)
    _setup(monkeypatch, {"device-1": {"name": "Laptop", "os": "Windows 11", "capabilities": {
        "commands": ["captive_portal"],
    }}})
    monkeypatch.setattr(bot, "ADMIN_ID", 20)
    monkeypatch.setattr(bot, "SESSION", {"target": "device-1"})
    monkeypatch.setattr(bot, "get_user_role", lambda user_id: bot.Role.OWNER if user_id == 20 else bot.Role.GUEST)
    monkeypatch.setattr(bot.access_store, "can_use_callback", lambda *_args: True)
    published, waited = [], []
    monkeypatch.setattr(bot, "publish_tracked", lambda *args, **kwargs: published.append((args, kwargs)) or (True, "portal-1"))

    async def wait_for(*args):
        waited.append(args)
        return {
            "type": "captive_portal", "schema": "xider.captive-portal.v1", "feature_id": "D04-F11",
            "device_id": "device-1", "request_id": "portal-1", "source": "http_connect_test",
            "state": "suspected", "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "data": {"portal_origin": "https://login.example"}, "detail": "redirected",
        }

    monkeypatch.setattr(bot, "captive_portal_collector", SimpleNamespace(wait_for=wait_for))
    async def answer(*_args, **_kwargs): pass
    async def edit_text(text, **_kwargs): telegram.cards[30]["text"] = text
    message = SimpleNamespace(chat=SimpleNamespace(id=10), message_id=30, edit_text=edit_text)
    cq = SimpleNamespace(from_user=SimpleNamespace(id=20), data="cmd:captive_portal", answer=answer, message=message)
    asyncio.run(bot.on_cmd_captive_portal(cq))

    owner_menu = bot.network_menu(user_id=20)
    guest_menu = bot.network_menu(user_id=21)
    owner_buttons = {button.callback_data: button.text for row in owner_menu.inline_keyboard for button in row}
    guest_callbacks = {button.callback_data for row in guest_menu.inline_keyboard for button in row}
    assert owner_buttons["cmd:captive_portal"] == "🛜 Проверить captive portal"
    assert "cmd:captive_portal" not in guest_callbacks
    assert _expected_callback_gate("cmd:captive_portal") == "OwnerFilter"
    assert published == [(("captive_portal",), {"_target": "device-1"})]
    assert waited == [("device-1", "captive_portal", 8.0, "portal-1")]
    assert list(telegram.cards) == [30] and not telegram.sent
    assert "Возможна" in telegram.cards[30]["text"] and "https://login.example" in telegram.cards[30]["text"]


def test_ipv6_connectivity_validator_requires_fresh_correlated_source_and_state():
    now = datetime.datetime.now(datetime.timezone.utc)
    result = {
        "type": "ipv6_connectivity", "schema": "xider.ipv6-connectivity.v1", "feature_id": "D04-F12",
        "device_id": "device-1", "request_id": "ipv6-1", "source": "cloudflare_ipv6_dot",
        "state": "reachable", "checked_at": now.isoformat(), "data": None, "detail": None,
    }
    assert bot._validated_ipv6_connectivity(result, target="device-1", command_id="ipv6-1")["state"] == "reachable"
    assert bot._validated_ipv6_connectivity({**result, "request_id": "stale"}, target="device-1", command_id="ipv6-1") is None
    assert bot._validated_ipv6_connectivity({**result, "source": "interface_address_only"}, target="device-1", command_id="ipv6-1") is None
    assert bot._validated_ipv6_connectivity({**result, "state": "reachable", "detail": "probe_failed"}, target="device-1", command_id="ipv6-1") is None


def test_ipv6_connectivity_is_owner_only_correlated_and_edits_one_card(monkeypatch, tmp_path):
    telegram = _card_telegram(monkeypatch, tmp_path)
    _setup(monkeypatch, {"device-1": {"name": "Laptop", "os": "Windows 11", "capabilities": {
        "commands": ["ipv6_connectivity"],
    }}})
    monkeypatch.setattr(bot, "ADMIN_ID", 20)
    monkeypatch.setattr(bot, "SESSION", {"target": "device-1"})
    monkeypatch.setattr(bot, "get_user_role", lambda user_id: bot.Role.OWNER if user_id == 20 else bot.Role.GUEST)
    monkeypatch.setattr(bot.access_store, "can_use_callback", lambda *_args: True)
    published, waited = [], []
    monkeypatch.setattr(bot, "publish_tracked", lambda *args, **kwargs: published.append((args, kwargs)) or (True, "ipv6-1"))

    async def wait_for(*args):
        waited.append(args)
        return {
            "type": "ipv6_connectivity", "schema": "xider.ipv6-connectivity.v1", "feature_id": "D04-F12",
            "device_id": "device-1", "request_id": "ipv6-1", "source": "cloudflare_ipv6_dot",
            "state": "reachable", "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "data": None, "detail": None,
        }

    monkeypatch.setattr(bot, "ipv6_connectivity_collector", SimpleNamespace(wait_for=wait_for))
    async def answer(*_args, **_kwargs): pass
    async def edit_text(text, **_kwargs): telegram.cards[30]["text"] = text
    message = SimpleNamespace(chat=SimpleNamespace(id=10), message_id=30, edit_text=edit_text)
    cq = SimpleNamespace(from_user=SimpleNamespace(id=20), data="cmd:ipv6_connectivity", answer=answer, message=message)
    asyncio.run(bot.on_cmd_ipv6_connectivity(cq))

    owner_menu = bot.network_menu(user_id=20)
    guest_menu = bot.network_menu(user_id=21)
    owner_buttons = {button.callback_data: button.text for row in owner_menu.inline_keyboard for button in row}
    guest_callbacks = {button.callback_data for row in guest_menu.inline_keyboard for button in row}
    assert owner_buttons["cmd:ipv6_connectivity"] == "🌐 Проверить IPv6"
    assert "cmd:ipv6_connectivity" not in guest_callbacks
    assert _expected_callback_gate("cmd:ipv6_connectivity") == "OwnerFilter"
    assert published == [(("ipv6_connectivity",), {"_target": "device-1"})]
    assert waited == [("device-1", "ipv6_connectivity", 8.0, "ipv6-1")]
    assert list(telegram.cards) == [30] and not telegram.sent
    assert "Рабочий IPv6-путь подтверждён" in telegram.cards[30]["text"]


def test_tls_certificate_input_and_validator_reject_urls_stale_or_untrusted_results():
    assert bot._parse_tls_certificate_input("example.com") == {"host": "example.com", "port": 443}
    assert bot._parse_tls_certificate_input("example.com:8443") == {"host": "example.com", "port": 8443}
    assert bot._parse_tls_certificate_input("[2001:4860:4860::8888]:443") == {"host": "2001:4860:4860::8888", "port": 443}
    for value in ("https://example.com", "example.com/path", "user@example.com", "example.com:0", "1.2.3.0/24"):
        assert bot._parse_tls_certificate_input(value) is None
    now = datetime.datetime.now(datetime.timezone.utc)
    result = {
        "type": "tls_certificate", "schema": "xider.tls-certificate.v1", "feature_id": "D04-F13",
        "device_id": "device-1", "request_id": "cert-1", "source": "verified_tls_certificate",
        "host": "example.com", "port": 443, "state": "valid", "checked_at": now.isoformat(),
        "detail": None, "data": {"not_before": (now - datetime.timedelta(days=1)).isoformat(),
                                  "not_after": (now + datetime.timedelta(days=20)).isoformat(), "sha256": "a" * 64},
    }
    assert bot._validated_tls_certificate(result, target="device-1", command_id="cert-1", host="example.com", port=443)["state"] == "valid"
    assert bot._validated_tls_certificate({**result, "request_id": "old"}, target="device-1", command_id="cert-1", host="example.com", port=443) is None
    assert bot._validated_tls_certificate({**result, "state": "valid", "detail": "certificate_chain_invalid"}, target="device-1", command_id="cert-1", host="example.com", port=443) is None


def test_tls_certificate_is_owner_only_correlated_and_replaces_the_current_card(monkeypatch, tmp_path):
    from types import SimpleNamespace

    telegram = _card_telegram(monkeypatch, tmp_path)
    _setup(monkeypatch, {"device-1": {"name": "Laptop", "os": "Windows 11", "capabilities": {
        "commands": ["tls_certificate"],
    }}})
    monkeypatch.setattr(bot, "ADMIN_ID", 20)
    monkeypatch.setattr(bot, "SESSION", {"target": "device-1"})
    monkeypatch.setattr(bot, "get_user_role", lambda uid: bot.Role.OWNER if uid == 20 else bot.Role.GUEST)
    monkeypatch.setattr(bot.access_store, "can_use_callback", lambda *_args: True)
    monkeypatch.setattr(bot, "_callback_result_access_or_report", lambda *_args: asyncio.sleep(0, result=True))
    monkeypatch.setattr(bot, "_pending_device_input_still_allowed", lambda *_args: True)
    published, waited = [], []
    monkeypatch.setattr(bot, "publish_tracked", lambda *args, **kwargs: published.append((args, kwargs)) or (True, "cert-1"))

    async def wait_for(*args):
        waited.append(args)
        now = datetime.datetime.now(datetime.timezone.utc)
        return {
            "type": "tls_certificate", "schema": "xider.tls-certificate.v1", "feature_id": "D04-F13",
            "device_id": "device-1", "request_id": "cert-1", "source": "verified_tls_certificate",
            "host": "example.com", "port": 443, "state": "valid", "checked_at": now.isoformat(),
            "detail": None, "data": {"not_before": (now - datetime.timedelta(days=1)).isoformat(),
                                      "not_after": (now + datetime.timedelta(days=20)).isoformat(), "sha256": "a" * 64},
        }

    monkeypatch.setattr(bot, "tls_certificate_collector", SimpleNamespace(wait_for=wait_for))
    class State:
        data = {}
        async def set_state(self, value): self.current = value
        async def update_data(self, **kwargs): self.data.update(kwargs)
        async def get_data(self): return dict(self.data)
        async def clear(self): self.data = {}
    state = State()
    async def answer(*_args, **_kwargs): pass
    cq = telegram.callback("cmd:tls_certificate")
    asyncio.run(bot.on_cmd_tls_certificate(cq, state))
    message = SimpleNamespace(from_user=SimpleNamespace(id=20), text="example.com", chat=SimpleNamespace(id=10), message_id=30)
    asyncio.run(bot.on_tls_certificate_input(message, state))

    owner_menu, guest_menu = bot.network_menu(user_id=20), bot.network_menu(user_id=21)
    owner_buttons = {button.callback_data: button.text for row in owner_menu.inline_keyboard for button in row}
    guest_callbacks = {button.callback_data for row in guest_menu.inline_keyboard for button in row}
    assert owner_buttons["cmd:tls_certificate"] == "🔐 Проверить TLS-сертификат"
    assert "cmd:tls_certificate" not in guest_callbacks
    assert _expected_callback_gate("cmd:tls_certificate") == "OwnerFilter"
    assert published == [(("tls_certificate",), {"_target": "device-1", "host": "example.com", "port": 443})]
    assert waited == [("device-1", "tls_certificate", 8.0, "cert-1")]
    assert list(telegram.cards) == [30] and not telegram.sent
    assert "Цепочка доверия подтверждена" in telegram.cards[30]["text"]


def test_service_probe_parser_and_validator_allow_only_one_correlated_endpoint():
    assert bot._parse_service_probe_input("tcp 192.168.1.1:22") == {
        "host": "192.168.1.1", "port": 22, "protocol": "tcp",
    }
    assert bot._parse_service_probe_input("TLS [2001:4860:4860::8888]:443") == {
        "host": "2001:4860:4860::8888", "port": 443, "protocol": "tls",
    }
    for value in ("udp example.com:53", "tcp https://example.com", "tls example.com/path", "tcp 10.0.0.0/8"):
        assert bot._parse_service_probe_input(value) is None
    result = {
        "type": "service_probe", "schema": "xider.service-probe.v1", "feature_id": "D04-F14",
        "device_id": "device-1", "request_id": "service-1", "host": "example.com", "port": 443,
        "protocol": "tls", "source": "single_endpoint_tcp_tls", "state": "reachable",
        "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat(), "detail": None,
    }
    assert bot._validated_service_probe(result, target="device-1", command_id="service-1",
                                       host="example.com", port=443, protocol="tls")["state"] == "reachable"
    untrusted = {**result, "state": "reachable_untrusted", "detail": "tls_certificate_untrusted"}
    assert bot._validated_service_probe(untrusted, target="device-1", command_id="service-1",
                                        host="example.com", port=443, protocol="tls")["state"] == "reachable_untrusted"
    assert bot._validated_service_probe({**result, "host": "other.example"}, target="device-1",
        command_id="service-1", host="example.com", port=443, protocol="tls") is None


def test_service_probe_is_owner_only_and_replaces_the_current_card(monkeypatch, tmp_path):
    from types import SimpleNamespace

    telegram = _card_telegram(monkeypatch, tmp_path)
    _setup(monkeypatch, {"device-1": {"name": "Laptop", "os": "Windows 11", "capabilities": {
        "commands": ["service_probe"],
    }}})
    monkeypatch.setattr(bot, "ADMIN_ID", 20)
    monkeypatch.setattr(bot, "SESSION", {"target": "device-1"})
    monkeypatch.setattr(bot, "get_user_role", lambda uid: bot.Role.OWNER if uid == 20 else bot.Role.GUEST)
    monkeypatch.setattr(bot.access_store, "can_use_callback", lambda *_args: True)
    monkeypatch.setattr(bot, "_callback_result_access_or_report", lambda *_args: asyncio.sleep(0, result=True))
    monkeypatch.setattr(bot, "_pending_device_input_still_allowed", lambda *_args: True)
    published, waited = [], []
    monkeypatch.setattr(bot, "publish_tracked", lambda *args, **kwargs: published.append((args, kwargs)) or (True, "service-1"))

    async def wait_for(*args):
        waited.append(args)
        return {
            "type": "service_probe", "schema": "xider.service-probe.v1", "feature_id": "D04-F14",
            "device_id": "device-1", "request_id": "service-1", "host": "example.com", "port": 443,
            "protocol": "tls", "source": "single_endpoint_tcp_tls", "state": "reachable",
            "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat(), "detail": None,
        }
    monkeypatch.setattr(bot, "service_probe_collector", SimpleNamespace(wait_for=wait_for))
    class State:
        data = {}
        async def set_state(self, value): self.current = value
        async def update_data(self, **kwargs): self.data.update(kwargs)
        async def get_data(self): return dict(self.data)
        async def clear(self): self.data = {}
    state = State()
    cq = telegram.callback("cmd:service_probe")
    asyncio.run(bot.on_cmd_service_probe(cq, state))
    message = SimpleNamespace(from_user=SimpleNamespace(id=20), text="tls example.com:443",
                              chat=SimpleNamespace(id=10), message_id=30)
    asyncio.run(bot.on_service_probe_input(message, state))

    owner_menu, guest_menu = bot.network_menu(user_id=20), bot.network_menu(user_id=21)
    owner_buttons = {button.callback_data: button.text for row in owner_menu.inline_keyboard for button in row}
    guest_callbacks = {button.callback_data for row in guest_menu.inline_keyboard for button in row}
    assert owner_buttons["cmd:service_probe"] == "🌐 Проверить сервис"
    assert "cmd:service_probe" not in guest_callbacks
    assert _expected_callback_gate("cmd:service_probe") == "OwnerFilter"
    assert published == [(("service_probe",), {"_target": "device-1", "host": "example.com", "port": 443, "protocol": "tls"})]
    assert waited == [("device-1", "service_probe", 8.0, "service-1")]
    assert list(telegram.cards) == [30] and not telegram.sent
    assert "TLS handshake завершён" in telegram.cards[30]["text"]


def test_wifi_quality_history_validator_requires_fresh_platform_metric():
    result = {
        "type": "wifi_quality_history", "schema": "xider.wifi-quality.v1", "feature_id": "D04-F15",
        "device_id": "device-1", "request_id": "wifi-1", "source": "windows_netsh_signal_percent",
        "state": "available", "metric": "signal_percent", "value": 83,
        "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat(), "detail": None,
    }
    parsed = bot._validated_wifi_quality_history(result, target="device-1", command_id="wifi-1")
    assert parsed["metric"] == "signal_percent" and parsed["value"] == 83
    assert bot._validated_wifi_quality_history({**result, "value": 150}, target="device-1", command_id="wifi-1") is None
    assert bot._validated_wifi_quality_history({**result, "source": "macos_ipconfig_agrctlrssi"}, target="device-1", command_id="wifi-1") is None
    assert bot._validated_wifi_quality_history({**result, "ssid": "private"}, target="device-1", command_id="wifi-1") is None


def test_wifi_quality_history_is_owner_only_persists_one_sample_and_edits_card(monkeypatch, tmp_path):
    from types import SimpleNamespace

    telegram = _card_telegram(monkeypatch, tmp_path)
    _setup(monkeypatch, {"device-1": {"name": "Laptop", "os": "Windows 11", "capabilities": {
        "commands": ["wifi_quality_history"],
    }}})
    monkeypatch.setattr(bot, "ADMIN_ID", 20)
    monkeypatch.setattr(bot, "SESSION", {"target": "device-1"})
    monkeypatch.setattr(bot, "get_user_role", lambda uid: bot.Role.OWNER if uid == 20 else bot.Role.GUEST)
    monkeypatch.setattr(bot.access_store, "can_use_callback", lambda *_args: True)
    monkeypatch.setattr(bot, "_callback_result_access_or_report", lambda *_args: asyncio.sleep(0, result=True))
    monkeypatch.setattr(bot, "_pending_device_input_still_allowed", lambda *_args: True)
    captured, published = [], []
    monkeypatch.setattr(bot.devices, "record_wifi_quality_sample", lambda *args: captured.append(args) or {"captured_at": 1, "source": "windows_netsh_signal_percent", "metric": "signal_percent", "value": 83}, raising=False)
    monkeypatch.setattr(bot.devices, "list_wifi_quality_samples", lambda *_args: [
        {"captured_at": 1_800_000_000, "source": "windows_netsh_signal_percent", "metric": "signal_percent", "value": 72},
        {"captured_at": 1_800_000_060, "source": "windows_netsh_signal_percent", "metric": "signal_percent", "value": 83},
    ], raising=False)
    monkeypatch.setattr(bot, "publish_tracked", lambda *args, **kwargs: published.append((args, kwargs)) or (True, "wifi-1"))

    async def wait_for(*_args):
        return {
            "type": "wifi_quality_history", "schema": "xider.wifi-quality.v1", "feature_id": "D04-F15",
            "device_id": "device-1", "request_id": "wifi-1", "source": "windows_netsh_signal_percent",
            "state": "available", "metric": "signal_percent", "value": 83,
            "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat(), "detail": None,
        }
    monkeypatch.setattr(bot, "wifi_quality_history_collector", SimpleNamespace(wait_for=wait_for))

    asyncio.run(bot.on_cmd_wifi_quality_history(telegram.callback("cmd:wifi_quality_history")))
    owner_menu, guest_menu = bot.network_menu(user_id=20), bot.network_menu(user_id=21)
    owner_buttons = {b.callback_data: b.text for row in owner_menu.inline_keyboard for b in row}
    guest_callbacks = {b.callback_data for row in guest_menu.inline_keyboard for b in row}
    assert owner_buttons["cmd:wifi_quality_history"] == "📈 Тренд Wi-Fi"
    assert "cmd:wifi_quality_history" not in guest_callbacks
    assert _expected_callback_gate("cmd:wifi_quality_history") == "OwnerFilter"
    assert published == [(("wifi_quality_history",), {"_target": "device-1"})]
    assert captured and captured[0][0:3] == (20, 20, "device-1")
    assert list(telegram.cards) == [30] and not telegram.sent
    assert "72%" in telegram.cards[30]["text"] and "83%" in telegram.cards[30]["text"]


def test_wifi_channel_summary_validator_rejects_bad_fields_and_preserves_unknown_width():
    result = {
        "type": "wifi_channel_summary", "schema": "xider.wifi-channel.v1", "feature_id": "D04-F16",
        "device_id": "device-1", "request_id": "channel-1", "source": "windows_netsh_wlan_channel",
        "state": "available", "channel": 149, "width_mhz": None, "width_state": "unsupported",
        "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat(), "detail": None,
    }
    parsed = bot._validated_wifi_channel_summary(result, target="device-1", command_id="channel-1")
    assert parsed["channel"] == 149 and parsed["width_state"] == "unsupported"
    assert bot._validated_wifi_channel_summary({**result, "channel": 999}, target="device-1", command_id="channel-1") is None
    assert bot._validated_wifi_channel_summary({**result, "width_mhz": 80}, target="device-1", command_id="channel-1") is None
    assert bot._validated_wifi_channel_summary({**result, "ssid": "private"}, target="device-1", command_id="channel-1") is None


def test_proxy_status_validator_accepts_only_correlated_redacted_categories():
    result = {
        "type": "proxy_status", "schema": "xider.proxy-status.v1", "feature_id": "D04-F23",
        "device_id": "device-1", "request_id": "proxy-1", "source": "windows_proxy_flags",
        "state": "available", "sources": ["current_user_manual", "winhttp"],
        "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat(), "detail": None,
    }
    parsed = bot._validated_proxy_status(result, target="device-1", command_id="proxy-1")
    assert parsed["sources"] == ["current_user_manual", "winhttp"]
    assert bot._validated_proxy_status({**result, "request_id": "other"}, target="device-1", command_id="proxy-1") is None
    assert bot._validated_proxy_status({**result, "sources": ["private.proxy.local"]}, target="device-1", command_id="proxy-1") is None
    assert bot._validated_proxy_status({**result, "endpoint": "http://user:secret@proxy.local"}, target="device-1", command_id="proxy-1") is None


def test_proxy_status_is_owner_only_and_edits_the_current_card(monkeypatch, tmp_path):
    from types import SimpleNamespace

    telegram = _card_telegram(monkeypatch, tmp_path)
    _setup(monkeypatch, {"device-1": {"name": "Laptop", "os": "Windows 11", "capabilities": {
        "commands": ["proxy_status"],
    }}})
    monkeypatch.setattr(bot, "ADMIN_ID", 20)
    monkeypatch.setattr(bot, "SESSION", {"target": "device-1"})
    monkeypatch.setattr(bot, "get_user_role", lambda uid: bot.Role.OWNER if uid == 20 else bot.Role.GUEST)
    monkeypatch.setattr(bot, "_callback_result_access_or_report", lambda *_args: asyncio.sleep(0, result=True))
    published, waited = [], []
    monkeypatch.setattr(bot, "publish_tracked", lambda *args, **kwargs: published.append((args, kwargs)) or (True, "proxy-1"))

    async def wait_for(*args):
        waited.append(args)
        return {
            "type": "proxy_status", "schema": "xider.proxy-status.v1", "feature_id": "D04-F23",
            "device_id": "device-1", "request_id": "proxy-1", "source": "windows_proxy_flags",
            "state": "available", "sources": ["current_user_manual", "winhttp"],
            "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat(), "detail": None,
        }
    monkeypatch.setattr(bot, "proxy_status_collector", SimpleNamespace(wait_for=wait_for), raising=False)

    asyncio.run(bot.on_cmd_proxy_status(telegram.callback("cmd:proxy_status")))
    owner_menu, guest_menu = bot.network_menu(user_id=20), bot.network_menu(user_id=21)
    owner_buttons = {b.callback_data: b.text for row in owner_menu.inline_keyboard for b in row}
    guest_callbacks = {b.callback_data for row in guest_menu.inline_keyboard for b in row}
    assert owner_buttons["cmd:proxy_status"] == "🛡 Proxy-настройки"
    assert "cmd:proxy_status" not in guest_callbacks
    assert _expected_callback_gate("cmd:proxy_status") == "OwnerFilter"
    assert published == [(("proxy_status",), {"_target": "device-1"})]
    assert waited == [("device-1", "proxy_status", 8.0, "proxy-1")]
    assert list(telegram.cards) == [30] and not telegram.sent
    assert "текущего пользователя" in telegram.cards[30]["text"]
    assert "proxy.local" not in telegram.cards[30]["text"]


def test_vpn_route_validator_requires_correlated_aggregate_and_fresh_response():
    result = {
        "type": "vpn_route_status", "schema": "xider.vpn-route.v1", "feature_id": "D04-F24",
        "device_id": "device-1", "request_id": "vpn-1", "source": "windows_interface_hints",
        "state": "available", "up_tunnel_candidates": 1, "down_tunnel_candidates": 0,
        "route_assessment": "not_verified",
        "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat(), "detail": None,
    }
    parsed = bot._validated_vpn_route_status(result, target="device-1", command_id="vpn-1")
    assert parsed["up_tunnel_candidates"] == 1
    assert parsed["route_assessment"] == "not_verified"
    assert bot._validated_vpn_route_status({**result, "request_id": "other"}, target="device-1", command_id="vpn-1") is None
    assert bot._validated_vpn_route_status({**result, "interface_names": ["private-vpn"]}, target="device-1", command_id="vpn-1") is None
    assert bot._validated_vpn_route_status({**result, "up_tunnel_candidates": 999}, target="device-1", command_id="vpn-1") is None
    assert bot._validated_vpn_route_status({**result, "route_assessment": "all_traffic_protected"}, target="device-1", command_id="vpn-1") is None


def test_vpn_route_status_is_owner_only_and_edits_the_current_card(monkeypatch, tmp_path):
    from types import SimpleNamespace

    telegram = _card_telegram(monkeypatch, tmp_path)
    _setup(monkeypatch, {"device-1": {"name": "Laptop", "os": "Windows 11", "capabilities": {
        "commands": ["vpn_route_status"],
    }}})
    monkeypatch.setattr(bot, "ADMIN_ID", 20)
    monkeypatch.setattr(bot, "SESSION", {"target": "device-1"})
    monkeypatch.setattr(bot, "get_user_role", lambda uid: bot.Role.OWNER if uid == 20 else bot.Role.GUEST)
    monkeypatch.setattr(bot, "_callback_result_access_or_report", lambda *_args: asyncio.sleep(0, result=True))
    published, waited = [], []
    monkeypatch.setattr(bot, "publish_tracked", lambda *args, **kwargs: published.append((args, kwargs)) or (True, "vpn-1"))

    async def wait_for(*args):
        waited.append(args)
        return {
            "type": "vpn_route_status", "schema": "xider.vpn-route.v1", "feature_id": "D04-F24",
            "device_id": "device-1", "request_id": "vpn-1", "source": "windows_interface_hints",
            "state": "available", "up_tunnel_candidates": 1, "down_tunnel_candidates": 0,
            "route_assessment": "not_verified",
            "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat(), "detail": None,
        }
    monkeypatch.setattr(bot, "vpn_route_status_collector", SimpleNamespace(wait_for=wait_for), raising=False)

    asyncio.run(bot.on_cmd_vpn_route_status(telegram.callback("cmd:vpn_route_status")))
    owner_menu, guest_menu = bot.network_menu(user_id=20), bot.network_menu(user_id=21)
    owner_buttons = {b.callback_data: b.text for row in owner_menu.inline_keyboard for b in row}
    guest_callbacks = {b.callback_data for row in guest_menu.inline_keyboard for b in row}
    assert owner_buttons["cmd:vpn_route_status"] == "🧭 VPN-маршрут"
    assert "cmd:vpn_route_status" not in guest_callbacks
    assert _expected_callback_gate("cmd:vpn_route_status") == "OwnerFilter"
    assert published == [(("vpn_route_status",), {"_target": "device-1"})]
    assert waited == [("device-1", "vpn_route_status", 8.0, "vpn-1")]
    assert list(telegram.cards) == [30] and not telegram.sent
    assert "маршрут и защита всего трафика не проверены" in telegram.cards[30]["text"].lower()


def test_endpoint_batch_validator_accepts_only_the_fixed_correlated_result_set():
    result = {
        "type": "endpoint_batch", "schema": "xider.endpoint-batch.v1", "feature_id": "D04-F25",
        "device_id": "device-1", "request_id": "batch-1", "source": "fixed_public_tcp_endpoints",
        "preset": "public_basics", "state": "available", "results": [
            {"target_id": "example", "state": "reachable", "detail": None},
            {"target_id": "cloudflare", "state": "unknown", "detail": "probe_timeout"},
            {"target_id": "github", "state": "unreachable", "detail": "connection_refused"},
        ], "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }
    parsed = bot._validated_endpoint_batch(result, target="device-1", command_id="batch-1")
    assert [item["target_id"] for item in parsed["results"]] == ["example", "cloudflare", "github"]
    assert bot._validated_endpoint_batch({**result, "request_id": "other"}, target="device-1", command_id="batch-1") is None
    assert bot._validated_endpoint_batch({**result, "results": result["results"][:2]}, target="device-1", command_id="batch-1") is None
    assert bot._validated_endpoint_batch({**result, "results": [{**result["results"][0], "host": "private.test"}, *result["results"][1:]]}, target="device-1", command_id="batch-1") is None
    assert bot._validated_endpoint_batch({**result, "results": [{**result["results"][0], "target_id": "private.test"}, *result["results"][1:]]}, target="device-1", command_id="batch-1") is None


def test_endpoint_batch_is_owner_only_fixed_and_edits_the_current_card(monkeypatch, tmp_path):
    from types import SimpleNamespace

    telegram = _card_telegram(monkeypatch, tmp_path)
    _setup(monkeypatch, {"device-1": {"name": "Laptop", "os": "Windows 11", "capabilities": {
        "commands": ["endpoint_batch"],
    }}})
    monkeypatch.setattr(bot, "ADMIN_ID", 20)
    monkeypatch.setattr(bot, "SESSION", {"target": "device-1"})
    monkeypatch.setattr(bot, "get_user_role", lambda uid: bot.Role.OWNER if uid == 20 else bot.Role.GUEST)
    monkeypatch.setattr(bot, "_callback_result_access_or_report", lambda *_args: asyncio.sleep(0, result=True))
    published, waited = [], []
    monkeypatch.setattr(bot, "publish_tracked", lambda *args, **kwargs: published.append((args, kwargs)) or (True, "batch-1"))

    async def wait_for(*args):
        waited.append(args)
        return {
            "type": "endpoint_batch", "schema": "xider.endpoint-batch.v1", "feature_id": "D04-F25",
            "device_id": "device-1", "request_id": "batch-1", "source": "fixed_public_tcp_endpoints",
            "preset": "public_basics", "state": "available", "results": [
                {"target_id": "example", "state": "reachable", "detail": None},
                {"target_id": "cloudflare", "state": "unknown", "detail": "probe_timeout"},
                {"target_id": "github", "state": "unreachable", "detail": "connection_refused"},
            ], "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        }
    monkeypatch.setattr(bot, "endpoint_batch_collector", SimpleNamespace(wait_for=wait_for), raising=False)

    asyncio.run(bot.on_cmd_endpoint_batch(telegram.callback("cmd:endpoint_batch")))
    owner_menu, guest_menu = bot.network_menu(user_id=20), bot.network_menu(user_id=21)
    owner_buttons = {b.callback_data: b.text for row in owner_menu.inline_keyboard for b in row}
    guest_callbacks = {b.callback_data for row in guest_menu.inline_keyboard for b in row}
    assert owner_buttons["cmd:endpoint_batch"] == "🌐 Набор endpoints"
    assert "cmd:endpoint_batch" not in guest_callbacks
    assert _expected_callback_gate("cmd:endpoint_batch") == "OwnerFilter"
    assert published == [(("endpoint_batch",), {"_target": "device-1"})]
    assert waited == [("device-1", "endpoint_batch", 12.0, "batch-1")]
    assert list(telegram.cards) == [30] and not telegram.sent
    assert "example.com:443" in telegram.cards[30]["text"]
    assert "www.cloudflare.com:443" in telegram.cards[30]["text"]
    assert "github.com:443" in telegram.cards[30]["text"]
    assert "private.test" not in telegram.cards[30]["text"]


def test_path_diagnostic_labels_only_confirmed_failures():
    uncertain = bot._format_path_diagnostic(
        gateway={"state": "unknown"}, dns={"state": "unknown"},
        route={"state": "partial"}, endpoints={"results": [
            {"target_id": "example", "state": "unknown", "detail": "probe_timeout"},
            {"target_id": "cloudflare", "state": "unknown", "detail": "probe_timeout"},
            {"target_id": "github", "state": "unknown", "detail": "probe_timeout"},
        ]},
    )
    assert "Подтверждённый отказ: не найден" in uncertain
    assert "тайм-аут" in uncertain.lower() or "неизвест" in uncertain.lower()

    no_route = bot._format_path_diagnostic(
        gateway={"state": "no_gateway"}, dns=None, route=None, endpoints=None,
    )
    assert "Подтверждённый отказ: IPv4-маршрут по умолчанию отсутствует" in no_route
    assert "DNS" in no_route and "не проверен" in no_route


def test_network_path_report_is_editable_json_with_allowlisted_fields_only():
    build_report = getattr(bot, "_build_network_path_report_json", None)
    assert callable(build_report), "D04-F28 JSON report builder is missing"
    payload = build_report(
        device_id="device-1", checked_at="2026-10-20T12:00:00+00:00",
        gateway={"state": "reachable", "gateway": "192.168.1.1", "wifi_key": "do-not-export"},
        dns={"state": "supported", "addresses": ["203.0.113.8"], "cookie": "do-not-export"},
        route={"state": "partial", "hops": [{"address": "10.0.0.1"}], "auth_token": "do-not-export"},
        endpoints={"results": [
            {"target_id": "example", "state": "reachable", "detail": None},
            {"target_id": "cloudflare", "state": "unknown", "detail": "probe_timeout"},
            {"target_id": "github", "state": "unreachable", "detail": "connection_refused"},
            {"target_id": "unexpected", "state": "reachable", "detail": None},
        ]},
    )

    report = json.loads(payload.decode("utf-8"))
    assert report == {
        "schema": "xider.network-diagnostic-report.v1",
        "feature_id": "D04-F28",
        "device_id": "device-1",
        "checked_at": "2026-10-20T12:00:00+00:00",
        "checks": {
            "gateway": "reachable", "dns": "supported", "route": "partial",
            "endpoints": {"example": "reachable", "cloudflare": "unknown", "github": "unreachable"},
        },
        "confirmed_failure": "tcp_refused:github",
    }
    for secret in (b"do-not-export", b"wifi_key", b"cookie", b"auth_token", b"192.168.1.1", b"203.0.113.8", b"10.0.0.1"):
        assert secret not in payload


def test_network_path_report_export_button_is_one_use_action():
    build_keyboard = getattr(bot, "_network_path_report_keyboard", None)
    assert callable(build_keyboard), "D04-F28 export button is missing"
    keyboard = build_keyboard("0123456789abcdef")
    callbacks = _callback_data(keyboard)
    assert callbacks[0] == "network-report:export:0123456789abcdef"
    assert callbacks[1] == "back:device"


def test_network_path_report_export_is_owner_bound_and_consumed_once(monkeypatch, tmp_path):
    telegram = _card_telegram(monkeypatch, tmp_path)
    monkeypatch.setattr(bot, "SESSION", {"target": "device-1"})
    monkeypatch.setattr(bot, "ADMIN_ID", 20)
    monkeypatch.setattr(bot, "get_user_role", lambda _uid: bot.Role.OWNER)
    monkeypatch.setattr(bot, "_callback_result_access_or_report", lambda *_args, **_kwargs: asyncio.sleep(0, result=True))
    token = "0123456789abcdef"
    monkeypatch.setattr(bot, "_NETWORK_PATH_REPORT_EXPORTS", {token: {
        "owner_id": 20, "device_id": "device-1", "chat_id": 10, "message_id": 30,
        "expires_at": time.monotonic() + 30, "payload": b'{"schema":"xider.network-diagnostic-report.v1"}',
        "checked_at": "2026-10-20T12:00:00+00:00",
    }})
    sent = []
    async def replace(_cq, sender, **kwargs):
        sent.append((sender, kwargs))
    monkeypatch.setattr(bot, "_replace_callback_with_media", replace)

    cq = telegram.callback(f"network-report:export:{token}")
    asyncio.run(bot.on_network_path_report_export(cq))
    assert len(sent) == 1
    assert sent[0][0] == bot.bot.send_document
    assert sent[0][1]["document"].filename.startswith("xider-network-report-")
    assert token not in bot._NETWORK_PATH_REPORT_EXPORTS

    asyncio.run(bot.on_network_path_report_export(cq))
    assert len(sent) == 1


def test_path_diagnostic_runs_fixed_correlated_probes_and_edits_one_card(monkeypatch, tmp_path):
    from types import SimpleNamespace

    telegram = _card_telegram(monkeypatch, tmp_path)
    commands = ["gateway_probe", "dns_resolution", "route_trace", "endpoint_batch"]
    _setup(monkeypatch, {"device-1": {"name": "Laptop", "os": "Windows 11", "capabilities": {
        "commands": commands,
    }}})
    monkeypatch.setattr(bot, "ADMIN_ID", 20)
    monkeypatch.setattr(bot, "SESSION", {"target": "device-1"})
    monkeypatch.setattr(bot, "get_user_role", lambda uid: bot.Role.OWNER if uid == 20 else bot.Role.GUEST)
    monkeypatch.setattr(bot, "_callback_result_access_or_report", lambda *_args, **_kwargs: asyncio.sleep(0, result=True))
    published = []
    monkeypatch.setattr(bot, "publish_tracked", lambda *args, **kwargs: (
        published.append((args, kwargs)) or (True, {"gateway_probe": "gw-1", "dns_resolution": "dns-1",
                                                     "route_trace": "route-1", "endpoint_batch": "end-1"}[args[0]])
    ))

    responses = {
        "gateway_probe": {"type": "gateway_probe", "schema": "xider.gateway-probe.v1", "feature_id": "D04-F08",
            "device_id": "device-1", "request_id": "gw-1", "source": "active_ipv4_default_route_and_single_icmp_echo",
            "state": "reachable", "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "data": {"gateway": "192.168.1.1"}, "detail": None},
        "dns_resolution": {"type": "dns_resolution", "schema": "xider.dns-resolution.v1", "feature_id": "D04-F05",
            "device_id": "device-1", "request_id": "dns-1", "source": "system_getaddrinfo", "state": "supported",
            "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "data": {"hostname": "example.com", "addresses": ["93.184.216.34"], "truncated": False, "expected_match": None}, "detail": None},
        "route_trace": {"type": "route_trace", "schema": "xider.route-trace.v1", "feature_id": "D04-F09",
            "device_id": "device-1", "request_id": "route-1", "source": "windows_tracert_icmp", "state": "complete",
            "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "data": {"target": "1.1.1.1", "reached": True, "hops": [{"index": 1, "address": "1.1.1.1"}]}, "detail": None},
        "endpoint_batch": {"type": "endpoint_batch", "schema": "xider.endpoint-batch.v1", "feature_id": "D04-F25",
            "device_id": "device-1", "request_id": "end-1", "source": "fixed_public_tcp_endpoints", "preset": "public_basics",
            "state": "available", "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "results": [{"target_id": name, "state": "reachable", "detail": None}
                        for name in ("example", "cloudflare", "github")]},
    }
    waited = []
    for command in commands:
        async def wait_for(*args, _command=command):
            waited.append((_command, args))
            return responses[_command]
        monkeypatch.setattr(bot, {
            "gateway_probe": "gateway_probe_collector", "dns_resolution": "dns_resolution_collector",
            "route_trace": "route_trace_collector", "endpoint_batch": "endpoint_batch_collector",
        }[command], SimpleNamespace(wait_for=wait_for), raising=False)

    asyncio.run(bot.on_cmd_path_diagnostic(telegram.callback("cmd:path_diagnostic")))

    assert [row[0][0] for row in published] == commands
    assert len(waited) == 4
    assert list(telegram.cards) == [30] and not telegram.sent
    text = telegram.cards[30]["text"]
    assert all(label in text for label in ("Шлюз", "DNS", "Маршрут", "Endpoints"))
    assert "Подтверждённый отказ: не найден" in text
    assert _expected_callback_gate("cmd:path_diagnostic") == "OwnerFilter"


def test_dnssec_input_parser_and_result_validator_reject_bad_or_unbound_data():
    assert bot._parse_dnssec_check_input("Example.com | 1.1.1.1") == {
        "hostname": "example.com", "resolver": "1.1.1.1",
    }
    for value in ("localhost | 1.1.1.1", "example.com | 224.0.0.1", "example.com | dns.example",
                  "example.com | 1.1.1.1 | 8.8.8.8", "bad_domain | 1.1.1.1"):
        assert bot._parse_dnssec_check_input(value) is None
    result = {
        "type": "dnssec_check", "schema": "xider.dnssec-check.v1", "feature_id": "D04-F26",
        "device_id": "device-1", "request_id": "dnssec-1", "state": "validated_by_resolver",
        "source": "edns_do_ad_from_configured_resolver",
        "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "hostname": "example.com", "resolver": "1.1.1.1", "ad_bit": True, "rcode": "NOERROR",
    }
    assert bot._validated_dnssec_check(result, target="device-1", command_id="dnssec-1",
                                       hostname="example.com", resolver="1.1.1.1")["state"] == "validated_by_resolver"
    assert bot._validated_dnssec_check({**result, "request_id": "stale"}, target="device-1", command_id="dnssec-1",
                                       hostname="example.com", resolver="1.1.1.1") is None
    assert bot._validated_dnssec_check({**result, "ad_bit": False}, target="device-1", command_id="dnssec-1",
                                       hostname="example.com", resolver="1.1.1.1") is None


def test_dnssec_check_is_owner_only_and_edits_current_card(monkeypatch, tmp_path):
    from types import SimpleNamespace

    telegram = _card_telegram(monkeypatch, tmp_path)
    _setup(monkeypatch, {"device-1": {"name": "Laptop", "os": "Windows 11", "capabilities": {
        "commands": ["dnssec_check"],
    }}})
    monkeypatch.setattr(bot, "ADMIN_ID", 20)
    monkeypatch.setattr(bot, "SESSION", {"target": "device-1"})
    monkeypatch.setattr(bot, "get_user_role", lambda uid: bot.Role.OWNER if uid == 20 else bot.Role.GUEST)
    monkeypatch.setattr(bot, "_callback_result_access_or_report", lambda *_args: asyncio.sleep(0, result=True))
    monkeypatch.setattr(bot, "_pending_device_input_still_allowed", lambda *_args: True)
    published, waited = [], []
    monkeypatch.setattr(bot, "publish_tracked", lambda *args, **kwargs: published.append((args, kwargs)) or (True, "dnssec-1"))

    async def wait_for(*args):
        waited.append(args)
        return {
            "type": "dnssec_check", "schema": "xider.dnssec-check.v1", "feature_id": "D04-F26",
            "device_id": "device-1", "request_id": "dnssec-1", "state": "validated_by_resolver",
            "source": "edns_do_ad_from_configured_resolver",
            "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "hostname": "example.com", "resolver": "1.1.1.1", "ad_bit": True, "rcode": "NOERROR",
        }
    monkeypatch.setattr(bot, "dnssec_check_collector", SimpleNamespace(wait_for=wait_for), raising=False)

    class State:
        def __init__(self): self.data = {}
        async def set_state(self, _value): pass
        async def update_data(self, **values): self.data.update(values)
        async def get_data(self): return dict(self.data)
        async def clear(self): self.data.clear()
    state = State()
    asyncio.run(bot.on_cmd_dnssec_check(telegram.callback("cmd:dnssec_check"), state))
    message = telegram.message(message_id=46, chat_id=10, user_id=20)
    message.text = "example.com | 1.1.1.1"
    asyncio.run(bot.on_dnssec_check_input(message, state))

    owner_menu, guest_menu = bot.network_menu(user_id=20), bot.network_menu(user_id=21)
    owner_buttons = {b.callback_data: b.text for row in owner_menu.inline_keyboard for b in row}
    guest_callbacks = {b.callback_data for row in guest_menu.inline_keyboard for b in row}
    assert owner_buttons["cmd:dnssec_check"] == "🔐 Проверить DNSSEC"
    assert "cmd:dnssec_check" not in guest_callbacks
    assert _expected_callback_gate("cmd:dnssec_check") == "OwnerFilter"
    assert published == [(("dnssec_check",), {"_target": "device-1", "hostname": "example.com", "resolver": "1.1.1.1"})]
    assert waited == [("device-1", "dnssec_check", 9.0, "dnssec-1")]
    assert list(telegram.cards) == [30] and not telegram.sent
    assert "AD=1" in telegram.cards[30]["text"] and "не самостоятельная криптопроверка клиента" in telegram.cards[30]["text"]


def test_wifi_channel_summary_is_owner_only_correlated_and_replaces_the_current_card(monkeypatch, tmp_path):
    from types import SimpleNamespace

    telegram = _card_telegram(monkeypatch, tmp_path)
    _setup(monkeypatch, {"device-1": {"name": "Laptop", "os": "Windows 11", "capabilities": {
        "commands": ["wifi_channel_summary"],
    }}})
    monkeypatch.setattr(bot, "ADMIN_ID", 20)
    monkeypatch.setattr(bot, "SESSION", {"target": "device-1"})
    monkeypatch.setattr(bot, "get_user_role", lambda uid: bot.Role.OWNER if uid == 20 else bot.Role.GUEST)
    monkeypatch.setattr(bot, "_callback_result_access_or_report", lambda *_args: asyncio.sleep(0, result=True))
    published, waited = [], []
    monkeypatch.setattr(bot, "publish_tracked", lambda *args, **kwargs: published.append((args, kwargs)) or (True, "channel-1"))

    async def wait_for(*args):
        waited.append(args)
        return {
            "type": "wifi_channel_summary", "schema": "xider.wifi-channel.v1", "feature_id": "D04-F16",
            "device_id": "device-1", "request_id": "channel-1", "source": "windows_netsh_wlan_channel",
            "state": "available", "channel": 149, "width_mhz": 80, "width_state": "available",
            "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat(), "detail": None,
        }
    monkeypatch.setattr(bot, "wifi_channel_summary_collector", SimpleNamespace(wait_for=wait_for), raising=False)

    asyncio.run(bot.on_cmd_wifi_channel_summary(telegram.callback("cmd:wifi_channel_summary")))
    owner_menu, guest_menu = bot.network_menu(user_id=20), bot.network_menu(user_id=21)
    owner_buttons = {b.callback_data: b.text for row in owner_menu.inline_keyboard for b in row}
    guest_callbacks = {b.callback_data for row in guest_menu.inline_keyboard for b in row}
    assert owner_buttons["cmd:wifi_channel_summary"] == "📡 Канал Wi-Fi"
    assert "cmd:wifi_channel_summary" not in guest_callbacks
    assert _expected_callback_gate("cmd:wifi_channel_summary") == "OwnerFilter"
    assert published == [(("wifi_channel_summary",), {"_target": "device-1"})]
    assert waited == [("device-1", "wifi_channel_summary", 8.0, "channel-1")]
    assert list(telegram.cards) == [30] and not telegram.sent
    assert "149" in telegram.cards[30]["text"] and "80 МГц" in telegram.cards[30]["text"]


def test_wifi_connection_history_validator_accepts_only_fresh_redacted_events():
    now = datetime.datetime.now(datetime.timezone.utc)
    result = {
        "type": "wifi_connection_history", "schema": "xider.wifi-connection-history.v1",
        "feature_id": "D04-F17", "device_id": "device-1", "request_id": "history-1",
        "source": "windows_wlan_autoconfig_events", "state": "available",
        "events": [{"at": (now - datetime.timedelta(minutes=5)).isoformat(), "kind": "connected"}],
        "checked_at": now.isoformat(), "detail": None,
    }
    parsed = bot._validated_wifi_connection_history(result, target="device-1", command_id="history-1")
    assert parsed["events"][0]["kind"] == "connected"
    assert bot._validated_wifi_connection_history({**result, "events": [{"at": now.isoformat(), "kind": "connected", "ssid": "private"}]}, target="device-1", command_id="history-1") is None
    assert bot._validated_wifi_connection_history({**result, "events": [{"at": now.isoformat(), "kind": "connected"}] * 21}, target="device-1", command_id="history-1") is None
    assert bot._validated_wifi_connection_history({**result, "request_id": "other"}, target="device-1", command_id="history-1") is None


def test_wifi_connection_history_is_owner_only_and_edits_the_same_card(monkeypatch, tmp_path):
    from types import SimpleNamespace

    telegram = _card_telegram(monkeypatch, tmp_path)
    _setup(monkeypatch, {"device-1": {"name": "Laptop", "os": "Windows 11", "capabilities": {
        "commands": ["wifi_connection_history"],
    }}})
    monkeypatch.setattr(bot, "ADMIN_ID", 20)
    monkeypatch.setattr(bot, "SESSION", {"target": "device-1"})
    monkeypatch.setattr(bot, "get_user_role", lambda uid: bot.Role.OWNER if uid == 20 else bot.Role.GUEST)
    monkeypatch.setattr(bot, "_callback_result_access_or_report", lambda *_args: asyncio.sleep(0, result=True))
    published, waited = [], []
    monkeypatch.setattr(bot, "publish_tracked", lambda *args, **kwargs: published.append((args, kwargs)) or (True, "history-1"))

    async def wait_for(*args):
        waited.append(args)
        now = datetime.datetime.now(datetime.timezone.utc)
        return {
            "type": "wifi_connection_history", "schema": "xider.wifi-connection-history.v1",
            "feature_id": "D04-F17", "device_id": "device-1", "request_id": "history-1",
            "source": "windows_wlan_autoconfig_events", "state": "available",
            "events": [{"at": (now - datetime.timedelta(minutes=5)).isoformat(), "kind": "connected"}],
            "checked_at": now.isoformat(), "detail": None,
        }
    monkeypatch.setattr(bot, "wifi_connection_history_collector", SimpleNamespace(wait_for=wait_for), raising=False)

    asyncio.run(bot.on_cmd_wifi_connection_history(telegram.callback("cmd:wifi_connection_history")))
    owner_menu, guest_menu = bot.network_menu(user_id=20), bot.network_menu(user_id=21)
    owner_buttons = {b.callback_data: b.text for row in owner_menu.inline_keyboard for b in row}
    guest_callbacks = {b.callback_data for row in guest_menu.inline_keyboard for b in row}
    assert owner_buttons["cmd:wifi_connection_history"] == "🧾 История Wi-Fi"
    assert "cmd:wifi_connection_history" not in guest_callbacks
    assert _expected_callback_gate("cmd:wifi_connection_history") == "OwnerFilter"
    assert published == [(("wifi_connection_history",), {"_target": "device-1"})]
    assert waited == [("device-1", "wifi_connection_history", 8.0, "history-1")]
    assert list(telegram.cards) == [30] and not telegram.sent
    assert "подключение" in telegram.cards[30]["text"] and "private" not in telegram.cards[30]["text"]


def test_network_traffic_validator_requires_fresh_exact_redacted_sample():
    now = datetime.datetime.now(datetime.timezone.utc)
    interface_id = "bc60aa9b07ccca8059ab"
    result = {
        "type": "network_traffic", "schema": "xider.network-traffic.v1",
        "feature_id": "D04-F18", "device_id": "device-1", "request_id": "traffic-1",
        "source": "windows_psutil_interface_bytes", "mode": "sample", "state": "available",
        "interface_id": interface_id, "label": "Wi-Fi", "sent_bytes": 1200,
        "received_bytes": 3400, "sampled_at": now.isoformat(), "detail": None,
        "checked_at": now.isoformat(),
    }
    parsed = bot._validated_network_traffic(result, target="device-1", command_id="traffic-1", mode="sample")
    assert parsed["sent_bytes"] == 1200
    assert bot._validated_network_traffic({**result, "request_id": "stale"}, target="device-1", command_id="traffic-1", mode="sample") is None
    assert bot._validated_network_traffic({**result, "mac_address": "secret"}, target="device-1", command_id="traffic-1", mode="sample") is None
    assert bot._validated_network_traffic({**result, "sent_bytes": -1}, target="device-1", command_id="traffic-1", mode="sample") is None
    unknown = {**result, "state": "unknown", "interface_id": "bc60aa9b07ccca8059ab",
               "label": None, "sent_bytes": None, "received_bytes": None, "sampled_at": None,
               "detail": "counter_source_unavailable"}
    assert bot._validated_network_traffic(unknown, target="device-1", command_id="traffic-1", mode="sample")["interface_id"] == "bc60aa9b07ccca8059ab"
    assert bot._validated_network_traffic({**unknown, "interface_id": "invalid"}, target="device-1", command_id="traffic-1", mode="sample") is None


def test_network_traffic_alert_sample_requires_exact_fresh_selected_interface(monkeypatch):
    now = datetime.datetime.now(datetime.timezone.utc)
    sample = {
        "type": "network_traffic_alert_sample", "schema": "xider.network-traffic-alert.v1",
        "feature_id": "D04-F19", "device_id": "device-1", "schedule_id": "a" * 32,
        "interface_id": "b" * 20, "source": "windows_psutil_interface_bytes", "state": "available",
        "sent_bytes": 100, "received_bytes": 200,
        "sampled_at": now.isoformat().replace("+00:00", "Z"),
        "next_sample_at": int(now.timestamp()) + 900, "detail": None,
    }
    monkeypatch.setattr(bot.devices, "get", lambda _device_id: {"os": "Windows 11"})
    assert bot._validated_network_traffic_alert_sample(sample, device_id="device-1")["schedule_id"] == "a" * 32
    assert bot._validated_network_traffic_alert_sample({**sample, "mac_address": "not-allowed"}, device_id="device-1") is None
    assert bot._validated_network_traffic_alert_sample({**sample, "source": "macos_psutil_interface_bytes"}, device_id="device-1") is None
    stale = {**sample, "sampled_at": (now - datetime.timedelta(hours=3)).isoformat()}
    assert bot._validated_network_traffic_alert_sample(stale, device_id="device-1") is None


def test_network_traffic_alert_controls_are_owner_gated_bounded_and_offer_disable():
    markup = bot._network_traffic_alert_markup("device-1", "b" * 20, enabled=True)
    callbacks = [button.callback_data for row in markup.inline_keyboard for button in row]
    assert len(callbacks) == 14
    assert all(len(callback) <= 64 for callback in callbacks)
    assert "traffic:alert-off:" in callbacks[-2]
    assert all(_expected_callback_gate(callback) == "OwnerFilter" for callback in callbacks[:-1])
    assert _expected_callback_gate(callbacks[-1]) == "AdminFilter"


def test_network_traffic_alert_enable_and_disable_require_correlated_agent_ack(monkeypatch, tmp_path):
    from types import SimpleNamespace

    telegram = _card_telegram(monkeypatch, tmp_path)
    _setup(monkeypatch, {"device-1": {"name": "Laptop", "os": "Windows 11", "capabilities": {
        "commands": ["network_traffic_interval"],
    }}})
    monkeypatch.setattr(bot, "ADMIN_ID", 20)
    monkeypatch.setattr(bot, "SESSION", {"target": "device-1"})
    monkeypatch.setattr(bot, "get_user_role", lambda uid: bot.Role.OWNER if uid == 20 else bot.Role.GUEST)
    monkeypatch.setattr(bot, "_callback_result_access_or_report", lambda *_args: asyncio.sleep(0, result=True))
    monkeypatch.setattr(bot, "_device_action_still_allowed", lambda *_args: True)
    interface_id = "b" * 20
    saved = {"enabled": False, "interface_id": interface_id, "threshold_bytes": 100_000_000,
             "interval_seconds": 0, "label": "Wi-Fi"}
    monkeypatch.setattr(bot.devices, "get_network_traffic_sample", lambda *_args: {
        "interface_id": interface_id, "label": "Wi-Fi", "sampled_at": int(time.time()),
    }, raising=False)
    monkeypatch.setattr(bot.devices, "get_network_traffic_alert", lambda *_args: dict(saved), raising=False)
    configured = []
    def configure(_device_id, **kwargs):
        saved.update(kwargs)
        configured.append(dict(kwargs))
        return dict(saved)
    monkeypatch.setattr(bot.devices, "configure_network_traffic_alert", configure, raising=False)

    published, waited = [], []
    command_ids = iter(["alert-on", "alert-off"])
    def publish(action, **kwargs):
        published.append((action, dict(kwargs)))
        return True, next(command_ids)
    monkeypatch.setattr(bot, "publish_tracked", publish)
    async def wait_for(device, action, timeout, command_id):
        waited.append((device, action, timeout, command_id))
        request = published[-1][1]
        return {
            "type": "network_traffic_alert_status", "schema": "xider.network-traffic-alert.v1",
            "feature_id": "D04-F19", "device_id": device, "request_id": command_id,
            "state": "supported", "interval_seconds": request["interval_seconds"],
            "schedule_id": request["schedule_id"], "interface_id": interface_id,
            "threshold_bytes": request["threshold_bytes"],
            "next_sample_at": int(time.time()) + 900 if request["interval_seconds"] else None,
        }
    monkeypatch.setattr(bot, "network_traffic_alert_collector", SimpleNamespace(wait_for=wait_for))

    tag = bot._network_traffic_device_tag("device-1")
    enable = f"traffic:alert-set:{tag}:{interface_id}:100m:900"
    asyncio.run(bot.on_network_traffic_alert_set(telegram.callback(enable)))
    assert configured[0]["enabled"] is True and configured[0]["interval_seconds"] == 900
    assert "включён" in telegram.cards[30]["text"]

    disable = f"traffic:alert-off:{tag}:{interface_id}"
    asyncio.run(bot.on_network_traffic_alert_off(telegram.callback(disable)))
    assert configured[1]["enabled"] is False and configured[1]["interval_seconds"] == 0
    assert "выключен" in telegram.cards[30]["text"]
    assert [row[0] for row in published] == ["network_traffic_interval"] * 2
    assert [row[3] for row in waited] == ["alert-on", "alert-off"]


def test_interface_status_history_validator_requires_fresh_exact_transition_rows():
    checked = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    result = {
        "type": "interface_status_history", "schema": "xider.interface-status-history.v1",
        "feature_id": "D04-F20", "device_id": "device-1", "request_id": "request-1",
        "source": "windows_psutil_interface_status", "mode": "read", "state": "available",
        "interface_id": "a" * 20, "interval_seconds": 0, "interfaces": [],
        "events": [{"at": checked, "state": "online", "kind": "baseline"}], "checked_at": checked, "detail": None,
    }
    values = bot._validated_interface_status_history(
        result, target="device-1", command_id="request-1", mode="read", interface_id="a" * 20,
    )
    assert values["events"] == [{"at": checked, "state": "online", "kind": "baseline"}]
    assert bot._validated_interface_status_history(
        {**result, "mac_address": "not-allowed"}, target="device-1", command_id="request-1",
        mode="read", interface_id="a" * 20,
    ) is None
    assert bot._validated_interface_status_history(
        {**result, "events": [{"at": checked, "state": "online", "kind": "baseline", "ssid": "private"}]},
        target="device-1", command_id="request-1", mode="read", interface_id="a" * 20,
    ) is None


def test_interface_status_history_buttons_are_bounded_and_fit_telegram_callback_limit():
    markup = bot._interface_status_history_markup("device-1", "a" * 20)
    buttons = [button for row in markup.inline_keyboard for button in row]
    assert any("Следить" in button.text for button in buttons)
    assert any("Показать" in button.text for button in buttons)
    assert any("Остановить" in button.text for button in buttons)
    assert all(len(button.callback_data.encode("utf-8")) <= 64 for button in buttons if button.callback_data)


def test_network_traffic_is_owner_only_and_edits_the_same_card(monkeypatch, tmp_path):
    from device_store import DeviceStore
    from types import SimpleNamespace

    telegram = _card_telegram(monkeypatch, tmp_path)
    _setup(monkeypatch, {"device-1": {"name": "Laptop", "os": "Windows 11", "capabilities": {
        "commands": ["network_traffic"],
    }}})
    monkeypatch.setattr(bot, "ADMIN_ID", 20)
    monkeypatch.setattr(bot, "SESSION", {"target": "device-1"})
    monkeypatch.setattr(bot, "get_user_role", lambda uid: bot.Role.OWNER if uid == 20 else bot.Role.GUEST)
    monkeypatch.setattr(bot, "_callback_result_access_or_report", lambda *_args: asyncio.sleep(0, result=True))
    monkeypatch.setattr(DeviceStore, "FILE", tmp_path / "devices.json")
    monkeypatch.setattr(DeviceStore, "CATALOG_FILE", tmp_path / "catalog.json")
    store = DeviceStore()
    monkeypatch.setattr(bot.devices, "record_network_traffic_sample", store.record_network_traffic_sample, raising=False)

    command_ids = iter(["catalog-1", "sample-1", "sample-2"])
    published = []
    monkeypatch.setattr(bot, "publish_tracked", lambda *args, **kwargs: (published.append((args, kwargs)) or (True, next(command_ids))))
    now = datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0)
    interface_id = "bc60aa9b07ccca8059ab"
    responses = {
        "catalog-1": {
            "type": "network_traffic", "schema": "xider.network-traffic.v1", "feature_id": "D04-F18",
            "device_id": "device-1", "request_id": "catalog-1", "source": "windows_psutil_interface_bytes",
            "mode": "catalog", "state": "available", "interfaces": [{"interface_id": interface_id, "label": "Wi-Fi"}],
            "detail": None, "checked_at": now.isoformat(),
        },
        "sample-1": {
            "type": "network_traffic", "schema": "xider.network-traffic.v1", "feature_id": "D04-F18",
            "device_id": "device-1", "request_id": "sample-1", "source": "windows_psutil_interface_bytes",
            "mode": "sample", "state": "available", "interface_id": interface_id, "label": "Wi-Fi",
            "sent_bytes": 1200, "received_bytes": 3400, "sampled_at": (now - datetime.timedelta(seconds=10)).isoformat(),
            "detail": None, "checked_at": now.isoformat(),
        },
        "sample-2": {
            "type": "network_traffic", "schema": "xider.network-traffic.v1", "feature_id": "D04-F18",
            "device_id": "device-1", "request_id": "sample-2", "source": "windows_psutil_interface_bytes",
            "mode": "sample", "state": "available", "interface_id": interface_id, "label": "Wi-Fi",
            "sent_bytes": 1300, "received_bytes": 3900, "sampled_at": now.isoformat(),
            "detail": None, "checked_at": now.isoformat(),
        },
    }
    waited = []
    async def wait_for(target, action, timeout, command_id):
        waited.append((target, action, timeout, command_id))
        return responses[command_id]
    monkeypatch.setattr(bot, "network_traffic_collector", SimpleNamespace(wait_for=wait_for), raising=False)

    asyncio.run(bot.on_cmd_network_traffic(telegram.callback("cmd:network_traffic")))
    catalog_markup = telegram.cards[30]["markup"]
    choose = next(button.callback_data for row in catalog_markup.inline_keyboard for button in row
                  if button.callback_data.startswith("traffic:sample:"))
    asyncio.run(bot.on_network_traffic_sample(telegram.callback(choose)))
    assert "Базовый замер сохранён" in telegram.cards[30]["text"]
    asyncio.run(bot.on_network_traffic_sample(telegram.callback(choose)))

    owner_menu, guest_menu = bot.network_menu(user_id=20), bot.network_menu(user_id=21)
    owner_buttons = {b.callback_data: b.text for row in owner_menu.inline_keyboard for b in row}
    guest_callbacks = {b.callback_data for row in guest_menu.inline_keyboard for b in row}
    assert owner_buttons["cmd:network_traffic"] == "📊 Трафик адаптера"
    assert "cmd:network_traffic" not in guest_callbacks
    assert _expected_callback_gate("cmd:network_traffic") == "OwnerFilter"
    assert [entry[0][0] for entry in published] == ["network_traffic"] * 3
    assert published[1][1]["interface_id"] == interface_id
    assert waited == [
        ("device-1", "network_traffic", 8.0, "catalog-1"),
        ("device-1", "network_traffic", 8.0, "sample-1"),
        ("device-1", "network_traffic", 8.0, "sample-2"),
    ]
    assert list(telegram.cards) == [30] and not telegram.sent
    assert "+100 байт" in telegram.cards[30]["text"] and "+500 байт" in telegram.cards[30]["text"]
    assert _expected_callback_gate("traffic:sample:123456789abc:" + interface_id) == "OwnerFilter"


def test_latency_series_input_and_validator_require_exact_endpoint_and_correlated_five_points():
    assert bot._parse_latency_series_input("example.com:443") == {"host": "example.com", "port": 443}
    assert bot._parse_latency_series_input("[2001:db8::1]:443") == {"host": "2001:db8::1", "port": 443}
    assert bot._parse_latency_series_input("https://example.com:443") is None
    assert bot._parse_latency_series_input("example.com:0") is None
    samples = [{"index": i, "state": "connected", "latency_ms": float(i * 10)} for i in range(1, 6)]
    summary = {"successful": 5, "attempted": 5, "min_ms": 10.0, "mean_ms": 30.0, "max_ms": 50.0}
    result = {
        "type": "latency_series", "schema": "xider.latency-series.v1", "feature_id": "D04-F01",
        "device_id": "device-1", "request_id": "latency-1", "host": "example.com", "port": 443,
        "source": "tcp_connect_monotonic", "state": "available",
        "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "samples": samples, "summary": summary, "detail": None,
    }
    validated = bot._validated_latency_series(result, target="device-1", command_id="latency-1",
                                               host="example.com", port=443)
    assert validated["summary"] == summary
    assert bot._validated_latency_series({**result, "device_id": "other"}, target="device-1",
                                          command_id="latency-1", host="example.com", port=443) is None
    assert bot._validated_latency_series({**result, "samples": samples[:-1]}, target="device-1",
                                          command_id="latency-1", host="example.com", port=443) is None
    assert bot._validated_latency_series({**result, "samples": [*samples, {"index": 6}]}, target="device-1",
                                          command_id="latency-1", host="example.com", port=443) is None


def test_latency_series_owner_flow_is_correlated_and_edits_existing_card(monkeypatch, tmp_path):
    telegram = _card_telegram(monkeypatch, tmp_path)
    _setup(monkeypatch, {"device-1": {"name": "Laptop", "os": "Windows 11", "capabilities": {
        "commands": ["latency_series"],
    }}})
    monkeypatch.setattr(bot, "ADMIN_ID", 20)
    monkeypatch.setattr(bot, "SESSION", {"target": "device-1"})
    monkeypatch.setattr(bot, "get_user_role", lambda user_id: bot.Role.OWNER if user_id == 20 else bot.Role.GUEST)
    monkeypatch.setattr(bot.access_store, "can_use_callback", lambda *_args: True)
    published, waited = [], []
    monkeypatch.setattr(bot, "publish_tracked", lambda *args, **kwargs: published.append((args, kwargs)) or (True, "latency-1"))

    async def wait_for(*args):
        waited.append(args)
        samples = [{"index": i, "state": "connected", "latency_ms": float(i * 10)} for i in range(1, 6)]
        return {
            "type": "latency_series", "schema": "xider.latency-series.v1", "feature_id": "D04-F01",
            "device_id": "device-1", "request_id": "latency-1", "host": "example.com", "port": 443,
            "source": "tcp_connect_monotonic", "state": "available",
            "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat(), "samples": samples,
            "summary": {"successful": 5, "attempted": 5, "min_ms": 10.0, "mean_ms": 30.0, "max_ms": 50.0},
            "detail": None,
        }

    monkeypatch.setattr(bot, "latency_series_collector", SimpleNamespace(wait_for=wait_for))

    class State:
        def __init__(self):
            self.data = {}
        async def set_state(self, _value):
            pass
        async def update_data(self, **values):
            self.data.update(values)
        async def get_data(self):
            return dict(self.data)
        async def clear(self):
            self.data.clear()

    state = State()
    cq = telegram.callback("cmd:latency_series")
    asyncio.run(bot.on_cmd_latency_series(cq, state))
    message = telegram.message(message_id=45, chat_id=10, user_id=20)
    message.text = "example.com:443"
    asyncio.run(bot.on_latency_series_input(message, state))

    owner_menu, guest_menu = bot.network_menu(user_id=20), bot.network_menu(user_id=21)
    owner_buttons = {button.callback_data: button.text for row in owner_menu.inline_keyboard for button in row}
    guest_callbacks = {button.callback_data for row in guest_menu.inline_keyboard for button in row}
    assert owner_buttons["cmd:latency_series"] == "📈 Серия latency"
    assert "cmd:latency_series" not in guest_callbacks
    assert _expected_callback_gate("cmd:latency_series") == "OwnerFilter"
    assert published == [(("latency_series",), {"_target": "device-1", "host": "example.com", "port": 443})]
    assert waited == [("device-1", "latency_series", 10.0, "latency-1")]
    assert list(telegram.cards) == [30] and not telegram.sent
    assert "TCP connect" in telegram.cards[30]["text"]
    assert "10, 20, 30, 40, 50" in telegram.cards[30]["text"]


def test_packet_loss_history_validator_requires_literal_target_and_consistent_samples():
    assert bot._parse_packet_loss_address("192.0.2.8") == "192.0.2.8"
    assert bot._parse_packet_loss_address("2001:db8::8") == "2001:db8::8"
    for value in ("example.com", "127.0.0.1", "224.0.0.1", "192.0.2.1;whoami", "0.0.0.0"):
        assert bot._parse_packet_loss_address(value) is None
    samples = [{"index": i, "state": "reply", "detail": None, "elapsed_ms": 2} for i in range(1, 11)]
    result = {
        "type": "packet_loss_history", "schema": "xider.packet-loss-history.v1", "feature_id": "D04-F02",
        "device_id": "device-1", "request_id": "loss-1", "address": "192.0.2.8",
        "source": "ten_bounded_icmp_echo_samples", "state": "complete",
        "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "data": {"address": "192.0.2.8", "state": "complete", "source": "ten_bounded_icmp_echo_samples",
                 "detail": None, "sent": 10, "observed": 10, "received": 10, "lost": 0,
                 "loss_percent": 0.0, "samples": samples},
    }
    validated = bot._validated_packet_loss_history(result, target="device-1", command_id="loss-1", address="192.0.2.8")
    assert validated["loss_percent"] == 0.0
    inconsistent = {**result, "data": {**result["data"], "lost": 1}}
    assert bot._validated_packet_loss_history(inconsistent, target="device-1", command_id="loss-1", address="192.0.2.8") is None
    assert bot._validated_packet_loss_history({**result, "device_id": "other"}, target="device-1", command_id="loss-1", address="192.0.2.8") is None


def test_network_jitter_validator_recomputes_adjacent_rtt_deltas():
    values = [10.0, 12.0, 17.0, 17.0, 21.0, 25.0, 30.0, 31.0, 35.0, 40.0]
    samples = [{"index": i, "state": "reply", "rtt_ms": value, "elapsed_ms": 2}
               for i, value in enumerate(values, 1)]
    result = {
        "type": "network_jitter", "schema": "xider.network-jitter.v1", "feature_id": "D04-F03",
        "device_id": "device-1", "request_id": "jitter-1", "address": "192.0.2.8",
        "source": "icmp_rtt_adjacent_mean_abs_delta", "state": "available",
        "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "data": {"address": "192.0.2.8", "state": "available", "source": "icmp_rtt_adjacent_mean_abs_delta",
                 "detail": None, "attempted": 10, "received": 10, "lost": 0, "unknown": 0,
                 "pairs": 9, "jitter_ms": 3.333, "samples": samples},
    }
    validated = bot._validated_network_jitter(result, target="device-1", command_id="jitter-1", address="192.0.2.8")
    assert validated["jitter_ms"] == 3.333
    inconsistent = {**result, "data": {**result["data"], "jitter_ms": 0}}
    assert bot._validated_network_jitter(inconsistent, target="device-1", command_id="jitter-1", address="192.0.2.8") is None


def test_network_jitter_owner_flow_is_single_device_owner_only_and_correlated(monkeypatch, tmp_path):
    telegram = _card_telegram(monkeypatch, tmp_path)
    _setup(monkeypatch, {"device-1": {"name": "Laptop", "os": "Windows 11", "capabilities": {
        "commands": ["network_jitter"],
    }}})
    monkeypatch.setattr(bot, "ADMIN_ID", 20)
    monkeypatch.setattr(bot, "SESSION", {"target": "device-1"})
    monkeypatch.setattr(bot, "get_user_role", lambda user_id: bot.Role.OWNER if user_id == 20 else bot.Role.GUEST)
    monkeypatch.setattr(bot.access_store, "can_use_callback", lambda *_args: True)
    published, waited = [], []
    monkeypatch.setattr(bot, "publish_tracked", lambda *args, **kwargs: published.append((args, kwargs)) or (True, "jitter-1"))
    values = [10.0, 12.0, 17.0, 17.0, 21.0, 25.0, 30.0, 31.0, 35.0, 40.0]

    async def wait_for(*args):
        waited.append(args)
        samples = [{"index": i, "state": "reply", "rtt_ms": value, "elapsed_ms": 2}
                   for i, value in enumerate(values, 1)]
        return {
            "type": "network_jitter", "schema": "xider.network-jitter.v1", "feature_id": "D04-F03",
            "device_id": "device-1", "request_id": "jitter-1", "address": "192.0.2.8",
            "source": "icmp_rtt_adjacent_mean_abs_delta", "state": "available",
            "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "data": {"address": "192.0.2.8", "state": "available", "source": "icmp_rtt_adjacent_mean_abs_delta",
                     "detail": None, "attempted": 10, "received": 10, "lost": 0, "unknown": 0,
                     "pairs": 9, "jitter_ms": 3.333, "samples": samples},
        }

    monkeypatch.setattr(bot, "network_jitter_collector", SimpleNamespace(wait_for=wait_for))

    class State:
        def __init__(self): self.data = {}
        async def set_state(self, _value): pass
        async def update_data(self, **values): self.data.update(values)
        async def get_data(self): return dict(self.data)
        async def clear(self): self.data.clear()

    state = State()
    asyncio.run(bot.on_cmd_network_jitter(telegram.callback("cmd:network_jitter"), state))
    message = telegram.message(message_id=47, chat_id=10, user_id=20)
    message.text = "192.0.2.8"
    asyncio.run(bot.on_network_jitter_input(message, state))

    owner_menu, guest_menu = bot.network_menu(user_id=20), bot.network_menu(user_id=21)
    owner_buttons = {button.callback_data: button.text for row in owner_menu.inline_keyboard for button in row}
    guest_callbacks = {button.callback_data for row in guest_menu.inline_keyboard for button in row}
    assert owner_buttons["cmd:network_jitter"] == "📶 Jitter сети"
    assert "cmd:network_jitter" not in guest_callbacks
    assert _expected_callback_gate("cmd:network_jitter") == "OwnerFilter"
    assert published == [(("network_jitter",), {"_target": "device-1", "address": "192.0.2.8"})]
    assert waited == [("device-1", "network_jitter", 30.0, "jitter-1")]
    assert list(telegram.cards) == [30] and not telegram.sent
    assert "ICMP RTT jitter" in telegram.cards[30]["text"]
    assert "3.333 ms" in telegram.cards[30]["text"]


def test_packet_loss_history_owner_flow_is_single_device_owner_only_and_correlated(monkeypatch, tmp_path):
    telegram = _card_telegram(monkeypatch, tmp_path)
    _setup(monkeypatch, {"device-1": {"name": "Laptop", "os": "Windows 11", "capabilities": {
        "commands": ["packet_loss_history"],
    }}})
    monkeypatch.setattr(bot, "ADMIN_ID", 20)
    monkeypatch.setattr(bot, "SESSION", {"target": "device-1"})
    monkeypatch.setattr(bot, "get_user_role", lambda user_id: bot.Role.OWNER if user_id == 20 else bot.Role.GUEST)
    monkeypatch.setattr(bot.access_store, "can_use_callback", lambda *_args: True)
    published, waited = [], []
    monkeypatch.setattr(bot, "publish_tracked", lambda *args, **kwargs: published.append((args, kwargs)) or (True, "loss-1"))

    async def wait_for(*args):
        waited.append(args)
        samples = [{"index": i, "state": "reply", "detail": None, "elapsed_ms": 2} for i in range(1, 11)]
        return {
            "type": "packet_loss_history", "schema": "xider.packet-loss-history.v1", "feature_id": "D04-F02",
            "device_id": "device-1", "request_id": "loss-1", "address": "192.0.2.8",
            "source": "ten_bounded_icmp_echo_samples", "state": "complete",
            "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "data": {"address": "192.0.2.8", "state": "complete", "source": "ten_bounded_icmp_echo_samples",
                     "detail": None, "sent": 10, "observed": 10, "received": 10, "lost": 0,
                     "loss_percent": 0.0, "samples": samples},
        }

    monkeypatch.setattr(bot, "packet_loss_history_collector", SimpleNamespace(wait_for=wait_for))

    class State:
        def __init__(self): self.data = {}
        async def set_state(self, _value): pass
        async def update_data(self, **values): self.data.update(values)
        async def get_data(self): return dict(self.data)
        async def clear(self): self.data.clear()

    state = State()
    asyncio.run(bot.on_cmd_packet_loss_history(telegram.callback("cmd:packet_loss_history"), state))
    message = telegram.message(message_id=45, chat_id=10, user_id=20)
    message.text = "192.0.2.8"
    asyncio.run(bot.on_packet_loss_history_input(message, state))

    owner_menu, guest_menu = bot.network_menu(user_id=20), bot.network_menu(user_id=21)
    owner_buttons = {button.callback_data: button.text for row in owner_menu.inline_keyboard for button in row}
    guest_callbacks = {button.callback_data for row in guest_menu.inline_keyboard for button in row}
    assert owner_buttons["cmd:packet_loss_history"] == "📉 Потери пакетов"
    assert "cmd:packet_loss_history" not in guest_callbacks
    assert _expected_callback_gate("cmd:packet_loss_history") == "OwnerFilter"
    assert published == [(("packet_loss_history",), {"_target": "device-1", "address": "192.0.2.8"})]
    assert waited == [("device-1", "packet_loss_history", 30.0, "loss-1")]
    assert list(telegram.cards) == [30] and not telegram.sent
    assert "0%" in telegram.cards[30]["text"]


def test_network_throughput_validator_recomputes_rates_and_rejects_forgery():
    endpoint = "https://test.example/.well-known/xider-bandwidth"
    result = {
        "type": "network_throughput", "schema": "xider.network-throughput.v1", "feature_id": "D04-F04",
        "device_id": "device-1", "request_id": "speed-1", "endpoint": endpoint,
        "source": "https_xider_bandwidth_endpoint", "state": "available",
        "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "data": {"state": "available", "source": "https_xider_bandwidth_endpoint",
                 "download_bytes": 100, "download_duration_ms": 1000,
                 "download_bytes_per_second": 100, "upload_bytes": 65536,
                 "upload_duration_ms": 2000, "upload_bytes_per_second": 32768},
    }
    valid = bot._validated_network_throughput(result, target="device-1", command_id="speed-1", endpoint=endpoint)
    assert valid["download_bytes_per_second"] == 100
    forged = {**result, "data": {**result["data"], "upload_bytes_per_second": 1}}
    assert bot._validated_network_throughput(forged, target="device-1", command_id="speed-1", endpoint=endpoint) is None


def test_network_throughput_owner_flow_is_single_device_owner_only_and_correlated(monkeypatch, tmp_path):
    endpoint = "https://test.example/.well-known/xider-bandwidth"
    telegram = _card_telegram(monkeypatch, tmp_path)
    _setup(monkeypatch, {"device-1": {"name": "Laptop", "os": "Windows 11", "capabilities": {
        "commands": ["network_throughput"],
    }}})
    monkeypatch.setattr(bot, "ADMIN_ID", 20)
    monkeypatch.setattr(bot, "SESSION", {"target": "device-1"})
    monkeypatch.setattr(bot, "get_user_role", lambda user_id: bot.Role.OWNER if user_id == 20 else bot.Role.GUEST)
    monkeypatch.setattr(bot.access_store, "can_use_callback", lambda *_args: True)
    published, waited = [], []
    monkeypatch.setattr(bot, "publish_tracked", lambda *args, **kwargs: published.append((args, kwargs)) or (True, "speed-1"))

    async def wait_for(*args):
        waited.append(args)
        return {
            "type": "network_throughput", "schema": "xider.network-throughput.v1", "feature_id": "D04-F04",
            "device_id": "device-1", "request_id": "speed-1", "endpoint": endpoint,
            "source": "https_xider_bandwidth_endpoint", "state": "available",
            "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "data": {"state": "available", "source": "https_xider_bandwidth_endpoint",
                     "download_bytes": 100, "download_duration_ms": 1000,
                     "download_bytes_per_second": 100, "upload_bytes": 65536,
                     "upload_duration_ms": 2000, "upload_bytes_per_second": 32768},
        }

    monkeypatch.setattr(bot, "network_throughput_collector", SimpleNamespace(wait_for=wait_for))

    class State:
        def __init__(self): self.data = {}
        async def set_state(self, _value): pass
        async def update_data(self, **values): self.data.update(values)
        async def get_data(self): return dict(self.data)
        async def clear(self): self.data.clear()

    state = State()
    asyncio.run(bot.on_cmd_network_throughput(telegram.callback("cmd:network_throughput"), state))
    message = telegram.message(message_id=47, chat_id=10, user_id=20)
    message.text = endpoint
    asyncio.run(bot.on_network_throughput_input(message, state))

    owner_menu, guest_menu = bot.network_menu(user_id=20), bot.network_menu(user_id=21)
    owner_buttons = {button.callback_data: button.text for row in owner_menu.inline_keyboard for button in row}
    guest_callbacks = {button.callback_data for row in guest_menu.inline_keyboard for button in row}
    assert owner_buttons["cmd:network_throughput"] == "🚀 Пропускная способность"
    assert "cmd:network_throughput" not in guest_callbacks
    assert _expected_callback_gate("cmd:network_throughput") == "OwnerFilter"
    assert published == [(("network_throughput",), {"_target": "device-1", "endpoint": endpoint})]
    assert waited == [("device-1", "network_throughput", 30.0, "speed-1")]
    assert list(telegram.cards) == [30] and not telegram.sent
    assert "100 B/s" in telegram.cards[30]["text"]


def test_network_link_speed_validator_checks_measurement_and_unit():
    checked = datetime.datetime.now(datetime.timezone.utc).isoformat()
    result = {
        "type": "network_link_speed", "schema": "xider.network-link-speed.v1", "feature_id": "D04-F21",
        "device_id": "device-1", "request_id": "link-1", "mode": "measure",
        "source": "windows_psutil_link_speed", "state": "available", "interface_id": "a" * 20,
        "data": {"state": "available", "source": "windows_psutil_link_speed", "interface_id": "a" * 20,
                 "label": "Ethernet", "speed_mbps": 1000, "checked_at": checked, "detail": None},
    }
    values = bot._validated_network_link_speed(result, target="device-1", command_id="link-1", mode="measure")
    assert values["speed_mbps"] == 1000
    forged = {**result, "data": {**result["data"], "speed_mbps": 0}}
    assert bot._validated_network_link_speed(forged, target="device-1", command_id="link-1", mode="measure") is None


def test_network_link_speed_owner_flow_selects_one_interface_and_edits_card(monkeypatch, tmp_path):
    telegram = _card_telegram(monkeypatch, tmp_path)
    _setup(monkeypatch, {"device-1": {"name": "Laptop", "os": "Windows 11", "capabilities": {
        "commands": ["network_link_speed"],
    }}})
    monkeypatch.setattr(bot, "ADMIN_ID", 20)
    monkeypatch.setattr(bot, "SESSION", {"target": "device-1"})
    monkeypatch.setattr(bot, "get_user_role", lambda user_id: bot.Role.OWNER if user_id == 20 else bot.Role.GUEST)
    monkeypatch.setattr(bot.access_store, "can_use_callback", lambda *_args: True)
    published, waited = [], []
    monkeypatch.setattr(bot, "publish_tracked", lambda *args, **kwargs: published.append((args, kwargs)) or (True, f"link-{len(published)}"))

    async def wait_for(*args):
        waited.append(args)
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        if args[3] == "link-1":
            return {"type": "network_link_speed", "schema": "xider.network-link-speed.v1", "feature_id": "D04-F21",
                    "device_id": "device-1", "request_id": "link-1", "mode": "catalog",
                    "source": "windows_psutil_link_speed", "state": "available", "interface_id": None,
                    "data": {"state": "available", "interfaces": [{"interface_id": "a" * 20, "label": "Ethernet"}], "detail": None}}
        return {"type": "network_link_speed", "schema": "xider.network-link-speed.v1", "feature_id": "D04-F21",
                "device_id": "device-1", "request_id": "link-2", "mode": "measure",
                "source": "windows_psutil_link_speed", "state": "available", "interface_id": "a" * 20,
                "data": {"state": "available", "source": "windows_psutil_link_speed", "interface_id": "a" * 20,
                         "label": "Ethernet", "speed_mbps": 1000, "checked_at": now, "detail": None}}

    monkeypatch.setattr(bot, "network_link_speed_collector", SimpleNamespace(wait_for=wait_for))

    class Callback:
        from_user = SimpleNamespace(id=20)
        class _Message:
            message_id = 41
            chat = SimpleNamespace(id=10)
            async def answer(self, *_args, **_kwargs): pass
        message = _Message()
        def __init__(self, data): self.data = data; self.answers = []
        async def answer(self, *args, **kwargs): self.answers.append((args, kwargs))

    asyncio.run(bot.on_cmd_network_link_speed(Callback("cmd:network_link_speed")))
    choose = Callback(f"network-link-speed:{bot._network_traffic_device_tag('device-1')}:{'a' * 20}")
    asyncio.run(bot.on_network_link_speed_select(choose))

    owner_menu = bot.network_menu(user_id=20)
    owner_callbacks = {button.callback_data for row in owner_menu.inline_keyboard for button in row}
    assert "cmd:network_link_speed" in owner_callbacks
    assert _expected_callback_gate("cmd:network_link_speed") == "OwnerFilter"
    assert published == [(("network_link_speed",), {"_target": "device-1"}),
                         (("network_link_speed",), {"_target": "device-1", "interface_id": "a" * 20})]
    assert waited == [("device-1", "network_link_speed", 8.0, "link-1"),
                      ("device-1", "network_link_speed", 8.0, "link-2")]
    assert list(telegram.cards) == [30] and "1000 Mbps" in telegram.cards[30]["text"]


def test_route_lookup_is_owner_only_correlated_and_edits_one_card(monkeypatch, tmp_path):
    telegram = _card_telegram(monkeypatch, tmp_path)
    _setup(monkeypatch, {"device-1": {"name": "Laptop", "os": "Windows 11", "capabilities": {
        "commands": ["route_lookup"],
    }}})
    monkeypatch.setattr(bot, "ADMIN_ID", 20)
    monkeypatch.setattr(bot, "SESSION", {"target": "device-1"})
    monkeypatch.setattr(bot, "get_user_role", lambda user_id: bot.Role.OWNER if user_id == 20 else bot.Role.GUEST)
    monkeypatch.setattr(bot, "_callback_result_access_or_report", lambda *_args, **_kwargs: asyncio.sleep(0, result=True))

    class Callback:
        from_user = SimpleNamespace(id=20)
        class _Message:
            message_id = 41
            chat = SimpleNamespace(id=10)
            async def answer(self, *_args, **_kwargs): pass
        message = _Message()
        data = "cmd:route_lookup"
        async def answer(self, *_args, **_kwargs): pass

    class State:
        async def set_state(self, _state): pass
        async def update_data(self, **_data): pass

    asyncio.run(bot.on_cmd_route_lookup(Callback(), State()))
    owner_menu = bot.network_menu(user_id=20)
    guest_menu = bot.network_menu(user_id=21)
    owner_callbacks = {button.callback_data for row in owner_menu.inline_keyboard for button in row}
    guest_callbacks = {button.callback_data for row in guest_menu.inline_keyboard for button in row}
    assert "cmd:route_lookup" in owner_callbacks and "cmd:route_lookup" not in guest_callbacks
    assert _expected_callback_gate("cmd:route_lookup") == "OwnerFilter"
    assert list(telegram.cards) == [30] and "сетевые пакеты" in telegram.cards[30]["text"]
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    result = {"type": "route_lookup", "schema": "xider.route-lookup.v1", "feature_id": "D04-F22",
              "device_id": "device-1", "request_id": "route-1", "source": "windows_find_net_route",
              "state": "available", "checked_at": now,
              "data": {"state": "available", "destination": "203.0.113.8", "route_prefix": "0.0.0.0/0",
                       "gateway": "192.0.2.1", "interface": "Ethernet", "metric": 25,
                       "source": "windows_find_net_route"}}
    parsed = bot._validated_route_lookup(result, target="device-1", command_id="route-1", destination="203.0.113.8")
    assert parsed["interface"] == "Ethernet"
    result["request_id"] = "stale"
    assert bot._validated_route_lookup(result, target="device-1", command_id="route-1", destination="203.0.113.8") is None


def test_shell_timing_report_is_owner_only_aggregate_and_edits_one_card(monkeypatch, tmp_path):
    telegram = _card_telegram(monkeypatch, tmp_path)
    _setup(monkeypatch, {"device-1": {"name": "Laptop", "os": "Windows 11", "capabilities": {"commands": ["shell"]}}})
    monkeypatch.setattr(bot, "ADMIN_ID", 20)
    monkeypatch.setattr(bot, "SESSION", {"target": "device-1"})
    monkeypatch.setattr(bot, "get_user_role", lambda user_id: bot.Role.OWNER if user_id == 20 else bot.Role.GUEST)
    monkeypatch.setattr(bot.access_store, "shell_execution_timing_summary", lambda actor, owner: {
        "state": "available", "resolution_seconds": 1, "sampled_requests": 3,
        "dispatch": {"count": 3, "mean_seconds": 2.0, "median_seconds": 2, "max_seconds": 4},
        "result": {"count": 2, "mean_seconds": 5.5, "median_seconds": 5.5, "max_seconds": 8},
    })

    class Callback:
        from_user = SimpleNamespace(id=20)
        class _Message:
            message_id = 41
            chat = SimpleNamespace(id=10)
            async def answer(self, *_args, **_kwargs): pass
        message = _Message()
        data = "cmd:shell_timing_report"
        async def answer(self, *_args, **_kwargs): pass

    asyncio.run(bot.on_shell_timing_report(Callback()))
    owner_menu = bot.terminal_menu(user_id=20)
    guest_menu = bot.terminal_menu(user_id=21)
    owner_callbacks = {button.callback_data for row in owner_menu.inline_keyboard for button in row}
    guest_callbacks = {button.callback_data for row in guest_menu.inline_keyboard for button in row}
    assert "cmd:shell_timing_report" in owner_callbacks and "cmd:shell_timing_report" not in guest_callbacks
    assert _expected_callback_gate("cmd:shell_timing_report") == "OwnerFilter"
    assert list(telegram.cards) == [30]
    body = telegram.cards[30]["text"]
    assert "Подтверждено → отправлено" in body and "Отправлено → итог агента" in body
    assert "Ожидание подтверждения владельца не измеряется" in body
    assert "SHA-256" not in body and "command_hash" not in body and "private-request-id" not in body


def test_shell_runner_diagnostic_is_owner_only_and_uses_fresh_capability_reply(monkeypatch, tmp_path):
    telegram = _card_telegram(monkeypatch, tmp_path)
    _setup(monkeypatch, {"device-1": {"name": "Laptop", "os": "Windows 11"}})
    monkeypatch.setattr(bot, "ADMIN_ID", 20)
    monkeypatch.setattr(bot, "SESSION", {"target": "device-1"})
    monkeypatch.setattr(bot, "get_user_role", lambda user_id: bot.Role.OWNER if user_id == 20 else bot.Role.GUEST)
    monkeypatch.setattr(bot, "_shell_request_allowed", lambda *_args: True)
    async def allow(*_args, **_kwargs):
        return True
    monkeypatch.setattr(bot, "_callback_result_access_or_report", allow)
    published = []
    monkeypatch.setattr(bot, "publish_tracked", lambda kind, **kwargs: published.append((kind, kwargs)) or (True, "cap-1"))
    async def response(*_args, **_kwargs):
        return {"type": "capabilities", "id": "cap-1", "device_id": "device-1", "shell_runner": {
            "schema": 1, "protocol_state": "ready", "executor_state": "available",
            "runner_state": "available", "permission_state": "available",
        }}
    monkeypatch.setattr(bot.capabilities_collector, "wait_for", response)

    asyncio.run(bot.on_shell_runner_diagnostic(telegram.callback("cmd:shell_runner_diagnostic")))

    owner = {button.callback_data for row in bot.terminal_menu(user_id=20).inline_keyboard for button in row}
    guest = {button.callback_data for row in bot.terminal_menu(user_id=21).inline_keyboard for button in row}
    assert "cmd:shell_runner_diagnostic" in owner and "cmd:shell_runner_diagnostic" not in guest
    assert _expected_callback_gate("cmd:shell_runner_diagnostic") == "OwnerFilter"
    assert published == [("capabilities", {"_target": "device-1"})]
    assert len(telegram.cards) == 1
    assert "Связь: свежий ответ получен" in next(iter(telegram.cards.values()))["text"]
    assert "Протокол подтверждений: готов" in next(iter(telegram.cards.values()))["text"]


def test_shell_approval_inbox_is_owner_only_read_only_and_shows_exact_preview(monkeypatch, tmp_path):
    telegram = _card_telegram(monkeypatch, tmp_path)
    _setup(monkeypatch, {"device-1": {"name": "Laptop", "os": "Windows 11"}})
    monkeypatch.setattr(bot, "ADMIN_ID", 20)
    monkeypatch.setattr(bot, "SESSION", {"target": "device-1"})
    monkeypatch.setattr(bot, "get_user_role", lambda user_id: bot.Role.OWNER if user_id == 20 else bot.Role.GUEST)
    monkeypatch.setattr(bot, "_shell_request_allowed", lambda *_args: True)
    now = 1_800_100_000
    monkeypatch.setattr(bot.time, "time", lambda: now)
    pending = bot.command_approval.create_pending(21, 20, "device-1", "printf '<exact & unchanged>'", now)

    class Callback:
        from_user = SimpleNamespace(id=20)
        class _Message:
            message_id = 41
            chat = SimpleNamespace(id=10)
            async def answer(self, *_args, **_kwargs): pass
        message = _Message()
        data = "cmd:shell_approval_inbox"
        async def answer(self, *_args, **_kwargs): pass

    asyncio.run(bot.on_shell_approval_inbox(Callback()))
    owner_menu = bot.terminal_menu(user_id=20)
    guest_menu = bot.terminal_menu(user_id=22)
    owner_callbacks = {button.callback_data for row in owner_menu.inline_keyboard for button in row}
    guest_callbacks = {button.callback_data for row in guest_menu.inline_keyboard for button in row}
    assert "cmd:shell_approval_inbox" in owner_callbacks
    assert "cmd:shell_approval_inbox" not in guest_callbacks
    assert list(telegram.cards) == [30]
    listing = telegram.cards[30]
    assert "ожидают подтверждения" in listing["text"].lower()
    detail = Callback()
    detail.data = f"cmd:shell-inbox:view:{pending.request_id}"
    asyncio.run(bot.on_shell_approval_inbox_view(detail))
    body = telegram.cards[30]["text"]
    assert "printf &#x27;&lt;exact &amp; unchanged&gt;&#x27;" in body
    assert "Точный preview" in body and "Просмотр ничего не запускает" in body
    callbacks = {button.callback_data for row in telegram.cards[30]["markup"].inline_keyboard for button in row}
    assert f"cmd:shell-approve:{pending.request_id}" in callbacks
    assert f"cmd:shell-cancel:{pending.request_id}" in callbacks
    assert bot.command_approval.get_pending(pending.request_id) is pending
    assert _expected_callback_gate("cmd:shell_approval_inbox") == "OwnerFilter"
    assert _expected_callback_gate(f"cmd:shell-inbox:view:{pending.request_id}") == "OwnerFilter"


def test_shell_late_result_inbox_is_exact_owner_only_redacted_and_single_use(monkeypatch, tmp_path):
    telegram = _card_telegram(monkeypatch, tmp_path)
    _setup(monkeypatch, {"device-1": {"name": "Laptop", "os": "macOS"}})
    monkeypatch.setattr(bot, "ADMIN_ID", 20)
    monkeypatch.setattr(bot, "SESSION", {"target": "device-1"})
    monkeypatch.setattr(bot, "get_user_role", lambda user_id: bot.Role.OWNER if user_id == 20 else bot.Role.GUEST)
    monkeypatch.setattr(bot, "shell_late_result_inbox_store", bot.shell_late_result_inbox.ShellLateResultInbox())
    monkeypatch.setattr(bot.access_store, "filter_audit", lambda *_args, **_kwargs: [
        {"request_id": "late-req-1", "status": "result_timeout", "at": 1_800_100_000},
    ])
    result = {"type": "shell", "request_id": "late-req-1", "device_id": "device-1",
              "status": "completed", "returncode": 0, "stdout": "TOKEN=secret-value\nlate output"}
    monkeypatch.setattr(bot.shell_collector, "recent_for", lambda *args: dict(result))
    monkeypatch.setattr(bot.shell_collector, "take_recent", lambda *args: dict(result))
    now = 1_800_100_000
    monkeypatch.setattr(bot.time, "time", lambda: now)

    asyncio.run(bot.on_shell_late_results(telegram.callback("cmd:shell_late_results")))
    owner = {button.callback_data for row in bot.terminal_menu(user_id=20).inline_keyboard for button in row}
    guest = {button.callback_data for row in bot.terminal_menu(user_id=21).inline_keyboard for button in row}
    assert "cmd:shell_late_results" in owner and "cmd:shell_late_results" not in guest
    assert _expected_callback_gate("cmd:shell_late_results") == "OwnerFilter"
    listing = telegram.cards[30]
    callback_data = next(button.callback_data for row in listing["markup"].inline_keyboard
                         for button in row if button.callback_data.startswith("cmd:shell-late:view:"))
    assert list(telegram.cards) == [30]

    asyncio.run(bot.on_shell_late_result_view(telegram.callback(callback_data)))
    final = telegram.cards[30]["text"]
    assert "late output" in final and "secret-value" not in final
    assert "поздний ответ" in final.lower()
    callbacks = {button.callback_data for row in telegram.cards[30]["markup"].inline_keyboard for button in row}
    assert not any("shell-repeat" in callback or "shell-approve" in callback for callback in callbacks)
    assert list(telegram.cards) == [30]
    assert _expected_callback_gate(callback_data) == "OwnerFilter"


def test_response_collector_late_result_cache_is_exact_ttl_bound_and_consumable(monkeypatch):
    collector = bot.ResponseCollector()
    clock = {"now": 100.0}
    monkeypatch.setattr(bot.time, "monotonic", lambda: clock["now"])
    collector.submit("device-1", {"type": "shell", "request_id": "req-1", "stdout": "result"})
    assert collector.recent_for("device-2", "shell", "req-1") is None
    assert collector.recent_for("device-1", "status", "req-1") is None
    assert collector.recent_for("device-1", "shell", "req-2") is None
    assert collector.recent_for("device-1", "shell", "req-1")["stdout"] == "result"
    assert collector.take_recent("device-1", "shell", "req-1")["stdout"] == "result"
    assert collector.take_recent("device-1", "shell", "req-1") is None

    collector.submit("device-1", {"type": "shell", "request_id": "req-2", "stdout": "expired"})
    clock["now"] += collector._recent_ttl + 1
    assert collector.take_recent("device-1", "shell", "req-2") is None
