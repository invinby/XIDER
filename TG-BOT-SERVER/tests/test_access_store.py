import asyncio
from types import SimpleNamespace

import access_store as store
import roles


def _user(user_id: int, username: str = "tester"):
    return SimpleNamespace(id=user_id, username=username, first_name="Test", last_name="User")


def _isolate(monkeypatch, tmp_path):
    monkeypatch.setattr(store, "FILE", tmp_path / "access.json")
    monkeypatch.setattr(store, "AUDIT_FILE", tmp_path / "audit.jsonl")


def test_first_start_creates_guest_and_owner_is_immutable(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    record, first = store.register_start(_user(200))
    assert first is True
    assert record["role"] == store.Role.GUEST
    assert store.get_role(100, 100) == store.Role.OWNER
    assert store.set_role(100, 100, store.Role.USER, 100) is False
    assert store.set_blocked(100, 100, True, 100) is False


def test_user_permissions_are_exact_and_bound_to_device(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    store.register_start(_user(200))
    assert store.set_role(100, 200, store.Role.USER, 100) is True
    assert store.toggle_callback(100, 200, "cmd:status", 100) is True

    assert store.can_use_callback(200, "menu:devices", 100) is True
    assert store.can_use_callback(200, "cmd:status", 100, "laptop-a") is False
    assert store.toggle_device(100, 200, "laptop-a", 100) is True
    assert store.can_use_callback(200, "dev:laptop-a", 100) is True
    assert store.can_use_callback(200, "dev:laptop-b", 100) is False
    assert store.can_use_callback(200, "cmd:status", 100, "laptop-a") is True
    assert store.can_use_callback(200, "cmd:screenshot", 100, "laptop-a") is False
    assert store.can_use_callback(200, "dev:all", 100) is False


def test_category_access_comes_only_from_granted_action_in_same_device(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    store.register_start(_user(200))
    store.set_role(100, 200, store.Role.USER, 100)
    store.toggle_device(100, 200, "laptop-a", 100)
    store.toggle_callback(100, 200, "cmd:screenshot", 100)

    assert store.can_use_callback(200, "cat:media", 100, "laptop-a") is True
    assert store.can_use_callback(200, "cat:system", 100, "laptop-a") is False
    assert store.can_use_callback(200, "cat:power", 100, "laptop-a") is False
    assert store.can_use_callback(200, "cat:media", 100, "laptop-b") is False

    store.toggle_callback(100, 200, "cmd:lock", 100)
    assert store.can_use_callback(200, "cat:power", 100, "laptop-a") is True
    assert store.can_use_callback(200, "power:shutdown", 100, "laptop-a") is False


def test_legacy_coowner_is_downgraded_and_cannot_be_granted(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    store.register_start(_user(200))
    store._update(200, lambda record: record.update(role=store.Role.COOWNER))

    assert store.get_role(200, 100) == store.Role.USER
    assert store.set_role(100, 200, store.Role.COOWNER, 100) is False


def test_legacy_admin_list_does_not_grant_full_access(monkeypatch):
    monkeypatch.setattr(store, "get_role", lambda user_id, owner_id: store.Role.GUEST)
    monkeypatch.setattr(
        roles.bot_settings, "get",
        lambda key, default=None: [200] if key == "admins" else default,
    )

    assert roles.get_user_role(200) == store.Role.USER
    assert roles.get_user_role(201) == store.Role.GUEST


def test_shell_remains_owner_only_even_if_a_user_callback_is_granted(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    store.register_start(_user(200))
    assert store.set_role(100, 200, store.Role.USER, 100) is True
    assert store.toggle_device(100, 200, "laptop-a", 100) is True
    assert store.toggle_callback(100, 200, "full_device", 100) is True
    assert store.toggle_callback(100, 200, "cmd:shell", 100) is True

    assert store.can_use_callback(200, "cmd:shell", 100, "laptop-a") is False
    assert store.can_use_callback(200, "cmd:screenshot", 100, "laptop-a") is True
    assert store.can_use_callback(200, "devmg:clear_all", 100, "laptop-a") is False
    assert store.can_use_callback(200, "devmg:clear_all_confirm", 100, "laptop-a") is False
    assert store.can_use_callback(200, "menu:target", 100, "laptop-a") is False
    assert store.can_use_callback(200, "all:status", 100, "laptop-a") is False
    assert store.can_use_callback(200, "admin:users", 100, "laptop-a") is False
    assert store.can_use_callback(200, "ev:toggle:notify_offline", 100, "laptop-a") is False
    assert store.can_use_callback(200, "versions:server", 100, "laptop-a") is False
    assert store.can_use_callback(200, "cmd:status", 100, "other-device") is False


def test_legacy_global_callback_grants_are_ignored(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    store.register_start(_user(200))
    assert store.set_role(100, 200, store.Role.USER, 100) is True
    assert store.toggle_device(100, 200, "laptop-a", 100) is True
    assert store.toggle_callback(100, 200, "devmg:clear_all", 100) is True

    assert store.can_use_callback(200, "devmg:clear_all", 100, "laptop-a") is False


def test_revoking_removed_device_removes_all_user_grants(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    store.register_start(_user(200))
    store.set_role(100, 200, store.Role.USER, 100)
    store.toggle_device(100, 200, "laptop-a", 100)
    store.toggle_device(100, 200, "laptop-b", 100)

    assert store.revoke_devices(["laptop-a"], actor_id=100) == 1
    assert store.get_user(200)["permissions"]["devices"] == ["laptop-b"]
    assert store.can_use_callback(200, "cmd:status", 100, "laptop-a") is False


def test_block_replaces_access_and_audit_redacts_tokens(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    store.register_start(_user(200))
    assert store.set_blocked(100, 200, True, 100) is True
    assert store.get_role(200, 100) == store.Role.BLOCKED
    assert store.can_use_callback(200, "cmd:status", 100) is False

    store.append_audit("message", actor_id=200, detail="token=123456:abcdefghijklmnopqrstuvwxyz")
    rows = store.recent_audit()
    assert "[REDACTED]" in rows[0]["detail"] or "[TOKEN]" in rows[0]["detail"]


def test_message_redaction_handles_env_credentials_bearer_and_private_keys():
    raw = (
        "BOT_TOKEN=123456:abcdefghijklmnopqrstuvwxyz "
        "MQTT_PASSWORD=do-not-log Bearer abc.def.ghi\n"
        "-----BEGIN OPENSSH PRIVATE KEY-----\nprivate-material\n"
        "-----END OPENSSH PRIVATE KEY-----"
    )
    safe = store.redact_text(raw)

    assert "abcdefghijklmnopqrstuvwxyz" not in safe
    assert "do-not-log" not in safe
    assert "abc.def.ghi" not in safe
    assert "private-material" not in safe
    assert "[PRIVATE KEY REDACTED]" in safe


class _FakeState:
    def __init__(self, data=None, current="Form:wait_open_app"):
        self.data = dict(data or {})
        self.current = current
        self.cleared = False

    async def get_data(self):
        return dict(self.data)

    async def get_state(self):
        return self.current

    async def clear(self):
        self.cleared = True
        self.data.clear()
        self.current = None


class _FakeMessage(SimpleNamespace):
    pass


def _message(user_id: int, text: str = "launch"):
    return _FakeMessage(from_user=SimpleNamespace(id=user_id), text=text)


def test_pending_user_input_rechecks_the_original_device_and_action_grant(
    monkeypatch, tmp_path
):
    _isolate(monkeypatch, tmp_path)
    store.register_start(_user(200))
    assert store.set_role(100, 200, store.Role.USER, 100)
    assert store.toggle_device(100, 200, "mac-1", 100) is True
    assert store.toggle_callback(100, 200, "full_device", 100) is True
    monkeypatch.setattr(roles, "ADMIN_ID", 100)
    monkeypatch.setattr(roles, "Message", _FakeMessage)
    monkeypatch.setattr(roles, "_device_store", lambda: SimpleNamespace(get=lambda _id: object()))

    state = _FakeState({
        "authorization_callback": "cmd:open_app",
        "authorization_target": "mac-1",
    })
    admin_filter = roles.AdminFilter()
    assert asyncio.run(admin_filter(_message(200), state=state)) is True

    # Revoking the action grant while the text prompt is open must invalidate
    # that already-open form; the input cannot still reach MQTT.
    assert store.toggle_callback(100, 200, "full_device", 100) is False
    assert asyncio.run(admin_filter(_message(200), state=state)) is False
    assert state.cleared is True


def test_pending_user_input_fails_closed_for_removed_device_and_unknown_form(
    monkeypatch, tmp_path
):
    _isolate(monkeypatch, tmp_path)
    store.register_start(_user(200))
    assert store.set_role(100, 200, store.Role.USER, 100)
    assert store.toggle_device(100, 200, "mac-1", 100) is True
    assert store.toggle_callback(100, 200, "full_device", 100) is True
    monkeypatch.setattr(roles, "ADMIN_ID", 100)
    monkeypatch.setattr(roles, "Message", _FakeMessage)
    monkeypatch.setattr(roles, "_device_store", lambda: SimpleNamespace(get=lambda _id: None))

    state = _FakeState({
        "authorization_callback": "cmd:open_app",
        "authorization_target": "mac-1",
    })
    admin_filter = roles.AdminFilter()
    assert asyncio.run(admin_filter(_message(200), state=state)) is False
    assert state.cleared is True

    missing = _FakeState({})
    assert asyncio.run(admin_filter(_message(200), state=missing)) is False
    assert missing.cleared is True


def test_wallpaper_photo_state_is_invalidated_when_full_device_access_is_revoked(
    monkeypatch, tmp_path
):
    _isolate(monkeypatch, tmp_path)
    store.register_start(_user(200))
    assert store.set_role(100, 200, store.Role.USER, 100)
    assert store.toggle_device(100, 200, "mac-1", 100) is True
    assert store.toggle_callback(100, 200, "full_device", 100) is True
    monkeypatch.setattr(roles, "ADMIN_ID", 100)
    monkeypatch.setattr(roles, "Message", _FakeMessage)
    monkeypatch.setattr(roles, "_device_store", lambda: SimpleNamespace(get=lambda _id: object()))

    state = _FakeState(
        {
            "authorization_callback": "cmd:wallpaper_photo_guide",
            "authorization_target": "mac-1",
        },
        current="Form:wait_wallpaper_photo",
    )
    admin_filter = roles.AdminFilter()
    assert asyncio.run(admin_filter(_message(200, text=None), state=state)) is True

    # Pressing the guide is not a permanent ticket: revocation before the
    # photo message invalidates this pending one-shot upload state.
    assert store.toggle_callback(100, 200, "full_device", 100) is False
    assert asyncio.run(admin_filter(_message(200, text=None), state=state)) is False
    assert state.cleared is True


def test_pending_input_cancel_is_allowed_and_owner_remains_unrestricted(
    monkeypatch, tmp_path
):
    _isolate(monkeypatch, tmp_path)
    store.register_start(_user(200))
    assert store.set_role(100, 200, store.Role.USER, 100)
    monkeypatch.setattr(roles, "ADMIN_ID", 100)
    monkeypatch.setattr(roles, "Message", _FakeMessage)
    admin_filter = roles.AdminFilter()

    assert asyncio.run(admin_filter(_message(200, "/cancel@xider_bot"))) is True
    assert asyncio.run(admin_filter(_message(100), state=None)) is True
