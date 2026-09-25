"""Настройки бота (JSON-файл рядом с bot.py): флаги уведомлений и т.п."""

import json
import re
import threading
import time
from pathlib import Path

FILE = Path(__file__).resolve().parent / "bot_settings.json"
_lock = threading.Lock()
_defaults = {
    "developer_contact": "@a9m6u",
    "notify_online": True,
    "notify_offline": True,
    "notify_battery_low": True,
    "quiet_from": "",
    "quiet_to": "",
    "report_hour": "",
    "admins": [],
    "blocked_ids": [],
    "ui_style": "technical",
    "require_device_approval": True,
}


def normalize_contact(value: str) -> str:
    value = value.strip().removeprefix("https://t.me/").removeprefix("@").rstrip("/")
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{4,31}", value):
        raise ValueError("Нужен Telegram username: 5–32 символа, латиница, цифры и _, первая — буква.")
    return "@" + value


def developer_contact() -> str:
    try:
        return normalize_contact(str(get("developer_contact", "@a9m6u")))
    except ValueError:
        return "@a9m6u"


def _load() -> dict:
    try:
        data = json.loads(FILE.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    return {}


def all_settings() -> dict:
    with _lock:
        return {**_defaults, **_load()}


def get(key: str, default=None):
    return all_settings().get(key, default)


def set_key(key: str, value) -> None:
    with _lock:
        data = _load()
        data[key] = value
        tmp = FILE.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        tmp.replace(FILE)


def toggle(key: str) -> bool:
    """Инвертировать флаг. Возвращает новое значение."""
    new_val = not bool(all_settings().get(key, _defaults.get(key, False)))
    set_key(key, new_val)
    return new_val


def quiet_active(hour: int | None = None) -> bool:
    """Включены ли «тихие часы» сейчас (или в заданный час).

    Окно задаётся как quiet_from/quiet_to (0-23). Если from <= to — обычное
    окно в течение суток, иначе — пересекающее полночь.
    """
    fr = str(get("quiet_from", "") or "").strip()
    to = str(get("quiet_to", "") or "").strip()
    if not fr or not to:
        return False
    try:
        h = int(hour) if hour is not None else int(time.strftime("%H"))
        f = int(fr)
        t = int(to)
    except (TypeError, ValueError):
        return False
    if f <= t:
        return f <= h < t
    return h >= f or h < t


def is_blocked(device_id: str) -> bool:
    """Заблокировано ли устройство (не показывать / не принимать статусы)."""
    blocked = [str(x) for x in (get("blocked_ids") or [])]
    return device_id in blocked


def block(device_id: str) -> bool:
    """Добавить устройство в блок-лист. True если добавлено (не было)."""
    blocked = [str(x) for x in (get("blocked_ids") or [])]
    if device_id in blocked:
        return False
    blocked.append(device_id)
    set_key("blocked_ids", blocked)
    return True


def unblock(device_id: str) -> bool:
    """Убрать устройство из блок-листа. True если было заблокировано."""
    blocked = [str(x) for x in (get("blocked_ids") or [])]
    if device_id not in blocked:
        return False
    blocked = [x for x in blocked if x != device_id]
    set_key("blocked_ids", blocked)
    return True


def add_admin(user_id: int) -> bool:
    """Добавить второго администратора. True если добавлен (не было)."""
    admins = [int(x) for x in (get("admins") or []) if str(x).strip().isdigit()]
    if user_id in admins:
        return False
    admins.append(int(user_id))
    set_key("admins", admins)
    return True


def remove_admin(user_id: int) -> bool:
    """Убрать администратора. True если был."""
    admins = [int(x) for x in (get("admins") or []) if str(x).strip().isdigit()]
    if user_id not in admins:
        return False
    admins = [x for x in admins if x != int(user_id)]
    set_key("admins", admins)
    return True
