"""Short-lived, owner-bound references to shell replies received after timeout.

The inbox stores no command text, agent output, or executable payload. Result
bytes remain in the existing bounded ResponseCollector cache and are consumed
there only after an owner opens one exact device/request reference.
"""

from __future__ import annotations

import re
import secrets


TTL_SECONDS = 30
MAX_ITEMS = 20
_REQUEST_ID = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")
_DEVICE_ID = re.compile(r"[^\x00-\x1f\x7f]{1,256}\Z")


class ShellLateResultInbox:
    def __init__(self) -> None:
        self._items: dict[str, dict] = {}

    def refresh(
        self,
        requesting_user_id: int,
        owner_id: int,
        candidates: list[dict],
        *,
        now: int,
        device_id: str | None = None,
    ) -> list[dict]:
        """Replace the current owner's opaque inbox references from vetted rows."""
        if (type(requesting_user_id) is not int or type(owner_id) is not int
                or requesting_user_id <= 0 or requesting_user_id != owner_id
                or type(now) is not int or now < 0 or not isinstance(candidates, list)):
            return []
        if device_id is not None and (
            not isinstance(device_id, str) or not device_id or len(device_id.encode("utf-8")) > 256
            or _DEVICE_ID.fullmatch(device_id) is None
        ):
            return []

        self._items = {
            token: item for token, item in self._items.items()
            if item["owner_id"] != owner_id and item["expires_at"] > now
        }
        normalized: dict[tuple[str, str], dict] = {}
        for candidate in candidates[:500]:
            if not isinstance(candidate, dict) or candidate.get("status") != "result_timeout":
                continue
            target = candidate.get("device_id")
            request_id = candidate.get("request_id")
            timed_out_at = candidate.get("timed_out_at")
            if (not isinstance(target, str) or not target or len(target.encode("utf-8")) > 256
                    or _DEVICE_ID.fullmatch(target) is None
                    or (device_id is not None and target != device_id)
                    or not isinstance(request_id, str) or _REQUEST_ID.fullmatch(request_id) is None
                    or type(timed_out_at) is not int or timed_out_at < 0
                    or timed_out_at > now or now - timed_out_at >= TTL_SECONDS):
                continue
            key = (target, request_id)
            current = normalized.get(key)
            if current is None or timed_out_at > current["timed_out_at"]:
                normalized[key] = {"device_id": target, "request_id": request_id,
                                   "timed_out_at": timed_out_at,
                                   "expires_at": timed_out_at + TTL_SECONDS}

        rows: list[dict] = []
        for item in sorted(normalized.values(), key=lambda value: (value["timed_out_at"], value["request_id"]), reverse=True)[:MAX_ITEMS]:
            token = secrets.token_urlsafe(9)
            while token in self._items:
                token = secrets.token_urlsafe(9)
            self._items[token] = {"owner_id": owner_id, **item}
            rows.append({"token": token, **item})
        return rows

    def consume(self, requesting_user_id: int, owner_id: int, token: str, *, now: int) -> dict | None:
        """Consume one reference after rechecking owner and expiry."""
        if (type(requesting_user_id) is not int or type(owner_id) is not int
                or requesting_user_id <= 0 or requesting_user_id != owner_id
                or not isinstance(token, str) or not re.fullmatch(r"[A-Za-z0-9_-]{8,32}", token)
                or type(now) is not int or now < 0):
            return None
        item = self._items.get(token)
        if item is None or item["owner_id"] != owner_id or item["expires_at"] <= now:
            self._items.pop(token, None)
            return None
        self._items.pop(token, None)
        return {key: item[key] for key in ("device_id", "request_id", "timed_out_at")}
