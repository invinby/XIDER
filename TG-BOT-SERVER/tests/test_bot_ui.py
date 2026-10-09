"""Тесты UI-редизайна бота: device_card и новые категорийные меню (2026-09-05)."""

import asyncio
import ast
import base64
import time
from pathlib import Path

import pytest

import bot


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


def _shell_fixture(monkeypatch, tmp_path):
    from types import SimpleNamespace
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives import serialization
    Callback, rendered = _admin_fixture(monkeypatch, tmp_path)
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
    assert "whoami" not in caplog.text
    assert bot.command_approval.get_pending(pending.request_id) is None


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
    monkeypatch.setattr(bot, "verify_message_detailed", lambda data: (data, "ok"))
    monkeypatch.setattr(bot, "ENCRYPT_PAYLOAD", False)
    monkeypatch.setattr(bot.bot_settings, "is_blocked", lambda _id: False)
    topic = f"{bot.MQTT_PREFIX}/a/status"
    bot.on_mqtt_message(topic, {"type": "status", "command_approval_protocol": 1})
    assert bot._shell_request_allowed(bot.ADMIN_ID, "a")
    bot.on_mqtt_message(topic, {"type": "status"})
    assert not bot._shell_request_allowed(bot.ADMIN_ID, "a")


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
    "cat:power", "versions:device", "cat:device", "cat:pranks",
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

    async def replace(_cq, text, reply_markup=None):
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
    assert DEVICE_SECTIONS.issubset(set(cbs))
    assert "cat:media" not in cbs
    assert "cat:input" not in cbs


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
                    "cat:terminal", "cat:power", "cat:device", "cat:pranks",
                    "cmd:screenshot", "files:list", "cmd:apps", "proc:0", "dev:dev1",
                }
            ),
    )

    assert _callback_data(bot.main_menu(100)) == [
        "menu:devices", "menu:server", "menu:admin", "menu:about",
    ]
    assert _callback_data(bot.main_menu(200)) == [
        "menu:devices", "menu:server", "menu:about",
    ]
    assert _callback_data(bot.main_menu(300)) == [
        "menu:guest_devices", "menu:server", "menu:about",
    ]
    assert _callback_data(bot.main_menu(400)) == []

    expected_owner_sections = {
        "cat:system", "cat:screen", "cat:files", "cat:network", "cat:terminal",
        "cat:power", "versions:device", "cat:device", "cat:pranks",
    }
    owner_device = set(_callback_data(bot.device_menu("dev1", 100)))
    assert expected_owner_sections <= owner_device
    assert "dev:all" in _callback_data(bot.devices_menu(100))
    assert "ev:menu" in _callback_data(bot.admin_menu())

    user_device = set(_callback_data(bot.device_menu("dev1", 200)))
    assert {
        "cat:system", "cat:screen", "cat:files", "cat:network", "cat:terminal",
        "cat:power", "cat:device", "cat:pranks",
    } <= user_device
    assert "versions:device" not in user_device
    assert not any(callback.startswith(("devmg:", "admin:", "server:")) for callback in user_device)


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

    async def replace(_cq, text, reply_markup=None):
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
        ["menu:devices", "menu:server"],
        ["menu:admin", "menu:about"],
    ]

    device_rows = [
        [button.callback_data for button in row]
        for row in bot.device_menu("dev1").inline_keyboard
    ]
    assert device_rows[:5] == [
        ["cat:system", "cat:screen"],
        ["cat:files", "cat:network"],
        ["cat:terminal", "cat:power"],
        ["versions:device", "cat:device"],
        ["cat:pranks", "cfm:stop"],
    ]
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

    async def replace(_cq, text, reply_markup=None):
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

    async def replace(_cq, text, reply_markup=None):
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

    async def replace(_cq, text, reply_markup=None):
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

    async def replace(_cq, text, reply_markup=None):
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
        ["admin:texts", "admin:style"],
        ["ev:menu", "menu:server"],
        ["menu:main"],
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


def test_agent_release_catalog_has_a_direct_install_action(monkeypatch):
    import release_catalog
    from types import SimpleNamespace

    session = bot.SessionRegistry()
    session["target"] = "device-1"
    monkeypatch.setattr(bot, "SESSION", session)
    _setup(monkeypatch, {"device-1": {
        "name": "My device", "os": "macOS 27", "version": "4.2.0",
        "update_mode": "source", "release_update_ready": True,
        "online": True, "last_seen": time.time(),
    }})
    release = release_catalog.Release(
        tag="v4.2.0", version=(4, 2, 0), name="Chosen release",
        published_at="2026-10-07T00:00:00Z", notes="Signed release",
        asset_names=frozenset({
            "XGENT-MCS-macos-bundle.zip", "XIDER-source.zip", "release-manifest.json",
        }),
    )
    monkeypatch.setattr(bot.release_catalog.catalog, "list", lambda: [release])
    cards = []

    async def replace(_cq, text, reply_markup=None):
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

    async def replace(_cq, text, reply_markup=None):
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


@pytest.mark.parametrize("action", ["screenshot", "webcam", "mic"])
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
            return {"image": base64.b64encode(b"fixture-image").decode("ascii"),
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
    })
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
        bot.back_to_device_kb(), bot.power_menu(), bot.confirm_power_menu("shutdown"),
        bot.rotate_menu(), bot.media_menu(), bot.screen_menu(), bot.wallpaper_menu(),
        bot.input_menu(), bot.system_menu(), bot.network_menu(), bot.files_menu(),
        bot.terminal_menu(), bot.power_menu_new(), bot.guardian_menu(),
        *(bot.pranks_menu(page=page, target="device-1") for page in range(1, 6)),
        bot.device_settings_menu(), bot.mic_options_menu(), bot.vol_options_menu(),
        bot.confirm_kb("stop:ok"), bot.events_menu(), bot.quiet_hours_menu(),
        bot.digest_menu(), bot.admins_menu(), bot.admin_menu(), bot.admin_texts_menu(),
        bot.admin_users_menu(), bot.admin_user_menu(0), bot.admin_user_menu(200), bot.admin_devices_menu(200),
        bot.admin_permissions_menu(0), bot.guest_devices_menu(), bot.server_menu(bot.ADMIN_ID),
        *(bot.server_confirm_menu(action) for action in ("restart", "update", "rollback")),
        *(bot.confirm_power_menu(action) for action in ("shutdown", "reboot", "sleep")),
        bot.blocked_menu(), bot._version_root_menu(),
        bot._version_root_menu(server=True),
        bot.manual_install_os_keyboard(), bot.manual_install_os_keyboard(selected_os="windows"),
        bot.manual_install_os_keyboard(selected_os="macos"),
        bot.url_count_keyboard(), bot.url_count_keyboard(is_owner=True),
        bot.url_favorites_keyboard(), bot.url_favorites_keyboard(is_owner=True),
        bot._favorites_kb("device-1", ["status"]),
        bot._version_install_confirmation_kb("v4.1.0"),
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
            "versions:install_confirm:v4.1.0", "versions:install_cancel:v4.1.0"} <= callbacks
    # Some screens build their keyboard inside a handler. Evaluate only the
    # callback expressions with controlled samples, never execute the handler
    # or its network/disk/publish side effects. Every expression is included,
    # including confirmation, paging and owner notification branches.
    tree = ast.parse(Path(bot.__file__).read_text(encoding="utf-8"))
    context = {
        "message": SimpleNamespace(from_user=SimpleNamespace(id=200)),
        "device_id": "device-1", "info_book": bot.info_book, "index": 1,
        "it": {"pid": 123}, "page": 1, "i": 0, "notes_page": 1,
        "token": "0123456789abcdef0123456789abcdef",
        "approve_callback": "cmd:shell-approve:sample", "cancel_callback": "cmd:shell-cancel:sample",
        "release": SimpleNamespace(tag="v4.1.0"),
        "bootstrap_release": SimpleNamespace(tag="v4.1.0"), "tag": "v4.1.0",
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


def _expected_callback_gate(callback):
    """Independent route contract: never inferred from the installed filter."""
    if callback in {"shell:ok", "menu:admin", "menu:target", "url:favorites", "versions:server"} or callback.startswith(
        ("admin:", "devmg:", "install:", "server:", "server_confirm:", "url:fav:", "versions:bootstrap:", "cmd:shell-approve:", "cmd:shell-cancel:")
    ):
        return "OwnerFilter"
    if callback in {"menu:main", "menu:about", "menu:server", "menu:guest_devices", "guest:readonly", "url:cancel"} or callback.startswith(("about:chapter:", "url:count:")):
        return "ReadOnlyFilter"
    if callback in {"cmd:net_ping", "cmd:geo_location"}:
        return "AnyAccessFilter"
    assert callback == "noop" or callback.startswith((
        "cmd:", "cat:", "dev:", "back:", "all:", "ev:", "adm:", "quiet:", "digest:",
        "files:", "fun:", "power:", "power_confirm:", "proc:", "kill:", "killq:", "rotate:",
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
on_cmd_webcam media webcam 20 private_image privacy_read
on_cmd_clipboard input clipboard 10 private_clipboard privacy_read
on_cmd_processes terminal processes 15 process_list privacy_read
on_proc_page terminal processes 15 process_list privacy_read
on_cmd_battery system battery 15 device_telemetry read
on_cmd_network network network 15 network_configuration privacy_read
on_cmd_services terminal services 15 service_list privacy_read
on_cmd_capabilities system capabilities 15 device_capabilities read
on_cmd_sysinfo system sysinfo 12 device_identifiers privacy_read
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
on_guest_devices on_guest_readonly on_noop on_back_to_device on_dev on_cat_device on_cat_files on_files_menu
on_cat_input on_cat_media on_cat_network on_cat_power on_cat_pranks on_cat_screen on_cat_system on_cat_terminal
on_cmd_guardian_menu on_cmd_rotate on_menu_wallpaper on_fav_menu on_mic_opts on_vol_opts on_prank_page
on_ev_menu on_ev_quiet on_ev_digest on_ev_admins on_admin_users on_admin_user on_admin_devices
on_admin_permissions on_admin_texts on_admin_style on_devmg_blocked on_manual_install on_versions_device
on_versions_server on_versions_list on_versions_detail on_url_favorites on_history on_kill_confirm
on_power_action on_server_dangerous_request on_devmg_delete on_devmg_uninstall on_devmg_block
on_devmg_clear_all on_versions_install_select on_versions_install_cancel on_url_cancel
""".split())


_DEPENDENT_ACTION_CONTRACTS = {
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
        elif callback in {"menu:main", "menu:devices", "menu:about", "back:device"}:
            metadata["required_permission"] = f"user navigation: {callback}"
        elif callback.startswith("dev:"):
            metadata["required_permission"] = f"device selection grant: {callback}"
    name = route.callback.__name__
    if name in _ACTION_CONTRACTS:
        category, command, timeout, sensitivity, risk = _ACTION_CONTRACTS[name]
        metadata.update(category=category, wire_action=command, timeout=timeout,
                        os_support=("Windows", "macOS"), result_sensitivity=sensitivity, risk=risk)
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
        if name == "on_history":
            metadata.update(category="history", result_sensitivity="private command history", risk="privacy read")
        if name in {"on_menu_devices", "on_guest_devices", "on_dev", "on_cat_device"}:
            metadata["result_sensitivity"] = "role-filtered device inventory"
    elif name == "on_mic_dur":
        metadata.update(category="media", wire_action="mic", os_support=("Windows", "macOS"),
                        timeout=int(callback.split(":")[1]) + 12.0,
                        result_sensitivity="private microphone audio", risk="privacy recording")
    elif name == "on_server_confirm":
        metadata.update(category="server", os_support=("Linux/systemd",),
                        timeout={"restart": 30.0, "update": 180.0, "rollback": 180.0}[callback.split(":")[1]],
                        result_sensitivity="server operation output", risk="server restart/executable replacement")
    elif name.startswith(("on_admin_", "on_adm_", "on_ev_")) or name in {
        "on_quiet_set", "on_digest_set", "on_fav_toggle", "on_server_approval", "on_server_autostart_toggle",
        "on_url_favorite_delete", "on_url_favorite_save_prompt", "on_devmg_allow", "on_devmg_rename",
        "on_devmg_delete_ok", "on_devmg_blockok", "on_devmg_unblock", "on_devmg_clear_all_confirm", "on_devmg_manualadd",
    }:
        metadata.update(category="administration", result_sensitivity="identities/settings/device records",
                        risk="local configuration/access mutation")
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
                    f"user navigation: {callback}" if callback in {"menu:main", "menu:devices", "menu:about", "back:device"} else
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
            command = metadata.get("wire_action")
            if command:
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
