"""Owner-provided file staging with bounded retention and integrity checks.

Objects are opaque bytes in this store. The module never extracts, opens, or
executes staged content; the bot must request an explicit placement action.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
import unicodedata
import uuid
from pathlib import Path
from typing import Any


_ITEM_ID = re.compile(r"[0-9a-f]{32}\Z")
_INVALID_NAME_CHARS = re.compile(r'[<>:"|?*]')


class FileInbox:
    def __init__(
        self, root: str | Path, *, max_item_bytes: int = 10 * 1024 * 1024,
        max_items: int = 100, ttl_seconds: int = 7 * 24 * 60 * 60,
    ) -> None:
        if type(max_item_bytes) is not int or not 1 <= max_item_bytes <= 100 * 1024 * 1024:
            raise ValueError("max_item_bytes must be between 1 byte and 100 MiB")
        if type(max_items) is not int or not 1 <= max_items <= 10_000:
            raise ValueError("max_items must be between 1 and 10000")
        if type(ttl_seconds) is not int or not 60 <= ttl_seconds <= 90 * 24 * 60 * 60:
            raise ValueError("ttl_seconds must be between one minute and 90 days")
        self.root = Path(root).expanduser().resolve()
        self.max_item_bytes = max_item_bytes
        self.max_items = max_items
        self.ttl_seconds = ttl_seconds
        self._lock = threading.RLock()

    @staticmethod
    def _safe_name(value: str) -> str:
        if not isinstance(value, str):
            value = "upload.bin"
        basename = value.replace("\\", "/").split("/")[-1]
        basename = "".join(ch for ch in basename if unicodedata.category(ch) not in {"Cc", "Cf"})
        basename = _INVALID_NAME_CHARS.sub("_", basename).strip(" .")[:120]
        return basename or "upload.bin"

    @staticmethod
    def _safe_mime(value: str | None) -> str:
        if not isinstance(value, str) or len(value) > 120 or any(ord(ch) < 32 for ch in value):
            return "application/octet-stream"
        return value or "application/octet-stream"

    def _metadata_path(self, item_id: str) -> Path:
        return self.root / f"{item_id}.json"

    def _blob_path(self, item_id: str) -> Path:
        return self.root / f"{item_id}.bin"

    def _read_metadata(self, item_id: str) -> dict[str, Any] | None:
        if not _ITEM_ID.fullmatch(item_id):
            return None
        path = self._metadata_path(item_id)
        if path.is_symlink() or not path.is_file():
            return None
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return None
        if (
            not isinstance(record, dict)
            or record.get("item_id") != item_id
            or type(record.get("owner_id")) is not int
            or type(record.get("size")) is not int
            or type(record.get("created_at")) is not int
            or type(record.get("expires_at")) is not int
            or not isinstance(record.get("sha256"), str)
            or not isinstance(record.get("name"), str)
        ):
            return None
        return record

    def _delete_id(self, item_id: str) -> bool:
        removed = False
        for path in (self._metadata_path(item_id), self._blob_path(item_id)):
            if path.is_symlink():
                continue
            try:
                path.unlink()
                removed = True
            except FileNotFoundError:
                pass
        return removed

    def _purge_expired(self, now: int) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        for path in self.root.glob("[0-9a-f]" * 32 + ".json"):
            item_id = path.stem
            record = self._read_metadata(item_id)
            if record is None or now >= record["expires_at"]:
                self._delete_id(item_id)

    def stage(
        self, *, actor_id: int, owner_id: int, name: str, data: bytes,
        mime_type: str | None = None, now: int | None = None,
    ) -> dict[str, Any]:
        if int(actor_id) != int(owner_id) or int(owner_id) <= 0:
            raise PermissionError("only the configured owner may stage files")
        if not isinstance(data, bytes) or not data:
            raise ValueError("file data must be non-empty bytes")
        if len(data) > self.max_item_bytes:
            raise ValueError("file size exceeds the inbox limit")
        current = int(now if now is not None else time.time())
        digest = hashlib.sha256(data).hexdigest()
        record = {
            "item_id": uuid.uuid4().hex,
            "owner_id": int(owner_id),
            "name": self._safe_name(name),
            "mime_type": self._safe_mime(mime_type),
            "size": len(data),
            "sha256": digest,
            "created_at": current,
            "expires_at": current + self.ttl_seconds,
        }
        with self._lock:
            self._purge_expired(current)
            if len(list(self.root.glob("[0-9a-f]" * 32 + ".json"))) >= self.max_items:
                raise ValueError("file inbox is full")
            blob = self._blob_path(record["item_id"])
            meta = self._metadata_path(record["item_id"])
            blob_tmp = blob.with_suffix(".bin.tmp")
            meta_tmp = meta.with_suffix(".json.tmp")
            try:
                with blob_tmp.open("xb") as stream:
                    stream.write(data)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(blob_tmp, blob)
                with meta_tmp.open("x", encoding="utf-8") as stream:
                    json.dump(record, stream, ensure_ascii=False, separators=(",", ":"))
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(meta_tmp, meta)
            except Exception:
                for path in (blob_tmp, meta_tmp, blob, meta):
                    try:
                        path.unlink()
                    except FileNotFoundError:
                        pass
                raise
        return dict(record)

    def list_items(
        self, *, actor_id: int, owner_id: int, now: int | None = None,
    ) -> list[dict[str, Any]]:
        if int(actor_id) != int(owner_id) or int(owner_id) <= 0:
            return []
        current = int(now if now is not None else time.time())
        with self._lock:
            self._purge_expired(current)
            rows = []
            for path in self.root.glob("[0-9a-f]" * 32 + ".json"):
                record = self._read_metadata(path.stem)
                if record is not None and record["owner_id"] == int(owner_id):
                    rows.append(record)
            rows.sort(key=lambda row: (row["created_at"], row["item_id"]), reverse=True)
            return [dict(row) for row in rows]

    def get(
        self, *, actor_id: int, owner_id: int, item_id: str, now: int | None = None,
    ) -> tuple[dict[str, Any], bytes] | None:
        if int(actor_id) != int(owner_id) or int(owner_id) <= 0 or not _ITEM_ID.fullmatch(str(item_id)):
            return None
        current = int(now if now is not None else time.time())
        with self._lock:
            self._purge_expired(current)
            record = self._read_metadata(str(item_id))
            if record is None or record["owner_id"] != int(owner_id):
                return None
            blob = self._blob_path(str(item_id))
            if blob.is_symlink() or not blob.is_file():
                raise ValueError("staged file integrity check failed")
            data = blob.read_bytes()
            if len(data) != record["size"] or hashlib.sha256(data).hexdigest() != record["sha256"]:
                raise ValueError("staged file integrity check failed")
            return dict(record), data

    def delete(self, *, actor_id: int, owner_id: int, item_id: str) -> bool:
        if int(actor_id) != int(owner_id) or int(owner_id) <= 0 or not _ITEM_ID.fullmatch(str(item_id)):
            return False
        with self._lock:
            record = self._read_metadata(str(item_id))
            if record is None or record["owner_id"] != int(owner_id):
                return False
            self._delete_id(str(item_id))
            return True
