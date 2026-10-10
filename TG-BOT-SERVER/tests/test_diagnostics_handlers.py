"""Owner-only snapshot export with offline Telegram boundary doubles."""
import asyncio
import json
import logging
import logging.handlers
from types import SimpleNamespace
import pytest

logging.handlers.RotatingFileHandler = lambda *args, **kwargs: logging.NullHandler()
import bot
import check_store


@pytest.fixture
def export_ui(monkeypatch):
    delivered, notices = [], []
    monkeypatch.setattr(bot, "ADMIN_ID", 100)
    monkeypatch.setattr(bot, "get_user_role", lambda user: bot.Role.OWNER if user == 100 else bot.Role.GUEST)
    monkeypatch.setattr(check_store, "_default", None)
    monkeypatch.setattr(bot.devices, "all", lambda: {})
    monkeypatch.setattr(bot.server_ops, "health_states", lambda: {"broker": {"state": "unknown", "checked_at": None, "value": None}})
    for method in ("status", "logs", "metrics", "specs", "run_terminal"):
        monkeypatch.setattr(bot.server_ops, method, lambda *a, **kw: pytest.fail("export initiated probe"))
    monkeypatch.setattr(bot, "publish", lambda *a, **kw: pytest.fail("export initiated MQTT"))
    class Message:
        chat = SimpleNamespace(id=100)
        message_id = 10
        async def answer_document(self, document, **kwargs):
            delivered.append(document)
    class Callback:
        data = "server:diagnostics"
        from_user = SimpleNamespace(id=100)
        message = Message()
        async def answer(self, text=None, **kwargs):
            notices.append(text)
    return Callback(), delivered, notices


def handler():
    assert hasattr(bot, "on_server_diagnostics_export"), "owner snapshot export callback missing"
    return bot.on_server_diagnostics_export


def test_export_owner_gets_one_json_document_without_probes(export_ui):
    cq, delivered, notices = export_ui
    asyncio.run(handler()(cq))
    assert len(delivered) == 1
    doc = json.loads(delivered[0].data)
    assert doc["schema"] == 1
    assert doc["versions"]["server"] == bot.VERSION
    assert doc["log_tail"] == []
    assert doc["statuses"]["log_tail"]["state"] == "unknown"
    assert check_store._default is None, "export must not initialize/read disk check store"


@pytest.mark.parametrize("user", [None, 200])
def test_export_missing_identity_or_guest_fails_closed(export_ui, user):
    cq, delivered, notices = export_ui
    cq.from_user = None if user is None else SimpleNamespace(id=user)
    asyncio.run(handler()(cq))
    assert not delivered


def test_export_rechecks_owner_after_callback_ack(export_ui):
    cq, delivered, notices = export_ui
    async def revoke(*args, **kwargs):
        cq.from_user = SimpleNamespace(id=200)
    cq.answer = revoke
    asyncio.run(handler()(cq))
    assert not delivered


def test_export_owner_cannot_send_private_bundle_into_group(export_ui):
    cq, delivered, notices = export_ui
    cq.message.chat = SimpleNamespace(id=-100001)
    asyncio.run(handler()(cq))
    assert not delivered


def test_export_rejection_does_not_echo_private_input(export_ui, monkeypatch):
    cq, delivered, notices = export_ui
    handler()
    def fail(**kwargs):
        raise ValueError("PRIVATE token=synthetic-file.txt")
    monkeypatch.setattr(bot.diagnostics, "build_diagnostic_snapshot", fail)
    asyncio.run(handler()(cq))
    assert not delivered
    assert all("PRIVATE" not in str(value) and "synthetic" not in str(value) for value in notices)


def test_export_callback_only_in_owner_menu(export_ui):
    assert "server:diagnostics" in {button.callback_data for row in bot.server_menu(100).inline_keyboard for button in row}
    assert "server:diagnostics" not in {button.callback_data for row in bot.server_menu(200).inline_keyboard for button in row}


def test_export_uses_seeded_metadata_not_private_results(export_ui, monkeypatch, tmp_path):
    cq, delivered, notices = export_ui
    facts = {"private-host.local": {"name": "PRIVATE name", "os": "Darwin", "version": "4.1.2",
        "online": True, "last_seen": 1, "raw_text": "PRIVATE result", "coordinates": [41.2, 69.3],
        "capabilities": {"snapshot": {"schema": 1, "checked_at": "2026-10-08T10:00:00Z",
            "items": [{"id": "camera", "state": "granted", "detail": "PRIVATE permission text", "settings_uri": "private://url"}]},
            "feature_status": {"battery": "supported"}}, "capabilities_seen": 1}}
    monkeypatch.setattr(bot.devices, "all", lambda: facts)
    store = check_store.CheckStore(tmp_path / "checks.json")
    store.record_check(check_store.CheckRecord("private-host.local", "cmd:sys_uptime", "macos", "4.1.2",
        "2026-10-08T10:00:00Z", 12, "succeeded", "abc123"))
    monkeypatch.setattr(check_store, "_default", store)
    asyncio.run(handler()(cq))
    assert len(delivered) == 1
    content = delivered[0].data.decode()
    assert "PRIVATE" not in content and "private-host" not in content and "coordinates" not in content
    doc = json.loads(content)
    assert doc["checks"][0]["freshness"] == "stale"
    assert doc["checks"][0]["platform"] == "macos"
    assert doc["checks"][0]["device_id"] == doc["capabilities"][0]["device_id"]
    assert doc["correlation_ids"] == ["abc123"]
    assert list(tmp_path.iterdir()) == [store.path], "export must not persist bundle"
