"""Persistent Telegram access control and privacy-aware audit trail for XIDER.

This module intentionally stores neither bot tokens, SSH credentials nor MQTT
secrets.  It only keeps Telegram identities, granted permissions and audit
metadata next to the bot runtime data.
"""

from __future__ import annotations

import json
import re
import threading
import time
from pathlib import Path
from typing import Any


FILE = Path(__file__).resolve().parent / "access_control.json"
AUDIT_FILE = Path(__file__).resolve().parent / "admin_audit.jsonl"
MAX_AUDIT_ROWS = 2_000
_LOCK = threading.RLock()


class Role:
    OWNER = "owner"
    COOWNER = "coowner"
    USER = "user"
    GUEST = "guest"
    BLOCKED = "blocked"

    ALL = (OWNER, COOWNER, USER, GUEST, BLOCKED)


def _load() -> dict[str, Any]:
    try:
        data = json.loads(FILE.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        pass
    return {"users": {}}


def _save(data: dict[str, Any]) -> None:
    FILE.parent.mkdir(parents=True, exist_ok=True)
    temp = FILE.with_suffix(".json.tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(FILE)


def _empty_user(user_id: int) -> dict[str, Any]:
    now = int(time.time())
    return {
        "id": int(user_id),
        "role": Role.GUEST,
        "blocked": False,
        "first_seen": now,
        "last_seen": now,
        "username": "",
        "display_name": "",
        "permissions": {"callbacks": [], "devices": []},
    }


def _safe_user(user: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(user, dict):
        return _empty_user(0)
    base = _empty_user(int(user.get("id") or 0))
    base.update(user)
    perms = user.get("permissions") if isinstance(user.get("permissions"), dict) else {}
    base["permissions"] = {
        "callbacks": sorted({str(x) for x in perms.get("callbacks", []) if str(x)}),
        "devices": sorted({str(x) for x in perms.get("devices", []) if str(x)}),
    }
    return base


def _identity(user: Any) -> dict[str, str]:
    return {
        "username": str(getattr(user, "username", "") or "")[:128],
        "display_name": " ".join(
            part for part in (
                str(getattr(user, "first_name", "") or "").strip(),
                str(getattr(user, "last_name", "") or "").strip(),
            ) if part
        )[:256],
    }


def _redact(text: str) -> str:
    """Keep useful audit context without copying credentials into the log."""
    text = re.sub(r"\b\d{6,}:[A-Za-z0-9_-]{20,}\b", "[TOKEN]", text)
    text = re.sub(
        r"(?i)\b(token|password|passwd|secret|api[_-]?key|ssh[_-]?key)\s*[:=]\s*\S+",
        r"\1=[REDACTED]",
        text,
    )
    text = " ".join(text.split())
    return text[:500]


def append_audit(kind: str, *, actor_id: int | None = None, target_id: int | None = None,
                 detail: str = "") -> None:
    row = {
        "at": int(time.time()),
        "kind": str(kind)[:80],
        "actor_id": int(actor_id) if actor_id is not None else None,
        "target_id": int(target_id) if target_id is not None else None,
        "detail": _redact(str(detail)),
    }
    with _LOCK:
        AUDIT_FILE.parent.mkdir(parents=True, exist_ok=True)
        with AUDIT_FILE.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
        try:
            rows = AUDIT_FILE.read_text(encoding="utf-8").splitlines()
            if len(rows) > MAX_AUDIT_ROWS:
                AUDIT_FILE.write_text("\n".join(rows[-MAX_AUDIT_ROWS:]) + "\n", encoding="utf-8")
        except OSError:
            pass


def recent_audit(limit: int = 20) -> list[dict[str, Any]]:
    try:
        rows = AUDIT_FILE.read_text(encoding="utf-8").splitlines()
    except (FileNotFoundError, OSError):
        return []
    result: list[dict[str, Any]] = []
    for line in reversed(rows[-max(1, min(limit, MAX_AUDIT_ROWS)):]):
        try:
            row = json.loads(line)
            if isinstance(row, dict):
                result.append(row)
        except json.JSONDecodeError:
            continue
    return result


def register_start(user: Any) -> tuple[dict[str, Any], bool]:
    """Register /start and return (record, first_start). New people are guests."""
    user_id = int(user.id)
    with _LOCK:
        data = _load()
        users = data.setdefault("users", {})
        key = str(user_id)
        first_start = key not in users
        record = _safe_user(users.get(key)) if not first_start else _empty_user(user_id)
        record["id"] = user_id
        record["last_seen"] = int(time.time())
        record.update(_identity(user))
        users[key] = record
        _save(data)
    append_audit("telegram_start", actor_id=user_id, detail=f"first={first_start}")
    return dict(record), first_start


def touch_user(user: Any, kind: str, detail: str = "") -> None:
    """Audit a Telegram event without granting access to an unknown person."""
    if user is None:
        return
    user_id = int(user.id)
    with _LOCK:
        data = _load()
        users = data.setdefault("users", {})
        key = str(user_id)
        record = _safe_user(users.get(key)) if key in users else _empty_user(user_id)
        record["id"] = user_id
        record["last_seen"] = int(time.time())
        record.update(_identity(user))
        users[key] = record
        _save(data)
    append_audit(kind, actor_id=user_id, detail=detail)


def get_user(user_id: int) -> dict[str, Any] | None:
    with _LOCK:
        raw = _load().get("users", {}).get(str(int(user_id)))
    return _safe_user(raw) if raw else None


def list_users() -> list[dict[str, Any]]:
    with _LOCK:
        users = _load().get("users", {})
    records = [_safe_user(value) for value in users.values() if isinstance(value, dict)]
    return sorted(records, key=lambda item: (item.get("last_seen", 0), item.get("id", 0)), reverse=True)


def get_role(user_id: int, owner_id: int) -> str:
    if int(user_id) == int(owner_id):
        return Role.OWNER
    record = get_user(int(user_id))
    if not record:
        return Role.GUEST
    if record.get("blocked") or record.get("role") == Role.BLOCKED:
        return Role.BLOCKED
    role = str(record.get("role") or Role.GUEST)
    return role if role in Role.ALL else Role.GUEST


def _update(user_id: int, mutator) -> dict[str, Any]:
    with _LOCK:
        data = _load()
        users = data.setdefault("users", {})
        key = str(int(user_id))
        record = _safe_user(users.get(key)) if key in users else _empty_user(int(user_id))
        mutator(record)
        users[key] = record
        _save(data)
        return dict(record)


def set_role(actor_id: int, target_id: int, role: str, owner_id: int) -> bool:
    if int(target_id) == int(owner_id) or role not in (Role.COOWNER, Role.USER, Role.GUEST):
        return False
    _update(target_id, lambda record: record.update(role=role, blocked=False))
    append_audit("role_set", actor_id=actor_id, target_id=target_id, detail=role)
    return True


def set_blocked(actor_id: int, target_id: int, blocked: bool, owner_id: int) -> bool:
    if int(target_id) == int(owner_id):
        return False
    def change(record: dict[str, Any]) -> None:
        record["blocked"] = bool(blocked)
        record["role"] = Role.BLOCKED if blocked else Role.GUEST
    _update(target_id, change)
    append_audit("user_blocked" if blocked else "user_unblocked", actor_id=actor_id, target_id=target_id)
    return True


def toggle_callback(actor_id: int, target_id: int, callback: str, owner_id: int) -> bool | None:
    if int(target_id) == int(owner_id):
        return None
    current = get_user(target_id) or _empty_user(target_id)
    enabled = callback not in set(current["permissions"]["callbacks"])
    def change(record: dict[str, Any]) -> None:
        callbacks = set(record["permissions"]["callbacks"])
        if enabled:
            callbacks.add(callback)
        else:
            callbacks.discard(callback)
        record["permissions"]["callbacks"] = sorted(callbacks)
    _update(target_id, change)
    append_audit("callback_permission", actor_id=actor_id, target_id=target_id,
                 detail=f"{callback}={enabled}")
    return enabled


def toggle_device(actor_id: int, target_id: int, device_id: str, owner_id: int) -> bool | None:
    if int(target_id) == int(owner_id):
        return None
    current = get_user(target_id) or _empty_user(target_id)
    enabled = device_id not in set(current["permissions"]["devices"])
    def change(record: dict[str, Any]) -> None:
        devices = set(record["permissions"]["devices"])
        if enabled:
            devices.add(device_id)
        else:
            devices.discard(device_id)
        record["permissions"]["devices"] = sorted(devices)
    _update(target_id, change)
    append_audit("device_permission", actor_id=actor_id, target_id=target_id,
                 detail=f"{device_id}={enabled}")
    return enabled


def can_use_callback(user_id: int, callback: str | None, owner_id: int,
                     selected_device: str | None = None) -> bool:
    role = get_role(user_id, owner_id)
    if role in (Role.OWNER, Role.COOWNER):
        return True
    if role != Role.USER:
        return False
    record = get_user(user_id) or {}
    perms = record.get("permissions") or {}
    callbacks = set(perms.get("callbacks") or [])
    device_ids = set(perms.get("devices") or [])
    data = str(callback or "")
    if data in {"menu:main", "menu:about", "menu:devices", "back:device"}:
        return True
    if data.startswith("dev:"):
        device_id = data.split(":", 1)[1]
        return device_id in device_ids
    if selected_device and selected_device in device_ids and "full_device" in callbacks:
        return True
    return data in callbacks
