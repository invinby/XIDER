from __future__ import annotations

import re

import pytest

from file_inbox import FileInbox


def test_staged_file_is_owner_only_and_returns_metadata_not_storage_path(tmp_path):
    inbox = FileInbox(tmp_path / "inbox", max_item_bytes=64)
    record = inbox.stage(
        actor_id=100, owner_id=100, name="../../weekly.txt",
        data=b"owner supplied bytes", mime_type="text/plain", now=1_000,
    )

    assert re.fullmatch(r"[0-9a-f]{32}", record["item_id"])
    assert record["name"] == "weekly.txt"
    assert record["size"] == len(b"owner supplied bytes")
    assert record["sha256"] == "".join(__import__("hashlib").sha256(b"owner supplied bytes").hexdigest())
    assert not any(key in record for key in {"path", "storage_path", "data"})
    assert inbox.list_items(actor_id=200, owner_id=100, now=1_000) == []
    assert inbox.get(actor_id=200, owner_id=100, item_id=record["item_id"], now=1_000) is None


def test_staged_file_limits_expiry_and_owner_scoped_delete(tmp_path):
    inbox = FileInbox(tmp_path / "inbox", max_item_bytes=8, ttl_seconds=60)
    with pytest.raises(PermissionError):
        inbox.stage(actor_id=200, owner_id=100, name="x.txt", data=b"x", now=100)
    with pytest.raises(ValueError, match="size"):
        inbox.stage(actor_id=100, owner_id=100, name="large.txt", data=b"123456789", now=100)

    item = inbox.stage(actor_id=100, owner_id=100, name="x.txt", data=b"1234", now=100)
    assert inbox.get(actor_id=100, owner_id=100, item_id=item["item_id"], now=159)[1] == b"1234"
    assert inbox.list_items(actor_id=100, owner_id=100, now=160) == []
    assert inbox.get(actor_id=100, owner_id=100, item_id=item["item_id"], now=160) is None
    assert inbox.delete(actor_id=100, owner_id=100, item_id=item["item_id"]) is False


def test_staged_file_detects_corruption_and_rejects_path_like_ids(tmp_path):
    inbox = FileInbox(tmp_path / "inbox")
    item = inbox.stage(actor_id=100, owner_id=100, name="x.bin", data=b"safe", now=100)

    assert inbox.get(actor_id=100, owner_id=100, item_id="../x.bin", now=101) is None
    assert inbox.get(actor_id=200, owner_id=100, item_id=item["item_id"], now=101) is None
    blob = tmp_path / "inbox" / f"{item['item_id']}.bin"
    blob.write_bytes(b"tampered")
    with pytest.raises(ValueError, match="integrity"):
        inbox.get(actor_id=100, owner_id=100, item_id=item["item_id"], now=101)


def test_staged_file_uses_bounded_queue_and_expires_without_opening_content(tmp_path):
    inbox = FileInbox(tmp_path / "inbox", max_items=1, ttl_seconds=60)
    first = inbox.stage(actor_id=100, owner_id=100, name="a.txt", data=b"a", now=100)

    with pytest.raises(ValueError, match="full"):
        inbox.stage(actor_id=100, owner_id=100, name="b.txt", data=b"b", now=101)
    assert inbox.list_items(actor_id=100, owner_id=100, now=160) == []
    second = inbox.stage(actor_id=100, owner_id=100, name="b.txt", data=b"b", now=161)
    assert second["item_id"] != first["item_id"]
