"""Подпись и проверка сообщений HMAC-SHA256 с окном воспроизведения."""

import collections
import hashlib
import hmac
import json
import math
import re
import threading
import time
import uuid
from typing import Any, Dict, Optional

from config import SHARED_KEY

MAX_AGE = 120  # секунд (anti-replay одно окно: сообщения старше этого окна считаются недействительными
_SEEN_NONCES_MAX = 2048  # максимум запомненных nonce (кольцевой буфер)
_MAX_DIAGNOSTIC_DELTA_SECONDS = 10 * 365 * 24 * 60 * 60

_seen = collections.deque()
_seen_set = set()
_seen_lock = threading.Lock()


def _mark_seen(nonce: str, ts: float) -> bool:
    """Пометить nonce как использованный. Возвращает False, если nonce уже был."""
    with _seen_lock:
        # Выкидывать устаревшие (старше окна воспроизведения) из памяти, чтобы не росло.

        cutoff = time.time() - MAX_AGE
        # Messages may arrive out of timestamp order after reconnect/retry, so
        # purge every expired nonce instead of assuming the deque is time-sorted.
        if any(ts < cutoff for _, ts in _seen):
            retained = [entry for entry in _seen if entry[1] >= cutoff]
            _seen.clear()
            _seen.extend(retained)
            _seen_set.intersection_update(nonce for nonce, _ in retained)
        if nonce in _seen_set:
            return False
        # Fail closed at capacity: evicting a still-fresh nonce would reopen its
        # replay window. The cache is bounded by the per-process hard limit.
        if len(_seen) >= _SEEN_NONCES_MAX:
            return False
        _seen.append((nonce, ts))
        _seen_set.add(nonce)
        return True


def sign_message(payload: Dict[str, Any], *, key: str | None = None) -> Dict[str, Any]:
    """Возвращает конверт {"sig": ..., "payload": {...}} с меткой времени ts."""
    payload = dict(payload)
    payload["ts"] = int(time.time())
    payload["nonce"] = uuid.uuid4().hex
    body = _canonical(payload)
    signature = hmac.new((SHARED_KEY if key is None else key).encode("utf-8"), body, hashlib.sha256).hexdigest()
    return {"sig": signature, "payload": payload}


def timestamp_delta_seconds(envelope: Dict[str, Any], *, now: float | None = None) -> int | None:
    """Return bounded receive-time minus signed-time seconds for safe diagnostics.

    Positive means the signed timestamp is older than the receiver clock; negative
    means it is ahead. This diagnostic does not affect signature/freshness policy.
    """
    payload = envelope.get("payload") if isinstance(envelope, dict) else None
    ts = payload.get("ts") if isinstance(payload, dict) else None
    if type(ts) not in (int, float):
        return None
    try:
        signed_time = float(ts)
        received_time = time.time() if now is None else float(now)
    except (OverflowError, TypeError, ValueError):
        return None
    if not math.isfinite(signed_time) or not math.isfinite(received_time):
        return None
    delta = int(round(received_time - signed_time))
    return max(-_MAX_DIAGNOSTIC_DELTA_SECONDS, min(_MAX_DIAGNOSTIC_DELTA_SECONDS, delta))


def verify_message(envelope: Dict[str, Any], *, key: str | None = None) -> Optional[Dict[str, Any]]:
    """Проверяет подпись и свежесть сообщения.

    Возвращает payload при успешной проверке, иначе None.
    """
    payload, _reason = verify_message_detailed(envelope, key=key)
    return payload


def verify_message_detailed(
    envelope: Dict[str, Any],
    *,
    key: str | None = None,
) -> tuple[Optional[Dict[str, Any]], str]:
    """Validate an MQTT envelope and return a safe diagnostic reason.

    The reason is intended for local logs only; it never includes payload,
    signature, or key material.
    """
    if not isinstance(envelope, dict):
        return None, "invalid_envelope"
    signature = envelope.get("sig")
    payload = envelope.get("payload")
    if not isinstance(signature, str) or not isinstance(payload, dict):
        return None, "invalid_envelope"
    expected = hmac.new((SHARED_KEY if key is None else key).encode("utf-8"), _canonical(payload), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature):
        return None, "bad_hmac"
    ts = payload.get("ts")
    if type(ts) not in (int, float):
        return None, "invalid_timestamp"
    try:
        finite_timestamp = math.isfinite(float(ts))
    except (OverflowError, ValueError):
        finite_timestamp = False
    if not finite_timestamp:
        return None, "invalid_timestamp"
    # Старый timestamp допустим только у подписанного LWT с токеном сессии;
    # bot.py сверяет его с последним принятым online-сеансом устройства.
    is_lwt = (
        payload.get("type") == "status"
        and str(payload.get("status")).lower() == "offline"
        and isinstance(payload.get("mqtt_session_id"), str)
        and re.fullmatch(r"[0-9a-f]{32}", payload["mqtt_session_id"]) is not None
        and ts <= time.time() + MAX_AGE
    )
    if not is_lwt:
        if abs(time.time() - ts) > MAX_AGE:
            return None, "stale_timestamp"
        nonce = payload.get("nonce")
        if not isinstance(nonce, str) or not nonce:
            return None, "missing_nonce"
        if not _mark_seen(nonce, ts):
            log_replay_warning(nonce, ts)
            return None, "replay"
    return payload, "ok"


def log_replay_warning(nonce: str, ts: float) -> None:
    """Заглушка для логирования replay-попыток (без спама в stdout)."""
    import logging
    logging.getLogger("xgent.crypto").warning("Rejected replay nonce %s (ts=%s", nonce, ts)


def _canonical(payload: Dict[str, Any]) -> bytes:
    """Каноничный JSON для подписи: сортировка ключей, без лишних пробелов."""
    return json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
