import importlib

import pytest

import shell_repeat as repeat


@pytest.fixture(autouse=True)
def fresh_store():
    importlib.reload(repeat)


def test_repeat_is_owner_scoped_short_lived_and_consumed_once():
    token = repeat.create(owner_id=20, device_id="device-a", command="whoami",
                          outcome="completed", now=100)
    assert token
    assert repeat.peek(token, owner_id=21, now=101) is None
    candidate = repeat.peek(token, owner_id=20, now=101)
    assert candidate is not None
    assert candidate.device_id == "device-a" and candidate.command_text == "whoami"
    assert repeat.consume(token, owner_id=20, now=102) == candidate
    assert repeat.consume(token, owner_id=20, now=103) is None


@pytest.mark.parametrize("outcome", ["timeout", "unknown", "rejected", "cancelled"])
def test_repeat_candidates_require_a_terminal_execution_result(outcome):
    assert repeat.create(owner_id=20, device_id="device-a", command="whoami",
                         outcome=outcome, now=100) is None


def test_repeat_candidate_rejects_invalid_or_oversized_command_and_expires():
    assert repeat.create(owner_id=True, device_id="device-a", command="whoami",
                         outcome="completed", now=100) is None
    assert repeat.create(owner_id=20, device_id="device-a", command="x" * 4097,
                         outcome="completed", now=100) is None
    token = repeat.create(owner_id=20, device_id="device-a", command="whoami",
                          outcome="failed", now=100)
    assert repeat.peek(token, owner_id=20, now=100 + repeat.TTL_SECONDS) is None


def test_candidate_repr_does_not_expose_command_text():
    token = repeat.create(owner_id=20, device_id="device-a", command="password=private-value",
                          outcome="completed", now=100)
    candidate = repeat.peek(token, owner_id=20, now=101)
    assert "private-value" not in repr(candidate)
