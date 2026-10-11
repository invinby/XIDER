"""Pure sanitized diagnostic bundle contracts; no live probes."""
import importlib.util
import json
import pytest


def module():
    assert importlib.util.find_spec("diagnostics") is not None, "sanitized diagnostic builder missing"
    import diagnostics
    return diagnostics


def inputs(**changes):
    values = dict(generated_at="2026-10-11T00:00:00Z", versions={"server": "4.1.2"},
        statuses={"broker": {"state": "connected", "source": "server_observation", "checked_at": None}},
        capabilities=[{"device_id": "device_123", "id": "camera", "state": "needs_live_test",
                       "source": "agent_capability_snapshot", "checked_at": "2026-10-10T23:59:59Z"}],
        checks=[], log_tail=["process started"], correlation_ids=["abc123"])
    values.update(changes)
    return values


def test_diagnostics_include_versions_capabilities_and_correlation_ids():
    m = module()
    blob = m.build_diagnostic_snapshot(**inputs())
    doc = json.loads(blob)
    assert doc["schema"] == 1
    assert doc["versions"] == {"server": "4.1.2"}
    assert doc["capabilities"][0]["state"] == "needs_live_test"
    assert doc["correlation_ids"] == ["abc123"]


@pytest.mark.parametrize("change", [
    {"log_tail": ["password=synthetic-private"]},
    {"log_tail": ["PRIVATE C:/private/file.txt"]},
    {"log_tail": ["41.1234, 69.9876"]},
    {"log_tail": ["-----BEGIN PRIVATE KEY-----truncated-private"]},
    {"statuses": {"token": {"state": "unknown", "source": "unavailable", "checked_at": None}}},
    {"capabilities": [{"device_id": "device_123", "id": "camera", "state": "unknown", "source": "agent_capability_snapshot", "checked_at": None, "text": "private-result"}]},
    {"versions": {"server": "token=synthetic-private"}},
])
def test_diagnostics_redact_secrets_and_exclude_sensitive_payloads(change):
    m = module()
    with pytest.raises(ValueError) as error:
        m.build_diagnostic_snapshot(**inputs(**change))
    assert str(error.value) == "Invalid diagnostic snapshot"


@pytest.mark.parametrize("change", [{"log_tail": ["x" * 501]}, {"correlation_ids": ["a"] * 129},
    {"generated_at": "invalid"}, {"generated_at": "2026-10-11T00:00:00"},
    {"statuses": {"broker": {"state": object(), "source": "server_observation", "checked_at": None}}},
    {"statuses": {"broker": {"state": "connected", "source": "server_observation", "checked_at": float("nan")}}},
    {"checks": [{"raw": {"private": "secret"}}]}, {"capabilities": [{"nested": {"bad": []}}]},
])
def test_diagnostics_bundle_is_bounded(change):
    m = module()
    with pytest.raises(ValueError, match="^Invalid diagnostic snapshot$"):
        m.build_diagnostic_snapshot(**inputs(**change))


def test_diagnostics_output_cap_is_enforced(monkeypatch):
    m = module()
    monkeypatch.setattr(m, "MAX_BYTES", 10)
    with pytest.raises(ValueError, match="^Invalid diagnostic snapshot$"):
        m.build_diagnostic_snapshot(**inputs())
