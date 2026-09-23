"""Юнит-тесты AES-GCM шифрования payload (xgencrypto)."""

from xgencrypto import encrypt_payload, decrypt_payload


def test_roundtrip():
    enc = encrypt_payload({"type": "shell", "command": "whoami", "id": "abc123"})
    assert set(enc) == {"enc"}
    dec = decrypt_payload(enc)
    assert dec == {"type": "shell", "command": "whoami", "id": "abc123"}


def test_no_plaintext_leak():
    enc = encrypt_payload({"type": "clipboard", "text": "SUPER-SECRET-ДАННЫЕ"})
    blob = enc["enc"]
    assert "SUPER-SECRET" not in blob
    assert "clipboard" not in blob
    # Внешний конверт не должен содержать исходного payload.
    assert "SUPER-SECRET" not in str(enc)


def test_tampered_ciphertext_returns_none():
    enc = encrypt_payload({"type": "status"})
    blob = bytearray(__import__("base64").b64decode(enc["enc"]))
    blob[-1] ^= 0xFF  # flip последнего байта (часть tag)
    tampered = {"enc": __import__("base64").b64encode(bytes(blob)).decode()}
    assert decrypt_payload(tampered) is None


def test_missing_enc_returns_none():
    assert decrypt_payload({"type": "status"}) is None
    assert decrypt_payload({}) is None


def test_distinct_ciphertexts_for_same_payload():
    e1 = encrypt_payload({"type": "status"})
    e2 = encrypt_payload({"type": "status"})
    assert e1["enc"] != e2["enc"]  # random IV каждый раз