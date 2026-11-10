from __future__ import annotations

import json

from pending_restart import CHECKS, parse_pending_restart_checks


def test_pending_restart_parser_returns_only_fixed_reason_ids():
    checks = {name: False for name in CHECKS}
    checks["windows_update"] = True
    result = parse_pending_restart_checks(json.dumps({
        "checks": checks,
        "registry_value": "private file path must not escape",
    }))

    assert result == {
        "state": "supported", "source": "windows_pending_restart_registry",
        "pending": True, "reasons": ["windows_update"], "completeness": "complete",
    }
    assert "private file path" not in repr(result)


def test_pending_restart_parser_distinguishes_clear_from_unknown_and_partial():
    clear = parse_pending_restart_checks(json.dumps({"checks": {name: False for name in CHECKS}}))
    unknown_checks = {name: None for name in CHECKS}
    unknown = parse_pending_restart_checks(json.dumps({"checks": unknown_checks}))
    partial_checks = {name: None for name in CHECKS}
    partial_checks["component_servicing"] = True
    partial = parse_pending_restart_checks(json.dumps({"checks": partial_checks}))

    assert clear["state"] == "supported" and clear["pending"] is False
    assert unknown["state"] == "unknown" and unknown["pending"] is None
    assert partial["state"] == "supported" and partial["pending"] is True
    assert partial["completeness"] == "partial"
    assert parse_pending_restart_checks("bad json")["state"] == "unknown"
    assert parse_pending_restart_checks(json.dumps({"checks": {"arbitrary": True}}))["state"] == "unknown"
