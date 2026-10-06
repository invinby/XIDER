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


def test_verify_detailed_reports_safe_failure_categories():
    import hashlib
    import hmac

    assert m.verify_message_detailed(None) == (None, "invalid_envelope")

    env = m.sign_message({"type": "status"})
    tampered = {**env, "sig": "0" * 64}
    assert m.verify_message_detailed(tampered) == (None, "bad_hmac")

    stale = m.sign_message({"type": "status"})
    stale_payload = dict(stale["payload"], ts=int(time.time()) - m.MAX_AGE - 1)
    stale_sig = hmac.new(
        m.SHARED_KEY.encode("utf-8"), m._canonical(stale_payload), hashlib.sha256
    ).hexdigest()
    assert m.verify_message_detailed(
        {"sig": stale_sig, "payload": stale_payload}
    ) == (None, "stale_timestamp")

    missing_nonce_payload = {"type": "status", "ts": int(time.time())}
    missing_nonce_sig = hmac.new(
        m.SHARED_KEY.encode("utf-8"),
        m._canonical(missing_nonce_payload),
        hashlib.sha256,
    ).hexdigest()
    assert m.verify_message_detailed(
        {"sig": missing_nonce_sig, "payload": missing_nonce_payload}
    ) == (None, "missing_nonce")

    replay = m.sign_message({"type": "status"})
    assert m.verify_message_detailed(replay)[1] == "ok"
    assert m.verify_message_detailed(replay) == (None, "replay")


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
