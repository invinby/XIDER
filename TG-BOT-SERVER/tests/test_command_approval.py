import hashlib
import importlib
import json
import logging
import pickle
from concurrent.futures import ThreadPoolExecutor

import pytest

import command_approval as approval


REQUESTER_ID = 125
OWNER_ID = 9001
DEVICE_ID = "device-17"
COMMAND = "printf 'точный текст\n'  "


@pytest.fixture(autouse=True)
def fresh_pending_store():
    importlib.reload(approval)


def create_request(command=COMMAND, *, now=1_800_000_000):
    return approval.create_pending(
        requester_id=REQUESTER_ID,
        owner_id=OWNER_ID,
        device_id=DEVICE_ID,
        command=command,
        now=now,
    )


def test_create_and_consume_preserves_exact_utf8_command_and_hash():
    pending = create_request()

    assert pending.command_text == COMMAND
    assert pending.command_hash == hashlib.sha256(COMMAND.encode("utf-8")).hexdigest()
    assert pending.owner_id == OWNER_ID
    assert pending.expires_at - pending.created_at == 120

    consumed = approval.consume_pending(
        pending.request_id,
        approver_id=OWNER_ID,
        device_id=DEVICE_ID,
        command_hash=pending.command_hash,
        now=pending.created_at + 119,
    )

    assert consumed is pending
    assert consumed.command_text == COMMAND


def test_consume_rejects_altered_hash_without_consuming():
    pending = create_request()

    assert approval.consume_pending(
        pending.request_id,
        approver_id=OWNER_ID,
        device_id=DEVICE_ID,
        command_hash="0" * 64,
        now=pending.created_at,
    ) is None
    assert approval.consume_pending(
        pending.request_id,
        approver_id=OWNER_ID,
        device_id=DEVICE_ID,
        command_hash=pending.command_hash,
        now=pending.created_at,
    ) is pending


def test_consume_rejects_non_ascii_hash_without_raising():
    pending = create_request()

    assert approval.consume_pending(
        pending.request_id,
        approver_id=OWNER_ID,
        device_id=DEVICE_ID,
        command_hash="я" * 64,
        now=pending.created_at,
    ) is None
    assert approval.consume_pending(
        pending.request_id,
        approver_id=OWNER_ID,
        device_id=DEVICE_ID,
        command_hash=pending.command_hash,
        now=pending.created_at,
    ) is pending


def test_consume_rejects_altered_device_without_consuming():
    pending = create_request()

    assert approval.consume_pending(
        pending.request_id,
        approver_id=OWNER_ID,
        device_id="another-device",
        command_hash=pending.command_hash,
        now=pending.created_at,
    ) is None
    assert approval.consume_pending(
        pending.request_id,
        approver_id=OWNER_ID,
        device_id=DEVICE_ID,
        command_hash=pending.command_hash,
        now=pending.created_at,
    ) is pending


def test_only_owner_captured_at_creation_can_consume():
    pending = create_request()

    assert approval.consume_pending(
        pending.request_id,
        approver_id=OWNER_ID + 1,
        device_id=DEVICE_ID,
        command_hash=pending.command_hash,
        now=pending.created_at,
    ) is None
    assert approval.consume_pending(
        pending.request_id,
        approver_id=OWNER_ID,
        device_id=DEVICE_ID,
        command_hash=pending.command_hash,
        now=pending.created_at,
    ) is pending


def test_consume_expires_at_120_seconds_with_inclusive_boundary():
    before_boundary = create_request(now=1_800_000_000)
    assert approval.consume_pending(
        before_boundary.request_id,
        approver_id=OWNER_ID,
        device_id=DEVICE_ID,
        command_hash=before_boundary.command_hash,
        now=before_boundary.created_at + 119,
    ) is before_boundary

    at_boundary = create_request(now=1_800_001_000)
    assert approval.consume_pending(
        at_boundary.request_id,
        approver_id=OWNER_ID,
        device_id=DEVICE_ID,
        command_hash=at_boundary.command_hash,
        now=at_boundary.created_at + 120,
    ) is None


def test_consumed_request_cannot_be_replayed():
    pending = create_request()
    args = (pending.request_id,)
    kwargs = {
        "approver_id": OWNER_ID,
        "device_id": DEVICE_ID,
        "command_hash": pending.command_hash,
        "now": pending.created_at,
    }

    assert approval.consume_pending(*args, **kwargs) is pending
    assert approval.consume_pending(*args, **kwargs) is None


def test_cancel_allows_requester_or_captured_owner_only():
    requester_cancellable = create_request()
    assert approval.cancel_pending(requester_cancellable.request_id, OWNER_ID + 1) is False
    assert approval.cancel_pending(requester_cancellable.request_id, REQUESTER_ID) is True
    assert approval.cancel_pending(requester_cancellable.request_id, REQUESTER_ID) is False

    owner_cancellable = create_request(now=1_800_000_010)
    assert approval.cancel_pending(owner_cancellable.request_id, OWNER_ID) is True


def test_restart_invalidates_pending_request():
    pending = create_request()
    importlib.reload(approval)

    assert approval.consume_pending(
        pending.request_id,
        approver_id=OWNER_ID,
        device_id=DEVICE_ID,
        command_hash=pending.command_hash,
        now=pending.created_at,
    ) is None


def test_command_text_is_not_in_repr_public_serialization_or_logs(caplog):
    secret_command = "SECRET-COMMAND-MUST-NOT-LEAK-\u2620"
    with caplog.at_level(logging.DEBUG):
        pending = create_request(secret_command)
        public_json = json.dumps(pending.to_public_dict(), sort_keys=True)

    assert secret_command not in repr(pending)
    assert secret_command not in public_json
    assert "command_text" not in public_json
    assert secret_command not in caplog.text
    with pytest.raises((TypeError, pickle.PicklingError)):
        pickle.dumps(pending)


def test_concurrent_double_consume_succeeds_exactly_once():
    pending = create_request()

    def consume():
        return approval.consume_pending(
            pending.request_id,
            approver_id=OWNER_ID,
            device_id=DEVICE_ID,
            command_hash=pending.command_hash,
            now=pending.created_at,
        )

    with ThreadPoolExecutor(max_workers=16) as pool:
        results = list(pool.map(lambda _: consume(), range(32)))

    assert sum(result is not None for result in results) == 1


def test_create_rejects_commands_over_utf8_byte_limit():
    too_long = "я" * (approval.MAX_COMMAND_BYTES // 2 + 1)

    with pytest.raises(ValueError):
        create_request(too_long)


def test_store_rejects_requests_over_capacity(monkeypatch):
    monkeypatch.setattr(approval, "MAX_PENDING_COMMANDS", 2)
    create_request(now=1_800_000_000)
    create_request(now=1_800_000_001)

    with pytest.raises(approval.PendingStoreFull):
        create_request(now=1_800_000_002)


def test_store_discards_expired_requests_before_enforcing_capacity(monkeypatch):
    monkeypatch.setattr(approval, "MAX_PENDING_COMMANDS", 1)
    old_request = create_request(now=1_800_000_000)

    replacement = create_request(now=old_request.expires_at)

    assert replacement.request_id != old_request.request_id


def test_nonce_is_random_and_not_reused_for_pending_requests():
    requests = [create_request(now=1_800_000_000 + i) for i in range(4)]
    nonces = [pending.nonce for pending in requests]

    assert len(set(nonces)) == len(nonces)
    assert all(len(nonce) >= 32 for nonce in nonces)
