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
    # Read-only migration marker for older installations. `get_role` downgrades
    # it to USER; it must never continue to grant owner-level access.
    COOWNER = "coowner"
    USER = "user"
    GUEST = "guest"
    BLOCKED = "blocked"

    ALL = (OWNER, COOWNER, USER, GUEST, BLOCKED)


OWNER_ONLY_CALLBACKS = frozenset({"menu:target", "cfm:stop_all"})
OWNER_ONLY_CALLBACK_PREFIXES = (
    "admin:", "devmg:", "server:", "versions:", "ev:", "adm:", "all:",
    "cmd:shell-approve:", "cmd:shell-cancel:",
)
USER_NAVIGATION_CALLBACKS = frozenset({"menu:main", "menu:about", "menu:devices", "back:device"})

INPUT_ACTION_CALLBACKS = frozenset({
    "cmd:clipboard", "cmd:clipset", "fun:type", "fun:hotkey",
    "prank:swapmouse", "prank:crazycursor",
})

# A small set of categories is useful when a role gets only one or two
# individual actions. Entering a category must never imply permission for the
# other buttons inside it; the UI filters those buttons separately.
CATEGORY_REQUIRED_GRANTS = {
    "cat:media": frozenset({
        "cmd:screenshot", "cmd:webcam", "mic:opts", "cmd:sound", "vol:opts", "cmd:volume",
    }),
    "cat:screen": frozenset({
        "cmd:screenshot", "cmd:webcam", "mic:opts", "cmd:sound", "vol:opts", "cmd:volume",
        "cmd:geo_location", "fun:screenoff", "fun:screensaver", "menu:wallpaper",
        "cmd:brightness", "cmd:nightlight",
        "cmd:rotate", "cmd:wallpaper_random_meme", "cmd:wallpaper_photo_guide", "fun:wallpaper",
        "cmd:prank_restore_wallpaper", "rotate:0", "rotate:90", "rotate:180", "rotate:270",
    }),
    "cat:input": INPUT_ACTION_CALLBACKS,
    "cat:system": frozenset({
        "cmd:status", "cmd:sysinfo", "cmd:battery", "cmd:sys_uptime", "cmd:disks", "cmd:smart",
        "cmd:sys_clean_temp", "cmd:apps", "cmd:capabilities",
    }),
    "cat:network": frozenset({
        "fun:extip", "cmd:net_ping", "cmd:network", "cmd:wifi", "cmd:usb", "cmd:bluetooth",
        "cmd:netstat", "cmd:apps", "cmd:url",
    }),
    "menu:wallpaper": frozenset({
        "cmd:wallpaper_random_meme", "cmd:wallpaper_photo_guide", "fun:wallpaper",
        "cmd:prank_restore_wallpaper",
    }),
    "cat:files": frozenset({"files:list", "files:find", "files:get", "files:put", "files:del", "files:open", "cmd:disks"}),
    "cat:terminal": frozenset({
        "shell_request",
        "proc:0", "cmd:prockillname", "cfm:shell", "cmd:open_app", "cmd:startup", "cmd:services",
        "cmd:cmdhistory", "hist:0", "rep:0",
    }) | INPUT_ACTION_CALLBACKS,
    "cat:power": frozenset({
        "cmd:lock", "power:sleep", "power:reboot", "power:shutdown", "cmd:wol", "cmd:autorun_status",
        "cmd:autorun_enable", "cmd:autorun_disable", "cmd:standby_sleep", "cmd:wake", "cmd:guardian_menu",
        "cfm:stop", "guardianstop:ok", "stop:ok", "sleep:ok",
    }),
    "cat:device": frozenset({
        "fav:menu", "hist:0", "cmd:autorun_status", "cmd:autorun_enable", "cmd:autorun_disable",
        "cmd:capabilities",
    }),
}

CATEGORY_REQUIRED_GRANT_PREFIXES = {
    # Dynamic prank names are an existing wire namespace. Pagination is read-only
    # navigation within that exact grant family, never an additional privilege.
    "cat:pranks": ("prank:", "cmd:prank_"),
    "prankpage:": ("prank:", "cmd:prank_"),
}

# `full_device` expands only into callbacks that operate on the currently
# selected, explicitly granted device. New/global callback families stay
# unavailable until deliberately classified here.
DEVICE_ACTION_PREFIXES = (
    "cmd:", "cat:", "files:", "fun:", "power:", "power_confirm:",
    "proc:", "kill:", "killq:", "rotate:", "volq:", "micdur:",
    "prank:", "prankpage:", "fav:", "rep:", "cfm:",
)
DEVICE_ACTION_CALLBACKS = frozenset({
    "menu:wallpaper", "noop", "vol:opts", "mic:opts", "hist:0",
    "openapp:ok", "stop:ok", "guardianstop:ok", "sleep:ok",
})


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


def _redact_credentials(text: str) -> str:
    """Redact before truncation, including a key cut off by the stream cap."""
    text = re.sub(
        r"-----BEGIN (?:OPENSSH |RSA |EC |DSA |ENCRYPTED )?PRIVATE KEY-----.*?"
        r"(?:-----END (?:OPENSSH |RSA |EC |DSA |ENCRYPTED )?PRIVATE KEY-----|\Z)",
        "[PRIVATE KEY REDACTED]",
        text,
        flags=re.DOTALL,
    )
    text = re.sub(r"(?i)\bBearer\s+\S+", "Bearer [REDACTED]", text)
    text = re.sub(r"\b\d{6,}:[A-Za-z0-9_-]{20,}\b", "[TOKEN]", text)
    text = re.sub(
        r'''(?i)["']?\b((?:bot[_-]?)?token|password|passwd|secret|shared[_-]?key|'''
        r'''mqtt[_-]?password|api[_-]?key|ssh[_-]?key|private[_-]?key|authorization)["']?'''
        r'''\s*[:=]\s*(["'])(?:\\(?:.|\Z)|(?!\2)[^\\])*(?:\2|\Z)''',
        r"\1=[REDACTED]", text, flags=re.DOTALL,
    )
    text = re.sub(
        r"(?i)\b((?:bot[_-]?)?token|password|passwd|secret|shared[_-]?key|"
        r"mqtt[_-]?password|api[_-]?key|ssh[_-]?key|private[_-]?key|authorization)"
        r'''[ \t]*[:=][ \t]*(?!\[REDACTED\])[^"'\s][^\r\n]*''',
        r"\1=[REDACTED]",
        text,
    )
    return text


def redact_shell_output(text: str) -> str:
    """Preserve useful multiline output; credential patterns never reach Telegram."""
    return _redact_credentials(str(text))


def _redact(text: str) -> str:
    """Keep useful audit context without copying credentials into the log."""
    text = _redact_credentials(text)
    # An unquoted Windows path has no reliable delimiter: spaces, commas and
    # semicolons can all be part of directory names. Mask its whole remainder.
    text = re.sub(r"(?i)\b[A-Z]:[\\/][^\r\n]*|(?<!\w)/(?:[^\s,;]+)", "[PATH REDACTED]", text)
    text = re.sub(r"\b[\w-]+\.[A-Za-z][A-Za-z0-9]{0,9}\b", "[FILE REDACTED]", text)
    text = re.sub(r"-?\b\d{1,3}\.\d+\s*,\s*-?\d{1,3}\.\d+\b", "[LOCATION REDACTED]", text)
    text = " ".join(text.split())
    return text[:500]


def redact_text(text: str) -> str:
    """Redact credential-like content before it reaches application logs."""
    return _redact(str(text))


def append_audit(kind: str, *, actor_id: int | None = None, target_id: int | None = None,
                 detail: str = "") -> None:
    # Only known metadata events carry detail. New/raw payload kinds fail closed.
    detail_kinds = {
        "telegram_start", "telegram_callback", "role_set", "callback_permission",
        "device_permission", "device_permissions_revoked", "device_approval_policy",
        "server_status", "server_logs", "server_metrics", "server_specs", "server_chart",
        "server_terminal", "server_operation", "bot_text_updated", "ui_style_set",
    }
    if kind not in detail_kinds:
        detail = ""
    row = {
        "at": int(time.time()),
        "kind": str(kind)[:80],
        "actor_id": int(actor_id) if actor_id is not None else None,
        "target_id": int(target_id) if target_id is not None else None,
        "detail": _redact(str(detail)),
    }
    _write_audit_row(row)


def append_command_audit(*, request_id: str, command_hash: str, status: str,
                         requester_id: int, approver_id: int, device_id: str,
                         exit_code: int | None) -> None:
    """Typed execution metadata. This API cannot accept command text/output."""
    statuses = {"attempting", "sent", "completed", "failed", "timeout", "error", "rejected",
                "signing_unavailable", "publish_failed", "result_timeout"}
    if (not isinstance(request_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", request_id)
            or not isinstance(command_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", command_hash)
            or status not in statuses
            or type(requester_id) is not int or type(approver_id) is not int
            or (exit_code is not None and type(exit_code) is not int)):
        raise ValueError("Invalid command audit metadata")
    _write_audit_row({
        "kind": "command_execution", "at": int(time.time()),
        "request_id": request_id, "command_hash": command_hash, "status": status,
        "requester_id": requester_id, "approver_id": approver_id,
        "device_id": _redact_credentials(str(device_id))[:256], "exit_code": exit_code,
    })


def _write_audit_row(row: dict) -> None:
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
        record = _safe_user(users.get(key)) if key in users else _empty_user(user_id)
        first_start = "started_at" not in record
        record["started_at"] = int(time.time())
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
    if role == Role.OWNER:
        return Role.GUEST
    if role == Role.COOWNER:
        return Role.USER
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
    if (int(actor_id) != int(owner_id) or int(target_id) == int(owner_id)
            or not get_user(target_id) or role not in (Role.USER, Role.GUEST)):
        return False
    def change(record):
        record.update(role=role, blocked=False)
        record.pop("role_before_block", None)
    _update(target_id, change)
    append_audit("role_set", actor_id=actor_id, target_id=target_id, detail=role)
    return True


def set_blocked(actor_id: int, target_id: int, blocked: bool, owner_id: int) -> bool:
    if int(actor_id) != int(owner_id) or int(target_id) == int(owner_id) or not get_user(target_id):
        return False
    def change(record: dict[str, Any]) -> None:
        if blocked and not record.get("blocked") and record.get("role") != Role.BLOCKED:
            prior = record.get("role")
            record["role_before_block"] = Role.USER if prior in (Role.USER, Role.COOWNER) else Role.GUEST
        record["blocked"] = bool(blocked)
        if blocked:
            record["role"] = Role.BLOCKED
        else:
            prior = record.pop("role_before_block", Role.GUEST)
            record["role"] = prior if prior in (Role.USER, Role.GUEST) else Role.GUEST
    _update(target_id, change)
    append_audit("user_blocked" if blocked else "user_unblocked", actor_id=actor_id, target_id=target_id)
    return True


def toggle_callback(actor_id: int, target_id: int, callback: str, owner_id: int) -> bool | None:
    if int(actor_id) != int(owner_id) or int(target_id) == int(owner_id) or not get_user(target_id):
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
    if int(actor_id) != int(owner_id) or int(target_id) == int(owner_id) or not get_user(target_id):
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


def revoke_devices(device_ids, *, actor_id: int | None = None) -> int:
    """Remove device grants before a device is deleted, blocked, or uninstalled."""
    revoked = {str(device_id) for device_id in device_ids if str(device_id)}
    if not revoked:
        return 0
    changed_users = 0
    with _LOCK:
        data = _load()
        users = data.setdefault("users", {})
        for key, raw in list(users.items()):
            if not isinstance(raw, dict):
                continue
            record = _safe_user(raw)
            permissions = record["permissions"]
            current = set(permissions.get("devices") or [])
            updated = current - revoked
            if updated == current:
                continue
            permissions["devices"] = sorted(updated)
            users[key] = record
            changed_users += 1
        if changed_users:
            _save(data)
    if changed_users:
        append_audit(
            "device_permissions_revoked",
            actor_id=actor_id,
            detail=f"count={len(revoked)}; users={changed_users}",
        )
    return changed_users


def can_use_callback(user_id: int, callback: str | None, owner_id: int,
                     selected_device: str | None = None) -> bool:
    role = get_role(user_id, owner_id)
    if role == Role.OWNER:
        return True
    data = str(callback or "")
    if data in OWNER_ONLY_CALLBACKS or any(data.startswith(prefix) for prefix in OWNER_ONLY_CALLBACK_PREFIXES):
        return False
    if role != Role.USER:
        return False
    record = get_user(user_id) or {}
    perms = record.get("permissions") or {}
    callbacks = set(perms.get("callbacks") or [])
    device_ids = set(perms.get("devices") or [])
    if data == "cmd:shell":
        return bool(selected_device and selected_device != "all"
                    and selected_device in device_ids and "shell_request" in callbacks)
    if data in USER_NAVIGATION_CALLBACKS:
        return True
    if data.startswith("dev:"):
        device_id = data.split(":", 1)[1]
        return bool(device_id and device_id != "all" and device_id in device_ids)
    # A button grant never grants a device, and a device grant never grants
    # every button. Both scopes must match the current target. Unknown/global
    # callback families fail closed even if stale permission data contains them.
    if not selected_device or selected_device not in device_ids:
        return False
    is_device_action = (
        data in DEVICE_ACTION_CALLBACKS
        or any(data.startswith(prefix) for prefix in DEVICE_ACTION_PREFIXES)
    )
    if not is_device_action:
        return False
    if "full_device" in callbacks or data in callbacks:
        return True
    if data.startswith("kill:"):
        pid = data.split(":", 1)[1]
        if pid.isdigit() and f"killq:{pid}" in callbacks:
            return True
    if data.startswith("proc:"):
        proc_action = data.split(":", 1)[1]
        if (proc_action == "refresh" or proc_action.isdigit()) and "proc:0" in callbacks:
            return True
    required_grants = CATEGORY_REQUIRED_GRANTS.get(data)
    if required_grants and callbacks.intersection(required_grants):
        return True
    for category_or_prefix, required_prefixes in CATEGORY_REQUIRED_GRANT_PREFIXES.items():
        if data == category_or_prefix or data.startswith(category_or_prefix):
            if any(
                grant.startswith(prefix)
                for grant in callbacks
                for prefix in required_prefixes
            ):
                return True
    return False
