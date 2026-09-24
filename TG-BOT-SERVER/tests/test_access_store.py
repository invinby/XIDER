from types import SimpleNamespace

import access_store as store


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
    assert store.toggle_device(100, 200, "laptop-a", 100) is True
    assert store.toggle_callback(100, 200, "cmd:status", 100) is True

    assert store.can_use_callback(200, "menu:devices", 100) is True
    assert store.can_use_callback(200, "dev:laptop-a", 100) is True
    assert store.can_use_callback(200, "dev:laptop-b", 100) is False
    assert store.can_use_callback(200, "cmd:status", 100, "laptop-a") is True
    assert store.can_use_callback(200, "cmd:screenshot", 100, "laptop-a") is False


def test_block_replaces_access_and_audit_redacts_tokens(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    store.register_start(_user(200))
    assert store.set_blocked(100, 200, True, 100) is True
    assert store.get_role(200, 100) == store.Role.BLOCKED
    assert store.can_use_callback(200, "cmd:status", 100) is False

    store.append_audit("message", actor_id=200, detail="token=123456:abcdefghijklmnopqrstuvwxyz")
    rows = store.recent_audit()
    assert "[REDACTED]" in rows[0]["detail"] or "[TOKEN]" in rows[0]["detail"]
