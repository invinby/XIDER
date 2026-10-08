"""In-memory, one-time approvals for exact device commands.

Command text is kept only in process memory so an owner can review it before
approval. Public representations and serialization expose metadata only.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import threading


TTL_SECONDS = 120
MAX_PENDING_COMMANDS = 1024
MAX_COMMAND_BYTES = 4096
MAX_DEVICE_ID_BYTES = 256
MAX_REQUEST_ID_CHARS = 128


class PendingStoreFull(RuntimeError):
    """Raised when the bounded pending-command store has no free slot."""


class PendingCommand:
    """Immutable pending command with redacted repr and explicit safe output."""

    __slots__ = (
        "request_id",
        "requester_id",
        "owner_id",
        "device_id",
        "_command_text",
        "command_hash",
        "created_at",
        "expires_at",
        "nonce",
    )

    def __init__(
        self,
        request_id: str,
        requester_id: int,
        owner_id: int,
        device_id: str,
        command_text: str,
        command_hash: str,
        created_at: int,
        expires_at: int,
        nonce: str,
    ) -> None:
        object.__setattr__(self, "request_id", request_id)
        object.__setattr__(self, "requester_id", requester_id)
        object.__setattr__(self, "owner_id", owner_id)
        object.__setattr__(self, "device_id", device_id)
        object.__setattr__(self, "_command_text", command_text)
        object.__setattr__(self, "command_hash", command_hash)
        object.__setattr__(self, "created_at", created_at)
        object.__setattr__(self, "expires_at", expires_at)
        object.__setattr__(self, "nonce", nonce)

    @property
    def command_text(self) -> str:
        """Exact command text for the in-memory approval flow."""

        return self._command_text

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError("PendingCommand is immutable")

    def __repr__(self) -> str:
        return (
            "PendingCommand("
            f"request_id={self.request_id!r}, "
            f"requester_id={self.requester_id!r}, "
            f"owner_id={self.owner_id!r}, "
            f"device_id={self.device_id!r}, "
            f"command_hash={self.command_hash!r}, "
            f"created_at={self.created_at!r}, "
            f"expires_at={self.expires_at!r})"
        )

    def to_public_dict(self) -> dict[str, object]:
        """Return safe request metadata; command text and nonce stay private."""

        return {
            "request_id": self.request_id,
            "requester_id": self.requester_id,
            "owner_id": self.owner_id,
            "device_id": self.device_id,
            "command_hash": self.command_hash,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
        }

    def __reduce_ex__(self, protocol: int) -> object:
        raise TypeError("PendingCommand contains process-local command text and cannot be serialized")


_PENDING_COMMANDS: dict[str, PendingCommand] = {}
_LOCK = threading.RLock()


def _is_plain_int(value: object, *, minimum: int = 0) -> bool:
    return type(value) is int and value >= minimum


def _utf8_size(value: str, field_name: str, *, limit: int) -> int:
    try:
        size = len(value.encode("utf-8"))
    except UnicodeEncodeError as exc:
        raise ValueError(f"{field_name} must be valid UTF-8 text") from exc
    if size > limit:
        raise ValueError(f"{field_name} exceeds the {limit}-byte limit")
    return size


def _prune_expired(now: int) -> None:
    expired = [
        request_id
        for request_id, pending in _PENDING_COMMANDS.items()
        if now >= pending.expires_at
    ]
    for request_id in expired:
        del _PENDING_COMMANDS[request_id]


def create_pending(
    requester_id: int,
    owner_id: int,
    device_id: str,
    command: str,
    now: int,
) -> PendingCommand:
    """Create and store an immutable approval request for exact command text."""

    if not _is_plain_int(requester_id, minimum=1):
        raise ValueError("requester_id must be a positive integer")
    if not _is_plain_int(owner_id, minimum=1):
        raise ValueError("owner_id must be a positive integer")
    if not _is_plain_int(now):
        raise ValueError("now must be a non-negative integer timestamp")
    if not isinstance(device_id, str) or not device_id:
        raise ValueError("device_id must be non-empty text")
    _utf8_size(device_id, "device_id", limit=MAX_DEVICE_ID_BYTES)
    if not isinstance(command, str) or not command.strip():
        raise ValueError("command must be non-empty text")
    _utf8_size(command, "command", limit=MAX_COMMAND_BYTES)
    command_bytes = command.encode("utf-8")

    command_hash = hashlib.sha256(command_bytes).hexdigest()
    nonce = secrets.token_urlsafe(32)

    with _LOCK:
        _prune_expired(now)
        if len(_PENDING_COMMANDS) >= MAX_PENDING_COMMANDS:
            raise PendingStoreFull("pending command store is full")

        request_id = secrets.token_urlsafe(16)
        while request_id in _PENDING_COMMANDS:
            request_id = secrets.token_urlsafe(16)
        pending = PendingCommand(
            request_id=request_id,
            requester_id=requester_id,
            owner_id=owner_id,
            device_id=device_id,
            command_text=command,
            command_hash=command_hash,
            created_at=now,
            expires_at=now + TTL_SECONDS,
            nonce=nonce,
        )
        _PENDING_COMMANDS[request_id] = pending
    return pending


def consume_pending(
    request_id: str,
    approver_id: int,
    device_id: str,
    command_hash: str,
    now: int,
) -> PendingCommand | None:
    """Atomically consume a matching, unexpired request by its captured owner."""

    if (
        not isinstance(request_id, str)
        or not request_id
        or len(request_id) > MAX_REQUEST_ID_CHARS
        or not _is_plain_int(approver_id, minimum=1)
        or not isinstance(device_id, str)
        or not isinstance(command_hash, str)
        or len(command_hash) != 64
        or not command_hash.isascii()
        or any(char not in "0123456789abcdef" for char in command_hash)
        or not _is_plain_int(now)
    ):
        return None

    with _LOCK:
        pending = _PENDING_COMMANDS.get(request_id)
        if pending is None:
            return None
        if now >= pending.expires_at:
            del _PENDING_COMMANDS[request_id]
            return None
        if approver_id != pending.owner_id:
            return None
        if device_id != pending.device_id:
            return None
        if not hmac.compare_digest(command_hash, pending.command_hash):
            return None
        del _PENDING_COMMANDS[request_id]
        return pending


def cancel_pending(request_id: str, actor_id: int) -> bool:
    """Cancel a pending request only for its requester or captured owner."""

    if (
        not isinstance(request_id, str)
        or not request_id
        or len(request_id) > MAX_REQUEST_ID_CHARS
        or not _is_plain_int(actor_id, minimum=1)
    ):
        return False

    with _LOCK:
        pending = _PENDING_COMMANDS.get(request_id)
        if pending is None or actor_id not in (pending.requester_id, pending.owner_id):
            return False
        del _PENDING_COMMANDS[request_id]
        return True
