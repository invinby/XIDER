import base64
import hashlib
import json

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

import command_signing as signing


REQUEST_ID = "request_0123456789"
REQUESTER_ID = 125
APPROVER_ID = 9001
DEVICE_ID = "MacBook-Air.local"
COMMAND = "printf 'точный текст\\n'  "
ISSUED_AT = 1_800_000_000
EXPIRES_AT = ISSUED_AT + 120
NOW = ISSUED_AT + 30
NONCE = "A" * 43
PRIVATE_KEY = Ed25519PrivateKey.from_private_bytes(bytes(range(32)))
PUBLIC_BYTES = PRIVATE_KEY.public_key().public_bytes(
    serialization.Encoding.Raw,
    serialization.PublicFormat.Raw,
)
KEY_ID = hashlib.sha256(PUBLIC_BYTES).hexdigest()[:16]


def _b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _canonical_json(value: dict) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _keyset_b64(*public_keys: bytes) -> str:
    keyset = {
        hashlib.sha256(public_key).hexdigest()[:16]: _b64url(public_key)
        for public_key in public_keys
    }
    return _b64url(_canonical_json(keyset))


def _payload(**overrides) -> dict:
    payload = {
        "key_id": KEY_ID,
        "request_id": REQUEST_ID,
        "requester_id": REQUESTER_ID,
        "approver_id": APPROVER_ID,
        "device_id": DEVICE_ID,
        "command_hash": hashlib.sha256(COMMAND.encode("utf-8")).hexdigest(),
        "issued_at": ISSUED_AT,
        "expires_at": EXPIRES_AT,
        "nonce": NONCE,
    }
    payload.update(overrides)
    return payload


def _signed_envelope(**overrides) -> dict:
    payload = _payload(**overrides)
    return dict(payload, signature=_b64url(PRIVATE_KEY.sign(_canonical_json(payload))))


@pytest.fixture(autouse=True)
def test_public_keyset(monkeypatch):
    monkeypatch.setenv("XIDER_COMMAND_VERIFY_KEYSET_B64", _keyset_b64(PUBLIC_BYTES))


def _verify(envelope, *, command=COMMAND, expected_device_id=DEVICE_ID, now=NOW):
    return signing.verify_approved_command(
        envelope,
        command=command,
        expected_device_id=expected_device_id,
        now=now,
    )


def test_verifies_canonical_schema_1_envelope_and_exact_utf8_command_hash():
    envelope = _signed_envelope()

    verified = _verify(envelope)

    assert verified == _payload()
    assert set(verified) == {
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
    assert "schema" not in verified
    assert "command" not in verified


def test_rejects_command_whitespace_or_unicode_byte_change():
    envelope = _signed_envelope()

    assert _verify(envelope, command=COMMAND + " ") is None
    assert _verify(envelope, command=COMMAND.replace("точный", "точныи")) is None


def test_verifier_accepts_multiline_command_without_normalizing_it():
    command = "printf 'first line'\nprintf 'second line'"
    payload = _payload(command_hash=hashlib.sha256(command.encode("utf-8")).hexdigest())
    envelope = dict(payload, signature=_b64url(PRIVATE_KEY.sign(_canonical_json(payload))))

    assert _verify(envelope, command=command) == payload


def test_rejects_wrong_device_and_altered_signed_fields():
    envelope = _signed_envelope()

    assert _verify(envelope, expected_device_id="different-device") is None
    envelope["approver_id"] += 1
    assert _verify(envelope) is None


@pytest.mark.parametrize(
    "mutate",
    [
        lambda envelope: envelope.pop("key_id"),
        lambda envelope: envelope.update(schema=1),
        lambda envelope: envelope.update(extra="no"),
        lambda envelope: envelope.update(requester_id=True),
        lambda envelope: envelope.update(requester_id=1 << 100),
        lambda envelope: envelope.update(signature="bad%signature"),
        lambda envelope: envelope.update(signature=envelope["signature"] + "="),
        lambda envelope: envelope.update(signature="A" * 100_000),
        lambda envelope: envelope.update(nonce="A" * 42),
    ],
)
def test_fails_closed_on_malformed_envelope(mutate):
    envelope = _signed_envelope()
    mutate(envelope)

    assert _verify(envelope) is None


def test_rejects_unknown_key_and_mismatched_key_id(monkeypatch):
    envelope = _signed_envelope(key_id="0" * 16)
    assert _verify(envelope) is None

    wrong_keyset = {"0" * 16: _b64url(PUBLIC_BYTES)}
    monkeypatch.setenv("XIDER_COMMAND_VERIFY_KEYSET_B64", _b64url(_canonical_json(wrong_keyset)))
    assert _verify(_signed_envelope()) is None


def test_accepts_old_key_during_bounded_rotation_overlap(monkeypatch):
    old_key = Ed25519PrivateKey.from_private_bytes(bytes(reversed(range(32))))
    old_public = old_key.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )
    old_id = hashlib.sha256(old_public).hexdigest()[:16]
    payload = _payload(key_id=old_id)
    envelope = dict(payload, signature=_b64url(old_key.sign(_canonical_json(payload))))
    monkeypatch.setenv("XIDER_COMMAND_VERIFY_KEYSET_B64", _keyset_b64(PUBLIC_BYTES, old_public))

    assert _verify(envelope) == payload

    third_key = Ed25519PrivateKey.from_private_bytes(bytes([17]) * 32)
    third_public = third_key.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )
    monkeypatch.setenv(
        "XIDER_COMMAND_VERIFY_KEYSET_B64",
        _keyset_b64(PUBLIC_BYTES, old_public, third_public),
    )
    assert _verify(envelope) is None


@pytest.mark.parametrize(
    ("issued_at", "expires_at", "now"),
    [
        (ISSUED_AT, ISSUED_AT + 121, ISSUED_AT + 1),
        (ISSUED_AT + 1, EXPIRES_AT + 1, ISSUED_AT),
        (ISSUED_AT, EXPIRES_AT, EXPIRES_AT),
        (ISSUED_AT, ISSUED_AT, ISSUED_AT),
    ],
)
def test_rejects_invalid_not_yet_valid_or_expired_window(issued_at, expires_at, now):
    assert _verify(_signed_envelope(issued_at=issued_at, expires_at=expires_at), now=now) is None


@pytest.mark.parametrize(
    "keyset",
    [
        "",
        "not-base64url!",
        _b64url(_canonical_json({})),
        _b64url(b'{"bad": "json"}'),
        _b64url(b'{ "' + KEY_ID.encode("ascii") + b'":"' + _b64url(PUBLIC_BYTES).encode("ascii") + b'"}'),
        _b64url(_canonical_json({KEY_ID: _b64url(PUBLIC_BYTES) + "="})),
    ],
)
def test_malformed_or_noncanonical_keyset_fails_closed(monkeypatch, keyset):
    monkeypatch.setenv("XIDER_COMMAND_VERIFY_KEYSET_B64", keyset)

    assert _verify(_signed_envelope()) is None


def test_missing_keyset_fails_closed(monkeypatch):
    monkeypatch.delenv("XIDER_COMMAND_VERIFY_KEYSET_B64", raising=False)

    assert _verify(_signed_envelope()) is None


def test_duplicate_keys_in_keyset_fail_closed(monkeypatch):
    encoded_public = _b64url(PUBLIC_BYTES)
    duplicate_json = (
        "{" + json.dumps(KEY_ID) + ":" + json.dumps(encoded_public)
        + "," + json.dumps(KEY_ID) + ":" + json.dumps(encoded_public) + "}"
    ).encode("utf-8")
    monkeypatch.setenv("XIDER_COMMAND_VERIFY_KEYSET_B64", _b64url(duplicate_json))

    assert _verify(_signed_envelope()) is None
