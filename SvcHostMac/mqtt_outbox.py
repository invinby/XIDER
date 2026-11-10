"""Bounded in-memory queue for MQTT replies that hit a disconnect."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import json
import threading
import time
import uuid
from typing import Any


@dataclass(frozen=True)
class PendingMqttResponse:
    token: str
    topic_suffix: str
    payload: dict[str, Any]
    payload_bytes: int
    enqueued_at: float


class MqttResponseOutbox:
    """Keep bounded response payloads until the MQTT client accepts them."""

    def __init__(
        self,
        *,
        max_entries: int = 64,
        max_total_bytes: int = 32 * 1024 * 1024,
        max_entry_bytes: int = 8 * 1024 * 1024,
        ttl_seconds: float = 15 * 60,
        clock=time.monotonic,
    ) -> None:
        if max_entries < 1 or max_total_bytes < 1 or max_entry_bytes < 1 or ttl_seconds <= 0:
            raise ValueError("invalid MQTT response outbox limits")
        self.max_entries = max_entries
        self.max_total_bytes = max_total_bytes
        self.max_entry_bytes = max_entry_bytes
        self.ttl_seconds = ttl_seconds
        self._clock = clock
        self._entries: deque[PendingMqttResponse] = deque()
        self._queued_bytes = 0
        self._lock = threading.RLock()

    def enqueue(
        self, topic_suffix: str, payload: dict[str, Any]
    ) -> tuple[bool, str | None, int]:
        """Return (accepted, rejection_reason, expired_entries_removed)."""
        if not isinstance(topic_suffix, str) or not topic_suffix or not isinstance(payload, dict):
            return False, "invalid_response", 0
        try:
            serialized = json.dumps(
                payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False,
            )
            normalized = json.loads(serialized)
            payload_bytes = len(serialized.encode("utf-8"))
        except (TypeError, ValueError, UnicodeEncodeError):
            return False, "not_json_serializable", 0
        if payload_bytes > self.max_entry_bytes:
            return False, "entry_too_large", 0

        now = self._clock()
        with self._lock:
            expired = self._discard_expired_locked(now)
            if len(self._entries) >= self.max_entries:
                return False, "queue_full", expired
            if self._queued_bytes + payload_bytes > self.max_total_bytes:
                return False, "queue_byte_limit", expired
            self._entries.append(PendingMqttResponse(
                token=uuid.uuid4().hex,
                topic_suffix=topic_suffix,
                payload=normalized,
                payload_bytes=payload_bytes,
                enqueued_at=now,
            ))
            self._queued_bytes += payload_bytes
            return True, None, expired

    def peek(self) -> tuple[PendingMqttResponse | None, int]:
        with self._lock:
            expired = self._discard_expired_locked(self._clock())
            return (self._entries[0] if self._entries else None), expired

    def remove(self, token: str) -> bool:
        with self._lock:
            if not self._entries or self._entries[0].token != token:
                return False
            self._queued_bytes -= self._entries.popleft().payload_bytes
            return True

    def __len__(self) -> int:
        with self._lock:
            self._discard_expired_locked(self._clock())
            return len(self._entries)

    def _discard_expired_locked(self, now: float) -> int:
        expired = 0
        while self._entries and now - self._entries[0].enqueued_at >= self.ttl_seconds:
            self._queued_bytes -= self._entries.popleft().payload_bytes
            expired += 1
        return expired
