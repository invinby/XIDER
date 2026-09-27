"""Remember one editable X-STAB navigation card per chat and user."""

from __future__ import annotations

import json
import threading
from pathlib import Path


FILE = Path(__file__).resolve().parent / ".ui_cards.json"
_lock = threading.Lock()


def _key(chat_id: int, user_id: int) -> str:
    return f"{int(chat_id)}:{int(user_id)}"


def _load() -> dict:
    try:
        data = json.loads(FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def get(chat_id: int, user_id: int) -> int | None:
    with _lock:
        value = _load().get(_key(chat_id, user_id))
    return value if isinstance(value, int) and value > 0 else None


def set_card(chat_id: int, user_id: int, message_id: int) -> None:
    if int(message_id) <= 0:
        raise ValueError("message_id must be positive")
    with _lock:
        data = _load()
        key = _key(chat_id, user_id)
        if data.get(key) == int(message_id):
            return
        data[key] = int(message_id)
        # Bound the local index; stale entries fall back to a new card.
        if len(data) > 2000:
            data = dict(list(data.items())[-1000:])
        tmp = FILE.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        tmp.replace(FILE)
