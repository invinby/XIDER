"""Editable, non-secret bot copy used by the owner-facing admin panel."""

from __future__ import annotations

import json
import threading
from pathlib import Path

FILE = Path(__file__).resolve().parent / "bot_texts.json"
_lock = threading.Lock()
DEFAULTS = {
    "start_guest": "XIDER: доступ для просмотра. Управляющие действия недоступны, пока владелец не выдаст права.",
    "start_user": "XIDER: доступны только выданные тебе устройства и кнопки.",
    "start_owner": "XIDER: панель управления готова.",
    "blocked": "Доступ для этого аккаунта заблокирован.",
    "custom_start_guest": "Залёт разрешён: смотреть можно, кнопки управления пока под замком.",
    "custom_start_user": "Доступ выдан: жми разрешённые кнопки и не ломай то, что работает.",
    "custom_start_owner": "Панель хозяина на месте. Сервер жив, устройства ждут приказов.",
    "custom_blocked": "Стоп-машина: этот аккаунт заблокирован владельцем.",
}


def _load() -> dict:
    try:
        value = json.loads(FILE.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def all_texts() -> dict:
    with _lock:
        return {**DEFAULTS, **_load()}


def get(key: str) -> str:
    return str(all_texts().get(key, DEFAULTS.get(key, "")))


def set_text(key: str, value: str) -> None:
    if key not in DEFAULTS:
        raise KeyError(key)
    value = str(value).strip()
    if not value or len(value) > 1000:
        raise ValueError("Текст должен быть от 1 до 1000 символов.")
    with _lock:
        data = _load()
        data[key] = value
        tmp = FILE.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(FILE)
