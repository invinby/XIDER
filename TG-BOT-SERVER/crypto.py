"""Подпись и проверка сообщений HMAC-SHA256 с окном воспроизведения."""

import collections
import hashlib
import hmac
import json
import threading
import time
import uuid
from typing import Any, Dict, Optional

from config import SHARED_KEY

MAX_AGE = 120  # секунд (anti-replay одно окно: сообщения старше этого окна считаются недействительными
_SEEN_NONCES_MAX = 2048  # максимум запомненных nonce (кольцевой буфер)

_seen = collections.deque(maxlen=_SEEN_NONCES_MAX)
_seen_set = set()
_seen_lock = threading.Lock()


def _mark_seen(nonce: str, ts: float) -> bool:
    """Пометить nonce как использованный. Возвращает False, если nonce уже был."""
    with _seen_lock:
        # Выкидывать устаревшие (старше окна воспроизведения) из памяти, чтобы не росло.

        cutoff = time.time() - MAX_AGE
        while _seen and _seen[0][1] < cutoff:
            old = _seen.popleft()
            _seen_set.discard(old[0])
        if nonce in _seen_set:
            return False
        _seen.append((nonce, ts))
        _seen_set.add(nonce)
        return True


def sign_message(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Возвращает конверт {"sig": ..., "payload": {...}} с меткой времени ts."""
    payload = dict(payload)
    payload["ts"] = int(time.time())
    payload["nonce"] = uuid.uuid4().hex
    body = _canonical(payload)
    signature = hmac.new(SHARED_KEY.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return {"sig": signature, "payload": payload}


def verify_message(envelope: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Проверяет подпись и свежесть сообщения.

    Возвращает payload при успешной проверке, иначе None.
    """
    signature = envelope.get("sig")
    payload = envelope.get("payload")
    if not isinstance(signature, str) or not isinstance(payload, dict):
        return None
    expected = hmac.new(SHARED_KEY.encode("utf-8"), _canonical(payload), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature):
        return None
    ts = payload.get("ts")
    if not isinstance(ts, (int, float)):
        return None
    # LWT (Last Will and Testament) оффлайн-статус отправляется брокером при обрыве связи,
    # поэтому его ts может быть старше MAX_AGE. Подпись HMAC при этом проверяется всегда.
    is_lwt = (payload.get("type") == "status" and str(payload.get("status")).lower() == "offline")
    if not is_lwt:
        if abs(time.time() - ts) > MAX_AGE:
            return None
        nonce = payload.get("nonce")
        if not isinstance(nonce, str) or not nonce:
            return None
        if not _mark_seen(nonce, ts):
            log_replay_warning(nonce, ts)
            return None
    return payload


def log_replay_warning(nonce: str, ts: float) -> None:
    """Заглушка для логирования replay-попыток (без спама в stdout)."""
    import logging
    logging.getLogger("xgent.crypto").warning("Rejected replay nonce %s (ts=%s", nonce, ts)


def _canonical(payload: Dict[str, Any]) -> bytes:
    """Каноничный JSON для подписи: сортировка ключей, без лишних пробелов."""
    return json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
