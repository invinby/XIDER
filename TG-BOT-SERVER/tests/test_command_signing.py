import base64
import hashlib
import json

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

import command_signing as signing

_REAL_KEY_LOADER = signing._load_private_key


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


@pytest.fixture(autouse=True)
def test_signing_key(monkeypatch):
    monkeypatch.setattr(signing, "_load_private_key", lambda: PRIVATE_KEY)
    monkeypatch.setenv("XIDER_COMMAND_VERIFY_KEYSET_B64", _keyset_b64(PUBLIC_BYTES))


def _sign(**overrides):
    values = {
        "request_id": REQUEST_ID,
        "requester_id": REQUESTER_ID,
        "approver_id": APPROVER_ID,
        "device_id": DEVICE_ID,
        "command": COMMAND,
        "issued_at": ISSUED_AT,
        "expires_at": EXPIRES_AT,
        "nonce": NONCE,
        "key_id": KEY_ID,
    }
    values.update(overrides)
    return signing.sign_approved_command(**values)


def _sign_payload(payload):
    return dict(payload, signature=_b64url(PRIVATE_KEY.sign(_canonical_json(payload))))


def test_signer_emits_exact_canonical_schema_1_envelope_and_hashes_exact_utf8():
    envelope = _sign()

    assert set(envelope) == {
        "key_id",
        "request_id",
        "requester_id",
        "approver_id",
        "device_id",
        "command_hash",
        "issued_at",
        "expires_at",
        "nonce",
        "signature",
    }
    assert "schema" not in envelope
    assert "command" not in envelope
    assert envelope["key_id"] == KEY_ID
    assert envelope["command_hash"] == hashlib.sha256(COMMAND.encode("utf-8")).hexdigest()

    expected_canonical = (
        b'{"approver_id":9001,"command_hash":"'
        + envelope["command_hash"].encode("ascii")
        + b'","device_id":"MacBook-Air.local","expires_at":1800000120,"issued_at":1800000000,"key_id":"'
        + KEY_ID.encode("ascii")
        + b'","nonce":"'
        + NONCE.encode("ascii")
        + b'","request_id":"request_0123456789","requester_id":125}'
    )
    expected = PRIVATE_KEY.sign(expected_canonical)
    assert envelope["signature"] == _b64url(expected)


def test_signer_and_verifier_accept_valid_approval():
    envelope = _sign()

    verified = signing.verify_approved_command(
        envelope,
        command=COMMAND,
        expected_device_id=DEVICE_ID,
        now=NOW,
    )

    assert verified == {key: value for key, value in envelope.items() if key != "signature"}


def test_signer_preserves_multiline_shell_text_as_exact_utf8_hash():
    command = "printf 'first line'\nprintf 'second line'"
    envelope = _sign(command=command)

    assert envelope["command_hash"] == hashlib.sha256(command.encode("utf-8")).hexdigest()
    assert "command" not in envelope
    assert signing.verify_approved_command(
        envelope,
        command=command,
        expected_device_id=DEVICE_ID,
        now=NOW,
    ) is not None


def test_canonical_payload_uses_utf8_json_for_unicode_device_id():
    unicode_device_id = "MacBook-ноут.local"
    envelope = _sign(device_id=unicode_device_id)
    payload = {key: value for key, value in envelope.items() if key != "signature"}
    expected_canonical = (
        '{"approver_id":9001,"command_hash":"'
        + payload["command_hash"]
        + '","device_id":"MacBook-ноут.local","expires_at":1800000120,"issued_at":1800000000,"key_id":"'
        + KEY_ID
        + '","nonce":"'
        + NONCE
        + '","request_id":"request_0123456789","requester_id":125}'
    ).encode("utf-8")

    assert envelope["signature"] == _b64url(PRIVATE_KEY.sign(expected_canonical))
    assert signing.verify_approved_command(
        envelope,
        command=COMMAND,
        expected_device_id=unicode_device_id,
        now=NOW,
    ) == payload


def test_verifier_rejects_tampered_exact_command_bytes():
    envelope = _sign()

    assert signing.verify_approved_command(
        envelope,
        command=COMMAND + " ",
        expected_device_id=DEVICE_ID,
        now=NOW,
    ) is None


def test_verifier_rejects_altered_signed_field():
    envelope = _sign()
    envelope["approver_id"] += 1

    assert signing.verify_approved_command(
        envelope,
        command=COMMAND,
        expected_device_id=DEVICE_ID,
        now=NOW,
    ) is None


def test_verifier_rejects_wrong_target_even_when_envelope_is_valid():
    envelope = _sign()

    assert signing.verify_approved_command(
        envelope,
        command=COMMAND,
        expected_device_id="other-device",
        now=NOW,
    ) is None


@pytest.mark.parametrize(
    "mutate",
    [
        lambda envelope: envelope.pop("nonce"),
        lambda envelope: envelope.update(extra="not-in-schema-1"),
        lambda envelope: envelope.update(requester_id=True),
        lambda envelope: envelope.update(requester_id=1 << 100),
        lambda envelope: envelope.update(signature="not base64url!"),
        lambda envelope: envelope.update(signature=envelope["signature"] + "="),
        lambda envelope: envelope.update(signature="A" * 100_000),
        lambda envelope: envelope.update(key_id="0" * 16),
    ],
)
def test_verifier_fails_closed_on_malformed_or_unknown_envelope(mutate):
    envelope = _sign()
    mutate(envelope)

    assert signing.verify_approved_command(
        envelope,
        command=COMMAND,
        expected_device_id=DEVICE_ID,
        now=NOW,
    ) is None


def test_verifier_rejects_a_key_id_that_does_not_match_its_public_key(monkeypatch):
    envelope = _sign()
    wrong_id = "f" * 16
    # This keyset is canonically encoded but its mapping key does not identify
    # the raw public key, so the complete trust set must be rejected.
    malformed_mapping = {wrong_id: _b64url(PUBLIC_BYTES)}
    wrong_keyset = _b64url(_canonical_json(malformed_mapping))
    monkeypatch.setenv("XIDER_COMMAND_VERIFY_KEYSET_B64", wrong_keyset)

    assert signing.verify_approved_command(
        envelope,
        command=COMMAND,
        expected_device_id=DEVICE_ID,
        now=NOW,
    ) is None


def test_verifier_accepts_two_key_rotation_overlap_and_rejects_more_than_two(monkeypatch):
    old_key = Ed25519PrivateKey.from_private_bytes(bytes(reversed(range(32))))
    old_public = old_key.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )
    old_key_id = hashlib.sha256(old_public).hexdigest()[:16]
    old_payload = {
        "key_id": old_key_id,
        "request_id": REQUEST_ID,
        "requester_id": REQUESTER_ID,
        "approver_id": APPROVER_ID,
        "device_id": DEVICE_ID,
        "command_hash": hashlib.sha256(COMMAND.encode("utf-8")).hexdigest(),
        "issued_at": ISSUED_AT,
        "expires_at": EXPIRES_AT,
        "nonce": NONCE,
    }
    old_envelope = dict(old_payload, signature=_b64url(old_key.sign(_canonical_json(old_payload))))

    monkeypatch.setenv("XIDER_COMMAND_VERIFY_KEYSET_B64", _keyset_b64(PUBLIC_BYTES, old_public))
    assert signing.verify_approved_command(
        old_envelope,
        command=COMMAND,
        expected_device_id=DEVICE_ID,
        now=NOW,
    ) == old_payload

    third_key = Ed25519PrivateKey.from_private_bytes(bytes([17]) * 32)
    third_public = third_key.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )
    monkeypatch.setenv(
        "XIDER_COMMAND_VERIFY_KEYSET_B64",
        _keyset_b64(PUBLIC_BYTES, old_public, third_public),
    )
    assert signing.verify_approved_command(
        old_envelope,
        command=COMMAND,
        expected_device_id=DEVICE_ID,
        now=NOW,
    ) is None


@pytest.mark.parametrize(
    ("issued_at", "expires_at", "now"),
    [
        (ISSUED_AT, ISSUED_AT, ISSUED_AT),
        (ISSUED_AT, ISSUED_AT + 121, ISSUED_AT),
        (ISSUED_AT + 1, EXPIRES_AT + 1, ISSUED_AT),
        (ISSUED_AT, EXPIRES_AT, EXPIRES_AT),
    ],
)
def test_verifier_rejects_invalid_expiry_and_time_window(issued_at, expires_at, now):
    payload = {
        "key_id": KEY_ID,
        "request_id": REQUEST_ID,
        "requester_id": REQUESTER_ID,
        "approver_id": APPROVER_ID,
        "device_id": DEVICE_ID,
        "command_hash": hashlib.sha256(COMMAND.encode("utf-8")).hexdigest(),
        "issued_at": issued_at,
        "expires_at": expires_at,
        "nonce": NONCE,
    }
    envelope = _sign_payload(payload)

    assert signing.verify_approved_command(
        envelope,
        command=COMMAND,
        expected_device_id=DEVICE_ID,
        now=now,
    ) is None


@pytest.mark.parametrize("nonce", ["", "not a nonce", "A" * 42, "A" * 44, "A" * 42 + "="])
def test_signer_rejects_invalid_nonce(nonce):
    with pytest.raises(ValueError, match="signing fields"):
        _sign(nonce=nonce)


def test_signer_rejects_private_key_unavailable_without_echoing_command_or_key(monkeypatch):
    def unavailable():
        raise signing.CommandSigningUnavailable("signing key unavailable")

    monkeypatch.setattr(signing, "_load_private_key", unavailable)

    with pytest.raises(signing.CommandSigningUnavailable) as error:
        _sign()

    assert COMMAND not in str(error.value)


def test_signer_rejects_a_private_key_that_does_not_match_requested_key_id(monkeypatch):
    other_key = Ed25519PrivateKey.from_private_bytes(bytes([23]) * 32)
    monkeypatch.setattr(signing, "_load_private_key", lambda: other_key)

    with pytest.raises(signing.CommandSigningUnavailable) as error:
        _sign()

    assert COMMAND not in str(error.value)


def test_default_key_loader_does_not_create_a_replacement_key(monkeypatch, tmp_path):
    missing_key_path = tmp_path / "not-provisioned.pem"
    monkeypatch.setattr(signing, "_PRIVATE_KEY_PATH", missing_key_path)

    with pytest.raises(signing.CommandSigningUnavailable) as error:
        _REAL_KEY_LOADER()

    assert not missing_key_path.exists()
    assert "not-provisioned.pem" not in str(error.value)


def test_verifier_rejects_duplicate_keys_in_keyset(monkeypatch):
    encoded_public = _b64url(PUBLIC_BYTES)
    duplicate_json = (
        "{" + json.dumps(KEY_ID) + ":" + json.dumps(encoded_public)
        + "," + json.dumps(KEY_ID) + ":" + json.dumps(encoded_public) + "}"
    ).encode("utf-8")
    monkeypatch.setenv("XIDER_COMMAND_VERIFY_KEYSET_B64", _b64url(duplicate_json))

    assert signing.verify_approved_command(
        _sign(),
        command=COMMAND,
        expected_device_id=DEVICE_ID,
        now=NOW,
    ) is None
