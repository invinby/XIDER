"""Validate and atomically write the local XIDER installation report."""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile
from typing import Any


COMPONENTS = ("agent", "guardian", "safari", "siri")
COMPONENT_STATES = {
    "installed", "failed", "disabled", "not_applicable", "not_started", "unknown",
}
BOT_RESPONSE_STATES = {
    "verified", "not_verified", "failed", "not_applicable", "not_tested",
}
MAX_REPORT_BYTES = 128 * 1024


def _text(value: Any, field: str, *, nullable: bool = False) -> str | None:
    if nullable and value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string")
    cleaned = " ".join(value.split())
    if len(cleaned) > 1000:
        cleaned = cleaned[:1000]
    return cleaned


def _validate_report(report: Any) -> dict[str, Any]:
    if not isinstance(report, dict) or report.get("schema") != 1:
        raise ValueError("install report must use schema 1")
    for field in ("install_id", "installed_at", "platform", "outcome"):
        _text(report.get(field), field)
    if report["platform"] not in {"windows", "macos"}:
        raise ValueError("platform must be windows or macos")
    if report["outcome"] not in {"succeeded", "failed", "partial"}:
        raise ValueError("outcome must be succeeded, failed, or partial")
    requested_version = _text(report.get("requested_version"), "requested_version", nullable=True)

    components = report.get("components")
    if not isinstance(components, dict) or set(components) != set(COMPONENTS):
        raise ValueError("install report must include the four required components")

    normalized: dict[str, Any] = {
        "schema": 1,
        "install_id": report["install_id"],
        "installed_at": report["installed_at"],
        "platform": report["platform"],
        "requested_version": requested_version,
        "outcome": report["outcome"],
        "components": {},
    }
    for name in COMPONENTS:
        component = components[name]
        if not isinstance(component, dict):
            raise ValueError(f"component {name} must be an object")
        state = _text(component.get("status"), f"{name}.status")
        if state not in COMPONENT_STATES:
            raise ValueError(f"invalid component state for {name}")
        version = _text(component.get("version"), f"{name}.version", nullable=True)
        process = component.get("process")
        if not isinstance(process, dict) or type(process.get("running")) is not bool:
            raise ValueError(f"{name}.process must include a boolean running field")
        pid = process.get("pid")
        if pid is not None and (type(pid) is not int or pid <= 0):
            raise ValueError(f"{name}.process.pid must be a positive integer or null")
        process_name = _text(process.get("name"), f"{name}.process.name", nullable=True)
        bot_response = component.get("bot_response")
        if not isinstance(bot_response, dict):
            raise ValueError(f"{name}.bot_response must be an object")
        bot_state = _text(bot_response.get("status"), f"{name}.bot_response.status")
        if bot_state not in BOT_RESPONSE_STATES:
            raise ValueError(f"invalid bot response state for {name}")
        bot_detail = _text(bot_response.get("detail"), f"{name}.bot_response.detail")
        error = _text(component.get("error"), f"{name}.error", nullable=True)
        if state == "installed" and (not version or not process["running"] or pid is None):
            raise ValueError(f"installed component {name} must have version and a running PID")
        normalized["components"][name] = {
            "status": state,
            "version": version,
            "process": {"running": process["running"], "pid": pid, "name": process_name},
            "bot_response": {"status": bot_state, "detail": bot_detail},
            "error": error,
        }
    return normalized


def write_install_report(destination: str | os.PathLike[str], report: Any) -> Path:
    """Validate the report and replace its file atomically."""
    path = Path(destination)
    normalized = _validate_report(report)
    encoded = (json.dumps(
        normalized, ensure_ascii=False, separators=(",", ":"), allow_nan=False,
    ) + "\n").encode("utf-8")
    if len(encoded) > MAX_REPORT_BYTES:
        raise ValueError("install report exceeds the size limit")

    path.parent.mkdir(parents=True, exist_ok=True)
    if path.parent.is_symlink() or path.is_symlink():
        raise ValueError("install report path must not be a symlink")
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent,
    )
    try:
        if hasattr(os, "fchmod"):
            os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_name, path)
    finally:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
    return path


def main() -> int:
    if len(sys.argv) != 3:
        print("usage: install_report.py OUTPUT_PATH INPUT_JSON", file=sys.stderr)
        return 2
    try:
        with Path(sys.argv[2]).open("r", encoding="utf-8") as stream:
            report = json.load(stream)
        written = write_install_report(sys.argv[1], report)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"XIDER install report was not written: {exc}", file=sys.stderr)
        return 1
    print(written)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
