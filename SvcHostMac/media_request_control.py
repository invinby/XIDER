"""Bounded, process-local coordination for explicit media request cancellation."""

from __future__ import annotations

from collections import OrderedDict
from datetime import datetime, timedelta, timezone
from threading import Event, RLock


MEDIA_REQUEST_ACTIONS = frozenset({
    "screenshot", "display_region_screenshot", "window_screenshot", "display_contact_sheet",
    "webcam", "camera_series", "camera_quality", "microphone_catalog", "mic",
    "microphone_level", "microphone_balance", "microphone_test", "audio_output_catalog",
    "audio_output_switch",
})
_MAX_ACTIVE = 16
_MAX_TERMINAL = 128
_TERMINAL_TTL = timedelta(seconds=120)


def _now(value: datetime | None) -> datetime:
    current = value or datetime.now(timezone.utc)
    if current.tzinfo is None or current.utcoffset() is None:
        raise ValueError("timestamp_must_be_timezone_aware")
    return current.astimezone(timezone.utc)


class MediaRequestRegistry:
    """Track a small set of active operations and short-lived terminal receipts."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._active: dict[str, tuple[str, bool, Event]] = {}
        self._terminal: OrderedDict[str, tuple[str, datetime]] = OrderedDict()

    def _prune(self, current: datetime) -> None:
        for request_id, (_state, finished_at) in list(self._terminal.items()):
            if current - finished_at > _TERMINAL_TTL:
                del self._terminal[request_id]

    def begin(
        self, request_id: str, action: str, *, cancellable: bool,
        now: datetime | None = None,
    ) -> Event | None:
        current = _now(now)
        if (not isinstance(request_id, str) or not 1 <= len(request_id) <= 128
                or not isinstance(action, str) or action not in MEDIA_REQUEST_ACTIONS
                or not isinstance(cancellable, bool)):
            return None
        with self._lock:
            self._prune(current)
            if request_id in self._active or request_id in self._terminal or len(self._active) >= _MAX_ACTIVE:
                return None
            event = Event()
            self._active[request_id] = (action, cancellable, event)
            return event

    def finish(self, request_id: str, *, now: datetime | None = None) -> str:
        current = _now(now)
        with self._lock:
            self._prune(current)
            entry = self._active.pop(request_id, None)
            if entry is None:
                terminal = self._terminal.get(request_id)
                return terminal[0] if terminal else "unknown"
            state = "cancelled" if entry[2].is_set() else "completed"
            self._terminal[request_id] = (state, current)
            while len(self._terminal) > _MAX_TERMINAL:
                self._terminal.popitem(last=False)
            return state

    def cancel(self, request_id: str, *, now: datetime | None = None) -> str:
        current = _now(now)
        if not isinstance(request_id, str) or not 1 <= len(request_id) <= 128:
            return "unavailable"
        with self._lock:
            self._prune(current)
            entry = self._active.get(request_id)
            if entry is not None:
                _action, cancellable, event = entry
                if not cancellable:
                    return "unavailable"
                event.set()
                return "cancelled"
            terminal = self._terminal.get(request_id)
            if terminal is None:
                return "unavailable"
            return "cancelled" if terminal[0] == "cancelled" else "already_completed"
