"""Persistent Telegram access control and privacy-aware audit trail for XIDER.

This module intentionally stores neither bot tokens, SSH credentials nor MQTT
secrets.  It only keeps Telegram identities, granted permissions and audit
metadata next to the bot runtime data.
"""

from __future__ import annotations

import json
import hashlib
import re
import statistics
import uuid
import threading
import time
from pathlib import Path
from typing import Any


FILE = Path(__file__).resolve().parent / "access_control.json"
AUDIT_FILE = Path(__file__).resolve().parent / "admin_audit.jsonl"
MAX_AUDIT_ROWS = 2_000
COMMAND_AUDIT_STATUSES = frozenset({
    "attempting", "sent", "completed", "failed", "timeout", "error", "rejected",
    "cancelled", "cancel_requested", "cancel_confirmed", "cancel_unknown",
    "signing_unavailable", "publish_failed", "publish_unknown", "result_timeout",
    "queue_expired",
})
MAX_RBAC_POLICY_SNAPSHOTS = 50
RBAC_POLICY_SNAPSHOT_SCHEMA = 1
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


OWNER_ONLY_CALLBACKS = frozenset({"menu:target", "cfm:stop_all", "cpu:history", "memory:history", "filevault:status", "sip:status", "gatekeeper:status", "cmd:shell_approval_inbox", "cmd:shell_runbook_templates", "cmd:shell_metadata_export"})
OWNER_ONLY_CALLBACK_PREFIXES = (
    "admin:", "devmg:", "server:", "versions:", "ev:", "adm:", "all:",
    "catalog:fields:",
    "cmd:shell-approve:", "cmd:shell-cancel:", "cmd:shell-inbox:view:", "cmd:shell-page:", "cmd:shell-preview:",
    "cmd:shell-compare-select:", "cmd:shell-repeat:",
    "cmd:shell-template-", "cmd:shell-tpl-",
    "cmd:shell-json:", "cmd:shell-source:",
    "cpu:history:", "memory:history:",
)
USER_NAVIGATION_CALLBACKS = frozenset({
    "menu:main", "menu:about", "menu:devices", "back:device", "devices:tools", "devices:search",
    "devices:search:cancel", "access:request",
})

ACCESS_REQUEST_CALLBACKS = frozenset({
    "shell_request", "cmd:status", "cmd:sysinfo", "cmd:battery",
    "cmd:screenshot", "cmd:lock", "full_device",
})
ACCESS_REQUEST_TTL = 14 * 86_400
MAX_PERMISSION_REQUESTS = 5_000

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
        "cmd:sys_clean_temp", "cmd:apps", "cpu:history", "memory:history",
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
    }),
}

CATEGORY_REQUIRED_GRANT_PREFIXES = {
    # Dynamic prank names are an existing wire namespace. Pagination is read-only
    # navigation within that exact grant family, never an additional privilege.
    # This root is the combined "extra functions / pranks" screen. Device
    # settings remain separately filtered inside it, so users with only a
    # settings grant can still reach their permitted panel without seeing
    # any ungranted prank action.
    "cat:pranks": ("prank:", "cmd:prank_", "fav:", "hist:", "cmd:autorun_"),
    "prankpage:": ("prank:", "cmd:prank_"),
}

# `full_device` expands only into callbacks that operate on the currently
# selected, explicitly granted device. New/global callback families stay
# unavailable until deliberately classified here.
DEVICE_ACTION_PREFIXES = (
    "cmd:", "cat:", "files:", "fun:", "power:", "power_confirm:", "cpu:",
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
            data.setdefault("users", {})
            data.setdefault("permission_profiles", {})
            return data
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        pass
    return {"users": {}, "permission_profiles": {}}


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
        "blocked_until": None,
        "block_reason": "",
        "first_seen": now,
        "last_seen": now,
        "username": "",
        "display_name": "",
        "permissions": {"callbacks": [], "devices": []},
        "permission_expirations": {},
        "permission_expiry_reminders": {},
        "permission_granted_at": {},
        "permission_last_used_at": {},
        "permissions_paused": False,
        "permissions_paused_at": None,
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
    raw_expirations = user.get("permission_expirations") if isinstance(user.get("permission_expirations"), dict) else {}
    base["permission_expirations"] = {
        str(key): int(value)
        for key, value in raw_expirations.items()
        if str(key) and str(value).isdigit() and int(value) > 0
    }
    raw_reminders = user.get("permission_expiry_reminders") if isinstance(user.get("permission_expiry_reminders"), dict) else {}
    base["permission_expiry_reminders"] = {}
    for key, value in raw_reminders.items():
        if (not str(key).startswith(("callback:", "device:")) or not isinstance(value, dict)
                or not str(value.get("expires_at", "")).isdigit()
                or int(value["expires_at"]) <= 0
                or value.get("status") not in {"claimed", "sent"}):
            continue
        marker = {"expires_at": int(value["expires_at"]), "status": value["status"]}
        if str(value.get("updated_at", "")).isdigit() and int(value["updated_at"]) > 0:
            marker["updated_at"] = int(value["updated_at"])
        base["permission_expiry_reminders"][str(key)] = marker
    for field in ("permission_granted_at", "permission_last_used_at"):
        raw = user.get(field) if isinstance(user.get(field), dict) else {}
        base[field] = {
            str(key): int(value)
            for key, value in raw.items()
            if str(key).startswith(("callback:", "device:"))
            and str(value).isdigit() and int(value) > 0
        }
    raw_blocked_until = user.get("blocked_until")
    base["blocked_until"] = (
        int(raw_blocked_until)
        if str(raw_blocked_until or "").isdigit() and int(raw_blocked_until) > 0
        else None
    )
    base["block_reason"] = str(user.get("block_reason") or "").strip()[:240]
    base["permissions_paused"] = bool(user.get("permissions_paused", False))
    raw_paused_at = user.get("permissions_paused_at")
    base["permissions_paused_at"] = (
        int(raw_paused_at) if str(raw_paused_at or "").isdigit() and int(raw_paused_at) > 0 else None
    )
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
    """Redact credentials without exposing category counts."""
    return _redact_credentials_with_summary(text)[0]


def _redact_credentials_with_summary(text: str) -> tuple[str, dict[str, Any]]:
    """Apply the canonical redaction rules while counting only their matches."""
    categories = {
        "private_key": 0,
        "bearer_token": 0,
        "telegram_token": 0,
        "credential_field": 0,
    }

    def count(category: str, replacement: str):
        def replace(_match):
            categories[category] += 1
            return replacement
        return replace

    text = re.sub(
        r"-----BEGIN (?:OPENSSH |RSA |EC |DSA |ENCRYPTED )?PRIVATE KEY-----.*?"
        r"(?:-----END (?:OPENSSH |RSA |EC |DSA |ENCRYPTED )?PRIVATE KEY-----|\Z)",
        count("private_key", "[PRIVATE KEY REDACTED]"),
        text,
        flags=re.DOTALL,
    )

    def replace_bearer(_match):
        categories["bearer_token"] += 1
        return "Bearer [REDACTED]"

    text = re.sub(r"(?i)\bBearer\s+\S+", replace_bearer, text)

    def replace_telegram_token(_match):
        categories["telegram_token"] += 1
        return "[TOKEN]"

    text = re.sub(r"\b\d{6,}:[A-Za-z0-9_-]{20,}(?![A-Za-z0-9_-])", replace_telegram_token, text)
    # Raw redaction-marker text is not trusted; mask an unquoted line before
    # quoted-value replacement introduces any markers of our own.
    text = re.sub(
        r"(?i)\b((?:bot[_-]?)?token|password|passwd|secret|shared[_-]?key|"
        r"mqtt[_-]?password|api[_-]?key|ssh[_-]?key|private[_-]?key|authorization)"
        r'''[ \t]*[:=][ \t]*[^"'\s][^\r\n]*''',
        lambda match: _count_credential_field(categories, match),
        text,
    )

    text = re.sub(
        r'''(?i)["']?\b((?:bot[_-]?)?token|password|passwd|secret|shared[_-]?key|'''
        r'''mqtt[_-]?password|api[_-]?key|ssh[_-]?key|private[_-]?key|authorization)["']?'''
        r'''\s*[:=]\s*(["'])(?:\\(?:.|\Z)|(?!\2)[^\\])*(?:\2|\Z)''',
        lambda match: _count_credential_field(categories, match), text, flags=re.DOTALL,
    )
    return text, {"total": sum(categories.values()), "categories": categories}


def _count_credential_field(categories: dict[str, int], match: re.Match) -> str:
    categories["credential_field"] += 1
    return f"{match.group(1)}=[REDACTED]"


def redact_shell_output(text: str) -> str:
    """Preserve useful multiline output; credential patterns never reach Telegram."""
    return _redact_credentials(str(text))


def redact_shell_output_with_summary(text: str) -> tuple[str, dict[str, Any]]:
    """Return canonical redacted shell text and counts, never matched values."""
    return _redact_credentials_with_summary(str(text))


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
        "user_temporary_blocked", "user_blocked", "user_unblocked", "user_block_expired",
        "permission_request_submitted", "grant_change_undo",
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


def submit_permission_request(
    actor_id: int, owner_id: int, grant_kind: str, item: str, *,
    available_device_ids, now: int | None = None,
) -> dict[str, Any] | None:
    """Queue one exact user permission request; this never grants access."""
    current = int(now if now is not None else time.time())
    if (type(actor_id) is not int or actor_id <= 0 or type(owner_id) is not int
            or owner_id <= 0 or actor_id == owner_id or get_role(actor_id, owner_id) != Role.USER
            or grant_kind not in {"callback", "device"} or not isinstance(item, str)
            or not item or len(item.encode("utf-8")) > 256
            or any(ord(char) < 32 or ord(char) == 127 for char in item)):
        return None
    if grant_kind == "callback":
        if item not in ACCESS_REQUEST_CALLBACKS:
            return None
    else:
        if (not isinstance(available_device_ids, (set, frozenset, list, tuple))
                or len(available_device_ids) > 5_000
                or item not in available_device_ids):
            return None
        if any(not isinstance(device_id, str) or not device_id
               or len(device_id.encode("utf-8")) > 256
               or any(ord(char) < 32 or ord(char) == 127 for char in device_id)
               for device_id in available_device_ids):
            return None

    request = None
    created = False
    with _LOCK:
        data = _load()
        users = data.get("users")
        raw = users.get(str(actor_id)) if isinstance(users, dict) else None
        if not isinstance(raw, dict):
            return None
        record = _safe_user(raw)
        if record.get("blocked") or record.get("role") != Role.USER:
            return None
        permissions = record.get("permissions") or {}
        field = "callbacks" if grant_kind == "callback" else "devices"
        expiry_key = _expiry_key(grant_kind, item)
        expiry = (record.get("permission_expirations") or {}).get(expiry_key)
        if item in set(permissions.get(field) or []) and (expiry is None or int(expiry) > current):
            return {"status": "already_granted", "grant_kind": grant_kind, "item": item}
        rows = data.get("permission_requests")
        if not isinstance(rows, dict):
            rows = {}
        for row in rows.values():
            if (isinstance(row, dict) and row.get("status") == "pending"
                    and type(row.get("expires_at")) is int and row["expires_at"] > current
                    and row.get("actor_id") == actor_id and row.get("grant_kind") == grant_kind
                    and row.get("item") == item):
                request = dict(row)
                request["duplicate"] = True
                break
        if request is None:
            if len(rows) >= MAX_PERMISSION_REQUESTS:
                return None
            request_id = uuid.uuid4().hex[:12]
            while request_id in rows:
                request_id = uuid.uuid4().hex[:12]
            request = {
                "request_id": request_id, "actor_id": actor_id,
                "grant_kind": grant_kind, "item": item,
                "created_at": current, "expires_at": current + ACCESS_REQUEST_TTL,
                "status": "pending", "decision_by": None, "decision_at": None,
            }
            rows[request_id] = request
            data["permission_requests"] = rows
            _save(data)
            created = True
    if created:
        append_audit(
            "permission_request_submitted", actor_id=actor_id, target_id=actor_id,
            detail=f"request_id={request['request_id']}; grant_kind={grant_kind}",
        )
    return {**request, "duplicate": False} if not request.get("duplicate") else request


def list_permission_requests(
    actor_id: int, owner_id: int, *, now: int | None = None, limit: int = 100,
) -> list[dict[str, Any]]:
    """Return pending requests to the owner only; expired and malformed rows stay hidden."""
    if type(actor_id) is not int or type(owner_id) is not int or actor_id != owner_id or owner_id <= 0:
        return []
    current = int(now if now is not None else time.time())
    try:
        limit = max(1, min(int(limit), 500))
    except (TypeError, ValueError, OverflowError):
        return []
    with _LOCK:
        rows = _load().get("permission_requests")
    if not isinstance(rows, dict):
        return []
    pending = [
        dict(row) for row in rows.values()
        if isinstance(row, dict) and row.get("status") == "pending"
        and isinstance(row.get("request_id"), str) and 0 < len(row["request_id"]) <= 32
        and type(row.get("actor_id")) is int and row["actor_id"] > 0
        and row.get("grant_kind") in {"callback", "device"}
        and isinstance(row.get("item"), str) and row["item"]
        and type(row.get("created_at")) is int and type(row.get("expires_at")) is int
        and row["created_at"] <= current < row["expires_at"]
    ]
    return sorted(pending, key=lambda row: (row["created_at"], row["request_id"]))[:limit]


def decide_permission_request(
    actor_id: int, owner_id: int, request_id: str, decision: str, *,
    available_device_ids, now: int | None = None,
) -> dict[str, Any] | None:
    """Owner-only one-shot decision; approval grants exactly one right for 24h."""
    current = int(now if now is not None else time.time())
    if (type(actor_id) is not int or type(owner_id) is not int or owner_id <= 0
            or actor_id != owner_id or not isinstance(request_id, str)
            or len(request_id) != 12 or any(ch not in "0123456789abcdef" for ch in request_id)
            or decision not in {"approve", "reject"}):
        return None
    if not isinstance(available_device_ids, (set, frozenset, list, tuple)) or len(available_device_ids) > 5_000:
        return None
    rows = None
    outcome = None
    grant_written = False
    expires_at = None
    item = grant_kind = None
    target_id = None
    with _LOCK:
        data = _load()
        requests = data.get("permission_requests")
        request = requests.get(request_id) if isinstance(requests, dict) else None
        if (not isinstance(request, dict) or request.get("request_id") != request_id
                or request.get("status") != "pending"
                or type(request.get("expires_at")) is not int
                or not (current < request["expires_at"])
                or type(request.get("actor_id")) is not int or request["actor_id"] <= 0
                or request.get("grant_kind") not in {"callback", "device"}
                or not isinstance(request.get("item"), str) or not request["item"]):
            return None
        target_id = request["actor_id"]
        grant_kind = request["grant_kind"]
        item = request["item"]
        users = data.get("users")
        raw = users.get(str(target_id)) if isinstance(users, dict) else None
        if not isinstance(raw, dict):
            return None
        user = _safe_user(raw)
        if decision == "reject":
            outcome = "rejected"
        elif (user.get("blocked") or user.get("role") != Role.USER
              or (grant_kind == "callback" and item not in ACCESS_REQUEST_CALLBACKS)
              or (grant_kind == "device" and item not in available_device_ids)):
            outcome = "stale"
        else:
            outcome = "approved"
            permission_field = "callbacks" if grant_kind == "callback" else "devices"
            permissions = user["permissions"]
            assigned = set(permissions.get(permission_field) or [])
            expiry_key = _expiry_key(grant_kind, item)
            expiration_values = user.setdefault("permission_expirations", {})
            current_expiry = expiration_values.get(expiry_key)
            already_effective = item in assigned and (
                current_expiry is None or (type(current_expiry) is int and current_expiry > current)
            )
            if not already_effective:
                assigned.add(item)
                permissions[permission_field] = sorted(assigned)
                user.setdefault("permission_granted_at", {})[expiry_key] = current
                user.setdefault("permission_last_used_at", {}).pop(expiry_key, None)
                expires_at = current + 86_400
                expiration_values[expiry_key] = expires_at
                grant_written = True
            users[str(target_id)] = user
        request = dict(request)
        request.update({"status": outcome, "decision_by": actor_id, "decision_at": current})
        if expires_at is not None:
            request["grant_expires_at"] = expires_at
        requests[request_id] = request
        data["permission_requests"] = requests
        _save(data)
    append_audit(
        "permission_request_decision", actor_id=actor_id, target_id=target_id,
        detail=f"request_id={request_id}; decision={outcome}; grant_kind={grant_kind}",
    )
    if grant_written:
        append_grant_audit(
            actor_id=actor_id, target_id=target_id, grant_kind=grant_kind,
            item=item, action="granted", expires_at=expires_at,
        )
    return {**request, "grant_written": grant_written}


def append_grant_audit(*, actor_id: int | None, target_id: int, grant_kind: str,
                       item: str, action: str, expires_at: int | None = None,
                       before: dict[str, Any] | None = None, after: dict[str, Any] | None = None,
                       guard: dict[str, Any] | None = None) -> str:
    """Persist typed permission metadata for the owner-only per-user history."""
    if ((actor_id is not None and (type(actor_id) is not int or actor_id <= 0))
            or type(target_id) is not int or target_id <= 0
            or grant_kind not in {"callback", "device"} or action not in {"granted", "revoked"}
            or not isinstance(item, str) or not item or len(item.encode("utf-8")) > 512
            or any(ord(char) < 32 or ord(char) == 127 for char in item)
            or (expires_at is not None and (type(expires_at) is not int or expires_at <= 0))):
        raise ValueError("Invalid grant audit metadata")
    row = {
        "kind": "grant_change", "at": int(time.time()), "actor_id": actor_id,
        "target_id": target_id, "grant_kind": grant_kind,
        "item": _redact_credentials(item)[:512], "action": action,
        "expires_at": expires_at,
        "change_id": uuid.uuid4().hex,
    }
    if (isinstance(before, dict) and type(before.get("present")) is bool
            and (before.get("expires_at") is None or type(before.get("expires_at")) is int)
            and isinstance(after, dict) and type(after.get("present")) is bool
            and (after.get("expires_at") is None or type(after.get("expires_at")) is int)
            and isinstance(guard, dict) and guard.get("role") in Role.ALL
            and type(guard.get("blocked")) is bool and type(guard.get("permissions_paused")) is bool):
        row["before"] = {"present": before["present"], "expires_at": before.get("expires_at")}
        row["after"] = {"present": after["present"], "expires_at": after.get("expires_at")}
        row["guard"] = {
            "role": guard["role"], "blocked": guard["blocked"],
            "permissions_paused": guard["permissions_paused"],
        }
    _write_audit_row(row)
    return str(row["change_id"])


def append_command_audit(*, request_id: str, command_hash: str, status: str,
                         requester_id: int, approver_id: int, device_id: str,
                         exit_code: int | None) -> None:
    """Typed execution metadata. This API cannot accept command text/output."""
    statuses = {"attempting", "sent", "completed", "failed", "timeout", "error", "rejected", "cancelled", "cancel_requested", "cancel_confirmed", "cancel_unknown",
                "signing_unavailable", "publish_failed", "publish_unknown", "result_timeout", "queue_expired"}
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


def shell_execution_timing_summary(requesting_user_id: int, owner_id: int, *, limit: int = 500) -> dict:
    """Summarize only persisted dispatch/result intervals; approval wait is absent by design."""
    empty = {"state": "empty", "resolution_seconds": 1, "sampled_requests": 0,
             "dispatch": {"count": 0, "mean_seconds": None, "median_seconds": None, "max_seconds": None},
             "result": {"count": 0, "mean_seconds": None, "median_seconds": None, "max_seconds": None}}
    if type(requesting_user_id) is not int or type(owner_id) is not int or requesting_user_id != owner_id:
        return {**empty, "state": "denied"}
    try:
        limit = max(1, min(int(limit), 500))
    except (TypeError, ValueError, OverflowError):
        return {**empty, "state": "invalid"}
    rows = filter_audit(requesting_user_id, owner_id, event_type="command_execution", limit=limit)
    if rows is None:
        return {**empty, "state": "denied"}
    valid_statuses = {"attempting", "sent", "completed", "failed", "timeout", "error", "rejected", "cancelled", "cancel_requested", "cancel_confirmed", "cancel_unknown", "result_timeout", "queue_expired"}
    rank = {"attempting": 0, "sent": 1, "completed": 2, "failed": 2, "timeout": 2,
            "error": 2, "rejected": 2, "cancelled": 2, "cancel_requested": 1,
            "cancel_confirmed": 1, "cancel_unknown": 1, "result_timeout": 2, "queue_expired": 2}
    groups: dict[str, list[dict]] = {}
    identities: dict[str, tuple] = {}
    for row in rows:
        request_id, status, at = row.get("request_id"), row.get("status"), row.get("at")
        command_hash = row.get("command_hash")
        identity = (row.get("device_id"), row.get("requester_id"), row.get("approver_id"), command_hash)
        if (not isinstance(request_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", request_id)
                or status not in valid_statuses or type(at) is not int or at < 0
                or not isinstance(command_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", command_hash)):
            continue
        if request_id in identities and identities[request_id] != identity:
            continue
        identities[request_id] = identity
        groups.setdefault(request_id, []).append({"at": at, "status": status, "rank": rank[status]})

    dispatch: list[int] = []
    result: list[int] = []
    for events in groups.values():
        events.sort(key=lambda item: (item["at"], item["rank"]))
        attempt = next((item["at"] for item in events if item["status"] == "attempting"), None)
        sent = next((item["at"] for item in events if item["status"] == "sent"), None)
        terminal = next((item["at"] for item in events if item["rank"] == 2), None)
        if attempt is not None and sent is not None and sent >= attempt:
            dispatch.append(sent - attempt)
        if sent is not None and terminal is not None and terminal >= sent:
            result.append(terminal - sent)

    def stage(values: list[int]) -> dict:
        return {"count": len(values), "mean_seconds": round(sum(values) / len(values), 2) if values else None,
                "median_seconds": statistics.median(values) if values else None,
                "max_seconds": max(values) if values else None}

    return {"state": "available" if groups else "empty", "resolution_seconds": 1,
            "sampled_requests": len(groups), "dispatch": stage(dispatch), "result": stage(result)}


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


def filter_audit(
    requesting_user_id: int, owner_id: int, *, since: int | None = None, until: int | None = None,
    actor_id: int | None = None, event_type: str | None = None,
    device_id: str | None = None, limit: int = 100,
) -> list[dict[str, Any]] | None:
    """Search bounded audit history; only the configured owner may query it."""
    if int(requesting_user_id) != int(owner_id):
        return None
    if (any(value is not None and (type(value) is not int or value < 0)
            for value in (since, until))
            or (since is not None and until is not None and since > until)
            or (actor_id is not None and (type(actor_id) is not int or actor_id <= 0))
            or (event_type is not None and (not isinstance(event_type, str) or not event_type
                                            or len(event_type) > 80 or any(ord(c) < 32 for c in event_type)))
            or (device_id is not None and (not isinstance(device_id, str) or not device_id
                                           or len(device_id.encode("utf-8")) > 256
                                           or any(ord(c) < 32 for c in device_id)))):
        return None
    try:
        limit = max(1, min(int(limit), 500))
    except (TypeError, ValueError, OverflowError):
        return None
    matches: list[dict[str, Any]] = []
    for row in recent_audit(MAX_AUDIT_ROWS):
        at = row.get("at")
        if type(at) is not int or (since is not None and at < since) or (until is not None and at > until):
            continue
        if event_type is not None and row.get("kind") != event_type:
            continue
        if actor_id is not None and actor_id not in (
            row.get("actor_id"), row.get("requester_id"), row.get("approver_id"),
        ):
            continue
        if device_id is not None:
            referenced = (row.get("device_id"), row.get("device_a"), row.get("device_b"))
            listed = row.get("device_ids")
            if isinstance(listed, list):
                referenced += tuple(item for item in listed if isinstance(item, str))
            if device_id not in referenced:
                continue
        matches.append(row)
        if len(matches) >= limit:
            break
    return matches


def audit_event_reference(row: dict[str, Any]) -> str:
    """Return an opaque reference for a persisted audit row, never its contents."""
    if not isinstance(row, dict):
        return ""
    try:
        encoded = json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    except (TypeError, ValueError, UnicodeError):
        return ""
    if len(encoded) > 32_768:
        return ""
    return hashlib.sha256(encoded).hexdigest()[:20]


def _normalize_audit_event(row: dict[str, Any], reference: str) -> dict[str, Any] | None:
    at = row.get("at")
    kind = row.get("kind")
    if type(at) is not int or at < 0 or not isinstance(kind, str) or not kind or len(kind) > 80:
        return None
    actor = row.get("actor_id")
    target = row.get("target_id")
    device = row.get("device_id")
    if type(actor) is not int and kind == "command_execution":
        actor = row.get("approver_id")
    event: dict[str, Any] = {
        "event_id": reference,
        "at": at,
        "kind": kind,
        "actor_id": actor if type(actor) is int and actor > 0 else None,
        "target_id": target if type(target) is int and target > 0 else None,
        "device_id": _redact_credentials(device)[:256] if isinstance(device, str) and device else None,
        "outcome": "unknown",
        "fields": {},
    }
    fields: dict[str, Any] = {}
    if kind == "command_execution":
        status = row.get("status")
        if isinstance(status, str) and status in COMMAND_AUDIT_STATUSES:
            event["outcome"] = status
        for key in ("requester_id", "approver_id"):
            value = row.get(key)
            if type(value) is int and value > 0:
                fields[key] = value
        exit_code = row.get("exit_code")
        if type(exit_code) is int:
            fields["exit_code"] = exit_code
    elif kind == "grant_change":
        grant_kind, action = row.get("grant_kind"), row.get("action")
        item = row.get("item")
        if grant_kind in {"callback", "device"} and action in {"granted", "revoked"}:
            event["outcome"] = action
            fields["grant_kind"] = grant_kind
            fields["item"] = _redact_credentials(item)[:256] if isinstance(item, str) else "unknown"
            expires = row.get("expires_at")
            if type(expires) is int and expires > 0:
                fields["expires_at"] = expires
    elif kind in {"role_set", "user_blocked", "user_temporary_blocked", "user_unblocked", "user_block_expired"}:
        detail = str(row.get("detail") or "")
        if kind == "role_set":
            role = detail if detail in {Role.USER, Role.GUEST} else "unknown"
            event["outcome"] = "role_changed"
            fields["role"] = role
        elif kind == "user_blocked":
            event["outcome"] = "blocked"
        elif kind == "user_temporary_blocked":
            event["outcome"] = "temporarily_blocked"
            expiry = re.search(r"(?:^|;)\s*expires_at=(\d+)(?:;|$)", detail)
            if expiry:
                fields["expires_at"] = int(expiry.group(1))
        elif kind == "user_unblocked":
            event["outcome"] = "unblocked"
        else:
            event["outcome"] = "block_expired"
        role_match = re.search(r"(?:^|;)\s*role=(user|guest)(?:;|$)", detail)
        if role_match:
            fields["restored_role"] = role_match.group(1)
    elif kind in {"callback_permission", "device_permission"}:
        detail = str(row.get("detail") or "")
        match = re.fullmatch(r"([^=;]{1,128})=(True|False); expires_at=(none|\d+)", detail)
        if match:
            event["outcome"] = "enabled" if match.group(2) == "True" else "disabled"
            fields["permission"] = _redact_credentials(match.group(1))[:128]
            if match.group(3).isdigit():
                fields["expires_at"] = int(match.group(3))
    event["fields"] = fields
    return event


def get_audit_event(requesting_user_id: int, owner_id: int, reference: str) -> dict[str, Any] | None:
    """Fetch one matching owner-only event as a fixed, secret-free projection."""
    if int(requesting_user_id) != int(owner_id) or not isinstance(reference, str) or not re.fullmatch(r"[0-9a-f]{20}", reference):
        return None
    for row in recent_audit(MAX_AUDIT_ROWS):
        if audit_event_reference(row) != reference:
            continue
        return _normalize_audit_event(row, reference)
    return None


def export_audit(
    requesting_user_id: int, owner_id: int, *, since: int, until: int,
    actor_id: int | None = None, event_type: str | None = None,
    device_id: str | None = None, limit: int = 100,
) -> dict[str, Any] | None:
    """Return a bounded, redacted audit export for an explicit UTC interval."""
    if (int(requesting_user_id) != int(owner_id) or type(since) is not int or type(until) is not int
            or since < 0 or until < since or until - since > (31 * 86_400 - 1)
            or type(limit) is not int or not 1 <= limit <= 100):
        return None
    rows = filter_audit(
        requesting_user_id, owner_id, since=since, until=until,
        actor_id=actor_id, event_type=event_type, device_id=device_id, limit=limit + 1,
    )
    if rows is None:
        return None
    normalized = []
    for row in rows[:limit]:
        event = _normalize_audit_event(row, audit_event_reference(row))
        if event is not None:
            normalized.append(event)
    return {
        "schema": "xider.audit-export.v1",
        "generated_at": int(time.time()),
        "range": {"since": since, "until": until},
        "filters": {
            "actor_id": actor_id,
            "event_type": event_type,
            "device_id": _redact_credentials(device_id)[:256] if isinstance(device_id, str) else None,
        },
        "truncated": len(rows) > limit,
        "events": normalized,
    }


def export_shell_command_audit(
    requesting_user_id: int, owner_id: int, *, since: int, until: int,
    statuses: list[str] | tuple[str, ...] | set[str] | frozenset[str],
    device_id: str | None = None, limit: int = 100,
) -> dict[str, Any] | None:
    """Export only selected shell execution metadata; never command text or output."""
    if (int(requesting_user_id) != int(owner_id) or type(since) is not int or type(until) is not int
            or since < 0 or until < since or until - since > (31 * 86_400 - 1)
            or type(limit) is not int or not 1 <= limit <= 100
            or not isinstance(statuses, (list, tuple, set, frozenset)) or not statuses
            or len(statuses) > len(COMMAND_AUDIT_STATUSES)
            or any(not isinstance(status, str) or status not in COMMAND_AUDIT_STATUSES for status in statuses)
            or (device_id is not None and (not isinstance(device_id, str) or not device_id
                                           or len(device_id.encode("utf-8")) > 256
                                           or any(ord(char) < 32 for char in device_id)))):
        return None
    selected_statuses = sorted(set(statuses))
    events: list[dict[str, Any]] = []
    for row in recent_audit(MAX_AUDIT_ROWS):
        at = row.get("at")
        if (row.get("kind") != "command_execution" or type(at) is not int
                or at < since or at > until or row.get("status") not in selected_statuses
                or (device_id is not None and row.get("device_id") != device_id)):
            continue
        normalized = _normalize_audit_event(row, audit_event_reference(row))
        if normalized is None:
            continue
        fields = normalized.get("fields") if isinstance(normalized.get("fields"), dict) else {}
        events.append({
            "at": normalized["at"],
            "status": normalized["outcome"],
            "actor_id": normalized["actor_id"],
            "target_id": normalized["target_id"],
            "device_id": normalized["device_id"],
            "requester_id": fields.get("requester_id"),
            "approver_id": fields.get("approver_id"),
            "exit_code": fields.get("exit_code"),
        })
        if len(events) > limit:
            break
    return {
        "schema": "xider.shell-audit-export.v1",
        "generated_at": int(time.time()),
        "range": {"since": since, "until": until},
        "filters": {"statuses": selected_statuses, "device_id": device_id},
        "truncated": len(events) > limit,
        "events": events[:limit],
    }


def get_role_history(actor_id: int, target_id: int, owner_id: int, limit: int = 20) -> list[dict[str, Any]] | None:
    """Return one account's role/block transitions to the owner only."""
    if int(actor_id) != int(owner_id) or int(target_id) == int(owner_id) or not get_user(int(target_id)):
        return None
    limit = max(1, min(int(limit), 100))
    events = {"role_set", "user_blocked", "user_temporary_blocked", "user_unblocked", "user_block_expired"}
    history: list[dict[str, Any]] = []
    for row in recent_audit(MAX_AUDIT_ROWS):
        if row.get("target_id") != int(target_id) or row.get("kind") not in events:
            continue
        event = str(row["kind"])
        detail = str(row.get("detail") or "")
        if event == "role_set":
            role = detail if detail in {Role.USER, Role.GUEST} else "unknown"
        elif event in {"user_blocked", "user_temporary_blocked"}:
            role = Role.BLOCKED
        else:
            match = re.search(r"(?:^|;)\s*role=(user|guest)(?:;|$)", detail)
            role = match.group(1) if match else "unknown"
        item = {
            "at": int(row.get("at") or 0),
            "event": event,
            "role": role,
            "actor_id": row.get("actor_id") if type(row.get("actor_id")) is int else None,
        }
        expiry = re.search(r"(?:^|;)\s*expires_at=(\d+)(?:;|$)", detail)
        if expiry:
            item["expires_at"] = int(expiry.group(1))
        history.append(item)
        if len(history) >= limit:
            break
    return history


def get_grant_history(actor_id: int, target_id: int, owner_id: int, limit: int = 30) -> list[dict[str, Any]] | None:
    """Return a target-scoped list of typed permission grants and revocations."""
    if int(actor_id) != int(owner_id) or int(target_id) == int(owner_id) or not get_user(int(target_id)):
        return None
    limit = max(1, min(int(limit), 100))
    history: list[dict[str, Any]] = []
    for row in recent_audit(MAX_AUDIT_ROWS):
        if row.get("kind") != "grant_change" or row.get("target_id") != int(target_id):
            continue
        grant_kind = row.get("grant_kind")
        action = row.get("action")
        item = row.get("item")
        if grant_kind not in {"callback", "device"} or action not in {"granted", "revoked"} or not isinstance(item, str):
            continue
        history.append({
            "at": int(row.get("at") or 0),
            "actor_id": row.get("actor_id") if type(row.get("actor_id")) is int else None,
            "kind": grant_kind,
            "item": item,
            "action": action,
            "expires_at": row.get("expires_at") if type(row.get("expires_at")) is int else None,
        })
        if len(history) >= limit:
            break
    return history


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
    _expire_temporary_block(int(user_id))
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
        record["blocked_until"] = None
        record["block_reason"] = ""
    _update(target_id, change)
    append_audit("role_set", actor_id=actor_id, target_id=target_id, detail=role)
    return True


def set_blocked(
    actor_id: int, target_id: int, blocked: bool, owner_id: int, *,
    expires_at: int | None = None, reason: str | None = None,
) -> bool:
    if int(actor_id) != int(owner_id) or int(target_id) == int(owner_id) or not get_user(target_id):
        return False
    if blocked and expires_at is not None:
        if not _valid_expiry(expires_at) or not isinstance(reason, str) or not reason.strip():
            return False
    elif expires_at is not None or reason:
        return False
    def change(record: dict[str, Any]) -> None:
        if blocked and not record.get("blocked") and record.get("role") != Role.BLOCKED:
            prior = record.get("role")
            record["role_before_block"] = Role.USER if prior in (Role.USER, Role.COOWNER) else Role.GUEST
        record["blocked"] = bool(blocked)
        if blocked:
            record["role"] = Role.BLOCKED
            record["blocked_until"] = int(expires_at) if expires_at is not None else None
            record["block_reason"] = str(reason or "").strip()[:240]
        else:
            prior = record.pop("role_before_block", Role.GUEST)
            record["role"] = prior if prior in (Role.USER, Role.GUEST) else Role.GUEST
            record["blocked_until"] = None
            record["block_reason"] = ""
    updated = _update(target_id, change)
    append_audit(
        "user_temporary_blocked" if blocked and expires_at is not None else
        "user_blocked" if blocked else "user_unblocked",
        actor_id=actor_id, target_id=target_id,
        detail=(
            f"expires_at={int(expires_at)}; role=blocked" if blocked and expires_at is not None else
            "role=blocked" if blocked else f"role={updated.get('role') or Role.GUEST}"
        ),
    )
    return True


def _expire_temporary_block(user_id: int, *, now: int | None = None) -> bool:
    current = int(now if now is not None else time.time())
    record = get_user(user_id)
    expires_at = (record or {}).get("blocked_until")
    if not record or not record.get("blocked") or not expires_at or int(expires_at) > current:
        return False
    expired = False
    restored_role = Role.GUEST

    def restore_if_due(current_record: dict[str, Any]) -> None:
        nonlocal expired, restored_role
        deadline = current_record.get("blocked_until")
        if not current_record.get("blocked") or not deadline or int(deadline) > current:
            return
        prior = current_record.pop("role_before_block", Role.GUEST)
        current_record["role"] = prior if prior in (Role.USER, Role.GUEST) else Role.GUEST
        restored_role = current_record["role"]
        current_record["blocked"] = False
        # Keep the deadline and reason as a small, reviewable history record.
        expired = True

    _update(user_id, restore_if_due)
    if expired:
        append_audit("user_block_expired", actor_id=None, target_id=user_id, detail=f"role={restored_role}")
    return expired


def _valid_expiry(expires_at: int | None) -> bool:
    if expires_at is None:
        return True
    try:
        value = int(expires_at)
    except (TypeError, ValueError):
        return False
    now = int(time.time())
    return now < value <= now + 365 * 86_400


def get_latest_grant_change(actor_id: int, target_id: int, owner_id: int) -> dict[str, Any] | None:
    """Return the latest grant event for owner preview, including reversible metadata when available."""
    if (type(actor_id) is not int or type(target_id) is not int or type(owner_id) is not int
            or actor_id != owner_id or target_id == owner_id or not get_user(target_id)):
        return None
    for row in recent_audit(MAX_AUDIT_ROWS):
        if row.get("kind") != "grant_change" or row.get("target_id") != target_id:
            continue
        if row.get("grant_kind") not in {"callback", "device"} or row.get("action") not in {"granted", "revoked"}:
            continue
        item = row.get("item")
        if not isinstance(item, str) or not item:
            return None
        before, after, guard = row.get("before"), row.get("after"), row.get("guard")
        reversible = (
            isinstance(row.get("change_id"), str) and re.fullmatch(r"[0-9a-f]{32}", row["change_id"])
            and isinstance(before, dict) and type(before.get("present")) is bool
            and (before.get("expires_at") is None or type(before.get("expires_at")) is int)
            and isinstance(after, dict) and type(after.get("present")) is bool
            and (after.get("expires_at") is None or type(after.get("expires_at")) is int)
            and isinstance(guard, dict) and guard.get("role") in Role.ALL
            and type(guard.get("blocked")) is bool and type(guard.get("permissions_paused")) is bool
        )
        return {
            "reference": audit_event_reference(row), "at": int(row.get("at") or 0),
            "kind": row["grant_kind"], "item": _redact_credentials(item)[:256],
            "action": row["action"], "expires_at": row.get("expires_at") if type(row.get("expires_at")) is int else None,
            "reversible": bool(reversible),
            "before": dict(before) if reversible else None,
            "after": dict(after) if reversible else None,
        }
    return None


def undo_latest_grant_change(
    actor_id: int, target_id: int, owner_id: int, reference: str, *,
    valid_callbacks: set[str], valid_devices: set[str],
) -> dict[str, Any]:
    """Reverse the latest exact grant event only if policy and grant state are unchanged."""
    failure = {"ok": False, "reason": "stale_or_conflicting"}
    if (type(actor_id) is not int or type(target_id) is not int or type(owner_id) is not int
            or actor_id != owner_id or target_id == owner_id or owner_id <= 0
            or not isinstance(reference, str) or not re.fullmatch(r"[0-9a-f]{20}", reference)
            or not isinstance(valid_callbacks, set) or not isinstance(valid_devices, set)
            or any(not isinstance(value, str) for value in valid_callbacks | valid_devices)):
        return failure
    target = get_user(target_id)
    if target is None:
        return failure
    latest = next((row for row in recent_audit(MAX_AUDIT_ROWS)
                   if row.get("kind") == "grant_change" and row.get("target_id") == target_id
                   and row.get("grant_kind") in {"callback", "device"}
                   and row.get("action") in {"granted", "revoked"}), None)
    if latest is None or audit_event_reference(latest) != reference:
        return failure
    conflict_kinds = {
        "role_set", "user_blocked", "user_temporary_blocked", "user_unblocked",
        "user_block_expired", "user_grants_paused", "user_grants_resumed",
    }
    for row in recent_audit(MAX_AUDIT_ROWS):
        if row.get("target_id") == target_id and row.get("kind") in conflict_kinds:
            return failure
        if row.get("kind") == "grant_change" and audit_event_reference(row) == reference:
            break
    preview = get_latest_grant_change(actor_id, target_id, owner_id)
    if not preview or not preview["reversible"]:
        return {"ok": False, "reason": "not_reversible"}
    kind, item = preview["kind"], str(latest["item"])
    before, after = preview["before"], preview["after"]
    if _grant_state(target, kind, item) != after or _grant_guard(target) != latest["guard"]:
        return failure
    restore = before["present"]
    if restore and ((kind == "callback" and item not in valid_callbacks)
                    or (kind == "device" and item not in valid_devices)):
        return {"ok": False, "reason": "target_no_longer_valid"}
    field = "callbacks" if kind == "callback" else "devices"
    expiry_key = _expiry_key(kind, item)

    def change(record: dict[str, Any]) -> None:
        values = set(record["permissions"][field])
        if restore:
            values.add(item)
            if before["expires_at"] is None:
                record.setdefault("permission_expirations", {}).pop(expiry_key, None)
            else:
                record.setdefault("permission_expirations", {})[expiry_key] = int(before["expires_at"])
            record.setdefault("permission_granted_at", {})[expiry_key] = int(time.time())
            record.setdefault("permission_last_used_at", {}).pop(expiry_key, None)
        else:
            values.discard(item)
            record.setdefault("permission_expirations", {}).pop(expiry_key, None)
            record.setdefault("permission_granted_at", {}).pop(expiry_key, None)
            record.setdefault("permission_last_used_at", {}).pop(expiry_key, None)
            record.setdefault("permission_expiry_reminders", {}).pop(expiry_key, None)
        record["permissions"][field] = sorted(values)

    _update(target_id, change)
    updated = get_user(target_id) or _empty_user(target_id)
    after_undo = _grant_state(updated, kind, item)
    append_audit("grant_change_undo", actor_id=actor_id, target_id=target_id,
                 detail=f"{kind}:{item}; source={reference}")
    append_grant_audit(
        actor_id=actor_id, target_id=target_id, grant_kind=kind, item=item,
        action="granted" if after_undo["present"] else "revoked",
        expires_at=after_undo["expires_at"], before=after, after=after_undo,
        guard=_grant_guard(updated),
    )
    return {"ok": True, "kind": kind, "item": _redact_credentials(item)[:256], "restored": bool(restore)}


def _expiry_key(kind: str, value: str) -> str:
    return f"{kind}:{value}"


def _grant_state(record: dict[str, Any], kind: str, item: str) -> dict[str, Any]:
    values = (record.get("permissions") or {}).get("callbacks" if kind == "callback" else "devices") or []
    expiry = (record.get("permission_expirations") or {}).get(_expiry_key(kind, item))
    return {
        "present": item in set(values),
        "expires_at": int(expiry) if type(expiry) is int and expiry > 0 else None,
    }


def _grant_guard(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "role": record.get("role") if record.get("role") in Role.ALL else Role.GUEST,
        "blocked": bool(record.get("blocked")),
        "permissions_paused": bool(record.get("permissions_paused")),
    }


def toggle_callback(
    actor_id: int, target_id: int, callback: str, owner_id: int,
    *, expires_at: int | None = None,
) -> bool | None:
    if int(actor_id) != int(owner_id) or int(target_id) == int(owner_id) or not get_user(target_id):
        return None
    if not _valid_expiry(expires_at):
        return None
    # Expired rows remain in storage for audit/history, but toggling the UI
    # must operate on effective access so the first click renews the grant.
    target = get_user(target_id) or _empty_user(target_id)
    before_state = _grant_state(target, "callback", callback)
    if target.get("permissions_paused"):
        enabled = callback not in set(target["permissions"].get("callbacks") or [])
    else:
        enabled = callback not in set(get_effective_grants(target_id)["callbacks"])
    def change(record: dict[str, Any]) -> None:
        callbacks = set(record["permissions"]["callbacks"])
        expirations = record.setdefault("permission_expirations", {})
        granted_at = record.setdefault("permission_granted_at", {})
        last_used = record.setdefault("permission_last_used_at", {})
        expiry_key = _expiry_key("callback", callback)
        if enabled:
            callbacks.add(callback)
            granted_at[expiry_key] = int(time.time())
            last_used.pop(expiry_key, None)
            if expires_at is None:
                expirations.pop(expiry_key, None)
            else:
                expirations[expiry_key] = int(expires_at)
        else:
            callbacks.discard(callback)
            expirations.pop(expiry_key, None)
            granted_at.pop(expiry_key, None)
            last_used.pop(expiry_key, None)
        record["permissions"]["callbacks"] = sorted(callbacks)
    _update(target_id, change)
    after_record = get_user(target_id) or _empty_user(target_id)
    after_state = _grant_state(after_record, "callback", callback)
    append_audit("callback_permission", actor_id=actor_id, target_id=target_id,
                 detail=f"{callback}={enabled}; expires_at={int(expires_at) if enabled and expires_at else 'none'}")
    append_grant_audit(
        actor_id=int(actor_id), target_id=int(target_id), grant_kind="callback", item=callback,
        action="granted" if enabled else "revoked", expires_at=int(expires_at) if enabled and expires_at else None,
        before=before_state, after=after_state, guard=_grant_guard(after_record),
    )
    return enabled


def toggle_device(
    actor_id: int, target_id: int, device_id: str, owner_id: int,
    *, expires_at: int | None = None,
) -> bool | None:
    if int(actor_id) != int(owner_id) or int(target_id) == int(owner_id) or not get_user(target_id):
        return None
    if not _valid_expiry(expires_at):
        return None
    target = get_user(target_id) or _empty_user(target_id)
    before_state = _grant_state(target, "device", device_id)
    if target.get("permissions_paused"):
        enabled = device_id not in set(target["permissions"].get("devices") or [])
    else:
        enabled = device_id not in set(get_effective_grants(target_id)["devices"])
    def change(record: dict[str, Any]) -> None:
        devices = set(record["permissions"]["devices"])
        expirations = record.setdefault("permission_expirations", {})
        granted_at = record.setdefault("permission_granted_at", {})
        last_used = record.setdefault("permission_last_used_at", {})
        expiry_key = _expiry_key("device", device_id)
        if enabled:
            devices.add(device_id)
            granted_at[expiry_key] = int(time.time())
            last_used.pop(expiry_key, None)
            if expires_at is None:
                expirations.pop(expiry_key, None)
            else:
                expirations[expiry_key] = int(expires_at)
        else:
            devices.discard(device_id)
            expirations.pop(expiry_key, None)
            granted_at.pop(expiry_key, None)
            last_used.pop(expiry_key, None)
        record["permissions"]["devices"] = sorted(devices)
    _update(target_id, change)
    after_record = get_user(target_id) or _empty_user(target_id)
    after_state = _grant_state(after_record, "device", device_id)
    append_audit("device_permission", actor_id=actor_id, target_id=target_id,
                 detail=f"{device_id}={enabled}; expires_at={int(expires_at) if enabled and expires_at else 'none'}")
    append_grant_audit(
        actor_id=int(actor_id), target_id=int(target_id), grant_kind="device", item=device_id,
        action="granted" if enabled else "revoked", expires_at=int(expires_at) if enabled and expires_at else None,
        before=before_state, after=after_state, guard=_grant_guard(after_record),
    )
    return enabled


def set_temporary_callback_grant(
    actor_id: int, target_id: int, callback: str, owner_id: int, *, expires_at: int,
) -> bool | None:
    """Set (rather than toggle) one owner-issued, expiring callback grant."""
    if (int(actor_id) != int(owner_id) or int(target_id) == int(owner_id)
            or not get_user(target_id) or not _valid_expiry(expires_at)
            or expires_at is None):
        return None
    before_record = get_user(target_id) or _empty_user(target_id)
    before_state = _grant_state(before_record, "callback", callback)
    def change(record: dict[str, Any]) -> None:
        callbacks = set(record["permissions"]["callbacks"])
        callbacks.add(callback)
        record["permissions"]["callbacks"] = sorted(callbacks)
        expiry_key = _expiry_key("callback", callback)
        record.setdefault("permission_expirations", {})[expiry_key] = int(expires_at)
        record.setdefault("permission_granted_at", {})[expiry_key] = int(time.time())
        record.setdefault("permission_last_used_at", {}).pop(expiry_key, None)
        record.setdefault("permission_expiry_reminders", {}).pop(expiry_key, None)
    _update(target_id, change)
    after_record = get_user(target_id) or _empty_user(target_id)
    append_audit("callback_permission", actor_id=actor_id, target_id=target_id,
                 detail=f"{callback}=temporary; expires_at={int(expires_at)}")
    append_grant_audit(
        actor_id=int(actor_id), target_id=int(target_id), grant_kind="callback", item=callback,
        action="granted", expires_at=int(expires_at),
        before=before_state, after=_grant_state(after_record, "callback", callback), guard=_grant_guard(after_record),
    )
    return True


def set_temporary_device_grant(
    actor_id: int, target_id: int, device_id: str, owner_id: int, *, expires_at: int,
) -> bool | None:
    """Set (rather than toggle) one owner-issued, expiring device grant."""
    if (int(actor_id) != int(owner_id) or int(target_id) == int(owner_id)
            or not get_user(target_id) or not _valid_expiry(expires_at)
            or expires_at is None):
        return None
    before_record = get_user(target_id) or _empty_user(target_id)
    before_state = _grant_state(before_record, "device", device_id)
    def change(record: dict[str, Any]) -> None:
        devices = set(record["permissions"]["devices"])
        devices.add(device_id)
        record["permissions"]["devices"] = sorted(devices)
        expiry_key = _expiry_key("device", device_id)
        record.setdefault("permission_expirations", {})[expiry_key] = int(expires_at)
        record.setdefault("permission_granted_at", {})[expiry_key] = int(time.time())
        record.setdefault("permission_last_used_at", {}).pop(expiry_key, None)
        record.setdefault("permission_expiry_reminders", {}).pop(expiry_key, None)
    _update(target_id, change)
    after_record = get_user(target_id) or _empty_user(target_id)
    append_audit("device_permission", actor_id=actor_id, target_id=target_id,
                 detail=f"{device_id}=temporary; expires_at={int(expires_at)}")
    append_grant_audit(
        actor_id=int(actor_id), target_id=int(target_id), grant_kind="device", item=device_id,
        action="granted", expires_at=int(expires_at),
        before=before_state, after=_grant_state(after_record, "device", device_id), guard=_grant_guard(after_record),
    )
    return True


def get_effective_grants(user_id: int, *, now: int | None = None) -> dict[str, list[str]]:
    """Return only current grants; expired stored rows confer no authority."""
    current = int(now if now is not None else time.time())
    record = get_user(user_id) or _empty_user(user_id)
    if record.get("permissions_paused"):
        return {"callbacks": [], "devices": []}
    expirations = record.get("permission_expirations") or {}
    permissions = record.get("permissions") or {}
    def active(kind: str, items: list[str]) -> list[str]:
        return sorted({str(item) for item in items if str(item) and
                       int(expirations.get(_expiry_key(kind, str(item)), current + 1)) > current})
    return {
        "callbacks": active("callback", permissions.get("callbacks") or []),
        "devices": active("device", permissions.get("devices") or []),
    }


def _rbac_snapshot_digest(snapshot: dict[str, Any]) -> str:
    payload = {
        key: snapshot[key]
        for key in ("schema_version", "owner_id", "created_at", "entry_count", "entries")
    }
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def verify_rbac_policy_snapshot(snapshot: Any) -> bool:
    """Verify the schema and content digest of a stored RBAC policy snapshot."""
    if not isinstance(snapshot, dict) or set(snapshot) != {
        "snapshot_id", "schema_version", "owner_id", "created_at", "entry_count", "entries", "sha256",
    }:
        return False
    if (type(snapshot.get("schema_version")) is not int
            or snapshot["schema_version"] != RBAC_POLICY_SNAPSHOT_SCHEMA
            or type(snapshot.get("owner_id")) is not int or snapshot["owner_id"] <= 0
            or type(snapshot.get("created_at")) is not int or snapshot["created_at"] <= 0
            or type(snapshot.get("entry_count")) is not int
            or not isinstance(snapshot.get("entries"), list)
            or snapshot["entry_count"] != len(snapshot["entries"])):
        return False
    digest = snapshot.get("sha256")
    snapshot_id = snapshot.get("snapshot_id")
    if (not isinstance(digest, str) or len(digest) != 64
            or not isinstance(snapshot_id, str) or len(snapshot_id) != 24):
        return False
    try:
        actual = _rbac_snapshot_digest(snapshot)
    except (KeyError, TypeError, ValueError):
        return False
    return digest == actual and snapshot_id == actual[:24]


def _rbac_snapshot_effective_role(user_id: int, owner_id: int, record: dict[str, Any], now: int) -> str:
    if user_id == owner_id:
        return Role.OWNER
    role = str(record.get("role") or Role.GUEST)
    blocked = bool(record.get("blocked"))
    deadline = record.get("blocked_until")
    if blocked and deadline is not None and int(deadline) <= now:
        blocked = False
        role = str(record.get("role_before_block") or Role.GUEST)
    if blocked or role == Role.BLOCKED:
        return Role.BLOCKED
    if role == Role.COOWNER:
        return Role.USER
    return role if role in {Role.USER, Role.GUEST} else Role.GUEST


def _rbac_policy_entry(user_id: int, owner_id: int, record: dict[str, Any], now: int) -> dict[str, Any]:
    safe = _safe_user(record)
    permissions = safe.get("permissions") or {}
    expirations = safe.get("permission_expirations") or {}
    expiration_rows = []
    for key, expiry in expirations.items():
        kind, separator, item = str(key).partition(":")
        if separator and kind in {"callback", "device"} and item:
            expiration_rows.append({"kind": kind, "item": item, "expires_at": int(expiry)})
    expiration_rows.sort(key=lambda row: (row["kind"], row["item"], row["expires_at"]))
    effective = get_effective_grants(user_id, now=now)
    return {
        "user_id": int(user_id),
        "stored_role": str(safe.get("role") or Role.GUEST),
        "effective_role": _rbac_snapshot_effective_role(user_id, owner_id, safe, now),
        "stored_blocked": bool(safe.get("blocked")),
        "permissions_paused": bool(safe.get("permissions_paused")),
        "stored_callbacks": sorted(set(permissions.get("callbacks") or [])),
        "stored_devices": sorted(set(permissions.get("devices") or [])),
        "effective_callbacks": sorted(set(effective.get("callbacks") or [])),
        "effective_devices": sorted(set(effective.get("devices") or [])),
        "expirations": expiration_rows,
    }


def create_rbac_policy_snapshot(
    actor_id: int, owner_id: int, *, now: int | None = None,
) -> dict[str, Any] | None:
    """Persist an owner-only, secret-free, content-verifiable RBAC snapshot."""
    if (type(actor_id) is not int or actor_id <= 0 or type(owner_id) is not int
            or owner_id <= 0 or actor_id != owner_id):
        return None
    current = int(now if now is not None else time.time())
    if current <= 0:
        return None
    with _LOCK:
        data = _load()
        users = data.get("users") if isinstance(data.get("users"), dict) else {}
        records: dict[int, dict[str, Any]] = {}
        for key, raw in users.items():
            if not isinstance(raw, dict) or not str(key).isdigit():
                continue
            user_id = int(key)
            if user_id <= 0 or type(raw.get("id")) is not int or int(raw["id"]) != user_id:
                continue
            records[user_id] = _safe_user(raw)
        records.setdefault(owner_id, _empty_user(owner_id))
        entries = [_rbac_policy_entry(user_id, owner_id, records[user_id], current)
                   for user_id in sorted(records)]
        snapshot: dict[str, Any] = {
            "schema_version": RBAC_POLICY_SNAPSHOT_SCHEMA,
            "owner_id": owner_id,
            "created_at": current,
            "entry_count": len(entries),
            "entries": entries,
        }
        digest = _rbac_snapshot_digest(snapshot)
        snapshot["snapshot_id"] = digest[:24]
        snapshot["sha256"] = digest
        snapshots = data.get("policy_snapshots")
        snapshots = snapshots if isinstance(snapshots, list) else []
        snapshots = [row for row in snapshots
                     if verify_rbac_policy_snapshot(row) and row.get("owner_id") == owner_id]
        snapshots = [row for row in snapshots if row.get("snapshot_id") != snapshot["snapshot_id"]]
        snapshots.append(snapshot)
        data["policy_snapshots"] = snapshots[-MAX_RBAC_POLICY_SNAPSHOTS:]
        _save(data)
    append_audit("rbac_policy_snapshot_created", actor_id=actor_id)
    return snapshot


def get_rbac_policy_snapshot(actor_id: int, owner_id: int, snapshot_id: str) -> dict[str, Any] | None:
    """Retrieve one verified snapshot for its owner; corrupted rows fail closed."""
    if (type(actor_id) is not int or type(owner_id) is not int or actor_id != owner_id
            or owner_id <= 0 or not isinstance(snapshot_id, str) or len(snapshot_id) != 24):
        return None
    with _LOCK:
        data = _load()
    snapshots = data.get("policy_snapshots") if isinstance(data.get("policy_snapshots"), list) else []
    for row in snapshots:
        if (isinstance(row, dict) and row.get("owner_id") == owner_id
                and row.get("snapshot_id") == snapshot_id and verify_rbac_policy_snapshot(row)):
            return json.loads(json.dumps(row, ensure_ascii=False))
    return None


def list_rbac_policy_snapshots(
    actor_id: int, owner_id: int, *, limit: int = 10,
) -> list[dict[str, int | str]] | None:
    """List only verified receipt metadata for the configured owner."""
    if (type(actor_id) is not int or type(owner_id) is not int or actor_id != owner_id
            or owner_id <= 0 or type(limit) is not int or limit <= 0):
        return None
    bounded_limit = min(limit, MAX_RBAC_POLICY_SNAPSHOTS)
    with _LOCK:
        data = _load()
    snapshots = data.get("policy_snapshots") if isinstance(data.get("policy_snapshots"), list) else []
    verified = [row for row in snapshots
                if isinstance(row, dict) and row.get("owner_id") == owner_id
                and verify_rbac_policy_snapshot(row)]
    verified.sort(key=lambda row: (int(row["created_at"]), str(row["snapshot_id"])), reverse=True)
    return [{
        "snapshot_id": str(row["snapshot_id"]),
        "created_at": int(row["created_at"]),
        "entry_count": int(row["entry_count"]),
        "schema_version": int(row["schema_version"]),
    } for row in verified[:bounded_limit]]


def compare_rbac_policy_snapshots(
    actor_id: int, owner_id: int, from_snapshot_id: str, to_snapshot_id: str,
) -> dict[str, Any] | None:
    """Compare two verified snapshots and return exact owner-visible policy deltas."""
    if (type(actor_id) is not int or type(owner_id) is not int or actor_id != owner_id
            or owner_id <= 0 or from_snapshot_id == to_snapshot_id):
        return None
    before = get_rbac_policy_snapshot(actor_id, owner_id, from_snapshot_id)
    after = get_rbac_policy_snapshot(actor_id, owner_id, to_snapshot_id)
    if before is None or after is None:
        return None

    before_rows = {row["user_id"]: row for row in before["entries"]}
    after_rows = {row["user_id"]: row for row in after["entries"]}
    empty = {
        "stored_role": Role.GUEST, "effective_role": Role.GUEST,
        "stored_blocked": False, "permissions_paused": False,
        "stored_callbacks": [], "stored_devices": [],
        "effective_callbacks": [], "effective_devices": [], "expirations": [],
    }

    def set_delta(left, right) -> dict[str, list[str]]:
        left_set, right_set = set(left or []), set(right or [])
        return {"added": sorted(right_set - left_set), "removed": sorted(left_set - right_set)}

    changes = []
    for user_id in sorted(set(before_rows) | set(after_rows)):
        left, right = before_rows.get(user_id, empty), after_rows.get(user_id, empty)
        row: dict[str, Any] = {"user_id": user_id}
        if left.get("effective_role") != right.get("effective_role"):
            row["role"] = {"before": left.get("effective_role"), "after": right.get("effective_role")}
        if left.get("stored_blocked") != right.get("stored_blocked"):
            row["blocked"] = {"before": bool(left.get("stored_blocked")), "after": bool(right.get("stored_blocked"))}
        if left.get("permissions_paused") != right.get("permissions_paused"):
            row["permissions_paused"] = {
                "before": bool(left.get("permissions_paused")),
                "after": bool(right.get("permissions_paused")),
            }
        for field in ("stored_callbacks", "stored_devices", "effective_callbacks", "effective_devices"):
            delta = set_delta(left.get(field), right.get(field))
            if delta["added"] or delta["removed"]:
                row[field] = delta
        def expiry_map(entry):
            rows = entry.get("expirations") if isinstance(entry.get("expirations"), list) else []
            result = {}
            for item in rows:
                if (isinstance(item, dict) and item.get("kind") in {"callback", "device"}
                        and isinstance(item.get("item"), str) and type(item.get("expires_at")) is int):
                    result[(item["kind"], item["item"])] = int(item["expires_at"])
            return result
        left_exp, right_exp = expiry_map(left), expiry_map(right)
        expiry_delta = {
            "added": [
                {"kind": kind, "item": item, "expires_at": right_exp[(kind, item)]}
                for kind, item in sorted(set(right_exp) - set(left_exp))
            ],
            "removed": [
                {"kind": kind, "item": item, "expires_at": left_exp[(kind, item)]}
                for kind, item in sorted(set(left_exp) - set(right_exp))
            ],
            "changed": [
                {"kind": kind, "item": item, "before": left_exp[(kind, item)], "after": right_exp[(kind, item)]}
                for kind, item in sorted(set(left_exp) & set(right_exp))
                if left_exp[(kind, item)] != right_exp[(kind, item)]
            ],
        }
        if any(expiry_delta.values()):
            row["expirations"] = expiry_delta
        if len(row) > 1:
            changes.append(row)
    return {
        "from_snapshot_id": before["snapshot_id"],
        "to_snapshot_id": after["snapshot_id"],
        "from_created_at": before["created_at"],
        "to_created_at": after["created_at"],
        "changes": changes,
    }


def list_expiring_grants(*, now: int | None = None, within_s: int = 900) -> list[dict[str, Any]]:
    """List active user grants expiring within the notification horizon."""
    current = int(now if now is not None else time.time())
    horizon = max(1, min(int(within_s), 30 * 86_400))
    rows: list[dict[str, Any]] = []
    for record in list_users():
        if record.get("blocked") or record.get("role") not in {Role.USER, Role.COOWNER}:
            continue
        user_id = int(record.get("id") or 0)
        permissions = record.get("permissions") or {}
        expirations = record.get("permission_expirations") or {}
        for kind, field in (("callback", "callbacks"), ("device", "devices")):
            for item in permissions.get(field) or []:
                expiry = expirations.get(_expiry_key(kind, str(item)))
                if expiry is not None and current < int(expiry) <= current + horizon:
                    rows.append({"user_id": user_id, "kind": kind, "item": str(item), "expires_at": int(expiry)})
    return sorted(rows, key=lambda row: (row["expires_at"], row["user_id"], row["kind"], row["item"]))


def claim_expiry_reminder(
    user_id: int, kind: str, item: str, expires_at: int, *, now: int | None = None,
    stale_claim_s: int = 300,
) -> bool:
    """Atomically claim one reminder for an unchanged, still-effective grant."""
    if kind not in {"callback", "device"} or not str(item):
        return False
    current = int(now if now is not None else time.time())
    key = _expiry_key(kind, str(item))
    claimed = False

    def change(record: dict[str, Any]) -> None:
        nonlocal claimed
        expiry = (record.get("permission_expirations") or {}).get(key)
        field = "callbacks" if kind == "callback" else "devices"
        role = record.get("role")
        if (record.get("blocked") or role not in {Role.USER, Role.COOWNER}
                or str(item) not in set((record.get("permissions") or {}).get(field) or [])
                or expiry != int(expires_at) or int(expiry or 0) <= current):
            return
        reminders = record.setdefault("permission_expiry_reminders", {})
        marker = reminders.get(key) if isinstance(reminders.get(key), dict) else {}
        if int(marker.get("expires_at") or 0) == int(expires_at):
            if marker.get("status") == "sent":
                return
            if marker.get("status") == "claimed" and current - int(marker.get("updated_at") or current) < max(30, int(stale_claim_s)):
                return
        reminders[key] = {"expires_at": int(expires_at), "status": "claimed", "updated_at": current}
        claimed = True

    _update(int(user_id), change)
    return claimed


def finish_expiry_reminder(
    user_id: int, kind: str, item: str, expires_at: int, *, sent: bool, now: int | None = None,
) -> bool:
    """Mark a claimed reminder sent, or release it after delivery failure."""
    if kind not in {"callback", "device"} or not str(item):
        return False
    current = int(now if now is not None else time.time())
    key = _expiry_key(kind, str(item))
    finished = False

    def change(record: dict[str, Any]) -> None:
        nonlocal finished
        reminders = record.setdefault("permission_expiry_reminders", {})
        marker = reminders.get(key) if isinstance(reminders.get(key), dict) else {}
        if int(marker.get("expires_at") or 0) != int(expires_at) or marker.get("status") != "claimed":
            return
        if sent:
            reminders[key] = {"expires_at": int(expires_at), "status": "sent", "updated_at": current}
        else:
            reminders.pop(key, None)
        finished = True

    _update(int(user_id), change)
    return finished


def _matching_callback_grants(callback: str, callbacks: set[str]) -> list[str]:
    if callback == "cmd:shell":
        return ["shell_request"] if "shell_request" in callbacks else []
    if "full_device" in callbacks:
        return ["full_device"]
    if callback in callbacks:
        return [callback]
    if callback.startswith("kill:"):
        pid = callback.split(":", 1)[1]
        candidate = f"killq:{pid}"
        return [candidate] if pid.isdigit() and candidate in callbacks else []
    if callback.startswith("proc:"):
        proc_action = callback.split(":", 1)[1]
        return ["proc:0"] if (proc_action == "refresh" or proc_action.isdigit()) and "proc:0" in callbacks else []
    required = CATEGORY_REQUIRED_GRANTS.get(callback)
    if required:
        matched = sorted(callbacks.intersection(required))
        if matched:
            return matched
    for category_or_prefix, prefixes in CATEGORY_REQUIRED_GRANT_PREFIXES.items():
        if callback == category_or_prefix or callback.startswith(category_or_prefix):
            return sorted({grant for grant in callbacks for prefix in prefixes if grant.startswith(prefix)})
    return []


def record_grant_use(
    user_id: int, callback: str | None, owner_id: int, selected_device: str | None = None,
) -> bool:
    """Record only the active grant(s) that passed the exact user authorization gate."""
    if get_role(user_id, owner_id) != Role.USER:
        return False
    data = str(callback or "")
    if not explain_callback_access(user_id, data, owner_id, selected_device)["allowed"]:
        return False
    if data in USER_NAVIGATION_CALLBACKS:
        return False
    grants = get_effective_grants(user_id)
    if data.startswith("dev:"):
        device_id = data.split(":", 1)[1]
        used_devices = [device_id] if device_id in grants["devices"] else []
        used_callbacks: list[str] = []
    else:
        used_devices = [selected_device] if selected_device in grants["devices"] else []
        used_callbacks = _matching_callback_grants(data, set(grants["callbacks"]))
    if not used_devices and not used_callbacks:
        return False
    now = int(time.time())
    changed = False

    def update(record: dict[str, Any]) -> None:
        nonlocal changed
        if record.get("blocked") or record.get("role") != Role.USER:
            return
        permissions = record.get("permissions") or {}
        expirations = record.get("permission_expirations") or {}
        last_used = record.setdefault("permission_last_used_at", {})
        for kind, items in (("device", used_devices), ("callback", used_callbacks)):
            current = set(permissions.get("devices" if kind == "device" else "callbacks") or [])
            for item in items:
                key = _expiry_key(kind, item)
                expiry = expirations.get(key)
                if item not in current or (expiry is not None and int(expiry) <= now):
                    continue
                if int(last_used.get(key) or 0) != now:
                    last_used[key] = now
                    changed = True

    _update(user_id, update)
    return changed


def review_grants(
    actor_id: int, owner_id: int, *, unused_after_s: int = 30 * 24 * 60 * 60,
    now: int | None = None,
) -> list[dict[str, Any]] | None:
    """Owner-only inventory of expired, stale, and historically unknown grants."""
    if int(actor_id) != int(owner_id) or type(unused_after_s) is not int or not 1 <= unused_after_s <= 365 * 24 * 60 * 60:
        return None
    current = int(now if now is not None else time.time())
    rows: list[dict[str, Any]] = []
    for user in list_users():
        user_id = int(user.get("id") or 0)
        if not user_id or user_id == int(owner_id):
            continue
        safe = _safe_user(user)
        permissions = safe.get("permissions") or {}
        expirations = safe.get("permission_expirations") or {}
        granted_at = safe.get("permission_granted_at") or {}
        last_used = safe.get("permission_last_used_at") or {}
        for kind, items in (("device", permissions.get("devices") or []), ("permission", permissions.get("callbacks") or [])):
            expiry_kind = "device" if kind == "device" else "callback"
            for item in sorted({str(value) for value in items if str(value)}):
                key = _expiry_key(expiry_kind, item)
                expires = expirations.get(key)
                created = granted_at.get(key)
                used = last_used.get(key)
                if expires is not None and int(expires) <= current:
                    state = "expired"
                elif not created or int(created) > current or (used is not None and int(used) > current):
                    state = "history_unknown"
                elif used is None and current - int(created) >= unused_after_s:
                    state = "unused"
                elif used is not None and current - int(used) >= unused_after_s:
                    state = "long_unused"
                else:
                    state = "active"
                rows.append({
                    "target_id": user_id,
                    "kind": kind,
                    "item": item,
                    "state": state,
                    "granted_at": int(created) if created is not None else None,
                    "expires_at": int(expires) if expires is not None else None,
                    "last_used_at": int(used) if used is not None else None,
                    "blocked": bool(safe.get("blocked")),
                })
    return sorted(rows, key=lambda row: (row["kind"], row["item"], row["target_id"]))


def find_stale_device_grants(
    actor_id: int, owner_id: int, available_device_ids, *, now: int | None = None,
    limit: int = 200,
) -> dict[str, Any] | None:
    """List active grants whose exact device ID is absent from the live inventory."""
    if (int(actor_id) != int(owner_id) or int(owner_id) <= 0
            or not isinstance(available_device_ids, (set, frozenset, list, tuple))
            or len(available_device_ids) > 5_000 or type(limit) is not int or not 1 <= limit <= 500):
        return None
    known: set[str] = set()
    for device_id in available_device_ids:
        if (not isinstance(device_id, str) or not device_id or len(device_id.encode("utf-8")) > 256
                or any(ord(char) < 32 for char in device_id)):
            return None
        known.add(device_id)
    current = int(now if now is not None else time.time())
    matches: list[dict[str, Any]] = []
    for user in list_users():
        user_id = int(user.get("id") or 0)
        if not user_id or user_id == int(owner_id):
            continue
        safe = _safe_user(user)
        permissions = safe.get("permissions") or {}
        expirations = safe.get("permission_expirations") or {}
        for raw_id in permissions.get("devices") or []:
            if not isinstance(raw_id, str) or not raw_id or raw_id in known:
                continue
            expiry = expirations.get(_expiry_key("device", raw_id))
            if expiry is not None:
                if type(expiry) is not int or expiry <= current:
                    continue
            matches.append({
                "target_id": user_id,
                "device_id": raw_id,
                "blocked": bool(safe.get("blocked")),
                "expires_at": expiry,
            })
    matches.sort(key=lambda row: (row["device_id"], row["target_id"]))
    return {"rows": matches[:limit], "truncated": len(matches) > limit}


def monthly_access_review(
    actor_id: int, owner_id: int, *, page: int = 0, page_size: int = 4,
    now: int | None = None,
) -> dict[str, Any] | None:
    """Build a read-only, owner-only, paginated snapshot of roles and effective grants."""
    if (int(actor_id) != int(owner_id) or int(owner_id) <= 0
            or type(page) is not int or page < 0
            or type(page_size) is not int or not 1 <= page_size <= 10):
        return None
    current = int(now if now is not None else time.time())
    records: list[tuple[int, dict[str, Any]]] = []
    for user in list_users():
        safe = _safe_user(user)
        user_id = int(safe.get("id") or 0)
        if user_id > 0 and user_id != int(owner_id):
            records.append((user_id, safe))
    records.sort(key=lambda row: row[0])
    total = len(records)
    pages = max(1, (total + page_size - 1) // page_size)
    if page >= pages:
        return None
    selected = records[page * page_size:(page + 1) * page_size]
    rows = []
    for user_id, record in selected:
        role = str(record.get("role") or Role.GUEST)
        blocked = bool(record.get("blocked"))
        deadline = record.get("blocked_until")
        if blocked and deadline is not None and int(deadline) <= current:
            blocked = False
            role = str(record.get("role_before_block") or Role.GUEST)
        if blocked or role == Role.BLOCKED:
            role, blocked = Role.BLOCKED, True
        elif role == Role.COOWNER:
            role = Role.USER
        elif role not in {Role.USER, Role.GUEST}:
            role = Role.GUEST
        grants = get_effective_grants(user_id, now=current)
        rows.append({
            "target_id": user_id,
            "role": role,
            "blocked": blocked,
            "devices": grants["devices"],
            "callbacks": grants["callbacks"],
        })
    return {
        "generated_at": current, "page": page, "page_size": page_size,
        "total": total, "pages": pages, "rows": rows,
    }


_BULK_GRANT_CALLBACKS = frozenset({
    "shell_request", "cmd:status", "cmd:sysinfo", "cmd:battery",
    "cmd:screenshot", "cmd:lock", "full_device",
})

MAX_PERMISSION_PROFILES = 100
MAX_PERMISSION_PROFILE_NAME = 48


def _safe_permission_profile(profile_id: str, raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    name = raw.get("name")
    callbacks = raw.get("callbacks")
    devices = raw.get("devices")
    created_at, updated_at = raw.get("created_at"), raw.get("updated_at")
    if (not isinstance(profile_id, str) or not re.fullmatch(r"[0-9a-f]{16}", profile_id)
            or not isinstance(name, str) or not _valid_permission_profile_name(name)
            or not isinstance(callbacks, list) or len(callbacks) > len(_BULK_GRANT_CALLBACKS)
            or not isinstance(devices, list) or len(devices) > 20
            or type(created_at) is not int or created_at <= 0
            or type(updated_at) is not int or updated_at <= 0):
        return None
    if (any(not isinstance(item, str) or item not in _BULK_GRANT_CALLBACKS for item in callbacks)
            or any(not isinstance(item, str) or not item or len(item.encode("utf-8")) > 256
                   or any(ord(char) < 32 for char in item) for item in devices)
            or len(set(callbacks)) != len(callbacks) or len(set(devices)) != len(devices)):
        return None
    return {
        "profile_id": profile_id,
        "name": name,
        "devices": sorted(devices),
        "callbacks": sorted(callbacks),
        "created_at": created_at,
        "updated_at": updated_at,
    }


def _valid_permission_profile_name(name: Any) -> bool:
    return (
        isinstance(name, str)
        and bool(name)
        and len(name) <= MAX_PERMISSION_PROFILE_NAME
        and len(name.encode("utf-8")) <= 128
        and name == name.strip()
        and not any(ord(char) < 32 or ord(char) == 127 for char in name)
    )


def list_permission_profiles(actor_id: int, owner_id: int) -> list[dict[str, Any]] | None:
    """Return named reusable grant sets only to the configured owner."""
    if type(actor_id) is not int or type(owner_id) is not int or actor_id != owner_id or owner_id <= 0:
        return None
    with _LOCK:
        raw_profiles = _load().get("permission_profiles")
    if not isinstance(raw_profiles, dict):
        return []
    rows = [
        profile for profile_id, raw in raw_profiles.items()
        if (profile := _safe_permission_profile(profile_id, raw)) is not None
    ]
    return sorted(rows, key=lambda item: (item["name"].casefold(), item["profile_id"]))


def get_permission_profile(actor_id: int, owner_id: int, profile_id: str) -> dict[str, Any] | None:
    if (type(actor_id) is not int or type(owner_id) is not int or actor_id != owner_id
            or owner_id <= 0 or not isinstance(profile_id, str)):
        return None
    with _LOCK:
        raw = _load().get("permission_profiles", {}).get(profile_id)
    return _safe_permission_profile(profile_id, raw)


def create_permission_profile(
    actor_id: int, owner_id: int, name: str, source_user_id: int,
) -> dict[str, Any] | None:
    """Save a bounded, owner-named snapshot of another account's effective grants."""
    if (type(actor_id) is not int or type(owner_id) is not int or actor_id != owner_id
            or owner_id <= 0 or type(source_user_id) is not int or source_user_id <= 0
            or source_user_id == owner_id or not _valid_permission_profile_name(name)):
        return None
    source = get_user(source_user_id)
    if source is None:
        return None
    grants = get_effective_grants(source_user_id)
    callbacks = grants["callbacks"]
    devices = grants["devices"]
    if (not callbacks and not devices
            or any(item not in _BULK_GRANT_CALLBACKS for item in callbacks)
            or len(callbacks) > len(_BULK_GRANT_CALLBACKS) or len(devices) > 20):
        return None
    now = int(time.time())
    profile_id = uuid.uuid4().hex[:16]
    profile = {
        "profile_id": profile_id, "name": name, "devices": sorted(devices),
        "callbacks": sorted(callbacks), "created_at": now, "updated_at": now,
    }
    with _LOCK:
        data = _load()
        raw_profiles = data.setdefault("permission_profiles", {})
        if not isinstance(raw_profiles, dict) or len(raw_profiles) >= MAX_PERMISSION_PROFILES:
            return None
        current = [
            _safe_permission_profile(key, value)
            for key, value in raw_profiles.items()
        ]
        if any(row is not None and row["name"].casefold() == name.casefold() for row in current):
            return None
        raw_profiles[profile_id] = profile
        _save(data)
    append_audit(
        "permission_profile_created", actor_id=actor_id,
        detail=f"profile={profile_id}; devices={len(devices)}; callbacks={len(callbacks)}",
    )
    return dict(profile)


def delete_permission_profile(actor_id: int, owner_id: int, profile_id: str) -> bool:
    if (type(actor_id) is not int or type(owner_id) is not int or actor_id != owner_id
            or owner_id <= 0 or not isinstance(profile_id, str)
            or not re.fullmatch(r"[0-9a-f]{16}", profile_id)):
        return False
    with _LOCK:
        data = _load()
        raw_profiles = data.get("permission_profiles")
        if not isinstance(raw_profiles, dict) or _safe_permission_profile(profile_id, raw_profiles.get(profile_id)) is None:
            return False
        raw_profiles.pop(profile_id, None)
        _save(data)
    append_audit("permission_profile_deleted", actor_id=actor_id, detail=f"profile={profile_id}")
    return True


def _bulk_access_snapshot(user_id: int, record: dict[str, Any], now: int) -> dict[str, Any]:
    role = str(record.get("role") or Role.GUEST)
    blocked = bool(record.get("blocked"))
    deadline = record.get("blocked_until")
    if blocked and deadline is not None and int(deadline) <= now:
        blocked = False
        role = str(record.get("role_before_block") or Role.GUEST)
    if blocked or role == Role.BLOCKED:
        role, blocked = Role.BLOCKED, True
    elif role == Role.COOWNER:
        role = Role.USER
    elif role not in {Role.USER, Role.GUEST}:
        role = Role.GUEST
    expirations = record.get("permission_expirations") or {}
    permissions = record.get("permissions") or {}

    def active(kind: str, values: list[str]) -> list[str]:
        return sorted({
            str(item) for item in values
            if str(item) and int(expirations.get(_expiry_key(kind, str(item)), now + 1)) > now
        })

    return {
        "target_id": int(user_id), "role": role, "blocked": blocked,
        "devices": active("device", permissions.get("devices") or []),
        "callbacks": active("callback", permissions.get("callbacks") or []),
    }


def _validate_bulk_grant_inputs(
    actor_id: int, owner_id: int, target_ids, callbacks, device_ids, available_device_ids,
) -> tuple[list[int], list[str], list[str]] | None:
    try:
        if int(actor_id) != int(owner_id) or int(owner_id) <= 0:
            return None
    except (TypeError, ValueError):
        return None
    if (not isinstance(target_ids, (list, tuple)) or not 1 <= len(target_ids) <= 20
            or not isinstance(callbacks, (list, tuple)) or len(callbacks) > len(_BULK_GRANT_CALLBACKS)
            or not isinstance(device_ids, (list, tuple)) or len(device_ids) > 20
            or not isinstance(available_device_ids, (set, frozenset, list, tuple))
            or len(available_device_ids) > 5_000):
        return None
    if not callbacks and not device_ids:
        return None
    if any(type(value) is not int or value <= 0 or value == int(owner_id) for value in target_ids):
        return None
    targets = list(target_ids)
    callbacks_clean = list(callbacks)
    devices_clean = list(device_ids)
    if (len(set(targets)) != len(targets) or len(set(callbacks_clean)) != len(callbacks_clean)
            or len(set(devices_clean)) != len(devices_clean)):
        return None
    if any(not isinstance(value, str) or value not in _BULK_GRANT_CALLBACKS for value in callbacks_clean):
        return None
    known = set()
    for device_id in available_device_ids:
        if (not isinstance(device_id, str) or not device_id or len(device_id.encode("utf-8")) > 256
                or any(ord(char) < 32 for char in device_id)):
            return None
        known.add(device_id)
    if any(not isinstance(value, str) or not value or value not in known for value in devices_clean):
        return None
    return targets, sorted(callbacks_clean), sorted(devices_clean)


def preview_bulk_grants(
    actor_id: int, owner_id: int, target_ids, *, callbacks=(), device_ids=(),
    available_device_ids, expires_at: int | None = None, now: int | None = None,
) -> dict[str, Any] | None:
    """Return an exact, read-only preview for a bounded owner bulk grant."""
    if expires_at is not None and not _valid_expiry(expires_at):
        return None
    parsed = _validate_bulk_grant_inputs(
        actor_id, owner_id, target_ids, callbacks, device_ids, available_device_ids,
    )
    if parsed is None:
        return None
    targets, callbacks_clean, devices_clean = parsed
    current = int(now if now is not None else time.time())
    rows = []
    snapshot = []
    for target_id in targets:
        record = get_user(target_id)
        if record is None:
            return None
        state = _bulk_access_snapshot(target_id, record, current)
        snapshot.append(state)
        current_callbacks = set(state["callbacks"])
        current_devices = set(state["devices"])
        rows.append({
            **state,
            "add_callbacks": [item for item in callbacks_clean if item not in current_callbacks],
            "already_had_callbacks": [item for item in callbacks_clean if item in current_callbacks],
            "add_devices": [item for item in devices_clean if item not in current_devices],
            "already_had_devices": [item for item in devices_clean if item in current_devices],
        })
    return {
        "targets": targets, "callbacks": callbacks_clean, "devices": devices_clean,
        "rows": rows, "snapshot": snapshot, "expires_at": expires_at,
    }


def apply_bulk_grants(
    actor_id: int, owner_id: int, target_ids, *, callbacks=(), device_ids=(),
    available_device_ids, expected_snapshot, expires_at: int | None = None,
    now: int | None = None,
) -> dict[str, int] | None:
    """Apply a previously previewed grant set in one access-store write."""
    if expires_at is not None and not _valid_expiry(expires_at):
        return None
    parsed = _validate_bulk_grant_inputs(
        actor_id, owner_id, target_ids, callbacks, device_ids, available_device_ids,
    )
    if parsed is None or not isinstance(expected_snapshot, list) or len(expected_snapshot) != len(parsed[0]):
        return None
    targets, callbacks_clean, devices_clean = parsed
    current = int(now if now is not None else time.time())
    additions: list[tuple[int, str, str]] = []
    with _LOCK:
        data = _load()
        users = data.get("users")
        if not isinstance(users, dict):
            return None
        current_snapshot = []
        records = {}
        for target_id in targets:
            raw = users.get(str(target_id))
            if not isinstance(raw, dict):
                return None
            record = _safe_user(raw)
            records[target_id] = record
            current_snapshot.append(_bulk_access_snapshot(target_id, record, current))
        if current_snapshot != expected_snapshot:
            return None
        for target_id, state in zip(targets, current_snapshot):
            record = records[target_id]
            for kind, field, items in (
                ("callback", "callbacks", callbacks_clean),
                ("device", "devices", devices_clean),
            ):
                active_items = set(state[field])
                grants = set(record["permissions"][field])
                expirations = record.setdefault("permission_expirations", {})
                granted_at = record.setdefault("permission_granted_at", {})
                last_used = record.setdefault("permission_last_used_at", {})
                for item in items:
                    if item in active_items:
                        continue
                    grants.add(item)
                    key = _expiry_key(kind, item)
                    if expires_at is None:
                        expirations.pop(key, None)
                    else:
                        expirations[key] = int(expires_at)
                    granted_at[key] = current
                    last_used.pop(key, None)
                    record.setdefault("permission_expiry_reminders", {}).pop(key, None)
                    additions.append((target_id, kind, item))
                record["permissions"][field] = sorted(grants)
            users[str(target_id)] = record
        if additions:
            _save(data)
    for target_id, kind, item in additions:
        append_grant_audit(
            actor_id=int(actor_id), target_id=target_id,
            grant_kind=kind, item=item, action="granted", expires_at=expires_at,
        )
    if additions:
        append_audit(
            "bulk_grant_applied", actor_id=int(actor_id),
            detail=f"targets={len(targets)}; grants={len(additions)}; expires_at={expires_at or 'none'}",
        )
    return {
        "targets": len(targets), "grants_added": len(additions),
        "callbacks_added": sum(1 for _target, kind, _item in additions if kind == "callback"),
        "devices_added": sum(1 for _target, kind, _item in additions if kind == "device"),
    }


def preview_bulk_revocations(
    actor_id: int, owner_id: int, target_ids, *, callbacks=(), now: int | None = None,
) -> dict[str, Any] | None:
    """Read-only exact dry-run for revoking selected callbacks from up to 20 users."""
    parsed = _validate_bulk_grant_inputs(
        actor_id, owner_id, target_ids, callbacks, (), (),
    )
    if parsed is None:
        return None
    targets, callbacks_clean, _devices = parsed
    current = int(now if now is not None else time.time())
    rows = []
    snapshot = []
    for target_id in targets:
        record = get_user(target_id)
        if record is None:
            return None
        state = _bulk_access_snapshot(target_id, record, current)
        snapshot.append(state)
        current_callbacks = set(state["callbacks"])
        rows.append({
            "target_id": target_id, "role": state["role"], "blocked": state["blocked"],
            "remove_callbacks": [item for item in callbacks_clean if item in current_callbacks],
            "missing_callbacks": [item for item in callbacks_clean if item not in current_callbacks],
        })
    return {"targets": targets, "callbacks": callbacks_clean, "rows": rows, "snapshot": snapshot}


def apply_bulk_revocations(
    actor_id: int, owner_id: int, target_ids, *, callbacks=(), expected_snapshot,
    now: int | None = None,
) -> dict[str, int] | None:
    """Atomically remove only reviewed active callbacks if target snapshots are unchanged."""
    parsed = _validate_bulk_grant_inputs(
        actor_id, owner_id, target_ids, callbacks, (), (),
    )
    if parsed is None or not isinstance(expected_snapshot, list) or len(expected_snapshot) != len(parsed[0]):
        return None
    targets, callbacks_clean, _devices = parsed
    current = int(now if now is not None else time.time())
    revoked: list[tuple[int, str]] = []
    with _LOCK:
        data = _load()
        users = data.get("users")
        if not isinstance(users, dict):
            return None
        records = {}
        current_snapshot = []
        for target_id in targets:
            raw = users.get(str(target_id))
            if not isinstance(raw, dict):
                return None
            record = _safe_user(raw)
            records[target_id] = record
            current_snapshot.append(_bulk_access_snapshot(target_id, record, current))
        if current_snapshot != expected_snapshot:
            return None
        for target_id, state in zip(targets, current_snapshot):
            record = records[target_id]
            active_callbacks = set(state["callbacks"])
            permissions = record.setdefault("permissions", {}).setdefault("callbacks", [])
            for callback in callbacks_clean:
                if callback not in active_callbacks:
                    continue
                permissions = [item for item in permissions if item != callback]
                expiry_key = _expiry_key("callback", callback)
                record.setdefault("permission_expirations", {}).pop(expiry_key, None)
                record.setdefault("permission_granted_at", {}).pop(expiry_key, None)
                record.setdefault("permission_last_used_at", {}).pop(expiry_key, None)
                record.setdefault("permission_expiry_reminders", {}).pop(expiry_key, None)
                revoked.append((target_id, callback))
            record["permissions"]["callbacks"] = sorted(set(permissions))
            users[str(target_id)] = record
        if revoked:
            _save(data)
    for target_id, callback in revoked:
        append_grant_audit(
            actor_id=int(actor_id), target_id=target_id,
            grant_kind="callback", item=callback, action="revoked",
        )
    if revoked:
        append_audit(
            "bulk_revoke_applied", actor_id=int(actor_id),
            detail=f"targets={len(targets)}; grants={len(revoked)}",
        )
    return {"targets": len(targets), "grants_revoked": len(revoked)}


def preview_block_impact(actor_id: int, target_id: int, owner_id: int) -> dict[str, Any] | None:
    """Owner-only, read-only preview of the target's current access surface."""
    if int(actor_id) != int(owner_id) or int(target_id) == int(owner_id):
        return None
    record = get_user(target_id)
    if not record:
        return None
    grants = get_effective_grants(target_id)
    return {
        "target_id": int(target_id),
        "role": get_role(target_id, owner_id),
        "blocked": bool(record.get("blocked")),
        "callbacks": grants["callbacks"],
        "devices": grants["devices"],
        "will_revoke_all_access": not bool(record.get("blocked")),
    }


def preview_role_grant_diff(
    actor_id: int, target_id: int, new_role: str, owner_id: int,
    *, callbacks=None, devices=None,
) -> dict[str, Any] | None:
    """Compare current effective grants with a proposed owner configuration."""
    if (int(actor_id) != int(owner_id) or int(target_id) == int(owner_id)
            or new_role not in (Role.USER, Role.GUEST)):
        return None
    record = get_user(target_id)
    if not record:
        return None
    current = get_effective_grants(target_id)
    proposed_callbacks = sorted({str(value) for value in (callbacks if callbacks is not None else current["callbacks"]) if str(value)})
    proposed_devices = sorted({str(value) for value in (devices if devices is not None else current["devices"]) if str(value)})
    return {
        "target_id": int(target_id),
        "role_before": get_role(target_id, owner_id),
        "role_after": new_role,
        "callbacks_added": sorted(set(proposed_callbacks) - set(current["callbacks"])),
        "callbacks_removed": sorted(set(current["callbacks"]) - set(proposed_callbacks)),
        "devices_added": sorted(set(proposed_devices) - set(current["devices"])),
        "devices_removed": sorted(set(current["devices"]) - set(proposed_devices)),
    }


def preview_grant_conflicts(
    actor_id: int, target_id: int, kind: str, item: str, owner_id: int,
) -> dict[str, Any] | None:
    """Identify duplicate or semantically overlapping access before a grant change."""
    if (int(actor_id) != int(owner_id) or int(target_id) == int(owner_id)
            or not isinstance(kind, str) or kind not in {"callback", "device"} or not isinstance(item, str)
            or not item or len(item.encode("utf-8")) > 256 or any(ord(char) < 32 for char in item)):
        return None
    record = get_user(int(target_id))
    if record is None:
        return None
    grants = get_effective_grants(int(target_id))
    if kind == "device":
        existing = set(grants["devices"])
        overlaps = [item] if item in existing else []
        reason = "already_granted" if overlaps else "none"
    else:
        existing = set(grants["callbacks"])
        if item in existing:
            overlaps = [item]
            reason = "already_granted"
        elif item == "full_device":
            overlaps = sorted(
                grant for grant in existing
                if grant != "shell_request"
                and _matching_callback_grants(grant, {"full_device"}) == ["full_device"]
            )
            reason = "broader_grant_overlaps_existing" if overlaps else "none"
        else:
            overlaps = _matching_callback_grants(item, existing)
            reason = "covered_by_existing_grant" if overlaps else "none"
    return {
        "target_id": int(target_id),
        "kind": kind,
        "item": item,
        "reason": reason,
        "conflict": reason != "none",
        "overlapping_grants": sorted(set(overlaps)),
        "blocked": bool(record.get("blocked")),
    }


def preview_grant_revocation(
    actor_id: int, target_id: int, kind: str, item: str, owner_id: int,
) -> dict[str, Any] | None:
    """Return the exact currently-effective scope affected by revoking one grant."""
    if int(actor_id) != int(owner_id) or int(target_id) == int(owner_id):
        return None
    if kind not in {"device", "permission"} or not isinstance(item, str) or not item:
        return None
    record = get_user(target_id)
    if not record:
        return None
    grants = get_effective_grants(target_id)
    if kind == "device":
        if item not in grants["devices"]:
            return None
        affected_devices = [item]
        affected_callbacks = grants["callbacks"]
    else:
        if item not in grants["callbacks"]:
            return None
        affected_devices = grants["devices"]
        affected_callbacks = [item]
    return {
        "target_id": int(target_id),
        "kind": kind,
        "item": item,
        "affected_devices": sorted(affected_devices),
        "affected_callbacks": sorted(affected_callbacks),
        "blocked": bool(record.get("blocked")),
    }


def _user_grant_revocation_state(target_id: int, record: dict[str, Any], now: int) -> dict[str, Any]:
    safe = _safe_user(record)
    stored = safe["permissions"]
    expirations = safe.get("permission_expirations") or {}

    def effective(kind: str, values: list[str]) -> list[str]:
        prefix = "callback" if kind == "callbacks" else "device"
        return sorted({
            item for item in values
            if expirations.get(_expiry_key(prefix, item)) is None
            or expirations[_expiry_key(prefix, item)] > now
        })

    return {
        "target_id": int(target_id),
        "role": str(safe.get("role") or Role.GUEST),
        "blocked": bool(safe.get("blocked")),
        "stored_callbacks": sorted(stored.get("callbacks") or []),
        "stored_devices": sorted(stored.get("devices") or []),
        "callbacks": effective("callbacks", stored.get("callbacks") or []),
        "devices": effective("devices", stored.get("devices") or []),
    }


def _user_grant_pause_state(
    target_id: int, record: dict[str, Any], available_device_ids: set[str], now: int,
) -> dict[str, Any]:
    safe = _safe_user(record)
    permissions = safe["permissions"]
    expirations = safe.get("permission_expirations") or {}

    def active(kind: str, field: str) -> list[str]:
        return sorted(
            item for item in permissions.get(field, [])
            if expirations.get(_expiry_key(kind, item)) is None
            or int(expirations[_expiry_key(kind, item)]) > now
        )

    callbacks = active("callback", "callbacks")
    devices = active("device", "devices")
    available = {str(item) for item in available_device_ids if str(item)}
    return {
        "target_id": int(target_id),
        "role": str(safe.get("role") or Role.GUEST),
        "blocked": bool(safe.get("blocked")),
        "paused": bool(safe.get("permissions_paused")),
        "stored_callbacks": sorted(permissions.get("callbacks") or []),
        "stored_devices": sorted(permissions.get("devices") or []),
        "resume_callbacks": callbacks,
        "resume_devices": [item for item in devices if item in available],
        "stale_devices": [item for item in devices if item not in available],
        "permission_expirations": dict(sorted(expirations.items())),
        "available_device_ids": sorted(available),
    }


def preview_user_grant_pause(
    actor_id: int, target_id: int, owner_id: int, *,
    available_device_ids: set[str], now: int | None = None,
) -> dict[str, Any] | None:
    """Preview an owner-confirmed pause/resume without changing stored grants."""
    if (type(actor_id) is not int or type(target_id) is not int or type(owner_id) is not int
            or actor_id != owner_id or owner_id <= 0 or target_id <= 0 or target_id == owner_id
            or not isinstance(available_device_ids, (set, frozenset))):
        return None
    current = int(now if now is not None else time.time())
    with _LOCK:
        raw = _load().get("users", {}).get(str(target_id))
        if not isinstance(raw, dict):
            return None
        snapshot = _user_grant_pause_state(target_id, raw, set(available_device_ids), current)
    return {
        "target_id": target_id,
        "paused": snapshot["paused"],
        "resume_callbacks": snapshot["resume_callbacks"],
        "resume_devices": snapshot["resume_devices"],
        "stale_devices": snapshot["stale_devices"],
        "stored_callbacks": snapshot["stored_callbacks"],
        "stored_devices": snapshot["stored_devices"],
        "snapshot": snapshot,
    }


def apply_user_grant_pause(
    actor_id: int, target_id: int, owner_id: int, *, paused: bool,
    available_device_ids: set[str], expected_snapshot, now: int | None = None,
) -> dict[str, Any] | None:
    """Atomically pause or resume a user's existing grants after exact owner confirmation."""
    if (type(actor_id) is not int or type(target_id) is not int or type(owner_id) is not int
            or actor_id != owner_id or owner_id <= 0 or target_id <= 0 or target_id == owner_id
            or type(paused) is not bool or not isinstance(expected_snapshot, dict)
            or not isinstance(available_device_ids, (set, frozenset))):
        return None
    current = int(now if now is not None else time.time())
    revoked_stale_devices: list[str] = []
    with _LOCK:
        data = _load()
        users = data.get("users")
        raw = users.get(str(target_id)) if isinstance(users, dict) else None
        if not isinstance(raw, dict):
            return None
        record = _safe_user(raw)
        snapshot = _user_grant_pause_state(target_id, record, set(available_device_ids), current)
        if snapshot != expected_snapshot or bool(record.get("permissions_paused")) == paused:
            return None
        if not paused:
            revoked_stale_devices = list(snapshot["stale_devices"])
            if revoked_stale_devices:
                permissions = record.setdefault("permissions", {})
                permissions["devices"] = sorted(set(permissions.get("devices") or []) - set(revoked_stale_devices))
                for device_id in revoked_stale_devices:
                    key = _expiry_key("device", device_id)
                    for field in ("permission_expirations", "permission_granted_at", "permission_last_used_at", "permission_expiry_reminders"):
                        record.setdefault(field, {}).pop(key, None)
        record["permissions_paused"] = paused
        record["permissions_paused_at"] = current if paused else None
        users[str(target_id)] = record
        _save(data)
    action = "user_grants_paused" if paused else "user_grants_resumed"
    for device_id in revoked_stale_devices:
        expiry = snapshot["permission_expirations"].get(_expiry_key("device", device_id))
        append_grant_audit(
            actor_id=actor_id, target_id=target_id, grant_kind="device", item=device_id,
            action="revoked", expires_at=expiry,
        )
    append_audit(
        action, actor_id=actor_id, target_id=target_id,
        detail=f"callbacks={len(snapshot['stored_callbacks'])};devices={len(snapshot['stored_devices'])};"
               f"resume_callbacks={len(snapshot['resume_callbacks'])};resume_devices={len(snapshot['resume_devices'])};"
               f"stale_devices={len(snapshot['stale_devices'])}",
    )
    return {
        "paused": paused,
        "callbacks_preserved": len(snapshot["stored_callbacks"]),
        "devices_preserved": len(snapshot["stored_devices"]) - len(revoked_stale_devices),
    }


def preview_user_grant_revocation(
    actor_id: int, target_id: int, owner_id: int, *, now: int | None = None,
) -> dict[str, Any] | None:
    """Read-only owner preview of all stored and effective delegated grants for one user."""
    if (type(actor_id) is not int or type(target_id) is not int or type(owner_id) is not int
            or actor_id != owner_id or owner_id <= 0 or target_id <= 0 or target_id == owner_id):
        return None
    current = int(now if now is not None else time.time())
    with _LOCK:
        raw = _load().get("users", {}).get(str(target_id))
        if not isinstance(raw, dict):
            return None
        snapshot = _user_grant_revocation_state(target_id, raw, current)
    return {
        "target_id": target_id,
        "callbacks": snapshot["callbacks"],
        "devices": snapshot["devices"],
        "stored_callbacks": snapshot["stored_callbacks"],
        "stored_devices": snapshot["stored_devices"],
        "snapshot": snapshot,
    }


def apply_user_grant_revocation(
    actor_id: int, target_id: int, owner_id: int, *, expected_snapshot,
    now: int | None = None,
) -> dict[str, int] | None:
    """Atomically revoke every delegated callback/device grant after exact owner preview."""
    if (type(actor_id) is not int or type(target_id) is not int or type(owner_id) is not int
            or actor_id != owner_id or owner_id <= 0 or target_id <= 0 or target_id == owner_id
            or not isinstance(expected_snapshot, dict)):
        return None
    current = int(now if now is not None else time.time())
    revoked_callbacks: list[str] = []
    revoked_devices: list[str] = []
    with _LOCK:
        data = _load()
        users = data.get("users")
        raw = users.get(str(target_id)) if isinstance(users, dict) else None
        if not isinstance(raw, dict):
            return None
        record = _safe_user(raw)
        if _user_grant_revocation_state(target_id, record, current) != expected_snapshot:
            return None
        permissions = record.setdefault("permissions", {})
        revoked_callbacks = sorted(set(permissions.get("callbacks") or []))
        revoked_devices = sorted(set(permissions.get("devices") or []))
        permissions["callbacks"] = []
        permissions["devices"] = []
        record["permission_expirations"] = {}
        record["permission_granted_at"] = {}
        record["permission_last_used_at"] = {}
        record["permission_expiry_reminders"] = {}
        users[str(target_id)] = record
        _save(data)
    for callback in revoked_callbacks:
        append_grant_audit(
            actor_id=actor_id, target_id=target_id, grant_kind="callback",
            item=callback, action="revoked",
        )
    for device_id in revoked_devices:
        append_grant_audit(
            actor_id=actor_id, target_id=target_id, grant_kind="device",
            item=device_id, action="revoked",
        )
    append_audit(
        "user_all_grants_revoked", actor_id=actor_id, target_id=target_id,
        detail=f"callbacks={len(revoked_callbacks)};devices={len(revoked_devices)}",
    )
    return {"revoked_devices": len(revoked_devices), "revoked_callbacks": len(revoked_callbacks)}


def revoke_devices(device_ids, *, actor_id: int | None = None) -> int:
    """Remove device grants before a device is deleted, blocked, or uninstalled."""
    revoked = {str(device_id) for device_id in device_ids if str(device_id)}
    if not revoked:
        return 0
    changed_users = 0
    revoked_rows: list[tuple[int, str]] = []
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
            revoked_rows.extend(
                (int(record.get("id") or key), device_id)
                for device_id in sorted(current - updated)
            )
            permissions["devices"] = sorted(updated)
            expirations = record.get("permission_expirations") or {}
            granted_at = record.get("permission_granted_at") or {}
            last_used = record.get("permission_last_used_at") or {}
            for device_id in revoked:
                expiry_key = _expiry_key("device", device_id)
                expirations.pop(expiry_key, None)
                granted_at.pop(expiry_key, None)
                last_used.pop(expiry_key, None)
            record["permission_expirations"] = expirations
            record["permission_granted_at"] = granted_at
            record["permission_last_used_at"] = last_used
            users[key] = record
            changed_users += 1
        if changed_users:
            _save(data)
    if changed_users:
        for user_id, device_id in revoked_rows:
            append_grant_audit(
                actor_id=int(actor_id) if actor_id is not None else None,
                target_id=user_id, grant_kind="device", item=device_id, action="revoked",
            )
        append_audit(
            "device_permissions_revoked",
            actor_id=actor_id,
            detail=f"count={len(revoked)}; users={changed_users}",
        )
    return changed_users


def explain_callback_access(user_id: int, callback: str | None, owner_id: int,
                            selected_device: str | None = None) -> dict[str, Any]:
    """Explain the first RBAC gate without returning private grant contents."""
    role = get_role(user_id, owner_id)
    if role == Role.OWNER:
        return {"allowed": True, "reason": "owner_access"}
    data = str(callback or "")
    if data in OWNER_ONLY_CALLBACKS or any(data.startswith(prefix) for prefix in OWNER_ONLY_CALLBACK_PREFIXES):
        return {"allowed": False, "reason": "owner_only"}
    if role != Role.USER:
        return {"allowed": False, "reason": "account_blocked" if role == Role.BLOCKED else "guest_role"}
    grants = get_effective_grants(user_id)
    callbacks = set(grants["callbacks"])
    device_ids = set(grants["devices"])
    if data == "cmd:shell":
        if not selected_device or selected_device == "all":
            return {"allowed": False, "reason": "device_required"}
        if selected_device not in device_ids:
            return {"allowed": False, "reason": "device_grant_required"}
        if "shell_request" not in callbacks:
            return {"allowed": False, "reason": "shell_request_required"}
        return {"allowed": True, "reason": "granted_action"}
    if data in USER_NAVIGATION_CALLBACKS:
        return {"allowed": True, "reason": "user_navigation"}
    if data.startswith("dev:"):
        device_id = data.split(":", 1)[1]
        if not device_id or device_id == "all":
            return {"allowed": False, "reason": "invalid_device"}
        if device_id not in device_ids:
            return {"allowed": False, "reason": "device_grant_required"}
        return {"allowed": True, "reason": "device_grant"}
    # A button grant never grants a device, and a device grant never grants
    # every button. Both scopes must match the current target. Unknown/global
    # callback families fail closed even if stale permission data contains them.
    if not selected_device or selected_device not in device_ids:
        return {"allowed": False, "reason": "device_grant_required" if selected_device else "device_required"}
    is_device_action = (
        data in DEVICE_ACTION_CALLBACKS
        or any(data.startswith(prefix) for prefix in DEVICE_ACTION_PREFIXES)
    )
    if not is_device_action:
        return {"allowed": False, "reason": "action_not_allowlisted"}
    if "full_device" in callbacks or data in callbacks:
        return {"allowed": True, "reason": "granted_action"}
    if data.startswith("kill:"):
        pid = data.split(":", 1)[1]
        if pid.isdigit() and f"killq:{pid}" in callbacks:
            return {"allowed": True, "reason": "granted_action"}
    if data.startswith("proc:"):
        proc_action = data.split(":", 1)[1]
        if (proc_action == "refresh" or proc_action.isdigit()) and "proc:0" in callbacks:
            return {"allowed": True, "reason": "granted_action"}
    required_grants = CATEGORY_REQUIRED_GRANTS.get(data)
    if required_grants and callbacks.intersection(required_grants):
        return {"allowed": True, "reason": "granted_action"}
    for category_or_prefix, required_prefixes in CATEGORY_REQUIRED_GRANT_PREFIXES.items():
        if data == category_or_prefix or data.startswith(category_or_prefix):
            if any(
                grant.startswith(prefix)
                for grant in callbacks
                for prefix in required_prefixes
            ):
                return {"allowed": True, "reason": "granted_action"}
    return {"allowed": False, "reason": "action_grant_required"}


def can_use_callback(user_id: int, callback: str | None, owner_id: int,
                     selected_device: str | None = None) -> bool:
    return bool(explain_callback_access(user_id, callback, owner_id, selected_device)["allowed"])
