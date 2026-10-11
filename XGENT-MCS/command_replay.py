"""Durable, bounded one-time reservations for approved shell request IDs.

The ledger deliberately stores only request IDs and envelope expiries. It is
not a command log, signer, or fallback authorization mechanism.
"""

from __future__ import annotations

import json
import os
import re
import sys
import tempfile
import threading
from pathlib import Path


_SCHEMA = 1
_MAX_ENTRIES = 4096
_MAX_BYTES = 256 * 1024
_MAX_SAFE_INTEGER = (1 << 63) - 1
_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_LOCKS_GUARD = threading.Lock()
_PATH_LOCKS: dict[str, threading.RLock] = {}


def default_replay_path(
    platform_name: str | None = None,
    *,
    local_app_data: str | os.PathLike | None = None,
    home: str | os.PathLike | None = None,
) -> Path:
    """Return the approved per-user platform location; unknown platforms fail closed."""
    selected = sys.platform if platform_name is None else platform_name
    if selected.startswith("win"):
        root = local_app_data if local_app_data is not None else os.environ.get("LOCALAPPDATA")
        if not isinstance(root, (str, os.PathLike)) or not os.fspath(root).strip():
            raise OSError("LOCALAPPDATA is unavailable")
        return Path(root) / "XIDER" / "command-replay.json"
    if selected == "darwin":
        root = Path(home) if home is not None else Path.home()
        return root / "Library" / "Application Support" / "XIDER" / "command-replay.json"
    raise OSError("command replay storage is unsupported on this platform")


def _path_lock(path: Path) -> threading.RLock:
    key = os.path.normcase(os.path.abspath(os.fspath(path)))
    with _LOCKS_GUARD:
        lock = _PATH_LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _PATH_LOCKS[key] = lock
        return lock


def _reject_duplicate_pairs(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate replay ledger key")
        result[key] = value
    return result


class CommandReplayLedger:
    """Thread-safe durable replay ledger with atomic, fail-closed writes."""

    def __init__(
        self,
        path: str | os.PathLike | None = None,
        *,
        max_entries: int = _MAX_ENTRIES,
        max_bytes: int = _MAX_BYTES,
    ) -> None:
        if type(max_entries) is not int or max_entries < 0:
            raise ValueError("max_entries must be a non-negative integer")
        if type(max_bytes) is not int or max_bytes < 64:
            raise ValueError("max_bytes is too small")
        self.path = Path(path) if path is not None else default_replay_path()
        self.max_entries = max_entries
        self.max_bytes = max_bytes
        self._lock = _path_lock(self.path)

    def _read(self) -> dict[str, int]:
        try:
            metadata = self.path.lstat()
        except FileNotFoundError:
            return {}
        if self.path.is_symlink() or not self.path.is_file():
            raise ValueError("replay ledger is not a regular file")
        if metadata.st_size > self.max_bytes:
            raise ValueError("replay ledger exceeds its size limit")
        raw = self.path.read_bytes()
        if len(raw) > self.max_bytes:
            raise ValueError("replay ledger exceeds its size limit")
        try:
            data = json.loads(raw.decode("utf-8"), object_pairs_hook=_reject_duplicate_pairs)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("replay ledger is malformed") from exc
        if type(data) is not dict or set(data) != {"schema", "entries"}:
            raise ValueError("replay ledger schema is invalid")
        if type(data["schema"]) is not int or data["schema"] != _SCHEMA:
            raise ValueError("replay ledger schema is unsupported")
        entries = data["entries"]
        if type(entries) is not dict or len(entries) > self.max_entries:
            raise ValueError("replay ledger entry count is invalid")
        validated = {}
        for request_id, expires_at in entries.items():
            if not isinstance(request_id, str) or not _REQUEST_ID_RE.fullmatch(request_id):
                raise ValueError("replay ledger request id is invalid")
            if type(expires_at) is not int or not 0 <= expires_at <= _MAX_SAFE_INTEGER:
                raise ValueError("replay ledger expiry is invalid")
            validated[request_id] = expires_at
        return validated

    def _atomic_write(self, entries: dict[str, int]) -> None:
        serialized = json.dumps(
            {"schema": _SCHEMA, "entries": entries},
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        if len(serialized) > self.max_bytes:
            raise OSError("replay ledger exceeds its size limit")

        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.is_symlink():
            raise OSError("replay ledger path is a symbolic link")
        fd, temporary_name = tempfile.mkstemp(
            prefix=".command-replay-", suffix=".tmp", dir=self.path.parent
        )
        temporary_path = Path(temporary_name)
        try:
            if os.name != "nt":
                os.chmod(temporary_path, 0o600)
            with os.fdopen(fd, "wb") as stream:
                fd = -1
                stream.write(serialized)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary_path, self.path)
            if os.name != "nt":
                directory_fd = os.open(self.path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
        finally:
            if fd >= 0:
                try:
                    os.close(fd)
                except OSError:
                    pass
            try:
                temporary_path.unlink()
            except FileNotFoundError:
                pass
            except OSError:
                pass

    def _pruned(self, entries: dict[str, int], now: int) -> dict[str, int]:
        return {request_id: expiry for request_id, expiry in entries.items() if expiry > now}

    def reserve(self, request_id: object, expires_at: object, *, now: object) -> bool:
        """Atomically persist a verified request ID before its command can execute."""
        if not isinstance(request_id, str) or not _REQUEST_ID_RE.fullmatch(request_id):
            return False
        if (
            type(expires_at) is not int
            or not 0 <= expires_at <= _MAX_SAFE_INTEGER
            or type(now) is not int
            or not 0 <= now <= _MAX_SAFE_INTEGER
        ):
            return False
        if expires_at <= now:
            return False
        with self._lock:
            try:
                entries = self._pruned(self._read(), now)
                if request_id in entries or len(entries) >= self.max_entries:
                    return False
                entries[request_id] = expires_at
                self._atomic_write(entries)
                return True
            except (OSError, ValueError, TypeError):
                return False

    def ensure_available(self, *, now: int) -> bool:
        """Probe storage for status reporting; malformed/unwritable storage is unavailable."""
        if type(now) is not int or now < 0:
            return False
        with self._lock:
            try:
                entries = self._pruned(self._read(), now)
                if len(entries) >= self.max_entries:
                    return False
                self._atomic_write(entries)
                return True
            except (OSError, ValueError, TypeError):
                return False
