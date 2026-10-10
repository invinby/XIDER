"""Local fake transport/Telegram only; real check persistence and authorization."""
import asyncio
import logging
import logging.handlers
from types import SimpleNamespace
from datetime import datetime, timezone, timedelta

logging.handlers.RotatingFileHandler = lambda *args, **kwargs: logging.NullHandler()
import bot
import check_store
import pytest


@pytest.fixture
def check_ui(tmp_path, monkeypatch):
    store = check_store.CheckStore(tmp_path / "checks.json")
    monkeypatch.setattr(check_store, "_default", store)
    monkeypatch.setitem(bot.SESSION, "target", "device-a")
    monkeypatch.setattr(bot, "target_label", lambda target: "fixture-device")
    monkeypatch.setattr(bot, "_simple_command_still_allowed", lambda user, callback, target: user == 1)
    monkeypatch.setattr(bot, "get_user_role", lambda user: bot.Role.OWNER if user == 1 else bot.Role.GUEST)
    edits = []
    async def replace_callback(cq, text, **kwargs):
        edits.append(text)
        return cq.message
    async def replace_card(message, user, text, **kwargs):
        edits.append(text)
        return message
    monkeypatch.setattr(bot, "_replace_callback_message", replace_callback)
    monkeypatch.setattr(bot, "_replace_message_card", replace_card)
    class Message:
        chat = SimpleNamespace(id=1)
        message_id = 88
        async def edit_text(self, text, **kwargs):
            edits.append(text)
    class Callback:
        data = "cmd:sys_uptime"
        from_user = SimpleNamespace(id=1)
        message = Message()
        async def answer(self, *args, **kwargs):
            pass
    return store, Callback(), edits


@pytest.mark.parametrize("payload,want", [
    ({"ok": True}, "succeeded"), ({"ok": False}, "failed"),
    ({"ok": False, "outcome": "unsupported", "text": "PRIVATE unsupported output"}, "unsupported"),
    ({"ok": False, "outcome": "needs_permission", "text": "PRIVATE permission output"}, "needs_permission"),
    ({"ok": "true"}, "unknown"), ({}, "unknown"), (None, "timed_out"),
    ({"ok": True, "device_id": "device-b", "text": "PRIVATE wrong device"}, "unknown"),
    ({"ok": True, "id": "other", "text": "PRIVATE wrong id"}, "unknown"),
    ({"ok": True, "type": "autorun_status", "text": "PRIVATE wrong callback"}, "unknown"),
    ({"ok": False, "state": "permission_denied", "text": "PRIVATE"}, "needs_permission"),
    ({"ok": False, "text": "unsupported: PRIVATE"}, "failed"),
])
def test_handler_records_only_correlated_metadata(check_ui, monkeypatch, payload, want):
    store, cq, edits = check_ui
    response = None if payload is None else {"device_id": "device-a", "type": "sys_uptime", "id": "abc123", **payload}
    class Collector:
        async def wait_for(self, device, action, *, timeout, command_id):
            assert (device, action, command_id) == ("device-a", "sys_uptime", "abc123")
            return response
    monkeypatch.setattr(bot, "fun_text_collector", Collector())
    monkeypatch.setattr(bot, "publish_tracked", lambda *args, **kwargs: (True, "abc123"))
    asyncio.run(bot._simple_command_unlocked(cq, "sys_uptime", "⏱", "uptime", timeout=.01))
    saved = store.get_last_check("device-a", "cmd:sys_uptime")
    assert saved is not None, "allowlisted check must record metadata"
    assert saved.outcome == want
    assert saved.correlation_id == "abc123"
    assert "Последняя проверка" in edits[-1]
    assert "PRIVATE" not in edits[-1]
    assert "PRIVATE" not in store.path.read_text()
    assert set(__import__("json").loads(store.path.read_text())["records"][0]) == {
        "device_id", "callback", "platform", "agent_version", "checked_at", "latency_ms", "outcome", "correlation_id"}


@pytest.mark.parametrize("sent", [False, None])
def test_publish_refusal_or_ambiguity_is_unknown_not_success(check_ui, monkeypatch, sent):
    store, cq, edits = check_ui
    monkeypatch.setattr(bot, "publish_tracked", lambda *args, **kwargs: (sent, "abc123"))
    asyncio.run(bot._simple_command_unlocked(cq, "sys_uptime", "⏱", "uptime", timeout=.01))
    saved = store.get_last_check("device-a", "cmd:sys_uptime")
    assert saved is not None
    assert saved.outcome == "unknown"
    assert saved.latency_ms is None


def test_revoked_grant_after_wait_hides_check_metadata(check_ui, monkeypatch):
    store, cq, edits = check_ui
    allowed = [True]
    monkeypatch.setattr(bot, "_simple_command_still_allowed", lambda *args: allowed[0])
    class Collector:
        async def wait_for(self, *args, **kwargs):
            allowed[0] = False
            return {"device_id": "device-a", "id": "abc123", "type": "sys_uptime", "ok": True, "text": "PRIVATE"}
    monkeypatch.setattr(bot, "fun_text_collector", Collector())
    monkeypatch.setattr(bot, "publish_tracked", lambda *args, **kwargs: (True, "abc123"))
    assert asyncio.run(bot._simple_command_unlocked(cq, "sys_uptime", "⏱", "uptime")) is None
    assert "PRIVATE" not in edits[-1]
    assert "Последняя проверка" not in edits[-1]


def test_guest_cannot_view_last_check_and_stale_is_not_refreshed(check_ui, monkeypatch):
    store, cq, edits = check_ui
    stamp = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    row = check_store.CheckRecord("device-a", "cmd:sys_uptime", "windows", "4.1.2", stamp, 5, "succeeded", "abc123")
    store.record_check(row)
    assert hasattr(bot, "_last_check_summary"), "role-aware last-check summary is missing"
    assert stamp not in bot._last_check_summary(2, "device-a", "cmd:sys_uptime")
    owner = bot._last_check_summary(1, "device-a", "cmd:sys_uptime")
    assert "устарело" in owner and stamp in owner and "4.1.2" in owner
    assert store.get_last_check("device-a", "cmd:sys_uptime").checked_at == stamp


@pytest.mark.parametrize("callback,action,kwargs", [("cmd:lock", "lock", {}),
    ("cmd:sys_uptime", "lock", {}), ("cmd:sys_uptime", "sys_uptime", {"argument": "PRIVATE"})])
def test_mutations_and_parameterized_checks_do_not_persist(check_ui, monkeypatch, callback, action, kwargs):
    store, cq, edits = check_ui
    cq.data = callback
    class Collector:
        async def wait_for(self, *args, **kwargs):
            return {"ok": True}
    monkeypatch.setattr(bot, "fun_text_collector", Collector())
    monkeypatch.setattr(bot, "publish_tracked", lambda *args, **kwargs: (True, "abc123"))
    asyncio.run(bot._simple_command_unlocked(cq, action, "X", "fixture", **kwargs))
    assert not store.path.exists()


def test_guest_cannot_dispatch_check(check_ui, monkeypatch):
    store, cq, edits = check_ui
    cq.from_user = SimpleNamespace(id=2)
    monkeypatch.setattr(bot, "publish_tracked", lambda *a, **kw: pytest.fail("guest publish"))
    asyncio.run(bot._simple_command_unlocked(cq, "sys_uptime", "X", "fixture"))
    assert not store.path.exists()
    assert "Последняя проверка" not in edits[-1]


def test_granted_user_gets_summary_then_revoke_hides_it(check_ui, monkeypatch, tmp_path):
    store, cq, edits = check_ui
    monkeypatch.setattr(bot.access_store, "FILE", tmp_path / "access.json")
    monkeypatch.setattr(bot.access_store, "AUDIT_FILE", tmp_path / "audit.jsonl")
    monkeypatch.setattr(bot, "ADMIN_ID", 100)
    monkeypatch.setattr(bot, "get_user_role", lambda user: bot.access_store.get_role(user, 100))
    monkeypatch.setattr(bot, "_simple_command_still_allowed", lambda user, callback, target:
                        bot.access_store.can_use_callback(user, callback, 100, target))
    bot.access_store.register_start(SimpleNamespace(id=200, username="fixture", first_name="Fixture", last_name=""))
    bot.access_store.set_role(100, 200, bot.Role.USER, 100)
    bot.access_store.toggle_device(100, 200, "device-a", 100)
    bot.access_store.toggle_callback(100, 200, "cmd:sys_uptime", 100)
    cq.from_user = SimpleNamespace(id=200)
    class Collector:
        async def wait_for(self, *args, **kwargs):
            return {"ok": True, "device_id": "device-a", "type": "sys_uptime", "id": "abc123", "text": "PRIVATE"}
    monkeypatch.setattr(bot, "fun_text_collector", Collector())
    monkeypatch.setattr(bot, "publish_tracked", lambda *args, **kwargs: (True, "abc123"))
    asyncio.run(bot._simple_command_unlocked(cq, "sys_uptime", "X", "fixture"))
    assert "Последняя проверка" in edits[-1] and "succeeded" in edits[-1]
    bot.access_store.toggle_callback(100, 200, "cmd:sys_uptime", 100)
    assert bot._last_check_summary(200, "device-a", "cmd:sys_uptime") == ""


@pytest.mark.parametrize("os_name,platform", [("Darwin", "macos"), ("Windows 11", "windows")])
def test_record_uses_inventory_platform_and_agent_version(check_ui, monkeypatch, os_name, platform):
    store, cq, edits = check_ui
    bot._save_action_check("device-a", "cmd:sys_uptime", "abc123", "succeeded",
                           {"os": os_name, "version": "4.1.2"}, 123)
    row = store.get_last_check("device-a", "cmd:sys_uptime")
    assert row.platform == platform
    assert row.agent_version == "4.1.2"


def test_hostname_identifiers_reload_without_accepting_paths(tmp_path):
    store = check_store.CheckStore(tmp_path / "checks.json")
    row = check_store.CheckRecord("MacBook-Air.local", "cmd:sys_uptime", "macos", "4.1.2",
                                  "2026-10-08T10:00:00Z", 1, "succeeded", "abc123")
    store.record_check(row)
    assert check_store.CheckStore(store.path).get_last_check("MacBook-Air.local", "cmd:sys_uptime") == row


def test_check_latency_excludes_telegram_progress_edit(check_ui, monkeypatch):
    store, cq, edits = check_ui
    clock = [0.0]
    monkeypatch.setattr(bot.time, "monotonic", lambda: clock[0])
    async def slow_progress(cq, text, **kwargs):
        clock[0] += 20.0
        return cq.message
    class Collector:
        async def wait_for(self, *args, **kwargs):
            clock[0] += .125
            return {"ok": True, "device_id": "device-a", "type": "sys_uptime", "id": "abc123"}
    monkeypatch.setattr(bot, "_replace_callback_message", slow_progress)
    monkeypatch.setattr(bot, "fun_text_collector", Collector())
    monkeypatch.setattr(bot, "publish_tracked", lambda *args, **kwargs: (True, "abc123"))
    asyncio.run(bot._simple_command_unlocked(cq, "sys_uptime", "X", "fixture"))
    assert store.get_last_check("device-a", "cmd:sys_uptime").latency_ms == 125
