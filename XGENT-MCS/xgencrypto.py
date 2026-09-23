"""AES-256-GCM шифрование payload поверх HMAC-подписи.

Включается ENCRYPT_PAYLOAD=true (должно быть одинаково во всех компонентах).
Ключ шифрования выводится из SHARED_KEY через HKDF-SHA256 с отдельным
контекстом — никакого самопального крипто, только `cryptography.AESGCM`.

Формат конверта (id = тот же, что в crypto.py):
  {"sig": ..., "ts": ..., "nonce": ..., "enc": base64(nonce_iv || ct || tag)}
Тогда внешние поля (ts/nonce) проверяются как раньше, а сам payload лежит
внутри `enc` и виден только тем, у кого есть SHARED_KEY.

ВАЖНО: этот файл намеренно идентичен во всех трёх компонентах
(TG-BOT-SERVER, XGENT-MCS, XGENT-WDS) — меняйте синхронно.
"""

import base64
import json
import os
from typing import Any, Dict, Optional

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from config import SHARED_KEY  # noqa: F401  (загружает .env fail-closed)

_CTX = b"xgent-payload-v1"
_IV_LEN = 12


def _key() -> bytes:
    """32-байтовый ключ AES-GCM, производный от SHARED_KEY через HKDF-SHA256."""
    hkdf = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=None,
        info=_CTX,
    )
    return hkdf.derive(SHARED_KEY.encode("utf-8"))


def encrypt_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Шифрует словарь в {"enc": base64(iv + ct + tag)}."""
    raw = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    iv = os.urandom(_IV_LEN)
    ct = AESGCM(_key()).encrypt(iv, raw, None)
    return {"enc": base64.b64encode(iv + ct).decode("ascii")}


def decrypt_payload(envelope: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Расшифровывает {"enc": ...} обратно в словарь.

    Возвращает None, если шифротекст повреждён, подделан или ключ не подходит.
    """
    enc = envelope.get("enc")
    if not isinstance(enc, str):
        return None
    try:
        blob = base64.b64decode(enc)
        iv, ct = blob[:_IV_LEN], blob[_IV_LEN:]
        raw = AESGCM(_key()).decrypt(iv, ct, None)
        data = json.loads(raw.decode("utf-8"))
        return data if isinstance(data, dict) else None
    except Exception:
        return None