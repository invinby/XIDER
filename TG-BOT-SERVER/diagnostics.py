"""Pure, fail-closed serialization of bounded diagnostic metadata.

No filesystem, environment, network or probe access. Unknown fields are rejected,
not copied into a generic result dump. Errors intentionally contain no input.
"""
from dataclasses import asdict
import json
import re

import access_store
import check_store

MAX_BYTES = 256 * 1024
_KEY = re.compile(r"[a-z][a-z0-9_]{0,63}\Z")
_ID = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")
_SECRET_KEY = re.compile(r"token|password|passwd|secret|key|authorization|environment|stdout|stderr|shell|filename|coordinate", re.I)
STATES = frozenset({"unknown", "stale", "running", "stopped", "reachable", "unavailable",
    "available", "connected", "disconnected", "message_received", "update_received",
    "online", "offline", "supported", "dependency_missing", "permission_unverified",
    "device_unverified", "approximate", "granted", "denied", "not_determined", "needs_live_test"})
SOURCES = frozenset({"server_observation", "device_heartbeat", "agent_capability_snapshot",
                     "agent_capability_declaration", "action_check", "unavailable"})


def _text(value, maximum=128, *, pattern=None):
    if type(value) is not str or not value or len(value) > maximum:
        raise ValueError
    if any(ord(char) < 32 or ord(char) == 127 for char in value) or "REDACTED" in value:
        raise ValueError
    if access_store.redact_text(value) != value:
        raise ValueError
    if pattern and not pattern.fullmatch(value):
        raise ValueError
    return value


def _key(value):
    _text(value, 64, pattern=_KEY)
    if _SECRET_KEY.search(value):
        raise ValueError
    return value


def _stamp(value, *, nullable=True):
    if value is None and nullable:
        return None
    check_store._timestamp(value)
    return value


def _rows(value, maximum):
    if type(value) is not list or len(value) > maximum:
        raise ValueError
    return value


def _state(row, *, capability=False):
    fields = {"state", "source", "checked_at"}
    if capability:
        fields |= {"device_id", "id"}
    if type(row) is not dict or set(row) != fields:
        raise ValueError
    state, source = row["state"], row["source"]
    if type(state) is not str or state not in STATES or type(source) is not str or source not in SOURCES:
        raise ValueError
    result = {"state": state, "source": source, "checked_at": _stamp(row["checked_at"])}
    if capability:
        result.update(device_id=_text(row["device_id"], pattern=_ID), id=_key(row["id"]))
    return result


def build_diagnostic_snapshot(*, generated_at: str, versions: dict[str, str],
    statuses: dict[str, object], capabilities: list[dict], checks: list[dict],
    log_tail: list[str], correlation_ids: list[str]) -> bytes:
    try:
        moment = check_store._timestamp(_stamp(generated_at, nullable=False))
        if type(versions) is not dict or len(versions) > 64 or type(statuses) is not dict or len(statuses) > 64:
            raise ValueError
        safe_versions = {}
        for key, version in versions.items():
            _key(key)
            if type(version) is not str or not check_store._VERSION.fullmatch(version):
                raise ValueError
            safe_versions[key] = _text(version)
        safe_statuses = {_key(key): _state(value) for key, value in statuses.items()}
        safe_capabilities = [_state(row, capability=True) for row in _rows(capabilities, 256)]
        safe_checks = []
        for row in _rows(checks, 256):
            if type(row) is not dict:
                raise ValueError
            record = check_store.CheckRecord(**row)
            check_store._validate(record)
            # Check records from callers still pass credential/path sanitizer.
            for value in asdict(record).values():
                if isinstance(value, str):
                    _text(value)
            safe_checks.append({**asdict(record), "source": "action_check",
                                "freshness": "current" if check_store.is_fresh(record, now=moment) else "stale"})
        safe_logs = [_text(line, 500) for line in _rows(log_tail, 80)]
        safe_ids = [_text(value, pattern=_ID) for value in _rows(correlation_ids, 128)]
        encoded = json.dumps({"schema": 1, "generated_at": generated_at, "versions": safe_versions,
            "statuses": safe_statuses, "capabilities": safe_capabilities, "checks": safe_checks,
            "log_tail": safe_logs, "correlation_ids": safe_ids}, ensure_ascii=False,
            allow_nan=False, separators=(",", ":")).encode("utf-8")
        if len(encoded) > MAX_BYTES:
            raise ValueError
        return encoded
    except (ValueError, TypeError, OverflowError, RecursionError, AttributeError):
        raise ValueError("Invalid diagnostic snapshot") from None
