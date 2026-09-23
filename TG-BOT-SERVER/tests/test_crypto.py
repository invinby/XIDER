"""Юнит-тесты crypto (HMAC-SHA256 + nonce + replay-защита)."""

import time

import pytest

import crypto as m


def test_sign_has_nonce():
    env = m.sign_message({"type": "status"})
    assert env["payload"]["nonce"]
    assert len(env["payload"]["nonce"]) == 32  # uuid4().hex


def test_verify_ok():
    env = m.sign_message({"type": "open_url", "url": "https://example.com"})
    payload = m.verify_message(env)
    assert payload is not None
    assert payload["type"] == "open_url"


def test_verify_tampered_payload():
    env = m.sign_message({"type": "open_url", "url": "https://a.com"})
    env["payload"]["url"] = "https://evil.com"
    assert m.verify_message(env) is None


def test_verify_bad_signature():
    env = m.sign_message({"type": "open_url"})
    env["sig"] = "0" * 64
    assert m.verify_message(env) is None


def test_replay_denied():
    env = m.sign_message({"type": "shell", "command": "whoami"})
    assert m.verify_message(env) is not None
    # Тот же конверт повторно — replay, должен быть отклонён.
    assert m.verify_message(dict(env)) is None


def test_verify_without_nonce():
    # Сообщение без nonce должно отклоняться.
    from crypto import _canonical
    import hashlib
    import hmac

    payload = {"type": "status", "ts": int(time.time())}
    body = _canonical(payload)
    sig = hmac.new(m.SHARED_KEY.encode(), body, hashlib.sha256).hexdigest()
    assert m.verify_message({"sig": sig, "payload": payload}) is None


def test_multiple_unique_nonces_accepted():
    for _ in range(10):
        env = m.sign_message({"type": "status"})
        assert m.verify_message(env) is not None