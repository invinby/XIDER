"""Strict parser for Windows reboot-pending registry markers."""

from __future__ import annotations

import json


SOURCE = "windows_pending_restart_registry"
MAX_INPUT_CHARS = 16 * 1024
CHECKS = ("component_servicing", "windows_update", "pending_file_operations", "update_exe_volatile")


def parse_pending_restart_checks(text: str) -> dict:
    """Expose only fixed reason IDs and tri-state checks, never registry values."""
    unknown = {"state": "unknown", "source": SOURCE, "pending": None, "reasons": [], "completeness": "unknown"}
    if not isinstance(text, str) or not text or len(text) > MAX_INPUT_CHARS:
        return unknown
    try:
        raw = json.loads(text)
    except (TypeError, json.JSONDecodeError):
        return unknown
    if not isinstance(raw, dict) or not isinstance(raw.get("checks"), dict):
        return unknown
    checks = raw["checks"]
    if set(checks) != set(CHECKS) or any(value not in (True, False, None) for value in checks.values()):
        return unknown
    reasons = [name for name in CHECKS if checks[name] is True]
    unavailable = any(checks[name] is None for name in CHECKS)
    if reasons:
        return {
            "state": "supported", "source": SOURCE, "pending": True,
            "reasons": reasons, "completeness": "partial" if unavailable else "complete",
        }
    if unavailable:
        return unknown
    return {"state": "supported", "source": SOURCE, "pending": False, "reasons": [], "completeness": "complete"}
