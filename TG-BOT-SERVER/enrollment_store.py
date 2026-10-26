"""One-use owner-approved X-DOCK pairing requests.

The HTTP layer supplies an opaque source tag for throttling. No MQTT or bot
credentials enter a pending request; the final response is sealed to a device
generated X25519 public key before being stored.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import sqlite3
import time
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF


PENDING_SECONDS = 600
DELIVERY_SECONDS = 1200
_REQUEST_ID = re.compile(r"[0-9a-f]{32}\Z")
_LABEL = re.compile(r"[\w .()_-]{1,64}\Z", re.UNICODE)
_FIELDS = frozenset({"request_id", "device_id", "platform", "label", "signing_key", "encryption_key", "proof"})


def _decode(value: str, length: int) -> bytes:
    if not isinstance(value, str) or len(value) > 256 or not re.fullmatch(r"[A-Za-z0-9_-]+", value):
        raise ValueError("invalid public request encoding")
    raw = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    if len(raw) != length:
        raise ValueError("invalid public request length")
    return raw


def _encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _canonical(data: dict[str, Any]) -> bytes:
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def validate_request(request: dict[str, Any]) -> tuple[bytes, bytes, str]:
    if not isinstance(request, dict) or set(request) != _FIELDS:
        raise ValueError("invalid enrollment fields")
    request_id, device_id, platform, label = (
        request["request_id"], request["device_id"], request["platform"], request["label"]
    )
    if not isinstance(request_id, str) or not _REQUEST_ID.fullmatch(request_id):
        raise ValueError("invalid request ID")
    if not isinstance(device_id, str) or not re.fullmatch(r"[0-9a-f]{12}", device_id):
        raise ValueError("invalid device ID")
    if platform not in ("windows", "macos") or not isinstance(label, str) or not _LABEL.fullmatch(label):
        raise ValueError("invalid platform or device label")
    signing_key = _decode(request["signing_key"], 32)
    encryption_key = _decode(request["encryption_key"], 32)
    proof = _decode(request["proof"], 64)
    signed = {field: request[field] for field in sorted(_FIELDS - {"proof"})}
    try:
        Ed25519PublicKey.from_public_bytes(signing_key).verify(proof, _canonical(signed))
        X25519PublicKey.from_public_bytes(encryption_key)
    except (InvalidSignature, ValueError) as exc:
        raise ValueError("request possession proof failed") from exc
    return signing_key, encryption_key, hashlib.sha256(signing_key).hexdigest()[:16]


def seal_response(request_id: str, fingerprint: str, public_key: bytes, settings: dict[str, Any]) -> dict[str, str]:
    """Encrypt provisioned settings to the device public key with fresh ECDH."""
    if not _REQUEST_ID.fullmatch(request_id):
        raise ValueError("invalid request ID")
    ephemeral = X25519PrivateKey.generate()
    shared = ephemeral.exchange(X25519PublicKey.from_public_bytes(public_key))
    key = HKDF(algorithm=hashes.SHA256(), length=32, salt=request_id.encode(), info=b"X-DOCK-response-v1").derive(shared)
    nonce = os.urandom(12)
    aad = f"{request_id}:{fingerprint}".encode("ascii")
    ciphertext = AESGCM(key).encrypt(nonce, _canonical(settings), aad)
    return {
        "ephemeral_key": _encode(ephemeral.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)),
        "nonce": _encode(nonce),
        "ciphertext": _encode(ciphertext),
    }


class EnrollmentStore:
    def __init__(self, path: Path, *, clock=time.time):
        self.path = Path(path)
        self.clock = clock
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS requests (
                    request_id TEXT PRIMARY KEY, device_id TEXT NOT NULL,
                    platform TEXT NOT NULL, label TEXT NOT NULL,
                    signing_key BLOB NOT NULL, encryption_key BLOB NOT NULL,
                    fingerprint TEXT NOT NULL, source_tag TEXT NOT NULL,
                    created_at INTEGER NOT NULL, expires_at INTEGER NOT NULL,
                    state TEXT NOT NULL, owner_id INTEGER, sealed_reply TEXT,
                    delivered_at INTEGER
                );
                CREATE TABLE IF NOT EXISTS poll_nonces (
                    request_id TEXT NOT NULL, nonce TEXT NOT NULL,
                    PRIMARY KEY(request_id, nonce)
                );
            """)
        if os.name == "posix":
            os.chmod(self.path, 0o600)

    def _connect(self):
        db = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        db.row_factory = sqlite3.Row
        return db

    def submit(self, request: dict[str, Any], source_tag: str) -> dict[str, Any]:
        signing, encryption, fingerprint = validate_request(request)
        if not isinstance(source_tag, str) or not re.fullmatch(r"[0-9a-f]{64}", source_tag):
            raise ValueError("invalid source tag")
        now = int(self.clock())
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            recent = db.execute(
                "SELECT COUNT(*) FROM requests WHERE source_tag=? AND created_at>?",
                (source_tag, now - 600),
            ).fetchone()[0]
            if recent >= 5:
                raise ValueError("enrollment rate limit reached")
            try:
                db.execute(
                    "INSERT INTO requests VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', NULL, NULL, NULL)",
                    (request["request_id"], request["device_id"], request["platform"], request["label"], signing,
                     encryption, fingerprint, source_tag, now, now + PENDING_SECONDS),
                )
            except sqlite3.IntegrityError as exc:
                raise ValueError("duplicate enrollment request") from exc
        return {"request_id": request["request_id"], "fingerprint": fingerprint, "expires_at": now + PENDING_SECONDS}

    def pending(self) -> list[dict[str, Any]]:
        now = int(self.clock())
        with self._connect() as db:
            rows = db.execute(
                "SELECT request_id, device_id, platform, label, fingerprint, expires_at FROM requests "
                "WHERE state='pending' AND expires_at>? ORDER BY created_at LIMIT 30", (now,),
            ).fetchall()
        return [dict(row) for row in rows]

    def decide(self, request_id: str, owner_id: int, expected_owner_id: int, *, approve: bool) -> bool:
        if not _REQUEST_ID.fullmatch(request_id) or type(owner_id) is not int or owner_id <= 0 or owner_id != expected_owner_id:
            return False
        now = int(self.clock())
        with self._connect() as db:
            result = db.execute(
                "UPDATE requests SET state=?, owner_id=? WHERE request_id=? AND state='pending' AND expires_at>?",
                ("approved" if approve else "denied", owner_id, request_id, now),
            )
            return result.rowcount == 1

    def approved(self, request_id: str) -> dict[str, Any] | None:
        if not _REQUEST_ID.fullmatch(request_id):
            return None
        with self._connect() as db:
            row = db.execute(
                "SELECT request_id, device_id, platform, label, encryption_key, fingerprint FROM requests "
                "WHERE request_id=? AND state='approved' AND expires_at>?",
                (request_id, int(self.clock())),
            ).fetchone()
        return dict(row) if row else None

    def claim_approved(self, request_id: str) -> dict[str, Any] | None:
        """Atomically claim one approval so duplicate bot callbacks cannot issue twice."""
        if not _REQUEST_ID.fullmatch(request_id):
            return None
        now = int(self.clock())
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            result = db.execute(
                "UPDATE requests SET state='provisioning' "
                "WHERE request_id=? AND state='approved' AND expires_at>?",
                (request_id, now),
            )
            if result.rowcount != 1:
                return None
            row = db.execute(
                "SELECT request_id, device_id, platform, label, encryption_key, fingerprint "
                "FROM requests WHERE request_id=? AND state='provisioning'",
                (request_id,),
            ).fetchone()
            return dict(row) if row else None

    def release_claim(self, request_id: str) -> bool:
        """Return a failed provisioning attempt to the approved retry state."""
        if not _REQUEST_ID.fullmatch(request_id):
            return False
        with self._connect() as db:
            result = db.execute(
                "UPDATE requests SET state='approved' WHERE request_id=? AND state='provisioning'",
                (request_id,),
            )
            return result.rowcount == 1

    def set_sealed_reply(self, request_id: str, sealed: dict[str, str]) -> bool:
        if not _REQUEST_ID.fullmatch(request_id) or set(sealed) != {"ephemeral_key", "nonce", "ciphertext"}:
            raise ValueError("invalid sealed response")
        now = int(self.clock())
        with self._connect() as db:
            result = db.execute(
                "UPDATE requests SET state='ready', sealed_reply=?, expires_at=? "
                "WHERE request_id=? AND state='provisioning' AND expires_at>?",
                (_canonical(sealed).decode("utf-8"), now + DELIVERY_SECONDS, request_id, now),
            )
            return result.rowcount == 1

    def poll(self, request_id: str, nonce: str, signature: str) -> dict[str, Any]:
        if not _REQUEST_ID.fullmatch(request_id) or not re.fullmatch(r"[0-9a-f]{32}", nonce):
            raise ValueError("invalid poll request")
        proof = _decode(signature, 64)
        now = int(self.clock())
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM requests WHERE request_id=?", (request_id,)).fetchone()
            if row is None or row["expires_at"] <= now:
                return {"state": "expired"}
            try:
                Ed25519PublicKey.from_public_bytes(row["signing_key"]).verify(
                    proof, _canonical({"request_id": request_id, "nonce": nonce})
                )
            except InvalidSignature as exc:
                raise ValueError("poll possession proof failed") from exc
            try:
                db.execute("INSERT INTO poll_nonces VALUES (?, ?)", (request_id, nonce))
            except sqlite3.IntegrityError as exc:
                raise ValueError("poll nonce reused") from exc
            if row["state"] != "ready":
                return {"state": row["state"]}
            result = db.execute(
                "UPDATE requests SET state='delivered', delivered_at=?, sealed_reply=NULL "
                "WHERE request_id=? AND state='ready'", (now, request_id),
            )
            if result.rowcount != 1:
                return {"state": "delivered"}
            return {"state": "ready", "sealed": json.loads(row["sealed_reply"])}
