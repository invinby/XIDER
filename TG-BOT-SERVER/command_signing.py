"""Server-side Ed25519 contract for one-time approved device commands.

Schema 1 signs exactly the nine payload fields below. The command itself is
kept outside the envelope and is bound by its exact UTF-8 SHA-256 digest.
The server private key is read from its provisioned path; this module never
creates, rotates, exports, or logs key material.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)


_PRIVATE_KEY_PATH = Path("/etc/xider/command-signing-private.pem")
_KEYSET_ENV = "XIDER_COMMAND_VERIFY_KEYSET_B64"
_MAX_COMMAND_BYTES = 4096
_MAX_DEVICE_ID_BYTES = 256
_MAX_REQUEST_ID_CHARS = 128
_MAX_KEYSET_BYTES = 512
_MAX_TRUSTED_KEYS = 2
_MAX_TTL_SECONDS = 120
_MAX_SAFE_INTEGER = (1 << 63) - 1
_KEY_ID_RE = re.compile(r"^[0-9a-f]{16}$")
_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_NONCE_RE = re.compile(r"^[A-Za-z0-9_-]{43}$")
_HEX_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
_PAYLOAD_FIELDS = frozenset(
    {
        "key_id",
        "request_id",
        "requester_id",
        "approver_id",
        "device_id",
        "command_hash",
        "issued_at",
        "expires_at",
        "nonce",
    }
)
_ENVELOPE_FIELDS = _PAYLOAD_FIELDS | {"signature"}


class CommandSigningUnavailable(RuntimeError):
    """The provisioned server signing key is missing or invalid."""


def _b64url_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _b64url_decode(value: object, *, expected_length: int | None = None) -> bytes:
    if not isinstance(value, str) or not value or not value.isascii():
        raise ValueError("invalid base64url value")
    if any(char not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-" for char in value):
        raise ValueError("invalid base64url value")
    if expected_length is not None and len(value) != (expected_length * 8 + 5) // 6:
        raise ValueError("invalid base64url length")
    if len(value) % 4 == 1:
        raise ValueError("invalid base64url value")
    try:
        decoded = base64.urlsafe_b64decode(value + "=" * ((-len(value)) % 4))
    except (binascii.Error, ValueError) as exc:
        raise ValueError("invalid base64url value") from exc
    if _b64url_encode(decoded) != value:
        raise ValueError("non-canonical base64url value")
    if expected_length is not None and len(decoded) != expected_length:
        raise ValueError("invalid base64url length")
    return decoded


def _canonical_json_bytes(value: dict[str, Any]) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise ValueError("approved-command payload is not canonical JSON") from exc


def _key_id(public_bytes: bytes) -> str:
    if type(public_bytes) is not bytes or len(public_bytes) != 32:
        raise ValueError("invalid Ed25519 public key")
    return hashlib.sha256(public_bytes).hexdigest()[:16]


def _validate_text(
    value: object,
    *,
    maximum_bytes: int,
    reject_control: bool = False,
) -> str:
    if not isinstance(value, str):
        raise ValueError("invalid approved-command text field")
    try:
        encoded = value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ValueError("invalid approved-command text field") from exc
    if not encoded or len(encoded) > maximum_bytes or not value.strip():
        raise ValueError("invalid approved-command text field")
    if reject_control and any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ValueError("invalid approved-command text field")
    return value


def _validate_request_id(value: object) -> str:
    if not isinstance(value, str) or len(value) > _MAX_REQUEST_ID_CHARS or not _REQUEST_ID_RE.fullmatch(value):
        raise ValueError("invalid request_id")
    return value


def _validate_nonce(value: object) -> str:
    if not isinstance(value, str) or not _NONCE_RE.fullmatch(value):
        raise ValueError("invalid nonce")
    # The pending-command model uses token_urlsafe(32): canonical encoding of
    # exactly 32 random bytes, represented by 43 unpadded base64url chars.
    _b64url_decode(value, expected_length=32)
    return value


def _validate_id(value: object, name: str) -> int:
    if type(value) is not int or not 1 <= value <= _MAX_SAFE_INTEGER:
        raise ValueError(f"invalid {name}")
    return value


def _validate_timestamp(value: object, name: str) -> int:
    if type(value) is not int or not 0 <= value <= _MAX_SAFE_INTEGER:
        raise ValueError(f"invalid {name}")
    return value


def _validate_payload_fields(
    *,
    key_id: object,
    request_id: object,
    requester_id: object,
    approver_id: object,
    device_id: object,
    command_hash: object,
    issued_at: object,
    expires_at: object,
    nonce: object,
) -> dict[str, Any]:
    if not isinstance(key_id, str) or not _KEY_ID_RE.fullmatch(key_id):
        raise ValueError("invalid key_id")
    if not isinstance(command_hash, str) or not _HEX_DIGEST_RE.fullmatch(command_hash):
        raise ValueError("invalid command_hash")
    issued = _validate_timestamp(issued_at, "issued_at")
    expires = _validate_timestamp(expires_at, "expires_at")
    if not 0 < expires - issued <= _MAX_TTL_SECONDS:
        raise ValueError("invalid approval lifetime")
    return {
        "key_id": key_id,
        "request_id": _validate_request_id(request_id),
        "requester_id": _validate_id(requester_id, "requester_id"),
        "approver_id": _validate_id(approver_id, "approver_id"),
        "device_id": _validate_text(
            device_id,
            maximum_bytes=_MAX_DEVICE_ID_BYTES,
            reject_control=True,
        ),
        "command_hash": command_hash,
        "issued_at": issued,
        "expires_at": expires,
        "nonce": _validate_nonce(nonce),
    }


def _load_private_key() -> Ed25519PrivateKey:
    """Load the already-provisioned server key; never create a replacement."""
    try:
        key_bytes = _PRIVATE_KEY_PATH.read_bytes()
        key = serialization.load_pem_private_key(key_bytes, password=None)
    except (OSError, TypeError, ValueError):
        raise CommandSigningUnavailable("server command signing key is unavailable or invalid") from None
    if not isinstance(key, Ed25519PrivateKey):
        raise CommandSigningUnavailable("server command signing key is unavailable or invalid")
    return key


def _decode_keyset() -> dict[str, bytes]:
    encoded = os.environ.get(_KEYSET_ENV)
    if not isinstance(encoded, str) or not encoded or len(encoded) > _MAX_KEYSET_BYTES * 2:
        raise ValueError("command verification keyset is unavailable")
    raw_json = _b64url_decode(encoded)
    if len(raw_json) > _MAX_KEYSET_BYTES:
        raise ValueError("command verification keyset is too large")

    def reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate key in command verification keyset")
            result[key] = value
        return result

    try:
        parsed = json.loads(raw_json.decode("utf-8"), object_pairs_hook=reject_duplicate_keys)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("command verification keyset is malformed") from exc
    if type(parsed) is not dict or not 1 <= len(parsed) <= _MAX_TRUSTED_KEYS:
        raise ValueError("command verification keyset has an invalid size")
    if _canonical_json_bytes(parsed) != raw_json:
        raise ValueError("command verification keyset is not canonical")

    keys: dict[str, bytes] = {}
    for key_id, encoded_public in parsed.items():
        if not isinstance(key_id, str) or not _KEY_ID_RE.fullmatch(key_id):
            raise ValueError("command verification keyset has an invalid key id")
        public_bytes = _b64url_decode(encoded_public, expected_length=32)
        if _key_id(public_bytes) != key_id:
            raise ValueError("command verification key id does not match its public key")
        keys[key_id] = public_bytes
    return keys


def sign_approved_command(
    *,
    request_id: str,
    requester_id: int,
    approver_id: int,
    device_id: str,
    command: str,
    issued_at: int,
    expires_at: int,
    nonce: str,
    key_id: str,
) -> dict:
    """Sign one owner-approved request using the provisioned server key.

    `command` is represented only by its SHA-256 digest in the returned
    envelope; callers carry the exact command text separately over transport.
    """
    try:
        command_text = _validate_text(command, maximum_bytes=_MAX_COMMAND_BYTES)
        command_hash = hashlib.sha256(command_text.encode("utf-8")).hexdigest()
        payload = _validate_payload_fields(
            key_id=key_id,
            request_id=request_id,
            requester_id=requester_id,
            approver_id=approver_id,
            device_id=device_id,
            command_hash=command_hash,
            issued_at=issued_at,
            expires_at=expires_at,
            nonce=nonce,
        )
    except (TypeError, ValueError, UnicodeError):
        raise ValueError("invalid approved-command signing fields") from None

    private_key = _load_private_key()
    public_bytes = private_key.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )
    if _key_id(public_bytes) != key_id:
        raise CommandSigningUnavailable("server command signing key does not match key_id")

    try:
        signature = private_key.sign(_canonical_json_bytes(payload))
    except (TypeError, ValueError):
        raise CommandSigningUnavailable("server command signing operation failed") from None
    return {**payload, "signature": _b64url_encode(signature)}


def verify_approved_command(
    envelope: dict,
    *,
    command: str,
    expected_device_id: str,
    now: int,
) -> dict | None:
    """Verify schema-1 approval; every malformed or untrusted input fails closed."""
    try:
        if type(envelope) is not dict or set(envelope) != _ENVELOPE_FIELDS:
            return None
        command_text = _validate_text(command, maximum_bytes=_MAX_COMMAND_BYTES)
        expected_device = _validate_text(
            expected_device_id,
            maximum_bytes=_MAX_DEVICE_ID_BYTES,
            reject_control=True,
        )
        current_time = _validate_timestamp(now, "now")
        payload = _validate_payload_fields(
            key_id=envelope["key_id"],
            request_id=envelope["request_id"],
            requester_id=envelope["requester_id"],
            approver_id=envelope["approver_id"],
            device_id=envelope["device_id"],
            command_hash=envelope["command_hash"],
            issued_at=envelope["issued_at"],
            expires_at=envelope["expires_at"],
            nonce=envelope["nonce"],
        )
        if payload["device_id"] != expected_device:
            return None
        if not payload["issued_at"] <= current_time < payload["expires_at"]:
            return None
        command_hash = hashlib.sha256(command_text.encode("utf-8")).hexdigest()
        if command_hash != payload["command_hash"]:
            return None
        public_bytes = _decode_keyset().get(payload["key_id"])
        if public_bytes is None:
            return None
        signature = _b64url_decode(envelope["signature"], expected_length=64)
        Ed25519PublicKey.from_public_bytes(public_bytes).verify(
            signature,
            _canonical_json_bytes(payload),
        )
        return payload
    except (InvalidSignature, TypeError, ValueError, UnicodeError, binascii.Error):
        return None
