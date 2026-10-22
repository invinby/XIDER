import asyncio
from types import SimpleNamespace

import access_store as store
import roles
import pytest


def test_command_audit_is_typed_metadata_only(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    store.append_command_audit(request_id="request_123", command_hash="a" * 64,
        status="completed", requester_id=200, approver_id=100,
        device_id="MacBook-Air.local", exit_code=0)
    row = store.recent_audit()[0]
    assert row["request_id"] == "request_123"
    assert row["command_hash"] == "a" * 64
    assert row["status"] == "completed"
    assert row["requester_id"] == 200 and row["approver_id"] == 100
    assert row["device_id"] == "MacBook-Air.local" and row["exit_code"] == 0
    assert type(row["at"]) is int
    assert set(row) == {"kind", "at", "request_id", "command_hash", "status",
                        "requester_id", "approver_id", "device_id", "exit_code"}
    with pytest.raises(TypeError):
        store.append_command_audit(request_id="request_123", command_hash="a" * 64,
            status="completed", requester_id=200, approver_id=100,
            device_id="a", exit_code=0, output="secret output")


def test_confirmed_shell_cancellation_is_a_valid_typed_audit_outcome(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    store.append_command_audit(
        request_id="request_cancel", command_hash="b" * 64,
        status="cancelled", requester_id=200, approver_id=100,
        device_id="mac-a", exit_code=-9,
    )
    row = store.recent_audit()[0]
    assert row["status"] == "cancelled"
    event = store.get_audit_event(100, 100, store.audit_event_reference(row))
    assert event["outcome"] == "cancelled"


def test_shell_timing_summary_groups_only_the_two_approved_intervals(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    for at, request_id, status, device in (
        (10, "request_a", "attempting", "mac-a"), (12, "request_a", "sent", "mac-a"),
        (20, "request_a", "completed", "mac-a"), (100, "request_b", "attempting", "pc-b"),
        (101, "request_b", "publish_failed", "pc-b"),
    ):
        store._write_audit_row({"kind": "command_execution", "at": at, "request_id": request_id,
            "command_hash": "a" * 64, "status": status, "requester_id": 200,
            "approver_id": 100, "device_id": device, "exit_code": None})
    report = store.shell_execution_timing_summary(100, 100)
    assert report == {"state": "available", "resolution_seconds": 1, "sampled_requests": 2,
        "dispatch": {"count": 1, "mean_seconds": 2.0, "median_seconds": 2, "max_seconds": 2},
        "result": {"count": 1, "mean_seconds": 8.0, "median_seconds": 8, "max_seconds": 8}}
    assert store.shell_execution_timing_summary(200, 100)["state"] == "denied"
    assert "request_a" not in str(report) and "mac-a" not in str(report) and "command_hash" not in str(report)


def test_owner_audit_filter_matches_inclusive_time_actor_type_and_device(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    store._write_audit_row({"at": 100, "kind": "device_online", "actor_id": 100,
                            "device_id": "mac-a", "detail": "first"})
    store._write_audit_row({"at": 150, "kind": "command_execution", "requester_id": 200,
                            "approver_id": 100, "device_id": "mac-a", "detail": "second"})
    store._write_audit_row({"at": 200, "kind": "device_online", "actor_id": 100,
                            "device_id": "pc-b", "detail": "third"})

    rows = store.filter_audit(100, 100, since=100, until=150, actor_id=100,
                              event_type="command_execution", device_id="mac-a")
    assert [row["detail"] for row in rows] == ["second"]
    assert store.filter_audit(200, 100) is None


def test_owner_audit_filter_ignores_malformed_rows_and_rejects_invalid_ranges(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    store._write_audit_row({"at": [100], "kind": "device_online", "actor_id": [100],
                            "device_id": {"bad": "shape"}})
    store._write_audit_row({"at": 100, "kind": "device_online", "actor_id": 100,
                            "device_ids": ["mac-a"]})
    rows = store.filter_audit(100, 100, actor_id=100, device_id="mac-a")
    assert [row["at"] for row in rows] == [100]
    assert store.filter_audit(100, 100, since=200, until=100) is None
    assert store.filter_audit(100, 100, actor_id=True) is None


def test_owner_audit_event_is_normalized_and_never_returns_raw_detail_or_credentials(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    store.append_command_audit(
        request_id="request-a", command_hash="a" * 64, status="completed",
        requester_id=200, approver_id=100, device_id="mac-a", exit_code=0,
    )
    row = store.recent_audit()[0]
    reference = store.audit_event_reference(row)
    event = store.get_audit_event(100, 100, reference)

    assert event == {
        "event_id": reference, "at": row["at"], "kind": "command_execution",
        "actor_id": 100, "target_id": None, "device_id": "mac-a",
        "outcome": "completed", "fields": {"requester_id": 200, "approver_id": 100, "exit_code": 0},
    }
    assert store.get_audit_event(200, 100, reference) is None
    assert store.get_audit_event(100, 100, "0" * 20) is None
    assert "command_hash" not in str(event) and "request_id" not in str(event)


def test_owner_audit_event_does_not_expose_freeform_legacy_detail(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    store.append_audit("bot_text_updated", actor_id=100, target_id=200,
                       detail="token=secret-value user supplied text")
    row = store.recent_audit()[0]
    event = store.get_audit_event(100, 100, store.audit_event_reference(row))
    assert event["outcome"] == "unknown"
    assert event["fields"] == {}
    assert "detail" not in event and "secret-value" not in str(event)


def test_owner_audit_export_is_range_limited_redacted_and_capped(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    store._write_audit_row({"at": 100, "kind": "command_execution", "requester_id": 200,
                            "approver_id": 100, "status": "completed", "exit_code": 0,
                            "device_id": "mac-a", "request_id": "private-id",
                            "command_hash": "a" * 64, "detail": "password=do-not-export"})
    store._write_audit_row({"at": 200, "kind": "bot_text_updated", "actor_id": 100,
                            "detail": "token=must-not-appear"})
    exported = store.export_audit(100, 100, since=100, until=200, limit=100)

    assert exported["schema"] == "xider.audit-export.v1"
    assert exported["range"] == {"since": 100, "until": 200}
    assert len(exported["events"]) == 2
    assert exported["events"][0]["outcome"] == "unknown"
    assert exported["events"][1]["outcome"] == "completed"
    assert "private-id" not in str(exported) and "must-not-appear" not in str(exported)
    assert "do-not-export" not in str(exported) and "command_hash" not in str(exported)
    assert store.export_audit(200, 100, since=100, until=200) is None
    assert store.export_audit(100, 100, since=0, until=31 * 86_400) is None


def test_owner_shell_audit_export_filters_dates_and_status_without_command_content(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    store._write_audit_row({"at": 100, "kind": "command_execution", "request_id": "private-one",
                            "command_hash": "a" * 64, "status": "completed", "requester_id": 200,
                            "approver_id": 100, "device_id": "mac-a", "exit_code": 0,
                            "command": "echo secret-command", "output": "token=secret-output"})
    store._write_audit_row({"at": 150, "kind": "command_execution", "request_id": "private-two",
                            "command_hash": "b" * 64, "status": "failed", "requester_id": 200,
                            "approver_id": 100, "device_id": "mac-a", "exit_code": 2,
                            "command": "echo other-secret"})
    store._write_audit_row({"at": 175, "kind": "device_online", "status": "completed", "device_id": "mac-a"})
    store._write_audit_row({"at": 250, "kind": "command_execution", "request_id": "private-three",
                            "command_hash": "c" * 64, "status": "completed", "requester_id": 200,
                            "approver_id": 100, "device_id": "mac-a", "exit_code": 0})

    exported = store.export_shell_command_audit(
        100, 100, since=100, until=200, statuses=["completed"], device_id="mac-a", limit=100,
    )

    assert exported["schema"] == "xider.shell-audit-export.v1"
    assert exported["range"] == {"since": 100, "until": 200}
    assert exported["filters"] == {"statuses": ["completed"], "device_id": "mac-a"}
    assert len(exported["events"]) == 1
    assert exported["events"][0] == {
        "at": 100, "status": "completed", "actor_id": 100, "target_id": None,
        "device_id": "mac-a", "requester_id": 200, "approver_id": 100, "exit_code": 0,
    }
    assert exported["truncated"] is False
    assert all(secret not in str(exported) for secret in (
        "private-one", "private-two", "private-three", "command_hash", "secret-command", "secret-output", "other-secret",
    ))


def test_owner_shell_audit_export_rejects_non_owner_invalid_status_and_unbounded_range(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)

    assert store.export_shell_command_audit(200, 100, since=1, until=2, statuses=["completed"]) is None
    assert store.export_shell_command_audit(100, 100, since=1, until=2, statuses=[]) is None
    assert store.export_shell_command_audit(100, 100, since=1, until=2, statuses=["unknown"]) is None
    assert store.export_shell_command_audit(100, 100, since=0, until=31 * 86_400, statuses=["completed"]) is None


@pytest.mark.parametrize("raw,secret", [
    ('password=[REDACTED]synthetic-second', 'synthetic-second'),
    ('password=[REDACTED] first synthetic-second', 'synthetic-second'),
    ('Authorization: Basic c3ludGhldGljLXBhc3N3b3Jk', 'c3ludGhldGljLXBhc3N3b3Jk'),
    ('password=first synthetic-second', 'synthetic-second'),
    ('password="synthetic password"', "synthetic password"),
    ('token=synthetic-token', "synthetic-token"),
    ('123456:abcdefghijklmnopqrstuvwxyz', "123456:abcdefghijklmnopqrstuvwxyz"),
    ('-----BEGIN PRIVATE KEY-----\nprivate-fixture\n-----END PRIVATE KEY-----', "private-fixture"),
    ('-----BEGIN OPENSSH PRIVATE KEY-----\ntruncated-fixture', "truncated-fixture"),
    ('password="truncated quoted password', "quoted password"),
    ('password="first synthetic-second' + chr(92), "synthetic-second"),
    ('-----BEGIN ENCRYPTED PRIVATE KEY-----\nencrypted-fixture', "encrypted-fixture"),
])
def test_shell_output_redacts_credentials_without_losing_nonsecret_lines(raw, secret):
    safe = store.redact_shell_output("first line\n" + raw)
    assert secret not in safe
    assert safe.startswith("first line\n")


def test_shell_redactor_summary_counts_real_categories_without_storing_secret_values():
    raw = (
        "Authorization: Bearer bearer-fixture\n"
        "password=plain-fixture\n"
        "123456:abcdefghijklmnopqrstuvwxyz\n"
        "-----BEGIN PRIVATE KEY-----\nprivate-key-fixture\n-----END PRIVATE KEY-----"
    )

    safe, summary = store.redact_shell_output_with_summary(raw)

    assert safe == store.redact_shell_output(raw)
    assert "bearer-fixture" not in safe and "plain-fixture" not in safe
    assert "abcdefghijklmnopqrstuvwxyz" not in safe and "private-key-fixture" not in safe
    assert summary == {
        "total": 5,
        "categories": {
            "bearer_token": 1,
            "credential_field": 2,
            "private_key": 1,
            "telegram_token": 1,
        },
    }


@pytest.mark.parametrize("operation", [
    lambda actor, target: store.set_role(actor, target, store.Role.USER, 100),
    lambda actor, target: store.set_blocked(actor, target, True, 100),
    lambda actor, target: store.toggle_device(actor, target, "a", 100),
    lambda actor, target: store.toggle_callback(actor, target, "cmd:status", 100),
])
def test_mutation_store_denies_nonowner_and_unknown_targets(monkeypatch, tmp_path, operation):
    _isolate(monkeypatch, tmp_path)
    store.register_start(_user(200))
    before = store.get_user(200)
    assert not operation(200, 200)
    assert store.get_user(200) == before
    assert not operation(100, 999)
    assert store.get_user(999) is None
    assert not operation(100, 100)


def test_unblock_restores_prior_role_and_exact_grants_without_elevation(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    store.register_start(_user(200))
    store.set_role(100, 200, store.Role.USER, 100)
    store.toggle_device(100, 200, "a", 100)
    store.toggle_callback(100, 200, "cmd:status", 100)
    store.set_blocked(100, 200, True, 100)
    store.set_blocked(100, 200, True, 100)
    assert not store.can_use_callback(200, "cmd:status", 100, "a")
    store.set_blocked(100, 200, False, 100)
    assert store.get_role(200, 100) == store.Role.USER
    assert store.can_use_callback(200, "cmd:status", 100, "a")
    assert not store.can_use_callback(200, "cmd:screenshot", 100, "a")
    store.set_role(100, 200, store.Role.GUEST, 100)
    store.set_blocked(100, 200, True, 100)
    store.set_blocked(100, 200, False, 100)
    assert store.get_role(200, 100) == store.Role.GUEST
    store.set_role(100, 200, store.Role.USER, 100)
    assert store.get_user(200)["permissions"] == {"devices": ["a"], "callbacks": ["cmd:status"]}


def test_temporary_block_preserves_reason_expires_and_can_be_extended(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    now = 1_800_000_000
    monkeypatch.setattr(store.time, "time", lambda: now)
    store.register_start(_user(200))
    store.set_role(100, 200, store.Role.USER, 100)

    assert store.set_blocked(100, 200, True, 100, expires_at=now + 3600, reason="rate limit")
    first = store.get_user(200)
    assert store.get_role(200, 100) == store.Role.BLOCKED
    assert first["blocked_until"] == now + 3600
    assert first["block_reason"] == "rate limit"
    assert first["role_before_block"] == store.Role.USER

    assert store.set_blocked(100, 200, True, 100, expires_at=now + 7200, reason="extended review")
    extended = store.get_user(200)
    assert extended["blocked_until"] == now + 7200
    assert extended["block_reason"] == "extended review"
    assert extended["role_before_block"] == store.Role.USER

    monkeypatch.setattr(store.time, "time", lambda: now + 7200)
    assert store.get_role(200, 100) == store.Role.USER
    expired = store.get_user(200)
    assert expired["blocked"] is False
    assert expired["block_reason"] == "extended review"
    assert store.recent_audit()[0]["kind"] == "user_block_expired"


def test_temporary_block_rejects_invalid_expiry_or_reason_without_mutation(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    now = 1_800_000_000
    monkeypatch.setattr(store.time, "time", lambda: now)
    store.register_start(_user(200))
    before = store.get_user(200)

    assert not store.set_blocked(100, 200, True, 100, expires_at=now, reason="valid reason")
    assert not store.set_blocked(100, 200, True, 100, expires_at=now + 60, reason="   ")
    assert store.get_user(200) == before


def test_audit_discards_raw_message_shell_file_location_payloads(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    for kind in ("telegram_message", "admin_message_sent", "shell", "stdout", "geo_location", "file_contents"):
        store.append_audit(kind, actor_id=200, detail="41.123,69.456 secret-file-bytes rm -rf / raw stdout")
    assert all(not row["detail"] for row in store.recent_audit())


def test_first_start_is_distinct_from_prior_callback_activity(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    store.touch_user(_user(200), "telegram_callback", "admin:users")
    assert store.register_start(_user(200))[1] is True
    assert store.register_start(_user(200))[1] is False


def test_persisted_owner_marker_cannot_create_a_second_owner(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    store.register_start(_user(200))
    store._update(200, lambda record: record.update(role=store.Role.OWNER))
    assert store.get_role(200, 100) == store.Role.GUEST
    assert not store.can_use_callback(200, "admin:audit", 100)
    assert store.get_role(100, 100) == store.Role.OWNER


def test_audit_drops_unknown_payload_and_redacts_paths_and_coordinates(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    store.append_audit("unclassified_payload", detail="raw stdout whoami private.txt")
    assert store.recent_audit()[0]["detail"] == ""
    store.append_audit("telegram_callback", detail=r"token=secret C:\Users\example\secret.txt /home/user/private.txt 41.123, 69.456")
    detail = store.recent_audit()[0]["detail"]
    for private in ("secret.txt", "private.txt", "Users", "41.123", "69.456", "token=secret"):
        assert private not in detail


def test_redaction_handles_quoted_credentials_with_spaces():
    safe = store.redact_text('"password": "very secret value", MQTT_PASSWORD=\'another private value\'')
    assert "very secret value" not in safe
    assert "another private value" not in safe
    assert safe.count("[REDACTED]") == 2


@pytest.mark.parametrize("raw", [
    r'{"password": "prefix\" TOPSECRET suffix ,; end"}',
    r'MQTT_PASSWORD="prefix\" TOPSECRET suffix ,; end"',
    r"MQTT_PASSWORD='prefix\' TOPSECRET suffix ,; end'",
])
def test_redaction_masks_entire_escaped_quoted_credentials(raw):
    safe = store.redact_text(raw)
    assert "TOPSECRET" not in safe
    assert "suffix" not in safe
    assert "prefix" not in safe
    assert "[REDACTED]" in safe


@pytest.mark.parametrize("raw", [
    r'C:\Users\John Smith\Documents\private.txt',
    r'file="C:\Users\John Smith\Documents\private.txt"',
    r"file='C:\Users\John Smith\Documents\private.txt'",
])
def test_redaction_masks_entire_windows_path_with_spaces(raw):
    safe = store.redact_text(raw)
    for fragment in ("John", "Smith", "Documents", "private.txt"):
        assert fragment not in safe
    assert "[PATH REDACTED]" in safe


@pytest.mark.parametrize("callback,full_device_allowed", [
    ("cmd:screenshot", True), ("cmd:status", True), ("files:get", True),
    ("fun:type", True), ("power_confirm:shutdown", True), ("kill:123", True),
    ("volq:50", True), ("micdur:10", True), ("rotate:90", True),
    ("fav:toggle:screenshot", True), ("rep:0", True), ("sleep:ok", True),
    ("cmd:shell", False), ("shell:ok", False), ("cfm:stop_all", False),
    ("stopall:ok", False), ("all:status", False), ("devmg:allow:device-1", False),
    ("admin:users", False), ("server:logs", False), ("server_confirm:restart", False),
    ("versions:install_confirm:v4.1.0", False), ("install:windows", False),
    ("cpu:history", False), ("cpu:history:30", False),
    ("memory:history", False), ("memory:history:30", False),
    ("catalog:fields:list", False), ("catalog:fields:create", False),
    ("ev:menu", False), ("quiet:22:8", False), ("digest:60", False),
    ("adm:rm:200", False), ("unknown:action", False),
])
def test_inventory_permission_matrix_is_device_bound_and_role_scoped(
    monkeypatch, tmp_path, callback, full_device_allowed,
):
    # Expectations are hand-classified fixtures, not recomputed from store's
    # prefix tables. A missing owner-only gate or widened family fails here.
    _isolate(monkeypatch, tmp_path)
    owner, user = 100, 200
    store.register_start(_user(user))
    assert store.can_use_callback(owner, callback, owner, "device-1") is True
    assert store.can_use_callback(user, callback, owner, "device-1") is False
    store.set_role(owner, user, store.Role.USER, owner)
    store.toggle_device(owner, user, "device-1", owner)
    assert store.can_use_callback(user, callback, owner, "device-1") is False
    store.toggle_callback(owner, user, "full_device", owner)
    assert store.can_use_callback(user, callback, owner, "device-1") is full_device_allowed
    assert store.can_use_callback(user, callback, owner, "other-device") is False
    assert store.can_use_callback(user, callback, owner, None) is False
    store.set_blocked(owner, user, True, owner)
    assert store.can_use_callback(user, callback, owner, "device-1") is False


def _user(user_id: int, username: str = "tester"):
    return SimpleNamespace(id=user_id, username=username, first_name="Test", last_name="User")


def _isolate(monkeypatch, tmp_path):
    monkeypatch.setattr(store, "FILE", tmp_path / "access.json")
    monkeypatch.setattr(store, "AUDIT_FILE", tmp_path / "audit.jsonl")


def test_owner_rbac_policy_snapshot_is_versioned_secret_free_and_verifiable(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    store.register_start(_user(200))
    assert store.set_role(100, 200, store.Role.USER, 100)
    assert store.toggle_callback(100, 200, "cmd:status", 100)
    assert store.toggle_device(100, 200, "laptop-a", 100)

    snapshot = store.create_rbac_policy_snapshot(100, 100, now=500)

    assert snapshot["schema_version"] == 1
    assert snapshot["owner_id"] == 100 and snapshot["created_at"] == 500
    assert snapshot["entries"] == [
        {"user_id": 100, "stored_role": "guest", "effective_role": "owner",
         "stored_blocked": False, "permissions_paused": False,
         "stored_callbacks": [], "stored_devices": [], "effective_callbacks": [],
         "effective_devices": [], "expirations": []},
        {"user_id": 200, "stored_role": "user", "effective_role": "user",
         "stored_blocked": False, "permissions_paused": False,
         "stored_callbacks": ["cmd:status"], "stored_devices": ["laptop-a"],
         "effective_callbacks": ["cmd:status"], "effective_devices": ["laptop-a"],
         "expirations": []},
    ]
    assert store.verify_rbac_policy_snapshot(snapshot)
    assert store.get_rbac_policy_snapshot(100, 100, snapshot["snapshot_id"]) == snapshot
    assert store.get_rbac_policy_snapshot(200, 100, snapshot["snapshot_id"]) is None
    assert not any(secret in str(snapshot) for secret in ("BOT_TOKEN", "username", "display_name", "password"))

    assert store.set_role(100, 200, store.Role.GUEST, 100)
    assert store.get_rbac_policy_snapshot(100, 100, snapshot["snapshot_id"]) == snapshot


def test_rbac_policy_snapshot_verification_rejects_tampering_and_non_owner_write(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    store.register_start(_user(200))
    assert store.create_rbac_policy_snapshot(200, 100) is None
    snapshot = store.create_rbac_policy_snapshot(100, 100, now=700)
    changed = {**snapshot, "entries": []}
    assert not store.verify_rbac_policy_snapshot(changed)

    data = store._load()
    data["policy_snapshots"][0]["entries"] = []
    store._save(data)
    assert store.get_rbac_policy_snapshot(100, 100, snapshot["snapshot_id"]) is None


def test_rbac_policy_snapshot_evaluates_temporary_block_at_snapshot_time_without_mutating_user(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setattr(store.time, "time", lambda: 100)
    store.register_start(_user(200))
    assert store.set_role(100, 200, store.Role.USER, 100)
    assert store.set_blocked(100, 200, True, 100, expires_at=200, reason="review")

    snapshot = store.create_rbac_policy_snapshot(100, 100, now=300)

    target = next(row for row in snapshot["entries"] if row["user_id"] == 200)
    assert target["stored_role"] == store.Role.BLOCKED
    assert target["stored_blocked"] is True
    assert target["effective_role"] == store.Role.USER
    assert store.get_user(200)["blocked"] is True


def test_owner_rbac_policy_snapshot_comparison_reports_exact_role_and_grant_changes(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    store.register_start(_user(200))
    assert store.set_role(100, 200, store.Role.USER, 100)
    assert store.toggle_callback(100, 200, "cmd:status", 100)
    assert store.toggle_device(100, 200, "laptop-a", 100)
    before = store.create_rbac_policy_snapshot(100, 100, now=500)

    assert store.toggle_callback(100, 200, "cmd:sysinfo", 100)
    assert store.toggle_device(100, 200, "laptop-a", 100) is False
    assert store.set_role(100, 200, store.Role.GUEST, 100)
    after = store.create_rbac_policy_snapshot(100, 100, now=600)

    diff = store.compare_rbac_policy_snapshots(100, 100, before["snapshot_id"], after["snapshot_id"])

    assert diff == {
        "from_snapshot_id": before["snapshot_id"],
        "to_snapshot_id": after["snapshot_id"],
        "from_created_at": 500,
        "to_created_at": 600,
        "changes": [{
            "user_id": 200,
            "role": {"before": store.Role.USER, "after": store.Role.GUEST},
            "stored_callbacks": {"added": ["cmd:sysinfo"], "removed": []},
            "stored_devices": {"added": [], "removed": ["laptop-a"]},
            "effective_callbacks": {"added": ["cmd:sysinfo"], "removed": []},
            "effective_devices": {"added": [], "removed": ["laptop-a"]},
        }],
    }
    assert store.compare_rbac_policy_snapshots(200, 100, before["snapshot_id"], after["snapshot_id"]) is None
    assert store.compare_rbac_policy_snapshots(100, 100, before["snapshot_id"], before["snapshot_id"]) is None


def test_owner_can_list_only_verified_rbac_snapshot_receipts(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    first = store.create_rbac_policy_snapshot(100, 100, now=800)
    second = store.create_rbac_policy_snapshot(100, 100, now=900)

    rows = store.list_rbac_policy_snapshots(100, 100, limit=1)

    assert rows == [{
        "snapshot_id": second["snapshot_id"], "created_at": 900,
        "entry_count": second["entry_count"], "schema_version": second["schema_version"],
    }]
    assert store.list_rbac_policy_snapshots(200, 100) is None
    assert first["snapshot_id"] != second["snapshot_id"]


def test_owner_can_undo_latest_grant_only_when_exact_post_state_still_matches(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    store.register_start(_user(200))
    assert store.set_role(100, 200, store.Role.USER, 100)
    assert store.toggle_callback(100, 200, "cmd:status", 100) is True
    latest = store.get_latest_grant_change(100, 200, 100)
    assert latest["action"] == "granted"
    assert latest["before"] == {"present": False, "expires_at": None}
    assert latest["after"] == {"present": True, "expires_at": None}

    undone = store.undo_latest_grant_change(
        100, 200, 100, latest["reference"], valid_callbacks={"cmd:status"}, valid_devices=set(),
    )

    assert undone["ok"] is True
    assert "cmd:status" not in store.get_effective_grants(200)["callbacks"]
    assert store.undo_latest_grant_change(
        100, 200, 100, latest["reference"], valid_callbacks={"cmd:status"}, valid_devices=set(),
    )["ok"] is False


def test_owner_grant_undo_fails_closed_on_policy_conflict_or_missing_device(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    store.register_start(_user(200))
    assert store.set_role(100, 200, store.Role.USER, 100)
    assert store.toggle_callback(100, 200, "cmd:status", 100) is True
    first = store.get_latest_grant_change(100, 200, 100)
    assert store.set_role(100, 200, store.Role.GUEST, 100)
    assert store.undo_latest_grant_change(
        100, 200, 100, first["reference"], valid_callbacks={"cmd:status"}, valid_devices=set(),
    )["ok"] is False

    store.register_start(_user(201))
    assert store.set_role(100, 201, store.Role.USER, 100)
    assert store.toggle_callback(100, 201, "cmd:status", 100) is True
    grant = store.get_latest_grant_change(100, 201, 100)
    pause = store.preview_user_grant_pause(100, 201, 100, available_device_ids=set(), now=1_000)
    paused = store.apply_user_grant_pause(
        100, 201, 100, paused=True, available_device_ids=set(), expected_snapshot=pause["snapshot"], now=1_001,
    )
    resume = store.preview_user_grant_pause(100, 201, 100, available_device_ids=set(), now=1_002)
    assert store.apply_user_grant_pause(
        100, 201, 100, paused=False, available_device_ids=set(), expected_snapshot=resume["snapshot"], now=1_003,
    )
    assert paused["paused"] is True
    assert store.undo_latest_grant_change(
        100, 201, 100, grant["reference"], valid_callbacks={"cmd:status"}, valid_devices=set(),
    )["ok"] is False

    assert store.set_role(100, 200, store.Role.USER, 100)
    assert store.toggle_device(100, 200, "laptop-a", 100) is True
    granted = store.get_latest_grant_change(100, 200, 100)
    assert store.toggle_device(100, 200, "laptop-a", 100) is False
    revoked = store.get_latest_grant_change(100, 200, 100)
    assert revoked["before"] == {"present": True, "expires_at": None}
    assert store.undo_latest_grant_change(
        100, 200, 100, revoked["reference"], valid_callbacks=set(), valid_devices=set(),
    )["ok"] is False
    assert store.undo_latest_grant_change(
        200, 200, 100, granted["reference"], valid_callbacks=set(), valid_devices={"laptop-a"},
    )["ok"] is False


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


def test_expired_callback_and_device_grants_are_not_effective(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    store.register_start(_user(200))
    assert store.set_role(100, 200, store.Role.USER, 100)
    monkeypatch.setattr(store.time, "time", lambda: 100)
    assert store.toggle_device(100, 200, "laptop-a", 100, expires_at=200)
    assert store.toggle_callback(100, 200, "cmd:status", 100, expires_at=200)

    monkeypatch.setattr(store.time, "time", lambda: 199)
    assert store.can_use_callback(200, "cmd:status", 100, "laptop-a") is True
    monkeypatch.setattr(store.time, "time", lambda: 200)
    assert store.can_use_callback(200, "cmd:status", 100, "laptop-a") is False
    assert store.get_effective_grants(200) == {"callbacks": [], "devices": []}


def test_first_toggle_after_expiry_regrants_callback_and_device(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    store.register_start(_user(200))
    assert store.set_role(100, 200, store.Role.USER, 100)
    monkeypatch.setattr(store.time, "time", lambda: 100)
    assert store.toggle_device(100, 200, "laptop-a", 100, expires_at=200)
    assert store.toggle_callback(100, 200, "cmd:status", 100, expires_at=200)

    monkeypatch.setattr(store.time, "time", lambda: 201)
    assert store.toggle_device(100, 200, "laptop-a", 100) is True
    assert store.toggle_callback(100, 200, "cmd:status", 100) is True
    assert store.get_effective_grants(200) == {
        "callbacks": ["cmd:status"], "devices": ["laptop-a"]
    }


def test_temporary_grant_explicitly_sets_expiry_without_toggling_existing_access(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    store.register_start(_user(200))
    assert store.set_role(100, 200, store.Role.USER, 100)
    monkeypatch.setattr(store.time, "time", lambda: 100)
    assert store.toggle_callback(100, 200, "cmd:status", 100)
    assert store.toggle_device(100, 200, "laptop-a", 100)

    assert store.set_temporary_callback_grant(100, 200, "cmd:status", 100, expires_at=200)
    assert store.set_temporary_device_grant(100, 200, "laptop-a", 100, expires_at=200)
    assert store.get_effective_grants(200) == {
        "callbacks": ["cmd:status"], "devices": ["laptop-a"]
    }
    assert store.get_user(200)["permission_expirations"] == {
        "callback:cmd:status": 200, "device:laptop-a": 200
    }

    monkeypatch.setattr(store.time, "time", lambda: 200)
    assert store.get_effective_grants(200) == {"callbacks": [], "devices": []}
    assert store.set_temporary_callback_grant(200, 200, "cmd:status", 100, expires_at=300) is None


def test_expiring_grants_are_listed_once_claimed_and_reissued_after_extension(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    store.register_start(_user(200))
    assert store.set_role(100, 200, store.Role.USER, 100)
    monkeypatch.setattr(store.time, "time", lambda: 100)
    assert store.set_temporary_callback_grant(100, 200, "cmd:status", 100, expires_at=700)
    assert store.set_temporary_device_grant(100, 200, "laptop-a", 100, expires_at=2_000)
    assert store.toggle_callback(100, 200, "cmd:screenshot", 100)  # permanent

    rows = store.list_expiring_grants(now=200, within_s=900)
    assert rows == [{"user_id": 200, "kind": "callback", "item": "cmd:status", "expires_at": 700}]
    row = rows[0]
    assert store.claim_expiry_reminder(**row, now=200) is True
    assert store.claim_expiry_reminder(**row, now=201) is False
    assert store.finish_expiry_reminder(**row, sent=True, now=202) is True
    assert store.claim_expiry_reminder(**row, now=203) is False

    assert store.set_temporary_callback_grant(100, 200, "cmd:status", 100, expires_at=700)
    assert store.claim_expiry_reminder(**row, now=204) is True

    monkeypatch.setattr(store.time, "time", lambda: 300)
    assert store.set_temporary_callback_grant(100, 200, "cmd:status", 100, expires_at=1_000)
    extended = {**row, "expires_at": 1_000}
    assert store.claim_expiry_reminder(**extended, now=400) is True
    assert store.finish_expiry_reminder(**extended, sent=False, now=401) is True
    assert store.claim_expiry_reminder(**extended, now=402) is True


def test_expiry_reminder_claim_skips_revoked_blocked_or_expired_permission(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    store.register_start(_user(200))
    assert store.set_role(100, 200, store.Role.USER, 100)
    monkeypatch.setattr(store.time, "time", lambda: 100)
    assert store.set_temporary_device_grant(100, 200, "laptop-a", 100, expires_at=700)
    row = {"user_id": 200, "kind": "device", "item": "laptop-a", "expires_at": 700}
    assert store.claim_expiry_reminder(**row, now=200) is True
    assert store.finish_expiry_reminder(**row, sent=False, now=201) is True

    assert store.toggle_device(100, 200, "laptop-a", 100) is False
    assert store.claim_expiry_reminder(**row, now=202) is False
    assert store.list_expiring_grants(now=701) == []

    assert store.set_temporary_device_grant(100, 200, "laptop-a", 100, expires_at=900)
    refreshed = {**row, "expires_at": 900}
    assert store.set_blocked(100, 200, True, 100)
    assert store.claim_expiry_reminder(**refreshed, now=800) is False


def test_owner_can_review_exact_role_history_for_one_user(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    now = [100]
    monkeypatch.setattr(store.time, "time", lambda: now[0])
    store.register_start(_user(200))
    assert store.set_role(100, 200, store.Role.USER, 100)
    now[0] = 110
    assert store.set_blocked(100, 200, True, 100, expires_at=200, reason="review")
    now[0] = 200
    assert store.get_role(200, 100) == store.Role.USER
    now[0] = 210
    assert store.set_role(100, 200, store.Role.GUEST, 100)
    store.append_audit("callback_permission", actor_id=100, target_id=200, detail="unrelated")

    history = store.get_role_history(100, 200, 100)

    assert [(row["event"], row["role"], row["actor_id"]) for row in history] == [
        ("role_set", store.Role.GUEST, 100),
        ("user_block_expired", store.Role.USER, None),
        ("user_temporary_blocked", store.Role.BLOCKED, 100),
        ("role_set", store.Role.USER, 100),
    ]
    assert [row["at"] for row in history] == [210, 200, 110, 100]
    assert store.get_role_history(200, 200, 100) is None
    assert store.get_role_history(100, 999, 100) is None


def test_owner_grant_history_is_typed_target_scoped_and_includes_expiry(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    now = [1_800_000_000]
    monkeypatch.setattr(store.time, "time", lambda: now[0])
    store.register_start(_user(200))
    store.register_start(_user(201))
    assert store.toggle_callback(100, 200, "cmd:status", 100) is True
    now[0] += 1
    assert store.set_temporary_device_grant(100, 200, "laptop-a", 100, expires_at=now[0] + 3600)
    now[0] += 1
    assert store.toggle_callback(100, 200, "cmd:status", 100) is False
    assert store.toggle_callback(100, 201, "cmd:screenshot", 100) is True

    history = store.get_grant_history(100, 200, 100)

    assert [(row["kind"], row["item"], row["action"]) for row in history] == [
        ("callback", "cmd:status", "revoked"),
        ("device", "laptop-a", "granted"),
        ("callback", "cmd:status", "granted"),
    ]
    assert history[1]["expires_at"] == now[0] - 1 + 3600
    assert all("201" not in str(row) for row in history)
    assert store.get_grant_history(201, 200, 100) is None


def test_bulk_device_revocation_is_attributed_to_each_affected_users_grant_history(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    store.register_start(_user(200))
    store.register_start(_user(201))
    assert store.toggle_device(100, 200, "laptop-a", 100) is True
    assert store.toggle_device(100, 201, "laptop-a", 100) is True

    assert store.revoke_devices(["laptop-a"], actor_id=None) == 2

    for user_id in (200, 201):
        latest = store.get_grant_history(100, user_id, 100)[0]
        assert (latest["kind"], latest["item"], latest["action"], latest["actor_id"]) == (
            "device", "laptop-a", "revoked", None
        )


def test_role_and_block_previews_are_owner_only_and_do_not_mutate(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    store.register_start(_user(200))
    assert store.set_role(100, 200, store.Role.USER, 100)
    assert store.toggle_device(100, 200, "laptop-a", 100)
    assert store.toggle_callback(100, 200, "cmd:status", 100)
    audit_before = store.recent_audit()

    block_preview = store.preview_block_impact(100, 200, 100)
    role_preview = store.preview_role_grant_diff(
        100, 200, store.Role.GUEST, 100,
        callbacks=["cmd:status", "cmd:screenshot"], devices=["laptop-a", "mac-b"],
    )

    assert block_preview == {
        "target_id": 200, "role": store.Role.USER, "blocked": False,
        "callbacks": ["cmd:status"], "devices": ["laptop-a"],
        "will_revoke_all_access": True,
    }
    assert role_preview["role_before"] == store.Role.USER
    assert role_preview["role_after"] == store.Role.GUEST
    assert role_preview["callbacks_added"] == ["cmd:screenshot"]
    assert role_preview["devices_added"] == ["mac-b"]
    assert role_preview["callbacks_removed"] == []
    assert store.preview_block_impact(200, 200, 100) is None
    assert store.recent_audit() == audit_before
    assert store.get_role(200, 100) == store.Role.USER


def test_grant_conflict_preview_detects_duplicate_coverage_and_broader_overlap_without_mutation(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    store.register_start(_user(200))
    assert store.set_role(100, 200, store.Role.USER, 100)
    assert store.toggle_callback(100, 200, "shell_request", 100)
    assert store.toggle_callback(100, 200, "cmd:status", 100)
    audit_before = store.recent_audit()

    shell = store.preview_grant_conflicts(100, 200, "callback", "cmd:shell", 100)
    broad = store.preview_grant_conflicts(100, 200, "callback", "full_device", 100)
    duplicate = store.preview_grant_conflicts(100, 200, "callback", "cmd:status", 100)

    assert shell["reason"] == "covered_by_existing_grant"
    assert shell["overlapping_grants"] == ["shell_request"]
    assert broad["reason"] == "broader_grant_overlaps_existing"
    assert broad["overlapping_grants"] == ["cmd:status"]
    assert duplicate["reason"] == "already_granted"
    assert store.preview_grant_conflicts(200, 200, "callback", "cmd:screenshot", 100) is None
    assert store.recent_audit() == audit_before
    assert store.get_effective_grants(200)["callbacks"] == ["cmd:status", "shell_request"]


def test_owner_stale_device_grant_scan_uses_exact_inventory_and_skips_expired_rights(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    store.register_start(_user(200))
    store.register_start(_user(201))
    assert store.set_role(100, 200, store.Role.USER, 100)
    assert store.toggle_device(100, 200, "mac-gone", 100)
    assert store.toggle_device(100, 200, "mac-live", 100)
    assert store.set_blocked(100, 201, True, 100)
    assert store.toggle_device(100, 201, "pc-gone", 100)
    def add_expired(record):
        record["permissions"]["devices"].append("mac-expired")
        record.setdefault("permission_expirations", {})["device:mac-expired"] = 999
    store._update(200, add_expired)
    before = store.recent_audit()

    result = store.find_stale_device_grants(100, 100, {"mac-live", "pc-live"}, now=1_000)

    assert result == {
        "rows": [
            {"target_id": 200, "device_id": "mac-gone", "blocked": False, "expires_at": None},
            {"target_id": 201, "device_id": "pc-gone", "blocked": True, "expires_at": None},
        ],
        "truncated": False,
    }
    assert store.find_stale_device_grants(200, 100, {"mac-live"}, now=1_000) is None
    assert store.recent_audit() == before


def test_monthly_owner_access_review_is_read_only_exact_and_paginated(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    store.register_start(_user(200))
    store.register_start(_user(201))
    store.register_start(_user(100))
    assert store.set_role(100, 200, store.Role.USER, 100)
    assert store.toggle_device(100, 200, "mac-one", 100)
    assert store.toggle_callback(100, 200, "cmd:status", 100)
    store._update(200, lambda row: row.setdefault("permission_expirations", {}).__setitem__("callback:cmd:old", 999))
    store._update(200, lambda row: row["permissions"]["callbacks"].append("cmd:old"))
    assert store.set_blocked(100, 201, True, 100)
    before_users = store.list_users()
    before_audit = store.recent_audit()

    report = store.monthly_access_review(100, 100, page=0, page_size=1, now=1_000)
    second = store.monthly_access_review(100, 100, page=1, page_size=1, now=1_000)

    assert report == {
        "generated_at": 1_000, "page": 0, "page_size": 1, "total": 2, "pages": 2,
        "rows": [{"target_id": 200, "role": "user", "blocked": False,
                  "devices": ["mac-one"], "callbacks": ["cmd:status"]}],
    }
    assert second["rows"] == [{"target_id": 201, "role": "blocked", "blocked": True, "devices": [], "callbacks": []}]
    assert store.monthly_access_review(200, 100) is None
    assert store.list_users() == before_users
    assert store.recent_audit() == before_audit


def test_bulk_grant_preview_is_exact_read_only_and_confirmation_rejects_stale_targets(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    store.register_start(_user(200))
    store.register_start(_user(201))
    store.register_start(_user(202))
    assert store.set_role(100, 200, store.Role.USER, 100)
    assert store.toggle_callback(100, 200, "cmd:status", 100)
    assert store.set_blocked(100, 202, True, 100)
    before_audit = store.recent_audit()
    before = {uid: store.get_user(uid) for uid in (200, 201, 202)}

    preview = store.preview_bulk_grants(
        100, 100, [200, 201, 202], callbacks=["cmd:status", "cmd:screenshot"],
        device_ids=["mac-a"], available_device_ids={"mac-a"}, now=10_000,
    )

    assert preview["targets"] == [200, 201, 202]
    assert preview["callbacks"] == ["cmd:screenshot", "cmd:status"]
    assert preview["devices"] == ["mac-a"]
    assert preview["rows"][0]["already_had_callbacks"] == ["cmd:status"]
    assert preview["rows"][2]["blocked"] is True
    assert {uid: store.get_user(uid) for uid in before} == before
    assert store.recent_audit() == before_audit

    store.toggle_callback(100, 201, "cmd:battery", 100)
    assert store.apply_bulk_grants(
        100, 100, preview["targets"], callbacks=preview["callbacks"],
        device_ids=preview["devices"], available_device_ids={"mac-a"},
        expected_snapshot=preview["snapshot"], now=10_001,
    ) is None
    assert store.get_effective_grants(200)["callbacks"] == ["cmd:status"]
    assert store.get_effective_grants(201)["callbacks"] == ["cmd:battery"]


def test_bulk_grant_expiry_applies_to_new_group_rights_without_shortening_existing_access(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    current = 2_000_000_000
    monkeypatch.setattr(store.time, "time", lambda: current)
    store.register_start(_user(200))
    assert store.set_role(100, 200, store.Role.USER, 100)
    assert store.toggle_callback(100, 200, "cmd:status", 100)
    expiry = current + 3_600

    preview = store.preview_bulk_grants(
        100, 100, [200], callbacks=["cmd:status", "cmd:screenshot"],
        available_device_ids=set(), expires_at=expiry, now=current,
    )
    assert preview["expires_at"] == expiry
    assert preview["rows"][0]["already_had_callbacks"] == ["cmd:status"]
    assert preview["rows"][0]["add_callbacks"] == ["cmd:screenshot"]

    result = store.apply_bulk_grants(
        100, 100, [200], callbacks=preview["callbacks"], available_device_ids=set(),
        expected_snapshot=preview["snapshot"], expires_at=preview["expires_at"], now=current,
    )

    assert result["grants_added"] == 1
    assert store.get_effective_grants(200, now=expiry - 1)["callbacks"] == ["cmd:screenshot", "cmd:status"]
    assert store.get_effective_grants(200, now=expiry)["callbacks"] == ["cmd:status"]
    record = store.get_user(200)
    assert record["permission_expirations"]["callback:cmd:screenshot"] == expiry
    assert "callback:cmd:status" not in record["permission_expirations"]
    grant_audit = [row for row in store.recent_audit(100) if row.get("kind") == "grant_change"]
    assert any(row.get("item") == "cmd:screenshot" and row.get("expires_at") == expiry for row in grant_audit)


def test_user_permission_request_is_owner_bound_deduplicated_and_never_grants(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    store.register_start(_user(200))
    assert store.set_role(100, 200, store.Role.USER, 100)
    before = store.get_effective_grants(200)

    first = store.submit_permission_request(
        200, 100, "callback", "cmd:screenshot", available_device_ids={"mac-a"}, now=20_000,
    )
    repeated = store.submit_permission_request(
        200, 100, "callback", "cmd:screenshot", available_device_ids={"mac-a"}, now=20_001,
    )

    assert first["status"] == "pending"
    assert first["request_id"] == repeated["request_id"]
    assert repeated["duplicate"] is True
    assert store.get_effective_grants(200) == before
    assert [row["grant_kind"] for row in store.list_permission_requests(100, 100, now=20_002)] == ["callback"]
    assert sum(row.get("kind") == "permission_request_submitted" for row in store.recent_audit(100)) == 1


def test_permission_request_rejects_guest_blocked_owner_and_unregistered_targets(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    for user_id in (200, 201, 202):
        store.register_start(_user(user_id))
    assert store.set_role(100, 200, store.Role.USER, 100)
    assert store.set_blocked(100, 201, True, 100)

    assert store.submit_permission_request(
        200, 100, "callback", "cmd:execute", available_device_ids={"mac-a"},
    ) is None
    assert store.submit_permission_request(
        200, 100, "device", "missing-device", available_device_ids={"mac-a"},
    ) is None
    assert store.submit_permission_request(
        201, 100, "callback", "cmd:status", available_device_ids={"mac-a"},
    ) is None
    assert store.submit_permission_request(
        202, 100, "callback", "cmd:status", available_device_ids={"mac-a"},
    ) is None
    assert store.submit_permission_request(
        100, 100, "callback", "cmd:status", available_device_ids={"mac-a"},
    ) is None


def test_owner_decides_exact_permission_request_once_and_approval_expires(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    store.register_start(_user(200))
    assert store.set_role(100, 200, store.Role.USER, 100)
    request = store.submit_permission_request(
        200, 100, "callback", "cmd:screenshot", available_device_ids={"mac-a"}, now=20_000,
    )

    approved = store.decide_permission_request(
        100, 100, request["request_id"], "approve",
        available_device_ids={"mac-a"}, now=20_001,
    )

    assert approved["status"] == "approved"
    assert approved["grant_expires_at"] == 20_001 + 86_400
    assert "cmd:screenshot" in store.get_effective_grants(200, now=20_002)["callbacks"]
    assert store.get_effective_grants(200, now=approved["grant_expires_at"])["callbacks"] == []
    assert store.decide_permission_request(
        100, 100, request["request_id"], "approve",
        available_device_ids={"mac-a"}, now=20_003,
    ) is None
    assert store.list_permission_requests(100, 100, now=20_003) == []


def test_owner_can_reject_request_without_grant_and_cannot_approve_stale_device(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    store.register_start(_user(200))
    assert store.set_role(100, 200, store.Role.USER, 100)
    rejected_request = store.submit_permission_request(
        200, 100, "callback", "cmd:status", available_device_ids={"mac-a"}, now=30_000,
    )
    rejected = store.decide_permission_request(
        100, 100, rejected_request["request_id"], "reject",
        available_device_ids={"mac-a"}, now=30_001,
    )
    assert rejected["status"] == "rejected"
    assert store.get_effective_grants(200)["callbacks"] == []

    stale_request = store.submit_permission_request(
        200, 100, "device", "mac-a", available_device_ids={"mac-a"}, now=30_002,
    )
    stale = store.decide_permission_request(
        100, 100, stale_request["request_id"], "approve",
        available_device_ids=set(), now=30_003,
    )
    assert stale["status"] == "stale"
    assert store.get_effective_grants(200)["devices"] == []
    assert store.list_permission_requests(100, 100, now=30_004) == []
    assert store.list_permission_requests(100, 100) == []


def test_bulk_revoke_dry_run_lists_exact_users_and_callbacks_and_rechecks_before_apply(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    for user_id in (200, 201, 202):
        store.register_start(_user(user_id))
        assert store.set_role(100, user_id, store.Role.USER, 100)
    assert store.toggle_callback(100, 200, "cmd:status", 100)
    assert store.toggle_callback(100, 200, "cmd:screenshot", 100)
    assert store.toggle_callback(100, 201, "cmd:status", 100)
    assert store.set_blocked(100, 202, True, 100)
    before = {user_id: store.get_user(user_id) for user_id in (200, 201, 202)}
    before_audit = store.recent_audit()

    preview = store.preview_bulk_revocations(
        100, 100, [200, 201, 202], callbacks=["cmd:status", "cmd:screenshot"], now=10_000,
    )

    assert preview["targets"] == [200, 201, 202]
    assert preview["callbacks"] == ["cmd:screenshot", "cmd:status"]
    assert preview["rows"] == [
        {"target_id": 200, "role": "user", "blocked": False,
         "remove_callbacks": ["cmd:screenshot", "cmd:status"], "missing_callbacks": []},
        {"target_id": 201, "role": "user", "blocked": False,
         "remove_callbacks": ["cmd:status"], "missing_callbacks": ["cmd:screenshot"]},
        {"target_id": 202, "role": "blocked", "blocked": True,
         "remove_callbacks": [], "missing_callbacks": ["cmd:screenshot", "cmd:status"]},
    ]
    assert {user_id: store.get_user(user_id) for user_id in before} == before
    assert store.recent_audit() == before_audit

    assert store.toggle_callback(100, 201, "cmd:battery", 100)
    assert store.apply_bulk_revocations(
        100, 100, preview["targets"], callbacks=preview["callbacks"],
        expected_snapshot=preview["snapshot"], now=10_001,
    ) is None
    assert store.get_effective_grants(200)["callbacks"] == ["cmd:screenshot", "cmd:status"]
    assert store.get_effective_grants(201)["callbacks"] == ["cmd:battery", "cmd:status"]


def test_grant_revocation_preview_lists_exact_affected_scope_without_mutation(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    store.register_start(_user(200))
    assert store.set_role(100, 200, store.Role.USER, 100)
    assert store.toggle_device(100, 200, "laptop-a", 100)
    assert store.toggle_device(100, 200, "mac-b", 100)
    assert store.toggle_callback(100, 200, "cmd:status", 100)
    assert store.toggle_callback(100, 200, "cmd:screenshot", 100)
    before = store.get_user(200)
    audit_before = store.recent_audit()

    device_preview = store.preview_grant_revocation(100, 200, "device", "laptop-a", 100)
    action_preview = store.preview_grant_revocation(100, 200, "permission", "cmd:status", 100)

    assert device_preview == {
        "target_id": 200,
        "kind": "device",
        "item": "laptop-a",
        "affected_devices": ["laptop-a"],
        "affected_callbacks": ["cmd:screenshot", "cmd:status"],
        "blocked": False,
    }
    assert action_preview == {
        "target_id": 200,
        "kind": "permission",
        "item": "cmd:status",
        "affected_devices": ["laptop-a", "mac-b"],
        "affected_callbacks": ["cmd:status"],
        "blocked": False,
    }
    assert store.preview_grant_revocation(200, 200, "permission", "cmd:status", 100) is None
    assert store.preview_grant_revocation(100, 999, "permission", "cmd:status", 100) is None
    assert store.preview_grant_revocation(100, 200, "device", "unknown", 100) is None
    assert store.get_user(200) == before
    assert store.recent_audit() == audit_before


def test_grant_revocation_preview_ignores_expired_grants(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    store.register_start(_user(200))
    assert store.set_role(100, 200, store.Role.USER, 100)
    monkeypatch.setattr(store.time, "time", lambda: 100)
    assert store.toggle_device(100, 200, "laptop-a", 100, expires_at=200)
    assert store.toggle_callback(100, 200, "cmd:status", 100, expires_at=200)
    monkeypatch.setattr(store.time, "time", lambda: 200)

    assert store.preview_grant_revocation(100, 200, "device", "laptop-a", 100) is None
    assert store.preview_grant_revocation(100, 200, "permission", "cmd:status", 100) is None


def test_owner_revokes_all_target_grants_only_after_exact_snapshot_confirmation(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    store.register_start(_user(200))
    assert store.set_role(100, 200, store.Role.USER, 100)
    assert store.toggle_device(100, 200, "mac-a", 100)
    assert store.toggle_device(100, 200, "win-b", 100)
    assert store.toggle_callback(100, 200, "cmd:status", 100)
    assert store.toggle_callback(100, 200, "cmd:screenshot", 100)
    before = store.get_user(200)

    preview = store.preview_user_grant_revocation(100, 200, 100)

    assert preview["target_id"] == 200
    assert preview["devices"] == ["mac-a", "win-b"]
    assert preview["callbacks"] == ["cmd:screenshot", "cmd:status"]
    assert store.get_user(200) == before
    result = store.apply_user_grant_revocation(
        100, 200, 100, expected_snapshot=preview["snapshot"],
    )

    assert result == {"revoked_devices": 2, "revoked_callbacks": 2}
    after = store.get_user(200)
    assert after["permissions"] == {"callbacks": [], "devices": []}
    assert after["role"] == "user"
    assert after["blocked"] is False
    assert store.get_effective_grants(200) == {"callbacks": [], "devices": []}
    history = store.get_grant_history(100, 200, 100)
    assert {(row["kind"], row["item"], row["action"]) for row in history if row["action"] == "revoked"} == {
        ("device", "mac-a", "revoked"), ("device", "win-b", "revoked"),
        ("callback", "cmd:status", "revoked"), ("callback", "cmd:screenshot", "revoked"),
    }
    assert store.apply_user_grant_revocation(
        100, 200, 100, expected_snapshot=preview["snapshot"],
    ) is None


def test_owner_bulk_user_grant_revocation_rejects_stale_and_owner_targets(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    store.register_start(_user(200))
    assert store.set_role(100, 200, store.Role.USER, 100)
    assert store.toggle_callback(100, 200, "cmd:status", 100)
    preview = store.preview_user_grant_revocation(100, 200, 100)
    assert preview
    assert store.toggle_callback(100, 200, "cmd:battery", 100)

    assert store.apply_user_grant_revocation(
        100, 200, 100, expected_snapshot=preview["snapshot"],
    ) is None
    assert store.get_effective_grants(200)["callbacks"] == ["cmd:battery", "cmd:status"]
    assert store.preview_user_grant_revocation(200, 200, 100) is None
    assert store.preview_user_grant_revocation(100, 100, 100) is None
    assert store.apply_user_grant_revocation(100, 100, 100, expected_snapshot=[]) is None


def test_grant_pause_preserves_saved_rights_and_owner_confirmation_resumes_current_rights(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    store.register_start(_user(200))
    assert store.set_role(100, 200, store.Role.USER, 100)
    monkeypatch.setattr(store.time, "time", lambda: 1_000)
    assert store.toggle_device(100, 200, "mac-a", 100)
    assert store.toggle_callback(100, 200, "cmd:status", 100)
    assert store.toggle_callback(100, 200, "cmd:screenshot", 100, expires_at=1_100)
    before = store.get_user(200)

    preview = store.preview_user_grant_pause(100, 200, 100, available_device_ids={"mac-a"}, now=1_000)

    assert preview["paused"] is False
    assert preview["resume_callbacks"] == ["cmd:screenshot", "cmd:status"]
    assert preview["resume_devices"] == ["mac-a"]
    paused = store.apply_user_grant_pause(
        100, 200, 100, paused=True, available_device_ids={"mac-a"},
        expected_snapshot=preview["snapshot"], now=1_000,
    )

    assert paused == {"paused": True, "callbacks_preserved": 2, "devices_preserved": 1}
    held = store.get_user(200)
    assert held["permissions"] == before["permissions"]
    assert held["permission_expirations"] == before["permission_expirations"]
    assert held["role"] == "user" and held["blocked"] is False
    assert store.get_effective_grants(200, now=1_001) == {"callbacks": [], "devices": []}

    monkeypatch.setattr(store.time, "time", lambda: 1_101)
    resume_preview = store.preview_user_grant_pause(
        100, 200, 100, available_device_ids={"mac-a"}, now=1_101,
    )
    assert resume_preview["paused"] is True
    assert resume_preview["resume_callbacks"] == ["cmd:status"]
    assert resume_preview["resume_devices"] == ["mac-a"]
    resumed = store.apply_user_grant_pause(
        100, 200, 100, paused=False, available_device_ids={"mac-a"},
        expected_snapshot=resume_preview["snapshot"], now=1_101,
    )

    assert resumed == {"paused": False, "callbacks_preserved": 2, "devices_preserved": 1}
    assert store.get_effective_grants(200, now=1_102) == {
        "callbacks": ["cmd:status"], "devices": ["mac-a"],
    }


def test_grant_pause_resume_fails_when_preview_or_live_device_inventory_changes(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    store.register_start(_user(200))
    assert store.set_role(100, 200, store.Role.USER, 100)
    assert store.toggle_device(100, 200, "mac-a", 100)
    first = store.preview_user_grant_pause(100, 200, 100, available_device_ids={"mac-a"}, now=2_000)
    assert first
    assert store.apply_user_grant_pause(
        100, 200, 100, paused=True, available_device_ids={"mac-a"},
        expected_snapshot=first["snapshot"], now=2_000,
    )
    second = store.preview_user_grant_pause(100, 200, 100, available_device_ids={"mac-a"}, now=2_001)

    assert store.apply_user_grant_pause(
        100, 200, 100, paused=False, available_device_ids=set(),
        expected_snapshot=second["snapshot"], now=2_001,
    ) is None
    assert store.apply_user_grant_pause(
        200, 200, 100, paused=False, available_device_ids={"mac-a"},
        expected_snapshot=second["snapshot"], now=2_001,
    ) is None
    assert store.get_effective_grants(200, now=2_002) == {"callbacks": [], "devices": []}


def test_owner_can_remove_a_saved_grant_while_user_grants_are_paused(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    store.register_start(_user(200))
    assert store.set_role(100, 200, store.Role.USER, 100)
    assert store.toggle_callback(100, 200, "cmd:status", 100)
    preview = store.preview_user_grant_pause(100, 200, 100, available_device_ids=set(), now=2_000)
    assert store.apply_user_grant_pause(
        100, 200, 100, paused=True, available_device_ids=set(),
        expected_snapshot=preview["snapshot"], now=2_000,
    )

    assert store.toggle_callback(100, 200, "cmd:status", 100) is False
    current = store.get_user(200)
    assert current["permissions"]["callbacks"] == []
    assert current["permissions_paused"] is True
    assert store.get_effective_grants(200, now=2_001) == {"callbacks": [], "devices": []}


def test_resuming_grants_drops_device_permissions_missing_from_confirmed_inventory(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    store.register_start(_user(200))
    assert store.set_role(100, 200, store.Role.USER, 100)
    assert store.toggle_device(100, 200, "mac-a", 100)
    assert store.toggle_device(100, 200, "old-device", 100)
    preview = store.preview_user_grant_pause(100, 200, 100, available_device_ids={"mac-a"}, now=2_000)
    assert preview["stale_devices"] == ["old-device"]
    assert store.apply_user_grant_pause(
        100, 200, 100, paused=True, available_device_ids={"mac-a"},
        expected_snapshot=preview["snapshot"], now=2_000,
    )
    resume = store.preview_user_grant_pause(100, 200, 100, available_device_ids={"mac-a"}, now=2_001)
    assert resume["resume_devices"] == ["mac-a"]
    assert resume["stale_devices"] == ["old-device"]

    result = store.apply_user_grant_pause(
        100, 200, 100, paused=False, available_device_ids={"mac-a"},
        expected_snapshot=resume["snapshot"], now=2_001,
    )

    assert result is not None
    assert store.get_user(200)["permissions"]["devices"] == ["mac-a"]
    assert store.get_effective_grants(200, now=2_002)["devices"] == ["mac-a"]


def test_grant_review_distinguishes_expired_unused_and_unknown_history(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    store.register_start(_user(200))
    assert store.set_role(100, 200, store.Role.USER, 100)
    monkeypatch.setattr(store.time, "time", lambda: 1_000)
    assert store.toggle_device(100, 200, "laptop-a", 100)
    assert store.toggle_callback(100, 200, "cmd:status", 100)
    assert store.toggle_callback(100, 200, "cmd:screenshot", 100, expires_at=1_050)
    store._update(200, lambda record: record["permissions"]["callbacks"].append("cmd:legacy"))

    rows = store.review_grants(100, 100, unused_after_s=100, now=1_200)

    assert [(row["kind"], row["item"], row["state"]) for row in rows] == [
        ("device", "laptop-a", "unused"),
        ("permission", "cmd:legacy", "history_unknown"),
        ("permission", "cmd:screenshot", "expired"),
        ("permission", "cmd:status", "unused"),
    ]
    assert store.review_grants(200, 100, unused_after_s=100, now=1_200) is None


def test_grant_use_records_only_the_authorized_device_and_action(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    store.register_start(_user(200))
    assert store.set_role(100, 200, store.Role.USER, 100)
    monkeypatch.setattr(store.time, "time", lambda: 1_000)
    assert store.toggle_device(100, 200, "mac-b", 100)
    monkeypatch.setattr(store.time, "time", lambda: 2_000)
    assert store.toggle_device(100, 200, "laptop-a", 100)
    assert store.toggle_callback(100, 200, "cmd:status", 100)

    assert store.record_grant_use(200, "cmd:status", 100, "laptop-a") is True
    rows = store.review_grants(100, 100, unused_after_s=100, now=2_050)
    states = {(row["kind"], row["item"]): row["state"] for row in rows}
    assert states[("device", "laptop-a")] == "active"
    assert states[("device", "mac-b")] == "unused"
    assert states[("permission", "cmd:status")] == "active"
    assert store.record_grant_use(200, "cmd:screenshot", 100, "laptop-a") is False


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


def test_process_pagination_uses_the_process_list_grant_without_granting_kill(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    store.register_start(_user(200))
    store.set_role(100, 200, store.Role.USER, 100)
    store.toggle_device(100, 200, "laptop-a", 100)
    store.toggle_callback(100, 200, "proc:0", 100)

    assert store.can_use_callback(200, "proc:1", 100, "laptop-a") is True
    assert store.can_use_callback(200, "proc:refresh", 100, "laptop-a") is True
    assert store.can_use_callback(200, "proc:invalid", 100, "laptop-a") is False
    assert store.can_use_callback(200, "killq:123", 100, "laptop-a") is False
    assert store.can_use_callback(200, "proc:1", 100, "laptop-b") is False

    store.set_blocked(100, 200, True, 100)
    assert store.can_use_callback(200, "proc:1", 100, "laptop-a") is False


def test_specific_kill_grant_allows_only_its_matching_process_confirmation(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    store.register_start(_user(200))
    store.set_role(100, 200, store.Role.USER, 100)
    store.toggle_device(100, 200, "laptop-a", 100)
    store.toggle_callback(100, 200, "proc:0", 100)
    store.toggle_callback(100, 200, "killq:42", 100)

    assert store.can_use_callback(200, "killq:42", 100, "laptop-a") is True
    assert store.can_use_callback(200, "kill:42", 100, "laptop-a") is True
    assert store.can_use_callback(200, "killq:43", 100, "laptop-a") is False
    assert store.can_use_callback(200, "kill:43", 100, "laptop-a") is False


def test_screen_category_and_wallpaper_entry_follow_their_action_grants(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    store.register_start(_user(200))
    store.set_role(100, 200, store.Role.USER, 100)
    store.toggle_device(100, 200, "laptop-a", 100)

    store.toggle_callback(100, 200, "cmd:geo_location", 100)
    assert store.can_use_callback(200, "cat:screen", 100, "laptop-a") is True
    assert store.can_use_callback(200, "cmd:geo_location", 100, "laptop-a") is True
    store.toggle_callback(100, 200, "cmd:apps", 100)
    assert store.can_use_callback(200, "cat:network", 100, "laptop-a") is True

    store.toggle_callback(100, 200, "cmd:wallpaper_random_meme", 100)
    assert store.can_use_callback(200, "menu:wallpaper", 100, "laptop-a") is True


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


def test_shell_request_grant_cannot_approve_shell_execution(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    store.register_start(_user(200))
    store.set_role(100, 200, store.Role.USER, 100)
    store.toggle_device(100, 200, "laptop-a", 100)
    store.toggle_callback(100, 200, "cfm:shell", 100)
    assert store.can_use_callback(200, "cfm:shell", 100, "laptop-a") is True
    assert store.can_use_callback(200, "shell:ok", 100, "laptop-a") is False
    assert store.can_use_callback(200, "cmd:shell", 100, "laptop-a") is False
    store.toggle_callback(100, 200, "full_device", 100)
    assert store.can_use_callback(200, "shell:ok", 100, "laptop-a") is False


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

    store.append_audit("callback_permission", actor_id=200, detail="token=123456:abcdefghijklmnopqrstuvwxyz")
    rows = store.recent_audit()
    assert "[REDACTED]" in rows[0]["detail"] or "[TOKEN]" in rows[0]["detail"]


def test_access_explanation_reports_nonsecret_rbac_denial_without_changing_policy(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    store.register_start(_user(200))
    blocked = store.explain_callback_access(200, "cmd:status", 100, "laptop-a")
    assert blocked == {"allowed": False, "reason": "guest_role"}

    assert store.set_role(100, 200, store.Role.USER, 100)
    owner_only = store.explain_callback_access(200, "admin:users", 100, "laptop-a")
    assert owner_only == {"allowed": False, "reason": "owner_only"}
    missing_device = store.explain_callback_access(200, "cmd:status", 100, "laptop-a")
    assert missing_device == {"allowed": False, "reason": "device_grant_required"}

    assert store.toggle_device(100, 200, "laptop-a", 100)
    unknown = store.explain_callback_access(200, "global:unknown", 100, "laptop-a")
    assert unknown == {"allowed": False, "reason": "action_not_allowlisted"}
    missing_action = store.explain_callback_access(200, "cmd:status", 100, "laptop-a")
    assert missing_action == {"allowed": False, "reason": "action_grant_required"}
    assert store.toggle_callback(100, 200, "cmd:status", 100)
    assert store.explain_callback_access(200, "cmd:status", 100, "laptop-a") == {
        "allowed": True, "reason": "granted_action"
    }
    assert store.can_use_callback(200, "cmd:status", 100, "laptop-a") is True


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


def test_named_permission_profile_captures_only_effective_grants_and_is_owner_only(
    monkeypatch, tmp_path,
):
    _isolate(monkeypatch, tmp_path)
    store.register_start(_user(200))
    assert store.set_role(100, 200, store.Role.USER, 100)
    monkeypatch.setattr(store.time, "time", lambda: 100)
    assert store.toggle_device(100, 200, "mac-a", 100) is True
    assert store.toggle_callback(100, 200, "cmd:status", 100) is True
    assert store.toggle_callback(100, 200, "cmd:screenshot", 100, expires_at=200) is True
    monkeypatch.setattr(store.time, "time", lambda: 201)

    profile = store.create_permission_profile(100, 100, "Support Desk", 200)

    assert isinstance(profile, dict)
    assert profile["name"] == "Support Desk"
    assert profile["devices"] == ["mac-a"]
    assert profile["callbacks"] == ["cmd:status"]
    assert set(profile) == {"profile_id", "name", "devices", "callbacks", "created_at", "updated_at"}
    assert store.get_permission_profile(100, 100, profile["profile_id"]) == profile
    assert store.list_permission_profiles(100, 100) == [profile]
    assert store.create_permission_profile(100, 100, "support desk", 200) is None
    assert store.list_permission_profiles(200, 100) is None
    assert store.create_permission_profile(200, 100, "forbidden", 200) is None
    assert store.delete_permission_profile(200, 100, profile["profile_id"]) is False


def test_named_permission_profile_rejects_invalid_names_and_unknown_source_grants(
    monkeypatch, tmp_path,
):
    _isolate(monkeypatch, tmp_path)
    store.register_start(_user(200))
    assert store.set_role(100, 200, store.Role.USER, 100)
    assert store.toggle_callback(100, 200, "cmd:unclassified", 100) is True

    assert store.create_permission_profile(100, 100, "", 200) is None
    assert store.create_permission_profile(100, 100, "\nInjected", 200) is None
    assert store.create_permission_profile(100, 100, "Unknown grant", 200) is None
    assert store.create_permission_profile(100, 100, "owner", 100) is None
