"""Bounded owner-only candidates for creating a fresh shell approval request."""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, field
import secrets
import threading
import re


TTL_SECONDS = 300
MAX_CANDIDATES = 128
MAX_COMMAND_BYTES = 4096
MAX_DEVICE_ID_BYTES = 256
_TOKEN_CHARS = 64
_TOKEN_RE = re.compile(r"[A-Za-z0-9_-]{16,64}\Z")
_LOCK = threading.RLock()


@dataclass(frozen=True)
class RepeatCandidate:
    owner_id: int
    device_id: str
    outcome: str
    created_at: int
    expires_at: int
    _command_text: str = field(repr=False)

    @property
    def command_text(self) -> str:
        return self._command_text

    def __repr__(self) -> str:
        return ("RepeatCandidate(owner_id=<redacted>, device_id=<redacted>, "
                f"outcome={self.outcome!r}, expires_at={self.expires_at!r})")


_CANDIDATES: OrderedDict[str, RepeatCandidate] = OrderedDict()


def _prune(now: int) -> None:
    for token, candidate in list(_CANDIDATES.items()):
        if now >= candidate.expires_at:
            del _CANDIDATES[token]


def create(*, owner_id: int, device_id: str, command: str, outcome: str, now: int,
           result_token: str | None = None) -> str | None:
    """Retain exact command text briefly only after a known terminal execution result."""
    if (type(owner_id) is not int or owner_id <= 0 or type(now) is not int or now < 0
            or not isinstance(device_id, str) or not device_id.strip()
            or not isinstance(command, str) or not command.strip()
            or not isinstance(outcome, str) or outcome not in {"completed", "failed"}
            or (result_token is not None and (not isinstance(result_token, str)
                                                or not _TOKEN_RE.fullmatch(result_token)))):
        return None
    try:
        if len(device_id.encode("utf-8")) > MAX_DEVICE_ID_BYTES or len(command.encode("utf-8")) > MAX_COMMAND_BYTES:
            return None
    except UnicodeEncodeError:
        return None
    with _LOCK:
        _prune(now)
        if len(_CANDIDATES) >= MAX_CANDIDATES:
            return None
        if result_token is None:
            token = secrets.token_urlsafe(18)
            while token in _CANDIDATES:
                token = secrets.token_urlsafe(18)
        else:
            token = result_token
            if token in _CANDIDATES:
                return None
        _CANDIDATES[token] = RepeatCandidate(owner_id, device_id, outcome, now,
                                             now + TTL_SECONDS, command)
        return token


def _resolve(token: str, *, owner_id: int, now: int, consume: bool) -> RepeatCandidate | None:
    if (not isinstance(token, str) or not token or len(token) > _TOKEN_CHARS
            or type(owner_id) is not int or owner_id <= 0 or type(now) is not int or now < 0):
        return None
    with _LOCK:
        _prune(now)
        candidate = _CANDIDATES.get(token)
        if candidate is None or candidate.owner_id != owner_id:
            return None
        if consume:
            del _CANDIDATES[token]
        return candidate


def peek(token: str, *, owner_id: int, now: int) -> RepeatCandidate | None:
    """Read a live candidate only for its captured owner."""
    return _resolve(token, owner_id=owner_id, now=now, consume=False)


def consume(token: str, *, owner_id: int, now: int) -> RepeatCandidate | None:
    """Consume a live candidate once; the caller must still create fresh approval."""
    return _resolve(token, owner_id=owner_id, now=now, consume=True)
